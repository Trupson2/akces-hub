"""Redis-backed nonce cache dla sklepakces HMAC layer 3 (replay protection).

Każda HMAC signature jest 1× akceptowana w obrębie 24h okna. Bez tego
atakujący w network mógłby odtworzyć valid request w 5-min plugin'owym oknie
replay protection.

Fallback do in-memory dict gdy Redis down (z warning + Telegram alert raz na proces).
Single-process fallback NIE chroni przed replay across Gunicorn workers — w prod
RPi5 powinien mieć Redis działający.

Key format: `akces:sklepakces:nonce:{signature}`
TTL: 24h (86400s) — generous margin nad plugin'owym 5-min replay window.

Config (opcjonalny):
    set_config('redis_url', 'redis://localhost:6379/0')   # default
"""
from __future__ import annotations

import threading
import time
from typing import Optional

try:
    import redis as _redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


_NONCE_TTL_SECONDS = 86400  # 24h
_KEY_PREFIX = "akces:sklepakces:nonce:"

# Fallback in-memory store (used when Redis unavailable)
_fallback_cache: dict[str, float] = {}
_fallback_lock = threading.Lock()
_fallback_warning_sent = False

# Redis client singleton (lazy init)
_redis_client: Optional["_redis.Redis"] = None
_redis_lock = threading.Lock()


def _get_redis():
    """Lazy singleton — initialize client on first call, retry on failure."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    if not REDIS_AVAILABLE:
        return None

    with _redis_lock:
        if _redis_client is not None:
            return _redis_client
        try:
            from modules.database import get_config
            url = get_config('redis_url', '') or 'redis://localhost:6379/0'
            client = _redis.from_url(url, decode_responses=True, socket_timeout=2.0)
            client.ping()  # verify alive
            _redis_client = client
            return _redis_client
        except Exception as e:
            print(f"[WARN] Redis nonce cache init failed: {e}")
            return None


def _fallback_cleanup() -> None:
    """Remove expired entries from in-memory fallback."""
    cutoff = time.time() - _NONCE_TTL_SECONDS
    expired = [k for k, t in _fallback_cache.items() if t < cutoff]
    for k in expired:
        del _fallback_cache[k]


def _alert_fallback_once() -> None:
    """Telegram alert (raz na proces) gdy fallback aktywuje."""
    global _fallback_warning_sent
    if _fallback_warning_sent:
        return
    _fallback_warning_sent = True
    try:
        from modules.sklepakces_telegram import alert_redis_down
        alert_redis_down()
    except Exception:
        pass


def is_nonce_seen(nonce: str) -> bool:
    """Sprawdz czy nonce był już widziany (replay detection).

    Returns True → replay detected, caller powinien zwrócić 403.
    """
    client = _get_redis()

    if client is not None:
        try:
            return bool(client.exists(_KEY_PREFIX + nonce))
        except Exception as e:
            print(f"[WARN] Redis EXISTS failed: {e}")
            # Fall through to in-memory fallback

    _alert_fallback_once()
    with _fallback_lock:
        _fallback_cleanup()
        return nonce in _fallback_cache


def mark_nonce_seen(nonce: str, ttl_seconds: int = _NONCE_TTL_SECONDS) -> None:
    """Mark nonce as seen z TTL (default 24h).

    Atomic via SET ... EX (Redis) lub timestamped dict entry (fallback).
    """
    client = _get_redis()

    if client is not None:
        try:
            client.setex(_KEY_PREFIX + nonce, ttl_seconds, "1")
            return
        except Exception as e:
            print(f"[WARN] Redis SETEX failed: {e}")
            # Fall through

    _alert_fallback_once()
    with _fallback_lock:
        _fallback_cache[nonce] = time.time()


def get_stats() -> dict:
    """Debug helper — Redis status + counts. Useful in health checks."""
    client = _get_redis()
    stats = {
        'redis_available': client is not None,
        'redis_library_installed': REDIS_AVAILABLE,
        'fallback_size': len(_fallback_cache),
        'fallback_warning_sent': _fallback_warning_sent,
        'ttl_seconds': _NONCE_TTL_SECONDS,
    }
    if client is not None:
        try:
            count = sum(1 for _ in client.scan_iter(_KEY_PREFIX + '*'))
            stats['redis_nonces_count'] = count
        except Exception:
            stats['redis_nonces_count'] = -1
    return stats


def clear_all() -> int:
    """Test helper — wyczyść wszystko (Redis + fallback). Returns liczbę usuniętych."""
    deleted = 0
    client = _get_redis()
    if client is not None:
        try:
            for key in client.scan_iter(_KEY_PREFIX + '*'):
                client.delete(key)
                deleted += 1
        except Exception:
            pass
    with _fallback_lock:
        deleted += len(_fallback_cache)
        _fallback_cache.clear()
    return deleted
