# -*- coding: utf-8 -*-
"""
Photo Daemon — operacje bazodanowe.
Obsługuje tabele photo_jobs i processed_photos w akces_hub.db.
Działa zarówno ze świeżą bazą jak i istniejącą bazą Akces Hub.
"""

import sqlite3
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_db_path: str | None = None


def set_db_path(path: str) -> None:
    """Ustawia ścieżkę do bazy danych."""
    global _db_path
    _db_path = path


def _get_conn() -> sqlite3.Connection:
    """Otwiera połączenie do bazy danych."""
    global _db_path
    if not _db_path:
        raise RuntimeError("Ścieżka do bazy danych nie jest ustawiona. Wywołaj set_db_path() lub init_tables(db_path).")

    conn = sqlite3.connect(_db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")   # 60s — Flask i worker mogą pisać równocześnie
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    return conn


def init_tables(db_path: str | None = None) -> None:
    """
    Tworzy tabele photo_jobs i processed_photos jeśli nie istnieją.
    Dodaje kolumny images_ready i photo_job_id do produkty (jeśli tabela istnieje).

    Args:
        db_path: Ścieżka do akces_hub.db. Jeśli None, używa wcześniej ustawionej ścieżki.
    """
    global _db_path

    if db_path:
        set_db_path(db_path)

    if not _db_path:
        raise ValueError("Ścieżka do bazy danych jest wymagana.")

    # Upewnij się że katalog istnieje
    db_dir = os.path.dirname(_db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = _get_conn()
    try:
        # Tabela zleceń photo processing
        conn.execute("""
            CREATE TABLE IF NOT EXISTS photo_jobs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                original_path TEXT NOT NULL,
                work_path    TEXT,
                product_id   INTEGER NULL,
                sku          TEXT NULL,
                status       TEXT NOT NULL DEFAULT 'new',
                error_msg    TEXT NULL,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            )
        """)

        # Tabela przetworzonych zdjęć (warianty)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_photos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      INTEGER NOT NULL REFERENCES photo_jobs(id),
                product_id  INTEGER NULL,
                sku         TEXT NULL,
                variant     TEXT NOT NULL,
                path        TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)

        # Indeksy
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_photo_jobs_status
                ON photo_jobs(status)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_photo_jobs_sku
                ON photo_jobs(sku)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_processed_photos_product
                ON processed_photos(product_id, variant)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_processed_photos_job
                ON processed_photos(job_id)
        """)

        # Migracje do tabeli produkty (jeśli tabela istnieje)
        try:
            conn.execute("ALTER TABLE produkty ADD COLUMN images_ready INTEGER DEFAULT 0")
            logger.info("[db_utils] Dodano kolumnę produkty.images_ready")
        except sqlite3.OperationalError:
            pass  # Kolumna już istnieje lub tabela nie istnieje

        try:
            conn.execute("ALTER TABLE produkty ADD COLUMN photo_job_id INTEGER NULL")
            logger.info("[db_utils] Dodano kolumnę produkty.photo_job_id")
        except sqlite3.OperationalError:
            pass  # Kolumna już istnieje lub tabela nie istnieje

        conn.commit()
        logger.info(f"[db_utils] Tabele zainicjalizowane w {_db_path}")

    except Exception as e:
        logger.error(f"[db_utils] Błąd inicjalizacji tabel: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    """Zwraca aktualny czas jako ISO string."""
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def create_job(original_path: str, sku: str | None = None, product_id: int | None = None) -> int:
    """
    Tworzy nowe zlecenie przetwarzania zdjęcia.

    Args:
        original_path: Ścieżka do oryginalnego pliku
        sku: SKU produktu (opcjonalne)
        product_id: ID produktu w bazie (opcjonalne)

    Returns:
        ID nowego zlecenia
    """
    conn = _get_conn()
    try:
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO photo_jobs (original_path, sku, product_id, status, created_at, updated_at)
            VALUES (?, ?, ?, 'new', ?, ?)
            """,
            (original_path, sku, product_id, now, now)
        )
        conn.commit()
        job_id = cur.lastrowid
        logger.debug(f"[db_utils] Utworzono job #{job_id} dla {original_path}")
        return job_id
    except Exception as e:
        logger.error(f"[db_utils] Błąd tworzenia joba: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def update_job_status(
    job_id: int,
    status: str,
    error_msg: str | None = None,
    work_path: str | None = None
) -> None:
    """
    Aktualizuje status zlecenia.

    Args:
        job_id: ID zlecenia
        status: Nowy status (new|processing|done|error)
        error_msg: Komunikat błędu (opcjonalny)
        work_path: Ścieżka do pliku roboczego (opcjonalny)
    """
    conn = _get_conn()
    try:
        now = _now()
        if work_path is not None:
            conn.execute(
                """
                UPDATE photo_jobs
                SET status=?, error_msg=?, work_path=?, updated_at=?
                WHERE id=?
                """,
                (status, error_msg, work_path, now, job_id)
            )
        else:
            conn.execute(
                """
                UPDATE photo_jobs
                SET status=?, error_msg=?, updated_at=?
                WHERE id=?
                """,
                (status, error_msg, now, job_id)
            )
        conn.commit()
        logger.debug(f"[db_utils] Job #{job_id} -> status={status}")
    except Exception as e:
        logger.error(f"[db_utils] Błąd aktualizacji joba #{job_id}: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def get_pending_jobs(limit: int = 10) -> list[dict]:
    """
    Pobiera zlecenia do przetworzenia (status='new').

    Args:
        limit: Maksymalna liczba zleceń

    Returns:
        Lista słowników z danymi zleceń
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, original_path, work_path, product_id, sku, status, error_msg,
                   created_at, updated_at,
                   COALESCE(image_index, 0) as image_index
            FROM photo_jobs
            WHERE status = 'new'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"[db_utils] Błąd pobierania pending jobs: {e}")
        return []
    finally:
        conn.close()


def save_processed_photo(
    job_id: int,
    product_id: int | None,
    sku: str | None,
    variant: str,
    path: str
) -> int:
    """
    Zapisuje informację o przetworzonym zdjęciu (wariancie).

    Args:
        job_id: ID zlecenia
        product_id: ID produktu (opcjonalne)
        sku: SKU produktu (opcjonalne)
        variant: Nazwa wariantu (allegro_main|vinted|thumb)
        path: Ścieżka do pliku

    Returns:
        ID nowego rekordu
    """
    conn = _get_conn()
    try:
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO processed_photos (job_id, product_id, sku, variant, path, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, product_id, sku, variant, path, now)
        )
        conn.commit()
        return cur.lastrowid
    except Exception as e:
        logger.error(f"[db_utils] Błąd zapisu processed_photo dla job #{job_id}: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def get_job(job_id: int) -> dict | None:
    """
    Pobiera szczegóły zlecenia.

    Args:
        job_id: ID zlecenia

    Returns:
        Słownik z danymi zlecenia lub None
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            """
            SELECT id, original_path, work_path, product_id, sku, status, error_msg, created_at, updated_at
            FROM photo_jobs
            WHERE id = ?
            """,
            (job_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"[db_utils] Błąd pobierania joba #{job_id}: {e}")
        return None
    finally:
        conn.close()


def get_job_photos(job_id: int) -> list[dict]:
    """
    Pobiera przetworzone zdjęcia dla danego zlecenia.

    Args:
        job_id: ID zlecenia

    Returns:
        Lista wariantów zdjęć
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, job_id, product_id, sku, variant, path, created_at
            FROM processed_photos
            WHERE job_id = ?
            ORDER BY variant
            """,
            (job_id,)
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"[db_utils] Błąd pobierania zdjęć dla job #{job_id}: {e}")
        return []
    finally:
        conn.close()


