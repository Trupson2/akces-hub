"""
Cloudflare Turnstile - opcjonalna ochrona loginu przed botami.

Feature dziala TYLKO gdy obie zmienne srodowiskowe sa ustawione:
  - TURNSTILE_SITE_KEY (public - w HTML)
  - TURNSTILE_SECRET_KEY (server-side - do siteverify)

W przeciwnym razie feature jest disabled (backward compat) - is_enabled()
zwraca False, template renderuje formularz bez widgetu, handler loginu
nie wymaga tokena.

API reference:
  https://developers.cloudflare.com/turnstile/get-started/server-side-validation/
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import requests

SITEVERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'
REQUEST_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class TurnstileResult:
    """Wynik weryfikacji tokena Turnstile."""
    success: bool
    error_codes: tuple = ()
    hostname: str = ''
    action: str = ''


def get_site_key() -> str:
    """Public site key dla widgetu. Pusty string = feature disabled."""
    return os.environ.get('TURNSTILE_SITE_KEY', '').strip()


def get_secret_key() -> str:
    """Server-side secret key. Pusty string = feature disabled."""
    return os.environ.get('TURNSTILE_SECRET_KEY', '').strip()


def is_enabled() -> bool:
    """True tylko gdy OBIE zmienne sa skonfigurowane."""
    return bool(get_site_key() and get_secret_key())


def verify_token(
    token: str,
    remote_ip: Optional[str] = None,
    secret: Optional[str] = None,
) -> TurnstileResult:
    """Waliduje token Turnstile poprzez POST do Cloudflare siteverify.

    Args:
        token: wartosc pola formularza `cf-turnstile-response`.
        remote_ip: IP klienta (opcjonalne, ale rekomendowane).
        secret: server-side secret key (pobierany z env jesli None).

    Returns:
        TurnstileResult z success=True tylko gdy Cloudflare potwierdzi valid.

    UWAGA: ta funkcja jest FAIL-CLOSED — jesli Cloudflare nie odpowie (network
    down, timeout), zwracamy success=False. Caller musi zablokowac login i
    zalogowac incydent (np. przez log_admin_action).
    """
    # Brak tokena -> natychmiastowa porazka, bez network callu.
    if not token:
        return TurnstileResult(success=False, error_codes=('missing-input-response',))

    effective_secret = (secret if secret is not None else get_secret_key()).strip()
    if not effective_secret:
        # Feature wylaczony ale caller o tym nie wiedzial - potraktuj jako error.
        return TurnstileResult(success=False, error_codes=('missing-input-secret',))

    payload = {
        'secret': effective_secret,
        'response': token,
    }
    if remote_ip:
        payload['remoteip'] = remote_ip

    try:
        resp = requests.post(SITEVERIFY_URL, data=payload, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        # Fail-closed przy network error
        return TurnstileResult(
            success=False,
            error_codes=('network-error', str(exc).__class__.__name__),
        )

    if not resp.ok:
        return TurnstileResult(
            success=False,
            error_codes=('http-error', f'status-{resp.status_code}'),
        )

    try:
        data = resp.json()
    except ValueError:
        return TurnstileResult(success=False, error_codes=('invalid-json',))

    success = bool(data.get('success'))
    error_codes = tuple(data.get('error-codes') or ())
    return TurnstileResult(
        success=success,
        error_codes=error_codes,
        hostname=str(data.get('hostname', '')),
        action=str(data.get('action', '')),
    )
