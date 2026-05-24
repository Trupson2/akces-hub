#!/usr/bin/env python3
"""
auto_fill_gpsr_brands.py — Gemini fetch EU responsible person per brand.

Hub ma 48+ brandów (z parameters JSON Gemini-extracted). Manual lookup dla każdego
z Amazon listing = dużo pracy. Gemini ma knowledge o popularnych brandach
(Canon, Tesla, UGREEN) i może podać best-guess EU rep.

WAŻNE: Gemini może halucinować! Każdy entry dostaje source='gemini_guess' tag.
User powinien zweryfikować przed prod use (z Amazon listing prawdziwego produktu).

Workflow:
    # 1. Auto-fill wszystkie wykryte brandy:
    python3 scripts/auto_fill_gpsr_brands.py --all

    # 2. Lista po fill:
    python3 scripts/gpsr_brands.py --list

    # 3. Verify Canon (popularny, Gemini powinien wiedzieć):
    python3 scripts/gpsr_brands.py --show Canon

    # 4. Jeśli błąd — manual override:
    python3 scripts/gpsr_brands.py --add Canon --rp-name "Canon Europa N.V." --rp-addr "..." --rp-email "..."

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

from modules.database import get_db, get_config  # noqa: E402
from modules.amazon_gpsr_scraper import (  # noqa: E402
    init_brand_overrides_schema, get_brand_gpsr_override, save_brand_gpsr_override,
)

THROTTLE_SEC = 1.5  # Gemini paid tier safe


def _gemini_lookup(brand: str, api_key: str) -> dict:
    """Zapytaj Gemini o EU responsible person dla brand. Returns dict lub {}."""
    try:
        import google.generativeai as genai
    except ImportError:
        logger.error('google-generativeai nie zainstalowane')
        return {}

    prompt = f"""Jesteś ekspertem prawnym GPSR (UE 2023/988).

BRAND: "{brand}"

Podaj EU responsible person dla tego brandu (per UE 2023/988 art. 8).
Wymagane pola:
- name: pełna nazwa firmy EU representative (np. "Canon Europa N.V.", "Sony Europe B.V.")
- address: pełen adres EU rep (ulica, miasto, kod, kraj)
- email: kontakt mailowy EU rep

Jeśli to popularny brand (Canon, Sony, Bosch, Tesla, UGREEN, JJC, Smallrig, PETKIT etc.),
podaj znany EU representative. Dla niszowych chińskich brandów (np. AZDOME, HOMCA) —
często EU rep to Amazon Retourenkauf / CET PRODUCT SERVICE SP. Z O.O. (Polska).

Jeśli NIE znasz, zwróć {{name: null, address: null, email: null, confidence: "low"}}.
Dla pewnych zwróć confidence: "high". Dla średnich (znany brand ale rep niepewny):
"medium".

Zwróć WYŁĄCZNIE valid JSON:
{{
  "name": "string or null",
  "address": "string or null",
  "email": "string or null",
  "confidence": "high|medium|low",
  "notes": "krótki komentarz, np. źródło info"
}}

