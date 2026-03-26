"""
Obsidian Daily Tasks Generator
Generuje codzienne notatki z taskami na podstawie danych z Akces Hub.
Zapisuje jako .md w Obsidian vault (syncowany przez Syncthing).
"""

import sqlite3
import os
from datetime import datetime, timedelta

# Konfiguracja
VAULT_PATH = os.environ.get('OBSIDIAN_VAULT', '/home/pi/obsidian-vault')
DB_PATH = os.environ.get('AKCES_DB', '/home/pi/akces-hub/akces_hub.db')
DAILY_DIR = 'Daily'


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def gather_data():
    """Zbiera dane z bazy do generowania tasków"""
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    month = datetime.now().strftime('%Y-%m')
    weekday = datetime.now().weekday()  # 0=pon, 6=niedz

    data = {}

    # --- ZAMÓWIENIA DO WYSYŁKI ---
    data['do_wyslania'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM sprzedaze
        WHERE status IN ('nowa', 'sprzedana')
        AND status != 'zwrot'
    ''').fetchone()['cnt']

    # --- ZWROTY DO OBSŁUGI ---
    data['zwroty_nowe'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM sprzedaze
        WHERE status = 'zwrot'
        AND data_sprzedazy >= date('now', '-7 days')
    ''').fetchone()['cnt']

    # --- PRODUKTY BEZ OFERTY (do wystawienia) ---
    data['bez_oferty'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM produkty
        WHERE status = 'magazyn' AND ilosc > 0
        AND id NOT IN (SELECT DISTINCT produkt_id FROM oferty WHERE produkt_id IS NOT NULL)
    ''').fetchone()['cnt']

    # Top 5 produktów bez oferty (najdroższe)
    data['bez_oferty_top'] = conn.execute('''
        SELECT nazwa, cena_allegro, ilosc, kod_magazynowy FROM produkty
        WHERE status = 'magazyn' AND ilosc > 0
        AND id NOT IN (SELECT DISTINCT produkt_id FROM oferty WHERE produkt_id IS NOT NULL)
        ORDER BY COALESCE(cena_allegro, cena_brutto, 0) DESC
        LIMIT 5
    ''').fetchall()

    # --- PRODUKTY BEZ ZDJĘĆ ---
    data['bez_zdjec'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM produkty
        WHERE status = 'magazyn' AND ilosc > 0
        AND (zdjecie_url IS NULL OR zdjecie_url = '')
    ''').fetchone()['cnt']

    # --- PRODUKTY BEZ OPISU AI ---
    data['bez_opisu'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM produkty
        WHERE status = 'magazyn' AND ilosc > 0
        AND (opis_ai IS NULL OR opis_ai = '')
    ''').fetchone()['cnt']

    # --- SERWIS W TOKU ---
    data['serwis_aktywny'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM serwis
        WHERE status NOT IN ('zakonczone', 'anulowane')
    ''').fetchone()['cnt']

    data['serwis_items'] = conn.execute('''
        SELECT s.id, p.nazwa, s.status, s.opis_usterki, s.data_przyjecia
        FROM serwis s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        WHERE s.status NOT IN ('zakonczone', 'anulowane')
        ORDER BY s.data_przyjecia ASC
        LIMIT 5
    ''').fetchall()

    # --- PALETY NIEDOSTARCZONE ---
    data['palety_w_drodze'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM palety
        WHERE dostarczona = 0 OR dostarczona IS NULL
    ''').fetchone()['cnt']

    # --- STATYSTYKI MIESIĄCA ---
    stats = conn.execute('''
        SELECT
            COUNT(*) as zamowienia,
            COALESCE(SUM(CASE WHEN status != 'zwrot' THEN cena * ilosc ELSE 0 END), 0)
            - COALESCE(SUM(CASE WHEN status = 'zwrot' THEN cena * ilosc ELSE 0 END), 0) as przychod,
            COALESCE(SUM(CASE WHEN status = 'zwrot' THEN 1 ELSE 0 END), 0) as zwroty
        FROM sprzedaze
        WHERE strftime('%Y-%m', data_sprzedazy) = ?
    ''', (month,)).fetchone()
    data['month_orders'] = stats['zamowienia']
    data['month_revenue'] = stats['przychod']
    data['month_returns'] = stats['zwroty']

    # --- SPRZEDAŻ WCZORAJ ---
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    yst = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc), 0) as total
        FROM sprzedaze
        WHERE date(data_sprzedazy) = ? AND status != 'zwrot'
    ''', (yesterday,)).fetchone()
    data['yesterday_orders'] = yst['cnt']
    data['yesterday_revenue'] = yst['total']

    # --- SPRZEDAŻ DZIŚ ---
    tst = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc), 0) as total
        FROM sprzedaze
        WHERE date(data_sprzedazy) = ? AND status != 'zwrot'
    ''', (today,)).fetchone()
    data['today_orders'] = tst['cnt']
    data['today_revenue'] = tst['total']

    # --- STOCK OGÓLNY ---
    data['total_stock'] = conn.execute('''
        SELECT COALESCE(SUM(ilosc), 0) as cnt FROM produkty
        WHERE status = 'magazyn' AND ilosc > 0
    ''').fetchone()['cnt']

    data['total_products'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM produkty
        WHERE status = 'magazyn' AND ilosc > 0
    ''').fetchone()['cnt']

    # --- OFERTY AKTYWNE ---
    data['oferty_aktywne'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM oferty
        WHERE status = 'ACTIVE'
    ''').fetchone()['cnt']

    # --- OFERTY BEZ SPRZEDAŻY (>14 dni) ---
    data['oferty_martwe'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM oferty
        WHERE status = 'ACTIVE'
        AND data_wystawienia < date('now', '-14 days')
        AND id NOT IN (
            SELECT DISTINCT oferta_id FROM sprzedaze
            WHERE oferta_id IS NOT NULL
            AND data_sprzedazy > date('now', '-14 days')
        )
    ''').fetchone()['cnt']

    # --- CEL (Hyundai i30 N itp.) ---
    try:
        goal = conn.execute('SELECT target_amount, current_amount, name FROM goal LIMIT 1').fetchone()
        data['goal_target'] = goal['target_amount'] if goal else None
        data['goal_current'] = goal['current_amount'] if goal else 0
        data['goal_name'] = goal['name'] if goal else None
    except Exception:
        data['goal_target'] = None
        data['goal_current'] = 0
        data['goal_name'] = None

    data['weekday'] = weekday
    data['today'] = today
    data['month'] = month

    conn.close()
    return data


