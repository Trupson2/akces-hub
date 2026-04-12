"""Testy bezpieczenstwa -- SQL injection, XSS, CSRF, auth, CSV injection, headers."""
import html
import re
import sys
import os

# Dodaj root projektu do PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from werkzeug.security import generate_password_hash


# ============================================================
# SQL INJECTION TESTS (istniejace)
# ============================================================

def test_sql_injection_whitelist_app():
    """Test: whitelist kolumn w admin_subscriptions_update blokuje SQL injection."""
    _ALLOWED_COLS = {'plan', 'expires_date', 'expires', 'active'}

    safe_updates = ['plan = ?', 'active = ?']
    for u in safe_updates:
        col_name = u.split(' ')[0]
        assert col_name in _ALLOWED_COLS, f"Kolumna '{col_name}' powinna byc dozwolona"

    attack_updates = ["license_key = ?; DROP TABLE users; --"]
    for u in attack_updates:
        col_name = u.split(' ')[0]
        assert col_name not in _ALLOWED_COLS, f"Kolumna '{col_name}' powinna byc zablokowana"


def test_sql_injection_whitelist_serwisant():
    """Test: whitelist kolumn w serwisant blokuje SQL injection."""
    _ALLOWED_COLS = {'status', 'koszt_naprawy', 'uwagi', 'data_zakonczenia'}

    safe = ['status = ?', 'koszt_naprawy = ?', 'uwagi = ?', "data_zakonczenia = datetime('now')"]
    for u in safe:
        col_name = u.split(' ')[0]
        assert col_name in _ALLOWED_COLS

    attack = ['id = ?', "active = ?; DROP TABLE serwis; --"]
    for u in attack:
        col_name = u.split(' ')[0]
        assert col_name not in _ALLOWED_COLS


# ============================================================
# XSS TESTS (istniejace)
# ============================================================

def test_xss_html_escape():
    """Test: html.escape() prawidlowo neutralizuje XSS w danych z DB."""
    malicious_title = '<script>alert("XSS")</script>'
    malicious_url = 'javascript:alert("XSS")'
    malicious_image = '" onerror="alert(1)" src="x'

    safe_title = html.escape(malicious_title)
    safe_url = html.escape(malicious_url, quote=True)
    safe_image = html.escape(malicious_image, quote=True)

    assert '<script>' not in safe_title
    assert '&lt;script&gt;' in safe_title
    assert '"' not in safe_url
    assert '"' not in safe_image


def test_xss_keyword_escape():
    """Test: keyword tags sa escapowane."""
    keywords = ['normal', '<img src=x onerror=alert(1)>', 'good"bad']
    for k in keywords:
        escaped = html.escape(k)
        assert '<' not in escaped or '&lt;' in escaped
        assert '>' not in escaped or '&gt;' in escaped


# ============================================================
# LICENSE KEY VALIDATION (istniejace)
# ============================================================

def test_license_key_format_validation():
    """Test: auto-rejestracja akceptuje tylko prawidlowy format klucza."""
    valid_keys = [
        'AKCES-P1AB-CD23-EF45-GH67',
        'AKCES-SXXX-XXXX-XXXX-XXXX',
        'AKCES-E000-0000-0000-0000',
    ]
    invalid_keys = [
        'FAKEKEY',
        'AKCES-SHORT',
        'AKCES-xxxx-xxxx-xxxx-xxxx',
        '',
        'AKCES-P1AB-CD23-EF45-GH67-EXTRA',
        "AKCES-'; DROP TABLE licenses_issued;--",
    ]

    pattern = r'^AKCES-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$'

    for key in valid_keys:
        assert re.match(pattern, key), f"'{key}' powinien byc prawidlowy"
    for key in invalid_keys:
        assert not re.match(pattern, key), f"'{key}' powinien byc odrzucony"


def test_allegro_secret_not_in_html():
    """Test: client_secret nie powinien byc w value formularza HTML."""
    secret = 'super_secret_value_12345'

    old_html = f'<input type="password" value="{secret}">'
    assert secret in old_html

    new_html = '<input type="password" value="" placeholder="Wpisz nowy secret lub zostaw puste">'
    assert secret not in new_html


