#!/usr/bin/env python3
"""
FULLAUTOMAT SCAN-TO-PRINT
=========================
Pętla: skan kodu zamówienia → utwórz przesyłkę WZA → pobierz etykietę → drukuj na Vretti

Użycie:
    python tools/fullautomat.py                  # tryb interaktywny (wpisuj order ID)
    python tools/fullautomat.py --test ORDER_ID  # test jednego zamówienia
    python tools/fullautomat.py --no-print       # bez drukowania (tylko generuj PDF)

Wymagania:
    pip install requests

Drukarka:
    Windows: używa os.startfile (domyślna drukarka PDF)
    Linux/Pi: używa lp/lpr (CUPS)
"""

import os
import sys
import time
import platform
import subprocess
import tempfile
import argparse
from datetime import datetime

# Dodaj ścieżkę główną projektu
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ============================================================
# KONFIGURACJA
# ============================================================

# DPD credentials (Twoje cenniki)
DPD_CREDENTIALS = {
    'cennik': 'bf1a1cf0-6a1e-41b3-a42e-d46846b35f43',
    'zwroty': '7b75ba63-0967-4536-a439-730f8e563a59',
    'reklamacje': '128af307-9341-4f8c-b406-63b9060cce7d',
}

# Domyślny gabaryt paczkomatu
DEFAULT_PARCEL_SIZE = 'A'  # A=mały, B=średni, C=duży

# Folder na etykiety PDF
LABELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'labels')

# Kolory terminala
class C:
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    PINK = '\033[95m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'


# ============================================================
# DRUKOWANIE ETYKIET
# ============================================================

