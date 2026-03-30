"""
Notion Daily Tasks — Akces Hub
Generuje codzienną stronę z taskami w Notion na podstawie danych z bazy.
Zastępuje obsidian_tasks.py — żadnych plików, żadnego gita.
"""

import sqlite3
import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta

DB_PATH = os.environ.get('AKCES_DB', '/home/pi/akces-hub/akces_hub.db')

NOTION_API = 'https://api.notion.com/v1'
NOTION_VERSION = '2022-06-28'


# ---------- helpers ----------

def _get_notion_config():
    """Pobiera token i database_id z bazy lub env"""
    token = os.environ.get('NOTION_TOKEN', '')
    db_id = os.environ.get('NOTION_DATABASE_ID', '')

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT klucz, wartosc FROM config WHERE klucz IN ('notion_token','notion_database_id')"
        ).fetchall()
        conn.close()
        for r in rows:
            if r['klucz'] == 'notion_token' and r['wartosc']:
                token = r['wartosc']
            if r['klucz'] == 'notion_database_id' and r['wartosc']:
                db_id = r['wartosc']
    except Exception:
        pass

    return token, db_id


def _notion_request(method, path, data=None, token=''):
    """Prosty wrapper HTTP dla Notion API (bez zewnętrznych bibliotek)"""
    url = f'{NOTION_API}{path}'
    headers = {
        'Authorization': f'Bearer {token}',
        'Notion-Version': NOTION_VERSION,
        'Content-Type': 'application/json',
    }
    body = json.dumps(data).encode('utf-8') if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        err = e.read().decode('utf-8')
        raise RuntimeError(f'Notion API {method} {path} → HTTP {e.code}: {err[:300]}')


def _txt(content, bold=False, color=None):
    """Notion rich_text object"""
    obj = {'type': 'text', 'text': {'content': str(content)}}
    if bold or color:
        obj['annotations'] = {}
        if bold:
            obj['annotations']['bold'] = True
        if color:
            obj['annotations']['color'] = color
    return obj


def _heading(level, text):
    t = f'heading_{level}'
    return {'object': 'block', 'type': t, t: {'rich_text': [_txt(text)]}}


def _para(*parts):
    return {'object': 'block', 'type': 'paragraph',
            'paragraph': {'rich_text': list(parts)}}


def _todo(text, checked=False):
    return {'object': 'block', 'type': 'to_do',
            'to_do': {'rich_text': [_txt(text)], 'checked': checked}}


def _divider():
    return {'object': 'block', 'type': 'divider', 'divider': {}}


def _callout(text, emoji='📌'):
    return {
        'object': 'block', 'type': 'callout',
        'callout': {
            'rich_text': [_txt(text)],
            'icon': {'type': 'emoji', 'emoji': emoji},
        }
    }


def _bullet(text):
    return {'object': 'block', 'type': 'bulleted_list_item',
            'bulleted_list_item': {'rich_text': [_txt(text)]}}


def _table_row(cells):
    return {
        'type': 'table_row',
        'table_row': {'cells': [[_txt(c)] for c in cells]}
    }


def _table(rows):
    """rows = list of lists (first = header)"""
    return {
        'object': 'block',
        'type': 'table',
        'table': {
            'table_width': len(rows[0]),
            'has_column_header': True,
            'has_row_header': False,
            'children': [_table_row(r) for r in rows]
        }
    }


