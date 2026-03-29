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

        # Ścieżka do workflow (relatywna względem katalogu photo_daemon/)
        self._workflow_path = self._resolve_workflow_path(self.workflow_file)

        logger.info(
            f"[ComfyUIClient] Inicjalizacja: url={self.base_url}, "
            f"mock_mode={self.mock_mode}, workflow={self._workflow_path}"
        )

    def _resolve_workflow_path(self, workflow_file: str) -> str:
        """Rozwiązuje ścieżkę do pliku workflow."""
        if os.path.isabs(workflow_file):
            return workflow_file
        # Relatywna do katalogu tego pliku (photo_daemon/)
        base = Path(__file__).parent
        return str(base / workflow_file)

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
