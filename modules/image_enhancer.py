# -*- coding: utf-8 -*-
"""
Generator 8 zdjec produktowych dla Allegro
Kazde zdjecie ma inny styl/szablon zgodny z regulaminem Allegro.

Szablon 1 (miniaturka): Czyste biale tlo, SAM produkt, ZERO tekstu/strzalek
Szablony 2-8 (galeria): Wymiary, detale, lifestyle, warianty, montaz, itp.

Uzywa Gemini 2.5 Flash Image do natywnej edycji/generacji zdjec.
Koszt: ~$0.001 per zdjecie = ~$0.008 za komplet 8 zdjec.
"""

import os
import time
import tempfile
from PIL import Image
from io import BytesIO

# rembg (lokalne, darmowe)
try:
    from rembg import remove as _rembg_remove
    REMBG_AVAILABLE = True
    print("[image_enhancer] rembg dostepny")
except ImportError:
    REMBG_AVAILABLE = False

# Gemini API (fallback / AI szablony)
try:
    from google import genai
    from google.genai import types
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from gemini_config import GEMINI_API_KEY
    _client = genai.Client(api_key=GEMINI_API_KEY)
    GEMINI_AVAILABLE = True
except Exception as e:
    print(f"[image_enhancer] Gemini niedostepny: {e}")
    GEMINI_AVAILABLE = False
    _client = None

# Jesli rembg dostepny — traktuj enhancer jako dostepny (miniaturki rembg)
if REMBG_AVAILABLE and not GEMINI_AVAILABLE:
    GEMINI_AVAILABLE = True  # hack: enhance worker sprawdza ta flage


# === HYBRID MODE ===
# Oryginalne zdjecia z Amazona (wyczyszczone z logo) = sloty na czyste zdjecia
# AI-generowane = wymiary, uzycie, lifestyle, skala
# Dzieki temu klient widzi PRAWDZIWY produkt na glownych zdjeciach

# Kolejnosc slotow w trybie hybrydowym:
HYBRID_ORIGINAL_SLOTS = ['mini', 'det', 'zest', 'kat2']  # Sloty na oryginaly
HYBRID_AI_TEMPLATES = [
    (2, 'wym'),      # Wymiary — AI dorysowuje linie pomiarowe
    (6, 'uzycie'),   # W uzyciu — AI generuje kontekst
    (7, 'life'),     # Lifestyle — AI generuje scenerie
]


