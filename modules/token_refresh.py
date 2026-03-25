"""
Auto-refresh tokena Allegro
Automatycznie odświeża token przed jego wygaśnięciem
"""

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

def get_token_info():
    """Pobiera informacje o tokenie"""
    token = get_config('allegro_access_token', '')
    expires_at_str = get_config('allegro_token_expires_at', '')
    
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

def refresh_allegro_token():
    """Odświeża token Allegro używając refresh_token"""
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
        
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'client_id': client_id,
            'client_secret': client_secret
        }
        
        response = requests.post(token_url, data=data, timeout=15)
        
        if response.status_code == 200:
            token_data = response.json()
            
            # Zapisz nowy token
            new_access_token = token_data.get('access_token')
            new_refresh_token = token_data.get('refresh_token', refresh_token)  # Czasem dostajemy nowy refresh token
            expires_in = token_data.get('expires_in', 43200)  # Domyślnie 12h
            
            expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            set_config('allegro_access_token', new_access_token)
            set_config('allegro_refresh_token', new_refresh_token)
            set_config('allegro_token_expires_at', expires_at.isoformat())
            
            print(f"[OK] Token odświeżony! Wygasa: {expires_at.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Wyślij powiadomienie Telegram jeśli dostępne
            try:
                from modules.telegram_bot import send_telegram
                send_telegram(f"<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#22c55e">check_circle</span> Token Allegro odświeżony\nWygasa: {expires_at.strftime('%Y-%m-%d %H:%M')}")
            except:
                pass
            
            return True
        else:
            print(f"[ERR] Błąd odświeżania tokena: {response.status_code} - {response.text}")
            
            # Wyślij powiadomienie o błędzie
            try:
                from modules.telegram_bot import send_telegram
                send_telegram(f"<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#ef4444">cancel</span> Błąd odświeżania tokena Allegro\nKod: {response.status_code}\nMusisz zalogować się ponownie!")
            except:
                pass
            
            return False
            
    except Exception as e:
        print(f"[ERR] Wyjątek podczas odświeżania tokena: {e}")
        
        try:
            from modules.telegram_bot import send_telegram
            send_telegram(f"<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#ef4444">cancel</span> Błąd odświeżania tokena Allegro\n{str(e)}\nMusisz zalogować się ponownie!")
        except:
            pass
        
        return False

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
    print("<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">do_not_disturb</span> Token refresh daemon zatrzymany")

def _token_refresh_loop():
    """Główna pętla token refresh daemon"""
    # Sprawdź od razu przy starcie
    token_info = get_token_info()
    if token_info:
        print(f"ℹ  Token wygasa: {token_info['expires_at_str']} (za {token_info['time_left_hours']:.1f}h)")
        
        if token_info['needs_refresh']:
            print("[WARN]  Token wymaga odświeżenia!")
            refresh_allegro_token()
    
    while _refresh_daemon_running:
        try:
            # Sprawdzaj co minutę
            for _ in range(CHECK_INTERVAL):
                if not _refresh_daemon_running:
                    break
                time.sleep(1)
            
            if not _refresh_daemon_running:
                break
            
            # Sprawdź status tokena
            token_info = get_token_info()
            
            if not token_info:
                # Brak tokena, poczekaj
                continue
            
            # Jeśli token wygasł lub potrzebuje odświeżenia
            if token_info['needs_refresh']:
                print(f"⏰ Token wymaga odświeżenia (wygasa za {token_info['time_left_hours']:.1f}h)")
                success = refresh_allegro_token()
                
                if success:
                    # Po udanym odświeżeniu czekaj 11 godzin
                    print(f"[OK] Token odświeżony, następne odświeżenie za {REFRESH_INTERVAL//3600}h")
                else:
                    # Po błędzie spróbuj ponownie za 10 minut
                    print("[ERR] Błąd odświeżania, ponowna próba za 10 minut")
                    for _ in range(600):
                        if not _refresh_daemon_running:
                            break
                        time.sleep(1)
                        
        except Exception as e:
            print(f"[ERR] Błąd w token refresh daemon: {e}")
            time.sleep(60)  # Odczekaj minutę po błędzie

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
