# -*- coding: utf-8 -*-
"""
Winning Scout — automatyczny skaner nowych produktów do sprzedaży na Allegro.

Źródła: AliExpress trending, Amazon bestsellers, Alibaba wholesale.
Filtrowanie: blacklist, duplikaty, fuzzy match, blokowane rodziny.
Scoring: trend, margin, novelty, competition, sourcing.
"""

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional

import requests

logger = logging.getLogger(__name__)

def _log(msg):
    """Print log z timestampem — widoczny w journalctl."""
    print(f"[SCOUT] {msg}", flush=True)

# ─── STAŁE ───────────────────────────────────────────────────────────────────

BLOCKED_FAMILIES = [
    'kamerki samochodowe', 'dash cam', 'dashcam',
    'obciążniki fitness', 'ankle weights', 'resistance bands',
    'projektory rozrywkowe', 'night lights', 'galaxy projectors',
    'uchwyty telefoniczne', 'car mounts', 'car phone holders',
    'ładowarki bezprzewodowe', 'wireless chargers',
    'smartwatche', 'fitness trackers', 'smart watch',
    'mini projektory', 'portable projectors',
    'gadżety rowerowe', 'lampki rowerowe',
    'części samochodowe drobne', 'organizery samochodowe',
    'powerbanki', 'portable chargers', 'power bank',
    'karimata', 'sleeping pad', 'inflatable mat', 'camping mat',
    'azdome', 'obciążniki', 'kamera samochodowa', 'projektor kosmos',
    'ankle weights', 'galaxy projector', 'car mount',
    'wireless charger', 'fitness tracker',
    'lawnmaster', 'kosiarka', 'piła teleskopowa',
]

BLOCKED_KEYWORDS = [
    'azdome', 'obciążniki', 'kamera samochodowa', 'projektor kosmos',
    'dash cam', 'ankle weights', 'galaxy projector', 'car mount',
    'powerbank', 'wireless charger', 'smartwatch', 'fitness tracker',
    'lawnmaster', 'kosiarka', 'piła teleskopowa',
]

NOISE_WORDS = {
    'nowy', 'new', 'premium', 'super', 'pro', 'ultra', 'deluxe',
    '2025', '2026', 'bestseller', 'hot', 'sale', 'best', 'top',
    'original', 'genuine', 'upgraded', 'improved', 'latest',
}

# Kategorie Amazon do skanowania
AMAZON_CATEGORIES = {
    'sport': 'https://www.amazon.de/gp/bestsellers/sports/ref=zg_bs_sports_sm',
    'elektronika': 'https://www.amazon.de/gp/bestsellers/ce-de/ref=zg_bs_ce-de_sm',
    'auto': 'https://www.amazon.de/gp/bestsellers/automotive/ref=zg_bs_automotive_sm',
    'dom': 'https://www.amazon.de/gp/bestsellers/kitchen/ref=zg_bs_kitchen_sm',
    'zwierzeta': 'https://www.amazon.de/gp/bestsellers/pet-supplies/ref=zg_bs_pet-supplies_sm',
    'dzieci': 'https://www.amazon.de/gp/bestsellers/toys/ref=zg_bs_toys_sm',
    'beauty': 'https://www.amazon.de/gp/bestsellers/beauty/ref=zg_bs_beauty_sm',
}

SEASON_2026_KEYWORDS = [
    'outdoor', 'smart home', 'fitness', 'auto gadget', 'pet tech',
    'beauty tool', 'garden', 'camping', 'grill', 'solar',
    'massage', 'posture', 'ergonomic', 'led', 'usb',
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7,de;q=0.6',
    'Accept-Encoding': 'gzip, deflate',
}

# ─── SCHEDULER ────────────────────────────────────────────────────────────────

_scheduler_thread = None
_scheduler_running = False
_scan_lock = threading.Lock()
_scan_started_at = 0  # timestamp kiedy skan wystartował (do auto-unlock)


def start_scout_scheduler():
    """Uruchamia scheduler — auto-skan co 24h."""
    global _scheduler_thread, _scheduler_running
    if _scheduler_running:
        return
    _scheduler_running = True
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, daemon=True, name="WinningScoutScheduler"
    )
    _scheduler_thread.start()
    _log("[scout] Scheduler uruchomiony (co 24h)")


def _scheduler_loop():
    """Główna pętla schedulera."""
    global _scheduler_running
    while _scheduler_running:
        try:
            from modules.database import get_config
            last_run = get_config('scout_last_run', '')
            auto_enabled = get_config('scout_auto_enabled', 'true')

            if auto_enabled != 'true':
                time.sleep(300)
                continue

            should_run = False
            if not last_run:
                should_run = True
            else:
                try:
                    last_dt = datetime.fromisoformat(last_run)
                    if datetime.now() - last_dt > timedelta(hours=24):
                        should_run = True
                except (ValueError, TypeError):
                    should_run = True

            if should_run:
                now = datetime.now()
                if 6 <= now.hour <= 22:  # Tylko w rozsądnych godzinach
                    _log("[scout] Auto-skan uruchomiony")
                    try:
                        run_scout_scan()
                    except Exception as e:
                        _log(f"[scout] Auto-skan błąd: {e}")

            time.sleep(600)  # Sprawdzaj co 10 minut

        except Exception as e:
            _log(f"[scout] Scheduler error: {e}")
            time.sleep(300)


# ─── TABELA DB ────────────────────────────────────────────────────────────────

def init_scout_tables():
    """Tworzy tabelę winning_candidates jeśli nie istnieje."""
    try:
        from modules.database import get_db
        conn = get_db()

        conn.execute("""
            CREATE TABLE IF NOT EXISTS winning_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT,
                product_name TEXT,
                product_name_pl TEXT,
                category TEXT,
                source TEXT DEFAULT 'unknown',
                source_url TEXT,
                alibaba_url TEXT,
                alibaba_moq INTEGER,
                alibaba_price_usd REAL,
                alibaba_supplier TEXT,
                alibaba_trade_assurance INTEGER DEFAULT 0,
                buy_price_pln REAL,
                sell_price_pln REAL,
                margin_percent REAL,
                trend_score INTEGER DEFAULT 0,
                margin_score INTEGER DEFAULT 0,
                novelty_score INTEGER DEFAULT 0,
                competition_score INTEGER DEFAULT 0,
                sourcing_score INTEGER DEFAULT 0,
                final_score REAL DEFAULT 0,
                status TEXT DEFAULT 'new',
                reject_reason TEXT,
                why_new TEXT,
                why_can_sell TEXT,
                risk_flags TEXT,
                paczkomat_fit TEXT DEFAULT 'B',
                ali_rating REAL,
                allegro_competition INTEGER,
                growth_7d INTEGER,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migracja: image_url
        try:
            conn.execute("ALTER TABLE winning_candidates ADD COLUMN image_url TEXT")
        except Exception:
            pass

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_scout_score
                ON winning_candidates(final_score DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_scout_batch
                ON winning_candidates(batch_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_scout_status
                ON winning_candidates(status)
        """)

        conn.commit()
        _log("[scout] Tabela winning_candidates zainicjalizowana")
    except Exception as e:
        _log(f"[scout] init_scout_tables error: {e}")


# ─── BLACKLIST ────────────────────────────────────────────────────────────────

