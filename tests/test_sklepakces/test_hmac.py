"""Sklepakces HMAC unit tests — bez Flask, bez DB.

Verifikuje:
- canonical_string format (must match PHP Akces_Hmac::canonical)
- sign() output (hex 64 chars)
- verify_signature() — valid / wrong secret / old timestamp / empty secret
- PHP↔Python parity (test vector matching PHPUnit Akces_Hmac_Test).
"""
from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from modules.sklepakces_hmac import canonical_string, sign, verify_signature


SECRET = "test_secret_32_chars_minimum_for_realism_xx"


# ---------------------------------------------------------------------------
# canonical_string + sign
# ---------------------------------------------------------------------------

def test_sign_returns_hex_64_chars():
    sig = sign("POST", "/x", 1714400000, "{}", SECRET)
    assert len(sig) == 64
    assert all(c in "0123456789abcdef" for c in sig)


def test_canonical_string_format():
    expected = 'POST:/api/v1/sklepakces/orders:1714400000:{"test":1}'
    actual = canonical_string("POST", "/api/v1/sklepakces/orders", 1714400000, '{"test":1}')
    assert actual == expected


def test_canonical_method_uppercased():
    """canonical zawsze UPPERCASE method — lower input → upper canonical."""
    lower = canonical_string("post", "/x", 1, "{}")
    upper = canonical_string("POST", "/x", 1, "{}")
    assert lower == upper
    assert lower.startswith("POST:")


def test_sign_method_case_insensitive():
    """Lower-case method produces same signature as upper-case."""
    lower = sign("post", "/x", 1, "{}", SECRET)
    upper = sign("POST", "/x", 1, "{}", SECRET)
    assert lower == upper


def test_php_python_parity_reference_vector():
    """Bit-perfect match z PHP Akces_Hmac_Test::test_sign_canonical_string_format.

    Reference vector — gdyby ten test fail'ował, plugin PHP nie mógłby dogadać
    się z Hub'em Pythonowym. Najważniejszy test kontraktu.
    """
    expected = hmac.new(
        SECRET.encode(),
        b'POST:/akces/v1/products:1714400000:{"test":1}',
        hashlib.sha256,
    ).hexdigest()
    actual = sign("POST", "/akces/v1/products", 1714400000, '{"test":1}', SECRET)
    assert actual == expected, (
        f"PHP↔Python parity BROKEN: {actual} != {expected} "
        "— plugin PHP nie zweryfikuje signature z Hub'a Pythonowego"
    )


# ---------------------------------------------------------------------------
# verify_signature
# ---------------------------------------------------------------------------

def test_verify_valid_signature_returns_true():
    now = int(time.time())
    sig = sign("POST", "/x", now, "{}", SECRET)
    ok, err = verify_signature("POST", "/x", now, "{}", sig, SECRET)
    assert ok is True
    assert err == ""


def test_verify_wrong_secret_returns_false():
    now = int(time.time())
    sig = sign("POST", "/x", now, "{}", SECRET)
    ok, err = verify_signature("POST", "/x", now, "{}", sig,
                                "WRONG_SECRET_xxxxxxxxxxxxxxxxxxxxxxx")
    assert ok is False
    assert "mismatch" in err.lower()


def test_verify_old_timestamp_outside_window_rejected():
    # 10 min temu — outside 5-min replay window.
    old_ts = int(time.time()) - 600
    sig = sign("POST", "/x", old_ts, "{}", SECRET)
    ok, err = verify_signature("POST", "/x", old_ts, "{}", sig, SECRET)
    assert ok is False
    assert "window" in err.lower() or "300" in err


def test_verify_future_timestamp_rejected():
    """Clock skew protection — przyszły timestamp >5 min też rejected."""
    future_ts = int(time.time()) + 600
    sig = sign("POST", "/x", future_ts, "{}", SECRET)
    ok, err = verify_signature("POST", "/x", future_ts, "{}", sig, SECRET)
    assert ok is False


def test_verify_empty_secret_rejected():
    ok, err = verify_signature("POST", "/x", int(time.time()), "{}", "anysig", "")
    assert ok is False
    assert "not configured" in err.lower()


def test_verify_body_mismatch_rejected():
    """Signature signed na innym body niż request body → reject."""
    now = int(time.time())
    sig = sign("POST", "/x", now, '{"orig":1}', SECRET)
    ok, err = verify_signature("POST", "/x", now, '{"tampered":2}', sig, SECRET)
    assert ok is False


def test_verify_path_mismatch_rejected():
    """Sig dla /orders nie zweryfikuje się dla /products (anti-replay na inny endpoint)."""
    now = int(time.time())
    sig = sign("POST", "/api/v1/sklepakces/orders", now, "{}", SECRET)
    ok, err = verify_signature("POST", "/api/v1/sklepakces/products",
                                now, "{}", sig, SECRET)
    assert ok is False
