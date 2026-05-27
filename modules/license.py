"""
System licencji AKCES HUB
Generowanie i weryfikacja kluczy licencyjnych.
Klucz: AKCES-XXXX-XXXX-XXXX-XXXX (HMAC-based)
HWID binding + heartbeat do serwera licencji.
"""

import hmac
import hashlib
import time
import json
import os
import platform
import subprocess
import threading


# Secret do podpisywania licencji — TYLKO w Twoim generatorze
# Klient NIE ma tego klucza, więc nie może wygenerować licencji sam
def _load_license_secret():
    """Wczytaj secret z env → pliku → wygeneruj nowy (NIE hardcodowany)."""
    s = os.environ.get('AKCES_LICENSE_SECRET', '').strip()
    if s:
        return s
    _path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.license_secret')
    if os.path.exists(_path):
        with open(_path, 'r') as f:
            s = f.read().strip()
        if s:
            return s
    import secrets as _sec
    s = _sec.token_hex(32)
    try:
        with open(_path, 'w') as f:
            f.write(s)
        os.chmod(_path, 0o600)
    except Exception:
        pass
    print(f"[LICENSE] Wygenerowano nowy LICENSE_SECRET → {_path}. Przenieś do AKCES_LICENSE_SECRET w .env!")
    return s

LICENSE_SECRET = _load_license_secret()

# Heartbeat config
# SECURITY: URL konfigurowalny — bez ngrok hardcoded. Default = pusty
# → heartbeat WYŁĄCZONY → license validation 100% offline (HMAC + HWID).
# Aby włączyć remote verify: set_config('license_server_url', 'https://api.akceshub.com')
# License endpoint POST musi obsłużyć /api/license/verify z { license_key, hwid, version }.
try:
    from modules.database import get_config as _gc
    _LIC_BASE = (_gc('license_server_url', '') or '').strip().rstrip('/')
    HEARTBEAT_URL = (_LIC_BASE + '/api/license/verify') if _LIC_BASE else ''
except Exception:
    HEARTBEAT_URL = ''
HEARTBEAT_INTERVAL = 86400  # 24h
HEARTBEAT_GRACE_DAYS = 7  # Dni offline bez blokady (gdy URL skonfigurowany)


def _encode_base36(num):
    """Konwertuj liczbę na base36 (0-9A-Z)"""
    chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    result = ''
    while num > 0:
        result = chars[num % 36] + result
        num //= 36
    return result or '0'


