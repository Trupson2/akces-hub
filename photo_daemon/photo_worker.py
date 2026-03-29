#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Photo Daemon — worker przetwarzający zlecenia.
Pipeline: load → fix orientation → crop → enhance → bg remove → warianty → zapis

Użycie:
    python photo_worker.py [--config config.yaml]

Idempotentny — bezpieczne uruchamianie wielokrotnie (cron co minutę).
"""

import argparse
import logging
import os
import sys
import traceback
from pathlib import Path

# Dodaj katalog photo_daemon do ścieżki
sys.path.insert(0, str(Path(__file__).parent))

from config import load_config, get_full_config
import db_utils
import image_utils
from external_api_client import ComfyUIClient

# ============================================================
# KONFIGURACJA LOGOWANIA
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("photo_worker")


def process_job(job: dict, cfg: dict, comfy_client: ComfyUIClient) -> bool:
    """
    Przetwarza jedno zlecenie photo.

    Pipeline:
      1. Ustaw status=processing
      2. Wczytaj obraz
      3. Fix orientation (EXIF)
      4. Crop do aspect ratio (jeśli włączone)
      5. Enhance (brightness/contrast)
      6. Zapisz plik roboczy work_{job_id}.jpg
      7. ComfyUI bg removal → work_bg_{job_id}.jpg (fallback: bez bg removal)
      8. Generuj warianty (allegro_main, vinted, thumb)
      9. Zapisz przetworzone zdjęcia do DB
      10. Aktualizuj produkty.images (jeśli jest product_id)
      11. Ustaw status=done

    Args:
        job: Słownik z danymi zlecenia
        cfg: Pełna konfiguracja (sekcja photo_daemon)
        comfy_client: Klient ComfyUI

    Returns:
        True jeśli sukces
    """
    job_id = job["id"]
    original_path = job["original_path"]
    sku = job.get("sku")
    product_id = job.get("product_id")
    image_index = job.get("image_index", 0) or 0  # 0=miniaturka, 1+=galeria

    proc = cfg.get("processing", {})
    workdir = cfg.get("workdir_path", "/tmp")
    processed_base = cfg.get("processed_base_path", "/tmp/processed")

    logger.info(f"[worker] Processing job #{job_id}: {original_path} (sku={sku}, product_id={product_id})")

    # Krok 1: Status → processing
    db_utils.update_job_status(job_id, "processing")

    try:
        # Krok 2: Pobierz z URL jeśli original_path to link (Amazon itp.)
        local_path = original_path
        _tmp_file = None
        if original_path and (
            original_path.startswith("http://")
            or original_path.startswith("https://")
            or original_path.startswith("//")
            or "amazon" in original_path
        ):
            import requests as _requests
            import tempfile as _tempfile
            full_url = original_path if original_path.startswith("http") else "https://" + original_path
            logger.info(f"[worker] Job #{job_id}: pobieranie zdjęcia z URL: {full_url[:100]}")
            r = _requests.get(
                full_url,
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            r.raise_for_status()
            ext = ".jpg"
            ct = r.headers.get("Content-Type", "")
            if "png" in ct:
                ext = ".png"
            elif "webp" in ct:
                ext = ".webp"
            _tmp = _tempfile.NamedTemporaryFile(suffix=ext, delete=False)
            _tmp.write(r.content)
            _tmp.close()
            local_path = _tmp.name
            _tmp_file = _tmp.name
            logger.info(f"[worker] Job #{job_id}: zapisano do {local_path} ({len(r.content)//1024} KB)")

        # Wczytaj obraz
        img = image_utils.load_image(local_path)
        if img is None:
            raise RuntimeError(f"Nie można wczytać pliku: {local_path}")
        logger.info(f"[worker] Job #{job_id}: wczytano {img.size} {img.mode}")

        # Krok 3: Fix orientation
        img = image_utils.fix_orientation(img)
        logger.debug(f"[worker] Job #{job_id}: po fix_orientation: {img.size}")

        # Krok 4: Crop do aspect ratio
        # Miniaturka (index=0): crop do 1:1 (wymóg Allegro — białe tło, kwadrat)
        # Galeria (index>0): NIE cropujemy — zachowujemy oryginalne proporcje
        crop_enabled = proc.get("crop_enabled", True) and (image_index == 0)
        aspect_ratio = proc.get("target_aspect_ratio", "1:1")
        if crop_enabled:
            img = image_utils.crop_to_aspect(img, aspect_ratio)
            logger.debug(f"[worker] Job #{job_id}: po crop {aspect_ratio}: {img.size}")

        # Krok 5: Enhance
        brightness = float(proc.get("brightness", 1.05))
        contrast = float(proc.get("contrast", 1.10))
        img = image_utils.enhance_image(img, brightness=brightness, contrast=contrast)
        logger.debug(f"[worker] Job #{job_id}: po enhance (b={brightness}, c={contrast})")

        # Krok 6: Zapisz plik roboczy
        os.makedirs(workdir, exist_ok=True)
        work_path = str(Path(workdir) / f"work_{job_id}.jpg")
        jpeg_quality = int(proc.get("jpeg_quality", 90))

        if not image_utils.save_jpeg(img, work_path, quality=jpeg_quality):
            raise RuntimeError(f"Nie można zapisać pliku roboczego: {work_path}")

        db_utils.update_job_status(job_id, "processing", work_path=work_path)
        logger.info(f"[worker] Job #{job_id}: zapisano work: {work_path}")

        # Katalog docelowy: processed/{sku lub product_id}/
        folder_name = sku if sku else (str(product_id) if product_id else f"job_{job_id}")
        # Dla galerii: osobny subfolder żeby nie nadpisywać miniaturki
        if image_index == 0:
            variant_dir = Path(processed_base) / folder_name
        else:
            variant_dir = Path(processed_base) / folder_name / f"gallery_{image_index}"
        os.makedirs(str(variant_dir), exist_ok=True)

        # ── Krok 7: Przetwarzanie zależne od image_index ──────────────────
        if image_index == 0:
            # ── MINIATURKA (index=0) ──
            # Pipeline: bg removal → białe tło 1:1 → allegro_main/thumb/vinted
            bg_removed_path = str(Path(workdir) / f"work_bg_{job_id}.jpg")
            bg_success = comfy_client.remove_background(work_path, bg_removed_path)

            if bg_success and os.path.exists(bg_removed_path):
                source_for_variants = bg_removed_path
                logger.info(f"[worker] Job #{job_id}: bg removal OK -> {bg_removed_path}")
            else:
                logger.warning(f"[worker] Job #{job_id}: bg removal nie powiódł się — używam oryginalnego")
                source_for_variants = work_path

            img_for_variants = image_utils.load_image(source_for_variants)
            if img_for_variants is None:
                logger.warning(f"[worker] Job #{job_id}: nie można wczytać source — używam work_path")
                img_for_variants = img

            # Miniaturka Allegro: 1200×1200, białe tło, najwyższa jakość
            variants_config = [
                ("allegro_main", proc.get("allegro_size", [1200, 1200]), int(proc.get("jpeg_quality", 90))),
                ("thumb",        proc.get("thumb_size",   [300, 300]),   int(proc.get("thumb_quality", 80))),
                ("vinted",       proc.get("vinted_size",  [800, 800]),   int(proc.get("vinted_quality", 85))),
            ]

        else:
            # ── GALERIA (index=1..7) ──
            # Pipeline: usuwanie tekstu (jeśli włączone) → 2560×2560
            text_skip = proc.get("text_removal_skip", False) or cfg.get("processing", {}).get("text_removal_skip", False)
            text_skip = bool(cfg.get("external_api", {}).get("text_removal_skip", text_skip))

            if text_skip:
                logger.info(f"[worker] Job #{job_id}[{image_index}]: text removal pominięte (text_removal_skip=true)")
                source_for_variants = work_path
            else:
                text_removed_path = str(Path(workdir) / f"work_txt_{job_id}_{image_index}.jpg")
                txt_success = comfy_client.remove_text(work_path, text_removed_path)

                if txt_success and os.path.exists(text_removed_path):
                    source_for_variants = text_removed_path
                    logger.info(f"[worker] Job #{job_id}[{image_index}]: text removal OK -> {text_removed_path}")
                else:
                    logger.warning(f"[worker] Job #{job_id}[{image_index}]: text removal nie powiódł się — używam oryginalnego")
                    source_for_variants = work_path

            img_for_variants = image_utils.load_image(source_for_variants)
            if img_for_variants is None:
                logger.warning(f"[worker] Job #{job_id}: nie można wczytać source — używam work_path")
                img_for_variants = img

            # Galeria Allegro: max 2560×2560, oryginalne proporcje (padding biały, bez crop)
            gallery_variant = f"allegro_gallery_{image_index}"
            variants_config = [
                (gallery_variant, proc.get("gallery_size", [2560, 2560]), int(proc.get("jpeg_quality", 90))),
            ]

        # ── Krok 8: Generuj warianty i zapisz ──
        saved_paths = []
        for variant_name, size, quality in variants_config:
            size_tuple = (int(size[0]), int(size[1]))
            variant_img = image_utils.resize_variant(img_for_variants, size_tuple)
            variant_path = str(variant_dir / f"{variant_name}.jpg")

            if image_utils.save_jpeg(variant_img, variant_path, quality=quality):
                # Krok 9: Zapisz w DB
                db_utils.save_processed_photo(
                    job_id=job_id,
                    product_id=product_id,
                    sku=sku,
                    variant=variant_name,
                    path=variant_path,
                )
                saved_paths.append(variant_path)
                logger.info(f"[worker] Job #{job_id}: wariant {variant_name} -> {variant_path}")
            else:
                logger.error(f"[worker] Job #{job_id}: błąd zapisu wariantu {variant_name}")

        # Krok 10: Aktualizuj produkty.images
        if product_id and saved_paths:
            required_count = cfg.get("required_photo_count", 1)
            images_ready = len(saved_paths) >= required_count
            db_utils.update_product_images(product_id, saved_paths, images_ready=images_ready)
            logger.info(f"[worker] Job #{job_id}: zaktualizowano images dla produkt #{product_id}")

        # Krok 11: Status → done
        db_utils.update_job_status(job_id, "done")
        logger.info(f"[worker] Job #{job_id}: DONE ({len(saved_paths)} wariantów)")

        # Usuń tymczasowy plik jeśli był pobrany z URL
        if _tmp_file:
            try:
                os.remove(_tmp_file)
            except Exception:
                pass

        return True

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[worker] Job #{job_id}: BŁĄD: {error_msg}")
        logger.debug(traceback.format_exc())
        try:
            db_utils.update_job_status(job_id, "error", error_msg=error_msg[:1000])
        except Exception as db_e:
            logger.error(f"[worker] Nie można zaktualizować statusu błędu: {db_e}")
        # Usuń tymczasowy plik jeśli był pobrany z URL
        try:
            if _tmp_file:
                os.remove(_tmp_file)
        except Exception:
            pass
        return False


def run_worker(cfg: dict) -> dict:
    """
    Główna pętla workera — przetwarza pending joby.

    Args:
        cfg: Pełna konfiguracja photo_daemon

    Returns:
        Statystyki: {processed, success, failed}
    """
    max_jobs = int(cfg.get("max_jobs_per_run", 10))
    db_path = cfg.get("db_path", "")

    if not db_path:
        logger.error("[worker] Brak db_path w konfiguracji!")
        return {"processed": 0, "success": 0, "failed": 0}

    # Inicjalizuj tabele
    db_utils.init_tables(db_path)

    # Inicjalizuj ComfyUI client
    api_cfg = cfg.get("external_api", {})
    comfy_client = ComfyUIClient(api_cfg)

    if not comfy_client.mock_mode:
        if not comfy_client.health_check():
            logger.warning("[worker] ComfyUI niedostępne! Zlecenia będą przetwarzane bez bg removal.")

    # Pobierz pending joby
    jobs = db_utils.get_pending_jobs(limit=max_jobs)

    if not jobs:
        logger.info("[worker] Brak zleceń do przetworzenia")
        return {"processed": 0, "success": 0, "failed": 0}

    logger.info(f"[worker] Znaleziono {len(jobs)} zleceń do przetworzenia")

    processed = 0
    success = 0
    failed = 0

    for job in jobs:
        processed += 1
        if process_job(job, cfg, comfy_client):
            success += 1
        else:
            failed += 1

    logger.info(f"[worker] Podsumowanie: przetworzono={processed}, sukces={success}, błędy={failed}")
    return {"processed": processed, "success": success, "failed": failed}


def main():
    parser = argparse.ArgumentParser(
        description="Photo Daemon Worker — przetwarza zlecenia zdjęciowe"
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

    logger.info("[worker] Startowanie Photo Worker...")
    stats = run_worker(cfg)

    print(f"\nWorker zakończony: processed={stats['processed']}, "
          f"success={stats['success']}, failed={stats['failed']}")


if __name__ == "__main__":
    main()
