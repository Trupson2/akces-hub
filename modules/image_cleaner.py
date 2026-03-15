# -*- coding: utf-8 -*-
"""
Czyszczenie zdjec produktowych z napisow/overlayow
Uzywa Gemini 2.5 Flash Image do natywnej edycji zdjec
"""

import os
import time
import tempfile
import requests
from PIL import Image
from io import BytesIO

from .database import get_db

# Gemini API
try:
    from google import genai
    from google.genai import types
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from gemini_config import GEMINI_API_KEY
    _client = genai.Client(api_key=GEMINI_API_KEY)
    GEMINI_AVAILABLE = True
except Exception as e:
    print(f"[image_cleaner] Gemini niedostepny: {e}")
    GEMINI_AVAILABLE = False
    _client = None


CLEAN_PROMPT = (
    "I am the product seller. This is an Amazon product listing photo. "
    "I need a clean version for my own store. "
    "\n\n"
    "STEP 1 — IDENTIFY what is DIGITALLY OVERLAID on the photo (not part of the real product):\n"
    "These are things like: German/Chinese/English marketing text floating over the image, "
    "Amazon watermarks, store logos in corners, promotional badges (Bestseller, Prime, etc.), "
    "price tags, checkmarks, X marks, comparison arrows, numbered callout bubbles, "
    "info panels, spec tables overlaid on the image, colored/dark backgrounds.\n"
    "\n"
    "STEP 2 — REMOVE only those digital overlays. Replace background with PURE WHITE (#FFFFFF).\n"
    "If the image is a multi-panel infographic, extract ONLY the main product photo.\n"
    "\n"
    "STEP 3 — PRESERVE everything that is PHYSICALLY PART of the real product:\n"
    "- Embroidered/sewn logos, crests, patches, emblems on fabric\n"
    "- Printed brand names ON the product body (e.g. 'Nike' on a shoe)\n"
    "- Engraved/molded text on plastic or metal\n"
    "- Decorative stitching, contrast seams, piping, accent stripes\n"
    "- Buttons, buckles, zippers, clips, hardware\n"
    "- Material textures, patterns, perforations, mesh\n"
    "- Color variations, panels, design elements of the product\n"
    "- Labels, tags physically attached to the product\n"
    "\n"
    "KEY RULE: If text/logo is ON the product surface (sewn, printed, engraved) — KEEP IT. "
    "If text/logo is FLOATING OVER the photo (added digitally) — REMOVE IT. "
    "When in doubt, KEEP IT — it is better to leave a detail than to erase part of the product.\n"
    "\n"
    "Output: the product centered on pure white background, all real details intact."
)


def clean_image_from_url(image_url, max_dim=1024):
    """
    Czysci zdjecie z URL - usuwa napisy, strzalki, overlaye.
    Zwraca (cleaned_image_bytes, mime_type, error)
    """
    if not GEMINI_AVAILABLE:
        return None, None, "Gemini API niedostepne"

    try:
        # Pobierz zdjecie
        resp = requests.get(image_url, timeout=30)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert('RGB')

        return _clean_image(img, max_dim)
    except Exception as e:
        return None, None, str(e)


def clean_image_from_bytes(image_bytes, max_dim=1024):
    """
    Czysci zdjecie z bytes.
    Zwraca (cleaned_image_bytes, mime_type, error)
    """
    if not GEMINI_AVAILABLE:
        return None, None, "Gemini API niedostepne"

    try:
        img = Image.open(BytesIO(image_bytes)).convert('RGB')
        return _clean_image(img, max_dim)
    except Exception as e:
        return None, None, str(e)


def _clean_image(img, max_dim=1024):
    """
    Wewnetrzna funkcja czyszczenia.
    Zwraca (cleaned_image_bytes, mime_type, error)
    """
    start_time = time.time()

    # Zmniejsz jesli za duze (oszczednosc tokenow)
    if max(img.width, img.height) > max_dim:
        ratio = max_dim / max(img.width, img.height)
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    # Zapisz tymczasowo (bezpieczne na Pi i Windows)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='_clean.jpg')
        os.close(fd)
        img.save(tmp_path, 'JPEG', quality=90)

        uploaded = _client.files.upload(file=tmp_path)

        response = _client.models.generate_content(
            model='gemini-2.5-flash-image',
            contents=[CLEAN_PROMPT, uploaded],
            config=types.GenerateContentConfig(
                response_modalities=['TEXT', 'IMAGE'],
            )
        )

        # Loguj koszt Gemini
        _log_usage(response, time.time() - start_time)

        if response is None:
            return None, None, "Gemini zwrocil None"

        # Wyciagnij zdjecie z odpowiedzi
        if response.candidates and len(response.candidates) > 0:
            candidate = response.candidates[0]
            if candidate and candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if part.inline_data and part.inline_data.mime_type and part.inline_data.mime_type.startswith('image/'):
                        return part.inline_data.data, part.inline_data.mime_type, None

        # Brak zdjecia - sprawdz tekst
        text_response = ""
        try:
            if response.candidates and len(response.candidates) > 0:
                candidate = response.candidates[0]
                if candidate and candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.text:
                            text_response = part.text
        except Exception:
            pass

        return None, None, f"Gemini nie zwrocil zdjecia. Odpowiedz: {text_response[:200]}"

    except Exception as e:
        return None, None, str(e)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def _log_usage(response, elapsed_sec):
    """Loguje uzycie Gemini do monitor_stats"""
    try:
        from .pallet_monitor import log_gemini_usage
        log_gemini_usage(response, 'image_clean')
    except Exception:
        pass


def needs_cleaning(image_url):
    """
    Szybko sprawdza czy zdjecie ma overlaye do usuniecia.
    Uzywa Gemini Vision (tani, bez edycji).
    Zwraca True/False
    """
    if not GEMINI_AVAILABLE:
        return False

    try:
        resp = requests.get(image_url, timeout=15)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert('RGB')

        # Zmniejsz mocno dla szybkiej analizy
        max_dim = 512
        if max(img.width, img.height) > max_dim:
            ratio = max_dim / max(img.width, img.height)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)

        tmp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_tmp_check.jpg')
        img.save(tmp_path, 'JPEG', quality=75)

        uploaded = _client.files.upload(file=tmp_path)

        response = _client.models.generate_content(
            model='gemini-2.0-flash',
            contents=[
                "Does this product photo have any watermarks, store logos, promotional text, "
                "badges, or foreign characters (e.g., Chinese/Asian text) that should be removed? "
                "Do NOT count arrows, dimension lines, or measurement numbers as things to remove. "
                "Answer only YES or NO.",
                uploaded
            ]
        )

        os.remove(tmp_path)

        # Loguj
        try:
            from .pallet_monitor import log_gemini_usage
            log_gemini_usage(response, 'image_check')
        except Exception:
            pass

        if response.candidates:
            text = response.candidates[0].content.parts[0].text.strip().upper()
            return text.startswith('YES')

        return False
    except Exception:
        return False
