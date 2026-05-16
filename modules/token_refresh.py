"""
Auto-refresh tokena Allegro
Automatycznie odświeża token przed jego wygaśnięciem
"""

import base64
import sqlite3
import threading
import time
from datetime import datetime, timedelta
import requests
from modules.database import get_config, set_config  # ← NAPRAWIONY IMPORT!

# Token wygasa po 12 godzinach, odświeżamy po 11 godzinach
REFRESH_INTERVAL = 11 * 3600  # 11 godzin w sekundach
CHECK_INTERVAL = 60  # Sprawdzaj co minutę czy czas refresh

_refresh_daemon_running = False
_refresh_thread = None
# FIX 2026-05-09: licznik kolejnych porazek refresh
_refresh_failure_count = 0
# FIX 2026-05-10: throttle alertu (max 1/h) zeby nie spamowac przy ciaglym failu
_refresh_last_alert_at = 0
_ALERT_COOLDOWN_SECONDS = 3600  # 1h miedzy alertami

# FIX 2026-05-10 (D): alert Telegram dopiero po 3. porazce z rzedu
_ALERT_THRESHOLD = 3
# FIX 2026-05-10 (C): early-stop - po tylu porazkach z rzedu daemon
# przestaje probowac (zeby nie walic w martwy endpoint w nieskonczonosc
# ani nie ryzykowac blokady klienta przez Allegro). Wznawia gdy refresh_token
# w bazie sie zmieni (reczny /allegro/auth) albo przy restarcie procesu.
_MAX_RETRY_ATTEMPTS = 5
_refresh_gave_up = False
# Ostatni znany refresh_token - do detekcji recznego /allegro/auth (recovery
# z early-stop). Gdy token w bazie != ten -> ktos zrobil re-auth -> reset.
_last_known_refresh_token = ''
# Ostatnia kategoria bledu (NET / API / DB / ERR) - do healthchecku
_last_error_category = None
_last_error_detail = ''

def get_token_info():
    """Pobiera informacje o tokenie"""
    token = get_config('allegro_access_token', '')
    # FIX 2026-05-09 BUG #1: allegro_api.py zapisuje pod kluczem
    # `allegro_token_expires` (bez `_at`). Wczesniej szukalismy `_at`
    # ktory nigdy nie byl zapisany - przez to daemon nigdy nie startowal.
    expires_at_str = get_config('allegro_token_expires', '')

    if not token or not expires_at_str:
        return None
    
    try:
        expires_at = datetime.fromisoformat(expires_at_str)
        now = datetime.now()
        time_left = expires_at - now
        
        return {
            'token': token,
            'expires_at': expires_at,
            'expires_at_str': expires_at.strftime('%Y-%m-%d %H:%M:%S'),
            'time_left': time_left,
            'time_left_hours': time_left.total_seconds() / 3600,
            'is_expired': time_left.total_seconds() <= 0,
            'needs_refresh': time_left.total_seconds() < 3600  # Odśwież jeśli < 1h
        }
    except:
        return None

def _maybe_send_alert(msg):
    """Wyslij alert Telegram z throttle (max 1 alert na _ALERT_COOLDOWN_SECONDS).

    FIX 2026-05-10: aktualnie daemon co 10 min retry-uje po failu, wiec bez
    throttle dostawalismy alert ~6x/h. Throttle = max 1/h zeby user widzial
    problem, ale nie byl spamowany.
    """
    global _refresh_last_alert_at
    now = time.time()
    if now - _refresh_last_alert_at < _ALERT_COOLDOWN_SECONDS:
        return
    try:
        from modules.telegram_bot import send_telegram
        send_telegram(msg)
        _refresh_last_alert_at = now
    except Exception:
        pass


def _send_alert_now(msg):
    """Alert Telegram NATYCHMIAST, ignorujac throttle. Tylko dla zdarzen
    krytycznych (utrata tokena, early-stop) ktore wymagaja recznej reakcji."""
    try:
        from modules.telegram_bot import send_telegram
        send_telegram(msg)
    except Exception:
        pass


