#!/usr/bin/env python3
"""relink_orphaned_oferty.py — link oferty.produkt_id dla orphaned aukcji.

USER SCENARIO: 184 aktywne aukcje na Allegro w oferty table ale
produkt_id=NULL (legacy sprzed-Paletomatu). Push_sklepakces nie widzi
ich bo szuka p.id przez oferty.produkt_id link. Glückstoff = przykład.

Workflow:
  1. SELECT oferty WHERE produkt_id IS NULL AND status='aktywna'
  2. Per oferta:
     a) Fetch /sale/product-offers/{allegro_id} z Allegro API
     b) Extract EAN z params lub product.gtin
     c) Find produkty WHERE ean=? (jeśli match → UPDATE oferty.produkt_id)
     d) Jeśli brak match → fuzzy match po title prefix (last resort)
     e) Jeśli dalej brak → CREATE stub produkt + link

Po skończeniu: push_sklepakces.py --all --include-listed wciągnie wszystko.

Usage:
    python3 scripts/relink_orphaned_oferty.py --dry-run
    python3 scripts/relink_orphaned_oferty.py
    python3 scripts/relink_orphaned_oferty.py --limit 20

@author: Akces Hub
"""
import argparse
import logging
import os
import sys
import time

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


def _extract_ean(offer: dict) -> str:
    """EAN z Allegro offer JSON."""
    if not isinstance(offer, dict):
        return ''
    # 1. product.gtin (Allegro Product Catalog match)
    product = offer.get('product')
    if isinstance(product, dict):
        gtin = (product.get('gtin') or product.get('ean') or '').strip()
        if gtin:
            return gtin
    # 2. parameters[] z name LIKE %ean%/gtin%
    params = offer.get('parameters', []) or []
    for p in params:
        if not isinstance(p, dict):
            continue
        name = (p.get('name') or '').lower()
        if 'ean' in name or 'gtin' in name or 'kod producenta' in name:
            vals = p.get('values') or []
            if vals and isinstance(vals, list):
                v = str(vals[0]).strip()
                # Czyść z non-digit
                v_clean = ''.join(c for c in v if c.isdigit())
                if 8 <= len(v_clean) <= 14:
                    return v_clean
    return ''


def _extract_asin(offer: dict) -> str:
    """ASIN z external.id gdy paletomat-wystawione."""
    ext = offer.get('external')
    if isinstance(ext, dict):
        ext_id = (ext.get('id') or '').strip()
        if len(ext_id) == 10 and ext_id.upper().startswith('B0'):
            return ext_id.upper()
    return ''


def _find_produkt(conn, ean: str, asin: str, tytul: str) -> int:
    """Match offer do produkt Hub. Returns hub_id lub 0."""
    if ean:
        row = conn.execute('SELECT id FROM produkty WHERE ean = ? LIMIT 1', (ean,)).fetchone()
        if row:
            return int(row['id'])
    if asin:
        row = conn.execute(
            'SELECT id FROM produkty WHERE UPPER(asin) = ? LIMIT 1', (asin,)
        ).fetchone()
        if row:
            return int(row['id'])
    # Fuzzy by title prefix (last resort)
    if tytul and len(tytul) >= 25:
        prefix = tytul[:25]
        row = conn.execute(
            'SELECT id FROM produkty WHERE krotki_tytul LIKE ? OR nazwa LIKE ? LIMIT 1',
            (f'{prefix}%', f'{prefix}%'),
        ).fetchone()
        if row:
            return int(row['id'])
    return 0