def generate_tasks(data):
    """Generuje listę tasków na podstawie danych"""
    tasks = []
    priority_tasks = []

    # --- PILNE (zawsze na górze) ---
    if data['do_wyslania'] > 0:
        priority_tasks.append(f"📦 **Wyślij {data['do_wyslania']} zamówień** — klienci czekają!")

    if data['zwroty_nowe'] > 0:
        priority_tasks.append(f"↩️ **Obsłuż {data['zwroty_nowe']} nowych zwrotów** z ostatnich 7 dni")

    # --- WYSTAWIANIE ---
    if data['bez_oferty'] > 0:
        n = min(data['bez_oferty'], 10)
        tasks.append(f"🏷️ Wystaw {n} produktów na Allegro (łącznie {data['bez_oferty']} bez oferty)")

    # --- CONTENT ---
    if data['bez_zdjec'] > 0:
        n = min(data['bez_zdjec'], 5)
        tasks.append(f"📸 Zrób zdjęcia {n} produktom (łącznie {data['bez_zdjec']} bez zdjęć)")

    if data['bez_opisu'] > 0:
        n = min(data['bez_opisu'], 5)
        tasks.append(f"✍️ Wygeneruj opisy AI dla {n} produktów ({data['bez_opisu']} łącznie)")

    # --- SERWIS ---
    if data['serwis_aktywny'] > 0:
        tasks.append(f"🔧 Sprawdź {data['serwis_aktywny']} produktów w serwisie")

    # --- PALETY ---
    if data['palety_w_drodze'] > 0:
        tasks.append(f"🚚 Sprawdź status {data['palety_w_drodze']} palet w drodze")

    # --- MARTWE OFERTY ---
    if data['oferty_martwe'] > 0:
        tasks.append(f"💀 Przejrzyj {data['oferty_martwe']} ofert bez sprzedaży >14 dni — obniż ceny?")

    # --- CYKLICZNE (dzień tygodnia) ---
    weekday = data['weekday']

    if weekday == 0:  # Poniedziałek
        tasks.append("📊 Poniedziałkowy przegląd — sprawdź wyniki weekendu")
        tasks.append("🎯 Ustaw cele na ten tydzień")

    if weekday == 4:  # Piątek
        tasks.append("📈 Piątkowe podsumowanie tygodnia")
        tasks.append("🔄 Sprawdź i uzupełnij stany magazynowe na weekend")

    # Pierwszy dzień miesiąca
    if data['today'].endswith('-01'):
        tasks.append("📅 Nowy miesiąc — ustaw cel sprzedażowy")
        tasks.append("💰 Podsumuj koszty i zysk z poprzedniego miesiąca")
        tasks.append("📦 Rozważ zamówienie nowych palet")

    return priority_tasks, tasks


