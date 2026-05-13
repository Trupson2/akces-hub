"""Pytest fixtures dla sklepakces integration tests.

Najważniejsze:
- `minimal_app` — Flask test client z izolowaną SQLite (tmp_path) i config secret
- `signed_request` — helper do podpisywania HMAC requestów (same algo co plugin PHP)

Tests nie wymagają running Hub'a ani Redis — używają fallback in-memory.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time

import pytest


SECRET = "test_secret_32_chars_minimum_for_realism_xx"


@pytest.fixture
def minimal_app(tmp_path, monkeypatch):
    """Minimal Flask app z sklepakces_bp + isolated tmp SQLite.

    Yields Flask test client. Po teście — cleanup connection pool i nonce cache.
    """
    db_path = str(tmp_path / "test_akces.db")

    # Initialize SQLite z basic schema (config table + insert hmac_secret).
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('CREATE TABLE config (klucz TEXT PRIMARY KEY, wartosc TEXT)')
    conn.execute("INSERT INTO config VALUES ('sklepakces_hmac_secret', ?)", (SECRET,))
    conn.commit()
    conn.close()

    # Monkeypatch DATABASE path + close existing pool żeby get_db() użyło nowego path.
    from modules import database as db_mod
    monkeypatch.setattr(db_mod, 'DATABASE', db_path)
    db_mod.close_connection_pool()

    # Clear Redis nonce cache (fallback in-memory też).
    from modules import redis_nonce_cache
    redis_nonce_cache.clear_all()

    # Build minimal Flask app z sklepakces_bp.
    from flask import Flask
    from modules.sklepakces_blueprint import sklepakces_bp, init_sklepakces_schema

    app = Flask(__name__)
    app.config['TESTING'] = True
    init_sklepakces_schema()
    app.register_blueprint(sklepakces_bp)

    with app.test_client() as client:
        yield client

    # Cleanup: pool + nonce cache (tmp_path znika po teście).
    db_mod.close_connection_pool()
    redis_nonce_cache.clear_all()


@pytest.fixture
def signed_request(minimal_app):
    """Helper do podpisywania HMAC requestów. Zwraca closure (method, path, payload).

    Usage:
        def test_x(signed_request):
            r = signed_request('POST', '/api/v1/sklepakces/orders', {"order_id": 1, ...})
            assert r.status_code == 200
    """

    def _call(method: str, path: str, payload=None, ts_offset: int = 0):
        body = json.dumps(payload, separators=(",", ":")) if payload is not None else ""
        ts = int(time.time()) + ts_offset
        canonical = f"{method.upper()}:{path}:{ts}:{body}".encode("utf-8")
        sig = hmac.new(SECRET.encode("utf-8"), canonical, hashlib.sha256).hexdigest()

        headers = {
            "X-Akces-Timestamp": str(ts),
            "X-Akces-Signature": sig,
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"

        method_upper = method.upper()
        if method_upper == "POST":
            return minimal_app.post(path, data=body, headers=headers)
        if method_upper == "GET":
            return minimal_app.get(path, headers=headers)
        if method_upper == "PUT":
            return minimal_app.put(path, data=body, headers=headers)
        if method_upper == "DELETE":
            return minimal_app.delete(path, headers=headers)
        raise ValueError(f"Unsupported method: {method}")

    return _call
