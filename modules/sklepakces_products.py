"""Sklepakces product webhook handler — POST /api/v1/sklepakces/products.

Plugin notifies Hub o product create/update (event=product.created lub product.updated).
INSERT or UPDATE do `sklepakces_products` — idempotency via UNIQUE constraint
na `wc_product_id`. Telegram alert `product_synced`.

Future Faza 4: po insert, triggeruj sync do Allegro przez existing
modules/allegro_api (build_offer_parameters + listing publish).
"""
from __future__ import annotations

import json

from flask import request

from modules.api_v1.response import ErrorCodes, error_response, success_response
from modules.database import get_db
from modules.sklepakces_telegram import alert_product_synced


_REQUIRED_FIELDS = ('product_id', 'sku', 'name')


def handle_product_webhook():
    """POST /api/v1/sklepakces/products handler."""
    if not request.is_json:
        return error_response('Content-Type must be application/json',
                              ErrorCodes.INVALID_JSON, 400)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error_response('Body must be JSON object',
                              ErrorCodes.INVALID_JSON, 400)

    missing = [f for f in _REQUIRED_FIELDS if f not in payload]
    if missing:
        return error_response(f'Missing fields: {missing}',
                              ErrorCodes.MISSING_FIELD, 400,
                              details={'missing': missing})

    try:
        product_id = int(payload['product_id'])
    except (TypeError, ValueError):
        return error_response('product_id must be int',
                              ErrorCodes.VALIDATION_ERROR, 400)
    sku = str(payload['sku']).strip()
    name = str(payload['name']).strip()

    if not sku or not name:
        return error_response('sku and name cannot be empty',
                              ErrorCodes.VALIDATION_ERROR, 400)

    # Idempotency: check existing
    conn = get_db()
    try:
        existing = conn.execute(
            'SELECT id FROM sklepakces_products WHERE wc_product_id = ?',
            (product_id,),
        ).fetchone()

        gpsr_data = json.dumps(payload.get('gpsr', {}), ensure_ascii=False,
                               default=str)
        product_data_json = json.dumps(payload, ensure_ascii=False, default=str)

        # Sale price może być None — odrózniamy "nie podano" od "0"
        sale_price = payload.get('sale_price')
        sale_price_val = float(sale_price) if sale_price not in (None, '', 0) else None

        if existing:
            conn.execute(
                'UPDATE sklepakces_products SET '
                ' sku = ?, name = ?, regular_price = ?, sale_price = ?, '
                ' stock_quantity = ?, product_data = ?, gpsr_data = ?, '
                ' updated_at = CURRENT_TIMESTAMP '
                'WHERE wc_product_id = ?',
                (
                    sku, name,
                    float(payload.get('regular_price') or 0),
                    sale_price_val,
                    int(payload.get('stock_quantity') or 0),
                    product_data_json,
                    gpsr_data,
                    product_id,
                ),
            )
            conn.commit()
            hub_internal_id = existing['id']
            action = 'updated'
        else:
            cur = conn.execute(
                'INSERT INTO sklepakces_products '
                '(wc_product_id, sku, name, regular_price, sale_price, '
                ' stock_quantity, product_data, gpsr_data) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    product_id, sku, name,
                    float(payload.get('regular_price') or 0),
                    sale_price_val,
                    int(payload.get('stock_quantity') or 0),
                    product_data_json,
                    gpsr_data,
                ),
            )
            conn.commit()
            hub_internal_id = cur.lastrowid
            action = 'created'
    except Exception as e:
        print(f"[sklepakces_products] DB error: {e}")
        return error_response('Database error',
                              ErrorCodes.DATABASE_ERROR, 500)

    # Telegram alert (non-blocking)
    try:
        alert_product_synced(product_id, sku, action)
    except Exception as e:
        print(f"[sklepakces_products] Telegram alert failed: {e}")

    return success_response({
        'received': True,
        'product_id': product_id,
        'hub_internal_id': hub_internal_id,
        'action': action,
    })
