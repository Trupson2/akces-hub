"""Testy bazy danych — config, licencje."""
import sqlite3
import pytest


def test_config_set_get(test_db):
    """Test zapisu i odczytu konfiguracji."""
    conn = test_db
    conn.execute('INSERT OR REPLACE INTO config (klucz, wartosc) VALUES (?, ?)',
                 ('test_key', 'test_value'))
    conn.commit()

    row = conn.execute('SELECT wartosc FROM config WHERE klucz = ?', ('test_key',)).fetchone()
    assert row is not None
    assert row['wartosc'] == 'test_value'


def test_config_update(test_db):
    """Test aktualizacji konfiguracji."""
    conn = test_db
    conn.execute('INSERT OR REPLACE INTO config (klucz, wartosc) VALUES (?, ?)',
                 ('key1', 'value1'))
    conn.execute('INSERT OR REPLACE INTO config (klucz, wartosc) VALUES (?, ?)',
                 ('key1', 'value2'))
    conn.commit()

    row = conn.execute('SELECT wartosc FROM config WHERE klucz = ?', ('key1',)).fetchone()
    assert row['wartosc'] == 'value2'


def test_licenses_issued_insert(test_db):
    """Test dodawania licencji do tabeli licenses_issued."""
    conn = test_db
    conn.execute('''INSERT INTO licenses_issued
        (license_key, client_name, plan, hwid, active)
        VALUES (?, ?, ?, ?, 1)''',
        ('AKCES-P123-4567-89AB-CDEF', 'Test Client', 'pro', 'abc123'))
    conn.commit()

    row = conn.execute('SELECT * FROM licenses_issued WHERE license_key = ?',
                       ('AKCES-P123-4567-89AB-CDEF',)).fetchone()
    assert row is not None
    assert row['client_name'] == 'Test Client'
    assert row['plan'] == 'pro'
    assert row['active'] == 1


def test_licenses_issued_unique_key(test_db):
    """Test: duplikat license_key powinien rzucić błąd."""
    conn = test_db
    conn.execute('INSERT INTO licenses_issued (license_key, client_name) VALUES (?, ?)',
                 ('AKCES-DUPL-ICAT-EKEY-TEST', 'Client 1'))
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute('INSERT INTO licenses_issued (license_key, client_name) VALUES (?, ?)',
                     ('AKCES-DUPL-ICAT-EKEY-TEST', 'Client 2'))


def test_licenses_auto_registration(test_db):
    """Test auto-rejestracji: INSERT przy pierwszym heartbeat."""
    conn = test_db
    key = 'AKCES-ANEW-KEY0-TEST-0001'
    hwid = 'test_hwid_12345'

    # Sprawdź że klucz nie istnieje
    row = conn.execute('SELECT * FROM licenses_issued WHERE license_key = ?', (key,)).fetchone()
    assert row is None

    # Auto-register (symulacja logiki z api_license_verify)
    conn.execute('''INSERT INTO licenses_issued
        (license_key, client_name, plan, hwid, active, created_at)
        VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)''',
        (key, 'Auto-registered', 'pro', hwid))
    conn.commit()

    # Sprawdź że klucz istnieje
    row = conn.execute('SELECT * FROM licenses_issued WHERE license_key = ?', (key,)).fetchone()
    assert row is not None
    assert row['client_name'] == 'Auto-registered'
    assert row['hwid'] == hwid
    assert row['active'] == 1


def test_licenses_update_whitelist(test_db):
    """Test: UPDATE z whitelistą kolumn."""
    conn = test_db
    conn.execute('INSERT INTO licenses_issued (license_key, client_name, plan, active) VALUES (?, ?, ?, 1)',
                 ('AKCES-TEST-UPDT-KEY0-0001', 'Test', 'pro'))
    conn.commit()

    # Dozwolone kolumny
    _ALLOWED_COLS = {'plan', 'expires_date', 'expires', 'active'}

    updates = ['plan = ?', 'active = ?']
    params = ['business', 0, 'AKCES-TEST-UPDT-KEY0-0001']

    # Walidacja
    for u in updates:
        col_name = u.split(' ')[0]
        assert col_name in _ALLOWED_COLS

    conn.execute(f'UPDATE licenses_issued SET {", ".join(updates)} WHERE license_key = ?', params)
    conn.commit()

    row = conn.execute('SELECT plan, active FROM licenses_issued WHERE license_key = ?',
                       ('AKCES-TEST-UPDT-KEY0-0001',)).fetchone()
    assert row['plan'] == 'business'
    assert row['active'] == 0


def test_wal_mode(test_db):
    """Test: WAL mode jest włączony."""
    conn = test_db
    mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
    assert mode == 'wal'
