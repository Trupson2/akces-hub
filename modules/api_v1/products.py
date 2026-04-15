"""API v1: /api/v1/products endpoints.

Mapuje do istniejacej tabeli `produkty` (modules.database). Transformujemy
wewnetrzne kolumny (ilosc, cena_netto, cena_brutto, nazwa) na konsekwentny
JSON API schema:

    {
      "id": 123,
      "ean": "...",
      "name": "...",
      "description": "...",
      "price": {"net": 100.0, "gross": 123.0, "currency": "PLN"},
      "stock": 5,
      "category": "...",
      "location": "...",
      "status": "magazyn",
      "parameters": {...},
      "created_at": "...",
      "updated_at": "..."
    }
"""
from __future__ import annotations

import json
from flask import g, request

from . import api_v1_bp
from .auth import require_api_v1
from .response import success_response, error_response, paginate, ErrorCodes
from .schemas import ProductCreateSchema, ProductUpdateSchema


def _row_to_product(row):
    """Konwertuje Row z produkty na API JSON."""
    if row is None:
        return None
    try:
        params = json.loads(row['parameters']) if row['parameters'] else {}
    except Exception:
        params = {}
    return {
        'id': row['id'],
        'ean': row['ean'] or '',
        'asin': row['asin'] or '',
        'name': row['nazwa'],
        'description': row['opis_ai'] or '',
        'price': {
            'net': float(row['cena_netto'] or 0),
            'gross': float(row['cena_brutto'] or 0),
            'currency': 'PLN',
        },
        'stock': int(row['ilosc'] or 0),
        'category': row['kategoria'] or '',
        'location': row['lokalizacja'] or '',
        'status': row['status'] or 'magazyn',
        'parameters': params,
        'created_at': row['data_dodania'],
        'updated_at': row['data_dodania'],  # brak osobnej kolumny updated_at
    }


@api_v1_bp.route('/products', methods=['GET'])
@require_api_v1
def list_products():
    """GET /api/v1/products?page=1&per_page=50&status=magazyn&category=laptopy&search=...

    Query params:
      page         integer >= 1 (default 1)
      per_page     integer 1-200 (default 50)
      status       filtr po produkty.status
      category     filtr po produkty.kategoria
      ean          dokladne dopasowanie EAN
      search       LIKE %search% w nazwa/opis
    """
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
    if request.args.get('category'):
        where.append('kategoria = ?')
        params.append(request.args['category'])
    if request.args.get('ean'):
        where.append('ean = ?')
        params.append(request.args['ean'])
    if request.args.get('search'):
        where.append('(nazwa LIKE ? OR opis_ai LIKE ?)')
        q = f"%{request.args['search']}%"
        params.extend([q, q])

    where_sql = ' AND '.join(where)
    conn = get_db()
    total = conn.execute(
        f'SELECT COUNT(*) as c FROM produkty WHERE {where_sql}', params
    ).fetchone()['c']

    offset = (page - 1) * per_page
    rows = conn.execute(
        f'SELECT * FROM produkty WHERE {where_sql} '
        f'ORDER BY data_dodania DESC LIMIT ? OFFSET ?',
        params + [per_page, offset],
    ).fetchall()

    data = [_row_to_product(r) for r in rows]
    return success_response(data, meta=paginate(page, per_page, total))


@api_v1_bp.route('/products/<int:product_id>', methods=['GET'])
@require_api_v1
def get_product(product_id):
    """GET /api/v1/products/{id}."""
    from modules.database import get_db
    conn = get_db()
    row = conn.execute('SELECT * FROM produkty WHERE id = ?', (product_id,)).fetchone()
    if not row:
        return error_response(f'Product {product_id} not found', ErrorCodes.NOT_FOUND, 404)
    return success_response(_row_to_product(row))


@api_v1_bp.route('/products/<int:product_id>/stock', methods=['GET'])
@require_api_v1
def get_product_stock(product_id):
    """GET /api/v1/products/{id}/stock. Returns {product_id, stock}."""
    from modules.database import get_db
    conn = get_db()
    row = conn.execute(
        'SELECT id, ilosc FROM produkty WHERE id = ?', (product_id,)
    ).fetchone()
    if not row:
        return error_response(f'Product {product_id} not found', ErrorCodes.NOT_FOUND, 404)
    return success_response({
        'product_id': row['id'],
        'stock': int(row['ilosc'] or 0),
    })


