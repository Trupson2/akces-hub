# -*- coding: utf-8 -*-
"""
Centralny pomocnik dla Gemini AI. 
Unifikuje pobieranie klucza API z różnych źródeł.
"""
import os
from modules.database import get_config

def get_gemini_api_key():
    """
    Pobiera klucz API Gemini z (w kolejności):
    1. gemini_config.py
    2. os.environ['GEMINI_API_KEY']
    3. Bazy danych (get_config)
    """
    # 1. Próba z gemini_config.py
    try:
        from gemini_config import GEMINI_API_KEY
        if GEMINI_API_KEY and GEMINI_API_KEY != 'WKLEJ_TUTAJ_SWOJ_KLUCZ':
            return GEMINI_API_KEY
    except ImportError:
        pass

    # 2. Próba ze zmiennych środowiskowych
    env_key = os.environ.get('GEMINI_API_KEY', '')
    if env_key:
        return env_key

    # 3. Próba z bazy danych
    try:
        db_key = get_config('gemini_api_key', '')
        if db_key:
            return db_key
    except:
        pass

    # 4. NOWOŚĆ: Ostateczny fallback - czytaj plik ręcznie z dysku (pancerne)
    try:
        # Sprawdź w katalogu głównym (o jeden wyżej od modules/)
        paths = ['gemini_config.py', '../gemini_config.py', './gemini_config.py']
        import re
        for p in paths:
            if os.path.exists(p):
                with open(p, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # Szukaj GEMINI_API_KEY = '...' lub "..."
                    match = re.search(r"GEMINI_API_KEY\s*=\s*['\"]([^'\"]+)['\"]", content)
                    if match and match.group(1) != 'WKLEJ_TUTAJ_SWOJ_KLUCZ':
                        return match.group(1).strip()
    except:
        pass

    return ''
