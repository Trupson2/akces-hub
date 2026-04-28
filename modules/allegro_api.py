"""
Allegro API module - pełna integracja z Allegro REST API
OAuth2 + zamówienia + oferty + powiadomienia
WERSJA: 2.0 (z poprawkami uploadu zdjęć)
"""

import os
import json
import time
import base64
import requests
import hashlib
import re
from datetime import datetime, timedelta
from flask import Blueprint, request, redirect, jsonify
from flask_wtf.csrf import generate_csrf
from io import BytesIO

from .database import get_db, get_config, set_config
from .telegram_bot import send_telegram, alert_sprzedaz, alert_whatsapp_sprzedaz, whatsapp_enabled

allegro_bp = Blueprint('allegro', __name__)

# ============================================================
# KONFIGURACJA ALLEGRO API
# ============================================================
ALLEGRO_AUTH_URL = "https://allegro.pl/auth/oauth/authorize"
ALLEGRO_TOKEN_URL = "https://allegro.pl/auth/oauth/token"
ALLEGRO_API_URL = "https://api.allegro.pl"

# Sandbox (do testów)
ALLEGRO_SANDBOX_AUTH_URL = "https://allegro.pl.allegrosandbox.pl/auth/oauth/authorize"
ALLEGRO_SANDBOX_TOKEN_URL = "https://allegro.pl.allegrosandbox.pl/auth/oauth/token"
ALLEGRO_SANDBOX_API_URL = "https://api.allegro.pl.allegrosandbox.pl"

# Folder na zdjęcia - NOWA STRUKTURA: static/downloads/{asin}/
DOWNLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'downloads')
IMAGES_DIR = DOWNLOADS_DIR  # Backward compatibility

# Retry config
MAX_RETRIES = 3
RETRY_DELAY = 2

def ensure_images_dir(asin=None):
    """
    Tworzy folder na zdjęcia jeśli nie istnieje.
    NOWA WERSJA: Tworzy subfolder dla każdego ASIN: static/downloads/{asin}/
    """
    base_dir = DOWNLOADS_DIR
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)
    
    if asin:
        # Stwórz subfolder dla ASIN
        asin_dir = os.path.join(base_dir, str(asin))
        if not os.path.exists(asin_dir):
            os.makedirs(asin_dir)
        return asin_dir
    
    if not os.path.exists('logs'):
        os.makedirs('logs')
    
    return base_dir


# ============================================================
# FUNKCJE POMOCNICZE DLA ZDJĘĆ (POPRAWIONE)
# ============================================================

def is_allegro_url(url):
    """
    Sprawdza czy URL jest już uploadowany na Allegro.
    Obsługuje różne formaty URL-i Allegro.
    """
    if not url or not isinstance(url, str):
        return False
    
    url_lower = url.lower()
    allegro_patterns = [
        'allegroimg.com',
        'allegrostatic.com',
        'allegro.pl/sale/images',
        'a.allegroimg',
        'b.allegroimg',
        'c.allegroimg',
        'allegrolokalnie',
    ]
    
    return any(pattern in url_lower for pattern in allegro_patterns)


def validate_image_url(url):
    """
    Waliduje URL zdjęcia przed próbą pobrania.
    Returns: (is_valid, cleaned_url, reason)
    """
    if not url or not isinstance(url, str):
        return False, None, "Pusty URL"
    
    url = url.strip()
    
    if not url.startswith('http'):
        return False, None, "URL nie zaczyna się od http"
    
    if len(url) > 2048:
        return False, None, "URL za długi"
    
    # Normalizuj URL Amazon dla lepszej jakości
    if 'media-amazon.com' in url or 'amazon.com/images' in url:
        url = re.sub(r'\._[A-Z0-9_,]+_\.', '._AC_SL1500_.', url)
        url = url.replace('http://', 'https://')
    
    return True, url, "OK"


