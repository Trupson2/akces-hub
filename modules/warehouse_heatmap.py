"""
Warehouse Heatmap Module - 3D Wizualizacja magazynu
===================================================

Zarządzanie lokalizacjami magazynowymi i generowanie heatmapy 3D.

Struktura magazynu:
- Regalów: 4 (A, B, C, D)
- Półek na regał: 5 (1-5, od góry do dołu)
- Sekcji na półkę: 4 (1-4, od lewej do prawej)

Format lokalizacji: "A2-3" (Regał A, Półka 2, Sekcja 3)
"""

import sqlite3
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

from .database import get_db, execute_db, query_db


# ============================================================
# KONFIGURACJA MAGAZYNU - Warehouse configuration
# ============================================================

WAREHOUSE_CONFIG = {
    'layout': 'sections',

    # Sekcje magazynu — DYNAMICZNE (dodajesz sekcje = legenda sie aktualizuje)
    'sections': {
        'A': {'name': 'LEWA SCIANA',       'racks': ['A1', 'A2', 'A3', 'A4']},
        'B': {'name': 'TYLNA SCIANA',      'racks': ['B1', 'B2', 'B3', 'B4', 'B5']},
        'C': {'name': 'PRAWA SCIANA',      'racks': ['C1', 'C2', 'C3', 'C4']},
        'D': {'name': 'SRODEK (LEWY)',     'racks': ['D1', 'D2', 'D3']},
        'E': {'name': 'SRODEK (PRAWY)',    'racks': ['E1', 'E2']},
    },

    # Wszystkie regaly (generowane z sekcji)
    'shelves': [
        'A1', 'A2', 'A3', 'A4',
        'B1', 'B2', 'B3', 'B4', 'B5',
        'C1', 'C2', 'C3', 'C4',
        'D1', 'D2', 'D3',
        'E1', 'E2',
    ],

    # Domyslne wymiary
    'default': {
        'height': 225,
        'width': 85,
        'depth': 42,
        'levels': 6,
        'capacity_per_shelf': 50
    },

    # Kolory sekcji (dla heatmapy i legendy)
    'section_colors': {
        'A': '#2563eb',
        'B': '#7c3aed',
        'C': '#059669',
        'D': '#d97706',
        'E': '#dc2626',
    },

    'total_shelves': 18,
    'levels': 6,
    'section_1': ['A1', 'A2', 'A3', 'A4', 'B1', 'B2', 'B3', 'B4', 'B5'],
    'section_2': ['C1', 'C2', 'C3', 'C4', 'D1', 'D2', 'D3', 'E1', 'E2'],
}


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class WarehouseLocation:
    """Reprezentacja lokalizacji w magazynie (cała półka)"""
    shelf: str  # A-N, P-S
    level: int  # 1-6 (numer półki)
    items_count: int  # Ilość produktów
    capacity: int  # Max pojemność
    height: int = 225  # Wysokość regału (180 lub 225)
    max_levels: int = 6  # Max półek dla tego regału (5 lub 6)
    
    @property
    def code(self) -> str:
        """Zwraca kod lokalizacji np. 'A2' (regał A, półka 2)"""
        return f"{self.shelf}{self.level}"
    
    @property
    def fill_percentage(self) -> float:
        """Zwraca % zapełnienia (0.0 - 1.0)"""
        if self.capacity == 0:
            return 0.0
        return min(self.items_count / self.capacity, 1.0)
    
    @property
    def fill_status(self) -> str:
        """Zwraca status: empty, low, medium, high, full"""
        pct = self.fill_percentage
        if pct == 0:
            return 'empty'
        elif pct < 0.25:
            return 'low'
        elif pct < 0.5:
            return 'medium'
        elif pct < 0.75:
            return 'high'
        else:
            return 'full'
    
    @property
    def color(self) -> str:
        """Zwraca kolor dla heatmapy (hex)"""
        status_colors = {
            'empty': '#2ecc71',    # Zielony
            'low': '#3498db',      # Niebieski
            'medium': '#f39c12',   # Pomarańczowy
            'high': '#e67e22',     # Ciemny pomarańczowy
            'full': '#e74c3c',     # Czerwony
        }
        return status_colors.get(self.fill_status, '#95a5a6')


