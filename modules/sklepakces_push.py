"""
sklepakces_push.py — Hub → sklepakces.pl WC product OUTGOING sync.

Czyta produkty z Hub `produkty` table, mapuje na plugin REST schema,
podpisuje HMAC, POSTuje do https://sklepakces.pl/wp-json/akces/v1/products.

Mirror table `sklepakces_products` (po stronie Huba) trackuje co już wysłane
— idempotent: sku jako natural key (EAN-{ean} jeśli walidny EAN, inaczej
HUB-{hub_product_id}).

Plugin schema (z class-akces-product-sync.php):
  required: sku, title, price_pln, condition, stock
  optional: slug, description, categories, brand, ean, images, gpsr
  SKU_REGEX:        /^[A-Z0-9-]{3,64}$/
  ALLOWED_CONDITIONS: nowy | jak-nowy | uzywane | slady-uzywania
  RATE_LIMIT:       60 req / 60s per IP (throttle 1.1s/req w batchu)

Config (Hub `config` table):
  sklepakces_url            (default 'https://sklepakces.pl')
  sklepakces_hmac_secret    (TEN SAM co akces_hub_hmac_secret w WP plugin)

Usage:
  from modules.sklepakces_push import push_one_product, push_all_unsynced
  push_one_product(hub_product_id=42)
  results = list(push_all_unsynced(limit=10, dry_run=True))

CLI: scripts/push_sklepakces.py

@author: Akces Hub
"""
import json
import logging
import re
import time
import uuid
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

import requests

from .database import get_db, get_config
from .sklepakces_hmac import sign, get_hmac_secret

try:
    from . import sklepakces_telegram  # opcjonalny alert kanał
    _HAS_TELEGRAM = True
except Exception:
    _HAS_TELEGRAM = False

logger = logging.getLogger(__name__)

# URL path: gdzie POSTujemy (HTTP request line)
ENDPOINT_URL_PATH = '/wp-json/akces/v1/products'
# Canonical path do HMAC sign: WP REST router strippuje "/wp-json" przed routingiem
# (per class-akces-hmac.php KONTRAKT PATH: $request->get_route() = "/akces/v1/products").
# Hub MUSI podpisywać tym samym path co plugin verify, INACZEJ akces_invalid_signature.
ENDPOINT_CANONICAL_PATH = '/akces/v1/products'

# Alias dla backward-compat (testy używały ENDPOINT_PATH; teraz wskazuje URL path).
ENDPOINT_PATH = ENDPOINT_URL_PATH

DEFAULT_URL = 'https://sklepakces.pl'
THROTTLE_SECONDS = 1.1   # plugin RATE_LIMIT = 60/min → 1.1s/req = ~54/min safe
HTTP_TIMEOUT = 30

# Pricing source priority:
#   1) oferty.cena WHERE produkt_id=X AND status='aktywna' (REAL aktywne Allegro auction)
#   2) produkty.cena_allegro (DB RRP fallback gdy ALLOW_DB_FALLBACK=True; INACZEJ skip + Telegram alert)
#   3) cena_netto * 1.23 (last-resort fallback supplier price → VAT brutto)
# Threshold "podejrzanie niska": price < koszt_paleta_szt * SUSPICIOUS_MARKUP_THRESHOLD → Telegram alert (push idzie dalej)
SUSPICIOUS_MARKUP_THRESHOLD = 1.3  # 30% min markup nad kosztem zakupu — poniżej alert

# Hub `stan` (wszystkie warianty case/diacritics) → plugin condition (whitelist).
STAN_MAP: Dict[str, str] = {
    'nowy': 'nowy',
    'jak nowy': 'jak-nowy',
    'jak-nowy': 'jak-nowy',
    'jaknowy': 'jak-nowy',
    'używany': 'uzywane',
    'uzywany': 'uzywane',
    'używane': 'uzywane',
    'uzywane': 'uzywane',
    'ślady używania': 'slady-uzywania',
    'slady uzywania': 'slady-uzywania',
    'slady-uzywania': 'slady-uzywania',
    'uszkodzony': 'slady-uzywania',  # closest match w plugin whitelist
    'nieoceniony': 'jak-nowy',       # fallback domyślny
}

