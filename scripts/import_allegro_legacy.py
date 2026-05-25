#!/usr/bin/env python3
"""import_allegro_legacy.py — importuj stare aukcje Allegro do Hub (sprzed-Paletomatu).

User raport: "chodzi o to ktore byly poprostu juz wystawiione na allegro
przed ery paletomatu". Hub nie wie o tych aukcjach (oferty tabela pusta),
więc push_sklepakces nie ma czego push'ować dla legacy listings.

Workflow:
  1. Fetch ALL aktywne Allegro offers via allegro_api.get_my_offers()
  2. Dla każdej oferty:
     - Sprawdź czy już w oferty table (po allegro_id) → skip
     - Spróbuj match do istniejącego produktu Hub (po EAN z external.id albo SKU)
     - Jeśli match → INSERT oferty z linkiem do produkt_id
     - Jeśli brak match → CREATE stub produkt + INSERT oferty
  3. Po import można `push_sklepakces.py --all --include-listed` → wszystko na sklep

Usage:
    # Dry-run zobacz co by zrobiło (NIE zapisuj):
    python3 scripts/import_allegro_legacy.py --dry-run

    # Faktyczny import:
    python3 scripts/import_allegro_legacy.py

    # Limit (test):
    python3 scripts/import_allegro_legacy.py --limit 10

@author: Akces Hub
"""
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO'),
    format='%(asctime)s %(levelname)s: %(message)s',
)
logger = logging.getLogger(__name__)

from modules.database import get_db  # noqa: E402


def _extract_ean_from_offer(offer: dict) -> str:
    """Wyciągnij EAN z Allegro offer params (jeśli set jako jeden z atrybutów)."""
    # Allegro offer ma 'parameters' lista z {name, valuesIds, values, ...}
    params = offer.get('parameters', []) or []
    if not isinstance(params, list):
        return ''
    for p in params:
        if not isinstance(p, dict):
            continue
        name = (p.get('name') or '').lower()
        if 'ean' in name or 'gtin' in name:
            vals = p.get('values') or []
            if vals and isinstance(vals, list):
                return str(vals[0]).strip()
    # Try product.gtin (if linked to Allegro product catalog)
    product = offer.get('product')
    if isinstance(product, dict):
        gtin = product.get('gtin') or product.get('ean')
        if gtin:
            return str(gtin).strip()
    return ''


def _extract_asin_from_offer(offer: dict) -> str:
    """external.id w Allegro to często ASIN gdy paletomat wystawiał."""
    ext = offer.get('external')
    if isinstance(ext, dict):
        ext_id = ext.get('id') or ''
        # ASIN ma format B0XXXXXXXX (10 chars)
        if ext_id and len(ext_id) == 10 and ext_id.upper().startswith('B0'):
            return ext_id.upper()
    return ''


def _match_produkt(conn, ean: str, asin: str, tytul: str) -> int:
    """Spróbuj znaleźć istniejący produkt Hub. Returns hub_id lub 0."""
    if ean:
        row = conn.execute('SELECT id FROM produkty WHERE ean = ? LIMIT 1', (ean,)).fetchone()
        if row:
            return int(row['id'])
    if asin:
        row = conn.execute('SELECT id FROM produkty WHERE UPPER(asin) = ? LIMIT 1', (asin,)).fetchone()
        if row:
            return int(row['id'])
    # Fuzzy match po tytule (last resort — może false positive)
    if tytul and len(tytul) >= 20:
        # Match top 20 chars (najczęściej najbardziej charakterystyczne)
        prefix = tytul[:20]
        row = conn.execute(
            "SELECT id FROM produkty WHERE krotki_tytul LIKE ? OR nazwa LIKE ? LIMIT 1",
            (f'{prefix}%', f'{prefix}%'),
        ).fetchone()
        if row:
            return int(row['id'])
    return 0


def _create_stub_produkt(conn, offer: dict, ean: str, asin: str) -> int:
    """Stwórz minimalny produkt na podstawie Allegro offer. Returns nowy hub_id."""
    nazwa = (offer.get('name') or '')[:500]
    # Cena z Allegro
    price = 0.0
    try:
        price = float(offer.get('sellingMode', {}).get('price', {}).get('amount', 0))
    except (TypeError, ValueError):
        pass
    # Stock
    try:
        ilosc = int(offer.get('stock', {}).get('available', 1))
    except (TypeError, ValueError):
        ilosc = 1

    cur = conn.execute('''
        INSERT INTO produkty
            (nazwa, krotki_tytul, ean, asin, ilosc, cena_allegro, status, data_dodania, paleta_id)
        VALUES (?, ?, ?, ?, ?, ?, 'wystawiony', CURRENT_TIMESTAMP, NULL)
    ''', (nazwa, nazwa[:120], ean or '', asin or '', ilosc, price))
    conn.commit()
    return int(cur.lastrowid)


