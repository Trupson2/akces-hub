# -*- coding: utf-8 -*-
"""
Winning Products — niche-based scoring system.
Czysta logika, zero dostępu do DB.

Exports: compute_scores, generate_notes, detect_niche,
         score_trend, score_competition, score_margin, score_fit, score_return
"""

import logging
import math
import statistics
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS: dict[str, float] = {
    "trend":       0.20,
    "competition": 0.15,
    "margin":      0.35,
    "fit":         0.20,
    "return":      0.10,
}

DEFAULT_THRESHOLDS: dict[str, Any] = {
    # margin thresholds
    "margin_excellent": 0.35,
    "margin_good":      0.25,
    "margin_ok":        0.15,
    # volume thresholds per niche
    "pet_volume_high":          300,
    "pet_volume_med":           100,
    "home_office_volume_high":  500,
    "home_office_volume_med":   200,
    "home_volume_high":         400,
    "home_volume_med":          150,
    "auto_volume_high":         200,
    "auto_volume_med":          80,
    "comfort_volume_high":      300,
    "comfort_volume_med":       100,
    # costs
    "allegro_commission":   0.08,
    "shipping_cost_pln":    12.0,
    "packaging_cost_pln":   2.0,
    # assumed cost ratio by niche
    "cost_ratio_pet":         0.45,
    "cost_ratio_home_office": 0.40,
    "cost_ratio_home":        0.38,
    "cost_ratio_comfort":     0.42,
    "cost_ratio_auto":        0.48,
}

# Keywords for niche detection (lowercase, Polish + common EN)
NICHE_KEYWORDS: dict[str, list[str]] = {
    "pet": [
        "zwierzęt", "zwierząt", "pet", "kot", "pies", "akwarium",
        "karma", "kuwet", "legowisko", "smycz", "zabawka dla psa",
        "fontanna dla kota", "klatka", "terrarium",
    ],
    "home_office": [
        "statyw", "lampa ring", "ring light", "mikrofon", "uchwyt",
        "monitor", "foto", "video", "studio", "kamera", "nagryw",
        "streaming", "podkast", "oświetlenie led foto",
    ],
    "home": [
        "dom", "ogród", "kuchni", "przechowywanie", "organiz",
        "oświetlenie", "łazienka", "garden", "pojemnik", "półka",
        "wieszak", "suszark", "zlew", "dekor",
    ],
    "comfort": [
        "poduszka", "ergonomia", "masaż", "rehabilitacja", "fitness",
        "mata", "wałek", "zdrowie", "ortopedyczn", "fotel",
        "lumbar", "cervical", "roller",
    ],
    "auto": [
        "samochód", "auto", "motor", "garaż", "narzędzia",
        "chemia samochodow", "warsztat", "rower", "felga", "opona",
        "płyn", "akcesoria samochodow", "uchwyt samochodow",
    ],
}

# Electronics keywords — used in return_score detection
ELECTRONICS_KEYWORDS: list[str] = [
    "wifi", "usb", "bluetooth", "ładow", "akumulator",
    "bateria", "pilot", "sensor", "czujnik", "smart",
    "automatyczny", "automat", "fontanna",
]

# Simple-goods keywords — low-return products
SIMPLE_GOODS_KEYWORDS: list[str] = [
    "organiz", "pojemnik", "pokrowiec", "torba", "mata",
    "miska", "kuweta", "statyw", "uchwyt", "stojak",
    "wieszak", "suszark",
]


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _safe_float(value: Any, default: float = 0.0) -> float:
    """Safely cast *value* to float, return *default* on failure."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    """Safely cast *value* to int, return *default* on failure."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _linscale(value: float, lo: float, hi: float, score_lo: float, score_hi: float) -> float:
    """Linearly interpolate *value* from [lo, hi] → [score_lo, score_hi]."""
    if hi <= lo:
        return score_lo
    t = (value - lo) / (hi - lo)
    return score_lo + t * (score_hi - score_lo)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(hi, value))


