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

# Bulk delete endpoint — usuwa wszystkie produkty utworzone przez Hub (po meta _akces_hub_id).
BULK_DELETE_URL_PATH = '/wp-json/akces/v1/products/bulk_delete'
BULK_DELETE_CANONICAL_PATH = '/akces/v1/products/bulk_delete'

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


# Nazwy palet / dostawców hurtowych — NIE są to marki produktów ani platformy ogólne.
# User raport: "Marka: Warrington" → Warrington to nazwa palety, nie producent.
# Plus: "zdradzanie dostawcow nie git" — nie pokazuj konkurencji skąd masz hurtowo.
#
# Tylko stricte paletowe brands tu — NIE "amazon"/"allegro" (zbyt szerokie, false
# positives w opisach produktów typu "kompatybilny z Amazon Echo / Allegro Smart").
_PALETA_SUPPLIER_BLACKLIST = {
    'warrington', 'jobalots', 'jobalot', 'jobalots premium',
    'b-stock', 'bstock', 'b stock',
    'miglo',
    'salvex', 'liquidation auction',
    'amazon retourenkauf', 'amazon returns',
    'mix paleta', 'mix pallet',
}


def _is_paleta_supplier(value: str) -> bool:
    """True gdy value to nazwa palety/dostawcy (nie prawdziwa marka producenta)."""
    if not value:
        return True
    v = value.strip().lower()
    if not v:
        return True
    # Exact match
    if v in _PALETA_SUPPLIER_BLACKLIST:
        return True
    # Substring match — np. "Warrington Returns Pallet 2025-08" → contains "warrington"
    for bad in _PALETA_SUPPLIER_BLACKLIST:
        if bad in v:
            return True
    return False


def _sanitize_paleta_names_from_text(text: str) -> str:
    """Wytnij z text fragments które wyglądają na nazwy palet/dostawców.

    Cele:
    - "Warrington Lot 2024-08 - Mini kamera Full HD" → "Mini kamera Full HD"
    - "Jobalots Mix Pallet / Aparat fotograficzny" → "Aparat fotograficzny"
    - "<p>Dostawca: Warrington Returns</p>" → "<p>Dostawca: </p>" → strip pustki
    - Każda fraza zawierająca paleta-supplier name → wytnij całe paragrafy/linie

    Cel BIZNESOWY: nie zdradzaj sklepowi publicznemu skąd masz hurtowo produkty
    (konkurencja by chciała wiedzieć). User raport: "zdradzanie dostawcow nie git".
    """
    if not text:
        return text
    out = text

    # Strip linie/paragraphs które ZAWIERAJĄ nazwę palety (case-insensitive)
    # Patterny: "Dostawca: X", "From: X", "Source: X", "Paleta: X", "Lot: X"
    for bad in _PALETA_SUPPLIER_BLACKLIST:
        # Skip ogólnych słów które mogą być fałszywym positives
        if bad in ('returns', 'pallets', 'pallet', 'palety', 'paleta', 'mix paleta', 'mix pallet'):
            continue
        # Pattern: cała fraza zawierająca tę nazwę (do separatora ./;\n lub end)
        # Np. "Warrington Returns Lot 2024" — match całość przed separator
        bad_escaped = re.escape(bad)
        # Strip w title-line (np. "Warrington XXX / Produkt nazwa" → "Produkt nazwa")
        out = re.sub(
            r'(?i)\b[^./;,\n<>|]*\b' + bad_escaped + r'\b[^./;,\n<>|]*[./;,|]?\s*',
            '',
            out,
        )
        # Strip w HTML paragraph (np. "<p>Dostawca: Warrington XXX</p>" → "")
        out = re.sub(
            r'(?is)<p[^>]*>[^<]*\b' + bad_escaped + r'\b[^<]*</p>\s*',
            '',
            out,
        )
        # Strip w <li> (np. "<li>Marka: Warrington</li>" → "")
        out = re.sub(
            r'(?is)<li[^>]*>[^<]*\b' + bad_escaped + r'\b[^<]*</li>\s*',
            '',
            out,
        )

    # Cleanup multiple whitespace + leading/trailing separators
    out = re.sub(r'\s{2,}', ' ', out)
    out = re.sub(r'^\s*[-–—,;:|/]\s*', '', out)
    out = out.strip()
    return out