# Hub `kategoria` → list of WC product_cat slugs (plugin tworzy term jeśli nie istnieje).
# Theme hero tiles: audio/wnetrze/narzedzia/elektronika — produkty z kategorii powiązanych
# z tile'em dostają DODATKOWO ten slug (np. foto_video → ['foto-video', 'elektronika']),
# żeby pojawiły się i pod specyficzną kategorią i pod hero tile na stronie głównej.
# Klucze normalizowane przez _norm_kategoria (case-insensitive, diacritics handled).
KATEGORIA_MAP: Dict[str, List[str]] = {
    # === AUDIO/RTV hero tile parent: 'elektronika' lub osobny tile ===
    'audio':            ['audio'],
    'car_audio':        ['car-audio', 'audio', 'motoryzacja'],
    'rtv':              ['rtv', 'elektronika'],
    'muzyka':           ['muzyka', 'audio'],

    # === ELEKTRONIKA hero tile family ===
    'elektronika':      ['elektronika'],
    'foto_video':       ['foto-video', 'elektronika'],
    'foto-video':       ['foto-video', 'elektronika'],
    'smart_home':       ['smart-home', 'elektronika'],
    'smart-home':       ['smart-home', 'elektronika'],
    'komputery':        ['komputery', 'elektronika'],
    'telefony':         ['telefony', 'elektronika'],
    'gaming':           ['gaming', 'elektronika'],
    'druk3d':           ['druk-3d', 'elektronika'],
    'druk-3d':          ['druk-3d', 'elektronika'],
    'optyka':           ['optyka', 'elektronika'],
    'cb_radio':         ['cb-radio', 'elektronika'],
    'cb-radio':         ['cb-radio', 'elektronika'],
    'akcesoria':        ['akcesoria', 'elektronika'],

    # === WNĘTRZE hero tile family ===
    'wnetrze':          ['wnetrze'],
    'wnętrze':          ['wnetrze'],
    'agd':              ['agd-male', 'wnetrze'],          # legacy alias
    'agd_male':         ['agd-male', 'wnetrze'],
    'agd-male':         ['agd-male', 'wnetrze'],
    'agd_duze':         ['agd-duze', 'wnetrze'],
    'agd-duze':         ['agd-duze', 'wnetrze'],
    'kuchnia':          ['kuchnia', 'wnetrze'],
    'dekoracje':        ['dekoracje', 'wnetrze'],
    'oswietlenie':      ['oswietlenie', 'wnetrze'],
    'oświetlenie':      ['oswietlenie', 'wnetrze'],
    'tekstylia':        ['tekstylia', 'wnetrze'],
    'dom_ogrod':        ['dom-ogrod', 'wnetrze'],
    'dom-ogrod':        ['dom-ogrod', 'wnetrze'],
    'klimatyzacja':     ['klimatyzacja', 'wnetrze'],

    # === NARZĘDZIA hero tile family ===
    'narzedzia':        ['narzedzia'],
    'narzędzia':        ['narzedzia'],
    'elektronarzedzia': ['elektronarzedzia', 'narzedzia'],
    'budowa':           ['budowa', 'narzedzia'],

    # === MOTORYZACJA (osobna gałąź) ===
    'motoryzacja':      ['motoryzacja'],
    'ev_ladowarki':     ['ev-ladowarki', 'motoryzacja'],
    'ev-ladowarki':     ['ev-ladowarki', 'motoryzacja'],

    # === SPORT/OUTDOOR ===
    'sport':            ['sport'],
    'silownia':         ['silownia', 'sport'],
    'siłownia':         ['silownia', 'sport'],
    'rowery':           ['rowery', 'sport'],
    'hulajnogi':        ['hulajnogi', 'sport'],
    'wedkarstwo':       ['wedkarstwo', 'sport'],
    'wędkarstwo':       ['wedkarstwo', 'sport'],
    'outdoor':          ['outdoor'],

    # === DZIECI/RODZINA ===
    'niemowleta':       ['niemowleta'],
    'niemowlęta':       ['niemowleta'],
    'zabawki':          ['zabawki'],
    'zwierzeta':        ['zwierzeta'],
    'zwierzęta':        ['zwierzeta'],

    # === MODA/LIFESTYLE ===
    'moda':             ['moda'],
    'kosmetyki':        ['kosmetyki'],
    'zdrowie':          ['zdrowie'],
    'rehabilitacja':    ['rehabilitacja', 'zdrowie'],
    'bagaz':            ['bagaz'],
    'bagaż':            ['bagaz'],

    # === BIZNES/HOBBY ===
    'biuro':            ['biuro'],
    'ksiazki':          ['ksiazki'],
    'książki':          ['ksiazki'],
    'hobby':            ['hobby'],
    'prezenty':         ['prezenty'],
    'bezpieczenstwo':   ['bezpieczenstwo'],
    'bezpieczeństwo':   ['bezpieczenstwo'],

    # === SPECJALISTYCZNE ===
    'rolnictwo':        ['rolnictwo'],
    'hydroponika':      ['hydroponika'],
    'laboratorium':     ['laboratorium'],
    'event':            ['event'],
    # 'inne' i puste → brak categories (WC default = Uncategorized)
}

