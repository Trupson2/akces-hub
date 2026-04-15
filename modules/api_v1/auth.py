"""API v1 autentykacja przez X-API-Key.

Klucze maja format `ak_live_<32 znaki base62>` (~40 char calkowitej dlugosci).
Przy tworzeniu zwracamy plain text RAZ. W DB trzymamy bcrypt hash + prefix
(pierwsze 8 znakow, np. `ak_live_abc12345`) dla szybkiego lookup.

Przy weryfikacji:
1. Z headera bierzemy X-API-Key (lub Authorization: Bearer ak_...).
2. Wyciagamy prefix z klucza, robimy SELECT po indexowanym api_keys.key_prefix.
3. bcrypt.check_password_hash porownuje hash.
4. Jesli revoked -> 403. Jesli lookup failure -> 401.

Po udanym request: wpis do api_usage_log + update last_used_at (async).
"""
from __future__ import annotations

import secrets
import string
import time
import threading
from functools import wraps

from flask import g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from . import api_v1_bp
from .response import error_response, success_response, ErrorCodes


# ---------------------------------------------------------------------------
# Key generation / verification
# ---------------------------------------------------------------------------

KEY_PREFIX_LENGTH = 8            # po "ak_live_" + 8 znakow = indexed lookup
KEY_BODY_LENGTH = 32             # dlugosc losowej czesci
KEY_PREFIX = 'ak_live_'          # aktualnie zawsze production; future: ak_test_


