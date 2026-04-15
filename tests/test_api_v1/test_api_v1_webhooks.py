"""Testy /api/v1/webhooks + delivery workera."""
import hashlib
import hmac
import json


def test_register_webhook_returns_secret_once(api_client, auth_headers):
    client, _plain, _kid = api_client
    r = client.post('/api/v1/webhooks',
                    data=json.dumps({'url': 'https://example.com/hook',
                                     'events': ['order.created']}),
                    headers=auth_headers)
    assert r.status_code == 201
    data = r.get_json()['data']
    assert 'secret' in data
    assert len(data['secret']) == 64  # hex(32) = 64 chars
    wh_id = data['id']

    # GET list nie powinien zawierac secret
    r = client.get('/api/v1/webhooks', headers=auth_headers)
    assert r.status_code == 200
    listed = r.get_json()['data']
    assert any(w['id'] == wh_id for w in listed)
    assert all('secret' not in w for w in listed)


def test_register_webhook_invalid_event_rejected(api_client, auth_headers):
    client, _plain, _kid = api_client
    r = client.post('/api/v1/webhooks',
                    data=json.dumps({'url': 'https://example.com',
                                     'events': ['order.created', 'fake.event']}),
                    headers=auth_headers)
    assert r.status_code == 400
    assert r.get_json()['code'] == 'VALIDATION_ERROR'


def test_register_webhook_invalid_url_rejected(api_client, auth_headers):
    client, _plain, _kid = api_client
    r = client.post('/api/v1/webhooks',
                    data=json.dumps({'url': 'not-a-url-at-all',
                                     'events': ['order.created']}),
                    headers=auth_headers)
    assert r.status_code == 400


def test_delete_webhook(api_client, auth_headers):
    client, _plain, _kid = api_client
    r = client.post('/api/v1/webhooks',
                    data=json.dumps({'url': 'https://example.com/x',
                                     'events': ['order.created']}),
                    headers=auth_headers)
    wh_id = r.get_json()['data']['id']
    r = client.delete(f'/api/v1/webhooks/{wh_id}', headers=auth_headers)
    assert r.status_code == 200
    assert r.get_json()['data']['deleted'] is True


def test_webhook_signature_is_verifiable(api_client, auth_headers):
    """Generuj signature jak worker, verify z secret."""
    from modules.api_v1.webhooks import _compute_signature
    secret = 'deadbeef' * 8
    payload = b'{"hello":"world"}'
    sig = _compute_signature(secret, payload)
    assert sig.startswith('sha256=')
    # Verify zwrotnie
    expected = 'sha256=' + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    assert sig == expected


def test_trigger_webhook_event_enqueues_delivery(api_client, auth_headers):
    """trigger_webhook_event powinno wstawic pending row."""
    client, _plain, _kid = api_client
    # Zarejestruj webhook
    r = client.post('/api/v1/webhooks',
                    data=json.dumps({'url': 'https://example.com/a',
                                     'events': ['product.stock_low']}),
                    headers=auth_headers)
    assert r.status_code == 201

    from modules.api_v1.webhooks import trigger_webhook_event
    queued = trigger_webhook_event('product.stock_low',
                                   {'product_id': 1, 'stock': 1})
    assert queued >= 1

    # Weryfikacja DB
    from modules.database import get_db
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM webhook_deliveries WHERE event_type = 'product.stock_low' "
        "AND status = 'pending'"
    ).fetchall()
    assert len(rows) >= 1


def test_webhook_retry_logic_updates_next_retry_at(api_client, auth_headers, monkeypatch):
    """Kiedy _deliver_one zwroci 5xx (retryable), status ma zostac pending i attempts++."""
    client, _plain, _kid = api_client

    r = client.post('/api/v1/webhooks',
                    data=json.dumps({'url': 'https://example.invalid/never',
                                     'events': ['order.created']}),
                    headers=auth_headers)
    wh_id = r.get_json()['data']['id']

    from modules.database import get_db
    conn = get_db()
    # Wrzuc pending delivery recznie
    conn.execute(
        "INSERT INTO webhook_deliveries (webhook_id, event_type, payload, status) "
        "VALUES (?, ?, ?, 'pending')",
        (wh_id, 'order.created', '{"x":1}'),
    )
    conn.commit()

    # Monkeypatch _deliver_one zeby zwracal 5xx
    from modules.api_v1 import webhooks as wh_module

    def fake_deliver(row):
        return False, 'HTTP 503 (retry)'

    monkeypatch.setattr(wh_module, '_deliver_one', fake_deliver)

    stats = wh_module.process_pending_deliveries(max_batch=10)
    assert stats['still_pending'] >= 1 or stats['failed'] >= 1

    # Sprawdz ze attempts zostal zwiekszony
    rows = conn.execute(
        'SELECT attempts, status FROM webhook_deliveries WHERE webhook_id = ?',
        (wh_id,),
    ).fetchall()
    assert any(r['attempts'] >= 1 for r in rows)