def download_image(url, asin=None, image_index=None, force_redownload=False):
    """
    Pobiera zdjęcie z URL, konwertuje do JPEG i zapisuje lokalnie.
    WERSJA 3.0: NOWA ORGANIZACJA KATALOGÓW
    - Jeśli podano ASIN: zapisuje w static/downloads/{asin}/image_{index}.jpg
    - Jeśli brak ASIN: zapisuje w static/downloads/ z hashem MD5
    
    Args:
        url: URL zdjęcia do pobrania
        asin: ASIN produktu (opcjonalnie)
        image_index: Numer zdjęcia w galerii (1, 2, 3...) (opcjonalnie)
        force_redownload: Wymuś ponowne pobranie jeśli plik już istnieje
    
    Returns: ścieżka do pliku lub None
    """
    try:
        from PIL import Image
        
        # Generuj nazwę pliku i ścieżkę
        if asin:
            # Nowa struktura: static/downloads/{asin}/image_N.jpg
            target_dir = ensure_images_dir(asin)
            if image_index is not None:
                filename = f"image_{image_index}.jpg"
            else:
                url_hash = hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()[:8]
                filename = f"{asin}_{url_hash}.jpg"
            filepath = os.path.join(target_dir, filename)
        else:
            # Fallback: static/downloads/hash.jpg
            ensure_images_dir()
            filename = f"{hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()}.jpg"
            filepath = os.path.join(DOWNLOADS_DIR, filename)
        
        # Sprawdź cache
        if not force_redownload and os.path.exists(filepath):
            file_size = os.path.getsize(filepath)
            if file_size > 5000:
                print(f"    [FOLD] Cache hit: {filepath} ({file_size} bytes)")
                return filepath
            else:
                print(f"    [WARN] Cache file too small ({file_size} bytes), redownloading...")
                try:
                    os.remove(filepath)
                except:
                    pass
        
        # Nagłówki
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            'Accept-Language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://www.amazon.de/',
            'Sec-Fetch-Dest': 'image',
            'Sec-Fetch-Mode': 'no-cors',
            'Sec-Fetch-Site': 'cross-site',
        }
        
        # Pobierz z retry
        response = None
        last_error = None
        
        for attempt in range(MAX_RETRIES):
            try:
                print(f"    [DOWN] Download attempt {attempt + 1}/{MAX_RETRIES}: {url[:60]}...")
                response = requests.get(url, headers=headers, timeout=60)  # Zwiększono z 30 do 60s dla ngrok
                
                if response.status_code == 200 and len(response.content) > 1000:
                    break
                elif response.status_code != 200:
                    last_error = f"HTTP {response.status_code}"
                else:
                    last_error = f"Too small: {len(response.content)} bytes"
                    
            except requests.RequestException as e:
                last_error = str(e)
            
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
        
        if not response or response.status_code != 200:
            print(f"    [ERR] Download failed: {last_error}")
            return None
        
        if len(response.content) < 1000:
            print(f"    [ERR] Image too small: {len(response.content)} bytes")
            return None
        
        # Konwertuj do JPEG
        try:
            img = Image.open(BytesIO(response.content))
            print(f"    [STRA] Image: {img.size}, format={img.format}, mode={img.mode}")
            
            # Konwertuj do RGB
            if img.mode in ('RGBA', 'P', 'LA', 'PA'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                if img.mode in ('RGBA', 'LA', 'PA'):
                    try:
                        background.paste(img, mask=img.split()[-1])
                    except:
                        background.paste(img)
                else:
                    background.paste(img)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Zapisz jako JPEG
            img.save(filepath, 'JPEG', quality=90, optimize=True)
            final_size = os.path.getsize(filepath)
            print(f"    [OK] Saved: {filepath} ({final_size} bytes)")
            return filepath
            
        except Exception as e:
            print(f"    [WARN] Pillow error: {e}, saving raw...")
            with open(filepath, 'wb') as f:
                f.write(response.content)
            return filepath
        
    except Exception as e:
        print(f"    [ERR] Download exception: {e}")
        import traceback
        traceback.print_exc()
        return None


def upload_image_to_allegro(image_path_or_url, asin=None):
    """
    Uploaduje zdjęcie do Allegro i zwraca URL.
    WERSJA 2.0 z lepszą obsługą błędów i retry.
    
    Args:
        image_path_or_url: Ścieżka do pliku lokalnego LUB URL zdjęcia
        asin: ASIN produktu (opcjonalnie, dla lepszej organizacji plików)
    
    Returns: URL zdjęcia na Allegro lub None
    """
    from PIL import Image
    
    if not is_authenticated():
        print("    [ERR] Upload: nie zalogowany do Allegro")
        return None
    
    try:
        local_path = None
        
        # Jeśli to URL
        if isinstance(image_path_or_url, str) and image_path_or_url.startswith('http'):
            # Sprawdź czy już na Allegro
            if is_allegro_url(image_path_or_url):
                print(f"    [OK] Already on Allegro: {image_path_or_url[:50]}...")
                return image_path_or_url
            
            # Waliduj URL
            is_valid, clean_url, reason = validate_image_url(image_path_or_url)
            if not is_valid:
                print(f"    [ERR] Invalid URL: {reason}")
                return None
            
            print(f"    [DOWN] Downloading: {clean_url[:60]}...")
            
            # PRZEKAŻ ASIN do download_image
            print(f"    [SELL] ASIN: {asin if asin else '(brak - użyje hash)'}")
            local_path = download_image(clean_url, asin=asin)
            if not local_path:
                print(f"    [WARN] Download failed")
                return None
            print(f"    [OK] Downloaded to: {local_path}")
        else:
            local_path = image_path_or_url
        
        # Normalizuj ścieżkę (Windows backslash → forward slash)
        if local_path:
            local_path = os.path.normpath(local_path)
        
        if not local_path or not os.path.exists(local_path):
            # Spróbuj ścieżkę absolutną (relative to project root)
            if local_path:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                abs_path = os.path.join(base_dir, local_path)
                abs_path = os.path.normpath(abs_path)
                if os.path.exists(abs_path):
                    local_path = abs_path
                    print(f"    [FOLD] Found at absolute path: {local_path}")
                else:
                    print(f"    [WARN] File not found: {local_path} (tried also: {abs_path})")
                    return None
            else:
                print(f"    [WARN] File not found: {local_path}")
                return None
        
        # Sprawdź rozmiar
        file_size = os.path.getsize(local_path)
        print(f"    [FOLD] File size: {file_size} bytes")
        
        if file_size < 1000:
            print(f"    [WARN] File too small: {file_size} bytes")
            return None
        
        if file_size > 10 * 1024 * 1024:
            print(f"    [WARN] File too large: {file_size} bytes (max 10MB)")
            return None
        
        # Wczytaj plik
        with open(local_path, 'rb') as f:
            image_data = f.read()
        
        # Sprawdź czy to JPEG
        if not image_data[:2] == b'\xff\xd8':
            print(f"    [WARN] Not JPEG, converting...")
            try:
                img = Image.open(local_path)
                if img.mode in ('RGBA', 'P', 'LA', 'PA'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    try:
                        background.paste(img, mask=img.split()[-1])
                    except:
                        background.paste(img)
                    img = background
                elif img.mode != 'RGB':
                    img = img.convert('RGB')
                
                buffer = BytesIO()
                img.save(buffer, 'JPEG', quality=90)
                image_data = buffer.getvalue()
                print(f"    [OK] Converted to JPEG: {len(image_data)} bytes")
            except Exception as e:
                print(f"    [ERR] Conversion failed: {e}")
                return None
        
        # Upload do Allegro z retry
        config = get_allegro_config()
        api_url = ALLEGRO_SANDBOX_API_URL if config['sandbox'] else ALLEGRO_API_URL
        
        headers = {
            'Authorization': f"Bearer {config['access_token']}",
            'Content-Type': 'image/jpeg',
            'Accept': 'application/vnd.allegro.public.v1+json'
        }
        
        last_error = None
        
        for attempt in range(MAX_RETRIES):
            try:
                print(f"    [UPLO] Upload attempt {attempt + 1}/{MAX_RETRIES}...")
                
                response = requests.post(
                    f"{api_url}/sale/images",
                    headers=headers,
                    data=image_data,
                    timeout=60
                )
                
                print(f"    [SATE] Response: {response.status_code}")
                
                if response.status_code in [200, 201]:
                    # Allegro zwraca URL w nagłówku Location
                    allegro_url = response.headers.get('Location')
                    if allegro_url:
                        print(f"    [OK] Success: {allegro_url[:60]}...")
                        return allegro_url
                    
                    # Fallback - sprawdź body
                    try:
                        data = response.json()
                        allegro_url = data.get('location') or data.get('externalUrl') or data.get('url')
                        if allegro_url:
                            print(f"    [OK] Success (body): {allegro_url[:60]}...")
                            return allegro_url
                        print(f"    [WARN] Response without URL: {data}")
                    except:
                        print(f"    [WARN] Cannot parse response")
                    
                    last_error = "No URL in response"
                    
                elif response.status_code == 401:
                    print(f"    [ERR] Unauthorized - token expired?")
                    # Spróbuj odświeżyć token
                    if refresh_access_token():
                        config = get_allegro_config()
                        headers['Authorization'] = f"Bearer {config['access_token']}"
                        continue
                    return None
                    
                else:
                    try:
                        err_data = response.json()
                        errors = err_data.get('errors', [])
                        if errors:
                            last_error = errors[0].get('message', str(err_data))
                        else:
                            last_error = str(err_data)
                    except:
                        last_error = response.text[:200]
                    
                    print(f"    [ERR] Allegro error: {last_error}")
                    
            except requests.RequestException as e:
                last_error = str(e)
                print(f"    [ERR] Request error: {e}")
            
            if attempt < MAX_RETRIES - 1:
                print(f"    ⏳ Waiting {RETRY_DELAY}s before retry...")
                time.sleep(RETRY_DELAY)
        
        print(f"    [ERR] Upload failed after {MAX_RETRIES} attempts: {last_error}")
        return None
            
    except Exception as e:
        print(f"    [ERR] Upload exception: {e}")
        import traceback
        traceback.print_exc()
        return None


def cleanup_old_images(days=7):
    """Usuwa zdjęcia starsze niż X dni"""
    try:
        ensure_images_dir()
        deleted = 0
        cutoff = time.time() - (days * 24 * 60 * 60)
        
        for filename in os.listdir(IMAGES_DIR):
            filepath = os.path.join(IMAGES_DIR, filename)
            if os.path.isfile(filepath):
                if os.path.getmtime(filepath) < cutoff:
                    os.remove(filepath)
                    deleted += 1
        
        return deleted
    except Exception as e:
        print(f"Błąd czyszczenia zdjęć: {e}")
        return 0


def get_images_stats():
    """Zwraca statystyki folderu zdjęć"""
    try:
        ensure_images_dir()
        files = os.listdir(IMAGES_DIR)
        total_size = sum(os.path.getsize(os.path.join(IMAGES_DIR, f)) for f in files if os.path.isfile(os.path.join(IMAGES_DIR, f)))
        return {
            'count': len(files),
            'size_mb': round(total_size / (1024 * 1024), 2)
        }
    except:
        return {'count': 0, 'size_mb': 0}


# ============================================================
# KONFIGURACJA I AUTORYZACJA
# ============================================================

def get_allegro_config():
    # Use cached config to avoid 12 separate DB queries
    from modules.database import get_config_cached
    return {
        'client_id': get_config_cached('allegro_client_id', ''),
        'client_secret': get_config_cached('allegro_client_secret', ''),
        'access_token': get_config_cached('allegro_access_token', ''),
        'refresh_token': get_config_cached('allegro_refresh_token', ''),
        'token_expires': get_config_cached('allegro_token_expires', ''),
        'sandbox': get_config_cached('allegro_sandbox', 'false') == 'true',
        'redirect_uri': get_config_cached('allegro_redirect_uri', 'http://localhost:5000/allegro/callback'),
        'shipping_id': get_config_cached('allegro_shipping_id', ''),
        'city': get_config_cached('allegro_city', 'Poznan'),
        'province': get_config_cached('allegro_province', 'WIELKOPOLSKIE'),
        'postcode': get_config_cached('allegro_postcode', '61-001'),
    }


def get_api_urls():
    config = get_allegro_config()
    if config['sandbox']:
        return ALLEGRO_SANDBOX_AUTH_URL, ALLEGRO_SANDBOX_TOKEN_URL, ALLEGRO_SANDBOX_API_URL
    return ALLEGRO_AUTH_URL, ALLEGRO_TOKEN_URL, ALLEGRO_API_URL


def is_configured():
    config = get_allegro_config()
    return bool(config['client_id'] and config['client_secret'])


def is_authenticated():
    config = get_allegro_config()
    if not config['access_token']:
        return False
    if config['token_expires']:
        try:
            expires = datetime.fromisoformat(config['token_expires'])
            if datetime.now() >= expires:
                return refresh_access_token()
        except:
            pass
    return True


def refresh_access_token():
    config = get_allegro_config()
    if not config['refresh_token']:
        print("[TokenRefresh] Brak refresh_token — nie można odświeżyć")
        return False
    if not config.get('client_secret'):
        print("[TokenRefresh] Brak client_secret — nie można odświeżyć")
        return False

    _, token_url, _ = get_api_urls()

    try:
        auth_string = f"{config['client_id']}:{config['client_secret']}"
        auth_bytes = base64.b64encode(auth_string.encode()).decode()

        headers = {
            'Authorization': f'Basic {auth_bytes}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': config['refresh_token'],
            'redirect_uri': config['redirect_uri']
        }

        print(f"[TokenRefresh] POST {token_url} (client_id: {config['client_id'][:8]}...)")
        response = requests.post(token_url, headers=headers, data=data, timeout=30)
        print(f"[TokenRefresh] Status: {response.status_code}")

        if response.status_code == 200:
            tokens = response.json()
            set_config('allegro_access_token', tokens['access_token'])
            if 'refresh_token' in tokens:
                set_config('allegro_refresh_token', tokens['refresh_token'])
            expires_in = tokens.get('expires_in', 43200)
            expires_at = datetime.now() + timedelta(seconds=expires_in - 300)
            set_config('allegro_token_expires', expires_at.isoformat())
            print(f"[TokenRefresh] [OK] Token odświeżony, wygasa: {expires_at.isoformat()}")
            return True
        else:
            print(f"[TokenRefresh] [ERR] Błąd: {response.text[:200]}")
        return False
    except Exception as e:
        print(f"[TokenRefresh] [ERR] Wyjątek: {e}")
        return False


def allegro_request(method, endpoint, data=None, params=None, _retry=False, _attempt=0):
    config = get_allegro_config()
    _, _, api_url = get_api_urls()

    if not config['access_token']:
        # Próba auto-refresh
        if config.get('refresh_token') and config.get('client_secret'):
            print("[AllegroAPI] Brak access_token, próbuję refresh...")
            if refresh_access_token():
                config = get_allegro_config()
            else:
                return None, "Brak tokenu — zaloguj się ponownie na /allegro"
        else:
            return None, "Brak tokenu — zaloguj się na /allegro"

    # Sprawdź czy token wygasł
    if config['token_expires']:
        try:
            expires = datetime.fromisoformat(config['token_expires'])
            if datetime.now() >= expires:
                print(f"[AllegroAPI] Token wygasł ({config['token_expires']}), odświeżam...")
                if refresh_access_token():
                    config = get_allegro_config()
                else:
                    return None, "Token wygasł — zaloguj się ponownie na /allegro"
        except:
            pass

    headers = {
        'Authorization': f'Bearer {config["access_token"]}',
        'Accept': 'application/vnd.allegro.public.v1+json',
        'Content-Type': 'application/vnd.allegro.public.v1+json'
    }

    url = f"{api_url}{endpoint}"

    try:
        if method == 'GET':
            response = requests.get(url, headers=headers, params=params, timeout=30)
        elif method == 'POST':
            response = requests.post(url, headers=headers, json=data, timeout=30)
        elif method == 'PUT':
            response = requests.put(url, headers=headers, json=data, timeout=30)
        elif method == 'PATCH':
            response = requests.patch(url, headers=headers, json=data, timeout=30)
        elif method == 'DELETE':
            response = requests.delete(url, headers=headers, timeout=30)
        else:
            return None, f"Nieznana metoda: {method}"

        # Auto-refresh na 401 (max 1 retry)
        if response.status_code == 401 and not _retry:
            print(f"[AllegroAPI] 401 na {endpoint}, odświeżam token...")
            if refresh_access_token():
                return allegro_request(method, endpoint, data, params, _retry=True)
            return None, "Nieautoryzowany — zaloguj się ponownie na /allegro"

        # Auto-refresh na 403 (token może być invalid)
        if response.status_code == 403 and not _retry:
            print(f"[AllegroAPI] 403 na {endpoint}, próbuję odświeżyć token...")
            if refresh_access_token():
                return allegro_request(method, endpoint, data, params, _retry=True)
            return None, "Brak dostępu — zaloguj się ponownie na /allegro"

        # Retry on 5xx server errors (max 2 retries)
        if response.status_code >= 500 and _attempt < 2:
            import time as _t
            _t.sleep(1 + _attempt)
            print(f"[AllegroAPI] {response.status_code} na {endpoint}, retry {_attempt+1}/2...")
            return allegro_request(method, endpoint, data, params, _retry, _attempt + 1)

        if response.status_code >= 400:
            error_details = f"Błąd {response.status_code}"
            try:
                # Zabezpieczenie na non-JSON responses (np. HTML error pages)
                content_type = response.headers.get('Content-Type', '')
                if 'json' not in content_type and 'text/html' in content_type:
                    error_details = f"Allegro zwróciło HTML (HTTP {response.status_code}) — prawdopodobny problem z tokenem lub uprawnieniami"
                    print(f"* {error_details}")
                    return None, error_details

                err_json = response.json()
                errors = err_json.get('errors', [])
                if errors:
                    msgs = []
                    for e in errors[:10]:
                        msg = e.get('userMessage') or e.get('message', '')
                        path = e.get('path', '')
                        if path:
                            msgs.append(f"{path}: {msg}")
                        else:
                            msgs.append(msg)
                    error_details = "; ".join(msgs)
                print(f"* Allegro API error {response.status_code}: {error_details} [URL: {method} {endpoint}]")
            except ValueError:
                # response.json() failed — not valid JSON
                error_details = f"Allegro API HTTP {response.status_code} (odpowiedź nie jest JSON)"
                print(f"* {error_details}: {response.text[:200]}")
            except Exception as ex:
                print(f"* Allegro API error {response.status_code}: {ex}")
            return None, error_details

        if response.text:
            try:
                return response.json(), None
            except ValueError:
                # Odpowiedź 200 ale nie JSON (np. PDF label)
                return {'raw_content': response.content, 'status': response.status_code}, None
        return {}, None
    except requests.exceptions.Timeout:
        if _attempt < 2:
            import time as _t
            _t.sleep(1 + _attempt)
            print(f"[AllegroAPI] Timeout na {endpoint}, retry {_attempt+1}/2...")
            return allegro_request(method, endpoint, data, params, _retry, _attempt + 1)
        return None, "Timeout — Allegro API nie odpowiada (po 3 próbach)"
    except requests.exceptions.ConnectionError:
        if _attempt < 2:
            import time as _t
            _t.sleep(1 + _attempt)
            print(f"[AllegroAPI] ConnectionError na {endpoint}, retry {_attempt+1}/2...")
            return allegro_request(method, endpoint, data, params, _retry, _attempt + 1)
        return None, "Brak połączenia z Allegro API (po 3 próbach)"
    except Exception as e:
        print(f"* Wyjątek allegro_request: {e}")
        return None, str(e)


# ============================================================
# FUNKCJE API
# ============================================================

def get_user_info():
    return allegro_request('GET', '/me')


def get_orders(status='READY_FOR_PROCESSING', limit=100, fetch_all=True, from_date=None):
    """Pobiera zamówienia. 
    - fetch_all=True: pobiera wszystkie strony
    - from_date: filtruje zamówienia od podanej daty (format ISO lub datetime)
    """
    all_orders = []
    offset = 0
    
    while True:
        params = {'status': status, 'limit': limit, 'offset': offset}
        
        # Dodaj filtr daty jeśli podany
        if from_date:
            if isinstance(from_date, str):
                params['updatedAt.gte'] = from_date
            else:
                params['updatedAt.gte'] = from_date.strftime('%Y-%m-%dT00:00:00Z')
        
        result, error = allegro_request('GET', '/order/checkout-forms', params=params)
        
        if error or not result:
            break
        
        orders = result.get('checkoutForms', [])
        all_orders.extend(orders)
        
        total = result.get('totalCount', len(orders))
        print(f"[ASSI] Pobrano {len(all_orders)}/{total} zamówień...")
        
        if not fetch_all or len(all_orders) >= total or not orders:
            break
        
        offset += limit
    
    return {'checkoutForms': all_orders, 'totalCount': len(all_orders)}, None


def get_order_details(order_id):
    return allegro_request('GET', f'/order/checkout-forms/{order_id}')


def get_offer_visits(offer_id):
    """Pobiera statystyki wyświetleń oferty z Allegro API."""
    # /sale/offers/{id}/visit-statistics deprecated → use /sale/product-offers/{id}
    result, error = allegro_request('GET', f'/sale/product-offers/{offer_id}')
    if result and not error:
        # Extract stats from product-offer response
        _stats = result.get('stats', {})
        result = {'totalViews': _stats.get('viewsCount', 0) or _stats.get('visitsCount', 0)}
    return result, error


def get_offer_smart_stats(offer_ids):
    """
    Pobiera statystyki (wyświetlenia, obserwujący) dla wielu ofert naraz.
    Allegro API: GET /sale/offer-events-statistics

    Args:
        offer_ids: lista ID ofert
    Returns:
        dict: {offer_id: {'views': int, 'watchers': int}}
    """
    stats = {}

    # Allegro: /sale/offers/{id} jest deprecated (403 od 2024)
    # Używamy /sale/product-offers/{id} jako replacement
    for i in range(0, len(offer_ids), 20):
        batch = offer_ids[i:i+20]
        for oid in batch:
            try:
                result, error = allegro_request('GET', f'/sale/product-offers/{oid}')
                if result and not error:
                    _stats = result.get('stats', {})
                    _stock = result.get('stock', {})
                    stats[str(oid)] = {
                        'views': _stats.get('viewsCount', 0) or _stats.get('visitsCount', 0) or 0,
                        'watchers': _stats.get('watchersCount', 0),
                        'sold': _stock.get('sold', 0),
                    }
            except Exception as e:
                print(f"  [WARN] Stats for {oid}: {e}")

    return stats


def sync_offer_stats(offer_ids=None):
    """
    Synchronizuje statystyki (wyświetlenia, obserwujący) dla aktywnych ofert.

    Args:
        offer_ids: opcjonalnie lista konkretnych ID ofert. Jeśli None, sync aktywnych.
    Returns:
        dict: Statystyki synchronizacji
    """
    if not is_authenticated():
        return {'error': 'Nie zalogowany do Allegro'}

    from .database import get_db
    conn = get_db()

    if not offer_ids:
        rows = conn.execute("SELECT allegro_id FROM oferty WHERE status='aktywna' AND allegro_id IS NOT NULL").fetchall()
        offer_ids = [r['allegro_id'] for r in rows]

    if not offer_ids:
        return {'updated': 0, 'message': 'Brak aktywnych ofert'}

    print(f"[BAR_] Sync stats for {len(offer_ids)} offers...")
    stats = get_offer_smart_stats(offer_ids)

    updated = 0
    for oid, s in stats.items():
        try:
            conn.execute('''UPDATE oferty SET wyswietlenia=?, obserwujacych=?, data_aktualizacji=datetime('now')
                           WHERE allegro_id=?''', (s.get('views', 0), s.get('watchers', 0), oid))
            updated += 1
        except:
            pass
    conn.commit()

    print(f"[BAR_] Updated stats for {updated}/{len(offer_ids)} offers")
    return {'updated': updated, 'total': len(offer_ids)}


def sync_offers_status():
    """
    Synchronizuje statusy ofert z Allegro API.
    Sprawdza które oferty są szkicami, aktywne, lub zakończone.
    
    Returns:
        dict: Statystyki synchronizacji
    """
    if not is_authenticated():
        return {'error': 'Nie zalogowany do Allegro'}
    
    print(f"[SYNC] Synchronizacja ofert z Allegro...")
    
    # Pobierz wszystkie oferty z Allegro
    result, error = get_my_offers(limit=100, fetch_all=True)
    
    if error:
        return {'error': f'Błąd API: {error}'}
    
    allegro_offers = result.get('offers', [])
    print(f"[INVE] Pobrano {len(allegro_offers)} ofert z Allegro")
    
    from .database import get_db
    conn = get_db()
    
    stats = {
        'total': len(allegro_offers),
        'draft': 0,
        'active': 0,
        'ended': 0,
        'updated': 0,
        'new': 0
    }
    
    # Mapa statusów Allegro → nasz system
    status_map = {
        'INACTIVE': 'draft',
        'ACTIVE': 'aktywna',
        'ACTIVATING': 'aktywna',
        'ENDED': 'zakonczona'
    }
    
    for offer in allegro_offers:
        if not offer or not isinstance(offer, dict):
            continue
        offer_id = offer.get('id')
        if not offer_id:
            continue
        allegro_status = (offer.get('publication') or {}).get('status', 'INACTIVE')
        our_status = status_map.get(allegro_status, 'draft')
        
        # Statystyki
        if allegro_status == 'INACTIVE':
            stats['draft'] += 1
        elif allegro_status in ['ACTIVE', 'ACTIVATING']:
            stats['active'] += 1
        elif allegro_status == 'ENDED':
            stats['ended'] += 1
        
        # Pobierz dodatkowe dane
        nazwa = offer.get('name', '')
        cena = float(((offer.get('sellingMode') or {}).get('price') or {}).get('amount', 0))
        ilosc = (offer.get('stock') or {}).get('available', 0)

        # Statystyki z offer (viewsCount, watchersCount)
        _offer_stats = offer.get('stats') or {}
        _views = _offer_stats.get('viewsCount', 0) or _offer_stats.get('visitsCount', 0) or 0
        _watchers = _offer_stats.get('watchersCount', 0)

        # Sprawdź czy oferta już jest w bazie
        existing = conn.execute('SELECT id, status FROM oferty WHERE allegro_id = ?', (offer_id,)).fetchone()
        
        if existing:
            # Aktualizuj istniejącą ofertę
            old_status = existing['status']
            
            # Pobierz datę wystawienia z Allegro (do uzupełnienia jeśli brak)
            publication = offer.get('publication') or {}
            raw_pub_date = publication.get('startedAt') or publication.get('startingAt') or offer.get('createdAt') or ''
            data_wystawienia_update = None
            if raw_pub_date:
                try:
                    raw_pub_date = raw_pub_date.replace('Z', '+00:00')
                    from datetime import datetime as _dt
                    dt_pub = _dt.fromisoformat(raw_pub_date)
                    dt_local = dt_pub.astimezone().replace(tzinfo=None)
                    data_wystawienia_update = dt_local.strftime('%Y-%m-%d %H:%M:%S')
                except:
                    data_wystawienia_update = raw_pub_date[:19].replace('T', ' ')
            
            conn.execute('''UPDATE oferty SET status = ?, tytul = ?, cena = ?, ilosc = ?,
                wyswietlenia = ?, obserwujacych = ?,
                data_aktualizacji = datetime('now'),
                data_wystawienia = CASE WHEN ? IS NOT NULL THEN ? ELSE data_wystawienia END
                WHERE allegro_id = ?''',
                (our_status, nazwa, cena, ilosc, _views, _watchers, data_wystawienia_update, data_wystawienia_update, offer_id))
            if old_status != our_status:
                stats['updated'] += 1
                print(f"  [OK] {offer_id[:8]}... status: {old_status} → {our_status}")
        else:
            # Dodaj nową ofertę
            # Spróbuj znaleźć produkt po nazwie lub external.id (ASIN)
            external_id = (offer.get('external') or {}).get('id', '')
            produkt_id = None

            if external_id:
                # external.id moze byc "ASIN / MAG-XXXXX" - wyciagnij sam ASIN
                _asin_from_ext = external_id.split(' / ')[0].strip() if ' / ' in external_id else external_id
                # Szukaj po ASIN — bierzemy najnowszy (data_dodania DESC) żeby trafić w właściwą paletę
                # Duplikaty ASIN: każda paleta to osobny produkt, bierzemy ostatnio dodany aktywny
                produkt = conn.execute('''
                    SELECT id FROM produkty
                    WHERE UPPER(asin) = UPPER(?) AND status != 'sprzedany'
                    ORDER BY data_dodania DESC, ilosc DESC LIMIT 1
                ''', (_asin_from_ext,)).fetchone()
                if produkt:
                    produkt_id = produkt['id']

            # Fallback: smart matching po nazwie + cenie
            if not produkt_id and nazwa and len(nazwa) > 5:
                try:
                    if not hasattr(sync_offers_status, '_prod_cache'):
                        sync_offers_status._prod_cache = _precompute_produkty_data(conn)
                    pid, conf = _find_best_product_match(nazwa, cena, sync_offers_status._prod_cache)
                    if pid and conf >= 0.5:
                        produkt_id = pid
                        print(f"  [SEAR] Smart match: {nazwa[:40]} → produkt [{pid}] ({conf:.0%})")
                except:
                    pass
            
            # Pobierz prawdziwą datę wystawienia z Allegro (publication.startedAt lub createdAt)
            publication = offer.get('publication') or {}
            raw_pub_date = publication.get('startedAt') or publication.get('startingAt') or publication.get('endingAt') or offer.get('createdAt') or ''
            data_wystawienia_val = None
            if raw_pub_date:
                try:
                    raw_pub_date = raw_pub_date.replace('Z', '+00:00')
                    from datetime import datetime as _dt
                    dt_pub = _dt.fromisoformat(raw_pub_date)
                    dt_local = dt_pub.astimezone().replace(tzinfo=None)
                    data_wystawienia_val = dt_local.strftime('%Y-%m-%d %H:%M:%S')
                except:
                    data_wystawienia_val = raw_pub_date[:19].replace('T', ' ')
            
            conn.execute('''INSERT INTO oferty (allegro_id, produkt_id, tytul, cena, ilosc, status, 
                data_wystawienia, data_aktualizacji) 
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))''',
                (offer_id, produkt_id, nazwa, cena, ilosc, our_status, data_wystawienia_val))
            stats['new'] += 1
            print(f"  🆕 {offer_id[:8]}... dodano jako {our_status}, wystawiona: {data_wystawienia_val}")
    
    # === KLUCZOWE: Oznacz oferty w bazie które NIE istnieją na Allegro ===
    # (usunięte szkice, ręcznie usunięte oferty itp.)
    allegro_ids_on_server = set()
    for offer in allegro_offers:
        if offer and isinstance(offer, dict) and offer.get('id'):
            allegro_ids_on_server.add(str(offer['id']))

    if allegro_ids_on_server:
        # Znajdź "aktywne" oferty w bazie których NIE MA na Allegro
        local_active = conn.execute(
            "SELECT id, allegro_id, tytul FROM oferty "
            "WHERE status IN ('active','ACTIVE','aktywna','wystawiona','published') "
            "AND allegro_id IS NOT NULL AND allegro_id != ''"
        ).fetchall()

        orphaned = 0
        for row in local_active:
            if str(row['allegro_id']) not in allegro_ids_on_server:
                conn.execute("UPDATE oferty SET status = 'zakonczona' WHERE id = ?", (row['id'],))
                orphaned += 1
                print(f"  [DELE] Oferta #{row['allegro_id']} ({row['tytul'][:30]}) nie istnieje na Allegro → zakończona")

        if orphaned:
            stats['orphaned'] = orphaned
            print(f"   [DELE] Usunięto z bazy: {orphaned} ofert nieistniejących na Allegro")

    conn.commit()

    print(f"\n[OK] Synchronizacja zakończona:")
    print(f"   [INVE] Wszystkich ofert: {stats['total']}")
    print(f"   [EDIT] Szkice: {stats['draft']}")
    print(f"   [OK] Aktywne: {stats['active']}")
    print(f"   ⏹ Zakończone: {stats['ended']}")
    print(f"   [SYNC] Zaktualizowano: {stats['updated']}")
    print(f"   🆕 Nowe: {stats['new']}")
    if stats.get('orphaned'):
        print(f"   [DELE] Usunięte (nie istnieją na Allegro): {stats['orphaned']}")

    # === SYNC STATYSTYK (wyświetlenia, obserwujący) dla aktywnych ofert ===
    # Pobiera po jednej ofercie z API — limituj do 50 żeby nie zabić API
    try:
        active_ids = [r['allegro_id'] for r in conn.execute(
            "SELECT allegro_id FROM oferty WHERE status='aktywna' AND allegro_id IS NOT NULL ORDER BY data_aktualizacji DESC LIMIT 50").fetchall()]
        if active_ids:
            print(f"\n[BAR_] Pobieram statystyki dla {len(active_ids)} aktywnych ofert...")
            offer_stats = get_offer_smart_stats(active_ids)
            _stats_updated = 0
            for oid, s in offer_stats.items():
                conn.execute('UPDATE oferty SET wyswietlenia=?, obserwujacych=? WHERE allegro_id=?',
                    (s.get('views', 0), s.get('watchers', 0), oid))
                _stats_updated += 1
            conn.commit()
            stats['stats_updated'] = _stats_updated
            print(f"[BAR_] Zaktualizowano statystyki: {_stats_updated}/{len(active_ids)} ofert")
    except Exception as e:
        print(f"[WARN] Stats sync error: {e}")

    return stats


def get_my_offers(limit=100, fetch_all=True):
    """Pobiera oferty. Jeśli fetch_all=True, pobiera wszystkie strony."""
    all_offers = []
    offset = 0
    
    while True:
        params = {'limit': limit, 'offset': offset}
        result, error = allegro_request('GET', '/sale/offers', params=params)
        
        if error or not result:
            break
        
        offers = result.get('offers', [])
        all_offers.extend(offers)
        
        total = result.get('totalCount', 0)
        print(f"[INVE] Pobrano {len(all_offers)}/{total} ofert...")
        
        if not fetch_all or len(all_offers) >= total or not offers:
            break
        
        offset += limit
    
    return {'offers': all_offers, 'totalCount': len(all_offers)}, None


def find_active_offer_on_allegro(asin=None, ean=None, nazwa=None):
    """Szuka aktywnej oferty BEZPOŚREDNIO na Allegro API (nie w lokalnej bazie).
    Sprawdza external.id (sygnatura ASIN) i nazwę oferty.
    Returns: dict {allegro_id, tytul, ilosc, cena} lub None
    """
    if not is_authenticated():
        return None

    try:
        # Pobierz aktywne oferty (limit 100 najnowszych)
        params = {'limit': 100, 'offset': 0,
                  'publication.status': 'ACTIVE'}
        result, error = allegro_request('GET', '/sale/offers', params=params)
        if error or not result:
            return None

        offers = result.get('offers', [])
        asin_upper = asin.upper().strip() if asin else ''
        ean_clean = ean.strip() if ean else ''

        for offer in offers:
            if not offer:
                continue
            offer_id = offer.get('id', '')
            offer_name = offer.get('name', '')
            ext_id = ((offer.get('external') or {}).get('id', '') or '').upper()
            offer_price = float(((offer.get('sellingMode') or {}).get('price') or {}).get('amount', 0))
            offer_stock = (offer.get('stock') or {}).get('available', 0)

            matched = False

            # Match by ASIN in external.id
            if asin_upper and asin_upper in ext_id:
                matched = True
                print(f"[DEDUP-API] Match po external.id: {asin_upper} in {ext_id}")

            # Match by EAN in external.id
            if not matched and ean_clean and ean_clean in ext_id:
                matched = True
                print(f"[DEDUP-API] Match po EAN w external.id: {ean_clean}")

            # Match by name similarity (80%+ words match)
            if not matched and nazwa and len(nazwa) > 10:
                _ignore = {'uniwersalne', 'uniwersalny', 'premium', 'zestaw', 'komplet', 'nowy', 'nowa'}
                _words_n = [w.lower() for w in nazwa.split() if len(w) > 2 and w.lower() not in _ignore][:6]
                _words_o = [w.lower() for w in offer_name.split() if len(w) > 2 and w.lower() not in _ignore][:6]
                if _words_n and _words_o:
                    _match_cnt = sum(1 for w in _words_n if any(w in wo or wo in w for wo in _words_o))
                    if _match_cnt >= max(3, len(_words_n) * 0.7):
                        matched = True
                        print(f"[DEDUP-API] Match po nazwie: '{offer_name[:40]}' ({_match_cnt}/{len(_words_n)} words)")

            if matched:
                # Zaimportuj ofertę do lokalnej bazy jeśli jej nie ma
                from .database import get_db
                conn = get_db()
                existing_local = conn.execute('SELECT id FROM oferty WHERE allegro_id = ?', (str(offer_id),)).fetchone()
                if not existing_local:
                    conn.execute('''INSERT INTO oferty (allegro_id, tytul, cena, ilosc, status, data_aktualizacji)
                        VALUES (?, ?, ?, ?, 'aktywna', datetime('now'))''',
                        (str(offer_id), offer_name, offer_price, offer_stock))
                    conn.commit()
                    print(f"[DEDUP-API] Zaimportowano ofertę {offer_id} do lokalnej bazy")

                return {
                    'allegro_id': str(offer_id),
                    'tytul': offer_name,
                    'ilosc': offer_stock,
                    'cena': offer_price
                }

        return None
    except Exception as e:
        print(f"[DEDUP-API] Błąd: {e}")
        return None


def detect_category_id(nazwa):
    """Wykrywa kategorię Allegro na podstawie nazwy produktu"""
    nazwa_lower = nazwa.lower()
    
    # WAŻNE: kolejność ma znaczenie — bardziej specyficzne frazy PRZED ogólnymi!
    category_map = [
        # === Motoryzacja - Relingi dachowe, bagażniki ===
        ('reling', '261636'),
        ('roof rail', '261636'),
        ('roof rack', '261636'),
        ('bagażnik dachowy', '261636'),
        ('cross bar', '261636'),
        ('crossbar', '261636'),
        ('belki dachowe', '261636'),
        ('belki relingi', '261636'),
        ('drążek przenośny', '261636'),

        # === Motoryzacja - Pokrowce ===
        ('pokrowc', '261680'),
        ('seat cover', '261680'),
        ('car seat', '261680'),
        ('autositzbezug', '261680'),
        ('sitzbezug', '261680'),
        ('coverado', '261680'),

        # === Motoryzacja - Ładowarki EV ===
        ('wallbox', '310037'),
        ('ev charger', '310037'),
        ('type 2', '310037'),
        ('type2', '310037'),
        ('ładowarka ev', '310037'),
        ('charging cable ev', '310037'),
        ('kabel ładujący', '310037'),
        ('ladekabel', '310037'),
        ('elektroauto', '310037'),

        # === Motoryzacja - Dywaniki ===
        ('dywanik', '261647'),
        ('floor mat', '261647'),
        ('fußmatte', '261647'),
        ('car mat', '261647'),
        ('mata bagażnika', '261648'),
        ('trunk mat', '261648'),
        ('cargo mat', '261648'),
        ('mata do bagażnika', '261648'),

        # === Motoryzacja - Maty grzewcze ===
        ('mata grzewcza', '261696'),
        ('heated seat', '261696'),
        ('sitzheizung', '261696'),

        # === Motoryzacja - Nakładki na progi ===
        ('nakładka na próg', '261665'),
        ('door sill', '261665'),
        ('scuff plate', '261665'),

        # === Motoryzacja - Osłony, spoilery ===
        ('spoiler', '261670'),
        ('deflector', '261670'),
        ('osłona silnika', '261662'),
        ('mud flap', '261667'),
        ('chlapacz', '261667'),

        # === Motoryzacja - Organizery ===
        ('organizer samocho', '261692'),
        ('car organizer', '261692'),
        ('schowek', '261692'),
        ('podłokietnik', '261692'),
        ('console organizer', '261692'),

        # === Motoryzacja - Oświetlenie samochodowe ===
        ('led car', '261658'),
        ('car light', '261658'),
        ('oświetlenie wnętrza', '261658'),

        # === Narzędzia warsztatowe ===
        ('lampa solarna', '228089'),  # Narzędzia warsztatowe - lampy
        ('lampa warsztat', '228089'),
        ('lampa lakier', '228089'),
        ('infrared lamp', '228089'),
        ('krótkofalowa', '228089'),
        ('suszarka lakier', '228089'),
        ('paint dryer', '228089'),
        ('heat lamp', '228089'),
        ('podnośnik', '228041'),  # Podnośniki
        ('jack stand', '228041'),
        ('car lift', '228041'),
        ('kompresor', '228053'),  # Kompresory
        ('spawarka', '228067'),  # Spawarki
        ('szlifierka', '228073'),  # Szlifierki
        ('wiertarka', '228075'),  # Wiertarki
        ('klucz udarowy', '228049'),  # Klucze udarowe
        ('wózek warsztat', '228087'),  # Wózki warsztatowe
        ('wózek narzędziowy', '228087'),

        # === Elektronika - specyficzne ===
        ('panel słoneczny', '214853'),  # Panele solarne
        ('solar panel', '214853'),
        ('monokrystaliczny', '214853'),
        ('polikrystaliczny', '214853'),
        ('fotowoltaiczny', '214853'),
        ('przełącznik ethernet', '172089'),  # Switche sieciowe
        ('switch ethernet', '172089'),
        ('network switch', '172089'),
        ('hub ethernet', '172089'),
        ('router', '172087'),
        ('access point', '172091'),
        ('kabel usb', '165'),
        ('usb cable', '165'),
        ('adapter', '165'),
        ('power bank', '174895'),
        ('powerbank', '174895'),
        ('ładowarka', '20650'),
        ('charger', '20650'),

        # === Dom i ogród - specyficzne ===
        ('lampa ogrodowa', '124402'),
        ('lampa stojąca', '260480'),
        ('lampa biurkowa', '260474'),
        ('lampa sufitowa', '260476'),
        ('lampa nocna', '260474'),
        ('żarówka', '260490'),
        ('drukarka 3d', '261345'),  # Drukarki 3D
        ('3d printer', '261345'),
        ('osuszacz', '260656'),  # Osuszacze powietrza
        ('dehumidifier', '260656'),
        ('odkurzacz', '260644'),

        # === Zabawki / Dziecięce ===
        ('zabawka', '261066'),
        ('toy', '261066'),
        ('spielzeug', '261066'),
        ('lego', '261066'),
        ('klocki', '261066'),
        ('pluszak', '261066'),
        ('puzzle', '261066'),
        ('lalka', '261066'),
        ('doll', '261066'),
        ('wózek dziecięcy', '261484'),
        ('fotelik', '261486'),
        ('child seat', '261486'),
        ('kindersitz', '261486'),
        ('kojec', '261488'),
        ('łóżeczko', '261488'),

        # === Sport / Fitness ===
        ('rower', '120028'),
        ('bike', '120028'),
        ('bicycle', '120028'),
        ('fahrrad', '120028'),
        ('hulajnoga', '265839'),
        ('scooter', '265839'),
        ('roller', '265839'),
        ('bieżnia', '260530'),
        ('treadmill', '260530'),
        ('walkingpad', '260530'),
        ('walking pad', '260530'),
        ('orbitrek', '260532'),
        ('elliptical', '260532'),
        ('hantle', '260536'),
        ('dumbbell', '260536'),
        ('kettlebell', '260536'),
        ('mata yoga', '260540'),
        ('yoga mat', '260540'),
        ('trampolin', '260538'),
        ('trampoline', '260538'),
        ('namiot', '122928'),
        ('tent', '122928'),
        ('sleeping bag', '122934'),
        ('śpiwór', '122934'),
        ('plecak', '122920'),
        ('backpack', '122920'),

        # === Ogród ===
        ('kosiarka', '124300'),
        ('lawn mower', '124300'),
        ('rasenmäher', '124300'),
        ('podkaszarka', '124302'),
        ('trimmer', '124302'),
        ('myjka ciśnieniowa', '124320'),
        ('pressure washer', '124320'),
        ('hochdruckreiniger', '124320'),
        ('dmuchawa', '124310'),
        ('leaf blower', '124310'),
        ('piła łańcuchowa', '124306'),
        ('chainsaw', '124306'),
        ('grill ogrodowy', '124360'),
        ('bbq', '124360'),
        ('meble ogrodowe', '124350'),
        ('garden furniture', '124350'),
        ('parasol ogrodowy', '124356'),
        ('basen ogrodowy', '124370'),
        ('pool', '124370'),
        ('fontanna', '124380'),
        ('zraszacz', '124340'),
        ('sprinkler', '124340'),

        # === Kuchnia / AGD małe ===
        ('mikser', '260644'),
        ('blender', '260644'),
        ('mixer', '260644'),
        ('toster', '260646'),
        ('toaster', '260646'),
        ('czajnik', '260648'),
        ('kettle', '260648'),
        ('wasserkocher', '260648'),
        ('ekspres do kawy', '260650'),
        ('coffee machine', '260650'),
        ('kaffeemaschine', '260650'),
        ('frytkownica', '260652'),
        ('air fryer', '260652'),
        ('heißluftfritteuse', '260652'),
        ('robot kuchenny', '260654'),
        ('food processor', '260654'),
        ('küchenmaschine', '260654'),
        ('sokowirówka', '260656'),
        ('juicer', '260656'),

        # === AGD duże ===
        ('klimatyzator', '260700'),
        ('air conditioner', '260700'),
        ('klimaanlage', '260700'),
        ('osuszacz powietrza', '260702'),
        ('dehumidifier', '260702'),
        ('oczyszczacz powietrza', '260704'),
        ('air purifier', '260704'),
        ('luftreiniger', '260704'),
        ('nawilżacz', '260706'),
        ('humidifier', '260706'),
        ('grzejnik', '260710'),
        ('heater', '260710'),
        ('radiator', '260710'),
        ('wentylator', '260712'),
        ('fan ', '260712'),
        ('ventilator', '260712'),

        # === Komputery / IT ===
        ('laptop', '491'),
        ('notebook', '491'),
        ('monitor', '260258'),
        ('klawiatura', '260262'),
        ('keyboard', '260262'),
        ('tastatur', '260262'),
        ('myszka', '260264'),
        ('mouse', '260264'),
        ('słuchawki', '260266'),
        ('headphone', '260266'),
        ('headset', '260266'),
        ('kopfhörer', '260266'),
        ('ssd', '260270'),
        ('dysk twardy', '260272'),
        ('hard drive', '260272'),
        ('festplatte', '260272'),
        ('pendrive', '260274'),
        ('flash drive', '260274'),
        ('usb stick', '260274'),
        ('ram ', '260276'),
        ('webcam', '260278'),
        ('kamera internet', '260278'),

        # === Telefony / Tablety ===
        ('smartfon', '165'),
        ('smartphone', '165'),
        ('iphone', '165'),
        ('samsung galaxy', '165'),
        ('xiaomi', '165'),
        ('tablet', '260200'),
        ('ipad', '260200'),
        ('etui na telefon', '260210'),
        ('phone case', '260210'),
        ('handyhülle', '260210'),
        ('folia ochronna', '260212'),
        ('screen protector', '260212'),

        # === Gaming ===
        ('gamepad', '261470'),
        ('kontroler', '261470'),
        ('controller', '261470'),
        ('konsola', '261472'),
        ('playstation', '261472'),
        ('xbox', '261472'),
        ('nintendo', '261472'),

        # === Oświetlenie ===
        ('żyrandol', '260476'),
        ('chandelier', '260476'),
        ('kronleuchter', '260476'),
        ('kinkiet', '260478'),
        ('wall lamp', '260478'),
        ('wandleuchte', '260478'),
        ('taśma led', '258682'),
        ('led strip', '258682'),
        ('led streifen', '258682'),
        ('reflektor', '258684'),
        ('spotlight', '258684'),
        ('strahler', '258684'),

        # === Łazienka ===
        ('bateria łazienkowa', '260600'),
        ('faucet', '260600'),
        ('wasserhahn', '260600'),
        ('prysznic', '260602'),
        ('shower', '260602'),
        ('dusche', '260602'),
        ('lustro', '260604'),
        ('mirror', '260604'),
        ('spiegel', '260604'),
        ('suszarka do włosów', '260606'),
        ('hair dryer', '260606'),
        ('haartrockner', '260606'),
        ('prostownica', '260608'),
        ('hair straightener', '260608'),
        ('lokówka', '260610'),
        ('curling iron', '260610'),

        # === Biuro ===
        ('krzesło biurowe', '260450'),
        ('office chair', '260450'),
        ('bürostuhl', '260450'),
        ('biurko', '260452'),
        ('desk', '260452'),
        ('schreibtisch', '260452'),
        ('niszczarka', '260454'),
        ('shredder', '260454'),
        ('fotel', '260450'),

        # === Zwierzęta ===
        ('karma', '261200'),
        ('pet food', '261200'),
        ('tierfutter', '261200'),
        ('akwarium', '261210'),
        ('aquarium', '261210'),
        ('klatka', '261212'),
        ('cage', '261212'),
        ('smycz', '261214'),
        ('leash', '261214'),
        ('leine', '261214'),
        ('obroża', '261216'),
        ('collar', '261216'),
        ('halsband', '261216'),

        # === Zdrowie / Uroda ===
        ('peruka', '260820'),
        ('wig', '260820'),
        ('perücke', '260820'),
        ('hair extension', '260822'),
        ('doczepiany', '260822'),
        ('masażer', '260800'),
        ('massager', '260800'),
        ('massage', '260800'),
        ('inhalator', '260802'),
        ('ciśnieniomierz', '260804'),
        ('termometr', '260806'),
        ('thermometer', '260806'),
        ('waga łazienkowa', '260808'),
        ('scale', '260808'),
        ('depilator', '260810'),
        ('epilator', '260810'),
        ('golarka', '260812'),
        ('shaver', '260812'),
        ('rasierer', '260812'),
        ('szczoteczka elektryczna', '260814'),
        ('electric toothbrush', '260814'),

        # === Odzież / Buty ===
        ('kurtka', '261300'),
        ('jacket', '261300'),
        ('jacke', '261300'),
        ('bluza', '261302'),
        ('hoodie', '261302'),
        ('sweter', '261304'),
        ('sweater', '261304'),
        ('pullover', '261304'),
        ('spodnie', '261306'),
        ('pants', '261306'),
        ('hose', '261306'),
        ('buty', '261310'),
        ('shoes', '261310'),
        ('schuhe', '261310'),
        ('sneakers', '261310'),
        ('sandały', '261312'),
        ('sandals', '261312'),
        ('kalosze', '261314'),
        ('boots', '261314'),
        ('stiefel', '261314'),
        ('rękawice', '261320'),
        ('gloves', '261320'),
        ('handschuhe', '261320'),
        ('czapka', '261322'),
        ('hat', '261322'),
        ('mütze', '261322'),
        ('szalik', '261324'),
        ('scarf', '261324'),

        # === Meble ===
        ('regał', '260440'),
        ('shelf', '260440'),
        ('regal', '260440'),
        ('komoda', '260442'),
        ('szafka', '260444'),
        ('cabinet', '260444'),
        ('schrank', '260444'),
        ('stolik', '260446'),
        ('table', '260446'),
        ('tisch', '260446'),
        ('sofa', '260448'),
        ('kanapa', '260448'),
        ('materac', '260460'),
        ('mattress', '260460'),
        ('matratze', '260460'),
        ('poduszka', '260462'),
        ('pillow', '260462'),
        ('kissen', '260462'),

        # === Narzędzia ręczne ===
        ('wkrętarka', '228055'),
        ('drill', '228055'),
        ('bohrmaschine', '228055'),
        ('zestaw narzędzi', '228001'),
        ('tool set', '228001'),
        ('werkzeugset', '228001'),
        ('klucz nasadowy', '228003'),
        ('socket set', '228003'),
        ('poziomica', '228005'),
        ('level', '228005'),
        ('miara', '228007'),
        ('tape measure', '228007'),
        ('szczypce', '228009'),
        ('pliers', '228009'),
        ('zange', '228009'),
        ('młotek', '228011'),
        ('hammer', '228011'),
        ('piła', '228013'),
        ('saw', '228013'),
        ('säge', '228013'),
        ('szlifierka', '228073'),
        ('sander', '228073'),

        # === RTV / Audio ===
        ('telewizor', '260240'),
        ('tv', '260240'),
        ('fernseher', '260240'),
        ('soundbar', '260242'),
        ('głośnik', '260244'),
        ('speaker', '260244'),
        ('lautsprecher', '260244'),
        ('bluetooth speaker', '260244'),
        ('projektor', '260246'),
        ('projector', '260246'),
        ('beamer', '260246'),
        ('kamera', '260248'),
        ('camera', '260248'),
        ('kamera sportowa', '260250'),
        ('action camera', '260250'),
        ('gopro', '260250'),
        ('dron', '260252'),
        ('drone', '260252'),
        ('drohne', '260252'),

        # === Drukarki / Skanery ===
        ('drukarka', '260280'),
        ('printer', '260280'),
        ('drucker', '260280'),
        ('skaner', '260282'),
        ('scanner', '260282'),
        ('toner', '260284'),
        ('tusz', '260286'),
        ('ink cartridge', '260286'),

        # === Huśtawki / Plac zabaw ===
        ('huśtawka', '124390'),
        ('swing', '124390'),
        ('schaukel', '124390'),
        ('zjeżdżalnia', '124392'),
        ('slide', '124392'),
        ('piaskownica', '124394'),
        ('sandbox', '124394'),
        ('brama', '124396'),
        ('gate', '124396'),
        ('tor', '124396'),
        ('bramka', '124396'),

        # === Rampy / Mobilność ===
        ('rampa', '261500'),
        ('ramp', '261500'),
        ('wózek inwalidzki', '261502'),
        ('wheelchair', '261502'),
        ('rollstuhl', '261502'),
        ('balkonik', '261504'),
        ('walker', '261504'),
        ('rollator', '261504'),
        ('kula', '261506'),
        ('crutch', '261506'),

        # === Biżuteria / Zegarki ===
        ('zegarek', '260900'),
        ('watch', '260900'),
        ('uhr', '260900'),
        ('bransoletka', '260902'),
        ('bracelet', '260902'),
        ('naszyjnik', '260904'),
        ('necklace', '260904'),
        ('kolczyki', '260906'),
        ('earrings', '260906'),
        ('pierścionek', '260908'),
        ('ring', '260908'),

        # === Walizki / Torby ===
        ('walizka', '261330'),
        ('suitcase', '261330'),
        ('koffer', '261330'),
        ('torba', '261332'),
        ('bag', '261332'),
        ('tasche', '261332'),

        # === Ogólne - na końcu (fallback) ===
        ('lampa', '260474'),  # Lampy ogólnie
        ('led', '258682'),
        ('light', '258682'),
    ]
    
    for keyword, cat_id in category_map:
        if keyword in nazwa_lower:
            return cat_id

    return '258682'


def clean_html_for_allegro(html):
    """
    Czyści HTML do formatu akceptowanego przez Allegro description sections.
    Dozwolone tagi w sekcjach: h1, h2, h3, p, ul, ol, li
    NIE dozwolone: b, strong, i, em, u, div, span, img, table, br, style
    """
    if not html:
        return ""

    # Zamień div na p
    html = re.sub(r'<div[^>]*>', '<p>', html, flags=re.IGNORECASE)
    html = re.sub(r'</div>', '</p>', html, flags=re.IGNORECASE)

    # Usuń tagi inline formatowania (zachowaj tekst wewnątrz)
    for tag in ['b', 'strong', 'i', 'em', 'u', 'span', 'font', 'a', 'small', 'big', 'sub', 'sup', 'mark']:
        html = re.sub(rf'<{tag}[^>]*>', '', html, flags=re.IGNORECASE)
        html = re.sub(rf'</{tag}>', '', html, flags=re.IGNORECASE)

    # Usuń tagi blokowe niedozwolone (cała zawartość)
    html = re.sub(r'<img[^>]*/?>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<table[^>]*>.*?</table>', '', html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r'<br\s*/?>', ' ', html, flags=re.IGNORECASE)

    # Usuń atrybuty style/class/id z dozwolonych tagów
    html = re.sub(r'style="[^"]*"', '', html, flags=re.IGNORECASE)
    html = re.sub(r"style='[^']*'", '', html, flags=re.IGNORECASE)
    html = re.sub(r'class="[^"]*"', '', html, flags=re.IGNORECASE)
    html = re.sub(r'id="[^"]*"', '', html, flags=re.IGNORECASE)

    # Wyczyść atrybuty z dozwolonych tagów (zostaw tylko czysty tag)
    for tag in ['p', 'h1', 'h2', 'h3', 'ul', 'ol', 'li']:
        html = re.sub(rf'<{tag}\s+[^>]*>', f'<{tag}>', html, flags=re.IGNORECASE)

    # Usuń puste paragrafy
    html = re.sub(r'<p>\s*</p>', '', html)

    # Usuń wielokrotne spacje
    html = re.sub(r'\s+', ' ', html)

    return html.strip()


def _map_stan_to_condition(stan):
    """Mapuje polski stan produktu na wartość Allegro condition.
    Dozwolone wartości Allegro REST API:
    NEW, USED, VERY_GOOD, GOOD, ACCEPTABLE, FOR_RENOVATION, REFURBISHED, NOT_APPLICABLE
    """
    _stan = (stan or 'Nowy').strip()
    mapping = {
        'Nowy': 'NEW',
        'nowy': 'NEW',
        'new': 'NEW',
        'Powystawowy': 'VERY_GOOD',
        'powystawowy': 'VERY_GOOD',
        'Używany': 'USED',
        'uzywany': 'USED',
        'Używany - bardzo dobry': 'VERY_GOOD',
        'Używany - dobry': 'GOOD',
        'Używany - akceptowalny': 'ACCEPTABLE',
        'Uszkodzony': 'FOR_RENOVATION',
        'uszkodzony': 'FOR_RENOVATION',
        'Na części': 'FOR_RENOVATION',
        'Odnowiony': 'REFURBISHED',
        'odnowiony': 'REFURBISHED',
    }
    return mapping.get(_stan, 'USED')


def _inject_stan_parameter(offer_data, stan, category_id):
    """
    Wstrzykuje parametr 'Stan' do offer_data['parameters'] na podstawie stanu magazynowego.
    Pobiera dostępne wartości z GET /sale/categories/{categoryId}/parameters.
    Szuka parametru po ID=11323 lub nazwie 'Stan'/'Stan produktu'.
    Nadpisuje wartość ustawioną przez AI (AI zawsze wpisuje 'Nowy').
    """
    if not stan or not category_id:
        return

    # Pobierz parametry kategorii (korzystamy z istniejącej funkcji)
    params_result, error = get_category_parameters(category_id)
    if error or not params_result:
        print(f"[STAN] Nie można pobrać parametrów kategorii {category_id}: {error}")
        return

    # Znajdź parametr Stan po ID 11323 lub nazwie
    stan_param = None
    for p in params_result.get('parameters', []):
        if str(p.get('id')) == '11323' or p.get('name', '').lower() in ('stan', 'stan produktu', 'condition'):
            stan_param = p
            break

    if not stan_param:
        print(f"[STAN] Brak parametru Stan w kategorii {category_id}")
        return

    stan_param_id = str(stan_param['id'])
    dictionary = stan_param.get('dictionary', [])
    if not dictionary:
        print(f"[STAN] Parametr Stan nie ma słownika")
        return

    # Mapowanie wewnętrznych nazw → szukane frazy w słowniku Allegro
    _search_map = {
        'Nowy':                ['nowy'],
        'Powystawowy':         ['powystawowy', 'powystawiony', 'bardzo dobry'],
        'Używany':             ['używany', 'dobry'],
        'Używany - dobry':     ['dobry', 'używany'],
        'Używany - akceptowalny': ['akceptowalny', 'używany'],
        'Uszkodzony':          ['do renowacji', 'na części', 'uszkodzony', 'uszkodz'],
        'Na części':           ['na części', 'do renowacji'],
        'Odnowiony':           ['odnowiony', 'refurbished'],
    }
    search_terms = _search_map.get(stan, [stan.lower()])

    found_value_id = None
    for dict_val in dictionary:
        val_name = dict_val.get('value', '').lower()
        for term in search_terms:
            if term in val_name or val_name in term:
                found_value_id = str(dict_val['id'])
                print(f"[STAN] {stan!r} → '{dict_val['value']}' (id={found_value_id})")
                break
        if found_value_id:
            break

    # Fallback: pierwsza wartość słownika (zazwyczaj "Nowy")
    if not found_value_id and dictionary:
        found_value_id = str(dictionary[0]['id'])
        print(f"[STAN] Fallback → '{dictionary[0].get('value')}' (id={found_value_id})")

    if not found_value_id:
        return

    # Wstrzyknij/nadpisz w offer_data['parameters']
    current_params = offer_data.get('parameters', [])
    # Usuń poprzedni Stan (ustawiony przez AI)
    current_params = [p for p in current_params if str(p.get('id')) != stan_param_id]
    # Wstaw Stan na początku (wymagany parametr)
    current_params.insert(0, {'id': stan_param_id, 'valuesIds': [found_value_id]})
    offer_data['parameters'] = current_params
    print(f"[STAN] Parametr Stan ({stan_param_id}) ustawiony: valuesIds=[{found_value_id}]")


def update_offer_condition(offer_id, stan):
    """Ustawia stan produktu (condition) na istniejącej ofercie przez PATCH"""
    condition_value = _map_stan_to_condition(stan)
    print(f"ℹ Stan oferty {offer_id}: {condition_value} (ustaw ręcznie w Sales Center)")
    return None


def create_offer(nazwa, opis, cena, zdjecia_urls=None, kategoria_id=None, ilosc=1, czas_wysylki='PT24H', ean=None, asin=None, gpsr=None, stan=None, product_specs=None, bullet_points=None, kod_magazynowy=None):
    """
    Tworzy nową ofertę na Allegro.
    WERSJA 2.0 - poprawiona obsługa zdjęć + GPSR
    """
    import io
    import sys

    # Przechwytuj logi
    old_stdout = sys.stdout
    sys.stdout = log_capture = io.StringIO()

    try:
        return _create_offer_impl(nazwa, opis, cena, zdjecia_urls, kategoria_id, ilosc, czas_wysylki, ean, asin, gpsr, stan, product_specs=product_specs, bullet_points=bullet_points, kod_magazynowy=kod_magazynowy)
    finally:
        logs = log_capture.getvalue()
        sys.stdout = old_stdout
        
        if logs:
            try:
                with open('logs/allegro_upload.log', 'a', encoding='utf-8') as f:
                    f.write(f"\n{'='*50}\n")
                    f.write(f"[{datetime.now()}] Tworzenie oferty: {nazwa[:40]}\n")
                    f.write(logs)
            except:
                pass
            print(logs)


def _create_offer_impl(nazwa, opis, cena, zdjecia_urls=None, kategoria_id=None, ilosc=1, czas_wysylki='PT24H', ean=None, asin=None, gpsr=None, stan=None, product_specs=None, bullet_points=None, kod_magazynowy=None):
    """Implementacja tworzenia oferty"""
    if not is_authenticated():
        return None, "Nie zalogowany do Allegro"
    
    config = get_allegro_config()
    
    # Sprawdź cennik wysyłki
    shipping_id = config.get('shipping_id', '')
    if not shipping_id:
        return None, "BRAK CENNIKA WYSYŁKI! Wejdź w Allegro → Ustawienia i wybierz cennik."
    
    # Auto-wykryj kategorię jeśli nie podano
    print(f"[FOLD] Received kategoria_id: '{kategoria_id}' (type: {type(kategoria_id).__name__})")
    
    if not kategoria_id:
        # === PRIORYTET 1: Allegro API matching (najdokładniejsze) ===
        try:
            import re as _re_cat
            _nazwa_clean = _re_cat.sub(r'\b[A-Z0-9]{5,}\b', '', nazwa)  # Usuń kody ASIN
            _nazwa_clean = _re_cat.sub(r'\b\d{4,}\b', '', _nazwa_clean)  # Usuń długie numery
            _nazwa_clean = _re_cat.sub(r'\s+', ' ', _nazwa_clean).strip()
            _search_name = _nazwa_clean[:50] if _nazwa_clean else nazwa[:50]
            print(f"[FOLD] Category search query: '{_search_name}'")
            cat_result, _cat_err = search_categories(_search_name)
            if cat_result and cat_result.get('matchingCategories'):
                _matches = cat_result['matchingCategories']
                # Filtruj: tylko leaf categories (bez dzieci)
                _leaf_matches = [m for m in _matches if m.get('leaf', True)]
                _display = _leaf_matches[:5] if _leaf_matches else _matches[:5]
                for _m in _display:
                    print(f"[FOLD]   Match: {_m.get('id')} - {_m.get('name', '?')} (leaf={_m.get('leaf','?')})")
                if _leaf_matches:
                    kategoria_id = _leaf_matches[0].get('id')
                    print(f"[FOLD] Auto-category from API (leaf): {kategoria_id}")
                elif _matches:
                    kategoria_id = _matches[0].get('id')
                    print(f"[FOLD] Auto-category from API (first): {kategoria_id}")
            elif _cat_err:
                print(f"[WARN] Category API returned error: {_cat_err}")
        except Exception as e:
            print(f"[WARN] Category API error: {e}")

        # === PRIORYTET 2: Lokalne dopasowanie (fallback) ===
        if not kategoria_id:
            _local_cat = detect_category_id(nazwa)
            if _local_cat != '258682':
                kategoria_id = _local_cat
                print(f"[FOLD] Local category fallback: {kategoria_id}")
            else:
                kategoria_id = '258682'
                print(f"[FOLD] Default category (no match): {kategoria_id}")
    
    print(f"[FOLD] Final category ID: {kategoria_id}")
    
    # === EAN i ASIN (info) ===
    ean_clean = None
    if ean:
        ean_clean = str(ean).strip().replace(' ', '').replace('-', '')
        if not (ean_clean.isdigit() and len(ean_clean) in [8, 12, 13, 14]):
            print(f"[WARN] Invalid EAN format: {ean_clean}")
            ean_clean = None
    
    # Przygotuj opis HTML
    opis_html = clean_html_for_allegro(opis) if opis else ""
    print(f"[EDIT] Opis input: {len(opis)} chars -> cleaned: {len(opis_html)} chars")
    
    # === PAYLOAD OFERTY ===
    
    _condition = _map_stan_to_condition(stan)
    print(f"[COND] Stan: {stan!r} → condition: {_condition} (ustawiane po wystawieniu przez PATCH)")

    offer_data = {
        'name': nazwa[:75],
        'category': {'id': str(kategoria_id)},
        'sellingMode': {
            'format': 'BUY_NOW',
            'price': {'amount': f"{float(cena):.2f}", 'currency': 'PLN'}
        },
        'stock': {'available': int(ilosc)},
        'publication': {'status': 'INACTIVE'},
        'location': {
            'countryCode': 'PL',
            'province': config.get('province', 'WIELKOPOLSKIE'),
            'city': config.get('city', 'Poznan'),
            'postCode': config.get('postcode', '61-001')
        },
        'delivery': {
            'handlingTime': czas_wysylki,
            'shippingRates': {'id': shipping_id}
        }
    }
    
    # External ID = ASIN / KOD_MAGAZYNOWY (sygnatura oferty)
    _ext_id_parts = []
    if asin:
        _ext_id_parts.append(str(asin))
    if kod_magazynowy:
        _ext_id_parts.append(str(kod_magazynowy))
    if _ext_id_parts:
        offer_data['external'] = {'id': ' / '.join(_ext_id_parts)}
        print(f"[SELL] external.id (sygnatura): {offer_data['external']['id']}")
    
    # === EAN - będzie dodany jako parametr GTIN ===
    if ean_clean:
        print(f"[BAR_] EAN: {ean_clean} (będzie dodany jako parametr GTIN)")
    
    # === RÓWNOLEGŁY: AI parametry + GPSR upload + producent/osoba ===
    from concurrent.futures import ThreadPoolExecutor, as_completed
    product_params = []
    _gpsr_ps_entry = {}
    _product_ps_entry = {}
    _gpsr_attachment_id = None

    skip_auto_params = get_config('skip_allegro_auto_params', 'false')
    gemini_key = get_config('gemini_api_key', '')

    def _task_ai_params():
        """AI parametry kategorii (Gemini call ~10-20s)"""
        if skip_auto_params.lower() in ('true', '1', 'yes', 'tak'):
            return None
        try:
            return build_offer_parameters_ai(
                category_id=kategoria_id, product_name=nazwa, description=opis,
                ean=ean_clean, asin=asin, gemini_key=gemini_key, product_specs=product_specs
            )
        except Exception as e:
            print(f"[ERR] AI params: {e}")
            return None

    def _task_gpsr_upload():
        """Upload GPSR PDF (~2-5s)"""
        if not gpsr:
            return None
        return upload_gpsr_attachment(gpsr, nazwa)

    def _task_gpsr_producer():
        """Pobierz producenta z Allegro (~1s)"""
        if not gpsr:
            return None
        try:
            producers = get_responsible_producers()
            return producers[0] if producers else None
        except:
            return None

    def _task_gpsr_person():
        """Pobierz osobę odpowiedzialną (~1s)"""
        if not gpsr:
            return None
        try:
            persons = get_responsible_persons()
            return persons[0] if persons else None
        except:
            return None

    # Odpal WSZYSTKO równolegle (zamiast sekwencyjnie ~25s → ~15s)
    print(f"[BOLT] Równoległy start: AI params + GPSR upload + producent/osoba...")
    with ThreadPoolExecutor(max_workers=4) as executor:
        f_params = executor.submit(_task_ai_params)
        f_gpsr_upload = executor.submit(_task_gpsr_upload)
        f_producer = executor.submit(_task_gpsr_producer)
        f_person = executor.submit(_task_gpsr_person)

        # Zbierz wyniki
        params_result = f_params.result()
        _gpsr_attachment_id = f_gpsr_upload.result()
        _producer = f_producer.result()
        _person = f_person.result()

    # Przetwórz wyniki AI parametrów
    if params_result:
        if isinstance(params_result, dict) and 'offer' in params_result:
            offer_params = params_result.get('offer', [])
            product_params = params_result.get('product', [])
            if offer_params:
                offer_data['parameters'] = offer_params
                print(f"[OK] {len(offer_params)} OFFER params + {len(product_params)} PRODUCT params")
        elif isinstance(params_result, list) and params_result:
            offer_data['parameters'] = params_result
            print(f"[OK] {len(params_result)} params (legacy)")

    # Wstrzyknij parametr Stan (11323) — nadpisuje wartość AI, używa rzeczywistego stanu magazynowego
    # Stan jest parametrem oferty (nie produktu), więc musi być w offer_data['parameters']
    if stan:
        _inject_stan_parameter(offer_data, stan, kategoria_id)

    # Zbuduj GPSR productSet entry — ZAWSZE jako tekst (MANUAL), PDF attachment jako bonus
    if gpsr:
        # Główna metoda: tekst TEXT (Allegro API wspiera type TEXT + description)
        _gpsr_ps_entry['safetyInformation'] = {'type': 'TEXT', 'description': gpsr[:5000]}
        _gpsr_ps_entry['marketedBeforeGPSRObligation'] = False
        print(f"[OK] GPSR: {len(gpsr)} znaków (MANUAL text)")
        # Jeśli PDF upload się udał — nadpisz na ATTACHMENTS (lepiej wygląda)
        if _gpsr_attachment_id:
            _gpsr_ps_entry['safetyInformation'] = {'type': 'ATTACHMENTS', 'attachments': [{'id': _gpsr_attachment_id}]}
            print(f"[OK] GPSR PDF: attachment {_gpsr_attachment_id} (upgrade z MANUAL)")
        if _producer:
            _gpsr_ps_entry['responsibleProducer'] = {'type': 'ID', 'id': _producer['id']}
            print(f"[OK] Producent: {_producer.get('name', '?')}")
        if _person:
            _gpsr_ps_entry['responsiblePerson'] = {'id': _person['id']}
            print(f"[OK] Osoba: {_person.get('name', '?')}")

    # EAN/product params -> PATCH
    if product_params:
        _product_images = []
        if zdjecia_urls:
            _product_images = zdjecia_urls[:1]
        elif offer_data.get('images'):
            _product_images = [offer_data['images'][0]] if offer_data['images'] else []

        _product_ps_entry = {
            'product': {
                'name': nazwa[:50],
                'category': {'id': str(kategoria_id)},
                'parameters': product_params,
                'images': _product_images
            }
        }
        _pp_ids = [p.get('id') for p in product_params]
        print(f"Product params (EAN/GTIN): {_pp_ids} -> PATCH po utworzeniu")

    # === productSet do POST: EAN + GPSR w jednym strzale ===
    _combined_ps = {}
    if _product_ps_entry:
        _combined_ps.update(_product_ps_entry)
    if _gpsr_ps_entry:
        _combined_ps.update(_gpsr_ps_entry)
    if _combined_ps:
        offer_data['productSet'] = [_combined_ps]
        print(f"productSet w POST: {list(_combined_ps.keys())}")

    # Opis - będzie uzupełniony o zdjęcia po uploadzie
    opis_html_clean = opis_html if opis_html else ''
    
    # Allegro wymaga &amp; zamiast & w HTML - escapuj surowe ampersandy
    # (nie podwajaj już istniejących entities jak &amp; &nbsp; &lt; &gt; itd.)
    if opis_html_clean:
        opis_html_clean = re.sub(r'&(?!amp;|nbsp;|quot;|apos;|lt;|gt;|#\d+;|#x[0-9a-fA-F]+;)', '&amp;', opis_html_clean)
    
    # ============================================================
    # ZDJĘCIA - RÓWNOLEGŁY UPLOAD (ThreadPoolExecutor)
    # ============================================================
    uploaded_images = []

    if zdjecia_urls:

        # Przygotuj listę URL do uploadu
        urls_to_process = []
        for url in zdjecia_urls[:8]:
            if url and isinstance(url, str) and url.strip():
                urls_to_process.append(url.strip())

        print(f"[PHOT] Processing {len(urls_to_process)} images (parallel)...")

        def _upload_single(idx_url):
            idx, url = idx_url
            try:
                if is_allegro_url(url):
                    print(f"  [{idx+1}] Already on Allegro")
                    return (idx, url)
                allegro_url = upload_image_to_allegro(url)
                if allegro_url:
                    print(f"  [{idx+1}] Uploaded OK")
                    return (idx, allegro_url)
                else:
                    print(f"  [{idx+1}] Upload failed")
                    return (idx, None)
            except Exception as e:
                print(f"  [{idx+1}] Error: {e}")
                return (idx, None)

        # Upload max 4 naraz (Allegro rate limit)
        results = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_upload_single, (i, u)): i for i, u in enumerate(urls_to_process)}
            for future in as_completed(futures):
                results.append(future.result())

        # Sortuj po oryginalnej kolejności
        results.sort(key=lambda x: x[0])
        uploaded_images = [url for _, url in results if url]

        print(f"[PHOT] Result: {len(uploaded_images)}/{len(urls_to_process)} images ready")

        if uploaded_images:
            print(f"[PHOT] Images: {len(uploaded_images)} URLs ready for description sections")

    # === GALERIA ZDJĘĆ OFERTY (osobne od description sections!) ===
    if uploaded_images:
        offer_data['images'] = uploaded_images[:16]
        print(f"[PHOT] Gallery images added to offer: {len(offer_data['images'])}")

    # ============================================================
    # OPIS Z OBRAZKAMI - ŁADNY LAYOUT (zdjęcia przeplatane z tekstem)
    # ============================================================
    # ============================================================
    # BUDOWANIE SEKCJI OPISU - PROSTA LOGIKA
    # Tytuł RAZ na górze, każde zdjęcie RAZ, cały opis bez ucinania
    # ============================================================
    sections = []

    # === SEKCJA 0: Tytuł <h2> na górze ===
    _h2_nazwa = nazwa[:75].replace('&', '&amp;')
    sections.append({
        'items': [{'type': 'TEXT', 'content': f'<h2>{_h2_nazwa}</h2>'}]
    })

    # === Bullet points HTML ===
    bp_html = ''
    if bullet_points and isinstance(bullet_points, list) and len(bullet_points) > 0:
        bp_items = [f'<li>{bp}</li>' for bp in bullet_points[:8]]
        bp_html = '<ul>' + ''.join(bp_items) + '</ul>'

    # === Parsuj paragrafy z opisu (pomiń tytuł - już jest w sekcji 0) ===
    paragraphs = []
    if opis_html_clean:
        all_paragraphs = re.findall(r'<p>(.*?)</p>', opis_html_clean, re.DOTALL)
        # Pomiń pierwszy paragraf jeśli to bold tytuł
        if all_paragraphs and '<b>' in all_paragraphs[0][:10]:
            paragraphs = all_paragraphs[1:]
        else:
            paragraphs = list(all_paragraphs)

        # FALLBACK: jeśli regex nie znalazł <p>, użyj całego opisu jako 1 paragraf
        if not paragraphs and len(opis_html_clean.strip()) > 10:
            print(f"OPIS FALLBACK: brak <p> tagow, uzyj calego opisu ({len(opis_html_clean)} chars)")
            # Owin w <p> jeśli nie ma żadnych tagów blokowych
            _stripped = opis_html_clean.strip()
            if not _stripped.startswith('<'):
                _stripped = f'<p>{_stripped}</p>'
            paragraphs = [_stripped]  # Cały opis jako 1 "paragraf" (już z tagami)

    # === Podziel paragrafy na chunki (po 2-3 paragrafy) do parowania ze zdjęciami ===
    # Ile zdjęć mamy na tekst? (image[0] idzie z bullet_points, reszta z opisem)
    text_images = max(0, min(len(uploaded_images), 4) - 1)  # max 3 zdjęcia z tekstem
    if not text_images or not paragraphs:
        # Brak zdjęć na tekst lub brak paragrafów → 1 duży chunk
        # Jeśli fallback (paragraf już ma tagi) → nie owijaj ponownie w <p>
        _has_tags = any('<' in p for p in paragraphs)
        chunks = [''.join(p if _has_tags else f'<p>{p}</p>' for p in paragraphs)] if paragraphs else []
    else:
        # Podziel paragrafy równo na tyle chunków ile mamy zdjęć
        chunk_size = max(2, len(paragraphs) // text_images)
        chunks = []
        for i in range(0, len(paragraphs), chunk_size):
            chunk = ''.join(f'<p>{p}</p>' for p in paragraphs[i:i+chunk_size])
            chunks.append(chunk)

    # === BUDOWANIE SEKCJI ===
    img_idx = 0

    if uploaded_images:
        # SEKCJA 1: Zdjęcie[0] + pierwszy chunk opisu AI (nie bullet points z Amazona)
        if chunks:
            sections.append({
                'items': [
                    {'type': 'IMAGE', 'url': uploaded_images[0]},
                    {'type': 'TEXT', 'content': chunks.pop(0)}
                ]
            })
        else:
            sections.append({
                'items': [{'type': 'IMAGE', 'url': uploaded_images[0]}]
            })
        img_idx = 1

        # SEKCJE 2-4: Zdjęcia[1..3] + chunki opisu
        chunk_idx = 0
        while img_idx < min(len(uploaded_images), 4) and chunk_idx < len(chunks):
            sections.append({
                'items': [
                    {'type': 'IMAGE', 'url': uploaded_images[img_idx]},
                    {'type': 'TEXT', 'content': chunks[chunk_idx]}
                ]
            })
            img_idx += 1
            chunk_idx += 1

        # Pozostały tekst (bez zdjęć)
        if chunk_idx < len(chunks):
            remaining = ''.join(chunks[chunk_idx:])
            sections.append({
                'items': [{'type': 'TEXT', 'content': remaining}]
            })

        # Pozostałe zdjęcia (po 2, bez tekstu) - max 8 zdjęć łącznie w opisie
        while img_idx < min(len(uploaded_images), 8):
            img_items = [{'type': 'IMAGE', 'url': uploaded_images[img_idx]}]
            img_idx += 1
            if img_idx < min(len(uploaded_images), 8):
                img_items.append({'type': 'IMAGE', 'url': uploaded_images[img_idx]})
                img_idx += 1
            sections.append({'items': img_items})

    elif chunks:
        # Brak zdjęć - cały tekst w jednej sekcji
        sections.append({
            'items': [{'type': 'TEXT', 'content': ''.join(chunks)}]
        })

    elif uploaded_images:
        # Brak tekstu - tylko zdjęcia
        for i in range(0, min(len(uploaded_images), 8), 2):
            img_items = [{'type': 'IMAGE', 'url': uploaded_images[i]}]
            if i + 1 < len(uploaded_images):
                img_items.append({'type': 'IMAGE', 'url': uploaded_images[i + 1]})
            sections.append({'items': img_items})
    
    # SAFETY NET: jeśli sections nie zawiera TEXT a mamy opis → dodaj go
    has_text_section = any(
        item.get('type') == 'TEXT'
        for s in sections for item in s.get('items', [])
    )
    if not has_text_section and opis_html_clean and len(opis_html_clean.strip()) > 10:
        _fallback_content = opis_html_clean.strip()
        if not _fallback_content.startswith('<'):
            _fallback_content = f'<p>{_fallback_content}</p>'
        sections.append({'items': [{'type': 'TEXT', 'content': _fallback_content}]})
        print(f"OPIS SAFETY NET: dodano caly opis jako ostatnia sekcja ({len(_fallback_content)} chars)")

    if sections:
        offer_data['description'] = {'sections': sections}
        print(f"[EDIT] Description: {len(sections)} sections (mixed layout)")

    # Wyślij ofertę
    print(f"[UPLO] Sending offer: {nazwa[:40]}...")
    print(f"[PHOT] Images in payload: {len(uploaded_images)}")
    
    # DEBUG: Pokaż pełny payload
    if uploaded_images:
        print(f"[PHOT] Image URLs being sent:")
        for i, img in enumerate(uploaded_images):
            print(f"   [{i+1}] {img[:80]}...")
        print(f"[PHOT] Images structure: {offer_data.get('images', 'NONE')}")
    
    import json as _json
    print(f"[INVE] FULL PAYLOAD KEYS: {list(offer_data.keys())}")
    print(f"[INVE] payments: {offer_data.get('payments')}")
    print(f"[INVE] delivery keys: {list(offer_data.get('delivery', {}).keys())}")
    result, error = allegro_request('POST', '/sale/product-offers', data=offer_data)

    # Retry: usuń problematyczne parametry — szuka w OBIE listy (offer + product)
    import re as _re
    retry_count = 0
    _has_any_params = lambda: ('parameters' in offer_data or
        (offer_data.get('productSet') and isinstance(offer_data['productSet'], list) and
         offer_data['productSet'][0].get('product', {}).get('parameters')))

    # EXPLICIT retry logging
    _offer_param_ids = [str(p.get('id')) for p in offer_data.get('parameters', [])]
    print(f"[SYNC] RETRY CHECK: error={bool(error)}, offer_params={_offer_param_ids}, retry_count={retry_count}")

    while error and ('parameters' in offer_data) and retry_count < 8:
        error_str = str(error)
        print(f"[SYNC] RETRY LOOP #{retry_count}: error='{error_str[:80]}'")

        # Wyciągnij ID problematycznego parametru z błędu
        bad_param = _re.search(r'Parameter\s*`?(\d+)', error_str)
        print(f"[SYNC] Regex match: {bad_param.group(0) if bad_param else 'NO MATCH'}")

        if bad_param:
            bad_id = bad_param.group(1)
            old_len = len(offer_data.get('parameters', []))
            offer_data['parameters'] = [p for p in offer_data.get('parameters', []) if str(p.get('id')) != bad_id]
            new_len = len(offer_data['parameters'])
            print(f"[SYNC] Usunięto param {bad_id}: {old_len} → {new_len}")

            if new_len < old_len:
                if not offer_data['parameters']:
                    del offer_data['parameters']
                    print(f"[SYNC] Brak parametrów — usunięto klucz 'parameters'")
                result, error = allegro_request('POST', '/sale/product-offers', data=offer_data)
                retry_count += 1
                if not error:
                    print(f"[OK] Retry #{retry_count} SUKCES!")
                continue
            else:
                print(f"[WARN] Param {bad_id} NIE znaleziony w offer params {_offer_param_ids}")
                break

        elif 'should not be specified' in error_str.lower():
            print(f"[WARN] Nie sparsowano ID — usuwam WSZYSTKIE parametry")
            offer_data.pop('parameters', None)
            result, error = allegro_request('POST', '/sale/product-offers', data=offer_data)
            break
        else:
            print(f"[SYNC] Error nie dotyczy parametrów — break")
            break

    # Retry productSet errors - zamiast usuwać cały productSet, zachowaj GPSR
    if error and 'productSet' in offer_data:
        error_str = str(error).lower()
        if any(x in error_str for x in ['productsafety', 'safety', 'productset', 'responsibleproducer', 'nie można stworzyć produktu']):
            print(f"productSet blokuje oferte - retry z productSet BEZ product (zachowaj GPSR)")
            saved_ps = offer_data.get('productSet', [])

            # Zachowaj GPSR ale usuń problematyczny 'product' z productSet
            if saved_ps and isinstance(saved_ps, list) and len(saved_ps) > 0:
                gpsr_only_ps = {}
                for key in ['safetyInformation', 'responsibleProducer', 'responsiblePerson', 'quantity', 'marketedBeforeGPSRObligation']:
                    if key in saved_ps[0]:
                        gpsr_only_ps[key] = saved_ps[0][key]

                if gpsr_only_ps:
                    offer_data['productSet'] = [gpsr_only_ps]
                    print(f"productSet retry keys: {list(gpsr_only_ps.keys())}")
                    result, error = allegro_request('POST', '/sale/product-offers', data=offer_data)

                    if error:
                        print(f"productSet retry error: {error} -> usuwam productSet calkowicie")
                        offer_data.pop('productSet', None)
                        result, error = allegro_request('POST', '/sale/product-offers', data=offer_data)
                else:
                    offer_data.pop('productSet', None)
                    result, error = allegro_request('POST', '/sale/product-offers', data=offer_data)
            else:
                offer_data.pop('productSet', None)
                result, error = allegro_request('POST', '/sale/product-offers', data=offer_data)

    if error:
        print(f"Allegro error: {error}")
        return result, error

    offer_id = result.get('id', '')
    params_count = len(offer_data.get('parameters', []))
    print(f"Offer created: {offer_id} | params: {params_count}")

    import time as _time
    import json as _json2

    # Zdjęcia galerii — w description.sections + 1 z productSet
    if offer_id and uploaded_images:
        print(f"[PHOT] {len(uploaded_images)} zdjęć w description sections")

    # === KROK 1.5: PATCH description + images (bo POST może je ignorować) ===
    if offer_id:
        _desc_patch = {}
        if offer_data.get('description'):
            _desc_patch['description'] = offer_data['description']
        if offer_data.get('images'):
            _desc_patch['images'] = offer_data['images']
        if _desc_patch:
            print(f"[EDIT] PATCH description+images na {offer_id}...")
            _dp_result, _dp_error = allegro_request('PATCH', f'/sale/product-offers/{offer_id}', data=_desc_patch)
            if _dp_error:
                print(f"[EDIT] PATCH description error: {_dp_error}")
                # Spróbuj osobno description i images
                if offer_data.get('description'):
                    _d2, _e2 = allegro_request('PATCH', f'/sale/product-offers/{offer_id}', data={'description': offer_data['description']})
                    print(f"[EDIT] PATCH description only: {'OK' if not _e2 else _e2}")
                if offer_data.get('images'):
                    _d3, _e3 = allegro_request('PATCH', f'/sale/product-offers/{offer_id}', data={'images': offer_data['images']})
                    print(f"[PHOT] PATCH images only: {'OK' if not _e3 else _e3}")
            else:
                print(f"[EDIT] PATCH description+images OK!")

    # === KROK 2: GPSR fallback (tylko jeśli POST nie zapisał) ===
    if offer_id and gpsr and _gpsr_ps_entry:
        # Szybka weryfikacja — bez sleep, Allegro przetwarza synchronicznie
        verify_result, verify_error = allegro_request('GET', f'/sale/product-offers/{offer_id}')
        gpsr_saved = False
        if not verify_error and verify_result:
            for ps_item in verify_result.get('productSet', []):
                sv = ps_item.get('safetyInformation')
                if sv and sv != 'None' and isinstance(sv, dict) and (sv.get('type') or sv.get('description')):
                    gpsr_saved = True
                    print(f"[OK] GPSR saved via POST!")
                    break

        # Fallback: PATCH z GPSR jeśli POST nie zapisał
        if not gpsr_saved:
            print(f"[WARN] GPSR nie w POST → PATCH fallback...")
            _gpsr_patch = dict(_gpsr_ps_entry)  # kopia z producent + osoba
            # ZAWSZE tekst TEXT (niezawodne, Allegro API oficjalnie wspiera)
            _gpsr_patch['safetyInformation'] = {'type': 'TEXT', 'description': gpsr[:5000]}
            _gpsr_patch['marketedBeforeGPSRObligation'] = False

            patch_data = {'productSet': [_gpsr_patch]}
            if offer_data.get('description'):
                patch_data['description'] = offer_data['description']
            if offer_data.get('images'):
                patch_data['images'] = offer_data['images']

            patch_result, patch_error = allegro_request('PATCH', f'/sale/product-offers/{offer_id}', data=patch_data)
            if patch_error:
                print(f"GPSR PATCH error: {patch_error}")
                # Ostatnia próba: modification-commands (bez sleep)
                import uuid as _uuid
                offer_criteria = [{'offers': [{'id': offer_id}], 'type': 'CONTAINS_OFFERS'}]
                if _gpsr_attachment_id:
                    cmd_id = str(_uuid.uuid4())
                    allegro_request('PUT', f'/sale/offer-modification-commands/{cmd_id}', data={
                        'modification': {'safetyInformation': {'type': 'ATTACHMENTS', 'attachments': [{'id': _gpsr_attachment_id}]}},
                        'offerCriteria': offer_criteria
                    })
                    print(f"GPSR modification-command sent")
            else:
                print(f"[OK] GPSR PATCH OK!")

    # EAN PATCH jeśli nie był w POST
    _ean_was_in_post = False
    if 'productSet' in offer_data and offer_data['productSet']:
        _ean_was_in_post = 'product' in offer_data['productSet'][0]

    if _product_ps_entry and offer_id and not _ean_was_in_post:
        print(f"EAN PATCH...")
        ean_patch = {'productSet': [_product_ps_entry]}
        if offer_data.get('description'):
            ean_patch['description'] = offer_data['description']
        if offer_data.get('images'):
            ean_patch['images'] = offer_data['images']
        ean_r, ean_e = allegro_request('PATCH', f'/sale/product-offers/{offer_id}', data=ean_patch)
        print(f"EAN PATCH: {'OK' if not ean_e else ean_e}")

    return result, error


def get_responsible_persons():
    """Pobiera listę osób odpowiedzialnych (GPSR) z Allegro"""
    result, error = allegro_request('GET', '/sale/responsible-persons')
    if error:
        print(f"[WARN] GET responsible-persons error: {error}")
        return []
    persons = result.get('responsiblePersons', [])
    print(f"[PERS] Responsible persons: {len(persons)}")
    for p in persons:
        print(f"   - {p.get('id', '?')}: {p.get('name', '?')} ({p.get('address', {}).get('city', '?')})")
    return persons


def get_responsible_producers():
    """Pobiera listę producentów (GPSR) z Allegro"""
    result, error = allegro_request('GET', '/sale/responsible-producers')
    if error:
        print(f"[WARN] GET responsible-producers error: {error}")
        return []
    producers = result.get('responsibleProducers', [])
    print(f"[FACT] Responsible producers: {len(producers)}")
    for p in producers:
        print(f"   - {p.get('id', '?')}: {p.get('name', '?')}")
    return producers


def upload_gpsr_attachment(gpsr_text, product_name=''):
    """
    Generuje PDF z tekstu GPSR i wgrywa jako attachment do Allegro.

    Allegro API wymaga GPSR jako plik (PDF/JPG/PNG) - typ "ATTACHMENTS".
    Typ "MANUAL"/"TEXT" NIE JEST obsługiwany przez API!

    Args:
        gpsr_text: Tekst GPSR do wgrania
        product_name: Nazwa produktu (do tytułu PDF)

    Returns:
        str: attachment_id lub None jeśli upload się nie powiódł
    """
    if not gpsr_text or not is_authenticated():
        return None

    try:
        # Próba 1: ReportLab PDF (jeśli zainstalowane)
        pdf_bytes = None
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.units import mm
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
            from reportlab.lib.styles import ParagraphStyle

            pdf_buffer = BytesIO()
            doc = SimpleDocTemplate(pdf_buffer, pagesize=A4,
                                    topMargin=20*mm, bottomMargin=20*mm,
                                    leftMargin=20*mm, rightMargin=20*mm)

            styles = getSampleStyleSheet()
            title_style = ParagraphStyle('GPSRTitle', parent=styles['Heading1'],
                fontSize=14, spaceAfter=10, fontName='Helvetica-Bold')
            body_style = ParagraphStyle('GPSRBody', parent=styles['Normal'],
                fontSize=10, leading=14, spaceAfter=6, fontName='Helvetica')

            story = []
            story.append(Paragraph("Informacje o bezpieczeństwie produktu (GPSR)", title_style))
            if product_name:
                story.append(Paragraph(f"Produkt: {product_name[:100]}", body_style))
            story.append(Spacer(1, 10))

            for line in gpsr_text.split('\n'):
                line = line.strip()
                if not line:
                    story.append(Spacer(1, 6))
                    continue
                line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                if line.startswith('* '):
                    line = '• ' + line[2:]
                story.append(Paragraph(line, body_style))

            doc.build(story)
            pdf_bytes = pdf_buffer.getvalue()
            pdf_buffer.close()
            print(f"[SHIE] GPSR PDF (reportlab): {len(pdf_bytes)} bytes")
        except ImportError:
            print(f"[WARN] reportlab nie zainstalowane — generuję minimalny PDF")

        # Próba 2: Minimalny PDF bez reportlab (ręczny format)
        if not pdf_bytes:
            # Generuj najprostszy możliwy PDF
            _lines = gpsr_text.replace('* ', '• ').split('\n')
            _text_content = '\n'.join(l.strip() for l in _lines if l.strip())
            # Minimalny valid PDF z tekstem
            _content = f"Informacje o bezpieczeństwie produktu (GPSR)\n\nProdukt: {product_name[:100]}\n\n{_text_content}"
            _stream = _content.encode('latin-1', errors='replace')
            _pdf = (
                b'%PDF-1.4\n'
                b'1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n'
                b'2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n'
                b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]'
                b'/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n'
                b'5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n'
                b'4 0 obj<</Length ' + str(len(_stream) + 50).encode() + b'>>\nstream\n'
                b'BT /F1 10 Tf 50 800 Td (' + _stream[:800] + b') Tj ET\n'
                b'endstream\nendobj\n'
                b'xref\n0 6\n'
                b'0000000000 65535 f \n'
                b'trailer<</Size 6/Root 1 0 R>>\nstartxref\n0\n%%EOF'
            )
            pdf_bytes = _pdf
            print(f"[SHIE] GPSR PDF (minimal): {len(pdf_bytes)} bytes")

        # Upload do Allegro
        token = get_config('allegro_access_token', '')
        env = get_config('allegro_environment', 'sandbox')
        api_base = 'https://api.allegro.pl' if env == 'production' else 'https://api.allegro.pl.allegrosandbox.pl'

        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.allegro.public.v1+json',
            'Content-Type': 'multipart/form-data',
        }
        # requests wymaga usunięcia Content-Type żeby sam ustawił boundary
        del headers['Content-Type']

        filename = 'gpsr_safety_info.pdf'
        files = {
            'file': (filename, BytesIO(pdf_bytes), 'application/pdf')
        }
        data = {
            'type': 'SAFETY_INFORMATION_MANUAL'
        }

        resp = requests.post(
            f'{api_base}/sale/offer-attachments',
            headers=headers,
            files=files,
            data=data,
            timeout=60
        )

        if resp.status_code in [200, 201]:
            result = resp.json()
            attachment_id = result.get('id', '')
            print(f"[SHIE] GPSR attachment uploaded: {attachment_id}")
            return attachment_id
        else:
            print(f"[SHIE] GPSR upload error: {resp.status_code}: {resp.text[:300]}")
            return None

    except Exception as e:
        print(f"[SHIE] GPSR PDF/upload error: {e}")
        return None