def _name_lower(product_data: dict) -> str:
    """Return lowercase product name from product_data dict."""
    name = product_data.get("name") or product_data.get("title") or ""
    return str(name).lower()


def _has_keyword(text: str, keywords: list[str]) -> bool:
    """Return True if *text* contains any keyword from *keywords*."""
    tl = text.lower()
    return any(kw in tl for kw in keywords)


def _th(config: dict, key: str) -> Any:
    """Get threshold from config['thresholds'], fall back to DEFAULT_THRESHOLDS."""
    return config.get("thresholds", {}).get(key, DEFAULT_THRESHOLDS.get(key))


# ---------------------------------------------------------------------------
# Niche detection
# ---------------------------------------------------------------------------

def detect_niche(product_data: dict) -> str:
    """
    Detect which of the 5 niches a product belongs to.

    Detection is based on the product name and Allegro category name/path
    present in *product_data*.  Returns one of:
    "pet", "home_office", "home", "comfort", "auto".
    Defaults to "home" when no match is found.

    Args:
        product_data: Dict with at least one of: name, title, category (str or dict).

    Returns:
        Niche string.
    """
    # Build a single text blob from name + category info
    parts: list[str] = []
    for key in ("name", "title"):
        v = product_data.get(key)
        if v:
            parts.append(str(v))

    category = product_data.get("category")
    if isinstance(category, dict):
        # Allegro API returns {"id": "...", "name": "...", "path": "..."}
        for ck in ("name", "path"):
            v = category.get(ck)
            if v:
                parts.append(str(v))
    elif isinstance(category, str):
        parts.append(category)

    text = " ".join(parts).lower()

    if not text:
        return "home"

    # Match against niche keyword lists; return the first niche that matches
    # Priority order: more specific niches first
    priority_order = ["pet", "home_office", "comfort", "auto", "home"]
    for niche in priority_order:
        if _has_keyword(text, NICHE_KEYWORDS[niche]):
            return niche

    return "home"


# ---------------------------------------------------------------------------
# Individual score functions
# ---------------------------------------------------------------------------

def score_trend(
    product_data: dict,
    niche: str,
    config: dict,
) -> tuple[float, str]:
    """
    Calculate trend score (0.0–1.0).

    Inputs read from *product_data*:
        watchers_count      – number of watchers (proxy for interest)
        views_count         – number of page views
        monthly_sales_est   – estimated monthly unit sales (if available)
        category_rank       – rank in category (lower = better; optional)

    Args:
        product_data: Product dict from Allegro or local DB.
        niche: Detected niche string.
        config: Config dict (thresholds sub-key used).

    Returns:
        Tuple of (score 0.0–1.0, Polish explanation string).
    """
    try:
        watchers = _safe_int(product_data.get("watchers_count") or product_data.get("watchersCount"), 0)
        views    = _safe_int(product_data.get("views_count")    or product_data.get("viewsCount"),    0)
        sales_est = product_data.get("monthly_sales_est")

        # --- Volume proxy ---
        if sales_est is not None:
            volume = _safe_float(sales_est, 0.0)
            volume_source = "sprzedaż"
        else:
            volume = watchers * 3.0  # rough watchers→sales estimate
            volume_source = "obserwujący×3"

        # --- Niche-specific thresholds and band mapping ---
        if niche in ("pet", "comfort"):
            hi  = _safe_float(_th(config, "pet_volume_high"),  300)
            med = _safe_float(_th(config, "pet_volume_med"),   100)
        elif niche == "home_office":
            hi  = _safe_float(_th(config, "home_office_volume_high"), 500)
            med = _safe_float(_th(config, "home_office_volume_med"),  200)
        elif niche == "home":
            hi  = _safe_float(_th(config, "home_volume_high"), 400)
            med = _safe_float(_th(config, "home_volume_med"),  150)
        else:  # auto
            hi  = _safe_float(_th(config, "auto_volume_high"), 200)
            med = _safe_float(_th(config, "auto_volume_med"),   80)

        lo = med / 3.0  # lower threshold = ~1/3 of medium

        if volume >= hi:
            base = 1.0
        elif volume >= med:
            base = _linscale(volume, med, hi, 0.7, 1.0)
        elif volume >= lo:
            base = _linscale(volume, lo, med, 0.4, 0.7)
        else:
            base = _linscale(volume, 0, lo, 0.1, 0.4)

        # --- Views boost ---
        views_boost = 0.05 if views > 10_000 else 0.0

        score = _clamp(base + views_boost)

        # Explanation
        vol_int = int(volume)
        if volume_source == "sprzedaż":
            trend_desc = f"Est. {vol_int} szt/mies."
        else:
            trend_desc = f"~{vol_int} szt/mies. (proxy z {watchers} obserwujących)"

        if score >= 0.8:
            label = "Wysoki popyt"
        elif score >= 0.55:
            label = "Umiarkowany popyt"
        elif score >= 0.35:
            label = "Niski popyt"
        else:
            label = "Bardzo niski popyt"

        explanation = f"{label} ({trend_desc})"
        if views_boost:
            explanation += f" +boost (>{views:,} wyświetleń)"

        return round(score, 4), explanation

    except Exception as e:
        logger.error(f"[scoring] score_trend error: {e}", exc_info=True)
        return 0.3, "Brak danych (błąd trendu)"