def _persist_tokens(access_token, refresh_token, expires_at, refresh_rotated):
    """FIX 2026-05-10 (A): zapis tokenow z retry.

    Allegro przy HTTP 200 JUZ ZUZYL stary refresh_token (rotacja). Jezeli
    zapis nowego do bazy padnie (np. 'database is locked' przy REINDEX/backup),
    integracja jest martwa az do recznego /allegro/auth - bo Allegro nie da
    nam juz odswiezyc starym tokenem. Dlatego 3 proby z rosnacym sleep.

    Zwraca True jezeli zapisano, False jezeli token bezpowrotnie utracony.
    """
    last_err = None
    for attempt in range(1, 4):
        try:
            set_config('allegro_access_token', access_token)
            set_config('allegro_refresh_token', refresh_token)
            set_config('allegro_token_expires', expires_at.isoformat())
            if refresh_rotated:
                refresh_expires = datetime.now() + timedelta(days=30)
                set_config('allegro_refresh_token_expires_at',
                           refresh_expires.isoformat())
            return True
        except sqlite3.OperationalError as e:
            last_err = e
            print(f"[DB] Zapis tokena - proba {attempt}/3 padla (lock?): {e}")
            time.sleep(2 * attempt)  # 2s, 4s, 6s
        except Exception as e:
            last_err = e
            print(f"[DB] Zapis tokena - proba {attempt}/3 padla: {e}")
            time.sleep(2 * attempt)

    # 3 proby padly - token z Allegro UTRACONY (stary refresh juz zuzyty)
    print("[CRITICAL] Allegro zwrocilo 200 OK ale zapis do bazy padl 3x: "
          f"{last_err}")
    print("[CRITICAL] Stary refresh_token zostal ZUZYTY przez Allegro - "
          "WYMAGANE reczne /allegro/auth")
    _send_alert_now(
        "🔴 KRYTYCZNE: Allegro odświeżyło token (HTTP 200), ale zapis do "
        f"bazy padł 3× ({last_err}).\n\n"
        "Stary refresh_token został już ZUŻYTY przez Allegro — integracja "
        "martwa do ręcznego /allegro/auth.\n\n"
        "Najpewniej 'database is locked' (REINDEX/backup). "
        "Wejdź na /allegro/auth NATYCHMIAST."
    )
    return False


def _handle_failure(category, detail):
    """FIX 2026-05-10 (B+D): obsluga porazki refresh z klasyfikacja bledu.

    category: 'NET' (DNS/connection/timeout), 'API' (4xx/5xx z Allegro),
              'DB' (blad bazy), 'ERR' (inne).
    Loguje z prefixem [category], inkrementuje licznik, alertuje Telegram
    dopiero po _ALERT_THRESHOLD porazkach z rzedu (throttled 1/h).
    Zwraca False - zeby caller mogl `return _handle_failure(...)`.
    """
    global _refresh_failure_count, _last_error_category, _last_error_detail
    _refresh_failure_count += 1
    _last_error_category = category
    _last_error_detail = detail
    print(f"[{category}] Refresh padl (#{_refresh_failure_count}): {detail}")

    if _refresh_failure_count >= _ALERT_THRESHOLD:
        opis = {
            'NET': 'Błąd sieci/DNS (RPi nie rozwiązuje allegro.pl?)',
            'API': 'Allegro odrzuca refresh_token',
            'DB':  'Błąd bazy przy zapisie tokena',
            'ERR': 'Nieznany błąd',
        }.get(category, 'Błąd')
        _maybe_send_alert(
            f"❌ Refresh tokena Allegro — {opis} (próba #{_refresh_failure_count})\n"
            f"[{category}] {detail[:200]}\n"
            f"{'Wymagane /allegro/auth' if category in ('API', 'DB') else 'Sprawdź sieć RPi'}"
        )
    return False


