"""Testy autentykacji API v1."""
import json


def test_health_public_no_auth(api_client):
    """GET /api/v1/health jest publiczny."""
    client, _plain, _kid = api_client
    r = client.get('/api/v1/health')
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'success'
    assert body['data']['ok'] is True
    assert body['data']['version'] == 'v1'


def test_missing_api_key_returns_401(api_client):
    client, _plain, _kid = api_client
    r = client.get('/api/v1/products')
    assert r.status_code == 401
    body = r.get_json()
    assert body['status'] == 'error'
    assert body['code'] == 'MISSING_API_KEY'


def test_invalid_api_key_returns_401(api_client):
    client, _plain, _kid = api_client
    r = client.get('/api/v1/products',
                   headers={'X-API-Key': 'ak_live_not_a_real_key_garbage_123'})
    assert r.status_code == 401
    body = r.get_json()
    assert body['code'] == 'INVALID_API_KEY'


def test_valid_api_key_passes(api_client):
    client, plain, _kid = api_client
    r = client.get('/api/v1/products', headers={'X-API-Key': plain})
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'success'
    assert 'data' in body
    assert 'meta' in body


def test_bearer_token_works(api_client):
    """Authorization: Bearer <key> jest alternatywa dla X-API-Key."""
    client, plain, _kid = api_client
    r = client.get('/api/v1/me', headers={'Authorization': f'Bearer {plain}'})
    assert r.status_code == 200
    assert r.get_json()['data']['name'] == 'test:primary'


def test_revoked_key_returns_403(api_client):
    client, plain, key_id = api_client
    from modules.database import get_db
    conn = get_db()
    conn.execute('UPDATE api_keys SET revoked_at = CURRENT_TIMESTAMP WHERE id = ?', (key_id,))
    conn.commit()

    r = client.get('/api/v1/products', headers={'X-API-Key': plain})
    assert r.status_code == 403
    body = r.get_json()
    assert body['code'] == 'API_KEY_REVOKED'


def test_rate_limit_enforced(api_client):
    """Nadmiarowe requesty dostaja 429. Testujemy z niskim limitem."""
    client, plain, key_id = api_client
    from modules.database import get_db
    from modules.api_v1.rate_limit import reset_rate_limits
    conn = get_db()
    # Ustaw bardzo niski limit
    conn.execute('UPDATE api_keys SET rate_limit_per_min = 3 WHERE id = ?', (key_id,))
    conn.commit()
    reset_rate_limits()

    headers = {'X-API-Key': plain}
    # Pierwsze 3 requesty OK
    for _ in range(3):
        r = client.get('/api/v1/products', headers=headers)
        assert r.status_code == 200
    # 4ty dostaje 429
    r = client.get('/api/v1/products', headers=headers)
    assert r.status_code == 429
    assert r.get_json()['code'] == 'RATE_LIMIT_EXCEEDED'
    # Rate limit headers sa obecne
    assert 'X-RateLimit-Limit' in r.headers
    assert r.headers.get('X-RateLimit-Remaining') == '0'


def test_me_endpoint_returns_key_info(api_client):
    client, plain, _kid = api_client
    r = client.get('/api/v1/me', headers={'X-API-Key': plain})
    assert r.status_code == 200
    data = r.get_json()['data']
    assert data['name'] == 'test:primary'
    assert 'key_prefix' in data
    assert 'rate_limit_per_min' in data
