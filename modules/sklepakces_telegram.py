"""Sklepakces Telegram alerts — reuse modules/telegram_bot.send_telegram with [HUB] prefix.

Alert types:
  order_received      Nowe zamówienie z sklepakces.pl
  product_synced      Produkt zsynchronizowany (create/update)
  stock_changed       Stock update (sklepakces → Hub)
  webhook_failed      HMAC fail / timestamp out of window / schema validation
  nonce_replay        Replay detected (signature reuse)
  redis_down          Redis niedostępny, fallback in-memory aktywny

Throttle: 10/min/category (in-memory deque, single-process). Chroni przed
spamem gdy plugin'owy retry queue masowo failuje (np. Hub restart).

Format: HTML (Telegram parse_mode=HTML), `send_telegram` strip-uje niedozwolone tagi.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from modules.telegram_bot import send_telegram


_THROTTLE_LIMIT = 10
_THROTTLE_WINDOW = 60.0  # seconds

_throttle_buckets: dict[str, deque[float]] = {}
_throttle_lock = threading.Lock()


def _check_throttle(category: str) -> bool:
    """Returns True if NOT throttled (OK to send). False if throttled.

    Sliding 60s window per category. Powyżej 10 — alert dropped, log to stdout.
    """
    now = time.monotonic()
    cutoff = now - _THROTTLE_WINDOW

    with _throttle_lock:
        bucket = _throttle_buckets.setdefault(category, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= _THROTTLE_LIMIT:
            return False
        bucket.append(now)
    return True


def alert(category: str, message: str, emoji: str = '🛒') -> bool:
    """Send Telegram alert z [HUB] prefix.

    Args:
        category: alert type slug (dla throttle)
        message: HTML message body
        emoji: leading icon (default 🛒)

    Returns: True jeśli wysłano, False jeśli throttled lub send fail.
    """
    if not _check_throttle(category):
        print(f"[sklepakces_telegram] Throttled {category} alert (>{_THROTTLE_LIMIT}/min)")
        return False

    full_message = f"[HUB] {emoji} {message}"
    try:
        return bool(send_telegram(full_message, parse_mode='HTML'))
    except Exception as e:
        print(f"[sklepakces_telegram] send_telegram failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Pre-formatted helpers — używaj w handlers'ach
# ---------------------------------------------------------------------------

def alert_order_received(order_id: int, total: float, customer_name: str = '') -> bool:
    msg = f"<b>New order #{order_id}</b>: {total:.2f} PLN"
    if customer_name:
        msg += f" ({customer_name})"
    return alert('order_received', msg, '🛒')


def alert_product_synced(product_id: int, sku: str, action: str = 'updated') -> bool:
    msg = f"<b>Product {action}</b>: #{product_id} <code>{sku}</code>"
    return alert('product_synced', msg, '📦')


def alert_stock_changed(sku: str, old_qty: int, new_qty: int, reason: str = '') -> bool:
    msg = f"<b>Stock change</b>: <code>{sku}</code> {old_qty} → {new_qty}"
    if reason:
        msg += f" ({reason})"
    return alert('stock_changed', msg, '📊')


def alert_webhook_failed(reason: str, client_ip: str = '') -> bool:
    msg = f"<b>Webhook failed</b>: {reason}"
    if client_ip:
        msg += f" (IP <code>{client_ip}</code>)"
    return alert('webhook_failed', msg, '🚨')


def alert_nonce_replay(client_ip: str = '') -> bool:
    msg = "<b>Nonce replay attempt</b>"
    if client_ip:
        msg += f" (IP <code>{client_ip}</code>)"
    return alert('nonce_replay', msg, '🔁')


def alert_redis_down() -> bool:
    return alert(
        'redis_down',
        '<b>Redis connection lost</b>, using in-memory fallback (single-process replay protection only)',
        '⚠️',
    )


def get_throttle_stats() -> dict:
    """Debug helper — current throttle buckets state."""
    with _throttle_lock:
        return {
            cat: {
                'count': len(bucket),
                'limit': _THROTTLE_LIMIT,
                'window_sec': int(_THROTTLE_WINDOW),
            }
            for cat, bucket in _throttle_buckets.items()
        }
