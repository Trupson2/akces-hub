"""
amazon_gpsr_scraper.py — pobiera dane GPSR (UE 2023/988) z Amazon product page.

Co fetchuje:
- Manufacturer (Producent): nazwa, adres
- Responsible Person (EU representative): nazwa, adres, email
- Product safety info (instrukcje bezpiecz., warning labels)

Plugin GPSR gate (class-akces-gpsr.php) accepts gdy obecny:
  manufacturer OR responsible_person (minimum 1 z dwóch).

Lookup chain:
1. Cache hit (gpsr_amazon_cache) → return cached
2. ASIN present → fetch amazon.{region}/dp/{ASIN}
3. EAN present (no ASIN) → search amazon.{region}/s?k={EAN} → resolve to ASIN → fetch
4. Fail (no ASIN, captcha, timeout) → return fallback (Twoja firma jako importer)

Anti-bot strategy:
- Realistic User-Agent (Chrome 121 Windows)
- Accept-Language per region
- 3s throttle between requests
- Captcha detection (HTML pattern check) → bail-out
- Snapshot raw HTML w cache dla audit (debug + manual review jak parser nie znajdzie pól)

Compliance note: scraping technically narusza Amazon ToS. Mitygacja: niska rate
(3s/req max), nie używaj bulk dla 1000+ ASINs naraz, cache permanent → ToS-risk
≈ low dla casual product enrichment use case.

@author: Akces Hub
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, Optional

import requests

from .database import get_db, get_config

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Config — fallback (Twoja firma jako importer EU dla GPSR)
# ──────────────────────────────────────────────────────────────────────────────

FALLBACK_RESPONSIBLE_PERSON = {
    'responsible_person_name':    'AKCES Andrzej Gauza',
    'responsible_person_address': 'ul. Poniatowskiego 13, 74-505 Mieszkowice, woj. zachodniopomorskie, Polska',
    'responsible_person_email':   'kontakt@sklepakces.pl',
}

# Realistic UA — Chrome 121 stable na Windows. Niska rotacja by Amazon nie banowało.
USER_AGENTS = {
    'de': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'pl': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'uk': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'it': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'fr': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
}

ACCEPT_LANG = {
    'de': 'de-DE,de;q=0.9,en;q=0.7',
    'pl': 'pl-PL,pl;q=0.9,en;q=0.7',
    'uk': 'en-GB,en;q=0.9',
    'it': 'it-IT,it;q=0.9,en;q=0.7',
    'fr': 'fr-FR,fr;q=0.9,en;q=0.7',
}

REGION_DOMAIN = {
    'de': 'amazon.de',
    'pl': 'amazon.pl',
    'uk': 'amazon.co.uk',
    'it': 'amazon.it',
    'fr': 'amazon.fr',
    'es': 'amazon.es',
}

THROTTLE_SECONDS = 3.0
HTTP_TIMEOUT = 30

# Captcha / blocked page signatures (HTML patterns)
CAPTCHA_PATTERNS = [
    'Type the characters you see in this image',
    'Geben Sie die Zeichen ein',
    'Wprowadź znaki, które widzisz',
    'api-services-support@amazon.com',
    '/errors/validateCaptcha',
    'Sorry, we just need to make sure you',
    'opfcaptcha.amazon',           # OPF CAPTCHA service (Amazon new anti-bot)
    'csm-captcha-instrumentation',
    'Klicke auf die Schaltfläche unten',  # DE button challenge
    'Click the button below to continue',  # EN button challenge
    'Zur Bestätigung, dass Sie kein Roboter sind',
]

# Stub/blocked page size threshold — Amazon captcha pages są ~5 KB, real product ~300+ KB
MIN_REAL_PAGE_BYTES = 30_000

# ──────────────────────────────────────────────────────────────────────────────
# Data class
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class GpsrData:
    """Wynik fetch GPSR z Amazon (lub fallback)."""
    manufacturer_name: str = ''
    manufacturer_address: str = ''
    responsible_person_name: str = ''
    responsible_person_address: str = ''
    responsible_person_email: str = ''
    product_safety_info: str = ''
    source: str = ''           # 'amazon' | 'fallback' | 'cache'
    source_url: str = ''
    asin: str = ''
    region: str = ''
    fetched_at: str = ''

    def is_compliant(self) -> bool:
        """Zgodnie z plugin GPSR gate: manufacturer OR responsible_person."""
        return bool(self.manufacturer_name or self.responsible_person_name)

    def to_plugin_payload(self) -> Dict[str, str]:
        """Format zgodny z plugin REST schema (class-akces-product-sync.sanitize_gpsr)."""
        return {
            'manufacturer_name':           self.manufacturer_name,
            'manufacturer_address':        self.manufacturer_address,
            'responsible_person_name':     self.responsible_person_name,
            'responsible_person_address':  self.responsible_person_address,
            'responsible_person_email':    self.responsible_person_email,
            'product_safety_info':         self.product_safety_info,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Cache (gpsr_amazon_cache table)
# ──────────────────────────────────────────────────────────────────────────────

def init_cache_schema(conn=None) -> None:
    """Tworzy gpsr_amazon_cache table (idempotent, CREATE IF NOT EXISTS)."""
    if conn is None:
        conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS gpsr_amazon_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asin TEXT NOT NULL,
            region TEXT NOT NULL DEFAULT 'de',
            ean TEXT,
            manufacturer_name TEXT DEFAULT '',
            manufacturer_address TEXT DEFAULT '',
            responsible_person_name TEXT DEFAULT '',
            responsible_person_address TEXT DEFAULT '',
            responsible_person_email TEXT DEFAULT '',
            product_safety_info TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            raw_html_snippet TEXT,
            fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(asin, region)
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_gpsr_amazon_cache_ean ON gpsr_amazon_cache(ean)')
    conn.commit()


def cache_lookup(asin: str, region: str = 'de', conn=None) -> Optional[GpsrData]:
    """Sprawdz cache. Returns GpsrData lub None."""
    if conn is None:
        conn = get_db()
    init_cache_schema(conn)
    cur = conn.execute(
        'SELECT * FROM gpsr_amazon_cache WHERE asin = ? AND region = ?',
        (asin, region),
    )
    row = cur.fetchone()
    if row is None:
        return None
    d = dict(row)
    return GpsrData(
        manufacturer_name=d.get('manufacturer_name') or '',
        manufacturer_address=d.get('manufacturer_address') or '',
        responsible_person_name=d.get('responsible_person_name') or '',
        responsible_person_address=d.get('responsible_person_address') or '',
        responsible_person_email=d.get('responsible_person_email') or '',
        product_safety_info=d.get('product_safety_info') or '',
        source='cache',
        source_url=d.get('source_url') or '',
        asin=asin,
        region=region,
        fetched_at=d.get('fetched_at') or '',
    )


def cache_save(gpsr: GpsrData, raw_snippet: str = '', conn=None) -> None:
    """Zapisz GpsrData w cache. Upsert by (asin, region)."""
    if conn is None:
        conn = get_db()
    init_cache_schema(conn)
    conn.execute(
        '''
        INSERT INTO gpsr_amazon_cache
            (asin, region, manufacturer_name, manufacturer_address,
             responsible_person_name, responsible_person_address, responsible_person_email,
             product_safety_info, source_url, raw_html_snippet, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(asin, region) DO UPDATE SET
            manufacturer_name = excluded.manufacturer_name,
            manufacturer_address = excluded.manufacturer_address,
            responsible_person_name = excluded.responsible_person_name,
            responsible_person_address = excluded.responsible_person_address,
            responsible_person_email = excluded.responsible_person_email,
            product_safety_info = excluded.product_safety_info,
            source_url = excluded.source_url,
            raw_html_snippet = excluded.raw_html_snippet,
            fetched_at = excluded.fetched_at
        ''',
        (
            gpsr.asin, gpsr.region,
            gpsr.manufacturer_name, gpsr.manufacturer_address,
            gpsr.responsible_person_name, gpsr.responsible_person_address, gpsr.responsible_person_email,
            gpsr.product_safety_info, gpsr.source_url, raw_snippet[:4000],
        ),
    )
    conn.commit()


# ──────────────────────────────────────────────────────────────────────────────
# HTTP fetch
# ──────────────────────────────────────────────────────────────────────────────

def is_captcha_page(html: str) -> bool:
    """Wykryj Amazon captcha / blocked page.

    Sygnały: znane pattern stringi OR mała wielkość strony (Amazon stub ~5 KB
    vs realna karta produktu 300+ KB).
    """
    if any(p in html for p in CAPTCHA_PATTERNS):
        return True
    # Size-based check: Amazon product page < 30 KB to prawie na pewno stub/blocked
    if len(html) < MIN_REAL_PAGE_BYTES:
        return True
    return False


def fetch_amazon_html(asin: str, region: str = 'de', session: Optional[requests.Session] = None) -> Optional[str]:
    """Fetch Amazon product page HTML. Returns html string lub None (captcha/error)."""
    domain = REGION_DOMAIN.get(region, 'amazon.de')
    url = f'https://www.{domain}/dp/{asin}'
    headers = {
        'User-Agent': USER_AGENTS.get(region, USER_AGENTS['de']),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': ACCEPT_LANG.get(region, 'de-DE,de;q=0.9,en;q=0.7'),
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
    }
    s = session or requests.Session()
    try:
        r = s.get(url, headers=headers, timeout=HTTP_TIMEOUT, allow_redirects=True)
    except requests.RequestException as e:
        logger.warning(f'Amazon fetch failed asin={asin} region={region}: {e}')
        return None

    if r.status_code == 503:
        logger.warning(f'Amazon 503 (rate limit / temporarily unavailable) asin={asin}')
        return None
    if r.status_code != 200:
        logger.warning(f'Amazon HTTP {r.status_code} asin={asin}: {r.text[:200]}')
        return None

    if is_captcha_page(r.text):
        logger.warning(f'Amazon captcha wykryta asin={asin} (size={len(r.text)}b) — pomijam (cooldown wymagany)')
        return None

    logger.debug(f'Amazon HTML OK asin={asin} size={len(r.text)}b region={region}')
    return r.text


# ──────────────────────────────────────────────────────────────────────────────
# Parser — wyciąga GPSR fields z HTML
# ──────────────────────────────────────────────────────────────────────────────

# Patterns do labeli GPSR pol per region. Robust regex (case-insensitive, multi-language).
LABEL_RESPONSIBLE_PERSON = re.compile(
    r'(EU\s+representative|Responsible\s+Person|EU-Verantwortlicher|Verantwortliche[rs]?\s+'
    r'(?:Inverkehrbringer|Bevollmächtigter)?|Osoba\s+odpowiedzialna|Persona\s+responsable|'
    r'Personne\s+responsable|Persona\s+responsabile)',
    re.IGNORECASE,
)
LABEL_MANUFACTURER = re.compile(
    r'(Manufacturer|Hersteller|Producent|Productor|Fabricant|Produttore)',
    re.IGNORECASE,
)
LABEL_SAFETY = re.compile(
    r'(Safety|Sicherheit|Bezpieczeństwo|Sécurité|Sicurezza|Seguridad)',
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')


def _extract_block_text(html: str, anchor_ids: list) -> str:
    """Wyciągnij text z bloków o określonych id (sequence prób). Wraca pusty str gdy brak."""
    for aid in anchor_ids:
        # Match <div id="..." ...>...</div> (greedy do </div> next match — proste, działa
        # dla 90% przypadków; lepsze BS4 ale chcemy zero deps)
        m = re.search(
            r'<(?:div|section)[^>]*id\s*=\s*["\']' + re.escape(aid) + r'["\'][^>]*>(.*?)</(?:div|section)>',
            html, re.IGNORECASE | re.DOTALL,
        )
        if m:
            return _strip_html(m.group(1))
    return ''


def _extract_by_data_feature(html: str, feature_names: list) -> str:
    """Wyciągnij text z bloków o data-feature-name='X' attribute (Amazon 2024 layout)."""
    for fname in feature_names:
        m = re.search(
            r'<(?:div|section)[^>]*data-feature-name\s*=\s*["\']' + re.escape(fname) + r'["\'][^>]*>(.*?)</(?:div|section)>',
            html, re.IGNORECASE | re.DOTALL,
        )
        if m:
            return _strip_html(m.group(1))
    return ''


# Anchor IDs do Manufacturer info card (nowy Amazon GPSR layout 2024)
MANUFACTURER_ANCHORS = [
    'manufacturerInformation_feature_div',
    'manufacturer-information',
    'manufacturerInformation',
    'productManufacturer_feature_div',
    'productManufacturer',
]
MANUFACTURER_FEATURES = [
    'manufacturerInformation',
    'productManufacturer',
    'manufacturer',
]

# Anchor IDs do Responsible Person card
RESPONSIBLE_PERSON_ANCHORS = [
    'responsiblePerson_feature_div',
    'responsible-person',
    'responsiblePerson',
    'gpsr-eu-responsible-party_feature_div',
    'euResponsibleParty_feature_div',
]
RESPONSIBLE_PERSON_FEATURES = [
    'responsiblePerson',
    'euResponsibleParty',
    'gpsrResponsiblePerson',
]

# Anchor IDs do Product Safety info
SAFETY_ANCHORS = [
    'productSafetyImages_feature_div',
    'product-safety-images',
    'productSafety_feature_div',
    'safety-information',
    'product-safety-information',
]

# Stare anchor IDs (one-block layout — Amazon do 2023) — fallback
LEGACY_COMBINED_ANCHORS = [
    'product-safety-and-compliance_feature_div',
    'productSafetyInformation_feature_div',
    'important-information',
    'importantInformation_feature_div',
    'manufacturer_feature_div',
    'manufacturerInformation_feature_div',
    'images-documents-and-contacts_feature_div',
]


def _strip_html(s: str) -> str:
    """Strip tags, normalize whitespace BUT zachowaj newlines (z <br>/</p>/</li>) —
    parser address-lines polega na nich."""
    s = re.sub(r'<script[^>]*>.*?</script>', ' ', s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r'<style[^>]*>.*?</style>', ' ', s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r'<br\s*/?>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'</p>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'</h[1-6]>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'</li>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'<[^>]+>', ' ', s)
    # Normalize TYLKO poziome whitespace (spaces, tabs), zachowaj \n
    s = re.sub(r'[ \t]+', ' ', s)
    s = re.sub(r' *\n *', '\n', s)        # strip spaces around newlines
    s = re.sub(r'\n{3,}', '\n\n', s)       # max 2 consecutive newlines
    return s.strip()


def _find_section_by_header_text(html: str, header_patterns: list) -> str:
    """Content-based search: znajdź header (h1-h6/strong/b/span/div z bold class)
    który matchuje którykolwiek pattern, potem capture sąsiedni content do następnego
    header (max 3000 chars).

    Bardziej resilient niż _extract_block_text bo nie polega na konkretnych ID anchors
    — Amazon zmienia layout co kilka miesięcy. Header text jest stabilny.
    """
    for pattern in header_patterns:
        # Match w różnych tag types: h1-h6, span, b, strong, label, dt
        m = re.search(
            r'<(h[1-6]|strong|b|label|dt|span|div)\b[^>]*>\s*(?:<[^>]+>\s*)*'
            + r'(' + pattern + r')'
            + r'\s*(?:<[^>]+>\s*)*</\1>',
            html, re.IGNORECASE | re.DOTALL,
        )
        if not m:
            continue
        # Capture od końca headera do następnego h1-h6 lub max 3000 chars
        section_start = m.end()
        cap = min(section_start + 3000, len(html))
        section_html = html[section_start:cap]
        next_header = re.search(
            r'<h[1-6]\b',
            section_html,
            re.IGNORECASE,
        )
        if next_header and next_header.start() > 30:  # mały minimum żeby nie złapać header samego siebie
            section_html = section_html[:next_header.start()]
        stripped = _strip_html(section_html)
        # Wymóg minimum: musi mieć przynajmniej 10 chars i nie być sam labelem
        if len(stripped) >= 10:
            return stripped
    return ''


def _strip_header_lines(text: str, header_patterns: list) -> str:
    """Usuń z początku text linie pasujące do header patterns (Amazon intro/label sentences).

    Iteruje od góry: dopóki pierwsza linia pasuje do któregokolwiek z patterns
    (case-insensitive), usuwa ją. Stop gdy pierwsza linia NIE pasuje — to już dane.
    """
    lines = text.split('\n')
    while lines:
        first = lines[0].strip()
        if not first:
            lines.pop(0)
            continue
        if any(re.match(p, first, re.IGNORECASE) for p in header_patterns):
            lines.pop(0)
        else:
            break
    return '\n'.join(lines).strip()


def _parse_address_lines(text: str) -> tuple[str, str, str]:
    """
    Heurystyka: text z bloku → (name, address, email).
    Name = pierwsza linia / segment przed adresem.
    Address = środek (linie 2-N bez email).
    Email = pierwszy match @.
    """
    # Primary split: newlines (preferred — HTML <br> separators).
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
    # Fallback: gdy single-line ALE z comma → split po comma/semicolon
    # (case Amazon DE inline "EU-Verantwortlicher: Foo GmbH, Berliner Str., support@x.de").
    if len(lines) <= 1 and lines and ',' in lines[0]:
        lines = [ln.strip() for ln in re.split(r'[,;]', text) if ln.strip()]
    if not lines:
        return '', '', ''
    name = lines[0][:200]
    email_m = EMAIL_RE.search(text)
    email = email_m.group(0) if email_m else ''
    addr_lines = [ln for ln in lines[1:] if EMAIL_RE.search(ln) is None]
    address = ', '.join(addr_lines)[:500]
    return name, address, email


def parse_gpsr_from_html(html: str) -> Dict[str, str]:
    """
    Parsuj GPSR fields z Amazon HTML.

    Strategy A (NEW Amazon layout 2024 — osobne sekcje per tab):
      - "Manufacturer information" tab → <div id="manufacturerInformation_feature_div">
      - "Responsible person" tab → <div id="responsiblePerson_feature_div">
      - "Product safety images" tab → <div id="productSafetyImages_feature_div">
      Pierwsza linia bloku = name (bold/strong), reszta = address + email.

    Strategy B (LEGACY layout — one block z labelami):
      - <div id="product-safety-and-compliance_feature_div">
      - W tekście szukamy labeli "Manufacturer/Hersteller/EU representative/..."
      - Wycinamy segment do następnego labela.

    Strategy B fires gdy A nie znalazł nic (backward-compat z 2023 layoutem).

    Returns dict (empty values gdy brak danych).
    """
    out = {
        'manufacturer_name': '',
        'manufacturer_address': '',
        'responsible_person_name': '',
        'responsible_person_address': '',
        'responsible_person_email': '',
        'product_safety_info': '',
    }

    # =====================================================================
    # STRATEGY 0 — CONTENT-BASED (najszerszy net, działa nawet gdy
    # Amazon zmieni anchor IDs / data-feature-name attrs). Szukamy headera
    # po TEXT ("Responsible person", "Manufacturer information") a nie po ID.
    # =====================================================================
    mf_content = _find_section_by_header_text(html, [
        r'Manufacturer\s+information',
        r'Manufacturer\b',
        r'Hersteller\b',
        r'Producent\b',
        r'Fabricant\b',
        r'Productor\b',
    ])
    if mf_content:
        cleaned = _strip_header_lines(mf_content, [
            r'^Manufacturer\s+information\s*$',
            r'^Manufacturer\s*$',
            r'^Hersteller\s*$',
            r'^Manufacturing\s+information\b.*',
            r'^Information\s+(?:about|on)\b.*',
        ])
        name, addr, _email = _parse_address_lines(cleaned)
        if name:
            out['manufacturer_name'] = name
            out['manufacturer_address'] = addr

    rp_content = _find_section_by_header_text(html, [
        r'Responsible\s+person',
        r'EU\s+representative',
        r'EU[-\s]?Verantwortlicher',
        r'Verantwortlicher\s+(?:Inverkehrbringer|Bevollm[äa]chtigter)',
        r'Osoba\s+odpowiedzialna',
        r'Personne\s+responsable',
        r'Persona\s+responsable',
        r'Persona\s+responsabile',
    ])
    if rp_content:
        cleaned = _strip_header_lines(rp_content, [
            r'^Responsible\s+person\s*$',
            r'^EU\s+representative\s*$',
            r'^Osoba\s+odpowiedzialna\b.*',
            r'^EU\s+based\s+economic\s+operator\b.*',
            r'^Economic\s+operator\b.*',
        ])
        name, addr, email = _parse_address_lines(cleaned)
        if name:
            out['responsible_person_name'] = name
            out['responsible_person_address'] = addr
            out['responsible_person_email'] = email

    # Strategy 0 znalazła RP/MF? Pobierz też safety info i wracaj.
    if out['manufacturer_name'] or out['responsible_person_name']:
        # Safety info — z legacy combined block (zachowuje pełen text dla audit/debug)
        safety_text = _extract_block_text(html, LEGACY_COMBINED_ANCHORS)
        if safety_text:
            out['product_safety_info'] = safety_text[:3000]
        else:
            # Jeśli brak legacy block, doczep mf+rp text jako fallback
            chunks = [t for t in (mf_content, rp_content) if t]
            if chunks:
                out['product_safety_info'] = ' '.join(chunks)[:3000]
        return out

    # =====================================================================
    # STRATEGY A — NEW layout (2024 Amazon, osobne sekcje per typ)
    # =====================================================================
    # Header / intro patterns które należy SKIP (Amazon dodaje sentence "Manufacturing
    # information includes the address..." nad faktycznymi danymi)
    MF_HEADER_LINES = [
        r'^Manufacturer\s+information\s*$',
        r'^Manufacturer\s*$',
        r'^Hersteller\s*$',
        r'^Producent\s*$',
        r'^Manufacturing\s+information\b.*',  # "Manufacturing information includes..."
        r'^Information\s+(?:about|on)\s+(?:the\s+)?manufacturer\b.*',
    ]
    RP_HEADER_LINES = [
        r'^Responsible\s+person\s*$',
        r'^EU\s+representative\s*$',
        r'^EU-?Verantwortlicher\s*$',
        r'^Osoba\s+odpowiedzialna\s*$',
        r'^EU\s+based\s+economic\s+operator\b.*',  # intro sentence
        r'^Economic\s+operator\b.*',
        r'^Osoba\s+odpowiedzialna\s+za\s+zgodno[śs][ćc]\b.*',
    ]

    mf_block = _extract_block_text(html, MANUFACTURER_ANCHORS)
    if not mf_block:
        mf_block = _extract_by_data_feature(html, MANUFACTURER_FEATURES)
    if mf_block:
        cleaned = _strip_header_lines(mf_block, MF_HEADER_LINES)
        name, addr, _email = _parse_address_lines(cleaned)
        out['manufacturer_name'] = name
        out['manufacturer_address'] = addr

    rp_block = _extract_block_text(html, RESPONSIBLE_PERSON_ANCHORS)
    if not rp_block:
        rp_block = _extract_by_data_feature(html, RESPONSIBLE_PERSON_FEATURES)
    if rp_block:
        cleaned = _strip_header_lines(rp_block, RP_HEADER_LINES)
        # Multi-responsible (np. Amazon Retourenkauf + LIAO ZHENWEI) — bierzemy całość;
        # _parse_address_lines weźmie name=first-line, address=joined-rest, email=first match.
        # User dostaje wszystko w joined address. Splitting na osobne person wymagałby
        # bardziej skomplikowanej heurystyki (np. detection ALL_CAPS new name) — skip na teraz.
        name, addr, email = _parse_address_lines(cleaned)
        out['responsible_person_name'] = name
        out['responsible_person_address'] = addr
        out['responsible_person_email'] = email

    # Safety info (osobno, niekrytyczne)
    safety_block = _extract_block_text(html, SAFETY_ANCHORS)
    if safety_block:
        out['product_safety_info'] = safety_block[:3000]

    # Strategy A znalazł cokolwiek? Wracaj.
    if out['manufacturer_name'] or out['responsible_person_name']:
        return out

    # =====================================================================
    # STRATEGY B — LEGACY layout (2023, one block z labelami inline)
    # =====================================================================
    safety_text = _extract_block_text(html, LEGACY_COMBINED_ANCHORS)
    if not safety_text:
        return out

    out['product_safety_info'] = safety_text[:3000]

    # Wyciągnij Responsible Person segment
    m_rp = LABEL_RESPONSIBLE_PERSON.search(safety_text)
    if m_rp:
        start = m_rp.end()
        rest = safety_text[start:start + 800]
        for stop_re in (LABEL_MANUFACTURER, LABEL_SAFETY):
            mstop = stop_re.search(rest)
            if mstop:
                rest = rest[:mstop.start()]
                break
        rest = re.sub(r'^[:\s\-—–]+', '', rest).strip()
        name, addr, email = _parse_address_lines(rest)
        out['responsible_person_name'] = name
        out['responsible_person_address'] = addr
        out['responsible_person_email'] = email

    # Wyciągnij Manufacturer segment
    m_mf = LABEL_MANUFACTURER.search(safety_text)
    if m_mf:
        start = m_mf.end()
        rest = safety_text[start:start + 800]
        for stop_re in (LABEL_RESPONSIBLE_PERSON, LABEL_SAFETY):
            mstop = stop_re.search(rest)
            if mstop:
                rest = rest[:mstop.start()]
                break
        rest = re.sub(r'^[:\s\-—–]+', '', rest).strip()
        name, addr, _email = _parse_address_lines(rest)
        out['manufacturer_name'] = name
        out['manufacturer_address'] = addr

    return out


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def _try_playwright_fetch(asin: str, region: str) -> Optional[str]:
    """Lazy-load playwright module + fetch. Returns HTML lub None gdy unavailable/fail."""
    try:
        from .amazon_gpsr_playwright import fetch_amazon_html_playwright, is_available
    except ImportError:
        logger.debug('Playwright module not importable')
        return None
    if not is_available():
        logger.warning('Playwright nie zainstalowany — pomijam fallback (pip install playwright)')
        return None
    try:
        return fetch_amazon_html_playwright(asin=asin, region=region)
    except Exception as e:
        logger.warning(f'Playwright fetch exception asin={asin}: {e}')
        return None


def fetch_gpsr(
    asin: str,
    region: str = 'de',
    ean: str = '',
    use_cache: bool = True,
    use_fallback: bool = True,
    session: Optional[requests.Session] = None,
    conn=None,
    use_playwright: bool = False,
    playwright_fallback: bool = False,
) -> GpsrData:
    """
    Główna funkcja: cache → Amazon fetch → parse → cache save → fallback gdy fail.

    Args:
        asin:        Amazon Standard ID (wymagany)
        region:      'de'/'pl'/'uk'/'it'/'fr' (default 'de')
        ean:         opcjonalnie zapisany w cache dla revercie lookup
        use_cache:   sprawdź cache przed fetch (default True)
        use_fallback: gdy fetch fail lub brak danych → użyj Twojej firmy jako importer (default True)
        session:     opcjonalna shared requests.Session (dla batchu)
        conn:        opcjonalny SQLite connection
        use_playwright: gdy True, używa headless Chromium do scrap'owania lazy-loaded
                        GPSR tabs (manufacturer + responsible person). Wolniejsze
                        (~5-10s/req) ale wyciąga real producenta gdy HTTP scraper widzi
                        tylko placeholder. Wymaga `pip install playwright + playwright install chromium`.
        playwright_fallback: gdy use_playwright=False ale HTTP scraper nic nie znalazł
                             AND playwright dostępny → automatycznie spróbuj playwright
                             jako fallback (przed AKCES). Default False (opt-in).
                             Aktywuj via config: set_config('amazon_gpsr_playwright_fallback', '1')
                             lub explicit parameter playwright_fallback=True.

    Returns:
        GpsrData (source='amazon'/'amazon_playwright'/'cache'/'fallback')
    """
    if not asin:
        if use_fallback:
            return GpsrData(asin='', region=region, source='fallback', **FALLBACK_RESPONSIBLE_PERSON)
        return GpsrData(asin='', region=region, source='')

    asin = asin.strip().upper()
    region = region.lower()

    # 1. Cache
    if use_cache:
        cached = cache_lookup(asin, region, conn=conn)
        if cached is not None:
            logger.info(f'GPSR cache hit asin={asin} region={region}')
            return cached

    domain = REGION_DOMAIN.get(region, 'amazon.de')
    source_url = f'https://www.{domain}/dp/{asin}'

    # 2a. Primary fetch — Playwright gdy explicit requested, inaczej HTTP requests
    parsed = {}
    used_source = 'amazon'
    if use_playwright:
        html = _try_playwright_fetch(asin, region)
        if html:
            parsed = parse_gpsr_from_html(html)
            used_source = 'amazon_playwright'
    else:
        html = fetch_amazon_html(asin, region, session=session)
        if html:
            parsed = parse_gpsr_from_html(html)

        # Diagnostic — pokazuje co parser znalazł (lub brak)
        mf = parsed.get('manufacturer_name', '')
        rp = parsed.get('responsible_person_name', '')
        sf = parsed.get('product_safety_info', '')
        if mf or rp:
            logger.info(f'Amazon parsed asin={asin}: mf="{mf[:25]}" rp="{rp[:25]}"')
        elif html:
            # HTML pobrany ALE parser nie znalazł GPSR sections (lazy-loaded JS).
            logger.warning(
                f'Amazon HTML OK asin={asin} (size={len(html)}b) ale parser BRAK GPSR sections '
                f'(safety="{sf[:50]}"). Spróbuję Playwright fallback={playwright_fallback}.'
            )

    has_data = any(parsed.get(k) for k in ('manufacturer_name', 'responsible_person_name'))

    # 2b. Playwright fallback — gdy HTTP scraper nic nie znalazł i playwright_fallback=True
    if not has_data and playwright_fallback and not use_playwright:
        pw_html = _try_playwright_fetch(asin, region)
        if pw_html:
            parsed = parse_gpsr_from_html(pw_html)
            has_data = any(parsed.get(k) for k in ('manufacturer_name', 'responsible_person_name'))
            if has_data:
                used_source = 'amazon_playwright'
                logger.info(f'Playwright fallback ZNALAZL GPSR asin={asin}')

    if has_data:
        gpsr = GpsrData(
            asin=asin, region=region,
            source=used_source,
            source_url=source_url,
            fetched_at=datetime.utcnow().isoformat() + 'Z',
            **parsed,
        )
        cache_save(gpsr, raw_snippet=parsed.get('product_safety_info', ''), conn=conn)
        logger.info(f'GPSR z {used_source}: asin={asin} mf="{gpsr.manufacturer_name[:30]}" rp="{gpsr.responsible_person_name[:30]}"')
        return gpsr

    # 3. Fallback — NIE cachuj. Cache TYLKO realne Amazon data (source='amazon');
    # fallback dla każdego nieudanego fetch'a osobny → następny push znowu spróbuje Amazon.
    if use_fallback:
        gpsr = GpsrData(
            asin=asin, region=region,
            source='fallback',
            source_url=source_url,
            fetched_at=datetime.utcnow().isoformat() + 'Z',
            **FALLBACK_RESPONSIBLE_PERSON,
        )
        logger.info(f'GPSR fallback (AKCES importer, NIE cached — następny push retry Amazon) asin={asin}')
        return gpsr

    return GpsrData(asin=asin, region=region, source='')


def fetch_gpsr_throttled(
    asin: str,
    region: str = 'de',
    ean: str = '',
    last_fetch_ts: list = None,
    **kwargs,
) -> GpsrData:
    """Wrapper z throttle (3s/req) gdy fetchujesz w batchu. last_fetch_ts to lista [float]."""
    if last_fetch_ts:
        elapsed = time.time() - last_fetch_ts[0]
        if elapsed < THROTTLE_SECONDS:
            time.sleep(THROTTLE_SECONDS - elapsed)
    result = fetch_gpsr(asin=asin, region=region, ean=ean, **kwargs)
    if last_fetch_ts is not None:
        last_fetch_ts[0] = time.time()
    return result
