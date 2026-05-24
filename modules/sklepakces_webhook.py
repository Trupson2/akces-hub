"""Sklepakces order webhook handler — POST /api/v1/sklepakces/orders.

Plugin PHP `Akces_Order_Webhook` po `woocommerce_payment_complete` POSTuje
order payload + HMAC headers. Hub validuje (HMAC + schema), persystuje do
`sklepakces_orders`, triggeruje Telegram alert `order_received`.

Idempotency: UNIQUE constraint na `signature_nonce` — duplicate POST → 409.
Future Faza 4: integration z Paletomat (item allocation dla SKU PAL-*).

Caller MUSI zarejestrować @require_sklepakces_hmac PRZED tą funkcją
(zrobione w sklepakces_blueprint.py).
"""
from __future__ import annotations

import json
import logging
import re
import time

from flask import request

from modules.api_v1.response import ErrorCodes, error_response, success_response
from modules.database import get_db
from modules.sklepakces_telegram import alert_order_received

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = ('order_id', 'total', 'customer', 'items')

# SKU formats z modules/sklepakces_push.py:_build_sku()
#   "EAN-{8-14 cyfr}" → szukaj produkt po ean
#   "HUB-{int}"       → szukaj produkt po id
_SKU_HUB_RE = re.compile(r'^HUB-(\d+)$', re.I)
_SKU_EAN_RE = re.compile(r'^EAN-(\d{8,14})$', re.I)


def handle_order_webhook():
    """POST /api/v1/sklepakces/orders handler."""
    t_start = time.perf_counter()

    # 1. JSON parse
    if not request.is_json:
        return _log_and_error('order_received', 400,
                              'Content-Type must be application/json',
                              ErrorCodes.INVALID_JSON, t_start)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _log_and_error('order_received', 400, 'Body must be JSON object',
                              ErrorCodes.INVALID_JSON, t_start)

    # 2. Required fields validation
    missing = [f for f in _REQUIRED_FIELDS if f not in payload]
    if missing:
        return _log_and_error('order_received', 400, f'Missing fields: {missing}',
                              ErrorCodes.MISSING_FIELD, t_start,
                              details={'missing': missing})

    customer = payload.get('customer', {})
    if not isinstance(customer, dict) or not customer.get('email'):
        return _log_and_error('order_received', 400, 'customer.email required',
                              ErrorCodes.VALIDATION_ERROR, t_start)

    items = payload.get('items', [])
    if not isinstance(items, list) or len(items) == 0:
        return _log_and_error('order_received', 400, 'items must be non-empty list',
                              ErrorCodes.VALIDATION_ERROR, t_start)

    # 3. Signature nonce z headers (dla UNIQUE constraint — idempotency)
    signature_nonce = request.headers.get('X-Akces-Signature', '')

    # 4. Insert
    conn = get_db()
    try:
        cur = conn.execute(
            'INSERT INTO sklepakces_orders '
            '(wc_order_id, order_number, status, total, currency, '
            ' customer_email, customer_data, items_data, payment_data, metadata, '
            ' signature_nonce) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                int(payload['order_id']),
                str(payload.get('order_number', '')),
                str(payload.get('status', 'processing')),
                float(payload['total']),
                str(payload.get('currency', 'PLN')),
                str(customer['email']),
                json.dumps(customer, ensure_ascii=False, default=str),
                json.dumps(items, ensure_ascii=False, default=str),
                json.dumps(payload.get('payment', {}), ensure_ascii=False, default=str),
                json.dumps(payload.get('metadata', {}), ensure_ascii=False, default=str),
                signature_nonce,
            ),
        )
        conn.commit()
        hub_internal_id = cur.lastrowid
    except Exception as e:
        err_str = str(e)
        if 'UNIQUE' in err_str or 'unique' in err_str.lower():
            # Duplicate webhook — idempotency: zwracaj sukces zamiast 409
            # bo plugin mógł retry'ować po network glitch.
            row = conn.execute(
                'SELECT id, wc_order_id FROM sklepakces_orders WHERE signature_nonce = ?',
                (signature_nonce,),
            ).fetchone()
            if row:
                duration_ms = int((time.perf_counter() - t_start) * 1000)
                _log_webhook('order_received', row['wc_order_id'], 'duplicate',
                             200, 'idempotency hit', duration_ms)
                return success_response({
                    'received': True,
                    'order_id': row['wc_order_id'],
                    'hub_internal_id': row['id'],
                    'idempotency_hit': True,
                })
        print(f"[sklepakces_webhook] DB insert failed: {e}")
        return _log_and_error('order_received', 500, 'Database error',
                              ErrorCodes.DATABASE_ERROR, t_start)

    # 5. Telegram alert (non-blocking — failure nie kasuje response)
    customer_name = (f"{customer.get('first_name', '')} "
                     f"{customer.get('last_name', '')}").strip()
    try:
        alert_order_received(int(payload['order_id']),
                             float(payload['total']),
                             customer_name)
    except Exception as e:
        print(f"[sklepakces_webhook] Telegram alert failed: {e}")

    # 6. CROSS-CHANNEL STOCK SYNC — sprzedaż na sklepie → zamknij aukcje Allegro
    # Best-effort: failure NIE kasuje response 200 dla pluginu WC.
    try:
        sync_result = _apply_cross_channel_stock_sync(items, int(payload['order_id']))
        if sync_result['closed_offers'] > 0:
            logger.info(
                f'[sklepakces_webhook] order={payload["order_id"]} '
                f'cross-channel: zamknięto {sync_result["closed_offers"]} aukcji Allegro, '
                f'sprzedano {sync_result["sold_products"]} produktów'
            )
    except Exception as e:
        logger.warning(f'[sklepakces_webhook] cross-channel sync failed (non-fatal): {e}')

    # 7. Log + return success
    duration_ms = int((time.perf_counter() - t_start) * 1000)
    _log_webhook('order_received', int(payload['order_id']),
                 'success', 200, None, duration_ms)

    return success_response({
        'received': True,
        'order_id': payload['order_id'],
        'hub_internal_id': hub_internal_id,
    })


