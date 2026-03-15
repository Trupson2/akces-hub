"""
ANALYTICS MODULE - Dashboard KPI & Kalkulator opłacalności palety
v1.0 - Styczeń 2026
"""

from flask import Blueprint, render_template_string, request, jsonify
from datetime import datetime, timedelta
from .database import get_db
import json

analytics_bp = Blueprint('analytics', __name__)

# ============================================================
# STYLE CSS DLA ANALYTICS
# ============================================================
ANALYTICS_CSS = '''
<style>
:root {
    --bg: #0a0a0f;
    --card: #12121a;
    --border: #1e1e2e;
    --text: #e2e8f0;
    --muted: #64748b;
    --green: #22c55e;
    --red: #ef4444;
    --blue: #3b82f6;
    --purple: #8b5cf6;
    --yellow: #eab308;
    --orange: #f97316;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { 
    font-family: -apple-system, system-ui, sans-serif; 
    background: var(--bg); 
    color: var(--text);
    padding: 15px;
    max-width: 1400px;
    margin: 0 auto;
}
.header { text-align: center; padding: 20px 0; }
.header h1 { font-size: 1.8rem; margin-bottom: 5px; }
.header small { color: var(--muted); }

/* KPI Cards Grid */
.kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 15px;
    margin-bottom: 20px;
}
.kpi-card {
    background: var(--card);
    border-radius: 12px;
    padding: 20px;
    text-align: center;
    border: 1px solid var(--border);
}
.kpi-value {
    font-size: 2rem;
    font-weight: 700;
    margin-bottom: 5px;
}
.kpi-label {
    color: var(--muted);
    font-size: 0.85rem;
}
.kpi-trend {
    font-size: 0.8rem;
    margin-top: 8px;
    padding: 4px 8px;
    border-radius: 4px;
    display: inline-block;
}
.kpi-trend.up { background: rgba(34,197,94,0.2); color: var(--green); }
.kpi-trend.down { background: rgba(239,68,68,0.2); color: var(--red); }
.kpi-trend.neutral { background: rgba(100,116,139,0.2); color: var(--muted); }

/* Charts */
.chart-container {
    background: var(--card);
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 20px;
    border: 1px solid var(--border);
}
.chart-title {
    font-size: 1.1rem;
    font-weight: 600;
    margin-bottom: 15px;
    display: flex;
    align-items: center;
    gap: 10px;
}
.chart-bar {
    display: flex;
    align-items: center;
    margin-bottom: 12px;
}
.chart-bar-label {
    width: 80px;
    font-size: 0.85rem;
    color: var(--muted);
}
.chart-bar-track {
    flex: 1;
    height: 24px;
    background: var(--border);
    border-radius: 4px;
    overflow: hidden;
    margin: 0 10px;
}
.chart-bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.5s ease;
}
.chart-bar-value {
    width: 80px;
    text-align: right;
    font-weight: 600;
}

/* Alerts */
.alert-box {
    background: var(--card);
    border-radius: 12px;
    padding: 15px;
    margin-bottom: 20px;
    border-left: 4px solid var(--yellow);
}
.alert-box.success { border-color: var(--green); }
.alert-box.danger { border-color: var(--red); }
.alert-box.info { border-color: var(--blue); }
.alert-title {
    font-weight: 600;
    margin-bottom: 5px;
}
.alert-text {
    color: var(--muted);
    font-size: 0.9rem;
}

/* Kalkulator */
.calc-card {
    background: var(--card);
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 20px;
    border: 1px solid var(--border);
}
.calc-title {
    font-size: 1.2rem;
    font-weight: 600;
    margin-bottom: 15px;
}
.form-group {
    margin-bottom: 15px;
}
.form-group label {
    display: block;
    margin-bottom: 5px;
    color: var(--muted);
    font-size: 0.9rem;
}
.form-control {
    width: 100%;
    padding: 12px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    font-size: 1rem;
}
.form-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 15px;
}
.btn {
    padding: 12px 24px;
    border: none;
    border-radius: 8px;
    font-size: 1rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
}
.btn-primary { background: var(--blue); color: white; }
.btn-success { background: var(--green); color: white; }
.btn-primary:hover { opacity: 0.9; }

/* Results */
.result-card {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    border-radius: 12px;
    padding: 20px;
    margin-top: 20px;
    border: 1px solid var(--blue);
}
.result-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 15px;
}
.result-header h3 {
    font-size: 1.3rem;
}
.result-badge {
    padding: 6px 12px;
    border-radius: 20px;
    font-weight: 600;
    font-size: 0.9rem;
}
.result-badge.excellent { background: var(--green); }
.result-badge.good { background: var(--blue); }
.result-badge.average { background: var(--yellow); color: #000; }
.result-badge.poor { background: var(--red); }

.result-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 15px;
}
.result-item {
    text-align: center;
    padding: 15px;
    background: rgba(0,0,0,0.3);
    border-radius: 8px;
}
.result-value {
    font-size: 1.5rem;
    font-weight: 700;
}
.result-label {
    font-size: 0.8rem;
    color: var(--muted);
    margin-top: 5px;
}

.back-link {
    display: inline-block;
    margin-top: 20px;
    color: var(--muted);
    text-decoration: none;
}
.back-link:hover { color: var(--text); }

/* Top lists */
.top-list {
    background: var(--card);
    border-radius: 12px;
    padding: 20px;
    border: 1px solid var(--border);
}
.top-item {
    display: flex;
    align-items: center;
    padding: 12px 0;
    border-bottom: 1px solid var(--border);
}
.top-item:last-child { border-bottom: none; }
.top-rank {
    width: 30px;
    font-weight: 700;
    color: var(--muted);
}
.top-rank.gold { color: #ffd700; }
.top-rank.silver { color: #c0c0c0; }
.top-rank.bronze { color: #cd7f32; }
.top-info { flex: 1; }
.top-name { font-weight: 500; }
.top-meta { font-size: 0.8rem; color: var(--muted); }
.top-value { font-weight: 700; }
</style>
'''

