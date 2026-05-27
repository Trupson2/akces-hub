"""
Kursy walut z NBP API (Narodowy Bank Polski).
Live rates, cache 12h w configu DB (oszczednosc API calls).

API NBP: http://api.nbp.pl/api/exchangerates/tables/A?format=json
Bez autoryzacji, bez limitow, publiczne, darmowe, oficjalne.

Wspierane waluty: EUR, USD, GBP, CZK, HUF, CHF, SEK, NOK, DKK + 30 innych.
PLN = 1.0 zawsze (baza).
"""
import json
import time
from typing import Dict

# Fallback rates (jak NBP API nie dziala / brak internetu)
FALLBACK_RATES = {
    'PLN': 1.0,
    'EUR': 4.30,
    'USD': 3.95,
    'GBP': 5.05,
    'CZK': 0.175,
    'HUF': 0.011,
    'CHF': 4.50,
    'SEK': 0.39,
    'NOK': 0.37,
    'DKK': 0.58,
}

CACHE_TTL_SECONDS = 12 * 3600  # 12 godzin


def _fetch_nbp_rates() -> Dict[str, float]:
    """Pobierz aktualne kursy z NBP API. Zwroca dict {currency: pln_rate}."""
    import requests
    try:
        r = requests.get(
            'http://api.nbp.pl/api/exchangerates/tables/A?format=json',
            timeout=10,
            headers={'Accept': 'application/json'}
        )
        r.raise_for_status()
        data = r.json()
        if not data or not data[0].get('rates'):
            return {}
        rates = {'PLN': 1.0}
        for rate in data[0]['rates']:
            code = rate.get('code', '').upper()
            mid = float(rate.get('mid', 0))
            if code and mid > 0:
                rates[code] = mid
        return rates
    except Exception as e:
        print(f'[fx_rates] NBP fetch failed: {e}')
        return {}


def get_all_rates() -> Dict[str, float]:
    """Zwroc wszystkie kursy (cache 12h). Live z NBP albo fallback hardcoded."""
    try:
        from modules.database import get_config, set_config
        cache_raw = get_config('fx_rates_cache', '')
        cache = json.loads(cache_raw) if cache_raw else {}
        cache_age = time.time() - cache.get('ts', 0)
        if cache.get('rates') and cache_age < CACHE_TTL_SECONDS:
            return cache['rates']

        # Cache miss - pobierz z NBP
        fresh = _fetch_nbp_rates()
        if fresh:
            new_cache = {'ts': time.time(), 'rates': fresh, 'source': 'nbp'}
            set_config('fx_rates_cache', json.dumps(new_cache))
            print(f'[fx_rates] NBP rates fetched: EUR={fresh.get("EUR")}, CZK={fresh.get("CZK")}, HUF={fresh.get("HUF")}')
            return fresh
    except Exception as e:
        print(f'[fx_rates] cache error: {e}')

    # Fallback
    return FALLBACK_RATES


def get_pln_rate(currency: str) -> float:
    """Zwroc kurs <currency> -> PLN.

    Args:
        currency: kod waluty np. 'EUR', 'CZK', 'HUF'
    Returns:
        float: 1 unit of currency = X PLN
        Jesli waluta nieznana -> 1.0 (zachowawcze)
    """
    if not currency:
        return 1.0
    code = currency.upper()
    rates = get_all_rates()
    return rates.get(code, FALLBACK_RATES.get(code, 1.0))


def force_refresh():
    """Wymus refresh cache (np. po user action 'Odswiez kursy')."""
    try:
        from modules.database import set_config
        set_config('fx_rates_cache', '')
    except Exception:
        pass
    return get_all_rates()
