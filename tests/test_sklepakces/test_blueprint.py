"""Sklepakces Blueprint E2E tests — Flask test client + isolated SQLite.

Wszystkie 5 endpointów + 3-warstwowa walidacja HMAC (signature/timestamp/nonce):

  GET  /api/v1/sklepakces/health             (no HMAC — diagnostic)
  POST /api/v1/sklepakces/orders             (full flow + DB insert)
  POST /api/v1/sklepakces/products           (create + update idempotency)
  POST /api/v1/sklepakces/stock_sync         (audit log)
  GET  /api/v1/sklepakces/inventory/<sku>    (404 / 200)

Plus error paths:
  - missing HMAC headers → 401
  - wrong signature → 401
  - timestamp >5 min → 401
  - replay same signature → 403
  - missing required field → 400
"""
from __future__ import annotations

import json
import sqlite3

import pytest


# ---------------------------------------------------------------------------
# /health (no HMAC)
# ---------------------------------------------------------------------------

def test_health_endpoint_returns_ok_no_hmac(minimal_app):
    r = minimal_app.get("/api/v1/sklepakces/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "success"
    assert data["data"]["service"] == "akces-hub-sklepakces"
    assert data["data"]["ok"] is True
    assert "redis" in data["data"]


# ---------------------------------------------------------------------------
# /orders — HMAC auth + payload validation
# ---------------------------------------------------------------------------

def _valid_order_payload():
    return {
        "order_id": 42,
        "order_number": "1042",
        "total": 99.99,
        "currency": "PLN",
        "status": "processing",
        "customer": {
            "email": "test@example.com",
            "first_name": "Jan",
            "last_name": "Kowalski",
        },
        "items": [{
            "product_id": 1,
            "sku": "PAL-001",
            "name": "Test paleta",
            "quantity": 1,
            "price": 99.99,
        }],
        "payment": {"method": "p24"},
        "metadata": {"_wants_invoice": "no"},
    }


def test_orders_endpoint_rejects_missing_hmac(minimal_app):
    r = minimal_app.post(
        "/api/v1/sklepakces/orders",
        data=json.dumps(_valid_order_payload()),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 401


def test_orders_endpoint_rejects_wrong_signature(minimal_app):
    import time
    r = minimal_app.post(
        "/api/v1/sklepakces/orders",
        data=json.dumps(_valid_order_payload()),
        headers={
            "Content-Type": "application/json",
            "X-Akces-Timestamp": str(int(time.time())),
            "X-Akces-Signature": "0" * 64,
        },
    )
    assert r.status_code == 401
    assert "mismatch" in r.get_data(as_text=True).lower() or "invalid" in r.get_data(as_text=True).lower()


def test_orders_endpoint_rejects_old_timestamp(signed_request):
    r = signed_request("POST", "/api/v1/sklepakces/orders",
                       _valid_order_payload(), ts_offset=-600)
    assert r.status_code == 401


def test_orders_endpoint_accepts_valid_signed_request(signed_request):
    r = signed_request("POST", "/api/v1/sklepakces/orders", _valid_order_payload())
    assert r.status_code == 200, f"Body: {r.get_data(as_text=True)}"
    data = r.get_json()
    assert data["status"] == "success"
    assert data["data"]["received"] is True
    assert data["data"]["order_id"] == 42
    assert data["data"]["hub_internal_id"] > 0


def test_orders_endpoint_rejects_replay(signed_request, minimal_app):
    """Sam podpis 2× → 403 (nonce cache layer 3)."""
    import hashlib
    import hmac as hmac_mod
    import time

    # Manually build identyczny request 2× (signed_request używa różnego ts za każdym razem)
    SECRET = "test_secret_32_chars_minimum_for_realism_xx"
    payload = _valid_order_payload()
    payload["order_id"] = 999  # unique żeby nie kolidować z innymi testami
    body = json.dumps(payload, separators=(",", ":"))
    ts = int(time.time())
    canonical = f"POST:/api/v1/sklepakces/orders:{ts}:{body}".encode("utf-8")
    sig = hmac_mod.new(SECRET.encode(), canonical, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Akces-Timestamp": str(ts),
        "X-Akces-Signature": sig,
    }

    r1 = minimal_app.post("/api/v1/sklepakces/orders", data=body, headers=headers)
    assert r1.status_code == 200, f"1st call: {r1.get_data(as_text=True)}"

    r2 = minimal_app.post("/api/v1/sklepakces/orders", data=body, headers=headers)
    assert r2.status_code == 403, f"Replay expected 403, got {r2.status_code}: {r2.get_data(as_text=True)}"


def test_orders_endpoint_rejects_missing_required_field(signed_request):
    payload = _valid_order_payload()
    del payload["customer"]
    r = signed_request("POST", "/api/v1/sklepakces/orders", payload)
    assert r.status_code == 400


def test_orders_endpoint_rejects_empty_items(signed_request):
    payload = _valid_order_payload()
    payload["items"] = []
    r = signed_request("POST", "/api/v1/sklepakces/orders", payload)
    assert r.status_code == 400


def test_orders_endpoint_rejects_invalid_customer_email(signed_request):
    payload = _valid_order_payload()
    payload["customer"] = {"first_name": "X"}  # brak email
    r = signed_request("POST", "/api/v1/sklepakces/orders", payload)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /products
# ---------------------------------------------------------------------------

def test_products_endpoint_creates_record(signed_request):
    payload = {
        "event": "product.created",
        "product_id": 100,
        "sku": "PAL-100",
        "name": "Test paleta #100",
        "regular_price": 599.99,
        "stock_quantity": 1,
    }
    r = signed_request("POST", "/api/v1/sklepakces/products", payload)
    assert r.status_code == 200, f"Body: {r.get_data(as_text=True)}"
    data = r.get_json()
    assert data["data"]["action"] == "created"
    assert data["data"]["product_id"] == 100


def test_products_endpoint_updates_existing_idempotent(signed_request):
    """Sam product_id 2× → 1st CREATED, 2nd UPDATED (UNIQUE wc_product_id)."""
    payload = {
        "product_id": 200,
        "sku": "PAL-200",
        "name": "First name",
        "regular_price": 100.00,
    }
    r1 = signed_request("POST", "/api/v1/sklepakces/products", payload)
    assert r1.status_code == 200
    assert r1.get_json()["data"]["action"] == "created"

    payload["name"] = "Updated name"
    payload["regular_price"] = 150.00
    r2 = signed_request("POST", "/api/v1/sklepakces/products", payload)
    assert r2.status_code == 200
    assert r2.get_json()["data"]["action"] == "updated"
    # hub_internal_id should be same (same row)
    assert r1.get_json()["data"]["hub_internal_id"] == r2.get_json()["data"]["hub_internal_id"]


def test_products_endpoint_missing_sku_rejected(signed_request):
    r = signed_request("POST", "/api/v1/sklepakces/products",
                       {"product_id": 300, "name": "no sku"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /stock_sync + /inventory
# ---------------------------------------------------------------------------

def test_stock_sync_logs_change(signed_request):
    # First create product żeby UPDATE miał co aktualizować.
    signed_request("POST", "/api/v1/sklepakces/products", {
        "product_id": 500,
        "sku": "PAL-500",
        "name": "Test 500",
        "stock_quantity": 5,
    })

    r = signed_request("POST", "/api/v1/sklepakces/stock_sync", {
        "product_id": 500,
        "sku": "PAL-500",
        "old_quantity": 5,
        "new_quantity": 4,
        "reason": "order_completed",
    })
    assert r.status_code == 200
    data = r.get_json()
    assert data["data"]["new_quantity"] == 4
    assert data["data"]["product_existed"] is True


def test_stock_sync_rejects_negative_quantity(signed_request):
    r = signed_request("POST", "/api/v1/sklepakces/stock_sync", {
        "product_id": 1, "sku": "X", "new_quantity": -1,
    })
    assert r.status_code == 400


def test_inventory_endpoint_returns_404_for_unknown_sku(signed_request):
    r = signed_request("GET", "/api/v1/sklepakces/inventory/UNKNOWN-XYZ", None)
    assert r.status_code == 404


def test_inventory_endpoint_returns_stock_for_known_sku(signed_request):
    # Create product
    signed_request("POST", "/api/v1/sklepakces/products", {
        "product_id": 700,
        "sku": "PAL-700",
        "name": "Inventory test",
        "stock_quantity": 3,
    })

    r = signed_request("GET", "/api/v1/sklepakces/inventory/PAL-700", None)
    assert r.status_code == 200
    data = r.get_json()
    assert data["data"]["sku"] == "PAL-700"
    assert data["data"]["stock_quantity"] == 3
    assert data["data"]["wc_product_id"] == 700
