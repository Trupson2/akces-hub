"""Testy dla modules.totp (2FA TOTP + backup codes) + route'y /auth/2fa/*."""

import json
import os
import re
import sys
import time

import pyotp
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import totp as totp_module  # noqa: E402


# =====================================================================
# CZESC 1: modules.totp (czyste jednostkowe)
# =====================================================================

def test_generate_secret_format():
    """Generate secret -> base32, min 16 znakow."""
    secret = totp_module.generate_secret()
    assert isinstance(secret, str)
    assert len(secret) >= 16
    # Base32 alphabet: A-Z, 2-7
    assert re.match(r'^[A-Z2-7]+=*$', secret), f'Not base32: {secret}'


def test_generate_qr_uri_format():
    """QR URI ma format otpauth://totp/..."""
    secret = totp_module.generate_secret()
    uri = totp_module.generate_qr_uri('test_user', secret)
    assert uri.startswith('otpauth://totp/')
    assert 'secret=' in uri
    assert 'issuer=' in uri


def test_generate_qr_svg_is_svg_string():
    """QR SVG zwraca inline SVG string (bez <script>)."""
    secret = totp_module.generate_secret()
    svg = totp_module.generate_qr_svg('user123', secret)
    assert isinstance(svg, str)
    assert '<svg' in svg
    # Nie powinno byc <script> (CSP-safe)
    assert '<script' not in svg.lower()


def test_verify_current_code_passes():
    """Aktualny kod z pyotp -> verify pass."""
    secret = totp_module.generate_secret()
    totp = pyotp.TOTP(secret)
    current_code = totp.now()
    assert totp_module.verify_code(secret, current_code) is True


def test_verify_code_with_window_accepts_prev_and_next(monkeypatch):
    """Kod z poprzedniego okna (+/- 30s) -> pass."""
    secret = totp_module.generate_secret()
    totp = pyotp.TOTP(secret)

    # Kod z 30s temu
    prev_code = totp.at(int(time.time()) - 30)
    assert totp_module.verify_code(secret, prev_code, window=1) is True

    # Kod z +30s
    next_code = totp.at(int(time.time()) + 30)
    assert totp_module.verify_code(secret, next_code, window=1) is True


def test_verify_code_2min_old_fails():
    """Kod sprzed 2 minut (window=1) -> fail."""
    secret = totp_module.generate_secret()
    totp = pyotp.TOTP(secret)
    old_code = totp.at(int(time.time()) - 120)
    assert totp_module.verify_code(secret, old_code, window=1) is False


def test_verify_code_rejects_non_numeric():
    """Nie-cyfrowy string odrzucony."""
    secret = totp_module.generate_secret()
    assert totp_module.verify_code(secret, 'abcdef') is False
    assert totp_module.verify_code(secret, '') is False
    assert totp_module.verify_code(secret, '12345') is False  # za krotki
    assert totp_module.verify_code(secret, '1234567') is False  # za dlugi


def test_verify_code_empty_secret_fails():
    """Brak secret -> zawsze False."""
    assert totp_module.verify_code('', '123456') is False
    assert totp_module.verify_code(None, '123456') is False


def test_generate_backup_codes_count_and_format():
    """8 kodow w formacie XXXX-XXXX."""
    plain, hashed_json = totp_module.generate_backup_codes(n=8)
    assert len(plain) == 8
    # Format check
    pattern = re.compile(r'^[0-9A-F]{4}-[0-9A-F]{4}$')
    for code in plain:
        assert pattern.match(code), f'Bad format: {code}'
    # JSON z 8 hashami
    hashes = json.loads(hashed_json)
    assert isinstance(hashes, list)
    assert len(hashes) == 8
    for h in hashes:
        assert h.startswith('$2')  # bcrypt prefix


def test_generate_backup_codes_unique():
    """8 kodow -> wszystkie unikalne."""
    plain, _ = totp_module.generate_backup_codes(n=8)
    assert len(set(plain)) == 8


def test_verify_backup_code_works_once_then_removed():
    """Backup code uzyty raz -> drugi raz fail (removed)."""
    plain, hashed_json = totp_module.generate_backup_codes(n=3)
    used_code = plain[0]

    ok, new_hashes = totp_module.verify_backup_code(hashed_json, used_code)
    assert ok is True

    # Drugi raz -> fail
    ok2, _ = totp_module.verify_backup_code(new_hashes, used_code)
    assert ok2 is False


