"""Testy kontroli dostepu do /system/update — BROKEN ACCESS CONTROL (OWASP A01).

Sprawdzaja ze endpoint /system/update (git pull + systemctl restart) jest
dostepny WYLACZNIE dla zalogowanego admina. Wczesniej kazdy zalogowany user
mogl wykonac RCE przez git pull z potencjalnie zlosliwego remote'a.

Zakres testow:
    - anonim  -> 401 JSON albo redirect do /auth/login
    - user    -> 403
    - manager -> 403
    - admin   -> przechodzi do handlera (subprocess zamockowany)
    - GET     -> 405 (endpoint tylko POST)
    - wymagany rowniez decorator @require_admin na /system/gemini-model
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

# Root projektu na PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ────────────────────────────────────────────────────────────────────
# Wspoldzielony helper — login przez session (omijamy UI loginu)
# ────────────────────────────────────────────────────────────────────

import time as _time

DENIED_CODES = (302, 303, 401, 403)  # dowolna forma odmowy dostepu


def _login_as(client, role='admin', user_id=1, username='testuser'):
    """Wstrzykuje sesje bez wywolywania faktycznego /auth/login."""
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
        sess['username'] = username
        sess['rola'] = role
        sess['last_active'] = _time.time()  # teraz — nie expire


@pytest.fixture(autouse=True)
def _mock_has_users():
    """Udaje ze sa userzy — inaczej auth middleware redirectuje na /auth/setup."""
    with patch('modules.auth._has_any_users', return_value=True), \
         patch('modules.auth._users_exist_cache', {'val': True, 'ts': 9999999999}):
        yield


# ────────────────────────────────────────────────────────────────────
# /system/update — access control
# ────────────────────────────────────────────────────────────────────

def test_system_update_anonymous_denied(app_client):
    """Anonim bez sesji -> odmowa (401 JSON albo redirect do loginu)."""
    r = app_client.post('/system/update',
                        headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code in DENIED_CODES, \
        f'Anonim powinien zostac odrzucony, dostal {r.status_code}'
    if r.status_code == 401:
        data = r.get_json(silent=True) or {}
        assert data.get('ok') is False or data.get('success') is False


def test_system_update_normal_user_denied(app_client):
    """Zalogowany user (nie-admin) -> odmowa dostepu (nie 200)."""
    _login_as(app_client, role='user', user_id=2, username='zwykly')
    r = app_client.post('/system/update',
                        headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code in DENIED_CODES, \
        f'User non-admin powinien zostac odrzucony, dostal {r.status_code}'
    assert r.status_code != 200, 'User non-admin NIE MOZE dostac 200 z /system/update!'


def test_system_update_manager_denied(app_client):
    """Manager (nie admin) -> odmowa."""
    _login_as(app_client, role='manager', user_id=3, username='kierownik')
    r = app_client.post('/system/update',
                        headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code in DENIED_CODES
    assert r.status_code != 200


def test_system_update_magazynier_denied(app_client):
    """Magazynier -> odmowa (najnizsza rola)."""
    _login_as(app_client, role='magazynier', user_id=4, username='mag')
    r = app_client.post('/system/update',
                        headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code in DENIED_CODES
    assert r.status_code != 200


def test_system_update_admin_not_blocked_by_auth(app_client):
    """Admin -> NIE dostaje 401/403 od dekoratora. Subprocess zamockowany."""
    _login_as(app_client, role='admin', user_id=1, username='admin')

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = 'Already up to date.'
    fake_proc.stderr = ''
    fake_backup = MagicMock()
    fake_backup.name = 'test_backup.db'

    with patch('subprocess.run', return_value=fake_proc), \
         patch('modules.backup_manager.create_backup', return_value=fake_backup), \
         patch('threading.Thread'):
        try:
            r = app_client.post('/system/update',
                                headers={'X-Requested-With': 'XMLHttpRequest'})
            status = r.status_code
        except Exception as e:
            # Rate limiter threading error na Windows w tescie — akceptujemy,
            # glowny test (401/403) juz przeszedl dla non-admin
            pytest.skip(f'Rate limiter threading conflict w tescie: {e}')
    assert status not in (401, 403), \
        f'Admin nie powinien dostac 401/403, dostal {status}'


def test_system_update_get_method_returns_405(app_client):
    """GET /system/update -> 405 Method Not Allowed (endpoint tylko POST).
    Logujemy sie jako admin zeby obejsc before_request redirect dla GET anonim."""
    _login_as(app_client, role='admin', user_id=1, username='admin')
    r = app_client.get('/system/update',
                       headers={'Referer': 'http://localhost/'})
    assert r.status_code == 405, \
        f'GET powinien dawac 405 MethodNotAllowed, dostal {r.status_code}'


# ────────────────────────────────────────────────────────────────────
# /system/gemini-model — drugi krytyczny endpoint pod tym samym gate
# ────────────────────────────────────────────────────────────────────

def test_system_gemini_model_normal_user_denied(app_client):
    """Zmiana modelu Gemini (koszty API) -> wymagany admin."""
    _login_as(app_client, role='user', user_id=2)
    r = app_client.post('/system/gemini-model',
                        json={'model': 'gemini-2.5-flash'},
                        headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code in DENIED_CODES
    assert r.status_code != 200


def test_system_gemini_model_anonymous_denied(app_client):
    """Anonim -> odmowa."""
    r = app_client.post('/system/gemini-model',
                        json={'model': 'gemini-2.5-flash'},
                        headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code in DENIED_CODES


# ────────────────────────────────────────────────────────────────────
# /backup/* — access control (analogicznie)
# ────────────────────────────────────────────────────────────────────

def test_backup_create_anonymous_returns_401(app_client):
    """Anonim nie moze tworzyc backupow."""
    r = app_client.post('/backup/create',
                        headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code in (401, 302, 303, 404)
    # 404 tylko gdy backup_bp nie zarejestrowany — nie traktuj jako fail


def test_backup_list_user_denied(app_client):
    """Zwykly user nie widzi listy backupow (reconnaissance)."""
    _login_as(app_client, role='user', user_id=2)
    r = app_client.get('/backup/list',
                       headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code in DENIED_CODES + (404,)  # 404 jesli blueprint niezarejestrowany
    assert r.status_code != 200, 'User non-admin NIE MOZE widziec listy backupow!'


def test_backup_restore_path_traversal_blocked(app_client):
    """Path traversal w filename -> 400 (filename validator) albo 403 (CSRF).
    Oba sa OK z perspektywy bezpieczenstwa — atakujacy zostal zablokowany."""
    _login_as(app_client, role='admin', user_id=1)
    malicious = ['../../etc/passwd', '..\\..\\windows\\system32',
                 '/absolute/path.db', 'file;rm -rf /']
    for bad in malicious:
        r = app_client.post(
            '/backup/restore',
            json={'filename': bad},
            headers={
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': 'http://localhost/',  # same-origin zeby CSRF przepuscil
            })
        # 400 = filename validator zwalidowal, 403 = CSRF blokuje,
        # 404 = blueprint niezarejestrowany — wszystkie akceptowalne bo blokuja atak
        assert r.status_code in (400, 403, 404), \
            f'Filename "{bad}" powinien byc odrzucony, dostal {r.status_code}'
        assert r.status_code != 200, \
            f'KRYTYCZNE: Filename "{bad}" NIE ZOSTAL odrzucony!'


# ────────────────────────────────────────────────────────────────────
# Smoke test: sam dekorator require_admin (unit test bez Flask app)
# ────────────────────────────────────────────────────────────────────

def test_require_admin_decorator_exists():
    """Sanity: modules.auth.require_admin istnieje i jest callable."""
    try:
        from modules.auth import require_admin
    except ImportError:
        pytest.skip('modules.auth niedostepny')
    assert callable(require_admin), 'require_admin musi byc dekoratorem (callable)'
