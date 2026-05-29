"""
ZIP-based update mechanism (dla instalacji bez gita, np. klient Windows).

Workflow:
1. check_github_release() — porownuje VERSION z latest GitHub Release
2. download_release_zip() — sciaga zip z GitHub Releases assets
3. verify_zip_signature() — weryfikuje HMAC-SHA256 (LICENSE_SECRET)
4. install_update() — backup obecnej kopii, extract nowej, restart Pythona

Bezpieczenstwo:
- Zip MUSI byc podpisany (HMAC-SHA256 z master LICENSE_SECRET)
- Bez podpisu — odrzucamy (atak: ktos podstawi zlosliwy zip pod te sam URL)
- Backup obecnej kopii PRZED nadpisaniem (rollback gdyby cos sie zepsulo)
- Restart przez os.execv (Windows-friendly, nie wymaga systemctl)
"""
import os
import sys
import json
import shutil
import hmac
import hashlib
import zipfile
import tempfile
import time
from typing import Optional, Dict, Any

# Pliki/foldery KTORYCH NIE NADPISUJEMY przy update (stan klienta)
# Musza zostac niezmienione: DB, licencja, secret keys, uploady, backupy
PRESERVE_PATHS = {
    'akces_hub.db',
    'akces_hub.db-shm',
    'akces_hub.db-wal',
    '.license_secret',
    '.license',           # opcjonalny plik licencji (jesli klient ma backup obok DB)
    '.lic',
    'license.json',
    '.secret_key',
    '.env',
    'static/uploads',
    'static/downloads',
    # FIX 2026-05-28: klient logo / branding plików - klient wgral wlasne,
    # NIE nadpisuj przy update (sa juz w .gitignore, ale safety net).
    'static/brand_logo.svg',
    'static/brand_logo.png',
    'static/pwa_icon.png',
    'static/icon-192.png',
    'static/icon-512.png',
    # vendor_config.json - safety net (gitignore, ale gdyby ktos przypadkiem
    # zacommitowal swoja kopie do publicznego repo, nie nadpisuj klienckiej)
    'vendor_config.json',
    'backups',
    'logs',
    'venv',
    'installer/python',   # embedded Python folder (380 MB, NIE nadpisuj)
    'python',             # backward compat
    '__pycache__',
}


