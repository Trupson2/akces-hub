"""
System logowania do AKCES HUB
Auth oparty na sesji Flask + hashowane hasla (Argon2id)
Rate limiting na login (ochrona przed brute-force)
"""

import hashlib
import os
import secrets
import sqlite3
import time
from functools import wraps
from pathlib import Path

from flask import Blueprint, request, redirect, url_for, session, render_template_string, render_template, jsonify, abort, flash
from werkzeug.security import generate_password_hash, check_password_hash

# Argon2id — nowoczesny hashing haseł (odporny na GPU/ASIC ataki)
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

_ph = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=1)

auth_bp = Blueprint('auth', __name__)

DB_PATH = str(Path(__file__).parent.parent / 'akces_hub.db')

# Rate limiting — max 5 prob logowania na 15 minut per IP (DB-backed, przeżywa restart)
MAX_LOGIN_ATTEMPTS = 5
LOGIN_COOLDOWN = 900  # 15 minut

# Endpointy NIE wymagajace logowania
PUBLIC_ENDPOINTS = {
    'auth.login',
    'auth.first_setup',
    'project_launcher',
    'static',
}

# Prefiksy URL bez logowania (API health, read-only warehouse heatmap)
PUBLIC_PREFIXES = [
    '/static/',
    '/manifest.json',
    '/sw.js',
    '/api/health',
    '/api/warehouse/heatmap',  # tylko odczyt heatmapy (bez assign/remove)
    '/api/v1/',                # Public REST API v1 — auth via X-API-Key (nie sesja)
    '/license',  # Aktywacja licencji — dostępna bez logowania
    '/setup',    # Setup wizard — dostępny bez logowania
    '/auth/login',        # Logowanie
    '/auth/setup',        # Tworzenie pierwszego konta
    '/auth/logout',       # Wylogowanie
    '/auth/2fa/verify',   # 2FA step po password — pending user bez user_id w session
    '/changelog',         # Historia zmian — dostępna bez logowania
]


def _is_rate_limited(ip):
    """Sprawdza czy IP przekroczyl limit prob logowania (DB-backed)."""
    import sqlite3 as _sq
    now = time.time()
    cutoff = now - LOGIN_COOLDOWN
    try:
        con = _sq.connect(DB_PATH, timeout=3)
        con.execute('CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT, ts REAL)')
        con.execute('DELETE FROM login_attempts WHERE ts < ?', (cutoff,))
        cnt = con.execute('SELECT COUNT(*) FROM login_attempts WHERE ip=?', (ip,)).fetchone()[0]
        con.commit(); con.close()
        return cnt >= MAX_LOGIN_ATTEMPTS
    except Exception:
        return False  # DB error — nie blokuj


def _record_failed_login(ip):
    """Zapisuje nieudana probe logowania do DB."""
    import sqlite3 as _sq
    try:
        con = _sq.connect(DB_PATH, timeout=3)
        con.execute('CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT, ts REAL)')
        con.execute('INSERT INTO login_attempts VALUES (?,?)', (ip, time.time()))
        con.commit(); con.close()
    except Exception:
        pass


def _hash_password(password, salt=None):
    """Hashuje haslo z Argon2id. Parametr salt ignorowany — dla kompatybilnosci."""
    return _ph.hash(password)


def _verify_password(password, stored_hash):
    """Weryfikuje haslo — Argon2id (nowe) + pbkdf2 (migracja). Legacy SHA-256 odrzucone."""
    # Argon2id (nowe hashe)
    if stored_hash.startswith('$argon2'):
        try:
            return _ph.verify(stored_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    # pbkdf2 (stare hashe — migracja; caller powinien upgrade'owac)
    if stored_hash.startswith('pbkdf2:'):
        return check_password_hash(stored_hash, password)
    # Legacy SHA-256 — NIE akceptuj, wymuś reset
    return False


def _needs_rehash(stored_hash):
    """Sprawdza czy hash wymaga upgrade'u do Argon2id."""
    if stored_hash.startswith('$argon2'):
        # Argon2 — sprawdz czy parametry sa aktualne
        return _ph.check_needs_rehash(stored_hash)
    # pbkdf2 lub inne — zawsze upgrade
    return True


def _has_legacy_hash(stored_hash):
    """Check if hash is legacy SHA-256 (requires password reset)."""
    return stored_hash and not stored_hash.startswith('pbkdf2:') and ':' in stored_hash


def _get_auth_db():
    """Polaczenie do bazy dla auth"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    return conn


def init_auth_db():
    """Tworzy tabele users jesli nie istnieje"""
    conn = _get_auth_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        rola TEXT DEFAULT 'user',
        aktywny INTEGER DEFAULT 1,
        utworzony TEXT DEFAULT CURRENT_TIMESTAMP,
        ostatnie_logowanie TEXT
    )''')
    # Migracja 2FA TOTP (Phase 3) — bezpieczne na istniejacych bazach
    totp_migrations = [
        ('totp_secret', 'TEXT'),
        ('totp_enabled', 'INTEGER DEFAULT 0'),
        ('totp_backup_codes', 'TEXT'),
        ('totp_last_verified_at', 'TIMESTAMP'),
    ]
    for col, coltype in totp_migrations:
        try:
            conn.execute(f'ALTER TABLE users ADD COLUMN {col} {coltype}')
        except Exception:
            pass  # kolumna juz istnieje
    conn.commit()
    conn.close()


_users_exist_cache = {'val': None, 'ts': 0}

def _has_any_users():
    """Sprawdza czy sa jacykolwiek uzytkownicy (cache 120s)"""
    import time as _t
    now = _t.time()
    if _users_exist_cache['val'] is not None and (now - _users_exist_cache['ts']) < 120:
        return _users_exist_cache['val']
    conn = _get_auth_db()
    count = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    conn.close()
    _users_exist_cache['val'] = count > 0
    _users_exist_cache['ts'] = now
    return _users_exist_cache['val']


def require_login(f):
    """Dekorator wymagajacy zalogowania"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'error': 'Wymagane logowanie'}), 401
            return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))
        return f(*args, **kwargs)
    return decorated


# Hierarchia ról: admin > manager > user > magazynier
ROLE_HIERARCHY = {'admin': 3, 'manager': 2, 'user': 1, 'magazynier': 0}

# Dozwolone ścieżki per rola (magazynier ma ograniczony dostęp)
ROLE_ALLOWED_PATHS = {
    'magazynier': [
        '/dashboard',           # dashboard (read-only)
        '/wysylki',             # wysyłki
        '/magazyn',             # magazyn (statystyki, produkty, skaner)
        '/warehouse',           # regały, półki, mapa
        '/auth/zmien-haslo',    # zmiana hasła
        '/auth/logout',         # wylogowanie
        '/static/',             # pliki statyczne
        '/api/health',          # healthcheck
        '/api/warehouse',       # API magazynu (heatmapa, skaner)
    ],
    # admin, manager, user — pełny dostęp (brak ograniczeń)
}