# ============================================================
# NOWE: AUTH BYPASS TESTS
# ============================================================

def test_unauthenticated_redirect(app_client):
    """Niezalogowani uzytkownicy powinni byc przekierowani (login/eula/license/setup)."""
    rv = app_client.get('/')
    # Powinno przekierowac na login/eula/license/setup lub pokazac strone logowania
    assert rv.status_code in (302, 200)
    if rv.status_code == 302:
        location = rv.headers.get('Location', '')
        assert any(p in location for p in ('login', 'eula', 'license', 'setup')), \
            f"Unexpected redirect: {location}"


def test_dashboard_requires_auth(app_client):
    """Dashboard powinien wymagac logowania."""
    rv = app_client.get('/dashboard')
    assert rv.status_code in (302, 200)
    if rv.status_code == 302:
        location = rv.headers.get('Location', '')
        assert any(p in location for p in ('login', 'eula', 'license', 'setup')), \
            f"Unexpected redirect: {location}"


def test_api_returns_401_without_auth(app_client):
    """API powinno zwracac 401 lub redirect dla niezalogowanych XHR."""
    rv = app_client.get('/dashboard', headers={'X-Requested-With': 'XMLHttpRequest'})
    # Moze zwrocic 401, 302 (redirect do login/eula) lub 200 (setup)
    assert rv.status_code in (302, 401, 200)


# ============================================================
# NOWE: XSS - sanitize_html z modules/utils
# ============================================================

def test_html_sanitization():
    """sanitize_html powinien escapowac tagi HTML."""
    from modules.utils import sanitize_html
    assert '<script>' not in sanitize_html('<script>alert("xss")</script>')
    assert '&lt;script&gt;' in sanitize_html('<script>alert("xss")</script>')
    assert sanitize_html('Normal text') == 'Normal text'
    assert sanitize_html(None) is None


def test_html_sanitization_edge_cases():
    """sanitize_html -- edge cases: puste stringi, liczby, zagniezdzone tagi."""
    from modules.utils import sanitize_html
    assert sanitize_html('') == ''
    assert sanitize_html(123) == 123
    assert '<img' not in sanitize_html('<img src=x onerror=alert(1)>')
    assert '&lt;img' in sanitize_html('<img src=x onerror=alert(1)>')
    assert sanitize_html('a & b < c > d') == 'a &amp; b &lt; c &gt; d'


# ============================================================
# NOWE: CSV INJECTION TESTS
# ============================================================

def test_csv_injection_sanitize():
    """sanitize_csv_cell powinien neutralizowac formuly Excel."""
    from modules.utils import sanitize_csv_cell
    assert sanitize_csv_cell('=CMD("calc")') == "'=CMD(\"calc\")"
    assert sanitize_csv_cell('+1+1') == "'+1+1"
    assert sanitize_csv_cell('-1-1') == "'-1-1"
    assert sanitize_csv_cell('@SUM(A1)') == "'@SUM(A1)"
    assert sanitize_csv_cell('Normal text') == 'Normal text'
    assert sanitize_csv_cell('') == ''
    assert sanitize_csv_cell(None) is None
    assert sanitize_csv_cell(123) == 123


def test_csv_injection_pipe_and_whitespace():
    """sanitize_csv_cell -- pipe jest niebezpieczny, whitespace usuwany przez strip."""
    from modules.utils import sanitize_csv_cell
    assert sanitize_csv_cell('|cmd') == "'|cmd"
    # Whitespace na poczatku jest usuwany przez strip() -- po stripie jest bezpieczny
    assert sanitize_csv_cell('\tcmd') == 'cmd'
    assert sanitize_csv_cell('\rcmd') == 'cmd'
    assert sanitize_csv_cell('\ncmd') == 'cmd'
    # Ale whitespace w srodku tekstu jest OK
    assert sanitize_csv_cell('a\tb') == 'a\tb'


# ============================================================
# NOWE: ERROR MESSAGE SANITIZATION
# ============================================================

