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

# Minimalne plany dla poszczególnych funkcji/stron
# Wszystko co nie jest wymienione = dostępne od TRIAL
FEATURE_MIN_PLAN = {
    # === TRIAL (starter) - podstawy ===
    # Palety, produkty, sprzedaże ręczne, kalkulator marży
    # — domyślnie dostępne

    # === PRO - analityka + eksport ===
    '/analytics/profit': 'pro',
    '/analytics/dashboard': 'pro',
    '/analytics/allegro-performance': 'pro',
    '/narzedzia/raporty': 'pro',
    '/narzedzia/analiza-oferty': 'pro',
    '/narzedzia/export': 'pro',
    '/narzedzia/cloud-export': 'pro',
    '/analityka/analizator-palet': 'pro',
    '/analytics/kalkulator-palety': 'pro',

    # === MAX (business) - integracje + automatyzacja ===
    '/allegro': 'business',
    '/telegram': 'business',
    '/narzedzia/generator': 'business',      # AI opisy
    '/palety/bulk-import': 'business',        # bulk import
    '/poziom': 'business',                    # gamifikacja
    '/bingo2026': 'business',

    # === ENTERPRISE - wszystko ===
    '/admin/subscriptions': 'enterprise',
    '/narzedzia/licencje': 'enterprise',
    '/serwis': 'enterprise',
    '/warehouse': 'enterprise',               # 3D heatmapa
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
