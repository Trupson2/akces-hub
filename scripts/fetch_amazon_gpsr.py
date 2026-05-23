#!/usr/bin/env python3
"""
fetch_amazon_gpsr.py — CLI do scrapowania GPSR z Amazon dla produktów Hub.

Usage:
    # Fetch 1 ASIN (--region default 'de')
    python3 scripts/fetch_amazon_gpsr.py --asin B07ABCD123 [--region de]

    # Fetch dla 1 Hub product (po ID — bierze asin z `produkty` table)
    python3 scripts/fetch_amazon_gpsr.py --hub-id 13

    # Bulk: dla każdego Hub produktu z asin (jeszcze nie w cache) — z throttle 3s/req
    python3 scripts/fetch_amazon_gpsr.py --all-from-hub [--region de] [--limit 20]

    # Pokaż cache (co już mamy zscrapowane)
    python3 scripts/fetch_amazon_gpsr.py --show-cache [--limit 20]

    # Wymuś re-fetch (ignor cache)
    python3 scripts/fetch_amazon_gpsr.py --asin B07ABCD123 --force

@author: Akces Hub
"""
import argparse
import json
import logging
import os
import sys
import time

# Project root na sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO'),
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)

from modules.amazon_gpsr_scraper import (  # noqa: E402
    fetch_gpsr,
    fetch_gpsr_throttled,
    cache_lookup,
    init_cache_schema,
    GpsrData,
    REGION_DOMAIN,
    THROTTLE_SECONDS,
)
from modules.database import get_db  # noqa: E402


def _fmt_gpsr(g: GpsrData) -> str:
    return json.dumps(
        {
            'source': g.source,
            'asin': g.asin,
            'region': g.region,
            'manufacturer_name': g.manufacturer_name,
            'manufacturer_address': g.manufacturer_address,
            'responsible_person_name': g.responsible_person_name,
            'responsible_person_address': g.responsible_person_address,
            'responsible_person_email': g.responsible_person_email,
            'product_safety_info': (g.product_safety_info[:300] + '...') if len(g.product_safety_info) > 300 else g.product_safety_info,
            'is_compliant': g.is_compliant(),
            'source_url': g.source_url,
            'fetched_at': g.fetched_at,
        },
        indent=2,
        ensure_ascii=False,
    )


def cmd_asin(asin: str, region: str, force: bool, use_playwright: bool = False) -> int:
    g = fetch_gpsr(asin=asin, region=region, use_cache=not force, use_playwright=use_playwright)
    print(_fmt_gpsr(g))
    return 0 if g.is_compliant() else 1


def cmd_hub_id(hub_id: int, region: str, force: bool, use_playwright: bool = False) -> int:
    conn = get_db()
    row = conn.execute('SELECT id, asin, ean, nazwa FROM produkty WHERE id = ?', (hub_id,)).fetchone()
    if row is None:
        print(f'Hub produkt id={hub_id} nie istnieje')
        return 2
    asin = (dict(row).get('asin') or '').strip().upper()
    if not asin:
        print(f'Hub produkt id={hub_id} ("{dict(row)["nazwa"][:50]}") nie ma asin — fallback do AKCES jako importer')
        g = fetch_gpsr(asin='', region=region, use_fallback=True)
    else:
        print(f'Hub produkt id={hub_id} asin={asin} ("{dict(row)["nazwa"][:50]}")...')
        g = fetch_gpsr(asin=asin, region=region, ean=dict(row).get('ean') or '', use_cache=not force, use_playwright=use_playwright)
    print(_fmt_gpsr(g))
    return 0 if g.is_compliant() else 1


def cmd_all(region: str, limit: int) -> int:
    conn = get_db()
    init_cache_schema(conn)
    # Wybierz produkty z asin, których ASIN nie ma jeszcze w cache (dla danego regionu)
    sql = """
        SELECT p.id, p.asin, p.ean, p.nazwa
        FROM produkty p
        WHERE p.asin IS NOT NULL AND p.asin != ''
          AND p.status = 'magazyn'
          AND NOT EXISTS (
              SELECT 1 FROM gpsr_amazon_cache c WHERE c.asin = UPPER(p.asin) AND c.region = ?
          )
        ORDER BY p.id
    """
    params = [region]
    if limit and limit > 0:
        sql += ' LIMIT ?'
        params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    print(f'Znaleziono {len(rows)} produkt(ów) do scrape (region={region}, throttle={THROTTLE_SECONDS}s/req)\n')

    import requests
    session = requests.Session()
    last_ts = [0.0]
    ok = err = fallback = 0
    for r in rows:
        d = dict(r)
        asin = (d['asin'] or '').strip().upper()
        if not asin:
            continue
        try:
            g = fetch_gpsr_throttled(
                asin=asin, region=region, ean=d.get('ean') or '',
                last_fetch_ts=last_ts, session=session,
            )
        except Exception as e:
            print(f'  ❌ hub_id={d["id"]} asin={asin} -- {e}')
            err += 1
            continue
        marker = {'amazon': '✅', 'cache': '⊘', 'fallback': '⚠'}.get(g.source, '?')
        nazwa = (d.get('nazwa') or '')[:60]
        print(f'  {marker} hub_id={d["id"]:>5} asin={asin} src={g.source:<8} mf="{g.manufacturer_name[:25]}" rp="{g.responsible_person_name[:25]}" {nazwa}')
        if g.source == 'amazon':
            ok += 1
        elif g.source == 'fallback':
            fallback += 1

    print(f'\nResults: amazon={ok}  fallback={fallback}  errors={err}  total={len(rows)}')
    return 0


