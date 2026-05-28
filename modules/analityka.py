"""
Modul analityki -- routes dla /analityka/*, /statystyki
"""
from flask import Blueprint, request, redirect, session, flash, jsonify, Response, current_app
from flask_wtf.csrf import generate_csrf
from datetime import datetime
import os

analityka_bp = Blueprint('analityka', __name__)


def render(content, page_title='Statystyki'):
    from flask import render_template_string, session, current_app
    template = """{% extends "base.html" %}
{% block page_title %}""" + page_title + """{% endblock %}
{% block content %}
{{ content|safe }}
{% endblock %}"""
    return render_template_string(template,
        content=content,
        version=current_app.config.get('VERSION',''),
        brand_name=current_app.config.get('BRAND_NAME','Akces Hub'),
        current_user=session.get('username'))


@analityka_bp.route('/statystyki')
def statystyki():
    from modules.database import get_full_stats, get_palety_list, get_db
    import json

    stats = get_full_stats()

    # Pobierz dane miesięczne do wykresu (przychód bez zwrotów, spójne z widokiem szczegółowym)
    current_year = datetime.now().year
    conn = get_db()
    miesieczne = conn.execute('''
        SELECT strftime('%m', REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) as miesiac,
               COALESCE(SUM(cena * ilosc), 0) as suma,
               COUNT(*) as cnt
        FROM sprzedaze
        WHERE strftime('%Y', REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) = ?
          AND data_sprzedazy IS NOT NULL AND data_sprzedazy != ''
          AND status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')
         
        GROUP BY miesiac
        HAVING miesiac IS NOT NULL
        ORDER BY miesiac
    ''', (str(current_year),)).fetchall()

    # Dodaj sprzedaż prywatną (z tabeli sprzedaze_prywatne) — spójne z widokiem szczegółowym
    try:
        pryw_miesieczne = conn.execute('''
            SELECT strftime('%m', data) as miesiac, COALESCE(SUM(kwota), 0) as suma
            FROM sprzedaze_prywatne
            WHERE strftime('%Y', data) = ?
            GROUP BY miesiac
        ''', (str(current_year),)).fetchall()
    except:
        pryw_miesieczne = []

    nazwy_miesiecy = ['Sty', 'Lut', 'Mar', 'Kwi', 'Maj', 'Cze', 'Lip', 'Sie', 'Wrz', 'Paz', 'Lis', 'Gru']
    dane_miesieczne = [0] * 12
    dane_zamowienia = [0] * 12
    for m in miesieczne:
        if m['miesiac'] is None:
            continue
        idx = int(m['miesiac']) - 1
        dane_miesieczne[idx] = float(m['suma'] or 0)
        dane_zamowienia[idx] = int(m['cnt'] or 0)
    # Dodaj prywatne do słupków
    for m in pryw_miesieczne:
        if m['miesiac'] is None:
            continue
        idx = int(m['miesiac']) - 1
        dane_miesieczne[idx] += float(m['suma'] or 0)

    chart_labels = json.dumps(nazwy_miesiecy)
    chart_data = json.dumps(dane_miesieczne)
    chart_orders = json.dumps(dane_zamowienia)

    # === WYLICZNIK CZASU OSZCZĘDZONEGO ===
    # Każda operacja ma szacowany czas ręcznego wykonania
    time_data = {}
    try:
        # Etykiety wygenerowane (status wyslana/nadana)
        etykiety = conn.execute(
            "SELECT COUNT(*) as cnt FROM sprzedaze WHERE status IN ('wyslana','nadana')"
        ).fetchone()['cnt'] or 0

        # Oferty wystawione na Allegro
        oferty_cnt = conn.execute("SELECT COUNT(*) as cnt FROM oferty").fetchone()['cnt'] or 0

        # Produkty dodane do magazynu
        produkty_cnt = conn.execute("SELECT COUNT(*) as cnt FROM produkty").fetchone()['cnt'] or 0

        # Produkty z analizą AI (mają wypełniony meta_title)
        ai_analizy = conn.execute(
            "SELECT COUNT(*) as cnt FROM produkty WHERE meta_title IS NOT NULL AND meta_title != '' AND LENGTH(meta_title) > 3"
        ).fetchone()['cnt'] or 0

        # Miesiące aktywne (miesiące ze sprzedażą)
        miesiace_aktywne = conn.execute(
            "SELECT COUNT(DISTINCT strftime('%Y-%m', REPLACE(SUBSTR(data_sprzedazy,1,10),'T',''))) as cnt FROM sprzedaze WHERE data_sprzedazy IS NOT NULL AND data_sprzedazy != ''"
        ).fetchone()['cnt'] or 0

        # Palety przetworzone
        palety_cnt = conn.execute("SELECT COUNT(*) as cnt FROM palety").fetchone()['cnt'] or 0

        # Czas oszczędzony w minutach
        MIN_ETYKIETA = 4       # ręczne nadanie przez portal kuriera
        MIN_OFERTA = 8         # ręczne wystawienie (tytuł, opis, kategoria, zdjęcia)
        MIN_PRODUKT = 2        # ręczne wpisanie do Excela/bazy
        MIN_AI_ANALIZA = 12    # ręczne sprawdzenie ceny na Amazon/Allegro
        MIN_RAPORT = 90        # miesięczny raport P&L w Excelu
        MIN_PALETA = 30        # ręczna inwentaryzacja palety

        min_etykiety = etykiety * MIN_ETYKIETA
        min_oferty = oferty_cnt * MIN_OFERTA
        min_produkty = produkty_cnt * MIN_PRODUKT
        min_ai = ai_analizy * MIN_AI_ANALIZA
        min_raporty = miesiace_aktywne * MIN_RAPORT
        min_palety = palety_cnt * MIN_PALETA

        total_min = min_etykiety + min_oferty + min_produkty + min_ai + min_raporty + min_palety
        total_h = total_min / 60
        total_dni_robocze = total_h / 8  # 8h dzień roboczy

        time_data = {
            'etykiety': etykiety, 'min_etykiety': min_etykiety,
            'oferty': oferty_cnt, 'min_oferty': min_oferty,
            'produkty': produkty_cnt, 'min_produkty': min_produkty,
            'ai_analizy': ai_analizy, 'min_ai': min_ai,
            'miesiace': miesiace_aktywne, 'min_raporty': min_raporty,
            'palety': palety_cnt, 'min_palety': min_palety,
            'total_min': total_min,
            'total_h': total_h,
            'total_dni': total_dni_robocze,
        }
    except Exception as _e:
        print(f"[time_data] error: {_e}")
        time_data = {'total_min': 0, 'total_h': 0, 'total_dni': 0,
                     'etykiety': 0, 'min_etykiety': 0,
                     'oferty': 0, 'min_oferty': 0,
                     'produkty': 0, 'min_produkty': 0,
                     'ai_analizy': 0, 'min_ai': 0,
                     'miesiace': 0, 'min_raporty': 0,
                     'palety': 0, 'min_palety': 0}

    def fmt_czas(min):
        h = int(min) // 60
        m = int(min) % 60
        if h == 0: return f"{m}min"
        if m == 0: return f"{h}h"
        return f"{h}h {m}min"

    def _time_row(icon, label, cnt, min_per, total_min_val):
        return f'''<div style="display:flex;align-items:center;gap:10px;padding:10px 12px;background:rgba(13,15,26,0.8);border:1px solid rgba(255,255,255,0.05)">
        <div style="font-size:1.2rem;width:24px;text-align:center">{icon}</div>
        <div style="flex:1;font-size:0.8rem;color:#e2e8f0">{label}</div>
        <div style="font-size:0.75rem;color:#64748b;white-space:nowrap">{cnt} × {min_per}min</div>
        <div style="font-size:0.85rem;font-weight:700;color:#beee00;white-space:nowrap;min-width:60px;text-align:right">= {fmt_czas(total_min_val)}</div>
    </div>'''

    _rows_html = ''.join([
        _time_row('⚡', 'Etykiety kurierskie',   time_data.get('etykiety', 0),  4,  time_data.get('min_etykiety', 0)),
        _time_row('📦', 'Wystawienia Allegro',    time_data.get('oferty', 0),    8,  time_data.get('min_oferty', 0)),
        _time_row('🗂️', 'Produkty w magazynie',   time_data.get('produkty', 0),  2,  time_data.get('min_produkty', 0)),
        _time_row('🤖', 'Analizy AI',             time_data.get('ai_analizy', 0),12, time_data.get('min_ai', 0)),
        _time_row('📊', 'Raporty miesięczne',     time_data.get('miesiace', 0),  90, time_data.get('min_raporty', 0)),
        _time_row('🏭', 'Inwentaryzacje palet',   time_data.get('palety', 0),    30, time_data.get('min_palety', 0)),
    ])

    panel_czas_html = f"""
<div id="panel-czas" class="stat-panel cy-panel" style="display:none">
    <div style="text-align:center;padding:20px 0 10px">
        <div style="font-family:'Space Grotesk',sans-serif;font-size:3rem;font-weight:800;color:#8ff5ff;text-shadow:0 0 20px rgba(143,245,255,0.4);line-height:1">{time_data['total_h']:.1f}h</div>
        <div style="font-size:0.6rem;text-transform:uppercase;letter-spacing:2px;color:#64748b;margin-top:4px">zaoszczędzonego czasu</div>
        <div style="font-size:0.85rem;color:#beee00;margin-top:8px;font-weight:600">≈ {time_data['total_dni']:.1f} dni roboczych</div>
    </div>
    <div style="margin:16px 0;display:flex;flex-direction:column;gap:8px">
        {_rows_html}
    </div>
    <div style="font-size:0.6rem;color:#64748b;text-align:center;margin-top:8px">* Szacunki na podstawie typowego czasu ręcznego wykonania każdej operacji</div>
</div>
"""

    # TOP produkty i dostawcy
    top_produkty = stats.get('top_produkty', [])
    top_dostawcy = stats.get('top_dostawcy', [])

    # Aktualny miesiąc
    _nazwy_mies = {1:'Styczeń',2:'Luty',3:'Marzec',4:'Kwiecień',5:'Maj',6:'Czerwiec',7:'Lipiec',8:'Sierpień',9:'Wrzesień',10:'Październik',11:'Listopad',12:'Grudzień'}
    miesiac = f"{_nazwy_mies[datetime.now().month]} {datetime.now().year}"

    # TOP produkty HTML
    top_prod_html = ''
    for i, p in enumerate(top_produkty[:5]):
        border = f'border-bottom:1px solid var(--border);' if i < min(len(top_produkty), 5) - 1 else ''
        from .magazynier import _resolve_product_image
        img = _resolve_product_image(dict(p), size='sm')
        nazwa = p['nazwa'][:40] + ('...' if len(p['nazwa']) > 40 else '')
        top_prod_html += f'''<div style="display:flex;align-items:center;gap:10px;padding:8px 0;{border}">
            <div style="font-weight:700;color:var(--orange);width:20px">{i+1}.</div>
            <img src="{img}" style="width:40px;height:40px;border-radius:8px;object-fit:cover">
            <div style="flex:1;min-width:0">
                <div style="font-size:0.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{nazwa}</div>
                <div style="font-size:0.75rem;color:var(--text-muted)">{p['sprzedazy_cnt']} szt</div>
            </div>
            <div style="font-weight:600;color:var(--green)">{p['sprzedazy_suma']:,.0f} zl</div>
        </div>'''

    # TOP dostawcy HTML
    top_dost_html = ''
    for i, d in enumerate(top_dostawcy[:5]):
        border = f'border-bottom:1px solid var(--border);' if i < min(len(top_dostawcy), 5) - 1 else ''
        roi_color = 'var(--green)' if d['roi'] > 50 else ('var(--yellow)' if d['roi'] > 20 else 'var(--red)')
        top_dost_html += f'''<div style="display:flex;align-items:center;gap:10px;padding:8px 0;{border}">
            <div style="font-weight:700;color:var(--orange);width:20px">{i+1}</div>
            <div style="flex:1">
                <div style="font-weight:600" class="dostawca-name">{d['dostawca']}</div>
                <div style="font-size:0.75rem;color:var(--text-muted)">{d['sprzedazy_cnt']} szt | {d['przychod']:,.0f} zl przychod</div>
            </div>
            <div style="text-align:right">
                <div style="font-weight:700;color:{roi_color}">{d['roi']:.0f}%</div>
                <div style="font-size:0.7rem;color:var(--text-muted)">koszt: {d['koszt']:,.0f} zl</div>
            </div>
        </div>'''

    pryw_info = f' (W TYM {int(stats.get("sprzedaz_lacznie_pryw_suma",0))} ZL PRYWATNE)' if stats.get('sprzedaz_lacznie_pryw_suma',0) > 0 else ''

    html = f'''
    <style>
    /* === STATYSTYKI CYBERPUNK === */
    .cy-header{{font-family:'Space Grotesk','Inter',sans-serif;font-size:1.35rem;font-weight:800;background:linear-gradient(135deg,#8ff5ff,#beee00);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;letter-spacing:-0.5px}}
    .cy-sub{{font-size:0.62rem;text-transform:uppercase;letter-spacing:1.5px;color:#64748b;margin-top:2px;font-weight:600}}
    .cy-tabs{{display:flex;gap:0;margin-bottom:16px;background:rgba(9,9,20,0.8);border:1px solid rgba(255,255,255,0.06);overflow-x:auto;-webkit-overflow-scrolling:touch}}
    .cy-tab{{flex:1;padding:10px 6px;background:none;border:none;border-bottom:2px solid transparent;color:#64748b;font-family:'Space Grotesk','Inter',sans-serif;font-size:0.68rem;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;cursor:pointer;transition:all 0.2s;white-space:nowrap}}
    .cy-tab.active{{color:#beee00;border-bottom-color:#beee00;background:rgba(190,238,0,0.04)}}
    .cy-tab:hover:not(.active){{color:#e2e8f0;background:rgba(255,255,255,0.03)}}
    .cy-panel{{animation:cyFade 0.15s ease}}
    @keyframes cyFade{{from{{opacity:0;transform:translateY(3px)}}to{{opacity:1;transform:translateY(0)}}}}
    .cy-kpi-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px}}
    .cy-kpi-2{{grid-template-columns:repeat(2,1fr)}}
    .cy-kpi{{padding:14px 12px;background:rgba(13,15,26,0.8);border-left:3px solid rgba(143,245,255,0.15);transition:all 0.2s}}
    .cy-kpi:hover{{border-left-color:#8ff5ff}}
    .cy-kpi-val{{font-family:'Space Grotesk','Inter',sans-serif;font-size:1.3rem;font-weight:800;letter-spacing:-0.5px;line-height:1}}
    .cy-kpi-lbl{{font-size:0.56rem;text-transform:uppercase;letter-spacing:1.2px;color:#64748b;font-weight:600;margin-top:5px}}
    .cy-kpi.cyan .cy-kpi-val{{color:#8ff5ff}}.cy-kpi.lime .cy-kpi-val,.cy-kpi.lime{{border-left-color:rgba(190,238,0,0.2)}}.cy-kpi.lime .cy-kpi-val{{color:#beee00}}
    .cy-kpi.pink .cy-kpi-val{{color:#ff6b9b}}.cy-kpi.pink{{border-left-color:rgba(255,107,155,0.15)}}
    .cy-kpi.orange .cy-kpi-val{{color:#fb923c}}.cy-kpi.orange{{border-left-color:rgba(251,146,60,0.15)}}
    .cy-kpi.purple .cy-kpi-val{{color:#9b59ff}}.cy-kpi.purple{{border-left-color:rgba(155,89,255,0.15)}}
    .cy-kpi.red .cy-kpi-val{{color:#ef4444}}.cy-kpi.red{{border-left-color:rgba(239,68,68,0.15)}}
    .cy-highlight{{margin-top:10px;padding:14px 18px;background:rgba(190,238,0,0.04);border:1px solid rgba(190,238,0,0.15);display:flex;align-items:center;justify-content:space-between}}
    .cy-highlight-val{{font-family:'Space Grotesk','Inter',sans-serif;font-size:1.7rem;font-weight:800;letter-spacing:-0.5px;color:#beee00;text-shadow:0 0 12px rgba(190,238,0,0.35)}}
    .cy-highlight-lbl{{font-size:0.58rem;text-transform:uppercase;letter-spacing:1.2px;color:#64748b;font-weight:600}}
    .cy-section{{display:flex;align-items:center;gap:8px;margin:14px 0 8px}}
    .cy-section-bar{{width:2px;height:12px;background:#8ff5ff;flex-shrink:0}}.cy-section-bar.lime{{background:#beee00}}
    .cy-section-lbl{{font-size:0.62rem;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#64748b}}
    .cy-card{{background:rgba(13,15,26,0.8);padding:18px;margin-bottom:14px;border:1px solid rgba(255,255,255,0.05)}}
    .cy-links{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:14px}}
    .cy-link{{display:flex;align-items:center;justify-content:center;gap:5px;padding:10px 6px;background:rgba(13,15,26,0.8);border:1px solid rgba(143,245,255,0.12);color:#8ff5ff;text-decoration:none;font-family:'Space Grotesk',sans-serif;font-size:0.68rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;transition:all 0.2s}}
    .cy-link:hover{{background:rgba(143,245,255,0.07);border-color:rgba(143,245,255,0.25)}}
    .cy-link.lime{{border-color:rgba(190,238,0,0.12);color:#beee00}}.cy-link.lime:hover{{background:rgba(190,238,0,0.07);border-color:rgba(190,238,0,0.25)}}
    .cy-link.pink{{border-color:rgba(255,107,155,0.12);color:#ff6b9b}}.cy-link.pink:hover{{background:rgba(255,107,155,0.07);border-color:rgba(255,107,155,0.25)}}
    .cy-top-item{{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.04)}}
    .cy-top-item:last-child{{border-bottom:none}}
    .cy-rank{{font-family:'Space Grotesk',sans-serif;font-weight:800;color:#8ff5ff;width:18px;font-size:0.85rem;flex-shrink:0;text-align:center}}
    </style>

    <!-- HEADER -->
    <div style="display:flex;align-items:center;justify-content:space-between;padding:2px 0 18px">
        <div>
            <div class="cy-header"><span class="material-symbols-outlined" style="font-size:1.2rem;vertical-align:middle;margin-right:5px">hub</span>SALES_HUB</div>
            <div class="cy-sub">NODE_01 &middot; {datetime.now().strftime('%d.%m.%Y')} &middot; LIVE_FEED</div>
        </div>
        <a href="/magazyn/statystyki" style="display:inline-flex;align-items:center;gap:5px;padding:6px 12px;background:rgba(13,15,26,0.8);border:1px solid rgba(143,245,255,0.15);color:#8ff5ff;text-decoration:none;font-family:'Space Grotesk',sans-serif;font-size:0.68rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;transition:all 0.2s">
            <span class="material-symbols-outlined" style="font-size:0.85rem">bar_chart</span> Pełne stats
        </a>
    </div>

    <!-- TABS -->
    <div class="cy-tabs">
        <button class="cy-tab active" onclick="showTab('dzis')" id="tab-dzis">Dziś</button>
        <button class="cy-tab" onclick="showTab('miesiac')" id="tab-miesiac">Miesiąc</button>
        <button class="cy-tab" onclick="showTab('magazyn')" id="tab-magazyn">Magazyn</button>
        <button class="cy-tab" onclick="showTab('alltime')" id="tab-alltime">Łącznie</button>
        <button class="cy-tab" onclick="showTab('top')" id="tab-top">Top</button>
        <button class="cy-tab" onclick="showTab('czas')" id="tab-czas">⏱ Czas</button>
    </div>


    <!-- TAB: DZIŚ -->
    <div id="panel-dzis" class="stat-panel cy-panel">
        <div class="cy-kpi-grid">
            <div class="cy-kpi cyan">
                <div class="cy-kpi-val">{stats['sprzedaz_dzis_cnt']}</div>
                <div class="cy-kpi-lbl">Zamówień</div>
            </div>
            <div class="cy-kpi lime">
                <div class="cy-kpi-val">{stats['sprzedaz_dzis_suma']:,.0f} zł</div>
                <div class="cy-kpi-lbl">Przychód</div>
            </div>
            <div class="cy-kpi orange">
                <div class="cy-kpi-val">{stats.get('do_wyslania', 0)}</div>
                <div class="cy-kpi-lbl">Do wysyłki</div>
            </div>
        </div>
    </div>

    <!-- TAB: MIESIĄC -->
    <div id="panel-miesiac" class="stat-panel cy-panel" style="display:none">
        <div class="cy-kpi-grid cy-kpi-2">
            <div class="cy-kpi purple">
                <div class="cy-kpi-val">{stats['palety_miesiac']}</div>
                <div class="cy-kpi-lbl">Palet kupionych</div>
            </div>
            <div class="cy-kpi pink">
                <div class="cy-kpi-val">{stats['palety_miesiac_koszt']:,.0f} zł</div>
                <div class="cy-kpi-lbl">Wydane</div>
            </div>
            <div class="cy-kpi cyan">
                <div class="cy-kpi-val">{stats['sprzedaz_miesiac_cnt']}</div>
                <div class="cy-kpi-lbl">Sprzedaży</div>
            </div>
            <div class="cy-kpi lime">
                <div class="cy-kpi-val">{stats['sprzedaz_miesiac_suma']:,.0f} zł</div>
                <div class="cy-kpi-lbl">Przychód</div>
            </div>
        </div>
        <div class="cy-highlight">
            <div>
                <div class="cy-highlight-lbl">Szacowany zysk</div>
                <div style="font-size:0.68rem;color:#64748b;margin-top:2px">{miesiac}</div>
            </div>
            <div class="cy-highlight-val">{stats['zysk_miesiac']:,.0f} zł</div>
        </div>
    </div>

    <!-- TAB: MAGAZYN -->
    <div id="panel-magazyn" class="stat-panel cy-panel" style="display:none">
        <div class="cy-kpi-grid">
            <div class="cy-kpi purple">
                <div class="cy-kpi-val">{stats['magazyn_produkty']}</div>
                <div class="cy-kpi-lbl">Produktów</div>
            </div>
            <div class="cy-kpi cyan">
                <div class="cy-kpi-val">{stats['magazyn_sztuki']}</div>
                <div class="cy-kpi-lbl">Sztuk</div>
            </div>
            <div class="cy-kpi lime">
                <div class="cy-kpi-val">{stats['magazyn_wartosc']:,.0f} zł</div>
                <div class="cy-kpi-lbl">Wartość</div>
            </div>
        </div>
        <div class="cy-kpi-grid cy-kpi-2" style="margin-top:8px">
            <div class="cy-kpi cyan">
                <div class="cy-kpi-val">{stats['wystawione']}</div>
                <div class="cy-kpi-lbl">Wystawionych</div>
            </div>
            <div class="cy-kpi orange" style="cursor:pointer" onclick="location.href='/magazyn/lezaki'">
                <div class="cy-kpi-val">{stats['stojace_30dni']}</div>
                <div class="cy-kpi-lbl">Stoi &gt;30 dni ↗</div>
            </div>
        </div>
    </div>

    <!-- TAB: ALL-TIME -->
    <div id="panel-alltime" class="stat-panel cy-panel" style="display:none">
        <div class="cy-kpi-grid cy-kpi-2">
            <div class="cy-kpi purple">
                <div class="cy-kpi-val">{stats['palety_lacznie']}</div>
                <div class="cy-kpi-lbl">Palet łącznie</div>
            </div>
            <div class="cy-kpi pink">
                <div class="cy-kpi-val">{stats['palety_lacznie_koszt']:,.0f} zł</div>
                <div class="cy-kpi-lbl">Zainwestowane</div>
            </div>
            <div class="cy-kpi cyan">
                <div class="cy-kpi-val">{stats['sprzedaz_lacznie_cnt']}</div>
                <div class="cy-kpi-lbl">Sprzedanych</div>
            </div>
            <div class="cy-kpi lime">
                <div class="cy-kpi-val">{stats['sprzedaz_lacznie_suma']:,.0f} zł</div>
                <div class="cy-kpi-lbl">Przychód{pryw_info}</div>
            </div>
        </div>
        <div class="cy-kpi orange" style="margin-top:8px;text-align:center;padding:16px">
            <div class="cy-kpi-val" style="font-size:1.6rem">{stats['srednia_zamowienie']:.2f} zł</div>
            <div class="cy-kpi-lbl">Średnia wartość zamówienia</div>
        </div>
    </div>

    <!-- TAB: TOP -->
    <div id="panel-top" class="stat-panel cy-panel" style="display:none">
        {'<div class="cy-section"><span class="cy-section-bar lime"></span><span class="cy-section-lbl">Top produkty</span></div><div class="cy-card">' + top_prod_html + '</div>' if top_prod_html else ''}
        {'<div class="cy-section"><span class="cy-section-bar"></span><span class="cy-section-lbl">Top dostawcy (ROI)</span></div><div class="cy-card">' + top_dost_html + '</div>' if top_dost_html else ''}
    </div>

    <!-- TAB: CZAS -->
    {panel_czas_html}

    <!-- WYKRES - zawsze widoczny -->
    <div class="cy-section"><span class="cy-section-bar"></span><span class="cy-section-lbl">Wykres {current_year}</span></div>
    <div class="cy-card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
            <div style="font-family:'Space Grotesk',sans-serif;font-size:0.8rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.5px">Przychód / Zamówienia</div>
            <div style="display:flex;gap:5px">
                <button onclick="toggleChart('przychod')" id="btn-przychod" style="padding:5px 11px;border:1px solid #8ff5ff;background:rgba(143,245,255,0.1);color:#8ff5ff;font-family:'Space Grotesk',sans-serif;font-size:0.65rem;font-weight:700;text-transform:uppercase;cursor:pointer;letter-spacing:0.5px">Przychód</button>
                <button onclick="toggleChart('zamowienia')" id="btn-zamowienia" style="padding:5px 11px;border:1px solid rgba(255,255,255,0.08);background:none;color:#64748b;font-family:'Space Grotesk',sans-serif;font-size:0.65rem;font-weight:700;text-transform:uppercase;cursor:pointer;letter-spacing:0.5px">Zamówienia</button>
            </div>
        </div>
        <canvas id="chartMiesiace" height="200"></canvas>
    </div>

    <!-- QUICK LINKS -->
    <div class="cy-links">
        <a href="/palety" class="cy-link"><span class="material-symbols-outlined" style="font-size:0.85rem">inventory_2</span> Palety</a>
        <a href="/sprzedaze" class="cy-link lime"><span class="material-symbols-outlined" style="font-size:0.85rem">paid</span> Sprzedaże</a>
        <a href="/analityka" class="cy-link pink"><span class="material-symbols-outlined" style="font-size:0.85rem">trending_up</span> Analityka</a>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js" integrity="sha384-jb8JQMbMoBUzgWatfe6COACi2ljcDdZQ2OxczGA3bGNeWe+6DChMTBJemed7ZnvJ" crossorigin="anonymous"></script>
    <script>
    const chartLabels = {chart_labels};
    const chartPrzychod = {chart_data};
    const chartZamowienia = {chart_orders};
    Chart.defaults.color = '#64748b';
    Chart.defaults.borderColor = 'rgba(255,255,255,0.04)';
    Chart.defaults.font.family = "'Space Grotesk','Inter',sans-serif";

    const ctx = document.getElementById('chartMiesiace');
    let chart = new Chart(ctx, {{
        type: 'bar',
        data: {{
            labels: chartLabels,
            datasets: [{{
                label: 'Przychód (zł)',
                data: chartPrzychod,
                backgroundColor: chartPrzychod.map(v => v > 0 ? 'rgba(143,245,255,0.75)' : 'rgba(239,68,68,0.3)'),
                borderWidth: 0,
                borderRadius: 0
            }}]
        }},
        options: {{
            responsive: true,
            plugins: {{
                legend: {{ display: false }},
                tooltip: {{
                    backgroundColor: 'rgba(9,9,20,0.95)',
                    borderColor: 'rgba(143,245,255,0.2)',
                    borderWidth: 1,
                    titleFont: {{ family: "'Space Grotesk',sans-serif", weight: '700' }},
                    callbacks: {{ label: function(c) {{ return c.parsed.y.toLocaleString() + ' zł'; }} }}
                }}
            }},
            scales: {{
                y: {{ beginAtZero: true, grid: {{ color: 'rgba(255,255,255,0.04)' }}, ticks: {{ callback: v => v.toLocaleString() + ' zł' }} }},
                x: {{ grid: {{ display: false }} }}
            }},
            onClick: function(e, elements) {{
                if (elements.length > 0) {{
                    const idx = elements[0].index;
                    const miesiac = String(idx + 1).padStart(2, '0');
                    window.location.href = '/magazyn/statystyki?miesiac={current_year}-' + miesiac;
                }}
            }}
        }}
    }});
    ctx.style.cursor = 'pointer';

    function toggleChart(type) {{
        const btnP = document.getElementById('btn-przychod');
        const btnZ = document.getElementById('btn-zamowienia');
        if (type === 'przychod') {{
            btnP.style.background = 'rgba(143,245,255,0.1)'; btnP.style.borderColor = '#8ff5ff'; btnP.style.color = '#8ff5ff';
            btnZ.style.background = 'none'; btnZ.style.borderColor = 'rgba(255,255,255,0.08)'; btnZ.style.color = '#64748b';
            chart.data.datasets[0].data = chartPrzychod;
            chart.data.datasets[0].label = 'Przychód (zł)';
            chart.data.datasets[0].backgroundColor = chartPrzychod.map(v => v > 0 ? 'rgba(143,245,255,0.75)' : 'rgba(239,68,68,0.3)');
        }} else {{
            btnZ.style.background = 'rgba(190,238,0,0.1)'; btnZ.style.borderColor = '#beee00'; btnZ.style.color = '#beee00';
            btnP.style.background = 'none'; btnP.style.borderColor = 'rgba(255,255,255,0.08)'; btnP.style.color = '#64748b';
            chart.data.datasets[0].data = chartZamowienia;
            chart.data.datasets[0].label = 'Zamówienia';
            chart.data.datasets[0].backgroundColor = chartZamowienia.map(v => v > 0 ? 'rgba(190,238,0,0.75)' : 'rgba(239,68,68,0.3)');
        }}
        chart.update();
    }}

    function showTab(tab) {{
        document.querySelectorAll('.stat-panel').forEach(p => p.style.display = 'none');
        document.querySelectorAll('.cy-tab').forEach(t => t.classList.remove('active'));
        document.getElementById('panel-' + tab).style.display = 'block';
        document.getElementById('tab-' + tab).classList.add('active');
    }}
    </script>
    '''
    return render(html, 'Statystyki')



