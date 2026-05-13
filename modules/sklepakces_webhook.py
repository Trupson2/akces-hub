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
import time

from flask import request

from modules.api_v1.response import ErrorCodes, error_response, success_response
from modules.database import get_db
from modules.sklepakces_telegram import alert_order_received


_REQUIRED_FIELDS = ('order_id', 'total', 'customer', 'items')


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

    # 6. Log + return success
    duration_ms = int((time.perf_counter() - t_start) * 1000)
    _log_webhook('order_received', int(payload['order_id']),
                 'success', 200, None, duration_ms)

    return success_response({
        'received': True,
        'order_id': payload['order_id'],
        'hub_internal_id': hub_internal_id,
    })


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
