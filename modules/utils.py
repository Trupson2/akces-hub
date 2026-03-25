# -*- coding: utf-8 -*-
"""
Utils module - funkcje pomocnicze
"""

import re
import requests

# NOWE API Gemini
try:
    from google import genai
    GEMINI_SDK_AVAILABLE = True
except ImportError:
    GEMINI_SDK_AVAILABLE = False
    print("[WARN]  google.genai not available")

def get_gemini_model():
    """Pobierz wybrany model Gemini z configu"""
    try:
        from modules.database import get_config
        return get_config('gemini_model', 'gemini-2.5-flash')
    except:
        return 'gemini-2.5-flash'

def get_gemini_api_url(api_key):
    """Zwróć URL do Gemini API z wybranym modelem"""
    model = get_gemini_model()
    return f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}'

# ============================================================
# POLSKIE ZNAKI -> ASCII
# ============================================================
def pl_to_ascii(text):
    """Zamienia polskie znaki na ASCII (dla czcionek bez polskich znaków)"""
    if not text:
        return text
    replacements = {
        'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n', 
        'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z',
        'Ą': 'A', 'Ć': 'C', 'Ę': 'E', 'Ł': 'L', 'Ń': 'N',
        'Ó': 'O', 'Ś': 'S', 'Ź': 'Z', 'Ż': 'Z'
    }
    for pl, ascii_char in replacements.items():
        text = text.replace(pl, ascii_char)
    return text

# Prowizje Allegro wg kategorii
ALLEGRO_PROWIZJE = {
    'elektronika': 0.10,
    'motoryzacja': 0.08,
    'dom_ogrod': 0.12,
    'moda': 0.15,
    'inne': 0.11
}

DOSTAWCY = ['Jobalots', 'Warrington', 'Miglo', 'Amazon Returns', 'Inny']

def get_amazon_image_url(asin, use_scraper=False):
    """
    Pobiera URL zdjęcia z Amazona dla danego ASIN.
    
    Metoda: Generuje bezpośredni URL do Amazon media (szybkie, bez scrapowania)
    
    Args:
        asin: kod ASIN produktu (B0XXXXXXXX)
        use_scraper: czy użyć scrapera (wolniejsze ale pewniejsze) - domyślnie False
    
    Returns:
        str: URL do zdjęcia lub pusty string
    """
    if not asin:
        return ''
    asin = str(asin).strip().upper()
    
    if not re.match(r'^B0[A-Z0-9]{8,10}$', asin):
        return ''
    
    # Szybki URL nie działa (Amazon CDN wymaga image ID, nie ASIN)
    if not use_scraper:
        return ''
    
    # Metoda 2: Scraper (wolne, ale pewniejsze)
    try:
        result = scrape_amazon_product(asin)
        if result and result.get('image_url'):
            print(f"[OK] [ASIN] Pobrano zdjęcie z Amazon: {asin}")
            return result['image_url']
    except Exception as e:
        print(f"[WARN] [ASIN] Scraper error dla {asin}: {e}")
    
    return ''


def get_product_image_by_ean(ean):
    """
    Pobiera zdjęcie produktu po kodzie EAN z różnych źródeł.
    Próbuje kilku darmowych API w kolejności.
    
    Args:
        ean: kod EAN/UPC produktu (8-14 cyfr)
    
    Returns:
        str: URL do zdjęcia lub pusty string
    """
    if not ean:
        return ''
    
    ean = str(ean).strip().replace(' ', '').replace('-', '')
    
    # Sprawdź czy to ASIN - jeśli tak, użyj Amazon
    if re.match(r'^B0[A-Z0-9]{8,10}$', ean.upper()):
        return get_amazon_image_url(ean)
    
    # Sprawdź czy to poprawny EAN (8-14 cyfr)
    if not re.match(r'^\d{8,14}$', ean):
        return ''
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
    }
    
    # === 1. UPC Item DB (darmowe, bez klucza) ===
    try:
        url = f'https://api.upcitemdb.com/prod/trial/lookup?upc={ean}'
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('items') and len(data['items']) > 0:
                images = data['items'][0].get('images', [])
                if images:
                    print(f"[OK] [EAN] Znaleziono zdjęcie w UPCitemdb: {ean}")
                    return images[0]
    except Exception as e:
        print(f"[WARN] [EAN] UPCitemdb error: {e}")
    
    # === 2. Open Food Facts (dla żywności) ===
    try:
        url = f'https://world.openfoodfacts.org/api/v0/product/{ean}.json'
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 1 and data.get('product', {}).get('image_url'):
                print(f"[OK] [EAN] Znaleziono zdjęcie w OpenFoodFacts: {ean}")
                return data['product']['image_url']
    except Exception as e:
        print(f"[WARN] [EAN] OpenFoodFacts error: {e}")
    
    # === 3. Open Beauty Facts (dla kosmetyków) ===
    try:
        url = f'https://world.openbeautyfacts.org/api/v0/product/{ean}.json'
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 1 and data.get('product', {}).get('image_url'):
                print(f"[OK] [EAN] Znaleziono zdjęcie w OpenBeautyFacts: {ean}")
                return data['product']['image_url']
    except Exception as e:
        pass
    
    print(f"[WARN] [EAN] Nie znaleziono zdjęcia dla: {ean}")
    return ''


def get_product_image(code, dostawca=None, use_scraper=False):
    """
    Uniwersalna funkcja pobierania zdjęcia produktu.
    Automatycznie wykrywa czy to ASIN czy EAN i używa odpowiedniej metody.
    
    Args:
        code: ASIN lub EAN produktu
        dostawca: opcjonalnie nazwa dostawcy (dla optymalizacji)
        use_scraper: czy użyć scrapera dla ASIN (wolne, domyślnie False)
    
    Returns:
        str: URL do zdjęcia lub pusty string
    """
    if not code:
        return ''
    
    code = str(code).strip()
    
    # Dla Jobalots/Warrington/Amazon - zawsze próbuj jako ASIN pierwszy
    if dostawca and dostawca.lower() in ['jobalots', 'warrington', 'amazon', 'amazon returns']:
        # Nawet jeśli kod nie wygląda na ASIN, spróbuj go jako ASIN
        if len(code) == 10 and code.upper().startswith('B'):
            code = code.upper()
    
    # Jeśli to ASIN (B0XXXXXXXX) - użyj Amazon
    if re.match(r'^B0[A-Z0-9]{8,10}$', code.upper()):
        return get_amazon_image_url(code.upper(), use_scraper=use_scraper)
    
    # Jeśli to EAN (cyfry) - szukaj w bazach produktów
    if re.match(r'^\d{8,14}$', code):
        return get_product_image_by_ean(code)
    
    return ''

def _safe_print(msg):
    """Print bez crashowania na emoji (Windows cp1250)"""
    try:
        print(msg)
    except (UnicodeEncodeError, UnicodeDecodeError):
        try:
            print(msg.encode('ascii', errors='replace').decode('ascii'))
        except:
            pass

