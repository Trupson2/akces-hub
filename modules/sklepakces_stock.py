"""Sklepakces stock handlers — bidirectional inventory.

POST /api/v1/sklepakces/stock_sync (sklepakces → Hub: stock change notification)
GET  /api/v1/sklepakces/inventory/<sku> (Hub → response z current stock dla SKU)

Log każdej zmiany do sklepakces_stock_log (audit trail).
Future Faza 4: cross-channel sync z Magazynier (Allegro + sklep + OLX).
"""
from __future__ import annotations

from flask import request

from modules.api_v1.response import ErrorCodes, error_response, success_response
from modules.database import get_db
from modules.sklepakces_telegram import alert_stock_changed


def handle_stock_sync():
    """POST /stock_sync — record stock change z sklepakces."""
    if not request.is_json:
        return error_response('Content-Type must be application/json',
                              ErrorCodes.INVALID_JSON, 400)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error_response('Body must be JSON object',
                              ErrorCodes.INVALID_JSON, 400)

    required = ('product_id', 'sku', 'new_quantity')
    missing = [f for f in required if f not in payload]
    if missing:
        return error_response(f'Missing fields: {missing}',
                              ErrorCodes.MISSING_FIELD, 400,
                              details={'missing': missing})

    try:
        product_id = int(payload['product_id'])
        new_qty = int(payload['new_quantity'])
        old_qty = int(payload.get('old_quantity') or 0)
    except (TypeError, ValueError):
        return error_response('product_id / new_quantity / old_quantity must be int',
                              ErrorCodes.VALIDATION_ERROR, 400)

    sku = str(payload['sku']).strip()
    reason = str(payload.get('reason', 'unknown'))

    if not sku:
        return error_response('sku cannot be empty',
                              ErrorCodes.VALIDATION_ERROR, 400)
    if new_qty < 0:
        return error_response('new_quantity must be >= 0',
                              ErrorCodes.VALIDATION_ERROR, 400)

    conn = get_db()
    try:
        # Update sklepakces_products (jeśli produkt już istnieje)
        result = conn.execute(
            'UPDATE sklepakces_products SET stock_quantity = ?, '
            ' updated_at = CURRENT_TIMESTAMP WHERE wc_product_id = ?',
            (new_qty, product_id),
        )
        product_existed = result.rowcount > 0

        # Always log to sklepakces_stock_log (audit even jeśli produkt nieznany)
        conn.execute(
            'INSERT INTO sklepakces_stock_log '
            '(wc_product_id, sku, old_quantity, new_quantity, reason) '
            'VALUES (?, ?, ?, ?, ?)',
            (product_id, sku, old_qty, new_qty, reason),
        )
        conn.commit()
    except Exception as e:
        print(f"[sklepakces_stock] DB error: {e}")
        return error_response('Database error',
                              ErrorCodes.DATABASE_ERROR, 500)

    # Telegram alert (non-blocking)
    try:
        alert_stock_changed(sku, old_qty, new_qty, reason)
    except Exception as e:
        print(f"[sklepakces_stock] Telegram alert failed: {e}")

    return success_response({
        'received': True,
        'product_id': product_id,
        'sku': sku,
        'new_quantity': new_qty,
        'product_existed': product_existed,
    })


def handle_inventory_query(sku: str):
    """GET /inventory/<sku> — current stock dla SKU."""
    sku = str(sku).strip()
    if not sku:
        return error_response('sku required',
                              ErrorCodes.VALIDATION_ERROR, 400)

    conn = get_db()
    row = conn.execute(
        'SELECT wc_product_id, sku, stock_quantity, updated_at, created_at '
        'FROM sklepakces_products WHERE sku = ?',
        (sku,),
    ).fetchone()

    if not row:
        return error_response(f'SKU "{sku}" not found',
                              ErrorCodes.NOT_FOUND, 404)

    return success_response({
        'sku': row['sku'],
        'wc_product_id': row['wc_product_id'],
        'stock_quantity': int(row['stock_quantity'] or 0),
        'last_updated': row['updated_at'] or row['created_at'],
    })
