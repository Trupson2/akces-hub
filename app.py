#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔═══════════════════════════════════════════════════════════════╗
║                      AKCES HUB v2.7.0                         ║
║          Paletomat + Magazynier + Telegram w jednym           ║
╠═══════════════════════════════════════════════════════════════╣
║  Uruchomienie:  python app.py                                 ║
║  Adres:         http://127.0.0.1:5000                         ║
╚═══════════════════════════════════════════════════════════════╝
"""

import os
import sys
import subprocess

# Fix Windows cp1250 encoding — emoji/unicode w print()
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# ============================================================
# SPRAWDZENIE WYMAGANYCH BIBLIOTEK (bez auto-instalacji)
# ============================================================
_REQUIRED_MODULES = ['flask', 'flask_cors', 'requests', 'openpyxl', 'PIL', 'qrcode', 'bs4', 'schedule']
_missing = []
for _mod in _REQUIRED_MODULES:
    try:
        __import__(_mod)
    except ImportError:
        _missing.append(_mod)
if _missing:
    print(f"❌ Brakujące moduły: {', '.join(_missing)}")
    print(f"   Zainstaluj: pip install -r requirements.txt")
    sys.exit(1)

import threading
import time
from datetime import datetime
import json

from flask import Flask, render_template, render_template_string, request, redirect, jsonify, Response, send_from_directory, make_response, flash, url_for
from flask_cors import CORS  # ← DODANO DLA NGROK!

# Importy modułów
from modules.database import init_db, get_db, get_config_cached
from modules.magazynier import magazynier_bp, get_stats as mag_stats
from modules.paletomat import paletomat_bp, get_stats as pal_stats
from modules.telegram_bot import telegram_bp, send_telegram, bot_status, start_bot, stop_bot
from modules.allegro_api import allegro_bp
from modules.logger import log, log_error, log_warning
from modules.auth import auth_bp, setup_auth
# OLX i Vinted - pliki zachowane, moduły wyłączone z menu
# from modules.olx_api import olx_bp
# from modules.vinted_api import vinted_bp
from modules.utils import get_amazon_image_url, oblicz_cene_allegro, generuj_opis_ai

# Gemini AI dla ekstraktora parametrów Allegro
try:
    from google import genai
    from google.genai import types
    
    # Spróbuj załadować z gemini_config.py (jeśli istnieje)
    try:
        from gemini_config import GEMINI_API_KEY
        print("✅ Klucz Gemini załadowany z gemini_config.py")
    except:
        GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
        if not GEMINI_API_KEY:
            print("⚠️  Nie znaleziono gemini_config.py - sprawdzam zmienną środowiskową")
    
    # ALBO HARDCODE TUTAJ (odkomentuj i wklej klucz):
    # GEMINI_API_KEY = 'AIzaSy...'  # Twój klucz API z Google AI Studio
    
    if GEMINI_API_KEY and GEMINI_API_KEY != 'WKLEJ_TUTAJ_SWOJ_KLUCZ':
        GEMINI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)
        print("✅ Gemini AI skonfigurowane (NOWY google.genai!) - Model: gemini-2.0-flash")
    else:
        GEMINI_CLIENT = None
        print("⚠️  Brak GEMINI_API_KEY - Extraktor Allegro wyłączony")
except Exception as e:
    GEMINI_CLIENT = None
    print(f"⚠️  Gemini AI niedostępne: {e}")

# ============================================================
# WERSJA I KONFIGURACJA
# ============================================================
VERSION = "6.1.13 MULTI IMAGES"
APP_START_TIME = time.time()

app = Flask(__name__, static_folder='static', static_url_path='/static')
# SECRET_KEY — generowany losowo i zapisywany do pliku (nie hardcoded!)
_secret_key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.secret_key')
if os.path.exists(_secret_key_path):
    with open(_secret_key_path, 'r') as f:
        _secret_key = f.read().strip()
else:
    import secrets as _secrets
    _secret_key = _secrets.token_hex(32)
    with open(_secret_key_path, 'w') as f:
        f.write(_secret_key)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', _secret_key)
app.config['DATABASE'] = 'akces_hub.db'
app.config['VERSION'] = VERSION

# Loguj WSZYSTKIE błędy 500 do konsoli (Flask domyślnie je ukrywa w non-debug)
import logging
logging.basicConfig(level=logging.ERROR)
app.logger.setLevel(logging.ERROR)

@app.errorhandler(500)
def handle_500(e):
    import traceback
    traceback.print_exc()
    app.logger.error(f"500 error: {e}", exc_info=True)
    from flask import request as _req, jsonify as _jf
    if _req.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return _jf({'success': False, 'message': f'Server error: {e}'}), 500
    return "<h1>500 Internal Server Error</h1><p>Wystapil blad serwera. Szczegoly zostaly zapisane w logach.</p>", 500

# ============================================================
# ✅ CORS CONFIGURATION - NGROK & REMOTE ACCESS FIX!
# ============================================================
CORS(app, resources={
    r"/*": {
        "origins": ["http://localhost:*", "http://127.0.0.1:*"],  # Tylko lokalne domeny
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "Accept"],
        "expose_headers": ["Content-Type", "X-Total-Count"],
        "supports_credentials": True,
        "max_age": 3600
    }
})

print("""
╔═══════════════════════════════════════════════════════════════╗
║                   ✅ CORS ENABLED!                            ║
║  Akces Hub dostępny z każdej domeny (ngrok, localhost, etc.) ║
╚═══════════════════════════════════════════════════════════════╝
""")

@app.after_request
def after_request(response):
    """Dodaj CORS headers + cache control dla SSE"""
    # CORS headers zarzadzane przez flask-cors — nie nadpisuj globalnie
    if 'Access-Control-Allow-Origin' not in response.headers:
        origin = request.headers.get('Origin', '')
        if origin.startswith('http://localhost') or origin.startswith('http://127.0.0.1'):
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
            response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS'
    
    # Dla SSE streams - wyłącz buffering i cache
    if response.mimetype == 'text/event-stream':
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['X-Accel-Buffering'] = 'no'
        response.headers['Connection'] = 'keep-alive'
    # Cache dla statycznych plików (obrazki, CSS, JS)
    elif response.mimetype and (response.mimetype.startswith('image/') or response.mimetype in ('text/css', 'application/javascript')):
        response.headers['Cache-Control'] = 'public, max-age=86400'

    return response
# ============================================================

# Folder na zdjęcia produktów
IMAGES_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'images')
if not os.path.exists(IMAGES_FOLDER):
    os.makedirs(IMAGES_FOLDER)

# WAŻNE: Najpierw zarejestruj routes drukarki DO blueprintu
from modules.magazynier_extensions import register_printer_routes
register_printer_routes(magazynier_bp)

# POTEM rejestruj blueprinty w aplikacji
app.register_blueprint(auth_bp, url_prefix='/auth')
setup_auth(app)

app.register_blueprint(magazynier_bp, url_prefix='/magazyn')
app.register_blueprint(paletomat_bp, url_prefix='/paletomat')
app.register_blueprint(telegram_bp, url_prefix='/telegram')
app.register_blueprint(allegro_bp, url_prefix='/allegro')
# app.register_blueprint(olx_bp, url_prefix='/olx')
# app.register_blueprint(vinted_bp, url_prefix='/vinted')

# Daemon blueprints
try:
    from modules.backup_manager import backup_bp
    if backup_bp:
        app.register_blueprint(backup_bp, url_prefix='/api')
except:
    pass

try:
    from modules.token_refresh import token_refresh_bp
    if token_refresh_bp:
        app.register_blueprint(token_refresh_bp, url_prefix='/api')
except:
    pass

# Cloud Export blueprint
try:
    from modules.cloud_export import cloud_bp
    if cloud_bp:
        app.register_blueprint(cloud_bp, url_prefix='/api')
except:
    pass

# Analytics blueprint (Dashboard KPI + Kalkulator)
try:
    from modules.analytics import analytics_bp
    if analytics_bp:
        app.register_blueprint(analytics_bp, url_prefix='/analytics')
except Exception as e:
    print(f"⚠️ Analytics module not loaded: {e}")

# ============================================================
# EXTRAKTOR ALLEGRO - PARAMETRY + META TITLE
# ============================================================
def extract_allegro_params(produkt_nazwa, produkt_ean='', produkt_asin='', bullet_points=None):
    """
    Używa Gemini AI do wygenerowania parametrów technicznych + meta_title
    
    Zwraca:
    {
        'meta_title': 'Samsung Galaxy Watch 4 Smartwatch GPS NFC',
        'params': {
            'Marka': 'Samsung',
            'Model': 'Galaxy Watch 4',
            'Kolor': 'Czarny',
            'Stan': 'Powystawowy',
            ...
        }
    }
    """
    if not GEMINI_CLIENT:
        return {
            'error': 'Gemini AI niedostępne - ustaw GEMINI_API_KEY w gemini_config.py',
            'meta_title': '',
            'params': {}
        }
    
    try:
        # Prompt dla Gemini
        bullet_str = '\n'.join(f'- {b}' for b in (bullet_points or [])[:5])
        cechy_section = f'CECHY:\n{bullet_str}' if bullet_str else ''
        prompt = f"""Wygeneruj tytuł produktu i parametry dla Allegro.

PRODUKT: {produkt_nazwa}
{f'EAN: {produkt_ean}' if produkt_ean else ''}
{f'ASIN: {produkt_asin}' if produkt_asin else ''}
{cechy_section}

ZADANIE 1 - TYTUŁ:
1. NAJPIERW rodzaj (Smartwatch, Statyw, Kamera)
2. POTEM rozmiar/model (Galaxy Watch 4, 2.5x1.8m)
3. POTEM cechy (GPS, NFC, Aluminiowy)
4. NA KOŃCU marka (Samsung) - jeśli znana
5. MAX 75 znaków, bez przecinków
6. BEZ stanu (Nowy/Używany)

PRZYKŁAD TYTUŁU:
"Smartwatch Galaxy Watch 4 GPS NFC Pulsometr Samsung"

ZADANIE 2 - PARAMETRY:
Wyodrębnij: Marka, Model, Kolor, Stan, Typ, EAN

ODPOWIEDŹ W JSON:
{{
    "meta_title": "Smartwatch Galaxy Watch 4 GPS NFC Pulsometr Samsung",
    "params": {{
        "Marka": "Samsung",
        "Model": "Galaxy Watch 4",
        "Typ": "Smartwatch",
        "Kolor": "Czarny",
        "Stan": "Powystawowy",
        "EAN": "{produkt_ean if produkt_ean else 'Brak'}"
    }}
}}

TYLKO JSON:"""

        # Wywołaj Gemini (nowy API)
        response = GEMINI_CLIENT.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt
        )
        try:
            from modules.pallet_monitor import log_gemini_usage
            log_gemini_usage(response, 'title_params')
        except: pass

        # Wyciągnij tekst z odpowiedzi
        if hasattr(response, 'text'):
            response_text = response.text.strip()
        elif hasattr(response, 'candidates') and len(response.candidates) > 0:
            candidate = response.candidates[0]
            if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                response_text = ''.join(part.text for part in candidate.content.parts if hasattr(part, 'text'))
            else:
                return {'error': 'Gemini nie zwrócił tekstu', 'meta_title': '', 'params': {}}
        else:
            return {'error': 'Gemini nie zwrócił odpowiedzi', 'meta_title': '', 'params': {}}
        
        # Usuń markdown jeśli jest
        if response_text.startswith('```json'):
            response_text = response_text.replace('```json', '').replace('```', '').strip()
        elif response_text.startswith('```'):
            response_text = response_text.replace('```', '').strip()
        
        # Parsuj JSON
        import json
        result = json.loads(response_text)
        
        return result
        
    except Exception as e:
        return {
            'error': f'Błąd generowania: {str(e)}',
            'meta_title': '',
            'params': {}
        }


def auto_kategoryzuj(nazwa):
    """Automatycznie przypisz kategorię na podstawie nazwy produktu.

    WAŻNE: Kolejność sprawdzania ma znaczenie! Najpierw sprawdzamy specyficzne
    wielowyrazowe frazy (żeby uniknąć false positives z krótkich słów),
    potem bardziej ogólne kategorie.
    """
    nazwa_lower = (nazwa or '').lower()
    if not nazwa_lower.strip():
        return 'inne'

    # Helper: sprawdź czy słowo występuje jako oddzielne słowo (nie jako część innego słowa)
    # Np. "kran" nie powinno matchować w "ekran"
    import re as _re_kat
    def _word_match(words, text):
        """Sprawdź czy którekolwiek słowo z listy występuje w tekście"""
        return any(w in text for w in words)

    def _whole_word(word, text):
        """Sprawdź czy słowo występuje jako oddzielne (nie-substring)"""
        return bool(_re_kat.search(r'(?<![a-ząćęłńóśźż])' + _re_kat.escape(word) + r'(?![a-ząćęłńóśźż])', text))

    # ============================================================
    # RUNDA 1: BARDZO SPECYFICZNE FRAZY (multi-word, priorytetowe)
    # Te sprawdzamy PRZED ogólnymi kategoriami żeby uniknąć błędów
    # ============================================================

    # Ekran fotograficzny → foto_video (PRZED rtv żeby złapać "tło do fotografii")
    if _word_match(['ekran ze stojak', 'ekran '], nazwa_lower) and \
       _word_match(['fotografii', 'fotograficzn', 'tło', 'backdrop', 'streaming'], nazwa_lower):
        return 'foto_video'

    # Ekrany projekcyjne → rtv (nie "budowa" przez "kran" w "ekran")
    if _word_match(['ekran projekcyjn', 'projektor ', 'projector', 'ekran przenośn',
                     'ekran kinowy', 'projection screen'], nazwa_lower):
        return 'rtv'

    # Bieżnia / Walking Pad → sport (nie "biuro" przez "pod biurko")
    if _word_match(['bieżnia', 'treadmill', 'walking pad', 'walkingpad', 'bieżni'], nazwa_lower):
        return 'sport'

    # Drony → foto_video (nie "elektronarzedzia" przez "bezszczotkow")
    if _word_match(['dron ', 'dron,', 'drone', 'quadcopter', 'kwadrokopter', 'dron gps',
                     'mini dron', 'fpv', 'dji mini', 'dji mavic', 'dji air'], nazwa_lower):
        return 'foto_video'

    # Pokrowce samochodowe → motoryzacja
    if _word_match(['pokrowce samochod', 'pokrowiec samochod', 'pokrowce na siedzen',
                     'pokrowce na fotele samochod', 'mata samochod', 'dywaniki samochod'], nazwa_lower):
        return 'motoryzacja'

    # Wózek transportowy → outdoor/dom (nie "zabawki" przez "wózek")
    if _word_match(['wózek transportow', 'wózek ogrodow', 'wózek składan', 'taczka',
                     'wózek platformow', 'wózek magazynow'], nazwa_lower):
        return 'outdoor'

    # Okap kuchenny → agd_duze (nie "biuro" przez "biurkowy")
    if _word_match(['okap', 'hood kitchen', 'pochłaniacz', 'wyciąg kuchenn'], nazwa_lower):
        return 'agd_duze'

    # Odkurzacz → agd_male (nie "smart_home" przez markę ezviz)
    if _word_match(['odkurzacz', 'vacuum', 'odkurzać'], nazwa_lower):
        return 'agd_male'

    # Poidło/fontanna/grooming dla zwierząt → zwierzeta (nie "rolnictwo")
    if _word_match(['petkit', 'poidło fontanna', 'fontanna dla kot', 'fontanna dla ps',
                     'poidło dla kot', 'poidło dla ps', 'poidło automatyczne',
                     'drapak', 'legowisko dla', 'kuweta', 'karuzela dla kot',
                     'strzyżeni zwierząt', 'strzyżenia psów', 'maszynka do strzyż',
                     'oneisall', 'grooming', 'trymer dla ps', 'trymer dla zwierz'], nazwa_lower):
        return 'zwierzeta'

    # Fotel biurowy → biuro (specyficzne, przed ogólnym "krzesło")
    if _word_match(['fotel biurow', 'krzesło biurow', 'office chair', 'fotel obrotow',
                     'fotel ergonomiczn', 'fotel gamingowy'], nazwa_lower):
        return 'biuro'

    # Ramka na zdjęcia → foto_video
    if _word_match(['ramka na zdjęci', 'ramka cyfrowa', 'digital frame', 'photo frame',
                     'ramka wifi'], nazwa_lower):
        return 'foto_video'

    # Walizki podróżne → bagaz (nie "moda" przez "plecak")
    if _word_match(['walizk', 'suitcase', 'luggage', 'torba podróżn', 'zestaw walizek',
                     'cabin max', 'bagaż podręczn', 'bagaż kabinow', 'torba kabinow'], nazwa_lower):
        return 'bagaz'

    # Plecak podróżny/kabinowy → bagaz (nie "moda")
    if _word_match(['plecak podróżn', 'plecak kabinow', 'plecak turystyczn',
                     'travel backpack', 'cabin backpack'], nazwa_lower):
        return 'bagaz'

    # Obciążniki sportowe → sport
    if _word_match(['obciążnik', 'ankle weight', 'wrist weight', 'mankiety obciąż',
                     'obciążenie na kostk', 'obciążenie na nadgarst'], nazwa_lower):
        return 'sport'

    # Odzież sportowa → sport
    if _word_match(['biustonosz sportow', 'stanik sportow', 'sportowa podprask',
                     'legginsy sportow', 'getry sportow', 'spodenki sportow',
                     'koszulka sportow', 'odzież sportow', 'biegania', 'do jogi',
                     'biustonosz do biegania', 'legginsy damskie', 'getry damskie',
                     'sportowy biustonosz', 'sportowa bielizna'], nazwa_lower):
        return 'sport'

    # Biustonosz / bielizna (nie-sportowa) → moda
    if _word_match(['biustonosz', 'stanik', 'bielizna', 'majtki', 'bokserki', 'figi',
                     'kalesony', 'rajstopy', 'skarpetki', 'skarpety', 'underwear',
                     'lingerie', 'bra ', 'panties', 'socks'], nazwa_lower):
        return 'moda'

    # Legginsy / getry (nie-sportowe) → moda
    if _word_match(['legginsy', 'leggings', 'getry', 'rajtuzy'], nazwa_lower):
        return 'moda'

    # Hamulec ręczny do gier → gaming
    if _word_match(['hamulec ręczny usb', 'hamulec ręczny pc', 'logitech g27', 'logitech g29',
                     'logitech g920', 'thrustmaster', 'sim racing', 'symulat'], nazwa_lower):
        return 'gaming'

    # Łóżko polowe / leżak → outdoor
    if _word_match(['łóżko polowe', 'łóżko składane', 'leżak turystyczn', 'leżak składan',
                     'łóżko turystyczn', 'camp bed', 'cot bed'], nazwa_lower):
        return 'outdoor'

    # Kojec dla dzieci → niemowleta (nie zabawki)
    if _word_match(['kojec dla dzieci', 'kojec dziecięc', 'kojec składan'], nazwa_lower):
        return 'niemowleta'

    # Poduszka ortopedyczna → rehabilitacja (nie tekstylia)
    if _word_match(['poduszka ortopedyczn', 'poduszka do siedzenia', 'poduszka memory',
                     'poduszka kość ogonow', 'poduszka lędźwiow', 'podkładka do siedzenia',
                     'poduszka z otworem'], nazwa_lower):
        return 'rehabilitacja'

    # Smart home / automatyka → smart_home
    if _word_match(['shelly', 'sonoff', 'ściemniacz wifi', 'dimmer wifi', 'smart switch',
                     'inteligentne gniazdko', 'smart plug', 'zigbee', 'z-wave', 'home assistant',
                     'ściemniacz', 'smart dimmer', 'meross', 'homekit wifi', 'sterownik wifi',
                     'sterownik homekit'], nazwa_lower):
        return 'smart_home'

    # Kamera bezprzewodowa/IP EZVIZ/Menborn → smart_home (nie agd)
    if _word_match(['ezviz c', 'ezviz cb', 'kamera obrotowa', 'kamera bezprzewodow',
                     'kamera akumulator', 'menborn', 'kamera wifi'], nazwa_lower):
        return 'smart_home'

    # Pokrowiec kierownicy / pokrowce foteli/siedzeń → motoryzacja
    if _word_match(['pokrowiec kierownicy', 'pokrowc na fotel', 'pokrowc na siedzen',
                     'pokrowc fotel', 'pokrowc siedzen', 'kierownicy skóra',
                     'pokrowiec na kierownic', 'pokrowców na fotel', 'pokrowców na siedzen',
                     'komplet pokrowców na fotel', 'pokrowców na siedz'], nazwa_lower):
        return 'motoryzacja'

    # Sakwy rowerowe / kółka rowerowe → sport/rowery
    if _word_match(['sakwa rowerow', 'sakwy rowerow', 'kółka rowerow', 'kółka boczne',
                     'kółka do rower', 'sakwa na bagażnik'], nazwa_lower):
        return 'sport'

    # Namiot kempingowy → outdoor
    if _word_match(['namiot kempingow', 'kempingow', 'namiot turystyczn', 'namiot kopułow',
                     'łóżko kempingow'], nazwa_lower):
        return 'outdoor'

    # Paralety / push-up / drążki baletowe → sport
    if _word_match(['paralety', 'push up', 'pushup', 'push-up', 'poręcze do ćwicz',
                     'drążk baletow', 'balet', 'drążek baletow', 'drążki baletow'], nazwa_lower):
        return 'sport'

    # Mata do yogi → sport
    if _word_match(['mata do yog', 'mata do ćwicz', 'mata fitness', 'mata gym'], nazwa_lower):
        return 'sport'

    # Prasa termotransferowa / sublimacja → hobby
    if _word_match(['prasa termotransfer', 'sublimacj', 'termotransfer', 'heat press',
                     'prasa do koszulek', 'prasa do kubków'], nazwa_lower):
        return 'hobby'

    # Gałki do kuchenki → agd_duze
    if _word_match(['gałki do kuchenk', 'gałki do piekarnik', 'gałka do kuchenk',
                     'gałka do piekarnik'], nazwa_lower):
        return 'agd_duze'

    # Podkładki perkusyjne → muzyka
    if _word_match(['podkładki perkusyjn', 'pad perkusyjn', 'pałki perkusyjn',
                     'drum pad', 'practice pad', 'zestaw perkusyjn'], nazwa_lower):
        return 'muzyka'

    # Siłownik termoelektryczny → klimatyzacja
    if _word_match(['siłownik termoelektr', 'siłownik zaworu', 'rozdzielacz podłogow',
                     'termostat podłogow'], nazwa_lower):
        return 'klimatyzacja'

    # Dozownik → dom_ogrod
    if _word_match(['dozownik', 'dispenser'], nazwa_lower):
        return 'dom_ogrod'

    # Zestaw chłodzenia (PC) → komputery
    if _word_match(['chłodzeni', 'cooling', 'radiator', 'cooler cpu', 'wentylator cpu',
                     'pasta termiczna', 'thermal paste', 'heat sink'], nazwa_lower):
        return 'komputery'

    # ============================================================
    # RUNDA 2: GŁÓWNE KATEGORIE (ze poprawionymi keywordami)
    # ============================================================

    # EV / Ładowarki samochodowe (priorytet!)
    if _word_match(['wallbox', 'evse', 'ev charger', 'type 2', 'type2', 'type-2',
        'ccs', 'chademo', 'tesla', 'charging station', 'stacja ładowania', 'ładowarka samochod', 'ładowarka ev',
        'electric vehicle', 'elektromobil', 'green cell ev', 'juice booster', 'go-e', 'easee', 'zappi',
        'mennekes', 'j1772', 'nema', '11kw', '22kw', '7kw', '3.6kw', '32a', '16a'], nazwa_lower):
        if _whole_word('ev', nazwa_lower) or not _word_match(['ev'], nazwa_lower):
            return 'ev_ladowarki'

    # 📸 Foto / Video / Streaming
    if _word_match(['softbox', 'ring light', 'lampa pierścieniowa', 'pierścieniowa',
        'tło fotograficzne', 'tlo fotograficzne', 'backdrop', 'greenscreen', 'green screen', 'tło świąteczne',
        'gopro', 'insta360', 'dji', 'osmo', 'action cam', 'kamera sportowa', 'kamera hd', 'kamera 4k',
        'smallrig', 'stabilizator', 'steadycam', 'follow focus', 'neewer', 'godox',
        'mikrofon', 'microphone', 'lavalier', 'shotgun mic', 'rode', 'boya', 'fifine',
        'teleprompter', 'prompter', 'stojak na tło', 'statyw oświetleniowy', 'light stand', 'boom arm',
        'panel led', 'oświetlenie fotograficzne', 'oświetlenie studyjne', 'video light',
        'transmisja', 'streaming', 'capture card', 'elgato', 'cam link', 'webkamera', 'webcam',
        'emart', 'raleno', 'fgen', 'lywygg', 'julius studio', 'limo studio',
        'fotograficzn', 'fotografi', 'papier foto', 'canon selphy', 'selphy', 'kp-108',
        'objektiv', 'obiektyw', 'lens cap', 'osłona obiektyw', 'filtr uv', 'filtr nd',
        'aparat fotograficzn', 'fujifilm', 'nikon', 'canon eos', 'sony alpha',
        'do fotografii', 'do streamingu', 'foto ', 'wkład do drukark'], nazwa_lower):
        return 'foto_video'

    # 🖨️ Druk 3D
    if _word_match(['filament', 'drukarka 3d', '3d printer', 'druk 3d', 'nozzle', 'dysza', 'hotend', 'extruder',
        'creality', 'ender 3', 'prusa', 'anycubic', 'elegoo', 'żywica uv', 'szpula', 'jayo'], nazwa_lower):
        if _whole_word('pla', nazwa_lower) or _whole_word('abs', nazwa_lower) or \
           _whole_word('petg', nazwa_lower) or _whole_word('tpu', nazwa_lower):
            return 'druk3d'
        return 'druk3d'

    # 📹 Smart Home / Monitoring / Kamery IP
    if _word_match(['kamera ip', 'kamera wifi', 'kamera wlan', 'monitoring', 'cctv',
        'hikvision', 'dahua', 'reolink', 'imou', 'tapo', 'arlo', 'blink', 'wyze', 'eufy',
        'smart home', 'smarthome', 'inteligentny dom', 'czujnik ruchu', 'motion sensor',
        'wideodomofon', 'domofon', 'dzwonek wifi', 'ring doorbell',
        'niania elektroniczna', 'baby monitor', 'kamera bezprzewodowa', 'kamera zewnętrzna'], nazwa_lower):
        return 'smart_home'

    # 🚗 Motoryzacja
    if _word_match(['samochod', 'samochód', 'obd', 'diagnosty',
        'opona', 'kamera cofania', 'cofania', 'backup camera', 'reversing',
        'dash cam', 'dashcam', 'rejestrator jazdy', 'wideorejestrator', 'parkowania', 'czujnik parkowania',
        'nawigacja gps', 'uchwyt samochodowy', 'ładowarka samochodowa', 'car charger',
        'camecho', 'viofo', 'nextbase', '70mai', 'dywaniki samochodow',
        'pokrowiec samochodow', 'pokrowce samochodow', 'fotelik samochodow'], nazwa_lower):
        return 'motoryzacja'

    # 🔭 Optyka (teleskopy, lornetki, mikroskopy)
    if _word_match(['teleskop', 'telescope', 'lornetka', 'binoculars', 'mikroskop', 'microscope',
        'okular', 'eyepiece', 'luneta', 'monokular', 'kolimator', 'collimator', 'svbony', 'celestron', 'bresser',
        'powiększenie', 'zoom optyczny', 'pryzmat'], nazwa_lower):
        return 'optyka'

    # 🐾 Zwierzęta (PRZED rolnictwem - bo "poidło" łapie zwierzęta)
    if _word_match(['karma dla', 'pet food', 'obroża', 'smycz', 'leash', 'klatka dla',
        'akwarium', 'aquarium', 'terrarium', 'legowisko', 'kuweta', 'litter', 'drapak',
        'zabawka dla ps', 'zabawka dla kot', 'miska dla', 'poidło dla',
        'karma', 'transporter dla', 'szelki dla psa', 'smycz dla psa', 'dla zwierząt',
        'dla psa', 'dla kota', 'petkit', 'fontanna dla', 'poidło', 'miska', 'gryzak dla'], nazwa_lower):
        return 'zwierzeta'

    # 🐣 Rolnictwo / Hodowla
    if _word_match(['inkubator', 'incubator', 'wylęg', 'kurnik',
        'hodowla', 'breeding', 'karmnik', 'nawóz', 'fertilizer',
        'nasiona', 'seeds', 'szklarnia', 'greenhouse', 'growbox', 'hydroponika'], nazwa_lower):
        return 'rolnictwo'

    # 🎄 Dekoracje / Święta
    if _word_match(['świąteczn', 'christmas', 'dekoracj', 'decoration', 'ozdoba', 'ornament',
        'girlanda', 'garland', 'lampki choinkowe', 'choinka', 'bożonarodzeni', 'halloween', 'wielkanoc',
        'balony', 'balloon', 'konfetti'], nazwa_lower):
        return 'dekoracje'

    # AGD małe (PRZED oświetleniem - bo "grill" itp.)
    if _word_match(['mikser', 'blender', 'toster', 'czajnik', 'kettle',
        'żelazko', 'suszarka do włos', 'golarki', 'shaver', 'depilator', 'maszynka do golenia',
        'robot kuchenny', 'ekspres do kawy', 'ekspres ciśnieniow',
        'frytkownica', 'air fryer', 'opiekacz', 'mikrofala', 'microwave',
        'robot sprzątający', 'roomba', 'roborock', 'parowar', 'steamer', 'wyciskarka', 'juicer',
        'gofrownica', 'waffle maker', 'jajecznica', 'sandwich maker',
        'krajalnica', 'slicer', 'maszynka do mięsa', 'meat grinder'], nazwa_lower):
        return 'agd_male'

    # AGD duże
    if _word_match(['lodówka', 'fridge', 'pralka', 'washing machine', 'zmywarka', 'dishwasher',
        'piekarnik', 'oven', 'kuchenka', 'cooker', 'klimatyzator', 'air condition', 'freezer', 'zamrażar',
        'suszarka do prania', 'tumble dryer', 'płyta indukcyjn', 'płyta ceramiczn',
        'okap kuchenn'], nazwa_lower):
        return 'agd_duze'

    # 💡 Oświetlenie domowe (POPRAWIONE - usunięto zbyt ogólne 'light', 'lamp')
    if _word_match(['żarówka', 'bulb', 'oświetlenie', 'lighting', 'kinkiet', 'plafon',
        'żyrandol', 'chandelier', 'taśma led', 'halogen', 'świecznik', 'latarnia',
        'lampka nocna', 'lampka biurkowa', 'lampka led', 'lampa stojąca', 'lampa sufitowa',
        'lampa wisząca', 'lampa podłogowa', 'listwa led', 'neon led'], nazwa_lower):
        return 'oswietlenie'

    # 🍳 Kuchnia / Naczynia (POPRAWIONE - usunięto "pot", "pan", "glass" itp.)
    if _word_match(['garnek', 'patelnia', 'naczyn', 'sztućce', 'cutlery',
        'talerz', 'kubek', 'szklanka', 'termos', 'thermos', 'lunch box',
        'deska do krojenia', 'cutting board', 'nóż kuchenny', 'kitchen knife', 'sitko',
        'rondelek', 'wok', 'taca', 'pojemnik kuchenn', 'pojemnik na żywność',
        'szczypce kuchenn', 'otwieracz', 'korkociąg'], nazwa_lower):
        return 'kuchnia'

    # 🛠️ Budowa / Majsterkowanie (POPRAWIONE - usunięto "kran", "tap", "pipe" itp.)
    if _word_match(['cement', 'beton', 'cegła', 'brick', 'fuga', 'grout',
        'farba ścienn', 'farba do ścian', 'pędzel malarski', 'wałek malarski', 'szpachla', 'tynk',
        'złączka hydraul', 'zawór hydraul', 'uszczelka', 'silikon', 'klej montażowy',
        'wiertło', 'kołek rozporowy', 'wkręt', 'śruba', 'gwoźdź'], nazwa_lower):
        return 'budowa'

    # Komputery / IT (POPRAWIONE - "monitor" tylko jako oddzielne słowo)
    if _word_match(['laptop', 'notebook', 'komputer', 'computer',
        'klawiatura', 'myszka komputerow', 'drukarka', 'printer', 'skaner', 'scanner', 'ssd', 'hdd',
        'procesor', 'cpu', 'gpu', 'karta graficzna', 'płyta główna', 'motherboard',
        'pendrive', 'dysk zewnętrzny', 'zewnętrzny dysk',
        'zasilacz komputerow', 'obudowa komputerow', 'pamięć ram'], nazwa_lower):
        return 'komputery'
    if _whole_word('monitor', nazwa_lower) and not _word_match(['baby monitor', 'niania', 'selfie'], nazwa_lower):
        return 'komputery'
    if _whole_word('router', nazwa_lower) or _whole_word('modem', nazwa_lower):
        return 'komputery'

    # 💼 Biuro / Szkoła (POPRAWIONE - usunięto ogólne "desk", "pen", "notes")
    if _word_match(['biurko', 'krzesło biurowe', 'office chair', 'segregator',
        'długopis', 'ołówek', 'zeszyt', 'kalendarz biurow', 'planner',
        'tablica sucho', 'whiteboard', 'niszczarka', 'shredder', 'laminat', 'laminator',
        'organizer biurow', 'szuflada biurow', 'teczka', 'bindownica'], nazwa_lower):
        return 'biuro'

    # Sport / Fitness (ROZSZERZONY)
    if _word_match(['hulajnoga', 'scooter', 'rolki', 'siłownia',
        'hantle', 'dumbbell', 'orbitrek', 'yoga', 'fitness',
        'stepper', 'kettlebell', 'gryf olimpijski', 'sztanga', 'ćwiczeni',
        'rakieta', 'racket', 'tenisow', 'badminton', 'ping pong',
        'trampolin', 'hula hoop', 'skakanka', 'ekspander', 'guma oporow',
        'ławka treningow', 'drążek do podciąg', 'trening', 'sportow',
        'obciążnik', 'gumy fitness', 'mata do ćwicz', 'mata do yoga',
        'rękawice boksersk', 'worek bokserski', 'rękawice treningowe'], nazwa_lower):
        return 'sport'
    if _whole_word('rower', nazwa_lower) or _whole_word('bike', nazwa_lower):
        return 'sport'

    # 🎒 Outdoor / Turystyka
    if _word_match(['namiot turystyczn', 'namiot kampingow', 'śpiwór', 'sleeping bag', 'karimata',
        'latarka', 'flashlight', 'kompas', 'nóż survivalowy', 'survival', 'paracord',
        'hiking', 'trekking', 'karabińczyk', 'carabiner', 'hamak', 'hammock',
        'łóżko polowe', 'leżak', 'torba termoizolacyjn', 'cooler bag',
        'menażka', 'kuchenka turystyczn', 'palnik gazowy', 'czołówka'], nazwa_lower):
        return 'outdoor'

    # Telefony / Smartfony (POPRAWIONE)
    if _word_match(['smartfon', 'smartphone', 'iphone', 'samsung galaxy', 'xiaomi', 'redmi',
        'huawei', 'oppo', 'realme', 'oneplus', 'google pixel', 'mobile phone', 'cell phone',
        'motorola', 'nokia', 'poco', 'honor', 'do telefonu', 'do iphone', 'do samsung',
        'monitor do selfie'], nazwa_lower):
        return 'telefony'
    if _whole_word('telefon', nazwa_lower):
        return 'telefony'

    # Akcesoria elektroniczne
    if _word_match(['ładowarka', 'charger', 'kabel usb', 'kabel lightning', 'kabel type-c',
        'słuchawki', 'headphone', 'earbuds', 'earphone',
        'powerbank', 'power bank', 'adapter', 'przejściówka', 'hub usb', 'stacja dokująca',
        'etui na telefon', 'case ', 'szkło hartowane', 'folia ochronn',
        'statyw', 'tripod', 'gimbal', 'selfi', 'selfie stick',
        'czytnik kart', 'card reader', 'ugreen', 'anker', 'baseus'], nazwa_lower):
        return 'akcesoria'
    if _whole_word('bluetooth', nazwa_lower) and not _word_match(['głośnik', 'speaker', 'soundbar'], nazwa_lower):
        return 'akcesoria'

    # RTV / Audio-Video
    if _word_match(['telewizor', 'soundbar', 'głośnik', 'speaker', 'kino domowe',
        'projektor', 'projector', 'odtwarzacz', 'amplituner', 'subwoofer',
        'blu-ray', 'chromecast', 'fire stick', 'apple tv', 'roku',
        'kabel hdmi', 'kabel audio', 'ekran projekcyjn', 'kolumna', 'wieża audio'], nazwa_lower):
        return 'rtv'
    if _whole_word('tv', nazwa_lower) or _whole_word('radio', nazwa_lower):
        return 'rtv'

    # Gaming (ROZSZERZONY)
    if _word_match(['playstation', 'ps4', 'ps5', 'xbox', 'nintendo', 'konsola',
        'gamepad', 'kontroler gier', 'joystick', 'gaming',
        'oculus', 'quest', 'pad perkusyjny',
        'kierownica do gier', 'racing wheel', 'flight stick', 'hotas',
        'logitech g', 'razer', 'steelseries', 'hyperx',
        'mysz gamingowa', 'klawiatura gamingowa', 'podkładka gamingowa',
        'fotel gamingowy', 'sim racing'], nazwa_lower):
        return 'gaming'
    if _whole_word('vr', nazwa_lower):
        return 'gaming'

    # Narzędzia
    if _word_match(['wiertarka', 'drill', 'wkrętarka', 'screwdriver', 'szlifierka', 'grinder',
        'piła', 'młotek', 'hammer', 'zestaw narzędzi', 'tool kit', 'kompresor',
        'spawarka', 'welder', 'lutownica', 'multimetr', 'poziomica', 'obcęgi', 'pliers', 'szczypce',
        'imadło', 'imbus', 'torx', 'klucz nasadow', 'klucz płaski', 'klucz oczkowy'], nazwa_lower):
        return 'narzedzia'
    if _whole_word('klucz', nazwa_lower) and _word_match(['nasaw', 'płask', 'oczkow', 'nasad', 'zestaw'], nazwa_lower):
        return 'narzedzia'

    # Dom i ogród (POPRAWIONE - usunięto ogólne "pot", "chair", "table")
    if _word_match(['meble', 'furniture', 'ogród', 'garden',
        'dywan', 'carpet', 'rolet', 'żaluzj',
        'sofa', 'kanapa', 'szafka', 'cabinet', 'regał',
        'kosiarka', 'mower', 'podkaszarka', 'wąż ogrodowy', 'grill ogrodowy', 'parasol ogrodow',
        'nawadnianie', 'doniczka', 'wieszak', 'organizer dom',
        'pojemnik', 'kosz na śmieci', 'suszarka na pranie', 'deska do prasowania',
        'lustro', 'zegar ścienny', 'ramka na zdjęcia'], nazwa_lower):
        return 'dom_ogrod'
    if _whole_word('krzesło', nazwa_lower) or _whole_word('stół', nazwa_lower):
        return 'dom_ogrod'

    # Zabawki / Dzieci (POPRAWIONE - usunięto ogólne "wózek", "baby")
    if _word_match(['zabawka', 'toy', 'klocki', 'lego', 'lalka', 'doll', 'pluszak', 'gra planszowa',
        'puzzle', 'samochodzik', 'kolejka elektryczn', 'dziecięc', 'piaskownic',
        'fotelik dziecięc', 'car seat', 'rowerek dziecięc',
        'kredki', 'plastelina', 'zjeżdżalnia', 'huśtawka dziecięc', 'bujak'], nazwa_lower):
        return 'zabawki'
    if _word_match(['wózek dziecięc', 'wózek spacer'], nazwa_lower):
        return 'zabawki'

    # Moda (POPRAWIONE - usunięto ogólne "bag", "belt", "hat", "watch")
    if _word_match(['buty', 'shoes', 'ubrani', 'koszul', 'shirt', 'spodni', 'pants',
        'sukienk', 'dress', 'kurtk', 'jacket', 'bluza', 'sweater', 'czapk',
        'torebk', 'damska', 'portfel', 'wallet', 'biżuteria', 'jewelry', 'okulary',
        'garnitur', 'kamizelk', 'szalik', 'rękawiczk', 'kapelusz',
        'sneakers', 'sandały', 'botki', 'kozaki', 'klapki', 'trampki',
        't-shirt', 'polo', 'jeansy', 'dżinsy'], nazwa_lower):
        return 'moda'
    if _whole_word('plecak', nazwa_lower):
        return 'moda'
    if _whole_word('zegarek', nazwa_lower) or _whole_word('watch', nazwa_lower):
        return 'moda'
    if _whole_word('pasek', nazwa_lower) and not _word_match(['pasek klinow', 'pasek rozrząd', 'pasek do zegarki'], nazwa_lower):
        return 'moda'

    # Zdrowie / Uroda (ROZSZERZONY)
    if _word_match(['masażer', 'massager', 'ciśnieniomierz', 'termometr medyczn', 'inhalator',
        'szczoteczka elektr', 'szczoteczka sonic', 'suszarka do włosów',
        'prostownica', 'lokówka', 'trymer do włos', 'trymer do brod',
        'waga łazienkowa', 'pulsoksymetr', 'glukometr', 'aparat słuchowy',
        'pistolet do masażu', 'masaż perkusyjn', 'masażer perkusyjn',
        'depilator', 'ipl', 'laser do depilacji',
        'irygator', 'waterpik', 'inhalator'], nazwa_lower):
        return 'zdrowie'

    # ♿ Rehabilitacja / Mobilność
    if _word_match(['rampa', 'ramp', 'podjazd', 'inwalidzk', 'wheelchair', 'wózek inwalidzki',
        'balkonik', 'walker', 'chodzik', 'orteza', 'orthosis',
        'rehabilitacj', 'rehabilitation', 'ortopedyczn', 'orthopedic', 'temblak',
        'pas ortopedyczny', 'gorset ortopedyczn', 'kołnierz ortopedyczny',
        'materac przeciwodleżynowy', 'podpórka', 'kule inwalidzk',
        'stabilizator kolana', 'stabilizator nadgarst', 'opaska ortopedyczn'], nazwa_lower):
        return 'rehabilitacja'

    # 🛏️ Tekstylia domowe (POPRAWIONE - bardziej specyficzne)
    if _word_match(['kołdra', 'duvet', 'quilt', 'pościel', 'bedding', 'prześcieradło', 'sheet',
        'ręcznik', 'towel', 'szlafrok', 'bathrobe',
        'obrus', 'tablecloth', 'serwetka', 'narzuta', 'bedspread',
        'poszewka', 'pillowcase', 'firanka',
        'ściereczka', 'ścierka', 'mop'], nazwa_lower):
        return 'tekstylia'
    # Poduszka → tekstylia TYLKO jeśli nie ortopedyczna
    if _word_match(['poduszka', 'pillow', 'cushion'], nazwa_lower) and \
       not _word_match(['ortopedyczn', 'memory', 'do siedzenia', 'z otworem', 'kość ogonow'], nazwa_lower):
        return 'tekstylia'

    # 🧴 Kosmetyki / Chemia
    if _word_match(['szampon', 'shampoo', 'mydło', 'soap', 'żel pod prysznic', 'shower gel',
        'balsam', 'lotion', 'perfum', 'dezodorant', 'deodorant', 'pasta do zębów', 'toothpaste',
        'proszek do prania', 'detergent', 'płyn do mycia', 'środek czystości',
        'krem do twarzy', 'krem nawilżając', 'peeling', 'serum', 'tonik',
        'lakier do paznokci', 'żel do paznokci', 'manicure', 'pedicure'], nazwa_lower):
        return 'kosmetyki'

    # 📚 Książki / Media (POPRAWIONE - usunięto ogólne "cd", "dvd")
    if _word_match(['książka', 'book', 'audiobook', 'ebook', 'e-book', 'komiks', 'comic',
        'czasopismo', 'poradnik', 'encyklopedia', 'słownik', 'dictionary',
        'vinyl', 'płyta cd', 'płyta dvd'], nazwa_lower):
        return 'ksiazki'

    # 🎁 Prezenty / Upominki
    if _word_match(['prezent', 'gift', 'upominek', 'voucher',
        'opakowanie prezentowe', 'gift box', 'wstążka', 'papier do pakowania', 'wrapping'], nazwa_lower):
        return 'prezenty'

    # 🔒 Bezpieczeństwo (POPRAWIONE - usunięto ogólne "lock", "safe", "alarm")
    if _word_match(['sejf', 'kłódka', 'padlock', 'zamek do drzwi', 'zamek szyfrowy',
        'gaśnica', 'extinguisher', 'czujnik dymu', 'smoke detector', 'apteczka',
        'kamizelka odblaskowa', 'czujnik gazu', 'czujnik czadu', 'kamera bezpieczeństwa'], nazwa_lower):
        return 'bezpieczenstwo'

    # 🧳 Bagaż / Podróże
    if _word_match(['torba podróżna', 'travel bag',
        'kosmetyczka', 'organizer podróżny', 'nerka', 'saszetka',
        'plecak podróżny', 'torba sportowa', 'torba na ramię'], nazwa_lower):
        return 'bagaz'

    # 🏋️ Siłownia / Crossfit
    if _word_match(['barbell', 'weight plate', 'ławka treningowa', 'bench press',
        'drążek do podciąg', 'pull-up bar', 'atlas treningow', 'suwnica', 'power rack',
        'gumy oporowe', 'resistance band', 'piłka gimnastyczna', 'gym ball'], nazwa_lower):
        return 'silownia'

    # 🚴 Rowery / E-bike (POPRAWIONE - "rower" matched earlier in sport)
    if _word_match(['e-bike', 'ebike', 'elektryczny rower',
        'kask rowerowy', 'cycling helmet', 'siodełko rowerow', 'pedał rowerow',
        'lampka rowerowa', 'bike light', 'bagażnik rowerowy', 'bike rack'], nazwa_lower):
        return 'rowery'

    # 🧰 Elektronarzędzia (Makita, Bosch, DeWalt)
    if _word_match(['makita', 'dewalt', 'milwaukee', 'metabo', 'hikoki', 'einhell',
        'akumulatorow', 'cordless', 'bezszczotkow', 'brushless'], nazwa_lower):
        return 'elektronarzedzia'
    # "bosch" → elektronarzedzia tylko z kontekstem narzędzi
    if 'bosch' in nazwa_lower and _word_match(['wiertark', 'szlifierk', 'piła', 'wkrętar', 'frezar',
                                                 'professional', 'gsr', 'gws', 'gbh'], nazwa_lower):
        return 'elektronarzedzia'

    # 🎨 Hobby / Rękodzieło
    if _word_match(['maszyna do szycia', 'sewing machine', 'overlock', 'hafciarka', 'embroidery',
        'farby akrylowe', 'acrylic paint', 'sztaluga', 'easel', 'płótno malarskie', 'canvas',
        'dziewiar', 'knitting', 'szydełk', 'crochet', 'scrapbook', 'decoupage', 'modelarstwo', 'airbrush',
        'diamond painting', 'malowanie po numerach', 'zestaw do malowania',
        'pyrograf', 'wypalarka', 'piaskowanie'], nazwa_lower):
        return 'hobby'

    # 🍼 Dla niemowląt (POPRAWIONE)
    if _word_match(['niemowl', 'infant', 'noworod', 'newborn', 'łóżeczko dziecięc', 'kojec', 'playpen',
        'przewijak', 'sterilizator butelek', 'podgrzewacz do butelek', 'bottle warmer',
        'mata edukacyjna', 'karuzela nad łóżeczk', 'nosidełko', 'baby carrier',
        'smoczek', 'butelka dla niemowl', 'pieluch'], nazwa_lower):
        return 'niemowleta'

    # 🔊 Car Audio
    if _word_match(['głośnik samochodowy', 'car speaker', 'subwoofer samochodowy', 'car subwoofer',
        'wzmacniacz samochodowy', 'car amplifier', 'radio samochodowe', 'car radio', 'android auto', 'carplay',
        'tweetery', 'zwrotnica', 'kondensator car audio'], nazwa_lower):
        return 'car_audio'

    # 🌡️ Klimatyzacja / Wentylacja (POPRAWIONE - usunięto ogólne "fan")
    if _word_match(['wentylator', 'oczyszczacz powietrza', 'air purifier', 'nawilżacz', 'humidifier',
        'osuszacz', 'dehumidifier', 'klimatyzator przenośny', 'portable ac', 'rekuperator', 'filtr hepa',
        'wentylacja', 'cyrkulat'], nazwa_lower):
        return 'klimatyzacja'

    # 🪴 Hydroponika / Growbox
    if _word_match(['growbox', 'grow box', 'namiot uprawowy', 'grow tent', 'lampa led grow', 'grow light',
        'hydroponik', 'hydroponic', 'system nawadniania', 'ph metr', 'ec metr'], nazwa_lower):
        return 'hydroponika'

    # 🎣 Wędkarstwo
    if _word_match(['wędka', 'fishing rod', 'kołowrotek', 'żyłka wędkars', 'fishing line',
        'przynęta', 'bait', 'lure', 'podbierak', 'landing net', 'echosonda', 'fish finder',
        'łódź wędkarska'], nazwa_lower):
        return 'wedkarstwo'
    if _whole_word('spinning', nazwa_lower) and _word_match(['wędka', 'kołowrot', 'fishing'], nazwa_lower):
        return 'wedkarstwo'

    # 🔬 Laboratorium
    if _word_match(['waga laboratoryjna', 'lab scale', 'waga precyzyjna', 'precision scale', 'pipeta', 'pipette',
        'probówka', 'test tube', 'zlewka', 'beaker', 'kolba miarowa', 'mikroskop laboratoryjny',
        'wirówka', 'centrifuge'], nazwa_lower):
        return 'laboratorium'

    # 🎪 Event / Imprezy
    if _word_match(['namiot imprezowy', 'party tent', 'pawilon', 'gazebo', 'oświetlenie sceniczne', 'stage light',
        'maszyna do dymu', 'fog machine', 'laser sceniczny', 'kula disco', 'disco ball', 'nagłośnienie', 'pa system',
        'mikser dj', 'dj mixer', 'kontroler dj', 'dj controller'], nazwa_lower):
        return 'event'

    # 📡 CB / Radio / Komunikacja
    if _word_match(['cb radio', 'krótkofalówka', 'walkie talkie', 'pmr', 'radiotelefon', 'antena cb',
        'skaner radiowy', 'radio scanner', 'sdr', 'radio amatorskie', 'ham radio', 'baofeng', 'midland'], nazwa_lower):
        return 'cb_radio'

    # ============================================================
    # RUNDA 3: OSTATECZNE FALLBACKI (dla popularnych produktów)
    # ============================================================

    # Ogólne "lampa" → oświetlenie (ale nie "lampa pierścieniowa" - to foto)
    if _whole_word('lampa', nazwa_lower) and not _word_match(['pierścieniow', 'studyjn', 'fotograficzn'], nazwa_lower):
        return 'oswietlenie'

    # Ogólne "grill" → dom_ogrod
    if _whole_word('grill', nazwa_lower):
        return 'dom_ogrod'

    # Produkty "Amazon" bez rozpoznawalnej nazwy
    if _word_match(['produkt amazon', 'produkt b0'], nazwa_lower):
        return 'inne'

    return 'inne'


# Słownik kategorii do wyświetlania
KATEGORIE_DISPLAY = {
    'ev_ladowarki': '⚡ Ładowarki EV',
    'foto_video': '📸 Foto/Video',
    'druk3d': '🖨️ Druk 3D',
    'smart_home': '📹 Smart Home',
    'motoryzacja': '🚗 Motoryzacja',
    'optyka': '🔭 Optyka',
    'rolnictwo': '🐣 Rolnictwo',
    'dekoracje': '🎄 Dekoracje',
    'oswietlenie': '💡 Oświetlenie',
    'kuchnia': '🍳 Kuchnia',
    'budowa': '🛠️ Budowa',
    'biuro': '💼 Biuro',
    'outdoor': '🎒 Outdoor',
    'rehabilitacja': '♿ Rehabilitacja',
    'tekstylia': '🛏️ Tekstylia',
    'kosmetyki': '🧴 Kosmetyki',
    'ksiazki': '📚 Książki',
    'prezenty': '🎁 Prezenty',
    'bezpieczenstwo': '🔒 Bezpieczeństwo',
    'bagaz': '🧳 Bagaż',
    'silownia': '🏋️ Siłownia',
    'rowery': '🚴 Rowery',
    'hulajnogi': '🛴 Hulajnogi',
    'agd_male': '🔌 AGD małe',
    'agd_duze': '🏠 AGD duże',
    'komputery': '💻 Komputery/IT',
    'telefony': '📱 Telefony',
    'akcesoria': '🔋 Akcesoria',
    'rtv': '📺 RTV/Audio',
    'gaming': '🎮 Gaming',
    'narzedzia': '🔧 Narzędzia',
    'dom_ogrod': '🏡 Dom i ogród',
    'sport': '⚽ Sport/Fitness',
    'zabawki': '🧸 Zabawki',
    'moda': '👕 Moda',
    'zdrowie': '💊 Zdrowie/Uroda',
    'zwierzeta': '🐾 Zwierzęta',
    'muzyka': '🎸 Muzyka',
    'elektronarzedzia': '🧰 Elektronarzędzia',
    'hobby': '🎨 Hobby/Rękodzieło',
    'niemowleta': '🍼 Niemowlęta',
    'car_audio': '🔊 Car Audio',
    'klimatyzacja': '🌡️ Klimatyzacja',
    'hydroponika': '🪴 Hydroponika',
    'wedkarstwo': '🎣 Wędkarstwo',
    'laboratorium': '🔬 Laboratorium',
    'event': '🎪 Event/Imprezy',
    'cb_radio': '📡 CB/Radio',
    'elektronika': '📷 Elektronika',
    'inne': '📦 Inne'
}


# Route do serwowania lokalnych zdjęć
@app.route('/images/<filename>')
def serve_image(filename):
    """Serwuje lokalne zdjęcia produktów"""
    return send_from_directory(IMAGES_FOLDER, filename)

# ============================================================
# SZABLONY HTML (extracted to templates/ directory)
# ============================================================

# CSS variable kept for other inline templates that still use it
CSS = '''
<style>
/* ===========================================
   CSS VARIABLES - THEME SUPPORT
   =========================================== */
:root {
    /* Dark theme (default) */
    --bg-primary: #0a0a0f;
    --bg-secondary: #12121a;
    --bg-tertiary: #1e1e2e;
    --border-color: #2a2a3a;
    --text-primary: #ffffff;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;
    --accent-blue: #3b82f6;
    --accent-green: #22c55e;
    --accent-yellow: #eab308;
    --accent-red: #ef4444;
    --accent-purple: #8b5cf6;
    --accent-orange: #ff5a00;
    --nav-bg: #0a0a0f;
}

[data-theme="light"] {
    --bg-primary: #f8fafc;
    --bg-secondary: #ffffff;
    --bg-tertiary: #f1f5f9;
    --border-color: #e2e8f0;
    --text-primary: #1e293b;
    --text-secondary: #475569;
    --text-muted: #94a3b8;
    --accent-blue: #2563eb;
    --accent-green: #16a34a;
    --accent-yellow: #ca8a04;
    --accent-red: #dc2626;
    --accent-purple: #7c3aed;
    --accent-orange: #ea580c;
    --nav-bg: #ffffff;
}

*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg-primary);color:var(--text-primary);min-height:100vh;transition:background 0.3s, color 0.3s}
button,a,.btn,.card,.quick-btn,.module,.tool-card,.list-item,[onclick]{-webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent;outline:none}
button:active,a:active,.btn:active,[onclick]:active{outline:none}
body.kiosk,body.kiosk *{cursor:none!important}
.container{max-width:1600px;margin:0 auto;padding:20px;padding-bottom:90px}
.header{text-align:center;padding:25px 0;border-bottom:1px solid var(--border-color);margin-bottom:25px}
.header h1{font-size:1.8rem;background:linear-gradient(135deg,var(--accent-blue),var(--accent-purple));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.header small{color:var(--text-muted);font-size:0.85rem}

/* Theme Toggle */
.theme-toggle{position:fixed;top:15px;right:15px;z-index:200;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:50%;width:44px;height:44px;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:1.3rem;transition:all 0.3s}
.theme-toggle:hover{transform:scale(1.1);border-color:var(--accent-blue)}

/* Cards */
.card{background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:16px;padding:18px;margin-bottom:15px;transition:all 0.2s}
.card:hover{border-color:var(--accent-blue)}
.card-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.card-title{font-weight:600;font-size:1.05rem}

/* Stats Grid - responsive */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
.stat{background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:14px;padding:16px;text-align:center;transition:all 0.2s}
.stat:hover{border-color:var(--accent-blue)}
.stat-value{font-size:1.6rem;font-weight:700;color:var(--accent-blue)}
.stat-value.green{color:var(--accent-green)}
.stat-value.yellow{color:var(--accent-yellow)}
.stat-label{font-size:0.8rem;color:var(--text-muted);text-transform:uppercase;margin-top:5px}

/* Today Stats */
.today-stats{background:linear-gradient(135deg,rgba(34,197,94,0.1),rgba(16,185,129,0.1));border:1px solid rgba(34,197,94,0.3);border-radius:16px;padding:20px;margin-bottom:20px}
.today-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:15px}
.today-title{color:var(--accent-green);font-weight:600;font-size:1.15rem}
.today-date{color:var(--text-muted);font-size:0.85rem}
.today-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;text-align:center}
.today-value{font-size:2rem;font-weight:700;color:var(--accent-green)}
.today-label{font-size:0.8rem;color:var(--text-muted)}

/* Quick Actions - responsive */
.quick-actions{display:grid;grid-template-columns:repeat(6,1fr);gap:15px;margin-bottom:20px}
.quick-btn{background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:14px;padding:20px 15px;text-align:center;color:var(--text-primary);text-decoration:none;transition:all 0.2s}
.quick-btn:hover{border-color:var(--accent-blue);transform:translateY(-2px)}
.quick-btn .icon{font-size:1.8rem;margin-bottom:10px}
.quick-btn .label{font-size:0.85rem;color:var(--text-secondary)}
.quick-btn.active{border-color:var(--accent-green);background:rgba(34,197,94,0.1)}
.quick-btn.alert{border-color:var(--accent-red);background:rgba(239,68,68,0.1)}

/* Module Cards - 2 column layout on desktop */
.modules-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:15px;margin-bottom:20px}
.module{background:linear-gradient(135deg,var(--bg-tertiary),var(--bg-secondary));border:1px solid var(--border-color);border-radius:16px;padding:20px;margin-bottom:0;text-decoration:none;color:var(--text-primary);display:block;transition:all 0.2s}
.module:hover{border-color:var(--accent-blue);transform:translateY(-3px)}
.module.purple{background:linear-gradient(135deg,rgba(139,92,246,0.2),rgba(88,28,135,0.2));border-color:rgba(139,92,246,0.3)}
.module.blue{background:linear-gradient(135deg,rgba(59,130,246,0.2),rgba(37,99,235,0.2));border-color:rgba(59,130,246,0.3)}
.module.orange{background:linear-gradient(135deg,rgba(255,90,0,0.2),rgba(200,70,0,0.2));border-color:rgba(255,90,0,0.3)}
.module-header{display:flex;align-items:center;gap:14px;margin-bottom:12px}
.module-icon{font-size:2.4rem}
.module-title{font-weight:700;font-size:1.2rem}
.module-desc{font-size:0.9rem;color:var(--text-secondary)}
.module-stats{display:flex;gap:12px;margin-top:14px;flex-wrap:wrap}
.module-stat{background:rgba(0,0,0,0.2);padding:8px 14px;border-radius:8px;font-size:0.85rem}
.module-stat strong{color:var(--accent-green)}

/* Buttons */
.btn{display:block;width:100%;padding:15px;font-size:1rem;font-weight:600;text-align:center;text-decoration:none;border:none;border-radius:12px;cursor:pointer;margin-bottom:12px;color:#fff;transition:all 0.2s}
.btn-primary{background:var(--accent-blue)}
.btn-primary:hover{background:#2563eb;transform:translateY(-1px)}
.btn-success{background:var(--accent-green)}
.btn-success:hover{background:#16a34a}
.btn-purple{background:linear-gradient(135deg,var(--accent-purple),#7c3aed)}
.btn-secondary{background:var(--bg-tertiary);border:1px solid var(--border-color);color:var(--text-primary)}
.btn-danger{background:var(--accent-red)}
.btn-warning{background:var(--accent-yellow);color:#000}
.btn-sm{padding:10px 18px;font-size:0.9rem;width:auto;display:inline-block}

/* Tools Grid */
.tools-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px}
.tool-card{background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:14px;padding:18px;text-decoration:none;color:var(--text-primary);transition:all 0.2s}
.tool-card:hover{border-color:var(--accent-blue);transform:translateY(-2px)}
.tool-icon{font-size:2rem;margin-bottom:10px}
.tool-name{font-weight:600;font-size:0.95rem}
.tool-desc{font-size:0.75rem;color:var(--text-muted);margin-top:5px}

/* List Items */
.list-item{display:flex;align-items:center;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:12px;padding:14px;margin-bottom:10px;text-decoration:none;color:var(--text-primary);transition:all 0.2s}
.list-item:hover{border-color:var(--accent-blue)}
.list-item img{width:52px;height:52px;object-fit:contain;background:#fff;border-radius:10px;margin-right:14px}
.list-item-info{flex:1;min-width:0}
.list-item-title{font-weight:600;font-size:0.95rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.list-item-meta{font-size:0.8rem;color:var(--text-muted)}
.list-item-right{text-align:right;margin-left:12px}
.list-item-value{font-weight:700;color:var(--accent-blue)}
.list-item-sub{font-size:0.75rem;color:var(--text-muted)}

/* Activity */
.activity-item{display:flex;align-items:center;gap:14px;padding:14px;background:var(--bg-secondary);border-radius:12px;margin-bottom:10px}
.activity-dot{width:10px;height:10px;border-radius:50%}
.activity-dot.green{background:var(--accent-green)}
.activity-dot.yellow{background:var(--accent-yellow)}
.activity-dot.red{background:var(--accent-red)}
.activity-content{flex:1}
.activity-msg{font-size:0.95rem}
.activity-time{font-size:0.75rem;color:var(--text-muted)}

/* Forms */
.form-group{margin-bottom:18px}
.form-group label{display:block;font-size:0.85rem;color:var(--text-secondary);margin-bottom:8px;font-weight:500}
.form-control{width:100%;padding:14px;background:var(--bg-primary);border:1px solid var(--border-color);border-radius:10px;color:var(--text-primary);font-size:1rem;transition:border-color 0.2s}
.form-control:focus{outline:none;border-color:var(--accent-blue)}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}

/* Alerts */
.alert{padding:14px 18px;border-radius:12px;margin-bottom:18px;font-size:0.95rem}
.alert-success{background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.3);color:var(--accent-green)}
.alert-warning{background:rgba(234,179,8,0.15);border:1px solid rgba(234,179,8,0.3);color:var(--accent-yellow)}
.alert-error{background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);color:var(--accent-red)}

/* Status Bar */
.status-bar{display:flex;align-items:center;justify-content:space-between;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:12px;padding:14px 18px;margin-bottom:18px}
.status-bar.online{border-color:rgba(34,197,94,0.5);background:rgba(34,197,94,0.1)}
.status-bar.offline{border-color:rgba(239,68,68,0.5);background:rgba(239,68,68,0.1)}
.status-indicator{display:flex;align-items:center;gap:12px}
.status-dot{width:12px;height:12px;border-radius:50%;background:var(--text-muted)}
.status-dot.online{background:var(--accent-green);animation:pulse 2s infinite}
.status-dot.offline{background:var(--accent-red)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.5}}

/* Section Title */
.section-title{color:var(--accent-blue);font-weight:600;font-size:0.95rem;margin:25px 0 15px;display:flex;align-items:center;gap:10px}

/* Calc Result */
.calc-result{background:var(--bg-primary);border-radius:12px;padding:18px;margin-top:18px}
.calc-row{display:flex;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--border-color)}
.calc-row:last-child{border:none}
.calc-label{color:var(--text-secondary)}
.calc-value{font-weight:700}
.calc-value.green{color:var(--accent-green)}
.calc-value.red{color:var(--accent-red)}
.calc-value.big{font-size:1.6rem}
.calc-highlight{border-top:2px solid var(--accent-green);padding-top:18px;margin-top:12px}
.sugestia{background:var(--bg-tertiary);border-radius:12px;padding:18px;text-align:center;margin-top:18px}
.sugestia-value{font-size:2.2rem;font-weight:700;color:var(--accent-yellow)}

/* Opis Box */
.opis-box{background:var(--bg-tertiary);border-radius:12px;padding:18px;white-space:pre-wrap;font-size:0.95rem;line-height:1.7;max-height:280px;overflow-y:auto;margin:18px 0}

/* Toggle */
.toggle-row{display:flex;align-items:center;justify-content:space-between;padding:14px;background:var(--bg-primary);border-radius:12px;margin-bottom:10px}
.toggle-label{font-size:0.95rem}
.toggle{width:48px;height:26px;background:var(--bg-tertiary);border-radius:13px;padding:3px;cursor:pointer;transition:all 0.2s}
.toggle.on{background:var(--accent-blue)}
.toggle-knob{width:20px;height:20px;background:#fff;border-radius:50%;transition:all 0.2s}
.toggle.on .toggle-knob{transform:translateX(22px)}

/* Log Item */
.log-item{display:flex;align-items:center;gap:12px;padding:12px;background:var(--bg-primary);border-radius:10px;margin-bottom:8px}
.log-icon{width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:1.1rem}
.log-icon.sale{background:rgba(34,197,94,0.2)}
.log-icon.alert{background:rgba(234,179,8,0.2)}
.log-icon.report{background:rgba(59,130,246,0.2)}
.log-content{flex:1;min-width:0}
.log-msg{font-size:0.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.log-time{font-size:0.75rem;color:var(--text-muted)}
.log-status{font-size:0.75rem;color:var(--accent-green)}

/* Back Link */
.back{display:block;text-align:center;color:var(--text-muted);text-decoration:none;padding:18px;font-size:0.95rem;transition:color 0.2s}
.back:hover{color:var(--text-primary)}

/* Bottom Nav */
.bottom-nav{position:fixed;bottom:0;left:0;right:0;background:var(--nav-bg);border-top:1px solid var(--border-color);padding:10px 0;z-index:100}
.bottom-nav-inner{max-width:1600px;margin:0 auto;display:flex;justify-content:space-around}
.nav-item{text-align:center;color:var(--text-muted);text-decoration:none;padding:10px 20px;border-radius:12px;transition:all 0.2s}
.nav-item:hover,.nav-item.active{color:var(--accent-blue);background:rgba(59,130,246,0.1)}
.nav-icon{font-size:1.5rem;margin-bottom:4px}
.nav-label{font-size:0.75rem}

/* Badge */
.badge{display:inline-block;padding:4px 10px;border-radius:10px;font-size:0.75rem;font-weight:600}
.badge-success{background:rgba(34,197,94,0.2);color:var(--accent-green)}
.badge-warning{background:rgba(234,179,8,0.2);color:var(--accent-yellow)}
.badge-error{background:rgba(239,68,68,0.2);color:var(--accent-red)}

/* Version Badge */
.version-badge{position:fixed;bottom:75px;right:15px;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:8px;padding:4px 10px;font-size:0.7rem;color:var(--text-muted);z-index:99}

/* ===========================================
   RESPONSIVE DESIGN
   =========================================== */

/* Extra Large Desktop (1600px+) */
@media (min-width:1600px){
    .container{max-width:1600px;padding:30px}
    .modules-grid{grid-template-columns:repeat(2,1fr)}
    .tools-grid{grid-template-columns:repeat(4,1fr)}
    .stats{grid-template-columns:repeat(4,1fr)}
    .quick-actions{grid-template-columns:repeat(6,1fr);gap:20px}
}

/* Large Desktop (1200px - 1600px) */
@media (min-width:1200px) and (max-width:1599px){
    .container{max-width:1400px;padding:25px}
    .modules-grid{grid-template-columns:repeat(2,1fr)}
    .tools-grid{grid-template-columns:repeat(4,1fr)}
}

/* Desktop (900px - 1200px) */
@media (max-width:1199px){
    .container{max-width:100%;padding:20px}
    .modules-grid{grid-template-columns:repeat(2,1fr)}
    .tools-grid{grid-template-columns:repeat(3,1fr)}
}

/* Tablet (768px - 900px) */
@media (max-width:900px){
    .container{max-width:100%;padding:15px}
    .modules-grid{grid-template-columns:1fr}
    .stats{grid-template-columns:repeat(3,1fr)}
    .quick-actions{grid-template-columns:repeat(5,1fr)}
    .tools-grid{grid-template-columns:repeat(2,1fr)}
}

/* Large Phone / Small Tablet (600px - 768px) */
@media (max-width:768px){
    .container{padding:12px}
    .stats{grid-template-columns:repeat(2,1fr)}
    .quick-actions{grid-template-columns:repeat(4,1fr)}
    .today-value{font-size:1.6rem}
    .stat-value{font-size:1.4rem}
    .module-title{font-size:1.05rem}
    .module-icon{font-size:2rem}
    .form-row{grid-template-columns:1fr}
    .theme-toggle{width:40px;height:40px;font-size:1.1rem}
}

/* Phone (max 480px) */
@media (max-width:480px){
    .container{padding:10px}
    .header h1{font-size:1.4rem}
    .header{padding:18px 0}
    .quick-actions{grid-template-columns:repeat(3,1fr);gap:8px}
    .quick-btn{padding:12px 8px}
    .quick-btn .icon{font-size:1.3rem}
    .quick-btn .label{font-size:0.65rem}
    .stats{grid-template-columns:repeat(2,1fr);gap:8px}
    .stat{padding:12px}
    .stat-value{font-size:1.3rem}
    .today-grid{gap:8px}
    .today-value{font-size:1.4rem}
    .today-label{font-size:0.7rem}
    .module{padding:16px}
    .module-stats{gap:8px}
    .module-stat{padding:6px 10px;font-size:0.75rem}
    .tools-grid{grid-template-columns:1fr 1fr}
    .btn{padding:13px;font-size:0.95rem}
    .bottom-nav-inner{justify-content:space-between;padding:0 4px}
    .nav-item{padding:6px 6px}
    .nav-icon{font-size:1.4rem}
    .nav-label{font-size:0.7rem}
    .theme-toggle{top:10px;right:10px;width:36px;height:36px;font-size:1rem}
}

/* Extra small phone */
@media (max-width:360px){
    .quick-actions{grid-template-columns:repeat(3,1fr)}
    .stats{grid-template-columns:1fr 1fr}
    .today-grid{grid-template-columns:1fr 1fr 1fr}
    .tools-grid{grid-template-columns:1fr}
}
</style>
'''



# Widok dla dziadka - uproszczony

@app.route('/wybierz-konto')
def wybierz_konto():
    """Strona wyboru konta"""
    return render_template('wybor_konta.html')

@app.route('/ustaw-konto/<user>')
def ustaw_konto(user):
    """Ustawia cookie z wybranym kontem"""
    resp = make_response(redirect('/'))
    resp.set_cookie('akces_user', user, max_age=60*60*24*365)  # 1 rok
    return resp

@app.route('/zmien-konto')
def zmien_konto():
    """Usuwa cookie i przekierowuje do wyboru"""
    resp = make_response(redirect('/wybierz-konto'))
    resp.delete_cookie('akces_user')
    return resp

# ============================================================
# HEALTH CHECK ENDPOINT (dla debugging)
# ============================================================
@app.route('/api/health')
def api_health():
    """Health check endpoint - sprawdz czy backend dziala"""
    # DB check
    db_status = 'ok'
    try:
        conn = get_db()
        conn.execute('SELECT 1').fetchone()
    except Exception as e:
        db_status = f'error: {e}'

    # Uptime
    uptime_sec = int(time.time() - APP_START_TIME)
    days = uptime_sec // 86400
    hours = (uptime_sec % 86400) // 3600
    mins = (uptime_sec % 3600) // 60
    secs = uptime_sec % 60
    if days > 0:
        uptime_str = f"{days}d {hours}h {mins}m"
    elif hours > 0:
        uptime_str = f"{hours}h {mins}m"
    else:
        uptime_str = f"{mins}m {secs}s"

    return jsonify({
        'status': 'ok' if db_status == 'ok' else 'degraded',
        'version': VERSION,
        'uptime': uptime_str,
        'uptime_seconds': uptime_sec,
        'db_status': db_status,
        'timestamp': datetime.now().isoformat(),
        'features': {
            'paletomat': True,
            'magazynier': True,
            'allegro': True,
            'telegram': True,
        }
    })

@app.route('/api/ngrok-status')
def api_ngrok_status():
    """Ngrok tunnel status — sprawdza ngrok API lokalnie"""
    import requests as req
    try:
        # Ngrok udostepnia lokalne API na porcie 4040
        r = req.get('http://127.0.0.1:4040/api/tunnels', timeout=2)
        if r.status_code == 200:
            tunnels = r.json().get('tunnels', [])
            for t in tunnels:
                url = t.get('public_url', '')
                if url.startswith('https://'):
                    # Zapisz URL do configa zeby inne moduly mialy dostep
                    from modules.database import set_config
                    set_config('app_base_url', url)
                    return jsonify({'url': url})
            # Ngrok dziala ale brak HTTPS tunnela
            if tunnels:
                return jsonify({'url': tunnels[0].get('public_url', '')})
    except Exception:
        pass
    # Ngrok nie dziala
    return jsonify({'url': ''})

@app.route('/api/ngrok-control', methods=['POST'])
def api_ngrok_control():
    """Start/stop ngrok tunnel from kiosk dashboard"""
    import subprocess
    data = request.get_json() or {}
    action = data.get('action', '')
    if action == 'start':
        try:
            # Sprawdz czy ngrok juz dziala
            import requests as req
            try:
                r = req.get('http://127.0.0.1:4040/api/tunnels', timeout=2)
                if r.status_code == 200 and r.json().get('tunnels'):
                    return jsonify({'ok': True, 'msg': 'Ngrok juz dziala'})
            except Exception:
                pass
            # Pobierz domain z configa jesli jest
            from modules.database import get_config
            domain = get_config('ngrok_domain', '')
            token = get_config('ngrok_auth_token', '')
            cmd = ['ngrok', 'http', '5000', '--log=stdout']
            if domain:
                cmd.extend(['--url', domain])
            # Uruchom ngrok w tle
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           start_new_session=True)
            return jsonify({'ok': True, 'msg': 'Ngrok starting...'})
        except Exception as e:
            return jsonify({'ok': False, 'msg': str(e)})
    elif action == 'stop':
        try:
            subprocess.run(['pkill', '-f', 'ngrok'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            from modules.database import set_config
            set_config('app_base_url', 'http://localhost:5000')
            return jsonify({'ok': True, 'msg': 'Ngrok stopped'})
        except Exception as e:
            return jsonify({'ok': False, 'msg': str(e)})
    return jsonify({'ok': False, 'msg': 'Unknown action'})

@app.route('/allegro/moje-oferty')
def redirect_moje_oferty():
    """Redirect — stary link z paletomat buttons"""
    return redirect('/allegro/oferty')

@app.route('/api/kiosk-exit')
def api_kiosk_exit():
    """Close kiosk chromium on Pi"""
    import subprocess
    try:
        subprocess.Popen(['pkill', '-f', 'chromium'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except:
        pass
    return jsonify({'ok': True})

@app.route('/api/system-stats')
def api_system_stats():
    """System stats for Raspberry Pi dashboard"""
    import psutil, time
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    # Temperature - Linux (Pi) or fallback
    temp = 0
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for name in ('cpu_thermal', 'cpu-thermal', 'coretemp', 'soc_thermal'):
                if name in temps and temps[name]:
                    temp = temps[name][0].current
                    break
            if temp == 0:
                first = list(temps.values())[0]
                if first:
                    temp = first[0].current
    except Exception:
        try:
            with open('/sys/class/thermal/thermal_zone0/temp') as f:
                temp = int(f.read().strip()) / 1000
        except Exception:
            pass
    # Uptime
    uptime_sec = time.time() - psutil.boot_time()
    days = int(uptime_sec // 86400)
    hours = int((uptime_sec % 86400) // 3600)
    mins = int((uptime_sec % 3600) // 60)
    if days > 0:
        uptime_str = f"{days}d {hours}h"
    elif hours > 0:
        uptime_str = f"{hours}h {mins}m"
    else:
        uptime_str = f"{mins}m"
    return jsonify({
        'cpu': round(cpu, 1),
        'ram_used': round(mem.used / (1024**3), 1),
        'ram_total': round(mem.total / (1024**3), 1),
        'ram_percent': mem.percent,
        'disk_used': round(disk.used / (1024**3), 1),
        'disk_total': round(disk.total / (1024**3), 1),
        'disk_percent': disk.percent,
        'temp': round(temp, 1),
        'uptime': uptime_str
    })

@app.route('/')
def home():
    # Sprawdź czy jest wybrane konto (auto-set adrian if not)
    user = request.cookies.get('akces_user')
    
    if not user:
        user = 'adrian'
    
    # Pobierz statystyki
    from modules.database import get_full_stats, get_db
    stats = get_full_stats()
    
    # Pobierz goal (Hyundai i30 N)
    from modules.simple_goal_manager import get_current_goal
    goal = get_current_goal()
    
    # Oblicz status SYPIE — jedno zapytanie zamiast dwóch
    conn = get_db()
    today_str = datetime.now().strftime('%Y-%m-%d')
    month_start = datetime.now().strftime('%Y-%m-01')

    sypie_row = conn.execute('''
        SELECT
            SUM(CASE WHEN date(data_sprzedazy) = ? THEN 1 ELSE 0 END) as dzis_cnt,
            COALESCE(SUM(CASE WHEN date(data_sprzedazy) = ? THEN cena * ilosc ELSE 0 END), 0) as dzis_suma,
            COUNT(*) as msc_cnt,
            COALESCE(SUM(cena * ilosc), 0) as msc_suma
        FROM sprzedaze
        WHERE date(data_sprzedazy) >= ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
        AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''', (today_str, today_str, month_start)).fetchone()

    dzis_data = {'cnt': sypie_row['dzis_cnt'], 'suma': sypie_row['dzis_suma']}
    miesiac_data = {'cnt': sypie_row['msc_cnt'], 'suma': sypie_row['msc_suma']}
    
    sypie_kwota = float(dzis_data['suma'] or 0)
    sypie_zamowienia = int(dzis_data['cnt'] or 0)
    
    # SYPIE = powyżej 3000 zł w zamówieniach
    PROG_SYPIE = 3000
    sypie = sypie_kwota >= PROG_SYPIE
    
    # Różne poziomy sypania
    if sypie_kwota >= 5000:
        sypie_text = "MEGA SYPIE!"
        sypie_color = "#22c55e"
    elif sypie_kwota >= PROG_SYPIE:
        sypie_text = "SYPIE!"
        sypie_color = "#22c55e"
    elif sypie_kwota >= 1500:
        sypie_text = "Calkiem niezle"
        sypie_color = "#eab308"
    elif sypie_kwota >= 500:
        sypie_text = "Sypie troche"
        sypie_color = "#f97316"
    else:
        sypie_text = "NIE SYPIE"
        sypie_color = "#ef4444"
    
    # Statystyki miesięczne
    miesiac_kwota = float(miesiac_data['suma'] or 0)
    miesiac_zamowienia = int(miesiac_data['cnt'] or 0)
    
    # Polskie nazwy miesięcy
    MIESIACE_PL = {
        1: 'Styczeń', 2: 'Luty', 3: 'Marzec', 4: 'Kwiecień',
        5: 'Maj', 6: 'Czerwiec', 7: 'Lipiec', 8: 'Sierpień',
        9: 'Wrzesień', 10: 'Październik', 11: 'Listopad', 12: 'Grudzień'
    }
    miesiac_nazwa = MIESIACE_PL.get(datetime.now().month, 'Miesiąc')
    
    # Czy w tym miesiącu sypało? (średnio >= 500 zł dziennie)
    dni_w_miesiacu = datetime.now().day
    srednia_dzienna = miesiac_kwota / dni_w_miesiacu if dni_w_miesiacu > 0 else 0
    miesiac_sypie = srednia_dzienna >= 500  # średnio 500 zł dziennie = sypie
    
    sypie_data = {
        'sypie_text': sypie_text,
        'sypie_color': sypie_color,
        'sypie_miesiac': f"Dzisiaj ({datetime.now().strftime('%d.%m')})",
        'sypie_kwota': f"{sypie_kwota:.0f}",
        'sypie_zamowienia': sypie_zamowienia,
        # Miesięczne
        'miesiac_nazwa': miesiac_nazwa,
        'miesiac_kwota': f"{miesiac_kwota:.0f}",
        'miesiac_zamowienia': miesiac_zamowienia,
        'miesiac_srednia': f"{srednia_dzienna:.0f}",
        'miesiac_sypie': miesiac_sypie
    }
    
    # Jeśli dziadek lub babcia - pokaż uproszczony widok
    if user in ['dziadek', 'babcia']:
        icon = '👴' if user == 'dziadek' else '👵'
        nazwa = user.upper()
        return render_template('dziadek.html', user_icon=icon, user_name=nazwa, do_wyslania=stats['do_wyslania'])
    
    # Adrian - pełny widok
    mag = mag_stats()
    pal = pal_stats()
    
    # Allegro status
    from modules.allegro_api import is_configured, is_authenticated
    allegro = {
        'status': '🟢 Online' if is_authenticated() else ('🟡 Skonfiguruj' if is_configured() else '⚪ Offline'),
        'zamowienia': stats['sprzedaz_dzis_cnt'],
        'oferty': stats['wystawione']
    }
    
    # Dzisiejsze dane z bazy
    today = {
        'sprzedaz': stats['sprzedaz_dzis_cnt'],
        'przychod': round(stats['sprzedaz_dzis_suma'] or 0, 2),
        'do_wyslania': stats['do_wyslania']
    }
    
    # Override mag stats with real data
    mag['produkty'] = stats['magazyn_produkty']
    mag['sztuk'] = stats['magazyn_sztuki']
    
    # Ostatnia aktywność
    activity = [
        {'msg': f"Sprzedaż dziś: {stats['sprzedaz_dzis_cnt']} szt", 'time': 'dziś', 'color': 'green'},
        {'msg': f"Magazyn: {stats['magazyn_produkty']} produktów", 'time': 'aktualnie', 'color': 'blue'},
        {'msg': f"Stoi >30 dni: {stats['stojace_30dni']} szt", 'time': 'uwaga', 'color': 'yellow'},
    ]
    
    # Kiosk mode — uproszczony dashboard (URL param lub cookie)
    is_kiosk = request.args.get('kiosk') == '1' or request.cookies.get('kiosk_mode') == '1'
    if is_kiosk:
        resp = make_response(render_template('kiosk_home.html',
            version=VERSION,
            today=today, mag=mag, pal=pal, allegro=allegro,
            active_home='active', active_magazyn='', active_paletomat='',
            active_allegro='', active_olx='', active_vinted='', active_narzedzia='',
            active_monitor='',
            **sypie_data
        ))
        resp.set_cookie('kiosk_mode', '1', max_age=365*24*3600)
        return resp

    resp = make_response(render_template('home.html',
        version=VERSION,
        today_date=datetime.now().strftime('%d.%m.%Y'),
        today=today,
        mag=mag,
        pal=pal,
        allegro=allegro,
        telegram_online=bot_status(),
        unread_count=2,
        activity=activity,
        goal=goal,  # Hyundai i30 N Goal
        top_produkty=stats.get('top_produkty', []),
        top_dostawcy=stats.get('top_dostawcy', []),
        active_home='active', active_magazyn='', active_paletomat='',
        active_allegro='', active_monitor='', active_narzedzia='',
        **sypie_data
    ))
    if not request.cookies.get('akces_user'):
        resp.set_cookie('akces_user', 'adrian', max_age=60*60*24*365)
    return resp


# === CACHE zamówień Allegro (żeby nie odpytywać API przy każdym ładowaniu) ===
_wysylki_cache = {'data': None, 'timestamp': 0, 'raw': None}
_WYSYLKI_CACHE_TTL = 120  # 2 minuty

def _pobierz_zamowienia_allegro(force_refresh=False):
    """Pobiera zamówienia z Allegro API z cache (2 min TTL)"""
    import time as _time
    from modules.database import get_db
    from modules.allegro_api import get_orders, is_authenticated

    now = _time.time()
    if not force_refresh and _wysylki_cache['data'] is not None and (now - _wysylki_cache['timestamp']) < _WYSYLKI_CACHE_TTL:
        return _wysylki_cache['data'], _wysylki_cache['raw']

    zamowienia = []
    produkty_cnt = 0
    wartosc = 0
    raw_orders = None

    # Pobieramy z LOKALNEJ BAZY (status='nowa') zamiast z Allegro API
    # Dzięki temu oznaczone jako wysłane w bazie znikają z listy
    conn = get_db()
    rows = conn.execute('''
        SELECT s.id, s.allegro_order_id, s.nazwa, s.cena, s.ilosc, s.kupujacy,
               s.data_sprzedazy, s.adres, s.produkt_id,
               p.lokalizacja, p.regal, p.zdjecie_url
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        WHERE s.status = 'nowa'
        ORDER BY s.data_sprzedazy DESC
    ''').fetchall()

    # Grupuj po allegro_order_id (jedno zamówienie = wiele produktów)
    orders_map = {}
    for row in rows:
        oid = row['allegro_order_id'] or f"LOCAL-{row['id']}"
        if oid not in orders_map:
            orders_map[oid] = {
                'order_id': oid,
                'order_id_short': oid[:8] if oid else '',
                'buyer': row['kupujacy'] or 'Nieznany',
                'date': (row['data_sprzedazy'] or '')[:10],
                'address': row['adres'] or 'Brak adresu',
                'pickup_point': '',
                'produkty': [],
                'total_sum': 0
            }
        lok = row['lokalizacja'] or row['regal'] or ''
        name = row['nazwa'] or 'Produkt'
        qty = row['ilosc'] or 1
        price = row['cena'] or 0
        orders_map[oid]['total_sum'] += price * qty
        produkty_cnt += qty
        orders_map[oid]['produkty'].append({
            'name': name,
            'name_short': name[:50] + '...' if len(name) > 50 else name,
            'qty': qty, 'price': price,
            'lokalizacja': lok,
            'zdjecie_url': row['zdjecie_url'] or ''
        })

    for o in orders_map.values():
        o['total'] = f"{o['total_sum']:.0f}"
        wartosc += o['total_sum']
        zamowienia.append(o)

    raw_orders = None

    result = {'zamowienia': zamowienia, 'produkty_cnt': produkty_cnt, 'wartosc': f"{wartosc:.0f}"}
    _wysylki_cache['data'] = result
    _wysylki_cache['raw'] = raw_orders
    _wysylki_cache['timestamp'] = now
    return result, raw_orders

@app.route('/wysylki/allegro')
def wysylki_allegro():
    """Lista zamówień do wysłania z Allegro API z lokalizacjami produktów"""
    force = request.args.get('refresh', '') == '1'

    # Przy odświeżeniu — najpierw sync z Allegro (aktualizuje statusy wysłanych)
    if force:
        try:
            from modules.allegro_api import sync_orders
            print(f"[Wysylki] START sync...")
            result = sync_orders(today_only=False)  # Sync cały miesiąc
            print(f"[Wysylki] DONE sync: {result}")
        except Exception as e:
            import traceback
            print(f"[Wysylki] Sync error: {e}")
            traceback.print_exc()

    result, _ = _pobierz_zamowienia_allegro(force_refresh=force)

    return render_template('wysylki.html',
        version=VERSION,
        zamowienia=result['zamowienia'],
        zamowienia_cnt=len(result['zamowienia']),
        produkty_cnt=result['produkty_cnt'],
        wartosc=result['wartosc'],
        active_wysylki='active', active_home='', active_magazyn='',
        active_paletomat='', active_allegro='', active_monitor='', active_narzedzia=''
    )

@app.route('/wysylki/sync')
def wysylki_sync():
    """Odświeża zamówienia z Allegro"""
    from modules.allegro_api import sync_orders
    sync_orders(today_only=False, notify=False)
    return redirect('/wysylki/allegro')



@app.route('/wysylki/pakowanie')
def wysylki_pakowanie():
    """Stacja pakowania ze skanerem"""
    return render_template('pakowanie.html',
        version=VERSION,
        active_wysylki='active', active_home='', active_magazyn='',
        active_paletomat='', active_allegro='', active_monitor='', active_narzedzia='')


@app.route('/api/wysylki/szukaj')
def api_wysylki_szukaj():
    """API - szuka zamówienia po EAN/ASIN/nazwie/order_id (z cache)"""
    from modules.database import get_db
    from modules.allegro_api import is_authenticated

    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'error': 'Podaj EAN, ASIN, nazwę lub zeskanuj etykietę'})

    if not is_authenticated():
        return jsonify({'error': 'Nie zalogowano do Allegro'})

    print(f"🔍 Szukam zamówienia dla: {q}")

    # Pobierz zamówienia z cache (szybko!)
    result, raw_orders = _pobierz_zamowienia_allegro()

    if not raw_orders or 'checkoutForms' not in raw_orders:
        return jsonify({'error': 'Brak zamówień do wysłania'})

    q_lower = q.lower().strip()

    # === 1. Szukaj po ORDER ID (etykieta wysyłkowa) ===
    for order in raw_orders.get('checkoutForms', []):
        order_id = order.get('id', '')
        # Dopasuj pełny order_id lub jego fragment (min 8 znaków)
        if order_id and (q_lower == order_id.lower() or
                        (len(q) >= 8 and q_lower in order_id.lower()) or
                        order_id.lower().startswith(q_lower)):
            print(f"   → ✅ Znaleziono po order_id: {order_id}")
            items = order.get('lineItems', [])
            item = items[0] if items else {}
            return _zwroc_zamowienie_full(order)

    # === 2. Szukaj po EAN/ASIN w bazie danych ===
    conn = get_db()
    produkt_z_bazy = conn.execute('''
        SELECT p.id, p.nazwa, p.ean, p.asin, p.ilosc, p.lokalizacja, p.regal, p.zdjecie_url,
               o.allegro_id, o.tytul
        FROM produkty p
        LEFT JOIN oferty o ON o.produkt_id = p.id
        WHERE p.ean = ? OR p.asin = ? OR LOWER(p.asin) = LOWER(?) OR p.kod_magazynowy = ?
        LIMIT 1
    ''', (q, q, q, q.upper())).fetchone()

    if produkt_z_bazy:
        print(f"   → Znaleziono w bazie: {produkt_z_bazy['nazwa'][:40]}")

    # === 3. Szukaj w zamówieniach po allegro_id / frazie ===
    szukane_allegro_ids = []
    szukane_frazy = [q_lower]

    if produkt_z_bazy:
        if produkt_z_bazy['allegro_id']:
            szukane_allegro_ids.append(str(produkt_z_bazy['allegro_id']))
        if produkt_z_bazy['tytul']:
            szukane_frazy.append(produkt_z_bazy['tytul'].lower()[:30])
        if produkt_z_bazy['nazwa']:
            words = produkt_z_bazy['nazwa'].split()[:3]
            for word in words:
                if len(word) > 3:
                    szukane_frazy.append(word.lower())

    for order in raw_orders.get('checkoutForms', []):
        for item in order.get('lineItems', []):
            offer_name = item.get('offer', {}).get('name', '')
            offer_id = str(item.get('offer', {}).get('id', ''))

            if offer_id in szukane_allegro_ids:
                print(f"   → ✅ Znaleziono po allegro_id: {offer_id}")
                return _zwroc_zamowienie(order, item, produkt_z_bazy)

            for fraza in szukane_frazy:
                if len(fraza) > 3 and fraza in offer_name.lower():
                    print(f"   → ✅ Znaleziono po frazie '{fraza}'")
                    return _zwroc_zamowienie(order, item, produkt_z_bazy)

    if produkt_z_bazy:
        return jsonify({
            'error': f'Produkt "{produkt_z_bazy["nazwa"][:40]}" (stan: {produkt_z_bazy["ilosc"]} szt.) - brak zamówienia do wysłania',
            'produkt': {
                'nazwa': produkt_z_bazy['nazwa'],
                'asin': produkt_z_bazy['asin'],
                'ean': produkt_z_bazy['ean'],
                'ilosc': produkt_z_bazy['ilosc'],
                'lokalizacja': produkt_z_bazy['lokalizacja'] or produkt_z_bazy['regal']
            }
        })

    return jsonify({'error': f'Nie znaleziono: {q}'})


def _zwroc_zamowienie_full(order):
    """Helper - formatuje odpowiedź z WSZYSTKIMI produktami zamówienia (np. ze skanowanej etykiety)"""
    from modules.database import get_db
    order_id = order.get('id', '')
    buyer = order.get('buyer', {}).get('login', 'Nieznany')

    delivery = order.get('delivery', {})
    address_data = delivery.get('address', {})
    address = ', '.join([p for p in [
        address_data.get('street', ''), address_data.get('city', ''), address_data.get('zipCode', '')
    ] if p])
    pickup_point = ''
    if delivery.get('pickupPoint'):
        pp = delivery.get('pickupPoint', {})
        pickup_point = f"{pp.get('name', '')} - {pp.get('address', {}).get('street', '')}"

    total = sum(float(i.get('price', {}).get('amount', 0)) * int(i.get('quantity', 1))
               for i in order.get('lineItems', []))

    # Zbierz WSZYSTKIE produkty z lokalizacjami i zdjęciami
    conn = get_db()
    produkty = []
    for item in order.get('lineItems', []):
        offer_id = item.get('offer', {}).get('id', '')
        name = item.get('offer', {}).get('name', 'Produkt')
        qty = int(item.get('quantity', 1))
        lokalizacja = ''
        zdjecie_url = ''
        if offer_id:
            p = conn.execute('''
                SELECT p.lokalizacja, p.regal, p.zdjecie_url
                FROM produkty p JOIN oferty o ON o.produkt_id = p.id
                WHERE o.allegro_id = ? LIMIT 1
            ''', (offer_id,)).fetchone()
            if p:
                lokalizacja = p['lokalizacja'] or p['regal'] or ''
                zdjecie_url = p['zdjecie_url'] or ''
        produkty.append({
            'nazwa': name[:60],
            'qty': qty,
            'lokalizacja': lokalizacja,
            'zdjecie_url': zdjecie_url
        })

    return jsonify({
        'zamowienie': {
            'order_id': order_id,
            'buyer': buyer,
            'address': address or 'Brak adresu',
            'pickup_point': pickup_point,
            'total': f"{total:.0f}",
            'produkt_nazwa': produkty[0]['nazwa'] if produkty else '',
            'inne_produkty': len(produkty) - 1,
            'produkty': produkty,
            'lokalizacja': produkty[0]['lokalizacja'] if produkty else None,
            'asin': None, 'ean': None, 'stan_magazynowy': None
        }
    })


def _zwroc_zamowienie(order, item, produkt_z_bazy):
    """Helper - formatuje odpowiedź z zamówieniem"""
    order_id = order.get('id', '')
    buyer = order.get('buyer', {}).get('login', 'Nieznany')
    offer_name = item.get('offer', {}).get('name', '')

    delivery = order.get('delivery', {})
    address_data = delivery.get('address', {})
    address = ', '.join([p for p in [
        address_data.get('street', ''),
        address_data.get('city', ''),
        address_data.get('zipCode', '')
    ] if p])

    pickup_point = ''
    if delivery.get('pickupPoint'):
        pp = delivery.get('pickupPoint', {})
        pickup_point = f"{pp.get('name', '')} - {pp.get('address', {}).get('street', '')}"

    total = sum(float(i.get('price', {}).get('amount', 0)) * int(i.get('quantity', 1))
               for i in order.get('lineItems', []))

    inne_produkty = len(order.get('lineItems', [])) - 1

    return jsonify({
        'zamowienie': {
            'order_id': order_id,
            'buyer': buyer,
            'address': address or 'Brak adresu',
            'pickup_point': pickup_point,
            'total': f"{total:.0f}",
            'produkt_nazwa': offer_name[:60],
            'inne_produkty': inne_produkty,
            'asin': produkt_z_bazy['asin'] if produkt_z_bazy else None,
            'ean': produkt_z_bazy['ean'] if produkt_z_bazy else None,
            'lokalizacja': (produkt_z_bazy['lokalizacja'] or produkt_z_bazy['regal']) if produkt_z_bazy else None,
            'stan_magazynowy': produkt_z_bazy['ilosc'] if produkt_z_bazy else None
        }
    })


@app.route('/wysylki/nadaj/<order_id>')
def wysylki_nadaj(order_id):
    """Tworzy przesyłkę (jeśli nie istnieje) i zwraca etykietę PDF"""
    from modules.allegro_api import create_and_get_label
    
    print(f"🖨️ Nadawanie przesyłki dla zamówienia: {order_id}")
    
    # Spróbuj utworzyć przesyłkę i pobrać etykietę
    label_pdf, shipment_id, error = create_and_get_label(order_id)
    
    if error:
        return f'''
        <html>
        <head><meta charset="utf-8"><title>Błąd</title></head>
        <body style="font-family:sans-serif;padding:40px;background:#12121a;color:#fff">
            <h2>❌ Błąd nadawania przesyłki</h2>
            <p style="color:#ef4444">{error}</p>
            <p>Zamówienie: {order_id[:8]}...</p>
            <p style="color:#64748b;font-size:0.9rem;margin-top:20px">Możliwe przyczyny:</p>
            <ul style="color:#64748b;font-size:0.85rem">
                <li>Brak uprawnień API do tworzenia przesyłek</li>
                <li>Zamówienie już ma nadaną przesyłkę ręcznie</li>
                <li>Problem z metodą dostawy</li>
            </ul>
            <a href="https://allegro.pl/moje-allegro/sprzedaz/zamowienia/{order_id}" target="_blank" style="display:inline-block;margin:20px 0;padding:12px 20px;background:#3b82f6;color:#fff;text-decoration:none;border-radius:8px;font-weight:600">📦 Nadaj ręcznie na Allegro →</a><br>
            <a href="/wysylki" style="color:#64748b">← Powrót do wysyłek</a>
        </body>
        </html>
        ''', 400
    
    if label_pdf:
        print(f"   → ✅ Etykieta gotowa! Rozmiar: {len(label_pdf)} bytes")
        # Zwróć PDF do druku
        response = make_response(label_pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename=etykieta_{order_id[:8]}.pdf'
        return response
    else:
        # Przesyłka utworzona ale brak etykiety
        return f'''
        <html>
        <head><meta charset="utf-8"><title>Przesyłka utworzona</title></head>
        <body style="font-family:sans-serif;padding:40px;background:#12121a;color:#fff">
            <h2>✅ Przesyłka utworzona!</h2>
            <p>ID przesyłki: {shipment_id}</p>
            <p style="color:#f59e0b">Etykieta może być niedostępna od razu. Spróbuj pobrać za chwilę.</p>
            <a href="/wysylki/etykieta/{order_id}" style="display:inline-block;margin:20px 0;padding:12px 20px;background:#22c55e;color:#fff;text-decoration:none;border-radius:8px;font-weight:600">🖨️ Pobierz etykietę</a><br>
            <a href="/wysylki" style="color:#64748b">← Powrót do wysyłek</a>
        </body>
        </html>
        '''


@app.route('/wysylki/etykieta/<order_id>')
def wysylki_etykieta(order_id):
    """Pobiera etykietę PDF dla istniejącej przesyłki"""
    from modules.allegro_api import get_shipment_label
    
    label_pdf, shipment_id, error = get_shipment_label(order_id)
    
    if error == "BRAK_PRZESYLKI":
        return f'''
        <html>
        <head><meta charset="utf-8"><title>Brak przesyłki</title></head>
        <body style="font-family:sans-serif;padding:40px;background:#12121a;color:#fff">
            <h2>📦 Przesyłka nie została jeszcze nadana</h2>
            <p>Najpierw nadaj przesyłkę na Allegro, potem wróć po etykietę.</p>
            <a href="https://allegro.pl/moje-allegro/sprzedaz/zamowienia/{order_id}" target="_blank" style="display:inline-block;margin:20px 0;padding:12px 20px;background:#3b82f6;color:#fff;text-decoration:none;border-radius:8px;font-weight:600">📦 Nadaj na Allegro →</a><br>
            <a href="/wysylki" style="color:#64748b">← Powrót do wysyłek</a>
        </body>
        </html>
        '''
    
    if error:
        return f'''
        <html>
        <head><meta charset="utf-8"><title>Błąd</title></head>
        <body style="font-family:sans-serif;padding:40px;background:#12121a;color:#fff">
            <h2>❌ Błąd pobierania etykiety</h2>
            <p style="color:#ef4444">{error}</p>
            <a href="https://allegro.pl/moje-allegro/sprzedaz/zamowienia/{order_id}" target="_blank" style="color:#3b82f6;display:block;margin:20px 0">📦 Pobierz etykietę na Allegro →</a>
            <a href="/wysylki" style="color:#64748b">← Powrót do wysyłek</a>
        </body>
        </html>
        ''', 400
    
    response = make_response(label_pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=etykieta_{order_id[:8]}.pdf'
    return response



@app.route('/monitor')
def monitor_page():
    """Strona monitora okazji palet"""
    from modules.pallet_monitor import get_recent_deals, get_deal_stats, get_keywords, get_monitor_costs
    stats = get_deal_stats()
    costs = get_monitor_costs()
    deals = get_recent_deals(limit=100)
    keywords = get_keywords()

    deals_html = ''
    for d in deals:
        kw = d.get('matched_keywords', '[]')
        try:
            kw_list = json.loads(kw) if isinstance(kw, str) else kw
            kw_str = ', '.join(kw_list[:3])
        except:
            kw_str = str(kw)[:50]

        source_emoji = '🏪' if d['source'] == 'warrington' else '🎪'
        # Ceny już w PLN (API z url-accept-currency: pln)
        _dp = float(d.get('price', 0) or 0)
        price_str = f"{_dp:.0f} PLN"
        time_str = d.get('first_seen', '')[:16] if d.get('first_seen') else ''

        img_html = ''
        if d.get('image_url'):
            img_html = f'<img src="{d["image_url"]}" style="width:80px;height:80px;object-fit:cover;border-radius:8px;flex-shrink:0" onerror="this.style.display=\'none\'" loading="lazy">'
        else:
            img_html = f'<div style="width:80px;height:80px;background:var(--border-color);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:28px;flex-shrink:0">{source_emoji}</div>'

        # RRP i ROI info
        _rrp = float(d.get('market_value', 0) or 0)
        _roi = round(_rrp / _dp, 1) if _rrp > 0 and _dp > 0 else 0
        roi_badge = ''
        if _roi >= 5:
            roi_badge = '<span style="background:#ef4444;color:#fff;padding:1px 6px;border-radius:8px;font-size:10px;font-weight:700">🔥 ROI {:.0f}x</span>'.format(_roi)
        elif _roi >= 3:
            roi_badge = '<span style="background:#f59e0b;color:#fff;padding:1px 6px;border-radius:8px;font-size:10px;font-weight:700">💰 ROI {:.0f}x</span>'.format(_roi)
        elif _roi >= 1.5:
            roi_badge = '<span style="background:#3b82f6;color:#fff;padding:1px 6px;border-radius:8px;font-size:10px">ROI {:.1f}x</span>'.format(_roi)

        rrp_str = f' | RRP: {_rrp:.0f} PLN' if _rrp > 0 else ''

        deals_html += f'''
        <div style="display:flex;gap:12px;padding:12px;border-bottom:1px solid var(--border-color);align-items:center">
            {img_html}
            <div style="flex:1;min-width:0">
                <div style="font-weight:600;margin-bottom:3px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                    <a href="{d.get('url', '#')}" target="_blank" style="color:var(--text-color);text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{d.get('title', '?')[:90]}</a>
                    {roi_badge}
                </div>
                <div style="font-size:12px;color:var(--text-secondary)">
                    💵 {price_str}{rrp_str} | 📁 {d.get('category', '-')}
                </div>
                <div style="font-size:11px;color:var(--text-secondary);margin-top:2px">
                    {source_emoji} {d['source'].title()} | {kw_str if kw_str else '-'} | {time_str}
                </div>
            </div>
        </div>'''

    if not deals_html:
        deals_html = '<div style="padding:30px;text-align:center;color:var(--text-secondary)">Brak znalezionych okazji. Uruchom skan lub poczekaj na harmonogram.</div>'

    kw_tags = ' '.join([f'<span style="display:inline-block;background:var(--accent-color);color:white;padding:2px 8px;border-radius:12px;font-size:12px;margin:2px">{k}</span>' for k in keywords[:20]])

    _msg = request.args.get('msg', '')
    _err = request.args.get('err', '')
    _alert = ''
    if _msg:
        _alert = f'<div style="padding:10px;margin-bottom:12px;background:rgba(0,180,0,0.1);border-radius:8px;text-align:center;font-size:14px">✅ {_msg}</div>'
    elif _err:
        _alert = f'<div style="padding:10px;margin-bottom:12px;background:rgba(255,0,0,0.1);border-radius:8px;text-align:center;font-size:14px">❌ {_err}</div>'

    content = f'''
    <div class="hdr"><h1>🔍 Monitor Okazji Palet</h1></div>
    {_alert}
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:15px">
        <div class="card" style="padding:12px;text-align:center">
            <div style="font-size:24px;font-weight:700">{stats.get('today_new', 0)}</div>
            <div style="font-size:11px;color:var(--text-secondary)">Nowe dzisiaj</div>
        </div>
        <div class="card" style="padding:12px;text-align:center">
            <div style="font-size:24px;font-weight:700">🏪 {stats.get('warrington_total', 0)}</div>
            <div style="font-size:11px;color:var(--text-secondary)">Warrington</div>
        </div>
        <div class="card" style="padding:12px;text-align:center">
            <div style="font-size:24px;font-weight:700">🎪 {stats.get('jobalots_total', 0)}</div>
            <div style="font-size:11px;color:var(--text-secondary)">Jobalots</div>
        </div>
    </div>

    <details style="margin-bottom:15px">
        <summary class="card" style="padding:12px;cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center">
            <span style="font-weight:600">📊 Statystyki i koszty AI</span>
            <span style="font-size:12px;color:var(--text-secondary)">
                Dzisiaj: ${costs.get('today_all_ai_cost',0):.4f} | Miesiąc: ${costs.get('month_all_ai_cost',0):.4f} | Zaoszcz: ~{costs.get('month_time_saved_min',0)//60}h
            </span>
        </summary>
        <div class="card" style="padding:0;margin-top:-8px;border-top:none;border-top-left-radius:0;border-top-right-radius:0">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:0">
                <div style="padding:12px;border-bottom:1px solid var(--border-color);border-right:1px solid var(--border-color)">
                    <div style="font-weight:600;font-size:13px;margin-bottom:8px">📅 Dzisiaj</div>
                    <div style="font-size:12px;line-height:1.8">
                        🔍 Skanów palet: <b>{costs.get('today_scans',0)}</b><br>
                        📦 Przeskanowanych: <b>{costs.get('today_scraped',0)}</b><br>
                        ✨ Nowych deali: <b>{costs.get('today_new_deals',0)}</b><br>
                        ⏱️ Czas skanów: <b>{costs.get('today_scan_time',0):.0f}s</b>
                    </div>
                </div>
                <div style="padding:12px;border-bottom:1px solid var(--border-color)">
                    <div style="font-weight:600;font-size:13px;margin-bottom:8px">📆 Ten miesiąc</div>
                    <div style="font-size:12px;line-height:1.8">
                        🔍 Skanów palet: <b>{costs.get('month_scans',0)}</b><br>
                        📦 Przeskanowanych: <b>{costs.get('month_scraped',0)}</b><br>
                        ✨ Nowych deali: <b>{costs.get('month_new_deals',0)}</b><br>
                        ⏱️ Czas skanów: <b>{costs.get('month_scan_time',0):.0f}s</b>
                    </div>
                </div>
            </div>
            <div style="padding:12px;border-bottom:1px solid var(--border-color)">
                <div style="font-weight:600;font-size:13px;margin-bottom:8px">🤖 Koszty AI — ten miesiąc</div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
                    <div style="font-size:12px;line-height:1.8;background:rgba(139,92,246,0.05);padding:8px;border-radius:8px">
                        <div style="font-weight:600;color:#8b5cf6;margin-bottom:4px">Perplexity (analiza palet)</div>
                        Wywołań: <b>{costs.get('month_ai_calls',0)}</b><br>
                        Tokeny: <b>{costs.get('month_ai_tokens',0):,}</b><br>
                        Koszt: <b>${costs.get('month_ai_cost',0):.4f}</b>
                    </div>
                    <div style="font-size:12px;line-height:1.8;background:rgba(59,130,246,0.05);padding:8px;border-radius:8px">
                        <div style="font-weight:600;color:#3b82f6;margin-bottom:4px">Gemini (oferty Allegro)</div>
                        Wywołań: <b>{costs.get('month_gemini_calls',0)}</b><br>
                        Tokeny: <b>{costs.get('month_gemini_tokens',0):,}</b><br>
                        Koszt: <b>${costs.get('month_gemini_cost',0):.5f}</b>
                    </div>
                </div>
                {''.join(f'<div style="font-size:11px;color:var(--text-secondary);margin-top:6px">  └ {ctx}: {cnt}x (${c:.5f})</div>' for ctx, cnt, c in costs.get('gemini_breakdown', []))}
            </div>
            <div style="padding:12px;border-bottom:1px solid var(--border-color)">
                <div style="font-weight:600;font-size:13px;margin-bottom:8px">📈 System od początku ({costs.get('system_start','?')})</div>
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:8px;text-align:center">
                    <div style="background:rgba(59,130,246,0.08);padding:8px;border-radius:8px">
                        <div style="font-size:20px;font-weight:700">{costs.get('total_products',0)}</div>
                        <div style="font-size:10px;color:var(--text-secondary)">Produktów</div>
                    </div>
                    <div style="background:rgba(139,92,246,0.08);padding:8px;border-radius:8px">
                        <div style="font-size:20px;font-weight:700">{costs.get('total_offers',0)}</div>
                        <div style="font-size:10px;color:var(--text-secondary)">Ofert Allegro</div>
                    </div>
                    <div style="background:rgba(16,185,129,0.08);padding:8px;border-radius:8px">
                        <div style="font-size:20px;font-weight:700">{costs.get('total_sales',0)}</div>
                        <div style="font-size:10px;color:var(--text-secondary)">Sprzedaży</div>
                    </div>
                    <div style="background:rgba(245,158,11,0.08);padding:8px;border-radius:8px">
                        <div style="font-size:20px;font-weight:700">{costs.get('total_pallets',0)}</div>
                        <div style="font-size:10px;color:var(--text-secondary)">Palet</div>
                    </div>
                    <div style="background:rgba(239,68,68,0.08);padding:8px;border-radius:8px">
                        <div style="font-size:20px;font-weight:700">{costs.get('total_revenue',0):,.0f}</div>
                        <div style="font-size:10px;color:var(--text-secondary)">Przychód PLN</div>
                    </div>
                </div>
                <div style="font-size:11px;color:var(--text-secondary);margin-top:6px;text-align:center">
                    Ten miesiąc: {costs.get('month_products',0)} prod. | {costs.get('month_offers',0)} ofert | {costs.get('month_sales',0)} sprzedaży | {costs.get('month_revenue',0):,.0f} PLN
                </div>
            </div>
            <div style="padding:12px;display:grid;grid-template-columns:1fr 1fr;gap:12px">
                <div>
                    <div style="font-weight:600;font-size:13px;margin-bottom:6px">⏰ Zaoszczędzony czas</div>
                    <div style="font-size:12px;line-height:1.8">
                        Ten miesiąc: <b>~{costs.get('month_time_saved_min',0)} min</b> (~{costs.get('month_time_saved_h',0)}h)<br>
                        <b style="font-size:14px;color:var(--accent-green)">Łącznie: ~{costs.get('total_time_saved_h',0)}h</b> ({costs.get('total_time_saved_min',0):,} min)<br>
                        <span style="color:var(--text-secondary);font-size:11px">~15 min/ofertę | ~5 min/skan | ~2 min/produkt</span>
                    </div>
                </div>
                <div>
                    <div style="font-weight:600;font-size:13px;margin-bottom:6px">💰 Koszty AI (all-time)</div>
                    <div style="font-size:12px;line-height:1.8">
                        Perplexity: <b>${costs.get('total_ai_cost',0):.4f}</b> ({costs.get('total_ai_calls',0)}x)<br>
                        Gemini: <b>${costs.get('total_gemini_cost',0):.5f}</b> ({costs.get('total_gemini_calls',0)}x)<br>
                        <b style="color:var(--accent-color)">Razem: ${costs.get('total_all_ai_cost',0):.4f}</b> (~{round(costs.get('total_all_ai_cost',0)*4.2,2):.2f} PLN)
                    </div>
                </div>
            </div>
        </div>
    </details>

    <div style="display:flex;gap:8px;margin-bottom:15px;flex-wrap:wrap">
        <button onclick="doScan('warrington',this)" class="btn" style="flex:1;min-width:120px;text-align:center;background:var(--accent-blue);padding:12px;margin:0">🏪 Skanuj Warrington</button>
        <button onclick="doScan('jobalots',this)" class="btn" style="flex:1;min-width:120px;text-align:center;background:var(--accent-purple);padding:12px;margin:0">🎪 Skanuj Jobalots</button>
        <button onclick="doScan('all',this)" class="btn" style="flex:1;min-width:120px;text-align:center;background:var(--accent-green);padding:12px;margin:0">🔄 Skanuj wszystko</button>
    </div>
    <div id="scanStatus" style="display:none;text-align:center;padding:12px;margin-bottom:15px;background:var(--card-bg);border-radius:10px;border:1px solid var(--border-color)">
        <div style="display:inline-block;width:20px;height:20px;border:3px solid var(--border-color);border-top-color:var(--accent-color);border-radius:50%;animation:spin 0.8s linear infinite;vertical-align:middle"></div>
        <span id="scanText" style="margin-left:8px;vertical-align:middle">Skanowanie...</span>
    </div>
    <style>@keyframes spin{{from{{transform:rotate(0deg)}}to{{transform:rotate(360deg)}}}}</style>
    <script>
    function doScan(source, btn) {{
        var btns = btn.parentElement.querySelectorAll('button');
        btns.forEach(function(b){{ b.disabled=true; b.style.opacity='0.5'; }});
        var st = document.getElementById('scanStatus');
        var tx = document.getElementById('scanText');
        var labels = {{warrington:'Skanowanie Warrington...', jobalots:'Skanowanie Jobalots...', all:'Skanowanie wszystkiego...'}};
        tx.textContent = labels[source] || 'Skanowanie...';
        st.style.display = 'block';
        fetch('/monitor/scan?source=' + source)
            .then(function(r){{ return r.json(); }})
            .then(function(d){{
                if(d.ok){{
                    tx.textContent = '✅ ' + d.msg;
                    st.style.background = 'rgba(0,180,0,0.1)';
                }} else {{
                    tx.textContent = '❌ ' + (d.err||'Błąd');
                    st.style.background = 'rgba(255,0,0,0.1)';
                }}
                setTimeout(function(){{ window.location.href = '/monitor'; }}, 1500);
            }})
            .catch(function(){{ window.location.href = '/monitor'; }});
    }}
    </script>

    <div class="card" style="padding:12px;margin-bottom:15px">
        <div style="font-weight:600;margin-bottom:8px">Keywords:</div>
        <div>{kw_tags}</div>
        <a href="/monitor/keywords" style="font-size:12px;color:var(--accent-color)">Edytuj keywords</a>
        <div style="font-size:11px;color:var(--text-secondary);margin-top:5px">
            Harmonogram: Warrington 10-11, 16-17 co 5min | Jobalots co 2h (8:00-22:00)
        </div>
    </div>

    <div class="card" style="padding:0;overflow:hidden">
        <div style="padding:12px;border-bottom:1px solid var(--border-color);font-weight:600">
            Znalezione okazje ({len(deals)})
        </div>
        {deals_html}
    </div>

    <a href="/" class="back" style="margin-top:15px">← Powrót</a>
    '''
    return render_template('monitor.html',
        version=VERSION,
        content=content,
        active_monitor='active', active_home='', active_magazyn='',
        active_paletomat='', active_allegro='', active_narzedzia='')


@app.route('/monitor/scan')
def monitor_scan():
    """Ręczne uruchomienie skanowania"""
    source = request.args.get('source', 'all')
    from modules.pallet_monitor import run_monitor
    try:
        new_deals, all_matched = run_monitor(source=source, notify=True)
        msg = f'Skan {source}: {len(new_deals)} nowych, {len(all_matched)} matched'
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'fetch' in request.headers.get('Sec-Fetch-Mode', ''):
            return jsonify({'ok': True, 'msg': msg, 'new': len(new_deals), 'matched': len(all_matched)})
        return redirect(f'/monitor?msg={msg.replace(" ", "+")}')
    except Exception as e:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'fetch' in request.headers.get('Sec-Fetch-Mode', ''):
            return jsonify({'ok': False, 'err': str(e)[:80]})
        return redirect(f'/monitor?err={str(e)[:80]}')


@app.route('/monitor/keywords', methods=['GET', 'POST'])
def monitor_keywords():
    """Edycja keywords"""
    from modules.pallet_monitor import get_keywords, save_keywords

    if request.method == 'POST':
        raw = request.form.get('keywords', '')
        keywords = [k.strip() for k in raw.split('\n') if k.strip()]
        save_keywords(keywords)
        return redirect('/monitor')

    keywords = get_keywords()
    kw_text = '\n'.join(keywords)

    content = '<div class="hdr"><h1>Keywords Monitora</h1></div>'
    content += '<form method="POST" class="card" style="padding:15px">'
    content += '<p style="font-size:13px;color:var(--text-secondary)">Jedno slowo kluczowe na linie (PL lub EN):</p>'
    content += '<textarea name="keywords" rows="15" style="width:100%;padding:10px;border:1px solid var(--border-color);border-radius:8px;font-size:14px;background:var(--card-bg);color:var(--text-color)">' + kw_text + '</textarea>'
    content += '<button type="submit" class="btn btn-p" style="width:100%;margin-top:10px">Zapisz</button>'
    content += '</form><a href="/monitor" class="back">Powrot</a>'

    return render_template('monitor.html',
        version=VERSION,
        content=content,
        active_monitor='active', active_home='', active_magazyn='',
        active_paletomat='', active_allegro='', active_narzedzia='')


@app.route('/narzedzia')
def narzedzia():
    return render_template('narzedzia.html',
        version=VERSION,
        active_narzedzia='active', active_home='', active_magazyn='',
        active_paletomat='', active_allegro='', active_monitor='')

# KALKULATOR

@app.route('/narzedzia/kalkulator', methods=['GET', 'POST'])
def kalkulator():
    wynik = None
    cena_zakupu = request.form.get('cena_zakupu', '')
    marza = request.form.get('marza', 40)
    kategoria = request.form.get('kategoria', 'inne')
    
    if request.method == 'POST' and cena_zakupu:
        wynik = oblicz_cene_allegro(float(cena_zakupu), int(marza), kategoria)
    
    return render_template('kalkulator.html',
        version=VERSION,
        wynik=wynik, cena_zakupu=cena_zakupu, marza=marza, kategoria=kategoria,
        active_narzedzia='active', active_home='', active_magazyn='',
        active_paletomat='', active_allegro='', active_monitor='')

# GENERATOR OPISÓW

@app.route('/narzedzia/generator', methods=['GET', 'POST'])
def generator():
    opis = None
    nazwa = request.form.get('nazwa', '')
    kategoria = request.form.get('kategoria', 'inne')
    
    if request.method == 'POST' and nazwa:
        opis = generuj_opis_ai(nazwa, kategoria)
    
    return render_template('generator.html',
        version=VERSION,
        opis=opis, nazwa=nazwa, kategoria=kategoria,
        active_narzedzia='active', active_home='', active_magazyn='',
        active_paletomat='', active_allegro='', active_monitor='')

# EXPORT

@app.route('/narzedzia/export', methods=['GET', 'POST'])
def narzedzia_export():
    if request.method == 'POST':
        return redirect('/magazyn/export')
    return render_template('export.html',
        version=VERSION,
        active_narzedzia='active', active_home='', active_magazyn='',
        active_paletomat='', active_allegro='', active_monitor='')

# RAPORTY

@app.route('/narzedzia/raporty')
def narzedzia_raporty():
    from modules.database import get_palety_list
    
    # Używamy get_palety_list() która JUŻ poprawnie liczy sprzedaż!
    all_palety = get_palety_list(limit=1000)
    
    palety = []
    for pal_row in all_palety:
        # Konwertuj Row na dict
        pal = dict(pal_row)
        
        # Koszt palety: cena_zakupu z palety, fallback na sumę kosztów produktów
        zakup_paleta = float(pal.get('cena_zakupu') or 0)
        koszt_all = float(pal.get('koszt_produktow_all') or 0)
        zakup_produkty = float(pal.get('wartosc_zakupu_produktow') or 0)
        
        # Użyj cena_zakupu palety jeśli > 0, potem suma brutto/netto produktów
        if zakup_paleta > 0:
            zakup = zakup_paleta
        elif koszt_all > 0:
            zakup = koszt_all
        elif zakup_produkty > 0:
            zakup = zakup_produkty
        else:
            zakup = 0
        
        # Użyj sprzedano_wartosc_tabela (faktyczna sprzedaż z tabeli sprzedaze)
        # lub sprzedano_wartosc_status (ceny produktów sprzedanych) jako fallback
        allegro_tabela = float(pal.get('sprzedano_wartosc_tabela') or 0)
        allegro_status = float(pal.get('sprzedano_wartosc_status') or 0)
        offline = float(pal.get('przychod_offline') or 0)

        # FIX: sprzedano_wartosc_tabela JUŻ ZAWIERA offline (kupujacy='offline')
        # NIE dodawaj przychod_offline osobno — to podwójne liczenie!
        if allegro_tabela > 0:
            przychod_total = allegro_tabela
        else:
            przychod_total = allegro_status + offline

        prowizja = przychod_total * 0.11
        zysk = przychod_total - zakup - prowizja
        roi = (zysk / zakup * 100) if zakup > 0 else 0

        # Postęp sprzedaży
        produktow = int(pal.get('produktow') or 0)
        sprzedano_tabela = int(pal.get('sprzedano_tabela') or 0)
        sprzedano_status = int(pal.get('sprzedano_status') or 0)
        sprzedano_offline = int(pal.get('sprzedano_offline') or pal.get('sprzedano_offline_szt') or 0)
        # FIX: sprzedano_tabela zawiera już offline
        if sprzedano_tabela > 0:
            sprzedano = sprzedano_tabela
        else:
            sprzedano = sprzedano_status + sprzedano_offline
        
        palety.append({
            'id': pal.get('nazwa') or 'Bez nazwy',
            'cnt': produktow,
            'dostawca': pal.get('dostawca') or 'Nieznany',
            'zakup': f"{zakup:.0f}",
            'allegro': f"{przychod_total:.0f}",
            'zysk': f"{zysk:.0f}",
            'roi': f"{roi:.1f}",
            'roi_num': roi,  # do sortowania
            'sprzedano': sprzedano,
            'zysk_num': zysk
        })
    
    # Sortuj po ROI malejąco
    palety.sort(key=lambda x: x['roi_num'], reverse=True)
    
    return render_template('raporty.html',
        version=VERSION,
        palety=palety,
        active_narzedzia='active', active_home='', active_magazyn='',
        active_paletomat='', active_allegro='', active_monitor='')


# CLOUD EXPORT - eksport do chmury
@app.route('/narzedzia/cloud-export')
def narzedzia_cloud_export():
    """Strona eksportu do chmury"""
    try:
        from modules.cloud_export import get_export_files, EXPORT_DIR
        files = get_export_files()
        export_dir = str(EXPORT_DIR)
    except:
        files = []
        export_dir = 'cloud_exports'
    
    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>☁️ EKSPORT DO CHMURY</h1>
            <small>CSV do synchronizacji z Google Drive / Dropbox</small>
        </div>
        
        <div class="card" style="padding:15px;margin-bottom:15px">
            <div style="font-weight:600;margin-bottom:10px">📁 Folder eksportów:</div>
            <div style="font-size:0.85rem;color:#64748b;background:#0a0a0f;padding:10px;border-radius:6px;font-family:monospace">
                {export_dir}
            </div>
            <div style="font-size:0.75rem;color:#94a3b8;margin-top:8px">
                💡 Zsynchronizuj ten folder z Google Drive lub Dropbox żeby mieć automatyczny backup w chmurze
            </div>
        </div>
        
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:15px">
            <a href="/api/cloud/export/palety" class="btn" style="display:block;text-align:center;padding:15px;background:#22c55e;border-radius:10px;color:#fff;text-decoration:none;font-weight:600">
                📦 Eksportuj palety
            </a>
            <a href="/api/cloud/export/produkty" class="btn" style="display:block;text-align:center;padding:15px;background:#3b82f6;border-radius:10px;color:#fff;text-decoration:none;font-weight:600">
                📋 Eksportuj produkty
            </a>
        </div>
        
        <button onclick="doBackup()" class="btn" style="width:100%;padding:14px;background:#8b5cf6;border:none;border-radius:10px;color:#fff;font-weight:600;cursor:pointer;margin-bottom:15px">
            💾 Zrób backup teraz (palety + produkty)
        </button>
        
        <div class="section-title">📋 OSTATNIE EKSPORTY</div>
        <div style="background:#12121a;border-radius:12px;padding:12px">
    '''
    
    if files:
        for f in files[:10]:
            icon = '📦' if 'palety' in f['name'] else '📋'
            html += f'''
            <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #1e1e2e">
                <div>
                    <div style="font-size:0.85rem">{icon} {f['name']}</div>
                    <div style="font-size:0.7rem;color:#64748b">{f['modified']} • {f['size_kb']:.1f} KB</div>
                </div>
            </div>
            '''
    else:
        html += '<div style="color:#64748b;text-align:center;padding:20px">Brak eksportów</div>'
    
    html += '''
        </div>
        
        <div class="card" style="padding:15px;margin-top:15px;background:#f59e0b22;border:1px solid #f59e0b">
            <div style="font-weight:600;color:#f59e0b;margin-bottom:8px">⏰ Automatyczny backup</div>
            <div style="font-size:0.85rem;color:#94a3b8">
                • Baza danych: co 1 godzinę<br>
                • Eksport CSV: co 6 godzin<br>
                • Stare backupy: usuwane automatycznie (ostatnie 7)
            </div>
        </div>
        
        <a href="/narzedzia" class="back">← Powrót</a>
    </div>
    
    <script>
    function doBackup() {
        fetch('/api/cloud/backup', {method: 'POST'})
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    alert('✅ Backup wykonany!');
                    location.reload();
                } else {
                    alert('❌ Błąd: ' + (data.error || 'Nieznany'));
                }
            })
            .catch(e => alert('❌ Błąd połączenia'));
    }
    </script>
    '''
    return html

# ============================================================
# GOAL (HYUNDAI i30 N) - ZARZĄDZANIE
# ============================================================

@app.route('/goal/details')
def goal_details():
    """Szczegóły celu finansowego"""
    from modules.simple_goal_manager import get_goal_stats
    
    goal = get_goal_stats()
    
    html = f'''
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🚗 Hyundai i30 N - Goal</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0a0a0f 0%, #1a1a2e 100%);
            color: #fff;
            padding: 20px;
            min-height: 100vh;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
        }}
        .header {{
            text-align: center;
            margin-bottom: 30px;
        }}
        .header h1 {{
            font-size: 2.5rem;
            margin-bottom: 10px;
        }}
        .goal-card {{
            background: linear-gradient(135deg, rgba(59,130,246,0.2), rgba(139,92,246,0.15));
            border: 2px solid #3b82f6;
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 20px;
        }}
        .progress-bar {{
            background: rgba(0,0,0,0.3);
            border-radius: 12px;
            height: 30px;
            overflow: hidden;
            margin: 20px 0;
        }}
        .progress-fill {{
            background: linear-gradient(90deg, #22c55e, #16a34a);
            height: 100%;
            transition: width 0.5s;
            display: flex;
            align-items: center;
            justify-content: flex-end;
            padding-right: 10px;
        }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
            margin: 20px 0;
        }}
        .stat {{
            text-align: center;
            padding: 20px;
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
        }}
        .stat-label {{
            font-size: 0.8rem;
            color: #64748b;
            margin-bottom: 5px;
        }}
        .stat-value {{
            font-size: 1.8rem;
            font-weight: 700;
        }}
        .form-group {{
            margin-bottom: 20px;
        }}
        .form-group label {{
            display: block;
            margin-bottom: 8px;
            color: #94a3b8;
        }}
        .form-group input {{
            width: 100%;
            padding: 12px;
            background: rgba(255,255,255,0.1);
            border: 2px solid rgba(255,255,255,0.2);
            border-radius: 8px;
            color: #fff;
            font-size: 1rem;
        }}
        .btn {{
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            font-size: 1rem;
            cursor: pointer;
            transition: all 0.3s;
        }}
        .btn-primary {{
            background: #3b82f6;
            color: #fff;
        }}
        .btn-primary:hover {{
            background: #2563eb;
            transform: translateY(-2px);
        }}
        .btn-success {{
            background: #22c55e;
            color: #fff;
        }}
        .btn-danger {{
            background: #ef4444;
            color: #fff;
        }}
        .actions {{
            display: flex;
            gap: 10px;
            margin-top: 20px;
        }}
        .back-btn {{
            display: inline-block;
            padding: 10px 20px;
            background: rgba(255,255,255,0.1);
            color: #fff;
            text-decoration: none;
            border-radius: 8px;
            margin-bottom: 20px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <a href="/" class="back-btn">← Powrót</a>
        
        <div class="header">
            <h1>🚗 {goal['name']}</h1>
            <p style="color: #64748b;">Zarządzanie celem finansowym</p>
        </div>
        
        <div class="goal-card">
            <h2 style="margin-bottom: 20px;">Postęp: {goal.get('progress', 0)}%</h2>
            
            <div class="progress-bar">
                <div class="progress-fill" style="width: {goal.get('progress', 0)}%">
                    <span style="color: #fff; font-weight: 700;">{goal['current']:,.0f} PLN</span>
                </div>
            </div>
            
            <div class="stats">
                <div class="stat">
                    <div class="stat-label">UZBIERANE</div>
                    <div class="stat-value" style="color: #22c55e;">{goal['current']:,.0f} PLN</div>
                </div>
                <div class="stat">
                    <div class="stat-label">CEL</div>
                    <div class="stat-value" style="color: #3b82f6;">{goal['target']:,.0f} PLN</div>
                </div>
                <div class="stat">
                    <div class="stat-label">POZOSTAŁO</div>
                    <div class="stat-value" style="color: #f59e0b;">{goal['remaining']:,.0f} PLN</div>
                </div>
            </div>
        </div>
        
        <div class="goal-card">
            <h3 style="margin-bottom: 20px;">✏️ Edytuj Goal</h3>
            
            <form action="/goal/update" method="POST">
                <div class="form-group">
                    <label>Uzbierana kwota (PLN):</label>
                    <input type="number" name="current" value="{goal['current']:.0f}" step="0.01" required>
                </div>
                
                <div class="form-group">
                    <label>Cel (PLN):</label>
                    <input type="number" name="target" value="{goal['target']:.0f}" step="0.01" required>
                </div>
                
                <div class="form-group">
                    <label>Nazwa celu:</label>
                    <input type="text" name="name" value="{goal['name']}" required>
                </div>
                
                <button type="submit" class="btn btn-primary">💾 Zapisz zmiany</button>
            </form>
        </div>
        
        <div class="goal-card">
            <h3 style="margin-bottom: 20px;">💰 Szybkie akcje</h3>
            
            <form action="/goal/add" method="POST" style="margin-bottom: 15px;">
                <div class="form-group">
                    <label>Dodaj kwotę (PLN):</label>
                    <input type="number" name="amount" placeholder="np. 5000" step="0.01" required>
                </div>
                <button type="submit" class="btn btn-success">➕ Dodaj</button>
            </form>
            
            <form action="/goal/subtract" method="POST" style="margin-bottom: 15px;">
                <div class="form-group">
                    <label>Odejmij kwotę (PLN):</label>
                    <input type="number" name="amount" placeholder="np. 1000" step="0.01" required>
                </div>
                <button type="submit" class="btn btn-danger">➖ Odejmij</button>
            </form>
        </div>
        
        <p style="text-align: center; color: #64748b; margin-top: 30px;">
            Ostatnia aktualizacja: {goal['updated_at'][:10]}
        </p>
    </div>
</body>
</html>
'''
    return html


@app.route('/goal/update', methods=['POST'])
def goal_update():
    """Aktualizuje goal"""
    from modules.simple_goal_manager import save_goal
    
    try:
        current = float(request.form.get('current', 0))
        target = float(request.form.get('target', 150000))
        name = request.form.get('name', 'Hyundai i30 N')
        
        save_goal(current, target, name)
        return redirect('/goal/details?success=updated')
    except Exception as e:
        return f"Error: {e}", 400


@app.route('/goal/add', methods=['POST'])
def goal_add():
    """Dodaje kwotę do goala"""
    from modules.simple_goal_manager import add_to_goal
    
    try:
        amount = float(request.form.get('amount', 0))
        if amount > 0:
            add_to_goal(amount)
        return redirect('/goal/details?success=added')
    except Exception as e:
        return f"Error: {e}", 400


@app.route('/goal/subtract', methods=['POST'])
def goal_subtract():
    """Odejmuje kwotę od goala"""
    from modules.simple_goal_manager import subtract_from_goal
    
    try:
        amount = float(request.form.get('amount', 0))
        if amount > 0:
            subtract_from_goal(amount)
        return redirect('/goal/details?success=subtracted')
    except Exception as e:
        return f"Error: {e}", 400
    try:
        from modules.goal_manager import get_current_goal
        from modules.database import get_db
        
        goal = get_current_goal()
        
        # Historia wplat na cel
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, amount, description, source_type, source_id, created_at 
            FROM goal_contributions 
            ORDER BY created_at DESC 
            LIMIT 50
        ''')
        wplaty_raw = cursor.fetchall()
        
        # Oblicz ile jeszcze zostalo
        remaining = int(goal['target'] - goal['current'])
        weeks_to_goal = int(remaining / 1000) if remaining > 0 else 0
        
        # Buduj liste wplat z przyciskami USUN
        wplaty_html_list = []
        for w in wplaty_raw:
            wpl_id = w[0]
            amount = int(w[1])
            desc = str(w[2] or 'Wplata')
            data = str(w[5])[:10] if w[5] else '---'
            
            wplata_item = '<div style="background:#1e1e2e;border-radius:10px;padding:12px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center">'
            wplata_item += '<div>'
            wplata_item += '<div style="font-weight:600;color:#22c55e">+' + str(amount) + ' PLN</div>'
            wplata_item += '<div style="font-size:0.75rem;color:#64748b">' + desc + ' &bull; ' + data + '</div>'
            wplata_item += '</div>'
            wplata_item += '<form action="/goal/delete-contribution" method="POST" style="margin:0">'
            wplata_item += '<input type="hidden" name="id" value="' + str(wpl_id) + '">'
            wplata_item += '<button type="submit" style="background:#ef4444;color:#fff;border:none;padding:8px 15px;border-radius:6px;cursor:pointer;font-weight:600">Usun</button>'
            wplata_item += '</form>'
            wplata_item += '</div>'
            
            wplaty_html_list.append(wplata_item)
        
        if not wplaty_html_list:
            wplaty_html = '<div style="text-align:center;color:#64748b;padding:30px">Brak wplat. Dodaj pierwsza!</div>'
        else:
            wplaty_html = ''.join(wplaty_html_list)
        
        # Buduj strone
        progress_int = int(goal.get('progress', 0))
        current_int = int(goal.get('current', 0))
        target_int = int(goal.get('target', 150000))
        
        html = CSS
        html += '<div class="container">'
        html += '<div class="header"><h1>&#x1F697; Hyundai i30 N</h1><small>Cel finansowy</small></div>'
        
        # HERO
        html += '<div style="background:linear-gradient(135deg,#1e1e2e,#0a0a0f);border-radius:20px;padding:0;margin-bottom:20px;overflow:hidden;position:relative;height:250px">'
        html += '<img src="/static/goal.jpg" style="width:100%;height:100%;object-fit:cover;opacity:0.7">'
        html += '<div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center;width:100%">'
        html += '<div style="font-size:3rem;font-weight:800;color:#fff;text-shadow:0 4px 12px rgba(0,0,0,0.8)">' + str(progress_int) + '%</div>'
        html += '<div style="font-size:1.2rem;color:#fff;text-shadow:0 2px 8px rgba(0,0,0,0.8)">DO CELU</div></div></div>'
        
        # STATS
        html += '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px">'
        html += '<div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center">'
        html += '<div style="font-size:1.5rem;font-weight:700;color:#22c55e">' + str(current_int) + ' PLN</div>'
        html += '<div style="font-size:0.7rem;color:#64748b">UZBIERANO</div></div>'
        html += '<div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center">'
        html += '<div style="font-size:1.5rem;font-weight:700;color:#f59e0b">' + str(remaining) + ' PLN</div>'
        html += '<div style="font-size:0.7rem;color:#64748b">POZOSTALO</div></div>'
        html += '<div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center">'
        html += '<div style="font-size:1.5rem;font-weight:700;color:#3b82f6">' + str(weeks_to_goal) + '</div>'
        html += '<div style="font-size:0.7rem;color:#64748b">TYGODNI (1k/tydz)</div></div></div>'
        
        # PROGRESS BAR
        html += '<div style="background:#12121a;border-radius:16px;padding:20px;margin-bottom:20px">'
        html += '<div style="display:flex;justify-content:space-between;margin-bottom:10px">'
        html += '<span style="font-weight:600">Postep</span>'
        html += '<span style="color:#22c55e;font-weight:700">' + str(progress_int) + '%</span></div>'
        html += '<div style="background:#1e1e2e;border-radius:12px;height:20px;overflow:hidden">'
        html += '<div style="background:linear-gradient(90deg,#22c55e,#16a34a);height:100%;width:' + str(progress_int) + '%;transition:width 0.5s"></div></div>'
        html += '<div style="display:flex;justify-content:space-between;margin-top:8px;font-size:0.75rem;color:#64748b">'
        html += '<span>0 PLN</span><span>' + str(target_int) + ' PLN</span></div></div>'
        
        # FORMULARZ DODAWANIA
        html += '<div style="background:linear-gradient(135deg,rgba(59,130,246,0.15),rgba(139,92,246,0.1));border:1px solid rgba(59,130,246,0.3);border-radius:12px;padding:15px;margin-bottom:20px">'
        html += '<div style="font-weight:600;margin-bottom:12px;color:#3b82f6">&#x1F4B0; Dodaj wplate recznie</div>'
        html += '<form action="/goal/add-manual" method="POST">'
        html += '<div style="display:flex;gap:10px;margin-bottom:10px">'
        html += '<input type="number" name="amount" placeholder="Kwota PLN" required min="1" step="1" style="flex:1;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">'
        html += '<input type="text" name="description" placeholder="Opis" required style="flex:2;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">'
        html += '</div>'
        html += '<button type="submit" style="width:100%;padding:12px;background:#22c55e;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">Dodaj wplate</button>'
        html += '</form></div>'
        
        # HISTORIA
        html += '<div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin-bottom:10px">&#x1F4CB; HISTORIA WPLAT</div>'
        html += wplaty_html
        
        html += '<a href="/" class="back" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:15px">&larr; Dashboard</a>'
        html += '</div>'
        
        return html
        
    except Exception as e:
        import traceback
        return '<html><body style="background:#000;color:#fff;padding:20px"><h1>ERROR:</h1><pre>' + str(e) + '\n\n' + traceback.format_exc() + '</pre></body></html>', 500

# ============================================================
# EXTRAKTOR ALLEGRO - REGENERUJ META TITLE
# ============================================================
@app.route('/produkty/<int:produkt_id>/regenerate-meta-title', methods=['POST', 'OPTIONS'])
def produkt_regenerate_meta_title(produkt_id):
    """Regeneruje meta_title dla pojedynczego produktu"""
    # CORS preflight
    if request.method == 'OPTIONS':
        response = jsonify({'success': True})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response
    
    from modules.database import get_db
    
    try:
        # Import funkcji z gemini_config
        try:
            from gemini_config import GEMINI_API_KEY
            from google import genai  # NOWE API!
            
            if not GEMINI_API_KEY or GEMINI_API_KEY == 'WKLEJ_TUTAJ_SWOJ_KLUCZ':
                response = jsonify({'success': False, 'error': 'Brak klucza Gemini API'})
                response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
                return response
            
            # NOWE API - Client
            client = genai.Client(api_key=GEMINI_API_KEY)
            
        except Exception as e:
            response = jsonify({'success': False, 'error': f'Gemini niedostępne: {str(e)}'})
            response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
            return response
        
        # Pobierz produkt
        conn = get_db()
        produkt = conn.execute('SELECT nazwa, ean, asin FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
        
        if not produkt:
            response = jsonify({'success': False, 'error': 'Produkt nie znaleziony'})
            response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
            return response
        
        # Generuj meta_title
        from modules.smart_importer import generate_meta_title
        meta_title = generate_meta_title(
            produkt_nazwa=produkt['nazwa'] or '',
            produkt_ean=produkt['ean'] or '',
            produkt_asin=produkt['asin'] or ''
        )
        
        if not meta_title:
            response = jsonify({'success': False, 'error': 'Nie udało się wygenerować tytułu'})
            response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
            return response
        
        # Zapisz do bazy
        conn.execute('UPDATE produkty SET meta_title = ? WHERE id = ?', (meta_title, produkt_id))
        conn.commit()
        
        response = jsonify({'success': True, 'meta_title': meta_title})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        return response
        
    except Exception as e:
        response = jsonify({'success': False, 'error': str(e)})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        return response

# ============================================================
# EXTRAKTOR ALLEGRO - BATCH GENERATION
# ============================================================
@app.route('/api/generate_meta_title_batch', methods=['POST', 'OPTIONS'])
def generate_meta_title_batch():
    """Generuje meta_title dla wielu produktów naraz"""
    # CORS preflight
    if request.method == 'OPTIONS':
        response = jsonify({'success': True})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response
    
    import time
    from modules.database import get_db
    
    try:
        data = request.get_json()
        product_ids = data.get('product_ids', [])
        
        # BATCH SIZE LIMIT (zwiększony dla paid tier)
        MAX_BATCH_SIZE = 100  # Zwiększone z 10 na 100 dla paid tier
        if len(product_ids) > MAX_BATCH_SIZE:
            response = jsonify({
                'success': False, 
                'error': f'Zbyt dużo produktów! Max {MAX_BATCH_SIZE} na raz. Zaznacz mniej produktów lub podziel na mniejsze batche.'
            })
            response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
            return response
        
        if not product_ids:
            response = jsonify({'success': False, 'error': 'Brak produktów do przetworzenia'})
            response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
            return response
        
        # Sprawdź API key
        try:
            from gemini_config import GEMINI_API_KEY
            
            if not GEMINI_API_KEY or GEMINI_API_KEY == 'WKLEJ_TUTAJ_SWOJ_KLUCZ':
                response = jsonify({'success': False, 'error': 'Brak klucza Gemini API w gemini_config.py'})
                response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
                return response
            
        except Exception as e:
            response = jsonify({'success': False, 'error': f'Gemini niedostępne: {str(e)}'})
            response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
            return response
        
        conn = get_db()
        results = {
            'success': True,
            'total': len(product_ids),
            'generated': 0,
            'failed': 0,
            'details': []
        }
        
        # Generuj dla każdego produktu
        print(f"\n🚀 [BATCH START] Przetwarzam {len(product_ids)} produktów...")
        
        for idx, product_id in enumerate(product_ids, 1):
            try:
                print(f"\n📦 [{idx}/{len(product_ids)}] Processing product ID: {product_id}")
                
                # Pobierz produkt
                produkt = conn.execute('SELECT nazwa, ean, asin FROM produkty WHERE id = ?', (product_id,)).fetchone()
                
                if not produkt:
                    print(f"   ✗ Produkt nie znaleziony w bazie!")
                    results['failed'] += 1
                    results['details'].append({
                        'id': product_id,
                        'status': 'error',
                        'error': 'Produkt nie znaleziony'
                    })
                    continue
                
                print(f"   → Nazwa z bazy: {produkt['nazwa'][:50]}...")
                
                # Generuj meta_title
                from modules.smart_importer import generate_meta_title
                meta_title = generate_meta_title(
                    produkt_nazwa=produkt['nazwa'] or '',
                    produkt_ean=produkt['ean'] or '',
                    produkt_asin=produkt['asin'] or ''
                )
                
                print(f"   ← Otrzymano meta_title: {meta_title[:75] if meta_title else 'BRAK'}")
                
                if meta_title:
                    # Zapisz do bazy
                    conn.execute('UPDATE produkty SET meta_title = ? WHERE id = ?', (meta_title, product_id))
                    conn.commit()
                    print(f"   ✓ Zapisano do bazy")
                    
                    results['generated'] += 1
                    results['details'].append({
                        'id': product_id,
                        'status': 'success',
                        'meta_title': meta_title
                    })
                else:
                    print(f"   ✗ Brak meta_title (puste)")
                    results['failed'] += 1
                    results['details'].append({
                        'id': product_id,
                        'status': 'error',
                        'error': 'Nie udało się wygenerować tytułu'
                    })
                
                # Delay dla rate limiting (WOLNIEJ = STABILNIEJ)
                if idx < len(product_ids):
                    # Start z większym delay dla stabilności
                    if not hasattr(generate_meta_title_batch, '_api_delay'):
                        generate_meta_title_batch._api_delay = 2.0  # 2s = ~30 req/min (BEZPIECZNY!)
                    
                    print(f"   ⏳ Czekam {generate_meta_title_batch._api_delay}s przed następnym...")
                    time.sleep(generate_meta_title_batch._api_delay)
                
            except Exception as e:
                error_msg = str(e)
                
                # Sprawdź czy to błąd quota (429)
                if '429' in error_msg or 'quota' in error_msg.lower() or 'exceeded' in error_msg.lower():
                    # AUTO-SLOWDOWN: zwiększ delay
                    if not hasattr(generate_meta_title_batch, '_api_delay'):
                        generate_meta_title_batch._api_delay = 2.0  # Start z 2s
                    
                    old_delay = generate_meta_title_batch._api_delay
                    generate_meta_title_batch._api_delay = min(old_delay * 2, 10.0)  # Max 10s
                    
                    print(f"   ⚠️  QUOTA EXCEEDED! Zwiększam delay: {old_delay}s → {generate_meta_title_batch._api_delay}s")
                    print(f"   💡 WOLNIEJ = STABILNIEJ!")
                    
                    results['failed'] += 1
                    results['details'].append({
                        'id': product_id,
                        'status': 'error',
                        'error': f'⏰ Quota exceeded! Zwiększono delay do {generate_meta_title_batch._api_delay}s. Upgrade do PAID = 2000 RPM (tylko dodaj kartę!)'
                    })
                    # NIE przerywaj - spróbuj dalej z większym delay
                    continue
                else:
                    results['failed'] += 1
                    results['details'].append({
                        'id': product_id,
                        'status': 'error',
                        'error': error_msg
                    })
        
        response = jsonify(results)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        return response
        
    except Exception as e:
        response = jsonify({'success': False, 'error': str(e)})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        return response

# ============================================================
# EXTRAKTOR ALLEGRO - UI
# ============================================================
@app.route('/produkty/<int:produkt_id>/extract-params')
def produkt_extract_params(produkt_id):
    """Strona z parametrami Allegro wygenerowanymi przez AI"""
    from modules.database import get_db
    
    conn = get_db()
    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    
    if not produkt:
        return redirect('/palety')
    
    # Sprawdź czy Gemini jest dostępne
    if not GEMINI_CLIENT:
        error_html = CSS + '''
        <div class="container">
            <div class="header">
                <h1>⚠️ Extraktor Allegro</h1>
                <small>Gemini AI niedostępne</small>
            </div>
            <div style="background:#1e1e2e;padding:20px;border-radius:12px;margin-bottom:20px">
                <p style="color:#ef4444">Aby użyć Extraktora Allegro, ustaw GEMINI_API_KEY w gemini_config.py:</p>
                <code style="background:#0a0a0f;padding:10px;display:block;margin-top:10px;color:#22c55e">
                GEMINI_API_KEY = 'twoj_klucz_api'
                </code>
                <p style="margin-top:15px;color:#64748b;font-size:0.9rem">
                Klucz API możesz uzyskać na: <a href="https://aistudio.google.com/apikey" target="_blank" style="color:#3b82f6">Google AI Studio</a>
                </p>
            </div>
            <a href="/palety" class="back">&larr; Powrót do palet</a>
        </div>
        '''
        return error_html
    
    # Generuj parametry
    # Dociągnij bullet_points ze scraped żeby tytuł był lepszy
    bullet_points = []
    if produkt.get('asin'):
        import json as _json
        scraped_row = conn.execute('SELECT bullet_points FROM scraped WHERE asin = ?', (produkt['asin'],)).fetchone()
        if scraped_row and scraped_row['bullet_points']:
            try:
                bullet_points = _json.loads(scraped_row['bullet_points'])
            except:
                pass
    
    result = extract_allegro_params(
        produkt_nazwa=produkt['nazwa'] or '',
        produkt_ean=produkt['ean'] or '',
        produkt_asin=produkt['asin'] or '',
        bullet_points=bullet_points
    )
    
    # Sprawdź błędy
    if 'error' in result and result['error']:
        error_html = CSS + f'''
        <div class="container">
            <div class="header">
                <h1>❌ Błąd Extraktora</h1>
                <small>Produkt #{produkt_id}</small>
            </div>
            <div style="background:#1e1e2e;padding:20px;border-radius:12px;margin-bottom:20px">
                <p style="color:#ef4444">{result['error']}</p>
            </div>
            <a href="javascript:history.back()" class="back">&larr; Powrót</a>
        </div>
        '''
        return error_html
    
    meta_title = result.get('meta_title', '')
    params = result.get('params', {})
    
    # Buduj tabelkę parametrów
    params_html = ''
    for key, value in params.items():
        params_html += f'''
        <tr style="border-bottom:1px solid #2a2a3a">
            <td style="padding:12px;color:#64748b;font-weight:600">{key}</td>
            <td style="padding:12px;color:#fff">{value}</td>
        </tr>
        '''
    
    if not params_html:
        params_html = '<tr><td colspan="2" style="padding:20px;text-align:center;color:#64748b">Brak parametrów</td></tr>'
    
    # Strona wyników
    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>🤖 Extraktor Allegro</h1>
            <small>Produkt #{produkt_id}</small>
        </div>
        
        <!-- META TITLE -->
        <div style="background:linear-gradient(135deg,rgba(34,197,94,0.15),rgba(16,185,129,0.1));border:2px solid rgba(34,197,94,0.5);border-radius:12px;padding:20px;margin-bottom:20px">
            <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin-bottom:8px">📝 META TYTUŁ ALLEGRO (Skopiuj poniżej)</div>
            <div style="background:#1e1e2e;padding:15px;border-radius:8px;font-size:1.1rem;font-weight:600;color:#22c55e;cursor:pointer" 
                 onclick="navigator.clipboard.writeText(this.innerText); alert('Skopiowano do schowka!')">
                {meta_title or 'Brak tytułu'}
            </div>
            <div style="font-size:0.7rem;color:#64748b;margin-top:8px">💡 Kliknij aby skopiować</div>
        </div>
        
        <!-- ORYGINALNA NAZWA -->
        <div style="background:#1e1e2e;padding:15px;border-radius:12px;margin-bottom:20px">
            <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin-bottom:8px">📦 ORYGINALNA NAZWA</div>
            <div style="color:#fff;font-size:0.9rem">{produkt['nazwa'] or 'Brak nazwy'}</div>
        </div>
        
        <!-- PARAMETRY TECHNICZNE -->
        <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin-bottom:10px">⚙️ PARAMETRY TECHNICZNE</div>
        <div style="background:#1e1e2e;border-radius:12px;overflow:hidden;margin-bottom:20px">
            <table style="width:100%;border-collapse:collapse">
                {params_html}
            </table>
        </div>
        
        <!-- PRZYCISKI -->
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:20px">
            <form action="/produkty/{produkt_id}/quick-draft" method="POST" style="margin:0">
                <input type="hidden" name="meta_title" value="{meta_title}">
                <button type="submit" style="width:100%;padding:12px;background:linear-gradient(135deg,#22c55e,#16a34a);border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">
                    🚀 Wystaw szkic
                </button>
            </form>
            <button onclick="window.print()" style="padding:12px;background:#3b82f6;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">
                🖨️ Drukuj
            </button>
            <button onclick="window.location.reload()" style="padding:12px;background:#8b5cf6;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">
                🔄 Regeneruj
            </button>
        </div>
        
        <a href="javascript:history.back()" class="back">&larr; Powrót</a>
    </div>
    '''
    
    return html

@app.route('/produkty/<int:produkt_id>/quick-draft', methods=['POST'])
def produkt_quick_draft(produkt_id):
    """Szybkie wystawienie szkicu na Allegro z wygenerowanym META_TITLE"""
    from modules.database import get_db
    from modules.allegro_api import create_offer, is_authenticated, upload_image_to_allegro
    import re
    
    meta_title = request.form.get('meta_title', '').strip()[:75]
    
    if not meta_title:
        return redirect(f'/produkty/{produkt_id}/extract-params?error=no_title')
    
    # Sprawdź autoryzację Allegro
    if not is_authenticated():
        error_html = CSS + f'''
        <div class="container">
            <div class="header">
                <h1>❌ Błąd Allegro</h1>
                <small>Produkt #{produkt_id}</small>
            </div>
            <div style="background:#1e1e2e;padding:20px;border-radius:12px;margin-bottom:20px">
                <p style="color:#ef4444;font-weight:600">Nie jesteś zalogowany do Allegro!</p>
                <p style="margin-top:10px;color:#64748b">Musisz najpierw połączyć konto Allegro w ustawieniach.</p>
                <a href="/allegro/auth" style="display:inline-block;margin-top:15px;padding:12px 24px;background:#22c55e;border-radius:8px;color:#fff;text-decoration:none;font-weight:600">
                    Połącz Allegro
                </a>
            </div>
            <a href="javascript:history.back()" class="back">&larr; Powrót</a>
        </div>
        '''
        return error_html
    
    # Pobierz dane produktu
    conn = get_db()
    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    
    if not produkt:
        return redirect('/palety')
    
    # Przygotuj dane oferty
    # Preferuj tytul_seo z scraped (wygenerowany przez AI z bullet points) nad meta_title z formularza
    tytul = meta_title
    if produkt.get('asin'):
        import json as _json
        scraped_seo = conn.execute('SELECT tytul_seo FROM scraped WHERE asin = ?', (produkt['asin'],)).fetchone()
        if scraped_seo and scraped_seo['tytul_seo'] and len(scraped_seo['tytul_seo']) > 10:
            tytul = scraped_seo['tytul_seo'][:75]
            print(f"   🎯 Używam tytul_seo ze scraped: {tytul}")
    cena = produkt['cena_allegro'] or 100.0
    ilosc = produkt['ilosc'] or 1
    ean = produkt['ean'] or None
    kategoria = produkt['kategoria'] or ''
    
    # Generuj prosty opis (albo użyj istniejącego)
    opis = produkt['opis_ai'] if produkt['opis_ai'] else f'''
    <p><strong>{produkt['nazwa']}</strong></p>
    <p>Stan: {produkt['stan'] or 'Używany'}</p>
    <p>Ilość: {ilosc} szt.</p>
    '''
    
    # Pobierz zdjęcia z kolumny images (lokalne ścieżki) lub fallback na scraped/zdjecie_url
    zdjecia = []
    
    # Sposób 1: Pobierz z produkty.images (lokalne ścieżki)
    if produkt.get('images'):
        try:
            import json
            images_data = produkt['images']
            if isinstance(images_data, str):
                zdjecia = json.loads(images_data) if images_data and images_data != '[]' else []
            elif isinstance(images_data, list):
                zdjecia = images_data
            if zdjecia:
                print(f"   📸 [SOURCE] produkty.images: {len(zdjecia)} plików")
        except Exception as e:
            print(f"   ⚠️  [ERROR] Parse images: {e}")
    
    # Sposób 2: FALLBACK na scraped.wszystkie_zdjecia (lokalne ścieżki przez ASIN)
    if not zdjecia and produkt.get('asin'):
        try:
            import json
            scraped = conn.execute('SELECT wszystkie_zdjecia FROM scraped WHERE asin = ?', (produkt['asin'],)).fetchone()
            if scraped and scraped['wszystkie_zdjecia']:
                try:
                    scraped_images = json.loads(scraped['wszystkie_zdjecia'])
                    if scraped_images and len(scraped_images) > 0:
                        zdjecia = scraped_images
                        print(f"   📸 [SOURCE] scraped.wszystkie_zdjecia: {len(zdjecia)} plików")
                except:
                    pass
        except Exception as e:
            print(f"   ⚠️  [ERROR] Read scraped: {e}")
    
    # Sposób 3: Fallback na zdjecie_url
    if not zdjecia and produkt['zdjecie_url']:
        img_url = produkt['zdjecie_url']
        if 'media-amazon.com' in img_url:
            img_url = re.sub(r'\._[A-Z0-9_,]+_\.', '._AC_SL1500_.', img_url)
        zdjecia = [img_url]
        print(f"   📸 [SOURCE] produkty.zdjecie_url: 1 URL")
    
    # Zamknij połączenie dopiero teraz
    
    print(f"   📸 [TOTAL] {len(zdjecia)} zdjęć do uploadu")
    
    # Upload zdjęć do Allegro (LOKALNE PLIKI lub URL)
    uploaded_urls = []
    print(f"   📤 Uploaduję {len(zdjecia[:8])} zdjęć do Allegro...")
    for idx, path_or_url in enumerate(zdjecia[:8], 1):
        try:
            # Sprawdź czy to lokalny plik czy URL
            if isinstance(path_or_url, str) and not path_or_url.startswith('http'):
                print(f"      [{idx}/{min(len(zdjecia), 8)}] Local file: {path_or_url}")
            else:
                print(f"      [{idx}/{min(len(zdjecia), 8)}] URL: {path_or_url[:60]}...")
            
            allegro_url = upload_image_to_allegro(path_or_url)
            if allegro_url:
                uploaded_urls.append(allegro_url)
                print(f"      ✓ [{idx}/{min(len(zdjecia), 8)}] Success!")
            else:
                print(f"      ✗ [{idx}/{min(len(zdjecia), 8)}] Failed")
        except Exception as e:
            print(f"      ✗ [{idx}/{min(len(zdjecia), 8)}] Error: {str(e)[:80]}")
    
    print(f"   ✅ Uploaded {len(uploaded_urls)}/{min(len(zdjecia), 8)} zdjęć")
    
    # Utwórz ofertę jako szkic
    try:
        offer_data = {
            'name': tytul,
            'category': {'id': kategoria} if kategoria else None,
            'sellingMode': {
                'price': {
                    'amount': str(cena),
                    'currency': 'PLN'
                }
            },
            'stock': {
                'available': ilosc
            },
            'description': {
                'sections': [
                    {
                        'items': [
                            {
                                'type': 'TEXT',
                                'content': opis
                            }
                        ]
                    }
                ]
            },
            'images': [{'url': url} for url in uploaded_urls] if uploaded_urls else [],
            'publication': {
                'status': 'INACTIVE'  # Szkic
            }
        }
        
        # Dodaj EAN jeśli jest
        if ean:
            offer_data['ean'] = [ean]
        
        # Wywołaj Allegro API
        result = create_offer(offer_data)
        
        if result and 'id' in result:
            offer_id = result['id']
            
            # Zaktualizuj status w bazie
            conn = get_db()
            conn.execute('''
                UPDATE produkty 
                SET status = 'szkic',
                    krotki_tytul = ?
                WHERE id = ?
            ''', (tytul, produkt_id))
            conn.commit()
            
            # Sukces!
            success_html = CSS + f'''
            <div class="container">
                <div class="header">
                    <h1>✅ Szkic utworzony!</h1>
                    <small>Produkt #{produkt_id}</small>
                </div>
                
                <div style="background:linear-gradient(135deg,rgba(34,197,94,0.15),rgba(16,185,129,0.1));border:2px solid rgba(34,197,94,0.5);border-radius:12px;padding:20px;margin-bottom:20px">
                    <div style="font-size:1.2rem;font-weight:600;color:#22c55e;margin-bottom:10px">🎉 Oferta na Allegro!</div>
                    <div style="color:#fff;margin-bottom:15px">
                        <strong>Tytuł:</strong> {tytul}<br>
                        <strong>Cena:</strong> {cena:.2f} PLN<br>
                        <strong>ID Allegro:</strong> {offer_id}
                    </div>
                    <a href="https://allegro.pl/moje-allegro/sprzedaz/drafted/{offer_id}" target="_blank" 
                       style="display:inline-block;padding:12px 24px;background:#22c55e;border-radius:8px;color:#fff;text-decoration:none;font-weight:600">
                        📝 Zobacz szkic na Allegro
                    </a>
                </div>
                
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px">
                    <a href="/produkty/{produkt_id}/extract-params" style="display:block;padding:12px;background:#3b82f6;border-radius:8px;color:#fff;text-decoration:none;text-align:center;font-weight:600">
                        🔄 Wygeneruj ponownie
                    </a>
                    <a href="javascript:history.back()" style="display:block;padding:12px;background:#64748b;border-radius:8px;color:#fff;text-decoration:none;text-align:center;font-weight:600">
                        ← Powrót
                    </a>
                </div>
            </div>
            '''
            return success_html
        else:
            raise Exception("Nie otrzymano ID oferty z Allegro")
            
    except Exception as e:
        # Błąd
        error_html = CSS + f'''
        <div class="container">
            <div class="header">
                <h1>❌ Błąd wystawiania</h1>
                <small>Produkt #{produkt_id}</small>
            </div>
            <div style="background:#1e1e2e;padding:20px;border-radius:12px;margin-bottom:20px">
                <p style="color:#ef4444;font-weight:600">Nie udało się wystawić szkicu:</p>
                <p style="margin-top:10px;color:#64748b">{str(e)}</p>
            </div>
            <a href="javascript:history.back()" class="back">&larr; Powrót</a>
        </div>
        '''
        return error_html

@app.route('/palety/<int:paleta_id>/edit', methods=['GET', 'POST'])
def paleta_edit(paleta_id):
    """Edycja palety - formularz"""
    from modules.database import get_db
    
    conn = get_db()
    
    if request.method == 'POST':
        # Pobierz dane z formularza
        nazwa = request.form.get('nazwa', '').strip()
        dostawca = request.form.get('dostawca', '').strip()
        regal = request.form.get('regal', '').strip()
        cena_zakupu = float(request.form.get('cena_zakupu', 0))
        # cena_zakupu = brutto
        cena_zakupu_netto = round(cena_zakupu / 1.23, 2) if cena_zakupu > 0 else 0
        data_zakupu = request.form.get('data_zakupu', '')
        notatki = request.form.get('notatki', '').strip()
        koszt_jedn = float(request.form.get('koszt_jednostkowy', 0) or 0)

        # Zaktualizuj paletę
        conn.execute('''
            UPDATE palety
            SET nazwa = ?, dostawca = ?, cena_zakupu = ?, cena_zakupu_netto = ?, data_zakupu = ?, notatki = ?, regal = ?, koszt_jednostkowy = ?
            WHERE id = ?
        ''', (nazwa, dostawca, cena_zakupu, cena_zakupu_netto, data_zakupu, notatki, regal, koszt_jedn, paleta_id))
        conn.commit()
        
        return redirect(f'/palety/{paleta_id}?success=updated')
    
    # GET - wyświetl formularz
    paleta = conn.execute('SELECT * FROM palety WHERE id = ?', (paleta_id,)).fetchone()
    
    if not paleta:
        return redirect('/palety')
    
    # Buduj formularz
    html = CSS
    html += '<div class="container">'
    html += '<div class="header"><h1>&#x270F; Edytuj Palete</h1><small>ID: ' + str(paleta_id) + '</small></div>'
    
    html += '<form method="POST" style="background:#1e1e2e;padding:20px;border-radius:12px;margin-bottom:20px">'
    
    # Nazwa
    html += '<div style="margin-bottom:15px">'
    html += '<label style="display:block;color:#64748b;font-size:0.85rem;margin-bottom:5px">Nazwa palety</label>'
    html += '<input type="text" name="nazwa" value="' + (paleta['nazwa'] or '') + '" '
    html += 'style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">'
    html += '</div>'
    
    # Dostawca
    html += '<div style="margin-bottom:15px">'
    html += '<label style="display:block;color:#64748b;font-size:0.85rem;margin-bottom:5px">Dostawca</label>'
    html += '<select name="dostawca" style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">'
    
    dostawcy = ['', 'Warrington', 'Miglo', 'Jobalots', 'Inny']
    for d in dostawcy:
        selected = ' selected' if d == paleta['dostawca'] else ''
        html += f'<option value="{d}"{selected}>{d or "— Wybierz —"}</option>'
    
    html += '</select>'
    html += '</div>'
    
    # Regal / Lokalizacja
    html += '<div style="margin-bottom:15px">'
    html += '<label style="display:block;color:#64748b;font-size:0.85rem;margin-bottom:5px">📍 Regal / Lokalizacja</label>'
    try:
        regal_value = paleta['regal'] or ''
    except (KeyError, TypeError):
        regal_value = ''
    html += '<input type="text" name="regal" value="' + regal_value + '" placeholder="np. Migło, Regał A1" '
    html += 'style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">'
    html += '</div>'
    
    # Cena zakupu
    html += '<div style="margin-bottom:15px">'
    html += '<label style="display:block;color:#64748b;font-size:0.85rem;margin-bottom:5px">Cena zakupu (PLN brutto)</label>'
    html += '<input type="number" name="cena_zakupu" value="' + str(paleta['cena_zakupu'] or 0) + '" step="0.01" '
    html += 'style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">'
    html += '</div>'
    
    # Koszt jednostkowy (netto/szt) - STAŁY
    _kj_val = 0
    try:
        _kj_val = float(paleta['koszt_jednostkowy'] or 0)
    except:
        pass
    html += '<div style="margin-bottom:15px">'
    html += '<label style="display:block;color:#64748b;font-size:0.85rem;margin-bottom:5px">Koszt jednostkowy (netto/szt) - staly</label>'
    html += '<input type="number" name="koszt_jednostkowy" value="' + str(_kj_val) + '" step="0.01" min="0" '
    html += 'style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #f59e0b;border-radius:8px;color:#fff;font-size:1rem" '
    html += 'placeholder="Zostaw 0 dla auto-obliczenia z ceny palety">'
    html += '<div style="font-size:0.7rem;color:#64748b;margin-top:4px">Cena netto za 1 sztuke. Zostaw 0 = auto z ceny palety / ilosc sztuk</div>'
    html += '</div>'

    # Data zakupu
    html += '<div style="margin-bottom:15px">'
    html += '<label style="display:block;color:#64748b;font-size:0.85rem;margin-bottom:5px">Data zakupu</label>'
    html += '<input type="date" name="data_zakupu" value="' + (paleta['data_zakupu'] or '') + '" '
    html += 'style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">'
    html += '</div>'
    
    # Notatki
    html += '<div style="margin-bottom:15px">'
    html += '<label style="display:block;color:#64748b;font-size:0.85rem;margin-bottom:5px">Notatki</label>'
    html += '<textarea name="notatki" rows="4" '
    html += 'style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">'
    html += (paleta['notatki'] or '')
    html += '</textarea>'
    html += '</div>'
    
    # Przyciski
    html += '<div style="display:flex;gap:10px;margin-top:20px">'
    html += '<button type="submit" style="flex:1;padding:12px;background:#22c55e;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">Zapisz zmiany</button>'
    html += '<a href="/palety/' + str(paleta_id) + '" style="flex:1;padding:12px;background:#ef4444;border:none;border-radius:8px;color:#fff;font-weight:600;text-align:center;text-decoration:none;display:block">Anuluj</a>'
    html += '</div>'
    
    html += '</form>'
    
    html += '<a href="/palety/' + str(paleta_id) + '" class="back">&larr; Powrot do palety</a>'
    html += '</div>'
    
    return html

# POWIADOMIENIA

@app.route('/powiadomienia')
def powiadomienia():
    # W przyszłości - z bazy danych
    notyfikacje = [
        {'type': 'sale', 'msg': 'Sprzedano: Pokrowce Coverado', 'time': '2 min temu'},
        {'type': 'alert', 'msg': 'Niski stan: Dash Cam (2 szt)', 'time': '15 min temu'},
        {'type': 'sale', 'msg': 'Sprzedano: Gogle noktowizyjne', 'time': '1h temu'},
    ]
    return render_template('powiadomienia.html',
        version=VERSION,
        notyfikacje=notyfikacje,
        active_home='active', active_magazyn='', active_paletomat='',
        active_allegro='', active_monitor='', active_narzedzia='')

# ============================================================
# API ENDPOINTS
# ============================================================
@app.route('/api/stats')
def api_stats():
    """Zwraca statystyki jako JSON"""
    return jsonify({
        'magazyn': mag_stats(),
        'paletomat': pal_stats(),
        'telegram': bot_status()
    })

@app.route('/api/widget')
def api_widget():
    """Endpoint dla widgetu Android - zwraca kluczowe statystyki"""
    from modules.database import get_db
    
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Sprzedaż dziś
    sprzedaz_dzis = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc), 0) as suma
        FROM sprzedaze WHERE DATE(data_sprzedazy) = ?
    ''', (today,)).fetchone()
    
    # Do wysłania
    do_wyslania = conn.execute('''
        SELECT COUNT(*) as cnt FROM produkty WHERE status = 'sprzedany'
    ''').fetchone()['cnt']
    
    # Magazyn
    magazyn = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(ilosc), 0) as sztuk
        FROM produkty WHERE status IN ('magazyn', 'nowy', 'gotowy')
    ''').fetchone()
    
    # Sprzedaż ten miesiąc
    miesiac = datetime.now().strftime('%Y-%m')
    sprzedaz_miesiac = conn.execute('''
        SELECT COALESCE(SUM(cena * ilosc), 0) as suma
        FROM sprzedaze WHERE strftime('%Y-%m', data_sprzedazy) = ?
    ''', (miesiac,)).fetchone()['suma']
    
    
    return jsonify({
        'dzis': {
            'sprzedane': sprzedaz_dzis['cnt'],
            'przychod': round(sprzedaz_dzis['suma'], 2)
        },
        'do_wyslania': do_wyslania,
        'magazyn': {
            'produktow': magazyn['cnt'],
            'sztuk': magazyn['sztuk']
        },
        'miesiac': {
            'przychod': round(sprzedaz_miesiac, 2)
        },
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/stats/monthly')
def api_stats_monthly():
    """Zwraca dane miesięczne do wykresów"""
    from modules.database import get_db
    
    current_year = datetime.now().year
    conn = get_db()
    
    # Pobierz sprzedaż miesięcznie
    miesieczne = conn.execute('''
        SELECT strftime('%m', data_sprzedazy) as miesiac, SUM(cena * ilosc) as suma
        FROM sprzedaze
        WHERE strftime('%Y', data_sprzedazy) = ?
          AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
          AND (kupujacy IS NULL OR kupujacy != 'offline')
        GROUP BY miesiac
        ORDER BY miesiac
    ''', (str(current_year),)).fetchall()
    
    nazwy_miesiecy = ['Sty', 'Lut', 'Mar', 'Kwi', 'Maj', 'Cze', 'Lip', 'Sie', 'Wrz', 'Paź', 'Lis', 'Gru']
    dane = [0] * 12
    for m in miesieczne:
        idx = int(m['miesiac']) - 1
        dane[idx] = float(m['suma'])
    
    return jsonify({
        'labels': nazwy_miesiecy,
        'values': dane
    })

@app.route('/api/check-sales')
def api_check_sales():
    """Sprawdza nowe zamówienia - sync tylko co 60 sekund, nie przy każdym wywołaniu"""
    import time
    try:
        from modules.allegro_api import sync_orders, is_authenticated
        from modules.database import get_db
        
        if not is_authenticated():
            return jsonify({'success': False, 'error': 'Token wygasł', 'new_sales': []})
        
        conn = get_db()
        before = conn.execute('SELECT MAX(id) as last_id FROM sprzedaze').fetchone()
        last_id_before = before['last_id'] or 0
        
        # Sync tylko co 60 sekund - nie przy każdym sprawdzeniu
        now = time.time()
        last_sync = getattr(api_check_sales, '_last_sync', 0)
        synced = 0
        if now - last_sync > 60:
            api_check_sales._last_sync = now
            synced, _ = sync_orders(today_only=True)
        
        new_sales = conn.execute('''
            SELECT s.id, s.cena, s.ilosc, s.kupujacy,
                   COALESCE(NULLIF(s.nazwa,''), p.nazwa, 'Produkt') as produkt_nazwa
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            WHERE s.id > ?
            ORDER BY s.id DESC
            LIMIT 10
        ''', (last_id_before,)).fetchall()

        sales_list = [{'id': s['id'], 'nazwa': s['produkt_nazwa'],
                       'cena': s['cena'], 'ilosc': s['ilosc'], 'kupujacy': s['kupujacy']}
                      for s in new_sales]
        
        return jsonify({'success': True, 'synced': synced, 'new_sales': sales_list})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'new_sales': []})

@app.route('/api/notify', methods=['POST'])
def api_notify():
    """Wysyła powiadomienie przez Telegram"""
    data = request.json
    msg = data.get('message', '')
    if msg:
        success = send_telegram(msg)
        return jsonify({'success': success})
    return jsonify({'success': False, 'error': 'No message'}), 400


@app.route('/offline')
def offline():
    return render_template('offline.html')

# ============================================================
# IKONY PWA (generowane dynamicznie)
# ============================================================
@app.route('/static/icon-<int:size>.png')
def pwa_icon(size):
    """Generuje ikonę PWA jako SVG (przeglądarki obsługują)"""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">
    <rect width="{size}" height="{size}" rx="{int(size*0.2)}" fill="#0a0a0f"/>
    <rect x="{int(size*0.08)}" y="{int(size*0.08)}" width="{int(size*0.84)}" height="{int(size*0.84)}" rx="{int(size*0.15)}" fill="#12121a"/>
    <text x="50%" y="50%" font-size="{int(size*0.45)}" text-anchor="middle" dominant-baseline="middle">📦</text>
    <text x="50%" y="80%" font-family="system-ui,sans-serif" font-size="{int(size*0.11)}" font-weight="bold" fill="#3b82f6" text-anchor="middle">AKCES</text>
    </svg>'''
    return Response(svg, mimetype='image/svg+xml')

# ============================================================
# USTAWIENIA SYSTEMU
# ============================================================
@app.route('/ustawienia')
def ustawienia():
    from modules.database import get_config, is_module_enabled
    from modules.email_reports import get_email_config

    base_url = get_config('app_base_url', 'http://localhost:5000')
    email_cfg = get_email_config()

    # Module toggles
    modules_cfg = {
        'paletomat': {'name': 'Paletomat', 'desc': 'Skaner palet, scraping Amazon', 'enabled': is_module_enabled('paletomat')},
        'magazynier': {'name': 'Magazynier', 'desc': 'Zarzadzanie magazynem', 'enabled': is_module_enabled('magazynier')},
        'allegro': {'name': 'Allegro', 'desc': 'Integracja z Allegro', 'enabled': is_module_enabled('allegro')},
        'olx': {'name': 'OLX', 'desc': 'Integracja z OLX', 'enabled': is_module_enabled('olx')},
        'vinted': {'name': 'Vinted', 'desc': 'Integracja z Vinted', 'enabled': is_module_enabled('vinted')},
        'telegram': {'name': 'Telegram', 'desc': 'Bot Telegram', 'enabled': is_module_enabled('telegram')},
    }
    brand_name = get_config('brand_name', 'AKCES HUB')
    brand_color = get_config('brand_color', '#6366f1')
    
    # Sprawdź czy to ngrok URL
    is_ngrok = 'ngrok' in base_url
    
    html = CSS + '''
    <div class="container">
        <div class="header">
            <h1>⚙️ USTAWIENIA SYSTEMU</h1>
            <small>Konfiguracja ''' + brand_name + '''</small>
        </div>
        
        <form action="/ustawienia/save" method="POST">
            <div class="card" style="padding:15px">
                <div style="font-weight:600;margin-bottom:15px">🌐 Adres URL aplikacji (dla QR kodów)</div>
                
                <div style="padding:12px;background:rgba(234,179,8,0.1);border:1px solid rgba(234,179,8,0.3);border-radius:8px;margin-bottom:15px">
                    <div style="font-size:0.85rem;color:#eab308">
                        <b>⚠️ WAŻNE:</b> Żeby QR kody działały z telefonu, wpisz swój adres ngrok!
                    </div>
                </div>
                
                <input type="text" name="app_base_url" id="baseUrlInput" value="''' + base_url + '''" 
                    placeholder="https://xxx.ngrok-free.dev"
                    class="form-ctrl" style="padding:12px;font-size:1rem;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;width:100%">
                
                <div style="margin-top:10px;font-size:0.8rem;color:#64748b">
                    ''' + ('✅ Ngrok wykryty - QR kody będą działać z telefonu!' if is_ngrok else '⚠️ localhost - QR kody nie będą działać z telefonu') + '''
                </div>
            </div>
            
            <button type="submit" style="width:100%;padding:14px;background:#3b82f6;border:none;border-radius:10px;color:#fff;font-weight:600;font-size:1rem;cursor:pointer;margin-top:10px">💾 ZAPISZ</button>
        </form>
        
        <!-- RAPORTY EMAIL -->
        <div style="margin-top:20px;padding:15px;background:linear-gradient(135deg,rgba(59,130,246,0.1),rgba(37,99,235,0.1));border:1px solid rgba(59,130,246,0.3);border-radius:12px">
            <div style="font-weight:600;margin-bottom:15px;color:#3b82f6;display:flex;align-items:center;gap:10px">
                📧 Raporty Email
                <span style="font-size:0.75rem;padding:3px 8px;background:''' + ('#22c55e' if email_cfg.get('enabled') else '#64748b') + ''';border-radius:10px;color:#fff">
                    ''' + ('WŁĄCZONE' if email_cfg.get('enabled') else 'WYŁĄCZONE') + '''
                </span>
            </div>
            
            <form action="/ustawienia/email" method="POST">
                <div style="margin-bottom:12px">
                    <label style="font-size:0.8rem;color:#64748b">Email (Gmail)</label>
                    <input type="email" name="email" value="''' + (email_cfg.get('email') or '') + '''" 
                        placeholder="twoj@gmail.com"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:5px">
                </div>
                
                <div style="margin-bottom:12px">
                    <label style="font-size:0.8rem;color:#64748b">Hasło aplikacji (nie zwykłe hasło!)</label>
                    <input type="password" name="password" placeholder="''' + ('••••••••••••••••' if email_cfg.get('password') else 'xxxx xxxx xxxx xxxx') + '''"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:5px">
                </div>
                
                <div style="margin-bottom:15px">
                    <label style="font-size:0.8rem;color:#64748b">Odbiorca (opcjonalnie, domyślnie = nadawca)</label>
                    <input type="email" name="recipient" value="''' + (email_cfg.get('recipient') or '') + '''" 
                        placeholder="Zostaw puste jeśli ten sam email"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:5px">
                </div>
                
                <div style="margin-bottom:15px">
                    <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
                        <input type="checkbox" name="enabled" ''' + ('checked' if email_cfg.get('enabled') else '') + ''' style="width:18px;height:18px">
                        <span style="color:#fff">Włącz raporty email</span>
                    </label>
                </div>
                
                <button type="submit" style="width:100%;padding:12px;background:#3b82f6;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">
                    💾 Zapisz konfigurację email
                </button>
            </form>
            
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:15px">
                <a href="/raport/podglad" target="_blank" style="display:block;text-align:center;padding:10px;background:#1e1e2e;border:1px solid #3b82f6;border-radius:8px;color:#3b82f6;text-decoration:none;font-weight:600;font-size:0.85rem">
                    👁️ Podgląd raportu
                </a>
                <a href="/raport/wyslij" onclick="return confirm('Wysłać raport tygodniowy na email?')" style="display:block;text-align:center;padding:10px;background:#22c55e;border-radius:8px;color:#fff;text-decoration:none;font-weight:600;font-size:0.85rem">
                    📤 Wyślij teraz
                </a>
            </div>
            
            <div style="margin-top:12px;padding:10px;background:#1e1e2e;border-radius:8px;font-size:0.8rem;color:#64748b">
                <b>💡 Jak uzyskać hasło aplikacji Gmail?</b><br>
                1. Wejdź na <a href="https://myaccount.google.com/apppasswords" target="_blank" style="color:#3b82f6">myaccount.google.com/apppasswords</a><br>
                2. Wybierz "Poczta" i "Windows"<br>
                3. Skopiuj 16-znakowe hasło (bez spacji)
            </div>
        </div>
        
        <!-- Ngrok Token (auto-connect na Pi) -->
        <div style="margin-top:20px;padding:15px;background:linear-gradient(135deg,rgba(139,92,246,0.1),rgba(88,28,135,0.1));border:1px solid rgba(139,92,246,0.3);border-radius:12px">
            <div style="font-weight:600;margin-bottom:10px;color:#8b5cf6">🚀 Ngrok - Zdalny dostep</div>
            <form action="/ustawienia/ngrok-token" method="POST">
                <div style="margin-bottom:12px">
                    <label style="font-size:0.8rem;color:#64748b">Auth Token (z <a href="https://dashboard.ngrok.com/get-started/your-authtoken" target="_blank" style="color:#8b5cf6">dashboard.ngrok.com</a>)</label>
                    <input type="password" name="ngrok_token" value="''' + get_config('ngrok_auth_token', '') + '''"
                        placeholder="2abc...xyz123"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:5px;font-family:monospace">
                </div>
                <div style="margin-bottom:12px">
                    <label style="font-size:0.8rem;color:#64748b">Stala domena (opcjonalnie, np. akceshub.ngrok.dev)</label>
                    <input type="text" name="ngrok_domain" value="''' + get_config('ngrok_domain', '') + '''"
                        placeholder="twoja-domena.ngrok-free.dev"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:5px;font-family:monospace">
                </div>
                <button type="submit" style="width:100%;padding:12px;background:#8b5cf6;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">
                    💾 Zapisz i polacz
                </button>
            </form>
            <div style="margin-top:10px;font-size:0.8rem;color:#64748b">
                Na Raspberry Pi ngrok startuje automatycznie. Token zapisuje sie w bazie danych.
            </div>
        </div>
        
        <!-- KIOSK MODE -->
        <div style="margin-top:20px;padding:15px;background:linear-gradient(135deg,rgba(99,102,241,0.1),rgba(59,130,246,0.1));border:1px solid rgba(99,102,241,0.3);border-radius:12px">
            <div style="font-weight:600;margin-bottom:8px;color:#818cf8">📺 Tryb Kiosk (Raspberry Pi)</div>
            <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:12px">
                Optymalizacja UI dla ekranu dotykowego 7"
            </div>
            <div style="display:flex;gap:10px">
                <a href="/?kiosk=1" style="flex:1;text-align:center;padding:14px;background:#6366f1;border-radius:10px;color:#fff;text-decoration:none;font-weight:600;font-size:0.95rem">
                    ✅ Włącz Kiosk
                </a>
                <a href="/?kiosk=0" style="flex:1;text-align:center;padding:14px;background:var(--bg-tertiary,#1e1e2e);border:1px solid var(--border-color,#2a2a3a);border-radius:10px;color:#fff;text-decoration:none;font-weight:600;font-size:0.95rem">
                    ❌ Wyłącz Kiosk
                </a>
            </div>
        </div>

        <!-- MODUŁY -->
        <div style="margin-top:20px;padding:15px;background:linear-gradient(135deg,rgba(34,197,94,0.1),rgba(22,163,74,0.1));border:1px solid rgba(34,197,94,0.3);border-radius:12px">
            <div style="font-weight:600;margin-bottom:15px;color:#22c55e">🧩 Moduły systemu</div>
            <form action="/ustawienia/modules" method="POST">
                <div style="display:grid;gap:10px">
                    ''' + ''.join([f'''
                    <label style="display:flex;align-items:center;gap:12px;padding:10px;background:#1e1e2e;border-radius:8px;cursor:pointer">
                        <input type="checkbox" name="module_{key}" {'checked' if mod['enabled'] else ''} style="width:18px;height:18px;accent-color:#22c55e">
                        <div>
                            <div style="font-weight:600;font-size:0.9rem">{mod['name']}</div>
                            <div style="font-size:0.75rem;color:#64748b">{mod['desc']}</div>
                        </div>
                    </label>''' for key, mod in modules_cfg.items()]) + '''
                </div>
                <button type="submit" style="width:100%;padding:12px;background:#22c55e;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer;margin-top:12px">
                    💾 Zapisz moduły
                </button>
            </form>
        </div>

        <!-- BRANDING -->
        <div style="margin-top:20px;padding:15px;background:linear-gradient(135deg,rgba(236,72,153,0.1),rgba(168,85,247,0.1));border:1px solid rgba(236,72,153,0.3);border-radius:12px">
            <div style="font-weight:600;margin-bottom:15px;color:#ec4899">🎨 Branding</div>
            <form action="/ustawienia/branding" method="POST" enctype="multipart/form-data">
                <div style="margin-bottom:12px">
                    <label style="font-size:0.8rem;color:#64748b">Nazwa systemu</label>
                    <input type="text" name="brand_name" value="''' + brand_name + '''"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:5px">
                </div>
                <div style="margin-bottom:12px">
                    <label style="font-size:0.8rem;color:#64748b">Logo firmy (PNG/JPG, max 500KB)</label>
                    <div style="display:flex;gap:10px;align-items:center;margin-top:5px">
                        ''' + (f'<img src="/static/brand_logo.png?v={int(__import__("time").time())}" style="height:40px;border-radius:6px">' if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'brand_logo.png')) else '<span style="color:#64748b;font-size:0.85rem">Brak logo</span>') + '''
                        <input type="file" name="brand_logo" accept="image/png,image/jpeg"
                            style="flex:1;padding:8px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#e2e8f0;font-size:0.85rem">
                    </div>
                </div>
                <div style="margin-bottom:12px">
                    <label style="font-size:0.8rem;color:#64748b">Kolor przewodni</label>
                    <div style="display:flex;gap:10px;align-items:center;margin-top:5px">
                        <input type="color" name="brand_color" value="''' + brand_color + '''" style="width:50px;height:38px;border:none;background:none;cursor:pointer">
                        <input type="text" value="''' + brand_color + '''"
                            style="flex:1;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-family:monospace"
                            onchange="this.previousElementSibling.value=this.value" readonly>
                    </div>
                </div>
                <button type="submit" style="width:100%;padding:12px;background:linear-gradient(135deg,#ec4899,#a855f7);border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">
                    💾 Zapisz branding
                </button>
            </form>
        </div>

        <!-- UŻYTKOWNICY -->
        <div style="margin-top:20px">
            <a href="/auth/users" style="display:block;text-align:center;padding:14px;background:linear-gradient(135deg,rgba(99,102,241,0.2),rgba(139,92,246,0.2));border:1px solid rgba(99,102,241,0.3);border-radius:12px;color:#818cf8;text-decoration:none;font-weight:600;font-size:1rem">
                👥 Zarządzanie użytkownikami
            </a>
        </div>

        <!-- AKTUALIZACJA SYSTEMU -->
        <div style="margin-top:20px;padding:15px;background:rgba(34,197,94,0.05);border:1px solid rgba(34,197,94,0.2);border-radius:12px">
            <div style="font-weight:600;margin-bottom:10px;color:#22c55e">🔄 Aktualizacja systemu</div>

            <!-- Git pull (glowna metoda) -->
            <form action="/admin/update-git" method="POST" onsubmit="return confirm('Pobrac najnowsza wersje z GitHub?')" style="margin-bottom:12px">
                <button type="submit" style="width:100%;padding:14px;background:linear-gradient(135deg,rgba(34,197,94,0.2),rgba(22,163,74,0.2));border:1px solid rgba(34,197,94,0.3);border-radius:12px;color:#22c55e;font-weight:600;font-size:1rem;cursor:pointer">
                    🔄 Aktualizuj z GitHub (git pull)
                </button>
            </form>

            <!-- ZIP upload (fallback) -->
            <details style="margin-top:8px">
                <summary style="color:#94a3b8;font-size:0.85rem;cursor:pointer">📦 Alternatywnie: wgraj ZIP reczne</summary>
                <form action="/admin/update" method="POST" enctype="multipart/form-data" onsubmit="return confirm('Aktualizowac system? Backup zostanie wykonany automatycznie.')" style="margin-top:10px">
                    <input type="file" name="update_zip" accept=".zip" required
                        style="width:100%;padding:10px;background:rgba(30,30,50,0.5);border:1px solid rgba(100,100,140,0.3);border-radius:8px;color:#e2e8f0;margin-bottom:10px;font-size:0.9rem">
                    <button type="submit" style="width:100%;padding:12px;background:rgba(30,30,50,0.5);border:1px solid rgba(100,100,140,0.3);border-radius:12px;color:#94a3b8;font-weight:600;font-size:0.9rem;cursor:pointer">
                        📦 Wgraj ZIP
                    </button>
                </form>
            </details>
        </div>

        <!-- DANGER ZONE -->
        <div style="margin-top:20px;padding:15px;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:12px">
            <div style="font-weight:600;margin-bottom:10px;color:#ef4444">⚠️ Strefa niebezpieczna</div>
            <div style="font-size:0.85rem;color:#94a3b8;margin-bottom:15px">
                Wyczyść testowe dane. Ta operacja jest nieodwracalna!
            </div>
            
            <div style="display:grid;gap:10px">
                <form method="POST" action="/ustawienia/reset-sprzedaze" onsubmit="return confirm('Na pewno wyczyścić historię sprzedaży?')">
                    <button type="submit" style="width:100%;padding:12px;background:#ef4444;border-radius:8px;color:#fff;border:none;font-weight:600;cursor:pointer;font-size:0.9rem">
                        🗑️ Wyczyść historię sprzedaży
                    </button>
                </form>

                <form method="POST" action="/ustawienia/reset-magazyn" onsubmit="return confirm('⚠️ UWAGA!\\n\\nTo usunie WSZYSTKIE produkty z magazynu!\\n\\nNa pewno kontynuować?')">
                    <button type="submit" style="width:100%;padding:12px;background:#dc2626;border-radius:8px;color:#fff;border:none;font-weight:600;cursor:pointer;font-size:0.9rem">
                        🗑️ Wyczyść magazyn (produkty)
                    </button>
                </form>

                <form method="POST" action="/ustawienia/reset-palety" onsubmit="return confirm('⚠️ UWAGA!\\n\\nTo usunie WSZYSTKIE palety i powiązane produkty!\\n\\nNa pewno kontynuować?')">
                    <button type="submit" style="width:100%;padding:12px;background:#b91c1c;border-radius:8px;color:#fff;border:none;font-weight:600;cursor:pointer;font-size:0.9rem">
                        🗑️ Wyczyść palety
                    </button>
                </form>

                <form method="POST" action="/ustawienia/reset-scraped" onsubmit="return confirm('Wyczyścić zescrapowane produkty z Palatomatu?')">
                    <button type="submit" style="width:100%;padding:12px;background:#991b1b;border-radius:8px;color:#fff;border:none;font-weight:600;cursor:pointer;font-size:0.9rem">
                        🗑️ Wyczyść scraped (Paletomat)
                    </button>
                </form>
            </div>
        </div>
        
        <a href="/" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:20px">← Powrót</a>
    </div>
    '''
    return html


# ============================================================
# SYNCHRONIZACJA ZAMÓWIEŃ Z ALLEGRO
# ============================================================
@app.route('/sync-historyczny', methods=['GET', 'POST'])
def sync_historyczny():
    from modules.allegro_api import sync_orders, is_authenticated
    from datetime import date, timedelta
    
    if not is_authenticated():
        return '<html><body style="background:#0a0a0f;color:#fff;font-family:system-ui;padding:40px">Zaloguj sie ponownie do Allegro.</body></html>'
    
    if request.method == 'POST':
        from_date = request.form.get('from_date', '')
        if not from_date:
            return redirect('/sync-historyczny')
        synced, error = sync_orders(today_only=False, from_date_str=from_date)
        msg = f'Zsynchronizowano {synced} zamowien od {from_date}' if not error else f'Blad: {error}'
        kolor = '#22c55e' if not error else '#ef4444'
        return f'<html><head><meta http-equiv="refresh" content="4;url=/magazyn/statystyki"></head><body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0"><div style="text-align:center"><div style="font-size:1.5rem;color:{kolor};padding:40px">{msg}</div><div style="color:#64748b">Przekierowanie...</div></div></body></html>'
    
    miesiac_temu = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1).strftime('%Y-%m-%d')
    return f'<html><head><title>Sync historyczny</title></head><body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0"><div style="background:#12121a;border-radius:16px;padding:30px;min-width:320px"><h2 style="margin:0 0 15px">🔄 Sync historyczny</h2><p style="color:#64748b;margin-bottom:20px">Pobierz zamowienia od wybranej daty (np. poprzedni miesiac)</p><form method="POST"><label style="display:block;color:#94a3b8;margin-bottom:6px">Data od:</label><input type="date" name="from_date" value="{miesiac_temu}" style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #334155;border-radius:8px;color:#fff;font-size:1rem;box-sizing:border-box;margin-bottom:15px"><button type="submit" style="width:100%;padding:12px;background:#3b82f6;border:none;border-radius:8px;color:#fff;font-size:1rem;font-weight:600;cursor:pointer">🔄 Synchronizuj</button></form><a href="/magazyn/statystyki" style="display:block;text-align:center;margin-top:15px;color:#64748b;font-size:0.85rem">Anuluj</a></div></body></html>'


@app.route('/sync-miesiac')
def sync_miesiac():
    """Synchronizuje zamówienia z całego miesiąca z Allegro"""
    try:
        from modules.allegro_api import sync_orders, is_authenticated
        
        if not is_authenticated():
            return '''
            <html><head><meta http-equiv="refresh" content="3;url=/statystyki"></head>
            <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
                <div style="text-align:center">
                    <div style="font-size:3rem;margin-bottom:20px">⚠️</div>
                    <div style="font-size:1.2rem;color:#f59e0b">Token Allegro wygasł!</div>
                    <div style="color:#64748b;margin-top:10px">Zaloguj się ponownie w Allegro</div>
                </div>
            </body></html>
            '''
        
        synced, error = sync_orders(today_only=False)  # Cały miesiąc!
        
        if error:
            return f'''
            <html><head><meta http-equiv="refresh" content="3;url=/statystyki"></head>
            <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
                <div style="text-align:center">
                    <div style="font-size:3rem;margin-bottom:20px">❌</div>
                    <div style="font-size:1.2rem;color:#ef4444">Błąd: {error}</div>
                </div>
            </body></html>
            '''
        
        return f'''
        <html><head><meta http-equiv="refresh" content="2;url=/statystyki"></head>
        <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
            <div style="text-align:center">
                <div style="font-size:3rem;margin-bottom:20px">✅</div>
                <div style="font-size:1.2rem">Zsynchronizowano <b>{synced}</b> nowych zamówień!</div>
                <div style="color:#64748b;margin-top:10px">Przekierowuję do statystyk...</div>
            </div>
        </body></html>
        '''

    except Exception as e:
        return f'''
        <html><head><meta http-equiv="refresh" content="3;url=/statystyki"></head>
        <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
            <div style="text-align:center">
                <div style="font-size:3rem;margin-bottom:20px">❌</div>
                <div style="font-size:1.2rem;color:#ef4444">Błąd: {str(e)}</div>
            </div>
        </body></html>
        '''


@app.route('/sync-custom')
def sync_custom():
    """Synchronizuje zamówienia od podanej daty (np. /sync-custom?from=2026-02-01)"""
    from_date = request.args.get('from', '')
    if not from_date:
        return '''
        <html><head><meta http-equiv="refresh" content="3;url=/statystyki"></head>
        <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
            <div style="text-align:center">
                <div style="font-size:3rem;margin-bottom:20px">⚠️</div>
                <div style="font-size:1.2rem;color:#f59e0b">Podaj datę: /sync-custom?from=2026-02-01</div>
            </div>
        </body></html>
        '''
    try:
        from modules.allegro_api import sync_orders, is_authenticated
        if not is_authenticated():
            return '<html><head><meta http-equiv="refresh" content="3;url=/statystyki"></head><body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0"><div style="text-align:center"><div style="font-size:3rem;margin-bottom:20px">⚠️</div><div style="color:#f59e0b">Token Allegro wygasł!</div></div></body></html>'
        synced, error = sync_orders(from_date_str=from_date)
        if error:
            return f'<html><head><meta http-equiv="refresh" content="3;url=/sprzedaze"></head><body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0"><div style="text-align:center"><div style="font-size:3rem;margin-bottom:20px">❌</div><div style="color:#ef4444">Błąd: {error}</div></div></body></html>'
        return f'''
        <html><head><meta http-equiv="refresh" content="2;url=/sprzedaze?miesiac={from_date[:7]}"></head>
        <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
            <div style="text-align:center">
                <div style="font-size:3rem;margin-bottom:20px">✅</div>
                <div style="font-size:1.2rem">Zsynchronizowano <b>{synced}</b> zamówień od {from_date}!</div>
                <div style="color:#64748b;margin-top:10px">Przekierowuję...</div>
            </div>
        </body></html>
        '''
    except Exception as e:
        return f'<html><head><meta http-equiv="refresh" content="3;url=/statystyki"></head><body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0"><div style="text-align:center"><div style="font-size:3rem;margin-bottom:20px">❌</div><div style="color:#ef4444">Błąd: {str(e)}</div></div></body></html>'


# ============================================================
# WYSYŁKI (Widok dla dziadka)
# ============================================================


@app.route('/wysylki/wyczysc-all')
def wysylki_wyczysc_all():
    """Oznacza WSZYSTKIE zamówienia ze statusem 'nowa'/'nowe' jako wysłane"""
    from modules.database import get_db
    conn = get_db()
    cnt = conn.execute('''
        UPDATE sprzedaze SET status = 'wyslana'
        WHERE status IN ('nowa', 'nowe')
    ''').rowcount
    conn.commit()
    print(f"🗑️ Wyczyszczono {cnt} zamówień → wyslana")
    return redirect('/wysylki/allegro')


@app.route('/wysylki/debug-sync')
def wysylki_debug_sync():
    """Diagnostyka: pokaż co jest w bazie + sync z logami"""
    from modules.database import get_db
    conn = get_db()

    # Pokaż statusy w DB
    stats = conn.execute('''
        SELECT status, COUNT(*) as cnt FROM sprzedaze
        GROUP BY status ORDER BY cnt DESC
    ''').fetchall()

    # Pokaż zamówienia 'nowa'
    nowe = conn.execute('''
        SELECT id, allegro_order_id, nazwa, cena, kupujacy, data_sprzedazy, status
        FROM sprzedaze WHERE status IN ('nowa', 'nowe')
        ORDER BY data_sprzedazy DESC
    ''').fetchall()

    result = {
        'statusy': {r['status']: r['cnt'] for r in stats},
        'nowe_zamowienia': [{
            'id': r['id'],
            'order_id': r['allegro_order_id'] or '',
            'nazwa': (r['nazwa'] or '')[:50],
            'cena': r['cena'],
            'kupujacy': r['kupujacy'],
            'data': r['data_sprzedazy'],
            'status': r['status']
        } for r in nowe]
    }

    from flask import jsonify
    return jsonify(result)


@app.route('/wysylki/wyslano/<int:id>')
def wysylki_wyslano(id):
    """Oznacza zamówienie jako wysłane"""
    from modules.database import get_db, add_historia
    conn = get_db()
    
    # Pobierz dane sprzedaży
    sprzedaz = conn.execute('SELECT * FROM sprzedaze WHERE id = ?', (id,)).fetchone()
    
    # Oznacz jako wysłane
    conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('wyslana', id))
    conn.commit()
    
    # Dodaj historię do produktu jeśli jest powiązany
    if sprzedaz and sprzedaz['produkt_id']:
        add_historia(sprzedaz['produkt_id'], 'wyslano', f'Wysłano do klienta: {sprzedaz["kupujacy"] or "—"}', 
            {'kupujacy': sprzedaz['kupujacy'], 'cena': sprzedaz['cena']})
    
    return redirect('/wysylki')


@app.route('/wysylki/cofnij/<int:id>')
def wysylki_cofnij(id):
    """Cofa status wysłania"""
    from modules.database import get_db
    conn = get_db()
    conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('nowa', id))
    conn.commit()
    return redirect('/wysylki')


# ============================================================
# SPRZEDAŻE I ZWROTY
# ============================================================
@app.route('/sprzedaze')
def sprzedaze_lista():
    """Lista sprzedaży z możliwością oznaczenia zwrotów"""
    from modules.database import get_db
    
    # Filtr miesiąca z query string
    miesiac_filter = request.args.get('miesiac', '')
    
    # Komunikat z sync zwrotów
    msg = request.args.get('msg', '')
    msg_cnt = request.args.get('cnt', '0')
    msg_detail = request.args.get('detail', '')
    
    # Domyślnie bieżący miesiąc
    if not miesiac_filter:
        miesiac_filter = datetime.now().strftime('%Y-%m')
    
    conn = get_db()
    
    # Pobierz sprzedaże z wybranego miesiąca
    sprzedaze = conn.execute('''
        SELECT s.*,
               COALESCE(p.nazwa, s.nazwa, 'Brak nazwy') as produkt_nazwa
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        LEFT JOIN oferty o ON s.oferta_id = o.id
        WHERE strftime('%Y-%m', s.data_sprzedazy) = ?
          AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
        ORDER BY s.data_sprzedazy DESC
    ''', (miesiac_filter,)).fetchall()
    
    # Statystyki dla wybranego miesiąca
    stats = conn.execute('''
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status != 'zwrot' THEN cena * ilosc ELSE 0 END) as przychod,
            SUM(CASE WHEN status = 'zwrot' THEN 1 ELSE 0 END) as zwroty_cnt,
            SUM(CASE WHEN status = 'zwrot' THEN cena * ilosc ELSE 0 END) as zwroty_suma
        FROM sprzedaze
        WHERE strftime('%Y-%m', data_sprzedazy) = ?
          AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''', (miesiac_filter,)).fetchone()
    
    # Lista dostępnych miesięcy
    miesiace_db = conn.execute('''
        SELECT DISTINCT strftime('%Y-%m', data_sprzedazy) as miesiac
        FROM sprzedaze
        ORDER BY miesiac DESC
        LIMIT 12
    ''').fetchall()
    
    # Generuj opcje select
    miesiace_nazwy = {
        '01': 'Styczeń', '02': 'Luty', '03': 'Marzec', '04': 'Kwiecień',
        '05': 'Maj', '06': 'Czerwiec', '07': 'Lipiec', '08': 'Sierpień',
        '09': 'Wrzesień', '10': 'Październik', '11': 'Listopad', '12': 'Grudzień'
    }
    
    select_options = ''
    for m in miesiace_db:
        msc = m['miesiac']
        rok = msc[:4]
        msc_num = msc[5:7]
        nazwa = f"{miesiace_nazwy.get(msc_num, msc_num)} {rok}"
        selected = 'selected' if msc == miesiac_filter else ''
        select_options += f'<option value="{msc}" {selected}>{nazwa}</option>'
    
    items_html = ''
    for s in sprzedaze:
        is_zwrot = s['status'] == 'zwrot'
        is_manual = (s['allegro_order_id'] or '').startswith('MANUAL-')
        status_badge = '<span style="color:#ef4444;font-size:0.75rem">🔄 ZWROT</span>' if is_zwrot else ''
        opacity = '0.5' if is_zwrot else '1'
        
        # Nazwa produktu - kilka źródeł
        try:
            nazwa = s['produkt_nazwa'] or s['nazwa'] or ''
        except (IndexError, KeyError):
            try:
                nazwa = s['produkt_nazwa'] or ''
            except (IndexError, KeyError):
                nazwa = ''
        if not nazwa or nazwa == 'Produkt':
            # Fallback - użyj kupującego ale zaznacz że brak nazwy
            nazwa = f"Zamówienie od {s['kupujacy']}"
        
        # Formatuj datę ładnie
        data_raw = s['data_sprzedazy'] or ''
        if 'T' in data_raw:
            data_str = data_raw[:10]  # YYYY-MM-DD
        else:
            data_str = data_raw[:10]
        
        # Dzień i miesiąc
        try:
            parts = data_str.split('-')
            dzien = parts[2] if len(parts) >= 3 else '??'
            miesiac_num = int(parts[1]) if len(parts) >= 2 else 0
            miesiace = ['', 'STY', 'LUT', 'MAR', 'KWI', 'MAJ', 'CZE', 'LIP', 'SIE', 'WRZ', 'PAŹ', 'LIS', 'GRU']
            miesiac = miesiace[miesiac_num] if 0 < miesiac_num <= 12 else '???'
        except:
            dzien = '??'
            miesiac = '???'
        
        # Określ przycisk akcji
        if is_manual:
            akcja_btn = f'<a href="/sprzedaze/usun/{s["id"]}?miesiac={miesiac_filter}" onclick="return confirm(\'Usunąć tę sprzedaż i przywrócić ilość?\')" style="padding:6px 10px;background:#f97316;border-radius:6px;color:#fff;text-decoration:none;font-size:0.75rem">🗑️ Usuń</a>'
        elif is_zwrot:
            akcja_btn = f'<a href="/sprzedaze/unzwrot/{s["id"]}?miesiac={miesiac_filter}" style="padding:6px 10px;background:#22c55e;border-radius:6px;color:#fff;text-decoration:none;font-size:0.75rem">Cofnij</a>'
        else:
            akcja_btn = f'<a href="/sprzedaze/zwrot/{s["id"]}?miesiac={miesiac_filter}" style="padding:6px 10px;background:#ef4444;border-radius:6px;color:#fff;text-decoration:none;font-size:0.75rem">Zwrot</a>'
        
        items_html += f'''
        <div style="display:flex;align-items:center;background:#12121a;border:1px solid #1e1e2e;border-radius:10px;padding:12px;margin-bottom:8px;opacity:{opacity}">
            <div style="min-width:50px;text-align:center;margin-right:12px;padding-right:12px;border-right:1px solid #2a2a3a">
                <div style="font-size:1.3rem;font-weight:700;color:#3b82f6">{dzien}</div>
                <div style="font-size:0.65rem;color:#64748b">{miesiac}</div>
            </div>
            <div style="flex:1;min-width:0">
                <div style="font-weight:600;font-size:0.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{nazwa[:40]}</div>
                <div style="font-size:0.75rem;color:#64748b">{s['kupujacy']} {status_badge}</div>
            </div>
            <div style="text-align:right;margin-left:10px">
                <div style="font-weight:700;color:{'#ef4444' if is_zwrot else '#22c55e'}">{'-' if is_zwrot else ''}{s['cena']:.0f} zł</div>
                <div style="font-size:0.7rem;color:#64748b">x{s['ilosc']}</div>
            </div>
            <div style="margin-left:10px">
                {akcja_btn}
            </div>
        </div>
        '''
    
    # Nazwa wybranego miesiąca do wyświetlenia
    msc_num = miesiac_filter[5:7] if len(miesiac_filter) >= 7 else '01'
    msc_rok = miesiac_filter[:4] if len(miesiac_filter) >= 4 else '2026'
    msc_nazwa = f"{miesiace_nazwy.get(msc_num, msc_num)} {msc_rok}"
    
    # Komunikat z sync zwrotów
    msg_html = ''
    if msg == 'success':
        msg_html = f'<div style="background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.3);padding:12px;border-radius:8px;margin-bottom:15px;text-align:center;color:#22c55e">✅ Oznaczono {msg_cnt} zwrotów z Allegro</div>'
    elif msg == 'none':
        msg_html = '<div style="background:rgba(234,179,8,0.15);border:1px solid rgba(234,179,8,0.3);padding:12px;border-radius:8px;margin-bottom:15px;text-align:center;color:#eab308">ℹ️ Brak nowych zwrotów w Allegro</div>'
    elif msg == 'allegro_auth':
        msg_html = '<div style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);padding:12px;border-radius:8px;margin-bottom:15px;text-align:center;color:#ef4444">❌ Zaloguj się do Allegro</div>'
    elif msg == 'error':
        msg_html = f'<div style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);padding:12px;border-radius:8px;margin-bottom:15px;text-align:center;color:#ef4444">❌ Błąd: {msg_detail}</div>'
    elif msg == 'naprawiono':
        msg_html = f'<div style="background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.3);padding:12px;border-radius:8px;margin-bottom:15px;text-align:center;color:#22c55e">✅ Naprawiono dane {msg_cnt} produktów (nazwy + zdjęcia)</div>'
    
    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>💰 SPRZEDAŻE</h1>
            <small>Lista zamówień i zwroty</small>
        </div>
        
        <!-- Filtr miesiąca -->
        <div style="margin-bottom:15px">
            <select onchange="window.location.href='/sprzedaze?miesiac='+this.value" 
                    style="width:100%;padding:12px;background:#12121a;border:1px solid #3b82f6;border-radius:8px;color:#fff;font-size:1rem;cursor:pointer">
                {select_options}
            </select>
        </div>
        
        <!-- Przyciski akcji -->
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:15px">
            <a href="/sync-custom?from={miesiac_filter}-01"
               style="display:block;text-align:center;padding:10px;background:#f59e0b;border-radius:8px;color:#fff;text-decoration:none;font-weight:600;font-size:0.85rem">
                🔄 Sync miesiąc
            </a>
            <a href="/sprzedaze/sync-zwroty?miesiac={miesiac_filter}"
               style="display:block;text-align:center;padding:10px;background:#ef4444;border-radius:8px;color:#fff;text-decoration:none;font-weight:600;font-size:0.85rem">
                🔄 Sync zwrotów
            </a>
            <a href="/sprzedaze/napraw-nazwy?miesiac={miesiac_filter}"
               style="display:block;text-align:center;padding:10px;background:#3b82f6;border-radius:8px;color:#fff;text-decoration:none;font-weight:600;font-size:0.85rem">
                🔧 Napraw dane
            </a>
            <a href="/sprzedaze/dopasuj"
               style="display:block;text-align:center;padding:10px;background:#8b5cf6;border-radius:8px;color:#fff;text-decoration:none;font-weight:600;font-size:0.85rem">
                🔗 Dopasuj
            </a>
        </div>
        
        {msg_html}
        
        <!-- Statystyki miesiąca -->
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px">
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#22c55e">{stats['przychod'] or 0:.0f} zł</div>
                <div style="font-size:0.7rem;color:#64748b">PRZYCHÓD</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#ef4444">{stats['zwroty_cnt'] or 0}</div>
                <div style="font-size:0.7rem;color:#64748b">ZWROTÓW</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#ef4444">-{stats['zwroty_suma'] or 0:.0f} zł</div>
                <div style="font-size:0.7rem;color:#64748b">WARTOŚĆ ZWROTÓW</div>
            </div>
        </div>
        
        <div style="font-size:0.8rem;color:#64748b;margin-bottom:10px">{msc_nazwa.upper()} ({len(sprzedaze)} zamówień)</div>
        
        {items_html if items_html else '<div style="text-align:center;color:#64748b;padding:30px">Brak sprzedaży w tym miesiącu</div>'}
        
        <a href="/statystyki" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:20px">← Statystyki</a>
    </div>
    '''
    return html


@app.route('/sprzedaze/zwrot/<int:sale_id>')
def oznacz_zwrot(sale_id):
    """Oznacza sprzedaż jako zwrot"""
    from modules.database import get_db
    conn = get_db()
    conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('zwrot', sale_id))
    conn.commit()
    # Zachowaj filtr miesiąca
    miesiac = request.args.get('miesiac', '')
    return redirect(f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze')


@app.route('/sprzedaze/unzwrot/<int:sale_id>')
def cofnij_zwrot(sale_id):
    """Cofa oznaczenie zwrotu"""
    from modules.database import get_db
    conn = get_db()
    conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('wyslana', sale_id))
    conn.commit()
    # Zachowaj filtr miesiąca
    miesiac = request.args.get('miesiac', '')
    return redirect(f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze')


@app.route('/sprzedaze/usun/<int:sale_id>')
def usun_sprzedaz(sale_id):
    """Usuwa sprzedaż (ręczną korektę) i przywraca ilość produktu"""
    from modules.database import get_db
    miesiac = request.args.get('miesiac', '')
    
    conn = get_db()
    
    # Pobierz dane sprzedaży
    sprzedaz = conn.execute('SELECT * FROM sprzedaze WHERE id = ?', (sale_id,)).fetchone()
    
    if not sprzedaz:
        flash('Nie znaleziono sprzedaży', 'error')
        return redirect(f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze')
    
    # Sprawdź czy to ręczna korekta
    if not (sprzedaz['allegro_order_id'] or '').startswith('MANUAL-'):
        flash('Można usuwać tylko ręczne korekty', 'error')
        return redirect(f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze')
    
    # Przywróć ilość produktu
    if sprzedaz['produkt_id']:
        conn.execute('''
            UPDATE produkty 
            SET ilosc = ilosc + ?,
                status = CASE WHEN status = 'sprzedany' THEN 'wystawiony' ELSE status END
            WHERE id = ?
        ''', (sprzedaz['ilosc'], sprzedaz['produkt_id']))
    
    # Usuń wpis sprzedaży
    conn.execute('DELETE FROM sprzedaze WHERE id = ?', (sale_id,))
    
    conn.commit()
    
    flash(f'✅ Usunięto sprzedaż i przywrócono {sprzedaz["ilosc"]} szt. do magazynu', 'success')
    return redirect(f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze')


@app.route('/sprzedaze/sync-zwroty')
def sync_zwroty_allegro():
    """Synchronizuje zwroty z Allegro API dla wybranego miesiąca"""
    from modules.allegro_api import sync_returns, is_authenticated
    
    miesiac = request.args.get('miesiac', '')
    base_url = f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze'
    
    if not is_authenticated():
        return redirect(f'{base_url}&msg=allegro_auth')
    
    try:
        updated, error = sync_returns(miesiac if miesiac else None)
        if error:
            return redirect(f'{base_url}&msg=error&detail={error[:50]}')
        elif updated > 0:
            return redirect(f'{base_url}&msg=success&cnt={updated}')
        else:
            return redirect(f'{base_url}&msg=none')
    except Exception as e:
        print(f"❌ Błąd sync_returns: {e}")
        return redirect(f'{base_url}&msg=error&detail={str(e)[:50]}')


@app.route('/sprzedaze/napraw-nazwy')
def napraw_nazwy_sprzedazy():
    """Uzupełnia brakujące nazwy, zdjęcia i daty w sprzedażach z Allegro API"""
    from modules.allegro_api import is_authenticated, allegro_request
    from modules.database import get_db

    miesiac = request.args.get('miesiac', '')
    if not miesiac:
        miesiac = datetime.now().strftime('%Y-%m')

    if not is_authenticated():
        return redirect(f'/sprzedaze?miesiac={miesiac}&msg=allegro_auth')

    conn = get_db()

    # Upewnij się że kolumny istnieją
    try:
        conn.execute('ALTER TABLE sprzedaze ADD COLUMN nazwa TEXT DEFAULT ""')
    except:
        pass
    try:
        conn.execute('ALTER TABLE sprzedaze ADD COLUMN zdjecie_url TEXT DEFAULT ""')
    except:
        pass

    # Pobierz sprzedaże z wybranego miesiąca bez nazwy/zdjęcia
    try:
        sprzedaze = conn.execute('''
            SELECT s.id, s.allegro_order_id, s.nazwa, s.zdjecie_url, s.oferta_id,
                   s.data_sprzedazy,
                   COALESCE(o.tytul, s.nazwa, '') as oferta_tytul, p.zdjecie_url as produkt_zdjecie
            FROM sprzedaze s
            LEFT JOIN oferty o ON s.oferta_id = o.id
            LEFT JOIN produkty p ON COALESCE(s.produkt_id, o.produkt_id) = p.id
            WHERE (s.nazwa IS NULL OR s.nazwa = '' OR s.nazwa = 'Produkt'
                   OR s.zdjecie_url IS NULL OR s.zdjecie_url = '')
            AND s.allegro_order_id IS NOT NULL
            AND strftime('%Y-%m', s.data_sprzedazy) = ?
            LIMIT 100
        ''', (miesiac,)).fetchall()
    except Exception as e:
        print(f"Query error: {e}")
        sprzedaze = conn.execute('''
            SELECT s.id, s.allegro_order_id, s.oferta_id, s.data_sprzedazy,
                   COALESCE(o.tytul, s.nazwa, '') as oferta_tytul
            FROM sprzedaze s
            LEFT JOIN oferty o ON s.oferta_id = o.id
            WHERE strftime('%Y-%m', s.data_sprzedazy) = ?
            LIMIT 100
        ''', (miesiac,)).fetchall()
    
    updated = 0
    
    # Helper do bezpiecznego dostępu do sqlite3.Row
    def safe_get(row, key, default=None):
        try:
            val = row[key]
            return val if val else default
        except (KeyError, IndexError):
            return default
    
    for s in sprzedaze:
        new_name = safe_get(s, 'nazwa')
        if new_name == 'Produkt':
            new_name = None
        new_image = safe_get(s, 'zdjecie_url') or safe_get(s, 'produkt_zdjecie')
        
        # Metoda 1: z tabeli oferty/produkty
        if not new_name and safe_get(s, 'oferta_tytul'):
            new_name = s['oferta_tytul'][:100]
        
        # Metoda 2: pobierz z Allegro API (nazwa + zdjęcie + popraw datę)
        new_date = None
        if s['allegro_order_id']:
            try:
                order_data, err = allegro_request('GET', f"/order/checkout-forms/{s['allegro_order_id']}")
                if order_data:
                    # Popraw datę z boughtAt
                    bought_at = order_data.get('boughtAt', '')
                    if bought_at:
                        try:
                            from datetime import datetime as _dt
                            dt_str = bought_at.replace('Z', '+00:00')
                            dt = _dt.fromisoformat(dt_str)
                            dt_local = dt.astimezone().replace(tzinfo=None)
                            correct_date = dt_local.strftime('%Y-%m-%d %H:%M:%S')
                            current_date = safe_get(s, 'data_sprzedazy', '')
                            # Napraw jeśli data jest inna (inny dzień)
                            if correct_date[:10] != (current_date or '')[:10]:
                                new_date = correct_date
                                print(f"📅 Poprawiam datę: {s['id']} {current_date[:10]} → {correct_date[:10]}")
                        except Exception as de:
                            print(f"Date parse error: {de}")

                    if 'lineItems' in order_data:
                        for item in order_data['lineItems']:
                            offer = item.get('offer', {})
                            if not new_name:
                                name = offer.get('name', '')
                                if name:
                                    new_name = name[:100]
                            # Pobierz zdjęcie z oferty
                            if not new_image:
                                offer_id = offer.get('id')
                                if offer_id:
                                    try:
                                        offer_data, _ = allegro_request('GET', f'/sale/product-offers/{offer_id}')
                                        if offer_data:
                                            images = offer_data.get('images', [])
                                            if images:
                                                new_image = images[0].get('url', '')
                                    except:
                                        pass
                            if new_name:
                                break
            except Exception as e:
                print(f"API error: {e}")
        
        # Aktualizuj jeśli znaleziono coś nowego (nazwa, zdjęcie lub data)
        if new_name or new_image or new_date:
            try:
                updates = []
                params = []
                if new_name:
                    updates.append('nazwa = ?')
                    params.append(new_name)
                if new_image:
                    updates.append('zdjecie_url = ?')
                    params.append(new_image)
                if new_date:
                    updates.append('data_sprzedazy = ?')
                    params.append(new_date)
                params.append(s['id'])
                conn.execute(f'UPDATE sprzedaze SET {", ".join(updates)} WHERE id = ?', params)
                updated += 1
                date_info = f' | data: {new_date[:10]}' if new_date else ''
                print(f"✅ Naprawiono: {s['id']} -> {(new_name or '')[:40]}... | img: {'✓' if new_image else '✗'}{date_info}")
            except Exception as e:
                print(f"❌ Błąd update: {e}")
    
    conn.commit()

    return redirect(f'/sprzedaze?miesiac={miesiac}&msg=naprawiono&cnt={updated}')


@app.route('/sprzedaze/dodaj-reczna', methods=['POST'])
def sprzedaze_dodaj_reczna():
    """Ręczne dodanie sprzedaży (korekta)"""
    from modules.database import get_db
    from datetime import datetime
    
    produkt_id = request.form.get('produkt_id', type=int)
    ilosc = request.form.get('ilosc', 1, type=int)
    cena = request.form.get('cena', 0, type=float)
    kupujacy = request.form.get('kupujacy', 'Ręczna korekta')
    
    if not produkt_id:
        flash('Brak ID produktu', 'error')
        return redirect(request.referrer or '/palety')
    
    conn = get_db()
    
    # Pobierz dane produktu
    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        flash('Nie znaleziono produktu', 'error')
        return redirect(request.referrer or '/palety')
    
    # Dodaj wpis do sprzedaze
    conn.execute('''INSERT INTO sprzedaze 
        (allegro_order_id, cena, ilosc, kupujacy, status, data_sprzedazy, produkt_id, nazwa)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (f'MANUAL-{datetime.now().strftime("%Y%m%d%H%M%S")}', cena, ilosc, kupujacy, 
         'nowa', datetime.now().isoformat(), produkt_id, produkt['nazwa']))
    
    # Zaktualizuj ilość w produkcie
    new_qty = max(0, produkt['ilosc'] - ilosc)
    conn.execute('''UPDATE produkty SET 
        ilosc = ?,
        status = CASE WHEN ? = 0 THEN 'sprzedany' ELSE status END
        WHERE id = ?''', (new_qty, new_qty, produkt_id))
    
    conn.commit()
    
    flash(f'✅ Dodano sprzedaż: {ilosc} szt. za {cena:.0f} zł', 'success')
    return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')


# ==================== DOPASOWYWANIE SPRZEDAZY ====================

def _normalize_pl(text):
    """Zamienia polskie znaki na ASCII do porównań"""
    if not text:
        return ''
    pl_map = str.maketrans('ąćęłńóśźżĄĆĘŁŃÓŚŹŻ', 'acelnoszzACELNOSZZ')
    return text.translate(pl_map).lower()


@app.route('/api/sprzedaze/szukaj-produkt')
def api_sprzedaze_szukaj_produkt():
    """API - wyszukuje produkty (multi-word, diacritics-insensitive)"""
    from modules.database import get_db
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'results': []})

    conn = get_db()

    # Normalizuj słowa do porównań (bez polskich znaków)
    words = [_normalize_pl(w) for w in q.split() if len(w) >= 2][:6]
    if not words:
        return jsonify({'results': []})

    # Pobierz wszystkie produkty (188 rekordów — szybko)
    all_products = conn.execute('''
        SELECT p.id, p.nazwa, p.ean, p.asin, p.ilosc, p.cena_allegro, p.zdjecie_url,
               COALESCE(pal.nazwa, '') as paleta_nazwa
        FROM produkty p
        LEFT JOIN palety pal ON p.paleta_id = pal.id
        ORDER BY p.id DESC
    ''').fetchall()

    # Filtruj w Pythonie (normalizacja polskich znaków)
    scored = []
    q_norm = _normalize_pl(q)
    min_match = max(2, int(len(words) * 0.6))  # min 60% słów lub 2

    for p in all_products:
        nazwa_norm = _normalize_pl(p['nazwa'] or '')
        ean = (p['ean'] or '').lower()
        asin = (p['asin'] or '').lower()

        # EAN/ASIN exact match — priorytet
        if q_norm in ean or q_norm in asin:
            scored.append((100, p))
            continue

        # Multi-word: liczymy ile słów matchuje
        hits = sum(1 for w in words if w in nazwa_norm)
        if hits >= min_match:
            scored.append((hits, p))

    # Sortuj: najlepsze dopasowanie na górze
    scored.sort(key=lambda x: -x[0])
    results = [p for _, p in scored[:15]]

    return jsonify({'results': [
        {'id': r['id'], 'nazwa': r['nazwa'], 'ean': r['ean'] or '', 'asin': r['asin'] or '',
         'ilosc': r['ilosc'], 'cena_allegro': r['cena_allegro'] or 0,
         'zdjecie_url': r['zdjecie_url'] or '', 'paleta': r['paleta_nazwa']}
        for r in results
    ]})


@app.route('/api/sprzedaze/dopasuj', methods=['POST'])
def api_sprzedaze_dopasuj():
    """API - dopasowuje grupę sprzedaży do produktu + historia"""
    from modules.database import get_db, add_historia

    sale_ids_str = request.form.get('sale_ids', '')
    produkt_id = request.form.get('produkt_id', type=int)

    if not sale_ids_str or not produkt_id:
        return jsonify({'ok': False, 'msg': 'Brak danych'}), 400

    try:
        sale_ids = [int(x.strip()) for x in sale_ids_str.split(',') if x.strip()]
    except ValueError:
        return jsonify({'ok': False, 'msg': 'Nieprawidłowe ID'}), 400

    if not sale_ids:
        return jsonify({'ok': False, 'msg': 'Brak ID sprzedaży'}), 400

    conn = get_db()

    produkt = conn.execute('SELECT id, nazwa, ilosc FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        return jsonify({'ok': False, 'msg': 'Produkt nie znaleziony'}), 404

    # Pobierz szczegóły sprzedaży PRZED update (do historii)
    placeholders = ','.join(['?' for _ in sale_ids])
    sprzedaze = conn.execute(f'''
        SELECT id, nazwa, cena, ilosc, data_sprzedazy
        FROM sprzedaze WHERE id IN ({placeholders}) AND produkt_id IS NULL
    ''', sale_ids).fetchall()

    # Update produkt_id
    updated = conn.execute(f'''
        UPDATE sprzedaze SET produkt_id = ?
        WHERE id IN ({placeholders}) AND produkt_id IS NULL
    ''', [produkt_id] + sale_ids)

    # Dodaj historię dla każdej dopasowanej sprzedaży
    for s in sprzedaze:
        przychod = (s['cena'] or 0) * (s['ilosc'] or 1)
        try:
            add_historia(produkt_id, 'sprzedano',
                f'Dopasowano sprzedaż #{s["id"]}: {s["ilosc"] or 1} szt. za {przychod:.0f} zł ({s["data_sprzedazy"][:10] if s["data_sprzedazy"] else "?"})',
                {'sprzedaz_id': s['id'], 'cena': s['cena'], 'ilosc': s['ilosc'],
                 'data_sprzedazy': s['data_sprzedazy'], 'zrodlo': 'dopasowanie'})
        except:
            pass

    conn.commit()

    return jsonify({
        'ok': True,
        'matched': updated.rowcount,
        'product_name': produkt['nazwa']
    })


@app.route('/api/sprzedaze/auto-dopasuj', methods=['POST'])
def api_sprzedaze_auto_dopasuj():
    """API - automatycznie dopasowuje wszystkie sugestie"""
    from modules.database import get_db, add_historia

    conn = get_db()

    grupy = conn.execute('''
        SELECT TRIM(nazwa) as grupa_nazwa, GROUP_CONCAT(id) as sale_ids, COUNT(*) as cnt
        FROM sprzedaze
        WHERE produkt_id IS NULL AND nazwa IS NOT NULL AND TRIM(nazwa) != ''
          AND status NOT IN ('anulowana', 'zwrot')
        GROUP BY TRIM(nazwa)
    ''').fetchall()

    matched_groups = 0
    total_sales = 0

    for g in grupy:
        # Multi-word matching
        words = [w for w in g['grupa_nazwa'].split() if len(w) >= 3][:4]
        if len(words) < 2:
            continue

        where_parts = []
        params = []
        for w in words:
            where_parts.append("LOWER(nazwa) LIKE ?")
            params.append(f'%{w.lower()}%')

        match = conn.execute(f'''
            SELECT id FROM produkty WHERE {' AND '.join(where_parts)} LIMIT 1
        ''', params).fetchone()

        if match:
            sale_ids = [int(x) for x in g['sale_ids'].split(',')]
            ph = ','.join(['?' for _ in sale_ids])

            # Pobierz szczegóły sprzedaży PRZED update (do historii)
            sprzedaze = conn.execute(f'''
                SELECT id, nazwa, cena, ilosc, data_sprzedazy
                FROM sprzedaze WHERE id IN ({ph}) AND produkt_id IS NULL
            ''', sale_ids).fetchall()

            conn.execute(f'''
                UPDATE sprzedaze SET produkt_id = ?
                WHERE id IN ({ph}) AND produkt_id IS NULL
            ''', [match['id']] + sale_ids)

            # Dodaj historię dla każdej dopasowanej sprzedaży
            for s in sprzedaze:
                przychod = (s['cena'] or 0) * (s['ilosc'] or 1)
                try:
                    add_historia(match['id'], 'sprzedano',
                        f'Auto-dopasowano sprzedaż #{s["id"]}: {s["ilosc"] or 1} szt. za {przychod:.0f} zł ({s["data_sprzedazy"][:10] if s["data_sprzedazy"] else "?"})',
                        {'sprzedaz_id': s['id'], 'cena': s['cena'], 'ilosc': s['ilosc'],
                         'data_sprzedazy': s['data_sprzedazy'], 'zrodlo': 'auto-dopasowanie'})
                except:
                    pass

            matched_groups += 1
            total_sales += g['cnt']

    conn.commit()

    return jsonify({
        'ok': True,
        'matched': matched_groups,
        'total_sales': total_sales
    })


@app.route('/api/sprzedaze/repair', methods=['POST'])
def api_sprzedaze_repair():
    """Naprawa danych: usunięcie duplikatów + aktualizacja stanów magazynowych"""
    from modules.database import get_db
    conn = get_db()
    repairs = []

    # === 1. Usuń duplikaty zamówień ===
    dupes = conn.execute('''
        SELECT allegro_order_id, nazwa, cena, COUNT(*) as cnt,
               MIN(id) as keep_id, GROUP_CONCAT(id) as all_ids
        FROM sprzedaze
        WHERE allegro_order_id IS NOT NULL
        GROUP BY allegro_order_id, nazwa, cena
        HAVING COUNT(*) > 1
    ''').fetchall()

    removed = 0
    for d in dupes:
        all_ids = [int(x) for x in d['all_ids'].split(',')]
        to_delete = [i for i in all_ids if i != d['keep_id']]
        if to_delete:
            ph = ','.join(['?' for _ in to_delete])
            conn.execute(f'DELETE FROM sprzedaze WHERE id IN ({ph})', to_delete)
            removed += len(to_delete)
    if removed:
        repairs.append(f'Usunięto {removed} duplikatów zamówień')

    # === 2. Przelicz stany magazynowe na podstawie sprzedaży ===
    # Pobierz ile sztuk sprzedano per produkt
    sold = conn.execute('''
        SELECT produkt_id, SUM(ilosc) as sold_qty
        FROM sprzedaze
        WHERE produkt_id IS NOT NULL
        AND status NOT IN ('anulowana', 'zwrot')
        GROUP BY produkt_id
    ''').fetchall()

    stock_fixed = 0
    for s in sold:
        pid = s['produkt_id']
        sold_qty = s['sold_qty'] or 0

        # Pobierz oryginalną ilość z palety (zakupowa)
        prod = conn.execute('''
            SELECT p.id, p.ilosc, p.nazwa, p.status,
                   COALESCE(p.ilosc + (SELECT COALESCE(SUM(sp.ilosc), 0)
                       FROM sprzedaze sp WHERE sp.produkt_id = p.id
                       AND sp.status NOT IN ('anulowana', 'zwrot')), p.ilosc) as original_qty
            FROM produkty p WHERE p.id = ?
        ''', (pid,)).fetchone()

        if not prod:
            continue

        # Oblicz prawidłową ilość
        correct_qty = max(0, prod['ilosc'])  # Obecna ilość

        # Jeśli status nie jest 'sprzedany' ale ilosc powinna być 0
        if sold_qty > 0 and prod['ilosc'] > 0:
            # Sprawdź czy stock został odjęty - porównaj oczekiwane
            pass  # Stock jest już odjęty przez sync

        # Jeśli ilosc=0 ale status nie jest 'sprzedany'
        if prod['ilosc'] <= 0 and prod['status'] not in ('sprzedany', 'wysłane'):
            conn.execute("UPDATE produkty SET status = 'sprzedany' WHERE id = ?", (pid,))
            stock_fixed += 1

        # Jeśli ilosc > 0 ale status jest 'sprzedany' (błędnie oznaczony)
        if prod['ilosc'] > 0 and prod['status'] == 'sprzedany':
            conn.execute("UPDATE produkty SET status = 'wystawiony' WHERE id = ?", (pid,))
            stock_fixed += 1

    if stock_fixed:
        repairs.append(f'Naprawiono status {stock_fixed} produktów')

    # === 3. Linkowanie przez tabelę oferty (allegro_id → produkt_id) ===
    linked = 0
    unlinked = conn.execute('''
        SELECT s.id, s.allegro_order_id
        FROM sprzedaze s
        WHERE s.produkt_id IS NULL AND s.oferta_id IS NOT NULL
        AND s.status NOT IN ('anulowana', 'zwrot')
    ''').fetchall()
    for sale in unlinked:
        oferta = conn.execute('SELECT produkt_id FROM oferty WHERE id = ?', (sale['oferta_id'],)).fetchone()
        if oferta and oferta['produkt_id']:
            conn.execute('UPDATE sprzedaze SET produkt_id = ? WHERE id = ?', (oferta['produkt_id'], sale['id']))
            linked += 1
    if linked:
        repairs.append(f'Połączono {linked} sprzedaży przez oferty')

    conn.commit()

    return jsonify({
        'ok': True,
        'repairs': repairs,
        'removed_duplicates': removed,
        'stock_fixed': stock_fixed,
        'linked': linked
    })


@app.route('/sprzedaze/dopasuj')
def sprzedaze_dopasuj():
    """Strona dopasowywania sprzedaży do produktów"""
    from modules.database import get_db
    import html as html_mod

    conn = get_db()

    # Grupuj niedopasowane sprzedaże po nazwie
    grupy = conn.execute('''
        SELECT
            COALESCE(NULLIF(TRIM(nazwa), ''), '(brak nazwy)') as grupa_nazwa,
            COUNT(*) as cnt,
            SUM(cena * ilosc) as wartosc,
            GROUP_CONCAT(id) as sale_ids
        FROM sprzedaze
        WHERE produkt_id IS NULL
          AND status NOT IN ('anulowana', 'zwrot')
        GROUP BY CASE
            WHEN nazwa IS NULL OR TRIM(nazwa) = '' THEN '(brak nazwy)'
            ELSE TRIM(nazwa)
        END
        ORDER BY cnt DESC
    ''').fetchall()

    total_unmatched = sum(g['cnt'] for g in grupy)

    # Auto-sugestie — szukaj produktu po nazwie
    suggestions = {}
    for g in grupy:
        if g['grupa_nazwa'] == '(brak nazwy)':
            continue
        # Multi-word matching: weź 3-4 kluczowe słowa i szukaj AND
        words = [w for w in g['grupa_nazwa'].split() if len(w) >= 3][:4]
        if len(words) < 2:
            continue
        where_parts = []
        params = []
        for w in words:
            where_parts.append("LOWER(nazwa) LIKE ?")
            params.append(f'%{w.lower()}%')
        match = conn.execute(f'''
            SELECT id, nazwa, zdjecie_url
            FROM produkty
            WHERE {' AND '.join(where_parts)}
            ORDER BY id DESC
            LIMIT 1
        ''', params).fetchone()
        if match:
            suggestions[g['grupa_nazwa']] = dict(match)

    # Buduj HTML grup
    groups_html = ''
    for g in grupy:
        nazwa = html_mod.escape(g['grupa_nazwa'])
        nazwa_js = html_mod.escape(g['grupa_nazwa']).replace("'", "\\'").replace('"', '&quot;')
        sale_ids = g['sale_ids']
        cnt = g['cnt']
        wartosc = g['wartosc'] or 0

        sug = suggestions.get(g['grupa_nazwa'])
        sug_html = ''
        if sug:
            sug_nazwa = html_mod.escape(sug['nazwa'][:55])
            sug_img = html_mod.escape(sug.get('zdjecie_url') or '')
            sug_html = f'''
            <div style="background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);border-radius:8px;padding:8px;margin-top:8px;display:flex;align-items:center;gap:8px">
                <img src="{sug_img}" style="width:32px;height:32px;object-fit:contain;background:#fff;border-radius:6px" onerror="this.style.display='none'">
                <div style="flex:1;min-width:0">
                    <div style="font-size:0.75rem;color:#22c55e">Sugestia:</div>
                    <div style="font-size:0.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{sug_nazwa}</div>
                </div>
                <button onclick="dopasuj('{sale_ids}', {sug['id']}, this)"
                        style="padding:6px 12px;background:#22c55e;border:none;border-radius:6px;color:#fff;font-size:0.75rem;cursor:pointer;white-space:nowrap">
                    ✓ Dopasuj
                </button>
            </div>'''

        groups_html += f'''
        <div class="grupa-item" style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:14px;margin-bottom:10px">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
                <div style="flex:1;min-width:0">
                    <div style="font-weight:600;font-size:0.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#fff">{nazwa[:60]}</div>
                    <div style="font-size:0.75rem;color:#64748b">{cnt} szt. | {wartosc:.0f} zł</div>
                </div>
                <button onclick="openSearch('{nazwa_js}', '{sale_ids}')"
                        style="padding:8px 14px;background:#3b82f6;border:none;border-radius:8px;color:#fff;font-size:0.8rem;cursor:pointer;white-space:nowrap">
                    🔍 Szukaj
                </button>
            </div>
            {sug_html}
        </div>
        '''

    # Przycisk auto-dopasuj (tylko gdy są sugestie)
    auto_btn_html = ''
    if suggestions:
        auto_btn_html = f'''
        <button onclick="autoMatchAll()"
                style="width:100%;padding:14px;background:linear-gradient(135deg,#22c55e,#16a34a);border:none;border-radius:10px;color:#fff;font-weight:700;font-size:1rem;cursor:pointer;margin-bottom:20px">
            ⚡ Auto-dopasuj {len(suggestions)} sugestii
        </button>
        '''

    page_html = CSS + f'''
    <div style="max-width:700px;margin:0 auto;padding:15px 15px 100px">
        <div style="text-align:center;margin-bottom:20px">
            <h1 style="color:#fff;font-size:1.5rem;margin:0">🔗 DOPASUJ SPRZEDAŻE</h1>
            <small style="color:#64748b">Połącz niedopasowane sprzedaże z produktami</small>
        </div>

        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px">
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#ef4444">{total_unmatched}</div>
                <div style="font-size:0.7rem;color:#64748b">NIEDOPASOWANYCH</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#f59e0b">{len(grupy)}</div>
                <div style="font-size:0.7rem;color:#64748b">GRUP</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#22c55e">{len(suggestions)}</div>
                <div style="font-size:0.7rem;color:#64748b">SUGESTII</div>
            </div>
        </div>

        {auto_btn_html}

        <div id="grupy-lista">
        {groups_html}
        </div>

        <a href="/sprzedaze" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:20px;padding:15px">← Powrót do sprzedaży</a>
    </div>

    <!-- Modal szukania -->
    <div id="searchModal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.85);z-index:1000;padding:15px;overflow-y:auto">
        <div style="max-width:500px;margin:40px auto;background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:20px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px">
                <h3 style="color:#3b82f6;margin:0;font-size:1.1rem">🔍 Szukaj produktu</h3>
                <button onclick="closeModal()" style="background:none;border:none;color:#64748b;font-size:1.5rem;cursor:pointer">&times;</button>
            </div>
            <div id="modalInfo" style="background:#0a0a0f;padding:10px;border-radius:8px;margin-bottom:12px;font-size:0.8rem;color:#94a3b8"></div>
            <input id="szukajInput" type="text" placeholder="Szukaj po nazwie, EAN, ASIN..."
                   style="width:100%;padding:12px;background:#0a0a0f;border:1px solid #1e1e2e;border-radius:10px;color:#fff;font-size:1rem;margin-bottom:12px;box-sizing:border-box"
                   oninput="debounceSearch(this.value)">
            <div id="wyniki" style="max-height:50vh;overflow-y:auto"></div>
        </div>
    </div>

    <script>
    let _saleIds = '';
    let _timer = null;

    function openSearch(nazwa, saleIds) {{
        _saleIds = saleIds;
        document.getElementById('searchModal').style.display = 'block';
        document.getElementById('modalInfo').textContent = nazwa.substring(0, 50) + ' (' + saleIds.split(',').length + ' szt.)';
        const inp = document.getElementById('szukajInput');
        inp.value = nazwa.substring(0, 30);
        inp.focus();
        debounceSearch(inp.value);
    }}

    function closeModal() {{
        document.getElementById('searchModal').style.display = 'none';
        _saleIds = '';
    }}

    function debounceSearch(q) {{
        clearTimeout(_timer);
        _timer = setTimeout(() => doSearch(q), 300);
    }}

    function doSearch(q) {{
        if (q.length < 2) {{ document.getElementById('wyniki').innerHTML = ''; return; }}
        document.getElementById('wyniki').innerHTML = '<div style="text-align:center;padding:20px;color:#64748b">Szukam...</div>';

        fetch('/api/sprzedaze/szukaj-produkt?q=' + encodeURIComponent(q))
            .then(r => r.json())
            .then(data => {{
                let h = '';
                if (data.results && data.results.length > 0) {{
                    data.results.forEach(p => {{
                        h += '<div style="display:flex;align-items:center;background:#0a0a0f;border:1px solid #1e1e2e;border-radius:10px;padding:10px;margin-bottom:8px;cursor:pointer" '
                           + 'onclick="dopasuj(\\'' + _saleIds + '\\', ' + p.id + ', this)">'
                           + '<img src="' + (p.zdjecie_url||'') + '" style="width:40px;height:40px;object-fit:contain;background:#fff;border-radius:8px;margin-right:10px" onerror="this.style.display=\\'none\\'">'
                           + '<div style="flex:1;min-width:0">'
                           + '<div style="font-size:0.85rem;font-weight:600;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + p.nazwa.substring(0,55) + '</div>'
                           + '<div style="font-size:0.7rem;color:#64748b">' + (p.ean||'') + ' ' + (p.asin||'') + ' | ' + p.paleta + '</div>'
                           + '</div>'
                           + '<div style="color:#22c55e;font-weight:700;margin-left:10px;font-size:0.85rem">' + (p.cena_allegro||0) + ' zl</div>'
                           + '</div>';
                    }});
                }} else {{
                    h = '<div style="text-align:center;padding:20px;color:#64748b">Brak wyników</div>';
                }}
                document.getElementById('wyniki').innerHTML = h;
            }});
    }}

    function dopasuj(saleIds, produktId, btn) {{
        const cnt = saleIds.split(',').length;
        if (!confirm('Dopasować ' + cnt + ' sprzedaży do tego produktu?')) return;

        btn.style.opacity = '0.5';
        btn.style.pointerEvents = 'none';

        const fd = new FormData();
        fd.append('sale_ids', saleIds);
        fd.append('produkt_id', produktId);

        fetch('/api/sprzedaze/dopasuj', {{method: 'POST', body: fd}})
            .then(r => r.json())
            .then(d => {{
                if (d.ok) {{
                    closeModal();
                    // Ukryj dopasowaną grupę
                    const items = document.querySelectorAll('.grupa-item');
                    items.forEach(el => {{
                        if (el.innerHTML.includes(saleIds.split(',')[0])) {{
                            el.style.opacity = '0.2';
                            el.style.pointerEvents = 'none';
                            el.innerHTML = '<div style="text-align:center;color:#22c55e;padding:10px">✓ Dopasowano ' + d.matched + ' szt. → ' + d.product_name.substring(0,40) + '</div>';
                        }}
                    }});
                }} else {{
                    alert('Błąd: ' + d.msg);
                    btn.style.opacity = '1';
                    btn.style.pointerEvents = 'auto';
                }}
            }})
            .catch(() => {{
                alert('Błąd połączenia');
                btn.style.opacity = '1';
                btn.style.pointerEvents = 'auto';
            }});
    }}

    function autoMatchAll() {{
        if (!confirm('Auto-dopasować wszystkie sugestie?\\nTo połączy sprzedaże z zasugerowanymi produktami.')) return;

        fetch('/api/sprzedaze/auto-dopasuj', {{method: 'POST'}})
            .then(r => r.json())
            .then(d => {{
                if (d.ok) {{
                    alert('Dopasowano ' + d.matched + ' grup (' + d.total_sales + ' sprzedaży)');
                    location.reload();
                }} else {{
                    alert('Błąd: ' + d.msg);
                }}
            }});
    }}

    // Zamknij modal kliknięciem w tło
    document.getElementById('searchModal').addEventListener('click', function(e) {{
        if (e.target === this) closeModal();
    }});
    </script>
    '''

    return page_html


@app.route('/sprzedaze/korekta-ilosci', methods=['POST'])
def sprzedaze_korekta_ilosci():
    """Ręczna korekta ilości produktu - jeśli ilość rośnie, cofa też sprzedaże"""
    from modules.database import get_db

    produkt_id = request.form.get('produkt_id', type=int)
    nowa_ilosc = request.form.get('nowa_ilosc', type=int)

    if produkt_id is None or nowa_ilosc is None:
        flash('Brak danych', 'error')
        return redirect(request.referrer or '/palety')

    conn = get_db()

    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        flash('Nie znaleziono produktu', 'error')
        return redirect(request.referrer or '/palety')

    stara_ilosc = produkt['ilosc'] or 0

    # Jeśli ilość rośnie (korekta w górę) → cofnij sprzedaże i odlicz przychód
    if nowa_ilosc > stara_ilosc:
        # Oznacz aktywne sprzedaże jako zwrot
        sprzedaze = conn.execute('''
            SELECT id, ilosc FROM sprzedaze
            WHERE produkt_id = ? AND COALESCE(status,'') NOT IN ('zwrot','anulowane','anulowana')
        ''', (produkt_id,)).fetchall()
        for s in sprzedaze:
            conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('zwrot', s['id']))

        # Wyczyść offline stats
        try:
            conn.execute('UPDATE produkty SET sprzedano_offline = 0, przychod_offline = 0 WHERE id = ?', (produkt_id,))
        except:
            pass

    # Określ nowy status
    if nowa_ilosc == 0:
        nowy_status = 'sprzedany'
    elif produkt['status'] == 'sprzedany':
        nowy_status = 'magazyn'
    else:
        nowy_status = produkt['status']

    # Zaktualizuj ilość i status
    conn.execute('UPDATE produkty SET ilosc = ?, status = ? WHERE id = ?',
                 (nowa_ilosc, nowy_status, produkt_id))

    conn.commit()

    flash(f'✅ Zaktualizowano ilość: {stara_ilosc} → {nowa_ilosc} szt.', 'success')
    return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')


@app.route('/produkt/oznacz-sprzedany/<int:produkt_id>')
def produkt_oznacz_sprzedany(produkt_id):
    """Oznacza produkt jako sprzedany BEZ dodawania do statystyk sprzedaży Allegro.
    Zmienia ilość produktu, zapisuje ile sprzedano offline i za ile.
    """
    from modules.database import get_db
    
    ilosc_sprzedana = request.args.get('ilosc', 1, type=int)
    _cena_raw = request.args.get('cena', '0').replace(',', '.')
    try:
        cena_sprzedazy = float(_cena_raw)
    except:
        cena_sprzedazy = 0.0
    przychod = ilosc_sprzedana * cena_sprzedazy
    
    print(f"📦 OFFLINE SALE: produkt={produkt_id}, ilosc={ilosc_sprzedana}, cena={cena_sprzedazy}, przychod={przychod}")
    print(f"   args: {dict(request.args)}")
    
    conn = get_db()
    
    # Dodaj kolumny OSOBNO jeśli nie istnieją
    try:
        conn.execute("SELECT sprzedano_offline FROM produkty LIMIT 1")
    except:
        try:
            conn.execute("ALTER TABLE produkty ADD COLUMN sprzedano_offline INTEGER DEFAULT 0")
            conn.commit()
            print("✅ Dodano kolumnę sprzedano_offline")
        except:
            pass
    
    try:
        conn.execute("SELECT przychod_offline FROM produkty LIMIT 1")
    except:
        try:
            conn.execute("ALTER TABLE produkty ADD COLUMN przychod_offline REAL DEFAULT 0")
            conn.commit()
            print("✅ Dodano kolumnę przychod_offline")
        except Exception as e:
            print(f"❌ Błąd dodawania przychod_offline: {e}")
    
    # Pobierz produkt
    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        flash('❌ Nie znaleziono produktu', 'error')
        return redirect(request.referrer or '/')
    
    stara_ilosc = produkt['ilosc'] or 1
    nowa_ilosc = max(0, stara_ilosc - ilosc_sprzedana)
    nowy_status = 'sprzedany' if nowa_ilosc == 0 else produkt['status']
    
    # Pobierz obecne wartości offline (mogą być NULL lub nie istnieć)
    try:
        obecne_szt_offline = produkt['sprzedano_offline'] or 0
    except:
        obecne_szt_offline = 0
    try:
        obecny_przychod_offline = produkt['przychod_offline'] or 0
    except:
        obecny_przychod_offline = 0
    
    nowe_szt_offline = obecne_szt_offline + ilosc_sprzedana
    nowy_przychod_offline = obecny_przychod_offline  # NIE aktualizuj - przychód trafia do sprzedaze

    print(f"📊 UPDATE: ilosc={nowa_ilosc}, status={nowy_status}, offline_szt={nowe_szt_offline}, offline_przychod={nowy_przychod_offline}")
    
    # Aktualizuj produkt - ilość, status, sprzedano_offline i przychod_offline
    try:
        conn.execute('''
            UPDATE produkty 
            SET ilosc = ?, status = ?, sprzedano_offline = ?, przychod_offline = ?
            WHERE id = ?
        ''', (nowa_ilosc, nowy_status, nowe_szt_offline, nowy_przychod_offline, produkt_id))
        print("✅ UPDATE wykonany z offline")
    except Exception as e:
        print(f"❌ UPDATE failed, fallback: {e}")
        # Fallback - tylko ilość i status
        conn.execute('''
            UPDATE produkty 
            SET ilosc = ?, status = ?
            WHERE id = ?
        ''', (nowa_ilosc, nowy_status, produkt_id))

    # ✅ KLUCZOWE: Dodaj rekord do sprzedaze żeby trafił do statystyk/dashboardu
    from datetime import datetime as _dt
    try:
        nazwa_prod = produkt['nazwa'] or f'Produkt #{produkt_id}'
        conn.execute('''
            INSERT INTO sprzedaze 
                (produkt_id, nazwa, cena, ilosc, status, data_sprzedazy, kupujacy, notified)
            VALUES (?, ?, ?, ?, 'sprzedana', ?, 'offline', 1)
        ''', (produkt_id, nazwa_prod, cena_sprzedazy, ilosc_sprzedana,
              _dt.now().strftime('%Y-%m-%dT%H:%M:%S')))
        print(f"✅ Dodano do sprzedaze: {nazwa_prod} × {ilosc_sprzedana} szt. × {cena_sprzedazy:.0f} zł = {przychod:.0f} zł")
    except Exception as e:
        print(f"❌ INSERT sprzedaze failed: {e}")

    try:
        conn.commit()
    except Exception as e:
        print(f"❌ COMMIT failed: {e}")
        flash(f'❌ Błąd zapisu do bazy: {e}', 'error')
        return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')

    if przychod > 0:
        flash(f'✅ Sprzedano offline: {ilosc_sprzedana} szt. × {cena_sprzedazy:.0f} zł = {przychod:.0f} zł', 'success')
    else:
        flash(f'✅ Sprzedano {ilosc_sprzedana} szt. (zostało: {nowa_ilosc})', 'success')

    return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')


@app.route('/produkt/cofnij-offline/<int:produkt_id>')
def produkt_cofnij_offline(produkt_id):
    """Cofa sprzedaż offline - zwraca produkty do magazynu."""
    from modules.database import get_db
    
    ilosc_do_cofniecia = request.args.get('ilosc', 1, type=int)
    
    conn = get_db()
    
    # Pobierz produkt
    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        flash('❌ Nie znaleziono produktu', 'error')
        return redirect(request.referrer or '/')
    
    # Pobierz obecne wartości offline
    try:
        obecne_szt_offline = produkt['sprzedano_offline'] or 0
    except:
        obecne_szt_offline = 0
    try:
        obecny_przychod_offline = produkt['przychod_offline'] or 0
    except:
        obecny_przychod_offline = 0
    
    if ilosc_do_cofniecia > obecne_szt_offline:
        flash(f'❌ Nie można cofnąć {ilosc_do_cofniecia} szt. - sprzedano tylko {obecne_szt_offline} szt. offline', 'error')
        return redirect(request.referrer or '/')
    
    # Oblicz nowe wartości
    nowe_szt_offline = obecne_szt_offline - ilosc_do_cofniecia
    
    # Proporcjonalnie zmniejsz przychód
    if obecne_szt_offline > 0:
        przychod_za_szt = obecny_przychod_offline / obecne_szt_offline
        nowy_przychod_offline = nowe_szt_offline * przychod_za_szt
    else:
        nowy_przychod_offline = 0
    
    # Zwiększ ilość w magazynie
    stara_ilosc = produkt['ilosc'] or 0
    nowa_ilosc = stara_ilosc + ilosc_do_cofniecia
    
    # Zmień status jeśli produkt miał status 'sprzedany' i był sprzedany tylko offline
    nowy_status = produkt['status']
    if produkt['status'] == 'sprzedany' and nowe_szt_offline == 0:
        nowy_status = 'wystawiony'  # Wróć do wystawionego
    
    # Aktualizuj produkt
    try:
        conn.execute('''
            UPDATE produkty
            SET ilosc = ?, status = ?, sprzedano_offline = ?, przychod_offline = ?
            WHERE id = ?
        ''', (nowa_ilosc, nowy_status, nowe_szt_offline, nowy_przychod_offline, produkt_id))
    except:
        conn.execute('''
            UPDATE produkty
            SET ilosc = ?, status = ?
            WHERE id = ?
        ''', (nowa_ilosc, nowy_status, produkt_id))

    # FIX: Aktualizuj też rekordy w tabeli sprzedaze (kupujacy='offline')
    # Bez tego cofnięcie pojedyncze nie działało — rekord sprzedaży dalej liczony w statystykach
    pozostalo_do_cofniecia = ilosc_do_cofniecia
    sprzedaze_offline = conn.execute('''
        SELECT id, ilosc FROM sprzedaze
        WHERE produkt_id = ? AND kupujacy = 'offline'
        AND COALESCE(status,'') NOT IN ('zwrot','anulowane','anulowana')
        ORDER BY id DESC
    ''', (produkt_id,)).fetchall()

    for s in sprzedaze_offline:
        if pozostalo_do_cofniecia <= 0:
            break
        s_ilosc = s['ilosc'] or 0
        if pozostalo_do_cofniecia >= s_ilosc:
            # Cofamy cały rekord
            conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('zwrot', s['id']))
            pozostalo_do_cofniecia -= s_ilosc
        else:
            # Cofamy częściowo — zmniejsz ilość w rekordzie
            conn.execute('UPDATE sprzedaze SET ilosc = ? WHERE id = ?', (s_ilosc - pozostalo_do_cofniecia, s['id']))
            pozostalo_do_cofniecia = 0

    conn.commit()

    flash(f'🔄 Cofnięto {ilosc_do_cofniecia} szt. ze sprzedaży offline (pozostało offline: {nowe_szt_offline})', 'success')

    return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')


@app.route('/produkt/cofnij-sprzedaz/<int:produkt_id>')
def produkt_cofnij_sprzedaz(produkt_id):
    """Cofa sprzedaż produktu - przywraca ilość i oznacza sprzedaże jako zwrot"""
    from modules.database import get_db

    conn = get_db()

    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        flash('❌ Nie znaleziono produktu', 'error')
        return redirect(request.referrer or '/')

    # Znajdź aktywne sprzedaże dla tego produktu
    sprzedaze = conn.execute('''
        SELECT id, ilosc, cena FROM sprzedaze
        WHERE produkt_id = ? AND COALESCE(status,'') NOT IN ('zwrot','anulowane','anulowana')
    ''', (produkt_id,)).fetchall()

    if not sprzedaze:
        flash('ℹ️ Brak sprzedaży do cofnięcia dla tego produktu', 'info')
        return redirect(request.referrer or '/')

    # Oblicz sumę cofanych sztuk
    cofniete_szt = sum(s['ilosc'] for s in sprzedaze)

    # Oznacz sprzedaże jako zwrot
    for s in sprzedaze:
        conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('zwrot', s['id']))

    # Przywróć ilość produktu i zmień status na magazyn
    nowa_ilosc = (produkt['ilosc'] or 0) + cofniete_szt
    conn.execute('UPDATE produkty SET ilosc = ?, status = ? WHERE id = ?',
                 (nowa_ilosc, 'magazyn', produkt_id))

    # Wyczyść offline stats jeśli istnieją
    try:
        conn.execute('UPDATE produkty SET sprzedano_offline = 0, przychod_offline = 0 WHERE id = ?', (produkt_id,))
    except:
        pass

    conn.commit()

    flash(f'🔄 Cofnięto sprzedaż: {cofniete_szt} szt. wraca do magazynu', 'success')
    return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')


# ============================================================
# SYSTEM WYSYŁEK - CHECKBOXY I BULK ACTIONS
# ============================================================

@app.route('/wysylki')
def wysylki_lista():
    """Lista zamówień do wysyłki z checkboxami (status='nowa') - GRUPOWANE PO ZAMÓWIENIU"""
    from modules.database import get_db
    from collections import defaultdict
    
    # Pobierz filtr użytkownika z parametru URL
    user_filter = request.args.get('user', '')
    
    conn = get_db()
    
    # Pobierz listę dostępnych użytkowników (dostawców)
    users = conn.execute('''
        SELECT DISTINCT p.dostawca 
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        WHERE s.status = 'nowa' AND p.dostawca IS NOT NULL AND p.dostawca != ''
        ORDER BY p.dostawca
    ''').fetchall()
    users_list = [u['dostawca'] for u in users]
    
    # Query z filtrem użytkownika - pobieramy też nazwę z oferty i sprzedaży
    if user_filter and user_filter != 'wszyscy':
        zamowienia = conn.execute('''
            SELECT s.*, 
                   COALESCE(p.nazwa, s.nazwa, 'Produkt') as produkt_nazwa, 
                   p.lokalizacja, p.dostawca, p.ean, p.asin,
                   COALESCE(o.tytul, s.nazwa, '') as oferta_tytul
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            LEFT JOIN oferty o ON s.oferta_id = o.id
            WHERE s.status = 'nowa' AND p.dostawca = ?
            ORDER BY s.allegro_order_id DESC, s.data_sprzedazy DESC
        ''', (user_filter,)).fetchall()
    else:
        zamowienia = conn.execute('''
            SELECT s.*, 
                   COALESCE(p.nazwa, s.nazwa, 'Produkt') as produkt_nazwa, 
                   p.lokalizacja, p.dostawca, p.ean, p.asin,
                   COALESCE(o.tytul, s.nazwa, '') as oferta_tytul
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            LEFT JOIN oferty o ON s.oferta_id = o.id
            WHERE s.status = 'nowa'
            ORDER BY s.allegro_order_id DESC, s.data_sprzedazy DESC
        ''').fetchall()
    
    
    # Grupuj zamówienia po allegro_order_id lub kupujacy+data
    grouped_orders = defaultdict(list)
    for z in zamowienia:
        # Klucz grupowania: allegro_order_id lub kupujacy+data (pierwsze 16 znaków)
        order_key = z['allegro_order_id'] or f"{z['kupujacy']}_{(z['data_sprzedazy'] or '')[:16]}"
        grouped_orders[order_key].append(z)
    
    # Buduj HTML z checkboxami - GRUPOWANE
    items_html = ''
    if len(zamowienia) == 0:
        items_html = '<div style="text-align:center;color:#64748b;padding:30px">🎉 Wszystkie zamówienia wysłane!</div>'
    else:
        for order_key, items in grouped_orders.items():
            first_item = items[0]
            
            # Zbierz wszystkie IDs do checkboxa
            all_ids = ','.join([str(z['id']) for z in items])
            
            # Oblicz łączną cenę i ilość
            total_price = sum(z['cena'] or 0 for z in items)
            total_qty = sum(z['ilosc'] or 1 for z in items)
            
            # Zbierz nazwy produktów
            product_names = []
            for z in items:
                nazwa = z['produkt_nazwa'] or z['oferta_tytul'] or 'Produkt'
                # Skróć nazwę ale zachowaj czytelność
                if len(nazwa) > 60:
                    nazwa = nazwa[:57] + '...'
                qty = z['ilosc'] or 1
                if qty > 1:
                    product_names.append(f"{nazwa} (x{qty})")
                else:
                    product_names.append(nazwa)
            
            # Jeśli wiele produktów - pokaż je osobno
            if len(items) > 1:
                products_display = '<br>'.join([f"• {n}" for n in product_names])
                badge = f'<span style="background:#f59e0b;color:#000;padding:2px 6px;border-radius:4px;font-size:0.7rem;font-weight:700;margin-left:8px">{len(items)} produkty</span>'
            else:
                products_display = product_names[0] if product_names else 'Produkt'
                badge = ''
            
            lokalizacja = first_item['lokalizacja'] or '—'
            dostawca = first_item['dostawca'] or 'Niezdefiniowany'
            code = first_item['ean'] or first_item['asin'] or '—'
            
            # Formatuj datę
            data_raw = first_item['data_sprzedazy'] or ''
            if 'T' in data_raw:
                data_str = data_raw[:16].replace('T', ' ')
            else:
                data_str = data_raw[:16]
            
            items_html += f'''
            <div style="display:flex;align-items:flex-start;background:#12121a;border:1px solid #1e1e2e;border-radius:10px;padding:12px;margin-bottom:8px">
                <label for="chk_{first_item['id']}" style="display:flex;align-items:flex-start;flex:1;cursor:pointer">
                    <input type="checkbox" id="chk_{first_item['id']}" name="ids" value="{all_ids}" 
                           style="width:20px;height:20px;margin-right:12px;margin-top:4px;cursor:pointer;accent-color:#22c55e">
                    <div style="flex:1;min-width:0">
                        <div style="font-weight:600;font-size:0.9rem;line-height:1.4">{products_display}{badge}</div>
                        <div style="font-size:0.75rem;color:#64748b;margin-top:4px">
                            📍 {lokalizacja} &nbsp;|&nbsp; 👤 {dostawca} &nbsp;|&nbsp; 🏷️ {code}
                        </div>
                        <div style="font-size:0.7rem;color:#64748b;margin-top:2px">
                            🛒 {first_item['kupujacy']} &nbsp;|&nbsp; 📅 {data_str}
                        </div>
                    </div>
                    <div style="text-align:right;margin-left:10px">
                        <div style="font-weight:700;color:#22c55e;font-size:1.1rem">{total_price:.0f} zł</div>
                        <div style="font-size:0.7rem;color:#64748b">x{total_qty}</div>
                    </div>
                </label>
                <div style="display:flex;flex-direction:column;gap:4px;margin-left:10px">
                    <a href="/wysylki/oznacz-wyslane?ids={all_ids}" style="padding:6px 10px;background:#22c55e;border-radius:6px;color:#fff;text-decoration:none;font-size:0.7rem;font-weight:600;text-align:center">✅ Wysłane</a>
                    <a href="https://allegro.pl/moje-allegro/sprzedaz/zamowienia/{first_item['allegro_order_id'] or ''}" target="_blank" style="padding:6px 10px;background:#3b82f6;border-radius:6px;color:#fff;text-decoration:none;font-size:0.7rem;text-align:center">Allegro</a>
                </div>
            </div>
            '''
    
    # Selektor użytkownika
    user_options = '<option value="wszyscy" ' + ('selected' if not user_filter or user_filter == 'wszyscy' else '') + '>👥 Wszyscy</option>'
    for user in users_list:
        selected = 'selected' if user_filter == user else ''
        user_options += f'<option value="{user}" {selected}>{user}</option>'
    
    user_selector = f'''
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:15px;background:#12121a;border:1px solid #1e1e2e;border-radius:10px;padding:12px">
        <label style="font-size:0.85rem;color:#94a3b8;font-weight:600">👤 UŻYTKOWNIK:</label>
        <select id="user-select" onchange="window.location.href='/wysylki?user=' + this.value" 
                style="flex:1;background:#1a1a24;border:1px solid #2a2a3a;color:#fff;padding:8px 12px;border-radius:8px;font-size:0.9rem;cursor:pointer">
            {user_options}
        </select>
    </div>
    '''
    
    # Liczba zamówień vs produktów
    orders_count = len(grouped_orders)
    products_count = len(zamowienia)
    count_info = f'{orders_count} zamówień' if orders_count != products_count else f'{orders_count} zamówień'
    if orders_count != products_count:
        count_info += f' <span style="font-size:0.75rem;color:#64748b">({products_count} produktów)</span>'
    
    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>📦 DO WYSYŁKI</h1>
            <small>Odhacz wysłane paczki (status: nowa)</small>
        </div>
        
        {user_selector}
        
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:15px">
            <a href="/wysylki/pakowanie" style="display:block;padding:12px;background:#f59e0b;border-radius:10px;color:#fff;text-decoration:none;text-align:center;font-weight:600">📱 Skanuj</a>
            <a href="/sync-miesiac" onclick="startSync(this)" style="display:block;padding:12px;background:#3b82f6;border-radius:10px;color:#fff;text-decoration:none;text-align:center;font-weight:600">🔄 Sync Allegro</a>
            <a href="/wysylki/allegro" style="display:block;padding:12px;background:#22c55e;border-radius:10px;color:#fff;text-decoration:none;text-align:center;font-weight:600">📦 Allegro Live</a>
            <a href="/wysylki/sync-stany" style="display:block;padding:12px;background:#8b5cf6;border-radius:10px;color:#fff;text-decoration:none;text-align:center;font-weight:600">📦 Sync Stany</a>
        </div>
        
        <form id="bulk-form" method="POST" action="/wysylki/bulk-wyslane">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;background:#12121a;border:1px solid #1e1e2e;border-radius:10px;padding:12px">
                <div>
                    <span style="font-size:1.5rem;font-weight:700;color:#eab308">{orders_count}</span>
                    <span style="font-size:0.85rem;color:#64748b;margin-left:8px">{count_info}</span>
                </div>
                <div style="display:flex;gap:8px">
                    <button type="button" onclick="selectAll()" 
                            style="background:#3b82f6;border:none;color:#fff;padding:8px 16px;border-radius:8px;font-size:0.85rem;cursor:pointer;font-weight:600">
                        ✓ Zaznacz wszystkie
                    </button>
                    <button type="submit" 
                            style="background:#22c55e;border:none;color:#fff;padding:8px 16px;border-radius:8px;font-size:0.85rem;cursor:pointer;font-weight:600">
                        ✈️ Oznacz jako wysłane
                    </button>
                </div>
            </div>
            
            {items_html}
        </form>
        
        <div style="margin-top:20px;text-align:center">
            <a href="/sprzedaze" style="color:#64748b;text-decoration:none;font-size:0.85rem">← Zobacz wszystkie sprzedaże</a>
        </div>
        <a href="/" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:10px">← Dashboard</a>
    </div>
    
    <style>@keyframes kspin{{to{{transform:rotate(360deg)}}}}</style>
    <script>
    function startSync(el) {{
        el.innerHTML = '<span style="display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,0.3);border-top-color:#fff;border-radius:50%;animation:kspin .6s linear infinite"></span> Sync...';
        el.style.opacity = '0.7';
    }}
    function selectAll() {{
        const checkboxes = document.querySelectorAll('input[name="ids"]');
        const allChecked = Array.from(checkboxes).every(cb => cb.checked);
        checkboxes.forEach(cb => cb.checked = !allChecked);
    }}
    
    // Prevent form submit if no checkboxes selected
    document.getElementById('bulk-form').addEventListener('submit', function(e) {{
        const checked = document.querySelectorAll('input[name="ids"]:checked');
        if (checked.length === 0) {{
            e.preventDefault();
            alert('Zaznacz przynajmniej jedno zamówienie!');
        }} else {{
            if (!confirm('Oznaczyć ' + checked.length + ' zamówień jako wysłane?')) {{
                e.preventDefault();
            }}
        }}
    }});
    </script>
    '''
    return html


@app.route('/wysylki/wyslano-order/<order_id>')
def wyslano_order(order_id):
    """Oznacza wszystkie produkty z danego zamówienia Allegro jako wysłane"""
    from modules.database import get_db
    
    conn = get_db()
    
    # Oznacz wszystkie sprzedaże z tym order_id jako wysłane
    result = conn.execute('''
        UPDATE sprzedaze SET status = 'wyslana' 
        WHERE allegro_order_id = ? AND status = 'nowa'
    ''', (order_id,))
    
    updated = result.rowcount
    conn.commit()
    
    flash(f'✅ Oznaczono {updated} produktów jako wysłane', 'success')
    return redirect('/wysylki/allegro')

@app.route('/wysylki/drukuj')
def wysylki_drukuj():
    """Strona druku listy pakowania ze zdjęciami (z cache)"""
    result, _ = _pobierz_zamowienia_allegro()
    zamowienia = result['zamowienia']
    produkty_cnt = result['produkty_cnt']
    wartosc = float(result['wartosc']) if result['wartosc'] else 0

    html = f'''<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Lista pakowania - {len(zamowienia)} zamówień</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, system-ui, Arial, sans-serif; font-size:12px; color:#000; background:#fff; padding:10px; }}
h1 {{ font-size:18px; text-align:center; margin-bottom:4px; }}
.summary {{ text-align:center; font-size:11px; color:#666; margin-bottom:12px; padding-bottom:8px; border-bottom:2px solid #000; }}
.order {{ display:flex; align-items:center; border:1px solid #ccc; border-radius:6px; margin-bottom:6px; overflow:hidden; page-break-inside:avoid; }}
.order-num {{ min-width:36px; background:#f0f0f0; display:flex; align-items:center; justify-content:center; font-size:16px; font-weight:700; padding:8px 4px; }}
.order-img {{ padding:6px; display:flex; flex-direction:column; gap:3px; }}
.order-img img {{ width:50px; height:50px; object-fit:contain; border:1px solid #ddd; border-radius:4px; background:#fff; }}
.order-info {{ flex:1; padding:6px 8px; }}
.order-info .name {{ font-weight:600; font-size:12px; margin-bottom:2px; }}
.order-info .addr {{ font-size:11px; color:#555; }}
.order-info .loc {{ display:inline-block; background:#666; color:#fff; padding:1px 6px; border-radius:3px; font-size:10px; font-weight:600; margin-top:3px; }}
.checkbox {{ width:16px; height:16px; border:2px solid #999; border-radius:3px; margin:0 6px; flex-shrink:0; }}
@media print {{
    body {{ padding:5px; }}
    .order {{ margin-bottom:4px; }}
}}
</style>
</head><body>
<h1>📦 LISTA PAKOWANIA</h1>
<div class="summary">{len(zamowienia)} zamówień · {produkty_cnt} produktów · {wartosc:.0f} zł · {datetime.now().strftime("%d.%m.%Y %H:%M")}</div>
'''

    for i, z in enumerate(zamowienia, 1):
        imgs_html = ''
        names_html = ''
        locs_html = ''
        for p in z['produkty']:
            img_src = p['zdjecie_url'] or 'https://via.placeholder.com/50'
            imgs_html += f'<img src="{img_src}" onerror="this.src=\'https://via.placeholder.com/50\'">'
            qty_str = f' <b>(×{p["qty"]})</b>' if p['qty'] > 1 else ''
            names_html += f'<div class="name">{p["name"][:60]}{qty_str}</div>'
            if p['lokalizacja']:
                locs_html += f'<span class="loc">📦 {p["lokalizacja"]}</span> '

        addr = z['pickup_point'] if z['pickup_point'] else z['address']

        html += f'''<div class="order">
    <div class="order-num">{i}</div>
    <div class="checkbox"></div>
    <div class="order-img">{imgs_html}</div>
    <div class="order-info">
        {names_html}
        <div class="addr">📍 {addr}</div>
        {locs_html}
    </div>
</div>
'''

    html += '''
<script>window.onload = function() { window.print(); }</script>
</body></html>'''

    return html

@app.route('/wysylki/bulk-wyslane-allegro', methods=['POST'])
def bulk_wyslane_allegro():
    """Bulk oznaczanie zamówień Allegro jako wysłane (z checkboxów)"""
    from modules.database import get_db

    order_ids = request.form.getlist('order_ids')

    if not order_ids:
        flash('Nie zaznaczono żadnych zamówień', 'error')
        return redirect('/wysylki/allegro')

    conn = get_db()
    total_updated = 0

    for order_id in order_ids:
        result = conn.execute('''
            UPDATE sprzedaze SET status = 'wyslana'
            WHERE allegro_order_id = ? AND status = 'nowa'
        ''', (order_id,))
        total_updated += result.rowcount

    conn.commit()

    flash(f'✅ Wysłano {len(order_ids)} zamówień ({total_updated} produktów)', 'success')
    return redirect('/wysylki/allegro')


@app.route('/wysylki/oznacz-wyslane')
def oznacz_wyslane_pojedyncze():
    """Oznacza pojedyncze zamówienie jako wysłane (z GET)"""
    from modules.database import get_db
    
    ids_raw = request.args.get('ids', '')
    
    if not ids_raw:
        return redirect('/wysylki')
    
    # Rozdziel comma-separated IDs
    all_ids = []
    for single_id in ids_raw.split(','):
        single_id = single_id.strip()
        if single_id and single_id.isdigit():
            all_ids.append(int(single_id))
    
    if not all_ids:
        return redirect('/wysylki')
    
    conn = get_db()
    
    # Zmień status na 'wyslana'
    placeholders = ','.join(['?' for _ in all_ids])
    conn.execute(f'UPDATE sprzedaze SET status = "wyslana" WHERE id IN ({placeholders})', all_ids)
    conn.commit()
    
    return redirect('/wysylki')


@app.route('/wysylki/sync-stany')
def sync_stany_magazynowe():
    """Synchronizuje stany magazynowe - aktualizuje ilości produktów na podstawie sprzedaży"""
    from modules.database import get_db
    
    conn = get_db()
    
    # Znajdź sprzedaże które mają produkt_id ale stan może być nieaktualny
    # Dla każdego produktu oblicz ile powinno być na stanie
    produkty_do_aktualizacji = conn.execute('''
        SELECT p.id, p.nazwa, p.ilosc as aktualna_ilosc,
               COALESCE(SUM(s.ilosc), 0) as sprzedano,
               (SELECT COALESCE(SUM(ilosc_oryginalna), p.ilosc + COALESCE(SUM(s.ilosc), 0)) 
                FROM produkty WHERE id = p.id) as ilosc_oryginalna
        FROM produkty p
        LEFT JOIN sprzedaze s ON s.produkt_id = p.id AND s.status != 'anulowana' AND s.status != 'zwrot'
        GROUP BY p.id
        HAVING sprzedano > 0
    ''').fetchall()
    
    updated = 0
    for prod in produkty_do_aktualizacji:
        # Oblicz poprawną ilość = oryginalna - sprzedano
        # Problem: nie mamy ilosc_oryginalna, więc użyjemy aktualna + sprzedano jako "oryginalna"
        # i sprawdzimy czy aktualna jest poprawna
        pass
    
    # Prostsze podejście - znajdź sprzedaże bez połączenia z produktem i spróbuj połączyć
    sprzedaze_bez_produktu = conn.execute('''
        SELECT s.id, s.nazwa, s.allegro_order_id
        FROM sprzedaze s
        WHERE s.produkt_id IS NULL AND s.nazwa IS NOT NULL AND s.nazwa != ''
        LIMIT 100
    ''').fetchall()
    
    polaczone = 0
    for s in sprzedaze_bez_produktu:
        # Szukaj produktu po nazwie (pierwsze 30 znaków)
        nazwa_szukaj = (s['nazwa'] or '')[:30].lower()
        if len(nazwa_szukaj) < 5:
            continue
            
        produkt = conn.execute('''
            SELECT id FROM produkty 
            WHERE LOWER(nazwa) LIKE ? 
            LIMIT 1
        ''', (f'%{nazwa_szukaj}%',)).fetchone()
        
        if produkt:
            conn.execute('UPDATE sprzedaze SET produkt_id = ? WHERE id = ?', (produkt['id'], s['id']))
            polaczone += 1
    
    # Teraz przelicz stany dla wszystkich produktów z nowymi sprzedażami
    # Podejście: dla każdego produktu ze sprzedażą, zmniejsz ilość o ile sprzedano
    produkty_ze_sprzedaza = conn.execute('''
        SELECT p.id, p.nazwa, p.ilosc, 
               COALESCE((SELECT SUM(s.ilosc) FROM sprzedaze s 
                         WHERE s.produkt_id = p.id 
                         AND s.status NOT IN ('anulowana', 'zwrot')
                         AND s.id NOT IN (SELECT id FROM sprzedaze WHERE produkt_id = p.id AND przeliczone = 1)), 0) as nowe_sprzedaze
        FROM produkty p
        WHERE EXISTS (SELECT 1 FROM sprzedaze s WHERE s.produkt_id = p.id AND s.status NOT IN ('anulowana', 'zwrot'))
    ''').fetchall()
    
    # Sprawdź czy kolumna 'przeliczone' istnieje
    try:
        conn.execute('ALTER TABLE sprzedaze ADD COLUMN przeliczone INTEGER DEFAULT 0')
    except:
        pass
    
    # Aktualizuj stany
    for prod in produkty_ze_sprzedaza:
        if prod['nowe_sprzedaze'] and prod['nowe_sprzedaze'] > 0:
            new_qty = max(0, prod['ilosc'] - prod['nowe_sprzedaze'])
            conn.execute('''
                UPDATE produkty SET 
                    ilosc = ?,
                    status = CASE WHEN ? = 0 THEN 'sprzedany' ELSE status END
                WHERE id = ?
            ''', (new_qty, new_qty, prod['id']))
            
            # Oznacz sprzedaże jako przeliczone
            conn.execute('''
                UPDATE sprzedaze SET przeliczone = 1 
                WHERE produkt_id = ? AND status NOT IN ('anulowana', 'zwrot')
            ''', (prod['id'],))
            
            updated += 1
            print(f"📦 Stock: {prod['nazwa'][:30]} ({prod['ilosc']} -> {new_qty})")
    
    conn.commit()
    
    flash(f'✅ Zaktualizowano {updated} produktów, połączono {polaczone} sprzedaży', 'success')
    return redirect('/wysylki')


@app.route('/wysylki/bulk-wyslane', methods=['POST'])
def bulk_oznacz_wyslane():
    """Bulk update - oznacza zaznaczone zamówienia jako wysłane (obsługuje zgrupowane zamówienia)"""
    from modules.database import get_db
    
    raw_ids = request.form.getlist('ids')
    
    if not raw_ids:
        return redirect('/wysylki')
    
    # Rozdziel comma-separated IDs (dla zgrupowanych zamówień)
    all_ids = []
    for id_group in raw_ids:
        for single_id in id_group.split(','):
            single_id = single_id.strip()
            if single_id and single_id.isdigit():
                all_ids.append(int(single_id))
    
    if not all_ids:
        return redirect('/wysylki')
    
    conn = get_db()
    
    # Zmień status na 'wyslana' dla zaznaczonych
    placeholders = ','.join(['?' for _ in all_ids])
    conn.execute(f'UPDATE sprzedaze SET status = "wyslana" WHERE id IN ({placeholders})', all_ids)
    conn.commit()
    
    # Success message - pokazuj liczbę produktów
    return f'''
    <html><head><meta http-equiv="refresh" content="2;url=/wysylki"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px">✈️</div>
            <div style="font-size:1.2rem">Oznaczono {len(all_ids)} produktów jako wysłane!</div>
            <div style="color:#64748b;margin-top:10px">Przekierowuję...</div>
        </div>
    </body></html>
    '''


@app.route('/ustawienia/save', methods=['POST'])
def ustawienia_save():
    from modules.database import set_config
    
    base_url = request.form.get('app_base_url', 'http://localhost:5000').strip()
    # Usuń trailing slash
    base_url = base_url.rstrip('/')
    
    set_config('app_base_url', base_url)
    
    return redirect('/ustawienia')


@app.route('/ustawienia/modules', methods=['POST'])
def ustawienia_modules():
    """Zapisuje włączone/wyłączone moduły"""
    from modules.database import set_config, invalidate_config_cache
    module_names = ['paletomat', 'magazynier', 'allegro', 'olx', 'vinted', 'telegram']
    for name in module_names:
        val = '1' if request.form.get(f'module_{name}') else '0'
        set_config(f'module_{name}', val)
    invalidate_config_cache()
    return redirect('/ustawienia')


@app.route('/ustawienia/branding', methods=['POST'])
def ustawienia_branding():
    """Zapisuje branding + logo"""
    from modules.database import set_config, invalidate_config_cache
    brand_name = request.form.get('brand_name', 'AKCES HUB').strip()
    brand_color = request.form.get('brand_color', '#6366f1').strip()
    set_config('brand_name', brand_name)
    set_config('brand_color', brand_color)

    # Logo upload
    logo = request.files.get('brand_logo')
    if logo and logo.filename:
        ext = logo.filename.rsplit('.', 1)[-1].lower()
        if ext in ('png', 'jpg', 'jpeg'):
            from PIL import Image
            import io
            img_data = logo.read()
            if len(img_data) <= 512 * 1024:  # max 500KB
                img = Image.open(io.BytesIO(img_data))
                # Resize if too large (max 200px height)
                if img.height > 200:
                    ratio = 200 / img.height
                    img = img.resize((int(img.width * ratio), 200), Image.LANCZOS)
                logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'brand_logo.png')
                img.save(logo_path, 'PNG', optimize=True)

    invalidate_config_cache()
    return redirect('/ustawienia')


@app.route('/admin/update-git', methods=['POST'])
def admin_update_git():
    """Aktualizacja systemu — git pull + pip install + restart"""
    import subprocess
    from html import escape

    if session.get('rola') != 'admin':
        return 'Brak uprawnien', 403

    page_style = 'background:#0a0a0f;color:#e2e8f0;font-family:monospace;padding:40px;white-space:pre-wrap'
    logs = []
    back = '<a href="/ustawienia" style="color:#818cf8;text-decoration:none">← Powrot do ustawien</a>'

    try:
        app_dir = os.path.dirname(os.path.abspath(__file__))

        # 1. Backup bazy
        logs.append('[1/4] Backup bazy...')
        db_path = os.path.join(app_dir, 'akces_hub.db')
        if os.path.exists(db_path):
            import sqlite3 as sq
            from datetime import datetime as dt
            backup_dir = os.path.join(app_dir, 'backups')
            os.makedirs(backup_dir, exist_ok=True)
            ts = dt.now().strftime('%Y%m%d_%H%M%S')
            bp = os.path.join(backup_dir, f'pre_update_{ts}.db')
            src = sq.connect(db_path)
            dst = sq.connect(bp)
            src.backup(dst)
            dst.close()
            src.close()
            logs.append(f'  -> Backup OK ({os.path.getsize(bp)/1024/1024:.1f} MB)')
        else:
            logs.append('  -> Brak bazy')

        # 2. Git pull
        logs.append('[2/4] Git pull...')
        if not os.path.isdir(os.path.join(app_dir, '.git')):
            logs.append('  -> Brak repo git. Inicjalizuje...')
            subprocess.run(['git', 'init'], cwd=app_dir, capture_output=True, timeout=10)
            subprocess.run(['git', 'remote', 'add', 'origin', 'https://github.com/Trupson2/akces-hub.git'],
                         cwd=app_dir, capture_output=True, timeout=10)

        r = subprocess.run(['git', 'pull', '--ff-only', 'origin', 'main'],
                          cwd=app_dir, capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            logs.append(f'  -> {r.stdout.strip()}')
        else:
            # Try with reset if ff-only fails
            subprocess.run(['git', 'fetch', 'origin'], cwd=app_dir, capture_output=True, timeout=60)
            r2 = subprocess.run(['git', 'reset', '--hard', 'origin/main'],
                               cwd=app_dir, capture_output=True, text=True, timeout=30)
            if r2.returncode == 0:
                logs.append(f'  -> Reset do origin/main OK')
            else:
                logs.append(f'  -> Git error: {r.stderr[:200]}')

        # 3. Pip install
        logs.append('[3/4] Pip install...')
        req = os.path.join(app_dir, 'requirements.txt')
        venv_pip = os.path.join(app_dir, 'venv', 'bin', 'pip')
        if os.path.exists(req) and os.path.exists(venv_pip):
            r = subprocess.run([venv_pip, 'install', '-r', req, '--quiet'],
                              capture_output=True, text=True, timeout=120)
            logs.append('  -> OK' if r.returncode == 0 else f'  -> {r.stderr[:100]}')
        else:
            logs.append('  -> Pomijam')

        # 4. Restart
        logs.append('[4/4] Restart Flask...')
        r = subprocess.run(['sudo', 'systemctl', 'restart', 'akceshub.service'],
                          capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            logs.append('  -> OK!')
        else:
            logs.append(f'  -> {r.stderr[:200]}')

        logs.append('')
        logs.append('AKTUALIZACJA ZAKONCZONA!')

        content = escape('\n'.join(logs))
        return f'''<html><head><meta charset="UTF-8"></head>
        <body style="{page_style}">
        <h2 style="color:#22c55e">Aktualizacja zakonczona!</h2>
        <pre style="font-size:0.85rem">{content}</pre>
        <p style="color:#94a3b8;margin-top:20px">Strona moze byc niedostepna przez kilka sekund po restarcie.</p>
        {back}
        </body></html>'''

    except Exception as e:
        logs.append(f'BLAD: {e}')
        content = escape('\n'.join(logs))
        return f'''<html><head><meta charset="UTF-8"></head>
        <body style="{page_style}">
        <h2 style="color:#ef4444">Blad aktualizacji</h2>
        <pre style="font-size:0.85rem">{content}</pre>
        {back}
        </body></html>'''


@app.route('/admin/update', methods=['POST'])
def admin_update():
    """Aktualizacja systemu — upload ZIP + backup + rozpakowanie + restart"""
    import subprocess, zipfile, shutil
    from html import escape

    if session.get('rola') != 'admin':
        return 'Brak uprawnien', 403

    page_style = 'background:#0a0a0f;color:#e2e8f0;font-family:monospace;padding:40px;white-space:pre-wrap'
    logs = []

    def page(title, color, extra=''):
        content = escape('\n'.join(logs))
        back = '<a href="/ustawienia" style="color:#818cf8;text-decoration:none">← Powrot do ustawien</a>'
        return f'''<html><head><meta charset="UTF-8"></head>
        <body style="{page_style}">
        <h2 style="color:{color}">{title}</h2>
        <pre style="font-size:0.85rem">{content}</pre>
        {extra}
        {back}
        </body></html>'''

    try:
        f = request.files.get('update_zip')
        if not f or not f.filename.endswith('.zip'):
            return page('Blad: Wybierz plik ZIP', '#ef4444')

        app_dir = os.path.dirname(os.path.abspath(__file__))
        tmp_dir = os.path.join(app_dir, '_update_tmp')
        skip_patterns = {'venv', 'backups', '__pycache__', '.git', 'akces_hub.db',
                         'cloud_exports', '_update_tmp', 'node_modules'}

        # 1. Backup bazy
        logs.append('[1/4] Backup bazy danych...')
        db_path = os.path.join(app_dir, 'akces_hub.db')
        if os.path.exists(db_path):
            backup_dir = os.path.join(app_dir, 'backups')
            os.makedirs(backup_dir, exist_ok=True)
            import sqlite3 as sq
            from datetime import datetime as dt
            ts = dt.now().strftime('%Y%m%d_%H%M%S')
            backup_path = os.path.join(backup_dir, f'pre_update_{ts}.db')
            src = sq.connect(db_path)
            dst = sq.connect(backup_path)
            src.backup(dst)
            dst.close()
            src.close()
            size_mb = os.path.getsize(backup_path) / (1024 * 1024)
            logs.append(f'  -> {backup_path} ({size_mb:.1f} MB)')
        else:
            logs.append('  -> Brak bazy (nowa instalacja?)')

        # 2. Rozpakuj ZIP do temp
        logs.append('[2/4] Rozpakowywanie ZIP...')
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
        os.makedirs(tmp_dir)

        f.save(os.path.join(tmp_dir, 'update.zip'))
        with zipfile.ZipFile(os.path.join(tmp_dir, 'update.zip'), 'r') as zf:
            zf.extractall(tmp_dir)
        os.remove(os.path.join(tmp_dir, 'update.zip'))

        # Znajdz root folder w ZIP (moze byc nested)
        extracted = os.listdir(tmp_dir)
        src_dir = tmp_dir
        if len(extracted) == 1 and os.path.isdir(os.path.join(tmp_dir, extracted[0])):
            src_dir = os.path.join(tmp_dir, extracted[0])

        # Sprawdz czy to prawidlowa paczka (ma app.py?)
        if not os.path.exists(os.path.join(src_dir, 'app.py')):
            shutil.rmtree(tmp_dir)
            logs.append('  -> BLAD: Paczka nie zawiera app.py!')
            return page('Blad: Nieprawidlowa paczka', '#ef4444')

        logs.append(f'  -> Rozpakowano ({len(os.listdir(src_dir))} plikow/folderow)')

        # 3. Kopiuj pliki (pomijaj venv, backups, db, __pycache__)
        logs.append('[3/4] Kopiowanie plikow...')
        updated = 0
        for root, dirs, files in os.walk(src_dir):
            # Filtruj katalogi
            dirs[:] = [d for d in dirs if d not in skip_patterns and not d.endswith('.pyc')]
            rel = os.path.relpath(root, src_dir)

            for fname in files:
                if fname.endswith(('.pyc', '.db')) or fname in skip_patterns:
                    continue
                src_file = os.path.join(root, fname)
                if rel == '.':
                    dst_file = os.path.join(app_dir, fname)
                else:
                    dst_dir = os.path.join(app_dir, rel)
                    os.makedirs(dst_dir, exist_ok=True)
                    dst_file = os.path.join(dst_dir, fname)
                shutil.copy2(src_file, dst_file)
                updated += 1

        logs.append(f'  -> Skopiowano {updated} plikow')

        # Cleanup tmp
        shutil.rmtree(tmp_dir, ignore_errors=True)

        # 4. Restart Flask
        logs.append('[4/4] Restart serwisu...')
        try:
            result = subprocess.run(
                ['sudo', 'systemctl', 'restart', 'akceshub.service'],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                logs.append('  -> Flask zrestartowany!')
            else:
                logs.append(f'  -> Restart blad: {result.stderr[:200]}')
                logs.append('  -> Sprobuj recznie: sudo systemctl restart akceshub.service')
        except Exception as e:
            logs.append(f'  -> Nie mozna zrestartowac automatycznie: {e}')
            logs.append('  -> Po odswiezeniu strona moze byc niedostepna przez chwile')

        logs.append('')
        logs.append('AKTUALIZACJA ZAKONCZONA!')

        return page('Aktualizacja zakonczona!', '#22c55e',
                     '<p style="color:#94a3b8;margin-top:20px">Strona moze byc niedostepna przez kilka sekund po restarcie.</p>')

    except Exception as e:
        logs.append(f'BLAD: {e}')
        # Cleanup on error
        tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_update_tmp')
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return page(f'Blad aktualizacji', '#ef4444')


@app.route('/ustawienia/ngrok-token', methods=['POST'])
def ustawienia_ngrok_token():
    from modules.database import set_config
    token = request.form.get('ngrok_token', '').strip()
    domain = request.form.get('ngrok_domain', '').strip()
    if token:
        set_config('ngrok_auth_token', token)
    set_config('ngrok_domain', domain)
    return redirect('/ustawienia')


@app.route('/ustawienia/email', methods=['POST'])
def ustawienia_email():
    """Zapisuje konfigurację email"""
    from modules.email_reports import get_email_config, save_email_config
    
    config = get_email_config()
    
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '').strip()
    recipient = request.form.get('recipient', '').strip()
    enabled = 'enabled' in request.form
    
    config['email'] = email
    if password:  # Tylko jeśli wpisano nowe hasło
        config['password'] = password
    config['recipient'] = recipient
    config['enabled'] = enabled
    
    save_email_config(config)
    
    return redirect('/ustawienia')


@app.route('/raport/podglad')
def raport_podglad():
    """Podgląd raportu tygodniowego"""
    from modules.email_reports import generate_weekly_report
    html = generate_weekly_report()
    return html

@app.route('/raport/dzienny')
def raport_dzienny_podglad():
    """Podgląd raportu dziennego z analiza palet"""
    from modules.email_reports import generate_daily_report
    return generate_daily_report()

@app.route('/raport/dzienny/wyslij')
def raport_dzienny_wyslij():
    """Wysyła raport dzienny na email"""
    from modules.email_reports import send_daily_report
    success, msg = send_daily_report()
    color = '#22c55e' if success else '#ef4444'
    icon = 'Wyslano!' if success else f'Blad: {msg}'
    return f'<html><body style="background:#0a0a0f;color:{color};font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh"><div>{icon}</div></body></html>'


@app.route('/raport/wyslij')
def raport_wyslij():
    """Wysyła raport tygodniowy na email"""
    from modules.email_reports import send_weekly_report, get_email_config
    
    config = get_email_config()
    
    if not config.get('enabled'):
        return '''
        <html><head><meta http-equiv="refresh" content="3;url=/ustawienia"></head>
        <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
            <div style="text-align:center">
                <div style="font-size:3rem;margin-bottom:20px">⚠️</div>
                <div style="font-size:1.2rem;color:#f59e0b">Email nie jest włączony!</div>
                <div style="color:#64748b;margin-top:10px">Włącz w ustawieniach</div>
            </div>
        </body></html>
        '''
    
    success, msg = send_weekly_report()
    
    if success:
        return f'''
        <html><head><meta http-equiv="refresh" content="3;url=/ustawienia"></head>
        <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
            <div style="text-align:center">
                <div style="font-size:3rem;margin-bottom:20px">✅</div>
                <div style="font-size:1.2rem">Raport wysłany!</div>
                <div style="color:#64748b;margin-top:10px">Sprawdź email: {config.get('recipient') or config.get('email')}</div>
            </div>
        </body></html>
        '''
    else:
        return f'''
        <html><head><meta http-equiv="refresh" content="5;url=/ustawienia"></head>
        <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
            <div style="text-align:center">
                <div style="font-size:3rem;margin-bottom:20px">❌</div>
                <div style="font-size:1.2rem;color:#ef4444">Błąd wysyłania!</div>
                <div style="color:#64748b;margin-top:10px;max-width:400px">{msg}</div>
            </div>
        </body></html>
        '''


@app.route('/ustawienia/reset-sprzedaze', methods=['POST'])
def reset_sprzedaze():
    """Czyści historię sprzedaży"""
    if session.get('rola') != 'admin':
        return 'Brak uprawnień (tylko admin)', 403
    from modules.database import get_db
    conn = get_db()
    conn.execute('DELETE FROM sprzedaze')
    conn.commit()
    
    return '''
    <html><head><meta http-equiv="refresh" content="2;url=/ustawienia"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px">✅</div>
            <div style="font-size:1.2rem">Historia sprzedaży wyczyszczona!</div>
            <div style="color:#64748b;margin-top:10px">Przekierowuję...</div>
        </div>
    </body></html>
    '''


@app.route('/ustawienia/reset-magazyn', methods=['POST'])
def reset_magazyn():
    """Czyści wszystkie produkty z magazynu"""
    if session.get('rola') != 'admin':
        return 'Brak uprawnień (tylko admin)', 403
    from modules.database import get_db
    conn = get_db()
    cnt = conn.execute('SELECT COUNT(*) FROM produkty').fetchone()[0]
    conn.execute('DELETE FROM produkty')
    conn.commit()
    
    return f'''
    <html><head><meta http-equiv="refresh" content="2;url=/ustawienia"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px">✅</div>
            <div style="font-size:1.2rem">Magazyn wyczyszczony!</div>
            <div style="color:#64748b;margin-top:10px">Usunięto {cnt} produktów</div>
        </div>
    </body></html>
    '''


@app.route('/ustawienia/reset-palety', methods=['POST'])
def reset_palety():
    """Czyści wszystkie palety i powiązane produkty (także ze scraped)"""
    if session.get('rola') != 'admin':
        return 'Brak uprawnień (tylko admin)', 403
    from modules.database import get_db
    conn = get_db()
    palety_cnt = conn.execute('SELECT COUNT(*) FROM palety').fetchone()[0]
    produkty_cnt = conn.execute('SELECT COUNT(*) FROM produkty WHERE paleta_id IS NOT NULL').fetchone()[0]
    
    # NOWE: Pobierz ASINy produktów z palet
    asiny = conn.execute('SELECT DISTINCT asin FROM produkty WHERE paleta_id IS NOT NULL AND asin != ""').fetchall()
    asiny_list = [row[0] for row in asiny if row[0]]
    
    # NOWE: Usuń te produkty ze scraped (Paletomat)
    scraped_cnt = 0
    if asiny_list:
        placeholders = ','.join(['?' for _ in asiny_list])
        scraped_cnt = conn.execute(f'DELETE FROM scraped WHERE asin IN ({placeholders})', asiny_list).rowcount
    
    # Usuń produkty i palety
    conn.execute('DELETE FROM produkty WHERE paleta_id IS NOT NULL')
    conn.execute('DELETE FROM palety')
    conn.commit()
    
    return f'''
    <html><head><meta http-equiv="refresh" content="3;url=/ustawienia"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px">✅</div>
            <div style="font-size:1.2rem">Palety wyczyszczone!</div>
            <div style="color:#64748b;margin-top:10px">
                Usunięto {palety_cnt} palet, {produkty_cnt} produktów z magazynu
                {f' i {scraped_cnt} produktów ze scraped' if scraped_cnt > 0 else ''}
            </div>
        </div>
    </body></html>
    '''


@app.route('/ustawienia/reset-scraped', methods=['POST'])
def reset_scraped():
    """Czyści zescrapowane produkty z Palatomatu"""
    if session.get('rola') != 'admin':
        return 'Brak uprawnień (tylko admin)', 403
    from modules.database import get_db
    conn = get_db()
    cnt = conn.execute('SELECT COUNT(*) FROM scraped').fetchone()[0]
    conn.execute('DELETE FROM scraped')
    conn.commit()
    
    return f'''
    <html><head><meta http-equiv="refresh" content="2;url=/ustawienia"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px">✅</div>
            <div style="font-size:1.2rem">Scraped wyczyszczony!</div>
            <div style="color:#64748b;margin-top:10px">Usunięto {cnt} zescrapowanych produktów</div>
        </div>
    </body></html>
    '''


# ============================================================
# STATYSTYKI DASHBOARD (zunifikowany panel)
# ============================================================
@app.route('/statystyki')
def statystyki():
    from modules.database import get_full_stats, get_palety_list, get_db
    import json

    stats = get_full_stats()

    # Pobierz dane miesięczne do wykresu (przychód bez zwrotów)
    current_year = datetime.now().year
    conn = get_db()
    miesieczne = conn.execute('''
        SELECT strftime('%m', REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) as miesiac,
               COALESCE(SUM(CASE WHEN status != 'zwrot' THEN cena * ilosc ELSE 0 END), 0) as suma,
               COUNT(*) as cnt
        FROM sprzedaze
        WHERE strftime('%Y', REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) = ?
          AND data_sprzedazy IS NOT NULL AND data_sprzedazy != ''
          AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
          AND (kupujacy IS NULL OR kupujacy != 'offline')
        GROUP BY miesiac
        HAVING miesiac IS NOT NULL
        ORDER BY miesiac
    ''', (str(current_year),)).fetchall()

    nazwy_miesiecy = ['Sty', 'Lut', 'Mar', 'Kwi', 'Maj', 'Cze', 'Lip', 'Sie', 'Wrz', 'Paz', 'Lis', 'Gru']
    dane_miesieczne = [0] * 12
    dane_zamowienia = [0] * 12
    for m in miesieczne:
        if m['miesiac'] is None:
            continue
        idx = int(m['miesiac']) - 1
        dane_miesieczne[idx] = float(m['suma'] or 0)
        dane_zamowienia[idx] = int(m['cnt'] or 0)

    chart_labels = json.dumps(nazwy_miesiecy)
    chart_data = json.dumps(dane_miesieczne)
    chart_orders = json.dumps(dane_zamowienia)

    # TOP produkty i dostawcy
    top_produkty = stats.get('top_produkty', [])
    top_dostawcy = stats.get('top_dostawcy', [])

    # Aktualny miesiąc
    _nazwy_mies = {1:'Styczeń',2:'Luty',3:'Marzec',4:'Kwiecień',5:'Maj',6:'Czerwiec',7:'Lipiec',8:'Sierpień',9:'Wrzesień',10:'Październik',11:'Listopad',12:'Grudzień'}
    miesiac = f"{_nazwy_mies[datetime.now().month]} {datetime.now().year}"

    # TOP produkty HTML
    top_prod_html = ''
    for i, p in enumerate(top_produkty[:5]):
        border = 'border-bottom:1px solid #1e1e2e;' if i < min(len(top_produkty), 5) - 1 else ''
        img = p.get('zdjecie_url') or 'https://via.placeholder.com/40'
        nazwa = p['nazwa'][:40] + ('...' if len(p['nazwa']) > 40 else '')
        top_prod_html += f'''<div style="display:flex;align-items:center;gap:10px;padding:8px 0;{border}">
            <div style="font-weight:700;color:#f59e0b;width:20px">{i+1}.</div>
            <img src="{img}" style="width:40px;height:40px;border-radius:8px;object-fit:cover">
            <div style="flex:1;min-width:0">
                <div style="font-size:0.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{nazwa}</div>
                <div style="font-size:0.75rem;color:#64748b">{p['sprzedazy_cnt']} szt</div>
            </div>
            <div style="font-weight:600;color:#22c55e">{p['sprzedazy_suma']:.0f} zl</div>
        </div>'''

    # TOP dostawcy HTML
    top_dost_html = ''
    for i, d in enumerate(top_dostawcy[:5]):
        border = 'border-bottom:1px solid #1e1e2e;' if i < min(len(top_dostawcy), 5) - 1 else ''
        roi_color = '#22c55e' if d['roi'] > 50 else ('#eab308' if d['roi'] > 20 else '#ef4444')
        top_dost_html += f'''<div style="display:flex;align-items:center;gap:10px;padding:8px 0;{border}">
            <div style="font-weight:700;color:#f59e0b;width:20px">{i+1}</div>
            <div style="flex:1">
                <div style="font-weight:600">{d['dostawca']}</div>
                <div style="font-size:0.75rem;color:#64748b">{d['sprzedazy_cnt']} szt | {d['przychod']:.0f} zl przychod</div>
            </div>
            <div style="text-align:right">
                <div style="font-weight:700;color:{roi_color}">{d['roi']:.0f}%</div>
                <div style="font-size:0.7rem;color:#64748b">koszt: {d['koszt']:.0f} zl</div>
            </div>
        </div>'''

    pryw_info = f' (W TYM {int(stats.get("sprzedaz_lacznie_pryw_suma",0))} ZL PRYWATNE)' if stats.get('sprzedaz_lacznie_pryw_suma',0) > 0 else ''

    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>📊 STATYSTYKI</h1>
            <small>Pelny przeglad biznesu</small>
        </div>

        <!-- TABS -->
        <div style="display:flex;gap:4px;margin-bottom:15px;overflow-x:auto;-webkit-overflow-scrolling:touch">
            <button class="stat-tab active" onclick="showTab('dzis')" id="tab-dzis" style="flex:1;padding:10px 6px;border:none;border-radius:10px;font-weight:600;font-size:0.8rem;cursor:pointer;background:#22c55e;color:#fff;white-space:nowrap">DZIS</button>
            <button class="stat-tab" onclick="showTab('miesiac')" id="tab-miesiac" style="flex:1;padding:10px 6px;border:none;border-radius:10px;font-weight:600;font-size:0.8rem;cursor:pointer;background:#1e1e2e;color:#64748b;white-space:nowrap">MIESIAC</button>
            <button class="stat-tab" onclick="showTab('magazyn')" id="tab-magazyn" style="flex:1;padding:10px 6px;border:none;border-radius:10px;font-weight:600;font-size:0.8rem;cursor:pointer;background:#1e1e2e;color:#64748b;white-space:nowrap">MAGAZYN</button>
            <button class="stat-tab" onclick="showTab('alltime')" id="tab-alltime" style="flex:1;padding:10px 6px;border:none;border-radius:10px;font-weight:600;font-size:0.8rem;cursor:pointer;background:#1e1e2e;color:#64748b;white-space:nowrap">LACZNIE</button>
            <button class="stat-tab" onclick="showTab('top')" id="tab-top" style="flex:1;padding:10px 6px;border:none;border-radius:10px;font-weight:600;font-size:0.8rem;cursor:pointer;background:#1e1e2e;color:#64748b;white-space:nowrap">TOP</button>
        </div>


        <!-- TAB: DZIŚ -->
        <div id="panel-dzis" class="stat-panel">
            <div style="background:linear-gradient(135deg,rgba(34,197,94,0.15),rgba(16,185,129,0.1));border:1px solid rgba(34,197,94,0.3);border-radius:16px;padding:18px;margin-bottom:15px">
                <div style="color:#22c55e;font-weight:600;font-size:1.1rem;margin-bottom:12px">📅 DZIS ({datetime.now().strftime('%d.%m.%Y')})</div>
                <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px">
                    <div style="text-align:center">
                        <div style="font-size:2rem;font-weight:700;color:#22c55e">{stats['sprzedaz_dzis_cnt']}</div>
                        <div style="font-size:0.75rem;color:#64748b">ZAMOWIEN</div>
                    </div>
                    <div style="text-align:center">
                        <div style="font-size:2rem;font-weight:700;color:#22c55e">{stats['sprzedaz_dzis_suma']:.0f} zl</div>
                        <div style="font-size:0.75rem;color:#64748b">PRZYCHOD</div>
                    </div>
                    <div style="text-align:center">
                        <div style="font-size:2rem;font-weight:700;color:#eab308">{stats.get('do_wyslania', 0)}</div>
                        <div style="font-size:0.75rem;color:#64748b">DO WYSYLKI</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- TAB: MIESIĄC -->
        <div id="panel-miesiac" class="stat-panel" style="display:none">
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:18px;margin-bottom:15px">
                <div style="color:#3b82f6;font-weight:600;font-size:1.1rem;margin-bottom:12px">🗓️ {miesiac.upper()}</div>
                <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:12px">
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#3b82f6">{stats['palety_miesiac']}</div>
                        <div style="font-size:0.7rem;color:#64748b">PALET</div>
                    </div>
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#ef4444">{stats['palety_miesiac_koszt']:.0f} zl</div>
                        <div style="font-size:0.7rem;color:#64748b">WYDANE</div>
                    </div>
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#22c55e">{stats['sprzedaz_miesiac_cnt']}</div>
                        <div style="font-size:0.7rem;color:#64748b">SPRZEDAZY</div>
                    </div>
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#22c55e">{stats['sprzedaz_miesiac_suma']:.0f} zl</div>
                        <div style="font-size:0.7rem;color:#64748b">PRZYCHOD</div>
                    </div>
                </div>
                <div style="margin-top:12px;padding:12px;background:rgba(34,197,94,0.1);border-radius:10px;text-align:center">
                    <div style="font-size:0.8rem;color:#64748b">SZACOWANY ZYSK</div>
                    <div style="font-size:1.8rem;font-weight:700;color:#22c55e">{stats['zysk_miesiac']:.0f} zl</div>
                </div>
            </div>
        </div>

        <!-- TAB: MAGAZYN -->
        <div id="panel-magazyn" class="stat-panel" style="display:none">
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:18px;margin-bottom:15px">
                <div style="color:#8b5cf6;font-weight:600;font-size:1.1rem;margin-bottom:12px">🏪 MAGAZYN</div>
                <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">
                    <div style="text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#8b5cf6">{stats['magazyn_produkty']}</div>
                        <div style="font-size:0.65rem;color:#64748b">PRODUKTOW</div>
                    </div>
                    <div style="text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#8b5cf6">{stats['magazyn_sztuki']}</div>
                        <div style="font-size:0.65rem;color:#64748b">SZTUK</div>
                    </div>
                    <div style="text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#8b5cf6">{stats['magazyn_wartosc']:.0f} zl</div>
                        <div style="font-size:0.65rem;color:#64748b">WARTOSC</div>
                    </div>
                </div>
                <div style="margin-top:12px;display:grid;grid-template-columns:1fr 1fr;gap:10px">
                    <div style="background:#1e1e2e;border-radius:8px;padding:10px;text-align:center">
                        <div style="font-size:1.2rem;font-weight:600;color:#3b82f6">{stats['wystawione']}</div>
                        <div style="font-size:0.65rem;color:#64748b">WYSTAWIONE</div>
                    </div>
                    <div style="background:#1e1e2e;border-radius:8px;padding:10px;text-align:center">
                        <a href="/magazyn/lezaki" style="text-decoration:none">
                            <div style="font-size:1.2rem;font-weight:600;color:#eab308">{stats['stojace_30dni']}</div>
                            <div style="font-size:0.65rem;color:#64748b">STOI &gt;30 DNI</div>
                        </a>
                    </div>
                </div>
            </div>
        </div>

        <!-- TAB: ALL-TIME -->
        <div id="panel-alltime" class="stat-panel" style="display:none">
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:18px;margin-bottom:15px">
                <div style="color:#f59e0b;font-weight:600;font-size:1.1rem;margin-bottom:12px">📈 LACZNIE (ALL-TIME)</div>
                <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:12px">
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#f59e0b">{stats['palety_lacznie']}</div>
                        <div style="font-size:0.7rem;color:#64748b">PALET</div>
                    </div>
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#f59e0b">{stats['palety_lacznie_koszt']:.0f} zl</div>
                        <div style="font-size:0.7rem;color:#64748b">ZAINWESTOWANE</div>
                    </div>
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#22c55e">{stats['sprzedaz_lacznie_cnt']}</div>
                        <div style="font-size:0.7rem;color:#64748b">SPRZEDANYCH</div>
                    </div>
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#22c55e">{stats['sprzedaz_lacznie_suma']:.0f} zl</div>
                        <div style="font-size:0.7rem;color:#64748b">PRZYCHOD{pryw_info}</div>
                    </div>
                </div>
                <div style="margin-top:12px;background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                    <div style="font-size:0.7rem;color:#64748b">SREDNIA WARTOSC ZAMOWIENIA</div>
                    <div style="font-size:1.3rem;font-weight:700;color:#f59e0b">{stats['srednia_zamowienie']:.2f} zl</div>
                </div>
            </div>
        </div>

        <!-- TAB: TOP -->
        <div id="panel-top" class="stat-panel" style="display:none">
            {'<div style="color:#f59e0b;font-weight:600;font-size:1.1rem;margin-bottom:10px">🏆 TOP PRODUKTY</div><div style="background:#12121a;border-radius:12px;padding:12px;margin-bottom:15px">' + top_prod_html + '</div>' if top_prod_html else ''}
            {'<div style="color:#f59e0b;font-weight:600;font-size:1.1rem;margin-bottom:10px">📦 TOP DOSTAWCY (ROI)</div><div style="background:#12121a;border-radius:12px;padding:12px;margin-bottom:15px">' + top_dost_html + '</div>' if top_dost_html else ''}
        </div>

        <!-- WYKRES - zawsze widoczny -->
        <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:18px;margin-bottom:15px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                <div style="color:#8b5cf6;font-weight:600;font-size:1.1rem">📊 WYKRES ({current_year})</div>
                <div style="display:flex;gap:6px">
                    <button onclick="toggleChart('przychod')" id="btn-przychod" style="padding:4px 10px;border:none;border-radius:6px;font-size:0.7rem;cursor:pointer;background:#8b5cf6;color:#fff">Przychod</button>
                    <button onclick="toggleChart('zamowienia')" id="btn-zamowienia" style="padding:4px 10px;border:none;border-radius:6px;font-size:0.7rem;cursor:pointer;background:#1e1e2e;color:#64748b">Zamowienia</button>
                </div>
            </div>
            <canvas id="chartMiesiace" height="200"></canvas>
        </div>

        <!-- Quick links -->
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:10px">
            <a href="/palety" style="display:block;padding:14px;background:#3b82f6;border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600">📦 Palety</a>
            <a href="/sprzedaze" style="display:block;padding:14px;background:#22c55e;border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600">💰 Sprzedaze</a>
            <a href="/analityka" style="display:block;padding:14px;background:#8b5cf6;border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600">📈 Analityka</a>
        </div>

        <a href="/" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:15px">← Powrot</a>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>
    const chartLabels = {chart_labels};
    const chartPrzychod = {chart_data};
    const chartZamowienia = {chart_orders};
    let currentChart = 'przychod';

    const ctx = document.getElementById('chartMiesiace');
    let chart = new Chart(ctx, {{
        type: 'bar',
        data: {{
            labels: chartLabels,
            datasets: [{{
                label: 'Przychod (zl)',
                data: chartPrzychod,
                backgroundColor: 'rgba(139, 92, 246, 0.8)',
                borderColor: 'rgba(139, 92, 246, 1)',
                borderWidth: 1,
                borderRadius: 5
            }}]
        }},
        options: {{
            responsive: true,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                y: {{ beginAtZero: true, grid: {{ color: 'rgba(255,255,255,0.1)' }}, ticks: {{ color: '#64748b' }} }},
                x: {{ grid: {{ display: false }}, ticks: {{ color: '#64748b' }} }}
            }},
            onClick: function(e, elements) {{
                if (elements.length > 0) {{
                    const idx = elements[0].index;
                    const miesiac = String(idx + 1).padStart(2, '0');
                    window.location.href = '/magazyn/statystyki?miesiac={current_year}-' + miesiac;
                }}
            }}
        }}
    }});

    // Zmień kursor na pointer nad słupkami
    ctx.style.cursor = 'pointer';

    function toggleChart(type) {{
        currentChart = type;
        document.getElementById('btn-przychod').style.background = type==='przychod' ? '#8b5cf6' : '#1e1e2e';
        document.getElementById('btn-przychod').style.color = type==='przychod' ? '#fff' : '#64748b';
        document.getElementById('btn-zamowienia').style.background = type==='zamowienia' ? '#22c55e' : '#1e1e2e';
        document.getElementById('btn-zamowienia').style.color = type==='zamowienia' ? '#fff' : '#64748b';

        chart.data.datasets[0].data = type==='przychod' ? chartPrzychod : chartZamowienia;
        chart.data.datasets[0].label = type==='przychod' ? 'Przychod (zl)' : 'Zamowienia';
        chart.data.datasets[0].backgroundColor = type==='przychod' ? 'rgba(139,92,246,0.8)' : 'rgba(34,197,94,0.8)';
        chart.data.datasets[0].borderColor = type==='przychod' ? 'rgba(139,92,246,1)' : 'rgba(34,197,94,1)';
        chart.update();
    }}

    function showTab(tab) {{
        document.querySelectorAll('.stat-panel').forEach(p => p.style.display = 'none');
        document.querySelectorAll('.stat-tab').forEach(t => {{ t.style.background = '#1e1e2e'; t.style.color = '#64748b'; }});
        document.getElementById('panel-' + tab).style.display = 'block';
        const btn = document.getElementById('tab-' + tab);
        const colors = {{ dzis: '#22c55e', miesiac: '#3b82f6', magazyn: '#8b5cf6', alltime: '#f59e0b', top: '#ef4444' }};
        btn.style.background = colors[tab] || '#3b82f6';
        btn.style.color = '#fff';
    }}
    </script>
    '''
    return html


# ============================================================
# ZARZĄDZANIE PALETAMI
# ============================================================
@app.route('/palety/napraw-ceny')
def napraw_ceny_palet():
    """Uzupełnia brakujące ceny zakupu w paletach (cena_zakupu = 0) - zapisuje netto i brutto"""
    from modules.database import get_db
    
    conn = get_db()
    
    # Sprawdź czy kolumna cena_zakupu_netto istnieje
    kolumny = [desc[0] for desc in conn.execute('PRAGMA table_info(palety)').fetchall()]
    ma_kolumne_netto = 'cena_zakupu_netto' in kolumny
    
    # Jeśli nie ma - dodaj ją
    if not ma_kolumne_netto:
        try:
            conn.execute('ALTER TABLE palety ADD COLUMN cena_zakupu_netto REAL DEFAULT 0')
            conn.commit()
            ma_kolumne_netto = True
            print("✅ Dodano kolumnę cena_zakupu_netto")
        except:
            pass
    
    # Pobierz palety z cena_zakupu = 0
    palety = conn.execute('''
        SELECT id, nazwa FROM palety WHERE cena_zakupu IS NULL OR cena_zakupu = 0
    ''').fetchall()
    
    updated = 0
    
    for p in palety:
        # Oblicz sumę cen brutto produktów (cena_brutto to ŁĄCZNA cena za produkt, nie za sztukę)
        suma_brutto = conn.execute('''
            SELECT COALESCE(SUM(cena_brutto), 0) FROM produkty WHERE paleta_id = ?
        ''', (p['id'],)).fetchone()[0]
        suma_netto = round(suma_brutto / 1.23, 2) if suma_brutto > 0 else 0

        if suma_brutto > 0:
            # cena_zakupu = BRUTTO
            if ma_kolumne_netto:
                conn.execute('''
                    UPDATE palety SET cena_zakupu = ?, cena_zakupu_netto = ? WHERE id = ?
                ''', (suma_brutto, suma_netto, p['id']))
            else:
                conn.execute('UPDATE palety SET cena_zakupu = ? WHERE id = ?', (suma_brutto, p['id']))
            updated += 1
            print(f"✅ Naprawiono paletę {p['id']}: {p['nazwa']} -> {suma_netto:.0f} netto | {suma_brutto:.0f} brutto")
    
    conn.commit()
    
    return f'''
    <html><head><meta http-equiv="refresh" content="2;url=/palety"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px">✅</div>
            <div style="font-size:1.2rem">Naprawiono {updated} palet!</div>
            <div style="color:#64748b;margin-top:10px">Ceny zakupu (netto + brutto) zostały uzupełnione</div>
        </div>
    </body></html>
    '''


@app.route('/palety/przelicz-brutto')
def przelicz_brutto_palet():
    """Przelicza WSZYSTKIE palety - cena_zakupu = suma netto produktów * 1.23"""
    from modules.database import get_db
    
    conn = get_db()
    
    # Pobierz WSZYSTKIE palety
    palety = conn.execute('SELECT id, nazwa, cena_zakupu FROM palety').fetchall()
    
    updated = 0
    
    for p in palety:
        # Oblicz sumę cen brutto produktów (cena_brutto = ŁĄCZNA za produkt)
        suma_brutto = conn.execute('''
            SELECT COALESCE(SUM(cena_brutto), 0) FROM produkty WHERE paleta_id = ?
        ''', (p['id'],)).fetchone()[0]
        suma_netto = round(suma_brutto / 1.23, 2) if suma_brutto > 0 else 0

        if suma_brutto > 0:
            stara_cena = p['cena_zakupu'] or 0
            conn.execute('UPDATE palety SET cena_zakupu = ?, cena_zakupu_netto = ? WHERE id = ?', (suma_brutto, suma_netto, p['id']))
            updated += 1
            print(f"✅ Paleta {p['id']}: {p['nazwa']} -> {stara_cena:.0f} → {suma_brutto:.0f} zł brutto")
    
    conn.commit()
    
    return f'''
    <html><head><meta http-equiv="refresh" content="2;url=/palety"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px">✅</div>
            <div style="font-size:1.2rem">Przeliczono {updated} palet!</div>
            <div style="color:#64748b;margin-top:10px">Wszystkie ceny zakupu = suma netto × 1.23 (brutto)</div>
        </div>
    </body></html>
    '''


@app.route('/admin/deploy', methods=['GET', 'POST'])
def admin_deploy():
    """Deploy plików modułów przez upload HTTP"""
    if request.method == 'POST':
        f = request.files.get('file')
        target = request.form.get('target', '')
        if not f or not target:
            return jsonify({'error': 'Brak pliku lub target'}), 400

        # Tylko dozwolone ścieżki
        ALLOWED = {
            'modules/magazynier.py',
            'modules/printer_manager.py',
            'modules/allegro_api.py',
            'modules/olx_api.py',
            'modules/vinted_api.py',
            'modules/database.py',
            'app.py',
        }
        if target not in ALLOWED:
            return jsonify({'error': f'Niedozwolona ścieżka: {target}'}), 403

        import shutil
        full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), target)
        # Backup
        if os.path.exists(full_path):
            shutil.copy2(full_path, full_path + '.bak')

        f.save(full_path)
        return jsonify({'ok': True, 'msg': f'Zapisano {target} ({os.path.getsize(full_path)} bytes). Restart wymagany.'})

    # GET — formularz
    return '''<!DOCTYPE html><html><head><title>Deploy</title>
    <style>body{font-family:sans-serif;background:#0f172a;color:#e2e8f0;max-width:600px;margin:50px auto;padding:20px}
    select,input,button{padding:10px;margin:8px 0;width:100%;border-radius:8px;border:1px solid #334155;background:#1e293b;color:#fff;font-size:1rem}
    button{background:#22c55e;cursor:pointer;font-weight:600;border:none}
    #result{margin-top:20px;padding:15px;border-radius:8px;display:none}</style></head>
    <body><h1>📦 Deploy modułu</h1>
    <form id="f" enctype="multipart/form-data">
    <label>Moduł:</label>
    <select name="target">
    <option value="modules/magazynier.py">modules/magazynier.py</option>
    <option value="modules/printer_manager.py">modules/printer_manager.py</option>
    <option value="modules/allegro_api.py">modules/allegro_api.py</option>
    <option value="modules/olx_api.py">modules/olx_api.py</option>
    <option value="modules/vinted_api.py">modules/vinted_api.py</option>
    <option value="modules/database.py">modules/database.py</option>
    <option value="app.py">app.py</option>
    </select>
    <label>Plik:</label><input type="file" name="file" accept=".py">
    <button type="submit">🚀 Deploy</button></form>
    <div id="result"></div>
    <script>document.getElementById('f').onsubmit=async e=>{e.preventDefault();
    const r=document.getElementById('result');r.style.display='block';r.style.background='#1e293b';r.textContent='Wysylanie...';
    const fd=new FormData(e.target);const res=await fetch('/admin/deploy',{method:'POST',body:fd});
    const d=await res.json();r.style.background=d.ok?'#166534':'#7f1d1d';r.textContent=d.ok?d.msg:d.error;}</script>
    <p style="margin-top:30px;font-size:0.8rem;color:#64748b">Po deploymencie zrestartuj usługę: <code>sudo systemctl restart akces-hub</code></p>
    </body></html>'''


@app.route('/admin/przelicz-palety')
def admin_przelicz_palety():
    """Jednorazowe przeliczenie cen palet z sumy cena_netto produktów.
    cena_netto = łączny koszt zakupu produktu (niezmienny, niezależny od sprzedaży).
    """
    from modules.database import get_db
    conn = get_db()

    kolumny = [desc[0] for desc in conn.execute('PRAGMA table_info(palety)').fetchall()]
    if 'cena_zakupu_netto' not in kolumny:
        try:
            conn.execute('ALTER TABLE palety ADD COLUMN cena_zakupu_netto REAL DEFAULT 0')
            conn.commit()
        except:
            pass

    palety = conn.execute('SELECT id, nazwa, cena_zakupu FROM palety').fetchall()
    updated = 0

    for p in palety:
        # SUM(cena_brutto) - koszt zakupu wszystkich produktów w palecie
        # cena_brutto to ŁĄCZNA cena za dany produkt (nie za sztukę)
        suma_brutto = conn.execute(
            'SELECT COALESCE(SUM(cena_brutto), 0) FROM produkty WHERE paleta_id = ?',
            (p['id'],)
        ).fetchone()[0]
        suma_netto = round(suma_brutto / 1.23, 2) if suma_brutto > 0 else 0

        if suma_brutto > 0:
            conn.execute(
                'UPDATE palety SET cena_zakupu = ?, cena_zakupu_netto = ? WHERE id = ?',
                (suma_brutto, suma_netto, p['id'])
            )
            updated += 1

    conn.commit()

    return f"""
    <html>
    <head>
        <meta http-equiv="refresh" content="3;url=/palety">
        <style>body{{background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column;gap:16px}}</style>
    </head>
    <body>
        <div style="font-size:3rem">✅</div>
        <div style="font-size:1.4rem;font-weight:700">Przeliczono {updated} palet!</div>
        <div style="color:#64748b;font-size:0.9rem">cena_zakupu = suma cena_netto × 1.23 (wartość z importu, stała)</div>
        <div style="color:#3b82f6;font-size:0.85rem">Przekierowanie za 3 sekundy...</div>
    </body>
    </html>
    """


@app.route('/palety')
def palety_lista():
    from modules.database import get_palety_list, get_full_stats
    
    palety = get_palety_list(100)
    stats = get_full_stats()
    
    palety_html = ''
    for p in palety:
        data = p['data_zakupu'] if p['data_zakupu'] else 'Brak daty'
        # Bezpieczne pobieranie wartości - sqlite3.Row nie ma .get()
        try:
            wartosc_zakupu_prod = p['wartosc_zakupu_produktow'] or 0
        except (KeyError, TypeError):
            wartosc_zakupu_prod = 0
        
        # Bezpieczne pobieranie regalu
        try:
            regal = p['regal'] if p['regal'] else ''
        except (KeyError, TypeError):
            regal = ''
        
        # Statystyki sprzedaży
        try:
            sztuk_w_magazynie = p['sztuk_w_magazynie'] or 0
            sprzedano_status = p['sprzedano_status'] or 0
            sprzedano_tabela = p['sprzedano_tabela'] or 0
            try:
                sprzedano_offline = p['sprzedano_offline'] or 0  # sprzedane poza Allegro
            except:
                sprzedano_offline = 0
            try:
                przychod_offline = p['przychod_offline'] or 0  # przychód ze sprzedaży offline
            except:
                przychod_offline = 0
            sprzedano_wartosc_status = p['sprzedano_wartosc_status'] or 0
            sprzedano_wartosc_tabela = p['sprzedano_wartosc_tabela'] or 0
            # ZMIANA: Użyj ceny liczonej z produktów zamiast z tabeli palety
            koszt_palety = p['cena_zakupu'] or wartosc_zakupu_prod  # Cena z palety, fallback na produkty
            
            # FIX: sprzedano_tabela JUŻ ZAWIERA offline (kupujacy='offline')
            # więc NIE dodajemy sprzedano_offline/przychod_offline osobno!
            if sprzedano_tabela > 0:
                sprzedano_szt = sprzedano_tabela
                sprzedano_wartosc = sprzedano_wartosc_tabela
            else:
                sprzedano_szt = sprzedano_status + sprzedano_offline
                sprzedano_wartosc = sprzedano_wartosc_status + przychod_offline
            # FIX: użyj MAX z dwóch źródeł:
            # 1) sztuk_w_magazynie + sprzedano_szt (dla produktów z ilosc=0 po sprzedaży)
            # 2) SUM(ilosc) z produktów (dla produktów z zachowanym oryginalnym ilosc)
            # Np. Plecaki: ilosc=42 ale sprzedano 17 → max(0+17, 42) = 42
            # Np. Bieżnie: ilosc=0 (sprzedany) → max(0+13, 0) = 13
            stary_lacznie = sztuk_w_magazynie + sprzedano_szt
            try:
                ilosc_total = p['sztuk_lacznie_total'] or 0
            except (KeyError, TypeError):
                ilosc_total = 0
            sztuk_lacznie = max(stary_lacznie, ilosc_total)
            # Przelicz sztuk_w_magazynie na resztę
            sztuk_w_magazynie = max(0, sztuk_lacznie - sprzedano_szt)
        except (KeyError, TypeError):
            sztuk_lacznie = 0
            sprzedano_szt = 0
            sprzedano_wartosc = 0
            koszt_palety = 0
        
        # Oblicz koszt sprzedanych na podstawie średniej ceny za sztukę
        if sztuk_lacznie > 0 and koszt_palety > 0:
            srednia_cena_szt = koszt_palety / sztuk_lacznie
            sprzedano_koszt = sprzedano_szt * srednia_cena_szt
        else:
            sprzedano_koszt = 0
        
        # Oblicz zysk netto (przychód - koszt)
        zysk_netto = sprzedano_wartosc - sprzedano_koszt
        
        # Pasek postępu sprzedaży
        procent_sprzedane = (sprzedano_szt / sztuk_lacznie * 100) if sztuk_lacznie > 0 else 0
        progress_color = '#22c55e' if procent_sprzedane >= 50 else '#eab308' if procent_sprzedane >= 20 else '#64748b'
        
        palety_html += f'''
        <div style="background:#1e1e2e;border-radius:12px;padding:12px;margin-bottom:10px">
            <div style="display:flex;justify-content:space-between;align-items:start">
                <div>
                    <div style="font-weight:600">{p['nazwa'] or f"Paleta #{p['id']}"}</div>
                    <div style="font-size:0.8rem;color:#64748b">{p['dostawca']} • {data}</div>
                    {f'<div style="font-size:0.75rem;color:#8b5cf6;margin-top:2px">📍 Regal: {regal}</div>' if regal else ''}
                </div>
                <div style="text-align:right">
                    <div style="font-weight:600;color:#ef4444">{koszt_palety:.0f} zł</div>
                    <div style="font-size:0.75rem;color:#64748b">{p['produktow']} prod.</div>
                </div>
            </div>
            
            <!-- PASEK SPRZEDAŻY -->
            <div style="margin-top:10px;background:#0a0a0f;border-radius:6px;padding:8px">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                    <span style="font-size:0.75rem;color:#94a3b8">📊 Sprzedano:</span>
                    <span style="font-size:0.85rem;font-weight:700;color:{progress_color}">{sprzedano_szt} / {sztuk_lacznie} szt</span>
                </div>
                <div style="background:#1e1e2e;border-radius:4px;height:8px;overflow:hidden">
                    <div style="background:{progress_color};width:{procent_sprzedane:.0f}%;height:100%;border-radius:4px;transition:width 0.3s"></div>
                </div>
                {f'<div style="display:flex;justify-content:space-between;margin-top:6px;font-size:0.7rem"><span style="color:#22c55e">💰 Zysk: {zysk_netto:+.0f} zł</span><span style="color:#64748b">({procent_sprzedane:.0f}%)</span></div>' if sprzedano_szt > 0 else ''}
            </div>
            
            <div style="margin-top:8px;display:flex;justify-content:space-between;font-size:0.75rem">
                <span style="color:#ef4444">💰 Zakup: {koszt_palety:.0f} zł</span>
                <span style="color:#22c55e">Detal: {p['wartosc_detalu']:.0f} zł</span>
            </div>
            <a href="/palety/{p['id']}" style="display:block;text-align:center;color:#3b82f6;margin-top:8px;font-size:0.8rem">Szczegóły →</a>
        </div>
        '''
    
    if not palety:
        palety_html = '<div style="text-align:center;color:#64748b;padding:30px">Brak palet. Dodaj pierwszą!</div>'
    
    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>📦 PALETY</h1>
            <small>Zarządzaj zakupami</small>
        </div>
        
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:15px">
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#3b82f6">{stats['palety_lacznie']}</div>
                <div style="font-size:0.7rem;color:#64748b">ŁĄCZNIE</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#3b82f6">{stats['palety_miesiac']}</div>
                <div style="font-size:0.7rem;color:#64748b">TEN MSC</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#ef4444">{stats['palety_lacznie_koszt']:.0f}</div>
                <div style="font-size:0.7rem;color:#64748b">WYDANE ZŁ</div>
            </div>
        </div>
        
        <a href="/palety/dodaj" class="btn" style="display:block;width:100%;padding:14px;background:#22c55e;border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600;margin-bottom:10px">➕ DODAJ PALETĘ</a>
        
        <a href="/palety/przelicz-brutto" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-bottom:15px;font-size:0.8rem" onclick="return confirm('Przeliczyć ceny zakupu wszystkich palet na brutto (netto × 1.23)?')">🔧 Przelicz ceny na brutto (+23% VAT)</a>
        
        <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin-bottom:10px">OSTATNIE PALETY</div>
        
        {palety_html}
        
        <a href="/statystyki" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:15px">← Statystyki</a>
    </div>
    '''
    return html


@app.route('/palety/dodaj', methods=['GET', 'POST'])
def paleta_dodaj():
    from modules.database import add_paleta
    
    if request.method == 'POST':
        nazwa = request.form.get('nazwa', '')
        dostawca = request.form.get('dostawca', 'Jobalots')  # Domyślnie Jobalots
        regal = request.form.get('regal', '')
        
        # Bezpieczna konwersja ceny
        cena_str = request.form.get('cena', '0')
        try:
            cena = float(cena_str) if cena_str else 0
        except:
            cena = 0
            
        data = request.form.get('data', '')
        notatki = request.form.get('notatki', '')
        
        # Debug log
        print(f"📦 Dodaję paletę: nazwa={nazwa}, dostawca={dostawca}, cena={cena}")
        
        paleta_id = add_paleta(nazwa, dostawca, cena, data, notatki, regal)
        
        print(f"✅ Utworzono paletę ID: {paleta_id}")
        
        return redirect(f'/palety/{paleta_id}')
    
    html = CSS + '''
    <div class="container">
        <div class="header">
            <h1>➕ NOWA PALETA</h1>
            <small>Dodaj zakupioną paletę</small>
        </div>
        
        <!-- IMPORT Z EXCEL -->
        <a href="/palety/import-xlsx" style="display:block;background:linear-gradient(135deg,#22c55e,#16a34a);border-radius:12px;padding:16px;margin-bottom:10px;text-decoration:none;color:#fff">
            <div style="display:flex;align-items:center;gap:12px">
                <div style="font-size:2rem">📊</div>
                <div>
                    <div style="font-weight:600;font-size:1.1rem">IMPORT Z EXCEL</div>
                    <div style="font-size:0.8rem;opacity:0.9">Wrzuć plik XLSX z listą produktów</div>
                </div>
                <div style="margin-left:auto;font-size:1.5rem">→</div>
            </div>
        </a>
        
        <a href="/palety/bulk-import" style="display:block;background:linear-gradient(135deg,#3b82f6,#2563eb);border-radius:12px;padding:14px;margin-bottom:10px;text-decoration:none;color:#fff">
            <div style="display:flex;align-items:center;gap:12px">
                <div style="font-size:2rem">📦</div>
                <div>
                    <div style="font-weight:600;font-size:1rem">BULK IMPORT (wiele palet)</div>
                    <div style="font-size:0.75rem;opacity:0.9">Importuj kilka palet naraz z osobnymi plikami</div>
                </div>
                <div style="margin-left:auto;font-size:1.5rem">→</div>
            </div>
        </a>
        
        <div style="text-align:center;color:#64748b;font-size:0.8rem;margin-bottom:15px">— lub dodaj ręcznie —</div>
        
        <form method="POST" style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:18px">
            <div style="margin-bottom:12px">
                <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Nazwa / Opis</label>
                <input type="text" name="nazwa" placeholder="np. Mix elektronika #15" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">
            </div>
            
            <div style="margin-bottom:12px">
                <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Dostawca</label>
                <select name="dostawca" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">
                    <option value="Jobalots">Jobalots</option>
                    <option value="Warrington">Warrington</option>
                    <option value="Miglo">Miglo</option>
                    <option value="Inny">Inny</option>
                </select>
            </div>
            
            <div style="margin-bottom:12px">
                <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">📍 Regal / Lokalizacja</label>
                <input type="text" name="regal" placeholder="np. Migło, Regał A1, itp." style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">
            </div>
            
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Cena zakupu brutto (zł)</label>
                    <input type="number" name="cena" placeholder="2500" step="0.01" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Data zakupu</label>
                    <input type="date" name="data" value="''' + datetime.now().strftime('%Y-%m-%d') + '''" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">
                </div>
            </div>
            
            <div style="margin-bottom:15px">
                <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Notatki</label>
                <textarea name="notatki" rows="2" placeholder="Opcjonalne uwagi..." style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem;resize:vertical"></textarea>
            </div>
            
            <button type="submit" style="width:100%;padding:14px;background:#22c55e;border:none;border-radius:10px;color:#fff;font-weight:600;font-size:1rem;cursor:pointer">💾 ZAPISZ PALETĘ</button>
        </form>
        
        <a href="/palety" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:15px">← Anuluj</a>
    </div>
    '''
    return html


@app.route('/palety/import-xlsx', methods=['GET', 'POST'])
def paleta_import_xlsx():
    """Import palety z pliku Excel"""
    import pandas as pd
    from modules.database import get_db, add_paleta
    
    if request.method == 'POST':
        # Obsługa uploadu pliku
        if 'file' not in request.files:
            return redirect('/palety/import-xlsx?error=no_file')
        
        file = request.files['file']
        if file.filename == '':
            return redirect('/palety/import-xlsx?error=no_file')
        
        if not file.filename.endswith(('.xlsx', '.xls')):
            return redirect('/palety/import-xlsx?error=wrong_format')
        
        try:
            # Wczytaj Excel
            df = pd.read_excel(file)
            
            # Pobierz dane palety z formularza
            nazwa = request.form.get('nazwa', file.filename)
            dostawca = request.form.get('dostawca', 'Jobalots')
            regal = request.form.get('regal', '')
            cena_zakupu = float(request.form.get('cena', 0) or 0)
            data_zakupu = request.form.get('data', datetime.now().strftime('%Y-%m-%d'))
            
            # Mapowanie kolumn (elastyczne)
            col_nazwa = request.form.get('col_nazwa', '')
            col_ean = request.form.get('col_ean', '')
            col_ilosc = request.form.get('col_ilosc', '')
            col_cena = request.form.get('col_cena', '')
            col_cena_detal = request.form.get('col_cena_detal', '')
            
            # Utwórz paletę
            paleta_id = add_paleta(nazwa, dostawca, cena_zakupu, data_zakupu, f'Import z: {file.filename}', regal)
            
            # Dodaj produkty
            conn = get_db()
            produkty_dodane = 0
            
            for idx, row in df.iterrows():
                try:
                    # Pobierz wartości z wybranych kolumn
                    prod_nazwa = str(row[col_nazwa]) if col_nazwa and col_nazwa in df.columns else f'Produkt {idx+1}'
                    prod_ean = str(row[col_ean]) if col_ean and col_ean in df.columns else ''
                    prod_ilosc = int(row[col_ilosc]) if col_ilosc and col_ilosc in df.columns and pd.notna(row[col_ilosc]) else 1
                    prod_cena = float(row[col_cena]) if col_cena and col_cena in df.columns and pd.notna(row[col_cena]) else 0
                    prod_cena_detal = float(row[col_cena_detal]) if col_cena_detal and col_cena_detal in df.columns and pd.notna(row[col_cena_detal]) else prod_cena * 2
                    # cena_brutto = cena_netto * 1.23 (VAT 23%)
                    prod_cena_brutto = round(prod_cena * 1.23, 2)
                    
                    # Pomiń puste wiersze
                    if not prod_nazwa or prod_nazwa == 'nan' or prod_nazwa.strip() == '':
                        continue
                    
                    # Auto-kategoryzacja na podstawie nazwy
                    prod_kategoria = auto_kategoryzuj(prod_nazwa)
                    
                    conn.execute('''
                        INSERT INTO produkty (nazwa, ean, ilosc, cena_netto, cena_brutto, cena_allegro, paleta_id, dostawca, status, kategoria)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'magazyn', ?)
                    ''', (prod_nazwa[:200], prod_ean, prod_ilosc, prod_cena, prod_cena_brutto, prod_cena_detal, paleta_id, dostawca, prod_kategoria))
                    
                    # Dodaj do historii - WYŁĄCZONE (konflikt bazy danych)
                    produkt_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
                    # from modules.database import add_historia
                    # add_historia(produkt_id, 'importowano', f'Zaimportowano z Excel ({nazwa})', {'dostawca': dostawca, 'ilosc': prod_ilosc, 'paleta_id': paleta_id})
                    
                    produkty_dodane += 1
                    
                except Exception as e:
                    print(f"Błąd wiersza {idx}: {e}")
                    continue
            
            # Aktualizuj liczbę produktów w palecie
            conn.execute('UPDATE palety SET ilosc_produktow = ? WHERE id = ?', (produkty_dodane, paleta_id))
            
            # NIE przeliczaj z sumy - cena_zakupu = STAŁA od momentu importu
            stara_cena = conn.execute('SELECT COALESCE(cena_zakupu, 0) FROM palety WHERE id = ?', (paleta_id,)).fetchone()[0]
            if stara_cena == 0:
                # Nowa paleta - ustaw z sumy cen brutto produktów (cena_brutto = ŁĄCZNA za produkt)
                suma_brutto = conn.execute('SELECT COALESCE(SUM(cena_brutto), 0) FROM produkty WHERE paleta_id = ?', (paleta_id,)).fetchone()[0]
                nowa_netto = round(suma_brutto / 1.23, 2) if suma_brutto > 0 else 0
                nowa_brutto = suma_brutto
            else:
                # Istniejąca paleta - nie ruszaj ceny, tylko dodaj nowe produkty
                nowe_netto = 0  # zostaje stara cena, nowe produkty były importowane przez paletomat który sam akumuluje
                nowa_netto = round(stara_cena / 1.23, 2)
                nowa_brutto = stara_cena

            kolumny = [desc[0] for desc in conn.execute('PRAGMA table_info(palety)').fetchall()]
            if 'cena_zakupu_netto' not in kolumny:
                try:
                    conn.execute('ALTER TABLE palety ADD COLUMN cena_zakupu_netto REAL DEFAULT 0')
                except:
                    pass

            try:
                conn.execute('UPDATE palety SET cena_zakupu = ?, cena_zakupu_netto = ? WHERE id = ?',
                    (nowa_brutto, nowa_netto, paleta_id))
            except:
                conn.execute('UPDATE palety SET cena_zakupu = ? WHERE id = ?', (nowa_brutto, paleta_id))
            print(f"💰 Cena zakupu palety (stała): {nowa_netto:.2f} netto | {nowa_brutto:.2f} brutto")
            
            conn.commit()
            
            return redirect(f'/palety/{paleta_id}?imported={produkty_dodane}')
            
        except Exception as e:
            return redirect(f'/palety/import-xlsx?error={str(e)[:50]}')
    
    # GET - pokaż formularz lub podgląd kolumn
    preview_html = ''
    columns = []
    
    # Jeśli jest plik w sesji - pokaż podgląd
    if 'xlsx_preview' in request.args:
        # TODO: obsługa podglądu
        pass
    
    error = request.args.get('error', '')
    error_html = ''
    if error:
        error_html = f'<div style="background:#ef4444;color:#fff;padding:12px;border-radius:8px;margin-bottom:15px">⚠️ Błąd: {error}</div>'
    
    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>📊 IMPORT Z EXCEL</h1>
            <small>Wrzuć plik XLSX z produktami</small>
        </div>
        
        {error_html}
        
        <form method="POST" enctype="multipart/form-data" style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:18px">
            
            <!-- PLIK -->
            <div style="margin-bottom:15px">
                <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">📁 Plik Excel (.xlsx)</label>
                <input type="file" name="file" accept=".xlsx,.xls" required
                    style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
            </div>
            
            <!-- DANE PALETY -->
            <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin:20px 0 10px;letter-spacing:1px">📦 DANE PALETY</div>
            
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Nazwa palety</label>
                    <input type="text" name="nazwa" placeholder="np. Jobalots #15" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
                </div>
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Dostawca</label>
                    <select name="dostawca" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
                        <option value="Jobalots">Jobalots</option>
                        <option value="Warrington">Warrington</option>
                        <option value="Miglo">Miglo</option>
                        <option value="Inny">Inny</option>
                    </select>
                </div>
            </div>
            
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:15px">
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Cena zakupu brutto (zł)</label>
                    <input type="number" name="cena" placeholder="2500" step="0.01" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
                </div>
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Data zakupu</label>
                    <input type="date" name="data" value="{datetime.now().strftime('%Y-%m-%d')}" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
                </div>
            </div>
            
            <div style="margin-bottom:15px">
                <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">📍 Regal / Lokalizacja</label>
                <input type="text" name="regal" placeholder="np. Migło, Regał A1, itp." style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
            </div>
            
            <!-- MAPOWANIE KOLUMN -->
            <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin:20px 0 10px;letter-spacing:1px">🔗 MAPOWANIE KOLUMN</div>
            <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:12px">Wpisz nazwy kolumn z Twojego Excela (dokładnie jak w nagłówku)</div>
            
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px">Kolumna z NAZWĄ *</label>
                    <input type="text" name="col_nazwa" placeholder="np. Description" required
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.9rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px">Kolumna z EAN</label>
                    <input type="text" name="col_ean" placeholder="np. EAN / Barcode"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.9rem">
                </div>
            </div>
            
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:15px">
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px">Ilość</label>
                    <input type="text" name="col_ilosc" placeholder="np. Qty"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.9rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px">Cena zakupu</label>
                    <input type="text" name="col_cena" placeholder="np. Unit Price"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.9rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px">RRP / Detal</label>
                    <input type="text" name="col_cena_detal" placeholder="np. RRP"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.9rem">
                </div>
            </div>
            
            <button type="submit" style="width:100%;padding:14px;background:linear-gradient(135deg,#22c55e,#16a34a);border:none;border-radius:10px;color:#fff;font-weight:600;font-size:1rem;cursor:pointer">
                📥 IMPORTUJ PALETĘ
            </button>
        </form>
        
        <!-- PRZYKŁADOWE NAZWY KOLUMN -->
        <div style="margin-top:15px;padding:15px;background:#1e1e2e;border-radius:12px">
            <div style="font-weight:600;margin-bottom:10px;color:#94a3b8">💡 Przykładowe nazwy kolumn</div>
            <div style="font-size:0.85rem;color:#64748b">
                <b>Jobalots:</b> Description, EAN, Qty, Unit Price, RRP<br>
                <b>Warrington:</b> Item Description, Barcode, Quantity, Cost, Retail<br>
                <b>Miglo:</b> Nazwa, EAN, Ilość, Cena, Cena detal
            </div>
        </div>
        
        <a href="/palety/dodaj" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:15px">← Powrót</a>
    </div>
    '''
    return html


# ═══════════════════════════════════════════════════════════════════════════
# BULK IMPORT - WIELE PALET NARAZ
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/palety/bulk-import', methods=['GET', 'POST'])
def paleta_bulk_import():
    """Import wielu palet naraz - każda z osobnym plikiem XLSX i nazwą"""
    import pandas as pd
    from modules.database import get_db, add_paleta
    
    if request.method == 'POST':
        try:
            conn = get_db()
            
            # Pobierz wspólne ustawienia
            dostawca = request.form.get('dostawca', 'Jobalots')
            col_nazwa = request.form.get('col_nazwa', '')
            col_ean = request.form.get('col_ean', '')
            col_ilosc = request.form.get('col_ilosc', '')
            col_cena = request.form.get('col_cena', '')
            col_cena_detal = request.form.get('col_cena_detal', '')
            
            wyniki = []
            
            # Iteruj po plikach (max 20)
            for i in range(20):
                file_key = f'file_{i}'
                name_key = f'nazwa_{i}'
                cena_key = f'cena_{i}'
                regal_key = f'regal_{i}'
                
                if file_key not in request.files:
                    continue
                
                file = request.files[file_key]
                if not file or file.filename == '':
                    continue
                
                if not file.filename.endswith(('.xlsx', '.xls')):
                    wyniki.append({'nazwa': file.filename, 'status': 'error', 'msg': 'Nieprawidłowy format'})
                    continue
                
                # Nazwa palety
                nazwa = request.form.get(name_key, '').strip()
                if not nazwa:
                    # Auto-nazwa z pliku
                    nazwa = file.filename.rsplit('.', 1)[0]
                
                cena_zakupu = float(request.form.get(cena_key, 0) or 0)
                regal = request.form.get(regal_key, '').strip()
                data_zakupu = request.form.get('data', datetime.now().strftime('%Y-%m-%d'))
                
                try:
                    df = pd.read_excel(file)
                    
                    # Utwórz paletę
                    paleta_id = add_paleta(nazwa, dostawca, cena_zakupu, data_zakupu, f'Bulk import: {file.filename}', regal)
                    
                    produkty_dodane = 0
                    for idx, row in df.iterrows():
                        try:
                            prod_nazwa = str(row[col_nazwa]) if col_nazwa and col_nazwa in df.columns else f'Produkt {idx+1}'
                            prod_ean = str(row[col_ean]) if col_ean and col_ean in df.columns else ''
                            prod_ilosc = int(row[col_ilosc]) if col_ilosc and col_ilosc in df.columns and pd.notna(row[col_ilosc]) else 1
                            prod_cena = float(row[col_cena]) if col_cena and col_cena in df.columns and pd.notna(row[col_cena]) else 0
                            prod_cena_detal = float(row[col_cena_detal]) if col_cena_detal and col_cena_detal in df.columns and pd.notna(row[col_cena_detal]) else prod_cena * 2
                            # cena_brutto = cena_netto * 1.23 (VAT 23%)
                            prod_cena_brutto = round(prod_cena * 1.23, 2)
                            
                            if not prod_nazwa or prod_nazwa == 'nan' or prod_nazwa.strip() == '':
                                continue
                            
                            prod_kategoria = auto_kategoryzuj(prod_nazwa)
                            
                            conn.execute('''
                                INSERT INTO produkty (nazwa, ean, ilosc, cena_netto, cena_brutto, cena_allegro, paleta_id, dostawca, status, kategoria)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'magazyn', ?)
                            ''', (prod_nazwa[:200], prod_ean, prod_ilosc, prod_cena, prod_cena_brutto, prod_cena_detal, paleta_id, dostawca, prod_kategoria))
                            
                            produkty_dodane += 1
                        except:
                            continue
                    
                    # Aktualizuj liczbę i cenę
                    conn.execute('UPDATE palety SET ilosc_produktow = ? WHERE id = ?', (produkty_dodane, paleta_id))
                    
                    if cena_zakupu == 0:
                        # Auto-oblicz z produktów (cena_brutto = ŁĄCZNA za produkt)
                        suma_brutto = conn.execute('SELECT COALESCE(SUM(cena_brutto), 0) FROM produkty WHERE paleta_id = ?', (paleta_id,)).fetchone()[0]
                        suma_netto = round(suma_brutto / 1.23, 2) if suma_brutto > 0 else 0
                        try:
                            conn.execute('UPDATE palety SET cena_zakupu = ?, cena_zakupu_netto = ? WHERE id = ?', (suma_brutto, suma_netto, paleta_id))
                        except:
                            conn.execute('UPDATE palety SET cena_zakupu = ? WHERE id = ?', (suma_brutto, paleta_id))
                    
                    wyniki.append({
                        'nazwa': nazwa, 'status': 'ok', 'paleta_id': paleta_id,
                        'produkty': produkty_dodane, 'plik': file.filename
                    })
                    
                except Exception as e:
                    wyniki.append({'nazwa': nazwa or file.filename, 'status': 'error', 'msg': str(e)[:80]})
            
            conn.commit()
            
            # Pokaż wyniki
            ok_count = sum(1 for w in wyniki if w['status'] == 'ok')
            err_count = sum(1 for w in wyniki if w['status'] == 'error')
            
            results_html = ''
            for w in wyniki:
                if w['status'] == 'ok':
                    results_html += f'''
                    <div style="display:flex;align-items:center;gap:10px;padding:12px;background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);border-radius:10px;margin-bottom:8px">
                        <div style="font-size:1.5rem">✅</div>
                        <div style="flex:1">
                            <div style="font-weight:600">{w['nazwa']}</div>
                            <div style="font-size:0.8rem;color:#64748b">{w['produkty']} produktów • {w['plik']}</div>
                        </div>
                        <a href="/palety/{w['paleta_id']}" style="color:#3b82f6;text-decoration:none;font-size:0.85rem">Otwórz →</a>
                    </div>'''
                else:
                    results_html += f'''
                    <div style="display:flex;align-items:center;gap:10px;padding:12px;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:10px;margin-bottom:8px">
                        <div style="font-size:1.5rem">❌</div>
                        <div style="flex:1">
                            <div style="font-weight:600">{w['nazwa']}</div>
                            <div style="font-size:0.8rem;color:#ef4444">{w.get('msg', 'Błąd')}</div>
                        </div>
                    </div>'''
            
            html = CSS + f'''
            <div class="container">
                <div class="header">
                    <h1>📊 WYNIKI IMPORTU</h1>
                    <small>Zaimportowano {ok_count} palet{', błędy: ' + str(err_count) if err_count else ''}</small>
                </div>
                {results_html}
                <a href="/palety" class="btn" style="display:block;width:100%;padding:14px;background:#3b82f6;border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600;margin-top:15px">📦 Przejdź do palet</a>
                <a href="/palety/bulk-import" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:10px">📊 Importuj kolejne</a>
            </div>'''
            return html
            
        except Exception as e:
            return redirect(f'/palety/bulk-import?error={str(e)[:50]}')
    
    # === GET - formularz ===
    error = request.args.get('error', '')
    error_html = f'<div style="background:#ef4444;color:#fff;padding:12px;border-radius:8px;margin-bottom:15px">⚠️ {error}</div>' if error else ''
    
    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>📊 BULK IMPORT PALET</h1>
            <small>Importuj wiele palet naraz — każda z osobnym plikiem XLSX</small>
        </div>
        
        {error_html}
        
        <form method="POST" enctype="multipart/form-data" id="bulk-form">
        
        <!-- WSPÓLNE USTAWIENIA -->
        <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:18px;margin-bottom:15px">
            <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin-bottom:12px;letter-spacing:1px">⚙️ WSPÓLNE USTAWIENIA</div>
            
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Dostawca</label>
                    <select name="dostawca" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
                        <option value="Jobalots">Jobalots</option>
                        <option value="Warrington">Warrington</option>
                        <option value="Miglo">Miglo</option>
                        <option value="Inny">Inny</option>
                    </select>
                </div>
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Data zakupu</label>
                    <input type="date" name="data" value="{datetime.now().strftime('%Y-%m-%d')}" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
                </div>
            </div>
            
            <!-- MAPOWANIE KOLUMN -->
            <div style="font-size:0.7rem;color:#64748b;text-transform:uppercase;margin:15px 0 8px;letter-spacing:1px">🔗 MAPOWANIE KOLUMN (wspólne dla wszystkich plików)</div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">Kolumna NAZWA *</label>
                    <input type="text" name="col_nazwa" placeholder="np. Description" required
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">Kolumna EAN</label>
                    <input type="text" name="col_ean" placeholder="np. EAN"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">Ilość</label>
                    <input type="text" name="col_ilosc" placeholder="Qty"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">Cena zakupu</label>
                    <input type="text" name="col_cena" placeholder="Unit Price"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">RRP / Detal</label>
                    <input type="text" name="col_cena_detal" placeholder="RRP"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
            </div>
        </div>
        
        <!-- PALETY -->
        <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin-bottom:10px;letter-spacing:1px">📦 PALETY DO IMPORTU</div>
        
        <div id="palety-container"></div>
        
        <button type="button" onclick="addPaleta()" style="width:100%;padding:14px;background:#1e1e2e;border:2px dashed #3b82f6;border-radius:12px;color:#3b82f6;font-weight:600;cursor:pointer;margin-bottom:15px;font-size:0.95rem">
            ➕ DODAJ PALETĘ
        </button>
        
        <button type="submit" id="submit-btn" disabled style="width:100%;padding:16px;background:linear-gradient(135deg,#22c55e,#16a34a);border:none;border-radius:12px;color:#fff;font-weight:700;font-size:1.1rem;cursor:pointer;opacity:0.5">
            📥 IMPORTUJ WSZYSTKIE
        </button>
        
        </form>
        
        <a href="/palety/dodaj" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:15px">← Powrót</a>
    </div>
    
    <script>
    let paletaCount = 0;
    
    function addPaleta() {{
        const i = paletaCount++;
        const container = document.getElementById('palety-container');
        
        const div = document.createElement('div');
        div.className = 'paleta-row';
        div.id = 'paleta-' + i;
        div.style.cssText = 'background:#12121a;border:1px solid #1e1e2e;border-radius:14px;padding:15px;margin-bottom:10px;position:relative';
        
        div.innerHTML = `
            <button type="button" onclick="removePaleta(${{i}})" style="position:absolute;top:10px;right:10px;background:rgba(239,68,68,0.2);border:none;border-radius:8px;color:#ef4444;padding:4px 10px;cursor:pointer;font-size:0.8rem">✕</button>
            
            <div style="font-weight:600;color:#3b82f6;margin-bottom:10px;font-size:0.9rem">📦 Paleta #${{i+1}}</div>
            
            <div style="margin-bottom:10px">
                <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">📁 Plik Excel</label>
                <input type="file" name="file_${{i}}" accept=".xlsx,.xls" required onchange="updateFileName(this, ${{i}})"
                    style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
            </div>
            
            <div style="display:grid;grid-template-columns:2fr 1fr 1fr;gap:8px">
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">Nazwa palety</label>
                    <input type="text" name="nazwa_${{i}}" id="nazwa-${{i}}" placeholder="Auto z nazwy pliku"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">Cena brutto</label>
                    <input type="number" name="cena_${{i}}" placeholder="0" step="0.01"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">Regał</label>
                    <input type="text" name="regal_${{i}}" placeholder="A1"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
            </div>
        `;
        
        container.appendChild(div);
        updateSubmitBtn();
    }}
    
    function removePaleta(i) {{
        const el = document.getElementById('paleta-' + i);
        if (el) el.remove();
        updateSubmitBtn();
    }}
    
    function updateFileName(input, i) {{
        const nameField = document.getElementById('nazwa-' + i);
        if (nameField && !nameField.value && input.files.length) {{
            // Auto-fill nazwa z pliku (bez rozszerzenia)
            nameField.placeholder = input.files[0].name.replace(/\\.[^.]+$/, '');
        }}
    }}
    
    function updateSubmitBtn() {{
        const rows = document.querySelectorAll('.paleta-row');
        const btn = document.getElementById('submit-btn');
        btn.disabled = rows.length === 0;
        btn.style.opacity = rows.length === 0 ? '0.5' : '1';
        btn.textContent = rows.length === 0 ? '📥 DODAJ PALETY POWYŻEJ' : `📥 IMPORTUJ ${{rows.length}} PALET`;
    }}
    
    // Dodaj pierwszą od razu
    addPaleta();
    </script>
    '''
    return html

# ═══════════════════════════════════════════════════════════════════════════
# MASOWA EDYCJA PALET - Adrian's custom feature v3.1.0
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/palety/<int:paleta_id>/mass-edit')
def paleta_mass_edit(paleta_id):
    """Strona masowej edycji produktów z palety"""
    from modules.database import get_db
    
    conn = get_db()
    paleta = conn.execute('SELECT * FROM palety WHERE id = ?', (paleta_id,)).fetchone()
    
    if not paleta:
        return redirect('/palety')
    
    # Pobierz produkty z magazynu
    produkty = conn.execute('''
        SELECT * FROM produkty 
        WHERE paleta_id = ? 
        ORDER BY 
            CASE 
                WHEN status = 'wystawiony' THEN 1
                WHEN status = 'magazyn' THEN 2
                ELSE 3
            END,
            data_dodania DESC
    ''', (paleta_id,)).fetchall()
    
    # Stats
    stats = conn.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'wystawiony' THEN 1 ELSE 0 END) as wystawione,
            SUM(CASE WHEN status = 'magazyn' THEN 1 ELSE 0 END) as magazyn,
            COALESCE(SUM(cena_allegro * ilosc), 0) as wartosc_total
        FROM produkty 
        WHERE paleta_id = ?
    ''', (paleta_id,)).fetchone()
    
    
    if not produkty or len(produkty) == 0:
        return CSS + f'''
        <div class="container">
            <div class="header">
                <h1>⚠️ Brak produktów</h1>
                <small>Paleta #{paleta_id}</small>
            </div>
            <div class="alert alert-warning">
                Ta paleta nie ma jeszcze żadnych produktów. Najpierw zaimportuj produkty z Excel.
            </div>
            <a href="/magazyn/import?paleta_id={paleta_id}" class="btn btn-primary">📥 Importuj produkty</a>
            <a href="/palety/{paleta_id}" class="btn btn-secondary">← Powrót</a>
        </div>
        '''
    
    # Generuj HTML produktów
    produkty_html = ''
    wybrane_count = 0
    
    for p in produkty:
        # Kolory statusów
        if p['status'] == 'wystawiony':
            status_badge = '<span style="background:#22c55e;color:#000;padding:4px 10px;border-radius:6px;font-size:0.75rem;font-weight:600">✅ WYSTAWIONE</span>'
            row_bg = 'rgba(34,197,94,0.05)'
            checkbox_disabled = 'disabled'
            checkbox_checked = ''
        elif p['status'] == 'magazyn':
            status_badge = '<span style="background:#3b82f6;color:#fff;padding:4px 10px;border-radius:6px;font-size:0.75rem;font-weight:600">🔵 MAGAZYN</span>'
            row_bg = 'rgba(59,130,246,0.05)'
            checkbox_disabled = ''
            checkbox_checked = 'checked'
            wybrane_count += 1
        elif p['status'] == 'szkic':
            status_badge = '<span style="background:#8b5cf6;color:#fff;padding:4px 10px;border-radius:6px;font-size:0.75rem;font-weight:600">📝 SZKIC</span>'
            row_bg = 'rgba(139,92,246,0.05)'
            checkbox_disabled = ''
            checkbox_checked = 'checked'
            wybrane_count += 1
        else:
            status_badge = '<span style="background:#64748b;color:#fff;padding:4px 10px;border-radius:6px;font-size:0.75rem;font-weight:600">⚪ NOWY</span>'
            row_bg = 'rgba(100,116,139,0.05)'
            checkbox_disabled = ''
            checkbox_checked = ''
        
        # Cena jednostkowa zakupu - z palety (cena_zakupu / ilosc_sztuk), fallback na cena_brutto/ilosc
        paleta_ilosc_szt_w = 0
        try:
            paleta_ilosc_szt_w = paleta['ilosc_sztuk'] or 0
        except:
            pass
        paleta_cena_zak_w = paleta['cena_zakupu'] or 0
        if paleta_cena_zak_w > 0 and paleta_ilosc_szt_w > 0:
            brutto_szt_w = paleta_cena_zak_w / paleta_ilosc_szt_w
            netto_szt_w = round(brutto_szt_w / 1.23, 2)
            ceny_tekst = f"Za szt: {netto_szt_w:.2f} zł netto / {brutto_szt_w:.2f} zł brutto (z palety)"
        else:
            ilosc_produktu = p['ilosc'] if p['ilosc'] > 0 else 1
            brutto_total = p['cena_brutto'] if p['cena_brutto'] > 0 else 0
            netto_total = p['cena_netto'] if p['cena_netto'] > 0 else 0
            brutto_szt_w = brutto_total if brutto_total > 0 else 0  # już jednostkowa, nie dzielić!
            netto_szt_w = netto_total if netto_total > 0 else 0
            if netto_total > 0 and brutto_total > 0:
                ceny_tekst = f"Za szt: {netto_szt_w:.2f} zł netto / {brutto_szt_w:.2f} zł brutto"
            elif netto_total > 0:
                ceny_tekst = f"Za szt: {netto_szt_w:.2f} zł netto"
            else:
                ceny_tekst = f"Za szt: {brutto_szt_w:.2f} zł brutto"
        
        img_html = ''
        if p['zdjecie_url']:
            img_html = f'<img src="{p["zdjecie_url"]}" style="width:50px;height:50px;object-fit:contain;border-radius:8px;background:#fff;margin-right:10px">'
        
        cena_input = f'''
        <input type="number"
               class="price-input"
               data-product-id="{p['id']}"
               value="{p['cena_allegro']:.0f}"
               min="1"
               step="1"
               style="width:90px;padding:10px 8px;background:var(--bg-primary);border:2px solid var(--border-color);border-radius:10px;color:var(--text-primary);text-align:center;font-weight:700;font-size:1rem;min-height:42px"
               {checkbox_disabled}>
        '''
        
        produkty_html += (f'''
        <div class="product-row" style="background:{row_bg};border:1px solid var(--border-color);border-radius:12px;padding:14px;margin-bottom:10px">
            ''' + (f'''
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;padding:8px 10px;background:rgba(34,197,94,0.1);border-radius:8px">
                <div style="font-size:0.85rem;color:#22c55e;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600">📝 {str(p["meta_title"])[:80]}</div>
                <button onclick="regenerateMetaTitle({p["id"]}, this)"
                        style="padding:8px 14px;background:#8b5cf6;border:none;border-radius:8px;color:#fff;font-size:0.8rem;cursor:pointer;white-space:nowrap;min-height:36px">
                    🔄 Regeneruj
                </button>
            </div>
            ''' if 'meta_title' in p.keys() and p['meta_title'] else f'''
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;padding:8px 10px;background:rgba(239,68,68,0.1);border-radius:8px">
                <div style="font-size:0.85rem;color:#ef4444;flex:1;font-weight:600">⚠️ Brak META TITLE</div>
                <button onclick="regenerateMetaTitle({p["id"]}, this)"
                        style="padding:8px 14px;background:#22c55e;border:none;border-radius:8px;color:#fff;font-size:0.8rem;cursor:pointer;white-space:nowrap;min-height:36px">
                    ✨ Generuj
                </button>
            </div>
            ''') + f'''
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
                <input type="checkbox"
                       class="product-checkbox"
                       data-product-id="{p['id']}"
                       value="{p['id']}"
                       {checkbox_checked}
                       {checkbox_disabled}
                       style="width:24px;height:24px;min-width:24px;cursor:pointer">
                {img_html}
                <div style="flex:1;min-width:0">
                    <div style="font-weight:600;font-size:0.95rem;line-height:1.3">{p['nazwa'][:60]}</div>
                </div>
                {status_badge}
            </div>
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                <div style="font-size:0.8rem;color:var(--text-muted);flex:1;min-width:150px">
                    {p['ean'] or p['asin'] or '—'} •
                    Lokalizacja: {p['lokalizacja'] or '—'} •
                    Ilość: {p['ilosc']}
                </div>
                <div style="font-size:0.75rem;color:#ef4444">💰 {ceny_tekst}</div>
                <div style="display:flex;align-items:center;gap:6px">
                    <span style="font-size:0.75rem;color:var(--text-muted)">CENA:</span>
                    {cena_input}
                </div>
            </div>
        </div>
        ''')

    html = CSS + f'''
    <style>
    .me-stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-bottom:16px }}
    .me-stat {{ background:var(--bg-secondary); border:1px solid var(--border-color); border-radius:12px; padding:12px; text-align:center }}
    .me-stat-num {{ font-size:1.3rem; font-weight:700 }}
    .me-stat-label {{ font-size:0.7rem; color:var(--text-muted) }}
    .me-bottom {{ position:fixed; bottom:0; left:0; right:0; background:var(--bg-secondary); border-top:2px solid var(--accent-blue); padding:12px 8px; z-index:100 }}
    .me-bottom-inner {{ display:flex; flex-direction:column; gap:8px; max-width:1600px; margin:0 auto }}
    .me-bottom-row {{ display:flex; gap:8px }}
    .me-btn {{ flex:1; margin:0; padding:14px 10px; font-size:0.95rem; font-weight:600; border:none; border-radius:10px; color:#fff; cursor:pointer; min-height:48px; text-align:center; text-decoration:none; display:flex; align-items:center; justify-content:center }}
    .me-btn-back {{ background:var(--bg-primary); color:var(--text-primary); border:1px solid var(--border-color); flex:0 0 auto; padding:14px 16px }}
    .me-btn-meta {{ background:linear-gradient(135deg,#8b5cf6,#6d28d9) }}
    .me-btn-wystaw {{ background:linear-gradient(135deg,#22c55e,#16a34a) }}
    .me-info {{ font-size:0.8rem; margin-bottom:12px; padding:10px; background:rgba(34,197,94,0.1); border-radius:10px; color:var(--text-muted) }}
    @media(max-width:768px) {{
        .me-stats {{ grid-template-columns:repeat(2,1fr) }}
        .me-stat-num {{ font-size:1.1rem }}
        .me-bottom {{ padding:10px 6px }}
        .me-bottom-row {{ gap:6px }}
        .me-btn {{ padding:12px 8px; font-size:0.85rem; min-height:44px }}
    }}
    @media(max-width:480px) {{
        .me-stats {{ grid-template-columns:repeat(2,1fr); gap:6px }}
    }}
    </style>
    <div class="container">
        <div class="header">
            <h1>✏️ Masowa edycja cen</h1>
            <small>{paleta['nazwa'] or f"Paleta #{paleta_id}"}</small>
        </div>

        <div class="me-stats">
            <div class="me-stat">
                <div class="me-stat-num" style="color:var(--accent-blue)">{stats['total']}</div>
                <div class="me-stat-label">WSZYSTKICH</div>
            </div>
            <div class="me-stat">
                <div class="me-stat-num" style="color:var(--accent-green)">{stats['wystawione']}</div>
                <div class="me-stat-label">WYSTAWIONE</div>
            </div>
            <div class="me-stat">
                <div class="me-stat-num" style="color:var(--accent-blue)" id="count-selected">{wybrane_count}</div>
                <div class="me-stat-label">ZAZNACZONE</div>
            </div>
            <div class="me-stat">
                <div class="me-stat-num" style="color:var(--accent-green)" id="value-total">{stats['wartosc_total']:.0f} zł</div>
                <div class="me-stat-label">WARTOŚĆ</div>
            </div>
        </div>

        <div class="me-info">
            💡 Zaznacz produkty → edytuj ceny → kliknij <b>Wystaw</b>. Wystawione (zielone) nie można zaznaczyć.
        </div>

        <div id="products-list" style="padding-bottom:140px">
            {produkty_html}
        </div>

        <div class="me-bottom">
            <div class="me-bottom-inner">
                <div class="me-bottom-row">
                    <a href="/palety/{paleta_id}" class="me-btn me-btn-back">← Powrót</a>
                    <button id="btn-select-all" class="me-btn" style="background:#334155" onclick="toggleSelectAll()">
                        ☑️ Zaznacz wszystkie
                    </button>
                </div>
                <div class="me-bottom-row">
                    <button id="btn-batch-meta" class="me-btn me-btn-meta" onclick="batchGenerateMetaTitles()">
                        ✨ Generuj META (<span id="count-meta-btn">{wybrane_count}</span>)
                    </button>
                    <button id="btn-wystaw" class="me-btn me-btn-wystaw" onclick="wystawZaznaczone()">
                        🚀 Wystaw (<span id="count-btn">{wybrane_count}</span>)
                    </button>
                </div>
            </div>
        </div>
    </div>
    
    <script>
    function updateCounter() {{
        const checkboxes = document.querySelectorAll('.product-checkbox:checked:not(:disabled)');
        const count = checkboxes.length;
        document.getElementById('count-selected').textContent = count;
        document.getElementById('count-btn').textContent = count;
        document.getElementById('count-meta-btn').textContent = count;
        document.getElementById('btn-wystaw').disabled = count === 0;
        document.getElementById('btn-batch-meta').disabled = count === 0;
        
        let total = 0;
        checkboxes.forEach(cb => {{
            const productId = cb.dataset.productId;
            const priceInput = document.querySelector('.price-input[data-product-id="' + productId + '"]');
            if (priceInput) {{
                total += parseFloat(priceInput.value) || 0;
            }}
        }});
        document.getElementById('value-total').textContent = total.toFixed(0) + ' zł';
    }}
    
    const priceInputs = document.querySelectorAll('.price-input');
    priceInputs.forEach(input => {{
        let timeout;
        input.addEventListener('input', function() {{
            clearTimeout(timeout);
            const productId = this.dataset.productId;
            const newPrice = parseFloat(this.value) || 0;
            this.style.borderColor = '#eab308';
            
            timeout = setTimeout(() => {{
                fetch('/palety/api/update-price', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        product_id: productId,
                        price: newPrice
                    }})
                }})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        input.style.borderColor = '#22c55e';
                        setTimeout(() => input.style.borderColor = '', 1000);
                        updateCounter();
                    }} else {{
                        input.style.borderColor = '#ef4444';
                        alert('Błąd zapisu: ' + data.error);
                    }}
                }})
                .catch(err => {{
                    input.style.borderColor = '#ef4444';
                    console.error('Error:', err);
                }});
            }}, 800);
        }});
    }});
    
    const checkboxes = document.querySelectorAll('.product-checkbox');
    checkboxes.forEach(cb => {{
        cb.addEventListener('change', updateCounter);
    }});
    
    function wystawZaznaczone() {{
        const checked = document.querySelectorAll('.product-checkbox:checked:not(:disabled)');
        if (checked.length === 0) {{
            alert('Zaznacz przynajmniej 1 produkt!');
            return;
        }}
        const productIds = Array.from(checked).map(cb => cb.value);
        window.location.href = '/paletomat/generator/mass-create-from-paleta?paleta_id={paleta_id}&ids=' + productIds.join(',');
    }}
    
    function batchGenerateMetaTitles() {{
        const checked = document.querySelectorAll('.product-checkbox:checked:not(:disabled)');
        if (checked.length === 0) {{
            alert('Zaznacz przynajmniej 1 produkt!');
            return;
        }}
        
        // BATCH LIMIT (zwiększony dla paid tier)
        const MAX_BATCH = 100;  // Zwiększone z 10 na 100
        if (checked.length > MAX_BATCH) {{
            alert(`❌ Zbyt dużo produktów!\\n\\nZaznaczono: ${{checked.length}}\\nMax: ${{MAX_BATCH}}\\n\\nZaznacz mniej produktów lub podziel na mniejsze batche.`);
            return;
        }}
        
        // Oblicz czas (5s delay na produkt dla bezpieczeństwa)
        const estimatedTime = checked.length * 5;
        const minutes = Math.floor(estimatedTime / 60);
        const seconds = estimatedTime % 60;
        const timeStr = minutes > 0 ? `${{minutes}}min ${{seconds}}s` : `${{seconds}}s`;
        
        if (!confirm(`🤖 Wygenerować META TITLE dla ${{checked.length}} produktów?\\n\\n⏱️  Szacowany czas: ~${{timeStr}}\\n⚠️  5s opóźnienie między produktami (safe rate limiting)\\n\\nKontynuować?`)) {{
            return;
        }}
        
        const productIds = Array.from(checked).map(cb => cb.value);
        const button = document.getElementById('btn-batch-meta');
        const originalText = button.innerHTML;
        
        // Disable button and show progress
        button.disabled = true;
        button.innerHTML = '⏳ Generuję 0/' + productIds.length + '... (może zająć ~' + timeStr + ')';
        
        fetch('/api/generate_meta_title_batch', {{
            method: 'POST',
            mode: 'cors',
            credentials: 'omit',
            headers: {{ 
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }},
            body: JSON.stringify({{ product_ids: productIds }})
        }})
        .then(res => res.json())
        .then(data => {{
            if (data.success) {{
                // Sprawdź czy były błędy quota
                const quotaErrors = data.details ? data.details.filter(d => d.error && d.error.includes('Quota')).length : 0;
                
                let msg = `✅ Gotowe!\\n\\nWygenerowano: ${{data.generated}}\\nBłędy: ${{data.failed}}`;
                
                if (quotaErrors > 0) {{
                    msg += `\\n\\n⚠️  Quota exceeded!\\nPoczekaj do jutra (reset o 9:00 AM)\\nlub upgrade do paid tier.`;
                }}
                
                alert(msg);
                location.reload();
            }} else {{
                // Lepsze error messages
                let errorMsg = data.error || 'Nieznany błąd';
                
                if (errorMsg.includes('Zbyt dużo')) {{
                    errorMsg = `❌ ${{errorMsg}}\\n\\nTIP: Zaznacz max 10 produktów lub poczekaj do jutra na reset quota.`;
                }}
                
                alert('❌ Błąd:\\n\\n' + errorMsg);
                button.disabled = false;
                button.innerHTML = originalText;
            }}
        }})
        .catch(err => {{
            alert('❌ Błąd połączenia:\\n\\n' + err + '\\n\\nSprawdź console (F12) dla szczegółów.');
            button.disabled = false;
            button.innerHTML = originalText;
        }});
    }}
    
    function toggleSelectAll() {{
        const checkboxes = document.querySelectorAll('.product-checkbox:not(:disabled)');
        const allChecked = Array.from(checkboxes).every(cb => cb.checked);
        checkboxes.forEach(cb => {{ cb.checked = !allChecked; }});
        const btn = document.getElementById('btn-select-all');
        btn.innerHTML = allChecked ? '☑️ Zaznacz wszystkie' : '☐ Odznacz wszystkie';
        updateCounter();
    }}

    function regenerateMetaTitle(productId, button) {{
        const originalText = button.innerHTML;
        button.disabled = true;
        button.innerHTML = '⏳ Generuję...';
        
        fetch('/produkty/' + productId + '/regenerate-meta-title', {{
            method: 'POST',
            mode: 'cors',
            credentials: 'omit',
            headers: {{ 
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }}
        }})
        .then(res => res.json())
        .then(data => {{
            if (data.success) {{
                // Odśwież stronę aby pokazać nowy META TITLE
                location.reload();
            }} else {{
                alert('Błąd: ' + (data.error || 'Nieznany błąd'));
                button.disabled = false;
                button.innerHTML = originalText;
            }}
        }})
        .catch(err => {{
            alert('Błąd połączenia: ' + err);
            button.disabled = false;
            button.innerHTML = originalText;
        }});
    }}
    
    updateCounter();
    </script>
    '''
    
    return html


@app.route('/palety/api/update-price', methods=['POST'])
def api_update_price():
    """API do aktualizacji ceny produktu"""
    from modules.database import get_db
    
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        new_price = float(data.get('price', 0))
        
        if not product_id or new_price < 0:
            return jsonify({'success': False, 'error': 'Nieprawidłowe dane'})
        
        conn = get_db()
        
        # Pobierz starą cenę do historii
        old_product = conn.execute('SELECT cena_allegro, paleta_id FROM produkty WHERE id = ?', (product_id,)).fetchone()
        old_price = old_product['cena_allegro'] if old_product else 0
        
        conn.execute('UPDATE produkty SET cena_allegro = ? WHERE id = ?', (new_price, product_id))
        
        # Dodaj do historii jeśli cena się zmieniła
        if old_price != new_price:
            from modules.database import add_historia
            add_historia(product_id, 'zmiana_ceny', f'Zmiana ceny Allegro: {old_price:.0f} → {new_price:.0f} zł', 
                {'stara_cena': old_price, 'nowa_cena': new_price})
        
        product = conn.execute('SELECT paleta_id FROM produkty WHERE id = ?', (product_id,)).fetchone()
        
        if product and product['paleta_id']:
            stats = conn.execute('''
                SELECT COALESCE(SUM(cena_allegro * ilosc), 0) as total
                FROM produkty WHERE paleta_id = ?
            ''', (product['paleta_id'],)).fetchone()
            conn.commit()
            return jsonify({'success': True, 'new_total': float(stats['total'])})
        
        conn.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"Error updating price: {e}")
        return jsonify({'success': False, 'error': str(e)})


# ============================================================
# WAREHOUSE HEATMAP - 3D VISUALIZATION
# ============================================================

# WAREHOUSE EDITOR ROUTES
@app.route('/warehouse/editor')
def warehouse_editor():
    """Visual editor for warehouse layout"""
    return render_template('warehouse_editor.html')


@app.route('/api/warehouse/layout/save', methods=['POST'])
def save_warehouse_layout():
    """Save warehouse layout to JSON file"""
    try:
        layout = request.json
        
        print("=" * 60)
        print("📥 SAVE LAYOUT REQUEST")
        print(f"Received data: {layout is not None}")
        
        # Validate
        if not layout or 'shelves' not in layout:
            print("❌ Invalid layout - missing shelves")
            return jsonify({'error': 'Invalid layout'}), 400
        
        print(f"✅ Valid layout with {len(layout['shelves'])} shelves")
        
        # Save to file - ABSOLUTE PATH
        app_dir = os.path.dirname(os.path.abspath(__file__))
        layout_path = os.path.join(app_dir, 'warehouse_layout.json')
        
        print(f"📁 Saving to: {layout_path}")
        
        with open(layout_path, 'w', encoding='utf-8') as f:
            json.dump(layout, f, indent=2, ensure_ascii=False)
        
        # Verify file exists
        if os.path.exists(layout_path):
            file_size = os.path.getsize(layout_path)
            print(f"✅ File saved successfully! Size: {file_size} bytes")
        else:
            print("❌ File NOT saved!")
            return jsonify({'error': 'File save failed'}), 500
        
        print("=" * 60)
        
        return jsonify({
            'success': True,
            'message': 'Layout saved successfully',
            'path': layout_path,
            'shelves_count': len(layout['shelves'])
        })
        
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/warehouse/layout/load', methods=['GET'])
def load_warehouse_layout():
    """Load warehouse layout from JSON file"""
    try:
        layout_path = os.path.join(os.path.dirname(__file__), 'warehouse_layout.json')
        
        if not os.path.exists(layout_path):
            return jsonify({'error': 'No layout found'}), 404
        
        with open(layout_path, 'r') as f:
            layout = json.load(f)
        
        return jsonify(layout)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/warehouse/layout/validate', methods=['POST'])
def validate_warehouse_layout():
    """Validate warehouse layout structure"""
    try:
        layout = request.json
        
        # Basic validation
        errors = []
        
        if 'shelves' not in layout:
            errors.append('Missing shelves array')
        elif not isinstance(layout['shelves'], list):
            errors.append('Shelves must be an array')
        else:
            # Check each shelf
            for i, shelf in enumerate(layout['shelves']):
                required = ['letter', 'x', 'y', 'shelfHeight', 'levels']
                for field in required:
                    if field not in shelf:
                        errors.append(f'Shelf {i}: missing {field}')
        
        if errors:
            return jsonify({'valid': False, 'errors': errors}), 400
        
        return jsonify({'valid': True, 'message': 'Layout is valid'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# WAREHOUSE HEATMAP ROUTES
@app.route('/warehouse/shelves')
def warehouse_shelves_map():
    """Interaktywna mapa regalow — zoptymalizowana pod telefon.
    Uzywa WAREHOUSE_CONFIG (sekcje) jako zrodlo prawdy o ukladzie magazynu."""
    import json as _json
    from modules.warehouse_heatmap import get_heatmap_data, WAREHOUSE_CONFIG as WH_CFG

    heatmap = get_heatmap_data()
    shelves_data = heatmap.get('shelves', {})

    # Uzyj WAREHOUSE_CONFIG jako zrodla prawdy (nie warehouse_layout.json)
    wh_sections = WH_CFG.get('sections', {})
    wh_colors = WH_CFG.get('section_colors', {})
    config_shelves = WH_CFG.get('shelves', [])

    # Przygotuj dane regalow dla JS — TYLKO regaly z WAREHOUSE_CONFIG
    shelves_js = {}
    shelf_letters_ordered = []
    for rack_key in config_shelves:
        shelf_letters_ordered.append(rack_key)
        levels = shelves_data.get(rack_key, [])
        if not isinstance(levels, list):
            levels = []
        total = sum(lv.get('items', 0) for lv in levels)
        shelves_js[rack_key] = {
            'levels': levels,
            'total_items': total
        }

    # Grupuj wg sekcji z WAREHOUSE_CONFIG
    wall_groups = {}
    for sec_letter, sec_data in wh_sections.items():
        group_name = f"{sec_letter} \u2014 {sec_data['name']}"
        wall_groups[group_name] = sec_data['racks']  # Wszystkie regaly z sekcji

    total_shelves = len(config_shelves)
    total_items = sum(s['total_items'] for s in shelves_js.values())
    empty_shelves = sum(1 for s in shelves_js.values() if s['total_items'] == 0)
    occupied = total_shelves - empty_shelves

    html = '''<!DOCTYPE html><html lang="pl"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>Mapa Magazynu</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;padding-bottom:80px}
.top{position:sticky;top:0;z-index:100;background:#1e293b;padding:12px 16px;border-bottom:1px solid #334155;display:flex;align-items:center;gap:12px}
.top h1{font-size:1.1rem;flex:1}
.top a{color:#94a3b8;text-decoration:none;font-size:1.2rem}
.stats-row{display:flex;gap:8px;padding:12px 16px;overflow-x:auto}
.stat{flex:0 0 auto;background:#1e293b;border-radius:10px;padding:10px 14px;text-align:center;min-width:80px}
.stat .n{font-size:1.3rem;font-weight:700;color:#22c55e}
.stat .l{font-size:0.65rem;color:#94a3b8}
.section-label{padding:8px 16px;font-size:0.75rem;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:1px}
.shelves-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(100px,1fr));gap:8px;padding:0 12px 12px}
.shelf-card{background:#1e293b;border-radius:12px;padding:12px 8px;text-align:center;cursor:pointer;border:2px solid transparent;transition:all 0.2s}
.shelf-card:active{transform:scale(0.95)}
.shelf-card .letter{font-size:1.6rem;font-weight:800}
.shelf-card .cnt{font-size:0.7rem;color:#94a3b8;margin-top:2px}
.shelf-card .levels-cnt{font-size:0.6rem;color:#475569;margin-top:1px}
.shelf-card .bar{height:4px;background:#334155;border-radius:2px;margin-top:6px;overflow:hidden}
.shelf-card .bar-fill{height:100%;border-radius:2px;transition:width 0.3s}
.panel-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);z-index:200}
.panel-overlay.open{display:block}
.panel{position:fixed;bottom:0;left:0;right:0;max-height:85vh;background:#1e293b;border-radius:20px 20px 0 0;z-index:201;overflow-y:auto;transform:translateY(100%);transition:transform 0.3s ease}
.panel.open{transform:translateY(0)}
.panel-handle{width:40px;height:4px;background:#475569;border-radius:2px;margin:10px auto}
.panel-header{padding:0 16px 12px;display:flex;align-items:center;gap:12px;border-bottom:1px solid #334155}
.panel-header h2{flex:1;font-size:1.2rem}
.panel-close{background:none;border:none;color:#94a3b8;font-size:1.5rem;cursor:pointer;padding:4px 8px}
.level-row{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid #33415522;cursor:pointer}
.level-row:active{background:#334155}
.level-badge{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:0.9rem;color:#fff}
.level-info{flex:1}
.level-info .code{font-weight:600;font-size:0.9rem}
.level-info .detail{font-size:0.7rem;color:#94a3b8}
.level-arrow{color:#475569;font-size:1.2rem}
.products-list{padding:8px 16px 20px}
.prod-item{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid #33415544;text-decoration:none;color:inherit}
.prod-item:active{background:#33415533}
.prod-img{width:50px;height:50px;border-radius:8px;object-fit:cover;background:#334155}
.prod-img-placeholder{width:50px;height:50px;border-radius:8px;background:#334155;display:flex;align-items:center;justify-content:center;font-size:1.2rem}
.prod-info{flex:1;min-width:0}
.prod-info .name{font-size:0.8rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.prod-info .meta{font-size:0.65rem;color:#94a3b8}
.prod-qty{font-weight:700;color:#22c55e;font-size:0.9rem;white-space:nowrap}
.bottom-bar{position:fixed;bottom:0;left:0;right:0;background:#1e293b;border-top:1px solid #334155;padding:8px 16px;display:flex;gap:8px;z-index:50}
.bottom-bar a{flex:1;text-align:center;padding:10px;border-radius:10px;text-decoration:none;color:#e2e8f0;font-size:0.75rem;font-weight:600}
.bb-print{background:#7c3aed}
.bb-heat{background:#0ea5e9}
.bb-back{background:#334155}
.empty-msg{text-align:center;padding:30px;color:#64748b;font-size:0.85rem}
.back-btn{display:inline-block;padding:6px 14px;background:#334155;border-radius:8px;color:#e2e8f0;font-size:0.75rem;text-decoration:none;margin:8px 16px}
</style></head><body>

<div class="top">
    <a href="/magazyn">&#8592;</a>
    <h1>Mapa Magazynu</h1>
    <a href="/warehouse/print-labels">&#128424;</a>
</div>

<div class="stats-row">'''

    html += f'''
    <div class="stat"><div class="n">{total_shelves}</div><div class="l">Regalow</div></div>
    <div class="stat"><div class="n" style="color:#3b82f6">{total_items}</div><div class="l">Produktow</div></div>
    <div class="stat"><div class="n" style="color:#22c55e">{empty_shelves}</div><div class="l">Pustych</div></div>
    <div class="stat"><div class="n" style="color:#ef4444">{total_shelves - empty_shelves}</div><div class="l">Zajetych</div></div>
</div>

<div style="padding:8px 12px">
    <div style="position:relative">
        <input type="text" id="searchInput" placeholder="Szukaj (nazwa, EAN, ASIN, kod mag...)"
            style="width:100%;padding:12px 16px 12px 40px;background:#1e293b;border:2px solid #334155;border-radius:12px;color:#e2e8f0;font-size:0.9rem;outline:none"
            oninput="searchProducts(this.value)" onfocus="this.style.borderColor='#7c3aed'" onblur="this.style.borderColor='#334155'">
        <span style="position:absolute;left:14px;top:50%;transform:translateY(-50%);color:#64748b">&#128269;</span>
    </div>
    <div id="searchResults" style="display:none;margin-top:8px;background:#1e293b;border-radius:12px;overflow:hidden;max-height:50vh;overflow-y:auto"></div>
</div>'''

    def get_shelf_color(letter):
        s = shelves_js.get(letter, {})
        levels = s.get('levels', [])
        if not levels:
            return '#22c55e'
        max_fill = max((lv.get('fill_percentage', 0) for lv in levels), default=0)
        if max_fill == 0: return '#22c55e'
        elif max_fill < 25: return '#3b82f6'
        elif max_fill < 50: return '#f59e0b'
        elif max_fill < 75: return '#fb923c'
        else: return '#ef4444'

    def render_shelf_card(letter):
        s = shelves_js.get(letter, {'total_items': 0, 'levels': []})
        items = s['total_items']
        color = get_shelf_color(letter)
        n_levels = len(s.get('levels', []))
        max_fill = max((lv.get('fill_percentage', 0) for lv in s.get('levels', [])), default=0)
        return f'''<div class="shelf-card" onclick="openShelf('{letter}')" style="border-color:{color}40">
            <div class="letter" style="color:{color}">{letter}</div>
            <div class="cnt">{items} szt</div>
            <div class="levels-cnt">{n_levels} polek</div>
            <div class="bar"><div class="bar-fill" style="width:{min(max_fill,100)}%;background:{color}"></div></div>
        </div>'''

    # Render wg grup scian (z custom layout) lub wszystkie naraz
    if wall_groups:
        for wall_name, letters in wall_groups.items():
            html += f'<div class="section-label">{wall_name}</div><div class="shelves-grid">'
            for letter in letters:
                html += render_shelf_card(letter)
            html += '</div>'
    else:
        html += '<div class="section-label">Wszystkie regaly</div><div class="shelves-grid">'
        for letter in shelf_letters_ordered:
            html += render_shelf_card(letter)
        html += '</div>'

    # Bottom sheet panel + JS
    shelves_json = _json.dumps(shelves_js, ensure_ascii=False)

    html += '''
<div class="panel-overlay" id="panelOverlay" onclick="closePanel()"></div>
<div class="panel" id="shelfPanel">
    <div class="panel-handle"></div>
    <div class="panel-header">
        <h2 id="panelTitle">Regal</h2>
        <button class="panel-close" onclick="closePanel()">&times;</button>
    </div>
    <div id="panelContent"></div>
</div>

<div class="bottom-bar">
    <a href="/magazyn" class="bb-back">Magazyn</a>
    <a href="/warehouse/print-labels" class="bb-print">Drukuj kartki</a>
    <a href="/warehouse/heatmap" class="bb-heat">Heatmapa 3D</a>
</div>

<script>
const shelvesData = ''' + shelves_json + ''';

function openShelf(letter) {
    var shelf = shelvesData[letter];
    document.getElementById("panelTitle").textContent = "Regal " + letter;
    var h = "";
    if (!shelf || !shelf.levels || shelf.levels.length === 0) {
        h = '<div class="empty-msg">Brak polek</div>';
    } else {
        var levels = shelf.levels.slice().sort(function(a,b){return a.level - b.level});
        for (var i = 0; i < levels.length; i++) {
            var lv = levels[i];
            var c = lv.color || "#64748b";
            h += '<div class="level-row" onclick="openLevel(\\x27' + lv.code + '\\x27)">';
            h += '<div class="level-badge" style="background:' + c + '">' + lv.level + '</div>';
            h += '<div class="level-info"><div class="code">' + lv.code + '</div>';
            h += '<div class="detail">' + lv.items + ' szt / ' + lv.capacity + ' max</div></div>';
            h += '<div class="level-arrow">&#8250;</div></div>';
        }
    }
    document.getElementById("panelContent").innerHTML = h;
    document.getElementById("panelOverlay").classList.add("open");
    document.getElementById("shelfPanel").classList.add("open");
}

function closePanel() {
    document.getElementById("panelOverlay").classList.remove("open");
    document.getElementById("shelfPanel").classList.remove("open");
}

function openLevel(code) {
    document.getElementById("panelTitle").textContent = "Polka " + code;
    document.getElementById("panelContent").innerHTML = '<div class="empty-msg">Ladowanie...</div>';

    fetch("/api/warehouse/location/" + code)
        .then(function(r){return r.json()})
        .then(function(data) {
            if (data.error) {
                document.getElementById("panelContent").innerHTML = '<div class="empty-msg">' + data.error + '</div>';
                return;
            }
            var h = "";
            var products = data.products || [];
            if (products.length === 0) {
                h = '<div class="empty-msg">Polka pusta</div>';
            } else {
                var shelfCode = code.replace(/\\d+$/, '').length > 1 ? code.slice(0, -1) : code.charAt(0);
                // Znajdz regal w shelvesData
                for (var sk in shelvesData) { if (shelvesData[sk].levels) { for (var li=0;li<shelvesData[sk].levels.length;li++) { if (shelvesData[sk].levels[li].code === code) { shelfCode = sk; break; }}}}
                h = '<a class="back-btn" href="javascript:void(0)" onclick="openShelf(\\x27' + shelfCode + '\\x27)">&#8592; Wroc do regalu</a>';
                h += '<div class="products-list">';
                for (var i = 0; i < products.length; i++) {
                    var p = products[i];
                    var img = p.zdjecie_url
                        ? '<img class="prod-img" src="' + p.zdjecie_url + '" onerror="this.style.display=\\x27none\\x27">'
                        : '<div class="prod-img-placeholder">&#128230;</div>';
                    var name = (p.nazwa || "Brak nazwy").substring(0, 60);
                    var ean = p.ean || p.asin || "";
                    h += '<a href="/magazyn/produkt/' + p.id + '" class="prod-item">';
                    h += img;
                    h += '<div class="prod-info"><div class="name">' + name + '</div>';
                    h += '<div class="meta">' + ean + '</div></div>';
                    h += '<div class="prod-qty">' + (p.ilosc || 0) + ' szt</div>';
                    h += '</a>';
                }
                h += '</div>';
            }
            document.getElementById("panelContent").innerHTML = h;
        })
        .catch(function(err) {
            document.getElementById("panelContent").innerHTML = '<div class="empty-msg">Blad: ' + err + '</div>';
        });
}

var searchTimer = null;
function searchProducts(q) {
    var box = document.getElementById("searchResults");
    if (!q || q.length < 2) {
        box.style.display = "none";
        box.innerHTML = "";
        return;
    }
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function() {
        fetch("/api/warehouse/search-product?q=" + encodeURIComponent(q))
            .then(function(r){return r.json()})
            .then(function(data) {
                var results = data.results || [];
                if (results.length === 0) {
                    box.innerHTML = '<div style="padding:16px;color:#64748b;text-align:center">Nie znaleziono</div>';
                    box.style.display = "block";
                    return;
                }
                var h = "";
                for (var i = 0; i < results.length; i++) {
                    var p = results[i];
                    var img = p.zdjecie_url
                        ? '<img style="width:40px;height:40px;border-radius:8px;object-fit:cover;background:#334155" src="'+p.zdjecie_url+'" onerror="this.style.display=\\x27none\\x27">'
                        : '<div style="width:40px;height:40px;border-radius:8px;background:#334155;display:flex;align-items:center;justify-content:center">&#128230;</div>';
                    var name = (p.nazwa || "?").substring(0, 50);
                    var loc = p.lokalizacja || "brak";
                    var ean = p.ean || p.asin || "";
                    h += '<a href="/magazyn/produkt/'+p.id+'" style="display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid #33415544;text-decoration:none;color:#e2e8f0">';
                    h += img;
                    h += '<div style="flex:1;min-width:0"><div style="font-size:0.8rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'+name+'</div>';
                    h += '<div style="font-size:0.65rem;color:#94a3b8">'+ean+'</div></div>';
                    h += '<div style="text-align:right;flex-shrink:0"><div style="font-weight:700;color:#22c55e;font-size:0.85rem">'+(p.ilosc||0)+' szt</div>';
                    h += '<div style="font-size:0.65rem;color:#a78bfa;font-weight:600">'+loc+'</div></div>';
                    h += '</a>';
                }
                box.innerHTML = h;
                box.style.display = "block";
            });
    }, 300);
}
</script>
</body></html>'''

    return html


@app.route('/warehouse/shelf/<code>')
def warehouse_shelf_view(code):
    """Widok regalu po zeskanowaniu QR — mobile friendly.
    /warehouse/shelf/A1 pokaze polki regalu A1 (A11, A12...).
    Klikniecie polki laduje produkty z API."""
    from modules.warehouse_heatmap import get_heatmap_data
    code = code.upper().strip()

    heatmap = get_heatmap_data()
    shelves_data = heatmap.get('shelves', {})

    # Szukaj dokladnego klucza (np. "A1") w shelves_data
    all_levels = []
    if code in shelves_data and isinstance(shelves_data[code], list):
        all_levels = shelves_data[code]
    else:
        # Fallback: szukaj po pierwszej literze
        for key, levels in shelves_data.items():
            if key == code and isinstance(levels, list):
                all_levels = levels
                break

    all_levels = sorted(all_levels, key=lambda l: l.get('level', 0))
    total_items = sum(lv.get('items', 0) for lv in all_levels)

    html = f'''<!DOCTYPE html><html lang="pl"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>Regal {code}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;padding-bottom:70px}}
.header{{background:#1e293b;padding:16px;text-align:center;border-bottom:1px solid #334155}}
.header h1{{font-size:2.5rem;font-weight:800;color:#22c55e}}
.header .sub{{color:#94a3b8;font-size:0.85rem;margin-top:4px}}
.count-badge{{display:inline-block;background:#22c55e22;color:#22c55e;padding:6px 16px;border-radius:20px;font-weight:700;font-size:0.9rem;margin:12px 0}}
.levels{{padding:12px}}
.level-card{{display:flex;align-items:center;gap:12px;padding:14px;background:#1e293b;border-radius:12px;margin-bottom:8px;cursor:pointer;border:2px solid #33415544}}
.level-card:active{{background:#334155}}
.level-badge{{width:48px;height:48px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:1.1rem;color:#fff;flex-shrink:0}}
.level-title{{flex:1}}
.level-title .name{{font-weight:700;font-size:1rem}}
.level-title .info{{font-size:0.75rem;color:#94a3b8;margin-top:2px}}
.level-arrow{{color:#475569;font-size:1.5rem}}
.products-panel{{display:none;background:#161e2e;border-radius:0 0 12px 12px;margin-top:-8px;margin-bottom:8px;padding:8px;overflow:hidden}}
.products-panel.open{{display:block}}
.prod-card{{display:flex;align-items:center;gap:10px;padding:10px;border-bottom:1px solid #33415533;text-decoration:none;color:inherit}}
.prod-card:active{{background:#334155}}
.prod-img{{width:45px;height:45px;border-radius:8px;object-fit:cover;background:#334155;flex-shrink:0}}
.prod-placeholder{{width:45px;height:45px;border-radius:8px;background:#334155;display:flex;align-items:center;justify-content:center;font-size:1rem;flex-shrink:0}}
.prod-info{{flex:1;min-width:0}}
.prod-name{{font-size:0.8rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.prod-meta{{font-size:0.65rem;color:#94a3b8}}
.prod-qty{{font-weight:800;color:#22c55e;font-size:0.95rem;flex-shrink:0}}
.empty{{text-align:center;padding:30px 20px;color:#64748b;font-size:0.85rem}}
.loading{{text-align:center;padding:20px;color:#64748b}}
.nav-bar{{position:fixed;bottom:0;left:0;right:0;background:#1e293b;border-top:1px solid #334155;padding:8px 12px;display:flex;gap:8px}}
.nav-bar a{{flex:1;text-align:center;padding:10px;border-radius:10px;text-decoration:none;color:#e2e8f0;font-size:0.75rem;font-weight:600;background:#334155}}
.nav-bar a.primary{{background:#7c3aed}}
</style></head><body>

<div class="header">
    <h1>Regal {code}</h1>
    <div class="sub">{len(all_levels)} polek</div>
    <div class="count-badge">{total_items} produktow</div>
</div>

<div class="levels">'''

    if not all_levels:
        html += '<div class="empty">Brak polek w tym regale</div>'
    else:
        for lv in all_levels:
            lv_code = lv.get('code', '?')
            lv_level = lv.get('level', 0)
            lv_items = lv.get('items', 0)
            lv_capacity = lv.get('capacity', 50)
            lv_color = lv.get('color', '#64748b')
            lv_fill = lv.get('fill_percentage', 0)
            status_text = f'{lv_items} szt' if lv_items > 0 else 'pusta'

            html += f'''<div class="level-card" onclick="toggleLevel(this, '{lv_code}')">
    <div class="level-badge" style="background:{lv_color}">{lv_level}</div>
    <div class="level-title">
        <div class="name">Polka {lv_code}</div>
        <div class="info">{status_text} / {lv_capacity} max</div>
    </div>
    <div class="level-arrow" id="arrow-{lv_code}">&#8250;</div>
</div>
<div class="products-panel" id="panel-{lv_code}"></div>'''

    html += '''</div>

<script>
var openPanels = {};

function toggleLevel(card, code) {
    var panel = document.getElementById("panel-" + code);
    var arrow = document.getElementById("arrow-" + code);

    if (panel.classList.contains("open")) {
        panel.classList.remove("open");
        arrow.style.transform = "";
        return;
    }

    arrow.style.transform = "rotate(90deg)";
    panel.classList.add("open");

    // Jesli juz zaladowane — nie laduj ponownie
    if (openPanels[code]) return;

    panel.innerHTML = '<div class="loading">Ladowanie...</div>';

    fetch("/api/warehouse/location/" + code)
        .then(function(r){return r.json()})
        .then(function(data) {
            var h = "";
            var products = data.products || [];
            if (products.length === 0) {
                h = '<div class="empty">Polka pusta</div>';
            } else {
                for (var i = 0; i < products.length; i++) {
                    var p = products[i];
                    var img = p.zdjecie_url
                        ? '<img class="prod-img" src="'+p.zdjecie_url+'" onerror="this.style.display=\\x27none\\x27">'
                        : '<div class="prod-placeholder">&#128230;</div>';
                    var name = (p.nazwa || "Brak nazwy").substring(0, 60);
                    var ean = p.ean || p.asin || "";
                    h += '<a href="/magazyn/produkt/'+p.id+'" class="prod-card">';
                    h += img;
                    h += '<div class="prod-info"><div class="prod-name">'+name+'</div>';
                    h += '<div class="prod-meta">'+ean+'</div></div>';
                    h += '<div class="prod-qty">'+(p.ilosc||0)+' szt</div>';
                    h += '</a>';
                }
            }
            panel.innerHTML = h;
            openPanels[code] = true;
        })
        .catch(function(err) {
            panel.innerHTML = '<div class="empty">Blad: '+err+'</div>';
        });
}
</script>

<div class="nav-bar">
    <a href="/warehouse/shelves">Mapa</a>
    <a href="/magazyn" class="primary">Magazyn</a>
</div>
</body></html>'''

    return html


@app.route('/warehouse/print-labels')
def warehouse_print_labels():
    """Drukuje kartki z QR kodami do kazdego regalu/polki"""
    from modules.warehouse_heatmap import get_heatmap_data, WAREHOUSE_CONFIG as WH_CFG

    heatmap = get_heatmap_data()
    shelves_data = heatmap.get('shelves', {})
    # Uzyj TYLKO regalow z WAREHOUSE_CONFIG (nie z warehouse_layout.json)
    config_shelves = WH_CFG.get('shelves', [])
    base_url = request.host_url.rstrip('/')

    html = '''<!DOCTYPE html><html lang="pl"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Kartki do regalow — Druk</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',sans-serif;background:#fff;color:#000}
@media screen {
    body{background:#0f172a;color:#e2e8f0;padding:20px}
    .no-print{display:block}
    .label-card{background:#1e293b;color:#e2e8f0;border:2px solid #334155}
}
@media print {
    .no-print{display:none !important}
    body{background:#fff;padding:0}
    .label-card{break-inside:avoid;border:2px solid #000}
}
.no-print{text-align:center;margin-bottom:20px}
.no-print h1{font-size:1.5rem;margin-bottom:10px;color:#e2e8f0}
.no-print button{padding:12px 30px;background:#7c3aed;color:#fff;border:none;border-radius:10px;font-size:1rem;font-weight:700;cursor:pointer}
.no-print button:active{transform:scale(0.95)}
.no-print a{color:#94a3b8;text-decoration:none;display:inline-block;margin:10px}
.controls{display:flex;gap:8px;justify-content:center;margin:12px 0;flex-wrap:wrap}
.controls label{background:#334155;padding:6px 12px;border-radius:8px;font-size:0.8rem;cursor:pointer;user-select:none}
.controls input[type=checkbox]{margin-right:4px}
.labels-grid{padding:10px}
.label-card{border-radius:16px;padding:40px 20px;text-align:center;page-break-after:always;min-height:90vh;display:flex;flex-direction:column;align-items:center;justify-content:center}
.label-card:last-child{page-break-after:auto}
.label-card .shelf-name{font-size:8rem;font-weight:900;margin-bottom:10px;line-height:1}
.label-card .shelf-sub{font-size:1.5rem;color:#666;margin-bottom:30px}
.label-card .qr-placeholder{margin:20px auto;width:300px;height:300px;display:flex;align-items:center;justify-content:center}
.label-card .qr-placeholder img{width:300px;height:300px}
@media print{
    .label-card .shelf-name{color:#000}
    .label-card .shelf-sub{color:#333}
    .label-card{border:3px solid #000;min-height:95vh}
}
</style></head><body>

<div class="no-print">
    <h1>Kartki z QR kodami do regalow</h1>
    <p style="color:#94a3b8;margin-bottom:12px">Wydrukuj i przyklej do regalu. Zeskanuj telefonem — zobaczysz co jest na polce.</p>
    <div class="controls" id="shelfFilter"></div>
    <button onclick="window.print()">Drukuj</button>
    <br><a href="/warehouse/shelves">Wroc do mapy</a>
</div>

<div class="labels-grid" id="labelsGrid">'''

    # ===== STRONA 1: LEGENDA DYNAMICZNA (z WAREHOUSE_CONFIG) =====
    from modules.warehouse_heatmap import WAREHOUSE_CONFIG as WH_CFG
    sections = WH_CFG.get('sections', {})
    section_colors = WH_CFG.get('section_colors', {})

    legend_rows = ''
    for sec_letter, sec_data in sections.items():
        color = section_colors.get(sec_letter, '#666')
        racks_list = ', '.join(sec_data['racks'])
        legend_rows += f'''<tr style="border-bottom:2px solid #ddd">
            <td style="padding:14px;font-weight:900;font-size:2rem;color:{color}">{sec_letter}</td>
            <td style="padding:14px;font-size:1.2rem"><b>{sec_data['name']}</b></td>
            <td style="padding:14px;font-size:1.2rem">{racks_list}</td>
            <td style="padding:14px;font-size:1.2rem;text-align:center;font-weight:700">{len(sec_data['racks'])}</td>
        </tr>'''

    total_racks = sum(len(s['racks']) for s in sections.values())

    html += f'''<div class="label-card legend-card" data-shelf="LEGENDA" style="text-align:left;padding:40px 50px">
    <div style="text-align:center;margin-bottom:30px">
        <div style="font-size:4rem;font-weight:900;letter-spacing:2px">MAGAZYN</div>
        <div style="font-size:1.3rem;color:#666;margin-top:5px">Rozklad regalow — co jest gdzie</div>
        <div style="font-size:1.1rem;color:#999;margin-top:5px">Lacznie: {total_racks} regalow</div>
    </div>
    <table style="width:100%;border-collapse:collapse;font-size:1.3rem;margin-top:20px">
        <tr style="border-bottom:3px solid #000">
            <th style="padding:12px;text-align:left;font-size:1.5rem">Sekcja</th>
            <th style="padding:12px;text-align:left;font-size:1.5rem">Lokalizacja</th>
            <th style="padding:12px;text-align:left;font-size:1.5rem">Regaly</th>
            <th style="padding:12px;text-align:center;font-size:1.5rem">Ile</th>
        </tr>
        {legend_rows}
    </table>
    <div style="text-align:center;margin-top:40px;font-size:1rem;color:#999">
        Zeskanuj QR kod na regale telefonem &mdash; zobaczysz co jest na polkach
    </div>
</div>'''

    # ===== KARTKI PER REGAL (tylko z WAREHOUSE_CONFIG) =====
    all_rack_keys = config_shelves
    for rack_key in all_rack_keys:
        levels = shelves_data.get(rack_key, [])
        total_items = sum(lv.get('items', 0) for lv in levels) if isinstance(levels, list) else 0
        n_levels = len(levels) if isinstance(levels, list) else 0
        url = f"{base_url}/warehouse/shelf/{rack_key}"

        html += f'''<div class="label-card" data-shelf="{rack_key}">
    <div class="shelf-name">{rack_key}</div>
    <div class="shelf-sub">Regal {rack_key} &middot; {n_levels} polek</div>
    <div class="qr-placeholder" data-url="{url}"></div>
</div>'''

    html += '</div>'

    # JS: lokalny generator QR (bez zewnetrznego serwera)
    shelf_letters_js = str(all_rack_keys)
    html += f'''
<script src="https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.min.js"></script>
<script>
// Generuj QR kody lokalnie
document.querySelectorAll('.qr-placeholder').forEach(function(el) {{
    var url = el.dataset.url;
    if (!url) return;
    var qr = qrcode(0, 'M');
    qr.addData(url);
    qr.make();
    el.innerHTML = qr.createSvgTag(8, 0);
    // Ustaw rozmiar SVG
    var svg = el.querySelector('svg');
    if (svg) {{
        svg.style.width = '280px';
        svg.style.height = '280px';
    }}
}});

const allShelves = ["LEGENDA"].concat({shelf_letters_js});
var filterDiv = document.getElementById('shelfFilter');
allShelves.forEach(function(s) {{
    var lbl = document.createElement('label');
    var label = s === 'LEGENDA' ? 'Legenda' : 'Regal '+s;
    lbl.innerHTML = '<input type="checkbox" checked onchange="filterCards()" value="'+s+'"> '+label;
    filterDiv.appendChild(lbl);
}});

function filterCards() {{
    var checked = [];
    document.querySelectorAll('#shelfFilter input:checked').forEach(function(i){{checked.push(i.value)}});
    document.querySelectorAll('.label-card').forEach(function(card) {{
        card.style.display = checked.indexOf(card.dataset.shelf) >= 0 ? '' : 'none';
    }});
}}
</script>
</body></html>'''

    return html


@app.route('/warehouse/heatmap')
def warehouse_heatmap_view():
    """Strona główna z 3D heatmapą magazynu"""
    return render_template('warehouse_heatmap.html')


@app.route('/api/warehouse/heatmap')
def api_warehouse_heatmap():
    """API endpoint - dane dla heatmapy"""
    try:
        from modules.warehouse_heatmap import get_heatmap_data
        data = get_heatmap_data()
        return jsonify(data)
    except Exception as e:
        print(f"❌ Error getting heatmap data: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/warehouse/locations')
def api_warehouse_locations():
    """API endpoint - lista wszystkich lokalizacji"""
    try:
        from modules.warehouse_heatmap import get_all_locations
        locations = get_all_locations()
        return jsonify({
            'locations': [
                {
                    'code': loc.code,
                    'shelf': loc.shelf,
                    'level': loc.level,
                    'section': loc.section,
                    'items': loc.items_count,
                    'capacity': loc.capacity,
                    'fill_percentage': round(loc.fill_percentage * 100, 1),
                    'status': loc.fill_status,
                    'color': loc.color
                }
                for loc in locations
            ]
        })
    except Exception as e:
        print(f"❌ Error getting locations: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/warehouse/location/<location_code>')
def api_warehouse_location_details(location_code):
    """API endpoint - szczegóły konkretnej lokalizacji"""
    try:
        from modules.warehouse_heatmap import get_location_details
        details = get_location_details(location_code)
        
        if not details:
            return jsonify({'error': 'Location not found'}), 404
        
        return jsonify(details)
    except Exception as e:
        print(f"❌ Error getting location details: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/warehouse/assign', methods=['POST'])
def api_warehouse_assign_product():
    """API endpoint - przypisz produkt do lokalizacji"""
    try:
        from modules.warehouse_heatmap import assign_product_to_location
        
        data = request.get_json()
        product_id = data.get('product_id')
        location_code = data.get('location_code')
        quantity = data.get('quantity', 1)
        notes = data.get('notes')
        
        if not product_id or not location_code:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        success = assign_product_to_location(
            product_id=product_id,
            location_code=location_code,
            quantity=quantity,
            notes=notes
        )
        
        if success:
            return jsonify({'success': True, 'message': 'Product assigned successfully'})
        else:
            return jsonify({'success': False, 'error': 'Failed to assign product'}), 500
            
    except Exception as e:
        print(f"❌ Error assigning product: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/warehouse/remove', methods=['POST'])
def api_warehouse_remove_product():
    """API endpoint - usuń produkt z lokalizacji"""
    try:
        from modules.warehouse_heatmap import remove_product_from_location
        
        data = request.get_json()
        product_id = data.get('product_id')
        location_code = data.get('location_code')
        
        if not product_id:
            return jsonify({'success': False, 'error': 'Missing product_id'}), 400
        
        success = remove_product_from_location(
            product_id=product_id,
            location_code=location_code
        )
        
        if success:
            return jsonify({'success': True, 'message': 'Product removed successfully'})
        else:
            return jsonify({'success': False, 'error': 'Failed to remove product'}), 500
            
    except Exception as e:
        print(f"❌ Error removing product: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/warehouse/empty')
def api_warehouse_empty_locations():
    """API endpoint - znajdź puste lokalizacje"""
    try:
        from modules.warehouse_heatmap import find_empty_locations
        
        min_capacity = request.args.get('min_capacity', 1, type=int)
        locations = find_empty_locations(min_capacity=min_capacity)
        
        return jsonify({
            'empty_locations': locations,
            'count': len(locations)
        })
    except Exception as e:
        print(f"❌ Error finding empty locations: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/warehouse/stats')
def api_warehouse_stats():
    """API endpoint - statystyki magazynu"""
    try:
        from modules.warehouse_heatmap import get_location_stats
        stats = get_location_stats()
        return jsonify(stats)
    except Exception as e:
        print(f"❌ Error getting warehouse stats: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/warehouse/search-product')
def api_warehouse_search_product():
    """API endpoint - wyszukiwanie produktu po nazwie/EAN/ASIN"""
    try:
        query = request.args.get('q', '').strip()
        
        if not query or len(query) < 2:
            return jsonify({'results': []})
        
        from modules.database import get_db
        conn = get_db()
        
        # Szukaj po nazwie, EAN, ASIN
        search_pattern = f'%{query}%'
        
        results = conn.execute('''
            SELECT id, nazwa, ean, asin, lokalizacja, ilosc, cena_allegro, dostawca, zdjecie_url, kod_magazynowy
            FROM produkty
            WHERE (
                UPPER(nazwa) LIKE UPPER(?)
                OR UPPER(ean) LIKE UPPER(?)
                OR UPPER(asin) LIKE UPPER(?)
                OR UPPER(kod_magazynowy) LIKE UPPER(?)
            )
            AND lokalizacja IS NOT NULL
            AND lokalizacja != ''
            ORDER BY
                CASE WHEN UPPER(nazwa) LIKE UPPER(?) THEN 1 ELSE 2 END,
                id DESC
            LIMIT 10
        ''', (search_pattern, search_pattern, search_pattern, search_pattern, search_pattern)).fetchall()
        
        products = []
        for row in results:
            products.append({
                'id': row[0],
                'nazwa': row[1],
                'ean': row[2],
                'asin': row[3],
                'lokalizacja': row[4],
                'ilosc': row[5],
                'cena_allegro': row[6],
                'dostawca': row[7],
                'zdjecie_url': row[8],
                'kod_magazynowy': row[9]
            })
        
        return jsonify({'results': products, 'query': query})
        
    except Exception as e:
        print(f"❌ Error searching product: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'results': []}), 500


@app.route('/palety/<int:paleta_id>')
def paleta_szczegoly(paleta_id):
    """Widok szczegółów palety z kolorami statusów"""
    from modules.database import get_db
    
    conn = get_db()
    paleta = conn.execute('SELECT * FROM palety WHERE id = ?', (paleta_id,)).fetchone()
    produkty = conn.execute('SELECT * FROM produkty WHERE paleta_id = ? ORDER BY data_dodania DESC', (paleta_id,)).fetchall()
    
    # MIGRACJA: przenieś przychod_offline z produkty -> sprzedaze (PRZED obliczaniem stats)
    try:
        from datetime import datetime as _dtm
        stare_offline = conn.execute("""
            SELECT p.id, p.nazwa, p.przychod_offline, p.sprzedano_offline, pal.data_zakupu
            FROM produkty p LEFT JOIN palety pal ON pal.id = p.paleta_id
            WHERE p.paleta_id = ? AND p.sprzedano_offline > 0 AND p.przychod_offline > 0
              AND NOT EXISTS (SELECT 1 FROM sprzedaze s WHERE s.produkt_id=p.id AND s.kupujacy='offline' AND s.cena>0)
        """, (paleta_id,)).fetchall()
        for row in stare_offline:
            data = row['data_zakupu'] or _dtm.now().strftime('%Y-%m-%dT%H:%M:%S')
            cena_szt = round(row['przychod_offline'] / max(row['sprzedano_offline'], 1), 2)
            conn.execute("DELETE FROM sprzedaze WHERE produkt_id=? AND kupujacy='offline' AND cena=0", (row['id'],))
            conn.execute("INSERT INTO sprzedaze (produkt_id,nazwa,cena,ilosc,status,data_sprzedazy,kupujacy,notified) VALUES (?,?,?,?,'sprzedana',?,'offline',1)",
                (row['id'], row['nazwa'] or f'Produkt #{row["id"]}', cena_szt, row['sprzedano_offline'], data))
            conn.execute("UPDATE produkty SET przychod_offline=0 WHERE id=?", (row['id'],))
        if stare_offline:
            conn.commit()
            print(f"✅ Migracja palety {paleta_id}: {len(stare_offline)} offline -> sprzedaze")
    except Exception as _em:
        print(f"⚠️ Migracja palety: {_em}")

    # Zeruj przychod_offline I sprzedano_offline dla produktów które mają już rekord w sprzedaze (cleanup)
    # FIX: zeruj OBA pola — wcześniej tylko przychod_offline, co powodowało mismatch (+1 sprzedanych)
    try:
        conn.execute('''
            UPDATE produkty SET przychod_offline = 0, sprzedano_offline = 0
            WHERE paleta_id = ? AND (przychod_offline > 0 OR sprzedano_offline > 0)
              AND EXISTS (
                  SELECT 1 FROM sprzedaze s
                  WHERE s.produkt_id = produkty.id AND s.kupujacy = 'offline' AND s.cena > 0
              )
        ''', (paleta_id,))
        conn.commit()
    except:
        pass

    # Sprawdź czy kolumny offline istnieją
    has_offline_columns = False
    try:
        conn.execute("SELECT sprzedano_offline, przychod_offline FROM produkty LIMIT 1")
        has_offline_columns = True
    except:
        pass
    
    if has_offline_columns:
        # Wykluczamy produkty sprzedane offline z liczenia Allegro (żeby się nie duplikowały)
        # sprzedane_produkty_allegro = tylko te bez offline (sprawdzamy sprzedano_offline, nie przychod)
        stats = conn.execute('''
            SELECT COUNT(*) as cnt, 
                   COALESCE(SUM(ilosc), 0) as sztuki,
                   COALESCE(SUM(CASE WHEN status IN ('wystawiony', 'szkic') THEN cena_allegro * ilosc ELSE 0 END), 0) as wartosc,
                   COALESCE(SUM(CASE WHEN status = 'sprzedany' AND (sprzedano_offline IS NULL OR sprzedano_offline = 0) THEN cena_allegro ELSE 0 END), 0) as sprzedano_wartosc,
                   COALESCE(SUM(CASE WHEN status = 'sprzedany' AND (sprzedano_offline IS NULL OR sprzedano_offline = 0) THEN 1 ELSE 0 END), 0) as sprzedane_produkty,
                   COALESCE(SUM(CASE WHEN status = 'sprzedany' THEN cena_brutto ELSE 0 END), 0) as sprzedano_koszt,
                   SUM(CASE WHEN status = 'wystawiony' THEN 1 ELSE 0 END) as wystawione,
                   SUM(CASE WHEN status = 'magazyn' THEN 1 ELSE 0 END) as magazyn,
                   SUM(CASE WHEN status = 'sprzedany' THEN 1 ELSE 0 END) as sprzedane_cnt,
                   COALESCE(SUM(cena_brutto), 0) as zakup_brutto_suma,
                   COALESCE(SUM(cena_netto), 0) as zakup_netto_suma,
                   COALESCE(SUM(sprzedano_offline), 0) as sprzedano_offline_suma,
                   COALESCE(SUM(przychod_offline), 0) as przychod_offline_suma
            FROM produkty WHERE paleta_id = ?
        ''', (paleta_id,)).fetchone()
    else:
        stats = conn.execute('''
            SELECT COUNT(*) as cnt, 
                   COALESCE(SUM(ilosc), 0) as sztuki,
                   COALESCE(SUM(CASE WHEN status IN ('wystawiony', 'szkic') THEN cena_allegro * ilosc ELSE 0 END), 0) as wartosc,
                   COALESCE(SUM(CASE WHEN status = 'sprzedany' THEN cena_allegro ELSE 0 END), 0) as sprzedano_wartosc,
                   COALESCE(SUM(CASE WHEN status = 'sprzedany' THEN 1 ELSE 0 END), 0) as sprzedane_produkty,
                   COALESCE(SUM(CASE WHEN status = 'sprzedany' THEN cena_brutto ELSE 0 END), 0) as sprzedano_koszt,
                   SUM(CASE WHEN status = 'wystawiony' THEN 1 ELSE 0 END) as wystawione,
                   SUM(CASE WHEN status = 'magazyn' THEN 1 ELSE 0 END) as magazyn,
                   SUM(CASE WHEN status = 'sprzedany' THEN 1 ELSE 0 END) as sprzedane_cnt,
                   COALESCE(SUM(cena_brutto), 0) as zakup_brutto_suma,
                   COALESCE(SUM(cena_netto), 0) as zakup_netto_suma,
                   0 as sprzedano_offline_suma,
                   0 as przychod_offline_suma
            FROM produkty WHERE paleta_id = ?
        ''', (paleta_id,)).fetchone()
    
    # Pobierz rzeczywistą sprzedaż z tabeli sprzedaze (dla dokładniejszych danych)
    # Obliczamy koszt na podstawie średniej ceny za sztukę (cena_brutto / ilosc_oryginalna)
    sprzedaz_stats = conn.execute('''
        SELECT COALESCE(SUM(s.cena * s.ilosc), 0) as przychod,
               COALESCE(SUM(s.ilosc), 0) as szt_sprzedanych
        FROM sprzedaze s
        JOIN produkty p ON s.produkt_id = p.id
        WHERE p.paleta_id = ?
          AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot','')
    ''', (paleta_id,)).fetchone()
    
    if not paleta:
        return redirect('/palety')
    
    # Bezpieczne pobieranie ceny netto (kolumna może nie istnieć w starej bazie)
    cena_zakupu_netto = 0
    try:
        # Sprawdź czy kolumna istnieje
        kolumny = [desc[0] for desc in conn.execute('PRAGMA table_info(palety)').fetchall()]
        if 'cena_zakupu_netto' in kolumny:
            val = conn.execute('SELECT cena_zakupu_netto FROM palety WHERE id = ?', (paleta_id,)).fetchone()
            cena_zakupu_netto = val[0] if val and val[0] else 0
    except:
        pass
    
    # AUTO-NAPRAWA: Jeśli cena_zakupu = 0, zapisz aktualną sumę (jednorazowo!)
    cena_zakupu = paleta['cena_zakupu'] or 0
    if cena_zakupu == 0:
        # Oblicz sumę netto z produktów
        suma_netto = stats['zakup_netto_suma'] or 0
        suma_brutto = round(suma_netto * 1.23, 2)
        
        if suma_netto > 0:
            # cena_zakupu = BRUTTO
            kolumny = [desc[0] for desc in conn.execute('PRAGMA table_info(palety)').fetchall()]
            if 'cena_zakupu_netto' in kolumny:
                conn.execute('''
                    UPDATE palety SET cena_zakupu = ?, cena_zakupu_netto = ? WHERE id = ?
                ''', (suma_brutto, suma_netto, paleta_id))
            else:
                conn.execute('UPDATE palety SET cena_zakupu = ? WHERE id = ?', (suma_brutto, paleta_id))
            conn.commit()
            cena_zakupu = suma_brutto
            cena_zakupu_netto = suma_netto
            print(f"💰 Auto-naprawiono cenę zakupu palety #{paleta_id}: {suma_netto:.2f} netto | {suma_brutto:.2f} brutto")
    
    # Przychód offline ze sprzedaze (nowe rekordy po migracji)
    przychod_offline_sprzedaze = conn.execute('''
        SELECT COALESCE(SUM(s.cena * s.ilosc), 0)
        FROM sprzedaze s
        JOIN produkty p ON s.produkt_id = p.id
        WHERE p.paleta_id = ? AND s.kupujacy = 'offline'
          AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot','')
    ''', (paleta_id,)).fetchone()[0] or 0

    # Przychód offline ze starych danych - TYLKO dla produktów bez rekordu w sprzedaze
    # (żeby nie liczyć podwójnie tych które już mają rekord w sprzedaze)
    przychod_offline_stare = conn.execute('''
        SELECT COALESCE(SUM(przychod_offline), 0)
        FROM produkty
        WHERE paleta_id = ? AND przychod_offline > 0
          AND NOT EXISTS (
              SELECT 1 FROM sprzedaze s
              WHERE s.produkt_id = produkty.id
                AND s.kupujacy = 'offline'
                AND s.cena > 0
          )
    ''', (paleta_id,)).fetchone()[0] or 0

    # Przychód z Allegro (sprzedaze bez offline)
    przychod_allegro_db = conn.execute('''
        SELECT COALESCE(SUM(s.cena * s.ilosc), 0)
        FROM sprzedaze s
        JOIN produkty p ON s.produkt_id = p.id
        WHERE p.paleta_id = ? AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
          AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot','')
    ''', (paleta_id,)).fetchone()[0] or 0

    
    # cena_zakupu w bazie = BRUTTO
    koszt_palety_brutto = cena_zakupu
    koszt_palety_netto = cena_zakupu_netto if cena_zakupu_netto > 0 else round(cena_zakupu / 1.23, 2)

    # STAŁY koszt jednostkowy (NETTO/szt) - raz ustawiony, nie zmienia się
    try:
        _kj_netto = float(paleta['koszt_jednostkowy'] or 0)
    except:
        _kj_netto = 0
    if _kj_netto == 0 and koszt_palety_netto > 0:
        # Auto-set: oblicz raz z aktualnej ilości (magazyn + sprzedane)
        _total = (stats['sztuki'] or 0) + (sprzedaz_stats['szt_sprzedanych'] or 0)
        if _total > 0:
            _kj_netto = round(koszt_palety_netto / _total, 2)
            try:
                conn.execute('UPDATE palety SET koszt_jednostkowy = ? WHERE id = ?', (_kj_netto, paleta_id))
                conn.commit()
                print(f"💰 Auto-set koszt_jednostkowy palety #{paleta_id}: {_kj_netto:.2f} zł/szt netto")
            except:
                pass
    koszt_jednostkowy_netto = _kj_netto
    koszt_jednostkowy_brutto = round(_kj_netto * 1.23, 2) if _kj_netto > 0 else 0

    # Rzeczywiste dane sprzedaży
    # sprzedane_produkty = liczba produktów ze statusem 'sprzedany' (każdy = min 1 szt)
    # sprzedaz_stats = dane z tabeli sprzedaze (dokładniejsze - ma ilość sztuk)
    # sprzedano_offline = ile sprzedano poza Allegro (bez statystyk)
    
    sprzedano_szt_db = sprzedaz_stats['szt_sprzedanych'] or 0  # z tabeli sprzedaze
    sprzedane_produkty = stats['sprzedane_produkty'] or 0  # liczba produktów ze statusem sprzedany
    try:
        sprzedano_offline = stats['sprzedano_offline_suma'] or 0  # sprzedane poza Allegro
    except:
        sprzedano_offline = 0
    try:
        przychod_offline = stats['przychod_offline_suma'] or 0  # przychód ze sprzedaży offline
    except:
        przychod_offline = 0
    
    from modules.database import get_db as _gdb_cnt
    conn_cnt = _gdb_cnt()
    print(f"📊 STATS paleta #{paleta_id}:")
    print(f"   - sprzedano_szt_db (tabela sprzedaze): {sprzedano_szt_db}")
    print(f"   - sprzedane_produkty (status=sprzedany bez offline): {sprzedane_produkty}")
    print(f"   - sprzedano_offline (suma): {sprzedano_offline}")
    print(f"   - przychod_offline (suma): {przychod_offline}")
    
    # Suma wszystkich źródeł sprzedaży
    # sprzedano_szt_db (z tabeli sprzedaze) już zawiera offline
    # Dla produktów bez rekordu w sprzedaze (stare dane) - użyj sprzedano_offline
    # Unikaj podwójnego liczenia
    offline_w_sprzedaze = conn_cnt.execute('''
        SELECT COALESCE(SUM(s.ilosc),0) FROM sprzedaze s
        JOIN produkty p ON s.produkt_id=p.id
        WHERE p.paleta_id=? AND s.kupujacy='offline'
        AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot','')
    ''', (paleta_id,)).fetchone()[0] or 0
    conn_cnt.close()
    offline_bez_sprzedaze = max(0, sprzedano_offline - offline_w_sprzedaze)
    sprzedano_szt = sprzedano_szt_db + offline_bez_sprzedaze
    print(f"   - WYNIK sprzedano_szt = {sprzedano_szt_db} (sprzedaze) + {offline_bez_sprzedaze} (offline bez sprzedaze) = {sprzedano_szt}")
    
    # Przychód - preferuj dane z produkty (sprzedano_wartosc), bo tabela sprzedaze może być niekompletna
    przychod_z_produktow = stats['sprzedano_wartosc'] or 0  # SUM(cena_allegro) WHERE status='sprzedany'
    przychod_z_sprzedazy = sprzedaz_stats['przychod'] or 0  # SUM(cena*ilosc) z tabeli sprzedaze
    
    przychod_rzeczywisty = przychod_allegro_db + przychod_offline_sprzedaze + przychod_offline_stare
    przychod_z_sprzedazy = przychod_allegro_db + przychod_offline_sprzedaze  # dla printu
    
    print(f"📊 PRZYCHOD: z_produktow={przychod_z_produktow}, z_sprzedazy={przychod_z_sprzedazy}, offline={przychod_offline}, SUMA={przychod_rzeczywisty}")
    
    # Koszt sprzedanych = stały koszt/szt × ilość sprzedana
    wszystkie_szt = (stats['sztuki'] or 0) + sprzedano_szt
    if koszt_jednostkowy_brutto > 0:
        koszt_sprzedanych = sprzedano_szt * koszt_jednostkowy_brutto
    elif wszystkie_szt > 0 and koszt_palety_brutto > 0:
        koszt_sprzedanych = (sprzedano_szt / wszystkie_szt) * koszt_palety_brutto
    else:
        koszt_sprzedanych = 0
    
    zysk_rzeczywisty = przychod_rzeczywisty - koszt_sprzedanych
    
    # DEBUG: wyświetl info o każdym produkcie
    print(f"📦 PRODUKTY na palecie #{paleta_id}:")
    for p in produkty:
        try:
            offline_szt = p['sprzedano_offline'] or 0
        except:
            offline_szt = 0
        try:
            offline_przychod = p['przychod_offline'] or 0
        except:
            offline_przychod = 0
        nazwa = (p['nazwa'] or '')[:30]
        print(f"   - ID:{p['id']} | {nazwa} | status={p['status']} | ilosc={p['ilosc']} | offline_szt={offline_szt} | offline_przychod={offline_przychod}")
    
    produkty_html = ''
    for p in produkty:
        # Pobierz offline_szt dla tego produktu
        try:
            p_offline_szt = p['sprzedano_offline'] or 0
        except:
            p_offline_szt = 0
            
        if p['status'] == 'sprzedany':
            status_color = '#22c55e'
            status_icon = '✅'
            status_text = 'SPRZEDANY'
        elif p['status'] == 'wystawiony':
            status_color = '#3b82f6'
            status_icon = '🔵'
            status_text = 'WYSTAWIONY'
        elif p['status'] == 'magazyn':
            status_color = '#eab308'
            status_icon = '📦'
            status_text = 'MAGAZYN'
        else:
            status_color = '#64748b'
            status_icon = '⚪'
            status_text = 'NOWY'
        
        # Cena jednostkowa zakupu - STAŁA z palety (koszt_jednostkowy = netto)
        if koszt_jednostkowy_netto > 0:
            netto_szt = koszt_jednostkowy_netto
            brutto_szt = koszt_jednostkowy_brutto
            cena_glowna = f"{netto_szt:.2f} zł/szt netto (stała)"
            cena_dodatkowa = ""
        else:
            brutto_szt = 0
            netto_szt = 0
            cena_glowna = "brak - ustaw w edycji palety"
            cena_dodatkowa = ""
        
        stan_opcje = ''
        stany = ['Nowy', 'Nowy w otwartym opakowaniu', 'Używany', 'Uszkodzony', 'Odnowiony']
        for s in stany:
            sel = 'selected' if (p['stan'] or 'Nowy') == s else ''
            stan_opcje += f'<option value="{s}" {sel}>{s}</option>'

        status_opcje = ''
        statusy = [('magazyn','📦 Magazyn'),('wystawiony','🛒 Wystawiony'),('sprzedany','💰 Sprzedany'),('uszkodzony','⚠️ Uszkodzony'),('zwrot','↩️ Zwrot')]
        for sv, sl in statusy:
            sel = 'selected' if (p['status'] or 'magazyn') == sv else ''
            status_opcje += f'<option value="{sv}" {sel}>{sl}</option>'

        produkty_html += f'''
        <div style="background:#1e1e2e;border-radius:8px;padding:10px;margin-bottom:8px" data-produkt-id="{p['id']}" data-ilosc="{p['ilosc']}">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:8px">
                <div style="flex:1;min-width:0">
                    <div style="font-size:0.85rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                        <a href="/magazyn/produkt/{p['id']}" style="color:#fff;text-decoration:none">{p['nazwa'][:45]}</a>
                    </div>
                    <div style="font-size:0.7rem;color:#64748b;margin-top:2px;display:flex;align-items:center;gap:4px">
                        <span>{p['ean'] or p['asin'] or '—'} •</span>
                        <button onclick="szybkaMinus({p['id']},{p['ilosc']},{int(p['cena_allegro'] or 0)})" style="background:#ef4444;border:none;border-radius:4px;color:#fff;width:20px;height:20px;font-size:0.7rem;cursor:pointer;padding:0;line-height:20px" {'disabled' if (p['ilosc'] or 0) == 0 else ''}>-</button>
                        <span style="color:#fff;font-weight:600" id="ilosc-{p['id']}">{p['ilosc']}</span>
                        <button onclick="szybkaPlus({p['id']},{p['ilosc']})" style="background:#22c55e;border:none;border-radius:4px;color:#fff;width:20px;height:20px;font-size:0.7rem;cursor:pointer;padding:0;line-height:20px">+</button>
                        <span>szt • {p['lokalizacja'] or '—'}</span>
                    </div>
                    <div class="sztuki-dots"></div>
                </div>
                <div style="text-align:right;flex-shrink:0">
                    <div style="font-weight:600;color:#22c55e">{p['cena_allegro']:.0f} zł</div>
                    <div style="font-size:0.65rem;color:#ef4444">{cena_glowna}</div>
                </div>
            </div>

            <!-- INLINE EDYCJA STANU I STATUSU -->
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px">
                <div>
                    <div style="font-size:0.6rem;color:#64748b;margin-bottom:3px">🏷️ STAN</div>
                    <select onchange="zapiszPole({p['id']}, 'stan', this.value, this)"
                        style="width:100%;background:#12121a;border:1px solid #334155;border-radius:6px;color:#fff;padding:5px 6px;font-size:0.72rem">
                        {stan_opcje}
                    </select>
                </div>
                <div>
                    <div style="font-size:0.6rem;color:#64748b;margin-bottom:3px">📦 STATUS</div>
                    <select onchange="zapiszPole({p['id']}, 'status', this.value, this)"
                        style="width:100%;background:#12121a;border:1px solid #334155;border-radius:6px;color:#fff;padding:5px 6px;font-size:0.72rem">
                        {status_opcje}
                    </select>
                </div>
            </div>

            <div style="display:flex;gap:4px">
                <button type="button"
                   onclick="document.getElementById('korektaProduktId').value='{p['id']}';document.getElementById('korektaIlosc').value={p['ilosc'] or 0};document.getElementById('maxIlosc').value={p['ilosc'] or 0};document.getElementById('sprzedajIlosc').value=1;document.getElementById('sprzedajIlosc').max={p['ilosc'] or 0};document.getElementById('sprzedajCena').value='{int(p['cena_allegro'] or p['cena_brutto'] or 0)}';document.getElementById('offlineSzt').value='{int(p_offline_szt)}';var cs=document.getElementById('cofnijOfflineSection');if({int(p_offline_szt)}>0){{cs.style.display='block';document.getElementById('offlineInfo').textContent='{int(p_offline_szt)} szt.';document.getElementById('cofnijIlosc').value=1;document.getElementById('cofnijIlosc').max={int(p_offline_szt)}}}else{{cs.style.display='none'}};document.getElementById('modalKorekta').style.display='block'"
                   style="padding:6px 10px;background:#f97316;border:none;border-radius:6px;color:#fff;font-size:0.65rem;font-weight:600;cursor:pointer;flex:1">
                    ✏️ Korekta
                </button>
                <a href="/magazyn/produkt/{p['id']}/edytuj"
                   style="padding:6px 10px;background:#3b82f6;border-radius:6px;color:#fff;text-decoration:none;font-size:0.65rem;font-weight:600;text-align:center;flex:1">
                    🖊️ Edytuj
                </a>
                <button onclick="pokazMenu(event, {p['id']}, {p['ilosc']}, '{p['nazwa'][:30].replace(chr(39), chr(96)).replace(chr(34), chr(96))}', this)"
                   style="padding:6px 10px;background:#334155;border:none;border-radius:6px;color:#fff;font-size:0.65rem;font-weight:600;cursor:pointer;flex:1">
                    ⋯ Akcje
                </button>
            </div>
        </div>
        '''
    
    if not produkty_html:
        produkty_html = '<div style="text-align:center;color:#64748b;padding:20px">Brak produktów. Importuj Excel!</div>'
    
    # ROI liczony na podstawie wartości Allegro (wystawione + szkic), nie sprzedanych
    zysk_potencjalny = stats['wartosc'] - koszt_palety_brutto
    roi = (zysk_potencjalny / koszt_palety_brutto * 100) if koszt_palety_brutto > 0 else 0
    
    # Bezpieczne pobieranie regału
    try:
        regal_palety = paleta['regal'] if paleta['regal'] else ''
    except (KeyError, TypeError):
        regal_palety = ''
    
    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>📦 {paleta['nazwa'] or f"Paleta #{paleta['id']}"}</h1>
            <small>{paleta['dostawca']} • {paleta['data_zakupu']}</small>
            {f'<div style="margin-top:6px;font-size:0.85rem;color:#8b5cf6">📍 Regal: {regal_palety}</div>' if regal_palety else ''}
        </div>
        
        <!-- GŁÓWNE STATYSTYKI SPRZEDAŻY -->
        <div style="background:linear-gradient(135deg,#065f46,#064e3b);border:2px solid #10b981;border-radius:16px;padding:15px;margin-bottom:15px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                <div style="font-size:0.8rem;color:#6ee7b7;text-transform:uppercase;letter-spacing:1px">📊 SPRZEDAŻ Z PALETY</div>
                <div style="font-size:1.8rem;font-weight:800;color:#fff">{sprzedano_szt} <span style="font-size:0.9rem;color:#6ee7b7">/ {(stats['sztuki'] or 0) + sprzedano_szt} szt</span></div>
            </div>
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">
                <div style="text-align:center">
                    <div style="font-size:1.3rem;font-weight:700;color:#22c55e">{przychod_rzeczywisty:.0f} zł</div>
                    <div style="font-size:0.65rem;color:#6ee7b7">PRZYCHÓD</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:1.3rem;font-weight:700;color:#ef4444">-{koszt_sprzedanych:.0f} zł</div>
                    <div style="font-size:0.65rem;color:#6ee7b7">KOSZT</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:1.3rem;font-weight:700;color:{'#22c55e' if zysk_rzeczywisty >= 0 else '#ef4444'}">{zysk_rzeczywisty:+.0f} zł</div>
                    <div style="font-size:0.65rem;color:#6ee7b7">ZYSK</div>
                </div>
            </div>
        </div>
        
        <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:15px">
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.3rem;font-weight:700;color:#f97316">{koszt_palety_netto:.0f} zł</div>
                <div style="font-size:0.7rem;color:#64748b">KOSZT NETTO (STAŁY)</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.3rem;font-weight:700;color:#ef4444">{koszt_palety_brutto:.0f} zł</div>
                <div style="font-size:0.7rem;color:#64748b">KOSZT BRUTTO (STAŁY)</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.3rem;font-weight:700;color:#22c55e">{stats['wartosc']:.0f} zł</div>
                <div style="font-size:0.7rem;color:#64748b">WARTOŚĆ ALLEGRO</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.3rem;font-weight:700;color:#3b82f6">{stats['cnt']} <span style="font-size:0.8rem;color:#64748b">({stats['sztuki']} szt)</span></div>
                <div style="font-size:0.7rem;color:#64748b">PRODUKTÓW</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.3rem;font-weight:700;color:#22c55e">{sprzedano_szt}</div>
                <div style="font-size:0.7rem;color:#64748b">SPRZEDANYCH</div>
            </div>
        </div>
        
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:15px">
            <div style="background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);border-radius:10px;padding:10px;text-align:center">
                <div style="font-size:1.1rem;font-weight:600;color:#22c55e">✅ {stats['wystawione'] or 0}</div>
                <div style="font-size:0.7rem;color:#64748b">WYSTAWIONE</div>
            </div>
            <div style="background:rgba(234,179,8,0.1);border:1px solid rgba(234,179,8,0.3);border-radius:10px;padding:10px;text-align:center">
                <div style="font-size:1.1rem;font-weight:600;color:#eab308">📦 {stats['magazyn'] or 0}</div>
                <div style="font-size:0.7rem;color:#64748b">W MAGAZYNIE</div>
            </div>
            <div style="background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:10px;padding:10px;text-align:center">
                <div style="font-size:1.1rem;font-weight:600;color:#ef4444">📊 {roi:.1f}%</div>
                <div style="font-size:0.7rem;color:#64748b">ROI</div>
            </div>
        </div>
        
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:15px">
            <a href="/palety/{paleta_id}/mass-edit" class="btn" style="display:block;padding:14px;background:linear-gradient(135deg,#8b5cf6,#7c3aed);border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600">✏️ MASOWE WYSTAWIANIE</a>
            <a href="/magazyn/import?paleta_id={paleta_id}" class="btn" style="display:block;padding:14px;background:#3b82f6;border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600">📥 IMPORTUJ EXCEL</a>
            <a href="/palety/{paleta_id}/edit" class="btn" style="display:block;padding:14px;background:linear-gradient(135deg,#f59e0b,#d97706);border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600">⚙️ EDYTUJ PALETE</a>
        </div>
        
        <!-- PRZEKAZ ZYSK NA CEL -->
        ''' + ('''
        <form action="/goal/add-contribution" method="POST" style="background:linear-gradient(135deg,rgba(34,197,94,0.15),rgba(16,185,129,0.1));border:1px solid rgba(34,197,94,0.3);border-radius:12px;padding:15px;margin-bottom:15px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                <div>
                    <div style="font-weight:600;color:#22c55e;font-size:1.05rem">&#x1F697; Przekaz zysk na Hyundaia i30 N</div>
                    <div style="font-size:0.75rem;color:#647d8b;margin-top:3px">Potencjalny zysk: ''' + str(int(zysk_potencjalny)) + ''' PLN</div>
                </div>
            </div>
            <div style="display:flex;gap:10px;align-items:center">
                <input type="hidden" name="paleta_id" value="''' + str(paleta_id) + '''">
                <input type="hidden" name="description" value="Zysk z palety ''' + str(paleta['nazwa'] or paleta_id) + '''">
                <input type="number" name="amount" placeholder="Kwota PLN" required min="1" step="1"
                       value="''' + str(max(0, int(zysk_potencjalny))) + '''"
                       style="flex:1;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">
                <button type="submit" style="padding:12px 24px;background:#22c55e;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer;white-space:nowrap">&#x1F4B0; PRZEKAZ</button>
            </div>
        </form>
        ''' if zysk_potencjalny > 0 else '') + '''
        
        <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin-bottom:10px">PRODUKTY (''' + str(stats['cnt']) + ''')</div>
        
        ''' + produkty_html + '''
        
        <form method="POST" action="/palety/''' + str(paleta_id) + '''/delete" style="margin-top:20px" onsubmit="return confirm('⚠️ UWAGA!\\n\\nTo usunie tę paletę i wszystkie jej produkty (''' + str(stats['cnt']) + ''' szt.)\\n\\nNa pewno kontynuować?')">
            <button type="submit" style="width:100%;padding:12px;background:#ef4444;border:none;border-radius:10px;color:#fff;font-weight:600;cursor:pointer">
                🗑️ USUŃ PALETĘ
            </button>
        </form>
        
        <a href="/palety" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:15px">← Powrót do palet</a>
    </div>
    
    <!-- MODAL KOREKTY ILOŚCI -->
    <div id="modalKorekta" onclick="if(event.target===this)this.style.display='none'" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.8);z-index:1000;padding:20px">
        <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;max-width:400px;margin:50px auto;padding:20px">
            <h3 style="color:#fff;margin:0 0 15px">✏️ Korekta produktu</h3>
            
            <!-- KOREKTA ILOŚCI: natywny HTML form → zero zależności od JS -->
            <form method="POST" action="/sprzedaze/korekta-ilosci">
                <input type="hidden" name="produkt_id" id="korektaProduktId" value="">
                <div style="margin-bottom:15px">
                    <label style="display:block;font-size:0.8rem;color:#64748b;margin-bottom:5px">Zmień ilość na:</label>
                    <input type="number" name="nowa_ilosc" id="korektaIlosc" min="0" style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #1e1e2e;border-radius:8px;color:#fff;font-size:1rem">
                </div>
                <div style="display:flex;gap:10px;margin-bottom:15px">
                    <button type="submit" style="flex:1;padding:12px;background:#3b82f6;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">💾 Zapisz ilość</button>
                    <button type="button" onclick="document.getElementById('modalKorekta').style.display='none'" style="padding:12px 16px;background:#64748b;border:none;border-radius:8px;color:#fff;cursor:pointer">✕</button>
                </div>
            </form>
            
            <div style="border-top:1px solid #1e1e2e;padding-top:15px;margin-top:10px">
                <label style="display:block;font-size:0.8rem;color:#f59e0b;margin-bottom:8px">📦 Sprzedaż offline (bez statystyk Allegro):</label>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
                    <div>
                        <label style="font-size:0.7rem;color:#64748b">Ile szt.:</label>
                        <input type="number" id="sprzedajIlosc" min="1" value="1" style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #f59e0b;border-radius:8px;color:#fff;font-size:1rem;text-align:center">
                    </div>
                    <div>
                        <label style="font-size:0.7rem;color:#64748b">Cena sprzedaży (zł):</label>
                        <input type="number" id="sprzedajCena" min="0.01" step="0.01" required placeholder="Wpisz cenę zł" style="width:100%;padding:10px;background:#0a0a0f;border:2px solid #f59e0b;border-radius:8px;color:#fff;font-size:1rem;text-align:center">
                    </div>
                </div>
                <button onclick="oznaczSprzedany()" style="width:100%;padding:12px;background:#f59e0b;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">📦 Sprzedaj offline</button>
                <div style="font-size:0.65rem;color:#64748b;margin-top:6px;text-align:center">
                    Dolicza do przychodu palety, ale NIE do statystyk sprzedaży Allegro
                </div>
            </div>

            <!-- OLX / Vinted ukryte -->

            <!-- COFNIJ OFFLINE - widoczne tylko gdy są sprzedane offline -->
            <div id="cofnijOfflineSection" style="display:none;border-top:1px solid #1e1e2e;padding-top:15px;margin-top:15px">
                <label style="display:block;font-size:0.8rem;color:#ef4444;margin-bottom:8px">🔄 Cofnij sprzedaż offline:</label>
                <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px">
                    <span style="font-size:0.75rem;color:#64748b">Sprzedano offline:</span>
                    <span id="offlineInfo" style="color:#f59e0b;font-weight:600">0 szt.</span>
                </div>
                <div style="display:flex;gap:10px">
                    <input type="number" id="cofnijIlosc" min="1" value="1" style="flex:1;padding:10px;background:#0a0a0f;border:1px solid #ef4444;border-radius:8px;color:#fff;font-size:1rem;text-align:center">
                    <button onclick="cofnijOffline()" style="padding:12px 20px;background:#ef4444;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">🔄 Cofnij</button>
                </div>
            </div>
            
            <!-- COFNIJ SPRZEDAŻ - cofa wszystkie sprzedaże produktu -->
            <div style="border-top:1px solid #1e1e2e;padding-top:15px;margin-top:15px">
                <label style="display:block;font-size:0.8rem;color:#ef4444;margin-bottom:8px">🔄 Cofnij sprzedaż (przywróć do magazynu):</label>
                <button onclick="cofnijSprzedaz()" style="width:100%;padding:12px;background:#ef4444;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">🔄 Cofnij sprzedaż</button>
                <div style="font-size:0.65rem;color:#64748b;margin-top:6px;text-align:center">
                    Cofa sprzedaż, przywraca ilość i zmienia status produktu na magazyn
                </div>
            </div>

            <input type="hidden" id="maxIlosc">
            <input type="hidden" id="offlineSzt">
        </div>
    </div>
    
    <script>
    function pokazKorekta(produktId, aktualnaIlosc, cena, offlineSzt) {
        document.getElementById('korektaProduktId').value = produktId;
        document.getElementById('korektaIlosc').value = aktualnaIlosc;
        document.getElementById('maxIlosc').value = aktualnaIlosc;
        document.getElementById('sprzedajIlosc').value = 1;
        document.getElementById('sprzedajIlosc').max = aktualnaIlosc;
        document.getElementById('sprzedajCena').value = cena || '';
        document.getElementById('offlineSzt').value = offlineSzt || 0;
        const cofnijSection = document.getElementById('cofnijOfflineSection');
        if (offlineSzt && offlineSzt > 0) {
            cofnijSection.style.display = 'block';
            document.getElementById('offlineInfo').textContent = offlineSzt + ' szt.';
            document.getElementById('cofnijIlosc').value = 1;
            document.getElementById('cofnijIlosc').max = offlineSzt;
        } else {
            cofnijSection.style.display = 'none';
        }
        document.getElementById('modalKorekta').style.display = 'block';
    }

    // Event delegation - działa nawet gdy onclick jest zablokowane
    document.addEventListener('click', function(e) {
        const btn = e.target.closest('.btn-korekta');
        if (btn) {
            e.preventDefault();
            pokazKorekta(
                btn.dataset.pid,
                parseInt(btn.dataset.ilosc),
                parseInt(btn.dataset.cena),
                parseInt(btn.dataset.offline)
            );
        }
    });
    
    function zamknijModal() {
        document.getElementById('modalKorekta').style.display = 'none';
    }

    function zapiszPole(produktId, pole, wartosc, el) {
        const fd = new FormData();
        fd.append('pole', pole);
        fd.append('wartosc', wartosc);
        fetch('/produkt/' + produktId + '/szybka-edycja', {method: 'POST', body: fd})
            .then(r => r.json())
            .then(d => {
                if (d.ok) {
                    el.style.border = '1px solid #22c55e';
                    if (pole === 'status' || d.reload) {
                        setTimeout(() => location.reload(), 400);
                    } else {
                        setTimeout(() => el.style.border = '1px solid #334155', 1200);
                    }
                } else {
                    el.style.border = '1px solid #ef4444';
                    alert('Błąd: ' + d.msg);
                }
            })
            .catch(() => { el.style.border = '1px solid #ef4444'; });
    }
    
    function cofnijOffline() {
        const ilosc = document.getElementById('cofnijIlosc').value;
        const maxOffline = document.getElementById('offlineSzt').value;

        if (parseInt(ilosc) > parseInt(maxOffline)) {
            alert('Nie możesz cofnąć więcej niż sprzedano offline (' + maxOffline + ' szt.)');
            return;
        }

        if (!confirm('Cofnąć ' + ilosc + ' szt. ze sprzedaży offline?\\n\\n(Produkty wrócą do magazynu)')) return;

        const produktId = document.getElementById('korektaProduktId').value;
        window.location.href = '/produkt/cofnij-offline/' + produktId + '?ilosc=' + ilosc;
    }

    function cofnijSprzedaz() {
        const produktId = document.getElementById('korektaProduktId').value;
        if (!confirm('Cofnąć sprzedaż tego produktu?\\n\\nProdukt wróci do magazynu, sprzedaż zostanie oznaczona jako zwrot.')) return;
        window.location.href = '/produkt/cofnij-sprzedaz/' + produktId;
    }
    
    function zapiszKorekta() {
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '/sprzedaze/korekta-ilosci';
        
        const produktId = document.createElement('input');
        produktId.name = 'produkt_id';
        produktId.value = document.getElementById('korektaProduktId').value;
        form.appendChild(produktId);
        
        const ilosc = document.createElement('input');
        ilosc.name = 'nowa_ilosc';
        ilosc.value = document.getElementById('korektaIlosc').value;
        form.appendChild(ilosc);
        
        document.body.appendChild(form);
        form.submit();
    }
    
    function oznaczSprzedany() {
        const ilosc = document.getElementById('sprzedajIlosc').value;
        const cena = document.getElementById('sprzedajCena').value || 0;
        const maxIlosc = document.getElementById('maxIlosc').value;
        
        if (parseInt(ilosc) > parseInt(maxIlosc)) {
            alert('Nie możesz sprzedać więcej niż masz w magazynie (' + maxIlosc + ' szt.)');
            return;
        }
        
        const przychod = (parseFloat(cena) * parseInt(ilosc)).toFixed(2);
        if (!cena || parseFloat(cena) <= 0) {
            alert('Podaj cenę sprzedaży (zł) — pole nie może być puste ani zerowe.');
            document.getElementById('sprzedajCena').focus();
            return;
        }
        if (!confirm('Sprzedaż offline:\\n\\n' + ilosc + ' szt. × ' + cena + ' zł = ' + przychod + ' zł\\n\\n(Doliczy do przychodu palety)')) return;
        
        const produktId = document.getElementById('korektaProduktId').value;
        
        const cenaFixed = String(cena).replace(',', '.');
        const url = '/produkt/oznacz-sprzedany/' + produktId + '?ilosc=' + ilosc + '&cena=' + cenaFixed;
        console.log('OFFLINE SALE URL:', url);
        if (parseFloat(cena) <= 0) {
            if (!confirm('Cena wynosi 0 zł - czy na pewno chcesz sprzedać za darmo?')) return;
        }
        window.location.href = url;
    }
    
    // Zamknij modal klikając poza nim
    document.getElementById('modalKorekta').addEventListener('click', function(e) {
        if (e.target === this) zamknijModal();
    });

    function szybkaMinus(produktId, aktIlosc, cena) {
        if (aktIlosc <= 0) { alert('Brak sztuk do odjęcia'); return; }
        const nowaIlosc = aktIlosc - 1;
        if (!confirm('Odjąć 1 szt? (' + aktIlosc + ' → ' + nowaIlosc + ')')) return;
        _submitKorekta(produktId, nowaIlosc);
    }
    function szybkaPlus(produktId, aktIlosc) {
        _submitKorekta(produktId, aktIlosc + 1);
    }
    function _submitKorekta(produktId, nowaIlosc) {
        const f = document.createElement('form');
        f.method = 'POST'; f.action = '/sprzedaze/korekta-ilosci';
        f.innerHTML = '<input name="produkt_id" value="'+produktId+'"><input name="nowa_ilosc" value="'+nowaIlosc+'">';
        document.body.appendChild(f); f.submit();
    }
    </script>

    <!-- MODAL: ROZBIJ NA SZTUKI -->
    <div id="modalRozbij" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.8);z-index:1000;overflow-y:auto;padding:20px">
      <div style="background:#1e1e2e;border-radius:16px;padding:20px;max-width:440px;margin:0 auto">
        <div style="font-size:1.2rem;font-weight:700;margin-bottom:4px">🎯 Rozbij stan na sztuki</div>
        <div id="rozbijNazwa" style="color:#94a3b8;font-size:0.85rem;margin-bottom:15px"></div>
        <div style="background:#12121a;border-radius:10px;padding:12px;margin-bottom:15px">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px">
            <span style="color:#94a3b8">Łącznie sztuk:</span>
            <span id="rozbijLacznie" style="font-weight:700"></span>
          </div>
          <div style="display:flex;justify-content:space-between">
            <span style="color:#94a3b8">Suma wpisanych:</span>
            <span id="rozbijSuma" style="font-weight:700;color:#22c55e"></span>
          </div>
        </div>
        <div id="rozbijStany"></div>
        <div style="color:#94a3b8;font-size:0.75rem;margin:12px 0 8px">Szybkie ustawienie:</div>
        <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:15px">
          <button onclick="rozbijSzybko('Nowy')" style="padding:6px 12px;background:#22c55e22;border:1px solid #22c55e;border-radius:8px;color:#22c55e;font-size:0.78rem;cursor:pointer">🟢 Wszystko nowe</button>
          <button onclick="rozbijSzybko('Powystawowy')" style="padding:6px 12px;background:#3b82f622;border:1px solid #3b82f6;border-radius:8px;color:#3b82f6;font-size:0.78rem;cursor:pointer">🔵 Powystawowe</button>
          <button onclick="rozbijSzybko('Używany')" style="padding:6px 12px;background:#eab30822;border:1px solid #eab308;border-radius:8px;color:#eab308;font-size:0.78rem;cursor:pointer">🟡 Używane</button>
          <button onclick="rozbijSzybko('Uszkodzony')" style="padding:6px 12px;background:#ef444422;border:1px solid #ef4444;border-radius:8px;color:#ef4444;font-size:0.78rem;cursor:pointer">🔴 Uszkodzone</button>
        </div>
        <div style="display:flex;gap:8px">
          <button onclick="rozbijWyczysc()" style="flex:1;padding:12px;background:#1e1e2e;border:1px solid #334155;border-radius:10px;color:#94a3b8;cursor:pointer">Wyczyść</button>
          <button onclick="zamknijRozbij()" style="flex:1;padding:12px;background:#334155;border:none;border-radius:10px;color:#fff;cursor:pointer">Anuluj</button>
          <button onclick="zapiszRozbij()" style="flex:1;padding:12px;background:#22c55e;border:none;border-radius:10px;color:#000;font-weight:700;cursor:pointer">✓ Zapisz</button>
        </div>
      </div>
    </div>

    <!-- MODAL: DO NAPRAWY -->
    <div id="modalNaprawa" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.8);z-index:1000;overflow-y:auto;padding:20px">
      <div style="background:#1e1e2e;border-radius:16px;padding:20px;max-width:440px;margin:0 auto">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
          <div style="font-size:1.2rem;font-weight:700">🔧 Do naprawy</div>
          <button onclick="zamknijNaprawa()" style="background:none;border:none;color:#94a3b8;font-size:1.3rem;cursor:pointer">✕</button>
        </div>
        <div id="naprawaNazwa" style="color:#94a3b8;font-size:0.85rem;margin-bottom:15px"></div>
        <div id="naprawaLista"></div>
        <button onclick="zamknijNaprawa()" style="width:100%;padding:12px;background:#334155;border:none;border-radius:10px;color:#fff;margin-top:10px;cursor:pointer">Zamknij</button>
      </div>
    </div>

    <!-- MENU KONTEKSTOWE -->
    <div id="menuKontekst" style="display:none;position:fixed;z-index:2000;background:#1e1e2e;border:1px solid #334155;border-radius:12px;padding:8px;min-width:220px;box-shadow:0 8px 32px rgba(0,0,0,0.6)">
      <div id="menuNaglowek" style="color:#64748b;font-size:0.7rem;font-weight:600;padding:4px 10px;margin-bottom:4px"></div>
      <div id="menuStatusy"></div>
      <div style="color:#64748b;font-size:0.7rem;font-weight:600;padding:4px 10px;margin:4px 0;border-top:1px solid #334155;padding-top:8px">INNE AKCJE</div>
      <div id="menuInne"></div>
    </div>

    <script>
    let _rozbijId = null, _rozbijIlosc = 0;
    let _naprawaId = null;
    let _menuId = null;

    const STANY_KOLORY = {
      'Nowy': '#22c55e', 'Powystawowy': '#3b82f6',
      'Używany': '#eab308', 'Uszkodzony': '#ef4444', 'Odnowiony': '#8b5cf6'
    };

    // ---- ROZBIJ NA SZTUKI ----
    function pokazRozbij(produktId, ilosc, nazwa) {
        _rozbijId = produktId; _rozbijIlosc = ilosc;
        document.getElementById('rozbijNazwa').textContent = nazwa;
        document.getElementById('rozbijLacznie').textContent = ilosc;
        fetch('/api/sztuki/' + produktId)
          .then(r => r.json()).then(d => {
            const istniejace = {};
            (d.sztuki || []).forEach(s => { istniejace[s.stan] = (istniejace[s.stan]||0)+1; });
            renderRozbijStany(istniejace);
          }).catch(() => renderRozbijStany({}));
        document.getElementById('modalRozbij').style.display = 'block';
    }
    function renderRozbijStany(wartosci) {
        const stany = ['Nowy','Powystawowy','Używany','Uszkodzony'];
        let html = '';
        stany.forEach(s => {
            const kolor = STANY_KOLORY[s];
            const val = wartosci[s] || 0;
            html += `<div style="display:flex;align-items:center;gap:12px;background:${kolor}11;border:1px solid ${kolor}44;border-radius:10px;padding:12px;margin-bottom:8px">
              <div style="width:14px;height:14px;border-radius:50%;background:${kolor};flex-shrink:0"></div>
              <div style="flex:1;font-weight:600">${s}</div>
              <button onclick="zmienjRozbij('${s}',-1)" style="width:36px;height:36px;background:#1e1e2e;border:1px solid #334155;border-radius:8px;color:#fff;font-size:1.1rem;cursor:pointer">−</button>
              <input type="number" id="rozbij_${s}" value="${val}" min="0" max="${_rozbijIlosc}"
                style="width:60px;text-align:center;background:#12121a;border:1px solid #334155;border-radius:8px;color:#fff;padding:6px;font-size:1rem"
                oninput="aktualizujSume()">
              <button onclick="zmienjRozbij('${s}',1)" style="width:36px;height:36px;background:#1e1e2e;border:1px solid #334155;border-radius:8px;color:#fff;font-size:1.1rem;cursor:pointer">+</button>
            </div>`;
        });
        document.getElementById('rozbijStany').innerHTML = html;
        aktualizujSume();
    }
    function zmienjRozbij(stan, delta) {
        const el = document.getElementById('rozbij_' + stan);
        el.value = Math.max(0, parseInt(el.value||0) + delta);
        aktualizujSume();
    }
    function aktualizujSume() {
        const stany = ['Nowy','Powystawowy','Używany','Uszkodzony'];
        let suma = 0;
        stany.forEach(s => { suma += parseInt(document.getElementById('rozbij_'+s)?.value||0); });
        const el = document.getElementById('rozbijSuma');
        el.textContent = suma + ' / ' + _rozbijIlosc;
        el.style.color = suma === _rozbijIlosc ? '#22c55e' : '#ef4444';
    }
    function rozbijSzybko(stan) {
        ['Nowy','Powystawowy','Używany','Uszkodzony'].forEach(s => {
            const el = document.getElementById('rozbij_'+s);
            if(el) el.value = s === stan ? _rozbijIlosc : 0;
        });
        aktualizujSume();
    }
    function rozbijWyczysc() {
        ['Nowy','Powystawowy','Używany','Uszkodzony'].forEach(s => {
            const el = document.getElementById('rozbij_'+s);
            if(el) el.value = 0;
        });
        aktualizujSume();
    }
    function zamknijRozbij() { document.getElementById('modalRozbij').style.display='none'; }
    function zapiszRozbij() {
        const stany = ['Nowy','Powystawowy','Używany','Uszkodzony'];
        let suma = 0, podzial = {};
        stany.forEach(s => {
            const v = parseInt(document.getElementById('rozbij_'+s)?.value||0);
            if(v > 0) { podzial[s] = v; suma += v; }
        });
        if(suma !== _rozbijIlosc) { alert('Suma musi wynosić ' + _rozbijIlosc + ' sztuk!'); return; }
        fetch('/api/sztuki/' + _rozbijId + '/rozbij', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({podzial})
        }).then(r=>r.json()).then(d => {
            if(d.ok) { zamknijRozbij(); location.reload(); }
        });
    }

    // ---- NAPRAWA ----
    function pokazNaprawa(produktId, nazwa, ilosc) {
        _naprawaId = produktId;
        document.getElementById('naprawaNazwa').textContent = nazwa + ' — ' + ilosc + ' szt.';
        document.getElementById('naprawaLista').innerHTML = '<div style="color:#64748b;text-align:center;padding:20px">Ładowanie...</div>';
        document.getElementById('modalNaprawa').style.display = 'block';
        fetch('/api/sztuki/' + produktId).then(r=>r.json()).then(d => {
            renderNaprawaLista(d.sztuki || [], ilosc);
        });
    }
    function renderNaprawaLista(sztuki, ilosc) {
        // Fill missing units
        const pelna = [];
        for(let i=1; i<=ilosc; i++) {
            pelna.push(sztuki.find(s=>s.numer===i) || {id:null, numer:i, stan:'Nowy', status:'magazyn', opis_naprawy:''});
        }
        let html = '';
        pelna.forEach(s => {
            if(s.status === 'naprawa') {
                html += `<div style="background:#f59e0b15;border:1px solid #f59e0b55;border-radius:10px;padding:12px;margin-bottom:8px">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                    <div style="font-weight:700;color:#f59e0b">🔧 szt. ${s.numer} <span style="font-size:0.7rem">DO NAPRAWY</span></div>
                    <div style="display:flex;gap:6px">
                      <button onclick="edytujNaprawa(${s.id}, '${(s.opis_naprawy||'').replace(/'/g,"\'")}')"
                        style="padding:4px 10px;background:#8b5cf6;border:none;border-radius:6px;color:#fff;font-size:0.72rem;cursor:pointer">✏️ Edytuj</button>
                      <button onclick="cofnijNaprawa(${s.id})"
                        style="padding:4px 10px;background:#ef444422;border:1px solid #ef4444;border-radius:6px;color:#ef4444;font-size:0.72rem;cursor:pointer">↩ Cofnij</button>
                    </div>
                  </div>
                  <div style="background:#1e1e2e;border-radius:6px;padding:8px;font-size:0.8rem">📝 ${s.opis_naprawy || '—'}</div>
                  ${s.data_naprawy ? `<div style="font-size:0.7rem;color:#64748b;margin-top:4px">${s.data_naprawy}</div>` : ''}
                </div>`;
            } else {
                html += `<div style="background:#12121a;border:1px solid #1e1e2e;border-radius:10px;padding:12px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center">
                  <div style="display:flex;align-items:center;gap:10px">
                    <div style="width:12px;height:12px;border-radius:3px;background:${STANY_KOLORY[s.stan]||'#64748b'}"></div>
                    <span style="font-weight:600">szt. ${s.numer}</span>
                    <span style="font-size:0.72rem;color:#64748b">${s.stan}</span>
                  </div>
                  <button onclick="dodajNaprawa(${s.id || 0}, ${s.numer}, ${_naprawaId})"
                    style="padding:6px 14px;background:#f59e0b;border:none;border-radius:8px;color:#000;font-size:0.75rem;font-weight:700;cursor:pointer">+ Do naprawy</button>
                </div>`;
            }
        });
        document.getElementById('naprawaLista').innerHTML = html;
    }
    function dodajNaprawa(sztukiId, numer, produktId) {
        const opis = prompt('Opis usterki dla szt. ' + numer + ':');
        if(opis === null) return;
        const doSave = (id) => {
            fetch('/api/sztuki/jednostka/' + id + '/naprawa', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({opis})
            }).then(r=>r.json()).then(() => {
                fetch('/api/sztuki/' + produktId).then(r=>r.json()).then(d => {
                    const ilosc = parseInt(document.getElementById('naprawaNazwa').textContent.match(/\d+ szt/)[0]);
                    renderNaprawaLista(d.sztuki||[], ilosc);
                    location.reload();
                });
            });
        };
        if(sztukiId > 0) { doSave(sztukiId); }
        else {
            // Auto-create unit first
            fetch('/api/sztuki/' + produktId + '/rozbij', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({podzial:{'Nowy': parseInt(document.getElementById('naprawaNazwa').textContent.match(/\d+/)[0])}})
            }).then(r=>r.json()).then(() => {
                fetch('/api/sztuki/' + produktId).then(r=>r.json()).then(d => {
                    const szt = (d.sztuki||[]).find(s=>s.numer===numer);
                    if(szt) doSave(szt.id);
                });
            });
        }
    }
    function edytujNaprawa(id, opisCurrent) {
        const opis = prompt('Edytuj opis naprawy:', opisCurrent);
        if(opis === null) return;
        fetch('/api/sztuki/jednostka/' + id + '/naprawa', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({opis})
        }).then(() => location.reload());
    }
    function cofnijNaprawa(id) {
        fetch('/api/sztuki/jednostka/' + id + '/naprawa', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({cofnij: true})
        }).then(() => location.reload());
    }
    function zamknijNaprawa() { document.getElementById('modalNaprawa').style.display='none'; }

    // ---- MENU KONTEKSTOWE ----
    function pokazMenu(evt, produktId, ilosc, nazwa) {
        evt.stopPropagation();
        _menuId = produktId;
        const menu = document.getElementById('menuKontekst');
        document.getElementById('menuNaglowek').textContent = 'ZMIEŃ STATUS (dostępne: ' + ilosc + '/' + ilosc + ')';
        document.getElementById('menuStatusy').innerHTML = `
            <div onclick="menuStatus('sprzedany')" class="menu-item">✅ Sprzedane</div>
            <div onclick="menuStatus('sprzedany_uszkodzony')" class="menu-item">⚠️ Sprzedane uszkodzone</div>
            <div onclick="menuNaprawyModal(${produktId}, '${nazwa.replace(/'/g,"\'")}', ${ilosc})" class="menu-item">🔧 Do naprawy...</div>
            <div onclick="menuStatus('wyrzucenie')" class="menu-item">🗑️ Do wyrzucenia</div>
            <div onclick="menuStatus('zwrot')" class="menu-item">↩️ Oddane (zwrot)</div>`;
        document.getElementById('menuInne').innerHTML = `
            <div onclick="pokazRozbij(${produktId}, ${ilosc}, '${nazwa.replace(/'/g,"\'")}'); zamknijMenu()" class="menu-item">🎯 Rozbij na sztuki</div>
            <a href="/magazyn/produkt/${produktId}/edytuj" class="menu-item" style="text-decoration:none;display:block;color:#fff">✏️ Edytuj produkt</a>`;
        const rect = evt.target.getBoundingClientRect();
        menu.style.display = 'block';
        menu.style.top = (rect.bottom + window.scrollY + 4) + 'px';
        menu.style.left = Math.min(rect.left, window.innerWidth - 240) + 'px';
    }
    function menuStatus(status) {
        if(!_menuId) return;
        zapiszPole(_menuId, 'status', status, document.createElement('span'));
        zamknijMenu();
        setTimeout(() => location.reload(), 300);
    }
    function menuNaprawyModal(id, nazwa, ilosc) {
        zamknijMenu();
        pokazNaprawa(id, nazwa, ilosc);
    }
    function zamknijMenu() { document.getElementById('menuKontekst').style.display='none'; }
    document.addEventListener('click', zamknijMenu);

    // Style dla menu
    document.head.insertAdjacentHTML('beforeend', `<style>
    .menu-item { padding:10px 14px; cursor:pointer; border-radius:8px; font-size:0.88rem; }
    .menu-item:hover { background:#334155; }
    </style>`);

    // Kropki stanów na kartach
    const KOLORY_STAN = {'Nowy':'#22c55e','Powystawowy':'#3b82f6','Używany':'#eab308','Uszkodzony':'#ef4444','Odnowiony':'#8b5cf6'};
    document.querySelectorAll('[data-produkt-id]').forEach(el => {
        const pid = el.dataset.produktId;
        const ilosc = parseInt(el.dataset.ilosc || 0);
        if(ilosc < 1) return;
        fetch('/api/sztuki/' + pid).then(r=>r.json()).then(d => {
            if(!d.sztuki || d.sztuki.length === 0) return;
            const counts = {};
            const naprawy = d.sztuki.filter(s => s.status === 'naprawa').length;
            d.sztuki.forEach(s => { counts[s.stan] = (counts[s.stan]||0)+1; });
            let html = '<div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:4px">';
            Object.entries(counts).forEach(([k,v]) => {
                const kolor = KOLORY_STAN[k] || '#64748b';
                html += `<span style="background:${kolor}33;border:1px solid ${kolor};color:${kolor};border-radius:20px;padding:1px 7px;font-size:0.62rem;font-weight:700">●${k.slice(0,3)} ${v}</span>`;
            });
            if(naprawy > 0) {
                html += `<span style="background:#f9730333;border:1px solid #f97316;color:#f97316;border-radius:20px;padding:1px 7px;font-size:0.62rem;font-weight:700">🔧${naprawy}</span>`;
            }
            html += '</div>';
            const dotsEl = el.querySelector('.sztuki-dots');
            if(dotsEl) dotsEl.innerHTML = html;
        }).catch(()=>{});
    });
    </script>
    '''
    return html




@app.route('/produkt/<int:produkt_id>/szybka-edycja', methods=['POST'])
def produkt_szybka_edycja(produkt_id):
    """Szybka inline zmiana pola produktu (stan, status) z palety"""
    from modules.database import get_db
    from flask import jsonify, request
    
    pole = request.form.get('pole', '').strip()
    wartosc = request.form.get('wartosc', '').strip()
    
    # Dozwolone pola do edycji inline
    DOZWOLONE = {'stan', 'status', 'lokalizacja', 'cena_allegro'}
    if pole not in DOZWOLONE:
        return jsonify({'ok': False, 'msg': 'Niedozwolone pole'}), 400
    
    conn = get_db()
    p = conn.execute('SELECT id, ilosc, status, nazwa, cena_allegro, cena_brutto FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not p:
        return jsonify({'ok': False, 'msg': 'Nie znaleziono'}), 404

    # Zabezpieczenie integralności: zmiana statusu na 'sprzedany' → zeruj ilosc + dodaj sprzedaz
    if pole == 'status' and wartosc == 'sprzedany' and (p['ilosc'] or 0) > 0:
        ilosc_sprzedana = p['ilosc'] or 1
        cena = float(p['cena_allegro'] or p['cena_brutto'] or 0)
        conn.execute('UPDATE produkty SET status = ?, ilosc = 0 WHERE id = ?', (wartosc, produkt_id))
        # Utwórz rekord sprzedaży żeby itemy nie "znikały" ze statystyk palety
        if cena > 0:
            from datetime import datetime
            conn.execute(
                '''INSERT INTO sprzedaze (produkt_id, nazwa, cena, ilosc, status, data_sprzedazy, kupujacy, notified)
                   VALUES (?, ?, ?, ?, 'sprzedana', ?, 'inline', 1)''',
                (produkt_id, p['nazwa'] or f'Produkt #{produkt_id}', cena, ilosc_sprzedana,
                 datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))
            )
        conn.commit()
        return jsonify({'ok': True, 'msg': f'Sprzedano {ilosc_sprzedana} szt.', 'reload': True})

    # Zabezpieczenie: zmiana statusu z 'sprzedany' na inny → jeśli ilosc=0, to ostrzeżenie
    if pole == 'status' and p['status'] == 'sprzedany' and wartosc != 'sprzedany' and (p['ilosc'] or 0) == 0:
        return jsonify({'ok': False, 'msg': 'Produkt ma ilość 0 — najpierw skoryguj ilość'}), 400

    conn.execute(f'UPDATE produkty SET {pole} = ? WHERE id = ?', (wartosc, produkt_id))
    conn.commit()
    return jsonify({'ok': True})

@app.route('/palety/<int:paleta_id>/delete', methods=['POST'])
def paleta_delete(paleta_id):
    """Usuwa pojedynczą paletę i wszystkie jej produkty"""
    from modules.database import get_db
    
    conn = get_db()
    
    # Pobierz ASIN-y produktów do usunięcia ze scraped
    asiny = conn.execute('SELECT asin FROM produkty WHERE paleta_id = ? AND asin IS NOT NULL', (paleta_id,)).fetchall()
    asiny_list = [row[0] for row in asiny if row[0]]
    
    # Usuń produkty z palety ze scraped (Paletomat)
    scraped_cnt = 0
    if asiny_list:
        placeholders = ','.join(['?' for _ in asiny_list])
        scraped_cnt = conn.execute(f'DELETE FROM scraped WHERE asin IN ({placeholders})', asiny_list).rowcount
    
    # Usuń produkty z palety
    produkty_cnt = conn.execute('DELETE FROM produkty WHERE paleta_id = ?', (paleta_id,)).rowcount
    
    # Usuń paletę
    conn.execute('DELETE FROM palety WHERE id = ?', (paleta_id,))
    conn.commit()
    
    return f'''
    <html><head><meta http-equiv="refresh" content="2;url=/palety"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px">✅</div>
            <div style="font-size:1.2rem">Paleta usunięta!</div>
            <div style="color:#64748b;margin-top:10px">
                Usunięto {produkty_cnt} produktów{f' i {scraped_cnt} z Palatomatu' if scraped_cnt > 0 else ''}
            </div>
        </div>
    </body></html>
    '''


@app.route('/historia')
def historia_globalna():
    """Globalna historia wszystkich produktów - ostatnie 100 akcji"""
    from modules.database import get_db
    
    conn = get_db()
    
    # Pobierz ostatnie 100 akcji ze wszystkich produktów
    historia = conn.execute('''
        SELECT h.*, p.nazwa as produkt_nazwa, p.ean, p.asin, p.lokalizacja
        FROM historia_produktu h
        LEFT JOIN produkty p ON h.produkt_id = p.id
        ORDER BY h.data DESC
        LIMIT 100
    ''').fetchall()
    
    
    # Ikony i kolory
    ikony = {
        'dodano': '📥', 'edytowano': '✏️', 'wystawiono': '🛒', 
        'sprzedano': '💰', 'wyslano': '📦', 'zmiana_ceny': '💵',
        'zmiana_lokalizacji': '📍', 'zmiana_ilosci': '📊',
        'drukowano': '🏷️', 'skanowano': '📱', 'importowano': '📂',
        'scrapowano': '🔍', 'wygenerowano_opis': '✨', 'dodano_zdjecia': '📷',
        'przeniesiono': '🔄', 'oznaczono': '🏷️'
    }
    kolory = {
        'dodano': 'rgba(59,130,246,0.1)', 
        'sprzedano': 'rgba(34,197,94,0.1)', 
        'wystawiono': 'rgba(139,92,246,0.1)', 
        'wyslano': 'rgba(34,197,94,0.1)', 
        'zmiana_ceny': 'rgba(234,179,8,0.1)',
        'skanowano': 'rgba(59,130,246,0.1)',
        'wygenerowano_opis': 'rgba(139,92,246,0.1)', 
        'importowano': 'rgba(59,130,246,0.1)', 
        'scrapowano': 'rgba(59,130,246,0.1)',
        'drukowano': 'rgba(139,92,246,0.1)'
    }
    
    historia_html = ''
    for h in historia:
        ikona = ikony.get(h['akcja'], '📌')
        bg_color = kolory.get(h['akcja'], 'rgba(30,30,46,0.5)')
        data_str = h['data'][:16] if h['data'] else ''
        produkt_info = h['produkt_nazwa'][:40] if h['produkt_nazwa'] else f"ID: {h['produkt_id']}"
        
        # Link do produktu
        produkt_link = f"/magazyn/produkt/{h['ean'] or h['asin'] or h['produkt_id']}"
        
        # Dane dodatkowe - konwertuj Row do dict dla .get()
        h_dict = dict(h)
        dane_extra = ''
        if h_dict.get('dane_json'):
            try:
                import json
                dane = json.loads(h_dict['dane_json'])
                if dane:
                    dane_extra = '<div style="font-size:0.7rem;color:#64748b;margin-top:4px">'
                    for k, v in dane.items():
                        if k not in ['allegro_id']:
                            dane_extra += f'<span style="background:rgba(30,30,46,0.5);padding:2px 6px;border-radius:4px;margin-right:6px">{k}: {v}</span>'
                    dane_extra += '</div>'
            except:
                pass
        
        lokalizacja_html = f'<div style="font-size:0.7rem;color:#64748b;margin-top:4px">📍 {h_dict.get("lokalizacja", "")}</div>' if h_dict.get('lokalizacja') else ''
        
        historia_html += f'''
        <div style="background:{bg_color};border:1px solid rgba(100,116,139,0.2);border-radius:12px;padding:15px;margin-bottom:12px">
            <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:8px">
                <div style="flex:1">
                    <div style="font-size:0.95rem;font-weight:600;color:#fff;margin-bottom:4px">
                        {ikona} {h['opis']}
                    </div>
                    <a href="{produkt_link}" style="font-size:0.8rem;color:#8b5cf6;text-decoration:none">
                        📦 {produkt_info}
                    </a>
                    {dane_extra}
                </div>
                <div style="text-align:right">
                    <div style="font-size:0.75rem;color:#64748b">{data_str}</div>
                    {lokalizacja_html}
                </div>
            </div>
        </div>
        '''
    
    if not historia_html:
        historia_html = '<div style="text-align:center;color:#64748b;padding:40px">Brak historii</div>'
    
    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>📜 HISTORIA WSZYSTKICH PRODUKTÓW</h1>
            <small>Ostatnie 100 akcji w systemie</small>
        </div>
        
        <div style="background:#1e1e2e;border-radius:12px;padding:15px;margin-bottom:20px">
            <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px">
                <div style="text-align:center">
                    <div style="font-size:1.5rem">📥</div>
                    <div style="font-size:0.7rem;color:#64748b;margin-top:4px">Import</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:1.5rem">🛒</div>
                    <div style="font-size:0.7rem;color:#64748b;margin-top:4px">Wystawiono</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:1.5rem">💰</div>
                    <div style="font-size:0.7rem;color:#64748b;margin-top:4px">Sprzedano</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:1.5rem">✏️</div>
                    <div style="font-size:0.7rem;color:#64748b;margin-top:4px">Edytowano</div>
                </div>
            </div>
        </div>
        
        {historia_html}
        
        <a href="/" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:20px">← Powrót</a>
    </div>
    '''
    return html


def print_banner():
    print("\n" + "="*60)
    print(f"  ⚡ AKCES HUB v{VERSION}")
    print("  Paletomat + Magazynier + Telegram + Allegro")
    print("="*60)
    print(f"  📦 Magazynier:  /magazyn")
    print(f"  🤖 Paletomat:   /paletomat")
    print(f"  💬 Telegram:    /telegram")
    print(f"  🛒 Allegro:     /allegro")
    print(f"  ⚡ Narzędzia:   /narzedzia")
    print("="*60)
# ═══════════════════════════════════════════════════════════════════════════
# AKCES HUB v3.0.21 - NOWY KOD DO WKLEJENIA
# ═══════════════════════════════════════════════════════════════════════════
# 
# INSTRUKCJA:
# 1. Otwórz app.py
# 2. Znajdź linię: 
@app.route('/poziom')
def poziom():
    """Strona celów i postępów — egzey style z realnymi danymi"""
    import json
    from datetime import date
    from modules.database import get_db

    conn = get_db()
    year = str(date.today().year)
    month = date.today().strftime('%Y-%m')

    try:
        # Przychód z bieżącego roku (tabela sprzedaze - Allegro)
        row = conn.execute(
            """SELECT COALESCE(SUM(cena * ilosc), 0) as total
               FROM sprzedaze
               WHERE status NOT IN ('anulowana','anulowane','zwrot')
               AND (kupujacy IS NULL OR kupujacy != 'offline')
               AND strftime('%Y', data_sprzedazy) = ?""", (year,)
        ).fetchone()
        przychod_rok = float(row['total'] or 0)

        # Przychód z prywatnych (sprzedaze_prywatne - spójne ze statystykami)
        try:
            row2 = conn.execute(
                """SELECT COALESCE(SUM(kwota), 0) as total
                   FROM sprzedaze_prywatne
                   WHERE strftime('%Y', data) = ?""", (year,)
            ).fetchone()
            przychod_rok += float(row2['total'] or 0)
        except:
            pass  # tabela może nie istnieć

        # Palety w bieżącym roku
        row3 = conn.execute(
            "SELECT COUNT(*) as cnt FROM palety WHERE strftime('%Y', data_zakupu) = ?",
            (year,)
        ).fetchone()
        palety_rok = int(row3['cnt'] or 0)

        # Palety w bieżącym miesiącu
        row4 = conn.execute(
            "SELECT COUNT(*) as cnt FROM palety WHERE strftime('%Y-%m', data_zakupu) = ?",
            (month,)
        ).fetchone()
        palety_msc = int(row4['cnt'] or 0)

        # Przychód w bieżącym miesiącu
        row5 = conn.execute(
            """SELECT COALESCE(SUM(cena * ilosc), 0) as total
               FROM sprzedaze
               WHERE status NOT IN ('anulowana','anulowane','zwrot')
               AND (kupujacy IS NULL OR kupujacy != 'offline')
               AND strftime('%Y-%m', data_sprzedazy) = ?""", (month,)
        ).fetchone()
        przychod_msc = float(row5['total'] or 0)

        # Miesiące minione w roku
        miesiac_nr = date.today().month
        sredni_msc = przychod_rok / miesiac_nr if miesiac_nr > 0 else 0
        avg_paleta = przychod_rok / palety_rok if palety_rok > 0 else 2000

        # Ile miesięcy do 1M przy obecnym tempie
        cel = 1_000_000
        brakuje = max(0, cel - przychod_rok)
        tempo = sredni_msc if sredni_msc > 0 else 33000
        miesiecy_do_celu = round(brakuje / tempo) if tempo > 0 else 999

        # Palety potrzebne/msc żeby trafić 1M
        palety_potrzeba_msc = round(cel / 12 / avg_paleta) if avg_paleta > 0 else 42


        real_data = {
            'przychod_rok': przychod_rok,
            'przychod_rok_fmt': f"{przychod_rok:,.0f}".replace(',', ' '),
            'cel': cel,
            'cel_fmt': '1 000 000',
            'xp_pct': min(99, round(przychod_rok / cel * 100)),
            'brakuje_fmt': f"{brakuje:,.0f}".replace(',', ' '),
            'miesiecy_do_celu': miesiecy_do_celu,
            'palety_rok': palety_rok,
            'palety_msc': palety_msc,
            'przychod_msc': przychod_msc,
            'przychod_msc_fmt': f"{przychod_msc:,.0f}".replace(',', ' '),
            'sredni_msc': sredni_msc,
            'sredni_msc_fmt': f"{sredni_msc:,.0f}".replace(',', ' '),
            'avg_paleta': avg_paleta,
            'avg_paleta_fmt': f"{avg_paleta:,.0f}".replace(',', ' '),
            'palety_potrzeba_msc': palety_potrzeba_msc,
            'cel_msc': 83333,
            'boss_pct': min(99, round(przychod_msc / 83333 * 100)),
            'year': year,
        }
    except Exception as e:
        print(f'⚠️ /poziom data error: {e}')
        real_data = {
            'przychod_rok': 0, 'przychod_rok_fmt': '0', 'cel': 1000000, 'cel_fmt': '1 000 000',
            'xp_pct': 0, 'brakuje_fmt': '1 000 000', 'miesiecy_do_celu': 999,
            'palety_rok': 0, 'palety_msc': 0, 'przychod_msc': 0, 'przychod_msc_fmt': '0',
            'sredni_msc': 0, 'sredni_msc_fmt': '0', 'avg_paleta': 2000, 'avg_paleta_fmt': '2 000',
            'palety_potrzeba_msc': 42, 'cel_msc': 83333, 'boss_pct': 0, 'year': year,
        }

    template_path = os.path.join(os.path.dirname(__file__), 'templates', 'poziom.html')
    with open(template_path, 'r', encoding='utf-8') as f:
        html = f.read()

    # Inject real data as JS variable
    inject = f'<script>window.REAL_DATA = {json.dumps(real_data)};</script>'
    html = html.replace('</head>', inject + '</head>')
    return html

# poziom route added above

# 3. PRZED TĄ LINIĄ wklej CAŁY ten kod
# 4. Zapisz plik (Ctrl+S)
#
# ═══════════════════════════════════════════════════════════════════════════

# IMPORT NOWEGO MODUŁU (dodaj to na górze z innymi importami, ale można też tutaj)
from modules.printing_config import (
    load_config, 
    save_config, 
    save_full_config,
    get_printer_settings,
    is_auto_print_enabled,
    get_default_printer
)

# ═══════════════════════════════════════════════════════════════════════════
# ROUTE: ANALITYKA - Mapa kupujących i rentowność kategorii
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/analityka')
def analityka_dashboard():
    """Dashboard analityczny - mapa kupujących i rentowność kategorii"""
    from modules.database import get_db
    from collections import defaultdict
    import re
    
    conn = get_db()
    
    # ========== MAPA KUPUJĄCYCH ==========
    # Pobierz wszystkie adresy ze sprzedaży
    sprzedaze = conn.execute('''
        SELECT s.adres, s.cena, s.ilosc, s.data_sprzedazy
        FROM sprzedaze s
        WHERE s.status NOT IN ('anulowana', 'zwrot') AND s.adres IS NOT NULL AND s.adres != ''
    ''').fetchall()
    
    # Wyciągnij miasta z adresów
    miasta_stats = defaultdict(lambda: {'zamowienia': 0, 'przychod': 0})
    
    for s in sprzedaze:
        adres = s['adres'] or ''
        miasto = None
        
        # Format 1: "ulica, XX-XXX, miasto" - miasto jest ostatnie
        parts = [p.strip() for p in adres.split(',')]
        if len(parts) >= 2:
            # Ostatnia część to miasto (po kodzie pocztowym)
            last_part = parts[-1]
            # Sprawdź czy nie jest to kod pocztowy
            if not re.match(r'^\d{2}-\d{3}$', last_part):
                miasto = last_part.title()
        
        # Format 2: "XX-XXX miasto" - kod + miasto razem
        if not miasto:
            match = re.search(r'\d{2}-\d{3}\s+([A-Za-zżźćńółęąśŻŹĆĄŚĘŁÓŃ\s\-]+)', adres)
            if match:
                miasto = match.group(1).strip().title()
        
        if miasto and len(miasto) > 2 and len(miasto) < 50:
            miasta_stats[miasto]['zamowienia'] += 1
            miasta_stats[miasto]['przychod'] += (s['cena'] or 0) * (s['ilosc'] or 1)
    
    # Sortuj miasta po liczbie zamówień
    miasta_sorted = sorted(miasta_stats.items(), key=lambda x: x[1]['zamowienia'], reverse=True)[:20]
    
    # ========== RENTOWNOŚĆ KATEGORII ==========
    # Pobierz dane o sprzedażach - używamy nazwy ze sprzedaży do kategoryzacji
    sprzedaze_all = conn.execute('''
        SELECT
            s.id,
            s.nazwa as sprzedaz_nazwa,
            s.cena,
            s.ilosc,
            COALESCE(p.kategoria, p2.kategoria) as produkt_kategoria,
            CASE
                WHEN sc.sale_cnt > 0 AND pal.cena_zakupu > 0
                THEN pal.cena_zakupu / sc.sale_cnt
                ELSE 0
            END as produkt_koszt
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        LEFT JOIN oferty o ON s.oferta_id = o.id
        LEFT JOIN produkty p2 ON o.produkt_id = p2.id
        LEFT JOIN palety pal ON COALESCE(p.paleta_id, p2.paleta_id) = pal.id
        LEFT JOIN (
            SELECT pal2.id as paleta_id,
                   CASE
                       WHEN COALESCE(SUM(s2.ilosc), 0) > pal2.ilosc_produktow
                       THEN COALESCE(SUM(s2.ilosc), 0)
                       ELSE pal2.ilosc_produktow
                   END as sale_cnt
            FROM palety pal2
            LEFT JOIN produkty p3 ON p3.paleta_id = pal2.id
            LEFT JOIN sprzedaze s2 ON s2.produkt_id = p3.id
                AND s2.status NOT IN ('anulowana', 'zwrot')
            GROUP BY pal2.id
        ) sc ON pal.id = sc.paleta_id
        WHERE s.status NOT IN ('anulowana', 'zwrot')
    ''').fetchall()
    
    # Grupuj per kategoria (używamy auto_kategoryzuj jeśli brak kategorii z produktu)
    kategorie_map = {}
    for s in sprzedaze_all:
        # Ustal kategorię: z produktu, z produktu przez ofertę, lub auto z nazwy
        kategoria = s['produkt_kategoria']
        if not kategoria or kategoria == 'inne':
            # Auto-kategoryzuj z nazwy sprzedaży
            kategoria = auto_kategoryzuj(s['sprzedaz_nazwa'] or '')
        
        if kategoria not in kategorie_map:
            kategorie_map[kategoria] = {'sprzedazy': 0, 'przychod': 0, 'koszt': 0}
        
        kategorie_map[kategoria]['sprzedazy'] += 1
        kategorie_map[kategoria]['przychod'] += (s['cena'] or 0) * (s['ilosc'] or 1)
        kategorie_map[kategoria]['koszt'] += (s['produkt_koszt'] or 0)
    
    # Oblicz zysk i marżę dla każdej kategorii
    kategorie_stats = []
    for kategoria, data in kategorie_map.items():
        przychod = data['przychod']
        koszt = data['koszt']
        prowizja = przychod * 0.11  # Allegro ~11%
        zysk = przychod - koszt - prowizja
        marza = (zysk / przychod * 100) if przychod > 0 else 0
        
        # Użyj ładnej nazwy z KATEGORIE_DISPLAY
        kategoria_display = KATEGORIE_DISPLAY.get(kategoria, kategoria or 'Inne')
        
        kategorie_stats.append({
            'kategoria': kategoria_display,
            'sprzedazy': data['sprzedazy'],
            'przychod': przychod,
            'koszt': koszt,
            'prowizja': prowizja,
            'zysk': zysk,
            'marza': marza
        })
    
    # Sortuj po zysku
    kategorie_stats.sort(key=lambda x: x['zysk'], reverse=True)
    
    # ========== SPRZEDAŻ W CZASIE (DZIENNIE) ==========
    sprzedaz_dni = conn.execute('''
        SELECT 
            DATE(data_sprzedazy) as dzien,
            COUNT(*) as liczba,
            COALESCE(SUM(cena * ilosc), 0) as przychod
        FROM sprzedaze
        WHERE status NOT IN ('anulowana', 'zwrot')
          AND data_sprzedazy >= date('now', '-30 days')
        GROUP BY DATE(data_sprzedazy)
        ORDER BY dzien ASC
    ''').fetchall()
    
    # Przychód skumulowany (narastająco)
    przychod_kumulowany = []
    suma = 0
    for d in sprzedaz_dni:
        suma += d['przychod']
        przychod_kumulowany.append(suma)
    
    # ========== PRODUKTY BEZ KATEGORII ==========
    produkty_bez_kat = conn.execute('''
        SELECT COUNT(*) as cnt FROM produkty WHERE kategoria IS NULL OR kategoria = '' OR kategoria = 'inne'
    ''').fetchone()['cnt']
    
    # Przygotuj dane do wykresów
    miasta_labels = [m[0] for m in miasta_sorted]
    miasta_values = [m[1]['zamowienia'] for m in miasta_sorted]
    miasta_przychod = [m[1]['przychod'] for m in miasta_sorted]
    
    kategorie_labels = [k['kategoria'][:15] for k in kategorie_stats[:10]]
    kategorie_zysk = [k['zysk'] for k in kategorie_stats[:10]]
    kategorie_marza = [k['marza'] for k in kategorie_stats[:10]]
    
    # Dane dzienne do wykresu
    dni_labels = [d['dzien'][5:] for d in sprzedaz_dni]  # Format MM-DD
    dni_przychod = [d['przychod'] for d in sprzedaz_dni]
    dni_kumulowany = przychod_kumulowany  # Narastająco
    
    # ========== TOP/FLOP PRODUKTY ==========
    # Pobierz produkty posortowane po zysku - z ceną zakupu
    top_flop_data = conn.execute('''
        SELECT
            s.nazwa,
            SUM(s.ilosc) as ilosc_sprzedazy,
            SUM(s.cena * s.ilosc) as przychod,
            AVG(s.cena) as srednia_cena,
            AVG(CASE
                WHEN pal.cena_zakupu > 0 AND pal_sum.total_szt > 0
                THEN pal.cena_zakupu / pal_sum.total_szt
                ELSE 0
            END) as avg_koszt_paleta
        FROM sprzedaze s
        LEFT JOIN produkty p2 ON s.produkt_id = p2.id
        LEFT JOIN palety pal ON p2.paleta_id = pal.id
        LEFT JOIN (SELECT paleta_id, SUM(COALESCE(ilosc, 1)) as total_szt FROM produkty GROUP BY paleta_id) pal_sum ON p2.paleta_id = pal_sum.paleta_id
        WHERE s.status NOT IN ('anulowana', 'zwrot')
        AND s.nazwa IS NOT NULL AND s.nazwa != ''
        GROUP BY s.nazwa
        HAVING SUM(s.ilosc) >= 1
        ORDER BY przychod DESC
    ''').fetchall()

    # TOP 10 - najlepsze produkty
    top_produkty = []
    for p in top_flop_data[:10]:
        przychod = p['przychod'] or 0
        ilosc = p['ilosc_sprzedazy'] or 1
        prowizja = przychod * 0.11  # Allegro ~11%

        # Koszt zakupu - z palety (cena_zakupu / ilość produktów w palecie)
        koszt_unit = p['avg_koszt_paleta'] or 0
        koszt_total = koszt_unit * ilosc

        zysk = przychod - koszt_total - prowizja

        top_produkty.append({
            'nazwa': (p['nazwa'] or '')[:50],
            'ilosc': ilosc,
            'przychod': przychod,
            'srednia_cena': p['srednia_cena'] or 0,
            'koszt_total': koszt_total,
            'prowizja': prowizja,
            'zysk': zysk,
            'has_koszt': koszt_unit > 0
        })
    
    # FLOP - produkty które się nie sprzedają (z magazynu)
    flop_produkty = conn.execute('''
        SELECT 
            p.nazwa,
            p.cena_brutto as cena_zakupu,
            p.cena_allegro as cena_sprzedazy,
            p.ilosc as stan,
            p.kategoria,
            julianday('now') - julianday(p.data_dodania) as dni_w_magazynie
        FROM produkty p
        WHERE p.status IN ('magazyn', 'wystawiony')
        AND p.ilosc > 0
        AND p.data_dodania IS NOT NULL
        ORDER BY dni_w_magazynie DESC
        LIMIT 10
    ''').fetchall()
    
    flop_lista = []
    for p in flop_produkty:
        dni = int(p['dni_w_magazynie'] or 0)
        flop_lista.append({
            'nazwa': (p['nazwa'] or '')[:50],
            'cena_zakupu': p['cena_zakupu'] or 0,
            'cena_sprzedazy': p['cena_sprzedazy'] or 0,
            'stan': p['stan'] or 0,
            'dni': dni,
            'kategoria': KATEGORIE_DISPLAY.get(p['kategoria'], p['kategoria'] or 'Inne')
        })
    
    # ========== SPRZEDAŻE PRYWATNE ==========
    try:
        pryw_row = conn.execute('SELECT COUNT(*) as cnt, COALESCE(SUM(kwota), 0) as suma FROM sprzedaze_prywatne').fetchone()
        prywatne_suma = pryw_row['suma'] or 0
        prywatne_cnt = pryw_row['cnt'] or 0
    except:
        prywatne_suma = 0
        prywatne_cnt = 0


    # Łączny zysk = Allegro zysk + prywatne
    allegro_zysk = sum(k['zysk'] for k in kategorie_stats)
    laczny_zysk = allegro_zysk + prywatne_suma

    # Warning HTML - musi być poza f-stringiem
    warning_html = ''
    if produkty_bez_kat > 0:
        warning_html = f'<div class="warning-box"><span>&#9888; <strong>{produkty_bez_kat}</strong> produktow bez kategorii</span><a href="/analityka/kategorie" class="action-btn">Przypisz kategorie</a></div>'

    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>📊 Analityka - {get_config_cached("brand_name", "AKCES HUB")}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #0a0a0f; 
                color: #e2e8f0;
                min-height: 100vh;
                padding: 20px;
            }}
            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 30px;
                padding-bottom: 20px;
                border-bottom: 1px solid #1e1e2e;
            }}
            .header h1 {{ color: #fff; font-size: 1.8rem; }}
            .header-btns {{ display: flex; gap: 10px; }}
            .back-btn, .action-btn {{
                padding: 10px 20px;
                color: #fff;
                text-decoration: none;
                border-radius: 8px;
                font-weight: 600;
            }}
            .back-btn {{ background: #3b82f6; }}
            .action-btn {{ background: #8b5cf6; }}
            .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(500px, 1fr)); gap: 20px; }}
            .card {{
                background: #12121a;
                border: 1px solid #1e1e2e;
                border-radius: 16px;
                padding: 20px;
            }}
            .card h2 {{
                color: #fff;
                font-size: 1.2rem;
                margin-bottom: 20px;
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            .chart-container {{ position: relative; height: 300px; }}
            .stats-table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 0.85rem;
            }}
            .stats-table th, .stats-table td {{
                padding: 10px;
                text-align: left;
                border-bottom: 1px solid #1e1e2e;
            }}
            .stats-table th {{ color: #64748b; font-weight: 500; }}
            .stats-table tr:hover {{ background: rgba(59, 130, 246, 0.1); }}
            .positive {{ color: #22c55e; }}
            .negative {{ color: #ef4444; }}
            .miasto-bar {{
                height: 8px;
                background: linear-gradient(90deg, #3b82f6, #8b5cf6);
                border-radius: 4px;
                margin-top: 4px;
            }}
            .summary-cards {{
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 15px;
                margin-bottom: 20px;
            }}
            .summary-card {{
                background: linear-gradient(135deg, #1e1e2e, #12121a);
                border: 1px solid #2a2a3a;
                border-radius: 12px;
                padding: 15px;
                text-align: center;
            }}
            .summary-card .value {{ font-size: 1.5rem; font-weight: 700; color: #fff; }}
            .summary-card .label {{ font-size: 0.75rem; color: #64748b; margin-top: 5px; }}
            .warning-box {{
                background: rgba(245, 158, 11, 0.1);
                border: 1px solid rgba(245, 158, 11, 0.3);
                border-radius: 12px;
                padding: 15px;
                margin-bottom: 20px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .warning-box span {{ color: #f59e0b; }}
            @media (max-width: 768px) {{
                .grid {{ grid-template-columns: 1fr; }}
                .summary-cards {{ grid-template-columns: repeat(2, 1fr); }}
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>&#x1F4CA; Analityka sprzedazy</h1>
            <div class="header-btns">
                <button onclick="uzupelnijAdresy()" class="action-btn" style="background:#22c55e;border:none;cursor:pointer">&#x1F4CD; Uzupelnij adresy</button>
                <button onclick="autoKategoryzujWszystkie()" class="action-btn" style="background:#f59e0b;border:none;cursor:pointer">&#x1F916; Auto-kategorie</button>
                <a href="/analityka/palety" class="action-btn" style="background:#3b82f6;text-decoration:none">📦 Bilans palet</a>
                <a href="/analityka/kategorie" class="action-btn">&#x1F3F7; Edytuj kategorie</a>
                <a href="/analityka/czas-sprzedazy" class="action-btn" style="background:#22c55e;text-decoration:none">⏱️ Czas sprzedaży</a>
                <a href="/magazyn/raport-sprzedazy" class="action-btn" style="background:#059669;text-decoration:none">📊 Eksport Excel</a>
                <a href="/" class="back-btn">&larr; Powrot</a>
            </div>
        </div>
        
        ''' + warning_html + f'''
        
        <div class="summary-cards">
            <div class="summary-card">
                <div class="value">{len(miasta_stats)}</div>
                <div class="label">&#x1F3D9; MIAST</div>
            </div>
            <div class="summary-card">
                <div class="value">{sum(m[1]['zamowienia'] for m in miasta_stats.items())}</div>
                <div class="label">&#x1F4E6; ZAMOWIEN</div>
            </div>
            <div class="summary-card">
                <div class="value">{len(kategorie_stats)}</div>
                <div class="label">&#x1F4C1; KATEGORII</div>
            </div>
            <div class="summary-card">
                <div class="value">{laczny_zysk:.0f} zl</div>
                <div class="label">&#x1F4B0; LACZNY ZYSK</div>
                <div style="font-size:0.65rem;color:#94a3b8;margin-top:4px">{allegro_zysk:.0f} Allegro{f' + {prywatne_suma:.0f} prywatne' if prywatne_suma > 0 else ''}</div>
            </div>
        </div>
        
        <div class="grid">
            <!-- MAPA KUPUJĄCYCH -->
            <div class="card">
                <h2>🗺️ Skąd kupują klienci (TOP 20)</h2>
                <div class="chart-container">
                    <canvas id="miastaChart"></canvas>
                </div>
            </div>
            
            <!-- RENTOWNOŚĆ KATEGORII -->
            <div class="card">
                <h2>💰 Rentowność kategorii (TOP 10)</h2>
                <div class="chart-container">
                    <canvas id="kategorieChart"></canvas>
                </div>
            </div>
            
            <!-- SPRZEDAŻ W CZASIE -->
            <div class="card">
                <h2>&#x1F4C8; Przychod (ostatnie 30 dni)</h2>
                <div class="chart-container">
                    <canvas id="czasChart"></canvas>
                </div>
            </div>
            
            <!-- TOP/FLOP PRODUKTY -->
            <div class="card" style="grid-column: span 2;">
                <h2>🏆 TOP 10 Bestsellerów vs 📉 FLOP (najdłużej w magazynie)</h2>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 30px;">
                    <!-- TOP 10 -->
                    <div>
                        <h3 style="color: #22c55e; margin-bottom: 15px;">🥇 Bestsellery (wg przychodu)</h3>
                        <table class="stats-table">
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>Produkt</th>
                                    <th>Szt.</th>
                                    <th>Przychód</th>
                                    <th>Zakup</th>
                                    <th>Prowizja</th>
                                    <th>Zysk</th>
                                </tr>
                            </thead>
                            <tbody>
                                {''.join(f"""
                                <tr>
                                    <td style="color: {'#ffd700' if i==0 else '#c0c0c0' if i==1 else '#cd7f32' if i==2 else '#888'};">
                                        {'🥇' if i==0 else '🥈' if i==1 else '🥉' if i==2 else str(i+1)}
                                    </td>
                                    <td title="{p['nazwa']}">{p['nazwa'][:30]}{'...' if len(p['nazwa'])>30 else ''}</td>
                                    <td>{p['ilosc']}</td>
                                    <td style="color: #22c55e;">{p['przychod']:.0f} zł</td>
                                    <td style="color: {'#ef4444' if p['has_koszt'] else '#555'};">{p['koszt_total']:.0f}{' zł' if p['has_koszt'] else ' ?'}</td>
                                    <td style="color: #f59e0b;">{p['prowizja']:.0f} zł</td>
                                    <td style="color: {'#22c55e' if p['zysk']>0 else '#ef4444'}; font-weight:700;">{p['zysk']:.0f} zł</td>
                                </tr>
                                """ for i, p in enumerate(top_produkty))}
                            </tbody>
                        </table>
                    </div>
                    
                    <!-- FLOP -->
                    <div>
                        <h3 style="color: #ef4444; margin-bottom: 15px;">📉 Najdłużej w magazynie</h3>
                        <table class="stats-table">
                            <thead>
                                <tr>
                                    <th>Produkt</th>
                                    <th>Dni</th>
                                    <th>Zakup</th>
                                    <th>Cena</th>
                                    <th>Kategoria</th>
                                </tr>
                            </thead>
                            <tbody>
                                {''.join(f"""
                                <tr>
                                    <td title="{p['nazwa']}">{p['nazwa'][:30]}{'...' if len(p['nazwa'])>30 else ''}</td>
                                    <td style="color: {'#ef4444' if p['dni']>60 else '#f59e0b' if p['dni']>30 else '#888'};">
                                        {p['dni']} dni
                                    </td>
                                    <td>{p['cena_zakupu']:.0f} zł</td>
                                    <td>{p['cena_sprzedazy']:.0f} zł</td>
                                    <td style="font-size: 0.85em;">{p['kategoria'][:15]}</td>
                                </tr>
                                """ for p in flop_lista) if flop_lista else '<tr><td colspan="5" style="text-align:center;color:#888;">Brak danych</td></tr>'}
                            </tbody>
                        </table>
                        <p style="font-size: 0.8em; color: #666; margin-top: 10px;">
                            💡 Produkty > 60 dni warto przecenić lub wystawić na OLX/Vinted
                        </p>
                    </div>
                </div>
            </div>
            
            <!-- TABELA MIAST -->
            <div class="card">
                <h2>🏙️ Szczegóły miast</h2>
                <table class="stats-table">
                    <thead>
                        <tr>
                            <th>Miasto</th>
                            <th>Zamówienia</th>
                            <th>Przychód</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(f"""
                        <tr>
                            <td>{m[0]}</td>
                            <td>{m[1]['zamowienia']}</td>
                            <td class="positive">{m[1]['przychod']:.0f} zł</td>
                        </tr>
                        """ for m in miasta_sorted[:15]) if miasta_sorted else '<tr><td colspan="3" style="text-align:center;color:#64748b">Brak danych o miastach</td></tr>'}
                    </tbody>
                </table>
            </div>
            
            <!-- TABELA KATEGORII -->
            <div class="card" style="grid-column: span 2;">
                <h2>📊 Szczegóły kategorii</h2>
                <table class="stats-table">
                    <thead>
                        <tr>
                            <th>Kategoria</th>
                            <th>Szt.</th>
                            <th>Przychód</th>
                            <th>Zakup</th>
                            <th>Prowizja</th>
                            <th>Zysk</th>
                            <th>Marża</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(f"""
                        <tr>
                            <td>{k['kategoria']}</td>
                            <td>{k['sprzedazy']}</td>
                            <td style="color:#22c55e">{k['przychod']:.0f} zł</td>
                            <td style="color:#ef4444">-{k['koszt']:.0f} zł</td>
                            <td style="color:#f59e0b">-{k['prowizja']:.0f} zł</td>
                            <td class="{'positive' if k['zysk'] >= 0 else 'negative'}" style="font-weight:700">{k['zysk']:.0f} zł</td>
                            <td class="{'positive' if k['marza'] >= 0 else 'negative'}">{k['marza']:.1f}%</td>
                        </tr>
                        """ for k in kategorie_stats) if kategorie_stats else '<tr><td colspan="7" style="text-align:center;color:#64748b">Brak danych o sprzedażach</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>
        
        <script>
            // Wykres miast
            new Chart(document.getElementById('miastaChart'), {{
                type: 'bar',
                data: {{
                    labels: {miasta_labels},
                    datasets: [{{
                        label: 'Zamówienia',
                        data: {miasta_values},
                        backgroundColor: 'rgba(59, 130, 246, 0.8)',
                        borderRadius: 4
                    }}]
                }},
                options: {{
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ display: false }} }},
                    scales: {{
                        x: {{ grid: {{ color: '#1e1e2e' }}, ticks: {{ color: '#64748b' }} }},
                        y: {{ grid: {{ display: false }}, ticks: {{ color: '#e2e8f0' }} }}
                    }}
                }}
            }});
            
            // Wykres kategorii
            new Chart(document.getElementById('kategorieChart'), {{
                type: 'bar',
                data: {{
                    labels: {kategorie_labels},
                    datasets: [{{
                        label: 'Zysk (zł)',
                        data: {kategorie_zysk},
                        backgroundColor: {kategorie_zysk}.map(v => v >= 0 ? 'rgba(34, 197, 94, 0.8)' : 'rgba(239, 68, 68, 0.8)'),
                        borderRadius: 4
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ display: false }} }},
                    scales: {{
                        x: {{ grid: {{ display: false }}, ticks: {{ color: '#e2e8f0', maxRotation: 45 }} }},
                        y: {{ grid: {{ color: '#1e1e2e' }}, ticks: {{ color: '#64748b' }} }}
                    }}
                }}
            }});
            
            // Wykres czasowy - dzienny z kumulowanym
            new Chart(document.getElementById('czasChart'), {{
                type: 'line',
                data: {{
                    labels: {dni_labels},
                    datasets: [
                        {{
                            label: 'Narastajaco (zl)',
                            data: {dni_kumulowany},
                            borderColor: '#22c55e',
                            backgroundColor: 'rgba(34, 197, 94, 0.15)',
                            fill: true,
                            tension: 0.3,
                            pointRadius: 4,
                            pointBackgroundColor: '#22c55e',
                            pointBorderColor: '#fff',
                            pointBorderWidth: 2,
                            order: 1
                        }},
                        {{
                            label: 'Dziennie (zl)',
                            data: {dni_przychod},
                            borderColor: '#8b5cf6',
                            backgroundColor: 'rgba(139, 92, 246, 0.3)',
                            fill: false,
                            tension: 0,
                            pointRadius: 5,
                            pointBackgroundColor: '#8b5cf6',
                            pointBorderColor: '#fff',
                            pointBorderWidth: 2,
                            type: 'bar',
                            order: 2
                        }}
                    ]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ 
                        legend: {{ display: true, position: 'top', labels: {{ color: '#e2e8f0' }} }}
                    }},
                    scales: {{
                        x: {{ grid: {{ color: '#1e1e2e' }}, ticks: {{ color: '#64748b', maxRotation: 45 }} }},
                        y: {{ grid: {{ color: '#1e1e2e' }}, ticks: {{ color: '#64748b' }} }}
                    }}
                }}
            }});
            
            // Funkcja uzupełniania adresów
            function uzupelnijAdresy() {{
                if (!confirm('Pobrac adresy z Allegro dla istniejacych zamowien?')) return;
                
                fetch('/analityka/uzupelnij-adresy', {{method: 'POST'}})
                    .then(r => r.json())
                    .then(data => {{
                        if (data.ok) {{
                            alert('Zaktualizowano ' + data.count + ' adresow z ' + data.total);
                            location.reload();
                        }} else {{
                            alert('Blad: ' + (data.error || 'Nieznany'));
                        }}
                    }})
                    .catch(e => alert('Blad: ' + e));
            }}
            
            // Funkcja auto-kategoryzacji wszystkich produktów
            function autoKategoryzujWszystkie() {{
                if (!confirm('Automatycznie przypisac kategorie do WSZYSTKICH produktow na podstawie nazw?')) return;
                
                fetch('/analityka/kategorie/auto', {{method: 'POST'}})
                    .then(r => r.json())
                    .then(data => {{
                        if (data.ok) {{
                            alert('Zaktualizowano ' + data.count + ' produktow!');
                            location.reload();
                        }} else {{
                            alert('Blad: ' + (data.error || 'Nieznany'));
                        }}
                    }})
                    .catch(e => alert('Blad: ' + e));
            }}
        </script>
    </body>
    </html>
    '''


# ═══════════════════════════════════════════════════════════════════════════
# ROUTE: BILANS PALET - ROI per paleta
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/analityka/palety')
def analityka_palety():
    """Bilans palet - koszt vs przychód, ROI"""
    from modules.database import get_db
    
    conn = get_db()
    
    # Pobierz wszystkie palety z pełnymi statystykami
    palety = conn.execute('''
        SELECT 
            p.id,
            p.nazwa,
            p.dostawca,
            p.cena_zakupu,
            p.data_zakupu,
            p.ilosc_produktow,
            (SELECT COUNT(*) FROM produkty WHERE paleta_id = p.id) as produktow_w_bazie,
            (SELECT COALESCE(SUM(ilosc), 0) FROM produkty WHERE paleta_id = p.id) as aktualna_ilosc,
            (SELECT COALESCE(SUM(cena_brutto), 0) FROM produkty WHERE paleta_id = p.id) as koszt_produktow,
            (SELECT COALESCE(SUM(cena_allegro * ilosc), 0) FROM produkty WHERE paleta_id = p.id AND status = 'dostepny') as wartosc_magazynu,
            (SELECT COALESCE(SUM(CASE WHEN status = 'sprzedany' AND (sprzedano_offline IS NULL OR sprzedano_offline = 0) THEN cena_allegro ELSE 0 END), 0) FROM produkty WHERE paleta_id = p.id) as przychod_produkty,
            COALESCE((SELECT SUM(s.cena * s.ilosc) FROM sprzedaze s JOIN produkty pr ON s.produkt_id = pr.id WHERE pr.paleta_id = p.id AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot','')), 0) as przychod_tabela,
            (SELECT COALESCE(SUM(przychod_offline), 0) FROM produkty WHERE paleta_id = p.id) as przychod_offline,
            (SELECT COALESCE(SUM(sprzedano_offline), 0) FROM produkty WHERE paleta_id = p.id) as sprzedano_offline_szt,
            (SELECT COUNT(*) FROM produkty WHERE paleta_id = p.id AND status = 'sprzedany' AND (sprzedano_offline IS NULL OR sprzedano_offline = 0)) as sprzedano_produkty,
            COALESCE((SELECT SUM(s.ilosc) FROM sprzedaze s JOIN produkty pr ON s.produkt_id = pr.id WHERE pr.paleta_id = p.id AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot','')), 0) as sprzedano_tabela,
            COALESCE((SELECT SUM(s.cena * s.ilosc) FROM sprzedaze s JOIN produkty pr ON s.produkt_id = pr.id WHERE pr.paleta_id = p.id AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot','') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')), 0) as przychod_allegro_only
        FROM palety p
        ORDER BY p.data_zakupu DESC
    ''').fetchall()
    
    # Oblicz statystyki dla każdej palety
    palety_stats = []
    total_koszt = 0
    total_przychod = 0
    total_zysk = 0
    
    for p in palety:
        koszt = p['cena_zakupu'] or 0
        przychod_produkty = p['przychod_produkty'] or 0  # z produktów ze statusem 'sprzedany' (cena_allegro)
        przychod_tabela = p['przychod_tabela'] or 0  # z tabeli sprzedaze (SUM cena*ilosc)
        przychod_offline = p['przychod_offline'] or 0  # stary system (produkty.przychod_offline)

        # FIX: przychod_tabela JUŻ ZAWIERA sprzedaże offline (kupujacy='offline')
        # więc NIE dodajemy przychod_offline osobno — bo to podwójne liczenie!
        # przychod_offline dodajemy TYLKO jeśli nie ma ich w sprzedaze (przychod_tabela=0)
        if przychod_tabela > 0:
            # Nowy system: wszystko (Allegro + offline) jest w tabeli sprzedaze
            przychod = przychod_tabela
        else:
            # Stary system: brak rekordów w sprzedaze, użyj danych z produktów
            przychod = przychod_produkty + przychod_offline

        # Prowizja Allegro ~11% — TYLKO od Allegro (nie offline)
        przychod_allegro_only = p['przychod_allegro_only'] or 0
        prowizja = przychod_allegro_only * 0.11
        zysk = przychod - koszt - prowizja
        roi = (zysk / koszt * 100) if koszt > 0 else 0
        
        # Status palety - liczymy SZTUKI
        # FIX: sprzedano_tabela JUŻ ZAWIERA offline (kupujacy='offline')
        # więc nie dodajemy sprzedano_offline osobno!

        aktualna_ilosc = p['aktualna_ilosc'] or 0  # ile teraz jest w magazynie
        sprzedanych_offline = p['sprzedano_offline_szt'] or 0  # stary system
        sprzedano_tabela = p['sprzedano_tabela'] or 0  # z tabeli sprzedaze (ile sztuk) — zawiera offline
        sprzedano_produkty = p['sprzedano_produkty'] or 0  # COUNT produktów ze statusem 'sprzedany'

        # FIX: Jeśli mamy dane w sprzedaze — nie dodawaj offline osobno
        if sprzedano_tabela > 0:
            sprzedanych = sprzedano_tabela
        else:
            sprzedanych = sprzedano_produkty + sprzedanych_offline
        
        # Wszystkich = w magazynie + sprzedanych
        wszystkich = aktualna_ilosc + sprzedanych
        
        # Zostało = aktualna ilość w magazynie
        zostalo = aktualna_ilosc
        
        if wszystkich == 0:
            status = 'pusta'
            status_color = '#666'
        elif zostalo == 0 and sprzedanych > 0:
            status = 'zakończona'
            status_color = '#22c55e' if zysk > 0 else '#ef4444'
        else:
            progress = (sprzedanych / wszystkich * 100) if wszystkich > 0 else 0
            status = f'{progress:.0f}% sprzedane'
            status_color = '#f59e0b' if progress < 100 else '#22c55e'
        
        # Koszt per sztuka
        koszt_szt = (koszt / wszystkich) if wszystkich > 0 else 0

        # Prognoza zysku (dla palet w trakcie sprzedaży)
        if sprzedanych > 0 and zostalo > 0:
            avg_cena = przychod / sprzedanych
            prognoza_przychod = avg_cena * wszystkich
            prognoza_prowizja = prognoza_przychod * 0.11
            prognoza = prognoza_przychod - koszt - prognoza_prowizja
        else:
            prognoza = zysk

        palety_stats.append({
            'id': p['id'],
            'nazwa': p['nazwa'] or f"Paleta #{p['id']}",
            'dostawca': p['dostawca'] or '-',
            'data': p['data_zakupu'] or '-',
            'koszt': koszt,
            'przychod': przychod,
            'przychod_allegro': przychod_tabela or przychod_produkty,
            'przychod_offline': przychod_offline if przychod_tabela == 0 else 0,
            'prowizja': prowizja,
            'zysk': zysk,
            'roi': roi,
            'koszt_szt': koszt_szt,
            'prognoza': prognoza,
            'wszystkich': wszystkich,
            'zostalo': zostalo,
            'sprzedanych': sprzedanych,
            'wartosc_magazynu': p['wartosc_magazynu'] or 0,
            'status': status,
            'status_color': status_color
        })
        
        total_koszt += koszt
        total_przychod += przychod
        total_zysk += zysk
    
    total_prowizja = sum(p['prowizja'] for p in palety_stats)
    total_roi = (total_zysk / total_koszt * 100) if total_koszt > 0 else 0

    # Unikalni dostawcy do filtra
    dostawcy = sorted(set(p['dostawca'] for p in palety_stats if p['dostawca'] != '-'))

    # Dane do wykresu - TOP 10 palet wg ROI
    top_palety = sorted([p for p in palety_stats if p['koszt'] > 0], key=lambda x: x['roi'], reverse=True)[:10]
    chart_labels = [p['nazwa'][:20] for p in top_palety]
    chart_roi = [p['roi'] for p in top_palety]

    # Wykres kumulacyjny zysku w czasie
    sorted_by_date = sorted([p for p in palety_stats if p['data'] != '-'], key=lambda x: x['data'])
    cum_dates = [p['data'] for p in sorted_by_date]
    cum_zysk = []
    running = 0
    for p in sorted_by_date:
        running += p['zysk']
        cum_zysk.append(round(running, 2))
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bilans Palet - {get_config_cached("brand_name", "AKCES HUB")}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #0a0a0f;
                color: #e2e8f0;
                min-height: 100vh;
                padding: 20px;
            }}
            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 30px;
                padding-bottom: 20px;
                border-bottom: 1px solid #1e1e2e;
            }}
            .header h1 {{ color: #fff; font-size: 1.8rem; }}
            .header a {{ color: #888; text-decoration: none; }}
            .header a:hover {{ color: #fff; }}
            .summary-grid {{
                display: grid;
                grid-template-columns: repeat(5, 1fr);
                gap: 16px;
                margin-bottom: 30px;
            }}
            .summary-card {{
                background: linear-gradient(135deg, #1a1a2e 0%, #16162a 100%);
                border-radius: 12px;
                padding: 18px;
                text-align: center;
            }}
            .summary-card .value {{
                font-size: 1.8rem;
                font-weight: bold;
                margin-bottom: 4px;
            }}
            .summary-card .label {{
                color: #888;
                font-size: 0.85rem;
            }}
            .green {{ color: #22c55e; }}
            .red {{ color: #ef4444; }}
            .yellow {{ color: #f59e0b; }}
            .blue {{ color: #3b82f6; }}
            .content-grid {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 20px;
                margin-bottom: 30px;
            }}
            .card {{
                background: linear-gradient(135deg, #1a1a2e 0%, #16162a 100%);
                border-radius: 12px;
                padding: 20px;
            }}
            .card h2 {{ margin-bottom: 20px; font-size: 1.2rem; }}
            .chart-container {{ height: 300px; }}
            .filter-bar {{
                display: flex;
                gap: 12px;
                align-items: center;
                margin-bottom: 15px;
            }}
            .filter-bar select {{
                padding: 8px 12px;
                background: #1e1e2e;
                border: 1px solid #2a2a3e;
                border-radius: 8px;
                color: #e2e8f0;
                font-size: 0.9rem;
            }}
            .filter-bar label {{ color: #888; font-size: 0.9rem; }}
            .palety-table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 0.85rem;
            }}
            .palety-table th, .palety-table td {{
                padding: 10px 6px;
                text-align: left;
                border-bottom: 1px solid #2a2a3e;
            }}
            .palety-table th {{
                color: #888;
                font-weight: 500;
                font-size: 0.75rem;
                text-transform: uppercase;
                cursor: pointer;
                user-select: none;
                white-space: nowrap;
            }}
            .palety-table th:hover {{ color: #fff; }}
            .palety-table th .sort-arrow {{ font-size: 0.7rem; margin-left: 3px; display: inline-block; min-width: 10px; transition: opacity 0.15s; }}
            .palety-table tbody tr {{
                cursor: pointer;
                transition: background 0.15s;
            }}
            .palety-table tbody tr:hover {{
                background: rgba(255,255,255,0.04);
            }}
            .status-badge {{
                padding: 4px 8px;
                border-radius: 4px;
                font-size: 0.7rem;
                white-space: nowrap;
            }}
            .progress-bar {{
                width: 100%;
                height: 6px;
                background: #2a2a3e;
                border-radius: 3px;
                overflow: hidden;
            }}
            .progress-fill {{
                height: 100%;
                background: linear-gradient(90deg, #22c55e, #3b82f6);
                transition: width 0.3s;
            }}
            .prognoza {{ color: #94a3b8; font-style: italic; }}
            @media (max-width: 900px) {{
                .summary-grid {{ grid-template-columns: repeat(2, 1fr); }}
                .content-grid {{ grid-template-columns: 1fr; }}
                .palety-table {{ font-size: 0.75rem; }}
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Bilans Palet</h1>
            <a href="/analityka">&larr; Powrot do Analityki</a>
        </div>

        <div class="summary-grid">
            <div class="summary-card">
                <div class="value blue">{len(palety_stats)}</div>
                <div class="label">Palet lacznie</div>
            </div>
            <div class="summary-card">
                <div class="value red">{total_koszt:,.0f} zl</div>
                <div class="label">Koszt zakupu</div>
            </div>
            <div class="summary-card">
                <div class="value green">{total_przychod:,.0f} zl</div>
                <div class="label">Przychod</div>
            </div>
            <div class="summary-card">
                <div class="value yellow">{total_prowizja:,.0f} zl</div>
                <div class="label">Prowizje Allegro</div>
            </div>
            <div class="summary-card">
                <div class="value {'green' if total_zysk >= 0 else 'red'}">{total_zysk:,.0f} zl ({total_roi:.0f}%)</div>
                <div class="label">Zysk netto (ROI)</div>
            </div>
        </div>

        <div class="content-grid">
            <div class="card">
                <h2>TOP 10 Palet wg ROI</h2>
                <div class="chart-container">
                    <canvas id="roiChart"></canvas>
                </div>
            </div>
            <div class="card">
                <h2>Zysk kumulacyjny w czasie</h2>
                <div class="chart-container">
                    <canvas id="cumChart"></canvas>
                </div>
            </div>
        </div>

        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;">
                <h2>Wszystkie palety ({len(palety_stats)})</h2>
                <div class="filter-bar">
                    <input type="text" id="searchInput" oninput="filterTable()" placeholder="Szukaj palety..." style="padding:8px 12px;background:#1e1e2e;border:1px solid #2a2a3e;border-radius:8px;color:#e2e8f0;font-size:0.9rem;width:200px;">
                    <label>Dostawca:</label>
                    <select id="dostawcaFilter" onchange="filterTable()">
                        <option value="">Wszyscy</option>
                        {''.join(f'<option value="{d}">{d}</option>' for d in dostawcy)}
                    </select>
                </div>
            </div>
            <div style="overflow-x:auto;">
            <table class="palety-table" id="paletyTable">
                <thead>
                    <tr>
                        <th onclick="sortTable(0,'str')">Paleta <span class="sort-arrow"></span></th>
                        <th onclick="sortTable(1,'str')">Dostawca <span class="sort-arrow"></span></th>
                        <th onclick="sortTable(2,'str')">Data <span class="sort-arrow"></span></th>
                        <th onclick="sortTable(3,'num')">Koszt <span class="sort-arrow"></span></th>
                        <th onclick="sortTable(4,'num')">Koszt/szt <span class="sort-arrow"></span></th>
                        <th onclick="sortTable(5,'num')">Przychod <span class="sort-arrow"></span></th>
                        <th onclick="sortTable(6,'num')">Zysk <span class="sort-arrow"></span></th>
                        <th onclick="sortTable(7,'num')">ROI <span class="sort-arrow"></span></th>
                        <th onclick="sortTable(8,'num')">Prognoza <span class="sort-arrow"></span></th>
                        <th>Postep</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(f"""
                    <tr onclick="window.location='/palety/{p['id']}'" data-dostawca="{p['dostawca']}" data-vals="{p['nazwa'][:30]}|{p['dostawca']}|{p['data']}|{p['koszt']:.2f}|{p['koszt_szt']:.2f}|{p['przychod']:.2f}|{p['zysk']:.2f}|{p['roi']:.2f}|{p['prognoza']:.2f}">
                        <td><strong>{p['nazwa'][:30]}</strong></td>
                        <td>{p['dostawca']}</td>
                        <td>{p['data']}</td>
                        <td>{p['koszt']:,.0f} zl</td>
                        <td>{p['koszt_szt']:,.0f} zl</td>
                        <td class="green">{p['przychod']:,.0f} zl</td>
                        <td class="{'green' if p['zysk'] >= 0 else 'red'}">{p['zysk']:,.0f} zl</td>
                        <td class="{'green' if p['roi'] >= 0 else 'red'}">{p['roi']:.0f}%</td>
                        <td class="prognoza{' green' if p['prognoza'] >= 0 else ' red'}">{p['prognoza']:,.0f} zl</td>
                        <td>
                            <div class="progress-bar">
                                <div class="progress-fill" style="width:{(p['sprzedanych']/p['wszystkich']*100) if p['wszystkich']>0 else 0}%"></div>
                            </div>
                            <small style="color:#888">{p['sprzedanych']}/{p['wszystkich']} szt.</small>
                        </td>
                        <td><span class="status-badge" style="background:{p['status_color']}20;color:{p['status_color']}">{p['status']}</span></td>
                    </tr>
                    """ for p in palety_stats) if palety_stats else '<tr><td colspan="11" style="text-align:center;color:#888;">Brak palet w bazie</td></tr>'}
                </tbody>
            </table>
            </div>
        </div>

        <script>
            // Wykres ROI
            new Chart(document.getElementById('roiChart'), {{
                type: 'bar',
                data: {{
                    labels: {chart_labels},
                    datasets: [{{
                        label: 'ROI %',
                        data: {chart_roi},
                        backgroundColor: {[f"'{'#22c55e' if r >= 0 else '#ef4444'}'" for r in chart_roi]},
                        borderRadius: 4
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ display: false }} }},
                    scales: {{
                        y: {{ grid: {{ color: '#2a2a3e' }}, ticks: {{ color: '#888', callback: v => v + '%' }} }},
                        x: {{ grid: {{ display: false }}, ticks: {{ color: '#888', maxRotation: 45 }} }}
                    }}
                }}
            }});

            // Wykres kumulacyjny zysku
            new Chart(document.getElementById('cumChart'), {{
                type: 'line',
                data: {{
                    labels: {cum_dates},
                    datasets: [{{
                        label: 'Zysk kumulacyjny (zl)',
                        data: {cum_zysk},
                        borderColor: '#3b82f6',
                        backgroundColor: 'rgba(59,130,246,0.1)',
                        fill: true,
                        tension: 0.3,
                        pointRadius: 3,
                        pointBackgroundColor: '#3b82f6'
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ display: false }} }},
                    scales: {{
                        y: {{ grid: {{ color: '#2a2a3e' }}, ticks: {{ color: '#888', callback: v => v + ' zl' }} }},
                        x: {{ grid: {{ display: false }}, ticks: {{ color: '#888', maxRotation: 45 }} }}
                    }}
                }}
            }});

            // Sortowanie tabeli
            let sortCol = -1, sortAsc = true;
            function sortTable(col, type) {{
                const table = document.getElementById('paletyTable');
                const tbody = table.querySelector('tbody');
                const rows = Array.from(tbody.querySelectorAll('tr'));
                if (sortCol === col) {{ sortAsc = !sortAsc; }} else {{ sortCol = col; sortAsc = true; }}
                rows.sort((a, b) => {{
                    const av = a.dataset.vals.split('|')[col] || '';
                    const bv = b.dataset.vals.split('|')[col] || '';
                    let cmp;
                    if (type === 'num') {{ cmp = parseFloat(av||0) - parseFloat(bv||0); }}
                    else {{ cmp = av.localeCompare(bv, 'pl'); }}
                    return sortAsc ? cmp : -cmp;
                }});
                rows.forEach(r => tbody.appendChild(r));
                // Aktualizuj strzalki
                table.querySelectorAll('.sort-arrow').forEach((s, i) => {{
                    s.textContent = i === col ? (sortAsc ? '\\u25B2' : '\\u25BC') : '';
                }});
            }}

            // Filtr dostawcy + wyszukiwanie
            function filterTable() {{
                const dost = document.getElementById('dostawcaFilter').value;
                const search = (document.getElementById('searchInput').value || '').toLowerCase();
                const rows = document.querySelectorAll('#paletyTable tbody tr');
                rows.forEach(r => {{
                    const matchDost = !dost || r.dataset.dostawca === dost;
                    const matchSearch = !search || (r.dataset.vals || '').toLowerCase().includes(search);
                    r.style.display = (matchDost && matchSearch) ? '' : 'none';
                }});
            }}
        </script>
    </body>
    </html>
    '''


@app.route('/analityka/kategorie')
def analityka_kategorie():
    """Masowa edycja kategorii produktów"""
    from modules.database import get_db
    
    conn = get_db()
    
    # Pobierz wszystkie produkty z ich kategoriami
    produkty = conn.execute('''
        SELECT id, nazwa, kategoria, cena_allegro, status, paleta_id
        FROM produkty
        ORDER BY kategoria, nazwa
    ''').fetchall()
    
    
    # Użyj globalnego słownika kategorii
    kategorie = KATEGORIE_DISPLAY
    
    produkty_html = ''
    for p in produkty:
        kat = p['kategoria'] or 'inne'
        sugerowana = auto_kategoryzuj(p['nazwa'])
        zmiana = sugerowana != kat
        
        produkty_html += f'''
        <tr data-id="{p['id']}" data-kat="{kat}">
            <td><input type="checkbox" class="produkt-check" value="{p['id']}"></td>
            <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{p['nazwa']}">{(p['nazwa'] or '')[:50]}</td>
            <td>{p['cena_allegro']:.0f} zł</td>
            <td>
                <select class="kat-select" data-id="{p['id']}" style="padding:6px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:6px;color:#fff">
                    {''.join(f'<option value="{k}" {"selected" if kat == k else ""}>{v}</option>' for k, v in kategorie.items())}
                </select>
            </td>
            <td>{'<span style="color:#f59e0b">💡 ' + kategorie.get(sugerowana, sugerowana) + '</span>' if zmiana else '<span style="color:#22c55e">✓</span>'}</td>
        </tr>
        '''
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>🏷️ Edycja kategorii - {get_config_cached("brand_name", "AKCES HUB")}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #0a0a0f; 
                color: #e2e8f0;
                min-height: 100vh;
                padding: 20px;
            }}
            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
            }}
            .header h1 {{ color: #fff; font-size: 1.5rem; }}
            .btn {{
                padding: 10px 20px;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                cursor: pointer;
                text-decoration: none;
                color: #fff;
            }}
            .btn-blue {{ background: #3b82f6; }}
            .btn-green {{ background: #22c55e; }}
            .btn-purple {{ background: #8b5cf6; }}
            .toolbar {{
                display: flex;
                gap: 10px;
                margin-bottom: 20px;
                flex-wrap: wrap;
                align-items: center;
                background: #12121a;
                padding: 15px;
                border-radius: 12px;
            }}
            .toolbar select {{
                padding: 10px;
                background: #1e1e2e;
                border: 1px solid #2a2a3a;
                border-radius: 8px;
                color: #fff;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: #12121a;
                border-radius: 12px;
                overflow: hidden;
            }}
            th, td {{
                padding: 12px;
                text-align: left;
                border-bottom: 1px solid #1e1e2e;
            }}
            th {{ background: #1e1e2e; color: #64748b; font-weight: 500; }}
            tr:hover {{ background: rgba(59, 130, 246, 0.1); }}
            .stats {{
                display: flex;
                gap: 15px;
                margin-bottom: 20px;
            }}
            .stat {{
                background: #12121a;
                padding: 15px 20px;
                border-radius: 10px;
                text-align: center;
            }}
            .stat .num {{ font-size: 1.3rem; font-weight: 700; color: #fff; }}
            .stat .label {{ font-size: 0.75rem; color: #64748b; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🏷️ Edycja kategorii produktów</h1>
            <a href="/analityka" class="btn btn-blue">← Powrót</a>
        </div>
        
        <div class="stats">
            <div class="stat">
                <div class="num">{len(produkty)}</div>
                <div class="label">PRODUKTÓW</div>
            </div>
            <div class="stat">
                <div class="num">{sum(1 for p in produkty if auto_kategoryzuj(p['nazwa']) != (p['kategoria'] or 'inne'))}</div>
                <div class="label">DO ZMIANY</div>
            </div>
        </div>
        
        <div class="toolbar">
            <label><input type="checkbox" id="selectAll"> Zaznacz wszystkie</label>
            <span style="color:#64748b">|</span>
            <span>Ustaw zaznaczonym:</span>
            <select id="bulkKategoria">
                {''.join(f'<option value="{k}">{v}</option>' for k, v in kategorie.items())}
            </select>
            <button class="btn btn-purple" onclick="bulkUpdate()">📝 Zastosuj</button>
            <span style="color:#64748b">|</span>
            <button class="btn btn-green" onclick="autoKategoryzuj()">🤖 Auto-kategoryzuj wszystkie</button>
        </div>
        
        <table>
            <thead>
                <tr>
                    <th style="width:40px"></th>
                    <th>Nazwa produktu</th>
                    <th>Cena</th>
                    <th>Kategoria</th>
                    <th>Sugestia AI</th>
                </tr>
            </thead>
            <tbody>
                {produkty_html}
            </tbody>
        </table>
        
        <script>
            document.getElementById('selectAll').addEventListener('change', function() {{
                document.querySelectorAll('.produkt-check').forEach(cb => cb.checked = this.checked);
            }});
            
            // Zmiana pojedynczej kategorii
            document.querySelectorAll('.kat-select').forEach(select => {{
                select.addEventListener('change', function() {{
                    const id = this.dataset.id;
                    const kat = this.value;
                    fetch('/analityka/kategorie/update', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{id: id, kategoria: kat}})
                    }}).then(r => r.json()).then(data => {{
                        if (data.ok) {{
                            this.style.borderColor = '#22c55e';
                            setTimeout(() => this.style.borderColor = '#2a2a3a', 1000);
                        }}
                    }});
                }});
            }});
            
            function bulkUpdate() {{
                const ids = [...document.querySelectorAll('.produkt-check:checked')].map(cb => cb.value);
                if (ids.length === 0) {{ alert('Zaznacz produkty'); return; }}
                
                const kat = document.getElementById('bulkKategoria').value;
                
                fetch('/analityka/kategorie/bulk-update', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{ids: ids, kategoria: kat}})
                }}).then(r => r.json()).then(data => {{
                    if (data.ok) {{
                        alert('Zaktualizowano ' + data.count + ' produktów');
                        location.reload();
                    }}
                }});
            }}
            
            function autoKategoryzuj() {{
                if (!confirm('Automatycznie przypisać kategorie do wszystkich produktów?')) return;
                
                fetch('/analityka/kategorie/auto', {{
                    method: 'POST'
                }}).then(r => r.json()).then(data => {{
                    if (data.ok) {{
                        alert('Zaktualizowano ' + data.count + ' produktów');
                        location.reload();
                    }}
                }});
            }}
        </script>
    </body>
    </html>
    '''


@app.route('/analityka/kategorie/update', methods=['POST'])
def analityka_kategorie_update():
    """Aktualizuj kategorię pojedynczego produktu"""
    from modules.database import get_db
    import json
    
    data = request.get_json()
    produkt_id = data.get('id')
    kategoria = data.get('kategoria')
    
    conn = get_db()
    conn.execute('UPDATE produkty SET kategoria = ? WHERE id = ?', (kategoria, produkt_id))
    conn.commit()
    
    return jsonify({'ok': True})


@app.route('/analityka/kategorie/bulk-update', methods=['POST'])
def analityka_kategorie_bulk_update():
    """Aktualizuj kategorie wielu produktów"""
    from modules.database import get_db
    
    data = request.get_json()
    ids = data.get('ids', [])
    kategoria = data.get('kategoria')
    
    if not ids:
        return jsonify({'ok': False, 'error': 'Brak produktów'})
    
    conn = get_db()
    placeholders = ','.join('?' * len(ids))
    conn.execute(f'UPDATE produkty SET kategoria = ? WHERE id IN ({placeholders})', [kategoria] + ids)
    conn.commit()
    
    return jsonify({'ok': True, 'count': len(ids)})


@app.route('/analityka/kategorie/auto', methods=['POST'])
def analityka_kategorie_auto():
    """Automatycznie kategoryzuj wszystkie produkty"""
    from modules.database import get_db
    
    conn = get_db()
    produkty = conn.execute('SELECT id, nazwa, kategoria FROM produkty').fetchall()
    
    print(f"\n🔍 Auto-kategoryzacja: {len(produkty)} produktów w bazie")
    
    count = 0
    stats = {}  # Statystyki kategorii
    
    for p in produkty:
        nazwa = p['nazwa'] or ''
        obecna_kat = p['kategoria'] or 'inne'
        nowa_kat = auto_kategoryzuj(nazwa)
        
        # Zlicz statystyki
        stats[nowa_kat] = stats.get(nowa_kat, 0) + 1
        
        # Aktualizuj jeśli kategoria jest inna
        if nowa_kat != obecna_kat:
            conn.execute('UPDATE produkty SET kategoria = ? WHERE id = ?', (nowa_kat, p['id']))
            count += 1
            print(f"  🏷️ [{p['id']}] {obecna_kat} → {nowa_kat}: {nazwa[:50]}")
        
    # Pokaż pierwsze 5 nazw produktów dla diagnostyki
    print(f"\n📋 Przykładowe nazwy produktów:")
    for p in produkty[:5]:
        print(f"  - {p['nazwa'][:60] if p['nazwa'] else '(brak nazwy)'}")
    
    print(f"\n📊 Statystyki kategorii:")
    for kat, cnt in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {kat}: {cnt}")
    
    conn.commit()
    
    print(f"\n✅ Zaktualizowano {count}/{len(produkty)} produktów")
    return jsonify({'ok': True, 'count': count, 'total': len(produkty)})


# ═══════════════════════════════════════════════════════════════════════════
# BINGO 2026 - Strona + API
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/bingo2026')
def bingo2026_page():
    """Pełna strona Bingo 2026"""
    bingo_html = """
    <div class="container">
        <div class="header">
            <h1>&#127919; BINGO 2026</h1>
            <small>Odkryj wszystkie cele i zdobądź BINGO!</small>
        </div>
        <a href="/" style="display:inline-block;margin-bottom:15px;color:#64748b;text-decoration:none;font-size:0.9rem">&#8592; Powrót do domu</a>
        <div id="bingo-info" style="background:linear-gradient(135deg,rgba(139,92,246,0.15),rgba(109,40,217,0.1));border:2px solid rgba(139,92,246,0.4);border-radius:16px;padding:16px;margin-bottom:15px;text-align:center">
            <div style="font-size:2rem;font-weight:800;color:#8b5cf6" id="bingo-big-cnt">...</div>
            <div style="font-size:0.85rem;color:#94a3b8">celów osiągniętych z 25</div>
            <div style="background:rgba(0,0,0,0.3);border-radius:8px;height:10px;overflow:hidden;margin:10px 0">
                <div id="bingo-pbar" style="background:linear-gradient(90deg,#8b5cf6,#22c55e);height:100%;width:0%;transition:width 0.6s ease"></div>
            </div>
            <div id="bingo-blines" style="font-size:0.9rem;color:#22c55e;font-weight:700;min-height:20px"></div>
        </div>
        <div id="bingo-grid-full" style="display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-bottom:15px"></div>
        <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;margin-bottom:80px">
            <div style="font-size:0.75rem;color:#64748b;margin-bottom:4px">&#128202; Dane live z bazy</div>
            <div id="bingo-live" style="font-size:0.8rem;color:#94a3b8"></div>
        </div>
    </div>
    <style>
    .bingo-big{aspect-ratio:1;border-radius:10px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:5px;border:1px solid #2a2a3a;background:#0d0d1a;transition:all 0.3s;position:relative}
    .bingo-big.ok{background:linear-gradient(135deg,#22c55e22,#16a34a11);border-color:#22c55e88;box-shadow:0 0 10px rgba(34,197,94,0.25)}
    .bingo-big.free-cell{background:linear-gradient(135deg,#f59e0b22,#d9770611) !important;border-color:#f59e0b88 !important}
    .bingo-big .ci{font-size:1.3rem}.bingo-big .cn{font-size:0.6rem;font-weight:700;color:#e2e8f0;margin-top:3px;line-height:1.1}
    .bingo-big .cd{font-size:0.5rem;color:#64748b;line-height:1.1;margin-top:1px}
    .bingo-big.ok .cn{color:#22c55e}.bingo-big.free-cell .cn{color:#f59e0b !important}
    .bingo-big .ck{display:none;position:absolute;top:3px;right:4px;font-size:0.7rem;color:#22c55e;font-weight:700}
    .bingo-big.ok .ck{display:block}
    </style>
    <script>
    fetch('/api/bingo2026').then(r=>r.json()).then(data=>{
        const g=document.getElementById('bingo-grid-full');
        g.innerHTML=data.cells.map(c=>{
            let cls='bingo-big'+(c.achieved?' ok':'')+(c.id===13?' free-cell':'');
            return '<div class="'+cls+'"><span class="ci">'+c.icon+'</span><span class="cn">'+c.name+'</span><span class="cd">'+c.desc+'</span><span class="ck">&#10003;</span></div>';
        }).join('');
        const pct=(data.achieved_count/25*100).toFixed(0);
        document.getElementById('bingo-big-cnt').textContent=data.achieved_count+' / 25';
        document.getElementById('bingo-pbar').style.width=pct+'%';
        if(data.bingo_lines&&data.bingo_lines.length>0){
            document.getElementById('bingo-blines').textContent='&#127881; BINGO! '+data.bingo_lines.join(', ');
        }
        document.getElementById('bingo-live').innerHTML=
            '&#128176; Przychód 2026: '+Math.round(data.przychod_2026).toLocaleString('pl-PL')+' zł &bull; '+
            '&#128197; Najlepszy dzień: '+Math.round(data.best_day_kwota).toLocaleString('pl-PL')+' zł &bull; '+
            '&#128197; Najlepszy mies: '+Math.round(data.best_month_kwota).toLocaleString('pl-PL')+' zł &bull; '+
            '&#128230; Max palet/mies: '+data.max_palet_miesiac;
    });
    </script>
    """
    return CSS + bingo_html


# ============================================================
# AKCES HUB PUBLIC API
# ============================================================

def get_api_key():
    """Czyta klucz API z env / config DB / generuje nowy"""
    import secrets
    # 1. Zmienna środowiskowa
    key = os.environ.get('AKCES_API_KEY')
    if key:
        return key.strip()
    # 2. Config DB
    try:
        from modules.database import get_config, set_config
        key = get_config('akces_api_key', '')
        if key:
            return key
    except Exception:
        pass
    # 3. Legacy plik (migracja → DB)
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'api_key.txt')
    if os.path.exists(key_file):
        key = open(key_file).read().strip()
        try:
            from modules.database import set_config
            set_config('akces_api_key', key)
        except Exception:
            pass
        return key
    # 4. Generuj nowy i zapisz do DB
    key = secrets.token_hex(24)
    try:
        from modules.database import set_config
        set_config('akces_api_key', key)
    except Exception:
        pass
    print(f'Wygenerowano nowy klucz API (config DB)')
    return key

AKCES_API_KEY = None  # Lazy-loaded przy pierwszym zapytaniu

def require_api_key(f):
    """Dekorator sprawdzajacy klucz API"""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        global AKCES_API_KEY
        if AKCES_API_KEY is None:
            AKCES_API_KEY = get_api_key()
        key = request.headers.get('X-API-Key') or request.args.get('api_key')
        if key != AKCES_API_KEY:
            return jsonify({'error': 'Unauthorized', 'hint': 'Dodaj naglowek X-API-Key lub parametr ?api_key='}), 401
        return f(*args, **kwargs)
    return decorated


@app.route('/api/key', methods=['GET'])
def api_show_key():
    """Pokazuje klucz API (tylko z localhost)"""
    if request.remote_addr not in ('127.0.0.1', '::1', 'localhost'):
        return jsonify({'error': 'Tylko z localhost'}), 403
    global AKCES_API_KEY
    if AKCES_API_KEY is None:
        AKCES_API_KEY = get_api_key()
    return jsonify({'api_key': AKCES_API_KEY, 'hint': 'Uzyj naglowka X-API-Key lub ?api_key= w zapytaniu'})


@app.route('/api/trendy')
@require_api_key
def api_trendy():
    """GET /api/trendy - TOP okazje z tabeli trendy (dla Perplexity / zewnetrznych narzedzi)"""
    from modules.database import get_db
    conn = get_db()
    miesiac = request.args.get('miesiac', datetime.now().strftime('%Y-%m'))
    limit   = min(int(request.args.get('limit', 20)), 100)

    # Sprawdz czy tabela trendy istnieje
    has_trendy = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trendy'"
    ).fetchone()

    if not has_trendy:
        return jsonify({'error': 'Tabela trendy nie istnieje. Uruchom: python analyze_trends.py', 'trendy': [], 'okazje': []})

    rows = conn.execute("""
        SELECT t.produkt_id, p.nazwa, p.kategoria, p.dostawca,
               t.miesiac, t.sprzedaz_szt, t.przychod, COALESCE(t.koszt,0) as koszt, t.roi, t.trend_mm, t.okazja_score
        FROM trendy t
        LEFT JOIN produkty p ON t.produkt_id = p.id
        WHERE t.miesiac = ?
        ORDER BY t.okazja_score DESC, t.przychod DESC
        LIMIT ?
    """, (miesiac, limit)).fetchall()

    okazje = []
    wszystkie = []
    for r in rows:
        item = {
            'produkt_id':   r[0],
            'nazwa':        r[1] or 'brak nazwy',
            'kategoria':    r[2] or 'inne',
            'dostawca':     r[3] or '',
            'miesiac':      r[4],
            'sprzedaz_szt': r[5],
            'przychod':     r[6],
            'koszt':        r[7],
            'roi':          r[8],
            'trend_mm':     r[9],
            'okazja_score': r[10],
        }
        wszystkie.append(item)
        if r[10] >= 7:
            okazje.append(item)

    # Kategorie
    kat_rows = conn.execute("""
        SELECT kategoria, miesiac, sprzedaz_szt, przychod, trend_mm
        FROM trendy_kategorie
        WHERE miesiac = ?
        ORDER BY przychod DESC
    """, (miesiac,)).fetchall() if conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trendy_kategorie'"
    ).fetchone() else []

    kategorie = [{'kategoria': r[0], 'miesiac': r[1], 'sprzedaz_szt': r[2],
                  'przychod': r[3], 'trend_mm': r[4]} for r in kat_rows]

    return jsonify({
        'miesiac':       miesiac,
        'timestamp':     datetime.now().isoformat(),
        'okazje':        okazje,
        'wszystkie':     wszystkie,
        'kategorie':     kategorie,
        'total_okazji':  len(okazje),
        'total_products': len(wszystkie),
    })


@app.route('/api/trendy/summary')
@require_api_key
def api_trendy_summary():
    """GET /api/trendy/summary - krotkie podsumowanie dla AI/chatbotow"""
    from modules.database import get_db
    conn = get_db()
    miesiac = datetime.now().strftime('%Y-%m')

    has_trendy = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trendy'"
    ).fetchone()

    if not has_trendy:
        return jsonify({'summary': 'Brak danych - uruchom analyze_trends.py', 'okazje': []})

    top5 = conn.execute("""
        SELECT p.nazwa, t.sprzedaz_szt, t.roi, t.trend_mm, t.okazja_score
        FROM trendy t LEFT JOIN produkty p ON t.produkt_id = p.id
        WHERE t.miesiac = ? AND t.okazja_score >= 7
        ORDER BY t.okazja_score DESC LIMIT 5
    """, (miesiac,)).fetchall()

    miesiac_stats = conn.execute("""
        SELECT COUNT(*) as cnt, COALESCE(SUM(przychod), 0) as suma, COALESCE(AVG(roi), 0) as avg_roi
        FROM trendy WHERE miesiac = ?
    """, (miesiac,)).fetchone()

    return jsonify({
        'miesiac': miesiac,
        'produkty_z_danymi': miesiac_stats[0] if miesiac_stats else 0,
        'przychod_total':    round(miesiac_stats[1], 2) if miesiac_stats else 0,
        'roi_srednie':       round(miesiac_stats[2], 1) if miesiac_stats else 0,
        'top5_okazji':       [{'nazwa': r[0], 'szt': r[1], 'roi': r[2], 'trend': r[3], 'score': r[4]} for r in top5],
        'summary':           f"Miesiac {miesiac}: {miesiac_stats[0] if miesiac_stats else 0} produktow, top okazji: {len(top5)}",
    })


@app.route('/api/sprzedaz/live')
@require_api_key
def api_sprzedaz_live():
    """GET /api/sprzedaz/live - sprzedaz z ostatnich X godzin"""
    from modules.database import get_db
    conn = get_db()
    godziny = min(int(request.args.get('godziny', 24)), 168)

    rows = conn.execute("""
        SELECT s.id, s.allegro_order_id, s.cena, s.ilosc, s.status, s.data_sprzedazy,
               p.nazwa, p.kategoria
        FROM sprzedaze s LEFT JOIN produkty p ON s.produkt_id = p.id
        WHERE s.data_sprzedazy >= datetime('now', ? || ' hours')
        ORDER BY s.data_sprzedazy DESC
        LIMIT 100
    """, (f'-{godziny}',)).fetchall()

    sprzedaz = []
    przychod = 0
    for r in rows:
        if r[4] not in ('zwrot','anulowane','anulowana'):
            przychod += (r[2] or 0) * (r[3] or 1)
        sprzedaz.append({
            'id': r[0], 'order_id': r[1], 'cena': r[2], 'ilosc': r[3],
            'status': r[4], 'data': r[5], 'produkt': r[6], 'kategoria': r[7]
        })

    return jsonify({
        'zakres_godzin':   godziny,
        'liczba_zamowien': len(sprzedaz),
        'przychod':        round(przychod, 2),
        'zamowienia':      sprzedaz,
        'timestamp':       datetime.now().isoformat(),
    })


@app.route('/api/magazyn/stan')
@require_api_key
def api_magazyn_stan():
    """GET /api/magazyn/stan - aktualny stan magazynu"""
    from modules.database import get_db
    conn = get_db()

    rows = conn.execute("""
        SELECT p.kategoria, COUNT(*) as szt, COALESCE(SUM(p.cena_brutto), 0) as wartosc
        FROM produkty p
        WHERE p.status IN ('magazyn','nowy','gotowy')
        GROUP BY p.kategoria
        ORDER BY wartosc DESC
    """).fetchall()

    total = conn.execute("""
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena_brutto), 0) as val
        FROM produkty WHERE status IN ('magazyn','nowy','gotowy')
    """).fetchone()

    return jsonify({
        'total_produktow': total[0],
        'wartosc_zakupu':  round(total[1], 2),
        'per_kategoria':   [{'kategoria': r[0] or 'inne', 'sztuk': r[1], 'wartosc': round(r[2], 2)} for r in rows],
        'timestamp':       datetime.now().isoformat(),
    })


@app.route('/api/docs')
def api_docs():
    """GET /api/docs - dokumentacja API (bez klucza)"""
    return jsonify({
        'name': get_config_cached('brand_name', 'AKCES HUB') + ' API',
        'version': 'v1.0',
        'auth': 'Naglowek X-API-Key lub parametr ?api_key=YOUR_KEY',
        'key_endpoint': 'GET /api/key (tylko localhost)',
        'endpoints': {
            'GET /api/health':           'Status serwera (bez klucza)',
            'GET /api/docs':             'Ta dokumentacja (bez klucza)',
            'GET /api/trendy':           'TOP okazje z analizy trendow | ?miesiac=2026-03&limit=20',
            'GET /api/trendy/summary':   'Krotkie podsumowanie dla AI/chatbotow',
            'GET /api/sprzedaz/live':    'Sprzedaz z ostatnich N godzin | ?godziny=24',
            'GET /api/magazyn/stan':     'Stan magazynu per kategoria',
            'GET /api/widget':           'Widget statystyki (bez klucza)',
            'GET /api/stats':            'Statystyki systemu (bez klucza)',
            'GET /api/stats/monthly':    'Dane miesiczne do wykresow (bez klucza)',
        },
        'example_curl': 'curl -H X-API-Key:YOUR_KEY http://localhost:5000/api/trendy',
        'perplexity_hint': 'W Perplexity: dodaj akcje HTTP z URL i naglowkiem X-API-Key',
    })

@app.route('/api/bingo2026')
def api_bingo2026():
    """Oblicza które cele Bingo 2026 zostały osiągnięte"""
    from modules.database import get_db
    conn = get_db()
    rok = '2026'
    
    przychod_2026 = float(conn.execute("""
        SELECT COALESCE(SUM(cena * ilosc), 0) as suma FROM sprzedaze
        WHERE strftime('%Y', REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) = ?
          AND status NOT IN ('zwrot','anulowane','anulowana')
          AND (kupujacy IS NULL OR kupujacy != 'offline')
    """, (rok,)).fetchone()['suma'])
    
    cnt_2026 = int(conn.execute("""
        SELECT COUNT(*) as cnt FROM sprzedaze
        WHERE strftime('%Y', REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) = ?
          AND status NOT IN ('zwrot','anulowane','anulowana')
          AND (kupujacy IS NULL OR kupujacy != 'offline')
    """, (rok,)).fetchone()['cnt'])
    
    best_day = conn.execute("""
        SELECT MAX(dzien_suma) as max_suma, MAX(dzien_cnt) as max_cnt FROM (
            SELECT date(REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) as dzien,
                   SUM(cena * ilosc) as dzien_suma, COUNT(*) as dzien_cnt
            FROM sprzedaze
            WHERE strftime('%Y', REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) = ?
              AND status NOT IN ('zwrot','anulowane','anulowana')
              AND (kupujacy IS NULL OR kupujacy != 'offline')
            GROUP BY dzien
        )
    """, (rok,)).fetchone()
    best_day_kwota = float(best_day['max_suma'] or 0)
    best_day_cnt = int(best_day['max_cnt'] or 0)
    
    best_month = conn.execute("""
        SELECT MAX(mies_suma) as max_suma FROM (
            SELECT strftime('%Y-%m', REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) as mies,
                   SUM(cena * ilosc) as mies_suma
            FROM sprzedaze
            WHERE strftime('%Y', REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) = ?
              AND status NOT IN ('zwrot','anulowane','anulowana')
              AND (kupujacy IS NULL OR kupujacy != 'offline')
            GROUP BY mies
        )
    """, (rok,)).fetchone()
    best_month_kwota = float(best_month['max_suma'] or 0)
    
    max_palet = conn.execute("""
        SELECT MAX(cnt) as max_cnt FROM (
            SELECT strftime('%Y-%m', data_zakupu) as mies, COUNT(*) as cnt
            FROM palety WHERE strftime('%Y', data_zakupu) = ? GROUP BY mies
        )
    """, (rok,)).fetchone()
    max_palet_miesiac = int(max_palet['max_cnt'] or 0)
    
    fast_sale_24h = int(conn.execute("""
        SELECT COUNT(*) as cnt FROM sprzedaze s
        JOIN oferty o ON s.oferta_id = o.id
        WHERE strftime('%Y', REPLACE(SUBSTR(s.data_sprzedazy,1,19),'T',' ')) = ?
          AND s.status NOT IN ('zwrot','anulowane','anulowana')
          AND o.data_wystawienia IS NOT NULL
          AND (julianday(REPLACE(SUBSTR(s.data_sprzedazy,1,19),'T',' '))
               - julianday(REPLACE(SUBSTR(o.data_wystawienia,1,19),'T',' '))) <= 1
    """, (rok,)).fetchone()['cnt'])
    
    fast_sale_6h = int(conn.execute("""
        SELECT COUNT(*) as cnt FROM sprzedaze s
        JOIN oferty o ON s.oferta_id = o.id
        WHERE strftime('%Y', REPLACE(SUBSTR(s.data_sprzedazy,1,19),'T',' ')) = ?
          AND s.status NOT IN ('zwrot','anulowane','anulowana')
          AND o.data_wystawienia IS NOT NULL
          AND (julianday(REPLACE(SUBSTR(s.data_sprzedazy,1,19),'T',' '))
               - julianday(REPLACE(SUBSTR(o.data_wystawienia,1,19),'T',' '))) * 24 <= 6
    """, (rok,)).fetchone()['cnt'])
    
    
    cells = [
        {'id': 1,  'icon': '💰', 'name': '100k PLN',   'desc': 'Przychód roczny',     'achieved': przychod_2026 >= 100000},
        {'id': 2,  'icon': '🔥', 'name': 'Dzień 3k',   'desc': '3 000 zł w 1 dzień',  'achieved': best_day_kwota >= 3000},
        {'id': 3,  'icon': '📦', 'name': '15 palet',   'desc': 'Palety w 1 miesiącu',  'achieved': max_palet_miesiac >= 15},
        {'id': 4,  'icon': '🚀', 'name': '200k PLN',   'desc': 'Przychód roczny',      'achieved': przychod_2026 >= 200000},
        {'id': 5,  'icon': '⚡', 'name': '10 zamówień','desc': '10 zam. w 1 dzień',    'achieved': best_day_cnt >= 10},
        {'id': 6,  'icon': '⏱', 'name': 'Sprzed. 24h','desc': 'Sprzedane w 24h',      'achieved': fast_sale_24h > 0},
        {'id': 7,  'icon': '📈', 'name': '40k/mies.',  'desc': '40k zł w miesiącu',    'achieved': best_month_kwota >= 40000},
        {'id': 8,  'icon': '🎯', 'name': '200 szt.',   'desc': '200 sprzedaży w roku', 'achieved': cnt_2026 >= 200},
        {'id': 9,  'icon': '💥', 'name': 'Dzień 5k',   'desc': '5 000 zł w 1 dzień',  'achieved': best_day_kwota >= 5000},
        {'id': 10, 'icon': '📦', 'name': '20 palet',   'desc': 'Palety w 1 miesiącu',  'achieved': max_palet_miesiac >= 20},
        {'id': 11, 'icon': '💎', 'name': '300k PLN',   'desc': 'Przychód roczny',      'achieved': przychod_2026 >= 300000},
        {'id': 12, 'icon': '⚡', 'name': 'Sprzed. 6h', 'desc': 'Sprzedane w 6h',       'achieved': fast_sale_6h > 0},
        {'id': 13, 'icon': '🆓', 'name': 'FREE',       'desc': 'Masz to!',              'achieved': True},
        {'id': 14, 'icon': '🏆', 'name': 'Dzień 20',   'desc': '20 zamówień w dzień',  'achieved': best_day_cnt >= 20},
        {'id': 15, 'icon': '📊', 'name': '60k/mies.',  'desc': '60k zł w miesiącu',    'achieved': best_month_kwota >= 60000},
        {'id': 16, 'icon': '🎲', 'name': '500 szt.',   'desc': '500 sprzedaży w roku', 'achieved': cnt_2026 >= 500},
        {'id': 17, 'icon': '💰', 'name': '25 palet',   'desc': 'Palety w 1 miesiącu',  'achieved': max_palet_miesiac >= 25},
        {'id': 18, 'icon': '🌟', 'name': '400k PLN',   'desc': '⭐ CEL ROCZNY!',        'achieved': przychod_2026 >= 400000},
        {'id': 19, 'icon': '🔥', 'name': '80k/mies.',  'desc': '80k zł w miesiącu',    'achieved': best_month_kwota >= 80000},
        {'id': 20, 'icon': '🎯', 'name': 'Dzień 10k',  'desc': '10k zł w 1 dzień',     'achieved': best_day_kwota >= 10000},
        {'id': 21, 'icon': '🚀', 'name': '1000 szt.',  'desc': '1000 sprzedaży w roku','achieved': cnt_2026 >= 1000},
        {'id': 22, 'icon': '💎', 'name': '500k PLN',   'desc': 'Przychód roczny',      'achieved': przychod_2026 >= 500000},
        {'id': 23, 'icon': '📦', 'name': '30 palet',   'desc': 'Palety w 1 miesiącu',  'achieved': max_palet_miesiac >= 30},
        {'id': 24, 'icon': '💥', 'name': '100k/mies.', 'desc': '100k zł w miesiącu',   'achieved': best_month_kwota >= 100000},
        {'id': 25, 'icon': '👑', 'name': 'LEGENDA',    'desc': 'Wszystko ukończone!',   'achieved': all([
            przychod_2026 >= 400000, max_palet_miesiac >= 25, best_day_kwota >= 5000])},
    ]
    
    achieved_count = sum(1 for c in cells if c['achieved'])
    grid = [c['achieved'] for c in cells]
    bingo_lines = []
    for r in range(5):
        if all(grid[r*5+c] for c in range(5)): bingo_lines.append(f'Rząd {r+1}')
    for c in range(5):
        if all(grid[r*5+c] for r in range(5)): bingo_lines.append(f'Kolumna {c+1}')
    if all(grid[i*5+i] for i in range(5)): bingo_lines.append('Przekątna ↘')
    if all(grid[i*5+(4-i)] for i in range(5)): bingo_lines.append('Przekątna ↗')
    
    return jsonify({'cells': cells, 'achieved_count': achieved_count, 'total': 25,
                    'bingo_lines': bingo_lines, 'przychod_2026': przychod_2026,
                    'best_day_kwota': best_day_kwota, 'best_month_kwota': best_month_kwota,
                    'max_palet_miesiac': max_palet_miesiac})


# ═══════════════════════════════════════════════════════════════════════════
# ANALITYKA: CZAS SPRZEDAŻY
# ═══════════════════════════════════════════════════════════════════════════



@app.route('/analityka/okazje')
def analityka_okazje():
    """Strona TOP Okazje - produkty z najwyzszym scoring + analiza Perplexity"""
    from modules.database import get_db, get_config
    import json as _json
    conn = get_db()
    miesiac = datetime.now().strftime('%Y-%m')

    has_trendy = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trendy'"
    ).fetchone()

    okazje_list = []
    wszystkie_list = []
    ostatnia_analiza = 'brak danych'
    perplexity_odpowiedz = None
    perplexity_citations = []
    perplexity_data = None

    if has_trendy:
        # Migracja inline - dodaj kolumny do trendy jeśli brak (stare bazy)
        for _col, _typ in [('nazwa','TEXT'),('kategoria','TEXT'),('dostawca','TEXT'),('koszt','REAL DEFAULT 0')]:
            try:
                conn.execute(f'ALTER TABLE trendy ADD COLUMN {_col} {_typ}')
                conn.commit()
            except:
                pass

        okazje_rows = conn.execute("""
            SELECT t.produkt_id,
                   COALESCE(p.nazwa, t.nazwa, 'Brak nazwy') as nazwa,
                   COALESCE(p.kategoria, t.kategoria, 'inne') as kategoria,
                   COALESCE(p.dostawca, t.dostawca, '') as dostawca,
                   t.sprzedaz_szt, t.przychod, COALESCE(t.koszt,0) as koszt,
                   t.roi, t.trend_mm, t.okazja_score, t.created_at
            FROM trendy t LEFT JOIN produkty p ON t.produkt_id = p.id
            WHERE t.miesiac = ? AND t.okazja_score >= 6
            ORDER BY t.okazja_score DESC, t.przychod DESC
        """, (miesiac,)).fetchall()

        wszystkie_rows = conn.execute("""
            SELECT t.produkt_id,
                   COALESCE(p.nazwa, t.nazwa, 'Brak nazwy') as nazwa,
                   COALESCE(p.kategoria, t.kategoria, 'inne') as kategoria,
                   t.sprzedaz_szt, t.przychod, t.roi, t.trend_mm, t.okazja_score
            FROM trendy t LEFT JOIN produkty p ON t.produkt_id = p.id
            WHERE t.miesiac = ?
            ORDER BY t.okazja_score DESC LIMIT 50
        """, (miesiac,)).fetchall()

        last = conn.execute("SELECT MAX(created_at) as ts FROM trendy").fetchone()
        ostatnia_analiza = last['ts'] if last and last['ts'] else 'brak danych'

        # Cache odpowiedzi Perplexity
        szukaj_odpowiedz = None
        szukaj_citations = []
        szukaj_data = None
        try:
            has_cache = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='perplexity_cache'"
            ).fetchone()
            if has_cache:
                cache = conn.execute(
                    "SELECT odpowiedz, citations, created_at FROM perplexity_cache WHERE klucz = ? ORDER BY created_at DESC LIMIT 1",
                    (f'okazje_{miesiac}',)
                ).fetchone()
                if cache:
                    perplexity_odpowiedz = cache['odpowiedz']
                    perplexity_data = cache['created_at']
                    try:
                        perplexity_citations = _json.loads(cache['citations'] or '[]')
                    except:
                        perplexity_citations = []
                cache2 = conn.execute(
                    "SELECT odpowiedz, citations, created_at FROM perplexity_cache WHERE klucz = ? ORDER BY created_at DESC LIMIT 1",
                    (f'szukaj_{miesiac}',)
                ).fetchone()
                if cache2:
                    szukaj_odpowiedz = cache2['odpowiedz']
                    szukaj_data = cache2['created_at']
                    try:
                        szukaj_citations = _json.loads(cache2['citations'] or '[]')
                    except:
                        szukaj_citations = []
        except:
            szukaj_odpowiedz = None

        okazje_list = [dict(r) for r in okazje_rows]
        wszystkie_list = [dict(r) for r in wszystkie_rows]


    # Sprawdź czy jest klucz Perplexity
    perplexity_key = get_config('perplexity_api_key', '')
    has_perplexity = bool(perplexity_key)
    perplexity_model = get_config('perplexity_model', 'sonar-pro')
    # Upgrade: sonar bez suffixu to słaby model - zamień na sonar-pro
    if perplexity_model == 'sonar':
        perplexity_model = 'sonar-pro'

    def trend_cls(v):
        return '#22c55e' if (v or 0) > 0 else '#ef4444'
    def roi_cls(v):
        return '#22c55e' if (v or 0) > 0 else '#ef4444'
    def sign(v):
        return '+' if (v or 0) > 0 else ''

    okazje_html = ''
    for r in okazje_list:
        score = r.get('okazja_score', 0)
        badge_bg = '#22c55e' if score >= 9 else '#f59e0b' if score >= 7 else '#3b82f6'
        okazje_html += f"""
        <div style='background:#12121a;border:1px solid #2a2a4a;border-radius:12px;padding:15px;margin-bottom:10px;transition:border-color 0.2s' onmouseover="this.style.borderColor='#f59e0b'" onmouseout="this.style.borderColor='#2a2a4a'">
          <div style='display:flex;align-items:center;gap:12px;margin-bottom:10px'>
            <div style='background:{badge_bg};color:#000;font-weight:800;font-size:0.85rem;border-radius:8px;padding:4px 10px;min-width:52px;text-align:center'>★{score}/10</div>
            <div style='flex:1'>
              <div style='font-weight:600'>{r.get('nazwa') or 'Produkt #' + str(r.get('produkt_id','?'))}</div>
              <div style='color:#64748b;font-size:0.78rem'>{r.get('kategoria') or 'inne'} · {r.get('dostawca') or ''}</div>
            </div>
          </div>
          <div style='display:flex;gap:20px;flex-wrap:wrap;font-size:0.85rem'>
            <div><div style='color:#64748b;font-size:0.72rem'>SPRZEDANO</div>{r.get('sprzedaz_szt',0)} szt</div>
            <div><div style='color:#64748b;font-size:0.72rem'>PRZYCHÓD</div>{(r.get('przychod') or 0):.0f} zł</div>
            <div><div style='color:#64748b;font-size:0.72rem'>ROI</div><span style='color:{roi_cls(r.get("roi"))};font-weight:600'>{(r.get("roi") or 0):.0f}%</span></div>
            <div><div style='color:#64748b;font-size:0.72rem'>TREND M/M</div><span style='color:{trend_cls(r.get("trend_mm"))};font-weight:600'>{sign(r.get("trend_mm"))}{(r.get("trend_mm") or 0):.0f}%</span></div>
          </div>
        </div>"""

    if not okazje_html:
        okazje_html = "<div style='background:#12121a;border:1px solid #2a2a4a;border-radius:12px;padding:30px;text-align:center;color:#64748b'>Brak okazji (score ≥ 6) w tym miesiącu.<br><br>Uruchom: <code style='background:#1e1e2e;padding:4px 8px;border-radius:6px'>python analyze_trends.py</code></div>"

    wszystkie_html = ''
    for r in wszystkie_list:
        score = r.get('okazja_score', 0)
        badge_bg = '#22c55e' if score >= 9 else '#f59e0b' if score >= 7 else '#3b82f6' if score >= 5 else '#475569'
        wszystkie_html += f"""
        <div style='background:#12121a;border:1px solid #1e1e2e;border-radius:10px;padding:12px;margin-bottom:8px;display:flex;align-items:center;gap:12px'>
          <div style='background:{badge_bg};color:#000;font-weight:700;font-size:0.8rem;border-radius:6px;padding:3px 8px;min-width:40px;text-align:center'>{score}/10</div>
          <div style='flex:1;font-size:0.85rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{r.get('nazwa') or 'Produkt'}</div>
          <div style='display:flex;gap:15px;font-size:0.8rem;white-space:nowrap'>
            <span>{r.get('sprzedaz_szt',0)} szt</span>
            <span style='color:{roi_cls(r.get("roi"))}'>{(r.get("roi") or 0):.0f}% ROI</span>
            <span style='color:{trend_cls(r.get("trend_mm"))}'>{sign(r.get("trend_mm"))}{(r.get("trend_mm") or 0):.0f}%</span>
          </div>
        </div>"""

    if not wszystkie_html:
        wszystkie_html = "<div style='color:#64748b;padding:20px;text-align:center'>Brak danych. Uruchom: <code>python analyze_trends.py</code></div>"

    # === SEKCJA LIVE SCRAPER (Warrington + Jobalots + Szukaj palet) ===
    live_scraper_section = """
        <div style='background:#12121a;border:1px solid #0ea5e940;border-radius:16px;padding:20px;margin-bottom:20px'>
          <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:14px'>
            <div style='color:#0ea5e9;font-weight:700;font-size:1rem'>🔴 Aktualne palety — na żywo</div>
            <div style='color:#64748b;font-size:0.75rem'>dane pobierane bezpośrednio ze stron dostawców</div>
          </div>
          <div style='display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px'>
            <div style='background:#0a1520;border:1px solid #0ea5e930;border-radius:10px;padding:14px'>
              <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:10px'>
                <a href='https://warrington.store/products/new' target='_blank' style='color:#0ea5e9;font-weight:600;font-size:0.9rem;text-decoration:none'>🏪 Warrington.store ↗</a>
                <button onclick='loadWarrington()' id='btn-warrington' style='background:#0ea5e920;color:#0ea5e9;border:1px solid #0ea5e940;border-radius:6px;padding:4px 12px;font-size:0.75rem;cursor:pointer'>▶ Załaduj</button>
              </div>
              <div id='warrington-results' style='color:#64748b;font-size:0.8rem'>Kliknij "Załaduj" aby pobrać aktualne palety</div>
            </div>
            <div style='background:#0a100a;border:1px solid #f59e0b30;border-radius:10px;padding:14px'>
              <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:10px'>
                <a href='https://jobalots.com/pl/pages/products-on-auction?page=1&currency=pln&type=pallets' target='_blank' style='color:#f59e0b;font-weight:600;font-size:0.9rem;text-decoration:none'>🏪 Jobalots.com ↗</a>
                <button onclick='loadJobalots()' id='btn-jobalots' style='background:#f59e0b20;color:#f59e0b;border:1px solid #f59e0b40;border-radius:6px;padding:4px 12px;font-size:0.75rem;cursor:pointer'>▶ Załaduj</button>
              </div>
              <div id='jobalots-results' style='color:#64748b;font-size:0.8rem'>Kliknij "Załaduj" aby pobrać aukcje palet</div>
            </div>
          </div>
          <div id='szukaj-panel' style='background:#0f1a12;border:1px solid #22c55e30;border-radius:10px;padding:14px'>
            <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:10px'>
              <div style='color:#22c55e;font-weight:600;font-size:0.9rem'>🛒 Szukaj palet pod mój profil (AI)</div>
              <div style='color:#64748b;font-size:0.73rem'>Perplexity analizuje Twój profil i szuka najlepszych ofert</div>
            </div>
            %%SZUKAJ_PLACEHOLDER%%
          </div>
        </div>
        <script>
        function loadWarrington() {
          var btn = document.getElementById('btn-warrington');
          var res = document.getElementById('warrington-results');
          btn.disabled = true; btn.textContent = '⏳ Ładowanie...';
          fetch('/analityka/okazje/scrape-warrington')
            .then(r => r.json())
            .then(d => {
              btn.disabled = false; btn.textContent = '🔄 Odśwież';
              if (!d.ok) { res.innerHTML = '<span style="color:#ef4444">Błąd: ' + d.error + '</span>'; return; }
              if (!d.products.length) { res.innerHTML = '<span style="color:#64748b">Brak produktów</span>'; return; }
              var html = '<div style="max-height:280px;overflow-y:auto">';
              d.products.forEach(function(p) {
                var priceStr = p.price_text || (p.price ? '£' + p.price.toFixed(0) : '');
                html += '<a href="' + p.url + '" target="_blank" style="display:block;padding:6px 8px;margin:3px 0;background:#0ea5e910;border:1px solid #0ea5e920;border-radius:6px;color:#e2e8f0;text-decoration:none;font-size:0.8rem">';
                html += p.title;
                if (priceStr && priceStr !== '?' && priceStr !== 'kategoria') html += ' <span style="color:#0ea5e9;font-weight:600">' + priceStr + '</span>';
                html += ' ↗</a>';
              });
              html += '</div><div style="color:#64748b;font-size:0.72rem;margin-top:6px">Źródło: ' + (d.source||'') + ' | Łącznie: ' + d.total + '</div>';
              res.innerHTML = html;
            })
            .catch(e => { btn.disabled=false; btn.textContent='▶ Załaduj'; res.innerHTML='<span style="color:#ef4444">Błąd połączenia</span>'; });
        }
        function loadJobalots() {
          var btn = document.getElementById('btn-jobalots');
          var res = document.getElementById('jobalots-results');
          btn.disabled = true; btn.textContent = '⏳ Ładowanie...';
          fetch('/analityka/okazje/scrape-jobalots')
            .then(r => r.json())
            .then(d => {
              btn.disabled = false; btn.textContent = '🔄 Odśwież';
              if (!d.ok) { res.innerHTML = '<span style="color:#ef4444">Błąd: ' + (d.error||'') + '</span>' + (d.fallback_url ? '<br><a href="'+d.fallback_url+'" target="_blank" style="color:#f59e0b">→ Otwórz Jobalots ↗</a>' : ''); return; }
              if (d.fallback_url && !d.products.length) {
                res.innerHTML = '<div style="color:#f59e0b;font-size:0.8rem">' + (d.note||'') + '</div><a href="' + d.fallback_url + '" target="_blank" style="color:#f59e0b;font-size:0.8rem">→ Otwórz Jobalots ↗</a>';
                return;
              }
              if (!d.products.length) { res.innerHTML = '<span style="color:#64748b">Brak produktów</span>'; return; }
              var html = '';
              if (d.note) html += '<div style="color:#f59e0b;font-size:0.73rem;margin-bottom:6px">' + d.note + '</div>';
              html += '<div style="max-height:280px;overflow-y:auto">';
              d.products.forEach(function(p) {
                html += '<a href="' + p.url + '" target="_blank" style="display:block;padding:6px 8px;margin:3px 0;background:#f59e0b10;border:1px solid #f59e0b20;border-radius:6px;color:#e2e8f0;text-decoration:none;font-size:0.78rem">';
                html += '<div style="font-weight:600;margin-bottom:2px">';
                if (p.tag) html += p.tag + ' ';
                html += p.title;
                if (p.discount > 30) html += ' <span style="background:#ef4444;color:#fff;padding:1px 5px;border-radius:4px;font-size:0.65rem;font-weight:700">-' + p.discount + '%</span>';
                html += '</div>';
                html += '<div style="display:flex;gap:8px;flex-wrap:wrap;font-size:0.72rem;color:#94a3b8">';
                if (p.price_text) html += '<span style="color:#f59e0b;font-weight:700">' + p.price_text + '</span>';
                if (p.rrp) html += '<span style="text-decoration:line-through;color:#64748b">' + Math.round(p.rrp) + ' RRP</span>';
                if (p.qty) html += '<span>' + p.qty + ' szt</span>';
                if (p.bid_count) html += '<span>' + p.bid_count + ' ofert</span>';
                if (p.end_at) html += '<span>⏰ ' + p.end_at + '</span>';
                html += '</div></a>';
              });
              html += '</div><div style="color:#64748b;font-size:0.72rem;margin-top:6px">Łącznie: ' + d.total + ' palet</div>';
              res.innerHTML = html;
            })
            .catch(e => { btn.disabled=false; btn.textContent='▶ Załaduj'; res.innerHTML='<span style="color:#ef4444">Błąd połączenia</span>'; });
        }
        </script>"""

    # === SEKCJA PERPLEXITY ===
    if not has_trendy:
        perplexity_section = ""
        live_scraper_section = live_scraper_section.replace('%%SZUKAJ_PLACEHOLDER%%',
            "<div style='color:#64748b;font-size:0.73rem'>Dodaj klucz Perplexity API poniżej</div>")
    elif not has_perplexity:
        live_scraper_section = live_scraper_section.replace('%%SZUKAJ_PLACEHOLDER%%',
            "<div style='color:#64748b;font-size:0.73rem'>Dodaj klucz Perplexity API poniżej aby aktywować</div>")
        perplexity_section = f"""
        <div style='background:#12121a;border:1px solid #3b82f640;border-radius:16px;padding:20px;margin-bottom:20px'>
          <div style='color:#3b82f6;font-weight:700;font-size:1rem;margin-bottom:12px'>🤖 Analiza rynkowa (Perplexity AI)</div>
          <div style='color:#64748b;font-size:0.85rem;margin-bottom:12px'>Dodaj klucz API Perplexity żeby otrzymać analizę rynkową produktów na podstawie Twoich trendów sprzedaży.</div>
          <form method='POST' action='/analityka/okazje/set-perplexity-key' style='display:flex;gap:8px;flex-wrap:wrap'>
            <input type='password' name='api_key' placeholder='pplx-xxxxxxxxxxxxxxxx' style='flex:1;min-width:220px;background:#0a0a0f;border:1px solid #2a2a4a;border-radius:8px;padding:8px 12px;color:#e2e8f0;font-size:0.85rem'>
            <button type='submit' style='background:#3b82f6;color:#fff;border:none;border-radius:8px;padding:8px 16px;font-weight:600;cursor:pointer'>Zapisz klucz</button>
          </form>
          <div style='color:#475569;font-size:0.75rem;margin-top:8px'>Klucz Perplexity → <a href='https://www.perplexity.ai/settings/api' target='_blank' style='color:#7dd3fc'>perplexity.ai/settings/api</a></div>
        </div>"""
    else:
        # Jest klucz — pokaż przycisk analizy i ewentualny cache
        cached_html = ""
        if perplexity_odpowiedz:
            # Użyj tego samego formatera co prawy panel (definiowany niżej)
            _left_odpowiedz = perplexity_odpowiedz
            _left_citations = perplexity_citations
            _left_data = perplexity_data
        else:
            _left_odpowiedz = None
            _left_citations = []
            _left_data = None

        # Szukaj cache HTML
        import html as _html_mod2
        def _cache_block(odp, cits, ts, refresh_url, title, icon):
            if not odp:
                return "<div style='color:#64748b;font-size:0.85rem;margin-top:10px'>Brak analizy — kliknij przycisk powyżej.</div>"
            import re as _re, html as _h
            safe = _h.escape(odp)
            # Usuń referencje [1][2] itp.
            safe = _re.sub(r'\[(\d+)\]', '', safe)
            # Nagłówki → kolorowe karty z separacją
            safe = _re.sub(r'(?m)^###\s+(.+)$', r'</div><div style="background:#1a1a2e;border:1px solid #2a2a4a;border-radius:10px;padding:12px;margin:14px 0 8px"><div style="color:#f59e0b;font-weight:700;font-size:0.95rem;margin-bottom:6px">\1</div><div>', safe)
            safe = _re.sub(r'(?m)^##\s+(.+)$', r'</div><div style="background:#0f1520;border-left:3px solid #3b82f6;padding:10px 12px;margin:12px 0 6px;border-radius:0 8px 8px 0"><div style="color:#7dd3fc;font-weight:700;font-size:0.9rem">\1</div></div><div>', safe)
            # Numerowane palety (1. 2. 3.) → wyróżnione karty
            safe = _re.sub(r'(?m)^(\d+)\.\s+(.+)$', r'<div style="background:#12121a;border:1px solid #2a2a4a;border-radius:8px;padding:10px 12px;margin:8px 0;position:relative;padding-left:40px"><span style="position:absolute;left:10px;top:10px;background:#f59e0b;color:#000;font-weight:800;border-radius:50%;width:22px;height:22px;display:flex;align-items:center;justify-content:center;font-size:0.75rem">\1</span>\2</div>', safe)
            # Linie z "Link:" → duży przycisk z linkiem (PRZED bold i bullet!)
            def _make_link_btn(m):
                url = m.group(1)
                rest = m.group(2) or ''
                rest = _re.sub(r'\*\*', '', rest).strip()
                # Wyciągnij nazwę produktu z URL
                path = url.split('/')[-1].split('?')[0]
                label = path.replace('-', ' ').replace('_', ' ').title()[:40]
                if not label or len(label) < 3:
                    domain = url.split('/')[2] if len(url.split('/')) > 2 else url
                    label = domain.replace('www.', '')
                return f'<div style="margin:6px 0"><a href="{url}" target="_blank" style="display:inline-block;background:#3b82f6;color:#fff;padding:8px 18px;border-radius:8px;text-decoration:none;font-weight:700;font-size:0.85rem">🔗 {label} ↗</a> <span style="color:#64748b;font-size:0.73rem">{rest}</span></div>'
            safe = _re.sub(r'(?m)^-\s+\*{0,2}[Ll]ink:?\*{0,2}\s*(https?://[^\s<>&]+)(.*?)$', _make_link_btn, safe)
            # Bold
            safe = _re.sub(r'\*\*(.+?)\*\*', r'<strong style="color:#f1f5f9">\1</strong>', safe)
            # Pozostałe linki URL → klikalne (ale nie te już w przyciskach)
            safe = _re.sub(r'(?<!href=")(https?://[^\s<>"&]+)(?!")', r'<a href="\1" target="_blank" style="color:#7dd3fc;text-decoration:underline;word-break:break-all;font-size:0.8rem">\1</a>', safe)
            # Bullet listy → czytelne elementy
            safe = _re.sub(r'(?m)^[\u2022\-]\s+(.+)$', r'<div style="padding:4px 0 4px 16px;border-left:2px solid #2a2a4a;margin:3px 0;font-size:0.82rem">\1</div>', safe)
            # Separator ---
            safe = _re.sub(r'(?m)^---+$', r'<hr style="border:none;border-top:1px solid #2a2a4a;margin:12px 0">', safe)
            safe = safe.replace('\n', '<br>')
            # Wyczyść puste divy
            safe = safe.replace('<div></div>', '').replace('<br><br><br>', '<br>')
            cit_items = ''
            if cits:
                for ci, cv in enumerate(cits[:8]):
                    short = cv[:80] + ('...' if len(cv) > 80 else '')
                    cit_items += f"<a href='{cv}' target='_blank' style='color:#7dd3fc;font-size:0.72rem;display:block;margin:2px 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>[{ci+1}] {short}</a>"
            cit_html2 = f"<div style='margin-top:10px;padding-top:10px;border-top:1px solid #1e1e2e'><div style='color:#64748b;font-size:0.72rem;margin-bottom:4px'>Źródła ({len(cits)}):</div>{cit_items}</div>" if cits else ''
            btn = f"<form method='POST' action='{refresh_url}' style='margin:0'><button type='submit' style='background:#1e1e2e;color:#94a3b8;border:1px solid #2a2a4a;border-radius:6px;padding:3px 10px;font-size:0.72rem;cursor:pointer'>🔄 Odśwież</button></form>"
            return f"<div style='background:#0a0f0a;border:1px solid #22c55e30;border-radius:10px;padding:14px;margin-top:12px'><div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:10px'><div style='color:#22c55e;font-size:0.78rem;font-weight:600'>✅ {ts}</div>{btn}</div><div style='color:#e2e8f0;font-size:0.83rem;line-height:1.75'>{safe}</div>{cit_html2}</div>"

        cached_html = _cache_block(_left_odpowiedz, _left_citations, _left_data,
            '/analityka/okazje/perplexity-analyze',
            'Analiza sprzedaży', '📊')

        szukaj_html_block = _cache_block(szukaj_odpowiedz, szukaj_citations, szukaj_data,
            '/analityka/okazje/perplexity-szukaj',
            'Okazje zakupowe', '🛒')

        # Wstaw przycisk "Szukaj" + wyniki do panelu w live_scraper_section
        _szukaj_panel_content = f"""<form method='POST' action='/analityka/okazje/perplexity-szukaj' onsubmit='showLoading(this,"szukaj")'>
                <button id='btn-szukaj' type='submit' style='width:100%;background:linear-gradient(135deg,#22c55e,#16a34a);color:#fff;border:none;border-radius:8px;padding:8px;font-weight:600;cursor:pointer;font-size:0.82rem'>
                  🔎 Szukaj teraz
                </button>
              </form>
              <div id='loading-szukaj' style='display:none;text-align:center;padding:10px;color:#22c55e;font-size:0.82rem'>
                <span style='animation:spin 1s linear infinite;display:inline-block'>⏳</span> Szukam palet... (~30-45 sek)
              </div>
              {szukaj_html_block}"""
        live_scraper_section = live_scraper_section.replace('%%SZUKAJ_PLACEHOLDER%%', _szukaj_panel_content)

        perplexity_section = f"""
        <div style='background:#12121a;border:1px solid #8b5cf640;border-radius:16px;padding:20px;margin-bottom:20px'>
          <div style='display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:10px'>
            <div style='color:#8b5cf6;font-weight:700;font-size:1rem'>🤖 Perplexity AI</div>
            <div style='display:flex;align-items:center;gap:8px'>
              <form method='POST' action='/analityka/okazje/set-perplexity-model' style='margin:0;display:flex;align-items:center;gap:6px'>
                <span style='color:#64748b;font-size:0.75rem'>Model:</span>
                <select name='model' onchange='this.form.submit()' style='background:#1e1e2e;color:#e2e8f0;border:1px solid #2a2a4a;border-radius:6px;padding:3px 8px;font-size:0.75rem;cursor:pointer'>
                  <option value='sonar-pro' {{'selected' if perplexity_model in ("sonar","sonar-pro") else ""}}>Sonar Pro ⭐ (zalecany)</option>

                  <option value='sonar-reasoning' {{'selected' if perplexity_model=="sonar-reasoning" else ""}}>Sonar Reasoning</option>
                  <option value='sonar-reasoning-pro' {{'selected' if perplexity_model=="sonar-reasoning-pro" else ""}}>Sonar Reasoning Pro</option>
                </select>
              </form>
              <form method='POST' action='/analityka/okazje/remove-perplexity-key' onsubmit="return confirm('Usunąć klucz Perplexity?')" style='margin:0'>
                <button type='submit' style='background:transparent;color:#475569;border:none;cursor:pointer;font-size:0.8rem'>🗑️ usuń klucz</button>
              </form>
            </div>
          </div>
          </div>

          <div style='background:#1a1025;border:1px solid #8b5cf630;border-radius:12px;padding:14px'>
              <div style='color:#8b5cf6;font-weight:600;font-size:0.85rem;margin-bottom:4px'>📊 Analiza moich sprzedaży</div>
              <div style='color:#64748b;font-size:0.75rem;margin-bottom:10px'>Ceny rynkowe produktów z palet/magazynu + co warto wystawiać</div>
              <form method='POST' action='/analityka/okazje/perplexity-analyze' onsubmit='showLoading(this,"analyze")'>
                <button id='btn-analyze' type='submit' style='width:100%;background:linear-gradient(135deg,#8b5cf6,#6d28d9);color:#fff;border:none;border-radius:8px;padding:8px;font-weight:600;cursor:pointer;font-size:0.82rem'>
                  🔍 Analizuj moje produkty
                </button>
              </form>
              <div id='loading-analyze' style='display:none;text-align:center;padding:10px;color:#8b5cf6;font-size:0.82rem'>
                <span style='animation:spin 1s linear infinite;display:inline-block'>⏳</span> Perplexity analizuje... (może potrwać ~30 sek)
              </div>
              {cached_html}
          </div>
        </div>"""

    no_data_banner = ""
    if not has_trendy:
        no_data_banner = "<div style='background:rgba(251,191,36,0.1);border:1px solid rgba(251,191,36,0.3);border-radius:10px;padding:12px;font-size:0.85rem;color:#f59e0b;margin-bottom:20px'>⚠️ Brak danych — uruchom <code style='background:#1e1e2e;padding:2px 6px;border-radius:4px'>python analyze_trends.py</code></div>"

    page = f"""<!DOCTYPE html>
<html lang='pl'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>TOP Okazje {miesiac} - {get_config_cached("brand_name", "AKCES HUB")}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0a0f;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:15px;max-width:900px;margin:0 auto;padding-bottom:60px}}
</style>
</head>
<body>
<a href='/analityka' style='color:#64748b;text-decoration:none;font-size:0.9rem;display:inline-block;margin-bottom:20px'>← Powrót</a>
<h1 style='font-size:1.6rem;margin-bottom:5px'>🔥 TOP Okazje</h1>
<p style='color:#64748b;font-size:0.85rem;margin-bottom:20px'>Miesiąc: <strong>{miesiac}</strong> · Ostatnia analiza: {ostatnia_analiza}</p>
{no_data_banner}

<div style='display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px'>
  <div style='background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center'>
    <div style='font-size:1.8rem;font-weight:700;color:#f59e0b'>{len(okazje_list)}</div>
    <div style='color:#64748b;font-size:0.75rem'>OKAZJI (score≥6)</div>
  </div>
  <div style='background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center'>
    <div style='font-size:1.8rem;font-weight:700;color:#3b82f6'>{len(wszystkie_list)}</div>
    <div style='color:#64748b;font-size:0.75rem'>PRODUKTÓW ZBADANYCH</div>
  </div>
  <div style='background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center'>
    <div style='font-size:1.8rem;font-weight:700;color:#22c55e'>{'✓' if has_perplexity else '✗'}</div>
    <div style='color:#64748b;font-size:0.75rem'>PERPLEXITY API</div>
  </div>
</div>

{live_scraper_section}
{perplexity_section}

<div style='margin-bottom:20px'>
  <h2 style='font-size:1.1rem;margin-bottom:12px;color:#f59e0b'>🏆 Najlepsze okazje (score ≥ 6)</h2>
  {okazje_html}
</div>

<div>
  <h2 style='font-size:1.1rem;margin-bottom:12px;color:#94a3b8'>📊 Wszystkie produkty (top 50)</h2>
  {wszystkie_html}
</div>
<style>@keyframes spin{{from{{transform:rotate(0deg)}}to{{transform:rotate(360deg)}}}}</style>
<script>
function showLoading(form, id) {{
  var btn = document.getElementById('btn-' + id);
  var loader = document.getElementById('loading-' + id);
  if(btn) {{ btn.disabled = true; btn.style.opacity = '0.5'; }}
  if(loader) loader.style.display = 'block';
}}
// Auto-polling jeśli task w toku
var loadingParam = new URLSearchParams(window.location.search).get('loading');
if(loadingParam) {{
  var klucz = loadingParam === 'analyze' ? 'okazje_{miesiac}' : 'szukaj_{miesiac}';
  var pollInterval = setInterval(function() {{
    fetch('/analityka/okazje/perplexity-status?klucz=' + klucz)
      .then(r => r.json())
      .then(d => {{
        if(d.status === 'done') {{
          clearInterval(pollInterval);
          window.location.href = '/analityka/okazje';
        }} else if(d.status === 'error') {{
          clearInterval(pollInterval);
          document.getElementById('loading-' + loadingParam).innerHTML = '❌ Błąd Perplexity — sprawdź klucz API i spróbuj ponownie';
        }}
      }});
  }}, 3000);
  // Pokaż loader od razu
  var loader = document.getElementById('loading-' + loadingParam);
  if(loader) loader.style.display = 'block';
  var btn = document.getElementById('btn-' + loadingParam);
  if(btn) {{ btn.disabled = true; btn.style.opacity = '0.5'; }}
}}
</script>
</body></html>"""

    return page


@app.route('/analityka/okazje/set-perplexity-key', methods=['POST'])
def okazje_set_perplexity_key():
    from modules.database import set_config
    api_key = request.form.get('api_key', '').strip()
    if api_key:
        set_config('perplexity_api_key', api_key)
    return redirect('/analityka/okazje')


@app.route('/analityka/okazje/remove-perplexity-key', methods=['POST'])
def okazje_remove_perplexity_key():
    from modules.database import set_config
    set_config('perplexity_api_key', '')
    return redirect('/analityka/okazje')


@app.route('/analityka/okazje/set-perplexity-model', methods=['POST'])
def okazje_set_perplexity_model():
    from modules.database import set_config
    model = request.form.get('model', 'sonar-pro').strip()
    set_config('perplexity_model', model)
    return redirect('/analityka/okazje')


# Słownik statusów zadań Perplexity
_perplexity_jobs = {}

def _run_perplexity(klucz, prompt, api_key, db_path, model="sonar-pro"):
    import requests as _req, json as _json, sqlite3 as _sq
    _perplexity_jobs[klucz] = 'running'
    try:
        resp = _req.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 2000, "return_citations": True},
            timeout=90)
        data = resp.json()
        odpowiedz = data['choices'][0]['message']['content']
        citations = data.get('citations', [])
        conn2 = _sq.connect(db_path)
        conn2.row_factory = _sq.Row
        conn2.execute("""CREATE TABLE IF NOT EXISTS perplexity_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT, klucz TEXT UNIQUE,
            odpowiedz TEXT, citations TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        from datetime import datetime as _dt
        conn2.execute(
            "INSERT OR REPLACE INTO perplexity_cache (klucz, odpowiedz, citations, created_at) VALUES (?, ?, ?, ?)",
            (klucz, odpowiedz, _json.dumps(citations), _dt.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn2.commit(); conn2.close()
        _perplexity_jobs[klucz] = 'done'
        print(f"[Perplexity] {klucz} gotowe")
    except Exception as e:
        _perplexity_jobs[klucz] = 'error'
        print(f"[Perplexity] blad {klucz}: {e}")



@app.route('/analityka/okazje/scrape-warrington')
def scrape_warrington():
    """Skrobie aktualne palety z Warrington - nowa strona (nie-Shopify)"""
    import requests as _req, re as _re, json as _jj
    _ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
    try:
        # Nowa struktura: /products/new ma najnowsze palety
        # HTML karty produktu zawiera:
        #   <h3 class="product-name"><a href="/product/{id}-{slug}">{nazwa}</a></h3>
        #   <ins class="new-price">{cena} zl</ins>
        #   <div class="product-cat"><a href="...">{kategoria}</a></div>
        products = []
        seen_ids = set()
        for page_url in [
            'https://warrington.store/products/new',
            'https://warrington.store/products/new/page/2',
            'https://warrington.store/products/new/page/3',
            'https://warrington.store/products/elektronika-i-gadzety',
            'https://warrington.store/products/akcesoria-tv',
            'https://warrington.store/products/dom',
            'https://warrington.store/products/kuchnia',
            'https://warrington.store/products/narzedzia',
            'https://warrington.store/products/ogrod',
            'https://warrington.store/products/sprzet-agd',
            'https://warrington.store/products/promotions',
        ]:
            try:
                resp = _req.get(page_url, headers={'User-Agent': _ua}, timeout=12)
                if resp.status_code != 200:
                    continue
                html = resp.text
                # Znajdź karty produktów - każda karta ma link, nazwę i cenę
                # Pattern: <h3 class="product-name"><a href="/product/{id}-{slug}">{name}</a></h3>
                # potem gdzieś: <ins class="new-price">{price} zl</ins>
                # Wyciągnij bloki kart produktowych
                cards = _re.findall(
                    r'<h3\s+class="product-name">\s*<a\s+href="(/product/(\d+)-([^"]+))"[^>]*>\s*(.*?)\s*</a>\s*</h3>.*?<ins\s+class="new-price">\s*([\d.,]+)\s*z[lł]\s*</ins>',
                    html, _re.DOTALL | _re.IGNORECASE
                )
                for href, pid, slug, name, price in cards:
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    title = name.strip() if name.strip() else slug.replace('-', ' ').title()
                    # Wyczyść HTML z nazwy
                    title = _re.sub(r'<[^>]+>', '', title).strip()
                    products.append({
                        'title': title,
                        'price_text': f'{price} zł',
                        'url': f'https://warrington.store{href}',
                        'available': True,
                        'id': pid,
                    })
            except:
                continue
            if len(products) >= 30:
                break
        # Jeśli regex nie złapał (inna struktura HTML) - fallback: proste linki
        if not products:
            for page_url in ['https://warrington.store/products/new']:
                try:
                    resp = _req.get(page_url, headers={'User-Agent': _ua}, timeout=12)
                    if resp.status_code != 200:
                        continue
                    prod_links = _re.findall(r'href="(/product/(\d+)-([^"]+))"', resp.text)
                    prices_all = _re.findall(r'<ins\s+class="new-price">\s*([\d.,]+)\s*z[lł]', resp.text)
                    pi = 0
                    for href, pid, slug in prod_links:
                        if pid in seen_ids:
                            continue
                        seen_ids.add(pid)
                        title = slug.replace('-', ' ').title()
                        price_txt = f'{prices_all[pi]} zł' if pi < len(prices_all) else '?'
                        pi += 1
                        products.append({
                            'title': title,
                            'price_text': price_txt,
                            'url': f'https://warrington.store{href}',
                            'available': True,
                            'id': pid,
                        })
                except:
                    continue
        # Jeśli znaleźliśmy produkty - zwróć; jeśli nie - podaj kategorie
        if products:
            return jsonify({'ok': True, 'products': products[:35], 'total': len(products), 'source': 'html_new'})
        # Fallback: zwróć kategorie jako linki
        categories = [
            {'title': 'Nowe palety', 'url': 'https://warrington.store/products/new', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Elektronika i gadżety', 'url': 'https://warrington.store/products/elektronika-i-gadzety', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Dom', 'url': 'https://warrington.store/products/dom', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Zwierzęta', 'url': 'https://warrington.store/products/zwierzeta', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Akcesoria TV', 'url': 'https://warrington.store/products/akcesoria-tv', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Sport', 'url': 'https://warrington.store/products/sport', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Kuchnia', 'url': 'https://warrington.store/products/kuchnia', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Narzędzia', 'url': 'https://warrington.store/products/narzedzia', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Ogród', 'url': 'https://warrington.store/products/ogrod', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Zabawki', 'url': 'https://warrington.store/products/zabawki', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Promocje', 'url': 'https://warrington.store/products/promotions', 'available': True, 'price_text': 'kategoria'},
        ]
        return jsonify({'ok': True, 'products': categories, 'total': len(categories), 'source': 'categories',
            'note': 'Nie udało się pobrać produktów - oto kategorie'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/analityka/okazje/scrape-jobalots')
def scrape_jobalots():
    """Jobalots - prawdziwe dane z API auction-list-v2 (popularne + okazje)"""
    import requests as _req
    _jb_headers = {
        'Content-Type': 'application/json',
        'url-accept-language': 'pl',
        'url-accept-currency': 'pln',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    _jb_base = {
        'manifest_type': ['pallets'],
        'ship_to': 'PL',
        'ship_from': 'all',
        'list_type': ['auction', 'buyitnow'],
        'is_list': True,
        'use_open_search': '0',
        'exact_match': '0',
        'search_manifests': '0',
    }
    try:
        # Pobierz kilka widoków: popularne, najwięcej ofert, najtańsze
        all_items = []
        seen_ids = set()
        for _sort, _per in [('popularity', 12), ('most_bids', 8), ('bid_low', 8)]:
            try:
                _body = {**_jb_base, 'per_page': _per, 'page': 1, 'sort_by': _sort}
                _r = _req.post('https://live1.jobalots.com/api/auction-list-v2',
                    headers=_jb_headers, json=_body, timeout=15)
                _d = _r.json()
                for _it in _d.get('result', {}).get('data', []):
                    _id = _it.get('id')
                    if _id not in seen_ids:
                        seen_ids.add(_id)
                        _it['_sort_tag'] = _sort
                        all_items.append(_it)
            except:
                continue
        total_available = 0
        try:
            _r0 = _req.post('https://live1.jobalots.com/api/auction-list-v2',
                headers=_jb_headers, json={**_jb_base, 'per_page': 1, 'page': 1, 'sort_by': 'popularity'}, timeout=10)
            total_available = _r0.json().get('result', {}).get('total', 0)
        except:
            pass
        items = all_items if all_items else []
        resp_data = {'error': False, 'status': 200, 'result': {'data': items, 'total': total_available or len(items)}}
        data = resp_data
        if data.get('error'):
            return jsonify({'ok': False, 'error': data.get('message', 'API error')})
        result = data.get('result', {})
        items = result.get('data', [])
        products = []
        # Kurs GBP→PLN (aktualizuj raz na jakiś czas)
        _GBP_PLN = float(get_config('gbp_pln_rate') or 5.30)
        _EUR_PLN = float(get_config('eur_pln_rate') or 4.35)

        for item in items:
            sku = item.get('sku', '')
            title = item.get('title', 'Paleta')[:80]
            rrp = float(item.get('rrp', 0) or 0)
            bid = float(item.get('latest_bid_price', 0) or item.get('reserve_price', 0) or 0)
            qty = item.get('qty', '?')
            discount = item.get('discount', 0)
            bid_count = item.get('bid_count', 0)

            # Przelicz walutę na PLN
            _orig_currency = (item.get('currency', '') or '').upper()
            if _orig_currency == 'GBP':
                rrp = round(rrp * _GBP_PLN, 2)
                bid = round(bid * _GBP_PLN, 2)
            elif _orig_currency == 'EUR':
                rrp = round(rrp * _EUR_PLN, 2)
                bid = round(bid * _EUR_PLN, 2)

            # Konwersja UTC → Warszawa (CET +1 / CEST +2)
            _eat_raw = item.get('end_at', '')
            if _eat_raw:
                try:
                    from datetime import datetime as _dtj, timedelta as _tdj
                    # Parse: "2026-03-06T08:00:00.000000Z"
                    _clean = _eat_raw.split('.')[0].replace('Z', '').replace('T', ' ')
                    _utc_dt = _dtj.strptime(_clean, '%Y-%m-%d %H:%M:%S')
                    _y = _utc_dt.year
                    # Ostatnia niedziela marca (start CEST)
                    _mar31 = _dtj(_y, 3, 31)
                    _last_sun_mar = _dtj(_y, 3, 31 - (_mar31.weekday() + 1) % 7, 2)
                    # Ostatnia niedziela października (koniec CEST)
                    _oct31 = _dtj(_y, 10, 31)
                    _last_sun_oct = _dtj(_y, 10, 31 - (_oct31.weekday() + 1) % 7, 3)
                    _hours = 2 if _last_sun_mar <= _utc_dt < _last_sun_oct else 1
                    _local = _utc_dt + _tdj(hours=_hours)
                    end_at = _local.strftime('%Y-%m-%d %H:%M')
                except Exception as _te:
                    end_at = _eat_raw[:16].replace('T', ' ')
            else:
                end_at = ''
            currency = 'PLN'  # Zawsze PLN po przeliczeniu
            # Obrazek
            manifest = item.get('manifest', {})
            img = ''
            if manifest.get('product_first_image'):
                img = manifest['product_first_image'].get('product_image_thumbnail_url', '')
            url = f'https://jobalots.com/pl/products/{sku}?currency=pln'
            sort_tag = item.get('_sort_tag', '')
            tag_label = {'popularity': '🔥', 'most_bids': '📈', 'bid_low': '💰'}.get(sort_tag, '')
            products.append({
                'title': title,
                'price_text': f'{bid:.0f} {currency}' if bid > 0 else f'{rrp:.0f} {currency} RRP',
                'rrp': rrp,
                'bid': bid,
                'qty': qty,
                'discount': discount,
                'bid_count': bid_count,
                'end_at': end_at,
                'url': url,
                'image': img,
                'sku': sku,
                'tag': tag_label,
            })
        total = result.get('total', len(products))
        return jsonify({'ok': True, 'products': products, 'total': total, 'source': 'api',
            'note': f'🔥 Popularne · 📈 Dużo ofert · 💰 Najtańsze ({total} palet łącznie)'})
    except Exception as e:
        # Fallback: kategorie
        _jb = 'https://jobalots.com/pl/pages/products-on-auction?page=1&currency=pln'
        categories = [
            {'title': 'Wszystkie palety', 'url': f'{_jb}&type=pallets'},
            {'title': 'Electronics', 'url': f'{_jb}&categories=electronics'},
            {'title': 'Home & Kitchen', 'url': f'{_jb}&categories=home-kitchen'},
            {'title': 'Garden', 'url': f'{_jb}&categories=garden'},
            {'title': 'Tools & DIY', 'url': f'{_jb}&categories=tools-diy'},
        ]
        return jsonify({'ok': False, 'error': str(e), 'products': categories, 'total': len(categories),
            'fallback_url': f'{_jb}&type=pallets'})



@app.route('/analityka/okazje/perplexity-status')
def perplexity_status():
    klucz = request.args.get('klucz', '')
    return jsonify({'status': _perplexity_jobs.get(klucz, 'idle')})


@app.route('/analityka/okazje/perplexity-analyze', methods=['POST'])
def okazje_perplexity_analyze():
    import threading
    from modules.database import get_db, get_config, DATABASE as _db_path
    api_key = get_config('perplexity_api_key', '')
    if not api_key:
        return redirect('/analityka/okazje')
    perp_model = get_config('perplexity_model', 'sonar-pro')
    if perp_model == 'sonar': perp_model = 'sonar-pro'
    miesiac = datetime.now().strftime('%Y-%m')
    klucz = f'okazje_{miesiac}'
    if _perplexity_jobs.get(klucz) == 'running':
        return redirect('/analityka/okazje?loading=analyze')
    conn = get_db()
    try:
        # Prawdziwe dane sprzedaży — LEFT JOIN bo większość nie ma produkt_id
        top = conn.execute("""
            SELECT COALESCE(p.nazwa, s.nazwa, 'Produkt') as nazwa,
                   COALESCE(p.kategoria, 'inne') as kategoria,
                   s.cena as cena_sprzedazy,
                   COALESCE(pal.dostawca, p.dostawca) as dostawca,
                   COUNT(*) as ilosc_sprzedanych, SUM(s.cena) as przychod
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            LEFT JOIN palety pal ON p.paleta_id = pal.id
            WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
              AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
              AND s.data_sprzedazy >= date('now', '-60 days')
            GROUP BY COALESCE(p.nazwa, s.nazwa)
            ORDER BY przychod DESC
            LIMIT 15
        """).fetchall()

        # Produkty na stanie (niesprzedane) z ceną
        na_stanie = conn.execute("""
            SELECT p.nazwa, p.kategoria, p.ilosc,
                   COALESCE(p.cena_allegro, p.cena_brutto, 0) as cena,
                   COALESCE(pal.dostawca, p.dostawca) as dostawca
            FROM produkty p
            LEFT JOIN palety pal ON p.paleta_id = pal.id
            WHERE p.ilosc > 0 AND p.status != 'sprzedany'
            ORDER BY COALESCE(p.cena_allegro, p.cena_brutto, 0) DESC
            LIMIT 10
        """).fetchall()

        # Kategorie z największym przychodem (bez wymagania produkt_id)
        kategorie = conn.execute("""
            SELECT COALESCE(p.kategoria, 'inne') as kategoria,
                   COUNT(*) as cnt, SUM(s.cena) as przychod
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
              AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
              AND s.data_sprzedazy >= date('now', '-60 days')
            GROUP BY kategoria ORDER BY przychod DESC LIMIT 5
        """).fetchall()
    except Exception as _e:
        print(f"BLAD okazje analyze: {_e}")
        top, na_stanie, kategorie = [], [], []

    sprzedane_txt = "\n".join(
        f"{i}. {r['nazwa'][:60]} [{r['kategoria'] or 'inne'}] — sprzedano {r['ilosc_sprzedanych']}x za {r['cena_sprzedazy']:.0f} zl, przychod {r['przychod']:.0f} zl, dostawca: {r['dostawca'] or 'własny'}"
        for i, r in enumerate(top, 1)) if top else "Brak danych sprzedażowych"

    stanie_txt = "\n".join(
        f"- {r['nazwa'][:60]} [{r['kategoria'] or ''}] — {r['ilosc']} szt na stanie, cena {r['cena']:.0f} zl"
        for r in na_stanie) if na_stanie else "Brak produktów na stanie"

    kat_txt = ", ".join(f"{r['kategoria']} ({r['przychod']:.0f} zl)" for r in kategorie) if kategorie else "mix"

    prompt = (
        f"Jestem sprzedawcą na Allegro, kupuję palety zwrotów konsumenckich i sprzedaję produkty pojedynczo. Data: {miesiac}.\n\n"
        f"=== MOJE NAJLEPIEJ SPRZEDAJĄCE SIĘ PRODUKTY (ostatnie 60 dni) ===\n{sprzedane_txt}\n\n"
        f"=== PRODUKTY NA STANIE (niesprzedane) ===\n{stanie_txt}\n\n"
        f"=== MOJE TOP KATEGORIE ===\n{kat_txt}\n\n"
        f"Sprawdź aktualne ceny tych produktów na Allegro.pl. Dla każdego sprzedanego produktu podaj:\n"
        f"1. Aktualna cena na Allegro (ile ofert jest)\n"
        f"2. Czy moja cena sprzedaży była dobra vs rynek\n"
        f"3. Dla produktów na stanie — za ile warto wystawić\n\n"
        f"Na koniec podaj podsumowanie: które kategorie produktów z palet są najbardziej opłacalne "
        f"i jakie typy palet powinienem kupować w przyszłości.\n"
        f"Odpowiedz po polsku, z cenami w złotych i linkami do wyszukań na Allegro."
    )
    threading.Thread(target=_run_perplexity, args=(klucz, prompt, api_key, _db_path, perp_model), daemon=True).start()
    return redirect('/analityka/okazje?loading=analyze')


@app.route('/analityka/okazje/perplexity-szukaj', methods=['POST'])
def okazje_perplexity_szukaj():
    import threading
    from modules.database import get_db, get_config, DATABASE as _db_path
    api_key = get_config('perplexity_api_key', '')
    if not api_key:
        return redirect('/analityka/okazje')
    perp_model = get_config('perplexity_model', 'sonar-pro')
    if perp_model == 'sonar': perp_model = 'sonar-pro'
    miesiac = datetime.now().strftime('%Y-%m')
    klucz = f'szukaj_{miesiac}'
    if _perplexity_jobs.get(klucz) == 'running':
        return redirect('/analityka/okazje?loading=szukaj')
    conn = get_db()
    try:
        # Co się sprzedaje — LEFT JOIN bo większość nie ma produkt_id
        top_sprzedaz = conn.execute("""
            SELECT COALESCE(p.nazwa, s.nazwa, 'Produkt') as nazwa,
                   COALESCE(p.kategoria, 'inne') as kategoria,
                   s.cena, COALESCE(pal.dostawca, p.dostawca) as dostawca
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            LEFT JOIN palety pal ON p.paleta_id = pal.id
            WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
              AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
              AND s.data_sprzedazy >= date('now', '-60 days')
            ORDER BY s.cena DESC
            LIMIT 10
        """).fetchall()

        # Kategorie z najlepszym przychodem
        top_kat = conn.execute("""
            SELECT COALESCE(p.kategoria, 'inne') as kategoria,
                   COUNT(*) as cnt, SUM(s.cena) as przychod,
                   AVG(s.cena) as sr_cena
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
              AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
              AND s.data_sprzedazy >= date('now', '-60 days')
            GROUP BY kategoria ORDER BY przychod DESC LIMIT 5
        """).fetchall()

        # Palety z wynikami
        palety_roi = conn.execute("""
            SELECT pal.nazwa, pal.dostawca, pal.cena_zakupu, pal.ilosc_produktow,
                   COALESCE(SUM(CASE WHEN s.id IS NOT NULL THEN 1 ELSE 0 END), 0) as sprzedanych,
                   COALESCE(SUM(s.cena), 0) as przychod_z_palety
            FROM palety pal
            LEFT JOIN produkty p ON p.paleta_id = pal.id
            LEFT JOIN sprzedaze s ON s.produkt_id = p.id
              AND s.status NOT IN ('zwrot','anulowane','anulowana')
              AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
            GROUP BY pal.id
            ORDER BY pal.data_zakupu DESC
            LIMIT 8
        """).fetchall()
    except Exception as _e:
        print(f"BLAD okazje szukaj: {_e}")
        top_sprzedaz, top_kat, palety_roi = [], [], []

    sprzedaz_txt = "\n".join(
        f"- {r['nazwa'][:50]} [{r['kategoria'] or ''}] — {r['cena']:.0f} zl, dostawca: {r['dostawca'] or 'własny'}"
        for r in top_sprzedaz) if top_sprzedaz else "brak danych"

    kat_txt = "\n".join(
        f"- {r['kategoria']}: {r['cnt']}x sprzedanych, {r['przychod']:.0f} zl przychód, śr. {r['sr_cena']:.0f} zl/szt"
        for r in top_kat) if top_kat else "elektronika, AGD, sport"

    palety_txt = "\n".join(
        f"- {r['nazwa']} ({r['dostawca']}): kupiono za {r['cena_zakupu']:.0f} zl ({r['ilosc_produktow']} szt), sprzedano {r['sprzedanych']}x = {(r['przychod_z_palety'] or 0):.0f} zl"
        for r in palety_roi) if palety_roi else "brak danych"

    # Pobierz PRAWDZIWE produkty z Warrington (nowa strona, nie-Shopify)
    warrington_txt = ""
    try:
        import requests as _rq, re as _rre, json as _jjw
        _ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
        wr_items = []
        wr_seen = set()
        for _wurl in ['https://warrington.store/products/new', 'https://warrington.store/products/new/page/2']:
            try:
                wr = _rq.get(_wurl, headers={'User-Agent': _ua}, timeout=12)
                if wr.status_code != 200:
                    continue
                # Karty produktów: <h3 class="product-name"><a href="/product/{id}-{slug}">{nazwa}</a></h3>
                # potem: <ins class="new-price">{cena} zl</ins>
                _cards = _rre.findall(
                    r'<h3\s+class="product-name">\s*<a\s+href="(/product/(\d+)-([^"]+))"[^>]*>\s*(.*?)\s*</a>\s*</h3>.*?<ins\s+class="new-price">\s*([\d.,]+)\s*z[lł]',
                    wr.text, _rre.DOTALL | _rre.IGNORECASE
                )
                for _href, _pid, _slug, _name, _price in _cards:
                    if _pid in wr_seen:
                        continue
                    wr_seen.add(_pid)
                    _title = _rre.sub(r'<[^>]+>', '', _name).strip() if _name.strip() else _slug.replace('-', ' ').title()
                    wr_items.append(f"- {_title} | cena: {_price} zł | link: https://warrington.store{_href}")
                # Fallback: proste linki jeśli regex nie złapał
                if not _cards:
                    _links = _rre.findall(r'href="(/product/(\d+)-([^"]+))"', wr.text)
                    _prices = _rre.findall(r'<ins\s+class="new-price">\s*([\d.,]+)\s*z[lł]', wr.text)
                    _pi = 0
                    for _href, _pid, _slug in _links:
                        if _pid in wr_seen:
                            continue
                        wr_seen.add(_pid)
                        _title = _slug.replace('-', ' ').title()
                        _pr = f" | cena: {_prices[_pi]} zł" if _pi < len(_prices) else ""
                        _pi += 1
                        wr_items.append(f"- {_title}{_pr} | link: https://warrington.store{_href}")
            except:
                continue
            if len(wr_items) >= 15:
                break
        if wr_items:
            warrington_txt = "\n".join(wr_items[:20])
        else:
            warrington_txt = "Nie udalo sie pobrac produktow. Strona: https://warrington.store/products/new"
    except Exception as _we:
        warrington_txt = f"blad pobierania: {_we}"

    # Pobierz PRAWDZIWE palety z Jobalots API
    jobalots_txt = ""
    try:
        import requests as _rqj
        _jb_resp = _rqj.post(
            'https://live1.jobalots.com/api/auction-list-v2',
            headers={'Content-Type': 'application/json', 'url-accept-language': 'pl', 'url-accept-currency': 'pln'},
            json={'per_page': 15, 'page': 1, 'sort_by': 'auction_end_soon',
                  'manifest_type': ['pallets'], 'ship_to': 'PL', 'ship_from': 'all',
                  'list_type': ['auction', 'buyitnow'], 'is_list': True},
            timeout=20
        )
        _jb_data = _jb_resp.json()
        _jb_items = []
        _GBP_PLN_ai = float(get_config('gbp_pln_rate') or 5.30)
        _EUR_PLN_ai = float(get_config('eur_pln_rate') or 4.35)
        for _ji in _jb_data.get('result', {}).get('data', [])[:15]:
            _jsku = _ji.get('sku', '')
            _jtitle = _ji.get('title', '')[:60]
            _jrrp = float(_ji.get('rrp', 0) or 0)
            _jbid = float(_ji.get('latest_bid_price', 0) or _ji.get('reserve_price', 0) or 0)
            _jqty = _ji.get('qty', '?')
            _jcur_orig = (_ji.get('currency', '') or '').upper()
            # Przelicz na PLN
            if _jcur_orig == 'GBP':
                _jrrp = round(_jrrp * _GBP_PLN_ai, 2)
                _jbid = round(_jbid * _GBP_PLN_ai, 2)
            elif _jcur_orig == 'EUR':
                _jrrp = round(_jrrp * _EUR_PLN_ai, 2)
                _jbid = round(_jbid * _EUR_PLN_ai, 2)
            _jurl = f'https://jobalots.com/pl/products/{_jsku}?currency=pln'
            _jprice = f'{_jbid:.0f} PLN' if _jbid > 0 else f'{_jrrp:.0f} PLN RRP'
            _jb_items.append(f"- {_jtitle} | {_jqty} szt | cena: {_jprice} | RRP: {_jrrp:.0f} PLN | link: {_jurl}")
        jobalots_txt = "\n".join(_jb_items) if _jb_items else "brak danych z API"
    except Exception as _je:
        jobalots_txt = f"blad pobierania: {_je}"

    prompt = (
        f"Jestem sprzedawcą na Allegro w Polsce, kupuję palety zwrotów konsumenckich i sprzedaję pojedynczo. Data: {miesiac}.\n\n"
        f"=== CO MI SIĘ NAJLEPIEJ SPRZEDAJE (ostatnie 60 dni) ===\n{sprzedaz_txt}\n\n"
        f"=== MOJE NAJLEPSZE KATEGORIE ===\n{kat_txt}\n\n"
        f"=== MOJE DOTYCHCZASOWE PALETY (wyniki) ===\n{palety_txt}\n\n"
        f"=== AKTUALNE PALETY NA WARRINGTON.STORE (prawdziwe dane ze sklepu) ===\n{warrington_txt}\n\n"
        f"=== AKTUALNE AUKCJE PALET NA JOBALOTS.COM (prawdziwe dane z API) ===\n{jobalots_txt}\n\n"
        f"ZADANIE:\n"
        f"Masz powyżej PRAWDZIWE, aktualne dane z obu sklepów z linkami.\n"
        f"1. Przeanalizuj które palety pasują do mojego profilu sprzedażowego (kategorie, marża, dostawca).\n"
        f"2. Dla KAŻDEJ rekomendowanej palety podaj link DOKŁADNIE taki jak w danych powyżej — NIE zmieniaj go!\n\n"
        f"FORMAT ODPOWIEDZI — dla każdej palety użyj sekcji z ###:\n\n"
        f"### 1. Nazwa palety\n"
        f"- Źródło: warrington.store / jobalots.com\n"
        f"- Cena: X PLN (aktualna oferta/cena)\n"
        f"- RRP: wartość rynkowa\n"
        f"- Zawartość: {'{'}ilość{'}'} szt, co jest w palecie\n"
        f"- Link: SKOPIUJ DOKŁADNIE z danych powyżej!\n"
        f"- Dlaczego pasuje: odnieś do moich najlepiej sprzedających się kategorii\n\n"
        f"WAŻNE: Skopiuj linki DOSŁOWNIE z danych — NIE wymyślaj nowych URL-i!\n"
        f"Na koniec dodaj sekcję ### PODSUMOWANIE z TOP 3 paletami i szacowanym zyskiem.\n"
        f"Odpowiedz po polsku."
    )
    threading.Thread(target=_run_perplexity, args=(klucz, prompt, api_key, _db_path, perp_model), daemon=True).start()
    return redirect('/analityka/okazje?loading=szukaj')


@app.route('/analityka/czas-sprzedazy')
def analityka_czas_sprzedazy():
    """Analityka czasu sprzedaży - od dodania/zakupu do sprzedaży, bazuje na produkty"""
    from modules.database import get_db
    import json as _json
    conn = get_db()

    # Migracja inline - dodaj brakujące kolumny w oferty jeśli stara baza
    for _sql in [
        "ALTER TABLE oferty ADD COLUMN tytul TEXT DEFAULT ''",
        "ALTER TABLE oferty ADD COLUMN data_wystawienia TIMESTAMP",
        "ALTER TABLE sprzedaze ADD COLUMN nazwa TEXT DEFAULT ''",
        "ALTER TABLE sprzedaze ADD COLUMN data_syncu TIMESTAMP",
    ]:
        try:
            conn.execute(_sql)
            conn.commit()
        except:
            pass  # kolumna już istnieje

    # MIGRACJA JEDNORAZOWA: przenieś stare przychod_offline z produkty -> sprzedaze
    # Stare rekordy mają sprzedano_offline > 0 i przychod_offline > 0
    # ale NIE mają rekordu w sprzedaze (sprzedane przed nowym systemem)
    try:
        stare = conn.execute("""
            SELECT p.id, p.nazwa, p.przychod_offline, p.sprzedano_offline,
                   p.data_dodania, pal.data_zakupu
            FROM produkty p
            LEFT JOIN palety pal ON pal.id = p.paleta_id
            WHERE p.sprzedano_offline > 0
              AND p.przychod_offline > 0
              AND NOT EXISTS (
                  SELECT 1 FROM sprzedaze s
                  WHERE s.produkt_id = p.id AND s.kupujacy = 'offline'
              )
        """).fetchall()
        from datetime import datetime as _dt2
        for row in stare:
            data = row['data_zakupu'] or row['data_dodania'] or _dt2.now().strftime('%Y-%m-%dT%H:%M:%S')
            cena_szt = round(row['przychod_offline'] / max(row['sprzedano_offline'], 1), 2)
            conn.execute("""
                INSERT INTO sprzedaze (produkt_id, nazwa, cena, ilosc, status, data_sprzedazy, kupujacy, notified)
                VALUES (?, ?, ?, ?, 'sprzedana', ?, 'offline', 1)
            """, (row['id'], row['nazwa'] or f'Produkt #{row["id"]}',
                  cena_szt, row['sprzedano_offline'], data))
        if stare:
            # Wyzeruj przychod_offline żeby nie duplikować (dane są już w sprzedaze)
            ids = [r['id'] for r in stare]
            placeholders = ','.join('?' * len(ids))
            conn.execute(f"UPDATE produkty SET przychod_offline = 0 WHERE id IN ({placeholders})", ids)
            conn.commit()
            print(f"✅ Migracja offline: przeniesiono {len(stare)} produktów do sprzedaze, wyzerowano przychod_offline")
    except Exception as _e:
        print(f"⚠️ Migracja offline: {_e}")

    # Napraw rekordy offline w sprzedaze które mają cena=0
    # (zostały dodane przez poprzednią wersję kodu z błędem)
    try:
        conn.execute("""
            UPDATE sprzedaze SET cena = (
                SELECT COALESCE(NULLIF(p.cena_allegro,0), p.cena_brutto, 0)
                FROM produkty p WHERE p.id = sprzedaze.produkt_id
            )
            WHERE kupujacy = 'offline'
              AND (cena IS NULL OR cena = 0)
              AND produkt_id IS NOT NULL
        """)
        naprawione = conn.execute("SELECT changes()").fetchone()[0]
        if naprawione:
            conn.commit()
            print(f"✅ Naprawiono ceny offline: {naprawione} rekordów")
    except Exception as _e:
        print(f"⚠️ Naprawa cen offline: {_e}")

    # Backfill data_syncu: dla rekordów bez powiązanego produktu/oferty
    # użyj data_sprzedazy jako przybliżonej daty dodania do systemu
    try:
        conn.execute("""
            UPDATE sprzedaze SET data_syncu = data_sprzedazy
            WHERE data_syncu IS NULL
              AND produkt_id IS NULL
              AND data_sprzedazy IS NOT NULL
        """)
        conn.commit()
    except:
        pass

    # Backfill produkty.data_dodania z pierwszej sprzedaży produktu
    # (dla produktów dodanych przez import bez daty)
    try:
        conn.execute("""
            UPDATE produkty SET data_dodania = (
                SELECT MIN(s.data_sprzedazy)
                FROM sprzedaze s
                WHERE s.produkt_id = produkty.id
                  AND s.data_sprzedazy IS NOT NULL
            )
            WHERE (data_dodania IS NULL OR data_dodania = '')
              AND id IN (SELECT DISTINCT produkt_id FROM sprzedaze WHERE produkt_id IS NOT NULL)
        """)
        conn.commit()
    except:
        pass

    # Backfill produkty.data_dodania z daty zakupu palety (pre-Paletomat)
    try:
        conn.execute("""
            UPDATE produkty SET data_dodania = (
                SELECT p2.data_zakupu FROM palety p2
                WHERE p2.id = produkty.paleta_id
                  AND p2.data_zakupu IS NOT NULL
            )
            WHERE (data_dodania IS NULL OR data_dodania = '')
              AND paleta_id IS NOT NULL
        """)
        conn.commit()
    except:
        pass

    # Backfill: uzupełnij s.nazwa z oferty.tytul dla starych rekordów
    try:
        conn.execute("""
            UPDATE sprzedaze SET nazwa = (
                SELECT COALESCE(o.tytul, '')
                FROM oferty o WHERE o.id = sprzedaze.oferta_id
            )
            WHERE (nazwa IS NULL OR nazwa = '')
              AND oferta_id IS NOT NULL
        """)
        conn.commit()
    except:
        pass
    # Backfill2: dla rekordów bez oferta_id — spróbuj przez allegro_order_id
    try:
        conn.execute("""
            UPDATE sprzedaze SET nazwa = (
                SELECT COALESCE(o.tytul, '')
                FROM oferty o
                JOIN sprzedaze s2 ON s2.oferta_id = o.id
                WHERE s2.allegro_order_id = sprzedaze.allegro_order_id
                LIMIT 1
            )
            WHERE (nazwa IS NULL OR nazwa = '')
              AND oferta_id IS NULL
              AND allegro_order_id IS NOT NULL
        """)
        conn.commit()
    except:
        pass
    # Backfill3: dla tych co mają oferta_id ale brak tytułu w oferty — użyj allegro_id jako nazwy
    try:
        conn.execute("""
            UPDATE sprzedaze SET nazwa = 
                'Zamówienie ' || SUBSTR(allegro_order_id, 1, 8)
            WHERE (nazwa IS NULL OR nazwa = '' OR nazwa LIKE 'Zamówienie #%')
              AND allegro_order_id IS NOT NULL
              AND (SELECT COALESCE(o.tytul,'') FROM oferty o WHERE o.id = sprzedaze.oferta_id) = ''
        """)
        conn.commit()
    except:
        pass

    # === DANE OD WYSTAWIENIA / DODANIA ===
    # Bierzemy produkty ze statusem sprzedany LUB z data_sprzedazy
    # Łączymy z ofertami (opcjonalnie) żeby mieć data_wystawienia na Allegro
    # Fallback: data_dodania = kiedy produkt trafił do systemu
    # Główne źródło: tabela sprzedaze (ma daty z Allegro) + produkty (dla dat dodania)
    # JOIN przez oferta_id -> oferty -> produkt_id LUB bezpośrednio przez produkt_id
    dane_od_wystawienia = conn.execute("""
        SELECT
            COALESCE(NULLIF(p.nazwa,''), NULLIF(s.nazwa,''), CASE WHEN s.allegro_order_id IS NOT NULL THEN 'Zamówienie ' || SUBSTR(s.allegro_order_id,1,8) ELSE 'Brak nazwy' END) as nazwa,
            s.cena,
            s.data_sprzedazy,
            COALESCE(
                o.data_wystawienia,
                o2.data_wystawienia,
                p.data_dodania,
                (SELECT pal.data_zakupu FROM palety pal WHERE pal.id = p.paleta_id)) as data_od,
            p.kategoria, p.dostawca,
            CASE
              WHEN COALESCE(o.data_wystawienia, o2.data_wystawienia, p.data_dodania,
                            (SELECT pal.data_zakupu FROM palety pal WHERE pal.id = p.paleta_id)) IS NOT NULL
              THEN MAX(0, (julianday(REPLACE(SUBSTR(s.data_sprzedazy,1,19),'T',' '))
                   - julianday(REPLACE(SUBSTR(
                       COALESCE(o.data_wystawienia, o2.data_wystawienia, p.data_dodania,
                                (SELECT pal.data_zakupu FROM palety pal WHERE pal.id = p.paleta_id)),1,19),'T',' '))))
              ELSE NULL
            END as dni_od_wystawienia
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        LEFT JOIN oferty o ON s.oferta_id = o.id
        LEFT JOIN oferty o2 ON o2.produkt_id = s.produkt_id AND s.oferta_id IS NULL
        WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
          AND s.data_sprzedazy IS NOT NULL AND s.data_sprzedazy != ''
          AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
        ORDER BY dni_od_wystawienia ASC
    """).fetchall()

    # === DANE OD ZAKUPU PALETY ===
    dane_od_zakupu = conn.execute("""
        SELECT
            COALESCE(NULLIF(p.nazwa,''), NULLIF(s.nazwa,''), CASE WHEN s.allegro_order_id IS NOT NULL THEN 'Zamówienie ' || SUBSTR(s.allegro_order_id,1,8) ELSE 'Brak nazwy' END) as nazwa,
            s.cena, s.data_sprzedazy,
            pal.data_zakupu, pal.nazwa as paleta_nazwa, pal.dostawca,
            (julianday(REPLACE(SUBSTR(s.data_sprzedazy,1,19),'T',' '))
             - julianday(pal.data_zakupu)) as dni_od_zakupu
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        JOIN palety pal ON p.paleta_id = pal.id
        WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
          AND s.data_sprzedazy IS NOT NULL AND s.data_sprzedazy != ''
          AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
          AND pal.data_zakupu IS NOT NULL
          AND (julianday(REPLACE(SUBSTR(s.data_sprzedazy,1,19),'T',' '))
               - julianday(pal.data_zakupu)) >= 0
        ORDER BY dni_od_zakupu ASC
    """).fetchall()


    def fmt_dni(d):
        if d is None: return '?'
        d = float(d)
        if d < 0.04: return '<1h'  # mniej niż godzina
        if d < 1: return f'{int(d*24)}h'
        return f'{d:.1f} dni'

    dw = [float(r['dni_od_wystawienia']) for r in dane_od_wystawienia if r['dni_od_wystawienia'] is not None]
    stat_w = {}
    if dw:
        sd = sorted(dw)
        stat_w = {'srednia': sum(dw)/len(dw), 'mediana': sd[len(sd)//2], 'min': sd[0], 'max': sd[-1],
                  'cnt': len(dw), 'w_24h': sum(1 for d in dw if d <= 1),
                  'w_7dni': sum(1 for d in dw if d <= 7), 'w_30dni': sum(1 for d in dw if d <= 30),
                  'pow_30dni': sum(1 for d in dw if d > 30)}

    dz = [float(r['dni_od_zakupu']) for r in dane_od_zakupu if r['dni_od_zakupu'] is not None]
    stat_z = {}
    if dz:
        sz = sorted(dz)
        stat_z = {'srednia': sum(dz)/len(dz), 'mediana': sz[len(sz)//2], 'min': sz[0], 'max': sz[-1],
                  'cnt': len(dz), 'w_7dni': sum(1 for d in dz if d <= 7),
                  'w_30dni': sum(1 for d in dz if d <= 30), 'w_60dni': sum(1 for d in dz if d <= 60),
                  'pow_60dni': sum(1 for d in dz if d > 60)}

    histogram_w = [0]*8
    for d in dw:
        if d <= 1: histogram_w[0] += 1
        elif d <= 3: histogram_w[1] += 1
        elif d <= 7: histogram_w[2] += 1
        elif d <= 14: histogram_w[3] += 1
        elif d <= 30: histogram_w[4] += 1
        elif d <= 60: histogram_w[5] += 1
        elif d <= 90: histogram_w[6] += 1
        else: histogram_w[7] += 1

    dostawca_stats = {}
    for r in dane_od_zakupu:
        d = r['dostawca'] or 'Nieznany'
        if d not in dostawca_stats: dostawca_stats[d] = []
        if r['dni_od_zakupu'] is not None: dostawca_stats[d].append(float(r['dni_od_zakupu']))
    dostawcy_wyniki = sorted(
        [{'dostawca': d, 'srednia': sum(v)/len(v), 'cnt': len(v)} for d,v in dostawca_stats.items() if len(v) >= 1],
        key=lambda x: x['srednia'])

    # Filtruj rekordy z datami (nie-NULL) do rankingów
    _dane_z_datami = [r for r in dane_od_wystawienia if r['dni_od_wystawienia'] is not None]

    # Deduplikacja — każdy produkt tylko raz (najszybszy czas)
    _seen_fast = set()
    najszybsze = []
    for r in _dane_z_datami:
        n = r['nazwa']
        if n not in _seen_fast:
            _seen_fast.add(n)
            najszybsze.append(r)
            if len(najszybsze) >= 10:
                break

    # Deduplikacja — każdy produkt tylko raz (najwolniejszy czas)
    _seen_slow = set()
    najwolniejsze = []
    for r in reversed(_dane_z_datami):
        n = r['nazwa']
        if n not in _seen_slow:
            _seen_slow.add(n)
            najwolniejsze.append(r)
            if len(najwolniejsze) >= 10:
                break

    cnt_bez_daty = len(dane_od_wystawienia) - len(_dane_z_datami)

    lbl_j = _json.dumps(['≤1 dzień','2-3 dni','4-7 dni','1-2 tyg','2-4 tyg','1-2 mies','2-3 mies','3+ mies'])
    dat_j = _json.dumps(histogram_w)

    karta_w = ""
    if stat_w:
        karta_w = f"""
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.4rem;font-weight:700;color:#22c55e">{stat_w['srednia']:.1f}</div>
                <div style="font-size:0.65rem;color:#64748b">ŚR. DNI</div>
            </div>
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.4rem;font-weight:700;color:#3b82f6">{stat_w['mediana']:.1f}</div>
                <div style="font-size:0.65rem;color:#64748b">MEDIANA</div>
            </div>
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.2rem;font-weight:700;color:#f59e0b">{fmt_dni(stat_w['min'])}</div>
                <div style="font-size:0.65rem;color:#64748b">NAJSZYBCIEJ</div>
            </div>
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.1rem;font-weight:700;color:#ef4444">{fmt_dni(stat_w['max'])}</div>
                <div style="font-size:0.65rem;color:#64748b">NAJWOLNIEJ</div>
            </div>
        </div>
        <div style="margin-top:10px;font-size:0.75rem;color:#94a3b8;text-align:center">{stat_w['cnt']} sprzedanych produktów</div>
        <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">
            <span style="background:#22c55e22;color:#22c55e;padding:3px 8px;border-radius:6px;font-size:0.7rem">⚡ {stat_w['w_24h']} w 24h</span>
            <span style="background:#3b82f622;color:#3b82f6;padding:3px 8px;border-radius:6px;font-size:0.7rem">📅 {stat_w['w_7dni']} w tyg</span>
            <span style="background:#f59e0b22;color:#f59e0b;padding:3px 8px;border-radius:6px;font-size:0.7rem">📆 {stat_w['w_30dni']} w mies</span>
            <span style="background:#ef444422;color:#ef4444;padding:3px 8px;border-radius:6px;font-size:0.7rem">🐢 {stat_w['pow_30dni']} pow. 30 dni</span>
        </div>"""
    else:
        info = f' ({cnt_bez_daty} szt. sprzedanych bez daty — synchronizuj z Allegro)' if cnt_bez_daty else ''
        karta_w = f'<div style="color:#64748b;font-size:0.85rem;padding:10px">Brak danych z datą sprzedaży.<br><span style="color:#f59e0b;font-size:0.8rem">{cnt_bez_daty} produktów sprzedanych bez daty — synchronizuj z Allegro lub kliknij -1 szt (od v32 ustawia datę)</span></div>'

    karta_z = ""
    if stat_z:
        karta_z = f"""
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.4rem;font-weight:700;color:#3b82f6">{stat_z['srednia']:.1f}</div>
                <div style="font-size:0.65rem;color:#64748b">ŚR. DNI</div>
            </div>
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.4rem;font-weight:700;color:#22c55e">{stat_z['mediana']:.1f}</div>
                <div style="font-size:0.65rem;color:#64748b">MEDIANA</div>
            </div>
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.1rem;font-weight:700;color:#f59e0b">{fmt_dni(stat_z['min'])}</div>
                <div style="font-size:0.65rem;color:#64748b">NAJSZYBCIEJ</div>
            </div>
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.1rem;font-weight:700;color:#ef4444">{fmt_dni(stat_z['max'])}</div>
                <div style="font-size:0.65rem;color:#64748b">NAJWOLNIEJ</div>
            </div>
        </div>
        <div style="margin-top:10px;font-size:0.75rem;color:#94a3b8;text-align:center">{stat_z['cnt']} sprzedaży z palet</div>
        <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">
            <span style="background:#22c55e22;color:#22c55e;padding:3px 8px;border-radius:6px;font-size:0.7rem">7 dni: {stat_z['w_7dni']}</span>
            <span style="background:#3b82f622;color:#3b82f6;padding:3px 8px;border-radius:6px;font-size:0.7rem">30 dni: {stat_z['w_30dni']}</span>
            <span style="background:#f59e0b22;color:#f59e0b;padding:3px 8px;border-radius:6px;font-size:0.7rem">60 dni: {stat_z['w_60dni']}</span>
            <span style="background:#ef444422;color:#ef4444;padding:3px 8px;border-radius:6px;font-size:0.7rem">60+: {stat_z['pow_60dni']}</span>
        </div>"""
    else:
        karta_z = f'<div style="color:#64748b;font-size:0.85rem;padding:10px">Brak danych z datą sprzedaży.<br><span style="color:#f59e0b;font-size:0.8rem">Produkty muszą być powiązane z paletą i mieć datę sprzedaży z Allegro lub -1 szt</span></div>'

    dostawcy_html = ""
    if dostawcy_wyniki:
        rows = ""
        for i, d in enumerate(dostawcy_wyniki[:8]):
            sep = "border-bottom:1px solid #1e1e2e;" if i < len(dostawcy_wyniki[:8])-1 else ""
            clr = "#22c55e" if d['srednia'] <= 14 else "#f59e0b" if d['srednia'] <= 30 else "#ef4444"
            rows += f'<div style="display:flex;align-items:center;gap:10px;padding:8px 0;{sep}"><div style="flex:1;font-size:0.85rem;font-weight:600">{d["dostawca"]}</div><div style="font-size:0.8rem;color:#64748b">{d["cnt"]} szt</div><div style="font-weight:700;color:{clr}">{d["srednia"]:.1f} dni</div></div>'
        dostawcy_html = f'<div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:16px;margin-bottom:15px"><div style="font-weight:700;color:#f59e0b;margin-bottom:12px">🏭 Dostawcy — średni czas sprzedaży od zakupu palety</div>{rows}</div>'

    def item_row_w(r, kolor, i, total):
        sep = "border-bottom:1px solid #1e1e2e;" if i < total-1 else ""
        name = (r['nazwa'] or 'Brak nazwy')[:50]
        cena = float(r['cena'] or 0)
        return f'<div style="display:flex;align-items:center;gap:8px;padding:6px 0;{sep}"><div style="font-weight:700;color:{kolor};min-width:65px;font-size:0.85rem">{fmt_dni(r["dni_od_wystawienia"])}</div><div style="flex:1;font-size:0.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{name}</div><div style="font-size:0.8rem;color:#64748b;white-space:nowrap">{cena:.0f} zł</div></div>'

    szybkie_html = ""
    if najszybsze:
        rows = "".join(item_row_w(r, "#22c55e", i, len(najszybsze)) for i, r in enumerate(najszybsze))
        szybkie_html = f'<div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:16px;margin-bottom:15px"><div style="font-weight:700;color:#22c55e;margin-bottom:12px">⚡ Najszybciej sprzedane (od dodania do systemu)</div>{rows}</div>'

    wolne_html = ""
    if najwolniejsze:
        rows = "".join(item_row_w(r, "#ef4444", i, len(najwolniejsze)) for i, r in enumerate(najwolniejsze))
        wolne_html = f'<div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:16px;margin-bottom:15px"><div style="font-weight:700;color:#ef4444;margin-bottom:12px">🐢 Najwolniej sprzedane (od dodania do systemu)</div>{rows}</div>'

    chart_html = ""
    if dw:
        chart_html = f"""
        <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:16px;margin-bottom:15px">
            <div style="font-weight:700;color:#94a3b8;margin-bottom:12px">📊 Rozkład czasu sprzedaży (od dodania do systemu)</div>
            <canvas id="histChart" style="max-height:180px"></canvas>
        </div>
        <script src="https://cdn.jsdelivr.net/npm/chart.js@3/dist/chart.min.js"></script>
        <script>
        new Chart(document.getElementById('histChart').getContext('2d'),{{
            type:'bar',
            data:{{labels:{lbl_j},datasets:[{{data:{dat_j},
                backgroundColor:['#22c55e','#22c55e','#3b82f6','#3b82f6','#f59e0b','#ef4444','#ef4444','#7f1d1d'],
                borderRadius:6}}]}},
            options:{{responsive:true,plugins:{{legend:{{display:false}}}},
                scales:{{y:{{beginAtZero:true,grid:{{color:'rgba(255,255,255,0.07)'}},ticks:{{color:'#64748b'}}}},
                         x:{{grid:{{display:false}},ticks:{{color:'#94a3b8',font:{{size:11}}}}}}}}}}
        }});
        </script>"""

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Czas Sprzedaży</title>
    <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0a0a0f;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:20px;max-width:900px;margin:0 auto}}
    h1{{text-align:center;color:#3b82f6;font-size:1.6rem;margin-bottom:5px}}
    .sub{{text-align:center;color:#64748b;font-size:0.8rem;margin-bottom:20px}}
    .back{{color:#64748b;font-size:0.85rem;text-decoration:none;display:inline-block;margin-bottom:15px}}
    .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:15px;margin-bottom:15px}}
    .card{{background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:16px}}
    .card-title{{font-weight:700;margin-bottom:12px;display:flex;align-items:center;gap:8px}}
    @media(max-width:600px){{.grid2{{grid-template-columns:1fr}}}}
    </style></head><body>
    <h1>⏱️ CZAS SPRZEDAŻY</h1>
    <div class="sub">Od dodania do systemu i zakupu palety do sprzedaży</div>
    <a href="/analityka" class="back">← Wróć do analityki</a>
    <div class="grid2">
        <div class="card" style="border-color:rgba(34,197,94,0.4)">
            <div class="card-title" style="color:#22c55e">📋 Od DODANIA DO SYSTEMU</div>
            {karta_w}
        </div>
        <div class="card" style="border-color:rgba(59,130,246,0.4)">
            <div class="card-title" style="color:#3b82f6">🚚 Od ZAKUPU PALETY</div>
            {karta_z}
        </div>
    </div>
    {chart_html}
    {dostawcy_html}
    {szybkie_html}
    {wolne_html}
    <div style='background:#12121a;border:1px solid #3b82f640;border-radius:12px;padding:14px;margin:0 0 16px 0;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap'>
      <div>
        <div style='color:#3b82f6;font-weight:600;font-size:0.85rem'>📅 Daty wystawienia ofert</div>
        <div style='color:#64748b;font-size:0.75rem;margin-top:3px'>Znaki <strong style='color:#f59e0b'>?</strong> = brak daty wystawienia w bazie. Zsynchronizuj oferty z Allegro żeby uzupełnić daty.</div>
      </div>
      <a href='/allegro/sync-oferty-daty' style='background:#3b82f620;color:#3b82f6;border:1px solid #3b82f640;border-radius:8px;padding:8px 16px;font-size:0.8rem;font-weight:600;text-decoration:none;white-space:nowrap'>🔄 Odśwież daty z Allegro</a>
    </div>
    <a href="/analityka" class="btn" style="display:block;text-align:center;margin-bottom:80px;padding:12px;background:#1e1e2e;border-radius:10px;color:#fff;text-decoration:none">← Powrót do analityki</a>
    </div>
    """
    return html



@app.route('/analityka/uzupelnij-adresy', methods=['POST'])
def analityka_uzupelnij_adresy():
    """Uzupełnia adresy dla istniejących zamówień z Allegro"""
    from modules.database import get_db
    from modules.allegro_api import get_orders
    from datetime import datetime, timedelta
    
    conn = get_db()
    
    # Pobierz zamówienia bez adresów
    sprzedaze_bez_adresow = conn.execute('''
        SELECT id, allegro_order_id FROM sprzedaze 
        WHERE (adres IS NULL OR adres = '') AND allegro_order_id IS NOT NULL
    ''').fetchall()
    
    if not sprzedaze_bez_adresow:
        return jsonify({'ok': True, 'count': 0, 'message': 'Wszystkie zamówienia mają adresy'})
    
    # Pobierz zamówienia z Allegro (ostatni miesiąc)
    from_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%dT00:00:00Z')
    all_orders = []
    
    for status in ['READY_FOR_PROCESSING', 'SENT', 'BOUGHT']:
        orders_data, error = get_orders(status, from_date=from_date)
        if orders_data and 'checkoutForms' in orders_data:
            all_orders.extend(orders_data['checkoutForms'])
    
    # Stwórz mapę order_id -> adres
    adresy_map = {}
    for order in all_orders:
        order_id = order['id']
        delivery = order.get('delivery', {})
        address = delivery.get('address', {})
        adres_parts = []
        if address.get('street'):
            adres_parts.append(address.get('street'))
        if address.get('postCode'):
            adres_parts.append(address.get('postCode'))
        if address.get('city'):
            adres_parts.append(address.get('city'))
        if adres_parts:
            adresy_map[order_id] = ', '.join(adres_parts)
    
    # Zaktualizuj adresy
    updated = 0
    for s in sprzedaze_bez_adresow:
        order_id = s['allegro_order_id']
        if order_id in adresy_map:
            conn.execute('UPDATE sprzedaze SET adres = ? WHERE id = ?', (adresy_map[order_id], s['id']))
            updated += 1
    
    conn.commit()
    
    return jsonify({'ok': True, 'count': updated, 'total': len(sprzedaze_bez_adresow)})

# ═══════════════════════════════════════════════════════════════════════════
# ROUTE: Ustawienia drukowania
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/settings/printing', methods=['GET', 'POST'])
def printing_settings():
    """Strona ustawień drukowania"""
    
    if request.method == 'POST':
        # Pobierz dane z formularza
        auto_print = request.form.get('auto_print') == 'on'
        printer_type = request.form.get('printer_type', 'niimbot')
        print_copies = int(request.form.get('print_copies', 1))
        ask_before = request.form.get('ask_before_print') == 'on'
        
        # Zapisz do config
        config = load_config()
        config['auto_print_enabled'] = auto_print
        config['default_printer'] = printer_type
        config['print_copies'] = print_copies
        config['ask_before_print'] = ask_before
        
        if save_full_config(config):
            flash('✅ Ustawienia drukowania zapisane!', 'success')
        else:
            flash('⚠️ Błąd zapisywania ustawień', 'error')
        
        return redirect(url_for('printing_settings'))
    
    # GET - wyświetl formularz
    settings = get_printer_settings()
    
    return render_template('settings_printing.html', 
        auto_print=settings['auto_print'],
        printer=settings['printer'],
        copies=settings['copies'],
        ask_before=settings['ask_before']
    )


@app.route('/settings/printing/test', methods=['POST'])
def test_print():
    """Testowe drukowanie"""
    printer_type = request.form.get('printer_type', 'niimbot')
    
    try:
        # Import odpowiedniego modułu drukarki
        if printer_type == 'niimbot':
            from modules.niimbot_print import test_print as niimbot_test
            niimbot_test()
            flash(f'✅ Test drukowania na Niimbot B1 zakończony!', 'success')
        elif printer_type == 'vretti':
            from modules.vretti_print import test_print as vretti_test
            vretti_test()
            flash(f'✅ Test drukowania na Vretti 420B zakończony!', 'success')
        else:
            flash(f'⚠️ Nieznany typ drukarki: {printer_type}', 'error')
    except ImportError as e:
        flash(f'⚠️ Moduł drukarki nie znaleziony: {e}', 'error')
    except Exception as e:
        flash(f'❌ Błąd drukowania: {e}', 'error')
    
    return redirect(url_for('printing_settings'))


# ═══════════════════════════════════════════════════════════════════════════
# FUNKCJA: Auto-drukowanie po wystawieniu oferty
# ═══════════════════════════════════════════════════════════════════════════

def trigger_auto_print(produkt_id):
    """
    Automatyczne drukowanie etykiety po wystawieniu oferty na Allegro
    
    Args:
        produkt_id: ID produktu w bazie danych
        
    Returns:
        bool: True jeśli drukowanie się powiodło, False w przeciwnym razie
    """
    
    # Sprawdź czy auto-print jest włączony
    if not is_auto_print_enabled():
        return False
    
    printer = get_default_printer()
    
    try:
        conn = get_db()
        produkt = conn.execute(
            "SELECT * FROM produkty WHERE id = ?", 
            (produkt_id,)
        ).fetchone()
        
        if not produkt:
            print(f"⚠️ Produkt ID {produkt_id} nie znaleziony")
            return False
        
        # Wybierz odpowiednią funkcję drukowania
        if printer == 'niimbot':
            from modules.niimbot_print import print_niimbot
            print_niimbot(produkt)
            print(f"✅ Auto-print (Niimbot): {produkt['nazwa'][:50]}")
            
            # Aktualizuj czas wydruku
            conn.execute(
                "UPDATE produkty SET last_printed_at = datetime('now') WHERE id = ?",
                (produkt_id,)
            )
            conn.commit()
            return True
            
        elif printer == 'vretti':
            from modules.vretti_print import print_vretti_usb
            print_vretti_usb(produkt)
            print(f"✅ Auto-print (Vretti): {produkt['nazwa'][:50]}")
            
            # Aktualizuj czas wydruku
            conn.execute(
                "UPDATE produkty SET last_printed_at = datetime('now') WHERE id = ?",
                (produkt_id,)
            )
            conn.commit()
            return True
            
        else:
            print(f"⚠️ Nieznany typ drukarki: {printer}")
            return False
            
    except Exception as e:
        print(f"❌ Auto-print error: {e}")
        import traceback
        traceback.print_exc()
        return False


# ═══════════════════════════════════════════════════════════════════════════
# KONIEC NOWEGO KODU
# ═══════════════════════════════════════════════════════════════════════════
#
# TERAZ POWINNA BYĆ LINIA:
# if __name__ == '__main__':
#     print_banner()
#     ...
#
# ═══════════════════════════════════════════════════════════════════════════

def ensure_offline_columns():
    """Force add offline columns if missing and fix data"""
    from modules.database import get_db, init_db
    
    # Najpierw upewnij się że baza jest zainicjalizowana
    init_db()
    
    conn = get_db()
    try:
        # Sprawdź czy tabela produkty istnieje
        try:
            conn.execute("SELECT 1 FROM produkty LIMIT 1")
        except:
            print("⚠️ Tabela produkty nie istnieje - pomijam ensure_offline_columns")
            return
        
        # Sprawdź i dodaj sprzedano_offline
        try:
            conn.execute("SELECT sprzedano_offline FROM produkty LIMIT 1")
        except:
            try:
                conn.execute("ALTER TABLE produkty ADD COLUMN sprzedano_offline INTEGER DEFAULT 0")
                conn.commit()
                print("✅ Dodano kolumnę sprzedano_offline")
            except:
                pass
        
        # Sprawdź i dodaj przychod_offline
        try:
            conn.execute("SELECT przychod_offline FROM produkty LIMIT 1")
        except:
            try:
                conn.execute("ALTER TABLE produkty ADD COLUMN przychod_offline REAL DEFAULT 0")
                conn.commit()
                print("✅ Dodano kolumnę przychod_offline")
            except:
                pass
        
        # Napraw dane: jeśli sprzedano_offline > 0 ale przychod_offline = 0
        try:
            fixed = conn.execute('''
                UPDATE produkty 
                SET przychod_offline = cena_allegro * sprzedano_offline
                WHERE sprzedano_offline > 0 AND (przychod_offline IS NULL OR przychod_offline = 0)
            ''').rowcount
            if fixed > 0:
                conn.commit()
                print(f"🔧 Naprawiono przychod_offline dla {fixed} produktów")
        except:
            pass
            

        # Sprawdź i dodaj kolumnę notified w sprzedaze
        try:
            conn.execute("SELECT notified FROM sprzedaze LIMIT 1")
        except:
            try:
                conn.execute("ALTER TABLE sprzedaze ADD COLUMN notified INTEGER DEFAULT 0")
                conn.commit()
                print("✅ Dodano kolumnę notified do sprzedaze")
            except Exception as e:
                print(f"⚠️ Błąd migracji notified: {e}")

    except Exception as e:
        print(f"⚠️ Błąd ensure_offline_columns: {e}")


@app.route('/api/sztuki/<int:produkt_id>', methods=['GET'])
def api_sztuki_get(produkt_id):
    from modules.database import get_db
    from flask import jsonify
    conn = get_db()
    # Upewnij się że tabela istnieje
    conn.execute('''CREATE TABLE IF NOT EXISTS sztuki (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        produkt_id INTEGER NOT NULL,
        numer INTEGER NOT NULL,
        stan TEXT DEFAULT 'Nowy',
        status TEXT DEFAULT 'magazyn',
        opis_naprawy TEXT DEFAULT '',
        data_naprawy DATE DEFAULT NULL,
        FOREIGN KEY (produkt_id) REFERENCES produkty(id)
    )''')
    conn.commit()
    p = conn.execute('SELECT id, nazwa, ilosc FROM produkty WHERE id=?', (produkt_id,)).fetchone()
    if not p:
        return jsonify({'ok': False, 'sztuki': []}), 200  # 200 zeby JS nie rzucal bledu
    try:
        conn.execute('ALTER TABLE sztuki ADD COLUMN zdjecie TEXT DEFAULT ""')
        conn.commit()
    except:
        pass
    sztuki = conn.execute('SELECT * FROM sztuki WHERE produkt_id=? ORDER BY numer', (produkt_id,)).fetchall()
    return jsonify({
        'ok': True,
        'produkt': {'id': p['id'], 'nazwa': p['nazwa'], 'ilosc': p['ilosc']},
        'sztuki': [dict(s) for s in sztuki]
    })


@app.route('/api/sztuki/<int:produkt_id>/rozbij', methods=['POST'])
def api_sztuki_rozbij(produkt_id):
    """Ustaw podział sztuk wg stanu: {Nowy: 2, Używany: 1, ...}"""
    from modules.database import get_db
    from flask import jsonify, request
    import json
    
    data = request.get_json() or {}
    podzial = data.get('podzial', {})  # {'Nowy': 1, 'Powystawowy': 2, ...}
    
    conn = get_db()
    # Upewnij się że tabela istnieje
    conn.execute('''CREATE TABLE IF NOT EXISTS sztuki (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        produkt_id INTEGER NOT NULL,
        numer INTEGER NOT NULL,
        stan TEXT DEFAULT 'Nowy',
        status TEXT DEFAULT 'magazyn',
        opis_naprawy TEXT DEFAULT '',
        data_naprawy DATE DEFAULT NULL
    )''')
    conn.commit()
    p = conn.execute('SELECT id, ilosc FROM produkty WHERE id=?', (produkt_id,)).fetchone()
    if not p:
        return jsonify({'ok': False, 'msg': 'Brak produktu'}), 404
    
    # Usuń stare sztuki i wstaw nowe
    conn.execute('DELETE FROM sztuki WHERE produkt_id=?', (produkt_id,))
    numer = 1
    for stan, ile in podzial.items():
        for _ in range(int(ile or 0)):
            conn.execute('INSERT INTO sztuki (produkt_id, numer, stan, status) VALUES (?,?,?,?)',
                (produkt_id, numer, stan, 'magazyn'))
            numer += 1
    conn.commit()
    return jsonify({'ok': True})


@app.route('/api/sztuki/jednostka/<int:sztuka_id>/naprawa', methods=['POST'])
def api_sztuka_naprawa(sztuka_id):
    from modules.database import get_db
    from flask import jsonify, request
    from datetime import date
    
    data = request.get_json() or {}
    opis = data.get('opis', '').strip()
    cofnij = data.get('cofnij', False)
    
    conn = get_db()
    if cofnij:
        conn.execute("UPDATE sztuki SET status='magazyn', opis_naprawy='', data_naprawy=NULL WHERE id=?", (sztuka_id,))
    else:
        conn.execute("UPDATE sztuki SET status='naprawa', opis_naprawy=?, data_naprawy=? WHERE id=?",
            (opis, date.today().isoformat(), sztuka_id))
    conn.commit()
    return jsonify({'ok': True})


@app.route('/api/sztuki/jednostka/<int:sztuka_id>/status', methods=['POST'])
def api_sztuka_status(sztuka_id):
    from modules.database import get_db
    from flask import jsonify, request
    data = request.get_json() or {}
    nowy_status = data.get('status', '')
    conn = get_db()
    conn.execute('UPDATE sztuki SET status=? WHERE id=?', (nowy_status, sztuka_id))
    conn.commit()
    return jsonify({'ok': True})


@app.route('/api/sztuki/jednostka/<int:sztuka_id>/stan', methods=['POST'])
def api_sztuka_stan(sztuka_id):
    from modules.database import get_db
    from flask import jsonify, request
    data = request.get_json() or {}
    nowy_stan = data.get('stan', '')
    conn = get_db()
    conn.execute('UPDATE sztuki SET stan=? WHERE id=?', (nowy_stan, sztuka_id))
    conn.commit()
    return jsonify({'ok': True})


@app.route('/api/sztuki/jednostka/<int:sztuka_id>/notatka', methods=['POST'])
def api_sztuka_notatka(sztuka_id):
    from modules.database import get_db
    from flask import jsonify, request
    from datetime import date
    data = request.get_json() or {}
    notatka = data.get('notatka', '').strip()
    conn = get_db()
    conn.execute('UPDATE sztuki SET opis_naprawy=?, data_naprawy=? WHERE id=?',
        (notatka, date.today().isoformat(), sztuka_id))
    conn.commit()
    return jsonify({'ok': True})


@app.route('/api/sztuki/jednostka/<int:sztuka_id>/zdjecie', methods=['POST'])
def api_sztuka_zdjecie(sztuka_id):
    from modules.database import get_db
    from flask import jsonify, request
    data = request.get_json() or {}
    zdjecie = data.get('zdjecie', '')
    conn = get_db()
    # Dodaj kolumnę jeśli nie istnieje
    try:
        conn.execute('ALTER TABLE sztuki ADD COLUMN zdjecie TEXT DEFAULT ""')
        conn.commit()
    except:
        pass
    conn.execute('UPDATE sztuki SET zdjecie=? WHERE id=?', (zdjecie, sztuka_id))
    conn.commit()
    return jsonify({'ok': True})


@app.route('/debug/czas-sprzedazy')
def debug_czas_sprzedazy():
    from modules.database import get_db
    conn = get_db()
    
    # Check produkty without data_dodania
    brak_daty = conn.execute("""
        SELECT COUNT(*) as cnt FROM produkty 
        WHERE data_dodania IS NULL OR data_dodania = ''
    """).fetchone()['cnt']
    
    # Check how many have paleta_id
    z_paleta = conn.execute("""
        SELECT COUNT(*) as cnt FROM produkty 
        WHERE (data_dodania IS NULL OR data_dodania = '') AND paleta_id IS NOT NULL
    """).fetchone()['cnt']
    
    # Check palety with data_zakupu
    palety_z_data = conn.execute("""
        SELECT COUNT(*) as cnt FROM palety WHERE data_zakupu IS NOT NULL AND data_zakupu != ''
    """).fetchone()['cnt']
    
    # Sample sprzedaz with ?
    sample = conn.execute("""
        SELECT s.id, s.nazwa, s.data_sprzedazy, s.produkt_id, s.oferta_id,
               p.data_dodania, p.paleta_id,
               pal.data_zakupu,
               o.data_wystawienia
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        LEFT JOIN palety pal ON pal.id = p.paleta_id
        LEFT JOIN oferty o ON o.id = s.oferta_id
        WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
          AND s.data_sprzedazy IS NOT NULL
        ORDER BY s.id DESC LIMIT 5
    """).fetchall()
    
    
    rows = ""
    for r in sample:
        rows += f"<tr><td>{r['nazwa'] or '-'[:20]}</td><td>{r['data_sprzedazy'][:10] if r['data_sprzedazy'] else '-'}</td><td>{r['produkt_id']}</td><td>{r['data_dodania'] or 'NULL'}</td><td>{r['paleta_id']}</td><td>{r['data_zakupu'] or 'NULL'}</td><td>{r['data_wystawienia'] or 'NULL'}</td></tr>"
    
    return f"""<html><body style="background:#111;color:#eee;font-family:mono;padding:20px">
    <h2>Debug czas-sprzedazy</h2>
    <p>Produkty bez data_dodania: <b>{brak_daty}</b></p>
    <p>Z paleta_id (mogą dostać date): <b>{z_paleta}</b></p>  
    <p>Palety z data_zakupu: <b>{palety_z_data}</b></p>
    <table border=1 style="border-collapse:collapse;font-size:12px">
    <tr><th>nazwa</th><th>data_sprzedazy</th><th>produkt_id</th><th>data_dodania</th><th>paleta_id</th><th>data_zakupu</th><th>data_wystawienia</th></tr>
    {rows}
    </table>
    </body></html>"""


@app.route('/debug/paleta-js/<int:paleta_id>')
def debug_paleta_js(paleta_id):
    """Zwraca tylko sekcję JS ze strony palety do debugowania"""
    from modules.database import get_db
    conn = get_db()
    produkty = conn.execute('SELECT * FROM produkty WHERE paleta_id = ? LIMIT 3', (paleta_id,)).fetchall()
    
    buttons = ""
    for p in produkty:
        buttons += f"""<button class="btn-korekta" data-pid="{p['id']}" data-ilosc="{p['ilosc'] or 0}" data-cena="{int(p['cena_allegro'] or p['cena_brutto'] or 0)}" data-offline="0">Korekta: {(p['nazwa'] or '')[:30]}</button><br>"""
    
    return f"""<html><body style="background:#111;color:#eee;padding:20px">
    <h2>Debug JS - paleta {paleta_id}</h2>
    <div id="status" style="color:#f59e0b;margin:10px 0">Czeka na klik...</div>
    {buttons}
    <div id="modalTest" style="display:none;background:#333;padding:20px;margin:10px 0;border-radius:8px">
        MODAL DZIAŁA! produktId=<span id="modalPid"></span>
    </div>
    <script>
    document.addEventListener('click', function(e) {{
        const btn = e.target.closest('.btn-korekta');
        if (btn) {{
            document.getElementById('status').textContent = 'Kliknięto! pid=' + btn.dataset.pid;
            document.getElementById('modalPid').textContent = btn.dataset.pid;
            document.getElementById('modalTest').style.display = 'block';
        }}
    }});
    </script>
    </body></html>"""


@app.route('/debug/paleta-html/<int:paleta_id>')
def debug_paleta_html(paleta_id):
    """Render paleta page and extract script/modal sections for inspection"""
    import re
    from flask import Response
    # Call the actual view function
    from flask import current_app
    with current_app.test_request_context(f'/palety/{paleta_id}'):
        try:
            resp = paleta_szczegoly(paleta_id)
            if hasattr(resp, 'get_data'):
                html = resp.get_data(as_text=True)
            else:
                html = str(resp)
            # Extract script tags
            scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
            # Find btn-korekta buttons
            btns = re.findall(r'<button class="btn-korekta"[^>]*>', html)
            out = f"<html><body style='background:#111;color:#eee;padding:20px;font-family:mono'>"
            out += f"<h2>btn-korekta count: {len(btns)}</h2>"
            for b in btns[:3]:
                out += f"<pre style='background:#222;padding:8px;font-size:11px'>{b[:200]}</pre>"
            out += f"<h2>Scripts: {len(scripts)}</h2>"
            for i,s in enumerate(scripts):
                # Show first 500 chars of each script
                out += f"<h3>Script {i+1} ({len(s)} chars)</h3>"
                out += f"<pre style='background:#222;padding:8px;font-size:11px;max-height:200px;overflow:auto'>{s[:800]}</pre>"
            out += "</body></html>"
            return out
        except Exception as e:
            return f"ERROR: {e}"


if __name__ == '__main__':
    # AUTO-FIX: Sprawdź czy baza jest uszkodzona
    import os
    import sqlite3
    import time
    
    db_corrupted = False
    if os.path.exists('akces_hub.db'):
        try:
            # Test połączenia
            test_conn = sqlite3.connect('akces_hub.db')
            test_conn.execute('PRAGMA journal_mode=WAL')
            test_conn.close()
        except sqlite3.DatabaseError:
            print("=" * 70)
            print("⚠️  UWAGA: Baza danych jest uszkodzona!")
            print("=" * 70)
            db_corrupted = True
    
    if db_corrupted:
        print()
        print("🔧 Próbuję naprawić bazę danych...")
        print()

        import shutil
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f'akces_hub_CORRUPTED_{timestamp}.db'

        try:
            shutil.copy('akces_hub.db', backup_name)
            print(f"✅ Backup uszkodzonej bazy: {backup_name}")
        except:
            print("⚠️  Nie udało się zrobić backupu")

        # Próba naprawy przez dump (BEZ KASOWANIA DANYCH)
        naprawiono = False
        try:
            import sqlite3 as _sq
            stara = _sq.connect('akces_hub.db', timeout=5)
            nowa_conn = _sq.connect('akces_hub_naprawiona_auto.db')
            bledy = 0
            linie = 0
            for linia in stara.iterdump():
                try:
                    nowa_conn.execute(linia)
                    linie += 1
                except:
                    bledy += 1
            nowa_conn.commit()
            nowa_conn.close()
            stara.close()
            # Weryfikacja
            test = _sq.connect('akces_hub_naprawiona_auto.db')
            cnt = test.execute("SELECT COUNT(*) FROM produkty").fetchone()[0]
            test.close()
            if cnt > 0:
                shutil.move('akces_hub_naprawiona_auto.db', 'akces_hub.db')
                print(f"✅ Baza naprawiona! Uratowano {cnt} produktów.")
                naprawiono = True
            else:
                print("⚠️  Naprawa nie uratowała produktów.")
        except Exception as _e:
            print(f"⚠️  Naprawa nie powiodła się: {_e}")

        if not naprawiono:
            print()
            print("=" * 70)
            print("❌ NIE UDAŁO SIĘ NAPRAWIĆ BAZY!")
            print("=" * 70)
            print("Uruchom ręcznie: python napraw_baze2.py")
            print("Backup uszkodzonej bazy zachowany jako:", backup_name)
            print()
            input("Naciśnij Enter aby zamknąć...")
            exit(1)
    
    print_banner()
    
    # Force add offline columns
    ensure_offline_columns()
    
    # 🚜 KOMBAJN MODE: Cleanup połączeń przy zamknięciu
    import atexit
    import signal
    from modules.database import close_connection_pool
    
    def cleanup_handler():
        """Zamyka wszystkie połączenia z bazą przy zamknięciu"""
        print("\n\n🧹 Cleaning up database connections...")
        # WAL checkpoint - zapisz wszystkie zmiany do głównego pliku DB
        try:
            import sqlite3
            from modules.database import DATABASE
            tmp = sqlite3.connect(DATABASE, timeout=10)
            tmp.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            tmp.close()
            print("✅ WAL checkpoint done")
        except Exception as e:
            print(f"⚠️ WAL checkpoint error: {e}")
        close_connection_pool()
        print("✅ Cleanup done\n")
    
    # Zarejestruj cleanup
    atexit.register(cleanup_handler)
    
    # Obsługa Ctrl+C
    def signal_handler(sig, frame):
        print("\n\n⚠️  Otrzymano sygnał zamknięcia...")
        cleanup_handler()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Sprawdź status bibliotek drukarki
    print("\n📦 Status bibliotek drukarki:")
    libs_status = []
    try:
        import bleak
        libs_status.append("  ✅ bleak (Bluetooth)")
    except ImportError:
        libs_status.append("  ❌ bleak (Bluetooth) - pip install bleak")
    
    # Szczegółowe sprawdzenie niimprint
    niimprint_ok = False
    try:
        from niimprint import BluetoothTransport, PrinterClient
        libs_status.append("  ✅ niimprint (Niimbot)")
        niimprint_ok = True
    except ImportError as e:
        libs_status.append(f"  ❌ niimprint (Niimbot) - brak modułu")
        libs_status.append(f"     → pip install niimprint --break-system-packages")
        libs_status.append(f"     → lub: pip install git+https://github.com/AndBondStyle/niimprint.git")
    except Exception as e:
        libs_status.append(f"  ❌ niimprint - błąd: {e}")
    
    try:
        import qrcode
        from PIL import Image
        libs_status.append("  ✅ pillow/qrcode (obrazy)")
    except ImportError:
        libs_status.append("  ❌ pillow/qrcode (obrazy) - pip install pillow qrcode")
    
    try:
        import barcode
        libs_status.append("  ✅ python-barcode (kody kreskowe)")
    except ImportError:
        libs_status.append("  ⚠️ python-barcode (opcjonalne)")
    
    for s in libs_status:
        print(s)
    
    if not niimprint_ok:
        print("\n  💡 Drukarka Niimbot będzie działać przez BLE (bleak),")
        print("     ale niimprint daje lepszą stabilność.")
    
    # Inicjalizacja bazy
    init_db()
    log("Baza danych OK")

    # Jednorazowe migracje
    from modules.database import migrate_reset_fake_data_wystawienia, fix_product_status_integrity
    migrate_reset_fake_data_wystawienia()

    # Naprawa integralności statusów produktów
    log("Sprawdzam integralnosc danych...")
    fix_product_status_integrity()

    # Uruchom daemon'y w tle
    log("Uruchamiam daemon'y...")
    
    # Auto-backup bazy danych
    try:
        from modules.backup_manager import start_backup_daemon, BACKUP_DIR, get_backups
        start_backup_daemon()
        backups = get_backups()
        backup_info = f"{len(backups)} backupów w {BACKUP_DIR}" if backups else f"brak backupów, folder: {BACKUP_DIR}"
        log(f"Backup daemon uruchomiony (backup co godzine) -- {backup_info}")
    except Exception as e:
        log_warning(f"Backup daemon - blad: {e}")
    
    # Auto-refresh tokena Allegro
    try:
        from modules.token_refresh import start_token_refresh_daemon, get_token_info
        token_info = get_token_info()
        if token_info:
            start_token_refresh_daemon()
            log(f"Token refresh daemon uruchomiony, wygasa: {token_info['expires_at_str']}")
        else:
            log_warning("Token refresh - brak tokena Allegro")
    except Exception as e:
        log_warning(f"Token refresh daemon - blad: {e}")
    
    # Inicjalizacja tabel warehouse heatmap
    try:
        from modules.warehouse_heatmap import init_warehouse_tables
        init_warehouse_tables()
        log("Warehouse heatmap tables OK")
    except Exception as e:
        log_warning(f"Warehouse heatmap init error: {e}")
    
    # Automatyczne czyszczenie starych zdjęć (starsze niż 7 dni)
    try:
        from modules.allegro_api import cleanup_old_images, get_images_stats
        deleted = cleanup_old_images(days=7)
        stats = get_images_stats()
        if deleted > 0:
            log(f"Wyczyszczono {deleted} starych zdjec")
        log(f"Folder zdjec: {stats['count']} plikow ({stats['size_mb']} MB)")
    except Exception as e:
        log_warning(f"Czyszczenie zdjec: {e}")
    
    # Start Telegram bota w tle (raport dzienny + auto-monitoring zamówień)
    try:
        start_bot()
        log("Telegram bot uruchomiony (raport dzienny + auto-monitoring)")
    except Exception as e:
        log_warning(f"Telegram bot error: {e}")

    # Start pallet monitor scheduler (Warrington 10-11, 16-17; Jobalots 8:30, 13:00)
    try:
        from modules.pallet_monitor import start_scheduler as start_pallet_scheduler
        start_pallet_scheduler()
        log("Pallet monitor scheduler uruchomiony")
    except Exception as e:
        log_warning(f"Pallet monitor scheduler error: {e}")
    
    # ============================================================
    # AUTO-SYNC ZAMÓWIEŃ Z ALLEGRO
    # ============================================================
    def auto_sync_orders_loop():
        """Background task - sprawdza nowe zamówienia co 5 minut"""
        from modules.allegro_api import sync_orders, is_authenticated
        from modules.database import get_config
        
        log("Auto-sync zamowien uruchomiony (co 5 minut)")
        
        while True:
            try:
                time.sleep(300)  # Czekaj 5 minut
                
                # Sprawdź czy auto-sync jest włączony
                if get_config('allegro_autosync', 'true') != 'true':
                    continue
                
                # Sprawdź czy Allegro jest połączone
                if not is_authenticated():
                    continue
                
                # Synchronizuj zamówienia z ostatnich 24h (łapie też wczorajsze wieczorne)
                from datetime import datetime, timedelta
                yesterday = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d')
                synced, error = sync_orders(today_only=False, notify=True, from_date_str=yesterday)
                
                if synced > 0:
                    log(f"Auto-sync: {synced} nowych zamowien zsynchronizowanych")

            except Exception as e:
                log_warning(f"Auto-sync blad: {e}")
    
    # Uruchom auto-sync w osobnym wątku
    sync_thread = threading.Thread(target=auto_sync_orders_loop, daemon=True)
    sync_thread.start()
    
    # Pokaż ścieżkę bazy danych
    from modules.database import DATABASE
    log(f"Baza danych: {DATABASE}")
    
    # Automatyczny backup przy zamknięciu (bezpieczeństwo!)
    import atexit
    def shutdown_backup():
        try:
            from modules.backup_manager import create_backup
            log("Tworze backup przed zamknieciem...")
            create_backup()
            log("Backup zapisany!")
        except Exception as e:
            log_warning(f"Blad backupu: {e}")
    
    atexit.register(shutdown_backup)
    log("Auto-backup przy zamknieciu: WLACZONY")
    
    # Auto-backup co 60 minut w tle
    import threading
    def hourly_backup():
        import time
        while True:
            time.sleep(3600)
            try:
                from modules.backup_manager import create_backup
                create_backup()
                log("[Auto] Backup godzinny zapisany")
            except Exception as e:
                log_warning(f"[Auto] Blad backupu: {e}")
    threading.Thread(target=hourly_backup, daemon=True).start()
    log("Auto-backup co godzine: WLACZONY")

    log("Serwer startuje: http://0.0.0.0:5000")
    print("="*60)

    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

# ============================================================
# SZTUKI - per-unit tracking
# ============================================================
