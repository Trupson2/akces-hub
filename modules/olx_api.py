"""
OLX.pl API module - integracja z OLX Partner API
OAuth2 + tworzenie ogłoszeń + zarządzanie ofertami
"""

import os
import json
import time
import requests
import hashlib
from datetime import datetime, timedelta
from flask import Blueprint, request, redirect, jsonify, flash
from urllib.parse import urlencode

from .database import get_db, get_config, set_config

olx_bp = Blueprint('olx', __name__)

# ============================================================
# KONFIGURACJA OLX API
# ============================================================
OLX_AUTH_URL = "https://www.olx.pl/oauth/authorize"
OLX_TOKEN_URL = "https://www.olx.pl/api/open/oauth/token"
OLX_API_URL = "https://www.olx.pl/api/partner"

# Retry config
MAX_RETRIES = 3
RETRY_DELAY = 2


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_olx_config():
    """Pobiera konfigurację OLX z bazy"""
    return {
        'client_id': get_config('olx_client_id', ''),
        'client_secret': get_config('olx_client_secret', ''),
        'access_token': get_config('olx_access_token', ''),
        'refresh_token': get_config('olx_refresh_token', ''),
        'token_expires': get_config('olx_token_expires', ''),
        'redirect_uri': get_config('olx_redirect_uri', 'http://localhost:5000/olx/callback'),
        'city': get_config('olx_city', ''),
        'city_id': get_config('olx_city_id', ''),
        'district_id': get_config('olx_district_id', ''),
        'latitude': get_config('olx_latitude', ''),
        'longitude': get_config('olx_longitude', ''),
    }


def is_configured():
    """Sprawdza czy OLX jest skonfigurowany"""
    cfg = get_olx_config()
    return bool(cfg['client_id'] and cfg['client_secret'])


def is_authenticated():
    """Sprawdza czy mamy ważny token OLX"""
    cfg = get_olx_config()
    if not cfg['access_token']:
        return False
    if cfg['token_expires']:
        try:
            expires = datetime.fromisoformat(cfg['token_expires'])
            if datetime.now() > expires:
                # Próbuj odświeżyć token
                if cfg['refresh_token']:
                    return refresh_olx_token()
                return False
        except:
            pass
    return True


def refresh_olx_token():
    """Odświeża token OLX"""
    cfg = get_olx_config()
    if not cfg['refresh_token']:
        return False

    try:
        response = requests.post(OLX_TOKEN_URL, data={
            'grant_type': 'refresh_token',
            'client_id': cfg['client_id'],
            'client_secret': cfg['client_secret'],
            'refresh_token': cfg['refresh_token'],
        }, timeout=30)

        if response.status_code == 200:
            tokens = response.json()
            set_config('olx_access_token', tokens['access_token'])
            if 'refresh_token' in tokens:
                set_config('olx_refresh_token', tokens['refresh_token'])
            expires_at = datetime.now() + timedelta(seconds=tokens.get('expires_in', 3600))
            set_config('olx_token_expires', expires_at.isoformat())
            print("✅ OLX token refreshed")
            return True
        else:
            print(f"❌ OLX token refresh failed: {response.status_code} {response.text}")
            return False
    except Exception as e:
        print(f"❌ OLX token refresh error: {e}")
        return False


