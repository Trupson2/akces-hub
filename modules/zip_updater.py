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
    'backups',
    'logs',
    'venv',
    'installer/python',   # embedded Python folder (380 MB, NIE nadpisuj)
    'python',             # backward compat
    '__pycache__',
    # vendor_config.json NIE jest w preserve - nadpisuje sie z aktualnej wersji
    # zeby klient dostawal nowe vendor credentials gdy Adrian zmieni token.
    # Wymaga: app.py przy starcie reloaduje vendor_config.json do DB (TODO).
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
    result = {'ok': False, 'files_updated': 0, 'backup_path': '', 'error': ''}
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

    # 2. Backup
    backup_dir = os.path.join(_app_dir(), 'backups', 'updates')
    backup_path = _make_backup(backup_dir)
    if not backup_path:
        result['error'] = 'Backup pre-update nie powiodl sie — przerywam'
        return result
    result['backup_path'] = backup_path

    # 3. Extract + swap
    swap = _extract_and_swap(zip_path, _app_dir())
    if not swap['ok']:
        result['error'] = f"Swap failed: {swap.get('error', '?')}"
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


def restart_python_process():
    """Restart obecnego procesu Pythona (Windows-friendly).

    Wywolaj W TLE PO wyslaniu response, np:
        threading.Thread(target=lambda: (time.sleep(2), restart_python_process()),
                         daemon=True).start()
    """
    try:
        # Windows: nie wykorzystuj execv (nie zawsze dziala z waitress + threads).
        # Lepiej: zapisz "restart pending", a wrapper bat-owy/systemd zauwazy
        # exit code i sam restartuje. Jesli wrappera nie ma — uzyj execv.
        if sys.platform.startswith('win'):
            # Sprawdz czy jest START.bat wrapper (klient Windows)
            wrapper = os.path.join(_app_dir(), 'START.bat')
            if os.path.isfile(wrapper):
                # Zapisz flage — START.bat watch loop ja zauwazy i zrestartuje
                with open(os.path.join(_app_dir(), '.restart_pending'), 'w') as f:
                    f.write(str(int(time.time())))
                # Zakoncz obecny proces — wrapper bat-owy odpali nowy
                os._exit(0)
            else:
                # Brak wrappera — execv (sam Python)
                os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            # Linux/Mac: execv
            os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        print(f'[zip_updater] restart failed: {e}')
        os._exit(0)  # fallback — wrapper musi odpalic
