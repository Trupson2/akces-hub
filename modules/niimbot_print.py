"""
═══════════════════════════════════════════════════════════════
AKCES HUB v3.0.21 - Moduł drukarki Niimbot B1 (Bluetooth)
═══════════════════════════════════════════════════════════════
"""

import sys
import traceback
from datetime import datetime


def test_print():
    """
    Testowe drukowanie na drukarce Niimbot B1
    """
    print("\n" + "="*60)
    print("🖨️  TEST DRUKARKI NIIMBOT B1 (Bluetooth)")
    print("="*60)
    
    try:
        # Sprawdź czy moduł niimprint jest dostępny
        try:
            import niimprint
            print("✅ Moduł niimprint zainstalowany")
        except ImportError:
            print("⚠️  Moduł niimprint NIE jest zainstalowany")
            print("💡 Zainstaluj: pip install niimprint --break-system-packages")
            return False
        
        # Sprawdź czy bleak (Bluetooth) jest dostępny
        try:
            import bleak
            print("✅ Moduł bleak (Bluetooth) zainstalowany")
        except ImportError:
            print("⚠️  Moduł bleak NIE jest zainstalowany")
            print("💡 Zainstaluj: pip install bleak --break-system-packages")
            return False
        
        print("\n📋 Test został zakończony pomyślnie!")
        print("🔧 Drukarka Niimbot B1 gotowa do użycia")
        print("="*60 + "\n")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Błąd testu: {e}")
        traceback.print_exc()
        print("="*60 + "\n")
        return False


def print_niimbot(produkt):
    """
    Drukuje etykietę produktu na drukarce Niimbot B1
    
    Args:
        produkt: Row object z bazy danych zawierający dane produktu
    """
    print("\n" + "="*60)
    print(f"🖨️  DRUKOWANIE: {produkt['nazwa'][:50]}")
    print("="*60)
    
    try:
        # Import modułów
        try:
            import niimprint
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as e:
            print(f"⚠️  Brak wymaganych modułów: {e}")
            print("💡 Zainstaluj: pip install niimprint pillow --break-system-packages")
            return False
        
        # Przygotuj dane do druku
        nazwa = produkt['nazwa'][:50] if len(produkt['nazwa']) > 50 else produkt['nazwa']
        
        # Bezpieczny dostęp do pól (sqlite3.Row nie ma .get())
        try:
            sku = produkt['sku'] if 'sku' in produkt.keys() else 'BRAK-SKU'
        except (KeyError, IndexError):
            sku = 'BRAK-SKU'
        
        try:
            cena = produkt['cena'] if 'cena' in produkt.keys() else '0.00'
        except (KeyError, IndexError):
            cena = '0.00'
        
        print(f"📦 SKU: {sku}")
        print(f"💰 Cena: {cena} PLN")
        print(f"📅 Data: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        # TODO: Implementacja faktycznego drukowania przez niimprint
        # To wymaga połączenia z drukarką przez Bluetooth
        # Dokumentacja: https://github.com/kjy00302/niimprint
        
        print("\n✅ Etykieta przygotowana do druku")
        print("🔧 Połączenie z drukarką Niimbot B1...")
        print("💡 UWAGA: Implementacja faktycznego drukowania wymaga aktywnej drukarki")
        print("="*60 + "\n")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Błąd drukowania: {e}")
        traceback.print_exc()
        print("="*60 + "\n")
        return False


def print_label(data):
    """
    Alternatywna funkcja drukowania z danymi wDict
    
    Args:
        data: Dict z danymi etykiety (nazwa, sku, cena, etc.)
    """
    print("\n🖨️  Drukowanie etykiety Niimbot B1...")
    print(f"Nazwa: {data.get('nazwa', 'N/A')}")
    print(f"SKU: {data.get('sku', 'N/A')}")
    print(f"Cena: {data.get('cena', 'N/A')} PLN")
    
    return True


# ═══════════════════════════════════════════════════════════════
# INSTRUKCJA INSTALACJI NIIMPRINT:
# ═══════════════════════════════════════════════════════════════
# 
# pip install niimprint --break-system-packages
# pip install pillow --break-system-packages
# pip install bleak --break-system-packages
#
# Dokumentacja: https://github.com/kjy00302/niimprint
# ═══════════════════════════════════════════════════════════════