def _load_blacklist() -> dict:
    """Ładuje blacklist z bazy danych (istniejące produkty, oferty, sprzedaże)."""
    from modules.database import get_db
    conn = get_db()
    bl = {
        'names': set(),
        'asins': set(),
        'eans': set(),
        'categories': set(),
    }

    try:
        # Aktywne produkty
        for r in conn.execute(
            "SELECT nazwa, asin, ean, kategoria FROM produkty WHERE ilosc > 0"
        ).fetchall():
            if r['nazwa']:
                bl['names'].add(_normalize(r['nazwa']))
            if r['asin']:
                bl['asins'].add(r['asin'].strip().upper())
            if r['ean']:
                bl['eans'].add(r['ean'].strip())
            if r['kategoria']:
                bl['categories'].add(r['kategoria'].lower())

        # Historyczne produkty
        for r in conn.execute(
            "SELECT nazwa, asin, ean FROM produkty WHERE ilosc = 0"
        ).fetchall():
            if r['nazwa']:
                bl['names'].add(_normalize(r['nazwa']))
            if r['asin']:
                bl['asins'].add(r['asin'].strip().upper())
            if r['ean']:
                bl['eans'].add(r['ean'].strip())

        # Sprzedane
        for r in conn.execute(
            "SELECT DISTINCT nazwa FROM sprzedaze WHERE nazwa IS NOT NULL"
        ).fetchall():
            if r['nazwa']:
                bl['names'].add(_normalize(r['nazwa']))

        # Aktywne oferty
        for r in conn.execute(
            "SELECT tytul FROM oferty WHERE status = 'aktywna'"
        ).fetchall():
            if r['tytul']:
                bl['names'].add(_normalize(r['tytul']))

        # Produkty z poprzednich skanów Scout (deduplikacja)
        for r in conn.execute(
            "SELECT product_name FROM winning_candidates WHERE status = 'keep_new'"
        ).fetchall():
            if r['product_name']:
                bl['names'].add(_normalize(r['product_name']))

    except Exception as e:
        _log(f"[scout] Błąd ładowania blacklist: {e}")

    # Dodaj zablokowane rodziny z config
    try:
        from modules.database import get_config
        extra = get_config('scout_blocked_families', '')
        if extra:
            for fam in json.loads(extra):
                BLOCKED_FAMILIES.append(fam.lower().strip())
    except Exception:
        pass

    return bl


def _normalize(text: str) -> str:
    """Normalizuj nazwę produktu do porównań."""
    text = text.lower().strip()
    # Usuń noise words
    for w in NOISE_WORDS:
        text = re.sub(r'\b' + re.escape(w) + r'\b', '', text)
    # Usuń wielokrotne spacje
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _fuzzy_match(a: str, b: str) -> float:
    """Zwraca similarity ratio 0.0-1.0."""
    return SequenceMatcher(None, a, b).ratio()


def _is_blocked_family(name: str) -> bool:
    """Sprawdza czy produkt należy do zablokowanej rodziny."""
    name_lower = name.lower()
    for family in BLOCKED_FAMILIES:
        if family in name_lower:
            return True
    for kw in BLOCKED_KEYWORDS:
        if kw in name_lower:
            return True
    return False


def _check_duplicate(name: str, blacklist: dict) -> tuple[str, str]:
    """
    Sprawdza kandydata vs blacklist.
    Returns: (status, reason) — 'keep_new'/reject_*
    """
    norm = _normalize(name)

    # Zablokowana rodzina
    if _is_blocked_family(name):
        return 'reject_blocked', f'Zablokowana rodzina: {name[:50]}'

    # Exact match
    if norm in blacklist['names']:
        return 'reject_duplicate', f'Dokładny duplikat: {norm[:50]}'

    # Fuzzy match
    for existing in blacklist['names']:
        if not existing:
            continue
        sim = _fuzzy_match(norm, existing)
        if sim > 0.70:
            return 'reject_similar', f'Podobny ({sim:.0%}): {existing[:50]}'

    return 'keep_new', ''


# ─── TŁUMACZENIE ──────────────────────────────────────────────────────────────

def _translate_to_pl(text: str) -> str:
    """Tłumaczy tekst na polski używając Google Translate (free endpoint)."""
    if not text or len(text) < 3:
        return text

    # Sprawdź czy to już po polsku
    polish_chars = set('ąćęłńóśźż')
    if any(c in text.lower() for c in polish_chars):
        return text

    try:
        url = 'https://translate.googleapis.com/translate_a/single'
        params = {
            'client': 'gtx',
            'sl': 'auto',
            'tl': 'pl',
            'dt': 't',
            'q': text[:500],
        }
        resp = requests.get(url, params=params, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0'
        })
        if resp.status_code == 200:
            data = resp.json()
            if data and data[0]:
                translated = ''.join(part[0] for part in data[0] if part[0])
                if translated and len(translated) > 2:
                    return translated
    except Exception as e:
        _log(f"[scout] Translate error: {e}")

    return text


def _translate_to_en(text: str) -> str:
    """Tłumaczy tekst na angielski dla lepszych wyników w Chinach."""
    if not text or len(text) < 3: return text
    polish_chars = set('ąćęłńóśźż')
    if not any(c in text.lower() for c in polish_chars): return text

    try:
        url = 'https://translate.googleapis.com/translate_a/single'
        params = { 'client': 'gtx', 'sl': 'pl', 'tl': 'en', 'dt': 't', 'q': text[:500] }
        resp = requests.get(url, params=params, timeout=10, headers={'User-Agent': HEADERS['User-Agent']})
        if resp.status_code == 200:
            data = resp.json()
            if data and data[0]:
                translated = ''.join(part[0] for part in data[0] if part[0])
                if translated and len(translated) > 2:
                    _log(f"[scout] Translation: '{text}' -> '{translated}'")
                    return translated
        _log(f"[scout] Translation failed (HTTP {resp.status_code}), using original text.")
    except Exception as e:
        _log(f"[scout] Translation error: {e}")
    return text


# ─── SCRAPING: AMAZON BESTSELLERS ────────────────────────────────────────────

def _scrape_amazon_bestsellers(category: str, url: str, limit: int = 15) -> list[dict]:
    """Scrape Amazon.de bestsellers — zwraca listę produktów."""
    products = []
    try:
        session = requests.Session()
        session.headers.update(HEADERS)

        resp = session.get(url, timeout=10)
        if resp.status_code != 200:
            _log(f"[scout] Amazon {category}: HTTP {resp.status_code}")
            return []

        html = resp.text

        # Wyciągnij produkty z regex (Amazon bestseller page)
        # Szukamy tytułów i ASIN-ów
        # Pattern: data-asin="BXXXXXXXX" ... class="...title..."
        asin_pattern = re.findall(r'data-asin="(B[A-Z0-9]{9})"', html)
        title_pattern = re.findall(
            r'class="[^"]*zg-text-center-align[^"]*"[^>]*>.*?<img[^>]*alt="([^"]{10,120})"',
            html, re.DOTALL
        )
        if not title_pattern:
            title_pattern = re.findall(
                r'class="_cDEzb_p13n-sc-css-line-clamp-[^"]*"[^>]*>([^<]{10,150})<',
                html
            )
        if not title_pattern:
            title_pattern = re.findall(
                r'<span[^>]*class="[^"]*"[^>]*>([^<]{15,120})</span>',
                html
            )

        # Zbierz unikalne
        seen = set()
        for i, title in enumerate(title_pattern[:limit * 2]):
            title = title.strip()
            if len(title) < 10 or title in seen:
                continue
            seen.add(title)

            asin = asin_pattern[i] if i < len(asin_pattern) else ''
            products.append({
                'name': title,
                'source': 'amazon',
                'source_url': f'https://www.amazon.de/dp/{asin}' if asin else url,
                'category': category,
                'asin': asin,
            })

            if len(products) >= limit:
                break

        _log(f"[scout] Amazon {category}: {len(products)} produktów")

    except Exception as e:
        _log(f"[scout] Amazon {category} scrape error: {e}")

    return products


# ─── SCRAPING: ALIEXPRESS TRENDING ───────────────────────────────────────────

def _scrape_aliexpress_trending(limit: int = 20) -> list[dict]:
    """Próbuje pobrać trending z AliExpress. Fallback na Gemini."""
    products = []
    try:
        session = requests.Session()
        session.headers.update(HEADERS)

        # AliExpress popular/bestsellers
        urls = [
            'https://www.aliexpress.com/popular.html',
            'https://best.aliexpress.com/',
        ]

        for url in urls:
            try:
                resp = session.get(url, timeout=15, allow_redirects=True)
                if resp.status_code != 200:
                    continue

                # Wyciągnij nazwy produktów
                titles = re.findall(
                    r'"subject":"([^"]{10,120})"', resp.text
                )
                if not titles:
                    titles = re.findall(
                        r'<a[^>]*title="([^"]{10,120})"[^>]*class="[^"]*product', resp.text
                    )

                for title in titles[:limit]:
                    title = title.strip()
                    if len(title) > 10:
                        products.append({
                            'name': title,
                            'source': 'aliexpress',
                            'source_url': url,
                            'category': 'trending',
                        })

                if products:
                    break

            except Exception:
                continue

        _log(f"[scout] AliExpress trending: {len(products)} produktów")

    except Exception as e:
        _log(f"[scout] AliExpress scrape error: {e}")

    return products