def get_hwid():
    """
    Pobierz unikalny identyfikator sprzętowy (HWID).
    Windows: wmic csproduct get uuid
    Linux/RPi: /sys/class/dmi/id/product_uuid lub MAC address
    Returns: sha256 hash pierwszych 16 znaków (short HWID)
    """
    try:
        raw_uuid = None

        if platform.system() == 'Windows':
            try:
                result = subprocess.run(
                    ['wmic', 'csproduct', 'get', 'uuid'],
                    capture_output=True, text=True, timeout=10,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                )
                for line in result.stdout.strip().split('\n'):
                    line = line.strip()
                    if line and line.upper() != 'UUID':
                        raw_uuid = line
                        break
            except Exception:
                pass
        else:
            # Linux / Raspberry Pi
            try:
                with open('/sys/class/dmi/id/product_uuid', 'r') as f:
                    raw_uuid = f.read().strip()
            except Exception:
                pass

        # Fallback: MAC address
        if not raw_uuid:
            try:
                import uuid as _uuid
                mac = _uuid.getnode()
                raw_uuid = format(mac, '012x')
            except Exception:
                pass

        if not raw_uuid:
            return 'UNKNOWN'

        hwid_hash = hashlib.sha256(raw_uuid.encode()).hexdigest()[:16]
        return hwid_hash

    except Exception:
        return 'UNKNOWN'


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

    # Plan code: T=trial/free, P=pro, M=max, S=starter, B=business, E=enterprise
    # Mapa musi byc identyczna z verify_license() ponizej!
    plan_code = {'trial': 'T', 'free': 'T', 'pro': 'P', 'max': 'M',
                 'starter': 'S', 'business': 'B', 'enterprise': 'E'}.get(plan, 'P')

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
    Sprawdza podpis HMAC, wygaśnięcie i HWID binding.

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
    # Mapa musi byc identyczna z generate_license_key() powyzej!
    plan_code = {'trial': 'T', 'free': 'T', 'pro': 'P', 'max': 'M',
                 'starter': 'S', 'business': 'B', 'enterprise': 'E'}.get(plan, 'P')
    payload = f"{client}|{plan_code}|{created}|{expires}"

    expected_sig = hmac.new(
        LICENSE_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()[:32]

    if not hmac.compare_digest(sig_stored, expected_sig):
        return False, 'Nieprawidlowy klucz licencyjny'

    # Sprawdź wygaśnięcie (unix timestamp)
    if expires > 0 and time.time() > expires:
        from datetime import datetime
        exp_date = datetime.fromtimestamp(expires).strftime('%d.%m.%Y')
        return False, f'Licencja wygasla {exp_date}'

    # Sprawdź wygaśnięcie (expiry_date YYYY-MM-DD — nowy format)
    expiry_date_str = license_data.get('expiry_date', '')
    if expiry_date_str:
        try:
            from datetime import datetime, date
            exp_d = datetime.strptime(expiry_date_str, '%Y-%m-%d').date()
            if date.today() > exp_d:
                return False, f'Licencja wygasla {exp_d.strftime("%d.%m.%Y")}'
        except (ValueError, TypeError):
            pass  # Nieprawidłowy format — ignoruj

    # Sprawdź HWID binding (multi-HWID: lista komputerow w 'hwids' + legacy 'hwid')
    # Plan -> max seatow: trial/pro=1, max=3, enterprise=5
    stored_hwids = license_data.get('hwids', [])
    if not stored_hwids:
        # Backward compat: stara licencja ma 'hwid' (string)
        old_hwid = license_data.get('hwid', '')
        if old_hwid:
            stored_hwids = [old_hwid]

    if stored_hwids:
        try:
            current_hwid = get_hwid()
            if current_hwid != 'UNKNOWN' and current_hwid not in stored_hwids:
                return False, 'Licencja nie jest aktywowana na tym komputerze. Wejdz /license -> Dodaj ten komputer.'
        except Exception:
            pass  # Błąd HWID nie blokuje — bezpieczeństwo

    return True, 'OK'


# Plan -> maksymalna liczba HWID jednoczesnie (seaty)
PLAN_MAX_SEATS = {
    'trial': 1, 'starter': 1,
    'pro': 1,
    'max': 3, 'business': 3,
    'enterprise': 5,
}


def add_hwid_to_license(reason=''):
    """Dodaj AKTUALNY HWID do listy aktywnych komputerow licencji.

    - Sprawdza limit per plan (max 3 dla MAX, 5 dla ENTERPRISE)
    - Jesli HWID juz na liscie -> no-op (success)
    - Wysyla Telegram notify do dostawcy (vendor_bot)

    Returns: (success: bool, message: str)
    """
    lic = get_license_info()
    if not lic:
        return False, 'Brak licencji'

    current = get_hwid()
    if current == 'UNKNOWN':
        return False, 'Nie moge odczytac HWID tego komputera'

    # Pobierz aktualna liste (backward compat z 'hwid' single string)
    hwids = list(lic.get('hwids', []))
    if not hwids and lic.get('hwid'):
        hwids = [lic['hwid']]

    if current in hwids:
        return True, 'Ten komputer juz jest aktywowany'

    # Limit per plan
    plan = (lic.get('plan', 'pro') or 'pro').lower()
    max_seats = PLAN_MAX_SEATS.get(plan, 1)
    if len(hwids) >= max_seats:
        return False, (
            f'Limit komputerow ({max_seats}) osiagniety dla planu {plan.upper()}. '
            f'Usun jeden z listy lub skontaktuj sie z dostawca.'
        )

    # Dodaj
    hwids.append(current)
    lic['hwids'] = hwids
    lic.pop('hwid', None)  # usun legacy field zeby nie myliło
    save_license(lic)

    # Notify dostawcy
    try:
        from .telegram_bot import send_telegram_support
        from .database import get_config
        brand = get_config('brand_name', 'AKCES HUB')
        client = lic.get('client', '?')
        reason_safe = (reason or '').replace('<', '&lt;').replace('>', '&gt;')[:300]
        msg = (
            f"➕ <b>DODANO KOMPUTER do licencji</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 Klient: <b>{brand}</b> / {client}\n"
            f"📦 Plan: {plan.upper()} ({len(hwids)}/{max_seats})\n"
            f"🆕 Nowy HWID: <code>{current[:16]}</code>\n"
            f"💬 Powod: {reason_safe or '(brak)'}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        send_telegram_support(msg, parse_mode='HTML')
    except Exception as e:
        print(f'[license] add_hwid notify fail: {e}')

    return True, f'Komputer dodany ({len(hwids)}/{max_seats}).'


def remove_hwid_from_license(target_hwid):
    """Usun HWID z listy aktywnych komputerow."""
    lic = get_license_info()
    if not lic:
        return False, 'Brak licencji'

    hwids = list(lic.get('hwids', []))
    if not hwids and lic.get('hwid'):
        hwids = [lic['hwid']]

    if target_hwid not in hwids:
        return False, 'Ten HWID nie jest na liscie'

    if len(hwids) <= 1:
        return False, 'Nie mozesz usunac OSTATNIEGO komputera (licencja przestałaby działać)'

    hwids.remove(target_hwid)
    lic['hwids'] = hwids
    save_license(lic)

    # Notify dostawcy
    try:
        from .telegram_bot import send_telegram_support
        from .database import get_config
        brand = get_config('brand_name', 'AKCES HUB')
        client = lic.get('client', '?')
        msg = (
            f"➖ <b>USUNIETO KOMPUTER z licencji</b>\n"
            f"📍 {brand} / {client}\n"
            f"🗑️ HWID: <code>{target_hwid[:16]}</code>\n"
            f"📊 Pozostalo: {len(hwids)} komputerow"
        )
        send_telegram_support(msg, parse_mode='HTML')
    except Exception:
        pass

    return True, f'Komputer usunięty. Pozostalo: {len(hwids)}.'


_license_cache = {'data': None, 'ts': 0}

def get_license_info():
    """Pobierz info o licencji z bazy config (cache 60s)."""
    import time
    now = time.time()
    if _license_cache['data'] is not None and (now - _license_cache['ts']) < 60:
        return _license_cache['data']
    try:
        from .database import get_config
        lic_json = get_config('license_data', '')
        if not lic_json:
            _license_cache['data'] = None
            _license_cache['ts'] = now
            return None
        result = json.loads(lic_json)
        _license_cache['data'] = result
        _license_cache['ts'] = now
        return result
    except:
        return None


def save_license(license_data):
    """Zapisz licencję do bazy config."""
    from .database import set_config
    set_config('license_data', json.dumps(license_data))
    # Invalidate cache
    _license_cache['data'] = None
    _license_cache['ts'] = 0


# Maksymalna liczba reset HWID per licencja (bez kontaktu z dostawca)
HWID_TRANSFER_LIMIT = 3


def reset_hwid(reason=''):
    """Self-service reset HWID — klient zmienil komputer.

    Workflow:
    1. Sprawdz limit transfer (max HWID_TRANSFER_LIMIT bez kontaktu z dostawca)
    2. Zapisz stary HWID jako previous_hwid (audit)
    3. Wpisz aktualny HWID (z nowego komputera)
    4. Notify do dostawcy przez Telegram (vendor_bot)
    5. Zapisz transfer_count + reason

    Returns: (success: bool, message: str)
    """
    lic = get_license_info()
    if not lic:
        return False, 'Brak licencji do zresetowania'

    transfer_count = int(lic.get('hwid_transfer_count', 0))
    if transfer_count >= HWID_TRANSFER_LIMIT:
        return False, (
            f'Osiagnieto limit reset HWID ({HWID_TRANSFER_LIMIT}). '
            f'Skontaktuj sie z dostawca: support@example.com'
        )

    old_hwid = lic.get('hwid', '')
    new_hwid = get_hwid()
    if new_hwid == 'UNKNOWN':
        return False, 'Nie moge odczytac HWID tego komputera'

    if old_hwid == new_hwid:
        return False, 'HWID nie zmienil sie — to ten sam komputer'

    # Update licencji
    from datetime import datetime
    lic['previous_hwid'] = old_hwid
    lic['hwid'] = new_hwid
    lic['hwid_transfer_count'] = transfer_count + 1
    lic['hwid_reset_at'] = datetime.now().isoformat()
    lic['hwid_reset_reason'] = (reason or '')[:300]

    save_license(lic)

    # Notify dostawcy (Twoj Telegram) - WAZNE: audit, wiesz ze klient zmienil komp
    try:
        from .telegram_bot import send_telegram_support
        from .database import get_config
        brand = get_config('brand_name', 'AKCES HUB')
        client = lic.get('client', '?')
        # Escape HTML w reason
        reason_safe = (reason or '').replace('<', '&lt;').replace('>', '&gt;')[:500]
        msg = (
            f"🔄 <b>HWID RESET</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 Klient: <b>{brand}</b> / {client}\n"
            f"📦 Licencja: <code>{lic.get('key', '?')[:30]}</code>\n"
            f"🔁 Transfer #{transfer_count + 1}/{HWID_TRANSFER_LIMIT}\n\n"
            f"➡️ Stary HWID: <code>{old_hwid[:16]}</code>\n"
            f"➡️ Nowy HWID: <code>{new_hwid[:16]}</code>\n\n"
            f"💬 Powod: {reason_safe or '(brak)'}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        send_telegram_support(msg, parse_mode='HTML')
    except Exception as e:
        print(f'[license] HWID reset notify fail: {e}')

    return True, f'HWID zresetowany ({transfer_count + 1}/{HWID_TRANSFER_LIMIT}). Licencja aktywna na nowym komputerze.'


def activate_license(key, client_name, plan, created, expires, signature):
    """Aktywuj licencję z podanych danych. Binduje HWID przy aktywacji."""
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

    # Bind HWID przy aktywacji (lista - wspiera multi-seat per plan)
    try:
        hwid = get_hwid()
        if hwid != 'UNKNOWN':
            license_data['hwids'] = [hwid]
    except Exception:
        pass  # Błąd HWID nie blokuje aktywacji

    # Dodaj activated_at i expiry_date (YYYY-MM-DD) dla nowego formatu
    from datetime import datetime
    license_data['activated_at'] = datetime.now().isoformat()
    expires_ts = license_data.get('expires', 0)
    if expires_ts and expires_ts > 0:
        try:
            license_data['expiry_date'] = datetime.fromtimestamp(expires_ts).strftime('%Y-%m-%d')
        except (ValueError, TypeError, OSError):
            pass

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
            'hwid': '',
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
        'hwid': lic.get('hwid', ''),
        'message': msg
    }


def get_days_remaining():
    """
    Oblicz ile dni pozostało do wygaśnięcia licencji.
    Returns: int (dni) lub None (brak licencji / bezterminowa)
    """
    lic = get_license_info()
    if not lic:
        return None

    from datetime import datetime, date

    # Sprawdź expiry_date (YYYY-MM-DD) — nowy format
    expiry_date_str = lic.get('expiry_date', '')
    if expiry_date_str:
        try:
            exp_d = datetime.strptime(expiry_date_str, '%Y-%m-%d').date()
            return (exp_d - date.today()).days
        except (ValueError, TypeError):
            pass

    # Sprawdź expires (unix timestamp) — stary format
    expires = lic.get('expires', 0)
    if expires and expires > 0:
        try:
            exp_dt = datetime.fromtimestamp(expires)
            days = (exp_dt - datetime.now()).days
            return days
        except (ValueError, TypeError, OSError):
            pass

    return None  # Bezterminowa


def is_subscription_expired():
    """
    Sprawdź czy subskrypcja wygasła.
    Returns: bool (True = wygasła, False = aktywna lub bezterminowa)
    """
    days = get_days_remaining()
    if days is None:
        return False  # Bezterminowa lub brak licencji (obsługiwane osobno)
    return days < 0


def check_time_manipulation():
    """
    Sprawdź czy czas systemowy nie został cofnięty (ochrona przed manipulacją).
    Porównuje aktualny czas z ostatnim zapisanym czasem logowania.
    Grace: 2 godziny wstecz (zmiany strefy czasowej, synchronizacja NTP).

    Returns: (ok, message)
        ok=True — czas OK
        ok=False — wykryto manipulację
    """
    try:
        from .database import get_config, set_config
        from datetime import datetime, timedelta

        last_login_str = get_config('last_successful_login_time', '')
        now = datetime.now()

        if last_login_str:
            try:
                last_login = datetime.fromisoformat(last_login_str)
                # Jeśli aktualny czas jest wcześniejszy niż ostatni login minus grace
                grace = timedelta(hours=2)
                if now < (last_login - grace):
                    return False, 'Wykryto manipulacje czasem systemowym. Ustaw prawidlowa date i godzine.'
            except (ValueError, TypeError):
                pass  # Nieprawidłowy format — ignoruj, zapisz nowy

        # Zapisz aktualny czas jako ostatni udany login
        set_config('last_successful_login_time', now.isoformat())
        return True, 'OK'

    except Exception:
        return True, 'OK'  # Błąd sprawdzania nie blokuje aplikacji


# ============================================================
# HEARTBEAT — weryfikacja licencji z serwerem
# ============================================================

def license_heartbeat():
    """
    Wyślij heartbeat do serwera licencji.
    Sprawdza ważność klucza online. Przy braku połączenia
    pozwala na pracę offline do HEARTBEAT_GRACE_DAYS dni.

    UWAGA: gdy HEARTBEAT_URL pusty (default) → heartbeat skipped,
    licencja walidowana 100% offline przez HMAC + HWID.
    """
    # Heartbeat wyłączony gdy URL nie skonfigurowany (self-hosted default)
    if not HEARTBEAT_URL:
        return

    try:
        import urllib.request
        import urllib.error
        from .database import get_config, set_config

        lic = get_license_info()
        if not lic:
            return  # Brak licencji — nic do sprawdzenia

        key = lic.get('key', '')
        if not key:
            return

        hwid = get_hwid()

        # Przygotuj dane heartbeat (client/plan dla auto-rejestracji na serwerze)
        payload = json.dumps({
            'key': key,
            'hwid': hwid,
            'client': lic.get('client', ''),
            'plan': lic.get('plan', 'pro'),
            'timestamp': int(time.time()),
            'version': _get_app_version()
        }).encode('utf-8')

        req = urllib.request.Request(
            HEARTBEAT_URL,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            if data.get('valid') is False:
                # Serwer odrzucił licencję — loguj ale NIE blokuj
                # (brak dedykowanego serwera licencji = self-hosted)
                return

            # Sukces — aktualizuj last_heartbeat
            set_config('last_heartbeat', str(int(time.time())))
            set_config('license_blocked', '0')

        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            # Serwer nieosiągalny — NIE blokuj (self-hosted, brak serwera)
            # Zapisz timestamp jeśli pierwszy kontakt się udał wcześniej
            pass

    except Exception:
        pass  # Heartbeat nigdy nie powinien crashować app


def _get_app_version():
    """Pobierz wersję aplikacji z pliku VERSION."""
    try:
        vf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'VERSION')
        with open(vf, 'r') as f:
            return f.read().strip().split('\n')[0]
    except Exception:
        return 'unknown'


def start_heartbeat_thread():
    """
    Uruchom wątek heartbeat w tle.
    Wysyła heartbeat co 24h (HEARTBEAT_INTERVAL).
    """
    def _heartbeat_loop():
        # Pierwsze sprawdzenie po 60s od startu (daj czas na init)
        time.sleep(60)
        while True:
            try:
                license_heartbeat()
            except Exception:
                pass
            time.sleep(HEARTBEAT_INTERVAL)

    t = threading.Thread(target=_heartbeat_loop, daemon=True, name='license-heartbeat')
    t.start()
    return t