SKU_REGEX = re.compile(r'^[A-Z0-9-]{3,64}$')
EAN_REGEX = re.compile(r'^[0-9]{8,14}$')


# ──────────────────────────────────────────────────────────────────────────────
# Config helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_sklepakces_url() -> str:
    """Base URL sklepakces.pl (bez trailing slash)."""
    return (get_config('sklepakces_url', DEFAULT_URL) or DEFAULT_URL).rstrip('/')


# ──────────────────────────────────────────────────────────────────────────────
# Mapping Hub → Plugin schema
# ──────────────────────────────────────────────────────────────────────────────

def _norm_stan(stan_raw: str) -> str:
    """Hub stan → plugin condition. Case+diacritics insensitive."""
    if not stan_raw:
        return 'jak-nowy'
    key = stan_raw.strip().lower()
    return STAN_MAP.get(key, 'jak-nowy')  # fallback bezpieczny dla unknown


def _norm_kategoria(kategoria_raw: str) -> List[str]:
    """Hub kategoria → list of WC product_cat slugs (puste = []).

    Normalizacja:
      - strip + lower
      - try as-is, potem replace '_'→'-', potem '-'→'_' (Hub używa '_', WC slugs używają '-')
      - keep first match (KATEGORIA_MAP zwraca już listę 1-3 slug)
    """
    if not kategoria_raw:
        return []
    key = kategoria_raw.strip().lower()
    if not key or key == 'inne':
        return []
    # Try exact + underscore/dash variants
    for variant in (key, key.replace('_', '-'), key.replace('-', '_')):
        if variant in KATEGORIA_MAP:
            return list(KATEGORIA_MAP[variant])
    return []


def _build_sku(hub_id: int, ean: str) -> str:
    """SKU = EAN-{ean} (jeśli walidny EAN 8-14 cyfr), inaczej HUB-{id}.

    Plugin regex /^[A-Z0-9-]{3,64}$/ — oba formaty pasują.
    """
    ean = (ean or '').strip()
    if ean and EAN_REGEX.match(ean):
        return f'EAN-{ean}'
    return f'HUB-{hub_id}'


def _get_allegro_active_price(conn, hub_id: int) -> Optional[float]:
    """Pobierz cenę z AKTYWNEJ aukcji Allegro dla danego Hub produktu.

    Zwraca None gdy:
      - brak oferty (produkt nigdy nie był wystawiony)
      - wszystkie oferty są draft/zakonczona/wystawiona (none aktywna)
      - cena <= 0 (sanity check)

    Sortowanie: najnowsza aktywna oferta (po data_aktualizacji DESC) wygrywa
    — gdy user manualnie zaktualizował cenę, ostatnia jest aktualna.
    """
    if conn is None or not hub_id:
        return None
    try:
        row = conn.execute(
            """
            SELECT cena FROM oferty
            WHERE produkt_id = ? AND status = 'aktywna' AND cena > 0
            ORDER BY data_aktualizacji DESC
            LIMIT 1
            """,
            (int(hub_id),),
        ).fetchone()
    except Exception as e:
        logger.warning(f'_get_allegro_active_price hub_id={hub_id} db error: {e}')
        return None
    if not row:
        return None
    cena = row['cena'] if hasattr(row, 'keys') else row[0]
    return float(cena) if cena and float(cena) > 0 else None