def _is_oversize(row: dict) -> bool:
    """Czy produkt jest gabarytowy (NIE mieści się do paczkomatu InPost)?

    Paczkomat InPost C (największy) limits:
      - Wymiary: 41 × 38 × 64 cm
      - Waga: 25 kg
      - Powyżej → kurier (DPD/GLS), NIE paczkomat

    Detection chain:
      1. Kategoria w "oversized" blacklist (rowery, agd_duze, hulajnogi, etc.)
      2. produkty.parameters JSON: waga > 25kg lub wymiar > 64cm
      3. krotki_tytul/nazwa zawiera markery ("XL", "bieżnia", "lodówka", "fotel")

    Plugin (set_oversize_class) ustawia WC shipping class "gabaryt" gdy True →
    WC admin może wyłączyć paczkomat method dla tej class (Shipping Zones config).
    """
    # 1. Kategoria — known oversized
    OVERSIZED_KATEGORIE = {
        'rowery', 'hulajnogi', 'silownia', 'siłownia',
        'agd_duze', 'agd-duze',
        'meble', 'wnetrze',  # niektóre meble (sofy, stoły, łóżka)
    }
    kat = (row.get('kategoria') or '').strip().lower()
    if kat in OVERSIZED_KATEGORIE:
        return True

    # 2. Title markers — keyword detection w nazwie/krotki_tytul
    title = ((row.get('krotki_tytul') or '') + ' ' + (row.get('nazwa') or '')).lower()
    OVERSIZED_KEYWORDS = (
        'bieżnia', 'biezni', 'treadmill', 'walking pad',
        'lodówka', 'lodowka', 'fridge',
        'pralka', 'pralk', 'washing machine',
        'zmywarka', 'dishwasher',
        'piec', 'okap', 'pochłaniacz', 'pochlani',
        'rower ', 'rowerem', 'roweru', 'bike ', 'bicycle',
        'hulajnoga elektr', 'e-scooter',
        'fotel masuj', 'fotel gaming', 'fotel obrotow',
        'sofa', 'kanapa',
        'łóżko polowe', 'lozko polowe', 'cot bed',
        'walizk',  # duże walizki
        'namiot ', 'tent',
        'choink', 'christmas tree',
        'parasol ogrod',
        'grill ',
        'taczka', 'wózek transport', 'wozek transport',
    )
    for kw in OVERSIZED_KEYWORDS:
        if kw in title:
            return True

    # 3. Parameters JSON (Gemini-extracted waga/wymiary)
    try:
        params_raw = row.get('parameters')
        if params_raw:
            params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
            if isinstance(params, dict):
                # Waga: "2.5 kg", "25kg", "25000 g", itp.
                waga_str = str(params.get('waga') or params.get('weight') or '').lower()
                m = re.search(r'(\d+(?:[.,]\d+)?)\s*(kg|g|lb)', waga_str)
                if m:
                    val = float(m.group(1).replace(',', '.'))
                    unit = m.group(2)
                    waga_kg = val if unit == 'kg' else (val / 1000 if unit == 'g' else val * 0.4536)
                    if waga_kg > 25:
                        return True
                # Wymiary: szukamy max wymiaru > 64cm (paczkomat C limit)
                wymiary_str = str(params.get('wymiary') or params.get('dimensions') or '').lower()
                # Match np. "100x80x40cm" → znajdź unit (cm/m) i wszystkie numbers przed.
                # Strategia: znajdź unit suffix, potem wszystkie liczby w stringu before unit.
                unit_match = re.search(r'\b(cm|mm|m)\b', wymiary_str)
                if unit_match:
                    unit = unit_match.group(1)
                    nums = re.findall(r'\d+(?:[.,]\d+)?', wymiary_str)
                    for n in nums:
                        try:
                            val = float(n.replace(',', '.'))
                        except ValueError:
                            continue
                        if unit == 'cm':
                            val_cm = val
                        elif unit == 'mm':
                            val_cm = val / 10
                        else:  # 'm'
                            val_cm = val * 100
                        if val_cm > 64:
                            return True
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    return False