# ─── IMAGE SEARCH ────────────────────────────────────────────────────────────

def _fetch_product_image(product_name: str) -> str:
    """Pobiera URL miniaturki produktu z DuckDuckGo Images."""
    try:
        query = requests.utils.quote(product_name[:60] + ' product white background')
        url = f'https://duckduckgo.com/?q={query}&iax=images&ia=images'

        session = requests.Session()
        session.headers.update(HEADERS)

        resp = session.get(url, timeout=8)
        if resp.status_code != 200:
            return ''

        # DuckDuckGo zwraca vqd token w HTML
        vqd_match = re.search(r'vqd=["\']([^"\']+)', resp.text)
        if not vqd_match:
            return ''

        vqd = vqd_match.group(1)

        # Fetch images JSON
        img_resp = session.get(
            f'https://duckduckgo.com/i.js?l=pl-pl&o=json&q={query}&vqd={vqd}&p=1',
            timeout=8,
            headers={**HEADERS, 'Referer': 'https://duckduckgo.com/'}
        )

        if img_resp.status_code != 200:
            return ''

        data = img_resp.json()
        results = data.get('results', [])

        # Weź pierwszą miniaturkę
        for r in results[:5]:
            thumb = r.get('thumbnail', '') or r.get('image', '')
            if thumb and thumb.startswith('http'):
                return thumb

    except Exception as e:
        _log(f"[scout] Image fetch error for '{product_name[:30]}': {e}")

    return ''


# ─── GEMINI: JSON PARSER ─────────────────────────────────────────────────────

def _parse_gemini_json(text: str) -> list:
    """Parsuje JSON z odpowiedzi Gemini — obsługuje markdown, ucięcia, błędy."""
    clean = text.strip()
    clean = re.sub(r'^```(?:json)?\s*', '', clean)
    clean = re.sub(r'\s*```\s*$', '', clean)
    clean = clean.strip()

    # Agresywny cleanup
    clean = clean.encode('ascii', errors='ignore').decode('ascii')
    clean = re.sub(r'[\x00-\x1f\x7f]', ' ', clean)
    clean = re.sub(r',\s*([}\]])', r'\1', clean)
    clean = re.sub(r'//[^\n]*', '', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()

    # Napraw ucięty JSON
    if clean.startswith('[') and not clean.endswith(']'):
        last_brace = clean.rfind('}')
        if last_brace > 0:
            clean = clean[:last_brace + 1] + ']'

    # Próba 1: czysty JSON
    try:
        return json.loads(clean, strict=False)
    except json.JSONDecodeError:
        pass

    # Próba 2: wyciągnij [...] z tekstu
    match = re.search(r'\[[\s\S]*\]', clean)
    if match:
        try:
            return json.loads(match.group(), strict=False)
        except json.JSONDecodeError:
            pass

    # Próba 3: regex — wyciągnij obiekty pojedynczo
    objects = re.findall(r'\{[^{}]{20,800}\}', clean)
    if objects:
        items = []
        for obj_str in objects:
            try:
                obj = json.loads(obj_str, strict=False)
                if isinstance(obj, dict) and obj.get('name'):
                    items.append(obj)
            except json.JSONDecodeError:
                continue
        if items:
            return items

    _log(f"[scout] JSON parse FAIL. Text ({len(clean)} chars): {clean[:300]}")
    return []


# ─── GEMINI: TREND DISCOVERY ─────────────────────────────────────────────────

def _gemini_discover_trends(existing_names: list[str]) -> list[dict]:
    """
    Używa Gemini AI do odkrywania trendujących produktów.
    Robi 3 osobne wywołania po różnych kategoriach — więcej produktów.
    """
    products = []

    try:
        from modules.database import get_config
        api_key = get_config('gemini_api_key', '')
        if not api_key:
            _log("[scout] Brak klucza Gemini — pomijam AI discovery")
            return []

        existing_str = ', '.join(existing_names[:20]) if existing_names else 'none'

        # 3 batche po różnych kategoriach
        batches = [
            {
                'focus': 'outdoor, camping, garden, sport, fitness',
                'examples': 'LED headlamp, camping hammock, garden tool, yoga mat strap, portable fan',
                'size_note': 'Mix of small (Paczkomat A/B) and medium items (Paczkomat C or courier)',
            },
            {
                'focus': 'smart home, auto accessories, electronics, gadgets',
                'examples': 'smart plug, car vacuum, LED strip controller, USB hub, car organizer',
                'size_note': 'Include both small gadgets AND bigger items like car accessories, organizers',
            },
            {
                'focus': 'pet supplies, beauty tools, kitchen, home organization, kids toys',
                'examples': 'pet grooming glove, face massager, kitchen scale, drawer organizer, kids tent',
                'size_note': 'Include bigger items too: pet beds, kitchen appliances, storage boxes (courier delivery OK)',
            },
        ]

        from modules.utils import get_gemini_api_url
        api_url = get_gemini_api_url(api_key)

        for batch_idx, batch in enumerate(batches):
            _log(f"[scout] Gemini batch {batch_idx+1}/3: {batch['focus'][:40]}...")

            prompt = f"""Find 10 trending products for Allegro.pl (Poland) in: {batch['focus']}.
Examples: {batch['examples']}.
{batch['size_note']}

RULES:
- Buy cost 2-50 USD from China/Alibaba
- Mix sizes: 4 small (Paczkomat A/B, sell 60-150 PLN), 3 medium (Paczkomat C, sell 100-250 PLN), 3 bigger (courier only, sell 150-400 PLN)
- Margin over 150% minimum
- Problem-solving, useful products
- Available wholesale on Alibaba (MOQ 50-500)

SKIP: {existing_str}
SKIP: dash cams, ankle weights, galaxy projectors, power banks, wireless chargers, smartwatches, lawn mowers.

paczkomat_fit rules:
A = tiny items: phone cases, small tools, cables, jewelry, cosmetics (under 8cm thick)
B = most products: massage guns, electronics, kitchen gadgets, pet toys, rollers (under 19cm thick)
C = bigger boxes: blenders, organizers, pet beds, car accessories (under 41cm thick)
NO = ONLY furniture, large appliances, items over 64cm in any dimension
Most products from China fit B or C. Use NO very rarely.

Return ONLY JSON array, ASCII only, no markdown, no comments:
[{{"name":"Product Name","category":"cat","buy_price_usd":5,"sell_price_pln":129,"source":"aliexpress","why_new":"powod po polsku","why_can_sell":"powod po polsku","risk_flags":"ryzyko po polsku","paczkomat_fit":"B","growth_7d":50,"alibaba_moq":100,"alibaba_price_usd":4}}]

IMPORTANT: "why_new", "why_can_sell" and "risk_flags" values MUST be in Polish language. All other fields in English/ASCII."""

            resp = requests.post(
                api_url,
                json={
                    'contents': [{'parts': [{'text': prompt}]}],
                    'generationConfig': {
                        'maxOutputTokens': 8192,
                        'temperature': 0.85,
                    }
                },
                timeout=90,
            )

            if resp.status_code != 200:
                _log(f"[scout] Gemini batch {batch_idx+1} FAIL HTTP {resp.status_code}")
                continue

            data = resp.json()
            text = ''
            try:
                text = data['candidates'][0]['content']['parts'][0]['text']
                _log(f"[scout] Gemini batch {batch_idx+1}: {len(text)} chars")
            except (KeyError, IndexError):
                _log(f"[scout] Gemini batch {batch_idx+1}: brak odpowiedzi")
                continue

            # Parsuj JSON
            batch_items = _parse_gemini_json(text)
            _log(f"[scout] Gemini batch {batch_idx+1}: {len(batch_items)} produktów")

            # Log Gemini usage
            try:
                from modules.pallet_monitor import log_gemini_usage
                log_gemini_usage(data, 'winning_scout')
            except Exception:
                pass

            for item in batch_items:
                if not isinstance(item, dict) or not item.get('name'):
                    cdef _gemini_search_fallback(phrase_en: str) -> list[dict]:
    """Używa Gemini do symulacji wyszukiwania produktów z Chin, gdy scrapery są zablokowane."""
    try:
        from modules.gemini_helper import get_gemini_api_key
        api_key = get_gemini_api_key()
        if not api_key:
            _log("[scout] AI Fallback: BRAK KLUCZA API")
            return []

        _log(f"[scout] AI Fallback start dla: '{phrase_en}'")
        prompt = f"""Find 10 LATEST/TRENDING wholesale products on Alibaba/AliExpress related to: "{phrase_en}".
Return ONLY a valid JSON array of objects. Schema: [{{"name":"Type name","price_usd":10,"image_url":"","source":"alibaba","url":""}}]
NO markdown, NO comments, ONLY the array."""
        
        from modules.utils import get_gemini_api_url
        api_url = get_gemini_api_url(api_key)
        
        resp = requests.post(api_url, json={'contents': [{'parts': [{'text': prompt}]}]}, timeout=25)
        if resp.status_code != 200:
            _log(f"[scout] AI API Error: {resp.status_code} - {resp.text[:100]}")
            return []

        data = resp.json()
        if 'candidates' not in data or not data['candidates']:
            _log(f"[scout] AI Error: Empty candidates. Raw: {data}")
            return []

        text = data['candidates'][0]['content']['parts'][0]['text']
        items = _parse_gemini_json(text)
        if not items:
            _log(f"[scout] AI Error: Failed to parse JSON from response: {text[:200]}")
            return []

        results = []
        for it in items:
            results.append({
                'name': it.get('name', 'Brak nazwy'),
                'price_usd': float(it.get('price_usd', 0) or 0),
                'url': it.get('url', ''),
                'image': it.get('image_url', ''),
                'source': it.get('source', 'alibaba'),
                'category': phrase_en
            })
        return results
    except Exception as e:
        _log(f"[scout] AI Fallback CRITICAL error: {e}")
    return []


# ─── ALIBABA SEARCH ──────────────────────────────────────────────────────────

def _search_alibaba(product_name: str) -> dict:
    """
    Szuka produktu na Alibaba.com i zwraca dane hurtowe.
    Returns: dict z url, moq, price_usd, supplier, trade_assurance
    """
    result = {
        'url': '',
        'moq': 0,
        'price_usd': 0.0,
        'supplier': '',
        'trade_assurance': False,
    }

    try:
        session = requests.Session()
        session.headers.update(HEADERS)

        # Search Alibaba
        query = product_name.replace(' ', '+')[:80]
        search_url = f'https://www.alibaba.com/trade/search?SearchText={query}&viewtype=G'

        resp = session.get(search_url, timeout=10, allow_redirects=True)
        _log(f"[scout] Alibaba '{product_name[:30]}' → HTTP {resp.status_code}, {len(resp.text)} chars")
        if resp.status_code != 200:
            return result

        html = resp.text

        # Wyciągnij pierwszy wynik
        # Szukaj linku do produktu
        urls = re.findall(
            r'href="(https://www\.alibaba\.com/product-detail/[^"]+)"', html
        )
        if not urls:
            urls = re.findall(
                r'href="(//www\.alibaba\.com/product-detail/[^"]+)"', html
            )
            urls = [f'https:{u}' for u in urls]

        if urls:
            result['url'] = urls[0]

        # MOQ
        moq_match = re.findall(r'(\d+)\s*(?:Piece|Set|Unit|szt)', html, re.IGNORECASE)
        if moq_match:
            result['moq'] = int(moq_match[0])

        # Cena
        price_matches = re.findall(r'\$\s*([\d.]+)\s*-\s*\$\s*([\d.]+)', html)
        if price_matches:
            result['price_usd'] = float(price_matches[0][0])
        else:
            price_single = re.findall(r'\$\s*([\d.]+)', html)
            if price_single:
                try:
                    p = float(price_single[0])
                    if 0.1 < p < 100:
                        result['price_usd'] = p
                except ValueError:
                    pass

        # Supplier name
        supplier_match = re.findall(
            r'class="[^"]*company-name[^"]*"[^>]*>([^<]{3,60})<', html
        )
        if supplier_match:
            result['supplier'] = supplier_match[0].strip()

        # Trade Assurance
        if 'trade assurance' in html.lower() or 'tradeassurance' in html.lower():
            result['trade_assurance'] = True

        _log(f"[scout] Alibaba result: url={bool(result['url'])}, price=${result['price_usd']}, moq={result['moq']}, supplier={result['supplier'][:20]}")

    except Exception as e:
        _log(f"[scout] Alibaba search error for '{product_name[:30]}': {e}")

    return result


# ─── ALLEGRO COMPETITION CHECK (WEB SCRAPING) ────────────────────────────────

def _check_allegro_competition(product_name_pl: str) -> int:
    """Sprawdza ile ofert jest na Allegro dla tego produktu via web scraping z fallbackiem na AI."""
    try:
        import urllib.parse
        query = urllib.parse.quote_plus(product_name_pl[:80])
        url = f"https://allegro.pl/listing?string={query}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'pl-PL,pl;q=0.9',
        }
        
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                html = resp.text
                if "captcha" in html.lower() or "robot" in html.lower():
                    _log(f"[scout] Allegro blocked (captcha detected). Switching to AI estimation.")
                    return _estimate_competition_ai(product_name_pl)

                # Szukamy "X ofert" lub "X wyników"
                import re
                count_match = re.search(r'(\d[\d\s]*)\s*(?:ofert|wynik)', html)
                if count_match:
                    count_str = count_match.group(1).replace(' ', '').replace('\xa0', '')
                    return int(count_str)
            
                # Fallback: policz ile <article> elementów
                articles = html.count('<article')
                if articles > 0: return articles
        except Exception:
            pass

        # Jeśli scraping zawiódł zupełnie (timeout, 403, 429) -> Używamy AI
        _log(f"[scout] Allegro access failed. Estimating competition via AI for '{product_name_pl[:30]}...'")
        return _estimate_competition_ai(product_name_pl)
        
    except Exception as e:
        _log(f"[scout] Allegro competition check error: {e}")
        return _estimate_competition_ai(product_name_pl)


