# -*- coding: utf-8 -*-
"""
Czyszczenie zdjec produktowych - usuwanie tla
Priorytet: VPS rembg > lokalne rembg > Gemini (fallback)
"""

import os
import time
import requests
from PIL import Image
from io import BytesIO

from .database import get_db, get_config

# === REMBG (lokalne, darmowe) ===
try:
    from rembg import remove as rembg_remove
    REMBG_AVAILABLE = True
    print("[image_cleaner] rembg dostepny (lokalne usuwanie tla)")
except ImportError:
    REMBG_AVAILABLE = False
    print("[image_cleaner] rembg niedostepny — pip install rembg[cpu]")

# === Gemini jako fallback ===
GEMINI_AVAILABLE = False
_client = None
try:
    from google import genai
    from google.genai import types
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from gemini_config import GEMINI_API_KEY
    _client = genai.Client(api_key=GEMINI_API_KEY)
    GEMINI_AVAILABLE = True
except Exception as e:
    print(f"[image_cleaner] Gemini niedostepny (fallback): {e}")


def _get_vps_url():
    """Zwraca URL rembg VPS lub '' jesli nie skonfigurowany"""
    try:
        return get_config('rembg_vps_url', '')
    except Exception:
        return ''


def clean_image_from_url(image_url, max_dim=1024):
    """
    Czysci zdjecie z URL - usuwa tlo, stawia na bialym tle.
    Zwraca (cleaned_image_bytes, mime_type, error)
    """
    try:
        resp = requests.get(image_url, timeout=30)
        resp.raise_for_status()
        return clean_image_from_bytes(resp.content, max_dim)
    except Exception as e:
        return None, None, str(e)


def clean_image_from_bytes(image_bytes, max_dim=1024):
    """
    Czysci zdjecie z bytes - usuwa tlo.
    Kolejnosc: VPS rembg > lokalne rembg > Gemini
    Zwraca (cleaned_image_bytes, mime_type, error)
    """
    # 1. VPS rembg (najszybciej, nie grzeje Pi)
    vps_url = _get_vps_url()
    if vps_url:
        result, mime, err = _clean_vps(image_bytes, max_dim)
        if result:
            return result, mime, err
        print(f"[image_cleaner] VPS niedostepny, fallback: {err}")

    # 2. Lokalne rembg (darmowe, ale grzeje Pi)
    if REMBG_AVAILABLE:
        return _clean_rembg(image_bytes, max_dim)

    # 3. Gemini (platne)
    if GEMINI_AVAILABLE:
        return _clean_gemini(image_bytes, max_dim)

    return None, None, "Brak backendu: skonfiguruj VPS lub zainstaluj rembg"


def _clean_vps(image_bytes, max_dim=1024):
    """Usuwanie tla przez zdalny VPS rembg service"""
    try:
        start = time.time()
        vps_url = _get_vps_url().rstrip('/')
        vps_key = ''
        try:
            vps_key = get_config('rembg_vps_key', '')
        except Exception:
            pass

        headers = {}
        if vps_key:
            headers['X-API-Key'] = vps_key

        resp = requests.post(
            f'{vps_url}/remove-bg',
            files={'image': ('photo.jpg', image_bytes, 'image/jpeg')},
            params={'max_dim': max_dim},
            headers=headers,
            timeout=90  # rembg moze trwac do 60s na tanim VPS
        )
        resp.raise_for_status()

        if resp.headers.get('Content-Type', '').startswith('image/'):
            elapsed = time.time() - start
            proc_time = resp.headers.get('X-Processing-Time', '?')
            print(f"[image_cleaner] VPS rembg: OK ({elapsed:.1f}s, serwer: {proc_time})")
            return resp.content, 'image/jpeg', None

        # Serwer zwrocil blad JSON
        try:
            err = resp.json().get('error', 'Unknown VPS error')
        except Exception:
            err = f'VPS HTTP {resp.status_code}'
        return None, None, err

    except requests.exceptions.ConnectionError:
        return None, None, "VPS nieosiagalny (connection refused)"
    except requests.exceptions.Timeout:
        return None, None, "VPS timeout (>90s)"
    except Exception as e:
        return None, None, f"VPS error: {str(e)}"


