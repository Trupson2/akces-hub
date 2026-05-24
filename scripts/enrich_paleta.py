#!/usr/bin/env python3
"""
enrich_paleta.py — auto-enrich produktów palety przez Gemini AI.

Cel: dla każdego produktu palety wywołać Gemini i:
  1. Wyciągnąć REAL brand (np. "Canon", "Sony", "JJC") z nazwy — NIE dostawca palety
  2. Wyciągnąć model, color, wymiary, waga, materiał — do parameters JSON
  3. Wygenerować pełny opis HTML do opis_ai

Update produkty.opis_ai + produkty.parameters w Hub DB.

Następny push (--force) wciągnie zaktualizowane dane → Specyfikacja tab i
"Marka" pole będą pokazywać prawdziwą markę zamiast nazwy palety.

Usage:
    # Enrich 1 paletę
    python3 scripts/enrich_paleta.py --paleta-id 5

    # Enrich N losowych produktów (test):
    python3 scripts/enrich_paleta.py --limit 10

    # Enrich wszystkie produkty bez opis_ai (status=magazyn):
    python3 scripts/enrich_paleta.py --all-empty

    # Force re-enrich (nadpisz istniejący opis_ai/parameters):
    python3 scripts/enrich_paleta.py --paleta-id 5 --force

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

# Suppress noisy SDK logs (HTTP per call + AFC zaciemnia output enrichu).
for _noisy in ('google_genai', 'google.genai', 'httpx', 'httpcore', 'urllib3'):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

from modules.database import get_db, get_config  # noqa: E402

THROTTLE_SEC = 2.0  # Gemini API quota — 2s/req safe (free tier 60/min)


def _clean_gemini_json(text: str) -> str:
    """Strip Gemini's common formatting bugs (markdown fences, BOM)."""
    if not text:
        return text
    s = text.strip()
    if s.startswith('```'):
        first_newline = s.find('\n')
        if first_newline > 0:
            s = s[first_newline + 1:]
        if s.endswith('```'):
            s = s[:-3].rstrip()
    if s.startswith('﻿'):
        s = s[1:]
    return s.strip()


def _extract_json_object(text: str) -> str:
    """Fallback: wyciągnij pierwszy {...} object z tekstu."""
    if not text:
        return ''
    m = re.search(r'\{.*\}', text, re.DOTALL)
    return m.group(0) if m else ''


def _gemini_call(prompt: str, api_key: str, max_tokens: int = 800) -> str:
    """Call Gemini API z prompt. Returns text response lub '' on fail.

    Próbuje multiple model names (Google zmienia versions, fallback chain).
    """
    # Nowy SDK google.genai (google-generativeai deprecated od 2025).
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.error('google-genai SDK not installed. Run: pip3 install --break-system-packages google-genai')
        return ''

    client = genai.Client(api_key=api_key)
    for model_name in ('gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.0-flash'):
        for attempt in range(3):
            try:
                # 2.5-flash: thinking + output to OSOBNE budżety w nowym SDK.
                # thinking=0 powoduje że Gemini nie reasonuje → częste "unknown".
                # Dajemy 1024 na thinking (wystarczy dla brand extraction) + bump output.
                config_kwargs = {
                    'temperature': 0.2,
                    'max_output_tokens': max(max_tokens, 4096),  # response body
                    'response_mime_type': 'application/json',
                }
                if '2.5' in model_name:
                    config_kwargs['thinking_config'] = types.ThinkingConfig(thinking_budget=1024)
                config = types.GenerateContentConfig(**config_kwargs)
                resp = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config,
                )
                return _clean_gemini_json(resp.text or '')
            except Exception as e:
                err = str(e)
                if '429' in err or 'Resource exhausted' in err or 'quota' in err.lower():
                    if attempt < 2:
                        backoff = 2 ** attempt * 3
                        logger.info(f'Gemini 429 model={model_name} — retry za {backoff}s ({attempt+1}/3)')
                        time.sleep(backoff)
                        continue
                    break  # try next model
                if '404' in err or 'NOT_FOUND' in err or 'not found' in err.lower():
                    break  # try next model
                logger.warning(f'Gemini call failed ({model_name}): {e}')
                return ''
    logger.error('All Gemini models failed — check API key + quota at https://aistudio.google.com/')
    return ''


