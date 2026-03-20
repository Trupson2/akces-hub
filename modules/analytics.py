"""
ANALYTICS MODULE - Dashboard KPI & Kalkulator opłacalności palety
v2.0 - Redesign with base.html template
"""

from flask import Blueprint, render_template_string, request, jsonify, session, current_app
from datetime import datetime, timedelta
from .database import get_db
import json

analytics_bp = Blueprint('analytics', __name__)


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
    przychod_data = conn.execute('''
        SELECT COALESCE(SUM(s.cena * s.ilosc), 0) as przychod
        FROM sprzedaze s
        WHERE date(s.data_sprzedazy) >= ?
        AND s.status NOT IN ('zwrot', 'anulowane', 'anulowana')
        AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
    ''', (month_start,)).fetchone()

    przychod = przychod_data['przychod'] or 0

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

    sold_this_month = conn.execute('''
        SELECT COALESCE(SUM(ilosc), 0) as s FROM sprzedaze
        WHERE date(data_sprzedazy) >= ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
        AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''', (month_start,)).fetchone()['s'] or 0

    koszty = avg_cost_per_item * sold_this_month
    prowizja = przychod * 0.11
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
            'title': f'{stojace_30} produktow stoi >30 dni',
            'text': 'Rozwaz obnizenie cen lub promocje'
        })
    if dzis_trend < -30:
        alerts.append({
            'type': 'warning',
            'title': 'Spadek sprzedazy',
            'text': f'Dzis {abs(dzis_trend):.0f}% mniej niz wczoraj'
        })
    if roi_miesiac > 100:
        alerts.append({
            'type': 'success',
            'title': f'Swietny ROI: {roi_miesiac:.0f}%',
            'text': 'Tak trzymaj!'
        })

    # === RENDER ===
    html = '''{% extends "base.html" %}
{% block page_title %}Dashboard KPI{% endblock %}
{% block content %}
<style>
.chart-bar{display:flex;align-items:center;margin-bottom:12px}
.chart-bar-label{width:80px;font-size:0.85rem;color:var(--text-muted)}
.chart-bar-track{flex:1;height:24px;background:var(--border);border-radius:4px;overflow:hidden;margin:0 10px}
.chart-bar-fill{height:100%;border-radius:4px;transition:width 0.5s ease}
.chart-bar-value{width:80px;text-align:right;font-weight:600}
.top-item{display:flex;align-items:center;padding:12px 0;border-bottom:1px solid var(--border)}
.top-item:last-child{border-bottom:none}
.top-rank{width:30px;font-weight:700;color:var(--text-muted)}
.top-rank.gold{color:#ffd700}
.top-rank.silver{color:#c0c0c0}
.top-rank.bronze{color:#cd7f32}
.top-info{flex:1}
.top-name{font-weight:500}
.top-meta{font-size:0.8rem;color:var(--text-muted)}
.top-value{font-weight:700}
</style>

<div style="margin-bottom:8px;font-size:0.78rem;color:var(--text-muted)">Aktualizacja: ''' + today.strftime('%d.%m.%Y %H:%M') + '''</div>

<!-- ALERTY -->
''' + ''.join([f'<div class="alert alert-{"error" if a["type"]=="danger" else a["type"]}">{a["title"]} &mdash; {a["text"]}</div>' for a in alerts]) + '''

<!-- KPI GŁÓWNE -->
<div class="kpi-grid">
    <div class="kpi-card green">
        <div class="kpi-icon" style="background:var(--green-soft)">$</div>
        <div class="kpi-value" style="color:var(--green)">''' + f'{dzis["suma"] or 0:.0f}' + ''' zl</div>
        <div class="kpi-label">Przychod DZIS</div>
        <div class="kpi-change ''' + dzis_trend_class + '''">''' + ('&uarr;' if dzis_trend > 0 else '&darr;' if dzis_trend < 0 else '&rarr;') + f' {abs(dzis_trend):.0f}' + '''% vs wczoraj</div>
    </div>
    <div class="kpi-card purple">
        <div class="kpi-icon" style="background:var(--accent-soft)">#</div>
        <div class="kpi-value">''' + f'{dzis["cnt"] or 0}' + '''</div>
        <div class="kpi-label">Zamowien DZIS</div>
    </div>
    <div class="kpi-card blue">
        <div class="kpi-icon" style="background:var(--blue-soft)">W</div>
        <div class="kpi-value" style="color:var(--blue)">''' + f'{tydzien["suma"] or 0:.0f}' + ''' zl</div>
        <div class="kpi-label">Przychod TYDZIEN</div>
        <div class="kpi-change ''' + tydzien_trend_class + '''">''' + ('&uarr;' if tydzien_trend > 0 else '&darr;' if tydzien_trend < 0 else '&rarr;') + f' {abs(tydzien_trend):.0f}' + '''% vs poprz.</div>
    </div>
    <div class="kpi-card orange">
        <div class="kpi-icon" style="background:var(--yellow-soft)">M</div>
        <div class="kpi-value" style="color:var(--purple)">''' + f'{miesiac["suma"] or 0:.0f}' + ''' zl</div>
        <div class="kpi-label">Przychod MIESIAC</div>
        <div class="kpi-change ''' + miesiac_trend_class + '''">''' + ('&uarr;' if miesiac_trend > 0 else '&darr;' if miesiac_trend < 0 else '&rarr;') + f' {abs(miesiac_trend):.0f}' + '''% vs poprz.</div>
    </div>
</div>

<div class="kpi-grid">
    <div class="kpi-card green" title="Przychod: ''' + f'{przychod:.0f}' + ''' - Koszt: ''' + f'{koszty:.0f}' + ''' - Prowizja: ''' + f'{prowizja:.0f}' + ''' = ''' + f'{zysk_miesiac:.0f}' + ''' zl">
        <div class="kpi-icon" style="background:var(--green-soft)">Z</div>
        <div class="kpi-value" style="color:var(--green)">''' + f'{zysk_miesiac:.0f}' + ''' zl</div>
        <div class="kpi-label">Zysk netto (miesiac)</div>
        <div style="font-size:0.65rem;color:var(--text-muted);margin-top:4px">Koszt: ''' + f'{koszty:.0f}' + ''' | Prowizja: ''' + f'{prowizja:.0f}' + ''' zl</div>
    </div>
    <div class="kpi-card blue" title="Zysk ''' + f'{zysk_miesiac:.0f}' + ''' / Koszt ''' + f'{koszty:.0f}' + ''' x 100%">
        <div class="kpi-icon" style="background:var(--blue-soft)">%</div>
        <div class="kpi-value" style="color:''' + ('var(--green)' if roi_miesiac > 50 else 'var(--yellow)') + '''">''' + f'{roi_miesiac:.0f}' + '''%</div>
        <div class="kpi-label">ROI (miesiac)</div>
    </div>
    <div class="kpi-card purple">
        <div class="kpi-icon" style="background:var(--accent-soft)">S</div>
        <div class="kpi-value">''' + f'{magazyn["sztuki"] or 0}' + '''</div>
        <div class="kpi-label">Sztuk w magazynie</div>
    </div>
    <div class="kpi-card orange">
        <div class="kpi-icon" style="background:var(--yellow-soft)">V</div>
        <div class="kpi-value">''' + f'{magazyn["wartosc_sprzedazy"] or 0:.0f}' + ''' zl</div>
        <div class="kpi-label">Wartosc magazynu</div>
    </div>
</div>

<!-- WYKRES SPRZEDAŻY -->
<div class="card">
    <div class="card-header">
        <div class="card-title">Sprzedaz ostatnie 7 dni</div>
    </div>
    ''' + ''.join([f'''
    <div class="chart-bar">
        <div class="chart-bar-label">{d['dzien'][5:]}</div>
        <div class="chart-bar-track">
            <div class="chart-bar-fill" style="width:{(d['suma']/max_dzien*100) if max_dzien > 0 else 0:.0f}%;background:var(--green)"></div>
        </div>
        <div class="chart-bar-value">{d['suma']:.0f} zl</div>
    </div>
    ''' for d in reversed(list(sprzedaz_dni))]) + '''
</div>

<!-- TOP PRODUKTY -->
<div class="dash-grid">
    <div class="card">
        <div class="card-header">
            <div class="card-title">TOP 5 Produktow (miesiac)</div>
        </div>
        ''' + ''.join([f'''
        <div class="top-item">
            <div class="top-rank {'gold' if i==0 else 'silver' if i==1 else 'bronze' if i==2 else ''}">{i+1}</div>
            <div class="top-info">
                <div class="top-name">{(p['produkt_nazwa'] or 'Nieznany')[:30]}{'...' if len(p['produkt_nazwa'] or '')>30 else ''}</div>
                <div class="top-meta">{p['sprzedane']} szt sprzedanych</div>
            </div>
            <div class="top-value" style="color:var(--green)">{(p['wartosc'] or 0):.0f} zl</div>
        </div>
        ''' for i, p in enumerate(top_produkty)]) + ('' if top_produkty else '<div style="text-align:center;color:var(--text-muted);padding:20px">Brak danych</div>') + '''
    </div>

    <div class="card">
        <div class="card-header">
            <div class="card-title">TOP 5 Dostawcow (ROI)</div>
        </div>
        ''' + ''.join([f'''
        <div class="top-item">
            <div class="top-rank {'gold' if i==0 else 'silver' if i==1 else 'bronze' if i==2 else ''}">{i+1}</div>
            <div class="top-info">
                <div class="top-name">{d['dostawca_nazwa']}</div>
                <div class="top-meta">{d['sprzedane']} szt | {(d['przychod'] or 0):.0f} zl przychod</div>
            </div>
            <div class="top-value" style="color:{'var(--green)' if (d['koszty'] or 0) > 0 and ((d['przychod'] or 0)-(d['koszty'] or 0)-(d['przychod'] or 0)*0.11)/(d['koszty'] or 1)*100 > 50 else 'var(--yellow)'}">{(((d['przychod'] or 0)-(d['koszty'] or 0)-(d['przychod'] or 0)*0.11)/(d['koszty'] or 1)*100) if (d['koszty'] or 0) > 0 else 0:.0f}%</div>
        </div>
        ''' for i, d in enumerate(top_dostawcy)]) + ('' if top_dostawcy else '<div style="text-align:center;color:var(--text-muted);padding:20px">Brak danych</div>') + '''
    </div>
</div>

{% endblock %}
'''
    return render_template_string(html,
        version=current_app.config.get('VERSION', ''),
        brand_name=current_app.config.get('BRAND_NAME', 'Akces Hub'),
        current_user=session.get('user')
    )


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
                'Jobalots': 0.75,
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
                ocena_text = 'SWIETNA OKAZJA!'
            elif roi_realny >= 50:
                ocena = 'good'
                ocena_text = 'Dobra inwestycja'
            elif roi_realny >= 20:
                ocena = 'average'
                ocena_text = 'Srednia oplacalnosc'
            else:
                ocena = 'poor'
                ocena_text = 'Ryzykowne'

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

    # Build result HTML block
    result_html = ''
    if result:
        # Badge class mapping
        badge_map = {'excellent': 'badge-success', 'good': 'badge-success', 'average': 'badge-warning', 'poor': 'badge-error'}
        badge_cls = badge_map.get(result['ocena'], 'badge-warning')

        result_html = f'''
<div class="card" style="border-color:var(--accent);margin-top:20px">
    <div class="card-header">
        <div class="card-title">Wyniki analizy</div>
        <span class="badge {badge_cls}">{result['ocena_text']}</span>
    </div>

    <div style="margin-bottom:20px;padding:15px;background:var(--bg);border-radius:var(--radius-sm)">
        <div style="color:var(--text-muted);font-size:0.9rem;margin-bottom:5px">
            {result['ilosc_produktow']} produktow x {result['sprzedawalnosc']:.0f}% sprzedawalnosc =
            <strong style="color:var(--text)">{result['produkty_do_sprzedazy']} szt do sprzedania</strong>
        </div>
        <div style="color:var(--text-muted);font-size:0.9rem">
            Srednia cena sprzedazy w kategorii: <strong style="color:var(--text)">{result['cena_avg']} zl</strong>
        </div>
    </div>

    <div class="section-title">Scenariusze</div>
    <div class="stat-row">
        <div class="stat-box" style="border-color:var(--red)">
            <div style="font-size:0.72rem;color:var(--red);margin-bottom:6px">Pesymistyczny</div>
            <div class="stat-val {'green' if result['zysk_pesymistyczny'] > 0 else 'red'}">{result['zysk_pesymistyczny']:.0f} zl</div>
            <div class="stat-lbl">ROI: {result['roi_pesymistyczny']:.0f}%</div>
        </div>
        <div class="stat-box" style="border-color:var(--blue);border-width:2px">
            <div style="font-size:0.72rem;color:var(--blue);margin-bottom:6px">Realny</div>
            <div class="stat-val {'green' if result['zysk_realny'] > 0 else 'red'}" style="font-size:1.5rem">{result['zysk_realny']:.0f} zl</div>
            <div class="stat-lbl">ROI: {result['roi_realny']:.0f}%</div>
        </div>
        <div class="stat-box" style="border-color:var(--green)">
            <div style="font-size:0.72rem;color:var(--green);margin-bottom:6px">Optymistyczny</div>
            <div class="stat-val green">{result['zysk_optymistyczny']:.0f} zl</div>
            <div class="stat-lbl">ROI: {result['roi_optymistyczny']:.0f}%</div>
        </div>
    </div>

    <div class="stat-row" style="grid-template-columns:repeat(4,1fr);margin-top:16px">
        <div class="stat-box">
            <div class="stat-val">{result['cena_palety']:.0f} zl</div>
            <div class="stat-lbl">Koszt palety</div>
        </div>
        <div class="stat-box">
            <div class="stat-val blue">{result['przychod_realny']:.0f} zl</div>
            <div class="stat-lbl">Przychod (realny)</div>
        </div>
        <div class="stat-box">
            <div class="stat-val {'green' if result['zysk_realny'] > 0 else 'red'}">{result['zysk_realny']:.0f} zl</div>
            <div class="stat-lbl">Zysk netto</div>
        </div>
        <div class="stat-box">
            <div class="stat-val {'green' if result['roi_realny'] > 50 else 'orange'}">{result['roi_realny']:.0f}%</div>
            <div class="stat-lbl">ROI</div>
        </div>
    </div>
</div>
'''

    html = '''{% extends "base.html" %}
{% block page_title %}Kalkulator Oplacalnosci{% endblock %}
{% block content %}

<div class="card">
    <div class="card-header">
        <div class="card-title">Dane palety</div>
        <span class="badge badge-success">Kalkulator</span>
    </div>
    <form method="POST">
        <div class="form-row">
            <div class="form-group">
                <label>Cena palety (brutto PLN)</label>
                <input type="number" step="0.01" name="cena_palety" class="form-control"
                       value="''' + (f'{result["cena_palety"]}' if result else '') + '''" placeholder="np. 2500" required>
            </div>
            <div class="form-group">
                <label>Ilosc produktow</label>
                <input type="number" name="ilosc_produktow" class="form-control"
                       value="''' + (f'{result["ilosc_produktow"]}' if result else '') + '''" placeholder="np. 50" required>
            </div>
        </div>

        <div class="form-row">
            <div class="form-group">
                <label>Dostawca</label>
                <select name="dostawca" class="form-control">
                    <option value="Jobalots" ''' + ('selected' if result and result['dostawca']=='Jobalots' else '') + '''>Jobalots (75% sprzedawalnosc)</option>
                    <option value="Warrington" ''' + ('selected' if result and result['dostawca']=='Warrington' else '') + '''>Warrington (70%)</option>
                    <option value="Miglo" ''' + ('selected' if result and result['dostawca']=='Miglo' else '') + '''>Miglo (80%)</option>
                    <option value="Amazon" ''' + ('selected' if result and result['dostawca']=='Amazon' else '') + '''>Amazon Returns (85%)</option>
                    <option value="Inny" ''' + ('selected' if result and result['dostawca']=='Inny' else '') + '''>Inny (65%)</option>
                </select>
            </div>
            <div class="form-group">
                <label>Kategoria</label>
                <select name="kategoria" class="form-control">
                    <option value="elektronika" ''' + ('selected' if result and result['kategoria']=='elektronika' else '') + '''>Elektronika (sr. 120 zl)</option>
                    <option value="agd" ''' + ('selected' if result and result['kategoria']=='agd' else '') + '''>AGD (sr. 90 zl)</option>
                    <option value="dom_ogrod" ''' + ('selected' if result and result['kategoria']=='dom_ogrod' else '') + '''>Dom i ogrod (sr. 70 zl)</option>
                    <option value="motoryzacja" ''' + ('selected' if result and result['kategoria']=='motoryzacja' else '') + '''>Motoryzacja (sr. 85 zl)</option>
                    <option value="zabawki" ''' + ('selected' if result and result['kategoria']=='zabawki' else '') + '''>Zabawki (sr. 55 zl)</option>
                    <option value="odziez" ''' + ('selected' if result and result['kategoria']=='odziez' else '') + '''>Odziez (sr. 45 zl)</option>
                    <option value="mix" ''' + ('selected' if result and result['kategoria']=='mix' else '') + '''>Mix (sr. 65 zl)</option>
                </select>
            </div>
        </div>

        <button type="submit" class="btn btn-primary" style="margin-top:10px">
            OBLICZ OPLACALNOSC
        </button>
    </form>
</div>

''' + result_html + '''

<div style="display:flex;gap:16px;margin-top:20px">
    <a href="/analytics/dashboard" class="btn btn-sm btn-secondary">Dashboard KPI</a>
    <a href="/analytics/profit" class="btn btn-sm btn-secondary">Profit Analyzer</a>
</div>

{% endblock %}
'''
    return render_template_string(html,
        version=current_app.config.get('VERSION', ''),
        brand_name=current_app.config.get('BRAND_NAME', 'Akces Hub'),
        current_user=session.get('user')
    )


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


