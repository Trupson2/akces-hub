"""
Cloud Export Module
Eksportuje palety i produkty do Google Sheets lub lokalnego CSV/Excel
"""

import os
import json
from datetime import datetime
from pathlib import Path
from .utils import sanitize_csv_cell as _sc

# Folder na eksporty - najpierw sprawdź czy istnieje Google Drive / Dropbox
# Kolejność: G:/Mój dysk, C:/Users/*/Google Drive, lokalny fallback

def _find_cloud_dir():
    import os
    # Typowe lokalizacje Google Drive Desktop na Windows
    candidates = [
        Path('G:/Mój dysk/akces_hub_exports'),
        Path('G:/My Drive/akces_hub_exports'),
        Path(os.path.expanduser('~/Google Drive/akces_hub_exports')),
        Path(os.path.expanduser('~/OneDrive/akces_hub_exports')),
    ]
    for c in candidates:
        try:
            # Sprawdź czy parent istnieje (czyli Drive jest zamontowany)
            if c.parent.exists():
                return c
        except:
            pass
    # Fallback do lokalnego folderu
    return Path(__file__).parent.parent / 'cloud_exports'

EXPORT_DIR = _find_cloud_dir()

def ensure_export_dir():
    """Upewnij się że folder eksportów istnieje"""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    return EXPORT_DIR

# Log przy imporcie żeby wiedzieć gdzie trafiają pliki
try:
    _test = ensure_export_dir()
    print(f"[CLOU]  Cloud exports → {EXPORT_DIR}")
except Exception as _e:
    print(f"[WARN]  Cloud exports folder error: {_e}")


def export_palety_csv(conn=None):
    """Eksportuje palety do CSV"""
    try:
        ensure_export_dir()
        
        if conn is None:
            from .database import get_db
            conn = get_db()
            close_conn = True
        else:
            close_conn = False
        
        # Pobierz palety ze statystykami
        palety = conn.execute('''
            SELECT p.*, 
                   (SELECT COUNT(*) FROM produkty WHERE paleta_id = p.id) as produktow,
                   (SELECT COALESCE(SUM(ilosc), 0) FROM produkty WHERE paleta_id = p.id) as sztuk,
                   (SELECT COALESCE(SUM(cena_allegro * ilosc), 0) FROM produkty WHERE paleta_id = p.id) as wartosc_detalu,
                   (SELECT COALESCE(SUM(CASE WHEN status = 'sprzedany' THEN ilosc ELSE 0 END), 0) FROM produkty WHERE paleta_id = p.id) as sprzedano_szt,
                   (SELECT COALESCE(SUM(CASE WHEN status = 'sprzedany' THEN cena_allegro * ilosc ELSE 0 END), 0) FROM produkty WHERE paleta_id = p.id) as sprzedano_wartosc
            FROM palety p
            ORDER BY data_zakupu DESC
        ''').fetchall()
        
        # Zapisz do CSV
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'palety_export_{timestamp}.csv'
        filepath = EXPORT_DIR / filename
        
        import csv
        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f, delimiter=';')
            
            # Nagłówki
            writer.writerow([
                'ID', 'Nazwa', 'Dostawca', 'Cena brutto', 'Cena netto', 'Data zakupu',
                'Produktów', 'Sztuk', 'Wartość detalu', 'Sprzedano szt', 
                'Sprzedano wartość', 'ROI %', 'Notatki'
            ])
            
            # Dane
            for p in palety:
                cena_brutto = p['cena_zakupu'] or 0
                cena_netto = round(cena_brutto / 1.23, 2) if cena_brutto > 0 else 0
                sprzedano = p['sprzedano_wartosc'] or 0
                roi = ((sprzedano - cena_brutto) / cena_brutto * 100) if cena_brutto > 0 else 0
                
                writer.writerow([
                    p['id'],
                    _sc(p['nazwa'] or ''),
                    _sc(p['dostawca'] or ''),
                    f"{cena_brutto:.2f}",
                    f"{cena_netto:.2f}",
                    p['data_zakupu'] or '',
                    p['produktow'] or 0,
                    p['sztuk'] or 0,
                    f"{p['wartosc_detalu'] or 0:.2f}",
                    p['sprzedano_szt'] or 0,
                    f"{sprzedano:.2f}",
                    f"{roi:.1f}",
                    _sc(p['notatki'] or '')
                ])
        
        print(f"[OK] Eksportowano palety do: {filepath}")
        return str(filepath)
        
    except Exception as e:
        print(f"[ERR] Błąd eksportu palet: {e}")
        return None