def _apply_cross_channel_stock_sync(items: list, wc_order_id: int) -> dict:
    """Po sprzedaży na sklepie WC:
    1. Dla każdego item parse SKU (HUB-X lub EAN-X) → znajdź produkt w Hub
    2. Zmniejsz produkty.ilosc o item.quantity
    3. Gdy ilosc <= 0: status='sprzedane' + data_sprzedazy=now
    4. Znajdź aktywne oferty Allegro (status='aktywna') dla tego produkt_id
    5. Dla każdej oferty wywołaj allegro_api.close_offer() → set ENDED

    Returns: {'sold_products': N, 'closed_offers': N, 'skipped': N, 'errors': [...]}
    """
    summary = {'sold_products': 0, 'closed_offers': 0, 'skipped': 0, 'errors': []}
    if not items:
        return summary

    # Lazy import — allegro_api ładuje sporo zależności, nie chcemy spowolnić webhook'u
    # gdy żadna pozycja nie ma aktywnych aukcji
    conn = get_db()
    close_offer_fn = None  # lazy load

    for item in items:
        if not isinstance(item, dict):
            continue
        sku = str(item.get('sku') or '').strip().upper()
        qty = int(item.get('quantity') or item.get('qty') or 1)
        if not sku:
            summary['skipped'] += 1
            continue

        # Parse SKU → hub_product_id
        hub_id = _resolve_hub_id_from_sku(sku, conn)
        if not hub_id:
            logger.info(f'[cross-sync] order={wc_order_id} SKU={sku} → brak match w produkty (skip)')
            summary['skipped'] += 1
            continue

        # Aktualnie zapisany stan
        row = conn.execute(
            'SELECT id, ilosc, status FROM produkty WHERE id = ?',
            (hub_id,),
        ).fetchone()
        if not row:
            summary['skipped'] += 1
            continue
        current_qty = int(row['ilosc'] or 0)
        current_status = (row['status'] or '').strip()

        # Już sprzedany — nie robimy nic (idempotent)
        if current_status == 'sprzedane':
            logger.info(f'[cross-sync] hub_id={hub_id} już sprzedany — skip')
            summary['skipped'] += 1
            continue

        new_qty = max(0, current_qty - qty)
        # Update produkty
        if new_qty == 0:
            conn.execute(
                "UPDATE produkty SET ilosc = 0, status = 'sprzedane', "
                "data_sprzedazy = CURRENT_TIMESTAMP WHERE id = ?",
                (hub_id,),
            )
            summary['sold_products'] += 1
        else:
            conn.execute(
                'UPDATE produkty SET ilosc = ? WHERE id = ?',
                (new_qty, hub_id),
            )
        conn.commit()

        # Jeśli ilość poszła do 0 — zamknij aukcje Allegro
        if new_qty == 0:
            allegro_offers = conn.execute(
                "SELECT id, allegro_id FROM oferty "
                "WHERE produkt_id = ? AND status = 'aktywna' AND allegro_id IS NOT NULL "
                "  AND allegro_id != ''",
                (hub_id,),
            ).fetchall()
            if allegro_offers and close_offer_fn is None:
                try:
                    from modules.allegro_api import close_offer as _co
                    close_offer_fn = _co
                except ImportError as e:
                    summary['errors'].append(f'allegro_api import failed: {e}')
                    continue

            for offer_row in allegro_offers:
                allegro_id = offer_row['allegro_id']
                try:
                    result, err = close_offer_fn(allegro_id)
                    if err and 'OFFER_ALREADY_ENDED_OR_GONE' not in err:
                        summary['errors'].append(f'allegro_id={allegro_id}: {err}')
                        logger.warning(
                            f'[cross-sync] close_offer({allegro_id}) error: {err}'
                        )
                    else:
                        summary['closed_offers'] += 1
                        logger.info(
                            f'[cross-sync] zamknięto allegro_id={allegro_id} '
                            f'(hub_id={hub_id}, order={wc_order_id})'
                        )
                except Exception as e:
                    summary['errors'].append(f'allegro_id={allegro_id}: {e}')
                    logger.exception(f'[cross-sync] close_offer({allegro_id}) exception')

    return summary