def test_verify_backup_code_accepts_without_dash():
    """User wpisal kod bez myslnika -> akceptuj."""
    plain, hashed_json = totp_module.generate_backup_codes(n=2)
    code_nodash = plain[0].replace('-', '')

    ok, _ = totp_module.verify_backup_code(hashed_json, code_nodash)
    assert ok is True


def test_verify_backup_code_case_insensitive():
    """Akceptuj lowercase."""
    plain, hashed_json = totp_module.generate_backup_codes(n=2)
    code_lower = plain[0].lower()

    ok, _ = totp_module.verify_backup_code(hashed_json, code_lower)
    assert ok is True


def test_verify_backup_code_wrong_rejected():
    """Niepoprawny kod -> fail, lista hashy bez zmian."""
    _, hashed_json = totp_module.generate_backup_codes(n=3)

    ok, new_hashes = totp_module.verify_backup_code(hashed_json, 'AAAA-BBBB')
    assert ok is False
    assert new_hashes == hashed_json


def test_verify_backup_code_empty_inputs():
    """Pusta lista lub pusty kod -> False."""
    assert totp_module.verify_backup_code('', 'AAAA-BBBB')[0] is False
    assert totp_module.verify_backup_code('[]', '')[0] is False
    assert totp_module.verify_backup_code('[]', 'AAAA-BBBB')[0] is False


def test_backup_codes_remaining_count():
    """backup_codes_remaining zwraca liczbe pozostalych."""
    _, hashed_json = totp_module.generate_backup_codes(n=5)
    assert totp_module.backup_codes_remaining(hashed_json) == 5

    assert totp_module.backup_codes_remaining('') == 0
    assert totp_module.backup_codes_remaining(None) == 0
    assert totp_module.backup_codes_remaining('invalid-json') == 0


# =====================================================================
# CZESC 2: Integracja z Flask app (routes /auth/2fa/*)
# =====================================================================

@pytest.fixture
def app_with_user_no_2fa(monkeypatch):
    """App + testowy user bez 2FA (do setup flow)."""
    os.environ['AKCES_TEST_MODE'] = '1'
    from app import app
    from modules.auth import _get_auth_db, _hash_password, _users_exist_cache, init_auth_db

    init_auth_db()

    conn = _get_auth_db()
    conn.execute("DELETE FROM users WHERE username = 'totp_test_user'")
    conn.execute(
        "INSERT INTO users (username, password_hash, rola, aktywny) VALUES (?, ?, 'admin', 1)",
        ('totp_test_user', _hash_password('totp_pass_123'))
    )
    conn.commit()
    user_id = conn.execute(
        "SELECT id FROM users WHERE username = 'totp_test_user'"
    ).fetchone()[0]
    conn.close()

    _users_exist_cache['val'] = True
    _users_exist_cache['ts'] = 0

    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

    yield app, user_id

    # Cleanup
    try:
        conn = _get_auth_db()
        conn.execute("DELETE FROM users WHERE username = 'totp_test_user'")
        conn.commit()
        conn.close()
    except Exception:
        pass
    os.environ.pop('AKCES_TEST_MODE', None)


@pytest.fixture
def app_with_user_has_2fa(monkeypatch):
    """App + testowy user z juz wlaczonym 2FA."""
    os.environ['AKCES_TEST_MODE'] = '1'
    from app import app
    from modules.auth import _get_auth_db, _hash_password, _users_exist_cache, init_auth_db

    init_auth_db()

    secret = pyotp.random_base32()
    plain_codes, hashed_json = totp_module.generate_backup_codes(n=3)

    conn = _get_auth_db()
    conn.execute("DELETE FROM users WHERE username = 'totp_2fa_user'")
    conn.execute(
        "INSERT INTO users (username, password_hash, rola, aktywny, "
        "totp_secret, totp_enabled, totp_backup_codes) VALUES (?, ?, 'admin', 1, ?, 1, ?)",
        ('totp_2fa_user', _hash_password('totp_pass_456'), secret, hashed_json)
    )
    conn.commit()
    user_id = conn.execute("SELECT id FROM users WHERE username = 'totp_2fa_user'").fetchone()[0]
    conn.close()

    _users_exist_cache['val'] = True
    _users_exist_cache['ts'] = 0

    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

    yield app, user_id, secret, plain_codes

    try:
        conn = _get_auth_db()
        conn.execute("DELETE FROM users WHERE username = 'totp_2fa_user'")
        conn.commit()
        conn.close()
    except Exception:
        pass
    os.environ.pop('AKCES_TEST_MODE', None)


