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
    """Po sprzedaży na sklepie WC — EAN-based stock pooling.

    User scenario: same produkt na 2 paletach (16+16 szt), wystawiony jako
    JEDNA aukcja Allegro z stock=32. Gdy paleta 1 wyprzeda, NIE zamykamy
    aukcji — paleta 2 dalej ma 16 szt. Allegro stock = sum(remaining
    across all produkty z tym samym EAN).

    Workflow per item:
    1. Parse SKU → znajdź wszystkie produkty z tym EAN/id (multi-pallet)
    2. Wybierz pierwszy z stock > 0 (FIFO po id), decrementuj go
    3. Gdy primary.ilosc=0: status='sprzedane' + data_sprzedazy=now
    4. Suma stocku across all related produkty z tym EAN
    5. Jeśli suma > 0: update Allegro stock = suma (KEEP offer open)
       Jeśli suma = 0: close_offer() na wszystkich linked aukcjach
    """
    summary = {
        'sold_products': 0, 'closed_offers': 0, 'updated_stock': 0,
        'skipped': 0, 'errors': [],
    }
    if not items:
        return summary

    conn = get_db()
    close_offer_fn = None
    update_offer_stock_fn = None

    for item in items:
        if not isinstance(item, dict):
            continue
        sku = str(item.get('sku') or '').strip().upper()
        qty = int(item.get('quantity') or item.get('qty') or 1)
        if not sku:
            summary['skipped'] += 1
            continue

        # Resolve SKU → wszystkie related produkty (multi-pallet pooling)
        related = _resolve_related_produkty(sku, conn)
        if not related:
            logger.info(f'[cross-sync] order={wc_order_id} SKU={sku} → brak match (skip)')
            summary['skipped'] += 1
            continue

        # Wybierz primary do decrement: pierwszy z stock > 0 (FIFO po id).
        # Jeśli wszystkie mają ilosc=0 → oznacz idempotent skip (już wszystkie sprzedane).
        primary = next((r for r in related if int(r['ilosc'] or 0) > 0), None)
        if primary is None:
            logger.info(
                f'[cross-sync] SKU={sku} order={wc_order_id} → wszystkie {len(related)} '
                f'related produkty już ilosc=0 (idempotent skip)'
            )
            summary['skipped'] += 1
            continue

        primary_id = int(primary['id'])
        current_qty = int(primary['ilosc'] or 0)
        new_qty = max(0, current_qty - qty)

        # Update primary produkt
        if new_qty == 0:
            conn.execute(
                "UPDATE produkty SET ilosc = 0, status = 'sprzedane', "
                "data_sprzedazy = CURRENT_TIMESTAMP WHERE id = ?",
                (primary_id,),
            )
            summary['sold_products'] += 1
        else:
            conn.execute(
                'UPDATE produkty SET ilosc = ? WHERE id = ?',
                (new_qty, primary_id),
            )
        conn.commit()

        # POOL TOTAL: suma stocku across all related produkty
        # (after primary decrement — re-fetch z DB żeby uwzględnić update)
        related_ids = [int(r['id']) for r in related]
        placeholders = ','.join('?' * len(related_ids))
        pool_total_row = conn.execute(
            f'SELECT COALESCE(SUM(ilosc), 0) AS total FROM produkty WHERE id IN ({placeholders})',
            related_ids,
        ).fetchone()
        pool_total = int(pool_total_row['total'] or 0)

        # Znajdź WSZYSTKIE aktywne aukcje Allegro linked do related produkty
        # (jedna aukcja może wskazywać na primary, ale wszystkie related ją dotyczą)
        allegro_offers = conn.execute(
            f"SELECT id, allegro_id, produkt_id FROM oferty "
            f"WHERE produkt_id IN ({placeholders}) AND status = 'aktywna' "
            f"  AND allegro_id IS NOT NULL AND allegro_id != ''",
            related_ids,
        ).fetchall()

        if not allegro_offers:
            logger.info(
                f'[cross-sync] SKU={sku} primary={primary_id} sold {qty} '
                f'(no active Allegro offers — shop-only)'
            )
            continue

        # Lazy load Allegro funkcji
        if close_offer_fn is None or update_offer_stock_fn is None:
            try:
                from modules.allegro_api import close_offer as _co, update_offer_stock as _uos
                close_offer_fn = _co
                update_offer_stock_fn = _uos
            except ImportError as e:
                summary['errors'].append(f'allegro_api import failed: {e}')
                continue

        # Decyzja: pool_total > 0 → update stock, pool_total = 0 → close
        for offer_row in allegro_offers:
            allegro_id = offer_row['allegro_id']
            try:
                if pool_total > 0:
                    # KEEP open, update stock = pool_total (suma multi-pallet)
                    result, err = update_offer_stock_fn(allegro_id, pool_total)
                    if err and 'OFFER_NOT_EXISTS' not in err:
                        summary['errors'].append(f'allegro_id={allegro_id} stock update: {err}')
                        logger.warning(f'[cross-sync] update_offer_stock({allegro_id}, {pool_total}) error: {err}')
                    else:
                        summary['updated_stock'] += 1
                        logger.info(
                            f'[cross-sync] update stock allegro_id={allegro_id} → {pool_total} '
                            f'(pool z {len(related)} pallet, primary={primary_id}, order={wc_order_id})'
                        )
                else:
                    # CLOSE — wszystkie palety wyczerpane
                    result, err = close_offer_fn(allegro_id)
                    if err and 'OFFER_ALREADY_ENDED_OR_GONE' not in err:
                        summary['errors'].append(f'allegro_id={allegro_id} close: {err}')
                        logger.warning(f'[cross-sync] close_offer({allegro_id}) error: {err}')
                    else:
                        summary['closed_offers'] += 1
                        logger.info(
                            f'[cross-sync] CLOSED allegro_id={allegro_id} '
                            f'(pool_total=0, primary={primary_id}, order={wc_order_id})'
                        )
            except Exception as e:
                summary['errors'].append(f'allegro_id={allegro_id}: {e}')
                logger.exception(f'[cross-sync] allegro_id={allegro_id} exception')

    return summary