def _resolve_hub_id_from_sku(sku: str, conn) -> int | None:
    """SKU 'HUB-42' → 42, SKU 'EAN-1234567890' → produkt.id WHERE ean='1234567890'.

    Returns hub_id (int) lub None gdy nie znaleziono.
    """
    if not sku:
        return None
    m = _SKU_HUB_RE.match(sku)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    m = _SKU_EAN_RE.match(sku)
    if m:
        ean = m.group(1)
        row = conn.execute(
            'SELECT id FROM produkty WHERE ean = ? LIMIT 1',
            (ean,),
        ).fetchone()
        return int(row['id']) if row else None
    # Fallback: szukaj w mirror table sklepakces_products po SKU
    # (np. gdy plugin dodał custom SKU lub user ręcznie edytował WC)
    try:
        row = conn.execute(
            "SELECT json_extract(product_data, '$.hub_id') AS hub_id "
            "FROM sklepakces_products WHERE sku = ? LIMIT 1",
            (sku,),
        ).fetchone()
        if row and row['hub_id']:
            return int(row['hub_id'])
    except Exception:
        pass  # malformed JSON w product_data — ignoruj
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log_and_error(event_type, http_code, error_msg, code, t_start, details=None):
    """Pojedyncza śrocka błędu — log + error_response."""
    duration_ms = int((time.perf_counter() - t_start) * 1000)
    _log_webhook(event_type, None, 'error', http_code, error_msg, duration_ms)
    return error_response(error_msg, code, http_code, details=details)


def _log_webhook(event_type, wc_order_id, status, http_code, error_msg, duration_ms):
    """Insert do sklepakces_webhook_log. Best-effort — failure print only."""
    try:
        conn = get_db()
        ip = (
            request.headers.get('CF-Connecting-IP')
            or request.headers.get('X-Real-IP')
            or request.remote_addr
            or ''
        )
        conn.execute(
            'INSERT INTO sklepakces_webhook_log '
            '(event_type, wc_order_id, status, http_code, error_message, '
            ' duration_ms, client_ip) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (event_type, wc_order_id, status, http_code, error_msg,
             duration_ms, ip),
        )
        conn.commit()
    except Exception as e:
        print(f"[sklepakces_webhook] Log insert failed: {e}")