def _estimate_competition_ai(product_name: str) -> int:
    """Używa Gemini do oszacowania nasycenia rynku na Allegro dla danej nazwy produktu."""
    try:
        from modules.gemini_helper import get_gemini_api_key
        api_key = get_gemini_api_key()
        if not api_key: return 10 # Safely assume medium competition

        prompt = f"""Estimate the competition/popularity on Polish marketplace 'Allegro' for this product: "{product_name}".
How many sellers roughly offer this EXACT OR VERY SIMILAR product?
Options: 0 (new niche), 5 (low), 20 (medium), 100 (high), 500+ (saturated).
Return ONLY an integer representing the estimated count (or closest match from options)."""

        from modules.utils import get_gemini_api_url
        api_url = get_gemini_api_url(api_key)
        
        resp = requests.post(
            api_url,
            json={'contents': [{'parts': [{'text': prompt}]}]},
            timeout=10
        )
        if resp.status_code == 200:
            text = resp.json()['candidates'][0]['content']['parts'][0]['text']
            num = re.search(r'\d+', text)
            if num:
                return int(num.group())
    except Exception:
        pass
    return 15 # Default fallback


# ─── ALLEGRO SEARCH VIA WEB SCRAPING ─────────────────────────────────────────

def _scrape_allegro_search(phrase: str, limit: int = 60) -> list[dict]:
    """
    Scrapuje publiczną stronę wyszukiwania Allegro.
    Zwraca listę produktów z nazwą, ceną, URL i obrazkiem.
    """
    import urllib.parse, re
    
    query = urllib.parse.quote_plus(phrase)
    url = f"https://allegro.pl/listing?string={query}&order=qd"  # qd = wg popularności
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'pl-PL,pl;q=0.9',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    
    results = []
    
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            _log(f"[scout_phrase] Allegro HTTP {resp.status_code}")
            return []
        
        html = resp.text
        
        # Szukaj JSON-LD lub data atrybutów z ofertami
        # Allegro renderuje oferty w <article> blokach
        # Parsujemy tytuły, ceny i linki
        
        # Metoda 1: JSON embedded w stronie (preferowana)
        json_blocks = re.findall(r'<script[^>]*type="application/json"[^>]*>(.+?)</script>', html, re.DOTALL)
        for block in json_blocks:
            try:
                data = json.loads(block)
                # Allegro sometimes embeds listing data in JSON
                items = _extract_items_from_json(data)
                if items:
                    results.extend(items[:limit])
                    break
            except:
                continue
        
        # Metoda 2: Parse HTML bezpośrednio jeśli JSON nie zadziałał
        if not results:
            # Szukaj wzorca: nazwa produktu + cena
            # <a href="/oferta/..." title="...">
            offer_links = re.findall(
                r'<a[^>]*href="(https://allegro\.pl/oferta/[^"]+)"[^>]*>.*?</a>',
                html, re.DOTALL
            )
            
            # Szukaj tytułów i cen
            titles = re.findall(r'<h2[^>]*>([^<]{10,200})</h2>', html)
            prices = re.findall(r'(\d+[\s,]\d{2})\s*zł', html)
            images = re.findall(r'<img[^>]*src="(https://[^"]*allegro[^"]*\.(?:jpg|jpeg|png|webp))"', html, re.I)
            
            for i, title in enumerate(titles[:limit]):
                price_val = 0
                if i < len(prices):
                    try:
                        price_val = float(prices[i].replace(' ', '').replace(',', '.'))
                    except:
                        price_val = 0
                
                img = images[i] if i < len(images) else ''
                link = offer_links[i] if i < len(offer_links) else f'https://allegro.pl/listing?string={query}'
                
                results.append({
                    'name': title.strip(),
                    'price': price_val,
                    'url': link,
                    'image': img,
                    'sold': 50 + (limit - i) * 3,  # Estymacja popularności (wyżej = popularniejsze)
                })
        
        _log(f"[scout_phrase] Scraped {len(results)} items from Allegro for '{phrase}'")
        
    except Exception as e:
        _log(f"[scout_phrase] Scraping error: {e}")
    
    return results[:limit]