def score_competition(
    product_data: dict,
    niche: str,
    market_data: dict,
    config: dict,
) -> tuple[float, str]:
    """
    Calculate competition score (0.0–1.0, higher = easier to compete).

    Inputs:
        product_data: name/title of the product being analysed.
        market_data:
            similar_offers_count  – total offers in category/subcategory (int)
            price_list            – list of competitor prices (floats, PLN)
            smart_sellers_fraction – fraction of Smart sellers (0.0–1.0)

    Args:
        product_data: Product dict.
        niche: Detected niche.
        market_data: Market-level data dict.
        config: Config dict.

    Returns:
        Tuple of (score 0.0–1.0, Polish explanation string).
    """
    try:
        n_offers       = _safe_int(market_data.get("similar_offers_count"), 0)
        price_list     = [_safe_float(p) for p in (market_data.get("price_list") or []) if p]
        smart_fraction = _safe_float(market_data.get("smart_sellers_fraction"), 0.0)
        name_lower     = _name_lower(product_data)

        # --- Base: inverse log of offer count ---
        base = 1.0 / (1.0 + math.log10(max(1, n_offers)))

        # --- Price war penalty ---
        price_war_penalty = 0.0
        if len(price_list) >= 3:
            mean_p = statistics.mean(price_list)
            std_p  = statistics.stdev(price_list)
            if mean_p > 0 and (std_p / mean_p) < 0.10:
                price_war_penalty = 0.15  # tight pricing = war

        # --- Smart penalty ---
        smart_penalty = _clamp(smart_fraction, 0.0, 1.0) * 0.20

        score = base - price_war_penalty - smart_penalty

        # --- Niche adjustments ---
        niche_adj = 0.0
        niche_note = ""

        if niche == "home_office" and n_offers > 500:
            # Extra penalty if top offers have many reviews (saturated)
            top_reviews = _safe_int(market_data.get("top_reviews_count"), 0)
            if top_reviews >= 1000:
                niche_adj -= 0.15
                niche_note = "Wysoka nasycona nisza (1000+ opinii)"

        elif niche == "pet":
            niche_features = ["filter", "stainless", "inox", "wifi", "nierdzewn"]
            if _has_keyword(name_lower, niche_features):
                niche_adj += 0.15
                niche_note = "Wyróżnik niszowy (filtr/stal/wifi)"

        elif niche == "auto":
            pl_signals = ["własna marka", "private label", "own brand", "oem"]
            if _has_keyword(name_lower, pl_signals):
                niche_adj += 0.10
                niche_note = "Sygnał własnej marki"

        score = _clamp(score + niche_adj)

        # Explanation
        smart_pct = int(smart_fraction * 100)
        if n_offers == 0:
            comp_desc = "Brak danych o liczbie ofert"
        elif n_offers < 50:
            comp_desc = f"Mała konkurencja ({n_offers} ofert, {smart_pct}% Smart)"
        elif n_offers < 300:
            comp_desc = f"Średnia konkurencja ({n_offers} ofert, {smart_pct}% Smart)"
        else:
            comp_desc = f"Duża konkurencja ({n_offers} ofert, {smart_pct}% Smart)"

        if price_war_penalty:
            comp_desc += ", ceny bardzo zbliżone (wojna cenowa)"
        if niche_note:
            comp_desc += f". {niche_note}"

        return round(score, 4), comp_desc

    except Exception as e:
        logger.error(f"[scoring] score_competition error: {e}", exc_info=True)
        return 0.4, "Brak danych (błąd konkurencji)"


