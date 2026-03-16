"""
Modul analityki -- routes dla /analityka/*, /statystyki
"""
from flask import Blueprint, request, redirect, session, flash, jsonify, Response, current_app
from datetime import datetime
import os

analityka_bp = Blueprint('analityka', __name__)

@analityka_bp.route('/statystyki')
def statystyki():
    from modules.database import get_full_stats, get_palety_list, get_db
    from modules.shared import CSS
    import json

    stats = get_full_stats()

    # Pobierz dane miesięczne do wykresu (przychód bez zwrotów)
    current_year = datetime.now().year
    conn = get_db()
    miesieczne = conn.execute('''
        SELECT strftime('%m', REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) as miesiac,
               COALESCE(SUM(CASE WHEN status != 'zwrot' THEN cena * ilosc ELSE 0 END), 0) as suma,
               COUNT(*) as cnt
        FROM sprzedaze
        WHERE strftime('%Y', REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) = ?
          AND data_sprzedazy IS NOT NULL AND data_sprzedazy != ''
          AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
        GROUP BY miesiac
        HAVING miesiac IS NOT NULL
        ORDER BY miesiac
    ''', (str(current_year),)).fetchall()

    nazwy_miesiecy = ['Sty', 'Lut', 'Mar', 'Kwi', 'Maj', 'Cze', 'Lip', 'Sie', 'Wrz', 'Paz', 'Lis', 'Gru']
    dane_miesieczne = [0] * 12
    dane_zamowienia = [0] * 12
    for m in miesieczne:
        if m['miesiac'] is None:
            continue
        idx = int(m['miesiac']) - 1
        dane_miesieczne[idx] = float(m['suma'] or 0)
        dane_zamowienia[idx] = int(m['cnt'] or 0)

    chart_labels = json.dumps(nazwy_miesiecy)
    chart_data = json.dumps(dane_miesieczne)
    chart_orders = json.dumps(dane_zamowienia)

    # TOP produkty i dostawcy
    top_produkty = stats.get('top_produkty', [])
    top_dostawcy = stats.get('top_dostawcy', [])

    # Aktualny miesiąc
    _nazwy_mies = {1:'Styczeń',2:'Luty',3:'Marzec',4:'Kwiecień',5:'Maj',6:'Czerwiec',7:'Lipiec',8:'Sierpień',9:'Wrzesień',10:'Październik',11:'Listopad',12:'Grudzień'}
    miesiac = f"{_nazwy_mies[datetime.now().month]} {datetime.now().year}"

    # TOP produkty HTML
    top_prod_html = ''
    for i, p in enumerate(top_produkty[:5]):
        border = 'border-bottom:1px solid #1e1e2e;' if i < min(len(top_produkty), 5) - 1 else ''
        img = p.get('zdjecie_url') or 'https://via.placeholder.com/40'
        nazwa = p['nazwa'][:40] + ('...' if len(p['nazwa']) > 40 else '')
        top_prod_html += f'''<div style="display:flex;align-items:center;gap:10px;padding:8px 0;{border}">
            <div style="font-weight:700;color:#f59e0b;width:20px">{i+1}.</div>
            <img src="{img}" style="width:40px;height:40px;border-radius:8px;object-fit:cover">
            <div style="flex:1;min-width:0">
                <div style="font-size:0.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{nazwa}</div>
                <div style="font-size:0.75rem;color:#64748b">{p['sprzedazy_cnt']} szt</div>
            </div>
            <div style="font-weight:600;color:#22c55e">{p['sprzedazy_suma']:.0f} zl</div>
        </div>'''

    # TOP dostawcy HTML
    top_dost_html = ''
    for i, d in enumerate(top_dostawcy[:5]):
        border = 'border-bottom:1px solid #1e1e2e;' if i < min(len(top_dostawcy), 5) - 1 else ''
        roi_color = '#22c55e' if d['roi'] > 50 else ('#eab308' if d['roi'] > 20 else '#ef4444')
        top_dost_html += f'''<div style="display:flex;align-items:center;gap:10px;padding:8px 0;{border}">
            <div style="font-weight:700;color:#f59e0b;width:20px">{i+1}</div>
            <div style="flex:1">
                <div style="font-weight:600">{d['dostawca']}</div>
                <div style="font-size:0.75rem;color:#64748b">{d['sprzedazy_cnt']} szt | {d['przychod']:.0f} zl przychod</div>
            </div>
            <div style="text-align:right">
                <div style="font-weight:700;color:{roi_color}">{d['roi']:.0f}%</div>
                <div style="font-size:0.7rem;color:#64748b">koszt: {d['koszt']:.0f} zl</div>
            </div>
        </div>'''

    pryw_info = f' (W TYM {int(stats.get("sprzedaz_lacznie_pryw_suma",0))} ZL PRYWATNE)' if stats.get('sprzedaz_lacznie_pryw_suma',0) > 0 else ''

    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>📊 STATYSTYKI</h1>
            <small>Pelny przeglad biznesu</small>
        </div>

        <!-- TABS -->
        <div style="display:flex;gap:4px;margin-bottom:15px;overflow-x:auto;-webkit-overflow-scrolling:touch">
            <button class="stat-tab active" onclick="showTab('dzis')" id="tab-dzis" style="flex:1;padding:10px 6px;border:none;border-radius:10px;font-weight:600;font-size:0.8rem;cursor:pointer;background:#22c55e;color:#fff;white-space:nowrap">DZIS</button>
            <button class="stat-tab" onclick="showTab('miesiac')" id="tab-miesiac" style="flex:1;padding:10px 6px;border:none;border-radius:10px;font-weight:600;font-size:0.8rem;cursor:pointer;background:#1e1e2e;color:#64748b;white-space:nowrap">MIESIAC</button>
            <button class="stat-tab" onclick="showTab('magazyn')" id="tab-magazyn" style="flex:1;padding:10px 6px;border:none;border-radius:10px;font-weight:600;font-size:0.8rem;cursor:pointer;background:#1e1e2e;color:#64748b;white-space:nowrap">MAGAZYN</button>
            <button class="stat-tab" onclick="showTab('alltime')" id="tab-alltime" style="flex:1;padding:10px 6px;border:none;border-radius:10px;font-weight:600;font-size:0.8rem;cursor:pointer;background:#1e1e2e;color:#64748b;white-space:nowrap">LACZNIE</button>
            <button class="stat-tab" onclick="showTab('top')" id="tab-top" style="flex:1;padding:10px 6px;border:none;border-radius:10px;font-weight:600;font-size:0.8rem;cursor:pointer;background:#1e1e2e;color:#64748b;white-space:nowrap">TOP</button>
        </div>


        <!-- TAB: DZIŚ -->
        <div id="panel-dzis" class="stat-panel">
            <div style="background:linear-gradient(135deg,rgba(34,197,94,0.15),rgba(16,185,129,0.1));border:1px solid rgba(34,197,94,0.3);border-radius:16px;padding:18px;margin-bottom:15px">
                <div style="color:#22c55e;font-weight:600;font-size:1.1rem;margin-bottom:12px">📅 DZIS ({datetime.now().strftime('%d.%m.%Y')})</div>
                <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px">
                    <div style="text-align:center">
                        <div style="font-size:2rem;font-weight:700;color:#22c55e">{stats['sprzedaz_dzis_cnt']}</div>
                        <div style="font-size:0.75rem;color:#64748b">ZAMOWIEN</div>
                    </div>
                    <div style="text-align:center">
                        <div style="font-size:2rem;font-weight:700;color:#22c55e">{stats['sprzedaz_dzis_suma']:.0f} zl</div>
                        <div style="font-size:0.75rem;color:#64748b">PRZYCHOD</div>
                    </div>
                    <div style="text-align:center">
                        <div style="font-size:2rem;font-weight:700;color:#eab308">{stats.get('do_wyslania', 0)}</div>
                        <div style="font-size:0.75rem;color:#64748b">DO WYSYLKI</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- TAB: MIESIĄC -->
        <div id="panel-miesiac" class="stat-panel" style="display:none">
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:18px;margin-bottom:15px">
                <div style="color:#3b82f6;font-weight:600;font-size:1.1rem;margin-bottom:12px">🗓️ {miesiac.upper()}</div>
                <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:12px">
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#3b82f6">{stats['palety_miesiac']}</div>
                        <div style="font-size:0.7rem;color:#64748b">PALET</div>
                    </div>
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#ef4444">{stats['palety_miesiac_koszt']:.0f} zl</div>
                        <div style="font-size:0.7rem;color:#64748b">WYDANE</div>
                    </div>
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#22c55e">{stats['sprzedaz_miesiac_cnt']}</div>
                        <div style="font-size:0.7rem;color:#64748b">SPRZEDAZY</div>
                    </div>
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#22c55e">{stats['sprzedaz_miesiac_suma']:.0f} zl</div>
                        <div style="font-size:0.7rem;color:#64748b">PRZYCHOD</div>
                    </div>
                </div>
                <div style="margin-top:12px;padding:12px;background:rgba(34,197,94,0.1);border-radius:10px;text-align:center">
                    <div style="font-size:0.8rem;color:#64748b">SZACOWANY ZYSK</div>
                    <div style="font-size:1.8rem;font-weight:700;color:#22c55e">{stats['zysk_miesiac']:.0f} zl</div>
                </div>
            </div>
        </div>

        <!-- TAB: MAGAZYN -->
        <div id="panel-magazyn" class="stat-panel" style="display:none">
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:18px;margin-bottom:15px">
                <div style="color:#8b5cf6;font-weight:600;font-size:1.1rem;margin-bottom:12px">🏪 MAGAZYN</div>
                <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">
                    <div style="text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#8b5cf6">{stats['magazyn_produkty']}</div>
                        <div style="font-size:0.65rem;color:#64748b">PRODUKTOW</div>
                    </div>
                    <div style="text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#8b5cf6">{stats['magazyn_sztuki']}</div>
                        <div style="font-size:0.65rem;color:#64748b">SZTUK</div>
                    </div>
                    <div style="text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#8b5cf6">{stats['magazyn_wartosc']:.0f} zl</div>
                        <div style="font-size:0.65rem;color:#64748b">WARTOSC</div>
                    </div>
                </div>
                <div style="margin-top:12px;display:grid;grid-template-columns:1fr 1fr;gap:10px">
                    <div style="background:#1e1e2e;border-radius:8px;padding:10px;text-align:center">
                        <div style="font-size:1.2rem;font-weight:600;color:#3b82f6">{stats['wystawione']}</div>
                        <div style="font-size:0.65rem;color:#64748b">WYSTAWIONE</div>
                    </div>
                    <div style="background:#1e1e2e;border-radius:8px;padding:10px;text-align:center">
                        <a href="/magazyn/lezaki" style="text-decoration:none">
                            <div style="font-size:1.2rem;font-weight:600;color:#eab308">{stats['stojace_30dni']}</div>
                            <div style="font-size:0.65rem;color:#64748b">STOI &gt;30 DNI</div>
                        </a>
                    </div>
                </div>
            </div>
        </div>

        <!-- TAB: ALL-TIME -->
        <div id="panel-alltime" class="stat-panel" style="display:none">
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:18px;margin-bottom:15px">
                <div style="color:#f59e0b;font-weight:600;font-size:1.1rem;margin-bottom:12px">📈 LACZNIE (ALL-TIME)</div>
                <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:12px">
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#f59e0b">{stats['palety_lacznie']}</div>
                        <div style="font-size:0.7rem;color:#64748b">PALET</div>
                    </div>
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#f59e0b">{stats['palety_lacznie_koszt']:.0f} zl</div>
                        <div style="font-size:0.7rem;color:#64748b">ZAINWESTOWANE</div>
                    </div>
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#22c55e">{stats['sprzedaz_lacznie_cnt']}</div>
                        <div style="font-size:0.7rem;color:#64748b">SPRZEDANYCH</div>
                    </div>
                    <div style="background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                        <div style="font-size:1.5rem;font-weight:700;color:#22c55e">{stats['sprzedaz_lacznie_suma']:.0f} zl</div>
                        <div style="font-size:0.7rem;color:#64748b">PRZYCHOD{pryw_info}</div>
                    </div>
                </div>
                <div style="margin-top:12px;background:#1e1e2e;border-radius:10px;padding:12px;text-align:center">
                    <div style="font-size:0.7rem;color:#64748b">SREDNIA WARTOSC ZAMOWIENIA</div>
                    <div style="font-size:1.3rem;font-weight:700;color:#f59e0b">{stats['srednia_zamowienie']:.2f} zl</div>
                </div>
            </div>
        </div>

        <!-- TAB: TOP -->
        <div id="panel-top" class="stat-panel" style="display:none">
            {'<div style="color:#f59e0b;font-weight:600;font-size:1.1rem;margin-bottom:10px">🏆 TOP PRODUKTY</div><div style="background:#12121a;border-radius:12px;padding:12px;margin-bottom:15px">' + top_prod_html + '</div>' if top_prod_html else ''}
            {'<div style="color:#f59e0b;font-weight:600;font-size:1.1rem;margin-bottom:10px">📦 TOP DOSTAWCY (ROI)</div><div style="background:#12121a;border-radius:12px;padding:12px;margin-bottom:15px">' + top_dost_html + '</div>' if top_dost_html else ''}
        </div>

        <!-- WYKRES - zawsze widoczny -->
        <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:18px;margin-bottom:15px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                <div style="color:#8b5cf6;font-weight:600;font-size:1.1rem">📊 WYKRES ({current_year})</div>
                <div style="display:flex;gap:6px">
                    <button onclick="toggleChart('przychod')" id="btn-przychod" style="padding:4px 10px;border:none;border-radius:6px;font-size:0.7rem;cursor:pointer;background:#8b5cf6;color:#fff">Przychod</button>
                    <button onclick="toggleChart('zamowienia')" id="btn-zamowienia" style="padding:4px 10px;border:none;border-radius:6px;font-size:0.7rem;cursor:pointer;background:#1e1e2e;color:#64748b">Zamowienia</button>
                </div>
            </div>
            <canvas id="chartMiesiace" height="200"></canvas>
        </div>

        <!-- Quick links -->
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:10px">
            <a href="/palety" style="display:block;padding:14px;background:#3b82f6;border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600">📦 Palety</a>
            <a href="/sprzedaze" style="display:block;padding:14px;background:#22c55e;border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600">💰 Sprzedaze</a>
            <a href="/analityka" style="display:block;padding:14px;background:#8b5cf6;border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600">📈 Analityka</a>
        </div>

        <a href="/" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:15px">← Powrot</a>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>
    const chartLabels = {chart_labels};
    const chartPrzychod = {chart_data};
    const chartZamowienia = {chart_orders};
    let currentChart = 'przychod';

    const ctx = document.getElementById('chartMiesiace');
    let chart = new Chart(ctx, {{
        type: 'bar',
        data: {{
            labels: chartLabels,
            datasets: [{{
                label: 'Przychod (zl)',
                data: chartPrzychod,
                backgroundColor: 'rgba(139, 92, 246, 0.8)',
                borderColor: 'rgba(139, 92, 246, 1)',
                borderWidth: 1,
                borderRadius: 5
            }}]
        }},
        options: {{
            responsive: true,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                y: {{ beginAtZero: true, grid: {{ color: 'rgba(255,255,255,0.1)' }}, ticks: {{ color: '#64748b' }} }},
                x: {{ grid: {{ display: false }}, ticks: {{ color: '#64748b' }} }}
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

    // Zmień kursor na pointer nad słupkami
    ctx.style.cursor = 'pointer';

    function toggleChart(type) {{
        currentChart = type;
        document.getElementById('btn-przychod').style.background = type==='przychod' ? '#8b5cf6' : '#1e1e2e';
        document.getElementById('btn-przychod').style.color = type==='przychod' ? '#fff' : '#64748b';
        document.getElementById('btn-zamowienia').style.background = type==='zamowienia' ? '#22c55e' : '#1e1e2e';
        document.getElementById('btn-zamowienia').style.color = type==='zamowienia' ? '#fff' : '#64748b';

        chart.data.datasets[0].data = type==='przychod' ? chartPrzychod : chartZamowienia;
        chart.data.datasets[0].label = type==='przychod' ? 'Przychod (zl)' : 'Zamowienia';
        chart.data.datasets[0].backgroundColor = type==='przychod' ? 'rgba(139,92,246,0.8)' : 'rgba(34,197,94,0.8)';
        chart.data.datasets[0].borderColor = type==='przychod' ? 'rgba(139,92,246,1)' : 'rgba(34,197,94,1)';
        chart.update();
    }}

    function showTab(tab) {{
        document.querySelectorAll('.stat-panel').forEach(p => p.style.display = 'none');
        document.querySelectorAll('.stat-tab').forEach(t => {{ t.style.background = '#1e1e2e'; t.style.color = '#64748b'; }});
        document.getElementById('panel-' + tab).style.display = 'block';
        const btn = document.getElementById('tab-' + tab);
        const colors = {{ dzis: '#22c55e', miesiac: '#3b82f6', magazyn: '#8b5cf6', alltime: '#f59e0b', top: '#ef4444' }};
        btn.style.background = colors[tab] || '#3b82f6';
        btn.style.color = '#fff';
    }}
    </script>
    '''
    return html



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

    # Warning HTML - musi być poza f-stringiem
    warning_html = ''
    if produkty_bez_kat > 0:
        warning_html = f'<div class="warning-box"><span>&#9888; <strong>{produkty_bez_kat}</strong> produktow bez kategorii</span><a href="/analityka/kategorie" class="action-btn">Przypisz kategorie</a></div>'

    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>📊 Analityka - {get_config_cached("brand_name", "AKCES HUB")}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #0a0a0f; 
                color: #e2e8f0;
                min-height: 100vh;
                padding: 20px;
            }}
            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 30px;
                padding-bottom: 20px;
                border-bottom: 1px solid #1e1e2e;
            }}
            .header h1 {{ color: #fff; font-size: 1.8rem; }}
            .header-btns {{ display: flex; gap: 10px; }}
            .back-btn, .action-btn {{
                padding: 10px 20px;
                color: #fff;
                text-decoration: none;
                border-radius: 8px;
                font-weight: 600;
            }}
            .back-btn {{ background: #3b82f6; }}
            .action-btn {{ background: #8b5cf6; }}
            .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(500px, 1fr)); gap: 20px; }}
            .card {{
                background: #12121a;
                border: 1px solid #1e1e2e;
                border-radius: 16px;
                padding: 20px;
            }}
            .card h2 {{
                color: #fff;
                font-size: 1.2rem;
                margin-bottom: 20px;
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            .chart-container {{ position: relative; height: 300px; }}
            .stats-table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 0.85rem;
            }}
            .stats-table th, .stats-table td {{
                padding: 10px;
                text-align: left;
                border-bottom: 1px solid #1e1e2e;
            }}
            .stats-table th {{ color: #64748b; font-weight: 500; }}
            .stats-table tr:hover {{ background: rgba(59, 130, 246, 0.1); }}
            .positive {{ color: #22c55e; }}
            .negative {{ color: #ef4444; }}
            .miasto-bar {{
                height: 8px;
                background: linear-gradient(90deg, #3b82f6, #8b5cf6);
                border-radius: 4px;
                margin-top: 4px;
            }}
            .summary-cards {{
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 15px;
                margin-bottom: 20px;
            }}
            .summary-card {{
                background: linear-gradient(135deg, #1e1e2e, #12121a);
                border: 1px solid #2a2a3a;
                border-radius: 12px;
                padding: 15px;
                text-align: center;
            }}
            .summary-card .value {{ font-size: 1.5rem; font-weight: 700; color: #fff; }}
            .summary-card .label {{ font-size: 0.75rem; color: #64748b; margin-top: 5px; }}
            .warning-box {{
                background: rgba(245, 158, 11, 0.1);
                border: 1px solid rgba(245, 158, 11, 0.3);
                border-radius: 12px;
                padding: 15px;
                margin-bottom: 20px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .warning-box span {{ color: #f59e0b; }}
            @media (max-width: 768px) {{
                .grid {{ grid-template-columns: 1fr; }}
                .summary-cards {{ grid-template-columns: repeat(2, 1fr); }}
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>&#x1F4CA; Analityka sprzedazy</h1>
            <div class="header-btns">
                <button onclick="uzupelnijAdresy()" class="action-btn" style="background:#22c55e;border:none;cursor:pointer">&#x1F4CD; Uzupelnij adresy</button>
                <button onclick="autoKategoryzujWszystkie()" class="action-btn" style="background:#f59e0b;border:none;cursor:pointer">&#x1F916; Auto-kategorie</button>
                <a href="/analityka/palety" class="action-btn" style="background:#3b82f6;text-decoration:none">📦 Bilans palet</a>
                <a href="/analityka/kategorie" class="action-btn">&#x1F3F7; Edytuj kategorie</a>
                <a href="/analityka/czas-sprzedazy" class="action-btn" style="background:#22c55e;text-decoration:none">⏱️ Czas sprzedaży</a>
                <a href="/magazyn/raport-sprzedazy" class="action-btn" style="background:#059669;text-decoration:none">📊 Eksport Excel</a>
                <a href="/" class="back-btn">&larr; Powrot</a>
            </div>
        </div>
        
        ''' + warning_html + f'''
        
        <div class="summary-cards">
            <div class="summary-card">
                <div class="value">{len(miasta_stats)}</div>
                <div class="label">&#x1F3D9; MIAST</div>
            </div>
            <div class="summary-card">
                <div class="value">{sum(m[1]['zamowienia'] for m in miasta_stats.items())}</div>
                <div class="label">&#x1F4E6; ZAMOWIEN</div>
            </div>
            <div class="summary-card">
                <div class="value">{len(kategorie_stats)}</div>
                <div class="label">&#x1F4C1; KATEGORII</div>
            </div>
            <div class="summary-card">
                <div class="value">{laczny_zysk:.0f} zl</div>
                <div class="label">&#x1F4B0; LACZNY ZYSK</div>
                <div style="font-size:0.65rem;color:#94a3b8;margin-top:4px">{allegro_zysk:.0f} Allegro{f' + {prywatne_suma:.0f} prywatne' if prywatne_suma > 0 else ''}</div>
            </div>
        </div>
        
        <div class="grid">
            <!-- MAPA KUPUJĄCYCH -->
            <div class="card">
                <h2>🗺️ Skąd kupują klienci (TOP 20)</h2>
                <div class="chart-container">
                    <canvas id="miastaChart"></canvas>
                </div>
            </div>
            
            <!-- RENTOWNOŚĆ KATEGORII -->
            <div class="card">
                <h2>💰 Rentowność kategorii (TOP 10)</h2>
                <div class="chart-container">
                    <canvas id="kategorieChart"></canvas>
                </div>
            </div>
            
            <!-- SPRZEDAŻ W CZASIE -->
            <div class="card">
                <h2>&#x1F4C8; Przychod (ostatnie 30 dni)</h2>
                <div class="chart-container">
                    <canvas id="czasChart"></canvas>
                </div>
            </div>
            
            <!-- TOP/FLOP PRODUKTY -->
            <div class="card" style="grid-column: span 2;">
                <h2>🏆 TOP 10 Bestsellerów vs 📉 FLOP (najdłużej w magazynie)</h2>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 30px;">
                    <!-- TOP 10 -->
                    <div>
                        <h3 style="color: #22c55e; margin-bottom: 15px;">🥇 Bestsellery (wg przychodu)</h3>
                        <table class="stats-table">
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>Produkt</th>
                                    <th>Szt.</th>
                                    <th>Przychód</th>
                                    <th>Zakup</th>
                                    <th>Prowizja</th>
                                    <th>Zysk</th>
                                </tr>
                            </thead>
                            <tbody>
                                {''.join(f"""
                                <tr>
                                    <td style="color: {'#ffd700' if i==0 else '#c0c0c0' if i==1 else '#cd7f32' if i==2 else '#888'};">
                                        {'🥇' if i==0 else '🥈' if i==1 else '🥉' if i==2 else str(i+1)}
                                    </td>
                                    <td title="{p['nazwa']}">{p['nazwa'][:30]}{'...' if len(p['nazwa'])>30 else ''}</td>
                                    <td>{p['ilosc']}</td>
                                    <td style="color: #22c55e;">{p['przychod']:.0f} zł</td>
                                    <td style="color: {'#ef4444' if p['has_koszt'] else '#555'};">{p['koszt_total']:.0f}{' zł' if p['has_koszt'] else ' ?'}</td>
                                    <td style="color: #f59e0b;">{p['prowizja']:.0f} zł</td>
                                    <td style="color: {'#22c55e' if p['zysk']>0 else '#ef4444'}; font-weight:700;">{p['zysk']:.0f} zł</td>
                                </tr>
                                """ for i, p in enumerate(top_produkty))}
                            </tbody>
                        </table>
                    </div>
                    
                    <!-- FLOP -->
                    <div>
                        <h3 style="color: #ef4444; margin-bottom: 15px;">📉 Najdłużej w magazynie</h3>
                        <table class="stats-table">
                            <thead>
                                <tr>
                                    <th>Produkt</th>
                                    <th>Dni</th>
                                    <th>Zakup</th>
                                    <th>Cena</th>
                                    <th>Kategoria</th>
                                </tr>
                            </thead>
                            <tbody>
                                {''.join(f"""
                                <tr>
                                    <td title="{p['nazwa']}">{p['nazwa'][:30]}{'...' if len(p['nazwa'])>30 else ''}</td>
                                    <td style="color: {'#ef4444' if p['dni']>60 else '#f59e0b' if p['dni']>30 else '#888'};">
                                        {p['dni']} dni
                                    </td>
                                    <td>{p['cena_zakupu']:.0f} zł</td>
                                    <td>{p['cena_sprzedazy']:.0f} zł</td>
                                    <td style="font-size: 0.85em;">{p['kategoria'][:15]}</td>
                                </tr>
                                """ for p in flop_lista) if flop_lista else '<tr><td colspan="5" style="text-align:center;color:#888;">Brak danych</td></tr>'}
                            </tbody>
                        </table>
                        <p style="font-size: 0.8em; color: #666; margin-top: 10px;">
                            💡 Produkty > 60 dni warto przecenić lub wystawić na OLX/Vinted
                        </p>
                    </div>
                </div>
            </div>
            
            <!-- TABELA MIAST -->
            <div class="card">
                <h2>🏙️ Szczegóły miast</h2>
                <table class="stats-table">
                    <thead>
                        <tr>
                            <th>Miasto</th>
                            <th>Zamówienia</th>
                            <th>Przychód</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(f"""
                        <tr>
                            <td>{m[0]}</td>
                            <td>{m[1]['zamowienia']}</td>
                            <td class="positive">{m[1]['przychod']:.0f} zł</td>
                        </tr>
                        """ for m in miasta_sorted[:15]) if miasta_sorted else '<tr><td colspan="3" style="text-align:center;color:#64748b">Brak danych o miastach</td></tr>'}
                    </tbody>
                </table>
            </div>
            
            <!-- TABELA KATEGORII -->
            <div class="card" style="grid-column: span 2;">
                <h2>📊 Szczegóły kategorii</h2>
                <table class="stats-table">
                    <thead>
                        <tr>
                            <th>Kategoria</th>
                            <th>Szt.</th>
                            <th>Przychód</th>
                            <th>Zakup</th>
                            <th>Prowizja</th>
                            <th>Zysk</th>
                            <th>Marża</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(f"""
                        <tr>
                            <td>{k['kategoria']}</td>
                            <td>{k['sprzedazy']}</td>
                            <td style="color:#22c55e">{k['przychod']:.0f} zł</td>
                            <td style="color:#ef4444">-{k['koszt']:.0f} zł</td>
                            <td style="color:#f59e0b">-{k['prowizja']:.0f} zł</td>
                            <td class="{'positive' if k['zysk'] >= 0 else 'negative'}" style="font-weight:700">{k['zysk']:.0f} zł</td>
                            <td class="{'positive' if k['marza'] >= 0 else 'negative'}">{k['marza']:.1f}%</td>
                        </tr>
                        """ for k in kategorie_stats) if kategorie_stats else '<tr><td colspan="7" style="text-align:center;color:#64748b">Brak danych o sprzedażach</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>
        
        <script>
            // Wykres miast
            new Chart(document.getElementById('miastaChart'), {{
                type: 'bar',
                data: {{
                    labels: {miasta_labels},
                    datasets: [{{
                        label: 'Zamówienia',
                        data: {miasta_values},
                        backgroundColor: 'rgba(59, 130, 246, 0.8)',
                        borderRadius: 4
                    }}]
                }},
                options: {{
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ display: false }} }},
                    scales: {{
                        x: {{ grid: {{ color: '#1e1e2e' }}, ticks: {{ color: '#64748b' }} }},
                        y: {{ grid: {{ display: false }}, ticks: {{ color: '#e2e8f0' }} }}
                    }}
                }}
            }});
            
            // Wykres kategorii
            new Chart(document.getElementById('kategorieChart'), {{
                type: 'bar',
                data: {{
                    labels: {kategorie_labels},
                    datasets: [{{
                        label: 'Zysk (zł)',
                        data: {kategorie_zysk},
                        backgroundColor: {kategorie_zysk}.map(v => v >= 0 ? 'rgba(34, 197, 94, 0.8)' : 'rgba(239, 68, 68, 0.8)'),
                        borderRadius: 4
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ display: false }} }},
                    scales: {{
                        x: {{ grid: {{ display: false }}, ticks: {{ color: '#e2e8f0', maxRotation: 45 }} }},
                        y: {{ grid: {{ color: '#1e1e2e' }}, ticks: {{ color: '#64748b' }} }}
                    }}
                }}
            }});
            
            // Wykres czasowy - dzienny z kumulowanym
            new Chart(document.getElementById('czasChart'), {{
                type: 'line',
                data: {{
                    labels: {dni_labels},
                    datasets: [
                        {{
                            label: 'Narastajaco (zl)',
                            data: {dni_kumulowany},
                            borderColor: '#22c55e',
                            backgroundColor: 'rgba(34, 197, 94, 0.15)',
                            fill: true,
                            tension: 0.3,
                            pointRadius: 4,
                            pointBackgroundColor: '#22c55e',
                            pointBorderColor: '#fff',
                            pointBorderWidth: 2,
                            order: 1
                        }},
                        {{
                            label: 'Dziennie (zl)',
                            data: {dni_przychod},
                            borderColor: '#8b5cf6',
                            backgroundColor: 'rgba(139, 92, 246, 0.3)',
                            fill: false,
                            tension: 0,
                            pointRadius: 5,
                            pointBackgroundColor: '#8b5cf6',
                            pointBorderColor: '#fff',
                            pointBorderWidth: 2,
                            type: 'bar',
                            order: 2
                        }}
                    ]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ 
                        legend: {{ display: true, position: 'top', labels: {{ color: '#e2e8f0' }} }}
                    }},
                    scales: {{
                        x: {{ grid: {{ color: '#1e1e2e' }}, ticks: {{ color: '#64748b', maxRotation: 45 }} }},
                        y: {{ grid: {{ color: '#1e1e2e' }}, ticks: {{ color: '#64748b' }} }}
                    }}
                }}
            }});
            
            // Funkcja uzupełniania adresów
            function uzupelnijAdresy() {{
                if (!confirm('Pobrac adresy z Allegro dla istniejacych zamowien?')) return;
                
                fetch('/analityka/uzupelnij-adresy', {{method: 'POST'}})
                    .then(r => r.json())
                    .then(data => {{
                        if (data.ok) {{
                            alert('Zaktualizowano ' + data.count + ' adresow z ' + data.total);
                            location.reload();
                        }} else {{
                            alert('Blad: ' + (data.error || 'Nieznany'));
                        }}
                    }})
                    .catch(e => alert('Blad: ' + e));
            }}
            
            // Funkcja auto-kategoryzacji wszystkich produktów
            function autoKategoryzujWszystkie() {{
                if (!confirm('Automatycznie przypisac kategorie do WSZYSTKICH produktow na podstawie nazw?')) return;
                
                fetch('/analityka/kategorie/auto', {{method: 'POST'}})
                    .then(r => r.json())
                    .then(data => {{
                        if (data.ok) {{
                            alert('Zaktualizowano ' + data.count + ' produktow!');
                            location.reload();
                        }} else {{
                            alert('Blad: ' + (data.error || 'Nieznany'));
                        }}
                    }})
                    .catch(e => alert('Blad: ' + e));
            }}
        </script>
    </body>
    </html>
    '''



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
            (SELECT COALESCE(SUM(ilosc), 0) FROM produkty WHERE paleta_id = p.id) as aktualna_ilosc,
            (SELECT COALESCE(SUM(cena_brutto), 0) FROM produkty WHERE paleta_id = p.id) as koszt_produktow,
            (SELECT COALESCE(SUM(cena_allegro * ilosc), 0) FROM produkty WHERE paleta_id = p.id AND status = 'dostepny') as wartosc_magazynu,
            (SELECT COALESCE(SUM(CASE WHEN status = 'sprzedany' AND (sprzedano_offline IS NULL OR sprzedano_offline = 0) THEN cena_allegro ELSE 0 END), 0) FROM produkty WHERE paleta_id = p.id) as przychod_produkty,
            COALESCE((SELECT SUM(s.cena * s.ilosc) FROM sprzedaze s JOIN produkty pr ON s.produkt_id = pr.id WHERE pr.paleta_id = p.id AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot','')), 0) as przychod_tabela,
            (SELECT COALESCE(SUM(przychod_offline), 0) FROM produkty WHERE paleta_id = p.id) as przychod_offline,
            (SELECT COALESCE(SUM(sprzedano_offline), 0) FROM produkty WHERE paleta_id = p.id) as sprzedano_offline_szt,
            (SELECT COUNT(*) FROM produkty WHERE paleta_id = p.id AND status = 'sprzedany' AND (sprzedano_offline IS NULL OR sprzedano_offline = 0)) as sprzedano_produkty,
            COALESCE((SELECT SUM(s.ilosc) FROM sprzedaze s JOIN produkty pr ON s.produkt_id = pr.id WHERE pr.paleta_id = p.id AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot','')), 0) as sprzedano_tabela,
            COALESCE((SELECT SUM(s.cena * s.ilosc) FROM sprzedaze s JOIN produkty pr ON s.produkt_id = pr.id WHERE pr.paleta_id = p.id AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot','') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')), 0) as przychod_allegro_only
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
        przychod_produkty = p['przychod_produkty'] or 0  # z produktów ze statusem 'sprzedany' (cena_allegro)
        przychod_tabela = p['przychod_tabela'] or 0  # z tabeli sprzedaze (SUM cena*ilosc)
        przychod_offline = p['przychod_offline'] or 0  # stary system (produkty.przychod_offline)

        # FIX: przychod_tabela JUŻ ZAWIERA sprzedaże offline (kupujacy='offline')
        # więc NIE dodajemy przychod_offline osobno — bo to podwójne liczenie!
        # przychod_offline dodajemy TYLKO jeśli nie ma ich w sprzedaze (przychod_tabela=0)
        if przychod_tabela > 0:
            # Nowy system: wszystko (Allegro + offline) jest w tabeli sprzedaze
            przychod = przychod_tabela
        else:
            # Stary system: brak rekordów w sprzedaze, użyj danych z produktów
            przychod = przychod_produkty + przychod_offline

        # Prowizja Allegro ~11% — TYLKO od Allegro (nie offline)
        przychod_allegro_only = p['przychod_allegro_only'] or 0
        prowizja = przychod_allegro_only * 0.11
        zysk = przychod - koszt - prowizja
        roi = (zysk / koszt * 100) if koszt > 0 else 0
        
        # Status palety - liczymy SZTUKI
        # FIX: sprzedano_tabela JUŻ ZAWIERA offline (kupujacy='offline')
        # więc nie dodajemy sprzedano_offline osobno!

        aktualna_ilosc = p['aktualna_ilosc'] or 0  # ile teraz jest w magazynie
        sprzedanych_offline = p['sprzedano_offline_szt'] or 0  # stary system
        sprzedano_tabela = p['sprzedano_tabela'] or 0  # z tabeli sprzedaze (ile sztuk) — zawiera offline
        sprzedano_produkty = p['sprzedano_produkty'] or 0  # COUNT produktów ze statusem 'sprzedany'

        # FIX: Jeśli mamy dane w sprzedaze — nie dodawaj offline osobno
        if sprzedano_tabela > 0:
            sprzedanych = sprzedano_tabela
        else:
            sprzedanych = sprzedano_produkty + sprzedanych_offline
        
        # Wszystkich = w magazynie + sprzedanych
        wszystkich = aktualna_ilosc + sprzedanych
        
        # Zostało = aktualna ilość w magazynie
        zostalo = aktualna_ilosc
        
        if wszystkich == 0:
            status = 'pusta'
            status_color = '#666'
        elif zostalo == 0 and sprzedanych > 0:
            status = 'zakończona'
            status_color = '#22c55e' if zysk > 0 else '#ef4444'
        else:
            progress = (sprzedanych / wszystkich * 100) if wszystkich > 0 else 0
            status = f'{progress:.0f}% sprzedane'
            status_color = '#f59e0b' if progress < 100 else '#22c55e'
        
        # Koszt per sztuka
        koszt_szt = (koszt / wszystkich) if wszystkich > 0 else 0

        # Prognoza zysku (dla palet w trakcie sprzedaży)
        if sprzedanych > 0 and zostalo > 0:
            avg_cena = przychod / sprzedanych
            prognoza_przychod = avg_cena * wszystkich
            prognoza_prowizja = prognoza_przychod * 0.11
            prognoza = prognoza_przychod - koszt - prognoza_prowizja
        else:
            prognoza = zysk

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
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bilans Palet - {get_config_cached("brand_name", "AKCES HUB")}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #0a0a0f;
                color: #e2e8f0;
                min-height: 100vh;
                padding: 20px;
            }}
            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 30px;
                padding-bottom: 20px;
                border-bottom: 1px solid #1e1e2e;
            }}
            .header h1 {{ color: #fff; font-size: 1.8rem; }}
            .header a {{ color: #888; text-decoration: none; }}
            .header a:hover {{ color: #fff; }}
            .summary-grid {{
                display: grid;
                grid-template-columns: repeat(5, 1fr);
                gap: 16px;
                margin-bottom: 30px;
            }}
            .summary-card {{
                background: linear-gradient(135deg, #1a1a2e 0%, #16162a 100%);
                border-radius: 12px;
                padding: 18px;
                text-align: center;
            }}
            .summary-card .value {{
                font-size: 1.8rem;
                font-weight: bold;
                margin-bottom: 4px;
            }}
            .summary-card .label {{
                color: #888;
                font-size: 0.85rem;
            }}
            .green {{ color: #22c55e; }}
            .red {{ color: #ef4444; }}
            .yellow {{ color: #f59e0b; }}
            .blue {{ color: #3b82f6; }}
            .content-grid {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 20px;
                margin-bottom: 30px;
            }}
            .card {{
                background: linear-gradient(135deg, #1a1a2e 0%, #16162a 100%);
                border-radius: 12px;
                padding: 20px;
            }}
            .card h2 {{ margin-bottom: 20px; font-size: 1.2rem; }}
            .chart-container {{ height: 300px; }}
            .filter-bar {{
                display: flex;
                gap: 12px;
                align-items: center;
                margin-bottom: 15px;
            }}
            .filter-bar select {{
                padding: 8px 12px;
                background: #1e1e2e;
                border: 1px solid #2a2a3e;
                border-radius: 8px;
                color: #e2e8f0;
                font-size: 0.9rem;
            }}
            .filter-bar label {{ color: #888; font-size: 0.9rem; }}
            .palety-table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 0.85rem;
            }}
            .palety-table th, .palety-table td {{
                padding: 10px 6px;
                text-align: left;
                border-bottom: 1px solid #2a2a3e;
            }}
            .palety-table th {{
                color: #888;
                font-weight: 500;
                font-size: 0.75rem;
                text-transform: uppercase;
                cursor: pointer;
                user-select: none;
                white-space: nowrap;
            }}
            .palety-table th:hover {{ color: #fff; }}
            .palety-table th .sort-arrow {{ font-size: 0.7rem; margin-left: 3px; display: inline-block; min-width: 10px; transition: opacity 0.15s; }}
            .palety-table tbody tr {{
                cursor: pointer;
                transition: background 0.15s;
            }}
            .palety-table tbody tr:hover {{
                background: rgba(255,255,255,0.04);
            }}
            .status-badge {{
                padding: 4px 8px;
                border-radius: 4px;
                font-size: 0.7rem;
                white-space: nowrap;
            }}
            .progress-bar {{
                width: 100%;
                height: 6px;
                background: #2a2a3e;
                border-radius: 3px;
                overflow: hidden;
            }}
            .progress-fill {{
                height: 100%;
                background: linear-gradient(90deg, #22c55e, #3b82f6);
                transition: width 0.3s;
            }}
            .prognoza {{ color: #94a3b8; font-style: italic; }}
            @media (max-width: 900px) {{
                .summary-grid {{ grid-template-columns: repeat(2, 1fr); }}
                .content-grid {{ grid-template-columns: 1fr; }}
                .palety-table {{ font-size: 0.75rem; }}
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Bilans Palet</h1>
            <a href="/analityka">&larr; Powrot do Analityki</a>
        </div>

        <div class="summary-grid">
            <div class="summary-card">
                <div class="value blue">{len(palety_stats)}</div>
                <div class="label">Palet lacznie</div>
            </div>
            <div class="summary-card">
                <div class="value red">{total_koszt:,.0f} zl</div>
                <div class="label">Koszt zakupu</div>
            </div>
            <div class="summary-card">
                <div class="value green">{total_przychod:,.0f} zl</div>
                <div class="label">Przychod</div>
            </div>
            <div class="summary-card">
                <div class="value yellow">{total_prowizja:,.0f} zl</div>
                <div class="label">Prowizje Allegro</div>
            </div>
            <div class="summary-card">
                <div class="value {'green' if total_zysk >= 0 else 'red'}">{total_zysk:,.0f} zl ({total_roi:.0f}%)</div>
                <div class="label">Zysk netto (ROI)</div>
            </div>
        </div>

        <div class="content-grid">
            <div class="card">
                <h2>TOP 10 Palet wg ROI</h2>
                <div class="chart-container">
                    <canvas id="roiChart"></canvas>
                </div>
            </div>
            <div class="card">
                <h2>Zysk kumulacyjny w czasie</h2>
                <div class="chart-container">
                    <canvas id="cumChart"></canvas>
                </div>
            </div>
        </div>

        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;">
                <h2>Wszystkie palety ({len(palety_stats)})</h2>
                <div class="filter-bar">
                    <input type="text" id="searchInput" oninput="filterTable()" placeholder="Szukaj palety..." style="padding:8px 12px;background:#1e1e2e;border:1px solid #2a2a3e;border-radius:8px;color:#e2e8f0;font-size:0.9rem;width:200px;">
                    <label>Dostawca:</label>
                    <select id="dostawcaFilter" onchange="filterTable()">
                        <option value="">Wszyscy</option>
                        {''.join(f'<option value="{d}">{d}</option>' for d in dostawcy)}
                    </select>
                </div>
            </div>
            <div style="overflow-x:auto;">
            <table class="palety-table" id="paletyTable">
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
                        <th>Postep</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(f"""
                    <tr onclick="window.location='/palety/{p['id']}'" data-dostawca="{p['dostawca']}" data-vals="{p['nazwa'][:30]}|{p['dostawca']}|{p['data']}|{p['koszt']:.2f}|{p['koszt_szt']:.2f}|{p['przychod']:.2f}|{p['zysk']:.2f}|{p['roi']:.2f}|{p['prognoza']:.2f}">
                        <td><strong>{p['nazwa'][:30]}</strong></td>
                        <td>{p['dostawca']}</td>
                        <td>{p['data']}</td>
                        <td>{p['koszt']:,.0f} zl</td>
                        <td>{p['koszt_szt']:,.0f} zl</td>
                        <td class="green">{p['przychod']:,.0f} zl</td>
                        <td class="{'green' if p['zysk'] >= 0 else 'red'}">{p['zysk']:,.0f} zl</td>
                        <td class="{'green' if p['roi'] >= 0 else 'red'}">{p['roi']:.0f}%</td>
                        <td class="prognoza{' green' if p['prognoza'] >= 0 else ' red'}">{p['prognoza']:,.0f} zl</td>
                        <td>
                            <div class="progress-bar">
                                <div class="progress-fill" style="width:{(p['sprzedanych']/p['wszystkich']*100) if p['wszystkich']>0 else 0}%"></div>
                            </div>
                            <small style="color:#888">{p['sprzedanych']}/{p['wszystkich']} szt.</small>
                        </td>
                        <td><span class="status-badge" style="background:{p['status_color']}20;color:{p['status_color']}">{p['status']}</span></td>
                    </tr>
                    """ for p in palety_stats) if palety_stats else '<tr><td colspan="11" style="text-align:center;color:#888;">Brak palet w bazie</td></tr>'}
                </tbody>
            </table>
            </div>
        </div>

        <script>
            // Wykres ROI
            new Chart(document.getElementById('roiChart'), {{
                type: 'bar',
                data: {{
                    labels: {chart_labels},
                    datasets: [{{
                        label: 'ROI %',
                        data: {chart_roi},
                        backgroundColor: {[f"'{'#22c55e' if r >= 0 else '#ef4444'}'" for r in chart_roi]},
                        borderRadius: 4
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ display: false }} }},
                    scales: {{
                        y: {{ grid: {{ color: '#2a2a3e' }}, ticks: {{ color: '#888', callback: v => v + '%' }} }},
                        x: {{ grid: {{ display: false }}, ticks: {{ color: '#888', maxRotation: 45 }} }}
                    }}
                }}
            }});

            // Wykres kumulacyjny zysku
            new Chart(document.getElementById('cumChart'), {{
                type: 'line',
                data: {{
                    labels: {cum_dates},
                    datasets: [{{
                        label: 'Zysk kumulacyjny (zl)',
                        data: {cum_zysk},
                        borderColor: '#3b82f6',
                        backgroundColor: 'rgba(59,130,246,0.1)',
                        fill: true,
                        tension: 0.3,
                        pointRadius: 3,
                        pointBackgroundColor: '#3b82f6'
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ display: false }} }},
                    scales: {{
                        y: {{ grid: {{ color: '#2a2a3e' }}, ticks: {{ color: '#888', callback: v => v + ' zl' }} }},
                        x: {{ grid: {{ display: false }}, ticks: {{ color: '#888', maxRotation: 45 }} }}
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
    </body>
    </html>
    '''



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
            <td>{p['cena_allegro']:.0f} zł</td>
            <td>
                <select class="kat-select" data-id="{p['id']}" style="padding:6px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:6px;color:#fff">
                    {''.join(f'<option value="{k}" {"selected" if kat == k else ""}>{v}</option>' for k, v in kategorie.items())}
                </select>
            </td>
            <td>{'<span style="color:#f59e0b">💡 ' + kategorie.get(sugerowana, sugerowana) + '</span>' if zmiana else '<span style="color:#22c55e">✓</span>'}</td>
        </tr>
        '''
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>🏷️ Edycja kategorii - {get_config_cached("brand_name", "AKCES HUB")}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #0a0a0f; 
                color: #e2e8f0;
                min-height: 100vh;
                padding: 20px;
            }}
            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
            }}
            .header h1 {{ color: #fff; font-size: 1.5rem; }}
            .btn {{
                padding: 10px 20px;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                cursor: pointer;
                text-decoration: none;
                color: #fff;
            }}
            .btn-blue {{ background: #3b82f6; }}
            .btn-green {{ background: #22c55e; }}
            .btn-purple {{ background: #8b5cf6; }}
            .toolbar {{
                display: flex;
                gap: 10px;
                margin-bottom: 20px;
                flex-wrap: wrap;
                align-items: center;
                background: #12121a;
                padding: 15px;
                border-radius: 12px;
            }}
            .toolbar select {{
                padding: 10px;
                background: #1e1e2e;
                border: 1px solid #2a2a3a;
                border-radius: 8px;
                color: #fff;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: #12121a;
                border-radius: 12px;
                overflow: hidden;
            }}
            th, td {{
                padding: 12px;
                text-align: left;
                border-bottom: 1px solid #1e1e2e;
            }}
            th {{ background: #1e1e2e; color: #64748b; font-weight: 500; }}
            tr:hover {{ background: rgba(59, 130, 246, 0.1); }}
            .stats {{
                display: flex;
                gap: 15px;
                margin-bottom: 20px;
            }}
            .stat {{
                background: #12121a;
                padding: 15px 20px;
                border-radius: 10px;
                text-align: center;
            }}
            .stat .num {{ font-size: 1.3rem; font-weight: 700; color: #fff; }}
            .stat .label {{ font-size: 0.75rem; color: #64748b; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🏷️ Edycja kategorii produktów</h1>
            <a href="/analityka" class="btn btn-blue">← Powrót</a>
        </div>
        
        <div class="stats">
            <div class="stat">
                <div class="num">{len(produkty)}</div>
                <div class="label">PRODUKTÓW</div>
            </div>
            <div class="stat">
                <div class="num">{sum(1 for p in produkty if auto_kategoryzuj(p['nazwa']) != (p['kategoria'] or 'inne'))}</div>
                <div class="label">DO ZMIANY</div>
            </div>
        </div>
        
        <div class="toolbar">
            <label><input type="checkbox" id="selectAll"> Zaznacz wszystkie</label>
            <span style="color:#64748b">|</span>
            <span>Ustaw zaznaczonym:</span>
            <select id="bulkKategoria">
                {''.join(f'<option value="{k}">{v}</option>' for k, v in kategorie.items())}
            </select>
            <button class="btn btn-purple" onclick="bulkUpdate()">📝 Zastosuj</button>
            <span style="color:#64748b">|</span>
            <button class="btn btn-green" onclick="autoKategoryzuj()">🤖 Auto-kategoryzuj wszystkie</button>
        </div>
        
        <table>
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
                            this.style.borderColor = '#22c55e';
                            setTimeout(() => this.style.borderColor = '#2a2a3a', 1000);
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
    </body>
    </html>
    '''



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
    conn.execute(f'UPDATE produkty SET kategoria = ? WHERE id IN ({placeholders})', [kategoria] + ids)
    conn.commit()
    
    return jsonify({'ok': True, 'count': len(ids)})



@analityka_bp.route('/analityka/kategorie/auto', methods=['POST'])
def analityka_kategorie_auto():
    """Automatycznie kategoryzuj wszystkie produkty"""
    from modules.database import get_db
    from modules.shared import auto_kategoryzuj
    
    conn = get_db()
    produkty = conn.execute('SELECT id, nazwa, kategoria FROM produkty').fetchall()
    
    print(f"\n🔍 Auto-kategoryzacja: {len(produkty)} produktów w bazie")
    
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
            print(f"  🏷️ [{p['id']}] {obecna_kat} → {nowa_kat}: {nazwa[:50]}")
        
    # Pokaż pierwsze 5 nazw produktów dla diagnostyki
    print(f"\n📋 Przykładowe nazwy produktów:")
    for p in produkty[:5]:
        print(f"  - {p['nazwa'][:60] if p['nazwa'] else '(brak nazwy)'}")
    
    print(f"\n📊 Statystyki kategorii:")
    for kat, cnt in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {kat}: {cnt}")
    
    conn.commit()
    
    print(f"\n✅ Zaktualizowano {count}/{len(produkty)} produktów")
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
        return '#22c55e' if (v or 0) > 0 else '#ef4444'
    def roi_cls(v):
        return '#22c55e' if (v or 0) > 0 else '#ef4444'
    def sign(v):
        return '+' if (v or 0) > 0 else ''

    okazje_html = ''
    for r in okazje_list:
        score = r.get('okazja_score', 0)
        badge_bg = '#22c55e' if score >= 9 else '#f59e0b' if score >= 7 else '#3b82f6'
        okazje_html += f"""
        <div style='background:#12121a;border:1px solid #2a2a4a;border-radius:12px;padding:15px;margin-bottom:10px;transition:border-color 0.2s' onmouseover="this.style.borderColor='#f59e0b'" onmouseout="this.style.borderColor='#2a2a4a'">
          <div style='display:flex;align-items:center;gap:12px;margin-bottom:10px'>
            <div style='background:{badge_bg};color:#000;font-weight:800;font-size:0.85rem;border-radius:8px;padding:4px 10px;min-width:52px;text-align:center'>★{score}/10</div>
            <div style='flex:1'>
              <div style='font-weight:600'>{r.get('nazwa') or 'Produkt #' + str(r.get('produkt_id','?'))}</div>
              <div style='color:#64748b;font-size:0.78rem'>{r.get('kategoria') or 'inne'} · {r.get('dostawca') or ''}</div>
            </div>
          </div>
          <div style='display:flex;gap:20px;flex-wrap:wrap;font-size:0.85rem'>
            <div><div style='color:#64748b;font-size:0.72rem'>SPRZEDANO</div>{r.get('sprzedaz_szt',0)} szt</div>
            <div><div style='color:#64748b;font-size:0.72rem'>PRZYCHÓD</div>{(r.get('przychod') or 0):.0f} zł</div>
            <div><div style='color:#64748b;font-size:0.72rem'>ROI</div><span style='color:{roi_cls(r.get("roi"))};font-weight:600'>{(r.get("roi") or 0):.0f}%</span></div>
            <div><div style='color:#64748b;font-size:0.72rem'>TREND M/M</div><span style='color:{trend_cls(r.get("trend_mm"))};font-weight:600'>{sign(r.get("trend_mm"))}{(r.get("trend_mm") or 0):.0f}%</span></div>
          </div>
        </div>"""

    if not okazje_html:
        okazje_html = "<div style='background:#12121a;border:1px solid #2a2a4a;border-radius:12px;padding:30px;text-align:center;color:#64748b'>Brak okazji (score ≥ 6) w tym miesiącu.<br><br>Uruchom: <code style='background:#1e1e2e;padding:4px 8px;border-radius:6px'>python analyze_trends.py</code></div>"

    wszystkie_html = ''
    for r in wszystkie_list:
        score = r.get('okazja_score', 0)
        badge_bg = '#22c55e' if score >= 9 else '#f59e0b' if score >= 7 else '#3b82f6' if score >= 5 else '#475569'
        wszystkie_html += f"""
        <div style='background:#12121a;border:1px solid #1e1e2e;border-radius:10px;padding:12px;margin-bottom:8px;display:flex;align-items:center;gap:12px'>
          <div style='background:{badge_bg};color:#000;font-weight:700;font-size:0.8rem;border-radius:6px;padding:3px 8px;min-width:40px;text-align:center'>{score}/10</div>
          <div style='flex:1;font-size:0.85rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{r.get('nazwa') or 'Produkt'}</div>
          <div style='display:flex;gap:15px;font-size:0.8rem;white-space:nowrap'>
            <span>{r.get('sprzedaz_szt',0)} szt</span>
            <span style='color:{roi_cls(r.get("roi"))}'>{(r.get("roi") or 0):.0f}% ROI</span>
            <span style='color:{trend_cls(r.get("trend_mm"))}'>{sign(r.get("trend_mm"))}{(r.get("trend_mm") or 0):.0f}%</span>
          </div>
        </div>"""

    if not wszystkie_html:
        wszystkie_html = "<div style='color:#64748b;padding:20px;text-align:center'>Brak danych. Uruchom: <code>python analyze_trends.py</code></div>"

    # === SEKCJA LIVE SCRAPER (Warrington + Jobalots + Szukaj palet) ===
    live_scraper_section = """
        <div style='background:#12121a;border:1px solid #0ea5e940;border-radius:16px;padding:20px;margin-bottom:20px'>
          <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:14px'>
            <div style='color:#0ea5e9;font-weight:700;font-size:1rem'>🔴 Aktualne palety — na żywo</div>
            <div style='color:#64748b;font-size:0.75rem'>dane pobierane bezpośrednio ze stron dostawców</div>
          </div>
          <div style='display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px'>
            <div style='background:#0a1520;border:1px solid #0ea5e930;border-radius:10px;padding:14px'>
              <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:10px'>
                <a href='https://warrington.store/products/new' target='_blank' style='color:#0ea5e9;font-weight:600;font-size:0.9rem;text-decoration:none'>🏪 Warrington.store ↗</a>
                <button onclick='loadWarrington()' id='btn-warrington' style='background:#0ea5e920;color:#0ea5e9;border:1px solid #0ea5e940;border-radius:6px;padding:4px 12px;font-size:0.75rem;cursor:pointer'>▶ Załaduj</button>
              </div>
              <div id='warrington-results' style='color:#64748b;font-size:0.8rem'>Kliknij "Załaduj" aby pobrać aktualne palety</div>
            </div>
            <div style='background:#0a100a;border:1px solid #f59e0b30;border-radius:10px;padding:14px'>
              <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:10px'>
                <a href='https://jobalots.com/pl/pages/products-on-auction?page=1&currency=pln&type=pallets' target='_blank' style='color:#f59e0b;font-weight:600;font-size:0.9rem;text-decoration:none'>🏪 Jobalots.com ↗</a>
                <button onclick='loadJobalots()' id='btn-jobalots' style='background:#f59e0b20;color:#f59e0b;border:1px solid #f59e0b40;border-radius:6px;padding:4px 12px;font-size:0.75rem;cursor:pointer'>▶ Załaduj</button>
              </div>
              <div id='jobalots-results' style='color:#64748b;font-size:0.8rem'>Kliknij "Załaduj" aby pobrać aukcje palet</div>
            </div>
          </div>
          <div id='szukaj-panel' style='background:#0f1a12;border:1px solid #22c55e30;border-radius:10px;padding:14px'>
            <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:10px'>
              <div style='color:#22c55e;font-weight:600;font-size:0.9rem'>🛒 Szukaj palet pod mój profil (AI)</div>
              <div style='color:#64748b;font-size:0.73rem'>Perplexity analizuje Twój profil i szuka najlepszych ofert</div>
            </div>
            %%SZUKAJ_PLACEHOLDER%%
          </div>
        </div>
        <script>
        function loadWarrington() {
          var btn = document.getElementById('btn-warrington');
          var res = document.getElementById('warrington-results');
          btn.disabled = true; btn.textContent = '⏳ Ładowanie...';
          fetch('/analityka/okazje/scrape-warrington')
            .then(r => r.json())
            .then(d => {
              btn.disabled = false; btn.textContent = '🔄 Odśwież';
              if (!d.ok) { res.innerHTML = '<span style="color:#ef4444">Błąd: ' + d.error + '</span>'; return; }
              if (!d.products.length) { res.innerHTML = '<span style="color:#64748b">Brak produktów</span>'; return; }
              var html = '<div style="max-height:280px;overflow-y:auto">';
              d.products.forEach(function(p) {
                var priceStr = p.price_text || (p.price ? '£' + p.price.toFixed(0) : '');
                html += '<a href="' + p.url + '" target="_blank" style="display:block;padding:6px 8px;margin:3px 0;background:#0ea5e910;border:1px solid #0ea5e920;border-radius:6px;color:#e2e8f0;text-decoration:none;font-size:0.8rem">';
                html += p.title;
                if (priceStr && priceStr !== '?' && priceStr !== 'kategoria') html += ' <span style="color:#0ea5e9;font-weight:600">' + priceStr + '</span>';
                html += ' ↗</a>';
              });
              html += '</div><div style="color:#64748b;font-size:0.72rem;margin-top:6px">Źródło: ' + (d.source||'') + ' | Łącznie: ' + d.total + '</div>';
              res.innerHTML = html;
            })
            .catch(e => { btn.disabled=false; btn.textContent='▶ Załaduj'; res.innerHTML='<span style="color:#ef4444">Błąd połączenia</span>'; });
        }
        function loadJobalots() {
          var btn = document.getElementById('btn-jobalots');
          var res = document.getElementById('jobalots-results');
          btn.disabled = true; btn.textContent = '⏳ Ładowanie...';
          fetch('/analityka/okazje/scrape-jobalots')
            .then(r => r.json())
            .then(d => {
              btn.disabled = false; btn.textContent = '🔄 Odśwież';
              if (!d.ok) { res.innerHTML = '<span style="color:#ef4444">Błąd: ' + (d.error||'') + '</span>' + (d.fallback_url ? '<br><a href="'+d.fallback_url+'" target="_blank" style="color:#f59e0b">→ Otwórz Jobalots ↗</a>' : ''); return; }
              if (d.fallback_url && !d.products.length) {
                res.innerHTML = '<div style="color:#f59e0b;font-size:0.8rem">' + (d.note||'') + '</div><a href="' + d.fallback_url + '" target="_blank" style="color:#f59e0b;font-size:0.8rem">→ Otwórz Jobalots ↗</a>';
                return;
              }
              if (!d.products.length) { res.innerHTML = '<span style="color:#64748b">Brak produktów</span>'; return; }
              var html = '';
              if (d.note) html += '<div style="color:#f59e0b;font-size:0.73rem;margin-bottom:6px">' + d.note + '</div>';
              html += '<div style="max-height:280px;overflow-y:auto">';
              d.products.forEach(function(p) {
                html += '<a href="' + p.url + '" target="_blank" style="display:block;padding:6px 8px;margin:3px 0;background:#f59e0b10;border:1px solid #f59e0b20;border-radius:6px;color:#e2e8f0;text-decoration:none;font-size:0.78rem">';
                html += '<div style="font-weight:600;margin-bottom:2px">';
                if (p.tag) html += p.tag + ' ';
                html += p.title;
                if (p.discount > 30) html += ' <span style="background:#ef4444;color:#fff;padding:1px 5px;border-radius:4px;font-size:0.65rem;font-weight:700">-' + p.discount + '%</span>';
                html += '</div>';
                html += '<div style="display:flex;gap:8px;flex-wrap:wrap;font-size:0.72rem;color:#94a3b8">';
                if (p.price_text) html += '<span style="color:#f59e0b;font-weight:700">' + p.price_text + '</span>';
                if (p.rrp) html += '<span style="text-decoration:line-through;color:#64748b">' + Math.round(p.rrp) + ' RRP</span>';
                if (p.qty) html += '<span>' + p.qty + ' szt</span>';
                if (p.bid_count) html += '<span>' + p.bid_count + ' ofert</span>';
                if (p.end_at) html += '<span>⏰ ' + p.end_at + '</span>';
                html += '</div></a>';
              });
              html += '</div><div style="color:#64748b;font-size:0.72rem;margin-top:6px">Łącznie: ' + d.total + ' palet</div>';
              res.innerHTML = html;
            })
            .catch(e => { btn.disabled=false; btn.textContent='▶ Załaduj'; res.innerHTML='<span style="color:#ef4444">Błąd połączenia</span>'; });
        }
        </script>"""

    # === SEKCJA PERPLEXITY ===
    if not has_trendy:
        perplexity_section = ""
        live_scraper_section = live_scraper_section.replace('%%SZUKAJ_PLACEHOLDER%%',
            "<div style='color:#64748b;font-size:0.73rem'>Dodaj klucz Perplexity API poniżej</div>")
    elif not has_perplexity:
        live_scraper_section = live_scraper_section.replace('%%SZUKAJ_PLACEHOLDER%%',
            "<div style='color:#64748b;font-size:0.73rem'>Dodaj klucz Perplexity API poniżej aby aktywować</div>")
        perplexity_section = f"""
        <div style='background:#12121a;border:1px solid #3b82f640;border-radius:16px;padding:20px;margin-bottom:20px'>
          <div style='color:#3b82f6;font-weight:700;font-size:1rem;margin-bottom:12px'>🤖 Analiza rynkowa (Perplexity AI)</div>
          <div style='color:#64748b;font-size:0.85rem;margin-bottom:12px'>Dodaj klucz API Perplexity żeby otrzymać analizę rynkową produktów na podstawie Twoich trendów sprzedaży.</div>
          <form method='POST' action='/analityka/okazje/set-perplexity-key' style='display:flex;gap:8px;flex-wrap:wrap'>
            <input type='password' name='api_key' placeholder='pplx-xxxxxxxxxxxxxxxx' style='flex:1;min-width:220px;background:#0a0a0f;border:1px solid #2a2a4a;border-radius:8px;padding:8px 12px;color:#e2e8f0;font-size:0.85rem'>
            <button type='submit' style='background:#3b82f6;color:#fff;border:none;border-radius:8px;padding:8px 16px;font-weight:600;cursor:pointer'>Zapisz klucz</button>
          </form>
          <div style='color:#475569;font-size:0.75rem;margin-top:8px'>Klucz Perplexity → <a href='https://www.perplexity.ai/settings/api' target='_blank' style='color:#7dd3fc'>perplexity.ai/settings/api</a></div>
        </div>"""
    else:
        # Jest klucz — pokaż przycisk analizy i ewentualny cache
        cached_html = ""
        if perplexity_odpowiedz:
            # Użyj tego samego formatera co prawy panel (definiowany niżej)
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
                return "<div style='color:#64748b;font-size:0.85rem;margin-top:10px'>Brak analizy — kliknij przycisk powyżej.</div>"
            import re as _re, html as _h
            safe = _h.escape(odp)
            # Usuń referencje [1][2] itp.
            safe = _re.sub(r'\[(\d+)\]', '', safe)
            # Nagłówki → kolorowe karty z separacją
            safe = _re.sub(r'(?m)^###\s+(.+)$', r'</div><div style="background:#1a1a2e;border:1px solid #2a2a4a;border-radius:10px;padding:12px;margin:14px 0 8px"><div style="color:#f59e0b;font-weight:700;font-size:0.95rem;margin-bottom:6px">\1</div><div>', safe)
            safe = _re.sub(r'(?m)^##\s+(.+)$', r'</div><div style="background:#0f1520;border-left:3px solid #3b82f6;padding:10px 12px;margin:12px 0 6px;border-radius:0 8px 8px 0"><div style="color:#7dd3fc;font-weight:700;font-size:0.9rem">\1</div></div><div>', safe)
            # Numerowane palety (1. 2. 3.) → wyróżnione karty
            safe = _re.sub(r'(?m)^(\d+)\.\s+(.+)$', r'<div style="background:#12121a;border:1px solid #2a2a4a;border-radius:8px;padding:10px 12px;margin:8px 0;position:relative;padding-left:40px"><span style="position:absolute;left:10px;top:10px;background:#f59e0b;color:#000;font-weight:800;border-radius:50%;width:22px;height:22px;display:flex;align-items:center;justify-content:center;font-size:0.75rem">\1</span>\2</div>', safe)
            # Linie z "Link:" → duży przycisk z linkiem (PRZED bold i bullet!)
            def _make_link_btn(m):
                url = m.group(1)
                rest = m.group(2) or ''
                rest = _re.sub(r'\*\*', '', rest).strip()
                # Wyciągnij nazwę produktu z URL
                path = url.split('/')[-1].split('?')[0]
                label = path.replace('-', ' ').replace('_', ' ').title()[:40]
                if not label or len(label) < 3:
                    domain = url.split('/')[2] if len(url.split('/')) > 2 else url
                    label = domain.replace('www.', '')
                return f'<div style="margin:6px 0"><a href="{url}" target="_blank" style="display:inline-block;background:#3b82f6;color:#fff;padding:8px 18px;border-radius:8px;text-decoration:none;font-weight:700;font-size:0.85rem">🔗 {label} ↗</a> <span style="color:#64748b;font-size:0.73rem">{rest}</span></div>'
            safe = _re.sub(r'(?m)^-\s+\*{0,2}[Ll]ink:?\*{0,2}\s*(https?://[^\s<>&]+)(.*?)$', _make_link_btn, safe)
            # Bold
            safe = _re.sub(r'\*\*(.+?)\*\*', r'<strong style="color:#f1f5f9">\1</strong>', safe)
            # Pozostałe linki URL → klikalne (ale nie te już w przyciskach)
            safe = _re.sub(r'(?<!href=")(https?://[^\s<>"&]+)(?!")', r'<a href="\1" target="_blank" style="color:#7dd3fc;text-decoration:underline;word-break:break-all;font-size:0.8rem">\1</a>', safe)
            # Bullet listy → czytelne elementy
            safe = _re.sub(r'(?m)^[\u2022\-]\s+(.+)$', r'<div style="padding:4px 0 4px 16px;border-left:2px solid #2a2a4a;margin:3px 0;font-size:0.82rem">\1</div>', safe)
            # Separator ---
            safe = _re.sub(r'(?m)^---+$', r'<hr style="border:none;border-top:1px solid #2a2a4a;margin:12px 0">', safe)
            safe = safe.replace('\n', '<br>')
            # Wyczyść puste divy
            safe = safe.replace('<div></div>', '').replace('<br><br><br>', '<br>')
            cit_items = ''
            if cits:
                for ci, cv in enumerate(cits[:8]):
                    short = cv[:80] + ('...' if len(cv) > 80 else '')
                    cit_items += f"<a href='{cv}' target='_blank' style='color:#7dd3fc;font-size:0.72rem;display:block;margin:2px 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>[{ci+1}] {short}</a>"
            cit_html2 = f"<div style='margin-top:10px;padding-top:10px;border-top:1px solid #1e1e2e'><div style='color:#64748b;font-size:0.72rem;margin-bottom:4px'>Źródła ({len(cits)}):</div>{cit_items}</div>" if cits else ''
            btn = f"<form method='POST' action='{refresh_url}' style='margin:0'><button type='submit' style='background:#1e1e2e;color:#94a3b8;border:1px solid #2a2a4a;border-radius:6px;padding:3px 10px;font-size:0.72rem;cursor:pointer'>🔄 Odśwież</button></form>"
            return f"<div style='background:#0a0f0a;border:1px solid #22c55e30;border-radius:10px;padding:14px;margin-top:12px'><div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:10px'><div style='color:#22c55e;font-size:0.78rem;font-weight:600'>✅ {ts}</div>{btn}</div><div style='color:#e2e8f0;font-size:0.83rem;line-height:1.75'>{safe}</div>{cit_html2}</div>"

        cached_html = _cache_block(_left_odpowiedz, _left_citations, _left_data,
            '/analityka/okazje/perplexity-analyze',
            'Analiza sprzedaży', '📊')

        szukaj_html_block = _cache_block(szukaj_odpowiedz, szukaj_citations, szukaj_data,
            '/analityka/okazje/perplexity-szukaj',
            'Okazje zakupowe', '🛒')

        # Wstaw przycisk "Szukaj" + wyniki do panelu w live_scraper_section
        _szukaj_panel_content = f"""<form method='POST' action='/analityka/okazje/perplexity-szukaj' onsubmit='showLoading(this,"szukaj")'>
                <button id='btn-szukaj' type='submit' style='width:100%;background:linear-gradient(135deg,#22c55e,#16a34a);color:#fff;border:none;border-radius:8px;padding:8px;font-weight:600;cursor:pointer;font-size:0.82rem'>
                  🔎 Szukaj teraz
                </button>
              </form>
              <div id='loading-szukaj' style='display:none;text-align:center;padding:10px;color:#22c55e;font-size:0.82rem'>
                <span style='animation:spin 1s linear infinite;display:inline-block'>⏳</span> Szukam palet... (~30-45 sek)
              </div>
              {szukaj_html_block}"""
        live_scraper_section = live_scraper_section.replace('%%SZUKAJ_PLACEHOLDER%%', _szukaj_panel_content)

        perplexity_section = f"""
        <div style='background:#12121a;border:1px solid #8b5cf640;border-radius:16px;padding:20px;margin-bottom:20px'>
          <div style='display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:10px'>
            <div style='color:#8b5cf6;font-weight:700;font-size:1rem'>🤖 Perplexity AI</div>
            <div style='display:flex;align-items:center;gap:8px'>
              <form method='POST' action='/analityka/okazje/set-perplexity-model' style='margin:0;display:flex;align-items:center;gap:6px'>
                <span style='color:#64748b;font-size:0.75rem'>Model:</span>
                <select name='model' onchange='this.form.submit()' style='background:#1e1e2e;color:#e2e8f0;border:1px solid #2a2a4a;border-radius:6px;padding:3px 8px;font-size:0.75rem;cursor:pointer'>
                  <option value='sonar-pro' {{'selected' if perplexity_model in ("sonar","sonar-pro") else ""}}>Sonar Pro ⭐ (zalecany)</option>

                  <option value='sonar-reasoning' {{'selected' if perplexity_model=="sonar-reasoning" else ""}}>Sonar Reasoning</option>
                  <option value='sonar-reasoning-pro' {{'selected' if perplexity_model=="sonar-reasoning-pro" else ""}}>Sonar Reasoning Pro</option>
                </select>
              </form>
              <form method='POST' action='/analityka/okazje/remove-perplexity-key' onsubmit="return confirm('Usunąć klucz Perplexity?')" style='margin:0'>
                <button type='submit' style='background:transparent;color:#475569;border:none;cursor:pointer;font-size:0.8rem'>🗑️ usuń klucz</button>
              </form>
            </div>
          </div>
          </div>

          <div style='background:#1a1025;border:1px solid #8b5cf630;border-radius:12px;padding:14px'>
              <div style='color:#8b5cf6;font-weight:600;font-size:0.85rem;margin-bottom:4px'>📊 Analiza moich sprzedaży</div>
              <div style='color:#64748b;font-size:0.75rem;margin-bottom:10px'>Ceny rynkowe produktów z palet/magazynu + co warto wystawiać</div>
              <form method='POST' action='/analityka/okazje/perplexity-analyze' onsubmit='showLoading(this,"analyze")'>
                <button id='btn-analyze' type='submit' style='width:100%;background:linear-gradient(135deg,#8b5cf6,#6d28d9);color:#fff;border:none;border-radius:8px;padding:8px;font-weight:600;cursor:pointer;font-size:0.82rem'>
                  🔍 Analizuj moje produkty
                </button>
              </form>
              <div id='loading-analyze' style='display:none;text-align:center;padding:10px;color:#8b5cf6;font-size:0.82rem'>
                <span style='animation:spin 1s linear infinite;display:inline-block'>⏳</span> Perplexity analizuje... (może potrwać ~30 sek)
              </div>
              {cached_html}
          </div>
        </div>"""

    no_data_banner = ""
    if not has_trendy:
        no_data_banner = "<div style='background:rgba(251,191,36,0.1);border:1px solid rgba(251,191,36,0.3);border-radius:10px;padding:12px;font-size:0.85rem;color:#f59e0b;margin-bottom:20px'>⚠️ Brak danych — uruchom <code style='background:#1e1e2e;padding:2px 6px;border-radius:4px'>python analyze_trends.py</code></div>"

    page = f"""<!DOCTYPE html>
<html lang='pl'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>TOP Okazje {miesiac} - {get_config_cached("brand_name", "AKCES HUB")}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0a0f;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:15px;max-width:900px;margin:0 auto;padding-bottom:60px}}
</style>
</head>
<body>
<a href='/analityka' style='color:#64748b;text-decoration:none;font-size:0.9rem;display:inline-block;margin-bottom:20px'>← Powrót</a>
<h1 style='font-size:1.6rem;margin-bottom:5px'>🔥 TOP Okazje</h1>
<p style='color:#64748b;font-size:0.85rem;margin-bottom:20px'>Miesiąc: <strong>{miesiac}</strong> · Ostatnia analiza: {ostatnia_analiza}</p>
{no_data_banner}

<div style='display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px'>
  <div style='background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center'>
    <div style='font-size:1.8rem;font-weight:700;color:#f59e0b'>{len(okazje_list)}</div>
    <div style='color:#64748b;font-size:0.75rem'>OKAZJI (score≥6)</div>
  </div>
  <div style='background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center'>
    <div style='font-size:1.8rem;font-weight:700;color:#3b82f6'>{len(wszystkie_list)}</div>
    <div style='color:#64748b;font-size:0.75rem'>PRODUKTÓW ZBADANYCH</div>
  </div>
  <div style='background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center'>
    <div style='font-size:1.8rem;font-weight:700;color:#22c55e'>{'✓' if has_perplexity else '✗'}</div>
    <div style='color:#64748b;font-size:0.75rem'>PERPLEXITY API</div>
  </div>
</div>

{live_scraper_section}
{perplexity_section}

<div style='margin-bottom:20px'>
  <h2 style='font-size:1.1rem;margin-bottom:12px;color:#f59e0b'>🏆 Najlepsze okazje (score ≥ 6)</h2>
  {okazje_html}
</div>

<div>
  <h2 style='font-size:1.1rem;margin-bottom:12px;color:#94a3b8'>📊 Wszystkie produkty (top 50)</h2>
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
          document.getElementById('loading-' + loadingParam).innerHTML = '❌ Błąd Perplexity — sprawdź klucz API i spróbuj ponownie';
        }}
      }});
  }}, 3000);
  // Pokaż loader od razu
  var loader = document.getElementById('loading-' + loadingParam);
  if(loader) loader.style.display = 'block';
  var btn = document.getElementById('btn-' + loadingParam);
  if(btn) {{ btn.disabled = true; btn.style.opacity = '0.5'; }}
}}
</script>
</body></html>"""

    return page



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
        # Nowa struktura: /products/new ma najnowsze palety
        # HTML karty produktu zawiera:
        #   <h3 class="product-name"><a href="/product/{id}-{slug}">{nazwa}</a></h3>
        #   <ins class="new-price">{cena} zl</ins>
        #   <div class="product-cat"><a href="...">{kategoria}</a></div>
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
                # Znajdź karty produktów - każda karta ma link, nazwę i cenę
                # Pattern: <h3 class="product-name"><a href="/product/{id}-{slug}">{name}</a></h3>
                # potem gdzieś: <ins class="new-price">{price} zl</ins>
                # Wyciągnij bloki kart produktowych
                cards = _re.findall(
                    r'<h3\s+class="product-name">\s*<a\s+href="(/product/(\d+)-([^"]+))"[^>]*>\s*(.*?)\s*</a>\s*</h3>.*?<ins\s+class="new-price">\s*([\d.,]+)\s*z[lł]\s*</ins>',
                    html, _re.DOTALL | _re.IGNORECASE
                )
                for href, pid, slug, name, price in cards:
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    title = name.strip() if name.strip() else slug.replace('-', ' ').title()
                    # Wyczyść HTML z nazwy
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
        # Jeśli regex nie złapał (inna struktura HTML) - fallback: proste linki
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
        # Jeśli znaleźliśmy produkty - zwróć; jeśli nie - podaj kategorie
        if products:
            return jsonify({'ok': True, 'products': products[:35], 'total': len(products), 'source': 'html_new'})
        # Fallback: zwróć kategorie jako linki
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
        # Pobierz kilka widoków: popularne, najwięcej ofert, najtańsze
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
        # Kurs GBP→PLN (aktualizuj raz na jakiś czas)
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

            # Przelicz walutę na PLN
            _orig_currency = (item.get('currency', '') or '').upper()
            if _orig_currency == 'GBP':
                rrp = round(rrp * _GBP_PLN, 2)
                bid = round(bid * _GBP_PLN, 2)
            elif _orig_currency == 'EUR':
                rrp = round(rrp * _EUR_PLN, 2)
                bid = round(bid * _EUR_PLN, 2)

            # Konwersja UTC → Warszawa (CET +1 / CEST +2)
            _eat_raw = item.get('end_at', '')
            if _eat_raw:
                try:
                    from datetime import datetime as _dtj, timedelta as _tdj
                    # Parse: "2026-03-06T08:00:00.000000Z"
                    _clean = _eat_raw.split('.')[0].replace('Z', '').replace('T', ' ')
                    _utc_dt = _dtj.strptime(_clean, '%Y-%m-%d %H:%M:%S')
                    _y = _utc_dt.year
                    # Ostatnia niedziela marca (start CEST)
                    _mar31 = _dtj(_y, 3, 31)
                    _last_sun_mar = _dtj(_y, 3, 31 - (_mar31.weekday() + 1) % 7, 2)
                    # Ostatnia niedziela października (koniec CEST)
                    _oct31 = _dtj(_y, 10, 31)
                    _last_sun_oct = _dtj(_y, 10, 31 - (_oct31.weekday() + 1) % 7, 3)
                    _hours = 2 if _last_sun_mar <= _utc_dt < _last_sun_oct else 1
                    _local = _utc_dt + _tdj(hours=_hours)
                    end_at = _local.strftime('%Y-%m-%d %H:%M')
                except Exception as _te:
                    end_at = _eat_raw[:16].replace('T', ' ')
            else:
                end_at = ''
            currency = 'PLN'  # Zawsze PLN po przeliczeniu
            # Obrazek
            manifest = item.get('manifest', {})
            img = ''
            if manifest.get('product_first_image'):
                img = manifest['product_first_image'].get('product_image_thumbnail_url', '')
            url = f'https://jobalots.com/pl/products/{sku}?currency=pln'
            sort_tag = item.get('_sort_tag', '')
            tag_label = {'popularity': '🔥', 'most_bids': '📈', 'bid_low': '💰'}.get(sort_tag, '')
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
            'note': f'🔥 Popularne · 📈 Dużo ofert · 💰 Najtańsze ({total} palet łącznie)'})
    except Exception as e:
        # Fallback: kategorie
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
        # Prawdziwe dane sprzedaży — LEFT JOIN bo większość nie ma produkt_id
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
              AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
              AND s.data_sprzedazy >= date('now', '-60 days')
            GROUP BY COALESCE(p.nazwa, s.nazwa)
            ORDER BY przychod DESC
            LIMIT 15
        """).fetchall()

        # Produkty na stanie (niesprzedane) z ceną
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

        # Kategorie z największym przychodem (bez wymagania produkt_id)
        kategorie = conn.execute("""
            SELECT COALESCE(p.kategoria, 'inne') as kategoria,
                   COUNT(*) as cnt, SUM(s.cena) as przychod
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
              AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
              AND s.data_sprzedazy >= date('now', '-60 days')
            GROUP BY kategoria ORDER BY przychod DESC LIMIT 5
        """).fetchall()
    except Exception as _e:
        print(f"BLAD okazje analyze: {_e}")
        top, na_stanie, kategorie = [], [], []

    sprzedane_txt = "\n".join(
        f"{i}. {r['nazwa'][:60]} [{r['kategoria'] or 'inne'}] — sprzedano {r['ilosc_sprzedanych']}x za {r['cena_sprzedazy']:.0f} zl, przychod {r['przychod']:.0f} zl, dostawca: {r['dostawca'] or 'własny'}"
        for i, r in enumerate(top, 1)) if top else "Brak danych sprzedażowych"

    stanie_txt = "\n".join(
        f"- {r['nazwa'][:60]} [{r['kategoria'] or ''}] — {r['ilosc']} szt na stanie, cena {r['cena']:.0f} zl"
        for r in na_stanie) if na_stanie else "Brak produktów na stanie"

    kat_txt = ", ".join(f"{r['kategoria']} ({r['przychod']:.0f} zl)" for r in kategorie) if kategorie else "mix"

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
        # Co się sprzedaje — LEFT JOIN bo większość nie ma produkt_id
        top_sprzedaz = conn.execute("""
            SELECT COALESCE(p.nazwa, s.nazwa, 'Produkt') as nazwa,
                   COALESCE(p.kategoria, 'inne') as kategoria,
                   s.cena, COALESCE(pal.dostawca, p.dostawca) as dostawca
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            LEFT JOIN palety pal ON p.paleta_id = pal.id
            WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
              AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
              AND s.data_sprzedazy >= date('now', '-60 days')
            ORDER BY s.cena DESC
            LIMIT 10
        """).fetchall()

        # Kategorie z najlepszym przychodem
        top_kat = conn.execute("""
            SELECT COALESCE(p.kategoria, 'inne') as kategoria,
                   COUNT(*) as cnt, SUM(s.cena) as przychod,
                   AVG(s.cena) as sr_cena
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
              AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
              AND s.data_sprzedazy >= date('now', '-60 days')
            GROUP BY kategoria ORDER BY przychod DESC LIMIT 5
        """).fetchall()

        # Palety z wynikami
        palety_roi = conn.execute("""
            SELECT pal.nazwa, pal.dostawca, pal.cena_zakupu, pal.ilosc_produktow,
                   COALESCE(SUM(CASE WHEN s.id IS NOT NULL THEN 1 ELSE 0 END), 0) as sprzedanych,
                   COALESCE(SUM(s.cena), 0) as przychod_z_palety
            FROM palety pal
            LEFT JOIN produkty p ON p.paleta_id = pal.id
            LEFT JOIN sprzedaze s ON s.produkt_id = p.id
              AND s.status NOT IN ('zwrot','anulowane','anulowana')
              AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
            GROUP BY pal.id
            ORDER BY pal.data_zakupu DESC
            LIMIT 8
        """).fetchall()
    except Exception as _e:
        print(f"BLAD okazje szukaj: {_e}")
        top_sprzedaz, top_kat, palety_roi = [], [], []

    sprzedaz_txt = "\n".join(
        f"- {r['nazwa'][:50]} [{r['kategoria'] or ''}] — {r['cena']:.0f} zl, dostawca: {r['dostawca'] or 'własny'}"
        for r in top_sprzedaz) if top_sprzedaz else "brak danych"

    kat_txt = "\n".join(
        f"- {r['kategoria']}: {r['cnt']}x sprzedanych, {r['przychod']:.0f} zl przychód, śr. {r['sr_cena']:.0f} zl/szt"
        for r in top_kat) if top_kat else "elektronika, AGD, sport"

    palety_txt = "\n".join(
        f"- {r['nazwa']} ({r['dostawca']}): kupiono za {r['cena_zakupu']:.0f} zl ({r['ilosc_produktow']} szt), sprzedano {r['sprzedanych']}x = {(r['przychod_z_palety'] or 0):.0f} zl"
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
                # Karty produktów: <h3 class="product-name"><a href="/product/{id}-{slug}">{nazwa}</a></h3>
                # potem: <ins class="new-price">{cena} zl</ins>
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
                # Fallback: proste linki jeśli regex nie złapał
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
            # Przelicz na PLN
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
    # Stare rekordy mają sprzedano_offline > 0 i przychod_offline > 0
    # ale NIE mają rekordu w sprzedaze (sprzedane przed nowym systemem)
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
            # Wyzeruj przychod_offline żeby nie duplikować (dane są już w sprzedaze)
            ids = [r['id'] for r in stare]
            placeholders = ','.join('?' * len(ids))
            conn.execute(f"UPDATE produkty SET przychod_offline = 0 WHERE id IN ({placeholders})", ids)
            conn.commit()
            print(f"✅ Migracja offline: przeniesiono {len(stare)} produktów do sprzedaze, wyzerowano przychod_offline")
    except Exception as _e:
        print(f"⚠️ Migracja offline: {_e}")

    # Napraw rekordy offline w sprzedaze które mają cena=0
    # (zostały dodane przez poprzednią wersję kodu z błędem)
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
            print(f"✅ Naprawiono ceny offline: {naprawione} rekordów")
    except Exception as _e:
        print(f"⚠️ Naprawa cen offline: {_e}")

    # Backfill data_syncu: dla rekordów bez powiązanego produktu/oferty
    # użyj data_sprzedazy jako przybliżonej daty dodania do systemu
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
    # (dla produktów dodanych przez import bez daty)
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

    # Backfill produkty.data_dodania z daty zakupu palety (pre-Paletomat)
    try:
        conn.execute("""
            UPDATE produkty SET data_dodania = (
                SELECT p2.data_zakupu FROM palety p2
                WHERE p2.id = produkty.paleta_id
                  AND p2.data_zakupu IS NOT NULL
            )
            WHERE (data_dodania IS NULL OR data_dodania = '')
              AND paleta_id IS NOT NULL
        """)
        conn.commit()
    except:
        pass

    # Backfill: uzupełnij s.nazwa z oferty.tytul dla starych rekordów
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
    # Backfill3: dla tych co mają oferta_id ale brak tytułu w oferty — użyj allegro_id jako nazwy
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
    # Bierzemy produkty ze statusem sprzedany LUB z data_sprzedazy
    # Łączymy z ofertami (opcjonalnie) żeby mieć data_wystawienia na Allegro
    # Fallback: data_dodania = kiedy produkt trafił do systemu
    # Główne źródło: tabela sprzedaze (ma daty z Allegro) + produkty (dla dat dodania)
    # JOIN przez oferta_id -> oferty -> produkt_id LUB bezpośrednio przez produkt_id
    dane_od_wystawienia = conn.execute("""
        SELECT
            COALESCE(NULLIF(p.nazwa,''), NULLIF(s.nazwa,''), CASE WHEN s.allegro_order_id IS NOT NULL THEN 'Zamówienie ' || SUBSTR(s.allegro_order_id,1,8) ELSE 'Brak nazwy' END) as nazwa,
            s.cena,
            s.data_sprzedazy,
            COALESCE(
                o.data_wystawienia,
                o2.data_wystawienia,
                p.data_dodania,
                (SELECT pal.data_zakupu FROM palety pal WHERE pal.id = p.paleta_id)) as data_od,
            p.kategoria, p.dostawca,
            CASE
              WHEN COALESCE(o.data_wystawienia, o2.data_wystawienia, p.data_dodania,
                            (SELECT pal.data_zakupu FROM palety pal WHERE pal.id = p.paleta_id)) IS NOT NULL
              THEN MAX(0, (julianday(REPLACE(SUBSTR(s.data_sprzedazy,1,19),'T',' '))
                   - julianday(REPLACE(SUBSTR(
                       COALESCE(o.data_wystawienia, o2.data_wystawienia, p.data_dodania,
                                (SELECT pal.data_zakupu FROM palety pal WHERE pal.id = p.paleta_id)),1,19),'T',' '))))
              ELSE NULL
            END as dni_od_wystawienia
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        LEFT JOIN oferty o ON s.oferta_id = o.id
        LEFT JOIN oferty o2 ON o2.produkt_id = s.produkt_id AND s.oferta_id IS NULL
        WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
          AND s.data_sprzedazy IS NOT NULL AND s.data_sprzedazy != ''
          AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
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
          AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
          AND pal.data_zakupu IS NOT NULL
          AND (julianday(REPLACE(SUBSTR(s.data_sprzedazy,1,19),'T',' '))
               - julianday(pal.data_zakupu)) >= 0
        ORDER BY dni_od_zakupu ASC
    """).fetchall()


    def fmt_dni(d):
        if d is None: return '?'
        d = float(d)
        if d < 0.04: return '<1h'  # mniej niż godzina
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

    # Filtruj rekordy z datami (nie-NULL) do rankingów
    _dane_z_datami = [r for r in dane_od_wystawienia if r['dni_od_wystawienia'] is not None]

    # Deduplikacja — każdy produkt tylko raz (najszybszy czas)
    _seen_fast = set()
    najszybsze = []
    for r in _dane_z_datami:
        n = r['nazwa']
        if n not in _seen_fast:
            _seen_fast.add(n)
            najszybsze.append(r)
            if len(najszybsze) >= 10:
                break

    # Deduplikacja — każdy produkt tylko raz (najwolniejszy czas)
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
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.4rem;font-weight:700;color:#22c55e">{stat_w['srednia']:.1f}</div>
                <div style="font-size:0.65rem;color:#64748b">ŚR. DNI</div>
            </div>
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.4rem;font-weight:700;color:#3b82f6">{stat_w['mediana']:.1f}</div>
                <div style="font-size:0.65rem;color:#64748b">MEDIANA</div>
            </div>
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.2rem;font-weight:700;color:#f59e0b">{fmt_dni(stat_w['min'])}</div>
                <div style="font-size:0.65rem;color:#64748b">NAJSZYBCIEJ</div>
            </div>
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.1rem;font-weight:700;color:#ef4444">{fmt_dni(stat_w['max'])}</div>
                <div style="font-size:0.65rem;color:#64748b">NAJWOLNIEJ</div>
            </div>
        </div>
        <div style="margin-top:10px;font-size:0.75rem;color:#94a3b8;text-align:center">{stat_w['cnt']} sprzedanych produktów</div>
        <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">
            <span style="background:#22c55e22;color:#22c55e;padding:3px 8px;border-radius:6px;font-size:0.7rem">⚡ {stat_w['w_24h']} w 24h</span>
            <span style="background:#3b82f622;color:#3b82f6;padding:3px 8px;border-radius:6px;font-size:0.7rem">📅 {stat_w['w_7dni']} w tyg</span>
            <span style="background:#f59e0b22;color:#f59e0b;padding:3px 8px;border-radius:6px;font-size:0.7rem">📆 {stat_w['w_30dni']} w mies</span>
            <span style="background:#ef444422;color:#ef4444;padding:3px 8px;border-radius:6px;font-size:0.7rem">🐢 {stat_w['pow_30dni']} pow. 30 dni</span>
        </div>"""
    else:
        info = f' ({cnt_bez_daty} szt. sprzedanych bez daty — synchronizuj z Allegro)' if cnt_bez_daty else ''
        karta_w = f'<div style="color:#64748b;font-size:0.85rem;padding:10px">Brak danych z datą sprzedaży.<br><span style="color:#f59e0b;font-size:0.8rem">{cnt_bez_daty} produktów sprzedanych bez daty — synchronizuj z Allegro lub kliknij -1 szt (od v32 ustawia datę)</span></div>'

    karta_z = ""
    if stat_z:
        karta_z = f"""
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.4rem;font-weight:700;color:#3b82f6">{stat_z['srednia']:.1f}</div>
                <div style="font-size:0.65rem;color:#64748b">ŚR. DNI</div>
            </div>
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.4rem;font-weight:700;color:#22c55e">{stat_z['mediana']:.1f}</div>
                <div style="font-size:0.65rem;color:#64748b">MEDIANA</div>
            </div>
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.1rem;font-weight:700;color:#f59e0b">{fmt_dni(stat_z['min'])}</div>
                <div style="font-size:0.65rem;color:#64748b">NAJSZYBCIEJ</div>
            </div>
            <div style="text-align:center;background:rgba(0,0,0,0.3);border-radius:8px;padding:8px">
                <div style="font-size:1.1rem;font-weight:700;color:#ef4444">{fmt_dni(stat_z['max'])}</div>
                <div style="font-size:0.65rem;color:#64748b">NAJWOLNIEJ</div>
            </div>
        </div>
        <div style="margin-top:10px;font-size:0.75rem;color:#94a3b8;text-align:center">{stat_z['cnt']} sprzedaży z palet</div>
        <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">
            <span style="background:#22c55e22;color:#22c55e;padding:3px 8px;border-radius:6px;font-size:0.7rem">7 dni: {stat_z['w_7dni']}</span>
            <span style="background:#3b82f622;color:#3b82f6;padding:3px 8px;border-radius:6px;font-size:0.7rem">30 dni: {stat_z['w_30dni']}</span>
            <span style="background:#f59e0b22;color:#f59e0b;padding:3px 8px;border-radius:6px;font-size:0.7rem">60 dni: {stat_z['w_60dni']}</span>
            <span style="background:#ef444422;color:#ef4444;padding:3px 8px;border-radius:6px;font-size:0.7rem">60+: {stat_z['pow_60dni']}</span>
        </div>"""
    else:
        karta_z = f'<div style="color:#64748b;font-size:0.85rem;padding:10px">Brak danych z datą sprzedaży.<br><span style="color:#f59e0b;font-size:0.8rem">Produkty muszą być powiązane z paletą i mieć datę sprzedaży z Allegro lub -1 szt</span></div>'

    dostawcy_html = ""
    if dostawcy_wyniki:
        rows = ""
        for i, d in enumerate(dostawcy_wyniki[:8]):
            sep = "border-bottom:1px solid #1e1e2e;" if i < len(dostawcy_wyniki[:8])-1 else ""
            clr = "#22c55e" if d['srednia'] <= 14 else "#f59e0b" if d['srednia'] <= 30 else "#ef4444"
            rows += f'<div style="display:flex;align-items:center;gap:10px;padding:8px 0;{sep}"><div style="flex:1;font-size:0.85rem;font-weight:600">{d["dostawca"]}</div><div style="font-size:0.8rem;color:#64748b">{d["cnt"]} szt</div><div style="font-weight:700;color:{clr}">{d["srednia"]:.1f} dni</div></div>'
        dostawcy_html = f'<div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:16px;margin-bottom:15px"><div style="font-weight:700;color:#f59e0b;margin-bottom:12px">🏭 Dostawcy — średni czas sprzedaży od zakupu palety</div>{rows}</div>'

    def item_row_w(r, kolor, i, total):
        sep = "border-bottom:1px solid #1e1e2e;" if i < total-1 else ""
        name = (r['nazwa'] or 'Brak nazwy')[:50]
        cena = float(r['cena'] or 0)
        return f'<div style="display:flex;align-items:center;gap:8px;padding:6px 0;{sep}"><div style="font-weight:700;color:{kolor};min-width:65px;font-size:0.85rem">{fmt_dni(r["dni_od_wystawienia"])}</div><div style="flex:1;font-size:0.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{name}</div><div style="font-size:0.8rem;color:#64748b;white-space:nowrap">{cena:.0f} zł</div></div>'

    szybkie_html = ""
    if najszybsze:
        rows = "".join(item_row_w(r, "#22c55e", i, len(najszybsze)) for i, r in enumerate(najszybsze))
        szybkie_html = f'<div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:16px;margin-bottom:15px"><div style="font-weight:700;color:#22c55e;margin-bottom:12px">⚡ Najszybciej sprzedane (od dodania do systemu)</div>{rows}</div>'

    wolne_html = ""
    if najwolniejsze:
        rows = "".join(item_row_w(r, "#ef4444", i, len(najwolniejsze)) for i, r in enumerate(najwolniejsze))
        wolne_html = f'<div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:16px;margin-bottom:15px"><div style="font-weight:700;color:#ef4444;margin-bottom:12px">🐢 Najwolniej sprzedane (od dodania do systemu)</div>{rows}</div>'

    chart_html = ""
    if dw:
        chart_html = f"""
        <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:16px;margin-bottom:15px">
            <div style="font-weight:700;color:#94a3b8;margin-bottom:12px">📊 Rozkład czasu sprzedaży (od dodania do systemu)</div>
            <canvas id="histChart" style="max-height:180px"></canvas>
        </div>
        <script src="https://cdn.jsdelivr.net/npm/chart.js@3/dist/chart.min.js"></script>
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

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Czas Sprzedaży</title>
    <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0a0a0f;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:20px;max-width:900px;margin:0 auto}}
    h1{{text-align:center;color:#3b82f6;font-size:1.6rem;margin-bottom:5px}}
    .sub{{text-align:center;color:#64748b;font-size:0.8rem;margin-bottom:20px}}
    .back{{color:#64748b;font-size:0.85rem;text-decoration:none;display:inline-block;margin-bottom:15px}}
    .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:15px;margin-bottom:15px}}
    .card{{background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:16px}}
    .card-title{{font-weight:700;margin-bottom:12px;display:flex;align-items:center;gap:8px}}
    @media(max-width:600px){{.grid2{{grid-template-columns:1fr}}}}
    </style></head><body>
    <h1>⏱️ CZAS SPRZEDAŻY</h1>
    <div class="sub">Od dodania do systemu i zakupu palety do sprzedaży</div>
    <a href="/analityka" class="back">← Wróć do analityki</a>
    <div class="grid2">
        <div class="card" style="border-color:rgba(34,197,94,0.4)">
            <div class="card-title" style="color:#22c55e">📋 Od DODANIA DO SYSTEMU</div>
            {karta_w}
        </div>
        <div class="card" style="border-color:rgba(59,130,246,0.4)">
            <div class="card-title" style="color:#3b82f6">🚚 Od ZAKUPU PALETY</div>
            {karta_z}
        </div>
    </div>
    {chart_html}
    {dostawcy_html}
    {szybkie_html}
    {wolne_html}
    <div style='background:#12121a;border:1px solid #3b82f640;border-radius:12px;padding:14px;margin:0 0 16px 0;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap'>
      <div>
        <div style='color:#3b82f6;font-weight:600;font-size:0.85rem'>📅 Daty wystawienia ofert</div>
        <div style='color:#64748b;font-size:0.75rem;margin-top:3px'>Znaki <strong style='color:#f59e0b'>?</strong> = brak daty wystawienia w bazie. Zsynchronizuj oferty z Allegro żeby uzupełnić daty.</div>
      </div>
      <a href='/allegro/sync-oferty-daty' style='background:#3b82f620;color:#3b82f6;border:1px solid #3b82f640;border-radius:8px;padding:8px 16px;font-size:0.8rem;font-weight:600;text-decoration:none;white-space:nowrap'>🔄 Odśwież daty z Allegro</a>
    </div>
    <a href="/analityka" class="btn" style="display:block;text-align:center;margin-bottom:80px;padding:12px;background:#1e1e2e;border-radius:10px;color:#fff;text-decoration:none">← Powrót do analityki</a>
    </div>
    """
    return html



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