def get_billing_types():
    """Pobiera listę typów operacji billingowych Allegro"""
    return allegro_request('GET', '/billing/billing-types')


def get_billing_entries(date_from=None, date_to=None, type_id=None, offer_id=None, limit=100, offset=0):
    """
    Pobiera operacje billingowe z Allegro.
    date_from/date_to: ISO 8601 (np. '2026-03-01T00:00:00Z')
    type_id: typ operacji (np. 'SUC' = prowizja)
    """
    params = {'limit': min(limit, 100), 'offset': offset}
    if date_from:
        params['occurredAt.gte'] = date_from
    if date_to:
        params['occurredAt.lte'] = date_to
    if type_id:
        params['type.id'] = type_id
    if offer_id:
        params['offer.id'] = offer_id
    return allegro_request('GET', '/billing/billing-entries', params=params)


def get_all_billing_entries(date_from=None, date_to=None, type_id=None, max_pages=50):
    """
    Pobiera WSZYSTKIE operacje billingowe (paginacja po 100).
    Zwraca listę entries + error string.
    """
    all_entries = []
    offset = 0
    for _ in range(max_pages):
        result, error = get_billing_entries(
            date_from=date_from, date_to=date_to,
            type_id=type_id, limit=100, offset=offset
        )
        if error:
            return all_entries, error
        entries = result.get('billingEntries', [])
        if not entries:
            break
        all_entries.extend(entries)
        offset += len(entries)
        if len(entries) < 100:
            break
    return all_entries, None