@analityka_bp.route('/analityka')
def analityka_dashboard():
    """Dashboard analityczny - mapa kupujących i rentowność kategorii"""
    from modules.database import get_db
    from modules.database import get_config_cached
    from modules.shared import auto_kategoryzuj, KATEGORIE_DISPLAY
    from collections import defaultdict
    import re

    conn = get_db()

    # ========== MAPA KUPUJĄCYCH ==========
    # Pobierz wszystkie adresy ze sprzedaży
    sprzedaze = conn.execute('''
        SELECT s.adres, s.cena, s.ilosc, s.data_sprzedazy
        FROM sprzedaze s
        WHERE s.status NOT IN ('anulowana', 'zwrot') AND s.adres IS NOT NULL AND s.adres != ''
    ''').fetchall()

    # Wyciągnij miasta z adresów
    miasta_stats = defaultdict(lambda: {'zamowienia': 0, 'przychod': 0})

    for s in sprzedaze:
        adres = s['adres'] or ''
        miasto = None

        # Format 1: "ulica, XX-XXX, miasto" - miasto jest ostatnie
        parts = [p.strip() for p in adres.split(',')]
        if len(parts) >= 2:
            # Ostatnia część to miasto (po kodzie pocztowym)
            last_part = parts[-1]
            # Sprawdź czy nie jest to kod pocztowy
            if not re.match(r'^\d{2}-\d{3}$', last_part):
                miasto = last_part.title()

        # Format 2: "XX-XXX miasto" - kod + miasto razem
        if not miasto:
            match = re.search(r'\d{2}-\d{3}\s+([A-Za-zżźćńółęąśŻŹĆĄŚĘŁÓŃ\s\-]+)', adres)
            if match:
                miasto = match.group(1).strip().title()

        if miasto and len(miasto) > 2 and len(miasto) < 50:
            miasta_stats[miasto]['zamowienia'] += 1
            miasta_stats[miasto]['przychod'] += (s['cena'] or 0) * (s['ilosc'] or 1)

    # Sortuj miasta po liczbie zamówień
    miasta_sorted = sorted(miasta_stats.items(), key=lambda x: x[1]['zamowienia'], reverse=True)[:20]

    # ========== RENTOWNOŚĆ KATEGORII ==========
    # Pobierz dane o sprzedażach - używamy nazwy ze sprzedaży do kategoryzacji
    sprzedaze_all = conn.execute('''
        SELECT
            s.id,
            s.nazwa as sprzedaz_nazwa,
            s.cena,
            s.ilosc,
            COALESCE(p.kategoria, p2.kategoria) as produkt_kategoria,
            CASE
                WHEN sc.sale_cnt > 0 AND pal.cena_zakupu > 0
                THEN pal.cena_zakupu / sc.sale_cnt
                ELSE 0
            END as produkt_koszt
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        LEFT JOIN oferty o ON s.oferta_id = o.id
        LEFT JOIN produkty p2 ON o.produkt_id = p2.id
        LEFT JOIN palety pal ON COALESCE(p.paleta_id, p2.paleta_id) = pal.id
        LEFT JOIN (
            SELECT pr.paleta_id,
                COALESCE(SUM(pr.ilosc), 0)
                + COALESCE(SUM(pr.sprzedano_offline), 0)
                + COALESCE((
                    SELECT SUM(sp2.ilosc) FROM sprzedaze sp2
                    JOIN produkty pp2 ON sp2.produkt_id = pp2.id
                    WHERE pp2.paleta_id = pr.paleta_id
                    AND sp2.status NOT IN ('zwrot','anulowane','anulowana')
                ), 0) as sale_cnt
            FROM produkty pr GROUP BY pr.paleta_id
        ) sc ON pal.id = sc.paleta_id
        WHERE s.status NOT IN ('anulowana', 'zwrot')
    ''').fetchall()

    # Grupuj per kategoria (używamy auto_kategoryzuj jeśli brak kategorii z produktu)
    kategorie_map = {}
    for s in sprzedaze_all:
        # Ustal kategorię: z produktu, z produktu przez ofertę, lub auto z nazwy
        kategoria = s['produkt_kategoria']
        if not kategoria or kategoria == 'inne':
            # Auto-kategoryzuj z nazwy sprzedaży
            kategoria = auto_kategoryzuj(s['sprzedaz_nazwa'] or '')

        if kategoria not in kategorie_map:
            kategorie_map[kategoria] = {'sprzedazy': 0, 'przychod': 0, 'koszt': 0}

        kategorie_map[kategoria]['sprzedazy'] += 1
        kategorie_map[kategoria]['przychod'] += (s['cena'] or 0) * (s['ilosc'] or 1)
        kategorie_map[kategoria]['koszt'] += (s['produkt_koszt'] or 0)

    # Oblicz zysk i marżę dla każdej kategorii
    kategorie_stats = []
    for kategoria, data in kategorie_map.items():
        przychod = data['przychod']
        koszt = data['koszt']
        prowizja = przychod * 0.11  # Allegro ~11%
        zysk = przychod - koszt - prowizja
        marza = (zysk / przychod * 100) if przychod > 0 else 0

        # Użyj ładnej nazwy z KATEGORIE_DISPLAY
        kategoria_display = KATEGORIE_DISPLAY.get(kategoria, kategoria or 'Inne')

        kategorie_stats.append({
            'kategoria': kategoria_display,
            'sprzedazy': data['sprzedazy'],
            'przychod': przychod,
            'koszt': koszt,
            'prowizja': prowizja,
            'zysk': zysk,
            'marza': marza
        })

    # Sortuj po zysku
    kategorie_stats.sort(key=lambda x: x['zysk'], reverse=True)

    # ========== SPRZEDAŻ W CZASIE (DZIENNIE) ==========
    sprzedaz_dni = conn.execute('''
        SELECT
            DATE(data_sprzedazy) as dzien,
            COUNT(*) as liczba,
            COALESCE(SUM(cena * ilosc), 0) as przychod
        FROM sprzedaze
        WHERE status NOT IN ('anulowana', 'zwrot')
          AND data_sprzedazy >= date('now', '-30 days')
        GROUP BY DATE(data_sprzedazy)
        ORDER BY dzien ASC
    ''').fetchall()

    # Przychód skumulowany (narastająco)
    przychod_kumulowany = []
    suma = 0
    for d in sprzedaz_dni:
        suma += d['przychod']
        przychod_kumulowany.append(suma)

    # ========== PRODUKTY BEZ KATEGORII ==========
    produkty_bez_kat = conn.execute('''
        SELECT COUNT(*) as cnt FROM produkty WHERE kategoria IS NULL OR kategoria = '' OR kategoria = 'inne'
    ''').fetchone()['cnt']

    # Przygotuj dane do wykresów
    miasta_labels = [m[0] for m in miasta_sorted]
    miasta_values = [m[1]['zamowienia'] for m in miasta_sorted]
    miasta_przychod = [m[1]['przychod'] for m in miasta_sorted]

    kategorie_labels = [k['kategoria'][:15] for k in kategorie_stats[:10]]
    kategorie_zysk = [k['zysk'] for k in kategorie_stats[:10]]
    kategorie_marza = [k['marza'] for k in kategorie_stats[:10]]

    # Dane dzienne do wykresu
    dni_labels = [d['dzien'][5:] for d in sprzedaz_dni]  # Format MM-DD
    dni_przychod = [d['przychod'] for d in sprzedaz_dni]
    dni_kumulowany = przychod_kumulowany  # Narastająco

    # ========== TOP/FLOP PRODUKTY ==========
    # Pobierz produkty posortowane po zysku - z ceną zakupu
    top_flop_data = conn.execute('''
        SELECT
            s.nazwa,
            SUM(s.ilosc) as ilosc_sprzedazy,
            SUM(s.cena * s.ilosc) as przychod,
            AVG(s.cena) as srednia_cena,
            AVG(CASE
                WHEN pal.cena_zakupu > 0 AND pal_sum.total_szt > 0
                THEN pal.cena_zakupu / pal_sum.total_szt
                ELSE 0
            END) as avg_koszt_paleta
        FROM sprzedaze s
        LEFT JOIN produkty p2 ON s.produkt_id = p2.id
        LEFT JOIN palety pal ON p2.paleta_id = pal.id
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
        ) pal_sum ON p2.paleta_id = pal_sum.paleta_id
        WHERE s.status NOT IN ('anulowana', 'zwrot')
        AND s.nazwa IS NOT NULL AND s.nazwa != ''
        GROUP BY s.nazwa
        HAVING SUM(s.ilosc) >= 1
        ORDER BY przychod DESC
    ''').fetchall()

    # TOP 10 - najlepsze produkty
    top_produkty = []
    for p in top_flop_data[:10]:
        przychod = p['przychod'] or 0
        ilosc = p['ilosc_sprzedazy'] or 1
        prowizja = przychod * 0.11  # Allegro ~11%

        # Koszt zakupu - z palety (cena_zakupu / ilość produktów w palecie)
        koszt_unit = p['avg_koszt_paleta'] or 0
        koszt_total = koszt_unit * ilosc

        zysk = przychod - koszt_total - prowizja

        top_produkty.append({
            'nazwa': (p['nazwa'] or '')[:50],
            'ilosc': ilosc,
            'przychod': przychod,
            'srednia_cena': p['srednia_cena'] or 0,
            'koszt_total': koszt_total,
            'prowizja': prowizja,
            'zysk': zysk,
            'has_koszt': koszt_unit > 0
        })

    # FLOP - produkty które się nie sprzedają (z magazynu)
    flop_produkty = conn.execute('''
        SELECT
            p.nazwa,
            p.cena_brutto as cena_zakupu,
            p.cena_allegro as cena_sprzedazy,
            p.ilosc as stan,
            p.kategoria,
            julianday('now') - julianday(p.data_dodania) as dni_w_magazynie
        FROM produkty p
        WHERE p.status IN ('magazyn', 'wystawiony')
        AND p.ilosc > 0
        AND p.data_dodania IS NOT NULL
        ORDER BY dni_w_magazynie DESC
        LIMIT 10
    ''').fetchall()

    flop_lista = []
    for p in flop_produkty:
        dni = int(p['dni_w_magazynie'] or 0)
        flop_lista.append({
            'nazwa': (p['nazwa'] or '')[:50],
            'cena_zakupu': p['cena_zakupu'] or 0,
            'cena_sprzedazy': p['cena_sprzedazy'] or 0,
            'stan': p['stan'] or 0,
            'dni': dni,
            'kategoria': KATEGORIE_DISPLAY.get(p['kategoria'], p['kategoria'] or 'Inne')
        })

    # ========== SPRZEDAŻE PRYWATNE ==========
    try:
        pryw_row = conn.execute('SELECT COUNT(*) as cnt, COALESCE(SUM(kwota), 0) as suma FROM sprzedaze_prywatne').fetchone()
        prywatne_suma = pryw_row['suma'] or 0
        prywatne_cnt = pryw_row['cnt'] or 0
    except:
        prywatne_suma = 0
        prywatne_cnt = 0


    # Łączny zysk = Allegro zysk + prywatne
    allegro_zysk = sum(k['zysk'] for k in kategorie_stats)
    laczny_zysk = allegro_zysk + prywatne_suma

    from flask import render_template

    return render_template('analityka.html',
        # KPI values
        miasta_count=len(miasta_stats),
        zamowien_total=sum(m[1]['zamowienia'] for m in miasta_stats.items()),
        kategorie_count=len(kategorie_stats),
        laczny_zysk=laczny_zysk,
        allegro_zysk=allegro_zysk,
        prywatne_suma=prywatne_suma,
        prywatne_cnt=prywatne_cnt,
        produkty_bez_kat=produkty_bez_kat,
        # Tables data
        miasta_sorted=miasta_sorted,
        kategorie_stats=kategorie_stats,
        top_produkty=top_produkty,
        flop_lista=flop_lista,
        # Chart data
        miasta_labels=miasta_labels,
        miasta_values=miasta_values,
        miasta_przychod=miasta_przychod,
        kategorie_labels=kategorie_labels,
        kategorie_zysk=kategorie_zysk,
        kategorie_marza=kategorie_marza,
        dni_labels=dni_labels,
        dni_przychod=dni_przychod,
        dni_kumulowany=dni_kumulowany,
        # Base template vars
        version=current_app.config.get('VERSION', ''),
        brand_name=current_app.config.get('BRAND_NAME', 'Akces Hub'),
        current_user=session.get('username')
    )



@analityka_bp.route('/analityka/palety')
def analityka_palety():
    """Bilans palet - koszt vs przychód, ROI"""
    from modules.database import get_db, get_config_cached

    conn = get_db()

    # Pobierz wszystkie palety z pełnymi statystykami
    palety = conn.execute('''
        SELECT
            p.id,
            p.nazwa,
            p.dostawca,
            p.cena_zakupu,
            p.data_zakupu,
            p.ilosc_produktow,
            (SELECT COUNT(*) FROM produkty WHERE paleta_id = p.id) as produktow_w_bazie,
            (SELECT COALESCE(SUM(CASE WHEN status IN ('sprzedany','wyslany','uszkodzony','naprawa','zlomowany') THEN 0 ELSE ilosc END), 0) FROM produkty WHERE paleta_id = p.id) as aktualna_ilosc,
            (SELECT COALESCE(SUM(cena_brutto), 0) FROM produkty WHERE paleta_id = p.id) as koszt_produktow,
            (SELECT COALESCE(SUM(cena_allegro * ilosc), 0) FROM produkty WHERE paleta_id = p.id AND status = 'dostepny') as wartosc_magazynu,
            (SELECT COALESCE(SUM(CASE WHEN status = 'sprzedany' AND (sprzedano_offline IS NULL OR sprzedano_offline = 0) THEN cena_allegro ELSE 0 END), 0) FROM produkty WHERE paleta_id = p.id) as przychod_produkty,
            COALESCE((SELECT SUM(s.cena * s.ilosc) FROM sprzedaze s LEFT JOIN produkty pr ON s.produkt_id = pr.id LEFT JOIN oferty o ON s.oferta_id = o.id LEFT JOIN produkty pr2 ON o.produkt_id = pr2.id WHERE COALESCE(pr.paleta_id, pr2.paleta_id) = p.id AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')), 0) as przychod_tabela,
            (SELECT COALESCE(SUM(przychod_offline), 0) FROM produkty WHERE paleta_id = p.id) as przychod_offline,
            (SELECT COALESCE(SUM(sprzedano_offline), 0) FROM produkty WHERE paleta_id = p.id) as sprzedano_offline_szt,
            (SELECT COUNT(*) FROM produkty WHERE paleta_id = p.id AND status = 'sprzedany' AND (sprzedano_offline IS NULL OR sprzedano_offline = 0)) as sprzedano_produkty,
            COALESCE((SELECT SUM(s.ilosc) FROM sprzedaze s LEFT JOIN produkty pr ON s.produkt_id = pr.id LEFT JOIN oferty o ON s.oferta_id = o.id LEFT JOIN produkty pr2 ON o.produkt_id = pr2.id WHERE COALESCE(pr.paleta_id, pr2.paleta_id) = p.id AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')), 0) as sprzedano_tabela,
            COALESCE((SELECT SUM(s.cena * s.ilosc) FROM sprzedaze s LEFT JOIN produkty pr ON s.produkt_id = pr.id LEFT JOIN oferty o ON s.oferta_id = o.id LEFT JOIN produkty pr2 ON o.produkt_id = pr2.id WHERE COALESCE(pr.paleta_id, pr2.paleta_id) = p.id AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')), 0) as przychod_allegro_only,
            p.ilosc_sztuk,
            (SELECT AVG(cena_allegro) FROM produkty WHERE paleta_id = p.id AND cena_allegro > 0) as avg_cena_allegro,
            (SELECT MIN(s.data_sprzedazy) FROM sprzedaze s
             LEFT JOIN produkty pr ON s.produkt_id = pr.id
             LEFT JOIN oferty o ON s.oferta_id = o.id
             LEFT JOIN produkty pr2 ON o.produkt_id = pr2.id
             WHERE COALESCE(pr.paleta_id, pr2.paleta_id) = p.id
               AND s.data_sprzedazy IS NOT NULL AND s.data_sprzedazy != ''
               AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot')) as first_sale_date
        FROM palety p
        ORDER BY p.data_zakupu DESC
    ''').fetchall()

    # Oblicz statystyki dla każdej palety
    palety_stats = []
    total_koszt = 0
    total_przychod = 0
    total_zysk = 0

    for p in palety:
        koszt = p['cena_zakupu'] or 0
        przychod_produkty = p['przychod_produkty'] or 0
        przychod_tabela = p['przychod_tabela'] or 0
        przychod_offline = p['przychod_offline'] or 0

        if przychod_tabela > 0:
            przychod = przychod_tabela
        else:
            przychod = przychod_produkty + przychod_offline

        przychod_allegro_only = p['przychod_allegro_only'] or 0

        aktualna_ilosc = p['aktualna_ilosc'] or 0
        sprzedanych_offline = p['sprzedano_offline_szt'] or 0
        sprzedano_tabela = p['sprzedano_tabela'] or 0
        sprzedano_produkty = p['sprzedano_produkty'] or 0

        if sprzedano_tabela > 0:
            sprzedanych = sprzedano_tabela
        else:
            sprzedanych = sprzedano_produkty + sprzedanych_offline

        # FALLBACK: jeśli ilosc_sztuk (oryginalna ilość przy zakupie) jest znana,
        # sprzedanych = ilosc_sztuk - aktualna_ilosc (obsługuje kliknięcia "-" bez wpisu w sprzedaze)
        try:
            _ilosc_sztuk_orig = int(p['ilosc_sztuk'] or 0)
        except:
            _ilosc_sztuk_orig = 0
        if _ilosc_sztuk_orig > 0 and (_ilosc_sztuk_orig - aktualna_ilosc) > sprzedanych:
            sprzedanych = _ilosc_sztuk_orig - aktualna_ilosc

        # Szacuj przychód dla nieśledzonych sprzedaży (brak wpisu w sprzedaze)
        _tracked_szt = sprzedano_tabela + sprzedanych_offline
        _untracked_szt = sprzedanych - _tracked_szt
        if _untracked_szt > 0:
            _avg_cena = float(p['avg_cena_allegro'] or 0)
            if _avg_cena > 0:
                przychod += _untracked_szt * _avg_cena

        # Zysk i ROI liczone PO aktualizacji przychodu (uwzględnia nieśledzone sprzedaże)
        prowizja = przychod_allegro_only * 0.11
        zysk = przychod - koszt - prowizja
        roi = (zysk / koszt * 100) if koszt > 0 else 0

        wszystkich = aktualna_ilosc + sprzedanych
        zostalo = aktualna_ilosc

        if wszystkich == 0:
            status = 'pusta'
            status_color = 'var(--text-muted)'
        elif zostalo == 0 and sprzedanych > 0:
            status = 'zakończona'
            status_color = 'var(--green)' if zysk > 0 else 'var(--red)'
        else:
            progress = (sprzedanych / wszystkich * 100) if wszystkich > 0 else 0
            status = f'{progress:.0f}% sprzedane'
            status_color = 'var(--orange)' if progress < 100 else 'var(--green)'

        koszt_szt = (koszt / wszystkich) if wszystkich > 0 else 0

        if sprzedanych > 0 and zostalo > 0:
            avg_cena = przychod / sprzedanych
            prognoza_przychod = avg_cena * wszystkich
            prognoza_prowizja = prognoza_przychod * 0.11
            prognoza = prognoza_przychod - koszt - prognoza_prowizja
        else:
            prognoza = zysk

        # Tempo sprzedaży (szt/dzień)
        # Logika: od daty pierwszej sprzedaży, ale jeśli ta data jest świeższa
        # niż 3 dni (spike!) — cofamy się do daty zakupu palety jako baseline,
        # żeby nie zawyżać tempa palet gdzie właśnie zaczęliśmy sprzedawać.
        from datetime import datetime as _dt
        try:
            _today = _dt.now()
            _first = p['first_sale_date']
            _data_zakupu_str = str(p['data_zakupu'] or '')[:10]
            _data_zakupu = _dt.strptime(_data_zakupu_str, '%Y-%m-%d') if _data_zakupu_str and _data_zakupu_str != 'N' else None
            if _first:
                _data_first_sale = _dt.strptime(str(_first)[:10], '%Y-%m-%d')
                _dni_od_first = (_today - _data_first_sale).days
                if _dni_od_first >= 3:
                    # Mamy wystarczająco dużo danych — liczymy od 1. sprzedaży
                    _dni = max(1, _dni_od_first)
                elif _data_zakupu:
                    # Świeża sprzedaż (<3 dni) — używamy daty zakupu palety
                    # żeby nie zawyżać przez spike jednego dnia
                    _dni = max(3, (_today - _data_zakupu).days)
                else:
                    _dni = max(1, _dni_od_first)
            elif _data_zakupu:
                # Brak sprzedaży jeszcze — fallback na datę zakupu
                _dni = max(1, (_today - _data_zakupu).days)
            else:
                _dni = 7  # nie mamy żadnej daty
        except:
            _dni = 7
        tempo = sprzedanych / _dni if sprzedanych > 0 else 0
        procent = (sprzedanych / wszystkich * 100) if wszystkich > 0 else 0

        palety_stats.append({
            'id': p['id'],
            'nazwa': p['nazwa'] or f"Paleta #{p['id']}",
            'dostawca': p['dostawca'] or '-',
            'data': p['data_zakupu'] or '-',
            'koszt': koszt,
            'przychod': przychod,
            'przychod_allegro': przychod_tabela or przychod_produkty,
            'przychod_offline': przychod_offline if przychod_tabela == 0 else 0,
            'prowizja': prowizja,
            'zysk': zysk,
            'roi': roi,
            'koszt_szt': koszt_szt,
            'prognoza': prognoza,
            'tempo': tempo,
            'procent': procent,
            'wszystkich': wszystkich,
            'zostalo': zostalo,
            'sprzedanych': sprzedanych,
            'wartosc_magazynu': p['wartosc_magazynu'] or 0,
            'status': status,
            'status_color': status_color
        })

        total_koszt += koszt
        total_przychod += przychod
        total_zysk += zysk

    total_prowizja = sum(p['prowizja'] for p in palety_stats)
    total_roi = (total_zysk / total_koszt * 100) if total_koszt > 0 else 0

    # Średnie tempo sprzedaży (szt/dzień) — tylko palety ze sprzedażą
    _tempa = [p['tempo'] for p in palety_stats if p['sprzedanych'] > 0]
    avg_tempo = sum(_tempa) / len(_tempa) if _tempa else 0
    total_sprzedanych = sum(p['sprzedanych'] for p in palety_stats)

    # Unikalni dostawcy do filtra
    dostawcy = sorted(set(p['dostawca'] for p in palety_stats if p['dostawca'] != '-'))

    # Dane do wykresu - TOP 10 palet wg ROI
    top_palety = sorted([p for p in palety_stats if p['koszt'] > 0], key=lambda x: x['roi'], reverse=True)[:10]
    chart_labels = [p['nazwa'][:20] for p in top_palety]
    chart_roi = [p['roi'] for p in top_palety]

    # Wykres kumulacyjny zysku w czasie
    sorted_by_date = sorted([p for p in palety_stats if p['data'] != '-'], key=lambda x: x['data'])
    cum_dates = [p['data'] for p in sorted_by_date]
    cum_zysk = []
    running = 0
    for p in sorted_by_date:
        running += p['zysk']
        cum_zysk.append(round(running, 2))

    html = f'''
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Manrope:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link href="https://fonts.googleapis.com/icon?family=Material+Icons+Round" rel="stylesheet">
    <style>
        :root{{--bp-cyan:#8ff5ff;--bp-pink:#ff6b9b;--bp-lime:#cafd00;--bp-lime-dim:#beee00;--bp-bg:#0e0e10;--bp-surface:rgba(19,19,21,0.8);--bp-surface-high:rgba(30,30,33,0.9);--bp-surface-bright:rgba(40,40,44,0.6);--bp-border:rgba(255,255,255,0.06);--bp-text:#f9f5f8;--bp-muted:rgba(255,255,255,0.45)}}

        /* Dot grid background */
        .bp-dot-grid{{position:fixed;inset:0;pointer-events:none;z-index:0;background-image:radial-gradient(rgba(143,245,255,0.05) 1px,transparent 1px);background-size:24px 24px}}

        /* Typography */
        .bp-headline{{font-family:'Space Grotesk',sans-serif}}
        .bp-label{{font-family:'Manrope',sans-serif;font-size:10px;text-transform:uppercase;letter-spacing:0.2em;color:var(--bp-muted)}}
        .bp-value{{font-family:'Space Grotesk',sans-serif;font-weight:700;font-variant-numeric:tabular-nums}}

        /* Glass panel */
        .bp-glass{{backdrop-filter:blur(12px);background:var(--bp-surface);border:1px solid var(--bp-border)}}

        /* Neon glow */
        .bp-glow{{box-shadow:0 0 20px rgba(143,245,255,0.15)}}
        .bp-glow-pink{{box-shadow:0 0 20px rgba(255,107,155,0.15)}}
        .bp-glow-lime{{box-shadow:0 0 20px rgba(202,253,0,0.15)}}

        /* KPI cards */
        .bp-kpi-grid{{display:grid;grid-template-columns:repeat(6,1fr);gap:16px;margin-bottom:24px}}
        @media(max-width:1400px){{.bp-kpi-grid{{grid-template-columns:repeat(3,1fr)}}}}
        @media(max-width:768px){{.bp-kpi-grid{{grid-template-columns:repeat(2,1fr)}}}}
        .bp-kpi{{backdrop-filter:blur(12px);background:var(--bp-surface);border:1px solid var(--bp-border);padding:20px;position:relative;transition:all 0.2s}}
        .bp-kpi:hover{{background:var(--bp-surface-bright);box-shadow:0 0 20px rgba(143,245,255,0.1)}}
        .bp-kpi-icon{{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;justify-content:center;margin-bottom:12px;font-size:18px}}
        .bp-kpi-icon .material-icons-round{{font-size:20px}}

        /* Charts grid */
        .bp-charts-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}}
        @media(max-width:900px){{.bp-charts-grid{{grid-template-columns:1fr}}}}
        .bp-chart-panel{{backdrop-filter:blur(12px);background:var(--bp-surface);border:1px solid var(--bp-border);padding:24px}}
        .bp-chart-panel:hover{{box-shadow:0 0 20px rgba(143,245,255,0.08)}}
        .bp-chart-container{{height:300px;position:relative}}

        /* Table */
        .bp-table-panel{{backdrop-filter:blur(12px);background:var(--bp-surface);border:1px solid var(--bp-border);padding:24px}}
        .bp-table{{width:100%;border-collapse:collapse;font-family:'Manrope',sans-serif;font-size:0.82rem}}
        .bp-table th{{padding:10px 8px;text-align:left;font-family:'Manrope',sans-serif;font-size:10px;text-transform:uppercase;letter-spacing:0.15em;color:var(--bp-muted);font-weight:600;cursor:pointer;user-select:none;white-space:nowrap;background:var(--bp-surface-high);border-bottom:1px solid var(--bp-border)}}
        .bp-table th:hover{{color:var(--bp-text)}}
        .bp-table th .sort-arrow{{font-size:0.65rem;margin-left:3px;display:inline-block;min-width:10px;transition:opacity 0.15s}}
        .bp-table td{{padding:12px 8px;border-bottom:1px solid var(--bp-border);color:var(--bp-text)}}
        .bp-table tbody tr{{cursor:pointer;transition:all 0.15s;position:relative}}
        .bp-table tbody tr:hover{{background:var(--bp-surface-bright)}}

        /* Progress bar */
        .bp-progress{{width:100%;height:4px;background:rgba(255,255,255,0.06);border-radius:2px;overflow:hidden}}
        .bp-progress-fill{{height:100%;background:linear-gradient(90deg,var(--bp-cyan),var(--bp-lime));transition:width 0.3s;box-shadow:0 0 8px rgba(143,245,255,0.3)}}

        /* ROI badge */
        .bp-roi-badge{{display:inline-block;padding:3px 10px;font-family:'Space Grotesk',sans-serif;font-size:11px;font-weight:700;font-variant-numeric:tabular-nums}}
        .bp-roi-pos{{background:rgba(202,253,0,0.15);color:var(--bp-lime)}}
        .bp-roi-neg{{background:rgba(255,107,155,0.15);color:var(--bp-pink)}}

        /* Status badge */
        .bp-status{{padding:3px 10px;font-family:'Manrope',sans-serif;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;white-space:nowrap;display:inline-block}}

        /* Filter buttons */
        .bp-filter-btn{{padding:7px 16px;background:rgba(19,19,21,0.8);border:1px solid var(--bp-border);color:var(--bp-muted);cursor:pointer;font-family:'Manrope',sans-serif;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;transition:all 0.15s}}
        .bp-filter-btn:hover{{color:var(--bp-text);border-color:rgba(143,245,255,0.3)}}

        /* Search input */
        .bp-search{{background:rgba(10,10,15,0.6);border:1px solid var(--bp-border);color:var(--bp-text);padding:9px 14px;font-family:'Manrope',sans-serif;font-size:13px;transition:all 0.2s;width:220px}}
        .bp-search:focus{{border-color:var(--bp-cyan);box-shadow:0 0 12px rgba(143,245,255,0.15);outline:none}}
        .bp-search::placeholder{{color:var(--bp-muted)}}

        /* Select */
        .bp-select{{background:rgba(10,10,15,0.6);border:1px solid var(--bp-border);color:var(--bp-text);padding:9px 14px;font-family:'Manrope',sans-serif;font-size:13px;transition:all 0.2s}}
        .bp-select:focus{{border-color:var(--bp-cyan);box-shadow:0 0 12px rgba(143,245,255,0.15);outline:none}}

        /* Accent bar on rows */
        .bp-accent-bar{{position:absolute;left:0;top:0;bottom:0;width:3px}}
    </style>

    <!-- Dot grid background -->
    <div class="bp-dot-grid"></div>

    <!-- Page header -->
    <div style="position:relative;z-index:1;margin-bottom:28px;display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:16px">
        <div>
            <h1 class="bp-headline" style="font-size:1.8rem;font-weight:700;color:var(--bp-text);margin:0 0 4px 0;letter-spacing:-0.02em">Bilans Palet</h1>
            <p class="bp-label" style="margin:0;font-size:11px;letter-spacing:0.18em">Warehouse Inventory & ROI Analytics</p>
        </div>
        <div style="display:flex;gap:10px;align-items:center">
            <a href="/palety" style="display:inline-flex;align-items:center;gap:6px;padding:9px 18px;background:rgba(143,245,255,0.08);border:1px solid rgba(143,245,255,0.2);color:var(--bp-cyan);font-family:'Space Grotesk',sans-serif;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;text-decoration:none;transition:all 0.15s">
                <span class="material-icons-round" style="font-size:16px">inventory_2</span> Palety
            </a>
        </div>
    </div>

    <!-- KPI Grid -->
    <div class="bp-kpi-grid" style="position:relative;z-index:1">
        <!-- Total Pallets -->
        <div class="bp-kpi bp-glow" style="border-left:2px solid var(--bp-cyan)">
            <div class="bp-kpi-icon" style="background:rgba(143,245,255,0.1)">
                <span class="material-icons-round" style="color:var(--bp-cyan)">inventory_2</span>
            </div>
            <div class="bp-value" style="font-size:1.6rem;color:var(--bp-cyan);margin-bottom:4px">{len(palety_stats)}</div>
            <div class="bp-label">Palet lacznie</div>
        </div>
        <!-- Purchase Cost -->
        <div class="bp-kpi bp-glow-pink" style="border-left:2px solid var(--bp-pink)">
            <div class="bp-kpi-icon" style="background:rgba(255,107,155,0.1)">
                <span class="material-icons-round" style="color:var(--bp-pink)">shopping_cart</span>
            </div>
            <div class="bp-value" style="font-size:1.6rem;color:var(--bp-pink);margin-bottom:4px">{total_koszt:,.0f} zl</div>
            <div class="bp-label">Koszt zakupu</div>
        </div>
        <!-- Revenue -->
        <div class="bp-kpi bp-glow-lime" style="border-left:2px solid var(--bp-lime)">
            <div class="bp-kpi-icon" style="background:rgba(202,253,0,0.1)">
                <span class="material-icons-round" style="color:var(--bp-lime)">trending_up</span>
            </div>
            <div class="bp-value" style="font-size:1.6rem;color:var(--bp-lime);margin-bottom:4px">{total_przychod:,.0f} zl</div>
            <div class="bp-label">Przychod</div>
        </div>
        <!-- Allegro Commission -->
        <div class="bp-kpi" style="border-left:2px solid rgba(255,255,255,0.15)">
            <div class="bp-kpi-icon" style="background:rgba(255,255,255,0.05)">
                <span class="material-icons-round" style="color:var(--bp-muted)">receipt_long</span>
            </div>
            <div class="bp-value" style="font-size:1.6rem;color:var(--bp-muted);margin-bottom:4px">{total_prowizja:,.0f} zl</div>
            <div class="bp-label">Prowizje Allegro</div>
        </div>
        <!-- Net Profit / ROI -->
        <div class="bp-kpi {'bp-glow-lime' if total_zysk >= 0 else 'bp-glow-pink'}" style="border-left:2px solid {'var(--bp-lime)' if total_zysk >= 0 else 'var(--bp-pink)'}">
            <div class="bp-kpi-icon" style="background:{'rgba(202,253,0,0.1)' if total_zysk >= 0 else 'rgba(255,107,155,0.1)'}">
                <span class="material-icons-round" style="color:{'var(--bp-lime)' if total_zysk >= 0 else 'var(--bp-pink)'}">{'emoji_events' if total_zysk >= 0 else 'warning'}</span>
            </div>
            <div class="bp-value" style="font-size:1.6rem;color:{'var(--bp-lime)' if total_zysk >= 0 else 'var(--bp-pink)'};margin-bottom:4px">{total_zysk:,.0f} zl <span style="font-size:0.85rem;opacity:0.7">({total_roi:.0f}%)</span></div>
            <div class="bp-label">Zysk netto / ROI</div>
        </div>
        <!-- Średnie tempo sprzedaży -->
        <div class="bp-kpi" style="border-left:2px solid rgba(255,200,80,0.6)">
            <div class="bp-kpi-icon" style="background:rgba(255,200,80,0.1)">
                <span class="material-icons-round" style="color:#ffc850">speed</span>
            </div>
            <div class="bp-value" style="font-size:1.6rem;color:#ffc850;margin-bottom:4px">{avg_tempo:.1f}<span style="font-size:0.9rem;opacity:0.7"> szt/d</span></div>
            <div class="bp-label">Srednie tempo</div>
            <div style="font-size:0.7rem;color:var(--bp-muted);margin-top:6px">{total_sprzedanych} szt lacznie od 1. sprzedazy</div>
        </div>
    </div>

    <!-- Charts Grid -->
    <div class="bp-charts-grid" style="position:relative;z-index:1">
        <div class="bp-chart-panel">
            <div style="margin-bottom:16px;display:flex;align-items:center;gap:8px">
                <span class="material-icons-round" style="color:var(--bp-lime);font-size:18px">bar_chart</span>
                <span class="bp-headline" style="font-size:14px;font-weight:600;color:var(--bp-text)">TOP 10 Palet wg ROI</span>
            </div>
            <div class="bp-chart-container">
                <canvas id="roiChart"></canvas>
            </div>
        </div>
        <div class="bp-chart-panel">
            <div style="margin-bottom:16px;display:flex;align-items:center;gap:8px">
                <span class="material-icons-round" style="color:var(--bp-cyan);font-size:18px">show_chart</span>
                <span class="bp-headline" style="font-size:14px;font-weight:600;color:var(--bp-text)">Zysk kumulacyjny w czasie</span>
            </div>
            <div class="bp-chart-container">
                <canvas id="cumChart"></canvas>
            </div>
        </div>
    </div>

    <!-- Pallet List Table -->
    <div class="bp-table-panel" style="position:relative;z-index:1">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;flex-wrap:wrap;gap:12px">
            <div style="display:flex;align-items:center;gap:10px">
                <span class="material-icons-round" style="color:var(--bp-cyan);font-size:20px">view_list</span>
                <span class="bp-headline" style="font-size:15px;font-weight:600;color:var(--bp-text)">Wszystkie palety</span>
                <span class="bp-label" style="background:rgba(143,245,255,0.08);padding:3px 10px;color:var(--bp-cyan);font-size:10px;letter-spacing:0.12em">{len(palety_stats)}</span>
            </div>
            <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
                <input type="text" id="searchInput" oninput="filterTable()" placeholder="Szukaj palety..." class="bp-search">
                <span class="bp-label" style="font-size:10px;letter-spacing:0.15em">Dostawca:</span>
                <select id="dostawcaFilter" onchange="filterTable()" class="bp-select" style="width:auto">
                    <option value="">Wszyscy</option>
                    {''.join(f'<option value="{d}">{d}</option>' for d in dostawcy)}
                </select>
            </div>
        </div>
        <div style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap">
            <button onclick="sortTable(9,'num');this.parentElement.querySelectorAll('button').forEach(b=>{{b.style.background='rgba(19,19,21,0.8)';b.style.borderColor='rgba(255,255,255,0.06)'}});this.style.background='rgba(143,245,255,0.08)';this.style.borderColor='rgba(143,245,255,0.3)'" class="bp-filter-btn"><span class="material-icons-round" style="font-size:13px;vertical-align:-2px;margin-right:4px">local_fire_department</span>Najszybciej schodzace</button>
            <button onclick="sortTable(5,'num');this.parentElement.querySelectorAll('button').forEach(b=>{{b.style.background='rgba(19,19,21,0.8)';b.style.borderColor='rgba(255,255,255,0.06)'}});this.style.background='rgba(143,245,255,0.08)';this.style.borderColor='rgba(143,245,255,0.3)'" class="bp-filter-btn"><span class="material-icons-round" style="font-size:13px;vertical-align:-2px;margin-right:4px">payments</span>Najwiekszy przychod</button>
            <button onclick="sortTable(6,'num');this.parentElement.querySelectorAll('button').forEach(b=>{{b.style.background='rgba(19,19,21,0.8)';b.style.borderColor='rgba(255,255,255,0.06)'}});this.style.background='rgba(143,245,255,0.08)';this.style.borderColor='rgba(143,245,255,0.3)'" class="bp-filter-btn"><span class="material-icons-round" style="font-size:13px;vertical-align:-2px;margin-right:4px">trending_up</span>Najwiekszy zysk</button>
            <button onclick="sortTable(7,'num');this.parentElement.querySelectorAll('button').forEach(b=>{{b.style.background='rgba(19,19,21,0.8)';b.style.borderColor='rgba(255,255,255,0.06)'}});this.style.background='rgba(143,245,255,0.08)';this.style.borderColor='rgba(143,245,255,0.3)'" class="bp-filter-btn"><span class="material-icons-round" style="font-size:13px;vertical-align:-2px;margin-right:4px">emoji_events</span>Najlepsze ROI</button>
        </div>
        <div style="overflow-x:auto;">
        <table class="bp-table" id="paletyTable">
            <thead>
                <tr>
                    <th onclick="sortTable(0,'str')">Paleta <span class="sort-arrow"></span></th>
                    <th onclick="sortTable(1,'str')">Dostawca <span class="sort-arrow"></span></th>
                    <th onclick="sortTable(2,'str')">Data <span class="sort-arrow"></span></th>
                    <th onclick="sortTable(3,'num')">Koszt <span class="sort-arrow"></span></th>
                    <th onclick="sortTable(4,'num')">Koszt/szt <span class="sort-arrow"></span></th>
                    <th onclick="sortTable(5,'num')">Przychod <span class="sort-arrow"></span></th>
                    <th onclick="sortTable(6,'num')">Zysk <span class="sort-arrow"></span></th>
                    <th onclick="sortTable(7,'num')">ROI <span class="sort-arrow"></span></th>
                    <th onclick="sortTable(8,'num')">Prognoza <span class="sort-arrow"></span></th>
                    <th onclick="sortTable(9,'num')" title="Sztuk sprzedanych na dzień, liczone od daty pierwszej sprzedaży z tej palety">Tempo <span class="sort-arrow"></span></th>
                    <th onclick="sortTable(10,'num')">Postep <span class="sort-arrow"></span></th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
                {''.join(f"""
                <tr onclick="window.location='/palety/{p['id']}'" data-dostawca="{p['dostawca']}" data-vals="{p['nazwa'][:30]}|{p['dostawca']}|{p['data']}|{p['koszt']:.2f}|{p['koszt_szt']:.2f}|{p['przychod']:.2f}|{p['zysk']:.2f}|{p['roi']:.2f}|{p['prognoza']:.2f}|{p['tempo']:.2f}|{p['procent']:.2f}" style="position:relative">
                    <td style="padding-left:14px"><div class="bp-accent-bar" style="background:{'var(--bp-lime)' if p['roi'] >= 50 else 'var(--bp-cyan)' if p['roi'] >= 0 else 'var(--bp-pink)'}"></div><strong style="font-family:'Space Grotesk',sans-serif;font-weight:600;font-size:0.85rem">{p['nazwa'][:30]}</strong></td>
                    <td><span style="color:var(--bp-muted);font-size:0.8rem">{p['dostawca']}</span></td>
                    <td><span style="color:var(--bp-muted);font-size:0.8rem;font-variant-numeric:tabular-nums">{p['data']}</span></td>
                    <td><span class="bp-value" style="font-size:0.82rem">{p['koszt']:,.0f} zl</span></td>
                    <td><span class="bp-value" style="font-size:0.82rem;color:var(--bp-muted)">{p['koszt_szt']:,.0f} zl</span></td>
                    <td><span class="bp-value" style="font-size:0.82rem;color:var(--bp-lime)">{p['przychod']:,.0f} zl</span></td>
                    <td><span class="bp-value" style="font-size:0.82rem;color:{'var(--bp-lime)' if p['zysk'] >= 0 else 'var(--bp-pink)'}">{p['zysk']:,.0f} zl</span></td>
                    <td><span class="bp-roi-badge {'bp-roi-pos' if p['roi'] >= 0 else 'bp-roi-neg'}">{p['roi']:.0f}%</span></td>
                    <td><span class="bp-value" style="font-size:0.82rem;color:{'var(--bp-lime)' if p['prognoza'] >= 0 else 'var(--bp-pink)'};opacity:0.7;font-style:italic">{p['prognoza']:,.0f} zl</span></td>
                    <td><span class="bp-value" style="font-size:0.82rem;color:{'var(--bp-lime)' if p['tempo'] >= 1 else 'var(--bp-cyan)' if p['tempo'] >= 0.3 else 'var(--bp-muted)'}">{p['tempo']:.1f}/d</span></td>
                    <td style="min-width:90px">
                        <div class="bp-progress">
                            <div class="bp-progress-fill" style="width:{p['procent']:.0f}%"></div>
                        </div>
                        <small style="color:var(--bp-muted);font-family:'Manrope',sans-serif;font-size:10px">{p['sprzedanych']}/{p['wszystkich']} szt.</small>
                    </td>
                    <td><span class="bp-status" style="background:{'rgba(202,253,0,0.1)' if 'zakonczona' in p['status'] and p['zysk'] >= 0 else 'rgba(255,107,155,0.1)' if 'zakonczona' in p['status'] else 'rgba(143,245,255,0.08)'};color:{'var(--bp-lime)' if 'zakonczona' in p['status'] and p['zysk'] >= 0 else 'var(--bp-pink)' if 'zakonczona' in p['status'] else 'var(--bp-cyan)'}">{p['status']}</span></td>
                </tr>
                """ for p in palety_stats) if palety_stats else '<tr><td colspan="12" style="text-align:center;color:var(--bp-muted);padding:40px;font-family:Manrope,sans-serif">Brak palet w bazie</td></tr>'}
            </tbody>
        </table>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js" integrity="sha384-jb8JQMbMoBUzgWatfe6COACi2ljcDdZQ2OxczGA3bGNeWe+6DChMTBJemed7ZnvJ" crossorigin="anonymous"></script>
    <script>
        // Wykres ROI
        new Chart(document.getElementById('roiChart'), {{
            type: 'bar',
            data: {{
                labels: {chart_labels},
                datasets: [{{
                    label: 'ROI %',
                    data: {chart_roi},
                    backgroundColor: {[f"'{'rgba(202,253,0,0.7)' if r >= 0 else 'rgba(255,107,155,0.7)'}'" for r in chart_roi]},
                    borderColor: {[f"'{'#cafd00' if r >= 0 else '#ff6b9b'}'" for r in chart_roi]},
                    borderWidth: 1,
                    borderRadius: 2
                }}]
            }},
            options: {{
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    x: {{ grid: {{ color: 'rgba(255,255,255,0.04)' }}, ticks: {{ color: 'rgba(255,255,255,0.35)', font: {{ family: 'Space Grotesk' }}, callback: v => v + '%' }} }},
                    y: {{ grid: {{ display: false }}, ticks: {{ color: 'rgba(255,255,255,0.5)', font: {{ family: 'Manrope', size: 11 }} }} }}
                }}
            }}
        }});

        // Wykres kumulacyjny zysku
        var cumCtx = document.getElementById('cumChart').getContext('2d');
        var cumGradient = cumCtx.createLinearGradient(0, 0, cumCtx.canvas.width, 0);
        cumGradient.strokeStyle = cumGradient;
        cumGradient.addColorStop(0, '#8ff5ff');
        cumGradient.addColorStop(1, '#ff6b9b');
        var cumFill = cumCtx.createLinearGradient(0, 0, 0, 300);
        cumFill.addColorStop(0, 'rgba(143,245,255,0.15)');
        cumFill.addColorStop(1, 'rgba(143,245,255,0)');
        new Chart(cumCtx, {{
            type: 'line',
            data: {{
                labels: {cum_dates},
                datasets: [{{
                    label: 'Zysk kumulacyjny (zl)',
                    data: {cum_zysk},
                    borderColor: cumGradient,
                    backgroundColor: cumFill,
                    fill: true,
                    tension: 0.35,
                    pointRadius: 3,
                    pointBackgroundColor: '#8ff5ff',
                    pointBorderColor: '#8ff5ff',
                    borderWidth: 2
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    y: {{ grid: {{ color: 'rgba(255,255,255,0.04)' }}, ticks: {{ color: 'rgba(255,255,255,0.35)', font: {{ family: 'Space Grotesk' }}, callback: v => v + ' zl' }} }},
                    x: {{ grid: {{ display: false }}, ticks: {{ color: 'rgba(255,255,255,0.35)', font: {{ family: 'Manrope', size: 10 }}, maxRotation: 45 }} }}
                }}
            }}
        }});

            // Sortowanie tabeli
            let sortCol = -1, sortAsc = true;
            function sortTable(col, type) {{
                const table = document.getElementById('paletyTable');
                const tbody = table.querySelector('tbody');
                const rows = Array.from(tbody.querySelectorAll('tr'));
                if (sortCol === col) {{ sortAsc = !sortAsc; }} else {{ sortCol = col; sortAsc = true; }}
                rows.sort((a, b) => {{
                    const av = a.dataset.vals.split('|')[col] || '';
                    const bv = b.dataset.vals.split('|')[col] || '';
                    let cmp;
                    if (type === 'num') {{ cmp = parseFloat(av||0) - parseFloat(bv||0); }}
                    else {{ cmp = av.localeCompare(bv, 'pl'); }}
                    return sortAsc ? cmp : -cmp;
                }});
                rows.forEach(r => tbody.appendChild(r));
                // Aktualizuj strzalki
                table.querySelectorAll('.sort-arrow').forEach((s, i) => {{
                    s.textContent = i === col ? (sortAsc ? '\\u25B2' : '\\u25BC') : '';
                }});
            }}

            // Filtr dostawcy + wyszukiwanie
            function filterTable() {{
                const dost = document.getElementById('dostawcaFilter').value;
                const search = (document.getElementById('searchInput').value || '').toLowerCase();
                const rows = document.querySelectorAll('#paletyTable tbody tr');
                rows.forEach(r => {{
                    const matchDost = !dost || r.dataset.dostawca === dost;
                    const matchSearch = !search || (r.dataset.vals || '').toLowerCase().includes(search);
                    r.style.display = (matchDost && matchSearch) ? '' : 'none';
                }});
            }}
        </script>
    '''
    return render(html, 'Bilans Palet')