def print_label(pdf_path, printer_name=None):
    """
    Wysyła PDF etykiety 100x150mm do drukarki w trybie cichym (silent).

    Kolejność prób na Windows:
    1. win32print + ShellExecute (cichy, bez okna podglądu)
    2. SumatraPDF -print-to-default -silent (cichy, bez okna)
    3. Ghostscript gswin64c (cichy)

    Linux/Pi: CUPS lp z wymiarami 100x150mm
    """
    system = platform.system()
    VRETTI_NAME = printer_name or 'Vretti 420B'  # Domyślna drukarka etykiet

    if system == 'Windows':
        # === METODA 1: win32print (najlepsza — cicha, bez okna) ===
        try:
            import win32print
            import win32api

            # Znajdź drukarkę Vretti (lub użyj domyślnej)
            printers = [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
            target_printer = None
            for p in printers:
                if 'vretti' in p.lower() or '420b' in p.lower():
                    target_printer = p
                    break
            if printer_name:
                target_printer = printer_name
            if not target_printer:
                target_printer = win32print.GetDefaultPrinter()

            # Ustaw jako domyślną tymczasowo i drukuj cicho
            old_default = win32print.GetDefaultPrinter()
            win32print.SetDefaultPrinter(target_printer)
            win32api.ShellExecute(0, 'print', pdf_path, None, '.', 0)  # 0 = SW_HIDE
            # Przywróć domyślną drukarkę po chwili
            time.sleep(2)
            win32print.SetDefaultPrinter(old_default)

            print(f"  {C.GREEN}[DRUK] Wysłano na {target_printer} (win32print, cichy){C.RESET}")
            return True
        except ImportError:
            pass  # win32print nie zainstalowany — próbuj SumatraPDF
        except Exception as e:
            print(f"  {C.YELLOW}[WARN] win32print błąd: {e} — próbuję SumatraPDF{C.RESET}")

        # === METODA 2: SumatraPDF (cichy, bez okna podglądu) ===
        try:
            sumatra_paths = [
                r'C:\Program Files\SumatraPDF\SumatraPDF.exe',
                r'C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe',
                os.path.expanduser(r'~\AppData\Local\SumatraPDF\SumatraPDF.exe'),
            ]
            sumatra = None
            for sp in sumatra_paths:
                if os.path.exists(sp):
                    sumatra = sp
                    break

            if sumatra:
                # Znajdź Vretti drukarkę
                target = VRETTI_NAME
                try:
                    import win32print
                    for p in [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]:
                        if 'vretti' in p.lower() or '420b' in p.lower():
                            target = p
                            break
                except ImportError:
                    pass

                # SumatraPDF silent print — format etykiety 100x150mm
                cmd = [
                    sumatra,
                    '-print-to', target,
                    '-silent',
                    '-print-settings', '100x150mm,fit',  # wymiary etykiety
                    pdf_path
                ]
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"  {C.GREEN}[DRUK] Wysłano na {target} (SumatraPDF, cichy 100x150mm){C.RESET}")
                return True
        except Exception as e:
            print(f"  {C.YELLOW}[WARN] SumatraPDF błąd: {e}{C.RESET}")

        # === METODA 3: Ghostscript (cichy) ===
        try:
            gs_paths = [
                r'C:\Program Files\gs\gs10.04.0\bin\gswin64c.exe',
                r'C:\Program Files\gs\gs10.03.1\bin\gswin64c.exe',
                r'C:\Program Files\gs\gs10.02.1\bin\gswin64c.exe',
            ]
            gs = None
            for gp in gs_paths:
                if os.path.exists(gp):
                    gs = gp
                    break
            if not gs:
                # Szukaj w PATH
                result = subprocess.run(['where', 'gswin64c'], capture_output=True, text=True)
                if result.returncode == 0:
                    gs = result.stdout.strip().split('\n')[0]

            if gs:
                cmd = [
                    gs, '-dBATCH', '-dNOPAUSE', '-dQUIET',
                    '-sDEVICE=mswinpr2',
                    f'-sOutputFile=%printer%{VRETTI_NAME}',
                    '-dFIXEDMEDIA', '-dDEVICEWIDTHPOINTS=283', '-dDEVICEHEIGHTPOINTS=425',  # 100x150mm w punktach
                    pdf_path
                ]
                subprocess.run(cmd, capture_output=True, timeout=30)
                print(f"  {C.GREEN}[DRUK] Wysłano na {VRETTI_NAME} (Ghostscript, 100x150mm){C.RESET}")
                return True
        except Exception as e:
            print(f"  {C.YELLOW}[WARN] Ghostscript błąd: {e}{C.RESET}")

        print(f"  {C.RED}[ERR] Brak metody druku! Zainstaluj SumatraPDF lub: pip install pywin32{C.RESET}")
        print(f"  {C.DIM}       PDF zapisany: {pdf_path}{C.RESET}")
        return False

    elif system == 'Linux':
        try:
            # CUPS: lp z wymiarami etykiety 100x150mm
            cmd = ['lp']
            if printer_name:
                cmd += ['-d', printer_name]
            cmd += [
                '-o', 'media=Custom.100x150mm',
                '-o', 'fit-to-page',
                '-o', 'orientation-requested=3',  # portrait
                pdf_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                print(f"  {C.GREEN}[DRUK] Wysłano na drukarkę CUPS (100x150mm){C.RESET}")
                return True
            else:
                print(f"  {C.RED}[ERR] lp error: {result.stderr}{C.RESET}")
                return False
        except FileNotFoundError:
            print(f"  {C.RED}[ERR] CUPS nie zainstalowany (sudo apt install cups){C.RESET}")
            return False

    print(f"  {C.YELLOW}[WARN] Nieobsługiwany system: {system}{C.RESET}")
    return False


def save_label(pdf_bytes, order_id):
    """Zapisuje etykietę PDF do folderu labels/"""
    os.makedirs(LABELS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%H%M%S')
    short_id = order_id[:8].upper()
    filename = f"etykieta_{short_id}_{timestamp}.pdf"
    filepath = os.path.join(LABELS_DIR, filename)
    with open(filepath, 'wb') as f:
        f.write(pdf_bytes)
    return filepath


# ============================================================
# PRZETWARZANIE ZAMÓWIENIA
# ============================================================

def process_order(order_id, parcel_size=None, auto_print=True):
    """
    Pełny flow: zamówienie → przesyłka WZA → etykieta → druk

    Returns: (success, message)
    """
    from modules.allegro_api import (
        is_authenticated, create_and_get_label, get_order_details
    )

    # Sanityzacja order_id
    order_id = order_id.strip()
    if not order_id or len(order_id) < 8:
        return False, "Za krótki order ID (min 8 znaków)"

    # Sprawdź auth
    if not is_authenticated():
        return False, "Nie zalogowano do Allegro! Wejdź na /allegro/ustawienia"

    print(f"\n{C.CYAN}{'='*60}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  PRZETWARZAM ZAMÓWIENIE: {order_id[:12]}...{C.RESET}")
    print(f"{C.CYAN}{'='*60}{C.RESET}")

    # Pobierz dane zamówienia
    print(f"  {C.DIM}Pobieram dane zamówienia...{C.RESET}")
    order, err = get_order_details(order_id)
    if order:
        items = order.get('lineItems', [])
        buyer = order.get('buyer', {}).get('login', '?')
        delivery = order.get('delivery', {})
        method_name = delivery.get('method', {}).get('name', '?')
        pickup = delivery.get('pickupPoint', {})
        address = delivery.get('address', {})

        # Wyświetl info
        print(f"  {C.GREEN}Kupujący:{C.RESET} {buyer}")
        print(f"  {C.GREEN}Dostawa:{C.RESET} {method_name}")
        if pickup and pickup.get('id'):
            print(f"  {C.GREEN}Paczkomat:{C.RESET} {pickup.get('id', '')} — {pickup.get('address', {}).get('city', '')}")
        elif address:
            print(f"  {C.GREEN}Adres:{C.RESET} {address.get('street', '')}, {address.get('city', '')}")

        for item in items:
            name = item.get('offer', {}).get('name', 'Produkt')
            qty = item.get('quantity', 1)
            price = item.get('price', {}).get('amount', '?')
            print(f"  {C.PINK}Produkt:{C.RESET} {name[:50]} x{qty} — {price} zł")

        # Auto-detect gabaryt
        if not parcel_size:
            method_lower = method_name.lower()
            if 'paczkomat' in method_lower or 'inpost' in method_lower:
                parcel_size = DEFAULT_PARCEL_SIZE
                print(f"  {C.YELLOW}Gabaryt:{C.RESET} {parcel_size} (InPost auto)")
            elif 'orlen' in method_lower:
                parcel_size = 'S'
                print(f"  {C.YELLOW}Gabaryt:{C.RESET} {parcel_size} (Orlen auto)")

    # Utwórz przesyłkę + pobierz etykietę
    print(f"\n  {C.YELLOW}Tworzę przesyłkę WZA...{C.RESET}")
    pdf_bytes, shipment_id, error = create_and_get_label(
        order_id,
        parcel_size=parcel_size
    )

    if error:
        return False, f"Błąd: {error}"

    if not pdf_bytes:
        return False, "Brak danych etykiety (PDF pusty)"

    # Zapisz PDF
    pdf_path = save_label(pdf_bytes, order_id)
    print(f"  {C.GREEN}[OK] Etykieta zapisana: {pdf_path}{C.RESET}")
    print(f"  {C.GREEN}[OK] Shipment ID: {shipment_id}{C.RESET}")

    # Drukuj
    if auto_print:
        print(f"  {C.YELLOW}Drukuję etykietę...{C.RESET}")
        printed = print_label(pdf_path)
        if not printed:
            print(f"  {C.YELLOW}[INFO] PDF zapisany w: {pdf_path}{C.RESET}")
    else:
        print(f"  {C.DIM}[SKIP] Drukowanie wyłączone (--no-print){C.RESET}")

    return True, f"OK! Shipment: {shipment_id}, PDF: {os.path.basename(pdf_path)}"


# ============================================================
# PĘTLA SKANERA
# ============================================================

def scanner_loop(auto_print=True, default_size=None):
    """
    Główna pętla — czeka na input ze skanera (emulacja klawiatury).
    Skaner wysyła order_id + Enter.
    """
    print(f"""
{C.CYAN}{C.BOLD}
╔══════════════════════════════════════════════════════════╗
║           FULLAUTOMAT SCAN-TO-PRINT v1.0                ║
║                                                         ║
║  Skanuj kod zamówienia lub wpisz order ID               ║
║  Komendy: 'q' = wyjdź, 'A/B/C' = zmień gabaryt        ║
║           'S/M/L' = Orlen, 'stats' = statystyki         ║
╚══════════════════════════════════════════════════════════╝
{C.RESET}""")

    parcel_size = default_size
    stats = {'ok': 0, 'err': 0, 'start': time.time()}

    while True:
        try:
            # Pokaż prompt
            size_info = f" [{parcel_size}]" if parcel_size else ""
            prompt = f"{C.CYAN}SKANUJ{size_info}>{C.RESET} "
            user_input = input(prompt).strip()

            if not user_input:
                continue

            # Komendy specjalne
            if user_input.lower() in ('q', 'quit', 'exit'):
                elapsed = time.time() - stats['start']
                print(f"\n{C.GREEN}Sesja zakończona. {stats['ok']} udanych, {stats['err']} błędów w {elapsed/60:.0f} min.{C.RESET}")
                break

            if user_input.upper() in ('A', 'B', 'C', 'S', 'M', 'L'):
                parcel_size = user_input.upper()
                print(f"  {C.YELLOW}Gabaryt ustawiony na: {parcel_size}{C.RESET}")
                continue

            if user_input.lower() == 'stats':
                elapsed = time.time() - stats['start']
                print(f"  {C.CYAN}Statystyki: {stats['ok']} udanych, {stats['err']} błędów, {elapsed/60:.0f} min sesji{C.RESET}")
                continue

            # Przetwórz zamówienie
            success, msg = process_order(user_input, parcel_size=parcel_size, auto_print=auto_print)

            if success:
                stats['ok'] += 1
                print(f"\n  {C.GREEN}{C.BOLD}✓ {msg}{C.RESET}")
                # Dźwięk sukcesu (Linux)
                if platform.system() == 'Linux':
                    os.system('aplay /usr/share/sounds/freedesktop/stereo/complete.oga 2>/dev/null &')
            else:
                stats['err'] += 1
                print(f"\n  {C.RED}{C.BOLD}✗ {msg}{C.RESET}")
                # Dźwięk błędu
                if platform.system() == 'Linux':
                    os.system('aplay /usr/share/sounds/freedesktop/stereo/dialog-error.oga 2>/dev/null &')

            print()

        except KeyboardInterrupt:
            print(f"\n{C.YELLOW}Przerwano (Ctrl+C){C.RESET}")
            break
        except EOFError:
            break


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='Fullautomat Scan-to-Print')
    parser.add_argument('--test', metavar='ORDER_ID', help='Testuj jedno zamówienie')
    parser.add_argument('--no-print', action='store_true', help='Nie drukuj (tylko generuj PDF)')
    parser.add_argument('--size', choices=['A', 'B', 'C', 'S', 'M', 'L'], help='Domyślny gabaryt paczki')
    parser.add_argument('--printer', help='Nazwa drukarki (domyślnie: systemowa)')
    args = parser.parse_args()

    # Inicjalizacja Flask app context (potrzebne do allegro_api)
    try:
        os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from app import app
        with app.app_context():
            if args.test:
                # Tryb testowy — jedno zamówienie
                success, msg = process_order(args.test, parcel_size=args.size, auto_print=not args.no_print)
                print(f"\n{'OK' if success else 'FAIL'}: {msg}")
                sys.exit(0 if success else 1)
            else:
                # Tryb pętli skanera
                scanner_loop(auto_print=not args.no_print, default_size=args.size)
    except ImportError as e:
        print(f"{C.RED}Błąd importu: {e}{C.RESET}")
        print(f"Uruchom z katalogu głównego: cd ~/akces-hub && python tools/fullautomat.py")
        sys.exit(1)


if __name__ == '__main__':
    main()