def _clean_rembg(image_bytes, max_dim=1024):
    """Usuwanie tla przez rembg (lokalne, darmowe)"""
    try:
        start = time.time()

        # Na Pi zmniejsz do 512 (szybciej, mniej RAM/CPU)
        rembg_dim = min(max_dim, 512)

        # Otworz i zmniejsz jesli za duze
        img = Image.open(BytesIO(image_bytes)).convert('RGBA')
        if max(img.width, img.height) > rembg_dim:
            ratio = rembg_dim / max(img.width, img.height)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)

        # Usun tlo
        img_bytes = BytesIO()
        img.save(img_bytes, format='PNG')
        img_bytes.seek(0)

        result_bytes = rembg_remove(img_bytes.read())
        result_img = Image.open(BytesIO(result_bytes)).convert('RGBA')

        # Biale tlo
        white_bg = Image.new('RGBA', result_img.size, (255, 255, 255, 255))
        white_bg.paste(result_img, mask=result_img.split()[3])
        final = white_bg.convert('RGB')

        # Zapisz jako JPEG
        output = BytesIO()
        final.save(output, format='JPEG', quality=92)
        output.seek(0)

        elapsed = time.time() - start
        print(f"[image_cleaner] rembg lokalne: OK ({elapsed:.1f}s)")

        return output.read(), 'image/jpeg', None

    except Exception as e:
        print(f"[image_cleaner] rembg error: {e}")
        # Fallback na Gemini
        if GEMINI_AVAILABLE:
            return _clean_gemini(image_bytes, max_dim)
        return None, None, f"rembg error: {str(e)}"


def _clean_gemini(image_bytes, max_dim=1024):
    """Fallback: usuwanie tla przez Gemini (platne)"""
    import tempfile

    CLEAN_PROMPT = (
        "Remove the background from this product photo. "
        "Place the product centered on a PURE WHITE (#FFFFFF) background. "
        "Remove any watermarks, store logos, promotional text, badges. "
        "Keep all physical product details intact. "
        "Output: product on pure white background."
    )

    try:
        start = time.time()
        img = Image.open(BytesIO(image_bytes)).convert('RGB')

        if max(img.width, img.height) > max_dim:
            ratio = max_dim / max(img.width, img.height)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)

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

            # Loguj koszt
            try:
                from .pallet_monitor import log_gemini_usage
                log_gemini_usage(response, 'image_clean')
            except Exception:
                pass

            if response and response.candidates:
                candidate = response.candidates[0]
                if candidate and candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.inline_data and part.inline_data.mime_type and part.inline_data.mime_type.startswith('image/'):
                            elapsed = time.time() - start
                            print(f"[image_cleaner] Gemini fallback: OK ({elapsed:.1f}s)")
                            return part.inline_data.data, part.inline_data.mime_type, None

            return None, None, "Gemini nie zwrocil zdjecia"

        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    except Exception as e:
        return None, None, f"Gemini error: {str(e)}"


def needs_cleaning(image_url):
    """
    Sprawdza czy zdjecie wymaga czyszczenia (overlaye, napisy).
    Jesli rembg/VPS dostepny — zawsze True (darmowe).
    """
    if REMBG_AVAILABLE or _get_vps_url():
        return True  # rembg jest darmowy, wiec czysc wszystko

    if not GEMINI_AVAILABLE:
        return False

    try:
        resp = requests.get(image_url, timeout=15)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert('RGB')

        max_dim = 512
        if max(img.width, img.height) > max_dim:
            ratio = max_dim / max(img.width, img.height)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)

        import tempfile
        fd, tmp_path = tempfile.mkstemp(suffix='_check.jpg')
        os.close(fd)
        img.save(tmp_path, 'JPEG', quality=75)

        uploaded = _client.files.upload(file=tmp_path)

        from .utils import get_gemini_model
        response = _client.models.generate_content(
            model=get_gemini_model(),
            contents=[
                "Does this product photo have any watermarks, store logos, promotional text, "
                "badges, or foreign characters that should be removed? "
                "Answer only YES or NO.",
                uploaded
            ]
        )

        os.remove(tmp_path)

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
