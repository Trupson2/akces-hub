"""Per-API-key rate limiter.

Uzywamy lightweight sliding-window w pamieci (dict[api_key_id] -> deque timestamps).
Flask-Limiter jest juz zainstalowany w app.py dla globalnego limitu per-IP,
ale nie nadaje sie tu bo:
  a) Chcemy limit z kolumny api_keys.rate_limit_per_min (per-klient configurable)
  b) Chcemy klucz `apiv1:{id}` zamiast per-IP (klient moze byc za proxy)

Implementacja: monotonic time + deque, thread-safe przez Lock.
Na potrzeby single-instance Pi deployment to wystarczy. Dla multi-instance
trzeba by przepiac na Redis/Limiter storage_uri — zostawione jako TODO.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict, Tuple


_buckets: Dict[int, Deque[float]] = {}
_lock = threading.Lock()

_WINDOW_SECONDS = 60.0


def check_rate_limit(api_key_id: int, limit_per_min: int) -> Tuple[bool, int, int]:
    """Sprawdza limit dla klucza. Jednoczesnie rejestruje biezacy request.

    Args:
        api_key_id: id z api_keys.id
        limit_per_min: limit requestow na minute (z api_keys.rate_limit_per_min)

    Returns:
        tuple (is_limited, remaining, reset_timestamp):
            is_limited: True jesli przekroczony -> caller zwraca 429
            remaining: ile requestow zostalo w biezacym oknie
            reset_timestamp: unix timestamp kiedy okno sie zresetuje
    """
    if limit_per_min <= 0:
        limit_per_min = 60

    now = time.monotonic()
    cutoff = now - _WINDOW_SECONDS

    with _lock:
        bucket = _buckets.setdefault(api_key_id, deque())
        # Wyczysc stare requesty poza oknem
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        count = len(bucket)
        reset_at = int(time.time()) + int(_WINDOW_SECONDS)

        if count >= limit_per_min:
            return True, 0, reset_at

        bucket.append(now)
        remaining = limit_per_min - count - 1
        return False, remaining, reset_at


def rate_limit_headers(limit: int, remaining: int, reset: int) -> Dict[str, str]:
    """Headery dla odpowiedzi. IETF draft `RateLimit-*` + GitHub style X-RateLimit-*.

    Klient moze checkac dowolnie — zwracamy obie wersje.
    """
    return {
        'X-RateLimit-Limit': str(int(limit)),
        'X-RateLimit-Remaining': str(int(remaining)),
        'X-RateLimit-Reset': str(int(reset)),
    }


def reset_rate_limits():
    """Reset calego stanu (uzywane w testach)."""
    with _lock:
        _buckets.clear()
