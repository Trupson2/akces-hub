from .utils import get_gemini_api_url
"""
Moduł generowania tytułów ofert Allegro z wykorzystaniem AI (Gemini)
Zgodny z wytycznymi SEO Allegro - maksymalizacja Trafności w wyszukiwarce
"""

import re
import requests
from typing import Dict, Optional


def generate_allegro_title_ai(product_data: Dict, gemini_key: str, max_length: int = 75) -> str:
    """
    Generuje tytuł oferty Allegro używając AI (Gemini) z precyzyjnym meta-promptem.
    
    ZASADY:
    - ABSOLUTNE MAXIMUM 75 znaków (wliczając spacje)
    - Hierarchia słów kluczowych: [Rodzaj Produktu] + [Marka] + [Model] + [Parametr] + [Kod]
    - Title Case, bez kropki na końcu
    - ZAKAZ słów marketingowych i emoji
    
    Args:
        product_data: Słownik z danymi produktu:
            - nazwa: str - nazwa produktu z Amazona (wymagane)
            - bullet_points: list - cechy produktu (opcjonalne)
            - kategoria: str - kategoria produktu (opcjonalne)
            - asin: str - kod produktu (opcjonalne)
        gemini_key: str - klucz API Gemini
        max_length: int - maksymalna długość tytułu (domyślnie 75)
    
    Returns:
        str - wygenerowany tytuł oferty lub fallback w przypadku błędu
    
    Example:
        >>> product_data = {
        ...     'nazwa': 'Sony X200 Backup Camera 170 Degree IP68 Waterproof 12V',
        ...     'bullet_points': ['170° wide angle', 'IP68 waterproof', '12V power'],
        ...     'asin': 'B0ABCD1234'
        ... }
        >>> generate_allegro_title_ai(product_data, 'your_api_key')
        'Kamera Cofania Sony X200 170st IP68 12V'
    """
    
    nazwa = product_data.get('nazwa', '')
    if not nazwa or not gemini_key:
        return _fallback_title(nazwa, max_length)

    # Jeśli nazwa to fallback scrapera (nie pobrano z Amazona), nie generuj AI tytułu
    if nazwa.startswith('Produkt Amazon ') or nazwa.startswith('Amazon Product '):
        print(f"[TITLE AI] [WARN] Nazwa to fallback scrapera — pomijam AI, zostawiam do ręcznej edycji")
        return _fallback_title(nazwa, max_length)
    
    # Przygotuj dane dodatkowe
    bullet_points = product_data.get('bullet_points', [])
    kategoria = product_data.get('kategoria', '')
    asin = product_data.get('asin', '')
    
    # Formatuj cechy produktu do promptu
    features_text = ''
    if bullet_points:
        features_text = '\n'.join([f'- {bp}' for bp in bullet_points[:5]])  # Max 5 cech
    
    # META PROMPT dla AI - BARDZO PRECYZYJNY
    prompt = f"""ZADANIE: Wygeneruj tytuł oferty Allegro dla tego produktu.
JĘZYK WYJŚCIOWY: TYLKO POLSKI - przetłumacz nazwę na język polski niezależnie od języka wejściowego (angielski, niemiecki, itd.)

PRODUKT: {nazwa}
{f'ASIN: {asin}' if asin else ''}
{f'KATEGORIA: {kategoria}' if kategoria else ''}

{f'CECHY PRODUKTU:{chr(10)}{features_text}' if features_text else ''}

=== KRYTYCZNE WYMAGANIA (NARUSZENIE = ODRZUCENIE) ===

1. JĘZYK: ZAWSZE po polsku. Tłumacz z angielskiego, niemieckiego, każdego innego języka.
   ✓ "Fußmatten BMW X3" → "Maty Samochodowe BMW X3"
   ✓ "Car Charger Anker" → "Ładowarka Samochodowa Anker"
   ✓ "Backup Camera" → "Kamera Cofania"

2. DŁUGOŚĆ:
   - ABSOLUTNE MAXIMUM: {max_length} znaków (wliczając spacje)
   - Jeśli przekracza limit, utnij najmniej ważne elementy z końca
   - NIGDY nie ucinaj nazwy modelu w połowie

3. HIERARCHIA SŁÓW KLUCZOWYCH (od lewej najważniejsze):
   [Rodzaj Produktu] + [Marka] + [Model] + [Kluczowy Parametr] + [Kod producenta]

   PRZYKŁADY DOBREJ HIERARCHII:
   ✓ "Kamera Cofania Sony X200 170st IP68 12V"
   ✓ "Ładowarka Samochodowa Anker 45W USB-C PD"
   ✓ "Mysz Logitech MX Master 3 Bluetooth 4000DPI"
   ✓ "Kabel HDMI 4K 2m Pozłacany HDR ARC"

   PRZYKŁADY ZŁEJ HIERARCHII:
   ✗ "Super Okazja Sony X200 Nowa Wysoka Jakość" (marketing na początku)
   ✗ "12V IP68 170st Kamera X200" (parametry przed nazwą produktu)

4. LISTA ZAKAZANA (STOP WORDS) - USUŃ BEZWZGLĘDNIE:

   MARKETING:
   ✗ "Super", "Hit", "Nowy", "Okazja", "Wyprzedaż", "Sale", "Hot"
   ✗ "Profesjonalny", "Premium", "Exclusive", "Limited"
   ✗ "Wysoka jakość", "Najlepsza jakość", "Top quality"
   ✗ "Bestseller", "Polecamy", "Sprawdź"

   ZBĘDNE ŁĄCZNIKI (chyba że niezbędne):
   ✗ "do", "z", "i", "dla", "na" (np. "do iPhone" → "iPhone")
   ✗ Wyjątek: "Etui do iPhone" (tutaj "do" jest OK)

   SŁOWA OZDOBNE:
   ✗ "Oryginalny" (chyba że to część nazwy marki)
   ✗ "Prawdziwy", "Autentyczny", "Genuine"
   ✗ "Uniwersalny", "Wielofunkcyjny"

   ZAKAZANE ZNAKI:
   ✗ Emoji, cudzysłowy, nawiasy ozdobne: "" '' „" () []

5. FORMATOWANIE:
   ✓ Title Case: Wielkie litery na początku wyrazów
   ✓ NIE PISZ CAPSLOCKIEM: "SUPER KAMERA" → "Kamera"
   ✓ Brak kropki na końcu tytułu
   ✓ Pojedyncze spacje między wyrazami

6. CO ZOSTAWIĆ (jeśli jest w nazwie):
   ✓ Nazwę produktu (co to jest): "Kamera", "Ładowarka", "Kabel"
   ✓ Markę: "Sony", "Anker", "Samsung", "BMW"
   ✓ Model: "X200", "MX Master 3", "Galaxy S24", "G45"
   ✓ Kluczowe parametry: "170°", "45W", "4K", "2m"
   ✓ Materiał: "IP68", "Skóra", "Aluminium"
   ✓ Kolor (jeśli charakterystyczny): "Czarny", "Srebrny"
   ✓ Kod producenta (jeśli krótki): "Type-C", "USB-C"

7. INTELIGENTNE SKRACANIE (gdy przekracza {max_length} znaków):
   PRIORYTET USUWANIA (od pierwszego do ostatniego):
   1. Usuń kolor (jeśli jest standardowy: czarny, biały)
   2. Usuń dodatkowe parametry (zostaw najważniejszy)
   3. Skróć nazwę produktu (np. "Ładowarka Samochodowa" → "Ładowarka")
   4. NIGDY nie skracaj marki i modelu

=== FORMAT ODPOWIEDZI ===

ZWRÓĆ TYLKO TYTUŁ - NIC WIĘCEJ!

NIE PISZ:
✗ "Oto propozycja tytułu:"
✗ "Tytuł oferty:"
✗ "Sugeruję:"
✗ Jakichkolwiek dodatkowych wyjaśnień

NAPISZ:
✓ Sam tytuł oferty po polsku (dokładnie {max_length} znaków lub mniej)

=== PRZYKŁADY KOŃCOWE ===

INPUT: "Sony Backup Camera 170 Degree Waterproof IP68 12V Night Vision New"
OUTPUT: Kamera Cofania Sony 170st IP68 12V Nocna

INPUT: "Anker PowerPort III 45W USB-C Car Charger Fast Charging PD Premium Quality"
OUTPUT: Ładowarka Samochodowa Anker 45W USB-C PD

INPUT: "Logitech MX Master 3 Advanced Wireless Mouse Bluetooth High Precision 4000DPI Black"
OUTPUT: Mysz Logitech MX Master 3 Bluetooth 4000DPI Czarna

INPUT: "3w bmw x3 g01 2018-2024/ix3 g08 2021-2024 2025 fußmatten und"
OUTPUT: Maty Samochodowe BMW X3 G01 G08 2018-2024

Wygeneruj tytuł:"""

    try:
        # Pobierz model per sektor (tytuly) z configa
        try:
            from modules.database import get_config as _get_cfg
            _tytuly_model = _get_cfg('ai_model_tytuly', _get_cfg('gemini_model', 'gemini-2.5-flash'))
            _tytuly_url = f'https://generativelanguage.googleapis.com/v1beta/models/{_tytuly_model}:generateContent?key={gemini_key}'
        except Exception:
            _tytuly_url = get_gemini_api_url(gemini_key)

        # Wywołanie API Gemini
        response = requests.post(
            _tytuly_url,
            json={
                'contents': [{'parts': [{'text': prompt}]}],
                'generationConfig': {
                    'temperature': 0.3,
                    'maxOutputTokens': 200,
                    'topP': 0.95,
                    'topK': 40,
                    'thinkingConfig': {'thinkingBudget': 0}  # disable thinking - tokens nie marnowane na myślenie
                }
            },
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            # Gemini 2.5+ thinking models interleave thought parts (thought:true) with answer parts
            # Concatenate ALL non-thought parts to get the full response
            _parts = data.get('candidates', [{}])[0].get('content', {}).get('parts', [])
            generated_text = ''.join(
                _p.get('text', '') for _p in _parts if not _p.get('thought', False)
            )
            
            if generated_text:
                # Wyczyść odpowiedź AI - usuń preambuły
                title = _clean_ai_response(generated_text)
                
                # Walidacja długości
                if len(title) > max_length:
                    print(f"[TITLE AI] Tytuł za długi ({len(title)} znaków), ucinam do {max_length}")
                    title = _smart_truncate(title, max_length)
                
                print(f"[TITLE AI] [OK] Wygenerowano: {title} ({len(title)} znaków)")
                return title
            else:
                print(f"[TITLE AI] [WARN] Brak odpowiedzi z API, używam fallback")
                return _fallback_title(nazwa, max_length)
        else:
            print(f"[TITLE AI] [ERR] Błąd API: {response.status_code} - {response.text[:200]}")
            return _fallback_title(nazwa, max_length)
            
    except requests.exceptions.Timeout:
        print(f"[TITLE AI] [TIME] Timeout API, używam fallback")
        return _fallback_title(nazwa, max_length)
    except Exception as e:
        print(f"[TITLE AI] [ERR] Wyjątek: {e}")
        return _fallback_title(nazwa, max_length)


def _clean_ai_response(text: str) -> str:
    """
    Czyści odpowiedź AI z niepożądanych elementów.
    
    Usuwa:
    - Preambuły typu "Oto propozycja:", "Tytuł:", itp.
    - Cudzysłowy
    - Emoji
    - Wielokrotne spacje
    - Kropkę na końcu
    """
    # Usuń preambuły
    prefixes_to_remove = [
        'oto propozycja tytułu:',
        'propozycja tytułu:',
        'tytuł oferty:',
        'tytuł:',
        'sugeruję:',
        'wygenerowany tytuł:',
    ]
    
    text_lower = text.lower().strip()
    for prefix in prefixes_to_remove:
        if text_lower.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    
    # Usuń cudzysłowy
    text = text.strip('"\'„"''')
    
    # Usuń emoji i nietypowe znaki
    text = re.sub(r'[^\w\sąćęłńóśźżĄĆĘŁŃÓŚŹŻ°\-./]', '', text, flags=re.UNICODE)
    
    # Usuń wielokrotne spacje
    text = re.sub(r'\s+', ' ', text)
    
    # Usuń kropkę na końcu
    text = text.rstrip('.')
    
    return text.strip()


def _smart_truncate(title: str, max_length: int) -> str:
    """
    Inteligentnie skraca tytuł do max_length znaków.
    Stara się uciąć na granicy wyrazów, nie w połowie słowa.
    """
    if len(title) <= max_length:
        return title
    
    # Znajdź ostatnią spację przed limitem
    truncated = title[:max_length]
    last_space = truncated.rfind(' ')
    
    # Jeśli ostatnia spacja jest daleko (>50% długości), utnij na twardo
    if last_space > max_length * 0.6:
        return truncated[:last_space].strip()
    else:
        return truncated.strip()


def _fallback_title(nazwa: str, max_length: int) -> str:
    """
    Fallback - prosta optymalizacja tytułu bez AI.
    Używana gdy AI nie działa lub brak klucza API.
    """
    if not nazwa:
        return ''
    
    # Usuń stop words
    stop_words = [
        'super', 'hit', 'new', 'neu', 'nowy', 'okazja', 'sale', 'hot',
        'premium', 'professional', 'profesjonalny', 'exclusive',
        'bestseller', 'original', 'genuine', 'high quality',
    ]
    
    words = nazwa.split()
    filtered_words = [w for w in words if w.lower() not in stop_words]
    title = ' '.join(filtered_words)
    
    # Title Case
    title = title.title()
    
    # Usuń znaki specjalne
    title = re.sub(r'[^\w\sąćęłńóśźżĄĆĘŁŃÓŚŹŻ°\-./]', '', title, flags=re.UNICODE)
    title = re.sub(r'\s+', ' ', title).strip()
    
    # Skróć do max_length
    if len(title) > max_length:
        title = _smart_truncate(title, max_length)
    
    return title


# =============================================================================
# FUNKCJA POMOCNICZA - BATCH PROCESSING
# =============================================================================

def generate_titles_batch(products: list, gemini_key: str, max_length: int = 75) -> dict:
    """
    Generuje tytuły dla wielu produktów naraz.
    
    Args:
        products: Lista słowników z danymi produktów
        gemini_key: Klucz API Gemini
        max_length: Maksymalna długość tytułu
    
    Returns:
        dict: {asin: tytuł} lub {index: tytuł} jeśli brak ASIN
    
    Example:
        >>> products = [
        ...     {'nazwa': 'Sony Camera...', 'asin': 'B0ABC123'},
        ...     {'nazwa': 'Anker Charger...', 'asin': 'B0DEF456'},
        ... ]
        >>> results = generate_titles_batch(products, 'your_api_key')
        >>> print(results)
        {'B0ABC123': 'Kamera Sony...', 'B0DEF456': 'Ładowarka Anker...'}
    """
    results = {}
    
    for i, product in enumerate(products):
        try:
            title = generate_allegro_title_ai(product, gemini_key, max_length)
            key = product.get('asin', f'product_{i}')
            results[key] = title
            
            # Małe opóźnienie żeby nie przeciążać API
            import time
            time.sleep(0.5)
            
        except Exception as e:
            print(f"[BATCH] Błąd dla produktu {i}: {e}")
            key = product.get('asin', f'product_{i}')
            results[key] = _fallback_title(product.get('nazwa', ''), max_length)
    
    return results