def score_margin(
    product_data: dict,
    niche: str,
    config: dict,
) -> tuple[float, str]:
    """
    Calculate margin score (0.0–1.0).

    Inputs read from *product_data*:
        price / est_price / sellingMode.price.amount  – Allegro selling price (PLN)
        purchase_cost_estimate                        – override cost (optional)

    Thresholds and cost ratios are read from *config['thresholds']*.

    Args:
        product_data: Product dict.
        niche: Detected niche.
        config: Config dict.

    Returns:
        Tuple of (score 0.0–1.0, Polish explanation string).
    """
    try:
        # Resolve selling price
        est_price = _safe_float(
            product_data.get("price")
            or product_data.get("est_price")
            or product_data.get("sellingMode", {}).get("price", {}).get("amount"),
            0.0,
        )
        if est_price <= 0:
            return 0.0, "Brak ceny produktu"

        # Cost ratios by niche
        cost_ratio_key = f"cost_ratio_{niche}"
        cost_ratio = _safe_float(_th(config, cost_ratio_key), DEFAULT_THRESHOLDS.get(cost_ratio_key, 0.40))

        # Costs
        commission_rate  = _safe_float(_th(config, "allegro_commission"),  0.08)
        shipping_cost    = _safe_float(_th(config, "shipping_cost_pln"),   12.0)
        packaging_cost   = _safe_float(_th(config, "packaging_cost_pln"),   2.0)

        # Purchase cost
        purchase_cost = _safe_float(product_data.get("purchase_cost_estimate"), 0.0)
        if purchase_cost <= 0:
            purchase_cost = est_price * cost_ratio

        allegro_commission = est_price * commission_rate

        margin = (
            est_price - purchase_cost - shipping_cost - packaging_cost - allegro_commission
        ) / est_price

        # Mapping
        m_exc  = _safe_float(_th(config, "margin_excellent"), 0.35)
        m_good = _safe_float(_th(config, "margin_good"),      0.25)
        m_ok   = _safe_float(_th(config, "margin_ok"),        0.15)

        if margin >= m_exc:
            score = 1.0
        elif margin >= m_good:
            score = _linscale(margin, m_good, m_exc, 0.7, 1.0)
        elif margin >= m_ok:
            score = _linscale(margin, m_ok, m_good, 0.4, 0.7)
        else:
            score = _linscale(margin, 0.0, m_ok, 0.0, 0.4)

        # Niche bonus: home_office set/zestaw
        bonus = 0.0
        name_lower = _name_lower(product_data)
        if niche == "home_office" and "zestaw" in name_lower:
            bonus = 0.10

        score = _clamp(score + bonus)

        # Explanation
        margin_pct = margin * 100
        if score >= 0.75:
            label = "Dobra marża"
        elif score >= 0.45:
            label = "Akceptowalna marża"
        else:
            label = "Niska marża"

        explanation = (
            f"{label} (~{margin_pct:.1f}% | "
            f"cena {est_price:.0f} zł, koszt ~{purchase_cost:.0f} zł, "
            f"wysyłka {shipping_cost:.0f} zł, prowizja {allegro_commission:.0f} zł)"
        )
        if bonus:
            explanation += " +bonus zestaw"

        return round(score, 4), explanation

    except Exception as e:
        logger.error(f"[scoring] score_margin error: {e}", exc_info=True)
        return 0.3, "Brak danych (błąd marży)"


