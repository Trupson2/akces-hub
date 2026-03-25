"""
Moduł raportów email - wysyłanie tygodniowych podsumowań
"""

import smtplib
import json
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from pathlib import Path

# Ścieżka do konfiguracji
CONFIG_FILE = Path(__file__).parent.parent / 'email_config.json'

def get_email_config():
    """Pobiera konfigurację email"""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {
        'smtp_server': 'smtp.gmail.com',
        'smtp_port': 587,
        'email': '',
        'password': '',  # App password dla Gmail
        'recipient': '',
        'enabled': False
    }

def save_email_config(config):
    """Zapisuje konfigurację email"""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

def send_email(subject, html_content, recipient=None):
    """Wysyła email z raportem"""
    config = get_email_config()
    
    if not config.get('enabled') or not config.get('email') or not config.get('password'):
        return False, "Email nie skonfigurowany"
    
    recipient = recipient or config.get('recipient') or config.get('email')
    
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = config['email']
        msg['To'] = recipient
        
        # Dodaj HTML
        html_part = MIMEText(html_content, 'html', 'utf-8')
        msg.attach(html_part)
        
        # Połącz i wyślij
        with smtplib.SMTP(config['smtp_server'], config['smtp_port']) as server:
            server.starttls()
            server.login(config['email'], config['password'])
            server.sendmail(config['email'], recipient, msg.as_string())
        
        return True, "Email wysłany"
    except Exception as e:
        return False, str(e)

