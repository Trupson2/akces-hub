# -*- coding: utf-8 -*-
"""
Photo Daemon — narzędzia do przetwarzania obrazów (Pillow).
Wszystkie funkcje logują błędy i nigdy nie crashują.
"""

import logging
import os
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageEnhance, ImageOps, ExifTags
    PIL_AVAILABLE = True
except ImportError:
    logger.error("[image_utils] Pillow nie jest zainstalowana! pip install Pillow")
    PIL_AVAILABLE = False


def _check_pil():
    if not PIL_AVAILABLE:
        raise ImportError("Pillow nie jest zainstalowana. Zainstaluj: pip install Pillow")


def fix_orientation(img: "Image.Image") -> "Image.Image":
    """
    Automatyczna rotacja obrazu na podstawie danych EXIF.
    Eliminuje problem obróconych zdjęć z telefonu.

    Args:
        img: Obraz PIL

    Returns:
        Obraz po korekcji orientacji
    """
    _check_pil()
    try:
        # Metoda 1: ImageOps.exif_transpose (Pillow >= 7.0)
        img = ImageOps.exif_transpose(img)
        return img
    except Exception:
        pass

    # Metoda 2: Ręczna korekcja z EXIF
    try:
        exif_data = img._getexif()
        if not exif_data:
            return img

        # Znajdź tag Orientation
        orientation_tag = None
        for tag, name in ExifTags.TAGS.items():
            if name == "Orientation":
                orientation_tag = tag
                break

        if orientation_tag is None or orientation_tag not in exif_data:
            return img

        orientation = exif_data[orientation_tag]
        rotations = {
            3: 180,
            6: 270,
            8: 90,
        }
        flips = {
            2: Image.FLIP_LEFT_RIGHT,
            4: Image.FLIP_TOP_BOTTOM,
            5: Image.FLIP_LEFT_RIGHT,
            7: Image.FLIP_LEFT_RIGHT,
        }

        if orientation in rotations:
            img = img.rotate(rotations[orientation], expand=True)
        elif orientation in flips:
            img = img.transpose(flips[orientation])
            if orientation in (5, 7):
                img = img.rotate(90 if orientation == 5 else 270, expand=True)

        return img

    except Exception as e:
        logger.debug(f"[image_utils] Nie można skorygować orientacji EXIF: {e}")
        return img


def crop_to_aspect(img: "Image.Image", ratio_str: str = "1:1") -> "Image.Image":
    """
    Centralne przycinanie obrazu do podanego aspect ratio.

    Args:
        img: Obraz PIL
        ratio_str: Aspect ratio jako "W:H" (np. "1:1", "4:3", "16:9")

    Returns:
        Przyciany obraz
    """
    _check_pil()
    try:
        # Parsuj ratio
        parts = ratio_str.strip().split(":")
        if len(parts) != 2:
            logger.warning(f"[image_utils] Nieprawidłowy format ratio: {ratio_str}")
            return img

        ratio_w = float(parts[0])
        ratio_h = float(parts[1])

        if ratio_w <= 0 or ratio_h <= 0:
            return img

        orig_w, orig_h = img.size
        target_ratio = ratio_w / ratio_h
        current_ratio = orig_w / orig_h

        if abs(current_ratio - target_ratio) < 0.01:
            # Już w dobrym ratio
            return img

        if current_ratio > target_ratio:
            # Za szeroki — przytnij boki
            new_w = int(orig_h * target_ratio)
            new_h = orig_h
        else:
            # Za wysoki — przytnij górę/dół
            new_w = orig_w
            new_h = int(orig_w / target_ratio)

        # Środkowe kadrowanie
        left = (orig_w - new_w) // 2
        top = (orig_h - new_h) // 2
        right = left + new_w
        bottom = top + new_h

        return img.crop((left, top, right, bottom))

    except Exception as e:
        logger.error(f"[image_utils] Błąd crop_to_aspect: {e}")
        return img


def enhance_image(
    img: "Image.Image",
    brightness: float = 1.05,
    contrast: float = 1.10
) -> "Image.Image":
    """
    Poprawia jasność i kontrast obrazu.

    Args:
        img: Obraz PIL
        brightness: Współczynnik jasności (1.0 = bez zmiany)
        contrast: Współczynnik kontrastu (1.0 = bez zmiany)

    Returns:
        Obraz po poprawie jakości
    """
    _check_pil()
    try:
        if brightness != 1.0:
            enhancer = ImageEnhance.Brightness(img)
            img = enhancer.enhance(brightness)

        if contrast != 1.0:
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(contrast)

        return img

    except Exception as e:
        logger.error(f"[image_utils] Błąd enhance_image: {e}")
        return img


