# -*- coding: utf-8 -*-
"""
Photo Daemon — ładowanie konfiguracji z config.yaml.
"""

import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Domyślna konfiguracja (fallback jeśli plik nie istnieje)
_DEFAULTS = {
    "photo_daemon": {
        "db_path": str(Path(__file__).parent.parent / "akces_hub.db"),
        "inbox_path": str(Path(__file__).parent / "storage" / "inbox"),
        "originals_path": str(Path(__file__).parent / "storage" / "originals"),
        "workdir_path": str(Path(__file__).parent / "storage" / "workdir"),
        "processed_base_path": str(Path(__file__).parent / "storage" / "processed"),
        "status_port": 5051,
        "status_host": "0.0.0.0",
        "max_jobs_per_run": 10,
        "required_photo_count": 1,
        "external_api": {
            "type": "comfyui",
            "url": "http://192.168.1.100:8188",
            "workflow_file": "workflows/bg_remove.json",
            "output_node_id": "9",
            "timeout_s": 60,
            "poll_interval_s": 2,
            "mock_mode": True,
        },
        "processing": {
            "target_aspect_ratio": "1:1",
            "crop_enabled": True,
            "brightness": 1.05,
            "contrast": 1.10,
            "allegro_size": [1200, 1200],
            "vinted_size": [800, 800],
            "thumb_size": [300, 300],
            "jpeg_quality": 90,
            "vinted_quality": 85,
            "thumb_quality": 80,
        },
        "log_level": "INFO",
    }
}

_config_cache: dict | None = None
_config_file: str | None = None


def load_config(config_path: str | None = None) -> dict:
    """
    Ładuje konfigurację z pliku YAML.

    Args:
        config_path: Ścieżka do pliku config.yaml.
                     Domyślnie: katalog tego skryptu / config.yaml

    Returns:
        Słownik konfiguracji (sekcja photo_daemon)
    """
    global _config_cache, _config_file

    if config_path is None:
        config_path = str(Path(__file__).parent / "config.yaml")

    _config_file = config_path

    try:
        import yaml
    except ImportError:
        logger.error("[config] PyYAML nie jest zainstalowany! pip install PyYAML")
        _config_cache = _DEFAULTS["photo_daemon"].copy()
        return _config_cache

    if not os.path.exists(config_path):
        logger.warning(f"[config] Plik konfiguracji nie istnieje: {config_path} — używam domyślnych wartości")
        _config_cache = _DEFAULTS["photo_daemon"].copy()
        return _config_cache

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        cfg = raw.get("photo_daemon", {})

        # Merge z domyślnymi (głęboki merge dla sekcji zagnieżdżonych)
        merged = _deep_merge(_DEFAULTS["photo_daemon"], cfg)
        _config_cache = merged
        logger.info(f"[config] Załadowano konfigurację z {config_path}")
        return _config_cache

    except Exception as e:
        logger.error(f"[config] Błąd ładowania {config_path}: {e} — używam domyślnych wartości")
        _config_cache = _DEFAULTS["photo_daemon"].copy()
        return _config_cache


def _deep_merge(base: dict, override: dict) -> dict:
    """Głęboki merge słowników — override nadpisuje base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def get_config(key: str, default=None):
    """
    Pobiera wartość konfiguracji po kluczu (obsługuje notację kropkową).

    Przykłady:
        get_config("db_path")
        get_config("external_api.mock_mode")
        get_config("processing.allegro_size")

    Args:
        key: Klucz konfiguracji (może zawierać '.' dla zagnieżdżonych kluczy)
        default: Wartość domyślna jeśli klucz nie istnieje

    Returns:
        Wartość konfiguracji lub default
    """
    global _config_cache

    if _config_cache is None:
        load_config()

    parts = key.split(".")
    current = _config_cache

    try:
        for part in parts:
            if isinstance(current, dict):
                current = current[part]
            else:
                return default
        return current
    except (KeyError, TypeError):
        return default


def get_full_config() -> dict:
    """Zwraca pełną konfigurację (sekcja photo_daemon)."""
    global _config_cache
    if _config_cache is None:
        load_config()
    return _config_cache


if __name__ == "__main__":
    # Test
    cfg = load_config()
    print(f"db_path: {get_config('db_path')}")
    print(f"mock_mode: {get_config('external_api.mock_mode')}")
    print(f"allegro_size: {get_config('processing.allegro_size')}")
    print(f"nonexistent: {get_config('nonexistent.key', 'DEFAULT')}")
