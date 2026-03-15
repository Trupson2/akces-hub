"""
System logowania do AKCES HUB
Prosty auth oparty na sesji Flask + hashowane hasla (SHA-256)
Rate limiting na login (ochrona przed brute-force)
"""

import hashlib
import os
import secrets
import sqlite3
import time
from collections import defaultdict
from functools import wraps
from pathlib import Path

from flask import Blueprint, request, redirect, url_for, session, render_template_string, jsonify, abort, flash

auth_bp = Blueprint('auth', __name__)

DB_PATH = str(Path(__file__).parent.parent / 'akces_hub.db')

# Rate limiting — max 5 prob logowania na 15 minut per IP
_login_attempts = defaultdict(list)  # ip -> [timestamp, ...]
MAX_LOGIN_ATTEMPTS = 5
LOGIN_COOLDOWN = 900  # 15 minut

# Endpointy NIE wymagajace logowania
PUBLIC_ENDPOINTS = {
    'auth.login',
    'auth.first_setup',
    'static',
}

# Prefiksy URL bez logowania (API health, kiosk na Pi)
PUBLIC_PREFIXES = [
    '/static/',
    '/api/health',
    '/api/warehouse/',
]


def _is_rate_limited(ip):
    """Sprawdza czy IP przekroczyl limit prob logowania"""
    now = time.time()
    # Usun stare wpisy
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < LOGIN_COOLDOWN]
    return len(_login_attempts[ip]) >= MAX_LOGIN_ATTEMPTS


def _record_failed_login(ip):
    """Zapisuje nieudana probe logowania"""
    _login_attempts[ip].append(time.time())


def _hash_password(password, salt=None):
    """Hashuje haslo z solą (SHA-256)"""
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{hashed}"


def _verify_password(password, stored_hash):
    """Weryfikuje haslo z hashem"""
    salt = stored_hash.split(':')[0]
    return _hash_password(password, salt) == stored_hash