def require_role(*roles):
    """Dekorator wymagający jednej z podanych ról (lub wyższej).

    Rowniez wymusza 2FA re-verify gdy user ma totp_enabled=1 ale sesja
    nie zawiera 2fa_verified (np. sesja po migracji lub gdy user wlaczyl
    2FA w trakcie trwajacej sesji z innego klienta).
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get('user_id'):
                return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))
            user_role = session.get('rola', 'user')
            user_level = ROLE_HIERARCHY.get(user_role, 0)
            min_level = min(ROLE_HIERARCHY.get(r, 99) for r in roles)
            if user_level < min_level:
                abort(403)
            # 2FA gate — user z opt-in 2FA musi miec zweryfikowana sesje
            if _user_requires_2fa_reverify():
                if _wants_json() or request.method != 'GET':
                    return jsonify({'ok': False, 'success': False, 'error': 'Wymagana weryfikacja 2FA'}), 401
                session['2fa_pending_user_id'] = session['user_id']
                session['2fa_pending_rola'] = session.get('rola', user_role)
                session['2fa_pending_username'] = session.get('username', '')
                session['2fa_pending_next'] = request.full_path.rstrip('?')
                return redirect(url_for('auth.totp_verify'))
            return f(*args, **kwargs)
        return decorated
    return decorator


def _wants_json():
    """Wykryj czy request oczekuje JSON (AJAX/fetch) czy HTML.
    Uzywa Werkzeug accept_mimetypes.best_match — bardziej niezawodne
    niz recznie parsowanie Accept headera (obsluguje q=... priority).
    """
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    if request.is_json:
        return True
    # Porownanie priorytetu JSON vs HTML w Accept headerze
    best = request.accept_mimetypes.best_match(['application/json', 'text/html'])
    if best == 'application/json':
        # JSON tylko gdy ma wyzszy lub rowny priorytet niz HTML
        return request.accept_mimetypes[best] >= request.accept_mimetypes['text/html']
    return False


def _user_requires_2fa_reverify():
    """Sprawdz czy user ma totp_enabled=1 ale nie zweryfikowal 2FA w tej sesji.

    Zwraca True gdy trzeba wymusic /auth/2fa/verify.
    """
    if not session.get('user_id'):
        return False
    if session.get('2fa_verified'):
        return False
    try:
        conn = _get_auth_db()
        row = conn.execute(
            'SELECT totp_enabled FROM users WHERE id = ?',
            (session['user_id'],)
        ).fetchone()
        conn.close()
        return bool(row and row['totp_enabled'])
    except Exception:
        return False


def require_admin(f):
    """Dekorator wymagający ROLI admin — JSON-aware.

    - Anonim (brak sesji): 401 JSON dla AJAX/fetch, redirect do /auth/login dla GET HTML
    - Zalogowany nie-admin: 403 JSON dla AJAX/fetch, abort(403) dla HTML
    - Admin z totp_enabled ale bez 2fa_verified: redirect do /auth/2fa/verify (HTML) lub 401 JSON
    - Admin (zweryfikowany): przepuszcza

    Używaj na endpointach wykonujących czynności administracyjne po stronie serwera
    (system update, restart, backup restore, zarządzanie userami itp.).
    Serwerowa autoryzacja — NIE polegaj na ukrywaniu przycisków w UI.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            if _wants_json() or request.method != 'GET':
                return jsonify({'ok': False, 'success': False, 'error': 'Wymagane logowanie'}), 401
            return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))
        user_role = (session.get('rola') or '').lower()
        if user_role != 'admin':
            if _wants_json() or request.method != 'GET':
                return jsonify({'ok': False, 'success': False, 'error': 'Brak uprawnień (wymaga admin)'}), 403
            abort(403)
        # 2FA gate — admin z opt-in 2FA musi miec zweryfikowana sesje
        if _user_requires_2fa_reverify():
            if _wants_json() or request.method != 'GET':
                return jsonify({'ok': False, 'success': False, 'error': 'Wymagana weryfikacja 2FA'}), 401
            # Ustaw pending dla plynnego flow
            session['2fa_pending_user_id'] = session['user_id']
            session['2fa_pending_rola'] = session.get('rola', 'admin')
            session['2fa_pending_username'] = session.get('username', '')
            session['2fa_pending_next'] = request.full_path.rstrip('?')
            return redirect(url_for('auth.totp_verify'))
        return f(*args, **kwargs)
    return decorated


# ============================================================
# STRONA LOGOWANIA
# ============================================================

