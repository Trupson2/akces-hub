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
    """Wczytaj secret z env → pliku → wygeneruj nowy (NIE hardcodowany).

    FIX 2026-05-28: backup starego secret-a przed wygenerowaniem nowego
    + wyrazne ostrzezenie. Klient ktory niechcacy usunie .license_secret
    przez update / skopiowanie plikow widzi WAZNY KOMUNIKAT zamiast
    cichego rotation.
    """
    s = os.environ.get('AKCES_LICENSE_SECRET', '').strip()
    if s:
        return s
    _path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.license_secret')
    if os.path.exists(_path):
        with open(_path, 'r') as f:
            s = f.read().strip()
        if s:
            return s

    # Sprawdz czy w bazie jest JUZ aktywowana licencja - jezeli tak,
    # NIE generuj nowego secret-a tylko poinformuj uzytkownika.
    try:
        from modules.database import get_config
        existing_license = get_config('license_data', '')
        if existing_license:
            import sys
            print("=" * 70, file=sys.stderr)
            print("[LICENSE CRITICAL] BRAK .license_secret ALE BAZA MA AKTYWNA LICENCJE!",
                  file=sys.stderr)
            print("  Walidacja nie zadziala. Mozliwe przyczyny:", file=sys.stderr)
            print("  1) Update / skopiowanie plikow bez .license_secret", file=sys.stderr)
            print("  2) Plik zostal przypadkiem usuniety", file=sys.stderr)
            print("  3) Klient ma backup .license_secret w starym folderze", file=sys.stderr)
            print("", file=sys.stderr)
            print("  ROZWIAZANIE: Skopiuj .license_secret z poprzedniej kopii.", file=sys.stderr)
            print(f"  Sciezka: {_path}", file=sys.stderr)
            print("  Jak nie masz - skontaktuj sie z dostawca o nowy klucz licencji.", file=sys.stderr)
            print("=" * 70, file=sys.stderr)
            # Mimo to wygeneruj awaryjny secret (aplikacja musi dzialac),
            # ale zaznacz ze licencja nie zadziala.
    except Exception:
        pass

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

    # Sprawdź HWID binding (backward compatible — jeśli brak hwid, pomijamy)
    stored_hwid = license_data.get('hwid', '')
    if stored_hwid:
        try:
            current_hwid = get_hwid()
            if current_hwid != 'UNKNOWN' and stored_hwid != current_hwid:
                return False, 'Licencja przypisana do innego urzadzenia'
        except Exception:
            pass  # Błąd HWID nie blokuje — bezpieczeństwo

    return True, 'OK'


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

    # Bind HWID przy aktywacji
    try:
        hwid = get_hwid()
        if hwid != 'UNKNOWN':
            license_data['hwid'] = hwid
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

    FIX 2026-05-28: TRYB SELF-HOSTED. Klient hostuje aplikacje sam na
    swoim Pi/Windows, vendor nie ma dostepu. Walidacja HMAC przy kazdym
    pull-u/restarcie tylko utrudniala bez realnej korzysci - klient z
    dostepem do .license_secret moze i tak generowac wlasne klucze.
    Wylacz przez set_config('license_check_disabled', '1') (default = '1'
    od v1.0.67 = WYLACZONE dla self-hosted).
    Pozostawiamy oryginalna logike validacji w verify_license() na
    wypadek gdyby ktos w przyszlosci chcial wlaczyc serwer licencji,
    ale check_license() krotko zwraca OK z planu zapisanego w configu.
    """
    try:
        from .database import get_config
        # Default '1' = wylaczone (self-hosted, klient hostuje sam)
        # Ustaw '0' zeby PRZYWROCIC stara walidacje (np vendor mode)
        if get_config('license_check_disabled', '1') == '1':
            lic = get_license_info() or {}
            return True, lic.get('plan', 'max'), 'self-hosted'
    except Exception:
        pass

    lic = get_license_info()
    if not lic:
        return False, None, 'Brak licencji — aktywuj w Ustawieniach'

    is_valid, msg = verify_license(lic)
    if not is_valid:
        return False, None, msg

    return True, lic.get('plan', 'pro'), msg


def get_license_display():
    """Pobierz dane do wyświetlenia na dashboardzie."""
    # FIX 2026-05-28: w trybie self-hosted zawsze pokazuj 'active'.
    try:
        from .database import get_config
        if get_config('license_check_disabled', '1') == '1':
            lic = get_license_info() or {}
            return {
                'active': True,
                'key': lic.get('key', 'SELF-HOSTED'),
                'client': lic.get('client', 'Self-hosted'),
                'plan': lic.get('plan', 'max'),
                'expires': 'Bezterminowo (self-hosted)',
                'hwid': lic.get('hwid', ''),
                'message': 'Tryb self-hosted - pelny dostep'
            }
    except Exception:
        pass

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

    FIX 2026-05-28: w trybie self-hosted (default) NIGDY nie zwraca True.
    Klient ma pelny dostep do aplikacji bez czasowych limitow.
    """
    try:
        from .database import get_config
        if get_config('license_check_disabled', '1') == '1':
            return False
    except Exception:
        pass

    days = get_days_remaining()
    if days is None:
        return False  # Bezterminowa lub brak licencji (obsługiwane osobno)
    return days < 0


_last_time_check_write = 0.0  # throttle zapisu last_successful_login_time (epoch s)


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

        # FIX 2026-05-30: NIE pisz do DB na KAZDYM zadaniu. Wczesniej ten
        # set_config lecial per-request (middleware check_license_middleware
        # -> check_time_manipulation), wiec gdy cokolwiek w tle trzymalo
        # write-lock SQLite, zapis czekal busy_timeout=30s i ZAMRAZAL KAZDA
        # chroniona strone na 30s (/auth/login byl szybki bo pomija middleware).
        # Throttle: zapis max raz na 10 min — dla detekcji cofniecia zegara
        # (grace 2h) w zupelnosci wystarcza. Odczyt powyzej jest tani (WAL
        # nie blokuje czytania), wiec render strony juz nie dotyka write-locka.
        import time as _t
        global _last_time_check_write
        if _t.time() - _last_time_check_write > 600:
            try:
                set_config('last_successful_login_time', now.isoformat())
                _last_time_check_write = _t.time()
            except Exception:
                pass
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
