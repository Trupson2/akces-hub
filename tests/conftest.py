"""Pytest fixtures for AKCES HUB tests."""
import sys
import os
import sqlite3
import pytest

# Dodaj root projektu do PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def test_db(tmp_path):
    """Tworzy tymczasową bazę SQLite z podstawowym schematem."""
    db_path = str(tmp_path / 'test.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')

    # Minimalne tabele potrzebne do testów
    conn.execute('''CREATE TABLE IF NOT EXISTS config (
        klucz TEXT PRIMARY KEY,
        wartosc TEXT
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS licenses_issued (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        license_key TEXT UNIQUE,
        client_name TEXT,
        plan TEXT DEFAULT 'pro',
        hwid TEXT DEFAULT '',
        expires INTEGER DEFAULT 0,
        expires_date TEXT,
        active INTEGER DEFAULT 1,
        last_heartbeat TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS uzytkownicy (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        login TEXT UNIQUE,
        haslo TEXT,
        rola TEXT DEFAULT 'user'
    )''')
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def app_client():
    """Flask test client — wymaga pełnej app (wolniejszy)."""
    try:
        os.environ['AKCES_TEST_MODE'] = '1'
        from app import app
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        with app.test_client() as client:
            yield client
    except Exception:
        pytest.skip('Flask app nie załadowana (brak zależności)')
    finally:
        os.environ.pop('AKCES_TEST_MODE', None)
