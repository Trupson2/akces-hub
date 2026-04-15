"""
Key loader - bezpieczne ladowanie klucza szyfrowania AKCES Hub.

Priorytet (od najbezpieczniejszego do dev fallback):
1. Zmienna srodowiskowa AKCES_ENCRYPTION_KEY (systemd EnvironmentFile)
2. /etc/akces/env.key (chmod 600, root + user aplikacji) - produkcja
3. ~/.akces/env.key (chmod 600) - user-space fallback (dev / brak root)
4. <app_dir>/.env.key - legacy (DEPRECATED, warning + auto-migracja)

Jesli klucz nie istnieje w zadnej lokalizacji, `load_encryption_key` moze
go wygenerowac (auto_generate=True) i zapisac w najbardziej bezpiecznym
dostepnym miejscu (preferowane ~/.akces/env.key).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Optional, Tuple


# Stale lokalizacji (latwe do mockowania w testach)
ENV_VAR_NAME = 'AKCES_ENCRYPTION_KEY'
ETC_KEY_PATH = Path('/etc/akces/env.key')
USER_KEY_PATH = Path.home() / '.akces' / 'env.key'


def _app_dir() -> Path:
    """Katalog aplikacji (rodzic folderu modules/)."""
    return Path(__file__).resolve().parent.parent


def legacy_key_path() -> Path:
    """Stara lokalizacja `.env.key` w folderze aplikacji (DEPRECATED)."""
    return _app_dir() / '.env.key'


def _read_key_file(path: Path) -> Optional[str]:
    """Zwraca zawartosc pliku (stripped) lub None jesli brak / blad."""
    try:
        if not path.exists() or not path.is_file():
            return None
        content = path.read_text(encoding='utf-8').strip()
        return content or None
    except (OSError, PermissionError):
        return None


def _fix_permissions(path: Path) -> None:
    """Ustaw chmod 600 na pliku klucza (best-effort, Unix)."""
    try:
        current = os.stat(str(path)).st_mode
        # Jesli world-readable lub group-readable — wymus 600.
        if current & (stat.S_IRWXG | stat.S_IRWXO):
            os.chmod(str(path), 0o600)
    except Exception:
        # Windows / brak uprawnien — ignoruj cicho.
        pass


def _ensure_parent_dir(path: Path) -> bool:
    """Tworzy katalog rodzica jesli nie istnieje. Zwraca True przy sukcesie."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return True
    except (OSError, PermissionError):
        return False


def _write_key(path: Path, key: str) -> bool:
    """Zapisuje klucz do pliku z chmod 600. True przy sukcesie."""
    if not _ensure_parent_dir(path):
        return False
    try:
        path.write_text(key, encoding='utf-8')
        try:
            os.chmod(str(path), 0o600)
        except Exception:
            pass
        return True
    except (OSError, PermissionError):
        return False


def _generate_key() -> str:
    """Generuje nowy klucz Fernet. Wymaga `cryptography`."""
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError(
            'cryptography nie zainstalowana - nie moge wygenerowac klucza. '
            'pip install cryptography'
        ) from exc
    return Fernet.generate_key().decode()


def load_encryption_key(
    auto_generate: bool = True,
    migrate_legacy: bool = True,
) -> Tuple[bytes, str]:
    """Zwraca (key_bytes, source_path) uzywajac priority chain.

    Args:
        auto_generate: jesli True i klucza nigdzie nie ma, wygeneruj i zapisz
            w ~/.akces/env.key (fallback) lub /etc/akces/env.key jesli zapisywalne.
        migrate_legacy: jesli znajdziemy legacy `.env.key` w folderze aplikacji,
            logujemy warning (zachowanie starych instalacji).

    Returns:
        (key_as_bytes, source_label) gdzie source_label to jedna z:
        - 'env:AKCES_ENCRYPTION_KEY'
        - '/etc/akces/env.key'
        - '<user_home>/.akces/env.key'
        - '<app_dir>/.env.key (legacy)'
        - 'generated:<path>'

    Raises:
        RuntimeError: brak klucza nigdzie i auto_generate=False, lub brak
            uprawnien do zapisu w zadnej lokalizacji.
    """
    # 1. Environment variable (najbezpieczniejsze - systemd EnvironmentFile).
    env_key = os.environ.get(ENV_VAR_NAME, '').strip()
    if env_key:
        return env_key.encode(), f'env:{ENV_VAR_NAME}'

    # 2. /etc/akces/env.key (produkcja).
    etc_key = _read_key_file(ETC_KEY_PATH)
    if etc_key:
        _fix_permissions(ETC_KEY_PATH)
        return etc_key.encode(), str(ETC_KEY_PATH)

    # 3. ~/.akces/env.key (user-space).
    user_key = _read_key_file(USER_KEY_PATH)
    if user_key:
        _fix_permissions(USER_KEY_PATH)
        return user_key.encode(), str(USER_KEY_PATH)

    # 4. Legacy: <app_dir>/.env.key.
    legacy_path = legacy_key_path()
    legacy_value = _read_key_file(legacy_path)
    if legacy_value:
        if migrate_legacy:
            print(
                '[WARN] Uzyto legacy .env.key z folderu aplikacji. '
                'Przenies klucz do /etc/akces/env.key (produkcja) lub '
                '~/.akces/env.key (dev). Patrz docs/DEPLOYMENT.md'
            )
            # Best-effort migracja: probuj skopiowac do user-home jesli tam brak.
            if not USER_KEY_PATH.exists() and _write_key(USER_KEY_PATH, legacy_value):
                print(f'[OK] Skopiowano klucz do {USER_KEY_PATH} (mozesz usunac .env.key)')
        _fix_permissions(legacy_path)
        return legacy_value.encode(), f'{legacy_path} (legacy)'

    # 5. Brak klucza nigdzie.
    if not auto_generate:
        raise RuntimeError(
            'Nie znaleziono klucza szyfrowania AKCES Hub.\n'
            f'Sprawdzane lokalizacje (w kolejnosci):\n'
            f'  1. env var {ENV_VAR_NAME}\n'
            f'  2. {ETC_KEY_PATH}\n'
            f'  3. {USER_KEY_PATH}\n'
            f'  4. {legacy_path} (legacy)\n'
            'Utworz plik z kluczem: patrz docs/DEPLOYMENT.md'
        )

    # Wygeneruj nowy klucz i zapisz w pierwszym zapisywalnym miejscu.
    new_key = _generate_key()

    # Preferowana kolejnosc zapisu: /etc/akces -> ~/.akces -> legacy (last resort).
    write_candidates = [ETC_KEY_PATH, USER_KEY_PATH, legacy_path]
    for candidate in write_candidates:
        if _write_key(candidate, new_key):
            print(f'[OK] Wygenerowano nowy klucz szyfrowania: {candidate}')
            if candidate == legacy_path:
                print('[WARN] Zapisano w legacy folderze. Przenies klucz do /etc/akces/env.key.')
            return new_key.encode(), f'generated:{candidate}'

    raise RuntimeError(
        'Nie moge zapisac nowego klucza w zadnej lokalizacji. '
        'Brak uprawnien do /etc/akces i do $HOME/.akces.'
    )