LOGIN_HTML = '''<!DOCTYPE html>
<html class="dark" lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1,user-scalable=no">
<title>{{ brand_name }} - Login</title>
<meta name="theme-color" content="#0e0e10">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="application-name" content="{{ brand_name|default('Akces Hub') }}">
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/static/icon-192.png">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Manrope:wght@400;500;600;700&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0e0e10;--surface:#19191c;--surface-high:#1f1f22;--surface-highest:#262528;--primary:#8ff5ff;--secondary:#ff6b9b;--tertiary:#beee00;--text:#f9f5f8;--text-muted:#adaaad;--outline:#767577}
body{font-family:'Manrope',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;display:flex;align-items:center;justify-content:center;overflow:hidden}
.font-headline{font-family:'Space Grotesk',sans-serif}

/* Cyber grid bg */
.cyber-grid{position:fixed;inset:0;background-image:linear-gradient(to right,rgba(143,245,255,0.05) 1px,transparent 1px),linear-gradient(to bottom,rgba(143,245,255,0.05) 1px,transparent 1px);background-size:40px 40px;z-index:0}

/* Floating keywords */
.floating-text{position:fixed;inset:0;z-index:1;user-select:none;pointer-events:none;overflow:hidden}
.floating-text span{position:absolute;font-family:'Space Grotesk',sans-serif;opacity:0.35}

/* Login card */
.login-card{position:relative;z-index:10;width:100%;max-width:440px;padding:0 24px}
.card-inner{background:rgba(25,25,28,0.8);backdrop-filter:blur(20px);border-left:4px solid var(--primary);padding:48px;box-shadow:0 0 40px rgba(143,245,255,0.1)}

/* Form */
.form-group{margin-bottom:28px}
.form-label{display:block;font-family:'Manrope',sans-serif;font-size:10px;text-transform:uppercase;letter-spacing:0.2em;color:var(--text-muted);margin-bottom:8px;transition:color 0.2s}
.form-input{width:100%;background:var(--surface-highest);border:none;color:var(--text);padding:16px;font-family:'Manrope',sans-serif;font-size:0.95rem;outline:none;transition:background 0.2s}
.form-input:focus{background:rgba(44,44,47,1)}
.form-input::placeholder{color:rgba(118,117,119,0.4)}
.input-wrap{position:relative}
.input-line{position:absolute;bottom:0;left:0;width:0;height:2px;background:var(--primary);transition:width 0.3s}
.form-input:focus ~ .input-line{width:100%}
.form-group:focus-within .form-label{color:var(--primary)}

/* Button */
.btn-login{width:100%;padding:18px;background:var(--primary);color:#005d63;font-family:'Space Grotesk',sans-serif;font-weight:700;text-transform:uppercase;letter-spacing:0.2em;font-size:0.85rem;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:12px;box-shadow:0 0 20px rgba(143,245,255,0.3);transition:all 0.2s}
.btn-login:hover{box-shadow:0 0 35px rgba(143,245,255,0.5)}
.btn-login:active{transform:scale(0.98)}

/* Error */
.error-box{background:rgba(255,107,155,0.08);border-left:3px solid var(--secondary);color:var(--secondary);padding:12px 16px;margin-bottom:20px;font-size:0.82rem}

/* First run */
.first-run-msg{color:var(--primary);text-align:center;margin-bottom:20px;font-size:0.85rem;font-weight:600;letter-spacing:0.05em}

/* Toggle */
.toggle-wrap{display:flex;align-items:center;gap:10px;margin-bottom:28px}
.toggle{position:relative;width:36px;height:20px;background:var(--surface-highest);border-radius:10px;cursor:pointer}
.toggle input{opacity:0;width:0;height:0}
.toggle-dot{position:absolute;top:2px;left:2px;width:16px;height:16px;background:#fff;border-radius:50%;transition:transform 0.2s}
.toggle input:checked ~ .toggle-dot{transform:translateX(16px)}
.toggle input:checked ~ .toggle-bg{background:var(--primary)}
.toggle-bg{position:absolute;inset:0;border-radius:10px;transition:background 0.2s}
.toggle-text{font-size:10px;text-transform:uppercase;letter-spacing:0.2em;color:var(--text-muted)}

/* Status HUD */
.status-hud{margin-top:32px;display:flex;justify-content:space-between;padding:0 8px}
.status-dot{width:6px;height:6px;border-radius:50%;background:var(--tertiary);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}

/* Footer */
.footer{margin-top:40px;padding-top:32px;border-top:1px solid rgba(72,71,74,0.1);text-align:center}
.footer-text{font-size:9px;text-transform:uppercase;letter-spacing:0.3em;color:var(--outline)}

/* Glow blobs */
.blob-primary{position:fixed;top:-250px;right:-250px;width:500px;height:500px;background:rgba(143,245,255,0.05);border-radius:50%;filter:blur(120px);z-index:-1}
.blob-secondary{position:fixed;bottom:-200px;left:-200px;width:400px;height:400px;background:rgba(255,107,155,0.05);border-radius:50%;filter:blur(100px);z-index:-1}

@media(max-width:500px){.card-inner{padding:32px 24px}.floating-text{display:none}}
.material-symbols-outlined{font-variation-settings:'FILL' 0,'wght' 400,'GRAD' 0,'opsz' 24}
</style>
</head>
<body>
<div class="cyber-grid"></div>

<!-- Floating keywords -->
<div class="floating-text">
<span style="top:10%;left:5%;font-size:1.1rem;color:var(--primary);letter-spacing:0.2em;filter:blur(1px)">Analiza zyskow</span>
<span style="top:15%;right:10%;font-size:1.4rem;color:var(--secondary);letter-spacing:-0.02em;filter:blur(0.5px)">Opisy AI</span>
<span style="bottom:20%;left:15%;font-size:1rem;color:var(--tertiary);letter-spacing:0.2em;opacity:0.6">Raporty ROI</span>
<span style="top:40%;right:5%;font-size:1.6rem;color:rgba(0,222,236,0.4);font-weight:700;letter-spacing:-0.02em;transform:rotate(12deg);filter:blur(2px)">DATA STREAM</span>
<span style="bottom:10%;right:20%;font-size:1.1rem;color:rgba(227,0,113,0.4);letter-spacing:0.2em;text-transform:uppercase">E-commerce Sync</span>
<span style="top:60%;left:2%;font-size:0.8rem;color:var(--outline);letter-spacing:0.2em;transform:rotate(-90deg)">LOGISTICS_CORE_v4.0</span>
</div>

<!-- Login -->
<div class="login-card">
<div class="card-inner">
<!-- Branding -->
<div style="text-align:center;margin-bottom:48px">
<div style="margin-bottom:20px">
<span class=material-symbols-outlined style='font-size:3rem;color:var(--primary);font-variation-settings:'FILL' 1'>sensors</span>
</div>
<h1 class="font-headline" style="font-size:2rem;font-weight:700;letter-spacing:-0.04em;color:var(--primary);text-shadow:0 0 10px rgba(143,245,255,0.4);margin-bottom:6px">{{ brand_name }}</h1>
<p style="font-size:10px;text-transform:uppercase;letter-spacing:0.3em;color:var(--text-muted);font-weight:500">System zarzadzania magazynem</p>
</div>

{% if error %}
<div class="error-box">{{ error }}</div>
{% endif %}

{% if first_run %}
<div class="first-run-msg">Pierwszy start — ustaw dane logowania</div>
{% endif %}

<form method="POST">
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
<div class="form-group">
<label class="form-label">LOGIN</label>
<div class="input-wrap">
<input class="form-input" type="text" name="username" required autofocus value="{{ username or '' }}" placeholder="Wprowadz identyfikator">
<div class="input-line"></div>
</div>
</div>
<div class="form-group">
<div style="display:flex;justify-content:space-between;align-items:center">
<label class="form-label" style="margin-bottom:0">HASLO</label>
</div>
<div class="input-wrap" style="margin-top:8px">
<input class="form-input" type="password" name="password" required placeholder="••••••••">
<div class="input-line"></div>
</div>
</div>

{% if first_run %}
<div class="form-group">
<label class="form-label">POWTORZ HASLO</label>
<div class="input-wrap">
<input class="form-input" type="password" name="password2" id="password2" required placeholder="Powtorz haslo">
<div class="input-line"></div>
</div>
<div id="passMatch" style="font-size:0.78rem;margin-top:6px;display:none"></div>
</div>
{% endif %}

<!-- Remember toggle -->
<div class="toggle-wrap">
<label class="toggle">
<input type="checkbox" name="remember" value="1">
<div class="toggle-bg"></div>
<div class="toggle-dot"></div>
</label>
<span class="toggle-text">Zapamietaj sesje</span>
</div>

<button type="submit" class="btn-login" id="submitBtn">
{% if first_run %}Utworz konto{% else %}Zaloguj{% endif %}
<span class=material-symbols-outlined style=font-size:1.2rem>arrow_right_alt</span>
</button>
</form>

<!-- Footer -->
<div class="footer">
<p class="footer-text">{{ brand_name }} v4.2 &bull; &copy; 2026</p>
</div>
</div>

<!-- Status HUD -->
<div class="status-hud">
<div style="display:flex;align-items:center;gap:8px">
<div class="status-dot"></div>
<span style="font-size:10px;text-transform:uppercase;letter-spacing:0.2em;color:var(--text-muted)">Mainframe Active</span>
</div>
<div style="display:flex;align-items:center;gap:16px;color:rgba(118,117,119,0.6)">
<span class=material-symbols-outlined style=font-size:14px>wifi</span>
<span class=material-symbols-outlined style=font-size:14px>database</span>
<span class=material-symbols-outlined style=font-size:14px>lock</span>
</div>
</div>
</div>

<div class="blob-primary"></div>
<div class="blob-secondary"></div>

{% if first_run %}
<script>
(function(){
    var p1 = document.querySelector('input[name="password"]');
    var p2 = document.getElementById('password2');
    var msg = document.getElementById('passMatch');
    var btn = document.getElementById('submitBtn');
    function check(){
        if(!p2.value) { msg.style.display='none'; btn.disabled=false; return; }
        msg.style.display='block';
        if(p1.value === p2.value){
            msg.style.color='#beee00';
            msg.textContent='Hasla sa zgodne';
            btn.disabled=false;
        } else {
            msg.style.color='#ff6b9b';
            msg.textContent='Hasla nie sa zgodne';
            btn.disabled=true;
        }
    }
    p1.addEventListener('input', check);
    p2.addEventListener('input', check);
})();
</script>
{% endif %}
</body>
</html>'''


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Strona logowania"""
    # Jesli nie ma uzytkownikow — przekieruj do first setup
    if not _has_any_users():
        return redirect(url_for('auth.first_setup'))

    error = None
    username = ''

    if request.method == 'POST':
        client_ip = request.remote_addr

        # Rate limiting
        if _is_rate_limited(client_ip):
            remaining = LOGIN_COOLDOWN // 60
            error = f'Za duzo prob logowania. Sprobuj za {remaining} min.'
            return render_template('login.html', error=error, username='', first_run=False)

        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        conn = _get_auth_db()
        user = conn.execute(
            'SELECT * FROM users WHERE username = ? AND aktywny = 1',
            (username,)
        ).fetchone()

        if user and _verify_password(password, user['password_hash']):
            # Cloudflare Turnstile — walidacja antybot PO credentials OK
            # (oszczedza siteverify call dla oczywiscie zlych hasel).
            # FAIL-CLOSED: jesli verify_token zwroci success=False z jakiegokolwiek
            # powodu (brak tokena, network error, Cloudflare rejected), login blocked.
            try:
                from modules import turnstile as _turnstile
                if _turnstile.is_enabled():
                    ts_token = request.form.get('cf-turnstile-response', '')
                    ts_result = _turnstile.verify_token(ts_token, remote_ip=client_ip)
                    if not ts_result.success:
                        conn.close()
                        # Audit log (nie blokuj odpowiedzi jesli log fail)
                        try:
                            from modules.database import log_admin_action
                            log_admin_action(
                                'login_turnstile_fail',
                                details={
                                    'username': username,
                                    'ip': client_ip,
                                    'error_codes': list(ts_result.error_codes),
                                },
                                success=False,
                                username=username,
                            )
                        except Exception:
                            pass
                        # Nie uzywamy abort(403) — zeby user dostal przyjemniejsza
                        # wiadomosc bez pelnego 403 error page.
                        error = 'Walidacja antybot nieudana. Odswiez strone i sprobuj ponownie.'
                        return render_template(
                            'login.html', error=error, username=username, first_run=False
                        )
            except ImportError:
                # Modul turnstile niezaladowany — zachowaj sie jakby feature disabled
                pass

            # Migracja starych haszy (pbkdf2/SHA-256) do Argon2id
            if _needs_rehash(user['password_hash']):
                conn.execute(
                    'UPDATE users SET password_hash = ? WHERE id = ?',
                    (_hash_password(password), user['id'])
                )
                conn.commit()

            # Udane logowanie — wyczysc licznik prob
            try:
                import sqlite3 as _sq
                con = _sq.connect(DB_PATH, timeout=3)
                con.execute('DELETE FROM login_attempts WHERE ip=?', (client_ip,))
                con.commit(); con.close()
            except Exception:
                pass

            # Regeneracja sesji — ochrona przed session fixation
            session.clear()
            # Wymuś nowy CSRF token po wyczyszczeniu sesji
            from flask_wtf.csrf import generate_csrf
            generate_csrf()

            # 2FA TOTP — jesli user wlaczyl 2FA, zatrzymaj w trybie "pending"
            # (nie ustawiaj user_id) i wymus weryfikacje kodu TOTP.
            _totp_enabled = False
            try:
                _totp_enabled = bool(user['totp_enabled']) and bool(user['totp_secret'])
            except (IndexError, KeyError, TypeError):
                _totp_enabled = False

            if _totp_enabled:
                session['2fa_pending_user_id'] = user['id']
                session['2fa_pending_rola'] = user['rola']
                session['2fa_pending_username'] = user['username']
                session['2fa_pending_next'] = request.args.get('next', '/dashboard')
                session.permanent = True
                conn.close()
                return redirect(url_for('auth.totp_verify'))

            session['user_id'] = user['id']
            session['username'] = user['username']
            session['rola'] = user['rola']
            session.permanent = True

            # Zapisz czas logowania (nie blokuj loginu jesli baza zajeta)
            try:
                conn.execute(
                    'UPDATE users SET ostatnie_logowanie = CURRENT_TIMESTAMP WHERE id = ?',
                    (user['id'],)
                )
                conn.commit()
            except sqlite3.OperationalError:
                pass  # Nie blokuj logowania z powodu locka
            conn.close()

            # PHASE 3 — audyt udanego logowania (infra admin_audit_log;
            # sesja juz ustawiona -> user_id/username/rola auto-fill)
            try:
                from modules.database import log_admin_action
                log_admin_action('login_success', details={'ip': client_ip})
            except Exception:
                pass

            next_url = request.args.get('next', '/dashboard')
            # Zabezpieczenie przed Open Redirect — tylko lokalne ścieżki
            if not next_url.startswith('/') or next_url.startswith('//'):
                next_url = '/dashboard'

            # Sprawdz czy to "swiezy" system — brak kluczy API → kreator
            if next_url == '/dashboard' and user['rola'] == 'admin':
                try:
                    from modules.database import get_config
                    has_allegro = bool(get_config('allegro_client_id', ''))
                    has_telegram = bool(get_config('telegram_bot_token', ''))
                    has_gemini = bool(get_config('gemini_api_key', ''))
                    if not has_allegro and not has_telegram and not has_gemini:
                        session['show_kreator'] = True
                except Exception:
                    pass

            # Zachowaj kiosk mode jesli byl w URL
            if 'kiosk=1' in request.url and 'kiosk' not in next_url:
                sep = '&' if '?' in next_url else '?'
                next_url = next_url + sep + 'kiosk=1'
            return redirect(next_url)
        else:
            _record_failed_login(client_ip)
            # PHASE 3 — audyt nieudanego logowania (widocznosc brute-force;
            # brak sesji -> przekazujemy username jawnie, success=False)
            try:
                from modules.database import log_admin_action
                log_admin_action(
                    'login_failed',
                    details={'username': username, 'ip': client_ip},
                    success=False,
                    username=username,
                )
            except Exception:
                pass
            import sqlite3 as _sq2
            try:
                con2 = _sq2.connect(DB_PATH, timeout=3)
                cnt2 = con2.execute('SELECT COUNT(*) FROM login_attempts WHERE ip=?', (client_ip,)).fetchone()[0]
                con2.close()
                attempts_left = max(0, MAX_LOGIN_ATTEMPTS - cnt2)
            except Exception:
                attempts_left = 1
            if attempts_left > 0:
                error = f'Nieprawidlowy login lub haslo ({attempts_left} prob pozostalo)'
            else:
                error = f'Konto zablokowane na {LOGIN_COOLDOWN // 60} minut'
        conn.close()

    return render_template('login.html', error=error, username=username, first_run=False)


@auth_bp.route('/setup', methods=['GET', 'POST'])
def first_setup():
    """Pierwszy setup — tworzenie konta admin"""
    if _has_any_users():
        return redirect(url_for('auth.login'))

    error = None

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        password2 = request.form.get('password2', '')

        if len(username) < 3:
            error = 'Login musi miec minimum 3 znaki'
        elif len(password) < 8:
            error = 'Haslo musi miec minimum 8 znaków'
        elif password != password2:
            error = 'Hasla nie sa identyczne'
        else:
            try:
                conn = _get_auth_db()
                conn.execute(
                    'INSERT INTO users (username, password_hash, rola) VALUES (?, ?, ?)',
                    (username, _hash_password(password), 'admin')
                )
                conn.commit()
                # Pobierz ID nowego użytkownika
                user = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
                conn.close()

                # Invaliduj cache _has_any_users
                _users_exist_cache['val'] = True
                _users_exist_cache['ts'] = 0

                # Zaloguj od razu
                session['user_id'] = user['id'] if user else 1
                session['username'] = username
                session['rola'] = 'admin'
                session.permanent = True

                # Przekieruj na EULA (pomijamy middleware walki)
                try:
                    from modules.eula import is_eula_accepted
                    if not is_eula_accepted():
                        return redirect('/eula')
                except Exception:
                    pass
                return redirect('/dashboard')
            except Exception as e:
                if 'UNIQUE' in str(e):
                    error = 'Ta nazwa uzytkownika jest juz zajeta'
                else:
                    error = f'Blad tworzenia konta: {str(e)[:100]}'

    return render_template('login.html', error=error, username='', first_run=True)


@auth_bp.route('/logout')
def logout():
    """Wylogowanie"""
    # PHASE 3 — audyt wylogowania PRZED session.clear() (auto-fill z sesji)
    try:
        from modules.database import log_admin_action
        log_admin_action('logout')
    except Exception:
        pass
    session.clear()
    return redirect(url_for('auth.login'))


# ============================================================
# 2FA TOTP (opt-in)
# ============================================================

_TOTP_SETUP_TEMPLATE = '''{% extends "base.html" %}
{% block content %}
<style>
.tw{max-width:560px;margin:40px auto;padding:24px;color:#e5e7eb;font-family:system-ui,sans-serif}
.tw h1{font-size:1.5rem;margin-bottom:8px;color:#8ff5ff}
.tw p{color:#94a3b8;margin-bottom:16px;line-height:1.5}
.tw .qr{background:#fff;padding:16px;border-radius:12px;max-width:280px;margin:20px auto}
.tw .qr svg{width:100%;height:auto}
.tw .secret{background:#12122a;padding:12px;border-radius:8px;font-family:monospace;letter-spacing:2px;text-align:center;margin:16px 0;word-break:break-all;font-size:0.9rem;color:#8ff5ff}
.tw label{display:block;font-size:0.85rem;color:#cbd5e1;margin:16px 0 6px}
.tw input[type=text]{width:100%;padding:14px;background:#0f0f20;border:1px solid #334155;border-radius:10px;color:#fff;font-size:1.1rem;letter-spacing:6px;text-align:center}
.tw button{width:100%;padding:14px;margin-top:16px;background:#6366f1;color:#fff;border:none;border-radius:10px;font-weight:600;cursor:pointer;font-size:0.95rem}
.tw button:hover{opacity:0.92}
.tw .err{background:rgba(239,68,68,.12);border-left:3px solid #ef4444;color:#fca5a5;padding:12px 14px;margin-bottom:16px;border-radius:6px}
.tw .ok{background:rgba(34,197,94,.12);border-left:3px solid #22c55e;color:#86efac;padding:12px 14px;margin-bottom:16px;border-radius:6px}
.tw .codes{background:#0f0f20;border:1px solid #334155;border-radius:10px;padding:16px;margin:20px 0;font-family:monospace;font-size:1rem;letter-spacing:2px}
.tw .codes ul{list-style:none;padding:0;margin:0;columns:2;column-gap:24px}
.tw .codes li{padding:6px 0;color:#e2e8f0}
.tw .warn{color:#fbbf24;font-weight:600;margin:12px 0}
</style>
<div class="tw">
  <h1>Konfiguracja 2FA (TOTP)</h1>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  {% if backup_codes %}
    <div class="ok">2FA wlaczone! Zapisz ponizsze kody zapasowe w bezpiecznym miejscu &mdash; kazdy dziala jednorazowo.</div>
    <div class="warn">Te kody pokazujemy TYLKO RAZ. Po opuszczeniu tej strony nie zobaczysz ich ponownie.</div>
    <div class="codes">
      <ul>
        {% for c in backup_codes %}<li>{{ c }}</li>{% endfor %}
      </ul>
    </div>
    <a href="/ustawienia" style="display:inline-block;padding:12px 24px;background:#6366f1;color:#fff;border-radius:10px;text-decoration:none">Zapisalem kody &rarr; przejdz do ustawien</a>
  {% else %}
    <p>1. Zainstaluj aplikacje Google Authenticator, Authy lub 1Password.</p>
    <p>2. Zeskanuj QR kod ponizej lub wpisz secret recznie.</p>
    <div class="qr">{{ qr_svg|safe }}</div>
    <p>Secret (jesli nie mozesz zeskanowac):</p>
    <div class="secret">{{ secret }}</div>
    <form method="POST">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <input type="hidden" name="secret" value="{{ secret }}">
      <label>Wpisz 6-cyfrowy kod z aplikacji:</label>
      <input type="text" name="code" autocomplete="one-time-code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required autofocus>
      <button type="submit">Potwierdz i wlacz 2FA</button>
    </form>
  {% endif %}
</div>
{% endblock %}'''


_TOTP_VERIFY_TEMPLATE = '''<!DOCTYPE html>
<html lang="pl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weryfikacja 2FA</title>
<style>
body{background:#0e0e10;color:#e5e7eb;font-family:system-ui,sans-serif;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.w{max-width:420px;width:90%;padding:32px;background:#19191c;border-left:4px solid #8ff5ff;border-radius:8px}
h1{color:#8ff5ff;font-size:1.4rem;margin:0 0 16px}
p{color:#94a3b8;margin:0 0 16px;line-height:1.5}
label{display:block;font-size:0.85rem;color:#cbd5e1;margin:16px 0 6px}
input[type=text]{width:100%;padding:14px;background:#0f0f20;border:1px solid #334155;border-radius:10px;color:#fff;font-size:1.2rem;letter-spacing:6px;text-align:center;box-sizing:border-box}
button{width:100%;padding:14px;margin-top:16px;background:#6366f1;color:#fff;border:none;border-radius:10px;font-weight:600;cursor:pointer;font-size:0.95rem}
button:hover{opacity:0.92}
.err{background:rgba(239,68,68,.12);border-left:3px solid #ef4444;color:#fca5a5;padding:12px 14px;margin-bottom:16px;border-radius:6px}
.hint{font-size:0.8rem;color:#64748b;margin-top:16px;text-align:center}
.hint a{color:#8ff5ff}
</style></head>
<body>
  <div class="w">
    <h1>Weryfikacja 2FA</h1>
    <p>Wpisz 6-cyfrowy kod z aplikacji Authenticator lub jeden z kodow zapasowych.</p>
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
    <form method="POST">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <label>Kod:</label>
      <input type="text" name="code" autocomplete="one-time-code" inputmode="text" maxlength="12" required autofocus>
      <button type="submit">Zweryfikuj</button>
    </form>
    <p class="hint"><a href="/auth/logout">Anuluj i wroc do loginu</a></p>
  </div>
</body></html>'''


@auth_bp.route('/2fa/setup', methods=['GET', 'POST'])
def totp_setup():
    """Konfiguracja 2FA - wymaga zalogowanego usera (bez 2fa_verified)."""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))

    from modules import totp as _totp

    conn = _get_auth_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    if not user:
        conn.close()
        return redirect(url_for('auth.login'))

    # Jesli 2FA juz wlaczone - redirect do ustawien
    if user['totp_enabled']:
        conn.close()
        flash('2FA jest juz wlaczone dla tego konta. Aby wygenerowac nowe backup codes, wylacz i wlacz ponownie.', 'info')
        return redirect('/ustawienia')

    error = None
    backup_codes_plain = None

    if request.method == 'POST':
        secret = request.form.get('secret', '').strip()
        code = request.form.get('code', '').strip()

        if not secret or not code:
            error = 'Brak secret lub kodu.'
        elif not _totp.verify_code(secret, code, window=1):
            error = 'Kod nieprawidlowy. Sprawdz zegar w telefonie i sprobuj ponownie.'
            # Audit log nieudanej proby
            try:
                from modules.database import log_admin_action
                log_admin_action(
                    '2fa_setup_fail',
                    details={'username': user['username']},
                    success=False,
                )
            except Exception:
                pass
        else:
            # OK — wygeneruj backup codes i zapisz
            plain, hashed_json = _totp.generate_backup_codes(n=8)
            conn.execute(
                'UPDATE users SET totp_secret = ?, totp_enabled = 1, '
                'totp_backup_codes = ?, totp_last_verified_at = CURRENT_TIMESTAMP '
                'WHERE id = ?',
                (secret, hashed_json, user['id'])
            )
            conn.commit()
            session['2fa_verified'] = True
            backup_codes_plain = plain
            try:
                from modules.database import log_admin_action
                log_admin_action(
                    '2fa_enable',
                    details={'username': user['username']},
                    success=True,
                )
            except Exception:
                pass

    # GET lub POST fail — wygeneruj (lub zachowaj z POSTu) QR i secret
    if backup_codes_plain:
        secret = ''
        qr_svg = ''
    else:
        # Jesli POST fail, zachowaj ten sam secret (zeby user nie musial ponownie skanowac)
        secret = request.form.get('secret', '').strip() or _totp.generate_secret()
        qr_svg = _totp.generate_qr_svg(user['username'], secret)

    conn.close()
    return render_template_string(
        _TOTP_SETUP_TEMPLATE,
        secret=secret,
        qr_svg=qr_svg,
        error=error,
        backup_codes=backup_codes_plain,
    )


@auth_bp.route('/2fa/verify', methods=['GET', 'POST'])
def totp_verify():
    """Weryfikacja kodu TOTP.

    Obsluguje dwa scenariusze:
    A) Pending login: session['2fa_pending_user_id'] ustawione po password check,
       `user_id` jeszcze NIE ustawiony. Po weryfikacji: pelna sesja.
    B) Re-verify: user juz zalogowany (session['user_id']) ale session['2fa_verified']
       != True. Np. require_admin zablokowal dostep do panelu. Po weryfikacji:
       session['2fa_verified'] = True, redirect do zapisanego next_url.
    """
    pending_user_id = session.get('2fa_pending_user_id') or session.get('user_id')
    if not pending_user_id:
        return redirect(url_for('auth.login'))

    from modules import totp as _totp

    conn = _get_auth_db()
    user = conn.execute('SELECT * FROM users WHERE id = ? AND aktywny = 1', (pending_user_id,)).fetchone()
    if not user or not user['totp_enabled'] or not user['totp_secret']:
        conn.close()
        session.clear()
        return redirect(url_for('auth.login'))

    error = None

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        verified = False
        verification_method = ''

        if _totp.verify_code(user['totp_secret'], code, window=1):
            verified = True
            verification_method = 'totp'
        else:
            ok, new_hashes = _totp.verify_backup_code(user['totp_backup_codes'] or '', code)
            if ok:
                verified = True
                verification_method = 'backup_code'
                conn.execute(
                    'UPDATE users SET totp_backup_codes = ? WHERE id = ?',
                    (new_hashes, user['id'])
                )
                conn.commit()

        if verified:
            username = user['username']
            rola = user['rola']
            next_url = session.get('2fa_pending_next', '/dashboard')
            if not next_url.startswith('/') or next_url.startswith('//'):
                next_url = '/dashboard'

            # Scenariusz A (pending login) vs B (re-verify)
            was_pending_only = bool(session.get('2fa_pending_user_id')) and not session.get('user_id')
            if was_pending_only:
                session.clear()
                from flask_wtf.csrf import generate_csrf
                generate_csrf()
                session['user_id'] = user['id']
                session['username'] = username
                session['rola'] = rola
            # W obu scenariuszach:
            session['2fa_verified'] = True
            session.pop('2fa_pending_user_id', None)
            session.pop('2fa_pending_rola', None)
            session.pop('2fa_pending_username', None)
            session.pop('2fa_pending_next', None)
            session.permanent = True

            try:
                conn.execute(
                    'UPDATE users SET ostatnie_logowanie = CURRENT_TIMESTAMP, '
                    'totp_last_verified_at = CURRENT_TIMESTAMP WHERE id = ?',
                    (user['id'],)
                )
                conn.commit()
            except sqlite3.OperationalError:
                pass
            conn.close()

            try:
                from modules.database import log_admin_action
                log_admin_action(
                    '2fa_verify_success',
                    details={'method': verification_method},
                    success=True,
                    user_id=user['id'],
                    username=username,
                    role=rola,
                )
            except Exception:
                pass
            return redirect(next_url)
        else:
            error = 'Kod nieprawidlowy. Sprobuj ponownie lub uzyj backup code.'
            try:
                from modules.database import log_admin_action
                log_admin_action(
                    '2fa_verify_fail',
                    details={'username': user['username']},
                    success=False,
                    user_id=user['id'],
                    username=user['username'],
                    role=user['rola'],
                )
            except Exception:
                pass

    conn.close()
    return render_template_string(_TOTP_VERIFY_TEMPLATE, error=error)


@auth_bp.route('/2fa/disable', methods=['POST'])
def totp_disable():
    """Wylacz 2FA dla zalogowanego usera - WYMAGA aktualnego TOTP kodu."""
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Wymagane logowanie'}), 401

    from modules import totp as _totp

    code = request.form.get('code', '').strip()
    conn = _get_auth_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    if not user or not user['totp_enabled'] or not user['totp_secret']:
        conn.close()
        flash('2FA jest juz wylaczone.', 'info')
        return redirect('/ustawienia')

    # Wymagamy aktualnego TOTP kodu ZANIM wylaczymy (ochrona sesji)
    if not _totp.verify_code(user['totp_secret'], code, window=1):
        # Akceptujemy rowniez backup code jako fallback
        ok, new_hashes = _totp.verify_backup_code(user['totp_backup_codes'] or '', code)
        if not ok:
            conn.close()
            try:
                from modules.database import log_admin_action
                log_admin_action(
                    '2fa_disable_fail',
                    details={'username': user['username']},
                    success=False,
                )
            except Exception:
                pass
            flash('Kod nieprawidlowy. 2FA nie zostalo wylaczone.', 'error')
            return redirect('/ustawienia')
        # Zarejestruj zuzycie backup codu (nawet przy disable)
        conn.execute('UPDATE users SET totp_backup_codes = ? WHERE id = ?', (new_hashes, user['id']))

    # Wylacz 2FA i wyczysc secret + backup codes
    conn.execute(
        'UPDATE users SET totp_enabled = 0, totp_secret = NULL, '
        'totp_backup_codes = NULL WHERE id = ?',
        (user['id'],)
    )
    conn.commit()
    conn.close()

    session.pop('2fa_verified', None)
    try:
        from modules.database import log_admin_action
        log_admin_action(
            '2fa_disable',
            details={'username': user['username']},
            success=True,
        )
    except Exception:
        pass
    flash('2FA wylaczone.', 'success')
    return redirect('/ustawienia')


@auth_bp.route('/zmien-haslo', methods=['GET', 'POST'])
def zmien_haslo():
    """Zmiana hasła zalogowanego użytkownika"""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))

    error = None
    success = None

    if request.method == 'POST':
        stare = request.form.get('old_password', '')
        nowe = request.form.get('new_password', '')
        nowe2 = request.form.get('new_password2', '')

        conn = _get_auth_db()
        user = conn.execute('SELECT password_hash FROM users WHERE id = ?', (session['user_id'],)).fetchone()

        if not user:
            error = 'Nie znaleziono użytkownika'
        elif not _verify_password(stare, user['password_hash']):
            error = 'Nieprawidłowe obecne hasło'
        elif len(nowe) < 8:
            error = 'Nowe hasło musi mieć minimum 8 znaków'
        elif nowe != nowe2:
            error = 'Nowe hasła nie są identyczne'
        elif stare == nowe:
            error = 'Nowe hasło musi być inne niż obecne'
        else:
            conn.execute('UPDATE users SET password_hash = ? WHERE id = ?',
                         (_hash_password(nowe), session['user_id']))
            conn.commit()
            success = 'Hasło zmienione pomyślnie!'
        conn.close()

    html = '''{% extends "base.html" %}
{% block content %}
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Manrope:wght@300;400;500;600&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0" rel="stylesheet">
<style>
/* ── Cyberpunk Change Password ── */
.cp-wrap{font-family:'Manrope',sans-serif;color:#c8d6e5;min-height:80vh;display:flex;align-items:center;justify-content:center;padding:40px 16px;position:relative}

/* Background grid overlay */
.cp-wrap::before{content:'';position:absolute;inset:0;
  background-image:linear-gradient(rgba(143,245,255,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(143,245,255,.03) 1px,transparent 1px);
  background-size:40px 40px;pointer-events:none}

.cp-container{width:100%;max-width:480px;position:relative;z-index:1}

/* ── Header ── */
.cp-header{text-align:center;margin-bottom:32px}
.cp-header-icon{font-size:48px;color:#8ff5ff;filter:drop-shadow(0 0 20px rgba(143,245,255,.5));margin-bottom:12px}
.cp-header h1{font-family:'Space Grotesk',sans-serif;font-size:1.8rem;font-weight:700;color:#fff;
  text-shadow:0 0 30px rgba(143,245,255,.3);letter-spacing:2px;margin:0}
.cp-header-sub{font-size:.85rem;color:#8ff5ff;opacity:.7;margin-top:6px;letter-spacing:1px;
  font-family:'Space Grotesk',sans-serif}

/* ── Glass Card ── */
.cp-card{background:rgba(15,15,30,.7);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
  border:1px solid rgba(143,245,255,.12);border-radius:16px;padding:36px;position:relative;overflow:hidden}

/* Corner accents */
.cp-card::before,.cp-card::after{content:'';position:absolute;width:24px;height:24px;pointer-events:none}
.cp-card::before{top:0;left:0;border-top:2px solid #8ff5ff;border-left:2px solid #8ff5ff;border-radius:0}
.cp-card::after{bottom:0;right:0;border-bottom:2px solid #ff6b9b;border-right:2px solid #ff6b9b;border-radius:0}

/* ── Alert Messages ── */
.cp-alert{padding:14px 16px;border-radius:10px;font-size:.88rem;margin-bottom:20px;display:flex;align-items:center;gap:10px}
.cp-alert .material-symbols-outlined{font-size:20px}
.cp-alert-error{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);color:#fca5a5}
.cp-alert-success{background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.3);color:#86efac}

/* ── Form Fields ── */
.cp-field{margin-bottom:20px}
.cp-field label{display:block;font-family:'Space Grotesk',sans-serif;font-size:.78rem;font-weight:500;
  color:#8ff5ff;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:8px}
.cp-input-wrap{position:relative;display:flex;align-items:center}
.cp-input-wrap .material-symbols-outlined.field-icon{position:absolute;left:14px;font-size:20px;color:rgba(143,245,255,.4);pointer-events:none}
.cp-input-wrap input{width:100%;padding:14px 48px 14px 44px;background:rgba(10,10,25,.8);
  border:1px solid rgba(143,245,255,.1);border-radius:10px;color:#e2e8f0;font-size:.95rem;
  font-family:'Manrope',sans-serif;transition:all .25s ease}
.cp-input-wrap input:focus{outline:none;border-color:rgba(143,245,255,.4);
  box-shadow:0 0 20px rgba(143,245,255,.08);background:rgba(10,10,25,.95)}
.cp-input-wrap input::placeholder{color:rgba(200,214,229,.25)}
.cp-toggle-pw{position:absolute;right:12px;background:none;border:none;cursor:pointer;
  color:rgba(143,245,255,.35);padding:4px;display:flex;align-items:center;transition:color .2s}
.cp-toggle-pw:hover{color:#8ff5ff}
.cp-toggle-pw .material-symbols-outlined{font-size:20px}
.cp-field-hint{font-size:.75rem;color:rgba(200,214,229,.35);margin-top:6px;padding-left:2px}

/* ── Submit Button ── */
.cp-submit{width:100%;padding:16px;margin-top:28px;border:none;border-radius:10px;cursor:pointer;
  font-family:'Space Grotesk',sans-serif;font-size:1rem;font-weight:600;letter-spacing:2px;
  background:linear-gradient(135deg,#8ff5ff,#00bcd4);color:#0a0a1e;
  box-shadow:0 0 30px rgba(143,245,255,.2);transition:all .3s ease;position:relative;overflow:hidden}
.cp-submit:hover{box-shadow:0 0 40px rgba(143,245,255,.35);transform:translateY(-1px)}
.cp-submit:active{transform:translateY(0)}
.cp-submit::after{content:'';position:absolute;top:0;left:-100%;width:100%;height:100%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.2),transparent);transition:left .5s}
.cp-submit:hover::after{left:100%}

/* ── Security Info Panels ── */
.cp-security-row{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:28px}
.cp-sec-panel{background:rgba(15,15,30,.5);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
  border:1px solid rgba(143,245,255,.08);border-radius:12px;padding:18px;text-align:center}
.cp-sec-panel .material-symbols-outlined{font-size:28px;margin-bottom:8px;display:block}
.cp-sec-panel:first-child .material-symbols-outlined{color:#ff6b9b}
.cp-sec-panel:last-child .material-symbols-outlined{color:#8ff5ff}
.cp-sec-panel h4{font-family:'Space Grotesk',sans-serif;font-size:.78rem;font-weight:600;color:#fff;
  margin-bottom:4px;letter-spacing:.5px}
.cp-sec-panel p{font-size:.72rem;color:rgba(200,214,229,.4);line-height:1.4}

/* ── Back Link ── */
.cp-back{display:flex;align-items:center;justify-content:center;gap:6px;margin-top:24px;
  color:rgba(143,245,255,.45);text-decoration:none;font-size:.85rem;font-family:'Space Grotesk',sans-serif;
  transition:color .2s;letter-spacing:.5px}
.cp-back:hover{color:#8ff5ff}
.cp-back .material-symbols-outlined{font-size:18px}

@media(max-width:540px){
  .cp-container{max-width:100%}
  .cp-card{padding:28px 20px}
  .cp-security-row{grid-template-columns:1fr}
  .cp-header h1{font-size:1.5rem}
}
</style>

<div class="cp-wrap">
  <div class="cp-container">

    <!-- Header -->
    <div class="cp-header">
      <span class="material-symbols-outlined cp-header-icon">shield_lock</span>
      <h1>ZMIANA HAS&Lstrok;A</h1>
      <div class="cp-header-sub">UPDATE ACCESS KEYS</div>
    </div>

    <!-- Glass Card -->
    <div class="cp-card">

      {% if error %}
      <div class="cp-alert cp-alert-error">
        <span class="material-symbols-outlined">error</span> {{ error }}
      </div>
      {% endif %}
      {% if success %}
      <div class="cp-alert cp-alert-success">
        <span class="material-symbols-outlined">check_circle</span> {{ success }}
      </div>
      {% endif %}

      <form method="POST" autocomplete="off">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

        <!-- Current password -->
        <div class="cp-field">
          <label>Obecne has&lstrok;o</label>
          <div class="cp-input-wrap">
            <span class="material-symbols-outlined field-icon">lock</span>
            <input type="password" name="old_password" id="cp_old" required autofocus placeholder="Wprowad&zacute; obecne has&lstrok;o">
            <button type="button" class="cp-toggle-pw" onclick="togglePw('cp_old',this)" tabindex="-1">
              <span class="material-symbols-outlined">visibility</span>
            </button>
          </div>
        </div>

        <!-- New password -->
        <div class="cp-field">
          <label>Nowe has&lstrok;o</label>
          <div class="cp-input-wrap">
            <span class="material-symbols-outlined field-icon">key</span>
            <input type="password" name="new_password" id="cp_new" required minlength="8" placeholder="Minimum 8 znak&oacute;w">
            <button type="button" class="cp-toggle-pw" onclick="togglePw('cp_new',this)" tabindex="-1">
              <span class="material-symbols-outlined">visibility</span>
            </button>
          </div>
          <div class="cp-field-hint">Min. 8 znak&oacute;w &bull; Zalecane: litery, cyfry, znaki specjalne</div>
        </div>

        <!-- Confirm password -->
        <div class="cp-field">
          <label>Potwierd&zacute; nowe has&lstrok;o</label>
          <div class="cp-input-wrap">
            <span class="material-symbols-outlined field-icon">verified_user</span>
            <input type="password" name="new_password2" id="cp_new2" required minlength="8" placeholder="Powt&oacute;rz nowe has&lstrok;o">
            <button type="button" class="cp-toggle-pw" onclick="togglePw('cp_new2',this)" tabindex="-1">
              <span class="material-symbols-outlined">visibility</span>
            </button>
          </div>
        </div>

        <!-- Submit -->
        <button type="submit" class="cp-submit">ZMIE&Nacute; HAS&Lstrok;O</button>
      </form>
    </div>

    <!-- Security Info -->
    <div class="cp-security-row">
      <div class="cp-sec-panel">
        <span class="material-symbols-outlined">timer</span>
        <h4>Timeout sesji</h4>
        <p>Sesja wygasa po 30 min nieaktywno&sacute;ci</p>
      </div>
      <div class="cp-sec-panel">
        <span class="material-symbols-outlined">enhanced_encryption</span>
        <h4>Szyfrowanie</h4>
        <p>Has&lstrok;a hashowane algorytmem bcrypt</p>
      </div>
    </div>

    <!-- Back link -->
    <a href="/ustawienia" class="cp-back">
      <span class="material-symbols-outlined">arrow_back</span> Wr&oacute;&cacute; do ustawie&nacute;
    </a>

  </div>
</div>

<script>
function togglePw(id,btn){
  const inp=document.getElementById(id);
  const icon=btn.querySelector('.material-symbols-outlined');
  if(inp.type==='password'){inp.type='text';icon.textContent='visibility_off';}
  else{inp.type='password';icon.textContent='visibility';}
}
</script>
{% endblock %}'''
    return render_template_string(html, error=error, success=success)


# ============================================================
# ZARZĄDZANIE UŻYTKOWNIKAMI (tylko admin)
# ============================================================

@auth_bp.route('/users')
@require_role('admin')
def users_list():
    """Lista użytkowników"""
    conn = _get_auth_db()
    users = conn.execute('SELECT id, username, rola, aktywny, utworzony, ostatnie_logowanie FROM users ORDER BY id').fetchall()
    users = [dict(u) for u in users]
    conn.close()
    return render_template('users.html', users=users)


@auth_bp.route('/users/add', methods=['POST'])
@require_role('admin')
def user_add():
    """Dodaj nowego użytkownika"""
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    rola = request.form.get('rola', 'user')

    if len(username) < 3:
        flash('Login musi miec minimum 3 znaki', 'error')
        return redirect(url_for('auth.users_list'))
    if len(password) < 8:
        flash('Haslo musi miec minimum 8 znaków', 'error')
        return redirect(url_for('auth.users_list'))
    if rola not in ROLE_HIERARCHY:
        rola = 'user'

    conn = _get_auth_db()
    try:
        conn.execute(
            'INSERT INTO users (username, password_hash, rola) VALUES (?, ?, ?)',
            (username, _hash_password(password), rola)
        )
        conn.commit()
        flash(f'Dodano uzytkownika: {username}', 'success')
    except sqlite3.IntegrityError:
        flash(f'Uzytkownik {username} juz istnieje', 'error')
    conn.close()
    return redirect(url_for('auth.users_list'))


@auth_bp.route('/users/toggle/<int:user_id>', methods=['POST'])
@require_role('admin')
def user_toggle(user_id):
    """Aktywuj/dezaktywuj użytkownika"""
    if user_id == session.get('user_id'):
        flash('Nie mozesz dezaktywowac siebie', 'error')
        return redirect(url_for('auth.users_list'))
    conn = _get_auth_db()
    conn.execute('UPDATE users SET aktywny = CASE WHEN aktywny=1 THEN 0 ELSE 1 END WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()
    flash('Zmieniono status uzytkownika', 'success')
    return redirect(url_for('auth.users_list'))


@auth_bp.route('/users/role/<int:user_id>', methods=['POST'])
@require_role('admin')
def user_change_role(user_id):
    """Zmień rolę użytkownika"""
    new_role = request.form.get('rola', 'user')
    if new_role not in ROLE_HIERARCHY:
        new_role = 'user'
    conn = _get_auth_db()
    conn.execute('UPDATE users SET rola = ? WHERE id = ?', (new_role, user_id))
    conn.commit()
    conn.close()
    flash('Zmieniono role uzytkownika', 'success')
    return redirect(url_for('auth.users_list'))


@auth_bp.route('/users/delete/<int:user_id>', methods=['POST'])
@require_role('admin')
def user_delete(user_id):
    """Usuń użytkownika"""
    if user_id == session.get('user_id'):
        flash('Nie mozesz usunac siebie', 'error')
        return redirect(url_for('auth.users_list'))
    conn = _get_auth_db()
    conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()
    flash('Usunieto uzytkownika', 'success')
    return redirect(url_for('auth.users_list'))


ACCESS_DENIED_HTML = '''<!DOCTYPE html>
<html lang="pl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Brak dostępu</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a1a;color:#fff;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#12122a;border:1px solid #ef4444;border-radius:16px;padding:40px;text-align:center;max-width:400px}
h1{font-size:2rem;margin-bottom:12px}
p{color:#94a3b8;margin-bottom:8px}
.role{color:#f59e0b;font-weight:600}
a{display:inline-block;margin-top:20px;padding:12px 24px;background:#6366f1;color:#fff;border-radius:10px;text-decoration:none;font-weight:600}
a:hover{opacity:0.9}
</style></head><body>
<div class="card">
    <h1><span class=material-symbols-outlined>lock</span></h1>
    <h1>Brak dostępu</h1>
    <p>Twoja rola (<span class="role">{{ role }}</span>) nie ma uprawnień do tej strony.</p>
    <p style="font-size:0.8rem;color:#64748b">{{ path }}</p>
    <a href="/">← Dashboard</a>
    <a href="/wysylki" style="background:#22c55e;margin-left:8px"><span class=material-symbols-outlined>inventory_2</span> Wysyłki</a>
    <a href="/magazyn" style="background:#f59e0b;margin-left:8px"><span class=material-symbols-outlined>assignment</span> Magazyn</a>
</div></body></html>'''


USERS_HTML = '''<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Uzytkownicy - {{ brand_name }}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a1a;color:#fff;padding:20px}
h1{font-size:1.5rem;margin-bottom:20px;background:linear-gradient(135deg,#818cf8,#6366f1);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.back{color:#818cf8;text-decoration:none;display:inline-block;margin-bottom:20px}
.back:hover{text-decoration:underline}
table{width:100%;border-collapse:collapse;background:#12122a;border-radius:12px;overflow:hidden}
th,td{padding:12px 16px;text-align:left;border-bottom:1px solid #1e1e3a}
th{background:#1a1a3a;color:#888;font-size:0.8rem;text-transform:uppercase}
td{font-size:0.9rem}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:0.75rem;font-weight:600}
.badge-admin{background:rgba(239,68,68,0.15);color:#ef4444}
.badge-manager{background:rgba(245,158,11,0.15);color:#f59e0b}
.badge-user{background:rgba(99,102,241,0.15);color:#818cf8}
.badge-active{background:rgba(34,197,94,0.15);color:#22c55e}
.badge-inactive{background:rgba(239,68,68,0.15);color:#ef4444}
.btn{padding:6px 14px;border:none;border-radius:8px;cursor:pointer;font-size:0.8rem;font-weight:500;transition:opacity 0.2s}
.btn:hover{opacity:0.8}
.btn-sm{padding:4px 10px;font-size:0.75rem}
.btn-danger{background:rgba(239,68,68,0.2);color:#ef4444}
.btn-primary{background:rgba(99,102,241,0.2);color:#818cf8}
.btn-warn{background:rgba(245,158,11,0.2);color:#f59e0b}
.add-form{background:#12122a;border:1px solid #1e1e3a;border-radius:12px;padding:20px;margin-bottom:20px;display:flex;gap:12px;align-items:end;flex-wrap:wrap}
.add-form .field{display:flex;flex-direction:column;gap:4px}
.add-form label{color:#888;font-size:0.75rem}
.add-form input,.add-form select{padding:8px 12px;background:#0a0a1a;border:1px solid #2a2a4a;border-radius:8px;color:#fff;font-size:0.85rem}
.flash{padding:10px 16px;border-radius:8px;margin-bottom:12px;font-size:0.85rem}
.flash-success{background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);color:#22c55e}
.flash-error{background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);color:#ef4444}
select.role-select{background:#0a0a1a;border:1px solid #2a2a4a;border-radius:6px;color:#fff;padding:4px 8px;font-size:0.8rem}
</style>
</head>
<body>
<a href="/" class="back">← Powrot</a>
<h1>Zarzadzanie uzytkownikami</h1>

{% with messages = get_flashed_messages(with_categories=true) %}
{% for cat, msg in messages %}
<div class="flash flash-{{ cat }}">{{ msg }}</div>
{% endfor %}
{% endwith %}

<form class="add-form" method="POST" action="{{ url_for('auth.user_add') }}">
<div class="field"><label>Login</label><input name="username" required minlength="3"></div>
<div class="field"><label>Haslo</label><input name="password" type="password" required minlength="4"></div>
<div class="field"><label>Rola</label>
<select name="rola"><option value="magazynier">Magazynier</option><option value="user">User</option><option value="manager">Manager</option><option value="admin">Admin</option></select>
</div>
<button class="btn btn-primary" type="submit">Dodaj uzytkownika</button>
</form>

<table>
<thead><tr><th>ID</th><th>Login</th><th>Rola</th><th>Status</th><th>Utworzony</th><th>Ostatnie logowanie</th><th>Akcje</th></tr></thead>
<tbody>
{% for u in users %}
<tr>
<td>{{ u.id }}</td>
<td>{{ u.username }}</td>
<td>
<form method="POST" action="{{ url_for('auth.user_change_role', user_id=u.id) }}" style="display:inline">
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
<select name="rola" class="role-select" onchange="this.form.submit()">
<option value="magazynier" {{ 'selected' if u.rola=='magazynier' }}>Magazynier</option>
<option value="user" {{ 'selected' if u.rola=='user' }}>User</option>
<option value="manager" {{ 'selected' if u.rola=='manager' }}>Manager</option>
<option value="admin" {{ 'selected' if u.rola=='admin' }}>Admin</option>
</select>
</form>
</td>
<td>
{% if u.aktywny %}
<span class="badge badge-active">Aktywny</span>
{% else %}
<span class="badge badge-inactive">Nieaktywny</span>
{% endif %}
</td>
<td>{{ u.utworzony or '-' }}</td>
<td>{{ u.ostatnie_logowanie or 'Nigdy' }}</td>
<td>
<form method="POST" action="{{ url_for('auth.user_toggle', user_id=u.id) }}" style="display:inline">
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
<button class="btn btn-sm btn-warn">{{ 'Dezaktywuj' if u.aktywny else 'Aktywuj' }}</button>
</form>
<form method="POST" action="{{ url_for('auth.user_delete', user_id=u.id) }}" style="display:inline" onsubmit="return confirm('Na pewno usunac?')">
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
<button class="btn btn-sm btn-danger">Usun</button>
</form>
</td>
</tr>
{% endfor %}
</tbody>
</table>
</body>
</html>'''


# ============================================================
# MIDDLEWARE — wymuszanie logowania na wszystkich stronach
# ============================================================

def setup_auth(app):
    """Konfiguruje auth middleware na aplikacji Flask"""
    init_auth_db()

    # Sesja wygasa po 24h
    from datetime import timedelta
    app.permanent_session_lifetime = timedelta(hours=24)

    # Auto-logout po bezczynności (30 minut)
    INACTIVITY_TIMEOUT = 30 * 60  # 30 minut w sekundach

    @app.before_request
    def check_auth():
        # Statyczne pliki — przepusc
        for prefix in PUBLIC_PREFIXES:
            if request.path.startswith(prefix):
                return None

        # Endpointy auth — przepusc
        if request.endpoint and request.endpoint in PUBLIC_ENDPOINTS:
            return None

        # Favicon
        if request.path == '/favicon.ico':
            return None

        # Nie ma uzytkownikow — ale najpierw sprawdź licencję
        has_users = _has_any_users()
        if not has_users:
            # Sprawdź czy licencja aktywna — jeśli nie, kieruj na /license
            if request.path not in ('/license', '/auth/setup') and not request.path.startswith('/static'):
                try:
                    from modules.license import check_license
                    is_valid, _, _ = check_license()
                    if not is_valid:
                        return redirect('/license')
                except (ImportError, Exception):
                    pass
            if request.path != '/auth/setup':
                return redirect('/auth/setup')
            return None

        # Swiezy system — przekieruj admina na kreator konfiguracji
        if session.get('show_kreator') and request.path == '/dashboard':
            session.pop('show_kreator', None)
            return redirect('/ustawienia/kreator?welcome=1')

        # Niezalogowany — auto-login na Pi (localhost / sieć lokalna)
        # SECURITY: Domyślnie WYŁĄCZONY. Włącz w Ustawienia → Bezpieczeństwo.
        # CRITICAL: Auto-login działa TYLKO gdy request nie przyszedł przez proxy.
        # Cloudflare Tunnel / ngrok robią request.remote_addr=127.0.0.1 dla
        # zewnętrznych userów — bez tego checku każdy z internetu dostałby admina.
        if not session.get('user_id'):
            _auto = False
            _remote = request.remote_addr or ''
            # Wykryj proxy: jeśli SĄ headery od proxy/CDN, to request NIE JEST lokalny
            # (nawet jeśli ProxyFix nadpisał remote_addr). Bezpiecznie odrzuć.
            _is_proxied = bool(
                request.headers.get('X-Forwarded-For')
                or request.headers.get('X-Real-IP')
                or request.headers.get('CF-Connecting-IP')
                or request.headers.get('CF-Ray')
                or request.headers.get('ngrok-trace-id')
                or request.headers.get('X-Forwarded-Host')
            )
            _is_local_net = (
                _remote in ('127.0.0.1', '::1')
                or _remote.startswith('192.168.')
                or _remote.startswith('10.')
            )
            from modules.database import get_config_cached
            _auto_login_enabled = get_config_cached('auto_login_lan', 'false') == 'true'
            if _auto_login_enabled and _is_local_net and not _is_proxied:
                try:
                    _conn = _get_auth_db()
                    _admin = _conn.execute("SELECT id, username, rola FROM users WHERE rola='admin' AND aktywny=1 ORDER BY id LIMIT 1").fetchone()
                    _conn.close()
                    if _admin:
                        session['user_id'] = _admin['id']
                        session['username'] = _admin['username']
                        session['rola'] = _admin['rola']
                        session['last_active'] = time.time()
                        session.permanent = True
                        _auto = True
                        print(f"[LOCK] AUTO-LOGIN: {_admin['username']} z {_remote}")
                except Exception as _e:
                    print(f"[WARN] Auto-login error: {_e}")
            if not _auto:
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'success': False, 'error': 'Wymagane logowanie'}), 401
                return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))

        # Auto-logout po bezczynności
        now = time.time()
        last_active = session.get('last_active', now)
        if now - last_active > INACTIVITY_TIMEOUT:
            user_name = session.get('user_name', '')
            session.clear()
            flash(f'Sesja wygasła po 30 min bezczynności. Zaloguj się ponownie.', 'warning')
            return redirect(url_for('auth.login'))
        session['last_active'] = now
        session.permanent = True

        # Ograniczenie dostępu dla roli magazynier
        user_role = session.get('rola', 'user')
        if request.path.startswith('/paletomat'):
            print(f"[LOCK] ROLE CHECK: user={session.get('username')} role={user_role} path={request.path} in_allowed={user_role in ROLE_ALLOWED_PATHS}", flush=True)
        if user_role in ROLE_ALLOWED_PATHS:
            allowed = ROLE_ALLOWED_PATHS[user_role]
            path = request.path
            if not any(path == a or (a != '/' and path.startswith(a + '/')) or (a != '/' and a.endswith('/') and path.startswith(a)) for a in allowed):
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'success': False, 'error': 'Brak uprawnień'}), 403
                return render_template_string(ACCESS_DENIED_HTML, path=path, role=user_role), 403

        return None

    # Blokuj dostęp do wyłączonych modułów
    MODULE_PREFIX_MAP = {
        '/allegro': 'allegro',
        '/olx': 'olx',
        '/vinted': 'vinted',
        '/telegram': 'telegram',
    }

    @app.before_request
    def check_module_enabled():
        from modules.database import is_module_enabled
        for prefix, mod_name in MODULE_PREFIX_MAP.items():
            if request.path.startswith(prefix):
                if not is_module_enabled(mod_name):
                    abort(404)
                break

    # Dodaj username + moduły + branding do kontekstu szablonów
    @app.context_processor
    def inject_user():
        from modules.database import is_module_enabled, get_config_cached
        # Turnstile site key — puste gdy feature disabled (backward compat)
        try:
            from modules import turnstile as _turnstile
            _turnstile_key = _turnstile.get_site_key() if _turnstile.is_enabled() else ''
        except ImportError:
            _turnstile_key = ''
        return {
            'current_user': session.get('username'),
            'current_role': session.get('rola'),
            'module_allegro': is_module_enabled('allegro'),
            'module_olx': is_module_enabled('olx'),
            'module_vinted': is_module_enabled('vinted'),
            'module_telegram': is_module_enabled('telegram'),
            'module_paletomat': is_module_enabled('paletomat'),
            'module_magazynier': is_module_enabled('magazynier'),
            'brand_name': get_config_cached('brand_name', 'AKCES HUB'),
            'brand_color': get_config_cached('brand_color', '#6366f1'),
            # FIX 2026-05-28: os.path.exists() zwraca True/False, ktore Jinja
            # renderuje jako 'True' -> 404 GET /static/True w base.html. Zwraca
            # nazwe pliku (lub pusty string, ktory tez evaluuje na falsy w {% if %}).
            'brand_logo': 'brand_logo.png' if os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'brand_logo.png')) else '',
            'turnstile_site_key': _turnstile_key,
        }