def _generate_minimal_attributes(row: dict) -> List[Dict]:
    """Auto-gen WC product attributes dla Specyfikacja tab z dostępnych Hub fields.

    Returns list of {name, value, visible} dicts. Plugin po stronie WC zapisuje
    jako WC_Product_Attribute → renderowane w "Specyfikacja" tab przez theme
    (single-tabs.php $product->get_attributes()).

    Źródła (z priorytetami):
      1. Stan (produkty.stan)
      2. Marka — PRIORYTET: parameters['brand']/['marka']/['producent'] (Gemini-extracted),
         FALLBACK: produkty.dostawca (TYLKO gdy nie jest paleta/dostawca z _PALETA_SUPPLIER_BLACKLIST)
      3. Kategoria (produkty.kategoria → human label)
      4. EAN (produkty.ean — walidny)
      5. ASIN (produkty.asin — walidny 10 chars)
      6. produkty.parameters JSON (Gemini-extracted specs: model/color/wymiary/itp)

    pa_stan i pa_marka są już osobne taxonomy (theme wyklucza je z Specyfikacja
    żeby uniknąć duplikatu z eyebrow/condition card). Tu dajemy zwykłe attributes.
    """
    attrs = []
    stan = (row.get('stan') or '').strip()
    if stan:
        attrs.append({'name': 'Stan produktu', 'value': stan, 'visible': True})

    # Parsuj parameters JSON jeden raz (używamy do brand priority + reszty)
    params = {}
    params_raw = row.get('parameters')
    if params_raw:
        try:
            p = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
            if isinstance(p, dict):
                params = p
        except (json.JSONDecodeError, TypeError):
            pass

    # MARKA: priority Gemini-extracted brand > dostawca (gdy nie paleta) > skip
    brand_value = ''
    for key in ('brand', 'marka', 'producent', 'manufacturer'):
        v = params.get(key)
        if v and not _is_paleta_supplier(str(v)):
            brand_value = str(v).strip()
            break
    if not brand_value:
        dostawca = (row.get('dostawca') or '').strip()
        if dostawca and not _is_paleta_supplier(dostawca):
            brand_value = dostawca
    if brand_value:
        attrs.append({'name': 'Marka / Producent', 'value': brand_value[:200], 'visible': True})

    kat = (row.get('kategoria') or '').strip()
    if kat and kat.lower() not in ('inne', ''):
        kat_label = kat.replace('_', ' ').replace('-', ' ').title()
        attrs.append({'name': 'Kategoria', 'value': kat_label, 'visible': True})

    ean = (row.get('ean') or '').strip()
    if ean and EAN_REGEX.match(ean):
        attrs.append({'name': 'Kod EAN', 'value': ean, 'visible': True})

    asin = (row.get('asin') or '').strip().upper()
    if asin and len(asin) == 10:
        attrs.append({'name': 'Kod ASIN', 'value': asin, 'visible': True})

    # Pozostałe parameters (skip already-used + helper keys)
    if params:
        skip_keys = {'ean', 'asin', 'sku', 'stan', 'kategoria', 'dostawca', 'marka',
                     'producent', 'brand', 'manufacturer', 'condition', 'category'}
        for k, v in params.items():
            key_lower = str(k).strip().lower()
            if key_lower in skip_keys or not v:
                continue
            name = str(k).replace('_', ' ').replace('-', ' ').strip().title()[:60]
            if isinstance(v, (list, tuple)):
                value = ', '.join(str(x) for x in v if x)[:200]
            else:
                value = str(v).strip()[:200]
            if name and value:
                attrs.append({'name': name, 'value': value, 'visible': True})

    return attrs


def _looks_like_non_polish(text: str) -> bool:
    """Heurystyka: czy text wygląda na obcojęzyczny (FR/DE/EN)?

    Triggers gdy znajdziemy charakterystyczne dla obcych języków słowa, plus
    BRAK polskich diakrytyków (ąćęłńóśźż). Konserwatywnie — false positives
    OK (zostanie original title).
    """
    if not text:
        return False
    t = text.lower()
    # Polskie diakrytyki → na pewno po polsku
    if any(c in t for c in 'ąćęłńóśźż'):
        return False
    # Charakterystyczne francuskie słowa
    fr_markers = ['caméra', 'sans fil', 'pouces', 'voiture', 'étanche', 'deux', 'vision nocturne',
                  'avec', 'pour', 'écran', 'sécurité', 'téléphone', 'maison']
    if any(m in t for m in fr_markers):
        return True
    # Niemieckie
    de_markers = ['kabelloser', 'für ihre', 'mit der', 'wasserdicht', 'überwachung', 'sicherheit',
                  'fernseher', 'küche', 'wohnzimmer']
    if any(m in t for m in de_markers):
        return True
    return False


def _get_scraped_tytul_seo(conn, asin: str) -> str:
    """Pobierz cached PL tytul SEO ze scraped (Hub generuje przy paletomat scrape)."""
    if conn is None or not asin:
        return ''
    asin = asin.strip().upper()
    try:
        row = conn.execute(
            'SELECT tytul_seo FROM scraped WHERE asin = ? AND tytul_seo IS NOT NULL AND tytul_seo != "" LIMIT 1',
            (asin,),
        ).fetchone()
    except Exception:
        return ''
    if not row:
        return ''
    t = row['tytul_seo'] if hasattr(row, 'keys') else row[0]
    return (t or '').strip()


def _strip_images_from_html(html: str) -> str:
    """Remove wszystkie <img>, <figure>, <picture> tags + base64 inline images z HTML.

    User raport: "z allegro ale bez tych zdjec zebyn ie wstawial" — opisy Allegro
    mają inline <img> które dublują się z WC product gallery (theme single-gallery.php
    już pokazuje wszystkie images jako thumb + main). Strippujemy images z opisu
    żeby unik duplikatu + wymusić cleaner content (text + bullets).

    Zachowuje: <p>, <ul>, <li>, <strong>, <em>, links, headings.
    """
    if not html:
        return html
    out = html
    # Strip <img ... />, <img ...> (self-closing or not)
    out = re.sub(r'<img\b[^>]*/?>\s*', '', out, flags=re.IGNORECASE)
    # Strip <figure>...</figure> (Allegro/HTML5 image containers)
    out = re.sub(r'<figure\b[^>]*>.*?</figure>\s*', '', out, flags=re.IGNORECASE | re.DOTALL)
    # Strip <picture>...</picture> (responsive images)
    out = re.sub(r'<picture\b[^>]*>.*?</picture>\s*', '', out, flags=re.IGNORECASE | re.DOTALL)
    # Strip <a> linki które tylko zawierają strippowany image (linki do galerii)
    out = re.sub(r'<a\b[^>]*>\s*</a>\s*', '', out, flags=re.IGNORECASE)
    # Cleanup empty paragraphs/divs po image removal
    out = re.sub(r'<p[^>]*>\s*</p>\s*', '', out, flags=re.IGNORECASE)
    out = re.sub(r'<div[^>]*>\s*</div>\s*', '', out, flags=re.IGNORECASE)
    # Multiple consecutive blank lines → max 2
    out = re.sub(r'\n\s*\n\s*\n+', '\n\n', out)
    return out.strip()


