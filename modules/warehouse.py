"""
Moduł magazynu — routes dla /warehouse/*, /api/warehouse/*
"""
from flask import Blueprint, request, redirect, session, flash, jsonify, Response, current_app, render_template
import os
import json

warehouse_bp = Blueprint('warehouse', __name__)


# ============================================================
# WAREHOUSE HEATMAP - 3D VISUALIZATION
# ============================================================

# WAREHOUSE EDITOR ROUTES
@warehouse_bp.route('/warehouse/editor')
def warehouse_editor():
    """Visual editor for warehouse layout"""
    return render_template('warehouse_editor.html')


@warehouse_bp.route('/api/warehouse/layout/save', methods=['POST'])
def save_warehouse_layout():
    """Save warehouse layout to JSON file"""
    try:
        layout = request.json

        print("=" * 60)
        print("[DOWN] SAVE LAYOUT REQUEST")
        print(f"Received data: {layout is not None}")

        # Validate
        if not layout or 'shelves' not in layout:
            print("[ERR] Invalid layout - missing shelves")
            return jsonify({'error': 'Invalid layout'}), 400

        print(f"[OK] Valid layout with {len(layout['shelves'])} shelves")

        # Save to file - ABSOLUTE PATH
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        layout_path = os.path.join(app_dir, 'warehouse_layout.json')

        print(f"[FOLD] Saving to: {layout_path}")

        with open(layout_path, 'w', encoding='utf-8') as f:
            json.dump(layout, f, indent=2, ensure_ascii=False)

        # Verify file exists
        if os.path.exists(layout_path):
            file_size = os.path.getsize(layout_path)
            print(f"[OK] File saved successfully! Size: {file_size} bytes")
        else:
            print("[ERR] File NOT saved!")
            return jsonify({'error': 'File save failed'}), 500

        print("=" * 60)

        return jsonify({
            'success': True,
            'message': 'Layout saved successfully',
            'path': layout_path,
            'shelves_count': len(layout['shelves'])
        })

    except Exception as e:
        print(f"[ERR] ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@warehouse_bp.route('/api/warehouse/layout/load', methods=['GET'])
def load_warehouse_layout():
    """Load warehouse layout from JSON file"""
    try:
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        layout_path = os.path.join(app_dir, 'warehouse_layout.json')

        if not os.path.exists(layout_path):
            return jsonify({'error': 'No layout found'}), 404

        with open(layout_path, 'r') as f:
            layout = json.load(f)

        return jsonify(layout)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@warehouse_bp.route('/api/warehouse/layout/validate', methods=['POST'])
def validate_warehouse_layout():
    """Validate warehouse layout structure"""
    try:
        layout = request.json

        # Basic validation
        errors = []

        if 'shelves' not in layout:
            errors.append('Missing shelves array')
        elif not isinstance(layout['shelves'], list):
            errors.append('Shelves must be an array')
        else:
            # Check each shelf
            for i, shelf in enumerate(layout['shelves']):
                required = ['letter', 'x', 'y', 'shelfHeight', 'levels']
                for field in required:
                    if field not in shelf:
                        errors.append(f'Shelf {i}: missing {field}')

        if errors:
            return jsonify({'valid': False, 'errors': errors}), 400

        return jsonify({'valid': True, 'message': 'Layout is valid'})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# WAREHOUSE HEATMAP ROUTES
@warehouse_bp.route('/warehouse/shelves')
def warehouse_shelves_map():
    """Interaktywna mapa regalow — zoptymalizowana pod telefon.
    Uzywa WAREHOUSE_CONFIG (sekcje) jako zrodlo prawdy o ukladzie magazynu."""
    import json as _json
    from modules.warehouse_heatmap import get_heatmap_data, WAREHOUSE_CONFIG as WH_CFG

    heatmap = get_heatmap_data()
    shelves_data = heatmap.get('shelves', {})

    # Uzyj WAREHOUSE_CONFIG jako zrodla prawdy (nie warehouse_layout.json)
    wh_sections = WH_CFG.get('sections', {})
    wh_colors = WH_CFG.get('section_colors', {})
    config_shelves = WH_CFG.get('shelves', [])

    # Przygotuj dane regalow dla JS — TYLKO regaly z WAREHOUSE_CONFIG
    shelves_js = {}
    shelf_letters_ordered = []
    for rack_key in config_shelves:
        shelf_letters_ordered.append(rack_key)
        levels = shelves_data.get(rack_key, [])
        if not isinstance(levels, list):
            levels = []
        total = sum(lv.get('items', 0) for lv in levels)
        shelves_js[rack_key] = {
            'levels': levels,
            'total_items': total
        }

    # Grupuj wg sekcji z WAREHOUSE_CONFIG
    wall_groups = {}
    for sec_letter, sec_data in wh_sections.items():
        group_name = f"{sec_letter} \u2014 {sec_data['name']}"
        wall_groups[group_name] = sec_data['racks']

    total_shelves = len(config_shelves)
    total_items = sum(s['total_items'] for s in shelves_js.values())
    empty_shelves = sum(1 for s in shelves_js.values() if s['total_items'] == 0)
    occupied = total_shelves - empty_shelves

    # Pre-compute max_fill for each shelf (used by Jinja2 template)
    for rack_key, sdata in shelves_js.items():
        levels = sdata.get('levels', [])
        sdata['max_fill'] = max((lv.get('fill_percentage', 0) for lv in levels), default=0)

    shelves_json = _json.dumps(shelves_js, ensure_ascii=False)

    return render_template('warehouse_shelves.html',
        wall_groups=wall_groups,
        shelves_js=shelves_js,
        shelf_letters_ordered=shelf_letters_ordered,
        wh_colors=wh_colors,
        total_shelves=total_shelves,
        total_items=total_items,
        empty_shelves=empty_shelves,
        occupied=occupied,
        config_shelves=config_shelves,
        shelves_json=shelves_json
    )


@warehouse_bp.route('/warehouse/shelf/<code>')
def warehouse_shelf_view(code):
    """Widok regalu po zeskanowaniu QR — mobile friendly.
    /warehouse/shelf/A1 pokaze polki regalu A1 (A11, A12...).
    Klikniecie polki laduje produkty z API."""
    from modules.warehouse_heatmap import get_heatmap_data
    code = code.upper().strip()

    heatmap = get_heatmap_data()
    shelves_data = heatmap.get('shelves', {})

    # Szukaj dokladnego klucza (np. "A1") w shelves_data
    all_levels = []
    if code in shelves_data and isinstance(shelves_data[code], list):
        all_levels = shelves_data[code]
    else:
        # Fallback: szukaj po pierwszej literze
        for key, levels in shelves_data.items():
            if key == code and isinstance(levels, list):
                all_levels = levels
                break

    all_levels = sorted(all_levels, key=lambda l: l.get('level', 0))
    total_items = sum(lv.get('items', 0) for lv in all_levels)

    # Cyberpunk template
    levels_data = []
    for lv in all_levels:
        levels_data.append({
            'code': lv.get('code', '?'),
            'level': lv.get('level', 0),
            'items': lv.get('items', 0),
            'capacity': lv.get('capacity', 50),
            'color': lv.get('color', '#64748b'),
            'fill_percentage': lv.get('fill_percentage', 0),
        })
    return render_template('warehouse_shelf.html',
        code=code,
        levels=levels_data,
        total_items=total_items,
        brand_name='AKCES HUB',
        version='4.0',
        current_user=session.get('username', 'admin')
    )

    # OLD INLINE HTML (kept for reference, dead code below)
    _old_html = f'''<!DOCTYPE html><html lang="pl"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>Regal {code}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;padding-bottom:70px}}
.header{{background:#1e293b;padding:16px;text-align:center;border-bottom:1px solid #334155}}
.header h1{{font-size:2.5rem;font-weight:800;color:#22c55e}}
.header .sub{{color:#94a3b8;font-size:0.85rem;margin-top:4px}}
.count-badge{{display:inline-block;background:#22c55e22;color:#22c55e;padding:6px 16px;border-radius:20px;font-weight:700;font-size:0.9rem;margin:12px 0}}
.levels{{padding:12px}}
.level-card{{display:flex;align-items:center;gap:12px;padding:14px;background:#1e293b;border-radius:12px;margin-bottom:8px;cursor:pointer;border:2px solid #33415544}}
.level-card:active{{background:#334155}}
.level-badge{{width:48px;height:48px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:1.1rem;color:#fff;flex-shrink:0}}
.level-title{{flex:1}}
.level-title .name{{font-weight:700;font-size:1rem}}
.level-title .info{{font-size:0.75rem;color:#94a3b8;margin-top:2px}}
.level-arrow{{color:#475569;font-size:1.5rem}}
.products-panel{{display:none;background:#161e2e;border-radius:0 0 12px 12px;margin-top:-8px;margin-bottom:8px;padding:8px;overflow:hidden}}
.products-panel.open{{display:block}}
.prod-card{{display:flex;align-items:center;gap:10px;padding:10px;border-bottom:1px solid #33415533;text-decoration:none;color:inherit}}
.prod-card:active{{background:#334155}}
.prod-img{{width:45px;height:45px;border-radius:8px;object-fit:cover;background:#334155;flex-shrink:0}}
.prod-placeholder{{width:45px;height:45px;border-radius:8px;background:#334155;display:flex;align-items:center;justify-content:center;font-size:1rem;flex-shrink:0}}
.prod-info{{flex:1;min-width:0}}
.prod-name{{font-size:0.8rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.prod-meta{{font-size:0.65rem;color:#94a3b8}}
.prod-qty{{font-weight:800;color:#22c55e;font-size:0.95rem;flex-shrink:0}}
.empty{{text-align:center;padding:30px 20px;color:#64748b;font-size:0.85rem}}
.loading{{text-align:center;padding:20px;color:#64748b}}
.nav-bar{{position:fixed;bottom:0;left:0;right:0;background:#1e293b;border-top:1px solid #334155;padding:8px 12px;display:flex;gap:8px}}
.nav-bar a{{flex:1;text-align:center;padding:10px;border-radius:10px;text-decoration:none;color:#e2e8f0;font-size:0.75rem;font-weight:600;background:#334155}}
.nav-bar a.primary{{background:#7c3aed}}
</style></head><body>

<div class="header">
    <h1>Regal {code}</h1>
    <div class="sub">{len(all_levels)} polek</div>
    <div class="count-badge">{total_items} produktow</div>
</div>

<div class="levels">'''

    if not all_levels:
        html += '<div class="empty">Brak polek w tym regale</div>'
    else:
        for lv in all_levels:
            lv_code = lv.get('code', '?')
            lv_level = lv.get('level', 0)
            lv_items = lv.get('items', 0)
            lv_capacity = lv.get('capacity', 50)
            lv_color = lv.get('color', '#64748b')
            lv_fill = lv.get('fill_percentage', 0)
            status_text = f'{lv_items} szt' if lv_items > 0 else 'pusta'

            html += f'''<div class="level-card" onclick="toggleLevel(this, '{lv_code}')">
    <div class="level-badge" style="background:{lv_color}">{lv_level}</div>
    <div class="level-title">
        <div class="name">Polka {lv_code}</div>
        <div class="info">{status_text} / {lv_capacity} max</div>
    </div>
    <div class="level-arrow" id="arrow-{lv_code}">&#8250;</div>
</div>
<div class="products-panel" id="panel-{lv_code}"></div>'''

    html += '''</div>

<script>
var openPanels = {};

function toggleLevel(card, code) {
    var panel = document.getElementById("panel-" + code);
    var arrow = document.getElementById("arrow-" + code);

    if (panel.classList.contains("open")) {
        panel.classList.remove("open");
        arrow.style.transform = "";
        return;
    }

    arrow.style.transform = "rotate(90deg)";
    panel.classList.add("open");

    // Jesli juz zaladowane — nie laduj ponownie
    if (openPanels[code]) return;

    panel.innerHTML = '<div class="loading">Ladowanie...</div>';

    fetch("/api/warehouse/location/" + code)
        .then(function(r){return r.json()})
        .then(function(data) {
            var h = "";
            var products = data.products || [];
            if (products.length === 0) {
                h = '<div class="empty">Polka pusta</div>';
            } else {
                for (var i = 0; i < products.length; i++) {
                    var p = products[i];
                    var img = p.zdjecie_url
                        ? '<img class="prod-img" src="'+p.zdjecie_url+'" onerror="this.style.display=\\x27none\\x27">'
                        : '<div class="prod-placeholder">&#128230;</div>';
                    var name = (p.nazwa || "Brak nazwy").substring(0, 60);
                    var ean = p.ean || p.asin || "";
                    h += '<a href="/magazyn/produkt/'+p.id+'" class="prod-card">';
                    h += img;
                    h += '<div class="prod-info"><div class="prod-name">'+name+'</div>';
                    h += '<div class="prod-meta">'+ean+'</div></div>';
                    h += '<div class="prod-qty">'+(p.ilosc||0)+' szt</div>';
                    h += '</a>';
                }
            }
            panel.innerHTML = h;
            openPanels[code] = true;
        })
        .catch(function(err) {
            panel.innerHTML = '<div class="empty">Blad: '+err+'</div>';
        });
}
</script>

<div class="nav-bar">
    <a href="/warehouse/shelves">Mapa</a>
    <a href="/magazyn" class="primary">Magazyn</a>
</div>
</body></html>'''

    return html


@warehouse_bp.route('/warehouse/print-labels')
def warehouse_print_labels():
    """Drukuje kartki z QR kodami do kazdego regalu/polki"""
    from modules.warehouse_heatmap import get_heatmap_data, WAREHOUSE_CONFIG as WH_CFG

    heatmap = get_heatmap_data()
    shelves_data = heatmap.get('shelves', {})
    # Uzyj TYLKO regalow z WAREHOUSE_CONFIG (nie z warehouse_layout.json)
    config_shelves = WH_CFG.get('shelves', [])
    # Użyj ngrok domeny jeśli ustawiona w config, inaczej request.host_url
    from modules.database import get_config
    ngrok_domain = get_config('ngrok_domain', '')
    base_url = f"https://{ngrok_domain}" if ngrok_domain else request.host_url.rstrip('/')

    html = '''<!DOCTYPE html><html lang="pl"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Kartki do regalow — Druk</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',sans-serif;background:#fff;color:#000}
@media screen {
    body{background:#0f172a;color:#e2e8f0;padding:20px}
    .no-print{display:block}
    .label-card{background:#1e293b;color:#e2e8f0;border:2px solid #334155}
}
@media print {
    .no-print{display:none !important}
    body{background:#fff;padding:0}
    .label-card{break-inside:avoid;border:2px solid #000}
}
.no-print{text-align:center;margin-bottom:20px}
.no-print h1{font-size:1.5rem;margin-bottom:10px;color:#e2e8f0}
.no-print button{padding:12px 30px;background:#7c3aed;color:#fff;border:none;border-radius:10px;font-size:1rem;font-weight:700;cursor:pointer}
.no-print button:active{transform:scale(0.95)}
.no-print a{color:#94a3b8;text-decoration:none;display:inline-block;margin:10px}
.controls{display:flex;gap:8px;justify-content:center;margin:12px 0;flex-wrap:wrap}
.controls label{background:#334155;padding:6px 12px;border-radius:8px;font-size:0.8rem;cursor:pointer;user-select:none}
.controls input[type=checkbox]{margin-right:4px}
.labels-grid{padding:10px}
.label-card{border-radius:16px;padding:40px 20px;text-align:center;page-break-after:always;min-height:90vh;display:flex;flex-direction:column;align-items:center;justify-content:center}
.label-card:last-child{page-break-after:auto}
.label-card .shelf-name{font-size:8rem;font-weight:900;margin-bottom:10px;line-height:1}
.label-card .shelf-sub{font-size:1.5rem;color:#666;margin-bottom:30px}
.label-card .qr-placeholder{margin:20px auto;width:300px;height:300px;display:flex;align-items:center;justify-content:center}
.label-card .qr-placeholder img{width:300px;height:300px}
@media print{
    .label-card .shelf-name{color:#000}
    .label-card .shelf-sub{color:#333}
    .label-card{border:3px solid #000;min-height:95vh}
}
</style></head><body>

<div class="no-print">
    <h1>Kartki z QR kodami do regalow</h1>
    <p style="color:#94a3b8;margin-bottom:12px">Wydrukuj i przyklej do regalu. Zeskanuj telefonem — zobaczysz co jest na polce.</p>
    <div class="controls" id="shelfFilter"></div>
    <button onclick="window.print()">Drukuj</button>
    <br><a href="/warehouse/shelves">Wroc do mapy</a>
</div>

<div class="labels-grid" id="labelsGrid">'''

    # ===== STRONA 1: LEGENDA DYNAMICZNA (z WAREHOUSE_CONFIG) =====
    from modules.warehouse_heatmap import WAREHOUSE_CONFIG as WH_CFG
    sections = WH_CFG.get('sections', {})
    section_colors = WH_CFG.get('section_colors', {})

    legend_rows = ''
    for sec_letter, sec_data in sections.items():
        color = section_colors.get(sec_letter, '#666')
        racks_list = ', '.join(sec_data['racks'])
        legend_rows += f'''<tr style="border-bottom:2px solid #ddd">
            <td style="padding:14px;font-weight:900;font-size:2rem;color:{color}">{sec_letter}</td>
            <td style="padding:14px;font-size:1.2rem"><b>{sec_data['name']}</b></td>
            <td style="padding:14px;font-size:1.2rem">{racks_list}</td>
            <td style="padding:14px;font-size:1.2rem;text-align:center;font-weight:700">{len(sec_data['racks'])}</td>
        </tr>'''

    total_racks = sum(len(s['racks']) for s in sections.values())

    html += f'''<div class="label-card legend-card" data-shelf="LEGENDA" style="text-align:left;padding:40px 50px">
    <div style="text-align:center;margin-bottom:30px">
        <div style="font-size:4rem;font-weight:900;letter-spacing:2px">MAGAZYN</div>
        <div style="font-size:1.3rem;color:#666;margin-top:5px">Rozklad regalow — co jest gdzie</div>
        <div style="font-size:1.1rem;color:#999;margin-top:5px">Lacznie: {total_racks} regalow</div>
    </div>
    <table style="width:100%;border-collapse:collapse;font-size:1.3rem;margin-top:20px">
        <tr style="border-bottom:3px solid #000">
            <th style="padding:12px;text-align:left;font-size:1.5rem">Sekcja</th>
            <th style="padding:12px;text-align:left;font-size:1.5rem">Lokalizacja</th>
            <th style="padding:12px;text-align:left;font-size:1.5rem">Regaly</th>
            <th style="padding:12px;text-align:center;font-size:1.5rem">Ile</th>
        </tr>
        {legend_rows}
    </table>
    <div style="text-align:center;margin-top:40px;font-size:1rem;color:#999">
        Zeskanuj QR kod na regale telefonem &mdash; zobaczysz co jest na polkach
    </div>
</div>'''

    # ===== KARTKI PER REGAL (tylko z WAREHOUSE_CONFIG) =====
    all_rack_keys = config_shelves
    for rack_key in all_rack_keys:
        levels = shelves_data.get(rack_key, [])
        total_items = sum(lv.get('items', 0) for lv in levels) if isinstance(levels, list) else 0
        n_levels = len(levels) if isinstance(levels, list) else 0
        url = f"{base_url}/warehouse/shelf/{rack_key}"

        html += f'''<div class="label-card" data-shelf="{rack_key}">
    <div class="shelf-name">{rack_key}</div>
    <div class="shelf-sub">Regal {rack_key} &middot; {n_levels} polek</div>
    <div class="qr-placeholder" data-url="{url}"></div>
</div>'''

    html += '</div>'

    # JS: lokalny generator QR (bez zewnetrznego serwera)
    shelf_letters_js = str(all_rack_keys)
    html += f'''
<script src="https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.min.js" integrity="sha384-lQXOAyZwHXE55JFyrOMB7nY2Wv+m5ZWNtJcHrd1rceRQXAYNLak8ukN5TjBTcIwz" crossorigin="anonymous"></script>
<script>
// Generuj QR kody lokalnie
document.querySelectorAll('.qr-placeholder').forEach(function(el) {{
    var url = el.dataset.url;
    if (!url) return;
    var qr = qrcode(0, 'M');
    qr.addData(url);
    qr.make();
    el.innerHTML = qr.createSvgTag(8, 0);
    // Ustaw rozmiar SVG
    var svg = el.querySelector('svg');
    if (svg) {{
        svg.style.width = '280px';
        svg.style.height = '280px';
    }}
}});

const allShelves = ["LEGENDA"].concat({shelf_letters_js});
var filterDiv = document.getElementById('shelfFilter');
allShelves.forEach(function(s) {{
    var lbl = document.createElement('label');
    var label = s === 'LEGENDA' ? 'Legenda' : 'Regal '+s;
    lbl.innerHTML = '<input type="checkbox" checked onchange="filterCards()" value="'+s+'"> '+label;
    filterDiv.appendChild(lbl);
}});

function filterCards() {{
    var checked = [];
    document.querySelectorAll('#shelfFilter input:checked').forEach(function(i){{checked.push(i.value)}});
    document.querySelectorAll('.label-card').forEach(function(card) {{
        card.style.display = checked.indexOf(card.dataset.shelf) >= 0 ? '' : 'none';
    }});
}}
</script>
</body></html>'''

    return html


# ============================================================
# BOX / SHELF LABEL — druk
# ============================================================
@warehouse_bp.route('/warehouse/box-label/<code>')
def box_label(code):
    """Printable label for a shelf/box — lists products at that location."""
    from modules.database import get_db, get_config
    code = code.upper().strip()

    conn = get_db()
    # Find all products whose lokalizacja starts with the given code
    pattern = f'{code}%'
    products = conn.execute(
        '''SELECT id, nazwa, ilosc, ean, asin, lokalizacja
           FROM produkty
           WHERE lokalizacja LIKE ?
             AND status IN ('magazyn','wystawiony')
           ORDER BY lokalizacja, nazwa''',
        (pattern,)
    ).fetchall()

    total_items = sum((p['ilosc'] or 0) for p in products)

    prod_rows = ''
    for idx, pr in enumerate(products, 1):
        name = (pr['nazwa'] or 'Brak nazwy')[:35]
        qty = pr['ilosc'] or 0
        ean = pr['ean'] or pr['asin'] or ''
        loc = pr['lokalizacja'] or ''
        prod_rows += f'''<tr>
            <td style="padding:3px 6px;border-bottom:1px solid #ccc;text-align:center;font-size:0.8rem">{idx}</td>
            <td style="padding:3px 6px;border-bottom:1px solid #ccc;font-size:0.8rem">{name}</td>
            <td style="padding:3px 6px;border-bottom:1px solid #ccc;text-align:center;font-weight:700">{qty}</td>
            <td style="padding:3px 6px;border-bottom:1px solid #ccc;font-size:0.7rem">{ean}</td>
            <td style="padding:3px 6px;border-bottom:1px solid #ccc;font-size:0.75rem">{loc}</td>
        </tr>'''

    ngrok_domain = get_config('ngrok_domain', '')
    base_url = ngrok_domain and f"https://{ngrok_domain}" or request.host_url.rstrip('/')
    qr_url = f"{base_url}/warehouse/shelf/{code}"

    html = f'''<!DOCTYPE html><html lang="pl"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Etykieta — {code}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',Arial,sans-serif;background:#fff;color:#000;padding:20px}}
@media screen {{
    body{{background:#0f172a;color:#e2e8f0}}
    .print-page{{background:#fff;color:#000;border-radius:12px;padding:30px;max-width:800px;margin:0 auto}}
}}
@media print {{
    .no-print{{display:none !important}}
    body{{padding:10px}}
    .print-page{{padding:10px}}
}}
.no-print{{text-align:center;margin-bottom:16px}}
.no-print button{{padding:10px 28px;background:#7c3aed;color:#fff;border:none;border-radius:10px;font-size:1rem;font-weight:700;cursor:pointer;margin:4px}}
.no-print a{{color:#94a3b8;text-decoration:none;margin:0 10px;font-size:0.9rem}}
.shelf-code{{font-size:7rem;font-weight:900;text-align:center;margin:10px 0;line-height:1}}
.qr-box{{text-align:center;margin:10px 0}}
.qr-box svg{{width:160px;height:160px}}
table{{width:100%;border-collapse:collapse;font-size:0.85rem;margin-top:10px}}
th{{background:#f0f0f0;padding:5px 6px;text-align:left;border-bottom:2px solid #333;font-weight:700;font-size:0.8rem}}
.totals{{margin-top:10px;font-size:1rem;font-weight:700;text-align:center}}
.totals span{{background:#f0f0f0;padding:6px 14px;border-radius:8px}}
</style>
</head><body>

<div class="no-print">
    <button onclick="window.print()">Drukuj</button>
    <a href="/warehouse/shelf/{code}">Wróć do regału</a>
    <a href="/warehouse/shelves">Mapa</a>
</div>

<div class="print-page">
    <div class="shelf-code">{code}</div>
    <div class="qr-box" id="qrBox" data-url="{qr_url}"></div>

    <div class="totals"><span>{total_items} szt. &nbsp;|&nbsp; {len(products)} produktów</span></div>

    <table>
        <tr><th>#</th><th>Produkt</th><th>Szt.</th><th>EAN/ASIN</th><th>Lok.</th></tr>
        {prod_rows}
    </table>
</div>

<script src="https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.min.js" integrity="sha384-lQXOAyZwHXE55JFyrOMB7nY2Wv+m5ZWNtJcHrd1rceRQXAYNLak8ukN5TjBTcIwz" crossorigin="anonymous"></script>
<script>
(function(){{
    var box = document.getElementById('qrBox');
    var url = box.dataset.url;
    if(!url) return;
    var qr = qrcode(0, 'M');
    qr.addData(url);
    qr.make();
    box.innerHTML = qr.createSvgTag(5, 0);
    var svg = box.querySelector('svg');
    if(svg){{ svg.style.width='160px'; svg.style.height='160px'; }}
}})();
</script>

</body></html>'''

    return html


@warehouse_bp.route('/warehouse/heatmap')
def warehouse_heatmap_view():
    """Strona główna z 3D heatmapą magazynu"""
    return render_template('warehouse_heatmap.html')


@warehouse_bp.route('/api/warehouse/heatmap')
def api_warehouse_heatmap():
    """API endpoint - dane dla heatmapy"""
    try:
        from modules.warehouse_heatmap import get_heatmap_data
        data = get_heatmap_data()
        return jsonify(data)
    except Exception as e:
        print(f"[ERR] Error getting heatmap data: {e}")
        return jsonify({'error': str(e)}), 500


@warehouse_bp.route('/api/warehouse/locations')
def api_warehouse_locations():
    """API endpoint - lista wszystkich lokalizacji"""
    try:
        from modules.warehouse_heatmap import get_all_locations
        locations = get_all_locations()
        return jsonify({
            'locations': [
                {
                    'code': loc.code,
                    'shelf': loc.shelf,
                    'level': loc.level,
                    'section': loc.section,
                    'items': loc.items_count,
                    'capacity': loc.capacity,
                    'fill_percentage': round(loc.fill_percentage * 100, 1),
                    'status': loc.fill_status,
                    'color': loc.color
                }
                for loc in locations
            ]
        })
    except Exception as e:
        print(f"[ERR] Error getting locations: {e}")
        return jsonify({'error': str(e)}), 500


@warehouse_bp.route('/api/warehouse/location/<location_code>')
def api_warehouse_location_details(location_code):
    """API endpoint - szczegóły konkretnej lokalizacji"""
    try:
        from modules.warehouse_heatmap import get_location_details
        details = get_location_details(location_code)

        if not details:
            return jsonify({'error': 'Location not found'}), 404

        return jsonify(details)
    except Exception as e:
        print(f"[ERR] Error getting location details: {e}")
        return jsonify({'error': str(e)}), 500


@warehouse_bp.route('/api/warehouse/assign', methods=['POST'])
def api_warehouse_assign_product():
    """API endpoint - przypisz produkt do lokalizacji"""
    try:
        from modules.warehouse_heatmap import assign_product_to_location

        data = request.get_json()
        product_id = data.get('product_id')
        location_code = data.get('location_code')
        quantity = data.get('quantity', 1)
        notes = data.get('notes')

        if not product_id or not location_code:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400

        success = assign_product_to_location(
            product_id=product_id,
            location_code=location_code,
            quantity=quantity,
            notes=notes
        )

        if success:
            return jsonify({'success': True, 'message': 'Product assigned successfully'})
        else:
            return jsonify({'success': False, 'error': 'Failed to assign product'}), 500

    except Exception as e:
        print(f"[ERR] Error assigning product: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@warehouse_bp.route('/api/warehouse/remove', methods=['POST'])
def api_warehouse_remove_product():
    """API endpoint - usuń produkt z lokalizacji"""
    try:
        from modules.warehouse_heatmap import remove_product_from_location

        data = request.get_json()
        product_id = data.get('product_id')
        location_code = data.get('location_code')

        if not product_id:
            return jsonify({'success': False, 'error': 'Missing product_id'}), 400

        success = remove_product_from_location(
            product_id=product_id,
            location_code=location_code
        )

        if success:
            return jsonify({'success': True, 'message': 'Product removed successfully'})
        else:
            return jsonify({'success': False, 'error': 'Failed to remove product'}), 500

    except Exception as e:
        print(f"[ERR] Error removing product: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@warehouse_bp.route('/api/warehouse/empty')
def api_warehouse_empty_locations():
    """API endpoint - znajdź puste lokalizacje"""
    try:
        from modules.warehouse_heatmap import find_empty_locations

        min_capacity = request.args.get('min_capacity', 1, type=int)
        locations = find_empty_locations(min_capacity=min_capacity)

        return jsonify({
            'empty_locations': locations,
            'count': len(locations)
        })
    except Exception as e:
        print(f"[ERR] Error finding empty locations: {e}")
        return jsonify({'error': str(e)}), 500


@warehouse_bp.route('/api/warehouse/stats')
def api_warehouse_stats():
    """API endpoint - statystyki magazynu"""
    try:
        from modules.warehouse_heatmap import get_location_stats
        stats = get_location_stats()
        return jsonify(stats)
    except Exception as e:
        print(f"[ERR] Error getting warehouse stats: {e}")
        return jsonify({'error': str(e)}), 500


@warehouse_bp.route('/api/warehouse/search-product')
def api_warehouse_search_product():
    """API endpoint - wyszukiwanie produktu po nazwie/EAN/ASIN"""
    try:
        query = request.args.get('q', '').strip()

        if not query or len(query) < 2:
            return jsonify({'results': []})

        from modules.database import get_db
        conn = get_db()

        # Szukaj po nazwie, EAN, ASIN
        search_pattern = f'%{query}%'

        results = conn.execute('''
            SELECT id, nazwa, ean, asin, lokalizacja, ilosc, cena_allegro, dostawca, zdjecie_url, kod_magazynowy
            FROM produkty
            WHERE (
                UPPER(nazwa) LIKE UPPER(?)
                OR UPPER(ean) LIKE UPPER(?)
                OR UPPER(asin) LIKE UPPER(?)
                OR UPPER(kod_magazynowy) LIKE UPPER(?)
            )
            AND lokalizacja IS NOT NULL
            AND lokalizacja != ''
            ORDER BY
                CASE WHEN UPPER(nazwa) LIKE UPPER(?) THEN 1 ELSE 2 END,
                id DESC
            LIMIT 10
        ''', (search_pattern, search_pattern, search_pattern, search_pattern, search_pattern)).fetchall()

        products = []
        for row in results:
            products.append({
                'id': row[0],
                'nazwa': row[1],
                'ean': row[2],
                'asin': row[3],
                'lokalizacja': row[4],
                'ilosc': row[5],
                'cena_allegro': row[6],
                'dostawca': row[7],
                'zdjecie_url': row[8],
                'kod_magazynowy': row[9]
            })

        return jsonify({'results': products, 'query': query})

    except Exception as e:
        print(f"[ERR] Error searching product: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'results': []}), 500