def _paleta_koszt_szt(row: dict) -> float:
    """Proporcjonalny koszt zakupu per sztuka dla danego produktu.

    Hub semantyka:
      produkty.cena_brutto = TOTAL koszt allocated dla tego produktu (split palety, with VAT)
      produkty.ilosc       = ilość sztuk w tym produkcie
    → koszt_szt = cena_brutto / ilosc

    Zwraca 0.0 gdy nie da się obliczyć (brak danych) — w tym przypadku
    suspicious-price check zostanie pominięty (lepiej cicho niż false alert).
    """
    try:
        cena_brutto = float(row.get('cena_brutto') or 0)
        ilosc = int(row.get('ilosc') or 0)
        if cena_brutto > 0 and ilosc > 0:
            return cena_brutto / ilosc
    except (ValueError, TypeError):
        pass
    return 0.0


def _collect_image_urls(row: dict, conn=None) -> List[str]:
    """Zbierz wszystkie URLe zdjęć produktu — max 8 (limit plugin/WC media).

    Kolejność źródeł:
      1. produkty.images (JSON array, primary; setowane przy scrape/AI enrichment)
      2. scraped.wszystkie_zdjecia (JSON array, fallback po asin JOIN)
      3. produkty.zdjecie_url (last-resort single)

    De-dup po URL (pierwsze wystąpienie wygrywa).
    """
    urls: List[str] = []
    seen = set()

    def _add(u: str) -> None:
        u = (u or '').strip()
        if u and u not in seen and (u.startswith('http://') or u.startswith('https://')):
            urls.append(u)
            seen.add(u)

    # 1. produkty.images JSON
    images_raw = row.get('images')
    if images_raw:
        try:
            arr = json.loads(images_raw) if isinstance(images_raw, str) else images_raw
            if isinstance(arr, list):
                for item in arr:
                    if isinstance(item, str):
                        _add(item)
                    elif isinstance(item, dict):
                        _add(item.get('url') or item.get('src') or '')
        except (json.JSONDecodeError, TypeError):
            pass

    # 2. scraped.wszystkie_zdjecia po asin JOIN (fallback dla scrapowanych z Amazon)
    if conn is not None and len(urls) < 8:
        asin = (row.get('asin') or '').strip().upper()
        if asin:
            try:
                scraped_row = conn.execute(
                    'SELECT wszystkie_zdjecia FROM scraped WHERE asin = ?', (asin,)
                ).fetchone()
                if scraped_row:
                    wsz = scraped_row['wszystkie_zdjecia'] if hasattr(scraped_row, 'keys') else scraped_row[0]
                    if wsz:
                        arr = json.loads(wsz) if isinstance(wsz, str) else wsz
                        if isinstance(arr, list):
                            for item in arr:
                                if isinstance(item, str):
                                    _add(item)
                                elif isinstance(item, dict):
                                    _add(item.get('url') or item.get('src') or '')
            except (json.JSONDecodeError, TypeError, Exception):
                pass

    # 3. zdjecie_url single (last-resort)
    if not urls and row.get('zdjecie_url'):
        _add(row['zdjecie_url'])

    return urls[:8]  # plugin/WC limit