def _extract_items_from_json(data, depth=0):
    """Rekursywnie szuka listy ofert w zagnieżdżonym JSON z Allegro."""
    if depth > 8:
        return []
    
    if isinstance(data, list):
        items = []
        for item in data:
            if isinstance(item, dict):
                # Sprawdź czy to oferta (ma name/title + price)
                name = item.get('name') or item.get('title') or item.get('productName', '')
                price = None
                
                # Szukaj ceny w różnych formatach
                if 'price' in item:
                    p = item['price']
                    if isinstance(p, dict):
                        price = p.get('amount') or p.get('value') or p.get('normal', {}).get('amount')
                    elif isinstance(p, (int, float)):
                        price = p
                elif 'sellingMode' in item:
                    price = item['sellingMode'].get('price', {}).get('amount')
                
                if name and price:
                    try:
                        price_float = float(str(price).replace(',', '.').replace(' ', ''))
                    except:
                        price_float = 0
                    
                    img = ''
                    if 'image' in item:
                        img = item['image'] if isinstance(item['image'], str) else item['image'].get('url', '')
                    elif 'images' in item and item['images']:
                        img = item['images'][0] if isinstance(item['images'][0], str) else item['images'][0].get('url', '')
                    
                    url = item.get('url') or item.get('href') or ''
                    if url and not url.startswith('http'):
                        url = f"https://allegro.pl{url}"
                    
                    sold = item.get('popularity') or item.get('soldCount') or 50
                    
                    items.append({
                        'name': name,
                        'price': price_float,
                        'url': url,
                        'image': img,
                        'sold': int(sold) if sold else 50,
                    })
                else:
                    # Zagłęb się dalej
                    for v in item.values():
                        if isinstance(v, (dict, list)):
                            sub = _extract_items_from_json(v, depth + 1)
                            if sub:
                                items.extend(sub)
        return items
    
    elif isinstance(data, dict):
        for v in data.values():
            if isinstance(v, (dict, list)):
                sub = _extract_items_from_json(v, depth + 1)
                if sub:
                    return sub
    
    return []


# ─── SCOUT PHRASE (WYSZUKIWANIE ALLEGRO) ─────────────────────────────────────

def scout_by_phrase(phrase: str, filters: dict = None) -> list[dict]:
    """
    Szuka produktów na Alibaba/AliExpress pasujących do frazy,
    potem sprawdza konkurencję na Allegro i oblicza marżę.
    Flow: Chiny (źródło) → Allegro (sprawdzenie rynku) → Wynik
    """
    if filters is None: filters = {}
    use_margin = filters.get('margin', True)
    use_comp = filters.get('competition', True)
    use_price = filters.get('price', True)
    
    # Przetłumacz na angielski dla lepszych wyników w Chinach
    phrase_en = _translate_to_en(phrase)
    _log(f"[scout_phrase] START szukam '{phrase}' (EN: '{phrase_en}') na Alibaba/AliExpress...")
    
    try:
        # ===== KROK 1: Szukaj produktów na Alibaba =====
        alibaba_products = _scrape_alibaba_search(phrase_en, limit=30)
        _log(f"[scout_phrase] Alibaba: znaleziono {len(alibaba_products)} produktów")
        
        # ===== KROK 2: Szukaj produktów na AliExpress =====
        aliexpress_products = _scrape_aliexpress_search(phrase_en, limit=20)
        _log(f"[scout_phrase] AliExpress: znaleziono {len(aliexpress_products)} produktów")
        
        # ===== KROK 2b: Fallback Gemini (jeśli scrapery zawiodły) =====
        all_products = alibaba_products + aliexpress_products
        if not all_products:
            _log(f"[scout_phrase] Brak wyników ze scrapingu dla '{phrase_en}'. Uruchamiam Gemini AI Search...")
            all_products = _gemini_search_fallback(phrase_en)
            _log(f"[scout_phrase] Gemini AI Search zwrócił {len(all_products)} produktów")
        
        if not all_products:
            error_msg = f'Brak wyników z Alibaba/AliExpress dla: "{phrase}". Spróbuj po angielsku!'
            from modules.gemini_helper import get_gemini_api_key
            if not get_gemini_api_key():
                error_msg = f'Błąd: Nie skonfigurowano klucza Gemini AI (sprawdź gemini_config.py)!'
            
            return [{
                'id': 'no-data', 
                'product_name': error_msg, 
                'category': '-', 'source': 'ALI',
                'source_url': f'https://www.alibaba.com/trade/search?SearchText={phrase}', 
                'buy_price_pln': 0, 'sell_price_pln': 0, 
                'margin_percent': 0, 'trend_score': 0, 'final_score': 0.0,
                'paczkomat_fit': '-', 'status': 'keep_new', 'image_url': '', 
                'allegro_competition': 0, 'created_at': datetime.now().isoformat()
            }]
        
        # ===== KROK 3: Dla każdego produktu sprawdź Allegro + oblicz marżę =====
        candidates = []
        seen = set()
        
        for prod in all_products:
            name = prod.get('name', '')
            if not name or len(name) < 5:
                continue
                
            norm = _normalize(name)
            if norm in seen: continue
            seen.add(norm)
            
            buy_price_usd = prod.get('price_usd', 0)
            buy_price_pln = buy_price_usd * 4.2  # kurs USD→PLN
            
            if buy_price_pln <= 0:
                continue  # Nie mamy ceny zakupu — pomijamy
            
            # Filtr cenowy (cena zakupu z Chin w PLN)
            if use_price and buy_price_pln > 500:
                continue
            
            # Estymacja ceny sprzedaży na Allegro (markup x2.5-x3)
            sell_price_estimate = buy_price_pln * 2.5
            if use_price and sell_price_estimate < 50:
                continue
                
            # Sprawdź konkurencję na Allegro
            name_pl = _translate_to_pl(name) if not any(c in name.lower() for c in ['ą','ę','ó','ś','ł','ż','ź','ć','ń']) else name
            
            comp_exact = 0
            if use_comp:
                comp_exact = _check_allegro_competition(name_pl)
                if comp_exact > 50:
                    _log(f"[scout_phrase] SKIP (konkurencja={comp_exact}): {name[:40]}")
                    continue
            
            # Oblicz marżę
            margin_percent = ((sell_price_estimate - buy_price_pln) / buy_price_pln) * 100
            if use_margin and margin_percent < 30:
                continue
            
            # Score: niska konkurencja = lepiej, wysoka marża = lepiej
            score = margin_percent + max(0, (100 - comp_exact))
            
            image_url = prod.get('image', '')
            source_url = prod.get('url', '')
            source = prod.get('source', 'alibaba')
            
            candidates.append({
                'id': str(uuid.uuid4())[:8],
                'product_name': name_pl if name_pl != name else name,
                'product_name_en': name,
                'category': prod.get('category', phrase),
                'source': source,
                'source_url': source_url,
                'buy_price_pln': round(buy_price_pln, 2),
                'sell_price_pln': round(sell_price_estimate, 2),
                'margin_percent': round(margin_percent, 1),
                'trend_score': round(score, 1),
                'final_score': round(score, 1),
                'paczkomat_fit': 'B',
                'status': 'keep_new',
                'image_url': image_url,
                'allegro_competition': comp_exact,
                'created_at': datetime.now().isoformat()
            })
            
            # Limit iteracji żeby nie czekać wieczność
            if len(candidates) >= 10:
                break
        
        if not candidates:
            return [{
                'id': 'empty', 
                'product_name': f'Znaleziono {len(all_products)} produktów z Chin, ale filtry odrzuciły wszystkie. Spróbuj zmienić filtry.', 
                'category': '-', 'source': 'alibaba',
                'source_url': f'https://www.alibaba.com/trade/search?SearchText={phrase}', 
                'buy_price_pln': 0, 'sell_price_pln': 0, 
                'margin_percent': 0, 'trend_score': 0, 'final_score': 0,
                'paczkomat_fit': '-', 'status': 'keep_new', 'image_url': '', 
                'allegro_competition': 0, 'created_at': datetime.now().isoformat()
            }]
        
        # Sortuj: marża↓, konkurencja↑
        candidates.sort(key=lambda x: (-x['margin_percent'], x['allegro_competition']))
        
        # Jeśli wciąż brak produktów (np. filtry odrzuciły wszystko), a mieliśmy wyniki z Chin
        if not candidates and all_products:
             _log("[scout_phrase] AI Fallback: Scrapery zawiodły, próbuję Gemini discovery...")
             ai_products = _gemini_search_fallback(phrase_en)
             if ai_products:
                 all_products += ai_products
                 # (Procedura sprawdzania ich na Allegro powtórzyłaby się tutaj, ale dla uproszczenia
                 #  po prostu dodajemy je do puli i lecimy dalej w następnej iteracji lub ponownym wywołaniu)
        
        _log(f"[scout_phrase] GOTOWE: {len(candidates)} kandydatów dla '{phrase}'")
        return candidates[:10]
        
    except Exception as e:
        _log(f"[scout_phrase] Error: {e}")
        return []