# ---------- dane z bazy ----------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def gather_data():
    """Zbiera dane z bazy do generowania tasków"""
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    month = datetime.now().strftime('%Y-%m')
    weekday = datetime.now().weekday()

    data = {}

    data['do_wyslania'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM sprzedaze
        WHERE status IN ('nowa', 'sprzedana') AND status != 'zwrot'
    ''').fetchone()['cnt']

    data['zwroty_nowe'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM sprzedaze
        WHERE status = 'zwrot' AND data_sprzedazy >= date('now', '-7 days')
    ''').fetchone()['cnt']

    data['bez_oferty'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM produkty
        WHERE status = 'magazyn' AND ilosc > 0
        AND id NOT IN (SELECT DISTINCT produkt_id FROM oferty WHERE produkt_id IS NOT NULL)
    ''').fetchone()['cnt']

    data['bez_oferty_top'] = conn.execute('''
        SELECT nazwa, cena_allegro, ilosc, kod_magazynowy FROM produkty
        WHERE status = 'magazyn' AND ilosc > 0
        AND id NOT IN (SELECT DISTINCT produkt_id FROM oferty WHERE produkt_id IS NOT NULL)
        ORDER BY COALESCE(cena_allegro, cena_brutto, 0) DESC
        LIMIT 5
    ''').fetchall()

    data['bez_zdjec'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM produkty
        WHERE status = 'magazyn' AND ilosc > 0
        AND (zdjecie_url IS NULL OR zdjecie_url = '')
    ''').fetchone()['cnt']

    data['bez_opisu'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM produkty
        WHERE status = 'magazyn' AND ilosc > 0
        AND (opis_ai IS NULL OR opis_ai = '')
    ''').fetchone()['cnt']

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

    data['palety_w_drodze'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM palety
        WHERE dostarczona = 0 OR dostarczona IS NULL
    ''').fetchone()['cnt']

    stats = conn.execute('''
        SELECT
            COUNT(*) as zamowienia,
            COALESCE(SUM(CASE WHEN status NOT IN ('zwrot','anulowane','anulowana')
                         THEN cena * ilosc ELSE 0 END), 0) as przychod,
            COALESCE(SUM(CASE WHEN status = 'zwrot' THEN 1 ELSE 0 END), 0) as zwroty
        FROM sprzedaze
        WHERE strftime('%Y-%m', data_sprzedazy) = ?
    ''', (month,)).fetchone()
    data['month_orders'] = stats['zamowienia']
    data['month_revenue'] = stats['przychod']
    data['month_returns'] = stats['zwroty']

    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    yst = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc), 0) as total
        FROM sprzedaze WHERE date(data_sprzedazy) = ? AND status != 'zwrot'
    ''', (yesterday,)).fetchone()
    data['yesterday_orders'] = yst['cnt']
    data['yesterday_revenue'] = yst['total']

    tst = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc), 0) as total
        FROM sprzedaze WHERE date(data_sprzedazy) = ? AND status != 'zwrot'
    ''', (today,)).fetchone()
    data['today_orders'] = tst['cnt']
    data['today_revenue'] = tst['total']

    data['total_stock'] = conn.execute('''
        SELECT COALESCE(SUM(ilosc), 0) as cnt FROM produkty
        WHERE status = 'magazyn' AND ilosc > 0
    ''').fetchone()['cnt']

    data['total_products'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM produkty WHERE status = 'magazyn' AND ilosc > 0
    ''').fetchone()['cnt']

    data['oferty_aktywne'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM oferty WHERE status = 'ACTIVE'
    ''').fetchone()['cnt']

    data['oferty_martwe'] = conn.execute('''
        SELECT COUNT(*) as cnt FROM oferty
        WHERE status = 'ACTIVE'
        AND data_wystawienia < date('now', '-14 days')
        AND id NOT IN (
            SELECT DISTINCT oferta_id FROM sprzedaze
            WHERE oferta_id IS NOT NULL AND data_sprzedazy > date('now', '-14 days')
        )
    ''').fetchone()['cnt']

    try:
        goal = conn.execute(
            'SELECT target_amount, current_amount, name FROM goal LIMIT 1'
        ).fetchone()
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

    if data['do_wyslania'] > 0:
        priority_tasks.append(f"📦 Wyślij {data['do_wyslania']} zamówień — klienci czekają!")

    if data['zwroty_nowe'] > 0:
        priority_tasks.append(f"↩️ Obsłuż {data['zwroty_nowe']} nowych zwrotów z ostatnich 7 dni")

    if data['bez_oferty'] > 0:
        n = min(data['bez_oferty'], 10)
        tasks.append(f"🏷️ Wystaw {n} produktów na Allegro (łącznie {data['bez_oferty']} bez oferty)")

    if data['bez_zdjec'] > 0:
        n = min(data['bez_zdjec'], 5)
        tasks.append(f"📸 Zrób zdjęcia {n} produktom (łącznie {data['bez_zdjec']} bez zdjęć)")

    if data['bez_opisu'] > 0:
        n = min(data['bez_opisu'], 5)
        tasks.append(f"✍️ Wygeneruj opisy AI dla {n} produktów ({data['bez_opisu']} łącznie)")

    if data['serwis_aktywny'] > 0:
        tasks.append(f"🔧 Sprawdź {data['serwis_aktywny']} produktów w serwisie")

    if data['palety_w_drodze'] > 0:
        tasks.append(f"🚚 Sprawdź status {data['palety_w_drodze']} palet w drodze")

    if data['oferty_martwe'] > 0:
        tasks.append(f"💀 Przejrzyj {data['oferty_martwe']} ofert bez sprzedaży >14 dni — obniż ceny?")

    weekday = data['weekday']
    if weekday == 0:
        tasks.append("📊 Poniedziałkowy przegląd — sprawdź wyniki weekendu")
        tasks.append("🎯 Ustaw cele na ten tydzień")
    if weekday == 4:
        tasks.append("📈 Piątkowe podsumowanie tygodnia")
        tasks.append("🔄 Sprawdź i uzupełnij stany magazynowe na weekend")
    if data['today'].endswith('-01'):
        tasks.append("📅 Nowy miesiąc — ustaw cel sprzedażowy")
        tasks.append("💰 Podsumuj koszty i zysk z poprzedniego miesiąca")
        tasks.append("📦 Rozważ zamówienie nowych palet")

    return priority_tasks, tasks