def resize_variant(
    img: "Image.Image",
    size: Tuple[int, int],
    bg_color: Tuple[int, int, int] = (255, 255, 255)
) -> "Image.Image":
    """
    Zmienia rozmiar obrazu z zachowaniem proporcji.
    Jeśli obraz ma kanał alfa (RGBA/PNG), wkleja na białe tło.
    Wynikowy obraz ma dokładnie rozmiar size (padding z białym tłem).

    Args:
        img: Obraz PIL
        size: Docelowy rozmiar (width, height)
        bg_color: Kolor tła dla obszarów padding (RGB)

    Returns:
        Obraz w docelowym rozmiarze na białym tle
    """
    _check_pil()
    try:
        target_w, target_h = size

        # Konwertuj RGBA/PA do RGB z białym tłem
        if img.mode in ("RGBA", "LA", "PA"):
            background = Image.new("RGB", img.size, bg_color)
            if img.mode == "PA":
                img = img.convert("RGBA")
            mask = img.split()[-1] if img.mode == "RGBA" else img.split()[-1]
            background.paste(img.convert("RGB"), mask=mask)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Oblicz rozmiar z zachowaniem proporcji (thumbnail)
        orig_w, orig_h = img.size
        ratio = min(target_w / orig_w, target_h / orig_h)
        new_w = int(orig_w * ratio)
        new_h = int(orig_h * ratio)

        img_resized = img.resize((new_w, new_h), Image.LANCZOS)

        # Utwórz finalne tło i wycentruj
        result = Image.new("RGB", size, bg_color)
        x_offset = (target_w - new_w) // 2
        y_offset = (target_h - new_h) // 2
        result.paste(img_resized, (x_offset, y_offset))

        return result

    except Exception as e:
        logger.error(f"[image_utils] Błąd resize_variant: {e}")
        # Zwróć białe tło jako fallback
        try:
            return Image.new("RGB", size, bg_color)
        except Exception:
            return img


def save_jpeg(img: "Image.Image", path: str, quality: int = 90) -> bool:
    """
    Zapisuje obraz jako JPEG.

    Args:
        img: Obraz PIL
        path: Ścieżka docelowa
        quality: Jakość JPEG (0-95)

    Returns:
        True jeśli sukces, False jeśli błąd
    """
    _check_pil()
    try:
        # Upewnij się że katalog istnieje
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

        # Konwertuj do RGB jeśli potrzeba
        if img.mode != "RGB":
            if img.mode in ("RGBA", "LA", "PA"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "PA":
                    img = img.convert("RGBA")
                mask = img.split()[-1]
                background.paste(img.convert("RGB"), mask=mask)
                img = background
            else:
                img = img.convert("RGB")

        img.save(path, format="JPEG", quality=quality, optimize=True)
        logger.debug(f"[image_utils] Zapisano JPEG: {path} (quality={quality})")
        return True

    except Exception as e:
        logger.error(f"[image_utils] Błąd zapisu JPEG {path}: {e}")
        return False


def load_image(path: str) -> "Image.Image | None":
    """
    Wczytuje obraz z pliku.

    Args:
        path: Ścieżka do pliku

    Returns:
        Obraz PIL lub None jeśli błąd
    """
    _check_pil()
    try:
        if not os.path.exists(path):
            logger.error(f"[image_utils] Plik nie istnieje: {path}")
            return None

        img = Image.open(path)
        img.load()  # Wymuś wczytanie (ważne dla plików tymczasowych)
        logger.debug(f"[image_utils] Wczytano obraz: {path} ({img.size}, {img.mode})")
        return img

    except Exception as e:
        logger.error(f"[image_utils] Błąd wczytywania obrazu {path}: {e}")
        return None


if __name__ == "__main__":
    # Prosty test
    logging.basicConfig(level=logging.DEBUG)

    if not PIL_AVAILABLE:
        print("Pillow nie jest zainstalowana!")
    else:
        print(f"Pillow dostępna: {Image.__version__}")
        # Test tworzenia obrazu testowego
        test_img = Image.new("RGB", (800, 600), (128, 128, 200))
        print(f"Test image: {test_img.size}, mode={test_img.mode}")

        # Test crop
        cropped = crop_to_aspect(test_img, "1:1")
        print(f"Po crop 1:1: {cropped.size}")

        # Test enhance
        enhanced = enhance_image(test_img, brightness=1.1, contrast=1.2)
        print(f"Po enhance: {enhanced.size}")

        # Test resize variant
        resized = resize_variant(test_img, (300, 300))
        print(f"Po resize 300x300: {resized.size}")

        print("Wszystkie testy OK!")