def _scrape_alibaba_search(phrase_en: str, limit: int = 30) -> list[dict]:
    """Szuka produktów na Alibaba.com po frazie (EN). Używa showroom URL dla lepszej stabilności."""
    products = []
    try:
        # Alibaba showroom URL jest bardziej stabilny
        query_dash = phrase_en.lower().replace(' ', '-')[:80]
        search_url = f'https://www.alibaba.com/showroom/{query_dash}.html'
        
        session = requests.Session()
        session.headers.update(HEADERS)
        
        resp = session.get(search_url, timeout=12, allow_redirects=True)
        if resp.status_code != 200:
            # Fallback na standardowy search
            query_plus = phrase_en.replace(' ', '+')
            search_url = f'https://www.alibaba.com/trade/search?SearchText={query_plus}&viewtype=G'
            resp = session.get(search_url, timeout=12)
            
        if resp.status_code != 200:
            _log(f"[scout_phrase] Alibaba HTTP {resp.status_code}")
            return []
        
        html = resp.text
        
        # Pattern 1: a.product-title
        titles = re.findall(r'class="[^"]*product-title[^"]*"[^>]*title="([^"]+)"', html)
        if not titles:
            titles = re.findall(r'class="[^"]*elements-title-normal[^"]*"[^>]*>([^<]+)<', html)
            
        urls = re.findall(r'href="(https://www\.alibaba\.com/product-detail/[^"]+)"', html)
        if not urls:
             urls = re.findall(r'href="(//www\.alibaba\.com/product-detail/[^"]+)"', html)
             urls = [f'https:{u}' for u in urls]
             
        prices = re.findall(r'\$\s*([\d.]+)\s*(?:-\s*\$\s*[\d.]+)?', html)
        
        images = re.findall(r'<img[^>]*(?:data-src|src)="(//[^"]*alibaba[^"]*\.(?:jpg|jpeg|png|webp))"', html, re.I)
        images = [f'https:{img}' for img in images]

        for i in range(min(len(titles), limit)):
            name = titles[i].strip()
            if len(name) < 10: continue
            
            p_val = 0
            if i < len(prices):
                try: p_val = float(prices[i])
                except: pass
                
            products.append({
                'name': name,
                'price_usd': p_val,
                'url': urls[i] if i < len(urls) else search_url,
                'image': images[i] if i < len(images) else '',
                'source': 'alibaba',
                'category': phrase_en,
            })
        
        _log(f"[scout_phrase] Alibaba search: found {len(products)} items")
        
    except Exception as e:
        _log(f"[scout_phrase] Alibaba search error: {e}")
    
    return products


def _scrape_aliexpress_search(phrase_en: str, limit: int = 20) -> list[dict]:
    """Szuka produktów na AliExpress używając JSONa z window.runParams."""
    products = []
    try:
        query_enc = requests.utils.quote(phrase_en[:80])
        search_url = f'https://www.aliexpress.com/wholesale?SearchText={query_enc}&SortType=total_tranpro_desc'
        
        session = requests.Session()
        session.headers.update(HEADERS)
        
        resp = session.get(search_url, timeout=12, allow_redirects=True)
        if resp.status_code != 200:
            _log(f"[scout_phrase] AliExpress HTTP {resp.status_code}")
            return []
        
        html = resp.text
        
        # Wyciągnij JSON z window.runParams
        json_match = re.search(r'window\.runParams\s*=\s*(\{.+?\});', html, re.DOTALL)
        if not json_match:
            json_match = re.search(r'data-gwd-json="(\{.+?\})"', html)
            
        if json_match:
            try:
                raw_json = json_match.group(1)
                data = json.loads(raw_json)
                items_data = _extract_items_from_json(data)
                if items_data:
                    for item in items_data[:limit]:
                        products.append({
                            'name': item.get('name', ''),
                            'price_usd': item.get('price', 0),
                            'url': item.get('url', ''),
                            'image': item.get('image', ''),
                            'source': 'aliexpress',
                            'category': phrase_en,
                        })
                    return products
            except:
                pass

        # Fallback na regex
        titles = re.findall(r'"subject":"([^"]{10,120})"', html)
        prices = re.findall(r'\"minPrice\":\"([\d.]+)\"', html)
        urls = re.findall(r'"productDetailUrl":"(https://[^"]+)"', html)
        
        for i in range(min(len(titles), limit)):
            products.append({
                'name': titles[i],
                'price_usd': float(prices[i]) if i < len(prices) else 0,
                'url': urls[i] if i < len(urls) else search_url,
                'image': '',
                'source': 'aliexpress',
                'category': phrase_en,
            })
            
    except Exception as e:
        _log(f"[scout_phrase] AliExpress error: {e}")
    
    return products



