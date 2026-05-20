"""Sklepakces HMAC verification + decorator.

Replikacja PHP plugin Akces_Hmac class (canonical = METHOD:PATH:TIMESTAMP:BODY,
HMAC-SHA256 hex digest). 3-warstwowa walidacja:

  1. Timestamp window 300s (anti-replay)
  2. Constant-time signature compare (hmac.compare_digest, NIE ==!)
  3. Nonce uniqueness (Redis 24h TTL via modules/redis_nonce_cache.py)

Reuse w handlers:
    from modules.sklepakces_hmac import require_sklepakces_hmac

    @sklepakces_bp.route('/orders', methods=['POST'])
    @require_sklepakces_hmac
    def receive_order():
        ...

Config:
    set_config('sklepakces_hmac_secret', '...') -- MUSI match plugin PHP option
    `akces_hub_hmac_secret` (wpisany w wp-admin → WC → Akces Hub → tab Hub).

Test vectors matching plugin PHPUnit Akces_Hmac_Test::test_sign_canonical_string_format.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from functools import wraps

from flask import request

from modules.api_v1.response import ErrorCodes, error_response


REPLAY_WINDOW_SECONDS = 300


def canonical_string(method: str, path: str, timestamp: int, body: str) -> str:
    """Format MUSI być identyczny z PHP Akces_Hmac::canonical():
        METHOD:PATH:TIMESTAMP:BODY

    METHOD — uppercase ASCII.
    PATH — URL path bez schematu/hosta/query, zaczyna '/'.
    TIMESTAMP — int unix seconds.
    BODY — raw UTF-8 string (empty dla GET).
    """
    return f"{method.upper()}:{path}:{timestamp}:{body}"


def sign(method: str, path: str, timestamp: int, body: str, secret: str) -> str:
    """HMAC-SHA256 hex digest (lowercase, 64 chars). Identical do PHP Akces_Hmac::sign()."""
    canonical = canonical_string(method, path, timestamp, body)
    return hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(
    method: str,
    path: str,
    timestamp: int,
    body: str,
    signature: str,
    secret: str,
) -> tuple[bool, str]:
    """Verify HMAC signature + timestamp window. NIE sprawdza nonce (osobny layer).

    Returns:
        (True, '') jeśli valid
        (False, error_msg) jeśli fail
    """
    if not secret:
        return False, "HMAC secret not configured"

    # Anti-replay timestamp window (oba kierunki — clock skew protection).
    now = int(time.time())
    delta = abs(now - timestamp)
    if delta > REPLAY_WINDOW_SECONDS:
        return False, f"Timestamp outside {REPLAY_WINDOW_SECONDS}s window (delta={delta})"

    # Constant-time signature compare (chroni przed timing attacks).
    expected = sign(method, path, timestamp, body, secret)
    if not hmac.compare_digest(expected, signature):
        return False, "Signature mismatch"

    return True, ""


def get_hmac_secret() -> str:
    """Lookup shared secret w config table.

    Set via Hub admin lub bezpośrednio:
        from modules.database import set_config
        set_config('sklepakces_hmac_secret', '<64 hex chars from plugin WP option>')
    """
    from modules.database import get_config
    return get_config('sklepakces_hmac_secret', '') or ''


def _get_client_ip() -> str:
    """ProxyFix-aware client IP (Cloudflare Tunnel / nginx)."""
    return (
        request.headers.get('CF-Connecting-IP')
        or request.headers.get('X-Real-IP')
        or request.remote_addr
        or ''
    )


def require_sklepakces_hmac(f):
    """Decorator dla `/api/v1/sklepakces/*` endpoints.

    3-warstwowa walidacja: signature → timestamp → nonce.
    Return error_response z odpowiednim status code:
      401 — brak/invalid signature/timestamp
      400 — invalid header format
      403 — nonce replay (signature już użyta)
      500 — HMAC secret nieskonfigurowany w Hub (admin error)
    """

    @wraps(f)
    def wrapper(*args, **kwargs):
        # Extract headers — same nazwy co w plugin PHP Akces_Api_Client.
        signature = request.headers.get('X-Akces-Signature', '').strip()
        timestamp_str = request.headers.get('X-Akces-Timestamp', '').strip()

        if not signature or not timestamp_str:
            return error_response(
                'Missing X-Akces-Signature or X-Akces-Timestamp header',
                ErrorCodes.MISSING_API_KEY,
                status_code=401,
            )

        try:
            timestamp = int(timestamp_str)
        except ValueError:
            return error_response(
                'Invalid X-Akces-Timestamp format (must be unix int)',
                ErrorCodes.BAD_REQUEST,
                status_code=400,
            )

        secret = get_hmac_secret()
        if not secret:
            return error_response(
                'sklepakces_hmac_secret not configured (set via Hub admin)',
                ErrorCodes.INTERNAL_ERROR,
                status_code=500,
            )

        # Layer 1+2: signature + timestamp window.
        body = request.get_data(as_text=True)
        ok, err = verify_signature(
            request.method,
            request.path,
            timestamp,
            body,
            signature,
            secret,
        )
        if not ok:
            # Telegram alert (best-effort) — nie blokuj response na alert failure.
            try:
                from modules.sklepakces_telegram import alert_webhook_failed
                alert_webhook_failed(err, _get_client_ip())
            except Exception:
                pass
            return error_response(
                f'HMAC verification failed: {err}',
                ErrorCodes.INVALID_API_KEY,
                status_code=401,
            )

        # Layer 3: nonce uniqueness (Redis 24h TTL).
        from modules.redis_nonce_cache import is_nonce_seen, mark_nonce_seen
        if is_nonce_seen(signature):
            try:
                from modules.sklepakces_telegram import alert_nonce_replay
                alert_nonce_replay(_get_client_ip())
            except Exception:
                pass
            return error_response(
                'Replay detected (signature already used in window)',
                ErrorCodes.CONFLICT,
                status_code=403,
            )
        mark_nonce_seen(signature)

        return f(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Self-test (verify PHP↔Python parity) — run: python -m modules.sklepakces_hmac
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    SECRET = "test_secret_32_chars_minimum_for_realism_xx"

    # Test vector matching PHP Akces_Hmac_Test::test_sign_canonical_string_format.
    expected = hmac.new(
        SECRET.encode(),
        b'POST:/api/v1/sklepakces/orders:1714400000:{"test":1}',
        hashlib.sha256,
    ).hexdigest()
    actual = sign("POST", "/api/v1/sklepakces/orders", 1714400000, '{"test":1}', SECRET)
    assert actual == expected, f"FAIL: {actual} != {expected}"
    print(f"✓ Test vector PASS — PHP↔Python parity: {actual}")

    # Method case insensitive.
    assert sign("post", "/x", 1, "{}", SECRET) == sign("POST", "/x", 1, "{}", SECRET)
    print("✓ Method case insensitive")

    # Old timestamp rejected.
    old_ts = int(time.time()) - 600
    sig = sign("POST", "/x", old_ts, "{}", SECRET)
    ok, err = verify_signature("POST", "/x", old_ts, "{}", sig, SECRET)
    assert not ok and "window" in err
    print(f"✓ Old timestamp rejected: {err}")

    # Valid signature.
    now = int(time.time())
    sig = sign("POST", "/x", now, "{}", SECRET)
    ok, err = verify_signature("POST", "/x", now, "{}", sig, SECRET)
    assert ok
    print("✓ Valid signature accepted")

    print("\n✅ All HMAC test vectors PASS — Python identical do PHP plugin Akces_Hmac")
