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


def generate_meta_title(produkt_nazwa: str, produkt_ean: str = '', produkt_asin: str = '', retry_count: int = 3) -> str:
    """
    Generuje META TITLE używając Gemini AI (REST API)

    Returns:
        META TITLE string (zawsze coś zwraca - fallback na produkt_nazwa)
    """
    import time
    import requests as _req

    short_name = produkt_nazwa[:50] + '...' if len(produkt_nazwa) > 50 else produkt_nazwa

    # Pobierz klucz Gemini z DB config
    from .database import get_config
    _gemini_key = get_config('gemini_api_key', '')
    if not _gemini_key:
        print(f"[WARN]  [AI DISABLED] Brak klucza Gemini w config - używam oryginalnej nazwy")
        return produkt_nazwa[:75]

    print(f"[SMAR] [AI REQUEST] Wysyłam do Gemini: {short_name}")

    for attempt in range(retry_count):
        try:
            prompt = f"""Jesteś ekspertem SEO na Allegro. Stwórz polski tytuł oferty.

ORYGINALNA NAZWA (często po angielsku/niemiecku): {produkt_nazwa}
{f'EAN: {produkt_ean}' if produkt_ean else ''}
{f'ASIN: {produkt_asin}' if produkt_asin else ''}

ZASADY:
1. Tytuł MUSI być po polsku
2. Struktura: [Rodzaj produktu] [Model/Seria] [Najważniejsze cechy] [Marka]
3. Rodzaj produktu ZAWSZE na początku (Smartwatch, Słuchawki, Etui, Kabel, Statyw, Ładowarka, Głośnik, Klawiatura, Mysz, Plecak, Lampa, Uchwyt, Filtr, Szczotka)
4. Przetłumacz angielskie nazwy na polski (Case→Etui, Charger→Ładowarka, Headphones→Słuchawki, Stand→Stojak, Cover→Pokrowiec, Tripod→Statyw, Keyboard→Klawiatura, Mouse→Mysz, Speaker→Głośnik, Screen Protector→Szkło Ochronne, Cable→Kabel, Holder→Uchwyt, Brush→Szczotka, Backpack→Plecak, Wallet→Portfel, Lamp→Lampa)
5. Używaj słów kluczowych które ludzie wyszukują na Allegro
6. MAX 75 znaków, bez przecinków, tylko spacje
7. BEZ stanu (Nowy/Używany/Powystawowy), BEZ ceny
8. Każde słowo z wielkiej litery

PRZYKŁADY:
"Samsung Galaxy Watch 4 44mm Bluetooth" → "Smartwatch Samsung Galaxy Watch 4 GPS NFC Pulsometr"
"JOILCAN 63cm Camera Tripod Aluminum" → "Statyw Fotograficzny Aluminiowy 63cm Regulowany JOILCAN"
"Anker USB-C Fast Charger 65W GaN" → "Ładowarka USB-C 65W Szybka GaN Anker"
"JBL Tune 510BT Wireless Headphones" → "Słuchawki Bezprzewodowe Bluetooth JBL Tune 510BT"
"Spigen iPhone 15 Pro Case Clear" → "Etui Ochronne iPhone 15 Pro Przezroczyste Spigen"

Odpowiedz TYLKO tytułem, nic więcej:"""

            if attempt > 0:
                print(f"   ↻ [RETRY {attempt+1}/{retry_count}] Ponawiam zapytanie...")

            from .utils import get_gemini_api_url
            _api_url = get_gemini_api_url(_gemini_key)
            _resp = _req.post(_api_url, json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 100}
            }, timeout=30)

            meta_title = None

            if _resp.status_code == 200:
                _data = _resp.json()
                try:
                    meta_title = _data['candidates'][0]['content']['parts'][0]['text'].strip()
                    print(f"   ✓ [RESPONSE] Odebrano: {meta_title[:100]}")
                except (KeyError, IndexError):
                    print(f"   [ERR] [PARSE ERROR] Nieoczekiwana struktura: {str(_data)[:200]}")
            elif _resp.status_code == 429:
                print(f"   [WARN]  [QUOTA] Rate limit - czekam 5s...")
                time.sleep(5)
                continue
            else:
                print(f"   [ERR] [API ERROR] {_resp.status_code}: {_resp.text[:200]}")

            if not meta_title:
                if attempt < retry_count - 1:
                    time.sleep(2)
                    continue
                print(f"   ✗ [FALLBACK] Używam oryginalnej nazwy po {retry_count} próbach")
                return produkt_nazwa[:75]

            # ========== CZYSZCZENIE TEKSTU ==========
            meta_title = re.sub(r'```.*?```', '', meta_title, flags=re.DOTALL)
            meta_title = meta_title.replace('**', '').strip()

            # Jeśli odpowiedź w JSON - wyciągnij tytuł
            if meta_title.startswith('{'):
                try:
                    json_match = re.search(r'\{[^}]+\}', meta_title, re.DOTALL)
                    if json_match:
                        data = json.loads(json_match.group(0))
                        for key in ['meta_title', 'title', 'name']:
                            if key in data:
                                meta_title = data[key]
                                break
                except Exception:
                    pass

            # Wyczyść cudzysłowy, newlines, wielokrotne spacje
            meta_title = meta_title.strip('"').strip("'").strip()
            meta_title = meta_title.replace('\n', ' ').replace('\r', ' ')
            meta_title = re.sub(r'\s+', ' ', meta_title)

            # Ogranicz do 75 znaków (ucinaj na granicy słowa)
            if len(meta_title) > 75:
                meta_title = meta_title[:75].rsplit(' ', 1)[0]

            # Walidacja
            if len(meta_title) < 5:
                print(f"   ✗ [ERROR] Tytuł za krótki: '{meta_title}'")
                if attempt < retry_count - 1:
                    time.sleep(2)
                    continue
                return produkt_nazwa[:75]

            print(f"   [OK] [SUCCESS] Wygenerowano: {meta_title}")
            return meta_title

        except Exception as e:
            error_msg = str(e)
            print(f"   ✗ [ERROR] Błąd Gemini (próba {attempt+1}/{retry_count}): {error_msg[:100]}")

            if '429' in error_msg or 'quota' in error_msg.lower():
                print(f"   ⏰ [QUOTA] Quota exceeded!")
                break

            if attempt >= retry_count - 1:
                return produkt_nazwa[:75]

            time.sleep(2)

    print(f"   ✗ [FALLBACK] Wszystkie próby failed - używam oryginalnej nazwy")
    return produkt_nazwa[:75]


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
        result["details"].append(f"<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">currency_exchange</span> Waluta: EUR → PLN (kurs: {eur_rate:.4f})")
    else:
        result["details"].append(f"<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">currency_exchange</span> Waluta: PLN (bez przeliczania)")

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
                            print(f"   [LIGH] TIP: Dodaj kartę kredytową w Google AI Studio aby zwiększyć limit z 15 RPM → 2000 RPM!")
                        
                        result["details"].append(f"<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">warning</span> Produkt {i}: {error_msg}")
                        continue
                
                conn.commit()

                print(f"\n[OK] [AI COMPLETE] Wygenerowano {meta_titles_generated}/{len(products)} meta_title")
                print(f"[OK] [STAN COMPLETE] Wykryto stan dla {stany_detected}/{len(products)} produktów")
                print(f"[OK] [GPSR COMPLETE] Wygenerowano GPSR dla {gpsr_generated}/{len(products)} produktów")
                
                result["details"].append(f"<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#22c55e">check_circle</span> Wygenerowano {meta_titles_generated}/{len(products)} tytułów META TITLE")
                result["details"].append(f"<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#22c55e">check_circle</span> Wykryto stan dla {stany_detected}/{len(products)} produktów")
                result["details"].append(f"<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#22c55e">check_circle</span> Wygenerowano GPSR dla {gpsr_generated}/{len(products)} produktów")
                
            except Exception as e:
                result["details"].append(f"<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">warning</span>  Błąd generowania META TITLE: {str(e)}")
        else:
            result["details"].append("<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">warning</span>  Gemini AI niedostępne - META TITLE nie wygenerowane")
        
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
