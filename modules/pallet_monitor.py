"""
PALLET MONITOR - Monitoring okazji palet z warrington.store i jobalots.com

Harmonogram:
  Warrington: co 5 min w 10:00-11:00 i 16:00-17:00
  Jobalots:   o 8:30 i ~13:00

Powiadomienia: Telegram + zapis do DB (pallet_deals)
Keywords: konfigurowalne w config (pallet_monitor_keywords)
"""

import requests
import json
import re
import time
from datetime import datetime
from bs4 import BeautifulSoup


# ============================================================
# KURSY WALUT (cache 24h, live z NBP)
# ============================================================
_exchange_cache = {'rates': {}, 'timestamp': 0}

def _get_exchange_rate(currency):
    """Pobiera kurs waluty do PLN. Cache 24h, fallback na stałe kursy."""
    currency = currency.upper()
    if currency == 'PLN':
        return 1.0

    now = time.time()
    # Odśwież cache co 24h
    if now - _exchange_cache['timestamp'] > 86400:
        try:
            # NBP API — tabela A (kursy średnie)
            resp = requests.get('https://api.nbp.pl/api/exchangerates/tables/A/?format=json', timeout=10)
            if resp.status_code == 200:
                rates = {}
                for rate in resp.json()[0].get('rates', []):
                    rates[rate['code'].upper()] = float(rate['mid'])
                _exchange_cache['rates'] = rates
                _exchange_cache['timestamp'] = now
        except Exception:
            pass  # Użyj cache/fallback

    rate = _exchange_cache['rates'].get(currency)
    if rate:
        return rate

    # Fallback — stałe kursy
    fallback = {'GBP': 5.20, 'EUR': 4.30, 'USD': 4.05, 'SEK': 0.40, 'NOK': 0.38, 'DKK': 0.58}
    return fallback.get(currency, 1.0)


# ============================================================
# KONFIGURACJA
# ============================================================

DEFAULT_KEYWORDS_PL = [
    # Elektronika / Tech
    'rampa inwalidzka', 'rampy inwalidzkie', 'wheelchair ramp', 'mobility ramp', 'access ramp',
    'fontanna dla kota', 'fontanny dla kota', 'cat fountain', 'pet fountain', 'water fountain',
    'kabel ev', 'kable ev', 'ev charging', 'ev charger', '11kw', 'type 2 cable', 'wallbox',
    'bieznia', 'bieżnia', 'bieznie', 'bieżnie', 'treadmill', 'running machine', 'folding treadmill',
    # Ogród / Narzędzia
    'robot koszący', 'robot mower', 'robotic mower', 'pressure washer', 'myjka ciśnieniowa',
    'spawarka', 'welder', 'welding', 'kompresor', 'compressor', 'air compressor',
    'generator', 'power generator', 'agregat prądotwórczy',
    'piła łańcuchowa', 'chainsaw', 'hedge trimmer', 'nożyce do żywopłotu',
    'kosiarka', 'lawn mower', 'grass trimmer',
    # Dom / AGD
    'odkurzacz', 'vacuum cleaner', 'robot vacuum', 'robot sprzątający',
    'oczyszczacz powietrza', 'air purifier', 'osuszacz', 'dehumidifier',
    'ekspres do kawy', 'coffee machine', 'espresso', 'coffee maker',
    'drukarka 3d', '3d printer', 'filament',
    # Elektronika
    'panel solarny', 'solar panel', 'photovoltaic', 'fotowoltaika',
    'powerstation', 'power station', 'portable power', 'stacja energii',
    'kamera', 'camera', 'dashcam', 'security camera', 'cctv',
    'router', 'switch ethernet', 'network switch',
    'monitor', 'projector', 'projektor',
    # Automotive
    'pokrowce samochodowe', 'car seat cover', 'car cover',
    'bagażnik', 'roof rack', 'roof box', 'relingi',
    'oświetlenie led', 'led bar', 'light bar',
    # Sport / Outdoor
    'rower', 'bike', 'e-bike', 'electric bike', 'hulajnoga', 'scooter', 'electric scooter',
    'namiot', 'tent', 'camping',
    'trampoline', 'trampolina',
    # Zabawki premium
    'lego', 'playmobil', 'hot wheels',
]

# Negatywne słowa kluczowe — jeśli palet zawiera DUŻO tych produktów, pomijaj
# (pojedyncze sztuki OK, ale jak dominują w palecie to skip)
NEGATIVE_KEYWORDS = [
    # Etui / Pokrowce na telefon
    'phone case', 'phone cover', 'etui na telefon', 'case iphone', 'case samsung',
    'screen protector', 'folia ochronna', 'tempered glass', 'szkło hartowane',
    # Maty do yogi
    'yoga mat', 'mata do yogi', 'mata do jogi', 'exercise mat', 'mata fitness',
    # Drobne akcesoria masowe
    'cable usb', 'kabel usb', 'charging cable', 'aux cable',
    'pop socket', 'popsocket', 'phone grip', 'phone holder',
    'sticker', 'naklejka', 'decal',
]

WARRINGTON_BASE = 'https://warrington.store'
JOBALOTS_BASE = 'https://jobalots.com'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'pl-PL,pl;q=0.9,en;q=0.7',
}


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] [PalletMonitor] {msg}", flush=True)
    try:
        from modules.logger import log as _file_log
        _file_log(f"[PalletMonitor] {msg}")
    except Exception:
        pass


# ============================================================
# DATABASE
# ============================================================

def init_pallet_monitor_db(conn):
    """Tworzy tabelę pallet_deals i monitor_stats jeśli nie istnieją"""
    conn.execute('''CREATE TABLE IF NOT EXISTS pallet_deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        external_id TEXT,
        title TEXT NOT NULL,
        url TEXT,
        price REAL DEFAULT 0,
        currency TEXT DEFAULT 'PLN',
        category TEXT DEFAULT '',
        image_url TEXT DEFAULT '',
        items_count INTEGER DEFAULT 0,
        market_value REAL DEFAULT 0,
        matched_keywords TEXT DEFAULT '',
        notified INTEGER DEFAULT 0,
        first_seen TIMESTAMP DEFAULT (datetime('now', 'localtime')),
        last_seen TIMESTAMP DEFAULT (datetime('now', 'localtime')),
        UNIQUE(source, external_id)
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS monitor_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TIMESTAMP DEFAULT (datetime('now', 'localtime')),
        event_type TEXT NOT NULL,
        source TEXT DEFAULT '',
        deals_found INTEGER DEFAULT 0,
        deals_new INTEGER DEFAULT 0,
        total_scraped INTEGER DEFAULT 0,
        scan_time_sec REAL DEFAULT 0,
        ai_model TEXT DEFAULT '',
        ai_tokens_in INTEGER DEFAULT 0,
        ai_tokens_out INTEGER DEFAULT 0,
        ai_cost_usd REAL DEFAULT 0,
        details TEXT DEFAULT ''
    )''')
    conn.commit()