# ---------- Notion builder ----------

def _build_page_blocks(data, priority_tasks, tasks):
    """Buduje listę bloków dla strony Notion"""
    blocks = []
    month_names = {
        '01': 'Styczeń', '02': 'Luty', '03': 'Marzec', '04': 'Kwiecień',
        '05': 'Maj', '06': 'Czerwiec', '07': 'Lipiec', '08': 'Sierpień',
        '09': 'Wrzesień', '10': 'Październik', '11': 'Listopad', '12': 'Grudzień'
    }
    month_name = month_names.get(data['month'][5:7], data['month'])

    # --- DASHBOARD ---
    blocks.append(_heading(2, '📊 Dashboard'))

    # Tabela statystyk
    tbl_rows = [['Metryka', 'Wartość']]
    tbl_rows.append(['Wczoraj', f"{data['yesterday_orders']} zamówień / {data['yesterday_revenue']:.0f} zł"])
    tbl_rows.append(['Dziś', f"{data['today_orders']} zamówień / {data['today_revenue']:.0f} zł"])
    tbl_rows.append([month_name, f"{data['month_orders']} zamówień / {data['month_revenue']:.0f} zł  (zwroty: {data['month_returns']})"])

    if data.get('goal_target'):
        pct = (data['goal_current'] / data['goal_target'] * 100) if data['goal_target'] > 0 else 0
        tbl_rows.append([
            f"🎯 {data['goal_name']}",
            f"{data['goal_current']:,.0f} / {data['goal_target']:,.0f} zł ({pct:.0f}%)"
        ])

    tbl_rows.append(['Stock', f"{data['total_stock']} szt / {data['total_products']} produktów"])
    tbl_rows.append(['Oferty aktywne', str(data['oferty_aktywne'])])

    blocks.append(_table(tbl_rows))
    blocks.append(_divider())

    # --- PILNE ---
    if priority_tasks:
        blocks.append(_heading(2, '🔴 Pilne'))
        for t in priority_tasks:
            blocks.append(_todo(t))
        blocks.append(_divider())

    # --- ZADANIA ---
    blocks.append(_heading(2, '✅ Zadania na dziś'))
    if tasks:
        for t in tasks:
            blocks.append(_todo(t))
    else:
        blocks.append(_para(_txt('Brak zaległości — dobra robota! 🎉')))
    blocks.append(_divider())

    # --- TOP PRODUKTY DO WYSTAWIENIA ---
    if data['bez_oferty_top']:
        blocks.append(_heading(2, '🏷️ Top produkty do wystawienia'))
        top_rows = [['Produkt', 'Cena', 'Szt', 'Kod']]
        for p in data['bez_oferty_top']:
            cena = p['cena_allegro'] or 0
            top_rows.append([
                (p['nazwa'] or '?')[:50],
                f'{cena:.0f} zł',
                str(p['ilosc']),
                p['kod_magazynowy'] or '-'
            ])
        blocks.append(_table(top_rows))
        blocks.append(_divider())

    # --- SERWIS ---
    if data['serwis_items']:
        blocks.append(_heading(2, '🔧 Serwis w toku'))
        for s in data['serwis_items']:
            nazwa = s['nazwa'] or 'Brak nazwy'
            opis = s['opis_usterki'] or 'brak opisu'
            blocks.append(_todo(f"{nazwa[:45]} — {s['status']} | {opis}"))
        blocks.append(_divider())

    # --- NOTATKI ---
    blocks.append(_heading(2, '📝 Notatki'))
    blocks.append(_para(_txt('')))

    return blocks