def generate_weekly_report():
    """Generuje raport tygodniowy"""
    from .database import get_db, get_config
    
    conn = get_db()
    
    # Daty
    today = datetime.now()
    week_ago = (today - timedelta(days=7)).strftime('%Y-%m-%d')
    prev_week_start = (today - timedelta(days=14)).strftime('%Y-%m-%d')
    prev_week_end = (today - timedelta(days=7)).strftime('%Y-%m-%d')
    
    # === STATYSTYKI TYGODNIA ===
    week_stats = conn.execute('''
        SELECT 
            COUNT(*) as zamowienia,
            COALESCE(SUM(cena * ilosc), 0) as przychod,
            COUNT(CASE WHEN status = 'zwrot' THEN 1 END) as zwroty
        FROM sprzedaze 
        WHERE date(data_sprzedazy) >= ?
    ''', (week_ago,)).fetchone()
    
    # Poprzedni tydzień (do porównania)
    prev_week = conn.execute('''
        SELECT 
            COUNT(*) as zamowienia,
            COALESCE(SUM(cena * ilosc), 0) as przychod
        FROM sprzedaze 
        WHERE date(data_sprzedazy) >= ? AND date(data_sprzedazy) < ?
        AND status NOT IN ('zwrot', 'anulowane')
    ''', (prev_week_start, prev_week_end)).fetchone()
    
    # === KOSZTY (z produktów SPRZEDANYCH w tym tygodniu) ===
    koszty = conn.execute('''
        SELECT COALESCE(SUM(
            CASE 
                WHEN p.cena_brutto > 0 THEN p.cena_brutto * s.ilosc
                WHEN p.cena_netto > 0 THEN p.cena_netto * 1.23 * s.ilosc
                WHEN p2.cena_brutto > 0 THEN p2.cena_brutto * s.ilosc
                WHEN p2.cena_netto > 0 THEN p2.cena_netto * 1.23 * s.ilosc
                ELSE 0
            END
        ), 0) as koszt
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        LEFT JOIN oferty o ON s.oferta_id = o.id
        LEFT JOIN produkty p2 ON o.produkt_id = p2.id
        WHERE date(s.data_sprzedazy) >= ?
        AND s.status NOT IN ('zwrot', 'anulowane')
    ''', (week_ago,)).fetchone()['koszt']
    
    przychod = week_stats['przychod'] or 0
    prowizja = przychod * 0.11
    zysk = przychod - koszty - prowizja
    
    # Trend
    prev_przychod = prev_week['przychod'] or 1
    trend = ((przychod - prev_przychod) / prev_przychod * 100) if prev_przychod > 0 else 0
    trend_icon = "<span class="material-symbols-outlined">trending_up</span>" if trend > 0 else "<span class="material-symbols-outlined">trending_down</span>" if trend < 0 else "➡"
    trend_color = "#22c55e" if trend > 0 else "#ef4444" if trend < 0 else "#64748b"
    
    # === TOP 5 PRODUKTÓW ===
    top_produkty = conn.execute('''
        SELECT 
            CASE 
                WHEN s.nazwa IS NOT NULL AND s.nazwa != '' THEN SUBSTR(s.nazwa, 1, 40)
                ELSE 'Produkt #' || s.id
            END as nazwa,
            COUNT(*) as sprzedane,
            SUM(s.cena * s.ilosc) as wartosc
        FROM sprzedaze s
        WHERE date(s.data_sprzedazy) >= ?
        AND s.status NOT IN ('zwrot', 'anulowane')
        GROUP BY nazwa
        ORDER BY sprzedane DESC
        LIMIT 5
    ''', (week_ago,)).fetchall()
    
    # === TOP DOSTAWCY ===
    top_dostawcy = conn.execute('''
        SELECT 
            COALESCE(pal.dostawca, 'Allegro') as dostawca_nazwa,
            COUNT(*) as sprzedane,
            SUM(s.cena * s.ilosc) as przychod
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        LEFT JOIN palety pal ON p.paleta_id = pal.id
        WHERE date(s.data_sprzedazy) >= ?
        AND s.status NOT IN ('zwrot', 'anulowane')
        GROUP BY dostawca_nazwa
        ORDER BY przychod DESC
        LIMIT 3
    ''', (week_ago,)).fetchall()
    
    # === STOJAKI (produkty > 30 dni) ===
    stojaki = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena_brutto), 0) as wartosc
        FROM produkty 
        WHERE status = 'magazyn' 
        AND date(data_dodania) <= date('now', '-30 days')
    ''').fetchone()

    # === STAN MAGAZYNU ===
    magazyn = conn.execute('''
        SELECT COUNT(*) as produkty, COALESCE(SUM(ilosc), 0) as sztuki
        FROM produkty WHERE status IN ('magazyn', 'wystawiony')
    ''').fetchone()

    # === GENERUJ HTML ===
    top_produkty_html = ""
    for i, p in enumerate(top_produkty):
        medal = "<span class="material-symbols-outlined">emoji_events</span>" if i == 0 else "<span class="material-symbols-outlined">emoji_events</span>" if i == 1 else "<span class="material-symbols-outlined">emoji_events</span>" if i == 2 else f"{i+1}."
        top_produkty_html += f'''
        <tr>
            <td style="padding:8px;border-bottom:1px solid #eee">{medal}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{p['nazwa']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;text-align:center">{p['sprzedane']} szt</td>
            <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;color:#22c55e;font-weight:600">{p['wartosc']:.0f} zł</td>
        </tr>
        '''
    
    top_dostawcy_html = ""
    for d in top_dostawcy:
        top_dostawcy_html += f'''
        <tr>
            <td style="padding:8px;border-bottom:1px solid #eee">{d['dostawca_nazwa']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;text-align:center">{d['sprzedane']} szt</td>
            <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;color:#22c55e">{d['przychod']:.0f} zł</td>
        </tr>
        '''
    
    html = f'''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8fafc; margin: 0; padding: 20px; }}
        .container {{ max-width: 600px; margin: 0 auto; background: #fff; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); overflow: hidden; }}
        .header {{ background: linear-gradient(135deg, #3b82f6, #8b5cf6); color: white; padding: 30px; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 24px; }}
        .header p {{ margin: 10px 0 0; opacity: 0.9; }}
        .content {{ padding: 25px; }}
        .stats {{ display: flex; gap: 15px; margin-bottom: 25px; }}
        .stat-box {{ flex: 1; background: #f8fafc; border-radius: 10px; padding: 15px; text-align: center; }}
        .stat-value {{ font-size: 28px; font-weight: 700; color: #1e293b; }}
        .stat-label {{ font-size: 12px; color: #64748b; margin-top: 5px; }}
        .section {{ margin-bottom: 25px; }}
        .section-title {{ font-size: 16px; font-weight: 600; color: #1e293b; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th {{ text-align: left; padding: 10px 8px; background: #f8fafc; font-size: 12px; color: #64748b; }}
        .trend {{ display: inline-block; padding: 4px 10px; border-radius: 20px; font-size: 13px; font-weight: 600; }}
        .alert {{ background: #fef3c7; border-left: 4px solid #f59e0b; padding: 12px; border-radius: 0 8px 8px 0; margin-bottom: 20px; }}
        .footer {{ background: #f8fafc; padding: 20px; text-align: center; color: #64748b; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1><span class="material-symbols-outlined">bar_chart</span> Raport Tygodniowy</h1>
            <p>{(today - timedelta(days=7)).strftime('%d.%m')} - {today.strftime('%d.%m.%Y')}</p>
        </div>
        
        <div class="content">
            <!-- Główne statystyki -->
            <div class="stats">
                <div class="stat-box">
                    <div class="stat-value" style="color:#22c55e">{przychod:.0f} zł</div>
                    <div class="stat-label">PRZYCHÓD</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value" style="color:#3b82f6">{week_stats['zamowienia']}</div>
                    <div class="stat-label">ZAMÓWIEŃ</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value" style="color:{'#22c55e' if zysk > 0 else '#ef4444'}">{zysk:.0f} zł</div>
                    <div class="stat-label">ZYSK NETTO</div>
                </div>
            </div>
            
            <!-- Trend -->
            <div style="text-align:center;margin-bottom:25px">
                <span class="trend" style="background:{trend_color}20;color:{trend_color}">
                    {trend_icon} {trend:+.1f}% vs poprzedni tydzień
                </span>
            </div>
            
            <!-- Alert o zwrotach -->
            {f'<div class="alert"><span class="material-symbols-outlined">warning</span> <strong>{week_stats["zwroty"]} zwrotów</strong> w tym tygodniu</div>' if week_stats['zwroty'] > 0 else ''}
            
            <!-- TOP 5 Produktów -->
            <div class="section">
                <div class="section-title"><span class="material-symbols-outlined">emoji_events</span> TOP 5 Produktów</div>
                <table>
                    <tr>
                        <th style="width:30px"></th>
                        <th>Produkt</th>
                        <th style="text-align:center">Ilość</th>
                        <th style="text-align:right">Wartość</th>
                    </tr>
                    {top_produkty_html if top_produkty_html else '<tr><td colspan="4" style="text-align:center;padding:20px;color:#64748b">Brak sprzedaży</td></tr>'}
                </table>
            </div>
            
            <!-- TOP Dostawcy -->
            <div class="section">
                <div class="section-title"><span class="material-symbols-outlined">inventory_2</span> Dostawcy</div>
                <table>
                    <tr>
                        <th>Dostawca</th>
                        <th style="text-align:center">Sprzedano</th>
                        <th style="text-align:right">Przychód</th>
                    </tr>
                    {top_dostawcy_html if top_dostawcy_html else '<tr><td colspan="3" style="text-align:center;padding:20px;color:#64748b">Brak danych</td></tr>'}
                </table>
            </div>
            
            <!-- Podsumowanie -->
            <div class="section">
                <div class="section-title"><span class="material-symbols-outlined">assignment</span> Podsumowanie</div>
                <table>
                    <tr>
                        <td style="padding:8px 0;color:#64748b">Koszty zakupu</td>
                        <td style="padding:8px 0;text-align:right;font-weight:600">{koszty:.0f} zł</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 0;color:#64748b">Prowizja Allegro (11%)</td>
                        <td style="padding:8px 0;text-align:right;font-weight:600">{prowizja:.0f} zł</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 0;color:#64748b">Na magazynie</td>
                        <td style="padding:8px 0;text-align:right;font-weight:600">{magazyn['produkty']} prod. ({magazyn['sztuki']} szt)</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 0;color:#64748b">Stojaki (&gt;30 dni)</td>
                        <td style="padding:8px 0;text-align:right;font-weight:600;color:#ef4444">{stojaki['cnt']} szt ({stojaki['wartosc']:.0f} zł)</td>
                    </tr>
                </table>
            </div>
        </div>
        
        <div class="footer">
            Wygenerowano automatycznie przez {get_config('brand_name', 'Akces Hub')}<br>
            {today.strftime('%d.%m.%Y %H:%M')}
        </div>
    </div>
</body>
</html>
'''
    
    return html

def send_weekly_report():
    """Wysyła raport tygodniowy"""
    config = get_email_config()
    if not config.get('enabled'):
        return False, "Email wyłączony"

    from .database import get_config
    html = generate_weekly_report()
    today = datetime.now()
    subject = f"Raport tygodniowy {get_config('brand_name', 'Akces Hub')} - {today.strftime('%d.%m.%Y')}"

    return send_email(subject, html)


def generate_daily_report():
    """Generuje dzienny raport poranny z analiza palet"""
    from .database import get_db

    conn = get_db()
    today = datetime.now()
    yesterday = (today - timedelta(days=1)).strftime('%Y-%m-%d')
    month_start = today.strftime('%Y-%m-01')

    # === WCZORAJSZA SPRZEDAZ ===
    wczoraj = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc), 0) as suma
        FROM sprzedaze
        WHERE date(data_sprzedazy) = ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')
       
    ''', (yesterday,)).fetchone()

    # === MIESIAC ===
    miesiac = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc), 0) as suma
        FROM sprzedaze
        WHERE date(data_sprzedazy) >= ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')
       
    ''', (month_start,)).fetchone()

    # === ANALIZA PALET ===
    palety = conn.execute('''
        SELECT
            pal.id, pal.nazwa, pal.dostawca, pal.cena_zakupu,
            COUNT(p.id) as produktow_total,
            SUM(CASE WHEN p.status IN ('magazyn', 'wystawiony') THEN 1 ELSE 0 END) as na_magazynie,
            SUM(CASE WHEN p.status = 'sprzedany' THEN 1 ELSE 0 END) as sprzedanych,
            COALESCE(SUM(CASE WHEN p.status = 'sprzedany' THEN p.cena_allegro ELSE 0 END), 0) as przychod_z_palety,
            pal.data_zakupu
        FROM palety pal
        LEFT JOIN produkty p ON p.paleta_id = pal.id
        GROUP BY pal.id
        ORDER BY pal.data_zakupu DESC
        LIMIT 10
    ''').fetchall()

    # === DO WYSLANIA ===
    do_wyslania = conn.execute('''
        SELECT COUNT(*) as cnt FROM sprzedaze
        WHERE status = 'oplacone'
    ''').fetchone()['cnt']

    # === STOJAKI > 30 DNI ===
    stojaki = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena_brutto), 0) as wartosc
        FROM produkty
        WHERE status IN ('magazyn', 'wystawiony')
        AND date(data_dodania) <= date('now', '-30 days')
    ''').fetchone()

    # === MAGAZYN ===
    magazyn = conn.execute('''
        SELECT COUNT(*) as produkty, COALESCE(SUM(ilosc), 0) as sztuki
        FROM produkty WHERE status IN ('magazyn', 'wystawiony')
    ''').fetchone()

    # === TOP OKAZJE (z tabeli trendy) ===
    top_okazje = []
    try:
        top_okazje = conn.execute('''
            SELECT t.nazwa, t.kategoria, t.dostawca, t.sprzedaz_szt, t.przychod,
                   t.roi, t.trend_mm, t.okazja_score
            FROM trendy t
            WHERE t.okazja_score >= 4
            ORDER BY t.okazja_score DESC, t.przychod DESC
            LIMIT 5
        ''').fetchall()
    except:
        pass

    # === NOWE PALETY Z WARRINGTON / JOBALOTS ===
    nowe_palety = []
    try:
        import requests as _req
        # Warrington
        try:
            resp = _req.get('http://localhost:5000/analityka/okazje/scrape-warrington', timeout=30)
            if resp.status_code == 200:
                data = resp.json() if resp.headers.get('content-type','').startswith('application/json') else {}
                for p in (data.get('products') or data.get('items') or [])[:5]:
                    nowe_palety.append({
                        'nazwa': p.get('title') or p.get('name') or p.get('nazwa','?'),
                        'cena': p.get('price_text') or p.get('price') or p.get('cena','?'),
                        'zrodlo': 'Warrington',
                        'url': p.get('url') or p.get('link','')
                    })
        except: pass
        # Jobalots
        try:
            resp = _req.get('http://localhost:5000/analityka/okazje/scrape-jobalots', timeout=30)
            if resp.status_code == 200:
                data = resp.json() if resp.headers.get('content-type','').startswith('application/json') else {}
                for p in (data.get('products') or data.get('items') or data.get('auctions') or [])[:5]:
                    nowe_palety.append({
                        'nazwa': p.get('title') or p.get('name') or p.get('nazwa','?'),
                        'cena': p.get('price_text') or p.get('bid') or p.get('cena','?'),
                        'zrodlo': 'Jobalots',
                        'url': p.get('url') or p.get('link','')
                    })
        except: pass
    except: pass

    # === PALETY HTML ===
    palety_html = ""
    for pal in palety:
        przychod = float(pal['przychod_z_palety'] or 0)
        koszt = float(pal['cena_zakupu'] or 0)
        roi = ((przychod - koszt) / koszt * 100) if koszt > 0 else 0
        sprzedanych = int(pal['sprzedanych'] or 0)
        na_mag = int(pal['na_magazynie'] or 0)
        total = int(pal['produktow_total'] or 0)
        pct_sold = (sprzedanych / total * 100) if total > 0 else 0

        # Kolor ROI
        if roi > 50:
            roi_color = "#22c55e"
        elif roi > 0:
            roi_color = "#eab308"
        else:
            roi_color = "#ef4444"

        # Progress bar
        bar_width = min(pct_sold, 100)
        bar_color = "#22c55e" if pct_sold > 70 else "#eab308" if pct_sold > 40 else "#3b82f6"

        palety_html += f'''
        <tr>
            <td style="padding:10px 8px;border-bottom:1px solid #f1f5f9">
                <div style="font-weight:600;color:#1e293b">{pal['nazwa'] or 'Paleta #' + str(pal['id'])}</div>
                <div style="font-size:11px;color:#94a3b8">{pal['dostawca'] or '-'} | {pal['data_zakupu'] or '-'}</div>
            </td>
            <td style="padding:10px 8px;border-bottom:1px solid #f1f5f9;text-align:center">
                <div style="font-weight:600">{sprzedanych}/{total}</div>
                <div style="background:#f1f5f9;border-radius:4px;height:6px;margin-top:4px;overflow:hidden">
                    <div style="background:{bar_color};height:100%;width:{bar_width}%;border-radius:4px"></div>
                </div>
            </td>
            <td style="padding:10px 8px;border-bottom:1px solid #f1f5f9;text-align:center;font-size:12px;color:#64748b">{na_mag} szt</td>
            <td style="padding:10px 8px;border-bottom:1px solid #f1f5f9;text-align:right">
                <div style="font-weight:600">{koszt:.0f} / {przychod:.0f} zl</div>
                <div style="font-size:12px;font-weight:700;color:{roi_color}">ROI {roi:+.0f}%</div>
            </td>
        </tr>
        '''

    # === OKAZJE HTML ===
    okazje_html = ""
    for oz in top_okazje:
        score = int(oz['okazja_score'] or 0)
        roi_oz = float(oz['roi'] or 0)
        trend = float(oz['trend_mm'] or 0)
        score_color = "#22c55e" if score >= 8 else "#eab308" if score >= 6 else "#64748b"
        trend_arrow = "+" if trend > 0 else ""
        okazje_html += f'''
        <tr>
            <td style="padding:8px;border-bottom:1px solid #f1f5f9">
                <div style="font-weight:600;color:#1e293b">{oz['nazwa'][:40] if oz['nazwa'] else '-'}</div>
                <div style="font-size:11px;color:#94a3b8">{oz['kategoria'] or '-'} | {oz['dostawca'] or '-'}</div>
            </td>
            <td style="padding:8px;border-bottom:1px solid #f1f5f9;text-align:center">{oz['sprzedaz_szt']} szt</td>
            <td style="padding:8px;border-bottom:1px solid #f1f5f9;text-align:center;color:{'#22c55e' if roi_oz > 0 else '#ef4444'}">{roi_oz:.0f}%</td>
            <td style="padding:8px;border-bottom:1px solid #f1f5f9;text-align:center">
                <span style="display:inline-block;padding:2px 8px;border-radius:10px;background:{score_color};color:#fff;font-weight:700;font-size:12px">{score}/10</span>
            </td>
        </tr>
        '''

    wczoraj_suma = float(wczoraj['suma'] or 0)
    miesiac_suma = float(miesiac['suma'] or 0)

    MIESIACE_PL = {1:'Styczen',2:'Luty',3:'Marzec',4:'Kwiecien',5:'Maj',6:'Czerwiec',
                   7:'Lipiec',8:'Sierpien',9:'Wrzesien',10:'Pazdziernik',11:'Listopad',12:'Grudzien'}
    mies_nazwa = MIESIACE_PL.get(today.month, 'Miesiac')

    from .database import get_config
    brand_name = get_config('brand_name', 'AKCES HUB')

    html = f'''
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;margin:0;padding:20px">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden">

    <!-- HEADER -->
    <div style="background:linear-gradient(135deg,#0f172a,#1e1b2e);color:white;padding:25px;text-align:center">
        <div style="font-size:24px;font-weight:800">{brand_name}</div>
        <div style="font-size:13px;opacity:0.7;margin-top:5px">Raport poranny - {today.strftime('%d.%m.%Y')} ({['pon','wt','sr','czw','pt','sob','niedz'][today.weekday()]})</div>
    </div>

    <!-- QUICK STATS -->
    <div style="display:flex;text-align:center;border-bottom:1px solid #f1f5f9">
        <div style="flex:1;padding:18px 10px;border-right:1px solid #f1f5f9">
            <div style="font-size:24px;font-weight:800;color:#3b82f6">{wczoraj_suma:.0f} zl</div>
            <div style="font-size:11px;color:#94a3b8;margin-top:3px">WCZORAJ ({wczoraj['cnt']} zam.)</div>
        </div>
        <div style="flex:1;padding:18px 10px;border-right:1px solid #f1f5f9">
            <div style="font-size:24px;font-weight:800;color:#22c55e">{miesiac_suma:.0f} zl</div>
            <div style="font-size:11px;color:#94a3b8;margin-top:3px">{mies_nazwa.upper()} ({miesiac['cnt']} zam.)</div>
        </div>
        <div style="flex:1;padding:18px 10px">
            <div style="font-size:24px;font-weight:800;color:#f97316">{do_wyslania}</div>
            <div style="font-size:11px;color:#94a3b8;margin-top:3px">DO WYSLANIA</div>
        </div>
    </div>

    <!-- ALERT STOJAKI -->
    {'<div style="margin:15px 20px;padding:12px;background:#fef3c7;border-left:4px solid #f59e0b;border-radius:0 8px 8px 0;font-size:13px"><b>Uwaga:</b> ' + str(stojaki["cnt"]) + ' produktow stoi >30 dni (wartosc: ' + f'{stojaki["wartosc"]:.0f}' + ' zl)</div>' if stojaki['cnt'] > 5 else ''}

    <!-- ANALIZA PALET -->
    <div style="padding:20px">
        <div style="font-size:16px;font-weight:700;color:#1e293b;margin-bottom:12px">Analiza palet (ostatnie 10)</div>
        <table style="width:100%;border-collapse:collapse;font-size:13px">
            <tr style="background:#f8fafc">
                <th style="padding:8px;text-align:left;font-size:11px;color:#64748b">PALETA</th>
                <th style="padding:8px;text-align:center;font-size:11px;color:#64748b">SPRZEDANE</th>
                <th style="padding:8px;text-align:center;font-size:11px;color:#64748b">MAGAZYN</th>
                <th style="padding:8px;text-align:right;font-size:11px;color:#64748b">KOSZT / PRZYCHOD</th>
            </tr>
            {palety_html if palety_html else '<tr><td colspan="4" style="text-align:center;padding:20px;color:#94a3b8">Brak palet</td></tr>'}
        </table>
    </div>

    <!-- TOP OKAZJE -->
    {'<div style="padding:0 20px 20px"><div style="font-size:16px;font-weight:700;color:#1e293b;margin-bottom:12px">TOP Okazje (score 6+)</div><table style="width:100%;border-collapse:collapse;font-size:13px"><tr style="background:#fffbeb"><th style="padding:8px;text-align:left;font-size:11px;color:#64748b">PRODUKT</th><th style="padding:8px;text-align:center;font-size:11px;color:#64748b">SPRZEDAZ</th><th style="padding:8px;text-align:center;font-size:11px;color:#64748b">ROI</th><th style="padding:8px;text-align:center;font-size:11px;color:#64748b">SCORE</th></tr>' + okazje_html + '</table></div>' if okazje_html else ''}

    <!-- NOWE PALETY DO KUPIENIA -->
    {'<div style="padding:0 20px 20px"><div style="font-size:16px;font-weight:700;color:#1e293b;margin-bottom:12px">Nowe palety do kupienia</div><table style="width:100%;border-collapse:collapse;font-size:13px"><tr style="background:#f0fdf4"><th style="padding:8px;text-align:left;font-size:11px;color:#64748b">PALETA</th><th style="padding:8px;text-align:center;font-size:11px;color:#64748b">CENA</th><th style="padding:8px;text-align:center;font-size:11px;color:#64748b">ZRODLO</th></tr>' + ''.join(f'<tr><td style="padding:8px;border-bottom:1px solid #f1f5f9"><a href="{p["url"]}" style="color:#3b82f6;text-decoration:none;font-weight:600">{str(p["nazwa"])[:50]}</a></td><td style="padding:8px;border-bottom:1px solid #f1f5f9;text-align:center;font-weight:600;color:#22c55e">{p["cena"]}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9;text-align:center"><span style="background:#e0f2fe;color:#0369a1;padding:2px 8px;border-radius:10px;font-size:11px">{p["zrodlo"]}</span></td></tr>' for p in nowe_palety) + '</table></div>' if nowe_palety else ''}

    <!-- MAGAZYN SUMMARY -->
    <div style="padding:0 20px 20px">
        <div style="display:flex;gap:10px">
            <div style="flex:1;background:#f0f9ff;border-radius:8px;padding:12px;text-align:center">
                <div style="font-size:18px;font-weight:700;color:#3b82f6">{magazyn['produkty']}</div>
                <div style="font-size:11px;color:#64748b">produktow</div>
            </div>
            <div style="flex:1;background:#f0fdf4;border-radius:8px;padding:12px;text-align:center">
                <div style="font-size:18px;font-weight:700;color:#22c55e">{magazyn['sztuki']}</div>
                <div style="font-size:11px;color:#64748b">sztuk na magazynie</div>
            </div>
        </div>
    </div>

    <!-- FOOTER -->
    <div style="background:#f8fafc;padding:15px;text-align:center;color:#94a3b8;font-size:11px">
        Wygenerowano automatycznie przez {brand_name} na Raspberry Pi<br>
        {today.strftime('%d.%m.%Y %H:%M')}
    </div>
</div>
</body>
</html>
'''
    return html


def send_daily_report():
    """Wysyla dzienny raport poranny"""
    config = get_email_config()
    if not config.get('enabled'):
        return False, "Email wylaczony"

    from .database import get_config
    html = generate_daily_report()
    today = datetime.now()
    subject = f"{get_config('brand_name', 'Akces Hub')} - Raport poranny {today.strftime('%d.%m.%Y')}"

    return send_email(subject, html)
