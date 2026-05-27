#!/usr/bin/env python3
"""
Pobiera Pythona 3.11 embeddable (Windows x64) i instaluje go w installer/python/
wraz ze wszystkimi bibliotekami z requirements.txt.

Wynik: installer/python/ (~100 MB) -> build_release.py pakuje go do zipa.
Klient po INSTALL.bat ma od razu dzialajacego Pythona BEZ instalacji.

WYMAGANIA:
- Internet (~30 MB do pobrania)
- Disk: ~250 MB tymczasowo, ~100 MB final
- Uruchom RAZ przed pierwszym build_release.py

UWAGA:
- Robi to TYLKO dla Windows (embedded Python = .exe).
- Dla Linux klientow (NUC/Pi/VPS): uzywaja systemowego Pythona.
- Skrypt mozna uruchamiac na Linux/Mac, ale wynik dziala tylko na Windows
  (skrypt sciaga windows-amd64.zip).

Uzycie:
    python tools/prepare_python_embed.py
    python tools/prepare_python_embed.py --version 3.11.9 --force
"""
import os
import sys
import shutil
import argparse
import subprocess
import zipfile
from pathlib import Path

PYTHON_VERSION_DEFAULT = '3.11.9'
PYTHON_EMBED_URL = 'https://www.python.org/ftp/python/{ver}/python-{ver}-embed-amd64.zip'
GET_PIP_URL = 'https://bootstrap.pypa.io/get-pip.py'


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _download(url: str, dest: Path, label: str = ''):
    """Sciagniecie z progress bar."""
    import urllib.request
    print(f"  [DL] {label or url} -> {dest.name}")
    def _hook(blocks, block_size, total):
        if total > 0:
            pct = min(100, blocks * block_size * 100 // total)
            print(f"    {pct}% ({blocks * block_size // (1024*1024)} MB)", end='\r')
    urllib.request.urlretrieve(url, dest, reporthook=_hook)
    print()  # newline


def prepare_embed(version: str, force: bool = False):
    root = _project_root()
    target = root / 'installer' / 'python'

    if target.exists() and not force:
        existing = (target / 'python.exe').exists()
        print(f"[SKIP] {target} juz istnieje (Python: {'OK' if existing else 'INCOMPLETE'}).")
        print(f"       Aby przebudowac: --force")
        return True

    if force and target.exists():
        print(f"[CLEAN] Usuwam {target}")
        shutil.rmtree(target)

    target.mkdir(parents=True, exist_ok=True)

    # 1. Sciagniecie Pythona embeddable
    embed_url = PYTHON_EMBED_URL.format(ver=version)
    tmp_zip = root / f'_python-{version}-embed.zip'
    print(f"\n[1/4] Pobieram Python {version} embeddable (Windows amd64)...")
    _download(embed_url, tmp_zip, label=f'python-{version}-embed-amd64.zip')

    # 2. Rozpakuj
    print(f"\n[2/4] Rozpakowuje do {target}")
    with zipfile.ZipFile(tmp_zip, 'r') as zf:
        zf.extractall(target)
    tmp_zip.unlink()

    # 3. Odblokuj imports z site-packages (.pth)
    # Embedded Python ma python311._pth ktore IGNORUJE site-packages.
    # Trzeba odkomentowac "import site" zeby pip / pip-installed pakiety dzialaly.
    pth_files = list(target.glob('python*._pth'))
    if pth_files:
        pth = pth_files[0]
        print(f"\n[3/4] Patcz {pth.name} (odkomentuj 'import site')")
        content = pth.read_text(encoding='utf-8')
        if '#import site' in content:
            content = content.replace('#import site', 'import site')
        elif 'import site' not in content:
            content += '\nimport site\n'
        # Dodaj sciezki ktore embedded Python potrzebuje dla naszych modulow
        if 'Lib\\site-packages' not in content:
            content += '\nLib\\site-packages\n'
        pth.write_text(content, encoding='utf-8')
        print(f"  [OK] {pth.name} zaktualizowany")

    # 4. Sciagnij get-pip.py i zainstaluj pip
    print(f"\n[4/4] Instaluje pip + requirements.txt")
    get_pip = target / 'get-pip.py'
    _download(GET_PIP_URL, get_pip, label='get-pip.py')

    # Czy jestesmy na Windows? Jesli tak -> wywoluj python.exe.
    # Jesli nie (np. budujemy na Linux) -> nie da sie odpalic python.exe,
    # wtedy uzytkownik MUSI uruchomic ten krok rcznie na Windowsie.
    python_exe = target / 'python.exe'
    if sys.platform.startswith('win') and python_exe.exists():
        print(f"  Uruchamiam: {python_exe} get-pip.py")
        r = subprocess.run([str(python_exe), str(get_pip)], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  [FAIL] get-pip.py: {r.stderr[:500]}")
            return False
        print(f"  [OK] pip zainstalowany")

        # Pip install requirements
        req = _project_root() / 'requirements.txt'
        if req.exists():
            print(f"\n  Instaluje pakiety z {req.name}...")
            r = subprocess.run(
                [str(python_exe), '-m', 'pip', 'install', '-r', str(req), '--no-warn-script-location'],
                capture_output=True, text=True
            )
            if r.returncode != 0:
                print(f"  [WARN] pip install miał błędy:")
                print(r.stderr[-1500:])
            else:
                print(f"  [OK] Wszystkie biblioteki zainstalowane")
        else:
            print(f"  [WARN] Brak {req}")
    else:
        print(f"  [!] Nie jestes na Windows ({sys.platform}) — nie moge uruchomic python.exe.")
        print(f"  [!] Skopiuj installer/python/ na maszyne Windows i tam wykonaj:")
        print(f"      python.exe get-pip.py")
        print(f"      python.exe -m pip install -r requirements.txt")

    # Posprzataj
    if get_pip.exists():
        get_pip.unlink()

    # Statystyki
    size = sum(f.stat().st_size for f in target.rglob('*') if f.is_file())
    size_mb = size / (1024 * 1024)
    print(f"\n[OK] Embedded Python gotowy: {target}")
    print(f"     Rozmiar: {size_mb:.1f} MB")
    print(f"     Plikow: {sum(1 for _ in target.rglob('*'))}")

    # Hint dla build_release
    print(f"\nNastepny krok: python tools/build_release.py")
    print(f"  Zip bedzie zawieral installer/python/ -> klient nie musi instalowac Pythona.")
    return True


def main():
    parser = argparse.ArgumentParser(description='Pobierz Python 3.11 embeddable do installer/python/')
    parser.add_argument('--version', default=PYTHON_VERSION_DEFAULT,
                        help=f'Wersja Pythona (default: {PYTHON_VERSION_DEFAULT})')
    parser.add_argument('--force', action='store_true',
                        help='Usun installer/python/ jesli istnieje i pobierz na nowo')
    args = parser.parse_args()

    ok = prepare_embed(args.version, args.force)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