def _check_page_exists(database_id, today, token):
    """Sprawdza czy strona na dzisiaj już istnieje w bazie Notion"""
    payload = {
        'filter': {
            'property': 'Data',
            'date': {'equals': today}
        }
    }
    try:
        result = _notion_request('POST', f'/databases/{database_id}/query', payload, token)
        results = result.get('results', [])
        if results:
            return results[0]['id']
    except Exception:
        pass
    return None


def push_to_notion(data, priority_tasks, tasks, token, database_id):
    """Tworzy (lub pomija istniejącą) stronę w Notion"""
    today = data['today']

    # Sprawdź czy strona już istnieje
    existing_id = _check_page_exists(database_id, today, token)
    if existing_id:
        print(f'[NOTION] Strona {today} już istnieje — pomijam')
        return existing_id

    day_names = ['Poniedziałek', 'Wtorek', 'Środa', 'Czwartek', 'Piątek', 'Sobota', 'Niedziela']
    day_name = day_names[data['weekday']]
    title = f'{today} — {day_name}'

    blocks = _build_page_blocks(data, priority_tasks, tasks)

    # Notion limit: max 100 bloków w jednym requescie — dzielimy jeśli więcej
    payload = {
        'parent': {'database_id': database_id},
        'properties': {
            'Name': {'title': [{'text': {'content': title}}]},
            'Data': {'date': {'start': today}},
            'Zamówienia do wysłania': {'number': data['do_wyslania']},
            'Przychód miesiąca': {'number': round(data['month_revenue'], 2)},
            'Status': {'select': {'name': 'Do zrobienia'}},
        },
        'children': blocks[:100]
    }

    page = _notion_request('POST', '/pages', payload, token)
    page_id = page['id']
    print(f'[NOTION] Utworzono stronę: {title} (id={page_id})')

    # Dołącz resztę bloków jeśli >100
    if len(blocks) > 100:
        remaining = blocks[100:]
        for i in range(0, len(remaining), 100):
            chunk = remaining[i:i+100]
            _notion_request('PATCH', f'/blocks/{page_id}/children', {'children': chunk}, token)

    return page_id


def generate_notion_daily(db_path=None):
    """Główna funkcja — generuje daily note w Notion"""
    global DB_PATH
    if db_path:
        DB_PATH = db_path

    token, database_id = _get_notion_config()

    if not token or not database_id:
        print('[NOTION] Brak konfiguracji (notion_token / notion_database_id) — pomijam')
        return None

    data = gather_data()
    priority_tasks, tasks = generate_tasks(data)

    try:
        page_id = push_to_notion(data, priority_tasks, tasks, token, database_id)
        return page_id
    except Exception as e:
        print(f'[NOTION] Błąd: {e}')
        return None


# ---------- CLI ----------

if __name__ == '__main__':
    import sys
    if '--dry-run' in sys.argv:
        data = gather_data()
        priority, tasks = generate_tasks(data)
        day_names = ['Poniedziałek', 'Wtorek', 'Środa', 'Czwartek', 'Piątek', 'Sobota', 'Niedziela']
        print(f"\n=== {data['today']} — {day_names[data['weekday']]} ===\n")
        if priority:
            print('🔴 PILNE:')
            for t in priority:
                print(f'  ☐ {t}')
        print('\n✅ ZADANIA:')
        for t in tasks:
            print(f'  ☐ {t}')
        print(f"\n📊 Dziś: {data['today_orders']} zamówień / {data['today_revenue']:.0f} zł")
        print(f"📊 Miesiąc: {data['month_orders']} zamówień / {data['month_revenue']:.0f} zł")
    else:
        result = generate_notion_daily()
        if result:
            print(f'[OK] page_id={result}')
        else:
            print('[FAIL] Sprawdź config notion_token i notion_database_id')