def olx_api_request(method, endpoint, data=None, files=None):
    """Wykonuje request do OLX API z auto-refresh tokena"""
    cfg = get_olx_config()

    if not cfg['access_token']:
        return None, "Brak tokena OLX - zaloguj się"

    headers = {
        'Authorization': f'Bearer {cfg["access_token"]}',
        'Version': '2.0',
    }

    if data and not files:
        headers['Content-Type'] = 'application/json'

    url = f"{OLX_API_URL}{endpoint}"

    for attempt in range(MAX_RETRIES):
        try:
            if method == 'GET':
                resp = requests.get(url, headers=headers, params=data, timeout=30)
            elif method == 'POST':
                if files:
                    resp = requests.post(url, headers=headers, files=files, data=data, timeout=60)
                else:
                    resp = requests.post(url, headers=headers, json=data, timeout=30)
            elif method == 'PUT':
                resp = requests.put(url, headers=headers, json=data, timeout=30)
            elif method == 'DELETE':
                resp = requests.delete(url, headers=headers, timeout=30)
            else:
                return None, f"Nieznana metoda: {method}"

            if resp.status_code == 401:
                # Token expired - refresh
                if refresh_olx_token():
                    cfg = get_olx_config()
                    headers['Authorization'] = f'Bearer {cfg["access_token"]}'
                    continue
                return None, "Token wygasł - zaloguj się ponownie"

            if resp.status_code == 429:
                # Rate limited
                wait = int(resp.headers.get('Retry-After', 60))
                print(f"⚠️ OLX rate limit, waiting {wait}s...")
                time.sleep(wait)
                continue

            if resp.status_code >= 200 and resp.status_code < 300:
                try:
                    return resp.json(), None
                except:
                    return {'status': 'ok'}, None

            return None, f"HTTP {resp.status_code}: {resp.text[:200]}"

        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                return None, f"Błąd połączenia: {e}"

    return None, "Max retries exceeded"


def get_olx_categories():
    """Pobiera kategorie OLX"""
    data, err = olx_api_request('GET', '/categories')
    if err:
        return []
    return data.get('data', [])


def map_category_to_olx(kategoria):
    """Mapuje kategorię produktu na kategorię OLX"""
    mapping = {
        'elektronika': 41,      # Elektronika
        'agd': 36,              # Dom i Ogród > AGD
        'sport': 70,            # Sport i Hobby
        'odziez': 83,           # Moda
        'telefony': 42,         # Elektronika > Telefony
        'komputery': 43,        # Elektronika > Komputery
        'inne': 41,             # Elektronika (default)
    }
    return mapping.get(kategoria.lower(), 41)


# ============================================================
# TWORZENIE OGŁOSZENIA OLX Z PRODUKTU
# ============================================================