@api_v1_bp.route('/products', methods=['POST'])
@require_api_v1
def create_product():
    """POST /api/v1/products — tworzy nowy produkt."""
    if not request.is_json:
        return error_response('Content-Type must be application/json',
                              ErrorCodes.INVALID_JSON, 400)
    data, errors = ProductCreateSchema().validate(request.get_json(silent=True))
    if errors:
        return error_response('Validation failed', ErrorCodes.VALIDATION_ERROR, 400,
                              details=errors)

    from modules.database import get_db
    conn = get_db()
    cur = conn.execute(
        'INSERT INTO produkty (ean, asin, nazwa, opis_ai, ilosc, cena_netto, '
        ' cena_brutto, kategoria, lokalizacja, status) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            data.get('ean', ''),
            data.get('asin', ''),
            data.get('name'),
            data.get('description', ''),
            int(data.get('stock') or 0),
            float(data.get('price_net') or 0),
            float(data.get('price_gross') or 0),
            data.get('category', 'inne'),
            data.get('location', ''),
            data.get('status', 'magazyn'),
        ),
    )
    conn.commit()
    new_id = cur.lastrowid
    row = conn.execute('SELECT * FROM produkty WHERE id = ?', (new_id,)).fetchone()
    return success_response(_row_to_product(row), status_code=201)


@api_v1_bp.route('/products/<int:product_id>', methods=['PUT'])
@require_api_v1
def update_product(product_id):
    """PUT /api/v1/products/{id} — partial update (PATCH-like)."""
    if not request.is_json:
        return error_response('Content-Type must be application/json',
                              ErrorCodes.INVALID_JSON, 400)
    data, errors = ProductUpdateSchema().validate(request.get_json(silent=True))
    if errors:
        return error_response('Validation failed', ErrorCodes.VALIDATION_ERROR, 400,
                              details=errors)

    from modules.database import get_db
    conn = get_db()
    existing = conn.execute('SELECT id FROM produkty WHERE id = ?', (product_id,)).fetchone()
    if not existing:
        return error_response(f'Product {product_id} not found', ErrorCodes.NOT_FOUND, 404)

    mapping = {
        'ean': 'ean', 'name': 'nazwa', 'description': 'opis_ai',
        'price_net': 'cena_netto', 'price_gross': 'cena_brutto',
        'stock': 'ilosc', 'category': 'kategoria',
        'location': 'lokalizacja', 'status': 'status',
    }
    sets = []
    args = []
    for api_name, db_name in mapping.items():
        if api_name in data:
            sets.append(f'{db_name} = ?')
            args.append(data[api_name])
    if not sets:
        # Nic nie zmieniono — zwroc aktualny stan
        row = conn.execute('SELECT * FROM produkty WHERE id = ?', (product_id,)).fetchone()
        return success_response(_row_to_product(row))
    args.append(product_id)
    conn.execute(f'UPDATE produkty SET {", ".join(sets)} WHERE id = ?', args)
    conn.commit()

    # Trigger stock.low / stock.zero webhooki jesli stock sie zmienil
    if 'stock' in data:
        try:
            from .webhooks import trigger_webhook_event
            stock_val = int(data['stock'])
            if stock_val == 0:
                trigger_webhook_event('product.stock_zero', {'product_id': product_id, 'stock': 0})
            elif stock_val <= 2:
                trigger_webhook_event('product.stock_low', {'product_id': product_id, 'stock': stock_val})
        except Exception:
            pass

    row = conn.execute('SELECT * FROM produkty WHERE id = ?', (product_id,)).fetchone()
    return success_response(_row_to_product(row))


@api_v1_bp.route('/products/<int:product_id>', methods=['DELETE'])
@require_api_v1
def delete_product(product_id):
    """DELETE /api/v1/products/{id} — soft delete (status='deleted')."""
    from modules.database import get_db
    conn = get_db()
    existing = conn.execute('SELECT id FROM produkty WHERE id = ?', (product_id,)).fetchone()
    if not existing:
        return error_response(f'Product {product_id} not found', ErrorCodes.NOT_FOUND, 404)
    conn.execute("UPDATE produkty SET status = 'deleted' WHERE id = ?", (product_id,))
    conn.commit()
    return success_response({'id': product_id, 'status': 'deleted'})
