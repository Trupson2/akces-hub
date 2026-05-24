#!/usr/bin/env python3
"""generate_polish_titles.py — bulk polski tytuł dla produktów bez krotki_tytul.

Push do sklepu używa `krotki_tytul` (polski) → fallback do `nazwa` (Amazon raw,
często francuski/niemiecki/angielski). Skrypt znajduje produkty z pustym
`krotki_tytul` i generuje polski tytuł via Gemini.

Usage:
    # Dry-run zobacz co by zrobiło:
    python3 scripts/generate_polish_titles.py --dry-run

    # Generuj dla wszystkich (status=magazyn):
    python3 scripts/generate_polish_titles.py

    # Limit (test):
    python3 scripts/generate_polish_titles.py --limit 10

    # Force re-generate (nawet gdy krotki_tytul już set):
    python3 scripts/generate_polish_titles.py --force

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
for _noisy in ('google_genai', 'google.genai', 'httpx', 'httpcore', 'urllib3', 'requests', 'urllib'):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

from modules.database import get_db, get_config  # noqa: E402
from modules.title_generator_ai import generate_allegro_title_ai  # noqa: E402


def find_candidates(force: bool, limit: int) -> list:
    """Znajdź produkty wymagające polskiego tytułu."""
    conn = get_db()
    if force:
        sql = '''
            SELECT id, nazwa, krotki_tytul, kategoria, asin, bullet_points
            FROM produkty
            WHERE status = 'magazyn' AND nazwa IS NOT NULL AND nazwa != ''
            ORDER BY id
        '''
    else:
        sql = '''
            SELECT id, nazwa, krotki_tytul, kategoria, asin, bullet_points
            FROM produkty
            WHERE status = 'magazyn'
              AND nazwa IS NOT NULL AND nazwa != ''
              AND (krotki_tytul IS NULL OR krotki_tytul = '' OR LENGTH(krotki_tytul) < 5)
            ORDER BY id
        '''
    if limit > 0:
        sql += f' LIMIT {int(limit)}'
    rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Bulk-generate polskie tytuły (krotki_tytul) via Gemini AI',
    )
    parser.add_argument('--dry-run', action='store_true', help='Pokaż co by zrobiło')
    parser.add_argument('--limit', type=int, default=0, help='Max N produktów (test)')
    parser.add_argument('--force', action='store_true', help='Re-generate nawet jeśli krotki_tytul jest')
    args = parser.parse_args()

    api_key = get_config('gemini_api_key', '') or os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        print('ERROR: brak GEMINI_API_KEY w Hub config')
        return 2

    candidates = find_candidates(args.force, args.limit)
    if not candidates:
        print('Nic do zrobienia — wszystkie produkty status=magazyn mają krotki_tytul.')
        return 0

    print(f'Znaleziono {len(candidates)} produktów wymagających polskiego tytułu '
          f'(force={args.force}, throttle 0.5s/req)\n')

    ok = err = skip = 0
    conn = get_db()
    for i, p in enumerate(candidates, 1):
        nazwa = (p.get('nazwa') or '').strip()
        if not nazwa:
            skip += 1
            continue
        prefix = f'[{i:>4}/{len(candidates)}] id={p["id"]:>5}'
        try:
            # Bullet points może być JSON string lub plaintext
            bullets = p.get('bullet_points') or ''
            if isinstance(bullets, str) and bullets.startswith('['):
                try:
                    import json
                    bullets = json.loads(bullets)
                except Exception:
                    bullets = [bullets]
            elif isinstance(bullets, str):
                bullets = [b.strip() for b in bullets.split('\n') if b.strip()][:5]

            new_title = generate_allegro_title_ai(
                {
                    'nazwa': nazwa,
                    'bullet_points': bullets if isinstance(bullets, list) else [],
                    'kategoria': p.get('kategoria', ''),
                    'asin': p.get('asin', ''),
                },
                api_key,
                max_length=75,
            )
            if not new_title or new_title == nazwa or len(new_title) < 5:
                print(f'  ⊘ {prefix} Gemini fail (zwrócił raw/short): "{(new_title or "")[:40]}"')
                skip += 1
                continue

            if args.dry_run:
                print(f'  [DRY] {prefix} "{nazwa[:30]}..." → "{new_title[:50]}"')
            else:
                conn.execute(
                    'UPDATE produkty SET krotki_tytul = ? WHERE id = ?',
                    (new_title, p['id']),
                )
                conn.commit()
                print(f'  ✅ {prefix} "{new_title[:55]}"')
            ok += 1
            time.sleep(0.5)
        except Exception as e:
            print(f'  ❌ {prefix} ERROR: {e}')
            err += 1

    print(f'\nResults: ok={ok}  skip={skip}  error={err}  total={len(candidates)}')
    if not args.dry_run and ok > 0:
        print(f'\n✅ {ok} produktów ma teraz polski krotki_tytul.')
        print('   Aby pchnąć zaktualizowane tytuły na sklep:')
        print('     python3 scripts/push_sklepakces.py --all --limit 200')
    return 0


if __name__ == '__main__':
    sys.exit(main())
