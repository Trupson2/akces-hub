# -*- coding: utf-8 -*-
"""
Winning Products — główny orchestrator analizy.
Łączy Allegro API z algorytmem scoringowym i zapisuje wyniki w DB.
"""

import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def init_winning_tables() -> None:
    """
    Tworzy tabele winning_products i winning_products_meta jeśli nie istnieją.
    Bezpieczne do wywołania wielokrotnie.
    """
    try:
        from modules.database import get_db
        conn = get_db()

        conn.execute("""
            CREATE TABLE IF NOT EXISTS winning_products (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                source             TEXT NOT NULL DEFAULT 'allegro',
                external_id        TEXT,
                name               TEXT,
                category           TEXT,
                category_id        TEXT,
                marketplace_url    TEXT,
                my_product_id      INTEGER NULL REFERENCES produkty(id),
                my_sku             TEXT NULL,
                est_price          REAL NULL,
                est_monthly_sales  REAL NULL,
                est_margin         REAL NULL,
                trend_score        REAL NULL,
                competition_score  REAL NULL,
                opportunity_score  REAL NULL,
                notes              TEXT NULL,
                batch_id           TEXT NULL,
                created_at         TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_winning_opportunity
                ON winning_products(opportunity_score DESC)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS winning_products_meta (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id       TEXT UNIQUE,
                started_at     TEXT,
                finished_at    TEXT,
                status         TEXT,
                categories_scanned TEXT,
                products_found INTEGER DEFAULT 0,
                error_msg      TEXT NULL
            )
        """)

        conn.commit()
        logger.info("[winning_analyzer] Tabele winning_products zainicjalizowane")

    except Exception as e:
        logger.error(f"[winning_analyzer] init_winning_tables error: {e}")
        raise


def get_categories_of_interest() -> list[str]:
    """
    Pobiera listę kategorii Allegro do analizy z config table.

    Returns:
        Lista ID kategorii (np. ["258682", "257993"])
    """
    try:
        from modules.database import get_config
        raw = get_config("winning_categories", "[]")
        categories = json.loads(raw)
        if not isinstance(categories, list):
            return []
        return [str(c) for c in categories if c]
    except Exception as e:
        logger.error(f"[winning_analyzer] get_categories_of_interest error: {e}")
        return []


def fetch_category_offers(category_id: str, limit: int = 50) -> list[dict]:
    """
    Pobiera popularne oferty z danej kategorii Allegro.
    Sortuje po liczbie obserwujących (proxy popularności).

    Args:
        category_id: ID kategorii Allegro
        limit: Maksymalna liczba ofert

    Returns:
        Lista ofert lub [] przy błędzie
    """
    try:
        from modules.allegro_api import allegro_request

        params = {
            "category.id": category_id,
            "sort": "-watchersCount",
            "limit": min(limit, 100),
            "include": "-all +items +filters",
        }

        response = allegro_request("GET", "/offers/listing", params=params)

        if not response:
            logger.warning(f"[winning_analyzer] Brak odpowiedzi dla kategorii {category_id}")
            return []

        if "error" in response and not isinstance(response.get("error"), bool):
            logger.warning(f"[winning_analyzer] Błąd API dla kategorii {category_id}: {response}")
            return []

        # Allegro zwraca offers w items.regular lub items.promoted
        items = response.get("items", {}) or {}
        offers = []

        # Zwykłe oferty
        regular = items.get("regular", []) or []
        offers.extend(regular)

        # Promowane oferty
        promoted = items.get("promoted", []) or []
        offers.extend(promoted)

        logger.info(f"[winning_analyzer] Kategoria {category_id}: {len(offers)} ofert")
        return offers[:limit]

    except Exception as e:
        logger.error(f"[winning_analyzer] fetch_category_offers({category_id}) error: {e}")
        return []


def _get_weights() -> dict:
    """Pobiera wagi scoringu z config DB."""
    try:
        from modules.database import get_config
        raw = get_config("winning_weights", '{"trend":0.35,"comp":0.30,"fit":0.20,"margin":0.15}')
        weights = json.loads(raw)
        return weights
    except Exception:
        return {"trend": 0.35, "comp": 0.30, "fit": 0.20, "margin": 0.15}


