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

# Hub `kategoria` → WC product_cat slug(i). Hero tiles z theme: audio/wnetrze/narzedzia/elektronika.
KATEGORIA_MAP: Dict[str, List[str]] = {
    'audio': ['audio'],
    'agd': ['wnetrze'],
    'wnetrze': ['wnetrze'],
    'wnętrze': ['wnetrze'],
    'narzedzia': ['narzedzia'],
    'narzędzia': ['narzedzia'],
    'elektronika': ['elektronika'],
    # 'inne' i puste -> brak categories (WC default = Uncategorized)
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
    """Hub kategoria → list of WC product_cat slugs (puste = []) ."""
    if not kategoria_raw:
        return []
    key = kategoria_raw.strip().lower()
    return list(KATEGORIA_MAP.get(key, []))


def _build_sku(hub_id: int, ean: str) -> str:
    """SKU = EAN-{ean} (jeśli walidny EAN 8-14 cyfr), inaczej HUB-{id}.

    Plugin regex /^[A-Z0-9-]{3,64}$/ — oba formaty pasują.
    """
    ean = (ean or '').strip()
    if ean and EAN_REGEX.match(ean):
        return f'EAN-{ean}'
    return f'HUB-{hub_id}'


def map_hub_to_plugin(row: dict, gpsr: Optional[Dict] = None) -> dict:
    """Map Hub `produkty` row → plugin REST payload.

    Args:
        row:   Hub `produkty` row dict
        gpsr:  opcjonalnie GPSR data dict (zwykle z amazon_gpsr_scraper.fetch_gpsr().to_plugin_payload());
               gdy obecne (z manufacturer_name lub responsible_person_name) → produkt publish; inaczej draft

    Returns payload dict ready to JSON-serialize. NIE wysyła; tylko mapuje.
    """
    hub_id = int(row['id'])
    ean = (row.get('ean') or '').strip()
    sku = _build_sku(hub_id, ean)

    title = (row.get('krotki_tytul') or '').strip() or (row.get('nazwa') or '').strip()

    # Price: cena_brutto > cena_allegro > cena_netto * 1.23 (fallback VAT)
    price = float(row.get('cena_brutto') or 0)
    if price <= 0:
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
    if row.get('opis_ai'):
        payload['description'] = (row['opis_ai'] or '').strip()
    cats = _norm_kategoria(row.get('kategoria') or '')
    if cats:
        payload['categories'] = cats
    if row.get('dostawca'):
        payload['brand'] = (row['dostawca'] or '').strip()
    if ean and EAN_REGEX.match(ean):
        payload['ean'] = ean
    if row.get('zdjecie_url'):
        payload['images'] = [{'url': (row['zdjecie_url'] or '').strip(), 'alt': title}]

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

def push_one_product(hub_product_id: int, with_gpsr: bool = True, gpsr_region: str = 'de') -> dict:
    """Push 1 Hub produkt by ID. Returns dict z status / sku / response / hub_id.

    Args:
        with_gpsr:    auto-fetch GPSR z Amazon (cache lub HTTP) i dodaj do payload (default True;
                      bez GPSR plugin tworzy produkt jako draft)
        gpsr_region:  region Amazon dla GPSR lookup (de/pl/uk/it/fr; default 'de')
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

    payload = map_hub_to_plugin(row_dict, gpsr=gpsr_payload)

    ok, err = validate_payload(payload)
    if not ok:
        return {
            'status': 'error',
            'hub_id': hub_product_id,
            'sku': payload.get('sku'),
            'msg': err,
        }

    # Idempotency: check if already synced by this sku
    if already_synced(conn, payload['sku']):
        return {
            'status': 'skip',
            'hub_id': hub_product_id,
            'sku': payload['sku'],
            'msg': 'już zsynchronizowany (mirror sklepakces_products)',
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
) -> Iterator[dict]:
    """Iteruj Hub produkty status=`magazyn` AND nie w mirror, pushuj każdy.

    Args:
        limit: max produktów do push (None = wszystkie eligible)
        dry_run: pokaż payloady, nie wysyłaj (do test)
        only_status: filter Hub `status` column (default 'magazyn' = ready to sell)

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
            payload = map_hub_to_plugin(row_dict)
            ok, err = validate_payload(payload)
            yield {
                'dry_run': True,
                'hub_id': row_dict['id'],
                'sku': payload.get('sku'),
                'title': payload.get('title'),
                'price': payload.get('price_pln'),
                'condition': payload.get('condition'),
                'stock': payload.get('stock'),
                'valid': ok,
                'validation_error': err,
            }
            continue

        # Throttle (plugin RATE_LIMIT = 60/min)
        if i > 0:
            time.sleep(THROTTLE_SECONDS)

        result = push_one_product(row_dict['id'], with_gpsr=with_gpsr, gpsr_region=gpsr_region)
        yield result
