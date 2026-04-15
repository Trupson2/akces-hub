"""API v1 outbound webhooks.

Zarejestrowany przez klienta URL dostaje POST z eventem (JSON) + HMAC-SHA256
signature header. Retry logic: exponential backoff, max 5 prob, 30s poll.

Supported events:
  order.created           — POST /api/v1/orders stworzyl nowe zamowienie
  order.status_changed    — PUT /status zmienilo status
  sale.completed          — order -> status 'wyslana'
  return.received         — order -> status 'zwrot'
  product.stock_low       — stock spadlo ponizej progu (default 2)
  product.stock_zero      — stock doszedl do 0

Architektura:
  - rejestracja: POST /api/v1/webhooks body {url, events} -> secret pokazany RAZ
  - trigger: kod aplikacyjny woła trigger_webhook_event(event_type, payload)
    co powoduje wstawienie wierszy do webhook_deliveries z status='pending'
  - delivery worker: daemon thread uruchomiony przez start_delivery_worker(),
    co 30s pobiera pending deliveries, POSTuje na URL, aktualizuje status
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import datetime, timedelta

from flask import g, request

from . import api_v1_bp
from .auth import require_api_v1
from .response import success_response, error_response, paginate, ErrorCodes
from .schemas import WebhookCreateSchema


SUPPORTED_EVENTS = {
    'order.created',
    'order.status_changed',
    'sale.completed',
    'return.received',
    'product.stock_low',
    'product.stock_zero',
}


def _generate_webhook_secret() -> str:
    """Kryptograficznie bezpieczny secret dla HMAC (32 bajty hex = 64 char)."""
    return secrets.token_hex(32)


def _compute_signature(secret: str, payload_bytes: bytes) -> str:
    """HMAC-SHA256 signature. Format: sha256=<hex>."""
    mac = hmac.new(secret.encode('utf-8'), payload_bytes, hashlib.sha256)
    return f'sha256={mac.hexdigest()}'


# ---------------------------------------------------------------------------
# Public: trigger_webhook_event
# ---------------------------------------------------------------------------

def trigger_webhook_event(event_type: str, payload: dict):
    """Zapisuje event do kolejki webhook_deliveries.

    Wywolywane z innych modulow (orders.py po create, products.py po stock change).
    Zwraca liczbe zakolejkowanych deliveries.
    """
    if event_type not in SUPPORTED_EVENTS:
        return 0
    from modules.database import get_db
    try:
        conn = get_db()
        rows = conn.execute(
            'SELECT id, events FROM webhooks WHERE active = 1'
        ).fetchall()
        payload_json = json.dumps(payload, default=str, ensure_ascii=False)
        queued = 0
        for row in rows:
            try:
                events = json.loads(row['events'] or '[]')
            except Exception:
                events = []
            if event_type not in events:
                continue
            conn.execute(
                'INSERT INTO webhook_deliveries '
                '(webhook_id, event_type, payload, status) '
                'VALUES (?, ?, ?, ?)',
                (row['id'], event_type, payload_json, 'pending'),
            )
            queued += 1
        conn.commit()
        return queued
    except Exception as e:
        print(f'[WARN] trigger_webhook_event failed: {e}')
        return 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _row_to_webhook(row, include_secret=False):
    d = {
        'id': row['id'],
        'url': row['url'],
        'events': json.loads(row['events']) if row['events'] else [],
        'active': bool(row['active']),
        'created_at': row['created_at'],
    }
    if include_secret:
        d['secret'] = row['secret']
    return d


@api_v1_bp.route('/webhooks', methods=['GET'])
@require_api_v1
def list_webhooks():
    """GET /api/v1/webhooks — lista wlasnych webhookow (bez secret)."""
    from modules.database import get_db
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM webhooks WHERE api_key_id = ? ORDER BY created_at DESC',
        (g.api_key_id,),
    ).fetchall()
    return success_response([_row_to_webhook(r) for r in rows])


@api_v1_bp.route('/webhooks', methods=['POST'])
@require_api_v1
def register_webhook():
    """POST /api/v1/webhooks body {url, events}.

    Response 201 z `secret` (pokazywany RAZ). Klient musi zapisac zeby moc
    weryfikowac HMAC signatures.
    """
    if not request.is_json:
        return error_response('Content-Type must be application/json',
                              ErrorCodes.INVALID_JSON, 400)
    data, errors = WebhookCreateSchema().validate(request.get_json(silent=True))
    if errors:
        return error_response('Validation failed', ErrorCodes.VALIDATION_ERROR, 400,
                              details=errors)

    events = data['events']
    # Walidacja eventow
    invalid = [e for e in events if e not in SUPPORTED_EVENTS]
    if invalid:
        return error_response(
            f'Unsupported events: {invalid}',
            ErrorCodes.VALIDATION_ERROR, 400,
            details={'events': f'supported: {sorted(SUPPORTED_EVENTS)}',
                     'invalid': invalid},
        )

    url = data['url'].strip()
    if not (url.startswith('http://') or url.startswith('https://')):
        return error_response(
            'URL must start with http:// or https://',
            ErrorCodes.VALIDATION_ERROR, 400,
            details={'url': 'must be absolute http(s) URL'},
        )

    secret = _generate_webhook_secret()
    from modules.database import get_db
    conn = get_db()
    cur = conn.execute(
        'INSERT INTO webhooks (api_key_id, url, events, secret, active) '
        'VALUES (?, ?, ?, ?, 1)',
        (g.api_key_id, url, json.dumps(events), secret),
    )
    conn.commit()
    row = conn.execute(
        'SELECT * FROM webhooks WHERE id = ?', (cur.lastrowid,)
    ).fetchone()
    # Pokazujemy secret RAZ
    return success_response(_row_to_webhook(row, include_secret=True), status_code=201)


@api_v1_bp.route('/webhooks/<int:webhook_id>', methods=['DELETE'])
@require_api_v1
def delete_webhook(webhook_id):
    """DELETE /api/v1/webhooks/{id}."""
    from modules.database import get_db
    conn = get_db()
    existing = conn.execute(
        'SELECT id FROM webhooks WHERE id = ? AND api_key_id = ?',
        (webhook_id, g.api_key_id),
    ).fetchone()
    if not existing:
        return error_response(f'Webhook {webhook_id} not found',
                              ErrorCodes.NOT_FOUND, 404)
    conn.execute('DELETE FROM webhooks WHERE id = ?', (webhook_id,))
    conn.commit()
    return success_response({'id': webhook_id, 'deleted': True})


# ---------------------------------------------------------------------------
# Delivery worker (background thread)
# ---------------------------------------------------------------------------

_worker_started = False
_worker_lock = threading.Lock()
_worker_stop_event = threading.Event()

MAX_ATTEMPTS = 5
POLL_INTERVAL_SECONDS = 30
DELIVERY_TIMEOUT_SECONDS = 10


def _deliver_one(delivery_row) -> tuple[bool, str]:
    """Wysyla jeden POST na URL webhooka.

    Returns: (success, error_msg_or_status)
    """
    try:
        import requests  # lazy import zeby nie crashnac w srodowiskach bez
    except ImportError:
        return False, 'requests library not available'

    try:
        from modules.database import get_db
        conn = get_db()
        wh = conn.execute(
            'SELECT url, secret, active FROM webhooks WHERE id = ?',
            (delivery_row['webhook_id'],),
        ).fetchone()
        if not wh or not wh['active']:
            return False, 'webhook disabled or deleted'

        payload_bytes = (delivery_row['payload'] or '').encode('utf-8')
        sig = _compute_signature(wh['secret'], payload_bytes)
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'AkcesHub-Webhook/1.0',
            'X-Akces-Event': delivery_row['event_type'],
            'X-Akces-Signature': sig,
            'X-Akces-Delivery-Id': str(delivery_row['id']),
        }
        resp = requests.post(
            wh['url'],
            data=payload_bytes,
            headers=headers,
            timeout=DELIVERY_TIMEOUT_SECONDS,
        )
        if 200 <= resp.status_code < 300:
            return True, f'HTTP {resp.status_code}'
        if 400 <= resp.status_code < 500:
            return False, f'HTTP {resp.status_code}: {resp.text[:200]}'
        # 5xx — retry
        return False, f'HTTP {resp.status_code} (retry)'
    except Exception as e:
        return False, f'exception: {e}'[:500]


def _next_retry_at(attempts: int) -> datetime:
    """Exponential backoff: 2^attempts minutes."""
    return datetime.utcnow() + timedelta(minutes=2 ** min(attempts, 6))


def process_pending_deliveries(max_batch=50):
    """Jeden przebieg workera. Wydzielone zeby dalo sie testowac.

    Returns: dict ze statystyka (processed, success, failed, still_pending).
    """
    from modules.database import get_db
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM webhook_deliveries "
        "WHERE status = 'pending' "
        "  AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP) "
        "ORDER BY created_at ASC LIMIT ?",
        (max_batch,),
    ).fetchall()

    stats = {'processed': 0, 'success': 0, 'failed': 0, 'still_pending': 0}
    for row in rows:
        ok, msg = _deliver_one(row)
        stats['processed'] += 1
        attempts = int(row['attempts'] or 0) + 1
        is_4xx = msg.startswith('HTTP 4')
        if ok:
            conn.execute(
                "UPDATE webhook_deliveries SET status = 'success', "
                "attempts = ?, last_error = NULL, completed_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (attempts, row['id']),
            )
            stats['success'] += 1
        elif is_4xx or attempts >= MAX_ATTEMPTS:
            conn.execute(
                "UPDATE webhook_deliveries SET status = 'failed', "
                "attempts = ?, last_error = ?, completed_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (attempts, msg, row['id']),
            )
            stats['failed'] += 1
        else:
            conn.execute(
                "UPDATE webhook_deliveries SET attempts = ?, last_error = ?, "
                "next_retry_at = ? WHERE id = ?",
                (attempts, msg, _next_retry_at(attempts).isoformat(), row['id']),
            )
            stats['still_pending'] += 1
    conn.commit()
    return stats


def _worker_loop():
    """Background loop: co POLL_INTERVAL_SECONDS przetwarza pending."""
    while not _worker_stop_event.is_set():
        try:
            process_pending_deliveries()
        except Exception as e:
            print(f'[WARN] webhook worker iteration failed: {e}')
        # Sleep ale respektuj stop event
        _worker_stop_event.wait(POLL_INTERVAL_SECONDS)


def start_delivery_worker():
    """Uruchamia daemon thread. Idempotent — pozniejsze wywolania no-op."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        # Nie startuj w test mode — testy moga mockowac lub wolac process_pending
        import os
        if os.environ.get('AKCES_TEST_MODE') == '1':
            _worker_started = True
            return
        t = threading.Thread(target=_worker_loop, daemon=True,
                             name='api-v1-webhook-worker')
        t.start()
        _worker_started = True
        print('[OK] API v1 webhook delivery worker started')


def stop_delivery_worker():
    """Stop workera (testy/shutdown)."""
    _worker_stop_event.set()