@dataclass
class ProductLocation:
    """Produkt z lokalizacją"""
    id: int
    name: str
    sku: str
    location: str
    quantity: int
    added_date: str


# ============================================================
# INICJALIZACJA BAZY
# ============================================================

def init_warehouse_tables():
    """Tworzy tabele dla systemu lokalizacji magazynowych"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Tabela lokalizacji magazynowych
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS warehouse_locations (
            location_code TEXT PRIMARY KEY,
            shelf TEXT NOT NULL,
            level INTEGER NOT NULL,
            section INTEGER NOT NULL,
            capacity INTEGER DEFAULT 20,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Tabela przypisań produktów do lokalizacji
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS product_locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            location_code TEXT NOT NULL,
            quantity INTEGER DEFAULT 1,
            added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY (location_code) REFERENCES warehouse_locations(location_code)
        )
    ''')
    
    # Index dla szybszego wyszukiwania
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_product_locations_product 
        ON product_locations(product_id)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_product_locations_location 
        ON product_locations(location_code)
    ''')
    
    conn.commit()
    
    # Wygeneruj wszystkie lokalizacje jeśli ich nie ma
    _generate_default_locations()
    
    print("[OK] Warehouse tables initialized")


def _generate_default_locations():
    """Generuje domyślne lokalizacje magazynowe"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Sprawdź czy są już jakieś lokalizacje
    existing = cursor.execute(
        'SELECT COUNT(*) FROM warehouse_locations'
    ).fetchone()[0]
    
    if existing > 0:
        return  # Już są lokalizacje
    
    # Generuj lokalizacje: każdy regał × każdy poziom
    locations = []
    levels = WAREHOUSE_CONFIG.get('levels', 6)
    capacity = WAREHOUSE_CONFIG['default'].get('capacity_per_shelf', 50)
    for shelf in WAREHOUSE_CONFIG['shelves']:
        # Determine section (1 or 2) based on config
        section = 1 if shelf in WAREHOUSE_CONFIG.get('section_1', []) else 2
        for level in range(1, levels + 1):
            code = f"{shelf}{level}"
            locations.append((
                code,
                shelf,
                level,
                section,
                capacity
            ))
    
    # Wstaw do bazy
    cursor.executemany('''
        INSERT INTO warehouse_locations 
        (location_code, shelf, level, section, capacity)
        VALUES (?, ?, ?, ?, ?)
    ''', locations)
    
    conn.commit()
    print(f"[OK] Generated {len(locations)} warehouse locations")


# ============================================================
# ZARZĄDZANIE LOKALIZACJAMI
# ============================================================