def _get_min_opportunity_score() -> float:
    """Pobiera minimalny próg opportunity_score z config."""
    try:
        from modules.database import get_config
        return float(get_config("winning_min_opportunity_score", "0.3"))
    except Exception:
        return 0.3


def _get_cooldown_minutes() -> int:
    """Pobiera cooldown w minutach z config."""
    try:
        from modules.database import get_config
        return int(get_config("winning_cooldown_minutes", "30"))
    except Exception:
        return 30


def _check_cooldown() -> tuple[bool, int]:
    """
    Sprawdza czy upłynął czas od ostatniego skanu.

    Returns:
        (can_run, minutes_remaining)
    """
    try:
        from modules.database import get_config
        last_run = get_config("winning_last_run", "")
        if not last_run:
            return True, 0

        cooldown_minutes = _get_cooldown_minutes()
        last_run_dt = datetime.fromisoformat(last_run)
        next_allowed = last_run_dt + timedelta(minutes=cooldown_minutes)

        if datetime.now() >= next_allowed:
            return True, 0

        remaining = int((next_allowed - datetime.now()).total_seconds() / 60)
        return False, remaining

    except Exception:
        return True, 0


def _save_winning_product(
    batch_id: str,
    offer: dict,
    category_id: str,
    category_name: str,
    trend: float,
    competition: float,
    opportunity: float,
    est_margin: float,
    notes: str,
) -> None:
    """Zapisuje pojedynczy produkt do tabeli winning_products."""
    try:
        from modules.database import get_db

        # Wyciągnij dane z oferty
        name = offer.get("name", "")[:500]
        external_id = offer.get("id", "")

        # Cena
        price = None
        try:
            price_raw = offer.get("sellingMode", {}).get("price", {}).get("amount")
            if price_raw:
                price = float(price_raw)
        except (ValueError, TypeError, AttributeError):
            pass

        # URL do oferty Allegro
        marketplace_url = None
        try:
            url_raw = offer.get("url", "")
            if url_raw:
                marketplace_url = url_raw
            elif external_id:
                marketplace_url = f"https://allegro.pl/oferta/{external_id}"
        except Exception:
            pass

        now = datetime.now().isoformat(sep=" ", timespec="seconds")

        conn = get_db()
        conn.execute(
            """
            INSERT INTO winning_products
                (source, external_id, name, category, category_id, marketplace_url,
                 est_price, est_margin, trend_score, competition_score,
                 opportunity_score, notes, batch_id, created_at)
            VALUES
                ('allegro', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                external_id, name, category_name, category_id, marketplace_url,
                price, est_margin, trend, competition, opportunity,
                notes, batch_id, now
            )
        )
        conn.commit()

    except Exception as e:
        logger.error(f"[winning_analyzer] _save_winning_product error: {e}")


def run_winning_products_scan(categories: list | None = None) -> dict:
    """
    Główna funkcja analizy winning products.

    1. Sprawdza cooldown
    2. Tworzy batch_id
    3. Inicjalizuje meta
    4. Ładuje dane wewnętrzne użytkownika
    5. Dla każdej kategorii: fetchuje oferty i scoruje
    6. Zapisuje do DB
    7. Aktualizuje meta i last_run
    8. Zwraca podsumowanie

    Args:
        categories: Lista ID kategorii (opcjonalnie, override config)

    Returns:
        Dict: {batch_id, products_found, duration_s, top_3}

    Raises:
        ValueError: Jeśli cooldown nie upłynął
        RuntimeError: W przypadku błędu analizy
    """
    # Inicjalizuj tabele przy pierwszym uruchomieniu
    init_winning_tables()

    # Sprawdź cooldown
    can_run, minutes_remaining = _check_cooldown()
    if not can_run:
        cooldown_minutes = _get_cooldown_minutes()
        last_run_minutes = cooldown_minutes - minutes_remaining
        error = {
            "error": f"Ostatnia analiza była {last_run_minutes} minut temu. Poczekaj {minutes_remaining} minut.",
            "cooldown": True,
            "minutes_remaining": minutes_remaining,
        }
        raise ValueError(json.dumps(error))

    start_time = time.time()
    batch_id = uuid.uuid4().hex[:12]

    # Ustal kategorie
    if not categories:
        categories = get_categories_of_interest()

    if not categories:
        # Domyślne popularne kategorie Allegro jeśli brak konfiguracji
        categories = ["258682", "257993", "4029"]  # Elektronika, AGD, Dom
        logger.warning("[winning_analyzer] Brak skonfigurowanych kategorii — używam domyślnych")

    logger.info(f"[winning_analyzer] Batch {batch_id}: skanowanie {len(categories)} kategorii")

    # Zapisz meta: status=running
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    try:
        from modules.database import get_db
        conn = get_db()
        conn.execute(
            """
            INSERT INTO winning_products_meta
                (batch_id, started_at, status, categories_scanned, products_found)
            VALUES (?, ?, 'running', ?, 0)
            """,
            (batch_id, now, json.dumps(categories))
        )
        conn.commit()
    except Exception as meta_e:
        logger.error(f"[winning_analyzer] Błąd zapisu meta: {meta_e}")

    # Załaduj dane wewnętrzne
    try:
        from modules.akces_data import get_my_categories, get_avg_margin, get_product_category_fit
        my_categories = get_my_categories()
        avg_margin = get_avg_margin()
        logger.info(f"[winning_analyzer] Moje kategorie: {my_categories[:5]}, avg_margin={avg_margin:.2%}")
    except Exception as data_e:
        logger.warning(f"[winning_analyzer] Błąd ładowania danych wewnętrznych: {data_e}")
        my_categories = []
        avg_margin = 0.25

    from modules.winning_scoring import (
        score_trend, score_competition, score_opportunity,
        estimate_margin_potential, generate_notes
    )

    weights = _get_weights()
    min_score = _get_min_opportunity_score()

    products_found = 0
    all_results = []

    try:
        for category_id in categories:
            logger.info(f"[winning_analyzer] Analizuję kategorię: {category_id}")

            offers = fetch_category_offers(category_id, limit=50)
            if not offers:
                logger.warning(f"[winning_analyzer] Brak ofert dla kategorii {category_id}")
                continue

            # Pobierz nazwę kategorii
            category_name = _get_category_name(category_id)

            # Pobierz dopasowanie kategorii do portfolio
            try:
                from modules.akces_data import get_product_category_fit
                cat_fit = get_product_category_fit(category_name)
            except Exception:
                cat_fit = 0.0

            for offer in offers:
                try:
                    # Oblicz scoring
                    trend = score_trend(offer)
                    competition = score_competition(offers, offer)

                    # Szacowana cena
                    est_price = 0.0
                    try:
                        est_price = float(
                            (offer.get("sellingMode", {}) or {})
                            .get("price", {})
                            .get("amount", 0) or 0
                        )
                    except (ValueError, TypeError):
                        pass

                    # Potencjał marżowy
                    margin_potential = estimate_margin_potential(est_price, est_price * (1 - avg_margin))
                    opportunity = score_opportunity(trend, competition, cat_fit, margin_potential, weights)

                    if opportunity < min_score:
                        continue

                    # Generuj notatki
                    notes = generate_notes(offer, trend, competition, opportunity)

                    # Zapisz do DB
                    _save_winning_product(
                        batch_id=batch_id,
                        offer=offer,
                        category_id=category_id,
                        category_name=category_name,
                        trend=trend,
                        competition=competition,
                        opportunity=opportunity,
                        est_margin=margin_potential,
                        notes=notes,
                    )

                    products_found += 1
                    all_results.append({
                        "name": offer.get("name", "")[:80],
                        "opportunity_score": opportunity,
                        "trend_score": trend,
                        "competition_score": competition,
                        "est_price": est_price,
                    })

                except Exception as offer_e:
                    logger.warning(f"[winning_analyzer] Błąd scoringu oferty: {offer_e}")
                    continue

            # Krótka pauza między kategoriami
            time.sleep(0.5)

        # Zaktualizuj meta: status=done
        duration_s = round(time.time() - start_time, 2)
        finished_at = datetime.now().isoformat(sep=" ", timespec="seconds")

        try:
            from modules.database import get_db, set_config
            conn = get_db()
            conn.execute(
                """
                UPDATE winning_products_meta
                SET status='done', finished_at=?, products_found=?
                WHERE batch_id=?
                """,
                (finished_at, products_found, batch_id)
            )
            conn.commit()

            # Zaktualizuj last_run
            set_config("winning_last_run", datetime.now().isoformat())

        except Exception as meta_e:
            logger.error(f"[winning_analyzer] Błąd aktualizacji meta: {meta_e}")

        # Top 3 wyniki
        all_results.sort(key=lambda x: x["opportunity_score"], reverse=True)
        top_3 = all_results[:3]

        logger.info(f"[winning_analyzer] Batch {batch_id}: znaleziono {products_found} produktów w {duration_s}s")

        return {
            "batch_id": batch_id,
            "products_found": products_found,
            "duration_s": duration_s,
            "top_3": top_3,
        }

    except Exception as e:
        # Zaktualizuj meta: status=error
        try:
            from modules.database import get_db
            conn = get_db()
            conn.execute(
                """
                UPDATE winning_products_meta
                SET status='error', error_msg=?, finished_at=?
                WHERE batch_id=?
                """,
                (str(e)[:1000], datetime.now().isoformat(sep=" ", timespec="seconds"), batch_id)
            )
            conn.commit()
        except Exception:
            pass

        logger.error(f"[winning_analyzer] run_winning_products_scan error: {e}", exc_info=True)
        raise RuntimeError(f"Błąd analizy: {e}") from e


def _get_category_name(category_id: str) -> str:
    """
    Próbuje pobrać nazwę kategorii przez Allegro API.
    Fallback: zwraca ID.

    Args:
        category_id: ID kategorii Allegro

    Returns:
        Nazwa kategorii lub ID jako fallback
    """
    try:
        from modules.allegro_api import allegro_request
        response = allegro_request("GET", f"/sale/categories/{category_id}")
        if response and "name" in response:
            return response["name"]
    except Exception:
        pass
    return f"Kategoria {category_id}"


def get_winning_products(
    limit: int = 50,
    offset: int = 0,
    min_score: float = 0.0,
    batch_id: str | None = None
) -> tuple[list[dict], int]:
    """
    Pobiera produkty z tabeli winning_products.

    Args:
        limit: Limit wyników
        offset: Offset
        min_score: Minimalny opportunity_score
        batch_id: Opcjonalnie filtruj po batch_id

    Returns:
        (lista produktów, total count)
    """
    try:
        from modules.database import get_db
        conn = get_db()

        where_clauses = ["opportunity_score >= ?"]
        params = [min_score]

        if batch_id:
            where_clauses.append("batch_id = ?")
            params.append(batch_id)

        where = " AND ".join(where_clauses)

        total = conn.execute(
            f"SELECT COUNT(*) FROM winning_products WHERE {where}",
            params
        ).fetchone()[0]

        rows = conn.execute(
            f"""
            SELECT id, source, external_id, name, category, category_id, marketplace_url,
                   my_product_id, my_sku, est_price, est_monthly_sales, est_margin,
                   trend_score, competition_score, opportunity_score, notes, batch_id, created_at
            FROM winning_products
            WHERE {where}
            ORDER BY opportunity_score DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset]
        ).fetchall()

        return [dict(row) for row in rows], total

    except Exception as e:
        logger.error(f"[winning_analyzer] get_winning_products error: {e}")
        return [], 0


def get_winning_meta(limit: int = 10) -> list[dict]:
    """
    Pobiera ostatnie meta-dane runów analizy.

    Args:
        limit: Maksymalna liczba wyników

    Returns:
        Lista słowników z metadanymi runów
    """
    try:
        from modules.database import get_db
        conn = get_db()

        rows = conn.execute(
            """
            SELECT id, batch_id, started_at, finished_at, status,
                   categories_scanned, products_found, error_msg
            FROM winning_products_meta
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()

        return [dict(row) for row in rows]

    except Exception as e:
        logger.error(f"[winning_analyzer] get_winning_meta error: {e}")
        return []


# Inicjalizuj tabele przy imporcie modułu
try:
    init_winning_tables()
except Exception as _init_e:
    logger.warning(f"[winning_analyzer] Nie można zainicjalizować tabel przy imporcie: {_init_e}")