def generate_markdown(data, priority_tasks, tasks):
    """Generuje Markdown daily note"""
    today = data['today']
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    day_names = ['Poniedziałek', 'Wtorek', 'Środa', 'Czwartek', 'Piątek', 'Sobota', 'Niedziela']
    day_name = day_names[data['weekday']]

    month_names = {
        '01': 'Styczeń', '02': 'Luty', '03': 'Marzec', '04': 'Kwiecień',
        '05': 'Maj', '06': 'Czerwiec', '07': 'Lipiec', '08': 'Sierpień',
        '09': 'Wrzesień', '10': 'Październik', '11': 'Listopad', '12': 'Grudzień'
    }
    month_name = month_names.get(data['month'][5:7], '')

    md = []
    md.append(f'---')
    md.append(f'date: {today}')
    md.append(f'tags: [daily, akces-hub, tasks]')
    md.append(f'---')
    md.append(f'')
    md.append(f'# {day_name}, {today}')
    md.append(f'')
    md.append(f'[[{yesterday}|← Wczoraj]] | [[{tomorrow}|Jutro →]]')
    md.append(f'')

    # --- STATYSTYKI ---
    md.append(f'## 📊 Dashboard')
    md.append(f'')
    md.append(f'| Metryka | Wartość |')
    md.append(f'|---------|--------|')
    md.append(f'| Wczoraj | **{data["yesterday_orders"]}** zamówień / **{data["yesterday_revenue"]:.0f} zł** |')
    md.append(f'| Dziś | **{data["today_orders"]}** zamówień / **{data["today_revenue"]:.0f} zł** |')
    md.append(f'| {month_name} | **{data["month_orders"]}** zamówień / **{data["month_revenue"]:.0f} zł** (zwroty: {data["month_returns"]}) |')

    if data.get('goal_target'):
        goal_progress = (data['goal_current'] / data['goal_target'] * 100) if data['goal_target'] > 0 else 0
        md.append(f'| 🎯 {data["goal_name"]} | **{data["goal_current"]:,.0f} / {data["goal_target"]:,.0f} zł** ({goal_progress:.0f}%) |')

    md.append(f'| Stock | **{data["total_stock"]}** szt / **{data["total_products"]}** produktów |')
    md.append(f'| Oferty aktywne | **{data["oferty_aktywne"]}** |')
    md.append(f'')

    # --- PILNE ---
    if priority_tasks:
        md.append(f'## 🔴 Pilne')
        md.append(f'')
        for t in priority_tasks:
            md.append(f'- [ ] {t}')
        md.append(f'')

    # --- TASKI ---
    md.append(f'## ✅ Zadania na dziś')
    md.append(f'')
    for t in tasks:
        md.append(f'- [ ] {t}')
    md.append(f'')

    # --- PRODUKTY DO WYSTAWIENIA ---
    if data['bez_oferty_top']:
        md.append(f'## 🏷️ Top produkty do wystawienia')
        md.append(f'')
        md.append(f'| Produkt | Cena | Szt | Kod |')
        md.append(f'|---------|------|-----|-----|')
        for p in data['bez_oferty_top']:
            cena = p['cena_allegro'] or 0
            md.append(f'| {(p["nazwa"] or "?")[:45]} | {cena:.0f} zł | {p["ilosc"]} | {p["kod_magazynowy"] or "-"} |')
        md.append(f'')

    # --- SERWIS ---
    if data['serwis_items']:
        md.append(f'## 🔧 Serwis')
        md.append(f'')
        for s in data['serwis_items']:
            nazwa = s['nazwa'] or 'Brak nazwy'
            md.append(f'- [ ] **{nazwa[:40]}** — {s["status"]} | {s["opis_usterki"] or "brak opisu"}')
        md.append(f'')

    # --- NOTATKI ---
    md.append(f'## 📝 Notatki')
    md.append(f'')
    md.append(f'')

    return '\n'.join(md)


