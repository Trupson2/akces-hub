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
    print(f"[ERR] Brakujące moduły: {', '.join(_missing)}")
    print(f"   Zainstaluj: pip install -r requirements.txt")
    sys.exit(1)

import threading
import time
from html import escape as _html_escape  # Security: XSS prevention
from datetime import datetime, timedelta
import json

from flask import Flask, render_template, render_template_string, request, redirect, jsonify, Response, send_from_directory, make_response, flash, url_for, session, g
from flask_cors import CORS  # ← DODANO DLA NGROK!
from flask_wtf.csrf import CSRFProtect, generate_csrf

# Importy modułów
from modules.database import init_db, get_db, get_config_cached, get_config, set_config
from modules.magazynier import magazynier_bp, get_stats as mag_stats
from modules.serwisant import serwisant_bp
from modules.paletomat import paletomat_bp, get_stats as pal_stats
from modules.telegram_bot import telegram_bp, send_telegram, bot_status, start_bot, stop_bot
from modules.allegro_api import allegro_bp
from modules.logger import log, log_error, log_warning
from modules.auth import auth_bp, setup_auth, require_admin, require_login
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
        print("[OK] Klucz Gemini załadowany z gemini_config.py")
    except:
        GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
        if not GEMINI_API_KEY:
            print("[WARN]  Nie znaleziono gemini_config.py - sprawdzam zmienną środowiskową")
    
    # ALBO HARDCODE TUTAJ (odkomentuj i wklej klucz):
    # GEMINI_API_KEY = 'AIzaSy...'  # Twój klucz API z Google AI Studio
    
    if GEMINI_API_KEY and GEMINI_API_KEY != 'WKLEJ_TUTAJ_SWOJ_KLUCZ':
        GEMINI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)
        print("[OK] Gemini AI skonfigurowane (NOWY google.genai!) - Model: gemini-2.0-flash")
    else:
        GEMINI_CLIENT = None
        print("[WARN]  Brak GEMINI_API_KEY - Extraktor Allegro wyłączony")
except Exception as e:
    GEMINI_CLIENT = None
    print(f"[WARN]  Gemini AI niedostępne: {e}")

# ============================================================
# WERSJA I KONFIGURACJA
# ============================================================
def _is_commit_hash(s):
    """v1.0.101: walidacja czy string to git commit hash (7-40 hex chars).

    Bez tego v1.0.97 zapisywal VERSION string ('1.0.100') jako last_install_commit
    -> sidebar pokazywal mylacy 'v1.0.100+1.0.100' + banner has_update=True
    bo string-compare '1.0.100' != git_hash '07a5b7f'.
    """
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    if len(s) < 7 or len(s) > 40:
        return False
    return all(c in '0123456789abcdefABCDEF' for c in s)


def _get_version():
    """Wersja z pliku VERSION + commit hash.

    v1.0.104 (FIX): zrodlo prawdy zalezy od typu instalacji:
    - GIT install (.git istnieje): ZAWSZE git rev-parse HEAD. Po manual
      git pull to jest aktualne. last_install_commit ignorowany (mogl
      zostac stale gdy klient pull-uje recznie zamiast przez endpoint).
    - ZIP install (brak .git): last_install_commit z config (git rev-parse
      zwrocilby stary commit bo ZIP nie ma .git lub ma stary).

    Bug v1.0.97-v1.0.103: _get_version uzywal last_install_commit nawet dla
    git install -> po manual git pull sidebar pokazywal stary commit
    (np 'v1.0.103+fc9421f' gdzie fc9421f to commit v1.0.102).
    """
    ver = '1.0.0'
    try:
        vf = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'VERSION')
        if os.path.exists(vf):
            ver = open(vf).read().strip()
    except:
        pass
    _app_d = os.path.dirname(os.path.abspath(__file__))
    _is_git = os.path.isdir(os.path.join(_app_d, '.git'))
    if _is_git:
        # GIT install: git HEAD = prawda (aktualne po kazdym pull)
        try:
            import subprocess
            r = subprocess.run(['git', 'log', '-1', '--pretty=format:%h'],
                              capture_output=True, text=True, timeout=5, cwd=_app_d)
            if r.returncode == 0 and r.stdout.strip():
                ver += f'+{r.stdout.strip()}'
        except:
            pass
    else:
        # ZIP install: last_install_commit z config (jezeli valid hash)
        try:
            from modules.database import get_config
            _last_install = get_config('last_install_commit', '').strip()
            if _is_commit_hash(_last_install):
                ver += f'+{_last_install[:7]}'
        except Exception:
            pass
    return ver

VERSION = _get_version()
APP_START_TIME = time.time()

app = Flask(__name__, static_folder='static', static_url_path='/static')

# ── ProxyFix: Cloudflare Tunnel / nginx / ngrok przekazują prawdziwy IP w X-Forwarded-For.
# Bez tego request.remote_addr == '127.0.0.1' dla WSZYSTKICH userów przez tunnel,
# co łamie: rate-limiter per-IP, auto-login LAN, block_unauthenticated_external. ──
#
# v1.0.94 SECURITY (K3): ProxyFix WARUNKOWY - tylko gdy faktycznie jestesmy za
# upstream proxy. Desktop ZIP install (Windows klient bez systemd) NIE ma upstream
# proxy -> ProxyFix bezwarunkowy umozliwia spoofing X-Forwarded-For:127.0.0.1
# z LAN, omijajac block_unauthenticated_external i ujawniajac /api/key.
_is_proxied_deployment = (
    os.environ.get('FLASK_ENV') == 'production'
    or os.path.exists('/etc/systemd/system/akces-hub.service')
    or os.path.exists('/etc/systemd/system/akceshub.service')
)
if _is_proxied_deployment:
    try:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
        print("[OK] ProxyFix aktywny (systemd/production deployment)")
    except Exception as _e:
        print(f"[WARN] ProxyFix nie załadowany: {_e}")
else:
    print("[INFO] ProxyFix wylaczony (desktop ZIP install - brak upstream proxy)")

# Max rozmiar uploadu — chroni przed DoS (zapychanie RAM/dysku)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB

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

# ── Auto-generate CHANGELOG.md from git log ──
def _generate_changelog():
    """Generate CHANGELOG.md from recent git commits, grouped by date."""
    import subprocess
    try:
        _cwd = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            ['git', 'log', '--format=%ai|%s', '--no-merges', '-100'],
            capture_output=True, text=True, timeout=10, cwd=_cwd,
            # git wypisuje UTF-8; bez jawnego encoding subprocess dekoduje
            # locale-em (cp1250 na Win) -> mojibake w CHANGELOG.md. PHASE 4.
            encoding='utf-8', errors='replace'
        )
        if result.returncode != 0 or not result.stdout.strip():
            return
        lines = result.stdout.strip().split('\n')
        from collections import OrderedDict
        days = OrderedDict()
        for line in lines:
            parts = line.split('|', 1)
            if len(parts) < 2:
                continue
            date_str, msg = parts
            day = date_str[:10]
            if 'Co-Authored' in msg or msg.startswith('Merge'):
                continue
            if day not in days:
                days[day] = []
            days[day].append(msg)
        if not days:
            return
        cl_path = os.path.join(_cwd, 'CHANGELOG.md')
        with open(cl_path, 'w', encoding='utf-8') as f:
            f.write('# Historia zmian (auto-generated)\n\n')
            for day, commits in days.items():
                dp = day.split('-')
                formatted = f"{dp[2]}.{dp[1]}.{dp[0]}" if len(dp) == 3 else day
                f.write(f'## {formatted}\n\n')
                for c in commits:
                    f.write(f'- {c}\n')
                f.write('\n')
    except Exception:
        pass

# v1.0.99 FIX: NIE regeneruj CHANGELOG.md automatycznie przy starcie.
# CHANGELOG.md jest w git repo (vendor edytuje recznie przy kazdym push),
# auto-regeneracja u klienta nadpisuje go, a potem 'git pull' pada bo
# 'local changes would be overwritten by merge'. Adrian dostal ten blad
# przy manualnym update na Pi.
#
# Funkcja _generate_changelog() zostaje dostepna jako utility (np. dla
# vendora przed push), ale NIE jest wywolywana automatycznie.
# import threading as _threading_init
# _threading_init.Thread(target=_generate_changelog, daemon=True).start()

# ── Asynchroniczny git fetch + porownanie z origin (uzywany przez /dashboard) ──
def _git_update_check_async():
    """Background: git fetch + porownaj HEAD z origin, zapisz w cache.
    Nigdy nie blokuje route'a — odpalany w threadzie."""
    try:
        import subprocess as _sp
        from modules.database import get_config, set_config
        _app_dir = os.path.dirname(os.path.abspath(__file__))
        if not os.path.isdir(os.path.join(_app_dir, '.git')):
            return
        _cur_branch = _sp.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                              capture_output=True, text=True, timeout=5,
                              cwd=_app_dir).stdout.strip() or 'main'
        _sp.run(['git', 'fetch', 'origin', _cur_branch, '--quiet'],
                capture_output=True, timeout=15, cwd=_app_dir)
        # v1.0.104: ta funkcja leci TYLKO dla git install (early return wyzej
        # gdy brak .git). Dla git install git HEAD = prawda po kazdym pull.
        # Usuniety last_install_commit override (v1.0.101) ktory powodowal
        # ze manual git pull nie aktualizowal local -> baner wisial mimo
        # ze HEAD == origin.
        local = _sp.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=5,
                        cwd=_app_dir).stdout.strip()
        remote = _sp.run(['git', 'rev-parse', f'origin/{_cur_branch}'], capture_output=True, text=True, timeout=5,
                         cwd=_app_dir).stdout.strip()
        # Porownanie: oba pelne SHA (40)
        has_update = bool(local) and bool(remote) and local != remote
        remote_msg = ''
        remote_hash = ''
        if has_update:
            r = _sp.run(['git', 'log', f'HEAD..origin/{_cur_branch}', '--pretty=format:%h - %s', '--reverse'],
                        capture_output=True, text=True, timeout=5, cwd=_app_dir)
            remote_msg = r.stdout.strip()[:300] if r.returncode == 0 else ''
            rh = _sp.run(['git', 'rev-parse', '--short', f'origin/{_cur_branch}'],
                         capture_output=True, text=True, timeout=5, cwd=_app_dir)
            remote_hash = rh.stdout.strip() if rh.returncode == 0 else ''
        cache_raw = get_config('update_check_cache', '')
        try:
            old_cache = json.loads(cache_raw) if cache_raw else {}
        except Exception:
            old_cache = {}
        cache = {
            'checked_at': time.time(),
            'has_update': has_update,
            'remote_msg': remote_msg,
            'remote_hash': remote_hash,
            'local_hash': local[:7],
            'notified': old_cache.get('notified', False) if has_update else False,
        }
        set_config('update_check_cache', json.dumps(cache))

        # FIX 2026-05-28: ustaw 'update_available' (uzywane przez fioletowy
        # banner w base.html). Wczesniej config NIGDY nie byl ustawiany,
        # wiec banner nigdy sie nie pokazywal mimo ze update byl dostepny.
        set_config('update_available', '1' if has_update else '0')
        # TG notify (raz)
        if has_update and not cache.get('notified'):
            try:
                bot_token = get_config('telegram_bot_token', '')
                chat_id = get_config('telegram_chat_id', '')
                if bot_token and chat_id:
                    import requests as _req
                    text = (
                        f"Dostepna aktualizacja!\n\n"
                        f"Nowa wersja: {remote_hash}\n"
                        f"Zmiany:\n{remote_msg[:200]}\n\n"
                        f"Wejdz na dashboard i kliknij Aktualizuj"
                    )
                    _req.post(
                        f'https://api.telegram.org/bot{bot_token}/sendMessage',
                        json={'chat_id': chat_id, 'text': text},
                        timeout=5
                    )
                    cache['notified'] = True
                    set_config('update_check_cache', json.dumps(cache))
            except Exception:
                pass
    except Exception:
        pass
    finally:
        try:
            home._git_check_running = False
        except Exception:
            pass


def _public_update_check_async():
    """Background: sprawdz wersje na PUBLIC repo (dla ZIP install jak Macek).

    v1.0.89: bez tego klienci ZIP nigdy nie widzieli banera 'Dostepna aktualizacja'.
    _git_update_check_async wychodzil early na if not .git folder.
    """
    try:
        from modules.zip_updater import check_public_version
        from modules.database import get_config, set_config
        info = check_public_version(repo='Trupson2/akces-hub', branch='main', timeout=10)
        has_update = bool(info.get('available', False))
        latest = info.get('latest', '') or ''
        current = info.get('current', '') or ''
        err = info.get('error', '')
        cache_raw = get_config('update_check_cache', '')
        try:
            old_cache = json.loads(cache_raw) if cache_raw else {}
        except Exception:
            old_cache = {}
        cache = {
            'checked_at': time.time(),
            'has_update': has_update,
            'remote_msg': (f'v{latest} dostepna' if has_update else f'aktualny v{current}') if not err else f'check error: {err[:60]}',
            'remote_hash': latest,
            'local_hash': current,
            'notified': old_cache.get('notified', False) if has_update else False,
            'is_zip': True,
        }
        set_config('update_check_cache', json.dumps(cache))
        set_config('update_available', '1' if has_update else '0')
        # TG notify (raz) gdy nowa wersja
        if has_update and not cache.get('notified'):
            try:
                bot_token = get_config('telegram_bot_token', '')
                chat_id = get_config('telegram_chat_id', '')
                if bot_token and chat_id:
                    import requests as _req
                    text = (
                        f"Dostepna aktualizacja!\n\n"
                        f"Nowa wersja: {latest}\n"
                        f"Twoja: {current}\n\n"
                        f"Wejdz na dashboard i kliknij Aktualizuj"
                    )
                    _req.post(
                        f'https://api.telegram.org/bot{bot_token}/sendMessage',
                        json={'chat_id': chat_id, 'text': text},
                        timeout=5
                    )
                    cache['notified'] = True
                    set_config('update_check_cache', json.dumps(cache))
            except Exception:
                pass
    except Exception as e:
        print(f"[update] public check error: {e}")
    finally:
        try:
            home._git_check_running = False  # ten sam flag co git check (mutex)
        except Exception:
            pass


# Session cookie security
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_NAME'] = 'akces_session'
# SESSION_COOKIE_SECURE: HTTPS-only cookies. Włączone w produkcji (Pi + systemd)
# oraz gdy działamy za Cloudflare Tunnel / ngrok (X-Forwarded-Proto=https).
# Dynamiczne wykrywanie HTTPS per-request robi before_request _enforce_secure_cookie.
app.config['SESSION_COOKIE_SECURE'] = (
    os.environ.get('FLASK_ENV') == 'production'
    or os.path.exists('/etc/systemd/system/akces-hub.service')
    or os.path.exists('/etc/systemd/system/akceshub.service')
)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)  # Sesja wygasa po 12h

# CSRF protection
csrf = CSRFProtect(app)
app.config['WTF_CSRF_CHECK_DEFAULT'] = False  # Wyłącz domyślnie, włącz per-route

# Rate limiting — ochrona przed brute-force i DDoS
def _real_ip():
    """Prawdziwy IP klienta — Cloudflare/ngrok/nginx -> ProxyFix X-Forwarded-For.
    Fallback chain: CF-Connecting-IP (Cloudflare) -> X-Real-IP (nginx) -> remote_addr."""
    cf = request.headers.get('CF-Connecting-IP', '').strip()
    if cf:
        return cf
    xr = request.headers.get('X-Real-IP', '').strip()
    if xr:
        return xr
    # ProxyFix już zaaplikowany — request.remote_addr zawiera X-Forwarded-For[0]
    return request.remote_addr or '127.0.0.1'

try:
    from flask_limiter import Limiter
    limiter = Limiter(
        _real_ip,
        app=app,
        default_limits=["200 per minute"],  # Globalny limit
        storage_uri="memory://",
    )
    # Granularne limity per-endpoint sa aplikowane ponizej w register_endpoint_limits()
    # (auth.login 5/min, first_setup 3/min, zmien_haslo 5/min, user_add 10/min,
    # API writes 30/min, api_show_key 5/min) — bardziej precyzyjne niz per-blueprint
    print("[OK] Rate limiter aktywny (200/min global, per real IP)")
except ImportError:
    limiter = None
    print("[WARN] flask-limiter nie zainstalowany — brak rate limitingu")

# Jinja2 filters
@app.template_filter('parse_json')
def parse_json_filter(value):
    """Parse JSON string in Jinja2 templates"""
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None

@app.after_request
def add_ngrok_headers(response):
    """Wymuś pomijanie ngrok interstitial + no-cache na SW"""
    if request.path == '/static/sw.js':
        response.headers['Cache-Control'] = 'no-cache, must-revalidate'
        response.headers['Service-Worker-Allowed'] = '/'
    # Cache fonts for 1 year
    if request.path.endswith(('.woff2', '.ttf', '.woff')):
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return response

@app.route('/manifest.json')
def serve_manifest():
    """PWA manifest — Chrome install prompt wymaga URL-i ikon (nie data: URI)"""
    manifest = {
        "name": "Akces Hub",
        "short_name": "Akces",
        "id": "/",
        "description": "Zarządzanie paletami i sprzedażą Allegro",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0e0e10",
        "theme_color": "#0e0e10",
        "orientation": "portrait-primary",
        "prefer_related_applications": False,
        "icons": [
            {"src": "/static/icon-192.png?v=2", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/icon-192.png?v=2", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": "/static/icon-512.png?v=2", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/icon-512.png?v=2", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}
        ],
        "categories": ["business", "productivity"],
        "lang": "pl-PL"
    }
    resp = jsonify(manifest)
    resp.headers['Content-Type'] = 'application/manifest+json'
    return resp

@app.route('/sw.js')
def serve_sw():
    """Serve Service Worker from root scope for proper PWA install"""
    resp = send_from_directory(app.static_folder, 'sw.js')
    resp.headers['Content-Type'] = 'application/javascript'
    resp.headers['Cache-Control'] = 'no-cache, must-revalidate'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp

@app.route('/api/csrf-token')
def api_csrf_token():
    """Endpoint do odnawiania CSRF tokena — używany przez auto-refresh JS"""
    return jsonify({'csrf_token': generate_csrf()})

@app.before_request
def block_unauthenticated_external():
    """Blokuj requesty z zewnątrz (ngrok) bez zalogowanej sesji.
    Lokalne requesty (127.0.0.1, 192.168.*) przechodzą bez sesji do strony logowania."""
    # Pozwól na statyczne pliki, login, API system-stats
    # /api/v1/* uzywa X-API-Key auth — nie wymaga sesji, wiec nie blokujemy
    # /manifest.json + /sw.js MUSZA byc publiczne — bez tego PWA install nie dziala
    # (browser pobiera je przed zalogowaniem; redirect na login psuje rejestracje SW)
    safe_paths = ('/auth', '/static', '/favicon', '/api/system-stats', '/api/csrf-token', '/launcher',
                  '/license', '/setup', '/eula', '/onboarding', '/subscription-expired', '/launcher',
                  '/api/v1/', '/manifest.json', '/sw.js')
    if any(request.path.startswith(p) for p in safe_paths):
        return

    # Zalogowany użytkownik — przepuść zawsze
    # NOTE: auth.py sets session['user_id'], NOT session['user']
    if session.get('user_id'):
        return

    # Niezalogowany — sprawdź czy lokalne czy zewnętrzne
    # CRITICAL: jeśli są headery proxy (CF/ngrok/nginx), to request nie jest lokalny
    # nawet jeśli remote_addr wygląda jak 127.0.0.1 / 192.168.*
    remote_ip = request.remote_addr or ''
    is_proxied = bool(
        request.headers.get('X-Forwarded-For')
        or request.headers.get('X-Real-IP')
        or request.headers.get('CF-Connecting-IP')
        or request.headers.get('CF-Ray')
        or request.headers.get('ngrok-trace-id')
    )
    is_local = (
        remote_ip in ('127.0.0.1', '::1')
        or remote_ip.startswith('192.168.')
        or remote_ip.startswith('10.')
    ) and not is_proxied

    # Lokalne bez sesji — przepuść (Pi w LAN)
    if is_local:
        return

    # Zewnętrzne bez sesji — zablokuj (ngrok)
    if request.path == '/auth/login' and request.method == 'GET':
        return
    if request.method == 'GET':
        return redirect('/auth/login')
    return jsonify({'error': 'Unauthorized — zaloguj się'}), 401

@app.before_request
def csrf_protect_forms():
    """CSRF dla formularzy HTML i JSON fetch.
    - Form POST od zalogowanego: wymaga csrf_token w formularzu
    - JSON POST od zalogowanego: wymaga X-CSRFToken header
    - Niezalogowany / API webhooks: skip (chronione przez auth middleware)
    """
    if request.method not in ('POST', 'PUT', 'DELETE', 'PATCH'):
        return
    # Login/setup nie wymaga CSRF — sesja może być wygasła
    if request.path in ('/auth/login', '/auth/setup', '/setup'):
        return
    # Tylko zalogowani użytkownicy muszą mieć CSRF (zewnętrzne API webhooks nie mają sesji)
    # NOTE: auth.py sets session['user_id'], NOT session['user']
    if not session.get('user_id'):
        return
    ct = request.content_type or ''
    # Wyłączenia CSRF: TYLKO webhooks z własną weryfikacją HMAC podpisu.
    # /system/* WYMAGA CSRF (wcześniej każdy zalogowany user mógł trigger git pull + restart).
    # /api/* NIE jest exempt — używa X-CSRFToken z sesji (chroni przed CSRF z obcych domen).
    _csrf_exempt = (
        '/allegro/callback',      # OAuth redirect z Allegro (signed state param)
        '/allegro/webhook',       # HMAC validated w handlerze
        '/telegram/webhook',      # secret_token validated w handlerze
        '/api/v1/',               # Public REST API — auth przez X-API-Key zamiast sesji
        '/api/license/verify',    # Heartbeat licencji od klientow - bez sesji
        '/api/vendor-notify',     # v1.0.95 W1: notify proxy od klientow - license_key auth
    )
    if any(request.path.startswith(p) for p in _csrf_exempt):
        return

    if 'application/json' in ct:
        # Fetch/AJAX — sprawdź X-CSRFToken header (Referer check jako fallback)
        token = request.headers.get('X-CSRFToken') or request.headers.get('X-CSRF-Token')
        referer = request.headers.get('Referer', '')
        host = request.host_url
        # Przepuść jeśli referer z naszej domeny (same-origin) LUB ma token
        # Uwzględnij ngrok/proxy — referer może być HTTPS a host HTTP
        _referer_ok = referer.startswith(host) or referer.startswith(host.replace('http://', 'https://'))
        # Ngrok: referer z *.ngrok-free.dev jest OK jeśli request przyszedł przez proxy
        if not _referer_ok and '.ngrok' in referer and request.headers.get('X-Forwarded-For'):
            _referer_ok = True
        if not token and not _referer_ok:
            from flask import abort
            abort(403, 'CSRF: brak tokena i nieprawidłowy referer')
        if token:
            try:
                from flask_wtf.csrf import validate_csrf
                validate_csrf(token)
            except Exception:
                from flask import abort
                abort(400, 'CSRF token nieprawidłowy. Odśwież stronę.')
    else:
        # Formularz HTML — wymagaj csrf_token LUB same-origin Referer
        # Test mode: testy maja WTF_CSRF_ENABLED=False, wiec pomijamy enforcement
        if os.environ.get('AKCES_TEST_MODE') == '1':
            return
        if request.form.get('csrf_token'):
            try:
                csrf.protect()
            except Exception:
                from flask_wtf.csrf import generate_csrf
                generate_csrf()
                from flask import abort
                abort(400, 'CSRF token wygasł. Odśwież stronę.')
        else:
            # Brak tokena w body — wymagaj same-origin Referer (zabezpiecza przed
            # cross-origin form POST bez JS, ktory wczesniej przechodzil bez sprawdzenia)
            referer = request.headers.get('Referer', '')
            host = request.host_url
            _referer_ok = referer.startswith(host) or referer.startswith(host.replace('http://', 'https://'))
            # Ngrok: referer z *.ngrok-free.dev jest OK jesli przyszedl przez proxy
            if not _referer_ok and '.ngrok' in referer and request.headers.get('X-Forwarded-For'):
                _referer_ok = True
            if not _referer_ok:
                from flask import abort
                abort(403, 'CSRF: form POST bez tokena i nie same-origin')

# ============================================================
# WEBHOOK SIGNATURE VALIDATION — Telegram & Allegro
# ============================================================
@app.before_request
def validate_webhook_signatures():
    """Validate webhook requests using HMAC signatures.
    - Telegram: X-Telegram-Bot-Api-Secret-Token header (set during setWebhook)
    - Allegro: X-Allegro-Webhook-Secret header (shared secret)
    """
    if request.method != 'POST':
        return
    import hmac as _hmac
    import hashlib as _hl

    # Telegram webhook validation
    if request.path.startswith('/telegram/webhook'):
        try:
            from modules.database import get_config_cached
            bot_token = get_config_cached('telegram_bot_token', '')
            if bot_token:
                # Telegram sends X-Telegram-Bot-Api-Secret-Token if set during setWebhook
                # We use a HMAC-SHA256 of the bot token as the expected secret
                expected_secret = _hmac.new(
                    b'telegram-webhook-secret', bot_token.encode(), _hl.sha256
                ).hexdigest()[:32]
                received_secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
                if not received_secret or not _hmac.compare_digest(received_secret, expected_secret):
                    from modules.logger import log_warning
                    log_warning(f"Telegram webhook: missing/invalid signature from {request.remote_addr}")
                    return jsonify({'error': 'Invalid webhook signature'}), 403
        except Exception:
            pass  # Don't block webhook if validation setup fails

    # Allegro webhook validation
    if request.path.startswith('/allegro/webhook') or request.path.startswith('/allegro/callback'):
        try:
            from modules.database import get_config_cached
            webhook_secret = get_config_cached('allegro_webhook_secret', '')
            if webhook_secret:
                received_sig = request.headers.get('X-Allegro-Webhook-Secret', '')
                if not received_sig or not _hmac.compare_digest(received_sig, webhook_secret):
                    from modules.logger import log_warning
                    log_warning(f"Allegro webhook: missing/invalid signature from {request.remote_addr}")
                    return jsonify({'error': 'Invalid webhook signature'}), 403
        except Exception:
            pass  # Don't block if config unavailable

# Sprawdzanie licencji
@app.before_request
def check_license_middleware():
    """Blokuj dostęp bez aktywnej licencji (oprócz setup, login, aktywacji)"""
    # Test mode: pomijamy sprawdzanie licencji (pytest)
    if os.environ.get('AKCES_TEST_MODE') == '1':
        return
    allowed = ('/setup', '/auth', '/static', '/api/system-stats', '/api/csrf-token', '/license', '/favicon', '/api/license/verify', '/subscription-expired', '/time-manipulation', '/eula', '/onboarding', '/launcher', '/api/v1/', '/manifest.json', '/sw.js')
    if any(request.path.startswith(p) for p in allowed):
        return
    if request.path == '/':
        return  # Home sam sprawdzi
    try:
        # Sprawdź czy licencja nie została zablokowana przez heartbeat
        from modules.database import get_config_cached
        if get_config_cached('license_blocked', '0') == '1':
            return redirect('/license?blocked=1')

        from modules.license import check_license, is_subscription_expired, check_time_manipulation, get_license_info
        is_valid, plan, msg = check_license()
        if not is_valid:
            # Sprawdź czy to wygaśnięcie subskrypcji
            if is_subscription_expired():
                return redirect('/subscription-expired')
            return redirect('/license')

        # Sprawdź manipulację czasem
        time_ok, time_msg = check_time_manipulation()
        if not time_ok:
            return redirect('/time-manipulation')
    except ImportError:
        pass  # Brak modułu license = dev mode
    except Exception:
        pass  # DB error, license corrupt — przepuść, nie blokuj

# Sprawdzanie EULA
@app.before_request
def check_eula_middleware():
    """Po walidacji licencji sprawdz czy EULA zaakceptowane"""
    if os.environ.get('AKCES_TEST_MODE') == '1':
        return
    allowed = ('/eula', '/license', '/auth', '/static', '/setup', '/favicon', '/api/system-stats', '/api/license/verify', '/subscription-expired', '/time-manipulation', '/launcher', '/api/v1/', '/manifest.json', '/sw.js')
    if any(request.path.startswith(p) for p in allowed):
        return
    try:
        from modules.eula import is_eula_accepted
        if not is_eula_accepted():
            return redirect('/eula')
    except ImportError:
        pass

# Sprawdzanie onboardingu (po EULA)
@app.before_request
def check_onboarding_middleware():
    """Po akceptacji EULA sprawdz czy onboarding ukonczony"""
    if os.environ.get('AKCES_TEST_MODE') == '1':
        return
    allowed = ('/onboarding', '/eula', '/license', '/auth', '/static', '/setup', '/favicon', '/api/system-stats', '/api/license/verify', '/subscription-expired', '/time-manipulation', '/launcher', '/api/v1/', '/manifest.json', '/sw.js')
    if any(request.path.startswith(p) for p in allowed):
        return
    try:
        from modules.onboarding import is_onboarding_completed
        if not is_onboarding_completed():
            return redirect('/onboarding')
    except ImportError:
        pass

# Sprawdzanie planu licencyjnego (ograniczenia funkcji per plan)
@app.before_request
def check_plan_features():
    """Blokuj dostęp do funkcji wymagających wyższego planu."""
    allowed = ('/auth', '/static', '/license', '/setup', '/favicon', '/api/', '/eula', '/onboarding', '/subscription-expired', '/time-manipulation', '/system/', '/cennik', '/manifest.json', '/sw.js')
    if any(request.path.startswith(p) for p in allowed):
        return
    if request.path == '/':
        return
    try:
        from modules.plan_features import has_feature_access, get_required_plan_display, PLAN_DISPLAY, get_current_plan
        if not has_feature_access(request.path):
            required = get_required_plan_display(request.path)
            current = PLAN_DISPLAY.get(get_current_plan(), 'TRIAL')
            return render_template('plan_upgrade.html', required_plan=required, current_plan=current, path=request.path), 403
    except Exception:
        pass  # Brak modułu lub błąd licencji = przepuść

# Branding — dostępny globalnie we wszystkich szablonach
@app.context_processor
def inject_branding():
    from modules.database import get_config_cached
    # Licencja
    lic_plan = 'FREE'
    lic_expires = ''
    lic_days_left = ''
    lic_days_left_num = None
    lic_expired = False
    try:
        from modules.license import get_license_display, get_days_remaining, is_subscription_expired
        lic = get_license_display()
        if lic.get('active'):
            lic_plan = (lic.get('plan', 'free')).upper()
            lic_expires = lic.get('expires', '')
            days_rem = get_days_remaining()
            if days_rem is not None:
                lic_days_left_num = days_rem
                if days_rem >= 0:
                    lic_days_left = f'{days_rem} dni'
                else:
                    lic_days_left = 'Wygasla!'
                    lic_expired = True
            # Bezterminowa — brak dni
        else:
            lic_expired = is_subscription_expired()
            if lic_expired:
                lic_days_left = 'Wygasla!'
                days_rem = get_days_remaining()
                if days_rem is not None:
                    lic_days_left_num = days_rem
    except Exception:
        pass
    try:
        _bn = get_config_cached('brand_name', 'AKCES HUB')
        _bc = get_config_cached('brand_color', '#6366f1')
        _bl = get_config_cached('brand_logo', '')
        _ua = get_config_cached('update_available', '0')
    except Exception:
        _bn, _bc, _bl, _ua = 'AKCES HUB', '#6366f1', '', '0'
    # Model Gemini
    try:
        _gm = get_config_cached('gemini_model', 'gemini-2.5-flash')
        _gm_short = _gm.replace('gemini-', '').replace('-preview', ' <span class=material-symbols-outlined>warning</span>')
    except:
        _gm_short = '2.5-flash'
    return {
        'brand_name': _bn,
        'brand_color': _bc,
        'brand_logo': _bl,
        'version': VERSION,
        'update_available': _ua,
        'lic_plan': lic_plan,
        'lic_expires': lic_expires,
        'lic_days_left': lic_days_left,
        'lic_days_left_num': lic_days_left_num,
        'lic_expired': lic_expired,
        'gemini_model_short': _gm_short,
    }

# Loguj WSZYSTKIE błędy 500 do konsoli (Flask domyślnie je ukrywa w non-debug)
import logging
logging.basicConfig(level=logging.ERROR)
app.logger.setLevel(logging.ERROR)

@app.errorhandler(500)
def handle_500(e):
    """500 handler — log full details server-side, return generic message to client."""
    import traceback
    from modules.logger import log_error
    from modules.utils import safe_error_message
    tb = traceback.format_exc()
    # Full details server-side only
    log_error(f"500 error on {request.method} {request.path}: {e}\n{tb}")
    # Generic message to client — never leak internals
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or (request.content_type and 'application/json' in request.content_type):
        return jsonify({'success': False, 'message': 'Internal server error'}), 500
    return render_template_string('''<!DOCTYPE html>
<html lang="pl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>500 - Błąd serwera</title>
<style>body{font-family:system-ui,-apple-system,sans-serif;background:#0e0e10;color:#e5e7eb;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{text-align:center;max-width:420px;padding:2rem}.code{font-size:5rem;font-weight:700;color:#6366f1;margin:0}.msg{color:#9ca3af;margin:1rem 0}
a{color:#6366f1;text-decoration:none}a:hover{text-decoration:underline}</style></head>
<body><div class="box"><p class="code">500</p><p class="msg">Wystąpił błąd serwera. Szczegóły zostały zapisane w logach.</p>
<a href="/dashboard">Powrót do Dashboard</a></div></body></html>'''), 500

@app.errorhandler(404)
def handle_404(e):
    """404 handler — clean page, log the miss."""
    from modules.logger import log_warning
    log_warning(f"404: {request.method} {request.path}")
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or (request.content_type and 'application/json' in request.content_type):
        return jsonify({'success': False, 'message': 'Nie znaleziono'}), 404
    return render_template_string('''<!DOCTYPE html>
<html lang="pl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>404 - Nie znaleziono</title>
<style>body{font-family:system-ui,-apple-system,sans-serif;background:#0e0e10;color:#e5e7eb;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{text-align:center;max-width:420px;padding:2rem}.code{font-size:5rem;font-weight:700;color:#6366f1;margin:0}.msg{color:#9ca3af;margin:1rem 0}
a{color:#6366f1;text-decoration:none}a:hover{text-decoration:underline}</style></head>
<body><div class="box"><p class="code">404</p><p class="msg">Strona nie znaleziona.</p>
<a href="/dashboard">Powrót do Dashboard</a></div></body></html>'''), 404

@app.errorhandler(403)
def handle_403(e):
    """403 handler — forbidden access."""
    from modules.logger import log_warning
    log_warning(f"403: {request.method} {request.path} from {request.remote_addr}")
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or (request.content_type and 'application/json' in request.content_type):
        return jsonify({'success': False, 'message': 'Brak dostępu'}), 403
    return render_template_string('''<!DOCTYPE html>
<html lang="pl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>403 - Brak dostępu</title>
<style>body{font-family:system-ui,-apple-system,sans-serif;background:#0e0e10;color:#e5e7eb;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{text-align:center;max-width:420px;padding:2rem}.code{font-size:5rem;font-weight:700;color:#ef4444;margin:0}.msg{color:#9ca3af;margin:1rem 0}
a{color:#6366f1;text-decoration:none}a:hover{text-decoration:underline}</style></head>
<body><div class="box"><p class="code">403</p><p class="msg">Brak dostępu do tej strony.</p>
<a href="/dashboard">Powrót do Dashboard</a></div></body></html>'''), 403

# ============================================================
# [OK] CORS CONFIGURATION - NGROK & REMOTE ACCESS FIX!
# ============================================================
_cors_origins = os.environ.get('CORS_ORIGINS', 'http://localhost:5000,http://127.0.0.1:5000').split(',')
CORS(app, resources={
    r"/*": {
        "origins": [o.strip() for o in _cors_origins],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "Accept", "X-CSRFToken"],
        "expose_headers": ["Content-Type", "X-Total-Count"],
        "supports_credentials": True,
        "max_age": 3600
    }
})

print("""
╔═══════════════════════════════════════════════════════════════╗
║                   CORS ENABLED!                            ║
║  Akces Hub dostępny z każdej domeny (ngrok, localhost, etc.) ║
╚═══════════════════════════════════════════════════════════════╝
""")

# Ukryj wersję serwera w HTTP response
import werkzeug.serving
werkzeug.serving.WSGIRequestHandler.server_version = "Server"
werkzeug.serving.WSGIRequestHandler.sys_version = ""

# CSP Nonce — generuj unikalne nonce per request
import secrets as _secrets

@app.before_request
def _generate_csp_nonce():
    """Generuj unikalny nonce per request (przed handlerem) i zapisz w flask.g.

    Nonce uzywany w naglowku Content-Security-Policy (after_request) oraz
    w templateach Jinja2 przez context processor inject_csp_nonce().

    UWAGA: nonce MUSI byc generowany per-request (nie cachowany), w przeciwnym
    razie traci wartosc bezpieczenstwa.
    """
    g.csp_nonce = _secrets.token_urlsafe(16)


@app.context_processor
def inject_csp_nonce():
    """Wstrzyknij nonce do wszystkich templateów Jinja2.

    Uzycie w template: <script nonce="{{ csp_nonce }}">...</script>
    Dzieki temu kiedy w Phase 3 usuniemy 'unsafe-inline' z CSP, nowe inline
    skrypty z nonce nadal beda dzialac (a obce skrypty XSS — nie).
    """
    nonce = getattr(g, 'csp_nonce', None) or _secrets.token_urlsafe(16)
    return {'csp_nonce': nonce}


