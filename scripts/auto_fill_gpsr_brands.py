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
logger = logging.getLogger(__name__)

from modules.database import get_db, get_config  # noqa: E402
from modules.amazon_gpsr_scraper import (  # noqa: E402
    init_brand_overrides_schema, get_brand_gpsr_override, save_brand_gpsr_override,
)

THROTTLE_SEC = 4.0  # Gemini 2.5-flash paid tier: 1000 RPM teoretycznie, ale per-key/region quota bywa niższa. 4s = 15 RPM = bezpieczne dla mniejszych limitów (po user raport 429 errors).


def _clean_gemini_json(text: str) -> str:
    """Strip Gemini's common formatting bugs przed json.loads.

    Gemini 2.5-flash bywa że zwraca markdown wrap mimo response_mime_type=application/json,
    albo prefix/suffix typu "Here is the JSON:" → wytnij.
    """
    if not text:
        return text
    s = text.strip()
    # Strip markdown code fences: ```json ... ``` lub ``` ... ```
    if s.startswith('```'):
        # Remove opening fence (```json\n or ```\n)
        first_newline = s.find('\n')
        if first_newline > 0:
            s = s[first_newline + 1:]
        # Remove closing fence
        if s.endswith('```'):
            s = s[:-3].rstrip()
    # Strip leading BOM jeśli jest
    if s.startswith('﻿'):
        s = s[1:]
    return s.strip()


def _extract_json_object(text: str) -> str:
    """Spróbuj wyciągnąć pierwszy {...} JSON object z tekstu (regex fallback).

    Gdy Gemini doda komentarze/wyjaśnienie przed/po JSON, ten regex znajdzie
    najszerszy match na fragment podobny do JSON object.
    """
    if not text:
        return ''
    # Greedy match od pierwszego { do ostatniego } (najszerszy possibly-JSON span)
    m = re.search(r'\{.*\}', text, re.DOTALL)
    return m.group(0) if m else ''


