#!/usr/bin/env python3
"""generate_polish_titles.py — bulk polski PRODUCT DISPLAY TITLE → meta_title.

User raport: francuskie/niemieckie tytuły z Amazon scrape ("Caméra de recul...")
lecą na sklep bo `nazwa` (raw scrape) jest foreign + brak Polish translation.

Pole `meta_title` = canonical AI-generated POLISH product display title
(DB migration line 286: "KRYTYCZNA!"). Push (sklepakces_push.py) priorytetowo
używa meta_title nad krotki_tytul/nazwa.

NIE generujemy 75-char Allegro SEO (krotki_tytul) — to inne pole, inny use case.
TUTAJ: opisowe Polish display titles 80-150 chars, naturalna polszczyzna.

Usage:
    python3 scripts/generate_polish_titles.py --dry-run --limit 5
    python3 scripts/generate_polish_titles.py
    python3 scripts/generate_polish_titles.py --force  # nadpisz istniejące

@author: Akces Hub
"""
import argparse
import json
import logging
import os
import re
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


def _build_prompt(nazwa: str, brand: str = '', kategoria: str = '', ean: str = '') -> str:
    """Polish display title prompt — naturalna nazwa do karty produktu."""
    brand_hint = f'\nMarka: {brand}' if brand else ''
    kat_hint = f'\nKategoria: {kategoria}' if kategoria else ''
    ean_hint = f'\nEAN: {ean}' if ean else ''
    return f"""Jesteś expertem od opisywania produktów e-commerce po polsku.

ORYGINALNA NAZWA (może być po angielsku/francusku/niemiecku):
{nazwa}{brand_hint}{kat_hint}{ean_hint}

ZADANIE: Wygeneruj POLSKĄ nazwę produktu do display na karcie sklepu.

ZASADY:
1. Długość 60-150 znaków (NIE 75-char Allegro SEO — to inna rzecz)
2. Naturalna polszczyzna, czytelna dla klienta
3. Zawiera: rodzaj produktu + marka + model/parametry kluczowe
4. Tytuł w Title Case (Pierwsza Litera Każdego Słowa)
5. NIE zostawiaj zwrotów obcojęzycznych (przetłumacz "Caméra de recul" → "Kamera Cofania")
6. NIE używaj emoji ani znaków specjalnych (poza myślnikami)
7. Końcówki techniczne (HD, 1080p, 12V, IP68) zostaw w oryginale
8. Bez kropki na końcu

PRZYKŁADY:
"Sony X200 Backup Camera 170 Degree IP68 Waterproof 12V"
→ "Kamera Cofania Sony X200 170° IP68 Wodoodporna 12V Wsteczna do Samochodu"

"Caméra de recul sans fil hd 1080p 5 pouces kamera de recul voiture ensemble étanche deux canaux vision nocturne t2"
→ "Bezprzewodowa Kamera Cofania HD 1080p 5 Cali Wodoodporna z Wizją Nocną Dwukanałowa do Samochodu"

"Anker PowerCore 10000mAh USB-C Power Bank PD Fast Charging"
→ "Powerbank Anker PowerCore 10000mAh USB-C Power Delivery Szybkie Ładowanie"

Zwróć WYŁĄCZNIE valid JSON:
{{"title": "polski tytuł 60-150 znaków"}}

Bez markdown, tylko czysty JSON."""


