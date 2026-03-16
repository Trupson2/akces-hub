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
app.config['DATABASE'] = os.environ.get('DATABASE_PATH', 'akces_hub.db')
app.config['VERSION'] = VERSION

# Session cookie security
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# SESSION_COOKIE_SECURE = True only when behind HTTPS (ngrok)
if os.environ.get('FLASK_HTTPS') or os.environ.get('NGROK_DOMAIN'):
    app.config['SESSION_COOKIE_SECURE'] = True

# Loguj WSZYSTKIE błędy 500 do konsoli (Flask domyślnie je ukrywa w non-debug)
import logging
logging.basicConfig(level=logging.ERROR)
app.logger.setLevel(logging.ERROR)

@app.errorhandler(500)
def handle_500(e):
    import traceback
    from modules.logger import log_error
    tb = traceback.format_exc()
    log_error(f"500 error: {e}\n{tb}")
    from flask import request as _req, jsonify as _jf
    if _req.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return _jf({'success': False, 'message': f'Server error: {e}'}), 500
    return "<h1>500 Internal Server Error</h1><p>Wystapil blad serwera. Szczegoly zostaly zapisane w logach.</p>", 500

@app.errorhandler(404)
def handle_404(e):
    from modules.logger import log_warning
    from flask import request as _req
    log_warning(f"404: {_req.method} {_req.path}")
    return "<h1>404</h1><p>Strona nie znaleziona.</p>", 404

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

    # Security headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'

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

try:
    from modules.remote_support import support_bp
    app.register_blueprint(support_bp)
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

# Extracted route blueprints
from modules.sprzedaze import sprzedaze_bp
app.register_blueprint(sprzedaze_bp)

from modules.wysylki import wysylki_bp
app.register_blueprint(wysylki_bp)

from modules.analityka import analityka_bp
app.register_blueprint(analityka_bp)

from modules.ustawienia import ustawienia_bp
app.register_blueprint(ustawienia_bp)

from modules.warehouse import warehouse_bp
app.register_blueprint(warehouse_bp)

from modules.palety import palety_bp
app.register_blueprint(palety_bp)

# Mail Import blueprint (auto-import palet z emaili)
try:
    from modules.mail_import import mail_import_bp, init_mail_import_db
    app.register_blueprint(mail_import_bp)
    with app.app_context():
        init_mail_import_db()
except Exception as e:
    print(f"⚠️ Mail Import module not loaded: {e}")

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

# auto_kategoryzuj i KATEGORIE_DISPLAY przeniesione do modules/shared.py
from modules.shared import auto_kategoryzuj, KATEGORIE_DISPLAY  # noqa: E402


# (auto_kategoryzuj + KATEGORIE_DISPLAY usunięte — teraz w modules/shared.py)


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

    # Statystyki COGS do dashboardu
    monthly_stats = {
        'przychod': f"{miesiac_kwota:.0f}",
        'cogs': f"{stats.get('cogs_miesiac', 0):.0f}",
        'koszt_palet': f"{stats.get('koszt_palet_msc', 0):.0f}",
        'prowizja': f"{miesiac_kwota * 0.11:.0f}",
        'zysk': f"{stats.get('zysk_miesiac', 0):.0f}",
        'roi': f"{stats.get('roi_miesiac', 0):.0f}",
        'zwroty_cnt': stats.get('zwroty_miesiac_cnt', 0),
        'zwroty_suma': f"{stats.get('zwroty_miesiac_suma', 0):.0f}",
        'magazyn_wartosc': f"{stats.get('magazyn_wartosc', 0):.0f}",
        'magazyn_sztuki': stats.get('magazyn_sztuki', 0),
        'stojace': stats.get('stojace_30dni', 0),
    }

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
        monthly=monthly_stats,
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

@app.route('/monitor')
def monitor_page():
    """Strona monitora okazji palet"""
    from modules.pallet_monitor import get_recent_deals, get_deal_stats, get_keywords, get_monitor_costs
    from modules.database import get_config
    stats = get_deal_stats()
    costs = get_monitor_costs()
    deals = get_recent_deals(limit=100)
    keywords = get_keywords()
    warrington_on = get_config('monitor_warrington_enabled', '1') == '1'
    jobalots_on = get_config('monitor_jobalots_enabled', '1') == '1'

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
        <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;align-items:center">
            <form method="POST" action="/monitor/toggle-source" style="margin:0">
                <input type="hidden" name="source" value="warrington">
                <button type="submit" style="padding:6px 14px;border-radius:8px;border:1px solid {'#22c55e' if warrington_on else '#ef4444'};background:{'rgba(34,197,94,0.1)' if warrington_on else 'rgba(239,68,68,0.1)'};color:{'#22c55e' if warrington_on else '#ef4444'};font-size:12px;font-weight:600;cursor:pointer">
                    🏪 Warrington: {'✅ ON' if warrington_on else '❌ OFF'}
                </button>
            </form>
            <form method="POST" action="/monitor/toggle-source" style="margin:0">
                <input type="hidden" name="source" value="jobalots">
                <button type="submit" style="padding:6px 14px;border-radius:8px;border:1px solid {'#22c55e' if jobalots_on else '#ef4444'};background:{'rgba(34,197,94,0.1)' if jobalots_on else 'rgba(239,68,68,0.1)'};color:{'#22c55e' if jobalots_on else '#ef4444'};font-size:12px;font-weight:600;cursor:pointer">
                    🎪 Jobalots: {'✅ ON' if jobalots_on else '❌ OFF'}
                </button>
            </form>
        </div>
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