def get_all_locations() -> List[WarehouseLocation]:
    """
    Zwraca wszystkie lokalizacje z aktualnymi stanami.
    INTEGRACJA: Czyta dane z magazyniera (tabela produkty) jeśli dostępne.
    STRUKTURA: 14 regałów w układzie U (A-N, P-S), różne wysokości
    """
    conn = get_db()
    cursor = conn.cursor()
    
    # Sprawdź czy tabela produkty istnieje (magazynier)
    try:
        cursor.execute("SELECT COUNT(*) FROM produkty")
        has_magazynier = True
    except:
        has_magazynier = False
    
    default_levels = WAREHOUSE_CONFIG['default']['levels']
    default_height = WAREHOUSE_CONFIG['default']['height']

    # Jeśli magazynier istnieje, użyj jego danych
    if has_magazynier:
        all_locations = []

        for shelf in WAREHOUSE_CONFIG['shelves']:
            max_levels = default_levels
            shelf_height = default_height
            
            for level in range(1, max_levels + 1):
                location_code = f"{shelf}{level}"
                
                # Policz produkty w tej lokalizacji z magazyniera
                try:
                    # NAPRAWIONE: Dokładne dopasowanie lokalizacji
                    # Format: "A2-3" lub "A2" lub " A2 " itp
                    # FILTRUJ specjalne lokalizacje (Piwnica, Garaż, etc.) - nie wliczaj do regałów
                    items_count = cursor.execute('''
                        SELECT COALESCE(SUM(ilosc), 0)
                        FROM produkty
                        WHERE (
                            UPPER(TRIM(lokalizacja)) = ?
                            OR UPPER(lokalizacja) LIKE ? 
                            OR UPPER(lokalizacja) LIKE ?
                        )
                        AND UPPER(lokalizacja) NOT IN ('PIWNICA', 'GARAZ', 'MAGAZYN', 'BIURO', 'INNE')
                        AND lokalizacja NOT LIKE '%piwnica%'
                        AND lokalizacja NOT LIKE '%garaz%'
                    ''', (location_code, f"{location_code}-%", f"%,{location_code},%")).fetchone()[0]
                    
                    # Jeśli wciąż 0, sprawdź czy nie ma partial match (np. "A2" w "Regał A2")
                    if items_count == 0:
                        # Sprawdź tylko jeśli lokalizacja zawiera dokładnie nasz kod
                        items_count = cursor.execute('''
                            SELECT COALESCE(SUM(ilosc), 0)
                            FROM produkty
                            WHERE (
                                UPPER(lokalizacja) LIKE ? 
                                OR UPPER(lokalizacja) LIKE ?
                            )
                            AND LENGTH(lokalizacja) < 20
                            AND UPPER(lokalizacja) NOT IN ('PIWNICA', 'GARAZ', 'MAGAZYN', 'BIURO', 'INNE')
                        ''', (f"% {location_code} %", f"% {location_code}-%")).fetchone()[0]
                    
                except Exception as e:
                    print(f"[WARN] Error counting items for {location_code}: {e}")
                    items_count = 0
                
                loc = WarehouseLocation(
                    shelf=shelf,
                    level=level,
                    capacity=WAREHOUSE_CONFIG['default']['capacity_per_shelf'],
                    items_count=items_count,
                    height=shelf_height,
                    max_levels=max_levels
                )
                all_locations.append(loc)
        
        return all_locations
    
    # Fallback: generuj puste lokalizacje
    all_locations = []
    for shelf in WAREHOUSE_CONFIG['shelves']:
        max_levels = default_levels
        shelf_height = default_height
        
        for level in range(1, max_levels + 1):
            loc = WarehouseLocation(
                shelf=shelf,
                level=level,
                capacity=WAREHOUSE_CONFIG['default']['capacity_per_shelf'],
                items_count=0,
                height=shelf_height,
                max_levels=max_levels
            )
            all_locations.append(loc)
    
    return all_locations


def get_products_from_magazynier(location_code: str) -> List[Dict]:
    """
    Pobiera produkty z tabeli magazyniera (produkty) dla danej lokalizacji.
    Format: A1, A2, B1, etc. (cała półka)
    """
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # Sprawdź czy tabela produkty istnieje
        tables = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='produkty'"
        ).fetchall()
        
        if not tables:
            return []
        
        # Normalizuj kod lokalizacji (A1, A2, B3, etc.)
        normalized = location_code.upper().strip()
        
        print(f"[SEAR] Searching for location: '{normalized}'")
        
        # ROZSZERZONE WYSZUKIWANIE - znajdzie nawet jeśli lokalizacja ma dodatkowy tekst
        # Przykłady: "A11", "A1-1", "Półka A11", "Regał A półka 1", itp.
        products = []
        
        # Najpierw: dokładne dopasowanie (najszybsze)
        rows = cursor.execute('''
            SELECT 
                id, ean, asin, nazwa, ilosc, lokalizacja, 
                cena_allegro, dostawca, zdjecie_url
            FROM produkty
            WHERE UPPER(TRIM(lokalizacja)) = ?
            ORDER BY id DESC
        ''', (normalized,)).fetchall()
        
        if not rows:
            # Drugie: zawiera kod lokalizacji (z word boundaries)
            rows = cursor.execute('''
                SELECT 
                    id, ean, asin, nazwa, ilosc, lokalizacja, 
                    cena_allegro, dostawca, zdjecie_url
                FROM produkty
                WHERE UPPER(lokalizacja) LIKE ?
                   OR UPPER(lokalizacja) LIKE ?
                   OR UPPER(lokalizacja) LIKE ?
                   OR UPPER(lokalizacja) LIKE ?
                ORDER BY id DESC
            ''', (
                f"{normalized}%",      # Zaczyna się od
                f"% {normalized}%",    # Zawiera ze spacją przed
                f"%{normalized} %",    # Zawiera ze spacją po
                f"%{normalized}-%"     # Z myślnikiem
            )).fetchall()
        
        print(f"   Found {len(rows)} products")
        
        for row in rows:
            print(f"   - {row[5]}: {row[3][:40]}...")
            products.append({
                'id': row[0],
                'ean': row[1],
                'asin': row[2],
                'nazwa': row[3] or 'Brak nazwy',
                'ilosc': row[4] or 0,
                'lokalizacja': row[5],
                'cena_allegro': row[6] or 0,
                'dostawca': row[7] or 'Nieznany',
                'zdjecie_url': row[8]
            })
        
        return products
        
    except Exception as e:
        print(f"Błąd pobierania produktów z magazyniera: {e}")
        return []


