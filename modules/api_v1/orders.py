"""API v1: /api/v1/orders — zamowienia ze zewnetrznego sklepu.

Mapuje do tabeli `sprzedaze`:
    allegro_order_id (tutaj uzywany jako external_order_id),
    produkt_id, nazwa, cena, ilosc, kupujacy, adres, status, data_sprzedazy
"""
from __future__ import annotations

from flask import request

from . import api_v1_bp
from .auth import require_api_v1
from .response import success_response, error_response, paginate, ErrorCodes
from .schemas import OrderCreateSchema, OrderStatusSchema


def _row_to_order(row):
    if row is None:
        return None
    return {
        'id': row['id'],
        'external_order_id': row['allegro_order_id'] or '',
        'product_id': row['produkt_id'],
        'product_name': row['nazwa'] or '',
        'quantity': int(row['ilosc'] or 1),
        'price': float(row['cena'] or 0),
        'buyer': row['kupujacy'] or '',
        'address': row['adres'] or '',
        'status': row['status'] or 'nowa',
        'created_at': row['data_sprzedazy'],
    }


@api_v1_bp.route('/orders', methods=['GET'])
@require_api_v1
def list_orders():
    """GET /api/v1/orders?status=nowa&page=1&per_page=50&from=2026-04-01"""
    from modules.database import get_db

    try:
        page = max(1, int(request.args.get('page', 1)))
        per_page = min(200, max(1, int(request.args.get('per_page', 50))))
    except (ValueError, TypeError):
        return error_response('Invalid pagination params', ErrorCodes.BAD_REQUEST, 400)

    where = ["1=1"]
    params = []
    if request.args.get('status'):
        where.append('status = ?')
        params.append(request.args['status'])
    if request.args.get('from'):
        where.append('data_sprzedazy >= ?')
        params.append(request.args['from'])
    if request.args.get('to'):
        where.append('data_sprzedazy <= ?')
        params.append(request.args['to'])
    if request.args.get('external_order_id'):
        where.append('allegro_order_id = ?')
        params.append(request.args['external_order_id'])

    where_sql = ' AND '.join(where)
    conn = get_db()
    total = conn.execute(
        f'SELECT COUNT(*) as c FROM sprzedaze WHERE {where_sql}', params
    ).fetchone()['c']

    offset = (page - 1) * per_page
    rows = conn.execute(
        f'SELECT * FROM sprzedaze WHERE {where_sql} '
        f'ORDER BY data_sprzedazy DESC LIMIT ? OFFSET ?',
        params + [per_page, offset],
    ).fetchall()
    return success_response(
        [_row_to_order(r) for r in rows],
        meta=paginate(page, per_page, total),
    )


@api_v1_bp.route('/orders/<int:order_id>', methods=['GET'])
@require_api_v1
def get_order(order_id):
    from modules.database import get_db
    conn = get_db()
    row = conn.execute('SELECT * FROM sprzedaze WHERE id = ?', (order_id,)).fetchone()
    if not row:
        return error_response(f'Order {order_id} not found', ErrorCodes.NOT_FOUND, 404)
    return success_response(_row_to_order(row))


@api_v1_bp.route('/orders', methods=['POST'])
@require_api_v1
def create_order():
    """POST /api/v1/orders — rejestruje zewnetrzne zamowienie.

    Triggeruje webhook `order.created` dla wszystkich zarejestrowanych
    subskrybentow tego eventu.
    """
    if not request.is_json:
        return error_response('Content-Type must be application/json',
                              ErrorCodes.INVALID_JSON, 400)
    data, errors = OrderCreateSchema().validate(request.get_json(silent=True))
    if errors:
        return error_response('Validation failed', ErrorCodes.VALIDATION_ERROR, 400,
                              details=errors)

    from modules.database import get_db
    conn = get_db()

    # Jesli podano product_id, sprawdz czy istnieje i pobierz nazwe
    product_name = data.get('product_name', '')
    product_id = data.get('product_id')
    if product_id:
        prod = conn.execute(
            'SELECT id, nazwa FROM produkty WHERE id = ?', (product_id,)
        ).fetchone()
        if not prod:
            return error_response(
                f'Product {product_id} not found',
                ErrorCodes.NOT_FOUND, 404,
            )
        if not product_name:
            product_name = prod['nazwa']

    cur = conn.execute(
        'INSERT INTO sprzedaze (allegro_order_id, produkt_id, nazwa, cena, ilosc,'
        ' kupujacy, adres, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (
            data.get('external_order_id', ''),
            product_id,
            product_name,
            float(data.get('price') or 0),
            int(data.get('quantity') or 1),
            data.get('buyer', ''),
            data.get('address', ''),
            data.get('status', 'nowa'),
        ),
    )
    conn.commit()
    order_id = cur.lastrowid
    row = conn.execute('SELECT * FROM sprzedaze WHERE id = ?', (order_id,)).fetchone()
    payload = _row_to_order(row)

    # Trigger outbound webhook
    try:
        from .webhooks import trigger_webhook_event
        trigger_webhook_event('order.created', payload)
    except Exception:
        pass

    return success_response(payload, status_code=201)


@api_v1_bp.route('/orders/<int:order_id>/status', methods=['PUT'])
@require_api_v1
def update_order_status(order_id):
    """PUT /api/v1/orders/{id}/status body {"status": "wyslana"}."""
    if not request.is_json:
        return error_response('Content-Type must be application/json',
                              ErrorCodes.INVALID_JSON, 400)
    data, errors = OrderStatusSchema().validate(request.get_json(silent=True))
    if errors:
        return error_response('Validation failed', ErrorCodes.VALIDATION_ERROR, 400,
                              details=errors)

    from modules.database import get_db
    conn = get_db()
    existing = conn.execute('SELECT status FROM sprzedaze WHERE id = ?', (order_id,)).fetchone()
    if not existing:
        return error_response(f'Order {order_id} not found', ErrorCodes.NOT_FOUND, 404)

    new_status = data['status']
    old_status = existing['status']
    conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', (new_status, order_id))
    conn.commit()

    row = conn.execute('SELECT * FROM sprzedaze WHERE id = ?', (order_id,)).fetchone()
    payload = _row_to_order(row)

    try:
        from .webhooks import trigger_webhook_event
        trigger_webhook_event('order.status_changed', {
            **payload,
            'old_status': old_status,
            'new_status': new_status,
        })
        if new_status in ('wyslana',):
            trigger_webhook_event('sale.completed', payload)
        if new_status == 'zwrot':
            trigger_webhook_event('return.received', payload)
    except Exception:
        pass

    return success_response(payload)


@api_v1_bp.route('/orders/<int:order_id>', methods=['DELETE'])
@require_api_v1
def cancel_order(order_id):
    """DELETE /api/v1/orders/{id} — soft delete (status='anulowana')."""
    from modules.database import get_db
    conn = get_db()
    existing = conn.execute('SELECT id FROM sprzedaze WHERE id = ?', (order_id,)).fetchone()
    if not existing:
        return error_response(f'Order {order_id} not found', ErrorCodes.NOT_FOUND, 404)
    conn.execute("UPDATE sprzedaze SET status = 'anulowana' WHERE id = ?", (order_id,))
    conn.commit()
    return success_response({'id': order_id, 'status': 'anulowana'})