@analityka_bp.route('/analityka/kategorie')
def analityka_kategorie():
    """Masowa edycja kategorii produktów"""
    from modules.database import get_db, get_config_cached
    from modules.shared import auto_kategoryzuj, KATEGORIE_DISPLAY

    conn = get_db()

    # Pobierz wszystkie produkty z ich kategoriami
    produkty = conn.execute('''
        SELECT id, nazwa, kategoria, cena_allegro, status, paleta_id
        FROM produkty
        ORDER BY kategoria, nazwa
    ''').fetchall()


    # Użyj globalnego słownika kategorii
    kategorie = KATEGORIE_DISPLAY

    produkty_html = ''
    for p in produkty:
        kat = p['kategoria'] or 'inne'
        sugerowana = auto_kategoryzuj(p['nazwa'])
        zmiana = sugerowana != kat

        produkty_html += f'''
        <tr data-id="{p['id']}" data-kat="{kat}">
            <td><input type="checkbox" class="produkt-check" value="{p['id']}"></td>
            <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{p['nazwa']}">{(p['nazwa'] or '')[:50]}</td>
            <td>{p['cena_allegro']:,.0f} zł</td>
            <td>
                <select class="kat-select form-control" data-id="{p['id']}" style="padding:6px;width:auto">
                    {''.join(f'<option value="{k}" {"selected" if kat == k else ""}>{v}</option>' for k, v in kategorie.items())}
                </select>
            </td>
            <td>{'<span style="color:var(--orange)"><span class=material-symbols-outlined>lightbulb</span> ' + kategorie.get(sugerowana, sugerowana) + '</span>' if zmiana else '<span style="color:var(--green)">✓</span>'}</td>
        </tr>
        '''

    html = f'''
    <style>
        .kat-table {{ width:100%;border-collapse:collapse }}
        .kat-table th, .kat-table td {{ padding:12px;text-align:left;border-bottom:1px solid var(--border) }}
        .kat-table th {{ color:var(--text-muted);font-weight:500 }}
        .kat-table tr:hover {{ background:var(--accent-soft) }}
    </style>

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
            <a href="/analityka" class="btn btn-sm btn-primary" style="text-decoration:none">← Powrót</a>
        </div>

        <div class="stat-row" style="grid-template-columns:1fr 1fr;margin-bottom:20px">
            <div class="stat-box">
                <div class="stat-val purple">{len(produkty)}</div>
                <div class="stat-lbl">PRODUKTÓW</div>
            </div>
            <div class="stat-box">
                <div class="stat-val orange">{sum(1 for p in produkty if auto_kategoryzuj(p['nazwa']) != (p['kategoria'] or 'inne'))}</div>
                <div class="stat-lbl">DO ZMIANY</div>
            </div>
        </div>

        <div class="card" style="margin-bottom:20px">
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer"><input type="checkbox" id="selectAll"> Zaznacz wszystkie</label>
                <span style="color:var(--text-muted)">|</span>
                <span>Ustaw zaznaczonym:</span>
                <select id="bulkKategoria" class="form-control" style="width:auto;padding:8px">
                    {''.join(f'<option value="{k}">{v}</option>' for k, v in kategorie.items())}
                </select>
                <button class="btn btn-sm btn-purple" onclick="bulkUpdate()"><span class=material-symbols-outlined>edit_note</span> Zastosuj</button>
                <span style="color:var(--text-muted)">|</span>
                <button class="btn btn-sm btn-success" onclick="autoKategoryzuj()"><span class=material-symbols-outlined>smart_toy</span> Auto-kategoryzuj wszystkie</button>
            </div>
        </div>

        <div class="card" style="overflow-x:auto">
        <table class="kat-table">
            <thead>
                <tr>
                    <th style="width:40px"></th>
                    <th>Nazwa produktu</th>
                    <th>Cena</th>
                    <th>Kategoria</th>
                    <th>Sugestia AI</th>
                </tr>
            </thead>
            <tbody>
                {produkty_html}
            </tbody>
        </table>
        </div>

        <script>
            document.getElementById('selectAll').addEventListener('change', function() {{
                document.querySelectorAll('.produkt-check').forEach(cb => cb.checked = this.checked);
            }});

            // Zmiana pojedynczej kategorii
            document.querySelectorAll('.kat-select').forEach(select => {{
                select.addEventListener('change', function() {{
                    const id = this.dataset.id;
                    const kat = this.value;
                    fetch('/analityka/kategorie/update', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{id: id, kategoria: kat}})
                    }}).then(r => r.json()).then(data => {{
                        if (data.ok) {{
                            this.style.borderColor = 'var(--green)';
                            setTimeout(() => this.style.borderColor = 'var(--border)', 1000);
                        }}
                    }});
                }});
            }});

            function bulkUpdate() {{
                const ids = [...document.querySelectorAll('.produkt-check:checked')].map(cb => cb.value);
                if (ids.length === 0) {{ alert('Zaznacz produkty'); return; }}

                const kat = document.getElementById('bulkKategoria').value;

                fetch('/analityka/kategorie/bulk-update', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{ids: ids, kategoria: kat}})
                }}).then(r => r.json()).then(data => {{
                    if (data.ok) {{
                        alert('Zaktualizowano ' + data.count + ' produktów');
                        location.reload();
                    }}
                }});
            }}

            function autoKategoryzuj() {{
                if (!confirm('Automatycznie przypisać kategorie do wszystkich produktów?')) return;

                fetch('/analityka/kategorie/auto', {{
                    method: 'POST'
                }}).then(r => r.json()).then(data => {{
                    if (data.ok) {{
                        alert('Zaktualizowano ' + data.count + ' produktów');
                        location.reload();
                    }}
                }});
            }}
        </script>
    '''
    return render(html, 'Edycja kategorii')



@analityka_bp.route('/analityka/kategorie/update', methods=['POST'])
def analityka_kategorie_update():
    """Aktualizuj kategorię pojedynczego produktu"""
    from modules.database import get_db
    import json

    data = request.get_json()
    produkt_id = data.get('id')
    kategoria = data.get('kategoria')

    conn = get_db()
    conn.execute('UPDATE produkty SET kategoria = ? WHERE id = ?', (kategoria, produkt_id))
    conn.commit()

    return jsonify({'ok': True})



@analityka_bp.route('/analityka/kategorie/bulk-update', methods=['POST'])
def analityka_kategorie_bulk_update():
    """Aktualizuj kategorie wielu produktów"""
    from modules.database import get_db

    data = request.get_json()
    ids = data.get('ids', [])
    kategoria = data.get('kategoria')

    if not ids:
        return jsonify({'ok': False, 'error': 'Brak produktów'})

    conn = get_db()
    placeholders = ','.join('?' * len(ids))
    conn.execute('UPDATE produkty SET kategoria = ? WHERE id IN (' + placeholders + ')', [kategoria] + ids)
    conn.commit()

    return jsonify({'ok': True, 'count': len(ids)})



@analityka_bp.route('/analityka/kategorie/auto', methods=['POST'])
def analityka_kategorie_auto():
    """Automatycznie kategoryzuj wszystkie produkty"""
    from modules.database import get_db
    from modules.shared import auto_kategoryzuj

    conn = get_db()
    produkty = conn.execute('SELECT id, nazwa, kategoria FROM produkty').fetchall()

    print(f"\n[SEARCH] Auto-kategoryzacja: {len(produkty)} produktów w bazie")

    count = 0
    stats = {}  # Statystyki kategorii

    for p in produkty:
        nazwa = p['nazwa'] or ''
        obecna_kat = p['kategoria'] or 'inne'
        nowa_kat = auto_kategoryzuj(nazwa)

        # Zlicz statystyki
        stats[nowa_kat] = stats.get(nowa_kat, 0) + 1

        # Aktualizuj jeśli kategoria jest inna
        if nowa_kat != obecna_kat:
            conn.execute('UPDATE produkty SET kategoria = ? WHERE id = ?', (nowa_kat, p['id']))
            count += 1
            print(f"  [LABEL] [{p['id']}] {obecna_kat} → {nowa_kat}: {nazwa[:50]}")

    # Pokaż pierwsze 5 nazw produktów dla diagnostyki
    print(f"\n[LIST_ALT] Przykładowe nazwy produktów:")
    for p in produkty[:5]:
        print(f"  - {p['nazwa'][:60] if p['nazwa'] else '(brak nazwy)'}")

    print(f"\n[BAR_CHART] Statystyki kategorii:")
    for kat, cnt in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {kat}: {cnt}")

    conn.commit()

    print(f"\n[CHECK_CIRCLE] Zaktualizowano {count}/{len(produkty)} produktów")
    return jsonify({'ok': True, 'count': count, 'total': len(produkty)})



