"""
Vinted API module - nieoficjalna integracja z Vinted.pl
Cookie-based auth + internal API v2 (bez Vinted Pro)
Działa przez sesję przeglądarkową - user loguje się raz, cookies są zapisywane.
"""

import os
import json
import time
import re
import requests
from datetime import datetime, timedelta
from flask import Blueprint, request, redirect, jsonify, flash
from pathlib import Path

from .database import get_db, get_config, set_config

vinted_bp = Blueprint('vinted', __name__)

# ============================================================
# KONFIGURACJA
# ============================================================
VINTED_BASE_URL = "https://www.vinted.pl"
VINTED_API_URL = f"{VINTED_BASE_URL}/api/v2"

MAX_RETRIES = 3
RETRY_DELAY = 2

# Plik na cookies
_APP_DIR = Path(__file__).parent.parent
COOKIES_FILE = str(_APP_DIR / 'vinted_cookies.json')

# Realistyczny User-Agent
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'


# ============================================================
# SESSION MANAGEMENT
# ============================================================

def _get_session():
    """Tworzy requests session z zapisanymi cookies"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': USER_AGENT,
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
        'Sec-Ch-Ua': '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
    })

    # Załaduj zapisane cookies
    cookies = _load_cookies()
    if cookies:
        for name, value in cookies.items():
            session.cookies.set(name, value, domain='.vinted.pl')

    return session


def _save_cookies(cookies_dict):
    """Zapisuje cookies do pliku"""
    try:
        with open(COOKIES_FILE, 'w') as f:
            json.dump(cookies_dict, f)
        print(f"✅ Vinted cookies saved ({len(cookies_dict)} cookies)")
    except Exception as e:
        print(f"❌ Vinted cookies save error: {e}")


def _load_cookies():
    """Wczytuje cookies z pliku"""
    try:
        if os.path.exists(COOKIES_FILE):
            with open(COOKIES_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️ Vinted cookies load error: {e}")
    return {}


def _get_csrf_token(session):
    """Pobiera CSRF token ze strony Vinted"""
    try:
        resp = session.get(f'{VINTED_BASE_URL}/items/new', timeout=15)
        if resp.status_code == 200:
            # Szukaj meta csrf-token
            match = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', resp.text)
            if match:
                return match.group(1)
            # Alternatywnie w JSON config
            match = re.search(r'"csrfToken":"([^"]+)"', resp.text)
            if match:
                return match.group(1)
        print(f"⚠️ CSRF token not found (status: {resp.status_code})")
    except Exception as e:
        print(f"❌ CSRF token error: {e}")
    return None


def is_configured():
    """Sprawdza czy mamy cookies Vinted"""
    cookies = _load_cookies()
    return bool(cookies and len(cookies) > 0)


def is_authenticated():
    """Sprawdza czy sesja Vinted jest aktywna"""
    if not is_configured():
        return False

    # Quick check - spróbuj pobrać profil
    try:
        session = _get_session()
        resp = session.get(f'{VINTED_API_URL}/users/current', timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('user', {}).get('id'):
                return True
    except:
        pass
    return False


def get_current_user():
    """Pobiera dane zalogowanego użytkownika"""
    try:
        session = _get_session()
        resp = session.get(f'{VINTED_API_URL}/users/current', timeout=10)
        if resp.status_code == 200:
            return resp.json().get('user', {})
    except:
        pass
    return None


# ============================================================
# API REQUESTS
# ============================================================

def vinted_api_request(method, endpoint, data=None, files=None):
    """Wykonuje request do Vinted internal API"""
    session = _get_session()

    # Pobierz CSRF token dla operacji zapisu
    csrf_token = None
    if method in ('POST', 'PUT', 'DELETE'):
        csrf_token = _get_csrf_token(session)
        if csrf_token:
            session.headers['X-CSRF-Token'] = csrf_token

    url = f"{VINTED_API_URL}{endpoint}"

    for attempt in range(MAX_RETRIES):
        try:
            if method == 'GET':
                resp = session.get(url, params=data, timeout=30)
            elif method == 'POST':
                if files:
                    resp = session.post(url, files=files, data=data, timeout=60)
                else:
                    resp = session.post(url, json=data, timeout=30)
            elif method == 'PUT':
                resp = session.put(url, json=data, timeout=30)
            elif method == 'DELETE':
                resp = session.delete(url, timeout=30)
            else:
                return None, f"Nieznana metoda: {method}"

            # Zapisz zaktualizowane cookies
            if session.cookies:
                all_cookies = {c.name: c.value for c in session.cookies}
                if all_cookies:
                    _save_cookies(all_cookies)

            if resp.status_code == 401:
                return None, "Sesja wygasła - zaloguj się ponownie"

            if resp.status_code == 403:
                return None, "Dostęp zabroniony - sprawdź cookies"

            if resp.status_code == 429:
                wait = int(resp.headers.get('Retry-After', 30))
                print(f"⚠️ Vinted rate limit, waiting {wait}s...")
                time.sleep(wait)
                continue

            if 200 <= resp.status_code < 300:
                try:
                    return resp.json(), None
                except:
                    return {'status': 'ok'}, None

            return None, f"HTTP {resp.status_code}: {resp.text[:300]}"

        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                return None, f"Błąd połączenia: {e}"

    return None, "Max retries exceeded"


# ============================================================
# MAPOWANIE DANYCH
# ============================================================

def map_condition(stan):
    """Mapuje stan na status_id Vinted"""
    # 1=nowy z metką, 2=nowy bez metki, 3=bardzo dobry, 4=dobry, 5=zadowalający
    mapping = {
        'Nowy': 1,
        'Nowy bez metki': 2,
        'Używany': 4,
        'Powystawkowy': 3,
        'Uszkodzony': 5,
    }
    return mapping.get(stan, 4)


def _detect_brand(nazwa):
    """Wykrywa markę z nazwy produktu"""
    brands = {
        'UGREEN': 319092, 'JBL': 2912, 'Sony': 150, 'Anker': 318684,
        'Samsung': 14, 'Apple': 3, 'Xiaomi': 320172, 'Philips': 167,
        'Nike': 53, 'Adidas': 14, 'Reebok': 129, 'Puma': 73,
        'PETKIT': None, 'TP-Link': None, 'Creality': None,
        'Alpicool': None, 'Niimbot': None, 'EZVIZ': None,
    }
    nazwa_upper = (nazwa or '').upper()
    for brand, brand_id in brands.items():
        if brand.upper() in nazwa_upper:
            return brand, brand_id
    return None, None


# ============================================================
# TWORZENIE OGŁOSZENIA
# ============================================================

def upload_photo_vinted(session, image_path_or_url, csrf_token=None):
    """Uploaduje zdjęcie na Vinted i zwraca photo_id"""
    if csrf_token:
        session.headers['X-CSRF-Token'] = csrf_token

    try:
        # Pobierz zdjęcie jeśli URL
        if isinstance(image_path_or_url, str) and image_path_or_url.startswith('http'):
            img_resp = requests.get(image_path_or_url, timeout=30, headers={
                'User-Agent': USER_AGENT
            })
            if img_resp.status_code != 200:
                return None
            from io import BytesIO
            photo_data = BytesIO(img_resp.content)
            filename = 'photo.jpg'
        elif isinstance(image_path_or_url, str) and os.path.exists(image_path_or_url):
            photo_data = open(image_path_or_url, 'rb')
            filename = os.path.basename(image_path_or_url)
        else:
            return None

        files = {'photo[file]': (filename, photo_data, 'image/jpeg')}
        resp = session.post(f'{VINTED_API_URL}/photos', files=files, timeout=60)

        if resp.status_code in (200, 201):
            data = resp.json()
            photo_id = data.get('photo', {}).get('id') or data.get('id')
            print(f"✅ Vinted photo uploaded: {photo_id}")
            return photo_id
        else:
            print(f"❌ Vinted photo upload: {resp.status_code} {resp.text[:200]}")

    except Exception as e:
        print(f"❌ Vinted photo upload error: {e}")

    return None


def create_vinted_item(produkt_id):
    """Tworzy ogłoszenie na Vinted z produktu w bazie"""
    conn = get_db()
    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()

    if not produkt:
        return None, "Nie znaleziono produktu"

    session = _get_session()

    # Pobierz CSRF token i temp_uuid z /items/new
    csrf_token = _get_csrf_token(session)
    if not csrf_token:
        return None, "Nie udało się pobrać CSRF token - zaloguj się ponownie"

    session.headers['X-CSRF-Token'] = csrf_token

    # Przygotuj dane
    nazwa = produkt['krotki_tytul'] or produkt['nazwa'] or ''
    if len(nazwa) > 150:
        nazwa = nazwa[:147] + '...'
    if len(nazwa) < 3:
        nazwa = f"Produkt {produkt['ean'] or produkt_id}"

    opis = produkt['opis_ai'] or produkt['nazwa'] or ''
    if len(opis) < 10:
        opis = f"{opis}\nStan: {produkt['stan'] or 'Nowy'}. Wysyłka w 24h."

    cena = produkt['cena_allegro'] or produkt['cena_brutto'] or 0
    if cena <= 0:
        return None, "Produkt nie ma ceny"

    # Upload zdjęć
    photo_ids = []
    images_to_upload = []
    if produkt['zdjecie_url']:
        url = produkt['zdjecie_url']
        if url.startswith('/static/'):
            url = str(_APP_DIR / url.lstrip('/'))
        images_to_upload.append(url)
    if produkt.get('images'):
        try:
            extra = json.loads(produkt['images']) if isinstance(produkt['images'], str) else []
            for img in extra[:4]:  # Max 5 zdjęć na start
                if img.startswith('/static/'):
                    img = str(_APP_DIR / img.lstrip('/'))
                images_to_upload.append(img)
        except:
            pass

    for img in images_to_upload[:5]:
        photo_id = upload_photo_vinted(session, img, csrf_token)
        if photo_id:
            photo_ids.append({'id': photo_id, 'orientation': 0})

    # Marka
    brand_name, brand_id = _detect_brand(produkt['nazwa'])

    # Payload
    item_data = {
        'item': {
            'id': None,
            'currency': 'PLN',
            'title': nazwa,
            'description': opis,
            'status_id': map_condition(produkt['stan']),
            'price': f"{cena:.2f}",
            'color_ids': [],
            'assigned_photos': photo_ids,
        }
    }

    if brand_id:
        item_data['item']['brand_id'] = brand_id

    # Wyślij
    resp_data, err = vinted_api_request('POST', '/items', item_data)
    if err:
        return None, f"Błąd Vinted: {err}"

    # Wyciąg ID
    vinted_item_id = None
    if resp_data:
        item = resp_data.get('item', resp_data)
        vinted_item_id = item.get('id') or item.get('item_id')

    # Zapisz w bazie
    try:
        conn.execute('''
            INSERT OR REPLACE INTO vinted_items
                (produkt_id, vinted_item_id, tytul, cena, status, data_utworzenia)
            VALUES (?, ?, ?, ?, 'active', CURRENT_TIMESTAMP)
        ''', (produkt_id, str(vinted_item_id or ''), nazwa, cena))
        conn.commit()
    except Exception as e:
        print(f"⚠️ Vinted DB save: {e}")

    return vinted_item_id, None


# ============================================================
# ROUTES
# ============================================================

@vinted_bp.route('/')
def vinted_home():
    """Strona główna Vinted"""
    auth = is_authenticated()
    user = get_current_user() if auth else None
    username = user.get('login', '?') if user else ''

    conn = get_db()
    items = []
    stats = {'active': 0, 'total': 0}
    moje_produkty = []

    try:
        items = conn.execute('''
            SELECT v.*, p.nazwa as produkt_nazwa, p.zdjecie_url, p.cena_allegro
            FROM vinted_items v
            LEFT JOIN produkty p ON v.produkt_id = p.id
            ORDER BY v.data_utworzenia DESC
            LIMIT 50
        ''').fetchall()

        counts = conn.execute('''
            SELECT status, COUNT(*) as cnt FROM vinted_items GROUP BY status
        ''').fetchall()
        for row in counts:
            stats[row['status']] = row['cnt']
            stats['total'] += row['cnt']

        # Osobiste produkty (nie z palet) - do sprzedaży na Vinted
        moje_produkty = conn.execute('''
            SELECT * FROM produkty
            WHERE paleta_id IS NULL AND dostawca = 'osobiste' AND ilosc > 0
            ORDER BY data_dodania DESC
            LIMIT 50
        ''').fetchall()
    except:
        pass

    return f'''<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Vinted - AKCES HUB</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:#0f172a; color:#e2e8f0; min-height:100vh; padding-bottom:80px; }}
.header {{ background:linear-gradient(135deg,#09b1ba,#1db5b9); padding:20px; text-align:center; }}
.header h1 {{ font-size:1.5rem; color:#fff; }}
.header .subtitle {{ color:rgba(255,255,255,0.8); font-size:0.85rem; margin-top:4px; }}
.container {{ max-width:800px; margin:0 auto; padding:16px; }}
.card {{ background:#1e293b; border-radius:12px; padding:20px; margin:16px 0; }}
.card h3 {{ font-size:1rem; margin-bottom:12px; }}
.badge {{ display:inline-block; padding:4px 12px; border-radius:20px; font-size:0.8rem; font-weight:600; }}
.badge-green {{ background:#065f46; color:#6ee7b7; }}
.badge-red {{ background:#7f1d1d; color:#fca5a5; }}
.stats-row {{ display:flex; gap:12px; margin:16px 0; }}
.stat-box {{ flex:1; background:#1e293b; border-radius:10px; padding:16px; text-align:center; }}
.stat-box .num {{ font-size:1.5rem; font-weight:700; color:#09b1ba; }}
.stat-box .label {{ font-size:0.75rem; color:#94a3b8; margin-top:4px; }}
.btn {{ display:inline-block; padding:12px 24px; border-radius:10px; text-decoration:none; font-weight:600; font-size:0.9rem; border:none; cursor:pointer; text-align:center; }}
.btn-primary {{ background:#09b1ba; color:#fff; }}
.info {{ background:rgba(9,177,186,0.1); border:1px solid rgba(9,177,186,0.3); border-radius:10px; padding:14px; margin:12px 0; font-size:0.85rem; color:#94a3b8; }}
.form-group {{ margin:12px 0; }}
.form-group label {{ display:block; color:#94a3b8; font-size:0.8rem; margin-bottom:4px; }}
.form-group input, .form-group select, .form-group textarea {{ width:100%; padding:10px; border-radius:8px; border:1px solid #334155; background:#0f172a; color:#e2e8f0; font-size:0.85rem; }}
.form-group textarea {{ font-family:monospace; min-height:120px; }}
.form-group input:focus, .form-group select:focus, .form-group textarea:focus {{ outline:none; border-color:#09b1ba; }}
.form-row {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
.btn-green {{ background:#059669; color:#fff; }}
.btn-red {{ background:#dc2626; color:#fff; font-size:0.75rem; padding:6px 12px; }}
.my-product {{ background:#0f172a; border:1px solid #334155; border-radius:10px; padding:12px; margin:8px 0; display:flex; gap:12px; align-items:center; }}
.my-product .mp-img {{ width:55px; height:55px; border-radius:8px; object-fit:cover; background:#334155; }}
.my-product .mp-info {{ flex:1; }}
.my-product .mp-name {{ font-size:0.9rem; font-weight:600; }}
.my-product .mp-meta {{ font-size:0.75rem; color:#94a3b8; margin-top:2px; }}
.my-product .mp-price {{ font-weight:700; color:#6ee7b7; font-size:1rem; }}
.my-product .mp-actions {{ display:flex; gap:6px; flex-direction:column; }}
.offer-card {{ background:#1e293b; border-radius:10px; padding:14px; margin:8px 0; display:flex; gap:12px; align-items:center; }}
.offer-img {{ width:50px; height:50px; border-radius:8px; object-fit:cover; background:#334155; }}
.offer-info {{ flex:1; }}
.offer-info .title {{ font-size:0.9rem; font-weight:600; }}
.offer-info .meta {{ font-size:0.75rem; color:#94a3b8; margin-top:2px; }}
.offer-price {{ font-weight:700; color:#6ee7b7; }}
a {{ color:#09b1ba; }}
</style>
<link rel="stylesheet" href="/static/kiosk.css">
</head>
<body>
<script>if(localStorage.getItem('kiosk_mode')==='1')document.body.classList.add('kiosk');</script>

<div class="header">
    <h1>👗 Vinted Integration</h1>
    <div class="subtitle">Wystawiaj produkty na Vinted.pl</div>
</div>

<div class="container">

    <div class="card">
        <h3>Status połączenia</h3>
        {'<span class="badge badge-green">✅ Zalogowano jako ' + username + '</span>' if auth else
         '<span class="badge badge-red">❌ Nie zalogowano</span>'}
    </div>

    <div class="stats-row">
        <div class="stat-box">
            <div class="num">{stats['total']}</div>
            <div class="label">Ogłoszeń</div>
        </div>
        <div class="stat-box">
            <div class="num">{stats.get('active', 0)}</div>
            <div class="label">Aktywnych</div>
        </div>
    </div>

    <div class="info">
        <b>Jak się połączyć z Vinted:</b><br><br>
        <b>Metoda 1 - Cookies z przeglądarki (zalecana):</b><br>
        1. Zaloguj się na <a href="https://www.vinted.pl" target="_blank">vinted.pl</a> w Chrome<br>
        2. Naciśnij F12 → zakładka Application → Cookies → vinted.pl<br>
        3. Skopiuj wartości cookies: <code>_vinted_fr_session</code>, <code>access_token_web</code><br>
        4. Wklej poniżej jako JSON<br><br>
        <b>Metoda 2 - Rozszerzenie EditThisCookie:</b><br>
        1. Zainstaluj <a href="https://chrome.google.com/webstore/detail/editthiscookie/fngmhnnpilhplaeedifhccceomclgfbg" target="_blank">EditThisCookie</a><br>
        2. Wejdź na vinted.pl (zalogowany)<br>
        3. Kliknij ikonkę → Export → wklej JSON poniżej
    </div>

    <div class="card">
        <h3>🍪 Wklej cookies z Vinted</h3>
        <form method="POST" action="/vinted/save-cookies">
            <div class="form-group">
                <label>Cookies JSON (z EditThisCookie lub ręcznie):</label>
                <textarea name="cookies_json" placeholder='{{"_vinted_fr_session": "wartość...", "access_token_web": "wartość..."}}'></textarea>
            </div>
            <button type="submit" class="btn btn-primary" style="width:100%">💾 Zapisz cookies</button>
        </form>
    </div>

    <div class="card">
        <h3>👟 Dodaj produkt (osobisty)</h3>
        <p style="font-size:0.8rem;color:#94a3b8;margin-bottom:12px">Dodaj buty, ubrania lub inne rzeczy do sprzedaży na Vinted - osobno od paletowego magazynu.</p>
        <form method="POST" action="/vinted/add-product">
            <div class="form-group">
                <label>Nazwa produktu *</label>
                <input type="text" name="nazwa" required placeholder="np. Nike Air Max 90 rozmiar 42">
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Marka</label>
                    <input type="text" name="marka" placeholder="np. Nike, Adidas">
                </div>
                <div class="form-group">
                    <label>Rozmiar</label>
                    <input type="text" name="rozmiar" placeholder="np. 42, M, L">
                </div>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Kategoria</label>
                    <select name="kategoria">
                        <option value="buty">👟 Buty</option>
                        <option value="ubrania">👕 Ubrania</option>
                        <option value="akcesoria">👜 Akcesoria</option>
                        <option value="elektronika">📱 Elektronika</option>
                        <option value="inne">📦 Inne</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Stan</label>
                    <select name="stan">
                        <option value="Nowy">Nowy z metką</option>
                        <option value="Nowy bez metki">Nowy bez metki</option>
                        <option value="Używany" selected>Używany - dobry</option>
                        <option value="Uszkodzony">Uszkodzony</option>
                    </select>
                </div>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Cena (PLN) *</label>
                    <input type="number" name="cena" required min="1" step="1" placeholder="np. 150">
                </div>
                <div class="form-group">
                    <label>Koszt zakupu (PLN)</label>
                    <input type="number" name="koszt" min="0" step="0.01" placeholder="opcjonalnie">
                </div>
            </div>
            <div class="form-group">
                <label>URL zdjęcia (opcjonalnie)</label>
                <input type="text" name="zdjecie_url" placeholder="https://... lub zostaw puste">
            </div>
            <button type="submit" class="btn btn-green" style="width:100%;margin-top:8px">➕ Dodaj produkt</button>
        </form>
    </div>

    {_render_moje_produkty(moje_produkty, auth)}

    {_render_vinted_items(items) if items else '<div class="card"><p style="color:#94a3b8;text-align:center">Brak ogłoszeń na Vinted.</p></div>'}

</div>

<nav style="position:fixed;bottom:0;left:0;right:0;background:#1e293b;border-top:1px solid #334155;display:flex;justify-content:space-around;padding:8px 0;z-index:100">
<a href="/" style="text-align:center;text-decoration:none;color:#94a3b8;font-size:0.7rem"><div style="font-size:1.4rem">🏠</div>Home</a>
<a href="/magazyn" style="text-align:center;text-decoration:none;color:#94a3b8;font-size:0.7rem"><div style="font-size:1.4rem">📦</div>Magazyn</a>
<a href="/paletomat" style="text-align:center;text-decoration:none;color:#94a3b8;font-size:0.7rem"><div style="font-size:1.4rem">🤖</div>Paletomat</a>
<a href="/allegro" style="text-align:center;text-decoration:none;color:#94a3b8;font-size:0.7rem"><div style="font-size:1.4rem">🛒</div>Allegro</a>
<a href="/olx" style="text-align:center;text-decoration:none;color:#94a3b8;font-size:0.7rem"><div style="font-size:1.4rem">🏪</div>OLX</a>
<a href="/vinted" style="text-align:center;text-decoration:none;color:#09b1ba;font-size:0.7rem"><div style="font-size:1.4rem">👗</div>Vinted</a>
<a href="/narzedzia" style="text-align:center;text-decoration:none;color:#94a3b8;font-size:0.7rem"><div style="font-size:1.4rem">⚡</div>Narzędzia</a>
</nav>

</body></html>'''


def _render_moje_produkty(produkty, auth=False):
    """Renderuje listę osobistych produktów do sprzedaży na Vinted"""
    if not produkty:
        return ''

    html = '<div class="card"><h3>👟 Moje produkty (osobiste)</h3>'
    for p in produkty:
        img = p['zdjecie_url'] or ''
        if img and not img.startswith('http'):
            img = f'/static/downloads/{img}' if not img.startswith('/') else img
        img_tag = f'<img src="{img}" class="mp-img" onerror="this.style.display=\'none\'">' if img else '<div class="mp-img" style="display:flex;align-items:center;justify-content:center;font-size:1.5rem">👟</div>'

        meta_parts = []
        if p.get('kategoria'):
            meta_parts.append(p['kategoria'])
        if p.get('stan'):
            meta_parts.append(p['stan'])
        meta = ' · '.join(meta_parts)

        vinted_btn = f'<a href="/vinted/create/{p["id"]}" class="btn btn-primary" style="font-size:0.75rem;padding:6px 12px">👗 Wystaw</a>' if auth else ''

        html += f'''
        <div class="my-product">
            {img_tag}
            <div class="mp-info">
                <div class="mp-name">{p['nazwa'][:50]}</div>
                <div class="mp-meta">{meta} · {p['ilosc']} szt</div>
            </div>
            <div class="mp-price">{p.get('cena_allegro') or p.get('cena_brutto') or 0:.0f} zł</div>
            <div class="mp-actions">
                {vinted_btn}
                <form method="POST" action="/vinted/delete-product/{p['id']}" style="margin:0">
                    <button type="submit" class="btn btn-red" onclick="return confirm('Usunąć?')">🗑️</button>
                </form>
            </div>
        </div>'''
    html += '</div>'
    return html


def _render_vinted_items(items):
    """Renderuje listę ogłoszeń Vinted"""
    html = '<div class="card"><h3>📋 Twoje ogłoszenia Vinted</h3>'
    for item in items:
        status_colors = {
            'active': ('badge-green', 'aktywne'),
            'sold': ('badge-green', 'sprzedane'),
            'failed': ('badge-red', 'błąd'),
        }
        cls, label = status_colors.get(item['status'], ('', item['status']))

        img = item['zdjecie_url'] or ''
        if img and not img.startswith('http'):
            img = f'/static/downloads/{img}' if not img.startswith('/') else img

        html += f'''
        <div class="offer-card">
            <img src="{img}" class="offer-img" onerror="this.style.display='none'">
            <div class="offer-info">
                <div class="title">{item['tytul'] or item.get('produkt_nazwa', '?')}</div>
                <div class="meta"><span class="badge {cls}">{label}</span> · ID: {item['vinted_item_id'] or '-'}</div>
            </div>
            <div class="offer-price">{item.get('cena', 0):.0f} zł</div>
        </div>'''
    html += '</div>'
    return html


@vinted_bp.route('/save-cookies', methods=['POST'])
def vinted_save_cookies():
    """Zapisuje cookies Vinted"""
    raw = request.form.get('cookies_json', '').strip()
    if not raw:
        flash('❌ Wklej cookies JSON', 'error')
        return redirect('/vinted')

    try:
        cookies = json.loads(raw)

        # Obsługa formatu EditThisCookie (lista obiektów)
        if isinstance(cookies, list):
            cookies_dict = {}
            for c in cookies:
                if isinstance(c, dict) and 'name' in c and 'value' in c:
                    cookies_dict[c['name']] = c['value']
            cookies = cookies_dict

        if not isinstance(cookies, dict) or len(cookies) == 0:
            flash('❌ Nieprawidłowy format JSON. Użyj {"nazwa": "wartość", ...}', 'error')
            return redirect('/vinted')

        _save_cookies(cookies)

        # Sprawdź czy działa
        if is_authenticated():
            user = get_current_user()
            name = user.get('login', '?') if user else '?'
            flash(f'✅ Zalogowano na Vinted jako {name}!', 'success')
        else:
            flash('⚠️ Cookies zapisane, ale sesja nie działa. Upewnij się że jesteś zalogowany na vinted.pl i skopiuj świeże cookies.', 'warning')

    except json.JSONDecodeError:
        flash('❌ Nieprawidłowy JSON. Skopiuj dokładnie z EditThisCookie.', 'error')

    return redirect('/vinted')


@vinted_bp.route('/add-product', methods=['POST'])
def vinted_add_product():
    """Dodaje osobisty produkt do sprzedaży na Vinted"""
    nazwa = request.form.get('nazwa', '').strip()
    marka = request.form.get('marka', '').strip()
    rozmiar = request.form.get('rozmiar', '').strip()
    kategoria = request.form.get('kategoria', 'inne')
    stan = request.form.get('stan', 'Używany')
    cena = float(request.form.get('cena', 0) or 0)
    koszt = float(request.form.get('koszt', 0) or 0)
    zdjecie_url = request.form.get('zdjecie_url', '').strip()

    if not nazwa or cena <= 0:
        flash('❌ Podaj nazwę i cenę produktu', 'error')
        return redirect('/vinted')

    # Dodaj markę/rozmiar do nazwy jeśli podane
    krotki_tytul = nazwa
    if marka and marka.lower() not in nazwa.lower():
        krotki_tytul = f"{marka} {nazwa}"
    if rozmiar:
        krotki_tytul = f"{krotki_tytul} r.{rozmiar}"

    try:
        conn = get_db()
        conn.execute('''
            INSERT INTO produkty (nazwa, krotki_tytul, kategoria, stan, cena_allegro, cena_brutto, cena_netto,
                                  ilosc, dostawca, paleta_id, zdjecie_url, vendor, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'osobiste', NULL, ?, ?, 'magazyn')
        ''', (nazwa, krotki_tytul, kategoria, stan, cena, koszt, round(koszt / 1.23, 2),
              zdjecie_url, marka))
        conn.commit()
        flash(f'✅ Dodano: {krotki_tytul} - {cena:.0f} zł', 'success')
    except Exception as e:
        flash(f'❌ Błąd: {e}', 'error')

    return redirect('/vinted')


@vinted_bp.route('/delete-product/<int:produkt_id>', methods=['POST'])
def vinted_delete_product(produkt_id):
    """Usuwa osobisty produkt"""
    try:
        conn = get_db()
        # Tylko osobiste produkty
        conn.execute('DELETE FROM produkty WHERE id = ? AND dostawca = ? AND paleta_id IS NULL',
                     (produkt_id, 'osobiste'))
        conn.commit()
        flash('✅ Usunięto produkt', 'success')
    except Exception as e:
        flash(f'❌ Błąd: {e}', 'error')
    return redirect('/vinted')


@vinted_bp.route('/logout', methods=['POST'])
def vinted_logout():
    """Usuwa cookies Vinted"""
    try:
        if os.path.exists(COOKIES_FILE):
            os.remove(COOKIES_FILE)
    except:
        pass
    flash('✅ Wylogowano z Vinted', 'success')
    return redirect('/vinted')


@vinted_bp.route('/create/<int:produkt_id>')
def vinted_create_item_route(produkt_id):
    """Tworzy ogłoszenie na Vinted"""
    if not is_authenticated():
        flash('❌ Najpierw zaloguj się do Vinted (wklej cookies)', 'error')
        return redirect('/vinted')

    item_id, err = create_vinted_item(produkt_id)
    if err:
        flash(f'❌ {err}', 'error')
    else:
        flash(f'✅ Wystawiono na Vinted! (ID: {item_id})', 'success')

    return redirect(request.referrer or '/vinted')


@vinted_bp.route('/delete/<vinted_item_id>', methods=['POST'])
def vinted_delete_item(vinted_item_id):
    """Usuwa ogłoszenie z Vinted"""
    if not is_authenticated():
        flash('❌ Najpierw zaloguj się', 'error')
        return redirect('/vinted')

    result, err = vinted_api_request('DELETE', f'/items/{vinted_item_id}')
    if err:
        flash(f'❌ {err}', 'error')
    else:
        try:
            conn = get_db()
            conn.execute('DELETE FROM vinted_items WHERE vinted_item_id = ?', (vinted_item_id,))
            conn.commit()
        except:
            pass
        flash('✅ Usunięto z Vinted', 'success')

    return redirect('/vinted')


@vinted_bp.route('/api/status')
def vinted_api_status():
    """Status API Vinted"""
    user = get_current_user() if is_authenticated() else None
    return jsonify({
        'configured': is_configured(),
        'authenticated': is_authenticated(),
        'user': user.get('login') if user else None,
    })
