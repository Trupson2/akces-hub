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
    logger.info("[scout] Scheduler uruchomiony (co 24h)")


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
                    logger.info("[scout] Auto-skan uruchomiony")
                    try:
                        run_scout_scan()
                    except Exception as e:
                        logger.error(f"[scout] Auto-skan błąd: {e}")

            time.sleep(600)  # Sprawdzaj co 10 minut

        except Exception as e:
            logger.error(f"[scout] Scheduler error: {e}")
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

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
        logger.info("[scout] Tabela winning_candidates zainicjalizowana")
    except Exception as e:
        logger.error(f"[scout] init_scout_tables error: {e}")


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

    except Exception as e:
        logger.warning(f"[scout] Błąd ładowania blacklist: {e}")

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
        logger.warning(f"[scout] Translate error: {e}")

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
            logger.warning(f"[scout] Amazon {category}: HTTP {resp.status_code}")
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

        logger.info(f"[scout] Amazon {category}: {len(products)} produktów")

    except Exception as e:
        logger.warning(f"[scout] Amazon {category} scrape error: {e}")

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

        logger.info(f"[scout] AliExpress trending: {len(products)} produktów")

    except Exception as e:
        logger.warning(f"[scout] AliExpress scrape error: {e}")

    return products


# ─── GEMINI: TREND DISCOVERY ─────────────────────────────────────────────────

def _gemini_discover_trends(existing_names: list[str]) -> list[dict]:
    """
    Używa Gemini AI do odkrywania trendujących produktów.
    To jest najbardziej niezawodne źródło trendów.
    """
    products = []

    try:
        from modules.database import get_config
        api_key = get_config('gemini_api_key', '')
        if not api_key:
            logger.warning("[scout] Brak klucza Gemini — pomijam AI discovery")
            return []

        existing_str = ', '.join(existing_names[:30]) if existing_names else 'brak'

        prompt = f"""Jesteś ekspertem od e-commerce na Allegro.pl w Polsce.
Znajdź 25 trendujących produktów do sprzedaży na Allegro w 2026 roku.

WYMAGANIA:
- Niski koszt zakupu z Chin (poniżej 30 PLN/szt)
- Małe i lekkie (mieszczą się w Paczkomat A/B/C)
- Marża >200% (kup za <30 PLN, sprzedaj za >80 PLN)
- Problem-solving (nie jednorazowe gadżety)
- Sezon 2026: outdoor, smart home, fitness, auto, pet tech, beauty tools
- Dostępne na Alibaba/AliExpress w hurcie (MOQ 50-300 szt)

NIE PROPONUJ produktów z tej listy (już sprzedajemy):
{existing_str}

NIE PROPONUJ: kamerek samochodowych, obciążników, projektorów galaktyk,
uchwytów telefonicznych, powerbanków, ładowarek bezprzewodowych, smartwatchy,
fitness trackerów, kosiarek, pił teleskopowych.

ODPOWIEDZ TYLKO jako JSON array (bez markdown):
[
  {{
    "name": "English product name",
    "category": "kategoria po polsku",
    "buy_price_usd": 3.5,
    "sell_price_pln": 89,
    "source": "aliexpress",
    "why_new": "dlaczego warto (1 zdanie)",
    "why_can_sell": "dlaczego się sprzeda na Allegro (1 zdanie)",
    "risk_flags": "ryzyka (1 zdanie)",
    "paczkomat_fit": "A/B/C",
    "growth_7d": 65,
    "alibaba_moq": 100,
    "alibaba_price_usd": 2.8
  }}
]"""

        from modules.utils import get_gemini_api_url
        resp = requests.post(
            get_gemini_api_url(api_key),
            json={
                'contents': [{'parts': [{'text': prompt}]}],
                'generationConfig': {
                    'maxOutputTokens': 4000,
                    'temperature': 0.9,
                    'responseMimeType': 'application/json',
                }
            },
            timeout=60,
        )

        if resp.status_code != 200:
            logger.warning(f"[scout] Gemini HTTP {resp.status_code}: {resp.text[:200]}")
            return []

        data = resp.json()
        text = ''
        try:
            text = data['candidates'][0]['content']['parts'][0]['text']
        except (KeyError, IndexError):
            logger.warning("[scout] Gemini — brak odpowiedzi")
            return []

        # Parsuj JSON
        try:
            items = json.loads(text)
        except json.JSONDecodeError:
            # Spróbuj wyciągnąć JSON z tekstu
            match = re.search(r'\[.*\]', text, re.DOTALL)
            if match:
                items = json.loads(match.group())
            else:
                logger.warning("[scout] Gemini — nie udało się sparsować JSON")
                return []

        if not isinstance(items, list):
            return []

        for item in items:
            if not isinstance(item, dict) or not item.get('name'):
                continue

            buy_usd = float(item.get('buy_price_usd', 0) or 0)
            sell_pln = float(item.get('sell_price_pln', 0) or 0)
            buy_pln = buy_usd * 4.2  # Kurs USD/PLN

            products.append({
                'name': item['name'],
                'category': item.get('category', 'inne'),
                'source': item.get('source', 'aliexpress'),
                'source_url': '',
                'buy_price_pln': round(buy_pln, 2),
                'sell_price_pln': sell_pln,
                'why_new': item.get('why_new', ''),
                'why_can_sell': item.get('why_can_sell', ''),
                'risk_flags': item.get('risk_flags', ''),
                'paczkomat_fit': item.get('paczkomat_fit', 'B'),
                'growth_7d': int(item.get('growth_7d', 0) or 0),
                'alibaba_moq': int(item.get('alibaba_moq', 0) or 0),
                'alibaba_price_usd': float(item.get('alibaba_price_usd', 0) or 0),
            })

        logger.info(f"[scout] Gemini discovery: {len(products)} produktów")

        # Log Gemini usage
        try:
            from modules.pallet_monitor import log_gemini_usage
            log_gemini_usage(data, 'winning_scout')
        except Exception:
            pass

    except Exception as e:
        logger.error(f"[scout] Gemini discovery error: {e}")

    return products


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

        resp = session.get(search_url, timeout=15, allow_redirects=True)
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

    except Exception as e:
        logger.debug(f"[scout] Alibaba search error for '{product_name[:30]}': {e}")

    return result


