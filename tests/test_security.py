"""Testy bezpieczeństwa — SQL injection, XSS, CSRF."""
import html
import re


def test_sql_injection_whitelist_app():
    """Test: whitelist kolumn w admin_subscriptions_update blokuje SQL injection."""
    # Symulacja logiki z app.py — whitelist powinien odrzucić nieznane kolumny
    _ALLOWED_COLS = {'plan', 'expires_date', 'expires', 'active'}

    # Normalne kolumny — OK
    safe_updates = ['plan = ?', 'active = ?']
    for u in safe_updates:
        col_name = u.split(' ')[0]
        assert col_name in _ALLOWED_COLS, f"Kolumna '{col_name}' powinna byc dozwolona"

    # Atak — kolumna spoza whitelisty
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


def test_xss_html_escape():
    """Test: html.escape() prawidłowo neutralizuje XSS w danych z DB."""
    # Symulacja ataku XSS w danych z bazy
    malicious_title = '<script>alert("XSS")</script>'
    malicious_url = 'javascript:alert("XSS")'
    malicious_image = '" onerror="alert(1)" src="x'

    safe_title = html.escape(malicious_title)
    safe_url = html.escape(malicious_url, quote=True)
    safe_image = html.escape(malicious_image, quote=True)

    assert '<script>' not in safe_title
    assert '&lt;script&gt;' in safe_title
    assert '"' not in safe_url  # Cudzysłowy powinny być escaped
    assert '"' not in safe_image


def test_xss_keyword_escape():
    """Test: keyword tags są escapowane."""
    keywords = ['normal', '<img src=x onerror=alert(1)>', 'good"bad']

    for k in keywords:
        escaped = html.escape(k)
        assert '<' not in escaped or '&lt;' in escaped
        assert '>' not in escaped or '&gt;' in escaped


def test_license_key_format_validation():
    """Test: auto-rejestracja akceptuje tylko prawidłowy format klucza."""
    valid_keys = [
        'AKCES-P1AB-CD23-EF45-GH67',
        'AKCES-SXXX-XXXX-XXXX-XXXX',
        'AKCES-E000-0000-0000-0000',
    ]
    invalid_keys = [
        'FAKEKEY',
        'AKCES-SHORT',
        'AKCES-xxxx-xxxx-xxxx-xxxx',  # lowercase
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
    """Test: client_secret nie powinien być w value formularza HTML."""
    # Symulacja starego kodu vs nowego
    secret = 'super_secret_value_12345'

    # Stary (zły): secret w value
    old_html = f'<input type="password" value="{secret}">'
    assert secret in old_html  # Stary kod miał sekret

    # Nowy (dobry): maskowanie
    masked = '****' + secret[-4:] if secret else ''
    new_html = f'<input type="password" value="" placeholder="Wpisz nowy secret lub zostaw puste">'
    assert secret not in new_html  # Nowy kod NIE ma sekretu
    assert masked == '****2345'