def refresh_allegro_token():
    """Odświeża token Allegro używając refresh_token"""
    global _refresh_failure_count, _refresh_last_alert_at, _refresh_gave_up
    global _last_known_refresh_token, _last_error_category
    try:
        print("[SYNC] Odświeżanie tokena Allegro...")

        refresh_token = get_config('allegro_refresh_token', '')
        client_id = get_config('allegro_client_id', '')
        client_secret = get_config('allegro_client_secret', '')

        if not all([refresh_token, client_id, client_secret]):
            print("[ERR] Brak wymaganych danych do odświeżenia tokena")
            return False

        # Allegro OAuth2 token refresh endpoint
        token_url = 'https://allegro.pl/auth/oauth/token'

        # FIX 2026-05-10: Basic Auth zgodnie z RFC 6749 §2.3.1 - Allegro odrzuca
        # client_id/secret w body z HTTP 401 "invalid_client". Zsynchronizowane
        # z `allegro_api.refresh_access_token` ktore Basic Auth uzywa.
        auth_string = f"{client_id}:{client_secret}"
        auth_b64 = base64.b64encode(auth_string.encode()).decode()
        headers = {
            'Authorization': f'Basic {auth_b64}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token
        }

        # FIX 2026-05-10 (B): rozroznienie bledu sieci (DNS/connection/timeout)
        # od bledu API. ConnectionError obejmuje NameResolutionError (DNS) -
        # to wlasnie ten przypadek z 06:56 'Failed to resolve allegro.pl'.
        try:
            response = requests.post(token_url, headers=headers,
                                     data=data, timeout=15)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            return _handle_failure('NET', f"Brak połączenia z allegro.pl: {e}")
        except requests.exceptions.RequestException as e:
            return _handle_failure('NET', f"Błąd HTTP request: {e}")

        if response.status_code == 200:
            token_data = response.json()
            new_access_token = token_data.get('access_token')
            # Allegro czasem rotuje refresh_token (nowy w odpowiedzi)
            new_refresh_token = token_data.get('refresh_token', refresh_token)
            expires_in = token_data.get('expires_in', 43200)  # domyslnie 12h
            expires_at = datetime.now() + timedelta(seconds=expires_in)
            refresh_rotated = new_refresh_token != refresh_token

            # FIX 2026-05-10 (A): zapis z retry. Allegro JUZ zuzyl stary
            # refresh - utrata tego zapisu = smierc integracji.
            if not _persist_tokens(new_access_token, new_refresh_token,
                                   expires_at, refresh_rotated):
                # Token bezpowrotnie utracony - alert juz poszedl w _persist_tokens
                return _handle_failure(
                    'DB', 'Allegro 200 OK ale zapis padl 3x - token utracony')

            print(f"[OK] Token odświeżony! Wygasa: "
                  f"{expires_at.strftime('%Y-%m-%d %H:%M:%S')}")

            # Sukces: reset licznika, throttle, early-stop + zapamietaj token
            _refresh_failure_count = 0
            _refresh_last_alert_at = 0
            _refresh_gave_up = False
            _last_known_refresh_token = new_refresh_token
            _last_error_category = None

            try:
                from modules.telegram_bot import send_telegram
                send_telegram(f"✅ Token Allegro odświeżony\nWygasa: "
                              f"{expires_at.strftime('%Y-%m-%d %H:%M')}")
            except Exception:
                pass

            return True
        else:
            # FIX 2026-05-10 (B): blad API (400=martwy refresh, 401=zly client)
            return _handle_failure(
                'API',
                f"HTTP {response.status_code}: {response.text[:200]}")

    except Exception as e:
        # FIX 2026-05-10 (B): nieznany blad (nie network, nie API) - np. JSON
        # decode, nieoczekiwany wyjatek. Klasyfikacja ERR.
        return _handle_failure('ERR', f"Wyjątek: {e}")

def start_token_refresh_daemon():
    """Uruchamia daemon automatycznego odświeżania tokena"""
    global _refresh_daemon_running, _refresh_thread
    
    if _refresh_daemon_running:
        print("[WARN]  Token refresh daemon już działa")
        return
    
    _refresh_daemon_running = True
    _refresh_thread = threading.Thread(target=_token_refresh_loop, daemon=True)
    _refresh_thread.start()
    print("[ROCK] Token refresh daemon uruchomiony")