def test_setup_get_shows_qr_and_secret(app_with_user_no_2fa):
    """GET /auth/2fa/setup -> strona z QR SVG + secret do wpisania."""
    app, user_id = app_with_user_no_2fa
    with app.test_client() as client:
        with client.session_transaction() as s:
            s['user_id'] = user_id
            s['username'] = 'totp_test_user'
            s['rola'] = 'admin'

        resp = client.get('/auth/2fa/setup')
        assert resp.status_code == 200
        assert b'<svg' in resp.data
        # Secret widoczny do manualnego wpisania
        assert b'secret' in resp.data.lower()


def test_setup_post_missing_code_shows_error(app_with_user_no_2fa):
    """POST /auth/2fa/setup bez kodu -> error, 2FA nie wlaczone."""
    app, user_id = app_with_user_no_2fa
    with app.test_client() as client:
        with client.session_transaction() as s:
            s['user_id'] = user_id
            s['username'] = 'totp_test_user'
            s['rola'] = 'admin'

        secret = pyotp.random_base32()
        resp = client.post('/auth/2fa/setup', data={'secret': secret, 'code': ''})
        assert resp.status_code == 200
        # User nie ma 2FA wlaczone
        from modules.auth import _get_auth_db
        conn = _get_auth_db()
        row = conn.execute('SELECT totp_enabled FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()
        assert row['totp_enabled'] == 0


def test_setup_post_bad_code_audit_logged(app_with_user_no_2fa):
    """POST z bad kodem -> error + 2FA nie wlaczone (audit log best-effort)."""
    app, user_id = app_with_user_no_2fa
    with app.test_client() as client:
        with client.session_transaction() as s:
            s['user_id'] = user_id
            s['username'] = 'totp_test_user'
            s['rola'] = 'admin'

        secret = pyotp.random_base32()
        resp = client.post('/auth/2fa/setup', data={'secret': secret, 'code': '000000'})
        assert resp.status_code == 200

        from modules.auth import _get_auth_db
        conn = _get_auth_db()
        row = conn.execute('SELECT totp_enabled FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()
        assert row['totp_enabled'] == 0


def test_setup_post_good_code_enables_2fa_returns_backup_codes(app_with_user_no_2fa):
    """POST z good kodem -> 2FA wlaczone + widocznych 8 backup codes."""
    app, user_id = app_with_user_no_2fa
    with app.test_client() as client:
        with client.session_transaction() as s:
            s['user_id'] = user_id
            s['username'] = 'totp_test_user'
            s['rola'] = 'admin'

        secret = pyotp.random_base32()
        current_code = pyotp.TOTP(secret).now()
        resp = client.post('/auth/2fa/setup', data={'secret': secret, 'code': current_code})
        assert resp.status_code == 200

        # Odpowiedz pokazuje backup codes
        body = resp.data.decode('utf-8', errors='ignore')
        # Szukaj formatu XXXX-XXXX w odpowiedzi
        codes_found = re.findall(r'\b[0-9A-F]{4}-[0-9A-F]{4}\b', body)
        assert len(codes_found) >= 8, f'Expected >=8 backup codes displayed, found {len(codes_found)}'

        # DB: 2FA wlaczone
        from modules.auth import _get_auth_db
        conn = _get_auth_db()
        row = conn.execute(
            'SELECT totp_enabled, totp_secret, totp_backup_codes FROM users WHERE id = ?',
            (user_id,)
        ).fetchone()
        conn.close()
        assert row['totp_enabled'] == 1
        assert row['totp_secret'] == secret
        assert row['totp_backup_codes']


def test_login_redirects_to_2fa_verify_when_enabled(app_with_user_has_2fa):
    """User z wlaczonym 2FA: login password -> redirect na /auth/2fa/verify."""
    app, user_id, secret, _ = app_with_user_has_2fa
    with app.test_client() as client:
        resp = client.post(
            '/auth/login',
            data={'username': 'totp_2fa_user', 'password': 'totp_pass_456'},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert '/auth/2fa/verify' in resp.headers.get('Location', '')

        # Pending user_id w sesji, ale jeszcze nie zalogowany
        with client.session_transaction() as s:
            assert s.get('user_id') is None
            assert s.get('2fa_pending_user_id') == user_id


def test_2fa_verify_good_code_completes_login(app_with_user_has_2fa):
    """POST /auth/2fa/verify z valid TOTP -> sesja w pelni ustawiona."""
    app, user_id, secret, _ = app_with_user_has_2fa
    with app.test_client() as client:
        # Najpierw login (password), zeby ustawic pending state
        client.post('/auth/login', data={'username': 'totp_2fa_user', 'password': 'totp_pass_456'})

        good_code = pyotp.TOTP(secret).now()
        resp = client.post('/auth/2fa/verify', data={'code': good_code}, follow_redirects=False)
        assert resp.status_code in (302, 303)

        with client.session_transaction() as s:
            assert s.get('user_id') == user_id
            assert s.get('2fa_verified') is True
            assert s.get('2fa_pending_user_id') is None


def test_2fa_verify_bad_code_stays_on_page(app_with_user_has_2fa):
    """Bad TOTP -> pozostaje na /auth/2fa/verify z error."""
    app, _, _, _ = app_with_user_has_2fa
    with app.test_client() as client:
        client.post('/auth/login', data={'username': 'totp_2fa_user', 'password': 'totp_pass_456'})

        resp = client.post('/auth/2fa/verify', data={'code': '000000'}, follow_redirects=False)
        assert resp.status_code == 200

        with client.session_transaction() as s:
            assert s.get('user_id') is None  # jeszcze nie zalogowany


def test_2fa_verify_backup_code_works_and_removed(app_with_user_has_2fa):
    """Backup code dziala 1x i znika."""
    app, user_id, _, backup_codes = app_with_user_has_2fa
    first_code = backup_codes[0]

    with app.test_client() as client:
        client.post('/auth/login', data={'username': 'totp_2fa_user', 'password': 'totp_pass_456'})

        resp = client.post('/auth/2fa/verify', data={'code': first_code}, follow_redirects=False)
        assert resp.status_code in (302, 303)

        # Drugi raz ten sam backup code -> fail
        client.get('/auth/logout')
        client.post('/auth/login', data={'username': 'totp_2fa_user', 'password': 'totp_pass_456'})
        resp2 = client.post('/auth/2fa/verify', data={'code': first_code}, follow_redirects=False)
        # Drugi raz -> bad code -> 200 (stays on page)
        assert resp2.status_code == 200


def test_admin_endpoint_blocks_when_2fa_pending(app_with_user_has_2fa):
    """require_admin: user z totp_enabled ale bez 2fa_verified -> redirect."""
    app, user_id, _, _ = app_with_user_has_2fa
    with app.test_client() as client:
        # Ustaw sesje RECZNIE omijajac normalny flow (symuluje np. stara sesje
        # ktora nie przeszla 2FA) - w praktyce session['user_id'] jest ale
        # session['2fa_verified'] NIE
        with client.session_transaction() as s:
            s['user_id'] = user_id
            s['username'] = 'totp_2fa_user'
            s['rola'] = 'admin'
            # bez 2fa_verified

        # Dowolny endpoint z @require_admin — sprobujmy /auth/users
        resp = client.get('/auth/users', follow_redirects=False)
        # Powinien byc redirect na /auth/2fa/verify
        assert resp.status_code in (302, 303)
        loc = resp.headers.get('Location', '')
        assert '/auth/2fa/verify' in loc


def test_disable_2fa_requires_code(app_with_user_has_2fa):
    """POST /auth/2fa/disable bez kodu -> 2FA nadal wlaczone."""
    app, user_id, _, _ = app_with_user_has_2fa
    with app.test_client() as client:
        with client.session_transaction() as s:
            s['user_id'] = user_id
            s['username'] = 'totp_2fa_user'
            s['rola'] = 'admin'
            s['2fa_verified'] = True

        # Bez kodu
        client.post('/auth/2fa/disable', data={'code': ''}, follow_redirects=False)

        from modules.auth import _get_auth_db
        conn = _get_auth_db()
        row = conn.execute('SELECT totp_enabled FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()
        assert row['totp_enabled'] == 1  # nadal wlaczone


def test_disable_2fa_with_good_code_succeeds(app_with_user_has_2fa):
    """POST /auth/2fa/disable z valid kodem -> wylacza 2FA."""
    app, user_id, secret, _ = app_with_user_has_2fa
    with app.test_client() as client:
        with client.session_transaction() as s:
            s['user_id'] = user_id
            s['username'] = 'totp_2fa_user'
            s['rola'] = 'admin'
            s['2fa_verified'] = True

        good_code = pyotp.TOTP(secret).now()
        client.post('/auth/2fa/disable', data={'code': good_code})

        from modules.auth import _get_auth_db
        conn = _get_auth_db()
        row = conn.execute(
            'SELECT totp_enabled, totp_secret FROM users WHERE id = ?', (user_id,)
        ).fetchone()
        conn.close()
        assert row['totp_enabled'] == 0
        assert row['totp_secret'] is None