def _insert_oferty(conn, offer: dict, produkt_id: int) -> int:
    """INSERT do oferty table. Returns oferty.id."""
    allegro_id = str(offer.get('id', '')).strip()
    if not allegro_id:
        return 0
    tytul = (offer.get('name') or '')[:200]
    try:
        cena = float(offer.get('sellingMode', {}).get('price', {}).get('amount', 0))
    except (TypeError, ValueError):
        cena = 0.0
    try:
        ilosc = int(offer.get('stock', {}).get('available', 1))
    except (TypeError, ValueError):
        ilosc = 1
    pub_status = (offer.get('publication', {}).get('status') or 'ACTIVE').upper()
    status = 'aktywna' if pub_status == 'ACTIVE' else (
        'zakonczona' if pub_status in ('ENDED', 'INACTIVE') else 'draft'
    )
    # Data wystawienia (publication.startedAt lub createdAt)
    started = offer.get('publication', {}).get('startedAt') or offer.get('createdAt') or None

    try:
        cur = conn.execute('''
            INSERT OR IGNORE INTO oferty
                (allegro_id, produkt_id, tytul, opis, cena, ilosc, status,
                 data_wystawienia, data_aktualizacji)
            VALUES (?, ?, ?, '', ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (allegro_id, produkt_id, tytul, cena, ilosc, status, started))
        conn.commit()
        return cur.lastrowid or 0
    except Exception as e:
        logger.warning(f'INSERT oferty failed allegro_id={allegro_id}: {e}')
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Import legacy Allegro auctions (sprzed-Paletomatu) do Hub oferty + produkty',
    )
    parser.add_argument('--dry-run', action='store_true', help='Pokaż co by zrobiło')
    parser.add_argument('--limit', type=int, default=0, help='Max N aukcji (test)')
    args = parser.parse_args()

    try:
        from modules.allegro_api import get_my_offers, is_authenticated
    except ImportError as e:
        print(f'ERROR: import allegro_api failed: {e}')
        return 2

    if not is_authenticated():
        print('ERROR: brak Allegro auth. Zaloguj się: /allegro')
        return 2

    print('Pobieram WSZYSTKIE aktywne Allegro offers (może potrwać 1-2 min)...')
    t0 = time.perf_counter()
    result, error = get_my_offers(limit=100, fetch_all=True)
    if error or not result:
        print(f'ERROR Allegro get_my_offers: {error}')
        return 1
    all_offers = result.get('offers', [])
    print(f'Allegro zwrócił {len(all_offers)} ofert w {time.perf_counter()-t0:.1f}s')

    if args.limit > 0:
        all_offers = all_offers[:args.limit]
        print(f'(limit {args.limit} — process tylko pierwszych {len(all_offers)})')

    conn = get_db()
    stats = {
        'fetched': len(all_offers),
        'already_in_oferty': 0,
        'matched_to_produkt': 0,
        'created_stub_produkt': 0,
        'inserted_oferty': 0,
        'errors': 0,
    }
    print()
    for i, offer in enumerate(all_offers, 1):
        allegro_id = str(offer.get('id', '')).strip()
        tytul = (offer.get('name') or '')[:50]
        pub_status = offer.get('publication', {}).get('status', '?')

        # Skip jeśli już w oferty
        existing = conn.execute(
            'SELECT id, produkt_id FROM oferty WHERE allegro_id = ? LIMIT 1', (allegro_id,)
        ).fetchone()
        if existing:
            stats['already_in_oferty'] += 1
            if i % 20 == 0 or i == len(all_offers):
                print(f'  [{i}/{len(all_offers)}] ⊘ already_in_oferty (allegro_id={allegro_id})')
            continue

        ean = _extract_ean_from_offer(offer)
        asin = _extract_asin_from_offer(offer)
        hub_id = _match_produkt(conn, ean, asin, tytul)

        if hub_id:
            stats['matched_to_produkt'] += 1
            marker = f'🔗 matched hub_id={hub_id}'
        else:
            if args.dry_run:
                hub_id = -1  # placeholder
                marker = '🆕 by stworzył stub produkt'
            else:
                hub_id = _create_stub_produkt(conn, offer, ean, asin)
                stats['created_stub_produkt'] += 1
                marker = f'🆕 stub hub_id={hub_id}'

        if not args.dry_run:
            inserted = _insert_oferty(conn, offer, hub_id)
            if inserted:
                stats['inserted_oferty'] += 1

        print(f'  [{i:>3}/{len(all_offers)}] {marker} allegro={allegro_id} '
              f'ean={ean or "-"} status={pub_status} "{tytul[:35]}"')

    print(f'\n═══ RESULTS ═══')
    print(f'Fetched z Allegro:          {stats["fetched"]}')
    print(f'Already in oferty (skip):   {stats["already_in_oferty"]}')
    print(f'Matched to existing produkt: {stats["matched_to_produkt"]}')
    print(f'Created stub produkt:        {stats["created_stub_produkt"]}')
    print(f'Inserted oferty rows:        {stats["inserted_oferty"]}')
    print(f'Errors:                      {stats["errors"]}')

    if not args.dry_run and stats['inserted_oferty'] > 0:
        print(f'\n✅ Done. Teraz pchnij na sklep z --include-listed:')
        print('   python3 scripts/push_sklepakces.py --all --include-listed --limit 500')
    elif args.dry_run:
        print('\n[DRY-RUN] Nic nie zapisano. Re-run bez --dry-run żeby wykonać.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