def get_location_details(location_code: str) -> Optional[Dict]:
    """
    Zwraca szczegóły lokalizacji z listą produktów.
    INTEGRACJA: Najpierw próbuje pobrać z magazyniera, potem z warehouse_locations.
    FORMAT: A1, A2, B3, etc. (całe półki)
    """
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. Spróbuj pobrać produkty z magazyniera
    magazynier_products = get_products_from_magazynier(location_code)
    
    # 2. Pobierz info o lokalizacji z warehouse_locations (jeśli istnieje)
    try:
        loc_row = cursor.execute('''
            SELECT shelf, level, capacity, notes
            FROM warehouse_locations
            WHERE location_code = ?
        ''', (location_code,)).fetchone()
    except:
        loc_row = None
    
    # Jeśli nie ma lokalizacji i nie ma produktów, zwróć None
    if not loc_row and not magazynier_products:
        return None
    
    # Parse kodu lokalizacji (np. "A11" -> shelf='A1', level=1; "A1" -> shelf='A1')
    import re
    m = re.match(r'^([A-E]\d)(\d*)$', location_code.upper())
    if m:
        shelf = m.group(1)
        level = int(m.group(2)) if m.group(2) else 0
    else:
        shelf = location_code
        level = 0
    
    # Użyj danych z bazy warehouse lub defaults
    if loc_row:
        capacity = loc_row[2]
        notes = loc_row[3]
    else:
        # FIXED: używaj capacity_per_shelf zamiast max_items_per_shelf
        capacity = WAREHOUSE_CONFIG['default']['capacity_per_shelf']
        notes = None
    
    # 3. Jeśli są produkty z magazyniera, użyj ich
    if magazynier_products:
        total_items = sum(p['ilosc'] for p in magazynier_products)
        
        return {
            'location_code': location_code,
            'shelf': shelf,
            'level': level,
            'capacity': capacity,
            'notes': notes,
            'items_count': total_items,
            'fill_percentage': round(total_items / capacity * 100, 1) if capacity > 0 else 0,
            'products': magazynier_products,
            'source': 'magazynier'
        }
    
    # 4. Fallback: użyj warehouse_locations (stary system)
    products = cursor.execute('''
        SELECT 
            pl.product_id,
            pl.quantity,
            pl.added_date,
            pl.notes
        FROM product_locations pl
        WHERE pl.location_code = ?
        ORDER BY pl.added_date DESC
    ''', (location_code,)).fetchall()
    
    total_items = sum(p[1] for p in products)
    
    return {
        'location_code': location_code,
        'shelf': shelf,
        'level': level,
        'capacity': capacity,
        'notes': notes,
        'items_count': total_items,
        'fill_percentage': round(total_items / capacity * 100, 1) if capacity > 0 else 0,
        'products': [
            {
                'product_id': p[0],
                'quantity': p[1],
                'added_date': p[2],
                'notes': p[3]
            }
            for p in products
        ],
        'source': 'warehouse_locations'
    }