@analityka_bp.route('/analityka/okazje')
def analityka_okazje():
    """Strona TOP Okazje - produkty z najwyzszym scoring + analiza Perplexity"""
    from modules.database import get_db, get_config, get_config_cached
    import json as _json
    conn = get_db()
    miesiac = datetime.now().strftime('%Y-%m')

    has_trendy = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trendy'"
    ).fetchone()

    okazje_list = []
    wszystkie_list = []
    ostatnia_analiza = 'brak danych'
    perplexity_odpowiedz = None
    perplexity_citations = []
    perplexity_data = None

    if has_trendy:
        # Migracja inline - dodaj kolumny do trendy jeśli brak (stare bazy)
        for _col, _typ in [('nazwa','TEXT'),('kategoria','TEXT'),('dostawca','TEXT'),('koszt','REAL DEFAULT 0')]:
            try:
                conn.execute(f'ALTER TABLE trendy ADD COLUMN {_col} {_typ}')
                conn.commit()
            except:
                pass

        okazje_rows = conn.execute("""
            SELECT t.produkt_id,
                   COALESCE(p.nazwa, t.nazwa, 'Brak nazwy') as nazwa,
                   COALESCE(p.kategoria, t.kategoria, 'inne') as kategoria,
                   COALESCE(p.dostawca, t.dostawca, '') as dostawca,
                   t.sprzedaz_szt, t.przychod, COALESCE(t.koszt,0) as koszt,
                   t.roi, t.trend_mm, t.okazja_score, t.created_at
            FROM trendy t LEFT JOIN produkty p ON t.produkt_id = p.id
            WHERE t.miesiac = ? AND t.okazja_score >= 6
            ORDER BY t.okazja_score DESC, t.przychod DESC
        """, (miesiac,)).fetchall()

        wszystkie_rows = conn.execute("""
            SELECT t.produkt_id,
                   COALESCE(p.nazwa, t.nazwa, 'Brak nazwy') as nazwa,
                   COALESCE(p.kategoria, t.kategoria, 'inne') as kategoria,
                   t.sprzedaz_szt, t.przychod, t.roi, t.trend_mm, t.okazja_score
            FROM trendy t LEFT JOIN produkty p ON t.produkt_id = p.id
            WHERE t.miesiac = ?
            ORDER BY t.okazja_score DESC LIMIT 50
        """, (miesiac,)).fetchall()

        last = conn.execute("SELECT MAX(created_at) as ts FROM trendy").fetchone()
        ostatnia_analiza = last['ts'] if last and last['ts'] else 'brak danych'

        # Cache odpowiedzi Perplexity
        szukaj_odpowiedz = None
        szukaj_citations = []
        szukaj_data = None
        try:
            has_cache = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='perplexity_cache'"
            ).fetchone()
            if has_cache:
                cache = conn.execute(
                    "SELECT odpowiedz, citations, created_at FROM perplexity_cache WHERE klucz = ? ORDER BY created_at DESC LIMIT 1",
                    (f'okazje_{miesiac}',)
                ).fetchone()
                if cache:
                    perplexity_odpowiedz = cache['odpowiedz']
                    perplexity_data = cache['created_at']
                    try:
                        perplexity_citations = _json.loads(cache['citations'] or '[]')
                    except:
                        perplexity_citations = []
                cache2 = conn.execute(
                    "SELECT odpowiedz, citations, created_at FROM perplexity_cache WHERE klucz = ? ORDER BY created_at DESC LIMIT 1",
                    (f'szukaj_{miesiac}',)
                ).fetchone()
                if cache2:
                    szukaj_odpowiedz = cache2['odpowiedz']
                    szukaj_data = cache2['created_at']
                    try:
                        szukaj_citations = _json.loads(cache2['citations'] or '[]')
                    except:
                        szukaj_citations = []
        except:
            szukaj_odpowiedz = None

        okazje_list = [dict(r) for r in okazje_rows]
        wszystkie_list = [dict(r) for r in wszystkie_rows]


    # Sprawdź czy jest klucz Perplexity
    perplexity_key = get_config('perplexity_api_key', '')
    has_perplexity = bool(perplexity_key)
    perplexity_model = get_config('perplexity_model', 'sonar-pro')
    # Upgrade: sonar bez suffixu to słaby model - zamień na sonar-pro
    if perplexity_model == 'sonar':
        perplexity_model = 'sonar-pro'

    def trend_cls(v):
        return 'var(--green)' if (v or 0) > 0 else 'var(--red)'
    def roi_cls(v):
        return 'var(--green)' if (v or 0) > 0 else 'var(--red)'
    def sign(v):
        return '+' if (v or 0) > 0 else ''

    okazje_html = ''
    for r in okazje_list:
        score = r.get('okazja_score', 0)
        badge_bg = 'var(--green)' if score >= 9 else 'var(--orange)' if score >= 7 else 'var(--blue)'
        okazje_html += f"""
        <div class="card" style="margin-bottom:10px;padding:15px" onmouseover="this.style.borderColor='var(--orange)'" onmouseout="this.style.borderColor='var(--border)'">
          <div style='display:flex;align-items:center;gap:12px;margin-bottom:10px'>
            <div style='background:{badge_bg};color:#000;font-weight:800;font-size:0.85rem;border-radius:8px;padding:4px 10px;min-width:52px;text-align:center'>★{score}/10</div>
            <div style='flex:1'>
              <div style='font-weight:600'>{r.get('nazwa') or 'Produkt #' + str(r.get('produkt_id','?'))}</div>
              <div style='color:var(--text-muted);font-size:0.78rem'>{r.get('kategoria') or 'inne'} · <span class="dostawca-name">{r.get('dostawca') or ''}</span></div>
            </div>
          </div>
          <div style='display:flex;gap:20px;flex-wrap:wrap;font-size:0.85rem'>
            <div><div style='color:var(--text-muted);font-size:0.72rem'>SPRZEDANO</div>{r.get('sprzedaz_szt',0)} szt</div>
            <div><div style='color:var(--text-muted);font-size:0.72rem'>PRZYCHÓD</div>{(r.get('przychod') or 0):,.0f} zł</div>
            <div><div style='color:var(--text-muted);font-size:0.72rem'>ROI</div><span style='color:{roi_cls(r.get("roi"))};font-weight:600'>{(r.get("roi") or 0):.0f}%</span></div>
            <div><div style='color:var(--text-muted);font-size:0.72rem'>TREND M/M</div><span style='color:{trend_cls(r.get("trend_mm"))};font-weight:600'>{sign(r.get("trend_mm"))}{(r.get("trend_mm") or 0):.0f}%</span></div>
          </div>
        </div>"""

    if not okazje_html:
        okazje_html = "<div class='card' style='text-align:center;color:var(--text-muted)'>Brak okazji (score ≥ 6) w tym miesiącu.<br><br>Uruchom: <code style='background:var(--bg);padding:4px 8px;border-radius:6px'>python analyze_trends.py</code></div>"

    wszystkie_html = ''
    for r in wszystkie_list:
        score = r.get('okazja_score', 0)
        badge_bg = 'var(--green)' if score >= 9 else 'var(--orange)' if score >= 7 else 'var(--blue)' if score >= 5 else 'var(--text-muted)'
        wszystkie_html += f"""
        <div style='background:var(--bg-card);border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:8px;display:flex;align-items:center;gap:12px'>
          <div style='background:{badge_bg};color:#000;font-weight:700;font-size:0.8rem;border-radius:6px;padding:3px 8px;min-width:40px;text-align:center'>{score}/10</div>
          <div style='flex:1;font-size:0.85rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{r.get('nazwa') or 'Produkt'}</div>
          <div style='display:flex;gap:15px;font-size:0.8rem;white-space:nowrap'>
            <span>{r.get('sprzedaz_szt',0)} szt</span>
            <span style='color:{roi_cls(r.get("roi"))}'>{(r.get("roi") or 0):.0f}% ROI</span>
            <span style='color:{trend_cls(r.get("trend_mm"))}'>{sign(r.get("trend_mm"))}{(r.get("trend_mm") or 0):.0f}%</span>
          </div>
        </div>"""

    if not wszystkie_html:
        wszystkie_html = "<div style='color:var(--text-muted);padding:20px;text-align:center'>Brak danych. Uruchom: <code>python analyze_trends.py</code></div>"

    # === SEKCJA LIVE SCRAPER (Warrington + Jobalots + Szukaj palet) ===
    live_scraper_section = """
        <div class='card' style='border-color:rgba(14,165,233,0.25);margin-bottom:20px'>
          <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:14px'>
            <div style='color:var(--cyan);font-weight:700;font-size:1rem'><span class=material-symbols-outlined>fiber_manual_record</span> Aktualne palety — na żywo</div>
            <div style='color:var(--text-muted);font-size:0.75rem'>dane pobierane bezpośrednio ze stron dostawców</div>
          </div>
          <div style='display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px'>
            <div style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px'>
              <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:10px'>
                <a href='https://warrington.store/products/new' target='_blank' style='color:var(--cyan);font-weight:600;font-size:0.9rem;text-decoration:none'><span class=material-symbols-outlined>store</span> Warrington.store ↗</a>
                <button onclick='loadWarrington()' id='btn-warrington' style='background:var(--blue-soft);color:var(--blue);border:1px solid rgba(59,130,246,0.3);border-radius:6px;padding:4px 12px;font-size:0.75rem;cursor:pointer'>▶ Załaduj</button>
              </div>
              <div id='warrington-results' style='color:var(--text-muted);font-size:0.8rem'>Kliknij "Załaduj" aby pobrać aktualne palety</div>
            </div>
            <div style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px'>
              <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:10px'>
                <a href='https://jobalots.com/pl/pages/products-on-auction?page=1&currency=pln&type=pallets' target='_blank' style='color:var(--orange);font-weight:600;font-size:0.9rem;text-decoration:none'><span class=material-symbols-outlined>store</span> Jobalots.com ↗</a>
                <button onclick='loadJobalots()' id='btn-jobalots' style='background:var(--yellow-soft);color:var(--orange);border:1px solid rgba(245,158,11,0.3);border-radius:6px;padding:4px 12px;font-size:0.75rem;cursor:pointer'>▶ Załaduj</button>
              </div>
              <div id='jobalots-results' style='color:var(--text-muted);font-size:0.8rem'>Kliknij "Załaduj" aby pobrać aukcje palet</div>
            </div>
          </div>
          <div id='szukaj-panel' style='background:var(--bg);border:1px solid rgba(34,197,94,0.2);border-radius:10px;padding:14px'>
            <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:10px'>
              <div style='color:var(--green);font-weight:600;font-size:0.9rem'><span class=material-symbols-outlined>shopping_cart</span> Szukaj palet pod mój profil (AI)</div>
              <div style='color:var(--text-muted);font-size:0.73rem'>Perplexity analizuje Twój profil i szuka najlepszych ofert</div>
            </div>
            %%SZUKAJ_PLACEHOLDER%%
          </div>
        </div>
        <script>
        function loadWarrington() {
          var btn = document.getElementById('btn-warrington');
          var res = document.getElementById('warrington-results');
          btn.disabled = true; btn.textContent = ' Ładowanie...';
          fetch('/analityka/okazje/scrape-warrington')
            .then(r => r.json())
            .then(d => {
              btn.disabled = false; btn.textContent = ' Odśwież';
              if (!d.ok) { res.innerHTML = '<span style="color:var(--red)">Błąd: ' + d.error + '</span>'; return; }
              if (!d.products.length) { res.innerHTML = '<span style="color:var(--text-muted)">Brak produktów</span>'; return; }
              var html = '<div style="max-height:280px;overflow-y:auto">';
              d.products.forEach(function(p) {
                var priceStr = p.price_text || (p.price ? '£' + p.price.toFixed(0) : '');
                html += '<a href="' + p.url + '" target="_blank" style="display:block;padding:6px 8px;margin:3px 0;background:var(--blue-soft);border:1px solid rgba(59,130,246,0.15);border-radius:6px;color:var(--text);text-decoration:none;font-size:0.8rem">';
                html += p.title;
                if (priceStr && priceStr !== '?' && priceStr !== 'kategoria') html += ' <span style="color:var(--blue);font-weight:600">' + priceStr + '</span>';
                html += ' ↗</a>';
              });
              html += '</div><div style="color:var(--text-muted);font-size:0.72rem;margin-top:6px">Źródło: ' + (d.source||'') + ' | Łącznie: ' + d.total + '</div>';
              res.innerHTML = html;
            })
            .catch(e => { btn.disabled=false; btn.textContent='▶ Załaduj'; res.innerHTML='<span style="color:var(--red)">Błąd połączenia</span>'; });
        }
        function loadJobalots() {
          var btn = document.getElementById('btn-jobalots');
          var res = document.getElementById('jobalots-results');
          btn.disabled = true; btn.textContent = ' Ładowanie...';
          fetch('/analityka/okazje/scrape-jobalots')
            .then(r => r.json())
            .then(d => {
              btn.disabled = false; btn.textContent = ' Odśwież';
              if (!d.ok) { res.innerHTML = '<span style="color:var(--red)">Błąd: ' + (d.error||'') + '</span>' + (d.fallback_url ? '<br><a href="'+d.fallback_url+'" target="_blank" style="color:var(--orange)">→ Otwórz Jobalots ↗</a>' : ''); return; }
              if (d.fallback_url && !d.products.length) {
                res.innerHTML = '<div style="color:var(--orange);font-size:0.8rem">' + (d.note||'') + '</div><a href="' + d.fallback_url + '" target="_blank" style="color:var(--orange);font-size:0.8rem">→ Otwórz Jobalots ↗</a>';
                return;
              }
              if (!d.products.length) { res.innerHTML = '<span style="color:var(--text-muted)">Brak produktów</span>'; return; }
              var html = '';
              if (d.note) html += '<div style="color:var(--orange);font-size:0.73rem;margin-bottom:6px">' + d.note + '</div>';
              html += '<div style="max-height:280px;overflow-y:auto">';
              d.products.forEach(function(p) {
                html += '<a href="' + p.url + '" target="_blank" style="display:block;padding:6px 8px;margin:3px 0;background:var(--yellow-soft);border:1px solid rgba(245,158,11,0.15);border-radius:6px;color:var(--text);text-decoration:none;font-size:0.78rem">';
                html += '<div style="font-weight:600;margin-bottom:2px">';
                if (p.tag) html += p.tag + ' ';
                html += p.title;
                if (p.discount > 30) html += ' <span style="background:var(--red);color:#fff;padding:1px 5px;border-radius:4px;font-size:0.65rem;font-weight:700">-' + p.discount + '%</span>';
                html += '</div>';
                html += '<div style="display:flex;gap:8px;flex-wrap:wrap;font-size:0.72rem;color:var(--text-secondary)">';
                if (p.price_text) html += '<span style="color:var(--orange);font-weight:700">' + p.price_text + '</span>';
                if (p.rrp) html += '<span style="text-decoration:line-through;color:var(--text-muted)">' + Math.round(p.rrp) + ' RRP</span>';
                if (p.qty) html += '<span>' + p.qty + ' szt</span>';
                if (p.bid_count) html += '<span>' + p.bid_count + ' ofert</span>';
                if (p.end_at) html += '<span>⏰ ' + p.end_at + '</span>';
                html += '</div></a>';
              });
              html += '</div><div style="color:var(--text-muted);font-size:0.72rem;margin-top:6px">Łącznie: ' + d.total + ' palet</div>';
              res.innerHTML = html;
            })
            .catch(e => { btn.disabled=false; btn.textContent='▶ Załaduj'; res.innerHTML='<span style="color:var(--red)">Błąd połączenia</span>'; });
        }
        </script>"""

    # === SEKCJA PERPLEXITY ===
    if not has_trendy:
        perplexity_section = ""
        live_scraper_section = live_scraper_section.replace('%%SZUKAJ_PLACEHOLDER%%',
            "<div style='color:var(--text-muted);font-size:0.73rem'>Dodaj klucz Perplexity API poniżej</div>")
    elif not has_perplexity:
        live_scraper_section = live_scraper_section.replace('%%SZUKAJ_PLACEHOLDER%%',
            "<div style='color:var(--text-muted);font-size:0.73rem'>Dodaj klucz Perplexity API poniżej aby aktywować</div>")
        perplexity_section = f"""
        <div class='card' style='border-color:rgba(59,130,246,0.25);margin-bottom:20px'>
          <div style='color:var(--blue);font-weight:700;font-size:1rem;margin-bottom:12px'><span class=material-symbols-outlined>smart_toy</span> Analiza rynkowa (Perplexity AI)</div>
          <div style='color:var(--text-muted);font-size:0.85rem;margin-bottom:12px'>Dodaj klucz API Perplexity żeby otrzymać analizę rynkową produktów na podstawie Twoich trendów sprzedaży.</div>
          <form method='POST' action='/analityka/okazje/set-perplexity-key' style='display:flex;gap:8px;flex-wrap:wrap'>
            <input type='hidden' name='csrf_token' value='{generate_csrf()}'>
            <input type='password' name='api_key' placeholder='pplx-xxxxxxxxxxxxxxxx' class='form-control' style='flex:1;min-width:220px'>
            <button type='submit' class='btn btn-sm btn-primary' style='border:none;cursor:pointer'>Zapisz klucz</button>
          </form>
          <div style='color:var(--text-muted);font-size:0.75rem;margin-top:8px'>Klucz Perplexity → <a href='https://www.perplexity.ai/settings/api' target='_blank' style='color:var(--blue)'>perplexity.ai/settings/api</a></div>
        </div>"""
    else:
        # Jest klucz — pokaż przycisk analizy i ewentualny cache
        cached_html = ""
        if perplexity_odpowiedz:
            _left_odpowiedz = perplexity_odpowiedz
            _left_citations = perplexity_citations
            _left_data = perplexity_data
        else:
            _left_odpowiedz = None
            _left_citations = []
            _left_data = None

        # Szukaj cache HTML
        import html as _html_mod2
        def _cache_block(odp, cits, ts, refresh_url, title, icon):
            if not odp:
                return "<div style='color:var(--text-muted);font-size:0.85rem;margin-top:10px'>Brak analizy — kliknij przycisk powyżej.</div>"
            import re as _re, html as _h
            safe = _h.escape(odp)
            # Usuń referencje [1][2] itp.
            safe = _re.sub(r'\[(\d+)\]', '', safe)
            # Nagłówki → kolorowe karty z separacją
            safe = _re.sub(r'(?m)^###\s+(.+)$', r'</div><div style="background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:12px;margin:14px 0 8px"><div style="color:var(--orange);font-weight:700;font-size:0.95rem;margin-bottom:6px">\1</div><div>', safe)
            safe = _re.sub(r'(?m)^##\s+(.+)$', r'</div><div style="background:var(--bg);border-left:3px solid var(--blue);padding:10px 12px;margin:12px 0 6px;border-radius:0 8px 8px 0"><div style="color:var(--blue);font-weight:700;font-size:0.9rem">\1</div></div><div>', safe)
            # Numerowane palety (1. 2. 3.) → wyróżnione karty
            safe = _re.sub(r'(?m)^(\d+)\.\s+(.+)$', r'<div style="background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:10px 12px;margin:8px 0;position:relative;padding-left:40px"><span style="position:absolute;left:10px;top:10px;background:var(--orange);color:#000;font-weight:800;border-radius:50%;width:22px;height:22px;display:flex;align-items:center;justify-content:center;font-size:0.75rem">\1</span>\2</div>', safe)
            # Linie z "Link:" → duży przycisk z linkiem (PRZED bold i bullet!)
            def _make_link_btn(m):
                url = m.group(1)
                rest = m.group(2) or ''
                rest = _re.sub(r'\*\*', '', rest).strip()
                path = url.split('/')[-1].split('?')[0]
                label = path.replace('-', ' ').replace('_', ' ').title()[:40]
                if not label or len(label) < 3:
                    domain = url.split('/')[2] if len(url.split('/')) > 2 else url
                    label = domain.replace('www.', '')
                return f'<div style="margin:6px 0"><a href="{url}" target="_blank" style="display:inline-block;background:var(--blue);color:#fff;padding:8px 18px;border-radius:8px;text-decoration:none;font-weight:700;font-size:0.85rem"><span class=material-symbols-outlined>link</span> {label} ↗</a> <span style="color:var(--text-muted);font-size:0.73rem">{rest}</span></div>'
            safe = _re.sub(r'(?m)^-\s+\*{0,2}[Ll]ink:?\*{0,2}\s*(https?://[^\s<>&]+)(.*?)$', _make_link_btn, safe)
            # Bold
            safe = _re.sub(r'\*\*(.+?)\*\*', r'<strong style="color:var(--text)">\1</strong>', safe)
            # Pozostałe linki URL → klikalne
            safe = _re.sub(r'(?<!href=")(https?://[^\s<>"&]+)(?!")', r'<a href="\1" target="_blank" style="color:var(--blue);text-decoration:underline;word-break:break-all;font-size:0.8rem">\1</a>', safe)
            # Bullet listy → czytelne elementy
            safe = _re.sub(r'(?m)^[\u2022\-]\s+(.+)$', r'<div style="padding:4px 0 4px 16px;border-left:2px solid var(--border);margin:3px 0;font-size:0.82rem">\1</div>', safe)
            # Separator ---
            safe = _re.sub(r'(?m)^---+$', r'<hr style="border:none;border-top:1px solid var(--border);margin:12px 0">', safe)
            safe = safe.replace('\n', '<br>')
            safe = safe.replace('<div></div>', '').replace('<br><br><br>', '<br>')
            cit_items = ''
            if cits:
                for ci, cv in enumerate(cits[:8]):
                    short = cv[:80] + ('...' if len(cv) > 80 else '')
                    cit_items += f"<a href='{cv}' target='_blank' style='color:var(--blue);font-size:0.72rem;display:block;margin:2px 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>[{ci+1}] {short}</a>"
            cit_html2 = f"<div style='margin-top:10px;padding-top:10px;border-top:1px solid var(--border)'><div style='color:var(--text-muted);font-size:0.72rem;margin-bottom:4px'>Źródła ({len(cits)}):</div>{cit_items}</div>" if cits else ''
            btn = f"<form method='POST' action='{refresh_url}' style='margin:0'><input type='hidden' name='csrf_token' value='{generate_csrf()}'><button type='submit' style='background:var(--bg);color:var(--text-secondary);border:1px solid var(--border);border-radius:6px;padding:3px 10px;font-size:0.72rem;cursor:pointer'><span class=material-symbols-outlined>sync</span> Odśwież</button></form>"
            return f"<div style='background:var(--green-soft);border:1px solid rgba(34,197,94,0.2);border-radius:10px;padding:14px;margin-top:12px'><div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:10px'><div style='color:var(--green);font-size:0.78rem;font-weight:600'><span class=material-symbols-outlined>check_circle</span> {ts}</div>{btn}</div><div style='color:var(--text);font-size:0.83rem;line-height:1.75'>{safe}</div>{cit_html2}</div>"

        cached_html = _cache_block(_left_odpowiedz, _left_citations, _left_data,
            '/analityka/okazje/perplexity-analyze',
            'Analiza sprzedaży', '<span class=material-symbols-outlined>bar_chart</span>')

        szukaj_html_block = _cache_block(szukaj_odpowiedz, szukaj_citations, szukaj_data,
            '/analityka/okazje/perplexity-szukaj',
            'Okazje zakupowe', '<span class=material-symbols-outlined>shopping_cart</span>')

        # Wstaw przycisk "Szukaj" + wyniki do panelu w live_scraper_section
        _szukaj_panel_content = f"""<form method='POST' action='/analityka/okazje/perplexity-szukaj' onsubmit='showLoading(this,"szukaj")'>
                <input type='hidden' name='csrf_token' value='{generate_csrf()}'>
                <button id='btn-szukaj' type='submit' class='btn btn-success' style='border:none;cursor:pointer;font-size:0.82rem'>
                  <span class=material-symbols-outlined>search</span> Szukaj teraz
                </button>
              </form>
              <div id='loading-szukaj' style='display:none;text-align:center;padding:10px;color:var(--green);font-size:0.82rem'>
                <span style='animation:spin 1s linear infinite;display:inline-block'>⏳</span> Szukam palet... (~30-45 sek)
              </div>
              {szukaj_html_block}"""
        live_scraper_section = live_scraper_section.replace('%%SZUKAJ_PLACEHOLDER%%', _szukaj_panel_content)

        perplexity_section = f"""
        <div class='card' style='border-color:rgba(139,92,246,0.25);margin-bottom:20px'>
          <div style='display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:10px'>
            <div style='color:var(--purple);font-weight:700;font-size:1rem'><span class=material-symbols-outlined>smart_toy</span> Perplexity AI</div>
            <div style='display:flex;align-items:center;gap:8px'>
              <form method='POST' action='/analityka/okazje/set-perplexity-model' style='margin:0;display:flex;align-items:center;gap:6px'>
                <input type='hidden' name='csrf_token' value='{generate_csrf()}'>
                <span style='color:var(--text-muted);font-size:0.75rem'>Model:</span>
                <select name='model' onchange='this.form.submit()' class='form-control' style='width:auto;padding:3px 8px;font-size:0.75rem;cursor:pointer'>
                  <option value='sonar-pro' {{'selected' if perplexity_model in ("sonar","sonar-pro") else ""}}>Sonar Pro ⭐ (zalecany)</option>

                  <option value='sonar-reasoning' {{'selected' if perplexity_model=="sonar-reasoning" else ""}}>Sonar Reasoning</option>
                  <option value='sonar-reasoning-pro' {{'selected' if perplexity_model=="sonar-reasoning-pro" else ""}}>Sonar Reasoning Pro</option>
                </select>
              </form>
              <form method='POST' action='/analityka/okazje/remove-perplexity-key' onsubmit="return confirm('Usunąć klucz Perplexity?')" style='margin:0'>
                <input type='hidden' name='csrf_token' value='{generate_csrf()}'>
                <button type='submit' style='background:transparent;color:var(--text-muted);border:none;cursor:pointer;font-size:0.8rem'><span class=material-symbols-outlined>delete</span> usuń klucz</button>
              </form>
            </div>
          </div>
          </div>

          <div class='card' style='border-color:rgba(139,92,246,0.2)'>
              <div style='color:var(--purple);font-weight:600;font-size:0.85rem;margin-bottom:4px'><span class=material-symbols-outlined>bar_chart</span> Analiza moich sprzedaży</div>
              <div style='color:var(--text-muted);font-size:0.75rem;margin-bottom:10px'>Ceny rynkowe produktów z palet/magazynu + co warto wystawiać</div>
              <form method='POST' action='/analityka/okazje/perplexity-analyze' onsubmit='showLoading(this,"analyze")'>
                <input type='hidden' name='csrf_token' value='{generate_csrf()}'>
                <button id='btn-analyze' type='submit' class='btn btn-purple' style='border:none;cursor:pointer;font-size:0.82rem'>
                  <span class=material-symbols-outlined>search</span> Analizuj moje produkty
                </button>
              </form>
              <div id='loading-analyze' style='display:none;text-align:center;padding:10px;color:var(--purple);font-size:0.82rem'>
                <span style='animation:spin 1s linear infinite;display:inline-block'>⏳</span> Perplexity analizuje... (może potrwać ~30 sek)
              </div>
              {cached_html}
          </div>
        </div>"""

    no_data_banner = ""
    if not has_trendy:
        no_data_banner = "<div class='alert alert-warning'><span class=material-symbols-outlined>warning</span> Brak danych — uruchom <code style='background:var(--bg);padding:2px 6px;border-radius:4px'>python analyze_trends.py</code></div>"

    content = f"""
<p style='color:var(--text-muted);font-size:0.85rem;margin-bottom:20px'>Miesiąc: <strong>{miesiac}</strong> · Ostatnia analiza: {ostatnia_analiza}</p>
{no_data_banner}

<div class="stat-row" style="margin-bottom:20px">
  <div class="stat-box">
    <div class="stat-val orange">{len(okazje_list)}</div>
    <div class="stat-lbl">OKAZJI (score≥6)</div>
  </div>
  <div class="stat-box">
    <div class="stat-val blue">{len(wszystkie_list)}</div>
    <div class="stat-lbl">PRODUKTÓW ZBADANYCH</div>
  </div>
  <div class="stat-box">
    <div class="stat-val green">{'✓' if has_perplexity else '✗'}</div>
    <div class="stat-lbl">PERPLEXITY API</div>
  </div>
</div>

{live_scraper_section}
{perplexity_section}

<div style='margin-bottom:20px'>
  <div class="section-title" style="color:var(--orange)"><span class=material-symbols-outlined>emoji_events</span> Najlepsze okazje (score ≥ 6)</div>
  {okazje_html}
</div>

<div>
  <div class="section-title"><span class=material-symbols-outlined>bar_chart</span> Wszystkie produkty (top 50)</div>
  {wszystkie_html}
</div>
<style>@keyframes spin{{from{{transform:rotate(0deg)}}to{{transform:rotate(360deg)}}}}</style>
<script>
function showLoading(form, id) {{
  var btn = document.getElementById('btn-' + id);
  var loader = document.getElementById('loading-' + id);
  if(btn) {{ btn.disabled = true; btn.style.opacity = '0.5'; }}
  if(loader) loader.style.display = 'block';
}}
// Auto-polling jeśli task w toku
var loadingParam = new URLSearchParams(window.location.search).get('loading');
if(loadingParam) {{
  var klucz = loadingParam === 'analyze' ? 'okazje_{miesiac}' : 'szukaj_{miesiac}';
  var pollInterval = setInterval(function() {{
    fetch('/analityka/okazje/perplexity-status?klucz=' + klucz)
      .then(r => r.json())
      .then(d => {{
        if(d.status === 'done') {{
          clearInterval(pollInterval);
          window.location.href = '/analityka/okazje';
        }} else if(d.status === 'error') {{
          clearInterval(pollInterval);
          document.getElementById('loading-' + loadingParam).innerHTML = '<span class=material-symbols-outlined>cancel</span> Błąd Perplexity — sprawdź klucz API i spróbuj ponownie';
        }}
      }});
  }}, 3000);
  // Pokaż loader od razu
  var loader = document.getElementById('loading-' + loadingParam);
  if(loader) loader.style.display = 'block';
  var btn = document.getElementById('btn-' + loadingParam);
  if(btn) {{ btn.disabled = true; btn.style.opacity = '0.5'; }}
}}
</script>"""

    return render(content, 'TOP Okazje')



@analityka_bp.route('/analityka/okazje/set-perplexity-key', methods=['POST'])
def okazje_set_perplexity_key():
    from modules.database import set_config
    api_key = request.form.get('api_key', '').strip()
    if api_key:
        set_config('perplexity_api_key', api_key)
    return redirect('/analityka/okazje')



@analityka_bp.route('/analityka/okazje/remove-perplexity-key', methods=['POST'])
def okazje_remove_perplexity_key():
    from modules.database import set_config
    set_config('perplexity_api_key', '')
    return redirect('/analityka/okazje')



@analityka_bp.route('/analityka/okazje/set-perplexity-model', methods=['POST'])
def okazje_set_perplexity_model():
    from modules.database import set_config
    model = request.form.get('model', 'sonar-pro').strip()
    set_config('perplexity_model', model)
    return redirect('/analityka/okazje')



# Słownik statusów zadań Perplexity
_perplexity_jobs = {}

