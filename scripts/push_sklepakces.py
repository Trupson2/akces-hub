#!/usr/bin/env python3
"""
push_sklepakces.py — CLI dla Hub → sklepakces.pl WC product sync.

Usage:
    # Sanity check konfiguracji (URL + HMAC secret + endpoint reachable)
    python scripts/push_sklepakces.py --check

    # Dry run: pokaż co BYŁOBY wysłane (bez HTTP, walidacja schema)
    python scripts/push_sklepakces.py --dry-run --limit 10

    # Push 1 produkt by Hub ID
    python scripts/push_sklepakces.py --id 42

    # Push wszystkich eligible (status='magazyn' AND not in mirror)
    python scripts/push_sklepakces.py --all
    python scripts/push_sklepakces.py --all --limit 50

Wymagana konfiguracja (Hub config table):
    set_config('sklepakces_url',         'https://sklepakces.pl')
    set_config('sklepakces_hmac_secret', '<64 hex chars z plugin WP option akces_hub_hmac_secret>')

Plugin endpoint: POST {sklepakces_url}/wp-json/akces/v1/products
Rate limit po stronie plugin: 60 req/min — CLI throttle 1.1s/req (safe ~54/min).

@author: Akces Hub
"""
import argparse
import json
import logging
import os
import sys
from typing import List

# Project root na sys.path (umożliwia `from modules.xxx import yyy`)
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO'),
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)

from modules.sklepakces_push import (  # noqa: E402
    push_one_product,
    push_all_unsynced,
    get_sklepakces_url,
    ENDPOINT_PATH,
)
from modules.sklepakces_hmac import get_hmac_secret  # noqa: E402


def cmd_check() -> int:
    """Verify konfiguracja + endpoint reachable. Returns exit code."""
    import requests

    url = get_sklepakces_url()
    secret = get_hmac_secret()

    print(f'sklepakces_url:         {url or "(NIE USTAWIONY) — set_config(\"sklepakces_url\", \"https://sklepakces.pl\")"}')
    print(f'sklepakces_hmac_secret: {f"ustawiony ({len(secret)} chars)" if secret else "(NIE USTAWIONY) — set_config(\"sklepakces_hmac_secret\", \"<64 hex>\")"}')

    if not url or not secret:
        print('\n❌ Brak wymaganej konfiguracji.')
        return 2

    # Endpoint reachable test (POST bez HMAC → oczekiwany 401 z plugin)
    try:
        r = requests.post(url + ENDPOINT_PATH, json={}, timeout=10)
        print(f'\nEndpoint test:           POST {url}{ENDPOINT_PATH}')
        print(f'  HTTP {r.status_code}')
        if r.status_code == 401:
            try:
                err = r.json().get('code', '')
                print(f'  code: {err}')
                if err in ('akces_missing_signature', 'akces_invalid_signature'):
                    print('\n✅ Konfig OK + endpoint odpowiada (HMAC verify działa — 401 bez podpisu jest oczekiwane).')
                    return 0
            except Exception:
                pass
        elif r.status_code == 404:
            print('\n❌ Endpoint 404 — plugin akces-hub-connector nieaktywny lub REST route nie zarejestrowany.')
            return 3
        else:
            print(f'\n⚠ Niespodziewany status {r.status_code} — sprawdź plugin / WP REST API.')
            return 4
    except requests.RequestException as e:
        print(f'\n❌ Request failed: {e}')
        return 5

    return 0