def _score_candidate(candidate: dict) -> dict:
    """
    Oblicza score dla kandydata.
    Modyfikuje dict in-place i zwraca go.
    """
    buy = candidate.get('buy_price_pln', 0) or 0
    sell = candidate.get('sell_price_pln', 0) or 0
    growth = candidate.get('growth_7d', 0) or 0
    competition = candidate.get('allegro_competition', -1)
    ali_moq = candidate.get('alibaba_moq', 0) or 0
    ali_price = candidate.get('alibaba_price_usd', 0) or 0

    # Trend score (0-100) — based on growth
    if growth >= 100:
        trend_score = 100
    elif growth >= 50:
        trend_score = 70 + int((growth - 50) * 0.6)
    elif growth >= 20:
        trend_score = 40 + int((growth - 20) * 1.0)
    else:
        trend_score = max(10, growth * 2)

    # Margin score (0-100) — based on buy vs sell
    if buy > 0 and sell > 0:
        prowizja = sell * 0.11  # Allegro commission
        wysylka = 12.0  # Shipping estimate
        profit = sell - buy - prowizja - wysylka
        margin_pct = (profit / buy) * 100 if buy > 0 else 0

        if margin_pct >= 300:
            margin_score = 100
        elif margin_pct >= 200:
            margin_score = 75 + int((margin_pct - 200) * 0.25)
        elif margin_pct >= 100:
            margin_score = 40 + int((margin_pct - 100) * 0.35)
        elif margin_pct >= 50:
            margin_score = 20 + int((margin_pct - 50) * 0.4)
        else:
            margin_score = max(0, int(margin_pct * 0.4))

        candidate['margin_percent'] = round(margin_pct, 1)
    else:
        margin_score = 30  # Unknown
        candidate['margin_percent'] = 0

    # Novelty score (0-100) — how new is this on Allegro
    if competition >= 0:
        if competition < 10:
            novelty_score = 100  # Prawie nic na Allegro
        elif competition < 50:
            novelty_score = 80
        elif competition < 200:
            novelty_score = 50
        elif competition < 1000:
            novelty_score = 25
        else:
            novelty_score = 10
    else:
        novelty_score = 50  # Unknown

    # Competition score (0-100) — less = better
    if competition >= 0:
        if competition < 20:
            comp_score = 100
        elif competition < 50:
            comp_score = 80
        elif competition < 150:
            comp_score = 50
        elif competition < 500:
            comp_score = 25
        else:
            comp_score = 10
    else:
        comp_score = 50

    # Sourcing score (0-100) — Alibaba availability
    sourcing_score = 30  # Base
    if ali_price > 0:
        sourcing_score += 30
    if ali_moq > 0 and ali_moq <= 300:
        sourcing_score += 20
    elif ali_moq > 300 and ali_moq <= 500:
        sourcing_score += 10
    if candidate.get('alibaba_trade_assurance'):
        sourcing_score += 10
    if candidate.get('alibaba_url'):
        sourcing_score += 10
    sourcing_score = min(100, sourcing_score)

    # Final score — weighted
    final = (
        0.30 * trend_score +
        0.20 * margin_score +
        0.20 * novelty_score +
        0.15 * comp_score +
        0.15 * sourcing_score
    )

    candidate['trend_score'] = trend_score
    candidate['margin_score'] = margin_score
    candidate['novelty_score'] = novelty_score
    candidate['competition_score'] = comp_score
    candidate['sourcing_score'] = sourcing_score
    candidate['final_score'] = round(final, 1)

    return candidate


# ─── GŁÓWNY ORKIESTRATOR ─────────────────────────────────────────────────────

def force_unlock():
    """Wymusza zwolnienie locka (np. po crashu)."""
    global _scan_started_at
    try:
        _scan_lock.release()
    except RuntimeError:
        pass
    _scan_started_at = 0
    _log("[scout] Lock wymuszony reset")


