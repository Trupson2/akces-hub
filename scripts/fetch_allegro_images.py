#!/usr/bin/env python3
"""fetch_allegro_images.py — pobierz zdjęcia z Allegro dla produktów bez images.

USER RAPORT: produkty na sklepie pokazują "Bez kategorii" placeholder
zamiast zdjęć. Stub produkty z relinker_orphaned_oferty.py mają puste
`produkty.images` (relinker tylko cena/ilość/tytul z Allegro).

Workflow:
  1. SELECT produkty WHERE images='' OR images='[]'
     AND ma linked aktywne oferty (allegro_id znany)
  2. Per produkt:
     a) Fetch /sale/product-offers/{allegro_id} z Allegro API
     b) Extract images list (offer.images[*].url)
     c) UPDATE produkty.images = JSON(urls)
  3. Re-push do sklepu — plugin attach_images() pobierze URL'e

Usage:
    python3 scripts/fetch_allegro_images.py --dry-run --limit 5
    python3 scripts/fetch_allegro_images.py
    python3 scripts/fetch_allegro_images.py --limit 50

@author: Akces Hub
"""
import argparse
import json
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


def _extract_images(offer: dict) -> list:
    """Wyciągnij URL'e zdjęć z Allegro offer JSON.

    Allegro format: offer.images = [{url: "https://a.allegroimg.com/...", ...}]
    """
    images = []
    if not isinstance(offer, dict):
        return images
    raw = offer.get('images', []) or []
    if not isinstance(raw, list):
        return images
    for img in raw[:8]:  # cap 8 (produkty.images max)
        if isinstance(img, dict):
            url = (img.get('url') or '').strip()
        elif isinstance(img, str):
            url = img.strip()
        else:
            continue
        if url and url.startswith('http'):
            images.append(url)
    return images


def main() -> int:
    parser = argparse.ArgumentParser(description='Fetch zdjęcia z Allegro dla produktów bez images')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()

    try:
        from modules.allegro_api import is_authenticated, allegro_request
    except ImportError as e:
        print(f'ERROR: {e}')
        return 2
    if not is_authenticated():
        print('ERROR: brak Allegro auth')
        return 2

    conn = get_db()
    # Produkty bez images, z aktywną ofertą Allegro
    sql = """
        SELECT p.id, p.krotki_tytul,
               (SELECT allegro_id FROM oferty
                WHERE produkt_id=p.id AND status='aktywna'
                  AND allegro_id IS NOT NULL AND allegro_id != ''
                LIMIT 1) AS allegro_id
        FROM produkty p
        WHERE (p.images IS NULL OR p.images = '' OR p.images = '[]')
          AND EXISTS (
              SELECT 1 FROM oferty o
              WHERE o.produkt_id = p.id AND o.status = 'aktywna'
                AND o.allegro_id IS NOT NULL AND o.allegro_id != ''
          )
        ORDER BY p.id
    """
    if args.limit > 0:
        sql += f' LIMIT {int(args.limit)}'
    candidates = conn.execute(sql).fetchall()
    if not candidates:
        print('Brak produktów wymagających pobrania zdjęć (wszystkie z ofertą Allegro mają już images).')
        return 0
    print(f'Znaleziono {len(candidates)} produktów do pobrania zdjęć.')
    print(f'Throttle 0.5s/req (Allegro API) → ETA ~{int(len(candidates)*0.5)}s\n')

    stats = {'fetched': 0, 'with_images': 0, 'no_images': 0, 'errors': 0, 'updated': 0}
    for i, row in enumerate(candidates, 1):
        hub_id = row['id']
        allegro_id = row['allegro_id']
        tytul = (row['krotki_tytul'] or '')[:40]

        try:
            offer, error = allegro_request('GET', f'/sale/product-offers/{allegro_id}')
            stats['fetched'] += 1
        except Exception as e:
            print(f'  ❌ [{i}/{len(candidates)}] hub_id={hub_id} fetch fail: {e}')
            stats['errors'] += 1
            time.sleep(0.5)
            continue
        if error or not offer:
            print(f'  ❌ [{i}/{len(candidates)}] hub_id={hub_id} allegro={allegro_id} error: {error}')
            stats['errors'] += 1
            time.sleep(0.5)
            continue

        images = _extract_images(offer)
        if not images:
            print(f'  ⊘ [{i}/{len(candidates)}] hub_id={hub_id} BRAK zdjęć w Allegro offer "{tytul}"')
            stats['no_images'] += 1
            time.sleep(0.5)
            continue

        if args.dry_run:
            print(f'  [DRY] [{i}/{len(candidates)}] hub_id={hub_id} → {len(images)} zdjęć: '
                  f'{images[0][:60]}...')
        else:
            images_json = json.dumps(images, ensure_ascii=False)
            try:
                conn.execute(
                    'UPDATE produkty SET images = ? WHERE id = ?',
                    (images_json, hub_id),
                )
                conn.commit()
                stats['updated'] += 1
                print(f'  ✅ [{i}/{len(candidates)}] hub_id={hub_id} → {len(images)} zdjęć '
                      f'"{tytul}"')
            except Exception as e:
                print(f'  ❌ [{i}/{len(candidates)}] hub_id={hub_id} UPDATE failed: {e}')
                stats['errors'] += 1
                continue

        stats['with_images'] += 1
        time.sleep(0.5)

    print(f'\n═══ RESULTS ═══')
    print(f'Fetched z Allegro:        {stats["fetched"]}/{len(candidates)}')
    print(f'Z zdjęciami:              {stats["with_images"]}')
    print(f'Brak zdjęć w Allegro:     {stats["no_images"]}')
    print(f'UPDATEd produkty.images:  {stats["updated"]}')
    print(f'Errors:                   {stats["errors"]}')
    if not args.dry_run and stats['updated'] > 0:
        print(f'\n✅ Re-push na sklep z nowymi zdjęciami:')
        print(f'   python3 scripts/push_sklepakces.py --all --include-listed --force --limit 500')
    return 0


if __name__ == '__main__':
    sys.exit(main())