@app.route('/monitor/toggle-source', methods=['POST'])
def monitor_toggle_source():
    """Włącz/wyłącz źródło skanowania (Warrington/Jobalots)"""
    from modules.database import get_config, set_config
    source = request.form.get('source', '')
    if source in ('warrington', 'jobalots'):
        key = f'monitor_{source}_enabled'
        current = get_config(key, '1')
        new_val = '0' if current == '1' else '1'
        set_config(key, new_val)
        state = 'włączony' if new_val == '1' else 'wyłączony'
        return redirect(f'/monitor?msg={source.title()}+{state}')
    return redirect('/monitor')

@app.route('/system/update', methods=['POST'])
def system_update():
    """Git pull + restart serwisu z poziomu apki"""
    import subprocess
    try:
        # Git pull
        result = subprocess.run(
            ['git', 'pull'], capture_output=True, text=True, timeout=30,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        pull_output = result.stdout.strip()
        if result.returncode != 0:
            return jsonify({'ok': False, 'error': f'Git pull failed: {result.stderr[:200]}'})

        if 'Already up to date' in pull_output:
            return jsonify({'ok': True, 'msg': 'Już aktualne, bez zmian'})

        # Restart serwisu z opóźnieniem 2s (żeby response zdążył dojść)
        import threading
        def _delayed_restart():
            import time
            time.sleep(2)
            subprocess.Popen(['sudo', 'systemctl', 'restart', 'akceshub'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        threading.Thread(target=_delayed_restart, daemon=True).start()
        return jsonify({'ok': True, 'msg': f'Zaktualizowano! {pull_output[:100]}. Restart za 2s...'})
    except subprocess.TimeoutExpired:
        return jsonify({'ok': False, 'error': 'Timeout — sprawdź połączenie z internetem'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]})

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

# ============================================================
# EXTRAKTOR ALLEGRO - BATCH GENERATION
# ============================================================

# ============================================================
# EXTRAKTOR ALLEGRO - UI
# ============================================================

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

# ============================================================
# SPRZEDAŻE I ZWROTY
# ============================================================

# ==================== DOPASOWYWANIE SPRZEDAZY ====================

# ============================================================
# SYSTEM WYSYŁEK - CHECKBOXY I BULK ACTIONS
# ============================================================

# ============================================================
# STATYSTYKI DASHBOARD (zunifikowany panel)
# ============================================================

# ============================================================
# ZARZĄDZANIE PALETAMI
# ============================================================

# ═══════════════════════════════════════════════════════════════════════════
# BULK IMPORT - WIELE PALET NARAZ
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# MASOWA EDYCJA PALET - Adrian's custom feature v3.1.0
# ═══════════════════════════════════════════════════════════════════════════

# ============================================================
# WAREHOUSE HEATMAP - 3D VISUALIZATION
# ============================================================

# WAREHOUSE EDITOR ROUTES

# WAREHOUSE HEATMAP ROUTES

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

# ═══════════════════════════════════════════════════════════════════════════
# ROUTE: BILANS PALET - ROI per paleta
# ═══════════════════════════════════════════════════════════════════════════

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

# Słownik statusów zadań Perplexity

# ═══════════════════════════════════════════════════════════════════════════
# ROUTE: Ustawienia drukowania
# ═══════════════════════════════════════════════════════════════════════════

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
    if session.get('rola') != 'admin':
        return 'Brak uprawnień', 403
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
    if session.get('rola') != 'admin':
        return 'Brak uprawnień', 403
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
    if session.get('rola') != 'admin':
        return 'Brak uprawnień', 403
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

    # Start mail import scheduler (auto-import palet z emaili)
    try:
        from modules.mail_import import start_mail_import_scheduler
        start_mail_import_scheduler()
        log("Mail import scheduler uruchomiony")
    except Exception as e:
        log_warning(f"Mail import scheduler error: {e}")
    
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