# ============================================================
# DASHBOARD KPI
# ============================================================
@analytics_bp.route('/dashboard')
def dashboard_kpi():
    """Zaawansowany Dashboard KPI"""
    conn = get_db()
    
    today = datetime.now()
    today_str = today.strftime('%Y-%m-%d')
    yesterday_str = (today - timedelta(days=1)).strftime('%Y-%m-%d')
    week_ago = (today - timedelta(days=7)).strftime('%Y-%m-%d')
    month_start = today.strftime('%Y-%m-01')
    last_month_start = (today.replace(day=1) - timedelta(days=1)).strftime('%Y-%m-01')
    last_month_end = (today.replace(day=1) - timedelta(days=1)).strftime('%Y-%m-%d')
    
    # === KPI DZIŚ ===
    dzis = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc), 0) as suma
        FROM sprzedaze WHERE date(data_sprzedazy) = ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
        AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''', (today_str,)).fetchone()
    
    # === KPI WCZORAJ (do porównania) ===
    wczoraj = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc), 0) as suma
        FROM sprzedaze WHERE date(data_sprzedazy) = ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
        AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''', (yesterday_str,)).fetchone()
    
    # === KPI TEN TYDZIEŃ ===
    tydzien = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc), 0) as suma
        FROM sprzedaze WHERE date(data_sprzedazy) >= ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
        AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''', (week_ago,)).fetchone()
    
    # === KPI POPRZEDNI TYDZIEŃ ===
    prev_week_start = (today - timedelta(days=14)).strftime('%Y-%m-%d')
    prev_week_end = (today - timedelta(days=7)).strftime('%Y-%m-%d')
    poprzedni_tydzien = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc), 0) as suma
        FROM sprzedaze WHERE date(data_sprzedazy) >= ? AND date(data_sprzedazy) < ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
        AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''', (prev_week_start, prev_week_end)).fetchone()
    
    # === KPI TEN MIESIĄC ===
    miesiac = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc), 0) as suma
        FROM sprzedaze WHERE date(data_sprzedazy) >= ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
        AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''', (month_start,)).fetchone()
    
    # === KPI POPRZEDNI MIESIĄC ===
    poprzedni_miesiac = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc), 0) as suma
        FROM sprzedaze WHERE date(data_sprzedazy) >= ? AND date(data_sprzedazy) <= ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
        AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''', (last_month_start, last_month_end)).fetchone()
    
    # === MAGAZYN ===
    magazyn = conn.execute('''
        SELECT COUNT(*) as produkty, COALESCE(SUM(ilosc), 0) as sztuki,
               COALESCE(SUM(cena_brutto), 0) as wartosc_zakupu,
               COALESCE(SUM(cena_allegro * ilosc), 0) as wartosc_sprzedazy
        FROM produkty WHERE status IN ('magazyn', 'wystawiony')
    ''').fetchone()
    
    # === ROI OGÓLNE ===
    # Przychód z tego miesiąca (tylko Allegro — offline to historyczne korekty)
    przychod_data = conn.execute('''
        SELECT COALESCE(SUM(s.cena * s.ilosc), 0) as przychod
        FROM sprzedaze s
        WHERE date(s.data_sprzedazy) >= ?
        AND s.status NOT IN ('zwrot', 'anulowane', 'anulowana')
        AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
    ''', (month_start,)).fetchone()

    przychod = przychod_data['przychod'] or 0

    # Koszty = średni koszt jednostkowy ze WSZYSTKICH palet * sprzedane szt w miesiącu
    # Dlaczego: większość sprzedaży z Allegro nie ma produkt_id (sync), nie da się
    # powiązać bezpośrednio z paletą. Średni koszt daje najbardziej realistyczny wynik.
    avg_cost_data = conn.execute('''
        SELECT
            SUM(pal.cena_zakupu) as total_koszt,
            SUM(
                COALESCE((SELECT SUM(CASE WHEN pr.status NOT IN ('sprzedany','wyslany') THEN pr.ilosc ELSE 0 END)
                          FROM produkty pr WHERE pr.paleta_id = pal.id), 0)
                + COALESCE((SELECT SUM(sp.ilosc) FROM sprzedaze sp
                            JOIN produkty pp ON sp.produkt_id = pp.id
                            WHERE pp.paleta_id = pal.id
                            AND sp.status NOT IN ('zwrot','anulowane','anulowana')), 0)
            ) as total_items
        FROM palety pal
        WHERE pal.cena_zakupu > 0
    ''').fetchone()

    total_palet_koszt = avg_cost_data['total_koszt'] or 0
    total_palet_items = avg_cost_data['total_items'] or 1
    avg_cost_per_item = total_palet_koszt / total_palet_items if total_palet_items > 0 else 0

    # Ile sztuk sprzedano na Allegro w tym miesiącu (bez offline)
    sold_this_month = conn.execute('''
        SELECT COALESCE(SUM(ilosc), 0) as s FROM sprzedaze
        WHERE date(data_sprzedazy) >= ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
        AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''', (month_start,)).fetchone()['s'] or 0

    koszty = avg_cost_per_item * sold_this_month
    prowizja = przychod * 0.11  # prowizja Allegro ~11%
    zysk_miesiac = przychod - koszty - prowizja
    roi_miesiac = (zysk_miesiac / koszty * 100) if koszty > 0 else 0
    
    # === SPRZEDAŻ PO DNIACH (ostatnie 7 dni) ===
    sprzedaz_dni = conn.execute('''
        SELECT date(data_sprzedazy) as dzien, 
               COUNT(*) as cnt, 
               COALESCE(SUM(cena * ilosc), 0) as suma
        FROM sprzedaze 
        WHERE date(data_sprzedazy) >= ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
        AND (kupujacy IS NULL OR kupujacy != 'offline')
        GROUP BY date(data_sprzedazy)
        ORDER BY dzien DESC
        LIMIT 7
    ''', (week_ago,)).fetchall()
    
    # === TOP 5 PRODUKTÓW ===
    top_produkty = conn.execute('''
        SELECT 
            CASE 
                WHEN s.nazwa IS NOT NULL AND s.nazwa != '' AND s.nazwa != 'Produkt' THEN SUBSTR(s.nazwa, 1, 50)
                WHEN o.tytul IS NOT NULL AND o.tytul != '' THEN SUBSTR(o.tytul, 1, 50)
                WHEN p.nazwa IS NOT NULL AND p.nazwa != '' THEN p.nazwa
                ELSE 'Produkt #' || s.id
            END as produkt_nazwa,
            COUNT(*) as sprzedane, 
            SUM(s.cena * s.ilosc) as wartosc
        FROM sprzedaze s
        LEFT JOIN oferty o ON s.oferta_id = o.id
        LEFT JOIN produkty p ON COALESCE(s.produkt_id, o.produkt_id) = p.id
        WHERE date(s.data_sprzedazy) >= ?
        AND s.status NOT IN ('zwrot', 'anulowane', 'anulowana')
        AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
        GROUP BY produkt_nazwa
        ORDER BY sprzedane DESC
        LIMIT 5
    ''', (month_start,)).fetchall()
    
    # === TOP 5 DOSTAWCÓW (ROI) ===
    # Podejscie: przychod z sprzedazy + koszt = suma cena_zakupu palet per dostawca
    top_dostawcy = conn.execute('''
        WITH dostawca_przychod AS (
            SELECT
                COALESCE(
                    NULLIF(pal.dostawca, ''),
                    NULLIF(p.dostawca, ''),
                    'Nieznany'
                ) as dostawca_nazwa,
                COUNT(DISTINCT s.id) as sprzedane,
                SUM(s.cena * s.ilosc) as przychod
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            LEFT JOIN palety pal ON p.paleta_id = pal.id
            WHERE date(s.data_sprzedazy) >= ?
            AND s.status NOT IN ('zwrot', 'anulowane', 'anulowana')
            GROUP BY dostawca_nazwa
            HAVING dostawca_nazwa != 'Nieznany'
        ),
        dostawca_koszt AS (
            SELECT
                COALESCE(NULLIF(dostawca, ''), 'Nieznany') as dostawca_nazwa,
                SUM(COALESCE(cena_zakupu, 0)) as koszt
            FROM palety
            WHERE cena_zakupu > 0
            GROUP BY dostawca_nazwa
        )
        SELECT
            dp.dostawca_nazwa,
            dp.sprzedane,
            dp.przychod,
            COALESCE(dk.koszt, 0) as koszty
        FROM dostawca_przychod dp
        LEFT JOIN dostawca_koszt dk ON dp.dostawca_nazwa = dk.dostawca_nazwa
        ORDER BY dp.przychod DESC
        LIMIT 5
    ''', (month_start,)).fetchall()
    
    # === STOJĄCE PRODUKTY ===
    stojace_30 = conn.execute('''
        SELECT COUNT(*) as cnt FROM produkty 
        WHERE status = 'magazyn' 
        AND date(data_dodania) <= date('now', '-30 days')
    ''').fetchone()['cnt']
    
    # === OBLICZ TRENDY ===
    def calc_trend(current, previous):
        if previous == 0:
            return 0, 'neutral'
        change = ((current - previous) / previous) * 100
        if change > 5:
            return change, 'up'
        elif change < -5:
            return change, 'down'
        return change, 'neutral'
    
    dzis_trend, dzis_trend_class = calc_trend(dzis['suma'] or 0, wczoraj['suma'] or 0)
    tydzien_trend, tydzien_trend_class = calc_trend(tydzien['suma'] or 0, poprzedni_tydzien['suma'] or 0)
    miesiac_trend, miesiac_trend_class = calc_trend(miesiac['suma'] or 0, poprzedni_miesiac['suma'] or 0)
    
    # === MAX dla wykresów ===
    max_dzien = max([d['suma'] for d in sprzedaz_dni]) if sprzedaz_dni else 1
    
    # === ALERTY ===
    alerts = []
    if stojace_30 > 10:
        alerts.append({
            'type': 'danger',
            'title': f'⚠️ {stojace_30} produktów stoi >30 dni',
            'text': 'Rozważ obniżenie cen lub promocję'
        })
    if dzis_trend < -30:
        alerts.append({
            'type': 'warning',
            'title': '📉 Spadek sprzedaży',
            'text': f'Dziś {abs(dzis_trend):.0f}% mniej niż wczoraj'
        })
    if roi_miesiac > 100:
        alerts.append({
            'type': 'success',
            'title': f'🎯 Świetny ROI: {roi_miesiac:.0f}%',
            'text': 'Tak trzymaj!'
        })
    
    # === RENDER ===
    html = f'''
<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>📊 Dashboard KPI - Akces Hub</title>
{ANALYTICS_CSS}
</head><body>

<div class="header">
    <h1>📊 DASHBOARD KPI</h1>
    <small>Aktualizacja: {today.strftime('%d.%m.%Y %H:%M')}</small>
</div>

<!-- ALERTY -->
{''.join([f'<div class="alert-box {a["type"]}"><div class="alert-title">{a["title"]}</div><div class="alert-text">{a["text"]}</div></div>' for a in alerts])}

<!-- KPI GŁÓWNE -->
<div class="kpi-grid">
    <div class="kpi-card">
        <div class="kpi-value" style="color:var(--green)">{dzis['suma'] or 0:.0f} zł</div>
        <div class="kpi-label">Przychód DZIŚ</div>
        <div class="kpi-trend {dzis_trend_class}">
            {'↑' if dzis_trend > 0 else '↓' if dzis_trend < 0 else '→'} {abs(dzis_trend):.0f}% vs wczoraj
        </div>
    </div>
    <div class="kpi-card">
        <div class="kpi-value">{dzis['cnt'] or 0}</div>
        <div class="kpi-label">Zamówień DZIŚ</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-value" style="color:var(--blue)">{tydzien['suma'] or 0:.0f} zł</div>
        <div class="kpi-label">Przychód TYDZIEŃ</div>
        <div class="kpi-trend {tydzien_trend_class}">
            {'↑' if tydzien_trend > 0 else '↓' if tydzien_trend < 0 else '→'} {abs(tydzien_trend):.0f}% vs poprz.
        </div>
    </div>
    <div class="kpi-card">
        <div class="kpi-value" style="color:var(--purple)">{miesiac['suma'] or 0:.0f} zł</div>
        <div class="kpi-label">Przychód MIESIĄC</div>
        <div class="kpi-trend {miesiac_trend_class}">
            {'↑' if miesiac_trend > 0 else '↓' if miesiac_trend < 0 else '→'} {abs(miesiac_trend):.0f}% vs poprz.
        </div>
    </div>
</div>

<div class="kpi-grid">
    <div class="kpi-card" title="Przychód: {przychod:.0f} - Koszt: {koszty:.0f} - Prowizja: {prowizja:.0f} = {zysk_miesiac:.0f} zł">
        <div class="kpi-value" style="color:var(--green)">{zysk_miesiac:.0f} zł</div>
        <div class="kpi-label">Zysk netto (miesiąc)</div>
        <div style="font-size:0.65rem;color:var(--muted);margin-top:4px">Koszt: {koszty:.0f} | Prowizja: {prowizja:.0f} zł</div>
    </div>
    <div class="kpi-card" title="Zysk {zysk_miesiac:.0f} / Koszt {koszty:.0f} × 100%">
        <div class="kpi-value" style="color:{'var(--green)' if roi_miesiac > 50 else 'var(--yellow)'}">{roi_miesiac:.0f}%</div>
        <div class="kpi-label">ROI (miesiąc)</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-value">{magazyn['sztuki'] or 0}</div>
        <div class="kpi-label">Sztuk w magazynie</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-value">{magazyn['wartosc_sprzedazy'] or 0:.0f} zł</div>
        <div class="kpi-label">Wartość magazynu</div>
    </div>
</div>

<!-- WYKRES SPRZEDAŻY -->
<div class="chart-container">
    <div class="chart-title">📈 Sprzedaż ostatnie 7 dni</div>
    {''.join([f"""
    <div class="chart-bar">
        <div class="chart-bar-label">{d['dzien'][5:]}</div>
        <div class="chart-bar-track">
            <div class="chart-bar-fill" style="width:{(d['suma']/max_dzien*100) if max_dzien > 0 else 0:.0f}%;background:var(--green)"></div>
        </div>
        <div class="chart-bar-value">{d['suma']:.0f} zł</div>
    </div>
    """ for d in reversed(sprzedaz_dni)])}
</div>

<!-- TOP PRODUKTY -->
<div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(300px, 1fr));gap:20px;">
    <div class="top-list">
        <div class="chart-title">🏆 TOP 5 Produktów (miesiąc)</div>
        {''.join([f"""
        <div class="top-item">
            <div class="top-rank {'gold' if i==0 else 'silver' if i==1 else 'bronze' if i==2 else ''}">{i+1}</div>
            <div class="top-info">
                <div class="top-name">{(p['produkt_nazwa'] or 'Nieznany')[:30]}{'...' if len(p['produkt_nazwa'] or '')>30 else ''}</div>
                <div class="top-meta">{p['sprzedane']} szt sprzedanych</div>
            </div>
            <div class="top-value" style="color:var(--green)">{(p['wartosc'] or 0):.0f} zł</div>
        </div>
        """ for i, p in enumerate(top_produkty)])}
        {'' if top_produkty else '<div style="text-align:center;color:var(--muted);padding:20px">Brak danych</div>'}
    </div>
    
    <div class="top-list">
        <div class="chart-title">📦 TOP 5 Dostawców (ROI)</div>
        {''.join([f"""
        <div class="top-item">
            <div class="top-rank {'gold' if i==0 else 'silver' if i==1 else 'bronze' if i==2 else ''}">{i+1}</div>
            <div class="top-info">
                <div class="top-name">{d['dostawca_nazwa']}</div>
                <div class="top-meta">{d['sprzedane']} szt | {(d['przychod'] or 0):.0f} zł przychód</div>
            </div>
            <div class="top-value" style="color:{'var(--green)' if (d['koszty'] or 0) > 0 and ((d['przychod'] or 0)-(d['koszty'] or 0)-(d['przychod'] or 0)*0.11)/(d['koszty'] or 1)*100 > 50 else 'var(--yellow)'}">{(((d['przychod'] or 0)-(d['koszty'] or 0)-(d['przychod'] or 0)*0.11)/(d['koszty'] or 1)*100) if (d['koszty'] or 0) > 0 else 0:.0f}%</div>
        </div>
        """ for i, d in enumerate(top_dostawcy)])}
        {'' if top_dostawcy else '<div style="text-align:center;color:var(--muted);padding:20px">Brak danych</div>'}
    </div>
</div>

<a href="/" class="back-link">← Powrót do strony głównej</a>

</body></html>
'''
    return html