def create_olx_listing(produkt_id):
    """Tworzy ogłoszenie OLX z produktu w bazie"""
    conn = get_db()
    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()

    if not produkt:
        return None, "Nie znaleziono produktu"

    cfg = get_olx_config()

    # Przygotuj dane ogłoszenia
    nazwa = produkt['krotki_tytul'] or produkt['nazwa'] or ''
    # OLX wymaga 5-70 znaków w tytule
    if len(nazwa) > 70:
        nazwa = nazwa[:67] + '...'
    if len(nazwa) < 5:
        nazwa = f"Produkt {produkt['ean'] or produkt_id}"

    opis = produkt['opis_ai'] or produkt['nazwa'] or ''
    # OLX wymaga min 50 znaków opisu
    if len(opis) < 50:
        opis = f"{opis}\n\nProdukt dostępny od ręki. Stan: {produkt['stan'] or 'Nowy'}. Wysyłka w 24h."

    cena = produkt['cena_allegro'] or produkt['cena_brutto'] or 0
    if cena <= 0:
        return None, "Produkt nie ma ceny"

    # Mapuj stan
    stan_mapping = {
        'Nowy': 'new',
        'Używany': 'used',
        'Powystawkowy': 'used',
        'Uszkodzony': 'damaged',
    }
    stan = stan_mapping.get(produkt['stan'], 'used')

    category_id = map_category_to_olx(produkt['kategoria'] or 'inne')

    listing_data = {
        'title': nazwa,
        'description': opis,
        'category_id': category_id,
        'advertiser_type': 'business',
        'contact': {
            'name': get_config('olx_contact_name', 'Sprzedawca'),
            'phone': get_config('olx_contact_phone', ''),
        },
        'location': {},
        'price': {
            'value': int(cena * 100),  # OLX ceny w groszach
            'currency': 'PLN',
            'negotiable': False,
        },
    }

    # Lokalizacja
    if cfg.get('city_id'):
        listing_data['location']['city_id'] = int(cfg['city_id'])
    if cfg.get('district_id'):
        listing_data['location']['district_id'] = int(cfg['district_id'])
    if cfg.get('latitude') and cfg.get('longitude'):
        listing_data['location']['lat'] = float(cfg['latitude'])
        listing_data['location']['lon'] = float(cfg['longitude'])

    # 1. Utwórz draft
    result, err = olx_api_request('POST', '/adverts', listing_data)
    if err:
        return None, f"Błąd tworzenia ogłoszenia: {err}"

    advert_id = result.get('data', {}).get('id') or result.get('id')
    if not advert_id:
        return None, f"Brak ID ogłoszenia w odpowiedzi: {result}"

    # 2. Upload zdjęć
    images = []
    if produkt['zdjecie_url']:
        images.append(produkt['zdjecie_url'])
    if produkt.get('images'):
        try:
            extra = json.loads(produkt['images']) if isinstance(produkt['images'], str) else []
            images.extend(extra[:7])  # OLX max 8 zdjęć
        except:
            pass

    for img_url in images[:8]:
        try:
            upload_image_to_olx(advert_id, img_url)
        except Exception as e:
            print(f"⚠️ OLX image upload failed: {e}")

    # 3. Zapisz w bazie
    try:
        conn.execute('''
            INSERT OR REPLACE INTO olx_oferty
                (produkt_id, olx_advert_id, tytul, cena, status, data_utworzenia)
            VALUES (?, ?, ?, ?, 'draft', CURRENT_TIMESTAMP)
        ''', (produkt_id, str(advert_id), nazwa, cena))
        conn.commit()
    except Exception as e:
        print(f"⚠️ OLX DB save: {e}")

    return advert_id, None


def publish_olx_listing(advert_id):
    """Publikuje draft ogłoszenia na OLX"""
    result, err = olx_api_request('POST', f'/adverts/{advert_id}/commands', {
        'command': 'activate'
    })
    if err:
        return False, err

    # Zaktualizuj status w bazie
    try:
        conn = get_db()
        conn.execute('''
            UPDATE olx_oferty SET status = 'active'
            WHERE olx_advert_id = ?
        ''', (str(advert_id),))
        conn.commit()
    except:
        pass

    return True, None


def upload_image_to_olx(advert_id, image_url):
    """Uploaduje zdjęcie do ogłoszenia OLX"""
    # Pobierz zdjęcie
    if image_url.startswith('/static/'):
        # Lokalne zdjęcie
        import os
        local_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), image_url.lstrip('/'))
        if os.path.exists(local_path):
            with open(local_path, 'rb') as f:
                files = {'file': ('image.jpg', f, 'image/jpeg')}
                result, err = olx_api_request('POST', f'/adverts/{advert_id}/images', files=files)
                return result, err
        return None, "Plik nie istnieje"
    else:
        # Zdalne zdjęcie - pobierz najpierw
        try:
            resp = requests.get(image_url, timeout=30, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0'
            })
            if resp.status_code == 200:
                from io import BytesIO
                files = {'file': ('image.jpg', BytesIO(resp.content), 'image/jpeg')}
                result, err = olx_api_request('POST', f'/adverts/{advert_id}/images', files=files)
                return result, err
        except Exception as e:
            return None, str(e)

    return None, "Nie udało się uploadować zdjęcia"


# ============================================================
# ROUTES - BLUEPRINT
# ============================================================

