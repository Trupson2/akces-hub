"""API v1: /api/v1/stock — operacje magazynowe.

GET /api/v1/stock              — overview: sumy stock per category
GET /api/v1/stock/{id}         — konkretny produkt (alias dla products/{id}/stock)
POST /api/v1/stock/adjust      — body {product_id, delta, reason}
"""
from __future__ import annotations

from flask import request

from . import api_v1_bp
from .auth import require_api_v1
from .response import success_response, error_response, ErrorCodes
from .schemas import StockAdjustSchema


@api_v1_bp.route('/stock', methods=['GET'])
@require_api_v1
def stock_overview():
    """GET /api/v1/stock — sumy stock per kategoria + calkowita."""
    from modules.database import get_db
    conn = get_db()
    rows = conn.execute(
        'SELECT COALESCE(kategoria, "inne") as category, '
        ' SUM(ilosc) as total_stock, COUNT(*) as product_count '
        'FROM produkty WHERE status != "deleted" '
        'GROUP BY kategoria ORDER BY total_stock DESC'
    ).fetchall()
    total = conn.execute(
        'SELECT SUM(ilosc) as t FROM produkty WHERE status != "deleted"'
    ).fetchone()['t'] or 0
    return success_response({
        'total_stock': int(total),
        'by_category': [
            {
                'category': r['category'] or 'inne',
                'stock': int(r['total_stock'] or 0),
                'product_count': int(r['product_count'] or 0),
            }
            for r in rows
        ],
    })


@api_v1_bp.route('/stock/<int:product_id>', methods=['GET'])
@require_api_v1
def stock_for_product(product_id):
    """GET /api/v1/stock/{product_id}."""
    from modules.database import get_db
    conn = get_db()
    row = conn.execute(
        'SELECT id, nazwa, ilosc FROM produkty WHERE id = ?', (product_id,)
    ).fetchone()
    if not row:
        return error_response(f'Product {product_id} not found', ErrorCodes.NOT_FOUND, 404)
    return success_response({
        'product_id': row['id'],
        'name': row['nazwa'],
        'stock': int(row['ilosc'] or 0),
    })


@api_v1_bp.route('/stock/adjust', methods=['POST'])
@require_api_v1
def adjust_stock():
    """POST /api/v1/stock/adjust body {product_id, delta, reason}.

    delta moze byc ujemne (damaged/lost) lub dodatnie (restock, przywrocenie ze zwrotu).
    """
    if not request.is_json:
        return error_response('Content-Type must be application/json',
                              ErrorCodes.INVALID_JSON, 400)
    data, errors = StockAdjustSchema().validate(request.get_json(silent=True))
    if errors:
        return error_response('Validation failed', ErrorCodes.VALIDATION_ERROR, 400,
                              details=errors)

    from modules.database import get_db
    conn = get_db()
    row = conn.execute(
        'SELECT id, ilosc FROM produkty WHERE id = ?', (data['product_id'],)
    ).fetchone()
    if not row:
        return error_response(f'Product {data["product_id"]} not found',
                              ErrorCodes.NOT_FOUND, 404)

    current = int(row['ilosc'] or 0)
    new_stock = max(0, current + int(data['delta']))
    conn.execute('UPDATE produkty SET ilosc = ? WHERE id = ?', (new_stock, row['id']))
    conn.commit()

    # Trigger stock webhooks
    try:
        from .webhooks import trigger_webhook_event
        if new_stock == 0:
            trigger_webhook_event('product.stock_zero', {
                'product_id': row['id'], 'stock': 0, 'reason': data.get('reason', '')})
        elif new_stock <= 2:
            trigger_webhook_event('product.stock_low', {
                'product_id': row['id'], 'stock': new_stock,
                'reason': data.get('reason', '')})
    except Exception:
        pass

    return success_response({
        'product_id': row['id'],
        'previous_stock': current,
        'new_stock': new_stock,
        'delta': int(data['delta']),
        'reason': data.get('reason', ''),
    })