def assign_product_to_location(product_id: int, location_code: str, 
                               quantity: int = 1, notes: str = None) -> bool:
    """
    Przypisuje produkt do lokalizacji w magazynie
    
    Args:
        product_id: ID produktu
        location_code: Kod lokalizacji (np. "A2-3")
        quantity: Ilość
        notes: Notatki
    
    Returns:
        True jeśli sukces, False jeśli błąd
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # Sprawdź czy lokalizacja istnieje
        loc = cursor.execute(
            'SELECT capacity FROM warehouse_locations WHERE location_code = ?',
            (location_code,)
        ).fetchone()
        
        if not loc:
            print(f"[ERR] Location {location_code} not found")
            return False
        
        capacity = loc[0]
        
        # Sprawdź aktualne zapełnienie
        current = cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0)
            FROM product_locations
            WHERE location_code = ?
        ''', (location_code,)).fetchone()[0]
        
        if current + quantity > capacity:
            print(f"[ERR] Location {location_code} would exceed capacity ({current + quantity} > {capacity})")
            return False
        
        # Dodaj przypisanie
        cursor.execute('''
            INSERT INTO product_locations 
            (product_id, location_code, quantity, notes)
            VALUES (?, ?, ?, ?)
        ''', (product_id, location_code, quantity, notes))
        
        # Update timestamp
        cursor.execute('''
            UPDATE warehouse_locations 
            SET updated_at = CURRENT_TIMESTAMP
            WHERE location_code = ?
        ''', (location_code,))
        
        conn.commit()
        print(f"[OK] Product {product_id} assigned to {location_code} (qty: {quantity})")
        return True
        
    except Exception as e:
        print(f"[ERR] Error assigning product: {e}")
        conn.rollback()
        return False


def remove_product_from_location(product_id: int, location_code: str = None) -> bool:
    """
    Usuwa produkt z lokalizacji
    
    Args:
        product_id: ID produktu
        location_code: Opcjonalnie konkretna lokalizacja (jeśli None - usuwa ze wszystkich)
    
    Returns:
        True jeśli sukces
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        if location_code:
            cursor.execute('''
                DELETE FROM product_locations
                WHERE product_id = ? AND location_code = ?
            ''', (product_id, location_code))
        else:
            cursor.execute('''
                DELETE FROM product_locations
                WHERE product_id = ?
            ''', (product_id,))
        
        conn.commit()
        return True
        
    except Exception as e:
        print(f"[ERR] Error removing product: {e}")
        conn.rollback()
        return False


def find_empty_locations(min_capacity: int = 1) -> List[str]:
    """
    Znajduje puste lokalizacje z min. pojemnością
    
    Args:
        min_capacity: Minimalna pojemność
    
    Returns:
        Lista kodów lokalizacji
    """
    conn = get_db()
    cursor = conn.cursor()
    
    rows = cursor.execute('''
        SELECT wl.location_code
        FROM warehouse_locations wl
        LEFT JOIN product_locations pl ON wl.location_code = pl.location_code
        WHERE wl.capacity >= ?
        GROUP BY wl.location_code
        HAVING COALESCE(SUM(pl.quantity), 0) = 0
        ORDER BY wl.shelf, wl.level, wl.section
    ''', (min_capacity,)).fetchall()
    
    return [row[0] for row in rows]


