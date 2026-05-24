#!/usr/bin/env python3
"""
gpsr_brands.py — manage GPSR EU responsible person per brand.

User raz wpisuje real EU rep dla AZDOME / HOMCA / itp (kopia z Amazon listing) →
wszystkie produkty tej marki na sklepie dostają ten sam rep (REAL, nie AKCES fallback).

Plus AUTO-POPULATE: gdy Amazon HTTP scraper UDAŁ się dla 1 ASIN → save dla reszty.

Usage:
    # Lista wszystkich override:
    python3 scripts/gpsr_brands.py --list

    # Dodaj/update override (z Amazon listing copy-paste):
    python3 scripts/gpsr_brands.py --add AZDOME \\
        --rp-name "AZDOME GmbH" \\
        --rp-addr "Schmalenbacher Str 1, 12435 Berlin, DE" \\
        --rp-email "support@azdome.com"

    # Usuń override:
    python3 scripts/gpsr_brands.py --remove AZDOME

    # Show 1 brand:
    python3 scripts/gpsr_brands.py --show AZDOME

@author: Akces Hub
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules.database import get_db  # noqa: E402
from modules.amazon_gpsr_scraper import (  # noqa: E402
    init_brand_overrides_schema, get_brand_gpsr_override, save_brand_gpsr_override,
)


def cmd_list() -> int:
    conn = get_db()
    init_brand_overrides_schema(conn)
    rows = conn.execute('SELECT brand, responsible_person_name, manufacturer_name, source, updated_at FROM gpsr_brand_overrides ORDER BY brand').fetchall()
    if not rows:
        print('Brak brand overrides. Dodaj: python3 scripts/gpsr_brands.py --add BRAND --rp-name "..." ...')
        return 0
    print(f'{"BRAND":<20} {"REP":<35} {"MANUFACTURER":<25} {"SOURCE":<14} UPDATED')
    print('-' * 120)
    for r in rows:
        d = dict(r)
        print(f'{d["brand"]:<20} {(d["responsible_person_name"] or "")[:33]:<35} '
              f'{(d["manufacturer_name"] or "")[:23]:<25} {d["source"]:<14} {d["updated_at"]}')
    return 0


def cmd_show(brand: str) -> int:
    o = get_brand_gpsr_override(brand)
    if not o:
        print(f'Brand "{brand}" — brak override.')
        return 1
    for k, v in o.items():
        print(f'  {k}: {v}')
    return 0


def cmd_add(brand: str, mf_name: str, mf_addr: str, rp_name: str, rp_addr: str, rp_email: str) -> int:
    if not (mf_name or rp_name):
        print('ERROR: musisz podać przynajmniej --mf-name lub --rp-name')
        return 2
    gpsr_data = {
        'manufacturer_name': mf_name,
        'manufacturer_address': mf_addr,
        'responsible_person_name': rp_name,
        'responsible_person_address': rp_addr,
        'responsible_person_email': rp_email,
    }
    ok = save_brand_gpsr_override(brand, gpsr_data, source='manual')
    if ok:
        print(f'✅ Zapisano brand="{brand}":')
        for k, v in gpsr_data.items():
            if v:
                print(f'  {k}: {v}')
        return 0
    else:
        print(f'❌ Save failed for brand="{brand}"')
        return 1


def cmd_purge_generic() -> int:
    """Usuń wszystkie entries z generic CET/Amazon Retourenkauf rep
    (auto-generated guesses które user chce zastąpić specyficznymi)."""
    conn = get_db()
    init_brand_overrides_schema(conn)
    cur = conn.execute('''
        DELETE FROM gpsr_brand_overrides
        WHERE LOWER(responsible_person_name) LIKE '%cet product service%'
           OR LOWER(responsible_person_name) LIKE '%amazon retourenkauf%'
           OR LOWER(responsible_person_name) LIKE '%amazon returns%'
    ''')
    conn.commit()
    print(f'Usunięto {cur.rowcount} entries z generic CET/Amazon Retourenkauf rep.')
    print('Możesz teraz re-run: python3 scripts/auto_fill_gpsr_brands.py --all --force')
    return 0


def cmd_remove(brand: str) -> int:
    conn = get_db()
    init_brand_overrides_schema(conn)
    cur = conn.execute('DELETE FROM gpsr_brand_overrides WHERE LOWER(brand) = LOWER(?)', (brand.strip(),))
    conn.commit()
    if cur.rowcount > 0:
        print(f'✅ Usunięto override dla brand="{brand}"')
        return 0
    print(f'⊘ Brand "{brand}" — brak override (nic do usunięcia)')
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Zarządzaj GPSR EU responsible person per brand',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--list', action='store_true', help='Lista wszystkich brand overrides')
    group.add_argument('--show', metavar='BRAND', help='Pokaż override dla 1 marki')
    group.add_argument('--add', metavar='BRAND', help='Dodaj/update override dla marki')
    group.add_argument('--remove', metavar='BRAND', help='Usuń override dla marki')
    group.add_argument('--purge-generic', action='store_true', help='Usuń wszystkie generic CET/Amazon Retourenkauf entries (przed re-run auto_fill z lepszym promptem)')

    parser.add_argument('--mf-name', default='', help='Manufacturer name (np. ZHONGSHAN STYLE APPLIANCES)')
    parser.add_argument('--mf-addr', default='', help='Manufacturer address')
    parser.add_argument('--rp-name', default='', help='EU responsible person name (np. CET PRODUCT SERVICE SP. Z O.O.)')
    parser.add_argument('--rp-addr', default='', help='EU responsible person address')
    parser.add_argument('--rp-email', default='', help='EU responsible person email')
    args = parser.parse_args()

    if args.list:
        return cmd_list()
    if args.show:
        return cmd_show(args.show)
    if args.add:
        return cmd_add(args.add, args.mf_name, args.mf_addr, args.rp_name, args.rp_addr, args.rp_email)
    if args.remove:
        return cmd_remove(args.remove)
    if args.purge_generic:
        return cmd_purge_generic()
    return 1


if __name__ == '__main__':
    sys.exit(main())