def _log_scan_stats(source, deals_found, deals_new, total_scraped, scan_time_sec):
    """Zapisuje statystyki skanu do DB"""
    try:
        from .database import get_db
        conn = get_db()
        conn.execute(
            '''INSERT INTO monitor_stats (event_type, source, deals_found, deals_new, total_scraped, scan_time_sec)
               VALUES ('scan', ?, ?, ?, ?, ?)''',
            (source, deals_found, deals_new, total_scraped, scan_time_sec)
        )
        conn.commit()
    except Exception as e:
        log(f"Stats log error: {e}")


def log_gemini_usage(response, context='unknown'):
    """
    Loguje użycie Gemini AI do monitor_stats.
    response: obiekt odpowiedzi z google.genai
    context: np. 'title_gen', 'description', 'params', 'gpsr', 'meta_title'
    """
    try:
        tokens_in = 0
        tokens_out = 0

        # Nowe API: response.usage_metadata
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            um = response.usage_metadata
            tokens_in = getattr(um, 'prompt_token_count', 0) or 0
            tokens_out = getattr(um, 'candidates_token_count', 0) or 0

        # Gemini 2.0 Flash pricing (per 1M tokens)
        # Input: $0.10/1M (<=128k), Output: $0.40/1M
        cost = (tokens_in * 0.10 + tokens_out * 0.40) / 1_000_000

        from .database import get_db
        conn = get_db()
        init_pallet_monitor_db(conn)
        conn.execute(
            '''INSERT INTO monitor_stats (event_type, source, ai_model, ai_tokens_in, ai_tokens_out, ai_cost_usd, details)
               VALUES ('gemini', ?, 'gemini-2.0-flash', ?, ?, ?, ?)''',
            (context, tokens_in, tokens_out, round(cost, 6), context)
        )
        conn.commit()
        log(f"Gemini [{context}]: ${cost:.5f} ({tokens_in}+{tokens_out} tok)")
    except Exception as e:
        try:
            log(f"Gemini stats log error: {e}")
        except:
            pass


def _log_perplexity_stats(model, tokens_in, tokens_out, deals_analyzed):
    """Zapisuje statystyki wywołania Perplexity do DB"""
    # Cennik Perplexity (za 1M tokenów)
    pricing = {
        'sonar': {'input': 1.0, 'output': 1.0},
        'sonar-pro': {'input': 3.0, 'output': 15.0},
        'sonar-reasoning': {'input': 1.0, 'output': 5.0},
        'sonar-reasoning-pro': {'input': 2.0, 'output': 8.0},
    }
    rates = pricing.get(model, {'input': 1.0, 'output': 1.0})
    cost = (tokens_in * rates['input'] + tokens_out * rates['output']) / 1_000_000

    try:
        from .database import get_db
        conn = get_db()
        conn.execute(
            '''INSERT INTO monitor_stats (event_type, source, deals_found, ai_model, ai_tokens_in, ai_tokens_out, ai_cost_usd)
               VALUES ('perplexity', 'ai', ?, ?, ?, ?, ?)''',
            (deals_analyzed, model, tokens_in, tokens_out, round(cost, 6))
        )
        conn.commit()
        log(f"Perplexity cost: ${cost:.4f} ({model}, {tokens_in}+{tokens_out} tokens)")
    except Exception as e:
        log(f"Perplexity stats log error: {e}")