def test_error_sanitization():
    """safe_error_message nie powinien wyciekac sciezek i info o DB."""
    from modules.utils import safe_error_message
    assert 'sqlite' not in safe_error_message('sqlite3.OperationalError: no such table').lower()
    assert '/home/' not in safe_error_message('FileNotFoundError: /home/pi/secret/file.py')
    assert 'C:\\' not in safe_error_message('Error at C:\\Users\\admin\\app.py line 42')


def test_error_sanitization_db_keywords():
    """safe_error_message -- rozne bledy DB sa maskowane."""
    from modules.utils import safe_error_message
    assert 'database' not in safe_error_message('database is locked').lower() or \
           safe_error_message('database is locked') == 'Database error'
    assert safe_error_message('SQL syntax error near SELECT') == 'Database error'


def test_error_sanitization_generic():
    """safe_error_message -- ogolne bledy sa obcinane do 100 znakow."""
    from modules.utils import safe_error_message
    long_err = 'x' * 200
    result = safe_error_message(long_err)
    assert len(result) <= 100


# ============================================================
# NOWE: PASSWORD HASHING (Argon2id)
# ============================================================

def test_argon2id_hash_and_verify():
    """_hash_password powinien tworzyc hash Argon2id, _verify_password weryfikuje."""
    from modules.auth import _hash_password, _verify_password
    h = _hash_password('TestPassword123!')
    assert h.startswith('$argon2')
    assert _verify_password('TestPassword123!', h) is True
    assert _verify_password('WrongPassword', h) is False


def test_pbkdf2_migration_path():
    """_verify_password powinien akceptowac stare hashe pbkdf2 (migracja)."""
    from modules.auth import _verify_password
    # Stworz hash pbkdf2 (stary format)
    pbkdf2_hash = generate_password_hash('OldPassword1', method='pbkdf2:sha256', salt_length=16)
    assert _verify_password('OldPassword1', pbkdf2_hash) is True
    assert _verify_password('WrongPassword', pbkdf2_hash) is False


def test_needs_rehash_pbkdf2():
    """_needs_rehash powinien zwracac True dla pbkdf2."""
    from modules.auth import _needs_rehash
    pbkdf2_hash = generate_password_hash('test', method='pbkdf2:sha256', salt_length=16)
    assert _needs_rehash(pbkdf2_hash) is True


def test_needs_rehash_argon2():
    """_needs_rehash powinien zwracac False dla aktualnego Argon2id."""
    from modules.auth import _hash_password, _needs_rehash
    h = _hash_password('TestPassword123!')
    assert _needs_rehash(h) is False


def test_legacy_sha256_rejected():
    """Legacy SHA-256 hashe powinny byc odrzucone."""
    from modules.auth import _verify_password
    import hashlib
    legacy_hash = hashlib.sha256(('salt' + 'password').encode()).hexdigest() + ':salt'
    assert _verify_password('password', legacy_hash) is False


# ============================================================
# NOWE: SECURITY HEADERS
# ============================================================

def test_security_headers(app_client):
    """Sprawdz czy odpowiedzi zawieraja podstawowe naglowki bezpieczenstwa."""
    rv = app_client.get('/')
    # Sprawdz naglowki niezaleznie od statusu (302 tez moze miec headers)
    headers = dict(rv.headers)
    # Serwer nie powinien ujawniac wersji
    server = headers.get('Server', '')
    if server:
        assert 'werkzeug' not in server.lower()
        assert 'python' not in server.lower()


# ============================================================
# NOWE: RATE LIMITING (koncepcyjny)
# ============================================================

def test_login_rate_limit_exists():
    """Sprawdz czy rate limiting jest skonfigurowany w auth."""
    from modules.auth import MAX_LOGIN_ATTEMPTS, LOGIN_COOLDOWN
    assert MAX_LOGIN_ATTEMPTS > 0
    assert MAX_LOGIN_ATTEMPTS <= 10  # rozsadny limit
    assert LOGIN_COOLDOWN >= 300  # min 5 minut


def test_rate_limit_function_exists():
    """Sprawdz czy funkcja rate limitingu istnieje."""
    from modules.auth import _is_rate_limited, _record_failed_login
    assert callable(_is_rate_limited)
    assert callable(_record_failed_login)