def get_heatmap_data() -> Dict:
    """
    Generuje dane dla 3D heatmapy
    CZYTA LAYOUT Z: warehouse_layout.json (z editora)
    FALLBACK: Jeśli nie ma JSON → używa WAREHOUSE_CONFIG
    
    Returns:
        Dict z danymi dla wizualizacji
    """
    import json
    import os
    
    # Spróbuj załadować custom layout z editora
    layout_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'warehouse_layout.json')
    custom_layout = None
    
    if os.path.exists(layout_file):
        try:
            with open(layout_file, 'r') as f:
                custom_layout = json.load(f)
            print(f"[OK] Loaded custom layout from {layout_file}")
        except Exception as e:
            print(f"[WARN] Error loading custom layout: {e}")
    
    # Jeśli mamy custom layout - użyj go!
    if custom_layout and 'shelves' in custom_layout:
        return get_heatmap_data_from_custom_layout(custom_layout)
    
    # Fallback: stary system
    locations = get_all_locations()
    
    # Grupuj po regałach
    shelves_data = {}
    for loc in locations:
        if loc.shelf not in shelves_data:
            shelves_data[loc.shelf] = []
        
        shelves_data[loc.shelf].append({
            'code': loc.code,
            'level': loc.level,
            'items': loc.items_count,
            'capacity': loc.capacity,
            'fill_percentage': round(loc.fill_percentage * 100, 1),
            'fill_status': loc.fill_status,
            'color': loc.color,
            'height': loc.height,
            'max_levels': loc.max_levels
        })
    
    # Statystyki
    total_capacity = sum(loc.capacity for loc in locations)
    total_items = sum(loc.items_count for loc in locations)
    empty_count = sum(1 for loc in locations if loc.items_count == 0)
    full_count = sum(1 for loc in locations if loc.fill_percentage >= 0.75)
    
    # DEBUG
    print(f"[SEAR] WAREHOUSE STATS DEBUG:")
    print(f"   Total locations: {len(locations)}")
    print(f"   Total capacity: {total_capacity}")
    print(f"   Total items: {total_items}")
    print(f"   Empty locations: {empty_count}")
    print(f"   Full locations: {full_count}")
    
    # Pokaż kilka lokalizacji z items > 0
    with_items = [loc for loc in locations if loc.items_count > 0]
    if with_items:
        print(f"   [WARN] LOCATIONS WITH ITEMS:")
        for loc in with_items[:10]:
            print(f"      {loc.code}: {loc.items_count} items")
    
    return {
        'shelves': shelves_data,
        'config': WAREHOUSE_CONFIG,
        'layout': {
            'type': 'u_shape_dual',
            'section_1': WAREHOUSE_CONFIG['section_1'],
            'section_2': WAREHOUSE_CONFIG['section_2'],
            'total_shelves': WAREHOUSE_CONFIG['total_shelves']
        },
        'stats': {
            'total_locations': len(locations),
            'total_capacity': total_capacity,
            'total_items': total_items,
            'fill_percentage': round(total_items / total_capacity * 100, 1) if total_capacity > 0 else 0,
            'empty_locations': empty_count,
            'full_locations': full_count,
            'shelves_count': len(WAREHOUSE_CONFIG['shelves'])
        }
    }


