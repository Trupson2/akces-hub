#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SMART IMPORTER - Inteligentne rozpoznawanie dostawcy i cen
===========================================================
Automatycznie wykrywa dostawcę po nazwie pliku i stosuje właściwą logikę cenową
+ Automatyczne generowanie META TITLE przez Gemini AI
"""

import os
import re
import json
import urllib.request
from typing import Dict, Any, Optional, Tuple


def get_eur_pln_rate() -> float:
    """
    Pobiera aktualny kurs EUR/PLN z NBP API.
    Fallback: zwraca ostatni znany kurs z config DB, lub 4.30 jeśli brak.
    """
    try:
        req = urllib.request.Request(
            'https://api.nbp.pl/api/exchangerates/rates/a/eur/?format=json',
            headers={'Accept': 'application/json', 'User-Agent': 'AkcesHub/1.0'}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            rate = float(data['rates'][0]['mid'])
            # Zapisz w config jako fallback
            try:
                from modules.database import set_config
                set_config('nbp_eur_pln', str(rate))
            except:
                pass
            print(f"   [CURR] Kurs EUR/PLN z NBP: {rate:.4f}")
            return rate
    except Exception as e:
        print(f"   [WARN]  Nie udało się pobrać kursu NBP: {e}")
        # Fallback z config
        try:
            from modules.database import get_config
            cached = get_config('nbp_eur_pln', '')
            if cached:
                rate = float(cached)
                print(f"   [CURR] Kurs EUR/PLN z cache: {rate:.4f}")
                return rate
        except:
            pass
        print(f"   [CURR] Kurs EUR/PLN fallback: 4.30")
        return 4.30


def detect_stan_from_name(nazwa: str) -> str:
    """
    Wykrywa stan produktu z nazwy
    
    Returns:
        "Nowy", "Używany", "Powystawowy", lub "Nowy" (default)
    """
    nazwa_lower = nazwa.lower()
    
    # Powystawowy / Exhibit
    if any(word in nazwa_lower for word in [
        'powystawowy', 'exhibit', 'ausstellungsstück', 'display', 
        'demo', 'showroom', 'floor model'
    ]):
        return "Powystawowy"
    
    # Używany / Used
    if any(word in nazwa_lower for word in [
        'używany', 'used', 'gebraucht', 'refurbished', 
        'restored', 'second hand', 'pre-owned'
    ]):
        return "Używany"
    
    # Nowy / New (default)
    return "Nowy"


# Gemini AI - klucz pobierany z DB config (get_config('gemini_api_key'))


def _is_polish(text: str) -> bool:
    """Sprawdza czy tekst jest po polsku"""
    _pl_chars = set('ąęóśłżźćń')
    _pl_words = {'do','na','dla','ze','od','lub','bez','nie','jak','jest',
                 'kosiarka','kamera','poduszka','statyw','zestaw','piła',
                 'elektryczna','akumulatorowa','bezprzewodowy','wodoodporna'}
    text_lower = text.lower()
    if any(c in _pl_chars for c in text_lower):
        return True
    words = text_lower.split()
    return sum(1 for w in words if w in _pl_words) >= 2


def _translate_to_polish(title: str) -> str:
    """Tłumaczy tytuł na polski przez Google Translate (darmowy, niezawodny)"""
    try:
        import urllib.request
        import urllib.parse
        _encoded = urllib.parse.quote(title)
        _url = f'https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=pl&dt=t&q={_encoded}'
        _req = urllib.request.Request(_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(_req, timeout=10) as _resp:
            _data = json.loads(_resp.read().decode('utf-8'))
            # Google Translate zwraca [[["tłumaczenie","oryginał",...],...],...]
            translated = ''.join(part[0] for part in _data[0] if part[0])
            translated = translated.strip()
            if translated and len(translated) >= 10 and translated.lower() != title.lower():
                print(f"[TRANS] OK: {title[:35]} → {translated[:35]}")
                return translated
    except Exception as e:
        print(f"[TRANS] Google Translate błąd: {e}")
    return title


def _optimize_amazon_title(title: str, max_len: int = 75) -> str:
    """Czyści tytuł z Amazon na SEO Allegro — marka na końcu, kategoria na początku."""
    if not title:
        return title

    # Tłumacz jeśli nie po polsku
    if not _is_polish(title):
        title = _translate_to_polish(title)

    # Ucięcie po pierwszym przecinku (zbędne opisy)
    if ',' in title:
        title = title.split(',')[0].strip()

    # Kompresja jednostek: "1800 W" → "1800W", "40 cm" → "40cm"
    title = re.sub(r'(\d+)\s+(W|V|A|cm|mm|m|kg|g|l|L)\b', r'\1\2', title)
    title = re.sub(r'\s+', ' ', title).strip()

    # Przenieś markę (1. wyraz) i model (2. wyraz jeśli ma cyfry) na koniec
    # Amazon: "LawnMaster MEB1840M Kosiarka elektryczna 1800W"
    # Allegro: "Kosiarka Elektryczna 1800W LawnMaster MEB1840M"
    words = title.split()
    brand_words = []

    if len(words) >= 2:
        # Pierwszy wyraz = marka (jeśli nie jest polskim słowem opisowym)
        _common = {'kosiarka','kamera','statyw','piła','pila','odkurzacz','zgrzewarka',
                    'teleskop','monitor','klawiatura','mysz','poduszka','tło','ramka',
                    'zestaw','mini','wielofunkcyjna','cyfrowa','robot','wecool'}
        w0 = words[0]
        if w0.lower() not in _common:
            brand_words.append(w0)
            words = words[1:]
            # Drugi wyraz = model (jeśli ma cyfry+litery, np. MEB1840M, CLMF4841E)
            if words and any(c.isdigit() for c in words[0]) and any(c.isalpha() for c in words[0]):
                brand_words.append(words[0])
                words = words[1:]

    # Title Case na reszcie
    titled = []
    for w in words:
        if any(c.isdigit() for c in w) or w.isupper() or len(w) <= 2:
            titled.append(w)
        else:
            titled.append(w.capitalize())

    if brand_words:
        title = ' '.join(titled) + ' ' + ' '.join(brand_words)
    else:
        title = ' '.join(titled)

    # Truncate na granicy słowa
    if len(title) > max_len:
        title = title[:max_len].rsplit(' ', 1)[0]

    return title


def _format_seo_title(nazwa: str, max_len: int = 75) -> str:
    """Programatyczne formatowanie tytułu SEO - bez AI, szybko i niezawodnie."""
    if not nazwa:
        return nazwa

    # 1. Wyczyść ASIN-y, ilości, przecinki
    t = re.sub(r'\bB0[A-Za-z0-9]{7,}\b', '', nazwa).strip()
    t = re.sub(r'\b\d+\s*(?:szt\.?|pack|pcs|pieces|sztuk|zestawów?|stück)\b', '', t, flags=re.IGNORECASE)
    t = t.replace(',', '')
    t = re.sub(r'\s*[-–—]\s*$', '', t)  # trailing dashes
    t = re.sub(r'\s+', ' ', t).strip()

    # 2. Wykryj markę (pierwsze słowo jeśli wygląda na markę: CAPSLOCK, CamelCase, lub krótkie bez polskich znaków)
    words = t.split()
    brand = ''
    if len(words) >= 3:
        first = words[0]
        # Marka to zazwyczaj: HOMCA, AZDOME, Bonmedico, LawnMaster, ZHOOGE itp.
        _is_brand = (
            first.isupper() and len(first) >= 3 or  # HOMCA, AZDOME
            (first[0].isupper() and any(c.isupper() for c in first[1:]) and len(first) >= 4) or  # LawnMaster
            (first[0].isupper() and not any(c in 'ąćęłńóśźżĄĆĘŁŃÓŚŹŻ' for c in first) and len(first) >= 4
             and words[1][0].isupper() if len(words) > 1 else False)  # Bonmedico
        )
        if _is_brand:
            brand = first
            words = words[1:]

    # 3. Title Case (ale zachowaj akronimy jak USB, LED, 4K)
    titled = []
    for w in words:
        if w.isupper() and len(w) <= 5:  # USB, LED, HDMI, 4K
            titled.append(w)
        elif re.match(r'^\d', w):  # 1800W, 40cm
            titled.append(w)
        else:
            titled.append(w.capitalize())
    words = titled

    # 4. Złóż: [cechy] + [marka na końcu]
    if brand:
        words.append(brand)

    result = ' '.join(words)

    # 5. Obetnij do max_len na granicy słowa
    if len(result) > max_len:
        result = result[:max_len].rsplit(' ', 1)[0]

    return result.strip()


def generate_meta_title(produkt_nazwa: str, produkt_ean: str = '', produkt_asin: str = '', retry_count: int = 3, bullet_points: str = '') -> str:
    """
    Generuje META TITLE programatycznie z nazwy produktu.
    Formatuje: czyści, Title Case, marka na koniec, max 75 znaków.

    Returns:
        META TITLE string (zawsze coś zwraca)
    """

    # Wyczyść ASIN-y i kody Amazon z nazwy
    _clean_nazwa = re.sub(r'\bB0[A-Z0-9]{7,}\b', '', produkt_nazwa).strip()
    _clean_nazwa = re.sub(r'\s+', ' ', _clean_nazwa)

    if len(_clean_nazwa) < 10:
        print(f"[SMAR] [WARN] Nazwa za krótka: '{_clean_nazwa}' - zwracam jak jest")
        return _clean_nazwa

    meta_title = _format_seo_title(_clean_nazwa, 75)
    print(f"[SMAR] [OK] Wygenerowano: {meta_title} ({len(meta_title)} znaków)")
    return meta_title


def detect_vendor_from_filename(filename: str) -> Tuple[str, str]:
    """
    Rozpoznaje dostawcę po nazwie pliku
    
    Returns:
        (vendor_name, vendor_type): np. ("Jobalots", "manifest") lub ("Warrington", "offer")
    """
    filename_lower = filename.lower()
    
    # SCENARIUSZ A: Warrington/Miglo (pliki "Oferta", "Paleta", ID)
    if any(x in filename_lower for x in ['warrington', 'offer', 'oferta', 'pallet']):
        if 'miglo' in filename_lower:
            return ("Miglo", "offer")
        return ("Warrington", "offer")
    
    # SCENARIUSZ B: Jobalots (pliki "Manifest")
    if 'manifest' in filename_lower or 'jobalots' in filename_lower:
        return ("Jobalots", "manifest")
    
    # Dodatkowe heurystyki
    if 'miglo' in filename_lower:
        return ("Miglo", "offer")
    
    # Domyślnie Warrington (najczęstszy)
    return ("Warrington", "offer")


def calculate_unit_cost_with_vat(unit_price: float, quantity: int = 1) -> float:
    """
    Oblicza koszt jednostkowy BRUTTO (z VAT 23%)
    Dla Warrington/Miglo - cena z pliku * 1.23
    
    Args:
        unit_price: Cena jednostkowa NETTO z Excela
        quantity: Ilość sztuk (ignorowana - cena jest za sztukę)
        
    Returns:
        Koszt jednostkowy BRUTTO
    """
    return unit_price * 1.23


def calculate_proportional_cost(total_cost: float, rrp: float, all_products_rrp: list) -> float:
    """
    Rozdziela całkowity koszt palety proporcjonalnie do RRP produktów
    Dla Jobalots - użytkownik podaje KOSZT CAŁKOWITY, rozdzielamy proporcjonalnie
    
    Args:
        total_cost: Całkowity koszt palety BRUTTO (podany przez użytkownika)
        rrp: RRP tego produktu
        all_products_rrp: Lista RRP wszystkich produktów na palecie
        
    Returns:
        Koszt przypadający na ten produkt
    """
    total_rrp = sum(all_products_rrp)
    if total_rrp == 0:
        # Równy podział jeśli brak RRP
        return total_cost / len(all_products_rrp) if all_products_rrp else 0
    
    # Proporcjonalnie do RRP
    proportion = rrp / total_rrp
    return total_cost * proportion


def smart_import_excel(
    file_path: str,
    filename: str,
    paleta_id: Optional[int] = None,
    manual_vendor: Optional[str] = None,
    manual_total_cost: Optional[float] = None,
    currency: Optional[str] = None
) -> Dict[str, Any]:
    """
    Główna funkcja Smart Importera
    
    Args:
        file_path: Ścieżka do pliku Excel
        filename: Nazwa pliku (do auto-detekcji)
        paleta_id: ID palety (opcjonalne)
        manual_vendor: Ręcznie podany dostawca (nadpisuje auto-detekcję)
        manual_total_cost: Dla Jobalots - całkowity koszt palety BRUTTO
        
    Returns:
        Dict z wynikami importu
    """
    from modules.inventory_utils import import_excel_manifest
    from modules.database import get_db, execute_db
    
    result = {
        "success": False,
        "vendor_detected": "",
        "vendor_type": "",
        "products_imported": 0,
        "total_cost_calculated": 0.0,
        "errors": [],
        "details": []
    }
    
    # 1. WYKRYJ DOSTAWCĘ
    vendor, vendor_type = detect_vendor_from_filename(filename)
    if manual_vendor:
        vendor = manual_vendor
        result["details"].append(f"Użyto ręcznie podanego dostawcy: {vendor}")
    else:
        result["details"].append(f"Auto-wykryto dostawcę: {vendor} (typ: {vendor_type})")
    
    result["vendor_detected"] = vendor
    result["vendor_type"] = vendor_type

    # Waluta — z parametru lub domyślna wg dostawcy
    if currency is None:
        # Domyślnie: Jobalots = EUR, reszta = PLN
        currency = 'EUR' if vendor.lower() == 'jobalots' else 'PLN'
    currency = currency.upper()
    is_eur = currency == 'EUR'
    eur_rate = 1.0
    if is_eur:
        eur_rate = get_eur_pln_rate()
        result["details"].append(f"<span class=material-symbols-outlined>currency_exchange</span> Waluta: EUR → PLN (kurs: {eur_rate:.4f})")
    else:
        result["details"].append(f"<span class=material-symbols-outlined>currency_exchange</span> Waluta: PLN (bez przeliczania)")

    # 2. IMPORT PODSTAWOWY (użyj istniejącej funkcji)
    import_result = import_excel_manifest(
        file_path=file_path,
        dostawca=vendor,
        paleta_id=paleta_id,
        update_existing=False
    )
    
    if not import_result.get("success"):
        result["errors"] = import_result.get("errors", [])
        return result
    
    result["products_imported"] = import_result.get("added", 0)
    
    # 3. ZASTOSUJ LOGIKĘ CENOWĄ
    try:
        conn = get_db()
        
        if vendor_type == "offer":
            # SCENARIUSZ A: Warrington/Miglo - dokładne ceny jednostkowe z VAT
            eur_info = f" * {eur_rate:.2f} EUR→PLN" if is_eur else ""
            result["details"].append(f"Zastosowano logikę: Warrington/Miglo (Unit Price * 1.23{eur_info})")

            # Pobierz wszystkie produkty z tej palety
            products = conn.execute('''
                SELECT id, cena_netto, ilosc FROM produkty
                WHERE paleta_id = ? AND dostawca = ?
            ''', (paleta_id, vendor)).fetchall()

            for p in products:
                unit_netto = p['cena_netto'] if p['cena_netto'] > 0 else 0
                quantity = p['ilosc'] if p['ilosc'] > 0 else 1

                # Przelicz EUR→PLN jeśli zagraniczny dostawca
                unit_netto_pln = unit_netto * eur_rate

                # Cena BRUTTO = netto_pln * 1.23
                unit_brutto = calculate_unit_cost_with_vat(unit_netto_pln, quantity)
                total_brutto = unit_brutto * quantity

                # Aktualizuj w bazie
                execute_db('''
                    UPDATE produkty
                    SET cena_brutto = ?
                    WHERE id = ?
                ''', (total_brutto, p['id']))

                result["total_cost_calculated"] += total_brutto
            
        elif vendor_type == "manifest":
            # SCENARIUSZ B: Jobalots - proporcjonalny podział kosztu

            if manual_total_cost is None or manual_total_cost <= 0:
                result["errors"].append("Dla Jobalots musisz podać całkowity koszt palety BRUTTO!")
                result["success"] = False
                return result

            # Przelicz koszt palety EUR→PLN jeśli zagraniczny
            cost_pln = manual_total_cost * eur_rate if is_eur else manual_total_cost
            if is_eur:
                result["details"].append(f"Zastosowano logikę: Jobalots (proporcjonalny podział {manual_total_cost:.2f} EUR = {cost_pln:.2f} PLN)")
            else:
                result["details"].append(f"Zastosowano logikę: Jobalots (proporcjonalny podział {cost_pln:.2f} zł)")

            # Pobierz wszystkie RRP z palety
            products = conn.execute('''
                SELECT id, cena_allegro, cena_netto, ilosc FROM produkty
                WHERE paleta_id = ? AND dostawca = ?
            ''', (paleta_id, vendor)).fetchall()

            all_rrp = [p['cena_allegro'] * p['ilosc'] for p in products]
            total_rrp = sum(all_rrp)

            for i, p in enumerate(products):
                product_rrp = p['cena_allegro'] * p['ilosc']

                # Proporcjonalny koszt (od kosztu w PLN)
                product_cost = calculate_proportional_cost(
                    cost_pln,
                    product_rrp,
                    all_rrp
                )

                # Przelicz ceny EUR→PLN
                cena_allegro_pln = (p['cena_allegro'] or 0) * eur_rate if is_eur else (p['cena_allegro'] or 0)
                cena_netto_pln = (p['cena_netto'] or 0) * eur_rate if is_eur else (p['cena_netto'] or 0)

                # Aktualizuj w bazie (koszt + ceny w PLN)
                execute_db('''
                    UPDATE produkty
                    SET cena_brutto = ?, cena_allegro = ?, cena_netto = ?
                    WHERE id = ?
                ''', (product_cost, cena_allegro_pln, cena_netto_pln, p['id']))
            
            result["total_cost_calculated"] = manual_total_cost
        
        conn.commit()

        result["success"] = True

        # 4. GENERUJ META TITLE (jeśli Gemini dostępne)
        _gemini_key = get_config('gemini_api_key', '')
        if _gemini_key:
            try:
                print(f"\n[SMAR] [AI GENERATION] Rozpoczynam generowanie meta_title...")
                result["details"].append("Generuję META TITLE przez Gemini AI...")
                conn = get_db()
                
                # Pobierz wszystkie produkty z tej palety
                products = conn.execute('''
                    SELECT id, nazwa, ean, asin FROM produkty 
                    WHERE paleta_id = ?
                ''', (paleta_id,)).fetchall()
                
                print(f"   [INVE] Znaleziono {len(products)} produktów do przetworzenia")
                
                meta_titles_generated = 0
                stany_detected = 0
                gpsr_generated = 0
                for i, p in enumerate(products, 1):
                    try:
                        print(f"   [{i}/{len(products)}] Product ID {p['id']}: {p['nazwa'][:40]}...")
                        
                        # Wykryj stan z nazwy
                        stan = detect_stan_from_name(p['nazwa'])
                        print(f"       [ASSI] Stan wykryty: {stan}")
                        
                        # Generuj GPSR
                        from modules.utils import generuj_gpsr_info
                        gpsr = generuj_gpsr_info(p['nazwa'] or '', p.get('kategoria') or '')
                        if gpsr:
                            print(f"       [SHIE]  GPSR wygenerowany: {len(gpsr)} znaków")
                        
                        # Generuj meta_title
                        meta_title = generate_meta_title(
                            produkt_nazwa=p['nazwa'] or '',
                            produkt_ean=p['ean'] or '',
                            produkt_asin=p['asin'] or ''
                        )
                        
                        # Zapisz meta_title, stan i GPSR do bazy
                        if meta_title:
                            # Aktualizuj meta_title + nazwa (zastap angielska) + parameters z stanem + GPSR
                            params_update = '''
                                UPDATE produkty
                                SET meta_title = ?,
                                    nazwa = ?,
                                    parameters = json_set(
                                        json_set(COALESCE(parameters, '{}'), '$.Stan', ?),
                                        '$.GPSR', ?
                                    )
                                WHERE id = ?
                            '''
                            execute_db(params_update, (meta_title, meta_title, stan, gpsr or '', p['id']))
                            meta_titles_generated += 1
                            stany_detected += 1
                            if gpsr:
                                gpsr_generated += 1
                            print(f"   ✓ [{i}/{len(products)}] Zapisano: {meta_title[:60]}")
                            print(f"   ✓ Stan: {stan}")
                            if gpsr:
                                print(f"   ✓ GPSR: {len(gpsr)} znaków")
                        else:
                            print(f"   ✗ [{i}/{len(products)}] Brak meta_title (empty)")
                            # Zapisz przynajmniej stan + GPSR
                            params_update = '''
                                UPDATE produkty 
                                SET parameters = json_set(
                                    json_set(COALESCE(parameters, '{}'), '$.Stan', ?),
                                    '$.GPSR', ?
                                )
                                WHERE id = ?
                            '''
                            execute_db(params_update, (stan, gpsr or '', p['id']))
                            stany_detected += 1
                            if gpsr:
                                gpsr_generated += 1
                            print(f"   ✓ Stan zapisany: {stan}")
                            if gpsr:
                                print(f"   ✓ GPSR zapisany: {len(gpsr)} znaków")
                        
                        # Delay aby nie przekroczyć limitu API
                        # AUTO-ADJUST: zaczyna od 0.1s, zwiększa gdy quota error
                        if i < len(products):
                            import time
                            # API DELAY - wolniejszy = stabilniejszy
                            # TIER 1: 2000 RPM, ale lepiej wolniej = pewniej
                            if not hasattr(smart_import_excel, '_api_delay'):
                                smart_import_excel._api_delay = 2.0  # 2s = ~30 req/min (BEZPIECZNY!)
                            
                            time.sleep(smart_import_excel._api_delay)
                            print(f"   [TIME]  Delay: {smart_import_excel._api_delay}s (wolniej = stabilniej)")
                    
                    except Exception as e:
                        # Jeśli błąd (np. quota) - kontynuuj z innymi
                        error_msg = str(e)[:100]
                        print(f"   ✗ [{i}/{len(products)}] ERROR: {error_msg}")
                        
                        # AUTO-SLOWDOWN: zwiększ delay gdy quota exceeded
                        if '429' in error_msg or 'quota' in error_msg.lower() or 'Resource has been exhausted' in error_msg:
                            if not hasattr(smart_import_excel, '_api_delay'):
                                smart_import_excel._api_delay = 0.1
                            
                            old_delay = smart_import_excel._api_delay
                            smart_import_excel._api_delay = min(old_delay * 2, 5.0)  # Max 5s
                            print(f"   [WARN]  QUOTA EXCEEDED! Zwiększam delay: {old_delay}s → {smart_import_excel._api_delay}s")
                            print(f"   💡 TIP: Dodaj kartę kredytową w Google AI Studio aby zwiększyć limit z 15 RPM → 2000 RPM!")
                        
                        result["details"].append(f"<span class=material-symbols-outlined>warning</span> Produkt {i}: {error_msg}")
                        continue
                
                conn.commit()

                print(f"\n[OK] [AI COMPLETE] Wygenerowano {meta_titles_generated}/{len(products)} meta_title")
                print(f"[OK] [STAN COMPLETE] Wykryto stan dla {stany_detected}/{len(products)} produktów")
                print(f"[OK] [GPSR COMPLETE] Wygenerowano GPSR dla {gpsr_generated}/{len(products)} produktów")
                
                result["details"].append(f"<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Wygenerowano {meta_titles_generated}/{len(products)} tytułów META TITLE")
                result["details"].append(f"<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Wykryto stan dla {stany_detected}/{len(products)} produktów")
                result["details"].append(f"<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Wygenerowano GPSR dla {gpsr_generated}/{len(products)} produktów")
                
            except Exception as e:
                result["details"].append(f"<span class=material-symbols-outlined>warning</span>  Błąd generowania META TITLE: {str(e)}")
        else:
            result["details"].append("<span class=material-symbols-outlined>warning</span>  Gemini AI niedostępne - META TITLE nie wygenerowane")
        
    except Exception as e:
        result["errors"].append(f"Błąd obliczania cen: {str(e)}")
        return result
    
    return result


def prompt_for_total_cost_if_jobalots(vendor_type: str) -> Optional[float]:
    """
    Jeśli to Jobalots, wyświetl prompt o całkowity koszt
    
    Returns:
        None jeśli nie Jobalots, lub wartość float z inputu
    """
    if vendor_type != "manifest":
        return None
    
    # To będzie wywołane z UI - Flask przekaże to jako parametr
    # Zwracamy None tutaj, bo prompt jest w HTML formularzu
    return None