def _get_auth_db():
    """Polaczenie do bazy dla auth"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
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
    conn.commit()
    conn.close()


def _has_any_users():
    """Sprawdza czy sa jacykolwiek uzytkownicy"""
    conn = _get_auth_db()
    count = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    conn.close()
    return count > 0


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


# Hierarchia ról: admin > manager > user
ROLE_HIERARCHY = {'admin': 3, 'manager': 2, 'user': 1}

def require_role(*roles):
    """Dekorator wymagający jednej z podanych ról (lub wyższej)"""
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
            return f(*args, **kwargs)
        return decorated
    return decorator


# ============================================================
# STRONA LOGOWANIA
# ============================================================

LOGIN_HTML = '''<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Logowanie - {{ brand_name }}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a1a;color:#fff;display:flex;align-items:center;justify-content:center;min-height:100vh}
.login-box{background:#12122a;border:1px solid #1e1e3a;border-radius:16px;padding:40px;width:100%;max-width:400px;box-shadow:0 20px 60px rgba(0,0,0,0.5)}
.logo{text-align:center;margin-bottom:30px}
.logo h1{font-size:1.8rem;background:linear-gradient(135deg,{{ brand_color }},#818cf8);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.logo p{color:#666;font-size:0.85rem;margin-top:4px}
.form-group{margin-bottom:20px}
label{display:block;margin-bottom:6px;color:#888;font-size:0.85rem;font-weight:500}
input{width:100%;padding:12px 16px;background:#0a0a1a;border:1px solid #2a2a4a;border-radius:10px;color:#fff;font-size:1rem;outline:none;transition:border 0.2s}
input:focus{border-color:#6366f1}
button{width:100%;padding:14px;background:linear-gradient(135deg,#6366f1,#818cf8);border:none;border-radius:10px;color:#fff;font-size:1rem;font-weight:600;cursor:pointer;transition:opacity 0.2s}
button:hover{opacity:0.9}
.error{background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);color:#ef4444;padding:10px 14px;border-radius:8px;margin-bottom:16px;font-size:0.85rem}
</style>
</head>
<body>
<div class="login-box">
<div class="logo">
{% if brand_logo %}<img src="/static/brand_logo.png" style="max-height:60px;margin-bottom:10px">{% endif %}
<h1>{{ brand_name }}</h1>
<p>System zarzadzania magazynem</p>
</div>
{% if error %}
<div class="error">{{ error }}</div>
{% endif %}
{% if first_run %}
<p style="color:#818cf8;text-align:center;margin-bottom:20px;font-size:0.9rem">Pierwszy start — ustaw dane logowania</p>
{% endif %}
<form method="POST">
<div class="form-group">
<label>Login</label>
<input type="text" name="username" required autofocus value="{{ username or '' }}">
</div>
<div class="form-group">
<label>Haslo</label>
<input type="password" name="password" required>
</div>
{% if first_run %}
<div class="form-group">
<label>Powtorz haslo</label>
<input type="password" name="password2" required>
</div>
{% endif %}
<button type="submit">{% if first_run %}Utworz konto{% else %}Zaloguj{% endif %}</button>
</form>
</div>
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
            remaining = int(LOGIN_COOLDOWN - (time.time() - min(_login_attempts[client_ip])))
            error = f'Za duzo prob logowania. Sprobuj za {remaining // 60} min.'
            return render_template_string(LOGIN_HTML, error=error, username='', first_run=False)

        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        conn = _get_auth_db()
        user = conn.execute(
            'SELECT * FROM users WHERE username = ? AND aktywny = 1',
            (username,)
        ).fetchone()

        if user and _verify_password(password, user['password_hash']):
            # Udane logowanie — wyczysc licznik prob
            _login_attempts.pop(client_ip, None)

            session['user_id'] = user['id']
            session['username'] = user['username']
            session['rola'] = user['rola']
            session.permanent = True

            # Zapisz czas logowania
            conn.execute(
                'UPDATE users SET ostatnie_logowanie = CURRENT_TIMESTAMP WHERE id = ?',
                (user['id'],)
            )
            conn.commit()
            conn.close()

            next_url = request.args.get('next', '/')
            # Zachowaj kiosk mode jesli byl w URL
            if 'kiosk=1' in request.url and 'kiosk' not in next_url:
                sep = '&' if '?' in next_url else '?'
                next_url = next_url + sep + 'kiosk=1'
            return redirect(next_url)
        else:
            _record_failed_login(client_ip)
            attempts_left = MAX_LOGIN_ATTEMPTS - len(_login_attempts[client_ip])
            if attempts_left > 0:
                error = f'Nieprawidlowy login lub haslo ({attempts_left} prob pozostalo)'
            else:
                error = f'Konto zablokowane na {LOGIN_COOLDOWN // 60} minut'
        conn.close()

    return render_template_string(LOGIN_HTML, error=error, username=username, first_run=False)


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
        elif len(password) < 4:
            error = 'Haslo musi miec minimum 4 znaki'
        elif password != password2:
            error = 'Hasla nie sa identyczne'
        else:
            conn = _get_auth_db()
            conn.execute(
                'INSERT INTO users (username, password_hash, rola) VALUES (?, ?, ?)',
                (username, _hash_password(password), 'admin')
            )
            conn.commit()
            conn.close()

            # Zaloguj od razu
            session['user_id'] = 1
            session['username'] = username
            session['rola'] = 'admin'
            session.permanent = True

            return redirect('/')

    return render_template_string(LOGIN_HTML, error=error, username='', first_run=True)


@auth_bp.route('/logout')
def logout():
    """Wylogowanie"""
    session.clear()
    return redirect(url_for('auth.login'))


# ============================================================
# ZARZĄDZANIE UŻYTKOWNIKAMI (tylko admin)
# ============================================================

@auth_bp.route('/users')
@require_role('admin')
def users_list():
    """Lista użytkowników"""
    conn = _get_auth_db()
    users = conn.execute('SELECT id, username, rola, aktywny, utworzony, ostatnie_logowanie FROM users ORDER BY id').fetchall()
    conn.close()
    return render_template_string(USERS_HTML, users=users)


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
    if len(password) < 4:
        flash('Haslo musi miec minimum 4 znaki', 'error')
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
<select name="rola"><option value="user">User</option><option value="manager">Manager</option><option value="admin">Admin</option></select>
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
<select name="rola" class="role-select" onchange="this.form.submit()">
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
<button class="btn btn-sm btn-warn">{{ 'Dezaktywuj' if u.aktywny else 'Aktywuj' }}</button>
</form>
<form method="POST" action="{{ url_for('auth.user_delete', user_id=u.id) }}" style="display:inline" onsubmit="return confirm('Na pewno usunac?')">
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

        # Nie ma uzytkownikow — kieruj na setup
        if not _has_any_users():
            if request.endpoint != 'auth.first_setup':
                return redirect(url_for('auth.first_setup'))
            return None

        # Niezalogowany — kieruj na login
        if not session.get('user_id'):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'error': 'Wymagane logowanie'}), 401
            return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))

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
            'brand_logo': os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'brand_logo.png')),
        }
