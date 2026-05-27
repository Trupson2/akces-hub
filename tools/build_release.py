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
    '.env.key',        # PHASE 4: Fernet master key (deszyfruje WSZYSTKIE sekrety)
    '.env.extra',      # PHASE 4: dodatkowe env vars (mogą zawierać sekrety)
    '.secret_key',
    '.license_secret', # PHASE 4: sekret podpisu licencji (klient mógłby fałszować)
    'gemini_config.py',
    'api_key.txt',
    'vinted_cookies.json',
    'email_config.json',
    'goal_data.json',
    'OFERTA_KLIENTA.md',
    'OFERTA DLA KLIENTA.md',
    'TODO_IMPROVEMENTS.md',
    'app.py.backup',
    'server.log',
    'http_code.txt',
    'server_out.txt',
    'server_err.txt',
    'print_debug.log',
    'nul',
    '1',         # debug placeholder pliku z reusage typu `echo > 1`
    '2',
    '3',
    'tmp.txt',
    # Internal docs/reports — NIE dla klientów
    'SECURITY_HARDENING_REPORT.md',
    'SECURITY_VERIFICATION.md',
    'SECURITY_AUDIT_2026-04.md',
    'PLAN.md',
    'WINNING_SCOUT_PROMPT.md',
    'INTEGRATION_NOTE.txt',
    'PHASE1_INCIDENT_REPORT.md',
    'README_SKLEPAKCES.md',  # internal sklepakces (klient bez Enterprise nie ma)
    # Random debug/screenshots
    'op13_logcat.txt',
    'op13_preview.png',
    'op13_screen.png',
    'tab_now.png',
    'tab_screen.png',
    'warehouse_layout.json',
    # Twoje narzędzia developmentowe
    'push_file.ps1',
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
    # Inne projekty Adriana (NIE dla klienta):
    'mobile',     # Akces Booth Flutter app
    'landing',    # Twoja landing page index.html
    'designs',    # Marketing designs
    'docs',       # Twoje internal docs (DEPLOYMENT, SECURITY_AUDIT, PHASE1...)
    'photo_daemon',  # Photo Daemon side-project
    '.pytest_cache',
    '.idea',      # IDE config
    'dist',
    'build',
    # Zagnieżdżona kopia repo (occurs when source dir literalnie zawiera akces-hub/ subfolder):
    'akces-hub',  # zapobiega arcname zip 'akces-hub/akces-hub/...'
}

EXCLUDE_EXTENSIONS = {
    '.pyc', '.db', '.db-wal', '.db-shm', '.log',
    '.bak', '.gz',
    '.out',  # PHASE 4: nohup.out/server.out — dokładny wektor incydentu PHASE 1
}

EXCLUDE_PREFIXES = {
    'test_', 'debug_', 'curl_', 'pentest_',
    'enhanced_test', 'final_test', 'ev_', 'infographic_',
    'pipeline_test', 'vsprint_example', 'watermark_test',
}

# Pliki graficzne allegro (nie allegro_api.py!)
EXCLUDE_IMAGE_PREFIXES = {'allegro_'}
EXCLUDE_IMAGE_EXTENSIONS = {'.jpg', '.png', '.jpeg'}


def should_exclude(path, name):
    """Sprawdź czy plik/folder powinien być wykluczony."""
    if name in EXCLUDE_FILES:
        return True
    if name in EXCLUDE_DIRS:
        return True
    _, ext = os.path.splitext(name)
    if ext in EXCLUDE_EXTENSIONS:
        return True
    for pat in EXCLUDE_PREFIXES:
        if name.startswith(pat):
            return True
    # Obrazy allegro (allegro_*.jpg) ale NIE allegro_api.py
    for pat in EXCLUDE_IMAGE_PREFIXES:
        if name.startswith(pat) and ext in EXCLUDE_IMAGE_EXTENSIONS:
            return True
    # Pliki licencji JSON
    if name.startswith('license_') and name.endswith('.json'):
        return True
    return False