def _get_scraped_opis_html(conn, asin: str) -> str:
    """Pobierz cached opis_html ze scraped table (Hub generuje przy Allegro listing).

    Hub paletomat.py wywołuje generuj_opis_html_pro() podczas wystawiania na
    Allegro i zapisuje wynik do scraped.opis_html (po asin). Reuse tego cache
    dla sklepakces.pl push'a — user nie chce ponownie wywoływać Gemini.

    Returns: HTML opis lub '' gdy brak.
    """
    if conn is None or not asin:
        return ''
    asin = asin.strip().upper()
    try:
        row = conn.execute(
            'SELECT opis_html FROM scraped WHERE asin = ? AND opis_html IS NOT NULL AND opis_html != "" LIMIT 1',
            (asin,),
        ).fetchone()
    except Exception as e:
        logger.debug(f'_get_scraped_opis_html asin={asin}: {e}')
        return ''
    if not row:
        return ''
    opis = row['opis_html'] if hasattr(row, 'keys') else row[0]
    return (opis or '').strip()


def _generate_minimal_description(row: dict) -> str:
    """Auto-generate minimalny opis HTML z dostępnych danych Hub.

    Używany jako last-resort fallback gdy oferty.opis i opis_ai są puste.
    Zwraca HTML z bullet pointami: nazwa, stan, marka, kategoria, EAN.
    Nie zastępuje pełnego opisu (lepiej user wypełni Allegro listing lub
    uruchomi Gemini opis_ai generator), ale chroni przed placeholder
    "Pełny opis produktu zostanie uzupełniony wkrótce" na sklepie.
    """
    nazwa = (row.get('krotki_tytul') or '').strip() or (row.get('nazwa') or '').strip()
    if not nazwa:
        return ''  # bez nazwy nie warto generować

    parts = [f'<p><strong>{nazwa}</strong></p>']
    bullets = []
    stan = (row.get('stan') or '').strip()
    dostawca = (row.get('dostawca') or '').strip()
    kategoria = (row.get('kategoria') or '').strip()
    ean = (row.get('ean') or '').strip()

    if stan:
        bullets.append(f'<li><strong>Stan:</strong> {stan}</li>')
    if dostawca and not _is_paleta_supplier(dostawca):
        bullets.append(f'<li><strong>Marka:</strong> {dostawca}</li>')
    if kategoria and kategoria.lower() not in ('inne', ''):
        bullets.append(f'<li><strong>Kategoria:</strong> {kategoria.replace("_", " ").title()}</li>')
    if ean and EAN_REGEX.match(ean):
        bullets.append(f'<li><strong>EAN:</strong> {ean}</li>')

    if bullets:
        parts.append('<ul>' + ''.join(bullets) + '</ul>')

    parts.append(
        '<p><em>Pełny opis i specyfikacja produktu dostępne na życzenie. '
        'Skontaktuj się z nami w razie pytań.</em></p>'
    )
    return '\n'.join(parts)


def _get_allegro_active_offer(conn, hub_id: int) -> Optional[Dict]:
    """Pobierz AKTYWNĄ ofertę Allegro dla danego Hub produktu (cena + ilosc + opis + tytul).

    Zwraca dict {cena, ilosc, opis, tytul, allegro_id} lub None gdy:
      - brak oferty (produkt nigdy nie był wystawiony)
      - wszystkie oferty są draft/zakonczona/wystawiona (none aktywna)
      - cena <= 0 (sanity check)

    Sortowanie: najnowsza aktywna oferta (po data_aktualizacji DESC) wygrywa
    — gdy user manualnie zaktualizował cenę/stock/opis, ostatnia jest aktualna.
    """
    if conn is None or not hub_id:
        return None
    try:
        row = conn.execute(
            """
            SELECT cena, ilosc, opis, tytul, allegro_id FROM oferty
            WHERE produkt_id = ? AND status = 'aktywna' AND cena > 0
            ORDER BY data_aktualizacji DESC
            LIMIT 1
            """,
            (int(hub_id),),
        ).fetchone()
    except Exception as e:
        logger.warning(f'_get_allegro_active_offer hub_id={hub_id} db error: {e}')
        return None
    if not row:
        return None
    d = dict(row) if hasattr(row, 'keys') else {
        'cena': row[0], 'ilosc': row[1], 'opis': row[2], 'tytul': row[3], 'allegro_id': row[4],
    }
    cena = float(d.get('cena') or 0)
    if cena <= 0:
        return None
    return {
        'cena': cena,
        'ilosc': int(d.get('ilosc') or 0),
        'opis': (d.get('opis') or '').strip(),
        'tytul': (d.get('tytul') or '').strip(),
        'allegro_id': d.get('allegro_id') or '',
    }