def score_fit(
    product_data: dict,
    niche: str,
    my_data: dict,
    config: dict,
) -> tuple[float, str]:
    """
    Calculate fit score — how well the product fits user's warehouse/experience.

    Inputs:
        product_data:
            name / title      – product name
            price / est_price – selling price
        my_data:
            my_categories     – list of category strings user currently sells
            avg_price_range   – tuple (min, max) of user's typical price range
        config: Config dict (unused beyond defaults, provided for uniformity).

    Args:
        product_data: Product dict.
        niche: Detected niche.
        my_data: User warehouse/portfolio data dict.
        config: Config dict.

    Returns:
        Tuple of (score 0.0–1.0, Polish explanation string).
    """
    try:
        name_lower     = _name_lower(product_data)
        est_price      = _safe_float(
            product_data.get("price") or product_data.get("est_price"), 0.0
        )
        my_categories: list[str] = [
            str(c).lower() for c in (my_data.get("my_categories") or [])
        ]

        score = 0.0
        reasons: list[str] = []

        # ---- Universal rules ----
        if est_price > 0 and est_price <= 40:
            score += 0.20
            reasons.append("paczkomatowy (<40 zł)")

        if est_price > 0 and 20 <= est_price <= 200:
            score += 0.15
            reasons.append("sweet spot cenowy (20–200 zł)")

        # User already sells in this niche
        niche_kws = NICHE_KEYWORDS.get(niche, [])
        user_in_niche = any(
            any(kw in cat for kw in niche_kws)
            for cat in my_categories
        )
        if user_in_niche:
            score += 0.20
            reasons.append(f"masz już produkty z niszy {niche}")

        # ---- Niche-specific rules ----
        if niche == "pet":
            pet_kws = NICHE_KEYWORDS["pet"]
            if any(any(kw in cat for kw in pet_kws) for cat in my_categories):
                score += 0.30
                reasons.append("pet w portfolio")
            no_elec = ["automat", "automatyczny", "wifi", "smart", "fontanna elektr"]
            if not _has_keyword(name_lower, no_elec):
                score += 0.20
                reasons.append("nie wymaga serwisu elektryki")
            if est_price <= 120:
                score += 0.20
                reasons.append("mieści się w paczkomacie (wagowo)")

        elif niche == "home_office":
            ho_kws = NICHE_KEYWORDS["home_office"]
            if any(any(kw in cat for kw in ho_kws) for cat in my_categories):
                score += 0.40
                reasons.append("home_office w portfolio")
            if "składan" in name_lower or "składa" in name_lower:
                score += 0.20
                reasons.append("składany = łatwe składowanie")
            if "zestaw" in name_lower:
                score += 0.10
                reasons.append("zestaw = wyższy koszyk")

        elif niche == "home":
            if "organiz" in name_lower:
                score += 0.20
                reasons.append("product problem-solving (organizacja)")
            # Palety zwrotne fit: broad category, common returns
            if any(kw in name_lower for kw in ["organiz", "pojemnik", "kuchni", "łazienka", "ogród"]):
                score += 0.20
                reasons.append("pasuje do modelu palet zwrotnych")
            heavy_furniture = ["szafa", "mebel", "komoda", "kanapa", "stół"]
            if not _has_keyword(name_lower, heavy_furniture):
                score += 0.20
                reasons.append("lekki/niemasywny (nie meble wielkie)")

        elif niche == "comfort":
            if est_price <= 120:
                score += 0.40
                reasons.append("paczkomat (≤120 zł)")
            medical_claims = ["leczy", "medyczny", "ce medical", "wyrób medyczny"]
            if not _has_keyword(name_lower, medical_claims):
                score += 0.30
                reasons.append("brak roszczeń medycznych (niższe ryzyko prawne)")

        elif niche == "auto":
            auto_kws = NICHE_KEYWORDS["auto"]
            if any(any(kw in cat for kw in auto_kws) for cat in my_categories):
                score += 0.30
                reasons.append("auto w portfolio")
            if "chemia" in name_lower:
                score += 0.20
                reasons.append("chemia = niska zwrotowość, dobra marża")
            auto_returns = ["organiz", "pokrowiec", "dywanik", "torba", "uchwyt"]
            if _has_keyword(name_lower, auto_returns):
                score += 0.20
                reasons.append("pasuje do modelu palet zwrotnych")

        score = _clamp(score)
        explanation = ", ".join(reasons) if reasons else "Brak szczególnych dopasowań"

        return round(score, 4), explanation

    except Exception as e:
        logger.error(f"[scoring] score_fit error: {e}", exc_info=True)
        return 0.3, "Brak danych (błąd dopasowania)"


