"""Shared fixtures dla test_api_v1/*."""
import json
import os
import sys

import pytest

# Zapewnij sciezki
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@pytest.fixture(scope='function')
def api_client():
    """Flask test client z zarejestrowanym API v1.

    Tworzy klucz testowy w bazie i zwraca tuple (client, plain_key).
    """
    os.environ['AKCES_TEST_MODE'] = '1'
    from app import app
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

    # Upewnij sie ze schema DB jest zaaplikowany (init_db idempotent)
    from modules.database import init_db, get_db
    init_db()

    # Reset rate limiter state miedzy testami
    try:
        from modules.api_v1.rate_limit import reset_rate_limits
        reset_rate_limits()
    except ImportError:
        pass

    # Zapewnij czyste stany kluczy dla testow
    conn = get_db()
    # Usun wczesniejsze testowe klucze po nazwie prefix
    conn.execute("DELETE FROM api_keys WHERE name LIKE 'test:%'")
    # Wyczysc webhooks i deliveries (cascading delete nie zawsze jest aktywny)
    conn.execute("DELETE FROM webhook_deliveries")
    conn.execute("DELETE FROM webhooks")
    conn.execute("DELETE FROM api_usage_log")
    conn.commit()

    # Wygeneruj testowy klucz
    from modules.api_v1.auth import generate_api_key
    plain, key_hash, key_prefix = generate_api_key()
    cur = conn.execute(
        'INSERT INTO api_keys (key_hash, key_prefix, name, rate_limit_per_min) '
        'VALUES (?, ?, ?, ?)',
        (key_hash, key_prefix, 'test:primary', 1000),  # wysoki limit dla testow
    )
    conn.commit()
    key_id = cur.lastrowid

    client = app.test_client()
    yield client, plain, key_id

    # Cleanup
    try:
        conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
        conn.commit()
    except Exception:
        pass


@pytest.fixture
def auth_headers(api_client):
    """Gotowe headery z X-API-Key."""
    _client, plain, _kid = api_client
    return {'X-API-Key': plain, 'Content-Type': 'application/json'}


def _post_json(client, path, payload, headers):
    return client.post(path, data=json.dumps(payload), headers=headers)


def _put_json(client, path, payload, headers):
    return client.put(path, data=json.dumps(payload), headers=headers)
