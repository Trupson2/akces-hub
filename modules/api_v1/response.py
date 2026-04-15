"""Structured JSON response helpers dla API v1.

Wszystkie endpointy zwracaja JSON w stalym formacie, zeby klient mogl
deterministycznie parsowac odpowiedzi.

Sukces:
    {"status": "success", "data": {...}, "meta": {...}}   # meta opcjonalne
Blad:
    {"status": "error", "error": "human-readable", "code": "MACHINE_CODE",
     "details": {...}}                                     # details opcjonalne
"""

from flask import jsonify


def success_response(data, status_code=200, meta=None):
    """Zwraca standardowa strukture {status: success, data, meta?}.

    Args:
        data: dict lub list z payloadem
        status_code: HTTP status (200/201/204)
        meta: opcjonalny dict z pagination/rate-limit info
    """
    resp = {'status': 'success', 'data': data}
    if meta is not None:
        resp['meta'] = meta
    return jsonify(resp), status_code


def error_response(error_msg, code, status_code=400, details=None):
    """Zwraca standardowa strukture bledu.

    Args:
        error_msg: komunikat human-readable po angielsku (pole `error`)
        code: maszynowy kod (np. VALIDATION_ERROR, NOT_FOUND, INVALID_API_KEY)
        status_code: HTTP status (400/401/403/404/409/429/500)
        details: opcjonalny dict z detalami walidacji (np. {field: reason})
    """
    resp = {'status': 'error', 'error': error_msg, 'code': code}
    if details is not None:
        resp['details'] = details
    return jsonify(resp), status_code


def paginate(page, per_page, total):
    """Helper do metadanych paginacji.

    Returns dict z kluczami page, per_page, total, total_pages.
    """
    if per_page <= 0:
        per_page = 1
    total_pages = (int(total) + per_page - 1) // per_page
    return {
        'page': int(page),
        'per_page': int(per_page),
        'total': int(total),
        'total_pages': int(total_pages),
    }


# Wspolne maszynowe kody bledu — uzywaj tych stalych zamiast literalow,
# zeby API mialo spojny slownik.
class ErrorCodes:
    # 400
    VALIDATION_ERROR = 'VALIDATION_ERROR'
    BAD_REQUEST = 'BAD_REQUEST'
    MISSING_FIELD = 'MISSING_FIELD'
    INVALID_JSON = 'INVALID_JSON'
    # 401
    INVALID_API_KEY = 'INVALID_API_KEY'
    MISSING_API_KEY = 'MISSING_API_KEY'
    # 403
    API_KEY_REVOKED = 'API_KEY_REVOKED'
    FORBIDDEN = 'FORBIDDEN'
    # 404
    NOT_FOUND = 'NOT_FOUND'
    # 409
    CONFLICT = 'CONFLICT'
    # 429
    RATE_LIMIT_EXCEEDED = 'RATE_LIMIT_EXCEEDED'
    # 500
    INTERNAL_ERROR = 'INTERNAL_ERROR'
    DATABASE_ERROR = 'DATABASE_ERROR'
