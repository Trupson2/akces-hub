#!/usr/bin/env python3
"""
Automatyczny backup + cloud sync dla Akces Hub
Uruchamiany codziennie o 3:00 (po nocnym kombajnie o 2:00)

Wykonuje:
1. Backup bazy SQLite (bezpieczne SQLite backup API)
2. Export CSV (palety + produkty)
3. Sync do chmury via rclone (Google Drive / Dropbox / OneDrive)
4. Rotacja starych backupów (trzyma 7 dni + 4 tygodniowe)
"""

import sys, os, sqlite3, shutil, csv, json, subprocess
from datetime import datetime, timedelta
from pathlib import Path

# Auto-detect app directory
APP_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = APP_DIR / 'akces_hub.db'
BACKUP_DIR = APP_DIR / 'backups'
EXPORT_DIR = APP_DIR / 'cloud_exports'
LOG_FILE = APP_DIR / 'backups' / 'backup.log'

# Cloud config
RCLONE_REMOTE = 'akces-cloud'  # nazwa remote w rclone config
CLOUD_DIR = 'AkcesHub_Backups'  # folder w chmurze

def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        BACKUP_DIR.mkdir(exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except:
        pass

def backup_database():
    """Backup bazy przez SQLite backup API (bezpieczne z WAL mode)"""
    try:
        BACKUP_DIR.mkdir(exist_ok=True)

        if not DB_PATH.exists():
            log(f"❌ Baza nie istnieje: {DB_PATH}")
            return None

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        day_of_week = datetime.now().strftime('%A')

        # Codzienny backup
        backup_name = f"akces_daily_{ts}.db"
        backup_path = BACKUP_DIR / backup_name

        src = sqlite3.connect(str(DB_PATH))
        dst = sqlite3.connect(str(backup_path))
        src.backup(dst)
        dst.close()
        src.close()

        size_mb = backup_path.stat().st_size / (1024 * 1024)
        log(f"✅ Backup: {backup_name} ({size_mb:.1f} MB)")

        # Tygodniowy backup (niedziela)
        if day_of_week == 'Sunday':
            weekly_name = f"akces_weekly_{ts}.db"
            weekly_path = BACKUP_DIR / weekly_name
            shutil.copy2(backup_path, weekly_path)
            log(f"✅ Weekly backup: {weekly_name}")

        return backup_path

    except Exception as e:
        log(f"❌ Backup error: {e}")
        return None

def export_csv():
    """Export palet i produktów do CSV"""
    try:
        EXPORT_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')

        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # === Export palet ===
        palety = conn.execute('''
            SELECT p.*,
                   (SELECT COUNT(*) FROM produkty WHERE paleta_id = p.id) as produktow,
                   (SELECT COALESCE(SUM(ilosc), 0) FROM produkty WHERE paleta_id = p.id) as sztuk,
                   (SELECT COALESCE(SUM(cena_allegro * ilosc), 0) FROM produkty WHERE paleta_id = p.id) as wartosc
            FROM palety p ORDER BY data_zakupu DESC
        ''').fetchall()

        palety_file = EXPORT_DIR / f'palety_{ts}.csv'
        with open(palety_file, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['ID', 'Nazwa', 'Dostawca', 'Cena zakupu', 'Data', 'Produktów', 'Sztuk', 'Wartość'])
            for p in palety:
                w.writerow([p['id'], p['nazwa'] or '', p['dostawca'] or '',
                           f"{p['cena_zakupu'] or 0:.2f}", p['data_zakupu'] or '',
                           p['produktow'], p['sztuk'], f"{p['wartosc']:.2f}"])

        # === Export produktów ===
        produkty = conn.execute('''
            SELECT p.*, pal.nazwa as paleta_nazwa
            FROM produkty p LEFT JOIN palety pal ON p.paleta_id = pal.id
            ORDER BY p.id DESC
        ''').fetchall()

        produkty_file = EXPORT_DIR / f'produkty_{ts}.csv'
        with open(produkty_file, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['ID', 'EAN', 'ASIN', 'Nazwa', 'Ilość', 'Cena Allegro',
                        'Status', 'Paleta', 'Lokalizacja', 'Kategoria'])
            for p in produkty:
                w.writerow([p['id'], p['ean'] or '', p['asin'] or '',
                           p['nazwa'] or '', p['ilosc'] or 0,
                           f"{p['cena_allegro'] or 0:.2f}", p['status'] or '',
                           p['paleta_nazwa'] or '', p['lokalizacja'] or '',
                           p['kategoria'] or ''])

        # === Export sprzedaży ===
        sprzedaze = conn.execute('''
            SELECT s.*, p.nazwa as produkt_nazwa, p.ean, p.asin
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            ORDER BY s.data_sprzedazy DESC
        ''').fetchall()

        sprzedaze_file = EXPORT_DIR / f'sprzedaze_{ts}.csv'
        with open(sprzedaze_file, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['ID', 'Data', 'Produkt', 'EAN', 'ASIN', 'Platforma',
                        'Kwota', 'Prowizja', 'Numer zamówienia'])
            for s in sprzedaze:
                w.writerow([s['id'], s['data_sprzedazy'] or '',
                           s['produkt_nazwa'] or '', s['ean'] or '', s['asin'] or '',
                           s['platforma'] or '', f"{s['kwota'] or 0:.2f}",
                           f"{s['prowizja'] or 0:.2f}", s['numer_zamowienia'] or ''])

        # === Stats summary JSON ===
        stats = conn.execute('''
            SELECT
                (SELECT COUNT(*) FROM palety) as palety,
                (SELECT COUNT(*) FROM produkty) as produkty,
                (SELECT COUNT(*) FROM produkty WHERE status='magazyn') as w_magazynie,
                (SELECT COUNT(*) FROM produkty WHERE status='wystawiony') as wystawione,
                (SELECT COUNT(*) FROM produkty WHERE status='sprzedany') as sprzedane,
                (SELECT COUNT(*) FROM sprzedaze) as sprzedaze,
                (SELECT COALESCE(SUM(kwota), 0) FROM sprzedaze) as przychod,
                (SELECT COUNT(*) FROM oferty) as oferty_allegro
        ''').fetchone()

        stats_file = EXPORT_DIR / f'stats_{ts}.json'
        with open(stats_file, 'w', encoding='utf-8') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'palety': stats['palety'],
                'produkty': stats['produkty'],
                'w_magazynie': stats['w_magazynie'],
                'wystawione': stats['wystawione'],
                'sprzedane': stats['sprzedane'],
                'sprzedaze': stats['sprzedaze'],
                'przychod': float(stats['przychod']),
                'oferty_allegro': stats['oferty_allegro']
            }, f, indent=2, ensure_ascii=False)

        conn.close()

        log(f"✅ Export: {len(palety)} palet, {len(produkty)} produktów, {len(sprzedaze)} sprzedaży")
        return [palety_file, produkty_file, sprzedaze_file, stats_file]

    except Exception as e:
        log(f"❌ Export error: {e}")
        return []