def _create_stub_produkt(conn, offer: dict, tytul: str, ean: str, asin: str) -> int:
    """Stwórz minimalny produkt dla orphaned offer bez match'a."""
    try:
        price = float(offer.get('sellingMode', {}).get('price', {}).get('amount', 0))
    except (TypeError, ValueError):
        price = 0.0
    try:
        ilosc = int(offer.get('stock', {}).get('available', 1))
    except (TypeError, ValueError):
        ilosc = 1
    cur = conn.execute('''
        INSERT INTO produkty
            (nazwa, krotki_tytul, ean, asin, ilosc, cena_allegro, status, data_dodania, paleta_id)
        VALUES (?, ?, ?, ?, ?, ?, 'wystawiony', CURRENT_TIMESTAMP, NULL)
    ''', (tytul[:500], tytul[:120], ean, asin, ilosc, price))
    conn.commit()
    return int(cur.lastrowid)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Relink orphaned oferty (produkt_id=NULL) → matching produkt',
    )
    parser.add_argument('--dry-run', action='store_true', help='Pokaż co by zrobiło')
    parser.add_argument('--limit', type=int, default=0, help='Max N orphaned (test)')
    args = parser.parse_args()

    try:
        from modules.allegro_api import is_authenticated, allegro_request
    except ImportError as e:
        print(f'ERROR: import allegro_api failed: {e}')
        return 2
    if not is_authenticated():
        print('ERROR: brak Allegro auth')
        return 2

    conn = get_db()
    sql = """
        SELECT id, allegro_id, tytul, status FROM oferty
        WHERE produkt_id IS NULL AND status = 'aktywna'
          AND allegro_id IS NOT NULL AND allegro_id != ''
        ORDER BY id
    """
    if args.limit > 0:
        sql += f' LIMIT {int(args.limit)}'
    orphaned = conn.execute(sql).fetchall()
    if not orphaned:
        print('Brak orphaned oferty (wszystkie aktywne mają produkt_id).')
        return 0
    print(f'Znaleziono {len(orphaned)} orphaned aktywnych oferty (produkt_id=NULL).')
    print(f'Throttle 0.5s/req (Allegro API) → ETA ~{int(len(orphaned)*0.5)}s\n')

    stats = {
        'fetched': 0,
        'matched_to_existing_produkt': 0,
        'created_stub_produkt': 0,
        'relinked_oferty': 0,
        'errors': 0,
    }
    for i, row in enumerate(orphaned, 1):
        oferty_id = row['id']
        allegro_id = row['allegro_id']
        tytul = (row['tytul'] or '')[:60]

        # Fetch offer details z Allegro API (potrzebne dla EAN)
        try:
            offer, error = allegro_request('GET', f'/sale/product-offers/{allegro_id}')
            stats['fetched'] += 1
        except Exception as e:
            print(f'  ❌ [{i}/{len(orphaned)}] oferty_id={oferty_id} allegro={allegro_id} fetch fail: {e}')
            stats['errors'] += 1
            continue
        if error or not offer:
            print(f'  ❌ [{i}/{len(orphaned)}] oferty_id={oferty_id} allegro={allegro_id} Allegro error: {error}')
            stats['errors'] += 1
            time.sleep(0.5)
            continue

        ean = _extract_ean(offer)
        asin = _extract_asin(offer)
        hub_id = _find_produkt(conn, ean, asin, tytul)

        if hub_id:
            stats['matched_to_existing_produkt'] += 1
            marker = f'🔗 matched hub_id={hub_id}'
        else:
            if args.dry_run:
                hub_id = -1
                marker = '🆕 stworzyłby stub produkt'
            else:
                hub_id = _create_stub_produkt(conn, offer, tytul, ean, asin)
                stats['created_stub_produkt'] += 1
                marker = f'🆕 stub hub_id={hub_id}'

        if not args.dry_run and hub_id > 0:
            try:
                conn.execute('UPDATE oferty SET produkt_id = ? WHERE id = ?', (hub_id, oferty_id))
                conn.commit()
                stats['relinked_oferty'] += 1
            except Exception as e:
                print(f'  ❌ [{i}/{len(orphaned)}] UPDATE failed: {e}')
                stats['errors'] += 1

        print(f'  [{i:>3}/{len(orphaned)}] {marker} ean={ean or "-"} asin={asin or "-"} '
              f'"{tytul[:35]}"')
        time.sleep(0.5)  # Allegro API throttle

    print(f'\n═══ RESULTS ═══')
    print(f'Orphaned fetched z Allegro:  {stats["fetched"]}/{len(orphaned)}')
    print(f'Matched do istniejących:     {stats["matched_to_existing_produkt"]}')
    print(f'Utworzono stub produkty:     {stats["created_stub_produkt"]}')
    print(f'Re-linked oferty rows:       {stats["relinked_oferty"]}')
    print(f'Errors:                      {stats["errors"]}')
    if not args.dry_run and stats['relinked_oferty'] > 0:
        print(f'\n✅ Done. Teraz pchnij na sklep:')
        print('   python3 scripts/push_sklepakces.py --all --include-listed --limit 500')
    return 0


if __name__ == '__main__':
    sys.exit(main())