def cmd_purge_cache(only_fallback: bool) -> int:
    """Wyczyść cache.

    only_fallback=True → kasuje tylko entries z fallback AKCES (gdzie poprzednio
    Amazon był blocked → cache trzymał 'dziadka'). Cache realnych Amazon zostaje.
    """
    conn = get_db()
    init_cache_schema(conn)
    if only_fallback:
        cur = conn.execute(
            "DELETE FROM gpsr_amazon_cache WHERE responsible_person_name LIKE 'AKCES%' AND manufacturer_name = ''"
        )
    else:
        cur = conn.execute('DELETE FROM gpsr_amazon_cache')
    conn.commit()
    deleted = cur.rowcount
    label = 'fallback (AKCES) entries' if only_fallback else 'ALL cache entries'
    print(f'Usunięto {deleted} {label} z gpsr_amazon_cache.')
    return 0


def cmd_show_cache(limit: int) -> int:
    conn = get_db()
    init_cache_schema(conn)
    rows = conn.execute(
        'SELECT asin, region, manufacturer_name, responsible_person_name, fetched_at FROM gpsr_amazon_cache ORDER BY fetched_at DESC LIMIT ?',
        (limit,),
    ).fetchall()
    if not rows:
        print('Cache pusty.')
        return 0
    print(f'{"ASIN":<14} {"REG":<4} {"MANUFACTURER":<25} {"RESP_PERSON":<25} FETCHED_AT')
    print('-' * 100)
    for r in rows:
        d = dict(r)
        print(f'{d["asin"]:<14} {d["region"]:<4} {(d["manufacturer_name"] or "")[:25]:<25} {(d["responsible_person_name"] or "")[:25]:<25} {d["fetched_at"]}')
    print(f'\ntotal: {len(rows)}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Scrape GPSR data (UE 2023/988) z Amazon dla Hub produktów',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--asin', metavar='ASIN', help='Fetch GPSR dla 1 ASIN')
    group.add_argument('--hub-id', type=int, metavar='ID', help='Fetch dla Hub product by id (bierze asin z produkty table)')
    group.add_argument('--all-from-hub', action='store_true', help='Fetch dla wszystkich Hub produktów z asin (status=magazyn) jeszcze nie w cache')
    group.add_argument('--show-cache', action='store_true', help='Pokaż cache GPSR (co już mamy)')
    group.add_argument('--purge-fallback-cache', action='store_true', help='Wyczyść TYLKO fallback AKCES entries (zachowaj realne Amazon)')
    group.add_argument('--purge-all-cache', action='store_true', help='Wyczyść CAŁY cache GPSR (force full re-scrape przy następnym push)')
    parser.add_argument('--region', default='de', choices=sorted(REGION_DOMAIN.keys()), help='Amazon region (default de)')
    parser.add_argument('--limit', type=int, default=0, help='Max produktów dla --all-from-hub / --show-cache')
    parser.add_argument('--force', action='store_true', help='Wymuś re-fetch (ignor cache)')
    parser.add_argument('--playwright', action='store_true', help='Użyj headless Chromium (Playwright) do scrap\'u lazy-loaded GPSR tabs. Wymaga: pip install playwright && playwright install chromium')
    args = parser.parse_args()

    if args.asin:
        return cmd_asin(args.asin.strip().upper(), args.region, args.force, args.playwright)
    if args.hub_id is not None:
        return cmd_hub_id(args.hub_id, args.region, args.force, args.playwright)
    if args.all_from_hub:
        return cmd_all(args.region, args.limit)
    if args.show_cache:
        return cmd_show_cache(args.limit or 20)
    if args.purge_fallback_cache:
        return cmd_purge_cache(only_fallback=True)
    if args.purge_all_cache:
        return cmd_purge_cache(only_fallback=False)
    return 1


if __name__ == '__main__':
    sys.exit(main())