def _resolve_related_produkty(sku: str, conn) -> list:
    """Znajdź WSZYSTKIE produkty powiązane z SKU (multi-pallet pooling).

    Scenariusze:
    - "HUB-42" (specific produkt) → szukamy też innych z tym samym EAN
      (gdy produkt 42 ma EAN, dołącz inne produkty z tym EAN — pooling)
    - "EAN-1234567890" → wszystkie produkty z tym EAN
    - Inny SKU → mirror table fallback

    Returns: list sorted by id ASC (FIFO — pierwszy z stock>0 dostaje sale).
    """
    if not sku:
        return []
    rows = []
    m = _SKU_HUB_RE.match(sku)
    if m:
        try:
            hub_id = int(m.group(1))
        except ValueError:
            return []
        # Pobierz produkt + jego EAN
        primary = conn.execute(
            'SELECT id, ean, ilosc, status FROM produkty WHERE id = ?',
            (hub_id,),
        ).fetchone()
        if not primary:
            return []
        ean = (primary['ean'] or '').strip()
        if ean and _SKU_EAN_RE.match(f'EAN-{ean}'):
            # Multi-pallet pooling: wszystkie produkty z tym EAN (włącznie z primary)
            rows = conn.execute(
                'SELECT id, ean, ilosc, status FROM produkty '
                'WHERE ean = ? ORDER BY id ASC',
                (ean,),
            ).fetchall()
        else:
            # Brak EAN → tylko ten jeden produkt (single-source)
            rows = [primary]
        return [dict(r) for r in rows]

    m = _SKU_EAN_RE.match(sku)
    if m:
        ean = m.group(1)
        rows = conn.execute(
            'SELECT id, ean, ilosc, status FROM produkty '
            'WHERE ean = ? ORDER BY id ASC',
            (ean,),
        ).fetchall()
        return [dict(r) for r in rows]

    # Fallback: mirror table sklepakces_products → resolve hub_id → wszystkie z tym EAN
    hub_id = _resolve_hub_id_from_sku(sku, conn)
    if hub_id:
        primary = conn.execute(
            'SELECT id, ean, ilosc, status FROM produkty WHERE id = ?',
            (hub_id,),
        ).fetchone()
        if primary:
            ean = (primary['ean'] or '').strip()
            if ean and _SKU_EAN_RE.match(f'EAN-{ean}'):
                rows = conn.execute(
                    'SELECT id, ean, ilosc, status FROM produkty '
                    'WHERE ean = ? ORDER BY id ASC',
                    (ean,),
                ).fetchall()
                return [dict(r) for r in rows]
            return [dict(primary)]
    return []


def _resolve_hub_id_from_sku(sku: str, conn) -> int | None:
    """SKU 'HUB-42' → 42, SKU 'EAN-1234567890' → produkt.id WHERE ean='1234567890'.

    Returns hub_id (int) lub None gdy nie znaleziono.
    Używane jako fallback w _resolve_related_produkty dla custom SKU.
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