def get_monitor_costs():
    """Pobiera statystyki kosztów i skanów"""
    from .database import get_db
    conn = get_db()
    init_pallet_monitor_db(conn)

    stats = {}

    # Dzisiaj
    row = conn.execute('''
        SELECT COUNT(*) as scans, SUM(deals_new) as new_deals, SUM(total_scraped) as scraped, SUM(scan_time_sec) as time_s
        FROM monitor_stats WHERE event_type='scan' AND date(timestamp) = date('now')
    ''').fetchone()
    stats['today_scans'] = row['scans'] or 0
    stats['today_new_deals'] = int(row['new_deals'] or 0)
    stats['today_scraped'] = int(row['scraped'] or 0)
    stats['today_scan_time'] = round(row['time_s'] or 0, 1)

    # Ten miesiąc
    row = conn.execute('''
        SELECT COUNT(*) as scans, SUM(deals_new) as new_deals, SUM(total_scraped) as scraped, SUM(scan_time_sec) as time_s
        FROM monitor_stats WHERE event_type='scan' AND strftime('%Y-%m', timestamp) = strftime('%Y-%m', 'now')
    ''').fetchone()
    stats['month_scans'] = row['scans'] or 0
    stats['month_new_deals'] = int(row['new_deals'] or 0)
    stats['month_scraped'] = int(row['scraped'] or 0)
    stats['month_scan_time'] = round(row['time_s'] or 0, 1)

    # Perplexity koszty
    row = conn.execute('''
        SELECT COUNT(*) as calls, SUM(ai_tokens_in) as tin, SUM(ai_tokens_out) as tout, SUM(ai_cost_usd) as cost
        FROM monitor_stats WHERE event_type='perplexity' AND date(timestamp) = date('now')
    ''').fetchone()
    stats['today_ai_calls'] = row['calls'] or 0
    stats['today_ai_cost'] = round(row['cost'] or 0, 4)

    row = conn.execute('''
        SELECT COUNT(*) as calls, SUM(ai_tokens_in) as tin, SUM(ai_tokens_out) as tout, SUM(ai_cost_usd) as cost
        FROM monitor_stats WHERE event_type='perplexity' AND strftime('%Y-%m', timestamp) = strftime('%Y-%m', 'now')
    ''').fetchone()
    stats['month_ai_calls'] = row['calls'] or 0
    stats['month_ai_cost'] = round(row['cost'] or 0, 4)
    stats['month_ai_tokens'] = int((row['tin'] or 0) + (row['tout'] or 0))

    # Gemini koszty — dzisiaj
    row = conn.execute('''
        SELECT COUNT(*) as calls, SUM(ai_tokens_in) as tin, SUM(ai_tokens_out) as tout, SUM(ai_cost_usd) as cost
        FROM monitor_stats WHERE event_type='gemini' AND date(timestamp) = date('now')
    ''').fetchone()
    stats['today_gemini_calls'] = row['calls'] or 0
    stats['today_gemini_cost'] = round(row['cost'] or 0, 5)

    # Gemini — ten miesiąc
    row = conn.execute('''
        SELECT COUNT(*) as calls, SUM(ai_tokens_in) as tin, SUM(ai_tokens_out) as tout, SUM(ai_cost_usd) as cost
        FROM monitor_stats WHERE event_type='gemini' AND strftime('%Y-%m', timestamp) = strftime('%Y-%m', 'now')
    ''').fetchone()
    stats['month_gemini_calls'] = row['calls'] or 0
    stats['month_gemini_cost'] = round(row['cost'] or 0, 5)
    stats['month_gemini_tokens'] = int((row['tin'] or 0) + (row['tout'] or 0))

    # Gemini — per context (ten miesiąc)
    rows = conn.execute('''
        SELECT details as ctx, COUNT(*) as cnt, SUM(ai_cost_usd) as cost
        FROM monitor_stats WHERE event_type='gemini' AND strftime('%Y-%m', timestamp) = strftime('%Y-%m', 'now')
        GROUP BY details ORDER BY cnt DESC
    ''').fetchall()
    stats['gemini_breakdown'] = [(r['ctx'], r['cnt'], round(r['cost'] or 0, 5)) for r in rows]

    # Ogółem (all time) — Perplexity + Gemini łącznie
    row = conn.execute('''
        SELECT COUNT(*) as calls, SUM(ai_cost_usd) as cost
        FROM monitor_stats WHERE event_type='perplexity'
    ''').fetchone()
    stats['total_ai_calls'] = row['calls'] or 0
    stats['total_ai_cost'] = round(row['cost'] or 0, 4)

    row = conn.execute('''
        SELECT COUNT(*) as calls, SUM(ai_cost_usd) as cost
        FROM monitor_stats WHERE event_type='gemini'
    ''').fetchone()
    stats['total_gemini_calls'] = row['calls'] or 0
    stats['total_gemini_cost'] = round(row['cost'] or 0, 5)

    row = conn.execute('''
        SELECT COUNT(*) as scans, SUM(total_scraped) as scraped
        FROM monitor_stats WHERE event_type='scan'
    ''').fetchone()
    stats['total_scans'] = row['scans'] or 0
    stats['total_scraped'] = int(row['scraped'] or 0)

    # ====== STATYSTYKI SYSTEMU OD POCZĄTKU ======
    # Produkty
    row = conn.execute('SELECT COUNT(*) as cnt, MIN(data_dodania) as first FROM produkty').fetchone()
    stats['total_products'] = row['cnt'] or 0
    stats['system_start'] = (row['first'] or '')[:10]

    row = conn.execute("SELECT COUNT(*) as cnt FROM produkty WHERE strftime('%Y-%m', data_dodania) = strftime('%Y-%m', 'now')").fetchone()
    stats['month_products'] = row['cnt'] or 0

    # Oferty Allegro
    row = conn.execute('SELECT COUNT(*) as cnt FROM oferty').fetchone()
    stats['total_offers'] = row['cnt'] or 0

    row = conn.execute("SELECT COUNT(*) as cnt FROM oferty WHERE strftime('%Y-%m', data_wystawienia) = strftime('%Y-%m', 'now')").fetchone()
    stats['month_offers'] = row['cnt'] or 0

    # Sprzedaże
    row = conn.execute('SELECT COUNT(*) as cnt, SUM(cena * ilosc) as revenue FROM sprzedaze').fetchone()
    stats['total_sales'] = row['cnt'] or 0
    stats['total_revenue'] = round(row['revenue'] or 0, 2)

    row = conn.execute("SELECT COUNT(*) as cnt, SUM(cena * ilosc) as revenue FROM sprzedaze WHERE strftime('%Y-%m', data_sprzedazy) = strftime('%Y-%m', 'now')").fetchone()
    stats['month_sales'] = row['cnt'] or 0
    stats['month_revenue'] = round(row['revenue'] or 0, 2)

    # Palety
    row = conn.execute('SELECT COUNT(*) as cnt, SUM(cena_zakupu) as cost FROM palety').fetchone()
    stats['total_pallets'] = row['cnt'] or 0
    stats['total_pallets_cost'] = round(row['cost'] or 0, 2)

    # Czas zaoszczędzony (szacunki):
    # ~15 min/ofertę (tytuł+opis+parametry+zdjęcia+wystawienie ręczne)
    # ~5 min/skan palet (ręczne przeglądanie stron)
    # ~2 min/produkt dodany (ręczne wpisywanie danych)
    # ~3 min/AI analiza Perplexity (ręczne szukanie cen na Allegro)
    _offer_time = stats['total_offers'] * 15
    _scan_time = stats['total_scans'] * 5
    _product_time = stats['total_products'] * 2
    _ai_time = stats['total_ai_calls'] * 3

    stats['total_time_saved_min'] = _offer_time + _scan_time + _product_time + _ai_time
    stats['total_time_saved_h'] = round(stats['total_time_saved_min'] / 60, 1)

    _m_offer = stats['month_offers'] * 15
    _m_scan = stats['month_scans'] * 5
    _m_prod = stats['month_products'] * 2
    _m_ai = stats['month_ai_calls'] * 3
    stats['month_time_saved_min'] = _m_offer + _m_scan + _m_prod + _m_ai
    stats['month_time_saved_h'] = round(stats['month_time_saved_min'] / 60, 1)

    # Łączny koszt AI
    stats['total_all_ai_cost'] = round(stats['total_ai_cost'] + stats['total_gemini_cost'], 4)
    stats['month_all_ai_cost'] = round(stats['month_ai_cost'] + stats['month_gemini_cost'], 5)
    stats['today_all_ai_cost'] = round(stats['today_ai_cost'] + stats['today_gemini_cost'], 5)

    return stats


