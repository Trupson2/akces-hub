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

    # Pobierz wc_status z ostatniego SUCCESS audit log per SKU (response['status'])
    # Plugin response zawiera "status": "publish"|"draft" + "gpsr_blocked": bool
    wc_status_by_sku = {}
    try:
        log_rows = conn.execute("""
            SELECT * FROM sklepakces_webhook_log
            WHERE event_type = 'product_push' AND status = 'success' AND http_code >= 200 AND http_code < 300
            ORDER BY id DESC
        """).fetchall()
        for lr in log_rows:
            ld = dict(lr)
            # Brak SKU column w webhook_log — pomijamy. Wykorzystujemy `payload` z mirror.
            # (audit log nie ma SKU — workaround: w mirror.product_data jest sku payload)
            pass
    except Exception:
        pass

    products = []
    for r in rows:
        d = dict(r)
        # Parse product_data JSON dla payload context
        try:
            payload = json.loads(d.get('product_data') or '{}')
        except Exception:
            payload = {}
        d['payload'] = payload
        # WC status — wybór: explicit w mirror.product_data['_last_wc_status']
        # (zapis robi sklepakces_push.record_sync — patrz fix poniżej)
        d['wc_status'] = payload.get('_last_wc_status') or 'unknown'
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
        # Row filter category (do client-side filtra w JS)
        if not d['has_allegro_offer']:
            d['row_filter'] = 'no_allegro'
        elif not d['price_synced_with_allegro'] or not d['stock_synced_with_allegro']:
            d['row_filter'] = 'drift'
        else:
            d['row_filter'] = 'ok'
        products.append(d)

    # Statystyki — agregaty
    total = len(products)
    with_allegro = sum(1 for p in products if p['has_allegro_offer'])
    price_synced = sum(1 for p in products if p['price_synced_with_allegro'])
    stock_synced = sum(1 for p in products if p['stock_synced_with_allegro'])
    publish_count = sum(1 for p in products if p['wc_status'] == 'publish')
    draft_count = sum(1 for p in products if p['wc_status'] == 'draft')

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
            'publish': publish_count,
            'draft': draft_count,
            'unknown_status': total - publish_count - draft_count,
        },
        'log': [dict(r) for r in log_rows],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Dashboard route
# ──────────────────────────────────────────────────────────────────────────────