@olx_bp.route('/')
def olx_home():
    """Strona główna OLX"""
    cfg = get_olx_config()
    auth = is_authenticated()

    # Pobierz oferty OLX z bazy
    conn = get_db()
    oferty = []
    stats = {'active': 0, 'draft': 0, 'total': 0}

    try:
        oferty = conn.execute('''
            SELECT o.*, p.nazwa as produkt_nazwa, p.zdjecie_url, p.cena_allegro
            FROM olx_oferty o
            LEFT JOIN produkty p ON o.produkt_id = p.id
            ORDER BY o.data_utworzenia DESC
            LIMIT 50
        ''').fetchall()

        counts = conn.execute('''
            SELECT status, COUNT(*) as cnt FROM olx_oferty GROUP BY status
        ''').fetchall()
        for row in counts:
            stats[row['status']] = row['cnt']
            stats['total'] += row['cnt']
    except:
        pass

    return f'''<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OLX - AKCES HUB</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:#0f172a; color:#e2e8f0; min-height:100vh; padding-bottom:80px; }}
.header {{ background:linear-gradient(135deg,#002f34,#00505a); padding:20px; text-align:center; }}
.header h1 {{ font-size:1.5rem; color:#fff; }}
.header .subtitle {{ color:#94a3b8; font-size:0.85rem; margin-top:4px; }}
.container {{ max-width:800px; margin:0 auto; padding:16px; }}
.status-card {{ background:#1e293b; border-radius:12px; padding:20px; margin:16px 0; }}
.status-card h3 {{ font-size:1rem; margin-bottom:12px; }}
.status-badge {{ display:inline-block; padding:4px 12px; border-radius:20px; font-size:0.8rem; font-weight:600; }}
.badge-green {{ background:#065f46; color:#6ee7b7; }}
.badge-yellow {{ background:#78350f; color:#fcd34d; }}
.badge-red {{ background:#7f1d1d; color:#fca5a5; }}
.stats-row {{ display:flex; gap:12px; margin:16px 0; }}
.stat-box {{ flex:1; background:#1e293b; border-radius:10px; padding:16px; text-align:center; }}
.stat-box .num {{ font-size:1.5rem; font-weight:700; color:#38bdf8; }}
.stat-box .label {{ font-size:0.75rem; color:#94a3b8; margin-top:4px; }}
.btn {{ display:inline-block; padding:12px 24px; border-radius:10px; text-decoration:none; font-weight:600; font-size:0.9rem; border:none; cursor:pointer; text-align:center; }}
.btn-primary {{ background:#002f34; color:#fff; }}
.btn-primary:hover {{ background:#00505a; }}
.btn-green {{ background:#059669; color:#fff; }}
.btn-danger {{ background:#dc2626; color:#fff; }}
.config-form {{ background:#1e293b; border-radius:12px; padding:20px; margin:16px 0; }}
.config-form label {{ display:block; color:#94a3b8; font-size:0.8rem; margin:12px 0 4px; }}
.config-form input {{ width:100%; padding:10px 14px; border-radius:8px; border:1px solid #334155; background:#0f172a; color:#e2e8f0; font-size:0.9rem; }}
.config-form input:focus {{ outline:none; border-color:#002f34; }}
.offer-card {{ background:#1e293b; border-radius:10px; padding:14px; margin:8px 0; display:flex; gap:12px; align-items:center; }}
.offer-img {{ width:50px; height:50px; border-radius:8px; object-fit:cover; background:#334155; }}
.offer-info {{ flex:1; }}
.offer-info .title {{ font-size:0.9rem; font-weight:600; }}
.offer-info .meta {{ font-size:0.75rem; color:#94a3b8; margin-top:2px; }}
.offer-price {{ font-weight:700; color:#6ee7b7; }}
a {{ color:#38bdf8; }}
</style>
<link rel="stylesheet" href="/static/kiosk.css">
</head>
<body>
<script>if(localStorage.getItem('kiosk_mode')==='1')document.body.classList.add('kiosk');</script>

<div class="header">
    <h1>🏪 OLX Integration</h1>
    <div class="subtitle">Zarządzaj ogłoszeniami na OLX.pl</div>
</div>

<div class="container">

    <!-- Status -->
    <div class="status-card">
        <h3>Status połączenia</h3>
        {'<span class="status-badge badge-green">✅ Połączono</span>' if auth else
         '<span class="status-badge badge-yellow">⚠️ Skonfigurowano - wymaga logowania</span>' if is_configured() else
         '<span class="status-badge badge-red">❌ Nie skonfigurowano</span>'}
        {f'<br><br><a href="/olx/auth" class="btn btn-primary">🔑 Zaloguj do OLX</a>' if is_configured() and not auth else ''}
    </div>

    <!-- Stats -->
    <div class="stats-row">
        <div class="stat-box">
            <div class="num">{stats['total']}</div>
            <div class="label">Ogłoszeń</div>
        </div>
        <div class="stat-box">
            <div class="num">{stats.get('active', 0)}</div>
            <div class="label">Aktywnych</div>
        </div>
        <div class="stat-box">
            <div class="num">{stats.get('draft', 0)}</div>
            <div class="label">Drafty</div>
        </div>
    </div>

    <!-- Config form -->
    <div class="config-form">
        <h3>⚙️ Konfiguracja OLX API</h3>
        <p style="color:#94a3b8;font-size:0.8rem;margin:8px 0">
            Zarejestruj się na <a href="https://developer.olx.pl" target="_blank">developer.olx.pl</a>
            i utwórz aplikację aby uzyskać Client ID i Secret.
        </p>
        <form method="POST" action="/olx/config">
            <label>Client ID</label>
            <input type="text" name="client_id" value="{cfg['client_id']}" placeholder="Twój OLX Client ID">

            <label>Client Secret</label>
            <input type="password" name="client_secret" value="{cfg['client_secret']}" placeholder="Twój OLX Client Secret">

            <label>Redirect URI</label>
            <input type="text" name="redirect_uri" value="{cfg['redirect_uri']}" placeholder="http://localhost:5000/olx/callback">

            <label>Nazwa kontaktu</label>
            <input type="text" name="contact_name" value="{get_config('olx_contact_name', '')}" placeholder="Imię na ogłoszeniu">

            <label>Telefon kontaktowy</label>
            <input type="text" name="contact_phone" value="{get_config('olx_contact_phone', '')}" placeholder="Numer telefonu">

            <label>Miasto</label>
            <input type="text" name="city" value="{cfg['city']}" placeholder="np. Poznań">

            <label>Szerokość geograficzna (latitude)</label>
            <input type="text" name="latitude" value="{cfg['latitude']}" placeholder="np. 52.4064">

            <label>Długość geograficzna (longitude)</label>
            <input type="text" name="longitude" value="{cfg['longitude']}" placeholder="np. 16.9252">

            <br><br>
            <button type="submit" class="btn btn-primary" style="width:100%">💾 Zapisz konfigurację</button>
        </form>
    </div>

    <!-- Oferty -->
    {_render_olx_offers(oferty) if oferty else '<div class="status-card"><p style="color:#94a3b8;text-align:center">Brak ogłoszeń OLX. Przejdź do produktu i kliknij "Wystaw na OLX".</p></div>'}

</div>

<nav style="position:fixed;bottom:0;left:0;right:0;background:#1e293b;border-top:1px solid #334155;display:flex;justify-content:space-around;padding:8px 0;z-index:100">
<a href="/" style="text-align:center;text-decoration:none;color:#94a3b8;font-size:0.7rem"><div style="font-size:1.4rem">🏠</div>Home</a>
<a href="/magazyn" style="text-align:center;text-decoration:none;color:#94a3b8;font-size:0.7rem"><div style="font-size:1.4rem">📦</div>Magazyn</a>
<a href="/paletomat" style="text-align:center;text-decoration:none;color:#94a3b8;font-size:0.7rem"><div style="font-size:1.4rem">🤖</div>Paletomat</a>
<a href="/allegro" style="text-align:center;text-decoration:none;color:#94a3b8;font-size:0.7rem"><div style="font-size:1.4rem">🛒</div>Allegro</a>
<a href="/olx" style="text-align:center;text-decoration:none;color:#38bdf8;font-size:0.7rem"><div style="font-size:1.4rem">🏪</div>OLX</a>
<a href="/vinted" style="text-align:center;text-decoration:none;color:#94a3b8;font-size:0.7rem"><div style="font-size:1.4rem">👗</div>Vinted</a>
<a href="/narzedzia" style="text-align:center;text-decoration:none;color:#94a3b8;font-size:0.7rem"><div style="font-size:1.4rem">⚡</div>Narzędzia</a>
</nav>

</body></html>'''