@app.context_processor
def inject_sklepakces_owner():
    """Pokazuje czy obecna licencja jest na sklepakces whitelist.
    Używane w base.html do conditional rendering sidebar link."""
    try:
        return {'sklepakces_owner': _is_sklepakces_owner()}
    except Exception:
        return {'sklepakces_owner': False}

@app.after_request
def after_request(response):
    """Dodaj CORS headers + cache control + CSP nonce"""
    # CSP — nonce-based + unsafe-inline fallback (Phase 2 fundament).
    # TODO Phase 3: usunac 'unsafe-inline' i 'unsafe-eval' po migracji 577 inline
    # event handlerow (onclick/onchange/onsubmit) na addEventListener oraz
    # usunieciu eval()/Function() z legacy kodu (Chart.js itp.).
    if response.content_type and 'text/html' in response.content_type:
        # UWAGA: CSP Level 3 spec — gdy 'nonce-XXX' jest w source list, przegladarka
        # IGNORUJE 'unsafe-inline'. Wlaczenie nonce tutaj zablokuje WSZYSTKIE inline
        # <style>/<script> w templateach, poki nie dostana atrybutu nonce="{{ csp_nonce }}".
        # W kodzie mamy 577 inline event handlerow + wiele <style> tagow — Phase 4 refactor.
        # Do tego czasu: zostajemy przy unsafe-inline bez nonce w headerze.
        # Nonce infrastructure (g.csp_nonce, context processor) zostaje — gotowe pod Phase 4.
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://cdn.tailwindcss.com; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com https://cdn.tailwindcss.com; "
            "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "img-src 'self' data: blob: https:; "
            "connect-src 'self' https://generativelanguage.googleapis.com https://fonts.googleapis.com https://fonts.gstatic.com wss: ws:; "
            "worker-src 'self' blob:; "
            "frame-ancestors 'self'; "
            "base-uri 'self'; "
            "form-action 'self'; "
        )
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
        # NIE ustawiaj Connection: keep-alive — hop-by-hop headers są zakazane w WSGI (PEP 3333)
    # Cache dla statycznych plików (obrazki, CSS, JS)
    elif response.mimetype and (response.mimetype.startswith('image/') or response.mimetype in ('text/css', 'application/javascript')):
        response.headers['Cache-Control'] = 'public, max-age=86400'
        # Defense-in-depth: SVG (image/svg+xml) moze zawierac JS jesli sanitizer ominie cos
        # Wymuszamy CSP default-src 'none' zeby nawet jak SVG zawiera <script>, nie wykonal sie
        if response.mimetype == 'image/svg+xml' or request.path.lower().endswith('.svg'):
            response.headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    else:
        # Prywatne strony — nie cachuj
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'

    # Security headers (OWASP ZAP fixes)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(self), microphone=(), geolocation=(), payment=(), usb=()'
    # CSP already set above for HTML responses — don't overwrite with stricter one
    # Skip CSP for static assets (images, fonts, JS, CSS) — they don't need it
    # and it can interfere with manifest icon loading

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
app.register_blueprint(serwisant_bp, url_prefix='/serwis')
app.register_blueprint(paletomat_bp, url_prefix='/paletomat')
app.register_blueprint(telegram_bp, url_prefix='/telegram')
app.register_blueprint(allegro_bp, url_prefix='/allegro')

# Sklepakces integration — TWOJA marka (sklepakces.pl), NIE generic WC sync.
# GATED przez WHITELIST license.client (nie plan!) — nawet Enterprise klient
# bez zezwolenia NIE może pushować produktów na Twój sklep.
#
# Konfiguracja: env var AKCES_SKLEPAKCES_OWNERS (CSV nazw klientów licencji).
# Default: tylko "Adrian Gauza". Możesz dodać znajomych explicite:
#   export AKCES_SKLEPAKCES_OWNERS="Adrian Gauza,Jan Kowalski"
#
# Anti-abuse: nawet jeśli ktoś sfałszuje plan na enterprise, jego license
# client (podpisany HMAC sigem) musi matchować whitelist. Brak match = 404.
_SKLEPAKCES_OWNERS = [
    n.strip().lower() for n in os.environ.get(
        'AKCES_SKLEPAKCES_OWNERS', 'Adrian Gauza,Adrian'
    ).split(',') if n.strip()
]

def _is_sklepakces_owner() -> bool:
    """Czy obecna licencja jest na whitelist właścicieli sklepakces?
    Exact match (case-insensitive po strip). NIE substring — bo "Adrian"
    nie powinien matchować "Adrian Kowalski" gdyby ktoś dodał takiego klienta."""
    try:
        from modules.license import get_license_info
        lic = get_license_info() or {}
        client = (lic.get('client') or '').strip().lower()
        return bool(client) and client in _SKLEPAKCES_OWNERS
    except Exception:
        return False

if _is_sklepakces_owner():
    try:
        from modules.sklepakces_dashboard import sklepakces_ui_bp
        app.register_blueprint(sklepakces_ui_bp)
        print(f'[OK] Sklepakces UI dashboard zarejestrowane (owner whitelist match)')
    except Exception as e:
        print(f'[sklepakces_ui] blueprint registration failed: {e}')
else:
    print('[INFO] Sklepakces UI dashboard SKIPPED (license client NIE na whitelist owners)')
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
    print(f"[WARN] Analytics module not loaded: {e}")

# Extracted route blueprints
from modules.sprzedaze import sprzedaze_bp
app.register_blueprint(sprzedaze_bp)

from modules.wysylki import wysylki_bp
app.register_blueprint(wysylki_bp)

from modules.analityka import analityka_bp
app.register_blueprint(analityka_bp)

from modules.winning_products import winning_bp
app.register_blueprint(winning_bp)

from modules.ustawienia import ustawienia_bp
app.register_blueprint(ustawienia_bp)

from modules.warehouse import warehouse_bp
app.register_blueprint(warehouse_bp)

from modules.palety import palety_bp
app.register_blueprint(palety_bp)

from modules.eula import eula_bp
app.register_blueprint(eula_bp)

from modules.onboarding import onboarding_bp
app.register_blueprint(onboarding_bp)

# === API v1 — Public REST API dla zewnetrznych integracji ===
# Rejestruje blueprint /api/v1/* (products/orders/stock/pallets/webhooks),
# panel admina /api/admin/keys, OpenAPI docs /api/v1/docs, webhook delivery worker.
try:
    from modules.api_v1 import register_api_v1
    register_api_v1(app)
except Exception as _e:
    print(f"[WARN] API v1 nie zarejestrowane: {_e}")

# Sklepakces integration (Faza 3) — separate /api/v1/sklepakces/* namespace.
# Plugin WooCommerce sklepakces.pl wysyła webhooki tutaj (HMAC + nonce).
# Tabele sklepakces_* osobne od api_v1 owners — patrz README_SKLEPAKCES.md.
# GATED: whitelist license.client (zob. _is_sklepakces_owner() wyżej).
# Twoja marka = tylko Ty (i ewentualnie świadomie wpisani znajomi).
if _is_sklepakces_owner():
    try:
        from modules.sklepakces_blueprint import sklepakces_bp, init_sklepakces_schema
        init_sklepakces_schema()
        app.register_blueprint(sklepakces_bp)
        print("[OK] Sklepakces integration zarejestrowane (prefix /api/v1/sklepakces, owner whitelist match)")
    except Exception as _e:
        print(f"[WARN] Sklepakces integration nie zarejestrowana: {_e}")
else:
    print("[INFO] Sklepakces webhook API SKIPPED (license client NIE na whitelist owners)")