def cleanup_old():
    """Rotacja: 7 daily + 4 weekly + 3 exporty"""
    try:
        # Daily backups — trzymaj 7
        dailies = sorted(BACKUP_DIR.glob('akces_daily_*.db'), key=lambda x: x.stat().st_mtime, reverse=True)
        for old in dailies[7:]:
            old.unlink()
            log(f"🗑️ Usunięto: {old.name}")

        # Weekly backups — trzymaj 4
        weeklies = sorted(BACKUP_DIR.glob('akces_weekly_*.db'), key=lambda x: x.stat().st_mtime, reverse=True)
        for old in weeklies[4:]:
            old.unlink()
            log(f"🗑️ Usunięto: {old.name}")

        # CSV exports — trzymaj 3 najnowsze z każdego typu
        for pattern in ['palety_*.csv', 'produkty_*.csv', 'sprzedaze_*.csv', 'stats_*.json']:
            files = sorted(EXPORT_DIR.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)
            for old in files[3:]:
                old.unlink()

    except Exception as e:
        log(f"⚠️ Cleanup error: {e}")

def sync_to_cloud():
    """Sync backupów i exportów do chmury przez rclone"""
    try:
        # Sprawdź czy rclone jest zainstalowany
        result = subprocess.run(['rclone', 'version'], capture_output=True, timeout=10)
        if result.returncode != 0:
            log("⚠️ rclone nie zainstalowany — pomijam cloud sync")
            log("   Aby włączyć: curl https://rclone.org/install.sh | sudo bash && rclone config")
            return False

        # Sprawdź czy remote jest skonfigurowany
        result = subprocess.run(['rclone', 'listremotes'], capture_output=True, text=True, timeout=10)
        if RCLONE_REMOTE + ':' not in result.stdout:
            log(f"⚠️ rclone remote '{RCLONE_REMOTE}' nie skonfigurowany — pomijam cloud sync")
            log(f"   Aby skonfigurować: rclone config")
            log(f"   Utwórz remote o nazwie: {RCLONE_REMOTE}")
            return False

        # Sync backupów
        log("☁️ Syncing backups to cloud...")
        r1 = subprocess.run([
            'rclone', 'sync',
            str(BACKUP_DIR),
            f'{RCLONE_REMOTE}:{CLOUD_DIR}/backups',
            '--include', '*.db',
            '--transfers', '2',
            '-q'
        ], capture_output=True, text=True, timeout=300)

        if r1.returncode == 0:
            log("✅ Backups synced to cloud")
        else:
            log(f"⚠️ Backup sync error: {r1.stderr[:100]}")

        # Sync exportów
        r2 = subprocess.run([
            'rclone', 'sync',
            str(EXPORT_DIR),
            f'{RCLONE_REMOTE}:{CLOUD_DIR}/exports',
            '--transfers', '2',
            '-q'
        ], capture_output=True, text=True, timeout=300)

        if r2.returncode == 0:
            log("✅ Exports synced to cloud")
        else:
            log(f"⚠️ Export sync error: {r2.stderr[:100]}")

        # Sync bazy (najnowszy backup)
        latest = sorted(BACKUP_DIR.glob('akces_daily_*.db'), key=lambda x: x.stat().st_mtime, reverse=True)
        if latest:
            r3 = subprocess.run([
                'rclone', 'copyto',
                str(latest[0]),
                f'{RCLONE_REMOTE}:{CLOUD_DIR}/latest_backup.db',
                '-q'
            ], capture_output=True, text=True, timeout=120)
            if r3.returncode == 0:
                log("✅ Latest backup → cloud/latest_backup.db")

        return True

    except FileNotFoundError:
        log("⚠️ rclone nie znaleziony — pomijam cloud sync")
        return False
    except subprocess.TimeoutExpired:
        log("⚠️ Cloud sync timeout (>5min)")
        return False
    except Exception as e:
        log(f"❌ Cloud sync error: {e}")
        return False