def get_keywords():
    """Pobiera keywords z configu lub zwraca domyślne"""
    try:
        from .database import get_config
        raw = get_config('pallet_monitor_keywords', '')
        if raw:
            return json.loads(raw)
    except:
        pass
    return DEFAULT_KEYWORDS_PL


def save_keywords(keywords_list):
    """Zapisuje keywords do config"""
    from .database import set_config
    set_config('pallet_monitor_keywords', json.dumps(keywords_list, ensure_ascii=False))


# Kategorie do pomijania — nie interesują nas ciuchy, obuwie, odzież itp.
BLOCKED_CATEGORIES = [
    'clothing', 'odzież', 'odzieżowe', 'obuwie', 'shoes', 'footwear', 'buty',
    'apparel', 'fashion', 'textile', 'textiles', 'tekstylia',
    'ubrania', 'kurtki', 'sukienki', 'spodnie', 'bluzy', 'koszulki',
    't-shirt', 'underwear', 'bielizna', 'skarpety', 'socks',
    'bags', 'handbags', 'torebki',
    'jewellery', 'jewelry', 'biżuteria',
    'cosmetics', 'kosmetyki', 'perfume', 'perfumy',
]


def get_negative_keywords():
    """Pobiera negative keywords z configu lub zwraca domyślne"""
    try:
        from .database import get_config
        raw = get_config('pallet_monitor_negative_kw', '')
        if raw:
            return json.loads(raw)
    except:
        pass
    return NEGATIVE_KEYWORDS


def _is_blocked_category(text):
    """Sprawdza czy produkt należy do zablokowanej kategorii"""
    if not text:
        return False
    text_lower = text.lower()
    for blocked in BLOCKED_CATEGORIES:
        if blocked in text_lower:
            return True
    return False


def _count_negative_hits(text, negative_kw=None):
    """Liczy ile negatywnych keywords pasuje do tekstu. Zwraca listę matched."""
    if not text:
        return []
    if negative_kw is None:
        negative_kw = get_negative_keywords()
    text_lower = text.lower()
    hits = []
    for kw in negative_kw:
        if kw.lower() in text_lower:
            hits.append(kw)
    return hits


def _match_keywords(text, keywords=None):
    """Sprawdza czy tekst zawiera któryś z keywords. Zwraca listę matched."""
    if not text:
        return []
    if keywords is None:
        keywords = get_keywords()
    text_lower = text.lower()
    matched = []
    for kw in keywords:
        if kw.lower() in text_lower:
            matched.append(kw)
    return matched


# ============================================================
# WARRINGTON.STORE SCRAPER
# ============================================================

def scrape_warrington(keywords=None, max_pages=2):
    """
    Scrapuje warrington.store/products/new (najnowsze palety).
    Zwraca listę dict z danymi o paletach.
    """
    if keywords is None:
        keywords = get_keywords()

    session = requests.Session()
    session.headers.update(HEADERS)

    # Ustaw sortowanie i count
    try:
        session.post(f'{WARRINGTON_BASE}/ajax/product/set-list-options',
                     json={"order_by": "date-new", "count": 500},
                     timeout=10)
    except Exception as e:
        log(f"Warrington set-options error: {e}")

    all_products = []

    for page in range(1, max_pages + 1):
        url = f'{WARRINGTON_BASE}/products/new/page/{page}'
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code != 200:
                log(f"Warrington page {page}: HTTP {resp.status_code}")
                break

            soup = BeautifulSoup(resp.text, 'html.parser')
            cards = soup.select('.product.text-center')

            if not cards:
                break

            for card in cards:
                try:
                    product = _parse_warrington_card(card)
                    if product:
                        all_products.append(product)
                except Exception as e:
                    continue

            log(f"Warrington page {page}: {len(cards)} palet")

        except Exception as e:
            log(f"Warrington page {page} error: {e}")
            break

    # Filtruj: wyrzuć zablokowane kategorie i negatywne, resztę przepuść
    interesting = []
    blocked = 0
    negative_skipped = 0
    negative_kw = get_negative_keywords()

    for p in all_products:
        search_text = f"{p['title']} {p.get('category', '')}"

        if _is_blocked_category(search_text):
            blocked += 1
            continue

        neg_hits = _count_negative_hits(search_text, negative_kw)
        if len(neg_hits) >= 2:
            negative_skipped += 1
            continue

        kw_hits = _match_keywords(search_text, keywords)
        if kw_hits:
            p['matched_keywords'] = kw_hits
            p['priority'] = 'high'
        elif neg_hits:
            p['matched_keywords'] = []
            p['priority'] = 'low'
        else:
            p['matched_keywords'] = []
            p['priority'] = 'normal'

        p['roi_ratio'] = 0  # Warrington nie ma RRP
        interesting.append(p)

    log(f"Warrington: {len(all_products)} total, {blocked} blocked, {negative_skipped} negative, {len(interesting)} interesting")
    return all_products, interesting


def _parse_warrington_card(card):
    """Parsuje kartę produktu z listingu warrington.store"""
    product = {'source': 'warrington'}

    # ID
    ext_id = card.get('data-id', '')
    if not ext_id:
        link = card.select_one('a[href*="/product/"]')
        if link:
            m = re.search(r'/product/(\d+)', link['href'])
            if m:
                ext_id = m.group(1)
    product['external_id'] = ext_id

    # URL
    link = card.select_one('.product-name a') or card.select_one('a[href*="/product/"]')
    if link:
        href = link.get('href', '')
        product['url'] = href if href.startswith('http') else f'{WARRINGTON_BASE}{href}'
        product['title'] = link.get_text(strip=True)
    else:
        product['title'] = ''

    # Kategoria
    cat_el = card.select_one('.product-cat a')
    product['category'] = cat_el.get_text(strip=True) if cat_el else ''

    # Cena
    price_el = card.select_one('.new-price')
    if price_el:
        price_text = price_el.get_text(strip=True).replace(' ', '').replace('zl', '').replace('zł', '').replace(',', '.')
        try:
            product['price'] = float(re.sub(r'[^\d.]', '', price_text))
        except:
            product['price'] = 0
    else:
        product['price'] = 0
    product['currency'] = 'PLN'

    # Obrazek
    img = card.select_one('img')
    if img:
        src = img.get('src', '') or img.get('data-src', '')
        product['image_url'] = src if src.startswith('http') else f'{WARRINGTON_BASE}{src}'
    else:
        product['image_url'] = ''

    # Label (Nowosc, SALE)
    label = card.select_one('.product-label')
    if label:
        product['label'] = label.get_text(strip=True)

    return product