def generate_daily_note(vault_path=None, db_path=None):
    """Główna funkcja — generuje daily note"""
    global VAULT_PATH, DB_PATH

    if vault_path:
        VAULT_PATH = vault_path
    if db_path:
        DB_PATH = db_path

    # Utwórz folder Daily
    daily_dir = os.path.join(VAULT_PATH, DAILY_DIR)
    os.makedirs(daily_dir, exist_ok=True)

    # Zbierz dane
    data = gather_data()

    # Wygeneruj taski
    priority_tasks, tasks = generate_tasks(data)

    # Wygeneruj markdown
    md = generate_markdown(data, priority_tasks, tasks)

    # Zapisz plik
    today = datetime.now().strftime('%Y-%m-%d')
    filepath = os.path.join(daily_dir, f'{today}.md')

    # Nie nadpisuj jeśli już istnieje (żeby nie stracić checkboxów)
    if os.path.exists(filepath):
        print(f'[OBSIDIAN] Notatka {today}.md już istnieje — pomijam')
        return filepath

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(md)

    print(f'[OBSIDIAN] Wygenerowano: {filepath}')

    # Git commit + push do GitHub (sync z Obsidian na telefonie)
    try:
        import subprocess
        subprocess.run(['git', 'add', '-A'], cwd=VAULT_PATH, capture_output=True)
        subprocess.run(['git', 'commit', '-m', f'Daily tasks {today}'], cwd=VAULT_PATH, capture_output=True)
        result = subprocess.run(['git', 'push'], cwd=VAULT_PATH, capture_output=True, text=True)
        if result.returncode == 0:
            print(f'[OBSIDIAN] Pushed to GitHub')
        else:
            print(f'[OBSIDIAN] Push failed: {result.stderr[:100]}')
    except Exception as e:
        print(f'[OBSIDIAN] Git sync error: {e}')

    return filepath


if __name__ == '__main__':
    import sys
    if '--dry-run' in sys.argv:
        # Test — wyświetl w konsoli bez zapisu
        VAULT_PATH = '/tmp/obsidian-test'
        data = gather_data()
        priority, tasks = generate_tasks(data)
        md = generate_markdown(data, priority, tasks)
        print(md)
    else:
        generate_daily_note()
