"""Testy dla modules.key_loader - priority chain ladowania klucza."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import key_loader  # noqa: E402


@pytest.fixture
def clean_env(monkeypatch):
    """Usun ENV var przed kazdym testem."""
    monkeypatch.delenv(key_loader.ENV_VAR_NAME, raising=False)


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Zmien wszystkie lokalizacje kluczy na tmp_path (nie dotykaj realnych)."""
    etc_path = tmp_path / 'etc_akces' / 'env.key'
    user_path = tmp_path / 'home_user' / '.akces' / 'env.key'
    legacy_path = tmp_path / 'app_dir' / '.env.key'
    legacy_path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(key_loader, 'ETC_KEY_PATH', etc_path)
    monkeypatch.setattr(key_loader, 'USER_KEY_PATH', user_path)
    monkeypatch.setattr(key_loader, 'legacy_key_path', lambda: legacy_path)

    return {
        'etc': etc_path,
        'user': user_path,
        'legacy': legacy_path,
    }


def _fake_key() -> str:
    """Deterministyczny klucz Fernet (32 bajty base64)."""
    return 'ZmFrZWZha2VmYWtlZmFrZWZha2VmYWtlZmFrZWZha2VmYWtlMDE='


def test_priority_env_wins_over_all_files(clean_env, isolated_paths, monkeypatch):
    """ENV var ma najwyzszy priorytet — nawet gdy pliki istnieja."""
    # Wsadz wszystkie pliki z roznymi kluczami
    isolated_paths['etc'].parent.mkdir(parents=True, exist_ok=True)
    isolated_paths['user'].parent.mkdir(parents=True, exist_ok=True)
    isolated_paths['etc'].write_text('KEY_FROM_ETC')
    isolated_paths['user'].write_text('KEY_FROM_USER')
    isolated_paths['legacy'].write_text('KEY_FROM_LEGACY')

    monkeypatch.setenv(key_loader.ENV_VAR_NAME, 'KEY_FROM_ENV_VAR')

    key, source = key_loader.load_encryption_key(auto_generate=False, migrate_legacy=False)

    assert key == b'KEY_FROM_ENV_VAR'
    assert source == f'env:{key_loader.ENV_VAR_NAME}'


def test_priority_etc_wins_over_user_and_legacy(clean_env, isolated_paths):
    """Gdy brak ENV — /etc/akces/env.key wygrywa z ~/.akces i legacy."""
    isolated_paths['etc'].parent.mkdir(parents=True, exist_ok=True)
    isolated_paths['user'].parent.mkdir(parents=True, exist_ok=True)
    isolated_paths['etc'].write_text('KEY_FROM_ETC')
    isolated_paths['user'].write_text('KEY_FROM_USER')
    isolated_paths['legacy'].write_text('KEY_FROM_LEGACY')

    key, source = key_loader.load_encryption_key(auto_generate=False, migrate_legacy=False)

    assert key == b'KEY_FROM_ETC'
    assert source == str(isolated_paths['etc'])


def test_priority_user_wins_over_legacy(clean_env, isolated_paths):
    """Gdy brak ENV i /etc — ~/.akces/env.key wygrywa z legacy."""
    isolated_paths['user'].parent.mkdir(parents=True, exist_ok=True)
    isolated_paths['user'].write_text('KEY_FROM_USER')
    isolated_paths['legacy'].write_text('KEY_FROM_LEGACY')

    key, source = key_loader.load_encryption_key(auto_generate=False, migrate_legacy=False)

    assert key == b'KEY_FROM_USER'
    assert source == str(isolated_paths['user'])


def test_legacy_fallback_when_others_missing(clean_env, isolated_paths, capsys):
    """Legacy `.env.key` dziala jako fallback gdy nic innego nie ma."""
    isolated_paths['legacy'].write_text('KEY_FROM_LEGACY')

    key, source = key_loader.load_encryption_key(auto_generate=False, migrate_legacy=True)

    assert key == b'KEY_FROM_LEGACY'
    assert 'legacy' in source.lower()

    captured = capsys.readouterr()
    # Powinien byc warning o legacy lokalizacji
    assert 'legacy .env.key' in captured.out.lower() or 'legacy' in captured.out.lower()