def scrape_warrington_detail(product_url):
    """Scrapuje szczegóły palety (lista przedmiotów, ASIN-y, ceny)"""
    try:
        resp = requests.get(product_url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, 'html.parser')

        items = []
        # Tabela z przedmiotami (DataTable)
        table = soup.select_one('table.table') or soup.select_one('#subproductsTable')
        if table:
            rows = table.select('tbody tr')
            for row in rows:
                cells = row.select('td')
                if len(cells) >= 4:
                    item = {
                        'name': cells[1].get_text(strip=True) if len(cells) > 1 else '',
                        'asin': cells[2].get_text(strip=True) if len(cells) > 2 else '',
                        'qty': cells[3].get_text(strip=True) if len(cells) > 3 else '',
                    }
                    # Ceny
                    if len(cells) > 4:
                        item['sale_price'] = cells[4].get_text(strip=True)
                    if len(cells) > 5:
                        item['market_price'] = cells[5].get_text(strip=True)
                    items.append(item)

        # Ilość sztuk
        qty_el = soup.select_one('.product-quantity') or soup.find(text=re.compile(r'\d+\s*szt'))
        total_qty = 0
        if qty_el:
            m = re.search(r'(\d+)', str(qty_el))
            if m:
                total_qty = int(m.group(1))

        return {
            'items': items,
            'items_count': total_qty or len(items),
        }

    except Exception as e:
        log(f"Warrington detail error: {e}")
        return None


# ============================================================
# JOBALOTS.COM SCRAPER
# ============================================================

def scrape_jobalots(keywords=None, max_pages=3):
    """
    Scrapuje jobalots.com aukcje palet via API.
    Filtruje ship_to=PL (tylko palety z dostawą do Polski).
    """
    if keywords is None:
        keywords = get_keywords()

    all_products = []
    api_url = 'https://live1.jobalots.com/api/auction-list-v2'
    api_headers = {
        'Content-Type': 'application/json',
        'url-accept-language': 'pl',
        'url-accept-currency': 'pln',
    }

    sort_options = ['popularity', 'most_bids', 'bid_low', 'ending_soon']

    for sort_by in sort_options:
        for page in range(1, max_pages + 1):
            try:
                resp = requests.post(api_url, headers=api_headers, json={
                    'manifest_type': ['pallets'],
                    'ship_to': 'PL',
                    'ship_from': 'all',
                    'list_type': ['auction', 'buyitnow'],
                    'is_list': True,
                    'use_open_search': '0',
                    'exact_match': '0',
                    'search_manifests': '0',
                    'per_page': 48,
                    'page': page,
                    'sort_by': sort_by,
                }, timeout=25)

                if resp.status_code != 200:
                    log(f"Jobalots API ({sort_by} p{page}): HTTP {resp.status_code}")
                    break  # Nie ma więcej stron

                data = resp.json()
                items = data.get('result', {}).get('data', [])

                if not items:
                    break  # Pusta strona = koniec

                for item in items:
                    sku = item.get('sku', '')
                    title = item.get('title', '')
                    rrp_raw = float(item.get('rrp', 0) or 0)
                    bid_raw = float(item.get('latest_bid_price', 0) or 0)
                    reserve_raw = float(item.get('reserve_price', 0) or item.get('start_bid_price', 0) or 0)
                    # reserve/rrp w walucie listingu, bid w PLN (url-accept-currency)
                    item_currency = (item.get('currency') or 'GBP').upper()
                    kurs = _get_exchange_rate(item_currency)
                    rrp = round(rrp_raw * kurs, 2)
                    bid = bid_raw  # latest_bid_price jest już w PLN
                    reserve = round(reserve_raw * kurs, 2) if reserve_raw > 0 else 0
                    qty = item.get('qty', 0)
                    category = item.get('category_name', '') or item.get('category', '')
                    # Obrazek z manifest
                    image = ''
                    manifest = item.get('manifest', {}) or {}
                    image = manifest.get('image_thumbnail_url', '') or manifest.get('image_url', '')
                    if not image:
                        images = item.get('images', [])
                        if images and isinstance(images, list):
                            image = images[0] if isinstance(images[0], str) else images[0].get('url', '')

                    product = {
                        'source': 'jobalots',
                        'external_id': sku,
                        'title': title,
                        'url': f'https://jobalots.com/pl/products/{sku}?currency=pln',
                        'price': bid if bid > 0 else (reserve if reserve > 0 else rrp),
                        'currency': 'PLN',
                        'category': category,
                        'image_url': image,
                        'items_count': qty,
                        'market_value': rrp,
                    }
                    all_products.append(product)

                log(f"Jobalots API ({sort_by} p{page}): {len(items)} items")

            except Exception as e:
                log(f"Jobalots API ({sort_by} p{page}) error: {e}")
                break

    # Deduplikacja po SKU
    seen = set()
    unique = []
    for p in all_products:
        key = p.get('external_id', p.get('url', ''))
        if key and key not in seen:
            seen.add(key)
            unique.append(p)
    all_products = unique

    # Filtruj: wyrzuć zablokowane kategorie i negatywne, resztę przepuść
    interesting = []
    blocked = 0
    negative_skipped = 0
    keyword_matched = 0
    negative_kw = get_negative_keywords()

    for p in all_products:
        search_text = f"{p['title']} {p.get('category', '')}"

        # 1. Blokuj kategorie (odzież, kosmetyki itp.)
        if _is_blocked_category(search_text):
            blocked += 1
            continue

        # 2. Sprawdź negatywne keywords
        neg_hits = _count_negative_hits(search_text, negative_kw)
        if len(neg_hits) >= 2:
            # Dużo negatywnych = pomijaj (pewnie masówka etui/mat)
            negative_skipped += 1
            continue

        # 3. Sprawdź pozytywne keywords (dla priorytetu)
        kw_hits = _match_keywords(search_text, keywords)
        if kw_hits:
            p['matched_keywords'] = kw_hits
            p['priority'] = 'high'
            keyword_matched += 1
        elif neg_hits:
            # 1 negatywny hit ale nie zablokowany = niski priorytet
            p['matched_keywords'] = []
            p['priority'] = 'low'
            p['negative_hits'] = neg_hits
        else:
            p['matched_keywords'] = []
            p['priority'] = 'normal'

        # 4. Ocena opłacalności: RRP vs cena
        rrp = p.get('market_value', 0)
        price = p.get('price', 0)
        if rrp > 0 and price > 0:
            p['roi_ratio'] = round(rrp / price, 1)
        else:
            p['roi_ratio'] = 0

        interesting.append(p)

    # Sortuj: high priority first, potem po ROI malejąco
    priority_order = {'high': 0, 'normal': 1, 'low': 2}
    interesting.sort(key=lambda x: (priority_order.get(x.get('priority', 'normal'), 1), -x.get('roi_ratio', 0)))

    log(f"Jobalots: {len(all_products)} total, {blocked} blocked cat, {negative_skipped} negative, {keyword_matched} keyword match, {len(interesting)} interesting")
    return all_products, interesting