# ============================================================
# PROFIT ANALYZER — Dashboard zysków w stylu vSprint
# ============================================================
@analytics_bp.route('/profit')
def profit_analyzer():
    """Zaawansowany dashboard analizy zysków"""
    conn = get_db()
    today = datetime.now()

    # Parametr: zakres miesięcy (domyślnie 6)
    months_range = int(request.args.get('months', 6))

    # Prowizja — konfigurowalna (domyślnie 15% = Allegro 11% + dostawa ~4%)
    from .database import get_config
    prowizja_pct = float(get_config('allegro_prowizja_pct', '15')) / 100

    # === DANE MIESIĘCZNE (ostatnie N miesięcy) ===
    monthly_data = []
    for i in range(months_range - 1, -1, -1):
        # Prawidłowa arytmetyka miesięcy (timedelta(days=30) przeskakuje miesiące!)
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        dt = datetime(y, m, 1)
        m_start = dt.strftime('%Y-%m-01')
        # Koniec miesiąca
        if dt.month == 12:
            m_end_dt = dt.replace(year=dt.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            m_end_dt = dt.replace(month=dt.month + 1, day=1) - timedelta(days=1)
        m_end = m_end_dt.strftime('%Y-%m-%d')
        m_label = dt.strftime('%m/%Y')

        # Przychód
        rev = conn.execute('''
            SELECT COALESCE(SUM(cena * ilosc), 0) as r, COUNT(*) as cnt,
                   COALESCE(SUM(ilosc), 0) as szt
            FROM sprzedaze
            WHERE date(data_sprzedazy) >= ? AND date(data_sprzedazy) <= ?
            AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
            AND (kupujacy IS NULL OR kupujacy != 'offline')
        ''', (m_start, m_end)).fetchone()

        przychod = rev['r'] or 0
        zamowienia = rev['cnt'] or 0
        sztuki = rev['szt'] or 0

        # Zwroty
        zwroty = conn.execute('''
            SELECT COALESCE(SUM(cena * ilosc), 0) as r, COUNT(*) as cnt
            FROM sprzedaze
            WHERE date(data_sprzedazy) >= ? AND date(data_sprzedazy) <= ?
            AND status = 'zwrot'
        ''', (m_start, m_end)).fetchone()

        # COGS — średni koszt jednostkowy * sprzedane sztuki
        # produkty.ilosc NIE jest dekrementowane przy sprzedaży, więc SUM(ilosc) = oryginalna ilość
        avg_cost = conn.execute('''
            SELECT
                CASE WHEN SUM(sub.total_items) > 0
                     THEN SUM(sub.koszt) * 1.0 / SUM(sub.total_items)
                     ELSE 0 END as avg_unit
            FROM (
                SELECT pal.cena_zakupu as koszt,
                    COALESCE((SELECT SUM(pr.ilosc) FROM produkty pr WHERE pr.paleta_id = pal.id), 1)
                    as total_items
                FROM palety pal WHERE pal.cena_zakupu > 0
            ) sub
        ''').fetchone()
        avg_unit_cost = avg_cost['avg_unit'] or 0

        cogs = avg_unit_cost * sztuki
        prowizja = przychod * prowizja_pct
        zysk = przychod - cogs - prowizja
        marza = (zysk / przychod * 100) if przychod > 0 else 0
        roi = (zysk / cogs * 100) if cogs > 0 else 0

        # Koszty operacyjne z tabeli koszty
        koszty_op = conn.execute('''
            SELECT COALESCE(SUM(kwota), 0) as k FROM koszty
            WHERE date(data) >= ? AND date(data) <= ?
        ''', (m_start, m_end)).fetchone()['k'] or 0

        # Sprzedaż prywatna
        prywatne = conn.execute('''
            SELECT COALESCE(SUM(kwota), 0) as k FROM sprzedaze_prywatne
            WHERE date(data) >= ? AND date(data) <= ?
        ''', (m_start, m_end)).fetchone()['k'] or 0

        zysk_netto = zysk - koszty_op + prywatne

        monthly_data.append({
            'label': m_label,
            'przychod': przychod,
            'cogs': cogs,
            'prowizja': prowizja,
            'zysk_brutto': zysk,
            'koszty_op': koszty_op,
            'prywatne': prywatne,
            'zysk_netto': zysk_netto,
            'marza': marza,
            'roi': roi,
            'zamowienia': zamowienia,
            'sztuki': sztuki,
            'zwroty_kwota': zwroty['r'] or 0,
            'zwroty_cnt': zwroty['cnt'] or 0,
        })

    # === TOTALE ===
    total_przychod = sum(m['przychod'] for m in monthly_data)
    total_cogs = sum(m['cogs'] for m in monthly_data)
    total_prowizja = sum(m['prowizja'] for m in monthly_data)
    total_zysk = sum(m['zysk_netto'] for m in monthly_data)
    total_zamowienia = sum(m['zamowienia'] for m in monthly_data)
    total_zwroty = sum(m['zwroty_cnt'] for m in monthly_data)
    avg_marza = (total_zysk / total_przychod * 100) if total_przychod > 0 else 0
    avg_order = (total_przychod / total_zamowienia) if total_zamowienia > 0 else 0

    # === PER-PALETA ANALIZA ===
    palety_profit = conn.execute('''
        SELECT
            pal.id, pal.nazwa, pal.dostawca, pal.cena_zakupu,
            pal.data_zakupu,
            COUNT(DISTINCT CASE WHEN pr.status IN ('sprzedany','wyslany') THEN pr.id END) as sprzedane_typy,
            COALESCE(SUM(CASE WHEN pr.status IN ('sprzedany','wyslany') THEN pr.ilosc ELSE 0 END), 0) as sprzedane_szt,
            COUNT(DISTINCT CASE WHEN pr.status IN ('magazyn','wystawiony') THEN pr.id END) as w_magazynie_typy,
            COALESCE(SUM(CASE WHEN pr.status IN ('magazyn','wystawiony') THEN pr.ilosc ELSE 0 END), 0) as w_magazynie_szt,
            COALESCE((
                SELECT SUM(s.cena * s.ilosc)
                FROM sprzedaze s
                JOIN produkty pp ON s.produkt_id = pp.id
                WHERE pp.paleta_id = pal.id
                AND s.status NOT IN ('zwrot','anulowane','anulowana')
            ), 0) as przychod
        FROM palety pal
        LEFT JOIN produkty pr ON pr.paleta_id = pal.id
        WHERE pal.cena_zakupu > 0
        GROUP BY pal.id
        ORDER BY pal.data_zakupu DESC
        LIMIT 20
    ''').fetchall()

    # === DOSTAWCY PORÓWNANIE ===
    dostawcy = conn.execute('''
        WITH dost_koszty AS (
            SELECT
                COALESCE(NULLIF(dostawca,''), 'Nieznany') as nazwa,
                COUNT(*) as palet,
                SUM(cena_zakupu) as inwestycja
            FROM palety
            WHERE cena_zakupu > 0
            GROUP BY COALESCE(NULLIF(dostawca,''), 'Nieznany')
        ),
        dost_przychod AS (
            SELECT
                COALESCE(NULLIF(pal.dostawca,''), 'Nieznany') as nazwa,
                COALESCE(SUM(s.cena * s.ilosc), 0) as przychod
            FROM sprzedaze s
            JOIN produkty p ON s.produkt_id = p.id
            JOIN palety pal ON p.paleta_id = pal.id
            WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
            GROUP BY COALESCE(NULLIF(pal.dostawca,''), 'Nieznany')
        )
        SELECT dk.nazwa, dk.palet, dk.inwestycja,
               COALESCE(dp.przychod, 0) as przychod
        FROM dost_koszty dk
        LEFT JOIN dost_przychod dp ON dk.nazwa = dp.nazwa
        ORDER BY COALESCE(dp.przychod, 0) DESC
    ''').fetchall()

    # === DAILY TREND (30 dni) ===
    daily_30 = conn.execute('''
        SELECT date(data_sprzedazy) as dzien,
               COALESCE(SUM(cena * ilosc), 0) as suma,
               COUNT(*) as cnt
        FROM sprzedaze
        WHERE date(data_sprzedazy) >= date('now', '-30 days')
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana')
        AND (kupujacy IS NULL OR kupujacy != 'offline')
        GROUP BY dzien ORDER BY dzien
    ''').fetchall()

    # === TOP KATEGORII ===
    top_kat = conn.execute('''
        SELECT COALESCE(NULLIF(p.kategoria,''), 'Brak') as kat,
               COUNT(*) as cnt, SUM(s.cena * s.ilosc) as wartosc
        FROM sprzedaze s
        JOIN produkty p ON s.produkt_id = p.id
        WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
        AND date(s.data_sprzedazy) >= date('now', '-90 days')
        GROUP BY kat ORDER BY wartosc DESC LIMIT 8
    ''').fetchall()

    # === MAGAZYN STATUS ===
    magazyn = conn.execute('''
        SELECT
            COUNT(*) as produkty,
            COALESCE(SUM(ilosc), 0) as sztuki,
            COALESCE(SUM(cena_allegro * ilosc), 0) as wartosc_potencjalna,
            COUNT(CASE WHEN status = 'wystawiony' THEN 1 END) as wystawione,
            COUNT(CASE WHEN status = 'magazyn' AND date(data_dodania) <= date('now','-30 days') THEN 1 END) as stojace
        FROM produkty WHERE status IN ('magazyn', 'wystawiony')
    ''').fetchone()

    # === TOP PRODUKTÓW z zyskiem (per produkt) ===
    top_produkty_profit = conn.execute('''
        SELECT
            COALESCE(NULLIF(p.nazwa,''), SUBSTR(s.nazwa,1,60), 'Produkt #'||p.id) as nazwa,
            p.id as pid,
            SUM(s.cena * s.ilosc) as przychod,
            SUM(s.ilosc) as sprzedane,
            COALESCE(p.cena_allegro, 0) as cena_sprzedazy,
            COALESCE(pal.cena_zakupu, 0) as paleta_koszt,
            COALESCE((SELECT SUM(pr2.ilosc) FROM produkty pr2 WHERE pr2.paleta_id = pal.id), 1) as paleta_szt,
            p.status
        FROM sprzedaze s
        JOIN produkty p ON s.produkt_id = p.id
        LEFT JOIN palety pal ON p.paleta_id = pal.id
        WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
        GROUP BY p.id
        ORDER BY przychod DESC
        LIMIT 15
    ''').fetchall()

    # === NAJGORSZE PRODUKTY (stojace najdluzej) ===
    stojace_produkty = conn.execute('''
        SELECT p.nazwa, p.id, p.cena_allegro, p.ilosc, p.data_dodania,
               COALESCE(p.lokalizacja, p.regal, '-') as lokalizacja,
               julianday('now') - julianday(p.data_dodania) as dni_w_magazynie,
               COALESCE(pal.dostawca, p.dostawca, '-') as dostawca
        FROM produkty p
        LEFT JOIN palety pal ON p.paleta_id = pal.id
        WHERE p.status IN ('magazyn','wystawiony')
        AND p.data_dodania IS NOT NULL AND p.data_dodania != ''
        ORDER BY dni_w_magazynie DESC
        LIMIT 10
    ''').fetchall()

    # === DZIENNE SREDNIE ===
    daily_avg_7 = conn.execute('''
        SELECT COALESCE(AVG(d.suma), 0) as avg_rev, COALESCE(AVG(d.cnt), 0) as avg_ord
        FROM (
            SELECT date(data_sprzedazy) as dzien, SUM(cena*ilosc) as suma, COUNT(*) as cnt
            FROM sprzedaze
            WHERE date(data_sprzedazy) >= date('now','-7 days')
            AND status NOT IN ('zwrot','anulowane','anulowana')
            AND (kupujacy IS NULL OR kupujacy != 'offline')
            GROUP BY dzien
        ) d
    ''').fetchone()

    daily_avg_30 = conn.execute('''
        SELECT COALESCE(AVG(d.suma), 0) as avg_rev, COALESCE(AVG(d.cnt), 0) as avg_ord
        FROM (
            SELECT date(data_sprzedazy) as dzien, SUM(cena*ilosc) as suma, COUNT(*) as cnt
            FROM sprzedaze
            WHERE date(data_sprzedazy) >= date('now','-30 days')
            AND status NOT IN ('zwrot','anulowane','anulowana')
            AND (kupujacy IS NULL OR kupujacy != 'offline')
            GROUP BY dzien
        ) d
    ''').fetchone()

    # === RENDER ===
    max_monthly = max((m['przychod'] for m in monthly_data), default=1) or 1
    max_daily = max((d['suma'] for d in daily_30), default=1) or 1

    # Bieżący vs poprzedni miesiąc
    curr = monthly_data[-1] if monthly_data else None
    prev = monthly_data[-2] if len(monthly_data) >= 2 else None

    def trend_pct(c, p):
        if not p or p == 0: return 0
        return ((c - p) / abs(p)) * 100

    # Buduj HTML tabeli miesięcznej
    monthly_rows = ''
    for m in monthly_data:
        zysk_color = 'var(--green)' if m['zysk_netto'] >= 0 else 'var(--red)'
        monthly_rows += f'''<tr>
            <td style="font-weight:600">{m['label']}</td>
            <td style="color:var(--blue)">{m['przychod']:,.0f}</td>
            <td>{m['cogs']:,.0f}</td>
            <td>{m['prowizja']:,.0f}</td>
            <td>{m['koszty_op']:,.0f}</td>
            <td style="color:{zysk_color};font-weight:700">{m['zysk_netto']:,.0f}</td>
            <td>{m['marza']:.1f}%</td>
            <td>{m['zamowienia']}</td>
            <td style="color:var(--red)">{m['zwroty_cnt']}</td>
        </tr>'''

    # Wykres miesięczny
    monthly_chart = ''
    for m in monthly_data:
        pct_rev = (m['przychod'] / max_monthly * 100) if max_monthly > 0 else 0
        pct_cost = ((m['cogs'] + m['prowizja']) / max_monthly * 100) if max_monthly > 0 else 0
        zysk_color = 'var(--green)' if m['zysk_netto'] >= 0 else 'var(--red)'
        monthly_chart += f'''
        <div style="margin-bottom:14px">
            <div style="display:flex;align-items:center;margin-bottom:4px">
                <span style="width:60px;font-size:0.8rem;color:var(--text-muted)">{m['label']}</span>
                <div style="flex:1;position:relative;height:28px;background:var(--border);border-radius:4px;overflow:hidden">
                    <div style="position:absolute;height:100%;width:{pct_rev:.0f}%;background:linear-gradient(90deg,var(--blue),var(--accent));border-radius:4px;opacity:0.8"></div>
                    <div style="position:absolute;height:100%;width:{pct_cost:.0f}%;background:rgba(239,68,68,0.4);border-radius:4px"></div>
                </div>
                <span style="width:100px;text-align:right;font-weight:700;font-size:0.85rem;color:{zysk_color}">{m['zysk_netto']:,.0f} zl</span>
            </div>
        </div>'''

    # Wykres daily
    daily_chart = ''
    for d in daily_30:
        pct = (d['suma'] / max_daily * 100) if max_daily > 0 else 0
        lbl = d['dzien'][5:]
        daily_chart += f'''
        <div style="display:flex;align-items:center;margin-bottom:6px">
            <span style="width:50px;font-size:0.72rem;color:var(--text-muted)">{lbl}</span>
            <div style="flex:1;height:18px;background:var(--border);border-radius:3px;overflow:hidden">
                <div style="height:100%;width:{pct:.0f}%;background:var(--green);border-radius:3px"></div>
            </div>
            <span style="width:70px;text-align:right;font-size:0.75rem;font-weight:600">{d['suma']:,.0f}</span>
        </div>'''

    # Palety tabela
    palety_rows = ''
    for p in palety_profit:
        koszt = p['cena_zakupu'] or 0
        przychod_p = p['przychod'] or 0
        prowizja_p = przychod_p * prowizja_pct
        zysk_p = przychod_p - koszt - prowizja_p
        roi_p = (zysk_p / koszt * 100) if koszt > 0 else 0
        total_szt = (p['sprzedane_szt'] or 0) + (p['w_magazynie_szt'] or 0)
        sold_pct = ((p['sprzedane_szt'] or 0) / total_szt * 100) if total_szt > 0 else 0
        # Dni od zakupu
        try:
            from datetime import datetime as _dt
            dz = p['data_zakupu'] or ''
            if dz:
                dni_od = (today - _dt.strptime(dz[:10], '%Y-%m-%d')).days
            else:
                dni_od = 0
        except:
            dni_od = 0

        # Tempo: przychód / dzień
        tempo = (przychod_p / dni_od) if dni_od > 0 else 0
        # Prognoza ROI: ile dni do break-even
        if tempo > 0 and zysk_p < 0:
            dni_do_be = int(abs(zysk_p) / (tempo * 0.89))
        else:
            dni_do_be = 0

        zysk_color = 'var(--green)' if zysk_p >= 0 else 'var(--red)'
        roi_color = 'var(--green)' if roi_p >= 50 else 'var(--yellow)' if roi_p >= 0 else 'var(--red)'

        # Status
        if (p['w_magazynie_szt'] or 0) == 0 and (p['sprzedane_szt'] or 0) > 0:
            status_html = '<span class="badge badge-success">Sprzedana</span>'
        elif sold_pct >= 50:
            status_html = '<span class="badge" style="background:var(--blue-soft);color:var(--blue)">W trakcie</span>'
        elif sold_pct > 0:
            status_html = '<span class="badge badge-warning">Wolna</span>'
        else:
            status_html = '<span class="badge badge-error">Stoi</span>'

        palety_rows += f'''<tr>
            <td>
                <div style="font-weight:600;margin-bottom:2px">{(p['nazwa'] or 'Bez nazwy')[:30]}</div>
                <div style="font-size:0.68rem;color:var(--text-muted)">{p['dostawca'] or '-'} | {dni_od}d</div>
            </td>
            <td>{koszt:,.0f}</td>
            <td style="color:var(--blue)">{przychod_p:,.0f}</td>
            <td style="color:{zysk_color};font-weight:700">{zysk_p:,.0f}</td>
            <td style="color:{roi_color};font-weight:700">{roi_p:.0f}%</td>
            <td>
                <div style="display:flex;align-items:center;gap:6px">
                    <div style="flex:1;height:10px;background:var(--border);border-radius:5px;overflow:hidden;min-width:60px">
                        <div style="height:100%;width:{sold_pct:.0f}%;background:{'var(--green)' if sold_pct>70 else 'var(--blue)' if sold_pct>30 else 'var(--orange)'};border-radius:5px"></div>
                    </div>
                    <span style="font-size:0.72rem;white-space:nowrap">{p['sprzedane_szt'] or 0}/{total_szt}</span>
                </div>
            </td>
            <td style="font-size:0.72rem">{tempo:.0f} zl/d</td>
            <td>{status_html}</td>
        </tr>'''

    # Dostawcy tabela
    dostawcy_rows = ''
    total_dost_inv = sum((d['inwestycja'] or 0) for d in dostawcy) or 1
    max_dost_rev = max((d['przychod'] or 0 for d in dostawcy), default=1) or 1
    for d in dostawcy:
        inv = d['inwestycja'] or 0
        rev_d = d['przychod'] or 0
        prow_d = rev_d * prowizja_pct
        zysk_d = rev_d - inv - prow_d
        roi_d = (zysk_d / inv * 100) if inv > 0 else 0
        udzial = (inv / total_dost_inv * 100) if total_dost_inv > 0 else 0
        bar_w = (rev_d / max_dost_rev * 100) if max_dost_rev > 0 else 0
        dostawcy_rows += f'''<tr>
            <td style="font-weight:600">{d['nazwa']}</td>
            <td>{d['palet']}</td>
            <td>{inv:,.0f}</td>
            <td style="color:var(--blue)">{rev_d:,.0f}</td>
            <td style="color:{'var(--green)' if zysk_d>=0 else 'var(--red)'};font-weight:600">{zysk_d:,.0f}</td>
            <td style="color:{'var(--green)' if roi_d>=50 else 'var(--yellow)' if roi_d>=0 else 'var(--red)'};font-weight:700">{roi_d:.0f}%</td>
            <td>
                <div style="display:flex;align-items:center;gap:6px">
                    <div style="flex:1;height:10px;background:var(--border);border-radius:5px;overflow:hidden;min-width:60px">
                        <div style="height:100%;width:{bar_w:.0f}%;background:var(--blue);border-radius:5px"></div>
                    </div>
                    <span style="font-size:0.72rem">{udzial:.0f}%</span>
                </div>
            </td>
        </tr>'''

    # Top produkty tabela
    top_prod_rows = ''
    for idx, tp in enumerate(top_produkty_profit):
        rev_tp = tp['przychod'] or 0
        koszt_jedn = (tp['paleta_koszt'] / tp['paleta_szt']) if tp['paleta_szt'] > 0 else 0
        koszt_tp = koszt_jedn * (tp['sprzedane'] or 0)
        prow_tp = rev_tp * prowizja_pct
        zysk_tp = rev_tp - koszt_tp - prow_tp
        marza_tp = (zysk_tp / rev_tp * 100) if rev_tp > 0 else 0
        medal = ['style="color:#ffd700"', 'style="color:#c0c0c0"', 'style="color:#cd7f32"']
        rank_style = medal[idx] if idx < 3 else ''
        top_prod_rows += f'''<tr>
            <td {rank_style}><b>{idx+1}</b></td>
            <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{tp['nazwa'] or ''}">{(tp['nazwa'] or '?')[:35]}</td>
            <td>{tp['sprzedane'] or 0}</td>
            <td style="color:var(--blue)">{rev_tp:,.0f}</td>
            <td style="color:{'var(--green)' if zysk_tp>=0 else 'var(--red)'};font-weight:600">{zysk_tp:,.0f}</td>
            <td style="color:{'var(--green)' if marza_tp>=20 else 'var(--yellow)' if marza_tp>=0 else 'var(--red)'}">{marza_tp:.0f}%</td>
        </tr>'''

    # Stojące produkty
    stojace_rows = ''
    for sp in stojace_produkty:
        dni = int(sp['dni_w_magazynie'] or 0)
        wartosc = (sp['cena_allegro'] or 0) * (sp['ilosc'] or 1)
        dni_color = 'var(--red)' if dni > 60 else 'var(--orange)' if dni > 30 else 'var(--yellow)'
        stojace_rows += f'''<tr>
            <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{sp['nazwa'] or ''}">{(sp['nazwa'] or '?')[:30]}</td>
            <td>{sp['dostawca']}</td>
            <td style="color:{dni_color};font-weight:700">{dni}d</td>
            <td>{sp['ilosc'] or 1} szt</td>
            <td>{wartosc:,.0f} zl</td>
            <td>{sp['lokalizacja']}</td>
        </tr>'''

    # Kategorie
    max_kat = max((k['wartosc'] or 0 for k in top_kat), default=1) or 1
    kat_bars = ''
    kat_colors = ['var(--accent)', 'var(--accent2)', 'var(--blue)', 'var(--green)', 'var(--yellow)', 'var(--orange)', 'var(--red)', '#ec4899']
    total_kat_val = sum((k['wartosc'] or 0) for k in top_kat) or 1
    for idx, k in enumerate(top_kat):
        pct = ((k['wartosc'] or 0) / max_kat * 100) if max_kat > 0 else 0
        udzial_k = ((k['wartosc'] or 0) / total_kat_val * 100)
        color = kat_colors[idx % len(kat_colors)]
        kat_bars += f'''
        <div style="display:flex;align-items:center;margin-bottom:8px">
            <span style="width:110px;font-size:0.8rem;color:var(--text-muted)">{(k['kat'] or 'Brak')[:18]}</span>
            <div style="flex:1;height:22px;background:var(--border);border-radius:4px;overflow:hidden;position:relative">
                <div style="height:100%;width:{pct:.0f}%;background:{color};border-radius:4px;opacity:0.8"></div>
                <span style="position:absolute;right:8px;top:3px;font-size:0.68rem;color:var(--text)">{udzial_k:.0f}%</span>
            </div>
            <span style="width:90px;text-align:right;font-size:0.82rem;font-weight:600">{(k['wartosc'] or 0):,.0f} zl</span>
        </div>'''

    # Rozłożenie kosztów
    total_koszty_all = total_cogs + total_prowizja + sum(m['koszty_op'] for m in monthly_data)
    if total_koszty_all > 0:
        pct_cogs = total_cogs / total_koszty_all * 100
        pct_prow = total_prowizja / total_koszty_all * 100
        pct_op = sum(m['koszty_op'] for m in monthly_data) / total_koszty_all * 100
    else:
        pct_cogs = pct_prow = pct_op = 0

    # Build P&L waterfall for current month
    pl_waterfall = ''
    if curr:
        pl_waterfall = f'''
<div class="card">
    <div class="card-header"><div class="card-title">P&amp;L &mdash; {curr['label']}</div></div>
    <div class="wf-container">
        <div class="wf-row">
            <div class="wf-label">Przychod Allegro</div>
            <div class="wf-bar"><div style="height:100%;width:100%;background:linear-gradient(90deg,var(--blue),var(--accent));border-radius:6px"></div></div>
            <div class="wf-val" style="color:var(--blue)">+{curr['przychod']:,.0f}</div>
        </div>
        <div class="wf-row">
            <div class="wf-label">Koszt towaru (COGS)</div>
            <div class="wf-bar"><div style="height:100%;width:{(curr['cogs']/curr['przychod']*100) if curr['przychod']>0 else 0:.0f}%;background:var(--red);border-radius:6px;opacity:0.7"></div></div>
            <div class="wf-val" style="color:var(--red)">-{curr['cogs']:,.0f}</div>
        </div>
        <div class="wf-row">
            <div class="wf-label">Prowizja {prowizja_pct*100:.0f}%</div>
            <div class="wf-bar"><div style="height:100%;width:{(curr['prowizja']/curr['przychod']*100) if curr['przychod']>0 else 0:.0f}%;background:var(--orange);border-radius:6px;opacity:0.7"></div></div>
            <div class="wf-val" style="color:var(--orange)">-{curr['prowizja']:,.0f}</div>
        </div>
        <div class="wf-row">
            <div class="wf-label">Koszty operacyjne</div>
            <div class="wf-bar"><div style="height:100%;width:{(curr['koszty_op']/curr['przychod']*100) if curr['przychod']>0 else 0:.0f}%;background:var(--yellow);border-radius:6px;opacity:0.7"></div></div>
            <div class="wf-val" style="color:var(--yellow)">-{curr['koszty_op']:,.0f}</div>
        </div>
        <div class="wf-row">
            <div class="wf-label">Sprzedaz prywatna</div>
            <div class="wf-bar"><div style="height:100%;width:{(curr['prywatne']/curr['przychod']*100) if curr['przychod']>0 else 0:.0f}%;background:var(--green);border-radius:6px;opacity:0.5"></div></div>
            <div class="wf-val" style="color:var(--green)">+{curr['prywatne']:,.0f}</div>
        </div>
        <hr style="border-color:var(--border);margin:10px 0">
        <div class="wf-row">
            <div class="wf-label" style="font-weight:700;color:var(--text);font-size:0.9rem">ZYSK NETTO</div>
            <div class="wf-bar"><div style="height:100%;width:{abs(curr['zysk_netto'])/curr['przychod']*100 if curr['przychod']>0 else 0:.0f}%;background:{'var(--green)' if curr['zysk_netto']>=0 else 'var(--red)'};border-radius:6px"></div></div>
            <div class="wf-val" style="color:{'var(--green)' if curr['zysk_netto']>=0 else 'var(--red)'};font-size:1rem">{curr['zysk_netto']:,.0f}</div>
        </div>
    </div>
</div>'''
    else:
        pl_waterfall = '<div class="card"><p style="color:var(--text-muted)">Brak danych</p></div>'

    # Build trends for KPI cards
    trend_przychod_html = ''
    trend_zysk_html = ''
    if curr and prev and prev['przychod']:
        t = trend_pct(curr['przychod'], prev['przychod'])
        cls = 'up' if t > 0 else 'down'
        trend_przychod_html = f'<div class="kpi-change {cls}">{t:+.0f}% m/m</div>'
    if curr and prev:
        t = trend_pct(curr['zysk_netto'], prev['zysk_netto'])
        cls = 'up' if t > 0 else 'down' if t < 0 else 'neutral'
        trend_zysk_html = f'<div class="kpi-change {cls}">{t:+.0f}% m/m</div>'

    html = '''{% extends "base.html" %}
{% block page_title %}Profit Analyzer{% endblock %}
{% block content %}
<style>
.nav-tabs{display:flex;gap:8px;margin-bottom:24px;flex-wrap:wrap}
.nav-tabs a{padding:8px 18px;border-radius:var(--radius-sm);text-decoration:none;font-size:0.82rem;font-weight:600;color:var(--text-muted);background:var(--bg-card);border:1px solid var(--border);transition:all 0.2s}
.nav-tabs a:hover,.nav-tabs a.active{color:#fff;background:linear-gradient(135deg,var(--accent),var(--accent2));border-color:var(--accent)}
.wf-container{margin:10px 0}
.wf-row{display:flex;align-items:center;margin-bottom:8px}
.wf-label{width:130px;font-size:0.82rem;color:var(--text-muted)}
.wf-bar{flex:1;height:26px;border-radius:6px;position:relative}
.wf-val{width:100px;text-align:right;font-size:0.88rem;font-weight:700}
.cost-bar{height:32px;border-radius:8px;display:flex;overflow:hidden;margin-bottom:8px}
.cost-legend{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px}
.cost-legend-item{display:flex;align-items:center;gap:6px;font-size:0.78rem}
.cost-dot{width:10px;height:10px;border-radius:50%}
table{width:100%;border-collapse:collapse;font-size:0.78rem}
th{text-align:left;padding:10px 8px;color:var(--text-muted);border-bottom:2px solid var(--border);font-weight:600;white-space:nowrap;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.5px}
td{padding:9px 8px;border-bottom:1px solid var(--border-light)}
tr:hover{background:var(--accent-soft)}
@media(max-width:900px){
    table{font-size:0.7rem}
    th,td{padding:6px 4px}
}
</style>

<div class="nav-tabs">
    <a href="/analytics/profit?months=3" ''' + ('class="active"' if months_range==3 else '') + '''>3 mies.</a>
    <a href="/analytics/profit?months=6" ''' + ('class="active"' if months_range==6 else '') + '''>6 mies.</a>
    <a href="/analytics/profit?months=12" ''' + ('class="active"' if months_range==12 else '') + '''>12 mies.</a>
    <a href="/analytics/dashboard">Dashboard KPI</a>
</div>

<!-- KPI GLOWNE -->
<div class="section-title">Podsumowanie okresu (''' + str(months_range) + ''' mies.)</div>
<div class="kpi-grid">
    <div class="kpi-card blue">
        <div class="kpi-icon" style="background:var(--blue-soft)">P</div>
        <div class="kpi-value" style="color:var(--blue)">''' + f'{total_przychod:,.0f}' + ''' zl</div>
        <div class="kpi-label">Przychod</div>
        ''' + trend_przychod_html + '''
    </div>
    <div class="kpi-card green">
        <div class="kpi-icon" style="background:var(--green-soft)">Z</div>
        <div class="kpi-value" style="color:''' + ('var(--green)' if total_zysk>=0 else 'var(--red)') + '''">''' + f'{total_zysk:,.0f}' + ''' zl</div>
        <div class="kpi-label">Zysk netto</div>
        ''' + trend_zysk_html + '''
    </div>
    <div class="kpi-card purple">
        <div class="kpi-icon" style="background:var(--accent-soft)">%</div>
        <div class="kpi-value" style="color:var(--accent)">''' + f'{avg_marza:.1f}' + '''%</div>
        <div class="kpi-label">Srednia marza netto</div>
    </div>
    <div class="kpi-card orange">
        <div class="kpi-icon" style="background:var(--yellow-soft)">A</div>
        <div class="kpi-value">''' + f'{avg_order:.0f}' + ''' zl</div>
        <div class="kpi-label">Srednia wartosc zamowienia</div>
    </div>
</div>

<div class="kpi-grid">
    <div class="kpi-card purple">
        <div class="kpi-icon" style="background:var(--accent-soft)">#</div>
        <div class="kpi-value">''' + f'{total_zamowienia:,}' + '''</div>
        <div class="kpi-label">Zamowienia</div>
        <div style="font-size:0.68rem;color:var(--text-muted);margin-top:4px">~''' + f'{daily_avg_7["avg_ord"]:.0f}' + '''/dzien (7d) | ~''' + f'{daily_avg_30["avg_ord"]:.0f}' + '''/dzien (30d)</div>
    </div>
    <div class="kpi-card green">
        <div class="kpi-icon" style="background:var(--red-soft)">R</div>
        <div class="kpi-value" style="color:var(--red)">''' + f'{total_zwroty}' + '''</div>
        <div class="kpi-label">Zwroty</div>
        <div style="font-size:0.68rem;color:var(--red);margin-top:4px">''' + f'{(total_zwroty/total_zamowienia*100) if total_zamowienia>0 else 0:.1f}' + '''% wskaznik zwrotow</div>
    </div>
    <div class="kpi-card blue">
        <div class="kpi-icon" style="background:var(--blue-soft)">D</div>
        <div class="kpi-value" style="color:var(--cyan)">''' + f'{daily_avg_7["avg_rev"]:,.0f}' + ''' zl</div>
        <div class="kpi-label">Sredni przychod / dzien (7d)</div>
        <div style="font-size:0.68rem;color:var(--text-muted);margin-top:4px">''' + f'{daily_avg_30["avg_rev"]:,.0f}' + ''' zl (30d)</div>
    </div>
    <div class="kpi-card orange">
        <div class="kpi-icon" style="background:var(--yellow-soft)">M</div>
        <div class="kpi-value" style="color:var(--orange)">''' + f'{magazyn["sztuki"] or 0}' + '''</div>
        <div class="kpi-label">Magazyn (szt.)</div>
        <div style="font-size:0.68rem;color:var(--text-muted);margin-top:4px">''' + f'{magazyn["wystawione"] or 0}' + ''' wystawione | ''' + f'{magazyn["stojace"] or 0}' + ''' stojace &gt;30d</div>
    </div>
</div>

<!-- RACHUNEK WYNIKOW -->
<div class="section-title">Rachunek wynikow</div>
<div class="dash-grid">
''' + pl_waterfall + '''

<div class="card">
    <div class="card-header"><div class="card-title">Struktura kosztow (''' + str(months_range) + ''' mies.)</div></div>
    <div class="cost-bar">
        <div style="width:''' + f'{pct_cogs:.0f}' + '''%;background:var(--red);opacity:0.8" title="COGS ''' + f'{pct_cogs:.0f}' + '''%"></div>
        <div style="width:''' + f'{pct_prow:.0f}' + '''%;background:var(--orange);opacity:0.8" title="Prowizja ''' + f'{pct_prow:.0f}' + '''%"></div>
        <div style="width:''' + f'{pct_op:.0f}' + '''%;background:var(--yellow);opacity:0.8" title="Operacyjne ''' + f'{pct_op:.0f}' + '''%"></div>
    </div>
    <div class="cost-legend">
        <div class="cost-legend-item"><div class="cost-dot" style="background:var(--red)"></div>Koszt towaru: ''' + f'{total_cogs:,.0f}' + ''' zl (''' + f'{pct_cogs:.0f}' + '''%)</div>
        <div class="cost-legend-item"><div class="cost-dot" style="background:var(--orange)"></div>Prowizja: ''' + f'{total_prowizja:,.0f}' + ''' zl (''' + f'{pct_prow:.0f}' + '''%)</div>
        <div class="cost-legend-item"><div class="cost-dot" style="background:var(--yellow)"></div>Operacyjne: ''' + f'{sum(m["koszty_op"] for m in monthly_data):,.0f}' + ''' zl (''' + f'{pct_op:.0f}' + '''%)</div>
    </div>
    <div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border)">
        <div style="display:flex;justify-content:space-between;font-size:0.82rem;margin-bottom:6px">
            <span style="color:var(--text-muted)">Lacznie koszty:</span>
            <span style="font-weight:700;color:var(--red)">''' + f'{total_koszty_all:,.0f}' + ''' zl</span>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:0.82rem;margin-bottom:6px">
            <span style="color:var(--text-muted)">Przychod:</span>
            <span style="font-weight:700;color:var(--blue)">''' + f'{total_przychod:,.0f}' + ''' zl</span>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:0.88rem">
            <span style="color:var(--text);font-weight:600">Zysk netto:</span>
            <span style="font-weight:800;color:''' + ('var(--green)' if total_zysk>=0 else 'var(--red)') + '''">''' + f'{total_zysk:,.0f}' + ''' zl</span>
        </div>
    </div>
</div>
</div>

<!-- WYKRESY -->
<div class="section-title">Wykresy</div>
<div class="dash-grid">
    <div class="card">
        <div class="card-header"><div class="card-title">Zysk miesiecznie</div></div>
        ''' + monthly_chart + '''
        <div style="font-size:0.7rem;color:var(--text-muted);margin-top:10px">
            Niebieski = przychod | Czerwony = koszty | Wartosc = zysk netto
        </div>
    </div>
    <div class="card">
        <div class="card-header">
            <div class="card-title">Przychod dzienny</div>
            <span class="badge" style="background:var(--accent-soft);color:var(--accent)">30 dni</span>
        </div>
        <div style="max-height:420px;overflow-y:auto">
            ''' + daily_chart + '''
        </div>
    </div>
</div>

<!-- TABELA P&L -->
<div class="section-title">P&amp;L Miesiecznie</div>
<div class="card">
    <div style="overflow-x:auto">
        <table>
            <tr>
                <th>Miesiac</th><th>Przychod</th><th>COGS</th><th>Prowizja</th>
                <th>Koszty op.</th><th>Zysk netto</th><th>Marza</th>
                <th>Zam.</th><th>Zwroty</th>
            </tr>
            ''' + monthly_rows + '''
            <tr style="border-top:2px solid var(--accent);font-weight:700;background:var(--accent-soft)">
                <td>SUMA</td>
                <td style="color:var(--blue)">''' + f'{total_przychod:,.0f}' + '''</td>
                <td>''' + f'{total_cogs:,.0f}' + '''</td>
                <td>''' + f'{total_prowizja:,.0f}' + '''</td>
                <td>''' + f'{sum(m["koszty_op"] for m in monthly_data):,.0f}' + '''</td>
                <td style="color:''' + ('var(--green)' if total_zysk>=0 else 'var(--red)') + '''">''' + f'{total_zysk:,.0f}' + '''</td>
                <td>''' + f'{avg_marza:.1f}' + '''%</td>
                <td>''' + f'{total_zamowienia}' + '''</td>
                <td style="color:var(--red)">''' + f'{total_zwroty}' + '''</td>
            </tr>
        </table>
    </div>
</div>

<!-- TOP PRODUKTY -->
<div class="section-title">Analiza produktow</div>
<div class="dash-grid">
    <div class="card">
        <div class="card-header">
            <div class="card-title">TOP produkty wg przychodu</div>
            <span class="badge" style="background:var(--accent-soft);color:var(--accent)">''' + f'{len(top_produkty_profit)}' + '''</span>
        </div>
        <div style="overflow-x:auto">
            <table>
                <tr><th>#</th><th>Produkt</th><th>Szt.</th><th>Przychod</th><th>Zysk</th><th>Marza</th></tr>
                ''' + top_prod_rows + '''
            </table>
        </div>
    </div>
    <div class="card">
        <div class="card-header">
            <div class="card-title">Stojace produkty</div>
            <span class="badge badge-error">''' + f'{len(stojace_produkty)}' + '''</span>
        </div>
        <div style="overflow-x:auto">
            <table>
                <tr><th>Produkt</th><th>Dostawca</th><th>Dni</th><th>Ilosc</th><th>Wartosc</th><th>Regal</th></tr>
                ''' + stojace_rows + '''
            </table>
        </div>
        ''' + (f'<div class="alert alert-error" style="margin-top:12px">Wartosc zamrozona w stojacych produktach: <b>{sum((s["cena_allegro"] or 0)*(s["ilosc"] or 1) for s in stojace_produkty):,.0f} zl</b></div>' if stojace_produkty else '') + '''
    </div>
</div>

<!-- KATEGORIE -->
''' + (f'''
<div class="card">
    <div class="card-header">
        <div class="card-title">Przychod wg kategorii</div>
        <span class="badge" style="background:var(--accent-soft);color:var(--accent)">90 dni</span>
    </div>
    {kat_bars}
</div>
''' if top_kat else '') + '''

<!-- DOSTAWCY -->
<div class="section-title">Dostawcy</div>
<div class="card">
    <div class="card-header"><div class="card-title">Rentownosc dostawcow</div></div>
    <div style="overflow-x:auto">
        <table>
            <tr><th>Dostawca</th><th>Palet</th><th>Inwestycja</th><th>Przychod</th><th>Zysk</th><th>ROI</th><th>Udzial</th></tr>
            ''' + dostawcy_rows + '''
        </table>
    </div>
</div>

<!-- PALETY -->
<div class="section-title">Rentownosc palet</div>
<div class="card">
    <div class="card-header">
        <div class="card-title">Analiza palet</div>
        <span class="badge" style="background:var(--accent-soft);color:var(--accent)">ostatnie 20</span>
    </div>
    <div style="overflow-x:auto">
        <table>
            <tr><th>Paleta</th><th>Koszt</th><th>Przychod</th><th>Zysk</th><th>ROI</th><th>Sprzedane</th><th>Tempo</th><th>Status</th></tr>
            ''' + palety_rows + '''
        </table>
    </div>
</div>

<!-- MAGAZYN -->
<div class="section-title">Magazyn</div>
<div class="card">
    <div class="stat-row" style="grid-template-columns:repeat(5,1fr)">
        <div class="stat-box">
            <div class="stat-val">''' + f'{magazyn["produkty"] or 0}' + '''</div>
            <div class="stat-lbl">Produktow</div>
        </div>
        <div class="stat-box">
            <div class="stat-val">''' + f'{magazyn["sztuki"] or 0}' + '''</div>
            <div class="stat-lbl">Sztuk</div>
        </div>
        <div class="stat-box">
            <div class="stat-val blue">''' + f'{magazyn["wystawione"] or 0}' + '''</div>
            <div class="stat-lbl">Wystawionych</div>
        </div>
        <div class="stat-box">
            <div class="stat-val orange">''' + f'{magazyn["wartosc_potencjalna"] or 0:,.0f}' + ''' zl</div>
            <div class="stat-lbl">Wartosc potencjalna</div>
        </div>
        <div class="stat-box">
            <div class="stat-val red">''' + f'{magazyn["stojace"] or 0}' + '''</div>
            <div class="stat-lbl">Stojace &gt;30 dni</div>
        </div>
    </div>
</div>

<!-- AI REKOMENDACJE -->
<div class="section-title">Rekomendacje AI</div>
<div class="card" id="ai-rec-card">
    <div class="card-header">
        <div class="card-title">Analiza i porady biznesowe</div>
        <button class="btn btn-primary" id="ai-rec-btn" onclick="getAiRecommendations()" style="padding:8px 18px;font-size:0.82rem;font-weight:600">
            Generuj rekomendacje
        </button>
    </div>
    <div id="ai-rec-content" style="display:none;margin-top:12px">
        <div id="ai-rec-loading" style="text-align:center;padding:20px;color:var(--text-muted)">
            <div style="font-size:1.5rem;margin-bottom:8px;animation:pulse 1.5s infinite">...</div>
            <div>Analizuje dane sprzedazowe...</div>
        </div>
        <div id="ai-rec-result" style="display:none;line-height:1.7;font-size:0.88rem"></div>
    </div>
</div>

<script>
function getAiRecommendations() {
    var btn = document.getElementById('ai-rec-btn');
    var content = document.getElementById('ai-rec-content');
    var loading = document.getElementById('ai-rec-loading');
    var result = document.getElementById('ai-rec-result');
    btn.disabled = true;
    btn.textContent = 'Analizuje...';
    content.style.display = 'block';
    loading.style.display = 'block';
    result.style.display = 'none';
    fetch('/analytics/ai-recommendations?months=''' + str(months_range) + '''')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            loading.style.display = 'none';
            result.style.display = 'block';
            if (data.ok) {
                result.innerHTML = data.html;
            } else {
                result.innerHTML = '<div class="alert alert-error">' + (data.error || 'Blad') + '</div>';
            }
            btn.textContent = 'Odswież rekomendacje';
            btn.disabled = false;
        })
        .catch(function(e) {
            loading.style.display = 'none';
            result.style.display = 'block';
            result.innerHTML = '<div class="alert alert-error">Blad: ' + e.message + '</div>';
            btn.textContent = 'Sprobuj ponownie';
            btn.disabled = false;
        });
}
</script>

<div style="text-align:center;padding:24px;color:var(--text-muted);font-size:0.72rem">
    Profit Analyzer v2.1 | Akces Hub | Dane z bazy na zywo
</div>

{% endblock %}
'''
    return render_template_string(html,
        version=current_app.config.get('VERSION', ''),
        brand_name=current_app.config.get('BRAND_NAME', 'Akces Hub'),
        current_user=session.get('user')
    )


@analytics_bp.route('/ai-recommendations')
def ai_recommendations():
    """Endpoint AI: rekomendacje biznesowe na podstawie danych sprzedażowych"""
    import requests as _req
    import re as _re

    conn = get_db()
    from .database import get_config

    api_key = get_config('gemini_api_key', '')
    if not api_key:
        api_key = get_config('perplexity_api_key', '')
        provider = 'perplexity'
    else:
        provider = 'gemini'

    if not api_key:
        return jsonify({'ok': False, 'error': 'Brak klucza API (Gemini lub Perplexity). Ustaw w Ustawieniach.'})

    months_range = int(request.args.get('months', 6))

    # Zbierz dane do analizy
    top_prod = conn.execute('''
        SELECT COALESCE(NULLIF(p.nazwa,''), s.nazwa, 'Produkt') as nazwa,
               SUM(s.cena * s.ilosc) as przychod, SUM(s.ilosc) as szt,
               COALESCE(p.kategoria, '') as kategoria
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
        AND date(s.data_sprzedazy) >= date('now', ?)
        GROUP BY COALESCE(p.id, s.nazwa)
        ORDER BY przychod DESC LIMIT 10
    ''', (f'-{months_range * 30} days',)).fetchall()

    stojace = conn.execute('''
        SELECT p.nazwa, p.cena_allegro, p.ilosc,
               julianday('now') - julianday(p.data_dodania) as dni,
               COALESCE(pal.dostawca, '-') as dostawca
        FROM produkty p
        LEFT JOIN palety pal ON p.paleta_id = pal.id
        WHERE p.status IN ('magazyn','wystawiony')
        AND p.data_dodania IS NOT NULL
        ORDER BY dni DESC LIMIT 10
    ''').fetchall()

    dostawcy = conn.execute('''
        SELECT COALESCE(NULLIF(pal.dostawca,''), 'Nieznany') as dostawca,
               COUNT(DISTINCT pal.id) as palet,
               SUM(pal.cena_zakupu) as inwestycja,
               COALESCE((SELECT SUM(s.cena * s.ilosc) FROM sprzedaze s
                         JOIN produkty pp ON s.produkt_id = pp.id
                         WHERE pp.paleta_id = pal.id
                         AND s.status NOT IN ('zwrot','anulowane','anulowana')), 0) as przychod
        FROM palety pal WHERE pal.cena_zakupu > 0
        GROUP BY COALESCE(NULLIF(pal.dostawca,''), 'Nieznany')
        ORDER BY przychod DESC
    ''').fetchall()

    kategorie = conn.execute('''
        SELECT COALESCE(NULLIF(p.kategoria,''), 'Brak') as kat,
               COUNT(*) as cnt, SUM(s.cena * s.ilosc) as wartosc
        FROM sprzedaze s JOIN produkty p ON s.produkt_id = p.id
        WHERE s.status NOT IN ('zwrot','anulowane','anulowana')
        AND date(s.data_sprzedazy) >= date('now', ?)
        GROUP BY kat ORDER BY wartosc DESC LIMIT 8
    ''', (f'-{months_range * 30} days',)).fetchall()

    zwroty = conn.execute('''
        SELECT COALESCE(s.nazwa, p.nazwa, '?') as nazwa, COUNT(*) as cnt,
               SUM(s.cena * s.ilosc) as wartosc
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        WHERE s.status = 'zwrot'
        AND date(s.data_sprzedazy) >= date('now', ?)
        GROUP BY COALESCE(s.nazwa, p.nazwa)
        ORDER BY cnt DESC LIMIT 5
    ''', (f'-{months_range * 30} days',)).fetchall()

    data_summary = f"Dane z ostatnich {months_range} miesiecy sprzedazy na Allegro.\n\n"
    data_summary += "TOP 10 produktow wg przychodu:\n"
    for p in top_prod:
        data_summary += f"- {p['nazwa']}: {p['przychod']:.0f} zl, {p['szt']} szt., kat: {p['kategoria'] or 'brak'}\n"
    data_summary += "\nProdukty stojace w magazynie (najdluzej):\n"
    for s in stojace:
        data_summary += f"- {s['nazwa']}: {s['dni']:.0f} dni, cena {s['cena_allegro'] or 0:.0f} zl, {s['ilosc'] or 1} szt, dostawca: {s['dostawca']}\n"
    data_summary += "\nDostawcy (inwestycja vs przychod):\n"
    for d in dostawcy:
        roi = ((d['przychod'] - d['inwestycja']) / d['inwestycja'] * 100) if d['inwestycja'] > 0 else 0
        data_summary += f"- {d['dostawca']}: {d['palet']} palet, inwestycja {d['inwestycja']:.0f} zl, przychod {d['przychod']:.0f} zl, ROI {roi:.0f}%\n"
    data_summary += "\nKategorie wg przychodu:\n"
    for k in kategorie:
        data_summary += f"- {k['kat']}: {k['wartosc']:.0f} zl ({k['cnt']} sprzedazy)\n"
    if zwroty:
        data_summary += "\nNajczesciej zwracane:\n"
        for z in zwroty:
            data_summary += f"- {z['nazwa']}: {z['cnt']}x zwrot, wartosc {z['wartosc']:.0f} zl\n"

    prompt = f"""Jestes doradca biznesowym dla sprzedawcy palet zwrotowych na Allegro (Polska).
Na podstawie ponizszych danych, daj KONKRETNE, praktyczne rekomendacje:

1. **CO KUPOWAC WIECEJ** - jakie kategorie/typy produktow przynosza najlepszy ROI
2. **CO ODPUSCIC** - jakie produkty/kategorie unikac, co stoi za dlugo
3. **DOSTAWCY** - ktorych preferowac, ktorych unikac
4. **CENY** - czy warto podniesc/obnizyc na konkretne produkty
5. **ZWROTY** - jak zmniejszyc, ktore produkty problematyczne
6. **AKCJE NA TEN TYDZIEN** - 3-5 konkretnych krokow

Odpowiedz po polsku, konkretnie i krotko. Nie powtarzaj danych - analizuj i doradzaj.
Uzyj formatowania markdown (## naglowki, **bold**, listy z - ).

{data_summary}"""

    try:
        if provider == 'gemini':
            model = get_config('gemini_model', 'gemini-2.0-flash')
            resp = _req.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"maxOutputTokens": 4000, "temperature": 0.4}},
                timeout=60)
            data = resp.json()
            if 'error' in data:
                return jsonify({'ok': False, 'error': f"Gemini: {data['error'].get('message', str(data['error']))}"})
            if 'candidates' not in data or not data['candidates']:
                return jsonify({'ok': False, 'error': 'Gemini: brak odpowiedzi'})
            answer = data['candidates'][0]['content']['parts'][0]['text']
        else:
            resp = _req.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "sonar", "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 4000, "temperature": 0.4},
                timeout=60)
            data = resp.json()
            answer = data['choices'][0]['message']['content']

        # Konwertuj markdown na HTML
        import html as _html
        answer_safe = _html.escape(answer)
        answer_safe = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', answer_safe)
        answer_safe = _re.sub(r'^### (.+)$', r'<h4 style="color:var(--accent);margin:16px 0 8px">\1</h4>', answer_safe, flags=_re.MULTILINE)
        answer_safe = _re.sub(r'^## (.+)$', r'<h3 style="color:var(--accent);margin:16px 0 8px">\1</h3>', answer_safe, flags=_re.MULTILINE)
        answer_safe = _re.sub(r'^# (.+)$', r'<h3 style="color:var(--accent);margin:16px 0 8px">\1</h3>', answer_safe, flags=_re.MULTILINE)
        answer_safe = _re.sub(r'^- (.+)$', '<div style="padding:4px 0 4px 16px;border-left:2px solid var(--border)">\u2022 \\1</div>', answer_safe, flags=_re.MULTILINE)
        answer_safe = _re.sub(r'^\d+\. (.+)$', r'<div style="padding:4px 0 4px 16px;border-left:2px solid var(--accent);margin-bottom:4px">\1</div>', answer_safe, flags=_re.MULTILINE)
        answer_safe = answer_safe.replace('\n\n', '<br><br>')
        answer_safe = answer_safe.replace('\n', '<br>')

        return jsonify({'ok': True, 'html': answer_safe})

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})