def export_produkty_csv(paleta_id=None, conn=None):
    """Eksportuje produkty do CSV (opcjonalnie tylko z jednej palety)"""
    try:
        ensure_export_dir()
        
        if conn is None:
            from .database import get_db
            conn = get_db()
            close_conn = True
        else:
            close_conn = False
        
        # Pobierz produkty
        if paleta_id:
            produkty = conn.execute('''
                SELECT p.*, pal.nazwa as paleta_nazwa 
                FROM produkty p
                LEFT JOIN palety pal ON p.paleta_id = pal.id
                WHERE p.paleta_id = ?
                ORDER BY p.id DESC
            ''', (paleta_id,)).fetchall()
            suffix = f'_paleta{paleta_id}'
        else:
            produkty = conn.execute('''
                SELECT p.*, pal.nazwa as paleta_nazwa 
                FROM produkty p
                LEFT JOIN palety pal ON p.paleta_id = pal.id
                ORDER BY p.id DESC
            ''').fetchall()
            suffix = ''
        
        # Zapisz do CSV
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'produkty_export{suffix}_{timestamp}.csv'
        filepath = EXPORT_DIR / filename
        
        import csv
        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f, delimiter=';')
            
            # Nagłówki
            writer.writerow([
                'ID', 'EAN', 'ASIN', 'Nazwa', 'Ilość', 
                'Cena brutto', 'Cena netto', 'Cena Allegro', 
                'Status', 'Dostawca', 'Paleta', 'Lokalizacja'
            ])
            
            # Dane
            for p in produkty:
                cena_brutto = p['cena_brutto'] or 0
                cena_netto = p['cena_netto'] if p['cena_netto'] else round(cena_brutto / 1.23, 2)
                
                writer.writerow([
                    p['id'],
                    _sc(p['ean'] or ''),
                    _sc(p['asin'] or ''),
                    _sc(p['nazwa'] or ''),
                    p['ilosc'] or 0,
                    f"{cena_brutto:.2f}",
                    f"{cena_netto:.2f}",
                    f"{p['cena_allegro'] or 0:.2f}",
                    p['status'] or 'magazyn',
                    _sc(p['dostawca'] or ''),
                    _sc(p['paleta_nazwa'] or ''),
                    _sc(p['lokalizacja'] or '')
                ])
        
        print(f"[OK] Eksportowano produkty do: {filepath}")
        return str(filepath)
        
    except Exception as e:
        print(f"[ERR] Błąd eksportu produktów: {e}")
        return None


def export_to_google_sheets(spreadsheet_id, credentials_path):
    """
    Eksportuje dane do Google Sheets.
    Wymaga:
    - spreadsheet_id: ID arkusza Google Sheets
    - credentials_path: ścieżka do pliku credentials.json (Service Account)
    
    Instalacja: pip install gspread oauth2client
    """
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
        
        # Autoryzacja
        scope = ['https://spreadsheets.google.com/feeds',
                 'https://www.googleapis.com/auth/drive']
        
        creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_path, scope)
        client = gspread.authorize(creds)
        
        # Otwórz arkusz
        sheet = client.open_by_key(spreadsheet_id)
        
        from .database import get_db
        conn = get_db()
        
        # === ARKUSZ 1: PALETY ===
        try:
            ws_palety = sheet.worksheet('Palety')
        except:
            ws_palety = sheet.add_worksheet(title='Palety', rows=100, cols=12)
        
        palety = conn.execute('''
            SELECT p.*, 
                   (SELECT COUNT(*) FROM produkty WHERE paleta_id = p.id) as produktow,
                   (SELECT COALESCE(SUM(ilosc), 0) FROM produkty WHERE paleta_id = p.id) as sztuk,
                   (SELECT COALESCE(SUM(cena_allegro * ilosc), 0) FROM produkty WHERE paleta_id = p.id) as wartosc_detalu,
                   (SELECT COALESCE(SUM(CASE WHEN status = 'sprzedany' THEN cena_allegro * ilosc ELSE 0 END), 0) FROM produkty WHERE paleta_id = p.id) as sprzedano
            FROM palety p ORDER BY data_zakupu DESC
        ''').fetchall()
        
        # Wyczyść i wpisz nagłówki
        ws_palety.clear()
        ws_palety.append_row(['ID', 'Nazwa', 'Dostawca', 'Cena brutto', 'Cena netto', 'Data', 'Produktów', 'Sztuk', 'Wartość', 'Sprzedano', 'ROI %'])
        
        # Wpisz dane
        for p in palety:
            cena_brutto = p['cena_zakupu'] or 0
            cena_netto = round(cena_brutto / 1.23, 2) if cena_brutto > 0 else 0
            sprzedano = p['sprzedano'] or 0
            roi = ((sprzedano - cena_brutto) / cena_brutto * 100) if cena_brutto > 0 else 0
            
            ws_palety.append_row([
                p['id'], p['nazwa'] or '', p['dostawca'] or '', cena_brutto, cena_netto,
                p['data_zakupu'] or '', p['produktow'] or 0, p['sztuk'] or 0,
                p['wartosc_detalu'] or 0, sprzedano, round(roi, 1)
            ])
        
        # === ARKUSZ 2: PRODUKTY ===
        try:
            ws_produkty = sheet.worksheet('Produkty')
        except:
            ws_produkty = sheet.add_worksheet(title='Produkty', rows=1000, cols=12)
        
        produkty = conn.execute('''
            SELECT p.*, pal.nazwa as paleta_nazwa 
            FROM produkty p
            LEFT JOIN palety pal ON p.paleta_id = pal.id
            ORDER BY p.id DESC LIMIT 500
        ''').fetchall()
        
        ws_produkty.clear()
        ws_produkty.append_row(['ID', 'EAN', 'ASIN', 'Nazwa', 'Ilość', 'Cena brutto', 'Cena netto', 'Cena Allegro', 'Status', 'Dostawca', 'Paleta'])
        
        for p in produkty:
            cena_brutto = p['cena_brutto'] or 0
            cena_netto = p['cena_netto'] if p['cena_netto'] else round(cena_brutto / 1.23, 2)
            
            ws_produkty.append_row([
                p['id'], p['ean'] or '', p['asin'] or '', p['nazwa'] or '',
                p['ilosc'] or 0, cena_brutto, cena_netto, p['cena_allegro'] or 0,
                p['status'] or 'magazyn', p['dostawca'] or '', p['paleta_nazwa'] or ''
            ])

        print(f"[OK] Eksportowano do Google Sheets: {spreadsheet_id}")
        return True
        
    except ImportError:
        print("[ERR] Brak biblioteki gspread. Zainstaluj: pip install gspread oauth2client")
        return False
    except Exception as e:
        print(f"[ERR] Błąd eksportu do Google Sheets: {e}")
        return False