def is_clean_product_photo(img_bytes):
    """
    Sprawdza czy zdjecie to czyste zdjecie produktowe (biale tlo)
    czy infografika (kolorowe tlo, tekst, ikony).

    Heurystyka: sprawdza brzegi zdjecia — czyste produktowe maja biale tlo.
    Infografiki maja kolorowe/ciemne tlo, gradient, tekst.

    Returns: True jesli czyste zdjecie produktowe, False jesli infografika
    """
    try:
        img = Image.open(BytesIO(img_bytes)).convert('RGB')
        w, h = img.size

        # Sprawdz sample pikseli na brzegach (top, bottom, left, right)
        border_pixels = []
        sample_size = 20  # pikseli od kazdego brzegu

        for x in range(0, w, max(1, w // 30)):
            # Gora
            for y in range(0, min(sample_size, h)):
                border_pixels.append(img.getpixel((x, y)))
            # Dol
            for y in range(max(0, h - sample_size), h):
                border_pixels.append(img.getpixel((x, y)))

        for y in range(0, h, max(1, h // 30)):
            # Lewo
            for x in range(0, min(sample_size, w)):
                border_pixels.append(img.getpixel((x, y)))
            # Prawo
            for x in range(max(0, w - sample_size), w):
                border_pixels.append(img.getpixel((x, y)))

        if not border_pixels:
            return True

        # Ile pikseli jest bialych/jasnych (R>220, G>220, B>220)?
        white_count = sum(1 for r, g, b in border_pixels if r > 220 and g > 220 and b > 220)
        white_ratio = white_count / len(border_pixels)

        # Czyste zdjecie produktowe: >60% bialych pikseli na brzegach
        # Infografika: kolorowe tlo, ciemne tlo, gradient
        return white_ratio > 0.55

    except Exception:
        return True  # W razie bledu — traktuj jako ok


def prepare_original_photo(img_bytes, max_dim=2560):
    """
    Przygotowuje oryginalne zdjecie do Allegro:
    - Upscale do 2560x2560
    - JPEG quality 95
    Zwraca (processed_bytes, error)
    """
    try:
        img = Image.open(BytesIO(img_bytes)).convert('RGB')

        # Upscale/downscale do max_dim
        if max(img.width, img.height) != max_dim:
            ratio = max_dim / max(img.width, img.height)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)

        buf = BytesIO()
        img.save(buf, 'JPEG', quality=95)
        return buf.getvalue(), None
    except Exception as e:
        return None, str(e)


# === 8 SZABLONOW ALLEGRO ===

TEMPLATES = [
    {
        "id": 1,
        "name": "miniaturka",
        "label": "Zdjecie glowne (miniaturka)",
        "prompt": (
            "I am the product seller. Create a clean e-commerce main photo. "
            "Place ONLY the product on a PURE WHITE background (#FFFFFF). "
            "Remove ALL watermarks, store logos, badges, price tags, promotional overlays, arrows, dimensions. "
            "KEEP any text or branding that is PHYSICALLY PRINTED ON THE PRODUCT ITSELF "
            "(product labels, model names molded into the product, brand names on the product body). "
            "The product must be centered, well-lit, and fill about 80% of the frame. "
            "NO added text, NO annotations, NO measurements, NO decorative elements. "
            "Just the product on white with its original markings. Professional studio quality lighting."
        ),
    },
    {
        "id": 2,
        "name": "wymiary",
        "label": "Wymiary produktu",
        "prompt": (
            "I am the product seller. Create a technical dimensions photo for my product listing. "
            "Show the product on a clean light/white background with professional dimension lines "
            "and arrows showing the main measurements (height, width, depth) in centimeters. "
            "Use thin black lines with arrows at both ends for dimension indicators. "
            "Place measurement values (numbers + 'cm') next to each dimension line. "
            "Keep it clean and technical - NO marketing text, NO slogans, NO logos. "
            "Only the product with its physical dimensions clearly marked."
        ),
    },
    {
        "id": 3,
        "name": "detale",
        "label": "Zbliżenie na detale",
        "prompt": (
            "I am the product seller. Create a detail/close-up photo showing the key features "
            "and build quality of this product. Show a close-up perspective highlighting: "
            "material texture, buttons/ports/connectors, finish quality, important functional elements. "
            "Clean background (white or very light gray gradient). "
            "ABSOLUTELY NO text, NO labels, NO annotations, NO arrows, NO overlays of any kind. "
            "Just a clean close-up photo of the product details. Pure product photography."
        ),
    },
    {
        "id": 4,
        "name": "zawartosc",
        "label": "Zawartosc zestawu",
        "prompt": (
            "I am the product seller. Create a 'what's in the box' flat-lay photo. "
            "Look carefully at the original photo — identify ALL items/components visible in it "
            "(main product, cables, adapters, bags, manuals, screws, mounts, etc.). "
            "Arrange all those components in an organized flat-lay style on a white background. "
            "Show each item separately so the buyer can see exactly what they will receive. "
            "If the original photo shows accessories or multiple parts, include ALL of them. "
            "NO text, NO labels, NO annotations, NO arrows. "
            "Just the actual items laid out cleanly. Professional overhead photography style."
        ),
    },
    {
        "id": 5,
        "name": "kat2",
        "label": "Drugi kąt / perspektywa",
        "prompt": (
            "I am the product seller. Create a product photo from a DIFFERENT ANGLE than the main photo. "
            "Show the product from a 3/4 view, side view, or back view on a clean white background. "
            "Reveal parts of the product not visible in the front photo — back panel, side profile, bottom. "
            "Professional studio lighting, clean white background. "
            "ABSOLUTELY NO text, NO labels, NO annotations, NO arrows, NO overlays of any kind. "
            "Just the product from a different angle. Pure product photography."
        ),
    },
    {
        "id": 6,
        "name": "uzycie",
        "label": "Produkt w uzyciu",
        "prompt": (
            "I am the product seller. Create a photo showing this product INSTALLED in its natural setting. "
            "Show the product mounted, installed, or placed where it would be used "
            "(in a car, on a desk, in a kitchen, on a wall, etc. as appropriate). "
            "DO NOT add any people, hands, fingers, body parts, or human figures. "
            "Just the product in its environment — no people at all. "
            "Natural lighting, realistic setting, professional photography quality. "
            "ABSOLUTELY NO text, NO labels, NO arrows, NO annotations, NO overlays."
        ),
    },
    {
        "id": 7,
        "name": "lifestyle",
        "label": "Zdjecie lifestyle",
        "prompt": (
            "I am the product seller. Create a lifestyle photo showing this product "
            "placed in a beautiful, realistic everyday environment. "
            "Show the product in context — in a stylish room, modern car interior, "
            "clean workspace, etc. as appropriate for this product type. "
            "DO NOT add any people, hands, fingers, body parts, or human figures. "
            "Just the product in a nice setting — no people at all. "
            "Natural lighting, warm tones, professional interior/product photography. "
            "NO text overlays, NO labels, NO marketing slogans, NO logos."
        ),
    },
    {
        "id": 8,
        "name": "porownanie",
        "label": "Porownanie / skala",
        "prompt": (
            "I am the product seller. Create a scale/comparison photo that helps the buyer "
            "understand the actual size of this product. "
            "Show the product next to a common everyday object for scale reference "
            "(e.g., a smartphone, a coin, a ruler, a standard bottle, a pen). "
            "DO NOT use hands or any body parts for scale — use OBJECTS only. "
            "Clean white or light background. Both objects clearly visible. "
            "You may add a subtle dimension line showing the product's main dimension. "
            "NO marketing text, NO slogans, NO logos, NO prices, NO people."
        ),
    },
]


def get_templates():
    """Zwraca liste dostepnych szablonow"""
    return TEMPLATES


def enhance_single(img_bytes, template_id, product_name=None, max_dim=2560):
    """
    Generuje jedno zdjecie wg szablonu.
    Szablon 1 (miniaturka) — rembg (lokalne, darmowe)
    Szablony 2-8 — Gemini Image API (fallback)

    Args:
        img_bytes: bytes zdjecia zrodlowego (juz wyczyszczonego)
        template_id: int 1-8
        product_name: opcjonalnie nazwa produktu (dla lepszego kontekstu)
        max_dim: max wymiar

    Returns:
        (image_bytes, mime_type, error)
    """
    # Szablon 1 (miniaturka) — uzyj rembg (darmowe) jesli dostepny
    if template_id == 1:
        try:
            from .image_cleaner import REMBG_AVAILABLE
            if REMBG_AVAILABLE:
                from .image_cleaner import clean_image_from_bytes
                result_bytes, mime, err = clean_image_from_bytes(img_bytes, max_dim)
                if result_bytes:
                    return result_bytes, mime, None
                # Jesli rembg zawiodl — sprobuj Gemini
                print(f"[enhance] rembg fallback: {err}")
        except Exception as e:
            print(f"[enhance] rembg import error: {e}")

    if not GEMINI_AVAILABLE:
        return None, None, "Gemini API niedostepne (rembg niedostepny dla tego szablonu)"

    template = None
    for t in TEMPLATES:
        if t["id"] == template_id:
            template = t
            break

    if not template:
        return None, None, f"Nieznany szablon: {template_id}"

    tmp_path = None
    try:
        img = Image.open(BytesIO(img_bytes)).convert('RGB')

        # Zmniejsz
        if max(img.width, img.height) > max_dim:
            ratio = max_dim / max(img.width, img.height)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)

        # Zapisz tymczasowo w systemowym /tmp (bezpieczne na Pi i Windows)
        fd, tmp_path = tempfile.mkstemp(suffix=f'_enh_{template_id}.jpg')
        os.close(fd)
        img.save(tmp_path, 'JPEG', quality=90)

        uploaded = _client.files.upload(file=tmp_path)

        # Dodaj kontekst produktu jesli mamy nazwe
        prompt = template["prompt"]
        if product_name:
            prompt = f"Product: {product_name}. " + prompt

        response = _client.models.generate_content(
            model='gemini-2.5-flash-image',
            contents=[prompt, uploaded],
            config=types.GenerateContentConfig(
                response_modalities=['TEXT', 'IMAGE'],
            )
        )

        if response is None:
            return None, None, f"Gemini zwrocil None ({template['name']})"

        # Loguj koszt
        _log_usage(response, template["name"])

        # Wyciagnij zdjecie
        if response.candidates and len(response.candidates) > 0:
            candidate = response.candidates[0]
            if candidate and candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if part.inline_data and part.inline_data.mime_type and part.inline_data.mime_type.startswith('image/'):
                        return part.inline_data.data, part.inline_data.mime_type, None

        # Brak zdjecia — wyciagnij tekst bledu
        text_resp = ""
        try:
            if response.candidates and len(response.candidates) > 0:
                candidate = response.candidates[0]
                if candidate and candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.text:
                            text_resp = part.text
        except Exception:
            pass

        return None, None, f"Brak zdjecia ({template['name']}): {text_resp[:200]}"

    except Exception as e:
        return None, None, str(e)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def enhance_all(img_bytes, product_name=None, template_ids=None, max_dim=2560):
    """
    Generuje wszystkie (lub wybrane) warianty zdjec.

    Args:
        img_bytes: bytes zdjecia zrodlowego
        product_name: nazwa produktu
        template_ids: lista ID szablonow (domyslnie 1-8)
        max_dim: max wymiar

    Returns:
        list of dict: [{"template_id": 1, "name": "miniaturka", "data": bytes, "mime": str, "error": str}, ...]
    """
    if template_ids is None:
        template_ids = [1, 2, 3, 4, 5, 6, 7, 8]

    results = []
    for tid in template_ids:
        template = None
        for t in TEMPLATES:
            if t["id"] == tid:
                template = t
                break

        if not template:
            results.append({
                "template_id": tid,
                "name": "unknown",
                "data": None,
                "mime": None,
                "error": f"Nieznany szablon: {tid}"
            })
            continue

        print(f"[enhance] Generuje {tid}/8: {template['label']}...")
        data, mime, error = enhance_single(img_bytes, tid, product_name, max_dim)

        results.append({
            "template_id": tid,
            "name": template["name"],
            "label": template["label"],
            "data": data,
            "mime": mime,
            "error": error
        })

        # Maly delay zeby nie overloadowac API
        if data and tid < max(template_ids):
            time.sleep(1)

    return results


def _log_usage(response, template_name):
    """Loguje uzycie Gemini"""
    try:
        from .pallet_monitor import log_gemini_usage
        log_gemini_usage(response, f'image_enhance_{template_name}')
    except Exception:
        pass