def score_return(
    product_data: dict,
    niche: str,
    config: dict,
) -> tuple[float, str]:
    """
    Calculate return risk score (0.0–1.0, higher = lower return risk).

    Detection is based on keywords in the product name:
    - Is the product an electronic item? → higher return risk
    - Is it a simple/mechanical good? → lower return risk

    Args:
        product_data: Product dict with name/title.
        niche: Detected niche.
        config: Config dict (unused, provided for uniformity).

    Returns:
        Tuple of (score 0.0–1.0, Polish explanation string).
    """
    try:
        name_lower = _name_lower(product_data)

        is_electronics  = _has_keyword(name_lower, ELECTRONICS_KEYWORDS)
        is_simple_goods = _has_keyword(name_lower, SIMPLE_GOODS_KEYWORDS)

        # Niche-specific base scores
        niche_bases: dict[str, tuple[float, float]] = {
            # (electronics_base, simple/mechanical_base)
            "pet":         (0.55, 0.85),
            "home_office": (0.45, 0.75),
            "home":        (0.60, 0.90),
            "comfort":     (0.60, 0.80),
            "auto":        (0.45, 0.80),
        }

        elec_base, simple_base = niche_bases.get(niche, (0.55, 0.75))

        # Special overrides
        if niche == "auto":
            if "chemia" in name_lower:
                score = 0.85
                explanation = "Chemia samochodowa — bardzo niskie zwroty"
                return round(score, 4), explanation
            if any(kw in name_lower for kw in ["narzędzia", "organiz", "torba", "dywanik"]):
                score = 0.80
                explanation = "Narzędzia/organizer auto — niskie zwroty"
                return round(score, 4), explanation

        if niche == "comfort":
            if any(kw in name_lower for kw in ["wałek", "roller", "piłka"]):
                score = 0.80
                explanation = "Prosty akcesoria komfort (wałek/roller) — niskie zwroty"
                return round(score, 4), explanation

        # Main logic
        if is_electronics and not is_simple_goods:
            score = elec_base
            prod_type = "elektronika"
        elif is_simple_goods and not is_electronics:
            score = simple_base
            prod_type = "produkt mechaniczny/prosty"
        elif is_electronics and is_simple_goods:
            # Mixed: e.g. "uchwyt USB" — skew toward simple
            score = (elec_base * 0.4 + simple_base * 0.6)
            prod_type = "akcesoria (mix elektroniki i prostych)"
        else:
            # Unknown — use middle of the range
            score = (elec_base + simple_base) / 2.0
            prod_type = "nieokreślony typ"

        if score >= 0.80:
            risk_label = "Niskie ryzyko zwrotów"
        elif score >= 0.60:
            risk_label = "Umiarkowane ryzyko zwrotów"
        else:
            risk_label = "Wyższe ryzyko zwrotów"

        explanation = f"{risk_label} ({prod_type}, nisza {niche})"
        return round(score, 4), explanation

    except Exception as e:
        logger.error(f"[scoring] score_return error: {e}", exc_info=True)
        return 0.6, "Brak danych (błąd zwrotów)"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_scores(
    product_data: dict,
    my_data: dict,
    market_data: dict,
    config: dict,
) -> dict:
    """
    Compute all 5 component scores and the combined opportunity score.

    Args:
        product_data: Data from Allegro or local DB.  Expected keys (all optional
                      with graceful fallback):
                        name / title, price / est_price,
                        watchers_count / watchersCount,
                        views_count / viewsCount,
                        monthly_sales_est, category (str or dict),
                        purchase_cost_estimate.
        my_data:      Data about the user's current warehouse / portfolio.
                        my_categories (list[str]),
                        avg_price_range (tuple[float, float]).
        market_data:  Market-level signals.
                        similar_offers_count (int),
                        price_list (list[float]),
                        smart_sellers_fraction (float 0–1),
                        top_reviews_count (int, optional).
        config:       Config dict.  May contain sub-keys:
                        weights (dict), thresholds (dict).
                      Missing keys fall back to module defaults.

    Returns:
        Dict::

            {
                "niche": "pet",
                "trend_score": 0.72,
                "competition_score": 0.58,
                "margin_score": 0.81,
                "fit_score": 0.65,
                "return_score": 0.85,
                "opportunity_score": 0.73,
                "weights_used": {
                    "trend": 0.20, "competition": 0.15,
                    "margin": 0.35, "fit": 0.20, "return": 0.10,
                },
                "breakdown": {
                    "trend": "Umiarkowany popyt (est. 120 szt/mies.)",
                    "competition": "Średnia konkurencja (234 ofert, 45% Smart)",
                    "margin": "Dobra marża szacowana ~28%",
                    "fit": "Pasuje do Twojego portfolio (masz podobne w pet)",
                    "return": "Niskie ryzyko zwrotów (produkt mechaniczny)",
                },
            }
    """
    try:
        # Detect niche
        niche = detect_niche(product_data)

        # Resolve weights (config may override defaults)
        raw_weights = config.get("weights", {})
        weights: dict[str, float] = {
            "trend":       _safe_float(raw_weights.get("trend"),       DEFAULT_WEIGHTS["trend"]),
            "competition": _safe_float(raw_weights.get("competition"), DEFAULT_WEIGHTS["competition"]),
            "margin":      _safe_float(raw_weights.get("margin"),      DEFAULT_WEIGHTS["margin"]),
            "fit":         _safe_float(raw_weights.get("fit"),         DEFAULT_WEIGHTS["fit"]),
            "return":      _safe_float(raw_weights.get("return"),      DEFAULT_WEIGHTS["return"]),
        }

        # --- Compute individual scores ---
        trend_s,       trend_exp       = score_trend(product_data, niche, config)
        competition_s, competition_exp = score_competition(product_data, niche, market_data, config)
        margin_s,      margin_exp      = score_margin(product_data, niche, config)
        fit_s,         fit_exp         = score_fit(product_data, niche, my_data, config)
        return_s,      return_exp      = score_return(product_data, niche, config)

        # --- Weighted opportunity score ---
        w_sum = sum(weights.values())
        if w_sum <= 0:
            opportunity = 0.0
        else:
            opportunity = (
                trend_s       * weights["trend"] +
                competition_s * weights["competition"] +
                margin_s      * weights["margin"] +
                fit_s         * weights["fit"] +
                return_s      * weights["return"]
            ) / w_sum

        opportunity = round(_clamp(opportunity), 4)

        return {
            "niche":             niche,
            "trend_score":       trend_s,
            "competition_score": competition_s,
            "margin_score":      margin_s,
            "fit_score":         fit_s,
            "return_score":      return_s,
            "opportunity_score": opportunity,
            "weights_used":      weights,
            "breakdown": {
                "trend":       trend_exp,
                "competition": competition_exp,
                "margin":      margin_exp,
                "fit":         fit_exp,
                "return":      return_exp,
            },
        }

    except Exception as e:
        logger.error(f"[scoring] compute_scores error: {e}", exc_info=True)
        return {
            "niche":             "unknown",
            "trend_score":       0.0,
            "competition_score": 0.0,
            "margin_score":      0.0,
            "fit_score":         0.0,
            "return_score":      0.0,
            "opportunity_score": 0.0,
            "weights_used":      DEFAULT_WEIGHTS.copy(),
            "breakdown": {
                "trend":       "Błąd obliczeń",
                "competition": "Błąd obliczeń",
                "margin":      "Błąd obliczeń",
                "fit":         "Błąd obliczeń",
                "return":      "Błąd obliczeń",
            },
        }


