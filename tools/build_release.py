#!/usr/bin/env python3
"""
Buduje czystą wersję AKCES HUB do wydania klientowi.
Kopiuje kod bez wrażliwych plików, tworzy pustą bazę,
i pakuje do ZIP.

Użycie:
    python tools/build_release.py
    python tools/build_release.py --output ~/Desktop/akces-hub-release.zip
"""

import os
import sys
import shutil
import argparse
import zipfile
from datetime import datetime

# Pliki/foldery do WYKLUCZENIA
EXCLUDE_FILES = {
    '.env',
    '.secret_key',
    'gemini_config.py',
    'api_key.txt',
    'vinted_cookies.json',
    'email_config.json',
    'goal_data.json',
    'OFERTA_KLIENTA.md',
    'TODO_IMPROVEMENTS.md',
    'app.py.backup',
    'server.log',
    'http_code.txt',
    'server_out.txt',
    'server_err.txt',
    'print_debug.log',
    'nul',
}

EXCLUDE_DIRS = {
    '.git',
    '.claude',
    '__pycache__',
    'venv',
    '.venv',
    'node_modules',
    'backups',
    'cloud_exports',
    '_update_tmp',
    'logs',
    'tools',  # Generator licencji — nie dla klientów
    'static/downloads',
}

EXCLUDE_PATTERNS = {
    '.pyc', '.db', '.db-wal', '.db-shm', '.log',
    '.bak', '.gz',
    'test_', 'debug_', 'curl_', 'pentest_',
    'enhanced_test', 'final_test', 'ev_', 'infographic_',
    'pipeline_test', 'vsprint_example', 'watermark_test',
    'allegro_',
}


def should_exclude(path, name):
    """Sprawdź czy plik/folder powinien być wykluczony."""
    if name in EXCLUDE_FILES:
        return True
    if name in EXCLUDE_DIRS:
        return True
    for pat in EXCLUDE_PATTERNS:
        if name.startswith(pat) or name.endswith(pat):
            return True
    # Pliki licencji JSON
    if name.startswith('license_') and name.endswith('.json'):
        return True
    return False


def build_release(source_dir, output_path):
    """Zbuduj czystą wersję."""
    print(f"Budowanie release z: {source_dir}")
    print(f"Output: {output_path}")
    print()

    files_included = 0
    files_excluded = 0

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(source_dir):
            # Filtruj foldery
            dirs[:] = [d for d in dirs if not should_exclude(root, d)]

            for filename in files:
                if should_exclude(root, filename):
                    files_excluded += 1
                    continue

                filepath = os.path.join(root, filename)
                arcname = os.path.relpath(filepath, source_dir)

                # Zamień na unix-style paths
                arcname = 'akces-hub/' + arcname.replace('\\', '/')

                zf.write(filepath, arcname)
                files_included += 1

        # Dodaj puste foldery które klient potrzebuje
        for empty_dir in ['logs', 'backups', 'static/downloads']:
            zf.writestr(f'akces-hub/{empty_dir}/.gitkeep', '')

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"Pliki dołączone: {files_included}")
    print(f"Pliki wykluczone: {files_excluded}")
    print(f"Rozmiar ZIP: {size_mb:.1f} MB")
    print(f"\nGotowe! Plik: {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Build AKCES HUB release')
    parser.add_argument('--output', '-o', default=None,
                        help='Ścieżka do pliku ZIP (default: akces-hub-YYYYMMDD.zip)')
    args = parser.parse_args()

    source_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if args.output:
        output_path = args.output
    else:
        date_str = datetime.now().strftime('%Y%m%d')
        output_path = os.path.join(
            os.path.dirname(source_dir),
            f'akces-hub-release-{date_str}.zip'
        )

    build_release(source_dir, output_path)


if __name__ == '__main__':
    main()