# ============================================================
# KALKULATOR OPŁACALNOŚCI PALETY
# ============================================================
@analytics_bp.route('/kalkulator-palety', methods=['GET', 'POST'])
def kalkulator_palety():
    """Kalkulator opłacalności palety przed zakupem"""
    
    result = None
    
    if request.method == 'POST':
        try:
            # Pobierz dane z formularza
            cena_palety = float(request.form.get('cena_palety', 0))
            ilosc_produktow = int(request.form.get('ilosc_produktow', 0))
            dostawca = request.form.get('dostawca', 'Jobalots')
            kategoria = request.form.get('kategoria', 'elektronika')
            
            # Szacunkowe ceny sprzedaży bazując na kategorii
            SREDNIE_CENY = {
                'elektronika': {'min': 50, 'avg': 120, 'max': 250},
                'agd': {'min': 40, 'avg': 90, 'max': 180},
                'dom_ogrod': {'min': 30, 'avg': 70, 'max': 150},
                'motoryzacja': {'min': 35, 'avg': 85, 'max': 200},
                'zabawki': {'min': 25, 'avg': 55, 'max': 120},
                'odziez': {'min': 20, 'avg': 45, 'max': 100},
                'mix': {'min': 30, 'avg': 65, 'max': 140},
            }
            
            ceny = SREDNIE_CENY.get(kategoria, SREDNIE_CENY['mix'])
            
            # Procent sprzedawalności (bazując na dostawcy)
            SPRZEDAWALNOSC = {
                'Jobalots': 0.75,  # 75% produktów się sprzeda
                'Warrington': 0.70,
                'Miglo': 0.80,
                'Amazon': 0.85,
                'Inny': 0.65,
            }
            sprzedawalnosc = SPRZEDAWALNOSC.get(dostawca, 0.70)
            
            # Obliczenia
            produkty_do_sprzedazy = int(ilosc_produktow * sprzedawalnosc)
            
            # Scenariusze
            przychod_pesymistyczny = produkty_do_sprzedazy * ceny['min']
            przychod_realny = produkty_do_sprzedazy * ceny['avg']
            przychod_optymistyczny = produkty_do_sprzedazy * ceny['max']
            
            # Koszty (prowizja Allegro ~11%, wysyłka ~8 zł/szt)
            prowizja = 0.11
            koszt_wysylki_szt = 8
            
            koszty_dodatkowe_pesym = (przychod_pesymistyczny * prowizja) + (produkty_do_sprzedazy * koszt_wysylki_szt)
            koszty_dodatkowe_real = (przychod_realny * prowizja) + (produkty_do_sprzedazy * koszt_wysylki_szt)
            koszty_dodatkowe_optym = (przychod_optymistyczny * prowizja) + (produkty_do_sprzedazy * koszt_wysylki_szt)
            
            zysk_pesymistyczny = przychod_pesymistyczny - cena_palety - koszty_dodatkowe_pesym
            zysk_realny = przychod_realny - cena_palety - koszty_dodatkowe_real
            zysk_optymistyczny = przychod_optymistyczny - cena_palety - koszty_dodatkowe_optym
            
            roi_pesymistyczny = (zysk_pesymistyczny / cena_palety * 100) if cena_palety > 0 else 0
            roi_realny = (zysk_realny / cena_palety * 100) if cena_palety > 0 else 0
            roi_optymistyczny = (zysk_optymistyczny / cena_palety * 100) if cena_palety > 0 else 0
            
            # Ocena
            if roi_realny >= 100:
                ocena = 'excellent'
                ocena_text = '🔥 ŚWIETNA OKAZJA!'
            elif roi_realny >= 50:
                ocena = 'good'
                ocena_text = '👍 Dobra inwestycja'
            elif roi_realny >= 20:
                ocena = 'average'
                ocena_text = '🤔 Średnia opłacalność'
            else:
                ocena = 'poor'
                ocena_text = '⚠️ Ryzykowne'
            
            result = {
                'cena_palety': cena_palety,
                'ilosc_produktow': ilosc_produktow,
                'produkty_do_sprzedazy': produkty_do_sprzedazy,
                'sprzedawalnosc': sprzedawalnosc * 100,
                'dostawca': dostawca,
                'kategoria': kategoria,
                'przychod_pesymistyczny': przychod_pesymistyczny,
                'przychod_realny': przychod_realny,
                'przychod_optymistyczny': przychod_optymistyczny,
                'zysk_pesymistyczny': zysk_pesymistyczny,
                'zysk_realny': zysk_realny,
                'zysk_optymistyczny': zysk_optymistyczny,
                'roi_pesymistyczny': roi_pesymistyczny,
                'roi_realny': roi_realny,
                'roi_optymistyczny': roi_optymistyczny,
                'ocena': ocena,
                'ocena_text': ocena_text,
                'cena_avg': ceny['avg'],
            }
        except Exception as e:
            print(f"Błąd kalkulatora: {e}")
    
    html = f'''
<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🧮 Kalkulator Palety - Akces Hub</title>
{ANALYTICS_CSS}
</head><body>

<div class="header">
    <h1>🧮 KALKULATOR OPŁACALNOŚCI</h1>
    <small>Oszacuj zysk przed zakupem palety</small>
</div>

<div class="calc-card">
    <div class="calc-title">📦 Dane palety</div>
    <form method="POST">
        <div class="form-row">
            <div class="form-group">
                <label>💰 Cena palety (brutto PLN)</label>
                <input type="number" step="0.01" name="cena_palety" class="form-control" 
                       value="{result['cena_palety'] if result else ''}" placeholder="np. 2500" required>
            </div>
            <div class="form-group">
                <label>📦 Ilość produktów</label>
                <input type="number" name="ilosc_produktow" class="form-control" 
                       value="{result['ilosc_produktow'] if result else ''}" placeholder="np. 50" required>
            </div>
        </div>
        
        <div class="form-row">
            <div class="form-group">
                <label>🏭 Dostawca</label>
                <select name="dostawca" class="form-control">
                    <option value="Jobalots" {'selected' if result and result['dostawca']=='Jobalots' else ''}>🇳🇱 Jobalots (75% sprzedawalność)</option>
                    <option value="Warrington" {'selected' if result and result['dostawca']=='Warrington' else ''}>🇬🇧 Warrington (70%)</option>
                    <option value="Miglo" {'selected' if result and result['dostawca']=='Miglo' else ''}>🇵🇱 Miglo (80%)</option>
                    <option value="Amazon" {'selected' if result and result['dostawca']=='Amazon' else ''}>📦 Amazon Returns (85%)</option>
                    <option value="Inny" {'selected' if result and result['dostawca']=='Inny' else ''}>❓ Inny (65%)</option>
                </select>
            </div>
            <div class="form-group">
                <label>📁 Kategoria</label>
                <select name="kategoria" class="form-control">
                    <option value="elektronika" {'selected' if result and result['kategoria']=='elektronika' else ''}>📱 Elektronika (śr. 120 zł)</option>
                    <option value="agd" {'selected' if result and result['kategoria']=='agd' else ''}>🍳 AGD (śr. 90 zł)</option>
                    <option value="dom_ogrod" {'selected' if result and result['kategoria']=='dom_ogrod' else ''}>🏠 Dom i ogród (śr. 70 zł)</option>
                    <option value="motoryzacja" {'selected' if result and result['kategoria']=='motoryzacja' else ''}>🚗 Motoryzacja (śr. 85 zł)</option>
                    <option value="zabawki" {'selected' if result and result['kategoria']=='zabawki' else ''}>🧸 Zabawki (śr. 55 zł)</option>
                    <option value="odziez" {'selected' if result and result['kategoria']=='odziez' else ''}>👕 Odzież (śr. 45 zł)</option>
                    <option value="mix" {'selected' if result and result['kategoria']=='mix' else ''}>🎁 Mix (śr. 65 zł)</option>
                </select>
            </div>
        </div>
        
        <button type="submit" class="btn btn-primary" style="width:100%;margin-top:10px">
            🔮 OBLICZ OPŁACALNOŚĆ
        </button>
    </form>
</div>

{f"""
<div class="result-card">
    <div class="result-header">
        <h3>📊 Wyniki analizy</h3>
        <div class="result-badge {result['ocena']}">{result['ocena_text']}</div>
    </div>
    
    <div style="margin-bottom:20px;padding:15px;background:rgba(0,0,0,0.3);border-radius:8px">
        <div style="color:var(--muted);font-size:0.9rem;margin-bottom:5px">
            📦 {result['ilosc_produktow']} produktów × {result['sprzedawalnosc']:.0f}% sprzedawalność = 
            <strong style="color:var(--text)">{result['produkty_do_sprzedazy']} szt do sprzedania</strong>
        </div>
        <div style="color:var(--muted);font-size:0.9rem">
            💰 Średnia cena sprzedaży w kategorii: <strong style="color:var(--text)">{result['cena_avg']} zł</strong>
        </div>
    </div>
    
    <div style="margin-bottom:20px">
        <div style="font-weight:600;margin-bottom:10px">📈 Scenariusze:</div>
        <div style="display:grid;grid-template-columns:repeat(3, 1fr);gap:10px">
            <div style="background:rgba(239,68,68,0.1);padding:12px;border-radius:8px;text-align:center">
                <div style="font-size:0.8rem;color:var(--red)">😟 Pesymistyczny</div>
                <div style="font-size:1.3rem;font-weight:700;color:{'var(--green)' if result['zysk_pesymistyczny'] > 0 else 'var(--red)'}">{result['zysk_pesymistyczny']:.0f} zł</div>
                <div style="font-size:0.8rem;color:var(--muted)">ROI: {result['roi_pesymistyczny']:.0f}%</div>
            </div>
            <div style="background:rgba(59,130,246,0.2);padding:12px;border-radius:8px;text-align:center;border:2px solid var(--blue)">
                <div style="font-size:0.8rem;color:var(--blue)">🎯 Realny</div>
                <div style="font-size:1.5rem;font-weight:700;color:{'var(--green)' if result['zysk_realny'] > 0 else 'var(--red)'}">{result['zysk_realny']:.0f} zł</div>
                <div style="font-size:0.9rem;color:var(--muted)">ROI: {result['roi_realny']:.0f}%</div>
            </div>
            <div style="background:rgba(34,197,94,0.1);padding:12px;border-radius:8px;text-align:center">
                <div style="font-size:0.8rem;color:var(--green)">🚀 Optymistyczny</div>
                <div style="font-size:1.3rem;font-weight:700;color:var(--green)">{result['zysk_optymistyczny']:.0f} zł</div>
                <div style="font-size:0.8rem;color:var(--muted)">ROI: {result['roi_optymistyczny']:.0f}%</div>
            </div>
        </div>
    </div>
    
    <div class="result-grid">
        <div class="result-item">
            <div class="result-value">{result['cena_palety']:.0f} zł</div>
            <div class="result-label">Koszt palety</div>
        </div>
        <div class="result-item">
            <div class="result-value" style="color:var(--blue)">{result['przychod_realny']:.0f} zł</div>
            <div class="result-label">Przychód (realny)</div>
        </div>
        <div class="result-item">
            <div class="result-value" style="color:{'var(--green)' if result['zysk_realny'] > 0 else 'var(--red)'}">{result['zysk_realny']:.0f} zł</div>
            <div class="result-label">Zysk netto</div>
        </div>
        <div class="result-item">
            <div class="result-value" style="color:{'var(--green)' if result['roi_realny'] > 50 else 'var(--yellow)'}">{result['roi_realny']:.0f}%</div>
            <div class="result-label">ROI</div>
        </div>
    </div>
</div>
""" if result else ''}

<a href="/" class="back-link">← Powrót do strony głównej</a>
<a href="/analytics/dashboard" class="back-link" style="margin-left:20px">📊 Dashboard KPI</a>

</body></html>
'''
    return html


# ============================================================
# API ENDPOINTS
# ============================================================
@analytics_bp.route('/api/kpi')
def api_kpi():
    """API endpoint dla danych KPI (do odświeżania AJAX)"""
    conn = get_db()
    
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    dzis = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc), 0) as suma
        FROM sprzedaze WHERE date(data_sprzedazy) = ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
        AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''', (today_str,)).fetchone()
    
    return jsonify({
        'dzis_przychod': dzis['suma'] or 0,
        'dzis_zamowienia': dzis['cnt'] or 0,
        'timestamp': datetime.now().isoformat()
    })
