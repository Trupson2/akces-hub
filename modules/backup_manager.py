"""
Auto-backup systemu bazy danych
Wykonuje backup co godzinę i pozwala na przywracanie
"""

import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
import threading
import time

BACKUP_DIR = Path(__file__).parent.parent / 'backups'
DB_PATH = Path(__file__).parent.parent / 'akces_hub.db'  # ← NOWA BAZA!
BACKUP_INTERVAL = 3600  # 1 godzina w sekundach
MAX_BACKUPS = 24  # Trzymaj 24 ostatnie backupy (1 dzień)

# Google Drive sync via rclone
# Konfiguracja: rclone config → "gdrive" → Google Drive
GDRIVE_REMOTE = 'akces-cloud'  # nazwa remote w rclone (unified with backup_cloud.py)
GDRIVE_BACKUP_FOLDER = 'akces-hub-backups/backups'  # folder na Google Drive
_rclone_available = None  # cache: None=unchecked, True/False=checked
_stop_event = threading.Event()

def ensure_backup_dir():
    """Upewnij się że folder backups istnieje"""
    BACKUP_DIR.mkdir(exist_ok=True)
    return BACKUP_DIR

def create_backup():
    """Tworzy backup bazy danych"""
    try:
        ensure_backup_dir()
        
        if not DB_PATH.exists():
            print(f"[ERR] Baza nie istnieje: {DB_PATH}")
            return None
        
        # Nazwa backupu z timestampem
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f"akces_hub_backup_{timestamp}.db"
        backup_path = BACKUP_DIR / backup_name
        
        # Wykonaj backup używając SQLite backup API (bezpieczniejsze niż shutil.copy)
        source_conn = sqlite3.connect(str(DB_PATH))
        backup_conn = sqlite3.connect(str(backup_path))
        
        with backup_conn:
            source_conn.backup(backup_conn)
        
        source_conn.close()
        backup_conn.close()
        
        print(f"[OK] Backup utworzony: {backup_name}")

        # Usuń stare backupy
        cleanup_old_backups()

        # Sync do Google Drive (w tle, nie blokuje)
        threading.Thread(target=sync_to_gdrive, args=(backup_path,), daemon=True).start()

        return backup_path
        
    except Exception as e:
        print(f"[ERR] Błąd tworzenia backupu: {e}")
        return None

def _check_rclone():
    """Sprawdza dostępność rclone (wynik cache'owany)"""
    global _rclone_available
    if _rclone_available is not None:
        return _rclone_available
    try:
        result = subprocess.run(
            ['rclone', 'listremotes'], capture_output=True, text=True, timeout=10
        )
        _rclone_available = (result.returncode == 0 and f'{GDRIVE_REMOTE}:' in result.stdout)
        if not _rclone_available:
            print(f"[WARN] rclone remote '{GDRIVE_REMOTE}' nie skonfigurowany. Uruchom: rclone config")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _rclone_available = False
        print("[WARN] rclone nie znaleziony lub timeout")
    return _rclone_available


