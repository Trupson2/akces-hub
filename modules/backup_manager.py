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

        # Weryfikuj integralność backupu
        verify_conn = sqlite3.connect(str(backup_path))
        result = verify_conn.execute('PRAGMA integrity_check').fetchone()
        verify_conn.close()
        if result[0] != 'ok':
            print(f"[ERR] Backup uszkodzony! integrity_check: {result[0]}")
            backup_path.unlink(missing_ok=True)
            return None

        print(f"[OK] Backup utworzony i zweryfikowany: {backup_name}")

        # Encrypt backup using Fernet (from database._get_fernet)
        try:
            from .database import _get_fernet
            fernet = _get_fernet()
            if fernet:
                with open(str(backup_path), 'rb') as f_in:
                    plaintext = f_in.read()
                encrypted = fernet.encrypt(plaintext)
                enc_path = backup_path.parent / (backup_name + '.enc')
                with open(str(enc_path), 'wb') as f_out:
                    f_out.write(encrypted)
                # Remove unencrypted backup
                backup_path.unlink()
                backup_path = enc_path
                print(f"[OK] Backup zaszyfrowany: {enc_path.name}")
            else:
                print("[WARN] Fernet unavailable — backup saved unencrypted")
        except Exception as e:
            print(f"[WARN] Encryption failed, backup saved unencrypted: {e}")

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
        # Szukaj zarówno nowych jak i starych backupów (including encrypted .enc)
        all_backups = (list(BACKUP_DIR.glob('akces_hub_backup_*.db')) +
                       list(BACKUP_DIR.glob('akces_hub_backup_*.db.enc')) +
                       list(BACKUP_DIR.glob('magazyn_backup_*.db')))
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
    # Also include encrypted (.enc) backups
    all_backup_files = (list(BACKUP_DIR.glob('akces_hub_backup_*.db')) +
                        list(BACKUP_DIR.glob('akces_hub_backup_*.db.enc')) +
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

        # Decrypt .enc backup to temporary file first
        _temp_decrypted = None
        if str(backup_path).endswith('.enc'):
            try:
                from .database import _get_fernet
                fernet = _get_fernet()
                if not fernet:
                    return False, "Cannot decrypt backup: Fernet encryption unavailable"
                with open(str(backup_path), 'rb') as f_enc:
                    encrypted_data = f_enc.read()
                decrypted_data = fernet.decrypt(encrypted_data)
                _temp_decrypted = BACKUP_DIR / f"_temp_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
                with open(str(_temp_decrypted), 'wb') as f_dec:
                    f_dec.write(decrypted_data)
                backup_path = _temp_decrypted
                print(f"[OK] Backup odszyfrowany tymczasowo: {_temp_decrypted.name}")
            except Exception as e:
                if _temp_decrypted and _temp_decrypted.exists():
                    _temp_decrypted.unlink(missing_ok=True)
                return False, f"Decryption failed: {str(e)[:100]}"

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
    finally:
        # Clean up temporary decrypted file
        if _temp_decrypted and _temp_decrypted.exists():
            _temp_decrypted.unlink(missing_ok=True)

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
    print("[DO_NOT] Backup daemon zatrzymany")

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

def _is_safe_backup_filename(name):
    """Path traversal guard — filename musi być płaska nazwa pliku w BACKUP_DIR.
    Odrzuca: puste, z '/', '\\', '..', absolute path, spoza BACKUP_DIR."""
    if not name or not isinstance(name, str):
        return False
    # Tylko sama nazwa pliku — bez ścieżek
    if name != os.path.basename(name):
        return False
    if '..' in name or name.startswith('.') or '/' in name or '\\' in name:
        return False
    # Whitelist rozszerzeń
    if not (name.endswith('.db') or name.endswith('.db.enc')):
        return False
    # Zrealizowana ścieżka musi być WEWNĄTRZ BACKUP_DIR (po resolve symlinków)
    try:
        target = (BACKUP_DIR / name).resolve()
        if os.path.commonpath([str(target), str(BACKUP_DIR.resolve())]) != str(BACKUP_DIR.resolve()):
            return False
    except Exception:
        return False
    return True


try:
    from flask import Blueprint, jsonify, request, abort, session, redirect, url_for
    from functools import wraps

    backup_bp = Blueprint('backup', __name__)

    def _require_admin_api(f):
        """Lokalna wersja require_admin — JSON responses, unika circular importu z auth.py.
        Zwraca 401 gdy brak sesji, 403 gdy nie-admin."""
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get('user_id'):
                return jsonify({'success': False, 'error': 'Wymagane logowanie'}), 401
            if (session.get('rola') or '').lower() != 'admin':
                return jsonify({'success': False, 'error': 'Brak uprawnień (wymaga admin)'}), 403
            return f(*args, **kwargs)
        return decorated

    @backup_bp.route('/backup/create', methods=['POST'])
    @_require_admin_api
    def api_create_backup():
        """API endpoint do tworzenia backupu — TYLKO admin."""
        backup_path = create_backup()
        if backup_path:
            return jsonify({'success': True, 'backup': backup_path.name})
        return jsonify({'success': False, 'error': 'Nie udało się utworzyć backupu'}), 500

    @backup_bp.route('/backup/list')
    @_require_admin_api
    def api_list_backups():
        """API endpoint do listowania backupów — TYLKO admin.
        Ujawnia nazwy plików = wektor reconnaissance dla atakującego."""
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
    @_require_admin_api
    def api_restore_backup():
        """API endpoint do przywracania backupu — TYLKO admin.
        Filename validation zapobiega path traversal (np. '../../../etc/passwd').
        Kazda proba przywrocenia (udana/nieudana/odrzucona) jest logowana do audit logu."""
        import gc
        from .database import log_admin_action

        data = request.get_json(silent=True) or {}
        filename = data.get('filename')

        if not filename:
            log_admin_action('backup_restore', {'reason': 'missing_filename'}, success=False,
                             error_message='Brak nazwy pliku')
            return jsonify({'success': False, 'error': 'Brak nazwy pliku'}), 400

        # PATH TRAVERSAL GUARD — loguj proby ataku
        if not _is_safe_backup_filename(filename):
            log_admin_action('backup_restore', {'attempted_filename': filename[:200],
                                                'reason': 'path_traversal_blocked'},
                             success=False, error_message='Nieprawidlowa nazwa pliku backupu')
            return jsonify({'success': False, 'error': 'Nieprawidłowa nazwa pliku backupu'}), 400

        # Zamknij CAŁY connection pool przed przywracaniem
        try:
            from .database import close_connection_pool
            close_connection_pool()
        except Exception as e:
            print(f"[WARN] Nie udało się zamknąć connection pool: {e}")

        # Wymuś garbage collection
        gc.collect()

        success, message = restore_backup(filename)
        log_admin_action('backup_restore', {'filename': filename, 'message': message[:200]},
                         success=success,
                         error_message=None if success else message[:200])

        # Po przywracaniu wyczyść pool ponownie - nowe połączenia otworzą się na nowej bazie
        try:
            from .database import close_connection_pool
            close_connection_pool()
        except Exception as e:
            print(f"[WARN] Nie udało się zamknąć connection pool po przywracaniu: {e}")

        return jsonify({'success': success, 'message': message})

    @backup_bp.route('/backup/verify/<filename>')
    @_require_admin_api
    def api_verify_backup(filename):
        """API endpoint do weryfikacji backupu — TYLKO admin."""
        if not _is_safe_backup_filename(filename):
            return jsonify({'success': False, 'error': 'Nieprawidłowa nazwa pliku backupu'}), 400
        success, message = verify_backup(filename)
        return jsonify({'success': success, 'message': message})

    @backup_bp.route('/backup/sync-gdrive', methods=['POST'])
    @_require_admin_api
    def api_sync_gdrive():
        """Ręczna synchronizacja backupów z Google Drive — TYLKO admin."""
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
        status_icon = "<span class=material-symbols-outlined style=color:#22c55e>check_circle</span>" if status_ok else "<span class=material-symbols-outlined style=color:#ef4444>cancel</span>"
        print(f"  {status_icon} {b['filename']} - {b['size_mb']:.2f} MB - {b['created_str']} - {status_msg}")