def _enrich_prompt(nazwa: str, kategoria: str = '') -> str:
    """Build Gemini prompt — wyciągnij brand/model/specs + opis HTML."""
    return f"""Jesteś expertem od katalogowania produktów e-commerce.

PRODUKT: "{nazwa}"
KATEGORIA: {kategoria or 'nieznana'}

Wyciągnij z nazwy następujące dane:
- "brand": PRAWDZIWA marka producenta (np. "Canon", "Sony", "JJC", "Bosch"). NIE pisz nazw palet/dostawców (Warrington, Jobalots, Amazon DE, B-Stock). Jeśli nie da się zidentyfikować, zwróć "" (pusty string).
- "model": numer/nazwa modelu (np. "EOS R6", "WH-1000XM4")
- "color": kolor (po polsku, np. "czarny", "biały")
- "wymiary": jeśli wspomniane (np. "30x40cm")
- "waga": jeśli wspomniana
- "materiał": jeśli wspomniany
- "opis_html": krótki opis HTML 2-4 zdań (po polsku), używaj <p> i <strong>. NIE wymyślaj cech których nie ma w nazwie. Skup się na faktach z nazwy + ogólnej kategorii.

Zwróć WYŁĄCZNIE valid JSON o strukturze:
{{
  "brand": "string lub pusty",
  "model": "string lub pusty",
  "color": "string lub pusty",
  "wymiary": "string lub pusty",
  "waga": "string lub pusty",
  "materiał": "string lub pusty",
  "opis_html": "<p>HTML opis...</p>"
}}

NIE dodawaj komentarzy ani markdown — tylko JSON."""


def _enrich_one(produkt: dict, api_key: str, force: bool = False) -> dict:
    """Enrich 1 produkt. Returns dict z wynikiem (success/skip/error)."""
    pid = produkt['id']
    nazwa = (produkt.get('krotki_tytul') or produkt.get('nazwa') or '').strip()
    kategoria = (produkt.get('kategoria') or '').strip()

    if not nazwa:
        return {'status': 'skip', 'id': pid, 'msg': 'brak nazwy'}

    # Skip gdy juz ma opis_ai I parameters z brand (force=False)
    if not force:
        existing_opis = (produkt.get('opis_ai') or '').strip()
        existing_params = produkt.get('parameters') or ''
        if existing_opis and existing_params:
            try:
                p = json.loads(existing_params)
                if p.get('brand'):
                    return {'status': 'skip', 'id': pid, 'msg': 'already enriched (use --force aby nadpisać)'}
            except (json.JSONDecodeError, TypeError):
                pass

    prompt = _enrich_prompt(nazwa, kategoria)
    raw = _gemini_call(prompt, api_key)
    if not raw:
        return {'status': 'error', 'id': pid, 'msg': 'Gemini fail (no response)'}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        # Last resort — regex wyciągnij {...} z tekstu (Gemini doda komentarz przed/po)
        extracted = _extract_json_object(raw)
        if extracted:
            try:
                data = json.loads(extracted)
            except json.JSONDecodeError:
                return {'status': 'error', 'id': pid, 'msg': f'JSON parse fail: {e}', 'raw': raw[:200]}
        else:
            return {'status': 'error', 'id': pid, 'msg': f'JSON parse fail: {e}', 'raw': raw[:200]}

    if not isinstance(data, dict):
        return {'status': 'error', 'id': pid, 'msg': f'Gemini zwrocil non-dict: {type(data).__name__}'}

    opis_html = (data.pop('opis_html', '') or '').strip()
    # Reszta jako parameters JSON
    params = {k: v for k, v in data.items() if v and str(v).strip()}

    # Update DB
    conn = get_db()
    conn.execute(
        'UPDATE produkty SET opis_ai = ?, parameters = ? WHERE id = ?',
        (opis_html, json.dumps(params, ensure_ascii=False), pid),
    )
    conn.commit()

    return {
        'status': 'ok',
        'id': pid,
        'brand': params.get('brand', ''),
        'model': params.get('model', ''),
        'opis_len': len(opis_html),
        'params_keys': list(params.keys()),
    }


