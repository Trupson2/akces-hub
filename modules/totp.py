"""
TOTP 2FA dla AKCES Hub (opt-in).

Dependencies: pyotp (TOTP RFC 6238), qrcode (SVG QR generation), bcrypt (hash backup codes).

Public API:
    generate_secret()            -> base32 string (pyotp default: 32 znaki)
    generate_qr_uri(user, secret) -> otpauth://totp URL dla QR codu
    generate_qr_svg(user, secret) -> inline SVG (string) z QR kodem
    verify_code(secret, code, window=1) -> bool (accept current + prev + next 30s window)
    generate_backup_codes(n=8)   -> (plain_list, hashed_json) gdzie hashed_json to JSON-encoded lista bcrypt hasheow
    verify_backup_code(hashed_json, provided) -> (bool, new_hashed_json) — jesli match, zwraca JSON bez uzytego kodu

Backup codes format: XXXX-XXXX (8 znakow na segment + myslnik; bajty hex upper).
"""

from __future__ import annotations

import json
import secrets as _secrets
from typing import List, Tuple

import bcrypt
import pyotp
import qrcode
import qrcode.image.svg


DEFAULT_ISSUER = 'AKCES Hub'
TOTP_DIGITS = 6
TOTP_INTERVAL = 30  # seconds
# Backup code format
BACKUP_CODE_SEGMENTS = 2
BACKUP_CODE_SEGMENT_LEN = 4
DEFAULT_BACKUP_COUNT = 8


def generate_secret() -> str:
    """Zwraca nowy base32 secret (32 znaki) kompatybilny z Google Authenticator."""
    return pyotp.random_base32()


def generate_qr_uri(username: str, secret: str, issuer: str = DEFAULT_ISSUER) -> str:
    """Tworzy otpauth://totp URL do wczytania w aplikacji authenticator."""
    totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL)
    return totp.provisioning_uri(name=username, issuer_name=issuer)


def generate_qr_svg(username: str, secret: str, issuer: str = DEFAULT_ISSUER) -> str:
    """Zwraca inline SVG (string) z QR kodem zawierajacym otpauth URI.

    SVG nie potrzebuje pliku ani zewnetrznego CDN — idealne do inline
    w template bez naruszania CSP (qrcode.image.svg.SvgPathImage uzywa
    <path>, nie <script>/<img src="data:">).
    """
    uri = generate_qr_uri(username, secret, issuer=issuer)
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(uri, image_factory=factory, box_size=8, border=2)
    # img.to_string() zwraca bytes — dekodujemy do str
    return img.to_string(encoding='unicode')


def verify_code(secret: str, code: str, window: int = 1) -> bool:
    """Sprawdz czy `code` pasuje do secret'u (w oknie +/- window*30s).

    Args:
        secret: base32 TOTP secret
        code: 6-cyfrowy string z aplikacji authenticator
        window: ile +/- 30-sekundowych okien akceptujemy (domyslnie 1 = +/- 30s)

    Returns:
        True jesli kod valid, False w przeciwnym razie.
        False rowniez gdy code ma zly format (sanity check przed pyotp).
    """
    if not secret or not code:
        return False
    code_str = str(code).strip()
    if not code_str.isdigit() or len(code_str) != TOTP_DIGITS:
        return False
    try:
        totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL)
        return totp.verify(code_str, valid_window=window)
    except Exception:
        return False


# =====================================================================
# Backup codes
# =====================================================================

def _format_code(hex_bytes: str) -> str:
    """Formatuje hex do XXXX-XXXX (uppercase)."""
    parts = []
    pos = 0
    for _ in range(BACKUP_CODE_SEGMENTS):
        parts.append(hex_bytes[pos:pos + BACKUP_CODE_SEGMENT_LEN].upper())
        pos += BACKUP_CODE_SEGMENT_LEN
    return '-'.join(parts)


def generate_backup_codes(n: int = DEFAULT_BACKUP_COUNT) -> Tuple[List[str], str]:
    """Generuje `n` backup codes.

    Returns:
        (plain_codes, hashed_json) gdzie:
          - plain_codes: lista kodow w formacie XXXX-XXXX do pokazania userowi RAZ
          - hashed_json: JSON-encoded lista bcrypt hasheow (do zapisu w bazie)
    """
    if n <= 0:
        return ([], json.dumps([]))
    total_hex_len = BACKUP_CODE_SEGMENTS * BACKUP_CODE_SEGMENT_LEN
    plain_codes: List[str] = []
    hashes: List[str] = []
    for _ in range(n):
        random_hex = _secrets.token_hex(total_hex_len // 2)  # 2 hex chars per byte
        code = _format_code(random_hex)
        plain_codes.append(code)
        # bcrypt hash — usun myslnik przed hashem dla konsystencji
        hashed = bcrypt.hashpw(code.encode('utf-8'), bcrypt.gensalt(rounds=10)).decode('utf-8')
        hashes.append(hashed)
    return plain_codes, json.dumps(hashes)


def verify_backup_code(hashed_json: str, provided: str) -> Tuple[bool, str]:
    """Sprawdz czy `provided` pasuje do ktoregokolwiek hash'u z listy.

    Args:
        hashed_json: JSON-encoded lista bcrypt hasheow (lub ''/None)
        provided: kod wpisany przez usera (XXXX-XXXX lub xxxxxxxx)

    Returns:
        (matched, new_hashed_json) gdzie:
          - matched: True jesli kod valid
          - new_hashed_json: JSON-encoded lista BEZ uzytego kodu (single-use)
            (przy matched=False zwraca niezmodyfikowana liste)
    """
    if not hashed_json or not provided:
        return (False, hashed_json or json.dumps([]))

    # Normalizuj: uppercase, akceptuj zarowno XXXX-XXXX jak i XXXXXXXX
    normalized = provided.strip().upper().replace(' ', '')
    if '-' not in normalized and len(normalized) == BACKUP_CODE_SEGMENTS * BACKUP_CODE_SEGMENT_LEN:
        # user wpisal bez myslnika — dodaj go
        mid = BACKUP_CODE_SEGMENT_LEN
        normalized = f'{normalized[:mid]}-{normalized[mid:]}'

    try:
        hashes = json.loads(hashed_json)
        if not isinstance(hashes, list):
            return (False, hashed_json)
    except (json.JSONDecodeError, TypeError):
        return (False, hashed_json)

    for i, h in enumerate(hashes):
        try:
            if bcrypt.checkpw(normalized.encode('utf-8'), h.encode('utf-8')):
                # Match — usun hash z listy (single-use)
                remaining = hashes[:i] + hashes[i + 1:]
                return (True, json.dumps(remaining))
        except (ValueError, TypeError):
            continue

    return (False, hashed_json)


def backup_codes_remaining(hashed_json: str) -> int:
    """Zwraca ilosc pozostalych (nieuzytych) backup codes."""
    if not hashed_json:
        return 0
    try:
        parsed = json.loads(hashed_json)
        return len(parsed) if isinstance(parsed, list) else 0
    except (json.JSONDecodeError, TypeError):
        return 0