def get_stats() -> dict:
    """
    Zwraca statystyki zleceń (liczba per status).

    Returns:
        Słownik: {status: count, ...}
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT status, COUNT(*) as cnt
            FROM photo_jobs
            GROUP BY status
            """
        ).fetchall()
        stats = {"new": 0, "processing": 0, "done": 0, "error": 0, "total": 0}
        for row in rows:
            status = row["status"]
            count = row["cnt"]
            stats[status] = count
            stats["total"] += count
        # Zlicz przetworzone zdjęcia
        photos_count = conn.execute("SELECT COUNT(*) FROM processed_photos").fetchone()[0]
        stats["photos_total"] = photos_count
        return stats
    except Exception as e:
        logger.error(f"[db_utils] Błąd pobierania statystyk: {e}")
        return {"new": 0, "processing": 0, "done": 0, "error": 0, "total": 0, "photos_total": 0}
    finally:
        conn.close()


def get_recent_jobs(limit: int = 50) -> list[dict]:
    """
    Pobiera ostatnie zlecenia.

    Args:
        limit: Maksymalna liczba wyników

    Returns:
        Lista słowników z danymi zleceń (od najnowszych)
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, original_path, work_path, product_id, sku, status, error_msg, created_at, updated_at
            FROM photo_jobs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"[db_utils] Błąd pobierania ostatnich jobów: {e}")
        return []
    finally:
        conn.close()


def resolve_product_id(sku: str) -> int | None:
    """
    Próbuje znaleźć product_id na podstawie SKU.
    Szuka w tabeli produkty: po EAN, po nazwie (LIKE), po kodzie_magazynowym lub po ID.

    Args:
        sku: SKU do wyszukania

    Returns:
        product_id lub None jeśli nie znaleziono
    """
    conn = _get_conn()
    try:
        # 1. Sprawdź czy tabela produkty istnieje
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='produkty'"
        ).fetchone()
        if not table_exists:
            return None

        # 2. Szukaj po EAN
        row = conn.execute(
            "SELECT id FROM produkty WHERE ean = ? LIMIT 1",
            (sku,)
        ).fetchone()
        if row:
            return row["id"]

        # 3. Szukaj po kodzie magazynowym
        row = conn.execute(
            "SELECT id FROM produkty WHERE kod_magazynowy = ? LIMIT 1",
            (sku,)
        ).fetchone()
        if row:
            return row["id"]

        # 4. Szukaj po nazwie (LIKE)
        row = conn.execute(
            "SELECT id FROM produkty WHERE nazwa LIKE ? LIMIT 1",
            (f"%{sku}%",)
        ).fetchone()
        if row:
            return row["id"]

        # 5. Spróbuj jako numeryczny ID
        try:
            pid = int(sku)
            row = conn.execute("SELECT id FROM produkty WHERE id = ? LIMIT 1", (pid,)).fetchone()
            if row:
                return row["id"]
        except (ValueError, TypeError):
            pass

        return None

    except Exception as e:
        logger.error(f"[db_utils] Błąd szukania product_id dla sku={sku}: {e}")
        return None
    finally:
        conn.close()


def check_job_exists_by_path(original_path: str) -> bool:
    """
    Sprawdza czy job dla danej ścieżki już istnieje (idempotentność watchera).

    Args:
        original_path: Ścieżka do pliku źródłowego

    Returns:
        True jeśli job już istnieje
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM photo_jobs WHERE original_path = ? LIMIT 1",
            (original_path,)
        ).fetchone()
        return row is not None
    except Exception as e:
        logger.error(f"[db_utils] Błąd sprawdzania ścieżki: {e}")
        return False
    finally:
        conn.close()