def map_hub_to_plugin(
    row: dict,
    gpsr: Optional[Dict] = None,
    conn=None,
    allegro_active_price: Optional[float] = None,
) -> dict:
    """Map Hub `produkty` row → plugin REST payload.

    Args:
        row:                   Hub `produkty` row dict
        gpsr:                  opcjonalnie GPSR data dict (zwykle z amazon_gpsr_scraper.fetch_gpsr().to_plugin_payload());
                               gdy obecne (z manufacturer_name lub responsible_person_name) → produkt publish; inaczej draft
        conn:                  opcjonalny DB connection — używany do JOIN scraped.wszystkie_zdjecia (Amazon multi-image
                               fallback przy asin); gdy None → tylko zdjecie_url + produkty.images.
        allegro_active_price:  PRIMARY price — gdy przekazane, NADPISUJE cena_allegro/cena_netto chain.
                               Pochodzi z oferty.cena WHERE status='aktywna' (real-time Allegro auction price).
                               Gdy None i row.get('cena_allegro')=0 → fallback na cena_netto*1.23.

    Returns payload dict ready to JSON-serialize. NIE wysyła; tylko mapuje.

    UWAGA semantyki cen Hub `produkty` (sprawdzone w smart_importer.py):
      cena_brutto  = PROPORCJONALNY KOSZT zakupu per produkt (split palety, z VAT) — WHOLESALE, NIE detal!
      cena_allegro = RRP / cena DETALICZNA (Amazon MSRP / target sell price) — DB FALLBACK!
      cena_netto   = supplier netto (źródłowa cena dostawcy)
    Najlepiej: użyj `allegro_active_price` z `_get_allegro_active_price(conn, hub_id)` przed
    wywołaniem (real-time z aktywnej oferty Allegro). DB fallback chain to ostatnia deska ratunku.
    """
    hub_id = int(row['id'])
    ean = (row.get('ean') or '').strip()
    sku = _build_sku(hub_id, ean)

    title = (row.get('krotki_tytul') or '').strip() or (row.get('nazwa') or '').strip()

    # Price priority:
    # 1. allegro_active_price (REAL active Allegro auction price — z oferty.cena status='aktywna')
    # 2. cena_allegro (DB RRP fallback — często stara/zaniżona)
    # 3. cena_netto * 1.23 (supplier price → VAT brutto)
    # cena_brutto NIE używamy — to KOSZT proporcjonalny, nie retail.
    if allegro_active_price and allegro_active_price > 0:
        price = float(allegro_active_price)
    else:
        price = float(row.get('cena_allegro') or 0)
        if price <= 0:
            netto = float(row.get('cena_netto') or 0)
            if netto > 0:
                price = round(netto * 1.23, 2)

    payload: dict = {
        'sku': sku,
        'title': title,
        'price_pln': price,
        'condition': _norm_stan(row.get('stan') or ''),
        'stock': int(row.get('ilosc') or 0),
    }

    # --- Optional ---
    # description_html (KONTRAKT: plugin sanitize_payload czyta 'description_html' a NIE 'description'!)
    if row.get('opis_ai'):
        payload['description_html'] = (row['opis_ai'] or '').strip()
    cats = _norm_kategoria(row.get('kategoria') or '')
    if cats:
        payload['categories'] = cats
    if row.get('dostawca'):
        payload['brand'] = (row['dostawca'] or '').strip()
    if ean and EAN_REGEX.match(ean):
        payload['ean'] = ean

    # Images — multi-source collect (max 8). Pierwszy = primary (cover).
    image_urls = _collect_image_urls(row, conn=conn)
    if image_urls:
        payload['images'] = [
            {'url': u, 'alt': title, 'is_primary': (i == 0)}
            for i, u in enumerate(image_urls)
        ]

    # GPSR — dodaj gdy dostarczone i ma minimum manufacturer lub responsible_person.
    # Plugin GPSR gate (class-akces-gpsr.is_compliant): manufacturer OR responsible_person → publish, inaczej draft.
    if gpsr and (gpsr.get('manufacturer_name') or gpsr.get('responsible_person_name')):
        payload['gpsr'] = gpsr

    return payload


def validate_payload(payload: dict) -> Tuple[bool, Optional[str]]:
    """Pre-flight validate przed POST. Returns (ok, error_msg)."""
    if not SKU_REGEX.match(payload.get('sku', '')):
        return False, f'sku {payload.get("sku")!r} nie pasuje regex [A-Z0-9-]{{3,64}}'
    if not payload.get('title'):
        return False, 'title puste (krotki_tytul i nazwa nieustawione w Hub)'
    if (payload.get('price_pln') or 0) <= 0:
        return False, f'price_pln <= 0 ({payload.get("price_pln")}); ustaw cena_brutto/cena_allegro w Hub'
    if payload.get('condition') not in ('nowy', 'jak-nowy', 'uzywane', 'slady-uzywania'):
        return False, f'condition {payload.get("condition")!r} poza whitelistą'
    if not isinstance(payload.get('stock'), int):
        return False, 'stock nie jest int'
    return True, None