def _gen_title(nazwa: str, brand: str, kategoria: str, ean: str, api_key: str) -> str:
    """Generuj polski display title via Gemini 2.5-flash z dynamic thinking."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return ''
    client = genai.Client(api_key=api_key)
    prompt = _build_prompt(nazwa, brand, kategoria, ean)
    for model_name in ('gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.0-flash'):
        for attempt in range(2):
            try:
                config_kwargs = {
                    'temperature': 0.3,
                    'max_output_tokens': 4096,
                    'response_mime_type': 'application/json',
                }
                if '2.5' in model_name:
                    config_kwargs['thinking_config'] = types.ThinkingConfig(thinking_budget=-1)
                config = types.GenerateContentConfig(**config_kwargs)
                resp = client.models.generate_content(
                    model=model_name, contents=prompt, config=config,
                )
                text = (resp.text or '').strip()
                if not text:
                    continue
                # Strip markdown fences
                if text.startswith('```'):
                    nl = text.find('\n')
                    if nl > 0:
                        text = text[nl + 1:]
                    if text.endswith('```'):
                        text = text[:-3].rstrip()
                # Try parse + regex fallback
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    m = re.search(r'\{.*\}', text, re.DOTALL)
                    if m:
                        try:
                            data = json.loads(m.group(0))
                        except json.JSONDecodeError:
                            continue
                    else:
                        continue
                title = (data.get('title') or '').strip()
                if title and 20 <= len(title) <= 200:
                    return title
            except Exception as e:
                err = str(e)
                if '429' in err or 'quota' in err.lower():
                    time.sleep(3 * (2 ** attempt))
                    continue
                if '404' in err:
                    break
                logger.debug(f'Gemini fail ({model_name}): {e}')
                return ''
    return ''


def find_candidates(force: bool, limit: int) -> list:
    """Produkty status=magazyn z pustym meta_title (lub force)."""
    conn = get_db()
    if force:
        sql = '''SELECT id, nazwa, krotki_tytul, meta_title, kategoria, asin, ean,
                        parameters
                 FROM produkty
                 WHERE status='magazyn' AND nazwa IS NOT NULL AND nazwa != ''
                 ORDER BY id'''
    else:
        sql = '''SELECT id, nazwa, krotki_tytul, meta_title, kategoria, asin, ean,
                        parameters
                 FROM produkty
                 WHERE status='magazyn' AND nazwa IS NOT NULL AND nazwa != ''
                   AND (meta_title IS NULL OR meta_title = '' OR LENGTH(meta_title) < 10)
                 ORDER BY id'''
    if limit > 0:
        sql += f' LIMIT {int(limit)}'
    return [dict(r) for r in conn.execute(sql).fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description='Bulk-generate polskie display titles → meta_title')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--force', action='store_true', help='Nadpisz istniejące meta_title')
    args = parser.parse_args()

    api_key = get_config('gemini_api_key', '') or os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        print('ERROR: brak GEMINI_API_KEY')
        return 2

    candidates = find_candidates(args.force, args.limit)
    if not candidates:
        print('Nic do zrobienia.')
        return 0
    print(f'Znaleziono {len(candidates)} produktów wymagających polskiego meta_title '
          f'(force={args.force}, throttle 1s/req)\n')

    ok = err = skip = 0
    conn = get_db()
    for i, p in enumerate(candidates, 1):
        nazwa = (p.get('nazwa') or '').strip()
        if not nazwa:
            skip += 1
            continue
        # Extract brand z parameters JSON jeśli jest
        brand = ''
        try:
            if p.get('parameters'):
                params = json.loads(p['parameters'])
                if isinstance(params, dict):
                    brand = (params.get('brand') or params.get('marka') or '').strip()
        except (json.JSONDecodeError, TypeError):
            pass

        prefix = f'[{i:>4}/{len(candidates)}] id={p["id"]:>5}'
        new_title = _gen_title(nazwa, brand, p.get('kategoria') or '', p.get('ean') or '', api_key)
        if not new_title:
            print(f'  ⊘ {prefix} Gemini fail (no title)')
            skip += 1
            continue

        if args.dry_run:
            print(f'  [DRY] {prefix} "{nazwa[:35]}..." → "{new_title[:70]}"')
        else:
            conn.execute('UPDATE produkty SET meta_title = ? WHERE id = ?', (new_title, p['id']))
            conn.commit()
            print(f'  ✅ {prefix} "{new_title[:70]}"')
        ok += 1
        time.sleep(1.0)

    print(f'\nResults: ok={ok}  skip={skip}  error={err}  total={len(candidates)}')
    if not args.dry_run and ok > 0:
        print(f'\n✅ {ok} produktów ma teraz polski meta_title.')
        print('   Re-push do sklepu:')
        print('     python3 scripts/push_sklepakces.py --all --limit 200')
    return 0


if __name__ == '__main__':
    sys.exit(main())
