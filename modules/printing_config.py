"""
Moduł zarządzania konfiguracją drukowania
Akces Hub v3.0.21
"""

import json
import os

CONFIG_FILE = 'config.json'

def load_config():
    """Wczytaj konfigurację z pliku JSON"""
    if not os.path.exists(CONFIG_FILE):
        return get_default_config()
    
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config
    except Exception as e:
        print(f"⚠️ Błąd wczytywania config: {e}")
        return get_default_config()

def save_config(key, value):
    """Zapisz pojedyncze ustawienie"""
    config = load_config()
    config[key] = value
    
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"⚠️ Błąd zapisywania config: {e}")
        return False

def save_full_config(config_dict):
    """Zapisz całą konfigurację naraz"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config_dict, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"⚠️ Błąd zapisywania config: {e}")
        return False

def get_default_config():
    """Domyślna konfiguracja"""
    return {
        'auto_print_enabled': False,
        'default_printer': 'niimbot',
        'print_copies': 1,
        'ask_before_print': False
    }

def get_printer_settings():
    """Pobierz ustawienia drukarki"""
    config = load_config()
    return {
        'auto_print': config.get('auto_print_enabled', False),
        'printer': config.get('default_printer', 'niimbot'),
        'copies': config.get('print_copies', 1),
        'ask_before': config.get('ask_before_print', False)
    }

def is_auto_print_enabled():
    """Sprawdź czy auto-drukowanie jest włączone"""
    config = load_config()
    return config.get('auto_print_enabled', False)

def get_default_printer():
    """Pobierz domyślną drukarkę"""
    config = load_config()
    return config.get('default_printer', 'niimbot')