def update_product_images(product_id: int, new_paths: list[str], images_ready: bool = True) -> None:
    """
    Aktualizuje pole images w tabeli produkty (dodaje nowe ścieżki do tablicy JSON).

    Args:
        product_id: ID produktu
        new_paths: Lista nowych ścieżek do dodania
        images_ready: Czy ustawić images_ready=1
    """
    if not new_paths:
        return

    import json

    conn = _get_conn()
    try:
        # Sprawdź czy tabela produkty istnieje i ma kolumnę images
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='produkty'"
        ).fetchone()
        if not table_exists:
            logger.warning("[db_utils] Tabela produkty nie istnieje — pomijam aktualizację images")
            return

        # Pobierz aktualne images
        row = conn.execute(
            "SELECT images FROM produkty WHERE id = ?",
            (product_id,)
        ).fetchone()
        if not row:
            logger.warning(f"[db_utils] Produkt #{product_id} nie istnieje")
            return

        try:
            current_images = json.loads(row["images"] or "[]")
            if not isinstance(current_images, list):
                current_images = []
        except (json.JSONDecodeError, TypeError):
            current_images = []

        # Dodaj nowe ścieżki (bez duplikatów)
        for path in new_paths:
            if path not in current_images:
                current_images.append(path)

        ready_val = 1 if images_ready else 0
        conn.execute(
            "UPDATE produkty SET images = ?, images_ready = ? WHERE id = ?",
            (json.dumps(current_images), ready_val, product_id)
        )
        conn.commit()
        logger.info(f"[db_utils] Zaktualizowano images dla produktu #{product_id}: {len(current_images)} zdjęć")

    except Exception as e:
        logger.error(f"[db_utils] Błąd aktualizacji images dla produktu #{product_id}: {e}")
        conn.rollback()
    finally:
        conn.close()