def _random_base62(length: int) -> str:
    """Kryptograficznie bezpieczny losowy string z alfabetu base62."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def generate_api_key():
    """Generuje nowy klucz API.

    Returns:
        tuple (plain_key, key_hash, key_prefix):
            - plain_key: np. "ak_live_abc12345XYZ..." — pokazywany userowi RAZ
            - key_hash: werkzeug pbkdf2 hash (kompatybilny z reszta systemu)
            - key_prefix: pierwsze ~16 znakow klucza (do fast DB lookup)
    """
    body = _random_base62(KEY_BODY_LENGTH)
    plain_key = f'{KEY_PREFIX}{body}'
    # prefix ma pelne "ak_live_" + pierwsze 8 znakow body, zeby miec unikalnosc
    key_prefix = plain_key[:len(KEY_PREFIX) + KEY_PREFIX_LENGTH]
    # generate_password_hash: pbkdf2:sha256 (szybsze niz bcrypt, wystarczajaco
    # silne dla API keys 40-char entropy ~190 bitow)
    key_hash = generate_password_hash(plain_key, method='pbkdf2:sha256', salt_length=16)
    return plain_key, key_hash, key_prefix


def _extract_key_from_request():
    """Zwraca plain API key z requestu lub None.

    Priorytet: X-API-Key > Authorization: Bearer.
    """
    header = request.headers.get('X-API-Key', '').strip()
    if header:
        return header
    auth = request.headers.get('Authorization', '').strip()
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    return None


def verify_api_key(plain_key):
    """Weryfikuje klucz API.

    Returns:
        dict(row) z api_keys jesli valid + aktywny
        None jesli brak / niepoprawny
        'revoked' (str) jesli klucz jest w DB ale ma revoked_at IS NOT NULL
    """
    if not plain_key or not plain_key.startswith(KEY_PREFIX):
        return None
    prefix = plain_key[:len(KEY_PREFIX) + KEY_PREFIX_LENGTH]
    if len(prefix) < len(KEY_PREFIX) + 4:
        return None

    from modules.database import get_db
    try:
        conn = get_db()
        # Szukamy po prefixie (indexed). Moze byc wiele kluczy z tym samym
        # prefixem (kolizja mozliwa ale bardzo rzadka przy 8-char suffix).
        rows = conn.execute(
            'SELECT id, key_hash, key_prefix, name, created_at, last_used_at,'
            ' revoked_at, rate_limit_per_min '
            'FROM api_keys WHERE key_prefix = ?',
            (prefix,),
        ).fetchall()
    except Exception:
        return None

    for row in rows:
        try:
            if check_password_hash(row['key_hash'], plain_key):
                if row['revoked_at']:
                    return 'revoked'
                return dict(row)
        except Exception:
            continue
    return None


# Update last_used_at asynchronicznie — nie blokuj requestu.
_update_lock = threading.Lock()


def _async_update_last_used(api_key_id: int):
    """Odpal update w osobnym watku zeby nie blokowac."""

    def _run():
        try:
            from modules.database import get_db
            with _update_lock:
                conn = get_db()
                conn.execute(
                    'UPDATE api_keys SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?',
                    (api_key_id,),
                )
                conn.commit()
        except Exception:  # pragma: no cover
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def _log_usage(api_key_id: int, status_code: int, response_time_ms: int):
    """Wpis do api_usage_log dla analytics per-klucz."""
    try:
        from modules.database import get_db
        conn = get_db()
        # IP z prawdziwego headera (CF-Connecting-IP > X-Real-IP > remote_addr)
        ip = (
            request.headers.get('CF-Connecting-IP')
            or request.headers.get('X-Real-IP')
            or request.remote_addr
            or ''
        )
        conn.execute(
            'INSERT INTO api_usage_log (api_key_id, endpoint, method, status_code,'
            ' ip_address, response_time_ms) VALUES (?, ?, ?, ?, ?, ?)',
            (api_key_id, request.path, request.method, status_code, ip, response_time_ms),
        )
        conn.commit()
    except Exception:  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def require_api_v1(f):
    """Dekorator dla endpointow /api/v1/*.

    Zachowanie:
    - Brak X-API-Key / Bearer -> 401 MISSING_API_KEY
    - Nieprawidlowy klucz    -> 401 INVALID_API_KEY
    - Klucz revoked           -> 403 API_KEY_REVOKED
    - Klucz OK                -> ustawia g.api_key_id, g.api_key_name,
                                 g.api_key_rate_limit i przepuszcza

    Po handlerze (w after_request hook w __init__.py byloby czystsze, ale
    zeby nie gmerac ze wspolnym app.after_request — robimy inline):
    - Wpis do api_usage_log
    - Async update last_used_at
    """

    @wraps(f)
    def wrapper(*args, **kwargs):
        t_start = time.perf_counter()
        plain = _extract_key_from_request()
        if not plain:
            return error_response(
                'Missing API key. Provide X-API-Key header or Authorization: Bearer',
                ErrorCodes.MISSING_API_KEY,
                status_code=401,
            )

        result = verify_api_key(plain)
        if result is None:
            return error_response(
                'Invalid API key',
                ErrorCodes.INVALID_API_KEY,
                status_code=401,
            )
        if result == 'revoked':
            return error_response(
                'API key has been revoked',
                ErrorCodes.API_KEY_REVOKED,
                status_code=403,
            )

        # result to dict z row
        g.api_key_id = result['id']
        g.api_key_name = result['name']
        g.api_key_rate_limit = result.get('rate_limit_per_min') or 60

        # Rate limit check (custom, bez Flask-Limiter decoratora — robimy inline
        # zeby miec limit z DB per-key)
        from .rate_limit import check_rate_limit, rate_limit_headers
        limited, remaining, reset_at = check_rate_limit(
            g.api_key_id, g.api_key_rate_limit
        )
        if limited:
            resp = error_response(
                'Rate limit exceeded',
                ErrorCodes.RATE_LIMIT_EXCEEDED,
                status_code=429,
            )
            # resp to (response, status) tuple — dodaj headery
            resp[0].headers.update(rate_limit_headers(
                g.api_key_rate_limit, 0, reset_at))
            return resp

        # Wywolaj handler
        try:
            response = f(*args, **kwargs)
        except Exception as e:
            from modules.logger import log_error
            try:
                log_error(f'API v1 handler exception: {e}')
            except Exception:
                pass
            response = error_response(
                'Internal server error',
                ErrorCodes.INTERNAL_ERROR,
                status_code=500,
            )

        elapsed_ms = int((time.perf_counter() - t_start) * 1000)

        # Flask handler moze zwrocic tuple (resp, status), resp, lub Response
        status_code = 200
        if isinstance(response, tuple):
            if len(response) >= 2 and isinstance(response[1], int):
                status_code = response[1]
            flask_response = response[0]
        else:
            flask_response = response
            try:
                status_code = flask_response.status_code
            except Exception:
                pass

        # Dodaj rate-limit headers
        try:
            flask_response.headers.update(rate_limit_headers(
                g.api_key_rate_limit, remaining, reset_at))
        except Exception:
            pass

        # Async: log + last_used
        _log_usage(g.api_key_id, status_code, elapsed_ms)
        _async_update_last_used(g.api_key_id)

        return response

    return wrapper


# ---------------------------------------------------------------------------
# Public meta endpoints (NIE wymagaja auth)
# ---------------------------------------------------------------------------

@api_v1_bp.route('/health', methods=['GET'])
def health():
    """Publiczny healthcheck. Nie wymaga auth.

    Sluzy klientom do sprawdzenia czy instancja AKCES HUB zyje, zanim
    zaczna slac realne requesty.

    Response 200:
        {"status": "success", "data": {"service": "akces-hub-api", "version": "v1", "ok": true}}
    """
    return success_response({
        'service': 'akces-hub-api',
        'version': 'v1',
        'ok': True,
    })


@api_v1_bp.route('/me', methods=['GET'])
@require_api_v1
def me():
    """Info o aktualnie uzywanym API key (bez wartosci).

    Zwraca metadane klucza (nazwa, prefix, rate limit, last_used_at),
    pomocne do debugowania integracji po stronie klienta.
    """
    from modules.database import get_db
    conn = get_db()
    row = conn.execute(
        'SELECT key_prefix, name, created_at, last_used_at, rate_limit_per_min '
        'FROM api_keys WHERE id = ?',
        (g.api_key_id,),
    ).fetchone()
    if not row:
        return error_response('Key metadata not available', ErrorCodes.NOT_FOUND, 404)
    return success_response({
        'key_prefix': row['key_prefix'],
        'name': row['name'],
        'created_at': row['created_at'],
        'last_used_at': row['last_used_at'],
        'rate_limit_per_min': row['rate_limit_per_min'],
    })