# ─── ALLEGRO COMPETITION CHECK ───────────────────────────────────────────────

def _check_allegro_competition(product_name_pl: str) -> int:
    """Sprawdza ile ofert jest na Allegro dla tego produktu. Zwraca liczbę."""
    try:
        from modules.allegro_api import allegro_request, is_authenticated
        if not is_authenticated():
            return -1

        result = allegro_request("GET", "/offers/listing", params={
            "phrase": product_name_pl[:80],
            "limit": 1,
        })

        if isinstance(result, tuple):
            data, err = result
        else:
            data, err = result, None

        if err or not data:
            return -1

        # Total count
        total = 0
        try:
            search_meta = data.get('searchMeta', {})
            total = search_meta.get('totalCount', 0)
            if not total:
                items = data.get('items', {})
                total = len(items.get('regular', [])) + len(items.get('promoted', []))
        except Exception:
            pass

        return total

    except Exception as e:
        logger.debug(f"[scout] Allegro competition check error: {e}")
        return -1


# ─── SCORING ──────────────────────────────────────────────────────────────────

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
    logger.info("[scout] Lock wymuszony reset")


def run_scout_scan() -> dict:
    """
    Uruchamia pełny skan Winning Scout.
    Returns: dict z wynikami
    """
    global _scan_started_at

    # Auto-unlock jeśli skan trwa >5 minut (utknął)
    if _scan_started_at > 0 and (time.time() - _scan_started_at) > 300:
        logger.warning("[scout] Skan utknął >5min — wymuszam unlock")
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
        cooldown_min = int(get_config('scout_cooldown_minutes', '30'))
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

        logger.info(f"[scout] === START batch {batch_id} ===")

        # ── 1. Załaduj blacklist ─────────────────────────────────────────
        blacklist = _load_blacklist()
        existing_names = list(blacklist['names'])[:30]
        logger.info(f"[scout] Blacklist: {len(blacklist['names'])} nazw, {len(blacklist['asins'])} ASIN-ów")

        # ── 2. Zbierz kandydatów ze źródeł ──────────────────────────────
        all_candidates = []

        # 2a. Gemini trend discovery (najbardziej niezawodne)
        gemini_products = _gemini_discover_trends(existing_names)
        all_candidates.extend(gemini_products)

        # 2b. Amazon bestsellers (próbuj 2-3 kategorie)
        for cat_name, cat_url in list(AMAZON_CATEGORIES.items())[:3]:
            try:
                amazon_products = _scrape_amazon_bestsellers(cat_name, cat_url, limit=10)
                all_candidates.extend(amazon_products)
                time.sleep(2)  # Rate limiting
            except Exception as e:
                logger.warning(f"[scout] Amazon {cat_name}: {e}")

        # 2c. AliExpress trending
        try:
            ali_products = _scrape_aliexpress_trending(limit=15)
            all_candidates.extend(ali_products)
        except Exception as e:
            logger.warning(f"[scout] AliExpress: {e}")

        logger.info(f"[scout] Zebrano {len(all_candidates)} kandydatów z wszystkich źródeł")

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

        logger.info(f"[scout] Po filtrze: {len(kept)} kept, {len(rejected)} rejected")

        # ── 4. Wzbogać dane — Alibaba + Allegro ─────────────────────────
        for i, cand in enumerate(kept):
            name = cand['name']

            # Tłumacz na polski
            name_pl = _translate_to_pl(name)
            cand['product_name_pl'] = name_pl

            # Alibaba search (jeśli brak danych z Gemini)
            if not cand.get('alibaba_price_usd'):
                ali_data = _search_alibaba(name)
                cand['alibaba_url'] = ali_data['url']
                cand['alibaba_moq'] = ali_data['moq'] or cand.get('alibaba_moq', 0)
                cand['alibaba_price_usd'] = ali_data['price_usd'] or cand.get('alibaba_price_usd', 0)
                cand['alibaba_supplier'] = ali_data['supplier']
                cand['alibaba_trade_assurance'] = ali_data['trade_assurance']
                time.sleep(1)  # Rate limit

            # Przelicz buy price jeśli brak
            if not cand.get('buy_price_pln') and cand.get('alibaba_price_usd'):
                cand['buy_price_pln'] = round(cand['alibaba_price_usd'] * 4.2, 2)

            # Allegro competition (pierwsze 15 produktów)
            if i < 15:
                comp = _check_allegro_competition(name_pl)
                cand['allegro_competition'] = comp
                time.sleep(0.5)
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
                        allegro_competition, growth_7d
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                ))
                saved_count += 1
            except Exception as e:
                logger.warning(f"[scout] DB insert error: {e}")

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
                logger.debug(f"[scout] DB insert rejected error: {e}")

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

        logger.info(f"[scout] === DONE batch {batch_id}: {saved_count} saved, {len(rejected)} rejected in {duration}s ===")

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
        logger.error(f"[scout] run_scout_scan error: {e}", exc_info=True)
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
        logger.error(f"[scout] get_scout_results error: {e}")
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
        if not last_batch:
            return {'total': 0, 'kept': 0, 'rejected': 0, 'last_run': '', 'batch_id': ''}

        stats = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'keep_new' THEN 1 ELSE 0 END) as kept,
                SUM(CASE WHEN status LIKE 'reject_%' THEN 1 ELSE 0 END) as rejected,
                MAX(created_at) as last_run
            FROM winning_candidates
            WHERE batch_id = ?
        """, (last_batch,)).fetchone()

        return {
            'total': stats['total'] or 0,
            'kept': stats['kept'] or 0,
            'rejected': stats['rejected'] or 0,
            'last_run': get_config('scout_last_run', ''),
            'batch_id': last_batch,
        }
    except Exception as e:
        logger.error(f"[scout] get_scout_stats error: {e}")
        return {'total': 0, 'kept': 0, 'rejected': 0, 'last_run': '', 'batch_id': ''}


# Inicjalizuj tabele przy imporcie
try:
    init_scout_tables()
except Exception as _init_e:
    logger.warning(f"[scout] Init tables error: {_init_e}")