# ============================================================
# RATE LIMITING — per-endpoint limits (on top of global 200/min)
# Verified against actual Flask endpoint names from url_map
# ============================================================
if limiter:
    _rl_applied = []

    # Auth brute-force protection
    for _ep, _limit in [
        ('auth.login', '5 per minute'),
        ('auth.first_setup', '3 per minute'),
        ('auth.zmien_haslo', '5 per minute'),
        ('auth.user_add', '10 per minute'),
    ]:
        _fn = app.view_functions.get(_ep)
        if _fn:
            limiter.limit(_limit)(_fn)
            _rl_applied.append(_ep)

    # Sensitive API write endpoints: 30 per minute shared
    _api_write_limit = limiter.shared_limit("30 per minute", scope="api_write")
    for _ep in [
        'backup.api_create_backup', 'backup.api_restore_backup', 'backup.api_sync_gdrive',
        'api_ngrok_control', 'api_notify', 'api_license_verify',
        'setup_save', 'setup_logo',
        'token_refresh.api_refresh_token',
    ]:
        _fn = app.view_functions.get(_ep)
        if _fn:
            _api_write_limit(_fn)
            _rl_applied.append(_ep)

    # Allegro callback: 60 per minute
    _webhook_limit = limiter.shared_limit("60 per minute", scope="webhooks")
    for _ep in ['allegro.callback', 'allegro.auth', 'telegram.api_send']:
        _fn = app.view_functions.get(_ep)
        if _fn:
            _webhook_limit(_fn)
            _rl_applied.append(_ep)

    print(f"[OK] Rate limits applied to {len(_rl_applied)} endpoints: {', '.join(_rl_applied[:5])}...")


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
    resp = make_response(redirect('/dashboard'))
    resp.set_cookie('akces_user', user, max_age=60*60*24*365, httponly=True, samesite='Lax')  # 1 rok
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
        # PHASE 2: generyczny status w publicznym body (bez {e} = bez
        # leak sciezek/SQL — duch PHASE 1). Szczegoly tylko do logu serwera.
        db_status = 'error'
        log_warning(f"[health] DB check failed: {e}")

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

    # PHASE 2: HTTP 503 gdy DB padla — monitoring zewnetrzny (UptimeRobot
    # itp.) patrzy na kod HTTP, nie parsuje body. Wczesniej zawsze 200 =
    # awaria DB niewykrywalna z zewnatrz.
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
    }), (200 if db_status == 'ok' else 503)

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
    import subprocess, sys
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
            from modules.database import get_config
            domain = get_config('ngrok_domain', '')
            token = get_config('ngrok_auth_token', '')
            cmd = ['ngrok', 'http', '5000']
            if domain:
                cmd.extend(['--url', domain])
            # Przekaz token przez env (dziala na wszystkich platformach)
            env = os.environ.copy()
            if token:
                env['NGROK_AUTHTOKEN'] = token
            # Windows vs Linux: inne flagi dla procesu w tle
            if sys.platform == 'win32':
                subprocess.Popen(cmd, env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                subprocess.Popen(cmd, env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            return jsonify({'ok': True, 'msg': 'Ngrok starting...'})
        except Exception as e:
            return jsonify({'ok': False, 'msg': str(e)})
    elif action == 'stop':
        try:
            import subprocess, sys
            if sys.platform == 'win32':
                subprocess.run(['taskkill', '/F', '/IM', 'ngrok.exe', '/T'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.run(['pkill', '-f', 'ngrok'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            from modules.database import set_config
            set_config('app_base_url', 'http://localhost:5000')
            return jsonify({'ok': True, 'msg': 'Ngrok stopped'})
        except Exception as e:
            return jsonify({'ok': False, 'msg': str(e)})
    return jsonify({'ok': False, 'msg': 'Unknown action'})

@app.route('/api/cloudflare-status')
def api_cloudflare_status():
    """Cloudflare Tunnel status — sprawdza czy cloudflared dziala (systemd + proces)

    Hostname pobierany z config('cloudflare_url'), NIE hardcoded.
    """
    import subprocess, sys
    from modules.database import get_config as _get_config
    raw_url = (_get_config('cloudflare_url', '') or '').strip()
    # Extract hostname z URL (https://app.example.com → app.example.com)
    hostname = raw_url
    if raw_url.startswith('http'):
        try:
            from urllib.parse import urlparse
            hostname = urlparse(raw_url).hostname or raw_url
        except Exception:
            pass
    active = False
    try:
        if sys.platform != 'win32':
            r = subprocess.run(['systemctl', 'is-active', 'cloudflared'],
                               capture_output=True, text=True, timeout=3)
            if r.stdout.strip() == 'active':
                active = True
            else:
                r2 = subprocess.run(['pgrep', '-f', 'cloudflared'],
                                    capture_output=True, text=True, timeout=3)
                active = bool(r2.stdout.strip())
        else:
            r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq cloudflared.exe'],
                               capture_output=True, text=True, timeout=3)
            active = 'cloudflared.exe' in r.stdout
    except Exception:
        active = False
    return jsonify({
        'active': active,
        'hostname': hostname,
        'url': raw_url if raw_url else ('https://' + hostname if hostname else ''),
        'configured': bool(raw_url),
    })

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


@app.route('/api/paletomat-early-access', methods=['POST'])
def paletomat_early_access():
    """Webhook: wyslij powiadomienie o early access na Telegram"""
    from modules.database import get_config
    import requests as _req

    client = get_config('license_client', 'unknown')
    key = get_config('license_key', '?')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    msg = (
        "<b>Early Access Request</b>\n"
        f"Client: {client}\n"
        f"Key: {key}\n"
        f"Timestamp: {now}"
    )

    try:
        from modules.telegram_bot import send_telegram_support
        send_telegram_support(msg, parse_mode='HTML')
    except Exception:
        pass

    discord_url = get_config('discord_webhook_url', '')
    if discord_url:
        try:
            _req.post(discord_url, json={
                'content': f"Early Access Request\nClient: {client}\nKey: {key}\nTimestamp: {now}"
            }, timeout=10)
        except Exception:
            pass

    return jsonify({'ok': True})


@app.route('/brand-logo')
def brand_logo_serve():
    """v1.0.105: Serwuj logo server-side (pierwszy istniejacy plik).

    Bez tego base.html mial <img src=brand_logo.svg onerror=...png onerror=...default>
    -> 2x 404 (svg+png) per render gdy klient nie wgral wlasnego logo.
    Spam 404 w logach. Teraz endpoint zwraca pierwszy istniejacy = zero 404.
    """
    from flask import send_file, abort
    _static = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    for _fname in ('brand_logo.svg', 'brand_logo.png', 'brand_logo_default.svg'):
        _fp = os.path.join(_static, _fname)
        if os.path.exists(_fp):
            resp = make_response(send_file(_fp))
            resp.headers['Cache-Control'] = 'public, max-age=3600'  # cache 1h
            return resp
    abort(404)


_system_stats_cache = {'data': None, 'ts': 0}

@app.route('/api/system-stats')
def api_system_stats():
    """System stats for Raspberry Pi dashboard"""
    import psutil, time
    # v1.0.110: cache 10s. Dashboard polluje co 15s, ale przy wielu otwartych
    # kartach (Adrian ma 5-6) kazda osobny request -> psutil disk/mem/temp/uptime
    # liczone N razy. Cache 10s = jedno liczenie na ~10s niezaleznie od liczby kart.
    _now = time.time()
    if _system_stats_cache['data'] and (_now - _system_stats_cache['ts']) < 10:
        return jsonify(_system_stats_cache['data'])
    # v1.0.105: interval=None (nieblokujace) zamiast 0.5 - nie trzyma watka.
    cpu = psutil.cpu_percent(interval=None)
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
    _stats = {
        'cpu': round(cpu, 1),
        'ram_used': round(mem.used / (1024**3), 1),
        'ram_total': round(mem.total / (1024**3), 1),
        'ram_percent': mem.percent,
        'disk_used': round(disk.used / (1024**3), 1),
        'disk_total': round(disk.total / (1024**3), 1),
        'disk_percent': disk.percent,
        'temp': round(temp, 1),
        'uptime': uptime_str
    }
    _system_stats_cache['data'] = _stats
    _system_stats_cache['ts'] = _now
    return jsonify(_stats)

@app.route("/api/phonkbot-stats")
def api_phonkbot_stats():
    try:
        import requests as _req
        r = _req.get("http://localhost:5001/api/stats", timeout=3)
        return r.json()
    except:
        return jsonify({"total_tracks": 0, "published": 0, "pending_review": 0, "total_views": 0, "offline": True})

@app.route("/api/phonkbot-generate", methods=["POST"])
def api_phonkbot_generate():
    try:
        import requests as _req
        r = _req.post("http://localhost:5001/api/pipeline/run", timeout=5)
        return r.json()
    except:
        return jsonify({"status": "error", "message": "PhonkBot offline"})

@app.route('/api/live-sales')
def api_live_sales():
    """Ostatnie sprzedaze dzis — dla kiosk live feed"""
    from modules.database import get_db
    conn = get_db()
    today_str = datetime.now().strftime('%Y-%m-%d')
    rows = conn.execute('''
        SELECT s.data_sprzedazy, s.cena, s.ilosc,
               COALESCE(p.nazwa, s.nazwa, 'Zamowienie') as nazwa
        FROM sprzedaze s
        LEFT JOIN produkty p ON p.id = s.produkt_id
        WHERE date(s.data_sprzedazy) = ?
        AND s.status NOT IN ('zwrot', 'anulowane', 'anulowana')
        ORDER BY s.data_sprzedazy DESC
        LIMIT 15
    ''', (today_str,)).fetchall()
    sales = []
    for r in rows:
        nazwa = r['nazwa'] or 'Zamowienie'
        if len(nazwa) > 40:
            nazwa = nazwa[:37] + '...'
        kwota = round((r['cena'] or 0) * (r['ilosc'] or 1), 0)
        czas = r['data_sprzedazy']
        if czas and len(czas) > 10:
            czas = czas[11:16]  # HH:MM
        else:
            czas = '--:--'
        sales.append({'nazwa': nazwa, 'kwota': f"{kwota:.0f}", 'czas': czas})
    return jsonify({'sales': sales})


def _get_insights_safe():
    try:
        from modules.database import get_insights
        return get_insights()
    except Exception as e:
        print(f"[Insights] Error: {e}")
        return {'top_sellers': [], 'low_stock': [], 'best_categories': [], 'stale': []}

@app.route('/')
@app.route("/launcher")
def project_launcher():
    kiosk = request.args.get("kiosk", "")
    _brand = (get_config('brand_name', '') or 'AKCES HUB').strip()
    _platform = (get_config('platform_name', '') or 'Hub').strip()
    _pb_enabled = (get_config('phonkbot_enabled', '0') or '0') == '1'

    # Single-project tenant (klient bez PhonkBota) -> launcher z 1 kafelkiem to
    # zbedny extra klik. Redirect od razu do /dashboard (login screen jesli nie
    # zalogowany). Wlasciciel z PhonkBotem widzi launcher normalnie.
    # Wymus pokazanie launchera: ?force=1 (debug/preview).
    if not _pb_enabled and request.args.get('force') != '1':
        return redirect('/dashboard')

    return render_template("project_launcher.html",
        version=VERSION, kiosk=kiosk,
        brand_name=_brand, platform_name=_platform,
        phonkbot_enabled=_pb_enabled,
    )

@app.route('/dashboard')
def home():
    # Magazynier — uproszczony dashboard z linkami do wysyłek i magazynu
    if session.get('rola') == 'magazynier':
        from modules.database import get_db
        conn = get_db()
        # Statystyki dla magazyniera
        do_wysylki = conn.execute("SELECT COUNT(*) FROM sprzedaze WHERE status IN ('nowa','nadana')").fetchone()[0]
        wysylki_dzis = conn.execute("SELECT COUNT(*) FROM sprzedaze WHERE status = 'wyslana' AND DATE(data_sprzedazy) = DATE('now')").fetchone()[0]
        produkty_magazyn = conn.execute("SELECT COALESCE(SUM(ilosc), 0) FROM produkty WHERE status IN ('magazyn','wystawiony')").fetchone()[0]
        regaly_cnt = conn.execute("SELECT COUNT(DISTINCT lokalizacja) FROM produkty WHERE lokalizacja IS NOT NULL AND lokalizacja != ''").fetchone()[0]
        return render_template_string('''<!DOCTYPE html>
<html lang="pl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ brand_name }} - Panel magazyniera</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a1a;color:#fff;min-height:100vh}
.container{width:100%;max-width:600px;margin:0 auto;padding:20px}
h1{text-align:center;font-size:1.5rem;margin-bottom:4px;color:#e2e8f0}
.sub{text-align:center;color:#64748b;font-size:0.85rem;margin-bottom:20px}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:20px}
.stat{background:#12122a;border:1px solid #1e1e3a;border-radius:12px;padding:16px;text-align:center}
.stat-val{font-size:1.8rem;font-weight:700}
.stat-label{font-size:0.7rem;color:#64748b;margin-top:2px}
.card{display:block;background:#12122a;border:2px solid #1e1e3a;border-radius:16px;padding:24px;margin-bottom:12px;text-decoration:none;color:#fff;transition:all 0.2s}
.card:hover{border-color:#6366f1;transform:translateY(-2px);box-shadow:0 8px 30px rgba(99,102,241,0.2)}
.card-row{display:flex;align-items:center;gap:16px}
.card-icon{font-size:2.5rem;min-width:50px;text-align:center}
.card-title{font-size:1.1rem;font-weight:700;margin-bottom:2px}
.card-desc{font-size:0.8rem;color:#94a3b8}
.badge{display:inline-block;background:#ef4444;color:#fff;font-size:0.75rem;font-weight:700;padding:2px 8px;border-radius:10px;margin-left:8px}
.logout{display:block;text-align:center;margin-top:20px;color:#64748b;font-size:0.85rem}
.refresh{text-align:center;margin-bottom:16px}
.refresh a{color:#64748b;font-size:0.75rem;text-decoration:none}
</style></head><body>
<div class="container">
    <h1>{{ current_user }}</h1>
    <div class="sub">Panel magazyniera</div>

    <div class="stats">
        <div class="stat">
            <div class="stat-val" style="color:{% if do_wysylki > 0 %}#ef4444{% else %}#22c55e{% endif %}">{{ do_wysylki }}</div>
            <div class="stat-label">DO WYSYLKI</div>
        </div>
        <div class="stat">
            <div class="stat-val" style="color:#22c55e">{{ wysylki_dzis }}</div>
            <div class="stat-label">WYSLANE DZIS</div>
        </div>
        <div class="stat">
            <div class="stat-val" style="color:#3b82f6">{{ produkty_magazyn }}</div>
            <div class="stat-label">SZT W MAGAZYNIE</div>
        </div>
        <div class="stat">
            <div class="stat-val" style="color:#f59e0b">{{ regaly_cnt }}</div>
            <div class="stat-label">LOKALIZACJI</div>
        </div>
    </div>

    <a href="/wysylki" class="card" style="border-color:{% if do_wysylki > 0 %}#ef4444{% else %}#22c55e{% endif %}33">
        <div class="card-row">
            <div class="card-icon"><span class=material-symbols-outlined>inventory_2</span></div>
            <div>
                <div class="card-title" style="color:#22c55e">Wysylki{% if do_wysylki > 0 %}<span class="badge">{{ do_wysylki }}</span>{% endif %}</div>
                <div class="card-desc">Pakowanie i nadawanie paczek</div>
            </div>
        </div>
    </a>
    <a href="/warehouse/shelves" class="card" style="border-color:#3b82f633">
        <div class="card-row">
            <div class="card-icon"><span class=material-symbols-outlined>dns</span></div>
            <div>
                <div class="card-title" style="color:#3b82f6">Regaly</div>
                <div class="card-desc">Mapa regalow, polki, skanuj QR</div>
            </div>
        </div>
    </a>
    <a href="/magazyn" class="card" style="border-color:#f59e0b33">
        <div class="card-row">
            <div class="card-icon"><span class=material-symbols-outlined>assignment</span></div>
            <div>
                <div class="card-title" style="color:#f59e0b">Magazyn</div>
                <div class="card-desc">Produkty, wyszukiwarka</div>
            </div>
        </div>
    </a>
    <div class="refresh"><a href="/dashboard"><span class=material-symbols-outlined>sync</span> Odswiez</a></div>
    <a href="/auth/logout" class="logout">Wyloguj sie</a>
</div></body></html>''', do_wysylki=do_wysylki, wysylki_dzis=wysylki_dzis,
            produkty_magazyn=produkty_magazyn, regaly_cnt=regaly_cnt)

    # Onboarding — redirect jeśli nie przeszedł setup
    from modules.database import get_config as _gc
    if not _gc('setup_done', ''):
        return redirect('/setup')

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
            SUM(CASE WHEN date(data_sprzedazy) = ? AND status NOT IN ('zwrot','anulowane','anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline') THEN 1 ELSE 0 END) as dzis_cnt,
            COALESCE(SUM(CASE WHEN date(data_sprzedazy) = ? AND status NOT IN ('zwrot','anulowane','anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline') THEN cena * ilosc ELSE 0 END), 0) as dzis_suma,
            SUM(CASE WHEN status NOT IN ('zwrot','anulowane','anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline') THEN 1 ELSE 0 END) as msc_cnt,
            COALESCE(SUM(CASE WHEN status NOT IN ('zwrot','anulowane','anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline') THEN cena * ilosc ELSE 0 END), 0) as msc_suma
        FROM sprzedaze
        WHERE date(data_sprzedazy) >= ?

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
    
    # Statystyki miesięczne — używamy get_full_stats dla spójności z zysk_miesiac
    # (get_full_stats liczy cena*ilosc+koszt_dostawy + sprzedaze_prywatne,
    #  sypie_row liczy tylko cena*ilosc — używamy tej samej bazy co zysk)
    miesiac_kwota = float(stats.get('sprzedaz_miesiac_suma', miesiac_data['suma'] or 0))
    miesiac_zamowienia = int(stats.get('sprzedaz_miesiac_cnt', miesiac_data['cnt'] or 0))
    
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
        icon = 'D' if user == 'dziadek' else 'B'
        nazwa = user.upper()
        return render_template('dziadek.html', user_icon=icon, user_name=nazwa, do_wyslania=stats['do_wyslania'])
    
    # Adrian - pełny widok
    mag = mag_stats()
    pal = pal_stats()
    
    # Allegro status
    from modules.allegro_api import is_configured, is_authenticated
    allegro = {
        'status': 'Online' if is_authenticated() else ('Skonfiguruj' if is_configured() else 'Offline'),
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
    
    # Kiosk mode — tylko przez URL param ?kiosk=1 (nie sticky)
    # Na Pi: otwórz chromium z /?kiosk=1 w autostart
    # ?kiosk=0 wyłącza tryb kiosku (czyści cookie)
    kiosk_param = request.args.get('kiosk', '')
    if kiosk_param == '0':
        # Wyłącz kiosk — usuń cookie i pokaż normalny dashboard
        resp = make_response(redirect('/dashboard'))
        resp.delete_cookie('kiosk_mode')
        return resp
    is_kiosk = kiosk_param == '1'
    if is_kiosk:
        # Per-instance konfiguracja (klient ma swoje, nie Twoje):
        _platform = (get_config('platform_name', '') or '').strip()
        _cf_url = (get_config('cloudflare_url', '') or '').strip()
        _pb_enabled = (get_config('phonkbot_enabled', '0') or '0') == '1'
        resp = make_response(render_template('kiosk_home.html',
            version=VERSION,
            today=today, mag=mag, pal=pal, allegro=allegro,
            active_home='active', active_magazyn='', active_paletomat='',
            active_allegro='', active_olx='', active_vinted='', active_narzedzia='',
            active_monitor='',
            platform_name=_platform,
            cloudflare_url=_cf_url,
            phonkbot_enabled=_pb_enabled,
            **sypie_data
        ))
        return resp

    # Statystyki do dashboardu - zgodne z kalkulatorem marzy
    # zysk_miesiac liczy: przychod_netto (po VAT) - koszt - prowizja_z_netto (patrz database.py)
    zwroty_suma = float(stats.get('zwroty_miesiac_suma', 0))
    przychod_brutto_msc = miesiac_kwota  # brutto z Allegro (po zwrotach)
    przychod_netto_msc = float(stats.get('przychod_netto_msc', 0)) or (przychod_brutto_msc / 1.23 if przychod_brutto_msc > 0 else 0)
    _zysk = float(stats.get('zysk_miesiac', 0))
    _prowizja_kwota = float(stats.get('prowizja_msc', 0)) or (przychod_netto_msc * 0.11)
    # Marża = zysk / przychod_brutto (bo to porownujemy do tego co klient zaplacil)
    _marza = round(_zysk / przychod_brutto_msc * 100) if przychod_brutto_msc > 0 else 0
    _marza_color = '#22c55e' if _marza >= 40 else '#beee00' if _marza >= 25 else '#eab308' if _marza >= 15 else '#ef4444'
    # "Na rękę" = po PIT liniowym 19% (zysk juz jest po VAT)
    _marza_net = round(_zysk * 0.81 / przychod_brutto_msc * 100) if przychod_brutto_msc > 0 else 0
    _marza_net_color = '#22c55e' if _marza_net >= 25 else '#beee00' if _marza_net >= 15 else '#eab308' if _marza_net >= 10 else '#ef4444'
    monthly_stats = {
        'przychod': f"{przychod_brutto_msc:.0f}",
        'przychod_brutto': f"{przychod_brutto_msc:.0f}",
        'przychod_netto': f"{przychod_netto_msc:.0f}",
        'cogs': f"{stats.get('cogs_miesiac', 0):.0f}",
        'koszt_palet': f"{stats.get('koszt_palet_msc', 0):.0f}",
        'koszty_op': f"{stats.get('koszty_op_msc', 0):.0f}",
        'prowizja': f"{_prowizja_kwota:.0f}",
        'zysk': f"{_zysk:.0f}",
        'marza': _marza,
        'marza_color': _marza_color,
        'marza_net': _marza_net,
        'marza_net_color': _marza_net_color,
        'roi': f"{stats.get('roi_miesiac', 0):.0f}",
        'zwroty_cnt': stats.get('zwroty_miesiac_cnt', 0),
        'zwroty_suma': f"{zwroty_suma:.0f}",
        'magazyn_wartosc': f"{stats.get('magazyn_wartosc', 0):.0f}",
        'magazyn_sztuki': stats.get('magazyn_sztuki', 0),
        'stojace': stats.get('stojace_30dni', 0),
    }

    # Sprawdź czy jest nowa wersja dostępna (cache 2 min)
    update_status = None
    try:
        import subprocess as _sp
        from modules.database import get_config, set_config

        # v1.0.89: ZIP install (Macek) tez ma background check - poprzez public repo.
        _app_dir = os.path.dirname(os.path.abspath(__file__))
        _is_zip = not os.path.isdir(os.path.join(_app_dir, '.git'))

        cache_raw = get_config('update_check_cache', '')
        cache = json.loads(cache_raw) if cache_raw else {}
        cache_age = time.time() - cache.get('checked_at', 0)

        # PERF: NIGDY nie wywoluj git fetch / HTTP synchronicznie w route -
        # blokowal dashboard do 15s. Cache stary -> odpal background refresh
        # i zwroc to co jest. TTL 120s (2 min).
        if cache_age > 120 and not getattr(home, '_git_check_running', False):
            home._git_check_running = True
            _target = _public_update_check_async if _is_zip else _git_update_check_async
            threading.Thread(target=_target, daemon=True).start()

        update_status = {
            'has_update': cache.get('has_update', False),
            'remote_msg': cache.get('remote_msg', ''),
            'remote_hash': cache.get('remote_hash', ''),
            'local_hash': cache.get('local_hash', ''),
            'is_zip': _is_zip,
        }
        # v1.0.98 FIX: sync update_available z cache.has_update zeby banner
        # gorny zawsze zgadzal sie z dashboard widgetem. Bez tego update_available
        # mialo stale value (np. po manualnym git pull + restart, gdzie endpoint
        # update nie zostal wywolany -> config nie wyczyszczony) -> baner wisi
        # mimo ze cache.has_update=False i dashboard mowi 'System aktualny'.
        try:
            _expected = '1' if cache.get('has_update', False) else '0'
            if get_config('update_available', '0') != _expected:
                set_config('update_available', _expected)
        except Exception:
            pass
    except:
        pass

    # Po aktualizacji — jednorazowy banner "zaktualizowano"
    update_banner = None
    try:
        raw = get_config('last_update_info', '')
        if raw:
            ui = json.loads(raw)
            if not ui.get('seen'):
                update_banner = ui
                ui['seen'] = True
                set_config('last_update_info', json.dumps(ui))
    except:
        pass

    # Kiosk TYLKO na Pi ekranie (localhost BEZ proxy). Reszta — home.html.
    # Mobile dostaje home.html z responsive CSS (wiekszymi fontami/kafelkami).
    # UWAGA: Cloudflare Tunnel i ngrok forwardują przez localhost:5000 — trzeba to wykryć
    _xff = request.headers.get('X-Forwarded-For', '')
    _cf_ip = request.headers.get('CF-Connecting-IP', '')           # Cloudflare
    _cf_ray = request.headers.get('CF-Ray', '')                    # Cloudflare Ray ID
    _ngrok_trace = request.headers.get('ngrok-trace-id', '')       # ngrok
    _fwd_host = request.headers.get('X-Forwarded-Host', '')        # reverse proxy
    _remote = request.remote_addr or ''
    _is_proxied = bool(_xff or _cf_ip or _cf_ray or _ngrok_trace or _fwd_host)
    # Kiosk mode OPT-IN — domyślnie WYŁĄCZONY (klient widzi pełen dashboard).
    # Włącz dla swojego Pi przez:
    #   set_config('kiosk_auto_enabled', '1')
    # LAN IPs auto-detekcji konfigurowalne via config (NIE hardcoded Adrian's Pi):
    #   set_config('kiosk_local_ips', '127.0.0.1,::1,192.168.X.Y')
    _kiosk_auto_enabled = (get_config('kiosk_auto_enabled', '0') or '0') == '1'
    _local_ips = [
        ip.strip() for ip in (get_config('kiosk_local_ips', '127.0.0.1,::1') or '127.0.0.1,::1').split(',')
        if ip.strip()
    ]
    _is_pi_screen = (
        _kiosk_auto_enabled
        and _remote in _local_ips
        and not _is_proxied
    )
    _force_kiosk = request.args.get('kiosk') == '1'
    _force_full = request.args.get('kiosk') == '0' or request.args.get('full') == '1'
    if (_is_pi_screen or _force_kiosk) and not _force_full:
        # Per-instance konfiguracja (NIE hardcoded Adrian's Pi):
        _platform = (get_config('platform_name', '') or '').strip()
        _cf_url = (get_config('cloudflare_url', '') or '').strip()
        _pb_enabled = (get_config('phonkbot_enabled', '0') or '0') == '1'
        resp = make_response(render_template('kiosk_home.html',
            version=VERSION,
            today=today, mag=mag, pal=pal, allegro=allegro,
            active_home='active', active_magazyn='', active_paletomat='',
            active_allegro='', active_olx='', active_vinted='', active_narzedzia='',
            active_monitor='',
            platform_name=_platform,
            cloudflare_url=_cf_url,
            phonkbot_enabled=_pb_enabled,
            **sypie_data
        ))
    else:
        resp = make_response(render_template('home.html',
            version=VERSION,
            today_date=datetime.now().strftime('%d.%m.%Y'),
            today=today, mag=mag, pal=pal, allegro=allegro,
            telegram_online=bot_status(),
            unread_count=2, activity=activity,
            goal=goal, monthly=monthly_stats,
            update_banner=update_banner, update_status=update_status,
            top_produkty=stats.get('top_produkty', []),
            top_dostawcy=stats.get('top_dostawcy', []),
            insights=_get_insights_safe(),
            active_home='active', active_magazyn='', active_paletomat='',
            active_allegro='', active_monitor='', active_narzedzia='',
            **sypie_data
        ))
    if not request.cookies.get('akces_user'):
        resp.set_cookie('akces_user', 'adrian', max_age=60*60*24*365, httponly=True, samesite='Lax')
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

    import html as html_lib
    deals_html = ''
    for d in deals:
        kw = d.get('matched_keywords', '[]')
        try:
            kw_list = json.loads(kw) if isinstance(kw, str) else kw
            kw_str = html_lib.escape(', '.join(kw_list[:3]))
        except Exception:
            kw_str = html_lib.escape(str(kw)[:50])

        source_emoji = '<span class=material-symbols-outlined>store</span>' if d['source'] == 'warrington' else '<span class=material-symbols-outlined>storefront</span>'
        # Ceny już w PLN (API z url-accept-currency: pln)
        _dp = float(d.get('price', 0) or 0)
        price_str = f"{_dp:.0f} PLN"
        time_str = html_lib.escape(d.get('first_seen', '')[:16]) if d.get('first_seen') else ''

        # Escape all DB values to prevent XSS
        _safe_image_url = html_lib.escape(d.get('image_url', '') or '', quote=True)
        _safe_title = html_lib.escape(d.get('title', '?')[:90])
        _safe_url = html_lib.escape(d.get('url', '#'), quote=True)
        _safe_category = html_lib.escape(d.get('category', '-'))
        _safe_source = html_lib.escape(d.get('source', '').title())

        img_html = ''
        if d.get('image_url'):
            img_html = f'<img src="{_safe_image_url}" style="width:80px;height:80px;object-fit:cover;border-radius:8px;flex-shrink:0" onerror="this.style.display=\'none\'" loading="lazy">'
        else:
            img_html = f'<div style="width:80px;height:80px;background:var(--border-color);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:28px;flex-shrink:0">{source_emoji}</div>'

        # RRP i ROI info
        _rrp = float(d.get('market_value', 0) or 0)
        _roi = round(_rrp / _dp, 1) if _rrp > 0 and _dp > 0 else 0
        roi_badge = ''
        if _roi >= 5:
            roi_badge = '<span style="background:#ef4444;color:#fff;padding:1px 6px;border-radius:8px;font-size:10px;font-weight:700">ROI {:.0f}x</span>'.format(_roi)
        elif _roi >= 3:
            roi_badge = '<span style="background:#f59e0b;color:#fff;padding:1px 6px;border-radius:8px;font-size:10px;font-weight:700"><span class=material-symbols-outlined>payments</span> ROI {:.0f}x</span>'.format(_roi)
        elif _roi >= 1.5:
            roi_badge = '<span style="background:#3b82f6;color:#fff;padding:1px 6px;border-radius:8px;font-size:10px">ROI {:.1f}x</span>'.format(_roi)

        rrp_str = f' | RRP: {_rrp:.0f} PLN' if _rrp > 0 else ''

        deals_html += f'''
        <div style="display:flex;gap:12px;padding:12px;border-bottom:1px solid var(--border-color);align-items:center">
            {img_html}
            <div style="flex:1;min-width:0">
                <div style="font-weight:600;margin-bottom:3px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                    <a href="{_safe_url}" target="_blank" style="color:var(--text-color);text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{_safe_title}</a>
                    {roi_badge}
                </div>
                <div style="font-size:12px;color:var(--text-secondary)">
                    <span class=material-symbols-outlined>paid</span> {price_str}{rrp_str} | <span class=material-symbols-outlined>folder</span> {_safe_category}
                </div>
                <div style="font-size:11px;color:var(--text-secondary);margin-top:2px">
                    {source_emoji} {_safe_source} | {kw_str if kw_str else '-'} | {time_str}
                </div>
            </div>
        </div>'''

    if not deals_html:
        deals_html = '<div style="padding:30px;text-align:center;color:var(--text-secondary)">Brak znalezionych okazji. Uruchom skan lub poczekaj na harmonogram.</div>'

    kw_tags = ' '.join([f'<span style="display:inline-block;background:var(--accent-color);color:white;padding:2px 8px;border-radius:12px;font-size:12px;margin:2px">{html_lib.escape(k)}</span>' for k in keywords[:20]])

    _msg = html_lib.escape(request.args.get('msg', ''))
    _err = html_lib.escape(request.args.get('err', ''))
    _alert = ''
    if _msg:
        _alert = f'<div style="padding:10px;margin-bottom:12px;background:rgba(0,180,0,0.1);border-radius:8px;text-align:center;font-size:14px"><span class=material-symbols-outlined style=color:#22c55e>check_circle</span> {_msg}</div>'
    elif _err:
        _alert = f'<div style="padding:10px;margin-bottom:12px;background:rgba(255,0,0,0.1);border-radius:8px;text-align:center;font-size:14px"><span class=material-symbols-outlined style=color:#ef4444>cancel</span> {_err}</div>'

    content = f'''
    <div class="hdr"><h1><span class=material-symbols-outlined>search</span> Monitor Okazji Palet</h1></div>
    {_alert}
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:15px">
        <div class="card" style="padding:12px;text-align:center">
            <div style="font-size:24px;font-weight:700">{stats.get('today_new', 0)}</div>
            <div style="font-size:11px;color:var(--text-secondary)">Nowe dzisiaj</div>
        </div>
        <div class="card" style="padding:12px;text-align:center">
            <div style="font-size:24px;font-weight:700"><span class=material-symbols-outlined>store</span> {stats.get('warrington_total', 0)}</div>
            <div style="font-size:11px;color:var(--text-secondary)">Warrington</div>
        </div>
        <div class="card" style="padding:12px;text-align:center">
            <div style="font-size:24px;font-weight:700"><span class=material-symbols-outlined>storefront</span> {stats.get('jobalots_total', 0)}</div>
            <div style="font-size:11px;color:var(--text-secondary)">Jobalots</div>
        </div>
    </div>

    <details style="margin-bottom:15px">
        <summary class="card" style="padding:12px;cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center">
            <span style="font-weight:600"><span class=material-symbols-outlined>bar_chart</span> Statystyki i koszty AI</span>
            <span style="font-size:12px;color:var(--text-secondary)">
                Dzisiaj: ${costs.get('today_all_ai_cost',0):.4f} | Miesiąc: ${costs.get('month_all_ai_cost',0):.4f} | Zaoszcz: ~{costs.get('month_time_saved_min',0)//60}h
            </span>
        </summary>
        <div class="card" style="padding:0;margin-top:-8px;border-top:none;border-top-left-radius:0;border-top-right-radius:0">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:0">
                <div style="padding:12px;border-bottom:1px solid var(--border-color);border-right:1px solid var(--border-color)">
                    <div style="font-weight:600;font-size:13px;margin-bottom:8px"><span class=material-symbols-outlined>today</span> Dzisiaj</div>
                    <div style="font-size:12px;line-height:1.8">
                        <span class=material-symbols-outlined>search</span> Skanów palet: <b>{costs.get('today_scans',0)}</b><br>
                        <span class=material-symbols-outlined>inventory_2</span> Przeskanowanych: <b>{costs.get('today_scraped',0)}</b><br>
                        <span class=material-symbols-outlined>auto_awesome</span> Nowych deali: <b>{costs.get('today_new_deals',0)}</b><br>
                        <span class=material-symbols-outlined>timer</span> Czas skanów: <b>{costs.get('today_scan_time',0):.0f}s</b>
                    </div>
                </div>
                <div style="padding:12px;border-bottom:1px solid var(--border-color)">
                    <div style="font-weight:600;font-size:13px;margin-bottom:8px"><span class=material-symbols-outlined>calendar_month</span> Ten miesiąc</div>
                    <div style="font-size:12px;line-height:1.8">
                        <span class=material-symbols-outlined>search</span> Skanów palet: <b>{costs.get('month_scans',0)}</b><br>
                        <span class=material-symbols-outlined>inventory_2</span> Przeskanowanych: <b>{costs.get('month_scraped',0)}</b><br>
                        <span class=material-symbols-outlined>auto_awesome</span> Nowych deali: <b>{costs.get('month_new_deals',0)}</b><br>
                        <span class=material-symbols-outlined>timer</span> Czas skanów: <b>{costs.get('month_scan_time',0):.0f}s</b>
                    </div>
                </div>
            </div>
            <div style="padding:12px;border-bottom:1px solid var(--border-color)">
                <div style="font-weight:600;font-size:13px;margin-bottom:8px"><span class=material-symbols-outlined>smart_toy</span> Koszty AI — ten miesiąc</div>
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
                <div style="font-weight:600;font-size:13px;margin-bottom:8px"><span class=material-symbols-outlined>trending_up</span> System od początku ({costs.get('system_start','?')})</div>
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
                    <div style="font-weight:600;font-size:13px;margin-bottom:6px"><span class=material-symbols-outlined>payments</span> Koszty AI (all-time)</div>
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
        <button onclick="doScan('warrington',this)" class="btn" style="flex:1;min-width:120px;text-align:center;background:var(--accent-blue);padding:12px;margin:0"><span class=material-symbols-outlined>store</span> Skanuj Warrington</button>
        <button onclick="doScan('jobalots',this)" class="btn" style="flex:1;min-width:120px;text-align:center;background:var(--accent-purple);padding:12px;margin:0"><span class=material-symbols-outlined>storefront</span> Skanuj Jobalots</button>
        <button onclick="doScan('all',this)" class="btn" style="flex:1;min-width:120px;text-align:center;background:var(--accent-green);padding:12px;margin:0"><span class=material-symbols-outlined>sync</span> Skanuj wszystko</button>
    </div>
    <div id="scanStatus" style="display:none;text-align:center;padding:12px;margin-bottom:15px;background:var(--card-bg);border-radius:10px;border:1px solid var(--border-color)">
        <div style="display:inline-block;width:20px;height:20px;border:3px solid var(--border-color);border-top-color:var(--accent-color);border-radius:50%;animation:spin 0.8s linear infinite;vertical-align:middle"></div>
        <span id="scanText" style="margin-left:8px;vertical-align:middle">Skanowanie...</span>
    </div>
    <style>@keyframes spin{{from{{transform:rotate(0deg)}}to{{transform:rotate(360deg)}}}}</style>
    <script nonce="{getattr(request, '_csp_nonce', '')}">
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
                    tx.textContent = '[OK] ' + d.msg;
                    st.style.background = 'rgba(0,180,0,0.1)';
                }} else {{
                    tx.textContent = '[ERR] ' + (d.err||'Błąd');
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
                <input type="hidden" name="csrf_token" value="{generate_csrf()}">
                <input type="hidden" name="source" value="warrington">
                <button type="submit" style="padding:6px 14px;border-radius:8px;border:1px solid {'#22c55e' if warrington_on else '#ef4444'};background:{'rgba(34,197,94,0.1)' if warrington_on else 'rgba(239,68,68,0.1)'};color:{'#22c55e' if warrington_on else '#ef4444'};font-size:12px;font-weight:600;cursor:pointer">
                    <span class=material-symbols-outlined>store</span> Warrington: {'ON' if warrington_on else '<span class=material-symbols-outlined style=color:#ef4444>cancel</span> OFF'}
                </button>
            </form>
            <form method="POST" action="/monitor/toggle-source" style="margin:0">
                <input type="hidden" name="csrf_token" value="{generate_csrf()}">
                <input type="hidden" name="source" value="jobalots">
                <button type="submit" style="padding:6px 14px;border-radius:8px;border:1px solid {'#22c55e' if jobalots_on else '#ef4444'};background:{'rgba(34,197,94,0.1)' if jobalots_on else 'rgba(239,68,68,0.1)'};color:{'#22c55e' if jobalots_on else '#ef4444'};font-size:12px;font-weight:600;cursor:pointer">
                    <span class=material-symbols-outlined>storefront</span> Jobalots: {'ON' if jobalots_on else '<span class=material-symbols-outlined style=color:#ef4444>cancel</span> OFF'}
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

    <a href="/dashboard" class="back" style="margin-top:15px">← Powrót</a>
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
    content += f'<input type="hidden" name="csrf_token" value="{generate_csrf()}">'
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

def _validate_csrf_or_abort():
    """Belt-and-suspenders CSRF — explicit check wewnatrz krytycznych routow.
    Nie polegamy TYLKO na before_request middleware — druga warstwa ochrony
    przed regresja jesli middleware zostanie kiedys zmienione/wylaczone.

    Sprawdza X-CSRFToken header (AJAX), form 'csrf_token', lub same-origin Referer.
    Abort(403) jesli brak.
    """
    # Pomin w trybie testowym (testy maja WTF_CSRF_ENABLED=False)
    if os.environ.get('AKCES_TEST_MODE') == '1':
        return
    token = (request.headers.get('X-CSRFToken')
             or request.headers.get('X-CSRF-Token')
             or (request.form.get('csrf_token') if request.form else None)
             or (request.get_json(silent=True) or {}).get('csrf_token'))
    if token:
        try:
            from flask_wtf.csrf import validate_csrf
            validate_csrf(token)
            return  # OK
        except Exception:
            from flask import abort
            abort(403, description='CSRF token nieprawidlowy lub wygasl')
    # Brak tokena — sprobuj same-origin Referer fallback
    referer = request.headers.get('Referer', '')
    host = request.host_url
    if not (referer.startswith(host) or referer.startswith(host.replace('http://', 'https://'))):
        from flask import abort
        abort(403, description='CSRF: brak tokena i nieprawidlowy Referer')


@app.route('/system/gemini-model', methods=['POST'])
@require_admin
def system_gemini_model():
    """Szybka zmiana modelu Gemini AI — tylko admin (koszty API).
    CSRF + audit log (kto zmienil model, kiedy, z jakiego IP)."""
    _validate_csrf_or_abort()
    from modules.database import get_config, set_config, log_admin_action
    data = request.get_json(silent=True) or {}
    model = data.get('model', '')
    allowed = ['gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-3.1-flash-lite-preview', 'gemini-3.1-pro-preview']
    if model not in allowed:
        log_admin_action('system_gemini_model', {'attempted_model': model}, success=False,
                         error_message=f'Nieznany model: {model}')
        return jsonify({'ok': False, 'error': f'Nieznany model: {model}'})
    old_model = get_config('gemini_model', '')
    set_config('gemini_model', model)
    log_admin_action('system_gemini_model', {'old': old_model, 'new': model}, success=True)
    return jsonify({'ok': True, 'model': model})


@app.route('/admin/update-git', methods=['POST'])
@require_admin
def admin_update_git_alias():
    """ALIAS smart - auto-detect typ instalacji i wywoluje wlasciwy update.

    FIX 2026-05-28: Banner w base.html zawsze kieruje na /admin/update-git.
    Ale klienci ZIP install (jak Macek) NIE maja .git folderu - git pull
    padnie z 'fatal: not a git repository'. Detect:
    - .git folder istnieje -> git pull (system_update)
    - brak .git -> pobierz z PUBLIC repo (system_update_from_public)
    """
    _app_dir = os.path.dirname(os.path.abspath(__file__))
    is_git_install = os.path.isdir(os.path.join(_app_dir, '.git'))
    if is_git_install:
        return system_update()
    # ZIP install - pobierz z public repo bez tokenu
    return system_update_from_public()


@app.route('/system/update', methods=['POST'])
@require_admin
def system_update():
    """Backup + Git pull + restart serwisu z poziomu apki.

    Security layers:
    1. @require_admin — JSON-aware serwerowy admin check (401/403)
    2. _validate_csrf_or_abort() — explicit CSRF check (belt-and-suspenders
       poza before_request middleware)
    3. log_admin_action() — audit log (user_id, timestamp, IP, UA, success)
    4. POST-only (GET -> 405)

    TYLKO admin — git pull + systemctl restart = RCE jesli ktos przejmie
    sesje usera. NIE polegaj na ukrywaniu przycisku w UI.
    """
    _validate_csrf_or_abort()
    from modules.database import log_admin_action
    import subprocess
    try:
        # BACKUP PRZED AKTUALIZACJĄ
        try:
            from modules.backup_manager import create_backup
            backup_result = create_backup()
            if backup_result:
                print(f"[OK] Pre-update backup: {backup_result}")
            else:
                print("[WARN] Pre-update backup failed — continuing update anyway")
        except Exception as e:
            print(f"[WARN] Pre-update backup error: {e}")

        # CHANGELOG.md jest auto-generowany przy starcie -> zawsze ma lokalne zmiany.
        _app_cwd = os.path.dirname(os.path.abspath(__file__))

        # v1.0.114: git fetch + reset --hard origin ZAMIAST git pull --autostash.
        # Caly dzien 'git pull' padal na: divergent branches, merge conflicts
        # (CHANGELOG.md), 'local changes would be overwritten'. Kazdy wymagal
        # recznego SSH. reset --hard origin = DETERMINISTYCZNE: porzuca wszystkie
        # lokalne rozbieznosci tracked plikow, ustawia dokladnie origin. NIGDY
        # nie pada na konflikt/divergent.
        # BEZPIECZENSTWO: dane klienta (akces_hub.db, .license_secret,
        # vendor_config.json, static/brand_logo.*, backups/) sa untracked /
        # .gitignore -> git reset --hard ICH NIE TYKA. Tylko kod (tracked) -> origin.
        _branch = (subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                                  capture_output=True, text=True, timeout=5, cwd=_app_cwd
                                  ).stdout.strip() or 'main')
        _head_before = subprocess.run(['git', 'rev-parse', 'HEAD'],
                                      capture_output=True, text=True, timeout=5, cwd=_app_cwd).stdout.strip()
        # 1. Fetch origin
        _fetch = subprocess.run(['git', 'fetch', 'origin', _branch],
                                capture_output=True, text=True, timeout=60, cwd=_app_cwd)
        if _fetch.returncode != 0:
            log_admin_action('system_update', {'stage': 'git_fetch'}, success=False,
                             error_message=f'Git fetch failed: {_fetch.stderr[:200]}')
            return jsonify({'ok': False, 'error': f'Git fetch failed: {_fetch.stderr[:200]}'})
        # 2. Reset --hard do origin (deterministyczne, ignoruje divergent/konflikty)
        result = subprocess.run(['git', 'reset', '--hard', f'origin/{_branch}'],
                                capture_output=True, text=True, timeout=30, cwd=_app_cwd)
        if result.returncode != 0:
            log_admin_action('system_update', {'stage': 'git_reset'}, success=False,
                             error_message=f'Git reset failed: {result.stderr[:200]}')
            return jsonify({'ok': False, 'error': f'Git reset failed: {result.stderr[:200]}'})
        _head_after = subprocess.run(['git', 'rev-parse', 'HEAD'],
                                     capture_output=True, text=True, timeout=5, cwd=_app_cwd).stdout.strip()
        pull_output = ('Already up to date' if _head_before == _head_after
                       else f'Updated {_head_before[:7]}..{_head_after[:7]}')

        if 'Already up to date' in pull_output:
            # Nawet jeśli brak nowych commitów — wymuś restart (przeładowanie modułów)
            import threading
            def _force_restart():
                import time, subprocess, sys, os
                time.sleep(2)
                # Linux/Pi: próbuj systemctl
                for svc in ['akces-hub', 'akceshub']:
                    try:
                        r = subprocess.run(['sudo', 'systemctl', 'is-enabled', svc],
                                           capture_output=True, text=True, timeout=5)
                        if r.returncode == 0:
                            subprocess.Popen(['sudo', 'systemctl', 'restart', svc],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            return
                    except:
                        pass
                # Windows / fallback: zrestartuj proces Pythona
                try:
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                except Exception:
                    pass
            log_admin_action('system_update', {'stage': 'already_up_to_date', 'restart': True}, success=True)
            # Wyczysc banner update bo nic nowego nie ma
            try:
                set_config('update_available', '0')
            except Exception:
                pass
            threading.Thread(target=_force_restart, daemon=True).start()
            return jsonify({'ok': True, 'msg': 'Już aktualne — restart za chwilę...'})

        # Pobierz info o aktualizacji (ostatni commit)
        try:
            log_result = subprocess.run(
                ['git', 'log', '-1', '--pretty=format:%s'],
                capture_output=True, text=True, timeout=5,
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            commit_msg = log_result.stdout.strip() if log_result.returncode == 0 else ''

            ver_result = subprocess.run(
                ['git', 'log', '-1', '--pretty=format:%h'],
                capture_output=True, text=True, timeout=5,
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            commit_hash = ver_result.stdout.strip() if ver_result.returncode == 0 else ''
        except:
            commit_msg = ''
            commit_hash = ''

        # Zapisz info o aktualizacji do config (do wyświetlenia na dashboardzie)
        try:
            from modules.database import set_config
            update_info = json.dumps({
                'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'commit': commit_hash,
                'message': commit_msg[:200],
                'seen': False
            })
            set_config('last_update_info', update_info)
        except:
            pass

        # Wyślij powiadomienie na Telegram
        try:
            from modules.database import get_config
            bot_token = get_config('telegram_bot_token', '')
            chat_id = get_config('telegram_chat_id', '')
            if bot_token and chat_id:
                import requests as _req
                text = (
                    f"\U0001F504 *{get_config('brand_name', 'AKCES HUB')} \u2014 Aktualizacja systemu*\n\n"
                    f"\U0001F4E6 Wersja: `{commit_hash}`\n"
                    f"\U0001F4DD {commit_msg[:150]}\n"
                    f"\U0001F4C5 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
                    f"\u2705 System restartuje si\u0119 za chwil\u0119..."
                )
                _req.post(
                    f'https://api.telegram.org/bot{bot_token}/sendMessage',
                    json={'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'},
                    timeout=5
                )
        except:
            pass

        # Wyczyść cache update check — system jest aktualny po pull
        try:
            from modules.database import set_config as _sc
            _sc('update_check_cache', json.dumps({
                'checked_at': time.time(),
                'has_update': False,
                'remote_msg': '',
                'remote_hash': commit_hash,
                'local_hash': commit_hash,
                'notified': False
            }))
            # v1.0.97 FIX: zapamietaj commit hash do _get_version()
            # Bez tego sidebar pokazywal stary commit z git rev-parse
            _sc('last_install_commit', commit_hash or '')
        except:
            pass

        # Restart serwisu z opóźnieniem 2s (żeby response zdążył dojść)
        import threading
        def _delayed_restart():
            import time, sys, os
            time.sleep(2)
            # Linux/Pi: próbuj systemctl
            for svc in ['akces-hub', 'akceshub']:
                try:
                    r = subprocess.run(['sudo', 'systemctl', 'is-enabled', svc],
                                       capture_output=True, text=True, timeout=5)
                    if r.returncode == 0:
                        subprocess.Popen(['sudo', 'systemctl', 'restart', svc],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return
                except:
                    pass
            # Windows / fallback: zrestartuj proces Pythona
            try:
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception:
                pass
        log_admin_action('system_update', {
            'stage': 'updated',
            'commit_hash': commit_hash,
            'commit_msg': commit_msg[:200],
            'pull_output': pull_output[:500],
            'restart': True,
        }, success=True)
        # FIX 2026-05-28: po udanym git pull wyczysc banner update_available
        # (analogicznie do already_up_to_date). Bez tego banner wisi do
        # nastepnego background check (max 120s + restart).
        try:
            set_config('update_available', '0')
        except Exception:
            pass
        threading.Thread(target=_delayed_restart, daemon=True).start()
        return jsonify({'ok': True, 'msg': f'Zaktualizowano! {pull_output[:100]}. Restart za chwilę...'})
    except subprocess.TimeoutExpired:
        log_admin_action('system_update', {'stage': 'timeout'}, success=False,
                         error_message='Timeout — brak polaczenia z internetem')
        return jsonify({'ok': False, 'error': 'Timeout — sprawdź połączenie z internetem'})
    except Exception as e:
        log_admin_action('system_update', {'stage': 'exception'}, success=False,
                         error_message=str(e)[:500])
        return jsonify({'ok': False, 'error': str(e)[:200]})


# ============================================================
# ZIP-BASED UPDATE (dla klient\xf3w bez gita, np. Windows)
# ============================================================

@app.route('/system/check-update-public', methods=['POST'])
@require_admin
def system_check_update_public():
    """Sprawdz nowa wersje na PUBLIC repo Trupson2/akces-hub (BEZ tokenu).

    Alternatywa dla check-update-zip ktore wymaga PRIVATE release distribution.
    Klient ZIP install bez tokenu moze updateowac tu - public repo dziala
    bez autoryzacji.
    """
    _validate_csrf_or_abort()
    from modules.zip_updater import check_public_version
    info = check_public_version(repo='Trupson2/akces-hub', branch='main', timeout=10)
    return jsonify({'ok': True, **info})


@app.route('/system/update-from-public', methods=['POST'])
@require_admin
def system_update_from_public():
    """Pobierz + zainstaluj ZIP z PUBLIC repo (bez tokenu, bez HMAC podpisu).

    UWAGA: pomija weryfikacje HMAC - publiczny ZIP nie ma podpisu vendora.
    Bezpieczenstwo: ZIP pobierany przez HTTPS z github.com (TLS chroni
    integralnosc transportu). Source code commits sa publiczne i podpisane
    przez gita (sha hash).
    """
    _validate_csrf_or_abort()
    from modules.database import log_admin_action
    from modules.zip_updater import (
        check_public_version, download_public_archive,
        install_update, restart_python_process,
    )
    import tempfile as _tf, threading as _th, time as _t

    # 1. Sprawdz wersje
    info = check_public_version(repo='Trupson2/akces-hub', branch='main', timeout=10)
    if info.get('error'):
        log_admin_action('system_update_public', {'stage': 'check'}, success=False,
                         error_message=info['error'])
        return jsonify({'ok': False, 'error': info['error']})
    if not info.get('available'):
        # Mimo to wymus restart - moze klient chce odswiezyc cache
        _th.Thread(target=lambda: (_t.sleep(2), restart_python_process()), daemon=True).start()
        return jsonify({'ok': True, 'msg': 'Już aktualne — restart za chwilę...',
                        'current': info['current'], 'latest': info['latest']})

    # 2. Pobierz ZIP
    zip_path = _tf.mktemp(suffix='.zip', prefix='akces_public_')
    ok = download_public_archive(
        repo='Trupson2/akces-hub', branch='main',
        dest_path=zip_path, timeout=180,
    )
    if not ok:
        log_admin_action('system_update_public', {'stage': 'download'}, success=False,
                         error_message='Download failed')
        return jsonify({'ok': False, 'error': 'Pobranie ZIPa z GitHub nie powiodlo sie'})

    # 3. Install (signature_hex='' = pomin HMAC - public repo nie ma podpisu)
    result = install_update(zip_path, signature_hex='')
    try:
        os.remove(zip_path)
    except Exception:
        pass

    if not result.get('ok'):
        log_admin_action('system_update_public', {'stage': 'install'}, success=False,
                         error_message=result.get('error', 'install failed'))
        return jsonify({'ok': False, 'error': result.get('error', 'Install failed')})

    log_admin_action('system_update_public',
                     {'stage': 'ok', 'files_updated': result.get('files_updated', 0),
                      'from': info['current'], 'to': info['latest']}, success=True)

    # v1.0.97 + v1.0.101 FIX: wyczysc banner + cache.
    # ZIP install nie ma latwego dostepu do git commit hash nowej wersji,
    # wiec NIE ustawiamy last_install_commit (VERSION string mylil _get_version
    # i porownanie hash w _git_update_check_async). _get_version fallback do
    # git rev-parse zwroci poprawny commit (post-extract VERSION + git HEAD
    # ze starego .git folderu - jezeli klient git install) lub pusty (ZIP only).
    try:
        from modules.database import set_config
        import json as _json
        set_config('update_available', '0')
        # NIE: set_config('last_install_commit', ...) - VERSION nie jest commit hash
        # Resetuj cache zeby bg check zrobil fresh
        _latest_ver = info.get('latest', '') or ''
        set_config('update_check_cache', _json.dumps({
            'checked_at': _t.time(),
            'has_update': False,
            'remote_msg': '',
            'remote_hash': '',  # nie znamy commit hash po ZIP, fresh bg check ustawi
            'local_hash': '',
            'notified': False,
            'is_zip': True,
        }))
    except Exception:
        pass

    # 4. Restart Pythona po 2s (zeby response zdazyl dojsc do browsera)
    _th.Thread(target=lambda: (_t.sleep(2), restart_python_process()), daemon=True).start()

    return jsonify({
        'ok': True,
        'msg': f'Zaktualizowano do {info["latest"]} ({result.get("files_updated", 0)} plikow). Restart za chwilę...',
        'from': info['current'],
        'to': info['latest'],
        'files_updated': result.get('files_updated', 0),
    })


@app.route('/system/check-update-zip', methods=['POST'])
@require_admin
def system_check_update_zip():
    """Sprawdza GitHub Releases czy jest nowa wersja zip-a.

    Wywolywane przez UI w /ustawienia (przycisk "Sprawdz aktualizacje").
    Nie modyfikuje plikow — tylko czyta GitHub API.
    """
    _validate_csrf_or_abort()
    from modules.zip_updater import check_github_release
    from modules.database import get_config
    repo = (get_config('github_release_repo', '') or '').strip()
    if not repo:
        return jsonify({
            'ok': False,
            'error': 'Brak github_release_repo w konfiguracji. Skontaktuj sie z dostawca.'
        })
    info = check_github_release(repo, timeout=10)
    return jsonify({'ok': True, **info})


@app.route('/system/update-zip', methods=['POST'])
@require_admin
def system_update_zip():
    """Pobierz + zainstaluj zip update z GitHub Releases.

    Wymaga: github_release_repo w config, podpis HMAC zip-a.
    Bezpieczenstwo:
    - @require_admin + CSRF (jak /system/update)
    - HMAC verification zip-a (LICENSE_SECRET)
    - Backup obecnej kopii pre-update
    - Restart Pythona przez wrapper bat-owy (Windows) lub execv (Linux)
    """
    _validate_csrf_or_abort()
    from modules.database import log_admin_action, get_config
    from modules.zip_updater import (
        check_github_release, download_release_zip,
        install_update, restart_python_process,
    )
    import tempfile, threading, time as _time

    repo = (get_config('github_release_repo', '') or '').strip()
    if not repo:
        log_admin_action('system_update_zip', {'stage': 'config'}, success=False,
                         error_message='Brak github_release_repo')
        return jsonify({'ok': False, 'error': 'Brak github_release_repo w konfiguracji'})

    # 1. Sprawdz release
    info = check_github_release(repo, timeout=10)
    if info.get('error'):
        log_admin_action('system_update_zip', {'stage': 'check'}, success=False,
                         error_message=info['error'])
        return jsonify({'ok': False, 'error': info['error']})
    if not info.get('available'):
        return jsonify({'ok': True, 'msg': 'Juz aktualne', 'restart': False})

    # 2. Pobierz zip + signature
    tmpdir = tempfile.mkdtemp(prefix='akces_dl_')
    zip_path = os.path.join(tmpdir, 'release.zip')
    if not download_release_zip(info['download_url'], zip_path, timeout=180):
        log_admin_action('system_update_zip', {'stage': 'download'}, success=False,
                         error_message='Download failed')
        return jsonify({'ok': False, 'error': 'Pobieranie zip nie powiodlo sie'})

    sig_hex = ''
    if info.get('signature_url'):
        sig_path = os.path.join(tmpdir, 'release.zip.sig')
        if download_release_zip(info['signature_url'], sig_path, timeout=30):
            try:
                with open(sig_path, 'r', encoding='utf-8') as f:
                    sig_hex = f.read().strip().split()[0]  # plik moze byc "hex  filename"
            except Exception:
                pass

    # 3. Install (verify + backup + extract)
    result = install_update(zip_path, signature_hex=sig_hex)
    if not result.get('ok'):
        log_admin_action('system_update_zip', {'stage': 'install', **result}, success=False,
                         error_message=result.get('error', ''))
        return jsonify({'ok': False, 'error': result.get('error', 'Install failed')})

    log_admin_action('system_update_zip', {
        'stage': 'updated',
        'latest': info.get('latest', ''),
        'files_updated': result.get('files_updated', 0),
        'backup_path': result.get('backup_path', ''),
        'restart': True,
    }, success=True)

    # 4. Telegram notify (jak dla git-update)
    try:
        bot_token = get_config('telegram_bot_token', '')
        chat_id = get_config('telegram_chat_id', '')
        if bot_token and chat_id:
            import requests as _req
            text = (
                f"\U0001F504 *{get_config('brand_name', 'AKCES HUB')} — Aktualizacja*\n\n"
                f"\U0001F4E6 Wersja: `{info.get('latest', '?')}`\n"
                f"\U0001F4DD Plik\xf3w zaktualizowanych: {result.get('files_updated', 0)}\n"
                f"✅ Restart za chwilę..."
            )
            _req.post(
                f'https://api.telegram.org/bot{bot_token}/sendMessage',
                json={'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'},
                timeout=5
            )
    except Exception:
        pass

    # 5. Restart Pythona (po 3s zeby response dotarl + browser pokazal toast)
    threading.Thread(
        target=lambda: (_time.sleep(3), restart_python_process()),
        daemon=True,
    ).start()

    return jsonify({
        'ok': True,
        'msg': f'Zaktualizowano do {info.get("latest", "?")}. Restart za chwile...',
        'files_updated': result.get('files_updated', 0),
        'restart': True,
    })


# ============================================================
# ONBOARDING — kreator pierwszej konfiguracji
# ============================================================

def _setup_endpoint_guard():
    """v1.0.94 SECURITY (K1+K4): chroni /setup/* przed unauthorized writes.

    Logika:
    - Pierwsza konfiguracja (brak userow): dopusc TYLKO z localhost (127.0.0.1).
      Bez tego ktokolwiek w LAN moglby wyscignac klienta do setup i podmienic
      branding/logo lub zaloyzc admin konto na siebie.
    - Po pierwszym setupie: wymaga admin sesji (jak require_admin).

    Returns: None gdy OK, Response gdy block.
    """
    from modules.auth import _has_any_users
    if not _has_any_users():
        # First-setup phase - tylko localhost
        _real_ip = request.remote_addr or ''
        if _real_ip not in ('127.0.0.1', '::1', 'localhost'):
            from flask import abort
            abort(403, 'First setup tylko z localhost (race protection)')
        return None
    # Po pierwszym setupie - wymaga admin
    if not session.get('user_id'):
        if request.method != 'GET' or 'application/json' in (request.content_type or ''):
            return jsonify({'ok': False, 'error': 'Wymagane logowanie'}), 401
        return redirect(url_for('auth.login', next='/setup'))
    if (session.get('rola') or '').lower() != 'admin':
        if 'application/json' in (request.content_type or '') or request.method != 'GET':
            return jsonify({'ok': False, 'error': 'Tylko admin'}), 403
        from flask import abort
        abort(403)
    return None


@app.route('/setup')
def setup_wizard():
    """Wizard po pierwszym logowaniu — branding, moduły, API"""
    _block = _setup_endpoint_guard()
    if _block is not None:
        return _block
    from modules.database import get_config
    return render_template('setup.html',
        version=VERSION,
        current_brand=get_config('brand_name', 'AKCES HUB'),
        current_color=get_config('brand_color', '#6366f1'),
        has_allegro=bool(get_config('allegro_client_id', '')),
        has_telegram=bool(get_config('telegram_bot_token', '')),
    )

@app.route('/setup/save', methods=['POST'])
def setup_save():
    """Zapisz konfigurację z wizarda"""
    _block = _setup_endpoint_guard()
    if _block is not None:
        return _block
    from modules.database import set_config
    data = request.get_json() or {}
    if data.get('brand_name'):
        set_config('brand_name', data['brand_name'][:50])
    if data.get('brand_color'):
        set_config('brand_color', data['brand_color'][:7])
    set_config('setup_done', '1')
    return jsonify({'ok': True})

@app.route('/setup/logo', methods=['POST'])
def setup_logo():
    """Upload logo z wizarda"""
    _block = _setup_endpoint_guard()
    if _block is not None:
        return _block
    from PIL import Image
    f = request.files.get('logo')
    if not f:
        return jsonify({'ok': False, 'error': 'Brak pliku'}), 400
    if f.content_length and f.content_length > 512000:
        return jsonify({'ok': False, 'error': 'Za duzy plik'}), 400
    try:
        fname = f.filename or 'logo.png'
        ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else 'png'
        if ext == 'svg':
            # SVG sanitization — odrzuc XSS payloady przed zapisem
            from modules.utils import sanitize_svg
            try:
                f.stream.seek(0)
            except Exception:
                pass
            svg_content = f.read()
            is_safe, reason = sanitize_svg(svg_content)
            if not is_safe:
                # Audit log — proba ataku
                try:
                    from modules.database import log_admin_action
                    log_admin_action(
                        'logo_upload',
                        {'attack': 'svg_xss', 'reason': reason, 'filename': fname[:100]},
                        success=False,
                        error_message=f'SVG odrzucony: {reason}'
                    )
                except Exception:
                    pass
                return jsonify({'ok': False, 'error': f'SVG odrzucony (potencjalny XSS): {reason}'}), 400
            # Bezpieczny SVG — zapisz pod kontrolowana nazwa (NIE z user-controlled filename)
            from werkzeug.utils import secure_filename
            _ = secure_filename(fname)  # walidacja nazwy (efekt uboczny — nie uzywamy bo nazwa stala)
            logo_name = 'brand_logo.svg'
            logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', logo_name)
            with open(logo_path, 'wb') as out:
                out.write(svg_content)
        else:
            # Raster — resize i zapisz jako PNG
            img = Image.open(f.stream)
            if img.height > 200:
                ratio = 200 / img.height
                img = img.resize((int(img.width * ratio), 200), Image.LANCZOS)
            logo_name = 'brand_logo.png'
            logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', logo_name)
            img.save(logo_path, 'PNG', optimize=True)
        from modules.database import set_config
        set_config('brand_logo', logo_name)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:100]}), 500

# ============================================================
# LICENCJA — aktywacja i status
# ============================================================
@app.route('/license', methods=['GET', 'POST'])
def license_page():
    """Strona aktywacji licencji"""
    from modules.license import get_license_display, activate_license
    import json as _json

    msg = ''
    err = ''

    # Komunikat o blokadzie heartbeat
    if request.args.get('blocked') == '1':
        err = 'Licencja zostala dezaktywowana'

    if request.method == 'POST':
        # Aktywacja z pliku JSON lub ręcznie
        if 'license_file' in request.files:
            f = request.files['license_file']
            if f and f.filename:
                try:
                    data = _json.load(f)
                    ok, result = activate_license(
                        data['key'], data['client'], data['plan'],
                        data['created'], data['expires'], data['signature']
                    )
                    if ok:
                        msg = result
                    else:
                        err = result
                except Exception as e:
                    err = f'Nieprawidlowy plik licencji: {str(e)[:100]}'
        else:
            # Aktywacja ręczna z formularza
            try:
                key = request.form.get('key', '').strip()
                client = request.form.get('client', '').strip()
                plan = request.form.get('plan', '').strip()
                created = request.form.get('created', '').strip()
                expires = request.form.get('expires', '').strip()
                signature = request.form.get('signature', '').strip()

                if created and signature:
                    # DEV mode: pełna aktywacja z wszystkimi polami
                    ok, result = activate_license(key, client, plan or 'pro', int(created), int(expires or 0), signature)
                else:
                    # Klient: aktywacja kluczem przez serwer licencyjny.
                    # Gdy license_server_url NIE skonfigurowany (default) → wymaga
                    # pełnych pól (sig+created+expires) jako manual offline activation.
                    from modules.database import get_config as _get_config
                    _lic_server = (_get_config('license_server_url', '') or '').strip().rstrip('/')
                    if not _lic_server:
                        ok, result = False, ('Server licencyjny nie skonfigurowany. '
                                             'Skontaktuj się z dostawcą o klucz z pełną sygnaturą '
                                             '(key+client+plan+created+expires+signature), '
                                             'lub set_config(license_server_url, https://twoj-serwer)')
                    else:
                        try:
                            import requests as _rq
                            from modules.license import get_hwid
                            _hwid = get_hwid()
                            _resp = _rq.post(f'{_lic_server}/api/license/verify',
                                json={'key': key, 'hwid': _hwid, 'timestamp': __import__('datetime').datetime.now().isoformat(),
                                      'version': VERSION}, timeout=15)
                            _data = _resp.json()
                            if _data.get('valid'):
                                # Serwer potwierdził — aktywuj lokalnie
                                import time as _t, hmac as _hm, hashlib as _hl
                                _created = int(_t.time())
                                _exp = _data.get('expires_timestamp', 0)
                                _plan = _data.get('plan', 'pro')
                                from modules.license import LICENSE_SECRET as _secret
                                _sig_data = f"{key}|{client}|{_plan}|{_created}|{_exp}"
                                _sig = _hm.new(_secret.encode(), _sig_data.encode(), _hl.sha256).hexdigest()[:16]
                                ok, result = activate_license(key, client, _plan, _created, _exp, _sig)
                            else:
                                ok, result = False, _data.get('error', 'Nieprawidlowy klucz licencyjny')
                        except Exception as _e:
                            ok, result = False, f'Nie mozna zweryfikowac klucza. Sprawdz polaczenie z internetem. ({str(_e)[:80]})'

                if ok:
                    msg = result
                else:
                    err = result
            except Exception as e:
                err = f'Blad aktywacji: {str(e)[:100]}'

    lic = get_license_display()

    try:
        return render_template('license.html', lic=lic, msg=msg, err=err,
            is_dev=_is_dev_mode(), brand_name=app.config.get('BRAND_NAME', 'Akces Hub'))
    except Exception:
        return render_template_string('<html><body style="background:#0a0a14;color:#e2e8f0;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh"><div style="text-align:center"><h1>Licencja</h1><p style="color:#94a3b8">{{ msg or err or "Aktywuj licencje" }}</p></div></body></html>', msg=msg, err=err)


# ============================================================
# SUBSCRIPTION EXPIRED — ekran wygasłej subskrypcji
# ============================================================
@app.route('/subscription-expired')
def subscription_expired_page():
    """Standalone strona — subskrypcja wygasła"""
    from modules.license import get_license_info, get_days_remaining, get_hwid
    from modules.database import get_config
    lic = get_license_info()
    plan_name = ''
    expiry_str = ''
    hwid = ''
    renew_url = get_config('subscription_renew_url', 'https://paletomat.app/odnow')

    if lic:
        plan_raw = (lic.get('plan', '') or '').upper()
        plan_labels = {'STARTER': 'TRIAL', 'TRIAL': 'TRIAL', 'PRO': 'PRO', 'BUSINESS': 'MAX', 'MAX': 'MAX', 'ENTERPRISE': 'ENTERPRISE'}
        plan_name = plan_labels.get(plan_raw, plan_raw)
        expires_ts = lic.get('expires', 0)
        if expires_ts and expires_ts > 0:
            from datetime import datetime
            expiry_str = datetime.fromtimestamp(expires_ts).strftime('%d.%m.%Y')
        expiry_date = lic.get('expiry_date', '')
        if expiry_date:
            try:
                from datetime import datetime as _dt
                expiry_str = _dt.strptime(expiry_date, '%Y-%m-%d').strftime('%d.%m.%Y')
            except (ValueError, TypeError):
                pass
        hwid = lic.get('hwid', '') or ''

    if not hwid:
        try:
            hwid = get_hwid()
        except Exception:
            hwid = ''

    try:
        return render_template('subscription_expired.html', plan_name=plan_name, expiry_str=expiry_str, hwid=hwid, renew_url=renew_url)
    except Exception:
        return render_template_string('<html><body style="background:#0a0a14;color:#e2e8f0;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh"><div style="text-align:center"><h1 style="color:#f87171">Subskrypcja wygasla</h1><p style="color:#94a3b8">Odnow licencje.</p><a href="/license" style="color:#6366f1">Aktywuj &rarr;</a></div></body></html>')


# ============================================================
# TIME MANIPULATION — wykryto manipulację czasem
# ============================================================
@app.route('/time-manipulation')
def time_manipulation_page():
    """Standalone strona — wykryto manipulację czasem systemowym"""
    try:
        return render_template('time_manipulation.html')
    except Exception:
        return render_template_string('<html><body style="background:#0a0a14;color:#e2e8f0;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh"><div style="text-align:center"><h1 style="color:#f87171">Wykryto manipulacje czasem</h1><p style="color:#94a3b8">Ustaw prawidlowa date i godzine.</p><a href="/dashboard" style="color:#6366f1">Sprobuj ponownie</a></div></body></html>')


# ============================================================
# UPGRADE LICENCJI DO ENTERPRISE
# ============================================================
@app.route('/license/upgrade-enterprise', methods=['POST'])
def license_upgrade_enterprise():
    """Upgrade aktualnej licencji do planu Enterprise — tylko dev"""
    _tools_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
    if session.get('rola') != 'admin' or not os.path.isdir(_tools_path):
        return 'Brak dostepu', 403
    from modules.license import get_license_info, generate_license_key, activate_license
    lic = get_license_info()
    if not lic:
        return redirect('/license')
    # Wygeneruj nową licencję enterprise z tym samym klientem i czasem
    client = lic.get('client', 'Klient')
    # Oblicz ile miesięcy zostało
    expires = lic.get('expires', 0)
    new_lic = generate_license_key(client, 'enterprise', months=0)
    if expires > 0:
        import time as _t
        new_lic['expires'] = expires  # Zachowaj oryginalny expiry
        # Przelicz sygnaturę z nowym planem
        import hmac as _hmac, hashlib as _hl
        from modules.license import LICENSE_SECRET
        plan_code = 'E'
        payload = f"{client}|{plan_code}|{new_lic['created']}|{expires}"
        sig = _hmac.new(LICENSE_SECRET.encode(), payload.encode(), _hl.sha256).hexdigest()
        sig_short = sig[:16].upper()
        new_lic['key'] = f"AKCES-{plan_code}{sig_short[:3]}-{sig_short[3:7]}-{sig_short[7:11]}-{sig_short[11:15]}"
        new_lic['signature'] = sig[:32]
    ok, msg = activate_license(new_lic['key'], new_lic['client'], new_lic['plan'], new_lic['created'], new_lic['expires'], new_lic['signature'])
    return redirect('/license')


# ============================================================
# GENERATOR LICENCJI — panel admina
# ============================================================
@app.route('/narzedzia/licencje/delete', methods=['POST'])
def narzedzia_licencje_delete():
    """Usuń wygenerowaną licencję (plik .json + DB licenses_issued).

    Dev-only (sama generator UI). Bezpieczne — nie wpływa na aktywną licencję
    Twojego Hub'a (ta jest w config table, nie w licenses_issued).
    """
    from modules.database import get_config
    is_dev = get_config('is_dev', '0') == '1'
    if session.get('rola') != 'admin' or not is_dev:
        return 'Brak dostepu', 403

    filename = (request.form.get('filename') or '').strip()
    license_key = (request.form.get('key') or '').strip()

    # Safety: filename musi zaczynac sie 'license_' i konczyc '.json'.
    # Akceptujemy SPACJE i polskie znaki (np. 'license_Adrian Gauza_max.json').
    # Path traversal blokuje os.path.abspath() check ponizej.
    import re as _re
    # Tylko podstawowy sanity check + brak '..' i sciezki
    deleted_file = False
    safe_filename = filename and filename.startswith('license_') and filename.endswith('.json') \
                    and '..' not in filename and '/' not in filename and '\\' not in filename
    if safe_filename:
        _tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
        fpath = os.path.join(_tools_dir, filename)
        # Path traversal guard (final check)
        if os.path.abspath(fpath).startswith(os.path.abspath(_tools_dir) + os.sep):
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                    deleted_file = True
                except Exception:
                    pass

    # Usuń też z licenses_issued (server-side tracking)
    deleted_db = False
    if license_key:
        try:
            from modules.database import get_db
            _db = get_db()
            cur = _db.execute('DELETE FROM licenses_issued WHERE license_key = ?', (license_key,))
            _db.commit()
            deleted_db = (cur.rowcount > 0)
        except Exception:
            pass

    return redirect(f'/narzedzia/licencje?deleted={"1" if (deleted_file or deleted_db) else "0"}')


@app.route('/narzedzia/licencje', methods=['GET', 'POST'])
def narzedzia_licencje():
    """Panel generowania licencji — dostepny tylko dla dev"""
    from modules.database import get_config
    is_dev = get_config('is_dev', '0') == '1'
    if session.get('rola') != 'admin' or not is_dev:
        return 'Brak dostepu', 403
    from modules.license import generate_license_key
    from datetime import datetime
    import json as _json

    if request.method == 'POST':
        client = request.form.get('client', '').strip()
        plan = request.form.get('plan', 'pro')
        duration_type = request.form.get('duration_type', 'months')
        duration_val = int(request.form.get('duration_val', 1) or 1)

        if not client:
            client = 'Klient'

        if duration_type == 'days':
            # Ręcznie ustawiamy expires
            import time as _time
            created = int(_time.time())
            expires = created + (duration_val * 24 * 3600)
            plan_code = {'trial': 'T', 'pro': 'P', 'max': 'M', 'enterprise': 'E'}.get(plan, 'P')
            import hmac as _hmac, hashlib as _hl
            from modules.license import LICENSE_SECRET
            payload = f"{client}|{plan_code}|{created}|{expires}"
            sig = _hmac.new(LICENSE_SECRET.encode(), payload.encode(), _hl.sha256).hexdigest()
            sig_short = sig[:16].upper()
            key = f"AKCES-{plan_code}{sig_short[:3]}-{sig_short[3:7]}-{sig_short[7:11]}-{sig_short[11:15]}"
            generated = {
                'key': key, 'client': client, 'plan': plan,
                'created': created, 'expires': expires, 'signature': sig[:32]
            }
        elif duration_type == 'unlimited':
            generated = generate_license_key(client, plan, months=0)
        else:
            generated = generate_license_key(client, plan, months=duration_val)

        generated_json = _json.dumps(generated, indent=2, ensure_ascii=False)

        # Zapisz do pliku
        _safe_name = client.replace(' ', '_').lower()
        _fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools', f'license_{_safe_name}_{plan}.json')
        try:
            with open(_fpath, 'w', encoding='utf-8') as _f:
                _f.write(generated_json)
        except:
            pass

        # Zapisz do tabeli licenses_issued (serwer licencji)
        try:
            from modules.database import get_db
            _db = get_db()
            _db.execute('''INSERT OR REPLACE INTO licenses_issued
                (license_key, client_name, plan, expires, active, created_at)
                VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)''',
                (generated.get('key', ''), client, plan, generated.get('expires', 0)))
            _db.commit()
        except Exception:
            pass

        # PRG: redirect żeby F5 nie generowało nowej licencji
        flash_key = generated.get('key', '')
        return redirect(f'/narzedzia/licencje?generated={flash_key}')

    # GET handler
    _tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
    existing = []
    generated = None
    generated_json = ''
    flash_key = request.args.get('generated', '')

    # Pobierz dane heartbeat/HWID z licenses_issued
    _issued_map = {}
    try:
        from modules.database import get_db as _gdb
        _irows = _gdb().execute('SELECT license_key, hwid, last_heartbeat, active FROM licenses_issued').fetchall()
        for _ir in _irows:
            _issued_map[_ir['license_key']] = {
                'hwid': _ir['hwid'] or '',
                'last_heartbeat': _ir['last_heartbeat'] or '',
                'active': bool(_ir['active'])
            }
    except Exception:
        pass

    try:
        for fn in sorted(os.listdir(_tools_dir)):
            if fn.startswith('license_') and fn.endswith('.json'):
                fpath = os.path.join(_tools_dir, fn)
                try:
                    with open(fpath, 'r', encoding='utf-8') as _f:
                        data = _json.load(_f)
                    exp_str = 'Bezterminowo'
                    if data.get('expires', 0) > 0:
                        exp_str = datetime.fromtimestamp(data['expires']).strftime('%d.%m.%Y')
                    _iss = _issued_map.get(data.get('key', ''), {})
                    lic_entry = {
                        'filename': fn,
                        'client': data.get('client', '?'),
                        'plan': data.get('plan', '?'),
                        'key': data.get('key', '?'),
                        'expires': exp_str,
                        'hwid': _iss.get('hwid', ''),
                        'last_heartbeat': _iss.get('last_heartbeat', ''),
                        'srv_active': _iss.get('active', None),
                        'json': _json.dumps(data, indent=2, ensure_ascii=False)
                    }
                    existing.append(lic_entry)
                    # If this matches the just-generated key, show it
                    if flash_key and data.get('key') == flash_key:
                        generated = data
                        generated_json = _json.dumps(data, indent=2, ensure_ascii=False)
                except:
                    pass
    except:
        pass

    generated_expires = ''
    if generated:
        generated_expires = (
            datetime.fromtimestamp(generated['expires']).strftime('%d.%m.%Y')
            if generated.get('expires', 0) > 0 else 'Bezterminowo'
        )

    return render_template('licencje.html',
        generated=generated,
        generated_json=generated_json,
        generated_expires=generated_expires,
        existing=existing,
        version=app.config.get('VERSION', ''),
        brand_name=app.config.get('BRAND_NAME', 'Akces Hub'),
        current_user=session.get('username')
    )


# ============================================================
# ADMIN: ZARZĄDZANIE SUBSKRYPCJAMI
# ============================================================
def _get_server_hwid():
    try:
        from modules.license import get_hwid
        return get_hwid()
    except:
        return 'N/A'

@app.route('/admin/subscriptions')
def admin_subscriptions():
    """Panel zarządzania subskrypcjami — tylko dla dev"""
    from modules.database import get_config, get_db
    is_dev = get_config('is_dev', '0') == '1'
    if session.get('rola') != 'admin' or not is_dev:
        return 'Brak dostepu', 403

    conn = get_db()
    try:
        rows = conn.execute('''SELECT id, license_key, client_name, plan, hwid, expires,
            active, last_heartbeat, created_at, expires_date FROM licenses_issued ORDER BY created_at DESC''').fetchall()
    except Exception:
        # Fallback: kolumna expires_date może jeszcze nie istnieć
        rows = conn.execute('''SELECT id, license_key, client_name, plan, hwid, expires,
            active, last_heartbeat, created_at, '' as expires_date FROM licenses_issued ORDER BY created_at DESC''').fetchall()

    licenses = []
    for r in rows:
        key = r['license_key'] or ''
        masked = key[:4] + '****' + key[-4:] if len(key) > 8 else key
        exp_str = ''
        if r['expires_date']:
            exp_str = r['expires_date']
        elif r['expires'] and str(r['expires']).isdigit() and int(r['expires']) > 0:
            from datetime import datetime
            try:
                exp_str = datetime.fromtimestamp(int(r['expires'])).strftime('%Y-%m-%d')
            except (ValueError, TypeError, OSError):
                exp_str = str(r['expires'])
        else:
            exp_str = 'Bezterminowo'

        plan_raw = (r['plan'] or 'pro').upper()
        plan_labels = {'STARTER': 'TRIAL', 'TRIAL': 'TRIAL', 'PRO': 'PRO', 'BUSINESS': 'MAX', 'MAX': 'MAX', 'ENTERPRISE': 'ENTERPRISE'}
        plan_display = plan_labels.get(plan_raw, plan_raw)

        licenses.append({
            'id': r['id'],
            'key': key,
            'masked_key': masked,
            'client': r['client_name'] or '',
            'plan': plan_display,
            'plan_raw': r['plan'] or 'pro',
            'hwid': r['hwid'] or '',
            'expires': exp_str,
            'active': bool(r['active']),
            'last_heartbeat': r['last_heartbeat'] or '-',
            'created_at': r['created_at'] or '',
        })

    return render_template('admin_subscriptions.html', licenses=licenses, server_hwid=_get_server_hwid())


@app.route('/admin/subscriptions/update', methods=['POST'])
def admin_subscriptions_update():
    """Aktualizuj subskrypcję — plan, expiry, active"""
    from modules.database import get_config, get_db
    is_dev = get_config('is_dev', '0') == '1'
    if session.get('rola') != 'admin' or not is_dev:
        return 'Brak dostepu', 403

    license_key = request.form.get('license_key', '').strip()
    new_plan = request.form.get('new_plan', '').strip()
    new_expiry_date = request.form.get('new_expiry_date', '').strip()
    active = request.form.get('active', '1').strip()
    extend_30 = request.form.get('extend_30', '')

    if not license_key:
        return redirect('/admin/subscriptions')

    conn = get_db()

    if extend_30:
        # Przedłuż o 30 dni od dzisiaj lub od aktualnego expiry
        from datetime import datetime, timedelta
        row = conn.execute('SELECT expires, expires_date FROM licenses_issued WHERE license_key = ?', (license_key,)).fetchone()
        base_date = datetime.now().date()
        if row:
            if row['expires_date']:
                try:
                    d = datetime.strptime(row['expires_date'], '%Y-%m-%d').date()
                    if d > base_date:
                        base_date = d
                except (ValueError, TypeError):
                    pass
            elif row['expires'] and str(row['expires']).isdigit() and int(row['expires']) > 0:
                try:
                    d = datetime.fromtimestamp(int(row['expires'])).date()
                    if d > base_date:
                        base_date = d
                except (ValueError, TypeError, OSError):
                    pass
        new_date = base_date + timedelta(days=30)
        new_ts = int(new_date.strftime('%s')) if hasattr(new_date, 'strftime') else 0
        try:
            import time as _t
            from datetime import datetime as _dt2
            new_ts = int(_dt2.combine(new_date, _dt2.min.time()).timestamp())
        except Exception:
            new_ts = 0
        conn.execute('UPDATE licenses_issued SET expires_date = ?, expires = ?, active = 1 WHERE license_key = ?',
                     (new_date.strftime('%Y-%m-%d'), new_ts, license_key))
        conn.commit()
        return redirect('/admin/subscriptions')

    # Standardowa aktualizacja — whitelist dozwolonych kolumn (ochrona przed SQL injection)
    _ALLOWED_COLS = {'plan', 'expires_date', 'expires', 'active'}
    updates = []
    params = []
    if new_plan:
        updates.append('plan = ?')
        params.append(new_plan)
    if new_expiry_date:
        updates.append('expires_date = ?')
        params.append(new_expiry_date)
        # Też aktualizuj timestamp expires
        try:
            from datetime import datetime
            d = datetime.strptime(new_expiry_date, '%Y-%m-%d')
            updates.append('expires = ?')
            params.append(int(d.timestamp()))
        except (ValueError, TypeError):
            pass
    updates.append('active = ?')
    params.append(int(active))
    params.append(license_key)

    if updates:
        # Walidacja: każdy update musi być z whitelisty
        for u in updates:
            col_name = u.split(' ')[0]
            if col_name not in _ALLOWED_COLS:
                return 'Nieprawidlowa kolumna', 400
        conn.execute(f'UPDATE licenses_issued SET {", ".join(updates)} WHERE license_key = ?', params)
        conn.commit()

    return redirect('/admin/subscriptions')


# ============================================================
# LICENSE HEARTBEAT SERVER ENDPOINT
# ============================================================
@app.route('/api/license/verify', methods=['POST'])
def api_license_verify():
    """Endpoint serwera licencji — weryfikacja heartbeat od klientów."""
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'valid': False, 'error': 'Brak danych'}), 400

        key = data.get('key', '')
        hwid = data.get('hwid', '')

        if not key:
            return jsonify({'valid': False, 'error': 'Brak klucza'}), 400

        from modules.database import get_db
        conn = get_db()

        row = conn.execute(
            'SELECT id, license_key, hwid, active, expires FROM licenses_issued WHERE license_key = ?',
            (key,)
        ).fetchone()

        if not row:
            # Auto-rejestracja: klucz z poprawnym formatem AKCES-XXXX-XXXX-XXXX-XXXX
            import re
            if key and re.match(r'^AKCES-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$', key):
                _client = data.get('client', 'Auto-registered')
                _plan = data.get('plan', 'pro')
                conn.execute('''INSERT INTO licenses_issued
                    (license_key, client_name, plan, hwid, active, created_at)
                    VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)''',
                    (key, _client, _plan, hwid))
                conn.commit()
                return jsonify({'valid': True, 'registered': True})
            return jsonify({'valid': False, 'error': 'Klucz nie istnieje'})

        # Sprawdź czy aktywna
        if not row['active']:
            return jsonify({'valid': False, 'error': 'Licencja nieaktywna'})

        # Sprawdź wygaśnięcie
        if row['expires'] and row['expires'] > 0 and time.time() > row['expires']:
            return jsonify({'valid': False, 'error': 'Licencja wygasla'})

        # Sprawdź HWID — jeśli zapisany, musi się zgadzać
        stored_hwid = row['hwid'] or ''
        if stored_hwid and hwid and stored_hwid != hwid:
            return jsonify({'valid': False, 'error': 'HWID mismatch'})

        # Jeśli brak HWID w bazie, zapisz pierwszy
        if not stored_hwid and hwid:
            conn.execute('UPDATE licenses_issued SET hwid = ? WHERE id = ?', (hwid, row['id']))

        # Aktualizuj last_heartbeat
        from datetime import datetime
        conn.execute(
            'UPDATE licenses_issued SET last_heartbeat = ? WHERE id = ?',
            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), row['id'])
        )
        conn.commit()

        return jsonify({
            'valid': True,
            'plan': row['plan'] if row else 'pro',
            'expires_timestamp': row['expires'] if row else 0
        })

    except Exception as e:
        print(f"[ERR] License verify: {e}")
        return jsonify({'valid': False, 'error': 'Błąd weryfikacji'}), 500


# ============================================================
# VENDOR NOTIFY PROXY — klient woła ten endpoint na Pi vendora
# (akceshub.com) bez tokenow. Vendor routuje do swojego TG.
# W1 mitigation: klient nigdy nie ma vendor_bot_token.
# ============================================================
# v1.0.95 W1 mitigation. Aktywny tylko na Pi vendora (Adriana).
# U klienta endpoint istnieje ale nie ma sensu wywolywac go u siebie.
# Rate-limit: max 100 wiadomosci/dzien per license_key (anti-spam).
_vendor_notify_counters = {}  # {license_key: [count, day_str]}

@app.route('/api/vendor-notify', methods=['POST'])
def api_vendor_notify():
    """Proxy endpoint dla notify od klientow do vendora (Adrian).

    Klient wola: POST /api/vendor-notify {license_key, client, message, parse_mode}
    Walidacja:
    - license_key musi istniec w licenses_issued (klient zarejestrowany)
    - rate-limit 100/day per license
    Vendor (akceshub.com) routuje do swojego TG botu (bot token niewidoczny dla klienta).

    NIE ma _csrf_exempt — jest w /api/v1/* whitelist? sprawdzic.
    Nie wymaga sesji ale wymaga valid license_key.
    """
    try:
        data = request.get_json(silent=True) or {}
        license_key = (data.get('license_key') or '').strip()[:64]
        client_name = (data.get('client') or 'unknown')[:50]
        message = (data.get('message') or '')[:4000]
        parse_mode = (data.get('parse_mode') or 'HTML').strip()[:10]
        if parse_mode not in ('HTML', 'Markdown', 'MarkdownV2'):
            parse_mode = 'HTML'

        if not license_key or not message:
            return jsonify({'ok': False, 'error': 'Brak license_key lub message'}), 400

        # Walidacja licencji
        from modules.database import get_db
        conn = get_db()
        row = conn.execute(
            'SELECT id, active FROM licenses_issued WHERE license_key = ?',
            (license_key,)
        ).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Nieznana licencja'}), 401
        if not row['active']:
            return jsonify({'ok': False, 'error': 'Licencja nieaktywna'}), 403

        # Rate-limit: max 100/day per license
        import time
        _today = time.strftime('%Y-%m-%d')
        _state = _vendor_notify_counters.get(license_key, [0, _today])
        if _state[1] != _today:
            _state = [0, _today]
        if _state[0] >= 100:
            return jsonify({'ok': False, 'error': 'Rate limit dzienny (100/dzien)'}), 429
        _state[0] += 1
        _vendor_notify_counters[license_key] = _state

        # Routing do TG vendora (Adrian na swoim Pi - bot token w jego config)
        from modules.database import get_config
        vendor_bot_token = get_config('telegram_bot_token', '')
        vendor_chat_id = get_config('telegram_chat_id', '')
        if not vendor_bot_token or not vendor_chat_id:
            # Vendor nie skonfigurowal TG - logujemy ale nie failujemy klienta
            print(f"[vendor-notify] WARN: vendor nie ma TG ({client_name}: {message[:60]})")
            return jsonify({'ok': True, 'fallback': 'logged_only'})

        import requests as _req
        _full_msg = f"[{client_name}] {message}"[:4096]
        try:
            r = _req.post(
                f'https://api.telegram.org/bot{vendor_bot_token}/sendMessage',
                json={'chat_id': vendor_chat_id, 'text': _full_msg, 'parse_mode': parse_mode},
                timeout=10
            )
            if r.status_code == 200:
                return jsonify({'ok': True})
            return jsonify({'ok': False, 'error': f'TG HTTP {r.status_code}'}), 502
        except Exception as e:
            print(f"[vendor-notify] TG send error: {e}")
            return jsonify({'ok': False, 'error': 'TG send failed'}), 502

    except Exception as e:
        print(f"[ERR] vendor-notify: {e}")
        return jsonify({'ok': False, 'error': 'Server error'}), 500


# ============================================================
# CHANGELOG — historia zmian po polsku
# ============================================================
@app.route('/cennik')
def cennik():
    """Strona cennika planów"""
    from modules.plan_features import PLAN_DISPLAY, get_current_plan
    current = PLAN_DISPLAY.get(get_current_plan(), 'TRIAL')
    return render_template('plan_upgrade.html', required_plan='', current_plan=current, path='/cennik')


@app.route('/changelog')
def changelog():
    """Changelog: structured git commit log grouped by date."""
    from collections import OrderedDict
    days = OrderedDict()
    total_commits = 0
    try:
        import subprocess as _sp
        _cwd = os.path.dirname(os.path.abspath(__file__))
        r = _sp.run(
            ['git', 'log', '--format=%H|%s|%ai', '--no-merges', '-50'],
            capture_output=True, text=True, timeout=10, cwd=_cwd
        )
        if r.returncode == 0 and r.stdout.strip():
            for line in r.stdout.strip().split('\n'):
                parts = line.split('|', 2)
                if len(parts) < 3:
                    continue
                commit_hash, msg, date_str = parts
                if 'Co-Authored' in msg:
                    continue
                day = date_str[:10]
                short_hash = commit_hash[:7]
                # Determine type for color-coding
                msg_lower = msg.lower()
                if msg_lower.startswith('fix') or 'fix' in msg_lower.split()[:2]:
                    ctype = 'fix'
                elif msg_lower.startswith('add') or msg_lower.startswith('nowe') or msg_lower.startswith('implement'):
                    ctype = 'add'
                elif msg_lower.startswith('redesign') or 'redesign' in msg_lower:
                    ctype = 'redesign'
                else:
                    ctype = 'other'
                if day not in days:
                    days[day] = []
                days[day].append({
                    'hash': short_hash,
                    'message': msg,
                    'date': date_str.strip(),
                    'type': ctype,
                })
                total_commits += 1
    except Exception:
        pass

    return render_template('changelog.html',
                           days=days, total=total_commits, version=VERSION)

def _is_dev_mode():
    try:
        from modules.database import get_config
        return session.get('rola') == 'admin' and get_config('is_dev', '0') == '1'
    except Exception:
        return False

@app.route('/narzedzia')
def narzedzia():
    # Plan features — przekaż do szablonu info o zablokowanych funkcjach
    plan_level = 4
    current_plan = 'enterprise'
    try:
        from modules.plan_features import get_plan_level, get_current_plan
        current_plan = get_current_plan()
        plan_level = get_plan_level(current_plan)
    except Exception:
        pass  # Brak modułu lub błąd = pokaż wszystko
    from modules.database import get_config
    gemini_model = get_config('gemini_model', 'gemini-2.5-flash')
    # Detect install method (git vs zip) — wplywa na sciezke update w UI
    _app_dir = os.path.dirname(os.path.abspath(__file__))
    is_zip_install = not os.path.isdir(os.path.join(_app_dir, '.git'))
    github_release_repo = (get_config('github_release_repo', '') or '').strip()
    return render_template('narzedzia.html',
        version=VERSION,
        is_admin=(session.get('rola') == 'admin'),
        is_dev=_is_dev_mode(),
        plan_level=plan_level,
        current_plan=current_plan,
        gemini_model=gemini_model,
        is_zip_install=is_zip_install,
        github_release_repo=github_release_repo,
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

@app.route('/generator')
def generator_redirect():
    """Redirect /generator → /paletomat/generator (właściwy generator ofert)"""
    return redirect('/paletomat/generator')

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

# ANALIZA OFERTY

@app.route('/narzedzia/analiza-oferty', methods=['GET', 'POST'])
def analiza_oferty():
    from modules.database import get_db
    from modules.utils import ALLEGRO_PROWIZJE

    analiza = None
    query = request.form.get('query', '').strip()
    cena_sprzedazy = request.form.get('cena_sprzedazy', '')

    if request.method == 'POST' and (query or cena_sprzedazy):
        conn = get_db()
        produkt = None

        if query:
            # Szukaj po ID, EAN, ASIN lub nazwie
            if query.isdigit():
                produkt = conn.execute('SELECT p.*, pal.cena_zakupu as paleta_cena, pal.nazwa as paleta_nazwa, pal.ilosc_produktow as paleta_ilosc FROM produkty p LEFT JOIN palety pal ON pal.id=p.paleta_id WHERE p.id=?', (query,)).fetchone()
            if not produkt:
                produkt = conn.execute('SELECT p.*, pal.cena_zakupu as paleta_cena, pal.nazwa as paleta_nazwa, pal.ilosc_produktow as paleta_ilosc FROM produkty p LEFT JOIN palety pal ON pal.id=p.paleta_id WHERE p.ean=? OR p.asin=?', (query, query)).fetchone()
            if not produkt:
                produkt = conn.execute('SELECT p.*, pal.cena_zakupu as paleta_cena, pal.nazwa as paleta_nazwa, pal.ilosc_produktow as paleta_ilosc FROM produkty p LEFT JOIN palety pal ON pal.id=p.paleta_id WHERE p.nazwa LIKE ? LIMIT 1', (f'%{query}%',)).fetchone()

        if produkt:
            p = dict(produkt)
            kat = (p.get('kategoria') or 'inne').lower()
            prowizja_rate = ALLEGRO_PROWIZJE.get(kat, 0.11)

            # Koszt jednostkowy
            koszt_szt = float(p.get('cena_brutto') or 0)
            if koszt_szt == 0 and p.get('paleta_cena') and p.get('paleta_ilosc'):
                paleta_ilosc = max(int(p['paleta_ilosc']), 1)
                total_szt = conn.execute('SELECT COALESCE(SUM(ilosc),0) FROM produkty WHERE paleta_id=?', (p.get('paleta_id'),)).fetchone()[0] or paleta_ilosc
                koszt_szt = round(float(p['paleta_cena']) / max(total_szt, 1), 2)

            cena_al = float(cena_sprzedazy) if cena_sprzedazy else float(p.get('cena_allegro') or 0)
            prowizja = round(cena_al * prowizja_rate, 2)
            wysylka_koszt = 15  # średni koszt wysyłki
            zysk = round(cena_al - koszt_szt - prowizja - wysylka_koszt, 2) if cena_al > 0 else 0
            marza_pct = round((zysk / cena_al) * 100, 1) if cena_al > 0 else 0
            roi = round((zysk / koszt_szt) * 100, 1) if koszt_szt > 0 else 0

            # Cena Amazon
            cena_amazon = 0
            if p.get('asin'):
                scraped = conn.execute('SELECT cena_amazon FROM scraped WHERE asin=?', (p['asin'],)).fetchone()
                if scraped:
                    cena_amazon = float(scraped['cena_amazon'] or 0)

            # Ile sprzedano
            sprzedane = conn.execute('SELECT COALESCE(SUM(ilosc),0) FROM sprzedaze WHERE produkt_id=? AND COALESCE(status,"") NOT IN ("anulowana","anulowane","zwrot","") AND COALESCE(kupujacy,"") != "offline"', (p['id'],)).fetchone()[0]

            # === ALLEGRO PERFORMANCE ===
            allegro_oferta = None
            allegro_stats = {'wyswietlenia': 0, 'obserwujacych': 0, 'status': None, 'allegro_id': None, 'data_wystawienia': None, 'cena_allegro_live': 0}

            # Szukaj oferty — kilka metod
            _of = conn.execute('SELECT * FROM oferty WHERE produkt_id=? ORDER BY data_aktualizacji DESC LIMIT 1', (p['id'],)).fetchone()

            # Szukaj po nazwie produktu (częste dopasowanie)
            if not _of and p.get('nazwa'):
                _nazwa_short = (p['nazwa'] or '')[:40]
                if _nazwa_short:
                    _of = conn.execute("SELECT * FROM oferty WHERE tytul LIKE ? ORDER BY data_aktualizacji DESC LIMIT 1",
                        (f'%{_nazwa_short}%',)).fetchone()

            # Live sync z Allegro jeśli nie znaleziono — spróbuj sync i szukaj ponownie
            if not _of:
                try:
                    from modules.allegro_api import sync_offers_status, is_authenticated
                    if is_authenticated():
                        sync_offers_status()
                        # Spróbuj ponownie po syncu
                        _of = conn.execute('SELECT * FROM oferty WHERE produkt_id=? ORDER BY data_aktualizacji DESC LIMIT 1', (p['id'],)).fetchone()
                        if not _of and p.get('nazwa'):
                            _nazwa_short = (p['nazwa'] or '')[:40]
                            if _nazwa_short:
                                _of = conn.execute("SELECT * FROM oferty WHERE tytul LIKE ? ORDER BY data_aktualizacji DESC LIMIT 1",
                                    (f'%{_nazwa_short}%',)).fetchone()
                except:
                    pass
            if _of:
                allegro_oferta = dict(_of)
                allegro_stats['wyswietlenia'] = _of['wyswietlenia'] or 0
                allegro_stats['obserwujacych'] = _of['obserwujacych'] or 0
                allegro_stats['status'] = _of['status']
                allegro_stats['allegro_id'] = _of['allegro_id']
                allegro_stats['data_wystawienia'] = _of['data_wystawienia']
                allegro_stats['cena_allegro_live'] = float(_of['cena'] or 0)

            # Przychód i ilość z sprzedaży
            sprzedaz_data = conn.execute('''SELECT COALESCE(SUM(cena * ilosc), 0) as przychod,
                COALESCE(SUM(ilosc), 0) as sztuk,
                COUNT(*) as zamowien
                FROM sprzedaze WHERE produkt_id=?
                AND COALESCE(status,"") NOT IN ("anulowana","anulowane","zwrot","")''', (p['id'],)).fetchone()
            przychod_total = float(sprzedaz_data['przychod'] or 0) if sprzedaz_data else 0
            sprzedane_szt = int(sprzedaz_data['sztuk'] or 0) if sprzedaz_data else 0
            zamowien = int(sprzedaz_data['zamowien'] or 0) if sprzedaz_data else 0

            # Konwersja (sprzedane / wyświetlenia)
            konwersja = round((sprzedane_szt / allegro_stats['wyswietlenia']) * 100, 2) if allegro_stats['wyswietlenia'] > 0 else 0

            # Zysk total
            zysk_total = round(przychod_total - (koszt_szt * sprzedane_szt) - (przychod_total * prowizja_rate) - (wysylka_koszt * zamowien), 2) if sprzedane_szt > 0 else 0

            # Sugerowana cena (min 30% marży)
            if koszt_szt > 0:
                min_cena_30 = round((koszt_szt + wysylka_koszt) / (1 - prowizja_rate - 0.30), 2)
            else:
                min_cena_30 = 0

            # Analiza jakości oferty
            problemy = []
            wskazowki = []
            score = 100  # punkty jakości

            # Tytuł
            nazwa = p.get('nazwa') or ''
            if len(nazwa) < 20:
                problemy.append('Nazwa produktu za krótka (< 20 znaków)')
                score -= 15
            elif len(nazwa) < 40:
                wskazowki.append('Nazwa mogłaby być dłuższa — dodaj kluczowe cechy')
                score -= 5

            # EAN
            if not p.get('ean') or len(str(p.get('ean', ''))) < 8:
                problemy.append('Brak kodu EAN — oferta będzie gorzej widoczna')
                score -= 10

            # Zdjęcie
            if not p.get('zdjecie_url'):
                problemy.append('Brak zdjęcia głównego!')
                score -= 20
            # Więcej zdjęć — sprawdź: 1) lokalne pliki, 2) kolumna images, 3) scraped
            img_count = 0
            # Lokalne pliki w static/downloads/{ASIN}/
            if p.get('asin'):
                import os, glob as _glob
                asin_dir = os.path.join('static', 'downloads', str(p['asin']))
                if os.path.isdir(asin_dir):
                    img_count = len([f for f in os.listdir(asin_dir) if f.endswith(('.jpg', '.png', '.webp'))])
                # Też sprawdź enhanced
                enh_dir = os.path.join('static', 'enhanced', str(p['asin']))
                if os.path.isdir(enh_dir):
                    enh_count = len([f for f in os.listdir(enh_dir) if f.endswith(('.jpg', '.png', '.webp'))])
                    img_count = max(img_count, enh_count)
            # Fallback na kolumnę images
            if img_count == 0:
                images_json = p.get('images') or '[]'
                try:
                    import json as _json
                    img_list = _json.loads(images_json) if isinstance(images_json, str) else images_json
                    img_count = len(img_list) if img_list else 0
                except:
                    pass
            # Fallback na scraped.wszystkie_zdjecia
            if img_count == 0 and p.get('asin'):
                scraped_imgs = conn.execute('SELECT wszystkie_zdjecia FROM scraped WHERE asin=?', (p['asin'],)).fetchone()
                if scraped_imgs and scraped_imgs['wszystkie_zdjecia']:
                    try:
                        img_count = len(_json.loads(scraped_imgs['wszystkie_zdjecia']))
                    except:
                        pass
            if img_count == 0 and p.get('zdjecie_url'):
                img_count = 1
            if img_count < 3:
                wskazowki.append(f'Tylko {img_count} zdjęć — dodaj min. 4-6 zdjęć')
                score -= 10
            elif img_count < 6:
                wskazowki.append(f'{img_count} zdjęć — optymalnie 6-8')
                score -= 5

            # Cena
            if cena_al <= 0:
                problemy.append('Brak ceny sprzedaży!')
                score -= 15
            elif cena_al < koszt_szt:
                problemy.append('Cena sprzedaży NIŻSZA niż koszt zakupu!')
                score -= 20

            # Kategoria
            if kat in ('inne', ''):
                wskazowki.append('Kategoria "inne" — ustaw prawidłową kategorię Allegro')
                score -= 5

            # Opis HTML (z scraped)
            opis_html = ''
            if p.get('asin'):
                scraped_row = conn.execute('SELECT opis_html, tytul_seo FROM scraped WHERE asin=?', (p['asin'],)).fetchone()
                if scraped_row:
                    opis_html = scraped_row['opis_html'] or ''
            if not opis_html:
                wskazowki.append('Opis HTML zostanie wygenerowany automatycznie przez Generator ofert')
                score -= 5  # mały minus — opis wygeneruje się przy wystawianiu
            elif len(opis_html) < 200:
                wskazowki.append('Opis za krótki — min. 300+ znaków')
                score -= 10

            # Stan
            stan = p.get('stan') or 'Nowy'
            if stan.lower() in ('uszkodzony', 'używany'):
                wskazowki.append(f'Stan: {stan} — opisz dokładnie wady w opisie')

            score = max(score, 0)
            score_color = '#22c55e' if score >= 70 else '#eab308' if score >= 40 else '#ef4444'
            score_label = 'Świetna' if score >= 80 else 'Dobra' if score >= 60 else 'Do poprawy' if score >= 40 else 'Słaba'

            analiza = {
                'produkt': p,
                'koszt_szt': koszt_szt,
                'cena_allegro': cena_al,
                'prowizja_rate': prowizja_rate,
                'prowizja': prowizja,
                'wysylka': wysylka_koszt,
                'zysk': zysk,
                'marza_pct': marza_pct,
                'roi': roi,
                'cena_amazon': cena_amazon,
                'sprzedane': sprzedane,
                'min_cena_30': min_cena_30,
                'kategoria': kat,
                'score': score,
                'score_color': score_color,
                'score_label': score_label,
                'problemy': problemy,
                'wskazowki': wskazowki,
                'img_count': img_count,
                'has_opis': bool(opis_html),
                'stan': stan,
                # Allegro performance
                'allegro_stats': allegro_stats,
                'has_allegro': allegro_oferta is not None,
                'przychod_total': przychod_total,
                'sprzedane_szt': sprzedane_szt,
                'zamowien': zamowien,
                'konwersja': konwersja,
                'zysk_total': zysk_total
            }

    return render_template_string(ANALIZA_OFERTY_HTML,
        version=VERSION, analiza=analiza, query=query, cena_sprzedazy=cena_sprzedazy,
        active_narzedzia='active', active_home='', active_magazyn='',
        active_paletomat='', active_allegro='', active_monitor='')


ANALIZA_OFERTY_HTML = '''{% extends "base.html" %}
{% block page_title %}Analiza oferty{% endblock %}
{% block content %}
<style>
.ao-card{background:var(--bg-secondary,#12121a);border:1px solid var(--border-color,#1e1e2e);border-radius:14px;padding:20px;margin-bottom:15px}
.ao-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-bottom:15px}
.ao-stat{background:var(--bg-primary,#0a0a0f);border:1px solid var(--border-color,#1e1e2e);border-radius:12px;padding:16px;text-align:center}
.ao-stat-val{font-size:1.4rem;font-weight:800}
.ao-stat-lbl{font-size:0.7rem;text-transform:uppercase;letter-spacing:1px;color:var(--text-muted,#64748b);margin-top:4px}
.ao-bar{height:8px;border-radius:4px;background:#1e1e2e;overflow:hidden;margin:8px 0}
.ao-bar-fill{height:100%;border-radius:4px;transition:width 0.5s}
.ao-good{color:#22c55e}.ao-bad{color:#ef4444}.ao-warn{color:#eab308}
</style>

<div style="text-align:center;padding:20px 0 10px">
    <h1 style="font-size:1.5rem;background:linear-gradient(135deg,#06b6d4,#6366f1);-webkit-background-clip:text;-webkit-text-fill-color:transparent"><span class=material-symbols-outlined>search</span> ANALIZA OFERTY</h1>
    <small style="color:var(--text-muted)">Oplacalnosc, statystyki Allegro, jakosc oferty</small>
</div>

<div class="ao-card">
    <form method="POST" style="display:flex;gap:10px;align-items:end;flex-wrap:wrap">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div style="flex:2;min-width:200px">
            <label style="font-size:0.75rem;color:var(--text-muted);display:block;margin-bottom:4px">Produkt (ID / EAN / ASIN / nazwa)</label>
            <input type="text" name="query" value="{{ query }}" placeholder="np. B0D9QGW2M6 lub 6975069304199" style="width:100%;padding:10px 14px;background:var(--bg-primary);border:1px solid var(--border-color);border-radius:10px;color:var(--text-primary);font-size:0.9rem">
        </div>
        <div style="flex:1;min-width:120px">
            <label style="font-size:0.75rem;color:var(--text-muted);display:block;margin-bottom:4px">Cena sprzedazy (opcjonalnie)</label>
            <input type="number" step="0.01" name="cena_sprzedazy" value="{{ cena_sprzedazy }}" placeholder="np. 199.00" style="width:100%;padding:10px 14px;background:var(--bg-primary);border:1px solid var(--border-color);border-radius:10px;color:var(--text-primary);font-size:0.9rem">
        </div>
        <button type="submit" style="padding:10px 24px;background:linear-gradient(135deg,#06b6d4,#6366f1);border:none;border-radius:10px;color:#fff;font-weight:700;cursor:pointer;font-size:0.9rem"><span class=material-symbols-outlined>search</span> Analizuj</button>
    </form>
</div>

{% if analiza %}
<div class="ao-card" style="border-color:#6366f155">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px">
        <div>
            <div style="font-size:1.1rem;font-weight:700">{{ analiza.produkt.nazwa[:80] }}</div>
            <div style="font-size:0.8rem;color:var(--text-muted);margin-top:3px">
                {% if analiza.produkt.ean %}EAN: {{ analiza.produkt.ean }} • {% endif %}
                {% if analiza.produkt.asin %}ASIN: {{ analiza.produkt.asin }} • {% endif %}
                Kategoria: {{ analiza.kategoria }} • Prowizja: {{ (analiza.prowizja_rate * 100)|int }}%
            </div>
        </div>
        <div style="text-align:right">
            <div style="font-size:0.75rem;color:var(--text-muted)">Sprzedano</div>
            <div style="font-size:1.2rem;font-weight:700;color:#6366f1">{{ analiza.sprzedane }} szt</div>
        </div>
    </div>

    <div class="ao-grid">
        <div class="ao-stat">
            <div class="ao-stat-val" style="color:#ef4444">{{ "%.2f"|format(analiza.koszt_szt) }} zl</div>
            <div class="ao-stat-lbl">Koszt zakupu/szt</div>
        </div>
        <div class="ao-stat">
            <div class="ao-stat-val" style="color:#3b82f6">{{ "%.2f"|format(analiza.cena_allegro) }} zl</div>
            <div class="ao-stat-lbl">Cena sprzedazy</div>
        </div>
        <div class="ao-stat">
            <div class="ao-stat-val" style="color:#eab308">-{{ "%.2f"|format(analiza.prowizja) }} zl</div>
            <div class="ao-stat-lbl">Prowizja Allegro</div>
        </div>
        <div class="ao-stat">
            <div class="ao-stat-val" style="color:#f97316">-{{ "%.2f"|format(analiza.wysylka) }} zl</div>
            <div class="ao-stat-lbl">Koszt wysylki</div>
        </div>
        <div class="ao-stat" style="border-color:{% if analiza.zysk >= 0 %}#22c55e55{% else %}#ef444455{% endif %}">
            <div class="ao-stat-val {% if analiza.zysk >= 0 %}ao-good{% else %}ao-bad{% endif %}">{{ "%+.2f"|format(analiza.zysk) }} zl</div>
            <div class="ao-stat-lbl">Zysk netto/szt</div>
        </div>
        <div class="ao-stat" style="border-color:{% if analiza.marza_pct >= 20 %}#22c55e55{% elif analiza.marza_pct >= 10 %}#eab30855{% else %}#ef444455{% endif %}">
            <div class="ao-stat-val {% if analiza.marza_pct >= 20 %}ao-good{% elif analiza.marza_pct >= 10 %}ao-warn{% else %}ao-bad{% endif %}">{{ analiza.marza_pct }}%</div>
            <div class="ao-stat-lbl">Marza</div>
        </div>
    </div>

    <!-- Pasek marzy -->
    <div style="margin-bottom:15px">
        <div style="display:flex;justify-content:space-between;font-size:0.75rem;color:var(--text-muted)">
            <span>Marza</span>
            <span class="{% if analiza.marza_pct >= 20 %}ao-good{% elif analiza.marza_pct >= 10 %}ao-warn{% else %}ao-bad{% endif %}">{{ analiza.marza_pct }}%</span>
        </div>
        <div class="ao-bar">
            <div class="ao-bar-fill" style="width:{{ [analiza.marza_pct, 100]|min }}%;background:{% if analiza.marza_pct >= 20 %}#22c55e{% elif analiza.marza_pct >= 10 %}#eab308{% else %}#ef4444{% endif %}"></div>
        </div>
    </div>

    <!-- Rozklad ceny -->
    <div class="ao-card" style="background:var(--bg-primary)">
        <div style="font-weight:700;margin-bottom:10px;font-size:0.85rem"><span class=material-symbols-outlined>bar_chart</span> Rozklad ceny</div>
        {% set total = analiza.koszt_szt + analiza.prowizja + analiza.wysylka + ([analiza.zysk, 0]|max) %}
        {% if total > 0 %}
        <div style="display:flex;height:28px;border-radius:8px;overflow:hidden;margin-bottom:8px">
            <div style="width:{{ (analiza.koszt_szt/total*100)|round }}%;background:#ef4444;display:flex;align-items:center;justify-content:center;font-size:0.65rem;font-weight:700">Koszt</div>
            <div style="width:{{ (analiza.prowizja/total*100)|round }}%;background:#eab308;display:flex;align-items:center;justify-content:center;font-size:0.65rem;font-weight:700">Prow.</div>
            <div style="width:{{ (analiza.wysylka/total*100)|round }}%;background:#f97316;display:flex;align-items:center;justify-content:center;font-size:0.65rem;font-weight:700">Wys.</div>
            {% if analiza.zysk > 0 %}
            <div style="width:{{ (analiza.zysk/total*100)|round }}%;background:#22c55e;display:flex;align-items:center;justify-content:center;font-size:0.65rem;font-weight:700">Zysk</div>
            {% endif %}
        </div>
        {% endif %}
    </div>

    <!-- Allegro Performance -->
    {% if analiza.has_allegro %}
    <div class="ao-card" style="border-color:#6366f133">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px">
            <div style="font-weight:700;font-size:0.95rem"><span class=material-symbols-outlined>bar_chart</span> Allegro Performance</div>
            <div style="font-size:0.75rem;padding:4px 10px;border-radius:20px;font-weight:600;
                {% if analiza.allegro_stats.status == 'aktywna' %}background:#22c55e22;color:#22c55e
                {% elif analiza.allegro_stats.status == 'draft' %}background:#eab30822;color:#eab308
                {% else %}background:#64748b22;color:#64748b{% endif %}">
                {{ analiza.allegro_stats.status|upper if analiza.allegro_stats.status else 'BRAK' }}
            </div>
        </div>

        <div class="ao-grid" style="grid-template-columns:repeat(auto-fill,minmax(140px,1fr))">
            <div class="ao-stat">
                <div class="ao-stat-val" style="color:#3b82f6">{{ analiza.allegro_stats.wyswietlenia }}</div>
                <div class="ao-stat-lbl">Wyswietlenia</div>
            </div>
            <div class="ao-stat">
                <div class="ao-stat-val" style="color:#f59e0b">{{ analiza.allegro_stats.obserwujacych }}</div>
                <div class="ao-stat-lbl">Obserwujacych</div>
            </div>
            <div class="ao-stat">
                <div class="ao-stat-val" style="color:#6366f1">{{ analiza.sprzedane_szt }}</div>
                <div class="ao-stat-lbl">Sprzedanych szt</div>
            </div>
            <div class="ao-stat">
                <div class="ao-stat-val" style="color:#06b6d4">{{ analiza.zamowien }}</div>
                <div class="ao-stat-lbl">Zamowien</div>
            </div>
            <div class="ao-stat">
                <div class="ao-stat-val {% if analiza.konwersja >= 3 %}ao-good{% elif analiza.konwersja >= 1 %}ao-warn{% else %}ao-bad{% endif %}">{{ analiza.konwersja }}%</div>
                <div class="ao-stat-lbl">Konwersja</div>
            </div>
            <div class="ao-stat" style="border-color:{% if analiza.przychod_total > 0 %}#22c55e33{% else %}#1e1e2e{% endif %}">
                <div class="ao-stat-val ao-good">{{ "%.0f"|format(analiza.przychod_total) }} zl</div>
                <div class="ao-stat-lbl">Przychod total</div>
            </div>
        </div>

        {% if analiza.zysk_total != 0 %}
        <div style="margin-top:12px;padding:12px;background:var(--bg-primary);border-radius:10px;display:flex;justify-content:space-between;align-items:center">
            <div style="font-size:0.8rem;color:var(--text-muted)"><span class=material-symbols-outlined>payments</span> Zysk netto (po prowizji + wysylce)</div>
            <div style="font-size:1.2rem;font-weight:800;{% if analiza.zysk_total >= 0 %}color:#22c55e{% else %}color:#ef4444{% endif %}">{{ "%+.2f"|format(analiza.zysk_total) }} zl</div>
        </div>
        {% endif %}

        {% if analiza.allegro_stats.data_wystawienia %}
        <div style="margin-top:8px;font-size:0.72rem;color:var(--text-muted);text-align:right">
            Wystawiono: {{ analiza.allegro_stats.data_wystawienia[:10] if analiza.allegro_stats.data_wystawienia else '-' }}
            {% if analiza.allegro_stats.allegro_id %} • <a href="https://allegro.pl/oferta/{{ analiza.allegro_stats.allegro_id }}" target="_blank" style="color:#6366f1">Zobacz na Allegro ›</a>{% endif %}
        </div>
        {% endif %}
    </div>
    {% endif %}

    <!-- Jakosc oferty -->
    <div class="ao-card" style="border-color:{{ analiza.score_color }}55">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
            <div style="font-weight:700;font-size:0.95rem"><span class=material-symbols-outlined>assignment</span> Jakosc oferty</div>
            <div style="text-align:right">
                <div style="font-size:1.6rem;font-weight:800;color:{{ analiza.score_color }}">{{ analiza.score }}/100</div>
                <div style="font-size:0.7rem;color:{{ analiza.score_color }}">{{ analiza.score_label }}</div>
            </div>
        </div>
        <div class="ao-bar" style="height:10px;margin-bottom:15px">
            <div class="ao-bar-fill" style="width:{{ analiza.score }}%;background:{{ analiza.score_color }}"></div>
        </div>

        <!-- Checklist -->
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;font-size:0.8rem">
            <div>{{ '<span class=material-symbols-outlined style=color:#22c55e>check_circle</span>' if analiza.produkt.nazwa and analiza.produkt.nazwa|length > 20 else '<span class=material-symbols-outlined style=color:#ef4444>cancel</span>' }} Tytul ({{ analiza.produkt.nazwa|length if analiza.produkt.nazwa else 0 }} zn.)</div>
            <div>{{ '<span class=material-symbols-outlined style=color:#22c55e>check_circle</span>' if analiza.produkt.ean else '<span class=material-symbols-outlined style=color:#ef4444>cancel</span>' }} Kod EAN</div>
            <div>{{ '<span class=material-symbols-outlined style=color:#22c55e>check_circle</span>' if analiza.produkt.zdjecie_url else '<span class=material-symbols-outlined style=color:#ef4444>cancel</span>' }} Zdjecie glowne</div>
            <div>{{ '<span class=material-symbols-outlined style=color:#22c55e>check_circle</span>' if analiza.img_count >= 4 else '<span class=material-symbols-outlined>warning</span>' if analiza.img_count >= 2 else '<span class=material-symbols-outlined style=color:#ef4444>cancel</span>' }} Zdjecia ({{ analiza.img_count }})</div>
            <div>{{ '<span class=material-symbols-outlined style=color:#22c55e>check_circle</span>' if analiza.has_opis else '<span class=material-symbols-outlined style=color:#3b82f6>info</span>' }} Opis HTML{{ '' if analiza.has_opis else ' (auto)' }}</div>
            <div>{{ '<span class=material-symbols-outlined style=color:#22c55e>check_circle</span>' if analiza.cena_allegro > 0 else '<span class=material-symbols-outlined style=color:#ef4444>cancel</span>' }} Cena</div>
            <div>{{ '<span class=material-symbols-outlined style=color:#22c55e>check_circle</span>' if analiza.kategoria not in ('inne', '') else '<span class=material-symbols-outlined>warning</span>' }} Kategoria</div>
            <div>{{ '<span class=material-symbols-outlined style=color:#22c55e>check_circle</span>' if analiza.produkt.asin else '<span class=material-symbols-outlined>warning</span>' }} ASIN</div>
        </div>

        {% if analiza.problemy %}
        <div style="margin-bottom:10px">
            <div style="font-weight:600;color:#ef4444;font-size:0.8rem;margin-bottom:6px"><span class=material-symbols-outlined style=color:#ef4444>cancel</span> Problemy:</div>
            {% for p in analiza.problemy %}
            <div style="font-size:0.8rem;color:#fca5a5;margin-bottom:3px;padding-left:12px">• {{ p }}</div>
            {% endfor %}
        </div>
        {% endif %}
        {% if analiza.wskazowki %}
        <div>
            <div style="font-weight:600;color:#eab308;font-size:0.8rem;margin-bottom:6px"><span class=material-symbols-outlined>lightbulb</span> Wskazowki:</div>
            {% for w in analiza.wskazowki %}
            <div style="font-size:0.8rem;color:#fde68a;margin-bottom:3px;padding-left:12px">• {{ w }}</div>
            {% endfor %}
        </div>
        {% endif %}
    </div>

    <!-- Rekomendacje cenowe -->
    <div class="ao-card" style="background:var(--bg-primary)">
        <div style="font-weight:700;margin-bottom:10px;font-size:0.85rem"><span class=material-symbols-outlined>payments</span> Rekomendacje cenowe</div>
        {% if analiza.cena_amazon > 0 %}
        <div style="font-size:0.85rem;margin-bottom:6px"><span class=material-symbols-outlined>language</span> Cena Amazon: <b style="color:#3b82f6">{{ "%.2f"|format(analiza.cena_amazon) }} EUR</b> (~{{ "%.0f"|format(analiza.cena_amazon * 4.3) }} PLN)</div>
        {% endif %}
        {% if analiza.min_cena_30 > 0 %}
        <div style="font-size:0.85rem;margin-bottom:6px"><span class=material-symbols-outlined>trending_up</span> Min. cena dla 30% marzy: <b style="color:#22c55e">{{ "%.2f"|format(analiza.min_cena_30) }} zl</b></div>
        {% endif %}
        <div style="font-size:0.85rem;margin-bottom:6px"><span class=material-symbols-outlined>bar_chart</span> ROI: <b style="color:{% if analiza.roi >= 50 %}#22c55e{% elif analiza.roi >= 20 %}#eab308{% else %}#ef4444{% endif %}">{{ analiza.roi }}%</b></div>
        {% if analiza.zysk < 0 %}
        <div style="font-size:0.85rem;color:#ef4444;font-weight:600;margin-top:8px"><span class=material-symbols-outlined>warning</span> STRATA! Podniez cene powyzej {{ "%.0f"|format(analiza.min_cena_30) }} zl</div>
        {% elif analiza.marza_pct < 10 %}
        <div style="font-size:0.85rem;color:#eab308;font-weight:600;margin-top:8px"><span class=material-symbols-outlined>warning</span> Niska marza. Rozważ podniesienie ceny.</div>
        {% elif analiza.marza_pct >= 30 %}
        <div style="font-size:0.85rem;color:#22c55e;font-weight:600;margin-top:8px"><span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Swietna marza! Oferta bardzo oplacalna.</div>
        {% else %}
        <div style="font-size:0.85rem;color:#22c55e;margin-top:8px"><span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Oferta oplacalna.</div>
        {% endif %}
    </div>
</div>
{% elif query %}
<div class="ao-card" style="text-align:center;padding:30px">
    <div style="font-size:2rem;margin-bottom:10px"><span class=material-symbols-outlined>search</span></div>
    <div style="color:var(--text-muted)">Nie znaleziono produktu "{{ query }}"</div>
</div>
{% endif %}

<a href="/narzedzia" style="display:inline-block;margin-top:10px;color:var(--text-muted);text-decoration:none;font-size:0.85rem">← Powrot do narzedzi</a>
{% endblock %}
'''


# ============================================================
# ALLEGRO PERFORMANCE PANEL
# ============================================================

@app.route('/analytics/allegro-performance')
def allegro_performance():
    from modules.database import get_db
    conn = get_db()

    # Pokazuj dane z cache — sync ręcznie przyciskiem
    sync_msg = ''
    try:
        from modules.allegro_api import is_authenticated
        _last_sync = conn.execute("SELECT MAX(data_aktualizacji) as last FROM oferty").fetchone()
        _last = _last_sync['last'] if _last_sync else None
        if _last:
            sync_msg = f"Ostatni sync: {_last[:16]}"
        if not is_authenticated():
            sync_msg += " (nie zalogowany do Allegro)"
    except:
        pass

    # Pobierz wszystkie oferty z bazy
    oferty = conn.execute('''
        SELECT o.*, p.asin, p.kategoria, p.cena_brutto,
               pal.cena_zakupu as paleta_cena, pal.ilosc_produktow as paleta_ilosc
        FROM oferty o
        LEFT JOIN produkty p ON p.id = o.produkt_id
        LEFT JOIN palety pal ON pal.id = p.paleta_id
        ORDER BY o.wyswietlenia DESC, o.data_aktualizacji DESC
    ''').fetchall()

    # Zbierz sprzedaże per oferta
    items = []
    totals = {'wyswietlenia': 0, 'obserwujacych': 0, 'sprzedane': 0, 'przychod': 0, 'zysk': 0, 'aktywne': 0, 'draft': 0, 'zakonczone': 0, 'stan': 0}

    for o in oferty:
        o = dict(o)
        pid = o.get('produkt_id')

        # Sprzedaż
        sp = conn.execute('''SELECT COALESCE(SUM(ilosc),0) as szt, COALESCE(SUM(cena*ilosc),0) as przychod, COUNT(*) as zamowien
            FROM sprzedaze WHERE produkt_id=? AND COALESCE(status,"") NOT IN ("anulowana","anulowane","zwrot","")''',
            (pid,)).fetchone() if pid else None
        szt = int(sp['szt'] or 0) if sp else 0
        przychod = float(sp['przychod'] or 0) if sp else 0
        zamowien = int(sp['zamowien'] or 0) if sp else 0

        views = o.get('wyswietlenia') or 0
        watchers = o.get('obserwujacych') or 0
        konwersja = round((szt / views) * 100, 2) if views > 0 else 0
        status = o.get('status') or 'draft'

        stan = int(o.get('ilosc') or 0)

        items.append({
            'allegro_id': o.get('allegro_id'),
            'tytul': (o.get('tytul') or '')[:60],
            'cena': float(o.get('cena') or 0),
            'status': status,
            'stan': stan,
            'wyswietlenia': views,
            'obserwujacych': watchers,
            'sprzedane': szt,
            'zamowien': zamowien,
            'przychod': przychod,
            'konwersja': konwersja,
            'data_wystawienia': (o.get('data_wystawienia') or '')[:10],
            'asin': o.get('asin') or '',
        })

        totals['wyswietlenia'] += views
        totals['obserwujacych'] += watchers
        totals['stan'] += stan
        totals['sprzedane'] += szt
        totals['przychod'] += przychod
        if status == 'aktywna':
            totals['aktywne'] += 1
        elif status == 'draft':
            totals['draft'] += 1
        else:
            totals['zakonczone'] += 1

    totals['konwersja'] = round((totals['sprzedane'] / totals['wyswietlenia']) * 100, 2) if totals['wyswietlenia'] > 0 else 0
    totals['total'] = len(items)

    return render_template_string(ALLEGRO_PERF_HTML,
        version=VERSION, items=items, totals=totals, sync_msg=sync_msg,
        active_narzedzia='active', active_home='', active_magazyn='',
        active_paletomat='', active_allegro='', active_monitor='')


@app.route('/analytics/allegro-performance/sync', methods=['POST'])
def allegro_performance_sync():
    """Sync stats z Allegro — wywoływane AJAX-em z panelu."""
    try:
        from modules.allegro_api import sync_offers_status, is_authenticated
        if not is_authenticated():
            return jsonify({'ok': False, 'error': 'Nie zalogowany do Allegro'})
        result = sync_offers_status()
        return jsonify({'ok': True, 'total': result.get('total', 0), 'stats_updated': result.get('stats_updated', 0)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:100]})


ALLEGRO_PERF_HTML = '''{% extends "base.html" %}
{% block page_title %}Allegro Performance{% endblock %}
{% block content %}
<style>
.ap-card{background:rgba(15,15,30,0.65);backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.08);padding:20px;margin-bottom:15px}
.ap-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin-bottom:20px}
.ap-stat{background:rgba(13,15,26,0.8);border:1px solid rgba(255,255,255,0.06);border-left:3px solid rgba(143,245,255,0.2);padding:16px;text-align:center;transition:all 0.2s}
.ap-stat:hover{border-left-color:#8ff5ff;background:rgba(13,15,26,0.95)}
.ap-stat-val{font-size:1.4rem;font-weight:800;font-family:'Space Grotesk',sans-serif}
.ap-stat-lbl{font-size:0.6rem;text-transform:uppercase;letter-spacing:1.2px;color:var(--text-muted);margin-top:4px;font-weight:600}
.ap-table{width:100%;border-collapse:collapse;font-size:0.78rem}
.ap-table th{text-align:left;padding:10px 8px;background:rgba(13,15,26,0.8);border-bottom:1px solid rgba(143,245,255,0.1);color:var(--text-muted);font-size:0.65rem;text-transform:uppercase;letter-spacing:1px;font-weight:700;cursor:pointer;user-select:none}
.ap-table th:hover{color:#8ff5ff}
.ap-table td{padding:10px 8px;border-bottom:1px solid rgba(255,255,255,0.04)}
.ap-table tr:hover{background:rgba(143,245,255,0.02)}
.ap-badge{display:inline-block;padding:2px 8px;font-size:0.65rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px}
.ap-badge.active{background:rgba(190,238,0,0.1);color:#beee00;border:1px solid rgba(190,238,0,0.2)}
.ap-badge.draft{background:rgba(245,158,11,0.1);color:#f59e0b;border:1px solid rgba(245,158,11,0.2)}
.ap-badge.ended{background:rgba(100,116,139,0.1);color:#64748b;border:1px solid rgba(100,116,139,0.2)}
.ap-good{color:#beee00}.ap-warn{color:#f59e0b}.ap-bad{color:#ff4d6a}.ap-cyan{color:#8ff5ff}
.ap-filter{display:flex;gap:8px;margin-bottom:15px;flex-wrap:wrap}
.ap-filter-btn{padding:6px 14px;border:1px solid rgba(255,255,255,0.08);background:rgba(15,15,30,0.65);color:var(--text-muted);cursor:pointer;font-size:0.75rem;font-weight:700;font-family:'Space Grotesk',sans-serif;transition:all 0.2s}
.ap-filter-btn:hover,.ap-filter-btn.active{border-color:rgba(143,245,255,0.3);color:#8ff5ff;background:rgba(143,245,255,0.07)}
@media(max-width:768px){.ap-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:480px){.ap-grid{grid-template-columns:1fr}}
</style>

<div style="text-align:center;padding:20px 0 10px">
    <h1 style="font-size:1.5rem;font-family:'Space Grotesk',sans-serif;font-weight:800;background:linear-gradient(135deg,#8ff5ff,#beee00);-webkit-background-clip:text;-webkit-text-fill-color:transparent"><span class=material-symbols-outlined style="color:#8ff5ff;-webkit-text-fill-color:#8ff5ff">bar_chart</span> ALLEGRO PERFORMANCE</h1>
    <small style="color:var(--text-muted)">Wyswietlenia, obserwujacy, sprzedaze — wszystkie oferty</small>
    <div style="margin-top:8px;display:flex;align-items:center;justify-content:center;gap:12px">
        <button id="syncBtn" onclick="syncStats()" style="padding:8px 20px;background:rgba(143,245,255,0.10);border:1px solid rgba(143,245,255,0.25);border-radius:8px;color:#8ff5ff;font-weight:700;cursor:pointer;font-size:0.78rem;font-family:'Space Grotesk',sans-serif;transition:all 0.2s"><span class=material-symbols-outlined style="font-size:0.9rem;vertical-align:middle">sync</span> Sync z Allegro</button>
        <span id="syncMsg" style="font-size:0.7rem;color:var(--text-muted)">{{ sync_msg }}</span>
    </div>
</div>

<!-- Totals -->
<div class="ap-grid">
    <div class="ap-stat" style="border-left-color:rgba(143,245,255,0.4)">
        <div class="ap-stat-val" style="color:#8ff5ff">{{ totals.total }}</div>
        <div class="ap-stat-lbl">Ofert ({{ totals.aktywne }} aktywnych)</div>
    </div>
    <div class="ap-stat" style="border-left-color:rgba(59,130,246,0.4)">
        <div class="ap-stat-val ap-cyan">{{ "{:,}".format(totals.wyswietlenia).replace(",", " ") }}</div>
        <div class="ap-stat-lbl">Wyswietlenia</div>
    </div>
    <div class="ap-stat" style="border-left-color:rgba(245,158,11,0.4)">
        <div class="ap-stat-val" style="color:#f59e0b">{{ totals.obserwujacych }}</div>
        <div class="ap-stat-lbl">Obserwujacych</div>
    </div>
    <div class="ap-stat" style="border-left-color:rgba(168,85,247,0.4)">
        <div class="ap-stat-val" style="color:#a855f7">{{ "{:,}".format(totals.stan).replace(",", " ") }}</div>
        <div class="ap-stat-lbl">Stan magazynowy</div>
    </div>
    <div class="ap-stat" style="border-left-color:rgba(190,238,0,0.4)">
        <div class="ap-stat-val ap-good">{{ totals.sprzedane }}</div>
        <div class="ap-stat-lbl">Sprzedanych szt</div>
    </div>
    <div class="ap-stat" style="border-left-color:rgba(255,107,155,0.4)">
        <div class="ap-stat-val" style="color:#ff6b9b">{{ "%.1f"|format(totals.konwersja) }}%</div>
        <div class="ap-stat-lbl">Sr. konwersja</div>
    </div>
    <div class="ap-stat" style="border-left-color:rgba(190,238,0,0.4)">
        <div class="ap-stat-val ap-good">{{ "{:,.0f}".format(totals.przychod).replace(",", " ") }} zl</div>
        <div class="ap-stat-lbl">Przychod total</div>
    </div>
</div>

<!-- Filters -->
<div class="ap-filter">
    <button class="ap-filter-btn active" onclick="filterOffers('all', this)">Wszystkie ({{ totals.total }})</button>
    <button class="ap-filter-btn" onclick="filterOffers('aktywna', this)">Aktywne ({{ totals.aktywne }})</button>
    <button class="ap-filter-btn" onclick="filterOffers('draft', this)">Szkice ({{ totals.draft }})</button>
    <button class="ap-filter-btn" onclick="filterOffers('zakonczona', this)">Zakonczone ({{ totals.zakonczone }})</button>
</div>

<!-- Table -->
<div class="ap-card" style="padding:0;overflow-x:auto">
<table class="ap-table" id="perfTable">
    <thead>
        <tr>
            <th onclick="sortTable(0)">Oferta</th>
            <th onclick="sortTable(1)" style="text-align:right">Cena</th>
            <th onclick="sortTable(2)" style="text-align:center">Status</th>
            <th onclick="sortTable(3)" style="text-align:right"><span class=material-symbols-outlined>inventory_2</span> Stan</th>
            <th onclick="sortTable(4)" style="text-align:right"><span class=material-symbols-outlined>visibility</span> Wysw.</th>
            <th onclick="sortTable(5)" style="text-align:right"><span class=material-symbols-outlined>favorite</span> Obserwuj.</th>
            <th onclick="sortTable(6)" style="text-align:right"><span class=material-symbols-outlined>shopping_cart</span> Sprzedane</th>
            <th onclick="sortTable(7)" style="text-align:right"><span class=material-symbols-outlined>trending_up</span> Konwersja</th>
            <th onclick="sortTable(8)" style="text-align:right"><span class=material-symbols-outlined>payments</span> Przychod</th>
        </tr>
    </thead>
    <tbody>
    {% for item in items %}
        <tr data-status="{{ item.status }}">
            <td style="max-width:280px">
                <div style="font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                    {% if item.allegro_id %}<a href="https://allegro.pl/oferta/{{ item.allegro_id }}" target="_blank" style="color:var(--text-primary);text-decoration:none" title="{{ item.tytul }}">{{ item.tytul }}</a>
                    {% else %}{{ item.tytul }}{% endif %}
                </div>
                <div style="font-size:0.65rem;color:var(--text-muted)">{{ item.asin }}{% if item.data_wystawienia %} • {{ item.data_wystawienia }}{% endif %}</div>
            </td>
            <td style="text-align:right;font-weight:600">{{ "%.0f"|format(item.cena) }} zl</td>
            <td style="text-align:center"><span class="ap-badge {% if item.status == 'aktywna' %}active{% elif item.status == 'draft' %}draft{% else %}ended{% endif %}">{{ item.status }}</span></td>
            <td style="text-align:right;font-weight:700" class="{% if item.stan > 5 %}ap-good{% elif item.stan > 0 %}ap-warn{% else %}ap-bad{% endif %}">{{ item.stan }}</td>
            <td style="text-align:right;font-weight:700" class="ap-blue">{{ item.wyswietlenia }}</td>
            <td style="text-align:right;font-weight:600;color:#f59e0b">{{ item.obserwujacych }}</td>
            <td style="text-align:right;font-weight:700" class="{% if item.sprzedane > 0 %}ap-good{% endif %}">{{ item.sprzedane }}</td>
            <td style="text-align:right;font-weight:600" class="{% if item.konwersja >= 3 %}ap-good{% elif item.konwersja >= 1 %}ap-warn{% elif item.konwersja > 0 %}ap-bad{% endif %}">{% if item.konwersja > 0 %}{{ item.konwersja }}%{% else %}-{% endif %}</td>
            <td style="text-align:right;font-weight:700" class="{% if item.przychod > 0 %}ap-good{% endif %}">{% if item.przychod > 0 %}{{ "%.0f"|format(item.przychod) }} zl{% else %}-{% endif %}</td>
        </tr>
    {% endfor %}
    </tbody>
</table>
</div>

{% if not items %}
<div class="ap-card" style="text-align:center;padding:40px">
    <div style="font-size:2rem;margin-bottom:10px"><span class=material-symbols-outlined>bar_chart</span></div>
    <div style="color:var(--text-muted)">Brak ofert. Zsyncuj z Allegro lub wystaw pierwsza oferte.</div>
</div>
{% endif %}

<a href="/narzedzia" style="display:inline-block;margin-top:10px;color:var(--text-muted);text-decoration:none;font-size:0.85rem">← Powrot do narzedzi</a>

<script nonce="{getattr(request, '_csp_nonce', '')}">
function filterOffers(status, btn) {
    var rows = document.querySelectorAll('#perfTable tbody tr');
    rows.forEach(function(r) {
        if (status === 'all' || r.getAttribute('data-status') === status) {
            r.style.display = '';
        } else {
            r.style.display = 'none';
        }
    });
    document.querySelectorAll('.ap-filter-btn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
}

function syncStats() {
    var btn = document.getElementById('syncBtn');
    var msg = document.getElementById('syncMsg');
    btn.disabled = true;
    btn.style.opacity = '0.6';
    btn.textContent = '⏳ Syncowanie...';
    msg.textContent = '';
    fetch('/analytics/allegro-performance/sync', {method: 'POST', headers: {'Content-Type': 'application/json'}})
    .then(function(r) { return r.json(); })
    .then(function(d) {
        if (d.ok) {
            msg.style.color = '#22c55e';
            msg.textContent = '[OK] Zsyncowano ' + d.total + ' ofert, statystyki: ' + (d.stats_updated || 0);
            setTimeout(function() { location.reload(); }, 1500);
        } else {
            msg.style.color = '#ef4444';
            msg.textContent = '[ERR] ' + (d.error || 'Błąd');
            btn.disabled = false;
            btn.style.opacity = '1';
            btn.textContent = ' Sync z Allegro';
        }
    })
    .catch(function(e) {
        msg.style.color = '#ef4444';
        msg.textContent = '[ERR] ' + e;
        btn.disabled = false;
        btn.style.opacity = '1';
        btn.textContent = ' Sync z Allegro';
    });
}

var sortDir = {};
function sortTable(col) {
    var table = document.getElementById('perfTable');
    var tbody = table.querySelector('tbody');
    var rows = Array.from(tbody.querySelectorAll('tr'));
    sortDir[col] = !sortDir[col];
    rows.sort(function(a, b) {
        var aVal = a.cells[col].textContent.trim().replace(/[^0-9.\\-]/g, '');
        var bVal = b.cells[col].textContent.trim().replace(/[^0-9.\\-]/g, '');
        var aNum = parseFloat(aVal) || 0;
        var bNum = parseFloat(bVal) || 0;
        if (col === 0 || col === 2) {
            aVal = a.cells[col].textContent.trim().toLowerCase();
            bVal = b.cells[col].textContent.trim().toLowerCase();
            return sortDir[col] ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
        }
        return sortDir[col] ? aNum - bNum : bNum - aNum;
    });
    rows.forEach(function(r) { tbody.appendChild(r); });
}
</script>
{% endblock %}
'''


# ============================================================
# ZESTAWY ALLEGRO — propozycje bundli "Kup razem"
# ============================================================

@app.route('/narzedzia/zestawy-allegro')
def zestawy_allegro():
    from modules.database import get_db
    conn = get_db()

    # Pobierz aktywne oferty z produktami
    oferty = conn.execute('''
        SELECT o.id, o.allegro_id, o.tytul, o.cena, o.status, o.wyswietlenia, o.ilosc as stan,
               p.id as pid, p.nazwa, p.kategoria, p.zdjecie_url, p.asin, p.kod_magazynowy,
               p.ilosc as mag_ilosc
        FROM oferty o
        JOIN produkty p ON p.id = o.produkt_id
        WHERE o.status = 'aktywna' AND p.ilosc > 0
        ORDER BY o.wyswietlenia DESC
    ''').fetchall()

    oferty = [dict(o) for o in oferty]
    kategorie = {}
    for o in oferty:
        kat = (o.get('kategoria') or 'inne').lower()
        kategorie.setdefault(kat, []).append(o)

    # Komplementarne kategorie — co pasuje do czego
    _KOMPLEMENT = {
        'komputery': ['akcesoria', 'gaming', 'biuro', 'smart_home'],
        'akcesoria': ['komputery', 'telefony', 'gaming'],
        'telefony': ['akcesoria', 'komputery'],
        'gaming': ['komputery', 'akcesoria'],
        'sport': ['outdoor', 'rehabilitacja'],
        'outdoor': ['sport', 'motoryzacja'],
        'rehabilitacja': ['sport'],
        'zwierzeta': ['zwierzeta'],
        'zabawki': ['zabawki'],
        'dom_ogrod': ['oswietlenie', 'tekstylia', 'kuchnia', 'agd_duze'],
        'oswietlenie': ['dom_ogrod', 'smart_home'],
        'tekstylia': ['dom_ogrod'],
        'kuchnia': ['dom_ogrod', 'agd_duze'],
        'agd_duze': ['dom_ogrod', 'kuchnia'],
        'smart_home': ['komputery', 'oswietlenie'],
        'foto_video': ['foto_video', 'akcesoria'],
        'motoryzacja': ['motoryzacja', 'outdoor'],
        'bagaz': ['outdoor', 'sport'],
        'biuro': ['komputery', 'druk3d'],
        'druk3d': ['komputery', 'biuro'],
        'rtv': ['akcesoria', 'smart_home'],
        'ev_ladowarki': ['motoryzacja', 'smart_home'],
    }
    # Stop words do keyword matching
    _STOP = {'na', 'do', 'z', 'ze', 'w', 'i', 'dla', 'od', 'po', 'się', 'the',
             'szt', 'cm', 'mm', 'kg', 'ml', 'duży', 'mały', 'duza', 'mala',
             'komplet', 'zestaw', 'set', 'pro', 'max', 'mini', 'xl', 'xxl'}

    def _keywords(nazwa):
        """Wyciągnij słowa kluczowe z nazwy produktu (3+ znaki, bez stop words)."""
        words = set()
        for w in (nazwa or '').lower().split():
            w = w.strip('.,()-/[]')
            if len(w) >= 3 and w not in _STOP:
                words.add(w)
        return words

    def _score(main_offer, candidate):
        """Oblicz score dopasowania: wyższy = lepszy match."""
        m_kat = (main_offer.get('kategoria') or 'inne').lower()
        c_kat = (candidate.get('kategoria') or 'inne').lower()
        score = 0

        # Ta sama kategoria (ale nie "inne" — to catch-all)
        if m_kat == c_kat and m_kat != 'inne':
            score += 50

        # Komplementarna kategoria
        if c_kat in _KOMPLEMENT.get(m_kat, []):
            score += 30

        # Keyword matching — wspólne słowa w nazwach
        m_kw = _keywords(main_offer.get('tytul') or main_offer.get('nazwa'))
        c_kw = _keywords(candidate.get('tytul') or candidate.get('nazwa'))
        common = m_kw & c_kw
        score += len(common) * 15

        # Bonus za tańszy produkt (dobry jako dodatek do zestawu)
        m_cena = main_offer.get('cena') or 0
        c_cena = candidate.get('cena') or 0
        if 0 < c_cena < m_cena * 0.5:
            score += 10

        return score

    # Dla każdej oferty — znajdź kandydatów do zestawu
    for o in oferty:
        cur_id = o['pid']
        scored = []
        for other in oferty:
            if other['pid'] == cur_id:
                continue
            s = _score(o, other)
            if s > 0:
                scored.append(({**other, 'match_score': s}, s))
        scored.sort(key=lambda x: (-x[1], x[0].get('cena') or 9999))
        o['candidates'] = [c[0] for c in scored[:4]]

    totals = {
        'aktywne': len(oferty),
        'kategorie': len(kategorie),
    }

    return render_template_string(ZESTAWY_HTML,
        version=VERSION, oferty=oferty, totals=totals, kategorie=sorted(kategorie.keys()),
        active_narzedzia='active', active_home='', active_magazyn='',
        active_paletomat='', active_allegro='', active_monitor='')


ZESTAWY_HTML = '''{% extends "base.html" %}
{% block page_title %}Zestawy Allegro{% endblock %}
{% block content %}
<style>
.zs-card{background:rgba(15,15,30,0.65);backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.08);padding:20px;margin-bottom:15px}
.zs-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin-bottom:20px}
.zs-stat{background:rgba(13,15,26,0.8);border:1px solid rgba(255,255,255,0.06);border-left:3px solid rgba(168,85,247,0.3);padding:16px;text-align:center}
.zs-stat-val{font-size:1.4rem;font-weight:800;font-family:'Space Grotesk',sans-serif}
.zs-stat-lbl{font-size:0.6rem;text-transform:uppercase;letter-spacing:1.2px;color:var(--text-muted);margin-top:4px;font-weight:600}
.zs-offer{background:rgba(13,15,26,0.6);border:1px solid rgba(168,85,247,0.10);margin-bottom:12px;overflow:hidden;transition:border-color 0.2s}
.zs-offer:hover{border-color:rgba(168,85,247,0.30)}
.zs-offer-main{display:flex;gap:14px;padding:16px;align-items:center}
.zs-offer-img{width:60px;height:60px;object-fit:contain;background:rgba(255,255,255,0.9);flex-shrink:0}
.zs-offer-info{flex:1;min-width:0}
.zs-offer-name{font-weight:700;font-size:0.88rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.zs-offer-meta{font-size:0.7rem;color:var(--text-muted);margin-top:3px;display:flex;gap:12px;flex-wrap:wrap}
.zs-offer-price{font-size:1rem;font-weight:800;color:#a855f7;font-family:'Space Grotesk',sans-serif;white-space:nowrap}
.zs-cands{padding:0 16px 14px;display:flex;gap:8px;flex-wrap:wrap}
.zs-cand{display:flex;align-items:center;gap:8px;padding:8px 12px;background:rgba(168,85,247,0.06);border:1px solid rgba(168,85,247,0.12);text-decoration:none;color:#e2e8f0;transition:all 0.2s;flex:1;min-width:200px;max-width:calc(50% - 4px)}
.zs-cand:hover{border-color:rgba(168,85,247,0.35);background:rgba(168,85,247,0.12)}
.zs-cand-img{width:36px;height:36px;object-fit:contain;background:rgba(255,255,255,0.9);flex-shrink:0}
.zs-cand-info{flex:1;min-width:0}
.zs-cand-name{font-size:0.72rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.zs-cand-price{font-size:0.72rem;font-weight:700;color:#a855f7;margin-top:2px}
.zs-cand-badge{font-size:0.55rem;padding:1px 6px;text-transform:uppercase;letter-spacing:0.5px;background:rgba(168,85,247,0.15);color:#a855f7;font-weight:700;white-space:nowrap}
.zs-link{display:inline-flex;align-items:center;gap:4px;padding:6px 12px;font-size:0.7rem;font-weight:700;color:#8ff5ff;border:1px solid rgba(143,245,255,0.2);background:rgba(143,245,255,0.06);text-decoration:none;transition:all 0.2s;white-space:nowrap}
.zs-link:hover{border-color:rgba(143,245,255,0.4);background:rgba(143,245,255,0.12)}
.zs-filter{display:flex;gap:8px;margin-bottom:15px;flex-wrap:wrap}
.zs-filter-btn{padding:6px 14px;border:1px solid rgba(255,255,255,0.08);background:rgba(15,15,30,0.65);color:var(--text-muted);cursor:pointer;font-size:0.75rem;font-weight:700;font-family:'Space Grotesk',sans-serif;transition:all 0.2s}
.zs-filter-btn:hover,.zs-filter-btn.active{border-color:rgba(168,85,247,0.3);color:#a855f7;background:rgba(168,85,247,0.07)}
.zs-arrow{font-size:1.2rem;color:#a855f7;margin:0 4px}
@media(max-width:600px){.zs-cand{min-width:100%;max-width:100%}}
</style>

<div style="text-align:center;padding:20px 0 10px">
    <h1 style="font-size:1.5rem;font-family:'Space Grotesk',sans-serif;font-weight:800;background:linear-gradient(135deg,#a855f7,#8ff5ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent"><span class="material-symbols-outlined" style="color:#a855f7;-webkit-text-fill-color:#a855f7">loyalty</span> ZESTAWY ALLEGRO</h1>
    <small style="color:var(--text-muted)">Propozycje zestawow "Kup razem" — sparuj oferty i zwieksz sprzedaz</small>
</div>

<!-- Stats -->
<div class="zs-grid">
    <div class="zs-stat">
        <div class="zs-stat-val" style="color:#a855f7">{{ totals.aktywne }}</div>
        <div class="zs-stat-lbl">Aktywnych ofert</div>
    </div>
    <div class="zs-stat">
        <div class="zs-stat-val" style="color:#8ff5ff">{{ totals.kategorie }}</div>
        <div class="zs-stat-lbl">Kategorii</div>
    </div>
</div>

<!-- Category filter -->
<div class="zs-filter">
    <button class="zs-filter-btn active" onclick="filterZestawy('all', this)">Wszystkie ({{ totals.aktywne }})</button>
    {% for kat in kategorie %}
    <button class="zs-filter-btn" onclick="filterZestawy('{{ kat }}', this)">{{ kat|capitalize }}</button>
    {% endfor %}
</div>

<!-- Info -->
<div style="padding:10px 14px;background:rgba(168,85,247,0.06);border:1px solid rgba(168,85,247,0.12);margin-bottom:15px;font-size:0.75rem;color:#94a3b8;display:flex;align-items:center;gap:8px">
    <span class="material-symbols-outlined" style="font-size:1rem;color:#a855f7">info</span>
    Wybierz produkt glowny i sparuj z sugerowanym produktem. Na Allegro: Moje oferty → Edytuj oferte → Zestawy.
</div>

<!-- Offers with candidates -->
{% for item in oferty %}
<div class="zs-offer" data-kat="{{ (item.kategoria or 'inne')|lower }}">
    <div class="zs-offer-main">
        {% if item.zdjecie_url %}
        <img src="{{ item.zdjecie_url }}" class="zs-offer-img" loading="lazy" onerror="this.style.display='none'">
        {% endif %}
        <div class="zs-offer-info">
            <div class="zs-offer-name" title="{{ item.tytul or item.nazwa }}">{{ (item.tytul or item.nazwa)[:55] }}</div>
            <div class="zs-offer-meta">
                <span>{{ item.kategoria or 'inne' }}</span>
                <span>{{ item.mag_ilosc }} szt</span>
                <span>{{ item.wyswietlenia or 0 }} wysw.</span>
            </div>
        </div>
        <div class="zs-offer-price">{{ "%.0f"|format(item.cena or 0) }} zl</div>
        {% if item.allegro_id %}
        <a href="https://allegro.pl/oferta/{{ item.allegro_id }}" target="_blank" class="zs-link"><span class="material-symbols-outlined" style="font-size:0.85rem">open_in_new</span> Allegro</a>
        {% endif %}
    </div>
    {% if item.candidates %}
    <div style="padding:0 16px 6px;font-size:0.65rem;color:#64748b;text-transform:uppercase;letter-spacing:1px;font-weight:600">
        <span class="material-symbols-outlined" style="font-size:0.8rem;vertical-align:middle">add_circle</span> Sparuj z:
    </div>
    <div class="zs-cands">
        {% for c in item.candidates %}
        <a href="/magazyn/produkt/{{ c.kod_magazynowy or c.pid }}" class="zs-cand">
            {% if c.zdjecie_url %}
            <img src="{{ c.zdjecie_url }}" class="zs-cand-img" loading="lazy" onerror="this.style.display='none'">
            {% endif %}
            <div class="zs-cand-info">
                <div class="zs-cand-name" title="{{ c.nazwa }}">{{ c.nazwa[:40] }}</div>
                <div class="zs-cand-price">{{ "%.0f"|format(c.cena or 0) }} zl</div>
            </div>
            {% if c.match_score >= 50 %}<span class="zs-cand-badge">ta sama kat.</span>{% elif c.match_score >= 30 %}<span class="zs-cand-badge" style="background:rgba(143,245,255,0.15);color:#8ff5ff">pasuje</span>{% endif %}
        </a>
        {% endfor %}
    </div>
    {% else %}
    <div style="padding:0 16px 14px;font-size:0.75rem;color:#64748b">Brak kandydatow do zestawu</div>
    {% endif %}
</div>
{% endfor %}

{% if not oferty %}
<div class="zs-card" style="text-align:center;padding:40px">
    <div style="font-size:2rem;margin-bottom:10px"><span class="material-symbols-outlined">loyalty</span></div>
    <div style="color:var(--text-muted)">Brak aktywnych ofert z produktami na stanie. Wystaw oferty na Allegro.</div>
</div>
{% endif %}

<a href="/narzedzia" style="display:inline-block;margin-top:10px;color:var(--text-muted);text-decoration:none;font-size:0.85rem">← Powrot do narzedzi</a>

<script nonce="{getattr(request, '_csp_nonce', '')}">
function filterZestawy(kat, btn) {
    var offers = document.querySelectorAll('.zs-offer');
    offers.forEach(function(o) {
        if (kat === 'all' || o.getAttribute('data-kat') === kat) {
            o.style.display = '';
        } else {
            o.style.display = 'none';
        }
    });
    document.querySelectorAll('.zs-filter-btn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
}
</script>
{% endblock %}
'''


# ============================================================
# SMART INSIGHTS — co wystawić, martwy stock, koszt leżenia
# ============================================================

@app.route('/narzedzia/smart-insights')
def smart_insights():
    from modules.database import get_db
    from modules.utils import ALLEGRO_PROWIZJE
    conn = get_db()

    # --- 1. CO WYSTAWIĆ NAJPIERW (ranking niewystawionych po zysku) ---
    from modules.magazynier import _paleta_koszt_szt

    niewystawione = conn.execute('''
        SELECT p.id, p.nazwa, p.ilosc, p.kategoria, p.cena_allegro, p.zdjecie_url,
               p.kod_magazynowy, p.asin, p.paleta_id,
               COALESCE(pal.nazwa, '') as paleta_nazwa
        FROM produkty p
        LEFT JOIN palety pal ON pal.id = p.paleta_id
        WHERE p.ilosc > 0
          AND NOT EXISTS (SELECT 1 FROM oferty o WHERE o.produkt_id = p.id AND o.status IN ('aktywna', 'draft'))
        ORDER BY p.cena_allegro DESC
    ''').fetchall()

    # Cache koszt/szt per paleta (żeby nie liczyć dla każdego produktu osobno)
    _koszt_cache = {}

    ranking = []
    for n in niewystawione:
        n = dict(n)
        cena_al = float(n.get('cena_allegro') or 0)
        kat = (n.get('kategoria') or 'inne').lower()
        prowizja_rate = ALLEGRO_PROWIZJE.get(kat, 0.11)
        pid = n.get('paleta_id')
        if pid not in _koszt_cache:
            _koszt_cache[pid] = _paleta_koszt_szt(conn, pid)
        koszt_szt = _koszt_cache[pid]
        zysk = cena_al - koszt_szt - (cena_al * prowizja_rate) if cena_al > 0 else 0
        n['zysk_szt'] = zysk
        n['koszt_szt'] = koszt_szt
        n['prowizja'] = prowizja_rate
        ranking.append(n)
    ranking.sort(key=lambda x: -x['zysk_szt'])

    # --- 2. MARTWY STOCK (30+ dni bez sprzedaży) ---
    from modules.smart_alerts import check_dead_stock, check_price_suggestions
    dead = check_dead_stock()

    # --- 3. SUGESTIE OBNIŻKI ---
    price_sugg = check_price_suggestions()

    # --- 4. KOSZT LEŻENIA per paleta ---
    palety_koszt = conn.execute('''
        SELECT pal.id, pal.nazwa, pal.cena_zakupu, pal.data_zakupu,
               COUNT(p.id) as prod_count,
               SUM(p.ilosc) as szt_remaining,
               SUM(CASE WHEN p.ilosc > 0 THEN 1 ELSE 0 END) as prod_remaining,
               COALESCE((SELECT SUM(s.cena * s.ilosc) FROM sprzedaze s
                JOIN produkty pp ON pp.id = s.produkt_id
                WHERE pp.paleta_id = pal.id
                AND COALESCE(s.status, '') NOT IN ('anulowana', 'anulowane', 'zwrot', '')), 0) as przychod
        FROM palety pal
        LEFT JOIN produkty p ON p.paleta_id = pal.id
        WHERE pal.cena_zakupu > 0
        GROUP BY pal.id
        HAVING szt_remaining > 0
        ORDER BY pal.data_zakupu ASC
    ''').fetchall()

    lezenie = []
    for pl in palety_koszt:
        pl = dict(pl)
        cena = float(pl['cena_zakupu'] or 0)
        przychod = float(pl['przychod'] or 0)
        data_z = pl.get('data_zakupu')
        if data_z:
            try:
                bought = datetime.strptime(str(data_z)[:10], '%Y-%m-%d')
                days = (datetime.now() - bought).days
            except:
                days = 0
        else:
            days = 0
        zamrozony = max(0, cena - przychod)
        pl['days'] = days
        pl['zamrozony'] = zamrozony
        pl['roi'] = (przychod / cena * 100) if cena > 0 else 0
        lezenie.append(pl)
    lezenie.sort(key=lambda x: -x['zamrozony'])
    total_zamrozony = sum(l['zamrozony'] for l in lezenie)

    return render_template_string(INSIGHTS_HTML,
        version=VERSION,
        ranking=ranking, dead=dead, price_sugg=price_sugg,
        lezenie=lezenie, total_zamrozony=total_zamrozony,
        active_narzedzia='active', active_home='', active_magazyn='',
        active_paletomat='', active_allegro='', active_monitor='')


INSIGHTS_HTML = '''{% extends "base.html" %}
{% block page_title %}Smart Insights{% endblock %}
{% block content %}
<style>
.si-section{margin-bottom:24px}
.si-title{font-size:1rem;font-weight:800;font-family:'Space Grotesk',sans-serif;display:flex;align-items:center;gap:8px;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid rgba(143,245,255,0.08)}
.si-card{background:rgba(13,15,26,0.6);border:1px solid rgba(255,255,255,0.06);padding:12px;margin-bottom:8px;display:flex;gap:12px;align-items:center;text-decoration:none;color:inherit;transition:all 0.2s}
.si-card:hover{border-color:rgba(143,245,255,0.2);background:rgba(143,245,255,0.03)}
.si-card-img{width:48px;height:48px;object-fit:contain;background:rgba(255,255,255,0.9);flex-shrink:0}
.si-card-info{flex:1;min-width:0}
.si-card-name{font-weight:700;font-size:0.82rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.si-card-meta{font-size:0.68rem;color:var(--text-muted);margin-top:2px}
.si-card-val{text-align:right;flex-shrink:0}
.si-stat-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;margin-bottom:20px}
.si-stat{background:rgba(13,15,26,0.8);border:1px solid rgba(255,255,255,0.06);padding:14px;text-align:center}
.si-stat-v{font-size:1.3rem;font-weight:800;font-family:'Space Grotesk',sans-serif}
.si-stat-l{font-size:0.58rem;text-transform:uppercase;letter-spacing:1px;color:var(--text-muted);margin-top:4px;font-weight:600}
.si-tabs{display:flex;gap:6px;margin-bottom:16px;flex-wrap:wrap}
.si-tab{padding:8px 16px;border:1px solid rgba(255,255,255,0.08);background:rgba(15,15,30,0.65);color:var(--text-muted);cursor:pointer;font-size:0.78rem;font-weight:700;font-family:'Space Grotesk',sans-serif;transition:all 0.2s}
.si-tab:hover,.si-tab.active{border-color:rgba(143,245,255,0.3);color:#8ff5ff;background:rgba(143,245,255,0.07)}
.si-hidden{display:none}
.si-good{color:#beee00}.si-warn{color:#f59e0b}.si-bad{color:#ef4444}.si-cyan{color:#8ff5ff}.si-purple{color:#a855f7}
</style>

<div style="text-align:center;padding:20px 0 10px">
    <h1 style="font-size:1.5rem;font-family:'Space Grotesk',sans-serif;font-weight:800;background:linear-gradient(135deg,#8ff5ff,#ff6b9b);-webkit-background-clip:text;-webkit-text-fill-color:transparent"><span class="material-symbols-outlined" style="color:#8ff5ff;-webkit-text-fill-color:#8ff5ff">psychology</span> SMART INSIGHTS</h1>
    <small style="color:var(--text-muted)">Co wystawic, co obnizyc, gdzie lezi kapital</small>
</div>

<!-- Stats -->
<div class="si-stat-row">
    <div class="si-stat" style="border-left:3px solid rgba(190,238,0,0.3)">
        <div class="si-stat-v si-good">{{ ranking|length }}</div>
        <div class="si-stat-l">Do wystawienia</div>
    </div>
    <div class="si-stat" style="border-left:3px solid rgba(239,68,68,0.3)">
        <div class="si-stat-v si-bad">{{ dead|length }}</div>
        <div class="si-stat-l">Martwy stock</div>
    </div>
    <div class="si-stat" style="border-left:3px solid rgba(245,158,11,0.3)">
        <div class="si-stat-v si-warn">{{ price_sugg|length }}</div>
        <div class="si-stat-l">Do obniżki</div>
    </div>
    <div class="si-stat" style="border-left:3px solid rgba(168,85,247,0.3)">
        <div class="si-stat-v si-purple">{{ "{:,.0f}".format(total_zamrozony).replace(",", " ") }} zl</div>
        <div class="si-stat-l">Zamrożony kapitał</div>
    </div>
</div>

<!-- Tabs -->
<div class="si-tabs">
    <button class="si-tab active" onclick="showTab('ranking', this)"><span class="material-symbols-outlined" style="font-size:0.9rem;vertical-align:middle">rocket_launch</span> Co wystawić ({{ ranking|length }})</button>
    <button class="si-tab" onclick="showTab('dead', this)"><span class="material-symbols-outlined" style="font-size:0.9rem;vertical-align:middle">warning</span> Martwy stock ({{ dead|length }})</button>
    <button class="si-tab" onclick="showTab('prices', this)"><span class="material-symbols-outlined" style="font-size:0.9rem;vertical-align:middle">trending_down</span> Obniżki ({{ price_sugg|length }})</button>
    <button class="si-tab" onclick="showTab('kapital', this)"><span class="material-symbols-outlined" style="font-size:0.9rem;vertical-align:middle">account_balance</span> Koszt leżenia</button>
</div>

<!-- TAB: Co wystawić najpierw -->
<div id="tab-ranking" class="si-section">
    <div class="si-title" style="color:#beee00"><span class="material-symbols-outlined">rocket_launch</span> Co wystawić najpierw — ranking po zysku</div>
    {% for item in ranking[:20] %}
    <a href="/magazyn/produkt/{{ item.kod_magazynowy or item.id }}" class="si-card">
        {% if item.zdjecie_url %}<img src="{{ item.zdjecie_url }}" class="si-card-img" loading="lazy" onerror="this.style.display='none'">{% endif %}
        <div class="si-card-info">
            <div class="si-card-name">{{ item.nazwa[:50] }}</div>
            <div class="si-card-meta">{{ item.ilosc }} szt · {{ item.kategoria or 'inne' }} · {{ item.paleta_nazwa or '?' }}</div>
        </div>
        <div class="si-card-val">
            <div style="font-weight:800;font-size:0.95rem" class="{% if item.zysk_szt > 50 %}si-good{% elif item.zysk_szt > 0 %}si-warn{% else %}si-bad{% endif %}">{{ "%.0f"|format(item.zysk_szt) }} zl</div>
            <div style="font-size:0.6rem;color:var(--text-muted)">zysk/szt</div>
            <div style="font-size:0.72rem;color:#8ff5ff;font-weight:600">{{ "%.0f"|format(item.cena_allegro or 0) }} zl</div>
        </div>
    </a>
    {% endfor %}
    {% if not ranking %}<div style="padding:20px;text-align:center;color:var(--text-muted)">Wszystko wystawione! 🎉</div>{% endif %}
</div>

<!-- TAB: Martwy stock -->
<div id="tab-dead" class="si-section si-hidden">
    <div class="si-title" style="color:#ef4444"><span class="material-symbols-outlined">warning</span> Martwy stock — 30+ dni bez sprzedaży</div>
    {% for item in dead[:20] %}
    <a href="/magazyn/produkt/{{ item.kod_magazynowy or item.pid }}" class="si-card" style="border-left:3px solid rgba(239,68,68,0.3)">
        <div class="si-card-info">
            <div class="si-card-name">{{ item.tytul[:50] }}</div>
            <div class="si-card-meta">{{ item.wyswietlenia or 0 }} wyśw. · {{ item.ilosc }} szt · wystawiono {{ item.data_wystawienia }}</div>
        </div>
        <div class="si-card-val">
            <div style="font-weight:800;font-size:0.95rem;color:#ef4444">{{ "%.0f"|format(item.cena or 0) }} zl</div>
            <div style="font-size:0.6rem;color:var(--text-muted)">aktywna cena</div>
        </div>
    </a>
    {% endfor %}
    {% if not dead %}<div style="padding:20px;text-align:center;color:var(--text-muted)">Brak martwego stocku ✅</div>{% endif %}
</div>

<!-- TAB: Sugestie obniżki -->
<div id="tab-prices" class="si-section si-hidden">
    <div class="si-title" style="color:#f59e0b"><span class="material-symbols-outlined">trending_down</span> Sugestie obniżki — dużo views, 0 sprzedaży</div>
    {% for item in price_sugg[:15] %}
    {% set discount = 0.12 if (item.wyswietlenia or 0) > 200 else 0.10 %}
    {% set suggested = (item.cena or 0) * (1 - discount) %}
    <a href="/magazyn/produkt/{{ item.pid }}" class="si-card" style="border-left:3px solid rgba(245,158,11,0.3)">
        <div class="si-card-info">
            <div class="si-card-name">{{ item.tytul[:50] }}</div>
            <div class="si-card-meta">👁 {{ item.wyswietlenia or 0 }} wyśw. · ❤️ {{ item.obserwujacych or 0 }} obs.</div>
        </div>
        <div class="si-card-val">
            <div style="font-size:0.75rem;color:var(--text-muted);text-decoration:line-through">{{ "%.0f"|format(item.cena or 0) }} zl</div>
            <div style="font-weight:800;font-size:0.95rem;color:#f59e0b">→ {{ "%.0f"|format(suggested) }} zl</div>
            <div style="font-size:0.6rem;color:#ef4444">-{{ "%.0f"|format(discount * 100) }}%</div>
        </div>
    </a>
    {% endfor %}
    {% if not price_sugg %}<div style="padding:20px;text-align:center;color:var(--text-muted)">Brak kandydatów do obniżki ✅</div>{% endif %}
</div>

<!-- TAB: Koszt leżenia -->
<div id="tab-kapital" class="si-section si-hidden">
    <div class="si-title" style="color:#a855f7"><span class="material-symbols-outlined">account_balance</span> Zamrożony kapitał per paleta</div>
    {% for pl in lezenie[:20] %}
    <a href="/magazyn/paleta-id/{{ pl.id }}" class="si-card" style="border-left:3px solid rgba(168,85,247,0.3)">
        <div class="si-card-info">
            <div class="si-card-name">{{ pl.nazwa }}</div>
            <div class="si-card-meta">{{ pl.days }} dni · {{ pl.szt_remaining }} szt · przychód: {{ "%.0f"|format(pl.przychod) }} zł z {{ "%.0f"|format(pl.cena_zakupu) }} zł</div>
        </div>
        <div class="si-card-val">
            <div style="font-weight:800;font-size:0.95rem" class="{% if pl.zamrozony > 500 %}si-bad{% elif pl.zamrozony > 200 %}si-warn{% else %}si-good{% endif %}">{{ "%.0f"|format(pl.zamrozony) }} zl</div>
            <div style="font-size:0.6rem;color:var(--text-muted)">zamrożone</div>
            <div style="font-size:0.72rem;font-weight:600" class="{% if pl.roi > 80 %}si-good{% elif pl.roi > 40 %}si-warn{% else %}si-bad{% endif %}">ROI {{ "%.0f"|format(pl.roi) }}%</div>
        </div>
    </a>
    {% endfor %}
    <div style="margin-top:12px;padding:12px;background:rgba(168,85,247,0.06);border:1px solid rgba(168,85,247,0.15);font-size:0.8rem">
        <strong style="color:#a855f7">Łącznie zamrożony kapitał: {{ "{:,.0f}".format(total_zamrozony).replace(",", " ") }} zł</strong>
    </div>
</div>

<a href="/narzedzia" style="display:inline-block;margin-top:10px;color:var(--text-muted);text-decoration:none;font-size:0.85rem">← Powrot do narzedzi</a>

<script nonce="{getattr(request, '_csp_nonce', '')}">
function showTab(id, btn) {
    document.querySelectorAll('.si-section').forEach(function(s) { s.classList.add('si-hidden'); });
    document.getElementById('tab-' + id).classList.remove('si-hidden');
    document.querySelectorAll('.si-tab').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
}
</script>
{% endblock %}
'''


# EXPORT

@app.route('/narzedzia/fix-currency', methods=['POST'])
@require_admin
def narzedzia_fix_currency():
    """Backfill: przelicz stare zamowienia (zapisane jako PLN gdy faktycznie HUF/CZK/EUR).

    Leci po ostatnich 500 zamowieniach z allegro_order_id, fetch z Allegro,
    jesli currency != PLN -> przelicza po aktualnym kursie NBP, UPDATE sprzedaze.
    """
    _validate_csrf_or_abort()
    from modules.database import get_db, execute_with_retry, commit_with_retry
    from modules.allegro_api import is_authenticated, get_order_details
    from modules.fx_rates import get_pln_rate
    import sqlite3 as _sqlite3
    import time as _time

    if not is_authenticated():
        return jsonify({'ok': False, 'error': 'Allegro nie zalogowany'})

    conn = get_db()
    rows = conn.execute(
        "SELECT id, allegro_order_id, cena, ilosc, nazwa FROM sprzedaze "
        "WHERE allegro_order_id IS NOT NULL AND allegro_order_id != '' "
        "ORDER BY data_sprzedazy DESC LIMIT 500"
    ).fetchall()

    fixed = 0
    skipped = 0
    errors = []

    for row in rows:
        try:
            order_id = row['allegro_order_id']
            order, err = get_order_details(order_id)
            if err or not order:
                skipped += 1
                continue

            items = order.get('lineItems', [])
            target_item = None
            for item in items:
                if (item.get('offer') or {}).get('name', '')[:50] == (row['nazwa'] or '')[:50]:
                    target_item = item
                    break
            if not target_item and items:
                target_item = items[0]
            if not target_item:
                skipped += 1
                continue

            price_obj = target_item.get('price') or {}
            cena_orig = float(price_obj.get('amount', 0))
            currency = (price_obj.get('currency') or 'PLN').upper()

            if currency == 'PLN':
                skipped += 1
                continue

            fx = get_pln_rate(currency)
            cena_pln = round(cena_orig * fx, 2)
            # RETRY: 'database is locked' - auto-sync Allegro moze trzymac WRITE lock
            _updated_ok = False
            for _att in range(4):
                try:
                    execute_with_retry(conn, 'UPDATE sprzedaze SET cena = ? WHERE id = ?', (cena_pln, row['id']))
                    _updated_ok = True
                    break
                except _sqlite3.OperationalError as _e:
                    if 'database is locked' in str(_e).lower() and _att < 3:
                        _w = 2 ** _att  # 1, 2, 4, 8
                        print(f"[RETRY] fix_currency UPDATE locked #{row['id']} (proba {_att+1}/4), sleep {_w}s")
                        _time.sleep(_w)
                        continue
                    raise
            if not _updated_ok:
                errors.append(f'#{row["id"]}: locked po 4 probach')
                continue
            fixed += 1
            print(f'[FIX-CUR] #{row["id"]}: {cena_orig} {currency} -> {cena_pln} PLN (kurs {fx})')
        except Exception as e:
            errors.append(f'#{row["id"]}: {str(e)[:100]}')

    try:
        commit_with_retry(conn)
    except _sqlite3.OperationalError as e:
        errors.append(f'commit lock: {str(e)[:80]}')
    return jsonify({
        'ok': True,
        'fixed': fixed,
        'skipped': skipped,
        'errors': errors[:10],
        'total_checked': len(rows),
        'msg': f'Naprawiono {fixed} zamowien (z {len(rows)} sprawdzonych).'
    })


# ════════════════════════════════════════════════════════════════════════
# UZUPEŁNIJ ZDJECIA AMAZON dla produktów z ASIN ale bez zdjecie_url
# ════════════════════════════════════════════════════════════════════════

@app.route('/narzedzia/scal-duplikaty-magazyn', methods=['POST'])
@require_admin
def narzedzia_scal_duplikaty_magazyn():
    """Scala duplikaty produktow w magazynie po (paleta_id, nazwa).

    Kontekst: Maciek importowal Excel multi-paleta WIELOKROTNIE (przed split na
    aktualny + historyczny + obie wersje wgrywaly osobne rekordy). Na /do-wystawienia
    widzi ten sam produkt 2-3 razy z różnymi ilościami.

    Logika:
    1. Grupuj produkty po (paleta_id, nazwa[:60])
    2. Dla każdej grupy z >1 rekordami:
       - Zachowaj NAJSTARSZY (najmniejszy id) - ma referencje w sprzedaze/oferty
       - Sumuj ilosc z wszystkich aktywnych (status NIE IN sprzedany/uszkodzony)
       - Kasuj resztę (osierocone)
    3. Status sprzedany/uszkodzony zostają osobno (historia sprzedaży)

    Returns: {ok, merged_groups, deleted_rows, msg}
    """
    _validate_csrf_or_abort()
    from modules.database import get_db
    conn = get_db()
    try:
        # Znajdz grupy duplikatów (paleta_id + nazwa[:60], tylko aktywne magazynowe)
        groups = conn.execute("""
            SELECT paleta_id, substr(nazwa, 1, 60) as nazwa_klucz,
                   COUNT(*) as cnt, GROUP_CONCAT(id) as ids,
                   SUM(ilosc) as suma_ilosc
            FROM produkty
            WHERE paleta_id IS NOT NULL
              AND status IN ('magazyn', 'wystawiony', 'szkic')
              AND COALESCE(dla_siebie, 0) = 0
            GROUP BY paleta_id, substr(nazwa, 1, 60)
            HAVING COUNT(*) > 1
        """).fetchall()

        merged = 0
        deleted = 0
        for g in groups:
            ids = [int(x) for x in g['ids'].split(',')]
            keep_id = min(ids)  # Najstarszy - ma najwiecej referencji
            drop_ids = [i for i in ids if i != keep_id]
            # Update keep_id na sumę ilości
            conn.execute('UPDATE produkty SET ilosc = ? WHERE id = ?',
                         (g['suma_ilosc'] or 0, keep_id))
            # Skasuj duplikaty (ale tylko jeśli nie mają referencji w sprzedaze)
            for did in drop_ids:
                ref_count = conn.execute(
                    'SELECT COUNT(*) FROM sprzedaze WHERE produkt_id = ?', (did,)
                ).fetchone()[0]
                if ref_count == 0:
                    conn.execute('DELETE FROM produkty WHERE id = ?', (did,))
                    deleted += 1
                else:
                    # Ma sprzedaże - tylko ustaw ilosc=0 żeby nie pokazywało się
                    conn.execute('UPDATE produkty SET ilosc = 0 WHERE id = ?', (did,))
            merged += 1
        conn.commit()
        return jsonify({
            'ok': True,
            'merged_groups': merged,
            'deleted_rows': deleted,
            'msg': f'Scalono {merged} grup duplikatów, skasowano {deleted} rekordów (osierocone bez sprzedaży)'
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]})


@app.route('/narzedzia/uzupelnij-zdjecia-amazon', methods=['POST'])
@require_admin
def narzedzia_uzupelnij_zdjecia_amazon():
    """Naprawa pomieszania ASIN/EAN + uzupełnij zdjęcia.

    Trzy operacje w jednej akcji:
    1. Przenieś ASIN z pola ean → asin (gdy ean wygląda jak ASIN: B0XXXXXXXX)
    2. Wyczyść ean dla rekordów z ASIN-em (zostanie puste)
    3. Uzupełnij zdjecie_url URL-em Amazon dla produktów z ASIN ale bez zdjęcia
    """
    _validate_csrf_or_abort()
    from modules.database import get_db
    conn = get_db()
    try:
        # KROK 1: przenieś ASIN z ean → asin (produkty Macka z multi-paleta importu)
        # ASIN to "B0" + 8-10 znaków alfanum (10-12 total). EAN to 8-13 cyfr.
        # FIX 2026-05-28: usunieto GLOB pattern - nie matchowal, zostal LIKE.
        # Plus rozszerzono LENGTH 10-13 zeby objac dluzsze ASIN-y B0XXXXXXXXXX.
        r1 = conn.execute("""
            UPDATE produkty
            SET asin = UPPER(TRIM(ean)), ean = ''
            WHERE (asin IS NULL OR asin = '')
              AND ean IS NOT NULL
              AND LENGTH(TRIM(ean)) BETWEEN 10 AND 13
              AND UPPER(TRIM(ean)) LIKE 'B0%'
        """)
        moved = r1.rowcount

        # KROK 2: też wyczyść ean gdy DUPLIKAT asin (kiedy zarówno ean jak asin maja ten sam ASIN)
        r2 = conn.execute("""
            UPDATE produkty SET ean = ''
            WHERE ean IS NOT NULL AND ean != ''
              AND asin IS NOT NULL AND asin != ''
              AND UPPER(TRIM(ean)) = UPPER(TRIM(asin))
        """)
        cleaned = r2.rowcount

        # KROK 3: uzupełnij/NAPRAW zdjęcia Amazon dla produktów z ASIN.
        # FIX 2026-05-28: format URL zmieniony z 'images/I/{ASIN}' (NIE DZIAŁA,
        # Amazon wymaga image_id w /I/, nie ASIN) na 'images/P/{ASIN}.01._SL500_.jpg'
        # (prawidłowy format dla ASIN bez scrapowania).
        # Plus warunek nie tylko gdy zdjecie_url puste, ale tez gdy ma stary
        # ZŁY format (zawiera 'images/I/'+ASIN albo placeholder z nieistniejacym URL).
        r3 = conn.execute("""
            UPDATE produkty
            SET zdjecie_url = 'https://m.media-amazon.com/images/P/' || UPPER(TRIM(asin)) || '.01._SL500_.jpg'
            WHERE asin IS NOT NULL AND TRIM(asin) != ''
              AND LENGTH(TRIM(asin)) >= 8
              AND TRIM(UPPER(asin)) NOT IN ('NONE', 'NAN', 'N/A')
              AND (
                zdjecie_url IS NULL
                OR zdjecie_url = ''
                OR zdjecie_url LIKE '%images/I/%'  -- stary nieprawidłowy format
              )
        """)
        photos = r3.rowcount

        conn.commit()
        msg_parts = []
        if moved: msg_parts.append(f'{moved} ASIN przeniesiono z EAN do ASIN')
        if cleaned: msg_parts.append(f'{cleaned} EAN-ów wyczyszczono (duplikat ASIN)')
        if photos: msg_parts.append(f'{photos} zdjęć uzupełniono')
        if not msg_parts:
            msg_parts.append('nic do naprawy (już OK)')
        msg = '; '.join(msg_parts)
        return jsonify({
            'ok': True,
            'updated': photos,
            'moved_to_asin': moved,
            'cleaned_ean': cleaned,
            'msg': msg
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]})


# ════════════════════════════════════════════════════════════════════════
# DIAGNOZA CEN SPRZEDAZY - znalezienie suspicious rekordow (np × 100 bugi)
# ════════════════════════════════════════════════════════════════════════

@app.route('/narzedzia/diagnoza-cen', methods=['GET'])
@require_admin
def narzedzia_diagnoza_cen():
    """Pokazuje TOP10 wysokich + niskich cen + suspicious + statystyki.

    Suspicious = cena > 5000 zł (potencjalne ×100 bugi, np 468.70 -> 46870).
    Plus mediana referencyjna dla porownania.
    """
    from modules.database import get_db
    conn = get_db()

    # Statystyki ogolne
    stats = conn.execute('''
        SELECT COUNT(*) as cnt,
               MIN(cena) as min_cena, MAX(cena) as max_cena,
               AVG(cena) as avg_cena
        FROM sprzedaze WHERE cena IS NOT NULL AND cena > 0
    ''').fetchone()

    # Mediana (SQLite nie ma percentile, robimy przez ORDER BY + LIMIT)
    median_row = conn.execute('''
        SELECT cena FROM sprzedaze WHERE cena > 0
        ORDER BY cena LIMIT 1 OFFSET (SELECT COUNT(*)/2 FROM sprzedaze WHERE cena > 0)
    ''').fetchone()
    median = median_row['cena'] if median_row else 0

    # TOP10 najwyzszych cen
    top_high = conn.execute('''
        SELECT id, substr(nazwa,1,60) as nazwa, cena, ilosc,
               cena * ilosc as total, data_sprzedazy, status, allegro_order_id
        FROM sprzedaze WHERE cena > 0
        ORDER BY cena DESC LIMIT 10
    ''').fetchall()

    # TOP10 najnizszych cen (NIE 0)
    top_low = conn.execute('''
        SELECT id, substr(nazwa,1,60) as nazwa, cena, ilosc, data_sprzedazy
        FROM sprzedaze WHERE cena > 0
        ORDER BY cena ASC LIMIT 10
    ''').fetchall()

    # Suspicious (cena > 5000 zł) - potencjalne ×100 bugi
    suspicious_rows = conn.execute('''
        SELECT id, substr(nazwa,1,60) as nazwa, cena, ilosc,
               data_sprzedazy, status, allegro_order_id
        FROM sprzedaze WHERE cena > 5000
        ORDER BY cena DESC LIMIT 50
    ''').fetchall()

    def _row_html(row, suspicious=False):
        suspicious_badge = ''
        if suspicious or (row['cena'] or 0) > 5000:
            div100 = (row['cena'] or 0) / 100
            suspicious_badge = (
                f'<span style="background:rgba(239,68,68,0.15);color:#ef4444;'
                f'padding:2px 6px;border-radius:4px;font-size:0.7rem;margin-left:6px">'
                f'÷100 = {div100:.2f} zł</span>'
            )
        return (
            f'<tr style="border-bottom:1px solid rgba(255,255,255,0.05)">'
            f'<td style="padding:8px 10px;color:#64748b;font-family:monospace;font-size:0.78rem">#{row["id"]}</td>'
            f'<td style="padding:8px 10px;color:#cbd5e1;font-size:0.82rem">{(row["nazwa"] or "")}</td>'
            f'<td style="padding:8px 10px;text-align:right;color:#beee00;font-weight:700;font-family:monospace">{(row["cena"] or 0):,.2f} zł{suspicious_badge}</td>'
            f'<td style="padding:8px 10px;text-align:center;color:#94a3b8">{row.get("ilosc") if hasattr(row, "get") else row["ilosc"]}</td>'
            f'<td style="padding:8px 10px;color:#64748b;font-size:0.78rem">{(row["data_sprzedazy"] or "")[:16]}</td>'
            f'</tr>'
        )

    def _safe_get(row, key, default=''):
        try:
            return row[key]
        except (KeyError, IndexError):
            return default

    def _row_html_safe(row, suspicious=False):
        cena = _safe_get(row, 'cena', 0) or 0
        suspicious_badge = ''
        if suspicious or cena > 5000:
            suspicious_badge = (
                f'<span style="background:rgba(239,68,68,0.15);color:#ef4444;'
                f'padding:2px 6px;border-radius:4px;font-size:0.7rem;margin-left:6px">'
                f'÷100 = {cena/100:.2f} zł</span>'
            )
        return (
            f'<tr style="border-bottom:1px solid rgba(255,255,255,0.05)">'
            f'<td style="padding:8px 10px;color:#64748b;font-family:monospace;font-size:0.78rem">#{_safe_get(row, "id")}</td>'
            f'<td style="padding:8px 10px;color:#cbd5e1;font-size:0.82rem">{_safe_get(row, "nazwa", "")}</td>'
            f'<td style="padding:8px 10px;text-align:right;color:#beee00;font-weight:700;font-family:monospace">{cena:,.2f} zł{suspicious_badge}</td>'
            f'<td style="padding:8px 10px;text-align:center;color:#94a3b8">{_safe_get(row, "ilosc", "")}</td>'
            f'<td style="padding:8px 10px;color:#64748b;font-size:0.78rem">{str(_safe_get(row, "data_sprzedazy", ""))[:16]}</td>'
            f'</tr>'
        )

    rows_high = ''.join(_row_html_safe(r) for r in top_high)
    rows_low = ''.join(_row_html_safe(r) for r in top_low)
    rows_suspicious = ''.join(_row_html_safe(r, suspicious=True) for r in suspicious_rows)

    n_suspicious = len(suspicious_rows)

    suspicious_section = ''
    if n_suspicious > 0:
        suspicious_section = f'''
        <div class="card" style="padding:0;margin-top:18px;overflow:hidden;border:1px solid rgba(239,68,68,0.3)">
            <div style="padding:14px 18px;background:rgba(239,68,68,0.08);border-bottom:1px solid rgba(239,68,68,0.2)">
                <div style="font-weight:700;color:#ef4444;font-size:0.95rem">
                    <span class="material-symbols-outlined" style="font-size:1rem;vertical-align:middle">warning</span>
                    {n_suspicious} suspicious rekordow (cena > 5000 zł)
                </div>
                <div style="font-size:0.8rem;color:#94a3b8;margin-top:4px">
                    Potencjalne ×100 bugi — w czerwonym badge proponowana wartość po podzieleniu przez 100.
                    Jeśli realne ceny były ~50-500 zł a tutaj są 5000-50000 zł, to bug.
                </div>
            </div>
            <div style="overflow-x:auto;max-height:500px;overflow-y:auto">
                <table style="width:100%;border-collapse:collapse;font-size:0.85rem">
                    <thead style="position:sticky;top:0;background:#0f1019">
                        <tr>
                            <th style="padding:10px 12px;text-align:left;color:#8ff5ff;font-size:0.72rem;letter-spacing:1px;text-transform:uppercase">ID</th>
                            <th style="padding:10px 12px;text-align:left;color:#8ff5ff;font-size:0.72rem;letter-spacing:1px;text-transform:uppercase">Nazwa</th>
                            <th style="padding:10px 12px;text-align:right;color:#8ff5ff;font-size:0.72rem;letter-spacing:1px;text-transform:uppercase">Cena</th>
                            <th style="padding:10px 12px;text-align:center;color:#8ff5ff;font-size:0.72rem;letter-spacing:1px;text-transform:uppercase">Ilosc</th>
                            <th style="padding:10px 12px;text-align:left;color:#8ff5ff;font-size:0.72rem;letter-spacing:1px;text-transform:uppercase">Data</th>
                        </tr>
                    </thead>
                    <tbody>{rows_suspicious}</tbody>
                </table>
            </div>
            <div style="padding:14px 18px;border-top:1px solid rgba(239,68,68,0.2);background:rgba(0,0,0,0.2)">
                <form method="POST" action="/narzedzia/diagnoza-cen/napraw" onsubmit="return confirm('Podzielic {n_suspicious} rekordow przez 100? OPERACJA NIEODWRACALNA bez backupu DB!\\n\\nKliknij Anuluj jesli ceny SA prawidlowe (drogie produkty).');">
                    <input type="hidden" name="csrf_token" value="{generate_csrf()}">
                    <button type="submit" style="padding:10px 22px;background:linear-gradient(135deg,#ef4444,#dc2626);color:#fff;border:none;border-radius:8px;font-weight:700;cursor:pointer;font-size:0.88rem">
                        <span class="material-symbols-outlined" style="font-size:1rem;vertical-align:middle">build</span>
                        Napraw - podziel wszystkie przez 100
                    </button>
                </form>
            </div>
        </div>
        '''
    else:
        suspicious_section = '''
        <div class="card" style="padding:18px;margin-top:18px;background:rgba(34,197,94,0.06);border:1px solid rgba(34,197,94,0.3)">
            <div style="color:#22c55e;font-weight:700">
                <span class="material-symbols-outlined" style="font-size:1rem;vertical-align:middle">check_circle</span>
                Brak suspicious rekordow (cena > 5000 zł).
            </div>
            <div style="font-size:0.82rem;color:#94a3b8;margin-top:4px">
                Wszystkie ceny w bazie sa w rozsadnym zakresie - prawdopodobnie nie ma bugu ×100.
            </div>
        </div>
        '''

    html = f'''
    <div class="hdr"><h1><span class="material-symbols-outlined">price_check</span> DIAGNOZA CEN SPRZEDAŻY</h1>
        <div style="font-size:0.85rem;color:#94a3b8;margin-top:6px">Statystyki + TOP10 najwyzsze/najnizsze + suspicious (potencjalne ×100 bugi)</div>
    </div>

    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:20px">
        <div class="card" style="padding:14px;text-align:center">
            <div style="font-size:1.5rem;font-weight:800;color:#8ff5ff">{stats['cnt']}</div>
            <div style="font-size:0.7rem;color:#94a3b8;text-transform:uppercase;letter-spacing:1px">sprzedaży</div>
        </div>
        <div class="card" style="padding:14px;text-align:center">
            <div style="font-size:1.4rem;font-weight:800;color:#beee00">{(stats['min_cena'] or 0):,.2f} zł</div>
            <div style="font-size:0.7rem;color:#94a3b8;text-transform:uppercase;letter-spacing:1px">min cena</div>
        </div>
        <div class="card" style="padding:14px;text-align:center">
            <div style="font-size:1.4rem;font-weight:800;color:#22c55e">{median:,.2f} zł</div>
            <div style="font-size:0.7rem;color:#94a3b8;text-transform:uppercase;letter-spacing:1px">mediana</div>
        </div>
        <div class="card" style="padding:14px;text-align:center">
            <div style="font-size:1.4rem;font-weight:800;color:#a78bfa">{(stats['avg_cena'] or 0):,.2f} zł</div>
            <div style="font-size:0.7rem;color:#94a3b8;text-transform:uppercase;letter-spacing:1px">średnia</div>
        </div>
        <div class="card" style="padding:14px;text-align:center">
            <div style="font-size:1.4rem;font-weight:800;color:#ef4444">{(stats['max_cena'] or 0):,.2f} zł</div>
            <div style="font-size:0.7rem;color:#94a3b8;text-transform:uppercase;letter-spacing:1px">max cena</div>
        </div>
    </div>

    {suspicious_section}

    <div class="card" style="padding:0;margin-top:18px;overflow:hidden">
        <div style="padding:14px 18px;background:rgba(143,245,255,0.06);border-bottom:1px solid rgba(143,245,255,0.15)">
            <div style="font-weight:700;color:#8ff5ff;font-size:0.95rem">
                <span class="material-symbols-outlined" style="font-size:1rem;vertical-align:middle">trending_up</span>
                TOP 10 najwyższych cen
            </div>
        </div>
        <div style="overflow-x:auto">
            <table style="width:100%;border-collapse:collapse;font-size:0.85rem">
                <thead><tr>
                    <th style="padding:10px 12px;text-align:left;color:#8ff5ff;font-size:0.72rem;letter-spacing:1px;text-transform:uppercase">ID</th>
                    <th style="padding:10px 12px;text-align:left;color:#8ff5ff;font-size:0.72rem;letter-spacing:1px;text-transform:uppercase">Nazwa</th>
                    <th style="padding:10px 12px;text-align:right;color:#8ff5ff;font-size:0.72rem;letter-spacing:1px;text-transform:uppercase">Cena</th>
                    <th style="padding:10px 12px;text-align:center;color:#8ff5ff;font-size:0.72rem;letter-spacing:1px;text-transform:uppercase">Ilość</th>
                    <th style="padding:10px 12px;text-align:left;color:#8ff5ff;font-size:0.72rem;letter-spacing:1px;text-transform:uppercase">Data</th>
                </tr></thead>
                <tbody>{rows_high}</tbody>
            </table>
        </div>
    </div>

    <div class="card" style="padding:0;margin-top:18px;overflow:hidden">
        <div style="padding:14px 18px;background:rgba(143,245,255,0.06);border-bottom:1px solid rgba(143,245,255,0.15)">
            <div style="font-weight:700;color:#8ff5ff;font-size:0.95rem">
                <span class="material-symbols-outlined" style="font-size:1rem;vertical-align:middle">trending_down</span>
                TOP 10 najniższych cen
            </div>
        </div>
        <div style="overflow-x:auto">
            <table style="width:100%;border-collapse:collapse;font-size:0.85rem">
                <thead><tr>
                    <th style="padding:10px 12px;text-align:left;color:#8ff5ff;font-size:0.72rem;letter-spacing:1px;text-transform:uppercase">ID</th>
                    <th style="padding:10px 12px;text-align:left;color:#8ff5ff;font-size:0.72rem;letter-spacing:1px;text-transform:uppercase">Nazwa</th>
                    <th style="padding:10px 12px;text-align:right;color:#8ff5ff;font-size:0.72rem;letter-spacing:1px;text-transform:uppercase">Cena</th>
                    <th style="padding:10px 12px;text-align:center;color:#8ff5ff;font-size:0.72rem;letter-spacing:1px;text-transform:uppercase">Ilość</th>
                    <th style="padding:10px 12px;text-align:left;color:#8ff5ff;font-size:0.72rem;letter-spacing:1px;text-transform:uppercase">Data</th>
                </tr></thead>
                <tbody>{rows_low}</tbody>
            </table>
        </div>
    </div>

    <div style="margin-top:18px"><a href="/narzedzia" class="back">← Narzędzia</a></div>
    '''

    return render_template_string('''{% extends "base.html" %}
{% block page_title %}Diagnoza cen{% endblock %}
{% block content %}
<div style="max-width:1280px;margin:auto;padding:20px">
''' + html + '''
</div>
{% endblock %}''')


@app.route('/narzedzia/diagnoza-cen/napraw', methods=['POST'])
@require_admin
def narzedzia_diagnoza_cen_napraw():
    """Dziel wszystkie suspicious (cena > 5000) przez 100. NIEODWRACALNE bez backupu DB."""
    _validate_csrf_or_abort()
    from modules.database import get_db
    conn = get_db()

    # Pobierz suspicious i dziel
    rows = conn.execute('SELECT id, cena FROM sprzedaze WHERE cena > 5000').fetchall()
    fixed = 0
    for row in rows:
        try:
            new_cena = round(row['cena'] / 100, 2)
            conn.execute('UPDATE sprzedaze SET cena = ? WHERE id = ?', (new_cena, row['id']))
            fixed += 1
        except Exception:
            pass
    conn.commit()

    msg = f'Naprawiono {fixed} rekordów (cena podzielona przez 100).'
    inner_html = f'''
    <div class="hdr"><h1><span class="material-symbols-outlined">check_circle</span> NAPRAWIONO</h1></div>
    <div class="alert alert-ok" style="background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);color:#22c55e;padding:14px 18px;border-radius:10px;margin:18px 0">{msg}</div>
    <a href="/narzedzia/diagnoza-cen" class="btn btn-p">Sprawdź ponownie</a>
    <a href="/narzedzia" class="back" style="margin-left:8px">← Narzędzia</a>
    '''
    return render_template_string('''{% extends "base.html" %}
{% block page_title %}Naprawiono{% endblock %}
{% block content %}
<div style="max-width:900px;margin:auto;padding:20px">
''' + inner_html + '''
</div>
{% endblock %}''')


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

    return render_template('cloud_export.html', files=files, export_dir=export_dir)

# ============================================================
# GOAL (HYUNDAI i30 N) - ZARZĄDZANIE
# ============================================================

@app.route('/goal/details')
def goal_details():
    """Szczegóły celu finansowego"""
    from modules.simple_goal_manager import get_goal_stats
    
    goal = get_goal_stats()
    
    # Calculate savings deficit info
    monthly_needed = goal['remaining'] / 12 if goal['remaining'] > 0 else 0
    progress_val = min(goal.get('progress', 0), 100)

    html = f'''
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{goal['name']} - Goal Details</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Manrope:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: 'Manrope', sans-serif;
            background: #0e0e10;
            color: #e0e0e0;
            min-height: 100vh;
            overflow-x: hidden;
        }}

        body::before {{
            content: '';
            position: fixed;
            inset: 0;
            background-image:
                linear-gradient(rgba(143,245,255,0.03) 1px, transparent 1px),
                linear-gradient(90deg, rgba(143,245,255,0.03) 1px, transparent 1px);
            background-size: 60px 60px;
            pointer-events: none;
            z-index: 0;
        }}

        .page-wrap {{
            position: relative;
            z-index: 1;
            max-width: 720px;
            margin: 0 auto;
            padding: 24px 16px 60px;
        }}

        /* ---- Back link ---- */
        .back-link {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            color: #8ff5ff;
            text-decoration: none;
            font-size: 0.85rem;
            font-weight: 600;
            letter-spacing: 0.5px;
            text-transform: uppercase;
            margin-bottom: 28px;
            transition: color .2s;
        }}
        .back-link:hover {{ color: #fff; }}
        .back-link .material-symbols-outlined {{ font-size: 18px; }}

        /* ---- Glass panel base ---- */
        .glass {{
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(143,245,255,0.12);
            border-radius: 18px;
            backdrop-filter: blur(18px);
            -webkit-backdrop-filter: blur(18px);
            padding: 28px;
            margin-bottom: 20px;
        }}

        /* ---- Hero card ---- */
        .hero-card {{
            text-align: center;
            padding: 36px 28px 32px;
            position: relative;
            overflow: hidden;
        }}
        .hero-card::before {{
            content: '';
            position: absolute;
            top: -60%;
            left: -20%;
            width: 140%;
            height: 120%;
            background: radial-gradient(ellipse at center top, rgba(143,245,255,0.07) 0%, transparent 65%);
            pointer-events: none;
        }}

        .hero-icon {{
            width: 80px;
            height: 80px;
            margin: 0 auto 18px;
            border-radius: 50%;
            background: linear-gradient(135deg, rgba(143,245,255,0.15), rgba(255,107,155,0.12));
            border: 2px solid rgba(143,245,255,0.3);
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .hero-icon .material-symbols-outlined {{
            font-size: 38px;
            color: #8ff5ff;
        }}

        .hero-title {{
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.9rem;
            font-weight: 700;
            color: #fff;
            margin-bottom: 4px;
            letter-spacing: -0.5px;
        }}
        .hero-sub {{
            font-size: 0.8rem;
            color: rgba(143,245,255,0.6);
            text-transform: uppercase;
            letter-spacing: 1.5px;
            font-weight: 600;
            margin-bottom: 28px;
        }}

        /* Progress bar */
        .progress-track {{
            background: rgba(255,255,255,0.06);
            border-radius: 10px;
            height: 22px;
            position: relative;
            overflow: hidden;
            border: 1px solid rgba(143,245,255,0.1);
        }}
        .progress-fill {{
            height: 100%;
            border-radius: 10px;
            background: linear-gradient(90deg, #8ff5ff, #cafd00);
            box-shadow: 0 0 20px rgba(143,245,255,0.35), inset 0 1px 0 rgba(255,255,255,0.2);
            transition: width 1s cubic-bezier(.4,0,.2,1);
            position: relative;
        }}
        .progress-fill::after {{
            content: '';
            position: absolute;
            inset: 0;
            background: linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.15) 50%, transparent 100%);
            animation: shimmer 2.5s infinite;
        }}
        @keyframes shimmer {{
            0% {{ transform: translateX(-100%); }}
            100% {{ transform: translateX(100%); }}
        }}

        .progress-label {{
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            margin-top: 10px;
        }}
        .progress-pct {{
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.3rem;
            font-weight: 700;
            color: #8ff5ff;
        }}
        .progress-amt {{
            font-size: 0.82rem;
            color: rgba(255,255,255,0.45);
        }}

        /* ---- Stats grid ---- */
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 14px;
        }}
        .stat-card {{
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(143,245,255,0.08);
            border-radius: 14px;
            padding: 20px 14px;
            text-align: center;
            backdrop-filter: blur(10px);
        }}
        .stat-card .stat-icon {{
            display: flex;
            align-items: center;
            justify-content: center;
            width: 36px;
            height: 36px;
            margin: 0 auto 10px;
            border-radius: 10px;
        }}
        .stat-card .stat-icon .material-symbols-outlined {{ font-size: 20px; }}

        .stat-card .stat-label {{
            font-size: 0.65rem;
            font-weight: 700;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            color: rgba(255,255,255,0.4);
            margin-bottom: 6px;
        }}
        .stat-card .stat-value {{
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.35rem;
            font-weight: 700;
        }}
        .stat-card .stat-unit {{
            font-size: 0.7rem;
            color: rgba(255,255,255,0.35);
            margin-top: 2px;
        }}

        .stat-cyan .stat-icon {{ background: rgba(143,245,255,0.1); }}
        .stat-cyan .stat-icon .material-symbols-outlined {{ color: #8ff5ff; }}
        .stat-cyan .stat-value {{ color: #8ff5ff; }}

        .stat-lime .stat-icon {{ background: rgba(202,253,0,0.1); }}
        .stat-lime .stat-icon .material-symbols-outlined {{ color: #cafd00; }}
        .stat-lime .stat-value {{ color: #cafd00; }}

        .stat-pink .stat-icon {{ background: rgba(255,107,155,0.1); }}
        .stat-pink .stat-icon .material-symbols-outlined {{ color: #ff6b9b; }}
        .stat-pink .stat-value {{ color: #ff6b9b; }}

        /* ---- System alert ---- */
        .alert-card {{
            border: 1px solid rgba(255,107,155,0.25);
            background: rgba(255,107,155,0.06);
            position: relative;
            overflow: hidden;
        }}
        .alert-card::before {{
            content: '';
            position: absolute;
            left: 0;
            top: 0;
            bottom: 0;
            width: 3px;
            background: #ff6b9b;
            border-radius: 3px 0 0 3px;
        }}
        .alert-header {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 10px;
        }}
        .alert-header .material-symbols-outlined {{
            font-size: 20px;
            color: #ff6b9b;
        }}
        .alert-title {{
            font-family: 'Space Grotesk', sans-serif;
            font-weight: 700;
            font-size: 0.82rem;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #ff6b9b;
        }}
        .alert-body {{
            font-size: 0.88rem;
            color: rgba(255,255,255,0.7);
            line-height: 1.6;
        }}
        .alert-body strong {{
            color: #ff6b9b;
            font-weight: 700;
        }}

        /* ---- Edit form ---- */
        .form-panel {{
            position: relative;
        }}
        .form-panel-header {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 22px;
        }}
        .form-panel-header .material-symbols-outlined {{
            font-size: 22px;
            color: #cafd00;
        }}
        .form-panel-title {{
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1rem;
            font-weight: 700;
            color: #fff;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}

        .fg {{
            margin-bottom: 18px;
        }}
        .fg label {{
            display: block;
            font-size: 0.72rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1.2px;
            color: rgba(255,255,255,0.45);
            margin-bottom: 7px;
        }}
        .fg input {{
            width: 100%;
            padding: 12px 14px;
            background: rgba(0,0,0,0.5);
            border: 1px solid rgba(143,245,255,0.12);
            border-radius: 10px;
            color: #fff;
            font-family: 'Manrope', sans-serif;
            font-size: 0.95rem;
            font-weight: 500;
            transition: border-color .2s, box-shadow .2s;
        }}
        .fg input:focus {{
            outline: none;
            border-color: #8ff5ff;
            box-shadow: 0 0 0 3px rgba(143,245,255,0.1);
        }}

        .btn-save {{
            width: 100%;
            padding: 14px;
            border: none;
            border-radius: 12px;
            background: linear-gradient(135deg, #cafd00, #8ff5ff);
            color: #0e0e10;
            font-family: 'Space Grotesk', sans-serif;
            font-size: 0.85rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            cursor: pointer;
            transition: transform .15s, box-shadow .2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }}
        .btn-save:hover {{
            transform: translateY(-2px);
            box-shadow: 0 6px 24px rgba(202,253,0,0.25);
        }}
        .btn-save .material-symbols-outlined {{ font-size: 20px; }}

        /* ---- Quick actions ---- */
        .quick-actions {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 14px;
        }}
        .qa-col form {{
            display: flex;
            flex-direction: column;
            gap: 10px;
            height: 100%;
        }}
        .qa-col .fg {{ margin-bottom: 0; flex: 1; }}

        .btn-add, .btn-sub {{
            width: 100%;
            padding: 12px;
            border: none;
            border-radius: 10px;
            font-family: 'Space Grotesk', sans-serif;
            font-size: 0.78rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1px;
            cursor: pointer;
            transition: transform .15s, box-shadow .2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
        }}
        .btn-add {{
            background: rgba(143,245,255,0.12);
            border: 1px solid rgba(143,245,255,0.3);
            color: #8ff5ff;
        }}
        .btn-add:hover {{
            transform: translateY(-1px);
            box-shadow: 0 4px 16px rgba(143,245,255,0.15);
        }}
        .btn-sub {{
            background: rgba(255,107,155,0.1);
            border: 1px solid rgba(255,107,155,0.25);
            color: #ff6b9b;
        }}
        .btn-sub:hover {{
            transform: translateY(-1px);
            box-shadow: 0 4px 16px rgba(255,107,155,0.15);
        }}
        .btn-add .material-symbols-outlined,
        .btn-sub .material-symbols-outlined {{ font-size: 18px; }}

        /* ---- Footer ---- */
        .footer-meta {{
            text-align: center;
            font-size: 0.72rem;
            color: rgba(255,255,255,0.2);
            margin-top: 30px;
            letter-spacing: 0.5px;
        }}

        /* ---- Success toast ---- */
        .toast {{
            position: fixed;
            top: 20px;
            right: 20px;
            background: rgba(143,245,255,0.12);
            border: 1px solid rgba(143,245,255,0.3);
            backdrop-filter: blur(16px);
            border-radius: 12px;
            padding: 14px 20px;
            display: flex;
            align-items: center;
            gap: 10px;
            color: #8ff5ff;
            font-size: 0.85rem;
            font-weight: 600;
            animation: toastIn 0.4s ease, toastOut 0.4s ease 2.6s forwards;
            z-index: 100;
        }}
        .toast .material-symbols-outlined {{ font-size: 20px; }}
        @keyframes toastIn {{
            from {{ transform: translateX(120%); opacity: 0; }}
            to {{ transform: translateX(0); opacity: 1; }}
        }}
        @keyframes toastOut {{
            from {{ transform: translateX(0); opacity: 1; }}
            to {{ transform: translateX(120%); opacity: 0; }}
        }}

        /* ---- Responsive ---- */
        @media (max-width: 560px) {{
            .stats-grid {{ grid-template-columns: 1fr; gap: 10px; }}
            .quick-actions {{ grid-template-columns: 1fr; }}
            .hero-title {{ font-size: 1.5rem; }}
            .stat-card .stat-value {{ font-size: 1.15rem; }}
        }}
    </style>
</head>
<body>
    <div class="page-wrap">

        <!-- Back link -->
        <a href="/dashboard" class="back-link">
            <span class="material-symbols-outlined">arrow_back</span> Powrot
        </a>

        <!-- Hero card -->
        <div class="glass hero-card">
            <div class="hero-icon">
                <span class="material-symbols-outlined">directions_car</span>
            </div>
            <h1 class="hero-title">{goal['name']}</h1>
            <p class="hero-sub">Cel finansowy</p>

            <div class="progress-track">
                <div class="progress-fill" style="width: {progress_val}%"></div>
            </div>
            <div class="progress-label">
                <span class="progress-pct">{progress_val}%</span>
                <span class="progress-amt">{goal['current']:,.0f} / {goal['target']:,.0f} PLN</span>
            </div>
        </div>

        <!-- Stats grid -->
        <div class="stats-grid">
            <div class="stat-card stat-cyan">
                <div class="stat-icon"><span class="material-symbols-outlined">savings</span></div>
                <div class="stat-label">Uzbierane</div>
                <div class="stat-value">{goal['current']:,.0f}</div>
                <div class="stat-unit">PLN</div>
            </div>
            <div class="stat-card stat-lime">
                <div class="stat-icon"><span class="material-symbols-outlined">flag</span></div>
                <div class="stat-label">Cel</div>
                <div class="stat-value">{goal['target']:,.0f}</div>
                <div class="stat-unit">PLN</div>
            </div>
            <div class="stat-card stat-pink">
                <div class="stat-icon"><span class="material-symbols-outlined">trending_down</span></div>
                <div class="stat-label">Pozostalo</div>
                <div class="stat-value">{goal['remaining']:,.0f}</div>
                <div class="stat-unit">PLN</div>
            </div>
        </div>

        <!-- System alert -->
        <div class="glass alert-card" style="margin-top:20px;">
            <div class="alert-header">
                <span class="material-symbols-outlined">warning</span>
                <span class="alert-title">System Alert</span>
            </div>
            <div class="alert-body">
                Aby osiagnac cel w ciagu <strong>12 miesiecy</strong>, musisz odkladac
                <strong>{monthly_needed:,.0f} PLN</strong> miesiecznie.
                Pozostalo <strong>{goal['remaining']:,.0f} PLN</strong> do uzbierania.
            </div>
        </div>

        <!-- Edit form -->
        <div class="glass form-panel" style="margin-top:20px;">
            <div class="form-panel-header">
                <span class="material-symbols-outlined">edit_note</span>
                <span class="form-panel-title">Edytuj cel</span>
            </div>
            <form action="/goal/update" method="POST">
                <input type="hidden" name="csrf_token" value="{generate_csrf()}">
                <div class="fg">
                    <label>Nazwa celu</label>
                    <input type="text" name="name" value="{goal['name']}" required>
                </div>
                <div class="fg">
                    <label>Uzbierana kwota (PLN)</label>
                    <input type="number" name="current" value="{goal['current']:.0f}" step="0.01" required>
                </div>
                <div class="fg">
                    <label>Cel (PLN)</label>
                    <input type="number" name="target" value="{goal['target']:.0f}" step="0.01" required>
                </div>
                <button type="submit" class="btn-save">
                    <span class="material-symbols-outlined">save</span> Zapisz zmiany
                </button>
            </form>
        </div>

        <!-- Quick actions -->
        <div class="glass" style="margin-top:20px;">
            <div class="form-panel-header">
                <span class="material-symbols-outlined">bolt</span>
                <span class="form-panel-title">Szybkie akcje</span>
            </div>
            <div class="quick-actions">
                <div class="qa-col">
                    <form action="/goal/add" method="POST">
                        <input type="hidden" name="csrf_token" value="{generate_csrf()}">
                        <div class="fg">
                            <label>Dodaj kwote (PLN)</label>
                            <input type="number" name="amount" placeholder="np. 5000" step="0.01" required>
                        </div>
                        <button type="submit" class="btn-add">
                            <span class="material-symbols-outlined">add_circle</span> Dodaj
                        </button>
                    </form>
                </div>
                <div class="qa-col">
                    <form action="/goal/subtract" method="POST">
                        <input type="hidden" name="csrf_token" value="{generate_csrf()}">
                        <div class="fg">
                            <label>Odejmij kwote (PLN)</label>
                            <input type="number" name="amount" placeholder="np. 1000" step="0.01" required>
                        </div>
                        <button type="submit" class="btn-sub">
                            <span class="material-symbols-outlined">remove_circle</span> Odejmij
                        </button>
                    </form>
                </div>
            </div>
        </div>

        <!-- Footer -->
        <p class="footer-meta">
            Ostatnia aktualizacja: {goal['updated_at'][:10]}
        </p>

    </div>

    <script nonce="{getattr(request, '_csp_nonce', '')}">
        // Show success toast from URL param
        const params = new URLSearchParams(window.location.search);
        if (params.get('success')) {{
            const toast = document.createElement('div');
            toast.className = 'toast';
            toast.innerHTML = '<span class="material-symbols-outlined">check_circle</span>Zapisano pomyslnie';
            document.body.appendChild(toast);
            setTimeout(() => toast.remove(), 3200);
        }}
    </script>
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
        
        html += '<a href="/dashboard" class="back" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:15px">&larr; Dashboard</a>'
        html += '</div>'
        
        return html
        
    except Exception as e:
        import traceback
        traceback.print_exc()  # Log to server only
        return '<html><body style="background:#000;color:#fff;padding:20px"><h1>Wystąpił błąd serwera</h1><p>Szczegóły zapisane w logach.</p></body></html>', 500

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

        # v1.0.110 FIX: sync_orders() to network call do Allegro. Wczesniej
        # SYNCHRONICZNIE w request co 60s -> trzymal watek waitress kilka s
        # przy KAZDYM dashboardzie (poll 30s). Teraz w TLE (fire-and-forget) -
        # request zwraca od razu z bazy. Cha-ching pokaze sie przy nastepnym
        # pollu (max 30s pozniej) - akceptowalne. auto_sync_orders_loop tez
        # syncuje w tle co 5 min jako backup.
        now = time.time()
        last_sync = getattr(api_check_sales, '_last_sync', 0)
        synced = 0
        if now - last_sync > 60:
            api_check_sales._last_sync = now
            import threading as _cs_th
            def _bg_sync_orders():
                try:
                    sync_orders(today_only=True)
                except Exception:
                    pass
            _cs_th.Thread(target=_bg_sync_orders, daemon=True).start()

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
        print(f"[ERR] Sales sync: {e}")
        return jsonify({'success': False, 'error': 'Błąd synchronizacji', 'new_sales': []})

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
# IKONY PWA — usuniety dynamiczny route ktory zwracal SVG zamiast PNG
# Flask serwuje teraz prawdziwe static/icon-{192,512}.png z dysku.
# Manifest deklaruje image/png — Chrome wymaga prawdziwego PNG.
# ============================================================

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
    return f'<html><head><title>Sync historyczny</title></head><body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0"><div style="background:#12121a;border-radius:16px;padding:30px;min-width:320px"><h2 style="margin:0 0 15px"><span class=material-symbols-outlined>sync</span> Sync historyczny</h2><p style="color:#64748b;margin-bottom:20px">Pobierz zamowienia od wybranej daty (np. poprzedni miesiac)</p><form method="POST"><input type="hidden" name="csrf_token" value="{generate_csrf()}"><label style="display:block;color:#94a3b8;margin-bottom:6px">Data od:</label><input type="date" name="from_date" value="{miesiac_temu}" style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #334155;border-radius:8px;color:#fff;font-size:1rem;box-sizing:border-box;margin-bottom:15px"><button type="submit" style="width:100%;padding:12px;background:#3b82f6;border:none;border-radius:8px;color:#fff;font-size:1rem;font-weight:600;cursor:pointer"><span class=material-symbols-outlined>sync</span> Synchronizuj</button></form><a href="/magazyn/statystyki" style="display:block;text-align:center;margin-top:15px;color:#64748b;font-size:0.85rem">Anuluj</a></div></body></html>'

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
                    <div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined>warning</span></div>
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
                    <div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined style=color:#ef4444>cancel</span></div>
                    <div style="font-size:1.2rem;color:#ef4444">Błąd: {_html_escape(str(error))}</div>
                </div>
            </body></html>
            '''
        
        return f'''
        <html><head><meta http-equiv="refresh" content="2;url=/statystyki"></head>
        <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
            <div style="text-align:center">
                <div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined style=color:#22c55e>check_circle</span></div>
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
                <div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined style=color:#ef4444>cancel</span></div>
                <div style="font-size:1.2rem;color:#ef4444">Błąd: {_html_escape(str(e))}</div>
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
                <div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined>warning</span></div>
                <div style="font-size:1.2rem;color:#f59e0b">Podaj datę: /sync-custom?from=2026-02-01</div>
            </div>
        </body></html>
        '''
    try:
        from modules.allegro_api import sync_orders, is_authenticated
        if not is_authenticated():
            return '<html><head><meta http-equiv="refresh" content="3;url=/statystyki"></head><body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0"><div style="text-align:center"><div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined>warning</span></div><div style="color:#f59e0b">Token Allegro wygasł!</div></div></body></html>'
        synced, error = sync_orders(from_date_str=from_date)
        if error:
            return f'<html><head><meta http-equiv="refresh" content="3;url=/sprzedaze"></head><body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0"><div style="text-align:center"><div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined style=color:#ef4444>cancel</span></div><div style="color:#ef4444">Błąd: {_html_escape(str(error))}</div></div></body></html>'
        return f'''
        <html><head><meta http-equiv="refresh" content="2;url=/sprzedaze?miesiac={from_date[:7]}">
        <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@700&family=Material+Symbols+Outlined:wght,FILL@100..700,0..1" rel="stylesheet">
        </head>
        <body style="background:#0a0a0f;color:#fff;font-family:'Space Grotesk',system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
            <div style="text-align:center">
                <div style="font-size:4rem;margin-bottom:20px;color:#22c55e;text-shadow:0 0 30px rgba(34,197,94,0.4)"><span class=material-symbols-outlined style="font-size:inherit">check_circle</span></div>
                <div style="font-size:1.3rem;font-weight:700">Zsynchronizowano <span style="color:#beee00">{synced}</span> zamówień od {from_date}</div>
                <div style="color:#64748b;margin-top:12px;font-size:0.85rem">Przekierowuję...</div>
            </div>
        </body></html>
        '''
    except Exception as e:
        return f'<html><head><meta http-equiv="refresh" content="3;url=/statystyki"></head><body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0"><div style="text-align:center"><div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined style=color:#ef4444>cancel</span></div><div style="color:#ef4444">Błąd: {_html_escape(str(e))}</div></div></body></html>'

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
    try:
        _bn = get_config_cached('brand_name', 'AKCES HUB')
    except Exception:
        _bn = 'AKCES HUB'
    print("\n" + "="*60)
    print(f"  [BOLT] {_bn} v{VERSION}")
    print("  Paletomat + Magazynier + Telegram + Allegro")
    print("="*60)
    print(f"  [INVENT] Magazynier:  /magazyn")
    print(f"  [SMART_] Paletomat:   /paletomat")
    print(f"  [TG] Telegram:    /telegram")
    print(f"  [SHOPPI] Allegro:     /allegro")
    print(f"  [BOLT] Narzędzia:   /narzedzia")
    print("="*60)
# ═══════════════════════════════════════════════════════════════════════════
# AKCES HUB v3.0.21 - NOWY KOD DO WKLEJENIA
# ═══════════════════════════════════════════════════════════════════════════
# 
# INSTRUKCJA:
# 1. Otwórz app.py
# 2. Znajdź linię: 

# ═══════════════════════════════════════════════════════════════════════════
# ROUTE: POZIOM — gamifikacja / progress tracker
# ═══════════════════════════════════════════════════════════════════════════
@app.route('/poziom/config', methods=['POST'])
def poziom_config():
    """Zapisz cele/persona usera dla /poziom — kazdy klient ma swoje."""
    from modules.database import set_config
    if session.get('rola') not in ('admin', 'manager'):
        return 'Brak dostepu', 403
    try:
        goal = int(request.form.get('goal_yearly_pln', 1000000) or 1000000)
        if goal < 10000 or goal > 100000000:
            goal = 1000000
        set_config('goal_yearly_pln', str(goal))
        title = (request.form.get('persona_title', '') or '').strip()[:60]
        if title:
            set_config('persona_title', title)
        tags = (request.form.get('persona_tags', '') or '').strip()[:200]
        if tags:
            set_config('persona_tags', tags)
    except (ValueError, TypeError):
        pass
    return redirect('/poziom')


@app.route('/poziom')
def poziom_page():
    from modules.database import get_db, get_config
    conn = get_db()
    year = datetime.now().year

    # Per-instance personalizacja (NIE hardcoded Adrian's goals):
    try:
        cel = int(get_config('goal_yearly_pln', '1000000') or '1000000')
        if cel < 10000:
            cel = 1000000
    except (ValueError, TypeError):
        cel = 1000000
    persona_title = (get_config('persona_title', '') or '').strip() or 'PALET BOSS'
    persona_tags = (get_config('persona_tags', '') or '').strip() or 'zwroty konsumenckie · allegro · family team · 2h/dzien'
    needs_wizard = (get_config('goal_yearly_pln', '') == '')  # show wizard for fresh installs

    # Przychód roczny — identycznie jak get_full_stats() w database.py
    # Allegro + normalne sprzedaże (bez zwrotów, bez offline, bez MANUAL)
    year_start = f'{year}-01-01'
    month_start = datetime.now().strftime('%Y-%m-01')

    # Przychód z wszystkich źródeł (Allegro + offline, bez zwrotów/anulowanych)
    row = conn.execute('''
        SELECT
            COALESCE(SUM(CASE WHEN date(data_sprzedazy) >= ? AND COALESCE(status,'') NOT IN ('zwrot','anulowane','anulowana') THEN cena * ilosc ELSE 0 END), 0) as rok,
            COALESCE(SUM(CASE WHEN date(data_sprzedazy) >= ? AND COALESCE(status,'') NOT IN ('zwrot','anulowane','anulowana') THEN cena * ilosc ELSE 0 END), 0) as msc
        FROM sprzedaze
    ''', (year_start, month_start)).fetchone()
    przychod_rok = float(row['rok'] or 0)
    przychod_msc = float(row['msc'] or 0)

    # sprzedaze_prywatne NIE dodajemy — offline jest już w sprzedaze jako status='sprzedana'

    # Palety w tym roku
    row3 = conn.execute('''
        SELECT COUNT(*) as cnt FROM palety
        WHERE strftime('%Y', data_zakupu) = ?
    ''', (str(year),)).fetchone()
    palety_rok = int(row3['cnt'] or 0)

    # cel pobrany z config wyżej (per-instance, NIE hardcoded 1M)
    xp_pct = min(99, round(przychod_rok / cel * 100, 1))
    brakuje = max(0, cel - przychod_rok)
    palety_msc = max(1, round(palety_rok / max(1, datetime.now().month)))
    avg_paleta = round(przychod_rok / max(1, palety_rok))
    sredni_msc = round(przychod_rok / max(1, datetime.now().month))
    miesiecy_zostalo = 12 - datetime.now().month

    if sredni_msc > 0:
        miesiecy_do_celu = round(brakuje / sredni_msc)
    else:
        miesiecy_do_celu = 99

    monthly_goal = round(cel / 12)
    boss_pct = min(100, round(przychod_msc / max(1, monthly_goal) * 100, 1))
    palety_potrzeba = max(1, round(monthly_goal / max(1, avg_paleta)))

    # Prognoza na koniec miesiąca
    import calendar
    dzien_msc = datetime.now().day
    dni_w_msc = calendar.monthrange(year, datetime.now().month)[1]
    if dzien_msc > 0:
        srednia_dzienna_msc = przychod_msc / dzien_msc
        prognoza_msc = round(srednia_dzienna_msc * dni_w_msc)
    else:
        srednia_dzienna_msc = 0
        prognoza_msc = 0

    # Prognoza roczna na bazie dotychczasowego tempa
    dzien_roku = datetime.now().timetuple().tm_yday
    if dzien_roku > 0:
        prognoza_rok = round(przychod_rok / dzien_roku * 365)
    else:
        prognoza_rok = 0

    real_data = {
        'przychod_rok': przychod_rok,
        'przychod_rok_fmt': f"{przychod_rok:,.0f}".replace(',', ' '),
        'cel_fmt': f"{cel:,.0f}".replace(',', ' '),
        'xp_pct': xp_pct,
        'brakuje_fmt': f"{brakuje:,.0f}".replace(',', ' '),
        'miesiecy_do_celu': miesiecy_do_celu,
        'year': year,
        'przychod_msc': przychod_msc,
        'przychod_msc_fmt': f"{przychod_msc:,.0f}".replace(',', ' '),
        'sredni_msc': sredni_msc,
        'sredni_msc_fmt': f"{sredni_msc:,.0f}".replace(',', ' '),
        'palety_msc': palety_msc,
        'palety_potrzeba_msc': palety_potrzeba,
        'avg_paleta': avg_paleta,
        'avg_paleta_fmt': f"{avg_paleta:,.0f}".replace(',', ' '),
        'boss_pct': boss_pct,
        'prognoza_msc': prognoza_msc,
        'prognoza_msc_fmt': f"{prognoza_msc:,.0f}".replace(',', ' '),
        'prognoza_rok': prognoza_rok,
        'prognoza_rok_fmt': f"{prognoza_rok:,.0f}".replace(',', ' '),
        'srednia_dzienna_msc': round(srednia_dzienna_msc),
        'srednia_dzienna_msc_fmt': f"{srednia_dzienna_msc:,.0f}".replace(',', ' '),
        'dzien_msc': dzien_msc,
        'dni_w_msc': dni_w_msc,
    }

    # Wstrzyknij dane do template
    import json as _json
    _nonce = getattr(request, '_csp_nonce', '')
    data_script = f'<script nonce="{_nonce}">window.REAL_DATA = {_json.dumps(real_data)};</script>'

    username = session.get('username', 'User')
    html = render_template('poziom.html',
        username=username,
        persona_title=persona_title,
        persona_tags=persona_tags,
        cel_yearly=cel,
        cel_yearly_fmt=f"{cel:,.0f}".replace(',', ' '),
        monthly_goal_fmt=f"{round(cel / 12):,.0f}".replace(',', ' '),
        needs_wizard=needs_wizard,
    )
    # Wstaw dane przed </head>
    html = html.replace('</head>', data_script + '\n</head>')
    return html

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
        <a href="/dashboard" style="display:inline-block;margin-bottom:15px;color:#64748b;text-decoration:none;font-size:0.9rem">&#8592; Powrót do domu</a>
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
    <script nonce="{getattr(request, '_csp_nonce', '')}">
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
        key = request.headers.get('X-API-Key')
        if key != AKCES_API_KEY:
            return jsonify({'error': 'Unauthorized', 'hint': 'Dodaj naglowek X-API-Key'}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/api/key', methods=['GET'])
@(limiter.limit("5 per minute") if limiter else lambda f: f)
def api_show_key():
    """Pokazuje klucz API (tylko z localhost)"""
    if request.remote_addr not in ('127.0.0.1', '::1', 'localhost'):
        return jsonify({'error': 'Tylko z localhost'}), 403
    global AKCES_API_KEY
    if AKCES_API_KEY is None:
        AKCES_API_KEY = get_api_key()
    return jsonify({'api_key': AKCES_API_KEY, 'hint': 'Uzyj TYLKO naglowka X-API-Key (NIE w URL)'})

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
         
    """, (rok,)).fetchone()['suma'])
    
    cnt_2026 = int(conn.execute("""
        SELECT COUNT(*) as cnt FROM sprzedaze
        WHERE strftime('%Y', REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) = ?
          AND status NOT IN ('zwrot','anulowane','anulowana')
         
    """, (rok,)).fetchone()['cnt'])
    
    best_day = conn.execute("""
        SELECT MAX(dzien_suma) as max_suma, MAX(dzien_cnt) as max_cnt FROM (
            SELECT date(REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) as dzien,
                   SUM(cena * ilosc) as dzien_suma, COUNT(*) as dzien_cnt
            FROM sprzedaze
            WHERE strftime('%Y', REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) = ?
              AND status NOT IN ('zwrot','anulowane','anulowana')
             
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
        {'id': 1,  'icon': '<span class=material-symbols-outlined>payments</span>', 'name': '100k PLN',   'desc': 'Przychód roczny',     'achieved': przychod_2026 >= 100000},
        {'id': 2,  'icon': 'fire', 'name': 'Dzień 3k',   'desc': '3 000 zł w 1 dzień',  'achieved': best_day_kwota >= 3000},
        {'id': 3,  'icon': '<span class=material-symbols-outlined>inventory_2</span>', 'name': '15 palet',   'desc': 'Palety w 1 miesiącu',  'achieved': max_palet_miesiac >= 15},
        {'id': 4,  'icon': '<span class=material-symbols-outlined>rocket_launch</span>', 'name': '200k PLN',   'desc': 'Przychód roczny',      'achieved': przychod_2026 >= 200000},
        {'id': 5,  'icon': '<span class=material-symbols-outlined>bolt</span>', 'name': '10 zamówień','desc': '10 zam. w 1 dzień',    'achieved': best_day_cnt >= 10},
        {'id': 6,  'icon': '⏱', 'name': 'Sprzed. 24h','desc': 'Sprzedane w 24h',      'achieved': fast_sale_24h > 0},
        {'id': 7,  'icon': '<span class=material-symbols-outlined>trending_up</span>', 'name': '40k/mies.',  'desc': '40k zł w miesiącu',    'achieved': best_month_kwota >= 40000},
        {'id': 8,  'icon': '<span class=material-symbols-outlined>adjust</span>', 'name': '200 szt.',   'desc': '200 sprzedaży w roku', 'achieved': cnt_2026 >= 200},
        {'id': 9,  'icon': 'boom', 'name': 'Dzień 5k',   'desc': '5 000 zł w 1 dzień',  'achieved': best_day_kwota >= 5000},
        {'id': 10, 'icon': '<span class=material-symbols-outlined>inventory_2</span>', 'name': '20 palet',   'desc': 'Palety w 1 miesiącu',  'achieved': max_palet_miesiac >= 20},
        {'id': 11, 'icon': 'diamond', 'name': '300k PLN',   'desc': 'Przychód roczny',      'achieved': przychod_2026 >= 300000},
        {'id': 12, 'icon': '<span class=material-symbols-outlined>bolt</span>', 'name': 'Sprzed. 6h', 'desc': 'Sprzedane w 6h',       'achieved': fast_sale_6h > 0},
        {'id': 13, 'icon': '🆓', 'name': 'FREE',       'desc': 'Masz to!',              'achieved': True},
        {'id': 14, 'icon': '<span class=material-symbols-outlined>emoji_events</span>', 'name': 'Dzień 20',   'desc': '20 zamówień w dzień',  'achieved': best_day_cnt >= 20},
        {'id': 15, 'icon': '<span class=material-symbols-outlined>bar_chart</span>', 'name': '60k/mies.',  'desc': '60k zł w miesiącu',    'achieved': best_month_kwota >= 60000},
        {'id': 16, 'icon': 'dice', 'name': '500 szt.',   'desc': '500 sprzedaży w roku', 'achieved': cnt_2026 >= 500},
        {'id': 17, 'icon': '<span class=material-symbols-outlined>payments</span>', 'name': '25 palet',   'desc': 'Palety w 1 miesiącu',  'achieved': max_palet_miesiac >= 25},
        {'id': 18, 'icon': 'star', 'name': '400k PLN',   'desc': 'CEL ROCZNY!',        'achieved': przychod_2026 >= 400000},
        {'id': 19, 'icon': 'fire', 'name': '80k/mies.',  'desc': '80k zł w miesiącu',    'achieved': best_month_kwota >= 80000},
        {'id': 20, 'icon': '<span class=material-symbols-outlined>adjust</span>', 'name': 'Dzień 10k',  'desc': '10k zł w 1 dzień',     'achieved': best_day_kwota >= 10000},
        {'id': 21, 'icon': '<span class=material-symbols-outlined>rocket_launch</span>', 'name': '1000 szt.',  'desc': '1000 sprzedaży w roku','achieved': cnt_2026 >= 1000},
        {'id': 22, 'icon': 'diamond', 'name': '500k PLN',   'desc': 'Przychód roczny',      'achieved': przychod_2026 >= 500000},
        {'id': 23, 'icon': '<span class=material-symbols-outlined>inventory_2</span>', 'name': '30 palet',   'desc': 'Palety w 1 miesiącu',  'achieved': max_palet_miesiac >= 30},
        {'id': 24, 'icon': 'boom', 'name': '100k/mies.', 'desc': '100k zł w miesiącu',   'achieved': best_month_kwota >= 100000},
        {'id': 25, 'icon': 'crown', 'name': 'LEGENDA',    'desc': 'Wszystko ukończone!',   'achieved': all([
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
            print(f"[WARN] Produkt ID {produkt_id} nie znaleziony")
            return False
        
        # Wybierz odpowiednią funkcję drukowania
        if printer == 'niimbot':
            from modules.niimbot_print import print_niimbot
            print_niimbot(produkt)
            print(f"[OK] Auto-print (Niimbot): {produkt['nazwa'][:50]}")
            
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
            print(f"[OK] Auto-print (Vretti): {produkt['nazwa'][:50]}")
            
            # Aktualizuj czas wydruku
            conn.execute(
                "UPDATE produkty SET last_printed_at = datetime('now') WHERE id = ?",
                (produkt_id,)
            )
            conn.commit()
            return True
            
        else:
            print(f"[WARN] Nieznany typ drukarki: {printer}")
            return False
            
    except Exception as e:
        print(f"[ERR] Auto-print error: {e}")
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
            print("[WARN] Tabela produkty nie istnieje - pomijam ensure_offline_columns")
            return
        
        # Sprawdź i dodaj sprzedano_offline
        try:
            conn.execute("SELECT sprzedano_offline FROM produkty LIMIT 1")
        except:
            try:
                conn.execute("ALTER TABLE produkty ADD COLUMN sprzedano_offline INTEGER DEFAULT 0")
                conn.commit()
                print("[OK] Dodano kolumnę sprzedano_offline")
            except:
                pass
        
        # Sprawdź i dodaj przychod_offline
        try:
            conn.execute("SELECT przychod_offline FROM produkty LIMIT 1")
        except:
            try:
                conn.execute("ALTER TABLE produkty ADD COLUMN przychod_offline REAL DEFAULT 0")
                conn.commit()
                print("[OK] Dodano kolumnę przychod_offline")
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
                print(f"[BUILD] Naprawiono przychod_offline dla {fixed} produktów")
        except:
            pass
            
        # Sprawdź i dodaj kolumnę notified w sprzedaze
        try:
            conn.execute("SELECT notified FROM sprzedaze LIMIT 1")
        except:
            try:
                conn.execute("ALTER TABLE sprzedaze ADD COLUMN notified INTEGER DEFAULT 0")
                conn.commit()
                print("[OK] Dodano kolumnę notified do sprzedaze")
            except Exception as e:
                print(f"[WARN] Błąd migracji notified: {e}")

    except Exception as e:
        print(f"[WARN] Błąd ensure_offline_columns: {e}")

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
    <script nonce="{getattr(request, '_csp_nonce', '')}">
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
            print("[WARN]  UWAGA: Baza danych jest uszkodzona!")
            print("=" * 70)
            db_corrupted = True
    
    if db_corrupted:
        print()
        print("[BUILD] Próbuję naprawić bazę danych...")
        print()

        import shutil
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f'akces_hub_CORRUPTED_{timestamp}.db'

        try:
            shutil.copy('akces_hub.db', backup_name)
            print(f"[OK] Backup uszkodzonej bazy: {backup_name}")
        except:
            print("[WARN]  Nie udało się zrobić backupu")

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
                print(f"[OK] Baza naprawiona! Uratowano {cnt} produktów.")
                naprawiono = True
            else:
                print("[WARN]  Naprawa nie uratowała produktów.")
        except Exception as _e:
            print(f"[WARN]  Naprawa nie powiodła się: {_e}")

        if not naprawiono:
            print()
            print("=" * 70)
            print("[ERR] NIE UDAŁO SIĘ NAPRAWIĆ BAZY!")
            print("=" * 70)
            print("Uruchom ręcznie: python napraw_baze2.py")
            print("Backup uszkodzonej bazy zachowany jako:", backup_name)
            print()
            input("Naciśnij Enter aby zamknąć...")
            exit(1)
    
    # Force add offline columns
    ensure_offline_columns()
    
    # KOMBAJN MODE: Cleanup połączeń przy zamknięciu
    import atexit
    import signal
    from modules.database import close_connection_pool
    
    def cleanup_handler():
        """Zamyka wszystkie połączenia z bazą przy zamknięciu"""
        print("\n\n[CLEANUP] Cleaning up database connections...")
        # WAL checkpoint - zapisz wszystkie zmiany do głównego pliku DB
        try:
            import sqlite3
            from modules.database import DATABASE
            tmp = sqlite3.connect(DATABASE, timeout=10)
            tmp.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            tmp.close()
            print("[OK] WAL checkpoint done")
        except Exception as e:
            print(f"[WARN] WAL checkpoint error: {e}")
        close_connection_pool()
        print("[OK] Cleanup done\n")
    
    # Zarejestruj cleanup
    atexit.register(cleanup_handler)
    
    # Obsługa Ctrl+C
    def signal_handler(sig, frame):
        print("\n\n[WARNIN]  Otrzymano sygnał zamknięcia...")
        cleanup_handler()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Sprawdź status bibliotek drukarki
    print("\n[INVENT] Status bibliotek drukarki:")
    libs_status = []
    try:
        import bleak
        libs_status.append('  <span class=material-symbols-outlined style=color:#22c55e>check_circle</span> bleak (Bluetooth)')
    except ImportError:
        libs_status.append('  <span class=material-symbols-outlined style=color:#ef4444>cancel</span> bleak (Bluetooth) - pip install bleak')
    
    # Szczegółowe sprawdzenie niimprint
    niimprint_ok = False
    try:
        from niimprint import BluetoothTransport, PrinterClient
        libs_status.append('  <span class=material-symbols-outlined style=color:#22c55e>check_circle</span> niimprint (Niimbot)')
        niimprint_ok = True
    except ImportError as e:
        libs_status.append(f'  <span class=material-symbols-outlined style=color:#ef4444>cancel</span> niimprint (Niimbot) - brak modułu')
        libs_status.append(f"     → pip install niimprint --break-system-packages")
        libs_status.append(f"     → lub: pip install git+https://github.com/AndBondStyle/niimprint.git")
    except Exception as e:
        libs_status.append(f'  <span class=material-symbols-outlined style=color:#ef4444>cancel</span> niimprint - błąd: {e}')
    
    try:
        import qrcode
        from PIL import Image
        libs_status.append('  <span class=material-symbols-outlined style=color:#22c55e>check_circle</span> pillow/qrcode (obrazy)')
    except ImportError:
        libs_status.append('  <span class=material-symbols-outlined style=color:#ef4444>cancel</span> pillow/qrcode (obrazy) - pip install pillow qrcode')
    
    try:
        import barcode
        libs_status.append('  <span class=material-symbols-outlined style=color:#22c55e>check_circle</span> python-barcode (kody kreskowe)')
    except ImportError:
        libs_status.append('  <span class=material-symbols-outlined style=color:#eab308>warning</span> python-barcode (opcjonalne)')
    
    for s in libs_status:
        print(s)
    
    if not niimprint_ok:
        print("\n  [LIGHTB] Drukarka Niimbot będzie działać przez BLE (bleak),")
        print("     ale niimprint daje lepszą stabilność.")
    
    # Inicjalizacja bazy
    init_db()
    log("Baza danych OK")
    print_banner()

    # Jednorazowe migracje + naprawa integralnosci - SYNCHRONICZNIE (sa szybkie,
    # bez REINDEX). REINDEX wyniesiony do maintenance_reindex_if_needed() ktore
    # leci w tle i tylko raz na 7 dni - inaczej blokowal DB i auto-sync Allegro
    # walil "database is locked".
    from modules.database import (
        migrate_reset_fake_data_wystawienia,
        fix_product_status_integrity,
        maintenance_reindex_if_needed,
    )
    migrate_reset_fake_data_wystawienia()
    log("Sprawdzam integralnosc danych...")
    fix_product_status_integrity()

    # REINDEX w tle, z opoznieniem 60s zeby auto-sync zdazyl wystartowac.
    def _reindex_later():
        time.sleep(60)
        maintenance_reindex_if_needed()
    threading.Thread(target=_reindex_later, daemon=True).start()

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

    # FIX 2026-05-28: Auto-uzupełnij zdjęcia Amazon przy starcie
    # (jednorazowy bulk UPDATE w tle, 10s opóźnienia żeby DB była gotowa).
    # Robi to dla każdego produktu z ASIN >= 8 znaków i pustym zdjecie_url.
    # Browser załaduje URL dynamicznie - bez fetchowania plików.
    def _auto_fill_amazon_images_at_startup():
        import time as _t
        _t.sleep(10)
        try:
            from modules.database import get_db
            conn = get_db()
            r = conn.execute("""
                UPDATE produkty
                SET zdjecie_url = 'https://m.media-amazon.com/images/I/' || UPPER(TRIM(asin)) || '._AC_SL1500_.jpg'
                WHERE (zdjecie_url IS NULL OR zdjecie_url = '')
                  AND asin IS NOT NULL AND TRIM(asin) != ''
                  AND LENGTH(TRIM(asin)) >= 8
                  AND TRIM(UPPER(asin)) NOT IN ('NONE', 'NAN', 'N/A')
            """)
            n = r.rowcount
            conn.commit()
            if n > 0:
                print(f"[AUTO-FILL] Uzupełniono zdjęcia Amazon dla {n} produktów")
        except Exception as _e:
            print(f"[AUTO-FILL] Błąd auto-uzupełnienia zdjęć: {_e}")

    try:
        threading.Thread(target=_auto_fill_amazon_images_at_startup, daemon=True,
                         name='amazon-images-auto-fill').start()
    except Exception as _e:
        log_warning(f"Amazon images auto-fill thread: {_e}")
    
    # Auto-refresh tokena Allegro
    # FIX 2026-05-09: daemon startuje gdy sa credentials (client_id+secret),
    # nie gdy istnieje token. Wczesniej `if token_info:` blokowal start, bo
    # `get_token_info` szukal klucza `allegro_token_expires_at` ktory nigdy
    # nie byl zapisany - allegro_api.py uzywa `allegro_token_expires` bez `_at`.
    # Skutek: daemon NIGDY nie startowal w produkcji, refresh dzialal tylko
    # lazy on-401 z `allegro_api.refresh_access_token`.
    try:
        from modules.token_refresh import start_token_refresh_daemon, get_token_info
        from modules.allegro_api import is_configured as _allegro_is_configured
        if _allegro_is_configured():
            start_token_refresh_daemon()
            token_info = get_token_info()
            if token_info:
                log(f"Token refresh daemon uruchomiony, wygasa: {token_info['expires_at_str']}")
            else:
                log("Token refresh daemon uruchomiony (czeka na /allegro/auth - brak aktualnego tokena)")
        else:
            log_warning("Token refresh - Allegro nie skonfigurowane (brak client_id/secret)")
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
    # PERF: leci w tle — skanowanie folderu zdjec moze trwac sekundy na duzych instalacjach
    def _cleanup_images_async():
        try:
            from modules.allegro_api import cleanup_old_images, get_images_stats
            deleted = cleanup_old_images(days=7)
            stats = get_images_stats()
            if deleted > 0:
                log(f"Wyczyszczono {deleted} starych zdjec")
            log(f"Folder zdjec: {stats['count']} plikow ({stats['size_mb']} MB)")
        except Exception as e:
            log_warning(f"Czyszczenie zdjec: {e}")
    threading.Thread(target=_cleanup_images_async, daemon=True).start()
    
    # Start Telegram bota w tle (raport dzienny + auto-monitoring zamówień)
    try:
        start_bot()
        log("Telegram bot uruchomiony (raport dzienny + auto-monitoring)")
    except Exception as e:
        log_warning(f"Telegram bot error: {e}")

    # Notion Daily Tasks — generuj daily stronę w Notion codziennie o 7:30
    try:
        import schedule as _sched
        def _notion_daily():
            try:
                from modules.notion_tasks import generate_notion_daily
                db = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'akces_hub.db')
                generate_notion_daily(db_path=db)
            except Exception as e:
                log_warning(f"Notion daily note error: {e}")

        # PERF: zaleglą notatkę generuj W TLE — synchronicznie wisi serwer na requeście do Notion
        threading.Thread(target=_notion_daily, daemon=True).start()

        _sched.every().day.at("07:30").do(_notion_daily)

        def _notion_scheduler():
            import time
            while True:
                _sched.run_pending()
                time.sleep(60)

        threading.Thread(target=_notion_scheduler, daemon=True).start()
        log("Notion daily tasks uruchomiony (07:30)")
    except Exception as e:
        log_warning(f"Notion scheduler error: {e}")

    # Start pallet monitor scheduler — tylko w trybie dev
    try:
        from modules.database import get_config as _gc
        if _gc('is_dev', '0') == '1':
            from modules.pallet_monitor import start_scheduler as start_pallet_scheduler
            start_pallet_scheduler()
    except Exception:
        pass

    # Migracja sekretów do szyfrowania (jednorazowo)
    try:
        from modules.database import migrate_secrets
        migrate_secrets()
    except Exception as e:
        log_warning(f"Secret migration error: {e}")

    # Smart Alerts — martwy stock, raport dzienny, sugestie obniżek
    try:
        from modules.smart_alerts import start_smart_alerts
        start_smart_alerts()
        log("Smart Alerts uruchomiony (raport 20:00, alerty pon. 10:00)")
    except Exception as e:
        log_warning(f"Smart Alerts error: {e}")

    # Start Winning Scout scheduler (co 24h)
    try:
        from modules.winning_scout import start_scout_scheduler
        start_scout_scheduler()
        log("Winning Scout scheduler uruchomiony (co 24h)")
    except Exception as e:
        log_warning(f"Winning Scout scheduler error: {e}")

    # Start license heartbeat thread (co 24h)
    try:
        from modules.license import start_heartbeat_thread
        start_heartbeat_thread()
        log("License heartbeat uruchomiony (co 24h)")
    except Exception as e:
        log_warning(f"License heartbeat error: {e}")

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
    
    # Uruchom auto-sync zamówień w osobnym wątku
    sync_thread = threading.Thread(target=auto_sync_orders_loop, daemon=True)
    sync_thread.start()

    # ============================================================
    # AUTO-SYNC OFERT Z ALLEGRO (statusy, nowe oferty z Sales Center)
    # ============================================================
    def auto_sync_offers_loop():
        """Background task - synchronizuje oferty co 15 minut"""
        from modules.allegro_api import sync_offers_status, is_authenticated
        from modules.database import get_config

        log("Auto-sync ofert uruchomiony (co 15 minut)")

        # Pierwsze uruchomienie po 30s (daj czas na start)
        time.sleep(30)

        while True:
            try:
                if get_config('allegro_autosync', 'true') != 'true':
                    time.sleep(900)
                    continue

                if not is_authenticated():
                    time.sleep(900)
                    continue

                result = sync_offers_status()
                new_count = result.get('new', 0)
                updated = result.get('updated', 0)
                if new_count > 0 or updated > 0:
                    log(f"Auto-sync ofert: {new_count} nowych, {updated} zaktualizowanych")

            except Exception as e:
                log_warning(f"Auto-sync ofert blad: {e}")

            time.sleep(900)  # Co 15 minut

    offers_sync_thread = threading.Thread(target=auto_sync_offers_loop, daemon=True)
    offers_sync_thread.start()
    
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
    
    # Auto-backup co 60 minut w tle + RODO auto-anonimizacja raz dziennie
    import threading
    _last_rodo_day = [None]
    _last_license_check_day = [None]

    def hourly_backup():
        import time
        from datetime import date as _date
        while True:
            time.sleep(3600)
            # PHASE 2: USUNIETO duplikat create_backup() — backup robi
            # WYLACZNIE daemon backup_manager (start_backup_daemon, wyzej)
            # ktory ma rotacje (MAX_BACKUPS=24), PRAGMA integrity_check i
            # cloud_export. Wczesniej 2x backup/h (daemon + ta petla) =
            # podwojne I/O, mylace logi, restore "ktory plik". Ta petla
            # zostaje TYLKO dla RODO + license check (raz dziennie).
            # RODO auto-anonimizacja raz dziennie
            try:
                today = _date.today().isoformat()
                if _last_rodo_day[0] != today:
                    from modules.database import auto_anonymize_old_data
                    cnt = auto_anonymize_old_data()
                    _last_rodo_day[0] = today
                    if cnt:
                        log(f"[RODO] Auto-anonimizacja: {cnt} rekordow")
            except Exception as e:
                log_warning(f"[RODO] Blad auto-anonimizacji: {e}")
            # License expiry check raz dziennie
            try:
                today_lic = _date.today().isoformat()
                if _last_license_check_day[0] != today_lic:
                    from modules.license_mailer import check_expiring_licenses
                    check_expiring_licenses()
                    _last_license_check_day[0] = today_lic
                    log("[Mailer] Sprawdzono wygasajace licencje")
            except Exception as e:
                log_warning(f"[Mailer] Blad sprawdzania licencji: {e}")
    threading.Thread(target=hourly_backup, daemon=True).start()
    log("Auto: RODO+license check co godzine (backup = daemon backup_manager)")

    # Reset cache aktualizacji przy starcie — żeby od razu sprawdzał
    try:
        from modules.database import set_config as _sc_start, get_config as _gc_start
        _sc_start('update_check_cache', '')
        # v1.0.98 FIX: tez wyzeruj update_available zeby baner nie wisial
        # od razu po restartcie (jak Adrian po manualnym git pull + restart).
        # Pierwszy F5 na dashboardzie odpali bg check i ustawi prawidlowo.
        _sc_start('update_available', '0')
        # v1.0.101 FIX: wyczysc invalid last_install_commit (np. VERSION string
        # zapisany przez bug v1.0.97). Po fixie _get_version() falluje do git
        # rev-parse ktore zwroci prawidlowy commit hash.
        _li = _gc_start('last_install_commit', '').strip()
        if _li and not _is_commit_hash(_li):
            _sc_start('last_install_commit', '')
            log(f"Last_install_commit (invalid '{_li[:20]}') wyczyszczone")
        log("Update cache + update_available wyczyszczone (restart)")
    except:
        pass

    # v1.0.94 SECURITY (K3+K4): bind warunkowy.
    # - systemd/production (Pi z Cloudflare tunnel) -> 0.0.0.0 (LAN/tunnel dostep)
    # - desktop ZIP install (Windows klient) -> 127.0.0.1 (chroni przed wyscigiem
    #   first_setup z LAN-u, plus nikt z biura nie podsluchnie sesji)
    _bind_host = '0.0.0.0' if _is_proxied_deployment else '127.0.0.1'
    # Port konfigurowalny przez env (AKCES_PORT) — pozwala uruchomic kilka
    # instancji na jednej maszynie (np. dodatkowy klient obok istniejacego
    # Akces Hub na 5000). Domyslnie 5000 — zero zmian dla istniejacych wdrozen.
    try:
        _port = int(os.environ.get('AKCES_PORT', '5000'))
    except (TypeError, ValueError):
        _port = 5000
    log(f"Serwer startuje: http://{_bind_host}:{_port}")
    print("="*60)

    # Produkcyjny WSGI server (waitress) — zamiast Flask dev server (nie padał)
    try:
        from waitress import serve
        log(f"Uzywam waitress (produkcyjny WSGI, bind={_bind_host})")
        serve(app, host=_bind_host, port=_port, threads=16, channel_timeout=600)
    except ImportError:
        # PHASE 2: fallback zostaje (lepiej dzialac na dev niz nie dzialac
        # u klienta), ale GLOSNO — produkcja na Flask dev serverze nie ma
        # wydajnosci ani odpornosci waitress. Operator MUSI to zobaczyc.
        _banner = "!" * 64
        log(_banner)
        log("[CRITICAL] WAITRESS NIEZAINSTALOWANY — dzialam na Flask DEV serverze!")
        log("[CRITICAL] To NIE jest setup produkcyjny. Wykonaj:  pip install waitress")
        log("[CRITICAL] potem zrestartuj usluge. (requirements.txt zawiera waitress)")
        log(_banner)
        app.run(host=_bind_host, port=_port, debug=False, threaded=True)

# ============================================================
# SZTUKI - per-unit tracking
# ============================================================
