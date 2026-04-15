"""API v1: /api/v1/pallets — palety zwrotne (core biznesu AKCES HUB).

Mapuje do tabeli `palety`: id, nazwa, dostawca, cena_zakupu, ilosc_produktow,
data_zakupu, notatki, regal, data_dodania, typ, dostarczona.
"""
from __future__ import annotations

from flask import request

from . import api_v1_bp
from .auth import require_api_v1
from .response import success_response, error_response, paginate, ErrorCodes
from .schemas import PalletCreateSchema


def _row_to_pallet(row):
    if row is None:
        return None
    return {
        'id': row['id'],
        'name': row['nazwa'] or '',
        'supplier': row['dostawca'] or '',
        'purchase_price': float(row['cena_zakupu'] or 0),
        'product_count': int(row['ilosc_produktow'] or 0),
        'purchase_date': row['data_zakupu'],
        'notes': row['notatki'] or '',
        'location': row['regal'] or '',
        'type': row['typ'] if 'typ' in row.keys() else 'paleta',
        'delivered': bool(row['dostarczona']) if 'dostarczona' in row.keys() and row['dostarczona'] is not None else False,
        'created_at': row['data_dodania'],
    }


@api_v1_bp.route('/pallets', methods=['GET'])
@require_api_v1
def list_pallets():
    """GET /api/v1/pallets?page=1&per_page=50&supplier=...&from=...&to=..."""
    from modules.database import get_db

    try:
        page = max(1, int(request.args.get('page', 1)))
        per_page = min(200, max(1, int(request.args.get('per_page', 50))))
    except (ValueError, TypeError):
        return error_response('Invalid pagination params', ErrorCodes.BAD_REQUEST, 400)

    where = ['1=1']
    params = []
    if request.args.get('supplier'):
        where.append('dostawca = ?')
        params.append(request.args['supplier'])
    if request.args.get('from'):
        where.append('data_zakupu >= ?')
        params.append(request.args['from'])
    if request.args.get('to'):
        where.append('data_zakupu <= ?')
        params.append(request.args['to'])

    where_sql = ' AND '.join(where)
    conn = get_db()
    total = conn.execute(
        f'SELECT COUNT(*) as c FROM palety WHERE {where_sql}', params
    ).fetchone()['c']

    offset = (page - 1) * per_page
    rows = conn.execute(
        f'SELECT * FROM palety WHERE {where_sql} '
        f'ORDER BY data_dodania DESC LIMIT ? OFFSET ?',
        params + [per_page, offset],
    ).fetchall()
    return success_response(
        [_row_to_pallet(r) for r in rows],
        meta=paginate(page, per_page, total),
    )


@api_v1_bp.route('/pallets/<int:pallet_id>', methods=['GET'])
@require_api_v1
def get_pallet(pallet_id):
    from modules.database import get_db
    conn = get_db()
    row = conn.execute('SELECT * FROM palety WHERE id = ?', (pallet_id,)).fetchone()
    if not row:
        return error_response(f'Pallet {pallet_id} not found', ErrorCodes.NOT_FOUND, 404)
    return success_response(_row_to_pallet(row))


@api_v1_bp.route('/pallets', methods=['POST'])
@require_api_v1
def create_pallet():
    """POST /api/v1/pallets."""
    if not request.is_json:
        return error_response('Content-Type must be application/json',
                              ErrorCodes.INVALID_JSON, 400)
    data, errors = PalletCreateSchema().validate(request.get_json(silent=True))
    if errors:
        return error_response('Validation failed', ErrorCodes.VALIDATION_ERROR, 400,
                              details=errors)

    from modules.database import get_db
    conn = get_db()
    cur = conn.execute(
        'INSERT INTO palety (nazwa, dostawca, cena_zakupu, ilosc_produktow, '
        'notatki, regal) VALUES (?, ?, ?, ?, ?, ?)',
        (
            data['name'],
            data.get('supplier', ''),
            float(data.get('purchase_price') or 0),
            int(data.get('product_count') or 0),
            data.get('notes', ''),
            data.get('location', ''),
        ),
    )
    conn.commit()
    row = conn.execute('SELECT * FROM palety WHERE id = ?', (cur.lastrowid,)).fetchone()
    return success_response(_row_to_pallet(row), status_code=201)
