# -*- coding: utf-8 -*-
"""
Akces Data — read-only dostęp do akces_hub.db dla winning products.
Używa get_db() z modules/database.py.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def get_my_products(limit: int = 500) -> list[dict]:
    """
    Pobiera produkty z magazynu.

    Args:
        limit: Maksymalna liczba produktów

    Returns:
        Lista słowników: {id, nazwa, kategoria, cena_allegro, ean, status}
    """
    try:
        from modules.database import get_db
        conn = get_db()
        rows = conn.execute(
            """
            SELECT id, nazwa, kategoria, cena_allegro, ean, status
            FROM produkty
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"[akces_data] get_my_products error: {e}")
        return []


def get_my_categories() -> list[str]:
    """
    Pobiera unikalne kategorie z tabeli produkty.

    Returns:
        Lista nazw kategorii
    """
    try:
        from modules.database import get_db
        conn = get_db()
        rows = conn.execute(
            """
            SELECT DISTINCT kategoria
            FROM produkty
            WHERE kategoria IS NOT NULL AND kategoria != ''
            ORDER BY kategoria
            """
        ).fetchall()
        return [row["kategoria"] for row in rows]
    except Exception as e:
        logger.error(f"[akces_data] get_my_categories error: {e}")
        return []


def get_sales_stats(days: int = 90) -> dict:
    """
    Pobiera statystyki sprzedaży z ostatnich N dni.

    Args:
        days: Okres analizy w dniach

    Returns:
        Słownik: {total_sales, total_revenue, top_categories: [{kategoria, count, revenue}]}
    """
    try:
        from modules.database import get_db
        conn = get_db()

        date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        # Łączna sprzedaż i przychód
        row = conn.execute(
            """
            SELECT COUNT(*) as total_sales, COALESCE(SUM(cena * ilosc), 0) as total_revenue
            FROM sprzedaze
            WHERE data_sprzedazy >= ?
            """,
            (date_from,)
        ).fetchone()

        total_sales = row["total_sales"] if row else 0
        total_revenue = row["total_revenue"] if row else 0.0

        # Top kategorie (przez JOIN ze sprzedazami)
        top_categories = []
        try:
            cat_rows = conn.execute(
                """
                SELECT
                    COALESCE(p.kategoria, 'inne') as kategoria,
                    COUNT(*) as count,
                    COALESCE(SUM(s.cena * s.ilosc), 0) as revenue
                FROM sprzedaze s
                LEFT JOIN produkty p ON p.id = s.produkt_id
                WHERE s.data_sprzedazy >= ?
                GROUP BY COALESCE(p.kategoria, 'inne')
                ORDER BY revenue DESC
                LIMIT 10
                """,
                (date_from,)
            ).fetchall()
            top_categories = [dict(row) for row in cat_rows]
        except Exception as cat_e:
            logger.warning(f"[akces_data] Błąd pobierania top kategorii: {cat_e}")

        return {
            "total_sales": total_sales,
            "total_revenue": float(total_revenue),
            "top_categories": top_categories,
            "days": days,
        }

    except Exception as e:
        logger.error(f"[akces_data] get_sales_stats error: {e}")
        return {
            "total_sales": 0,
            "total_revenue": 0.0,
            "top_categories": [],
            "days": days,
        }


def get_avg_margin() -> float:
    """
    Oblicza średnią marżę z sprzedanych produktów.
    Używa cena_zakupu z palet (prawdziwy koszt zakupu).

    Returns:
        Średnia marża (0.0 - 1.0), np. 0.25 = 25%
    """
    try:
        from modules.database import get_db
        conn = get_db()

        # Marża = (cena_sprzedazy - koszt_zakupu) / cena_sprzedazy
        # koszt_zakupu z powiązanej palety
        rows = conn.execute(
            """
            SELECT
                s.cena as cena_sprzedazy,
                pal.cena_zakupu as cena_palety,
                pal.ilosc_produktow as ilosc_produktow
            FROM sprzedaze s
            JOIN produkty p ON p.id = s.produkt_id
            JOIN palety pal ON pal.id = p.paleta_id
            WHERE pal.cena_zakupu > 0
              AND pal.ilosc_produktow > 0
              AND s.cena > 0
            LIMIT 500
            """
        ).fetchall()

        if not rows:
            # Fallback: prosty szacunek z cen produktów
            return _estimate_margin_fallback()

        margins = []
        for row in rows:
            cost_per_unit = row["cena_palety"] / row["ilosc_produktow"]
            margin = (row["cena_sprzedazy"] - cost_per_unit) / row["cena_sprzedazy"]
            margins.append(margin)

        if not margins:
            return 0.25  # Domyślna marża 25%

        avg = sum(margins) / len(margins)
        return round(max(0.0, min(1.0, avg)), 4)

    except Exception as e:
        logger.error(f"[akces_data] get_avg_margin error: {e}")
        return 0.25


