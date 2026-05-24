#!/usr/bin/env python3
"""fix_gpsr_addresses.py — kompleksowy fix dla "adres EU rep'a nie pokazuje się
na sklepie".

Workflow:
  1. PRE-CHECK: pokaż stan brand_overrides (ile ma address, ile pustych)
  2. CLEANUP: usuń config fallback z personalnymi danymi (AKCES)
  3. CLEANUP: usuń brand_overrides bez address (incomplete Gemini entries
              z poprzednich runów PRZED moim fix wymagającym address)
  4. RE-FILL: Gemini auto_fill na brand'ach bez override (zostawiamy manual)
  5. RE-PUSH: pchnij wszystkie produkty z brand override na sklep z force=True
              (overwrite stale AKCES data na WC)

Po tym karty produktów na sklepie pokazują real EU rep z addresem.

Usage:
    # Dry-run — pokaż co by zrobiło:
    python3 scripts/fix_gpsr_addresses.py --dry-run

    # Wykonaj:
    python3 scripts/fix_gpsr_addresses.py

    # Tylko etap N (np. tylko re-push bez Gemini):
    python3 scripts/fix_gpsr_addresses.py --skip-gemini

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
for _noisy in ('google_genai', 'google.genai', 'httpx', 'httpcore', 'urllib3'):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

from modules.database import get_db, get_config, set_config  # noqa: E402


# ────────────────────────────────────────────────────────────────────────
# Krok 1: PRE-CHECK
# ────────────────────────────────────────────────────────────────────────

def step1_precheck() -> dict:
    """Stan obecny gpsr_brand_overrides."""
    conn = get_db()
    print('\n━━━ KROK 1: PRE-CHECK ━━━')
    total = conn.execute('SELECT COUNT(*) FROM gpsr_brand_overrides').fetchone()[0]
    with_addr = conn.execute(
        "SELECT COUNT(*) FROM gpsr_brand_overrides "
        "WHERE responsible_person_address IS NOT NULL AND responsible_person_address != ''"
    ).fetchone()[0]
    with_email = conn.execute(
        "SELECT COUNT(*) FROM gpsr_brand_overrides "
        "WHERE responsible_person_email IS NOT NULL AND responsible_person_email != ''"
    ).fetchone()[0]
    by_source = conn.execute(
        'SELECT source, COUNT(*) FROM gpsr_brand_overrides GROUP BY source'
    ).fetchall()
    no_addr = conn.execute(
        "SELECT brand, source FROM gpsr_brand_overrides "
        "WHERE responsible_person_address IS NULL OR responsible_person_address = ''"
    ).fetchall()

    print(f'  Total brand overrides:   {total}')
    print(f'  Z adresem:               {with_addr}/{total}  ({100*with_addr//max(total,1)}%)')
    print(f'  Z emailem:               {with_email}/{total}  ({100*with_email//max(total,1)}%)')
    print(f'  Po source:               {", ".join(f"{s}={c}" for s, c in by_source)}')
    print(f'  BEZ adresu (do re-fill): {len(no_addr)}')
    if no_addr and len(no_addr) <= 15:
        for r in no_addr:
            print(f'    - {r["brand"]:<25} source={r["source"]}')
    elif no_addr:
        for r in list(no_addr)[:10]:
            print(f'    - {r["brand"]:<25} source={r["source"]}')
        print(f'    ... + {len(no_addr) - 10} więcej')

    fallback_name = (get_config('fallback_responsible_person_name', '') or '').strip()
    print(f'\n  Fallback w config:       "{fallback_name}"')
    if 'gauza' in fallback_name.lower() or 'akces' in fallback_name.lower():
        print(f'  ⚠️  PERSONAL DATA LEAK — Twoje dane są na produktach bez brand override!')

    return {
        'total': total,
        'with_addr': with_addr,
        'no_addr_brands': [r['brand'] for r in no_addr if r['source'] != 'manual'],
        'fallback_personal': 'gauza' in fallback_name.lower() or 'akces' in fallback_name.lower(),
    }


# ────────────────────────────────────────────────────────────────────────
# Krok 2: CLEANUP fallback config (personal data leak)
# ────────────────────────────────────────────────────────────────────────

def step2_cleanup_fallback(dry: bool) -> int:
    print('\n━━━ KROK 2: CLEANUP FALLBACK CONFIG (personal data) ━━━')
    removed = 0
    conn = get_db()
    for key in ('fallback_responsible_person_name',
                'fallback_responsible_person_address',
                'fallback_responsible_person_email'):
        val = (get_config(key, '') or '').strip()
        if val and ('gauza' in val.lower() or 'akces' in val.lower()
                    or 'mieszkowic' in val.lower() or 'poniatowsk' in val.lower()):
            if dry:
                print(f'  [DRY] usunąłbym: {key} = "{val[:50]}"')
            else:
                conn.execute('DELETE FROM config WHERE key = ?', (key,))
                conn.commit()
                print(f'  ✅ usunięto: {key}')
            removed += 1
    if removed == 0:
        print('  ✅ brak personal data w fallback config (OK)')
    else:
        print(f'  Po usunięciu fallback wraca do default = CET PRODUCT SERVICE')
    return removed


# ────────────────────────────────────────────────────────────────────────
# Krok 3: CLEANUP brand_overrides bez address (incomplete)
# ────────────────────────────────────────────────────────────────────────

def step3_cleanup_incomplete(dry: bool) -> int:
    print('\n━━━ KROK 3: CLEANUP brand_overrides BEZ ADRESU (incomplete) ━━━')
    conn = get_db()
    # CHRONIMY source='manual' nawet jeśli ma puste address (user wie co robi)
    rows = conn.execute(
        "SELECT brand, source FROM gpsr_brand_overrides "
        "WHERE (responsible_person_address IS NULL OR responsible_person_address = '') "
        "  AND source != 'manual'"
    ).fetchall()
    if not rows:
        print('  ✅ wszystkie brand overrides mają address (lub są manual)')
        return 0
    print(f'  Znaleziono {len(rows)} incomplete entries do usunięcia (re-fill via Gemini):')
    for r in rows[:20]:
        print(f'    - {r["brand"]} ({r["source"]})')
    if len(rows) > 20:
        print(f'    ... + {len(rows) - 20} więcej')
    if dry:
        print(f'  [DRY] usunąłbym {len(rows)} entries')
        return len(rows)
    conn.execute(
        "DELETE FROM gpsr_brand_overrides "
        "WHERE (responsible_person_address IS NULL OR responsible_person_address = '') "
        "  AND source != 'manual'"
    )
    conn.commit()
    print(f'  ✅ usunięto {len(rows)} incomplete')
    return len(rows)


# ────────────────────────────────────────────────────────────────────────
# Krok 4: RE-FILL via Gemini (nowy strict prompt wymaga address)
# ────────────────────────────────────────────────────────────────────────

def step4_refill_gemini(dry: bool, api_key: str) -> int:
    print('\n━━━ KROK 4: RE-FILL via Gemini (wymagany address) ━━━')
    if not api_key:
        print('  ⚠️  brak GEMINI_API_KEY — SKIP. Set w Hub config.')
        return 0
    # Importuj cmd_all z auto_fill — ten sam workflow co `--all`
    # ale używamy current state DB (po step3 usunięto incomplete)
    from scripts.auto_fill_gpsr_brands import cmd_all
    if dry:
        print('  [DRY] uruchomiłbym auto_fill_gpsr_brands.py --all')
        return 0
    print('  Running cmd_all(force=True, limit=0, force_manual=False)...')
    return cmd_all(force=True, limit=0, api_key=api_key, force_manual=False)


# ────────────────────────────────────────────────────────────────────────
# Krok 5: RE-PUSH wszystkich produktów (force=True)
# ────────────────────────────────────────────────────────────────────────

def step5_repush(dry: bool, limit: int = 0) -> dict:
    print('\n━━━ KROK 5: RE-PUSH produktów na sklep (force=True) ━━━')
    conn = get_db()
    # Liczymy produkty które są na sklepie (są w mirror sklepakces_products)
    count = conn.execute(
        "SELECT COUNT(*) FROM sklepakces_products"
    ).fetchone()[0]
    print(f'  Produktów na sklepie (sklepakces_products mirror): {count}')

    # Liczymy produkty z brand w parameters (kandydaci do nowego GPSR)
    with_brand = conn.execute(
        "SELECT COUNT(*) FROM produkty p "
        "JOIN sklepakces_products s ON s.sku IN ('EAN-' || p.ean, 'HUB-' || p.id) "
        "WHERE p.parameters IS NOT NULL AND json_valid(p.parameters) = 1 "
        "  AND json_extract(p.parameters, '$.brand') IS NOT NULL"
    ).fetchone()[0]
    print(f'  Produktów z brand (kandydaci do GPSR override): {with_brand}')

    if dry:
        print(f'  [DRY] re-push wszystkich {count} produktów (force=True)')
        return {'pushed': 0, 'skipped': count, 'errors': 0}

    # Import push_all
    try:
        from modules.sklepakces_push import push_all
    except ImportError as e:
        print(f'  ❌ import sklepakces_push failed: {e}')
        return {'pushed': 0, 'skipped': 0, 'errors': 1}

    print(f'  Running push_all(force=True)... (może potrwać kilka minut)')
    t0 = time.perf_counter()
    try:
        result = push_all(force=True)
    except Exception as e:
        print(f'  ❌ push_all error: {e}')
        return {'pushed': 0, 'skipped': 0, 'errors': 1}
    dt = time.perf_counter() - t0
    if isinstance(result, dict):
        print(f'  ✅ push_all skończony w {dt:.0f}s: {result}')
        return result
    print(f'  ✅ push_all skończony w {dt:.0f}s (raw: {result})')
    return {'pushed': result, 'skipped': 0, 'errors': 0}


# ────────────────────────────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description='End-to-end fix dla "address EU rep nie pokazuje się na sklepie"',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--dry-run', action='store_true', help='Pokaż co by zrobiło, nic nie zmieniaj')
    parser.add_argument('--skip-gemini', action='store_true', help='Pomiń krok 4 (re-fill Gemini)')
    parser.add_argument('--skip-push', action='store_true', help='Pomiń krok 5 (re-push)')
    args = parser.parse_args()

    print('═' * 60)
    print('🔧 GPSR ADDRESSES — END-TO-END FIX')
    print('═' * 60)

    # Krok 1
    state = step1_precheck()

    # Krok 2
    step2_cleanup_fallback(args.dry_run)

    # Krok 3
    deleted = step3_cleanup_incomplete(args.dry_run)

    # Krok 4
    if not args.skip_gemini:
        api_key = get_config('gemini_api_key', '') or os.environ.get('GEMINI_API_KEY', '')
        step4_refill_gemini(args.dry_run, api_key)
    else:
        print('\n━━━ KROK 4: RE-FILL Gemini — SKIPPED ━━━')

    # Krok 5
    if not args.skip_push:
        step5_repush(args.dry_run)
    else:
        print('\n━━━ KROK 5: RE-PUSH — SKIPPED ━━━')

    print('\n' + '═' * 60)
    if args.dry_run:
        print('🏁 DRY-RUN skończony. Re-run BEZ --dry-run żeby wykonać.')
    else:
        print('✅ FIX skończony. Sprawdź kartę produktu na sklepie:')
        print('   https://sklepakces.pl/produkt/[any]')
        print('   Sekcja GPSR powinna teraz pokazywać:')
        print('     - Producent + adres')
        print('     - Osoba odpowiedzialna + ADRES + email (jeśli plugin v1.1.1 deployed)')
    print('═' * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())
