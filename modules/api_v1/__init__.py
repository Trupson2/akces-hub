"""AKCES HUB Public REST API v1.

Uniwersalny REST API pozwalajacy zewnetrznym systemom (sklepy PHP/Node/Django,
custom integracje) podlaczyc sie do instancji AKCES HUB klienta.

Charakterystyka:
- Prefix: /api/v1
- Auth: X-API-Key header (lub Authorization: Bearer ...)
- Klucze: bcrypt-hashed, prefix ak_live_*, nigdy nie logowane plain-text
- Rate limit: per-key (default 60/min, configurowalny)
- Response: structured JSON {status, data, meta} / {status, error, code}
- Webhooks: outbound HMAC-SHA256, retry exponential backoff
- Dokumentacja: OpenAPI 3.0 + Swagger UI pod /api/v1/docs
- Backwards compat: legacy /api/* zostaja nietkniete

Architektura:
- auth.py         -> API key generation/verification + @require_api_v1
- rate_limit.py   -> per-key limiter integracja z Flask-Limiter
- response.py     -> success_response, error_response, paginate helpers
- products.py     -> CRUD produktow
- orders.py       -> CRUD zamowien + webhook trigger
- stock.py        -> operacje magazynowe
- pallets.py      -> palety zwrotne
- webhooks.py     -> rejestracja webhookow + delivery worker
- schemas.py      -> walidacja request/response (lightweight, bez marshmallow)
- openapi.py      -> OpenAPI spec + Swagger UI
"""

from flask import Blueprint

# Single blueprint agregujacy wszystkie route'y v1.
api_v1_bp = Blueprint('api_v1', __name__)


def register_api_v1(app):
    """Rejestruje API v1 w Flask app.

    Podlacza wszystkie sub-modules route'ami do wspolnego blueprintu,
    inicjalizuje rate limiter i startuje background worker dla webhookow.
    """
    # Import lazy: unika cyklicznych importow na starcie aplikacji.
    from . import auth          # noqa: F401 -- rejestruje /api/v1/health (bez auth)
    from . import products      # noqa: F401
    from . import orders        # noqa: F401
    from . import stock         # noqa: F401
    from . import pallets       # noqa: F401
    from . import webhooks      # noqa: F401
    from . import openapi       # noqa: F401

    app.register_blueprint(api_v1_bp, url_prefix='/api/v1')

    # Start webhook delivery worker (background thread, daemon=True)
    try:
        from .webhooks import start_delivery_worker
        start_delivery_worker()
    except Exception as e:  # pragma: no cover - best effort
        print(f"[WARN] API v1 webhook worker nie wystartowal: {e}")

    # Admin UI for API keys
    try:
        from . import admin_ui
        admin_ui.register_admin_routes(app)
    except Exception as e:  # pragma: no cover
        print(f"[WARN] API v1 admin UI nie zarejestrowany: {e}")

    print("[OK] API v1 zarejestrowane (prefix /api/v1, docs /api/v1/docs)")