def _run_perplexity(klucz, prompt, api_key, db_path, model="sonar-pro"):
    import requests as _req, json as _json, sqlite3 as _sq
    _perplexity_jobs[klucz] = 'running'
    try:
        resp = _req.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 2000, "return_citations": True},
            timeout=90)
        data = resp.json()
        odpowiedz = data['choices'][0]['message']['content']
        citations = data.get('citations', [])
        conn2 = _sq.connect(db_path)
        conn2.row_factory = _sq.Row
        conn2.execute("""CREATE TABLE IF NOT EXISTS perplexity_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT, klucz TEXT UNIQUE,
            odpowiedz TEXT, citations TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        from datetime import datetime as _dt
        conn2.execute(
            "INSERT OR REPLACE INTO perplexity_cache (klucz, odpowiedz, citations, created_at) VALUES (?, ?, ?, ?)",
            (klucz, odpowiedz, _json.dumps(citations), _dt.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn2.commit(); conn2.close()
        _perplexity_jobs[klucz] = 'done'
        print(f"[Perplexity] {klucz} gotowe")
    except Exception as e:
        _perplexity_jobs[klucz] = 'error'
        print(f"[Perplexity] blad {klucz}: {e}")




@analityka_bp.route('/analityka/okazje/scrape-warrington')
def scrape_warrington():
    """Skrobie aktualne palety z Warrington - nowa strona (nie-Shopify)"""
    import requests as _req, re as _re, json as _jj
    _ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
    try:
        products = []
        seen_ids = set()
        for page_url in [
            'https://warrington.store/products/new',
            'https://warrington.store/products/new/page/2',
            'https://warrington.store/products/new/page/3',
            'https://warrington.store/products/elektronika-i-gadzety',
            'https://warrington.store/products/akcesoria-tv',
            'https://warrington.store/products/dom',
            'https://warrington.store/products/kuchnia',
            'https://warrington.store/products/narzedzia',
            'https://warrington.store/products/ogrod',
            'https://warrington.store/products/sprzet-agd',
            'https://warrington.store/products/promotions',
        ]:
            try:
                resp = _req.get(page_url, headers={'User-Agent': _ua}, timeout=12)
                if resp.status_code != 200:
                    continue
                html = resp.text
                cards = _re.findall(
                    r'<h3\s+class="product-name">\s*<a\s+href="(/product/(\d+)-([^"]+))"[^>]*>\s*(.*?)\s*</a>\s*</h3>.*?<ins\s+class="new-price">\s*([\d.,]+)\s*z[lł]\s*</ins>',
                    html, _re.DOTALL | _re.IGNORECASE
                )
                for href, pid, slug, name, price in cards:
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    title = name.strip() if name.strip() else slug.replace('-', ' ').title()
                    title = _re.sub(r'<[^>]+>', '', title).strip()
                    products.append({
                        'title': title,
                        'price_text': f'{price} zł',
                        'url': f'https://warrington.store{href}',
                        'available': True,
                        'id': pid,
                    })
            except:
                continue
            if len(products) >= 30:
                break
        if not products:
            for page_url in ['https://warrington.store/products/new']:
                try:
                    resp = _req.get(page_url, headers={'User-Agent': _ua}, timeout=12)
                    if resp.status_code != 200:
                        continue
                    prod_links = _re.findall(r'href="(/product/(\d+)-([^"]+))"', resp.text)
                    prices_all = _re.findall(r'<ins\s+class="new-price">\s*([\d.,]+)\s*z[lł]', resp.text)
                    pi = 0
                    for href, pid, slug in prod_links:
                        if pid in seen_ids:
                            continue
                        seen_ids.add(pid)
                        title = slug.replace('-', ' ').title()
                        price_txt = f'{prices_all[pi]} zł' if pi < len(prices_all) else '?'
                        pi += 1
                        products.append({
                            'title': title,
                            'price_text': price_txt,
                            'url': f'https://warrington.store{href}',
                            'available': True,
                            'id': pid,
                        })
                except:
                    continue
        if products:
            return jsonify({'ok': True, 'products': products[:35], 'total': len(products), 'source': 'html_new'})
        categories = [
            {'title': 'Nowe palety', 'url': 'https://warrington.store/products/new', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Elektronika i gadżety', 'url': 'https://warrington.store/products/elektronika-i-gadzety', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Dom', 'url': 'https://warrington.store/products/dom', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Zwierzęta', 'url': 'https://warrington.store/products/zwierzeta', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Akcesoria TV', 'url': 'https://warrington.store/products/akcesoria-tv', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Sport', 'url': 'https://warrington.store/products/sport', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Kuchnia', 'url': 'https://warrington.store/products/kuchnia', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Narzędzia', 'url': 'https://warrington.store/products/narzedzia', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Ogród', 'url': 'https://warrington.store/products/ogrod', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Zabawki', 'url': 'https://warrington.store/products/zabawki', 'available': True, 'price_text': 'kategoria'},
            {'title': 'Promocje', 'url': 'https://warrington.store/products/promotions', 'available': True, 'price_text': 'kategoria'},
        ]
        return jsonify({'ok': True, 'products': categories, 'total': len(categories), 'source': 'categories',
            'note': 'Nie udało się pobrać produktów - oto kategorie'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})



@analityka_bp.route('/analityka/okazje/scrape-jobalots')
def scrape_jobalots():
    """Jobalots - prawdziwe dane z API auction-list-v2 (popularne + okazje)"""
    import requests as _req
    from modules.database import get_config
    _jb_headers = {
        'Content-Type': 'application/json',
        'url-accept-language': 'pl',
        'url-accept-currency': 'pln',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    _jb_base = {
        'manifest_type': ['pallets'],
        'ship_to': 'PL',
        'ship_from': 'all',
        'list_type': ['auction', 'buyitnow'],
        'is_list': True,
        'use_open_search': '0',
        'exact_match': '0',
        'search_manifests': '0',
    }
    try:
        all_items = []
        seen_ids = set()
        for _sort, _per in [('popularity', 12), ('most_bids', 8), ('bid_low', 8)]:
            try:
                _body = {**_jb_base, 'per_page': _per, 'page': 1, 'sort_by': _sort}
                _r = _req.post('https://live1.jobalots.com/api/auction-list-v2',
                    headers=_jb_headers, json=_body, timeout=15)
                _d = _r.json()
                for _it in _d.get('result', {}).get('data', []):
                    _id = _it.get('id')
                    if _id not in seen_ids:
                        seen_ids.add(_id)
                        _it['_sort_tag'] = _sort
                        all_items.append(_it)
            except:
                continue
        total_available = 0
        try:
            _r0 = _req.post('https://live1.jobalots.com/api/auction-list-v2',
                headers=_jb_headers, json={**_jb_base, 'per_page': 1, 'page': 1, 'sort_by': 'popularity'}, timeout=10)
            total_available = _r0.json().get('result', {}).get('total', 0)
        except:
            pass
        items = all_items if all_items else []
        resp_data = {'error': False, 'status': 200, 'result': {'data': items, 'total': total_available or len(items)}}
        data = resp_data
        if data.get('error'):
            return jsonify({'ok': False, 'error': data.get('message', 'API error')})
        result = data.get('result', {})
        items = result.get('data', [])
        products = []
        _GBP_PLN = float(get_config('gbp_pln_rate') or 5.30)
        _EUR_PLN = float(get_config('eur_pln_rate') or 4.35)

        for item in items:
            sku = item.get('sku', '')
            title = item.get('title', 'Paleta')[:80]
            rrp = float(item.get('rrp', 0) or 0)
            bid = float(item.get('latest_bid_price', 0) or item.get('reserve_price', 0) or 0)
            qty = item.get('qty', '?')
            discount = item.get('discount', 0)
            bid_count = item.get('bid_count', 0)

            _orig_currency = (item.get('currency', '') or '').upper()
            if _orig_currency == 'GBP':
                rrp = round(rrp * _GBP_PLN, 2)
                bid = round(bid * _GBP_PLN, 2)
            elif _orig_currency == 'EUR':
                rrp = round(rrp * _EUR_PLN, 2)
                bid = round(bid * _EUR_PLN, 2)

            _eat_raw = item.get('end_at', '')
            if _eat_raw:
                try:
                    from datetime import datetime as _dtj, timedelta as _tdj
                    _clean = _eat_raw.split('.')[0].replace('Z', '').replace('T', ' ')
                    _utc_dt = _dtj.strptime(_clean, '%Y-%m-%d %H:%M:%S')
                    _y = _utc_dt.year
                    _mar31 = _dtj(_y, 3, 31)
                    _last_sun_mar = _dtj(_y, 3, 31 - (_mar31.weekday() + 1) % 7, 2)
                    _oct31 = _dtj(_y, 10, 31)
                    _last_sun_oct = _dtj(_y, 10, 31 - (_oct31.weekday() + 1) % 7, 3)
                    _hours = 2 if _last_sun_mar <= _utc_dt < _last_sun_oct else 1
                    _local = _utc_dt + _tdj(hours=_hours)
                    end_at = _local.strftime('%Y-%m-%d %H:%M')
                except Exception as _te:
                    end_at = _eat_raw[:16].replace('T', ' ')
            else:
                end_at = ''
            currency = 'PLN'
            manifest = item.get('manifest', {})
            img = ''
            if manifest.get('product_first_image'):
                img = manifest['product_first_image'].get('product_image_thumbnail_url', '')
            url = f'https://jobalots.com/pl/products/{sku}?currency=pln'
            sort_tag = item.get('_sort_tag', '')
            tag_label = {'popularity': '<span class=material-symbols-outlined>local_fire_department</span>', 'most_bids': '<span class=material-symbols-outlined>trending_up</span>', 'bid_low': '<span class=material-symbols-outlined>paid</span>'}.get(sort_tag, '')
            products.append({
                'title': title,
                'price_text': f'{bid:.0f} {currency}' if bid > 0 else f'{rrp:.0f} {currency} RRP',
                'rrp': rrp,
                'bid': bid,
                'qty': qty,
                'discount': discount,
                'bid_count': bid_count,
                'end_at': end_at,
                'url': url,
                'image': img,
                'sku': sku,
                'tag': tag_label,
            })
        total = result.get('total', len(products))
        return jsonify({'ok': True, 'products': products, 'total': total, 'source': 'api',
            'note': f'<span class=material-symbols-outlined>local_fire_department</span> Popularne · <span class=material-symbols-outlined>trending_up</span> Dużo ofert · <span class=material-symbols-outlined>paid</span> Najtańsze ({total} palet łącznie)'})
    except Exception as e:
        _jb = 'https://jobalots.com/pl/pages/products-on-auction?page=1&currency=pln'
        categories = [
            {'title': 'Wszystkie palety', 'url': f'{_jb}&type=pallets'},
            {'title': 'Electronics', 'url': f'{_jb}&categories=electronics'},
            {'title': 'Home & Kitchen', 'url': f'{_jb}&categories=home-kitchen'},
            {'title': 'Garden', 'url': f'{_jb}&categories=garden'},
            {'title': 'Tools & DIY', 'url': f'{_jb}&categories=tools-diy'},
        ]
        return jsonify({'ok': False, 'error': str(e), 'products': categories, 'total': len(categories),
            'fallback_url': f'{_jb}&type=pallets'})



@analityka_bp.route('/analityka/okazje/perplexity-status')
def perplexity_status():
    klucz = request.args.get('klucz', '')
    return jsonify({'status': _perplexity_jobs.get(klucz, 'idle')})



@analityka_bp.route('/analityka/okazje/perplexity-analyze', methods=['POST'])
def okazje_perplexity_analyze():
    import threading
    from modules.database import get_db, get_config, DATABASE as _db_path
    api_key = get_config('perplexity_api_key', '')
    if not api_key:
        return redirect('/analityka/okazje')
    perp_model = get_config('perplexity_model', 'sonar-pro')
    if perp_model == 'sonar': perp_model = 'sonar-pro'
    miesiac = datetime.now().strftime('%Y-%m')
    klucz = f'okazje_{miesiac}'
    if _perplexity_jobs.get(klucz) == 'running':
        return redirect('/analityka/okazje?loading=analyze')
    conn = get_db()
    try:
        top = conn.execute("""
            SELECT COALESCE(p.nazwa, s.nazwa, 'Produkt') as nazwa,
                   COALESCE(p.kategoria, 'inne') as kategoria,
                   s.cena as cena_sprzedazy,
                   COALESCE(pal.dostawca, p.dostawca) as dostawca,
                   COUNT(*) as ilosc_sprzedanych, SUM(s.cena) as przychod
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            LEFT JOIN palety pal ON p.paleta_id = pal.id
            WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
             
              AND s.data_sprzedazy >= date('now', '-60 days')
            GROUP BY COALESCE(p.nazwa, s.nazwa)
            ORDER BY przychod DESC
            LIMIT 15
        """).fetchall()

        na_stanie = conn.execute("""
            SELECT p.nazwa, p.kategoria, p.ilosc,
                   COALESCE(p.cena_allegro, p.cena_brutto, 0) as cena,
                   COALESCE(pal.dostawca, p.dostawca) as dostawca
            FROM produkty p
            LEFT JOIN palety pal ON p.paleta_id = pal.id
            WHERE p.ilosc > 0 AND p.status != 'sprzedany'
            ORDER BY COALESCE(p.cena_allegro, p.cena_brutto, 0) DESC
            LIMIT 10
        """).fetchall()

        kategorie = conn.execute("""
            SELECT COALESCE(p.kategoria, 'inne') as kategoria,
                   COUNT(*) as cnt, SUM(s.cena) as przychod
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
             
              AND s.data_sprzedazy >= date('now', '-60 days')
            GROUP BY kategoria ORDER BY przychod DESC LIMIT 5
        """).fetchall()
    except Exception as _e:
        print(f"BLAD okazje analyze: {_e}")
        top, na_stanie, kategorie = [], [], []

    sprzedane_txt = "\n".join(
        f"{i}. {r['nazwa'][:60]} [{r['kategoria'] or 'inne'}] — sprzedano {r['ilosc_sprzedanych']}x za {r['cena_sprzedazy']:,.0f} zl, przychod {r['przychod']:,.0f} zl, dostawca: {r['dostawca'] or 'własny'}"
        for i, r in enumerate(top, 1)) if top else "Brak danych sprzedażowych"

    stanie_txt = "\n".join(
        f"- {r['nazwa'][:60]} [{r['kategoria'] or ''}] — {r['ilosc']} szt na stanie, cena {r['cena']:,.0f} zl"
        for r in na_stanie) if na_stanie else "Brak produktów na stanie"

    kat_txt = ", ".join(f"{r['kategoria']} ({r['przychod']:,.0f} zl)" for r in kategorie) if kategorie else "mix"

    prompt = (
        f"Jestem sprzedawcą na Allegro, kupuję palety zwrotów konsumenckich i sprzedaję produkty pojedynczo. Data: {miesiac}.\n\n"
        f"=== MOJE NAJLEPIEJ SPRZEDAJĄCE SIĘ PRODUKTY (ostatnie 60 dni) ===\n{sprzedane_txt}\n\n"
        f"=== PRODUKTY NA STANIE (niesprzedane) ===\n{stanie_txt}\n\n"
        f"=== MOJE TOP KATEGORIE ===\n{kat_txt}\n\n"
        f"Sprawdź aktualne ceny tych produktów na Allegro.pl. Dla każdego sprzedanego produktu podaj:\n"
        f"1. Aktualna cena na Allegro (ile ofert jest)\n"
        f"2. Czy moja cena sprzedaży była dobra vs rynek\n"
        f"3. Dla produktów na stanie — za ile warto wystawić\n\n"
        f"Na koniec podaj podsumowanie: które kategorie produktów z palet są najbardziej opłacalne "
        f"i jakie typy palet powinienem kupować w przyszłości.\n"
        f"Odpowiedz po polsku, z cenami w złotych i linkami do wyszukań na Allegro."
    )
    threading.Thread(target=_run_perplexity, args=(klucz, prompt, api_key, _db_path, perp_model), daemon=True).start()
    return redirect('/analityka/okazje?loading=analyze')



@analityka_bp.route('/analityka/okazje/perplexity-szukaj', methods=['POST'])
def okazje_perplexity_szukaj():
    import threading
    from modules.database import get_db, get_config, DATABASE as _db_path
    api_key = get_config('perplexity_api_key', '')
    if not api_key:
        return redirect('/analityka/okazje')
    perp_model = get_config('perplexity_model', 'sonar-pro')
    if perp_model == 'sonar': perp_model = 'sonar-pro'
    miesiac = datetime.now().strftime('%Y-%m')
    klucz = f'szukaj_{miesiac}'
    if _perplexity_jobs.get(klucz) == 'running':
        return redirect('/analityka/okazje?loading=szukaj')
    conn = get_db()
    try:
        top_sprzedaz = conn.execute("""
            SELECT COALESCE(p.nazwa, s.nazwa, 'Produkt') as nazwa,
                   COALESCE(p.kategoria, 'inne') as kategoria,
                   s.cena, COALESCE(pal.dostawca, p.dostawca) as dostawca
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            LEFT JOIN palety pal ON p.paleta_id = pal.id
            WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
             
              AND s.data_sprzedazy >= date('now', '-60 days')
            ORDER BY s.cena DESC
            LIMIT 10
        """).fetchall()

        top_kat = conn.execute("""
            SELECT COALESCE(p.kategoria, 'inne') as kategoria,
                   COUNT(*) as cnt, SUM(s.cena) as przychod,
                   AVG(s.cena) as sr_cena
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
             
              AND s.data_sprzedazy >= date('now', '-60 days')
            GROUP BY kategoria ORDER BY przychod DESC LIMIT 5
        """).fetchall()

        palety_roi = conn.execute("""
            SELECT pal.nazwa, pal.dostawca, pal.cena_zakupu, pal.ilosc_produktow,
                   COALESCE(SUM(CASE WHEN s.id IS NOT NULL THEN 1 ELSE 0 END), 0) as sprzedanych,
                   COALESCE(SUM(s.cena), 0) as przychod_z_palety
            FROM palety pal
            LEFT JOIN produkty p ON p.paleta_id = pal.id
            LEFT JOIN sprzedaze s ON s.produkt_id = p.id
              AND s.status NOT IN ('zwrot','anulowane','anulowana')
             
            GROUP BY pal.id
            ORDER BY pal.data_zakupu DESC
            LIMIT 8
        """).fetchall()
    except Exception as _e:
        print(f"BLAD okazje szukaj: {_e}")
        top_sprzedaz, top_kat, palety_roi = [], [], []

    sprzedaz_txt = "\n".join(
        f"- {r['nazwa'][:50]} [{r['kategoria'] or ''}] — {r['cena']:,.0f} zl, dostawca: {r['dostawca'] or 'własny'}"
        for r in top_sprzedaz) if top_sprzedaz else "brak danych"

    kat_txt = "\n".join(
        f"- {r['kategoria']}: {r['cnt']}x sprzedanych, {r['przychod']:,.0f} zl przychód, śr. {r['sr_cena']:,.0f} zl/szt"
        for r in top_kat) if top_kat else "elektronika, AGD, sport"

    palety_txt = "\n".join(
        f"- {r['nazwa']} ({r['dostawca']}): kupiono za {r['cena_zakupu']:,.0f} zl ({r['ilosc_produktow']} szt), sprzedano {r['sprzedanych']}x = {(r['przychod_z_palety'] or 0):,.0f} zl"
        for r in palety_roi) if palety_roi else "brak danych"

    # Pobierz PRAWDZIWE produkty z Warrington (nowa strona, nie-Shopify)
    warrington_txt = ""
    try:
        import requests as _rq, re as _rre, json as _jjw
        _ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
        wr_items = []
        wr_seen = set()
        for _wurl in ['https://warrington.store/products/new', 'https://warrington.store/products/new/page/2']:
            try:
                wr = _rq.get(_wurl, headers={'User-Agent': _ua}, timeout=12)
                if wr.status_code != 200:
                    continue
                _cards = _rre.findall(
                    r'<h3\s+class="product-name">\s*<a\s+href="(/product/(\d+)-([^"]+))"[^>]*>\s*(.*?)\s*</a>\s*</h3>.*?<ins\s+class="new-price">\s*([\d.,]+)\s*z[lł]',
                    wr.text, _rre.DOTALL | _rre.IGNORECASE
                )
                for _href, _pid, _slug, _name, _price in _cards:
                    if _pid in wr_seen:
                        continue
                    wr_seen.add(_pid)
                    _title = _rre.sub(r'<[^>]+>', '', _name).strip() if _name.strip() else _slug.replace('-', ' ').title()
                    wr_items.append(f"- {_title} | cena: {_price} zł | link: https://warrington.store{_href}")
                if not _cards:
                    _links = _rre.findall(r'href="(/product/(\d+)-([^"]+))"', wr.text)
                    _prices = _rre.findall(r'<ins\s+class="new-price">\s*([\d.,]+)\s*z[lł]', wr.text)
                    _pi = 0
                    for _href, _pid, _slug in _links:
                        if _pid in wr_seen:
                            continue
                        wr_seen.add(_pid)
                        _title = _slug.replace('-', ' ').title()
                        _pr = f" | cena: {_prices[_pi]} zł" if _pi < len(_prices) else ""
                        _pi += 1
                        wr_items.append(f"- {_title}{_pr} | link: https://warrington.store{_href}")
            except:
                continue
            if len(wr_items) >= 15:
                break
        if wr_items:
            warrington_txt = "\n".join(wr_items[:20])
        else:
            warrington_txt = "Nie udalo sie pobrac produktow. Strona: https://warrington.store/products/new"
    except Exception as _we:
        warrington_txt = f"blad pobierania: {_we}"

    # Pobierz PRAWDZIWE palety z Jobalots API
    jobalots_txt = ""
    try:
        import requests as _rqj
        _jb_resp = _rqj.post(
            'https://live1.jobalots.com/api/auction-list-v2',
            headers={'Content-Type': 'application/json', 'url-accept-language': 'pl', 'url-accept-currency': 'pln'},
            json={'per_page': 15, 'page': 1, 'sort_by': 'auction_end_soon',
                  'manifest_type': ['pallets'], 'ship_to': 'PL', 'ship_from': 'all',
                  'list_type': ['auction', 'buyitnow'], 'is_list': True},
            timeout=20
        )
        _jb_data = _jb_resp.json()
        _jb_items = []
        _GBP_PLN_ai = float(get_config('gbp_pln_rate') or 5.30)
        _EUR_PLN_ai = float(get_config('eur_pln_rate') or 4.35)
        for _ji in _jb_data.get('result', {}).get('data', [])[:15]:
            _jsku = _ji.get('sku', '')
            _jtitle = _ji.get('title', '')[:60]
            _jrrp = float(_ji.get('rrp', 0) or 0)
            _jbid = float(_ji.get('latest_bid_price', 0) or _ji.get('reserve_price', 0) or 0)
            _jqty = _ji.get('qty', '?')
            _jcur_orig = (_ji.get('currency', '') or '').upper()
            if _jcur_orig == 'GBP':
                _jrrp = round(_jrrp * _GBP_PLN_ai, 2)
                _jbid = round(_jbid * _GBP_PLN_ai, 2)
            elif _jcur_orig == 'EUR':
                _jrrp = round(_jrrp * _EUR_PLN_ai, 2)
                _jbid = round(_jbid * _EUR_PLN_ai, 2)
            _jurl = f'https://jobalots.com/pl/products/{_jsku}?currency=pln'
            _jprice = f'{_jbid:.0f} PLN' if _jbid > 0 else f'{_jrrp:.0f} PLN RRP'
            _jb_items.append(f"- {_jtitle} | {_jqty} szt | cena: {_jprice} | RRP: {_jrrp:.0f} PLN | link: {_jurl}")
        jobalots_txt = "\n".join(_jb_items) if _jb_items else "brak danych z API"
    except Exception as _je:
        jobalots_txt = f"blad pobierania: {_je}"

    prompt = (
        f"Jestem sprzedawcą na Allegro w Polsce, kupuję palety zwrotów konsumenckich i sprzedaję pojedynczo. Data: {miesiac}.\n\n"
        f"=== CO MI SIĘ NAJLEPIEJ SPRZEDAJE (ostatnie 60 dni) ===\n{sprzedaz_txt}\n\n"
        f"=== MOJE NAJLEPSZE KATEGORIE ===\n{kat_txt}\n\n"
        f"=== MOJE DOTYCHCZASOWE PALETY (wyniki) ===\n{palety_txt}\n\n"
        f"=== AKTUALNE PALETY NA WARRINGTON.STORE (prawdziwe dane ze sklepu) ===\n{warrington_txt}\n\n"
        f"=== AKTUALNE AUKCJE PALET NA JOBALOTS.COM (prawdziwe dane z API) ===\n{jobalots_txt}\n\n"
        f"ZADANIE:\n"
        f"Masz powyżej PRAWDZIWE, aktualne dane z obu sklepów z linkami.\n"
        f"1. Przeanalizuj które palety pasują do mojego profilu sprzedażowego (kategorie, marża, dostawca).\n"
        f"2. Dla KAŻDEJ rekomendowanej palety podaj link DOKŁADNIE taki jak w danych powyżej — NIE zmieniaj go!\n\n"
        f"FORMAT ODPOWIEDZI — dla każdej palety użyj sekcji z ###:\n\n"
        f"### 1. Nazwa palety\n"
        f"- Źródło: warrington.store / jobalots.com\n"
        f"- Cena: X PLN (aktualna oferta/cena)\n"
        f"- RRP: wartość rynkowa\n"
        f"- Zawartość: {'{'}ilość{'}'} szt, co jest w palecie\n"
        f"- Link: SKOPIUJ DOKŁADNIE z danych powyżej!\n"
        f"- Dlaczego pasuje: odnieś do moich najlepiej sprzedających się kategorii\n\n"
        f"WAŻNE: Skopiuj linki DOSŁOWNIE z danych — NIE wymyślaj nowych URL-i!\n"
        f"Na koniec dodaj sekcję ### PODSUMOWANIE z TOP 3 paletami i szacowanym zyskiem.\n"
        f"Odpowiedz po polsku."
    )
    threading.Thread(target=_run_perplexity, args=(klucz, prompt, api_key, _db_path, perp_model), daemon=True).start()
    return redirect('/analityka/okazje?loading=szukaj')



@analityka_bp.route('/analityka/czas-sprzedazy')
def analityka_czas_sprzedazy():
    """Analityka czasu sprzedaży - od dodania/zakupu do sprzedaży, bazuje na produkty"""
    from modules.database import get_db
    import json as _json
    conn = get_db()

    # Migracja inline - dodaj brakujące kolumny w oferty jeśli stara baza
    for _sql in [
        "ALTER TABLE oferty ADD COLUMN tytul TEXT DEFAULT ''",
        "ALTER TABLE oferty ADD COLUMN data_wystawienia TIMESTAMP",
        "ALTER TABLE sprzedaze ADD COLUMN nazwa TEXT DEFAULT ''",
        "ALTER TABLE sprzedaze ADD COLUMN data_syncu TIMESTAMP",
    ]:
        try:
            conn.execute(_sql)
            conn.commit()
        except:
            pass  # kolumna już istnieje

    # MIGRACJA JEDNORAZOWA: przenieś stare przychod_offline z produkty -> sprzedaze
    try:
        stare = conn.execute("""
            SELECT p.id, p.nazwa, p.przychod_offline, p.sprzedano_offline,
                   p.data_dodania, pal.data_zakupu
            FROM produkty p
            LEFT JOIN palety pal ON pal.id = p.paleta_id
            WHERE p.sprzedano_offline > 0
              AND p.przychod_offline > 0
              AND NOT EXISTS (
                  SELECT 1 FROM sprzedaze s
                  WHERE s.produkt_id = p.id AND s.kupujacy = 'offline'
              )
        """).fetchall()
        from datetime import datetime as _dt2
        for row in stare:
            data = row['data_zakupu'] or row['data_dodania'] or _dt2.now().strftime('%Y-%m-%dT%H:%M:%S')
            cena_szt = round(row['przychod_offline'] / max(row['sprzedano_offline'], 1), 2)
            conn.execute("""
                INSERT INTO sprzedaze (produkt_id, nazwa, cena, ilosc, status, data_sprzedazy, kupujacy, notified)
                VALUES (?, ?, ?, ?, 'sprzedana', ?, 'offline', 1)
            """, (row['id'], row['nazwa'] or f'Produkt #{row["id"]}',
                  cena_szt, row['sprzedano_offline'], data))
        if stare:
            ids = [r['id'] for r in stare]
            placeholders = ','.join('?' * len(ids))
            conn.execute("UPDATE produkty SET przychod_offline = 0 WHERE id IN (" + placeholders + ")", ids)
            conn.commit()
            print(f"[CHECK_CIRCLE] Migracja offline: przeniesiono {len(stare)} produktów do sprzedaze, wyzerowano przychod_offline")
    except Exception as _e:
        print(f"⚠️ Migracja offline: {_e}")

    # Napraw rekordy offline w sprzedaze które mają cena=0
    try:
        conn.execute("""
            UPDATE sprzedaze SET cena = (
                SELECT COALESCE(NULLIF(p.cena_allegro,0), p.cena_brutto, 0)
                FROM produkty p WHERE p.id = sprzedaze.produkt_id
            )
            WHERE kupujacy = 'offline'
              AND (cena IS NULL OR cena = 0)
              AND produkt_id IS NOT NULL
        """)
        naprawione = conn.execute("SELECT changes()").fetchone()[0]
        if naprawione:
            conn.commit()
            print(f"[CHECK_CIRCLE] Naprawiono ceny offline: {naprawione} rekordów")
    except Exception as _e:
        print(f"⚠️ Naprawa cen offline: {_e}")

    # Backfill data_syncu
    try:
        conn.execute("""
            UPDATE sprzedaze SET data_syncu = data_sprzedazy
            WHERE data_syncu IS NULL
              AND produkt_id IS NULL
              AND data_sprzedazy IS NOT NULL
        """)
        conn.commit()
    except:
        pass

    # Backfill produkty.data_dodania z pierwszej sprzedaży produktu
    try:
        conn.execute("""
            UPDATE produkty SET data_dodania = (
                SELECT MIN(s.data_sprzedazy)
                FROM sprzedaze s
                WHERE s.produkt_id = produkty.id
                  AND s.data_sprzedazy IS NOT NULL
            )
            WHERE (data_dodania IS NULL OR data_dodania = '')
              AND id IN (SELECT DISTINCT produkt_id FROM sprzedaze WHERE produkt_id IS NOT NULL)
        """)
        conn.commit()
    except:
        pass

    # UWAGA: NIE backfillujemy data_dodania z data_zakupu palety
    # bo to psuje czas-sprzedazy (data zakupu palety != data wystawienia produktu)

    # Backfill: uzupełnij s.nazwa z oferty.tytul
    try:
        conn.execute("""
            UPDATE sprzedaze SET nazwa = (
                SELECT COALESCE(o.tytul, '')
                FROM oferty o WHERE o.id = sprzedaze.oferta_id
            )
            WHERE (nazwa IS NULL OR nazwa = '')
              AND oferta_id IS NOT NULL
        """)
        conn.commit()
    except:
        pass
    # Backfill2: dla rekordów bez oferta_id — spróbuj przez allegro_order_id
    try:
        conn.execute("""
            UPDATE sprzedaze SET nazwa = (
                SELECT COALESCE(o.tytul, '')
                FROM oferty o
                JOIN sprzedaze s2 ON s2.oferta_id = o.id
                WHERE s2.allegro_order_id = sprzedaze.allegro_order_id
                LIMIT 1
            )
            WHERE (nazwa IS NULL OR nazwa = '')
              AND oferta_id IS NULL
              AND allegro_order_id IS NOT NULL
        """)
        conn.commit()
    except:
        pass
    # Backfill3
    try:
        conn.execute("""
            UPDATE sprzedaze SET nazwa =
                'Zamówienie ' || SUBSTR(allegro_order_id, 1, 8)
            WHERE (nazwa IS NULL OR nazwa = '' OR nazwa LIKE 'Zamówienie #%')
              AND allegro_order_id IS NOT NULL
              AND (SELECT COALESCE(o.tytul,'') FROM oferty o WHERE o.id = sprzedaze.oferta_id) = ''
        """)
        conn.commit()
    except:
        pass

    # === DANE OD WYSTAWIENIA / DODANIA ===
    # data_od: używamy WYŁĄCZNIE daty wystawienia oferty lub data_dodania produktu
    # NIE używamy data_zakupu palety — to data kupna palety, nie wystawienia produktu
    dane_od_wystawienia = conn.execute("""
        SELECT
            COALESCE(NULLIF(p.nazwa,''), NULLIF(s.nazwa,''), CASE WHEN s.allegro_order_id IS NOT NULL THEN 'Zamówienie ' || SUBSTR(s.allegro_order_id,1,8) ELSE 'Brak nazwy' END) as nazwa,
            s.cena,
            s.data_sprzedazy,
            COALESCE(
                o.data_wystawienia,
                o2.data_wystawienia,
                NULLIF(p.data_dodania,'')) as data_od,
            p.kategoria, p.dostawca,
            CASE
              WHEN COALESCE(o.data_wystawienia, o2.data_wystawienia, NULLIF(p.data_dodania,'')) IS NOT NULL
              THEN MAX(0, (julianday(REPLACE(SUBSTR(s.data_sprzedazy,1,19),'T',' '))
                   - julianday(REPLACE(SUBSTR(
                       COALESCE(o.data_wystawienia, o2.data_wystawienia, NULLIF(p.data_dodania,'')),1,19),'T',' '))))
              ELSE NULL
            END as dni_od_wystawienia
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        LEFT JOIN oferty o ON s.oferta_id = o.id
        LEFT JOIN (
            SELECT produkt_id, MIN(data_wystawienia) as data_wystawienia
            FROM oferty
            WHERE data_wystawienia IS NOT NULL AND data_wystawienia != ''
            GROUP BY produkt_id
        ) o2 ON o2.produkt_id = s.produkt_id AND s.oferta_id IS NULL
        WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
          AND s.data_sprzedazy IS NOT NULL AND s.data_sprzedazy != ''

        ORDER BY dni_od_wystawienia ASC
    """).fetchall()

    # === DANE OD ZAKUPU PALETY ===
    dane_od_zakupu = conn.execute("""
        SELECT
            COALESCE(NULLIF(p.nazwa,''), NULLIF(s.nazwa,''), CASE WHEN s.allegro_order_id IS NOT NULL THEN 'Zamówienie ' || SUBSTR(s.allegro_order_id,1,8) ELSE 'Brak nazwy' END) as nazwa,
            s.cena, s.data_sprzedazy,
            pal.data_zakupu, pal.nazwa as paleta_nazwa, pal.dostawca,
            (julianday(REPLACE(SUBSTR(s.data_sprzedazy,1,19),'T',' '))
             - julianday(pal.data_zakupu)) as dni_od_zakupu
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        JOIN palety pal ON p.paleta_id = pal.id
        WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
          AND s.data_sprzedazy IS NOT NULL AND s.data_sprzedazy != ''
         
          AND pal.data_zakupu IS NOT NULL
          AND (julianday(REPLACE(SUBSTR(s.data_sprzedazy,1,19),'T',' '))
               - julianday(pal.data_zakupu)) >= 0
        ORDER BY dni_od_zakupu ASC
    """).fetchall()


    def fmt_dni(d):
        if d is None: return '?'
        d = float(d)
        if d < 0.04: return '<1h'
        if d < 1: return f'{int(d*24)}h'
        return f'{d:.1f} dni'

    dw = [float(r['dni_od_wystawienia']) for r in dane_od_wystawienia if r['dni_od_wystawienia'] is not None]
    stat_w = {}
    if dw:
        sd = sorted(dw)
        stat_w = {'srednia': sum(dw)/len(dw), 'mediana': sd[len(sd)//2], 'min': sd[0], 'max': sd[-1],
                  'cnt': len(dw), 'w_24h': sum(1 for d in dw if d <= 1),
                  'w_7dni': sum(1 for d in dw if d <= 7), 'w_30dni': sum(1 for d in dw if d <= 30),
                  'pow_30dni': sum(1 for d in dw if d > 30)}

    dz = [float(r['dni_od_zakupu']) for r in dane_od_zakupu if r['dni_od_zakupu'] is not None]
    stat_z = {}
    if dz:
        sz = sorted(dz)
        stat_z = {'srednia': sum(dz)/len(dz), 'mediana': sz[len(sz)//2], 'min': sz[0], 'max': sz[-1],
                  'cnt': len(dz), 'w_7dni': sum(1 for d in dz if d <= 7),
                  'w_30dni': sum(1 for d in dz if d <= 30), 'w_60dni': sum(1 for d in dz if d <= 60),
                  'pow_60dni': sum(1 for d in dz if d > 60)}

    histogram_w = [0]*8
    for d in dw:
        if d <= 1: histogram_w[0] += 1
        elif d <= 3: histogram_w[1] += 1
        elif d <= 7: histogram_w[2] += 1
        elif d <= 14: histogram_w[3] += 1
        elif d <= 30: histogram_w[4] += 1
        elif d <= 60: histogram_w[5] += 1
        elif d <= 90: histogram_w[6] += 1
        else: histogram_w[7] += 1

    dostawca_stats = {}
    for r in dane_od_zakupu:
        d = r['dostawca'] or 'Nieznany'
        if d not in dostawca_stats: dostawca_stats[d] = []
        if r['dni_od_zakupu'] is not None: dostawca_stats[d].append(float(r['dni_od_zakupu']))
    dostawcy_wyniki = sorted(
        [{'dostawca': d, 'srednia': sum(v)/len(v), 'cnt': len(v)} for d,v in dostawca_stats.items() if len(v) >= 1],
        key=lambda x: x['srednia'])

    _dane_z_datami = [r for r in dane_od_wystawienia if r['dni_od_wystawienia'] is not None]

    _seen_fast = set()
    najszybsze = []
    for r in _dane_z_datami:
        n = r['nazwa']
        if n not in _seen_fast:
            _seen_fast.add(n)
            najszybsze.append(r)
            if len(najszybsze) >= 10:
                break

    _seen_slow = set()
    najwolniejsze = []
    for r in reversed(_dane_z_datami):
        n = r['nazwa']
        if n not in _seen_slow:
            _seen_slow.add(n)
            najwolniejsze.append(r)
            if len(najwolniejsze) >= 10:
                break

    cnt_bez_daty = len(dane_od_wystawienia) - len(_dane_z_datami)

    lbl_j = _json.dumps(['≤1 dzień','2-3 dni','4-7 dni','1-2 tyg','2-4 tyg','1-2 mies','2-3 mies','3+ mies'])
    dat_j = _json.dumps(histogram_w)

    karta_w = ""
    if stat_w:
        karta_w = f"""
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div class="stat-box">
                <div class="stat-val green">{stat_w['srednia']:.1f}</div>
                <div class="stat-lbl">ŚR. DNI</div>
            </div>
            <div class="stat-box">
                <div class="stat-val blue">{stat_w['mediana']:.1f}</div>
                <div class="stat-lbl">MEDIANA</div>
            </div>
            <div class="stat-box">
                <div class="stat-val orange">{fmt_dni(stat_w['min'])}</div>
                <div class="stat-lbl">NAJSZYBCIEJ</div>
            </div>
            <div class="stat-box">
                <div class="stat-val red">{fmt_dni(stat_w['max'])}</div>
                <div class="stat-lbl">NAJWOLNIEJ</div>
            </div>
        </div>
        <div style="margin-top:10px;font-size:0.75rem;color:var(--text-secondary);text-align:center">{stat_w['cnt']} sprzedanych produktów</div>
        <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">
            <span class="badge badge-success"><span class=material-symbols-outlined>bolt</span> {stat_w['w_24h']} w 24h</span>
            <span style="background:var(--blue-soft);color:var(--blue);padding:3px 8px;border-radius:10px;font-size:0.7rem;font-weight:600"><span class=material-symbols-outlined>calendar_month</span> {stat_w['w_7dni']} w tyg</span>
            <span class="badge badge-warning"><span class=material-symbols-outlined>calendar_month</span> {stat_w['w_30dni']} w mies</span>
            <span class="badge badge-error"><span class=material-symbols-outlined>speed</span> {stat_w['pow_30dni']} pow. 30 dni</span>
        </div>"""
    else:
        info = f' ({cnt_bez_daty} szt. sprzedanych bez daty — synchronizuj z Allegro)' if cnt_bez_daty else ''
        karta_w = f'<div style="color:var(--text-muted);font-size:0.85rem;padding:10px">Brak danych z datą sprzedaży.<br><span style="color:var(--orange);font-size:0.8rem">{cnt_bez_daty} produktów sprzedanych bez daty — synchronizuj z Allegro lub kliknij -1 szt (od v32 ustawia datę)</span></div>'

    karta_z = ""
    if stat_z:
        karta_z = f"""
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div class="stat-box">
                <div class="stat-val blue">{stat_z['srednia']:.1f}</div>
                <div class="stat-lbl">ŚR. DNI</div>
            </div>
            <div class="stat-box">
                <div class="stat-val green">{stat_z['mediana']:.1f}</div>
                <div class="stat-lbl">MEDIANA</div>
            </div>
            <div class="stat-box">
                <div class="stat-val orange">{fmt_dni(stat_z['min'])}</div>
                <div class="stat-lbl">NAJSZYBCIEJ</div>
            </div>
            <div class="stat-box">
                <div class="stat-val red">{fmt_dni(stat_z['max'])}</div>
                <div class="stat-lbl">NAJWOLNIEJ</div>
            </div>
        </div>
        <div style="margin-top:10px;font-size:0.75rem;color:var(--text-secondary);text-align:center">{stat_z['cnt']} sprzedaży z palet</div>
        <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">
            <span class="badge badge-success">7 dni: {stat_z['w_7dni']}</span>
            <span style="background:var(--blue-soft);color:var(--blue);padding:3px 8px;border-radius:10px;font-size:0.7rem;font-weight:600">30 dni: {stat_z['w_30dni']}</span>
            <span class="badge badge-warning">60 dni: {stat_z['w_60dni']}</span>
            <span class="badge badge-error">60+: {stat_z['pow_60dni']}</span>
        </div>"""
    else:
        karta_z = f'<div style="color:var(--text-muted);font-size:0.85rem;padding:10px">Brak danych z datą sprzedaży.<br><span style="color:var(--orange);font-size:0.8rem">Produkty muszą być powiązane z paletą i mieć datę sprzedaży z Allegro lub -1 szt</span></div>'

    dostawcy_html = ""
    if dostawcy_wyniki:
        rows = ""
        for i, d in enumerate(dostawcy_wyniki[:8]):
            sep = f"border-bottom:1px solid var(--border);" if i < len(dostawcy_wyniki[:8])-1 else ""
            clr = "var(--green)" if d['srednia'] <= 14 else "var(--orange)" if d['srednia'] <= 30 else "var(--red)"
            rows += f'<div style="display:flex;align-items:center;gap:10px;padding:8px 0;{sep}"><div style="flex:1;font-size:0.85rem;font-weight:600" class="dostawca-name">{d["dostawca"]}</div><div style="font-size:0.8rem;color:var(--text-muted)">{d["cnt"]} szt</div><div style="font-weight:700;color:{clr}">{d["srednia"]:.1f} dni</div></div>'
        dostawcy_html = f'<div class="card"><div style="font-weight:700;color:var(--orange);margin-bottom:12px"><span class=material-symbols-outlined>factory</span> Dostawcy — średni czas sprzedaży od zakupu palety</div>{rows}</div>'

    def item_row_w(r, kolor, i, total):
        sep = f"border-bottom:1px solid var(--border);" if i < total-1 else ""
        name = (r['nazwa'] or 'Brak nazwy')[:50]
        cena = float(r['cena'] or 0)
        return f'<div style="display:flex;align-items:center;gap:8px;padding:6px 0;{sep}"><div style="font-weight:700;color:{kolor};min-width:65px;font-size:0.85rem">{fmt_dni(r["dni_od_wystawienia"])}</div><div style="flex:1;font-size:0.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{name}</div><div style="font-size:0.8rem;color:var(--text-muted);white-space:nowrap">{cena:,.0f} zł</div></div>'

    szybkie_html = ""
    if najszybsze:
        rows = "".join(item_row_w(r, "var(--green)", i, len(najszybsze)) for i, r in enumerate(najszybsze))
        szybkie_html = f'<div class="card"><div style="font-weight:700;color:var(--green);margin-bottom:12px"><span class=material-symbols-outlined>bolt</span> Najszybciej sprzedane (od dodania do systemu)</div>{rows}</div>'

    wolne_html = ""
    if najwolniejsze:
        rows = "".join(item_row_w(r, "var(--red)", i, len(najwolniejsze)) for i, r in enumerate(najwolniejsze))
        wolne_html = f'<div class="card"><div style="font-weight:700;color:var(--red);margin-bottom:12px"><span class=material-symbols-outlined>speed</span> Najwolniej sprzedane (od dodania do systemu)</div>{rows}</div>'

    chart_html = ""
    if dw:
        chart_html = f"""
        <div class="card">
            <div style="font-weight:700;color:var(--text-secondary);margin-bottom:12px"><span class=material-symbols-outlined>bar_chart</span> Rozkład czasu sprzedaży (od dodania do systemu)</div>
            <canvas id="histChart" style="max-height:180px"></canvas>
        </div>
        <script src="https://cdn.jsdelivr.net/npm/chart.js@3/dist/chart.min.js" integrity="sha384-9MhbyIRcBVQiiC7FSd7T38oJNj2Zh+EfxS7/vjhBi4OOT78NlHSnzM31EZRWR1LZ" crossorigin="anonymous"></script>
        <script>
        new Chart(document.getElementById('histChart').getContext('2d'),{{
            type:'bar',
            data:{{labels:{lbl_j},datasets:[{{data:{dat_j},
                backgroundColor:['#22c55e','#22c55e','#3b82f6','#3b82f6','#f59e0b','#ef4444','#ef4444','#7f1d1d'],
                borderRadius:6}}]}},
            options:{{responsive:true,plugins:{{legend:{{display:false}}}},
                scales:{{y:{{beginAtZero:true,grid:{{color:'rgba(255,255,255,0.07)'}},ticks:{{color:'#64748b'}}}},
                         x:{{grid:{{display:false}},ticks:{{color:'#94a3b8',font:{{size:11}}}}}}}}}}
        }});
        </script>"""

    html = f"""
    <div style="text-align:center;margin-bottom:5px;color:var(--text-muted);font-size:0.8rem">Od dodania do systemu i zakupu palety do sprzedaży</div>
    <a href="/analityka" style="color:var(--text-muted);font-size:0.85rem;text-decoration:none;display:inline-block;margin-bottom:15px">← Wróć do analityki</a>
    <div class="dash-grid" style="margin-bottom:20px">
        <div class="card" style="border-color:rgba(34,197,94,0.4)">
            <div style="font-weight:700;color:var(--green);margin-bottom:12px;display:flex;align-items:center;gap:8px"><span class=material-symbols-outlined>list_alt</span> Od DODANIA DO SYSTEMU</div>
            {karta_w}
        </div>
        <div class="card" style="border-color:rgba(59,130,246,0.4)">
            <div style="font-weight:700;color:var(--blue);margin-bottom:12px;display:flex;align-items:center;gap:8px"><span class=material-symbols-outlined>local_shipping</span> Od ZAKUPU PALETY</div>
            {karta_z}
        </div>
    </div>
    {chart_html}
    {dostawcy_html}
    {szybkie_html}
    {wolne_html}
    <div class="card" style="border-color:rgba(59,130,246,0.25);display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
      <div>
        <div style='color:var(--blue);font-weight:600;font-size:0.85rem'><span class=material-symbols-outlined>calendar_month</span> Daty wystawienia ofert</div>
        <div style='color:var(--text-muted);font-size:0.75rem;margin-top:3px'>Znaki <strong style='color:var(--orange)'>?</strong> = brak daty wystawienia w bazie. Zsynchronizuj oferty z Allegro żeby uzupełnić daty.</div>
      </div>
      <a href='/allegro/sync-oferty-daty' class='btn btn-sm btn-primary' style='text-decoration:none;white-space:nowrap'><span class=material-symbols-outlined>sync</span> Odśwież daty z Allegro</a>
    </div>
    <a href="/analityka" class="btn btn-secondary" style="display:block;text-align:center;margin-top:20px;text-decoration:none">← Powrót do analityki</a>
    """
    return render(html, 'Czas Sprzedazy')



@analityka_bp.route('/analityka/uzupelnij-adresy', methods=['POST'])
def analityka_uzupelnij_adresy():
    """Uzupełnia adresy dla istniejących zamówień z Allegro"""
    from modules.database import get_db
    from modules.allegro_api import get_orders
    from datetime import datetime, timedelta

    conn = get_db()

    # Pobierz zamówienia bez adresów
    sprzedaze_bez_adresow = conn.execute('''
        SELECT id, allegro_order_id FROM sprzedaze
        WHERE (adres IS NULL OR adres = '') AND allegro_order_id IS NOT NULL
    ''').fetchall()

    if not sprzedaze_bez_adresow:
        return jsonify({'ok': True, 'count': 0, 'message': 'Wszystkie zamówienia mają adresy'})

    # Pobierz zamówienia z Allegro (ostatni miesiąc)
    from_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%dT00:00:00Z')
    all_orders = []

    for status in ['READY_FOR_PROCESSING', 'SENT', 'BOUGHT']:
        orders_data, error = get_orders(status, from_date=from_date)
        if orders_data and 'checkoutForms' in orders_data:
            all_orders.extend(orders_data['checkoutForms'])

    # Stwórz mapę order_id -> adres
    adresy_map = {}
    for order in all_orders:
        order_id = order['id']
        delivery = order.get('delivery', {})
        address = delivery.get('address', {})
        adres_parts = []
        if address.get('street'):
            adres_parts.append(address.get('street'))
        if address.get('postCode'):
            adres_parts.append(address.get('postCode'))
        if address.get('city'):
            adres_parts.append(address.get('city'))
        if adres_parts:
            adresy_map[order_id] = ', '.join(adres_parts)

    # Zaktualizuj adresy
    updated = 0
    for s in sprzedaze_bez_adresow:
        order_id = s['allegro_order_id']
        if order_id in adresy_map:
            conn.execute('UPDATE sprzedaze SET adres = ? WHERE id = ?', (adresy_map[order_id], s['id']))
            updated += 1

    conn.commit()

    return jsonify({'ok': True, 'count': updated, 'total': len(sprzedaze_bez_adresow)})


# ============================================================
#  ANALIZATOR PALET — Perplexity AI analiza produktów z palety
# ============================================================

# Osobny słownik statusów i wyników dla analizatora palet
_pallet_analysis_jobs = {}
_pallet_analysis_results = {}


def _run_pallet_analysis(job_id, paleta_id, api_key, db_path, model="gemini-2.5-flash", excel_products=None, provider="gemini"):
    """Uruchamia analizę palety w tle — wysyła produkty do AI (Gemini/Perplexity), parsuje JSON.
    excel_products: opcjonalna lista dictów z Excela (analiza przed zakupem)"""
    import requests as _req, json as _json, sqlite3 as _sq, re as _re
    _pallet_analysis_jobs[job_id] = {'status': 'running', 'progress': 'Przygotowywanie...'}
    try:
        conn2 = _sq.connect(db_path, timeout=30)
        conn2.row_factory = _sq.Row

        if excel_products:
            # Analiza zakupu z Excela — nie ma palety w DB
            produkty_list = excel_products
            paleta_dict = {'id': 0, 'nazwa': 'Analiza przed zakupem', 'dostawca': 'Excel', 'cena_zakupu': 0, 'data_zakupu': ''}
        else:
            paleta = conn2.execute("SELECT * FROM palety WHERE id = ?", (paleta_id,)).fetchone()
            if not paleta:
                _pallet_analysis_jobs[job_id] = {'status': 'error', 'error': 'Paleta nie znaleziona'}
                conn2.close()
                return

            produkty = conn2.execute(
                "SELECT id, ean, asin, nazwa, ilosc, cena_netto, cena_brutto, kategoria, stan, zdjecie_url FROM produkty WHERE paleta_id = ?",
                (paleta_id,)
            ).fetchall()

            if not produkty:
                _pallet_analysis_jobs[job_id] = {'status': 'error', 'error': 'Brak produktów w palecie'}
                conn2.close()
                return

            produkty_list = []
            for p in produkty:
                pd = dict(p)
                if pd.get('zdjecie_url'):
                    pd['image_url'] = pd['zdjecie_url']
                produkty_list.append(pd)
            paleta_dict = dict(paleta)

        koszt_palety = paleta_dict.get('cena_zakupu', 0) or 0

        # Dziel na batche po max 15 produktów
        BATCH_SIZE = 15
        all_results = []
        all_citations = []
        batches = [produkty_list[i:i+BATCH_SIZE] for i in range(0, len(produkty_list), BATCH_SIZE)]

        for batch_idx, batch in enumerate(batches):
            _pallet_analysis_jobs[job_id] = {
                'status': 'running',
                'progress': f'Analizuję batch {batch_idx+1}/{len(batches)} ({len(batch)} produktów)...'
            }

            # Pre-processing: rozpoznaj ASIN w nazwie (bez scrapingu — AI sam rozpozna)
            import re as _re_pre
            for p in batch:
                nazwa = p.get('nazwa', '')
                asin_val = p.get('asin', '')
                # Jeśli nazwa to ASIN (B0...), przenieś do pola asin
                if _re_pre.match(r'^B0[A-Z0-9]{8,10}$', nazwa.strip().upper()):
                    if not asin_val:
                        p['asin'] = nazwa.strip().upper()

            # Buduj prompt dla batcha
            has_codes = any(bool(_re_pre.match(r'^[\d\-\.\/\s]{5,}$', p.get('nazwa','').strip())) for p in batch)
            batch_txt = ""
            for i, p in enumerate(batch, 1):
                ean_str = f", EAN: {p.get('ean','')}" if p.get('ean') else ""
                asin_str = f", ASIN: {p.get('asin','')}" if p.get('asin') else ""
                nazwa_orig = f" (kod: {p.get('nazwa_oryginalna','')})" if p.get('nazwa_oryginalna') else ""
                batch_txt += (
                    f"{i}. {p.get('nazwa','?')[:120]}{nazwa_orig} "
                    f"[{p.get('kategoria') or 'inne'}] "
                    f"— {p.get('ilosc', 1)} szt, "
                    f"cena Amazon RRP: {float(p.get('cena_brutto', 0) or 0):.2f} zł"
                    f"{ean_str}{asin_str}\n"
                )

            code_hint = ""
            if has_codes:
                code_hint = (
                    "UWAGA: Niektóre produkty mają kody zamiast nazw. "
                    "Zidentyfikuj produkt po kodzie EAN/numer i podaj PRAWDZIWĄ nazwę produktu w polu 'nazwa'.\n\n"
                )

            batch_prompt = (
                f"Jesteś ekspertem od resellingu zwrotów Amazon na Allegro.pl w Polsce.\n"
                f"Wyszukaj REALNE aktualne ceny tych produktów na Allegro.pl.\n\n"
                f"{code_hint}"
                f"PRODUKTY (batch {batch_idx+1}/{len(batches)}):\n{batch_txt}\n"
                f"Dla każdego produktu podaj REALNĄ cenę na Allegro.pl, popyt i czas sprzedaży.\n"
                f"Jeśli produkt ma kod zamiast nazwy, zidentyfikuj go i podaj prawdziwą nazwę.\n"
                f"WAŻNE: Pole 'nazwa' ZAWSZE podaj po POLSKU (przetłumacz z angielskiego jeśli trzeba).\n"
                f"Np. 'Wheelchair ramp folding...' → 'Rampa dla wózka inwalidzka składana...'\n\n"
                f"Odpowiedz WYŁĄCZNIE jako JSON array (bez markdown):\n"
                f'[{{"id": 1, "nazwa": "POLSKA NAZWA PRODUKTU", "cena_allegro": <float>, "popyt": "wysoki|średni|niski", '
                f'"czas_sprzedazy_dni": <int>, "uwagi": "krótki komentarz po polsku"}}]\n'
            )

            if provider == 'perplexity':
                resp = _req.post(
                    "https://api.perplexity.ai/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": [{"role": "user", "content": batch_prompt}],
                          "max_tokens": 8000, "return_citations": True},
                    timeout=180)
                data = resp.json()
                if 'error' in data:
                    raise Exception(f"Perplexity API: {data['error'].get('message', data['error'])}")
                batch_answer = data['choices'][0]['message']['content']
                batch_cit = data.get('citations', [])
            else:
                # Gemini API z response_schema — wymusza czysty JSON bez markdown
                _schema = {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "id": {"type": "INTEGER"},
                            "nazwa": {"type": "STRING"},
                            "cena_allegro": {"type": "NUMBER"},
                            "popyt": {"type": "STRING"},
                            "czas_sprzedazy_dni": {"type": "INTEGER"},
                            "uwagi": {"type": "STRING"}
                        },
                        "required": ["id", "nazwa", "cena_allegro", "popyt", "czas_sprzedazy_dni", "uwagi"]
                    }
                }
                resp = _req.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
                    headers={"Content-Type": "application/json"},
                    json={
                        "contents": [{"parts": [{"text": batch_prompt}]}],
                        "generationConfig": {
                            "maxOutputTokens": 8000,
                            "temperature": 0.3,
                            "response_mime_type": "application/json",
                            "response_schema": _schema
                        }
                    },
                    timeout=180)
                data = resp.json()
                if 'error' in data:
                    raise Exception(f"Gemini API: {data['error'].get('message', data['error'])}")
                if 'candidates' not in data or not data['candidates']:
                    raise Exception(f"Gemini: brak odpowiedzi. Odpowiedz API: {str(data)[:300]}")
                batch_answer = data['candidates'][0]['content']['parts'][0]['text']
                batch_cit = []
            all_citations.extend(batch_cit)

            # Parsuj JSON z odpowiedzi batcha
            # response_schema wymusza czysty JSON — prosta próba, fallback na regex
            batch_parsed = None
            try:
                batch_parsed = _json.loads(batch_answer.strip())
            except Exception:
                # Fallback: szukaj JSON array [...] lub object {...}
                match = _re.search(r'\[[\s\S]*\]', batch_answer)
                if match:
                    try:
                        batch_parsed = _json.loads(match.group())
                    except Exception:
                        pass
                if not batch_parsed:
                    match = _re.search(r'\{[\s\S]*\}', batch_answer)
                    if match:
                        try:
                            obj = _json.loads(match.group())
                            batch_parsed = obj.get('produkty', [obj])
                        except Exception:
                            pass

            if isinstance(batch_parsed, dict):
                batch_parsed = batch_parsed.get('produkty', [batch_parsed])
            if isinstance(batch_parsed, list):
                # Mapuj nazwy i ilości z oryginalnych produktów
                for j, item in enumerate(batch_parsed):
                    if not isinstance(item, dict):
                        continue
                    if j < len(batch):
                        orig_nazwa = batch[j].get('nazwa', '?')
                        ai_nazwa = item.get('nazwa', '')
                        # Użyj nazwy z AI jeśli jest lepsza (przetłumaczona na PL lub rozpoznana z kodu)
                        is_orig_code = bool(_re.match(r'^[\d\-\.\/\s]{5,}$', str(orig_nazwa).strip()))
                        if ai_nazwa and len(ai_nazwa) > 5 and ai_nazwa != orig_nazwa:
                            item['nazwa'] = ai_nazwa  # AI przetłumaczył/rozpoznał
                            if is_orig_code:
                                item['kod_oryginalny'] = orig_nazwa
                        else:
                            item['nazwa'] = orig_nazwa
                        item['cena_amazon_rpp'] = float(batch[j].get('cena_brutto', 0) or 0)
                        item['ilosc'] = int(batch[j].get('ilosc', 1) or 1)
                        item['asin'] = batch[j].get('asin', '')
                        item['ean'] = batch[j].get('ean', '')
                        if batch[j].get('image_url'):
                            item['image_url'] = batch[j]['image_url']
                all_results.extend(item for item in batch_parsed if isinstance(item, dict))
            else:
                # Fallback — dodaj surowe wyniki
                for p in batch:
                    fallback = {'nazwa': p.get('nazwa','?'), 'cena_allegro': 0,
                               'cena_amazon_rpp': float(p.get('cena_brutto', 0) or 0),
                               'ilosc': int(p.get('ilosc', 1) or 1),
                               'asin': p.get('asin', ''), 'ean': p.get('ean', ''),
                               'popyt': '?', 'czas_sprzedazy_dni': 0,
                               'uwagi': 'Nie udało się sparsować odpowiedzi AI'}
                    if p.get('image_url'):
                        fallback['image_url'] = p['image_url']
                    all_results.append(fallback)

            # Update progress po batchu
            pct = int((batch_idx + 1) / len(batches) * 100)
            _pallet_analysis_jobs[job_id] = {
                'status': 'running',
                'progress': f'✅ Batch {batch_idx+1}/{len(batches)} gotowy ({pct}%) — {len(all_results)} produktów przeanalizowanych'
            }

        # === WERYFIKACJA CEN Z ALLEGRO API ===
        try:
            from modules.paletomat import _search_allegro_prices
            _pallet_analysis_jobs[job_id] = {
                'status': 'running',
                'progress': f'🔍 Weryfikuję ceny na Allegro (0/{len(all_results)})...'
            }
            allegro_verified = 0
            allegro_corrected = 0
            for ri, r in enumerate(all_results):
                try:
                    ean = r.get('ean', '') or ''
                    nazwa = r.get('nazwa', '') or ''
                    prices = _search_allegro_prices(ean=ean, nazwa=nazwa)
                    if prices:
                        # Mediana cen z Allegro
                        prices.sort()
                        median_price = prices[len(prices)//2]
                        r['cena_allegro_real'] = median_price
                        r['allegro_ofert'] = len(prices)
                        r['allegro_min'] = min(prices)
                        r['allegro_max'] = max(prices)
                        # Korekta: jeśli Gemini dał cenę > 30% różnicy od mediany
                        gemini_cena = r.get('cena_allegro', 0) or 0
                        if gemini_cena > 0 and abs(gemini_cena - median_price) / median_price > 0.3:
                            r['cena_allegro_gemini'] = gemini_cena
                            r['cena_allegro'] = round(median_price, 2)
                            allegro_corrected += 1
                        allegro_verified += 1
                except Exception:
                    pass
                prod_nazwa = (r.get('nazwa', '') or '')[:40]
                _pallet_analysis_jobs[job_id] = {
                    'status': 'running',
                    'progress': f'🔍 Weryfikuję ceny na Allegro ({ri+1}/{len(all_results)})... ✅ {allegro_verified} zweryfikowanych, 🔄 {allegro_corrected} skorygowanych',
                    'detail': f'Sprawdzam: {prod_nazwa}...'
                }
                import time
                time.sleep(0.3)  # Rate limit Allegro API
            print(f'[Analizator] Allegro verification: {allegro_verified} verified, {allegro_corrected} corrected')
        except ImportError:
            print('[Analizator] Allegro API not available, skipping price verification')
        except Exception as e:
            print(f'[Analizator] Allegro verification error: {e}')

        # Podsumowanie — mnóż ceny przez ilość sztuk
        total_przychod = sum(r.get('cena_allegro', 0) * r.get('ilosc', 1) for r in all_results)
        prowizja = total_przychod * 0.11
        zysk = total_przychod - prowizja - koszt_palety
        roi = (zysk / koszt_palety * 100) if koszt_palety > 0 else 0
        ocena = min(10, max(1, int(roi / 15) + 3)) if koszt_palety > 0 else 5

        parsed = {
            'produkty': all_results,
            'podsumowanie': {
                'przychod': total_przychod,
                'prowizja_allegro': prowizja,
                'zysk': zysk,
                'roi': roi,
                'ocena': ocena
            }
        }

        # Zapisz do cache
        conn2.execute("""CREATE TABLE IF NOT EXISTS perplexity_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT, klucz TEXT UNIQUE,
            odpowiedz TEXT, citations TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        from datetime import datetime as _dt
        cache_klucz = f'paleta_analiza_{paleta_id}'
        odpowiedz = _json.dumps(parsed, ensure_ascii=False)
        conn2.execute(
            "INSERT OR REPLACE INTO perplexity_cache (klucz, odpowiedz, citations, created_at) VALUES (?, ?, ?, ?)",
            (cache_klucz, odpowiedz, _json.dumps(all_citations), _dt.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn2.commit()
        conn2.close()

        _pallet_analysis_results[job_id] = {
            'raw': odpowiedz,
            'parsed': parsed,
            'citations': all_citations,
            'paleta': paleta_dict,
            'produkty_db': produkty_list,
        }
        _pallet_analysis_jobs[job_id] = {'status': 'done'}
        print(f"[Analizator Palet] {job_id} gotowe, {len(all_results)} produktów, ROI={roi:.0f}%")

    except Exception as e:
        _pallet_analysis_jobs[job_id] = {'status': 'error', 'error': str(e)}
        print(f"[Analizator Palet] blad {job_id}: {e}")


@analityka_bp.route('/analityka/analizator-palet')
def analizator_palet():
    """Strona główna analizatora palet — wybór palety + wyniki."""
    from modules.database import get_db, get_config
    conn = get_db()

    palety = conn.execute(
        "SELECT p.id, p.nazwa, p.dostawca, p.cena_zakupu, p.data_zakupu, "
        "       (SELECT COUNT(*) FROM produkty pr WHERE pr.paleta_id = p.id) as cnt_produktow "
        "FROM palety p ORDER BY p.data_zakupu DESC, p.id DESC"
    ).fetchall()
    palety_list = [dict(p) for p in palety]

    provider = get_config('analyzer_ai_provider', 'gemini')
    if provider == 'perplexity':
        ai_api_key = get_config('perplexity_api_key', '')
        provider_name = 'Perplexity'
    else:
        provider = 'gemini'
        ai_api_key = get_config('gemini_api_key', '')
        provider_name = 'Gemini'
    has_api_key = bool(ai_api_key)

    # Build palety options
    options_html = '<option value="">— Wybierz paletę —</option>'
    for p in palety_list:
        nazwa = p.get('nazwa') or f"Paleta #{p['id']}"
        dostawca = p.get('dostawca') or ''
        data = p.get('data_zakupu') or ''
        koszt = p.get('cena_zakupu') or 0
        cnt = p.get('cnt_produktow') or 0
        label = f"{nazwa} | {dostawca} | {data} | {koszt:,.0f} zł | {cnt} prod."
        options_html += f'<option value="{p["id"]}">{label}</option>'

    no_key_warning = ""
    if not has_api_key:
        no_key_warning = f"""
        <div class='card' style='border-color:var(--red);margin-bottom:16px'>
            <div style='color:var(--red);font-weight:600;margin-bottom:6px'>Brak klucza API {provider_name}</div>
            <div style='color:var(--text-muted);font-size:0.83rem'>
                Ustaw klucz w <a href='/ustawienia' style='color:var(--blue)'>Ustawienia</a> &rarr; {provider_name} API Key
            </div>
        </div>"""

    content = f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700;900&family=Manrope:wght@300;400;500;600;700;800&display=swap');
    @import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap');
    .material-symbols-outlined{{font-variation-settings:'FILL' 0,'wght' 400,'GRAD' 0,'opsz' 24}}

    /* ─── Stitch tokens ─── */
    :root{{--ap-cyan:#8ff5ff;--ap-pink:#ff6b9b;--ap-lime:#cafd00;--ap-lime-dim:#beee00;--ap-bg:#0e0e10;--ap-card:#131315;--ap-card2:#19191c;--ap-card3:#1f1f22;--ap-card4:#262528;--ap-border:rgba(255,255,255,0.06);--ap-text:#f9f5f8;--ap-muted:#adaaad}}

    /* ─── Dot grid bg ─── */
    .ap-dot-grid{{position:fixed;inset:0;pointer-events:none;z-index:0;
        background-image:radial-gradient(rgba(72,71,74,0.5) 1px,transparent 1px);
        background-size:24px 24px}}

    /* ─── Neon flares ─── */
    .ap-flare-tl{{position:fixed;top:-120px;left:-120px;width:500px;height:500px;
        background:radial-gradient(circle,rgba(143,245,255,0.08) 0%,transparent 70%);pointer-events:none;z-index:0}}
    .ap-flare-br{{position:fixed;bottom:-120px;right:-120px;width:500px;height:500px;
        background:radial-gradient(circle,rgba(255,107,155,0.06) 0%,transparent 70%);pointer-events:none;z-index:0}}

    /* ─── Wrap ─── */
    .ap-wrap{{max-width:1100px;margin:0 auto;font-family:'Manrope',sans-serif;color:var(--ap-text);position:relative;z-index:1;padding:0 8px}}
    .ap-headline{{font-family:'Space Grotesk',sans-serif}}
    .ap-label{{font-family:'Manrope',sans-serif;font-size:10px;text-transform:uppercase;letter-spacing:0.2em;color:var(--ap-muted)}}

    /* ─── Glass panel ─── */
    .ap-glass{{background:rgba(19,19,21,0.60);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);border-left:2px solid var(--ap-cyan);box-shadow:0 0 30px rgba(143,245,255,0.07),0 4px 24px rgba(0,0,0,0.4);padding:28px 32px;margin-bottom:20px}}
    .ap-glass-pink{{border-left-color:var(--ap-pink);box-shadow:0 0 30px rgba(255,107,155,0.07),0 4px 24px rgba(0,0,0,0.4)}}

    /* ─── Biotech icon circle ─── */
    .ap-icon-circle{{width:52px;height:52px;border-radius:50%;display:flex;align-items:center;justify-content:center;
        background:linear-gradient(135deg,rgba(143,245,255,0.12),rgba(143,245,255,0.04));
        border:1px solid rgba(143,245,255,0.18);flex-shrink:0}}
    .ap-icon-circle .material-symbols-outlined{{font-size:26px;color:var(--ap-cyan)}}

    /* ─── Tab selector ─── */
    .ap-tabs{{display:flex;gap:0;margin-bottom:24px;border-bottom:1px solid var(--ap-border)}}
    .tab-btn{{padding:12px 24px;background:transparent;border:none;color:var(--ap-muted);font-family:'Space Grotesk',sans-serif;font-size:0.88rem;font-weight:600;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px;transition:all 0.2s;letter-spacing:-0.01em}}
    .tab-btn:hover{{color:var(--ap-text)}}
    .tab-btn.active{{color:var(--ap-cyan);border-bottom-color:var(--ap-cyan)}}

    /* ─── Stats bento ─── */
    .ap-stats-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}}
    .ap-stat-card{{background:var(--ap-card);padding:20px 24px;border-left:2px solid var(--ap-cyan);transition:background 0.2s}}
    .ap-stat-card:hover{{background:var(--ap-card3)}}
    .ap-stat-card.pink{{border-left-color:var(--ap-pink)}}
    .ap-stat-val{{font-family:'Space Grotesk',sans-serif;font-size:2rem;font-weight:700;line-height:1.1}}
    .ap-stat-label{{font-family:'Manrope',sans-serif;font-size:10px;text-transform:uppercase;letter-spacing:0.18em;color:var(--ap-muted);margin-top:4px}}

    /* ─── Select ─── */
    .ap-select{{width:100%;padding:14px 18px;font-size:0.95rem;font-family:'Manrope',sans-serif;
        background:var(--ap-card4);border:none;color:var(--ap-text);
        appearance:none;-webkit-appearance:none;cursor:pointer;transition:all 0.2s;
        background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%23adaaad' stroke-width='1.5' fill='none'/%3E%3C/svg%3E");
        background-repeat:no-repeat;background-position:right 16px center}}
    .ap-select:focus{{outline:none;box-shadow:0 0 0 1px var(--ap-cyan),0 0 12px rgba(143,245,255,0.15)}}

    /* ─── Button ─── */
    .ap-btn{{font-family:'Space Grotesk',sans-serif;font-weight:700;text-transform:uppercase;letter-spacing:0.02em;padding:14px 28px;border:none;cursor:pointer;transition:all 0.15s;font-size:0.95rem}}
    .ap-btn:hover{{transform:scale(1.02)}}
    .ap-btn:active{{transform:scale(0.95)}}
    .ap-btn:disabled{{opacity:0.4;cursor:not-allowed;transform:none}}
    .ap-btn-cyan{{background:var(--ap-cyan);color:#005d63;box-shadow:0 4px 20px rgba(143,245,255,0.3)}}
    .ap-btn-cyan:hover{{box-shadow:0 6px 30px rgba(143,245,255,0.45)}}

    /* ─── Form inputs ─── */
    .ap-input{{width:100%;padding:14px 18px;font-size:0.95rem;font-family:'Manrope',sans-serif;
        background:var(--ap-card4);border:none;color:var(--ap-text);transition:all 0.2s}}
    .ap-input:focus{{outline:none;box-shadow:0 0 0 1px var(--ap-cyan),0 0 12px rgba(143,245,255,0.15)}}
    .ap-input::placeholder{{color:var(--ap-muted)}}
    .ap-form-label{{display:block;font-family:'Manrope',sans-serif;font-size:10px;text-transform:uppercase;letter-spacing:0.18em;color:var(--ap-muted);margin-bottom:8px;font-weight:600}}

    /* ─── Progress ─── */
    .ap-progress-wrap{{background:rgba(19,19,21,0.6);backdrop-filter:blur(12px);border-left:2px solid var(--ap-cyan);padding:20px 24px;margin-top:16px}}
    .ap-progress-bar-track{{background:var(--ap-card4);height:4px;overflow:hidden;position:relative}}
    .ap-progress-bar-fill{{height:100%;background:var(--ap-cyan);box-shadow:0 0 12px var(--ap-cyan);transition:width 0.5s;animation:ap-pulse 2s infinite}}
    @keyframes ap-pulse{{0%,100%{{opacity:1}}50%{{opacity:0.6}}}}

    /* ─── Spinner ─── */
    @keyframes spin {{ 0%{{transform:rotate(0deg)}} 100%{{transform:rotate(360deg)}} }}
    .ap-spinner{{width:20px;height:20px;border:2px solid var(--ap-border);border-top-color:var(--ap-cyan);border-radius:50%;animation:spin 1s linear infinite}}

    /* ─── Demand badges ─── */
    .demand-badge {{
        display:inline-block; padding:2px 10px; border-radius:6px; font-size:0.78rem; font-weight:600;
    }}
    .demand-wysoki {{ background:rgba(34,197,94,0.15); color:var(--green); }}
    .demand-sredni, .demand-\\u015bredni {{ background:rgba(245,158,11,0.15); color:var(--orange); }}
    .demand-niski {{ background:rgba(239,68,68,0.15); color:var(--red); }}
    .analysis-table {{
        width:100%; border-collapse:collapse; font-size:0.83rem;
    }}
    .analysis-table th {{
        text-align:left; padding:10px 12px; background:var(--ap-card); color:var(--ap-muted);
        font-size:0.75rem; text-transform:uppercase; letter-spacing:0.03em; font-weight:600;
        border-bottom:1px solid var(--ap-border);
    }}
    .analysis-table td {{
        padding:10px 12px; border-bottom:1px solid var(--ap-border); vertical-align:top;
    }}
    .analysis-table tr:hover td {{ background:var(--ap-card2); }}

    /* ─── Responsive ─── */
    @media(max-width:768px){{
        .ap-stats-grid{{grid-template-columns:1fr!important}}
        .ap-action-row{{flex-direction:column!important}}
        .ap-action-row .ap-select{{min-width:0!important}}
    }}
    </style>

    <div class="ap-dot-grid"></div>
    <div class="ap-flare-tl"></div>
    <div class="ap-flare-br"></div>

    <div class="ap-wrap">

        <!-- ═══ HEADER ═══ -->
        <div style="display:flex;align-items:center;gap:16px;margin-bottom:28px">
            <div class="ap-icon-circle">
                <span class=material-symbols-outlined>biotech</span>
            </div>
            <div>
                <div class="ap-headline" style="font-size:1.35rem;font-weight:700;color:var(--ap-text)">Analizator Palet</div>
                <div style="color:var(--ap-muted);font-size:0.82rem;font-family:'Manrope',sans-serif">AI analizuje produkty z palety — ceny rynkowe, popyt, czas sprzedaży</div>
            </div>
        </div>

        {no_key_warning}

        <!-- ═══ STATS BENTO ═══ -->
        <div class="ap-stats-grid">
            <div class="ap-stat-card">
                <div class="ap-stat-val" style="color:var(--ap-cyan)">{len(palety_list)}</div>
                <div class="ap-stat-label">Palet łącznie</div>
            </div>
            <div class="ap-stat-card pink">
                <div class="ap-stat-val" style="color:var(--ap-pink)">0</div>
                <div class="ap-stat-label">W trakcie analizy</div>
            </div>
        </div>

        <!-- ═══ TABS ═══ -->
        <div class="ap-tabs">
            <button class="tab-btn active" onclick="switchTab('palety')" id="tab-palety">Moje palety</button>
            <button class="tab-btn" onclick="switchTab('zakup')" id="tab-zakup">Analiza zakupu</button>
        </div>

        <!-- ═══ TAB: MOJE PALETY ═══ -->
        <div id="panel-palety">
            <div class="ap-glass">
                <div class="ap-label" style="margin-bottom:14px">Wybierz paletę do analizy</div>
                <div class="ap-action-row" style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
                    <select id='paleta-select' class='ap-select' style='flex:1;min-width:300px'>
                        {options_html}
                    </select>
                    <button id='btn-analizuj' class='ap-btn ap-btn-cyan' onclick='startAnalysis()' {'disabled' if not has_api_key else ''}>
                        Analizuj
                    </button>
                </div>
            </div>
        </div>

        <!-- ═══ TAB: ANALIZA ZAKUPU ═══ -->
        <div id="panel-zakup" style="display:none">
            <div class="ap-glass">
                <p style="color:var(--ap-muted);font-size:0.85rem;margin-bottom:20px;font-family:'Manrope',sans-serif">
                    Wgraj manifest palety (Excel/XLSX) z listą produktów. AI sprawdzi realne ceny na Allegro i powie czy warto kupić.
                </p>
                <form id="excel-form" enctype="multipart/form-data">
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:18px">
                        <div>
                            <label class="ap-form-label">Plik Excel (XLSX)</label>
                            <input type="file" name="file" accept=".xlsx,.xls,.csv" class="ap-input" required>
                        </div>
                        <div>
                            <label class="ap-form-label">Koszt palety (PLN)</label>
                            <input type="number" name="koszt" class="ap-input" placeholder="np. 3500" step="0.01" value="0">
                        </div>
                    </div>
                    <button type="submit" class="ap-btn ap-btn-cyan" id="btn-analyze-excel">Analizuj przed zakupem</button>
                </form>
                <div id="excel-progress" style="display:none;margin-top:16px">
                    <div class="ap-progress-wrap">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                            <span id="excel-progress-text" style="color:var(--ap-cyan);font-weight:600;font-size:0.9rem;font-family:'Space Grotesk',sans-serif">⏳ Analizuję...</span>
                            <span id="excel-progress-pct" style="color:var(--ap-lime);font-weight:700;font-size:1.1rem;font-family:'Space Grotesk',sans-serif">0%</span>
                        </div>
                        <div class="ap-progress-bar-track">
                            <div id="excel-progress-bar" class="ap-progress-bar-fill" style="width:0%"></div>
                        </div>
                        <div id="excel-progress-detail" style="color:var(--ap-muted);font-size:0.78rem;margin-top:8px;font-family:'Manrope',sans-serif">Przygotowywanie...</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- ═══ STATUS ═══ -->
        <div id='analysis-status' style='display:none' class='ap-glass'>
            <div style='display:flex;align-items:center;gap:12px'>
                <div class='ap-spinner'></div>
                <span id='status-text' style='color:var(--ap-muted);font-size:0.85rem;font-family:Manrope,sans-serif'>Rozpoczynam analizę...</span>
            </div>
        </div>

        <!-- ═══ RESULTS ═══ -->
        <div id='analysis-results'></div>
    </div>

    <script>
    function switchTab(tab) {{
        document.getElementById('panel-palety').style.display = tab === 'palety' ? 'block' : 'none';
        document.getElementById('panel-zakup').style.display = tab === 'zakup' ? 'block' : 'none';
        document.getElementById('tab-palety').className = 'tab-btn' + (tab === 'palety' ? ' active' : '');
        document.getElementById('tab-zakup').className = 'tab-btn' + (tab === 'zakup' ? ' active' : '');
    }}
    if (window.location.search.includes('tab=zakup')) switchTab('zakup');

    var currentJobId = null;
    var pollTimer = null;

    function startAnalysis() {{
        var sel = document.getElementById('paleta-select');
        var paletaId = sel.value;
        if (!paletaId) {{ alert('Wybierz paletę!'); return; }}

        var btn = document.getElementById('btn-analizuj');
        btn.disabled = true; btn.textContent = ' Analizuję...';

        var statusDiv = document.getElementById('analysis-status');
        statusDiv.style.display = 'block';
        statusDiv.className = 'card';
        document.getElementById('analysis-results').innerHTML = '';

        fetch('/analityka/analizator-palet/analyze', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
            body: 'paleta_id=' + paletaId
        }})
        .then(r => r.json())
        .then(d => {{
            if (!d.ok) {{
                btn.disabled = false; btn.textContent = ' Analizuj';
                statusDiv.innerHTML = '<div style="color:var(--red)">Błąd: ' + (d.error||'nieznany') + '</div>';
                return;
            }}
            currentJobId = d.job_id;
            pollStatus();
        }})
        .catch(e => {{
            btn.disabled = false; btn.textContent = ' Analizuj';
            statusDiv.innerHTML = '<div style="color:var(--red)">Błąd połączenia</div>';
        }});
    }}

    function pollStatus() {{
        if (!currentJobId) return;
        fetch('/analityka/analizator-palet/status?job_id=' + currentJobId)
        .then(r => r.json())
        .then(d => {{
            if (d.status === 'running') {{
                document.getElementById('status-text').textContent = d.progress || 'Analizuję...';
                pollTimer = setTimeout(pollStatus, 2000);
            }} else if (d.status === 'done') {{
                document.getElementById('analysis-status').style.display = 'none';
                document.getElementById('btn-analizuj').disabled = false;
                document.getElementById('btn-analizuj').textContent = ' Analizuj';
                renderResults(d);
            }} else if (d.status === 'error') {{
                document.getElementById('analysis-status').innerHTML =
                    '<div style="color:var(--red)">Błąd: ' + (d.error||'nieznany') + '</div>';
                document.getElementById('btn-analizuj').disabled = false;
                document.getElementById('btn-analizuj').textContent = ' Analizuj';
            }}
        }})
        .catch(() => {{ pollTimer = setTimeout(pollStatus, 3000); }});
    }}

    document.getElementById('excel-form').addEventListener('submit', function(e) {{
        e.preventDefault();
        var btn = document.getElementById('btn-analyze-excel');
        btn.disabled = true; btn.textContent = 'Analizuję...';
        document.getElementById('excel-progress').style.display = 'block';
        document.getElementById('analysis-results').innerHTML = '';
        var fd = new FormData(this);
        fetch('/analityka/analiza-zakupu', {{method:'POST', body:fd}})
        .then(r => r.json()).then(function(d) {{
            if (!d.ok) {{ alert(d.error); btn.disabled=false; btn.textContent=' Analizuj przed zakupem'; document.getElementById('excel-progress').style.display='none'; return; }}
            document.getElementById('excel-progress-text').textContent = 'Znaleziono ' + d.produktow + ' produktów, analizuję...';
            pollExcelStatus(d.job_id);
        }}).catch(function(e) {{ alert('Błąd: ' + e); btn.disabled=false; btn.textContent=' Analizuj przed zakupem'; document.getElementById('excel-progress').style.display='none'; }});
    }});

    var excelTotalBatches = 1;
    function pollExcelStatus(jobId) {{
        fetch('/analityka/analizator-palet/status?job_id=' + jobId)
        .then(r => r.json()).then(function(d) {{
            if (d.status === 'running') {{
                var prog = d.progress || 'Analizuję...';
                document.getElementById('excel-progress-text').textContent = ' ' + prog;
                // Parsuj "batch X/Y" z progressu
                var m = prog.match(/batch\\s+(\\d+)\\/(\\d+)/);
                if (m) {{
                    var cur = parseInt(m[1]);
                    var total = parseInt(m[2]);
                    excelTotalBatches = total;
                    var pct = Math.round((cur - 1) / total * 100);
                    document.getElementById('excel-progress-bar').style.width = pct + '%';
                    document.getElementById('excel-progress-pct').textContent = pct + '%';
                    document.getElementById('excel-progress-detail').textContent = 'Batch ' + cur + ' z ' + total + ' • Gemini analizuje ' + (m[0].match(/\\((\\d+)/)?.[1] || '15') + ' produktów...';
                }}
                // Parsuj "(X produktów)" z progressu
                var mp = prog.match(/\\((\\d+)\\s+produkt/);
                if (mp) {{
                    document.getElementById('excel-progress-detail').textContent = 'Analizuję ' + mp[1] + ' produktów w tym batchu...';
                }}
                // Pokaż nazwę weryfikowanego produktu
                if (d.detail) {{
                    document.getElementById('excel-progress-detail').textContent = d.detail;
                }}
                setTimeout(function() {{ pollExcelStatus(jobId); }}, 2000);
            }} else if (d.status === 'done') {{
                document.getElementById('excel-progress-bar').style.width = '100%';
                document.getElementById('excel-progress-pct').textContent = '100%';
                document.getElementById('excel-progress-text').textContent = ' Gotowe!';
                document.getElementById('excel-progress-detail').textContent = 'Analiza zakończona pomyślnie';
                setTimeout(function() {{
                    document.getElementById('excel-progress').style.display = 'none';
                    document.getElementById('btn-analyze-excel').disabled = false;
                    document.getElementById('btn-analyze-excel').textContent = ' Analizuj przed zakupem';
                    renderResults(d);
                }}, 1000);
            }} else if (d.status === 'error') {{
                document.getElementById('excel-progress-text').textContent = ' Błąd';
                document.getElementById('excel-progress-detail').textContent = d.error || 'Nieznany błąd';
                document.getElementById('excel-progress-bar').style.width = '100%';
                document.getElementById('excel-progress-bar').style.background = 'var(--red)';
                document.getElementById('btn-analyze-excel').disabled = false;
                document.getElementById('btn-analyze-excel').textContent = ' Analizuj przed zakupem';
            }}
        }}).catch(function() {{ setTimeout(function() {{ pollExcelStatus(jobId); }}, 3000); }});
    }}

    {_get_render_results_js()}
    </script>
    """

    return render(content, page_title='Analizator Palet')


@analityka_bp.route('/analityka/analizator-palet/analyze', methods=['POST'])
def analizator_palet_analyze():
    """Rozpocznij analizę palety — uruchamia AI (Gemini/Perplexity) w tle."""
    import threading
    from modules.database import get_config, DATABASE as _db_path

    provider = get_config('analyzer_ai_provider', 'gemini')
    if provider == 'perplexity':
        api_key = get_config('perplexity_api_key', '')
        ai_model = get_config('perplexity_model', 'sonar-pro')
        if ai_model == 'sonar':
            ai_model = 'sonar-pro'
    else:
        provider = 'gemini'
        api_key = get_config('gemini_api_key', '')
        ai_model = get_config('ai_model_analiza_palet', get_config('gemini_model', 'gemini-2.5-flash'))

    if not api_key:
        return jsonify({'ok': False, 'error': f'Brak klucza API {provider.title()}. Ustaw w Ustawienia.'})

    paleta_id = request.form.get('paleta_id', '')
    if not paleta_id:
        return jsonify({'ok': False, 'error': 'Nie wybrano palety'})

    job_id = f'paleta_{paleta_id}_{int(datetime.now().timestamp())}'

    # Sprawdź czy już nie biegnie analiza tej palety
    for jid, info in _pallet_analysis_jobs.items():
        if jid.startswith(f'paleta_{paleta_id}_') and isinstance(info, dict) and info.get('status') == 'running':
            return jsonify({'ok': True, 'job_id': jid})

    threading.Thread(
        target=_run_pallet_analysis,
        args=(job_id, int(paleta_id), api_key, _db_path, ai_model),
        kwargs={'provider': provider},
        daemon=True
    ).start()

    return jsonify({'ok': True, 'job_id': job_id})


@analityka_bp.route('/analityka/analiza-zakupu', methods=['GET', 'POST'])
def analiza_zakupu():
    """Analiza zakupu — wrzuć Excel z manifestem palety PRZED zakupem."""
    import threading
    from modules.database import get_config, DATABASE as _db_path

    if request.method == 'POST':
        try:
            provider = get_config('analyzer_ai_provider', 'gemini')
            if provider == 'perplexity':
                api_key = get_config('perplexity_api_key', '')
                ai_model = get_config('perplexity_model', 'sonar-pro')
                if ai_model == 'sonar':
                    ai_model = 'sonar-pro'
            else:
                provider = 'gemini'
                api_key = get_config('gemini_api_key', '')
                ai_model = get_config('ai_model_analiza_palet', get_config('gemini_model', 'gemini-2.5-flash'))

            if not api_key:
                return jsonify({'ok': False, 'error': f'Brak klucza API {provider.title()}. Ustaw w Ustawienia.'})

            file = request.files.get('file')
            if not file:
                return jsonify({'ok': False, 'error': 'Nie wgrano pliku'})

            koszt_palety = float(request.form.get('koszt', 0) or 0)

            # Parsuj Excel
            import openpyxl
            import io
            wb = openpyxl.load_workbook(io.BytesIO(file.read()), data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                return jsonify({'ok': False, 'error': 'Plik jest pusty'})

            # Auto-detect kolumn (nazwy w pierwszym wierszu)
            header = [str(c).lower().strip() if c else '' for c in rows[0]]

            # Nazwa: preferuj "product title" / "title" nad "product sku" / "product"
            col_nazwa = None
            # Priorytet 1: kolumny z "title" w nazwie
            col_nazwa = next((i for i, h in enumerate(header) if 'title' in h), None)
            # Priorytet 2: "nazwa", "name", "opis", "description" (ale nie "product description" = N/A)
            if col_nazwa is None:
                col_nazwa = next((i for i, h in enumerate(header) if any(k in h for k in ['nazwa','name','opis'])), None)
            # Priorytet 3: "produkt", "product" (ale nie SKU)
            if col_nazwa is None:
                col_nazwa = next((i for i, h in enumerate(header) if ('product' in h or 'produkt' in h) and 'sku' not in h), None)
            # Fallback: pierwsza kolumna
            if col_nazwa is None:
                col_nazwa = 0

            col_ean = next((i for i, h in enumerate(header) if any(k in h for k in ['ean','barcode'])), None)
            col_asin = next((i for i, h in enumerate(header) if 'asin' in h), None)
            col_ilosc = next((i for i, h in enumerate(header) if any(k in h for k in ['ilosc','qty','quantity','szt','sztuk','ilość'])), None)
            # Cena: preferuj "unit rrp" nad "total rrp"
            col_cena = next((i for i, h in enumerate(header) if 'unit' in h and any(k in h for k in ['rrp','price','cena'])), None)
            if col_cena is None:
                col_cena = next((i for i, h in enumerate(header) if any(k in h for k in ['cena','price','rrp','brutto','retail']) and 'total' not in h), None)
            if col_cena is None:
                col_cena = next((i for i, h in enumerate(header) if any(k in h for k in ['cena','price','rrp','brutto','retail'])), None)
            col_kat = next((i for i, h in enumerate(header) if any(k in h for k in ['kategoria','category','cat'])), None)
            col_brand = next((i for i, h in enumerate(header) if 'brand' in h), None)
            col_condition = next((i for i, h in enumerate(header) if 'condition' in h or 'stan' in h), None)
            col_image = next((i for i, h in enumerate(header) if any(k in h for k in ['image','foto','zdjecie','thumbnail','img','picture','photo'])), None)

            print(f"[Analiza zakupu] Kolumny: nazwa={col_nazwa}({header[col_nazwa] if col_nazwa is not None else '?'}), "
                  f"ean={col_ean}, asin={col_asin}, ilosc={col_ilosc}, cena={col_cena}, kat={col_kat}, brand={col_brand}")

            def _parse_price(val):
                """Parsuj cenę — obsługa 'zł169.97', '€12,50', '169,97 PLN' itp."""
                if val is None:
                    return 0.0
                if isinstance(val, (int, float)):
                    return float(val)
                import re as _re2
                s = str(val).strip()
                # Usuń walutę i białe znaki
                s = _re2.sub(r'[złPLNEURUSD€$£\s]', '', s, flags=_re2.IGNORECASE)
                # Zamień przecinek na kropkę (format europejski)
                s = s.replace(',', '.')
                # Usuń wszystko oprócz cyfr, kropki i minusa
                s = _re2.sub(r'[^\d.\-]', '', s)
                try:
                    return float(s) if s else 0.0
                except ValueError:
                    return 0.0

            produkty = []
            for row in rows[1:]:
                if not row:
                    continue
                # Sprawdź czy wiersz ma jakieś dane (nie same None)
                if col_nazwa is not None and col_nazwa < len(row) and row[col_nazwa]:
                    nazwa = str(row[col_nazwa]).strip()
                else:
                    continue
                if not nazwa or nazwa.lower() in ('n/a', 'none', 'total', ''):
                    continue

                p = {
                    'nazwa': nazwa,
                    'ean': str(row[col_ean] or '').strip() if col_ean is not None and col_ean < len(row) and row[col_ean] else '',
                    'asin': str(row[col_asin] or '').strip().upper() if col_asin is not None and col_asin < len(row) and row[col_asin] else '',
                    'ilosc': int(float(row[col_ilosc] or 1)) if col_ilosc is not None and col_ilosc < len(row) and row[col_ilosc] else 1,
                    'cena_brutto': _parse_price(row[col_cena]) if col_cena is not None and col_cena < len(row) and row[col_cena] else 0,
                    'kategoria': str(row[col_kat] or '').strip() if col_kat is not None and col_kat < len(row) else 'inne',
                }
                # Obrazek URL z Excela
                if col_image is not None and col_image < len(row) and row[col_image]:
                    img_val = str(row[col_image]).strip()
                    if img_val.startswith('http'):
                        p['image_url'] = img_val
                # Dodaj brand do nazwy jeśli go nie zawiera
                if col_brand is not None and col_brand < len(row) and row[col_brand]:
                    brand = str(row[col_brand]).strip()
                    if brand.lower() not in ('n/a', 'none', '') and brand.lower() not in p['nazwa'].lower():
                        p['nazwa'] = f"{brand} {p['nazwa']}"
                # Dodaj stan (condition)
                if col_condition is not None and col_condition < len(row) and row[col_condition]:
                    p['stan'] = str(row[col_condition]).strip()

                if p['nazwa'] and p['asin'].lower() not in ('n/a', 'none', ''):
                    produkty.append(p)
                elif p['nazwa']:
                    produkty.append(p)

            if not produkty:
                return jsonify({'ok': False, 'error': 'Nie znaleziono produktów w pliku'})

            job_id = f'excel_{int(datetime.now().timestamp())}'

            # Przekaż koszt palety
            excel_paleta = {'id': 0, 'nazwa': file.filename, 'dostawca': 'Excel', 'cena_zakupu': koszt_palety}

            def run_excel_analysis():
                _run_pallet_analysis(job_id, 0, api_key, _db_path, ai_model, excel_products=produkty, provider=provider)
                # Nadpisz paletę z kosztem
                if job_id in _pallet_analysis_results:
                    _pallet_analysis_results[job_id]['paleta'] = excel_paleta
                    # Przelicz podsumowanie z kosztem
                    if _pallet_analysis_results[job_id].get('parsed', {}).get('podsumowanie'):
                        s = _pallet_analysis_results[job_id]['parsed']['podsumowanie']
                        s['zysk'] = s.get('przychod', 0) - s.get('prowizja_allegro', 0) - koszt_palety
                        s['roi'] = (s['zysk'] / koszt_palety * 100) if koszt_palety > 0 else 0
                        s['ocena'] = min(10, max(1, int(s['roi'] / 15) + 3)) if koszt_palety > 0 else 5

            threading.Thread(target=run_excel_analysis, daemon=True).start()
            return jsonify({'ok': True, 'job_id': job_id, 'produktow': len(produkty)})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)})

    # GET — redirect to combined page
    return redirect('/analityka/analizator-palet?tab=zakup')


def _get_render_results_js():
    """Zwraca wspólny JS renderResults() dla obu stron analizatora."""
    return '''
    function sortProducts() {
        var sort = document.getElementById('product-sort').value;
        if (!sort || !window._analysisProducts) return;
        var prods = window._analysisProducts.slice();
        var demandOrder = {'wysoki': 3, 'średni': 2, 'sredni': 2, 'niski': 1};
        if (sort === 'popyt') {
            prods.sort(function(a, b) { return (demandOrder[(b.popyt||'').toLowerCase()] || 0) - (demandOrder[(a.popyt||'').toLowerCase()] || 0); });
        } else if (sort === 'cena_desc') {
            prods.sort(function(a, b) { return (b.cena_allegro||0) - (a.cena_allegro||0); });
        } else if (sort === 'cena_asc') {
            prods.sort(function(a, b) { return (a.cena_allegro||0) - (b.cena_allegro||0); });
        } else if (sort === 'wartosc') {
            prods.sort(function(a, b) { return ((b.cena_allegro||0)*(b.ilosc||1)) - ((a.cena_allegro||0)*(a.ilosc||1)); });
        } else if (sort === 'czas') {
            prods.sort(function(a, b) { return (a.czas_sprzedazy_dni||999) - (b.czas_sprzedazy_dni||999); });
        }
        var tableContainer = document.querySelector('#analysis-results .card:last-child');
        if (tableContainer) {
            var oldTable = tableContainer.querySelector('table');
            var oldInfo = document.getElementById('product-table-info');
            if (oldTable) oldTable.outerHTML = '';
            if (oldInfo) oldInfo.outerHTML = '';
            tableContainer.insertAdjacentHTML('beforeend', renderProductTable(prods));
        }
    }
    function copyName(el) {
        var txt = el.getAttribute('data-copytext');
        navigator.clipboard.writeText(txt).then(function() {
            el.style.outline = '2px solid #22c55e';
            setTimeout(function() { el.style.outline = ''; }, 500);
        });
    }
    function demandClass(d) {
        if (!d) return 'demand-unknown';
        var dl = d.toLowerCase();
        if (dl === 'wysoki') return 'demand-high';
        if (dl === 'niski') return 'demand-low';
        return 'demand-medium';
    }
    function renderResults(d) {
        var res = document.getElementById('analysis-results');
        if (!res) return;
        var pal = d.paleta || {};
        var parsed = d.parsed;
        var html = '<div class="card" style="margin-bottom:16px">';
        html += '<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:12px">';
        html += '<div style="font-weight:700;font-size:1rem">' + (pal.nazwa || 'Analiza') + '</div>';
        html += '<div style="color:var(--text-muted);font-size:0.8rem" class="dostawca-name">' + (pal.dostawca||'') + '</div>';
        html += '</div>';
        if (parsed && parsed.podsumowanie) {
            var s = parsed.podsumowanie;
            var roiColor = (s.roi||0) > 0 ? 'var(--green)' : 'var(--red)';
            var ocenaBg = (s.ocena||0) >= 7 ? 'var(--green)' : (s.ocena||0) >= 4 ? 'var(--orange)' : 'var(--red)';
            html += '<div class="kpi-grid" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px">';
            html += '<div class="kpi-card green"><div class="kpi-label">Koszt palety</div><div class="kpi-value">' + ((pal.cena_zakupu||0)).toFixed(0) + ' zł</div></div>';
            html += '<div class="kpi-card blue"><div class="kpi-label">Szac. przychód</div><div class="kpi-value">' + ((s.przychod||0)).toFixed(0) + ' zł</div></div>';
            html += '<div class="kpi-card purple"><div class="kpi-label">Prowizja 11%</div><div class="kpi-value">' + ((s.prowizja_allegro||0)).toFixed(0) + ' zł</div></div>';
            html += '<div class="kpi-card"><div class="kpi-label">Zysk netto</div><div class="kpi-value" style="color:' + roiColor + '">' + ((s.zysk||0)).toFixed(0) + ' zł</div></div>';
            html += '<div class="kpi-card"><div class="kpi-label">ROI</div><div class="kpi-value" style="color:' + roiColor + '">' + ((s.roi||0)).toFixed(0) + '%</div></div>';
            html += '<div class="kpi-card orange"><div class="kpi-label">Ocena</div><div class="kpi-value" style="color:' + ocenaBg + '">' + (s.ocena||'?') + '/10</div></div>';
            html += '</div>';
        }
        html += '</div>';
        // Zapisz dane globalnie do filtrowania
        window._analysisProducts = (parsed && parsed.produkty) ? parsed.produkty : [];
        window._analysisPaleta = d.paleta || {};

        if (parsed && parsed.produkty && parsed.produkty.length) {
            html += '<div class="card" style="overflow-x:auto">';
            // Wyszukiwarka / filtr
            html += '<div style="display:flex;gap:10px;align-items:center;margin-bottom:14px;flex-wrap:wrap">';
            html += '<div style="font-weight:600">Produkty (' + parsed.produkty.length + ' typów)</div>';
            html += '<input type="text" id="product-filter" placeholder="🔍 Filtruj np. peruka, wig, hair..." style="flex:1;min-width:200px;padding:8px 12px;background:rgba(0,0,0,0.3);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:0.85rem">';
            html += '<button onclick="filterProducts()" style="padding:8px 16px;background:var(--accent);border:none;border-radius:8px;color:#fff;cursor:pointer;font-weight:600">Filtruj</button>';
            html += '<select id="product-sort" onchange="sortProducts()" style="padding:8px 12px;background:rgba(0,0,0,0.3);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:0.85rem"><option value="">Sortuj...</option><option value="popyt">🔥 Popyt (wysoki→niski)</option><option value="cena_desc">💰 Cena (najdroższe)</option><option value="cena_asc">💰 Cena (najtańsze)</option><option value="wartosc">📊 Wartość (najwyższa)</option><option value="czas">⏱ Czas sprzedaży (najszybsze)</option></select>';
            html += '<button onclick="clearFilter()" style="padding:8px 12px;background:rgba(255,255,255,0.1);border:none;border-radius:8px;color:var(--text-muted);cursor:pointer">Wyczyść</button>';
            html += '</div>';
            html += '<div id="filter-summary" style="display:none;margin-bottom:12px;padding:10px;background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);border-radius:8px"></div>';
            html += renderProductTable(parsed.produkty);
            html += '</div>';
        } else if (d.raw) {
            html += '<div class="card"><div style="white-space:pre-wrap;font-size:0.83rem">' + d.raw.replace(/</g,'&lt;') + '</div></div>';
        }
        if (d.citations && d.citations.length) {
            html += '<div class="card"><div style="font-weight:600;margin-bottom:8px;font-size:0.85rem">Źródła</div>';
            d.citations.forEach(function(c, i) {
                html += '<div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:4px">[' + (i+1) + '] <a href="' + c + '" target="_blank" style="color:var(--accent)">' + c + '</a></div>';
            });
            html += '</div>';
        }
        res.innerHTML = html;
        // Bind enter key na filtr
        var fi = document.getElementById('product-filter');
        if (fi) fi.addEventListener('keyup', function(e) { if (e.key === 'Enter') filterProducts(); });
    }

    function renderProductTable(products) {
        var totalSzt = 0;
        products.forEach(function(p) { totalSzt += (p.ilosc || 1); });
        var h = '<div id="product-table-info" style="font-size:0.8rem;color:var(--text-muted);margin-bottom:8px">' + products.length + ' typów, ' + totalSzt + ' szt.</div>';
        h += '<table style="width:100%;border-collapse:collapse;font-size:0.83rem"><thead><tr style="border-bottom:2px solid var(--border)">';
        h += '<th style="padding:8px;text-align:left">#</th><th style="padding:8px">Foto</th><th style="padding:8px;text-align:left">Produkt</th><th style="padding:8px">Szt.</th><th style="padding:8px">Cena Allegro</th><th style="padding:8px">Wartość</th><th style="padding:8px">RRP Amazon</th><th style="padding:8px">Allegro real</th><th style="padding:8px">Popyt</th><th style="padding:8px">Czas</th><th style="padding:8px;text-align:left">Uwagi</th>';
        h += '</tr></thead><tbody>';
        products.forEach(function(p, idx) {
            var cena = p.cena_allegro || p.cena_sprzedazy || 0;
            var szt = p.ilosc || 1;
            var wartosc = cena * szt;
            var cenaAmz = p.cena_amazon_rpp || 0;
            var corrected = p.cena_allegro_gemini ? true : false;
            h += '<tr style="border-bottom:1px solid var(--border)">';
            h += '<td style="padding:8px;color:var(--text-muted)">' + (idx+1) + '</td>';
            // Foto z Excel image_url, Amazon CDN (ASIN) lub placeholder
            var imgSrc = p.image_url || (p.asin && p.asin.length >= 10 ? 'https://m.media-amazon.com/images/P/' + p.asin + '._SL75_.jpg' : '');
            if (imgSrc) {
                h += '<td style="padding:4px;text-align:center"><img src="' + imgSrc + '" style="width:50px;height:50px;object-fit:contain;border-radius:6px;background:rgba(255,255,255,0.05)" onerror="this.parentElement.innerHTML=\\x27brak\\x27"></td>';
            } else {
                h += '<td style="padding:4px;text-align:center;color:var(--text-muted);font-size:0.7rem">—</td>';
            }
            var asinLink = '';
            if (p.asin) {
                asinLink = '<br><a href="https://www.amazon.de/dp/' + p.asin + '" target="_blank" style="font-size:0.7rem;color:#60a5fa;text-decoration:none"><span class=material-symbols-outlined>link</span> ' + p.asin + '</a>';
            } else if (p.ean) {
                asinLink = '<br><span style="font-size:0.7rem;color:var(--text-muted)">EAN: ' + p.ean + '</span>';
            }
            var nazwaText = (p.nazwa||'—').replace(/"/g, '&quot;');
            h += '<td style="padding:8px;font-weight:500;max-width:300px;cursor:pointer" onclick="copyName(this)" data-copytext="' + nazwaText + '" title="Kliknij aby skopiować"><span>' + (p.nazwa||'—') + '</span>' + asinLink + '</td>';
            h += '<td style="padding:8px;text-align:center;font-weight:600">' + szt + '</td>';
            if (corrected) {
                h += '<td style="padding:8px;text-align:center"><span style="color:var(--green);font-weight:700">' + cena.toFixed(0) + ' zł</span><br><span style="text-decoration:line-through;color:var(--text-muted);font-size:0.7rem">AI: ' + p.cena_allegro_gemini.toFixed(0) + ' zł</span></td>';
            } else {
                h += '<td style="padding:8px;color:var(--green);text-align:center">' + cena.toFixed(0) + ' zł</td>';
            }
            h += '<td style="padding:8px;font-weight:700;color:var(--green);text-align:center">' + wartosc.toFixed(0) + ' zł</td>';
            h += '<td style="padding:8px;color:var(--text-muted);text-align:center">' + (cenaAmz > 0 ? cenaAmz.toFixed(0) + ' zł' : '—') + '</td>';
            // Allegro real column
            if (p.cena_allegro_real) {
                h += '<td style="padding:8px;text-align:center"><span style="color:#60a5fa;font-weight:600">' + p.cena_allegro_real.toFixed(0) + ' zł</span><br><span style="font-size:0.65rem;color:var(--text-muted)">' + (p.allegro_ofert||0) + ' ofert (' + (p.allegro_min||0).toFixed(0) + '-' + (p.allegro_max||0).toFixed(0) + ')</span></td>';
            } else {
                h += '<td style="padding:8px;text-align:center;color:var(--text-muted)">—</td>';
            }
            var dc = demandClass(p.popyt);
            var dcColor = dc === 'demand-high' ? 'var(--green)' : dc === 'demand-low' ? 'var(--red)' : 'var(--orange)';
            h += '<td style="padding:8px;text-align:center"><span style="background:rgba(0,0,0,0.2);padding:3px 10px;border-radius:10px;font-size:0.75rem;font-weight:600;color:' + dcColor + '">' + (p.popyt||'?') + '</span></td>';
            h += '<td style="padding:8px;text-align:center">' + (p.czas_sprzedazy_dni || '?') + ' dni</td>';
            h += '<td style="padding:8px;color:var(--text-muted);font-size:0.78rem;max-width:200px">' + (p.uwagi||'—') + '</td>';
            h += '</tr>';
        });
        h += '</tbody></table>';
        return h;
    }

    function filterProducts() {
        var q = (document.getElementById('product-filter').value || '').toLowerCase().trim();
        if (!q || !window._analysisProducts) return clearFilter();
        var keywords = q.split(/[\\s,;]+/);
        var filtered = window._analysisProducts.filter(function(p) {
            var txt = ((p.nazwa||'') + ' ' + (p.uwagi||'')).toLowerCase();
            return keywords.some(function(k) { return txt.indexOf(k) >= 0; });
        });
        // Podsumowanie filtrowanych
        var totalSzt = 0, totalVal = 0;
        filtered.forEach(function(p) {
            var szt = p.ilosc || 1;
            totalSzt += szt;
            totalVal += (p.cena_allegro || 0) * szt;
        });
        var sumEl = document.getElementById('filter-summary');
        sumEl.style.display = 'block';
        sumEl.innerHTML = '<div style="display:flex;gap:20px;flex-wrap:wrap;align-items:center">'
            + '<span style="font-weight:700;color:var(--green)"><span class=material-symbols-outlined>search</span> "' + q + '"</span>'
            + '<span><b>' + filtered.length + '</b> typów</span>'
            + '<span><b>' + totalSzt + '</b> szt.</span>'
            + '<span>Wartość: <b style="color:var(--green)">' + totalVal.toFixed(0) + ' zł</b></span>'
            + '</div>';
        // Przerenderuj tabelę
        var tableContainer = document.querySelector('#analysis-results .card:last-child');
        if (tableContainer) {
            var oldTable = tableContainer.querySelector('table');
            var oldInfo = document.getElementById('product-table-info');
            if (oldTable) oldTable.outerHTML = '';
            if (oldInfo) oldInfo.outerHTML = '';
            tableContainer.insertAdjacentHTML('beforeend', renderProductTable(filtered));
        }
    }

    function clearFilter() {
        document.getElementById('product-filter').value = '';
        document.getElementById('filter-summary').style.display = 'none';
        if (window._analysisProducts) {
            var tableContainer = document.querySelector('#analysis-results .card:last-child');
            if (tableContainer) {
                var oldTable = tableContainer.querySelector('table');
                var oldInfo = document.getElementById('product-table-info');
                if (oldTable) oldTable.outerHTML = '';
                if (oldInfo) oldInfo.outerHTML = '';
                tableContainer.insertAdjacentHTML('beforeend', renderProductTable(window._analysisProducts));
            }
        }
    }
    '''


@analityka_bp.route('/analityka/analizator-palet/status')
def analizator_palet_status():
    """Sprawdź status analizy palety — polling endpoint."""
    import json as _json
    job_id = request.args.get('job_id', '')
    if not job_id:
        return jsonify({'status': 'error', 'error': 'Brak job_id'})

    job_info = _pallet_analysis_jobs.get(job_id, {})
    if not job_info:
        return jsonify({'status': 'idle'})

    status = job_info.get('status', 'idle') if isinstance(job_info, dict) else str(job_info)

    if status == 'running':
        progress = job_info.get('progress', 'Analizuję...') if isinstance(job_info, dict) else 'Analizuję...'
        return jsonify({'status': 'running', 'progress': progress})

    if status == 'error':
        error = job_info.get('error', 'Nieznany błąd') if isinstance(job_info, dict) else 'Nieznany błąd'
        return jsonify({'status': 'error', 'error': error})

    if status == 'done':
        result = _pallet_analysis_results.get(job_id, {})
        return jsonify({
            'status': 'done',
            'parsed': result.get('parsed'),
            'raw': result.get('raw', ''),
            'citations': result.get('citations', []),
            'paleta': result.get('paleta', {}),
        })

    return jsonify({'status': status})


# ═══════════════════════════════════════════════════
# KOSZTY ALLEGRO — per-product cost breakdown + ROAS
# ═══════════════════════════════════════════════════

_billing_cache = {'data': None, 'per_offer': None, 'source': None, 'ts': 0}

@analityka_bp.route('/analityka/koszty-allegro')
def koszty_allegro():
    from modules.database import get_db, get_config
    from datetime import datetime, timedelta
    import time
    conn = get_db()
    force_refresh = request.args.get('refresh') == '1'

    prowizja_pct = float(get_config('allegro_prowizja_pct', '15')) / 100

    # ── Try Allegro Billing API first (with 15-min cache) ──
    cache_ttl = 900  # 15 minutes
    now_ts = time.time()

    if not force_refresh and _billing_cache['data'] and (now_ts - _billing_cache['ts']) < cache_ttl:
        billing_data = _billing_cache['data']
        billing_per_offer = _billing_cache['per_offer']
        billing_source = _billing_cache['source']
    else:
        billing_data = {'prowizja': 0, 'reklama': 0, 'dostawa': 0, 'inne': 0}
        billing_per_offer = {}
        billing_source = 'manual'

        try:
            from modules.allegro_api import get_all_billing_entries, get_allegro_config
            config = get_allegro_config()
            if config.get('access_token'):
                date_from = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%dT00:00:00Z')
                entries, error = get_all_billing_entries(date_from=date_from, max_pages=20)
                if not error and entries:
                    billing_source = 'api'
                    type_breakdown = {}
                    for e in entries:
                        amount = abs(float(e.get('value', {}).get('amount', 0)))
                        type_id = e.get('type', {}).get('id', '')
                        type_name = (e.get('type', {}).get('name', '') or '').lower()
                        offer_id = e.get('offer', {}).get('id', '')

                        if type_id in ('SUC', 'SUC_BUY_NOW') or 'prowizj' in type_name or 'commission' in type_name:
                            billing_data['prowizja'] += amount
                            cat = 'prowizja'
                        elif 'reklam' in type_name or 'promow' in type_name or 'ads' in type_name or 'wyróżn' in type_name or type_id in ('PRO', 'PROMO', 'ADS'):
                            billing_data['reklama'] += amount
                            cat = 'reklama'
                        elif 'dostaw' in type_name or 'wysyłk' in type_name or 'shipping' in type_name or 'kurier' in type_name:
                            billing_data['dostawa'] += amount
                            cat = 'dostawa'
                        else:
                            billing_data['inne'] += amount
                            cat = 'inne'

                        # Track type breakdown for debug
                        key = f"{type_id}|{type_name}|→{cat}"
                        type_breakdown[key] = type_breakdown.get(key, 0) + amount

                        if offer_id:
                            if offer_id not in billing_per_offer:
                                billing_per_offer[offer_id] = {'prowizja': 0, 'reklama': 0, 'dostawa': 0}
                            if cat in ('prowizja', 'reklama', 'dostawa'):
                                billing_per_offer[offer_id][cat] += amount
                    # Log breakdown
                    for k, v in sorted(type_breakdown.items(), key=lambda x: -x[1]):
                        print(f"[Billing] {k}: {v:.2f} zł")
        except Exception as ex:
            print(f"[Koszty Allegro] Billing API error: {ex}")

        # Update cache
        _billing_cache['data'] = billing_data
        _billing_cache['per_offer'] = billing_per_offer
        _billing_cache['source'] = billing_source
        _billing_cache['ts'] = now_ts

    # ── Fallback: manual koszty table ──
    if billing_source == 'manual':
        koszty_real = conn.execute('''
            SELECT kategoria, SUM(kwota) as total
            FROM koszty
            GROUP BY kategoria
        ''').fetchall()
        koszty_map = {r['kategoria']: r['total'] for r in koszty_real}
        billing_data['prowizja'] = koszty_map.get('allegro', 0)
        billing_data['reklama'] = koszty_map.get('reklama', 0)
        billing_data['dostawa'] = koszty_map.get('wysylka', 0)

    real_allegro_fees = billing_data['prowizja']
    real_reklama = billing_data['reklama']
    real_dostawa = billing_data['dostawa']

    # Per-product: przychod, szt, prowizja, marza
    # Filter sales to same 90-day billing period
    date_90_ago = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')

    # Sort parameters from query string
    sort_field = request.args.get('sort', 'przychod')
    sort_dir = request.args.get('dir', 'desc')
    if sort_field not in ('przychod', 'szt', 'prowizja', 'reklama', 'dostawa', 'koszt', 'marza'):
        sort_field = 'przychod'
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'desc'

    rows = conn.execute('''
        SELECT
            sp.produkt_id as id,
            p.nazwa,
            p.zdjecie_url,
            sp.przychod,
            sp.szt,
            COALESCE(p.cena_allegro, 0) as cena_allegro,
            (SELECT o.allegro_id FROM oferty o WHERE o.produkt_id = sp.produkt_id LIMIT 1) as offer_allegro_id,
            CASE
                WHEN pal.cena_zakupu > 0 AND sc.total_cnt > 0
                THEN pal.cena_zakupu / sc.total_cnt
                ELSE 0
            END as koszt_szt
        FROM (
            SELECT produkt_id, SUM(cena * ilosc) as przychod, SUM(ilosc) as szt
            FROM sprzedaze
            WHERE status NOT IN ('anulowana', 'zwrot') AND produkt_id IS NOT NULL
                AND date(data_sprzedazy) >= ?
            GROUP BY produkt_id
        ) sp
        JOIN produkty p ON p.id = sp.produkt_id
        LEFT JOIN palety pal ON p.paleta_id = pal.id
        LEFT JOIN (
            SELECT p2.paleta_id,
                   SUM(COALESCE(p2.ilosc, 0) + COALESCE(p2.sprzedano_offline, 0)
                       + COALESCE(sp2.cnt, 0)) as total_cnt
            FROM produkty p2
            LEFT JOIN (
                SELECT produkt_id, SUM(ilosc) as cnt
                FROM sprzedaze WHERE status != 'anulowana'
                GROUP BY produkt_id
            ) sp2 ON sp2.produkt_id = p2.id
            GROUP BY p2.paleta_id
        ) sc ON sc.paleta_id = pal.id
        ORDER BY sp.przychod DESC
    ''', [date_90_ago]).fetchall()

    # Build product list
    products = []
    total_przychod = 0
    total_prowizja = 0
    total_reklama = 0
    total_dostawa = 0
    total_koszt = 0
    total_marza = 0

    # First pass: compute total revenue for proportional cost allocation
    all_przychod = sum((r['przychod'] or 0) for r in rows)

    # Use real Allegro fees if available, otherwise estimate
    use_real_fees = real_allegro_fees > 0

    for r in rows:
        przychod = r['przychod'] or 0
        szt = r['szt'] or 0
        koszt_szt = r['koszt_szt'] or 0
        koszt_total = koszt_szt * szt
        revenue_share = (przychod / all_przychod) if all_przychod > 0 else 0
        offer_id = r['offer_allegro_id'] or ''

        # Per-offer billing data from API (exact), or proportional fallback
        offer_billing = billing_per_offer.get(offer_id, {}) if billing_source == 'api' else {}
        if offer_billing:
            prowizja = offer_billing.get('prowizja', 0)
            reklama = offer_billing.get('reklama', 0)
            dostawa = offer_billing.get('dostawa', 0)
        elif use_real_fees:
            prowizja = real_allegro_fees * revenue_share
            reklama = real_reklama * revenue_share
            dostawa = real_dostawa * revenue_share
        else:
            prowizja = przychod * prowizja_pct
            reklama = real_reklama * revenue_share
            dostawa = real_dostawa * revenue_share

        koszt = prowizja + reklama + dostawa
        marza = przychod - koszt_total - prowizja - reklama - dostawa
        marza_pct = (marza / przychod * 100) if przychod > 0 else 0

        total_przychod += przychod
        total_prowizja += prowizja
        total_reklama += reklama
        total_dostawa += dostawa
        total_koszt += koszt
        total_marza += marza

        source = 'API' if offer_billing else ('~' if use_real_fees else 'EST')
        products.append({
            'nazwa': r['nazwa'] or 'Nieznany',
            'przychod': przychod,
            'szt': szt,
            'koszt_szt': koszt_szt,
            'koszt_total': koszt_total,
            'prowizja': prowizja,
            'reklama': reklama,
            'dostawa': dostawa,
            'koszt': koszt,
            'marza': marza,
            'marza_pct': marza_pct,
            'source': source,
        })

    # Server-side sort
    reverse_sort = (sort_dir == 'desc')
    products.sort(key=lambda p: p.get(sort_field, 0), reverse=reverse_sort)

    total_marza_pct = (total_marza / total_przychod * 100) if total_przychod > 0 else 0
    roas = (total_przychod / total_reklama) if total_reklama > 0 else 0

    # ── City heatmap data (1000+ miast z polish_cities.py) ──
    from modules.polish_cities import CITY_COORDS
    city_orders = {}
    for r in conn.execute("SELECT adres FROM sprzedaze WHERE adres IS NOT NULL AND adres != '' AND status NOT IN ('anulowana','zwrot')").fetchall():
        parts = r['adres'].split(',')
        city = (parts[-1].strip() if len(parts) >= 2 else r['adres'].strip()).split('\n')[0].strip()
        if city and len(city) > 1:
            city_lower = city.lower()
            if city_lower in CITY_COORDS:
                lat, lon = CITY_COORDS[city_lower]
                key = f"{city}|{lat}|{lon}"
                city_orders[key] = city_orders.get(key, 0) + 1

    import json
    map_data_json = json.dumps([
        {'city': k.split('|')[0], 'lat': float(k.split('|')[1]), 'lon': float(k.split('|')[2]), 'count': v}
        for k, v in sorted(city_orders.items(), key=lambda x: -x[1])
    ])

    if billing_source == 'api':
        fees_source = 'Allegro Billing API (90 dni)'
        fees_icon = 'cloud_sync'
    elif use_real_fees:
        fees_source = 'realne opłaty z tabeli Koszty'
        fees_icon = 'table_chart'
    else:
        fees_source = f'szacunkowe {prowizja_pct*100:.0f}%'
        fees_icon = 'calculate'
    billing_inne = billing_data.get('inne', 0)

    # Build HTML
    rows_html = ''
    for p in products:
        badge_color = '#beee00' if p['marza'] > 0 else '#ef4444'
        badge_bg = 'rgba(190,238,0,0.08)' if p['marza'] > 0 else 'rgba(239,68,68,0.08)'
        badge_border = 'rgba(190,238,0,0.20)' if p['marza'] > 0 else 'rgba(239,68,68,0.20)'
        badge_text = 'DOBRZE' if p['marza'] > 0 else 'STRATA'
        koszt_szt_str = f"{p['koszt_szt']:,.0f} zł" if p['koszt_szt'] > 0 else '-'
        rows_html += f'''<tr>
            <td style="padding:10px 14px;max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500">{p['nazwa'][:45]}</td>
            <td style="padding:10px 10px;text-align:right;font-family:'Space Grotesk',sans-serif;font-weight:600;color:#8ff5ff">{p['przychod']:,.0f} zł</td>
            <td style="padding:10px 8px;text-align:center">{p['szt']}</td>
            <td style="padding:10px 10px;text-align:right;color:var(--text-muted);font-size:0.78rem">{koszt_szt_str}</td>
            <td style="padding:10px 10px;text-align:right;color:#8ff5ff">{p['prowizja']:,.0f} zł</td>
            <td style="padding:10px 10px;text-align:right;color:#ff6b9b">{p['reklama']:,.0f} zł</td>
            <td style="padding:10px 10px;text-align:right;color:var(--text-muted)">{p['dostawa']:,.0f} zł</td>
            <td style="padding:10px 10px;text-align:right;font-family:'Space Grotesk',sans-serif;font-weight:700;color:#f59e0b">{p['koszt']:,.0f} zł</td>
            <td style="padding:10px 10px;text-align:right;font-family:'Space Grotesk',sans-serif;font-weight:700;color:{badge_color}">{p['marza']:,.0f} zł ({p['marza_pct']:.1f}%)</td>
            <td style="padding:10px 8px;text-align:center"><span style="display:inline-block;padding:3px 10px;border-radius:6px;font-size:0.65rem;font-weight:700;background:{badge_bg};color:{badge_color};border:1px solid {badge_border}">{badge_text}</span></td>
            <td style="padding:10px 4px;text-align:center;font-size:0.65rem;color:{'#beee00' if p['source'] == 'API' else 'var(--text-muted)'}">{p['source']}</td>
        </tr>'''

    html = f'''
    <style>
    .ka-summary{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:20px}}
    .ka-stat{{padding:18px;background:rgba(10,10,22,0.6);border:1px solid rgba(143,245,255,0.06);border-radius:12px}}
    .ka-stat-val{{font-family:'Space Grotesk',sans-serif;font-size:1.8rem;font-weight:800;line-height:1;letter-spacing:-0.02em}}
    .ka-stat-label{{font-size:0.62rem;text-transform:uppercase;letter-spacing:0.1em;color:var(--text-muted);margin-bottom:4px;font-weight:600;font-family:'Space Grotesk',sans-serif}}
    .ka-stat.featured{{grid-column:span 2;border-left:3px solid #8ff5ff;box-shadow:0 0 20px rgba(143,245,255,0.08)}}
    .ka-stat.accent-lime{{border-top:2px solid #beee00}}
    .ka-table{{width:100%;border-collapse:collapse;font-size:0.82rem}}
    .ka-table th{{padding:12px 14px;text-align:left;font-size:0.65rem;text-transform:uppercase;letter-spacing:0.08em;color:#8ff5ff;border-bottom:1px solid rgba(143,245,255,0.12);font-weight:600;font-family:'Space Grotesk',sans-serif}}
    .ka-table td{{border-bottom:1px solid rgba(255,255,255,0.03)}}
    .ka-table tr:hover{{background:rgba(143,245,255,0.02)}}
    .ka-roas{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:24px}}
    .ka-detail-row{{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.04);font-size:0.88rem}}
    .ka-detail-row:last-child{{border-bottom:none;padding-top:12px}}
    @media(max-width:900px){{.ka-summary{{grid-template-columns:1fr 1fr}}.ka-stat.featured{{grid-column:span 2}}.ka-roas{{grid-template-columns:1fr}}}}
    </style>

    <!-- Header -->
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
        <div style="display:flex;align-items:center;gap:10px">
            <span class=material-symbols-outlined style=color:#8ff5ff;font-size:1.4rem>analytics</span>
            <div>
                <h1 style="font-size:1.4rem;font-weight:800;color:#8ff5ff;text-shadow:0 0 15px rgba(143,245,255,0.3);margin:0;font-family:'Space Grotesk',sans-serif;letter-spacing:0.03em">Koszty Allegro</h1>
                <div style="color:var(--text-muted);font-size:0.8rem;margin-top:2px">Analiza kosztów i rentowności per produkt</div>
            </div>
        </div>
        <div style="display:flex;align-items:center;gap:10px">
            <a href="/analityka/koszty-allegro?refresh=1" style="padding:8px 16px;border-radius:10px;background:rgba(143,245,255,0.10);border:1px solid rgba(143,245,255,0.25);color:#8ff5ff;text-decoration:none;font-size:0.75rem;font-weight:700;transition:all 0.2s;display:flex;align-items:center;gap:6px;font-family:'Space Grotesk',sans-serif;box-shadow:0 0 10px rgba(143,245,255,0.15)">[CLOUD_SYNC] Synchronizuj</a>
        </div>
    </div>

    <!-- API Sync Status -->
    <div style="display:flex;align-items:center;justify-content:space-between;background:rgba(10,10,22,0.6);padding:14px 18px;border-radius:12px;border-left:2px solid #8ff5ff;margin-bottom:16px">
        <div>
            <div style="font-size:0.62rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.1em">Status połączenia</div>
            <div style="font-family:'Space Grotesk',sans-serif;font-size:0.88rem;font-weight:700;color:#8ff5ff;margin-top:2px">{fees_source}</div>
        </div>
        <span class=material-symbols-outlined style=color:#8ff5ff;font-size:1.2rem>{fees_icon}</span>
    </div>

    <!-- KPI Summary — Bento Grid -->
    <div class="ka-summary">
        <div class="ka-stat featured">
            <div class="ka-stat-label"><span class=material-symbols-outlined style=font-size:0.8rem;vertical-align:middle;margin-right:2px;opacity:0.5>payments</span> PRZYCHÓD</div>
            <div class="ka-stat-val" style="color:#8ff5ff">{total_przychod:,.0f}<span style="font-size:1rem;margin-left:4px;opacity:0.7">zł</span></div>
        </div>
        <div class="ka-stat">
            <div class="ka-stat-label">PROWIZJA</div>
            <div class="ka-stat-val" style="font-size:1.3rem">{total_prowizja:,.0f}<span style="font-size:0.85rem;margin-left:4px;opacity:0.5">zł</span></div>
        </div>
        <div class="ka-stat">
            <div class="ka-stat-label">KOSZT ŁĄCZNY</div>
            <div class="ka-stat-val" style="font-size:1.3rem;color:#ff6b9b">{total_koszt:,.0f}<span style="font-size:0.85rem;margin-left:4px;opacity:0.5">zł</span></div>
        </div>
        <div class="ka-stat accent-lime">
            <div class="ka-stat-label" style="color:#beee00">MARŻA NETTO</div>
            <div class="ka-stat-val" style="font-size:1.3rem;color:{'#beee00' if total_marza > 0 else '#ef4444'}">{total_marza:,.0f}<span style="font-size:0.85rem;margin-left:4px;opacity:0.5">zł</span></div>
        </div>
        <div class="ka-stat accent-lime">
            <div class="ka-stat-label" style="color:#beee00">MARŻA %</div>
            <div class="ka-stat-val" style="font-size:1.3rem;color:{'#beee00' if total_marza_pct > 30 else '#f59e0b' if total_marza_pct > 0 else '#ef4444'}">{total_marza_pct:.1f}%</div>
        </div>
    </div>

    <!-- ROAS + Cost Details -->
    <div class="ka-roas">
        <div style="padding:24px;background:rgba(10,10,22,0.6);border:1px solid rgba(255,107,155,0.12);border-radius:16px;box-shadow:0 0 30px rgba(255,107,155,0.06);display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center">
            <div style="font-size:0.62rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px;font-family:'Space Grotesk',sans-serif">ZWROT Z REKLAMY (ROAS)</div>
            <div style="font-size:3.5rem;font-weight:900;color:#ff6b9b;font-family:'Space Grotesk',sans-serif;text-shadow:0 0 20px rgba(255,107,155,0.3);line-height:1">{f'{roas:.1f}' if roas > 0 else '∞'}<span style="font-size:1.5rem;font-weight:700;margin-left:4px">x</span></div>
        </div>
        <div style="padding:20px;background:rgba(10,10,22,0.6);border:1px solid rgba(143,245,255,0.06);border-radius:16px">
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:14px">
                <span class=material-symbols-outlined style=color:#8ff5ff;font-size:1rem>list_alt</span>
                <span style="font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:0.88rem;letter-spacing:0.05em">Szczegóły kosztów</span>
            </div>
            <div class="ka-detail-row"><span style="color:var(--text-muted)">Prowizja</span><span style="font-weight:600;font-family:monospace;font-size:0.85rem">{total_prowizja:,.2f} zł</span></div>
            <div class="ka-detail-row"><span style="color:var(--text-muted)">Reklama</span><span style="font-weight:600;font-family:monospace;font-size:0.85rem">{total_reklama:,.2f} zł</span></div>
            <div class="ka-detail-row"><span style="color:var(--text-muted)">Dostawa</span><span style="font-weight:600;font-family:monospace;font-size:0.85rem">{total_dostawa:,.2f} zł</span></div>
            {'<div class="ka-detail-row"><span style="color:var(--text-muted)">Inne opłaty</span><span style="font-weight:600;font-family:monospace;font-size:0.85rem">' + f"{billing_inne:,.2f} zł" + '</span></div>' if billing_inne > 0 else ''}
            <div class="ka-detail-row" style="font-weight:700">
                <span style="color:#8ff5ff;text-transform:uppercase;font-size:0.72rem;letter-spacing:0.1em;font-family:'Space Grotesk',sans-serif">Razem</span>
                <span style="color:#8ff5ff;font-family:'Space Grotesk',sans-serif;font-size:1.1rem;text-shadow:0 0 8px rgba(143,245,255,0.3)">{total_prowizja + total_reklama + total_dostawa + billing_inne:,.2f} zł</span>
            </div>
        </div>
    </div>

    <!-- Products Section Header + Sort -->
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
        <div style="display:flex;align-items:center;gap:8px">
            <div style="width:3px;height:20px;background:#8ff5ff;border-radius:2px"></div>
            <span style="font-family:'Space Grotesk',sans-serif;font-size:1.1rem;font-weight:800;letter-spacing:0.05em">PRODUKTY</span>
            <span style="font-size:0.65rem;color:var(--text-muted);background:rgba(255,255,255,0.05);padding:2px 8px;border-radius:4px">{len(products)} SKUS</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
            <select id="sortField" onchange="window.location.href='/analityka/koszty-allegro?sort='+this.value+'&dir={sort_dir}'" style="padding:8px 12px;background:rgba(10,10,22,0.8);border:1px solid rgba(143,245,255,0.10);border-radius:8px;color:var(--text);font-size:0.8rem;font-family:'Space Grotesk',sans-serif">
                <option value="przychod" {'selected' if sort_field == 'przychod' else ''}>Przychód</option>
                <option value="szt" {'selected' if sort_field == 'szt' else ''}>Sztuki</option>
                <option value="prowizja" {'selected' if sort_field == 'prowizja' else ''}>Prowizja</option>
                <option value="reklama" {'selected' if sort_field == 'reklama' else ''}>Reklama</option>
                <option value="dostawa" {'selected' if sort_field == 'dostawa' else ''}>Dostawa</option>
                <option value="koszt" {'selected' if sort_field == 'koszt' else ''}>Koszt</option>
                <option value="marza" {'selected' if sort_field == 'marza' else ''}>Marża</option>
            </select>
            <a href="/analityka/koszty-allegro?sort={sort_field}&dir={'asc' if sort_dir == 'desc' else 'desc'}" style="padding:8px 12px;background:rgba(143,245,255,0.08);border:1px solid rgba(143,245,255,0.15);border-radius:8px;color:#8ff5ff;font-size:0.8rem;font-weight:600;text-decoration:none;display:flex;align-items:center;gap:4px;font-family:'Space Grotesk',sans-serif"><span class=material-symbols-outlined style=font-size:1rem>{'arrow_upward' if sort_dir == 'asc' else 'arrow_downward'}</span> {'ASC' if sort_dir == 'asc' else 'DESC'}</a>
        </div>
    </div>

    <!-- Products table -->
    <div style="background:rgba(10,10,22,0.6);border:1px solid rgba(143,245,255,0.06);border-radius:14px;padding:0;overflow-x:auto;margin-bottom:20px">
        <table class="ka-table">
            <thead>
                <tr>
                    <th>Produkt</th>
                    <th style="text-align:right">Przychód</th>
                    <th style="text-align:center">Szt</th>
                    <th style="text-align:right">Koszt/szt</th>
                    <th style="text-align:right">Prowizja</th>
                    <th style="text-align:right">Reklama</th>
                    <th style="text-align:right">Dostawa</th>
                    <th style="text-align:right;color:#f59e0b">Koszt</th>
                    <th style="text-align:right">Marża</th>
                    <th style="text-align:center"></th>
                    <th style="text-align:center;font-size:0.65rem;color:var(--text-muted)">Źródło</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </div>

    <div style="text-align:center;padding:16px;font-size:0.72rem;color:var(--text-muted)">
        {len(products)} produktów · Źródło: {fees_source} · {len(billing_per_offer)} ofert z danymi per-oferta
    </div>

    <!-- Map -->
    <div style="display:flex;align-items:center;gap:8px;margin:24px 0 12px">
        <span class=material-symbols-outlined style=color:#8ff5ff;font-size:1.1rem>map</span>
        <span style="font-size:0.72rem;text-transform:uppercase;letter-spacing:0.1em;color:#8ff5ff;font-weight:700;font-family:'Space Grotesk',sans-serif">Skąd kupują klienci</span>
    </div>
    <div style="background:rgba(10,10,22,0.6);border:1px solid rgba(143,245,255,0.06);border-radius:14px;padding:0;overflow:hidden;margin-bottom:20px">
        <div id="polandMap" style="height:500px;background:#0a0a16;border-radius:14px"></div>
    </div>

    <script>
    // ── Leaflet Map ──
    (function() {{
        var css = document.createElement('link');
        css.rel = 'stylesheet';
        css.href = '/static/leaflet.css';
        document.head.appendChild(css);

        var js = document.createElement('script');
        js.src = '/static/leaflet.js';
        js.onload = function() {{
            try {{
                var mapEl = document.getElementById('polandMap');
                if (!mapEl) {{ console.error('Map element not found'); return; }}

                var map = L.map(mapEl).setView([52.0, 19.5], 6);
                var b = String.fromCharCode(123), e = String.fromCharCode(125);
                L.tileLayer('https://cartodb-basemaps-a.global.ssl.fastly.net/dark_all/' + b+'z'+e + '/' + b+'x'+e + '/' + b+'y'+e + '.png', {{ maxZoom: 13, minZoom: 5, attribution: '' }}).addTo(map);

                var cities = {map_data_json};
                var maxCount = cities.length > 0 ? cities[0].count : 1;
                cities.forEach(function(c) {{
                    var r = Math.max(8, Math.min(40, (c.count / maxCount) * 40));
                    var op = Math.max(0.4, Math.min(0.9, c.count / maxCount));
                    L.circleMarker([c.lat, c.lon], {{
                        radius: r, fillColor: '#8ff5ff', color: '#8ff5ff',
                        weight: 1, opacity: 0.7, fillOpacity: op * 0.6
                    }}).addTo(map).bindPopup(
                        '<div style="text-align:center"><b style="color:#8ff5ff">' + c.city + '</b><br><span style="font-size:18px;font-weight:800">' + c.count + '</span><br><small>zamówień</small></div>'
                    );
                }});
                setTimeout(function() {{ map.invalidateSize(); }}, 300);
            }} catch(e) {{ console.error('Map init error:', e); }}
        }};
        document.head.appendChild(js);
    }})();
    </script>
    '''

    return render(html, page_title='Koszty Allegro')


@analityka_bp.route('/analityka/zakupy-dostawcy')
def analityka_zakupy_dostawcy():
    """Analityka zakupów od dostawców - wydatki, ROI, leżaki, macierz logistyczna."""
    from modules.database import get_db
    from flask import render_template
    from datetime import datetime

    conn = get_db()
    now = datetime.now()

    # ── KPI globalne ──
    kpi = conn.execute('''
        SELECT
            COUNT(*) as ilosc_palet,
            COALESCE(SUM(cena_zakupu), 0) as total_wydane,
            COALESCE(AVG(cena_zakupu), 0) as avg_palety,
            COALESCE(SUM(ilosc_sztuk), 0) as total_sztuk
        FROM palety
    ''').fetchone()

    # ── Przychód łączny z wszystkich palet (z tabeli sprzedaze) ──
    total_przychod_row = conn.execute('''
        SELECT COALESCE(SUM(s.cena * s.ilosc), 0) as przychod
        FROM sprzedaze s
        LEFT JOIN produkty pr ON s.produkt_id = pr.id
        LEFT JOIN oferty o ON s.oferta_id = o.id
        LEFT JOIN produkty pr2 ON o.produkt_id = pr2.id
        WHERE COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot')
          AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
    ''').fetchone()
    total_przychod = total_przychod_row['przychod'] or 0
    total_wydane = kpi['total_wydane'] or 0
    total_zysk = total_przychod - total_wydane
    total_roi = (total_zysk / total_wydane * 100) if total_wydane > 0 else 0

    # ── Dostawcy ──
    dostawcy_raw = conn.execute('''
        SELECT
            COALESCE(NULLIF(TRIM(dostawca),''), 'Nieznany') as dostawca,
            COUNT(*) as palety,
            COALESCE(SUM(cena_zakupu), 0) as wydane,
            COALESCE(AVG(cena_zakupu), 0) as avg_cena,
            MIN(data_zakupu) as first_zakup,
            MAX(data_zakupu) as last_zakup,
            COALESCE(SUM(ilosc_sztuk), 0) as sztuk_total
        FROM palety
        GROUP BY COALESCE(NULLIF(TRIM(dostawca),''), 'Nieznany')
        ORDER BY wydane DESC
    ''').fetchall()

    # Dla każdego dostawcy oblicz przychód i ROI
    dostawcy = []
    for d in dostawcy_raw:
        przychod_d = conn.execute('''
            SELECT COALESCE(SUM(s.cena * s.ilosc), 0) as przychod
            FROM sprzedaze s
            LEFT JOIN produkty pr ON s.produkt_id = pr.id
            LEFT JOIN oferty o ON s.oferta_id = o.id
            LEFT JOIN produkty pr2 ON o.produkt_id = pr2.id
            JOIN palety p ON COALESCE(pr.paleta_id, pr2.paleta_id) = p.id
            WHERE COALESCE(NULLIF(TRIM(p.dostawca),''), 'Nieznany') = ?
              AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot')
              AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
        ''', (d['dostawca'],)).fetchone()['przychod'] or 0

        wydane = d['wydane'] or 0
        zysk_d = przychod_d - wydane
        roi_d = (zysk_d / wydane * 100) if wydane > 0 else 0
        marza_d = (zysk_d / przychod_d * 100) if przychod_d > 0 else 0

        dostawcy.append({
            'dostawca': d['dostawca'],
            'palety': d['palety'],
            'wydane': wydane,
            'avg_cena': d['avg_cena'],
            'przychod': przychod_d,
            'zysk': zysk_d,
            'roi': roi_d,
            'marza': marza_d,
            'first_zakup': d['first_zakup'],
            'last_zakup': d['last_zakup'],
            'sztuk': d['sztuk_total'],
        })

    # ── Miesięczne zakupy (ostatnie 12 mies.) ──
    miesieczne = conn.execute('''
        SELECT
            strftime('%Y-%m', data_zakupu) as miesiac,
            COUNT(*) as palety,
            COALESCE(SUM(cena_zakupu), 0) as wydane
        FROM palety
        WHERE data_zakupu >= date('now', '-12 months')
        GROUP BY miesiac
        ORDER BY miesiac
    ''').fetchall()
    mies_labels = [r['miesiac'] for r in miesieczne]
    mies_wydane = [round(r['wydane'], 2) for r in miesieczne]
    mies_palety = [r['palety'] for r in miesieczne]

    # ── Leżaki (produkty w magazynie > 30 dni) ──
    lezaki = conn.execute('''
        SELECT
            pr.id,
            pr.nazwa,
            pr.kategoria,
            pr.cena_allegro,
            pr.cena_netto,
            pr.ilosc,
            pr.lokalizacja,
            pr.stan,
            p.dostawca,
            p.nazwa as paleta_nazwa,
            pr.data_dodania,
            CAST(julianday('now') - julianday(pr.data_dodania) AS INTEGER) as dni_w_magazynie
        FROM produkty pr
        LEFT JOIN palety p ON pr.paleta_id = p.id
        WHERE pr.status IN ('magazyn', 'dostepny')
          AND pr.ilosc > 0
          AND pr.data_dodania IS NOT NULL
          AND CAST(julianday('now') - julianday(pr.data_dodania) AS INTEGER) > 30
        ORDER BY dni_w_magazynie DESC
        LIMIT 50
    ''').fetchall()

    lezaki_list = [dict(r) for r in lezaki]

    # ── Macierz logistyczna per kategoria ──
    macierz = conn.execute('''
        SELECT
            COALESCE(NULLIF(pr.kategoria,''), 'inne') as kategoria,
            COUNT(DISTINCT pr.id) as produkty,
            COALESCE(SUM(pr.ilosc), 0) as sztuk_magazyn,
            COALESCE(SUM(CASE WHEN pr.status='sprzedany' THEN 1 ELSE 0 END), 0) as sprzedane,
            COALESCE(AVG(pr.cena_allegro), 0) as avg_cena
        FROM produkty pr
        GROUP BY COALESCE(NULLIF(pr.kategoria,''), 'inne')
        ORDER BY sprzedane DESC
        LIMIT 15
    ''').fetchall()

    return render_template(
        'analityka_zakupy.html',
        kpi_palety=kpi['ilosc_palet'] or 0,
        kpi_wydane=total_wydane,
        kpi_przychod=total_przychod,
        kpi_zysk=total_zysk,
        kpi_roi=total_roi,
        kpi_sztuk=kpi['total_sztuk'] or 0,
        dostawcy=dostawcy,
        mies_labels=mies_labels,
        mies_wydane=mies_wydane,
        mies_palety=mies_palety,
        lezaki=lezaki_list,
        macierz=[dict(r) for r in macierz],
        version=current_app.config.get('VERSION', ''),
        brand_name=current_app.config.get('BRAND_NAME', 'Akces Hub'),
        current_user=session.get('username'),
    )