# ============================================================
# GŁÓWNA LOGIKA MONITORINGU
# ============================================================

def run_monitor(source='all', notify=True):
    """
    Uruchamia monitoring dla wybranego źródła.
    source: 'warrington', 'jobalots', 'all'
    notify: czy wysyłać powiadomienia Telegram
    Zwraca (new_deals, all_interesting)
    """
    from .database import get_db

    conn = get_db()
    init_pallet_monitor_db(conn)

    keywords = get_keywords()
    new_deals = []
    all_interesting = []
    total_scraped = 0
    _scan_start = time.time()

    if source in ('warrington', 'all'):
        try:
            all_products, interesting = scrape_warrington(keywords)
            total_scraped += len(all_products)
            for p in interesting:
                is_new = _save_deal(conn, p)
                if is_new:
                    new_deals.append(p)
            all_interesting.extend(interesting)
        except Exception as e:
            log(f"Warrington monitor error: {e}")

    if source in ('jobalots', 'all'):
        try:
            all_products, interesting = scrape_jobalots(keywords)
            total_scraped += len(all_products)
            for p in interesting:
                is_new = _save_deal(conn, p)
                if is_new:
                    new_deals.append(p)
            all_interesting.extend(interesting)
        except Exception as e:
            log(f"Jobalots monitor error: {e}")

    conn.commit()

    # Wyślij powiadomienia Telegram — wszystkie nowe deale (nie low priority)
    if notify and new_deals:
        high_priority = [d for d in new_deals if d.get('priority') == 'high']
        normal_priority = [d for d in new_deals if d.get('priority') == 'normal']

        # Wysyłaj: wszystkie high + max 20 normalnych (posortowane po ROI)
        to_notify = high_priority + normal_priority[:20]
        if to_notify:
            _send_deal_notifications(to_notify)

        # Jeśli jest jeszcze więcej, wyślij podsumowanie
        if len(normal_priority) > 20:
            _send_summary_notification(source, len(new_deals), len(high_priority), len(normal_priority))

    # Perplexity AI analiza top deali — ZAWSZE gdy są interesujące (nie tylko nowe)
    if notify and all_interesting:
        _all_high = [d for d in all_interesting if d.get('priority') == 'high']
        _all_good = [d for d in all_interesting if d.get('priority') == 'normal' and d.get('roi_ratio', 0) >= 3]
        _all_good.sort(key=lambda x: -x.get('roi_ratio', 0))
        top_for_ai = (_all_high + _all_good)[:6]
        if top_for_ai:
            import threading
            threading.Thread(
                target=analyze_top_deals_with_perplexity,
                args=(top_for_ai,),
                daemon=True,
                name="PerplexityAnalysis"
            ).start()

    _scan_time = round(time.time() - _scan_start, 1)
    _log_scan_stats(source, len(all_interesting), len(new_deals), total_scraped, _scan_time)
    log(f"Monitor done: {len(new_deals)} new, {len(all_interesting)} interesting z {total_scraped} przeskanowanych ({_scan_time}s)")
    return new_deals, all_interesting