def get_shipping_rates():
    """Pobiera cenniki wysyłki użytkownika"""
    return allegro_request('GET', '/sale/shipping-rates')


def get_billing_entries(date_from=None, date_to=None, type_id=None, offer_id=None, limit=100, offset=0):
    """Pobiera historię opłat z Allegro API"""
    params = {'limit': min(limit, 100), 'offset': offset}
    if date_from:
        params['occurredAt.gte'] = date_from
    if date_to:
        params['occurredAt.lte'] = date_to
    if type_id:
        params['type.id'] = type_id
    if offer_id:
        params['offer.id'] = offer_id
    return allegro_request('GET', '/billing/billing-entries', params=params)


def get_billing_types():
    """Pobiera listę typów opłat Allegro"""
    return allegro_request('GET', '/billing/billing-types')


def sync_billing_to_db(days=30):
    """Synchronizuje opłaty z Allegro API do lokalnej bazy danych"""
    from .database import get_db
    from datetime import datetime, timedelta

    date_from = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00.000Z')
    date_to = datetime.now().strftime('%Y-%m-%dT23:59:59.999Z')

    conn = get_db()
    total_synced = 0
    offset = 0

    while True:
        result, error = get_billing_entries(date_from=date_from, date_to=date_to, limit=100, offset=offset)
        if error or not result:
            print(f"Billing sync error: {error}")
            break

        entries = result.get('billingEntries', [])
        if not entries:
            break

        for entry in entries:
            billing_id = entry.get('id', '')
            if not billing_id:
                continue

            type_info = entry.get('type', {})
            offer_info = entry.get('offer', {})
            value_info = entry.get('value', {})

            try:
                conn.execute('''INSERT OR IGNORE INTO allegro_billing
                    (billing_id, type_code, type_name, offer_id, offer_name, order_id, amount, occurred_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (
                    billing_id,
                    type_info.get('id', ''),
                    type_info.get('name', ''),
                    offer_info.get('id', ''),
                    offer_info.get('name', ''),
                    entry.get('order', {}).get('id', '') if entry.get('order') else '',
                    float(value_info.get('amount', 0)),
                    entry.get('occurredAt', '')
                ))
                total_synced += 1
            except Exception as e:
                print(f"Billing insert error: {e}")

        conn.commit()
        offset += len(entries)
        print(f"Billing sync: {offset} entries processed...")

        if len(entries) < 100:
            break

    print(f"Billing sync complete: {total_synced} new entries")
    return total_synced


def get_categories(parent_id=None):
    """Pobiera kategorie Allegro"""
    params = {'parent.id': parent_id} if parent_id else {}
    return allegro_request('GET', '/sale/categories', params=params)


def search_categories(name):
    """Wyszukuje kategorie po nazwie"""
    return allegro_request('GET', '/sale/matching-categories', params={'name': name})


def get_category_parameters(category_id):
    """Pobiera parametry wymagane dla kategorii"""
    return allegro_request('GET', f'/sale/categories/{category_id}/parameters')


def find_ean_parameter_id(category_id):
    """
    Znajduje ID parametru EAN/GTIN dla danej kategorii.
    Zwraca (param_id, param_type) lub (None, None) jeśli nie znaleziono.
    """
    params_result, error = get_category_parameters(category_id)
    if error or not params_result:
        return None, None
    
    # Szukaj parametru EAN/GTIN
    for param in params_result.get('parameters', []):
        param_name = param.get('name', '').lower()
        param_id = param.get('id')
        param_type = param.get('type')
        
        # Szukaj parametru o nazwie zawierającej EAN, GTIN, kod kreskowy
        if any(x in param_name for x in ['ean', 'gtin', 'kod kreskowy', 'barcode']):
            print(f"[BAR_] Found EAN parameter: {param.get('name')} (ID: {param_id}, type: {param_type})")
            return param_id, param_type
    
    return None, None


def extract_parameters_with_ai(title, description, category_parameters, gemini_key=None, product_specs=None):
    """
    Używa AI (Gemini) do ekstrakcji wartości parametrów z tytułu i opisu produktu.

    Args:
        title: Tytuł produktu
        description: Opis/bullet points produktu
        category_parameters: Lista parametrów kategorii z Allegro API
        gemini_key: Klucz API Gemini
        product_specs: Specyfikacja produktu (dict)
    
    Returns:
        dict: {param_id: {'value': str, 'value_id': str (jeśli słownik)}}
    """
    if not gemini_key:
        gemini_key = get_config('gemini_api_key', '')
    
    if not gemini_key:
        print("[WARN] Brak klucza Gemini API - pomijam ekstrakcję AI")
        return {}
    
    # Przygotuj listę parametrów do ekstrakcji
    params_for_ai = []
    for param in category_parameters:
        param_id = param.get('id')
        param_name = param.get('name', '')
        param_type = param.get('type')
        required = param.get('required', False)
        dictionary = param.get('dictionary', [])
        restrictions = param.get('restrictions', {})
        options = param.get('options', {})

        # Pomijaj czysto systemowe
        if options.get('ambiguousValueId'):
            continue

        # Pomijaj parametry z wyłączonymi sekcjami (section off)
        _skip = False
        for _k in ('section', 'restrictions', 'options'):
            _s = param.get(_k, {})
            if isinstance(_s, dict) and (_s.get('active') is False or _s.get('enabled') is False):
                _skip = True
                break
        if _skip:
            continue

        # Pomijaj EAN/GTIN — te mamy z bazy, AI nie musi
        _pn = param_name.lower()
        if any(x in _pn for x in ['ean', 'gtin', 'kod kreskowy', 'barcode']):
            continue
        
        param_info = {
            'id': param_id,
            'name': param_name,
            'type': param_type,
            'required': required,
            'options': []
        }
        
        # Jeśli ma słownik - podaj opcje
        if dictionary:
            param_info['options'] = [{'id': d.get('id'), 'value': d.get('value')} for d in dictionary[:40]]  # Max 40 opcji

        # Oznacz parametry wielowartościowe (checkboxy)
        if restrictions.get('multipleChoices') or options.get('multipleChoices'):
            param_info['multi'] = True
        
        params_for_ai.append(param_info)
    
    if not params_for_ai:
        return {}
    
    # Buduj prompt dla AI
    params_json = json.dumps(params_for_ai, ensure_ascii=False, indent=2)

    # Build specs section for AI
    specs_section = ""
    if product_specs and isinstance(product_specs, dict):
        specs_lines = [f"- {k}: {v}" for k, v in list(product_specs.items())[:30]]
        specs_section = "\n\nSPECYFIKACJA PRODUKTU (z Amazon — Funkcje, Szczegóły, Dodatkowe szczegóły):\n" + "\n".join(specs_lines)

    prompt = f"""Wypełnij WSZYSTKIE możliwe parametry oferty Allegro na podstawie danych produktu.
Zagłębiaj się w specyfikację — każdy szczegół jest ważny.

TYTUŁ: {title}

CECHY/OPIS:
{description[:3000] if description else 'brak opisu'}{specs_section}

PARAMETRY DO WYPEŁNIENIA:
{params_json}

ZASADY:
1. Analizuj DOKŁADNIE tytuł, opis, bullet points i specyfikację — wyciągaj KAŻDĄ możliwą wartość
2. Parametr z "options" → wybierz ID najlepiej pasującej opcji
3. Parametr bez opcji (type=string/float/integer) → podaj wartość
4. Nie znasz wartości → POMIŃ parametr (nie wpisuj "brak" ani "nie dotyczy")
5. "Stan" → zawsze "Nowy" jeśli dostępny
6. Marka/Producent → ZAWSZE "bez marki" — NIGDY konkretna marka
7. Kolor → z tytułu/opisu/specs (Black=Czarny, White=Biały, Grey=Szary, etc.)
8. Materiał → z opisu/specs (Plastic=Tworzywo sztuczne, Metal=Metal, Memory Foam=Pianka, etc.)
9. Wymiary → PRZELICZ JEDNOSTKI:
   - Cale na centymetry: 1" = 2.54cm (np. 28"x16"x5" = 71x41x13 cm)
   - cm na metry jeśli parametr wymaga metrów (190cm = 1.9m)
   - mm na cm jeśli trzeba
10. Parametry numeryczne → TYLKO liczba bez jednostki
11. Wymiary AxBxC → ZAWSZE wypełnij szerokość, długość, wysokość osobno
12. "Informacje o bezpieczeństwie" → ZAWSZE wypełnij (CE jeśli dostępne)
13. "Stan opakowania" → POMIŃ
14. PARAMETRY WIELOWARTOŚCIOWE (multi=true) → możesz wybrać KILKA opcji naraz!
    Np. "Cechy dodatkowe" poduszki: antyalergiczna + spanie na boku + spanie na plecach + zdejmowany pokrowiec
    Format: {{"value_ids": ["id1", "id2", "id3"]}}
15. Waga → przelicz: lbs na kg (1 lb = 0.45 kg), oz na g (1 oz = 28.35g)
16. Pojemność → przelicz: galony na litry, fl oz na ml
17. WYPEŁNIAJ MAKSYMALNIE DUŻO parametrów — lepiej więcej niż mniej

FORMAT ODPOWIEDZI (tylko JSON, bez komentarzy):
{{
  "param_id": {{"value": "tekst"}} lub {{"value_id": "id_opcji"}} lub {{"value_ids": ["id1", "id2"]}}
}}"""

    try:
        import requests as _req
        from modules.database import get_config as _gc
        gemini_model_name = _gc('ai_model_tytuly', _gc('gemini_model', 'gemini-2.5-flash'))
        _url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model_name}:generateContent?key={gemini_key}"
        _payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096}
        }
        _resp = _req.post(_url, json=_payload, timeout=60)
        _resp.raise_for_status()
        _data = _resp.json()
        response_text = _data['candidates'][0]['content']['parts'][0]['text'].strip()

        # Wyciągnij JSON z odpowiedzi
        if '```json' in response_text:
            response_text = response_text.split('```json')[1].split('```')[0].strip()
        elif '```' in response_text:
            response_text = response_text.split('```')[1].split('```')[0].strip()

        extracted = json.loads(response_text)
        print(f"[SMAR] AI wyekstrahował {len(extracted)} parametrów")

        for param_id, data in extracted.items():
            if 'value_ids' in data:
                print(f"   [OK] {param_id}: value_ids={data['value_ids']} (multi)")
            elif 'value_id' in data:
                print(f"   [OK] {param_id}: value_id={data['value_id']}")
            elif 'value' in data:
                print(f"   [OK] {param_id}: value={data['value']}")

        return extracted

    except Exception as e:
        print(f"[ERR] Błąd AI ekstrakcji: {e}")
        return {}


def build_offer_parameters_ai(category_id, product_name="", description="", ean=None, asin=None, gemini_key=None, product_specs=None):
    """
    Buduje listę parametrów dla oferty z użyciem AI.

    Returns:
        dict: {'offer': [...], 'product': [...]}
        - offer: parametry ofertowe (Stan, Kolor, Materiał) → offer_data['parameters']
        - product: parametry produktowe (EAN, Producent, MPN) → productSet[].product.parameters
    """
    offer_parameters = []
    product_parameters = []
    added_param_ids = set()
    
    # Pobierz parametry kategorii
    params_result, error = get_category_parameters(category_id)
    if error or not params_result:
        print(f"[WARN] Could not get parameters for category {category_id}")
        return {'offer': [], 'product': []}
    
    category_params = params_result.get('parameters', [])
    
    # === KROK 1: Ekstrakcja AI ===
    ai_extracted = {}
    if product_name and gemini_key:
        ai_extracted = extract_parameters_with_ai(product_name, description, category_params, gemini_key, product_specs=product_specs)
    
    # === KROK 2: Przetwarzanie parametrów ===
    # Parametry PRODUKTOWE — idą do productSet[].product.parameters (nie do offer parameters[])
    _product_level_names = [
        'ean', 'gtin', 'kod kreskowy', 'barcode',
        'producent', 'manufacturer', 'brand', 'marka',
        'numer katalogowy', 'mpn', 'part number',
        'zestaw wieloelementowy', 'multipack',
        'isbn', 'issn', 'upc'
    ]

    for param in category_params:
        param_id = str(param.get('id'))
        param_name = param.get('name', '')
        param_name_lower = param_name.lower()
        param_type = param.get('type')
        required = param.get('required', False)
        dictionary = param.get('dictionary', [])
        restrictions = param.get('restrictions', {})
        options = param.get('options', {})

        # Pomijaj czysto systemowe
        if options.get('ambiguousValueId'):
            continue

        # === Pomijaj parametry z wyłączonymi sekcjami ===
        # Głęboki check — szukamy active=False/enabled=False na KAŻDYM poziomie
        _skip_section = False
        for _key in ('section', 'restrictions', 'options'):
            _sec = param.get(_key, {})
            if isinstance(_sec, dict):
                # Check bezpośredni
                if _sec.get('active') is False or _sec.get('enabled') is False:
                    _skip_section = True
                    break
                # Check zagnieżdżony (np. section.offer.active)
                for _sub_key, _sub_val in _sec.items():
                    if isinstance(_sub_val, dict):
                        if _sub_val.get('active') is False or _sub_val.get('enabled') is False:
                            _skip_section = True
                            break
                    elif isinstance(_sub_val, bool) and _sub_val is False and _sub_key in ('active', 'enabled'):
                        _skip_section = True
                        break
                if _skip_section:
                    break
            elif _sec == 'off' or _sec is False:
                _skip_section = True
                break

        if _skip_section:
            print(f"   ⏭ Skip (sekcja off): {param_name} [{param_id}]")
            continue

        # DEBUG: loguj sekcje dla pierwszych 5 parametrów (żeby zrozumieć strukturę)
        if len(offer_parameters) + len(product_parameters) < 3:
            _dbg = {k: param.get(k) for k in ('section', 'restrictions', 'options') if param.get(k)}
            if _dbg:
                print(f"   [SEAR] DEBUG {param_name}: {str(_dbg)[:200]}")

        # === Rozdziel: produktowy vs ofertowy ===
        # WAŻNE: describesProduct jest kluczową flagą z API — Rodzaj, Typ, Przeznaczenie itp. mają ją ustawioną
        is_product_param = (
            restrictions.get('productRequired') or
            restrictions.get('describedProductOnly') or
            options.get('identifiesProduct') or
            options.get('describesProduct') or
            any(x in param_name_lower for x in _product_level_names)
        )

        if is_product_param:
            # === PARAMETR PRODUKTOWY → productSet[].product.parameters ===
            _built = None

            # EAN/GTIN
            if any(x in param_name_lower for x in ['ean', 'gtin', 'kod kreskowy', 'barcode']):
                if ean and param_id not in added_param_ids:
                    _built = {'id': param_id, 'values': [str(ean)]}
                    print(f"   [INVE] Product: {param_name} = {ean}")

            # Numer katalogowy → ASIN
            elif any(x in param_name_lower for x in ['numer katalogowy', 'mpn', 'part number']):
                if asin and param_id not in added_param_ids:
                    _built = {'id': param_id, 'values': [str(asin)]}
                    print(f"   [INVE] Product: {param_name} = {asin}")

            # Producent/Marka → ZAWSZE "bez marki" / "inna" / "nieokreślona"
            elif any(x in param_name_lower for x in ['producent', 'manufacturer', 'marka', 'brand']):
                if param_id not in added_param_ids:
                    if dictionary:
                        # 1. Szukaj "bez marki" / "inna" / "nieokreślona" w słowniku
                        _no_brand_keywords = ['bez marki', 'inna', 'nieokreślona', 'nieokreślony', 'nie dotyczy', 'brak']
                        for dv in dictionary:
                            dvl = dv.get('value', '').lower().strip()
                            if dvl in _no_brand_keywords:
                                _built = {'id': param_id, 'valuesIds': [str(dv['id'])]}
                                print(f"   [INVE] Product: {param_name} = {dv['value']} (bez marki)")
                                break
                        # 2. Sprawdź ambiguousValueId
                        if not _built and options.get('ambiguousValueId'):
                            _amb_id = str(options['ambiguousValueId'])
                            _built = {'id': param_id, 'valuesIds': [_amb_id]}
                            print(f"   [INVE] Product: {param_name} = ambiguousValueId={_amb_id}")
                        # 3. Fallback: pierwsza wartość ze słownika
                        if not _built:
                            _built = {'id': param_id, 'valuesIds': [str(dictionary[0]['id'])]}
                            print(f"   [INVE] Product: {param_name} = {dictionary[0].get('value','?')} (first dict fallback)")
                    else:
                        # Brak słownika - custom value
                        _built = {'id': param_id, 'values': ['bez marki']}
                        print(f"   [INVE] Product: {param_name} = bez marki (custom value)")
                    print(f"   DEBUG {param_name} dict[0:3]: {[d.get('value','') for d in (dictionary or [])[:3]]}, ambiguous: {options.get('ambiguousValueId')}")

            # Inne produktowe (Rodzaj, Typ, Przeznaczenie, Cechy dodatkowe itp.)
            elif param_id not in added_param_ids:
                # 1. Spróbuj AI extraction
                # 1a. Multi-value (checkboxy: value_ids)
                if param_id in ai_extracted and 'value_ids' in ai_extracted[param_id]:
                    vids = [str(v) for v in ai_extracted[param_id]['value_ids']]
                    if dictionary:
                        valid = [str(d.get('id')) for d in dictionary]
                        valid_vids = [v for v in vids if v in valid]
                        if valid_vids:
                            _built = {'id': param_id, 'valuesIds': valid_vids}
                            vnames = [next((d.get('value') for d in dictionary if str(d.get('id')) == v), v) for v in valid_vids]
                            print(f"   [INVE] Product AI multi: {param_name} = {vnames}")
                # 1b. Single value_id
                elif param_id in ai_extracted and 'value_id' in ai_extracted[param_id]:
                    vid = str(ai_extracted[param_id]['value_id'])
                    if dictionary:
                        valid = [str(d.get('id')) for d in dictionary]
                        if vid in valid:
                            _built = {'id': param_id, 'valuesIds': [vid]}
                            vname = next((d.get('value') for d in dictionary if str(d.get('id')) == vid), vid)
                            print(f"   [INVE] Product AI: {param_name} = {vname}")
                # 1c. Text value
                elif param_id in ai_extracted and 'value' in ai_extracted[param_id]:
                    _val = str(ai_extracted[param_id]['value'])
                    if dictionary:
                        # Szukaj dopasowania w słowniku
                        _val_lower = _val.lower().strip()
                        for dv in dictionary:
                            if dv.get('value', '').lower().strip() == _val_lower:
                                _built = {'id': param_id, 'valuesIds': [str(dv['id'])]}
                                print(f"   [INVE] Product AI match: {param_name} = {dv['value']}")
                                break
                    if not _built and not dictionary:
                        _built = {'id': param_id, 'values': [_val]}
                        print(f"   [INVE] Product AI text: {param_name} = {_val}")

                # 2. Fallback: szukaj domyślnej wartości w słowniku
                if not _built and dictionary:
                    _default_keywords = ['nie', 'brak', 'nie dotyczy', 'inna', 'inny', 'inne', 'uniwersalny', 'uniwersalna', '1 szt', '1 sztuka']
                    for dv in dictionary:
                        dvl = dv.get('value', '').lower()
                        if dvl in _default_keywords or any(x == dvl for x in _default_keywords):
                            _built = {'id': param_id, 'valuesIds': [str(dv['id'])]}
                            print(f"   [INVE] Product default: {param_name} = {dv['value']}")
                            break

                # 3. Ostatni fallback: pierwszy element słownika (dla wymaganych)
                if not _built and dictionary and required:
                    _built = {'id': param_id, 'valuesIds': [str(dictionary[0]['id'])]}
                    print(f"   [INVE] Product first: {param_name} = {dictionary[0].get('value', '?')} (required fallback)")

            if _built:
                product_parameters.append(_built)
                added_param_ids.add(param_id)
            elif required:
                print(f"   [WARN] Product REQUIRED but no value: {param_name} [{param_id}]")
            continue

        # === PARAMETR OFERTOWY === (Stan, Kolor, Materiał, Rozmiar itd.)

        # Pomijaj nie-wymagane bez słownika i bez AI
        if not required and not dictionary and param_id not in ai_extracted:
            continue

        # === Sprawdź czy AI wyekstrahował wartość ===
        if param_id in ai_extracted:
            ai_data = ai_extracted[param_id]

            # Multi-value (checkboxy: value_ids)
            if 'value_ids' in ai_data:
                vids = [str(v) for v in ai_data['value_ids']]
                valid_ids = [str(d.get('id')) for d in dictionary]
                valid_vids = [v for v in vids if v in valid_ids]
                if valid_vids:
                    offer_parameters.append({'id': param_id, 'valuesIds': valid_vids})
                    added_param_ids.add(param_id)
                    vnames = [next((d.get('value') for d in dictionary if str(d.get('id')) == v), v) for v in valid_vids]
                    print(f"   [SMAR] AI multi: {param_name} = {vnames}")
                    continue

            # Single value_id
            elif 'value_id' in ai_data:
                value_id = str(ai_data['value_id'])
                valid_ids = [str(d.get('id')) for d in dictionary]
                if value_id in valid_ids:
                    offer_parameters.append({'id': param_id, 'valuesIds': [value_id]})
                    added_param_ids.add(param_id)
                    value_name = next((d.get('value') for d in dictionary if str(d.get('id')) == value_id), value_id)
                    print(f"   [SMAR] AI: {param_name} = {value_name}")
                    continue
                else:
                    print(f"   [WARN] AI nieprawidłowe ID: {value_id} dla {param_name}")

            # Text/numeric value
            elif 'value' in ai_data:
                value = str(ai_data['value'])[:50]
                if value and value != '--':
                    if dictionary:
                        # Try to match text to dictionary
                        _val_lower = value.lower().strip()
                        _matched = False
                        for dv in dictionary:
                            if dv.get('value', '').lower().strip() == _val_lower:
                                offer_parameters.append({'id': param_id, 'valuesIds': [str(dv['id'])]})
                                added_param_ids.add(param_id)
                                print(f"   [SMAR] AI dict match: {param_name} = {dv['value']}")
                                _matched = True
                                break
                        if _matched:
                            continue
                    if param_type in ('string', 'float', 'integer', None):
                        offer_parameters.append({'id': param_id, 'values': [value]})
                        added_param_ids.add(param_id)
                        print(f"   [SMAR] AI: {param_name} = {value}")
                        continue

        # === Fallback: Stan → "Nowy" ===
        if 'stan' in param_name_lower and dictionary:
            for dict_value in dictionary:
                dict_name = dict_value.get('value', '').lower()
                if 'now' in dict_name:
                    value_id = dict_value.get('id')
                    offer_parameters.append({'id': param_id, 'valuesIds': [str(value_id)]})
                    added_param_ids.add(param_id)
                    print(f"   [OK] Stan: Nowy")
                    break
            if param_id in added_param_ids:
                continue

        # === Fallback: Pozostałe wymagane → "uniwersalny/inny" ===
        if required and dictionary and param_id not in added_param_ids:
            for dict_value in dictionary:
                dict_name = dict_value.get('value', '').lower()
                if any(x in dict_name for x in ['uniwersaln', 'inny', 'inna', 'inne', 'brak', 'nie dotyczy', 'pozostał', 'dowol']):
                    value_id = dict_value.get('id')
                    offer_parameters.append({'id': param_id, 'valuesIds': [str(value_id)]})
                    added_param_ids.add(param_id)
                    print(f"   [WARN] Fallback: {param_name} = {dict_value.get('value')}")
                    break

        # === Fallback: Pierwszy z listy (wymagane) ===
        if required and dictionary and param_id not in added_param_ids:
            first = dictionary[0]
            value_id = first.get('id')
            offer_parameters.append({'id': param_id, 'valuesIds': [str(value_id)]})
            added_param_ids.add(param_id)
            print(f"   [WARN] First: {param_name} = {first.get('value')}")

        # === Fallback: Tekstowe wymagane — tylko z AI ===
        if required and param_type == 'string' and not dictionary and param_id not in added_param_ids:
            if param_id in ai_extracted:
                val = str(ai_extracted[param_id].get('value', ''))[:50]
                if val and val != '--':
                    offer_parameters.append({'id': param_id, 'values': [val]})
                    added_param_ids.add(param_id)
                    print(f"   [SMAR] AI text: {param_name} = {val}")

    print(f"[ASSI] Built: {len(offer_parameters)} offer + {len(product_parameters)} product params (AI: {len(ai_extracted)})")
    return {'offer': offer_parameters, 'product': product_parameters}


def build_offer_parameters(category_id, product_name="", ean=None, asin=None):
    """Wrapper bez AI"""
    return build_offer_parameters_ai(category_id, product_name, "", ean, asin, None)


def publish_offer(offer_id):
    """Publikuje (aktywuje) ofertę"""
    if not is_authenticated():
        return None, "Nie zalogowany"
    
    data = {'publication': {'status': 'ACTIVE'}}
    
    result, error = allegro_request('PATCH', f'/sale/product-offers/{offer_id}', data=data)
    if result:
        return result, None
    
    # Fallback do PUT
    result, error = allegro_request('PUT', f'/sale/product-offers/{offer_id}', data=data)
    return result, error


def update_offer_stock(allegro_offer_id, new_quantity):
    """Aktualizuje ilość sztuk istniejącej oferty na Allegro"""
    if not is_authenticated():
        return None, "Nie zalogowany do Allegro"

    data = {'stock': {'available': int(new_quantity)}}
    result, error = allegro_request('PATCH', f'/sale/product-offers/{allegro_offer_id}', data=data)
    if error:
        # Oferta nie istnieje na Allegro — oznacz jako zakończoną w DB
        error_lower = str(error).lower()
        if 'not exist' in error_lower or 'not found' in error_lower or '404' in error_lower:
            conn = get_db()
            conn.execute("UPDATE oferty SET status='zakonczona', data_aktualizacji=CURRENT_TIMESTAMP WHERE allegro_id=?",
                         (allegro_offer_id,))
            conn.commit()
            return None, f"OFFER_NOT_EXISTS:{allegro_offer_id}"
        return None, error

    # Update local DB
    conn = get_db()
    conn.execute('UPDATE oferty SET ilosc = ?, data_aktualizacji = CURRENT_TIMESTAMP WHERE allegro_id = ?',
                 (int(new_quantity), allegro_offer_id))
    conn.commit()
    return result, None


def sync_orders(today_only=True, notify=True, from_date_str=None):
    """Synchronizuje zamówienia z Allegro do bazy.
    - today_only=True: pobiera tylko zamówienia z dzisiaj
    - today_only=False: pobiera wszystkie zamówienia z miesiąca
    - from_date_str: własna data od (YYYY-MM-DD), nadpisuje today_only
    - notify=True: wysyła powiadomienia Telegram (tylko dla nowych dzisiejszych)
    """
    from datetime import datetime, date, timedelta

    # Migracja: upewnij się że kolumna notified istnieje
    try:
        _mig_conn = get_db()
        col_existed = True
        try:
            _mig_conn.execute("SELECT notified FROM sprzedaze LIMIT 1")
        except:
            col_existed = False
            _mig_conn.execute("ALTER TABLE sprzedaze ADD COLUMN notified INTEGER DEFAULT 0")
            _mig_conn.commit()
            print("[OK] Migracja: dodano kolumnę notified")
        if not col_existed:
            # Kolumna dopiero dodana - oznacz WSZYSTKIE istniejące zamówienia jako notified
            # żeby nie spamować starymi powiadomieniami
            _mig_conn.execute("UPDATE sprzedaze SET notified=1 WHERE notified=0")
            _mig_conn.commit()
            print("[OK] Migracja: oznaczono istniejące zamówienia jako notified")
    except Exception as _e:
        print(f"[WARN] Migracja notified: {_e}")

    # Auto-cleanup: zamówienia starsze niż 2 dni ze statusem 'nowa' → 'wyslana'
    # (jeśli po 2 dniach nie nadałeś ręcznie, to albo już wysłane albo pominięte)
    try:
        _cleanup_conn = get_db()
        # Najpierw pokaż co jest w bazie (diagnostyka)
        _diag = _cleanup_conn.execute('''
            SELECT status, COUNT(*) as cnt FROM sprzedaze
            WHERE status IN ('nowa','nowe','wyslana','wyslane','wysłane')
            GROUP BY status
        ''').fetchall()
        print(f"[BAR_] DB statusy: {dict((r['status'], r['cnt']) for r in _diag)}")

        # Normalizuj wszystkie warianty do 'wyslana' (ASCII)
        _norm = _cleanup_conn.execute('''
            UPDATE sprzedaze SET status = 'wyslana'
            WHERE status IN ('wyslane', 'wysłane')
        ''').rowcount
        if _norm > 0:
            _cleanup_conn.commit()
            print(f"[BUIL] Znormalizowano {_norm} statusów → 'wyslana'")

        _stale = _cleanup_conn.execute('''
            UPDATE sprzedaze SET status = 'wyslana'
            WHERE status IN ('nowa', 'nowe')
            AND data_sprzedazy < datetime('now', '-2 days')
        ''').rowcount
        if _stale > 0:
            _cleanup_conn.commit()
            print(f"[MOP] Auto-cleanup: {_stale} starych zamówień 'nowa' → 'wyslana'")
    except Exception as _ce:
        print(f"[WARN] Cleanup error: {_ce}")

    # Przy ręcznym sync historycznym NIE wysyłaj powiadomień
    # Ale auto-sync z from_date_str MOŻE mieć notify=True (przekazane jawnie)
    if not today_only and not from_date_str:
        notify = False
        print("[PHONEL] Powiadomienia wyłączone (sync całego miesiąca)")
    
    # Filtruj po dacie
    from_date = None
    if from_date_str:
        # Własna data od użytkownika
        from_date = f"{from_date_str}T00:00:00Z"
        print(f"[SYNC] Synchronizacja zamówień od: {from_date}")
    elif today_only:
        from_date = date.today().strftime('%Y-%m-%dT00:00:00Z')
        print(f"[SYNC] Synchronizacja zamówień od: {from_date}")
    else:
        # Pobierz z początku miesiąca
        first_of_month = date.today().replace(day=1)
        from_date = first_of_month.strftime('%Y-%m-%dT00:00:00Z')
        print(f"[SYNC] Synchronizacja zamówień od początku miesiąca: {from_date}")
    
    # Pobierz zamówienia w różnych statusach
    # Tylko statusy które Allegro faktycznie obsługuje z filtrem daty
    all_orders = []
    # READY_FOR_PROCESSING z filtrem daty (nowe zamówienia)
    try:
        orders_data, error = get_orders('READY_FOR_PROCESSING', from_date=from_date)
        if orders_data and 'checkoutForms' in orders_data:
            for _o in orders_data['checkoutForms']:
                _o['_allegro_query_status'] = 'READY_FOR_PROCESSING'
            all_orders.extend(orders_data['checkoutForms'])
    except Exception as _e:
        print(f"[SYNC] Błąd READY_FOR_PROCESSING: {_e}")

    # UWAGA: Allegro nie obsługuje statusu SENT w checkout-forms query.
    # Status wysyłki jest w order.fulfillment.status (sprawdzany niżej w kodzie).
    
    if not all_orders:
        return 0, None

    # Deduplikacja — to samo zamówienie może pojawić się w wielu statusach
    seen_order_ids = set()
    unique_orders = []
    for _ord in all_orders:
        _oid = _ord.get('id') if _ord else None
        if _oid and _oid not in seen_order_ids:
            seen_order_ids.add(_oid)
            unique_orders.append(_ord)
    all_orders = unique_orders

    conn = get_db()
    # Ustaw długi timeout żeby uniknąć database locked podczas synca
    try:
        conn.execute('PRAGMA busy_timeout=60000')
    except:
        pass
    synced = 0
    notified = 0
    stock_updated = 0
    
    for order in all_orders:
        if not order or not isinstance(order, dict):
            continue
        order_id = order.get('id')
        if not order_id:
            continue
        # Szukaj WSZYSTKIE rekordy dla tego zamówienia (jedno zamówienie = wiele line items)
        existing_rows = conn.execute('SELECT id, status FROM sprzedaze WHERE allegro_order_id = ?', (order_id,)).fetchall()
        if existing_rows:
            try:
                # Aktualizuj status istniejącego zamówienia na podstawie Allegro
                allegro_status = order.get('_allegro_query_status') or order.get('status', '')
                fulfillment = order.get('fulfillment', {})
                shipment_status = fulfillment.get('status', '') if fulfillment else ''
                delivery = order.get('delivery', {})
                delivery_picked = delivery.get('pickedUp', False) if delivery else False

                # Loguj PEŁNY status z Allegro (diagnostyka)
                local_statuses = [row['status'] for row in existing_rows]
                print(f"[Sync] {order_id[:12]}... DB={local_statuses} allegro_q={allegro_status} fulfill={shipment_status} picked={delivery_picked}")

                # Mapowanie: Allegro status → lokalny status
                new_local_status = None

                # Mapowanie statusów Allegro → lokalny
                # CANCELLED
                if allegro_status == 'CANCELLED':
                    new_local_status = 'anulowana'
                # pickedUp=true = kurier odebrał = WYSŁANA
                elif delivery_picked:
                    new_local_status = 'wyslana'
                # fulfill=SENT lub PICKED_UP = nadano przesyłkę
                elif shipment_status in ('SENT', 'PICKED_UP'):
                    # Sprawdź czy zamówienie jest starsze niż 4h — wtedy oznacz jako wysłane
                    # (bo user ręcznie nadał i kurier już odebrał)
                    _order_age_h = 999
                    try:
                        _order_date = order.get('payment', {}).get('finishedAt', '') or order.get('updatedAt', '')
                        if _order_date:
                            from dateutil import parser as _dparser
                            _order_dt = _dparser.isoparse(_order_date).replace(tzinfo=None)
                            _order_age_h = (datetime.now() - _order_dt).total_seconds() / 3600
                    except Exception:
                        pass

                    if _order_age_h > 4:
                        new_local_status = 'wyslana'  # starsze niż 4h z fulfill=SENT → wysłane
                    else:
                        new_local_status = 'nadana'  # świeże — jeszcze pakuje
                elif allegro_status == 'SENT':
                    new_local_status = 'wyslana'  # Allegro status SENT = wysłane
                # BOUGHT/FILLED/READY z jakimkolwiek fulfillment
                elif allegro_status in ('BOUGHT', 'FILLED', 'READY_FOR_PROCESSING'):
                    new_local_status = None  # Nie zmieniaj — to nowe zamówienie

                # Aktualizuj adres dostawy (pickup point lub adres odbiorcy)
                delivery = order.get('delivery') or {}
                pickup = delivery.get('pickupPoint') or {}
                address = delivery.get('address') or {}
                adres_parts = []
                if pickup and pickup.get('name'):
                    adres_parts.append(pickup.get('name', ''))
                    pp_addr = pickup.get('address') or {}
                    if pp_addr.get('street'):
                        adres_parts.append(pp_addr.get('street'))
                    if pp_addr.get('postCode'):
                        adres_parts.append(pp_addr.get('postCode'))
                    if pp_addr.get('city'):
                        adres_parts.append(pp_addr.get('city'))
                else:
                    if address.get('street'):
                        adres_parts.append(address.get('street'))
                    if address.get('postCode'):
                        adres_parts.append(address.get('postCode'))
                    if address.get('city'):
                        adres_parts.append(address.get('city'))
                new_adres = ', '.join(adres_parts) if adres_parts else ''
                if new_adres:
                    for row in existing_rows:
                        conn.execute('UPDATE sprzedaze SET adres = ? WHERE id = ?', (new_adres, row['id']))

                # Backfill metoda_dostawy jeśli puste
                _delivery_ex = order.get('delivery') or {}
                _method_ex = ((_delivery_ex.get('method') or {}).get('name', '') or '').lower()
                _pickup_ex = ((_delivery_ex.get('pickupPoint') or {}).get('id', '') or '').upper()
                if 'orlen' in _method_ex or _pickup_ex.startswith('ORL'):
                    _md_ex = 'Orlen'
                elif any(x in _method_ex for x in ['inpost', 'paczkomat', 'paczka w ruchu']) or (_pickup_ex and not _pickup_ex.startswith('ORL')):
                    _md_ex = 'InPost'
                elif 'dpd' in _method_ex:
                    _md_ex = 'DPD'
                elif 'dhl' in _method_ex:
                    _md_ex = 'DHL'
                else:
                    _md_ex = (_delivery_ex.get('method', {}).get('name', '') or '')[:20] or 'Kurier'
                for row in existing_rows:
                    conn.execute('UPDATE sprzedaze SET metoda_dostawy = ? WHERE id = ? AND (metoda_dostawy IS NULL OR metoda_dostawy = "")', (_md_ex, row['id']))

                # Uzupełnij koszt_dostawy jeśli brakuje (backfill przy re-sync)
                try:
                    _has_delivery = conn.execute('SELECT SUM(koszt_dostawy) as kd FROM sprzedaze WHERE allegro_order_id = ?', (order_id,)).fetchone()
                    if not _has_delivery or not _has_delivery['kd']:
                        _summary = order.get('summary') or {}
                        _total_to_pay = float((_summary.get('totalToPay') or {}).get('amount', 0))
                        _items = order.get('lineItems') or []
                        _items_total = sum(float(it.get('price', {}).get('amount', 0)) * it.get('quantity', 1) for it in _items)
                        _delivery_cost = max(0, _total_to_pay - _items_total)
                        if _delivery_cost > 0 and _items_total > 0:
                            for row in existing_rows:
                                _item_share = _delivery_cost / len(existing_rows)
                                conn.execute('UPDATE sprzedaze SET koszt_dostawy = ? WHERE id = ?', (round(_item_share, 2), row['id']))
                except Exception:
                    pass

                if new_local_status:
                    updated_cnt = 0
                    for row in existing_rows:
                        cur = row['status']
                        # Aktualizuj status jeśli nie jest już docelowy
                        if new_local_status == 'nadana' and cur in ('nowa', 'nowe'):
                            conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', (new_local_status, row['id']))
                            updated_cnt += 1
                        elif new_local_status == 'wyslana' and cur not in ('wyslana', 'wysłane', 'wyslane'):
                            conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', (new_local_status, row['id']))
                            updated_cnt += 1
                        elif new_local_status == 'anulowana' and cur != 'anulowana':
                            conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', (new_local_status, row['id']))
                            updated_cnt += 1
                    if updated_cnt > 0:
                        conn.commit()  # Commit NATYCHMIAST po każdym zamówieniu
                        print(f"  [OK] Zaktualizowano {updated_cnt}/{len(existing_rows)} items → {new_local_status}")
                    else:
                        conn.commit()  # Commit adres update
                        print(f"  ⏭ Już {new_local_status} ({len(existing_rows)} items)")
                else:
                    print(f"  [WARN] Brak mapowania: allegro={allegro_status} fulfill={shipment_status}")
            except Exception as _upd_err:
                print(f"  [ERR] Błąd aktualizacji statusu: {_upd_err}")
            continue
        
        # Pobierz datę zamówienia z Allegro
        order_date_raw = order.get('boughtAt') or order.get('updatedAt') or datetime.now().isoformat()
        # Normalizuj datę do formatu YYYY-MM-DD HH:MM:SS (czas lokalny)
        try:
            dt_str = order_date_raw.replace('Z', '+00:00')
            if '+' in dt_str[10:] or order_date_raw.endswith('Z'):
                # Ma strefę czasową - konwertuj do lokalnej (PL)
                dt = datetime.fromisoformat(dt_str)
                dt_local = dt.astimezone().replace(tzinfo=None)
                order_date = dt_local.strftime('%Y-%m-%d %H:%M:%S')
            else:
                order_date = dt_str[:19].replace('T', ' ')
        except:
            order_date = order_date_raw[:19].replace('T', ' ')
        
        # Pobierz adres dostawy — preferuj punkt odbioru (paczkomat/OneBox)
        delivery = order.get('delivery') or {}
        pickup = delivery.get('pickupPoint') or {}
        address = delivery.get('address') or {}
        adres_parts = []
        if pickup and pickup.get('name'):
            # Paczkomat / Allegro One Box / punkt odbioru
            adres_parts.append(pickup.get('name', ''))
            pp_addr = pickup.get('address') or {}
            if pp_addr.get('street'):
                adres_parts.append(pp_addr.get('street'))
            if pp_addr.get('postCode'):
                adres_parts.append(pp_addr.get('postCode'))
            if pp_addr.get('city'):
                adres_parts.append(pp_addr.get('city'))
        else:
            # Dostawa kurierem — adres odbiorcy
            if address.get('street'):
                adres_parts.append(address.get('street'))
            if address.get('postCode'):
                adres_parts.append(address.get('postCode'))
            if address.get('city'):
                adres_parts.append(address.get('city'))
        adres = ', '.join(adres_parts) if adres_parts else ''

        # Wykryj metodę dostawy (carrier)
        _method_name = ((delivery.get('method') or {}).get('name', '') or '')
        _ml = _method_name.lower()
        _pid = ((pickup.get('id') if pickup else None) or '').upper()
        if 'orlen' in _ml or _pid.startswith('ORL'):
            _metoda_dostawy = 'Orlen'
        elif any(x in _ml for x in ['inpost', 'paczkomat', 'paczka w ruchu']) or (_pid and not _pid.startswith('ORL')):
            _metoda_dostawy = 'InPost'
        elif 'dpd' in _ml:
            _metoda_dostawy = 'DPD'
        elif 'dhl' in _ml:
            _metoda_dostawy = 'DHL'
        else:
            _metoda_dostawy = _method_name[:20] or 'Kurier'

        # Oblicz koszt dostawy per zamówienie (totalToPay - suma item prices)
        _order_summary = order.get('summary') or {}
        _order_total = float((_order_summary.get('totalToPay') or {}).get('amount', 0))
        _order_items = order.get('lineItems') or []
        _order_items_value = sum(float(it.get('price', {}).get('amount', 0)) * it.get('quantity', 1) for it in _order_items)
        _order_delivery_cost = max(0, _order_total - _order_items_value)
        _order_item_count = len(_order_items) or 1

        _order_notify_items = []  # Collect items for grouped notification

        for item in _order_items:
            try:
                offer = item.get('offer') or {}
                nazwa = (offer.get('name') or 'Produkt')[:100]  # Zwiększone do 100 znaków
                cena = float(item['price']['amount'])
                kupujacy = (order.get('buyer') or {}).get('login', 'Nieznany')
                ilosc = item.get('quantity', 1)
                offer_id = offer.get('id', '')
                
                # Znajdź produkt_id i oferta_id przez allegro_id oferty
                produkt_id = None
                oferta_db_id = None
                if offer_id:
                    oferta = conn.execute('SELECT id, produkt_id FROM oferty WHERE allegro_id = ?', (offer_id,)).fetchone()
                    if oferta:
                        oferta_db_id = oferta['id']
                        produkt_id = oferta['produkt_id']

                # Fallback 1: jeśli oferta ma produkt_id=NULL, spróbuj po EAN z oferty Allegro
                if not produkt_id and oferta_db_id:
                    try:
                        # Pobierz EAN z oferty (external.id = GTIN/EAN na Allegro)
                        ext = item.get('offer', {}).get('external', {})
                        ean_allegro = ext.get('id', '') if ext else ''
                        if ean_allegro and len(ean_allegro) >= 8:
                            p_ean = conn.execute(
                                'SELECT id FROM produkty WHERE ean = ? AND ilosc > 0 LIMIT 1',
                                (ean_allegro,)).fetchone()
                            if p_ean:
                                produkt_id = p_ean['id']
                                # Zaktualizuj ofertę żeby następnym razem match był bezpośredni
                                conn.execute('UPDATE oferty SET produkt_id = ? WHERE id = ?',
                                           (produkt_id, oferta_db_id))
                                print(f"  [LINK] EAN match: {nazwa[:40]} → produkt [{produkt_id}] (EAN: {ean_allegro})")
                    except:
                        pass

                # Fallback 2: szukaj po ASIN w nazwie oferty (np. "B0CZ3W8SRK" w tytule)
                if not produkt_id:
                    try:
                        import re as _re_asin
                        asin_match = _re_asin.search(r'\b(B0[A-Z0-9]{8})\b', nazwa)
                        if asin_match:
                            asin_val = asin_match.group(1)
                            p_asin = conn.execute(
                                'SELECT id FROM produkty WHERE asin = ? AND ilosc > 0 LIMIT 1',
                                (asin_val,)).fetchone()
                            if p_asin:
                                produkt_id = p_asin['id']
                                if oferta_db_id:
                                    conn.execute('UPDATE oferty SET produkt_id = ? WHERE id = ?',
                                               (produkt_id, oferta_db_id))
                                print(f"  [LINK] ASIN match: {nazwa[:40]} → produkt [{produkt_id}] (ASIN: {asin_val})")
                    except:
                        pass

                # Fallback 3: smart text matching po nazwie/cenie
                if not produkt_id and nazwa and len(nazwa) > 5:
                    try:
                        if not hasattr(sync_orders, '_prod_cache'):
                            sync_orders._prod_cache = _precompute_produkty_data(conn)
                        pid, conf = _find_best_product_match(nazwa, cena, sync_orders._prod_cache)
                        if pid and conf >= 0.55:
                            produkt_id = pid
                            print(f"  [SEAR] Smart match: {nazwa[:40]} → produkt [{pid}] ({conf:.0%})")
                    except:
                        pass
                
                # Sprawdź duplikat per line-item (bez cena — zmiana ceny między syncami tworzyła duplikaty)
                _dup = conn.execute(
                    'SELECT id FROM sprzedaze WHERE allegro_order_id = ? AND nazwa = ?',
                    (order_id, nazwa)
                ).fetchone()
                if _dup:
                    print(f"  ⏭ Skip duplikat: {nazwa[:30]} ({order_id[:12]}...)")
                    continue

                # Koszt dostawy per line item (równy podział)
                _item_delivery = round(_order_delivery_cost / _order_item_count, 2)

                # Zapisz do bazy - z produkt_id, oferta_id, nazwą, adresem i kosztem dostawy
                # OR IGNORE: UNIQUE INDEX na (allegro_order_id, nazwa) blokuje duplikaty od innych pathow zapisu
                cur = conn.execute('''INSERT OR IGNORE INTO sprzedaze
                    (allegro_order_id, cena, ilosc, kupujacy, status, data_sprzedazy, produkt_id, oferta_id, nazwa, adres, notified, koszt_dostawy, metoda_dostawy)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (order_id, cena, ilosc, kupujacy, 'nowa', order_date, produkt_id, oferta_db_id, nazwa, adres, 0, _item_delivery, _metoda_dostawy))
                if cur.rowcount == 0:
                    print(f"  ⏭ Skip duplikat (unique idx): {nazwa[:30]} ({order_id[:12]}...)")
                    continue
                synced += 1
                
                # ========================================
                # AKTUALIZACJA STANÓW MAGAZYNOWYCH
                # ========================================
                produkt = None
                new_qty = None
                if produkt_id:
                    produkt = conn.execute('''
                        SELECT p.id, p.ilosc, p.nazwa, p.lokalizacja, p.regal,
                               p.data_dodania, p.cena_allegro,
                               COALESCE(pal.nazwa, p.paleta, '') as paleta_nazwa
                        FROM produkty p
                        LEFT JOIN palety pal ON p.paleta_id = pal.id
                        WHERE p.id = ?
                    ''', (produkt_id,)).fetchone()
                    if produkt:
                        new_qty = max(0, produkt['ilosc'] - ilosc)
                        conn.execute('''
                            UPDATE produkty SET
                                ilosc = ?,
                                status = CASE WHEN ? = 0 THEN 'sprzedany' ELSE status END,
                                data_sprzedazy = CASE WHEN ? = 0 THEN ? ELSE data_sprzedazy END
                            WHERE id = ?
                        ''', (new_qty, new_qty, new_qty, datetime.now().isoformat(), produkt['id']))
                        stock_updated += 1
                        print(f"[INVE] Stock: {produkt['nazwa'][:30]} ({produkt['ilosc']} -> {new_qty})")

                        # Dodaj historię sprzedaży do produktu
                        try:
                            from .database import add_historia
                            add_historia(produkt['id'], 'sprzedano', f'Sprzedano za {cena:.0f} zł do {kupujacy}', {'cena': cena, 'kupujacy': kupujacy, 'ilosc': ilosc})
                        except:
                            pass

                        # Restock alert - jeśli produkt wyprzedany szybko (od daty WYSTAWIENIA)
                        if new_qty == 0:
                            try:
                                _restock_enabled = get_config('telegram_alert_restock', 'true')
                                if _restock_enabled == 'true':
                                    # Użyj daty wystawienia oferty (nie data_dodania do bazy!)
                                    _data_wyst = None
                                    if oferta_db_id:
                                        _of_row = conn.execute('SELECT data_wystawienia FROM oferty WHERE id = ?', (oferta_db_id,)).fetchone()
                                        if _of_row and _of_row['data_wystawienia']:
                                            _data_wyst = str(_of_row['data_wystawienia'])
                                    # Fallback: data_dodania z produktu
                                    if not _data_wyst:
                                        _data_wyst = str(produkt['data_dodania']) if 'data_dodania' in produkt.keys() and produkt['data_dodania'] else None

                                    if _data_wyst:
                                        _added = datetime.fromisoformat(_data_wyst.replace('Z', '+00:00')) if 'T' in _data_wyst else datetime.strptime(_data_wyst[:10], '%Y-%m-%d')
                                        _days = (datetime.now() - _added).days
                                        if _days < 30:
                                            _price_str = f"{float(produkt['cena_allegro'] or 0):.0f}" if produkt['cena_allegro'] else '?'
                                            _speed = "BŁYSKAWICZNIE! " if _days <= 1 else "SZYBKO! " if _days <= 3 else ""
                                            _restock_msg = (
                                                f"\U0001f504 RESTOCK ALERT!\n\n"
                                                f"\U0001f4e6 {produkt['nazwa']}\n"
                                                f"\u26a1 {_speed}Sprzedano w {_days} {'dzień' if _days == 1 else 'dni'}\n"
                                                f"\U0001f4b0 Cena: {_price_str} zł\n"
                                                f"\U0001f4cb Paleta: {produkt['paleta_nazwa'] or '?'}\n\n"
                                                f"Rozważ dokupienie!"
                                            )
                                            send_telegram(_restock_msg, parse_mode='HTML', silent=False)
                                            print(f"[SMAR] Restock alert: {produkt['nazwa'][:30]} ({_days} days)")
                            except Exception as e:
                                print(f"[WARN] Restock alert error: {e}")

                # Collect item for grouped notification (send after loop)
                if notify:
                    _lok = ''
                    _reg = ''
                    _pal = ''
                    _zostalo = None
                    _global_stock = None
                    if produkt_id and produkt:
                        _lok = produkt['lokalizacja'] or ''
                        _reg = produkt['regal'] or ''
                        _pal = produkt['paleta_nazwa'] or ''
                        _zostalo = new_qty
                        if new_qty is not None and new_qty <= 3:
                            try:
                                _prod_full = conn.execute('SELECT asin, ean FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
                                _asin = (_prod_full['asin'] or '').strip() if _prod_full else ''
                                _ean = (_prod_full['ean'] or '').strip() if _prod_full else ''
                                _others = []
                                if _asin and len(_asin) >= 5:
                                    _others = conn.execute('SELECT p.ilosc, COALESCE(pal.nazwa, p.paleta, "") as pal_nazwa FROM produkty p LEFT JOIN palety pal ON p.paleta_id = pal.id WHERE p.id != ? AND p.asin = ? AND p.ilosc > 0', (produkt_id, _asin)).fetchall()
                                elif _ean and len(_ean) >= 8:
                                    _others = conn.execute('SELECT p.ilosc, COALESCE(pal.nazwa, p.paleta, "") as pal_nazwa FROM produkty p LEFT JOIN palety pal ON p.paleta_id = pal.id WHERE p.id != ? AND p.ean = ? AND p.ilosc > 0', (produkt_id, _ean)).fetchall()
                                if _others:
                                    _global_stock = [{'ilosc': r['ilosc'], 'paleta': r['pal_nazwa'] or '?'} for r in _others]
                            except Exception as e:
                                print(f"[WARN] Cross-pallet lookup error: {e}")
                    _order_notify_items.append({
                        'nazwa': nazwa, 'cena': cena, 'ilosc': ilosc,
                        'lokalizacja': _lok, 'regal': _reg, 'paleta': _pal,
                        'zostalo': _zostalo, 'global_stock': _global_stock
                    })
                    conn.execute('UPDATE sprzedaze SET notified=1 WHERE allegro_order_id=? AND nazwa=?', (order_id, nazwa))

                    try:
                        if whatsapp_enabled():
                            delivery = order.get('delivery', {})
                            address = delivery.get('address', {})
                            miasto = address.get('city', '')
                            alert_whatsapp_sprzedaz(nazwa, miasto)
                    except Exception as e:
                        print(f"[WARN] Błąd WhatsApp: {e}")
                    
            except Exception as e:
                print(f"[ERR] Błąd przy zapisie zamówienia: {e}")

        # Send GROUPED notification for all items in this order
        if notify and _order_notify_items:
            try:
                kupujacy = (order.get('buyer') or {}).get('login', 'Nieznany')
                if len(_order_notify_items) == 1:
                    # Single item — use standard notification
                    it = _order_notify_items[0]
                    alert_sprzedaz(it['nazwa'], it['cena'], kupujacy,
                                   lokalizacja=it['lokalizacja'], regal=it['regal'],
                                   paleta=it['paleta'], ilosc_zostalo=it['zostalo'],
                                   ilosc=it['ilosc'], global_stock=it['global_stock'])
                else:
                    # Multiple items — send ONE grouped notification
                    total_value = sum(it['cena'] * it['ilosc'] for it in _order_notify_items)
                    msg = f"🔔💰 <b>SPRZEDAŻ!</b> 💰🔔\n\n"
                    msg += f"🛒 <b>{len(_order_notify_items)} produktów w zamówieniu</b>\n"
                    msg += f"👤 {kupujacy}\n\n"
                    for it in _order_notify_items:
                        msg += f"📦 {it['nazwa'][:50]}\n"
                        if it['ilosc'] > 1:
                            msg += f"   💵 {it['ilosc']} szt × {it['cena']:.2f} zł\n"
                        else:
                            msg += f"   💵 {it['cena']:.2f} zł\n"
                        if it['paleta']:
                            msg += f"   📦 {it['paleta']}\n"
                        if it['zostalo'] is not None and it['zostalo'] <= 3:
                            msg += f"   ⚠️ Zostało: {it['zostalo']} szt\n"
                        if it.get('global_stock'):
                            _total_gs = sum(g['ilosc'] for g in it['global_stock'])
                            msg += f"   📦 Ogólnie: {_total_gs} szt\n"
                    msg += f"\n💰 <b>RAZEM: {total_value:.2f} zł</b>"
                    msg += f"\n\n⏰ {datetime.now():%H:%M:%S}"
                    send_telegram(msg, silent=False)
                notified += 1
                print(f"[SMAR] Telegram grouped: {len(_order_notify_items)} items for {kupujacy}")
            except Exception as e:
                print(f"[WARN] Grouped notification error: {e}")

    conn.commit()
    conn.execute('PRAGMA wal_checkpoint(PASSIVE)')
    
    # Przy sync miesiąca wyślij jedno zbiorcze powiadomienie
    if not today_only and synced > 0:
        try:
            # Oblicz sumę zsynchronizowanych
            total_value = sum(float((o.get('summary') or {}).get('totalToPay', {}).get('amount', 0) if o else 0) for o in all_orders[:synced])
            msg = f"🔄 <b>SYNCHRONIZACJA</b>\n\n"
            msg += f"📦 Zsynchronizowano: <b>{synced}</b> zamówień\n"
            msg += f"📊 Zaktualizowano stanów: <b>{stock_updated}</b>\n"
            msg += f"\n⏰ {datetime.now():%H:%M:%S}"
            # Sync zbiorczy bez dźwięku
            send_telegram(msg, silent=True)
        except:
            pass
    
    # Backfill: dolinkuj sprzedaze ktore nie znalazly produkt_id w pierwszym przebiegu
    # (np. ze starszych syncow, albo gdy oferta byla podpinana po). Dzieje sie automatycznie
    # zeby COGS/zysk byly liczone na pelnych danych zamiast estymacji.
    try:
        bf_stats = backfill_link_sprzedaze(dry_run=False)
        _bf_total = bf_stats.get('sprzedaze_via_oferty', 0) + bf_stats.get('sprzedaze_direct', 0)
        if _bf_total > 0:
            print(f"[OK] Backfill: dolinkowano {_bf_total} sprzedaży do produktów")
    except Exception as _bf_e:
        print(f"[WARN] Backfill skipped: {_bf_e}")

    print(f"[OK] Zsynchronizowano {synced} zamówień, wysłano {notified} powiadomień, zaktualizowano {stock_updated} stanów")
    return synced, None


# ============================================================
# ŁĄCZENIE SPRZEDAŻY Z PRODUKTAMI (backfill + smart matching)
# ============================================================

def _word_tokens(text):
    """Wyciąga znaczące słowa z tekstu (3+ znaków) do porównywania"""
    import re as _re
    return set(_re.findall(r'[a-zA-Z0-9\u0080-\u017F]{3,}', (text or '').upper()))


def _text_similarity(tokens_a, tokens_b):
    """Oblicza podobieństwo dwóch zbiorów tokenów (Jaccard-like)"""
    if not tokens_a or not tokens_b:
        return 0.0
    common = tokens_a & tokens_b
    return len(common) / max(len(tokens_a), len(tokens_b))


def _find_best_product_match(nazwa_oferty, cena_oferty, produkty_data):
    """
    Znajduje najlepiej pasujący produkt dla oferty/sprzedaży.

    Używa wielu sygnałów:
    1. Dokładne dopasowanie meta_title/nazwa (pierwszych 30 znaków)
    2. Cena + podobieństwo słów
    3. Same słowa (brand + model)

    Args:
        nazwa_oferty: tytuł oferty Allegro
        cena_oferty: cena z Allegro
        produkty_data: lista dict z precomputed tokens

    Returns:
        (produkt_id, confidence) lub (None, 0)
    """
    if not nazwa_oferty or len(nazwa_oferty) < 5:
        return None, 0

    o_tokens = _word_tokens(nazwa_oferty)
    o_lower30 = nazwa_oferty[:30].lower().strip()
    o_lower40 = nazwa_oferty[:40].lower().strip()

    best_pid = None
    best_score = 0.0
    second_score = 0.0

    for pd in produkty_data:
        score = 0.0

        # Tier 1: Dokładne dopasowanie meta_title lub nazwa (30/40 znaków)
        if pd.get('mt_lower30') and pd['mt_lower30'] == o_lower30:
            score = max(score, 0.95)
        if pd.get('mt_lower40') and pd['mt_lower40'] == o_lower40:
            score = max(score, 0.98)
        if pd.get('n_lower30') and pd['n_lower30'] == o_lower30:
            score = max(score, 0.90)
        if pd.get('n_lower40') and pd['n_lower40'] == o_lower40:
            score = max(score, 0.95)

        # Tier 2: Podobieństwo słów
        sim = _text_similarity(o_tokens, pd['tokens'])

        # Tier 3: Bonus za dopasowanie ceny
        price_match = abs((cena_oferty or 0) - pd['cena']) < 0.02

        # Oblicz łączny score
        combined = sim
        if price_match:
            combined += 0.25  # Duży bonus za cenę

        score = max(score, combined)

        if score > best_score:
            second_score = best_score
            best_score = score
            best_pid = pd['id']
        elif score > second_score:
            second_score = score

    # Próg pewności: 0.5 minimum, i musi być wyraźnie lepszy od drugiego
    margin = best_score - second_score
    if best_score >= 0.5 and margin >= 0.05:
        confidence = min(best_score, 1.0)
        return best_pid, confidence

    return None, 0


def _precompute_produkty_data(conn):
    """Przygotowuje dane produktów do szybkiego matchingu"""
    produkty = conn.execute('''
        SELECT id, nazwa, meta_title, cena_allegro, asin, paleta_id
        FROM produkty
    ''').fetchall()

    data = []
    for p in produkty:
        tokens_n = _word_tokens(p['nazwa'])
        tokens_mt = _word_tokens(p['meta_title'])
        nazwa = p['nazwa'] or ''
        meta_t = p['meta_title'] or ''

        data.append({
            'id': p['id'],
            'cena': p['cena_allegro'] or 0,
            'tokens': tokens_n | tokens_mt,
            'n_lower30': nazwa[:30].lower().strip() if len(nazwa) > 5 else '',
            'n_lower40': nazwa[:40].lower().strip() if len(nazwa) > 5 else '',
            'mt_lower30': meta_t[:30].lower().strip() if len(meta_t) > 5 else '',
            'mt_lower40': meta_t[:40].lower().strip() if len(meta_t) > 5 else '',
            'asin': p['asin'] or '',
            'paleta_id': p['paleta_id']
        })
    return data


def backfill_link_sprzedaze(dry_run=False):
    """
    Łączy istniejące rekordy sprzedaży (sprzedaze) z produktami.

    Wieloetapowy algorytm:
    1. oferty → produkty (uzupełnia oferty.produkt_id)
    2. sprzedaze → oferty (uzupełnia sprzedaze.oferta_id)
    3. sprzedaze → produkty (uzupełnia sprzedaze.produkt_id przez łańcuch)
    4. Bezpośredni matching sprzedaze → produkty (fallback)

    Args:
        dry_run: jeśli True, nie zapisuje zmian (tylko statystyki)

    Returns:
        dict ze statystykami
    """
    conn = get_db()
    conn.execute('PRAGMA busy_timeout=30000')

    stats = {
        'oferty_linked': 0,
        'sprzedaze_via_oferty': 0,
        'sprzedaze_direct': 0,
        'oferty_total_unlinked': 0,
        'sprzedaze_total_unlinked': 0,
        'sprzedaze_still_unlinked': 0
    }

    # Precompute product data
    prod_data = _precompute_produkty_data(conn)

    # ==============================
    # KROK 1: Linkuj oferty → produkty
    # ==============================
    oferty_unlinked = conn.execute('''
        SELECT id, tytul, cena, allegro_id
        FROM oferty
        WHERE produkt_id IS NULL AND tytul IS NOT NULL AND LENGTH(tytul) > 5
    ''').fetchall()
    stats['oferty_total_unlinked'] = len(oferty_unlinked)

    for o in oferty_unlinked:
        pid, confidence = _find_best_product_match(o['tytul'], o['cena'], prod_data)
        if pid and confidence >= 0.5:
            if not dry_run:
                conn.execute('UPDATE oferty SET produkt_id = ? WHERE id = ?', (pid, o['id']))
            stats['oferty_linked'] += 1
            print(f"  [LINK] Oferta [{o['id']}] → Produkt [{pid}] (pewność: {confidence:.0%})")

    if not dry_run and stats['oferty_linked'] > 0:
        conn.commit()

    # ==============================
    # KROK 2: Linkuj sprzedaze → oferty (po nazwie)
    # ==============================
    # Buduj indeks ofert po nazwie
    all_oferty = conn.execute('SELECT id, tytul, produkt_id FROM oferty WHERE tytul IS NOT NULL').fetchall()
    oferta_by_name = {}
    for o in all_oferty:
        key40 = (o['tytul'] or '')[:40].lower().strip()
        if key40 and len(key40) > 5:
            # Preferuj ofertę z produkt_id
            existing = oferta_by_name.get(key40)
            if not existing or (o['produkt_id'] and not existing['produkt_id']):
                oferta_by_name[key40] = {
                    'id': o['id'],
                    'produkt_id': o['produkt_id'],
                    'tytul': o['tytul']
                }

    # Pobierz sprzedaże bez produkt_id
    sprz_unlinked = conn.execute('''
        SELECT id, nazwa, cena, oferta_id
        FROM sprzedaze
        WHERE produkt_id IS NULL
       
        AND nazwa IS NOT NULL AND LENGTH(nazwa) > 5
    ''').fetchall()
    stats['sprzedaze_total_unlinked'] = len(sprz_unlinked)

    linked_via_oferty = 0
    for s in sprz_unlinked:
        key40 = (s['nazwa'] or '')[:40].lower().strip()
        matched_oferta = oferta_by_name.get(key40)

        if matched_oferta and matched_oferta['produkt_id']:
            if not dry_run:
                updates = {'produkt_id': matched_oferta['produkt_id']}
                if not s['oferta_id']:
                    updates['oferta_id'] = matched_oferta['id']

                ALLOWED_COLS = {'produkt_id', 'oferta_id'}
                safe_keys = [k for k in updates.keys() if k in ALLOWED_COLS]
                set_clause = ', '.join(k + ' = ?' for k in safe_keys)
                conn.execute(
                    'UPDATE sprzedaze SET ' + set_clause + ' WHERE id = ?',
                    tuple(updates[k] for k in safe_keys) + (s['id'],)
                )
            linked_via_oferty += 1

    stats['sprzedaze_via_oferty'] = linked_via_oferty

    if not dry_run and linked_via_oferty > 0:
        conn.commit()

    # ==============================
    # KROK 3: Bezpośredni matching sprzedaze → produkty (fallback)
    # ==============================
    # Dla sprzedaży które nie znalazły oferty, spróbuj bezpośrednio
    still_unlinked = conn.execute('''
        SELECT id, nazwa, cena
        FROM sprzedaze
        WHERE produkt_id IS NULL
       
        AND nazwa IS NOT NULL AND LENGTH(nazwa) > 5
    ''').fetchall()

    # Buduj indeksy EAN/ASIN dla szybkiego lookup
    _ean_idx = {}
    _asin_idx = {}
    for pd in prod_data:
        if pd.get('asin') and len(pd['asin']) >= 10:
            _asin_idx[pd['asin']] = pd['id']
    _ean_rows = conn.execute('SELECT id, ean FROM produkty WHERE ean IS NOT NULL AND LENGTH(ean) >= 8').fetchall()
    for er in _ean_rows:
        _ean_idx[er['ean']] = er['id']

    import re as _re_bf
    direct_linked = 0
    for s in still_unlinked:
        pid = None

        # Próba 1: ASIN w nazwie (B0XXXXXXXXX)
        asin_m = _re_bf.search(r'\b(B0[A-Z0-9]{8})\b', s['nazwa'] or '')
        if asin_m and asin_m.group(1) in _asin_idx:
            pid = _asin_idx[asin_m.group(1)]

        # Próba 2: EAN w nazwie (13-cyfrowy numer)
        if not pid:
            ean_m = _re_bf.search(r'\b(\d{13})\b', s['nazwa'] or '')
            if ean_m and ean_m.group(1) in _ean_idx:
                pid = _ean_idx[ean_m.group(1)]

        # Próba 3: smart text matching
        if not pid:
            _pid, confidence = _find_best_product_match(s['nazwa'], s['cena'], prod_data)
            if _pid and confidence >= 0.55:
                pid = _pid

        if pid:
            if not dry_run:
                conn.execute('UPDATE sprzedaze SET produkt_id = ? WHERE id = ?', (pid, s['id']))
            direct_linked += 1

    stats['sprzedaze_direct'] = direct_linked

    if not dry_run and direct_linked > 0:
        conn.commit()

    # Policz ile zostało
    remaining = conn.execute('''
        SELECT COUNT(*) as cnt FROM sprzedaze
        WHERE produkt_id IS NULL
       
    ''').fetchone()
    stats['sprzedaze_still_unlinked'] = remaining['cnt']


    total_linked = stats['sprzedaze_via_oferty'] + stats['sprzedaze_direct']
    print(f"\n{'[DRY RUN] ' if dry_run else ''}=== BACKFILL ZAKOŃCZONY ===")
    print(f"  Oferty połączone z produktami: {stats['oferty_linked']} / {stats['oferty_total_unlinked']}")
    print(f"  Sprzedaże przez łańcuch oferty: {stats['sprzedaze_via_oferty']}")
    print(f"  Sprzedaże bezpośrednio: {stats['sprzedaze_direct']}")
    print(f"  RAZEM połączono: {total_linked} / {stats['sprzedaze_total_unlinked']}")
    print(f"  Zostało niepołączonych: {stats['sprzedaze_still_unlinked']}")

    return stats


def sync_returns(month=None):
    """
    Synchronizuje zwroty z Allegro API.
    Uzywa endpointu /payments/refunds oraz /order/refund-claims.

    Args:
        month: YYYY-MM format, domyslnie biezacy miesiac

    PERFORMANCE (refactor 15.04.2026):
    - Batch UPDATE z IN() zamiast N pojedynczych queries
    - 1 commit na koncu (atomic — albo wszystkie albo zadne, nic sie nie "traci")
    - Paginacja API (offset) dla > 100 zwrotow w miesiacu
    - Range filter na data_sprzedazy (uzywa indexu) zamiast strftime()
    - Summary SELECT zamiast N+1 SELECT per row
    Przed: ~30-60s dla 100 zwrotow. Po: ~1-3s (10-30x szybciej).
    """
    from datetime import datetime, date

    if not month:
        month = date.today().strftime('%Y-%m')

    print(f"[SYNC] Sprawdzam zwroty za {month}...")

    conn = get_db()

    # Range na data_sprzedazy (zamiast strftime — uzywa idx_sprzedaze_data)
    month_start = f"{month}-01T00:00:00"
    # Nastepny miesiac jako ograniczenie gorne
    _y, _m = int(month[:4]), int(month[5:7])
    if _m == 12:
        month_end = f"{_y + 1}-01-01T00:00:00"
    else:
        month_end = f"{_y}-{_m + 1:02d}-01T00:00:00"

    # DEBUG: Sprawdz ile zamowien (range filter, uzywa indexu)
    total_orders = conn.execute('''
        SELECT COUNT(*) as cnt FROM sprzedaze
        WHERE data_sprzedazy >= ? AND data_sprzedazy < ?
    ''', (month_start, month_end)).fetchone()['cnt']

    already_zwrot = conn.execute('''
        SELECT COUNT(*) as cnt FROM sprzedaze
        WHERE status = 'zwrot'
          AND data_sprzedazy >= ? AND data_sprzedazy < ?
    ''', (month_start, month_end)).fetchone()['cnt']

    print(f"[BAR_] W bazie: {total_orders} zamowien, {already_zwrot} juz zwrotow")

    from_date = f"{month}-01T00:00:00Z"
    refunded_order_ids = set()

    # METODA 1: /payments/refunds — z paginacja
    print(f"[DOWN] Pobieram payments/refunds (z paginacja)...")
    offset = 0
    page_limit = 100
    pages_fetched = 0
    max_pages = 50  # safety cap: 5000 zwrotow per miesiac wystarczy

    while pages_fetched < max_pages:
        refunds_data, error = allegro_request('GET', '/payments/refunds', params={
            'occurredAt.gte': from_date,
            'limit': page_limit,
            'offset': offset,
        })
        if error:
            print(f"   [WARN] Blad payments/refunds (offset={offset}): {error}")
            break
        if not refunds_data:
            break

        refunds_list = refunds_data.get('refunds', [])
        if not refunds_list:
            break

        pages_fetched += 1
        print(f"   [PAGE {pages_fetched}] offset={offset}, pobrano {len(refunds_list)}")

        for ref in refunds_list:
            ref_status = (ref.get('status') or '').upper()
            # Tylko sfinalizowane zwroty
            if ref_status not in ('SUCCESS', 'COMPLETED', 'RETURNED', 'FINISHED', ''):
                continue
            order_id = (ref.get('order') or {}).get('id')
            if order_id:
                refunded_order_ids.add(order_id)
            for item in ref.get('lineItems', []):
                checkout_id = (item.get('checkoutForm') or {}).get('id')
                if checkout_id:
                    refunded_order_ids.add(checkout_id)

        # Ostatnia strona?
        if len(refunds_list) < page_limit:
            break
        offset += page_limit

    # METODA 2: /order/refund-claims — z paginacja
    print(f"[DOWN] Pobieram refund-claims (z paginacja)...")
    offset = 0
    pages_fetched = 0

    while pages_fetched < max_pages:
        claims_data, error2 = allegro_request('GET', '/order/refund-claims', params={
            'createdAt.gte': from_date,
            'limit': page_limit,
            'offset': offset,
        })
        if error2:
            print(f"   [WARN] Blad refund-claims (offset={offset}): {error2}")
            break
        if not claims_data:
            break

        claims_list = claims_data.get('refundClaims', [])
        if not claims_list:
            break

        pages_fetched += 1
        print(f"   [PAGE {pages_fetched}] offset={offset}, pobrano {len(claims_list)}")

        for claim in claims_list:
            claim_status = (claim.get('status') or '').upper()
            if claim_status not in ('RETURNED', 'COMPLETED', 'APPROVED', 'FINISHED'):
                continue
            line_item = claim.get('lineItem', {})
            checkout_id = (line_item.get('checkoutForm') or {}).get('id')
            if checkout_id:
                refunded_order_ids.add(checkout_id)

        if len(claims_list) < page_limit:
            break
        offset += page_limit

    print(f"[BAR_] Unikalne order_id ze zwrotow: {len(refunded_order_ids)}")

    updated = 0
    if refunded_order_ids:
        # Pokaz kilka przykladow
        sample = list(refunded_order_ids)[:5]
        print(f"   Przyklady: {[s[:12] + '...' for s in sample]}")

        # BATCH UPDATE — jeden query z IN(...) zamiast N queries
        # SQLite ma limit 999 parametrow na query, wiec chunkujemy po 500
        ids_list = list(refunded_order_ids)
        CHUNK_SIZE = 500

        # Zbierz IDki zaktualizowane (do summary SELECT poza petla)
        updated_ids = []

        for chunk_start in range(0, len(ids_list), CHUNK_SIZE):
            chunk = ids_list[chunk_start:chunk_start + CHUNK_SIZE]
            placeholders = ','.join(['?'] * len(chunk))
            # UWAGA: NIE filtrujemy po data_sprzedazy — zwrot moze zostac zlozony
            # w innym miesiacu niz oryginalne zamowienie (typowy case: zakup w marcu,
            # zwrot w kwietniu). API juz filtruje po occurredAt.gte, a allegro_order_id
            # IN() precyzyjnie matchuje konkretne zamowienia.
            result = conn.execute(f'''
                UPDATE sprzedaze SET status = 'zwrot'
                WHERE allegro_order_id IN ({placeholders})
                  AND status != 'zwrot'
            ''', chunk)
            updated += result.rowcount

            # Ktore z chunka realnie sie zaktualizowaly — do logu summary
            # (SELECT poza petla, jeden query)
            if result.rowcount > 0:
                updated_ids.extend(chunk)

        # 1 commit na koncu — atomic
        conn.commit()

        # Summary SELECT: kilka przykladow kupujacych (zamiast N+1)
        if updated_ids:
            sample_ids = updated_ids[:5]
            sample_placeholders = ','.join(['?'] * len(sample_ids))
            sample_rows = conn.execute(
                f'SELECT allegro_order_id, kupujacy FROM sprzedaze '
                f'WHERE allegro_order_id IN ({sample_placeholders}) AND status = \'zwrot\'',
                sample_ids
            ).fetchall()
            for row in sample_rows:
                print(f"   [OK] Zwrot: {row['kupujacy']}")

    print(f"[OK] Oznaczono {updated} zwrotow za {month}")
    return updated, None


def repair_false_returns(month=None):
    """
    Napraw fałszywe zwroty - ponownie sprawdź status każdego 'zwrot' w Allegro API.
    Zamówienia które NIE są faktycznie zwrócone cofnij na 'wyslana'.
    """
    from datetime import date

    if not month:
        month = date.today().strftime('%Y-%m')

    conn = get_db()

    # Pobierz PRAWDZIWE zwroty z API (tylko finalized)
    real_refund_ids = set()

    from_date = f"{month}-01T00:00:00Z"

    # Metoda 1: payments/refunds - only SUCCESS
    refunds_data, _ = allegro_request('GET', '/payments/refunds', params={
        'occurredAt.gte': from_date, 'limit': 100
    })
    if refunds_data:
        for ref in refunds_data.get('refunds', []):
            ref_status = ref.get('status', '').upper()
            if ref_status in ('SUCCESS', 'COMPLETED', 'RETURNED', 'FINISHED', ''):
                order = ref.get('order', {})
                if order.get('id'):
                    real_refund_ids.add(order['id'])
                for item in ref.get('lineItems', []):
                    cid = item.get('checkoutForm', {}).get('id')
                    if cid:
                        real_refund_ids.add(cid)

    # Metoda 2: refund-claims - only RETURNED
    claims_data, _ = allegro_request('GET', '/order/refund-claims', params={
        'createdAt.gte': from_date, 'limit': 100
    })
    if claims_data:
        for claim in claims_data.get('refundClaims', []):
            claim_status = claim.get('status', '').upper()
            if claim_status in ('RETURNED', 'COMPLETED', 'APPROVED', 'FINISHED'):
                cid = claim.get('lineItem', {}).get('checkoutForm', {}).get('id')
                if cid:
                    real_refund_ids.add(cid)

    print(f"[REPAIR] Prawdziwe zwroty z API: {len(real_refund_ids)}")

    # Znajdź zamówienia w bazie oznaczone jako 'zwrot' które NIE są w real_refund_ids
    false_returns = conn.execute('''
        SELECT id, allegro_order_id, nazwa, kupujacy, cena, ilosc
        FROM sprzedaze
        WHERE status = 'zwrot' AND strftime('%Y-%m', data_sprzedazy) = ?
        AND allegro_order_id IS NOT NULL
    ''', (month,)).fetchall()

    repaired = 0
    for row in false_returns:
        if row['allegro_order_id'] not in real_refund_ids:
            conn.execute(
                'UPDATE sprzedaze SET status = ? WHERE id = ?',
                ('wyslana', row['id'])
            )
            repaired += 1
            print(f"   [FIX] Cofnięto zwrot: {row['kupujacy']} - {(row['nazwa'] or '')[:30]} ({row['cena']}zl)")

    conn.commit()
    print(f"[OK] Naprawiono {repaired} fałszywych zwrotów z {len(false_returns)} oznaczonych")
    return repaired


# ============================================================
# RENDER HELPER (extends base.html sidebar layout)
# ============================================================

def render(content, page_title='Allegro'):
    from flask import render_template_string, session, current_app
    template = """{% extends "base.html" %}
{% block page_title %}""" + page_title + """{% endblock %}
{% block content %}
{{ content|safe }}
{% endblock %}"""
    return render_template_string(template,
        content=content,
        version=current_app.config.get('VERSION',''),
        brand_name=current_app.config.get('BRAND_NAME','Akces Hub'),
        current_user=session.get('username'))


# ============================================================
# ROUTES
# ============================================================

@allegro_bp.route('/')
def index():
    config = get_allegro_config()
    configured = is_configured()
    authenticated = is_authenticated()

    if authenticated:
        status_cls = 'online'
        status_text = 'Polaczono z Allegro'
        dot_cls = 'online'
    elif configured:
        status_cls = ''
        status_text = 'Wymaga autoryzacji'
        dot_cls = ''
    else:
        status_cls = ''
        status_text = 'Nie skonfigurowano'
        dot_cls = ''

    # KPI stats
    conn = get_db()
    from datetime import date as _date
    _today = _date.today().strftime('%Y-%m-%d')
    _month = _date.today().strftime('%Y-%m')
    cnt_orders_today = conn.execute("SELECT COUNT(*) as c FROM sprzedaze WHERE date(data_sprzedazy)=?", (_today,)).fetchone()['c']
    cnt_orders_month = conn.execute("SELECT COUNT(*) as c FROM sprzedaze WHERE strftime('%Y-%m',data_sprzedazy)=?", (_month,)).fetchone()['c']
    cnt_offers = conn.execute("SELECT COUNT(*) as c FROM oferty WHERE status IN ('aktywna','active','ACTIVE','wystawiona')").fetchone()['c']
    revenue_month = conn.execute("SELECT COALESCE(SUM(cena*ilosc),0) as s FROM sprzedaze WHERE strftime('%Y-%m',data_sprzedazy)=? AND status NOT IN ('zwrot','anulowane','anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')", (_month,)).fetchone()['s']
    zwroty_suma = conn.execute("SELECT COALESCE(SUM(cena*ilosc),0) as s FROM sprzedaze WHERE strftime('%Y-%m',data_sprzedazy)=? AND status='zwrot'", (_month,)).fetchone()['s']

    user_info = None
    autosync_on = False
    if authenticated:
        user_info, _ = get_user_info()
        autosync_on = get_config('allegro_autosync', 'true') == 'true'

    # Sync history - recent daily order counts for last 7 days
    sync_history = conn.execute("""
        SELECT date(data_sprzedazy) as day, COUNT(*) as cnt
        FROM sprzedaze
        WHERE data_sprzedazy >= date('now', '-7 days')
        GROUP BY date(data_sprzedazy)
        ORDER BY day DESC
        LIMIT 7
    """).fetchall()

    # Returns count this month
    cnt_returns = conn.execute(
        "SELECT COUNT(*) as c FROM sprzedaze WHERE strftime('%Y-%m',data_sprzedazy)=? AND status='zwrot'",
        (_month,)
    ).fetchone()['c']

    # Last sync timestamp from config
    last_sync = get_config('allegro_last_sync', '')

    from flask import render_template
    return render_template('allegro_dashboard.html',
        authenticated=authenticated,
        configured=configured,
        status_text=status_text,
        cnt_orders_today=cnt_orders_today,
        cnt_orders_month=cnt_orders_month,
        cnt_offers=cnt_offers,
        revenue_month=revenue_month,
        user_info=user_info,
        autosync_on=autosync_on,
        sync_history=sync_history,
        cnt_returns=cnt_returns,
        last_sync=last_sync,
    )


@allegro_bp.route('/config', methods=['GET', 'POST'])
def config():
    if request.method == 'POST':
        import sqlite3 as _sql
        try:
            from modules.database import get_db
            conn = get_db()
            _new_secret = request.form.get('client_secret', '').strip()
            configs = {
                'allegro_client_id': request.form.get('client_id', '').strip(),
                'allegro_redirect_uri': request.form.get('redirect_uri', 'http://localhost:5000/allegro/callback').strip(),
                'allegro_sandbox': 'true' if request.form.get('sandbox') else 'false',
                'allegro_shipping_id': request.form.get('shipping_id', '').strip(),
                'allegro_city': request.form.get('city', 'Poznan').strip(),
                'allegro_province': request.form.get('province', 'WIELKOPOLSKIE').strip(),
                'allegro_postcode': request.form.get('postcode', '61-001').strip(),
                'allegro_autosync': 'true' if request.form.get('autosync') else 'false',
            }
            # Client secret: nadpisuj tylko jeśli podano nowy
            if _new_secret:
                configs['allegro_client_secret'] = _new_secret
            for k, v in configs.items():
                conn.execute('INSERT OR REPLACE INTO config (klucz, wartosc) VALUES (?, ?)', (k, v))
            conn.commit()
        except _sql.OperationalError:
            # Retry z set_config jeśli batch fail
            for k, v in configs.items():
                try:
                    set_config(k, v)
                except:
                    pass
        return redirect('/allegro')

    cfg = get_allegro_config()
    sandbox_checked = 'checked' if cfg['sandbox'] else ''
    autosync_checked = 'checked' if get_config('allegro_autosync', 'true') == 'true' else ''
    shipping_id = cfg.get('shipping_id', '')
    city = cfg.get('city', 'Poznan')
    province = cfg.get('province', 'WIELKOPOLSKIE')
    postcode = cfg.get('postcode', '61-001')

    # Pobierz cenniki wysyłki jeśli zalogowany
    shipping_options = ''
    if is_authenticated():
        rates, _ = get_shipping_rates()
        if rates and 'shippingRates' in rates:
            for rate in rates['shippingRates']:
                selected = 'selected' if rate['id'] == shipping_id else ''
                shipping_options += f'<option value="{rate["id"]}" {selected}>{rate["name"]}</option>'

    html = f'''
    <form method="POST">
    <input type="hidden" name="csrf_token" value="{generate_csrf()}">
    <div class="card">
        <div class="card-header"><div class="card-title"><span class=material-symbols-outlined>key</span> Dane API</div></div>
        <div class="form-group">
            <label>Client ID</label>
            <input type="text" name="client_id" class="form-control" value="{cfg['client_id']}" placeholder="Twoj Client ID">
        </div>
        <div class="form-group">
            <label>Client Secret {('(ustawiony: ****' + cfg['client_secret'][-4:] + ')') if cfg['client_secret'] else ''}</label>
            <input type="password" name="client_secret" class="form-control" value="" placeholder="Wpisz nowy secret lub zostaw puste">
        </div>
        <div class="form-group">
            <label>Redirect URI</label>
            <input type="text" name="redirect_uri" class="form-control" value="{cfg['redirect_uri']}">
        </div>
        <div class="toggle-row">
            <span><span class=material-symbols-outlined>science</span> Tryb Sandbox</span>
            <input type="checkbox" name="sandbox" {sandbox_checked}>
        </div>
    </div>

    <div class="card">
        <div class="card-header"><div class="card-title"><span class=material-symbols-outlined>sync</span> Auto-synchronizacja zamowien</div></div>
        <div class="toggle-row">
            <span><span class=material-symbols-outlined>smartphone</span> Automatyczna synchronizacja co 5 min</span>
            <input type="checkbox" name="autosync" {autosync_checked}>
        </div>
        <p style="font-size:0.75rem;color:var(--text-muted);margin-top:10px">
            Wlaczone: sprawdza nowe zamowienia co 5 minut i wysyla powiadomienia na Telegram
        </p>
    </div>

    <div class="card">
        <div class="card-header"><div class="card-title"><span class=material-symbols-outlined>inventory_2</span> Wysylka i lokalizacja</div></div>
        <div class="form-group">
            <label>Cennik wysylki</label>
            {'<select name="shipping_id" class="form-control"><option value="">-- Wybierz --</option>' + shipping_options + '</select>' if shipping_options else '<input type="text" name="shipping_id" class="form-control" value="' + shipping_id + '" placeholder="ID cennika (zaloguj sie aby pobrac liste)">'}
        </div>
        <div class="form-group">
            <label>Miasto</label>
            <input type="text" name="city" class="form-control" value="{city}" placeholder="Poznan">
        </div>
        <div class="form-group">
            <label>Kod pocztowy</label>
            <input type="text" name="postcode" class="form-control" value="{postcode}" placeholder="61-001">
        </div>
        <div class="form-group">
            <label>Wojewodztwo</label>
            <select name="province" class="form-control">
                <option value="DOLNOSLASKIE" {'selected' if province=='DOLNOSLASKIE' else ''}>Dolnoslaskie</option>
                <option value="KUJAWSKO_POMORSKIE" {'selected' if province=='KUJAWSKO_POMORSKIE' else ''}>Kujawsko-Pomorskie</option>
                <option value="LUBELSKIE" {'selected' if province=='LUBELSKIE' else ''}>Lubelskie</option>
                <option value="LUBUSKIE" {'selected' if province=='LUBUSKIE' else ''}>Lubuskie</option>
                <option value="LODZKIE" {'selected' if province=='LODZKIE' else ''}>Lodzkie</option>
                <option value="MALOPOLSKIE" {'selected' if province=='MALOPOLSKIE' else ''}>Malopolskie</option>
                <option value="MAZOWIECKIE" {'selected' if province=='MAZOWIECKIE' else ''}>Mazowieckie</option>
                <option value="OPOLSKIE" {'selected' if province=='OPOLSKIE' else ''}>Opolskie</option>
                <option value="PODKARPACKIE" {'selected' if province=='PODKARPACKIE' else ''}>Podkarpackie</option>
                <option value="PODLASKIE" {'selected' if province=='PODLASKIE' else ''}>Podlaskie</option>
                <option value="POMORSKIE" {'selected' if province=='POMORSKIE' else ''}>Pomorskie</option>
                <option value="SLASKIE" {'selected' if province=='SLASKIE' else ''}>Slaskie</option>
                <option value="SWIETOKRZYSKIE" {'selected' if province=='SWIETOKRZYSKIE' else ''}>Swietokrzyskie</option>
                <option value="WARMINSKO_MAZURSKIE" {'selected' if province=='WARMINSKO_MAZURSKIE' else ''}>Warminsko-Mazurskie</option>
                <option value="WIELKOPOLSKIE" {'selected' if province=='WIELKOPOLSKIE' else ''}>Wielkopolskie</option>
                <option value="ZACHODNIOPOMORSKIE" {'selected' if province=='ZACHODNIOPOMORSKIE' else ''}>Zachodniopomorskie</option>
            </select>
        </div>
    </div>

    <button type="submit" class="btn btn-primary"><span class=material-symbols-outlined>save</span> Zapisz</button>
    </form>
    '''

    # Sekcja zarządzania zdjęciami
    img_stats = get_images_stats()
    html += f'''
    <div class="card" style="margin-top:20px">
        <div class="card-header"><div class="card-title"><span class=material-symbols-outlined>photo_camera</span> Zarzadzanie zdjeciami</div></div>
        <div class="stat-row" style="grid-template-columns:1fr 1fr;margin-bottom:14px">
            <div class="stat-box">
                <div class="stat-val blue">{img_stats['count']}</div>
                <div class="stat-lbl">plikow</div>
            </div>
            <div class="stat-box">
                <div class="stat-val green">{img_stats['size_mb']} MB</div>
                <div class="stat-lbl">zajete</div>
            </div>
        </div>
        <a href="/allegro/cleanup-images" class="btn btn-secondary" onclick="return confirm('Usunac zdjecia starsze niz 7 dni?')">
            <span class=material-symbols-outlined>delete</span> Wyczysc stare zdjecia (7+ dni)
        </a>
        <div style="margin-top:8px;font-size:0.75rem;color:var(--text-muted)">
            Usuwa tylko zdjecia starsze niz 7 dni.
        </div>
    </div>

    <a href="/allegro" class="back">← Powrot</a>
    '''
    return render(html, 'Ustawienia Allegro')


@allegro_bp.route('/cleanup-images')
def cleanup_images_route():
    """Czyści stare zdjęcia (7+ dni)"""
    deleted = cleanup_old_images(days=7)
    stats = get_images_stats()

    return render(f'''
        <div class="alert alert-success">Usunieto {deleted} starych plikow (7+ dni)!</div>
        <div class="card" style="text-align:center">
            <div class="kpi-value" style="color:var(--green);margin-bottom:6px">{stats['count']}</div>
            <div style="color:var(--text-muted)">pozostalych plikow ({stats['size_mb']} MB)</div>
        </div>

        <div class="alert alert-warning" style="margin-top:15px">
            Jesli masz wciaz duzo plikow, uzyj pelnego czyszczenia ponizej
        </div>

        <div style="display:flex;gap:10px;margin-top:15px">
            <a href="/allegro/cleanup-images-all" class="btn btn-danger" onclick="return confirm('UWAGA!\\n\\nTo usunie WSZYSTKIE zdjecia ({stats['count']} plikow).\\n\\nOferty na Allegro NIE STRACA zdjec (sa juz na serwerach Allegro).\\n\\nKontynuowac?')" style="flex:1">
                <span class=material-symbols-outlined>delete</span> Wyczysc WSZYSTKIE ({stats['count']})
            </a>
            <a href="/allegro/config" class="btn btn-secondary" style="flex:1">← Powrot</a>
        </div>
    ''', 'Czyszczenie zdjec')


@allegro_bp.route('/cleanup-images-all')
def cleanup_images_all_route():
    """Usuwa WSZYSTKIE zdjęcia z folderu images"""
    try:
        ensure_images_dir()
        deleted = 0

        # Usuń wszystkie pliki
        for filename in os.listdir(IMAGES_DIR):
            filepath = os.path.join(IMAGES_DIR, filename)
            if os.path.isfile(filepath):
                try:
                    os.remove(filepath)
                    deleted += 1
                except Exception as e:
                    print(f"Nie mozna usunac {filename}: {e}")

        stats = get_images_stats()

        return render(f'''
            <div class="alert alert-success">
                <b>Usunieto {deleted} plikow!</b><br>
                <small>Folder images/ zostal wyczyszczony</small>
            </div>
            <div class="card" style="text-align:center">
                <div class="kpi-value" style="color:var(--green);margin-bottom:6px">{stats['count']}</div>
                <div style="color:var(--text-muted)">pozostalych plikow ({stats['size_mb']} MB)</div>
            </div>
            <div class="alert" style="background:var(--blue-soft);border:1px solid rgba(59,130,246,0.15);color:var(--blue);margin-top:15px">
                Oferty na Allegro nie stracily zdjec - sa juz na serwerach Allegro
            </div>
            <a href="/allegro/config" class="btn btn-primary">← Powrot do ustawien</a>
        ''', 'Wyczyszczono')

    except Exception as e:
        return render(f'''
            <div class="alert alert-error">Blad czyszczenia: {str(e)}</div>
            <a href="/allegro/config" class="btn btn-secondary">← Powrot</a>
        ''', 'Blad')


@allegro_bp.route('/auth')
def auth():
    """
    Autoryzacja Allegro - Authorization Code Flow
    Bardziej niezawodna metoda niż Device Flow
    """
    config = get_allegro_config()

    if not config['client_id']:
        return redirect('/allegro/config')

    auth_url, _, _ = get_api_urls()

    # Generuj state dla bezpieczeństwa
    import secrets
    state = secrets.token_urlsafe(32)
    set_config('allegro_oauth_state', state)

    # Buduj URL autoryzacji
    # UWAGA: Allegro nie obsługuje scope w URL — uprawnienia konfiguruje się
    # w panelu aplikacji: apps.developer.allegro.pl → Twoja aplikacja → Uprawnienia
    params = {
        'response_type': 'code',
        'client_id': config['client_id'],
        'redirect_uri': config['redirect_uri'],
        'state': state,
    }

    # Zbuduj pełny URL
    from urllib.parse import urlencode
    full_auth_url = f"{auth_url}?{urlencode(params)}"

    # Przekieruj do Allegro
    return redirect(full_auth_url)


@allegro_bp.route('/check')
def check_auth():
    """Sprawdź status autoryzacji - przekieruj do auth jeśli brak tokenu"""
    if is_authenticated():
        return render('''
            <div class="alert alert-success">Jestes zalogowany do Allegro!</div>
            <a href="/allegro" class="btn btn-primary"><span class=material-symbols-outlined>shopping_cart</span> Przejdz do Allegro</a>
        ''', 'Polaczono')
    else:
        return redirect('/allegro/auth')


@allegro_bp.route('/callback')
def callback():
    """
    Callback po autoryzacji Allegro - Authorization Code Flow
    """
    config = get_allegro_config()

    # Pobierz parametry z URL
    code = request.args.get('code')
    state = request.args.get('state')
    error = request.args.get('error')
    error_description = request.args.get('error_description', '')

    # Sprawdź błędy
    if error:
        return render(f'''
            <div class="alert alert-error">{error}: {error_description}</div>
            <a href="/allegro" class="btn btn-primary">← Powrot</a>
        ''', 'Blad autoryzacji')

    if not code:
        return render('''
            <div class="alert alert-error">Brak kodu autoryzacji</div>
            <a href="/allegro/auth" class="btn btn-primary"><span class=material-symbols-outlined>key</span> Sprobuj ponownie</a>
        ''', 'Blad')

    # Sprawdź state (ochrona przed CSRF)
    saved_state = get_config('allegro_oauth_state', '')
    if state and saved_state and state != saved_state:
        return render('''
            <div class="alert alert-error">Nieprawidlowy state - mozliwa proba ataku CSRF</div>
            <a href="/allegro/auth" class="btn btn-primary"><span class=material-symbols-outlined>key</span> Sprobuj ponownie</a>
        ''', 'Blad bezpieczenstwa')

    # Wymień kod na token
    _, token_url, _ = get_api_urls()

    try:
        auth_string = f"{config['client_id']}:{config['client_secret']}"
        auth_bytes = base64.b64encode(auth_string.encode()).decode()

        headers = {
            'Authorization': f'Basic {auth_bytes}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': config['redirect_uri']
        }

        print(f"[OAuth] Token exchange: POST {token_url}")
        print(f"[OAuth] redirect_uri: {config['redirect_uri']}")
        response = requests.post(token_url, headers=headers, data=data, timeout=30)
        print(f"[OAuth] Response: {response.status_code} {response.text[:200]}")

        if response.status_code == 200:
            tokens = response.json()

            # Zapisz tokeny
            set_config('allegro_access_token', tokens.get('access_token', ''))
            set_config('allegro_refresh_token', tokens.get('refresh_token', ''))

            # Oblicz czas wygaśnięcia
            expires_in = tokens.get('expires_in', 43200)
            expires_at = datetime.now() + timedelta(seconds=expires_in - 300)
            set_config('allegro_token_expires', expires_at.isoformat())

            # Wyczyść state
            set_config('allegro_oauth_state', '')

            return render('''
                <div class="alert alert-success">Pomyslnie polaczono z Allegro!</div>
                <p style="color:var(--text-muted);text-align:center;margin:15px 0">Teraz wybierz cennik wysylki w ustawieniach.</p>
                <a href="/allegro/config" class="btn btn-success"><span class=material-symbols-outlined>settings</span> Wybierz cennik wysylki</a>
                <a href="/allegro" class="btn btn-secondary"><span class=material-symbols-outlined>shopping_cart</span> Przejdz do Allegro</a>
            ''', 'Sukces')
        else:
            try:
                err_data = response.json()
                err_msg = err_data.get('error_description', err_data.get('error', response.text[:200]))
            except:
                err_msg = response.text[:200]

            return render(f'''
                <div class="alert alert-error">{err_msg}</div>
                <a href="/allegro/auth" class="btn btn-primary"><span class=material-symbols-outlined>key</span> Sprobuj ponownie</a>
            ''', 'Blad tokenu')

    except Exception as e:
        return render(f'''
            <div class="alert alert-error">{str(e)}</div>
            <a href="/allegro" class="btn btn-primary">← Powrot</a>
        ''', 'Blad')


@allegro_bp.route('/logout', methods=['POST'])
def logout():
    set_config('allegro_access_token', '')
    set_config('allegro_refresh_token', '')
    set_config('allegro_token_expires', '')
    return redirect('/allegro')


@allegro_bp.route('/zamowienia')
def zamowienia():
    orders_data, error = get_orders()

    html = ''

    if error:
        html += f'<div class="alert alert-error">{error}</div>'
    elif orders_data and 'checkoutForms' in orders_data:
        orders = orders_data['checkoutForms']
        html += f'<div class="alert alert-success" style="text-align:center">{len(orders)} zamowien do realizacji</div>'

        for order in orders:
            buyer = order.get('buyer', {}).get('login', 'Kupujacy')
            total = sum(float(item['price']['amount']) * item['quantity'] for item in order.get('lineItems', []))

            html += f'''
            <a href="/allegro/zamowienie/{order['id']}" class="list-item">
                <div class="list-item-info">
                    <div class="list-item-title"><span class=material-symbols-outlined>person</span> {buyer}</div>
                    <div class="list-item-meta">{len(order.get('lineItems', []))} prod.</div>
                </div>
                <div class="list-item-right">
                    <div class="list-item-value">{total:.2f} zl</div>
                </div>
            </a>'''
    else:
        html += '<div style="text-align:center;color:var(--text-muted);padding:30px">Brak zamowien</div>'

    html += '<a href="/allegro" class="back">← Powrot</a>'
    return render(html, 'Zamowienia')


@allegro_bp.route('/zamowienie/<order_id>')
def zamowienie_detail(order_id):
    order_data, error = get_order_details(order_id)

    if error:
        return render(f'<div class="alert alert-error">{error}</div><a href="/allegro/zamowienia" class="btn btn-primary">← Powrot</a>', 'Blad')

    buyer = order_data.get('buyer', {})
    delivery = order_data.get('delivery', {}).get('address', {})

    html = f'''
    <div class="card">
        <div class="card-header"><div class="card-title"><span class=material-symbols-outlined>person</span> Kupujacy</div></div>
        <div style="font-weight:600">{buyer.get('login', 'N/A')}</div>
        <div style="font-size:0.75rem;color:var(--text-muted);margin-top:4px">ID: {order_id[:20]}...</div>
    </div>
    <div class="card">
        <div class="card-header"><div class="card-title"><span class=material-symbols-outlined>location_on</span> Adres dostawy</div></div>
        <div style="font-size:0.85rem;color:var(--text-secondary)">
            {delivery.get('firstName', '')} {delivery.get('lastName', '')}<br>
            {delivery.get('street', '')}<br>
            {delivery.get('zipCode', '')} {delivery.get('city', '')}
        </div>
    </div>

    <div class="section-title">Produkty</div>
    '''

    total = 0
    for item in order_data.get('lineItems', []):
        price = float(item['price']['amount'])
        qty = item['quantity']
        total += price * qty
        html += f'''
        <div class="list-item">
            <div class="list-item-info">
                <div class="list-item-title">{(item.get('offer') or {}).get('name', 'Produkt')[:40]}</div>
                <div class="list-item-meta">{qty} x {price:.2f} zl</div>
            </div>
            <div class="list-item-right">
                <div class="list-item-value">{price*qty:.2f} zl</div>
            </div>
        </div>'''

    html += f'''
    <div class="card" style="background:linear-gradient(135deg,#ff5a00,#ff8c42);text-align:center;margin-top:16px">
        <div style="font-size:0.8rem;opacity:0.8;color:#fff">SUMA</div>
        <div style="font-size:1.5rem;font-weight:700;color:#fff">{total:.2f} zl</div>
    </div>
    <a href="/allegro/zamowienia" class="back">← Powrot</a>
    '''
    return render(html, 'Zamowienie')


@allegro_bp.route('/oferty')
def oferty():
    offers_data, error = get_my_offers()
    offers = []
    if not error and offers_data and 'offers' in offers_data:
        offers = offers_data['offers']
    from flask import render_template
    return render_template('allegro_oferty.html',
        offers=offers,
        error=error,
        total_count=len(offers),
    )


@allegro_bp.route('/napraw-zwroty')
def napraw_zwroty_route():
    """Napraw fałszywe zwroty - cofnij zamówienia błędnie oznaczone jako zwrot."""
    from datetime import date
    month = request.args.get('month', date.today().strftime('%Y-%m'))
    try:
        repaired = repair_false_returns(month)
        return f'<html><head><meta http-equiv="refresh" content="3;url=/sprzedaze"></head><body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0"><div style="text-align:center"><div style="font-size:1.5rem;color:#beee00;padding:40px">Naprawiono {repaired} fałszywych zwrotów za {month}</div><div style="color:#64748b">Przekierowanie...</div></div></body></html>'
    except Exception as e:
        return f'<html><body style="background:#0a0a0f;color:#ef4444;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0"><div>Błąd: {e}</div></body></html>'


@allegro_bp.route('/sync')
def sync():
    from datetime import date, datetime
    today = date.today().strftime('%d.%m.%Y')
    timestamp = datetime.now().strftime('%H:%M:%S')

    synced, error = sync_orders(today_only=True)

    if error:
        icon = 'error'
        icon_color = '#ef4444'
        icon_glow = 'rgba(239,68,68,0.4)'
        title = 'SYNC ERROR'
        subtitle = f'{error}'
        bar_color = '#ef4444'
        badge_text = 'FAILED'
        badge_bg = 'rgba(239,68,68,0.12)'
        badge_color = '#ef4444'
    elif synced > 0:
        icon = 'check_circle'
        icon_color = 'var(--neon-tertiary)'
        icon_glow = 'rgba(190,238,0,0.4)'
        title = 'SYNC COMPLETE'
        subtitle = f'Zsynchronizowano <span style="color:var(--neon-tertiary);font-weight:900">{synced}</span> nowych zamowien'
        bar_color = 'var(--neon-tertiary)'
        badge_text = f'+{synced} ORDERS'
        badge_bg = 'rgba(190,238,0,0.12)'
        badge_color = 'var(--neon-tertiary)'
    else:
        icon = 'verified'
        icon_color = 'var(--neon-primary)'
        icon_glow = 'rgba(143,245,255,0.4)'
        title = 'SYSTEM UP TO DATE'
        subtitle = 'Wszystkie zamowienia sa juz zsynchronizowane'
        bar_color = 'var(--neon-primary)'
        badge_text = 'ALL SYNCED'
        badge_bg = 'rgba(143,245,255,0.12)'
        badge_color = 'var(--neon-primary)'

    html = f'''
    <style>
    @keyframes syncBarFill {{ from {{ width:0 }} to {{ width:100% }} }}
    @keyframes syncFadeIn {{ from {{ opacity:0;transform:translateY(20px) }} to {{ opacity:1;transform:translateY(0) }} }}
    @keyframes syncIconPop {{ 0% {{ transform:scale(0);opacity:0 }} 60% {{ transform:scale(1.2) }} 100% {{ transform:scale(1);opacity:1 }} }}
    @keyframes syncGlowPulse {{ 0%,100% {{ box-shadow:0 0 20px {icon_glow},0 0 40px transparent }} 50% {{ box-shadow:0 0 30px {icon_glow},0 0 60px {icon_glow} }} }}
    .sync-glass {{ backdrop-filter:blur(16px);background:rgba(15,15,30,0.65);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:40px 32px;text-align:center;animation:syncFadeIn 0.5s ease-out }}
    .sync-icon-ring {{ width:88px;height:88px;border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 24px;background:rgba(15,15,30,0.8);border:2px solid {icon_color};animation:syncIconPop 0.6s ease-out 0.2s both, syncGlowPulse 3s ease-in-out infinite }}
    .sync-icon-ring .material-symbols-outlined {{ font-size:2.8rem;color:{icon_color} }}
    .sync-bar-track {{ width:100%;height:4px;background:rgba(255,255,255,0.06);border-radius:2px;margin:20px 0;overflow:hidden }}
    .sync-bar-fill {{ height:100%;background:{bar_color};border-radius:2px;animation:syncBarFill 0.8s ease-out forwards;box-shadow:0 0 12px {icon_glow} }}
    .sync-meta {{ display:flex;align-items:center;justify-content:center;gap:16px;font-size:0.65rem;color:var(--text-muted);letter-spacing:0.08em }}
    .sync-meta-dot {{ width:3px;height:3px;border-radius:50%;background:var(--text-muted) }}
    .sync-badge {{ display:inline-block;padding:4px 12px;background:{badge_bg};color:{badge_color};font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:0.6rem;letter-spacing:0.15em;border-radius:4px;margin-top:16px }}
    .sync-actions {{ display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:24px;animation:syncFadeIn 0.5s ease-out 0.3s both }}
    .sync-act {{ display:flex;align-items:center;justify-content:center;gap:8px;padding:14px 16px;backdrop-filter:blur(16px);background:rgba(15,15,30,0.65);border:1px solid rgba(255,255,255,0.08);border-radius:12px;text-decoration:none;color:var(--text);font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:0.68rem;letter-spacing:0.06em;transition:all 0.2s }}
    .sync-act:hover {{ background:rgba(15,15,30,0.85);border-color:rgba(255,255,255,0.15) }}
    .sync-act .material-symbols-outlined {{ font-size:1.1rem }}
    </style>

    <div class="sync-glass">
        <div class="sync-icon-ring">
            <span class="material-symbols-outlined">{icon}</span>
        </div>
        <div class="font-display" style="font-weight:900;font-size:1.3rem;letter-spacing:0.04em;margin-bottom:6px">{title}</div>
        <div style="font-size:0.78rem;color:var(--text-secondary);line-height:1.5">{subtitle}</div>
        <div class="sync-bar-track"><div class="sync-bar-fill"></div></div>
        <div class="sync-meta">
            <span>{today}</span>
            <span class="sync-meta-dot"></span>
            <span>{timestamp}</span>
            <span class="sync-meta-dot"></span>
            <span>ALLEGRO API</span>
        </div>
        <div class="sync-badge">{badge_text}</div>
    </div>

    <div class="sync-actions">
        <a href="/allegro/zamowienia" class="sync-act">
            <span class="material-symbols-outlined" style="color:var(--neon-tertiary)">inventory_2</span>
            ZAMOWIENIA
        </a>
        <a href="/allegro" class="sync-act">
            <span class="material-symbols-outlined" style="color:var(--neon-primary)">dashboard</span>
            DASHBOARD
        </a>
    </div>
    '''
    return render(html, 'Synchronizacja')



@allegro_bp.route('/sync-oferty-daty')
def sync_oferty_daty():
    """Synchronizuje daty wystawienia ofert z Allegro API i przekierowuje z informacją."""
    from flask import redirect, flash
    if not is_authenticated():
        flash('Nie zalogowany do Allegro', 'error')
        return redirect('/analityka/czas-sprzedazy')
    stats = sync_offers_status()
    if 'error' in stats:
        flash(f'Blad: {stats["error"]}', 'error')
    else:
        flash(f'Daty wystawienia zaktualizowane - pobrano {stats.get("total", 0)} ofert', 'success')
    return redirect('/analityka/czas-sprzedazy')


@allegro_bp.route('/backfill-link')
def backfill_link_route():
    """Uruchamia automatyczne łączenie sprzedaży z produktami"""
    stats = backfill_link_sprzedaze(dry_run=False)

    total_linked = stats['sprzedaze_via_oferty'] + stats['sprzedaze_direct']

    html = f'''
    <div class="kpi-grid" style="grid-template-columns:repeat(2,1fr)">
        <div class="kpi-card blue">
            <div class="kpi-icon" style="background:var(--blue-soft)"><span class=material-symbols-outlined>link</span></div>
            <div class="kpi-value">{stats['oferty_linked']}</div>
            <div class="kpi-label">Ofert polaczonych z produktami</div>
        </div>
        <div class="kpi-card green">
            <div class="kpi-icon" style="background:var(--green-soft)"><span class=material-symbols-outlined>payments</span></div>
            <div class="kpi-value">{total_linked}</div>
            <div class="kpi-label">Sprzedazy polaczonych z produktami</div>
        </div>
        <div class="kpi-card orange">
            <div class="kpi-icon" style="background:var(--yellow-soft)"><span class=material-symbols-outlined>warning</span></div>
            <div class="kpi-value">{stats['sprzedaze_still_unlinked']}</div>
            <div class="kpi-label">Nadal bez produktu</div>
        </div>
        <div class="kpi-card purple">
            <div class="kpi-icon" style="background:var(--accent-soft)"><span class=material-symbols-outlined>bar_chart</span></div>
            <div class="kpi-value">{stats['sprzedaze_total_unlinked']}</div>
            <div class="kpi-label">Bylo niepolaczonych</div>
        </div>
    </div>

    <div class="alert alert-success" style="margin-bottom:15px">
        <b>Podsumowanie:</b><br>
        Przez oferty: {stats['sprzedaze_via_oferty']} | Bezposrednio: {stats['sprzedaze_direct']}<br>
        Oferty: {stats['oferty_linked']} / {stats['oferty_total_unlinked']} polaczonych
    </div>
    '''

    if stats['sprzedaze_still_unlinked'] > 0:
        html += f'''
        <a href="/allegro/polacz-sprzedaze" class="btn btn-secondary" style="margin-bottom:10px">
            <span class=material-symbols-outlined>edit</span> Reczne laczenie ({stats['sprzedaze_still_unlinked']} szt)
        </a>
        '''

    html += '''
    <a href="/allegro/backfill-link" class="btn btn-secondary"><span class=material-symbols-outlined>sync</span> Uruchom ponownie</a>
    <a href="/allegro" class="back">← Powrot</a>
    '''
    return render(html, 'Laczenie sprzedazy')


@allegro_bp.route('/polacz-sprzedaze')
def polacz_sprzedaze():
    """Strona do recznego laczenia sprzedazy z produktami"""
    conn = get_db()

    # Pobierz niepołączone sprzedaże, grupowane po nazwie
    grupy = conn.execute('''
        SELECT nazwa, cena, COUNT(*) as cnt, SUM(ilosc) as szt,
               GROUP_CONCAT(id) as ids
        FROM sprzedaze
        WHERE produkt_id IS NULL
       
        AND nazwa IS NOT NULL AND LENGTH(nazwa) > 5
        AND nazwa NOT LIKE 'Zamówienie%'
        GROUP BY nazwa
        ORDER BY cnt DESC
    ''').fetchall()

    # Pobierz wszystkie produkty
    produkty = conn.execute('''
        SELECT p.id, p.nazwa, p.cena_allegro, p.paleta_id,
               pal.nazwa as paleta_nazwa
        FROM produkty p
        LEFT JOIN palety pal ON pal.id = p.paleta_id
        ORDER BY p.data_dodania DESC
    ''').fetchall()

    total_sprz = sum(g['cnt'] for g in grupy)

    html = f'''
    <div class="alert" style="background:var(--bg);border:1px solid var(--border);color:var(--text-secondary);text-align:center;margin-bottom:16px">
        {total_sprz} sprzedazy w {len(grupy)} grupach bez produktu
    </div>
    '''

    if not grupy:
        html += '<div class="alert alert-success">Wszystkie sprzedaze maja przypisany produkt!</div>'
        html += '<a href="/allegro" class="back">← Powrot</a>'
        return render(html, 'Laczenie sprzedazy')

    # Opcje produktów do select
    prod_options = '<option value="">-- wybierz produkt --</option>'
    for p in produkty:
        pal = f' [{p["paleta_nazwa"][:15]}]' if p['paleta_nazwa'] else ''
        cena = f' ({p["cena_allegro"]:.0f} zl)' if p['cena_allegro'] else ''
        nazwa_short = (p['nazwa'] or '')[:60]
        prod_options += f'<option value="{p["id"]}">{nazwa_short}{cena}{pal}</option>'

    html += f'''<form method="POST" action="/allegro/polacz-sprzedaze/zapisz"><input type="hidden" name="csrf_token" value="{generate_csrf()}">'''

    for g in grupy[:50]:  # Limit do 50 grup
        nazwa_display = (g['nazwa'] or '')[:80]
        cena_display = f"{g['cena']:.2f}" if g['cena'] else '?'
        html += f'''
        <div class="card" style="margin-bottom:8px;padding:14px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                <div>
                    <div style="font-weight:600;font-size:0.85rem">{nazwa_display}</div>
                    <div style="color:var(--text-muted);font-size:0.75rem">{g['cnt']}x sprzedaz | {g['szt']} szt | {cena_display} zl</div>
                </div>
            </div>
            <select name="match_{g['ids'].split(',')[0]}" class="form-control" style="font-size:0.8rem">
                {prod_options}
            </select>
            <input type="hidden" name="ids_{g['ids'].split(',')[0]}" value="{g['ids']}">
        </div>
        '''

    html += '''
        <button type="submit" class="btn btn-success" style="margin-top:15px">
            <span class=material-symbols-outlined>save</span> Zapisz polaczenia
        </button>
    </form>
    <a href="/allegro/backfill-link" class="btn btn-secondary" style="margin-top:10px"><span class=material-symbols-outlined>smart_toy</span> Auto-matching</a>
    <a href="/allegro" class="back">← Powrot</a>
    '''
    return render(html, 'Reczne laczenie')


@allegro_bp.route('/polacz-sprzedaze/zapisz', methods=['POST'])
def polacz_sprzedaze_zapisz():
    """Zapisuje reczne polaczenia sprzedazy z produktami"""
    conn = get_db()
    linked = 0

    for key, val in request.form.items():
        if key.startswith('match_') and val:
            try:
                produkt_id = int(val)
                sprz_key = key.replace('match_', 'ids_')
                ids_str = request.form.get(sprz_key, '')
                if ids_str:
                    for sid in ids_str.split(','):
                        sid = sid.strip()
                        if sid:
                            conn.execute(
                                'UPDATE sprzedaze SET produkt_id = ? WHERE id = ?',
                                (produkt_id, int(sid))
                            )
                            linked += 1
            except (ValueError, TypeError):
                continue

    conn.commit()

    html = f'''
    <div class="alert alert-success">Polaczono {linked} sprzedazy z produktami</div>
    <a href="/allegro/polacz-sprzedaze" class="btn btn-secondary"><span class=material-symbols-outlined>edit</span> Kontynuuj laczenie</a>
    <a href="/allegro" class="back">← Powrot</a>
    '''
    return render(html, 'Zapisano')


@allegro_bp.route('/api/status')
def api_status():
    return jsonify({'configured': is_configured(), 'authenticated': is_authenticated()})


# ============================================================
# SHIPMENT MANAGEMENT - Automatyczne nadawanie przesyłek
# ============================================================

def get_shipment_methods(order_id):
    """Pobiera istniejące przesyłki dla zamówienia"""
    print(f"[INVE] Pobieranie przesyłek dla zamówienia: {order_id}")
    result, error = allegro_request('GET', f'/order/checkout-forms/{order_id}/shipments')
    print(f"   → Wynik: {result}")
    print(f"   → Błąd: {error}")
    return result, error


def get_wysylam_z_allegro_shipments(order_id):
    """
    Pobiera przesyłki 'Wysyłam z Allegro' dla zamówienia.
    Używa endpointu shipment-management.
    """
    print(f"[INVE] Pobieranie przesyłek 'Wysyłam z Allegro' dla: {order_id}")
    
    # Pobierz przesyłki z shipment-management (próbuj oba formaty parametru)
    result, error = allegro_request('GET', '/shipment-management/shipments', params={
        'order.id': order_id
    })
    if error and '406' in str(error):
        print(f"   → Próbuję alternatywny parametr checkoutForm.id...")
        result, error = allegro_request('GET', '/shipment-management/shipments', params={
            'checkoutForm.id': order_id
        })
    
    print(f"   → Wynik: {result}")
    print(f"   → Błąd: {error}")
    return result, error


def create_wysylam_z_allegro_shipment(order_id, reference=None, parcel_size=None, dimensions=None):
    """
    Tworzy przesyłkę przez Wysyłam z Allegro (shipment-management API).
    POST /shipment-management/shipments/create-commands
    parcel_size: 'A', 'B', 'C' for InPost
    dimensions: {'length': cm, 'width': cm, 'height': cm, 'weight_kg': kg} for courier
    """
    print(f"[INVE] Tworzenie przesyłki (Wysyłam z Allegro) dla: {order_id}")

    # ── Stałe cenników i warunków ──
    CREDENTIALS_DPD = 'bf1a1cf0-6a1e-41b3-a42e-d46846b35f43'
    CREDENTIALS_INPOST = 'da329cd5-9819-4aef-aa77-2ee2d51abc59'
    RETURN_POLICY_ID = '7b75ba63-0967-4536-a439-730f8e563a59'
    WARRANTY_POLICY_ID = '128af307-9341-4f8c-b406-63b9060cce7d'

    # Pobierz dane zamówienia
    order, error = get_order_details(order_id)
    if error:
        return None, f"Nie można pobrać zamówienia: {error}"

    delivery = order.get('delivery') or {}
    method = delivery.get('method') or {}
    delivery_method_id = method.get('id')
    delivery_method_name = (method.get('name') or '').lower()
    pickup_point = delivery.get('pickupPoint') or {}
    address = delivery.get('address') or {}
    line_items = order.get('lineItems', [])

    print(f"   → deliveryMethodId: {delivery_method_id}")
    print(f"   → deliveryMethodName: {delivery_method_name}")
    print(f"   → pickupPoint: {pickup_point.get('id', 'brak')}")
    print(f"   → lineItems: {len(line_items)}")

    if not delivery_method_id:
        return None, "Brak metody dostawy w zamówieniu"

    if not line_items:
        return None, "Brak produktów w zamówieniu"

    line_item_ids = [item.get('id') for item in line_items if item.get('id')]

    # Zawartość paczki (dla etykiety DPD/DHL) - z nazwy oferty
    _item_name = (line_items[0].get('offer') or {}).get('name', '') if line_items else ''
    _parcel_content = _item_name[:50] if _item_name else 'Towar'

    # Rozpoznaj przewoźnika po nazwie metody dostawy
    is_orlen = 'orlen' in delivery_method_name
    is_inpost = any(kw in delivery_method_name for kw in ['inpost', 'paczkomat', 'paczka w ruchu']) and not is_orlen
    is_dpd = any(kw in delivery_method_name for kw in ['dpd', 'kurier dpd'])
    is_paczkomat = is_inpost or is_orlen
    credentials_id = CREDENTIALS_INPOST if is_paczkomat else CREDENTIALS_DPD
    carrier_name = 'InPost' if is_inpost else ('Orlen Paczka' if is_orlen else 'DPD/inny')
    print(f"   → Przewoźnik: {carrier_name}, credentialsId: {credentials_id}")

    # Buduj payload dla Wysyłam z Allegro
    import uuid
    command_id = str(uuid.uuid4())

    shipment_input = {
        'deliveryMethodId': delivery_method_id,
        'credentialsId': credentials_id,
        'lineItemIds': line_item_ids,
        'referenceNumber': reference[:20] if reference else None,
    }

    # Gabaryt paczki (paczkomaty) - wymiary w cm (API przyjmuje CENTIMETER)
    PACZKOMAT_SIZES = {
        # InPost A/B/C
        'A': {'length': 64, 'width': 38, 'height': 8, 'weight': 25},
        'B': {'length': 64, 'width': 38, 'height': 19, 'weight': 25},
        'C': {'length': 64, 'width': 38, 'height': 41, 'weight': 25},
        # Orlen Paczka S/M/L
        'S': {'length': 64, 'width': 38, 'height': 8, 'weight': 15},
        'M': {'length': 64, 'width': 38, 'height': 19, 'weight': 15},
        'L': {'length': 64, 'width': 38, 'height': 41, 'weight': 15},
    }
    if parcel_size and parcel_size.upper() in PACZKOMAT_SIZES:
        size_data = PACZKOMAT_SIZES[parcel_size.upper()]
        shipment_input['packages'] = [{
            'type': 'PACKAGE',
            'length': {'value': size_data['length'], 'unit': 'CENTIMETER'},
            'width': {'value': size_data['width'], 'unit': 'CENTIMETER'},
            'height': {'value': size_data['height'], 'unit': 'CENTIMETER'},
            'weight': {'value': size_data['weight'], 'unit': 'KILOGRAMS'},
            'content': _parcel_content,
        }]
        print(f"   → Gabaryt paczkomat: {parcel_size.upper()} ({size_data['length']}x{size_data['width']}x{size_data['height']}cm)")
    elif dimensions:
        shipment_input['packages'] = [{
            'type': 'PACKAGE',
            'length': {'value': int(float(dimensions.get('length', 30))), 'unit': 'CENTIMETER'},
            'width': {'value': int(float(dimensions.get('width', 25))), 'unit': 'CENTIMETER'},
            'height': {'value': int(float(dimensions.get('height', 15))), 'unit': 'CENTIMETER'},
            'weight': {'value': float(dimensions.get('weight_kg', 1)), 'unit': 'KILOGRAMS'},
            'content': _parcel_content,
        }]
        print(f"   → Wymiary kuriera: {dimensions}")
    else:
        # Default - mała paczka
        shipment_input['packages'] = [{
            'type': 'PACKAGE',
            'length': {'value': 30, 'unit': 'CENTIMETER'},
            'width': {'value': 25, 'unit': 'CENTIMETER'},
            'height': {'value': 15, 'unit': 'CENTIMETER'},
            'weight': {'value': 1, 'unit': 'KILOGRAMS'},
            'content': _parcel_content,
        }]

    # Adres odbiorcy (firstName/lastName lub companyName WYMAGANE)
    buyer = order.get('buyer', {})
    if address:
        first_name = address.get('firstName', '') or buyer.get('firstName', '')
        last_name = address.get('lastName', '') or buyer.get('lastName', '')
        # Fallback - wyciągnij z login kupującego
        if not first_name and not last_name:
            login = buyer.get('login', 'Kupujący')
            first_name = login
            last_name = ''
        receiver_name = f'{first_name} {last_name}'.strip() or 'Kupujący'
        # Email odbiorcy - z adresu, buyer, lub fallback
        receiver_email = address.get('email', '') or buyer.get('email', '') or 'noreply@allegro.pl'

        receiver = {
            'name': receiver_name,
            'street': address.get('street', '') or '-',
            'city': address.get('city', '') or '-',
            'postalCode': address.get('zipCode', '') or '00-000',
            'countryCode': address.get('countryCode', 'PL'),
            'email': receiver_email,
        }
        if address.get('phoneNumber'):
            receiver['phone'] = address['phoneNumber']
        if address.get('companyName'):
            receiver['company'] = address['companyName']
        shipment_input['receiver'] = receiver
        print(f"   → Odbiorca: {receiver_name}, {address.get('city', '')}, email: {receiver_email}")

    # Pickup point ODBIORU - WYMAGANE, Allegro NIE dziedziczy z zamówienia
    if pickup_point and pickup_point.get('id'):
        if 'receiver' in shipment_input:
            shipment_input['receiver']['point'] = pickup_point['id']
        print(f"   → Punkt odbioru (receiver.point): {pickup_point['id']}")

    # Nadawca — dane firmy z configu, potem hardcoded fallback
    try:
        from modules.database import get_config as _gc
        _fn = (_gc('firma_nazwa') or '').strip()
        _fi = (_gc('firma_imie') or '').strip()
        _fna = (_gc('firma_nazwisko') or '').strip()
        _fu = (_gc('firma_ulica') or '').strip()
        _fc = (_gc('allegro_city') or '').strip()
        _fp = (_gc('allegro_postcode') or '').strip()
        _fe = (_gc('firma_email') or '').strip()
        _ft = (_gc('firma_telefon') or '').strip()
    except:
        _fn = _fi = _fna = _fu = _fc = _fp = _fe = _ft = ''

    sender_first = _fi if _fi else 'Andrzej'
    sender_last = _fna if _fna else 'Gauza'
    shipment_input['sender'] = {
        'name': f'{sender_first} {sender_last}',
        'company': _fn if _fn else 'AKCES',
        'street': _fu if _fu else 'Poniatowskiego 13',
        'city': _fc if _fc else 'Mieszkowice',
        'postalCode': _fp if _fp else '74-505',
        'countryCode': 'PL',
        'email': _fe if _fe else 'agauza@interia.eu',
        'phone': _ft if _ft else '+48604753407',
    }
    # Nadanie w paczkomacie - sendingAtPoint + punkt nadania
    if is_inpost or is_orlen:
        sender_point = (get_config('sender_paczkomat') or '').strip() or 'MEZ01M'
        shipment_input['additionalServices'] = ['sendingAtPoint']
        shipment_input['sender']['point'] = sender_point
        print(f"   → Nadanie z paczkomatu: {sender_point} (sendingAtPoint)")

    print(f"   → [MAIL] SENDER PAYLOAD: {shipment_input['sender']}")
    print(f"   → [MAIL] RECEIVER PAYLOAD: {shipment_input.get('receiver', 'BRAK!')}")

    payload = {
        'commandId': command_id,
        'input': shipment_input
    }

    # Usuń None z payloadu (Allegro API nie akceptuje null wartości)
    def _clean(d):
        if isinstance(d, dict):
            return {k: _clean(v) for k, v in d.items() if v is not None}
        elif isinstance(d, list):
            return [_clean(i) for i in d if i is not None]
        return d
    payload = _clean(payload)

    print(f"   → Payload (cleaned): {payload}")

    # Wyślij do Wysyłam z Allegro API
    result, error = allegro_request('POST', '/shipment-management/shipments/create-commands', data=payload)

    print(f"   → Wynik: {result}")
    print(f"   → Błąd: {error}")

    if error:
        print(f"   → Błąd create-commands: {error}")
        # Nie rób fallback do standardowego API — wymaga carrierId/waybill które nie mamy
        return None, f"Błąd Wysyłam z Allegro: {error}"

    return result, error


def get_shipment_label(order_id):
    """
    Pobiera etykietę dla istniejącej przesyłki zamówienia.
    Próbuje najpierw 'Wysyłam z Allegro', potem standardowe API.
    
    Returns: (pdf_bytes, shipment_id, error)
    """
    # METODA 1: Sprawdź 'Wysyłam z Allegro' (shipment-management)
    result, error = get_wysylam_z_allegro_shipments(order_id)
    
    if result and result.get('shipments'):
        shipments = result.get('shipments', [])
        print(f"   → Znaleziono przesyłek (Wysyłam z Allegro): {len(shipments)}")
        
        if shipments:
            shipment = shipments[0]
            shipment_id = shipment.get('id')
            print(f"   → Shipment ID: {shipment_id}")
            
            # Pobierz etykietę
            config = get_allegro_config()
            base_url = ALLEGRO_SANDBOX_API_URL if config.get('sandbox') else ALLEGRO_API_URL
            
            # WZA label endpoint: /shipments/labels?shipmentIds={id} (batch endpoint)
            for accept_type in ['application/octet-stream', 'application/pdf']:
                try:
                    headers = {
                        'Authorization': f"Bearer {config['access_token']}",
                        'Accept': accept_type
                    }
                    label_url = f"{base_url}/shipment-management/shipments/labels?shipmentIds={shipment_id}"
                    print(f"   → Pobieranie etykiety WZA ({accept_type}): ...labels?shipmentIds={shipment_id[:20]}...")

                    response = requests.get(label_url, headers=headers, timeout=30)
                    print(f"   → HTTP Status: {response.status_code}, Content-Type: {response.headers.get('Content-Type','')}, Size: {len(response.content)}B")

                    if response.status_code == 200 and len(response.content) > 100:
                        print(f"   → [OK] Etykieta WZA pobrana! Rozmiar: {len(response.content)} bytes")
                        return response.content, shipment_id, None
                    else:
                        print(f"   → [WARN] {response.status_code}: {response.text[:100]}")
                except Exception as e:
                    print(f"   → [ERR] Wyjątek: {e}")
    # METODA 2: Sprawdź standardowe API (checkout-forms shipments)
    result, error = get_shipment_methods(order_id)
    if error:
        return None, None, f"Błąd pobierania przesyłek: {error}"
    
    shipments = result.get('shipments', []) if result else []
    print(f"   → Znaleziono przesyłek (standardowe): {len(shipments)}")
    
    if not shipments:
        return None, None, "BRAK_PRZESYLKI"
    
    # Weź pierwszą przesyłkę
    shipment = shipments[0]
    shipment_id = shipment.get('id')
    print(f"   → Shipment ID: {shipment_id}")
    print(f"   → Shipment data: {shipment}")
    
    # Pobierz etykietę jako PDF
    config = get_allegro_config()
    base_url = ALLEGRO_SANDBOX_API_URL if config.get('sandbox') else ALLEGRO_API_URL
    
    # Próbuj 3 endpointy: WZA batch, WZA single, checkout-forms
    endpoints = [
        f"{base_url}/shipment-management/shipments/labels?shipmentIds={shipment_id}",
        f"{base_url}/shipment-management/shipments/{shipment_id}/label",
        f"{base_url}/order/checkout-forms/{order_id}/shipments/{shipment_id}/label",
    ]
    for label_url in endpoints:
        for accept_type in ['application/octet-stream', 'application/pdf']:
            try:
                headers = {
                    'Authorization': f"Bearer {config['access_token']}",
                    'Accept': accept_type
                }
                print(f"   → Etykieta ({accept_type}): ...{label_url.split('/')[-1][:30]}")
                response = requests.get(label_url, headers=headers, timeout=30)

                if response.status_code == 200 and len(response.content) > 100:
                    print(f"   → [OK] Etykieta pobrana! {len(response.content)} bytes")
                    return response.content, shipment_id, None
            except Exception as e:
                print(f"   → [ERR] {e}")

    return None, shipment_id, f"Nie udało się pobrać etykiety po próbach wszystkich endpointów"


def create_and_get_label(order_id, reference=None, parcel_size=None, dimensions=None):
    """
    Tworzy przesyłkę i pobiera etykietę.
    Jeśli przesyłka istnieje - zwraca etykietę.
    Jeśli nie - tworzy nową przez API i pobiera etykietę.
    
    Returns: (pdf_bytes, shipment_id, error)
    """
    # Spróbuj pobrać etykietę istniejącej przesyłki
    label, shipment_id, error = get_shipment_label(order_id)
    
    if error == "BRAK_PRZESYLKI":
        # Utwórz nową przesyłkę
        print(f"[INVE] Brak przesyłki - tworzę nową...")
        
        # Pobierz dane zamówienia dla referencji (lokalizacja + nazwa produktu)
        order, ord_err = get_order_details(order_id)
        if order:
            items = order.get('lineItems', [])
            if items:
                offer_id = (items[0].get('offer') or {}).get('id', '')
                name = (items[0].get('offer') or {}).get('name', '')
                # Szukaj lokalizacji w bazie
                lok = ''
                if offer_id:
                    try:
                        from modules.database import get_db
                        conn = get_db()
                        p = conn.execute('''
                            SELECT p.lokalizacja, p.regal, p.kod_magazynowy
                            FROM produkty p JOIN oferty o ON o.produkt_id = p.id
                            WHERE o.allegro_id = ? LIMIT 1
                        ''', (offer_id,)).fetchone()
                        if p:
                            lok = p['kod_magazynowy'] or p['lokalizacja'] or p['regal'] or ''
                    except:
                        pass
                # Numer referencyjny = lokalizacja + skrócona nazwa (bez polskich znaków)
                # Zamień polskie znaki na ASCII
                _pl = str.maketrans('ąćęłńóśźżĄĆĘŁŃÓŚŹŻ', 'acelnoszzACELNOSZZ')
                import re as _re_ref
                clean_name = (name or '').translate(_pl)
                clean_name = _re_ref.sub(r'[^a-zA-Z0-9 _/\-]', '', clean_name).strip()
                # Skróć do kluczowych słów
                words = clean_name.split()[:3]  # max 3 słowa
                short = ' '.join(words)

                if lok:
                    clean_lok = _re_ref.sub(r'[^a-zA-Z0-9_/\-]', '', lok)
                    remaining = 20 - len(clean_lok) - 1
                    reference = f"{clean_lok}/{short[:remaining]}"[:20]
                else:
                    reference = short[:20] or order_id[:8].upper()
                print(f"   → Referencja: '{reference}'")
        
        # Spróbuj utworzyć przez Wysyłam z Allegro
        result, create_err = create_wysylam_z_allegro_shipment(order_id, reference, parcel_size=parcel_size, dimensions=dimensions)
        
        if create_err:
            print(f"   → Błąd tworzenia: {create_err}")
            return None, None, f"Nie można utworzyć przesyłki: {create_err}"
        
        if result:
            command_id = result.get('commandId') or result.get('id')
            print(f"   → [OK] Command utworzony: {command_id}")

            # Polluj status komendy - Allegro create jest asynchroniczne
            import time
            shipment_id = None
            for attempt in range(8):
                time.sleep(2)
                print(f"   → Polling command status (attempt {attempt+1}/8)...")
                cmd_result, cmd_err = allegro_request('GET', f'/shipment-management/shipments/create-commands/{command_id}')
                if cmd_result:
                    cmd_status = cmd_result.get('status', '')
                    print(f"   → Command status: {cmd_status}")
                    if cmd_status == 'SUCCESS':
                        shipment_id = cmd_result.get('shipmentId') or cmd_result.get('output', {}).get('shipmentId')
                        print(f"   → [OK] Shipment ID: {shipment_id}")
                        break
                    elif cmd_status in ('ERROR', 'FAILED'):
                        err_msg = cmd_result.get('errors', [{}])[0].get('message', '') if cmd_result.get('errors') else str(cmd_result)
                        print(f"   → [ERR] Command failed: {err_msg}")
                        return None, None, f"Allegro odrzuciło przesyłkę: {err_msg}"
                    # IN_PROGRESS - kontynuuj polling
                elif cmd_err:
                    print(f"   → Polling error: {cmd_err}")

            if not shipment_id:
                # Fallback - spróbuj pobrać po order_id
                print(f"   → Brak shipmentId z polling - szukam po order_id...")

            # Pobierz etykietę - retry kilka razy (Allegro generuje async)
            for label_attempt in range(10):
                time.sleep(5)
                print(f"   → Pobieranie etykiety (attempt {label_attempt+1}/10)...")
                label, shipment_id2, error = get_shipment_label(order_id)
                if label:
                    print(f"   → [OK] Etykieta pobrana!")
                    return label, shipment_id2 or shipment_id, None
                # Nie przerywaj pętli jeśli to błąd timing (label jeszcze nie gotowa)
                _transient = ('BRAK_PRZESYLKI', 'wszystkich endpointów', 'jeszcze niedostępna')
                if error and not any(t in str(error) for t in _transient) and '404' not in str(error) and 'not found' not in str(error).lower():
                    return None, shipment_id, f"Przesyłka utworzona ({shipment_id}), etykieta: {error}"
                print(f"   → Etykieta jeszcze niedostępna: {error}")

            # Po 10 próbach - przesyłka istnieje ale etykieta niedostępna
            return None, shipment_id, f"Przesyłka utworzona ({shipment_id}) ale etykieta jeszcze niedostępna. Spróbuj ponownie za chwilę."
    
    if error and error != "BRAK_PRZESYLKI":
        return None, shipment_id, error
    
    return label, shipment_id, None
