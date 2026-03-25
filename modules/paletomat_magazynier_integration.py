"""
Integracja Paletomat → Magazynier
Auto-workflow: Wystawienie oferty → Wybór lokalizacji → Drukowanie etykiety
"""
import asyncio
from typing import Optional, Dict
from datetime import datetime
import sqlite3


class PaletomatMagazynierBridge:
    """Most między Paletomatem a Magazynierem"""
    
    def __init__(self, db_path: str = "akces_hub.db"):
        self.db_path = db_path
        self.niimbot_address = None  # Auto-wykryje przy pierwszym użyciu
    
    def get_db(self):
        """Połączenie z bazą"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def create_integration_table(self):
        """Tworzy tabelę do śledzenia integracji"""
        conn = self.get_db()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS paletomat_magazynier_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produkt_id INTEGER,
                offer_id TEXT,
                lokalizacja TEXT,
                etykieta_wydrukowana BOOLEAN DEFAULT 0,
                data_wystawienia TEXT,
                data_druku TEXT,
                status TEXT DEFAULT 'pending',
                notatki TEXT
            )
        ''')
        conn.commit()
        conn.close()
    
    def save_offer_created(
        self,
        produkt_id: int,
        offer_id: str,
        lokalizacja: Optional[str] = None
    ) -> int:
        """
        Zapisuje informację o wystawieniu oferty
        
        Args:
            produkt_id: ID produktu z tabeli produkty
            offer_id: ID oferty z Allegro
            lokalizacja: Lokalizacja w magazynie (opcjonalna)
        
        Returns:
            ID wpisu w logu
        """
        conn = self.get_db()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO paletomat_magazynier_log 
            (produkt_id, offer_id, lokalizacja, data_wystawienia, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (produkt_id, offer_id, lokalizacja, datetime.now().isoformat(), 'pending'))
        
        log_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return log_id
    
    def update_lokalizacja(self, log_id: int, lokalizacja: str):
        """Aktualizuje lokalizację dla oferty"""
        conn = self.get_db()
        conn.execute('''
            UPDATE paletomat_magazynier_log
            SET lokalizacja = ?, status = 'located'
            WHERE id = ?
        ''', (lokalizacja, log_id))
        conn.commit()
        conn.close()
    
    def mark_label_printed(self, log_id: int):
        """Oznacza etykietę jako wydrukowaną"""
        conn = self.get_db()
        conn.execute('''
            UPDATE paletomat_magazynier_log
            SET etykieta_wydrukowana = 1,
                data_druku = ?,
                status = 'printed'
            WHERE id = ?
        ''', (datetime.now().isoformat(), log_id))
        conn.commit()
        conn.close()
    
    def get_product_data(self, produkt_id: int) -> Optional[Dict]:
        """Pobiera dane produktu"""
        conn = self.get_db()
        cursor = conn.cursor()
        
        row = cursor.execute('''
            SELECT id, nazwa, cena_sprzedazy, ean, asin, lokalizacja
            FROM produkty
            WHERE id = ?
        ''', (produkt_id,)).fetchone()
        
        conn.close()
        
        if row:
            return dict(row)
        return None
    
    async def auto_print_label(
        self,
        produkt_id: int,
        lokalizacja: str,
        force_print: bool = True
    ) -> bool:
        """
        Automatycznie drukuje etykietę po wystawieniu oferty
        
        Args:
            produkt_id: ID produktu
            lokalizacja: Lokalizacja w magazynie
            force_print: Czy wymusić druk (True) czy tylko przygotować (False)
        
        Returns:
            True jeśli sukces
        """
        try:
            # Pobierz dane produktu
            product = self.get_product_data(produkt_id)
            if not product:
                print(f"[ERR] Nie znaleziono produktu {produkt_id}")
                return False
            
            print(f"[INVE] Przygotowywanie etykiety dla: {product['nazwa']}")
            
            # Jeśli nie wymuszono druku, tylko zapisz
            if not force_print:
                print("ℹ Etykieta przygotowana (druk ręczny)")
                return True
            
            # Import Niimbot (tylko jeśli drukujemy)
            from niimbot_printer import print_product_label
            
            # Drukuj
            print(f"[PRIN] Drukowanie etykiety na Niimbot...")
            success = await print_product_label(
                nazwa=product['nazwa'][:50],  # Max 50 znaków
                cena=product['cena_sprzedazy'] or 0.0,
                sku=product['ean'] or product['asin'] or f"ID{produkt_id}",
                lokalizacja=lokalizacja,
                device_address=self.niimbot_address
            )
            
            if success:
                print("[OK] Etykieta wydrukowana!")
            else:
                print("[WARN] Nie udało się wydrukować etykiety")
            
            return success
            
        except Exception as e:
            print(f"[ERR] Błąd drukowania: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def get_available_locations(self) -> list:
        """
        Zwraca dostępne lokalizacje w magazynie
        
        Returns:
            Lista słowników z lokalizacjami
        """
        conn = self.get_db()
        cursor = conn.cursor()
        
        # Pobierz wszystkie lokalizacje z warehouse_locations (jeśli istnieje)
        try:
            locations = cursor.execute('''
                SELECT shelf, level, capacity, items_count
                FROM warehouse_locations
                ORDER BY shelf, level
            ''').fetchall()
            
            result = []
            for loc in locations:
                code = f"{loc['shelf']}{loc['level']}"
                available = loc['capacity'] - loc['items_count']
                result.append({
                    'code': code,
                    'shelf': loc['shelf'],
                    'level': loc['level'],
                    'available': available,
                    'capacity': loc['capacity'],
                    'free': available > 0
                })
            
            conn.close()
            return result
            
        except sqlite3.OperationalError:
            # Tabela nie istnieje, zwróć default layout
            conn.close()
            return self._get_default_locations()
    
    def _get_default_locations(self) -> list:
        """Zwraca domyślne lokalizacje (A-N, P-S)"""
        shelves = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'P', 'Q', 'R', 'S']
        short_shelves = ['A', 'E', 'H', 'M']
        
        locations = []
        for shelf in shelves:
            max_level = 5 if shelf in short_shelves else 6
            for level in range(1, max_level + 1):
                locations.append({
                    'code': f"{shelf}{level}",
                    'shelf': shelf,
                    'level': level,
                    'available': 20,  # Default capacity
                    'capacity': 20,
                    'free': True
                })
        
        return locations


# ==================== FUNKCJE API ====================

def trigger_auto_workflow(
    produkt_id: int,
    offer_id: str,
    lokalizacja: Optional[str] = None,
    auto_print: bool = True
) -> Dict:
    """
    Główna funkcja - wywołaj po wystawieniu oferty w Paletomatcie
    
    Args:
        produkt_id: ID produktu z bazy
        offer_id: ID oferty Allegro
        lokalizacja: Lokalizacja (jeśli już wybrana) lub None
        auto_print: Czy auto-drukować etykietę
    
    Returns:
        Dict ze statusem
    """
    bridge = PaletomatMagazynierBridge()
    bridge.create_integration_table()
    
    # Zapisz w logu
    log_id = bridge.save_offer_created(produkt_id, offer_id, lokalizacja)
    
    result = {
        'success': True,
        'log_id': log_id,
        'produkt_id': produkt_id,
        'offer_id': offer_id,
        'lokalizacja': lokalizacja,
        'printed': False
    }
    
    # Jeśli podano lokalizację i auto_print, drukuj
    if lokalizacja and auto_print:
        try:
            success = asyncio.run(bridge.auto_print_label(produkt_id, lokalizacja, force_print=True))
            result['printed'] = success
            
            if success:
                bridge.mark_label_printed(log_id)
        except Exception as e:
            result['success'] = False
            result['error'] = str(e)
    
    return result


def get_locations_for_select() -> list:
    """
    Zwraca lokalizacje do wyboru w UI
    
    Returns:
        Lista lokalizacji z dostępnością
    """
    bridge = PaletomatMagazynierBridge()
    return bridge.get_available_locations()


def manual_print_label(produkt_id: int, lokalizacja: str) -> bool:
    """
    Ręczne drukowanie etykiety (z UI Magazyniera)
    
    Args:
        produkt_id: ID produktu
        lokalizacja: Lokalizacja
    
    Returns:
        True jeśli sukces
    """
    bridge = PaletomatMagazynierBridge()
    return asyncio.run(bridge.auto_print_label(produkt_id, lokalizacja, force_print=True))


# ==================== PRZYKŁAD UŻYCIA ====================

if __name__ == "__main__":
    print("=== TEST INTEGRACJI PALETOMAT → MAGAZYNIER ===\n")
    
    # Symulacja wystawienia oferty w Paletomatcie
    print("1⃣ Paletomat wystawił ofertę na Allegro...")
    
    result = trigger_auto_workflow(
        produkt_id=123,
        offer_id="12345678",
        lokalizacja="A2-3",
        auto_print=True  # Zmień na False żeby tylko zapisać bez druku
    )
    
    print(f"\n[OK] Rezultat: {result}")
    
    if result['printed']:
        print("[PRIN] Etykieta została wydrukowana!")
    else:
        print("ℹ Etykieta przygotowana (druk ręczny)")
    
    # Pobierz dostępne lokalizacje
    print("\n2⃣ Dostępne lokalizacje w magazynie:")
    locations = get_locations_for_select()
    for loc in locations[:10]:  # Pokaż pierwsze 10
        status = "<span class="material-symbols-outlined" style="color:#22c55e">check_circle</span> Wolne" if loc['free'] else "<span class="material-symbols-outlined" style="color:#ef4444">cancel</span> Pełne"
        print(f"   {loc['code']}: {status} ({loc['available']}/{loc['capacity']})")