def run_scout_scan() -> dict:
    """
    Uruchamia pełny skan Winning Scout.
    Returns: dict z wynikami
    """
    global _scan_started_at

    # Auto-unlock jeśli skan trwa >5 minut (utknął)
    if _scan_started_at > 0 and (time.time() - _scan_started_at) > 300:
        _log("[scout] Skan utknął >5min — wymuszam unlock")
        force_unlock()

    if not _scan_lock.acquire(blocking=False):
        elapsed = int(time.time() - _scan_started_at) if _scan_started_at > 0 else 0
        return {
            'error': f'Skan już trwa ({elapsed}s). Poczekaj na zakończenie.',
            'running': True,
        }

    _scan_started_at = time.time()

    try:
        init_scout_tables()

        from modules.database import get_db, get_config, set_config
        conn = get_db()

        # Cooldown check
        last_run = get_config('scout_last_run', '')
        cooldown_min = int(get_config('scout_cooldown_minutes', '10'))
        if last_run:
            try:
                last_dt = datetime.fromisoformat(last_run)
                diff = (datetime.now() - last_dt).total_seconds() / 60
                if diff < cooldown_min:
                    remaining = int(cooldown_min - diff)
                    return {
                        'error': f'Poczekaj {remaining} min. Ostatni skan: {last_run[:16]}',
                        'cooldown': True,
                        'minutes_remaining': remaining,
                    }
            except (ValueError, TypeError):
                pass

        start_time = time.time()
        batch_id = uuid.uuid4().hex[:12]
        now_str = datetime.now().isoformat(sep=" ", timespec="seconds")

        _log(f"[scout] === START batch {batch_id} ===")

        # ── 1. Załaduj blacklist ─────────────────────────────────────────
        blacklist = _load_blacklist()
        existing_names = list(blacklist['names'])[:30]
        _log(f"[scout] Blacklist: {len(blacklist['names'])} nazw, {len(blacklist['asins'])} ASIN-ów")

        # ── 2. Zbierz kandydatów ze źródeł ──────────────────────────────
        all_candidates = []

        # 2a. Gemini trend discovery (najbardziej niezawodne)
        _log("[scout] >>> Faza 1: Gemini AI discovery...")
        gemini_products = _gemini_discover_trends(existing_names)
        _log(f"[scout] Gemini zwrócił {len(gemini_products)} produktów")
        all_candidates.extend(gemini_products)

        # 2b. Amazon bestsellers (próbuj 2-3 kategorie)
        _log("[scout] >>> Faza 2: Amazon bestsellers...")
        for cat_name, cat_url in list(AMAZON_CATEGORIES.items())[:3]:
            try:
                amazon_products = _scrape_amazon_bestsellers(cat_name, cat_url, limit=10)
                all_candidates.extend(amazon_products)
                time.sleep(2)  # Rate limiting
            except Exception as e:
                _log(f"[scout] Amazon {cat_name}: {e}")

        # 2c. AliExpress trending
        try:
            ali_products = _scrape_aliexpress_trending(limit=15)
            all_candidates.extend(ali_products)
        except Exception as e:
            _log(f"[scout] AliExpress: {e}")

        _log(f"[scout] Zebrano {len(all_candidates)} kandydatów z wszystkich źródeł")

        if not all_candidates:
            set_config('scout_last_run', datetime.now().isoformat())
            return {
                'batch_id': batch_id,
                'error': 'Nie znaleziono żadnych kandydatów. Sprawdź klucz Gemini.',
                'products_found': 0,
            }

        # ── 3. Filtruj duplikaty i blacklist ─────────────────────────────
        kept = []
        rejected = []
        seen_normalized = set()

        for cand in all_candidates:
            name = cand.get('name', '')
            if not name or len(name) < 5:
                continue

            norm = _normalize(name)

            # Deduplikacja wewnętrzna
            if norm in seen_normalized:
                continue
            seen_normalized.add(norm)

            status, reason = _check_duplicate(name, blacklist)

            if status != 'keep_new':
                rejected.append({**cand, 'status': status, 'reject_reason': reason})
                continue

            kept.append(cand)

        _log(f"[scout] Po filtrze: {len(kept)} kept, {len(rejected)} rejected")

        # ── 4. Wzbogać dane — Alibaba + Allegro ─────────────────────────
        _log(f"[scout] >>> Faza 4: Wzbogacanie danych ({len(kept)} produktów)...")
        for i, cand in enumerate(kept):
            name = cand['name']
            _log(f"[scout] Produkt {i+1}/{len(kept)}: {name[:40]}")

            # Tłumacz na polski
            name_pl = _translate_to_pl(name)
            cand['product_name_pl'] = name_pl

            # Generuj linki search (szybkie, bez scrapowania)
            query_enc = requests.utils.quote(name[:80])
            if not cand.get('alibaba_url'):
                cand['alibaba_url'] = f'https://www.alibaba.com/trade/search?SearchText={query_enc}'
            if not cand.get('source_url'):
                cand['source_url'] = f'https://www.aliexpress.com/w/wholesale-{query_enc.replace("%20", "-")}.html'

            # Zdjęcie — pobierz miniaturkę z DuckDuckGo
            if i < 20:  # Pierwsze 20 produktów
                img = _fetch_product_image(name)
                if img:
                    cand['image_url'] = img
                    _log(f"[scout] Zdjęcie OK: {name[:25]}")
                else:
                    cand['image_url'] = ''
                time.sleep(0.5)

            # Przelicz buy price jeśli brak
            if not cand.get('buy_price_pln') and cand.get('alibaba_price_usd'):
                cand['buy_price_pln'] = round(cand['alibaba_price_usd'] * 4.2, 2)

            # Allegro competition (pierwsze 10 — szybciej)
            if i < 10:
                comp = _check_allegro_competition(name_pl)
                cand['allegro_competition'] = comp
                time.sleep(0.3)
            else:
                cand['allegro_competition'] = -1

            # Reject jeśli brak hurtowej opcji
            if (not cand.get('alibaba_price_usd') and
                not cand.get('buy_price_pln') and
                cand.get('source') != 'gemini'):
                cand['status'] = 'reject_no_wholesale'
                cand['reject_reason'] = 'Brak ceny hurtowej na Alibaba'
                rejected.append(cand)
                continue

        # Usuń odrzucone z kept
        kept = [c for c in kept if c.get('status') != 'reject_no_wholesale']

        # ── 5. Scoring ───────────────────────────────────────────────────
        for cand in kept:
            _score_candidate(cand)
            cand['status'] = 'keep_new'

        # Sortuj po score
        kept.sort(key=lambda x: x.get('final_score', 0), reverse=True)

        # ── 6. Zapisz do DB ──────────────────────────────────────────────
        saved_count = 0

        # Zapisz kept
        for cand in kept:
            try:
                conn.execute("""
                    INSERT INTO winning_candidates (
                        batch_id, product_name, product_name_pl, category, source,
                        source_url, alibaba_url, alibaba_moq, alibaba_price_usd,
                        alibaba_supplier, alibaba_trade_assurance,
                        buy_price_pln, sell_price_pln, margin_percent,
                        trend_score, margin_score, novelty_score,
                        competition_score, sourcing_score, final_score,
                        status, reject_reason, why_new, why_can_sell,
                        risk_flags, paczkomat_fit, ali_rating,
                        allegro_competition, growth_7d, image_url
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    batch_id,
                    cand.get('name', '')[:300],
                    cand.get('product_name_pl', '')[:300],
                    cand.get('category', '')[:100],
                    cand.get('source', 'unknown')[:50],
                    cand.get('source_url', '')[:500],
                    cand.get('alibaba_url', '')[:500],
                    cand.get('alibaba_moq', 0),
                    cand.get('alibaba_price_usd', 0),
                    cand.get('alibaba_supplier', '')[:200],
                    1 if cand.get('alibaba_trade_assurance') else 0,
                    cand.get('buy_price_pln', 0),
                    cand.get('sell_price_pln', 0),
                    cand.get('margin_percent', 0),
                    cand.get('trend_score', 0),
                    cand.get('margin_score', 0),
                    cand.get('novelty_score', 0),
                    cand.get('competition_score', 0),
                    cand.get('sourcing_score', 0),
                    cand.get('final_score', 0),
                    cand.get('status', 'keep_new'),
                    cand.get('reject_reason', ''),
                    cand.get('why_new', '')[:500],
                    cand.get('why_can_sell', '')[:500],
                    cand.get('risk_flags', '')[:500],
                    cand.get('paczkomat_fit', 'B'),
                    cand.get('ali_rating', 0),
                    cand.get('allegro_competition', -1),
                    cand.get('growth_7d', 0),
                    cand.get('image_url', '')[:500],
                ))
                saved_count += 1
            except Exception as e:
                _log(f"[scout] DB insert error: {e}")

        # Zapisz rejected (max 10)
        for cand in rejected[:10]:
            try:
                conn.execute("""
                    INSERT INTO winning_candidates (
                        batch_id, product_name, product_name_pl, category, source,
                        source_url, status, reject_reason, final_score
                    ) VALUES (?,?,?,?,?,?,?,?,0)
                """, (
                    batch_id,
                    cand.get('name', '')[:300],
                    cand.get('product_name_pl', _translate_to_pl(cand.get('name', '')))[:300],
                    cand.get('category', '')[:100],
                    cand.get('source', 'unknown')[:50],
                    cand.get('source_url', '')[:500],
                    cand.get('status', 'rejected'),
                    cand.get('reject_reason', '')[:500],
                ))
            except Exception as e:
                _log(f"[scout] DB insert rejected error: {e}")

        conn.commit()

        duration = round(time.time() - start_time, 1)
        set_config('scout_last_run', datetime.now().isoformat())

        # ── 7. Kategoryzuj top 20 ────────────────────────────────────────
        top_kept = kept[:20]

        # Podział na grupy
        safest = [c for c in top_kept if c.get('margin_score', 0) > 50 and c.get('competition_score', 0) > 40][:5]
        high_margin = sorted(top_kept, key=lambda x: x.get('margin_percent', 0), reverse=True)[:5]
        emerging = sorted(top_kept, key=lambda x: x.get('novelty_score', 0), reverse=True)[:5]
        wildcard = sorted(top_kept, key=lambda x: x.get('trend_score', 0), reverse=True)[:5]

        _log(f"[scout] === DONE batch {batch_id}: {saved_count} saved, {len(rejected)} rejected in {duration}s ===")

        return {
            'batch_id': batch_id,
            'products_found': saved_count,
            'rejected_count': len(rejected),
            'duration_s': duration,
            'categories': {
                'safest_bets': len(safest),
                'high_margin': len(high_margin),
                'emerging': len(emerging),
                'wildcard': len(wildcard),
            },
            'top_3': [
                {'name': c.get('product_name_pl', c['name'])[:60], 'score': c.get('final_score', 0)}
                for c in top_kept[:3]
            ],
        }

    except Exception as e:
        _log(f"[scout] run_scout_scan error: {e}", exc_info=True)
        return {'error': f'Błąd skanu: {str(e)}', 'products_found': 0}

    finally:
        _scan_started_at = 0
        _scan_lock.release()


# ─── QUERY FUNCTIONS ──────────────────────────────────────────────────────────

def get_scout_results(
    batch_id: str = None,
    status_filter: str = None,
    limit: int = 50,
    offset: int = 0,
    min_score: float = 0.0,
) -> tuple[list[dict], int]:
    """Pobiera wyniki skanowania z DB."""
    try:
        from modules.database import get_db
        conn = get_db()

        where_parts = []
        params = []

        if batch_id:
            where_parts.append("batch_id = ?")
            params.append(batch_id)

        if status_filter:
            if status_filter == 'kept':
                where_parts.append("status = 'keep_new'")
            elif status_filter == 'rejected':
                where_parts.append("status LIKE 'reject_%'")
            else:
                where_parts.append("status = ?")
                params.append(status_filter)

        if min_score > 0:
            where_parts.append("final_score >= ?")
            params.append(min_score)

        where = "WHERE " + " AND ".join(where_parts) if where_parts else ""

        total = conn.execute(
            f"SELECT COUNT(*) FROM winning_candidates {where}", params
        ).fetchone()[0]

        rows = conn.execute(f"""
            SELECT * FROM winning_candidates
            {where}
            ORDER BY final_score DESC, created_at DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()

        return [dict(r) for r in rows], total

    except Exception as e:
        _log(f"[scout] get_scout_results error: {e}")
        return [], 0


def get_latest_batch_id() -> str:
    """Zwraca batch_id ostatniego skanu."""
    try:
        from modules.database import get_db
        row = get_db().execute(
            "SELECT batch_id FROM winning_candidates ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return row['batch_id'] if row else ''
    except Exception:
        return ''


def get_scout_stats() -> dict:
    """Statystyki dla UI."""
    try:
        from modules.database import get_db, get_config
        conn = get_db()

        last_batch = get_latest_batch_id()

        # Statystyki ze WSZYSTKICH skanów (nie kasujemy starych)
        stats = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'keep_new' THEN 1 ELSE 0 END) as kept,
                SUM(CASE WHEN status LIKE 'reject_%' THEN 1 ELSE 0 END) as rejected,
                COUNT(DISTINCT batch_id) as scans
            FROM winning_candidates
        """).fetchone()

        return {
            'total': stats['total'] or 0,
            'kept': stats['kept'] or 0,
            'rejected': stats['rejected'] or 0,
            'scans': stats['scans'] or 0,
            'last_run': get_config('scout_last_run', ''),
            'batch_id': last_batch or '',
        }
    except Exception as e:
        _log(f"[scout] get_scout_stats error: {e}")
        return {'total': 0, 'kept': 0, 'rejected': 0, 'last_run': '', 'batch_id': ''}


# Inicjalizuj tabele przy imporcie
try:
    init_scout_tables()
except Exception as _init_e:
    _log(f"[scout] Init tables error: {_init_e}")