def _get_allegro_active_price(conn, hub_id: int) -> Optional[float]:
    """Backward-compat alias — tylko cena z aktywnej oferty Allegro."""
    offer = _get_allegro_active_offer(conn, hub_id)
    return offer['cena'] if offer else None


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
    allegro_active_stock: Optional[int] = None,
    allegro_active_description: Optional[str] = None,
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

    # Title priority: krotki_tytul → nazwa → scraped.tytul_seo (cached Polish translation)
    title = (row.get('krotki_tytul') or '').strip() or (row.get('nazwa') or '').strip()
    # Jeśli title wygląda na obcojęzyczny (np. francuski/niemiecki Amazon name),
    # spróbuj fallback ze scraped.tytul_seo (Hub generuje SEO PL przy paletomat scrape).
    if title and conn is not None and _looks_like_non_polish(title):
        scraped_title = _get_scraped_tytul_seo(conn, row.get('asin') or '')
        if scraped_title:
            title = scraped_title

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

    # Stock priority:
    # 1. allegro_active_stock (REAL oferty.ilosc z aktywnej aukcji Allegro)
    # 2. produkty.ilosc (DB fallback)
    if allegro_active_stock is not None and allegro_active_stock >= 0:
        stock = int(allegro_active_stock)
    else:
        stock = int(row.get('ilosc') or 0)

    payload: dict = {
        'sku': sku,
        'title': title,
        'price_pln': price,
        'condition': _norm_stan(row.get('stan') or ''),
        'stock': stock,
    }

    # --- Optional ---
    # description_html priorytety (od najbardziej user-friendly do auto-gen fallback):
    #   1. allegro_active_description (oferty.opis z aktywnej aukcji — co user wpisał przy listing)
    #   2. opis_ai (produkty.opis_ai — Gemini batch-generated przez enrich_paleta.py)
    #   3. scraped.opis_html JOIN po asin (Hub generuje przy paletomat scrape — CACHE
    #      generated dla Allegro listings, REUSE dla sklepakces by uniknąć ponownego wywołania Gemini)
    #   4. auto-generated z dostępnych danych Hub (nazwa, stan, marka, kategoria) — last resort
    #      Plugin nie pokaze placeholder "Pełny opis ... wkrótce" gdy zwrócimy cokolwiek non-empty.
    # Plugin KONTRAKT: sanitize_payload czyta 'description_html' a NIE 'description'.
    description = ''
    if allegro_active_description and allegro_active_description.strip():
        description = allegro_active_description.strip()
    elif row.get('opis_ai'):
        description = (row['opis_ai'] or '').strip()
    else:
        # Spróbuj scraped.opis_html z cache po asin (Hub generuje dla Allegro listings)
        scraped_opis = _get_scraped_opis_html(conn, row.get('asin') or '')
        if scraped_opis:
            description = scraped_opis
        else:
            description = _generate_minimal_description(row)
    if description:
        # Strip <img>/<figure>/<picture> tags z opisu — sklep ma już galerię w
        # single-gallery.php (cover + thumbs), inline images z Allegro/Amazon
        # opisu dublowałyby się. User: "z allegro ale bez tych zdjec zebyn ie wstawial".
        description = _strip_images_from_html(description)
        # Brand/title sanitize NIE robimy na description (zbyt ryzykowne false-positive
        # z legitnymi produktami "kompatybilny z Amazon Echo" itp.). Brand attr już
        # filtrowane przez _is_paleta_supplier.
        if description:
            payload['description_html'] = description
    cats = _norm_kategoria(row.get('kategoria') or '')
    if cats:
        payload['categories'] = cats
    # Brand priority: Gemini-extracted parameters > dostawca (gdy NIE jest paleta/dostawca)
    brand_payload = ''
    try:
        params_raw = row.get('parameters')
        if params_raw:
            p = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
            if isinstance(p, dict):
                for k in ('brand', 'marka', 'producent', 'manufacturer'):
                    if p.get(k) and not _is_paleta_supplier(str(p[k])):
                        brand_payload = str(p[k]).strip()
                        break
    except (json.JSONDecodeError, TypeError):
        pass
    if not brand_payload:
        dostawca_raw = (row.get('dostawca') or '').strip()
        if dostawca_raw and not _is_paleta_supplier(dostawca_raw):
            brand_payload = dostawca_raw
    if brand_payload:
        payload['brand'] = brand_payload
    if ean and EAN_REGEX.match(ean):
        payload['ean'] = ean

    # Images — multi-source collect (max 8). Pierwszy = primary (cover).
    image_urls = _collect_image_urls(row, conn=conn)
    if image_urls:
        payload['images'] = [
            {'url': u, 'alt': title, 'is_primary': (i == 0)}
            for i, u in enumerate(image_urls)
        ]

    # Attributes (dla Specyfikacja tab) — auto-gen z dostępnych Hub fields + parameters JSON.
    # Plugin zapisuje jako WC_Product_Attribute. Theme renderuje przez $product->get_attributes().
    attrs = _generate_minimal_attributes(row)
    if attrs:
        payload['attributes'] = attrs

    # Gabaryt detection — plugin set_oversize_class ustawi WC shipping class 'gabaryt'
    # → WC admin może wyłączyć paczkomat InPost method dla tej class (Shipping Zones).
    # User: "czy juz jest logika zeby gabarytow do paczkomatu niebrali".
    if _is_oversize(row):
        payload['oversize'] = True

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