def stop_token_refresh_daemon():
    """Zatrzymuje daemon odświeżania tokena"""
    global _refresh_daemon_running
    _refresh_daemon_running = False
    print("[DO_NOT] Token refresh daemon zatrzymany")

def _interruptible_sleep(seconds):
    """Sleep ktory reaguje na zatrzymanie daemona (1s granularnosc)."""
    for _ in range(int(seconds)):
        if not _refresh_daemon_running:
            return
        time.sleep(1)


def _token_refresh_loop():
    """Główna pętla token refresh daemon"""
    global _refresh_gave_up, _refresh_failure_count, _last_known_refresh_token

    # Sprawdź od razu przy starcie
    token_info = get_token_info()
    if token_info:
        print(f"ℹ  Token wygasa: {token_info['expires_at_str']} "
              f"(za {token_info['time_left_hours']:.1f}h)")
        if token_info['needs_refresh']:
            print("[WARN]  Token wymaga odświeżenia!")
            refresh_allegro_token()

    while _refresh_daemon_running:
        try:
            _interruptible_sleep(CHECK_INTERVAL)
            if not _refresh_daemon_running:
                break

            # FIX 2026-05-10 (C): recovery z early-stop. Gdy daemon sie poddal,
            # sprawdzamy tylko czy refresh_token w bazie sie zmienil (ktos
            # zrobil reczny /allegro/auth albo lazy refresh w allegro_api.py).
            # Jezeli tak -> wznow normalna prace.
            if _refresh_gave_up:
                current_rt = get_config('allegro_refresh_token', '')
                if current_rt and current_rt != _last_known_refresh_token:
                    print("[OK] Wykryto nowy refresh_token (reczny "
                          "/allegro/auth?) - wznawiam daemon")
                    _refresh_gave_up = False
                    _refresh_failure_count = 0
                    _last_known_refresh_token = current_rt
                continue  # dopoki gave_up - nie probuj refresh

            token_info = get_token_info()
            if not token_info:
                continue

            if not token_info['needs_refresh']:
                continue

            print(f"⏰ Token wymaga odświeżenia "
                  f"(wygasa za {token_info['time_left_hours']:.1f}h)")
            success = refresh_allegro_token()

            if success:
                print(f"[OK] Token odświeżony, następne sprawdzenie za "
                      f"{REFRESH_INTERVAL // 3600}h")
                continue

            # FIX 2026-05-10 (C): early-stop po _MAX_RETRY_ATTEMPTS porazkach.
            # Daemon przestaje probowac (nie waliny w martwy endpoint w
            # nieskonczonosc, nie ryzykujemy blokady klienta przez Allegro).
            if _refresh_failure_count >= _MAX_RETRY_ATTEMPTS:
                if not _refresh_gave_up:
                    _refresh_gave_up = True
                    _last_known_refresh_token = get_config(
                        'allegro_refresh_token', '')
                    print(f"[STOP] Early-stop po {_refresh_failure_count} "
                          f"porazkach ({_last_error_category}). Daemon "
                          f"wstrzymany do recznego /allegro/auth.")
                    _send_alert_now(
                        f"🛑 Auto-refresh Allegro WSTRZYMANY po "
                        f"{_refresh_failure_count} próbach "
                        f"([{_last_error_category}] {_last_error_detail[:150]}).\n\n"
                        f"Daemon NIE będzie więcej próbował (ochrona przed "
                        f"blokadą klienta).\n\nWymagane ręczne /allegro/auth — "
                        f"po nim daemon wznowi się sam."
                    )
                continue

            # FIX 2026-05-10 (C): exponential backoff zamiast stalych 10 min.
            # 60s, 120s, 240s, 480s (cap 3600s). Allegro moze throttlowac
            # klienta po seryjnych failach - backoff = grzecznosc.
            backoff = min(60 * (2 ** (_refresh_failure_count - 1)), 3600)
            print(f"[{_last_error_category}] Ponowna próba za {backoff}s "
                  f"(porażka #{_refresh_failure_count}/{_MAX_RETRY_ATTEMPTS})")
            _interruptible_sleep(backoff)

        except Exception as e:
            print(f"[ERR] Błąd w token refresh daemon: {e}")
            time.sleep(60)

