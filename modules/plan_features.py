"""
System ograniczeń funkcji per plan licencyjny.
TRIAL < PRO < MAX < ENTERPRISE

Każdy plan odblokowuje kolejne moduły.
"""

# Hierarchia planów (wyższy = więcej funkcji)
PLAN_LEVEL = {
    'starter': 1,   # TRIAL
    'pro': 2,        # PRO
    'business': 3,   # MAX
    'enterprise': 4, # ENTERPRISE
}

# Display names
PLAN_DISPLAY = {
    'starter': 'TRIAL',
    'pro': 'PRO',
    'business': 'MAX',
    'enterprise': 'ENTERPRISE',
}

# Cennik (PLN)
PLAN_PRICING = {
    'starter': {'monthly': 0, 'yearly': 0, 'label': 'TRIAL 7 dni'},
    'pro': {'monthly': 149, 'yearly': 1490, 'label': '149 zł/mies'},
    'business': {'monthly': 299, 'yearly': 2990, 'label': '299 zł/mies'},
    'enterprise': {'monthly': 499, 'yearly': 0, 'label': 'indywidualnie'},
}

# Limity dla TRIAL (starter)
TRIAL_LIMITS = {
    'max_palety': 3,        # max 3 palety w systemie
    'max_sprzedaze': 20,    # max 20 sprzedaży ręcznych
    'max_produkty': 50,     # max 50 produktów
    'trial_days': 7,        # 7 dni trialu
}

# Minimalne plany dla poszczególnych funkcji/stron
# Wszystko co nie jest wymienione = dostępne od TRIAL
FEATURE_MIN_PLAN = {
    # === TRIAL (starter) - podstawy ===
    # Palety (max 3), produkty (max 50), sprzedaże ręczne (max 20), kalkulator marży
    # — domyślnie dostępne (z limitami)

    # === PRO - Allegro (read) + analityka + eksport ===
    '/allegro': 'pro',                           # Allegro OAuth (zamówienia, oferty)
    '/analytics/profit': 'pro',
    '/analytics/dashboard': 'pro',
    '/analytics/allegro-performance': 'pro',
    '/narzedzia/raporty': 'pro',
    '/narzedzia/analiza-oferty': 'pro',
    '/narzedzia/export': 'pro',
    '/narzedzia/cloud-export': 'pro',
    '/analityka/analizator-palet': 'pro',
    '/analytics/kalkulator-palety': 'pro',

    # === MAX (business) - automatyzacja + zaawansowane ===
    '/wysylki': 'business',                     # wysyłki/pakowanie/etykiety
    '/paletomat': 'business',                    # scraper Amazon + oferty
    '/telegram': 'business',                     # powiadomienia Telegram/WhatsApp
    '/narzedzia/generator': 'business',          # AI opisy
    '/palety/bulk-import': 'business',           # bulk import
    '/poziom': 'business',                       # gamifikacja
    '/bingo2026': 'business',

    # === MAX (business) - zaawansowane moduły ===
    '/serwis': 'business',
    '/warehouse': 'business',                    # 3D heatmapa

    # === ENTERPRISE - tylko admin (wewnętrzne) ===
    '/admin/subscriptions': 'enterprise',
    '/narzedzia/licencje': 'enterprise',
}

# Opis funkcji per plan (do wyświetlenia na stronie upgrade)
PLAN_FEATURES_DISPLAY = {
    'starter': [
        'Palety (max 3)',
        'Produkty (max 50)',
        'Sprzedaze reczne (max 20)',
        'Kalkulator marzy',
        'Dashboard podstawowy',
    ],
    'pro': [
        'Wszystko z TRIAL bez limitow',
        'Integracja Allegro (OAuth)',
        'Zamowienia i oferty Allegro',
        'Analityka i dashboard KPI',
        'Raporty ROI palet',
        'Export CSV/Excel/Google Sheets',
        'Analizator palet',
        'Analiza ofert',
    ],
    'business': [
        'Wszystko z PRO',
        'Wysylki i stacja pakowania',
        'Paletomat (scraper Amazon)',
        'AI generator opisow',
        'Telegram/WhatsApp powiadomienia',
        'Bulk import palet',
        'Drukarka etykiet (Niimbot/Vretti)',
        'Magazyn 3D + heatmapa',
        'Modul serwisowy',
        'Gamifikacja (poziomy, bingo)',
    ],
    'enterprise': [
        'Wszystko z MAX',
        'Zarzadzanie licencjami (admin)',
        'Panel admin subskrypcji',
    ],
}


def get_current_plan():
    """Pobierz aktualny plan z licencji. Zwraca surowy plan (starter/pro/business/enterprise)."""
    try:
        from modules.license import check_license
        is_valid, plan, msg = check_license()
        if is_valid and plan:
            return plan.lower()
    except Exception:
        pass
    return 'starter'  # domyślny


def get_plan_level(plan=None):
    """Zwróć numeryczny level planu."""
    if plan is None:
        plan = get_current_plan()
    return PLAN_LEVEL.get(plan.lower(), 1)


def is_trial():
    """Czy aktualny plan to trial/starter?"""
    return get_current_plan() == 'starter'


def has_feature_access(path, plan=None):
    """Sprawdź czy dany plan ma dostęp do ścieżki."""
    if plan is None:
        plan = get_current_plan()
    user_level = get_plan_level(plan)

    # Szukaj najdłuższego pasującego prefiksu
    best_match = ''
    required_plan = None
    for feature_path, min_plan in FEATURE_MIN_PLAN.items():
        if path.startswith(feature_path) and len(feature_path) > len(best_match):
            best_match = feature_path
            required_plan = min_plan

    if required_plan is None:
        return True  # nie wymienione = dostępne dla wszystkich

    required_level = PLAN_LEVEL.get(required_plan, 1)
    return user_level >= required_level


def check_trial_limit(resource_type, current_count):
    """
    Sprawdź limit triala. Zwraca (allowed, limit, message).
    resource_type: 'palety', 'sprzedaze', 'produkty'
    """
    if not is_trial():
        return True, None, None

    limit_key = f'max_{resource_type}'
    limit = TRIAL_LIMITS.get(limit_key)
    if limit is None:
        return True, None, None

    if current_count >= limit:
        plan_needed = 'PRO' if resource_type in ('palety', 'sprzedaze', 'produkty') else 'MAX'
        return False, limit, f'Limit TRIAL: max {limit} {resource_type}. Przejdź na plan {plan_needed} aby odblokować.'

    return True, limit, None


def get_required_plan_display(path):
    """Zwróć display name wymaganego planu dla ścieżki."""
    best_match = ''
    required_plan = None
    for feature_path, min_plan in FEATURE_MIN_PLAN.items():
        if path.startswith(feature_path) and len(feature_path) > len(best_match):
            best_match = feature_path
            required_plan = min_plan
    if required_plan:
        return PLAN_DISPLAY.get(required_plan, required_plan.upper())
    return None


def get_features_for_plan(plan):
    """Zwróć listę zablokowanych funkcji dla danego planu."""
    user_level = PLAN_LEVEL.get(plan.lower(), 1)
    blocked = []
    for path, min_plan in FEATURE_MIN_PLAN.items():
        req_level = PLAN_LEVEL.get(min_plan, 1)
        if user_level < req_level:
            blocked.append({
                'path': path,
                'required_plan': PLAN_DISPLAY.get(min_plan, min_plan.upper()),
            })
    return blocked