def _app_dir() -> str:
    """Glowny katalog aplikacji."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_current_version() -> str:
    """Czyta VERSION z pliku."""
    try:
        with open(os.path.join(_app_dir(), 'VERSION'), 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return '0.0.0'


def _version_tuple(v: str) -> tuple:
    """'1.2.3' -> (1,2,3). Bezpiecznie wzgl. nie-numerycznych segmentow."""
    out = []
    for part in v.lstrip('v').split('.'):
        try:
            out.append(int(part))
        except ValueError:
            # 'rc1' itp. — ignoruj
            break
    return tuple(out) or (0,)


def check_github_release(repo: str, timeout: int = 10) -> Dict[str, Any]:
    """Sprawdz latest release na GitHub.

    Args:
        repo: 'owner/name' (np. 'Trupson2/akces-hub-release')
        timeout: HTTP timeout w sekundach

    Returns:
        dict z polami:
          available: bool — czy jest update
          current: str   — aktualna wersja
          latest: str    — najnowsza wersja na GitHubie
          download_url: str — bezposredni URL do zip-a
          signature_url: str — URL do .sig (HMAC-SHA256)
          changelog: str — opis release
          published_at: str — data
          error: str (opcjonalnie) — gdy cos poszlo nie tak
    """
    import requests
    current = _get_current_version()
    result = {
        'available': False,
        'current': current,
        'latest': '',
        'download_url': '',
        'signature_url': '',
        'changelog': '',
        'published_at': '',
    }
    try:
        url = f'https://api.github.com/repos/{repo}/releases/latest'
        headers = {'Accept': 'application/vnd.github+json'}
        # Optional: GitHub token z config zwieksza rate limit (60 -> 5000/h)
        try:
            from modules.database import get_config
            gh_token = get_config('github_release_token', '')
            if gh_token:
                headers['Authorization'] = f'Bearer {gh_token}'
        except Exception:
            pass
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 404:
            result['error'] = f'Repo {repo} nie ma releases (404)'
            return result
        if r.status_code != 200:
            result['error'] = f'GitHub API: HTTP {r.status_code}'
            return result
        data = r.json()
        latest_tag = (data.get('tag_name') or '').strip()
        result['latest'] = latest_tag
        result['changelog'] = (data.get('body') or '')[:1000]
        result['published_at'] = data.get('published_at', '')
        # Znajdz asset .zip + asset .sig
        # WAZNE: dla PRIVATE repo uzywamy API URL (assets/{id}) zamiast
        # browser_download_url, bo browser url wymaga sesji a API akceptuje
        # Bearer token + Accept: application/octet-stream → redirect do CDN.
        for asset in data.get('assets', []):
            name = asset.get('name', '')
            asset_id = asset.get('id', 0)
            api_url = f'https://api.github.com/repos/{repo}/releases/assets/{asset_id}'
            if name.endswith('.zip'):
                result['download_url'] = api_url
            elif name.endswith('.sig') or name.endswith('.signature'):
                result['signature_url'] = api_url
        if not result['download_url']:
            result['error'] = 'Release nie ma asset .zip'
            return result
        # Porownaj wersje
        result['available'] = _version_tuple(latest_tag) > _version_tuple(current)
        return result
    except requests.Timeout:
        result['error'] = 'Timeout GitHub API'
        return result
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:200]}'
        return result


def check_public_version(repo: str = 'Trupson2/akces-hub', branch: str = 'main',
                          timeout: int = 10) -> Dict[str, Any]:
    """Sprawdz najnowsza wersje na PUBLIC repo (bez tokenu).

    Pobiera raw VERSION z github.com przez raw.githubusercontent.com.
    Bezpieczne dla klientow z ZIP install ktorzy NIE maja github_release_token.

    Args:
        repo: 'owner/name' (default: Trupson2/akces-hub - publiczne)
        branch: branch git (default: main)
        timeout: HTTP timeout
    Returns:
        dict: available, current, latest, download_url, error
    """
    import requests
    current = _get_current_version()
    result = {
        'available': False,
        'current': current,
        'latest': '',
        'download_url': f'https://github.com/{repo}/archive/refs/heads/{branch}.zip',
        'repo': repo,
        'branch': branch,
    }
    try:
        # Raw VERSION z public repo (bez auth)
        version_url = f'https://raw.githubusercontent.com/{repo}/{branch}/VERSION'
        r = requests.get(version_url, timeout=timeout)
        if r.status_code != 200:
            result['error'] = f'GitHub raw VERSION: HTTP {r.status_code}'
            return result
        latest = (r.text or '').strip().split('\n')[0].strip()
        result['latest'] = latest
        result['available'] = _version_tuple(latest) > _version_tuple(current)
        return result
    except requests.Timeout:
        result['error'] = 'Timeout GitHub raw'
        return result
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:200]}'
        return result


def download_public_archive(repo: str, branch: str, dest_path: str,
                            timeout: int = 180, progress_cb=None) -> bool:
    """Sciaga archive ZIP z PUBLIC repo (bez tokenu).

    URL: https://github.com/{repo}/archive/refs/heads/{branch}.zip
    GitHub odpowiada 302 redirect do codeload.github.com z signed URL.

    Args:
        repo: 'owner/name'
        branch: branch git
        dest_path: lokalna sciezka pliku zip do zapisania
        timeout: total timeout
        progress_cb: opcjonalne callable(downloaded, total)
    Returns:
        True OK, False fail
    """
    import requests
    url = f'https://github.com/{repo}/archive/refs/heads/{branch}.zip'
    try:
        with requests.get(url, stream=True, timeout=timeout, allow_redirects=True) as r:
            r.raise_for_status()
            total = int(r.headers.get('Content-Length', 0))
            downloaded = 0
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb:
                            try:
                                progress_cb(downloaded, total)
                            except Exception:
                                pass
        return True
    except Exception as e:
        print(f'[zip_updater] download public failed: {e}')
        return False


def download_release_zip(url: str, dest_path: str, timeout: int = 120,
                         progress_cb=None, token: str = '') -> bool:
    """Sciaga zip z GitHub Releases. Strumieniowo (nie ladowac calego do RAM).

    Args:
        url: URL do asset (API url 'https://api.github.com/repos/.../assets/{id}')
        dest_path: gdzie zapisac
        timeout: total timeout w sekundach
        progress_cb: opcjonalnie callable(downloaded_bytes, total_bytes)
        token: GitHub PAT (wymagany dla PRIVATE repo, opcjonalny dla PUBLIC)

    Returns:
        True gdy OK, False gdy blad.
    """
    import requests
    headers = {}
    # Token: Bearer auth dla PRIVATE repo
    if not token:
        try:
            from modules.database import get_config
            token = get_config('github_release_token', '') or ''
        except Exception:
            pass
    if token:
        headers['Authorization'] = f'Bearer {token}'
    # KRYTYCZNE: dla API URL /releases/assets/{id} GitHub zwraca metadata JSON
    # bez tego header. Z 'application/octet-stream' zwraca redirect do CDN
    # z signed URL gdzie idzie pobieranie binarki.
    headers['Accept'] = 'application/octet-stream'
    try:
        with requests.get(url, headers=headers, stream=True, timeout=timeout, allow_redirects=True) as r:
            r.raise_for_status()
            total = int(r.headers.get('Content-Length', 0))
            downloaded = 0
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb:
                            try:
                                progress_cb(downloaded, total)
                            except Exception:
                                pass
        return True
    except Exception as e:
        print(f'[zip_updater] download failed: {e}')
        return False


def verify_zip_signature(zip_path: str, signature_hex: str, secret: bytes) -> bool:
    """Weryfikuje HMAC-SHA256 zip-a przeciwko master LICENSE_SECRET.

    Args:
        zip_path: sciezka do .zip
        signature_hex: HMAC-SHA256 hex (64 znaki)
        secret: bytes master secret (LICENSE_SECRET)

    Returns:
        True gdy podpis OK, False inaczej (constant-time compare)
    """
    if not signature_hex or len(signature_hex) != 64:
        return False
    try:
        h = hmac.new(secret, digestmod=hashlib.sha256)
        with open(zip_path, 'rb') as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        expected = h.hexdigest()
        return hmac.compare_digest(expected, signature_hex.lower().strip())
    except Exception as e:
        print(f'[zip_updater] verify failed: {e}')
        return False


def _make_backup(backup_dir: str) -> Optional[str]:
    """Backup obecnej instalacji (bez DB/uploads — te juz inny mechanizm)."""
    src = _app_dir()
    os.makedirs(backup_dir, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(backup_dir, f'pre_update_{ts}')
    try:
        # Kopiuj TYLKO kod (.py, .html, .css, .js, requirements.txt itp.)
        # — bez DB/uploads/__pycache__/venv (te nie sa potrzebne do rollbacku
        # bo zostaja niezmienione w PRESERVE_PATHS)
        def _ignore(folder, names):
            ignored = []
            for n in names:
                if n in PRESERVE_PATHS or n.endswith('.pyc'):
                    ignored.append(n)
            return ignored
        shutil.copytree(src, backup_path, ignore=_ignore)
        return backup_path
    except Exception as e:
        print(f'[zip_updater] backup failed: {e}')
        return None


def _backup_database(backup_dir: str) -> Optional[str]:
    """v1.0.95 (S5): Backup DB przed update przez SQLite VACUUM INTO.

    Atomiczny + nie blokuje writerow dlugotrwale (vs file copy ktore moze
    zlapac WAL w niepelnym stanie). Backup uzyteczny do recznego rollback
    DB w razie niekompatybilnej migracji w nowej wersji.

    Returns: sciezka do backup .db lub None.
    """
    import sqlite3 as _sql
    db_path = os.path.join(_app_dir(), 'akces_hub.db')
    if not os.path.isfile(db_path):
        print(f'[zip_updater] DB backup: brak akces_hub.db (fresh install?)')
        return None
    os.makedirs(backup_dir, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    dst_path = os.path.join(backup_dir, f'akces_hub_pre_update_{ts}.db')
    try:
        # VACUUM INTO jest single-statement atomic, nie blokuje writerow
        # zbyt dlugo (vs source.backup(target) ktore drzy locki).
        # Wymaga SQLite >= 3.27.0 (Python 3.7+ ma to OK).
        conn = _sql.connect(db_path, timeout=30)
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")  # flush WAL do main
            conn.execute(f"VACUUM INTO ?", (dst_path,))
            conn.commit()
        finally:
            conn.close()
        # Walidacja: backup nie moze byc 0 byte
        if os.path.getsize(dst_path) < 1024:
            print(f'[zip_updater] DB backup za maly ({os.path.getsize(dst_path)} B) - usuwam')
            os.remove(dst_path)
            return None
        print(f'[zip_updater] DB backup OK: {dst_path} ({os.path.getsize(dst_path)//1024} KB)')
        return dst_path
    except Exception as e:
        print(f'[zip_updater] DB backup FAILED: {e}')
        # Sprzatnij polowiczny plik
        if os.path.isfile(dst_path):
            try:
                os.remove(dst_path)
            except Exception:
                pass
        return None


def _rollback_from_backup(backup_path: str) -> bool:
    """v1.0.95 (W3): Przywroc pliki z backupu po failed update.

    Wywoluj gdy _extract_and_swap zwroci ok=False - czesc plikow moze byc
    juz nadpisana, druga czesc stara. Klient zostaje z half-broken apka.

    Args:
        backup_path: sciezka do folderu z _make_backup()
    Returns:
        True gdy rollback OK, False inaczej.
    """
    if not os.path.isdir(backup_path):
        print(f'[zip_updater] rollback: brak backup folderu {backup_path}')
        return False
    target = _app_dir()
    try:
        # Kopiuj backup -> app dir nadpisujac. PRESERVE_PATHS nie tykamy
        # (DB, secret_key, vendor_config - nie zmienione przez update).
        for root, dirs, files in os.walk(backup_path):
            rel = os.path.relpath(root, backup_path)
            dirs[:] = [d for d in dirs if d not in PRESERVE_PATHS]
            dst_folder = target if rel == '.' else os.path.join(target, rel)
            os.makedirs(dst_folder, exist_ok=True)
            for fname in files:
                rel_path = fname if rel == '.' else os.path.join(rel, fname)
                if any(p in rel_path.replace('\\', '/') for p in PRESERVE_PATHS):
                    continue
                src_f = os.path.join(root, fname)
                dst_f = os.path.join(target, rel_path)
                try:
                    shutil.copy2(src_f, dst_f)
                except Exception as e:
                    print(f'[zip_updater] rollback copy {rel_path} failed: {e}')
        print(f'[zip_updater] rollback OK z {backup_path}')
        return True
    except Exception as e:
        print(f'[zip_updater] rollback FAILED: {e}')
        return False


def _extract_and_swap(zip_path: str, target_dir: str) -> Dict[str, Any]:
    """Rozpakuj zip do tmp, potem przenies pliki do target_dir.

    NIE nadpisuje plikow z PRESERVE_PATHS (DB, licencja, .env, uploady itd).
    Atomiczna podmiana: extract do tmp -> move file po file.
    """
    result = {'ok': False, 'files_updated': 0, 'files_skipped': 0, 'error': ''}
    try:
        with tempfile.TemporaryDirectory(prefix='akces_update_') as tmpdir:
            # Rozpakuj zip
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(tmpdir)
            # Czesto zip ma root folder akces-hub-X.Y.Z/, znajdz prawdziwy root
            entries = os.listdir(tmpdir)
            if len(entries) == 1 and os.path.isdir(os.path.join(tmpdir, entries[0])):
                src_root = os.path.join(tmpdir, entries[0])
            else:
                src_root = tmpdir
            # Przenies pliki, pomijajac PRESERVE_PATHS
            for root, dirs, files in os.walk(src_root):
                rel = os.path.relpath(root, src_root)
                # Pomin foldery PRESERVE (in-place modify dirs przy os.walk)
                dirs[:] = [d for d in dirs if d not in PRESERVE_PATHS]
                # Utworz docelowy folder
                dst_folder = target_dir if rel == '.' else os.path.join(target_dir, rel)
                os.makedirs(dst_folder, exist_ok=True)
                for fname in files:
                    rel_path = fname if rel == '.' else os.path.join(rel, fname)
                    # Pomin pliki PRESERVE (po relatywnej sciezce)
                    if any(p in rel_path.replace('\\', '/') for p in PRESERVE_PATHS):
                        result['files_skipped'] += 1
                        continue
                    src_f = os.path.join(root, fname)
                    dst_f = os.path.join(target_dir, rel_path)
                    try:
                        shutil.copy2(src_f, dst_f)
                        result['files_updated'] += 1
                    except Exception as e:
                        print(f'[zip_updater] copy {rel_path} failed: {e}')
            result['ok'] = True
            return result
    except Exception as e:
        result['error'] = str(e)[:300]
        return result


def install_update(zip_path: str, signature_hex: str = '') -> Dict[str, Any]:
    """Pelny proces install: verify -> backup -> extract -> swap.

    NIE restartuje procesu — to robi caller (po wyslaniu response do browsera).

    Args:
        zip_path: sciezka do pobranego .zip
        signature_hex: HMAC-SHA256 hex (puste = pomin weryfikacje TYLKO dla testow)

    Returns:
        dict: ok, files_updated, backup_path, error
    """
    result = {'ok': False, 'files_updated': 0, 'backup_path': '', 'db_backup_path': '', 'rollback_done': False, 'error': ''}
    if not os.path.isfile(zip_path):
        result['error'] = f'Zip nie istnieje: {zip_path}'
        return result

    # 1. Weryfikacja podpisu (jesli podane)
    if signature_hex:
        try:
            from modules.license import _load_license_secret
            secret = _load_license_secret()
            if not secret:
                result['error'] = 'Brak LICENSE_SECRET — nie moge zweryfikowac zip-a'
                return result
            if isinstance(secret, str):
                secret = secret.encode('utf-8')
            if not verify_zip_signature(zip_path, signature_hex, secret):
                result['error'] = 'Nieprawidlowy podpis HMAC zip-a — atak?'
                return result
        except Exception as e:
            result['error'] = f'Weryfikacja zip-a: {e}'
            return result

    # 2. Backup kodu
    backup_dir = os.path.join(_app_dir(), 'backups', 'updates')
    backup_path = _make_backup(backup_dir)
    if not backup_path:
        result['error'] = 'Backup pre-update nie powiodl sie — przerywam'
        return result
    result['backup_path'] = backup_path

    # 2b. v1.0.95 (S5): Backup DB osobno (atomicznie przez VACUUM INTO).
    # Bez tego klient nie ma jak rollback DB jak nowa wersja ma niekompatybilna
    # migracje (np ALTER TABLE DROP COLUMN). DB jest w PRESERVE_PATHS wiec
    # update nie nadpisze - ale migracja w nowym kodzie ja zmodyfikuje.
    db_backup_path = _backup_database(backup_dir)
    if db_backup_path:
        result['db_backup_path'] = db_backup_path
    else:
        # Brak DB backup nie blokuje update (np fresh install bez DB),
        # ale logujemy warning - bez backup DB rollback jest ryzykowny.
        print('[zip_updater] WARN: brak DB backup - rollback DB nie bedzie mozliwy')

    # 3. Extract + swap (z auto-rollback przy fail)
    swap = _extract_and_swap(zip_path, _app_dir())
    if not swap['ok']:
        # v1.0.95 (W3): AUTO-ROLLBACK kodu z backupu.
        # Inaczej klient zostaje z half-broken apka (czesc plikow nowa,
        # czesc stara - nie startuje albo crashuje na importach).
        print(f"[zip_updater] swap FAILED ({swap.get('error', '?')}) - probuje rollback z {backup_path}")
        rolled_back = _rollback_from_backup(backup_path)
        result['rollback_done'] = rolled_back
        if rolled_back:
            result['error'] = f"Swap failed: {swap.get('error', '?')[:200]} - ROLLBACK OK (kod przywrocony)"
        else:
            result['error'] = f"Swap failed: {swap.get('error', '?')[:200]} - ROLLBACK TEZ FAILED, klient w half-broken state. Backup: {backup_path}"
        return result
    result['files_updated'] = swap['files_updated']

    # 4. Zapisz info o update do config (dla bannera "zaktualizowano")
    try:
        from modules.database import set_config
        from datetime import datetime
        new_version = _get_current_version()  # po swapie powinno juz byc nowe
        set_config('last_update_info', json.dumps({
            'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'version': new_version,
            'method': 'zip',
            'files_updated': swap['files_updated'],
            'seen': False,
        }))
    except Exception:
        pass

    result['ok'] = True
    return result


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    """v1.0.96 (W4): Health check przed ubiciem starego procesu.

    Próbuje TCP connect do nowego procesu co 250ms az do timeout.
    Zwraca True gdy nowy proces zaczal nasluchiwac, False gdy timeout.

    Bez tego klient widzial 'aplikacja nie dziala' przez 5-10s na Windows
    bo Defender skanowal swiezo ekstraktowany pythonw.exe a my zabilismy
    stary po 1s nie sprawdzajac.
    """
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            time.sleep(0.25)
    return False


def restart_python_process():
    """Restart obecnego procesu Pythona (Windows + Linux).

    KLUCZOWE: NIE uzywaj 'os._exit(0)' + flagi pliku — to wymaga watchdog'a
    bat-owego ktorego klient nie ma. Wtedy proces UMIERA na zawsze.

    ZAMIAST: uzyj subprocess.Popen() zeby odpalic NOWY proces Pythona
    z tym samym app.py, potem os._exit(0) na starym. Nowy proces zyje
    niezaleznie od konsoli (DETACHED_PROCESS na Windows).

    v1.0.96 (W4): port health-check przed os._exit, zeby uniknac
    "aplikacja nie dziala" przez 5-10s na Windows (Defender skanuje
    pythonw.exe nowo ekstraktowany).

    Wywoluj W TLE PO wyslaniu response, np:
        threading.Thread(target=lambda: (time.sleep(2), restart_python_process()),
                         daemon=True).start()
    """
    import subprocess
    try:
        if sys.platform.startswith('win'):
            # Windows: odpal nowy pythonw.exe (silent, bez konsoli) z app.py
            # DETACHED_PROCESS (0x00000008) + CREATE_NEW_PROCESS_GROUP (0x00000200)
            # zeby nowy proces zyl po smierci obecnego.
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            python_exe = sys.executable
            # Jesli obecnie chodzimy z python.exe (konsola) -> uzyj pythonw.exe (cicho)
            if python_exe.lower().endswith('python.exe'):
                pythonw = python_exe[:-len('python.exe')] + 'pythonw.exe'
                if os.path.isfile(pythonw):
                    python_exe = pythonw
            app_py = os.path.join(_app_dir(), 'app.py')
            subprocess.Popen(
                [python_exe, app_py],
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                cwd=_app_dir(),
                close_fds=True,
            )
            # v1.0.96 (W4): czekaj max 15s aż nowy proces zacznie nasluchiwac.
            # Defender + pythonw.exe pierwszy start = 5-10s normalne.
            # Probuj 127.0.0.1 (bind=127.0.0.1 dla ZIP install z v1.0.94).
            ready = _wait_for_port('127.0.0.1', 5000, timeout=15.0)
            if not ready:
                # Sprobuj 0.0.0.0 binding (Pi/systemd warianty) - nie z Windows
                # ale jako fallback
                ready = _wait_for_port('localhost', 5000, timeout=3.0)
            if not ready:
                # Nowy proces sie nie odpalil w 18s. Lepiej zostawic stary zywy
                # niz zabic w slepo - klient zostalby z brokenem.
                print('[zip_updater] WARN: nowy proces nie nasluchuje po 18s - NIE zabijam starego')
                return
            print(f'[zip_updater] nowy proces gotowy, zabijam stary (PID {os.getpid()})')
            os._exit(0)
        else:
            # Linux/Mac: execv (replace in place) - atomic, nie ma race
            os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        print(f'[zip_updater] restart failed: {e}')
        # Fallback: spróbuj execv
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception:
            pass
