"""
Magazynier module - zarządzanie stanami magazynowymi
"""

import os
import io
import csv
import json
import tempfile
from datetime import datetime
from flask import Blueprint, render_template_string, request, redirect, Response, url_for, session, current_app

from .database import get_db, query_db, execute_db, get_config, get_config_cached
from .utils import get_amazon_image_url, get_product_image, oblicz_cene_allegro, is_code, DOSTAWCY, detect_supplier, parse_price, ALLEGRO_PROWIZJE

magazynier_bp = Blueprint('magazynier', __name__)

# ============================================================
# FUNKCJE POMOCNICZE
# ============================================================
def get_product_code(p):
    """Zwraca kod_magazynowy (MAG-XXXXX) do użycia w URL. Fallback: ID."""
    if hasattr(p, 'keys'):
        p = dict(p)
    kod = p.get('kod_magazynowy', '') or ''
    if kod:
        return kod
    pid = p.get('id')
    if pid:
        return str(pid)
    return ''

def get_produkt_by_code(conn, code):
    """Szukaj produktu po: kod_magazynowy (MAG-xxx), ID (krótki numer), EAN/ASIN."""
    c = str(code).strip()
    # 1. kod_magazynowy (MAG-00123)
    if c.upper().startswith('MAG-'):
        r = conn.execute('SELECT * FROM produkty WHERE kod_magazynowy=?', (c.upper(),)).fetchone()
        if r:
            return r
    # 2. Krótki numer (<=6 cyfr) → szukaj po ID
    if c.isdigit() and len(c) <= 6:
        r = conn.execute('SELECT * FROM produkty WHERE id=?', (int(c),)).fetchone()
        if r:
            return r
    # 3. EAN/ASIN — może się powtarzać, bierzemy z największą ilością
    return conn.execute(
        "SELECT * FROM produkty WHERE (ean=? OR asin=?) AND status != 'sprzedany' ORDER BY ilosc DESC",
        (c, c)).fetchone()


def _paleta_koszt_szt(conn, paleta_id):
    """Koszt brutto/szt = paleta.cena_zakupu (brutto) / łączna ilość sztuk."""
    if not paleta_id:
        return 0
    row = conn.execute('''
        SELECT pal.cena_zakupu,
            COALESCE((SELECT SUM(pr.ilosc) FROM produkty pr WHERE pr.paleta_id = pal.id), 0)
            + COALESCE((SELECT SUM(pr.sprzedano_offline) FROM produkty pr WHERE pr.paleta_id = pal.id), 0)
            + COALESCE((SELECT SUM(sp.ilosc) FROM sprzedaze sp
                JOIN produkty pp ON sp.produkt_id = pp.id
                WHERE pp.paleta_id = pal.id
                AND sp.status NOT IN ('zwrot','anulowane','anulowana')), 0)
            as ilosc_sztuk
        FROM palety pal WHERE pal.id = ?
    ''', (paleta_id,)).fetchone()
    if row and row['cena_zakupu'] and row['ilosc_sztuk'] and row['ilosc_sztuk'] > 0:
        return round(row['cena_zakupu'] / row['ilosc_sztuk'], 2)
    return 0


_mag_stats_cache = {'data': None, 'time': 0}

def get_stats():
    """Zwraca statystyki magazynu (cached 30s)"""
    import time as _time
    now = _time.time()
    if _mag_stats_cache['data'] and (now - _mag_stats_cache['time']) < 30:
        return _mag_stats_cache['data']
    conn = get_db()
    # Filtr: status IN ('magazyn','wystawiony') — spójnie z KPI dashboard
    _w_statuses = ('magazyn', 'wystawiony')
    stats = {
        'produkty': conn.execute('SELECT COUNT(*) FROM produkty WHERE status IN (?,?)', _w_statuses).fetchone()[0],
        'sztuki': conn.execute('SELECT COALESCE(SUM(ilosc),0) FROM produkty WHERE status IN (?,?)', _w_statuses).fetchone()[0],
        'palety': conn.execute('SELECT COUNT(DISTINCT paleta) FROM produkty WHERE paleta!="" AND status IN (?,?)', _w_statuses).fetchone()[0],
        'dostawcy': conn.execute('SELECT COUNT(DISTINCT dostawca) FROM produkty WHERE dostawca!="" AND status IN (?,?)', _w_statuses).fetchone()[0],
        # Wartość zakupu = avg koszt/szt z palet × sztuki w magazynie
        'wartosc_zakupu': 0,  # obliczone poniżej
        'wartosc_netto': 0,   # obliczone poniżej
        'wartosc_allegro': round(conn.execute('SELECT COALESCE(SUM(cena_allegro*ilosc),0) FROM produkty WHERE status IN (?,?)', _w_statuses).fetchone()[0] or 0, 2),
    }
    # Średni koszt/szt — identycznie jak w KPI dashboard (analytics.py)
    # total_items = aktualne w magazynie + sprzedane przez sprzedaze
    # Zoptymalizowane: JOIN zamiast correlated subqueries
    avg = conn.execute('''
        SELECT SUM(pal.cena_zakupu) as total_koszt,
        COALESCE(SUM(mag.mag_szt), 0) + COALESCE(SUM(spr.spr_szt), 0) as total_items
        FROM palety pal
        LEFT JOIN (
            SELECT paleta_id, SUM(CASE WHEN status NOT IN ('sprzedany','wyslany') THEN ilosc ELSE 0 END) as mag_szt
            FROM produkty GROUP BY paleta_id
        ) mag ON mag.paleta_id = pal.id
        LEFT JOIN (
            SELECT pp.paleta_id, SUM(sp.ilosc) as spr_szt
            FROM sprzedaze sp
            JOIN produkty pp ON sp.produkt_id = pp.id
            WHERE sp.status NOT IN ('zwrot','anulowane','anulowana')
            GROUP BY pp.paleta_id
        ) spr ON spr.paleta_id = pal.id
        WHERE pal.cena_zakupu > 0
    ''').fetchone()
    total_koszt = avg['total_koszt'] or 0
    total_items = avg['total_items'] or 1
    avg_cost = total_koszt / total_items if total_items > 0 else 0
    brutto = avg_cost * stats['sztuki']
    stats['wartosc_zakupu'] = round(brutto, 2)
    stats['wartosc_netto'] = round(brutto / 1.23, 2)
    _mag_stats_cache['data'] = stats
    _mag_stats_cache['time'] = _time.time()
    return stats

# ============================================================
# SZABLONY
# ============================================================
_MAGAZYNIER_CSS = '''
/* Magazynier module-specific styles using base.html CSS variables */
.hdr{text-align:center;padding:15px 0;border-bottom:1px solid var(--border);margin-bottom:15px}
.hdr h1{font-size:1.5rem;color:var(--accent)}
.hdr small{color:var(--text-muted);font-size:0.8rem}
.stat-v{font-size:1.4rem;font-weight:700;color:var(--accent)}
.stat-v.green{color:var(--green)}
.stat-l{font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;margin-top:4px}
.btn-p{background:linear-gradient(135deg,var(--accent),var(--accent2))}
.btn-ok{background:var(--green)}
.btn-2{background:var(--bg);border:1px solid var(--border);color:var(--text)}
.btn-warn{background:var(--yellow);color:#000}
.btn-err{background:var(--red)}
.search{background:var(--bg-card);border:1px solid var(--border);border-radius:12px;padding:14px;margin-bottom:15px}
.search form{display:flex;gap:10px}
.search input{flex:1;padding:14px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:1rem;transition:border-color 0.3s}
.search input:focus{outline:none;border-color:var(--accent)}
.search button{padding:14px 20px;background:linear-gradient(135deg,var(--accent),var(--accent2));border:none;border-radius:var(--radius-sm);color:#fff;font-size:1.2rem;cursor:pointer;transition:all 0.2s}
.search button:hover{transform:scale(1.05)}
.items-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.item{display:flex;align-items:center;background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:12px;margin-bottom:8px;text-decoration:none;color:var(--text);transition:all 0.3s;box-shadow:var(--shadow)}
.item:hover{border-color:var(--accent);transform:translateX(4px)}
.item img{width:50px;height:50px;object-fit:contain;background:#fff;border-radius:6px;margin-right:12px}
.item-info{flex:1;min-width:0}
.item-name{font-weight:600;font-size:0.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item-meta{font-size:0.75rem;color:var(--text-muted)}
.item-right{text-align:right;margin-left:10px}
.item-qty{font-size:1.2rem;font-weight:700;color:var(--accent)}
.item-price{font-size:0.75rem;color:var(--green)}
.card-img{width:100%;max-height:250px;object-fit:contain;background:#fff;padding:10px}
.card-body{padding:15px}
.card-name{font-size:1.15rem;font-weight:600;margin-bottom:12px}
.loc{background:var(--bg);border:2px solid var(--accent);border-radius:var(--radius-sm);padding:12px;margin-bottom:12px}
.loc-title{font-size:0.75rem;color:var(--accent);text-transform:uppercase;margin-bottom:8px}
.loc-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;text-align:center}
.loc-v{font-size:1.1rem;font-weight:700;color:var(--green)}
.loc-l{font-size:0.65rem;color:var(--text-muted)}
.det-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
.det{background:var(--bg);padding:12px;border-radius:8px;transition:all 0.2s}
.det:hover{background:var(--bg-card)}
.det-l{font-size:0.7rem;color:var(--text-muted)}
.det-v{font-size:0.95rem;font-weight:600;margin-top:2px}
.det-v.green{color:var(--green)}
.badge-ok{background:var(--green-soft);color:var(--green)}
.badge-err{background:var(--red-soft);color:var(--red)}
.form-ctrl,.form-input{width:100%;padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:1rem;transition:border-color 0.3s}
.form-ctrl:focus,.form-input:focus{outline:none;border-color:var(--accent)}
.form-row-3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
.alert-ok{background:var(--green-soft);border:1px solid rgba(34,197,94,0.3);color:var(--green)}
.alert-warn{background:var(--yellow-soft);border:1px solid rgba(234,179,8,0.3);color:var(--yellow)}
.alert-err{background:var(--red-soft);border:1px solid rgba(239,68,68,0.3);color:var(--red)}
.section{color:var(--accent);font-weight:600;font-size:0.9rem;margin:18px 0 12px;display:flex;align-items:center;gap:8px}
.quick-btn{padding:10px 5px;font-size:0.75rem;border-radius:8px;border:none;cursor:pointer;font-weight:600;color:#fff;transition:all 0.2s;text-decoration:none;display:block;text-align:center}
.quick-btn:hover{transform:translateY(-2px);box-shadow:0 4px 8px rgba(0,0,0,0.3)}
/* Timeline / Historia */
.timeline{position:relative;padding-left:25px;margin:15px 0}
.timeline::before{content:'';position:absolute;left:8px;top:0;bottom:0;width:2px;background:var(--border)}
.timeline-item{position:relative;padding:10px 0 10px 15px;border-bottom:1px solid var(--border)}
.timeline-item:last-child{border-bottom:none}
.timeline-item::before{content:'';position:absolute;left:-21px;top:14px;width:12px;height:12px;border-radius:50%;background:var(--accent);border:2px solid var(--bg)}
.timeline-item.green::before{background:var(--green)}
.timeline-item.yellow::before{background:var(--yellow)}
.timeline-item.purple::before{background:var(--purple)}
.timeline-date{font-size:0.7rem;color:var(--text-muted)}
.timeline-text{font-size:0.85rem;margin-top:2px}
/* Toast Notifications */
.toast-container{position:fixed;top:80px;right:20px;z-index:1000;display:flex;flex-direction:column;gap:10px;max-width:400px}
.toast{background:var(--bg-card);border:1px solid var(--border);border-radius:12px;padding:16px;box-shadow:var(--shadow-lg);display:flex;align-items:start;gap:12px;animation:slideInRight 0.3s ease-out;min-width:300px}
.toast.success{border-left:4px solid var(--green)}
.toast.error{border-left:4px solid var(--red)}
.toast.warning{border-left:4px solid var(--yellow)}
.toast.info{border-left:4px solid var(--blue)}
.toast-icon{font-size:1.5rem;flex-shrink:0}
.toast-content{flex:1}
.toast-title{font-weight:600;margin-bottom:4px;font-size:0.95rem}
.toast-message{font-size:0.85rem;color:var(--text-muted)}
.toast-close{cursor:pointer;color:var(--text-muted);font-size:1.2rem;flex-shrink:0;transition:color 0.2s}
.toast-close:hover{color:var(--red)}
.toast.removing{animation:slideOutRight 0.3s ease-in forwards}
/* Loading Overlay */
.loading-overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);backdrop-filter:blur(4px);z-index:999;display:flex;align-items:center;justify-content:center}
.loading-spinner{width:60px;height:60px;border:4px solid var(--border);border-top:4px solid var(--accent);border-radius:50%;animation:spin 0.8s linear infinite}
.loading-text{color:#fff;margin-top:20px;font-size:1.1rem;font-weight:600;text-align:center}
/* Animations */
@keyframes slideDown{from{opacity:0;transform:translateY(-20px)}to{opacity:1;transform:translateY(0)}}
@keyframes slideInRight{from{opacity:0;transform:translateX(100px)}to{opacity:1;transform:translateX(0)}}
@keyframes slideOutRight{to{opacity:0;transform:translateX(100px)}}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes bounce{0%,100%{transform:translateY(0)}50%{transform:translateY(-10px)}}
/* Responsive */
@media(min-width:1200px){.items-grid{grid-template-columns:repeat(3,1fr)}}
@media(max-width:900px){.toast-container{right:10px;max-width:320px}.toast{min-width:280px}}
@media(max-width:768px){.items-grid{grid-template-columns:1fr}.form-row{grid-template-columns:1fr}.quick-actions{grid-template-columns:repeat(2,1fr)}.toast-container{top:70px;right:10px;left:10px;max-width:none}.toast{min-width:auto}}
'''

_MAGAZYNIER_JS = '''
// Toast Notifications System
function showToast(title, message, type='info', duration=3000){
    let container=document.getElementById('toast-container');
    if(!container){container=document.createElement('div');container.id='toast-container';container.className='toast-container';document.body.appendChild(container);}
    const toast=document.createElement('div');
    toast.className='toast '+type;
    const icons={success:'\\u2705',error:'\\u274c',warning:'\\u26a0\\ufe0f',info:'\\u2139\\ufe0f'};
    const icon=icons[type]||'\\u2139\\ufe0f';
    toast.innerHTML='<div class="toast-icon">'+icon+'</div><div class="toast-content"><div class="toast-title">'+title+'</div>'+(message?'<div class="toast-message">'+message+'</div>':'')+'</div><div class="toast-close" onclick="removeToast(this.parentElement)">&times;</div>';
    container.appendChild(toast);
    if(duration>0){setTimeout(function(){removeToast(toast)},duration);}
    return toast;
}
function removeToast(toast){toast.classList.add('removing');setTimeout(function(){toast.remove()},300);}

// Loading Overlay System
function showLoading(text){
    text=text||'Ladowanie...';
    if(document.getElementById('loading-overlay'))return;
    var overlay=document.createElement('div');
    overlay.id='loading-overlay';overlay.className='loading-overlay';
    overlay.innerHTML='<div style="text-align:center"><div class="loading-spinner"></div><div class="loading-text">'+text+'</div></div>';
    document.body.appendChild(overlay);
}
function hideLoading(){var o=document.getElementById('loading-overlay');if(o){o.style.animation='fadeOut 0.2s ease-out';setTimeout(function(){o.remove()},200);}}

// Auto-convert URL msg params to toasts
window.addEventListener('DOMContentLoaded', function(){
    var params=new URLSearchParams(window.location.search);
    var msg=params.get('msg');
    if(msg){
        var decoded=decodeURIComponent(msg.replace(/\\+/g,' '));
        var type=(decoded.indexOf('Blad')>=0||decoded.indexOf('\\u274c')>=0)?'error':'success';
        showToast('', decoded, type);
        var url=new URL(window.location);url.searchParams.delete('msg');
        window.history.replaceState({}, '', url);
    }
});
'''

_MAGAZYNIER_TEMPLATE = '''{% extends "base.html" %}
{% block page_title %}{{ page_title }}{% endblock %}
{% block content %}
<style>{{ magazynier_css|safe }}</style>
<div class="toast-container" id="toast-container"></div>
{{ content_html|safe }}
<script>{{ magazynier_js|safe }}</script>
{% endblock %}'''

def render(content, page_title='Magazynier'):
    return render_template_string(
        _MAGAZYNIER_TEMPLATE,
        content_html=content,
        page_title=page_title,
        magazynier_css=_MAGAZYNIER_CSS,
        magazynier_js=_MAGAZYNIER_JS,
        version=current_app.config.get('VERSION', ''),
        brand_name=current_app.config.get('BRAND_NAME', 'Akces Hub'),
        current_user=session.get('user')
    )

# ============================================================
# ROUTES
# ============================================================
@magazynier_bp.route('/')
def index():
    s = get_stats()
    conn = get_db()
    products = conn.execute('SELECT * FROM produkty ORDER BY data_dodania DESC LIMIT 10').fetchall()
    
    # Helper do renderowania kafelka
    def tile(href, icon, label, value, bg='#1e293b', border='', valcolor='#3b82f6'):
        bdr = f'border:1px solid {border};' if border else ''
        return f'''<a href="{href}" style="display:flex;flex-direction:column;align-items:center;justify-content:center;padding:14px 8px;background:{bg};{bdr}border-radius:12px;color:#fff;text-decoration:none;gap:4px;transition:transform 0.15s,box-shadow 0.15s" onmouseover="this.style.transform='translateY(-2px)';this.style.boxShadow='0 4px 12px rgba(0,0,0,0.3)'" onmouseout="this.style.transform='';this.style.boxShadow=''">
            <span style="font-size:1.5rem">{icon}</span>
            <span style="font-size:0.75rem;font-weight:600">{label}</span>
            <span style="font-size:0.7rem;color:{valcolor};font-weight:700">{value}</span>
        </a>'''

    html = f'''
    <div class="hdr"><h1>📦 MAGAZYNIER</h1><small>{get_config_cached("brand_name", "AKCES HUB")}</small></div>

    <!-- STATYSTYKI -->
    <div class="stats">
        <div class="stat"><div class="stat-v">{s['produkty']}</div><div class="stat-l">Produktow</div></div>
        <div class="stat"><div class="stat-v">{s['sztuki']}</div><div class="stat-l">Sztuk</div></div>
        <div class="stat">
            <div class="stat-v green">{s['wartosc_zakupu']:.0f} zl</div>
            <div class="stat-l">Wartosc (brutto)</div>
            <div style="font-size:0.65rem;color:#64748b">netto: {s['wartosc_netto']:.0f} zl</div>
        </div>
        <div class="stat"><div class="stat-v green">{s['wartosc_allegro']:.0f} zl</div><div class="stat-l">Allegro</div></div>
    </div>

    <!-- SZUKAJ + SKANER -->
    <div class="search"><form action="/magazyn/szukaj" method="GET" style="display:flex;gap:8px">
        <input type="text" name="q" placeholder="EAN / ASIN / MAG-kod / nazwa..." style="flex:1">
        <button type="submit" style="padding:14px 18px;background:var(--blue);border:none;border-radius:8px;color:#fff;font-size:1.2rem;cursor:pointer">🔍</button>
        <a href="/magazyn/skaner" style="padding:14px 18px;background:#22c55e;border-radius:8px;color:#fff;font-size:1.2rem;display:flex;align-items:center;text-decoration:none" title="Skaner kodow">📷</a>
    </form></div>

    <!-- GLOWNE -->
    <div style="font-size:0.7rem;color:#64748b;text-transform:uppercase;font-weight:700;letter-spacing:1px;margin:16px 0 8px;padding-left:4px">Magazyn</div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:12px">
        {tile('/magazyn/produkty', '📋', 'PRODUKTY', f"{s['produkty']}", '#1e293b')}
        {tile('/magazyn/palety', '📦', 'PALETY', f"{s['palety']}", '#2d1b69', '', '#a78bfa')}
        {tile('/warehouse/shelves', '🗄️', 'REGALY', 'mapa + QR', '#1a0a30', '#7c3aed44', '#7c3aed')}
    </div>

    <!-- ANALITYKA -->
    <div style="font-size:0.7rem;color:#64748b;text-transform:uppercase;font-weight:700;letter-spacing:1px;margin:16px 0 8px;padding-left:4px">Analityka</div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:12px">
        {tile('/statystyki', '📊', 'STATYSTYKI', 'przegladaj', '#1c1600', '#eab30844', '#eab308')}
        {tile('/magazyn/statystyki-zakupow', '🛒', 'ZAKUPY', 'analiza', '#001c2d', '#0ea5e944', '#0ea5e9')}
        {tile('/magazyn/lezaki', '⏳', 'LEZAKI', 'zalegajace', '#1c0a00', '#f9731644', '#f97316')}
    </div>

    <!-- OPERACJE -->
    <div style="font-size:0.7rem;color:#64748b;text-transform:uppercase;font-weight:700;letter-spacing:1px;margin:16px 0 8px;padding-left:4px">Operacje</div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:12px">
        {tile('/magazyn/dostawcy', '🚚', 'DOSTAWCY', f"{s['dostawcy']}", '#1e293b', '', '#64748b')}
        {tile('/magazyn/koszty', '💸', 'KOSZTY', 'wydatki', '#1c0010', '#f43f5e44', '#f43f5e')}
        {tile('/magazyn/sprzedaz-prywatna', '🤝', 'PRYWATNA', 'sprzedaz', '#1a0a2e', '#8b5cf644', '#8b5cf6')}
    </div>

    <!-- NARZEDZIA -->
    <div style="font-size:0.7rem;color:#64748b;text-transform:uppercase;font-weight:700;letter-spacing:1px;margin:16px 0 8px;padding-left:4px">Narzedzia</div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:12px">
        {tile('/magazyn/import', '📥', 'IMPORT', 'CSV/dane', '#0a1a25', '#3b82f644', '#3b82f6')}
        {tile('/magazyn/export', '📤', 'EXPORT', 'pobierz', '#0a2518', '#22c55e44', '#22c55e')}
        {tile('/magazyn/fetch-images', '🖼️', 'ZDJECIA', 'pobierz', '#1c1600', '#eab30844', '#eab308')}
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px">
        {tile('/magazyn/remanent', '📋', 'REMANENT', 'Excel', '#0a1a25', '#0369a144', '#0369a1')}
        {tile('/magazyn/backup', '💾', 'BACKUP', 'przywroc', '#0a2218', '#22c55e44', '#22c55e')}
    </div>

    <div class="section">🕐 OSTATNIO DODANE</div>
    '''
    
    for p in products:
        img = p['zdjecie_url'] or 'https://via.placeholder.com/45'
        pcode = get_product_code(p)
        display_code = p['ean'] or p['asin'] or f"#{p['id']}"
        html += f'''<a href="/magazyn/produkt/{pcode}" class="item">
            <img src="{img}" onerror="this.src='https://via.placeholder.com/45'">
            <div class="item-info">
                <div class="item-name">{p['nazwa'][:35]}...</div>
                <div class="item-meta">{display_code} | 📍{p['lokalizacja'] or '—'}</div>
            </div>
            <div class="item-right">
                <div class="item-qty">{p['ilosc']}</div>
                <div class="item-price">{p['cena_allegro'] or 0:.0f} zł</div>
            </div>
        </a>'''
    
    html += '<a href="/" class="back">← Powrót</a>'
    return render(html)

@magazynier_bp.route('/skaner')
def skaner():
    """Skaner kodów kreskowych z kamery"""
    html = '''
    <div class="hdr"><h1>📷 SKANER KODÓW</h1><small>Zeskanuj EAN/ASIN</small></div>
    
    <div id="scanner-container" style="position:relative;width:100%;max-width:400px;margin:0 auto 15px">
        <video id="video" style="width:100%;border-radius:12px;background:#000" playsinline></video>
        <div id="scan-line" style="position:absolute;left:10%;right:10%;top:50%;height:2px;background:#22c55e;box-shadow:0 0 10px #22c55e;animation:scan 2s infinite"></div>
    </div>
    
    <style>
        @keyframes scan { 0%,100%{opacity:1} 50%{opacity:0.3} }
    </style>
    
    <div id="result" style="text-align:center;margin-bottom:15px">
        <div style="color:#64748b;font-size:0.9rem">Skieruj kamerę na kod kreskowy</div>
    </div>
    
    <div class="card" style="padding:15px">
        <div class="form-group">
            <label style="font-size:0.8rem;color:#64748b">Lub wpisz ręcznie:</label>
            <form action="/magazyn/szukaj" method="GET" style="display:flex;gap:8px;margin-top:8px">
                <input type="text" name="q" id="manual-input" class="form-ctrl" placeholder="EAN / ASIN..." style="flex:1;padding:12px;background:#0a0a0f;border:1px solid #1e1e2e;border-radius:8px;color:#fff">
                <button type="submit" class="btn btn-p" style="width:auto;padding:12px 20px;margin:0">🔍</button>
            </form>
        </div>
    </div>
    
    <div id="last-scanned" style="display:none" class="card">
        <div style="padding:15px">
            <div style="font-size:0.75rem;color:#64748b;margin-bottom:5px">Ostatnio zeskanowany:</div>
            <div id="last-code" style="font-size:1.2rem;font-weight:700;color:#22c55e"></div>
        </div>
    </div>
    
    <a href="/magazyn" class="back">← Powrót</a>
    
    <script src="https://cdn.jsdelivr.net/npm/@zxing/library@0.19.1/umd/index.min.js"></script>
    <script>
    (function() {
        const video = document.getElementById('video');
        const resultDiv = document.getElementById('result');
        const lastScanned = document.getElementById('last-scanned');
        const lastCode = document.getElementById('last-code');
        const manualInput = document.getElementById('manual-input');
        
        let lastResult = '';
        let lastTime = 0;
        
        // Inicjalizacja skanera
        const codeReader = new ZXing.BrowserMultiFormatReader();
        
        codeReader.listVideoInputDevices()
            .then(devices => {
                // Preferuj tylną kamerę
                let selectedDevice = devices[0];
                for (const device of devices) {
                    if (device.label.toLowerCase().includes('back') || 
                        device.label.toLowerCase().includes('rear') ||
                        device.label.toLowerCase().includes('environment')) {
                        selectedDevice = device;
                        break;
                    }
                }
                
                if (selectedDevice) {
                    startScanning(selectedDevice.deviceId);
                } else {
                    resultDiv.innerHTML = '<div style="color:#ef4444">Brak kamery</div>';
                }
            })
            .catch(err => {
                resultDiv.innerHTML = '<div style="color:#ef4444">Błąd dostępu do kamery: ' + err.message + '</div>';
            });
        
        function startScanning(deviceId) {
            codeReader.decodeFromVideoDevice(deviceId, 'video', (result, err) => {
                if (result) {
                    const code = result.getText();
                    const now = Date.now();
                    
                    // Zapobiegaj wielokrotnemu skanowaniu tego samego kodu
                    if (code !== lastResult || now - lastTime > 3000) {
                        lastResult = code;
                        lastTime = now;
                        
                        // Wibracja (jeśli dostępna) - mocna podwójna
                        if (navigator.vibrate) navigator.vibrate([50, 30, 100]);
                        
                        // Pokaż wynik
                        resultDiv.innerHTML = '<div style="color:#22c55e;font-size:1.2rem;font-weight:700">✅ ' + code + '</div>';
                        lastScanned.style.display = 'block';
                        lastCode.textContent = code;
                        manualInput.value = code;
                        
                        // Przekieruj do produktu po 1.5s
                        setTimeout(() => {
                            window.location.href = '/magazyn/szukaj?q=' + encodeURIComponent(code);
                        }, 1500);
                    }
                }
            });
        }
        
        // Zatrzymaj skaner przy opuszczaniu strony
        window.addEventListener('beforeunload', () => {
            codeReader.reset();
        });
    })();
    </script>
    '''
    return render(html)

@magazynier_bp.route('/produkty')
def produkty():
    # Pobierz parametry filtrowania i sortowania
    filter_status = request.args.get('status', '')
    filter_paleta = request.args.get('paleta', '')
    filter_dostawca = request.args.get('dostawca', '')
    sort_by = request.args.get('sort', 'data')  # data, cena, nazwa, ilosc
    sort_dir = request.args.get('dir', 'desc').upper()
    if sort_dir not in ('ASC', 'DESC'):
        sort_dir = 'DESC'
    search = request.args.get('search', '')
    msg = request.args.get('msg', '')
    
    conn = get_db()
    
    # Buduj query z filtrami
    query = 'SELECT * FROM produkty WHERE 1=1'
    params = []
    
    if filter_status:
        query += ' AND status = ?'
        params.append(filter_status)
    
    if filter_paleta:
        query += ' AND paleta = ?'
        params.append(filter_paleta)
    
    if filter_dostawca:
        query += ' AND dostawca = ?'
        params.append(filter_dostawca)
    
    if search:
        query += ' AND (nazwa LIKE ? OR ean LIKE ? OR asin LIKE ? OR kod_magazynowy LIKE ?)'
        search_term = f'%{search}%'
        params.extend([search_term, search_term, search_term, f'%{search.upper()}%'])
    
    # Dodaj sortowanie
    sort_columns = {
        'data': 'data_dodania',
        'cena': 'cena_allegro',
        'nazwa': 'nazwa',
        'ilosc': 'ilosc'
    }
    sort_col = sort_columns.get(sort_by, 'data_dodania')
    query += f' ORDER BY {sort_col} {sort_dir}'
    
    products = conn.execute(query, params).fetchall()

    # Pre-compute pallet cost/szt for profit (cena_zakupu_brutto / total_szt)
    _paleta_ids = set(p['paleta_id'] for p in products if p['paleta_id'])
    _koszt_cache = {}
    for pid in _paleta_ids:
        _koszt_cache[pid] = _paleta_koszt_szt(conn, pid)

    # Pobierz unikalne wartości dla filtrów
    palety = [r[0] for r in conn.execute('SELECT DISTINCT paleta FROM produkty WHERE paleta IS NOT NULL ORDER BY paleta').fetchall()]
    dostawcy = [r[0] for r in conn.execute('SELECT DISTINCT dostawca FROM produkty WHERE dostawca IS NOT NULL ORDER BY dostawca').fetchall()]
    
    # Policz statusy
    status_counts = {}
    conn = get_db()
    for status in ['nowy', 'wystawiony', 'sprzedany', 'wyslany', 'uszkodzony', 'zwrot']:
        count = conn.execute('SELECT COUNT(*) FROM produkty WHERE status = ? OR (status IS NULL AND ? = "nowy")', (status, status)).fetchone()[0]
        status_counts[status] = count
    
    html = f'''
    <div class="hdr">
        <h1>📋 PRODUKTY</h1>
        <small>{len(products)} pozycji</small>
    </div>
    '''
    
    if msg:
        html += f'<script>Toast.success("{msg}");</script>'
    
    # Zakładki statusów
    html += f'''
    <div class="card" style="padding:10px;margin-bottom:15px">
        <div style="display:flex;gap:8px;flex-wrap:wrap">
            <a href="/magazyn/produkty" class="btn {'btn-ok' if not filter_status else ''}" style="padding:8px 15px;font-size:0.85rem">
                📋 Wszystkie ({len(products) if not filter_status else sum(status_counts.values())})
            </a>
            <a href="/magazyn/produkty?status=nowy" class="btn {'btn-ok' if filter_status == 'nowy' else ''}" style="padding:8px 15px;font-size:0.85rem;background:var(--blue)">
                📦 Magazyn ({status_counts['nowy']})
            </a>
            <a href="/magazyn/produkty?status=wystawiony" class="btn {'btn-ok' if filter_status == 'wystawiony' else ''}" style="padding:8px 15px;font-size:0.85rem;background:var(--purple)">
                🛒 Allegro ({status_counts['wystawiony']})
            </a>
            <a href="/magazyn/produkty?status=sprzedany" class="btn {'btn-ok' if filter_status == 'sprzedany' else ''}" style="padding:8px 15px;font-size:0.85rem;background:var(--green)">
                💰 Sprzedane ({status_counts['sprzedany']})
            </a>
        </div>
    </div>
    '''
    
    # Filtry i sortowanie
    html += f'''
    <div class="card" style="padding:15px;margin-bottom:15px">
        <form method="GET" action="/magazyn/produkty" style="display:flex;gap:10px;flex-wrap:wrap">
            <input type="hidden" name="status" value="{filter_status}">
            
            <input type="text" name="search" value="{search}" placeholder="🔍 Szukaj..." class="form-input" style="flex:1;min-width:150px">
            
            <select name="paleta" class="form-input" style="min-width:120px">
                <option value="">📦 Paleta</option>
                {"".join([f'<option value="{p}" {"selected" if filter_paleta == p else ""}>{p}</option>' for p in palety])}
            </select>
            
            <select name="dostawca" class="form-input" style="min-width:120px">
                <option value="">🏢 Dostawca</option>
                {"".join([f'<option value="{d}" {"selected" if filter_dostawca == d else ""}>{d}</option>' for d in dostawcy])}
            </select>
            
            <select name="sort" class="form-input" style="min-width:120px">
                <option value="data" {"selected" if sort_by == "data" else ""}>📅 Data</option>
                <option value="cena" {"selected" if sort_by == "cena" else ""}>💰 Cena</option>
                <option value="nazwa" {"selected" if sort_by == "nazwa" else ""}>📝 Nazwa</option>
                <option value="ilosc" {"selected" if sort_by == "ilosc" else ""}>📊 Ilość</option>
            </select>
            
            <select name="dir" class="form-input" style="min-width:100px">
                <option value="desc" {"selected" if sort_dir == "desc" else ""}>⬇️ Malejąco</option>
                <option value="asc" {"selected" if sort_dir == "asc" else ""}>⬆️ Rosnąco</option>
            </select>
            
            <button type="submit" class="btn btn-ok">Filtruj</button>
            <a href="/magazyn/produkty" class="btn">Wyczyść</a>
        </form>
    </div>
    '''
    
    # Masowa edycja
    html += f'''
    <form id="mass-edit-form" method="POST" action="/magazyn/produkty/masowa-edycja">
        <div class="card" style="padding:15px;margin-bottom:15px;background:rgba(139,92,246,0.1);border:2px solid #8b5cf6">
            <div style="color:#8b5cf6;font-weight:700;font-size:0.95rem;margin-bottom:12px">⚡ MASOWA EDYCJA ZAZNACZONYCH</div>
            
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
                <div>
                    <label style="display:block;color:#94a3b8;font-size:0.75rem;margin-bottom:4px">🔄 Status</label>
                    <select name="new_status" class="form-input" style="width:100%;font-size:0.85rem">
                        <option value="">-- bez zmiany --</option>
                        <option value="magazyn">📦 Magazyn</option>
                        <option value="wystawiony">🛒 Wystawiony (Allegro)</option>
                        <option value="sprzedany">💰 Sprzedany</option>
                        <option value="uszkodzony">⚠️ Uszkodzony</option>
                        <option value="zwrot">↩️ Zwrot</option>
                    </select>
                </div>
                <div>
                    <label style="display:block;color:#94a3b8;font-size:0.75rem;margin-bottom:4px">🏷️ Stan</label>
                    <select name="new_stan" class="form-input" style="width:100%;font-size:0.85rem">
                        <option value="">-- bez zmiany --</option>
                        <option value="Nowy">✨ Nowy</option>
                        <option value="Nowy w otwartym opakowaniu">📦 Nowy w otwartym opak.</option>
                        <option value="Używany">🔄 Używany</option>
                        <option value="Uszkodzony">💥 Uszkodzony</option>
                        <option value="Odnowiony">♻️ Odnowiony</option>
                    </select>
                </div>
                <div>
                    <label style="display:block;color:#94a3b8;font-size:0.75rem;margin-bottom:4px">📍 Lokalizacja</label>
                    <input type="text" name="new_lokalizacja" class="form-input" placeholder="np. A1, B2 (puste = bez zmiany)" style="width:100%;font-size:0.85rem">
                </div>
                <div>
                    <label style="display:block;color:#94a3b8;font-size:0.75rem;margin-bottom:4px">💵 Cena Allegro (zł)</label>
                    <input type="number" name="new_cena_allegro" class="form-input" placeholder="puste = bez zmiany" step="0.01" min="0" style="width:100%;font-size:0.85rem">
                </div>
            </div>
            
            <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
                <button type="button" onclick="toggleAll()" class="btn" style="background:var(--blue);flex:1">
                    ☑️ Zaznacz wszystkie
                </button>
                <button type="submit" class="btn btn-ok" onclick="return confirm('Zastosować zmiany dla ' + document.getElementById('count').textContent + ' produktów?')" style="flex:1">
                    ✅ Zastosuj
                </button>
            </div>
            <div id="selected-count" style="margin-top:10px;color:#8b5cf6;font-size:0.85rem;font-weight:600">
                Zaznaczono: <span id="count">0</span> produktów
            </div>
        </div>
    '''
    
    # Lista produktów
    for p in products:
        img = p['zdjecie_url'] or 'https://via.placeholder.com/45'
        pcode = get_product_code(p)
        _km = p['kod_magazynowy'] if p['kod_magazynowy'] else f"#{p['id']}"
        display_code = f"{_km} | {p['ean'] or p['asin'] or ''}"

        # Zysk per item (koszt = paleta.cena_zakupu / szt)
        _ca = float(p['cena_allegro'] or 0)
        _ks = _koszt_cache.get(p['paleta_id'], 0)
        try:
            _kat = (p['kategoria'] or 'inne').lower()
        except (KeyError, IndexError):
            _kat = 'inne'
        _pr = ALLEGRO_PROWIZJE.get(_kat, 0.11)
        _zy = _ca - _ks - (_ca * _pr) if _ca > 0 and _ks > 0 else None
        
        # Badge statusu
        status_badges = {
            'nowy': '<span style="background:var(--blue);color:#fff;padding:3px 8px;border-radius:6px;font-size:0.7rem;font-weight:600">📦 MAGAZYN</span>',
            'wystawiony': '<span style="background:var(--purple);color:#fff;padding:3px 8px;border-radius:6px;font-size:0.7rem;font-weight:600">🛒 ALLEGRO</span>',
            'sprzedany': '<span style="background:var(--green);color:#fff;padding:3px 8px;border-radius:6px;font-size:0.7rem;font-weight:600">💰 SPRZEDANE</span>',
            'wyslany': '<span style="background:var(--green);color:#fff;padding:3px 8px;border-radius:6px;font-size:0.7rem;font-weight:600">📮 WYSŁANE</span>',
            'uszkodzony': '<span style="background:var(--red);color:#fff;padding:3px 8px;border-radius:6px;font-size:0.7rem;font-weight:600">⚠️ USZKODZONY</span>',
            'zwrot': '<span style="background:var(--yellow);color:#000;padding:3px 8px;border-radius:6px;font-size:0.7rem;font-weight:600">↩️ ZWROT</span>'
        }
        product_status = p['status'] if p['status'] else 'nowy'
        status_badge = status_badges.get(product_status, '')
        
        html += f'''
        <div class="item product-item" style="position:relative;padding-left:50px" 
             data-name="{p['nazwa'].lower()}" 
             data-status="{product_status}"
             data-paleta="{p['paleta'] or ''}"
             data-dostawca="{p['dostawca'] or ''}">
            <input type="checkbox" name="product_ids" value="{p['id']}" class="product-checkbox" 
                   style="position:absolute;left:15px;top:50%;transform:translateY(-50%);width:20px;height:20px;cursor:pointer"
                   onchange="updateCount()">
            <a href="/magazyn/produkt/{pcode}" style="display:flex;align-items:center;flex:1;text-decoration:none;color:inherit">
                <img src="{img}" onerror="this.src='https://via.placeholder.com/45'">
                <div class="item-info">
                    <div class="item-name">{p['nazwa'][:30]}...</div>
                    <div class="item-meta">{display_code} | 📍{p['lokalizacja'] or '—'} | 📦{p['paleta'] or '—'} | {status_badge}</div>
                </div>
                <div class="item-right">
                    <div class="item-qty">{p['ilosc']}</div>
                    <div class="item-price">{p['cena_allegro'] or 0:.0f} zł</div>
                    {f'<div style="font-size:0.65rem;color:{"#22c55e" if _zy >= 0 else "#ef4444"}">{_zy:.0f} zł zysk</div>' if _zy is not None else ''}
                </div>
            </a>
        </div>'''
    
    html += '''
    </form>
    
    <script>
    let allChecked = false;
    
    function toggleAll() {
        allChecked = !allChecked;
        const checkboxes = document.querySelectorAll('.product-checkbox');
        checkboxes.forEach(cb => cb.checked = allChecked);
        updateCount();
    }
    
    function updateCount() {
        const checked = document.querySelectorAll('.product-checkbox:checked').length;
        document.getElementById('count').textContent = checked;
    }
    
    // Zapobiegaj przypadkowemu kliknięciu checkboxa zamiast linku
    document.querySelectorAll('.product-checkbox').forEach(cb => {
        cb.addEventListener('click', (e) => e.stopPropagation());
    });
    </script>
    
    <a href="/magazyn" class="back">← Powrót</a>
    '''
    return render(html)

@magazynier_bp.route('/produkt/<path:code>')
def produkt(code):
    conn = get_db()
    # Szukaj po EAN, ASIN lub ID
    p = get_produkt_by_code(conn, code)

    # Pobierz historię produktu (50 ostatnich wpisów)
    historia = []
    if p:
        historia = conn.execute('''
            SELECT * FROM historia_produktu
            WHERE produkt_id = ?
            ORDER BY data DESC
            LIMIT 50
        ''', (p['id'],)).fetchall()


    msg = request.args.get('msg', '')
    is_new = p is None

    if is_new:
        p = {
            'id': 0, 'ean': code, 'asin': '', 'nazwa': f'Nowy produkt {code}', 'ilosc': 0,
            'cena_netto': 0, 'cena_brutto': 0, 'cena_allegro': 0, 'stan': 'Nowy',
            'lokalizacja': '', 'paleta': '', 'dostawca': '',
            'kategoria': 'inne', 'zdjecie_url': get_amazon_image_url(code)
        }
    else:
        p = dict(p)

    product_code = get_product_code(p) if not is_new else code
    img = p['zdjecie_url'] or ''
    # Fallback: jeśli brak zdjecie_url, spróbuj z kolumny images (lokalne pliki)
    if not img and p.get('images'):
        try:
            _imgs = json.loads(p['images']) if isinstance(p['images'], str) else p['images']
            if _imgs and len(_imgs) > 0:
                first_img = _imgs[0]
                if first_img.startswith('static/'):
                    img = '/' + first_img
                else:
                    img = first_img
        except:
            pass
    # Fallback: szukaj lokalnego pliku po ASIN
    if not img and p.get('asin'):
        import os
        local_path = f"static/downloads/{p['asin']}/image_1.jpg"
        if os.path.exists(local_path):
            img = '/' + local_path
    if not img:
        img = 'https://via.placeholder.com/400x180/12121a/fff?text=BRAK'

    # Koszt brutto/szt = własna cena produktu (jednostkowa z importu)
    # Fallback na średnią z palety tylko gdy produkt nie ma własnej ceny
    _koszt_brutto_szt = 0
    if p.get('cena_brutto') and p['cena_brutto'] > 0:
        _koszt_brutto_szt = float(p['cena_brutto'])
    elif p.get('paleta_id'):
        _koszt_brutto_szt = _paleta_koszt_szt(conn, p['paleta_id'])

    # Zysk per item = cena_allegro - koszt_zakupu - prowizja
    _cena_al = float(p['cena_allegro'] or 0)
    _kat = (p.get('kategoria') or 'inne').lower()
    _prowizja_rate = ALLEGRO_PROWIZJE.get(_kat, 0.11)
    _prowizja_kwota = _cena_al * _prowizja_rate
    _zysk_szt = _cena_al - _koszt_brutto_szt - _prowizja_kwota if _cena_al > 0 and _koszt_brutto_szt > 0 else 0
    _zysk_color = '#22c55e' if _zysk_szt >= 0 else '#ef4444'

    html = f'''<div class="hdr"><h1>📦 PRODUKT</h1></div>'''
    
    if is_new:
        html += '<div class="alert alert-warn">🆕 NOWY PRODUKT - kliknij EDYTUJ aby dodać</div>'
    if msg:
        html += f'<div class="alert alert-ok">{msg}</div>'
    
    # 📜 HISTORIA NA GÓRZE - PIERWSZA
    if historia:
        html += '''
        <div class="card" style="margin-bottom:20px;border:2px solid #8b5cf6;background:rgba(139,92,246,0.05)">
            <div style="background:linear-gradient(135deg,#8b5cf6,#7c3aed);padding:18px;border-radius:12px 12px 0 0">
                <div style="font-size:1.3rem;font-weight:700;color:#fff">📜 HISTORIA ZMIAN</div>
                <div style="font-size:0.85rem;color:rgba(255,255,255,0.85);margin-top:6px">Ostatnie ''' + str(len(historia)) + ''' działań na tym produkcie</div>
            </div>
            <div style="padding:15px;max-height:400px;overflow-y:auto">'''
        
        ikony = {
            'dodano': '📥', 'edytowano': '✏️', 'wystawiono': '🛒', 
            'sprzedano': '💰', 'wyslano': '📦', 'zmiana_ceny': '💵',
            'zmiana_lokalizacji': '📍', 'zmiana_ilosci': '📊',
            'drukowano': '🏷️', 'skanowano': '📱', 'importowano': '📂',
            'scrapowano': '🔍', 'wygenerowano_opis': '✨', 'dodano_zdjecia': '📷',
            'przeniesiono': '🔄', 'oznaczono': '🏷️'
        }
        kolory_bg = {
            'dodano': 'rgba(59,130,246,0.1)', 'sprzedano': 'rgba(34,197,94,0.15)', 
            'wystawiono': 'rgba(139,92,246,0.1)', 'wyslano': 'rgba(34,197,94,0.15)', 
            'zmiana_ceny': 'rgba(234,179,8,0.1)', 'skanowano': 'rgba(59,130,246,0.1)',
            'wygenerowano_opis': 'rgba(139,92,246,0.1)', 'importowano': 'rgba(59,130,246,0.1)', 
            'scrapowano': 'rgba(139,92,246,0.1)', 'drukowano': 'rgba(139,92,246,0.1)'
        }
        for h in historia:
            h = dict(h)  # Konwersja Row -> dict
            ikona = ikony.get(h['akcja'], '📌')
            bg_color = kolory_bg.get(h['akcja'], 'rgba(30,30,46,0.5)')
            data_str = h['data'][:16] if h['data'] else ''
            
            # Dane JSON
            dane_extra = ''
            if h.get('dane_json'):
                try:
                    import json
                    dane = json.loads(h['dane_json'])
                    if dane:
                        dane_extra = '<div style="font-size:0.75rem;color:#8b5cf6;margin-top:6px">'
                        for k, v in dane.items():
                            if k not in ['allegro_id']:
                                dane_extra += f'<span style="background:rgba(139,92,246,0.15);padding:3px 8px;border-radius:6px;margin-right:6px">{k}: {v}</span>'
                        dane_extra += '</div>'
                except:
                    pass
            
            html += f'''
            <div style="background:{bg_color};border:1px solid rgba(139,92,246,0.3);border-radius:10px;padding:14px;margin-bottom:12px">
                <div style="display:flex;justify-content:space-between;align-items:start">
                    <div style="flex:1">
                        <div style="font-size:1.05rem;font-weight:600;color:#fff;margin-bottom:4px">{ikona} {h['opis']}</div>
                        {dane_extra}
                    </div>
                    <div style="display:flex;align-items:center;gap:10px;margin-left:15px">
                        <div style="font-size:0.8rem;color:#64748b;white-space:nowrap">{data_str}</div>
                        <a href="/magazyn/historia/{h['id']}/edytuj" style="color:#fbbf24;text-decoration:none;font-size:0.9rem" title="Edytuj">✏️</a>
                        <a href="/magazyn/historia/{h['id']}/usun?redirect=/magazyn/produkt/{product_code}" onclick="return confirm('Usunąć ten wpis z historii?')" style="color:#ef4444;text-decoration:none;font-size:0.9rem" title="Usuń">🗑️</a>
                    </div>
                </div>
            </div>'''
        html += '</div></div>'
    
    badge = 'badge-ok' if p['ilosc'] > 0 else 'badge-err'
    
    # Pokaż EAN i ASIN
    ean_display = p.get('ean') or '—'
    asin_display = p.get('asin') or '—'
    
    # Quick Actions - pokaż dla wszystkich produktów z ID (nie tylko nie-nowych)
    quick_actions = ''
    if p and 'id' in p.keys() and p['id']:  # Jeśli ma ID (jest w bazie)
        # Status badge
        current_status = p['status'] if p['status'] else 'nowy'
        status_badges = {
            'nowy': ('📦', 'MAGAZYN', 'var(--blue)'),
            'wystawiony': ('🛒', 'ALLEGRO', 'var(--purple)'),
            'sprzedany': ('💰', 'SPRZEDANE', 'var(--green)'),
            'wyslany': ('📮', 'WYSŁANE', 'var(--green)'),
            'uszkodzony': ('⚠️', 'USZKODZONY', 'var(--red)'),
            'zwrot': ('↩️', 'ZWROT', 'var(--yellow)')
        }
        icon, label, color = status_badges.get(current_status, ('📦', 'MAGAZYN', 'var(--blue)'))
        
        quick_actions = f'''
        <div style="background:rgba(139,92,246,0.05);border:2px solid {color};border-radius:10px;padding:15px;margin-bottom:15px">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
                <div>
                    <div style="font-size:0.75rem;color:var(--text-dim);margin-bottom:4px">STATUS PRODUKTU</div>
                    <div style="font-size:1.2rem;font-weight:700;color:{color}">{icon} {label}</div>
                </div>
                <form method="POST" action="/magazyn/produkt/{product_code}/zmien-status" style="display:flex;gap:8px;align-items:center">
                    <select name="new_status" class="form-input" style="padding:8px 12px;font-size:0.85rem;min-width:150px" onchange="this.form.submit()">
                        <option value="">-- Zmień na --</option>
                        <option value="nowy" {'selected' if current_status == 'nowy' else ''}>📦 Magazyn</option>
                        <option value="wystawiony" {'selected' if current_status == 'wystawiony' else ''}>🛒 Allegro</option>
                        <option value="sprzedany" {'selected' if current_status == 'sprzedany' else ''}>💰 Sprzedany</option>
                        <option value="wyslany" {'selected' if current_status == 'wyslany' else ''}>📮 Wysłany</option>
                        <option value="uszkodzony" {'selected' if current_status == 'uszkodzony' else ''}>⚠️ Uszkodzony</option>
                        <option value="zwrot" {'selected' if current_status == 'zwrot' else ''}>↩️ Zwrot</option>
                    </select>
                </form>
            </div>
        </div>
        
        <div class="quick-actions">
            <a href="/magazyn/produkt/{product_code}/sprzedaj" class="quick-btn" style="background:var(--green)">💰 -1 SZT</a>
            <a href="/magazyn/etykieta-mobilna/{product_code}" class="quick-btn" style="background:var(--purple)">📱 ETYKIETA</a>
            <a href="/magazyn/produkt/{product_code}/edytuj" class="quick-btn" style="background:var(--yellow);color:#000">✏️ EDYTUJ</a>
            <a href="/paletomat/generator/from-magazyn/{p['id']}" class="quick-btn" style="background:var(--blue)">🛒 WYSTAW</a>
            <form method="POST" action="/magazyn/produkt/{product_code}/usun" style="display:inline" onsubmit="return confirm('Na pewno usunąć ten produkt?')">
                <button type="submit" class="quick-btn" style="background:var(--red);border:none;cursor:pointer">🗑️ USUŃ</button>
            </form>
        </div>
        '''
    
    html += f'''
    <div class="card">
        <img src="{img}" class="card-img" onerror="this.src='https://via.placeholder.com/400x180/12121a/fff?text=BRAK'">
        <div class="card-body">
            <div class="card-name">{p['nazwa']}</div>
            
            {quick_actions}
            
            <div class="loc">
                <div class="loc-title">📍 Lokalizacja</div>
                <div class="loc-grid">
                    <div><div class="loc-l">Regał</div><div class="loc-v">{p['lokalizacja'] or '—'}</div></div>
                    <div><div class="loc-l">Paleta</div><div class="loc-v">{p['paleta'] or '—'}</div></div>
                    <div><div class="loc-l">Dostawca</div><div class="loc-v">{(p['dostawca'] or '—')[:6]}</div></div>
                </div>
            </div>
            
            <div class="det-grid">
                <div class="det" style="border:1px solid #8b5cf644;border-radius:8px"><div class="det-l">🏷️ Kod mag.</div><div class="det-v" style="color:#8b5cf6;font-weight:700">{p.get('kod_magazynowy') or f"#{p['id']}"}</div></div>
                <div class="det"><div class="det-l">EAN</div><div class="det-v" style="font-size:0.75rem">{ean_display}</div></div>
                <div class="det"><div class="det-l">ASIN</div><div class="det-v" style="font-size:0.7rem">{asin_display}</div></div>
                <div class="det"><div class="det-l">Ilość</div><div class="det-v"><span class="badge {badge}">{p['ilosc']} szt</span></div></div>
                <div class="det"><div class="det-l">Stan</div><div class="det-v">{p['stan'] or 'Nowy'}</div></div>
                <div class="det"><div class="det-l">💰 Koszt/szt brutto</div><div class="det-v">{_koszt_brutto_szt:.2f} zł</div></div>
                <div class="det"><div class="det-l">💰 Koszt/szt netto</div><div class="det-v">{_koszt_brutto_szt / 1.23:.2f} zł</div></div>
                <div class="det"><div class="det-l">💵 Cena Allegro</div><div class="det-v green">{p['cena_allegro'] or 0:.2f} zł</div></div>
                <div class="det"><div class="det-l">📊 Prowizja ({int(_prowizja_rate*100)}%)</div><div class="det-v" style="color:#f59e0b">{_prowizja_kwota:.2f} zł</div></div>
                <div class="det" style="border:1px solid {_zysk_color}44;border-radius:8px"><div class="det-l">💎 Zysk/szt</div><div class="det-v" style="color:{_zysk_color};font-weight:700;font-size:1.1rem">{_zysk_szt:.2f} zł</div></div>
            </div>
        </div>
        
        <div style="padding:15px;background:var(--bg)">
            <a href="/magazyn/produkt/{product_code}/edytuj" class="btn btn-warn">✏️ EDYTUJ</a>
            <a href="/magazyn/drukuj/{product_code}" class="btn btn-2" style="background:#8b5cf6">🖨️ DRUKUJ ETYKIETĘ</a>
            <a href="/magazyn/produkt/{product_code}/opis" class="btn btn-purple">✨ GENERUJ OPIS AI</a>
            <button onclick="pokazGPSR()" class="btn" style="background:#059669">🛡️ GPSR</button>
            <button onclick="pokazRozbijProdukt({p['id']}, {p['ilosc']}, '{p['nazwa'][:40].replace(chr(39), '')}')" class="btn" style="background:#22c55e;color:#000">🎯 ROZBIJ NA SZTUKI</button>
            <button onclick="pokazNaprawaProdukt({p['id']}, '{p['nazwa'][:40].replace(chr(39), '')}', {p['ilosc']})" class="btn" style="background:#f59e0b;color:#000">🔧 DO NAPRAWY</button>
        </div>
        
        <!-- SZTUKI SECTION -->
        <div id="sztukiSekcja" style="margin:0 15px 15px;display:none">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                <div style="font-weight:700;font-size:1rem">📦 Ewidencja sztuk</div>
                <button onclick="pokazRozbijProdukt({p['id']}, {p['ilosc']}, '{p['nazwa'][:40].replace(chr(39), '')}')" 
                    style="padding:5px 12px;background:#22c55e22;border:1px solid #22c55e;border-radius:8px;color:#22c55e;font-size:0.75rem;cursor:pointer">🎯 Zmień rozbicie</button>
            </div>
            <div id="sztukiKarty"></div>
        </div>
        
        <!-- GPSR Modal -->
        <div id="gpsrModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:1000;padding:20px;overflow:auto">
            <div style="max-width:600px;margin:50px auto;background:var(--card);border-radius:12px;padding:20px">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px">
                    <h3 style="margin:0">🛡️ GPSR - Informacje o bezpieczeństwie</h3>
                    <button onclick="document.getElementById('gpsrModal').style.display='none'" style="background:none;border:none;color:white;font-size:24px;cursor:pointer">&times;</button>
                </div>
                <div id="gpsrContent" style="background:var(--bg);padding:15px;border-radius:8px;white-space:pre-wrap;font-family:monospace;font-size:13px;max-height:400px;overflow:auto"></div>
                <div style="margin-top:15px;display:flex;gap:10px">
                    <button onclick="kopiujGPSR()" class="btn btn-ok" style="flex:1">📋 KOPIUJ</button>
                    <button onclick="document.getElementById('gpsrModal').style.display='none'" class="btn" style="flex:1;background:#6b7280">Zamknij</button>
                </div>
            </div>
        </div>
        
        <script>
        async function pokazGPSR() {{
            const modal = document.getElementById('gpsrModal');
            const content = document.getElementById('gpsrContent');
            content.textContent = '⏳ Generowanie GPSR...';
            modal.style.display = 'block';
            
            try {{
                const resp = await fetch('/magazyn/api/gpsr/{p['id']}');
                const data = await resp.json();
                
                if (data.gpsr) {{
                    content.textContent = data.gpsr;
                }} else {{
                    content.textContent = '✅ Ten produkt nie wymaga informacji GPSR (dekoracja, odzież, papeteria)';
                }}
            }} catch (e) {{
                content.textContent = '❌ Błąd: ' + e.message;
            }}
        }}
        
        const KOLOR_STAN = {{'Nowy':'#22c55e','Powystawowy':'#3b82f6','Używany':'#eab308','Uszkodzony':'#ef4444','Odnowiony':'#8b5cf6'}};
        const PROD_ID = {p['id']};
        const PROD_ZDJECIE = "{p.get('zdjecie_url', '') or ''}";
        const STANY_WYMAGAJACE_FOTO = ['Powystawowy','Używany','Uszkodzony'];
        
        async function ladujSztuki() {{
            const resp = await fetch('/api/sztuki/' + PROD_ID).catch(()=>null);
            if (!resp) return;
            const d = await resp.json();
            const sekcja = document.getElementById('sztukiSekcja');
            const karty = document.getElementById('sztukiKarty');
            if (!d.sztuki || d.sztuki.length === 0) {{
                sekcja.style.display = 'none';
                return;
            }}
            sekcja.style.display = 'block';
            karty.innerHTML = d.sztuki.map(s => renderKartaSztuki(s)).join('');
        }}
        
        function renderKartaSztuki(s) {{
            const k = KOLOR_STAN[s.stan] || '#64748b';
            const naprawaBg = s.status === 'naprawa' ? '#f59e0b15' : '#12121a';
            const naprawaBorder = s.status === 'naprawa' ? '#f59e0b55' : '#1e1e2e';
            const statusLabel = {{'magazyn':'📦 Magazyn','naprawa':'🔧 Naprawa','sprzedany':'✅ Sprzedany','wyslany':'🚚 Wysłany','uszkodzony':'💥 Uszkodzony'}};
            const zdjecieSrc = s.zdjecie || '';
            const wymaga_foto = STANY_WYMAGAJACE_FOTO.includes(s.stan);
            
            let zdjecieHtml = '';
            if (zdjecieSrc) {{
                // Ma własne zdjęcie - pokaż je z opcją usunięcia
                zdjecieHtml = `<div style="position:relative;margin-bottom:10px">
                    <img src="${{zdjecieSrc}}" style="width:100%;max-height:220px;object-fit:contain;border-radius:10px;border:1px solid #334155;background:#0a0a0f">
                    <button onclick="usunZdjecie(${{s.id}})" style="position:absolute;top:6px;right:6px;background:#ef444488;border:none;border-radius:6px;color:#fff;padding:4px 8px;font-size:0.7rem;cursor:pointer">✕ Usuń</button>
                </div>`;
            }} else if (wymaga_foto) {{
                // Uszkodzone/Używane/Powystawowe bez zdjęcia - pokaż upload z ostrzeżeniem
                zdjecieHtml = `<div style="margin-bottom:10px">
                    <label for="foto_${{s.id}}" style="display:flex;align-items:center;justify-content:center;gap:8px;padding:14px;background:#f59e0b11;border:2px dashed #f59e0b55;border-radius:10px;cursor:pointer;color:#f59e0b;font-size:0.85rem">
                        📷 Dodaj zdjęcie stanu (${{s.stan}})
                    </label>
                    <input type="file" id="foto_${{s.id}}" accept="image/*" capture="environment" style="display:none" onchange="uploadZdjecie(${{s.id}}, this)">
                </div>`;
            }}
            // Nowy bez zdjęcia = nic nie pokazujemy (zdjęcie z Amazonu jest na stronie produktu)
            
            return `<div id="karta_${{s.id}}" style="background:${{naprawaBg}};border:1px solid ${{naprawaBorder}};border-radius:12px;padding:14px;margin-bottom:10px">
                <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
                    <div style="width:12px;height:12px;border-radius:50%;background:${{k}};flex-shrink:0"></div>
                    <div style="font-weight:700;font-size:1rem;flex:1">Sztuka nr ${{s.numer}}</div>
                    <div style="font-size:0.75rem;color:${{k}};background:${{k}}22;padding:3px 10px;border-radius:20px;border:1px solid ${{k}}44">${{s.stan}}</div>
                </div>
                
                ${{zdjecieHtml}}
                
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">
                    <div>
                        <div style="font-size:0.7rem;color:#64748b;margin-bottom:3px">STAN</div>
                        <select onchange="zapiszPoleSztuki(${{s.id}}, 'stan', this.value)"
                            style="width:100%;background:#0a0a0f;border:1px solid #334155;border-radius:8px;color:#fff;padding:6px 8px;font-size:0.85rem">
                            ${{['Nowy','Powystawowy','Używany','Uszkodzony','Odnowiony'].map(v =>
                                `<option value="${{v}}" ${{v===s.stan?'selected':''}}>${{v}}</option>`).join('')}}
                        </select>
                    </div>
                    <div>
                        <div style="font-size:0.7rem;color:#64748b;margin-bottom:3px">STATUS</div>
                        <select onchange="zapiszPoleSztuki(${{s.id}}, 'status', this.value)"
                            style="width:100%;background:#0a0a0f;border:1px solid #334155;border-radius:8px;color:#fff;padding:6px 8px;font-size:0.85rem">
                            ${{['magazyn','naprawa','sprzedany','wyslany','uszkodzony'].map(v =>
                                `<option value="${{v}}" ${{v===s.status?'selected':''}}>${{statusLabel[v]||v}}</option>`).join('')}}
                        </select>
                    </div>
                </div>
                
                <div style="margin-bottom:8px">
                    <div style="font-size:0.7rem;color:#64748b;margin-bottom:3px">NOTATKA</div>
                    <textarea id="notatka_${{s.id}}" rows="2" placeholder="np. zarysowanie obudowy, brak ładowarki..."
                        style="width:100%;background:#0a0a0f;border:1px solid #334155;border-radius:8px;color:#fff;padding:8px;font-size:0.85rem;resize:vertical;box-sizing:border-box"
                        >${{s.opis_naprawy || ''}}</textarea>
                </div>
                
                <button onclick="zapiszNotatke(${{s.id}})"
                    style="width:100%;padding:8px;background:#3b82f622;border:1px solid #3b82f655;border-radius:8px;color:#3b82f6;font-size:0.8rem;cursor:pointer;font-weight:600">
                    💾 Zapisz notatkę
                </button>
                ${{s.data_naprawy ? `<div style="font-size:0.7rem;color:#64748b;margin-top:6px;text-align:right">Ostatnia zmiana: ${{s.data_naprawy}}</div>` : ''}}
            </div>`;
        }}
        
        async function uploadZdjecie(id, input) {{
            if (!input.files[0]) return;
            const file = input.files[0];
            // Kompresuj do max 800px
            const canvas = document.createElement('canvas');
            const img = new Image();
            img.onload = async function() {{
                const max = 800;
                let w = img.width, h = img.height;
                if (w > max || h > max) {{
                    if (w > h) {{ h = Math.round(h * max / w); w = max; }}
                    else {{ w = Math.round(w * max / h); h = max; }}
                }}
                canvas.width = w; canvas.height = h;
                canvas.getContext('2d').drawImage(img, 0, 0, w, h);
                const base64 = canvas.toDataURL('image/jpeg', 0.75);
                const resp = await fetch('/api/sztuki/jednostka/' + id + '/zdjecie', {{
                    method: 'POST', headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{zdjecie: base64}})
                }});
                if (resp.ok) ladujSztuki();
            }};
            img.src = URL.createObjectURL(file);
        }}
        
        async function usunZdjecie(id) {{
            await fetch('/api/sztuki/jednostka/' + id + '/zdjecie', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{zdjecie: ''}})
            }});
            ladujSztuki();
        }}
        
        async function zapiszPoleSztuki(id, pole, wartosc) {{
            const karta = document.getElementById('karta_' + id);
            const endpoint = pole === 'status' ? '/api/sztuki/jednostka/' + id + '/status' : '/api/sztuki/jednostka/' + id + '/stan';
            const body = pole === 'status' ? {{status: wartosc}} : {{stan: wartosc}};
            const resp = await fetch(endpoint, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(body)}});
            if (resp.ok) {{
                karta.style.border = '1px solid #22c55e';
                setTimeout(() => {{ karta.style.border = '1px solid #1e1e2e'; ladujSztuki(); }}, 800);
            }}
        }}
        
        async function zapiszNotatke(id) {{
            const notatka = document.getElementById('notatka_' + id).value;
            const resp = await fetch('/api/sztuki/jednostka/' + id + '/notatka', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{notatka}})
            }});
            const karta = document.getElementById('karta_' + id);
            if (resp.ok) {{
                karta.style.border = '1px solid #22c55e';
                setTimeout(() => karta.style.border = '1px solid #1e1e2e', 1000);
            }}
        }}
        
        ladujSztuki();
        
        function kopiujGPSR() {{
            const content = document.getElementById('gpsrContent').textContent;
            navigator.clipboard.writeText(content).then(() => {{
                alert('✅ Skopiowano do schowka!');
            }}).catch(() => {{
                // Fallback
                const textarea = document.createElement('textarea');
                textarea.value = content;
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand('copy');
                document.body.removeChild(textarea);
                alert('✅ Skopiowano do schowka!');
            }});
        }}
        </script>

    <!-- MODAL ROZBIJ -->
    <div id="modalRozbijProd" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:1000;overflow-y:auto;padding:20px">
      <div style="background:#1e1e2e;border-radius:16px;padding:20px;max-width:440px;margin:0 auto">
        <div style="font-size:1.2rem;font-weight:700;margin-bottom:4px">🎯 Rozbij stan na sztuki</div>
        <div id="rozbijProdNazwa" style="color:#94a3b8;font-size:0.85rem;margin-bottom:15px"></div>
        <div style="background:#12121a;border-radius:10px;padding:12px;margin-bottom:15px">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px">
            <span style="color:#94a3b8">Łącznie sztuk:</span><span id="rozbijProdLacznie" style="font-weight:700"></span>
          </div>
          <div style="display:flex;justify-content:space-between">
            <span style="color:#94a3b8">Suma wpisanych:</span><span id="rozbijProdSuma" style="font-weight:700;color:#22c55e"></span>
          </div>
        </div>
        <div id="rozbijProdStany"></div>
        <div style="color:#94a3b8;font-size:0.75rem;margin:10px 0 6px">Szybkie ustawienie:</div>
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:15px">
          <button onclick="rozbijProdSzybko('Nowy')" style="padding:6px 10px;background:#22c55e22;border:1px solid #22c55e;border-radius:8px;color:#22c55e;font-size:0.75rem;cursor:pointer">🟢 Wszystko nowe</button>
          <button onclick="rozbijProdSzybko('Powystawowy')" style="padding:6px 10px;background:#3b82f622;border:1px solid #3b82f6;border-radius:8px;color:#3b82f6;font-size:0.75rem;cursor:pointer">🔵 Powystawowe</button>
          <button onclick="rozbijProdSzybko('Używany')" style="padding:6px 10px;background:#eab30822;border:1px solid #eab308;border-radius:8px;color:#eab308;font-size:0.75rem;cursor:pointer">🟡 Używane</button>
          <button onclick="rozbijProdSzybko('Uszkodzony')" style="padding:6px 10px;background:#ef444422;border:1px solid #ef4444;border-radius:8px;color:#ef4444;font-size:0.75rem;cursor:pointer">🔴 Uszkodzone</button>
        </div>
        <div style="display:flex;gap:8px">
          <button onclick="document.getElementById('modalRozbijProd').style.display='none'" style="flex:1;padding:12px;background:#334155;border:none;border-radius:10px;color:#fff;cursor:pointer">Anuluj</button>
          <button onclick="zapiszRozbijProd()" style="flex:1;padding:12px;background:#22c55e;border:none;border-radius:10px;color:#000;font-weight:700;cursor:pointer">✓ Zapisz</button>
        </div>
      </div>
    </div>

    <!-- MODAL NAPRAWA -->
    <div id="modalNaprawaProd" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:1000;overflow-y:auto;padding:20px">
      <div style="background:#1e1e2e;border-radius:16px;padding:20px;max-width:440px;margin:0 auto">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
          <div style="font-size:1.2rem;font-weight:700">🔧 Do naprawy</div>
          <button onclick="document.getElementById('modalNaprawaProd').style.display='none'" style="background:none;border:none;color:#94a3b8;font-size:1.3rem;cursor:pointer">✕</button>
        </div>
        <div id="naprawaProdNazwa" style="color:#94a3b8;font-size:0.85rem;margin-bottom:15px"></div>
        <div id="naprawaProdLista"></div>
        <button onclick="document.getElementById('modalNaprawaProd').style.display='none'" style="width:100%;padding:12px;background:#334155;border:none;border-radius:10px;color:#fff;margin-top:10px;cursor:pointer">Zamknij</button>
      </div>
    </div>

    <script>
    let _rpId=null, _rpIlosc=0;
    const KOLORY_P = {{'Nowy':'#22c55e','Powystawowy':'#3b82f6','Używany':'#eab308','Uszkodzony':'#ef4444','Odnowiony':'#8b5cf6'}};

    function pokazRozbijProdukt(id, ilosc, nazwa) {{
        _rpId=id; _rpIlosc=ilosc;
        document.getElementById('rozbijProdNazwa').textContent=nazwa;
        document.getElementById('rozbijProdLacznie').textContent=ilosc;
        fetch('/api/sztuki/'+id).then(r=>r.json()).then(d=>{{
            const istn={{}};
            (d.sztuki||[]).forEach(s=>{{istn[s.stan]=(istn[s.stan]||0)+1;}});
            renderRozbijProd(istn);
        }}).catch(()=>renderRozbijProd({{}}));
        document.getElementById('modalRozbijProd').style.display='block';
    }}
    function renderRozbijProd(val) {{
        const stany=['Nowy','Powystawowy','Używany','Uszkodzony'];
        let html='';
        stany.forEach(s=>{{
            const k=KOLORY_P[s], v=val[s]||0;
            html+=`<div style="display:flex;align-items:center;gap:10px;background:${{k}}11;border:1px solid ${{k}}44;border-radius:10px;padding:10px;margin-bottom:8px">
              <div style="width:12px;height:12px;border-radius:50%;background:${{k}};flex-shrink:0"></div>
              <div style="flex:1;font-weight:600">${{s}}</div>
              <button onclick="zmRozP('${{s}}',-1)" style="width:34px;height:34px;background:#12121a;border:1px solid #334155;border-radius:8px;color:#fff;cursor:pointer;font-size:1.1rem">−</button>
              <input type="number" id="rp_${{s}}" value="${{v}}" min="0" oninput="aktualizujSumP()"
                style="width:55px;text-align:center;background:#12121a;border:1px solid #334155;border-radius:8px;color:#fff;padding:5px;font-size:1rem">
              <button onclick="zmRozP('${{s}}',1)" style="width:34px;height:34px;background:#12121a;border:1px solid #334155;border-radius:8px;color:#fff;cursor:pointer;font-size:1.1rem">+</button>
            </div>`;
        }});
        document.getElementById('rozbijProdStany').innerHTML=html;
        aktualizujSumP();
    }}
    function zmRozP(s,d){{const e=document.getElementById('rp_'+s);e.value=Math.max(0,(parseInt(e.value||0)+d));aktualizujSumP();}}
    function aktualizujSumP(){{
        let suma=0;
        ['Nowy','Powystawowy','Używany','Uszkodzony'].forEach(s=>{{suma+=parseInt(document.getElementById('rp_'+s)?.value||0);}});
        const el=document.getElementById('rozbijProdSuma');
        el.textContent=suma+' / '+_rpIlosc;
        el.style.color=suma===_rpIlosc?'#22c55e':'#ef4444';
    }}
    function rozbijProdSzybko(stan){{
        ['Nowy','Powystawowy','Używany','Uszkodzony'].forEach(s=>{{
            const e=document.getElementById('rp_'+s);if(e)e.value=s===stan?_rpIlosc:0;
        }});aktualizujSumP();
    }}
    function zapiszRozbijProd(){{
        let suma=0,podzial={{}};
        ['Nowy','Powystawowy','Używany','Uszkodzony'].forEach(s=>{{
            const v=parseInt(document.getElementById('rp_'+s)?.value||0);
            if(v>0){{podzial[s]=v;suma+=v;}}
        }});
        if(suma!==_rpIlosc){{alert('Suma musi wynosić '+_rpIlosc+' sztuk!');return;}}
        fetch('/api/sztuki/'+_rpId+'/rozbij',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{podzial}})}})
        .then(r=>r.json()).then(d=>{{if(d.ok){{document.getElementById('modalRozbijProd').style.display='none';alert('✅ Zapisano!');}}}}); 
    }}

    function pokazNaprawaProdukt(id, nazwa, ilosc) {{
        document.getElementById('naprawaProdNazwa').textContent=nazwa+' — '+ilosc+' szt.';
        document.getElementById('naprawaProdLista').innerHTML='<div style="color:#64748b;text-align:center;padding:20px">Ładowanie...</div>';
        document.getElementById('modalNaprawaProd').style.display='block';
        fetch('/api/sztuki/'+id).then(r=>r.json()).then(d=>renderNaprawaProd(d.sztuki||[], ilosc, id));
    }}
    function renderNaprawaProd(sztuki, ilosc, prodId) {{
        const pelna=[];
        for(let i=1;i<=ilosc;i++) pelna.push(sztuki.find(s=>s.numer===i)||{{id:null,numer:i,stan:'Nowy',status:'magazyn',opis_naprawy:''}});
        let html='';
        pelna.forEach(s=>{{
            if(s.status==='naprawa'){{
                html+=`<div style="background:#f59e0b15;border:1px solid #f59e0b55;border-radius:10px;padding:12px;margin-bottom:8px">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                    <div style="font-weight:700;color:#f59e0b">🔧 szt. ${{s.numer}} DO NAPRAWY</div>
                    <div style="display:flex;gap:6px">
                      <button onclick="cofnijNaprawaProd(${{s.id}}, ${{prodId}}, ${{ilosc}})" style="padding:4px 10px;background:#ef444422;border:1px solid #ef4444;border-radius:6px;color:#ef4444;font-size:0.72rem;cursor:pointer">↩ Cofnij</button>
                    </div>
                  </div>
                  <div style="background:#1e1e2e;border-radius:6px;padding:8px;font-size:0.8rem">📝 ${{s.opis_naprawy||'—'}}</div>
                  ${{s.data_naprawy?`<div style="font-size:0.7rem;color:#64748b;margin-top:4px">${{s.data_naprawy}}</div>`:''}}
                </div>`;
            }} else {{
                html+=`<div style="background:#12121a;border:1px solid #1e1e2e;border-radius:10px;padding:12px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center">
                  <div style="display:flex;align-items:center;gap:8px">
                    <div style="width:10px;height:10px;border-radius:50%;background:${{KOLORY_P[s.stan]||'#64748b'}}"></div>
                    <span style="font-weight:600">szt. ${{s.numer}}</span>
                    <span style="font-size:0.72rem;color:#64748b">${{s.stan}}</span>
                  </div>
                  <button onclick="dodajNaprawaProd(${{s.id||0}}, ${{s.numer}}, ${{prodId}}, ${{ilosc}})" style="padding:6px 14px;background:#f59e0b;border:none;border-radius:8px;color:#000;font-size:0.75rem;font-weight:700;cursor:pointer">+ Do naprawy</button>
                </div>`;
            }}
        }});
        document.getElementById('naprawaProdLista').innerHTML=html||'<div style="color:#64748b;text-align:center;padding:15px">Brak rozbicia — najpierw użyj 🎯 Rozbij na sztuki</div>';
    }}
    function dodajNaprawaProd(sztukiId, numer, prodId, ilosc) {{
        const opis=prompt('Opis usterki dla szt. '+numer+':');
        if(opis===null) return;
        const doSave=(id)=>fetch('/api/sztuki/jednostka/'+id+'/naprawa',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{opis}})}})
            .then(()=>{{fetch('/api/sztuki/'+prodId).then(r=>r.json()).then(d=>renderNaprawaProd(d.sztuki||[],ilosc,prodId));}});
        if(sztukiId>0){{doSave(sztukiId);}}
        else{{
            fetch('/api/sztuki/'+prodId+'/rozbij',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{podzial:{{'Nowy':ilosc}}}})}}
            ).then(()=>fetch('/api/sztuki/'+prodId).then(r=>r.json()).then(d=>{{
                const szt=(d.sztuki||[]).find(s=>s.numer===numer);
                if(szt)doSave(szt.id);
            }}));
        }}
    }}
    function cofnijNaprawaProd(id, prodId, ilosc) {{
        fetch('/api/sztuki/jednostka/'+id+'/naprawa',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{cofnij:true}})}})
        .then(()=>fetch('/api/sztuki/'+prodId).then(r=>r.json()).then(d=>renderNaprawaProd(d.sztuki||[],ilosc,prodId)));
    }}
    </script>
    </div>
    <a href="/magazyn" class="back">← Powrót</a>
    '''
    return render(html)

@magazynier_bp.route('/produkt/<path:code>/zmien-status', methods=['POST'])
def zmien_status_produktu(code):
    """Szybka zmiana statusu produktu"""
    from .database import add_historia
    
    new_status = request.form.get('new_status', '').strip()
    
    if not new_status:
        return redirect(f'/magazyn/produkt/{code}')
    
    conn = get_db()
    p = get_produkt_by_code(conn, code)
    
    if not p:
        return redirect('/magazyn')
    
    old_status = p['status'] or 'nowy'
    
    # Nazwy statusów
    status_names = {
        'nowy': 'Magazyn',
        'wystawiony': 'Allegro',
        'sprzedany': 'Sprzedany',
        'wyslany': 'Wysłany',
        'uszkodzony': 'Uszkodzony',
        'zwrot': 'Zwrot'
    }
    
    # Aktualizuj status
    conn.execute('UPDATE produkty SET status = ? WHERE id = ?', (new_status, p['id']))
    conn.commit()
    
    # Dodaj do historii
    opis = f"Zmiana statusu: {status_names.get(old_status, old_status)} → {status_names.get(new_status, new_status)}"
    add_historia(p['id'], 'edytowano', opis, {'stary_status': old_status, 'nowy_status': new_status})
    
    product_code = get_product_code(dict(p))
    return redirect(f'/magazyn/produkt/{product_code}?msg=Status+zmieniony+na+{status_names.get(new_status, new_status)}')

@magazynier_bp.route('/produkt/<path:code>/edytuj', methods=['GET', 'POST'])
def edytuj_produkt(code):
    from .database import add_historia

    # ── POST: zapisz zmiany ──────────────────────────────────
    if request.method == 'POST':
        try:
            d = {}
            for k in ['nazwa','lokalizacja','paleta','dostawca','zdjecie_url','stan','kategoria','ean','asin']:
                d[k] = (request.form.get(k) or '').strip()

            if d['paleta'] == '__nowa__':
                d['paleta'] = (request.form.get('paleta_nowa') or '').strip()

            d['ilosc'] = int(request.form.get('ilosc', 0) or 0)

            cena_netto_szt  = float(request.form.get('cena_netto',  0) or 0)
            cena_brutto_szt = float(request.form.get('cena_brutto', 0) or 0)

            if cena_brutto_szt == 0 and cena_netto_szt > 0:
                cena_brutto_szt = round(cena_netto_szt * 1.23, 2)
            if cena_netto_szt == 0 and cena_brutto_szt > 0:
                cena_netto_szt = round(cena_brutto_szt / 1.23, 2)

            d['cena_netto']   = cena_netto_szt   # JEDNOSTKOWA (nie × ilosc)
            d['cena_brutto']  = cena_brutto_szt  # JEDNOSTKOWA (nie × ilosc)
            d['cena_allegro'] = float(request.form.get('cena_allegro', 0) or 0)

            conn = get_db()
            existing = get_produkt_by_code(conn, code)

            if existing:
                pid = existing['id']
                product_code = str(pid)

                # Zaktualizuj paleta_id jeśli zmieniono paletę
                paleta_id = existing['paleta_id']
                if d['paleta'] and d['paleta'] != (existing['paleta'] or ''):
                    row = conn.execute(
                        'SELECT id FROM palety WHERE nazwa=? OR paleta=?',
                        (d['paleta'], d['paleta'])
                    ).fetchone()
                    if row:
                        paleta_id = row['id']

                conn.execute('''UPDATE produkty
                    SET ean=?,asin=?,nazwa=?,ilosc=?,stan=?,lokalizacja=?,
                        paleta=?,paleta_id=?,dostawca=?,zdjecie_url=?,
                        cena_netto=?,cena_brutto=?,cena_allegro=?,kategoria=?
                    WHERE id=?''',
                    (d['ean'],d['asin'],d['nazwa'],d['ilosc'],d['stan'],d['lokalizacja'],
                     d['paleta'],paleta_id,d['dostawca'],d['zdjecie_url'],
                     d['cena_netto'],d['cena_brutto'],d['cena_allegro'],d['kategoria'],
                     pid))
                conn.commit()

                old_cena = existing['cena_allegro'] or 0
                if old_cena != d['cena_allegro']:
                    add_historia(pid, 'zmiana_ceny',
                        f'Cena: {old_cena:.0f} → {d["cena_allegro"]:.0f} zł',
                        {'stara_cena': old_cena, 'nowa_cena': d['cena_allegro']})
                else:
                    add_historia(pid, 'edytowano', 'Edytowano produkt', {'nazwa': d['nazwa']})

            else:
                cur = conn.execute('''INSERT INTO produkty
                    (ean,asin,nazwa,ilosc,stan,lokalizacja,paleta,dostawca,
                     zdjecie_url,cena_netto,cena_brutto,cena_allegro,kategoria)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (d['ean'],d['asin'],d['nazwa'],d['ilosc'],d['stan'],d['lokalizacja'],
                     d['paleta'],d['dostawca'],d['zdjecie_url'],
                     d['cena_netto'],d['cena_brutto'],d['cena_allegro'],d['kategoria']))
                pid = cur.lastrowid
                product_code = str(pid)
                conn.commit()
                add_historia(pid, 'dodano', f'Dodano do magazynu', {'paleta': d['paleta']})

            return redirect(f'/magazyn/produkt/{product_code}?msg=Zapisano!')

        except Exception as e:
            import traceback
            return f'<div style="padding:20px;color:#ef4444;background:#1e1e2e;font-family:monospace">' \
                   f'<h2>❌ Błąd zapisu</h2><pre>{traceback.format_exc()}</pre>' \
                   f'<a href="/magazyn">← Powrót</a></div>', 500

    # ── GET: wyświetl formularz ───────────────────────────────
    conn = get_db()
    p_row = get_produkt_by_code(conn, code)
    paleta_koszt_per_szt = 0

    if p_row:
        p = dict(p_row)
        product_code = str(p['id'])
        if p.get('paleta_id'):
            paleta_koszt_per_szt = _paleta_koszt_szt(conn, p['paleta_id'])
    else:
        ean  = code if not code.startswith('B0') else ''
        asin = code if code.startswith('B0') else ''
        p = {'id':0,'ean':ean,'asin':asin,'nazwa':'','ilosc':0,
             'cena_netto':0,'cena_brutto':0,'cena_allegro':0,
             'lokalizacja':'','paleta':'','paleta_id':None,
             'dostawca':'','kategoria':'inne',
             'zdjecie_url':get_amazon_image_url(code),'stan':'Nowy','status':'magazyn'}
        product_code = code


    # Jednostkowa cena do formularza
    try:
        ilosc_p = p.get('ilosc') or 1
        cb = p.get('cena_brutto') or 0
        cn = p.get('cena_netto') or 0
        if cb > 0:
            _p_brutto_szt = round(cb, 2)  # cena_brutto jest już JEDNOSTKOWA (nie dzielić!)
            _p_netto_szt  = round(cn, 2) if cn > 0 else round(_p_brutto_szt / 1.23, 2)
        elif paleta_koszt_per_szt > 0:
            _p_brutto_szt = round(paleta_koszt_per_szt, 2)
            _p_netto_szt  = round(_p_brutto_szt / 1.23, 2)
        else:
            _p_brutto_szt = 0
            _p_netto_szt  = 0
        if _p_netto_szt > _p_brutto_szt > 0:
            _p_netto_szt, _p_brutto_szt = _p_brutto_szt, _p_netto_szt
    except:
        _p_brutto_szt = 0
        _p_netto_szt  = 0

    dostawcy_options = ''.join([
        f'<option {"selected" if p.get("dostawca")==d else ""}>{d}</option>'
        for d in DOSTAWCY
    ])

    conn2 = get_db()
    palety_lista = conn2.execute(
        'SELECT DISTINCT paleta FROM produkty WHERE paleta IS NOT NULL AND paleta != "" ORDER BY paleta'
    ).fetchall()

    palety_options = '<option value="">-- Brak palety --</option>'
    for pr in palety_lista:
        pn = pr['paleta']
        sel = 'selected' if p.get('paleta') == pn else ''
        palety_options += f'<option value="{pn}" {sel}>{pn}</option>'

    html = f'''
    <div class="hdr"><h1>✏️ EDYTUJ</h1></div>
    
    <form action="/magazyn/produkt/{product_code}/edytuj" method="POST">
    <div class="card" style="padding:15px">
        <div class="form-row">
            <div class="form-group"><label>EAN</label>
                <input type="text" name="ean" class="form-ctrl" value="{p.get('ean', '')}" placeholder="EAN-13">
            </div>
            <div class="form-group"><label>ASIN</label>
                <input type="text" name="asin" class="form-ctrl" value="{p.get('asin', '')}" placeholder="B0XXXXXXXX">
            </div>
        </div>
        
        <div class="form-group"><label>Nazwa</label>
            <input type="text" name="nazwa" class="form-ctrl" value="{p['nazwa']}" required>
        </div>
        
        <div class="form-row">
            <div class="form-group"><label>Ilość</label>
                <input type="number" name="ilosc" class="form-ctrl" value="{p['ilosc']}" min="0">
            </div>
        </div>
        <div class="form-row-3">
            <div class="form-group"><label>💰 Netto/szt</label>
                <input type="number" step="0.01" name="cena_netto" class="form-ctrl" value="{_p_netto_szt:.2f}">
            </div>
            <div class="form-group"><label>💰 Brutto/szt</label>
                <input type="number" step="0.01" name="cena_brutto" class="form-ctrl" value="{_p_brutto_szt:.2f}">
            </div>
            <div class="form-group"><label>💵 Cena Allegro</label>
                <input type="number" step="0.01" name="cena_allegro" class="form-ctrl" value="{p['cena_allegro'] or 0}">
            </div>
        </div>
        
        <div class="form-row-3">
            <div class="form-group"><label>Kategoria</label>
                <select name="kategoria" class="form-ctrl">
                    <option value="ev_ladowarki" {"selected" if p.get('kategoria')=='ev_ladowarki' else ''}>⚡ Ładowarki EV</option>
                    <option value="foto_video" {"selected" if p.get('kategoria')=='foto_video' else ''}>📸 Foto/Video</option>
                    <option value="druk3d" {"selected" if p.get('kategoria')=='druk3d' else ''}>🖨️ Druk 3D</option>
                    <option value="smart_home" {"selected" if p.get('kategoria')=='smart_home' else ''}>📹 Smart Home</option>
                    <option value="motoryzacja" {"selected" if p.get('kategoria')=='motoryzacja' else ''}>🚗 Motoryzacja</option>
                    <option value="optyka" {"selected" if p.get('kategoria')=='optyka' else ''}>🔭 Optyka</option>
                    <option value="rolnictwo" {"selected" if p.get('kategoria')=='rolnictwo' else''}>🐣 Rolnictwo</option>
                    <option value="dekoracje" {"selected" if p.get('kategoria')=='dekoracje' else ''}>🎄 Dekoracje</option>
                    <option value="oswietlenie" {"selected" if p.get('kategoria')=='oswietlenie' else ''}>💡 Oświetlenie</option>
                    <option value="kuchnia" {"selected" if p.get('kategoria')=='kuchnia' else ''}>🍳 Kuchnia</option>
                    <option value="budowa" {"selected" if p.get('kategoria')=='budowa' else ''}>🛠️ Budowa</option>
                    <option value="biuro" {"selected" if p.get('kategoria')=='biuro' else ''}>💼 Biuro</option>
                    <option value="outdoor" {"selected" if p.get('kategoria')=='outdoor' else ''}>🎒 Outdoor</option>
                    <option value="rehabilitacja" {"selected" if p.get('kategoria')=='rehabilitacja' else ''}>♿ Rehabilitacja</option>
                    <option value="tekstylia" {"selected" if p.get('kategoria')=='tekstylia' else ''}>🛏️ Tekstylia</option>
                    <option value="kosmetyki" {"selected" if p.get('kategoria')=='kosmetyki' else ''}>🧴 Kosmetyki</option>
                    <option value="ksiazki" {"selected" if p.get('kategoria')=='ksiazki' else ''}>📚 Książki</option>
                    <option value="prezenty" {"selected" if p.get('kategoria')=='prezenty' else ''}>🎁 Prezenty</option>
                    <option value="bezpieczenstwo" {"selected" if p.get('kategoria')=='bezpieczenstwo' else ''}>🔒 Bezpieczeństwo</option>
                    <option value="bagaz" {"selected" if p.get('kategoria')=='bagaz' else ''}>🧳 Bagaż</option>
                    <option value="silownia" {"selected" if p.get('kategoria')=='silownia' else ''}>🏋️ Siłownia</option>
                    <option value="rowery" {"selected" if p.get('kategoria')=='rowery' else ''}>🚴 Rowery</option>
                    <option value="hulajnogi" {"selected" if p.get('kategoria')=='hulajnogi' else ''}>🛴 Hulajnogi</option>
                    <option value="elektronika" {"selected" if p.get('kategoria')=='elektronika' else ''}>📷 Elektronika</option>
                    <option value="akcesoria" {"selected" if p.get('kategoria')=='akcesoria' else ''}>🔋 Akcesoria</option>
                    <option value="agd_male" {"selected" if p.get('kategoria')=='agd_male' else ''}>🔌 AGD małe</option>
                    <option value="agd_duze" {"selected" if p.get('kategoria')=='agd_duze' else ''}>🏠 AGD duże</option>
                    <option value="komputery" {"selected" if p.get('kategoria')=='komputery' else ''}>💻 Komputery</option>
                    <option value="telefony" {"selected" if p.get('kategoria')=='telefony' else ''}>📱 Telefony</option>
                    <option value="rtv" {"selected" if p.get('kategoria')=='rtv' else ''}>📺 RTV/Audio</option>
                    <option value="gaming" {"selected" if p.get('kategoria')=='gaming' else ''}>🎮 Gaming</option>
                    <option value="narzedzia" {"selected" if p.get('kategoria')=='narzedzia' else ''}>🔧 Narzędzia</option>
                    <option value="dom_ogrod" {"selected" if p.get('kategoria')=='dom_ogrod' else ''}>🏡 Dom/Ogród</option>
                    <option value="sport" {"selected" if p.get('kategoria')=='sport' else ''}>⚽ Sport</option>
                    <option value="moda" {"selected" if p.get('kategoria')=='moda' else ''}>👕 Moda</option>
                    <option value="zabawki" {"selected" if p.get('kategoria')=='zabawki' else ''}>🧸 Zabawki</option>
                    <option value="zdrowie" {"selected" if p.get('kategoria')=='zdrowie' else ''}>💊 Zdrowie</option>
                    <option value="zwierzeta" {"selected" if p.get('kategoria')=='zwierzeta' else ''}>🐾 Zwierzęta</option>
                    <option value="muzyka" {"selected" if p.get('kategoria')=='muzyka' else ''}>🎸 Muzyka</option>
                    <option value="elektronarzedzia" {"selected" if p.get('kategoria')=='elektronarzedzia' else ''}>🧰 Elektronarzędzia</option>
                    <option value="hobby" {"selected" if p.get('kategoria')=='hobby' else ''}>🎨 Hobby</option>
                    <option value="niemowleta" {"selected" if p.get('kategoria')=='niemowleta' else ''}>🍼 Niemowlęta</option>
                    <option value="car_audio" {"selected" if p.get('kategoria')=='car_audio' else ''}>🔊 Car Audio</option>
                    <option value="klimatyzacja" {"selected" if p.get('kategoria')=='klimatyzacja' else ''}>🌡️ Klimatyzacja</option>
                    <option value="hydroponika" {"selected" if p.get('kategoria')=='hydroponika' else ''}>🪴 Hydroponika</option>
                    <option value="wedkarstwo" {"selected" if p.get('kategoria')=='wedkarstwo' else ''}>🎣 Wędkarstwo</option>
                    <option value="laboratorium" {"selected" if p.get('kategoria')=='laboratorium' else ''}>🔬 Laboratorium</option>
                    <option value="event" {"selected" if p.get('kategoria')=='event' else ''}>🎪 Event</option>
                    <option value="cb_radio" {"selected" if p.get('kategoria')=='cb_radio' else ''}>📡 CB/Radio</option>
                    <option value="inne" {"selected" if p.get('kategoria')=='inne' or not p.get('kategoria') else ''}>📦 Inne</option>
                </select>
            </div>
            <div class="form-group"><label>Stan</label>
                <select name="stan" class="form-ctrl">
                    <option {"selected" if p.get('stan')=='Nowy' else ''}>Nowy</option>
                    <option {"selected" if p.get('stan')=='Powystawowy' else ''}>Powystawowy</option>
                    <option {"selected" if p.get('stan')=='Używany' else ''}>Używany</option>
                    <option {"selected" if p.get('stan')=='Uszkodzony' else ''}>Uszkodzony</option>
                    <option {"selected" if p.get('stan')=='Odnowiony' else ''}>Odnowiony</option>
                </select>
            </div>
            <div class="form-group"><label>Dostawca</label>
                <select name="dostawca" class="form-ctrl"><option value="">—</option>{dostawcy_options}</select>
            </div>
        </div>
        
        <div class="form-row-3">
            <div class="form-group"><label>Regał</label>
                <input type="text" name="lokalizacja" class="form-ctrl" value="{p['lokalizacja'] or ''}">
            </div>
            <div class="form-group">
                <label>Paleta</label>
                <select name="paleta" class="form-ctrl" id="paleta-select" onchange="togglePaletaInput()">
                    {palety_options}
                    <option value="__nowa__">✨ Nowa paleta...</option>
                </select>
                <input type="text" name="paleta_nowa" id="paleta-nowa" class="form-ctrl" 
                       placeholder="Wpisz nazwę nowej palety" 
                       style="display:none;margin-top:8px">
            </div>
            <div class="form-group"></div>
        </div>
        
        <script>
        function togglePaletaInput() {{
            const select = document.getElementById('paleta-select');
            const input = document.getElementById('paleta-nowa');
            if (select.value === '__nowa__') {{
                input.style.display = 'block';
                input.required = true;
            }} else {{
                input.style.display = 'none';
                input.required = false;
                input.value = '';
            }}
        }}
        </script>
        
        <div class="form-group"><label>URL zdjęcia</label>
            <input type="text" name="zdjecie_url" class="form-ctrl" value="{p['zdjecie_url'] or ''}">
        </div>
    </div>
    
    <button type="submit" class="btn btn-ok">💾 ZAPISZ</button>
    </form>
    
    <form action="/magazyn/produkt/{product_code}/usun" method="POST" onsubmit="return confirm('Na pewno usunąć?')">
        <button type="submit" class="btn btn-err">🗑️ USUŃ</button>
    </form>
    
    <a href="/magazyn/produkt/{product_code}" class="back">← Anuluj</a>
    '''
    return render(html)

@magazynier_bp.route('/produkt/<path:code>/sprzedaj')
def sprzedaj_produkt(code):
    """Quick action: -1 szt
    
    Logika:
    - Zmniejsza ilosc o 1 w tabeli produkty
    - Zwiększa sprzedano_offline o 1 (licznik sztuk)
    - Zwiększa przychod_offline o cena_allegro (przychód na palecie)
    - NIE dodaje nic do tabeli sprzedaze (dzienne statystyki bez zmian)
    """
    from .database import add_historia
    from datetime import datetime
    
    conn = get_db()
    p = get_produkt_by_code(conn, code)
    
    if p and p['ilosc'] > 0:
        new_qty = p['ilosc'] - 1
        old_status = p['status'] or 'magazyn'
        new_status = 'sprzedany' if new_qty == 0 else old_status
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Cena za sztukę — doliczy się do przychodu palety
        cena_szt = float(p['cena_allegro'] or p['cena_brutto'] or 0)
        
        # Obecne wartości offline
        try:
            obecne_offline = p['sprzedano_offline'] or 0
        except:
            obecne_offline = 0
        try:
            obecny_przychod_offline = p['przychod_offline'] or 0
        except:
            obecny_przychod_offline = 0
        
        nowe_offline = obecne_offline + 1
        nowy_przychod_offline = obecny_przychod_offline + cena_szt
        
        # Upewnij się że kolumna przychod_offline istnieje
        try:
            conn.execute("SELECT przychod_offline FROM produkty LIMIT 1")
        except:
            try:
                conn.execute("ALTER TABLE produkty ADD COLUMN przychod_offline REAL DEFAULT 0")
                conn.commit()
            except:
                pass
        
        # UPDATE produkty — ilość, status, sprzedano_offline, przychod_offline
        # NIE ruszamy tabeli sprzedaze → dzienne statystyki bez zmian
        conn.execute('''UPDATE produkty 
            SET ilosc=?, status=?, sprzedano_offline=?, przychod_offline=?,
                data_sprzedazy=CASE WHEN ? = 0 THEN ? ELSE data_sprzedazy END
            WHERE id=?''',
            (new_qty, new_status, nowe_offline, nowy_przychod_offline,
             new_qty, now_str, p['id']))
        conn.commit()
        
        add_historia(p['id'], 'sprzedano',
            f'Korekta -1 szt. Pozostało: {new_qty}. Przychód palety +{cena_szt:.0f} zł',
            {'poprzednia_ilosc': p['ilosc'], 'nowa_ilosc': new_qty,
             'stary_status': old_status, 'nowy_status': new_status,
             'cena_sprzedazy': cena_szt})
        
        if cena_szt > 0:
            msg = f'✅ -1 szt. (+{cena_szt:.0f} zł na palecie) Pozostało: {new_qty} szt'
        else:
            msg = f'✅ -1 szt. Pozostało: {new_qty} szt'
    else:
        msg = '❌ Brak na stanie!'
    
    
    product_code = get_product_code(p) if p else code
    return redirect(f'/magazyn/produkt/{product_code}?msg={msg}')

@magazynier_bp.route('/produkt/<path:code>/usun', methods=['POST'])
def usun_produkt(code):
    conn = get_db()
    
    # Pobierz ID produktu przed usunięciem
    p = get_produkt_by_code(conn, code)
    
    if p:
        product_id = p['id']
        
        # Usuń historię produktu
        conn.execute('DELETE FROM historia_produktu WHERE produkt_id=?', (product_id,))
        
        # Usuń produkt
        conn.execute('DELETE FROM produkty WHERE id=?', (product_id,))
        
        conn.commit()
    
    return redirect('/magazyn?msg=Produkt+usunięty')

@magazynier_bp.route('/produkt/<path:code>/opis')
def produkt_opis(code):
    """Generuje opis AI dla produktu"""
    from .utils import generuj_opis_ai
    
    conn = get_db()
    p = get_produkt_by_code(conn, code)
    
    if not p:
        return redirect('/magazyn')
    
    product_code = get_product_code(p)
    display_code = p['ean'] or p['asin'] or f"#{p['id']}"
    opis = generuj_opis_ai(p['nazwa'], p['kategoria'] or 'inne')
    
    html = f'''
    <div class="hdr"><h1>✨ OPIS AI</h1><small>{display_code}</small></div>
    
    <div class="card" style="padding:15px">
        <div style="font-weight:600;margin-bottom:10px">{p['nazwa']}</div>
        <div style="background:#0a0a0f;border-radius:10px;padding:15px;white-space:pre-wrap;font-size:0.9rem;line-height:1.6;max-height:300px;overflow-y:auto">{opis}</div>
    </div>
    
    <button onclick="navigator.clipboard.writeText(document.querySelector('div[style*=pre-wrap]').innerText);this.innerText='✅ Skopiowano!';setTimeout(()=>this.innerText='📋 KOPIUJ DO SCHOWKA',2000)" class="btn btn-ok">📋 KOPIUJ DO SCHOWKA</button>
    
    <a href="/magazyn/produkt/{product_code}" class="back">← Powrót do produktu</a>
    '''
    return render(html)

@magazynier_bp.route('/szukaj')
def szukaj():
    q = request.args.get('q', '').strip()
    if not q:
        return redirect('/magazyn')
    
    # Obsługa kodów QR z prefiksem MAG: (format: MAG:B0B6NDX3SS)
    if q.upper().startswith('MAG:'):
        q = q[4:]  # Usuń prefiks "MAG:" - zostaje sam kod np. "B0B6NDX3SS"

    conn = get_db()

    # Szukaj po kodzie magazynowym (MAG-XXXXX)
    if q.upper().startswith('MAG-'):
        results = conn.execute('SELECT * FROM produkty WHERE kod_magazynowy=?', (q.upper(),)).fetchall()
    elif is_code(q):
        # Szukaj po EAN lub ASIN
        results = conn.execute('SELECT * FROM produkty WHERE ean=? OR asin=?', (q.upper(), q.upper())).fetchall()
    else:
        # Szukaj po nazwie lub kodzie magazynowym
        results = conn.execute('SELECT * FROM produkty WHERE nazwa LIKE ? OR kod_magazynowy LIKE ?', (f'%{q}%', f'%{q.upper()}%')).fetchall()
    
    if len(results) == 1:
        return redirect(f'/magazyn/produkt/{get_product_code(results[0])}')
    
    html = f'''<div class="hdr"><h1>🔍 WYNIKI</h1><small>"{q}"</small></div>'''
    
    for r in results:
        img = r['zdjecie_url'] or 'https://via.placeholder.com/45'
        pcode = get_product_code(r)
        display_code = r['ean'] or r['asin'] or f"#{r['id']}"
        html += f'''<a href="/magazyn/produkt/{pcode}" class="item">
            <img src="{img}" onerror="this.src='https://via.placeholder.com/45'">
            <div class="item-info">
                <div class="item-name">{r['nazwa']}</div>
                <div class="item-meta">{display_code}</div>
            </div>
            <div class="item-qty">{r['ilosc']}</div>
        </a>'''
    
    if not results:
        html += '<div class="alert alert-warn">Brak wyników</div>'
        if is_code(q):
            html += f'<a href="/magazyn/produkt/{q}" class="btn btn-ok">➕ DODAJ NOWY</a>'
    
    html += '<a href="/magazyn" class="back">← Powrót</a>'
    return render(html)

@magazynier_bp.route('/backup')
def backup_page():
    """Strona zarządzania backupami"""
    from modules.backup_manager import get_backups, create_backup, verify_backup
    
    backups = get_backups()
    
    html = '''
    <div class="hdr">
        <h1>💾 BACKUP & PRZYWRACANIE</h1>
        <small>Zarządzanie kopiami zapasowymi bazy danych</small>
    </div>
    
    <div class="card" style="padding:20px;margin-bottom:15px;background:rgba(34,197,94,0.1);border:2px solid #22c55e">
        <div style="display:flex;align-items:center;gap:15px">
            <div style="font-size:2.5rem">💾</div>
            <div style="flex:1">
                <div style="font-weight:600;font-size:1.1rem;margin-bottom:5px">Automatyczne backupy</div>
                <div style="font-size:0.9rem;opacity:0.8">System tworzy backup bazy co godzinę automatycznie</div>
                <div style="font-size:0.85rem;opacity:0.7;margin-top:3px">Przechowywane jest 24 ostatnie backupy (1 dzień)</div>
            </div>
        </div>
    </div>
    
    <div style="display:flex;gap:10px;margin-bottom:20px">
        <button onclick="createBackup()" class="btn btn-ok" style="flex:1">
            💾 Utwórz backup teraz
        </button>
    </div>
    
    <!-- WGRYWANIE ZEWNĘTRZNEGO BACKUPU -->
    <div class="card" style="padding:20px;margin-bottom:20px;background:rgba(249,115,22,0.1);border:2px solid #f97316">
        <div style="font-weight:600;font-size:1.1rem;margin-bottom:10px;color:#f97316">📤 Wgraj zewnętrzny backup</div>
        <div style="font-size:0.85rem;color:#94a3b8;margin-bottom:15px">
            Możesz wgrać stary plik bazy danych (.db) z komputera
        </div>
        <form action="/magazyn/backup/upload" method="POST" enctype="multipart/form-data" id="uploadBackupForm">
            <div style="display:flex;gap:10px;align-items:center">
                <div style="flex:1;position:relative">
                    <input type="file" id="backupFile" name="backup_file" accept=".db" style="display:none" onchange="updateFileName()">
                    <div onclick="document.getElementById('backupFile').click()" 
                         style="padding:12px 15px;background:#0a0a0f;border:1px dashed #f97316;border-radius:8px;cursor:pointer;text-align:center;color:#94a3b8">
                        <span id="fileNameDisplay">📁 Kliknij aby wybrać plik .db</span>
                    </div>
                </div>
                <button type="submit" class="btn" style="background:#f97316;padding:12px 20px" id="uploadBtn" disabled>
                    ⬆️ Wgraj
                </button>
            </div>
        </form>
    </div>
    
    <div class="section">📋 DOSTĘPNE BACKUPY</div>
    '''
    
    if not backups:
        html += '''
        <div class="card" style="padding:30px;text-align:center">
            <div style="font-size:3rem;opacity:0.3;margin-bottom:10px">📦</div>
            <div style="opacity:0.6">Brak backupów</div>
        </div>
        '''
    else:
        for backup in backups:
            # Weryfikacja backupu
            is_ok, status_msg = verify_backup(backup['filename'])
            status_icon = "✅" if is_ok else "❌"
            status_color = "var(--green)" if is_ok else "var(--red)"
            
            html += f'''
            <div class="card" style="padding:15px;margin-bottom:10px">
                <div style="display:flex;align-items:center;gap:15px">
                    <div style="font-size:2rem">{status_icon}</div>
                    <div style="flex:1">
                        <div style="font-weight:600;margin-bottom:3px">{backup['filename']}</div>
                        <div style="font-size:0.85rem;color:var(--text-dim)">
                            📅 {backup['created_str']} | 
                            💾 {backup['size_mb']:.2f} MB | 
                            <span style="color:{status_color}">{status_msg}</span>
                        </div>
                    </div>
                    <div style="display:flex;gap:8px">
                        <button onclick="restoreBackup('{backup['filename']}')" class="btn" style="background:var(--purple);padding:8px 15px;font-size:0.85rem">
                            ↩️ Przywróć
                        </button>
                    </div>
                </div>
            </div>
            '''
    
    html += '''
    <a href="/magazyn" class="back">← Powrót</a>
    
    <script>
    function updateFileName() {
        var input = document.getElementById('backupFile');
        var display = document.getElementById('fileNameDisplay');
        var btn = document.getElementById('uploadBtn');
        
        if (input.files.length > 0) {
            var file = input.files[0];
            var sizeMB = (file.size / 1024 / 1024).toFixed(2);
            display.innerHTML = '📁 ' + file.name + ' (' + sizeMB + ' MB)';
            display.style.color = '#f97316';
            btn.disabled = false;
        } else {
            display.innerHTML = '📁 Kliknij aby wybrać plik .db';
            display.style.color = '#94a3b8';
            btn.disabled = true;
        }
    }
    
    document.getElementById('uploadBackupForm').onsubmit = function(e) {
        if (!confirm('UWAGA!\\n\\nTo zastąpi aktualną bazę danych wgranym plikiem.\\nAktualna baza zostanie najpierw zbackupowana.\\n\\nKontynuować?')) {
            e.preventDefault();
            return false;
        }
        document.getElementById('uploadBtn').disabled = true;
        document.getElementById('uploadBtn').innerHTML = '⏳ Wgrywanie...';
    };
    
    function createBackup() {
        if (!confirm('Utworzyć backup bazy danych?')) return;
        
        showLoading('Tworzenie backupu...');
        
        fetch('/api/backup/create', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                hideLoading();
                if (data.success) {
                    showToast('Sukces', 'Backup utworzony: ' + data.backup, 'success');
                    setTimeout(() => location.reload(), 1000);
                } else {
                    showToast('Błąd', data.error || 'Nieznany błąd', 'error');
                }
            })
            .catch(err => {
                hideLoading();
                showToast('Błąd', 'Błąd połączenia: ' + err, 'error');
            });
    }
    
    function restoreBackup(filename) {
        if (!confirm('UWAGA! To przywróci bazę danych z backupu.\\n\\nObecna baza zostanie zastąpiona.\\n\\nKontynuować?')) {
            return;
        }
        
        showLoading('Przywracanie backupu...');
        
        fetch('/api/backup/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: filename })
        })
            .then(r => {
                if (!r.ok) {
                    throw new Error('HTTP ' + r.status);
                }
                return r.json();
            })
            .then(data => {
                hideLoading();
                if (data.success) {
                    showToast('Sukces', data.message, 'success');
                    setTimeout(() => location.href = '/magazyn', 2000);
                } else {
                    showToast('Błąd', data.message || data.error || 'Nieznany błąd', 'error');
                }
            })
            .catch(err => {
                hideLoading();
                showToast('Błąd', 'Błąd połączenia: ' + err.message, 'error');
            });
    }
    </script>
    '''
    
    return render(html)

@magazynier_bp.route('/backup/upload', methods=['POST'])
def backup_upload():
    """Wgrywanie zewnętrznego backupu bazy danych"""
    import sqlite3
    
    if 'backup_file' not in request.files:
        return render('<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">Nie wybrano pliku</div><a href="/magazyn/backup" class="btn btn-p">← Powrót</a>')
    
    file = request.files['backup_file']
    if file.filename == '':
        return render('<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">Nie wybrano pliku</div><a href="/magazyn/backup" class="btn btn-p">← Powrót</a>')
    
    if not file.filename.lower().endswith('.db'):
        return render('<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">Plik musi mieć rozszerzenie .db</div><a href="/magazyn/backup" class="btn btn-p">← Powrót</a>')
    
    try:
        # Ścieżki
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        backups_dir = os.path.join(base_dir, 'backups')
        
        # Utwórz folder backups jeśli nie istnieje
        os.makedirs(backups_dir, exist_ok=True)
        
        # Zapisz wgrany plik jako backup (z prefiksem uploaded_)
        backup_filename = f'uploaded_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
        backup_path = os.path.join(backups_dir, backup_filename)
        file.save(backup_path)
        
        # Weryfikuj że to poprawna baza SQLite
        try:
            test_conn = sqlite3.connect(backup_path)
            # Sprawdź czy ma tabelę produkty
            tables = test_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_names = [t[0] for t in tables]
            
            # Policz produkty
            count = 0
            if 'produkty' in table_names:
                count = test_conn.execute('SELECT COUNT(*) FROM produkty').fetchone()[0]
            
            test_conn.close()
            
            if 'produkty' not in table_names:
                os.remove(backup_path)
                return render('<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">Plik nie zawiera tabeli produkty - to nie jest baza Akces Hub</div><a href="/magazyn/backup" class="btn btn-p">← Powrót</a>')
            
        except sqlite3.DatabaseError as e:
            os.remove(backup_path)
            return render(f'<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">Plik nie jest poprawną bazą SQLite: {str(e)}</div><a href="/magazyn/backup" class="btn btn-p">← Powrót</a>')
        
        # Rozmiar pliku
        size_mb = os.path.getsize(backup_path) / 1024 / 1024
        
        html = f'''
        <div class="hdr"><h1>✅ BACKUP WGRANY</h1></div>
        
        <div class="alert alert-ok" style="margin-bottom:15px">
            Plik został dodany do listy backupów!
        </div>
        
        <div class="card" style="padding:20px;text-align:center">
            <div style="font-size:3rem;margin-bottom:10px">📦</div>
            <div style="font-weight:600;margin-bottom:5px">{backup_filename}</div>
            <div style="font-size:1.3rem;color:#22c55e">{count} produktów</div>
            <div style="font-size:0.85rem;color:#64748b">{size_mb:.2f} MB</div>
        </div>
        
        <div class="card" style="padding:15px;margin-top:15px;background:rgba(249,115,22,0.1);border:1px solid #f97316">
            <div style="font-size:0.9rem;color:#f97316">
                ⚠️ Aby aktywować ten backup, kliknij <strong>"↩️ Przywróć"</strong> przy nim na liście backupów
            </div>
        </div>
        
        <a href="/magazyn/backup" class="btn btn-ok" style="margin-top:20px">💾 Przejdź do listy backupów</a>
        <a href="/magazyn/backup" class="back">← Powrót</a>
        '''
        return render(html)
        
    except Exception as e:
        return render(f'<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">{str(e)}</div><a href="/magazyn/backup" class="btn btn-p">← Powrót</a>')

@magazynier_bp.route('/statystyki')
def statystyki():
    """Statystyki sprzedaży z wykresami"""
    import json
    conn = get_db()
    
    # FILTR: tylko opłacone (bez zwrotów i anulowanych)
    # STATUS_FILTER inlined in each query to avoid f-string SQL (B608)

    # Sprzedaż miesięcznie (bieżący rok)
    current_year = datetime.now().year
    miesieczne = conn.execute('''
        SELECT strftime('%m', REPLACE(SUBSTR(data_sprzedazy,1,19), 'T', ' ')) as miesiac,
               COUNT(*) as ilosc,
               COALESCE(SUM(cena * ilosc), 0) as suma
        FROM sprzedaze
        WHERE strftime('%Y', REPLACE(SUBSTR(data_sprzedazy,1,19), 'T', ' ')) = ?
          AND status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')
          AND data_sprzedazy IS NOT NULL AND data_sprzedazy != ''
        GROUP BY miesiac
        HAVING miesiac IS NOT NULL
        ORDER BY miesiac
    ''', (str(current_year),)).fetchall()
    
    # Sprzedaż dziennie dla każdego miesiąca (do drill-down) - z ilością zamówień!
    dzienne_all = conn.execute('''
        SELECT
            strftime('%m', REPLACE(SUBSTR(data_sprzedazy,1,19), 'T', ' ')) as miesiac,
            strftime('%d', REPLACE(SUBSTR(data_sprzedazy,1,19), 'T', ' ')) as dzien,
            COUNT(*) as cnt,
            COALESCE(SUM(cena * ilosc), 0) as suma
        FROM sprzedaze
        WHERE strftime('%Y', REPLACE(SUBSTR(data_sprzedazy,1,19), 'T', ' ')) = ?
          AND status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')
          AND data_sprzedazy IS NOT NULL
          AND data_sprzedazy != ''
        GROUP BY miesiac, dzien
        HAVING miesiac IS NOT NULL AND dzien IS NOT NULL
        ORDER BY miesiac, dzien
    ''', (str(current_year),)).fetchall()
    
    # Przygotuj dane dzienne per miesiąc (suma + ilość)
    dzienne_per_miesiac = {}
    dzienne_cnt_per_miesiac = {}
    for d in dzienne_all:
        m = int(d['miesiac'])
        if m not in dzienne_per_miesiac:
            dzienne_per_miesiac[m] = {}
            dzienne_cnt_per_miesiac[m] = {}
        dzien = int(d['dzien'])
        dzienne_per_miesiac[m][dzien] = float(d['suma'])
        dzienne_cnt_per_miesiac[m][dzien] = int(d['cnt'])
    
    # Sprzedaż rocznie (wszystkie lata) — z prywatnym (spójne z dashboardem)
    roczne = conn.execute('''
        SELECT rok, SUM(ilosc) as ilosc, SUM(suma) as suma FROM (
            SELECT strftime('%Y', data_sprzedazy) as rok,
                   COUNT(*) as ilosc,
                   COALESCE(SUM(cena * ilosc), 0) as suma
            FROM sprzedaze
            WHERE status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')
            GROUP BY rok
            UNION ALL
            SELECT strftime('%Y', data) as rok,
                   COUNT(*) as ilosc,
                   COALESCE(SUM(kwota), 0) as suma
            FROM sprzedaze_prywatne
            GROUP BY rok
        ) GROUP BY rok ORDER BY rok
    ''').fetchall()
    
    # Podsumowanie ogólne
    podsumowanie = conn.execute('''
        SELECT COUNT(*) as ilosc_transakcji,
               COALESCE(SUM(cena * ilosc), 0) as suma_total,
               COALESCE(AVG(cena * ilosc), 0) as srednia
        FROM sprzedaze
        WHERE status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''').fetchone()
    if podsumowanie is None:
        import sqlite3
        podsumowanie = sqlite3.Row.__new__(sqlite3.Row)
        podsumowanie = {'ilosc_transakcji': 0, 'suma_total': 0.0, 'srednia': 0.0}
    
    # Top 5 produktów (najczęściej sprzedawane) - bierze nazwę z sprzedaze
    top_produkty = conn.execute('''
        SELECT
            CASE
                WHEN s.nazwa IS NOT NULL AND s.nazwa != '' AND s.nazwa != 'Produkt' THEN SUBSTR(s.nazwa, 1, 50)
                WHEN o.tytul IS NOT NULL AND o.tytul != '' THEN SUBSTR(o.tytul, 1, 50)
                WHEN p.nazwa IS NOT NULL AND p.nazwa != '' THEN p.nazwa
                ELSE 'Produkt #' || s.id
            END as produkt_nazwa,
            COUNT(*) as sprzedane,
            SUM(s.cena * s.ilosc) as przychod
        FROM sprzedaze s
        LEFT JOIN oferty o ON s.oferta_id = o.id
        LEFT JOIN produkty p ON COALESCE(s.produkt_id, o.produkt_id) = p.id
        WHERE s.status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
        GROUP BY produkt_nazwa
        ORDER BY sprzedane DESC
        LIMIT 5
    ''').fetchall()
    
    # Top dostawcy - pobiera z palety przez produkt lub ofertę
    top_dostawcy = conn.execute('''
        SELECT
            COALESCE(pal.dostawca, pal2.dostawca, 'Allegro') as dostawca_nazwa,
            COUNT(*) as sprzedane,
            SUM(s.cena * s.ilosc) as przychod
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        LEFT JOIN palety pal ON p.paleta_id = pal.id
        LEFT JOIN oferty o ON s.oferta_id = o.id
        LEFT JOIN produkty p2 ON o.produkt_id = p2.id
        LEFT JOIN palety pal2 ON p2.paleta_id = pal2.id
        WHERE s.status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
        GROUP BY dostawca_nazwa
        ORDER BY przychod DESC
        LIMIT 5
    ''').fetchall()
    
    # Koszty miesięczne (bieżący rok)
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS koszty (id INTEGER PRIMARY KEY AUTOINCREMENT, nazwa TEXT, kwota REAL, kategoria TEXT, data DATE, notatka TEXT)')
        koszty_msc_rows = conn.execute('''
            SELECT strftime('%m', data) as m, SUM(kwota) as suma
            FROM koszty WHERE strftime('%Y', data) = ?
            GROUP BY m
        ''', (str(current_year),)).fetchall()
        koszty_per_msc = {int(r['m']): float(r['suma']) for r in koszty_msc_rows}
        koszty_total_rok = sum(koszty_per_msc.values())
    except:
        koszty_per_msc = {}
        koszty_total_rok = 0
    
    # Sprzedaż prywatna miesięcznie (bieżący rok)
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS sprzedaze_prywatne (id INTEGER PRIMARY KEY AUTOINCREMENT, opis TEXT, kwota REAL, data DATE, notatka TEXT)')
        pryw_msc_rows = conn.execute('''
            SELECT strftime('%m', data) as m, SUM(kwota) as suma
            FROM sprzedaze_prywatne WHERE strftime('%Y', data) = ?
            GROUP BY m
        ''', (str(current_year),)).fetchall()
        pryw_per_msc = {int(r['m']): float(r['suma']) for r in pryw_msc_rows}
        pryw_total_rok = sum(pryw_per_msc.values())
    except:
        pryw_per_msc = {}
        pryw_total_rok = 0

    # COGS per miesiąc — koszt SPRZEDANYCH produktów (nie kupionych palet)
    # koszt_per_szt = paleta.cena_zakupu / łączna_ilość_sztuk_z_palety
    try:
        cogs_msc_rows = conn.execute('''
            SELECT strftime('%m', s.data_sprzedazy) as m,
                COALESCE(SUM(
                    CASE
                        WHEN pal.cena_zakupu > 0 AND pal_total.total_szt > 0
                        THEN (pal.cena_zakupu / pal_total.total_szt) * s.ilosc
                        ELSE 0
                    END
                ), 0) as cogs
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            LEFT JOIN palety pal ON p.paleta_id = pal.id
            LEFT JOIN (
                SELECT pr.paleta_id,
                    COALESCE(SUM(pr.ilosc), 0)
                    + COALESCE(SUM(pr.sprzedano_offline), 0)
                    + COALESCE((
                        SELECT SUM(sp2.ilosc) FROM sprzedaze sp2
                        JOIN produkty pp2 ON sp2.produkt_id = pp2.id
                        WHERE pp2.paleta_id = pr.paleta_id
                        AND sp2.status NOT IN ('zwrot','anulowane','anulowana')
                    ), 0) as total_szt
                FROM produkty pr GROUP BY pr.paleta_id
            ) pal_total ON pal_total.paleta_id = pal.id
            WHERE strftime('%Y', s.data_sprzedazy) = ?
            AND s.status NOT IN ('zwrot', 'anulowane', 'anulowana')
            GROUP BY m
        ''', (str(current_year),)).fetchall()
        palety_per_msc = {int(r['m']): float(r['cogs'] or 0) for r in cogs_msc_rows}
        palety_total_rok = sum(palety_per_msc.values())
    except:
        palety_per_msc = {}
        palety_total_rok = 0

    # Ilość palet kupionych + kwota zakupu (do wyświetlania)
    try:
        palety_cnt_rows = conn.execute('''
            SELECT strftime('%m', data_zakupu) as m, COUNT(*) as cnt,
                   COALESCE(SUM(cena_zakupu), 0) as suma_zakupu
            FROM palety WHERE strftime('%Y', data_zakupu) = ?
            AND data_zakupu IS NOT NULL AND data_zakupu != ''
            GROUP BY m
        ''', (str(current_year),)).fetchall()
        palety_cnt_per_msc = {int(r['m']): int(r['cnt']) for r in palety_cnt_rows}
        palety_zakup_per_msc = {int(r['m']): float(r['suma_zakupu']) for r in palety_cnt_rows}
        palety_total_cnt_rok = sum(palety_cnt_per_msc.values())
    except:
        palety_cnt_per_msc = {}
        palety_zakup_per_msc = {}
        palety_total_cnt_rok = 0

    # ROI per paleta - ta sama logika co /analityka (proporcjonalny koszt sprzedanych)
    try:
        palety_roi_rows = conn.execute('''
            SELECT
                p.id,
                p.nazwa,
                p.dostawca,
                COALESCE(p.cena_zakupu, 0) as koszt_palety,
                COALESCE(p.ilosc_produktow, 0) as ilosc_produktow,
                (SELECT COALESCE(SUM(ilosc), 0) FROM produkty WHERE paleta_id = p.id) as aktualna_ilosc,
                (SELECT COALESCE(SUM(CASE WHEN status = 'sprzedany' AND (sprzedano_offline IS NULL OR sprzedano_offline = 0) THEN cena_allegro ELSE 0 END), 0) FROM produkty WHERE paleta_id = p.id) as przychod_produkty,
                COALESCE((SELECT SUM(s.cena * s.ilosc) FROM sprzedaze s JOIN produkty pr ON s.produkt_id = pr.id WHERE pr.paleta_id = p.id AND s.status NOT IN ('anulowana', 'zwrot') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')), 0) as przychod_tabela,
                (SELECT COALESCE(SUM(przychod_offline), 0) FROM produkty WHERE paleta_id = p.id) as przychod_offline,
                (SELECT COALESCE(SUM(sprzedano_offline), 0) FROM produkty WHERE paleta_id = p.id) as sprzedano_offline_szt,
                (SELECT COUNT(*) FROM produkty WHERE paleta_id = p.id AND status = 'sprzedany' AND (sprzedano_offline IS NULL OR sprzedano_offline = 0)) as sprzedano_produkty,
                COALESCE((SELECT SUM(s.ilosc) FROM sprzedaze s JOIN produkty pr ON s.produkt_id = pr.id WHERE pr.paleta_id = p.id AND s.status NOT IN ('anulowana', 'zwrot') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')), 0) as sprzedano_tabela
            FROM palety p
            ORDER BY p.id
        ''').fetchall()
        palety_roi = []
        for r in palety_roi_rows:
            koszt_palety = float(r['koszt_palety'] or 0)
            if koszt_palety <= 0:
                continue
            # Przychód - tylko z realnych transakcji (tabela sprzedaze) + offline
            # NIE używamy cena_allegro z produktów bo to cena wywoławcza, nie rzeczywista sprzedaż
            przychod_tabela = float(r['przychod_tabela'] or 0)
            przychod_offline = float(r['przychod_offline'] or 0)
            przychod = przychod_tabela + przychod_offline
            # Sprzedane sztuki - z tabeli sprzedaze (realne transakcje) + offline
            sprzedano_allegro = int(r['sprzedano_tabela'] or 0)
            sprzedano_offline = int(r['sprzedano_offline_szt'] or 0)
            sprzedanych = sprzedano_allegro + sprzedano_offline
            aktualna_ilosc = int(r['aktualna_ilosc'] or 0)
            ilosc_produktow = int(r['ilosc_produktow'] or 0)
            # Total: użyj ilosc_produktow z palety jeśli sensowne (> sprzedanych)
            # Fallback na aktualna + sprzedanych gdy ilosc_produktow nie wpisane lub za małe
            if ilosc_produktow > 0 and ilosc_produktow >= sprzedanych:
                total = ilosc_produktow
            else:
                total = aktualna_ilosc + sprzedanych
            if sprzedanych == 0 or przychod == 0:
                continue
            # Proporcjonalny koszt = koszt_palety * (sprzedane / wszystkie)
            udzial = (sprzedanych / total) if total > 0 else 1
            koszt_sprzedanych = koszt_palety * udzial
            zysk_pal = przychod - koszt_sprzedanych
            roi_pal = (zysk_pal / koszt_sprzedanych * 100)
            palety_roi.append({
                'nazwa': (r['nazwa'] or r['dostawca'] or f"Paleta #{r['id']}"),
                'koszt': koszt_sprzedanych,
                'koszt_palety': koszt_palety,
                'przychod': przychod,
                'zysk': zysk_pal,
                'roi': roi_pal,
                'sprzedane': sprzedanych,
                'total': total
            })
    except Exception as _e:
        palety_roi = []

    # ===== HISTOGRAM CZASU SPRZEDAŻY (od wystawienia do sprzedaży) =====
    sell_time_histogram = [0, 0, 0, 0, 0, 0, 0, 0]
    sell_time_labels = ['≤1 dzień', '2-3 dni', '4-7 dni', '1-2 tyg', '2-4 tyg', '1-2 mies', '2-3 mies', '3+ mies']
    najszybciej_sprzedane = []
    try:
        sell_times_raw = conn.execute('''
            SELECT 
                COALESCE(NULLIF(p.nazwa,''), NULLIF(s.nazwa,''), CASE WHEN s.allegro_order_id IS NOT NULL THEN 'Zamówienie #' || SUBSTR(s.allegro_order_id,-6) ELSE 'Brak nazwy' END) as nazwa,
                s.cena,
                s.data_sprzedazy,
                COALESCE(o.data_wystawienia, p.data_dodania) as data_od
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            LEFT JOIN oferty o ON s.oferta_id = o.id
            WHERE s.status NOT IN ('zwrot', 'anulowane', 'anulowana')
              AND s.data_sprzedazy IS NOT NULL AND s.data_sprzedazy != ''
            ORDER BY s.data_sprzedazy DESC LIMIT 500
        ''').fetchall()
        for row in sell_times_raw:
            try:
                dt_sprzed = datetime.fromisoformat(str(row['data_sprzedazy'])[:19].replace('T', ' '))
                data_od_val = row['data_od']
                if not data_od_val:
                    continue  # brak daty dodania — pomijamy w histogramie
                dt_od = datetime.fromisoformat(str(data_od_val)[:19].replace('T', ' '))
                diff_hours = (dt_sprzed - dt_od).total_seconds() / 3600
                if diff_hours < 0:
                    continue
                diff_days = diff_hours / 24
                if diff_days <= 1:       sell_time_histogram[0] += 1
                elif diff_days <= 3:     sell_time_histogram[1] += 1
                elif diff_days <= 7:     sell_time_histogram[2] += 1
                elif diff_days <= 14:    sell_time_histogram[3] += 1
                elif diff_days <= 28:    sell_time_histogram[4] += 1
                elif diff_days <= 60:    sell_time_histogram[5] += 1
                elif diff_days <= 90:    sell_time_histogram[6] += 1
                else:                    sell_time_histogram[7] += 1
                nazwa = row['nazwa'] or 'Produkt'
                time_str = f"{diff_hours:.0f}h" if diff_hours < 24 else f"{diff_days:.1f} dni"
                najszybciej_sprzedane.append({'czas': time_str, 'czas_h': diff_hours,
                    'nazwa': nazwa[:55], 'cena': float(row['cena'] or 0)})
            except:
                continue
        najszybciej_sprzedane.sort(key=lambda x: x['czas_h'])
        # Deduplikacja — każdy produkt tylko raz (najszybszy czas)
        _seen_ns = set()
        _unique_ns = []
        for item in najszybciej_sprzedane:
            if item['nazwa'] not in _seen_ns:
                _seen_ns.add(item['nazwa'])
                _unique_ns.append(item)
                if len(_unique_ns) >= 10:
                    break
        najszybciej_sprzedane = _unique_ns
    except:
        pass

    # Prebuduj HTML histogramu (unika zagnieżdżonych f-stringów)
    if sum(sell_time_histogram) == 0:
        histogram_html = '<div style="color:#64748b;font-size:0.85rem;text-align:center;padding:20px">Brak danych — synchronizuj sprzedaż z Allegro</div>'
    else:
        najszybciej_rows = ''.join([
            f'<div style="display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid #1e1e2e">'
            f'<div style="font-size:0.85rem;font-weight:700;color:#22c55e;min-width:60px">{item["czas"]}</div>'
            f'<div style="flex:1;font-size:0.8rem;color:#e2e8f0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{item["nazwa"]}</div>'
            f'<div style="font-size:0.8rem;color:#eab308;min-width:55px;text-align:right">{item["cena"]:.0f} zł</div>'
            f'</div>' for item in najszybciej_sprzedane
        ])
        histogram_html = (
            f'<canvas id="chartCzasSprzedazy" height="150" style="margin-bottom:15px"></canvas>'
            f'<div style="font-size:0.75rem;color:#22c55e;font-weight:600;margin-bottom:8px">⚡ Najszybciej sprzedane (od wystawienia)</div>'
            f'{najszybciej_rows}'
        )

    
    # Przygotuj dane do wykresów
    nazwy_miesiecy = ['Sty', 'Lut', 'Mar', 'Kwi', 'Maj', 'Cze', 'Lip', 'Sie', 'Wrz', 'Paź', 'Lis', 'Gru']
    przychod_total = podsumowanie['suma_total'] + pryw_total_rok
    koszty_total_lacznie = koszty_total_rok + palety_total_rok
    zysk_rok = przychod_total - koszty_total_lacznie
    zysk_kolor = '#22c55e' if zysk_rok >= 0 else '#ef4444'
    
    # === KALKULACJA PODATKOWA ===
    # VAT 23%, podatek liniowy 19%
    # Przychód = brutto (z VAT 23%) — zarówno Allegro jak i prywatne (faktury)
    # Koszty = brutto (z VAT 23%)
    # VAT ze sprzedaży
    vat_sprzedaz = przychod_total - (przychod_total / 1.23)
    # VAT z kosztów (odliczany)
    vat_koszty = koszty_total_lacznie - (koszty_total_lacznie / 1.23)
    # VAT do zapłaty = VAT ze sprzedaży - VAT z kosztów
    vat_do_zaplaty = vat_sprzedaz - vat_koszty
    # Przychód netto (bez VAT)
    przychod_netto = przychod_total / 1.23
    # Koszty netto (bez VAT) - odliczasz VAT z kosztów
    koszty_netto = koszty_total_lacznie / 1.23
    # Dochód do opodatkowania
    dochod = przychod_netto - koszty_netto
    # Podatek dochodowy 19%
    podatek = max(0, dochod * 0.19)
    # Prawdziwy zysk na rękę
    zysk_na_reke = dochod - podatek
    zysk_na_reke_kolor = '#22c55e' if zysk_na_reke >= 0 else '#ef4444'
    dane_miesieczne = [0] * 12
    dane_miesieczne_cnt = [0] * 12  # ilość zamówień per miesiąc
    dane_koszty = [0] * 12          # koszty per miesiąc
    dane_prywatne = [0] * 12
    dane_palety = [0] * 12
    dane_palety_cnt = [0] * 12  # ilość palet kupionych per miesiąc
    for m in miesieczne:
        idx = int(m['miesiac']) - 1
        dane_miesieczne[idx] = float(m['suma'])
        dane_miesieczne_cnt[idx] = int(m['ilosc'])
    for m_int, suma in koszty_per_msc.items():
        dane_koszty[m_int - 1] = suma
    for m_int, suma in pryw_per_msc.items():
        dane_prywatne[m_int - 1] = suma
    for m_int, suma in palety_per_msc.items():
        dane_palety[m_int - 1] = suma
    for m_int, cnt in palety_cnt_per_msc.items():
        dane_palety_cnt[m_int - 1] = cnt
    dane_palety_zakup = [0] * 12
    for m_int, suma in palety_zakup_per_msc.items():
        dane_palety_zakup[m_int - 1] = suma
    
    dane_roczne_labels = [r['rok'] for r in roczne] if roczne else [str(current_year)]
    dane_roczne_values = [float(r['suma']) for r in roczne] if roczne else [0]
    
    # Przygotuj dane dzienne jako JSON (dla drill-down) - suma i ilość
    dzienne_json = {}
    dzienne_cnt_json = {}
    for m in range(1, 13):
        dni_w_miesiacu = 31 if m in [1,3,5,7,8,10,12] else (30 if m in [4,6,9,11] else 29)
        dzienne_json[m] = [dzienne_per_miesiac.get(m, {}).get(d, 0) for d in range(1, dni_w_miesiacu+1)]
        dzienne_cnt_json[m] = [dzienne_cnt_per_miesiac.get(m, {}).get(d, 0) for d in range(1, dni_w_miesiacu+1)]
    
    # Status SYPIE - DZISIAJ (nie miesiąc!)
    current_month = datetime.now().month
    today_day = datetime.now().day
    today_sales = dzienne_per_miesiac.get(current_month, {}).get(today_day, 0)
    today_cnt = dzienne_cnt_per_miesiac.get(current_month, {}).get(today_day, 0)
    
    # 5 POZIOMÓW SYPANIA (bazowane na kwocie dziennej)
    if today_sales >= 5000:
        status_text = "🔥🔥🔥 MEGA SYPIE!"
        status_color = "#22c55e"
    elif today_sales >= 3000:
        status_text = "💸 SYPIE!"
        status_color = "#22c55e"
    elif today_sales >= 1500:
        status_text = "📈 Całkiem nieźle"
        status_color = "#eab308"
    elif today_sales >= 500:
        status_text = "🤏 Sypie trochę"
        status_color = "#f97316"
    else:
        status_text = "😴 NIE SYPIE"
        status_color = "#ef4444"
    
    html = f'''
    <div class="hdr"><h1>📊 STATYSTYKI</h1><small>Sprzedaż i przychody (tylko opłacone)</small></div>
    <div style="text-align:right;margin-bottom:10px">
        <a href="/sync-historyczny" style="font-size:0.75rem;color:#64748b;text-decoration:none;background:#1e1e2e;padding:5px 10px;border-radius:6px">🔄 Sync historyczny (poprzednie miesiące)</a>
    </div>
    
    <!-- STATUS SYPIE - DZISIAJ -->
    <div style="background:linear-gradient(135deg, {status_color}22, {status_color}11);border:2px solid {status_color};border-radius:16px;padding:20px;margin-bottom:20px;text-align:center">
        <div style="font-size:2.5rem;font-weight:700;color:{status_color}">{status_text}</div>
        <div style="font-size:0.9rem;color:#94a3b8;margin-top:5px">
            Dzisiaj ({datetime.now().strftime('%d.%m')}): <strong>{today_sales:.0f} zł</strong> | <strong>{today_cnt}</strong> zamówień
        </div>
    </div>
    
    <!-- Podsumowanie -->
    <div class="stats" style="margin-bottom:20px">
        <div class="stat">
            <div class="stat-v">{podsumowanie['ilosc_transakcji']}</div>
            <div class="stat-l">Transakcji</div>
        </div>
        <div class="stat">
            <div class="stat-v green">{przychod_total:.0f} zł</div>
            <div class="stat-l">Przychód ({current_year}){f' (w tym 🤝 {pryw_total_rok:.0f} zł prywatne)' if pryw_total_rok > 0 else ''}</div>
        </div>
        <div class="stat">
            <div class="stat-v" style="color:#f43f5e">-{koszty_total_lacznie:.0f} zł</div>
            <div class="stat-l">Koszty ({current_year}) <span style="font-size:0.65rem;color:#64748b">(w tym 📦 {palety_total_rok:.0f} zł palety)</span> <a href="/magazyn/koszty" style="color:#64748b;font-size:0.7rem;margin-left:4px">+dodaj</a></div>
        </div>
        <div class="stat">
            <div class="stat-v" style="color:{zysk_kolor}">{zysk_rok:.0f} zł</div>
            <div class="stat-l">Zysk brutto (przed podatkiem)</div>
        </div>
    </div>
    
    <!-- HISTOGRAM CZASU SPRZEDAŻY -->
    <div class="card" style="padding:15px;margin-bottom:15px;border:1px solid #22c55e44">
        <div style="font-weight:700;margin-bottom:12px;color:#22c55e">⏱️ Czas sprzedaży (od wystawienia)</div>
        {histogram_html}
    </div>
    
    <!-- Kalkulacja podatkowa -->
    <div class="card" style="padding:15px;margin-bottom:15px;border:1px solid #8b5cf644">
        <div style="font-weight:700;margin-bottom:12px;color:#a78bfa">🧾 Rozliczenie podatkowe ({current_year})</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:0.85rem">
            <div style="background:#12121a;border-radius:8px;padding:10px">
                <div style="color:#64748b;margin-bottom:4px">Przychód brutto</div>
                <div style="font-weight:700;color:#3b82f6">{przychod_total:.0f} zł</div>
            </div>
            <div style="background:#12121a;border-radius:8px;padding:10px">
                <div style="color:#64748b;margin-bottom:4px">Przychód netto (bez VAT)</div>
                <div style="font-weight:700">{przychod_netto:.0f} zł</div>
            </div>
            <div style="background:#12121a;border-radius:8px;padding:10px">
                <div style="color:#64748b;margin-bottom:4px">Koszty netto (bez VAT)</div>
                <div style="font-weight:700;color:#f43f5e">-{koszty_netto:.0f} zł</div>
            </div>
            <div style="background:#12121a;border-radius:8px;padding:10px">
                <div style="color:#64748b;margin-bottom:4px">Dochód do opodatkowania</div>
                <div style="font-weight:700">{dochod:.0f} zł</div>
            </div>
            <div style="background:#1a1025;border:1px solid #ef444433;border-radius:8px;padding:10px">
                <div style="color:#64748b;margin-bottom:4px">VAT do zapłaty (23%)</div>
                <div style="font-weight:700;color:#ef4444">-{vat_do_zaplaty:.0f} zł</div>
                <div style="font-size:0.7rem;color:#475569;margin-top:2px">VAT sprzedaż {vat_sprzedaz:.0f} − VAT koszty {vat_koszty:.0f}</div>
            </div>
            <div style="background:#1a1025;border:1px solid #ef444433;border-radius:8px;padding:10px">
                <div style="color:#64748b;margin-bottom:4px">Podatek dochodowy (19%)</div>
                <div style="font-weight:700;color:#ef4444">-{podatek:.0f} zł</div>
                <div style="font-size:0.7rem;color:#475569;margin-top:2px">{dochod:.0f} × 19%</div>
            </div>
        </div>
        <div style="margin-top:12px;padding:12px;background:#0a1f12;border:2px solid {zysk_na_reke_kolor}55;border-radius:10px;display:flex;justify-content:space-between;align-items:center">
            <div style="color:#94a3b8;font-size:0.9rem">💰 Zysk na rękę (po VAT i podatku)</div>
            <div style="font-size:1.4rem;font-weight:700;color:{zysk_na_reke_kolor}">{zysk_na_reke:.0f} zł</div>
        </div>
        <div style="font-size:0.7rem;color:#475569;margin-top:8px;text-align:center">⚠️ Szacunkowe — skonsultuj z księgową. Nie uwzględnia ZUS, ulg i odpisów.</div>
    </div>
    '''

    # === RENTOWNOŚĆ PALET ROI ===
    html_roi = ''
    if palety_roi:
        roi_total_koszt = sum(p['koszt'] for p in palety_roi)
        roi_total_przychod = sum(p['przychod'] for p in palety_roi)
        roi_total_zysk = roi_total_przychod - roi_total_koszt
        roi_total = (roi_total_zysk / roi_total_koszt * 100) if roi_total_koszt > 0 else 0
        roi_sredni = sum(p['roi'] for p in palety_roi) / len(palety_roi)
        roi_total_kolor = '#22c55e' if roi_total >= 0 else '#ef4444'
        roi_sr_kolor = '#22c55e' if roi_sredni >= 0 else '#ef4444'
        sorted_desc = sorted(palety_roi, key=lambda x: x['roi'], reverse=True)
        sorted_asc = sorted(palety_roi, key=lambda x: x['roi'])
        # Pokaż max 5 najlepszych, ale nie więcej niż połowa
        top_n = min(5, max(1, len(palety_roi) // 2))
        top3 = sorted_desc[:top_n]
        top_names = {p['nazwa'] for p in top3}
        # Najgorsze - wyklucz te które już są w najlepszych
        worst3 = [p for p in sorted_asc if p['nazwa'] not in top_names][:top_n]
        worst_label = '📉 Najgorsze' if worst3 and worst3[0]['roi'] < 0 else '📊 Najmniej rentowne'

        def _roi_row(p):
            kol = '#22c55e' if p['roi'] >= 0 else '#ef4444'
            pct = min(100, max(0, abs(p['roi'])))
            sign = '+' if p['roi'] >= 0 else ''
            bar = f'<div style="height:5px;background:#1e1e2e;border-radius:3px;margin-top:4px"><div style="height:5px;width:{pct:.0f}%;background:{kol};border-radius:3px"></div></div>'
            return (f'<div style="background:#12121a;border-radius:8px;padding:10px;margin-bottom:6px">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center">'
                    f'<div style="font-size:0.8rem;color:#e2e8f0;flex:1;margin-right:8px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">{p["nazwa"][:35]}</div>'
                    f'<div style="font-weight:700;color:{kol};white-space:nowrap">{sign}{p["roi"]:.0f}%</div>'
                    f'</div>'
                    f'<div style="font-size:0.7rem;color:#64748b;margin-top:2px">{p.get("sprzedane","?")}/{p.get("total","?")} szt. · koszt prop. {p["koszt"]:.0f} zł / {p.get("koszt_palety", p["koszt"]):.0f} zł · zysk {"+" if p["zysk"]>=0 else ""}{p["zysk"]:.0f} zł</div>'
                    f'{bar}'
                    f'</div>')

        top_html = ''.join(_roi_row(p) for p in top3)
        worst_html = ''.join(_roi_row(p) for p in worst3)

        html_roi = f'''<div class="card" style="padding:15px;margin-bottom:15px;border:1px solid #22c55e33">
        <div style="font-weight:700;margin-bottom:12px;color:#22c55e">📦 Rentowność palet ({current_year})</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:14px">
            <div style="background:#0a1f12;border:1px solid {roi_total_kolor}44;border-radius:10px;padding:12px;text-align:center">
                <div style="font-size:1.6rem;font-weight:700;color:{roi_total_kolor}">{roi_total:.0f}%</div>
                <div style="font-size:0.7rem;color:#64748b;margin-top:2px">ROI całkowity</div>
            </div>
            <div style="background:#12121a;border-radius:10px;padding:12px;text-align:center">
                <div style="font-size:1.6rem;font-weight:700;color:#a78bfa">{len(palety_roi)}</div>
                <div style="font-size:0.7rem;color:#64748b;margin-top:2px">Palety z danymi</div>
            </div>
            <div style="background:#12121a;border-radius:10px;padding:12px;text-align:center">
                <div style="font-size:1.6rem;font-weight:700;color:{roi_sr_kolor}">{roi_sredni:.0f}%</div>
                <div style="font-size:0.7rem;color:#64748b;margin-top:2px">Średni ROI</div>
            </div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div>
                <div style="font-size:0.75rem;color:#22c55e;font-weight:600;margin-bottom:6px">🏆 Najlepsze</div>
                {top_html}
            </div>
            <div>
                <div style="font-size:0.75rem;color:#eab308;font-weight:600;margin-bottom:6px">{worst_label}</div>
                {worst_html}
            </div>
        </div>
        <div style="font-size:0.7rem;color:#475569;margin-top:10px;text-align:center">ROI = (Przychód − Koszt palety) ÷ Koszt × 100%  |  <a href="/analityka" style="color:#64748b">📊 Szczegółowa analityka →</a></div>
    </div>'''

    html += html_roi

    # Buduj HTML palet per miesiąc (osobno, bo zagnieżdżone f-stringi w f-strings nie działają)
    nazwy_msc = ["Sty","Lut","Mar","Kwi","Maj","Cze","Lip","Sie","Wrz","Paź","Lis","Gru"]
    palety_cells = ''
    for i in range(12):
        cnt = dane_palety_cnt[i]
        kolor = '#3b82f6' if cnt > 0 else '#2d2d48'
        palety_cells += f'<div style="background:#1e1e2e;border-radius:8px;padding:8px;text-align:center"><div style="font-size:0.65rem;color:#64748b">{nazwy_msc[i]}</div><div style="font-size:1.1rem;font-weight:700;color:{kolor}">{cnt}</div></div>'

    html += f'''
    <!-- Wykres miesięczny z drill-down -->
    <div class="card" style="padding:15px;margin-bottom:15px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
            <div id="chartTitle" style="font-weight:600">📅 Sprzedaż miesięcznie ({current_year})</div>
            <button id="btnBack" onclick="showMonthlyView()" style="display:none;padding:5px 10px;background:#3b82f6;border:none;border-radius:5px;color:#fff;cursor:pointer">← Miesiące</button>
        </div>
        <div style="font-size:0.75rem;color:#64748b;margin-bottom:10px">💡 Kliknij na słupek miesiąca aby zobaczyć rozkład dzienny</div>
        <div id="monthSummary" style="display:none"></div>
        <canvas id="chartMiesiace" height="200"></canvas>
    </div>
    
    <!-- Wykres roczny -->
    <div class="card" style="padding:15px;margin-bottom:15px">
        <div style="font-weight:600;margin-bottom:10px">📈 Sprzedaż rocznie</div>
        <canvas id="chartLata" height="150"></canvas>
    </div>

    <!-- Palety kupione per miesiąc -->
    <div class="card" style="padding:15px;margin-bottom:15px">
        <div style="font-weight:600;margin-bottom:10px">📦 Palety kupione ({current_year}) — łącznie {palety_total_cnt_rok} szt.</div>
        <div style="display:grid;grid-template-columns:repeat(6, 1fr);gap:6px">
            {palety_cells}
        </div>
    </div>

    <!-- Top produkty -->
    <div class="section">🏆 TOP 5 PRODUKTÓW</div>
    '''
    
    if top_produkty:
        for i, p in enumerate(top_produkty):
            html += f'''<div class="item">
                <div style="font-size:1.2rem;margin-right:10px;width:25px;text-align:center">{i+1}</div>
                <div class="item-info">
                    <div class="item-name">{(p['produkt_nazwa'] or 'Nieznany')[:35]}</div>
                    <div class="item-meta">Sprzedano: {p['sprzedane']}x</div>
                </div>
                <div class="item-right">
                    <div class="item-qty" style="color:#22c55e">{p['przychod'] or 0:.0f} zł</div>
                </div>
            </div>'''
    else:
        html += '<div class="alert alert-warn">Brak danych o sprzedaży</div>'
    
    # Top dostawcy
    html += '<div class="section">🚚 TOP DOSTAWCY</div>'
    
    if top_dostawcy:
        for i, d in enumerate(top_dostawcy):
            html += f'''<div class="item">
                <div style="font-size:1.2rem;margin-right:10px;width:25px;text-align:center">{i+1}</div>
                <div class="item-info">
                    <div class="item-name">{d['dostawca_nazwa']}</div>
                    <div class="item-meta">Sprzedano: {d['sprzedane']}x</div>
                </div>
                <div class="item-right">
                    <div class="item-qty" style="color:#22c55e">{d['przychod'] or 0:.0f} zł</div>
                </div>
            </div>'''
    else:
        html += '<div class="alert alert-warn">Brak danych o dostawcach</div>'
    
    html += f'''
    <a href="/magazyn" class="back">← Powrót</a>
    
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>
    // Dane
    const nazwyMiesiecy = {json.dumps(nazwy_miesiecy)};
    const daneMiesieczne = {json.dumps(dane_miesieczne)};
    const daneMiesieczneCnt = {json.dumps(dane_miesieczne_cnt)};
    const daneKoszty = {json.dumps(dane_koszty)};
    const danePrywatne = {json.dumps(dane_prywatne)};
    const danePalety = {json.dumps(dane_palety)};
    const danePaletyZakup = {json.dumps(dane_palety_zakup)};
    const danePaletyCnt = {json.dumps([dane_palety_cnt[i] for i in range(12)])};
    // Łączne koszty = operacyjne + COGS (koszt sprzedanych produktów)
    const daneKosztyLacznie = daneKoszty.map((k, i) => k + danePalety[i]);
    // null dla miesięcy bez aktywności - linia nie spada do zera
    const daneZysk = daneMiesieczne.map((p, i) => {{
        const total = p + danePrywatne[i];
        if (total === 0 && daneKosztyLacznie[i] === 0) return null;
        return total - daneKosztyLacznie[i];
    }});

    // Histogram czasu sprzedaży
    const histLabels = {json.dumps(sell_time_labels)};
    const histData = {json.dumps(sell_time_histogram)};
    if (document.getElementById('chartCzasSprzedazy') && histData.some(v => v > 0)) {{
        new Chart(document.getElementById('chartCzasSprzedazy'), {{
            type: 'bar',
            data: {{
                labels: histLabels,
                datasets: [{{
                    label: 'Sprzedaży',
                    data: histData,
                    backgroundColor: histData.map((v, i) => i === 0 ? 'rgba(34,197,94,0.9)' : i <= 1 ? 'rgba(34,197,94,0.7)' : i <= 2 ? 'rgba(234,179,8,0.7)' : 'rgba(100,116,139,0.6)'),
                    borderRadius: 6
                }}]
            }},
            options: {{
                responsive: true,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    y: {{ beginAtZero: true, grid: {{ color: 'rgba(255,255,255,0.07)' }}, ticks: {{ color: '#64748b', stepSize: 1 }} }},
                    x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8', font: {{ size: 11 }} }} }}
                }}
            }}
        }});
    }}
    const daneDzienne = {json.dumps(dzienne_json)};
    const daneDzienneCnt = {json.dumps(dzienne_cnt_json)};
    
    let chartMiesiace = null;
    let currentView = 'monthly'; // monthly lub daily
    let currentMonth = 0;
    
    // Funkcja sprawdzająca czy dzień "sypał" (>= 5 zamówień i >= 300 zł)
    // Funkcja zwracająca kolor słupka według 5 progów (bazowane tylko na kwocie)
    function getBarColor(kwota) {{
        if (kwota >= 5000) return 'rgba(22, 163, 74, 0.9)';   // ciemny zielony - MEGA SYPIE
        if (kwota >= 3000) return 'rgba(34, 197, 94, 0.9)';   // zielony - SYPIE
        if (kwota >= 1500) return 'rgba(234, 179, 8, 0.9)';   // żółty - całkiem nieźle
        if (kwota >= 500) return 'rgba(249, 115, 22, 0.9)';   // pomarańczowy - sypie trochę
        return 'rgba(59, 130, 246, 0.6)';                      // niebieski - nie sypie
    }}
    
    // Funkcja sprawdzająca czy dzień "sypał" (>= 1500 zł)
    function czySypalo(kwota, cnt) {{
        return kwota >= 1500;
    }}
    
    // Inicjalizacja wykresu miesięcznego
    function initMonthlyChart() {{
        const ctx = document.getElementById('chartMiesiace');
        chartMiesiace = new Chart(ctx, {{
            type: 'bar',
            data: {{
                labels: nazwyMiesiecy,
                datasets: [{{
                    label: 'Przychód (zł)',
                    data: daneMiesieczne.map((v, i) => v + danePrywatne[i]),
                    backgroundColor: 'rgba(59, 130, 246, 0.6)',
                    borderColor: 'rgba(59, 130, 246, 1)',
                    borderWidth: 1,
                    borderRadius: 5,
                    order: 2
                }}, {{
                    label: 'Koszty (zł)',
                    data: daneKosztyLacznie,
                    backgroundColor: 'rgba(244, 63, 94, 0.5)',
                    borderColor: 'rgba(244, 63, 94, 1)',
                    borderWidth: 1,
                    borderRadius: 5,
                    order: 1
                }}, {{
                    label: 'Zakup palet (zł)',
                    data: danePaletyZakup,
                    backgroundColor: 'rgba(251, 191, 36, 0.5)',
                    borderColor: 'rgba(251, 191, 36, 1)',
                    borderWidth: 1,
                    borderRadius: 5,
                    order: 1
                }}, {{
                    label: 'Zysk netto',
                    data: daneZysk,
                    type: 'line',
                    borderColor: '#22c55e',
                    backgroundColor: 'rgba(34,197,94,0.1)',
                    borderWidth: 2,
                    pointRadius: 4,
                    pointBackgroundColor: '#22c55e',
                    tension: 0.3,
                    fill: false,
                    order: 0
                }}]
            }},
            options: {{
                responsive: true,
                onClick: function(event, elements, chart) {{
                    if (currentView === 'monthly' && elements && elements.length > 0) {{
                        const index = elements[0].index;
                        showDailyView(index + 1);
                    }}
                }},
                plugins: {{
                    legend: {{ display: true, labels: {{ color: '#94a3b8', font: {{ size: 11 }} }} }},
                    tooltip: {{
                        callbacks: {{
                            afterBody: function(items) {{
                                if (currentView !== 'monthly') return [];
                                const i = items[0].dataIndex;
                                const cnt = daneMiesieczneCnt[i];
                                const koszty = daneKosztyLacznie[i];
                                const pryw = danePrywatne[i];
                                const palety = danePalety[i];
                                const zysk = daneMiesieczne[i] + pryw - koszty;
                                const zakupPalet = danePaletyZakup[i];
                                const cntPalet = danePaletyCnt[i];
                                const lines = [cnt + ' zamowien'];
                                if (pryw > 0) lines.push('+ ' + pryw.toFixed(0) + ' zl prywatna');
                                if (zakupPalet > 0) lines.push('🛒 ' + cntPalet + ' palet kupiono za ' + zakupPalet.toFixed(0) + ' zl');
                                if (palety > 0) lines.push('- ' + palety.toFixed(0) + ' zl COGS (sprzedanych)');
                                if (koszty > palety) lines.push('- ' + (koszty-palety).toFixed(0) + ' zl inne koszty');
                                lines.push('Zysk: ' + zysk.toFixed(0) + ' zl');
                                return lines;
                            }},
                            afterLabel: function(context) {{
                                if (currentView !== 'monthly') {{
                                    const cnt = daneDzienneCnt[currentMonth] ? daneDzienneCnt[currentMonth][context.dataIndex] : 0;
                                    const sypalo = czySypalo(context.raw, cnt);
                                    return cnt + ' zamowien ' + (sypalo ? 'SYPALO!' : '');
                                }}
                                return '';
                            }}
                        }}
                    }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true,
                        grid: {{ color: 'rgba(255,255,255,0.1)' }},
                        ticks: {{ color: '#64748b' }}
                    }},
                    x: {{
                        grid: {{ display: false }},
                        ticks: {{ color: '#64748b' }}
                    }}
                }}
            }}
        }});
    }}
    
    // Pokaż widok dzienny
    function showDailyView(month) {{
        currentView = 'daily';
        currentMonth = month;
        
        const daysInMonth = daneDzienne[month] ? daneDzienne[month].length : 31;
        const dayLabels = Array.from({{length: daysInMonth}}, (_, i) => (i + 1).toString());
        const dailyData = daneDzienne[month] || [];
        
        const colors = dailyData.map(kwota => getBarColor(kwota));
        
        // Koszty miesięczne rozłożone równo na każdy dzień
        const kosztMiesieczny = daneKoszty[month - 1] || 0;
        const kosztDzienny = kosztMiesieczny / daysInMonth;
        // null dla dni bez sprzedaży żeby linia nie wisiała w powietrzu
        const dailyZysk = dailyData.map(p => p === 0 ? null : parseFloat((p - kosztDzienny).toFixed(2)));
        
        chartMiesiace.data.labels = dayLabels;
        chartMiesiace.data.datasets[0].data = dailyData;
        chartMiesiace.data.datasets[0].backgroundColor = colors;
        chartMiesiace.data.datasets[0].borderColor = colors.map(c => c.replace('0.9', '1').replace('0.6', '0.8'));
        // Koszty dzienne jako słupki
        chartMiesiace.data.datasets[1].data = new Array(daysInMonth).fill(parseFloat(kosztDzienny.toFixed(2)));
        chartMiesiace.data.datasets[1].hidden = kosztMiesieczny === 0;
        // Ukryj zakup palet w widoku dziennym
        chartMiesiace.data.datasets[2].data = new Array(daysInMonth).fill(0);
        chartMiesiace.data.datasets[2].hidden = true;
        // Zysk netto dzienny jako linia
        chartMiesiace.data.datasets[3].data = dailyZysk;
        chartMiesiace.data.datasets[3].hidden = false;
        chartMiesiace.update();
        
        document.getElementById('chartTitle').textContent = '📅 ' + nazwyMiesiecy[month-1] + ' {current_year} - rozkład dzienny';
        document.getElementById('btnBack').style.display = 'inline-block';
        
        // Podsumowanie miesiąca
        const przychod = (daneMiesieczne[month-1] || 0) + (danePrywatne[month-1] || 0);
        const koszty = daneKosztyLacznie[month-1] || 0;
        const kosztPalety = danePalety[month-1] || 0;
        const zysk = przychod - koszty;
        const zyskKolor = zysk >= 0 ? '#22c55e' : '#ef4444';
        const cnt = daneMiesieczneCnt[month-1] || 0;
        document.getElementById('monthSummary').innerHTML = `
            <div style="display:flex;gap:10px;margin-bottom:12px;flex-wrap:wrap">
                <div style="flex:1;min-width:100px;background:#1e1e2e;border-radius:10px;padding:10px;text-align:center">
                    <div style="font-size:1.1rem;font-weight:700;color:#3b82f6">${{przychod.toFixed(0)}} zł</div>
                    <div style="font-size:0.7rem;color:#64748b;margin-top:2px">Przychód</div>
                </div>
                <div style="flex:1;min-width:100px;background:#1e1e2e;border-radius:10px;padding:10px;text-align:center">
                    <div style="font-size:1.1rem;font-weight:700;color:#f43f5e">-${{koszty.toFixed(0)}} zł</div>
                    <div style="font-size:0.7rem;color:#64748b;margin-top:2px">Koszty (COGS${{kosztPalety > 0 ? ' ' + kosztPalety.toFixed(0) + ' zł' : ''}})</div>
                </div>
                <div style="flex:1;min-width:100px;background:#1e1e2e;border-radius:10px;padding:10px;text-align:center">
                    <div style="font-size:1.1rem;font-weight:700">${{cnt}}</div>
                    <div style="font-size:0.7rem;color:#64748b;margin-top:2px">Zamówień</div>
                </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:8px">
                ${{(function() {{
                    const vatSprzedaz = przychod - (przychod / 1.23);
                    const vatKoszty = koszty - (koszty / 1.23);
                    const vatDoZaplaty = vatSprzedaz - vatKoszty;
                    const przychodNetto = przychod / 1.23;
                    const kosztaNetto = koszty / 1.23;
                    const dochod = przychodNetto - kosztaNetto;
                    const podatek = Math.max(0, dochod * 0.19);
                    const naReke = dochod - podatek;
                    const kolor = naReke >= 0 ? '#22c55e' : '#ef4444';
                    return `
                    <div style="background:#1a1025;border:1px solid #ef444433;border-radius:8px;padding:8px;text-align:center">
                        <div style="font-size:0.95rem;font-weight:700;color:#ef4444">-${{vatDoZaplaty.toFixed(0)}} zł</div>
                        <div style="font-size:0.65rem;color:#64748b;margin-top:2px">VAT do zapłaty</div>
                    </div>
                    <div style="background:#1a1025;border:1px solid #ef444433;border-radius:8px;padding:8px;text-align:center">
                        <div style="font-size:0.95rem;font-weight:700;color:#ef4444">-${{podatek.toFixed(0)}} zł</div>
                        <div style="font-size:0.65rem;color:#64748b;margin-top:2px">Podatek 19%</div>
                    </div>
                    <div style="background:#0a1f12;border:2px solid ${{kolor}}55;border-radius:8px;padding:8px;text-align:center">
                        <div style="font-size:0.95rem;font-weight:700;color:${{kolor}}">${{naReke.toFixed(0)}} zł</div>
                        <div style="font-size:0.65rem;color:#64748b;margin-top:2px">Na rękę</div>
                    </div>`;
                }})()}}
            </div>`;
        document.getElementById('monthSummary').style.display = 'block';
    }}
    
    // Wróć do widoku miesięcznego
    function showMonthlyView() {{
        currentView = 'monthly';
        currentMonth = 0;
        
        chartMiesiace.data.labels = nazwyMiesiecy;
        chartMiesiace.data.datasets[0].data = daneMiesieczne.map((v, i) => v + danePrywatne[i]);
        chartMiesiace.data.datasets[0].backgroundColor = 'rgba(59, 130, 246, 0.6)';
        chartMiesiace.data.datasets[0].borderColor = 'rgba(59, 130, 246, 1)';
        chartMiesiace.data.datasets[1].data = daneKosztyLacznie;
        chartMiesiace.data.datasets[1].hidden = false;
        chartMiesiace.data.datasets[2].data = danePaletyZakup;
        chartMiesiace.data.datasets[2].hidden = false;
        chartMiesiace.data.datasets[3].data = daneZysk;
        chartMiesiace.data.datasets[3].hidden = false;
        chartMiesiace.data.datasets[3].spanGaps = false;
        chartMiesiace.update();
        
        document.getElementById('chartTitle').textContent = '📅 Sprzedaż miesięcznie ({current_year})';
        document.getElementById('btnBack').style.display = 'none';
        document.getElementById('monthSummary').style.display = 'none';
        document.getElementById('monthSummary').innerHTML = '';
    }}
    
    // Inicjalizacja
    initMonthlyChart();

    // Auto-otwórz miesiąc z URL (?miesiac=2026-03)
    const urlParams = new URLSearchParams(window.location.search);
    const paramMiesiac = urlParams.get('miesiac');
    if (paramMiesiac) {{
        const parts = paramMiesiac.split('-');
        if (parts.length === 2) {{
            const monthNum = parseInt(parts[1]);
            if (monthNum >= 1 && monthNum <= 12) {{
                setTimeout(() => showDailyView(monthNum), 500);
            }}
        }}
    }}
    
    // Wykres roczny
    new Chart(document.getElementById('chartLata'), {{
        type: 'bar',
        data: {{
            labels: {json.dumps(dane_roczne_labels)},
            datasets: [{{
                label: 'Przychód (zł)',
                data: {json.dumps(dane_roczne_values)},
                backgroundColor: 'rgba(34, 197, 94, 0.8)',
                borderColor: 'rgba(34, 197, 94, 1)',
                borderWidth: 1,
                borderRadius: 5
            }}]
        }},
        options: {{
            responsive: true,
            plugins: {{
                legend: {{ display: false }}
            }},
            scales: {{
                y: {{
                    beginAtZero: true,
                    grid: {{ color: 'rgba(255,255,255,0.1)' }},
                    ticks: {{ color: '#64748b' }}
                }},
                x: {{
                    grid: {{ display: false }},
                    ticks: {{ color: '#64748b' }}
                }}
            }}
        }}
    }});
    </script>
    '''
    return render(html)

@magazynier_bp.route('/palety')
def palety():
    from urllib.parse import quote
    conn = get_db()
    
    # Pobierz palety z tabeli palety + statystyki z produkty
    result = conn.execute('''
        SELECT p.id, p.nazwa, p.dostawca, p.data_zakupu, p.cena_zakupu, p.cena_zakupu_netto,
               p.ilosc_sztuk, 0 as dostarczona,
               COUNT(pr.id) as cnt,
               COALESCE(SUM(pr.ilosc), 0) as items,
               COALESCE(SUM(pr.cena_allegro * pr.ilosc), 0) as wartosc_allegro
        FROM palety p
        LEFT JOIN produkty pr ON pr.paleta_id = p.id
        GROUP BY p.id
        ORDER BY p.data_dodania DESC
    ''').fetchall()
    
    # Dodaj też produkty bez palety
    bez_palety = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(ilosc), 0) as items,
               COALESCE(SUM(cena_brutto), 0) as wartosc_zakupu,
               COALESCE(SUM(cena_allegro * ilosc), 0) as wartosc_allegro
        FROM produkty WHERE paleta_id IS NULL OR paleta_id = 0
    ''').fetchone()
    
    # Pobierz dostarczona osobno (z auto-migracją jeśli kolumna nie istnieje)
    dostarczona_map = {}
    try:
        d_rows = conn.execute('SELECT id, COALESCE(dostarczona, 0) as dostarczona FROM palety').fetchall()
        dostarczona_map = {r['id']: r['dostarczona'] for r in d_rows}
    except:
        try:
            conn.execute('ALTER TABLE palety ADD COLUMN dostarczona INTEGER DEFAULT 0')
            conn.commit()
            print('✅ Dodano kolumnę dostarczona')
        except:
            pass

    
    html = '''<div class="hdr"><h1>📦 PALETY</h1></div>

    <!-- Masowa edycja -->
    <div style="background:#12121a;border:1px solid #1e293b;border-radius:12px;padding:12px;margin-bottom:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <div style="font-size:0.85rem;color:#64748b;margin-right:4px">Zaznaczone:</div>
        <button onclick="selectAll()" style="padding:5px 10px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#94a3b8;font-size:0.75rem;cursor:pointer">☑️ Wszystkie</button>
        <button onclick="selectNone()" style="padding:5px 10px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#94a3b8;font-size:0.75rem;cursor:pointer">◻️ Odznacz</button>
        <div style="flex:1"></div>
        <button onclick="massUpdate(1)" style="padding:6px 12px;background:#22c55e22;border:1px solid #22c55e;border-radius:8px;color:#22c55e;font-size:0.8rem;cursor:pointer;font-weight:600">✅ Dostarczone</button>
        <button onclick="massUpdate(0)" style="padding:6px 12px;background:#f59e0b22;border:1px solid #f59e0b;border-radius:8px;color:#f59e0b;font-size:0.8rem;cursor:pointer;font-weight:600">🚚 W drodze</button>
        <span id="selectedCount" style="font-size:0.75rem;color:#64748b;margin-left:4px">(0 zaznaczonych)</span>
    </div>
    <div style="margin-bottom:12px">
        <input type="text" id="paletaSearch" oninput="searchPalety()" placeholder="🔍 Szukaj palety..."
            style="width:100%;padding:10px 14px;background:#12121a;border:1px solid #1e293b;border-radius:10px;color:#e2e8f0;font-size:0.9rem;outline:none">
    </div>'''

    for p in result:
        link = f"/magazyn/paleta-id/{p['id']}"
        dostawca_info = f" • {p['dostawca']}" if p['dostawca'] else ""
        data_info = f" • {p['data_zakupu']}" if p['data_zakupu'] else ""
        
        # Ilość sztuk: z palety (preferowane) lub z produktów
        try:
            sztuki = p['ilosc_sztuk'] if p['ilosc_sztuk'] and p['ilosc_sztuk'] > 0 else p['items']
        except:
            sztuki = p['items']
        
        # Kolory w zależności od stanu
        cnt_color = "#22c55e" if p['cnt'] > 0 else "#ef4444"
        
        # Cena zakupu: cena_zakupu w bazie = BRUTTO z faktury
        zakup_brutto = p['cena_zakupu'] or 0
        
        dostarczona = dostarczona_map.get(p['id'], 0)
        dostarczona_label = '✅ Dostarczona' if dostarczona else '🚚 W drodze'
        dostarczona_color = '#22c55e' if dostarczona else '#f59e0b'
        html += f'''<div class="item" style="position:relative;display:flex;align-items:center">
            <input type="checkbox" class="paleta-cb" data-id="{p['id']}" onchange="updateCount()"
                style="width:18px;height:18px;margin-right:8px;cursor:pointer;accent-color:#3b82f6;flex-shrink:0">
            <a href="{link}" style="display:flex;flex:1;align-items:center;text-decoration:none;color:inherit;min-width:0">
                <div style="font-size:1.5rem;margin-right:10px">📦</div>
                <div class="item-info">
                    <div class="item-name">{p['nazwa']}</div>
                    <div class="item-meta" style="color:{cnt_color}">{p['cnt']} prod. | {sztuki} szt{dostawca_info}{data_info}</div>
                    <div class="item-meta">💰 Zakup: {zakup_brutto:.0f} zł</div>
                </div>
            </a>
            <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px;min-width:100px;flex-shrink:0">
                <div style="color:#22c55e;font-weight:700">{p['wartosc_allegro'] or 0:.0f} zł</div>
                <button onclick="toggleDostarczona({p['id']}, this)" 
                    data-val="{dostarczona}"
                    style="padding:4px 8px;border:1px solid {dostarczona_color};background:{dostarczona_color}22;color:{dostarczona_color};border-radius:6px;font-size:0.7rem;cursor:pointer;white-space:nowrap">
                    {dostarczona_label}
                </button>
            </div>
        </div>'''
    
    # Produkty bez palety
    if bez_palety['cnt'] > 0:
        html += f'''<a href="/magazyn/paleta/brak" class="item" style="border-color:#f59e0b">
            <div style="font-size:1.5rem;margin-right:10px">⚠️</div>
            <div class="item-info">
                <div class="item-name" style="color:#f59e0b">Bez palety</div>
                <div class="item-meta">{bez_palety['cnt']} prod. | {bez_palety['items']} szt</div>
            </div>
            <div class="item-right">
                <div class="item-qty" style="color:#f59e0b">{bez_palety['wartosc_allegro'] or 0:.0f} zł</div>
            </div>
        </a>'''
    
    html += '''<script>
    function updateCount() {
        const n = document.querySelectorAll('.paleta-cb:checked').length;
        document.getElementById('selectedCount').textContent = '(' + n + ' zaznaczonych)';
    }
    function selectAll() {
        document.querySelectorAll('.paleta-cb').forEach(cb => cb.checked = true);
        updateCount();
    }
    function selectNone() {
        document.querySelectorAll('.paleta-cb').forEach(cb => cb.checked = false);
        updateCount();
    }
    function massUpdate(val) {
        const ids = [...document.querySelectorAll('.paleta-cb:checked')].map(cb => parseInt(cb.dataset.id));
        if (!ids.length) { alert('Zaznacz najpierw palety'); return; }
        fetch('/magazyn/api/paleta-dostarczona-bulk', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ids: ids, dostarczona: val})
        }).then(r => r.json()).then(d => {
            if (d.ok) {
                // Zaktualizuj przyciski wizualnie
                ids.forEach(id => {
                    const cb = document.querySelector('.paleta-cb[data-id="' + id + '"]');
                    if (!cb) return;
                    const btn = cb.closest('.item').querySelector('button[data-val]');
                    if (!btn) return;
                    btn.dataset.val = val;
                    if (val == 1) {
                        btn.textContent = '✅ Dostarczona';
                        btn.style.borderColor = '#22c55e';
                        btn.style.color = '#22c55e';
                        btn.style.background = '#22c55e22';
                    } else {
                        btn.textContent = '🚚 W drodze';
                        btn.style.borderColor = '#f59e0b';
                        btn.style.color = '#f59e0b';
                        btn.style.background = '#f59e0b22';
                    }
                });
                selectNone();
            }
        });
    }
    function toggleDostarczona(paletaId, btn) {
        const newVal = btn.dataset.val == '1' ? 0 : 1;
        fetch('/magazyn/api/paleta-dostarczona/' + paletaId, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({dostarczona: newVal})
        }).then(r => r.json()).then(d => {
            if (d.ok) {
                btn.dataset.val = newVal;
                if (newVal == 1) {
                    btn.textContent = '✅ Dostarczona';
                    btn.style.borderColor = '#22c55e';
                    btn.style.color = '#22c55e';
                    btn.style.background = '#22c55e22';
                } else {
                    btn.textContent = '🚚 W drodze';
                    btn.style.borderColor = '#f59e0b';
                    btn.style.color = '#f59e0b';
                    btn.style.background = '#f59e0b22';
                }
            }
        });
    }
    function searchPalety() {
        const q = document.getElementById('paletaSearch').value.toLowerCase();
        document.querySelectorAll('.item').forEach(el => {
            const name = (el.querySelector('.item-name') || {}).textContent || '';
            const meta = (el.querySelector('.item-meta') || {}).textContent || '';
            el.style.display = (!q || name.toLowerCase().includes(q) || meta.toLowerCase().includes(q)) ? '' : 'none';
        });
    }
    </script>'''
    html += '<a href="/magazyn" class="back">← Powrót</a>'
    return render(html)

@magazynier_bp.route('/api/paleta-dostarczona-bulk', methods=['POST'])
def api_paleta_dostarczona_bulk():
    from flask import jsonify, request as req
    conn = get_db()
    try:
        data = req.get_json()
        ids = data.get('ids', [])
        val = int(data.get('dostarczona', 0))
        for pid in ids:
            conn.execute('UPDATE palety SET dostarczona = ? WHERE id = ?', (val, int(pid)))
        conn.commit()
        return jsonify({'ok': True, 'updated': len(ids)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@magazynier_bp.route('/api/paleta-dostarczona/<int:paleta_id>', methods=['POST'])
def api_paleta_dostarczona(paleta_id):
    from flask import jsonify, request as req
    import json
    conn = get_db()
    try:
        data = req.get_json()
        val = int(data.get('dostarczona', 0))
        conn.execute('UPDATE palety SET dostarczona = ? WHERE id = ?', (val, paleta_id))
        conn.commit()
        return jsonify({'ok': True, 'dostarczona': val})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@magazynier_bp.route('/paleta-id/<int:paleta_id>')
def paleta_detail_by_id(paleta_id):
    """Szczegóły palety po ID (bezpieczniejsze niż po nazwie)"""
    from urllib.parse import quote
    conn = get_db()
    
    # Pobierz dane palety z ceną zakupu
    try:
        paleta_row = conn.execute('SELECT nazwa, ilosc_sztuk, cena_zakupu, cena_zakupu_netto, COALESCE(dostarczona, 0) as dostarczona FROM palety WHERE id = ?', (paleta_id,)).fetchone()
        ilosc_sztuk_paleta = paleta_row['ilosc_sztuk'] or 0 if paleta_row else 0
        cena_zakupu = paleta_row['cena_zakupu'] or 0 if paleta_row else 0
        try:
            cena_zakupu_netto = paleta_row['cena_zakupu_netto'] or 0 if paleta_row else 0
        except:
            cena_zakupu_netto = 0
        # cena_zakupu w bazie = BRUTTO z faktury
        # netto = brutto / 1.23
        if cena_zakupu > 0 and cena_zakupu_netto == 0:
            cena_zakupu_netto = round(cena_zakupu / 1.23, 2)
    except:
        try:
            paleta_row = conn.execute('SELECT nazwa, ilosc_sztuk, cena_zakupu FROM palety WHERE id = ?', (paleta_id,)).fetchone()
            ilosc_sztuk_paleta = paleta_row['ilosc_sztuk'] or 0 if paleta_row else 0
            cena_zakupu = paleta_row['cena_zakupu'] or 0 if paleta_row else 0
            cena_zakupu_netto = round(cena_zakupu / 1.23, 2)
        except:
            paleta_row = conn.execute('SELECT nazwa FROM palety WHERE id = ?', (paleta_id,)).fetchone()
            ilosc_sztuk_paleta = 0
            cena_zakupu = 0
            cena_zakupu_netto = 0
    
    if not paleta_row:
        return redirect('/magazyn/palety')
    
    nazwa_palety = paleta_row['nazwa']
    
    # Pobierz produkty
    products = conn.execute('SELECT * FROM produkty WHERE paleta_id = ?', (paleta_id,)).fetchall()
    
    # Statystyki palety
    stats = conn.execute('''
        SELECT COUNT(*) as cnt, SUM(ilosc) as items, 
               SUM(CASE WHEN status IN ('wystawiony', 'szkic') THEN cena_allegro*ilosc ELSE 0 END) as allegro,
               SUM(cena_allegro * ilosc) as allegro_all,
               SUM(cena_brutto) as suma_produktow_brutto
        FROM produkty WHERE paleta_id = ?
    ''', (paleta_id,)).fetchone()
    
    # Cena zakupu palety
    # Fallback: jeśli paleta nie ma wpisanej ceny, użyj sumy produktów
    suma_prod_brutto = stats['suma_produktow_brutto'] or 0
    
    if cena_zakupu > 0:
        # Upewnij się że cena_zakupu to BRUTTO (większa liczba)
        # Jeśli cena_zakupu < suma_prod / 1.5 to prawdopodobnie jest netto
        if suma_prod_brutto > 0 and cena_zakupu < suma_prod_brutto / 1.5:
            # cena_zakupu wydaje się być netto
            netto = cena_zakupu
            brutto = round(cena_zakupu * 1.23, 2)
        else:
            brutto = cena_zakupu
            netto = round(cena_zakupu / 1.23, 2)
    elif suma_prod_brutto > 0:
        brutto = suma_prod_brutto
        netto = round(brutto / 1.23, 2)
    else:
        brutto = 0
        netto = 0
    
    # Ostateczne zabezpieczenie - netto zawsze < brutto
    if netto > brutto and brutto > 0:
        netto, brutto = brutto, netto
    
    allegro = stats['allegro_all'] or 0  # Potencjalny przychód ze WSZYSTKICH produktów
    zysk = allegro - brutto - (allegro * 0.11)
    
    # Użyj ilosc_sztuk z palety jeśli jest, w przeciwnym razie z produktów
    sztuki_display = ilosc_sztuk_paleta if ilosc_sztuk_paleta > 0 else (stats['items'] or 0)
    
    dostarczona_val = paleta_row['dostarczona'] if paleta_row and 'dostarczona' in paleta_row.keys() else 0
    dostarczona_label = '✅ Dostarczona' if dostarczona_val else '🚚 W drodze'
    dostarczona_color = '#22c55e' if dostarczona_val else '#f59e0b'
    html = f'''<div class="hdr" style="display:flex;justify-content:space-between;align-items:center">
        <div><h1>📦 {nazwa_palety}</h1><small>{len(products)} prod. ({sztuki_display} szt.)</small></div>
        <button id="btnDostarczona" onclick="toggleDostarczona({paleta_id}, this)"
            data-val="{dostarczona_val}"
            style="padding:8px 16px;border:2px solid {dostarczona_color};background:{dostarczona_color}22;color:{dostarczona_color};border-radius:10px;font-size:0.9rem;font-weight:600;cursor:pointer">
            {dostarczona_label}
        </button>
    </div>
    <script>
    function toggleDostarczona(id, btn) {{
        const newVal = btn.dataset.val == '1' ? 0 : 1;
        fetch('/magazyn/api/paleta-dostarczona/' + id, {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{dostarczona: newVal}})
        }}).then(r => r.json()).then(d => {{
            if (d.ok) {{
                btn.dataset.val = newVal;
                if (newVal == 1) {{
                    btn.textContent = '✅ Dostarczona';
                    btn.style.borderColor = '#22c55e';
                    btn.style.color = '#22c55e';
                    btn.style.background = '#22c55e22';
                }} else {{
                    btn.textContent = '🚚 W drodze';
                    btn.style.borderColor = '#f59e0b';
                    btn.style.color = '#f59e0b';
                    btn.style.background = '#f59e0b22';
                }}
            }}
        }});
    }}
    </script>
    
    <div class="stats" style="margin-bottom:15px">
        <div class="stat">
            <div class="stat-v">{stats['cnt'] or 0}</div>
            <div class="stat-l">PRODUKTÓW</div>
        </div>
        <div class="stat">
            <div class="stat-v">{netto:.0f} zł</div>
            <div class="stat-l">ZAKUP NETTO</div>
        </div>
        <div class="stat" style="border:2px solid #3b82f6;border-radius:12px">
            <div class="stat-v">{brutto:.0f} zł</div>
            <div class="stat-l">ZAKUP BRUTTO</div>
        </div>
        <div class="stat">
            <div class="stat-v green">{allegro:.0f} zł</div>
            <div class="stat-l">ALLEGRO (suma)</div>
        </div>
        <div class="stat">
            <div class="stat-v" style="color:{('#22c55e' if zysk > 0 else '#ef4444')}">{zysk:.0f} zł</div>
            <div class="stat-l">ZYSK</div>
        </div>
    </div>
    '''
    
    # Przyciski akcji na palecie
    html += '<div style="display:flex;gap:10px;margin-bottom:15px;flex-wrap:wrap">'
    html += f'<a href="/palety/{paleta_id}/mass-edit" class="btn" style="background:var(--purple);flex:1">🛒 Wystaw bezpośrednio</a>'
    html += f'<a href="/magazyn/paleta-id/{paleta_id}/to-paletomat" class="btn btn-ok" style="flex:1">🔄 PALETOMAT (scrapuj)</a>'
    html += f'<button onclick="autoWycenaPaleta({paleta_id})" class="btn" style="background:#f59e0b;flex:1">💰 Auto-wycena</button>'
    html += '</div>'
    
    # Script dla Auto-wyceny (streamowane)
    html += '''
    <script>
    async function autoWycenaPaleta(paletaId) {
        const btn = event.target;
        btn.disabled = true;
        btn.innerHTML = '⏳ Pobieranie cen... 0%';

        // Stwórz progress div
        let progressDiv = document.getElementById('autowycena-progress');
        if (!progressDiv) {
            progressDiv = document.createElement('div');
            progressDiv.id = 'autowycena-progress';
            progressDiv.style.cssText = 'background:#1a1a2e;border:1px solid #f59e0b;border-radius:8px;padding:12px;margin:10px 0;font-size:13px;max-height:300px;overflow-y:auto;display:none';
            btn.parentNode.after(progressDiv);
        }
        progressDiv.style.display = 'block';
        progressDiv.innerHTML = '<b>🔄 Auto-wycena startuje...</b><br>';

        try {
            const resp = await fetch('/magazyn/api/autowycena-stream/paleta/' + paletaId, {method: 'POST'});
            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let stats = {updated:0, total:0, from_amazon:0, from_estimate:0, titles_optimized:0, errors:0};

            while (true) {
                const {done, value} = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, {stream: true});
                const lines = buffer.split('\\n');
                buffer = lines.pop();

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    try {
                        const ev = JSON.parse(line.slice(6));
                        if (ev.type === 'progress') {
                            const pct = Math.round(ev.current / ev.total * 100);
                            btn.innerHTML = '⏳ ' + pct + '% (' + ev.current + '/' + ev.total + ')';
                            let color = ev.source === 'amazon' ? '#10b981' : ev.source === 'estimate' ? '#f59e0b' : '#ef4444';
                            progressDiv.innerHTML += '<span style="color:' + color + '">• ' + ev.name + ' → ' + (ev.price ? ev.price + ' zł' : 'brak ceny') + ' [' + (ev.source||'—') + ']</span><br>';
                            progressDiv.scrollTop = progressDiv.scrollHeight;
                        } else if (ev.type === 'done') {
                            stats = ev;
                        } else if (ev.type === 'error') {
                            progressDiv.innerHTML += '<span style="color:#ef4444">❌ ' + ev.message + '</span><br>';
                        }
                    } catch(e) {}
                }
            }

            progressDiv.innerHTML += '<br><b style="color:#10b981">✅ Gotowe! Amazon: ' + stats.from_amazon + ', Szacowane: ' + stats.from_estimate + ', Tytuły: ' + stats.titles_optimized + ', Błędy: ' + stats.errors + '</b>';

            if (stats.updated > 0) {
                setTimeout(() => location.reload(), 2000);
            }
        } catch (e) {
            progressDiv.innerHTML += '<br><b style="color:#ef4444">❌ Błąd: ' + e.message + '</b>';
        }

        btn.disabled = false;
        btn.innerHTML = '💰 Auto-wycena';
    }
    </script>
    '''
    
    html += '<div class="section">PRODUKTY</div>'
    
    for p in products:
        img = p['zdjecie_url'] or 'https://via.placeholder.com/45'
        pcode = get_product_code(p)
        display_code = p['ean'] or p['asin'] or f"#{p['id']}"
        html += f'''<a href="/magazyn/produkt/{pcode}" class="item">
            <img src="{img}" onerror="this.src='https://via.placeholder.com/45'">
            <div class="item-info">
                <div class="item-name">{p['nazwa'][:35]}...</div>
                <div class="item-meta">{display_code} | {p['ilosc']} szt</div>
            </div>
            <div class="item-right">
                <div class="item-qty">{p['cena_allegro'] or 0:.0f} zł</div>
            </div>
        </a>'''
    
    if not products:
        html += '<div style="text-align:center;padding:30px;color:#64748b">Brak produktów</div>'
    
    html += '<a href="/magazyn/palety" class="back">← Powrót</a>'
    return render(html)


@magazynier_bp.route('/paleta-id/<int:paleta_id>/to-paletomat')
def paleta_to_paletomat_by_id(paleta_id):
    """Przenosi produkty z palety do Paletomat (scrapuje)"""
    conn = get_db()
    
    # Pobierz produkty z palety które mają ASIN
    products = conn.execute('''
        SELECT id, asin, ean, nazwa, cena_brutto, zdjecie_url 
        FROM produkty 
        WHERE paleta_id = ? AND asin IS NOT NULL AND asin != ''
    ''', (paleta_id,)).fetchall()
    
    added = 0
    for p in products:
        asin = p['asin']
        if not asin or asin in ('N/A', 'None'):
            continue
        
        # Sprawdź czy już istnieje w scraped
        existing = conn.execute('SELECT asin FROM scraped WHERE asin = ?', (asin,)).fetchone()
        if not existing:
            try:
                conn.execute('''
                    INSERT INTO scraped (asin, nazwa, zdjecie_url, cena_amazon, status, data_scrape)
                    VALUES (?, ?, ?, ?, 'nowy', datetime('now'))
                ''', (asin, p['nazwa'], p['zdjecie_url'] or '', p['cena_brutto'] or 0))
                added += 1
            except:
                pass
    
    conn.commit()
    
    return redirect(f'/paletomat/scraper?added={added}')


@magazynier_bp.route('/paleta/<path:n>')
def paleta_detail(n):
    from urllib.parse import unquote, quote
    n = unquote(n)
    conn = get_db()
    if n == 'brak':
        products = conn.execute('SELECT * FROM produkty WHERE paleta="" OR paleta IS NULL').fetchall()
        nazwa_palety = 'Bez palety'
        paleta_id = None
        cena_zakupu = 0
        cena_zakupu_netto = 0
    else:
        products = conn.execute('SELECT * FROM produkty WHERE paleta=?', (n,)).fetchall()
        nazwa_palety = n
        # Znajdź paleta_id z pierwszego produktu (jeśli istnieje)
        paleta_id = products[0]['paleta_id'] if products and products[0]['paleta_id'] else None
        # Pobierz cenę zakupu z tabeli palety
        cena_zakupu = 0
        cena_zakupu_netto = 0
        if paleta_id:
            try:
                pr = conn.execute('SELECT cena_zakupu, cena_zakupu_netto FROM palety WHERE id = ?', (paleta_id,)).fetchone()
                cena_zakupu = (pr['cena_zakupu'] or 0) if pr else 0
                cena_zakupu_netto = (pr['cena_zakupu_netto'] or 0) if pr else 0
                # cena_zakupu w bazie = BRUTTO
                if cena_zakupu > 0 and cena_zakupu_netto == 0:
                    cena_zakupu_netto = round(cena_zakupu / 1.23, 2)
            except:
                try:
                    pr = conn.execute('SELECT cena_zakupu FROM palety WHERE id = ?', (paleta_id,)).fetchone()
                    cena_zakupu = (pr['cena_zakupu'] or 0) if pr else 0
                    cena_zakupu_netto = round(cena_zakupu / 1.23, 2)
                except:
                    pass
    
    # Statystyki palety
    stats = conn.execute('''
        SELECT COUNT(*) as cnt, SUM(ilosc) as items, 
               SUM(cena_allegro * ilosc) as allegro_all,
               SUM(cena_brutto) as suma_produktow_brutto
        FROM produkty WHERE paleta=?
    ''', (n if n != 'brak' else '',)).fetchone()
    
    # cena_zakupu w bazie = BRUTTO
    brutto = cena_zakupu
    netto = cena_zakupu_netto if cena_zakupu_netto > 0 else round(cena_zakupu / 1.23, 2)
    
    # Fallback: jeśli brak ceny zakupu w palecie, użyj sumy produktów
    if brutto == 0 and (stats['suma_produktow_brutto'] or 0) > 0:
        brutto = stats['suma_produktow_brutto']
        netto = round(brutto / 1.23, 2)
    
    allegro = stats['allegro_all'] or 0
    zysk = allegro - brutto - (allegro * 0.11)
    
    paleta_encoded = quote(nazwa_palety, safe='')
    
    html = f'''<div class="hdr"><h1>📦 {nazwa_palety}</h1><small>{len(products)} produktów</small></div>
    
    <div class="stats" style="margin-bottom:15px">
        <div class="stat">
            <div class="stat-v">{stats['cnt'] or 0}</div>
            <div class="stat-l">Produktów</div>
        </div>
        <div class="stat">
            <div class="stat-v">{netto:.0f} zł</div>
            <div class="stat-l">Zakup netto</div>
        </div>
        <div class="stat">
            <div class="stat-v">{brutto:.0f} zł</div>
            <div class="stat-l">Zakup brutto</div>
        </div>
        <div class="stat">
            <div class="stat-v green">{allegro:.0f} zł</div>
            <div class="stat-l">Allegro (suma)</div>
        </div>
        <div class="stat">
            <div class="stat-l">Zysk</div>
        </div>
    </div>
    '''
    
    # Przyciski akcji na palecie
    if paleta_id or (n and n != 'brak'):
        html += '<div style="display:flex;gap:10px;margin-bottom:15px;flex-wrap:wrap">'
        
        # Masowe wystawianie (stary system - bezpośrednio)
        if paleta_id:
            html += f'<a href="/palety/{paleta_id}/mass-edit" class="btn" style="background:var(--purple);flex:1">🛒 Wystaw bezpośrednio</a>'
        
        # NOWE: Przenieś do Paletomat (ze scrapowaniem!)
        html += f'<a href="/magazyn/paleta/{paleta_encoded}/to-paletomat" class="btn btn-ok" style="flex:1">🔄 PALETOMAT (scrapuj)</a>'
        
        html += '</div>'
    
    for p in products:
        img = p['zdjecie_url'] or 'https://via.placeholder.com/45'
        pcode = get_product_code(p)
        display_code = p['ean'] or p['asin'] or f"#{p['id']}"
        
        # Cena zakupu produktu ZA SZTUKĘ (cena_netto/cena_brutto w bazie JUŻ są jednostkowe!)
        cena_za_sztuke = p['cena_netto'] if p['cena_netto'] and p['cena_netto'] > 0 else (p['cena_brutto'] or 0)
        
        html += f'''<a href="/magazyn/produkt/{pcode}" class="item">
            <img src="{img}" onerror="this.src='https://via.placeholder.com/45'">
            <div class="item-info">
                <div class="item-name">{p['nazwa'][:30]}...</div>
                <div class="item-meta">{display_code} • Zakup: {cena_za_sztuke:.2f} zł/szt</div>
            </div>
            <div class="item-qty">{p['ilosc']}</div>
        </a>'''
    
    # Przycisk usuwania palety (tylko jeśli nie jest to "Bez palety")
    if n != 'brak' and nazwa_palety:
        html += f'''
        <div style="margin-top:20px;padding:15px;background:#12121a;border-radius:12px">
            <form action="/magazyn/paleta/{paleta_encoded}/usun" method="POST" onsubmit="return confirm('Na pewno usunąć paletę {nazwa_palety} i wszystkie jej produkty?')">
                <button type="submit" class="btn btn-err" style="width:100%">🗑️ USUŃ PALETĘ + PRODUKTY</button>
            </form>
            <form action="/magazyn/paleta/{paleta_encoded}/wyczysc" method="POST" onsubmit="return confirm('Usunąć tylko przypisanie do palety (produkty zostaną)?')" style="margin-top:10px">
                <button type="submit" class="btn btn-warn" style="width:100%">📤 WYCZYŚĆ PRZYPISANIE</button>
            </form>
        </div>
        '''
    
    html += '<a href="/magazyn/palety" class="back">← Powrót</a>'
    return render(html)


@magazynier_bp.route('/paleta/<path:n>/to-paletomat')
def paleta_to_paletomat(n):
    """Przenosi wszystkie produkty z palety do Paletomat (tabela scraped) ze scrapowaniem"""
    from urllib.parse import unquote
    n = unquote(n)
    
    conn = get_db()
    
    if n == 'brak':
        products = conn.execute('SELECT * FROM produkty WHERE paleta="" OR paleta IS NULL').fetchall()
    else:
        products = conn.execute('SELECT * FROM produkty WHERE paleta=?', (n,)).fetchall()
    
    added_count = 0
    updated_count = 0
    skipped_count = 0
    
    for p in products:
        p = dict(p)
        
        # Ustal identyfikator (ASIN > EAN > MAG{id})
        asin = p.get('asin', '') or ''
        ean = p.get('ean', '') or ''
        
        if asin and asin not in ('N/A', 'None', ''):
            identyfikator = asin
        elif ean and ean not in ('N/A', 'None', ''):
            identyfikator = ean
        else:
            identyfikator = f"MAG{p['id']}"
        
        # Sprawdź czy już istnieje w scraped
        existing = conn.execute('SELECT asin FROM scraped WHERE asin=?', (identyfikator,)).fetchone()
        
        if not existing:
            # Dodaj do tabeli scraped
            conn.execute('''
                INSERT INTO scraped (asin, nazwa, zdjecie_url, cena_amazon, status, data_scrape, ean)
                VALUES (?, ?, ?, ?, 'nowy', datetime('now'), ?)
            ''', (
                identyfikator,
                p.get('nazwa', f'Produkt {identyfikator}'),
                p.get('zdjecie_url', ''),
                p.get('cena_brutto', 0) or 0,
                ean
            ))
            added_count += 1
        else:
            # Aktualizuj istniejący
            conn.execute('''
                UPDATE scraped SET nazwa=?, zdjecie_url=?, status='nowy'
                WHERE asin=?
            ''', (
                p.get('nazwa', f'Produkt {identyfikator}'),
                p.get('zdjecie_url', ''),
                identyfikator
            ))
            updated_count += 1
    
    conn.commit()
    
    # Pokaż komunikat i przekieruj
    from .magazynier import render
    html = f'''
    <div class="hdr"><h1>✅ PRZENIESIONO DO PALETOMAT</h1></div>
    
    <div class="alert alert-ok">
        📦 Paleta: {n}<br>
        ✅ Dodano: {added_count} produktów<br>
        🔄 Zaktualizowano: {updated_count} produktów<br>
        ⏭️ Pominięto: {skipped_count} produktów
    </div>
    
    <div class="card" style="padding:15px;margin-top:15px">
        <div style="font-weight:600;margin-bottom:12px">🎯 CO DALEJ?</div>
        <div style="color:#64748b;font-size:0.85rem;margin-bottom:15px">
            1. Przejdź do Paletomat → Generator<br>
            2. Zobaczysz swoje produkty<br>
            3. Kliknij na produkt → Zescrapuje z Amazona (zdjęcia, opisy)<br>
            4. Wystaw masowo z AI opisami!
        </div>
        <a href="/paletomat/generator" class="btn btn-ok" style="width:100%">
            🚀 OTWÓRZ PALETOMAT GENERATOR
        </a>
    </div>
    
    <a href="/magazyn/paleta/{n}" class="btn btn-2" style="margin-top:15px">← Powrót do palety</a>
    <a href="/magazyn" class="back">← Magazyn</a>
    '''
    return render(html)


@magazynier_bp.route('/paleta/<path:n>/usun', methods=['POST'])
def paleta_usun(n):
    """Usuwa paletę wraz z produktami (także ze scraped/Paletomat)"""
    from urllib.parse import unquote
    n = unquote(n)
    
    conn = get_db()
    if n and n != 'brak':
        # 🔥 NOWE: Pobierz ASINy produktów z tej palety
        asiny = conn.execute('SELECT DISTINCT asin FROM produkty WHERE paleta = ? AND asin != ""', (n,)).fetchall()
        asiny_list = [row[0] for row in asiny if row[0]]
        
        # 🔥 NOWE: Usuń te produkty ze scraped (Paletomat)
        if asiny_list:
            placeholders = ','.join(['?' for _ in asiny_list])
            conn.execute('DELETE FROM scraped WHERE asin IN (' + placeholders + ')', asiny_list)  # noqa: B608 - placeholders are only ?
            print(f"🗑️ Usunięto {len(asiny_list)} produktów ze scraped (Paletomat)")
        
        # Usuń wszystkie produkty z tej palety (Magazynier)
        conn.execute('DELETE FROM produkty WHERE paleta = ?', (n,))
        
        conn.commit()
        print(f"✅ Usunięto paletę: {n}")
    
    return redirect('/magazyn/palety')


@magazynier_bp.route('/paleta/<path:n>/wyczysc', methods=['POST'])
def paleta_wyczysc(n):
    """Usuwa przypisanie do palety (produkty zostają)"""
    from urllib.parse import unquote
    n = unquote(n)
    
    conn = get_db()
    if n and n != 'brak':
        # Wyczyść przypisanie palety
        conn.execute('UPDATE produkty SET paleta = "", paleta_id = NULL WHERE paleta = ?', (n,))
        conn.commit()
    
    return redirect('/magazyn/palety')


# ============================================================
# POBIERANIE ZDJĘĆ Z AMAZON
# ============================================================

@magazynier_bp.route('/fetch-images')
def fetch_images_page():
    """Strona do pobierania zdjęć z Amazon dla produktów z ASIN"""
    conn = get_db()
    
    # Policz produkty bez zdjęć z ASIN
    stats = conn.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN asin != '' AND asin IS NOT NULL THEN 1 ELSE 0 END) as with_asin,
            SUM(CASE WHEN (zdjecie_url IS NULL OR zdjecie_url = '') AND asin != '' AND asin IS NOT NULL THEN 1 ELSE 0 END) as no_image_with_asin
        FROM produkty
    ''').fetchone()
    
    total = stats['total'] or 0
    with_asin = stats['with_asin'] or 0
    no_image = stats['no_image_with_asin'] or 0
    
    html = f'''
    <div class="hdr">
        <h1>📷 POBIERZ ZDJĘCIA</h1>
        <small>Automatyczne pobieranie z Amazon</small>
    </div>
    
    <div class="card" style="padding:15px;margin-bottom:15px">
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;text-align:center">
            <div>
                <div style="font-size:1.8rem;font-weight:700;color:#3b82f6">{total}</div>
                <div style="font-size:0.75rem;color:#64748b">Produktów</div>
            </div>
            <div>
                <div style="font-size:1.8rem;font-weight:700;color:#22c55e">{with_asin}</div>
                <div style="font-size:0.75rem;color:#64748b">Z ASIN</div>
            </div>
            <div>
                <div style="font-size:1.8rem;font-weight:700;color:#f59e0b">{no_image}</div>
                <div style="font-size:0.75rem;color:#64748b">Bez zdjęcia</div>
            </div>
        </div>
    </div>
    
    <div class="card" style="padding:15px;margin-bottom:15px">
        <div style="font-weight:600;margin-bottom:10px">⚠️ Uwaga</div>
        <div style="font-size:0.85rem;color:#94a3b8">
            Pobieranie zdjęć wymaga scrapowania Amazona.<br>
            • Każdy produkt = ~3-5 sekund<br>
            • {no_image} produktów = ~{no_image * 4 // 60} minut<br>
            • Może być blokowane przez Amazon (captcha)
        </div>
    </div>
    
    <div id="progress" style="display:none;margin-bottom:15px">
        <div class="card" style="padding:15px">
            <div style="font-weight:600;margin-bottom:10px">⏳ Pobieranie w toku...</div>
            <div id="progress-text" style="font-size:0.85rem;color:#94a3b8">0 / {no_image}</div>
            <div style="background:#1e1e2e;border-radius:6px;height:10px;margin-top:10px;overflow:hidden">
                <div id="progress-bar" style="background:#22c55e;width:0%;height:100%;transition:width 0.3s"></div>
            </div>
            <div id="progress-log" style="margin-top:10px;font-size:0.75rem;color:#64748b;max-height:150px;overflow-y:auto"></div>
        </div>
    </div>
    
    <button onclick="startFetch()" id="start-btn" class="btn btn-ok" style="width:100%;padding:14px;font-size:1rem">
        📷 POBIERZ ZDJĘCIA ({no_image} produktów)
    </button>
    
    <a href="/magazyn" class="back">← Powrót</a>
    
    <script>
    let running = false;
    
    async function startFetch() {{
        if (running) return;
        running = true;
        
        document.getElementById('start-btn').style.display = 'none';
        document.getElementById('progress').style.display = 'block';
        
        const response = await fetch('/magazyn/api/fetch-images-start');
        const data = await response.json();
        
        if (data.success) {{
            pollProgress();
        }} else {{
            alert('Błąd: ' + data.error);
            running = false;
        }}
    }}
    
    async function pollProgress() {{
        while (running) {{
            const response = await fetch('/magazyn/api/fetch-images-status');
            const data = await response.json();
            
            document.getElementById('progress-text').textContent = data.done + ' / ' + data.total;
            document.getElementById('progress-bar').style.width = (data.done / data.total * 100) + '%';
            
            if (data.log) {{
                document.getElementById('progress-log').innerHTML = data.log.slice(-10).map(l => '<div>' + l + '</div>').join('');
                document.getElementById('progress-log').scrollTop = 9999;
            }}
            
            if (!data.running) {{
                running = false;
                alert('✅ Zakończono! Pobrano ' + data.done + ' zdjęć.');
                location.reload();
                break;
            }}
            
            await new Promise(r => setTimeout(r, 1000));
        }}
    }}
    </script>
    '''
    return render(html)


# Globalny stan pobierania zdjęć
_fetch_images_state = {
    'running': False,
    'total': 0,
    'done': 0,
    'log': []
}

@magazynier_bp.route('/api/fetch-images-start')
def api_fetch_images_start():
    """Rozpocznij pobieranie zdjęć w tle"""
    global _fetch_images_state
    
    if _fetch_images_state['running']:
        return jsonify({'success': False, 'error': 'Już działa'})
    
    import threading
    
    def fetch_worker():
        global _fetch_images_state
        from .utils import scrape_amazon_product
        
        _fetch_images_state['running'] = True
        _fetch_images_state['done'] = 0
        _fetch_images_state['log'] = []
        
        try:
            conn = get_db()
            products = conn.execute('''
                SELECT id, asin FROM produkty 
                WHERE asin != '' AND asin IS NOT NULL 
                AND (zdjecie_url IS NULL OR zdjecie_url = '')
                LIMIT 100
            ''').fetchall()
            
            _fetch_images_state['total'] = len(products)
            
            for p in products:
                if not _fetch_images_state['running']:
                    break
                
                asin = p['asin']
                try:
                    result = scrape_amazon_product(asin)
                    if result and result.get('image_url'):
                        conn = get_db()
                        conn.execute('UPDATE produkty SET zdjecie_url = ? WHERE id = ?', 
                            (result['image_url'], p['id']))
                        conn.commit()
                        _fetch_images_state['log'].append(f'✅ {asin}: OK')
                    else:
                        _fetch_images_state['log'].append(f'⚠️ {asin}: brak zdjęcia')
                except Exception as e:
                    _fetch_images_state['log'].append(f'❌ {asin}: {str(e)[:30]}')
                
                _fetch_images_state['done'] += 1
                
        except Exception as e:
            _fetch_images_state['log'].append(f'❌ Błąd: {str(e)}')
        finally:
            _fetch_images_state['running'] = False
    
    thread = threading.Thread(target=fetch_worker, daemon=True)
    thread.start()
    
    return jsonify({'success': True})


@magazynier_bp.route('/api/fetch-images-status')
def api_fetch_images_status():
    """Status pobierania zdjęć"""
    return jsonify(_fetch_images_state)


@magazynier_bp.route('/api/fetch-images-stop')
def api_fetch_images_stop():
    """Zatrzymaj pobieranie"""
    global _fetch_images_state
    _fetch_images_state['running'] = False
    return jsonify({'success': True})


@magazynier_bp.route('/dostawcy')
def dostawcy():
    conn = get_db()
    result = conn.execute('''SELECT dostawca, COUNT(*) as cnt, SUM(ilosc) as items 
        FROM produkty GROUP BY dostawca ORDER BY dostawca''').fetchall()
    
    html = '<div class="hdr"><h1>🚚 DOSTAWCY</h1></div>'
    
    for d in result:
        html += f'''<a href="/magazyn/dostawca/{d['dostawca'] or 'brak'}" class="item">
            <div style="font-size:1.5rem;margin-right:10px">🚚</div>
            <div class="item-info">
                <div class="item-name">{d['dostawca'] or 'Nieznany'}</div>
                <div class="item-meta">{d['cnt']} produktów</div>
            </div>
            <div class="item-qty">{d['items'] or 0}</div>
        </a>'''
    
    html += '<a href="/magazyn" class="back">← Powrót</a>'
    return render(html)

@magazynier_bp.route('/dostawca/<n>')
def dostawca_detail(n):
    conn = get_db()
    if n == 'brak':
        products = conn.execute('SELECT * FROM produkty WHERE dostawca="" OR dostawca IS NULL').fetchall()
        nazwa_dostawcy = 'Nieznany dostawca'
    else:
        products = conn.execute('SELECT * FROM produkty WHERE dostawca=?', (n,)).fetchall()
        nazwa_dostawcy = n
    
    html = f'''<div class="hdr"><h1>🚚 {nazwa_dostawcy}</h1><small>{len(products)} produktów</small></div>'''
    
    for p in products:
        img = p['zdjecie_url'] or 'https://via.placeholder.com/45'
        pcode = get_product_code(p)
        display_code = p['ean'] or p['asin'] or f"#{p['id']}"
        html += f'''<a href="/magazyn/produkt/{pcode}" class="item">
            <img src="{img}" onerror="this.src='https://via.placeholder.com/45'">
            <div class="item-info">
                <div class="item-name">{p['nazwa'][:30]}...</div>
                <div class="item-meta">{display_code} | 📦 {p['paleta'] or '—'}</div>
            </div>
            <div class="item-qty">{p['ilosc']}</div>
        </a>'''
    
    html += '<a href="/magazyn/dostawcy" class="back">← Powrót</a>'
    return render(html)

@magazynier_bp.route('/export')
def export_csv():
    """Export magazynu do Excel (.xlsx)"""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    import io
    
    conn = get_db()
    products = conn.execute('SELECT * FROM produkty ORDER BY paleta, lokalizacja').fetchall()
    
    # Utwórz workbook
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Magazyn"
    
    # Style
    header_fill = PatternFill(start_color="8B5CF6", end_color="8B5CF6", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=11)
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    
    # Nagłówki
    headers = ['EAN', 'ASIN', 'Nazwa', 'Ilość', 'Cena brutto', 'Cena Allegro', 
               'Lokalizacja', 'Paleta', 'Dostawca', 'Stan', 'Status', 'Kategoria', 'Data dodania']
    
    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = border
    
    # Dane
    for row_num, p in enumerate(products, 2):
        data = [
            p['ean'] or '',
            p['asin'] or '',
            p['nazwa'] or '',
            p['ilosc'] or 0,
            p['cena_brutto'] or 0,
            p['cena_allegro'] or 0,
            p['lokalizacja'] or '',
            p['paleta'] or '',
            p['dostawca'] or '',
            p['stan'] or '',
            p['status'] if p['status'] else 'nowy',
            p['kategoria'] or '',
            p['data_dodania'] or ''
        ]
        
        for col_num, value in enumerate(data, 1):
            cell = ws.cell(row=row_num, column=col_num, value=value)
            cell.border = border
            
            # Wyrównanie
            if col_num in [4, 5, 6]:  # Liczby
                cell.alignment = Alignment(horizontal='right')
            elif col_num in [1, 2, 7, 8]:  # Kody i lokalizacje
                cell.alignment = Alignment(horizontal='center')
    
    # Autofit kolumn
    for col_num in range(1, len(headers) + 1):
        column_letter = get_column_letter(col_num)
        max_length = 0
        
        for cell in ws[column_letter]:
            if cell.value:
                max_length = max(max_length, len(str(cell.value)))
        
        adjusted_width = min(max_length + 2, 50)
        ws.column_dimensions[column_letter].width = adjusted_width
    
    # Zamroź pierwszy wiersz
    ws.freeze_panes = 'A2'
    
    # Dodaj arkusz ze statystykami
    stats_ws = wb.create_sheet("Statystyki")
    stats = get_stats()
    
    stats_data = [
        ['Statystyka', 'Wartość'],
        ['Produktów', stats['produkty']],
        ['Sztuk', stats['sztuki']],
        ['Wartość zakupu (brutto)', f"{stats['wartosc_zakupu']:.2f} zł"],
        ['Wartość zakupu (netto)', f"{stats['wartosc_netto']:.2f} zł"],
        ['Wartość Allegro', f"{stats['wartosc_allegro']:.2f} zł"],
        ['Palet', stats['palety']],
        ['Dostawców', stats['dostawcy']],
        ['Data exportu', datetime.now().strftime('%Y-%m-%d %H:%M:%S')]
    ]
    
    for row_num, (label, value) in enumerate(stats_data, 1):
        cell_a = stats_ws.cell(row=row_num, column=1, value=label)
        cell_b = stats_ws.cell(row=row_num, column=2, value=value)
        
        if row_num == 1:
            cell_a.fill = header_fill
            cell_a.font = header_font
            cell_b.fill = header_fill
            cell_b.font = header_font
        else:
            cell_a.font = Font(bold=True)
        
        cell_a.border = border
        cell_b.border = border
    
    stats_ws.column_dimensions['A'].width = 30
    stats_ws.column_dimensions['B'].width = 20
    
    # Zapisz do BytesIO
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    
    filename = f'magazyn_{datetime.now():%Y%m%d_%H%M}.xlsx'
    
    return Response(
        output.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )

@magazynier_bp.route('/import')
def import_page():
    # Pobierz istniejące palety
    conn = get_db()
    palety = conn.execute('SELECT id, nazwa, dostawca, data_zakupu FROM palety ORDER BY data_dodania DESC').fetchall()
    
    # Generuj opcje palet
    palety_options = '<option value="">-- Bez palety (luźne produkty) --</option>'
    palety_options += '<option value="__NEW__">➕ Utwórz nową paletę...</option>'
    for p in palety:
        p_id, p_nazwa, p_dostawca, p_data = p
        label = f"{p_nazwa}" if p_nazwa else f"Paleta #{p_id}"
        if p_dostawca:
            label += f" ({p_dostawca})"
        if p_data:
            label += f" - {p_data}"
        palety_options += f'<option value="{p_id}">{label}</option>'
    
    html = f'''
    <div class="hdr"><h1>📥 IMPORT</h1></div>
    
    <form action="/magazyn/import/preview" method="POST" enctype="multipart/form-data" id="importForm">
        
        <!-- WYBÓR PALETY -->
        <div class="card" style="padding:15px;margin-bottom:15px">
            <div style="font-weight:600;margin-bottom:10px;color:#f59e0b">📦 Przypisz do palety:</div>
            <select name="paleta_id" id="paletaSelect" class="form-ctrl" style="width:100%;padding:12px;background:#0a0a0f;border:1px solid #1e1e2e;border-radius:8px;color:#fff;font-size:1rem" onchange="toggleNewPaleta()">
                {palety_options}
            </select>
            
            <!-- NOWA PALETA -->
            <div id="newPaletaFields" style="display:none;margin-top:15px;padding:15px;background:#0a0a0f;border-radius:8px;border:1px solid #f59e0b">
                <div style="font-weight:600;margin-bottom:10px;color:#f59e0b">✨ Nowa paleta:</div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
                    <div>
                        <label style="font-size:0.8rem;color:#64748b">Nazwa palety</label>
                        <input type="text" name="new_paleta_nazwa" class="form-ctrl" placeholder="np. Paleta Warrington #15" style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:6px;color:#fff">
                    </div>
                    <div>
                        <label style="font-size:0.8rem;color:#64748b">Dostawca</label>
                        <select name="new_paleta_dostawca" class="form-ctrl" style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:6px;color:#fff">
                            <option value="Jobalots" selected>Jobalots</option>
                            <option value="Warrington">Warrington</option>
                            <option value="Miglo">Miglo</option>
                            <option value="Amazon">Amazon</option>
                            <option value="Inny">Inny</option>
                        </select>
                    </div>
                </div>
                <div style="margin-top:10px">
                    <label style="font-size:0.8rem;color:#64748b">Cena zakupu palety (PLN brutto)</label>
                    <input type="number" name="new_paleta_cena" class="form-ctrl" placeholder="0.00" step="0.01" style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:6px;color:#fff">
                </div>
            </div>
        </div>
        
        <!-- WYBÓR PLIKU -->
        <div class="card" style="padding:30px;text-align:center;cursor:pointer" onclick="document.getElementById('file').click()">
            <div style="font-size:3rem;margin-bottom:10px">📁</div>
            <div style="font-weight:600">Wybierz plik Excel</div>
            <div style="font-size:0.8rem;color:#64748b;margin-top:5px">.xlsx, .csv</div>
            <input type="file" id="file" name="file" style="display:none" accept=".xlsx,.csv" onchange="this.form.submit()">
        </div>
    </form>
    
    <div class="card" style="padding:15px;margin-top:15px">
        <div style="font-weight:600;color:#eab308;margin-bottom:10px">💡 Obsługiwane formaty:</div>
        <div style="font-size:0.85rem;color:#94a3b8">
            • <strong>Warrington</strong> - auto-detekcja kolumn<br>
            • <strong>Miglo</strong> - ASIN, nazwa, ilość<br>
            • <strong>Jobalots</strong> - manifest produktów<br>
            • <strong>Własny format</strong> - wybierz kolumny ręcznie
        </div>
    </div>
    
    <a href="/magazyn" class="back">← Powrót</a>
    
    <script>
    function toggleNewPaleta() {{
        var sel = document.getElementById('paletaSelect');
        var fields = document.getElementById('newPaletaFields');
        fields.style.display = (sel.value === '__NEW__') ? 'block' : 'none';
    }}
    </script>
    '''
    return render(html)

@magazynier_bp.route('/import/preview', methods=['POST'])
def import_preview():
    """Podgląd pliku przed importem"""
    if 'file' not in request.files:
        return render('<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">Nie wybrano pliku</div><a href="/magazyn/import" class="btn btn-p">← Powrót</a>')
    
    file = request.files['file']
    if file.filename == '':
        return render('<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">Nie wybrano pliku</div><a href="/magazyn/import" class="btn btn-p">← Powrót</a>')
    
    # === OBSŁUGA PALETY ===
    paleta_id = request.form.get('paleta_id', '')
    paleta_nazwa = ''
    paleta_dostawca = ''  # NOWE: przechowujemy dostawcę
    
    if paleta_id == '__NEW__':
        # Utwórz nową paletę
        new_nazwa = request.form.get('new_paleta_nazwa', '').strip()
        new_dostawca = request.form.get('new_paleta_dostawca', '').strip()
        new_cena = request.form.get('new_paleta_cena', '0').strip()
        
        try:
            new_cena = float(new_cena) if new_cena else 0
        except:
            new_cena = 0
        
        if not new_nazwa:
            new_nazwa = f"Import {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        # Jeśli nie wybrano dostawcy, domyślnie Jobalots
        if not new_dostawca:
            new_dostawca = 'Jobalots'
        
        conn = get_db()
        try:
            new_cena_f = float(new_cena) if new_cena else 0
        except:
            new_cena_f = 0
        # cena_zakupu = brutto
        cursor = conn.execute('''INSERT INTO palety (nazwa, dostawca, cena_zakupu, cena_zakupu_netto, data_zakupu) 
            VALUES (?, ?, ?, ?, date('now'))''', (new_nazwa, new_dostawca, new_cena_f, round(new_cena_f / 1.23, 2)))
        paleta_id = str(cursor.lastrowid)
        paleta_nazwa = new_nazwa
        paleta_dostawca = new_dostawca  # NOWE: zapisz dostawcę
        conn.commit()
    elif paleta_id:
        # Pobierz nazwę i dostawcę istniejącej palety
        conn = get_db()
        row = conn.execute('SELECT nazwa, dostawca FROM palety WHERE id = ?', (paleta_id,)).fetchone()
        if row:
            paleta_nazwa = row[0] or f"Paleta #{paleta_id}"
            paleta_dostawca = row[1] or ''  # NOWE: pobierz dostawcę
    
    filename = file.filename.lower()
    headers = []
    preview_rows = []
    total_rows = 0
    
    try:
        if filename.endswith('.xlsx'):
            import openpyxl
            import tempfile
            import os as os_module
            
            tmp_path = os_module.path.join(tempfile.gettempdir(), f'preview_{os_module.getpid()}.xlsx')
            file.save(tmp_path)
            
            try:
                wb = openpyxl.load_workbook(tmp_path, read_only=True)
                ws = wb.active
                
                # Znajdź wiersz z nagłówkami
                for row_idx, row in enumerate(ws.iter_rows(max_row=10, values_only=True), 1):
                    row_str = [str(c or '').strip() for c in row]
                    if any(row_str):
                        headers = row_str
                        break
                
                # Pobierz podgląd (max 5 wierszy)
                for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True)):
                    total_rows += 1
                    if len(preview_rows) < 5:
                        preview_rows.append([str(c or '')[:50] for c in row])
                
                wb.close()
            finally:
                try:
                    os_module.remove(tmp_path)
                except:
                    pass
                    
        elif filename.endswith('.csv'):
            import csv
            import io
            
            raw_data = file.read()
            content = None
            for encoding in ['utf-8-sig', 'utf-8', 'cp1250', 'latin-1']:
                try:
                    content = raw_data.decode(encoding)
                    break
                except:
                    continue
            
            if content:
                delimiter = ';' if ';' in content[:500] else ','
                reader = csv.reader(io.StringIO(content), delimiter=delimiter)
                rows = list(reader)
                
                if rows:
                    headers = [str(h).strip() for h in rows[0]]
                    for row in rows[1:]:
                        total_rows += 1
                        if len(preview_rows) < 5:
                            preview_rows.append([str(c)[:50] for c in row])
        
        # Wykryj automatycznie kolumny (ulepszone wykrywanie EAN)
        auto_ean = auto_asin = auto_nazwa = auto_ilosc = auto_cena = -1
        detected_ean_col_name = ""
        detected_asin_col_name = ""
        
        for i, h in enumerate(headers):
            h_clean = h.lower().replace(' ', '').replace('_', '').replace('-', '')
            h_up = h.upper().replace(' ', '').replace('_', '')
            
            # === INTELIGENTNE WYKRYWANIE EAN ===
            # Szukaj: ean, barcode, kod_kreskowy, gtin, kodean
            if auto_ean == -1 and any(x in h_clean for x in ['ean', 'barcode', 'kodkreskowy', 'gtin', 'kodean']):
                auto_ean = i
                detected_ean_col_name = h
            
            # === OSOBNO WYKRYWANIE ASIN ===
            elif auto_asin == -1 and any(x in h_clean for x in ['asin', 'sku', 'code', 'kod']) and 'kreskowy' not in h_clean:
                auto_asin = i
                detected_asin_col_name = h
            
            # Nazwa produktu
            elif auto_nazwa == -1 and any(x in h_up for x in ['NAZWA', 'NAME', 'TYTUL', 'TITLE', 'PRODUCT', 'OPIS']):
                auto_nazwa = i
            
            # Ilość
            elif auto_ilosc == -1 and any(x in h_up for x in ['ILOSC', 'ILOŚĆ', 'QTY', 'QUANTITY', 'SZT', 'SZTUK']):
                auto_ilosc = i
            
            # Cena
            elif auto_cena == -1 and any(x in h_up for x in ['CENA', 'PRICE', 'KOSZT', 'COST']):
                auto_cena = i
        
        # UWAGA: NIE kopiuj ASIN do EAN! To różne pola.
        # ASIN (B0XXXXXXXX) powinien być w osobnej kolumnie
        
        # === AUTO-DETEKCJA DOSTAWCY Z NAGŁÓWKÓW ===
        if not paleta_dostawca and headers:
            detected_dostawca = detect_supplier(headers)
            if detected_dostawca:
                paleta_dostawca = detected_dostawca
                # Aktualizuj paletę jeśli została utworzona bez dostawcy
                if paleta_id:
                    try:
                        conn = get_db()
                        conn.execute('UPDATE palety SET dostawca = ? WHERE id = ? AND (dostawca IS NULL OR dostawca = "")', 
                            (detected_dostawca, paleta_id))
                        conn.commit()
                    except:
                        pass
        
        # Logi wykrywania (niebieskie)
        detection_logs = ""
        if paleta_dostawca:
            detection_logs += f'<div style="color:#22c55e;padding:4px 0">✅ [INFO] Dostawca: <strong>{paleta_dostawca}</strong></div>'
        if detected_ean_col_name:
            detection_logs += f'<div style="color:#3b82f6;padding:4px 0">ℹ️ [INFO] Wykryto kolumnę EAN: "{detected_ean_col_name}"</div>'
        if detected_asin_col_name and auto_asin != auto_ean:
            detection_logs += f'<div style="color:#3b82f6;padding:4px 0">ℹ️ [INFO] Wykryto kolumnę ASIN: "{detected_asin_col_name}"</div>'
        
        # Generuj opcje select
        def make_options(selected):
            opts = '<option value="-1">-- Brak --</option>'
            for i, h in enumerate(headers):
                sel = 'selected' if i == selected else ''
                opts += f'<option value="{i}" {sel}>{i+1}. {h[:30]}</option>'
            return opts
        
        # Tabela podglądu
        preview_table = '<div style="overflow-x:auto;margin:15px 0"><table style="width:100%;font-size:0.75rem;border-collapse:collapse">'
        preview_table += '<tr style="background:#1e1e2e">'
        for i, h in enumerate(headers):
            preview_table += f'<th style="padding:8px;border:1px solid #2a2a3a;white-space:nowrap">{i+1}. {h[:20]}</th>'
        preview_table += '</tr>'
        
        for row in preview_rows:
            preview_table += '<tr>'
            for c in row:
                preview_table += f'<td style="padding:6px;border:1px solid #1e1e2e;color:#94a3b8">{c[:30]}</td>'
            preview_table += '</tr>'
        preview_table += '</table></div>'
        
        # Info o palecie
        paleta_info = ''
        if paleta_id:
            paleta_info = f'<div class="alert" style="background:#f59e0b22;border:1px solid #f59e0b;color:#f59e0b;padding:10px;border-radius:8px;margin-bottom:15px">📦 Produkty zostaną przypisane do: <strong>{paleta_nazwa}</strong></div>'
        else:
            paleta_info = '<div class="alert" style="background:#64748b22;border:1px solid #64748b;color:#94a3b8;padding:10px;border-radius:8px;margin-bottom:15px">⚠️ Produkty będą bez przypisanej palety (luźne)</div>'
        
        html = f'''
        <div class="hdr"><h1>📋 PODGLĄD IMPORTU</h1><small>{total_rows} wierszy</small></div>
        
        {paleta_info}
        
        <div class="alert alert-ok" style="font-size:0.85rem">Znaleziono {len(headers)} kolumn, {total_rows} produktów</div>
        
        {f'<div class="card" style="padding:10px;font-family:monospace;font-size:0.8rem;background:#0a0a0f">{detection_logs}</div>' if detection_logs else ''}
        
        <div class="card" style="padding:15px">
            <div style="font-weight:600;margin-bottom:10px">📊 Podgląd danych:</div>
            {preview_table}
        </div>
        
        <form action="/magazyn/import/execute" method="POST">
            <input type="hidden" name="filename" value="{file.filename}">
            <input type="hidden" name="paleta_id" value="{paleta_id}">
            <input type="hidden" name="dostawca" value="{paleta_dostawca}">
            
            <div class="card" style="padding:15px">
                <div style="font-weight:600;margin-bottom:15px">🔧 Mapowanie kolumn:</div>
                
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
                    <div class="form-group">
                        <label style="font-size:0.8rem;color:#64748b">Kolumna z EAN</label>
                        <select name="col_ean" class="form-ctrl" style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #1e1e2e;border-radius:8px;color:#fff">
                            <option value="-1">-- Brak --</option>
                            {make_options(auto_ean)}
                        </select>
                        <div style="font-size:0.7rem;color:#64748b;margin-top:4px">Kod kreskowy (8-14 cyfr)</div>
                    </div>
                    <div class="form-group">
                        <label style="font-size:0.8rem;color:#64748b">Kolumna z ASIN</label>
                        <select name="col_asin" class="form-ctrl" style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #1e1e2e;border-radius:8px;color:#fff">
                            <option value="-1">-- Brak --</option>
                            {make_options(-1)}
                        </select>
                        <div style="font-size:0.7rem;color:#64748b;margin-top:4px">Amazon ASIN (B0XXXXXXXX)</div>
                    </div>
                </div>
                
                <div class="form-group">
                    <label style="font-size:0.8rem;color:#64748b">Kolumna z nazwą</label>
                    <select name="col_nazwa" class="form-ctrl" style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #1e1e2e;border-radius:8px;color:#fff">
                        {make_options(auto_nazwa)}
                    </select>
                </div>
                
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
                    <div class="form-group">
                        <label style="font-size:0.8rem;color:#64748b">Ilość</label>
                        <select name="col_ilosc" class="form-ctrl" style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #1e1e2e;border-radius:8px;color:#fff">
                            {make_options(auto_ilosc)}
                        </select>
                    </div>
                    <div class="form-group">
                        <label style="font-size:0.8rem;color:#64748b">Cena</label>
                        <select name="col_cena" class="form-ctrl" style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #1e1e2e;border-radius:8px;color:#fff">
                            {make_options(auto_cena)}
                        </select>
                    </div>
                </div>
            </div>
            
            <button type="submit" class="btn btn-ok">✅ IMPORTUJ {total_rows} PRODUKTÓW</button>
        </form>
        
        <a href="/magazyn/import" class="back">← Powrót</a>
        '''
        return render(html)
        
    except Exception as e:
        return render(f'<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">{str(e)}</div><a href="/magazyn/import" class="btn btn-p">← Powrót</a>')

@magazynier_bp.route('/import/execute', methods=['POST'])
def import_execute():
    """Wykonaj import z wybranymi kolumnami - wymaga ponownego przesłania pliku"""
    # Ze względu na bezstanowość, przekieruj do bezpośredniego importu
    # z parametrami kolumn w URL
    col_ean = request.form.get('col_ean', '-1')
    col_asin = request.form.get('col_asin', '-1')
    col_nazwa = request.form.get('col_nazwa', '1')
    col_ilosc = request.form.get('col_ilosc', '-1')
    col_cena = request.form.get('col_cena', '-1')
    paleta_id = request.form.get('paleta_id', '')
    dostawca = request.form.get('dostawca', '')  # NOWE: pobierz dostawcę
    
    # Info o palecie
    paleta_info = ''
    if paleta_id:
        conn = get_db()
        row = conn.execute('SELECT nazwa FROM palety WHERE id = ?', (paleta_id,)).fetchone()
        paleta_nazwa = row[0] if row else f'Paleta #{paleta_id}'
        paleta_info = f'<div class="alert" style="background:#f59e0b22;border:1px solid #f59e0b;color:#f59e0b;padding:10px;border-radius:8px;margin-bottom:15px">📦 Produkty zostaną przypisane do: <strong>{paleta_nazwa}</strong></div>'
    
    # Mapowanie info
    ean_info = f'EAN=kol.{int(col_ean)+1}' if int(col_ean) >= 0 else 'EAN=brak'
    asin_info = f'ASIN=kol.{int(col_asin)+1}' if int(col_asin) >= 0 else 'ASIN=brak'
    
    # NOWE: dodaj col_asin do URL
    return render(f'''
    <div class="hdr"><h1>📥 IMPORT</h1><small>Krok 2</small></div>
    
    {paleta_info}
    
    <div class="alert alert-warn">Wybierz ponownie ten sam plik żeby zaimportować z wybranymi kolumnami.</div>
    
    <form action="/magazyn/import/final?col_ean={col_ean}&col_asin={col_asin}&col_nazwa={col_nazwa}&col_ilosc={col_ilosc}&col_cena={col_cena}&paleta_id={paleta_id}&dostawca={dostawca}" method="POST" enctype="multipart/form-data">
        <div class="card" style="padding:30px;text-align:center;cursor:pointer" onclick="document.getElementById('file2').click()">
            <div style="font-size:3rem;margin-bottom:10px">📁</div>
            <div style="font-weight:600">Wybierz ten sam plik</div>
            <input type="file" id="file2" name="file" style="display:none" accept=".xlsx,.csv" onchange="this.form.submit()">
        </div>
    </form>
    
    <div class="card" style="padding:15px;margin-top:15px">
        <div style="font-size:0.85rem;color:#94a3b8">
            Mapowanie: {ean_info}, {asin_info}, Nazwa=kol.{int(col_nazwa)+1}, Ilość=kol.{int(col_ilosc)+1 if int(col_ilosc)>=0 else 'brak'}, Cena=kol.{int(col_cena)+1 if int(col_cena)>=0 else 'brak'}
        </div>
    </div>
    
    <a href="/magazyn/import" class="back">← Powrót</a>
    ''')

@magazynier_bp.route('/import/final', methods=['POST'])
def import_final():
    """Finalny import z określonymi kolumnami"""
    if 'file' not in request.files:
        return render('<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">Nie wybrano pliku</div><a href="/magazyn/import" class="btn btn-p">← Powrót</a>')
    
    file = request.files['file']
    if file.filename == '':
        return render('<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">Nie wybrano pliku</div><a href="/magazyn/import" class="btn btn-p">← Powrót</a>')
    
    col_ean = int(request.args.get('col_ean', -1))
    col_asin = int(request.args.get('col_asin', -1))
    col_nazwa = int(request.args.get('col_nazwa', 1))
    col_ilosc = int(request.args.get('col_ilosc', -1))
    col_cena = int(request.args.get('col_cena', -1))
    paleta_id = request.args.get('paleta_id', '')
    dostawca = request.args.get('dostawca', '')  # NOWE: pobierz dostawcę z URL
    
    # Konwertuj paleta_id na int lub None
    paleta_id_int = int(paleta_id) if paleta_id and paleta_id.isdigit() else None
    
    # Pobierz nazwę palety i dostawcę dla podsumowania
    paleta_nazwa = ''
    if paleta_id_int:
        conn = get_db()
        row = conn.execute('SELECT nazwa, dostawca FROM palety WHERE id = ?', (paleta_id_int,)).fetchone()
        if row:
            paleta_nazwa = row[0] if row[0] else f'Paleta #{paleta_id_int}'
            # Jeśli dostawca nie był przekazany, pobierz z palety
            if not dostawca and row[1]:
                dostawca = row[1]
    
    filename = file.filename.lower()
    added = 0
    errors = []
    
    try:
        if filename.endswith('.xlsx'):
            import openpyxl
            import tempfile
            import os as os_module
            
            tmp_path = os_module.path.join(tempfile.gettempdir(), f'import_{os_module.getpid()}.xlsx')
            file.save(tmp_path)
            
            try:
                wb = openpyxl.load_workbook(tmp_path)
                ws = wb.active
                
                conn = get_db()
                for row in ws.iter_rows(min_row=2, values_only=True):
                    try:
                        if not row:
                            continue
                        
                        # === POBIERZ EAN I ASIN Z OSOBNYCH KOLUMN ===
                        ean = ''
                        asin = ''
                        
                        # Kolumna EAN
                        if col_ean >= 0 and col_ean < len(row) and row[col_ean]:
                            ean_raw = str(row[col_ean]).strip()
                            if ean_raw.endswith('.0'):
                                ean_raw = ean_raw[:-2]
                            ean_raw = ean_raw.replace(' ', '').replace('-', '').replace('_', '')
                            if ean_raw and ean_raw.upper() not in ['NONE', 'NAN', '']:
                                ean = ean_raw
                        
                        # Kolumna ASIN
                        if col_asin >= 0 and col_asin < len(row) and row[col_asin]:
                            asin_raw = str(row[col_asin]).strip().upper()
                            if asin_raw and asin_raw not in ['NONE', 'NAN', '']:
                                asin = asin_raw
                        
                        # Pomiń jeśli nie ma ani EAN ani ASIN
                        if not ean and not asin:
                            continue
                        
                        # === AUTO-DETEKCJA JEŚLI TYLKO JEDNA KOLUMNA ===
                        # Jeśli EAN wygląda jak ASIN, przenieś go
                        if ean and not asin and len(ean) == 10 and ean.upper().startswith('B0'):
                            asin = ean.upper()
                            ean = ''
                        
                        nazwa = str(row[col_nazwa] if col_nazwa >= 0 and col_nazwa < len(row) and row[col_nazwa] else (asin or ean)).strip()
                        if nazwa.upper() in ['NONE', 'NAN']:
                            nazwa = asin or ean
                        
                        ilosc = 1
                        if col_ilosc >= 0 and col_ilosc < len(row) and row[col_ilosc]:
                            try:
                                ilosc = int(float(str(row[col_ilosc]).replace(',', '.')) or 1)
                            except:
                                ilosc = 1
                        
                        cena = 0
                        if col_cena >= 0 and col_cena < len(row) and row[col_cena]:
                            try:
                                cena = float(str(row[col_cena]).replace(',', '.').replace(' ', '') or 0)
                            except:
                                cena = 0
                        
                        # WAŻNE: Zapisujemy CAŁKOWITĄ cenę zakupu = cena_za_sztukę * ilość
                        cena_calkowita = cena * ilosc
                        
                        # NIE pobieramy zdjęć przy imporcie (za wolne)
                        # Użyj przycisku "Pobierz zdjęcia" po imporcie
                        zdjecie = ''
                        
                        # Sprawdź czy produkt już istnieje NA TEJ SAMEJ PALECIE
                        existing = None
                        if ean:
                            existing = conn.execute('SELECT id FROM produkty WHERE ean=? AND paleta_id=?', (ean, paleta_id_int)).fetchone()
                        if not existing and asin:
                            existing = conn.execute('SELECT id FROM produkty WHERE asin=? AND paleta_id=?', (asin, paleta_id_int)).fetchone()
                        
                        if existing:
                            # Aktualizuj istniejący na tej palecie
                            conn.execute('''UPDATE produkty SET nazwa=?, ilosc=?, cena_brutto=?, dostawca=? WHERE id=?''',
                                (nazwa, ilosc, cena_calkowita, dostawca, existing['id']))
                        else:
                            # Nowy produkt — zawsze INSERT, nie dotykamy innych palet
                            conn.execute('''INSERT INTO produkty (ean, asin, nazwa, ilosc, cena_brutto, zdjecie_url, paleta_id, paleta, dostawca)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (ean, asin, nazwa, ilosc, cena_calkowita, zdjecie, paleta_id_int, paleta_nazwa, dostawca))
                        added += 1
                    except Exception as e:
                        errors.append(str(e))
                
                conn.commit()
                wb.close()
            finally:
                try:
                    os_module.remove(tmp_path)
                except:
                    pass
                    
        elif filename.endswith('.csv'):
            import csv
            import io
            
            raw_data = file.read()
            content = None
            for encoding in ['utf-8-sig', 'utf-8', 'cp1250', 'latin-1']:
                try:
                    content = raw_data.decode(encoding)
                    break
                except:
                    continue
            
            if content:
                delimiter = ';' if ';' in content[:500] else ','
                reader = csv.reader(io.StringIO(content), delimiter=delimiter)
                rows = list(reader)
                
                conn = get_db()
                for row in rows[1:]:  # Skip header
                    try:
                        if not row:
                            continue
                        
                        # === POBIERZ EAN I ASIN Z OSOBNYCH KOLUMN ===
                        ean = ''
                        asin = ''
                        
                        # Kolumna EAN
                        if col_ean >= 0 and col_ean < len(row) and row[col_ean]:
                            ean_raw = str(row[col_ean]).strip()
                            if ean_raw.endswith('.0'):
                                ean_raw = ean_raw[:-2]
                            ean_raw = ean_raw.replace(' ', '').replace('-', '').replace('_', '')
                            if ean_raw and ean_raw.upper() not in ['NONE', 'NAN', '']:
                                ean = ean_raw
                        
                        # Kolumna ASIN
                        if col_asin >= 0 and col_asin < len(row) and row[col_asin]:
                            asin_raw = str(row[col_asin]).strip().upper()
                            if asin_raw and asin_raw not in ['NONE', 'NAN', '']:
                                asin = asin_raw
                        
                        # Pomiń jeśli nie ma ani EAN ani ASIN
                        if not ean and not asin:
                            continue
                        
                        # === AUTO-DETEKCJA JEŚLI TYLKO JEDNA KOLUMNA ===
                        # Jeśli EAN wygląda jak ASIN, przenieś go
                        if ean and not asin and len(ean) == 10 and ean.upper().startswith('B0'):
                            asin = ean.upper()
                            ean = ''
                        
                        nazwa = str(row[col_nazwa] if col_nazwa >= 0 and col_nazwa < len(row) else (asin or ean)).strip() or (asin or ean)
                        
                        ilosc = 1
                        if col_ilosc >= 0 and col_ilosc < len(row) and row[col_ilosc]:
                            try:
                                ilosc = int(float(row[col_ilosc].replace(',', '.')) or 1)
                            except:
                                ilosc = 1
                        
                        cena = 0
                        if col_cena >= 0 and col_cena < len(row) and row[col_cena]:
                            try:
                                cena = float(row[col_cena].replace(',', '.').replace(' ', '') or 0)
                            except:
                                cena = 0
                        
                        # WAŻNE: Zapisujemy CAŁKOWITĄ cenę zakupu = cena_za_sztukę * ilość
                        cena_calkowita = cena * ilosc
                        
                        # NIE pobieramy zdjęć przy imporcie (za wolne)
                        zdjecie = ''
                        
                        # Sprawdź czy produkt już istnieje NA TEJ SAMEJ PALECIE
                        existing = None
                        if ean:
                            existing = conn.execute('SELECT id FROM produkty WHERE ean=? AND paleta_id=?', (ean, paleta_id_int)).fetchone()
                        if not existing and asin:
                            existing = conn.execute('SELECT id FROM produkty WHERE asin=? AND paleta_id=?', (asin, paleta_id_int)).fetchone()
                        
                        if existing:
                            # Aktualizuj istniejący na tej palecie
                            conn.execute('''UPDATE produkty SET nazwa=?, ilosc=?, cena_brutto=?, dostawca=? WHERE id=?''',
                                (nazwa, ilosc, cena_calkowita, dostawca, existing['id']))
                        else:
                            # Nowy produkt — zawsze INSERT, nie dotykamy innych palet
                            conn.execute('''INSERT INTO produkty (ean, asin, nazwa, ilosc, cena_brutto, zdjecie_url, paleta_id, paleta, dostawca)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (ean, asin, nazwa, ilosc, cena_calkowita, zdjecie, paleta_id_int, paleta_nazwa, dostawca))
                        added += 1
                    except Exception as e:
                        errors.append(str(e))
                
                conn.commit()
    
    except Exception as e:
        return render(f'<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">{str(e)}</div><a href="/magazyn/import" class="btn btn-p">← Powrót</a>')
    
    # Zaktualizuj liczbę produktów w palecie
    if paleta_id_int and added > 0:
        try:
            conn = get_db()
            conn.execute('UPDATE palety SET ilosc_produktow = ilosc_produktow + ? WHERE id = ?', (added, paleta_id_int))
            conn.commit()
        except:
            pass
    
    # Info o palecie dla podsumowania
    paleta_info = ''
    if paleta_nazwa:
        dostawca_info = f' ({dostawca})' if dostawca else ''
        paleta_info = f'<div class="alert" style="background:#f59e0b22;border:1px solid #f59e0b;color:#f59e0b;padding:10px;border-radius:8px;margin-bottom:15px">📦 Przypisano do palety: <strong>{paleta_nazwa}</strong>{dostawca_info}</div>'
    
    html = f'''
    <div class="hdr"><h1>✅ IMPORT ZAKOŃCZONY</h1></div>
    {paleta_info}
    <div class="alert alert-ok">Zaimportowano {added} produktów</div>
    '''
    if errors:
        html += f'<div class="alert alert-warn">Błędy: {len(errors)}</div>'
    html += '''
    <a href="/magazyn/import" class="btn btn-p">📥 Importuj więcej</a>
    <a href="/magazyn" class="btn btn-2">📦 Magazyn</a>
    <a href="/magazyn" class="back">← Powrót</a>
    '''
    return render(html)

@magazynier_bp.route('/import/upload', methods=['POST'])
def import_upload():
    """Import pliku Excel/CSV"""
    if 'file' not in request.files:
        return render('<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">Nie wybrano pliku</div><a href="/magazyn/import" class="btn btn-p">← Powrót</a>')
    
    file = request.files['file']
    if file.filename == '':
        return render('<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">Nie wybrano pliku</div><a href="/magazyn/import" class="btn btn-p">← Powrót</a>')
    
    filename = file.filename.lower()
    added = 0
    errors = []
    
    try:
        if filename.endswith('.csv'):
            # Import CSV - próbuj różne kodowania
            import csv
            import io
            
            raw_data = file.read()
            content = None
            
            # Próbuj różne kodowania (polskie pliki często są w cp1250)
            for encoding in ['utf-8-sig', 'utf-8', 'cp1250', 'latin-1', 'iso-8859-2']:
                try:
                    content = raw_data.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            
            if content is None:
                return render('<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">Nie można odczytać pliku - nieznane kodowanie</div><a href="/magazyn/import" class="btn btn-p">← Powrót</a>')
            
            # Auto-wykryj separator (przecinek lub średnik)
            delimiter = ';' if ';' in content[:500] else ','
            
            reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
            
            conn = get_db()
            for row in reader:
                try:
                    # Szukaj kolumny z kodem produktu
                    ean = ''
                    for key in ['ean', 'EAN', 'ASIN', 'asin', 'Kod', 'KOD', 'SKU', 'sku', 'Kod produktu', 'kod']:
                        if key in row and row[key]:
                            ean = str(row[key]).strip()
                            break
                    
                    if not ean:
                        continue
                    
                    # Szukaj nazwy
                    nazwa = ean
                    for key in ['nazwa', 'Nazwa', 'NAZWA', 'name', 'Name', 'NAME', 'Tytuł', 'tytul', 'Product', 'Produkt']:
                        if key in row and row[key]:
                            nazwa = str(row[key]).strip()
                            break
                    
                    # Szukaj ilości
                    ilosc = 1
                    for key in ['ilosc', 'Ilosc', 'ILOSC', 'qty', 'Qty', 'QTY', 'Ilość', 'szt', 'Szt']:
                        if key in row and row[key]:
                            try:
                                ilosc = int(float(str(row[key]).replace(',', '.').strip()) or 1)
                            except:
                                ilosc = 1
                            break
                    
                    # Szukaj ceny
                    cena = 0
                    for key in ['cena', 'Cena', 'CENA', 'cena_brutto', 'price', 'Price', 'PRICE', 'Koszt', 'koszt']:
                        if key in row and row[key]:
                            try:
                                cena = float(str(row[key]).replace(',', '.').replace(' ', '').strip() or 0)
                            except:
                                cena = 0
                            break
                    
                    # WAŻNE: Zapisujemy CAŁKOWITĄ cenę zakupu = cena_za_sztukę * ilość
                    cena_calkowita = cena * ilosc
                    
                    # Zawsze INSERT — nie scalaj produktów z różnych palet
                    conn.execute('''INSERT INTO produkty (ean, nazwa, ilosc, cena_brutto, zdjecie_url)
                        VALUES (?, ?, ?, ?, ?)''', (ean, nazwa, ilosc, cena_calkowita, ''))
                    added += 1
                except Exception as e:
                    errors.append(str(e))
            
            conn.commit()
            
        elif filename.endswith('.xlsx'):
            # Import Excel - wymaga openpyxl
            try:
                import openpyxl
                import tempfile
                import os as os_module
                
                # Zapisz plik tymczasowo (Windows-compatible)
                tmp_path = os_module.path.join(tempfile.gettempdir(), f'magazynier_{os_module.getpid()}.xlsx')
                file.save(tmp_path)
                
                try:
                    wb = openpyxl.load_workbook(tmp_path)
                    ws = wb.active
                    
                    # Znajdź wiersz z nagłówkami (może nie być pierwszy)
                    header_row = 1
                    for row_idx in range(1, min(6, ws.max_row + 1)):
                        row_values = [str(cell.value or '').strip().upper() for cell in ws[row_idx]]
                        if any(h in ['EAN', 'ASIN', 'KOD', 'KOD 2', 'SKU', 'NAZWA', 'NAME'] for h in row_values):
                            header_row = row_idx
                            break
                    
                    # Pobierz nagłówki
                    headers = [str(cell.value or '').strip().upper() for cell in ws[header_row]]
                    
                    # Wykryj dostawcę
                    dostawca = detect_supplier(headers)
                    
                    # Mapowanie kolumn - bardziej elastyczne
                    col_map = {'ean': 0, 'nazwa': 1, 'ilosc': 2, 'cena': 3}  # Domyślne pozycje
                    
                    for i, h in enumerate(headers):
                        h_clean = h.replace(' ', '').replace('_', '')
                        # Kod produktu
                        if any(x in h_clean for x in ['EAN', 'ASIN', 'KOD', 'SKU', 'CODE', 'BARCODE']):
                            col_map['ean'] = i
                        # Nazwa
                        elif any(x in h_clean for x in ['NAZWA', 'NAME', 'TYTUL', 'TITLE', 'PRODUCT', 'OPIS']):
                            col_map['nazwa'] = i
                        # Ilość
                        elif any(x in h_clean for x in ['ILOSC', 'ILOŚĆ', 'QTY', 'QUANTITY', 'SZT', 'SZTUK']):
                            col_map['ilosc'] = i
                        # Cena
                        elif any(x in h_clean for x in ['CENA', 'PRICE', 'KOSZT', 'COST', 'BRUTTO']):
                            col_map['cena'] = i
                    
                    conn = get_db()
                    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
                        try:
                            if not row or len(row) == 0:
                                continue
                            
                            # Pobierz EAN - sprawdź czy indeks istnieje
                            ean_idx = col_map.get('ean', 0)
                            ean = str(row[ean_idx] if ean_idx < len(row) else '').strip()
                            
                            # Wyczyść EAN z .0 na końcu (Excel traktuje jako liczbę)
                            if ean.endswith('.0'):
                                ean = ean[:-2]
                            ean = ean.replace('.0', '').replace(' ', '')
                            
                            if not ean or ean.upper() in ['NONE', 'NAN', '']:
                                continue
                            
                            # Nazwa
                            nazwa_idx = col_map.get('nazwa', 1)
                            nazwa = str(row[nazwa_idx] if nazwa_idx < len(row) and row[nazwa_idx] else ean).strip()
                            if nazwa.upper() in ['NONE', 'NAN']:
                                nazwa = ean
                            
                            # Ilość
                            ilosc = 1
                            ilosc_idx = col_map.get('ilosc', 2)
                            if ilosc_idx < len(row) and row[ilosc_idx]:
                                try:
                                    ilosc = int(float(str(row[ilosc_idx]).replace(',', '.')) or 1)
                                except:
                                    ilosc = 1
                            
                            # Cena
                            cena = 0
                            cena_idx = col_map.get('cena', 3)
                            if cena_idx < len(row) and row[cena_idx]:
                                try:
                                    cena = float(str(row[cena_idx]).replace(',', '.').replace(' ', '') or 0)
                                except:
                                    cena = 0
                            
                            # WAŻNE: Zapisujemy CAŁKOWITĄ cenę zakupu = cena_za_sztukę * ilość
                            cena_calkowita = cena * ilosc
                            
                            # Zawsze INSERT — nie scalaj produktów z różnych palet
                            conn.execute('''INSERT INTO produkty (ean, nazwa, ilosc, cena_brutto, dostawca, zdjecie_url)
                                VALUES (?, ?, ?, ?, ?, ?)''', (ean, nazwa, ilosc, cena_calkowita, dostawca or '', ''))
                            added += 1
                        except Exception as row_err:
                            errors.append(str(row_err))
                    
                    conn.commit()
                    wb.close()
                finally:
                    # Usuń plik tymczasowy
                    try:
                        os_module.remove(tmp_path)
                    except:
                        pass
                    
            except ImportError:
                return render('<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">Brak biblioteki openpyxl. Zainstaluj: pip install openpyxl</div><a href="/magazyn/import" class="btn btn-p">← Powrót</a>')
        else:
            return render('<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">Nieobsługiwany format pliku</div><a href="/magazyn/import" class="btn btn-p">← Powrót</a>')
    
    except Exception as e:
        return render(f'<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">{str(e)}</div><a href="/magazyn/import" class="btn btn-p">← Powrót</a>')
    
    html = f'''
    <div class="hdr"><h1>✅ IMPORT ZAKOŃCZONY</h1></div>
    <div class="alert alert-ok">Zaimportowano {added} produktów</div>
    '''
    if errors:
        html += f'<div class="alert alert-warn">Błędy: {len(errors)}</div>'
    html += '''
    <a href="/magazyn/import" class="btn btn-p">📥 Importuj więcej</a>
    <a href="/magazyn" class="btn btn-2">📦 Magazyn</a>
    <a href="/magazyn" class="back">← Powrót</a>
    '''
    return render(html)

@magazynier_bp.route('/dodaj')
def dodaj():
    """Przekierowanie do dodawania produktu"""
    html = '''
    <div class="hdr"><h1>➕ DODAJ PRODUKT</h1></div>
    
    <div class="card" style="padding:15px">
        <form action="/magazyn/szukaj" method="GET">
            <div class="form-group">
                <label>Wpisz EAN / ASIN / SKU</label>
                <input type="text" name="q" class="form-ctrl" placeholder="np. B0CFQBBT7G" autofocus required>
            </div>
            <button type="submit" class="btn btn-ok">🔍 SZUKAJ / DODAJ</button>
        </form>
    </div>
    
    <div style="text-align:center;color:#64748b;padding:15px">lub</div>
    
    <a href="/magazyn/skanuj" class="btn btn-p">📷 SKANUJ KAMERĄ</a>
    <a href="/magazyn/import" class="btn btn-2" style="margin-top:10px">📥 IMPORT Z PLIKU</a>
    
    <a href="/magazyn" class="back">← Powrót</a>
    '''
    return render(html)

@magazynier_bp.route('/skanuj')
def skanuj_kamera():
    """Skaner QR/Barcode kamerą telefonu"""
    from .database import get_config
    base_url = get_config('app_base_url', 'http://localhost:5000')
    
    html = '''
    <div class="hdr">
        <h1>📷 SKANER</h1>
        <small>Skanuj QR lub kod kreskowy</small>
    </div>
    
    <div class="card" style="padding:0;overflow:hidden;border-radius:16px;background:#000">
        <div id="reader" style="width:100%;min-height:300px"></div>
    </div>
    
    <div id="result" class="card" style="display:none;padding:15px;margin-top:15px">
        <div style="font-weight:600;margin-bottom:8px">📦 Znaleziono:</div>
        <div id="resultText" style="font-size:1.1rem;word-break:break-all"></div>
    </div>
    
    <div id="notFound" class="alert alert-warn" style="display:none;margin-top:15px">
        ⚠️ Nie znaleziono produktu o tym kodzie
        <div style="margin-top:10px">
            <a id="addNewLink" href="#" class="btn btn-p" style="display:inline-block;padding:10px 20px">➕ DODAJ NOWY</a>
        </div>
    </div>
    
    <div style="display:flex;gap:10px;margin-top:15px">
        <button onclick="switchCamera()" class="btn btn-2" style="flex:1">🔄 Zmień kamerę</button>
        <button onclick="toggleFlash()" class="btn btn-2" style="flex:1">🔦 Latarka</button>
    </div>
    
    <div style="margin-top:10px;font-size:0.75rem;color:#64748b;text-align:center">
        Obsługuje: QR, EAN-13, EAN-8, Code128, UPC-A
    </div>
    
    <a href="/magazyn/dodaj" class="back">← Powrót</a>
    
    <!-- html5-qrcode library -->
    <script src="https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
    
    <script>
    const baseUrl = "''' + base_url + '''";
    let html5QrCode;
    let currentCamera = 'environment';
    let isProcessing = false;
    let scanCount = 0;

    // Debug info
    const dbg = document.createElement('div');
    dbg.id = 'scanDebug';
    dbg.style.cssText = 'font-size:11px;color:#94a3b8;text-align:center;margin-top:8px';
    dbg.textContent = 'Uruchamianie skanera...';
    document.getElementById('reader').parentNode.after(dbg);

    function onScanSuccess(decodedText, decodedResult) {
        if (isProcessing) return;
        isProcessing = true;

        const fmt = decodedResult.result.format?.formatName || '?';
        console.log('Zeskanowano:', decodedText, fmt);
        dbg.textContent = 'Odczytano: ' + decodedText + ' (' + fmt + ')';

        if (navigator.vibrate) navigator.vibrate([50, 30, 100]);

        document.getElementById('resultText').textContent = decodedText;
        document.getElementById('result').style.display = 'block';

        if (html5QrCode) html5QrCode.pause(true);

        // URL z naszej apki - przekieruj
        if (decodedText.includes('/magazyn/qr/') || decodedText.includes('/magazyn/produkt/')) {
            window.location.href = decodedText;
            return;
        }

        // Obetnij prefix MAG:
        let searchCode = decodedText;
        if (decodedText.toUpperCase().startsWith('MAG:')) {
            searchCode = decodedText.substring(4);
        }

        fetch('/magazyn/api/szukaj?q=' + encodeURIComponent(searchCode))
            .then(r => r.json())
            .then(data => {
                if (data.found && data.product_id) {
                    window.location.href = '/magazyn/produkt/' + data.product_id;
                } else {
                    showNotFound(searchCode);
                }
            })
            .catch(err => {
                console.error(err);
                showNotFound(searchCode);
            });
    }

    function showNotFound(code) {
        document.getElementById('notFound').style.display = 'block';
        document.getElementById('addNewLink').href = '/magazyn/dodaj?ean=' + encodeURIComponent(code);
        setTimeout(() => {
            isProcessing = false;
            document.getElementById('notFound').style.display = 'none';
            document.getElementById('result').style.display = 'none';
            if (html5QrCode) html5QrCode.resume();
        }, 3000);
    }

    function onScanFailure(errorMessage) {
        // Liczy próby skanowania (debug)
        scanCount++;
        if (scanCount % 100 === 0) {
            dbg.textContent = 'Skanuje... (' + scanCount + ' klatek) - skieruj kamerę na kod';
        }
    }

    function startScanner() {
        html5QrCode = new Html5Qrcode("reader", {
            experimentalFeatures: { useBarCodeDetectorIfSupported: true },
            verbose: false
        });

        const config = {
            fps: 10,
            qrbox: function(viewfinderWidth, viewfinderHeight) {
                let size = Math.floor(Math.min(viewfinderWidth, viewfinderHeight) * 0.8);
                return { width: size, height: size };
            },
            formatsToSupport: [
                Html5QrcodeSupportedFormats.QR_CODE,
                Html5QrcodeSupportedFormats.EAN_13,
                Html5QrcodeSupportedFormats.EAN_8,
                Html5QrcodeSupportedFormats.CODE_128,
                Html5QrcodeSupportedFormats.CODE_39,
                Html5QrcodeSupportedFormats.UPC_A,
                Html5QrcodeSupportedFormats.UPC_E
            ]
        };

        html5QrCode.start(
            { facingMode: currentCamera },
            config,
            onScanSuccess,
            onScanFailure
        ).then(() => {
            dbg.textContent = 'Kamera aktywna - skieruj na kod QR';
        }).catch(err => {
            console.error('Blad kamery:', err);
            dbg.textContent = 'Blad: ' + err;
            document.getElementById('reader').innerHTML = '<div style="padding:40px;text-align:center;color:#ef4444">Brak dostepu do kamery<br><small style="color:#64748b">Zezwol w ustawieniach przegladarki.<br>Wymagany HTTPS lub localhost.</small></div>';
        });
    }

    function switchCamera() {
        if (html5QrCode) {
            html5QrCode.stop().then(() => {
                currentCamera = currentCamera === 'environment' ? 'user' : 'environment';
                scanCount = 0;
                startScanner();
            });
        }
    }

    function toggleFlash() {
        if (html5QrCode) {
            html5QrCode.applyVideoConstraints({
                advanced: [{ torch: true }]
            }).catch(e => {
                alert('Latarka nie jest obslugiwana na tym urzadzeniu');
            });
        }
    }

    startScanner();
    </script>
    '''
    return render(html)

@magazynier_bp.route('/api/szukaj')
def api_szukaj():
    """API do szukania produktu po EAN/ASIN"""
    from flask import jsonify
    
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'found': False})
    
    # Obsługa kodów QR z prefiksem MAG: (format: MAG:B0B6NDX3SS)
    if q.upper().startswith('MAG:'):
        q = q[4:]  # Usuń prefiks "MAG:" - zostaje sam kod np. "B0B6NDX3SS"

    conn = get_db()
    # Szukaj po kodzie magazynowym, EAN, ASIN lub nazwie
    product = conn.execute('''
        SELECT id FROM produkty
        WHERE kod_magazynowy = ? OR ean = ? OR asin = ? OR nazwa LIKE ?
        LIMIT 1
    ''', (q.upper(), q, q, f'%{q}%')).fetchone()
    
    if product:
        return jsonify({'found': True, 'product_id': product['id']})
    else:
        return jsonify({'found': False})

@magazynier_bp.route('/etykiety')
def etykiety():
    """Drukowanie etykiet - wybór produktów"""
    conn = get_db()
    products = conn.execute('SELECT * FROM produkty WHERE ilosc > 0 ORDER BY data_dodania DESC LIMIT 100').fetchall()
    
    html = '''
    <div class="hdr"><h1>🏷️ ETYKIETY</h1><small>Drukuj etykiety z QR kodem</small></div>
    
    <div class="alert alert-ok" style="text-align:left;font-size:0.85rem">
        <strong>Jak drukować:</strong><br>
        1. Zaznacz produkty poniżej<br>
        2. Kliknij przycisk drukarki na dole (VRETTI lub NIIMBOT)
    </div>
    
    <div class="section">📦 WYBIERZ PRODUKTY DO DRUKU</div>
    <form action="/magazyn/etykiety/drukuj" method="POST" id="printForm">
    <input type="hidden" name="drukarka" id="drukarkaInput" value="vretti">
    '''
    
    for p in products:
        img_url = p['zdjecie_url'] or ''
        img_html = f'<img src="{img_url}" style="width:50px;height:50px;object-fit:cover;border-radius:6px;background:#1e1e2e" onerror="this.style.display=\'none\'">' if img_url else '<div style="width:50px;height:50px;background:#1e1e2e;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:1.2rem">📦</div>'
        html += f'''<div class="item" style="cursor:pointer;display:flex;align-items:center;gap:10px" onclick="this.querySelector('input').click()">
            <input type="checkbox" name="produkty" value="{p['id']}" style="width:20px;height:20px;flex-shrink:0" onclick="event.stopPropagation()">
            {img_html}
            <div class="item-info" style="flex:1;min-width:0">
                <div class="item-name">{p['nazwa'][:35]}</div>
                <div class="item-meta">{p['ean'] or 'Brak EAN'} | 📍{p['lokalizacja'] or '—'}</div>
            </div>
            <div class="item-right" style="text-align:right;flex-shrink:0">
                <div class="item-qty">{p['ilosc']}</div>
                <div class="item-price">{p['cena_allegro']:.0f} zł</div>
            </div>
        </div>'''
    
    html += '''
    </form>
    
    <div style="position:fixed;bottom:70px;left:0;right:0;padding:15px;background:#0a0a0f;border-top:1px solid #1e1e2e">
        <div style="max-width:1600px;margin:0 auto;display:grid;grid-template-columns:1fr 1fr;gap:10px">
            <button onclick="drukuj('vretti')" class="btn btn-p">🖨️ VRETTI</button>
            <button onclick="drukuj('niimbot')" class="btn btn-purple">📱 NIIMBOT</button>
        </div>
    </div>
    
    <script>
    function drukuj(drukarka) {
        var form = document.getElementById('printForm');
        var checked = form.querySelectorAll('input[name="produkty"]:checked');
        if (checked.length === 0) {
            alert('Wybierz co najmniej jeden produkt!');
            return;
        }
        document.getElementById('drukarkaInput').value = drukarka;
        form.submit();
    }
    </script>
    
    <div style="height:100px"></div>
    <a href="/magazyn" class="back">← Powrót</a>
    '''
    return render(html)


@magazynier_bp.route('/etykiety/drukuj', methods=['POST'])
def etykiety_drukuj():
    """Generowanie etykiet PDF z QR kodami"""
    produkty_ids = request.form.getlist('produkty')
    drukarka = request.form.get('drukarka', 'vretti')
    
    if not produkty_ids:
        return redirect('/magazyn/etykiety')
    
    conn = get_db()
    products = []
    for pid in produkty_ids:
        p = conn.execute('SELECT * FROM produkty WHERE id=?', (pid,)).fetchone()
        if p:
            products.append(dict(p))
    
    if drukarka == 'niimbot':
        return etykiety_niimbot_page(products)
    else:
        return etykiety_vretti_pdf(products)


def etykiety_vretti_pdf(products):
    """Generuje PDF z etykietami dla Vretti 420B (100x150mm)"""
    
    # Funkcja zamiany polskich znaków na ASCII (Helvetica nie obsługuje)
    def pl_to_ascii(text):
        if not text:
            return text
        replacements = {
            'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n', 
            'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z',
            'Ą': 'A', 'Ć': 'C', 'Ę': 'E', 'Ł': 'L', 'Ń': 'N',
            'Ó': 'O', 'Ś': 'S', 'Ź': 'Z', 'Ż': 'Z'
        }
        for pl, ascii in replacements.items():
            text = text.replace(pl, ascii)
        return text
    
    try:
        from reportlab.lib.pagesizes import mm
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
        import qrcode
        import io
        
        # Pobierz bazowy URL z konfiguracji (domyślnie localhost)
        base_url = get_config('app_base_url', 'http://localhost:5000')
        
        # Rozmiar etykiety: 100x150mm
        width, height = 100*mm, 150*mm
        
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=(width, height))
        
        for product in products:
            ilosc = max(int(product.get('ilosc') or 1), 1)
            mag_code = f"MAG-{product['id']:05d}"

            for szt_nr in range(1, ilosc + 1):
                # Numer seryjny: MAG-00464/1, MAG-00464/2, ...
                serial = f"{mag_code}/{szt_nr}" if ilosc > 1 else mag_code

                # QR kod z linkiem do produktu + numer seryjny
                qr = qrcode.QRCode(version=1, box_size=10, border=2)
                qr.add_data(f"{base_url}/magazyn/qr/{product['id']}?sn={szt_nr}")
                qr.make(fit=True)
                qr_img = qr.make_image(fill_color="black", back_color="white")

                # Zapisz QR do bufora
                qr_buffer = io.BytesIO()
                qr_img.save(qr_buffer, format='PNG')
                qr_buffer.seek(0)

                # Rysuj etykietę
                c.setFont("Helvetica-Bold", 16)

                # Nazwa produktu (max 2 linie) - zamień polskie znaki
                nazwa = pl_to_ascii(product['nazwa'][:60])
                if len(nazwa) > 30:
                    c.drawString(10*mm, 135*mm, nazwa[:30])
                    c.drawString(10*mm, 128*mm, nazwa[30:60])
                else:
                    c.drawString(10*mm, 135*mm, nazwa)

                # Numer seryjny (duży, widoczny)
                if ilosc > 1:
                    c.setFont("Helvetica-Bold", 14)
                    c.drawString(10*mm, 118*mm, f"S/N: {serial}")
                    c.setFont("Helvetica", 9)
                    c.drawString(10*mm, 112*mm, f"Sztuka {szt_nr} z {ilosc}")

                # QR kod (lewa strona)
                qr_reader = ImageReader(qr_buffer)
                c.drawImage(qr_reader, 8*mm, 65*mm, width=35*mm, height=35*mm)

                # Info obok QR - zamień polskie znaki
                c.setFont("Helvetica", 12)
                c.drawString(48*mm, 95*mm, f"Polka: {pl_to_ascii(product['lokalizacja'] or '—')}")
                c.drawString(48*mm, 85*mm, f"Stan: {pl_to_ascii(product.get('stan') or 'Nowy')}")
                c.drawString(48*mm, 75*mm, f"Szt: {ilosc}")

                # EAN/kod
                c.setFont("Helvetica", 10)
                ean = product.get('ean') or product.get('asin') or ''
                c.drawString(10*mm, 55*mm, f"Kod: {ean}")

                # Barcode EAN (jeśli jest)
                if ean and len(ean) >= 8:
                    try:
                        from reportlab.graphics.barcode import code128
                        barcode = code128.Code128(ean, barWidth=0.4*mm, barHeight=15*mm)
                        barcode.drawOn(c, 10*mm, 20*mm)
                    except:
                        c.drawString(10*mm, 30*mm, ean)

                # Data wydruku + serial
                c.setFont("Helvetica", 8)
                c.drawString(10*mm, 8*mm, f"{serial} | {datetime.now().strftime('%Y-%m-%d')}")

                c.showPage()
        
        c.save()
        buffer.seek(0)
        
        return Response(
            buffer.getvalue(),
            mimetype='application/pdf',
            headers={'Content-Disposition': f'inline; filename=etykiety_vretti.pdf'}
        )
        
    except ImportError as e:
        return render(f'''
            <div class="hdr"><h1>❌ BŁĄD</h1></div>
            <div class="alert alert-err">Brak biblioteki: {e}<br><br>
            Zainstaluj: <code>pip install reportlab qrcode pillow</code></div>
            <a href="/magazyn/etykiety" class="back">← Powrót</a>
        ''')


def etykiety_niimbot_page(products):
    """Strona do drukowania na Niimbot B1 - masowe drukowanie"""
    import json
    from .printer_manager import get_printer_manager as get_printer, BLEAK_AVAILABLE, IMAGING_AVAILABLE
    
    # Generuj podglądy etykiet
    previews = []
    pm = get_printer()
    base_url = get_config('app_base_url', 'http://localhost:5000')
    
    conn = get_db()
    for p in products:
        from .printer_manager import ProductLabel
        # Dane palety
        paleta_nazwa = ''
        koszt_szt = 0
        if p.get('paleta_id'):
            koszt_szt = _paleta_koszt_szt(conn, p['paleta_id'])
            pal_row = conn.execute('SELECT nazwa FROM palety WHERE id=?', (p['paleta_id'],)).fetchone()
            if pal_row:
                paleta_nazwa = pal_row['nazwa'] or ''

        kod_mag = p.get('kod_magazynowy', '') or ''
        label = ProductLabel(
            nazwa=p['nazwa'][:70],
            qr_data=kod_mag if kod_mag else f"MAG:{p.get('ean') or p.get('asin') or p['id']}",
            lokalizacja=p.get('lokalizacja', ''),
            ean=p.get('ean', ''),
            ilosc=p.get('ilosc', 1) or 1,
            dostawca=p.get('dostawca', '') or '',
            data_zakupu=p.get('data_zakupu', '') or p.get('data_dodania', '') or '',
            paleta=paleta_nazwa,
            koszt_szt=koszt_szt,
            cena_allegro=float(p.get('cena_allegro', 0) or 0),
            kod_magazynowy=kod_mag
        )
        preview = pm.generate_label_preview(label) if IMAGING_AVAILABLE else ''
        previews.append({
            'id': p['id'],
            'nazwa': p['nazwa'],
            'preview': preview,
            'ean': p.get('ean', ''),
            'lokalizacja': p.get('lokalizacja', ''),
            'ilosc': p.get('ilosc', 1),
            'dostawca': p.get('dostawca', '')
        })
    
    products_json = json.dumps(products)
    
    # Status backendu
    backend_status = '✅ Gotowe' if BLEAK_AVAILABLE else '❌ Brak biblioteki bleak'
    
    html = f'''
    <div class="hdr"><h1>🏷️ ETYKIETY NIIMBOT</h1><small>{len(products)} etykiet do druku</small></div>

    <!-- Instrukcja -->
    <div class="card" style="background:linear-gradient(135deg,rgba(34,197,94,0.15),rgba(59,130,246,0.15));border:1px solid rgba(34,197,94,0.3);padding:12px;margin-bottom:12px">
        <div style="font-size:0.85rem;color:#e2e8f0;line-height:1.5">
            Kliknij 🖨️ → pobierze PNG → otworz w apce Niimbot
        </div>
    </div>

    <!-- Masowe akcje -->
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:15px">
        <button onclick="downloadAll()" class="btn btn-p" style="padding:14px;font-size:0.95rem" id="btnAll">
            📥 POBIERZ WSZ.
        </button>
        <a href="/magazyn/etykiety/niimbot/zip?ids={','.join(str(p['id']) for p in products)}" class="btn btn-2" style="padding:14px;font-size:0.95rem;display:flex;align-items:center;justify-content:center;text-decoration:none">
            📦 ZIP
        </a>
        <button onclick="openNiimbot()" class="btn btn-purple" style="padding:14px;font-size:0.95rem">
            📱 NIIMBOT
        </button>
    </div>

    <!-- LISTA ETYKIET -->
    <div id="printList" style="display:flex;flex-direction:column;gap:10px">
    '''

    for i, pv in enumerate(previews):
        html += f'''
        <div class="card" style="padding:12px" id="card-{i}">
            <div style="display:flex;gap:12px;align-items:center">
                <img src="{pv['preview']}" style="width:100px;height:auto;border:1px solid #2d2d3e;border-radius:8px;cursor:pointer;background:#fff"
                     alt="Etykieta" onclick="showPreview({pv['id']}, this.src)">
                <div style="flex:1;min-width:0">
                    <div style="font-weight:600;font-size:0.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{pv['nazwa'][:35]}</div>
                    <div style="font-size:0.75rem;color:#64748b;margin-top:4px">{pv['ean'] or 'Brak EAN'} | 📍 {pv['lokalizacja'] or '—'}</div>
                    <div style="font-size:0.7rem;color:#8b5cf6;margin-top:2px">x{pv['ilosc']} szt.</div>
                </div>
                <button onclick="printLabel({pv['id']}, '{(pv['ean'] or str(pv['id']))}', {i})"
                   style="min-width:60px;padding:14px 18px;background:#22c55e;color:#fff;border:none;border-radius:12px;font-size:1.1rem;font-weight:700;cursor:pointer"
                   id="btn-{i}">
                    💾
                </button>
            </div>
        </div>'''

    html += f'''
    </div>

    <!-- Fullscreen preview overlay -->
    <div id="previewOverlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.95);z-index:1000;flex-direction:column;align-items:center;justify-content:center;padding:20px"
         onclick="closePreview()">
        <img id="previewImg" style="max-width:90%;max-height:70vh;border-radius:8px;background:#fff">
        <div style="margin-top:20px;display:flex;gap:12px">
            <a id="previewDownloadBtn" download onclick="event.stopPropagation()"
               style="padding:16px 32px;background:#22c55e;color:#fff;border:none;border-radius:12px;font-size:1.1rem;font-weight:700;cursor:pointer;text-decoration:none;display:flex;align-items:center">
                📥 POBIERZ PNG
            </a>
            <button onclick="event.stopPropagation();openNiimbot()"
                    style="padding:16px 32px;background:#8b5cf6;color:#fff;border:none;border-radius:12px;font-size:1.1rem;font-weight:700;cursor:pointer">
                📱 OTWORZ NIIMBOT
            </button>
        </div>
    </div>

    <!-- Licznik -->
    <div id="counter" style="display:none;position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:#22c55e;color:#fff;padding:10px 20px;border-radius:20px;font-weight:600;z-index:100"></div>

    <a href="/magazyn/etykiety" class="back">← Powrot</a>

    <script>
    const products = {products_json};
    let printed = 0;

    // Pobierz i zapisz PNG — potem user otwiera w Niimbot
    async function printLabel(productId, ean, btnIdx) {{
        const btn = document.getElementById('btn-' + btnIdx);
        btn.textContent = '⏳';
        btn.disabled = true;

        try {{
            // Pobierz PNG
            const link = document.createElement('a');
            link.href = '/magazyn/etykiety/niimbot/png/' + productId;
            link.download = 'etykieta_' + ean + '.png';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);

            btn.textContent = '✅';
            btn.style.background = '#64748b';
            markPrinted();
        }} catch(e) {{
            btn.textContent = '❌';
            btn.disabled = false;
        }}
    }}

    // Podglad pelnoekranowy — klik w miniaturke
    function showPreview(productId, imgSrc) {{
        const overlay = document.getElementById('previewOverlay');
        overlay.style.display = 'flex';
        document.getElementById('previewImg').src = imgSrc;

        document.getElementById('previewDownloadBtn').href = '/magazyn/etykiety/niimbot/png/' + productId;
        document.getElementById('previewDownloadBtn').download = 'etykieta_' + productId + '.png';
    }}

    function closePreview() {{
        document.getElementById('previewOverlay').style.display = 'none';
    }}

    // Pobierz wszystkie po kolei
    async function downloadAll() {{
        const btn = document.getElementById('btnAll');
        btn.disabled = true;

        for (let i = 0; i < products.length; i++) {{
            const p = products[i];
            const link = document.createElement('a');
            link.href = '/magazyn/etykiety/niimbot/png/' + p.id;
            link.download = 'etykieta_' + (p.ean || p.id) + '.png';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);

            const cardBtn = document.getElementById('btn-' + i);
            if (cardBtn) {{ cardBtn.textContent = '✅'; cardBtn.style.background = '#64748b'; }}
            markPrinted();

            btn.textContent = '⏳ ' + (i+1) + '/' + products.length;
            await new Promise(r => setTimeout(r, 600));
        }}

        btn.disabled = false;
        btn.textContent = '📥 POBIERZ WSZ.';
        showToast('Wszystkie pobrane! Otworz apke Niimbot');
    }}

    // Otworz apke Niimbot na Androidzie
    function openNiimbot() {{
        const a = document.createElement('a');
        a.href = 'intent://#Intent;package=com.gengcon.android.jccloudprinter;S.browser_fallback_url=https%3A%2F%2Fplay.google.com%2Fstore%2Fapps%2Fdetails%3Fid%3Dcom.gengcon.android.jccloudprinter;end';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    }}

    function markPrinted() {{
        printed++;
        showToast('Pobrano ' + printed + '/' + products.length);
    }}

    function showToast(msg) {{
        const c = document.getElementById('counter');
        c.style.display = 'block';
        c.textContent = msg;
        if (printed >= products.length) {{
            c.style.background = '#8b5cf6';
        }}
        clearTimeout(c._timer);
        c._timer = setTimeout(() => {{ c.style.display = 'none'; }}, 4000);
    }}
    </script>
    '''
    return render(html)


@magazynier_bp.route('/api/print/niimbot', methods=['POST'])
def api_print_niimbot():
    """API do drukowania pojedynczej etykiety na Niimbot przez backend (bleak)"""
    from flask import jsonify
    import asyncio
    
    try:
        from .printer_manager import get_printer_manager as get_printer, ProductLabel, BLEAK_AVAILABLE
    except ImportError:
        return jsonify({'success': False, 'message': 'Brak modułu printer_manager'})
    
    if not BLEAK_AVAILABLE:
        return jsonify({'success': False, 'message': 'Brak biblioteki bleak - zainstaluj: pip install bleak'})
    
    data = request.get_json()
    product_id = data.get('product_id')
    
    if not product_id:
        return jsonify({'success': False, 'message': 'Brak product_id'})
    
    # Pobierz produkt
    conn = get_db()
    product = conn.execute('SELECT * FROM produkty WHERE id=?', (product_id,)).fetchone()

    if not product:
        return jsonify({'success': False, 'message': 'Produkt nie znaleziony'})

    p = dict(product)

    # Dane palety
    paleta_nazwa = ''
    koszt_szt = 0
    if p.get('paleta_id'):
        koszt_szt = _paleta_koszt_szt(conn, p['paleta_id'])
        pal_row = conn.execute('SELECT nazwa FROM palety WHERE id=?', (p['paleta_id'],)).fetchone()
        if pal_row:
            paleta_nazwa = pal_row['nazwa'] or ''

    # Przygotuj etykietę
    kod_mag = p.get('kod_magazynowy', '') or ''
    label = ProductLabel(
        nazwa=p['nazwa'][:35],
        qr_data=kod_mag if kod_mag else f"MAG:{p.get('ean') or p.get('asin') or p['id']}",
        lokalizacja=p.get('lokalizacja', ''),
        ean=p.get('ean', ''),
        ilosc=p.get('ilosc', 1) or 1,
        dostawca=p.get('dostawca', '') or '',
        data_zakupu=p.get('data_zakupu', '') or p.get('data_dodania', '') or '',
        paleta=paleta_nazwa,
        koszt_szt=koszt_szt,
        cena_allegro=float(p.get('cena_allegro', 0) or 0),
        kod_magazynowy=kod_mag
    )
    
    # Drukuj przez BleakTransport + niimprint (prawidłowy protokół Niimbot)
    pm = get_printer()
    bt_address = pm.device_address or ''

    # Pobierz adres z config jeśli brak
    if not bt_address:
        try:
            from .database import get_config
            bt_address = get_config('niimbot_bt_address', '')
        except:
            pass

    try:
        from .printer_manager import print_niimbot_ble_sync
        result = print_niimbot_ble_sync(
            nazwa=label.nazwa,
            qr_data=label.qr_data,
            lokalizacja=label.lokalizacja,
            ean=label.ean,
            bt_address=bt_address,
            copies=1,
            ilosc=label.ilosc,
            dostawca=label.dostawca,
            data_zakupu=label.data_zakupu,
            paleta=label.paleta,
            koszt_szt=label.koszt_szt,
            cena_allegro=label.cena_allegro
        )
        if not result.get('success'):
            result['message'] = f"[ROUTE:api_print_niimbot] {result.get('message','')}"
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'[ROUTE:api_print_niimbot] {e}'})


@magazynier_bp.route('/etykiety/niimbot/png/<int:product_id>')
def etykiety_niimbot_png(product_id):
    """Pobierz pojedynczy PNG etykiety dla Niimbot"""
    import io
    
    try:
        from .printer_manager import get_printer_manager as get_printer, ProductLabel, IMAGING_AVAILABLE
    except ImportError:
        return "Brak modułu printer_manager", 500
    
    if not IMAGING_AVAILABLE:
        return "Brak biblioteki Pillow", 500
    
    # Pobierz produkt
    conn = get_db()
    p = conn.execute('SELECT * FROM produkty WHERE id=?', (product_id,)).fetchone()

    if not p:
        return "Produkt nie znaleziony", 404

    p = dict(p)

    # Dane palety
    paleta_nazwa = ''
    koszt_szt = 0
    if p.get('paleta_id'):
        koszt_szt = _paleta_koszt_szt(conn, p['paleta_id'])
        pal_row = conn.execute('SELECT nazwa FROM palety WHERE id=?', (p['paleta_id'],)).fetchone()
        if pal_row:
            paleta_nazwa = pal_row['nazwa'] or ''

    # Generuj etykietę
    pm = get_printer()
    kod_mag = p.get('kod_magazynowy', '') or ''
    label = ProductLabel(
        nazwa=p['nazwa'][:35],
        qr_data=kod_mag if kod_mag else f"MAG:{p.get('ean') or p.get('asin') or p['id']}",
        lokalizacja=p.get('lokalizacja', ''),
        ean=p.get('ean', ''),
        ilosc=p.get('ilosc', 1),
        dostawca=p.get('dostawca', ''),
        data_zakupu=p.get('data_zakupu', '') or p.get('data_dodania', ''),
        paleta=paleta_nazwa,
        koszt_szt=koszt_szt,
        cena_allegro=float(p.get('cena_allegro', 0) or 0),
        kod_magazynowy=kod_mag
    )

    # Generuj obraz
    img = pm._generate_label_image(label)
    
    # Konwertuj do PNG
    img_buffer = io.BytesIO()
    from PIL import Image
    img_rgb = Image.new('RGB', img.size, 'white')
    img_rgb.paste(img.convert('L'))
    img_rgb.save(img_buffer, format='PNG')
    img_buffer.seek(0)
    
    # Nazwa pliku
    filename = f"etykieta_{p.get('ean') or p.get('asin') or p['id']}.png"
    
    return Response(
        img_buffer.getvalue(),
        mimetype='image/png',
        headers={
            'Content-Disposition': f'attachment; filename={filename}',
            'Cache-Control': 'no-cache'
        }
    )


@magazynier_bp.route('/etykiety/niimbot/zip')
def etykiety_niimbot_zip():
    """Pobierz ZIP z etykietami PNG dla Niimbot"""
    import zipfile
    import io
    
    try:
        from .printer_manager import get_printer_manager as get_printer, ProductLabel, IMAGING_AVAILABLE
    except ImportError:
        return "Brak modułu printer_manager", 500
    
    if not IMAGING_AVAILABLE:
        return "Brak biblioteki Pillow - zainstaluj: pip install pillow qrcode", 500
    
    ids_str = request.args.get('ids', '')
    if not ids_str:
        return redirect('/magazyn/etykiety')
    
    ids = [int(x) for x in ids_str.split(',') if x.strip().isdigit()]
    
    if not ids:
        return redirect('/magazyn/etykiety')
    
    # Pobierz produkty
    conn = get_db()
    products = []
    for pid in ids:
        p = conn.execute('SELECT * FROM produkty WHERE id=?', (pid,)).fetchone()
        if p:
            products.append(dict(p))
    
    if not products:
        return "Brak produktów", 404
    
    # Generuj ZIP z PNG
    pm = get_printer()
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, p in enumerate(products):
            kod_mag = p.get('kod_magazynowy', '') or ''
            label = ProductLabel(
                nazwa=p['nazwa'][:35],
                qr_data=kod_mag if kod_mag else f"MAG:{p.get('ean') or p.get('asin') or p['id']}",
                lokalizacja=p.get('lokalizacja', ''),
                ean=p.get('ean', ''),
                ilosc=p.get('ilosc', 1),
                dostawca=p.get('dostawca', ''),
                data_zakupu=p.get('data_zakupu', '') or p.get('data_dodania', ''),
                kod_magazynowy=kod_mag
            )
            
            # Generuj obraz
            img = pm._generate_label_image(label)
            
            # Konwertuj do PNG
            img_buffer = io.BytesIO()
            # Konwertuj 1-bit do RGB dla lepszego PNG
            from PIL import Image
            img_rgb = Image.new('RGB', img.size, 'white')
            img_rgb.paste(img.convert('L'))
            img_rgb.save(img_buffer, format='PNG')
            
            # Nazwa pliku
            safe_name = ''.join(c if c.isalnum() else '_' for c in p['nazwa'][:20])
            filename = f"etykieta_{i+1:03d}_{safe_name}.png"
            
            zf.writestr(filename, img_buffer.getvalue())
    
    zip_buffer.seek(0)
    
    return Response(
        zip_buffer.getvalue(),
        mimetype='application/zip',
        headers={'Content-Disposition': f'attachment; filename=etykiety_niimbot_{len(products)}szt.zip'}
    )


@magazynier_bp.route('/qr/<int:product_id>')
def qr_product_view(product_id):
    """Strona wyświetlana po zeskanowaniu QR kodu"""
    conn = get_db()
    product = conn.execute('SELECT * FROM produkty WHERE id=?', (product_id,)).fetchone()
    
    if not product:
        return render('''
            <div class="hdr"><h1>❌ NIE ZNALEZIONO</h1></div>
            <div class="alert alert-err">Produkt nie istnieje w bazie</div>
            <a href="/magazyn" class="back">← Magazyn</a>
        ''')
    
    p = dict(product)
    
    # Sprawdź czy produkt jest sprzedany (sprawdź w Allegro - TODO)
    sprzedany = (p.get('status') or 'nowy') == 'sprzedany'
    
    # Status badge
    if sprzedany:
        status_html = '<span class="badge" style="background:#22c55e">✅ SPRZEDANY</span>'
        action_html = '''
            <div class="alert alert-ok" style="margin-bottom:15px">
                <b>Zamówienie do wysyłki!</b><br>
                <span id="buyerInfo">Ładowanie danych kupującego...</span>
            </div>
            <a href="#" class="btn btn-ok">📦 OZNACZ JAKO WYSŁANE</a>
            <a href="#" class="btn btn-2">🖨️ DRUKUJ ETYKIETĘ INPOST</a>
        '''
    else:
        status_html = '<span class="badge" style="background:#3b82f6">📦 W MAGAZYNIE</span>'
        action_html = f'''
            <a href="/magazyn/produkt/{p['id']}/edit" class="btn btn-2">✏️ EDYTUJ</a>
            <a href="/paletomat/generator/from-magazyn/{p['id']}" class="btn btn-p">🛒 WYSTAW NA ALLEGRO</a>
        '''
    
    html = f'''
    <div class="hdr">
        <h1>📦 PRODUKT</h1>
        {status_html}
    </div>
    
    <div class="card">
        <div class="card-body">
            <div class="card-name">{p['nazwa']}</div>
            
            <div class="loc">
                <div class="loc-title">📍 LOKALIZACJA</div>
                <div class="loc-grid">
                    <div><div class="loc-v">{p.get('lokalizacja', '—') or '—'}</div><div class="loc-l">Półka</div></div>
                    <div><div class="loc-v">{p.get('regal', '—') or '—'}</div><div class="loc-l">Regał</div></div>
                    <div><div class="loc-v">{p.get('paleta', '—') or '—'}</div><div class="loc-l">Paleta</div></div>
                </div>
            </div>
            
            <div class="det-grid">
                <div class="det"><div class="det-l">Cena Allegro</div><div class="det-v green">{p['cena_allegro']:.2f} zł</div></div>
                <div class="det"><div class="det-l">Ilość</div><div class="det-v">{p['ilosc']} szt</div></div>
                <div class="det"><div class="det-l">EAN</div><div class="det-v">{p.get('ean', '—') or '—'}</div></div>
                <div class="det"><div class="det-l">Dostawca</div><div class="det-v">{p.get('dostawca', '—') or '—'}</div></div>
            </div>
        </div>
    </div>
    
    {action_html}
    
    <a href="/magazyn" class="back">← Powrót</a>
    </div>
    '''
    return render(html)

@magazynier_bp.route('/historia/<int:historia_id>/edytuj', methods=['GET', 'POST'])
def edytuj_historie(historia_id):
    """Edycja wpisu w historii produktu"""
    conn = get_db()
    
    if request.method == 'POST':
        opis = request.form.get('opis', '').strip()
        akcja = request.form.get('akcja', 'edytowano').strip()
        
        if opis:
            conn.execute('''
                UPDATE historia_produktu 
                SET opis = ?, akcja = ?
                WHERE id = ?
            ''', (opis, akcja, historia_id))
            conn.commit()
        
        # Pobierz produkt_id aby przekierować z powrotem
        h = conn.execute('SELECT produkt_id FROM historia_produktu WHERE id = ?', (historia_id,)).fetchone()
        
        if h:
            p = get_db().execute('SELECT * FROM produkty WHERE id = ?', (h['produkt_id'],)).fetchone()
            if p:
                product_code = get_product_code(p)
                return redirect(f'/magazyn/produkt/{product_code}?msg=Zaktualizowano+wpis+historii')
        
        return redirect('/magazyn')
    
    # GET - formularz edycji
    h = conn.execute('SELECT * FROM historia_produktu WHERE id = ?', (historia_id,)).fetchone()
    if not h:
        return render('<div class="hdr"><h1>❌ BŁĄD</h1></div><div class="alert alert-err">Wpis nie istnieje</div><a href="/magazyn" class="back">← Powrót</a>')
    
    h = dict(h)
    
    # Dostępne akcje
    akcje = [
        ('dodano', '📥 Dodano'),
        ('edytowano', '✏️ Edytowano'),
        ('wystawiono', '🛒 Wystawiono'),
        ('sprzedano', '💰 Sprzedano'),
        ('wyslano', '📦 Wysłano'),
        ('zmiana_ceny', '💵 Zmiana ceny'),
        ('zmiana_lokalizacji', '📍 Zmiana lokalizacji'),
        ('zmiana_ilosci', '📊 Zmiana ilości'),
        ('drukowano', '🏷️ Drukowano'),
        ('skanowano', '📱 Skanowano'),
        ('importowano', '📂 Importowano'),
        ('scrapowano', '🔍 Scrapowano'),
        ('wygenerowano_opis', '✨ Wygenerowano opis'),
        ('dodano_zdjecia', '📷 Dodano zdjęcia'),
        ('przeniesiono', '🔄 Przeniesiono'),
        ('oznaczono', '🏷️ Oznaczono')
    ]
    
    akcje_options = ''.join([f'<option value="{a[0]}" {"selected" if h["akcja"] == a[0] else ""}>{a[1]}</option>' for a in akcje])
    
    
    html = f'''
    <div class="hdr"><h1>✏️ EDYCJA WPISU HISTORII</h1></div>
    
    <form method="POST" class="card">
        <div class="form-g">
            <label>Typ akcji</label>
            <select name="akcja" class="form-input">
                {akcje_options}
            </select>
        </div>
        
        <div class="form-g">
            <label>Opis</label>
            <textarea name="opis" class="form-input" rows="3" required>{h['opis']}</textarea>
        </div>
        
        <div style="display:flex;gap:10px;margin-top:20px">
            <button type="submit" class="btn btn-ok">💾 Zapisz</button>
            <a href="/magazyn" class="btn btn-p">✖ Anuluj</a>
        </div>
    </form>
    '''
    return render(html)

@magazynier_bp.route('/historia/<int:historia_id>/usun')
def usun_historie(historia_id):
    """Usunięcie wpisu z historii produktu"""
    conn = get_db()
    
    # Pobierz produkt_id przed usunięciem
    h = conn.execute('SELECT produkt_id FROM historia_produktu WHERE id = ?', (historia_id,)).fetchone()
    
    # Usuń wpis
    conn.execute('DELETE FROM historia_produktu WHERE id = ?', (historia_id,))
    conn.commit()
    
    # Przekieruj z powrotem do produktu
    redirect_url = request.args.get('redirect', '/magazyn')
    if redirect_url.startswith('/magazyn/produkt/'):
        redirect_url += '?msg=Usunięto+wpis+z+historii'
    
    return redirect(redirect_url)

@magazynier_bp.route('/produkty/masowa-edycja', methods=['POST'])
def masowa_edycja_statusu():
    """Masowa zmiana statusu produktów"""
    from .database import add_historia
    
    product_ids = request.form.getlist('product_ids')
    new_status = request.form.get('new_status', '').strip()
    new_stan = request.form.get('new_stan', '').strip()
    new_lokalizacja = request.form.get('new_lokalizacja', '').strip()
    new_cena_str = request.form.get('new_cena_allegro', '').strip()
    new_cena_allegro = float(new_cena_str) if new_cena_str else None

    if not product_ids:
        return redirect('/magazyn/produkty?msg=Nie+zaznaczono+produktów')

    if not new_status and not new_stan and not new_lokalizacja and new_cena_allegro is None:
        return redirect('/magazyn/produkty?msg=Nie+wybrano+żadnej+zmiany')

    # Nazwy statusów dla logów
    status_names = {
        'magazyn': 'Magazyn',
        'wystawiony': 'Wystawiony (na Allegro)',
        'sprzedany': 'Sprzedany',
        'uszkodzony': 'Uszkodzony',
        'zwrot': 'Zwrot'
    }
    
    conn = get_db()
    updated_count = 0
    
    try:
        for product_id in product_ids:
            # Pobierz aktualny produkt
            p = conn.execute('SELECT * FROM produkty WHERE id = ?', (product_id,)).fetchone()
            if not p:
                continue
            
            old_status = p['status'] or 'magazyn'

            # Buduj dynamicznie pola do aktualizacji
            updates = []
            vals = []
            changes = []

            if new_status:
                updates.append('status = ?')
                vals.append(new_status)
                changes.append(f"status: {status_names.get(old_status, old_status)} → {status_names.get(new_status, new_status)}")
            if new_stan:
                updates.append('stan = ?')
                vals.append(new_stan)
                changes.append(f"stan: {p['stan'] or '—'} → {new_stan}")
            if new_lokalizacja:
                updates.append('lokalizacja = ?')
                vals.append(new_lokalizacja)
                changes.append(f"lokalizacja: {p['lokalizacja'] or '—'} → {new_lokalizacja}")
            if new_cena_allegro is not None:
                updates.append('cena_allegro = ?')
                vals.append(new_cena_allegro)
                changes.append(f"cena: {p['cena_allegro'] or 0:.0f} → {new_cena_allegro:.0f} zł")

            if updates:
                # updates contains only whitelisted 'column = ?' fragments (status, stan, lokalizacja, cena_allegro)
                ALLOWED_SET_CLAUSES = {'status = ?', 'stan = ?', 'lokalizacja = ?', 'cena_allegro = ?'}
                if not all(u in ALLOWED_SET_CLAUSES for u in updates):
                    return jsonify({'error': 'Niedozwolone pole'}), 400
                vals.append(product_id)
                conn.execute("UPDATE produkty SET " + ', '.join(updates) + " WHERE id = ?", vals)
                opis = "Masowa edycja: " + " | ".join(changes)
                add_historia(product_id, 'edytowano', opis, {'zmiany': changes})
                updated_count += 1

        conn.commit()

        return redirect(f'/magazyn/produkty?msg=Zaktualizowano+{updated_count}+produktów')
        
    except Exception as e:
        return redirect(f'/magazyn/produkty?msg=Błąd:+{str(e)[:50]}')


# ======================== AUTO-WYCENA ========================

_nbp_cache = {}  # {waluta: (kurs, timestamp)}

def _get_nbp_rate(currency_code):
    """Pobiera aktualny kurs średni NBP. Cache na 6h."""
    import time as _time
    import requests as _req

    now = _time.time()
    cached = _nbp_cache.get(currency_code)
    if cached and (now - cached[1]) < 6 * 3600:
        return cached[0]

    # Fallbacki gdyby API nie odpowiedziało
    _fallback = {'EUR': 4.3, 'USD': 4.0, 'GBP': 5.1}

    try:
        resp = _req.get(
            f'https://api.nbp.pl/api/exchangerates/rates/a/{currency_code}/?format=json',
            timeout=5
        )
        if resp.status_code == 200:
            rate = resp.json()['rates'][0]['mid']
            _nbp_cache[currency_code] = (rate, now)
            print(f"[NBP] Kurs {currency_code}: {rate} PLN")
            return rate
    except Exception as e:
        print(f"[NBP] Błąd pobierania kursu {currency_code}: {e}")

    fallback = _fallback.get(currency_code, 4.3)
    _nbp_cache[currency_code] = (fallback, now)
    return fallback


def _amazon_price_to_pln(price, domain):
    """Konwertuje cenę z Amazon na PLN wg domeny — kurs z NBP"""
    if not price or price <= 0:
        return 0

    _domain_currency = {
        'amazon.pl': None,       # Już PLN
        'amazon.de': 'EUR',
        'amazon.fr': 'EUR',
        'amazon.it': 'EUR',
        'amazon.es': 'EUR',
        'amazon.com': 'USD',
        'amazon.co.uk': 'GBP',
    }

    currency = _domain_currency.get(domain)
    if currency is None:
        return price  # PLN lub nieznana domena — bez konwersji

    rate = _get_nbp_rate(currency)
    return price * rate


@magazynier_bp.route('/api/autowycena/<int:product_id>', methods=['POST'])
def api_autowycena(product_id):
    """Pobiera cenę z Amazon i sugeruje cenę Allegro"""
    from .utils import scrape_amazon_product
    import json
    
    conn = get_db()
    p = conn.execute('SELECT * FROM produkty WHERE id = ?', (product_id,)).fetchone()

    if not p:
        return json.dumps({'error': 'Produkt nie znaleziony'}), 404

    asin = p['asin']
    # Koszt jednostkowy = własna cena produktu (jednostkowa z importu), fallback na średnią z palety
    cena_brutto_szt = float(p['cena_brutto'] or 0) if p['cena_brutto'] and p['cena_brutto'] > 0 else 0
    if cena_brutto_szt == 0 and p['paleta_id']:
        cena_brutto_szt = _paleta_koszt_szt(conn, p['paleta_id'])

    result = {
        'product_id': product_id,
        'asin': asin,
        'cena_brutto_szt': round(cena_brutto_szt, 2),
        'cena_amazon': None,
        'cena_allegro_sugerowana': None,
        'zrodlo': None
    }
    
    # Próbuj pobrać cenę z Amazon
    if asin:
        try:
            amazon_data = scrape_amazon_product(asin)
            if amazon_data and amazon_data.get('price'):
                cena_amazon = amazon_data['price']
                result['cena_amazon'] = round(cena_amazon, 2)

                # Konwersja wg domeny Amazon
                cena_pln = _amazon_price_to_pln(cena_amazon, amazon_data.get('domain'))

                # Sugerowana cena Allegro = 85% ceny w PLN
                result['cena_allegro_sugerowana'] = round(cena_pln * 0.85, 2)
                result['zrodlo'] = f"amazon ({amazon_data.get('domain', '?')})"
        except Exception as e:
            print(f"[Auto-wycena] Błąd pobierania z Amazon: {e}")
    
    # Fallback: brutto × 2.5
    if not result['cena_allegro_sugerowana']:
        if cena_brutto_szt > 0:
            result['cena_allegro_sugerowana'] = round(cena_brutto_szt * 2.5, 2)
            result['zrodlo'] = 'szacowana (brutto × 2.5)'
        else:
            result['zrodlo'] = 'brak danych'
    
    
    return json.dumps(result), 200, {'Content-Type': 'application/json'}


@magazynier_bp.route('/api/autowycena/zastosuj/<int:product_id>', methods=['POST'])
def api_autowycena_zastosuj(product_id):
    """Zapisuje sugerowaną cenę jako cena_allegro"""
    import json
    
    data = request.get_json() or {}
    nowa_cena = data.get('cena')
    
    if not nowa_cena or nowa_cena <= 0:
        return json.dumps({'error': 'Brak ceny'}), 400
    
    conn = get_db()
    p = conn.execute('SELECT cena_allegro FROM produkty WHERE id = ?', (product_id,)).fetchone()
    
    if not p:
        return json.dumps({'error': 'Produkt nie znaleziony'}), 404
    
    stara_cena = p['cena_allegro'] or 0
    
    conn.execute('UPDATE produkty SET cena_allegro = ? WHERE id = ?', (nowa_cena, product_id))
    
    # Dodaj do historii
    add_historia(product_id, 'zmiana_ceny', 
        f'Auto-wycena: {stara_cena:.2f} → {nowa_cena:.2f} zł',
        {'stara_cena': stara_cena, 'nowa_cena': nowa_cena, 'metoda': 'autowycena'})
    
    conn.commit()
    
    return json.dumps({'success': True, 'nowa_cena': nowa_cena}), 200, {'Content-Type': 'application/json'}


@magazynier_bp.route('/api/autowycena/paleta/<int:paleta_id>', methods=['POST'])
def api_autowycena_paleta(paleta_id):
    """Auto-wycena (legacy, redirect do stream)"""
    return api_autowycena_paleta_stream(paleta_id)


@magazynier_bp.route('/api/autowycena-stream/paleta/<int:paleta_id>', methods=['POST'])
def api_autowycena_paleta_stream(paleta_id):
    """Auto-wycena STREAMOWANA — nie timeoutuje przeglądarki"""
    from .utils import scrape_amazon_product, optimize_title_allegro
    import json
    import time

    def generate():
        conn = get_db()
        produkty = conn.execute(
            'SELECT id, asin, nazwa, ilosc, cena_brutto, cena_allegro, paleta_id FROM produkty WHERE paleta_id = ?',
            (paleta_id,)
        ).fetchall()

        paleta_koszt_szt = _paleta_koszt_szt(conn, paleta_id)
        total = len(produkty)

        stats = {
            'type': 'done', 'total': total, 'updated': 0,
            'from_amazon': 0, 'from_estimate': 0,
            'titles_optimized': 0, 'errors': 0
        }

        for i, p in enumerate(produkty, 1):
            asin = p['asin']
            nazwa = p['nazwa'] or ''
            cena_brutto_szt = float(p['cena_brutto'] or 0) if p['cena_brutto'] and float(p['cena_brutto'] or 0) > 0 else 0
            if cena_brutto_szt == 0 and paleta_koszt_szt > 0:
                cena_brutto_szt = paleta_koszt_szt

            cena_allegro = None
            nowa_nazwa = None
            zrodlo = None

            # Próbuj Amazon (z krótkim timeout)
            if asin:
                try:
                    amazon_data = scrape_amazon_product(asin)
                    if amazon_data:
                        if amazon_data.get('price'):
                            cena_amazon = amazon_data['price']
                            cena_pln = _amazon_price_to_pln(cena_amazon, amazon_data.get('domain'))
                            cena_allegro = round(cena_pln * 0.85, 2)
                            zrodlo = 'amazon'
                            stats['from_amazon'] += 1

                        amazon_title = amazon_data.get('title', '')
                        if amazon_title:
                            nowa_nazwa = optimize_title_allegro(amazon_title)
                            if nowa_nazwa and nowa_nazwa != nazwa:
                                stats['titles_optimized'] += 1
                except Exception as e:
                    print(f"[Auto-wycena] Błąd {asin}: {e}")

                time.sleep(0.3)

            # Fallback cena
            if not cena_allegro and cena_brutto_szt > 0:
                cena_allegro = round(cena_brutto_szt * 2.5, 2)
                zrodlo = 'estimate'
                stats['from_estimate'] += 1

            # Fallback tytuł
            if not nowa_nazwa and nazwa:
                try:
                    nowa_nazwa = optimize_title_allegro(nazwa)
                    if nowa_nazwa and nowa_nazwa != nazwa:
                        stats['titles_optimized'] += 1
                    else:
                        nowa_nazwa = None
                except Exception:
                    nowa_nazwa = None

            # Zapisz do DB
            try:
                updates = []
                params = []
                if cena_allegro:
                    updates.append('cena_allegro = ?')
                    params.append(cena_allegro)
                if nowa_nazwa:
                    updates.append('nazwa = ?')
                    params.append(nowa_nazwa)
                if updates:
                    # updates contains only whitelisted 'column = ?' fragments (cena_allegro, nazwa)
                    ALLOWED_SET_CLAUSES = {'cena_allegro = ?', 'nazwa = ?'}
                    if not all(u in ALLOWED_SET_CLAUSES for u in updates):
                        continue
                    params.append(p['id'])
                    conn.execute("UPDATE produkty SET " + ', '.join(updates) + " WHERE id = ?", params)
                    conn.commit()
                    stats['updated'] += 1
            except Exception as e:
                print(f"[Auto-wycena] Błąd zapisu {p['id']}: {e}")
                stats['errors'] += 1

            # Wyślij progress do przeglądarki
            ev = {
                'type': 'progress',
                'current': i,
                'total': total,
                'name': (nazwa or f'Produkt #{p["id"]}')[:50],
                'price': cena_allegro,
                'source': zrodlo
            }
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

        # Podsumowanie
        yield f"data: {json.dumps(stats, ensure_ascii=False)}\n\n"

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


# ======================== GPSR API ========================

@magazynier_bp.route('/api/gpsr/<int:product_id>')
def api_gpsr(product_id):
    """Generuje informacje GPSR dla produktu"""
    from .utils import generuj_gpsr_info
    import json
    
    conn = get_db()
    p = conn.execute('SELECT nazwa, kategoria FROM produkty WHERE id = ?', (product_id,)).fetchone()
    
    if not p:
        return json.dumps({'error': 'Produkt nie znaleziony'}), 404
    
    gpsr = generuj_gpsr_info(p['nazwa'] or '', p['kategoria'] or '')
    
    return json.dumps({'gpsr': gpsr, 'nazwa': p['nazwa']}), 200, {'Content-Type': 'application/json'}


# ======================== RAPORT SPRZEDAŻY EXCEL ========================

@magazynier_bp.route('/raport-sprzedazy')
def raport_sprzedazy_excel():
    """Generuje raport sprzedaży Excel z podziałem na miesiące"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from datetime import datetime
    import os
    
    conn = get_db()

    # Pobierz wszystkie sprzedaże z prawdziwym kosztem z palety
    # Koszt = paleta.cena_zakupu / MAX(ile_sprzedazy_z_palety, ile_produktow_w_palecie)
    sprzedaze = conn.execute('''
        SELECT
            s.id,
            s.data_sprzedazy,
            s.nazwa,
            s.cena,
            s.ilosc,
            s.status,
            p.asin,
            p.ean,
            p.paleta,
            p.dostawca,
            p.kategoria,
            pal.cena_zakupu as paleta_cena_zakupu,
            CASE
                WHEN sc.sale_cnt > 0 AND pal.cena_zakupu > 0
                THEN pal.cena_zakupu / sc.sale_cnt
                ELSE 0
            END as koszt_jednostkowy
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        LEFT JOIN palety pal ON p.paleta_id = pal.id
        LEFT JOIN (
            SELECT pal2.id as paleta_id,
                   CASE
                       WHEN COALESCE(SUM(s2.ilosc), 0) > pal2.ilosc_produktow
                       THEN COALESCE(SUM(s2.ilosc), 0)
                       ELSE pal2.ilosc_produktow
                   END as sale_cnt
            FROM palety pal2
            LEFT JOIN produkty p3 ON p3.paleta_id = pal2.id
            LEFT JOIN sprzedaze s2 ON s2.produkt_id = p3.id
                AND s2.status NOT IN ('anulowana', 'zwrot')
            GROUP BY pal2.id
        ) sc ON pal.id = sc.paleta_id
        WHERE s.status NOT IN ('anulowana', 'zwrot')
        ORDER BY s.data_sprzedazy DESC
    ''').fetchall()
    
    # Utwórz workbook
    wb = Workbook()
    
    # ============ ARKUSZ 1: PODSUMOWANIE MIESIĘCZNE ============
    ws_summary = wb.active
    ws_summary.title = "Podsumowanie"
    
    # Style
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill('solid', fgColor='1e40af')
    money_fill = PatternFill('solid', fgColor='dcfce7')
    border = Side(style='thin', color='e5e7eb')
    cell_border = Border(left=border, right=border, top=border, bottom=border)
    
    # Nagłówki podsumowania
    headers_sum = ['Miesiąc', 'Sprzedaży', 'Przychód', 'Koszt zakupu', 'Prowizja Allegro (11%)', 'Zysk netto', 'ROI %']
    for col, header in enumerate(headers_sum, 1):
        cell = ws_summary.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = cell_border
    
    # Grupuj sprzedaże po miesiącach
    miesiace = {}
    for s in sprzedaze:
        data = s['data_sprzedazy'] or ''
        if data:
            # Format: YYYY-MM-DD lub YYYY-MM-DD HH:MM:SS
            miesiac = data[:7]  # YYYY-MM
        else:
            miesiac = 'Brak daty'
        
        if miesiac not in miesiace:
            miesiace[miesiac] = {'sprzedazy': 0, 'przychod': 0, 'koszt': 0}
        
        cena = s['cena'] or 0
        ilosc = s['ilosc'] or 1
        przychod = cena * ilosc
        koszt = (s['koszt_jednostkowy'] or 0) * ilosc

        miesiace[miesiac]['sprzedazy'] += 1
        miesiace[miesiac]['przychod'] += przychod
        miesiace[miesiac]['koszt'] += koszt
    
    # Sortuj miesiące (najnowsze na górze)
    sorted_miesiace = sorted(miesiace.keys(), reverse=True)
    
    # Wypełnij dane
    row = 2
    total_sprzedazy = 0
    total_przychod = 0
    total_koszt = 0
    total_prowizja = 0
    total_zysk = 0
    
    for miesiac in sorted_miesiace:
        data = miesiace[miesiac]
        przychod = data['przychod']
        koszt = data['koszt']
        prowizja = przychod * 0.11
        zysk = przychod - koszt - prowizja
        roi = (zysk / koszt * 100) if koszt > 0 else 0
        
        # Formatuj nazwę miesiąca
        try:
            dt = datetime.strptime(miesiac, '%Y-%m')
            nazwa_miesiaca = dt.strftime('%B %Y').capitalize()
            # Polski format
            pl_months = {'January': 'Styczeń', 'February': 'Luty', 'March': 'Marzec', 
                        'April': 'Kwiecień', 'May': 'Maj', 'June': 'Czerwiec',
                        'July': 'Lipiec', 'August': 'Sierpień', 'September': 'Wrzesień',
                        'October': 'Październik', 'November': 'Listopad', 'December': 'Grudzień'}
            for en, pl in pl_months.items():
                nazwa_miesiaca = nazwa_miesiaca.replace(en, pl)
        except:
            nazwa_miesiaca = miesiac
        
        ws_summary.cell(row=row, column=1, value=nazwa_miesiaca).border = cell_border
        ws_summary.cell(row=row, column=2, value=data['sprzedazy']).border = cell_border
        ws_summary.cell(row=row, column=3, value=round(przychod, 2)).border = cell_border
        ws_summary.cell(row=row, column=4, value=round(koszt, 2)).border = cell_border
        ws_summary.cell(row=row, column=5, value=round(prowizja, 2)).border = cell_border
        
        zysk_cell = ws_summary.cell(row=row, column=6, value=round(zysk, 2))
        zysk_cell.border = cell_border
        if zysk > 0:
            zysk_cell.fill = money_fill
        
        roi_cell = ws_summary.cell(row=row, column=7, value=f"{roi:.1f}%")
        roi_cell.border = cell_border
        
        total_sprzedazy += data['sprzedazy']
        total_przychod += przychod
        total_koszt += koszt
        total_prowizja += prowizja
        total_zysk += zysk
        
        row += 1
    
    # Wiersz SUMA
    row += 1
    suma_font = Font(bold=True)
    suma_fill = PatternFill('solid', fgColor='fef3c7')
    
    ws_summary.cell(row=row, column=1, value='RAZEM').font = suma_font
    ws_summary.cell(row=row, column=1).fill = suma_fill
    ws_summary.cell(row=row, column=2, value=total_sprzedazy).font = suma_font
    ws_summary.cell(row=row, column=2).fill = suma_fill
    ws_summary.cell(row=row, column=3, value=round(total_przychod, 2)).font = suma_font
    ws_summary.cell(row=row, column=3).fill = suma_fill
    ws_summary.cell(row=row, column=4, value=round(total_koszt, 2)).font = suma_font
    ws_summary.cell(row=row, column=4).fill = suma_fill
    ws_summary.cell(row=row, column=5, value=round(total_prowizja, 2)).font = suma_font
    ws_summary.cell(row=row, column=5).fill = suma_fill
    ws_summary.cell(row=row, column=6, value=round(total_zysk, 2)).font = suma_font
    ws_summary.cell(row=row, column=6).fill = suma_fill
    total_roi = (total_zysk / total_koszt * 100) if total_koszt > 0 else 0
    ws_summary.cell(row=row, column=7, value=f"{total_roi:.1f}%").font = suma_font
    ws_summary.cell(row=row, column=7).fill = suma_fill
    
    # Szerokość kolumn
    ws_summary.column_dimensions['A'].width = 18
    ws_summary.column_dimensions['B'].width = 12
    ws_summary.column_dimensions['C'].width = 14
    ws_summary.column_dimensions['D'].width = 14
    ws_summary.column_dimensions['E'].width = 20
    ws_summary.column_dimensions['F'].width = 14
    ws_summary.column_dimensions['G'].width = 10
    
    # ============ ARKUSZ 2: WSZYSTKIE SZCZEGÓŁY ============
    ws_details = wb.create_sheet("Wszystkie")

    headers_det = ['Data', 'Nazwa produktu', 'Kategoria', 'ASIN', 'EAN', 'Paleta', 'Dostawca',
                   'Ilość', 'Cena sprzedaży', 'Przychód', 'Koszt zakupu', 'Prowizja 11%', 'Zysk']

    def _fill_detail_headers(ws):
        for col, header in enumerate(headers_det, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center')
            cell.border = cell_border

    def _fill_detail_row(ws, row_num, s):
        cena = s['cena'] or 0
        ilosc = s['ilosc'] or 1
        przychod = cena * ilosc
        koszt = (s['koszt_jednostkowy'] or 0) * ilosc
        prowizja = przychod * 0.11
        zysk = przychod - koszt - prowizja

        ws.cell(row=row_num, column=1, value=s['data_sprzedazy'] or '').border = cell_border
        ws.cell(row=row_num, column=2, value=(s['nazwa'] or '')[:60]).border = cell_border
        ws.cell(row=row_num, column=3, value=s['kategoria'] or '').border = cell_border
        ws.cell(row=row_num, column=4, value=s['asin'] or '').border = cell_border
        ws.cell(row=row_num, column=5, value=s['ean'] or '').border = cell_border
        ws.cell(row=row_num, column=6, value=s['paleta'] or '').border = cell_border
        ws.cell(row=row_num, column=7, value=s['dostawca'] or '').border = cell_border
        ws.cell(row=row_num, column=8, value=ilosc).border = cell_border
        ws.cell(row=row_num, column=9, value=round(cena, 2)).border = cell_border
        ws.cell(row=row_num, column=10, value=round(przychod, 2)).border = cell_border
        ws.cell(row=row_num, column=11, value=round(koszt, 2)).border = cell_border
        ws.cell(row=row_num, column=12, value=round(prowizja, 2)).border = cell_border

        zysk_cell = ws.cell(row=row_num, column=13, value=round(zysk, 2))
        zysk_cell.border = cell_border
        if zysk > 0:
            zysk_cell.fill = money_fill
        elif zysk < 0:
            zysk_cell.font = Font(color='FF0000')
        return przychod, koszt, prowizja, zysk

    def _set_detail_widths(ws):
        widths = [12, 45, 14, 12, 14, 15, 12, 8, 14, 12, 14, 14, 12]
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

    # Wypełnij arkusz "Wszystkie"
    _fill_detail_headers(ws_details)
    row = 2
    for s in sprzedaze:
        _fill_detail_row(ws_details, row, s)
        row += 1
    _set_detail_widths(ws_details)

    # ============ ARKUSZE PER MIESIĄC ============
    # Grupuj sprzedaże po miesiącach
    sprzedaze_per_month = {}
    for s in sprzedaze:
        data = s['data_sprzedazy'] or ''
        miesiac = data[:7] if data else 'Brak-daty'
        if miesiac not in sprzedaze_per_month:
            sprzedaze_per_month[miesiac] = []
        sprzedaze_per_month[miesiac].append(s)

    for miesiac in sorted(sprzedaze_per_month.keys(), reverse=True):
        sales = sprzedaze_per_month[miesiac]
        # Nazwa arkusza (max 31 znaków, bez niedozwolonych)
        sheet_name = miesiac.replace('/', '-')[:31]
        ws_month = wb.create_sheet(sheet_name)
        _fill_detail_headers(ws_month)

        row = 2
        m_przychod = 0
        m_koszt = 0
        m_prowizja = 0
        m_zysk = 0
        for s in sales:
            p, k, pr, z = _fill_detail_row(ws_month, row, s)
            m_przychod += p
            m_koszt += k
            m_prowizja += pr
            m_zysk += z
            row += 1

        # Wiersz podsumowania miesiąca
        row += 1
        suma_font_m = Font(bold=True)
        suma_fill_m = PatternFill('solid', fgColor='fef3c7')
        ws_month.cell(row=row, column=1, value='RAZEM').font = suma_font_m
        ws_month.cell(row=row, column=1).fill = suma_fill_m
        ws_month.cell(row=row, column=8, value=len(sales)).font = suma_font_m
        ws_month.cell(row=row, column=10, value=round(m_przychod, 2)).font = suma_font_m
        ws_month.cell(row=row, column=10).fill = suma_fill_m
        ws_month.cell(row=row, column=11, value=round(m_koszt, 2)).font = suma_font_m
        ws_month.cell(row=row, column=11).fill = suma_fill_m
        ws_month.cell(row=row, column=12, value=round(m_prowizja, 2)).font = suma_font_m
        ws_month.cell(row=row, column=12).fill = suma_fill_m
        ws_month.cell(row=row, column=13, value=round(m_zysk, 2)).font = suma_font_m
        ws_month.cell(row=row, column=13).fill = suma_fill_m if m_zysk >= 0 else PatternFill('solid', fgColor='fecaca')
        _set_detail_widths(ws_month)
    
    # Zapisz plik
    filename = f"raport_sprzedazy_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'cloud_exports', filename)
    
    # Upewnij się, że folder istnieje
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    # Zapisz i zamknij workbook
    wb.save(filepath)
    wb.close()
    
    # Poczekaj chwilę na zakończenie zapisu
    import time
    time.sleep(0.1)
    
    # Sprawdź czy plik istnieje
    if not os.path.exists(filepath):
        from flask import jsonify
        return jsonify({'error': f'Nie udało się utworzyć pliku: {filepath}'}), 500
    
    # Zwróć plik do pobrania
    from flask import send_file
    return send_file(filepath, as_attachment=True, download_name=filename, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@magazynier_bp.route('/raport-sprzedazy-page')
def raport_sprzedazy_page():
    """Strona z przyciskiem do pobrania raportu"""
    html = '''
    <div class="hdr"><h1>📊 Raport sprzedaży</h1></div>
    
    <div style="padding:20px;text-align:center">
        <p style="color:#94a3b8;margin-bottom:20px">
            Pobierz raport sprzedaży w formacie Excel (.xlsx)<br>
            Zawiera podsumowanie miesięczne i szczegóły wszystkich transakcji
        </p>
        
        <a href="/magazyn/raport-sprzedazy" class="btn btn-ok" style="font-size:18px;padding:15px 30px">
            📥 POBIERZ RAPORT EXCEL
        </a>
        
        <div style="margin-top:30px;text-align:left;max-width:500px;margin-left:auto;margin-right:auto">
            <h3 style="color:#22c55e">📋 Co zawiera raport:</h3>
            <ul style="color:#94a3b8;line-height:2">
                <li><b>Arkusz "Podsumowanie"</b> - przychody, koszty, zyski per miesiąc</li>
                <li><b>Arkusz "Szczegóły"</b> - wszystkie sprzedaże z datami</li>
                <li>Automatyczne obliczenie prowizji Allegro (11%)</li>
                <li>ROI dla każdego miesiąca</li>
            </ul>
        </div>
    </div>
    
    <a href="/analityka" class="back">← Powrót do Analityki</a>
    '''
    return render(html)


@magazynier_bp.route('/lezaki')
def lezaki():
    """Lista produktów stojących >30 dni z kosztem z palety i sugestią obniżki"""
    from datetime import datetime, timedelta
    
    conn = get_db()
    days_30_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    
    # Pobierz lezaki z dołączoną paletą
    produkty = conn.execute('''
        SELECT 
            p.id, p.nazwa, p.ean, p.asin, p.ilosc, p.cena_brutto, p.cena_netto,
            p.cena_allegro, p.status, p.lokalizacja, p.paleta, p.paleta_id,
            p.zdjecie_url, p.data_dodania,
            CAST(julianday('now') - julianday(p.data_dodania) AS INTEGER) as dni_stoi,
            pal.cena_zakupu as paleta_cena_zakupu,
            COALESCE((SELECT SUM(pr2.ilosc) FROM produkty pr2 WHERE pr2.paleta_id = pal.id), 0)
            + COALESCE((SELECT SUM(pr2.sprzedano_offline) FROM produkty pr2 WHERE pr2.paleta_id = pal.id), 0)
            + COALESCE((SELECT SUM(sp2.ilosc) FROM sprzedaze sp2
                JOIN produkty pp2 ON sp2.produkt_id = pp2.id
                WHERE pp2.paleta_id = pal.id
                AND sp2.status NOT IN ('zwrot','anulowane','anulowana')), 0)
            as paleta_ilosc_sztuk,
            pal.nazwa as paleta_nazwa
        FROM produkty p
        LEFT JOIN palety pal ON p.paleta_id = pal.id
        WHERE p.status IN ('magazyn', 'wystawiony')
          AND date(p.data_dodania) < ?
          AND p.ilosc > 0
        ORDER BY dni_stoi DESC
    ''', (days_30_ago,)).fetchall()
    
    
    total_wartosc = 0
    
    html = '''
    <div class="hdr"><h1>⏳ LEŻAKI</h1><small>Produkty stojące >30 dni</small></div>
    '''
    
    if not produkty:
        html += '<div class="alert alert-ok">✅ Brak leżaków — wszystko się kręci!</div>'
        return render(html)
    
    # Karty produktów
    rows_html = ''
    for p in produkty:
        dni = p['dni_stoi'] or 0
        
        # Koszt jednostkowy z palety lub fallback
        paleta_cena = p['paleta_cena_zakupu'] or 0
        paleta_szt = p['paleta_ilosc_sztuk'] or 0
        if paleta_cena > 0 and paleta_szt > 0:
            koszt_szt_brutto = paleta_cena / paleta_szt
            koszt_szt_netto = koszt_szt_brutto / 1.23
            koszt_src = f"z palety ({p['paleta_nazwa'] or '—'})"
        else:
            koszt_szt_brutto = p['cena_brutto'] or 0  # JEDNOSTKOWA
            koszt_szt_netto = p['cena_netto'] or 0          # JEDNOSTKOWA
            koszt_src = "z produktu"
        
        cena_allegro = p['cena_allegro'] or 0
        
        # Sugestia obniżki — im dłużej stoi, tym agresywniejsza
        if dni < 45:
            obnizka_pct = 10
            obnizka_kolor = '#eab308'
            obnizka_ikona = '🟡'
        elif dni < 60:
            obnizka_pct = 20
            obnizka_kolor = '#f97316'
            obnizka_ikona = '🟠'
        elif dni < 90:
            obnizka_pct = 30
            obnizka_kolor = '#ef4444'
            obnizka_ikona = '🔴'
        else:
            obnizka_pct = 40
            obnizka_kolor = '#dc2626'
            obnizka_ikona = '🚨'
        
        cena_sugerowana = round(cena_allegro * (1 - obnizka_pct / 100), 2)
        marza_po_obnizce = cena_sugerowana - koszt_szt_brutto
        
        # Kolor dni
        if dni > 90:
            dni_kolor = '#dc2626'
        elif dni > 60:
            dni_kolor = '#ef4444'
        elif dni > 45:
            dni_kolor = '#f97316'
        else:
            dni_kolor = '#eab308'
        
        # Wartość leżąca
        wartosc_lezaca = koszt_szt_brutto * (p['ilosc'] or 0)
        total_wartosc += wartosc_lezaca
        
        img_html = ''
        if p['zdjecie_url']:
            img_html = f'<img src="{p["zdjecie_url"]}" style="width:60px;height:60px;object-fit:contain;border-radius:8px;background:#0a0a0f;flex-shrink:0">'
        else:
            img_html = '<div style="width:60px;height:60px;background:#1e1e2e;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:1.5rem;flex-shrink:0">📦</div>'
        
        status_color = '#3b82f6' if p['status'] == 'wystawiony' else '#eab308'
        status_text = 'WYSTAWIONY' if p['status'] == 'wystawiony' else 'MAGAZYN'
        
        rows_html += f'''
        <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:14px;margin-bottom:10px">
            <div style="display:flex;gap:12px;align-items:flex-start">
                {img_html}
                <div style="flex:1;min-width:0">
                    <div style="font-weight:600;font-size:0.9rem;margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                        <a href="/magazyn/produkt/{p['id']}" style="color:#fff;text-decoration:none">{p['nazwa']}</a>
                    </div>
                    <div style="font-size:0.72rem;color:#64748b;margin-bottom:8px">
                        {p['lokalizacja'] or '—'} • {p['paleta'] or '—'} • {p['ilosc']} szt • <span style="color:{status_color}">{status_text}</span>
                    </div>
                    
                    <!-- Dni stoi -->
                    <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
                        <div style="background:{dni_kolor}22;border:1px solid {dni_kolor};border-radius:20px;padding:3px 12px;font-size:0.8rem;font-weight:700;color:{dni_kolor}">
                            ⏳ {dni} dni
                        </div>
                        <div style="font-size:0.7rem;color:#64748b">od {(p['data_dodania'] or '')[:10]}</div>
                    </div>
                    
                    <!-- Ceny -->
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">
                        <div style="background:#1e1e2e;border-radius:8px;padding:8px;text-align:center">
                            <div style="font-size:0.65rem;color:#64748b;margin-bottom:2px">KOSZT/szt brutto</div>
                            <div style="font-weight:700;color:#94a3b8">{koszt_szt_brutto:.2f} zł</div>
                            <div style="font-size:0.6rem;color:#475569">{koszt_src}</div>
                        </div>
                        <div style="background:#1e1e2e;border-radius:8px;padding:8px;text-align:center">
                            <div style="font-size:0.65rem;color:#64748b;margin-bottom:2px">CENA ALLEGRO</div>
                            <div style="font-weight:700;color:#22c55e">{cena_allegro:.2f} zł</div>
                            <div style="font-size:0.6rem;color:#475569">marża: {cena_allegro - koszt_szt_brutto:.2f} zł</div>
                        </div>
                    </div>
                    
                    <!-- Sugestia obniżki -->
                    <div style="background:{obnizka_kolor}15;border:1px solid {obnizka_kolor}44;border-radius:8px;padding:10px">
                        <div style="font-size:0.75rem;font-weight:600;color:{obnizka_kolor};margin-bottom:6px">
                            {obnizka_ikona} Sugerowana obniżka -{obnizka_pct}%
                        </div>
                        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px">
                            <div>
                                <div style="font-size:0.7rem;color:#94a3b8">Nowa cena: <b style="color:#fff">{cena_sugerowana:.2f} zł</b></div>
                                <div style="font-size:0.75rem;color:#cbd5e1">Marża po obniżce: <b>{marza_po_obnizce:+.2f} zł</b></div>
                            </div>
                            <a href="/magazyn/produkt/{p['id']}/edytuj" 
                               style="padding:6px 12px;background:{obnizka_kolor};border-radius:6px;color:#000;font-size:0.72rem;font-weight:700;text-decoration:none;white-space:nowrap;flex-shrink:0">
                                ✏️ Zmień cenę
                            </a>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        '''
    
    # Podsumowanie na górze
    html += f'''
    <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;margin-bottom:15px">
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;text-align:center">
            <div>
                <div style="font-size:1.4rem;font-weight:700;color:#eab308">{len(produkty)}</div>
                <div style="font-size:0.65rem;color:#64748b">PRODUKTÓW</div>
            </div>
            <div>
                <div style="font-size:1.4rem;font-weight:700;color:#ef4444">{sum(p['ilosc'] or 0 for p in produkty)}</div>
                <div style="font-size:0.65rem;color:#64748b">SZTUK</div>
            </div>
            <div>
                <div style="font-size:1.1rem;font-weight:700;color:#f97316">{total_wartosc:.0f} zł</div>
                <div style="font-size:0.65rem;color:#64748b">ZAMROŻONE</div>
            </div>
        </div>
    </div>
    
    <div style="display:flex;gap:8px;margin-bottom:15px">
        <div style="background:#eab30822;border:1px solid #eab308;border-radius:8px;padding:6px 12px;font-size:0.72rem;color:#eab308">🟡 &lt;45 dni → -10%</div>
        <div style="background:#f9731622;border:1px solid #f97316;border-radius:8px;padding:6px 12px;font-size:0.72rem;color:#f97316">🟠 &lt;60 dni → -20%</div>
        <div style="background:#ef444422;border:1px solid #ef4444;border-radius:8px;padding:6px 12px;font-size:0.72rem;color:#ef4444">🔴 &lt;90 dni → -30%</div>
        <div style="background:#dc262622;border:1px solid #dc2626;border-radius:8px;padding:6px 12px;font-size:0.72rem;color:#dc2626">🚨 90+ dni → -40%</div>
    </div>
    '''
    
    html += rows_html
    html += '<a href="/analityka" class="back">← Powrót</a>'
    
    return render(html)




@magazynier_bp.route('/koszty', methods=['GET', 'POST'])
def koszty_page():
    from .database import get_db
    conn = get_db()
    
    # Upewnij się że tabela istnieje
    conn.execute('''CREATE TABLE IF NOT EXISTS koszty (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nazwa TEXT NOT NULL,
        kwota REAL NOT NULL,
        kategoria TEXT DEFAULT 'inne',
        data DATE DEFAULT CURRENT_DATE,
        notatka TEXT DEFAULT '',
        data_dodania TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    
    msg = ''
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'dodaj':
            nazwa  = request.form.get('nazwa', '').strip()
            kwota  = float(request.form.get('kwota', 0) or 0)
            kat    = request.form.get('kategoria', 'inne')
            data   = request.form.get('data', datetime.now().strftime('%Y-%m-%d'))
            notatka = request.form.get('notatka', '').strip()
            if nazwa and kwota > 0:
                conn.execute('INSERT INTO koszty (nazwa, kwota, kategoria, data, notatka) VALUES (?,?,?,?,?)',
                    (nazwa, kwota, kat, data, notatka))
                conn.commit()
                conn.execute('PRAGMA wal_checkpoint(PASSIVE)')
                msg = f'✅ Dodano koszt: {nazwa} — {kwota:.2f} zł'
        elif action == 'usun':
            kid = request.form.get('id')
            conn.execute('DELETE FROM koszty WHERE id=?', (kid,))
            conn.commit()
            conn.execute('PRAGMA wal_checkpoint(PASSIVE)')
            msg = '🗑️ Usunięto koszt'
    
    # Pobierz wszystkie koszty
    koszty = conn.execute('SELECT * FROM koszty ORDER BY data DESC, id DESC').fetchall()
    
    # Suma per miesiąc (bieżący rok)
    year = datetime.now().year
    koszty_miesiecznie = conn.execute('''
        SELECT strftime('%m', data) as m, SUM(kwota) as suma
        FROM koszty WHERE strftime('%Y', data) = ?
        GROUP BY m
    ''', (str(year),)).fetchall()
    koszty_msc = {int(r['m']): float(r['suma']) for r in koszty_miesiecznie}
    
    # Suma per kategoria
    koszty_kat = conn.execute('''
        SELECT kategoria, SUM(kwota) as suma, COUNT(*) as cnt
        FROM koszty GROUP BY kategoria ORDER BY suma DESC
    ''').fetchall()
    
    # Suma całkowita i bieżący miesiąc
    suma_total = sum(float(k['kwota']) for k in koszty)
    biezacy_m = datetime.now().month
    suma_msc = koszty_msc.get(biezacy_m, 0)
    
    
    KATEGORIE = [
        ('allegro', '🛒 Prowizje Allegro'),
        ('wysylka', '📦 Wysyłka / InPost'),
        ('reklama', '📣 Reklama'),
        ('magazyn', '🏭 Magazyn / najem'),
        ('zakup', '💰 Zakup towaru'),
        ('ksiegowosc', '📋 Księgowość / ZUS'),
        ('inne', '⚡ Inne'),
    ]
    
    nazwy_m = ['Sty','Lut','Mar','Kwi','Maj','Cze','Lip','Sie','Wrz','Paź','Lis','Gru']
    
    # Tabela kosztów HTML
    koszty_html = ''
    for k in koszty:
        kat_label = dict(KATEGORIE).get(k['kategoria'], k['kategoria'])
        notatka_html = f' • <span style="color:#94a3b8">{k["notatka"]}</span>' if k['notatka'] else ''
        koszty_html += f'''
        <div style="display:flex;align-items:center;gap:10px;padding:10px 12px;background:#12121a;border-radius:10px;margin-bottom:6px">
            <div style="flex:1;min-width:0">
                <div style="font-weight:600;font-size:0.9rem">{k['nazwa']}</div>
                <div style="font-size:0.75rem;color:#64748b;margin-top:2px">{kat_label} • {k['data']}{notatka_html}</div>
            </div>
            <div style="font-weight:700;color:#f43f5e;white-space:nowrap">-{k['kwota']:.2f} zł</div>
            <form action="/magazyn/koszty" method="POST" style="margin:0">
                <input type="hidden" name="action" value="usun">
                <input type="hidden" name="id" value="{k['id']}">
                <button type="submit" onclick="return confirm('Usun?')" 
                    style="background:#ef444422;border:1px solid #ef444455;border-radius:6px;color:#ef4444;padding:4px 10px;cursor:pointer;font-size:0.75rem">✕</button>
            </form>
        </div>'''
    
    if not koszty_html:
        koszty_html = '<div style="text-align:center;color:#64748b;padding:30px">Brak kosztów. Dodaj pierwszy!</div>'
    
    # Opcje kategorii
    kat_options = ''.join([f'<option value="{v}">{l}</option>' for v, l in KATEGORIE])
    
    msg_html = f'<div style="background:#22c55e22;border:1px solid #22c55e55;border-radius:10px;padding:10px 15px;margin-bottom:15px;color:#22c55e">{msg}</div>' if msg else ''
    
    html = f'''
    <div class="hdr"><h1>💸 KOSZTY</h1><small>Opłaty, prowizje, wydatki operacyjne</small></div>
    
    {msg_html}
    
    <!-- Podsumowanie -->
    <div class="stats" style="margin-bottom:15px">
        <div class="stat">
            <div class="stat-v" style="color:#f43f5e">{suma_total:.0f} zł</div>
            <div class="stat-l">Łącznie (wszystkie)</div>
        </div>
        <div class="stat">
            <div class="stat-v" style="color:#f97316">{suma_msc:.0f} zł</div>
            <div class="stat-l">Ten miesiąc</div>
        </div>
        <div class="stat">
            <div class="stat-v">{len(koszty)}</div>
            <div class="stat-l">Wpisów</div>
        </div>
    </div>
    
    <!-- Formularz dodawania -->
    <div class="card" style="padding:15px;margin-bottom:15px">
        <div style="font-weight:700;margin-bottom:12px">➕ Dodaj koszt</div>
        <form action="/magazyn/koszty" method="POST">
            <input type="hidden" name="action" value="dodaj">
            <div class="form-row" style="margin-bottom:10px">
                <div class="form-group">
                    <label>Nazwa</label>
                    <input type="text" name="nazwa" class="form-ctrl" placeholder="np. Prowizja Allegro Luty" required>
                </div>
                <div class="form-group">
                    <label>Kwota (zł)</label>
                    <input type="number" name="kwota" step="0.01" class="form-ctrl" placeholder="0.00" required>
                </div>
            </div>
            <div class="form-row" style="margin-bottom:10px">
                <div class="form-group">
                    <label>Kategoria</label>
                    <select name="kategoria" class="form-ctrl">{kat_options}</select>
                </div>
                <div class="form-group">
                    <label>Data</label>
                    <input type="date" name="data" class="form-ctrl" value="{datetime.now().strftime('%Y-%m-%d')}">
                </div>
            </div>
            <div class="form-group" style="margin-bottom:10px">
                <label>Notatka (opcjonalnie)</label>
                <input type="text" name="notatka" class="form-ctrl" placeholder="np. faktura FV/2026/02/001">
            </div>
            <button type="submit" class="btn btn-ok">💾 Dodaj</button>
        </form>
    </div>
    
    <!-- Podział na kategorie -->
    {"".join([f'<div style="display:inline-flex;align-items:center;gap:6px;background:#1e1e2e;border-radius:8px;padding:6px 12px;margin:0 6px 6px 0;font-size:0.8rem"><span style="color:#f43f5e;font-weight:700">{float(r["suma"]):.0f} zł</span><span style="color:#64748b">{dict(KATEGORIE).get(r["kategoria"],r["kategoria"])}</span></div>' for r in koszty_kat]) if koszty_kat else ""}
    
    <!-- Lista kosztów -->
    <div style="margin-top:15px">
        <div style="font-weight:700;margin-bottom:10px;display:flex;justify-content:space-between">
            <span>📋 Wszystkie koszty</span>
        </div>
        {koszty_html}
    </div>
    
    <a href="/magazyn" class="back">← Powrót</a>
    '''
    return render(html)



@magazynier_bp.route('/sprzedaz-prywatna', methods=['GET', 'POST'])
def sprzedaz_prywatna_page():
    from .database import get_db
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS sprzedaze_prywatne (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        opis TEXT NOT NULL, kwota REAL NOT NULL,
        data DATE DEFAULT CURRENT_DATE, notatka TEXT DEFAULT '',
        data_dodania TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    
    msg = ''
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'dodaj':
            opis   = request.form.get('opis', '').strip()
            kwota  = float(request.form.get('kwota', 0) or 0)
            data   = request.form.get('data', datetime.now().strftime('%Y-%m-%d'))
            notatka = request.form.get('notatka', '').strip()
            if opis and kwota > 0:
                conn.execute('INSERT INTO sprzedaze_prywatne (opis, kwota, data, notatka) VALUES (?,?,?,?)',
                    (opis, kwota, data, notatka))
                conn.commit()
                conn.execute('PRAGMA wal_checkpoint(PASSIVE)')
                msg = f'✅ Dodano: {opis} — {kwota:.2f} zł'
        elif action == 'usun':
            conn.execute('DELETE FROM sprzedaze_prywatne WHERE id=?', (request.form.get('id'),))
            conn.commit()
            conn.execute('PRAGMA wal_checkpoint(PASSIVE)')
            msg = '🗑️ Usunięto'
    
    sprzedaze = conn.execute('SELECT * FROM sprzedaze_prywatne ORDER BY data DESC, id DESC').fetchall()
    
    year = datetime.now().year
    suma_rok = conn.execute(
        'SELECT COALESCE(SUM(kwota),0) FROM sprzedaze_prywatne WHERE strftime(\'%Y\', data)=?', (str(year),)
    ).fetchone()[0]
    biezacy_m = datetime.now().month
    suma_msc = conn.execute(
        'SELECT COALESCE(SUM(kwota),0) FROM sprzedaze_prywatne WHERE strftime(\'%Y-%m\', data)=?',
        (f'{year}-{biezacy_m:02d}',)
    ).fetchone()[0]
    
    msg_html = f'<div style="background:#22c55e22;border:1px solid #22c55e55;border-radius:10px;padding:10px 15px;margin-bottom:15px;color:#22c55e">{msg}</div>' if msg else ''
    
    rows_html = ''
    for s in sprzedaze:
        rows_html += f'''
        <div style="display:flex;align-items:center;gap:10px;padding:10px 12px;background:#12121a;border-radius:10px;margin-bottom:6px">
            <div style="flex:1">
                <div style="font-weight:600">{s['opis']}</div>
                <div style="font-size:0.75rem;color:#64748b">{s['data']}
                    {f' • {s["notatka"]}' if s['notatka'] else ''}
                </div>
            </div>
            <div style="font-weight:700;color:#22c55e;white-space:nowrap">+{s['kwota']:.2f} zł</div>
            <form action="/magazyn/sprzedaz-prywatna" method="POST" style="margin:0">
                <input type="hidden" name="action" value="usun">
                <input type="hidden" name="id" value="{s['id']}">
                <button type="submit" onclick="return confirm('Usuń?')"
                    style="background:#ef444422;border:1px solid #ef444455;border-radius:6px;color:#ef4444;padding:4px 10px;cursor:pointer;font-size:0.75rem">✕</button>
            </form>
        </div>'''
    
    if not rows_html:
        rows_html = '<div style="text-align:center;color:#64748b;padding:30px">Brak wpisów</div>'
    
    html = f'''
    <div class="hdr"><h1>🤝 SPRZEDAŻ PRYWATNA</h1><small>Sprzedaż poza Allegro — OLX, Facebook, cash itp.</small></div>
    {msg_html}
    <div class="stats" style="margin-bottom:15px">
        <div class="stat">
            <div class="stat-v green">{suma_rok:.0f} zł</div>
            <div class="stat-l">Rok {year}</div>
        </div>
        <div class="stat">
            <div class="stat-v green">{suma_msc:.0f} zł</div>
            <div class="stat-l">Ten miesiąc</div>
        </div>
        <div class="stat">
            <div class="stat-v">{len(sprzedaze)}</div>
            <div class="stat-l">Transakcji</div>
        </div>
    </div>
    <div class="card" style="padding:15px;margin-bottom:15px">
        <div style="font-weight:700;margin-bottom:12px">➕ Dodaj sprzedaż</div>
        <form action="/magazyn/sprzedaz-prywatna" method="POST">
            <input type="hidden" name="action" value="dodaj">
            <div class="form-row" style="margin-bottom:10px">
                <div class="form-group">
                    <label>Opis</label>
                    <input type="text" name="opis" class="form-ctrl" placeholder="np. Odkurzacz Dyson, OLX" required>
                </div>
                <div class="form-group">
                    <label>Kwota (zł)</label>
                    <input type="number" name="kwota" step="0.01" class="form-ctrl" placeholder="0.00" required>
                </div>
            </div>
            <div class="form-row" style="margin-bottom:10px">
                <div class="form-group">
                    <label>Data</label>
                    <input type="date" name="data" class="form-ctrl" value="{datetime.now().strftime('%Y-%m-%d')}">
                </div>
                <div class="form-group">
                    <label>Notatka</label>
                    <input type="text" name="notatka" class="form-ctrl" placeholder="opcjonalnie">
                </div>
            </div>
            <button type="submit" class="btn btn-ok">💾 Dodaj</button>
        </form>
    </div>
    <div style="font-weight:700;margin-bottom:10px">📋 Historia</div>
    {rows_html}
    <a href="/magazyn" class="back">← Powrót</a>
    '''
    return render(html)



@magazynier_bp.route('/remanent')
def remanent_page():
    """Strona remanentu z podglądem i przyciskiem do pobrania Excela"""
    from datetime import datetime
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')

    # Podsumowanie per paleta
    # Używa _paleta_koszt_szt() — dynamicznie liczy magazyn + offline + allegro
    palety_all = conn.execute('''
        SELECT
            pal.id, pal.nazwa, pal.dostawca, pal.cena_zakupu, pal.data_zakupu,
            COALESCE(SUM(p.ilosc), 0) as szt_magazyn,
            COALESCE(SUM(p.cena_allegro * p.ilosc), 0) as wartosc_allegro
        FROM palety pal
        LEFT JOIN produkty p ON p.paleta_id = pal.id
        GROUP BY pal.id
        ORDER BY CAST(SUBSTR(pal.nazwa, INSTR(pal.nazwa,'#')+1) AS INTEGER) DESC, pal.data_zakupu DESC
    ''').fetchall()

    # Przelicz dane per paleta
    palety_data = []
    for p in palety_all:
        cena_zak = float(p['cena_zakupu'] or 0)
        szt_magazyn = int(p['szt_magazyn'] or 0)
        # Koszt/szt z helpera (dynamicznie: magazyn + offline + allegro sprzedaże)
        koszt_szt = _paleta_koszt_szt(conn, p['id'])
        wartosc_kosztowa = koszt_szt * szt_magazyn
        # Ile sztuk było łącznie w palecie
        szt_wszystkich = round(cena_zak / koszt_szt) if koszt_szt > 0 else szt_magazyn
        szt_sprzedano = max(0, szt_wszystkich - szt_magazyn)
        wartosc_allegro = float(p['wartosc_allegro'] or 0)
        palety_data.append({
            'nazwa': p['nazwa'], 'dostawca': p['dostawca'],
            'data_zakupu': p['data_zakupu'], 'cena_zakupu': cena_zak,
            'szt_magazyn': szt_magazyn, 'szt_sprzedano': szt_sprzedano,
            'szt_wszystkich': szt_wszystkich,
            'wartosc_kosztowa': wartosc_kosztowa,
            'wartosc_allegro': wartosc_allegro,
        })

    # Sumy ogólne
    suma_zakupu = sum(p['cena_zakupu'] for p in palety_data)
    suma_kosztowa = sum(p['wartosc_kosztowa'] for p in palety_data)
    suma_allegro = sum(p['wartosc_allegro'] for p in palety_data)
    suma_magazyn = sum(p['szt_magazyn'] for p in palety_data)
    suma_sprzedano = sum(p['szt_sprzedano'] for p in palety_data)

    # Podział per kategoria (produkty w magazynie)
    kategorie = conn.execute('''
        SELECT 
            COALESCE(NULLIF(kategoria,''),'inne') as kat,
            COUNT(*) as cnt,
            SUM(ilosc) as sztuki,
            COALESCE(SUM(cena_netto), 0) as wartosc_netto,
            COALESCE(SUM(cena_allegro * ilosc), 0) as wartosc_detal
        FROM produkty
        WHERE status != 'sprzedany'
        GROUP BY kat
        ORDER BY wartosc_detal DESC
    ''').fetchall()

    # Build HTML
    rows_palety = ''
    for p in palety_data:
        cena_zak = p['cena_zakupu']
        w_koszt = p['wartosc_kosztowa']
        w_allegro = p['wartosc_allegro']
        szt_mag = p['szt_magazyn']
        szt_sprz = p['szt_sprzedano']
        szt_all = p['szt_wszystkich']
        # ROI potencjalny: (wartość Allegro - koszt pozostałych) / koszt pozostałych
        roi = ((w_allegro - w_koszt) / w_koszt * 100) if w_koszt > 0 else (100 if w_allegro > 0 else -100)
        roi_kolor = '#22c55e' if roi > 50 else ('#f59e0b' if roi > 0 else '#ef4444')
        progress = (szt_sprz / szt_all * 100) if szt_all > 0 else 0
        rows_palety += f'''
        <tr>
            <td style="padding:8px 10px;border-bottom:1px solid #1e293b">{p['nazwa'] or '-'}</td>
            <td style="padding:8px 10px;border-bottom:1px solid #1e293b;color:#64748b;font-size:0.8rem">{p['dostawca'] or '-'}</td>
            <td style="padding:8px 10px;border-bottom:1px solid #1e293b;color:#64748b;font-size:0.8rem">{p['data_zakupu'] or '-'}</td>
            <td style="padding:8px 10px;border-bottom:1px solid #1e293b;text-align:right">{cena_zak:.0f} zł</td>
            <td style="padding:8px 10px;border-bottom:1px solid #1e293b;text-align:center">{szt_sprz}/{szt_all} szt</td>
            <td style="padding:8px 10px;border-bottom:1px solid #1e293b;text-align:center;color:#3b82f6;font-weight:600">{szt_mag} szt</td>
            <td style="padding:8px 10px;border-bottom:1px solid #1e293b;text-align:right;color:#94a3b8">{w_koszt:.0f} zł</td>
            <td style="padding:8px 10px;border-bottom:1px solid #1e293b;text-align:right;color:#22c55e">{w_allegro:.0f} zł</td>
            <td style="padding:8px 10px;border-bottom:1px solid #1e293b;text-align:right;font-weight:700;color:{roi_kolor}">{roi:.0f}%</td>
        </tr>'''

    rows_kat = ''
    for k in kategorie:
        rows_kat += f'''
        <tr>
            <td style="padding:7px 10px;border-bottom:1px solid #1e293b">{k['kat']}</td>
            <td style="padding:7px 10px;border-bottom:1px solid #1e293b;text-align:center">{k['cnt']}</td>
            <td style="padding:7px 10px;border-bottom:1px solid #1e293b;text-align:center">{k['sztuki'] or 0}</td>
            <td style="padding:7px 10px;border-bottom:1px solid #1e293b;text-align:right;color:#94a3b8">{float(k['wartosc_netto'] or 0):.0f} zł</td>
            <td style="padding:7px 10px;border-bottom:1px solid #1e293b;text-align:right;color:#22c55e">{float(k['wartosc_detal'] or 0):.0f} zł</td>
        </tr>'''

    html = f'''
    <div class="hdr"><h1>📋 REMANENT</h1><small>Stan magazynu na {today}</small></div>

    <!-- Kafelki podsumowania -->
    <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:15px">
        <div class="card" style="padding:12px;text-align:center">
            <div style="font-size:1.3rem;font-weight:700;color:#3b82f6">{len(palety_all)}</div>
            <div style="font-size:0.7rem;color:#64748b;margin-top:3px">Palet</div>
        </div>
        <div class="card" style="padding:12px;text-align:center">
            <div style="font-size:1.3rem;font-weight:700">{suma_magazyn}</div>
            <div style="font-size:0.7rem;color:#64748b;margin-top:3px">Szt. w magazynie</div>
        </div>
        <div class="card" style="padding:12px;text-align:center">
            <div style="font-size:1.3rem;font-weight:700;color:#f43f5e">{suma_zakupu:.0f} zł</div>
            <div style="font-size:0.7rem;color:#64748b;margin-top:3px">Koszt zakupu palet</div>
        </div>
        <div class="card" style="padding:12px;text-align:center">
            <div style="font-size:1.3rem;font-weight:700;color:#94a3b8">{suma_kosztowa:.0f} zł</div>
            <div style="font-size:0.7rem;color:#64748b;margin-top:3px">Wart. kosztowa mag.</div>
        </div>
        <div class="card" style="padding:12px;text-align:center">
            <div style="font-size:1.3rem;font-weight:700;color:#22c55e">{suma_allegro:.0f} zł</div>
            <div style="font-size:0.7rem;color:#64748b;margin-top:3px">Wart. sprzedażowa</div>
        </div>
    </div>

    <!-- Przycisk Excel -->
    <div style="text-align:right;margin-bottom:15px">
        <a href="/magazyn/remanent/excel" style="display:inline-flex;align-items:center;gap:8px;padding:10px 20px;background:#22c55e;border-radius:8px;color:#fff;text-decoration:none;font-weight:700;font-size:0.9rem">
            📥 Pobierz Excel
        </a>
    </div>

    <!-- Tabela palet -->
    <div class="card" style="padding:0;margin-bottom:15px;overflow:hidden">
        <div style="padding:12px 15px;background:#0f2235;font-weight:700;font-size:0.85rem">📦 Stan per paleta</div>
        <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:0.82rem">
            <thead>
                <tr style="background:#0f1a2a;color:#64748b;font-size:0.75rem">
                    <th style="padding:8px 10px;text-align:left">Paleta</th>
                    <th style="padding:8px 10px;text-align:left">Dostawca</th>
                    <th style="padding:8px 10px;text-align:left">Data zakupu</th>
                    <th style="padding:8px 10px;text-align:right">Koszt zakupu</th>
                    <th style="padding:8px 10px;text-align:center">Sprzedane</th>
                    <th style="padding:8px 10px;text-align:center">Pozostało</th>
                    <th style="padding:8px 10px;text-align:right">Wart. koszt.</th>
                    <th style="padding:8px 10px;text-align:right">Wart. Allegro</th>
                    <th style="padding:8px 10px;text-align:right">ROI</th>
                </tr>
            </thead>
            <tbody>{rows_palety}</tbody>
            <tfoot>
                <tr style="background:#0f1a2a;font-weight:700">
                    <td colspan="3" style="padding:8px 10px;color:#94a3b8">RAZEM</td>
                    <td style="padding:8px 10px;text-align:right;color:#f43f5e">{suma_zakupu:.0f} zł</td>
                    <td style="padding:8px 10px;text-align:center;color:#64748b">{suma_sprzedano} sprz.</td>
                    <td style="padding:8px 10px;text-align:center;color:#3b82f6">{suma_magazyn} szt</td>
                    <td style="padding:8px 10px;text-align:right;color:#94a3b8">{suma_kosztowa:.0f} zł</td>
                    <td style="padding:8px 10px;text-align:right;color:#22c55e">{suma_allegro:.0f} zł</td>
                    <td></td>
                </tr>
            </tfoot>
        </table>
        </div>
    </div>

    <!-- Tabela kategorii -->
    <div class="card" style="padding:0;overflow:hidden">
        <div style="padding:12px 15px;background:#0f2235;font-weight:700;font-size:0.85rem">🏷️ Stan per kategoria</div>
        <table style="width:100%;border-collapse:collapse;font-size:0.82rem">
            <thead>
                <tr style="background:#0f1a2a;color:#64748b;font-size:0.75rem">
                    <th style="padding:7px 10px;text-align:left">Kategoria</th>
                    <th style="padding:7px 10px;text-align:center">Produktów</th>
                    <th style="padding:7px 10px;text-align:center">Sztuk</th>
                    <th style="padding:7px 10px;text-align:right">Wart. netto</th>
                    <th style="padding:7px 10px;text-align:right">Wart. detal.</th>
                </tr>
            </thead>
            <tbody>{rows_kat}</tbody>
        </table>
    </div>

    <a href="/magazyn" class="back" style="margin-top:15px;display:inline-block">← Powrót</a>
    '''
    return render(html)


@magazynier_bp.route('/remanent/excel')
def remanent_excel():
    """Generuje remanent magazynowy - stan na dziś w formacie Excel"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from datetime import datetime
    import io
    from flask import send_file
    
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Pobierz wszystkie produkty w magazynie (nie sprzedane)
    produkty = conn.execute('''
        SELECT 
            p.id,
            p.nazwa,
            p.kategoria,
            p.ilosc,
            p.cena_netto,
            p.cena_allegro,
            p.status,
            p.stan,
            p.regal,
            p.ean,
            p.asin,
            p.data_dodania,
            pal.nazwa as paleta_nazwa,
            pal.cena_zakupu as paleta_cena_zakupu,
            pal.dostawca,
            pal.data_zakupu,
            COALESCE(
                (SELECT COUNT(*) FROM sprzedaze s WHERE s.produkt_id = p.id AND s.status NOT IN ('zwrot','anulowana')),
                0
            ) as ilosc_sprzedanych
        FROM produkty p
        LEFT JOIN palety pal ON p.paleta_id = pal.id
        WHERE p.status NOT IN ('sprzedany')
        ORDER BY CAST(SUBSTR(pal.nazwa, INSTR(pal.nazwa,'#')+1) AS INTEGER) DESC, pal.nazwa, p.kategoria, p.nazwa
    ''').fetchall()
    
    # Podsumowanie
    palety_all = conn.execute('''
        SELECT 
            pal.id,
            pal.nazwa,
            pal.dostawca,
            pal.cena_zakupu,
            pal.data_zakupu,
            COUNT(p.id) as ilosc_produktow,
            COUNT(CASE WHEN p.status = 'sprzedany' THEN 1 END) as sprzedanych,
            COUNT(CASE WHEN p.status != 'sprzedany' THEN 1 END) as pozostalo,
            COALESCE(SUM(CASE WHEN p.status != 'sprzedany' THEN p.cena_allegro * p.ilosc ELSE 0 END), 0) as wartosc_detaliczna,
            COALESCE(SUM(CASE WHEN p.status != 'sprzedany' THEN p.cena_netto * p.ilosc ELSE 0 END), 0) as wartosc_netto
        FROM palety pal
        LEFT JOIN produkty p ON p.paleta_id = pal.id
        GROUP BY pal.id
        ORDER BY CAST(SUBSTR(pal.nazwa, INSTR(pal.nazwa,'#')+1) AS INTEGER) DESC, pal.data_zakupu DESC
    ''').fetchall()
    
    
    # === TWORZENIE EXCELA ===
    wb = Workbook()
    
    # Kolory
    COL_NAGLOWEK = "1e3a5f"
    COL_SUBNAGLOWEK = "2d5a8e" 
    COL_ZIELONY = "e8f5e9"
    COL_ZOLTY = "fff9c4"
    COL_CZERWONY = "ffebee"
    COL_SZARY = "f5f5f5"
    COL_BIALY = "ffffff"
    
    def header_style(cell, bg=COL_NAGLOWEK, bold=True, color="FFFFFF", size=11, center=True):
        cell.font = Font(bold=bold, color=color, size=size, name="Arial")
        cell.fill = PatternFill("solid", start_color=bg)
        if center:
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        else:
            cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    
    def data_style(cell, bg=COL_BIALY, bold=False, color="000000", center=False, num_format=None):
        cell.font = Font(bold=bold, color=color, size=10, name="Arial")
        cell.fill = PatternFill("solid", start_color=bg)
        cell.alignment = Alignment(horizontal="center" if center else "left", vertical="center", wrap_text=True)
        if num_format:
            cell.number_format = num_format
    
    thin = Side(style="thin", color="CCCCCC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    
    def set_border(cell):
        cell.border = border
    
    # ============================================================
    # ARKUSZ 1: REMANENT SZCZEGÓŁOWY
    # ============================================================
    ws1 = wb.active
    ws1.title = "Remanent szczegółowy"
    ws1.freeze_panes = "A3"
    
    # Tytuł
    ws1.merge_cells("A1:O1")
    c = ws1["A1"]
    c.value = f"REMANENT MAGAZYNOWY — STAN NA DZIEŃ {today}"
    header_style(c, bg=COL_NAGLOWEK, size=13)
    ws1.row_dimensions[1].height = 30
    
    # Nagłówki kolumn
    headers = [
        "Lp.", "Paleta", "Dostawca", "Nazwa produktu", "Kategoria",
        "Stan", "Status", "Ilosc w mag.", "Cena netto (zl)",
        "Cena Allegro (zl)", "Wartosc netto (zl)", "Wartosc detal. (zl)",
        "Regal", "EAN", "Data zakupu palety"
    ]
    for col, h in enumerate(headers, 1):
        c = ws1.cell(row=2, column=col, value=h)
        header_style(c, bg=COL_SUBNAGLOWEK, size=10)
        set_border(c)
    ws1.row_dimensions[2].height = 35
    
    # Szerokości kolumn
    widths = [5, 20, 18, 40, 18, 12, 14, 8, 12, 12, 14, 14, 10, 16, 18]
    for i, w in enumerate(widths, 1):
        ws1.column_dimensions[get_column_letter(i)].width = w
    
    # Dane
    row = 3
    for lp, p in enumerate(produkty, 1):
        ilosc = p["ilosc"] or 1
        cena_netto_szt = p["cena_netto"] or 0  # JEDNOSTKOWA - nie dzielić przez ilosc
        cena_allegro = p["cena_allegro"] or 0
        
        # Kolor wiersza
        if p["status"] == "wystawiony":
            bg = COL_ZIELONY
        elif p["status"] == "uszkodzony":
            bg = COL_CZERWONY
        else:
            bg = COL_BIALY if lp % 2 == 0 else COL_SZARY
        
        dane = [
            lp,
            p["paleta_nazwa"] or "-",
            p["dostawca"] or "-",
            p["nazwa"] or "-",
            p["kategoria"] or "-",
            p["stan"] or "-",
            p["status"] or "-",
            ilosc,
            cena_netto_szt,      # cena jednostkowa netto
            cena_allegro,
            f"=H{row}*I{row}",  # wartość netto = ilosc × cena_netto_szt
            f"=H{row}*J{row}",  # wartość detal = ilosc × cena_allegro
            p["regal"] or "-",
            p["ean"] or "-",
            p["data_zakupu"] or "-",
        ]
        
        for col, val in enumerate(dane, 1):
            c = ws1.cell(row=row, column=col, value=val)
            data_style(c, bg=bg, center=(col in [1, 7, 8, 9, 10, 11, 12, 13]))
            set_border(c)
            if col in [9, 10, 11, 12]:
                c.number_format = '#,##0.00'
        row += 1
    
    # Wiersz podsumowania
    ws1.merge_cells(f"A{row}:G{row}")
    c = ws1.cell(row=row, column=1, value="RAZEM")
    header_style(c, bg=COL_NAGLOWEK, size=11)
    set_border(c)
    
    for col in [8, 11, 12]:
        c = ws1.cell(row=row, column=col, value=f"=SUM({get_column_letter(col)}3:{get_column_letter(col)}{row-1})")
        header_style(c, bg=COL_NAGLOWEK, size=11)
        set_border(c)
        if col in [11, 12]:
                c.number_format = '#,##0.00'
    
    # ============================================================
    # ARKUSZ 2: PODSUMOWANIE PER PALETA
    # ============================================================
    ws2 = wb.create_sheet("Podsumowanie palet")
    ws2.freeze_panes = "A3"
    
    ws2.merge_cells("A1:J1")
    c = ws2["A1"]
    c.value = f"PODSUMOWANIE PER PALETA — {today}"
    header_style(c, bg=COL_NAGLOWEK, size=13)
    ws2.row_dimensions[1].height = 30
    
    headers2 = [
        "Lp.", "Paleta", "Dostawca", "Data zakupu",
        "Koszt zakupu palety (zl)", "Produktow lacznie",
        "Sprzedanych", "Pozostalo w mag.",
        "Wartosc netto w mag. (zl)", "Wartosc detal. w mag. (zl)"
    ]
    for col, h in enumerate(headers2, 1):
        c = ws2.cell(row=2, column=col, value=h)
        header_style(c, bg=COL_SUBNAGLOWEK, size=10)
        set_border(c)
    ws2.row_dimensions[2].height = 35
    
    widths2 = [5, 25, 18, 14, 16, 12, 12, 12, 18, 18]
    for i, w in enumerate(widths2, 1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    
    row2 = 3
    for lp, pal in enumerate(palety_all, 1):
        bg = COL_BIALY if lp % 2 == 0 else COL_SZARY
        dane2 = [
            lp,
            pal["nazwa"] or "-",
            pal["dostawca"] or "-",
            pal["data_zakupu"] or "-",
            pal["cena_zakupu"] or 0,
            pal["ilosc_produktow"] or 0,
            pal["sprzedanych"] or 0,
            pal["pozostalo"] or 0,
            pal["wartosc_netto"] or 0,
            pal["wartosc_detaliczna"] or 0,
        ]
        for col, val in enumerate(dane2, 1):
            c = ws2.cell(row=row2, column=col, value=val)
            data_style(c, bg=bg, center=(col in [1, 5, 6, 7, 8, 9, 10]))
            set_border(c)
            if col in [5, 9, 10]:
                c.number_format = '#,##0.00'
        row2 += 1
    
    # Podsumowanie
    ws2.merge_cells(f"A{row2}:D{row2}")
    c = ws2.cell(row=row2, column=1, value="RAZEM")
    header_style(c, bg=COL_NAGLOWEK)
    set_border(c)
    for col in [5, 6, 7, 8, 9, 10]:
        c = ws2.cell(row=row2, column=col, value=f"=SUM({get_column_letter(col)}3:{get_column_letter(col)}{row2-1})")
        header_style(c, bg=COL_NAGLOWEK)
        set_border(c)
        if col in [5, 9, 10]:
                c.number_format = '#,##0.00'
    
    # ============================================================
    # ARKUSZ 3: PODSUMOWANIE KATEGORII
    # ============================================================
    ws3 = wb.create_sheet("Wg kategorii")
    ws3.freeze_panes = "A3"
    
    conn2 = get_db()
    kategorie = conn2.execute('''
        SELECT 
            COALESCE(kategoria, 'Brak kategorii') as kategoria,
            COUNT(*) as ilosc_prod,
            SUM(ilosc) as ilosc_szt,
            COALESCE(SUM(cena_netto), 0) as wartosc_netto,
            COALESCE(SUM(cena_allegro * ilosc), 0) as wartosc_detal
        FROM produkty
        WHERE status NOT IN ('sprzedany')
        GROUP BY kategoria
        ORDER BY wartosc_detal DESC
    ''').fetchall()

    ws3.merge_cells("A1:F1")
    c = ws3["A1"]
    c.value = f"STAN MAGAZYNU WG KATEGORII — {today}"
    header_style(c, bg=COL_NAGLOWEK, size=13)
    ws3.row_dimensions[1].height = 30
    
    headers3 = ["Lp.", "Kategoria", "Ilosc produktow", "Ilosc sztuk", "Wartosc netto (zl)", "Wartosc detal. (zl)"]




    for col, h in enumerate(headers3, 1):
        c = ws3.cell(row=2, column=col, value=h)
        header_style(c, bg=COL_SUBNAGLOWEK, size=10)
        set_border(c)
    ws3.row_dimensions[2].height = 35
    
    widths3 = [5, 30, 14, 12, 18, 18]
    for i, w in enumerate(widths3, 1):
        ws3.column_dimensions[get_column_letter(i)].width = w
    
    row3 = 3
    for lp, kat in enumerate(kategorie, 1):
        bg = COL_BIALY if lp % 2 == 0 else COL_SZARY
        for col, val in enumerate([lp, kat["kategoria"], kat["ilosc_prod"], kat["ilosc_szt"], kat["wartosc_netto"], kat["wartosc_detal"]], 1):
            c = ws3.cell(row=row3, column=col, value=val)
            data_style(c, bg=bg, center=(col != 2))
            set_border(c)
            if col in [5, 6]:
                c.number_format = '#,##0.00'
        row3 += 1
    
    # Podsumowanie
    ws3.merge_cells(f"A{row3}:B{row3}")
    c = ws3.cell(row=row3, column=1, value="RAZEM")
    header_style(c, bg=COL_NAGLOWEK)
    set_border(c)
    for col in [3, 4, 5, 6]:
        c = ws3.cell(row=row3, column=col, value=f"=SUM({get_column_letter(col)}3:{get_column_letter(col)}{row3-1})")
        header_style(c, bg=COL_NAGLOWEK)
        set_border(c)
        if col in [5, 6]:
                c.number_format = '#,##0.00'
    
    # Zapisz do bufora
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    
    filename = f"remanent_{today}.xlsx"
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@magazynier_bp.route('/statystyki-zakupow')
def statystyki_zakupow():
    """Statystyki zakupów: podział per dostawca, miesięczny, z wykresem kołowym"""
    import json
    from datetime import datetime

    conn = get_db()

    # Zakupy per dostawca — sztuki z SUM produktów (ilosc_sztuk może być puste)
    per_dostawca = conn.execute('''
        SELECT 
            COALESCE(NULLIF(TRIM(p.dostawca), ''), 'Brak dostawcy') as dostawca,
            COUNT(DISTINCT p.id) as palety_cnt,
            COALESCE(SUM(p.cena_zakupu), 0) as suma_brutto,
            COALESCE(SUM(p.ilosc_produktow), 0) as produkty_cnt,
            COALESCE(
                NULLIF(SUM(p.ilosc_sztuk), 0),
                (SELECT COALESCE(SUM(pr.ilosc), 0) FROM produkty pr WHERE pr.paleta_id = p.id)
            ) as sztuki_cnt
        FROM palety p
        GROUP BY COALESCE(NULLIF(TRIM(p.dostawca), ''), 'Brak dostawcy')
        ORDER BY suma_brutto DESC
    ''').fetchall()

    # Zakupy per miesiąc i dostawca — sztuki z produktów jeśli ilosc_sztuk puste
    per_miesiac = conn.execute('''
        SELECT 
            strftime('%Y-%m', p.data_zakupu) as miesiac,
            COALESCE(NULLIF(TRIM(p.dostawca), ''), 'Brak dostawcy') as dostawca,
            COUNT(*) as palety_cnt,
            COALESCE(SUM(p.cena_zakupu), 0) as suma_brutto,
            COALESCE(
                NULLIF(SUM(p.ilosc_sztuk), 0),
                (SELECT COALESCE(SUM(pr.ilosc), 0) FROM produkty pr WHERE pr.paleta_id = p.id)
            ) as sztuki_cnt
        FROM palety p
        WHERE p.data_zakupu IS NOT NULL
        GROUP BY miesiac, dostawca
        ORDER BY miesiac DESC, suma_brutto DESC
    ''').fetchall()

    # Top 10 najdroższych palet — sztuki fallback z produktów
    top_palety = conn.execute('''
        SELECT 
            p.nazwa, p.dostawca, p.cena_zakupu, p.data_zakupu,
            COALESCE(
                NULLIF(p.ilosc_sztuk, 0),
                (SELECT COALESCE(SUM(pr.ilosc), 0) FROM produkty pr WHERE pr.paleta_id = p.id)
            ) as sztuki_cnt
        FROM palety p
        ORDER BY p.cena_zakupu DESC
        LIMIT 10
    ''').fetchall()


    # Dane do wykresu kołowego
    dostawcy_labels = [r['dostawca'] for r in per_dostawca]
    dostawcy_wartosci = [round(r['suma_brutto'], 2) for r in per_dostawca]
    total_zakup = sum(dostawcy_wartosci)

    # Kolory dla wykresu
    COLORS = ['#3b82f6','#22c55e','#f59e0b','#ef4444','#8b5cf6','#06b6d4','#f97316','#ec4899','#14b8a6','#a855f7']

    # Grupuj per_miesiac w słownik
    miesiace_dict = {}
    for r in per_miesiac:
        m = r['miesiac'] or 'Brak daty'
        if m not in miesiace_dict:
            miesiace_dict[m] = []
        miesiace_dict[m].append(dict(r))

    # HTML wierszy miesięcznych
    miesiace_html = ''
    for miesiac in sorted(miesiace_dict.keys(), reverse=True):
        rows = miesiace_dict[miesiac]
        suma_m = sum(r['suma_brutto'] for r in rows)
        sztuki_m = sum(r['sztuki_cnt'] for r in rows)

        try:
            dt = datetime.strptime(miesiac, '%Y-%m')
            miesiac_label = dt.strftime('%B %Y').capitalize()
        except:
            miesiac_label = miesiac

        rows_html = ''
        for r in rows:
            pct = (r['suma_brutto'] / suma_m * 100) if suma_m > 0 else 0
            rows_html += f'''
            <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #1e1e2e">
                <div style="font-size:0.8rem;color:#94a3b8">{r['dostawca']}</div>
                <div style="display:flex;gap:12px;align-items:center">
                    <div style="font-size:0.7rem;color:#64748b">{r['sztuki_cnt']} szt | {r['palety_cnt']} palet</div>
                    <div style="font-size:0.85rem;font-weight:600;color:#22c55e">{r['suma_brutto']:.0f} zł</div>
                    <div style="font-size:0.7rem;color:#f59e0b;width:40px;text-align:right">{pct:.0f}%</div>
                </div>
            </div>'''

        miesiace_html += f'''
        <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:14px;margin-bottom:10px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                <div style="font-weight:700;color:#fff">{miesiac_label}</div>
                <div style="font-size:0.85rem;color:#22c55e;font-weight:600">{suma_m:.0f} zł | {sztuki_m} szt</div>
            </div>
            {rows_html}
        </div>'''

    # Top palety HTML
    top_html = ''
    for i, p in enumerate(top_palety):
        top_html += f'''
        <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #1e1e2e">
            <div style="width:24px;height:24px;background:#1e1e2e;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:0.7rem;font-weight:700;color:#64748b;flex-shrink:0">#{i+1}</div>
            <div style="flex:1;min-width:0">
                <div style="font-size:0.8rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{(p['nazwa'] or '—')[:35]}</div>
                <div style="font-size:0.7rem;color:#64748b">{p['dostawca'] or '—'} • {(p['data_zakupu'] or '')[:7]} • {p['sztuki_cnt'] or 0} szt</div>
            </div>
            <div style="font-weight:700;color:#f59e0b;white-space:nowrap">{p['cena_zakupu'] or 0:.0f} zł</div>
        </div>'''

    html = f'''
    <div class="hdr"><h1>📊 STATYSTYKI ZAKUPÓW</h1></div>

    <!-- PODSUMOWANIE -->
    <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;margin-bottom:15px">
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;text-align:center">
            <div>
                <div style="font-size:1.3rem;font-weight:700;color:#3b82f6">{len(per_dostawca)}</div>
                <div style="font-size:0.65rem;color:#64748b">DOSTAWCÓW</div>
            </div>
            <div>
                <div style="font-size:1.1rem;font-weight:700;color:#22c55e">{total_zakup:.0f} zł</div>
                <div style="font-size:0.65rem;color:#64748b">ŁĄCZNIE ZAKUP</div>
            </div>
            <div>
                <div style="font-size:1.3rem;font-weight:700;color:#f59e0b">{sum(r['sztuki_cnt'] or 0 for r in per_dostawca):,}</div>
                <div style="font-size:0.65rem;color:#64748b">SZTUK ŁĄCZNIE</div>
            </div>
        </div>
    </div>

    <!-- WYKRES KOŁOWY -->
    <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;margin-bottom:15px">
        <div style="font-weight:700;color:#fff;margin-bottom:15px">🥧 Podział zakupów per dostawca</div>
        <div style="display:flex;flex-direction:column;align-items:center">
            <canvas id="pieChart" width="280" height="280"></canvas>
            <div id="pie-legend" style="margin-top:15px;width:100%"></div>
        </div>
    </div>

    <!-- TOP 10 PALET -->
    <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;margin-bottom:15px">
        <div style="font-weight:700;color:#fff;margin-bottom:12px">🏆 Top 10 najdroższych palet</div>
        {top_html}
    </div>

    <!-- PER MIESIĄC -->
    <div style="font-weight:700;color:#fff;margin-bottom:10px;padding:0 4px">📅 Zakupy per miesiąc</div>
    {miesiace_html}

    <a href="/magazyn" class="back">← Powrót do Magazynu</a>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
    <script>
    const labels = {json.dumps(dostawcy_labels)};
    const values = {json.dumps(dostawcy_wartosci)};
    const colors = {json.dumps((COLORS * 5)[:len(dostawcy_labels)])};
    const total = values.reduce((a, b) => a + b, 0);

    const ctx = document.getElementById('pieChart').getContext('2d');
    new Chart(ctx, {{
        type: 'doughnut',
        data: {{
            labels: labels,
            datasets: [{{
                data: values,
                backgroundColor: colors,
                borderColor: '#0a0a0f',
                borderWidth: 3,
                hoverOffset: 8
            }}]
        }},
        options: {{
            responsive: false,
            cutout: '55%',
            plugins: {{
                legend: {{ display: false }},
                tooltip: {{
                    callbacks: {{
                        label: (ctx) => ` ${{ctx.label}}: ${{ctx.parsed.toLocaleString('pl-PL')}} zł (${{(ctx.parsed/total*100).toFixed(1)}}%)`
                    }}
                }}
            }}
        }}
    }});

    // Legenda
    const legend = document.getElementById('pie-legend');
    labels.forEach((l, i) => {{
        const pct = total > 0 ? (values[i]/total*100).toFixed(1) : 0;
        legend.innerHTML += `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #1e1e2e">
            <div style="display:flex;align-items:center;gap:8px">
                <div style="width:12px;height:12px;border-radius:3px;background:${{colors[i]}};flex-shrink:0"></div>
                <div style="font-size:0.8rem">${{l}}</div>
            </div>
            <div style="display:flex;gap:10px;align-items:center">
                <div style="font-size:0.75rem;color:#64748b">${{pct}}%</div>
                <div style="font-size:0.85rem;font-weight:600;color:#22c55e">${{values[i].toLocaleString('pl-PL')}} zł</div>
            </div>
        </div>`;
    }});
    </script>
    '''
    return render(html)