def _get_master_secret(source_dir):
    """Pobierz Twój LICENSE_SECRET (z env lub .license_secret) — do wbudowania w zip klienta.

    Klient potrzebuje SAMEGO secretu co Ty żeby Twoje wygenerowane licencje walidowały.
    Bez tego: Hub klienta generuje SWÓJ random secret → mismatch → Nieprawidłowy klucz.

    Returns: hex secret (str) lub None gdy brak.
    """
    s = os.environ.get('AKCES_LICENSE_SECRET', '').strip()
    if s:
        return s
    path = os.path.join(source_dir, '.license_secret')
    if os.path.exists(path):
        with open(path, 'r') as f:
            s = f.read().strip()
            if s:
                return s
    # Spróbuj z .env
    env_path = os.path.join(source_dir, '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if line.startswith('AKCES_LICENSE_SECRET='):
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
    return None


def build_release(source_dir, output_path, embed_secret=True):
    """Zbuduj czystą wersję. embed_secret=True dołącza Twój MASTER LICENSE_SECRET
    do .license_secret w zipie — klient używa go automatycznie, licencje walidują."""
    print(f"Budowanie release z: {source_dir}")
    print(f"Output: {output_path}")
    print()

    master_secret = _get_master_secret(source_dir) if embed_secret else None
    if embed_secret:
        if master_secret:
            print(f"  [INFO] MASTER secret znaleziony (len={len(master_secret)}) — zostanie wbudowany")
        else:
            print("  [WARN] Brak MASTER secret — klient wygeneruje SWÓJ → Twoje licencje nie zadziałają!")

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

        # Wbuduj MASTER LICENSE_SECRET (klient potrzebuje TWOJEGO secretu
        # żeby Twoje wygenerowane licencje walidowały u niego)
        if embed_secret and master_secret:
            zf.writestr('akces-hub/.license_secret', master_secret)
            print(f"  [INFO] .license_secret wbudowany do zipa (klient = Twój secret)")

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"Pliki dołączone: {files_included}")
    print(f"Pliki wykluczone: {files_excluded}")
    print(f"Rozmiar ZIP: {size_mb:.1f} MB")
    print(f"\nGotowe! Plik: {output_path}")

    verify_release(output_path, allow_license_secret=embed_secret)


# PHASE 4 — automatyczna weryfikacja paczki (fail-closed).
# Skanuje GOTOWY zip: zakazane pliki + sygnatura tokenu (incydent PHASE 1).
# Druga linia obrony NIEZALEŻNA od EXCLUDE_* (gdyby ktoś rozszczelnił listę).
_FORBIDDEN_NAMES = {
    '.env', '.env.key', '.env.extra', '.secret_key', '.license_secret',
    'nohup.out', 'gemini_config.py', 'generate_license.py',
}
_FORBIDDEN_EXTS = {'.db', '.db-wal', '.db-shm', '.out', '.log', '.pyc', '.key'}
_FORBIDDEN_PARTS = ('/.git/', '/backups/', '/cloud_exports/', '/tools/', '/__pycache__/')
_TOKEN_RE = __import__('re').compile(rb'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}')
_SCAN_EXT = {'.py', '.md', '.txt', '.json', '.html', '.js', '.cfg',
             '.ini', '.sh', '.yml', '.yaml', '.env', ''}


def verify_release(zip_path, allow_license_secret=False):
    """Skan gotowego ZIP. Zwraca True/False; przy FAIL -> SystemExit(1).

    allow_license_secret=True: pozwala na .license_secret w zipie (build dla
    klienta z embedded master secret — celowo, klient potrzebuje go do walidacji
    licencji wygenerowanych w Twoim panelu).
    """
    print("\n-- Weryfikacja paczki (PHASE 4) --")
    problems = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            arc = info.filename
            base = os.path.basename(arc)
            _, ext = os.path.splitext(base)
            # Pusty .gitkeep = celowy placeholder pustego katalogu
            # (logs/ backups/ static/downloads/ tworzy sam builder).
            if base == '.gitkeep' and info.file_size == 0:
                continue
            # .license_secret jest CELOWO embedded gdy build for client
            if base == '.license_secret' and allow_license_secret:
                continue
            if base in _FORBIDDEN_NAMES:
                problems.append(f"ZAKAZANY plik: {arc}")
                continue
            if ext in _FORBIDDEN_EXTS:
                problems.append(f"ZAKAZANE rozszerzenie: {arc}")
                continue
            if any(p in '/' + arc for p in _FORBIDDEN_PARTS):
                problems.append(f"ZAKAZANY katalog: {arc}")
                continue
            if ext.lower() in _SCAN_EXT and info.file_size <= 2_000_000:
                try:
                    if _TOKEN_RE.search(zf.read(arc)):
                        problems.append(f"SYGNATURA TOKENU (eyJ…): {arc}")
                except Exception:
                    pass
    if problems:
        print("  [FAIL] Paczka NIE nadaje sie do wysylki:")
        for p in problems[:40]:
            print(f"   x {p}")
        print(f"\n  Lacznie problemow: {len(problems)}. Usun pliki i zbuduj ponownie.")
        raise SystemExit(1)
    print("  [OK] Brak zakazanych plikow, sekretow i sygnatur tokenow. OK do wysylki.")
    return True


def main():
    parser = argparse.ArgumentParser(description='Build AKCES HUB release')
    parser.add_argument('--output', '-o', default=None,
                        help='Ścieżka do pliku ZIP (default: akces-hub-YYYYMMDD.zip)')
    parser.add_argument('--no-embed-secret', action='store_true',
                        help='NIE wbudowuj Twojego LICENSE_SECRET (klient wygeneruje swój — UI '
                             'Generator licencji u Ciebie wtedy NIE będzie generować ważnych '
                             'licencji dla tego klienta!)')
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

    build_release(source_dir, output_path, embed_secret=not args.no_embed_secret)


if __name__ == '__main__':
    main()
