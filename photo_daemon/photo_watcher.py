#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Photo Daemon — watcher INBOX.
Skanuje katalog inbox, rejestruje nowe zdjęcia jako zlecenia.

Użycie:
    python photo_watcher.py [--config config.yaml]

Idempotentny — bezpieczne uruchamianie wielokrotnie (cron co minutę).
"""

import argparse
import logging
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

# Dodaj katalog photo_daemon do ścieżki
sys.path.insert(0, str(Path(__file__).parent))

from config import load_config, get_config, get_full_config
import db_utils

# ============================================================
# KONFIGURACJA LOGOWANIA
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("photo_watcher")

# Obsługiwane rozszerzenia
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def parse_sku_from_filename(filename: str) -> str:
    """
    Parsuje SKU z nazwy pliku.

    Obsługiwane formaty:
    - "{SKU}_{numer}.{ext}"  → np. "DYSON-V11_1.jpg" → SKU="DYSON-V11"
    - "{SKU}_{tekst}.{ext}"  → np. "DYSON-V11_front.jpg" → SKU="DYSON-V11"
    - "{SKU}.{ext}"          → np. "DYSON-V11.jpg" → SKU="DYSON-V11"

    Args:
        filename: Nazwa pliku (bez katalogu)

    Returns:
        SKU (bez rozszerzenia i numeru)
    """
    stem = Path(filename).stem  # Bez rozszerzenia

    # Usuń końcowy _<liczba> lub _<tekst>
    # np. "DYSON-V11_1" → "DYSON-V11", "ABC_front" → "ABC"
    match = re.match(r"^(.+?)_(\d+|front|back|side|top|bottom|detail|\d+[a-z]*)$", stem, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return stem.strip()


def get_safe_filename(sku: str, original_ext: str) -> str:
    """
    Tworzy bezpieczną, unikalną nazwę pliku dla archiwum.

    Format: {sku}_{uuid4_short}{ext}

    Args:
        sku: SKU produktu
        original_ext: Oryginalne rozszerzenie (np. ".jpg")

    Returns:
        Bezpieczna nazwa pliku
    """
    # Sanityzuj SKU (usuń niebezpieczne znaki)
    safe_sku = re.sub(r"[^\w\-]", "_", sku)
    short_uuid = uuid.uuid4().hex[:8]
    return f"{safe_sku}_{short_uuid}{original_ext.lower()}"


def scan_inbox(inbox_path: str, originals_path: str, db_path: str) -> tuple[int, int]:
    """
    Skanuje INBOX i rejestruje nowe pliki jako zlecenia.

    Args:
        inbox_path: Ścieżka do katalogu INBOX
        originals_path: Ścieżka do archiwum oryginałów
        db_path: Ścieżka do bazy danych

    Returns:
        (pliki_znalezione, zlecenia_zarejestrowane)
    """
    if not os.path.exists(inbox_path):
        logger.warning(f"[watcher] Katalog INBOX nie istnieje: {inbox_path}")
        os.makedirs(inbox_path, exist_ok=True)
        logger.info(f"[watcher] Stworzono katalog INBOX: {inbox_path}")
        return 0, 0

    os.makedirs(originals_path, exist_ok=True)

    # Inicjalizuj DB
    db_utils.init_tables(db_path)

    found = 0
    registered = 0
    errors = 0

    # Skanuj pliki w INBOX (nie rekurencyjnie)
    inbox_dir = Path(inbox_path)
    all_files = sorted(inbox_dir.iterdir())

    for file_path in all_files:
        if not file_path.is_file():
            continue

        ext = file_path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            logger.debug(f"[watcher] Pomijam plik o nieobsługiwanym rozszerzeniu: {file_path.name}")
            continue

        found += 1
        filename = file_path.name

        # Parsuj SKU
        sku = parse_sku_from_filename(filename)

        # Stwórz bezpieczną nazwę w archiwum
        safe_name = get_safe_filename(sku, ext)
        archive_path = str(Path(originals_path) / safe_name)

        # Sprawdź czy job z tą nazwą pliku już istnieje
        # Sprawdzamy zarówno po oryginalnej ścieżce jak i po ścieżce w archiwum
        if db_utils.check_job_exists_by_path(str(file_path)) or \
           db_utils.check_job_exists_by_path(archive_path):
            logger.debug(f"[watcher] Plik już zarejestrowany: {filename}")
            continue

        # Sprawdź też po basename (dodatkowe zabezpieczenie)
        existing = _check_sku_already_in_progress(sku)
        # Nie blokujemy na podstawie SKU - można mieć wiele jobów dla tego samego SKU

        # Spróbuj znaleźć produkt w bazie
        product_id = db_utils.resolve_product_id(sku)
        if product_id:
            logger.info(f"[watcher] Znaleziono produkt #{product_id} dla sku={sku}")
        else:
            logger.info(f"[watcher] Nie znaleziono produktu dla sku={sku} (job bez powiązania)")

        try:
            # Przenieś plik do archiwum oryginałów
            shutil.move(str(file_path), archive_path)
            logger.debug(f"[watcher] Przeniesiono: {filename} -> {archive_path}")

            # Zarejestruj zlecenie
            job_id = db_utils.create_job(
                original_path=archive_path,
                sku=sku,
                product_id=product_id
            )

            pid_str = f"product_id={product_id}" if product_id else "product_id=None"
            print(f"Registered job #{job_id} for {filename} (sku={sku}, {pid_str})")
            registered += 1

        except Exception as e:
            logger.error(f"[watcher] Błąd rejestracji {filename}: {e}", exc_info=True)
            errors += 1

    return found, registered


def _check_sku_already_in_progress(sku: str) -> bool:
    """Placeholder — nie blokujemy per SKU."""
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Photo Daemon Watcher — skanuje INBOX i rejestruje zlecenia"
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config.yaml"),
        help="Ścieżka do pliku config.yaml"
    )
    args = parser.parse_args()

    # Załaduj konfigurację
    cfg = load_config(args.config)

    # Skonfiguruj logowanie
    log_level = cfg.get("log_level", "INFO").upper()
    logging.getLogger().setLevel(getattr(logging, log_level, logging.INFO))

    inbox_path = cfg.get("inbox_path", "")
    originals_path = cfg.get("originals_path", "")
    db_path = cfg.get("db_path", "")

    if not inbox_path or not originals_path or not db_path:
        logger.error("[watcher] Brakujące ścieżki w konfiguracji (inbox_path, originals_path, db_path)")
        sys.exit(1)

    logger.info(f"[watcher] Skanowanie INBOX: {inbox_path}")
    logger.info(f"[watcher] Archiwum oryginałów: {originals_path}")
    logger.info(f"[watcher] Baza danych: {db_path}")

    found, registered = scan_inbox(inbox_path, originals_path, db_path)

    print(f"\nFound {found} new files, registered {registered} jobs")

    if found == 0:
        logger.info("[watcher] Brak nowych plików w INBOX")
    elif registered == 0 and found > 0:
        logger.info("[watcher] Wszystkie pliki już zarejestrowane (idempotent)")


if __name__ == "__main__":
    main()