# ============================================================
# FLASK BLUEPRINT (opcjonalnie)
# ============================================================

try:
    from flask import Blueprint, jsonify, request
    
    token_refresh_bp = Blueprint('token_refresh', __name__)
    
    @token_refresh_bp.route('/token/info')
    def api_token_info():
        """API endpoint do sprawdzania statusu tokena"""
        token_info = get_token_info()
        if token_info:
            return jsonify({
                'success': True,
                'token_info': {
                    'expires_at': token_info['expires_at_str'],
                    'time_left_hours': round(token_info['time_left_hours'], 1),
                    'is_expired': token_info['is_expired'],
                    'needs_refresh': token_info['needs_refresh']
                }
            })
        return jsonify({'success': False, 'error': 'Brak tokena'}), 404
    
    @token_refresh_bp.route('/token/refresh', methods=['POST'])
    def api_refresh_token():
        """API endpoint do ręcznego odświeżenia tokena"""
        success = refresh_allegro_token()
        if success:
            return jsonify({'success': True, 'message': 'Token odświeżony'})
        return jsonify({'success': False, 'error': 'Nie udało się odświeżyć tokena'}), 500

    @token_refresh_bp.route('/token/health')
    def api_token_health():
        """FIX 2026-05-10 (F): healthcheck dla monitoringu zewnetrznego
        (UptimeRobot itp). HTTP 200 = zdrowy, HTTP 503 = wymaga uwagi.
        Monitor patrzy tylko na kod HTTP - nie trzeba parsowac JSON.
        """
        body = {
            'healthy': True,
            'access_token': 'missing',
            'refresh_token': 'unknown',
            'daemon_gave_up': _refresh_gave_up,
            'consecutive_failures': _refresh_failure_count,
            'last_error_category': _last_error_category,
            'last_error_detail': _last_error_detail[:200] if _last_error_detail else None,
        }

        info = get_token_info()
        if not info:
            body['access_token'] = 'missing'
            body['healthy'] = False
        else:
            body['access_token'] = 'expired' if info['is_expired'] else 'ok'
            body['access_expires_in_h'] = round(info['time_left_hours'], 1)
            if info['is_expired']:
                body['healthy'] = False

        refresh_exp = get_config('allegro_refresh_token_expires_at', '')
        if refresh_exp:
            try:
                secs = (datetime.fromisoformat(refresh_exp)
                        - datetime.now()).total_seconds()
                days = secs / 86400
                body['refresh_expires_in_days'] = round(days, 1)
                if days < 0:
                    body['refresh_token'] = 'expired'
                    body['healthy'] = False
                elif days < 7:
                    body['refresh_token'] = 'expiring_soon'
                else:
                    body['refresh_token'] = 'ok'
            except Exception:
                body['refresh_token'] = 'parse_error'

        # early-stop = nie zdrowy (wymaga recznego /allegro/auth)
        if _refresh_gave_up:
            body['healthy'] = False

        return jsonify(body), (200 if body['healthy'] else 503)
    
except ImportError:
    # Flask niedostępny
    token_refresh_bp = None

if __name__ == '__main__':
    # Test modułu
    print("[SCIE] Test modułu token refresh...")
    
    token_info = get_token_info()
    if token_info:
        print(f"[ASSI] Status tokena:")
        print(f"  Wygasa: {token_info['expires_at_str']}")
        print(f"  Zostało: {token_info['time_left_hours']:.1f} godzin")
        print(f"  Wymaga odświeżenia: {'TAK' if token_info['needs_refresh'] else 'NIE'}")
        
        if token_info['needs_refresh']:
            print("\n[SYNC] Odświeżam token...")
            success = refresh_allegro_token()
            print(f"Wynik: {'[OK] Sukces' if success else '[ERR] Błąd'}")
    else:
        print("[ERR] Brak tokena lub nieprawidłowe dane")