def send_telegram_report(backup_path, export_files):
    """Wyślij raport backupu na Telegram"""
    try:
        sys.path.insert(0, str(APP_DIR))
        from modules.database import get_config

        # Import get_config for telegram
        import sqlite3 as sq
        c = sq.connect(str(DB_PATH))
        c.row_factory = sq.Row
        token = ''
        chat_id = ''
        try:
            r = c.execute("SELECT value FROM config WHERE key='telegram_bot_token'").fetchone()
            if r: token = r['value']
            r = c.execute("SELECT value FROM config WHERE key='telegram_chat_id'").fetchone()
            if r: chat_id = r['value']
        except:
            pass
        c.close()

        if not token or not chat_id:
            return

        import urllib.request

        backup_size = backup_path.stat().st_size / (1024*1024) if backup_path else 0
        msg = f"🔄 *Backup codzienny*\n"
        msg += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        msg += f"💾 Backup: {backup_size:.1f} MB\n"
        msg += f"📊 Exportów: {len(export_files)}\n"
        msg += f"✅ Wszystko OK"

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({'chat_id': chat_id, 'text': msg, 'parse_mode': 'Markdown'}).encode()
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=10)
        log("📱 Telegram notification sent")

    except Exception as e:
        log(f"⚠️ Telegram notify error: {e}")

def main():
    log("=" * 50)
    log("🔄 BACKUP & CLOUD EXPORT START")
    log(f"   App: {APP_DIR}")
    log(f"   DB:  {DB_PATH}")

    # 1. Backup bazy
    backup_path = backup_database()

    # 2. Export CSV
    export_files = export_csv()

    # 3. Cleanup starych
    cleanup_old()

    # 4. Sync do chmury
    cloud_ok = sync_to_cloud()

    # 5. Powiadomienie Telegram
    if backup_path:
        send_telegram_report(backup_path, export_files)

    log(f"🔄 BACKUP DONE — DB: {'✅' if backup_path else '❌'} | Export: {len(export_files)} | Cloud: {'✅' if cloud_ok else '⏭️'}")
    log("=" * 50)

if __name__ == '__main__':
    main()