def _gemini_lookup(brand: str, api_key: str) -> dict:
    """Zapytaj Gemini o EU responsible person dla brand. Returns dict lub {}.

    Strategia: prompt strict — NIE używaj generic CET/Amazon Retourenkauf
    jako default guess. Każdy brand MA SWÓJ EU rep (UE 2023/988 art. 8).
    Jeśli Gemini nie zna konkretnego brand'a — zwróć null (skip override).
    """
    # Nowy SDK google.genai (google-generativeai jest deprecated od 2025).
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.error('google-genai nie zainstalowane. Run: pip install --break-system-packages google-genai')
        return {}

    prompt = f"""Jesteś ekspertem prawnym GPSR (UE 2023/988 art. 8).

BRAND: "{brand}"

ZADANIE: Znajdź SPECYFICZNEGO EU responsible person dla TEGO konkretnego brandu.

KRYTYCZNE ZASADY:
1. KAŻDY brand sprzedawany w UE MUSI mieć WŁASNEGO EU representative.
2. NIE wracaj generic "CET PRODUCT SERVICE SP. Z O.O." ani "Amazon Retourenkauf"
   jako default guess. To są EU reps SPECYFICZNE dla Amazon Renewed/Returns,
   NIE dla każdego brandu chińskiego.
3. Dla brandu "ELVIROS" → szukaj specific "ELVIROS EU GmbH" lub podobne.
   Dla "AZDOME" → "AZDOME GmbH" lub ich Amazon DE rejestracja.
   Dla "Canon" → "Canon Europa N.V." (Amsterdam, NL).
   Dla "Sony" → "Sony Europe B.V." (Surrey, UK lub NL).
4. Jeśli NIE ZNASZ specific EU rep dla tego brandu — zwróć name=null
   (skip — fallback handle sam). NIE zgaduj.

Zwróć WYŁĄCZNIE valid JSON (BEZ pola "notes" — nie potrzebne):
{{
  "name": "Pełna nazwa firmy EU rep (np. 'AZDOME GmbH') lub null jeśli nie wiesz",
  "address": "Pełen adres (ulica, miasto, kod, kraj) lub null",
  "email": "Kontakt mailowy EU rep lub null",
  "confidence": "high" (jesteś pewien) | "medium" (znany brand, rep prawdopodobny) | "low" (zgadujesz)
}}

WAŻNE:
- confidence=low + name=null → SKIP (lepsze niż błędny guess).
- NIE wracaj CET PRODUCT SERVICE / Amazon Retourenkauf chyba że WIESZ
  że ten brand formalnie używa tego rep'a (rzadko).
- NIE dodawaj długich tekstów ani pola "notes" — TYLKO 4 pola powyżej.
- Bez markdown, tylko czysty JSON."""

    client = genai.Client(api_key=api_key)
    # Model chain — od najnowszego/highest quota do legacy.
    # gemini-2.5-flash = current stable, dobry quota dla paid tier.
    # gemini-2.5-flash-lite = lighter, jeszcze tańszy.
    # gemini-1.5-flash-latest = legacy fallback.
    for model_name in ('gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.0-flash'):
        for attempt in range(3):
            try:
                # 2.5-flash thinking mode zjada tokeny — disable dla prostego lookup-u.
                config_kwargs = {
                    'temperature': 0.0,  # deterministic — brand → rep mapping, no creativity
                    'max_output_tokens': 4096,  # bump bo Gemini pisał eseje w "notes" → truncated JSON
                    'response_mime_type': 'application/json',
                }
                if '2.5' in model_name:
                    config_kwargs['thinking_config'] = types.ThinkingConfig(thinking_budget=0)
                config = types.GenerateContentConfig(**config_kwargs)
                resp = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config,
                )
                text = (resp.text or '').strip()
                if not text:
                    return {}  # empty response — try next model
                # Gemini czasem zwraca markdown wrap ```json ... ``` mimo response_mime_type
                # (znany bug 2.5-flash dla niszowych queries — strip & re-parse).
                cleaned = _clean_gemini_json(text)
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError as je:
                    logger.warning(
                        f'Gemini JSON parse fail brand={brand} model={model_name}: {je} | raw[0:300]={text[:300]!r}'
                    )
                    # Spróbuj wyciągnąć JSON object regex jako last resort
                    extracted = _extract_json_object(text)
                    if extracted:
                        try:
                            return json.loads(extracted)
                        except json.JSONDecodeError:
                            pass
                    return {}
            except Exception as e:
                err = str(e)
                # 429 Rate limit / quota — backoff retry
                if '429' in err or 'Resource exhausted' in err or 'quota' in err.lower():
                    if attempt < 2:
                        backoff = 2 ** attempt * 3  # 3s, 6s, 12s
                        logger.info(f'Gemini 429 brand={brand} model={model_name} — retry za {backoff}s ({attempt+1}/3)')
                        time.sleep(backoff)
                        continue
                    # Po 3 retry — try next model (może ma osobny quota)
                    logger.warning(f'Gemini 429 final brand={brand} model={model_name} — try next model')
                    break
                # 404 / model not found — try next model
                if '404' in err or 'NOT_FOUND' in err or 'not found' in err.lower():
                    logger.debug(f'Gemini model {model_name} not available for brand={brand}, try next')
                    break
                # Inne errors — log + return empty
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
        # REJECT generic CET/Amazon Retourenkauf guesses — Gemini leniwie wpisuje to
        # dla niszowych chińskich brandów, ale user chce SPECYFICZNEGO rep'a per brand.
        name_lower = data['name'].lower()
        if any(generic in name_lower for generic in ('cet product service', 'amazon retourenkauf', 'amazon returns')):
            print(f'  ⊘ [{i+1:>3}/{len(brands)}] {brand:<25} REJECT generic guess: "{data["name"][:30]}" → użyj manual lub fallback')
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