Bez markdown, tylko czysty JSON."""

    genai.configure(api_key=api_key)
    for model_name in ('gemini-2.0-flash', 'gemini-1.5-flash'):
        try:
            model = genai.GenerativeModel(model_name)
            resp = model.generate_content(
                prompt,
                generation_config={
                    'temperature': 0.1,  # Low = factual
                    'max_output_tokens': 500,
                    'response_mime_type': 'application/json',
                },
            )
            text = (resp.text or '').strip()
            if text:
                return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f'Gemini JSON parse fail brand={brand}: {e}')
            return {}
        except Exception as e:
            err = str(e)
            if '404' in err or 'NOT_FOUND' in err:
                continue
            logger.warning(f'Gemini fail ({model_name}) brand={brand}: {e}')
            return {}
    return {}


def _normalize_brand_aliases(brands: list) -> list:
    """Merge case variants ('UGREEN' i 'Ugreen' = ten sam brand)."""
    canonical = {}
    for b in brands:
        key = b.lower().strip()
        if key not in canonical or len(b) > len(canonical[key]):
            canonical[key] = b
    return sorted(canonical.values())


def cmd_all(force: bool, limit: int, api_key: str) -> int:
    """Auto-fill GPSR override dla wszystkich brandów w Hub."""
    conn = get_db()
    init_brand_overrides_schema(conn)

    # Get distinct brandów z parameters JSON
    rows = conn.execute(
        "SELECT DISTINCT parameters FROM produkty "
        "WHERE parameters IS NOT NULL AND parameters != '' AND parameters LIKE '%brand%'"
    ).fetchall()
    brand_set = set()
    for r in rows:
        try:
            p = json.loads(r[0])
            if isinstance(p, dict):
                for k in ('brand', 'marka', 'producent', 'manufacturer'):
                    v = p.get(k)
                    if v and isinstance(v, str) and len(v.strip()) >= 2:
                        brand_set.add(v.strip())
                        break
        except (json.JSONDecodeError, TypeError):
            continue
    brands = _normalize_brand_aliases(list(brand_set))
    if limit > 0:
        brands = brands[:limit]
    print(f'Znaleziono {len(brands)} unikalnych brandów do auto-fill (throttle {THROTTLE_SEC}s/req, force={force})\n')

    ok = skip = err = 0
    for i, brand in enumerate(brands):
        if not force and get_brand_gpsr_override(brand, conn=conn):
            print(f'  ⊘ [{i+1:>3}/{len(brands)}] {brand:<25} already in DB (use --force)')
            skip += 1
            continue
        if i > 0:
            time.sleep(THROTTLE_SEC)
        try:
            data = _gemini_lookup(brand, api_key)
        except Exception as e:
            print(f'  ❌ [{i+1:>3}/{len(brands)}] {brand:<25} Gemini error: {e}')
            err += 1
            continue
        if not data or not data.get('name'):
            print(f'  ⊘ [{i+1:>3}/{len(brands)}] {brand:<25} Gemini: unknown ({data.get("confidence", "?")})')
            skip += 1
            continue
        conf = data.get('confidence', 'medium')
        save_brand_gpsr_override(
            brand,
            {
                'manufacturer_name': brand,  # Producent = sam brand (znany konsumentowi)
                'manufacturer_address': '',
                'responsible_person_name': data.get('name', ''),
                'responsible_person_address': data.get('address', ''),
                'responsible_person_email': data.get('email', ''),
            },
            source=f'gemini_{conf}',
            conn=conn,
        )
        print(f'  ✅ [{i+1:>3}/{len(brands)}] {brand:<25} rep="{data["name"][:35]}" conf={conf}')
        ok += 1

    print(f'\nResults: ok={ok}  skip={skip}  error={err}  total={len(brands)}')
    print('\n⚠️  UWAGA: Gemini guesses — zweryfikuj brand entries z Amazon listing.')
    print('    Lista: python3 scripts/gpsr_brands.py --list')
    print('    Edit:  python3 scripts/gpsr_brands.py --add BRAND --rp-name "..." ...')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Gemini auto-fill GPSR EU rep dla wszystkich brandów w Hub',
    )
    parser.add_argument('--all', action='store_true', required=True, help='Fill all distinct brands')
    parser.add_argument('--force', action='store_true', help='Nadpisz istniejące (default skip)')
    parser.add_argument('--limit', type=int, default=0, help='Max N brandów (test)')
    args = parser.parse_args()

    api_key = get_config('gemini_api_key', '') or os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        print('ERROR: brak GEMINI_API_KEY w Hub config')
        return 2

    return cmd_all(args.force, args.limit, api_key)


if __name__ == '__main__':
    sys.exit(main())