def scheduled_backup():
    """
    Wykonuje zaplanowany backup (do wywołania przez scheduler).
    Tworzy CSV w folderze cloud_exports który można zsynchronizować z chmurą.
    """
    print(f"[SYNC] [{datetime.now().strftime('%Y-%m-%d %H:%M')}] Rozpoczynam zaplanowany backup...")
    
    # Eksportuj do CSV
    palety_file = export_palety_csv()
    produkty_file = export_produkty_csv()
    
    # Usuń stare backupy (zostaw ostatnie 7)
    cleanup_old_exports(keep_last=7)
    
    if palety_file and produkty_file:
        print(f"[OK] Backup zakończony: {EXPORT_DIR}")
        return True
    return False


def cleanup_old_exports(keep_last=7):
    """Usuwa stare pliki eksportu, zostawiając ostatnie N"""
    try:
        ensure_export_dir()
        
        # Znajdź wszystkie pliki CSV
        files = sorted(EXPORT_DIR.glob('*.csv'), key=lambda x: x.stat().st_mtime, reverse=True)
        
        # Usuń stare (zostaw keep_last najnowszych każdego typu)
        palety_files = [f for f in files if 'palety_export' in f.name]
        produkty_files = [f for f in files if 'produkty_export' in f.name]
        
        for old_file in palety_files[keep_last:]:
            old_file.unlink()
            print(f"[DELE] Usunięto stary backup: {old_file.name}")
        
        for old_file in produkty_files[keep_last:]:
            old_file.unlink()
            print(f"[DELE] Usunięto stary backup: {old_file.name}")
            
    except Exception as e:
        print(f"[WARN] Błąd czyszczenia: {e}")


def get_export_files():
    """Zwraca listę plików eksportu"""
    ensure_export_dir()
    files = []
    for f in sorted(EXPORT_DIR.glob('*.csv'), key=lambda x: x.stat().st_mtime, reverse=True):
        files.append({
            'name': f.name,
            'path': str(f),
            'size': f.stat().st_size,
            'size_kb': f.stat().st_size / 1024,
            'modified': datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        })
    return files


# ============================================================
# FLASK BLUEPRINT
# ============================================================

try:
    from flask import Blueprint, jsonify, request, send_file
    
    cloud_bp = Blueprint('cloud', __name__)
    
    @cloud_bp.route('/cloud/export/palety')
    def api_export_palety():
        """Eksportuje palety do CSV i zwraca plik"""
        filepath = export_palety_csv()
        if filepath:
            return send_file(filepath, as_attachment=True)
        return jsonify({'success': False, 'error': 'Błąd eksportu'}), 500
    
    @cloud_bp.route('/cloud/export/produkty')
    def api_export_produkty():
        """Eksportuje produkty do CSV i zwraca plik"""
        paleta_id = request.args.get('paleta_id')
        filepath = export_produkty_csv(paleta_id=paleta_id)
        if filepath:
            return send_file(filepath, as_attachment=True)
        return jsonify({'success': False, 'error': 'Błąd eksportu'}), 500
    
    @cloud_bp.route('/cloud/backup', methods=['POST'])
    def api_backup():
        """Tworzy backup do folderu cloud_exports"""
        success = scheduled_backup()
        return jsonify({'success': success})
    
    @cloud_bp.route('/cloud/files')
    def api_list_files():
        """Lista plików eksportu"""
        return jsonify({'success': True, 'files': get_export_files()})

except ImportError:
    cloud_bp = None


if __name__ == '__main__':
    print("[SCIE] Test modułu cloud_export...")
    print(f"[FOLD] Folder eksportów: {EXPORT_DIR}")
    
    # Test eksportu
    scheduled_backup()
    
    # Pokaż pliki
    files = get_export_files()
    print(f"\n[ASSI] Pliki eksportu ({len(files)}):")
    for f in files:
        print(f"  • {f['name']} - {f['size_kb']:.1f} KB - {f['modified']}")