def sync_to_gdrive(backup_path=None):
    """Synchronizuje backup na Google Drive przez rclone"""
    try:
        if not _check_rclone():
            return False

        dest = f'{GDRIVE_REMOTE}:{GDRIVE_BACKUP_FOLDER}'
        if backup_path:
            cmd = ['rclone', 'copy', str(backup_path), dest]
        else:
            cmd = ['rclone', 'sync', str(BACKUP_DIR), dest, '--max-age', '24h']

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode == 0:
            print(f"[CLOU] Backup zsynchronizowany z Google Drive ({GDRIVE_BACKUP_FOLDER}/)")
            return True
        else:
            print(f"[WARN] rclone error: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        print("[WARN] rclone timeout (>5min)")
        return False
    except Exception as e:
        print(f"[WARN] Błąd sync do Google Drive: {e}")
        return False


def cleanup_old_backups():
    """Usuwa stare backupy, zostawia tylko MAX_BACKUPS najnowszych"""
    try:
        # Szukaj zarówno nowych jak i starych backupów
        all_backups = list(BACKUP_DIR.glob('akces_hub_backup_*.db')) + list(BACKUP_DIR.glob('magazyn_backup_*.db'))
        backups = sorted(all_backups, key=lambda x: x.stat().st_mtime, reverse=True)
        
        # Usuń backupy powyżej limitu
        for old_backup in backups[MAX_BACKUPS:]:
            old_backup.unlink()
            print(f"[DELE]  Usunięto stary backup: {old_backup.name}")
            
    except Exception as e:
        print(f"[WARN]  Błąd czyszczenia starych backupów: {e}")

def get_backups():
    """Pobiera listę dostępnych backupów (zarówno nowych jak i starych)"""
    ensure_backup_dir()
    backups = []
    
    # Szukaj zarówno nowych (akces_hub_backup_*) jak i starych (magazyn_backup_*) oraz wgranych (uploaded_*) backupów
    all_backup_files = (list(BACKUP_DIR.glob('akces_hub_backup_*.db')) + 
                        list(BACKUP_DIR.glob('magazyn_backup_*.db')) +
                        list(BACKUP_DIR.glob('uploaded_*.db')))
    
    for backup_file in sorted(all_backup_files, key=lambda x: x.stat().st_mtime, reverse=True):
        stat = backup_file.stat()
        backups.append({
            'filename': backup_file.name,
            'path': backup_file,
            'size': stat.st_size,
            'size_mb': stat.st_size / (1024 * 1024),
            'created': datetime.fromtimestamp(stat.st_mtime),
            'created_str': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        })
    
    return backups

def restore_backup(backup_filename):
    """Przywraca bazę danych z backupu używając SQLite backup API"""
    import gc
    
    try:
        backup_path = BACKUP_DIR / backup_filename
        
        if not backup_path.exists():
            return False, f"Backup nie istnieje: {backup_filename}"
        
        # Sprawdź czy backup jest poprawny przed przywracaniem
        try:
            test_conn = sqlite3.connect(str(backup_path))
            test_cursor = test_conn.cursor()
            test_cursor.execute("SELECT COUNT(*) FROM produkty")
            count = test_cursor.fetchone()[0]
            test_conn.close()
            print(f"[OK] Backup zweryfikowany: {count} produktów")
        except Exception as e:
            return False, f"Backup jest uszkodzony: {e}"
        
        # Najpierw zrób backup obecnej bazy (bezpieczeństwo)
        safety_backup_name = f"akces_hub_pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        safety_backup_path = BACKUP_DIR / safety_backup_name
        
        if DB_PATH.exists():
            try:
                # Użyj SQLite backup API dla bezpieczeństwa
                source_conn = sqlite3.connect(str(DB_PATH))
                safety_conn = sqlite3.connect(str(safety_backup_path))
                source_conn.backup(safety_conn)
                safety_conn.close()
                source_conn.close()
                print(f"[SAVE] Bezpieczeństwo: stworzono backup przed przywracaniem: {safety_backup_name}")
            except Exception as e:
                print(f"[WARN] Nie udało się stworzyć safety backup: {e}")
        
        # Wymuś garbage collection żeby zamknąć ewentualne połączenia
        gc.collect()
        
        # Przywróć z backupu używając SQLite backup API
        try:
            backup_conn = sqlite3.connect(str(backup_path))
            dest_conn = sqlite3.connect(str(DB_PATH))
            backup_conn.backup(dest_conn)
            dest_conn.close()
            backup_conn.close()
            
            print(f"[OK] Przywrócono bazę z backupu: {backup_filename}")
            return True, f"Przywrócono bazę z backupu: {backup_filename}. Odśwież stronę."
        except Exception as e:
            # Fallback do shutil.copy2 jeśli backup API nie działa
            print(f"[WARN] SQLite backup API failed, próbuję shutil.copy2: {e}")
            shutil.copy2(backup_path, DB_PATH)
            print(f"[OK] Przywrócono bazę z backupu (fallback): {backup_filename}")
            return True, f"Przywrócono bazę z backupu: {backup_filename}. Odśwież stronę."
        
    except PermissionError as e:
        error_msg = f"Brak uprawnień do pliku bazy danych. Zrestartuj aplikację i spróbuj ponownie."
        print(f"[ERR] {error_msg}: {e}")
        return False, error_msg
    except Exception as e:
        error_msg = f"Błąd przywracania: {str(e)}"
        print(f"[ERR] {error_msg}")
        return False, error_msg

def verify_backup(backup_filename):
    """Sprawdza integralność backupu"""
    try:
        backup_path = BACKUP_DIR / backup_filename
        
        if not backup_path.exists():
            return False, "Backup nie istnieje"
        
        # Spróbuj otworzyć i wykonać prosty query
        conn = sqlite3.connect(str(backup_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM produkty")
        count = cursor.fetchone()[0]
        conn.close()
        
        return True, f"OK - {count} produktów"
        
    except Exception as e:
        return False, f"Błąd: {str(e)}"

# ============================================================
# AUTO-BACKUP DAEMON
# ============================================================

_backup_daemon_running = False
_backup_thread = None

def start_backup_daemon():
    """Uruchamia daemon wykonujący automatyczne backupy"""
    global _backup_daemon_running, _backup_thread
    
    if _backup_daemon_running:
        print("[WARN]  Backup daemon już działa")
        return
    
    _stop_event.clear()
    _backup_daemon_running = True
    _backup_thread = threading.Thread(target=_backup_loop, daemon=True)
    _backup_thread.start()
    print(f"[ROCK] Backup daemon uruchomiony (backup co {BACKUP_INTERVAL//60} minut)")

def stop_backup_daemon():
    """Zatrzymuje daemon backupu"""
    global _backup_daemon_running
    _backup_daemon_running = False
    _stop_event.set()
    print("<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">do_not_disturb</span> Backup daemon zatrzymany")

def _backup_loop():
    """Główna pętla backup daemon"""
    # Wykonaj backup od razu przy starcie
    print("[SYNC] Wykonuję pierwszy backup...")
    create_backup()
    
    # Eksport CSV do folderu cloud_exports (co 6 godzin)
    last_cloud_export = 0
    CLOUD_EXPORT_INTERVAL = 6 * 3600  # 6 godzin
    
    while _backup_daemon_running:
        try:
            # Czekaj godzinę (albo do przerwania przez stop_event)
            if _stop_event.wait(timeout=BACKUP_INTERVAL):
                break

            if _backup_daemon_running:
                print(f"⏰ Czas na automatyczny backup...")
                create_backup()
                
                # Co 6 godzin - eksport do cloud_exports
                last_cloud_export += BACKUP_INTERVAL
                if last_cloud_export >= CLOUD_EXPORT_INTERVAL:
                    last_cloud_export = 0
                    try:
                        from .cloud_export import scheduled_backup
                        print("[CLOU] Eksport do cloud_exports...")
                        scheduled_backup()
                    except Exception as e:
                        print(f"[WARN] Cloud export error: {e}")
                
        except Exception as e:
            print(f"[ERR] Błąd w backup daemon: {e}")
            time.sleep(60)  # Odczekaj minutę po błędzie

# ============================================================
# FLASK BLUEPRINT (opcjonalnie)
# ============================================================

try:
    from flask import Blueprint, jsonify, request
    
    backup_bp = Blueprint('backup', __name__)
    
    @backup_bp.route('/backup/create', methods=['POST'])
    def api_create_backup():
        """API endpoint do tworzenia backupu"""
        backup_path = create_backup()
        if backup_path:
            return jsonify({'success': True, 'backup': backup_path.name})
        return jsonify({'success': False, 'error': 'Nie udało się utworzyć backupu'}), 500
    
    @backup_bp.route('/backup/list')
    def api_list_backups():
        """API endpoint do listowania backupów"""
        backups = get_backups()
        return jsonify({
            'success': True,
            'backups': [
                {
                    'filename': b['filename'],
                    'size_mb': round(b['size_mb'], 2),
                    'created': b['created_str']
                }
                for b in backups
            ]
        })
    
    @backup_bp.route('/backup/restore', methods=['POST'])
    def api_restore_backup():
        """API endpoint do przywracania backupu"""
        import gc
        
        data = request.get_json()
        filename = data.get('filename')
        
        if not filename:
            return jsonify({'success': False, 'error': 'Brak nazwy pliku'}), 400
        
        # Zamknij CAŁY connection pool przed przywracaniem
        try:
            from .database import close_connection_pool
            close_connection_pool()
        except Exception as e:
            print(f"[WARN] Nie udało się zamknąć connection pool: {e}")

        # Wymuś garbage collection
        gc.collect()

        success, message = restore_backup(filename)

        # Po przywracaniu wyczyść pool ponownie - nowe połączenia otworzą się na nowej bazie
        try:
            from .database import close_connection_pool
            close_connection_pool()
        except Exception as e:
            print(f"[WARN] Nie udało się zamknąć connection pool po przywracaniu: {e}")
        
        return jsonify({'success': success, 'message': message})
    
    @backup_bp.route('/backup/verify/<filename>')
    def api_verify_backup(filename):
        """API endpoint do weryfikacji backupu"""
        success, message = verify_backup(filename)
        return jsonify({'success': success, 'message': message})

    @backup_bp.route('/backup/sync-gdrive', methods=['POST'])
    def api_sync_gdrive():
        """Ręczna synchronizacja backupów z Google Drive"""
        success = sync_to_gdrive()
        if success:
            return jsonify({'success': True, 'message': 'Zsynchronizowano z Google Drive'})
        return jsonify({'success': False, 'error': 'Nie udało się zsynchronizować. Sprawdź konfigurację rclone.'}), 500
    
except ImportError:
    # Flask niedostępny, skipujemy API endpoints
    backup_bp = None

if __name__ == '__main__':
    # Test modułu
    print("[SCIE] Test modułu backup...")
    print(f"[FOLD] Folder backupów: {BACKUP_DIR}")
    print(f"[SAVE] Baza danych: {DB_PATH}")
    
    # Utwórz backup
    backup = create_backup()
    if backup:
        print(f"[OK] Backup utworzony: {backup}")
    
    # Pokaż listę backupów
    backups = get_backups()
    print(f"\n[ASSI] Dostępne backupy ({len(backups)}):")
    for b in backups:
        status_ok, status_msg = verify_backup(b['filename'])
        status_icon = "<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#22c55e">check_circle</span>" if status_ok else "<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#ef4444">cancel</span>"
        print(f"  {status_icon} {b['filename']} - {b['size_mb']:.2f} MB - {b['created_str']} - {status_msg}")
