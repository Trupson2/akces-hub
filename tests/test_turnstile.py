"""Testy dla modules.turnstile - Cloudflare Turnstile antybot."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import turnstile  # noqa: E402


@pytest.fixture
def clean_env(monkeypatch):
    """Wyczysc Turnstile env vars przed kazdym testem."""
    monkeypatch.delenv('TURNSTILE_SITE_KEY', raising=False)
    monkeypatch.delenv('TURNSTILE_SECRET_KEY', raising=False)


def _mock_ok_response(success=True, error_codes=None):
    """Pomocnik tworzacy mock requests.Response."""
    r = MagicMock()
    r.ok = True
    r.status_code = 200
    r.json.return_value = {
        'success': success,
        'error-codes': error_codes or [],
        'hostname': 'example.com',
        'action': 'login',
    }
    return r


# =====================================================================
# is_enabled / get_site_key / get_secret_key
# =====================================================================

def test_feature_disabled_when_no_keys(clean_env):
    """Bez zmiennych env — feature wylaczony."""
    assert turnstile.is_enabled() is False
    assert turnstile.get_site_key() == ''
    assert turnstile.get_secret_key() == ''


def test_feature_disabled_when_only_site_key(clean_env, monkeypatch):
    """Tylko site_key bez secret -> disabled."""
    monkeypatch.setenv('TURNSTILE_SITE_KEY', '0x4AAASiteKey')
    assert turnstile.is_enabled() is False


def test_feature_disabled_when_only_secret(clean_env, monkeypatch):
    """Tylko secret bez site_key -> disabled."""
    monkeypatch.setenv('TURNSTILE_SECRET_KEY', '0x4AAASecretKey')
    assert turnstile.is_enabled() is False


def test_feature_enabled_when_both_keys(clean_env, monkeypatch):
    """Obie zmienne ustawione -> enabled."""
    monkeypatch.setenv('TURNSTILE_SITE_KEY', '0x4AAASiteKey')
    monkeypatch.setenv('TURNSTILE_SECRET_KEY', '0x4AAASecretKey')
    assert turnstile.is_enabled() is True


def test_whitespace_only_keys_disabled(clean_env, monkeypatch):
    """Whitespace-only zmienne traktowane jak puste."""
    monkeypatch.setenv('TURNSTILE_SITE_KEY', '   ')
    monkeypatch.setenv('TURNSTILE_SECRET_KEY', '\t\n')
    assert turnstile.is_enabled() is False


# =====================================================================
# verify_token - scenariusze
# =====================================================================

def test_empty_token_returns_failure(clean_env, monkeypatch):
    """Brak tokena -> success=False, bez network call."""
    monkeypatch.setenv('TURNSTILE_SECRET_KEY', 'secret')
    with patch('modules.turnstile.requests.post') as mock_post:
        result = turnstile.verify_token('')
        assert result.success is False
        assert 'missing-input-response' in result.error_codes
        mock_post.assert_not_called()


def test_missing_secret_returns_failure(clean_env):
    """Brak secret key w env -> porazka, bez network callu."""
    with patch('modules.turnstile.requests.post') as mock_post:
        result = turnstile.verify_token('some-token')
        assert result.success is False
        assert 'missing-input-secret' in result.error_codes
        mock_post.assert_not_called()


def test_valid_token_cloudflare_success(clean_env, monkeypatch):
    """Cloudflare zwraca success=True -> result.success=True."""
    monkeypatch.setenv('TURNSTILE_SECRET_KEY', 'secret')
    with patch('modules.turnstile.requests.post', return_value=_mock_ok_response(success=True)) as mock_post:
        result = turnstile.verify_token('valid-token', remote_ip='1.2.3.4')
        assert result.success is True
        assert result.error_codes == ()
        assert result.hostname == 'example.com'

        mock_post.assert_called_once()
        # requests.post(url, data=payload, timeout=...) — data w kwargs
        data = mock_post.call_args.kwargs.get('data', {})
        assert data.get('remoteip') == '1.2.3.4'
        assert data.get('response') == 'valid-token'
        assert data.get('secret') == 'secret'


def test_invalid_token_cloudflare_rejects(clean_env, monkeypatch):
    """Cloudflare zwraca success=False z error-codes."""
    monkeypatch.setenv('TURNSTILE_SECRET_KEY', 'secret')
    resp = _mock_ok_response(success=False, error_codes=['invalid-input-response'])
    with patch('modules.turnstile.requests.post', return_value=resp):
        result = turnstile.verify_token('bad-token')
        assert result.success is False
        assert 'invalid-input-response' in result.error_codes


def test_network_error_fails_closed(clean_env, monkeypatch):
    """Network error -> FAIL-CLOSED (success=False)."""
    monkeypatch.setenv('TURNSTILE_SECRET_KEY', 'secret')
    with patch('modules.turnstile.requests.post', side_effect=requests.ConnectionError('down')):
        result = turnstile.verify_token('some-token')
        assert result.success is False
        assert 'network-error' in result.error_codes


def test_timeout_fails_closed(clean_env, monkeypatch):
    """Timeout do Cloudflare -> FAIL-CLOSED."""
    monkeypatch.setenv('TURNSTILE_SECRET_KEY', 'secret')
    with patch('modules.turnstile.requests.post', side_effect=requests.Timeout('timeout')):
        result = turnstile.verify_token('some-token')
        assert result.success is False
        assert 'network-error' in result.error_codes


def test_http_5xx_fails_closed(clean_env, monkeypatch):
    """Cloudflare zwraca 500 -> FAIL-CLOSED."""
    monkeypatch.setenv('TURNSTILE_SECRET_KEY', 'secret')
    bad_resp = MagicMock()
    bad_resp.ok = False
    bad_resp.status_code = 500
    with patch('modules.turnstile.requests.post', return_value=bad_resp):
        result = turnstile.verify_token('some-token')
        assert result.success is False
        assert 'http-error' in result.error_codes


def test_invalid_json_response_fails_closed(clean_env, monkeypatch):
    """Nieparsowalny JSON -> FAIL-CLOSED."""
    monkeypatch.setenv('TURNSTILE_SECRET_KEY', 'secret')
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.json.side_effect = ValueError('bad json')
    with patch('modules.turnstile.requests.post', return_value=resp):
        result = turnstile.verify_token('some-token')
        assert result.success is False
        assert 'invalid-json' in result.error_codes


def test_explicit_secret_parameter(clean_env):
    """Secret mozna podac jawnie (omija env var)."""
    # Env pusty, ale podajemy secret explicit
    with patch('modules.turnstile.requests.post', return_value=_mock_ok_response(success=True)):
        result = turnstile.verify_token('t', secret='explicit-secret')
        assert result.success is True


# =====================================================================
# Integracja: login handler przy enabled / disabled
# =====================================================================

@pytest.fixture
def app_with_user(monkeypatch, tmp_path):
    """Stwor app z testowym userem w tymczasowej bazie."""
    os.environ['AKCES_TEST_MODE'] = '1'
    try:
        from app import app
        from modules.auth import _get_auth_db, _hash_password, _users_exist_cache, init_auth_db

        init_auth_db()
        conn = _get_auth_db()
        conn.execute("DELETE FROM users WHERE username = 'ts_test_user'")
        conn.execute(
            "INSERT INTO users (username, password_hash, rola, aktywny) VALUES (?, ?, 'admin', 1)",
            ('ts_test_user', _hash_password('secret_password_123'))
        )
        conn.commit()
        conn.close()
        _users_exist_cache['val'] = True
        _users_exist_cache['ts'] = 0

        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False

        yield app
    finally:
        try:
            conn = _get_auth_db()
            conn.execute("DELETE FROM users WHERE username = 'ts_test_user'")
            conn.commit()
            conn.close()
        except Exception:
            pass
        os.environ.pop('AKCES_TEST_MODE', None)


def test_login_disabled_feature_does_not_require_token(clean_env, app_with_user):
    """Feature disabled -> login NIE wymaga cf-turnstile-response."""
    client = app_with_user.test_client()
    resp = client.post(
        '/auth/login',
        data={'username': 'ts_test_user', 'password': 'secret_password_123'},
        follow_redirects=False,
    )
    # Powinien byc redirect (302), nie 403 ani error
    assert resp.status_code in (302, 303), f'Expected redirect, got {resp.status_code}'


def test_login_enabled_feature_blocks_missing_token(clean_env, app_with_user, monkeypatch):
    """Feature enabled + brak tokena -> login blocked, render_template('login.html')."""
    monkeypatch.setenv('TURNSTILE_SITE_KEY', '0x4AAASiteKey')
    monkeypatch.setenv('TURNSTILE_SECRET_KEY', '0x4AAASecretKey')

    client = app_with_user.test_client()
    with patch('modules.turnstile.requests.post') as mock_post:
        resp = client.post(
            '/auth/login',
            data={'username': 'ts_test_user', 'password': 'secret_password_123'},
            follow_redirects=False,
        )
        # Brak tokena = verify_token zwraca False zanim dojdzie do requests.post
        mock_post.assert_not_called()
        # Login NIE powinien przejsc (brak redirectu)
        assert resp.status_code == 200
        assert b'antybot' in resp.data.lower() or b'Walidacja' in resp.data


def test_login_enabled_feature_passes_valid_token(clean_env, app_with_user, monkeypatch):
    """Feature enabled + valid token -> login przechodzi."""
    monkeypatch.setenv('TURNSTILE_SITE_KEY', '0x4AAASiteKey')
    monkeypatch.setenv('TURNSTILE_SECRET_KEY', '0x4AAASecretKey')

    client = app_with_user.test_client()
    with patch('modules.turnstile.requests.post', return_value=_mock_ok_response(success=True)):
        resp = client.post(
            '/auth/login',
            data={
                'username': 'ts_test_user',
                'password': 'secret_password_123',
                'cf-turnstile-response': 'valid-token',
            },
            follow_redirects=False,
        )
        # Valid = login ok = redirect
        assert resp.status_code in (302, 303)


def test_login_enabled_feature_blocks_invalid_token(clean_env, app_with_user, monkeypatch):
    """Feature enabled + invalid token -> login blocked."""
    monkeypatch.setenv('TURNSTILE_SITE_KEY', '0x4AAASiteKey')
    monkeypatch.setenv('TURNSTILE_SECRET_KEY', '0x4AAASecretKey')

    client = app_with_user.test_client()
    invalid_resp = _mock_ok_response(success=False, error_codes=['invalid-input-response'])
    with patch('modules.turnstile.requests.post', return_value=invalid_resp):
        resp = client.post(
            '/auth/login',
            data={
                'username': 'ts_test_user',
                'password': 'secret_password_123',
                'cf-turnstile-response': 'fake-token',
            },
            follow_redirects=False,
        )
        # Rejected token -> login NIE przechodzi
        assert resp.status_code == 200


def test_login_enabled_network_error_fails_closed(clean_env, app_with_user, monkeypatch):
    """Feature enabled + Cloudflare down (network error) -> FAIL-CLOSED."""
    monkeypatch.setenv('TURNSTILE_SITE_KEY', '0x4AAASiteKey')
    monkeypatch.setenv('TURNSTILE_SECRET_KEY', '0x4AAASecretKey')

    client = app_with_user.test_client()
    with patch('modules.turnstile.requests.post', side_effect=requests.ConnectionError('down')):
        resp = client.post(
            '/auth/login',
            data={
                'username': 'ts_test_user',
                'password': 'secret_password_123',
                'cf-turnstile-response': 'maybe-valid-token',
            },
            follow_redirects=False,
        )
        # Network error = fail-closed = login blocked
        assert resp.status_code == 200