def get_heatmap_data_from_custom_layout(custom_layout: Dict) -> Dict:
    """
    Generuje dane heatmapy z custom layoutu (z editora)
    
    Args:
        custom_layout: Dict z warehouse_layout.json
        
    Returns:
        Dict z danymi dla wizualizacji
    """
    conn = get_db()
    cursor = conn.cursor()
    
    shelves_data = {}
    all_locations = []
    
    # Check if magazynier exists
    try:
        cursor.execute("SELECT COUNT(*) FROM produkty")
        has_magazynier = True
    except:
        has_magazynier = False
    
    # Dla każdego regału z custom layoutu
    for shelf_config in custom_layout['shelves']:
        letter = shelf_config['letter']
        levels = shelf_config.get('levels', 6)
        shelf_height = shelf_config.get('shelfHeight', 225)
        capacity = 50  # Default
        
        shelves_data[letter] = []
        
        # Generuj półki dla tego regału
        for level in range(1, levels + 1):
            location_code = f"{letter}{level}"
            
            # Pobierz produkty z magazyniera
            items_count = 0
            if has_magazynier:
                try:
                    patterns = [
                        f"{location_code}%",
                        f"{location_code}-%",
                        f"% {location_code} %",
                        f"% {location_code}",
                    ]
                    
                    for pattern in patterns:
                        count = cursor.execute('''
                            SELECT COALESCE(SUM(ilosc), 0)
                            FROM produkty
                            WHERE UPPER(lokalizacja) LIKE ?
                        ''', (pattern,)).fetchone()[0]
                        items_count = max(items_count, count)
                except:
                    items_count = 0
            
            # Oblicz fill percentage
            fill_pct = items_count / capacity if capacity > 0 else 0
            
            # Color based on fill
            if fill_pct == 0:
                color = '#4ade80'  # green
                status = 'empty'
            elif fill_pct < 0.25:
                color = '#60a5fa'  # blue
                status = 'low'
            elif fill_pct < 0.5:
                color = '#fbbf24'  # yellow
                status = 'medium'
            elif fill_pct < 0.75:
                color = '#fb923c'  # orange
                status = 'high'
            else:
                color = '#ef4444'  # red
                status = 'full'
            
            shelf_data = {
                'code': location_code,
                'level': level,
                'items': items_count,
                'capacity': capacity,
                'fill_percentage': round(fill_pct * 100, 1),
                'fill_status': status,
                'color': color,
                'height': shelf_height,
                'max_levels': levels,
                'x': shelf_config.get('x', 0),
                'y': shelf_config.get('y', 0),
                'width': shelf_config.get('width', 120),
                'shelf_width': shelf_config.get('shelfWidth', 90),
                'depth': shelf_config.get('depth', 40),
                'rotation': shelf_config.get('rotation', 0),
                'editor_color': shelf_config.get('color', '#667eea')
            }
            
            shelves_data[letter].append(shelf_data)
            all_locations.append(shelf_data)
    
    # Stats
    total_capacity = sum(loc['capacity'] for loc in all_locations)
    total_items = sum(loc['items'] for loc in all_locations)
    empty_count = sum(1 for loc in all_locations if loc['items'] == 0)
    full_count = sum(1 for loc in all_locations if loc['fill_percentage'] >= 75)
    
    return {
        'shelves': shelves_data,
        'layout': {
            'type': 'custom',
            'from_editor': True,
            'shelves_config': custom_layout['shelves']
        },
        'stats': {
            'total_locations': len(all_locations),
            'total_capacity': total_capacity,
            'total_items': total_items,
            'fill_percentage': round(total_items / total_capacity * 100, 1) if total_capacity > 0 else 0,
            'empty_locations': empty_count,
            'full_locations': full_count,
            'shelves_count': len(shelves_data)
        }
    }


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def parse_location_code(code: str) -> Optional[Tuple[str, int]]:
    """
    Parsuje kod lokalizacji (całej półki)
    
    Args:
        code: Kod lokalizacji (np. "A2", "B5")
    
    Returns:
        Tuple (shelf, level) lub None jeśli błąd
    """
    import re
    match = re.match(r'^([A-E]\d?)(\d+)$', code.upper())
    if match:
        return match.group(1), int(match.group(2))
    return None


def validate_location_code(code: str) -> bool:
    """Sprawdza czy kod lokalizacji jest poprawny"""
    parsed = parse_location_code(code)
    if not parsed:
        return False
    
    shelf, level = parsed
    return (
        shelf in WAREHOUSE_CONFIG['shelves'] and
        1 <= level <= WAREHOUSE_CONFIG['levels']
    )


def get_location_stats() -> Dict:
    """Zwraca statystyki magazynu"""
    locations = get_all_locations()
    
    stats = {
        'by_shelf': {},
        'by_level': {},
        'by_status': {
            'empty': 0,
            'low': 0,
            'medium': 0,
            'high': 0,
            'full': 0
        }
    }
    
    for loc in locations:
        # By shelf
        if loc.shelf not in stats['by_shelf']:
            stats['by_shelf'][loc.shelf] = {
                'items': 0,
                'capacity': 0,
                'locations': 0
            }
        stats['by_shelf'][loc.shelf]['items'] += loc.items_count
        stats['by_shelf'][loc.shelf]['capacity'] += loc.capacity
        stats['by_shelf'][loc.shelf]['locations'] += 1
        
        # By level
        if loc.level not in stats['by_level']:
            stats['by_level'][loc.level] = {
                'items': 0,
                'capacity': 0,
                'locations': 0
            }
        stats['by_level'][loc.level]['items'] += loc.items_count
        stats['by_level'][loc.level]['capacity'] += loc.capacity
        stats['by_level'][loc.level]['locations'] += 1
        
        # By status
        stats['by_status'][loc.fill_status] += 1
    
    # Calculate percentages
    for shelf_data in stats['by_shelf'].values():
        if shelf_data['capacity'] > 0:
            shelf_data['fill_percentage'] = round(
                shelf_data['items'] / shelf_data['capacity'] * 100, 1
            )
    
    for level_data in stats['by_level'].values():
        if level_data['capacity'] > 0:
            level_data['fill_percentage'] = round(
                level_data['items'] / level_data['capacity'] * 100, 1
            )
    
    return stats