def record_sync(conn, payload: dict, wc_product_id: Optional[int], success: bool,
                wc_response: Optional[dict] = None, hub_id: Optional[int] = None) -> None:
    """Insert/update sklepakces_products mirror po pomyślnej syncrze.

    Schema: wc_product_id UNIQUE — upsert by wc_product_id.
    Pomijamy zapis gdy wc_product_id brak (np. error przed kreacją WC produktu).

    Args:
        wc_response: response dict z plugin (zawiera status='publish'|'draft', gpsr_blocked, action)
        hub_id: Hub product ID (wstrzykiwany do product_data dla dashboard JOIN)
    """
    if wc_product_id is None or not success:
        return  # nie zaśmiecaj mirror gdy push fail (osobny log via record_log)

    # Wstrzykiwane fields do product_data dla późniejszego JOIN/display w dashboardzie
    enriched_payload = dict(payload)
    if hub_id is not None:
        enriched_payload['hub_id'] = int(hub_id)
    if wc_response is not None:
        enriched_payload['_last_wc_status'] = wc_response.get('status', '')
        enriched_payload['_last_wc_action'] = wc_response.get('action', '')
        enriched_payload['_last_gpsr_blocked'] = bool(wc_response.get('gpsr_blocked', False))

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
                json.dumps(enriched_payload, ensure_ascii=False),
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

    # Pobierz CENĘ + STOCK z aktywnej oferty Allegro — to NAJWAŻNIEJSZE source'y.
    # User chce ceny i ilosc które FAKTYCZNIE wystawione na Allegro, nie zaśmiecone DB.
    allegro_offer = _get_allegro_active_offer(conn, hub_product_id)
    allegro_active_price = allegro_offer['cena'] if allegro_offer else None
    allegro_active_stock = allegro_offer['ilosc'] if allegro_offer else None

    sku_preview = _build_sku(hub_product_id, (row_dict.get('ean') or '').strip())
    nazwa = (row_dict.get('krotki_tytul') or row_dict.get('nazwa') or '').strip()

    # GATE: brak aktywnej oferty Allegro → SKIP + Telegram alert.
    if allegro_offer is None and require_allegro_active:
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
    # Gdy compliant → produkt publish. Gdy nie (no asin lub Amazon ma luki) → fallback.
    #
    # SMART OPTIMIZATION: jeśli istnieje brand override dla tego produktu (z Gemini brand
    # w parameters JSON), skip Playwright (i tak override będzie nadpisał, no point fetching
    # Amazon side-sheet który zazwyczaj blokowany przez headless detection).
    gpsr_payload = None
    if with_gpsr:
        try:
            from .amazon_gpsr_scraper import fetch_gpsr, get_brand_gpsr_override
            from .database import get_config as _get_cfg
            asin = (row_dict.get('asin') or '').strip()

            # Check brand override first — skip Playwright gdy brand mamy w bazie
            skip_pw = False
            try:
                params_raw = row_dict.get('parameters')
                if params_raw:
                    params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
                    if isinstance(params, dict):
                        for k in ('brand', 'marka', 'producent', 'manufacturer'):
                            v = params.get(k)
                            if v and not _is_paleta_supplier(str(v)):
                                if get_brand_gpsr_override(str(v).strip(), conn=conn):
                                    skip_pw = True
                                    logger.info(f'GPSR brand override exists for "{v}" — skip Playwright')
                                break
            except (json.JSONDecodeError, TypeError, Exception):
                pass

            pw_fallback = (_get_cfg('amazon_gpsr_playwright_fallback', '0') or '0') == '1' and not skip_pw
            g = fetch_gpsr(
                asin=asin, region=gpsr_region, ean=(row_dict.get('ean') or '').strip(),
                use_cache=True, use_fallback=True,
                playwright_fallback=pw_fallback,
            )
            if g.is_compliant():
                gpsr_payload = g.to_plugin_payload()
                logger.info(f'GPSR: hub_id={hub_product_id} source={g.source} rp="{g.responsible_person_name[:30]}"')
        except Exception as e:
            logger.warning(f'GPSR fetch failed (push continues without gpsr) hub_id={hub_product_id}: {e}')

    # ENHANCE: gdy Amazon nie dał manufacturer (~88% przypadków) ALE Gemini wyciągnął
    # real brand do parameters JSON → wpisz brand jako manufacturer_name w GPSR payload.
    if gpsr_payload and not gpsr_payload.get('manufacturer_name'):
        try:
            params_raw = row_dict.get('parameters')
            if params_raw:
                params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
                if isinstance(params, dict):
                    for k in ('brand', 'marka', 'producent', 'manufacturer'):
                        v = params.get(k)
                        if v and not _is_paleta_supplier(str(v)):
                            gpsr_payload['manufacturer_name'] = str(v).strip()[:120]
                            logger.info(f'GPSR manufacturer z Gemini brand: hub_id={hub_product_id} brand="{gpsr_payload["manufacturer_name"]}"')
                            break
                    model = params.get('model')
                    if model and not gpsr_payload.get('model_number'):
                        gpsr_payload['model_number'] = str(model).strip()[:60]
        except (json.JSONDecodeError, TypeError):
            pass

    # BRAND-BASED GPSR OVERRIDE — user: "homca, azdome ze maja tam wlasne gpsr i itp ze to trzeba dac".
    # Pobiera real EU rep z gpsr_brand_overrides (user raz wpisuje per brand z Amazon listing).
    # Plus AUTO-POPULATE: gdy Amazon scrape udało się dla 1 ASIN → save dla reszty produktów tej marki.
    try:
        params_raw = row_dict.get('parameters')
        brand_from_params = ''
        if params_raw:
            params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
            if isinstance(params, dict):
                for k in ('brand', 'marka', 'producent', 'manufacturer'):
                    v = params.get(k)
                    if v and not _is_paleta_supplier(str(v)):
                        brand_from_params = str(v).strip()
                        break
        if brand_from_params:
            from .amazon_gpsr_scraper import get_brand_gpsr_override, save_brand_gpsr_override
            override = get_brand_gpsr_override(brand_from_params, conn=conn)
            if override and (override.get('responsible_person_name') or override.get('manufacturer_name')):
                if not gpsr_payload:
                    gpsr_payload = {}
                for fld in ('manufacturer_name', 'manufacturer_address',
                            'responsible_person_name', 'responsible_person_address',
                            'responsible_person_email'):
                    if override.get(fld) and not gpsr_payload.get(fld):
                        gpsr_payload[fld] = override[fld]
                logger.info(f'GPSR brand override: hub_id={hub_product_id} brand="{brand_from_params}" rp="{override.get("responsible_person_name", "")[:40]}"')
            elif gpsr_payload and gpsr_payload.get('responsible_person_name') and \
                 gpsr_payload['responsible_person_name'] not in ('CET PRODUCT SERVICE SP. Z O.O.', 'AKCES Andrzej Gauza'):
                # AUTO-SAVE: Amazon scrape OK dla TEJ marki (NIE generic fallback) → reuse dla reszty
                save_brand_gpsr_override(brand_from_params, gpsr_payload, source='amazon_scrape', conn=conn)
                logger.info(f'GPSR auto-saved brand override: brand="{brand_from_params}" z Amazon scrape ASIN={row_dict.get("asin", "?")}')
    except Exception as e:
        logger.debug(f'GPSR brand override hub_id={hub_product_id}: {e}')

    payload = map_hub_to_plugin(
        row_dict, gpsr=gpsr_payload, conn=conn,
        allegro_active_price=allegro_active_price,
        allegro_active_stock=allegro_active_stock,
        allegro_active_description=(allegro_offer['opis'] if allegro_offer else None),
    )

    ok, err = validate_payload(payload)
    if not ok:
        # "brak ceny" to SKIP (oczekiwane dla produktów bez Allegro+DB price),
        # nie error — żeby banner pokazywał skip=N nie err=N.
        is_no_price = 'price_pln' in (err or '')
        return {
            'status': 'skip' if is_no_price else 'error',
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

    # DEDUP po EAN — żeby 2 produkty z tym samym EAN nie wylądowały jako 2 osobne
    # produkty na WC (user raport: "2 ofert takich samych nie bylo na sklepie").
    # Hub może mieć 2+ produktów z tym samym EAN (różne paleta_id) — bierzemy
    # pierwszy zsynchronizowany, reszta SKIP.
    ean_payload = (payload.get('ean') or '').strip()
    if ean_payload and not force:
        existing_dup = conn.execute(
            "SELECT wc_product_id, sku FROM sklepakces_products WHERE sku = ? AND sku != ? LIMIT 1",
            (f'EAN-{ean_payload}', payload['sku']),
        ).fetchone()
        if existing_dup:
            return {
                'status': 'skip',
                'hub_id': hub_product_id,
                'sku': payload['sku'],
                'msg': f'duplikat EAN — produkt o EAN={ean_payload} już na sklepie jako '
                       f'{dict(existing_dup).get("sku")} (wc_id={dict(existing_dup).get("wc_product_id")}). '
                       f'Użyj force=True aby UPDATE existing.',
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
    record_sync(conn, payload, wc_product_id, success,
                wc_response=response if isinstance(response, dict) else None,
                hub_id=hub_product_id)

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


def delete_one_from_wc(
    sku: str,
    mode: str = 'trash',
    url: Optional[str] = None,
    secret: Optional[str] = None,
) -> dict:
    """Usuń 1 produkt z WC po SKU (re-uży bulk_delete endpoint z skus=[X]).

    Args:
        sku:  SKU produktu (EAN-X lub HUB-X)
        mode: 'trash' (default, recoverable) lub 'force' (permanent)
    """
    if not sku:
        return {'status': 'error', 'msg': 'sku required'}
    result = delete_all_from_wc(mode=mode, skus=[sku], url=url, secret=secret)
    return result


def delete_all_from_wc(
    mode: str = 'trash',
    skus: Optional[List[str]] = None,
    url: Optional[str] = None,
    secret: Optional[str] = None,
) -> dict:
    """Usuń wszystkie produkty Hub z WC (po meta _akces_hub_id) + czysci mirror.

    Args:
        mode: 'trash' (do kosza WP, recoverable) lub 'force' (permanent delete)
        skus: lista konkretnych SKU do usunięcia; None → wszystkie Hub products
        url, secret: WC URL + HMAC secret (default z config)

    Returns dict z statusem + ile usunięto.
    """
    if mode not in ('trash', 'force'):
        return {'status': 'error', 'msg': f'invalid mode={mode!r} (allowed: trash, force)'}

    if url is None:
        url = get_sklepakces_url()
    if secret is None:
        secret = get_hmac_secret()
    if not url or not secret:
        return {'status': 'error', 'msg': 'brak sklepakces_url / sklepakces_hmac_secret w config'}

    payload = {
        'confirm': 'DELETE_HUB_PRODUCTS',  # safeguard string
        'mode': mode,
    }
    if skus:
        payload['skus'] = list(skus)

    body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
    ts = int(time.time())
    nonce = str(uuid.uuid4())
    signature = sign('POST', BULK_DELETE_CANONICAL_PATH, ts, body, secret)

    headers = {
        'Content-Type': 'application/json',
        'X-Akces-Timestamp': str(ts),
        'X-Akces-Signature': signature,
        'X-Akces-Nonce': nonce,
        'User-Agent': 'AkcesHub-Push/1.0',
    }

    try:
        r = requests.post(
            url + BULK_DELETE_URL_PATH,
            data=body.encode('utf-8'),
            headers=headers,
            timeout=120,  # delete może długo trwać przy ~100 produktów
        )
    except requests.RequestException as e:
        logger.warning(f'sklepakces bulk_delete HTTP fail: {e}')
        return {'status': 'error', 'http_status': 0, 'msg': str(e)}

    try:
        resp = r.json()
    except Exception:
        resp = {'raw_body': r.text[:500]}

    if not (200 <= r.status_code < 300):
        return {
            'status': 'error',
            'http_status': r.status_code,
            'response': resp,
            'msg': resp.get('error') or resp.get('message') or f'HTTP {r.status_code}',
        }

    # Sukces — wyczyść mirror table żeby Hub też wiedział że te produkty już nie istnieją
    deleted_count = int(resp.get('deleted', 0))
    try:
        conn = get_db()
        if skus:
            placeholders = ','.join('?' * len(skus))
            conn.execute(f'DELETE FROM sklepakces_products WHERE sku IN ({placeholders})', list(skus))
        else:
            conn.execute('DELETE FROM sklepakces_products')
        conn.commit()
        logger.info(f'sklepakces mirror table wyczyszczone po bulk delete ({deleted_count} produktów na WC)')
    except Exception as e:
        logger.warning(f'mirror cleanup after bulk delete failed: {e}')

    return {
        'status': 'ok',
        'http_status': r.status_code,
        'deleted': deleted_count,
        'mode': mode,
        'response': resp,
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
            offer = _get_allegro_active_offer(conn, row_dict['id'])
            payload = map_hub_to_plugin(
                row_dict, conn=conn,
                allegro_active_price=(offer['cena'] if offer else None),
                allegro_active_stock=(offer['ilosc'] if offer else None),
                allegro_active_description=(offer['opis'] if offer else None),
            )
            ok, err = validate_payload(payload)
            yield {
                'dry_run': True,
                'hub_id': row_dict['id'],
                'allegro_active_price': offer['cena'] if offer else None,
                'allegro_active_stock': offer['ilosc'] if offer else None,
                'has_allegro_offer': offer is not None,
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