def scrape_amazon_product(asin, preferred_domain=None):
    """
    Scrapuje tytul i zdjecia z Amazona
    Returns: dict z title, image_url, all_images, price lub None
    preferred_domain: np. 'co.uk', 'de', 'com' - próbuje tę domenę jako pierwszą
    """
    if not asin:
        return None

    asin = str(asin).strip().upper()
    if not re.match(r'^B0[A-Z0-9]{8,10}$', asin):
        return None

    # Priorytet: amazon.pl (polskie napisy na zdjeciach!), potem reszta
    domains = ['amazon.pl', 'amazon.de', 'amazon.com', 'amazon.co.uk', 'amazon.fr', 'amazon.it', 'amazon.es', 'amazon.com.tr', 'amazon.nl', 'amazon.se']

    # Jeśli użytkownik wybrał domenę, przenieś ją na początek
    if preferred_domain:
        pref = f'amazon.{preferred_domain}'
        if pref not in domains:
            domains.insert(0, pref)
        else:
            domains.remove(pref)
            domains.insert(0, pref)

    # Session z cookies - kluczowe zeby Amazon nie blokowal!
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'pl-PL,pl;q=0.9,de-DE;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Upgrade-Insecure-Requests': '1',
    })

    for domain in domains:  # Próbuj wszystkie domeny aż znajdzie
        try:
            # Krok 1: odwiedz strone glowna zeby dostac cookies (anti-CAPTCHA)
            _safe_print(f"[Amazon] Probuje: {domain}...")
            try:
                session.get(f'https://www.{domain}/ref=cs_503_link', timeout=8, allow_redirects=True)
            except:
                pass

            # Krok 2: pobierz strone produktu
            url = f'https://www.{domain}/dp/{asin}'
            response = session.get(url, timeout=12)
            
            if response.status_code != 200:
                _safe_print(f"   HTTP {response.status_code} - skip")
                continue
            
            text = response.text
            
            # Sprawdz captcha
            if 'captcha' in text.lower() or 'robot check' in text.lower():
                _safe_print(f"   CAPTCHA na {domain}, probuje nastepna...")
                continue
            
            # Tytul - szukaj w HTML (TYLKO z elementów produktowych!)
            title = None
            # Priorytet 1: span#productTitle (pewny tytuł produktu)
            # Priorytet 2: h1#title > span (pewny tytuł produktu)
            title_product_patterns = [
                r'<span id="productTitle"[^>]*>\s*([^<]+?)\s*</span>',
                r'<h1[^>]*id="title"[^>]*>.*?<span[^>]*>([^<]+)</span>',
            ]
            for pattern in title_product_patterns:
                match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
                if match:
                    title = match.group(1).strip()
                    title = re.sub(r'\s+', ' ', title)
                    if len(title) > 15:
                        break
                    title = None

            # Fallback: <title> tag — ale TYLKO jeśli strona ma elementy produktowe
            if not title:
                is_product_page = bool(re.search(r'id="(add-to-cart-button|buybox|priceblock|ppd)"', text, re.IGNORECASE))
                if is_product_page:
                    match = re.search(r'<title>([^<|]+)', text, re.IGNORECASE)
                    if match:
                        title = match.group(1).strip()
                        title = re.sub(r'\s+', ' ', title)
                        title = re.sub(r'\s*[-:|]\s*Amazon\.(de|co\.uk|com|pl).*$', '', title, flags=re.IGNORECASE)
                        title = re.sub(r'^Amazon\.(de|co\.uk|com|pl)\s*[:\-|]\s*', '', title, flags=re.IGNORECASE)
                        title = title.strip()
                        if title.lower().startswith('amazon') or len(title) < 15:
                            title = None
            
            if not title or len(title) < 10:
                _safe_print(f"   Brak tytulu na {domain}")
                continue
            _safe_print(f"   OK Pobrano z {domain}: {title[:50]}")
            
            # ========== BULLET POINTS (FEATURES) ==========
            bullet_points = []
            
            # Metoda 1: feature-bullets lista
            feature_div = re.search(r'<div[^>]*id="feature-bullets"[^>]*>(.*?)</div>\s*</div>', text, re.DOTALL | re.IGNORECASE)
            if feature_div:
                bullets_html = feature_div.group(1)
                # Znajdź wszystkie <li> wewnątrz
                bullets = re.findall(r'<li[^>]*>\s*<span[^>]*>(.*?)</span>', bullets_html, re.DOTALL)
                for bullet in bullets:
                    # Wyczyść HTML
                    clean = re.sub(r'<[^>]+>', '', bullet)
                    clean = clean.strip()
                    if len(clean) > 10 and len(clean) < 500:  # Filtruj za krótkie i za długie
                        bullet_points.append(clean)
            
            # Metoda 2: a-unordered-list a-vertical
            if len(bullet_points) < 3:
                bullets = re.findall(r'<span class="a-list-item"[^>]*>(.*?)</span>', text, re.DOTALL)
                for bullet in bullets[:10]:  # Max 10
                    clean = re.sub(r'<[^>]+>', '', bullet)
                    clean = clean.strip()
                    if len(clean) > 10 and len(clean) < 500 and clean not in bullet_points:
                        bullet_points.append(clean)
            
            # Filtruj treści blokowane przez Allegro (gwarancja, kontakt, obsługa)
            _blocked = ['gwarancj', 'warranty', 'garantie', 'obsług', 'customer service',
                        'kundenservice', 'kontakt', 'contact us', 'skontaktuj',
                        'zwrot', 'return policy', 'refund', 'support team',
                        'obsługa klienta', 'after-sale', 'aftersale', 'after sale']
            bullet_points = [bp for bp in bullet_points
                             if not any(w in bp.lower() for w in _blocked)]

            # Ogranicz do 8 najważniejszych
            bullet_points = bullet_points[:8]
            
            # ========== KATEGORIA ==========
            category = ''
            
            # Metoda 1: breadcrumb navigation
            breadcrumb = re.search(r'<div[^>]*id="wayfinding-breadcrumbs_feature_div"[^>]*>(.*?)</div>', text, re.DOTALL | re.IGNORECASE)
            if breadcrumb:
                # Wyciągnij ostatnią kategorię z breadcrumba
                cats = re.findall(r'>([^<]+)</a>', breadcrumb.group(1))
                if cats:
                    category = cats[-1].strip()
            
            # Metoda 2: Department w HTML
            if not category:
                dept_match = re.search(r'"department"\s*:\s*"([^"]+)"', text)
                if dept_match:
                    category = dept_match.group(1)
            
            # ========== ZDJĘCIA - ULEPSZONE POBIERANIE ==========
            all_images = []
            
            # Metoda 1: colorImages JSON (najlepsza)
            color_match = re.search(r"'colorImages'\s*:\s*\{[^}]*'initial'\s*:\s*(\[[^\]]+\])", text)
            if not color_match:
                color_match = re.search(r'"colorImages"\s*:\s*\{[^}]*"initial"\s*:\s*(\[[^\]]+\])', text)
            
            if color_match:
                try:
                    import json
                    gallery_str = color_match.group(1).replace("'", '"')
                    gallery_data = json.loads(gallery_str)
                    for item in gallery_data:
                        if isinstance(item, dict):
                            # Preferuj hiRes, potem large
                            img_url = item.get('hiRes') or item.get('large') or item.get('main', {}).get('url')
                            if img_url and '/I/' in img_url and img_url not in all_images:
                                clean_url = re.sub(r'\._[A-Z0-9_,]+_\.', '._AC_SL1500_.', img_url)
                                if clean_url not in all_images:
                                    all_images.append(clean_url)
                except:
                    pass
            
            # Metoda 2: imageGalleryData
            if len(all_images) < 4:
                gallery_match = re.search(r'"imageGalleryData"\s*:\s*(\[[^\]]+\])', text)
                if gallery_match:
                    try:
                        urls = re.findall(r'"mainUrl"\s*:\s*"([^"]+)"', gallery_match.group(1))
                        for url in urls:
                            if '/I/' in url:
                                clean_url = re.sub(r'\._[A-Z0-9_,]+_\.', '._AC_SL1500_.', url)
                                if clean_url not in all_images:
                                    all_images.append(clean_url)
                    except:
                        pass
            
            # Metoda 3: Wszystkie hiRes/large z całego dokumentu
            if len(all_images) < 4:
                all_hires = re.findall(r'"hiRes"\s*:\s*"(https://[^"]+)"', text)
                all_large = re.findall(r'"large"\s*:\s*"(https://[^"]+)"', text)
                
                for img_list in [all_hires, all_large]:
                    for img in img_list:
                        if '/I/' in img and not any(x in img.lower() for x in ['icon', 'button', 'sprite', 'transparent']):
                            clean_url = re.sub(r'\._[A-Z0-9_,]+_\.', '._AC_SL1500_.', img)
                            if clean_url not in all_images:
                                all_images.append(clean_url)
                        if len(all_images) >= 8:
                            break
            
            # Metoda 4: data-old-hires atrybuty
            if len(all_images) < 4:
                hires_attrs = re.findall(r'data-old-hires="([^"]+)"', text)
                for img in hires_attrs:
                    if '/I/' in img:
                        clean_url = re.sub(r'\._[A-Z0-9_,]+_\.', '._AC_SL1500_.', img)
                        if clean_url not in all_images:
                            all_images.append(clean_url)
            
            # Metoda 5: landingImage
            if len(all_images) < 4:
                landing = re.search(r'"landingImageUrl"\s*:\s*"([^"]+)"', text)
                if landing:
                    img = landing.group(1)
                    clean_url = re.sub(r'\._[A-Z0-9_,]+_\.', '._AC_SL1500_.', img)
                    if clean_url not in all_images:
                        all_images.append(clean_url)
            
            # Fallback - generuj URL z ASIN
            if not all_images:
                all_images = [f'https://m.media-amazon.com/images/I/{asin}._AC_SL1500_.jpg']
            
            # Główne zdjęcie
            image_url = all_images[0]
            
            # Cena
            price = None
            price_patterns = [
                r'class="a-price-whole"[^>]*>([0-9,.\s]+)<',
                r'"priceAmount"\s*:\s*([0-9.]+)',
                r'<span[^>]*class="[^"]*a-price[^"]*"[^>]*>.*?<span[^>]*>([0-9,.\s]+)',
            ]
            for pattern in price_patterns:
                match = re.search(pattern, text, re.DOTALL)
                if match:
                    try:
                        price_str = match.group(1).replace(' ', '').replace(',', '.')
                        if price_str.count('.') > 1:
                            parts = price_str.rsplit('.', 1)
                            price_str = parts[0].replace('.', '') + '.' + parts[1]
                        price = float(price_str)
                        if price > 0 and price < 10000:
                            break
                    except:
                        pass
            
            # ========== PRODUCT SPECIFICATIONS ==========
            product_specs = {}

            # Method 1: Technical Details table (prodDetTable)
            spec_tables = re.findall(
                r'<table[^>]*(?:id="productDetails_techSpec_section_1"|class="[^"]*prodDetTable[^"]*")[^>]*>(.*?)</table>',
                text, re.DOTALL | re.IGNORECASE
            )
            for table_html in spec_tables:
                rows = re.findall(r'<tr>(.*?)</tr>', table_html, re.DOTALL)
                for row in rows:
                    th = re.search(r'<th[^>]*>(.*?)</th>', row, re.DOTALL)
                    td = re.search(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                    if th and td:
                        key = re.sub(r'<[^>]+>', '', th.group(1)).strip().replace('\n', ' ').strip()
                        val = re.sub(r'<[^>]+>', '', td.group(1)).strip().replace('\n', ' ').strip()
                        if key and val and len(key) < 100 and len(val) < 500:
                            product_specs[key] = val

            # Method 2: Detail Bullets (alternate format)
            detail_section = re.search(
                r'id="detailBulletsWrapper_feature_div"[^>]*>(.*?)</div>\s*</div>\s*</div>',
                text, re.DOTALL | re.IGNORECASE
            )
            if detail_section:
                pairs = re.findall(
                    r'<span\s+class="a-text-bold"[^>]*>(.*?)</span>.*?</span>\s*<span[^>]*>(.*?)</span>',
                    detail_section.group(1), re.DOTALL
                )
                for key_html, val_html in pairs:
                    key = re.sub(r'<[^>]+>', '', key_html).strip().rstrip(':').rstrip('\u200f').rstrip('\u200e').strip()
                    val = re.sub(r'<[^>]+>', '', val_html).strip().rstrip('\u200f').rstrip('\u200e').strip()
                    if key and val and key not in product_specs and len(key) < 100:
                        product_specs[key] = val

            # Method 3: Additional Information table
            addl_table = re.search(
                r'id="productDetails_detailBullets_sections1"[^>]*>(.*?)</table>',
                text, re.DOTALL | re.IGNORECASE
            )
            if addl_table:
                rows = re.findall(r'<tr>(.*?)</tr>', addl_table.group(1), re.DOTALL)
                for row in rows:
                    th = re.search(r'<th[^>]*>(.*?)</th>', row, re.DOTALL)
                    td = re.search(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                    if th and td:
                        key = re.sub(r'<[^>]+>', '', th.group(1)).strip().replace('\n', ' ').strip()
                        val = re.sub(r'<[^>]+>', '', td.group(1)).strip().replace('\n', ' ').strip()
                        if key and val and key not in product_specs:
                            product_specs[key] = val

            # Cleanup: remove ASIN from specs (already have it)
            product_specs.pop('ASIN', None)
            product_specs.pop('asin', None)

            if product_specs:
                print(f"[Specs] Extracted {len(product_specs)} specs: {list(product_specs.keys())[:5]}...")

            _safe_print(f"[Amazon {domain}] {asin}: {len(all_images)} zdjec, {len(bullet_points)} cech, cena: {price}")

            # ========== TŁUMACZENIE NA POLSKI (AUTO) ==========
            # Tłumacz tytuł
            title_pl = translate_product_name(title, use_ai=True)
            
            # Tłumacz bullet points (każdy osobno)
            bullet_points_pl = []
            for bp in bullet_points:
                bp_pl = translate_text(bp, use_ai=True)
                bullet_points_pl.append(bp_pl)
            
            # Tłumacz kategorię
            category_pl = translate_text(category, use_ai=True) if category else ''
            
            if title_pl != title:
                _safe_print(f"  Przetlumaczono: {title[:40]}... -> {title_pl[:40]}...")
            
            return {
                'title': title_pl,  # Przetłumaczony tytuł
                'image_url': image_url,
                'all_images': all_images[:8],  # Max 8 zdjęć
                'price': price,
                'domain': domain,
                'bullet_points': bullet_points_pl,  # Przetłumaczone cechy
                'category': category_pl,  # Przetłumaczona kategoria
                'product_specs': product_specs
            }
            
        except Exception as e:
            _safe_print(f"[Amazon {domain}] Blad: {e}")
            continue
    
    # Fallback - zwróć podstawowe dane
    return {
        'title': f'Produkt Amazon {asin}',
        'image_url': f'https://m.media-amazon.com/images/I/{asin}._AC_SL1500_.jpg',
        'all_images': [f'https://m.media-amazon.com/images/I/{asin}._AC_SL1500_.jpg'],
        'price': None,
        'domain': None,
        'bullet_points': [],
        'category': '',
        'product_specs': {}
    }

def optimize_title_seo(title, max_length=75):
    """
    Optymalizuje tytul pod SEO Allegro:
    - Wyciaga kolor i tlumczy na polski
    - Wyciaga ilosc sztuk
    - Wyciaga kluczowe cechy
    - Formatuje: Marka Produkt Cechy Kolor Ilosc
    """
    if not title:
        return ''
    
    # Slownik kolorow EN -> PL
    colors_map = {
        'black': 'czarny', 'white': 'biały', 'red': 'czerwony', 'blue': 'niebieski',
        'green': 'zielony', 'yellow': 'żółty', 'orange': 'pomarańczowy', 'pink': 'różowy',
        'purple': 'fioletowy', 'grey': 'szary', 'gray': 'szary', 'brown': 'brązowy',
        'beige': 'beżowy', 'gold': 'złoty', 'silver': 'srebrny', 'navy': 'granatowy',
        'burgundy': 'bordowy', 'cream': 'kremowy', 'tan': 'jasnobrązowy',
        'schwarz': 'czarny', 'weiß': 'biały', 'rot': 'czerwony', 'blau': 'niebieski',
        'grün': 'zielony', 'grau': 'szary', 'braun': 'brązowy',
    }
    
    # Wyciagnij kolor
    color_found = ''
    title_lower = title.lower()
    for en, pl in colors_map.items():
        if en in title_lower:
            color_found = pl
            # Usun kolor z oryginalnego tytulu
            title = re.sub(rf'\b{en}\b', '', title, flags=re.IGNORECASE)
            break
    
    # Wyciagnij ilosc (5 Pack, Set of 2, 2 Stück, x2, 2pcs)
    quantity_found = ''
    qty_patterns = [
        r'(\d+)\s*(?:pack|pcs|pieces|stück|sztuk|szt)',
        r'set\s*(?:of\s*)?(\d+)',
        r'(\d+)\s*(?:er\s*)?set',
        r'x\s*(\d+)',
        r'(\d+)\s*x\s*(?!\d)',  # "5 x " ale nie "5 x 10"
    ]
    for pattern in qty_patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            qty = match.group(1)
            if int(qty) > 1:
                quantity_found = f'{qty} szt'
                # Usun z tytulu
                title = re.sub(pattern, '', title, flags=re.IGNORECASE)
            break
    
    # Usun emoji i zbedne znaki
    title = re.sub(r'[^\w\s\-.,&()/äöüßÄÖÜ]', '', title, flags=re.UNICODE)
    title = re.sub(r'\s+', ' ', title).strip()
    
    # Usun zbedne slowa na poczatku
    remove_words = [
        'neu', 'new', 'original', 'genuine', 'brand new', 'hot sale', 'sale',
        'premium', 'hochwertig', 'qualität', 'top', 'best', 'upgrade',
        'universal', 'universell', 'für', 'for', 'auto', 'car',
    ]
    words = title.split()
    while words and words[0].lower() in remove_words:
        words.pop(0)
    title = ' '.join(words)
    
    # Usun zbedne slowa na koncu
    remove_end = ['amazon', 'de', 'uk', 'com', 'choice', 'basics']
    while words and words[-1].lower() in remove_end:
        words.pop()
    title = ' '.join(words)
    
    # Przetlumacz kluczowe slowa produktowe
    translations = {
        'seat cover': 'Pokrowce na siedzenia',
        'seat covers': 'Pokrowce na siedzenia',
        'car seat cover': 'Pokrowce samochodowe',
        'front seat': 'przednie',
        'rear seat': 'tylne',
        'back seat': 'tylne',
        'waterproof': 'wodoodporne',
        'leather': 'skórzane',
        'pu leather': 'ekoskóra',
        'full set': 'komplet',
        'charging cable': 'Kabel do ładowania',
        'wallbox': 'Wallbox ładowarka',
    }
    
    title_lower = title.lower()
    for en, pl in translations.items():
        if en in title_lower:
            title = re.sub(rf'\b{re.escape(en)}\b', pl, title, flags=re.IGNORECASE)
    
    # Dodaj kolor i ilosc na koncu jesli znalezione
    suffix = ''
    if color_found:
        suffix += f' {color_found}'
    if quantity_found:
        suffix += f' {quantity_found}'
    
    # Oblicz max dlugosc tytulu bez suffixu
    max_title = max_length - len(suffix)
    
    # Skroc jesli za dlugi
    if len(title) > max_title:
        cut_pos = title[:max_title].rfind(' ')
        if cut_pos > max_title * 0.5:
            title = title[:cut_pos]
        else:
            title = title[:max_title]
    
    # Polacz
    final_title = (title + suffix).strip()
    
    # Ostateczne czyszczenie
    final_title = re.sub(r'\s+', ' ', final_title)
    final_title = re.sub(r'\s*,\s*,', ',', final_title)
    final_title = final_title.strip(' ,.-')
    
    return final_title[:max_length]

def is_valid_asin(code):
    """Sprawdza czy kod to prawidlowy ASIN"""
    if not code:
        return False
    code = str(code).strip().upper()
    return bool(re.match(r'^B0[A-Z0-9]{8,10}$', code))

def is_valid_ean(code):
    """Sprawdza czy kod to prawidlowy EAN"""
    if not code:
        return False
    code = str(code).strip()
    return bool(re.match(r'^\d{8,14}$', code))

def is_code(q):
    """Sprawdza czy string to kod produktu (ASIN, EAN lub MAG-kod)"""
    q = str(q).strip().upper()
    return is_valid_asin(q) or is_valid_ean(q.replace(' ', '')) or q.startswith('MAG-')

def oblicz_cene_allegro(cena_zakupu, marza_procent=40, kategoria='inne', vat=0.23):
    """
    Oblicza cene sprzedazy na Allegro z uwzglednieniem prowizji i VAT.
    cena_zakupu - cena netto zakupu
    Wynikowa cena_allegro jest ceną BRUTTO (z VAT).
    """
    prowizja = ALLEGRO_PROWIZJE.get(kategoria, 0.11)
    
    # Oblicz cenę netto sprzedaży (pokrywającą marżę i prowizję Allegro)
    cena_allegro_netto = cena_zakupu * (1 + marza_procent/100) / (1 - prowizja)
    # Przelicz na brutto (z VAT)
    cena_allegro = cena_allegro_netto * (1 + vat)
    
    marza_kwota = cena_zakupu * marza_procent / 100
    prowizja_kwota = cena_allegro_netto * prowizja
    vat_kwota = cena_allegro - cena_allegro_netto
    zysk_netto = cena_allegro_netto - cena_zakupu - prowizja_kwota
    
    cena_sugerowana = int(cena_allegro) + 0.99 if cena_allegro > 10 else round(cena_allegro, 2)
    
    return {
        'cena_zakupu': f"{cena_zakupu:.2f}",
        'marza': marza_procent,
        'marza_kwota': f"{marza_kwota:.2f}",
        'prowizja_procent': int(prowizja * 100),
        'prowizja_kwota': f"{prowizja_kwota:.2f}",
        'vat_procent': int(vat * 100),
        'vat_kwota': f"{vat_kwota:.2f}",
        'cena_allegro_netto': f"{cena_allegro_netto:.2f}",
        'cena_allegro': f"{cena_allegro:.2f}",
        'zysk_netto': f"{zysk_netto:.2f}",
        'cena_sugerowana': f"{cena_sugerowana:.2f}",
        'cena_allegro_raw': cena_allegro
    }


# ============================================================
# TŁUMACZENIE NAZW PRODUKTÓW
# ============================================================

# Słownik popularnych słów DE/EN -> PL
TRANSLATIONS = {
    # Sprzęt fitness
    'laufband': 'bieżnia', 'treadmill': 'bieżnia',
    'heimtrainer': 'rower treningowy', 'exercise bike': 'rower treningowy',
    'hantel': 'hantel', 'dumbbell': 'hantel',
    'klimmzugstange': 'drążek do podciągania',
    'fitnessmatte': 'mata fitness', 'yoga mat': 'mata do jogi',
    'rudergerät': 'wioślarz', 'rowing machine': 'wioślarz',
    
    # Elektronika
    'kamera': 'kamera', 'camera': 'kamera',
    'überwachungskamera': 'kamera monitoringu',
    'lautsprecher': 'głośnik', 'speaker': 'głośnik',
    'kopfhörer': 'słuchawki', 'headphones': 'słuchawki',
    'ladegerät': 'ładowarka', 'charger': 'ładowarka',
    'tastatur': 'klawiatura', 'keyboard': 'klawiatura',
    'maus': 'mysz', 'mouse': 'mysz',
    'bildschirm': 'monitor', 'screen': 'ekran',
    'fernseher': 'telewizor', 'tv': 'telewizor',
    'staubsauger': 'odkurzacz', 'vacuum cleaner': 'odkurzacz',
    
    # Dom
    'schreibtisch': 'biurko', 'desk': 'biurko',
    'stuhl': 'krzesło', 'chair': 'krzesło',
    'bürostuhl': 'krzesło biurowe', 'office chair': 'krzesło biurowe',
    'lampe': 'lampa', 'lamp': 'lampa',
    'regal': 'regał', 'shelf': 'półka',
    'bett': 'łóżko', 'bed': 'łóżko',
    'matratze': 'materac', 'mattress': 'materac',
    'sofa': 'sofa', 'couch': 'kanapa',
    'tisch': 'stół', 'table': 'stół',
    'schrank': 'szafa', 'wardrobe': 'szafa',
    
    # Motoryzacja
    'autositzbezug': 'pokrowiec samochodowy',
    'fußmatte': 'dywanik', 'floor mat': 'dywanik',
    'ladekabel': 'kabel ładowania', 'charging cable': 'kabel ładowania',
    'wallbox': 'wallbox', 'ev charger': 'ładowarka EV',
    'reifen': 'opona', 'tire': 'opona',
    
    # Ogólne
    'set': 'zestaw', 'kit': 'zestaw',
    'ersatz': 'zamiennik', 'replacement': 'zamiennik',
    'universal': 'uniwersalny',
    'wasserdicht': 'wodoodporny', 'waterproof': 'wodoodporny',
    'kabellos': 'bezprzewodowy', 'wireless': 'bezprzewodowy',
    'tragbar': 'przenośny', 'portable': 'przenośny',
    'faltbar': 'składany', 'foldable': 'składany',
    'höhenverstellbar': 'z regulacją wysokości',
    'schwarz': 'czarny', 'black': 'czarny',
    'weiß': 'biały', 'white': 'biały',
    'grau': 'szary', 'grey': 'szary', 'gray': 'szary',
    'rot': 'czerwony', 'red': 'czerwony',
    'blau': 'niebieski', 'blue': 'niebieski',
    'grün': 'zielony', 'green': 'zielony',
}

def translate_product_name(name: str, use_ai: bool = True) -> str:
    """
    Tłumaczy nazwę produktu z niemieckiego/angielskiego na polski.

    Args:
        name: Nazwa produktu
        use_ai: Czy użyć Gemini AI do tłumaczenia
        
    Returns:
        Przetłumaczona nazwa (lokalny słownik)
    """
    if not name:
        return name
    
    from .database import get_config
    
    # Najpierw sprawdź słownik lokalny
    name_lower = name.lower()
    translated_parts = []
    
    for word in name.split():
        word_lower = word.lower().strip(',.;:!?()[]')
        if word_lower in TRANSLATIONS:
            translated_parts.append(TRANSLATIONS[word_lower])
        else:
            translated_parts.append(word)
    
    local_translation = ' '.join(translated_parts)
    
    # Jeśli coś się zmieniło lokalnie, użyj tego
    if local_translation.lower() != name_lower:
        # Popraw kapitalizację
        return local_translation.capitalize()
    
    # Spróbuj AI jeśli włączone i jest klucz
    if use_ai:
        api_key = get_config('gemini_api_key', '')
        if api_key:
            try:
                import requests
                
                prompt = f"""Jesteś tłumaczem nazw produktów na Allegro. Przetłumacz poniższą nazwę produktu na POLSKI.

ZASADY:
- Nazwy własne, marki i modele (np. "Samsung", "iPhone", "Bosch") ZACHOWAJ bez zmian
- Jednostki (cm, kg, W, mAh) ZACHOWAJ bez zmian
- Numery modeli ZACHOWAJ bez zmian
- Przetłumacz TYLKO słowa opisowe (przymiotniki, rzeczowniki pospolite, kolory)
- Zachowaj oryginalną strukturę nazwy (kolejność słów jak w oryginale, ale po polsku)
- NIE dodawaj słów których nie ma w oryginale
- NIE zmieniaj wielkości liter nazw własnych
- Jeśli nazwa jest już po polsku, zwróć ją bez zmian

PRZYKŁADY:
"UGREEN USB C to Ethernet Adapter 2.5G" → "UGREEN Adapter USB C na Ethernet 2.5G"
"Autositzbezüge Vordersitze Schwarz" → "Pokrowce na przednie fotele Czarne"
"Wireless Bluetooth Headphones with Noise Cancelling" → "Bezprzewodowe słuchawki Bluetooth z redukcją szumów"
"Laufband Klappbar mit Display 120kg" → "Bieżnia składana z wyświetlaczem 120kg"

Nazwa do tłumaczenia: {name}

Odpowiedz TYLKO przetłumaczoną nazwą, bez cudzysłowów, bez komentarzy:"""

                response = requests.post(
                    get_gemini_api_url(api_key),
                    json={
                        'contents': [{'parts': [{'text': prompt}]}],
                        'generationConfig': {'maxOutputTokens': 100, 'temperature': 0.1}
                    },
                    timeout=30  # Zwiększony timeout z 10s do 30s
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if 'candidates' in data and data['candidates']:
                        translated = data['candidates'][0]['content']['parts'][0]['text'].strip()
                        if translated and len(translated) > 5:
                            return translated
            except Exception as e:
                print(f"[WARN] Błąd tłumaczenia AI: {e}")
    
    return name


def translate_text(text: str, use_ai: bool = True) -> str:
    """
    Tłumaczy dowolny tekst (opis, bullet point) z niemieckiego/angielskiego na polski.
    
    Args:
        text: Tekst do przetłumaczenia
        use_ai: Czy użyć Gemini AI do tłumaczenia
        
    Returns:
        Przetłumaczony tekst
    """
    if not text or len(text) < 5:
        return text
    
    from .database import get_config
    
    # Sprawdź czy to już polski (proste heurystyki)
    polish_indicators = ['ą', 'ć', 'ę', 'ł', 'ń', 'ó', 'ś', 'ź', 'ż']
    if any(char in text.lower() for char in polish_indicators):
        return text  # Prawdopodobnie już polski
    
    # Spróbuj AI jeśli włączone i jest klucz
    if use_ai:
        api_key = get_config('gemini_api_key', '')
        if api_key:
            try:
                import requests
                
                prompt = f"""Przetłumacz ten tekst z niemieckiego lub angielskiego na polski. 
Zachowaj format i długość tekstu.
Odpowiedz TYLKO przetłumaczonym tekstem, bez dodatkowych komentarzy.
Jeśli tekst jest już po polsku, zostaw go bez zmian.

Tekst: {text}

Tłumaczenie:"""

                response = requests.post(
                    get_gemini_api_url(api_key),
                    json={
                        'contents': [{'parts': [{'text': prompt}]}],
                        'generationConfig': {'maxOutputTokens': 300, 'temperature': 0.1}
                    },
                    timeout=30  # Zwiększony timeout z 15s do 30s
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if 'candidates' in data and data['candidates']:
                        translated = data['candidates'][0]['content']['parts'][0]['text'].strip()
                        if translated and len(translated) > 3:
                            return translated
            except Exception as e:
                print(f"[WARN] Błąd tłumaczenia tekstu AI: {e}")
    
    return text


def generuj_opis_ai(nazwa, kategoria='inne', bullet_points=None, gemini_key=None):
    """
    Generuje profesjonalny opis produktu do Allegro używając Gemini API.
    ZMIANA: Używa bullet_points z Amazona zamiast wymyślać ogólniki.
    
    Args:
        nazwa: nazwa produktu
        kategoria: kategoria produktu
        bullet_points: lista cech z Amazona (teksty)
        gemini_key: klucz API Gemini
    """
    from .database import get_config
    
    # Sprawdź czy SDK jest dostępne
    if not GEMINI_SDK_AVAILABLE:
        print("[Gemini] SDK nie jest zainstalowane - używam fallback")
        return generuj_opis_fallback(nazwa, kategoria)
    
    api_key = gemini_key or get_config('gemini_api_key', '')
    
    if api_key and bullet_points and len(bullet_points) > 0:
        try:
            # Formatuj bullet points do promptu
            features_text = '\n'.join([f'- {bp}' for bp in bullet_points])
            
            # Określ typ produktu na podstawie nazwy
            nazwa_lower = nazwa.lower()
            
            # Czy to elektronika/urządzenie?
            is_electronics = any(word in nazwa_lower for word in [
                'ładowarka', 'kabel', 'adapter', 'powerbank', 'słuchawki', 'głośnik',
                'mysz', 'klawiatura', 'usb', 'hdmi', 'bluetooth', 'wifi', 'router',
                'lampka', 'led', 'zasilacz', 'bateria', 'akumulator'
            ])
            
            # Czy to dekoracja/materiał/ozdoba?
            is_decoration = any(word in nazwa_lower for word in [
                'tło', 'zasłona', 'banner', 'ozdoba', 'dekoracja', 'cekin', 
                'balkon', 'girlanda', 'konfetti', 'serwetka', 'obrus', 'tkanina'
            ])
            
            # PROMPT - ROZBUDOWANE OPISY v3 (konkretne, bez lania wody)
            prompt = f"""Jesteś doświadczonym sprzedawcą na Allegro. Pisz jak ekspert, który ZNA produkt — nie jak marketingowiec który wypełnia szablon.

PRODUKT: {nazwa}

CECHY Z AMAZONA (to Twoje jedyne źródło — NIE wymyślaj):
{features_text}

TYP: {'Elektronika/Urządzenie' if is_electronics else ('Dekoracja/Materiał' if is_decoration else 'Produkt fizyczny')}

=== STRUKTURA ===

**Wprowadzenie** (4-5 zdań)
- Zdanie 1: Czym jest produkt (konkretnie, z parametrami)
- Zdanie 2-3: Główne funkcje i do czego służy
- Zdanie 4-5: Dla kogo i w jakich sytuacjach

**Cechy i parametry** (8-12 punktów)
- Każdy punkt: NAZWA CECHY → co robi → konkretna wartość/parametr z bullet points
- Każdy punkt to 1-2 zdania, zwięzłe ale treściwe
- TYLKO fakty z bullet points — nie wymyślaj parametrów których nie ma

**Zastosowanie** (5-8 konkretnych scenariuszy)
- Realne sytuacje użycia, nie ogólniki
- Np. "w samochodzie podczas długiej trasy" zamiast "w wielu sytuacjach"

**Specyfikacja**
- Lista parametrów: wymiary, waga, materiał, kompatybilność
- TYLKO to co wynika z bullet points

**Podsumowanie** (2-3 zdania)
- Dla kogo ten produkt jest najlepszy
- Główna przewaga

=== STYL ===
✓ Pisz po polsku, naturalnie — jak opis od kogoś kto testował produkt
✓ Konkretne parametry i liczby (z bullet points!)
✓ Zwięzłe zdania — każde niesie informację
✓ 2500-4000 znaków
✓ Pisz "ten produkt", "to urządzenie" — nie powtarzaj pełnej nazwy

=== ZAKAZ ===
<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Puste frazesy: "najwyższa jakość", "wyjątkowe wykonanie", "innowacyjne rozwiązanie"
<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Wymyślanie parametrów których nie ma w bullet points
<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Sekcje: wysyłka, zwroty, kontakt, gwarancja, GPSR
<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Tytuł produktu na początku
<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Wymiary typu "10x2.75" to ROZMIARY, nie ilości sztuk
<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Wymyślanie ilości sztuk w zestawie jeśli nie podano

Wygeneruj opis:"""

            # NOWE API google.genai
            try:
                client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model=get_gemini_model(),
                    contents=prompt,
                    config={
                        'temperature': 0.9,
                        'max_output_tokens': 8000  # ZWIĘKSZONE z 4000 na 8000
                    }
                )
                try:
                    from .pallet_monitor import log_gemini_usage
                    log_gemini_usage(response, 'description')
                except: pass

                # Parse response
                text = None
                if hasattr(response, 'text') and response.text:
                    text = response.text.strip()
                elif hasattr(response, 'candidates') and len(response.candidates) > 0:
                    candidate = response.candidates[0]
                    if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                        parts_text = []
                        for part in candidate.content.parts:
                            if hasattr(part, 'text') and part.text:
                                parts_text.append(part.text)
                        if parts_text:
                            text = ''.join(parts_text).strip()
                
                if text:
                    # Usuń niechciane sekcje
                    lines = text.strip().split('\n')
                    filtered = []
                    skip_words = ['wysyłka', 'wysylka', 'zwrot', 'gwarancj', 'kontakt', 'informacj']
                    for line in lines:
                        line_lower = line.lower()
                        if not any(word in line_lower for word in skip_words):
                            filtered.append(line)
                    return '\n'.join(filtered).strip()
            except Exception as e:
                print(f"[Gemini] Błąd nowego API: {e}")
        except Exception as e:
            print(f"[Gemini] Błąd: {e}")
    
    # Fallback - szablon bez API lub bez bullet points
    return generuj_opis_fallback(nazwa, kategoria)


def generuj_opis_fallback(nazwa, kategoria='inne'):
    """Generuje opis bez AI - konkretne szablony dla różnych kategorii"""
    nazwa_lower = nazwa.lower()
    
    # Pokrowce samochodowe
    if any(x in nazwa_lower for x in ['pokrowc', 'seat', 'cover', 'sitzbezug']):
        material = "ekoskóra" if 'leather' in nazwa_lower or 'leder' in nazwa_lower else "tkanina"
        return f"""{nazwa}

Pokrowce na fotele samochodowe.

**Specyfikacja:**
- Materiał: {material}
- Kolor: Czarny
- Montaż: Elastyczne paski mocujące
- Zestaw: Przednie fotele z zagłówkami
- Stan: Nowy"""

    # Kable EV / ładowarki
    elif any(x in nazwa_lower for x in ['cable', 'kabel', 'charger', 'ev', 'type 2', 'type2', 'ladowarka', 'charging']):
        moc = "7.4kW" if '32a' in nazwa_lower else "3.7kW" if '16a' in nazwa_lower else "11kW"
        return f"""{nazwa}

Kabel ładujący do samochodów elektrycznych.

**Specyfikacja:**
- Moc: do {moc}
- Złącze: Type 2 (standard europejski)
- Długość: 5 metrów
- Zabezpieczenia: Termiczne, przeciążeniowe
- Stan: Nowy"""

    # Dywaniki samochodowe
    elif any(x in nazwa_lower for x in ['dywanik', 'mata', 'floor mat', 'fussmat', 'gumow']):
        material = "TPE" if 'tpe' in nazwa_lower or '3w' in nazwa_lower else "guma"
        return f"""{nazwa}

Dywaniki samochodowe.

**Specyfikacja:**
- Materiał: {material.upper()}
- Kolor: Czarny
- Właściwości: Antypoślizgowe, wysoki rand
- Stan: Nowy"""

    # Elektronika / gadżety
    elif any(x in nazwa_lower for x in ['bluetooth', 'wireless', 'usb', 'adapter', 'hub', 'camera', 'speaker']):
        return f"""{nazwa}

Urządzenie elektroniczne.

**Specyfikacja:**
- Kompatybilność: Windows, Mac, Android, iOS
- Materiał: Plastik ABS
- Kolor: Czarny
- Stan: Nowy"""

    # Domyślny szablon
    else:
        return f"""{nazwa}

**Specyfikacja:**
- Stan: Nowy
- Zgodny z opisem producenta

Szczegóły produktu dostępne w zdjęciach."""

def format_price(price):
    """Formatuje cene do wyswietlenia"""
    if price is None:
        return "0.00"
    return f"{float(price):.2f}"

# Alias
parse_price = format_price

def detect_supplier(headers):
    """Wykrywa dostawce na podstawie naglowkow Excel"""
    headers_str = ' '.join(str(h) for h in headers).upper()
    
    if 'WARRINGTON' in headers_str or 'MANIFESTO' in headers_str:
        return 'Warrington'
    elif 'JOBALOTS' in headers_str:
        return 'Jobalots'
    elif 'MIGLO' in headers_str:
        return 'Miglo'
    elif 'AMAZON' in headers_str:
        return 'Amazon Returns'
    
    return None

def clean_product_name(name, max_length=75):
    """Czysci nazwe produktu do Allegro (max 75 znakow)"""
    if not name:
        return ''
    
    # Usun nadmiarowe spacje
    name = ' '.join(name.split())
    
    # Skroc do max_length
    if len(name) > max_length:
        name = name[:max_length-3] + '...'
    
    return name


def generuj_dedykowany_prompt(nazwa, kategoria, bullet_points, gemini_key):
    """
    ETAP 1: Generuje dedykowany prompt pod konkretny produkt.
    Analizuje produkt i tworzy spersonalizowany prompt do generowania opisu.
    
    Args:
        nazwa: nazwa produktu
        kategoria: kategoria z Amazona
        bullet_points: lista cech produktu
        gemini_key: klucz API Gemini
    
    Returns:
        str: Wygenerowany dedykowany prompt lub None jeśli błąd
    """
    if not gemini_key or not bullet_points:
        return None
    
    try:
        # Formatuj cechy produktu
        features_text = '\n'.join([f'- {bp}' for bp in bullet_points[:8]])
        
        meta_prompt = f"""Jesteś ekspertem od tworzenia promptów dla AI generujących opisy produktów na Allegro.

TWOJE ZADANIE: Przeanalizuj produkt i wygeneruj DEDYKOWANY, SZCZEGÓŁOWY prompt do wygenerowania opisu tego konkretnego produktu.

=== PRODUKT DO ANALIZY ===
NAZWA: {nazwa}
KATEGORIA: {kategoria}

CECHY Z AMAZONA:
{features_text}

=== TWOJA ANALIZA (Krok po kroku) ===

1. OKREŚL TYP PRODUKTU:
   - Czy to elektronika, akcesoria, ubrania, dekoracje, narzędzia?
   - Jaki jest główny cel użycia tego produktu?
   - Kto jest grupą docelową (dzieci, profesjonaliści, entuzjaści, wszyscy)?

2. ZIDENTYFIKUJ KLUCZOWE PARAMETRY:
   - Jakie liczby, wymiary, specyfikacje techniczne są w cechach?
   - Jakie materiały, kolory, wzory?
   - Jakie funkcje, możliwości, zastosowania?

3. WYKRYJ CO JEST NAJWAŻNIEJSZE:
   - Co wyróżnia TEN produkt na tle podobnych?
   - Które cechy są najbardziej wartościowe dla kupującego?
   - Jakie problemy rozwiązuje ten produkt?

=== GENERUJ PROMPT ===

Na podstawie powyższej analizy, wygeneruj KOMPLETNY prompt do stworzenia opisu tego produktu.

Prompt MUSI zawierać:
- Dokładny typ produktu i jego przeznaczenie
- Listę KONKRETNYCH parametrów do wyciągnięcia z cech (np. "znajdź rozdzielczość nagrania", "znajdź materiał wykonania")
- Wskazówki co jest najważniejsze dla kupującego
- Strukturę opisu (intro + 5 features)
- Zakazy ogólników i marketingu

KRYTYCZNE ZASADY dla promptu:
1. "TYLKO FAKTY z cech - ZERO wymyślania"
2. "Jeśli czegoś NIE MA w cechach, NIE wspominaj o tym"
3. "Konkretne liczby, wymiary, parametry z cech"
4. "ZAKAZ: 'wysoka jakość', 'niezawodny', 'idealny', 'premium'"

Odpowiedz TYLKO samym promptem w formacie JSON:
{{
    "product_analysis": "Krótka analiza typu produktu (1-2 zdania)",
    "main_selling_points": ["kluczowy punkt 1", "punkt 2", "punkt 3"],
    "prompt": "TUTAJ PEŁNY SZCZEGÓŁOWY PROMPT do generowania opisu, minimum 300 słów, z konkretnymi instrukcjami co wyciągnąć z cech i jak to przedstawić"
}}

Przykład DOBREGO dedykowanego promptu (dla kamery):
"Jesteś ekspertem od kamer samochodowych. PRODUKT: [nazwa]. Przeanalizuj cechy i wyciągnij: 1) rozdzielczość nagrania (szukaj 4K/1080p/720p), 2) kąt widzenia obiektywu (szukaj stopni), 3) rozmiar i rozdzielczość ekranu, 4) wsparcie karty SD (max pojemność), 5) funkcje dodatkowe (G-sensor, tryb parkingowy, WDR). Wygeneruj intro: CO TO za kamera (rozdzielczość + kąt) i DO CZEGO (bezpieczeństwo, dokumentacja). Features: każda cecha to KONKRET z liczb (np. 'Obiektyw 170°' NIE 'szeroki kąt'). ZAKAZ ogólników bez liczb. Jeśli czegoś nie ma w cechach, pomiń."

Wygeneruj JESZCZE LEPSZY, bardziej szczegółowy prompt dla TEGO produktu:"""

        response = requests.post(
            get_gemini_api_url(gemini_key),
            json={
                'contents': [{'parts': [{'text': meta_prompt}]}],
                'generationConfig': {
                    'temperature': 0.7,  # Trochę wyższa dla kreatywności w tworzeniu promptu
                    'maxOutputTokens': 2000
                }
            },
            timeout=90
        )
        
        if response.status_code == 200:
            data = response.json()
            text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            
            if text:
                # Wyczyść markdown jeśli jest
                text = text.strip()
                if text.startswith('```'):
                    text = re.sub(r'^```json?\s*', '', text)
                    text = re.sub(r'\s*```$', '', text)
                
                import json
                result = json.loads(text)
                generated_prompt = result.get('prompt', '')
                
                if generated_prompt:
                    print(f"[Meta-Prompt] Wygenerowano dedykowany prompt ({len(generated_prompt)} znaków)")
                    return generated_prompt
        
        return None
        
    except Exception as e:
        print(f"[Meta-Prompt] Błąd generowania: {e}")
        return None


def generate_product_prompt(nazwa, bullet_points, kategoria='inne'):
    """
    META-PROMPT: Generuje dedykowany prompt dla konkretnego produktu.
    Analizuje nazwę i bullet points, żeby stworzyć optymalny prompt dla Gemini.
    
    WERSJA 2.0: ROZBUDOWANE OPISY (jak na profesjonalnych ofertach Allegro)
    
    Args:
        nazwa: nazwa produktu
        bullet_points: lista cech z Amazona
        kategoria: kategoria produktu
        
    Returns:
        str: Spersonalizowany prompt do generowania opisu
    """
    nazwa_lower = nazwa.lower()
    bp_text = ' '.join(bullet_points).lower() if bullet_points else ''
    
    # Wykryj typ produktu
    product_types = []
    
    if any(x in nazwa_lower or x in bp_text for x in ['kamera', 'dashcam', 'dash cam', 'wideorejestr']):
        product_types.append('dashcam')
    if any(x in nazwa_lower or x in bp_text for x in ['ładowark', 'charger', 'zasilacz', 'power adapter']):
        product_types.append('charger')
    if any(x in nazwa_lower or x in bp_text for x in ['kabel', 'cable', 'przewód']):
        product_types.append('cable')
    if any(x in nazwa_lower or x in bp_text for x in ['słuchawk', 'earphone', 'headphone', 'earbud']):
        product_types.append('audio')
    if any(x in nazwa_lower or x in bp_text for x in ['powerbank', 'power bank', 'bateria zewnętrzna']):
        product_types.append('powerbank')
    if any(x in nazwa_lower or x in bp_text for x in ['uchwyt', 'holder', 'mount']):
        product_types.append('holder')
    if any(x in nazwa_lower or x in bp_text for x in ['obudowa', 'case', 'etui', 'pokrowiec']):
        product_types.append('case')
    if any(x in nazwa_lower or x in bp_text for x in ['tło', 'backdrop', 'zasłona', 'banner', 'dekoracja']):
        product_types.append('decoration')
    if any(x in nazwa_lower or x in bp_text for x in ['lampka', 'light', 'led', 'oświetlenie']):
        product_types.append('lighting')
    if any(x in nazwa_lower or x in bp_text for x in ['bieżnia', 'treadmill', 'rowerek', 'bike', 'orbitrek']):
        product_types.append('fitness')
    if any(x in nazwa_lower or x in bp_text for x in ['ramka', 'frame', 'photo frame', 'digital frame']):
        product_types.append('frame')
    
    # Wykryj kluczowe parametry
    key_params = []
    
    # Rozdzielczość
    if '4k' in bp_text or '2160' in bp_text:
        key_params.append('4K Ultra HD')
    elif '1080' in bp_text or 'full hd' in bp_text:
        key_params.append('Full HD 1080p')
    
    # Moc ładowania
    if any(x in bp_text for x in ['20w', '30w', '45w', '65w', '100w']):
        match = re.search(r'(\d+)w', bp_text)
        if match:
            key_params.append(f'{match.group(1)}W moc ładowania')
    
    # Szybkie ładowanie
    if any(x in bp_text for x in ['fast charg', 'quick charg', 'szybkie ładow', 'pd ', 'qc']):
        key_params.append('szybkie ładowanie')
    
    # Bezprzewodowe
    if 'wireless' in bp_text or 'bezprzewod' in bp_text:
        key_params.append('bezprzewodowe')
    
    # Bluetooth
    if 'bluetooth' in bp_text:
        key_params.append('Bluetooth')
    
    # WiFi
    if 'wifi' in bp_text or 'wi-fi' in bp_text:
        key_params.append('WiFi')
    
    # Wodoodporność
    if any(x in bp_text for x in ['waterproof', 'water resist', 'wodoodporn', 'ip67', 'ip68']):
        key_params.append('wodoodporny')
    
    # Materiał
    if 'silicon' in bp_text or 'silikon' in bp_text:
        key_params.append('silikon')
    elif 'tpu' in bp_text:
        key_params.append('TPU')
    elif 'metal' in bp_text or 'alumin' in bp_text:
        key_params.append('metal')
    
    # Generuj prompt
    prompt = f"""ZADANIE: Stwórz PROFESJONALNY, ROZBUDOWANY opis produktu dla Allegro (wzorowany na najlepszych ofertach).

PRODUKT: {nazwa}
KATEGORIA: {kategoria}"""
    
    if product_types:
        prompt += f"\nTYP: {', '.join(product_types)}"
    
    if key_params:
        prompt += f"\nKLUCZOWE PARAMETRY: {', '.join(key_params)}"
    
    prompt += f"""

CECHY Z AMAZONA (jedyne źródło danych — NIE wymyślaj!):
{chr(10).join([f'- {bp}' for bp in bullet_points])}

=== WYMAGANIA ===

1. INTRO: 4-5 zdań (min 250 znaków)
   - Co to jest + główne parametry z cech
   - Do czego służy w praktyce
   - Dla kogo jest przeznaczony

2. FEATURES: 5-7 sekcji, każda 2-3 zdania (min 150 znaków)
   - Zdanie 1: Konkretna cecha z parametrem
   - Zdanie 2: Jak to działa / co daje użytkownikowi
   - Zdanie 3: Kontekst praktyczny (opcjonalne)

3. SPECS: Parametry techniczne wyciągnięte z bullet points

=== INSTRUKCJE SPECYFICZNE DLA PRODUKTU ===
"""
    
    # Dodaj specyficzne instrukcje dla typu produktu
    if 'dashcam' in product_types:
        prompt += """
TO JEST KAMERA SAMOCHODOWA - pisz ROZBUDOWANY opis:

INTRO (4-6 zdań):
- CO TO: Profesjonalna kamera samochodowa / wideorejestrator
- DO CZEGO: Dokumentacja zdarzeń drogowych, ochrona przed oszustami
- PARAMETRY: Rozdzielczość, kąt widzenia, tryb nocny (z cech!)
- DLA KOGO: Kierowcy dbający o bezpieczeństwo

SEKCJE (po 3-4 zdania każda):
✓ Jakość nagrania: Opisz rozdzielczość i FPS. Wyjaśnij co to daje w praktyce. Podaj konkretne liczby z cech.
✓ Kąt widzenia: Podaj stopnie. Wyjaśnij ile pasów ruchu obejmuje. Dlaczego to ważne.
✓ Tryb nocny: Opisz WDR/HDR. Jak radzi sobie po zmroku. Konkretne parametry.
✓ G-sensor: Co to jest. Jak działa wykrywanie kolizji. Automatyczne zabezpieczenie.
✓ Tryb parkingowy: Jak monitoruje auto. Detekcja ruchu. Bezpieczeństwo na parkingu.
✓ Karta pamięci: Obsługa SD. Zapis w pętli. Jak długo nagrywa na ile GB.
✓ Montaż i zasilanie: Jak się montuje. Zasilanie z zapalniczki. Dyskretna konstrukcja.

✗ NIE pisz: "zapewnia bezpieczeństwo", "spokój rodziny"
✓ PISZ: Konkretne parametry i jak działają w praktyce
"""
    elif 'fitness' in product_types:
        prompt += """
TO JEST SPRZĘT FITNESS (bieżnia/rowerek/orbitrek) - pisz ROZBUDOWANY opis:

INTRO (4-6 zdań):
- CO TO: Typ sprzętu (bieżnia/rowerek), model
- DO CZEGO: Trening cardio w domu, spalanie kalorii, kondycja
- PARAMETRY: Moc silnika, prędkość max, powierzchnia, waga użytkownika (z cech!)
- DLA KOGO: Początkujący/zaawansowani, aktywni, dbający o formę

SEKCJE (po 3-4 zdania każda):
✓ Silnik i prędkość: Moc w KM. Prędkość max km/h. Jak to przekłada się na trening.
✓ Powierzchnia biegu: Wymiary cm. Jak dużo miejsca. Komfort podczas biegu.
✓ Programy treningowe: Ile programów. Jakie typy (interwały, spalanie). Personalizacja.
✓ Wyświetlacz: Co pokazuje (dystans, kalorie, puls). Jak odczytywać dane.
✓ Pochylenie/Opór: Ile poziomów. Jak wpływa na intensywność. Regulacja.
✓ Bezpieczeństwo: Klucz bezpieczeństwa. Maksymalna waga użytkownika. Stabilność.
✓ Składanie i przechowywanie: Czy się składa. Wymiary po złożeniu. Koła transportowe.

✗ NIE pisz: "idealna do domu", "zmień swoje życie"
✓ PISZ: Konkretne parametry, wymiary, możliwości treningu
"""
    elif 'frame' in product_types:
        prompt += """
TO JEST RAMKA CYFROWA - pisz ROZBUDOWANY opis:

INTRO (4-6 zdań):
- CO TO: Ramka cyfrowa / digital photo frame
- DO CZEGO: Wyświetlanie zdjęć, wideo, łączność z rodziną
- PARAMETRY: Rozmiar ekranu, rozdzielczość, WiFi, aplikacja (z cech!)
- DLA KOGO: Rodzina, seniorzy, prezent, wspomnienia

SEKCJE (po 3-4 zdania każda):
✓ Ekran: Przekątna cali. Rozdzielczość pikseli. Jasność. Jakość obrazu.
✓ WiFi i aplikacja: Jak wysyłać zdjęcia. Z telefonu przez app. Zdalne zarządzanie.
✓ Udostępnianie rodzinne: Ilu użytkowników. Jak dodawać zdjęcia. Synchronizacja.
✓ Pamięć: Pamięć wbudowana GB. Karta SD. Ile zdjęć pomieści.
✓ Funkcje dodatkowe: Kalendarz. Zegar. Pogoda. Powiadomienia.
✓ Montaż: Stojak. Zawieszenie na ścianie. Orientacja pionowa/pozioma.
✓ Sterowanie: Panel dotykowy/przyciski. Pilot. Aplikacja mobilna.

✗ NIE pisz: "zachwyci bliskich", "wspaniały prezent"
✓ PISZ: Jak działa, parametry techniczne, możliwości
"""
    elif 'charger' in product_types:
        prompt += """
TO JEST ŁADOWARKA - pisz ROZBUDOWANY opis:

SEKCJE (po 3-4 zdania każda):
✓ Moc i prędkość: Moc W, prąd A. Jak szybko ładuje (procenty w minutach). Standardy PD/QC.
✓ Porty: Ile portów, jakie typy (USB-A/C). Jednoczesne ładowanie. Rozdzielenie mocy.
✓ Kompatybilność: Jakie urządzenia (iPhone, Samsung, etc.). Wersje modeli.
✓ Bezpieczeństwo: Zabezpieczenia (przeciążenie, przegrzanie). Certyfikaty.
✓ Konstrukcja: Rozmiary. Materiał. Wytrzymałość. Przenośność.
"""
    else:
        prompt += """
OGÓLNY PRODUKT - pisz ROZBUDOWANY opis:

SEKCJE (po 3-4 zdania każda):
✓ Parametry główne: Wymiary, waga, materiał. Konkretne liczby z cech.
✓ Funkcje: Co robi. Jak działa. Parametry techniczne.
✓ Kompatybilność: Z czym działa. Jakie urządzenia.
✓ Konstrukcja: Z czego wykonane. Wytrzymałość. Jakość.
✓ Zawartość zestawu: Co jest w pudełku. Akcesoria.
"""
    
    prompt += """

=== ZASADY ===

1. STYL:
   ✓ Pisz jak ekspert — konkretnie, z parametrami
   ✓ Każde zdanie niesie nową informację
   ✓ Używaj danych z bullet points (liczby, wartości, jednostki)
   ✓ Polski język, naturalny ton

2. ZAKAZ:
   ✗ Puste frazesy: "wysoka jakość", "solidna konstrukcja", "premium"
   ✗ Wymyślanie parametrów których NIE MA w bullet points
   ✗ "idealny", "perfekcyjny", "must-have", "bestseller"
   ✗ Lanie wody — każde zdanie musi mieć konkret

=== FORMAT ODPOWIEDZI ===

```json
{
    "intro": "DŁUGIE wprowadzenie 4-6 PEŁNYCH ZDAŃ opisujące co to jest, do czego służy, kluczowe parametry i dla kogo. MINIMUM 300 znaków!",
    "features": [
        {
            "icon": "<span class=material-symbols-outlined>inventory_2</span>",
            "title": "Konkretny tytuł",
            "text": "DŁUGI opis 3-4 PEŁNYCH ZDAŃ. Pierwsze zdanie opisuje cechę. Drugie wyjaśnia jak to działa w praktyce. Trzecie podaje konkretne parametry. Czwarte dodaje kontekst. MINIMUM 200 znaków!"
        },
        {
            "icon": "<span class=material-symbols-outlined>bolt</span>",
            "title": "Konkretny tytuł",
            "text": "DŁUGI opis 3-4 zdań z konkretnymi parametrami i wyjaśnieniami..."
        },
        // ... 5-7 sekcji total
    ],
    "specs": [
        {"label": "Parametr", "value": "z cech"},
        {"label": "Materiał", "value": "z cech"}
    ]
}
```

PRZYKŁAD DOBREGO OPISU:
"Przekątna ekranu 10,1 cala zapewnia wygodne oglądanie zdjęć z dowolnej odległości, idealnie dopasowując się do wymiarów standardowej ramki na biurku lub komodzie. Rozdzielczość 1280x800 pikseli gwarantuje wyraźne, kolorowe obrazy bez utraty jakości, nawet przy powiększeniach. Matowa powłoka eliminuje odblaski, dzięki czemu zdjęcia są doskonale widoczne niezależnie od kąta patrzenia i oświetlenia pomieszczenia."

ODPOWIEDZ TYLKO W FORMACIE JSON (bez markdown).
"""
    
    return prompt
    """
    META-PROMPT: Generuje dedykowany prompt dla konkretnego produktu.
    Analizuje nazwę i bullet points, żeby stworzyć optymalny prompt dla Gemini.
    
    Args:
        nazwa: nazwa produktu
        bullet_points: lista cech z Amazona
        kategoria: kategoria produktu
        
    Returns:
        str: Spersonalizowany prompt do generowania opisu
    """
    nazwa_lower = nazwa.lower()
    bp_text = ' '.join(bullet_points).lower() if bullet_points else ''
    
    # Wykryj typ produktu
    product_types = []
    
    if any(x in nazwa_lower or x in bp_text for x in ['kamera', 'dashcam', 'dash cam', 'wideorejestr']):
        product_types.append('dashcam')
    if any(x in nazwa_lower or x in bp_text for x in ['ładowark', 'charger', 'zasilacz', 'power adapter']):
        product_types.append('charger')
    if any(x in nazwa_lower or x in bp_text for x in ['kabel', 'cable', 'przewód']):
        product_types.append('cable')
    if any(x in nazwa_lower or x in bp_text for x in ['słuchawk', 'earphone', 'headphone', 'earbud']):
        product_types.append('audio')
    if any(x in nazwa_lower or x in bp_text for x in ['powerbank', 'power bank', 'bateria zewnętrzna']):
        product_types.append('powerbank')
    if any(x in nazwa_lower or x in bp_text for x in ['uchwyt', 'holder', 'mount']):
        product_types.append('holder')
    if any(x in nazwa_lower or x in bp_text for x in ['obudowa', 'case', 'etui', 'pokrowiec']):
        product_types.append('case')
    if any(x in nazwa_lower or x in bp_text for x in ['tło', 'backdrop', 'zasłona', 'banner', 'dekoracja']):
        product_types.append('decoration')
    if any(x in nazwa_lower or x in bp_text for x in ['lampka', 'light', 'led', 'oświetlenie']):
        product_types.append('lighting')
    
    # Wykryj kluczowe parametry
    key_params = []
    
    # Rozdzielczość
    if '4k' in bp_text or '2160' in bp_text:
        key_params.append('4K Ultra HD')
    elif '1080' in bp_text or 'full hd' in bp_text:
        key_params.append('Full HD 1080p')
    
    # Moc ładowania
    if any(x in bp_text for x in ['20w', '30w', '45w', '65w', '100w']):
        match = re.search(r'(\d+)w', bp_text)
        if match:
            key_params.append(f'{match.group(1)}W moc ładowania')
    
    # Szybkie ładowanie
    if any(x in bp_text for x in ['fast charg', 'quick charg', 'szybkie ładow', 'pd ', 'qc']):
        key_params.append('szybkie ładowanie')
    
    # Bezprzewodowe
    if 'wireless' in bp_text or 'bezprzewod' in bp_text:
        key_params.append('bezprzewodowe')
    
    # Bluetooth
    if 'bluetooth' in bp_text:
        key_params.append('Bluetooth')
    
    # Wodoodporność
    if any(x in bp_text for x in ['waterproof', 'water resist', 'wodoodporn', 'ip67', 'ip68']):
        key_params.append('wodoodporny')
    
    # Materiał
    if 'silicon' in bp_text or 'silikon' in bp_text:
        key_params.append('silikon')
    elif 'tpu' in bp_text:
        key_params.append('TPU')
    elif 'metal' in bp_text or 'alumin' in bp_text:
        key_params.append('metal')
    
    # Generuj prompt
    prompt = f"""ZADANIE: Stwórz profesjonalny opis produktu dla Allegro.

PRODUKT: {nazwa}
KATEGORIA: {kategoria}"""
    
    if product_types:
        prompt += f"\nTYP: {', '.join(product_types)}"
    
    if key_params:
        prompt += f"\nKLUCZOWE PARAMETRY: {', '.join(key_params)}"
    
    prompt += f"""

CECHY Z AMAZONA:
{chr(10).join([f'- {bp}' for bp in bullet_points])}

=== INSTRUKCJE SPECYFICZNE DLA PRODUKTU ===
"""
    
    # Dodaj specyficzne instrukcje dla typu produktu
    if 'dashcam' in product_types:
        prompt += """
TO JEST KAMERA SAMOCHODOWA - skup się na:
✓ Jakości nagrania (rozdzielczość, FPS)
✓ Kącie widzenia obiektywu
✓ Funkcjach nocnych (WDR, HDR)
✓ Czujnikach (G-sensor, detekcja ruchu)
✓ Trybie parkingowym
✓ Obsłudze kart SD
✗ NIE pisz o "bezpieczeństwie rodziny" - TYLKO FAKTY TECHNICZNE
"""
    elif 'charger' in product_types:
        prompt += """
TO JEST ŁADOWARKA - skup się na:
✓ Mocy ładowania (W) i prądzie (A)
✓ Kompatybilności z urządzeniami
✓ Standardach szybkiego ładowania (PD, QC, AFC)
✓ Liczbie portów
✓ Zabezpieczeniach (przeciążenie, przegrzanie)
✗ NIE pisz o "niezawodności" - TYLKO SPECYFIKACJA
"""
    elif 'cable' in product_types:
        prompt += """
TO JEST KABEL - skup się na:
✓ Długości kabla
✓ Typie złącz (USB-C, Lightning, Micro-USB)
✓ Prędkości ładowania i transferu
✓ Wytrzymałości (oplecenie nylonowe, etc.)
✓ Kompatybilności
✗ NIE pisz o "wygodzie" - TYLKO PARAMETRY
"""
    elif 'audio' in product_types:
        prompt += """
TO SĄ SŁUCHAWKI/AUDIO - skup się na:
✓ Typie (douszne, nauszne, przewodowe, BT)
✓ Czasie pracy baterii
✓ Jakości dźwięku (ANC, kodeki)
✓ Mikrofonie i rozmowach
✓ Wodoodporności (IPX)
✗ NIE pisz o "czystości dźwięku" bez faktów
"""
    elif 'decoration' in product_types:
        prompt += """
TO JEST DEKORACJA - skup się na:
✓ Wymiarach (dokładne cm/cal)
✓ Materiale (cekinki, tkanina, plastik)
✓ Zawartości zestawu
✓ Zastosowaniu (urodziny, ślub, etc.)
✓ Kolorach i wzorach
✗ NIE pisz o "jakości wykonania" - TYLKO FAKTY
"""
    else:
        prompt += """
OGÓLNY PRODUKT - skup się na:
✓ Konkretnych wymiarach i parametrach
✓ Materiale i konstrukcji
✓ Zawartości zestawu
✓ Kompatybilności
✓ Specyfikacji technicznej
"""
    
    prompt += """

=== ZASADY PISANIA (KRYTYCZNE) ===

1. DŁUGOŚĆ OPISU - TO JEST NAJWAŻNIEJSZE:
   ✓ INTRO: 4-6 rozbudowanych akapitów (minimum 800 znaków)
   ✓ FEATURES: Minimum 8-12 sekcji (każda po 150-250 znaków)
   ✓ SPECS: Wszystkie dostępne parametry z cech
   ✓ CAŁOŚĆ: Minimum 3000-5000 znaków tekstu
   
   PRZYKŁAD DOBREGO INTRO (długość!):
   "To profesjonalna kamera samochodowa zapewniająca nagrywanie w rozdzielczości Full HD 1080p przy 30 klatkach na sekundę. 
   
   Urządzenie wyposażono w szerokokątny obiektyw 170 stopni, który obejmuje 3 pasy ruchu, eliminując martwe pole widzenia. Dzięki technologii WDR (Wide Dynamic Range) kamera radzi sobie z trudnymi warunkami oświetleniowych, w tym z jazdą pod słońce lub w tunelach.
   
   Wbudowany czujnik G-sensor automatycznie wykrywa nagłe przyspieszenia i hamowania, zabezpieczając kluczowe nagrania przed nadpisaniem. Tryb parkingowy z detekcją ruchu pozwala na monitorowanie pojazdu nawet po wyłączeniu silnika.
   
   Kamera obsługuje karty microSD do 128GB z automatycznym nadpisywaniem najstarszych plików. Prosty montaż na szybie za pomocą przyssawki oraz zasilanie z gniazda 12V sprawiają, że instalacja nie wymaga specjalistycznej wiedzy.
   
   W zestawie znajduje się: kamera, uchwyt montażowy, ładowarka samochodowa, kabel USB oraz instrukcja obsługi w języku polskim."

2. BEZWZGLĘDNY ZAKAZ MARKETINGU:
   ✗ "wysoka jakość", "premium", "solidna konstrukcja"
   ✗ "tysiące zadowolonych klientów", "bestseller"
   ✗ "idealny", "perfekcyjny", "must-have"
   ✗ "bezproblemowy", "niezawodny"
   ✗ "satysfakcja gwarantowana"
   
3. TYLKO FAKTY Z CECH:
   ✓ Liczby, wymiary, parametry techniczne
   ✓ Materiały (jeśli podane w cechach)
   ✓ Funkcje (jeśli opisane w cechach)
   ✓ Kompatybilność (jeśli wymieniona)

4. STRUKTURA ODPOWIEDZI (JSON):
```json
{
    "intro": "BARDZO DŁUGIE wprowadzenie (4-6 akapitów, minimum 800 znaków). Każdy akapit to 3-5 zdań z konkretnymi faktami i parametrami z cech produktu. Opisz dokładnie CO TO jest, JAK działa, JAKIE MA funkcje, DO CZEGO służy, CO zawiera zestaw.",
    "features": [
        {"icon": "<span class=material-symbols-outlined>inventory_2</span>", "title": "KONKRETNY TYTUŁ", "text": "Długi opis (150-250 znaków) z konkretnymi faktami"},
        {"icon": "<span class=material-symbols-outlined>bolt</span>", "title": "KONKRETNY TYTUŁ", "text": "Długi opis (150-250 znaków) z konkretnymi faktami"},
        {"icon": "<span class=material-symbols-outlined>adjust</span>", "title": "KONKRETNY TYTUŁ", "text": "Długi opis (150-250 znaków) z konkretnymi faktami"},
        {"icon": "<span class=material-symbols-outlined>check_circle</span>", "title": "KONKRETNY TYTUŁ", "text": "Długi opis (150-250 znaków) z konkretnymi faktami"},
        {"icon": "<span class=material-symbols-outlined>build</span>", "title": "KONKRETNY TYTUŁ", "text": "Długi opis (150-250 znaków) z konkretnymi faktami"},
        {"icon": "<span class=material-symbols-outlined>lightbulb</span>", "title": "KONKRETNY TYTUŁ", "text": "Długi opis (150-250 znaków) z konkretnymi faktami"},
        {"icon": "<span class=material-symbols-outlined>battery_full</span>", "title": "KONKRETNY TYTUŁ", "text": "Długi opis (150-250 znaków) z konkretnymi faktami"},
        {"icon": "<span class=material-symbols-outlined>smartphone</span>", "title": "KONKRETNY TYTUŁ", "text": "Długi opis (150-250 znaków) z konkretnymi faktami"}
    ],
    "specs": [
        {"label": "Parametr", "value": "TYLKO JEŚLI W CECHACH"},
        {"label": "Materiał", "value": "TYLKO JEŚLI W CECHACH"}
    ]
}
```

PRZYKŁADY DŁUGOŚCI:
✓ DOBRZE (feature): "Obiektyw 170° obejmuje 3 pasy ruchu, eliminując martwe pole widzenia po bokach pojazdu. Szeroki kąt widzenia pozwala na rejestrację zdarzeń z sąsiednich pasów ruchu oraz chodników, co jest kluczowe podczas dokumentowania kolizji drogowych lub incydentów parkingowych."

✗ ŹLE (za krótkie): "Szeroki kąt zapewnia doskonałą widoczność"

✓ DOBRZE (feature): "Moc 20W z obsługą Power Delivery 3.0 pozwala na naładowanie iPhone'a 12-15 do 50% w zaledwie 30 minut. Inteligentny chip rozpoznaje podłączone urządzenie i automatycznie dostosowuje parametry ładowania, zapewniając maksymalną prędkość bez ryzyka uszkodzenia baterii."

✗ ŹLE (za krótkie): "Szybka ładowarka idealna dla Twojego telefonu"

WAŻNE:
- Intro MUSI mieć minimum 800 znaków (4-6 akapitów)
- Każdy feature MUSI mieć 150-250 znaków
- MINIMUM 8 features (jeśli są cechy)
- Całość MINIMUM 3000 znaków
- Wymiary typu 10x2.75, 8.5x2, M365 itp. to ROZMIARY/MODELE, NIE ilosci sztuk!
- Nie wymyslaj ilosci sztuk w zestawie jesli nie ma tego wprost w nazwie
- Tytul produktu NIE idzie do opisu - opis zaczyna sie od tekstu opisowego
- ABSOLUTNIE ZAKAZANE FRAZY (NIE UŻYWAJ ICH!):
  ✗ "Wysoka jakość wykonania"
  ✗ "Kompletny zestaw"
  ✗ "Praktyczne zastosowanie"
  ✗ "Przemyślany design łączy funkcjonalność z estetyką"
  ✗ "Satysfakcja z zakupu"
  ✗ "Bezproblemowe użytkowanie"
  ✗ "Spełniający oczekiwania nawet najbardziej wymagających użytkowników"
  ✗ "Solidne wykonanie i staranny dobór materiałów"
  Zamiast tego opisuj KONKRETNE cechy produktu!

Odpowiedz TYLKO w formacie JSON (bez markdown).
"""
    
    return prompt


def generuj_opis_html_pro(nazwa, zdjecia_urls, kategoria='inne', bullet_points=None, gemini_key=None, asin=None, kod_magazynowy=None):
    """
    Generuje profesjonalny opis HTML dla Allegro.
    ZMIANA: Używa bullet_points z Amazona zamiast wymyślać ogólniki.
    
    WAŻNE: Allegro description sections akceptuje tylko tagi:
    h1, h2, h3, p, ul, ol, li

    NIE WOLNO używać: b, strong, i, em, u, div, span, img, table, br, style=
    
    Args:
        nazwa: nazwa produktu
        zdjecia_urls: lista URL-i zdjęć (używane tylko do galerii oferty, NIE w opisie)
        kategoria: kategoria produktu
        bullet_points: lista cech z Amazona
        gemini_key: klucz API Gemini (opcjonalnie)
        asin: kod ASIN produktu (opcjonalnie)
    
    Returns:
        Tuple (html_opis, plain_text_opis)
    """
    from .database import get_config
    
    api_key = gemini_key or get_config('gemini_api_key', '')
    
    # Generuj teksty AI lub użyj szablonu
    intro_text = ""
    features = []
    specs = []
    
    if api_key:
        try:
            if bullet_points and len(bullet_points) > 0:
                # Z bullet points - użyj dedykowanego meta-promptu
                print(f"[Gemini] Generowanie opisu z bullet points ({len(bullet_points)} cech)...")
                prompt = generate_product_prompt(nazwa, bullet_points, kategoria)
            else:
                # Bez bullet points - generuj na podstawie nazwy
                print(f"[Gemini] Generowanie opisu z samej nazwy (brak bullet points)...")
                prompt = f"""ZADANIE: Stwórz PROFESJONALNY, ROZBUDOWANY opis produktu dla Allegro.

PRODUKT: {nazwa}
{f'KATEGORIA: {kategoria}' if kategoria and kategoria != 'inne' else ''}

=== WYMAGANIA DŁUGOŚCI (KRYTYCZNE!) ===

1. INTRO: 4-6 PEŁNYCH ZDAŃ (minimum 400 znaków)
   - Zdanie 1: CO TO jest za produkt (konkretnie, po polsku)
   - Zdanie 2-3: DO CZEGO służy, jakie problemy rozwiązuje
   - Zdanie 4-5: Kluczowe cechy i parametry produktu
   - Zdanie 6: Dla kogo jest ten produkt

2. KAŻDA SEKCJA FEATURES: 3-4 PEŁNE ZDANIA (minimum 150 znaków każda)
   - Zdanie 1: Główna cecha/funkcja
   - Zdanie 2: Jak to działa w praktyce
   - Zdanie 3-4: Konkretne parametry i zastosowania

3. GENERUJ 5-7 SEKCJI (nie mniej!)

Odpowiedz w JSON:
{{
  "intro": "Rozbudowane wprowadzenie (4-6 zdań, minimum 400 znaków)",
  "features": [
    {{"icon": "pasujące emoji", "title": "Konkretny tytuł cechy (2-4 słowa)", "text": "Rozbudowany opis cechy (3-4 zdania, minimum 150 znaków)"}},
    ... (5-7 sekcji)
  ],
  "specs": []
}}

=== ZAKAZANE FRAZY (NIE UŻYWAJ!) ===
✗ "Wysoka jakość wykonania"
✗ "Kompletny zestaw"
✗ "Praktyczne zastosowanie"
✗ "Przemyślany design łączy funkcjonalność z estetyką"
✗ "Satysfakcja z zakupu"
✗ "Bezproblemowe użytkowanie"
✗ "Solidne wykonanie i staranny dobór materiałów"
✗ "Spełniający oczekiwania nawet najbardziej wymagających"

=== ZASADY ===
- Pisz po polsku
- Bazuj na tym co WIESZ o produkcie z nazwy - nie wymyślaj dokładnych parametrów których nie znasz
- Opisuj KONKRETNE cechy: materiał, rozmiar, funkcje, zastosowania, kompatybilność
- Pisz jak profesjonalny sprzedawca który ZNA ten produkt
- Każda cecha musi być INNA - nie powtarzaj informacji

PRZYKŁAD DOBREJ DŁUGOŚCI FEATURE:
✓ "Obiektyw 170° obejmuje 3 pasy ruchu, eliminując martwe pole widzenia po bokach pojazdu. Szeroki kąt widzenia pozwala na rejestrację zdarzeń z sąsiednich pasów ruchu oraz chodników, co jest kluczowe podczas dokumentowania kolizji drogowych."

✗ ŹLE (za krótkie): "Szeroki kąt zapewnia doskonałą widoczność"

Odpowiedz TYLKO w formacie JSON (bez markdown)."""

            response = requests.post(
                get_gemini_api_url(api_key),
                json={
                    'contents': [{'parts': [{'text': prompt}]}],
                    'generationConfig': {
                        'temperature': 0.4,
                        'maxOutputTokens': 8000
                    }
                },
                timeout=120
            )

            if response.status_code == 200:
                data = response.json()
                text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                if text:
                    text = text.strip()
                    if text.startswith('```'):
                        text = re.sub(r'^```json?\s*', '', text)
                        text = re.sub(r'\s*```$', '', text)

                    import json
                    ai_data = json.loads(text)
                    intro_text = ai_data.get('intro', '')
                    features = ai_data.get('features', [])
                    specs = ai_data.get('specs', [])
                    print(f"[Gemini] [OK] Wygenerowano: intro={len(intro_text)} chars, {len(features)} features")
            else:
                print(f"[Gemini HTML] [ERR] API error: {response.status_code}: {response.text[:200]}")
        except Exception as e:
            print(f"[Gemini HTML] [WARN] Błąd: {e}")
    
    # Fallback - szablony dla różnych kategorii
    # Sprawdź czy mamy intro I features - oba są potrzebne
    if not intro_text or not features:
        if 'pokrowc' in nazwa.lower() or 'seat' in nazwa.lower() or 'cover' in nazwa.lower():
            intro_text = "Chcesz szybko odświeżyć wnętrze swojego samochodu i zabezpieczyć oryginalną tapicerkę przed zużyciem? Prezentowany zestaw pokrowców to idealne rozwiązanie łączące estetykę ze skuteczną ochroną. Dzięki uniwersalnemu krojowi i wytrzymałym materiałom, Twoje fotele zyskają nowoczesny, sportowy wygląd, a Ty komfort podróżowania bez obaw o zabrudzenia."
            features = [
                {"icon": "<span class=material-symbols-outlined>inventory_2</span>", "title": "Kompletny Zestaw 5-Miejscowy", "text": "Otrzymujesz pełen pakiet ochronny: pokrowce na dwa przednie fotele, pełne pokrycie tylnej kanapy (siedzisko i oparcie) oraz komplet 5 zagłówków. To spójna stylizacja całego wnętrza pojazdu."},
                {"icon": "<span class=material-symbols-outlined>shield</span>", "title": "Ochrona i Trwałość", "text": "Wykonane z wytrzymałej tkaniny poliestrowej, która jest odporna na przecieranie i codzienne użytkowanie. Materiał skutecznie chroni oryginalną tapicerkę przed sierścią zwierząt, okruchami, plamami i blaknięciem od słońca."},
                {"icon": "<span class=material-symbols-outlined>adjust</span>", "title": "Uniwersalne Dopasowanie", "text": "Elastyczny materiał sprawia, że pokrowce dopasowują się do kształtu większości standardowych foteli kubełkowych w autach osobowych. Klasyczny krój z osobnymi zagłówkami zapewnia estetyczny wygląd bez efektu 'worka'."},
                {"icon": "<span class=material-symbols-outlined>directions_car</span>", "title": "Komfort Podróży", "text": "Tkanina posiada właściwości oddychające, co zwiększa komfort jazdy zarówno latem, jak i zimą. Środek pokrowca jest przyjemny w dotyku, a piankowe podłoże zapobiega przesuwaniu się materiału po fotelu."},
                {"icon": "<span class=material-symbols-outlined>build</span>", "title": "Łatwy i Szybki Montaż", "text": "System gumek i haczyków montażowych pozwala na sprawną instalację bez konieczności demontażu foteli. W razie zabrudzenia pokrowce można łatwo zdjąć i uprać w pralce (program delikatny 30°C)."},
            ]
            specs = [
                {"label": "Marka", "value": "Uniwersalna / OEM"},
                {"label": "Typ", "value": "Zestaw pełny (Przód + Tył)"},
                {"label": "Materiał", "value": "Poliester techniczny (wytrzymała tkanina tapicerska)"},
                {"label": "Kolor", "value": "Czarny z szarymi wstawkami"},
                {"label": "Zawartość zestawu", "value": "2x pokrowiec na przedni fotel, 1x pokrowiec na tylne siedzisko, 1x pokrowiec na tylne oparcie, 5x pokrowiec na zagłówek, zestaw haczyków montażowych"},
                {"label": "Pranie", "value": "Tak, możliwość prania ręcznego lub w niskiej temperaturze"},
            ]
        elif 'dywanik' in nazwa.lower() and 'glamour' in nazwa.lower():
            # Szablon TYLKO dla dywaników glamour z kryształkami
            intro_text = "Chcesz nadać wnętrzu swojego samochodu niepowtarzalny blask i charakter? Prezentowany zestaw dywaników to idealne połączenie stylu glamour z funkcjonalną ochroną tapicerki. Dzięki nim każda podróż stanie się bardziej ekskluzywna, a wnętrze Twojego auta zyska zupełnie nowy, luksusowy wygląd."
            features = [
                {"icon": "<span class=material-symbols-outlined>diamond</span>", "title": "Wyjątkowy Design Glamour", "text": "Boczne krawędzie wykończone tysiącami mieniących się kryształków w kolorze czerwonym przyciągają wzrok i nadają wnętrzu luksusowy charakter."},
                {"icon": "<span class=material-symbols-outlined>shield</span>", "title": "Wzmocniona Strefa Kierowcy", "text": "Dywanik kierowcy posiada specjalną, gumowaną nakładkę pod piętę zapobiegającą szybkiemu przecieraniu."},
                {"icon": "<span class=material-symbols-outlined>adjust</span>", "title": "Uniwersalne Dopasowanie", "text": "Zoptymalizowany kształt dywaników sprawia, że pasują do większości modeli samochodów osobowych."},
            ]
            specs = []
        
        elif 'bieżnia' in nazwa.lower() or 'treadmill' in nazwa.lower() or 'walkingpad' in nazwa.lower() or 'mata do chodzenia' in nazwa.lower():
            # Rozbudowany szablon dla bieżni
            nazwa_clean = nazwa.replace('TODO ', '').strip()
            intro_text = f"Przedstawiamy {nazwa_clean} - idealne rozwiązanie do domowego treningu cardio. Ta kompaktowa bieżnia pozwala na efektywne ćwiczenia w zaciszu własnego domu, bez konieczności kosztownych wyjazdów na siłownię. Nowoczesna konstrukcja łączy funkcjonalność z eleganckim designem, a zaawansowane funkcje umożliwiają dostosowanie treningu do indywidualnych potrzeb. Sprawdzone rozwiązanie dla osób dbających o kondycję fizyczną i zdrowy styl życia."
            
            # Inteligentne generowanie sekcji z bullet_points
            features = []
            icons_default = ["🏃", "<span class=material-symbols-outlined>bolt</span>", "<span class=material-symbols-outlined>smartphone</span>", "<span class=material-symbols-outlined>straighten</span>", "<span class=material-symbols-outlined>build</span>", "<span class=material-symbols-outlined>shield</span>", "<span class=material-symbols-outlined>fitness_center</span>"]
            titles_default = ["Trening cardio", "Silnik i moc", "Wyświetlacz", "Wymiary", "Montaż", "Bezpieczeństwo", "Komfort"]
            
            if bullet_points:
                for i, bp in enumerate(bullet_points[:7]):
                    bp_lower = bp.lower()
                    
                    # Wykryj typ informacji i przypisz ikonę
                    if any(x in bp_lower for x in ['speed', 'prędkość', 'km/h', 'mph', 'motor', 'silnik', 'hp', 'kw']):
                        icon, title = "<span class=material-symbols-outlined>bolt</span>", "Silnik i prędkość"
                    elif any(x in bp_lower for x in ['display', 'wyświetlacz', 'lcd', 'led', 'screen', 'ekran']):
                        icon, title = "<span class=material-symbols-outlined>smartphone</span>", "Wyświetlacz i dane"
                    elif any(x in bp_lower for x in ['size', 'wymiar', 'cm', 'mm', 'inch', 'długość', 'szerokość']):
                        icon, title = "<span class=material-symbols-outlined>straighten</span>", "Wymiary i powierzchnia"
                    elif any(x in bp_lower for x in ['fold', 'składan', 'compact', 'storage', 'przechow']):
                        icon, title = "<span class=material-symbols-outlined>build</span>", "Składanie i przechowywanie"
                    elif any(x in bp_lower for x in ['weight', 'waga', 'kg', 'lb', 'max', 'capacity']):
                        icon, title = "<span class=material-symbols-outlined>shield</span>", "Nośność i bezpieczeństwo"
                    elif any(x in bp_lower for x in ['program', 'mode', 'tryb', 'workout']):
                        icon, title = "<span class=material-symbols-outlined>fitness_center</span>", "Programy treningowe"
                    elif any(x in bp_lower for x in ['incline', 'nachylenie', 'slope', 'angle']):
                        icon, title = "<span class=material-symbols-outlined>straighten</span>", "Regulacja nachylenia"
                    elif any(x in bp_lower for x in ['quiet', 'cichy', 'noise', 'silent', 'głośność']):
                        icon, title = "🔇", "Cicha praca"
                    else:
                        icon = icons_default[i % len(icons_default)]
                        title = titles_default[i % len(titles_default)]
                    
                    features.append({"icon": icon, "title": title, "text": bp})
            else:
                features = [
                    {"icon": "🏃", "title": "Domowy trening cardio", "text": "Idealna do ćwiczeń w domu - oszczędza czas i pieniądze na siłownię. Regularne ćwiczenia poprawiają kondycję i pomagają utrzymać zdrową wagę."},
                    {"icon": "<span class=material-symbols-outlined>straighten</span>", "title": "Kompaktowa konstrukcja", "text": "Łatwa do przechowywania dzięki składanej konstrukcji. Można schować pod łóżko, kanapę lub w szafie. Idealna do małych mieszkań."},
                    {"icon": "<span class=material-symbols-outlined>smartphone</span>", "title": "Wyświetlacz", "text": "Panel sterowania z wyświetlaczem pokazującym podstawowe parametry treningu: czas, dystans, prędkość, spalone kalorie."},
                    {"icon": "🔇", "title": "Cicha praca", "text": "Zaprojektowana z myślą o użytkowaniu w mieszkaniu. Cichy silnik nie przeszkadza domownikom ani sąsiadom."},
                    {"icon": "<span class=material-symbols-outlined>shield</span>", "title": "Bezpieczeństwo", "text": "Wyposażona w funkcje bezpieczeństwa: klucz awaryjny, antypoślizgowa powierzchnia, stabilna podstawa."},
                ]
            specs = []
        
        elif 'ładowarka' in nazwa.lower() or 'charger' in nazwa.lower() or 'ładowark' in nazwa.lower() or 'zasilacz' in nazwa.lower():
            # Szablon dla ładowarek
            intro_text = f"{nazwa} - "
            if bullet_points:
                bp_text = ' '.join(bullet_points).lower()
                if 'fast' in bp_text or 'szybk' in bp_text or '20w' in bp_text or '30w' in bp_text:
                    intro_text += "szybka ładowarka zapewniająca ekspresowe uzupełnianie energii Twoich urządzeń. "
                elif 'wireless' in bp_text or 'bezprzewod' in bp_text:
                    intro_text += "bezprzewodowa ładowarka oferująca wygodne ładowanie bez kabli. "
                elif 'car' in bp_text or 'samochod' in bp_text:
                    intro_text += "samochodowa ładowarka idealna do podróży. "
                else:
                    intro_text += "niezawodna ładowarka do codziennego użytku. "
            else:
                intro_text += "praktyczna ładowarka do codziennego użytku. "
            
            intro_text += "Kompatybilna z popularnymi urządzeniami mobilnymi, zapewnia bezpieczne i efektywne ładowanie dzięki zaawansowanym zabezpieczeniom przed przeciążeniem, przegrzaniem i zwarciem."
            
            features = []
            icons = ["<span class=material-symbols-outlined>bolt</span>", "<span class=material-symbols-outlined>power</span>", "<span class=material-symbols-outlined>shield</span>", "<span class=material-symbols-outlined>smartphone</span>", "<span class=material-symbols-outlined>sync</span>"]
            titles = ["Moc i wydajność", "Złącza", "Bezpieczeństwo", "Kompatybilność", "Dodatkowe funkcje"]
            
            if bullet_points:
                for i, bp in enumerate(bullet_points[:5]):
                    features.append({"icon": icons[i] if i < len(icons) else "<span class=material-symbols-outlined>check_circle</span>", "title": titles[i] if i < len(titles) else "Specyfikacja", "text": bp})
            
            specs = []
        
        elif 'kabel' in nazwa.lower() or 'cable' in nazwa.lower() or 'przewód' in nazwa.lower():
            # Szablon dla kabli
            intro_text = f"{nazwa} - wysokiej jakości kabel zapewniający niezawodne połączenie i "
            if bullet_points:
                bp_text = ' '.join(bullet_points).lower()
                if 'fast' in bp_text or 'szybk' in bp_text:
                    intro_text += "szybkie ładowanie oraz transfer danych. "
                else:
                    intro_text += "stabilny transfer danych. "
            else:
                intro_text += "stabilne działanie. "
            
            intro_text += "Wzmocniona konstrukcja i solidne wtyki gwarantują długą żywotność nawet przy intensywnym użytkowaniu."
            
            features = []
            if bullet_points:
                for i, bp in enumerate(bullet_points[:5]):
                    features.append({"icon": "<span class=material-symbols-outlined>power</span>", "title": f"Parametr {i+1}", "text": bp})
            
            specs = []
        elif 'kamera' in nazwa.lower() or 'dashcam' in nazwa.lower() or 'dash cam' in nazwa.lower() or 'wideorejestrator' in nazwa.lower():
            # Szablon dla kamer samochodowych - ROZBUDOWANY
            intro_text = f"{nazwa} - profesjonalna kamera samochodowa zapewniająca pełną dokumentację Twoich podróży. "
            
            # Dodaj więcej szczegółów bazując na bullet points
            if bullet_points:
                bp_text = ' '.join(bullet_points).lower()
                details = []
                
                if '4k' in bp_text or '2160' in bp_text:
                    details.append("nagrywanie w jakości 4K Ultra HD")
                elif '1080' in bp_text or 'full hd' in bp_text:
                    details.append("nagrywanie Full HD 1080p")
                
                if 'wide angle' in bp_text or 'szeroki kąt' in bp_text or '170' in bp_text or '160' in bp_text:
                    details.append("szerokokątny obiektyw obejmujący cały pas ruchu")
                
                if 'night' in bp_text or 'nocn' in bp_text or 'wdr' in bp_text:
                    details.append("tryb nocny dla wyraźnych nagrań po zmroku")
                
                if 'g-sensor' in bp_text or 'czujnik' in bp_text:
                    details.append("automatyczne zabezpieczenie nagrań przy wykryciu uderzenia")
                
                if 'parking' in bp_text:
                    details.append("tryb parkingowy monitorujący auto podczas postoju")
                
                if details:
                    intro_text += "Wyposażona w: " + ", ".join(details) + ". "
            
            intro_text += "Idealne rozwiązanie dla kierowców ceniących bezpieczeństwo, dokumentację zdarzeń drogowych i ochronę przed nieuczciwymi oszustami ubezpieczeniowymi. Łatwy montaż na przedniej szybie, dyskretna konstrukcja nie ograniczająca widoczności."
            
            # Inteligentne przetworzenie bullet points
            features = []
            icons = ["<span class=material-symbols-outlined>videocam</span>", "<span class=material-symbols-outlined>adjust</span>", "<span class=material-symbols-outlined>save</span>", "<span class=material-symbols-outlined>sync</span>", "<span class=material-symbols-outlined>bolt</span>"]
            titles = ["Nagrywanie wideo", "Funkcje", "Pamięć i zapis", "Dodatkowe możliwości", "Zasilanie i montaż"]
            
            if bullet_points and len(bullet_points) > 0:
                for i, bp in enumerate(bullet_points[:5]):
                    # Wyciągnij kluczowe info z bullet point
                    if any(x in bp.lower() for x in ['rozdzielcz', 'resolution', '4k', '1080', '720', 'fps']):
                        title = "Jakość nagrania"
                        icon = "<span class=material-symbols-outlined>videocam</span>"
                    elif any(x in bp.lower() for x in ['ekran', 'screen', 'monitor', 'lcd', 'wyświetlacz']):
                        title = "Wyświetlacz"
                        icon = "<span class=material-symbols-outlined>tv</span>"
                    elif any(x in bp.lower() for x in ['karta', 'card', 'sd', 'pamięć', 'storage', 'gb']):
                        title = "Pamięć"
                        icon = "<span class=material-symbols-outlined>save</span>"
                    elif any(x in bp.lower() for x in ['night', 'nocn', 'ir', 'infrared', 'widoczność']):
                        title = "Nagrywanie nocne"
                        icon = "<span class=material-symbols-outlined>dark_mode</span>"
                    elif any(x in bp.lower() for x in ['sensor', 'czujnik', 'g-sensor', 'parking']):
                        title = "Czujniki i funkcje"
                        icon = "<span class=material-symbols-outlined>adjust</span>"
                    elif any(x in bp.lower() for x in ['kąt', 'angle', 'wide', 'szeroki', 'obiektyw']):
                        title = "Kąt widzenia"
                        icon = "<span class=material-symbols-outlined>visibility</span>"
                    elif any(x in bp.lower() for x in ['gps', 'lokalizacja', 'location']):
                        title = "GPS i lokalizacja"
                        icon = "<span class=material-symbols-outlined>location_on</span>"
                    else:
                        title = titles[i] if i < len(titles) else "Specyfikacja"
                        icon = icons[i] if i < len(icons) else "<span class=material-symbols-outlined>check_circle</span>"
                    
                    features.append({"icon": icon, "title": title, "text": bp})
            else:
                features = [
                    {"icon": "<span class=material-symbols-outlined>videocam</span>", "title": "Wideorejestrator", "text": "Profesjonalne nagrywanie podczas jazdy z wysoką jakością obrazu"},
                    {"icon": "<span class=material-symbols-outlined>save</span>", "title": "Zapis nagrań", "text": "Automatyczny zapis na kartę SD z funkcją zapisu w pętli"},
                    {"icon": "<span class=material-symbols-outlined>adjust</span>", "title": "Szeroki kąt", "text": "Szerokokątny obiektyw obejmujący cały pas ruchu"},
                ]
            
            specs = []
            
        else:
            # ULEPSZONY FALLBACK - inteligentne przetworzenie bullet points
            nazwa_clean = nazwa.replace('TODO ', '').replace('  ', ' ').strip()
            nl = nazwa.lower()

            # Inteligentne intro bazujące na słowach kluczowych w nazwie
            if any(x in nl for x in ['fotel', 'krzesło', 'krzeslo', 'chair', 'stool']):
                intro_text = f"Przedstawiamy {nazwa_clean} — ergonomiczny mebel zaprojektowany z myślą o komforcie wielogodzinnej pracy lub wypoczynku. Solidna konstrukcja i staranny dobór materiałów gwarantują trwałość i wygodę na lata. Idealny wybór do biura, gabinetu lub domowego kącika do pracy."
            elif any(x in nl for x in ['biurko', 'desk', 'stolik', 'stół', 'table']):
                intro_text = f"Przedstawiamy {nazwa_clean} — funkcjonalny mebel łączący nowoczesny design z praktycznością. Solidna konstrukcja zapewnia stabilność, a przemyślane detale ułatwiają codzienne użytkowanie w domu i biurze."
            elif any(x in nl for x in ['lampa', 'lamp', 'oświetlenie', 'light', 'led']):
                intro_text = f"Przedstawiamy {nazwa_clean} — nowoczesne oświetlenie łączące energooszczędność z eleganckim designem. Idealne rozwiązanie do domu, biura lub przestrzeni komercyjnej."
            elif any(x in nl for x in ['torba', 'plecak', 'bag', 'backpack', 'etui', 'case', 'pokrowiec']):
                intro_text = f"Przedstawiamy {nazwa_clean} — praktyczny akcesoriom zapewniający ochronę i wygodę transportu. Wykonany z wytrzymałych materiałów, zaprojektowany z myślą o codziennym użytkowaniu."
            elif any(x in nl for x in ['słuchawki', 'headphone', 'earphone', 'earbud', 'głośnik', 'speaker']):
                intro_text = f"Przedstawiamy {nazwa_clean} — sprzęt audio zapewniający doskonałą jakość dźwięku. Komfortowa konstrukcja i nowoczesna technologia dla wymagających użytkowników."
            elif any(x in nl for x in ['hub', 'adapter', 'switch', 'splitter', 'dock']):
                intro_text = f"Przedstawiamy {nazwa_clean} — niezawodny akcesoriom rozszerzający możliwości Twoich urządzeń. Plug & play, kompaktowa konstrukcja, stabilne połączenie."
            elif any(x in nl for x in ['uchwyt', 'holder', 'stand', 'stojak', 'mount']):
                intro_text = f"Przedstawiamy {nazwa_clean} — praktyczny uchwyt zapewniający stabilne i wygodne pozycjonowanie urządzenia. Solidna konstrukcja i uniwersalne dopasowanie."
            else:
                intro_text = f"Przedstawiamy {nazwa_clean} — produkt wyróżniający się solidnym wykonaniem i starannym doborem materiałów."

            # Jeśli są bullet points z Amazona, przetwórz je inteligentnie
            if bullet_points and len(bullet_points) > 0:
                features = []
                icons_default = ["<span class=material-symbols-outlined>check_circle</span>", "<span class=material-symbols-outlined>inventory_2</span>", "<span class=material-symbols-outlined>bolt</span>", "<span class=material-symbols-outlined>adjust</span>", "<span class=material-symbols-outlined>lightbulb</span>"]
                for i, bp in enumerate(bullet_points[:5]):
                    # Wyczyść Amazonowe formatowanie
                    bp = re.sub(r'[【】\[\]●○•·]', '', bp).strip()
                    bp = re.sub(r'^[\-\*]\s*', '', bp).strip()
                    bp = re.sub(r'\s+', ' ', bp)
                    bp_lower = bp.lower()

                    if any(x in bp_lower for x in ['materiał', 'material', 'wykonany', 'tkanina', 'metal', 'plastik', 'steel', 'skóra', 'leather', 'pu']):
                        icon, title = "🧵", "Materiał i wykonanie"
                    elif any(x in bp_lower for x in ['wymiar', 'rozmiar', 'size', 'cm', 'mm', 'cal', 'inch']):
                        icon, title = "<span class=material-symbols-outlined>straighten</span>", "Wymiary"
                    elif any(x in bp_lower for x in ['kolor', 'color', 'barwa', 'czarny', 'biały', 'black', 'white']):
                        icon, title = "<span class=material-symbols-outlined>palette</span>", "Wygląd"
                    elif any(x in bp_lower for x in ['zestaw', 'zawiera', 'includes', 'package', 'w zestawie']):
                        icon, title = "<span class=material-symbols-outlined>inventory_2</span>", "W zestawie"
                    elif any(x in bp_lower for x in ['funkcja', 'feature', 'możliwość', 'zastosowanie', 'use']):
                        icon, title = "<span class=material-symbols-outlined>bolt</span>", "Funkcje"
                    elif any(x in bp_lower for x in ['kompatybil', 'compatible', 'pasuje', 'fit', 'universal']):
                        icon, title = "<span class=material-symbols-outlined>link</span>", "Kompatybilność"
                    elif any(x in bp_lower for x in ['moc', 'power', 'watt', 'voltage', 'prąd', 'volt']):
                        icon, title = "<span class=material-symbols-outlined>bolt</span>", "Parametry"
                    elif any(x in bp_lower for x in ['łatw', 'easy', 'simple', 'prosty', 'montaż', 'install']):
                        icon, title = "<span class=material-symbols-outlined>build</span>", "Montaż"
                    elif any(x in bp_lower for x in ['ergonomic', 'komfort', 'comfort', 'wygod', 'podłokiet', 'oparcie']):
                        icon, title = "🪑", "Ergonomia i komfort"
                    elif any(x in bp_lower for x in ['regulacja', 'adjust', 'regulowan', 'height', 'tilt']):
                        icon, title = "<span class=material-symbols-outlined>build</span>", "Regulacja"
                    elif any(x in bp_lower for x in ['gwarancja', 'warranty', 'jakość', 'quality']):
                        icon, title = "<span class=material-symbols-outlined>check_circle</span>", "Jakość"
                    else:
                        icon = icons_default[i % len(icons_default)]
                        title = f"Cecha {i+1}"

                    features.append({"icon": icon, "title": title, "text": bp})
            else:
                # Generuj cechy z nazwy produktu
                features = []
                if any(x in nl for x in ['fotel', 'krzesło', 'chair']):
                    features = [
                        {"icon": "🪑", "title": "Ergonomiczna konstrukcja", "text": "Fotel zaprojektowany z myślą o wielogodzinnym komforcie siedzenia. Anatomiczny kształt oparcia wspiera prawidłową postawę kręgosłupa."},
                        {"icon": "🧵", "title": "Wysokiej jakości materiały", "text": "Staranny dobór materiałów wykończeniowych zapewnia trwałość, łatwość czyszczenia i elegancki wygląd na lata użytkowania."},
                        {"icon": "<span class=material-symbols-outlined>build</span>", "title": "Regulacja i dopasowanie", "text": "Możliwość regulacji wysokości i kąta oparcia pozwala dopasować fotel do indywidualnych potrzeb każdego użytkownika."},
                        {"icon": "<span class=material-symbols-outlined>fitness_center</span>", "title": "Solidna konstrukcja", "text": "Wzmocniona podstawa i wytrzymały mechanizm gazowy gwarantują stabilność i bezpieczeństwo użytkowania."},
                        {"icon": "<span class=material-symbols-outlined>inventory_2</span>", "title": "Łatwy montaż", "text": "Produkt dostarczany z czytelną instrukcją i wszystkimi niezbędnymi narzędziami do samodzielnego montażu."},
                    ]
                elif any(x in nl for x in ['hub', 'adapter', 'switch', 'splitter']):
                    features = [
                        {"icon": "<span class=material-symbols-outlined>power</span>", "title": "Wielofunkcyjne złącza", "text": "Rozbudowane portfolio portów pozwala podłączyć wszystkie potrzebne urządzenia peryferyjne jednocześnie."},
                        {"icon": "<span class=material-symbols-outlined>bolt</span>", "title": "Szybki transfer danych", "text": "Nowoczesne standardy transmisji zapewniają błyskawiczny transfer plików i stabilne połączenie."},
                        {"icon": "<span class=material-symbols-outlined>build</span>", "title": "Plug & Play", "text": "Gotowy do pracy natychmiast po podłączeniu — nie wymaga instalacji dodatkowych sterowników."},
                    ]
                else:
                    # Spróbuj wygenerować opisy przez AI na podstawie samej nazwy
                    if api_key:
                        try:
                            _ai_prompt = f"""Na podstawie nazwy produktu wygeneruj profesjonalny opis na Allegro.

PRODUKT: {nazwa}

Odpowiedz w JSON:
{{
  "intro": "2-3 zdania opisujące produkt - CO to jest, DO CZEGO służy, JAKIE problemy rozwiązuje",
  "features": [
    {{"icon": "emoji", "title": "Krótki tytuł cechy", "text": "2-3 zdania opisujące tę cechę z konkretnymi parametrami"}},
    ... (4-6 cech)
  ]
}}

ZASADY:
- Pisz po polsku
- Opisuj KONKRETNE cechy produktu bazując na nazwie (nie ogólniki!)
- ZAKAZANE frazy: "wysoka jakość", "kompletny zestaw", "praktyczne zastosowanie", "przemyślany design", "satysfakcja z zakupu"
- Pisz jak profesjonalny sprzedawca który ZNA ten produkt
- Podawaj konkretne zastosowania, parametry, korzyści
- Każda cecha to 2-3 zdania"""

                            _ai_resp = requests.post(
                                get_gemini_api_url(api_key),
                                json={
                                    'contents': [{'parts': [{'text': _ai_prompt}]}],
                                    'generationConfig': {'temperature': 0.4, 'maxOutputTokens': 4000}
                                },
                                timeout=60
                            )
                            if _ai_resp.status_code == 200:
                                _ai_data = _ai_resp.json()
                                _ai_text = _ai_data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                                if _ai_text:
                                    _ai_text = _ai_text.strip()
                                    if _ai_text.startswith('```'):
                                        _ai_text = re.sub(r'^```json?\s*', '', _ai_text)
                                        _ai_text = re.sub(r'\s*```$', '', _ai_text)
                                    import json as _json
                                    _ai_parsed = _json.loads(_ai_text)
                                    if _ai_parsed.get('intro'):
                                        intro_text = _ai_parsed['intro']
                                    if _ai_parsed.get('features'):
                                        features = _ai_parsed['features']
                                    print(f"[Opis AI] [OK] Wygenerowano z nazwy: {len(features)} sekcji")
                        except Exception as _e:
                            print(f"[Opis AI] [WARN] Fallback z nazwy failed: {_e}")

                    # Jeśli AI nie zadziałało - daj minimum (bez ogólników)
                    if not features:
                        features = [
                            {"icon": "<span class=material-symbols-outlined>inventory_2</span>", "title": "Zawartość zestawu", "text": f"Produkt {nazwa_clean[:60]} dostarczany w oryginalnym opakowaniu ze wszystkimi niezbędnymi akcesoriami."},
                        ]

            specs = []
    
    # ========== GENERUJ HTML (tylko dozwolone tagi Allegro sections) ==========
    # Dozwolone w description sections: h1, h2, h3, p, ul, ol, li
    # NIEDOZWOLONE: b, strong, i, em, u, div, span, img, table, br, style=
    # NIE DODAJEMY Specyfikacji/Marki - Allegro może odrzucić!

    # Tytuł produktu na górze jako h2
    html = f'<h3>{nazwa}</h3>'
    html += f'<p>{intro_text}</p>'

    # FEATURES SECTIONS - rozbudowane!
    for feature in features[:7]:  # Do 7 sekcji zamiast 5
        icon = feature.get('icon', '<span class=material-symbols-outlined>check_circle</span>')
        title = feature.get('title', '')
        text = feature.get('text', '')
        # Wyczyść Amazonowe formatowanie z tekstu
        text = re.sub(r'[【】\[\]●○•·]', '', text).strip()
        text = re.sub(r'\s+', ' ', text)
        # Tytuł cechy jako h3, tekst jako p (bez <b> - Allegro nie akceptuje)
        html += f'<h3>{icon} {title}</h3>'
        html += f'<p>{text}</p>'
    
    # ========== KONIEC OPISU - bez dodatkowych sekcji ==========
    # Zdjęcia idą przez Allegro API (description.sections), nie przez HTML
    
    html_preview = html
    
    # ========== GENERUJ PLAIN TEXT (fallback) ==========
    plain_text = f"{nazwa}\n\n{intro_text}\n\n"
    for feature in features[:7]:
        plain_text += f"{feature.get('icon', '<span class=material-symbols-outlined>check_circle</span>')} **{feature.get('title', '')}**\n{feature.get('text', '')}\n\n"
    
    # ASIN usunięty z opisów — nie pokazujemy pochodzenia produktu
    
    # Zwracamy html_preview
    return html_preview, plain_text.strip()


def generuj_gpsr_info(nazwa_produktu, kategoria='', product_specs=None):
    """
    Generuje informacje o bezpieczeństwie produktu zgodnie z GPSR
    (General Product Safety Regulation - Rozporządzenie UE 2023/988)

    Używa Gemini AI do generowania szczegółowych, specyficznych dla produktu
    ostrzeżeń i zaleceń bezpieczeństwa.

    Args:
        nazwa_produktu: Nazwa produktu do analizy
        kategoria: Kategoria produktu (opcjonalnie)
        product_specs: Specyfikacja produktu (dict, opcjonalnie)

    Returns:
        str: Tekst informacji o bezpieczeństwie w języku polskim (max 5000 znaków)
    """
    from .database import get_config

    # Dane producenta z specyfikacji
    producent_info = ""
    brand = ""
    if product_specs and isinstance(product_specs, dict):
        brand = product_specs.get('Brand') or product_specs.get('Manufacturer') or product_specs.get('Marka') or ''
        country = product_specs.get('Country of Origin') or product_specs.get('Kraj pochodzenia') or ''
        weight = product_specs.get('Weight') or product_specs.get('Item Weight') or product_specs.get('Waga') or ''
        material = product_specs.get('Material') or product_specs.get('Materiał') or ''
        if brand:
            producent_info += f"Producent/Marka: {brand}\n"
        if country:
            producent_info += f"Kraj pochodzenia: {country}\n"
        if weight:
            producent_info += f"Waga: {weight}\n"
        if material:
            producent_info += f"Materiał: {material}\n"

    # Osoba odpowiedzialna w UE
    eu_person = get_config('gpsr_eu_person', '')
    eu_address = get_config('gpsr_eu_address', '')
    eu_email = get_config('gpsr_eu_email', '')

    eu_info = ""
    if eu_person:
        eu_info = f"\nOsoba odpowiedzialna w UE: {eu_person}"
        if eu_address:
            eu_info += f"\nAdres: {eu_address}"
        if eu_email:
            eu_info += f"\nKontakt: {eu_email}"

    # === PRÓBA AI (Gemini via REST API) ===
    gemini_key = get_config('gemini_api_key', '')

    if gemini_key:
        try:
            import requests as _req

            specs_text = ""
            if product_specs and isinstance(product_specs, dict):
                specs_lines = [f"- {k}: {v}" for k, v in list(product_specs.items())[:15]]
                specs_text = "\nSpecyfikacja:\n" + "\n".join(specs_lines)

            prompt = f"""Wygeneruj informacje o bezpieczeństwie produktu zgodnie z Rozporządzeniem (UE) 2023/988 (GPSR).

PRODUKT: {nazwa_produktu}
KATEGORIA: {kategoria or 'nie podano'}
{specs_text}

WYMAGANIA:
1. Napisz po polsku
2. Zacznij od: "Lista ostrzeżeń dotyczących bezpieczeństwa [typ produktu] oparta o wymagania Rozporządzenia (UE) 2023/988 w sprawie ogólnego bezpieczeństwa produktów (GPSR)."
3. Wygeneruj 10-15 konkretnych punktów bezpieczeństwa SPECYFICZNYCH dla tego produktu (nie generycznych!)
4. Każdy punkt zaczynaj od "* " (gwiazdka ze spacją)
5. Punkty powinny dotyczyć: montażu, użytkowania, konserwacji, obciążenia, przechowywania, bezpieczeństwa dzieci, czyszczenia
6. Bądź konkretny - jeśli to krzesło pisz o krześle, jeśli elektronika o elektronice itd.
7. Na końcu dodaj: "Używaj produktu zgodnie z przeznaczeniem i instrukcją producenta."
8. NIE dodawaj danych producenta ani kontaktu - to zostanie dodane automatycznie
9. Max 3500 znaków (zostawiam miejsce na dane producenta)
10. Nie używaj markdown, tylko zwykły tekst z bullet pointami

ZWRÓĆ TYLKO tekst GPSR, bez żadnych dodatkowych komentarzy."""

            _api_url = get_gemini_api_url(gemini_key)
            _payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4000}
            }
            _resp = _req.post(_api_url, json=_payload, timeout=60)
            _resp.raise_for_status()
            _data = _resp.json()
            gpsr_text = _data['candidates'][0]['content']['parts'][0]['text'].strip()

            # Zamień bullet points na * (Allegro nie akceptuje •)
            gpsr_text = gpsr_text.replace('• ', '* ')
            gpsr_text = gpsr_text.replace('· ', '* ')
            gpsr_text = gpsr_text.replace('- ', '* ')
            gpsr_text = gpsr_text.replace('● ', '* ')
            gpsr_text = gpsr_text.replace('○ ', '* ')
            gpsr_text = gpsr_text.replace('— ', '- ')

            # Dodaj dane producenta na końcu
            if producent_info:
                gpsr_text += f"\n\n{producent_info.strip()}"
            if eu_info:
                gpsr_text += eu_info

            # Limit 5000 znaków (Allegro max)
            gpsr_text = gpsr_text[:5000]

            print(f"[SHIE] GPSR AI (REST): {len(gpsr_text)} znaków wygenerowane")
            return gpsr_text

        except Exception as e:
            print(f"[WARN] GPSR AI error: {e} — fallback do szablonu")

    # === FALLBACK: Szablon bez AI ===
    nazwa_lower = (nazwa_produktu or "").lower()

    result = f"""Lista ostrzeżeń dotyczących bezpieczeństwa produktu oparta o wymagania Rozporządzenia (UE) 2023/988 w sprawie ogólnego bezpieczeństwa produktów (GPSR).

* Używaj produktu wyłącznie zgodnie z jego przeznaczeniem opisanym w instrukcji.
* Przed pierwszym użyciem zapoznaj się z instrukcją obsługi i zachowaj ją na przyszłość.
* Nie modyfikuj produktu na własną rękę - może to wpłynąć na bezpieczeństwo użytkowania.
* Regularnie sprawdzaj stan produktu - w przypadku uszkodzeń, pęknięć lub zużycia zaprzestań użytkowania.
* Przechowuj produkt w suchym miejscu, z dala od bezpośredniego działania promieni słonecznych i źródeł ciepła.
* Produkt przechowuj poza zasięgiem dzieci, chyba że jest przeznaczony do użytku przez dzieci pod nadzorem dorosłych.
* Nie przekraczaj maksymalnych parametrów użytkowania określonych przez producenta (obciążenie, temperatura, itp.).
* W przypadku kontaktu z żywnością upewnij się, że produkt posiada odpowiednie atesty.
* Utylizuj produkt zgodnie z lokalnymi przepisami dotyczącymi gospodarki odpadami.
* W przypadku wątpliwości dotyczących bezpieczeństwa skontaktuj się z producentem lub osobą odpowiedzialną w UE.

Używaj produktu zgodnie z przeznaczeniem i instrukcją producenta."""

    if producent_info:
        result += f"\n\n{producent_info.strip()}"
    if eu_info:
        result += eu_info

    return result[:5000]
    


def optimize_title_allegro(title: str, brand: str = '', category: str = '') -> str:
    """
    Pełna optymalizacja tytułu pod Allegro:
    1. Tłumaczy na polski
    2. Wyciąga kluczowe cechy
    3. Formatuje pod SEO Allegro
    4. Max 75 znaków
    
    Używa Gemini AI jeśli dostępny klucz.
    """
    if not title:
        return ''
    
    from .database import get_config
    
    api_key = get_config('gemini_api_key', '')
    
    print(f"[BUIL] [SEO] Optymalizacja: '{title[:50]}...'")
    print(f"[BUIL] [SEO] API key present: {bool(api_key)}")
    
    if api_key:
        try:
            import requests
            
            prompt = f"""Zoptymalizuj ten tytuł produktu pod sprzedaż na Allegro.

TYTUŁ ORYGINALNY: {title}
{f'MARKA: {brand}' if brand else ''}
{f'KATEGORIA: {category}' if category else ''}

ZASADY:
1. POLSKI język - przetłumacz wszystko
2. MAX 75 znaków (to BARDZO ważne!)
3. Format: MARKA Model/Typ Kluczowe-Cechy Kolor
4. Najważniejsze słowa na POCZĄTKU (SEO)
5. Bez zbędnych słów: "do", "dla", "z", "premium", "original"
6. Bez emoji i znaków specjalnych
7. Liczby i jednostki: "100W", "2m", "5szt"
8. Kolor na końcu jeśli jest

PRZYKŁADY:
- "UGREEN USB C to DisplayPort Cable 4K 60Hz 2m Black" → "UGREEN Kabel USB-C DisplayPort 4K 60Hz 2m Czarny"
- "Anker PowerCore 10000mAh Portable Charger Power Bank" → "Anker PowerCore 10000mAh Powerbank Ładowarka"
- "Baseus Car Phone Holder Mount for Dashboard" → "Baseus Uchwyt Samochodowy na Telefon Kokpit"

Odpowiedz TYLKO zoptymalizowanym tytułem, nic więcej:"""

            print(f"[BUIL] [SEO] Wysyłam do Gemini...")
            response = requests.post(
                get_gemini_api_url(api_key),
                json={
                    'contents': [{'parts': [{'text': prompt}]}],
                    'generationConfig': {'maxOutputTokens': 100, 'temperature': 0.3}
                },
                timeout=15
            )
            
            print(f"[BUIL] [SEO] Response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                if 'candidates' in data and data['candidates']:
                    optimized = data['candidates'][0]['content']['parts'][0]['text'].strip()
                    # Usuń cudzysłowy jeśli są
                    optimized = optimized.strip('"\'')
                    print(f"[BUIL] [SEO] AI response: '{optimized}'")
                    # Sprawdź czy sensowny
                    if optimized and 10 < len(optimized) <= 80:
                        print(f"[OK] AI optymalizacja: '{title[:40]}...' → '{optimized}'")
                        return optimized
                    else:
                        print(f"[WARN] [SEO] AI response za długi lub za krótki: {len(optimized)} znaków")
            else:
                print(f"[WARN] [SEO] API error: {response.text[:200]}")
                    
        except Exception as e:
            print(f"[WARN] Błąd AI optymalizacji: {e}")
    else:
        print(f"[WARN] [SEO] Brak klucza Gemini API!")
    
    # Fallback: użyj lokalnej optymalizacji
    print(f"[BUIL] [SEO] Używam lokalnej optymalizacji (fallback)")
    return optimize_title_seo(title, max_length=75)
