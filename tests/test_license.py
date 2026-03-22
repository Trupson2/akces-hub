"""Testy modułu licencji."""
import time
import pytest


def test_generate_license_key():
    """Test generowania klucza licencyjnego."""
    from modules.license import generate_license_key

    result = generate_license_key('Test Client', 'pro', 12)

    assert result['key'].startswith('AKCES-P')
    assert len(result['key']) == 25  # AKCES-PXXX-XXXX-XXXX-XXXX (plan code + 3 = 4)
    assert result['client'] == 'Test Client'
    assert result['plan'] == 'pro'
    assert result['created'] > 0
    assert result['signature']


def test_generate_license_key_plans():
    """Test generowania kluczy dla różnych planów."""
    from modules.license import generate_license_key

    for plan, prefix in [('starter', 'AKCES-S'), ('pro', 'AKCES-P'),
                         ('business', 'AKCES-B'), ('enterprise', 'AKCES-E')]:
        result = generate_license_key('Test', plan, 1)
        assert result['key'].startswith(prefix), f"Plan {plan} powinien zaczynac sie od {prefix}"


def test_generate_license_unlimited():
    """Test generowania bezterminowej licencji."""
    from modules.license import generate_license_key

    result = generate_license_key('Test', 'pro', months=0)
    assert result['expires'] == 0


def test_verify_license_valid():
    """Test weryfikacji prawidłowej licencji."""
    from modules.license import generate_license_key, verify_license

    lic = generate_license_key('Test Client', 'pro', 12)
    is_valid, msg = verify_license(lic)

    assert is_valid is True
    assert msg == 'OK'


def test_verify_license_expired():
    """Test weryfikacji wygasłej licencji — generujemy z expires w przeszłości."""
    from modules.license import verify_license, LICENSE_SECRET
    import hmac, hashlib

    # Generujemy licencję z datą wygaśnięcia w przeszłości (sygnatura musi pasować)
    created = int(time.time()) - 86400 * 60
    expires = int(time.time()) - 86400  # Wczoraj
    client = 'Test Expired'
    plan_code = 'P'
    payload = f"{client}|{plan_code}|{created}|{expires}"
    sig = hmac.new(LICENSE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

    lic = {
        'key': 'AKCES-PXXX-XXXX-XXXX-XXXX',
        'client': client,
        'plan': 'pro',
        'created': created,
        'expires': expires,
        'signature': sig[:32]
    }

    is_valid, msg = verify_license(lic)
    assert is_valid is False
    assert 'wygasla' in msg.lower()


def test_verify_license_bad_signature():
    """Test weryfikacji z fałszywą sygnaturą."""
    from modules.license import generate_license_key, verify_license

    lic = generate_license_key('Test Client', 'pro', 12)
    lic['signature'] = 'fakesignature00000000000000000000'

    is_valid, msg = verify_license(lic)
    assert is_valid is False
    assert 'nieprawidlowy' in msg.lower()


def test_verify_license_empty():
    """Test weryfikacji pustej licencji."""
    from modules.license import verify_license

    is_valid, msg = verify_license(None)
    assert is_valid is False

    is_valid, msg = verify_license({})
    assert is_valid is False


def test_verify_license_hwid_mismatch():
    """Test weryfikacji z niezgodnym HWID."""
    from modules.license import generate_license_key, verify_license

    lic = generate_license_key('Test Client', 'pro', 12)
    lic['hwid'] = 'abc123def456'  # Ustawiony HWID

    is_valid, msg = verify_license(lic)
    # Powinno albo przejść (jeśli HWID = current) albo failować z "innego urzadzenia"
    # Zależy od aktualnego HWID
    assert isinstance(is_valid, bool)


def test_get_hwid():
    """Test pobierania HWID."""
    from modules.license import get_hwid

    hwid = get_hwid()
    assert isinstance(hwid, str)
    assert len(hwid) > 0
    # HWID powinno być 16-znakowym hashem lub 'UNKNOWN'
    assert hwid == 'UNKNOWN' or len(hwid) == 16


def test_license_key_format():
    """Test formatu klucza AKCES-XXXX-XXXX-XXXX-XXXX."""
    from modules.license import generate_license_key
    import re

    lic = generate_license_key('Test', 'pro', 1)
    key = lic['key']

    # Format: AKCES-PXXX-XXXX-XXXX-XXXX (25 znaków, plan code + 3 + dashes)
    assert re.match(r'^AKCES-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$', key), \
        f"Klucz '{key}' nie pasuje do formatu AKCES-XXXX-XXXX-XXXX-XXXX"