def _save_deal(conn, product):
    """Zapisuje deal do DB. Zwraca True jeśli nowy (nie widziany wcześniej)."""
    source = product.get('source', '')
    ext_id = product.get('external_id', '')

    if not ext_id:
        # Generuj z URL
        ext_id = product.get('url', '').split('/')[-1][:100]

    # Sprawdź czy już istnieje
    existing = conn.execute(
        'SELECT id FROM pallet_deals WHERE source = ? AND external_id = ?',
        (source, ext_id)
    ).fetchone()

    matched_kw = json.dumps(product.get('matched_keywords', []), ensure_ascii=False)

    if existing:
        # Update last_seen
        conn.execute(
            "UPDATE pallet_deals SET last_seen = datetime('now', 'localtime'), price = ?, matched_keywords = ? WHERE id = ?",
            (product.get('price', 0), matched_kw, existing['id'])
        )
        return False
    else:
        # Nowy deal
        conn.execute('''INSERT INTO pallet_deals
            (source, external_id, title, url, price, currency, category, image_url, matched_keywords)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (source, ext_id, product.get('title', ''), product.get('url', ''),
             product.get('price', 0), product.get('currency', 'PLN'),
             product.get('category', ''), product.get('image_url', ''), matched_kw))
        return True


def _send_deal_notifications(deals):
    """Wysyła powiadomienia Telegram o nowych dealach"""
    try:
        from .telegram_bot import send_telegram
    except ImportError:
        log("Telegram bot not available")
        return

    for deal in deals[:25]:  # Max 25 powiadomień na raz
        source_emoji = '🏪' if deal['source'] == 'warrington' else '🎪'
        priority = deal.get('priority', 'normal')
        price = deal.get('price', 0)
        price_str = f"{price:.0f} {deal.get('currency', 'PLN')}"

        # ROI info
        roi = deal.get('roi_ratio', 0)
        roi_str = f"📈 ROI: {roi}x" if roi > 1 else ""

        # Qty info
        qty = int(deal.get('items_count', 0) or 0)
        qty_str = f"📦 {qty} szt" if qty > 0 else ""

        # Priority badge
        if priority == 'high':
            badge = "⭐ KEYWORD MATCH"
        elif roi >= 5:
            badge = "🔥 SUPER DEAL"
        elif roi >= 3:
            badge = "💰 DOBRY DEAL"
        else:
            badge = "📋 Nowa paleta"

        kw_str = ', '.join(deal.get('matched_keywords', [])[:3])
        kw_line = f"🔑 {kw_str}\n" if kw_str else ""

        # RRP info
        rrp = deal.get('market_value', 0)
        rrp_str = f"💎 RRP: {rrp:.0f} PLN\n" if rrp > 0 else ""

        # Buduj wiadomość
        lines = [
            f"{source_emoji} <b>{badge}</b>",
            "",
            f"<b>{deal.get('title', '?')[:120]}</b>",
            "",
            f"💵 Cena: {price_str}",
        ]
        if rrp_str:
            lines.append(rrp_str.strip())
        if roi_str:
            lines.append(roi_str)
        if qty_str:
            lines.append(qty_str)
        lines.append(f"📁 {deal.get('category', '-')}")
        if kw_line:
            lines.append(kw_line.strip())
        lines.append("")
        lines.append(f"🔗 {deal.get('url', '')}")
        msg = '\n'.join(lines)

        try:
            send_telegram(msg)
            time.sleep(0.5)
        except Exception as e:
            log(f"Telegram send error: {e}")


def _send_summary_notification(source, total_new, high_count, normal_count):
    """Wysyła podsumowanie gdy jest dużo nowych deali"""
    try:
        from .telegram_bot import send_telegram
    except ImportError:
        return

    msg = (
        f"📊 <b>Podsumowanie skanowania</b>\n\n"
        f"Źródło: {source}\n"
        f"Nowe palety: {total_new}\n"
        f"⭐ Keyword match: {high_count}\n"
        f"📋 Pozostałe: {normal_count}\n\n"
        f"Sprawdź szczegóły: /monitor"
    )
    try:
        send_telegram(msg)
    except Exception as e:
        log(f"Telegram summary error: {e}")


def get_recent_deals(limit=50, source=None):
    """Pobiera ostatnie deale z DB"""
    from .database import get_db
    conn = get_db()
    init_pallet_monitor_db(conn)

    if source:
        rows = conn.execute(
            'SELECT * FROM pallet_deals WHERE source = ? ORDER BY first_seen DESC LIMIT ?',
            (source, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT * FROM pallet_deals ORDER BY first_seen DESC LIMIT ?',
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_deal_stats():
    """Statystyki monitoringu"""
    from .database import get_db
    conn = get_db()
    init_pallet_monitor_db(conn)

    stats = {}

    # Dzisiejsze nowe
    today = conn.execute(
        "SELECT COUNT(*) as cnt FROM pallet_deals WHERE date(first_seen) = date('now')"
    ).fetchone()
    stats['today_new'] = today['cnt'] if today else 0

    # Total per source
    for src in ('warrington', 'jobalots'):
        row = conn.execute(
            'SELECT COUNT(*) as cnt FROM pallet_deals WHERE source = ?', (src,)
        ).fetchone()
        stats[f'{src}_total'] = row['cnt'] if row else 0

    # Ostatni skan
    last = conn.execute(
        'SELECT MAX(last_seen) as ts FROM pallet_deals'
    ).fetchone()
    stats['last_scan'] = last['ts'] if last else None

    return stats


# ============================================================
# PERPLEXITY AI - analiza sprzedawalności top deali
# ============================================================

def analyze_top_deals_with_perplexity(deals, max_deals=6):
    """
    Analizuje top deale przez Perplexity AI pod kątem sprzedawalności na Allegro.
    Wysyła wynik na Telegram.
    """
    if not deals:
        return

    try:
        from .database import get_config
        api_key = get_config('perplexity_api_key', '')
        if not api_key:
            log("Perplexity: brak klucza API — pomijam analizę")
            return
        model = get_config('perplexity_model', 'sonar-pro')
    except:
        return

    # Wybierz top deale do analizy (high priority + najlepszy ROI)
    top = [d for d in deals if d.get('priority') == 'high']
    normal_good = [d for d in deals if d.get('priority') == 'normal' and d.get('roi_ratio', 0) >= 3]
    normal_good.sort(key=lambda x: -x.get('roi_ratio', 0))
    top.extend(normal_good)
    top = top[:max_deals]

    if not top:
        log("Perplexity: brak deali do analizy")
        return

    # Pobierz szczegóły produktów z palet (Warrington)
    for d in top:
        if d.get('source') == 'warrington' and d.get('url') and not d.get('_items'):
            try:
                detail = scrape_warrington_detail(d['url'])
                if detail and detail.get('items'):
                    d['_items'] = detail['items']
                    d['items_count'] = detail.get('items_count') or len(detail['items'])
                    log(f"Perplexity: pobrano {len(detail['items'])} produktów z {d['title'][:40]}")
            except Exception as _de:
                log(f"Perplexity: detail error: {_de}")

    # Buduj prompt z listą produktów
    deals_text = ""
    for i, d in enumerate(top, 1):
        deals_text += (
            f"{i}. {d.get('title', '?')[:100]}\n"
            f"   Cena: {d.get('price', 0):.0f} PLN | RRP: {d.get('market_value', 0):.0f} PLN | "
            f"Ilość: {d.get('items_count', '?')} szt | Źródło: {d.get('source', '?')}\n"
            f"   Kategoria: {d.get('category', '-')}\n"
        )
        # Dodaj listę produktów jeśli dostępna
        items = d.get('_items', [])
        if items:
            deals_text += "   Produkty w palecie:\n"
            for j, item in enumerate(items[:15], 1):  # Max 15 produktów (token limit)
                name = item.get('name', '?')[:80]
                asin = item.get('asin', '')
                qty = item.get('qty', '?')
                market = item.get('market_price', '')
                deals_text += f"     {j}. {name}"
                if asin:
                    deals_text += f" (ASIN: {asin})"
                if qty:
                    deals_text += f" x{qty}"
                if market:
                    deals_text += f" — RRP: {market}"
                deals_text += "\n"
            if len(items) > 15:
                deals_text += f"     ... i {len(items)-15} więcej produktów\n"
        deals_text += "\n"

    prompt = (
        f"Ile kosztują te produkty na allegro.pl? Podaj ceny sprzedaży.\n\n"
        f"{deals_text}\n"
        f"Dla każdej palety: wyszukaj ceny głównych produktów na Allegro, "
        f"oblicz szacowany zysk (suma cen × 0.7 - koszt palety - 11%% prowizji), "
        f"oceń 1-10 i daj rekomendację: 🟢 KUP / 🟡 ROZWAŻ / 🔴 ODPUŚĆ.\n"
        f"Odpowiadaj po polsku z konkretnymi cenami."
    )

    # Zachowaj URL-e do dołączenia pod analizą
    deal_urls = [(d.get('title', '?')[:60], d.get('url', '')) for d in top if d.get('url')]

    log(f"Perplexity: analizuję {len(top)} top deali...")

    try:
        import requests as _req
        resp = _req.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4000,
                "return_citations": True,
                "search_recency_filter": "month",
            },
            timeout=90,
        )

        if resp.status_code != 200:
            log(f"Perplexity API error: HTTP {resp.status_code} — {resp.text[:200]}")
            return

        data = resp.json()
        answer = data.get('choices', [{}])[0].get('message', {}).get('content', '')
        citations = data.get('citations', [])

        # Loguj koszty
        usage = data.get('usage', {})
        tokens_in = usage.get('prompt_tokens', 0)
        tokens_out = usage.get('completion_tokens', 0)
        _log_perplexity_stats(model, tokens_in, tokens_out, len(top))

        if not answer:
            log("Perplexity: pusta odpowiedź")
            return

        log(f"Perplexity: odpowiedź OK ({len(answer)} znaków, {tokens_in}+{tokens_out} tok)")

        # Wyślij na Telegram
        _send_perplexity_telegram(answer, citations, len(top), deal_urls)

    except Exception as e:
        log(f"Perplexity error: {e}")


def _send_perplexity_telegram(answer, citations, deal_count, deal_urls=None):
    """Wysyła analizę Perplexity na Telegram"""
    try:
        from .telegram_bot import send_telegram
    except ImportError:
        return

    # Zamień markdown ** na HTML <b>
    import re
    answer = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', answer)
    answer = re.sub(r'### (.+)', r'<b>\1</b>', answer)

    msg = (
        f"🤖 <b>ANALIZA AI — Top {deal_count} palet</b>\n\n"
        f"{answer}"
    )

    # Dodaj linki do palet
    if deal_urls:
        msg += "\n\n🔗 <b>Linki do palet:</b>\n"
        for i, (title, url) in enumerate(deal_urls, 1):
            msg += f"{i}. <a href=\"{url}\">{title}</a>\n"

    # Telegram max 4096 znaków — skróć jeśli trzeba
    if len(msg) > 4000:
        msg = msg[:3950] + "\n\n(...skrócone)"

    try:
        send_telegram(msg)
        log("Perplexity analiza wysłana na Telegram")
    except Exception as e:
        log(f"Perplexity Telegram error: {e}")


# ============================================================
# SCHEDULER - automatyczne skanowanie wg harmonogramu
# ============================================================
# Warrington: co 5 min w 10:00-11:00 i 16:00-17:00
# Jobalots:   co 2h w godzinach 8:00-22:00 (8, 10, 12, 14, 16, 18, 20)

import threading

_scheduler_thread = None
_scheduler_running = False


def start_scheduler():
    """Uruchamia wątek schedulera pallet monitora"""
    global _scheduler_thread, _scheduler_running
    if _scheduler_running:
        log("Scheduler już działa")
        return

    _scheduler_running = True
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True, name="PalletScheduler")
    _scheduler_thread.start()
    log("Scheduler uruchomiony")


def stop_scheduler():
    """Zatrzymuje scheduler"""
    global _scheduler_running
    _scheduler_running = False
    log("Scheduler zatrzymany")


def is_scheduler_running():
    return _scheduler_running


def _scheduler_loop():
    """Główna pętla schedulera"""
    global _scheduler_running
    last_warrington_scan = 0
    last_jobalots_scan = 0
    _JOBALOTS_INTERVAL = 7200  # co 2h

    while _scheduler_running:
        try:
            now = datetime.now()
            h, m = now.hour, now.minute

            # === WARRINGTON: co 5 min w 10:00-11:00 i 16:00-17:00 ===
            from .database import get_config
            warrington_enabled = get_config('monitor_warrington_enabled', '1') == '1'
            jobalots_enabled = get_config('monitor_jobalots_enabled', '1') == '1'

            if warrington_enabled and ((10 <= h < 11) or (16 <= h < 17)):
                if time.time() - last_warrington_scan >= 300:  # 5 min
                    log("Scheduler: skan Warrington (harmonogram)")
                    try:
                        run_monitor(source='warrington', notify=True)
                    except Exception as e:
                        log(f"Scheduler Warrington error: {e}")
                    last_warrington_scan = time.time()

            # === JOBALOTS: co 2h w godzinach 8-22 ===
            if jobalots_enabled and 8 <= h < 22:
                if time.time() - last_jobalots_scan >= _JOBALOTS_INTERVAL:
                    log(f"Scheduler: skan Jobalots (co 2h, teraz {h}:{m:02d})")
                    try:
                        run_monitor(source='jobalots', notify=True)
                    except Exception as e:
                        log(f"Scheduler Jobalots error: {e}")
                    last_jobalots_scan = time.time()

            # === NOCNE GENEROWANIE ZDJĘĆ AI: o 22:00 (teraz rembg — darmowe) ===
            if h == 22 and m < 5 and not getattr(_scheduler_loop, '_enhance_done_today', False):
                log("Scheduler: nocne generowanie zdjęć AI (22:00)")
                try:
                    from modules.paletomat import _bg_enhance_worker, _bg_enhance_status
                    if not _bg_enhance_status.get('running'):
                        import threading
                        from flask import current_app
                        app = current_app._get_current_object()
                        _bg_enhance_status.update({
                            'running': True, 'progress': 0, 'current': 0, 'total': 0,
                            'done': 0, 'errors': 0, 'cost': 0.0, 'log': [], 'finished': False,
                            'started_at': time.time(), 'last_update': time.time()
                        })
                        t = threading.Thread(target=_bg_enhance_worker, args=(app, False), daemon=True)
                        t.start()
                        log("Scheduler: zdjęcia AI — generowanie uruchomione w tle")
                    else:
                        log("Scheduler: zdjęcia AI — już działa, pomijam")
                    _scheduler_loop._enhance_done_today = True
                except Exception as e:
                    log(f"Scheduler enhance error: {e}")

            # Reset flagi o północy
            if h == 0 and m < 5:
                _scheduler_loop._enhance_done_today = False

            time.sleep(30)  # Sprawdzaj co 30 sekund

        except Exception as e:
            log(f"Scheduler error: {e}")
            time.sleep(60)