DASHBOARD_TEMPLATE = """{% extends "base.html" %}
{% block page_title %}Sklepakces — Dashboard{% endblock %}
{% block content %}
<style>
.sk-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }
.sk-stat { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; transition: all 0.2s; }
.sk-stat:hover { border-color: var(--accent); transform: translateY(-1px); }
.sk-stat .lbl { color: var(--text-muted); font-size: 11px; text-transform: uppercase; letter-spacing: 1px; font-weight: 600; }
.sk-stat .val { font-size: 28px; font-weight: 700; margin-top: 6px; color: var(--text); }
.sk-stat.ok .val { color: var(--green); }
.sk-stat.warn .val { color: var(--yellow); }
.sk-stat.bad .val { color: var(--red); }
.sk-stat.info .val { color: var(--blue); }

.sk-action-bar { display: flex; gap: 10px; margin-bottom: 16px; align-items: center; flex-wrap: wrap; }
.sk-filter-btn { padding: 7px 14px; background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px;
                 font-size: 12px; cursor: pointer; color: var(--text-secondary); font-weight: 500; transition: all 0.15s; }
.sk-filter-btn:hover { border-color: var(--accent); color: var(--text); }
.sk-filter-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }

.sk-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; box-shadow: var(--shadow); }
.sk-card-header { padding: 16px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
.sk-card-title { font-weight: 600; font-size: 15px; color: var(--text); display: flex; align-items: center; gap: 8px; }

table.sk-table { width: 100%; border-collapse: collapse; font-size: 13px; }
table.sk-table th { padding: 10px 12px; text-align: left; font-size: 10px; font-weight: 700; text-transform: uppercase;
                    letter-spacing: 1px; color: var(--text-muted); border-bottom: 1px solid var(--border); background: var(--bg); }
table.sk-table td { padding: 12px; vertical-align: middle; border-bottom: 1px solid var(--border-light); color: var(--text); }
table.sk-table tr:last-child td { border-bottom: none; }
table.sk-table tr:hover { background: var(--bg); }

.sk-sku { font-family: ui-monospace, 'SF Mono', Monaco, monospace; font-size: 11px; color: var(--text-muted); }
.sk-name { font-weight: 500; max-width: 320px; }
.sk-name a { color: var(--text); text-decoration: none; }
.sk-name a:hover { color: var(--accent); }
.sk-price { font-weight: 600; }
.sk-price.drift { color: var(--yellow); }
.sk-stock-bad { color: var(--red); font-weight: 600; }
.sk-badge { display: inline-block; padding: 3px 8px; border-radius: 10px; font-size: 10px; font-weight: 600;
            text-transform: uppercase; letter-spacing: 0.5px; }
.sk-badge.ok { background: var(--green-soft); color: var(--green); border: 1px solid var(--green-soft); }
.sk-badge.warn { background: var(--yellow-soft); color: var(--yellow); border: 1px solid var(--yellow-soft); }
.sk-badge.err { background: var(--red-soft); color: var(--red); border: 1px solid var(--red-soft); }
.sk-badge.info { background: var(--blue-soft); color: var(--blue); border: 1px solid var(--blue-soft); }

.sk-btn { display: inline-flex; align-items: center; gap: 4px; padding: 6px 12px; background: var(--accent);
          color: #fff !important; border-radius: 6px; font-size: 12px; font-weight: 600; border: none; cursor: pointer;
          text-decoration: none; transition: all 0.15s; }
.sk-btn:hover { filter: brightness(1.15); }
.sk-btn.primary { background: var(--accent); }
.sk-btn.success { background: var(--green); }
.sk-btn.danger { background: var(--red); }
.sk-btn.secondary { background: var(--bg); color: var(--text) !important; border: 1px solid var(--border); }
.sk-btn-row { white-space: nowrap; display: flex; gap: 4px; }
.sk-btn-row form { display: inline; margin: 0; }
.sk-thumb { width: 40px; height: 40px; object-fit: cover; border-radius: 6px; background: var(--bg); }

.sk-flash { padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; font-size: 13px; font-weight: 500; }
.sk-flash.success { background: var(--green-soft); color: var(--green); border: 1px solid var(--green); }
.sk-flash.error { background: var(--red-soft); color: var(--red); border: 1px solid var(--red); }
.sk-flash.warning { background: var(--yellow-soft); color: var(--yellow); border: 1px solid var(--yellow); }

.sk-empty { text-align: center; padding: 40px; color: var(--text-muted); }
.sk-empty code { background: var(--bg); padding: 2px 6px; border-radius: 4px; color: var(--accent); }

.sk-subtitle { color: var(--text-muted); margin-bottom: 20px; font-size: 13px; }
.sk-h2 { margin: 28px 0 14px; font-size: 16px; font-weight: 700; color: var(--text); display: flex; align-items: center; gap: 8px; }
</style>

<div class="sk-subtitle">Produkty wypchnięte z Hub → sklepakces.pl WC. Cena/stock z aktywnych aukcji Allegro.</div>

{% with messages = get_flashed_messages(with_categories=true) %}
    {% for category, msg in messages %}
        <div class="sk-flash {{ category }}">{{ msg }}</div>
    {% endfor %}
{% endwith %}

<div class="sk-stats">
    <div class="sk-stat info">
        <div class="lbl">Wszystkie</div><div class="val">{{ stats.total }}</div>
    </div>
    <div class="sk-stat ok">
        <div class="lbl">Publish (na sklepie)</div><div class="val">{{ stats.publish }}</div>
    </div>
    <div class="sk-stat warn">
        <div class="lbl">Draft (ukryte)</div><div class="val">{{ stats.draft }}</div>
    </div>
    <div class="sk-stat ok">
        <div class="lbl">Z aukcją Allegro</div><div class="val">{{ stats.with_allegro }}</div>
    </div>
    <div class="sk-stat warn">
        <div class="lbl">Bez aukcji</div><div class="val">{{ stats.no_allegro }}</div>
    </div>
    <div class="sk-stat {{ 'bad' if stats.price_drift > 0 else 'ok' }}">
        <div class="lbl">Cena drift</div><div class="val">{{ stats.price_drift }}</div>
    </div>
    <div class="sk-stat {{ 'bad' if stats.stock_drift > 0 else 'ok' }}">
        <div class="lbl">Stock drift</div><div class="val">{{ stats.stock_drift }}</div>
    </div>
</div>

<div class="sk-action-bar">
    <form method="POST" action="{{ url_for('sklepakces_ui.repush_all') }}"
          onsubmit="return confirm('Re-pushnij WSZYSTKIE produkty z mirror? (force, ~1.1s/req)');">
        <button class="sk-btn primary" type="submit">
            <span class="material-symbols-outlined" style="font-size:1rem">refresh</span> Re-push wszystkie
        </button>
    </form>
    <div style="display:flex;gap:6px;margin-left:auto">
        <button class="sk-filter-btn active" onclick="filterRows('all', event)">Wszystkie</button>
        <button class="sk-filter-btn" onclick="filterRows('publish', event)">Publish</button>
        <button class="sk-filter-btn" onclick="filterRows('draft', event)">Draft</button>
        <button class="sk-filter-btn" onclick="filterRows('drift', event)">Drift</button>
        <button class="sk-filter-btn" onclick="filterRows('no_allegro', event)">Bez Allegro</button>
    </div>
</div>

{% if products %}
<div class="sk-card">
    <div class="sk-card-header">
        <div class="sk-card-title">
            <span class="material-symbols-outlined">inventory_2</span>
            Produkty na sklepie ({{ products|length }})
        </div>
    </div>
    <table class="sk-table" id="products-table">
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
                <th>WC Status</th>
                <th>Sync</th>
                <th>Akcje</th>
            </tr>
        </thead>
        <tbody>
            {% for p in products %}
            <tr data-row-filter="{{ p.row_filter }}" data-wc-status="{{ p.wc_status }}">
                <td>
                    {% if p.hub_zdjecie_url %}
                        <img class="sk-thumb" src="{{ p.hub_zdjecie_url }}" alt="" loading="lazy">
                    {% else %}
                        <div class="sk-thumb"></div>
                    {% endif %}
                </td>
                <td><a href="{{ wc_base }}/?post_type=product&p={{ p.wc_product_id }}" target="_blank" rel="noopener" class="sk-sku">#{{ p.wc_product_id }}</a></td>
                <td class="sk-sku">{{ p.sku }}</td>
                <td class="sk-name">
                    <a href="{{ wc_base }}/?post_type=product&p={{ p.wc_product_id }}" target="_blank" rel="noopener">{{ p.hub_krotki_tytul or p.hub_nazwa or p.name }}</a>
                    {% if p.hub_id %}<br><small class="sk-sku">hub_id={{ p.hub_id }}</small>{% endif %}
                </td>
                <td class="sk-price {% if not p.price_synced_with_allegro and p.has_allegro_offer %}drift{% endif %}">{{ '%.2f'|format(p.regular_price or 0) }} zł</td>
                <td>
                    {% if p.has_allegro_offer %}{{ '%.2f'|format(p.allegro_cena) }} zł
                    {% else %}<span style="color:var(--text-muted)">—</span>{% endif %}
                </td>
                <td class="{% if (p.stock_quantity or 0) == 0 %}sk-stock-bad{% endif %}">{{ p.stock_quantity or 0 }}</td>
                <td>
                    {% if p.has_allegro_offer %}{{ p.allegro_ilosc or 0 }}
                    {% else %}<span style="color:var(--text-muted)">—</span>{% endif %}
                </td>
                <td><span class="sk-sku">{{ p.hub_kategoria or '—' }}</span></td>
                <td>
                    {% if p.wc_status == 'publish' %}
                        <span class="sk-badge ok">publish</span>
                    {% elif p.wc_status == 'draft' %}
                        <span class="sk-badge warn">draft</span>
                    {% else %}
                        <span class="sk-badge info">{{ p.wc_status or '?' }}</span>
                    {% endif %}
                </td>
                <td>
                    {% if not p.has_allegro_offer %}<span class="sk-badge warn">brak aukcji</span>
                    {% elif not p.price_synced_with_allegro %}<span class="sk-badge warn">cena</span>
                    {% elif not p.stock_synced_with_allegro %}<span class="sk-badge warn">stock</span>
                    {% else %}<span class="sk-badge ok">OK</span>{% endif %}
                </td>
                <td class="sk-btn-row">
                    {% if p.hub_id %}
                    <form method="POST" action="{{ url_for('sklepakces_ui.repush', hub_id=p.hub_id) }}">
                        <button class="sk-btn primary" type="submit" title="Force re-push (override mirror)">
                            <span class="material-symbols-outlined" style="font-size:1rem">refresh</span>
                        </button>
                    </form>
                    {% endif %}
                    <a class="sk-btn secondary" href="{{ wc_base }}/wp-admin/post.php?post={{ p.wc_product_id }}&action=edit" target="_blank" rel="noopener" title="Edytuj w WP admin">
                        <span class="material-symbols-outlined" style="font-size:1rem">edit</span>
                    </a>
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div>
{% else %}
    <div class="sk-card">
        <div class="sk-empty">Brak pushed produktów — uruchom <code>scripts/push_sklepakces.py --all</code> na Pi.</div>
    </div>
{% endif %}

<div class="sk-h2"><span class="material-symbols-outlined">history</span> Ostatnie operacje (audit log)</div>
{% if log %}
<div class="sk-card">
    <table class="sk-table">
        <thead>
            <tr><th>Data</th><th>Event</th><th>Status</th><th>HTTP</th><th>Czas</th><th>Error</th></tr>
        </thead>
        <tbody>
            {% for l in log %}
            <tr>
                <td class="sk-sku">{{ l.created_at }}</td>
                <td>{{ l.event_type }}</td>
                <td>
                    {% if l.status == 'success' %}<span class="sk-badge ok">{{ l.status }}</span>
                    {% else %}<span class="sk-badge err">{{ l.status }}</span>{% endif %}
                </td>
                <td>{{ l.http_code }}</td>
                <td>{{ l.duration_ms }} ms</td>
                <td style="max-width:400px;font-size:11px;color:var(--text-muted)">{{ l.error_message or '—' }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div>
{% else %}
    <div class="sk-card"><div class="sk-empty" style="padding:16px">Brak wpisów w audit log.</div></div>
{% endif %}

<script>
function filterRows(type, evt) {
    document.querySelectorAll('.sk-filter-btn').forEach(b => b.classList.remove('active'));
    if (evt) evt.target.classList.add('active');
    document.querySelectorAll('#products-table tbody tr').forEach(row => {
        const rowType = row.dataset.rowFilter;
        const wcStatus = row.dataset.wcStatus;
        let show = false;
        if (type === 'all') show = true;
        else if (type === 'publish' || type === 'draft') show = (wcStatus === type);
        else show = (rowType === type);
        row.style.display = show ? '' : 'none';
    });
}
</script>
{% endblock %}
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