def generate_notes(scores: dict, product_data: dict) -> str:
    """
    Generate a 1-2 sentence Polish recommendation based on computed scores.

    Highlights the strongest positive signal and the most critical risk.

    Args:
        scores:       Output of :func:`compute_scores`.
        product_data: Original product dict (used for name context).

    Returns:
        Polish recommendation string.
    """
    try:
        opp    = _safe_float(scores.get("opportunity_score"), 0.0)
        niche  = scores.get("niche", "unknown")
        margin = _safe_float(scores.get("margin_score"),      0.0)
        comp   = _safe_float(scores.get("competition_score"), 0.0)
        ret    = _safe_float(scores.get("return_score"),      0.0)
        trend  = _safe_float(scores.get("trend_score"),       0.0)
        fit    = _safe_float(scores.get("fit_score"),         0.0)

        name = (product_data.get("name") or product_data.get("title") or "Produkt")[:60]

        if opp >= 0.70:
            opening = f'Dobra okazja! \u201e{name}\u201d ma wysoki potencja\u0142 w niszy {niche}.'
        elif opp >= 0.50:
            opening = f'\u201e{name}\u201d \u2014 interesuj\u0105ca propozycja w niszy {niche}, warta analizy.'
        elif opp >= 0.35:
            opening = f'\u201e{name}\u201d \u2014 przeci\u0119tny potencja\u0142 w niszy {niche}. Sprawd\u017a szczeg\u00f3\u0142y.'
        else:
            opening = f'\u201e{name}\u201d \u2014 s\u0142aby wynik. Niski potencja\u0142 w niszy {niche}.'

        # Find biggest risk / best signal for second sentence
        risks: list[tuple[float, str]] = []
        signals: list[tuple[float, str]] = []

        if comp < 0.35:
            risks.append((comp, "duża konkurencja — trudno się wyróżnić"))
        if ret < 0.50:
            risks.append((ret, "podwyższone ryzyko zwrotów (elektronika?)"))
        if margin < 0.35:
            risks.append((margin, "niska marża — sprawdź koszty dostawy i prowizji"))
        if trend < 0.35:
            risks.append((trend, "słaby sygnał popytu"))

        if margin >= 0.75:
            signals.append((margin, "wysoka marża"))
        if ret >= 0.80:
            signals.append((ret, "niskie ryzyko zwrotów"))
        if comp >= 0.65:
            signals.append((comp, "niska konkurencja"))
        if trend >= 0.75:
            signals.append((trend, "silny trend/popyt"))
        if fit >= 0.65:
            signals.append((fit, "świetne dopasowanie do portfolio"))

        parts: list[str] = []
        if risks:
            # Biggest risk first
            risks.sort(key=lambda x: x[0])
            parts.append(f"Uwaga: {risks[0][1]}.")
        if signals:
            signals.sort(key=lambda x: x[0], reverse=True)
            parts.append(f"Zaleta: {signals[0][1]}.")

        if not parts:
            if opp >= 0.50:
                parts.append("Warto sprawdzić dostawcę i negocjować cenę zakupu.")
            else:
                parts.append("Rozważ inny produkt lub sprawdź czy możesz obniżyć koszty.")

        return f"{opening} {' '.join(parts)}"

    except Exception as e:
        logger.error(f"[scoring] generate_notes error: {e}", exc_info=True)
        return "Brak możliwości wygenerowania rekomendacji."