def _estimate_margin_fallback() -> float:
    """Fallback do szacowania marży bez danych o kosztach."""
    try:
        from modules.database import get_db
        conn = get_db()

        row = conn.execute(
            """
            SELECT
                AVG((s.cena - p.cena_netto) / NULLIF(s.cena, 0)) as avg_margin
            FROM sprzedaze s
            JOIN produkty p ON p.id = s.produkt_id
            WHERE s.cena > 0 AND p.cena_netto > 0 AND p.cena_netto < s.cena
            LIMIT 200
            """
        ).fetchone()

        if row and row["avg_margin"] is not None:
            return round(max(0.0, min(1.0, row["avg_margin"])), 4)
    except Exception:
        pass

    return 0.25  # Domyślna marża 25%


def get_product_category_fit(category_name: str) -> float:
    """
    Ocenia jak dobrze dana kategoria pasuje do portfolio użytkownika (0.0 - 1.0).
    Bazuje na historii sprzedaży i aktualnych produktach w tej kategorii.

    Args:
        category_name: Nazwa kategorii Allegro

    Returns:
        Wynik dopasowania 0.0 - 1.0 (1.0 = idealne dopasowanie)
    """
    try:
        from modules.database import get_db
        conn = get_db()

        # Normalizacja nazwy kategorii (lowercase, trim)
        cat_lower = category_name.lower().strip()

        # 1. Sprawdź czy mamy produkty w tej kategorii
        product_count = conn.execute(
            """
            SELECT COUNT(*) as cnt
            FROM produkty
            WHERE LOWER(TRIM(kategoria)) = ?
            """,
            (cat_lower,)
        ).fetchone()["cnt"]

        # 2. Sprawdź historię sprzedaży w tej kategorii (ostatnie 90 dni)
        date_from = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        sales_count = conn.execute(
            """
            SELECT COUNT(*) as cnt
            FROM sprzedaze s
            JOIN produkty p ON p.id = s.produkt_id
            WHERE LOWER(TRIM(p.kategoria)) = ?
              AND s.data_sprzedazy >= ?
            """,
            (cat_lower, date_from)
        ).fetchone()["cnt"]

        # 3. Sprawdź podobieństwo nazwy (kategoria zawiera słowa kluczowe)
        # Szukaj produktów których kategoria zawiera fragment nazwy
        keywords = cat_lower.split()[:3]  # Max 3 słowa kluczowe
        keyword_matches = 0
        for keyword in keywords:
            if len(keyword) >= 3:
                cnt = conn.execute(
                    """
                    SELECT COUNT(*) as cnt FROM produkty
                    WHERE LOWER(kategoria) LIKE ?
                    """,
                    (f"%{keyword}%",)
                ).fetchone()["cnt"]
                if cnt > 0:
                    keyword_matches += 1

        # Oblicz wynik (0-1)
        # Produkty w kategorii: 0-0.5
        product_score = min(0.5, product_count * 0.05)
        # Sprzedaże: 0-0.3
        sales_score = min(0.3, sales_count * 0.03)
        # Dopasowanie słów kluczowych: 0-0.2
        kw_score = 0.2 * (keyword_matches / max(len(keywords), 1)) if keywords else 0.0

        fit = product_score + sales_score + kw_score
        return round(min(1.0, fit), 4)

    except Exception as e:
        logger.error(f"[akces_data] get_product_category_fit error: {e}")
        return 0.0
