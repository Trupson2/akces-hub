"""Sklepakces Flask Blueprint + schema migrations.

Endpoints (wszystkie pod /api/v1/sklepakces/* prefix — osobny namespace
od `api_v1` owners):

    GET  /api/v1/sklepakces/health             (no HMAC — connection diagnostic)
    POST /api/v1/sklepakces/orders             (HMAC + nonce — order webhook)
    POST /api/v1/sklepakces/products           (HMAC + nonce — product sync)
    POST /api/v1/sklepakces/stock_sync         (HMAC + nonce — inventory change)
    GET  /api/v1/sklepakces/inventory/<sku>    (HMAC + nonce — stock query)

Schema (4 tabele, prefix `sklepakces_*` — separation od `api_v1` orders/products):
    sklepakces_orders         — pełny payload + signature_nonce (UNIQUE → idempotency)
    sklepakces_products       — INSERT/UPDATE po wc_product_id (UNIQUE)
    sklepakces_stock_log      — audit trail każdej zmiany stocku
    sklepakces_webhook_log    — wszystkie webhook calls (success/error/duration)

Register w app.py (1 linia po init_db):
    from modules.sklepakces_blueprint import sklepakces_bp, init_sklepakces_schema
    init_sklepakces_schema()
    app.register_blueprint(sklepakces_bp)
"""
from __future__ import annotations

from flask import Blueprint, jsonify

from modules.sklepakces_hmac import require_sklepakces_hmac
from modules.sklepakces_products import handle_product_webhook
from modules.sklepakces_stock import handle_inventory_query, handle_stock_sync
from modules.sklepakces_webhook import handle_order_webhook


sklepakces_bp = Blueprint(
    'sklepakces',
    __name__,
    url_prefix='/api/v1/sklepakces',
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@sklepakces_bp.route('/health', methods=['GET'])
def health():
    """Public healthcheck — bez HMAC. Pozwala plugin'owi sprawdzić connection
    przed wysłaniem signed request'a (diagnostic).
    """
    from modules.redis_nonce_cache import get_stats as redis_stats
    return jsonify({
        'status': 'success',
        'data': {
            'service': 'akces-hub-sklepakces',
            'version': 'v1',
            'ok': True,
            'redis': redis_stats(),
        },
    }), 200


@sklepakces_bp.route('/orders', methods=['POST'])
@require_sklepakces_hmac
def orders_endpoint():
    return handle_order_webhook()


@sklepakces_bp.route('/products', methods=['POST'])
@require_sklepakces_hmac
def products_endpoint():
    return handle_product_webhook()


@sklepakces_bp.route('/stock_sync', methods=['POST'])
@require_sklepakces_hmac
def stock_sync_endpoint():
    return handle_stock_sync()


@sklepakces_bp.route('/inventory/<sku>', methods=['GET'])
@require_sklepakces_hmac
def inventory_endpoint(sku):
    return handle_inventory_query(sku)


# ---------------------------------------------------------------------------
# Schema migrations (idempotent CREATE TABLE IF NOT EXISTS)
# ---------------------------------------------------------------------------

def init_sklepakces_schema():
    """Create sklepakces_* tables jeśli nie istnieją.

    Called raz przy app start po init_db() lub przy pierwszym request.
    Idempotent — wielokrotne wywołanie no-op.
    """
    from modules.database import get_db
    conn = get_db()

    # 1. Orders — pełny payload jako JSON + signature_nonce UNIQUE
    conn.execute('''CREATE TABLE IF NOT EXISTS sklepakces_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wc_order_id INTEGER NOT NULL,
        order_number TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'processing',
        total DECIMAL(10,2) NOT NULL,
        currency TEXT NOT NULL DEFAULT 'PLN',
        customer_email TEXT NOT NULL,
        customer_data TEXT NOT NULL,
        items_data TEXT NOT NULL,
        payment_data TEXT NOT NULL DEFAULT '{}',
        metadata TEXT DEFAULT '{}',
        signature_nonce TEXT NOT NULL UNIQUE,
        received_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        processed_at DATETIME,
        paletomat_allocated_at DATETIME,
        notes TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_sklepakces_orders_wc_order_id '
                 'ON sklepakces_orders(wc_order_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_sklepakces_orders_status '
                 'ON sklepakces_orders(status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_sklepakces_orders_received_at '
                 'ON sklepakces_orders(received_at)')

    # 2. Products — INSERT/UPDATE po wc_product_id UNIQUE
    conn.execute('''CREATE TABLE IF NOT EXISTS sklepakces_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wc_product_id INTEGER NOT NULL UNIQUE,
        sku TEXT,
        name TEXT NOT NULL,
        regular_price DECIMAL(10,2),
        sale_price DECIMAL(10,2),
        stock_quantity INTEGER DEFAULT 0,
        product_data TEXT NOT NULL,
        gpsr_data TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME,
        allegro_listed_at DATETIME,
        allegro_listing_id TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_sklepakces_products_sku '
                 'ON sklepakces_products(sku)')

    # 3. Stock log — audit trail
    conn.execute('''CREATE TABLE IF NOT EXISTS sklepakces_stock_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wc_product_id INTEGER NOT NULL,
        sku TEXT,
        old_quantity INTEGER,
        new_quantity INTEGER,
        reason TEXT,
        changed_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_sklepakces_stock_log_sku '
                 'ON sklepakces_stock_log(sku)')

    # 4. Webhook log — all webhook calls (success + error + duration)
    conn.execute('''CREATE TABLE IF NOT EXISTS sklepakces_webhook_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        wc_order_id INTEGER,
        status TEXT NOT NULL,
        http_code INTEGER NOT NULL,
        error_message TEXT,
        duration_ms INTEGER,
        client_ip TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_sklepakces_webhook_log_status '
                 'ON sklepakces_webhook_log(status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_sklepakces_webhook_log_created '
                 'ON sklepakces_webhook_log(created_at)')

    conn.commit()