def _render_olx_offers(oferty):
    """Renderuje listę ofert OLX"""
    html = '<div class="status-card"><h3>📋 Twoje ogłoszenia OLX</h3>'
    for o in oferty:
        status_badge = {
            'active': '<span class="status-badge badge-green">aktywne</span>',
            'draft': '<span class="status-badge badge-yellow">draft</span>',
            'limited': '<span class="status-badge badge-yellow">ograniczone</span>',
            'removed': '<span class="status-badge badge-red">usunięte</span>',
        }.get(o['status'], f'<span class="status-badge badge-yellow">{o["status"]}</span>')

        img = o['zdjecie_url'] or ''
        if img and not img.startswith('http'):
            img = f'/static/downloads/{img}' if not img.startswith('/') else img

        html += f'''
        <div class="offer-card">
            <img src="{img}" class="offer-img" onerror="this.style.display='none'">
            <div class="offer-info">
                <div class="title">{o['tytul'] or o.get('produkt_nazwa', '?')}</div>
                <div class="meta">{status_badge} · OLX ID: {o['olx_advert_id']}</div>
            </div>
            <div class="offer-price">{o.get('cena', 0):.0f} zł</div>
        </div>'''
    html += '</div>'
    return html


@olx_bp.route('/config', methods=['POST'])
def olx_config_save():
    """Zapisuje konfigurację OLX"""
    set_config('olx_client_id', request.form.get('client_id', '').strip())
    set_config('olx_client_secret', request.form.get('client_secret', '').strip())
    set_config('olx_redirect_uri', request.form.get('redirect_uri', '').strip())
    set_config('olx_contact_name', request.form.get('contact_name', '').strip())
    set_config('olx_contact_phone', request.form.get('contact_phone', '').strip())
    set_config('olx_city', request.form.get('city', '').strip())
    set_config('olx_latitude', request.form.get('latitude', '').strip())
    set_config('olx_longitude', request.form.get('longitude', '').strip())
    flash('✅ Konfiguracja OLX zapisana', 'success')
    return redirect('/olx')