# ──────────────────────────────────────────────────────────────────────────────
# HTTP push
# ──────────────────────────────────────────────────────────────────────────────

def push_product(
    payload: dict,
    url: Optional[str] = None,
    secret: Optional[str] = None,
    timeout: int = HTTP_TIMEOUT,
) -> Tuple[int, dict]:
    """Sign HMAC + POST do plugin endpoint.

    Returns (http_status, response_json_or_error_dict).
    """
    if url is None:
        url = get_sklepakces_url()
    if secret is None:
        secret = get_hmac_secret()

    if not url:
        raise RuntimeError('sklepakces_url nieskonfigurowany — set_config("sklepakces_url", "https://sklepakces.pl")')
    if not secret:
        raise RuntimeError('sklepakces_hmac_secret nieskonfigurowany — set_config("sklepakces_hmac_secret", "<64 hex chars z plugin WP option akces_hub_hmac_secret>")')

    # Canonical: METHOD:PATH:TS:BODY (TA SAMA forma co plugin verify).
    # KRYTYCZNE: path = ENDPOINT_CANONICAL_PATH (bez "/wp-json"), bo plugin verify
    # używa $request->get_route() który WP REST router odcina o "/wp-json" prefix.
    body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
    ts = int(time.time())
    nonce = str(uuid.uuid4())
    signature = sign('POST', ENDPOINT_CANONICAL_PATH, ts, body, secret)

    headers = {
        'Content-Type': 'application/json',
        'X-Akces-Timestamp': str(ts),
        'X-Akces-Signature': signature,
        'X-Akces-Nonce': nonce,
        'User-Agent': 'AkcesHub-Push/1.0',
    }

    try:
        r = requests.post(
            url + ENDPOINT_URL_PATH,
            data=body.encode('utf-8'),
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as e:
        logger.warning(f'sklepakces push HTTP fail: {e}')
        return 0, {'error': f'request failed: {e}'}

    try:
        resp_json = r.json()
    except Exception:
        resp_json = {'raw_body': r.text[:500]}

    return r.status_code, resp_json


# ──────────────────────────────────────────────────────────────────────────────
# Idempotency: mirror table sklepakces_products
# ──────────────────────────────────────────────────────────────────────────────

def already_synced(conn, sku: str) -> bool:
    """Check sklepakces_products mirror table — czy sku już wysłany pomyślnie."""
    cur = conn.execute('SELECT 1 FROM sklepakces_products WHERE sku = ? LIMIT 1', (sku,))
    return cur.fetchone() is not None


def record_sync(conn, payload: dict, wc_product_id: Optional[int], success: bool) -> None:
    """Insert/update sklepakces_products mirror po pomyślnej syncrze.

    Schema: wc_product_id UNIQUE — upsert by wc_product_id.
    Pomijamy zapis gdy wc_product_id brak (np. error przed kreacją WC produktu).
    """
    if wc_product_id is None or not success:
        return  # nie zaśmiecaj mirror gdy push fail (osobny log via record_log)

    try:
        conn.execute(
            """
            INSERT INTO sklepakces_products (wc_product_id, sku, name, regular_price, stock_quantity, product_data, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(wc_product_id) DO UPDATE SET
                sku = excluded.sku,
                name = excluded.name,
                regular_price = excluded.regular_price,
                stock_quantity = excluded.stock_quantity,
                product_data = excluded.product_data,
                updated_at = excluded.updated_at
            """,
            (
                int(wc_product_id),
                payload.get('sku', ''),
                payload.get('title', ''),
                float(payload.get('price_pln', 0)),
                int(payload.get('stock', 0)),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f'record_sync failed: {e}')


def record_log(conn, sku: str, http_code: int, status_label: str, error_message: Optional[str], duration_ms: int) -> None:
    """Audit log do sklepakces_webhook_log (event_type='product_push')."""
    try:
        conn.execute(
            """
            INSERT INTO sklepakces_webhook_log
                (event_type, wc_order_id, status, http_code, error_message, duration_ms, client_ip, created_at)
            VALUES ('product_push', NULL, ?, ?, ?, ?, NULL, datetime('now'))
            """,
            (status_label, http_code, error_message, duration_ms),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f'record_log failed: {e}')


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def push_one_product(
    hub_product_id: int,
    with_gpsr: bool = True,
    gpsr_region: str = 'de',
    force: bool = False,
    require_allegro_active: bool = True,
) -> dict:
    """Push 1 Hub produkt by ID. Returns dict z status / sku / response / hub_id.

    Args:
        with_gpsr:               auto-fetch GPSR z Amazon (cache lub HTTP) i dodaj do payload (default True)
        gpsr_region:             region Amazon dla GPSR lookup (de/pl/uk/it/fr; default 'de')
        force:                   bypass `already_synced()` mirror check — plugin UPDATE'uje istniejący WC po SKU
        require_allegro_active:  gdy True (default), produkt MUSI mieć aktywną ofertę Allegro (oferty.status='aktywna')
                                 inaczej push SKIP + Telegram alert. Gdy False → fallback do produkty.cena_allegro
                                 (DB) — backward-compat (np. dla produktów premium które nie idą na Allegro).
    """
    conn = get_db()
    cur = conn.execute('SELECT * FROM produkty WHERE id = ?', (hub_product_id,))
    row = cur.fetchone()
    if row is None:
        return {
            'status': 'error',
            'hub_id': hub_product_id,
            'msg': f'Hub produkt id={hub_product_id} nie istnieje',
        }

    row_dict = dict(row)

    # Pobierz cenę z aktywnej oferty Allegro — to NAJWAŻNIEJSZY price source.
    # User chce ceny które FAKTYCZNIE wystawione na Allegro, nie zaśmiecone cena_allegro w DB.
    allegro_active_price = _get_allegro_active_price(conn, hub_product_id)

    sku_preview = _build_sku(hub_product_id, (row_dict.get('ean') or '').strip())
    nazwa = (row_dict.get('krotki_tytul') or row_dict.get('nazwa') or '').strip()

    # GATE: brak aktywnej oferty Allegro → SKIP + Telegram alert.
    if allegro_active_price is None and require_allegro_active:
        try:
            if _HAS_TELEGRAM:
                sklepakces_telegram.alert_no_allegro_offer(hub_product_id, sku_preview, nazwa)
        except Exception as e:
            logger.warning(f'Telegram alert no_allegro_offer failed: {e}')
        return {
            'status': 'skip',
            'hub_id': hub_product_id,
            'sku': sku_preview,
            'msg': 'brak aktywnej oferty Allegro (oferty.status=aktywna) — wystaw na Allegro, potem push --force',
        }

    # Suspicious low price check — pushujemy DALEJ ale wysyłamy alert.
    if allegro_active_price and allegro_active_price > 0:
        koszt_szt = _paleta_koszt_szt(row_dict)
        if koszt_szt > 0:
            markup = allegro_active_price / koszt_szt
            if markup < SUSPICIOUS_MARKUP_THRESHOLD:
                try:
                    if _HAS_TELEGRAM:
                        sklepakces_telegram.alert_suspicious_low_price(
                            hub_product_id, sku_preview, nazwa,
                            allegro_active_price, koszt_szt, markup,
                        )
                except Exception as e:
                    logger.warning(f'Telegram alert suspicious_low_price failed: {e}')

    # Auto-fetch GPSR z Amazon (cache hit szybko, miss → 3s fetch + parse).
    # Gdy compliant → produkt publish. Gdy nie (no asin lub Amazon ma luki) → fallback AKCES jako importer.
    gpsr_payload = None
    if with_gpsr:
        try:
            from .amazon_gpsr_scraper import fetch_gpsr  # lazy import — circular-safe
            asin = (row_dict.get('asin') or '').strip()
            g = fetch_gpsr(
                asin=asin, region=gpsr_region, ean=(row_dict.get('ean') or '').strip(),
                use_cache=True, use_fallback=True,
            )
            if g.is_compliant():
                gpsr_payload = g.to_plugin_payload()
                logger.info(f'GPSR: hub_id={hub_product_id} source={g.source} rp="{g.responsible_person_name[:30]}"')
        except Exception as e:
            logger.warning(f'GPSR fetch failed (push continues without gpsr) hub_id={hub_product_id}: {e}')

    payload = map_hub_to_plugin(
        row_dict, gpsr=gpsr_payload, conn=conn,
        allegro_active_price=allegro_active_price,
    )

    ok, err = validate_payload(payload)
    if not ok:
        return {
            'status': 'error',
            'hub_id': hub_product_id,
            'sku': payload.get('sku'),
            'msg': err,
        }

    # Idempotency: check if already synced by this sku (chyba że --force)
    if not force and already_synced(conn, payload['sku']):
        return {
            'status': 'skip',
            'hub_id': hub_product_id,
            'sku': payload['sku'],
            'msg': 'już zsynchronizowany (mirror sklepakces_products) — użyj force=True aby re-push',
        }

    t0 = time.time()
    http_code, response = push_product(payload)
    duration_ms = int((time.time() - t0) * 1000)

    success = 200 <= http_code < 300
    wc_product_id = None
    if success and isinstance(response, dict):
        wc_product_id = response.get('wc_product_id') or response.get('product_id') or response.get('id')

    # Audit log + mirror update
    err_msg = None
    if not success and isinstance(response, dict):
        err_msg = (response.get('message') or response.get('error') or str(response))[:500]
    record_log(conn, payload['sku'], http_code, 'success' if success else 'error', err_msg, duration_ms)
    record_sync(conn, payload, wc_product_id, success)

    log_func = logger.info if success else logger.warning
    log_func(f'sklepakces push: sku={payload["sku"]} hub_id={hub_product_id} http={http_code} dur={duration_ms}ms')

    return {
        'status': 'ok' if success else 'error',
        'hub_id': hub_product_id,
        'sku': payload['sku'],
        'http_status': http_code,
        'wc_product_id': wc_product_id,
        'duration_ms': duration_ms,
        'response': response,
    }


def push_all_unsynced(
    limit: Optional[int] = None,
    dry_run: bool = False,
    only_status: str = 'magazyn',
    with_gpsr: bool = True,
    gpsr_region: str = 'de',
    require_allegro_active: bool = True,
) -> Iterator[dict]:
    """Iteruj Hub produkty status=`magazyn` AND nie w mirror, pushuj każdy.

    Args:
        limit: max produktów do push (None = wszystkie eligible)
        dry_run: pokaż payloady, nie wysyłaj (do test)
        only_status: filter Hub `status` column (default 'magazyn' = ready to sell)
        require_allegro_active: gdy True (default) skip produktów bez aktywnej oferty Allegro
                                + Telegram alert (zob. push_one_product docstring)

    Yields dict per produkt — generator (streaming, nie blokuje na batchu).
    """
    conn = get_db()
    sql = """
        SELECT p.* FROM produkty p
        WHERE p.status = ?
          AND NOT EXISTS (
              SELECT 1 FROM sklepakces_products s
              WHERE s.sku IN ('EAN-' || p.ean, 'HUB-' || p.id)
          )
        ORDER BY p.id
    """
    params: List = [only_status]
    if limit and limit > 0:
        sql += ' LIMIT ?'
        params.append(int(limit))

    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    logger.info(f'push_all_unsynced: znaleziono {len(rows)} eligible produkt(ów) (status={only_status}, limit={limit}, dry_run={dry_run})')

    for i, row in enumerate(rows):
        row_dict = dict(row)
        if dry_run:
            allegro_price = _get_allegro_active_price(conn, row_dict['id'])
            payload = map_hub_to_plugin(row_dict, conn=conn, allegro_active_price=allegro_price)
            ok, err = validate_payload(payload)
            yield {
                'dry_run': True,
                'hub_id': row_dict['id'],
                'allegro_active_price': allegro_price,
                'has_allegro_offer': allegro_price is not None,
                'sku': payload.get('sku'),
                'title': payload.get('title'),
                'price': payload.get('price_pln'),
                'condition': payload.get('condition'),
                'stock': payload.get('stock'),
                'images_count': len(payload.get('images') or []),
                'valid': ok,
                'validation_error': err,
            }
            continue

        # Throttle (plugin RATE_LIMIT = 60/min)
        if i > 0:
            time.sleep(THROTTLE_SECONDS)

        result = push_one_product(
            row_dict['id'],
            with_gpsr=with_gpsr,
            gpsr_region=gpsr_region,
            require_allegro_active=require_allegro_active,
        )
        yield result