def cmd_push_id(hub_id: int, with_gpsr: bool, gpsr_region: str, force: bool, allow_no_allegro: bool) -> int:
    print(f'Push Hub produkt id={hub_id} (gpsr={"AUTO" if with_gpsr else "OFF"}, region={gpsr_region}, force={force}, require_allegro={not allow_no_allegro})...')
    result = push_one_product(
        hub_id, with_gpsr=with_gpsr, gpsr_region=gpsr_region, force=force,
        require_allegro_active=not allow_no_allegro,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get('status') in ('ok', 'skip') else 1


def cmd_dry_run(limit: int) -> int:
    print(f'DRY RUN — pokażę {limit or "wszystkie"} eligible produkt(ów) (status="magazyn" AND not in mirror)...\n')
    results: List[dict] = list(push_all_unsynced(limit=limit, dry_run=True))

    if not results:
        print('Brak eligible produktów (już zsynchronizowane lub Hub `produkty` pusta dla status="magazyn").')
        return 0

    invalid: List[dict] = []
    for r in results:
        marker = '✓' if r.get('valid') else '✗'
        print(f'  {marker} hub_id={r["hub_id"]:>5} sku={r["sku"]:<20} stan={r["condition"]:<14} stock={r["stock"]:>3} cena={r["price"]} tytul={(r["title"] or "")[:60]}')
        if not r.get('valid'):
            invalid.append(r)

    print(f'\nTotal: {len(results)}  valid: {len(results) - len(invalid)}  invalid: {len(invalid)}')
    if invalid:
        print('\nINVALID (do naprawy w Hub przed push):')
        for r in invalid:
            print(f'  hub_id={r["hub_id"]} sku={r["sku"]} -- {r["validation_error"]}')
    return 0


def cmd_push_all(limit: int, with_gpsr: bool, gpsr_region: str, allow_no_allegro: bool,
                 include_listed: bool = False, force: bool = False) -> int:
    status_filter = '*' if include_listed else 'magazyn'
    status_label = 'magazyn+wystawiony+aktywny+z_aukcją_Allegro' if include_listed else 'magazyn'
    if force:
        # Force re-push: bypass mirror check przez push_one_product per ID
        print(f'PUSH ALL --force — re-push WSZYSTKICH eligible (status="{status_label}", włącznie z już-zsynchronizowanymi)...\n')
        from modules.database import get_db
        from modules.sklepakces_push import push_one_product
        conn = get_db()
        if include_listed:
            sql = """
                SELECT p.id FROM produkty p
                WHERE (
                    p.status IN ('magazyn', 'wystawiony', 'aktywny')
                    OR EXISTS (
                        SELECT 1 FROM oferty o
                        WHERE o.produkt_id = p.id AND o.status = 'aktywna'
                          AND o.allegro_id IS NOT NULL AND o.allegro_id != ''
                    )
                )
                ORDER BY p.id
            """
        else:
            sql = "SELECT id FROM produkty WHERE status='magazyn' ORDER BY id"
        if limit and limit > 0:
            sql += f' LIMIT {int(limit)}'
        ids = [r[0] for r in conn.execute(sql).fetchall()]
        print(f'Re-push {len(ids)} produktów (force=True)\n')
        ok = err = skip = 0
        for i, hub_id in enumerate(ids, 1):
            try:
                r = push_one_product(
                    hub_product_id=hub_id, force=True, with_gpsr=with_gpsr,
                    gpsr_region=gpsr_region,
                    require_allegro_active=not allow_no_allegro,
                )
            except Exception as e:
                print(f'  ❌ [{i}/{len(ids)}] hub_id={hub_id} EXCEPTION: {e}')
                err += 1
                continue
            marker = {'ok': '✅', 'skip': '⊘', 'error': '❌'}.get(r.get('status'), '?')
            sku = r.get('sku', '(no-sku)')
            http = r.get('http_status')
            msg = ''
            if r.get('status') == 'ok':
                msg = f' wc_id={r.get("wc_product_id")} {r.get("duration_ms")}ms'
            elif r.get('status') != 'ok':
                msg = f' -- {r.get("msg") or "?"}'
            print(f'  {marker} [{i:>3}/{len(ids)}] hub_id={hub_id:>5} sku={sku:<20} http={http}{msg}')
            if r.get('status') == 'ok':
                ok += 1
            elif r.get('status') == 'skip':
                skip += 1
            else:
                err += 1
        print(f'\nResults: ok={ok}  skip={skip}  error={err}  total={ok+skip+err}')
        return 0 if err == 0 else 1

    # Default path: push_all_unsynced (skip if already in mirror)
    print(f'PUSH ALL — wysyłam {limit or "wszystkie"} eligible (status="{status_label}" AND not in mirror), throttle 1.1s/req (gpsr={"AUTO" if with_gpsr else "OFF"}, region={gpsr_region}, require_allegro={not allow_no_allegro})...\n')
    ok = err = skip = 0
    for r in push_all_unsynced(
        limit=limit, dry_run=False, with_gpsr=with_gpsr, gpsr_region=gpsr_region,
        only_status=status_filter,
        require_allegro_active=not allow_no_allegro,
    ):
        marker = {'ok': '✅', 'skip': '⊘', 'error': '❌'}.get(r.get('status'), '?')
        sku = r.get('sku', '(no-sku)')
        http = r.get('http_status')
        msg_extra = ''
        if r.get('status') == 'error':
            msg_extra = f' -- {r.get("msg") or r.get("response", {}).get("message") or r.get("response")}'
        elif r.get('status') == 'ok':
            msg_extra = f' (wc_id={r.get("wc_product_id")}, {r.get("duration_ms")}ms)'
        elif r.get('status') == 'skip':
            msg_extra = f' -- {r.get("msg")}'
        print(f'  {marker} hub_id={r.get("hub_id"):>5} sku={sku:<20} http={http}{msg_extra}')
        if r.get('status') == 'ok':
            ok += 1
        elif r.get('status') == 'skip':
            skip += 1
        else:
            err += 1

    print(f'\nResults: ok={ok}  skip={skip}  error={err}  total={ok + skip + err}')
    return 0 if err == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Push produkty Hub → sklepakces.pl WC (HMAC-signed POST do /wp-json/akces/v1/products)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split('Usage:', 1)[1].split('Wymagana')[0] if __doc__ else '',
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true', help='Sprawdź konfigurację + endpoint reachable (nie pushuj)')
    group.add_argument('--id', type=int, metavar='HUB_ID', help='Push 1 produkt by Hub product ID')
    group.add_argument('--dry-run', action='store_true', help='Pokaż co BYŁOBY wysłane (bez HTTP, walidacja schema)')
    group.add_argument('--all', action='store_true', help='Push wszystkich eligible (status=magazyn AND not in mirror)')
    parser.add_argument('--limit', type=int, default=0, help='Max produktów (do --dry-run / --all)')
    parser.add_argument('--no-gpsr', action='store_true', help='Pomiń auto-fetch GPSR z Amazon (produkty → draft w WP)')
    parser.add_argument('--gpsr-region', default='de', choices=['de', 'pl', 'uk', 'it', 'fr', 'es'], help='Amazon region dla GPSR lookup (default de)')
    parser.add_argument('--force', action='store_true', help='Re-push nawet jeśli już w mirror (plugin UPDATE\'uje WC produkt po SKU; użyj po fix mapowania)')
    parser.add_argument('--allow-no-allegro', action='store_true', help='Pozwól push produktów BEZ aktywnej oferty Allegro (fallback na cena_allegro z DB; default = SKIP + Telegram alert)')
    parser.add_argument('--include-listed', action='store_true', help='Push też produkty BEZ status=magazyn (np. wystawione na Allegro) — szukaj po aktywnej aukcji')

    args = parser.parse_args()
    with_gpsr = not args.no_gpsr

    if args.check:
        return cmd_check()
    if args.id is not None:
        return cmd_push_id(args.id, with_gpsr, args.gpsr_region, args.force, args.allow_no_allegro)
    if args.dry_run:
        return cmd_dry_run(args.limit)
    if args.all:
        return cmd_push_all(args.limit, with_gpsr, args.gpsr_region, args.allow_no_allegro,
                            include_listed=args.include_listed, force=args.force)

    return 1


if __name__ == '__main__':
    sys.exit(main())
