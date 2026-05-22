"""Sklepakces Dashboard — przegląd pushed produktów + akcje (Hub UI).

Endpoints (URL prefix /sklepakces — pod admin/auth):
    GET  /sklepakces/                          dashboard (lista + stats)
    POST /sklepakces/repush/<hub_id>           force re-push 1 produktu
    POST /sklepakces/repush_all                push wszystkie eligible (status=magazyn + aktywna Allegro oferta)
    GET  /sklepakces/api/products.json         JSON dla AJAX odświeżania

Łączy:
  sklepakces_products (mirror table, co już wysłaliśmy)
  produkty             (Hub source)
  oferty               (Allegro aktywne ceny/stock)
  sklepakces_webhook_log (audit history)

@author: Akces Hub
"""
from __future__ import annotations

import json
import logging

from flask import Blueprint, jsonify, redirect, render_template_string, request, url_for, flash

from .database import get_db
from .auth import require_admin

logger = logging.getLogger(__name__)

sklepakces_ui_bp = Blueprint('sklepakces_ui', __name__, url_prefix='/sklepakces')

WC_BASE_URL = 'https://sklepakces.pl'


# ──────────────────────────────────────────────────────────────────────────────
# Data helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_dashboard_data():
    """Pobierz wszystkie dane potrzebne do dashboardu (one query, joinami)."""
    conn = get_db()

    # Pushed produkty z mirror — JOIN z produkty (Hub) i oferty (active Allegro).
    rows = conn.execute("""
        SELECT
            s.wc_product_id, s.sku, s.name, s.regular_price, s.stock_quantity,
            s.product_data, s.created_at, s.updated_at,
            p.id            AS hub_id,
            p.nazwa         AS hub_nazwa,
            p.krotki_tytul  AS hub_krotki_tytul,
            p.kategoria     AS hub_kategoria,
            p.asin          AS hub_asin,
            p.ean           AS hub_ean,
            p.stan          AS hub_stan,
            p.zdjecie_url   AS hub_zdjecie_url,
            p.cena_brutto   AS hub_cena_brutto,
            p.cena_allegro  AS hub_cena_allegro,
            p.ilosc         AS hub_ilosc,
            (SELECT cena FROM oferty o
                WHERE o.produkt_id = p.id AND o.status='aktywna' AND o.cena > 0
                ORDER BY o.data_aktualizacji DESC LIMIT 1) AS allegro_cena,
            (SELECT ilosc FROM oferty o
                WHERE o.produkt_id = p.id AND o.status='aktywna' AND o.cena > 0
                ORDER BY o.data_aktualizacji DESC LIMIT 1) AS allegro_ilosc,
            (SELECT allegro_id FROM oferty o
                WHERE o.produkt_id = p.id AND o.status='aktywna' AND o.cena > 0
                ORDER BY o.data_aktualizacji DESC LIMIT 1) AS allegro_id
        FROM sklepakces_products s
        LEFT JOIN produkty p ON p.id = json_extract(s.product_data, '$.hub_id')
            OR ('EAN-' || p.ean = s.sku)
            OR ('HUB-' || p.id = s.sku)
        ORDER BY s.updated_at DESC, s.created_at DESC
        LIMIT 500
    """).fetchall()

    products = []
    for r in rows:
        d = dict(r)
        # Parse product_data JSON dla payload context
        try:
            payload = json.loads(d.get('product_data') or '{}')
        except Exception:
            payload = {}
        d['payload'] = payload
        # Allegro vs DB price/stock diff
        d['has_allegro_offer'] = d.get('allegro_cena') is not None
        d['price_synced_with_allegro'] = (
            d.get('allegro_cena') is not None
            and abs(float(d.get('allegro_cena') or 0) - float(d.get('regular_price') or 0)) < 0.01
        )
        d['stock_synced_with_allegro'] = (
            d.get('allegro_ilosc') is not None
            and int(d.get('allegro_ilosc') or 0) == int(d.get('stock_quantity') or 0)
        )
        products.append(d)

    # Statystyki — agregaty
    total = len(products)
    with_allegro = sum(1 for p in products if p['has_allegro_offer'])
    price_synced = sum(1 for p in products if p['price_synced_with_allegro'])
    stock_synced = sum(1 for p in products if p['stock_synced_with_allegro'])

    # Ostatnie 20 wpisów audit log
    log_rows = conn.execute("""
        SELECT event_type, status, http_code, error_message, duration_ms, created_at
        FROM sklepakces_webhook_log
        WHERE event_type = 'product_push'
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()

    return {
        'products': products,
        'stats': {
            'total': total,
            'with_allegro': with_allegro,
            'no_allegro': total - with_allegro,
            'price_synced': price_synced,
            'stock_synced': stock_synced,
            'price_drift': total - price_synced,
            'stock_drift': total - stock_synced,
        },
        'log': [dict(r) for r in log_rows],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Dashboard route
# ──────────────────────────────────────────────────────────────────────────────

DASHBOARD_TEMPLATE = """
<!doctype html>
<html lang="pl">
<head>
    <meta charset="utf-8">
    <title>Sklepakces Dashboard — Akces Hub</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; background: #f5f5f7; color: #222; }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        h1 { margin: 0 0 8px; font-size: 24px; }
        .subtitle { color: #888; margin-bottom: 20px; font-size: 14px; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }
        .stat-card { background: #fff; padding: 16px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
        .stat-card .label { color: #888; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
        .stat-card .value { font-size: 28px; font-weight: 600; margin-top: 4px; }
        .stat-card.warn .value { color: #d97706; }
        .stat-card.bad .value { color: #dc2626; }
        .stat-card.ok .value { color: #059669; }
        table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
        th { background: #f9f9fb; padding: 10px 12px; text-align: left; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: #666; border-bottom: 1px solid #e5e5e7; }
        td { padding: 12px; font-size: 13px; vertical-align: middle; border-bottom: 1px solid #f0f0f2; }
        tr:last-child td { border-bottom: none; }
        tr:hover { background: #fafafb; }
        .sku { font-family: ui-monospace, 'SF Mono', Monaco, monospace; font-size: 12px; color: #666; }
        .name { font-weight: 500; max-width: 350px; }
        .name a { color: #1d4ed8; text-decoration: none; }
        .name a:hover { text-decoration: underline; }
        .price { font-weight: 600; }
        .price.drift { color: #d97706; }
        .stock-bad { color: #dc2626; font-weight: 600; }
        .badge { display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 500; }
        .badge.ok { background: #d1fae5; color: #065f46; }
        .badge.warn { background: #fef3c7; color: #92400e; }
        .badge.err { background: #fee2e2; color: #991b1b; }
        .btn { display: inline-block; padding: 4px 10px; background: #2563eb; color: #fff !important; border-radius: 4px; font-size: 12px; border: none; cursor: pointer; text-decoration: none; }
        .btn:hover { background: #1d4ed8; }
        .btn.danger { background: #dc2626; }
        .btn.secondary { background: #6b7280; }
        .btn-row { white-space: nowrap; }
        .btn-row form { display: inline; margin: 0; }
        h2 { margin: 32px 0 12px; font-size: 18px; }
        .log-table td { font-size: 12px; }
        .flash { padding: 12px 16px; background: #d1fae5; color: #065f46; border-radius: 6px; margin-bottom: 16px; }
        .flash.error { background: #fee2e2; color: #991b1b; }
        .empty { text-align: center; padding: 40px; color: #888; }
        .action-bar { display: flex; gap: 8px; margin-bottom: 16px; align-items: center; }
        .filter-buttons { display: flex; gap: 6px; }
        .filter-btn { padding: 6px 12px; background: #fff; border: 1px solid #d1d5db; border-radius: 6px; font-size: 12px; cursor: pointer; }
        .filter-btn.active { background: #2563eb; color: #fff; border-color: #2563eb; }
        .thumb { width: 36px; height: 36px; object-fit: cover; border-radius: 4px; background: #f0f0f2; }
    </style>
</head>
<body>
<div class="container">

    <h1>🛒 Sklepakces Dashboard</h1>
    <div class="subtitle">Produkty wypchnięte z Hub → sklepakces.pl WC. Akcje: re-push, view, sync.</div>

    {% with messages = get_flashed_messages(with_categories=true) %}
        {% for category, msg in messages %}
            <div class="flash {{ category }}">{{ msg }}</div>
        {% endfor %}
    {% endwith %}

    <div class="stats">
        <div class="stat-card"><div class="label">Wszystkie</div><div class="value">{{ stats.total }}</div></div>
        <div class="stat-card ok"><div class="label">Z aukcją Allegro</div><div class="value">{{ stats.with_allegro }}</div></div>
        <div class="stat-card warn"><div class="label">Bez aukcji</div><div class="value">{{ stats.no_allegro }}</div></div>
        <div class="stat-card {{ 'bad' if stats.price_drift > 0 else 'ok' }}"><div class="label">Cena drift</div><div class="value">{{ stats.price_drift }}</div></div>
        <div class="stat-card {{ 'bad' if stats.stock_drift > 0 else 'ok' }}"><div class="label">Stock drift</div><div class="value">{{ stats.stock_drift }}</div></div>
    </div>

    <div class="action-bar">
        <form method="POST" action="{{ url_for('sklepakces_ui.repush_all') }}" onsubmit="return confirm('Re-pushnij WSZYSTKIE produkty z mirror? (force, ~1.1s/req)');">
            <button class="btn" type="submit">🔄 Re-push wszystkie</button>
        </form>
        <div class="filter-buttons">
            <button class="filter-btn active" onclick="filterRows('all')">Wszystkie</button>
            <button class="filter-btn" onclick="filterRows('drift')">Tylko drift</button>
            <button class="filter-btn" onclick="filterRows('no_allegro')">Bez Allegro</button>
        </div>
    </div>

    {% if products %}
    <table id="products-table">
        <thead>
            <tr>
                <th>Zdj</th>
                <th>WC ID</th>
                <th>SKU</th>
                <th>Nazwa</th>
                <th>Cena WC</th>
                <th>Cena Allegro</th>
                <th>Stock WC</th>
                <th>Stock Allegro</th>
                <th>Kategoria</th>
                <th>Status sync</th>
                <th>Akcje</th>
            </tr>
        </thead>
        <tbody>
            {% for p in products %}
            <tr data-row-filter="{% if not p.has_allegro_offer %}no_allegro{% elif not p.price_synced_with_allegro or not p.stock_synced_with_allegro %}drift{% else %}ok{% endif %}">
                <td>
                    {% if p.hub_zdjecie_url %}
                        <img class="thumb" src="{{ p.hub_zdjecie_url }}" alt="">
                    {% else %}
                        <div class="thumb"></div>
                    {% endif %}
                </td>
                <td><a href="{{ wc_base }}/?post_type=product&p={{ p.wc_product_id }}" target="_blank" rel="noopener" class="sku">#{{ p.wc_product_id }}</a></td>
                <td class="sku">{{ p.sku }}</td>
                <td class="name">
                    {{ p.hub_krotki_tytul or p.hub_nazwa or p.name }}
                    {% if p.hub_id %}<br><small style="color:#888">hub_id={{ p.hub_id }}</small>{% endif %}
                </td>
                <td class="price {% if not p.price_synced_with_allegro and p.has_allegro_offer %}drift{% endif %}">{{ '%.2f'|format(p.regular_price or 0) }} zł</td>
                <td>
                    {% if p.has_allegro_offer %}
                        {{ '%.2f'|format(p.allegro_cena) }} zł
                    {% else %}
                        <span style="color:#aaa">—</span>
                    {% endif %}
                </td>
                <td class="{% if (p.stock_quantity or 0) == 0 %}stock-bad{% endif %}">{{ p.stock_quantity or 0 }}</td>
                <td>
                    {% if p.has_allegro_offer %}
                        {{ p.allegro_ilosc or 0 }}
                    {% else %}
                        <span style="color:#aaa">—</span>
                    {% endif %}
                </td>
                <td>{{ p.hub_kategoria or '—' }}</td>
                <td>
                    {% if not p.has_allegro_offer %}
                        <span class="badge warn">brak aukcji</span>
                    {% elif not p.price_synced_with_allegro %}
                        <span class="badge warn">cena drift</span>
                    {% elif not p.stock_synced_with_allegro %}
                        <span class="badge warn">stock drift</span>
                    {% else %}
                        <span class="badge ok">OK</span>
                    {% endif %}
                </td>
                <td class="btn-row">
                    {% if p.hub_id %}
                    <form method="POST" action="{{ url_for('sklepakces_ui.repush', hub_id=p.hub_id) }}">
                        <button class="btn" type="submit" title="Force re-push (override mirror skip)">🔄</button>
                    </form>
                    {% endif %}
                    <a class="btn secondary" href="{{ wc_base }}/wp-admin/post.php?post={{ p.wc_product_id }}&action=edit" target="_blank" rel="noopener" title="Edytuj na WP admin">✏️</a>
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    {% else %}
        <div class="empty">Brak pushed produktów — uruchom <code>scripts/push_sklepakces.py --all</code> na Pi.</div>
    {% endif %}

    <h2>📜 Ostatnie operacje (audit log)</h2>
    {% if log %}
    <table class="log-table">
        <thead>
            <tr>
                <th>Data</th>
                <th>Event</th>
                <th>Status</th>
                <th>HTTP</th>
                <th>Czas</th>
                <th>Error</th>
            </tr>
        </thead>
        <tbody>
            {% for l in log %}
            <tr>
                <td>{{ l.created_at }}</td>
                <td>{{ l.event_type }}</td>
                <td>
                    {% if l.status == 'success' %}
                        <span class="badge ok">{{ l.status }}</span>
                    {% else %}
                        <span class="badge err">{{ l.status }}</span>
                    {% endif %}
                </td>
                <td>{{ l.http_code }}</td>
                <td>{{ l.duration_ms }} ms</td>
                <td style="max-width: 400px; font-size: 11px; color: #666;">{{ l.error_message or '—' }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    {% else %}
        <div class="empty" style="padding: 16px;">Brak wpisów w audit log.</div>
    {% endif %}

</div>

<script>
function filterRows(type) {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');
    document.querySelectorAll('#products-table tbody tr').forEach(row => {
        const rowType = row.dataset.rowFilter;
        if (type === 'all' || rowType === type) {
            row.style.display = '';
        } else {
            row.style.display = 'none';
        }
    });
}
</script>

</body>
</html>
"""


@sklepakces_ui_bp.route('/', methods=['GET'])
@require_admin
def dashboard():
    data = _get_dashboard_data()
    return render_template_string(
        DASHBOARD_TEMPLATE,
        wc_base=WC_BASE_URL,
        **data,
    )


@sklepakces_ui_bp.route('/repush/<int:hub_id>', methods=['POST'])
@require_admin
def repush(hub_id: int):
    """Force re-push 1 produktu Hub→sklepakces.pl (bypass mirror skip)."""
    try:
        from .sklepakces_push import push_one_product
        result = push_one_product(hub_id, force=True)
    except Exception as e:
        logger.exception(f'repush hub_id={hub_id} failed')
        flash(f'Re-push hub_id={hub_id} EXCEPTION: {e}', 'error')
        return redirect(url_for('sklepakces_ui.dashboard'))

    status = result.get('status')
    if status == 'ok':
        flash(
            f'✅ Re-push hub_id={hub_id} OK — sku={result.get("sku")} '
            f'wc_id={result.get("wc_product_id")} ({result.get("duration_ms")}ms)',
            'success',
        )
    elif status == 'skip':
        flash(f'⊘ hub_id={hub_id} SKIP: {result.get("msg")}', 'warning')
    else:
        flash(
            f'❌ Re-push hub_id={hub_id} ERROR: {result.get("msg") or result.get("response")}',
            'error',
        )
    return redirect(url_for('sklepakces_ui.dashboard'))


@sklepakces_ui_bp.route('/repush_all', methods=['POST'])
@require_admin
def repush_all():
    """Re-push wszystkich produktów z mirror (force, batchowo).

    UWAGA: throttle 1.1s/req → przy ~20 produktach to ~22 sekundy.
    Większe batche mogą hit Telegram throttle (10/min) jeśli wiele alertów.
    """
    try:
        conn = get_db()
        rows = conn.execute("""
            SELECT json_extract(product_data, '$.hub_id') AS hub_id
            FROM sklepakces_products
            WHERE json_extract(product_data, '$.hub_id') IS NOT NULL
            ORDER BY wc_product_id DESC
        """).fetchall()
        hub_ids = [int(r['hub_id']) for r in rows if r['hub_id']]
    except Exception as e:
        flash(f'Lookup error: {e}', 'error')
        return redirect(url_for('sklepakces_ui.dashboard'))

    if not hub_ids:
        flash('Brak produktów z hub_id w mirror — nic do re-pushowania.', 'warning')
        return redirect(url_for('sklepakces_ui.dashboard'))

    from .sklepakces_push import push_one_product
    import time
    ok = err = skip = 0
    for i, hid in enumerate(hub_ids):
        if i > 0:
            time.sleep(1.1)  # plugin RATE_LIMIT
        try:
            r = push_one_product(hid, force=True)
            if r.get('status') == 'ok':
                ok += 1
            elif r.get('status') == 'skip':
                skip += 1
            else:
                err += 1
        except Exception as e:
            logger.warning(f'repush_all hub_id={hid} failed: {e}')
            err += 1

    flash(
        f'Re-push batch zakończony: ok={ok} skip={skip} error={err} total={len(hub_ids)}',
        'success' if err == 0 else 'error',
    )
    return redirect(url_for('sklepakces_ui.dashboard'))


@sklepakces_ui_bp.route('/api/products.json', methods=['GET'])
@require_admin
def api_products():
    """JSON dla AJAX odświeżania (przyszłe SPA / auto-refresh)."""
    data = _get_dashboard_data()
    # Strip product_data JSON dla mniejszego payload
    for p in data['products']:
        p.pop('product_data', None)
        p.pop('payload', None)
    return jsonify(data)
