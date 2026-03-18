"""
System licencji AKCES HUB
Generowanie i weryfikacja kluczy licencyjnych.
Klucz: AKCES-XXXX-XXXX-XXXX-XXXX (HMAC-based)
"""

import hmac
import hashlib
import time
import json
import os


# Secret do podpisywania licencji — TYLKO w Twoim generatorze
# Klient NIE ma tego klucza, więc nie może wygenerować licencji sam
LICENSE_SECRET = os.environ.get('AKCES_LICENSE_SECRET', 'AkcesHub2026!SecretKeyForLicenseGeneration')


def _encode_base36(num):
    """Konwertuj liczbę na base36 (0-9A-Z)"""
    chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    result = ''
    while num > 0:
        result = chars[num % 36] + result
        num //= 36
    return result or '0'


def generate_license_key(client_name, plan='pro', months=12):
    """
    Generuj klucz licencyjny dla klienta.

    Args:
        client_name: Nazwa klienta/firmy
        plan: starter, pro, business
        months: Na ile miesięcy (0 = bezterminowo)

    Returns:
        dict z kluczem i metadanymi
    """
    created = int(time.time())

    if months > 0:
        expires = created + (months * 30 * 24 * 3600)
    else:
        expires = 0  # Bezterminowo

    # Plan code: S=starter, P=pro, B=business
    plan_code = {'starter': 'S', 'pro': 'P', 'business': 'B'}.get(plan, 'P')

    # Payload do podpisania
    payload = f"{client_name}|{plan_code}|{created}|{expires}"

    # HMAC signature
    sig = hmac.new(
        LICENSE_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

    # Formatuj klucz: AKCES-XXXX-XXXX-XXXX-XXXX
    # Używamy fragmentów HMAC + plan + expiry info
    sig_short = sig[:16].upper()
    key = f"AKCES-{plan_code}{sig_short[:3]}-{sig_short[3:7]}-{sig_short[7:11]}-{sig_short[11:15]}"

    # Dane licencji (zapisywane lokalnie u klienta po aktywacji)
    license_data = {
        'key': key,
        'client': client_name,
        'plan': plan,
        'created': created,
        'expires': expires,
        'signature': sig[:32]
    }

    return license_data


def verify_license(license_data):
    """
    Weryfikuj licencję offline (bez serwera).

    Args:
        license_data: dict z danymi licencji

    Returns:
        (is_valid, message)
    """
    if not license_data or not isinstance(license_data, dict):
        return False, 'Brak licencji'

    key = license_data.get('key', '')
    client = license_data.get('client', '')
    plan = license_data.get('plan', '')
    created = license_data.get('created', 0)
    expires = license_data.get('expires', 0)
    sig_stored = license_data.get('signature', '')

    if not all([key, client, plan, created, sig_stored]):
        return False, 'Niekompletne dane licencji'

    # Sprawdź podpis
    plan_code = {'starter': 'S', 'pro': 'P', 'business': 'B'}.get(plan, 'P')
    payload = f"{client}|{plan_code}|{created}|{expires}"

    expected_sig = hmac.new(
        LICENSE_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()[:32]

    if not hmac.compare_digest(sig_stored, expected_sig):
        return False, 'Nieprawidlowy klucz licencyjny'

    # Sprawdź wygaśnięcie
    if expires > 0 and time.time() > expires:
        from datetime import datetime
        exp_date = datetime.fromtimestamp(expires).strftime('%d.%m.%Y')
        return False, f'Licencja wygasla {exp_date}'

    return True, 'OK'


def get_license_info():
    """Pobierz info o licencji z bazy config."""
    try:
        from .database import get_config
        lic_json = get_config('license_data', '')
        if not lic_json:
            return None
        return json.loads(lic_json)
    except:
        return None


def save_license(license_data):
    """Zapisz licencję do bazy config."""
    from .database import set_config
    set_config('license_data', json.dumps(license_data))


def activate_license(key, client_name, plan, created, expires, signature):
    """Aktywuj licencję z podanych danych."""
    license_data = {
        'key': key,
        'client': client_name,
        'plan': plan,
        'created': created,
        'expires': expires,
        'signature': signature
    }

    is_valid, msg = verify_license(license_data)
    if not is_valid:
        return False, msg

    save_license(license_data)
    return True, 'Licencja aktywowana!'


def check_license():
    """
    Sprawdź czy licencja jest aktywna.
    Returns: (is_valid, plan, message)
    """
    lic = get_license_info()
    if not lic:
        return False, None, 'Brak licencji — aktywuj w Ustawieniach'

    is_valid, msg = verify_license(lic)
    if not is_valid:
        return False, None, msg

    return True, lic.get('plan', 'pro'), msg


def get_license_display():
    """Pobierz dane do wyświetlenia na dashboardzie."""
    lic = get_license_info()
    if not lic:
        return {
            'active': False,
            'key': '',
            'client': '',
            'plan': '',
            'expires': '',
            'message': 'Brak licencji'
        }

    is_valid, msg = verify_license(lic)

    expires_str = ''
    if lic.get('expires', 0) > 0:
        from datetime import datetime
        expires_str = datetime.fromtimestamp(lic['expires']).strftime('%d.%m.%Y')
    else:
        expires_str = 'Bezterminowo'

    return {
        'active': is_valid,
        'key': lic.get('key', ''),
        'client': lic.get('client', ''),
        'plan': lic.get('plan', ''),
        'expires': expires_str,
        'message': msg
    }
