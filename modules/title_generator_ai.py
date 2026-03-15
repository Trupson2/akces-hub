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
        print(f"[TITLE AI] ⚠️ Nazwa to fallback scrapera — pomijam AI, zostawiam do ręcznej edycji")
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

PRODUKT: {nazwa}
{f'ASIN: {asin}' if asin else ''}
{f'KATEGORIA: {kategoria}' if kategoria else ''}

{f'CECHY PRODUKTU:{chr(10)}{features_text}' if features_text else ''}

=== KRYTYCZNE WYMAGANIA (NARUSZENIE = ODRZUCENIE) ===

1. DŁUGOŚĆ:
   - ABSOLUTNE MAXIMUM: {max_length} znaków (wliczając spacje)
   - Jeśli przekracza limit, utnij najmniej ważne elementy z końca
   - NIGDY nie ucinaj nazwy modelu w połowie

2. HIERARCHIA SŁÓW KLUCZOWYCH (od lewej najważniejsze):
   [Rodzaj Produktu] + [Marka] + [Model] + [Kluczowy Parametr] + [Kod producenta]
   
   PRZYKŁADY DOBREJ HIERARCHII:
   ✓ "Kamera Cofania Sony X200 170st IP68 12V"
   ✓ "Ładowarka Samochodowa Anker 45W USB-C PD"
   ✓ "Mysz Logitech MX Master 3 Bluetooth 4000DPI"
   ✓ "Kabel HDMI 4K 2m Pozłacany HDR ARC"
   
   PRZYKŁADY ZŁEJ HIERARCHII:
   ✗ "Super Okazja Sony X200 Nowa Wysoka Jakość" (marketing na początku)
   ✗ "12V IP68 170st Kamera X200" (parametry przed nazwą produktu)

3. LISTA ZAKAZANA (STOP WORDS) - USUŃ BEZWZGLĘDNIE:
   
   MARKETING:
   ❌ "Super", "Hit", "Nowy", "Okazja", "Wyprzedaż", "Sale", "Hot"
   ❌ "Profesjonalny", "Premium", "Exclusive", "Limited"
   ❌ "Wysoka jakość", "Najlepsza jakość", "Top quality"
   ❌ "Bestseller", "Polecamy", "Sprawdź"
   
   ZBĘDNE ŁĄCZNIKI (chyba że niezbędne):
   ❌ "do", "z", "i", "dla", "na" (np. "do iPhone" → "iPhone")
   ❌ Wyjątek: "Etui do iPhone" (tutaj "do" jest OK)
   
   SŁOWA OZDOBNE:
   ❌ "Oryginalny" (chyba że to część nazwy marki)
   ❌ "Prawdziwy", "Autentyczny", "Genuine"
   ❌ "Uniwersalny", "Wielofunkcyjny"
   
   ZAKAZANE ZNAKI:
   ❌ Emoji, cudzysłowy, nawiasy ozdobne: 🔥 "" '' „" () []

4. FORMATOWANIE:
   ✓ Title Case: Wielkie litery na początku wyrazów
   ✓ NIE PISZ CAPSLOCKIEM: "SUPER KAMERA" → "Kamera"
   ✓ Brak kropki na końcu tytułu
   ✓ Pojedyncze spacje między wyrazami
   
   PRZYKŁADY:
   ✓ "Kamera Cofania Sony X200 170st" (Title Case)
   ✗ "KAMERA COFANIA SONY X200 170ST" (CAPSLOCK)
   ✗ "kamera cofania sony x200 170st" (lowercase)

5. CO ZOSTAWIĆ (jeśli jest w nazwie):
   ✓ Nazwę produktu (co to jest): "Kamera", "Ładowarka", "Kabel"
   ✓ Markę: "Sony", "Anker", "Samsung"
   ✓ Model: "X200", "MX Master 3", "Galaxy S24"
   ✓ Kluczowe parametry: "170°", "45W", "4K", "2m"
   ✓ Materiał: "IP68", "Skóra", "Aluminium"
   ✓ Kolor (jeśli charakterystyczny): "Czarny", "Srebrny"
   ✓ Kod producenta (jeśli krótki): "Type-C", "USB-C"

6. INTELIGENTNE SKRACANIE (gdy przekracza {max_length} znaków):
   PRIORYTET USUWANIA (od pierwszego do ostatniego):
   1. Usuń kolor (jeśli jest standardowy: czarny, biały)
   2. Usuń dodatkowe parametry (zostaw najważniejszy)
   3. Skróć nazwę produktu (np. "Ładowarka Samochodowa" → "Ładowarka")
   4. NIGDY nie skracaj marki i modelu

=== FORMAT ODPOWIEDZI ===

ZWRÓĆ TYLKO TYTUŁ - NIC WIĘCEJ!

NIE PISZ:
❌ "Oto propozycja tytułu:"
❌ "Tytuł oferty:"
❌ "Sugeruję:"
❌ Jakichkolwiek dodatkowych wyjaśnień

NAPISZ:
✓ Sam tytuł oferty (dokładnie {max_length} znaków lub mniej)

=== PRZYKŁADY KOŃCOWE ===

INPUT: "Sony Backup Camera 170 Degree Waterproof IP68 12V Night Vision New"
OUTPUT: Kamera Cofania Sony 170st IP68 12V Nocna

INPUT: "Anker PowerPort III 45W USB-C Car Charger Fast Charging PD Premium Quality"
OUTPUT: Ładowarka Samochodowa Anker 45W USB-C PD

INPUT: "Logitech MX Master 3 Advanced Wireless Mouse Bluetooth High Precision 4000DPI Black"
OUTPUT: Mysz Logitech MX Master 3 Bluetooth 4000DPI Czarna

Wygeneruj tytuł:"""

    try:
        # Wywołanie API Gemini
        response = requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}',
            json={
                'contents': [{'parts': [{'text': prompt}]}],
                'generationConfig': {
                    'temperature': 0.3,  # Niska temperatura = bardziej przewidywalne wyniki
                    'maxOutputTokens': 100,  # Tytuł to max kilkadziesiąt tokenów
                    'topP': 0.95,
                    'topK': 40
                }
            },
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            generated_text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            
            if generated_text:
                # Wyczyść odpowiedź AI - usuń preambuły
                title = _clean_ai_response(generated_text)
                
                # Walidacja długości
                if len(title) > max_length:
                    print(f"[TITLE AI] Tytuł za długi ({len(title)} znaków), ucinam do {max_length}")
                    title = _smart_truncate(title, max_length)
                
                print(f"[TITLE AI] ✅ Wygenerowano: {title} ({len(title)} znaków)")
                return title
            else:
                print(f"[TITLE AI] ⚠️ Brak odpowiedzi z API, używam fallback")
                return _fallback_title(nazwa, max_length)
        else:
            print(f"[TITLE AI] ❌ Błąd API: {response.status_code} - {response.text[:200]}")
            return _fallback_title(nazwa, max_length)
            
    except requests.exceptions.Timeout:
        print(f"[TITLE AI] ⏱️ Timeout API, używam fallback")
        return _fallback_title(nazwa, max_length)
    except Exception as e:
        print(f"[TITLE AI] ❌ Wyjątek: {e}")
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