@olx_bp.route('/auth')
def olx_auth():
    """Rozpoczyna OAuth2 flow z OLX"""
    cfg = get_olx_config()
    if not cfg['client_id']:
        flash('❌ Najpierw skonfiguruj Client ID i Secret', 'error')
        return redirect('/olx')

    # Generuj state dla bezpieczeństwa
    import secrets
    state = secrets.token_hex(16)
    set_config('olx_oauth_state', state)

    params = {
        'client_id': cfg['client_id'],
        'response_type': 'code',
        'redirect_uri': cfg['redirect_uri'],
        'scope': 'read write v2',
        'state': state,
    }
    auth_url = f"{OLX_AUTH_URL}?{urlencode(params)}"
    return redirect(auth_url)


@olx_bp.route('/callback')
def olx_callback():
    """Callback OAuth2 z OLX"""
    code = request.args.get('code')
    state = request.args.get('state')
    error = request.args.get('error')

    if error:
        flash(f'❌ OLX auth error: {error}', 'error')
        return redirect('/olx')

    if not code:
        flash('❌ Brak kodu autoryzacji', 'error')
        return redirect('/olx')

    # Weryfikuj state
    saved_state = get_config('olx_oauth_state', '')
    if state != saved_state:
        flash('❌ Nieprawidłowy state - możliwy atak CSRF', 'error')
        return redirect('/olx')

    cfg = get_olx_config()

    # Wymień code na token
    try:
        response = requests.post(OLX_TOKEN_URL, data={
            'grant_type': 'authorization_code',
            'client_id': cfg['client_id'],
            'client_secret': cfg['client_secret'],
            'redirect_uri': cfg['redirect_uri'],
            'code': code,
        }, timeout=30)

        if response.status_code == 200:
            tokens = response.json()
            set_config('olx_access_token', tokens['access_token'])
            if 'refresh_token' in tokens:
                set_config('olx_refresh_token', tokens['refresh_token'])
            expires_at = datetime.now() + timedelta(seconds=tokens.get('expires_in', 3600))
            set_config('olx_token_expires', expires_at.isoformat())
            flash('✅ Zalogowano do OLX!', 'success')
        else:
            flash(f'❌ Błąd tokena OLX: {response.status_code} {response.text[:200]}', 'error')

    except Exception as e:
        flash(f'❌ Błąd połączenia z OLX: {e}', 'error')

    return redirect('/olx')


