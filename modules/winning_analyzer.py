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

        # Migracja: kolumna ignored
        try:
            conn.execute("ALTER TABLE winning_products ADD COLUMN ignored INTEGER DEFAULT 0")
        except Exception:
            pass  # już istnieje

        # Tabela listy zakupów
        conn.execute("""
            CREATE TABLE IF NOT EXISTS zakupy_lista (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                winning_product_id  INTEGER NULL,
                name                TEXT NOT NULL,
                category            TEXT,
                est_price           REAL,
                niche               TEXT,
                notes               TEXT,
                status              TEXT DEFAULT 'new',
                created_at          TEXT NOT NULL
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

        # allegro_request zwraca (data, error_msg) — trzeba rozpakować
        result = allegro_request("GET", "/offers/listing", params=params)
        if isinstance(result, tuple):
            response, err = result
        else:
            response, err = result, None

        if err or not response:
            logger.warning(f"[winning_analyzer] Błąd API dla kategorii {category_id}: {err or 'brak odpowiedzi'}")
            return []

        if not isinstance(response, dict):
            logger.warning(f"[winning_analyzer] Nieoczekiwany typ odpowiedzi: {type(response)}")
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
    Analiza winning products na podstawie własnych danych sprzedażowych.
    Nie wymaga dostępu do zewnętrznego Allegro API (omija problem braku scope).

    Źródła danych:
    - sprzedaze: historia sprzedaży
    - produkty: stan magazynowy, ceny
    - palety: koszt zakupu

    Scoring oparty na:
    - Prędkość sprzedaży (sztuki/miesiąc)
    - ROI (cena sprzedaży vs koszt zakupu)
    - Trend (porównanie ostatnich 30 vs poprzednich 30 dni)
    - Dostępność (ile dni zostało przy obecnej prędkości)
    """
    init_winning_tables()

    can_run, minutes_remaining = _check_cooldown()
    if not can_run:
        cooldown_minutes = _get_cooldown_minutes()
        last_run_minutes = cooldown_minutes - minutes_remaining
        raise ValueError(json.dumps({
            "error": f"Ostatnia analiza była {last_run_minutes} minut temu. Poczekaj {minutes_remaining} minut.",
            "cooldown": True,
            "minutes_remaining": minutes_remaining,
        }))

    start_time = time.time()
    batch_id = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    min_score = _get_min_opportunity_score()

    from modules.database import get_db, set_config
    conn = get_db()

    try:
        conn.execute(
            "INSERT INTO winning_products_meta (batch_id, started_at, status, categories_scanned, products_found) VALUES (?, ?, 'running', ?, 0)",
            (batch_id, now, json.dumps(["internal_sales"]))
        )
        conn.commit()
    except Exception as e:
        logger.error(f"[winning_analyzer] meta insert error: {e}")

    products_found = 0
    all_results = []

    try:
        # ── 1. Prędkość sprzedaży per produkt (ostatnie 90 dni) ──────────────
        rows_90 = conn.execute("""
            SELECT produkt_id,
                   SUM(ilosc) as sold_90,
                   COUNT(*) as tx_90,
                   AVG(cena) as avg_price
            FROM sprzedaze
            WHERE produkt_id IS NOT NULL
              AND status NOT IN ('zwrot','anulowane','anulowana')
              AND (kupujacy IS NULL OR kupujacy != 'offline')
              AND data_sprzedazy >= date('now', '-90 days')
            GROUP BY produkt_id
            HAVING sold_90 > 0
            ORDER BY sold_90 DESC
            LIMIT 200
        """).fetchall()

        if not rows_90:
            # Fallback: wszystkie dane jeśli brak z 90 dni
            rows_90 = conn.execute("""
                SELECT produkt_id,
                       SUM(ilosc) as sold_90,
                       COUNT(*) as tx_90,
                       AVG(cena) as avg_price
                FROM sprzedaze
                WHERE produkt_id IS NOT NULL
                  AND status NOT IN ('zwrot','anulowane','anulowana')
                GROUP BY produkt_id
                HAVING sold_90 > 0
                ORDER BY sold_90 DESC
                LIMIT 200
            """).fetchall()

        # ── 2. Ostatnie 30 dni (trend) ─────────────────────────────────────
        rows_30 = {}
        try:
            for r in conn.execute("""
                SELECT produkt_id, SUM(ilosc) as sold_30
                FROM sprzedaze
                WHERE produkt_id IS NOT NULL
                  AND status NOT IN ('zwrot','anulowane','anulowana')
                  AND (kupujacy IS NULL OR kupujacy != 'offline')
                  AND data_sprzedazy >= date('now', '-30 days')
                GROUP BY produkt_id
            """).fetchall():
                rows_30[r['produkt_id']] = r['sold_30'] or 0
        except Exception:
            pass

        # ── 3. Dane produktów ─────────────────────────────────────────────
        for row in rows_90:
            try:
                pid = row['produkt_id']
                sold_90 = row['sold_90'] or 0
                avg_price = float(row['avg_price'] or 0)
                sold_30 = rows_30.get(pid, 0)

                # Pobierz dane produktu
                p = conn.execute(
                    "SELECT id, nazwa, ean, ilosc, cena_brutto, zdjecie_url, kategoria FROM produkty WHERE id=?",
                    (pid,)
                ).fetchone()
                if not p:
                    continue

                nazwa = p['nazwa'] or ''
                ilosc = p['ilosc'] or 0
                kategoria = p['kategoria'] or 'Inne'

                # Koszt zakupu — z palety jeśli dostępny
                koszt = 0.0
                try:
                    paleta_row = conn.execute("""
                        SELECT pp.cena_zakupu / NULLIF(pp.ilosc_produktow, 0) as unit_cost
                        FROM palety pp
                        JOIN produkty pr ON pr.paleta_id = pp.id
                        WHERE pr.id = ?
                    """, (pid,)).fetchone()
                    if paleta_row and paleta_row['unit_cost']:
                        koszt = float(paleta_row['unit_cost'])
                except Exception:
                    pass

                if koszt <= 0 and avg_price > 0:
                    koszt = avg_price * 0.35  # szacunkowy koszt 35% ceny

                # ── Scoring ───────────────────────────────────────────────
                # Prędkość: sztuki/miesiąc (normalizuj do 0-1, max=30)
                velocity = sold_90 / 3.0  # sztuki/miesiąc
                velocity_score = min(velocity / 30.0, 1.0)

                # Trend: czy ostatnie 30 dni lepsze niż poprzednie 30?
                sold_prev_30 = (sold_90 - sold_30) / 2.0
                if sold_prev_30 > 0:
                    trend_ratio = sold_30 / sold_prev_30
                    trend_score = min(trend_ratio / 2.0, 1.0)  # 2x = max
                else:
                    trend_score = 0.5 if sold_30 > 0 else 0.2

                # ROI
                if koszt > 0 and avg_price > 0:
                    prowizja = avg_price * 0.11
                    zysk_jednostkowy = avg_price - koszt - prowizja
                    roi = zysk_jednostkowy / koszt
                    margin_score = min(max(roi / 1.5, 0.0), 1.0)  # 150% ROI = max
                else:
                    margin_score = 0.3

                # Dostępność (im mniej zostało, tym pilniejsze do uzupełnienia)
                if velocity > 0 and ilosc > 0:
                    days_left = (ilosc / velocity) * 30
                    if days_left < 14:
                        urgency_score = 0.9  # krytyczne — kończy się
                    elif days_left < 30:
                        urgency_score = 0.7
                    elif days_left < 60:
                        urgency_score = 0.5
                    else:
                        urgency_score = 0.3
                elif ilosc == 0:
                    urgency_score = 0.1  # brak towaru
                else:
                    urgency_score = 0.4

                # Opportunity score — ważona suma
                opportunity = (
                    velocity_score * 0.35 +
                    trend_score    * 0.25 +
                    margin_score   * 0.30 +
                    urgency_score  * 0.10
                )

                if opportunity < min_score:
                    continue

                # Notatki
                notes_parts = []
                if sold_30 > sold_prev_30 * 1.2:
                    notes_parts.append(f"📈 Trend wzrostowy (+{int((sold_30/max(sold_prev_30,0.1)-1)*100)}%)")
                if velocity > 10:
                    notes_parts.append(f"🔥 {velocity:.1f} szt/mies")
                if margin_score > 0.6:
                    notes_parts.append(f"💰 Dobra marża")
                if ilosc < velocity * 1.5 and ilosc > 0:
                    notes_parts.append(f"⚠️ Kończy się ({ilosc} szt, ~{int(days_left if velocity>0 else 99)} dni)")
                if ilosc == 0:
                    notes_parts.append("❌ Brak w magazynie")
                notes = " | ".join(notes_parts) if notes_parts else f"Sprzedano {sold_90} szt w 90 dni"

                # Szacowane URL Allegro (z naszej oferty)
                marketplace_url = None
                try:
                    offer_row = conn.execute(
                        "SELECT allegro_offer_id FROM produkty WHERE id=?", (pid,)
                    ).fetchone()
                    if offer_row and offer_row[0]:
                        marketplace_url = f"https://allegro.pl/oferta/{offer_row[0]}"
                except Exception:
                    pass

                # Zapisz do DB
                conn.execute("""
                    INSERT INTO winning_products
                        (source, external_id, name, category, category_id, marketplace_url,
                         my_product_id, est_price, est_monthly_sales, est_margin,
                         trend_score, competition_score, opportunity_score,
                         notes, batch_id, created_at)
                    VALUES ('internal', ?, ?, ?, 'own', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(pid), nazwa[:500], kategoria, marketplace_url,
                    pid, avg_price, velocity,
                    round(margin_score, 3),
                    round(trend_score, 3),
                    round(velocity_score, 3),
                    round(opportunity, 3),
                    notes, batch_id, now
                ))

                products_found += 1
                all_results.append({
                    "name": nazwa[:80],
                    "opportunity_score": opportunity,
                    "trend_score": trend_score,
                    "competition_score": velocity_score,
                    "est_price": avg_price,
                })

            except Exception as pe:
                logger.warning(f"[winning_analyzer] Błąd scoringu produktu {row.get('produkt_id')}: {pe}")
                continue

        conn.commit()

        duration_s = round(time.time() - start_time, 2)
        finished_at = datetime.now().isoformat(sep=" ", timespec="seconds")

        conn.execute(
            "UPDATE winning_products_meta SET status='done', finished_at=?, products_found=? WHERE batch_id=?",
            (finished_at, products_found, batch_id)
        )
        conn.commit()
        set_config("winning_last_run", datetime.now().isoformat())

        all_results.sort(key=lambda x: x["opportunity_score"], reverse=True)
        logger.info(f"[winning_analyzer] Batch {batch_id}: znaleziono {products_found} produktów w {duration_s}s")

        return {
            "batch_id": batch_id,
            "products_found": products_found,
            "duration_s": duration_s,
            "top_3": all_results[:3],
        }

    except Exception as e:
        try:
            conn.execute(
                "UPDATE winning_products_meta SET status='error', error_msg=?, finished_at=? WHERE batch_id=?",
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
        result = allegro_request("GET", f"/sale/categories/{category_id}")
        if isinstance(result, tuple):
            response, err = result
        else:
            response, err = result, None
        if response and isinstance(response, dict) and "name" in response:
            return response["name"]
    except Exception:
        pass
    return f"Kategoria {category_id}"


def get_winning_products(
    limit: int = 50,
    offset: int = 0,
    min_score: float = 0.0,
    batch_id: str | None = None,
    include_ignored: bool = False,
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

        if not include_ignored:
            where_clauses.append("(ignored IS NULL OR ignored = 0)")

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


def toggle_ignore(item_id: int) -> bool:
    """Toggle flagi ignored dla produktu. Zwraca nowy stan (True=ignorowany)."""
    try:
        from modules.database import get_db
        conn = get_db()
        row = conn.execute("SELECT ignored FROM winning_products WHERE id=?", (item_id,)).fetchone()
        if not row:
            return False
        new_val = 0 if row[0] else 1
        conn.execute("UPDATE winning_products SET ignored=? WHERE id=?", (new_val, item_id))
        conn.commit()
        return bool(new_val)
    except Exception as e:
        logger.error(f"[winning_analyzer] toggle_ignore error: {e}")
        return False


def add_to_zakupy_lista(item_id: int) -> dict:
    """Dodaje produkt do listy zakupów. Zwraca słownik z wynikiem."""
    try:
        from modules.database import get_db
        conn = get_db()
        row = conn.execute(
            "SELECT id, name, category, est_price, notes FROM winning_products WHERE id=?",
            (item_id,)
        ).fetchone()
        if not row:
            return {"success": False, "error": "Nie znaleziono produktu"}

        # Sprawdź czy już na liście
        existing = conn.execute(
            "SELECT id FROM zakupy_lista WHERE winning_product_id=? AND status NOT IN ('received','skipped')",
            (item_id,)
        ).fetchone()
        if existing:
            return {"success": False, "error": "Produkt już jest na liście zakupów", "already": True}

        conn.execute(
            """INSERT INTO zakupy_lista (winning_product_id, name, category, est_price, notes, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'new', datetime('now'))""",
            (item_id, row['name'], row['category'], row['est_price'], row['notes'])
        )
        conn.commit()
        return {"success": True, "name": row['name']}
    except Exception as e:
        logger.error(f"[winning_analyzer] add_to_zakupy_lista error: {e}")
        return {"success": False, "error": str(e)}


def get_zakupy_lista(status_filter: str | None = None) -> list[dict]:
    """Pobiera listę zakupów."""
    try:
        from modules.database import get_db
        conn = get_db()
        where = ""
        params = []
        if status_filter:
            where = "WHERE z.status = ?"
            params = [status_filter]
        rows = conn.execute(
            f"""SELECT z.*, w.opportunity_score, w.marketplace_url
                FROM zakupy_lista z
                LEFT JOIN winning_products w ON z.winning_product_id = w.id
                {where}
                ORDER BY z.created_at DESC""",
            params
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[winning_analyzer] get_zakupy_lista error: {e}")
        return []


def update_zakupy_status(item_id: int, status: str) -> bool:
    """Aktualizuje status pozycji na liście zakupów."""
    try:
        from modules.database import get_db
        conn = get_db()
        conn.execute("UPDATE zakupy_lista SET status=? WHERE id=?", (status, item_id))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"[winning_analyzer] update_zakupy_status error: {e}")
        return False


# Inicjalizuj tabele przy imporcie modułu
try:
    init_winning_tables()
except Exception as _init_e:
    logger.warning(f"[winning_analyzer] Nie można zainicjalizować tabel przy imporcie: {_init_e}")
