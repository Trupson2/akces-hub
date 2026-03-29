# -*- coding: utf-8 -*-
"""
Photo Daemon — klient ComfyUI do usuwania tła ze zdjęć.
Obsługuje tryb mock (kopiuje plik bez zmian) i tryb realny (ComfyUI API).

ComfyUI flow:
  1. POST /upload/image  → przesyłamy plik, dostajemy filename
  2. POST /prompt        → wysyłamy workflow JSON z tym filename, dostajemy prompt_id
  3. GET  /history/{id}  → polling aż status != 'pending'
  4. GET  /view?filename=X&type=output → pobieramy wynikowy plik
"""

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class ComfyUIClient:
    """
    Klient do komunikacji z ComfyUI API.
    Obsługuje asynchroniczne zlecenia usuwania tła.
    """

    def __init__(self, config: dict):
        """
        Args:
            config: Sekcja external_api z config.yaml (słownik)
        """
        self.base_url = config.get("url", "http://192.168.1.100:8188").rstrip("/")
        self.workflow_file = config.get("workflow_file", "workflows/bg_remove.json")
        self.output_node_id = str(config.get("output_node_id", "9"))
        self.timeout_s = int(config.get("timeout_s", 60))
        self.poll_interval_s = float(config.get("poll_interval_s", 2))
        self.mock_mode = bool(config.get("mock_mode", True))

        # Text removal config
        self.text_use_python = bool(config.get("text_removal_use_python", True))
        self.text_workflow_file = config.get("text_removal_workflow_file", "workflows/text_remove.json")
        self._text_workflow_path = self._resolve_workflow_path(self.text_workflow_file)
        # IOPaint (lama-cleaner) URL — jeśli ustawione, używamy zamiast ComfyUI/Python
        self.iopaint_url = config.get("iopaint_url", "").rstrip("/")

        # Ścieżka do workflow (relatywna względem katalogu photo_daemon/)
        self._workflow_path = self._resolve_workflow_path(self.workflow_file)

        logger.info(
            f"[ComfyUIClient] Inicjalizacja: url={self.base_url}, "
            f"mock_mode={self.mock_mode}, workflow={self._workflow_path}, "
            f"text_use_python={self.text_use_python}, iopaint_url={self.iopaint_url or 'brak'}"
        )

    def _resolve_workflow_path(self, workflow_file: str) -> str:
        """Rozwiązuje ścieżkę do pliku workflow."""
        if os.path.isabs(workflow_file):
            return workflow_file
        # Relatywna do katalogu tego pliku (photo_daemon/)
        base = Path(__file__).parent
        return str(base / workflow_file)

    def remove_text(self, input_path: str, output_path: str) -> bool:
        """
        Usuwa tekst/napisy/watermarki ze zdjęcia galerii.

        Priorytet:
          1. IOPaint (lama-cleaner) — jeśli iopaint_url ustawione
          2. ComfyUI LaMa — jeśli text_use_python=False
          3. Python pytesseract+OpenCV — fallback

        Args:
            input_path:  Ścieżka do pliku wejściowego
            output_path: Ścieżka do pliku wynikowego

        Returns:
            True jeśli sukces (nawet jeśli nie wykryto tekstu)
        """
        # ── Tryb IOPaint (najlepszy — GPU LaMa przez HTTP) ──
        if self.iopaint_url:
            import tempfile as _tf
            mask_path = None
            try:
                mask_fd, mask_path = _tf.mkstemp(suffix="_text_mask.png")
                os.close(mask_fd)
                if self._generate_text_mask_file(input_path, mask_path):
                    success = self._iopaint_remove_text(input_path, mask_path, output_path)
                    if success:
                        return True
                    logger.warning("[ComfyUIClient] IOPaint nie powiódł się — fallback Python")
                else:
                    logger.info("[ComfyUIClient] Brak tekstu do usunięcia — kopiuję oryginał")
                    shutil.copy2(input_path, output_path)
                    return True
            except Exception as e:
                logger.error(f"[ComfyUIClient] IOPaint wyjątek: {e}")
            finally:
                if mask_path and os.path.exists(mask_path):
                    try:
                        os.remove(mask_path)
                    except Exception:
                        pass

        if self.text_use_python:
            return self._python_remove_text(input_path, output_path)

        # ── Tryb ComfyUI LaMa ──
        import tempfile as _tf
        mask_path = None
        try:
            mask_fd, mask_path = _tf.mkstemp(suffix="_text_mask.png")
            os.close(mask_fd)
            if not self._generate_text_mask_file(input_path, mask_path):
                logger.warning("[ComfyUIClient] Brak maski tekstowej — fallback Python")
                return self._python_remove_text(input_path, output_path)

            success = self._comfy_remove_text(input_path, mask_path, output_path)
            if not success:
                logger.warning("[ComfyUIClient] ComfyUI text removal nie powiódł się — fallback Python")
                return self._python_remove_text(input_path, output_path)
            return True

        except Exception as e:
            logger.error(f"[ComfyUIClient] remove_text wyjątek: {e}")
            return self._python_remove_text(input_path, output_path)
        finally:
            if mask_path and os.path.exists(mask_path):
                try:
                    os.remove(mask_path)
                except Exception:
                    pass

    def get_text_mask_coverage(self, input_path: str) -> float:
        """
        Oblicza jaki procent obrazu pokrywa wykryty tekst.

        Returns:
            0.0  - brak tekstu
            >0.0 - ratio pokrycia (0.0-1.0)
            -1.0 - błąd (brak OpenCV)
        """
        try:
            import cv2
            import numpy as np
            from PIL import Image as _Img

            img_pil = _Img.open(input_path).convert("RGB")
            img_bgr = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
            h, w = img_bgr.shape[:2]
            mask = np.zeros((h, w), dtype=np.uint8)
            found = False

            # Metoda 1: pytesseract
            try:
                import pytesseract
                data = pytesseract.image_to_data(
                    img_pil,
                    output_type=pytesseract.Output.DICT,
                    config="--psm 11 --oem 3"
                )
                for i in range(len(data["text"])):
                    conf = int(data["conf"][i])
                    text = str(data["text"][i]).strip()
                    if conf > 30 and len(text) >= 2:
                        x, y, bw, bh = (data["left"][i], data["top"][i],
                                        data["width"][i], data["height"][i])
                        if bw > 5 and bh > 5:
                            pad = max(10, int(bh * 0.35))
                            cv2.rectangle(mask,
                                          (max(0, x - pad), max(0, y - pad)),
                                          (min(w, x + bw + pad), min(h, y + bh + pad)),
                                          255, -1)
                            found = True
            except ImportError:
                pass

            # Metoda 2: MSER fallback
            if not found:
                gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
                mser = cv2.MSER_create()
                mser.setMinArea(30)
                mser.setMaxArea(min(w * h // 50, 5000))
                regions, _ = mser.detectRegions(gray)
                for pts in regions:
                    rx, ry, rw, rh = cv2.boundingRect(pts.reshape(-1, 1, 2))
                    aspect = rw / max(rh, 1)
                    area = rw * rh
                    if 0.5 < aspect < 15 and 50 < area < (w * h * 0.01):
                        cv2.rectangle(mask,
                                      (max(0, rx - 4), max(0, ry - 4)),
                                      (min(w, rx + rw + 4), min(h, ry + rh + 4)),
                                      255, -1)
                        found = True

            if not found:
                return 0.0

            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            mask = cv2.dilate(mask, kernel, iterations=1)
            ratio = float(mask.sum()) / (255.0 * w * h)
            logger.debug(f"[ComfyUIClient] Pokrycie tekstu: {ratio:.1%} ({input_path})")
            return ratio

        except ImportError:
            logger.warning("[ComfyUIClient] OpenCV niedostępny — get_text_mask_coverage zwraca -1")
            return -1.0
        except Exception as e:
            logger.error(f"[ComfyUIClient] get_text_mask_coverage błąd: {e}")
            return -1.0

    def _iopaint_remove_text(self, input_path: str, mask_path: str, output_path: str) -> bool:
        """
        Wysyła obraz + maskę do IOPaint (lama-cleaner) i pobiera wynik.

        IOPaint API: POST /api/v1/inpaint
          - image: plik JPEG
          - mask:  plik PNG (biały = inpaint, czarny = zachowaj)
        Odpowiedź: bajty PNG z wynikiem.
        """
        try:
            inpaint_url = f"{self.iopaint_url}/api/v1/inpaint"
            logger.info(f"[ComfyUIClient] IOPaint: wysyłam {input_path} + maska -> {inpaint_url}")

            with open(input_path, "rb") as img_f, open(mask_path, "rb") as mask_f:
                files = {
                    "image": (os.path.basename(input_path), img_f, "image/jpeg"),
                    "mask":  (os.path.basename(mask_path),  mask_f, "image/png"),
                }
                resp = requests.post(inpaint_url, files=files, timeout=120)

            if resp.status_code != 200:
                logger.error(f"[ComfyUIClient] IOPaint HTTP {resp.status_code}: {resp.text[:200]}")
                return False

            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

            # IOPaint zwraca PNG — konwertuj do JPEG
            try:
                from PIL import Image as _PilImg
                import io as _io
                img_result = _PilImg.open(_io.BytesIO(resp.content)).convert("RGB")
                img_result.save(output_path, "JPEG", quality=95, optimize=True)
            except Exception:
                # fallback: zapisz surowe bajty
                with open(output_path, "wb") as f:
                    f.write(resp.content)

            logger.info(f"[ComfyUIClient] IOPaint OK: {output_path} ({len(resp.content)//1024} KB)")
            return True

        except requests.RequestException as e:
            logger.error(f"[ComfyUIClient] IOPaint request error: {e}")
            return False
        except Exception as e:
            logger.error(f"[ComfyUIClient] IOPaint unexpected error: {e}")
            return False

    def _python_remove_text(self, input_path: str, output_path: str) -> bool:
        """Usuwa tekst w całości w Pythonie (pytesseract + OpenCV inpaint)."""
        try:
            import image_utils
            from PIL import Image
            img = Image.open(input_path)
            img.load()
            result = image_utils.remove_text_watermark(img)
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            # Zachowaj jakość 95 dla zdjęć galerii
            if result.mode != "RGB":
                result = result.convert("RGB")
            result.save(output_path, "JPEG", quality=95, optimize=True)
            logger.info(f"[ComfyUIClient] Python text removal OK: {output_path}")
            return True
        except Exception as e:
            logger.error(f"[ComfyUIClient] _python_remove_text błąd: {e}")
            # Ostateczny fallback — skopiuj oryginał
            try:
                shutil.copy2(input_path, output_path)
            except Exception:
                pass
            return False

    def _generate_text_mask_file(self, input_path: str, mask_path: str) -> bool:
        """
        Generuje plik maski PNG (białe = tekst, czarne = tło) dla IOPaint LaMa.

        Używa pytesseract z 3 trybami PSM dla najdokładniejszej detekcji tekstu.
        Fallback: MSER tylko dla bardzo małych regionów (watermark logo).
        Brak limitu rozmiaru maski — LaMa radzi sobie z dużymi obszarami.
        """
        try:
            import cv2
            import numpy as np
            from PIL import Image

            img_pil = Image.open(input_path).convert("RGB")
            img_bgr = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
            h, w = img_bgr.shape[:2]
            mask = np.zeros((h, w), dtype=np.uint8)
            found = False

            # ── Pytesseract: 3 tryby PSM dla kompletnej detekcji ──
            try:
                import pytesseract
                psm_configs = [
                    "--psm 3 --oem 3",   # auto page segmentation
                    "--psm 6 --oem 3",   # uniform block of text
                    "--psm 11 --oem 3",  # sparse text
                ]
                for psm_cfg in psm_configs:
                    data = pytesseract.image_to_data(
                        img_pil,
                        output_type=pytesseract.Output.DICT,
                        config=psm_cfg
                    )
                    for i in range(len(data["text"])):
                        conf = int(data["conf"][i])
                        text = str(data["text"][i]).strip()
                        if conf > 25 and len(text) >= 1:
                            x, y, bw, bh = (
                                data["left"][i], data["top"][i],
                                data["width"][i], data["height"][i]
                            )
                            if bw > 3 and bh > 3:
                                # Tight padding — mała ramka wokół liter
                                pad = max(6, int(bh * 0.2))
                                cv2.rectangle(
                                    mask,
                                    (max(0, x - pad), max(0, y - pad)),
                                    (min(w, x + bw + pad), min(h, y + bh + pad)),
                                    255, -1
                                )
                                found = True
                logger.debug(f"[ComfyUIClient] pytesseract: found={found}")
            except ImportError:
                logger.debug("[ComfyUIClient] pytesseract niedostępny — używam MSER")

            # ── MSER fallback: tylko drobne regiony (logo, mały watermark) ──
            if not found:
                gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
                mser = cv2.MSER_create()
                mser.setMinArea(40)
                mser.setMaxArea(min(w * h // 80, 3000))  # max ~1.2% na region
                regions, _ = mser.detectRegions(gray)
                for pts in regions:
                    rx, ry, rw, rh = cv2.boundingRect(pts.reshape(-1, 1, 2))
                    aspect = rw / max(rh, 1)
                    area = rw * rh
                    if 0.6 < aspect < 12 and 60 < area < (w * h * 0.008):
                        cv2.rectangle(mask,
                                      (max(0, rx - 3), max(0, ry - 3)),
                                      (min(w, rx + rw + 3), min(h, ry + rh + 3)),
                                      255, -1)
                        found = True
                logger.debug(f"[ComfyUIClient] MSER fallback: found={found}")

            if not found:
                logger.info(f"[ComfyUIClient] Brak tekstu w {os.path.basename(input_path)}")
                return False

            # Dylatacja — wypełnia luki między literami w słowie
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
            mask = cv2.dilate(mask, kernel, iterations=2)

            ratio = float(mask.sum()) / (255.0 * w * h)
            logger.info(f"[ComfyUIClient] Maska tekstowa: {ratio:.1%} obrazu ({os.path.basename(input_path)})")

            # Zapisz jako RGB PNG
            mask_rgb = cv2.cvtColor(mask, cv2.COLOR_GRAY2RGB)
            Image.fromarray(mask_rgb).save(mask_path, "PNG")
            return True

        except Exception as e:
            logger.error(f"[ComfyUIClient] _generate_text_mask_file błąd: {e}")
            return False

    def _comfy_remove_text(self, input_path: str, mask_path: str, output_path: str) -> bool:
        """Wysyła obraz + maskę do ComfyUI i uruchamia workflow LaMa."""
        try:
            uploaded_img = self._upload_image(input_path)
            if not uploaded_img:
                return False
            uploaded_mask = self._upload_image(mask_path)
            if not uploaded_mask:
                return False

            # Załaduj text_remove.json i podstaw placeholdery
            if not os.path.exists(self._text_workflow_path):
                logger.error(f"[ComfyUIClient] Brak text workflow: {self._text_workflow_path}")
                return False
            with open(self._text_workflow_path, "r", encoding="utf-8") as f:
                wf_str = f.read()
            wf_str = wf_str.replace("{INPUT_FILENAME}", uploaded_img)
            wf_str = wf_str.replace("{MASK_FILENAME}", uploaded_mask)
            wf_str = wf_str.replace("{OUTPUT_PREFIX}", Path(output_path).stem)

            import json as _json
            workflow = _json.loads(wf_str)

            prompt_id = self._submit_prompt(workflow)
            if not prompt_id:
                return False

            result_filename = self._poll_until_done(prompt_id)
            if not result_filename:
                return False

            return self._download_result(result_filename, output_path)

        except Exception as e:
            logger.error(f"[ComfyUIClient] _comfy_remove_text błąd: {e}")
            return False

    def remove_background(self, input_path: str, output_path: str) -> bool:
        """
        Usuwa tło ze zdjęcia używając ComfyUI (lub mock).

        Args:
            input_path: Ścieżka do pliku wejściowego (JPEG/PNG)
            output_path: Ścieżka gdzie zapisać wynik

        Returns:
            True jeśli sukces, False jeśli błąd
        """
        if self.mock_mode:
            return self._mock_remove_background(input_path, output_path)

        return self._real_remove_background(input_path, output_path)

    def _mock_remove_background(self, input_path: str, output_path: str) -> bool:
        """Tryb mock — po prostu kopiuje plik."""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            shutil.copy2(input_path, output_path)
            logger.info(f"[ComfyUIClient] MOCK: skopiowano {input_path} -> {output_path}")
            return True
        except Exception as e:
            logger.error(f"[ComfyUIClient] MOCK: błąd kopiowania: {e}")
            return False

    def _real_remove_background(self, input_path: str, output_path: str) -> bool:
        """
        Prawdziwe usuwanie tła przez ComfyUI API.

        Kroki:
        1. Upload pliku
        2. Wyślij workflow
        3. Polling historii
        4. Pobierz wynik
        """
        try:
            # Krok 1: Upload pliku
            uploaded_filename = self._upload_image(input_path)
            if not uploaded_filename:
                logger.error("[ComfyUIClient] Upload nie powiódł się")
                return False
            logger.info(f"[ComfyUIClient] Upload OK: {uploaded_filename}")

            # Krok 2: Przygotuj i wyślij workflow
            workflow = self._load_workflow(uploaded_filename, output_path)
            if not workflow:
                logger.error("[ComfyUIClient] Nie można załadować workflow")
                return False

            prompt_id = self._submit_prompt(workflow)
            if not prompt_id:
                logger.error("[ComfyUIClient] Submit prompt nie powiódł się")
                return False
            logger.info(f"[ComfyUIClient] Prompt submitted: {prompt_id}")

            # Krok 3: Polling
            result_filename = self._poll_until_done(prompt_id)
            if not result_filename:
                logger.error(f"[ComfyUIClient] Timeout lub błąd dla prompt {prompt_id}")
                return False
            logger.info(f"[ComfyUIClient] Gotowe: {result_filename}")

            # Krok 4: Pobierz wynik
            success = self._download_result(result_filename, output_path)
            return success

        except Exception as e:
            logger.error(f"[ComfyUIClient] Nieoczekiwany błąd: {e}", exc_info=True)
            return False

    def _upload_image(self, input_path: str) -> Optional[str]:
        """
        Przesyła plik do ComfyUI.

        Returns:
            Nazwa pliku na serwerze lub None
        """
        try:
            upload_url = f"{self.base_url}/upload/image"
            filename = os.path.basename(input_path)

            with open(input_path, "rb") as f:
                files = {
                    "image": (filename, f, "image/jpeg"),
                }
                response = requests.post(
                    upload_url,
                    files=files,
                    timeout=30
                )

            if response.status_code == 200:
                data = response.json()
                return data.get("name") or data.get("filename")
            else:
                logger.error(f"[ComfyUIClient] Upload błąd HTTP {response.status_code}: {response.text[:200]}")
                return None

        except requests.RequestException as e:
            logger.error(f"[ComfyUIClient] Upload request error: {e}")
            return None
        except Exception as e:
            logger.error(f"[ComfyUIClient] Upload unexpected error: {e}")
            return None

    def _load_workflow(self, input_filename: str, output_path: str) -> Optional[dict]:
        """
        Ładuje workflow JSON i zastępuje placeholdery.

        Args:
            input_filename: Nazwa pliku po upload (na serwerze ComfyUI)
            output_path: Ścieżka docelowa (używana do stworzenia prefixu)

        Returns:
            Słownik workflow lub None
        """
        try:
            if not os.path.exists(self._workflow_path):
                logger.error(f"[ComfyUIClient] Brak workflow: {self._workflow_path}")
                return None

            with open(self._workflow_path, "r", encoding="utf-8") as f:
                workflow_str = f.read()

            # Zamień placeholdery
            output_prefix = Path(output_path).stem
            workflow_str = workflow_str.replace("{INPUT_FILENAME}", input_filename)
            workflow_str = workflow_str.replace("{OUTPUT_PREFIX}", output_prefix)

            return json.loads(workflow_str)

        except json.JSONDecodeError as e:
            logger.error(f"[ComfyUIClient] Błąd JSON w workflow: {e}")
            return None
        except Exception as e:
            logger.error(f"[ComfyUIClient] Błąd ładowania workflow: {e}")
            return None

    def _submit_prompt(self, workflow: dict) -> Optional[str]:
        """
        Wysyła workflow do ComfyUI.

        Returns:
            prompt_id lub None
        """
        try:
            prompt_url = f"{self.base_url}/prompt"
            payload = {"prompt": workflow}

            response = requests.post(
                prompt_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("prompt_id")
            else:
                logger.error(f"[ComfyUIClient] Prompt błąd HTTP {response.status_code}: {response.text[:200]}")
                return None

        except requests.RequestException as e:
            logger.error(f"[ComfyUIClient] Prompt request error: {e}")
            return None

    def _poll_until_done(self, prompt_id: str) -> Optional[str]:
        """
        Polluje endpoint /history/{prompt_id} do momentu zakończenia.
        Timeout kontrolowany przez self.timeout_s.

        Returns:
            Nazwa pliku wynikowego lub None przy timeout/błędzie
        """
        history_url = f"{self.base_url}/history/{prompt_id}"
        deadline = time.time() + self.timeout_s

        while time.time() < deadline:
            try:
                response = requests.get(history_url, timeout=10)

                if response.status_code == 200:
                    data = response.json()

                    if prompt_id in data:
                        prompt_data = data[prompt_id]
                        outputs = prompt_data.get("outputs", {})

                        # Sprawdź outputs z noda SaveImage (self.output_node_id)
                        node_output = outputs.get(self.output_node_id, {})
                        images = node_output.get("images", [])

                        if images:
                            # Weź pierwszą wygenerowaną grafikę
                            img_info = images[0]
                            return img_info.get("filename")

                        # Sprawdź status przez wszystkie nody jeśli nie ma output_node_id
                        if outputs:
                            for node_id, node_data in outputs.items():
                                imgs = node_data.get("images", [])
                                if imgs:
                                    return imgs[0].get("filename")

                elif response.status_code == 404:
                    # Jeszcze nie w historii — czekaj
                    pass
                else:
                    logger.warning(f"[ComfyUIClient] History poll HTTP {response.status_code}")

            except requests.RequestException as e:
                logger.warning(f"[ComfyUIClient] Poll request error: {e}")

            time.sleep(self.poll_interval_s)

        logger.error(f"[ComfyUIClient] Timeout ({self.timeout_s}s) dla prompt {prompt_id}")
        return None

    def _download_result(self, filename: str, output_path: str) -> bool:
        """
        Pobiera wynikowy plik z ComfyUI.

        Args:
            filename: Nazwa pliku na serwerze
            output_path: Lokalna ścieżka docelowa

        Returns:
            True jeśli sukces
        """
        try:
            view_url = f"{self.base_url}/view"
            params = {
                "filename": filename,
                "type": "output",
                "subfolder": "",
                "channel": "rgb",
            }

            response = requests.get(view_url, params=params, timeout=30, stream=True)

            if response.status_code == 200:
                os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                with open(output_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                logger.info(f"[ComfyUIClient] Pobrano wynik: {output_path}")
                return True
            else:
                logger.error(f"[ComfyUIClient] Download błąd HTTP {response.status_code}")
                return False

        except requests.RequestException as e:
            logger.error(f"[ComfyUIClient] Download request error: {e}")
            return False
        except Exception as e:
            logger.error(f"[ComfyUIClient] Download unexpected error: {e}")
            return False

    def health_check(self) -> bool:
        """
        Sprawdza czy ComfyUI jest dostępne.

        Returns:
            True jeśli serwer odpowiada
        """
        if self.mock_mode:
            return True

        try:
            response = requests.get(f"{self.base_url}/system_stats", timeout=5)
            return response.status_code == 200
        except Exception:
            return False


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    # Test z mock mode
    config_mock = {
        "url": "http://192.168.1.100:8188",
        "workflow_file": "workflows/bg_remove.json",
        "output_node_id": "9",
        "timeout_s": 60,
        "poll_interval_s": 2,
        "mock_mode": True,
    }

    client = ComfyUIClient(config_mock)

    # Stwórz testowy plik wejściowy
    test_input = "/tmp/test_input.jpg"
    test_output = "/tmp/test_output.jpg"

    try:
        from PIL import Image
        img = Image.new("RGB", (200, 200), (255, 100, 50))
        img.save(test_input)
        print(f"Stworzono testowy plik: {test_input}")
    except ImportError:
        print("Pillow nie zainstalowana — pomijam test")
        sys.exit(0)

    # Test mock
    print("\n--- Test MOCK mode ---")
    success = client.remove_background(test_input, test_output)
    print(f"remove_background (mock): {success}")
    print(f"Plik wynikowy istnieje: {os.path.exists(test_output)}")

    # Test health check
    print(f"health_check (mock): {client.health_check()}")

    # Test real mode (ComfyUI musi być dostępne)
    if len(sys.argv) > 1 and sys.argv[1] == "--real":
        print("\n--- Test REAL mode (ComfyUI wymagane) ---")
        config_real = {**config_mock, "mock_mode": False}
        client_real = ComfyUIClient(config_real)
        print(f"health_check (real): {client_real.health_check()}")
        if client_real.health_check():
            test_output_real = "/tmp/test_output_real.jpg"
            success = client_real.remove_background(test_input, test_output_real)
            print(f"remove_background (real): {success}")

    print("\nTesty zakończone!")