def test_legacy_auto_migration_copies_to_user_home(clean_env, isolated_paths):
    """Legacy -> kopiuje do ~/.akces/env.key (gdy tam brak)."""
    isolated_paths['legacy'].write_text('KEY_FROM_LEGACY_AUTO_MIGRATE')

    assert not isolated_paths['user'].exists()

    key, _ = key_loader.load_encryption_key(auto_generate=False, migrate_legacy=True)
    assert key == b'KEY_FROM_LEGACY_AUTO_MIGRATE'

    # Sprawdz ze migracja sie wykonala
    assert isolated_paths['user'].exists(), 'Legacy key should be migrated to user home'
    assert isolated_paths['user'].read_text().strip() == 'KEY_FROM_LEGACY_AUTO_MIGRATE'


def test_missing_key_raises_clear_error(clean_env, isolated_paths):
    """Brak klucza + auto_generate=False -> RuntimeError z jasnym komunikatem."""
    # Nie twoz zadnych plikow
    with pytest.raises(RuntimeError) as exc_info:
        key_loader.load_encryption_key(auto_generate=False, migrate_legacy=False)

    msg = str(exc_info.value)
    # Komunikat musi zawierac liste sprawdzanych lokalizacji
    assert key_loader.ENV_VAR_NAME in msg
    assert 'env.key' in msg
    # Powinien odsylac do dokumentacji
    assert 'DEPLOYMENT' in msg or 'docs' in msg


def test_auto_generate_creates_new_key(clean_env, isolated_paths, monkeypatch):
    """auto_generate=True + brak klucza -> wygeneruj i zapisz."""
    # Mock _generate_key zeby nie wymagac cryptography w testach
    monkeypatch.setattr(key_loader, '_generate_key', lambda: _fake_key())

    assert not isolated_paths['etc'].exists()
    assert not isolated_paths['user'].exists()
    assert not isolated_paths['legacy'].exists()

    key, source = key_loader.load_encryption_key(auto_generate=True, migrate_legacy=False)

    assert key == _fake_key().encode()
    assert source.startswith('generated:')
    # Nowy klucz zapisany gdzies (najprawdopodobniej user lub legacy — /etc wymaga root)
    written_files = [p for p in (isolated_paths['etc'], isolated_paths['user'], isolated_paths['legacy']) if p.exists()]
    assert len(written_files) >= 1, 'Generated key should be written somewhere'


def test_empty_file_treated_as_missing(clean_env, isolated_paths):
    """Pusty plik klucza -> traktowany jako brak, fallback dziala."""
    isolated_paths['etc'].parent.mkdir(parents=True, exist_ok=True)
    isolated_paths['etc'].write_text('')  # pusty
    isolated_paths['user'].parent.mkdir(parents=True, exist_ok=True)
    isolated_paths['user'].write_text('KEY_FROM_USER')

    key, source = key_loader.load_encryption_key(auto_generate=False, migrate_legacy=False)

    assert key == b'KEY_FROM_USER'
    assert source == str(isolated_paths['user'])


def test_env_var_stripped_of_whitespace(clean_env, isolated_paths, monkeypatch):
    """ENV var z whitespace na koncu jest strippowany."""
    monkeypatch.setenv(key_loader.ENV_VAR_NAME, '  KEY_FROM_ENV_WITH_SPACES  \n')

    key, source = key_loader.load_encryption_key(auto_generate=False, migrate_legacy=False)

    assert key == b'KEY_FROM_ENV_WITH_SPACES'
    assert source == f'env:{key_loader.ENV_VAR_NAME}'


def test_env_var_empty_falls_through(clean_env, isolated_paths, monkeypatch):
    """Pusta ENV var -> dalej fallback do plikow."""
    monkeypatch.setenv(key_loader.ENV_VAR_NAME, '')
    isolated_paths['user'].parent.mkdir(parents=True, exist_ok=True)
    isolated_paths['user'].write_text('KEY_FROM_USER')

    key, source = key_loader.load_encryption_key(auto_generate=False, migrate_legacy=False)

    assert key == b'KEY_FROM_USER'
    assert 'env.key' in source


def test_return_tuple_format(clean_env, isolated_paths, monkeypatch):
    """load_encryption_key zwraca (bytes, str) zgodnie z kontraktem."""
    monkeypatch.setenv(key_loader.ENV_VAR_NAME, 'SOME_KEY')
    result = key_loader.load_encryption_key(auto_generate=False, migrate_legacy=False)

    assert isinstance(result, tuple)
    assert len(result) == 2
    key, source = result
    assert isinstance(key, bytes)
    assert isinstance(source, str)