@olx_bp.route('/logout', methods=['POST'])
def olx_logout():
    """Wylogowuje z OLX"""
    set_config('olx_access_token', '')
    set_config('olx_refresh_token', '')
    set_config('olx_token_expires', '')
    flash('✅ Wylogowano z OLX', 'success')
    return redirect('/olx')


@olx_bp.route('/create/<int:produkt_id>')
def olx_create_listing(produkt_id):
    """Tworzy ogłoszenie OLX z produktu"""
    if not is_authenticated():
        flash('❌ Najpierw zaloguj się do OLX', 'error')
        return redirect('/olx')

    advert_id, err = create_olx_listing(produkt_id)
    if err:
        flash(f'❌ {err}', 'error')
    else:
        flash(f'✅ Utworzono ogłoszenie OLX (ID: {advert_id}) - draft. Kliknij "Opublikuj" aby aktywować.', 'success')

    return redirect(request.referrer or '/olx')


@olx_bp.route('/publish/<advert_id>')
def olx_publish_listing(advert_id):
    """Publikuje ogłoszenie OLX"""
    if not is_authenticated():
        flash('❌ Najpierw zaloguj się do OLX', 'error')
        return redirect('/olx')

    success, err = publish_olx_listing(advert_id)
    if err:
        flash(f'❌ {err}', 'error')
    else:
        flash('✅ Ogłoszenie opublikowane na OLX!', 'success')

    return redirect('/olx')


@olx_bp.route('/delete/<advert_id>', methods=['POST'])
def olx_delete_listing(advert_id):
    """Usuwa ogłoszenie OLX"""
    if not is_authenticated():
        flash('❌ Najpierw zaloguj się do OLX', 'error')
        return redirect('/olx')

    result, err = olx_api_request('DELETE', f'/adverts/{advert_id}')
    if err:
        flash(f'❌ {err}', 'error')
    else:
        try:
            conn = get_db()
            conn.execute('DELETE FROM olx_oferty WHERE olx_advert_id = ?', (str(advert_id),))
            conn.commit()
        except:
            pass
        flash('✅ Ogłoszenie usunięte z OLX', 'success')

    return redirect('/olx')


@olx_bp.route('/api/status')
def olx_api_status():
    """Status API OLX"""
    return jsonify({
        'configured': is_configured(),
        'authenticated': is_authenticated(),
        'config': {
            'client_id': bool(get_config('olx_client_id', '')),
            'has_token': bool(get_config('olx_access_token', '')),
        }
    })