def cmd_paleta(paleta_id: int, force: bool, api_key: str) -> int:
    conn = get_db()
    rows = conn.execute(
        'SELECT id, nazwa, krotki_tytul, kategoria, opis_ai, parameters FROM produkty WHERE paleta_id = ? AND status = ?',
        (paleta_id, 'magazyn'),
    ).fetchall()
    if not rows:
        print(f'Brak produktów dla paleta_id={paleta_id} (status=magazyn).')
        return 1
    print(f'Enrich {len(rows)} produktów z paleta_id={paleta_id} (force={force}, throttle={THROTTLE_SEC}s/req)...\n')
    return _process_batch([dict(r) for r in rows], force, api_key)


def cmd_all_empty(force: bool, api_key: str, limit: int) -> int:
    conn = get_db()
    sql = '''
        SELECT id, nazwa, krotki_tytul, kategoria, opis_ai, parameters
        FROM produkty
        WHERE status = 'magazyn'
          AND (opis_ai IS NULL OR opis_ai = '' OR parameters IS NULL OR parameters = '')
        ORDER BY id
    '''
    if limit > 0:
        sql += f' LIMIT {int(limit)}'
    rows = conn.execute(sql).fetchall()
    if not rows:
        print('Brak produktów do enrichment (wszystkie maja opis_ai + parameters).')
        return 0
    print(f'Enrich {len(rows)} produktów BEZ opis_ai/parameters (throttle {THROTTLE_SEC}s/req)...\n')
    return _process_batch([dict(r) for r in rows], force, api_key)


def cmd_limit(limit: int, force: bool, api_key: str) -> int:
    conn = get_db()
    rows = conn.execute(
        'SELECT id, nazwa, krotki_tytul, kategoria, opis_ai, parameters FROM produkty WHERE status = ? ORDER BY id LIMIT ?',
        ('magazyn', int(limit)),
    ).fetchall()
    print(f'Enrich {len(rows)} produktów (LIMIT {limit}, force={force})...\n')
    return _process_batch([dict(r) for r in rows], force, api_key)


def _process_batch(rows: list, force: bool, api_key: str) -> int:
    ok = skip = err = 0
    for i, row in enumerate(rows):
        if i > 0:
            time.sleep(THROTTLE_SEC)
        result = _enrich_one(row, api_key, force=force)
        status = result.get('status')
        marker = {'ok': '✅', 'skip': '⊘', 'error': '❌'}.get(status, '?')
        nazwa_short = (row.get('krotki_tytul') or row.get('nazwa') or '')[:55]
        if status == 'ok':
            print(f'  {marker} id={result["id"]:>5} brand="{result["brand"][:20]}" model="{result["model"][:20]}" opis={result["opis_len"]}b  {nazwa_short}')
            ok += 1
        elif status == 'skip':
            print(f'  {marker} id={result["id"]:>5} {result["msg"]:<50}  {nazwa_short}')
            skip += 1
        else:
            print(f'  {marker} id={result["id"]:>5} ERROR: {result["msg"]:<50}  {nazwa_short}')
            err += 1
    print(f'\nResults: ok={ok}  skip={skip}  error={err}  total={len(rows)}')
    return 0 if err == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Enrich produkty Hub przez Gemini AI — extract brand/model/specs + generuje opis_ai',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--paleta-id', type=int, metavar='ID', help='Enrich wszystkie produkty z palety o tym ID')
    group.add_argument('--all-empty', action='store_true', help='Enrich wszystkie produkty bez opis_ai lub parameters (status=magazyn)')
    group.add_argument('--limit', type=int, metavar='N', help='Enrich pierwsze N produktów (test)')
    parser.add_argument('--force', action='store_true', help='Wymuś re-enrichment (nadpisz istniejący opis_ai/parameters)')
    args = parser.parse_args()

    api_key = get_config('gemini_api_key', '') or os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        print('ERROR: brak GEMINI_API_KEY. Ustaw config:')
        print('  python3 -c "from modules.database import set_config; set_config(\'gemini_api_key\', \'YOUR_KEY\')"')
        print('  lub export GEMINI_API_KEY=...')
        return 2

    if args.paleta_id is not None:
        return cmd_paleta(args.paleta_id, args.force, api_key)
    if args.all_empty:
        return cmd_all_empty(args.force, api_key, limit=args.limit or 0)
    if args.limit:
        return cmd_limit(args.limit, args.force, api_key)
    return 1


if __name__ == '__main__':
    sys.exit(main())
