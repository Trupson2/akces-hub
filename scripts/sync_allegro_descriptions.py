#!/usr/bin/env python3
"""
sync_allegro_descriptions.py — pobiera FAKTYCZNE opisy z Allegro aktywnych ofert.

Cel: Hub aktualnie syncuje cena/stock/tytul z Allegro (modules/allegro_api.sync_offers)
ale NIE pobiera opisów. `oferty.opis` jest zawsze puste → push do sklepakces
leciał na fallback (Gemini opis_ai lub auto-gen) zamiast realnego Allegro opisu.

Ten script fetchuje description.sections per offer + concatenate do HTML + zapisuje
do oferty.opis. Push sklepakces (priority #1) wtedy wstawi IDENTYCZNY opis jak
na Allegro (text + bullets + headings).

Allegro description format:
  {
    "description": {
      "sections": [
        {"items": [{"type": "TEXT", "content": "<p>...</p>"}, {"type": "IMAGE", "url": "..."}]}
      ]
    }
  }

Usage:
    # Sync wszystkich aktywnych ofert (oferty.status='aktywna'):
    python3 scripts/sync_allegro_descriptions.py --all

    # Konkretna oferta po allegro_id:
    python3 scripts/sync_allegro_descriptions.py --offer-id 17539123456

    # Test 5 ofert:
    python3 scripts/sync_allegro_descriptions.py --limit 5

    # Force re-sync (nadpisz istniejące oferty.opis):
    python3 scripts/sync_allegro_descriptions.py --all --force

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
from modules.allegro_api import allegro_request  # noqa: E402

THROTTLE_SEC = 0.3  # Allegro API rate limit — 3 RPS safe (limit ~9000/h)


def _sections_to_html(description: dict) -> str:
    """Convert Allegro description.sections JSON → HTML string.

    Allegro TEXT items zawierają już HTML z whitelist tags (p, h1-h5, ul, ol,
    li, strong, em, a, br). Concatenate w kolejności sections > items.
    IMAGE items: wstawiamy jako <img> (push potem stripuje przez
    _strip_images_from_html — user chce opis bez zdjęć).

    Returns HTML string lub '' gdy brak description.
    """
    if not isinstance(description, dict):
        return ''
    sections = description.get('sections') or []
    if not isinstance(sections, list):
        return ''

    parts = []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        items = sec.get('items') or []
        if not isinstance(items, list):
            continue
        section_html = []
        for it in items:
            if not isinstance(it, dict):
                continue
            item_type = (it.get('type') or '').upper()
            if item_type == 'TEXT':
                content = (it.get('content') or '').strip()
                if content:
                    section_html.append(content)
            elif item_type == 'IMAGE':
                url = (it.get('url') or '').strip()
                if url:
                    section_html.append(f'<img src="{url}" alt="">')
            # Inne typy (VIDEO, PRODUCT) — pomijamy
        if section_html:
            parts.append('\n'.join(section_html))

    return '\n\n'.join(parts).strip()


def _extract_gtin_from_allegro_offer(result: dict) -> str:
    """Wyciągnij GTIN/EAN z Allegro offer JSON (różne miejsca per Allegro API version)."""
    # 1. external.id (GTIN-13 jako string)
    ext = result.get('external') or {}
    if ext.get('id'):
        return str(ext['id']).strip()
    # 2. productSet[0].product.gtin / .gtinTag / .ean
    psets = result.get('productSet') or []
    if psets and isinstance(psets, list):
        prod = (psets[0].get('product') or {}) if isinstance(psets[0], dict) else {}
        for key in ('gtin', 'gtinTag', 'ean', 'EAN'):
            if prod.get(key):
                return str(prod[key]).strip()
    # 3. Parameters list (rzadziej)
    for psets_item in psets:
        if not isinstance(psets_item, dict): continue
        params = (psets_item.get('product') or {}).get('parameters') or []
        for p in params:
            if not isinstance(p, dict): continue
            pid = (p.get('id') or '').lower()
            if 'ean' in pid or 'gtin' in pid:
                vals = p.get('values') or []
                if vals: return str(vals[0]).strip()
    return ''


def _link_produkt_id(conn, oferta_db_id: int, allegro_offer: dict, tytul: str) -> int:
    """Match oferty.produkt_id z Hub produkty po EAN (Allegro GTIN) lub fuzzy title.

    Returns produkt_id (lub 0 gdy nie znaleziono).
    """
    # 1. Try po GTIN/EAN
    ean = _extract_gtin_from_allegro_offer(allegro_offer)
    if ean and len(ean) >= 8:
        row = conn.execute('SELECT id FROM produkty WHERE ean = ? LIMIT 1', (ean,)).fetchone()
        if row:
            pid = int(row['id'])
            conn.execute('UPDATE oferty SET produkt_id = ? WHERE id = ?', (pid, oferta_db_id))
            return pid

    # 2. Fallback: fuzzy match po tytule (first 30 chars LIKE)
    if tytul:
        # Sanitize: take first 30 chars, escape SQL wildcards
        prefix = tytul.strip()[:40].replace('%', '').replace('_', '')
        if len(prefix) >= 10:
            row = conn.execute(
                'SELECT id FROM produkty WHERE (krotki_tytul LIKE ? OR nazwa LIKE ?) LIMIT 1',
                (f'%{prefix}%', f'%{prefix}%'),
            ).fetchone()
            if row:
                pid = int(row['id'])
                conn.execute('UPDATE oferty SET produkt_id = ? WHERE id = ?', (pid, oferta_db_id))
                return pid

    return 0


def _sync_one(allegro_id: str, force: bool = False) -> dict:
    """Fetch description z Allegro API + save do oferty.opis + link produkt_id.

    Returns dict {status, allegro_id, msg, opis_len, produkt_id (after link)}.
    """
    conn = get_db()
    row = conn.execute(
        'SELECT id, produkt_id, opis, tytul FROM oferty WHERE allegro_id = ? LIMIT 1',
        (allegro_id,),
    ).fetchone()
    if not row:
        return {'status': 'skip', 'allegro_id': allegro_id, 'msg': 'oferta nie w bazie'}

    # Fetch z Allegro API
    result, error = allegro_request('GET', f'/sale/product-offers/{allegro_id}')
    if error or not result:
        return {'status': 'error', 'allegro_id': allegro_id, 'msg': f'Allegro API: {error or "no data"}'}

    # ZAWSZE try link produkt_id (jeśli pusto) — niezależnie od force/cached opis
    produkt_id = row['produkt_id']
    if not produkt_id:
        produkt_id = _link_produkt_id(conn, row['id'], result, row['tytul'] or '')
        conn.commit()

    # Sprawdz cached opis (po link próbie)
    if not force and row['opis'] and len(row['opis']) > 50:
        return {
            'status': 'skip', 'allegro_id': allegro_id,
            'produkt_id': produkt_id or None,
            'msg': f'opis cached, link {"OK" if produkt_id else "FAIL"}',
        }

    description = result.get('description') or {}
    html = _sections_to_html(description)
    if not html:
        return {'status': 'skip', 'allegro_id': allegro_id, 'msg': 'Allegro offer ma puste description.sections'}

    conn.execute(
        'UPDATE oferty SET opis = ?, data_aktualizacji = datetime("now") WHERE id = ?',
        (html, row['id']),
    )
    conn.commit()
    return {
        'status': 'ok',
        'allegro_id': allegro_id,
        'produkt_id': produkt_id or None,
        'opis_len': len(html),
    }


def cmd_offer(allegro_id: str, force: bool) -> int:
    print(f'Sync opisu z Allegro dla offer_id={allegro_id} (force={force})...')
    r = _sync_one(allegro_id, force=force)
    print(r)
    return 0 if r.get('status') in ('ok', 'skip') else 1


def cmd_all(force: bool, limit: int) -> int:
    conn = get_db()
    sql = """
        SELECT allegro_id FROM oferty
        WHERE status = 'aktywna' AND allegro_id IS NOT NULL AND allegro_id != ''
    """
    if not force:
        sql += ' AND (opis IS NULL OR opis = "" OR length(opis) < 50)'
    sql += ' ORDER BY data_aktualizacji DESC'
    if limit > 0:
        sql += f' LIMIT {int(limit)}'
    rows = conn.execute(sql).fetchall()
    print(f'Sync {len(rows)} aktywnych ofert Allegro (throttle {THROTTLE_SEC}s/req, force={force})...\n')

    ok = skip = err = 0
    for i, row in enumerate(rows):
        if i > 0:
            time.sleep(THROTTLE_SEC)
        aid = row['allegro_id']
        r = _sync_one(aid, force=force)
        st = r.get('status')
        marker = {'ok': '✅', 'skip': '⊘', 'error': '❌'}.get(st, '?')
        msg = r.get('msg', '') or f'opis={r.get("opis_len", 0)}b'
        print(f'  {marker} [{i+1:>4}/{len(rows)}] aid={aid[:14]}.. produkt={r.get("produkt_id", "?")} {msg}')
        if st == 'ok':
            ok += 1
        elif st == 'skip':
            skip += 1
        else:
            err += 1

    print(f'\nResults: ok={ok}  skip={skip}  error={err}  total={len(rows)}')
    return 0 if err == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Sync opisów (description.sections) z Allegro do oferty.opis',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--all', action='store_true', help='Sync wszystkich aktywnych ofert (oferty.status=aktywna)')
    group.add_argument('--offer-id', metavar='ID', help='Sync konkretnej oferty (po allegro_id)')
    group.add_argument('--limit', type=int, metavar='N', help='Sync pierwszych N aktywnych ofert (test)')
    parser.add_argument('--force', action='store_true', help='Nadpisz istniejące oferty.opis')
    args = parser.parse_args()

    if args.offer_id:
        return cmd_offer(args.offer_id, args.force)
    if args.all:
        return cmd_all(args.force, limit=0)
    if args.limit:
        return cmd_all(args.force, limit=args.limit)
    return 1


if __name__ == '__main__':
    sys.exit(main())
