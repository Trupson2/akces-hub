"""
Database module - obsługa bazy danych SQLite
"""

import sqlite3
import os
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path

# WAŻNE: Baza danych zawsze w katalogu aplikacji (nie w CWD!)
_APP_DIR = Path(__file__).parent.parent  # Katalog główny aplikacji
DATABASE = str(_APP_DIR / 'akces_hub.db')

# KOMBAJN MODE: Connection pool + thread safety
_connection_pool = {}
_pool_lock = threading.Lock()

def get_db():
    """
    Zwraca połączenie do bazy z timeoutem i WAL mode.
    KOMBAJN MODE: Używa connection pooling dla każdego wątku.
    """
    thread_id = threading.get_ident()
    
    # Sprawdź czy wątek ma już connection
    with _pool_lock:
        if thread_id in _connection_pool:
            conn = _connection_pool[thread_id]
            try:
                conn.execute('SELECT 1')
                return conn
            except:
                # Connection zamknięty/martwy — usuń z puli
                del _connection_pool[thread_id]
    
    # Stwórz nowe connection
    conn = sqlite3.connect(DATABASE, timeout=60.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    
    # Tryb WAL - bezpieczny z auto-checkpoint
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    conn.execute('PRAGMA synchronous=NORMAL')  # NORMAL jest bezpieczne z WAL mode
    conn.execute('PRAGMA wal_autocheckpoint=100')  # checkpoint co 100 stron
    conn.execute('PRAGMA cache_size=-32000')   # 32MB cache
    conn.execute('PRAGMA temp_store=MEMORY')
    # NIE robimy integrity_check przy każdym połączeniu - to skanuje CAŁĄ bazę
    # i blokuje inne operacje, powodując "database is locked" 500 errors
    
    # Dodaj do poola
    with _pool_lock:
        _connection_pool[thread_id] = conn
    
    return conn

def close_connection_pool():
    """Zamyka wszystkie połączenia w poolu (przy shutdown)"""
    with _pool_lock:
        for conn in _connection_pool.values():
            try:
                conn.close()
            except:
                pass
        _connection_pool.clear()

def retry_db_operation(func, max_retries=5, delay=0.5):
    """
    Wykonuje operację bazodanową z retry w przypadku database locked.
    
    Args:
        func: Funkcja do wykonania (lambda lub callable)
        max_retries: Maksymalna liczba prób
        delay: Opóźnienie między próbami w sekundach
    
    Returns:
        Wynik funkcji lub None w przypadku błędu
    """
    import time
    
    for attempt in range(max_retries):
        try:
            return func()
        except sqlite3.OperationalError as e:
            if 'database is locked' in str(e) and attempt < max_retries - 1:
                print(f"[WARN] Database locked, retry {attempt+1}/{max_retries}...")
                time.sleep(delay * (attempt + 1))  # Zwiększające się opóźnienie
                continue
            else:
                print(f"[ERR] Database error after {attempt+1} attempts: {e}")
                raise
        except Exception as e:
            print(f"[ERR] Unexpected error: {e}")
            raise
    
    return None

def init_db():
    """Inicjalizuje bazę danych - tworzy tabele jeśli nie istnieją"""
    with get_db() as conn:
        # Tabela palet (NOWA!)
        conn.execute('''CREATE TABLE IF NOT EXISTS palety (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT DEFAULT '',
            dostawca TEXT DEFAULT '',
            cena_zakupu REAL DEFAULT 0,
            ilosc_produktow INTEGER DEFAULT 0,
            data_zakupu DATE DEFAULT CURRENT_DATE,
            notatki TEXT DEFAULT '',
            regal TEXT DEFAULT '',
            data_dodania TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            typ TEXT DEFAULT 'paleta'
        )''')

        # Tabela produktów (Magazynier)
        conn.execute('''CREATE TABLE IF NOT EXISTS produkty (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ean TEXT,
            asin TEXT DEFAULT '',
            nazwa TEXT NOT NULL,
            krotki_tytul TEXT DEFAULT '',
            opis_ai TEXT DEFAULT '',
            ilosc INTEGER DEFAULT 0,
            cena_netto REAL DEFAULT 0,
            cena_brutto REAL DEFAULT 0,
            cena_allegro REAL DEFAULT 0,
            lokalizacja TEXT DEFAULT '',
            regal TEXT DEFAULT '',
            paleta_id INTEGER DEFAULT NULL,
            paleta TEXT DEFAULT '',
            dostawca TEXT DEFAULT '',
            kategoria TEXT DEFAULT 'inne',
            zdjecie_url TEXT DEFAULT '',
            stan TEXT DEFAULT 'Nowy',
            status TEXT DEFAULT 'magazyn',
            data_dodania TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            data_sprzedazy TIMESTAMP DEFAULT NULL,
            FOREIGN KEY (paleta_id) REFERENCES palety(id)
        )''')
        
        # Tabela ofert Allegro (Paletomat)
        conn.execute('''CREATE TABLE IF NOT EXISTS oferty (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            allegro_id TEXT UNIQUE,
            produkt_id INTEGER,
            tytul TEXT NOT NULL,
            opis TEXT,
            cena REAL DEFAULT 0,
            ilosc INTEGER DEFAULT 1,
            status TEXT DEFAULT 'draft',
            wyswietlenia INTEGER DEFAULT 0,
            obserwujacych INTEGER DEFAULT 0,
            data_wystawienia TIMESTAMP,
            data_aktualizacji TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (produkt_id) REFERENCES produkty(id)
        )''')
        
        # FIX 2026-05: Tabela kalibracji cen Allegro — uczenie się z realnych
        # sprzedazy. Po kazdej finalizacji sprzedazy (status='wyslana') zapis
        # rekordu (nazwa, cena_sprzedazy, paleta_koszt_szt). Przy autowycenie
        # Gemini dostaje top-K najblizszych precedensow zamiast zgadywac
        # "z powietrza". UNIQUE(sprzedaz_id) zapobiega duplikatom przy
        # ponownej synchronizacji.
        conn.execute('''CREATE TABLE IF NOT EXISTS wycena_kalibracja (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            produkt_id INTEGER,
            sprzedaz_id INTEGER UNIQUE,
            nazwa TEXT NOT NULL,
            asin TEXT DEFAULT '',
            cena_brutto_snapshot REAL DEFAULT 0,
            cena_sprzedazy REAL NOT NULL,
            paleta_koszt_szt REAL DEFAULT 0,
            zrodlo TEXT DEFAULT 'sprzedaz',
            notatki TEXT DEFAULT '',
            data_kalibracji TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (produkt_id) REFERENCES produkty(id),
            FOREIGN KEY (sprzedaz_id) REFERENCES sprzedaze(id)
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_kalibracja_nazwa ON wycena_kalibracja(LOWER(nazwa))')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_kalibracja_asin ON wycena_kalibracja(asin)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_kalibracja_data ON wycena_kalibracja(data_kalibracji DESC)')

        # Backfill: jednorazowo (gdy tabela pusta) zapisz kalibracje z
        # istniejacych zakonczonych sprzedazy. paleta_koszt_szt = 0 dla
        # backfill — zostawiamy bezposrednim sprzedazom przyszlosci.
        try:
            _kalib_count = conn.execute('SELECT COUNT(*) FROM wycena_kalibracja').fetchone()[0]
            if _kalib_count == 0:
                conn.execute('''
                    INSERT OR IGNORE INTO wycena_kalibracja
                    (produkt_id, sprzedaz_id, nazwa, asin, cena_brutto_snapshot,
                     cena_sprzedazy, paleta_koszt_szt, zrodlo, data_kalibracji)
                    SELECT s.produkt_id, s.id,
                           COALESCE(p.nazwa, s.nazwa, ''),
                           COALESCE(p.asin, ''),
                           COALESCE(p.cena_brutto, 0),
                           s.cena, 0, 'sprzedaz_backfill',
                           s.data_sprzedazy
                    FROM sprzedaze s
                    LEFT JOIN produkty p ON s.produkt_id = p.id
                    WHERE s.status = 'wyslana'
                      AND s.cena IS NOT NULL AND s.cena > 0
                      AND COALESCE(p.nazwa, s.nazwa, '') != ''
                ''')
                _filled = conn.execute('SELECT COUNT(*) FROM wycena_kalibracja').fetchone()[0]
                if _filled > 0:
                    print(f"[init_db] Backfill wycena_kalibracja: {_filled} precedensow z historii sprzedazy")
        except Exception as _be:
            print(f"[init_db] Backfill wycena_kalibracja blad: {_be}")

        # Tabela sprzedaży
        conn.execute('''CREATE TABLE IF NOT EXISTS sprzedaze (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            allegro_order_id TEXT,
            oferta_id INTEGER,
            produkt_id INTEGER,
            nazwa TEXT DEFAULT '',
            cena REAL,
            ilosc INTEGER DEFAULT 1,
            kupujacy TEXT,
            adres TEXT DEFAULT '',
            status TEXT DEFAULT 'nowa',
            data_sprzedazy TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notified INTEGER DEFAULT 0,
            FOREIGN KEY (oferta_id) REFERENCES oferty(id),
            FOREIGN KEY (produkt_id) REFERENCES produkty(id)
        )''')
        
        # Migracja: dodaj kolumnę notified jeśli baza istniała przed tą zmianą
        try:
            conn.execute('ALTER TABLE sprzedaze ADD COLUMN notified INTEGER DEFAULT 0')
        except:
            pass  # Kolumna już istnieje
        # Migracja: dodaj kolumnę nazwa
        try:
            conn.execute("ALTER TABLE sprzedaze ADD COLUMN nazwa TEXT DEFAULT ''")
        except:
            pass
        # Migracja: dodaj kolumnę koszt_dostawy (delivery cost per line item)
        try:
            conn.execute("ALTER TABLE sprzedaze ADD COLUMN koszt_dostawy REAL DEFAULT 0")
        except:
            pass
        # Migracja: oryginalna waluta + cena (HUF/CZK/EUR -> zachowana, cena PLN przeliczona)
        try:
            conn.execute("ALTER TABLE sprzedaze ADD COLUMN cena_oryginalna REAL DEFAULT 0")
        except:
            pass
        try:
            conn.execute("ALTER TABLE sprzedaze ADD COLUMN waluta_oryginalna TEXT DEFAULT 'PLN'")
        except:
            pass
        try:
            conn.execute("ALTER TABLE sprzedaze ADD COLUMN kurs_pln REAL DEFAULT 1.0")
        except:
            pass
        # Migracja: dodaj kolumnę tytul do oferty jeśli brak (stare bazy)
        try:
            conn.execute("ALTER TABLE oferty ADD COLUMN tytul TEXT DEFAULT ''")
        except:
            pass
        # Migracja: dodaj kolumnę nazwa do sprzedaze (alias dla tytul oferty)
        try:
            conn.execute("ALTER TABLE oferty ADD COLUMN data_wystawienia TIMESTAMP")
        except:
            pass
        
        # Tabela scrapowanych produktów (Paletomat)
        conn.execute('''CREATE TABLE IF NOT EXISTS scraped (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asin TEXT UNIQUE NOT NULL,
            nazwa TEXT,
            cena_amazon REAL DEFAULT 0,
            waluta TEXT DEFAULT 'EUR',
            kategoria TEXT DEFAULT '',
            zdjecie_url TEXT DEFAULT '',
            wszystkie_zdjecia TEXT DEFAULT '',
            amazon_url TEXT DEFAULT '',
            status TEXT DEFAULT 'nowy',
            data_scrape TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        # Dodaj kolumny jeśli nie istnieją (migracje)
        migrations = [
            ('scraped', 'wszystkie_zdjecia', 'TEXT DEFAULT ""'),
            ('scraped', 'tytul_seo', 'TEXT DEFAULT ""'),
            ('scraped', 'opis_html', 'TEXT DEFAULT ""'),
            ('scraped', 'bullet_points', 'TEXT DEFAULT ""'),
            ('scraped', 'gpsr', 'TEXT DEFAULT ""'),  # ← NOWA! GPSR info
            ('scraped', 'ean', 'TEXT DEFAULT ""'),  # ← NOWA! EAN kod
            ('scraped', 'images', 'TEXT DEFAULT "[]"'),  # ← NOWA! JSON array zdjęć
            ('scraped', 'product_specs', 'TEXT DEFAULT ""'),  # ← NOWA! JSON specs z Amazon
            ('produkty', 'paleta_id', 'INTEGER DEFAULT NULL'),
            ('produkty', 'asin', 'TEXT DEFAULT ""'),
            ('produkty', 'regal', 'TEXT DEFAULT ""'),
            ('produkty', 'status', 'TEXT DEFAULT "magazyn"'),
            ('produkty', 'data_sprzedazy', 'TIMESTAMP DEFAULT NULL'),
            ('produkty', 'meta_title', 'TEXT DEFAULT ""'),  # ← KRYTYCZNA! AI-generated title
            ('produkty', 'parameters', 'TEXT DEFAULT ""'),  # JSON parameters from Gemini
            ('produkty', 'vendor', 'TEXT DEFAULT ""'),  # Dostawca/źródło
            ('produkty', 'service_notes', 'TEXT DEFAULT ""'),  # Notatki serwisowe
            ('produkty', 'images', 'TEXT DEFAULT "[]"'),  # ← NOWA! JSON array wszystkich zdjęć (max 8)
            ('produkty', 'sprzedano_offline', 'INTEGER DEFAULT 0'),  # ← NOWA! Ile sprzedano poza Allegro (bez statystyk)
            ('produkty', 'przychod_offline', 'REAL DEFAULT 0'),  # ← NOWA! Przychód ze sprzedaży offline
            ('sprzedaze', 'adres', 'TEXT DEFAULT ""'),
            ('sprzedaze', 'nazwa', 'TEXT DEFAULT ""'),  # ← NOWA! Nazwa produktu z Allegro
            ('sprzedaze', 'zdjecie_url', 'TEXT DEFAULT ""'),  # ← NOWA! Zdjęcie produktu
            ('sprzedaze', 'metoda_dostawy', 'TEXT DEFAULT ""'),  # ← NOWA! Metoda dostawy (InPost/DPD/DHL/Orlen)
            ('telegram_logs', 'message_id', 'INTEGER DEFAULT NULL'),
            ('palety', 'regal', 'TEXT DEFAULT ""'),
            ('palety', 'dostawca', 'TEXT DEFAULT ""'),  # ← WAŻNA! Dostawca palety
            ('palety', 'cena_zakupu_netto', 'REAL DEFAULT 0'),  # ← NOWA! Cena netto (stała)
            ('palety', 'ilosc_sztuk', 'INTEGER DEFAULT 0'),  # ← NOWA! Ilość sztuk zaimportowanych
            ('palety', 'dostarczona', 'INTEGER DEFAULT 0'),  # ← NOWA! Czy paleta dostarczona
            ('palety', 'koszt_jednostkowy', 'REAL DEFAULT 0'),  # ← NOWA! Stały koszt brutto/szt
            ('sztuki', 'zdjecie', 'TEXT DEFAULT ""'),  # ← NOWA! Zdjęcie sztuki (base64)
            ('produkty', 'kod_magazynowy', 'TEXT DEFAULT ""'),  # ← NOWA! Unikalny kod magazynowy MAG-XXXXX
            ('produkty', 'dla_siebie', 'INTEGER DEFAULT 0'),  # 1 = zatrzymane do uzytku wlasnego, blokuje wystawianie
            ('produkty', 'powod_zatrzymania', 'TEXT DEFAULT ""'),  # opcjonalna notatka czemu zatrzymane
        ]
        
        for table, column, coltype in migrations:
            try:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {coltype}')
            except:
                pass  # Kolumna już istnieje
        
        # Migracja: zamien angielskie nazwy na meta_title (polskie, AI-generated)
        try:
            updated = conn.execute("""
                UPDATE produkty SET nazwa = meta_title
                WHERE meta_title IS NOT NULL AND meta_title != '' AND LENGTH(meta_title) > 5
                AND nazwa != meta_title
            """).rowcount
            if updated:
                conn.commit()
                print(f"[DB] Zaktualizowano {updated} nazw produktow na meta_title")
        except:
            pass

        # Auto-generate kod_magazynowy for products that don't have one
        try:
            missing = conn.execute("SELECT id FROM produkty WHERE kod_magazynowy IS NULL OR kod_magazynowy = ''").fetchall()
            for row in missing:
                kod = f"MAG-{row[0]:05d}"
                conn.execute('UPDATE produkty SET kod_magazynowy = ? WHERE id = ?', (kod, row[0]))
            if missing:
                conn.commit()
        except:
            pass

        # Trigger: auto-generate kod_magazynowy na INSERT.
        # Prefix CONFIGUROWALNY: czyta z config.kod_magazynowy_prefix (default 'MAG').
        # Klient ustawia własny: set_config('kod_magazynowy_prefix', 'ZAG')
        # → wszystkie nowe produkty auto-dostają ZAG-00001, ZAG-00002...
        # Drop stary trigger jeśli istnieje (hardcoded MAG-) → recreate dynamic.
        try:
            conn.execute('DROP TRIGGER IF EXISTS auto_kod_magazynowy')
        except Exception:
            pass
        conn.execute('''CREATE TRIGGER IF NOT EXISTS auto_kod_magazynowy
            AFTER INSERT ON produkty
            FOR EACH ROW
            WHEN NEW.kod_magazynowy IS NULL OR NEW.kod_magazynowy = ''
            BEGIN
                UPDATE produkty SET kod_magazynowy =
                    COALESCE(
                        (SELECT wartosc FROM config WHERE klucz = 'kod_magazynowy_prefix' AND wartosc != ''),
                        'MAG'
                    ) || '-' || SUBSTR('00000' || NEW.id, -5)
                WHERE id = NEW.id;
            END''')

        # Trigger: auto-uppercase ASIN na INSERT
        conn.execute('''CREATE TRIGGER IF NOT EXISTS auto_asin_upper_insert
            AFTER INSERT ON produkty
            FOR EACH ROW
            WHEN NEW.asin IS NOT NULL AND NEW.asin != '' AND NEW.asin != UPPER(NEW.asin)
            BEGIN
                UPDATE produkty SET asin = UPPER(NEW.asin) WHERE id = NEW.id;
            END''')

        # Trigger: auto-uppercase ASIN na UPDATE
        conn.execute('''CREATE TRIGGER IF NOT EXISTS auto_asin_upper_update
            AFTER UPDATE OF asin ON produkty
            FOR EACH ROW
            WHEN NEW.asin IS NOT NULL AND NEW.asin != '' AND NEW.asin != UPPER(NEW.asin)
            BEGIN
                UPDATE produkty SET asin = UPPER(NEW.asin) WHERE id = NEW.id;
            END''')

        # Migracja: uppercase all existing ASINs
        conn.execute("UPDATE produkty SET asin = UPPER(asin) WHERE asin IS NOT NULL AND asin != '' AND asin != UPPER(asin)")
        # scraped has UNIQUE constraint on asin — delete lowercase duplicates first, then uppercase
        try:
            conn.execute("""DELETE FROM scraped WHERE rowid NOT IN (
                SELECT MIN(rowid) FROM scraped GROUP BY UPPER(asin)
            ) AND asin != UPPER(asin)""")
            conn.execute("UPDATE scraped SET asin = UPPER(asin) WHERE asin IS NOT NULL AND asin != '' AND asin != UPPER(asin)")
        except Exception:
            pass  # Safe to skip if already uppercase

        # Auto-fix: cena_zakupu w bazie = BRUTTO, netto = brutto / 1.23
        try:
            conn.execute('''
                UPDATE palety SET cena_zakupu_netto = ROUND(cena_zakupu / 1.23, 2)
                WHERE cena_zakupu > 0 AND (cena_zakupu_netto IS NULL OR cena_zakupu_netto = 0)
            ''')
        except:
            pass

        # === DEDUPLIKACJA SPRZEDAZY ===
        # Trzy miejsca w kodzie wstawialy do sprzedaze niezaleznie (sync_orders, telegram_bot,
        # inventory_utils) - kazde z wlasnym dedup, ktore sie nie chronily nawzajem.
        # Jednorazowy cleanup + UNIQUE INDEX zeby nikt wiecej nie wstawil duplikatu.
        try:
            # Usun duplikaty (allegro_order_id, nazwa) - zostaw najstarszy (najnizszy id)
            removed = conn.execute('''
                DELETE FROM sprzedaze
                WHERE id NOT IN (
                    SELECT MIN(id) FROM sprzedaze
                    WHERE allegro_order_id IS NOT NULL AND allegro_order_id != ''
                    GROUP BY allegro_order_id, COALESCE(nazwa, '')
                )
                AND allegro_order_id IS NOT NULL AND allegro_order_id != ''
            ''').rowcount
            if removed:
                conn.commit()
                print(f"[DB] Usunieto {removed} zduplikowanych sprzedaży")
        except Exception as _e:
            print(f"[DB] Cleanup sprzedaze skipped: {_e}")

        # UNIQUE INDEX (partial - tylko dla rekordów Allegro z order_id)
        try:
            conn.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS uniq_sprzedaze_order_nazwa
                ON sprzedaze(allegro_order_id, nazwa)
                WHERE allegro_order_id IS NOT NULL AND allegro_order_id != ''
            ''')
        except Exception as _e:
            print(f"[DB] Unique index sprzedaze skipped: {_e}")
        
        # Tabela logów Telegram
        conn.execute('''CREATE TABLE IF NOT EXISTS telegram_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            typ TEXT,
            wiadomosc TEXT,
            status TEXT DEFAULT 'sent',
            data TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        # Tabela kosztów operacyjnych
        conn.execute('''CREATE TABLE IF NOT EXISTS koszty (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT NOT NULL,
            kwota REAL NOT NULL,
            kategoria TEXT DEFAULT 'inne',
            data DATE DEFAULT CURRENT_DATE,
            notatka TEXT DEFAULT \'\',
            data_dodania TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        # Tabela sprzedaży prywatnych (poza Allegro)
        # Tabela opłat Allegro (billing) - koszty per oferta
        conn.execute('''CREATE TABLE IF NOT EXISTS allegro_billing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            billing_id TEXT UNIQUE,
            type_code TEXT,
            type_name TEXT,
            offer_id TEXT,
            offer_name TEXT,
            order_id TEXT,
            amount REAL,
            occurred_at TEXT,
            synced_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')

        conn.execute('''CREATE TABLE IF NOT EXISTS sprzedaze_prywatne (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opis TEXT NOT NULL,
            kwota REAL NOT NULL,
            data DATE DEFAULT CURRENT_DATE,
            notatka TEXT DEFAULT '',
            data_dodania TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        # Tabela historii produktu (timeline)
        conn.execute('''CREATE TABLE IF NOT EXISTS historia_produktu (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            produkt_id INTEGER,
            akcja TEXT,
            opis TEXT,
            dane_json TEXT,
            data TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (produkt_id) REFERENCES produkty(id)
        )''')
        
        # Tabela serwis (naprawy produktów)
        conn.execute('''CREATE TABLE IF NOT EXISTS serwis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            produkt_id INTEGER,
            technik TEXT DEFAULT '',
            opis_usterki TEXT DEFAULT '',
            koszt_naprawy REAL DEFAULT 0,
            ilosc_szt INTEGER DEFAULT 1,
            status TEXT DEFAULT 'przyjety',
            data_przyjecia TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            data_zakonczenia TIMESTAMP,
            uwagi TEXT DEFAULT '',
            FOREIGN KEY (produkt_id) REFERENCES produkty(id)
        )''')

        # Tabela sztuk (per-unit tracking)
        conn.execute('''CREATE TABLE IF NOT EXISTS sztuki (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            produkt_id INTEGER NOT NULL,
            numer INTEGER NOT NULL,
            stan TEXT DEFAULT 'Nowy',
            status TEXT DEFAULT 'magazyn',
            opis_naprawy TEXT DEFAULT '',
            data_naprawy DATE DEFAULT NULL,
            data_dodania TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (produkt_id) REFERENCES produkty(id)
        )''')

        # Tabela konfiguracji
        conn.execute('''CREATE TABLE IF NOT EXISTS config (
            klucz TEXT PRIMARY KEY,
            wartosc TEXT,
            data_aktualizacji TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')

        # Tabela ogłoszeń OLX
        try:
            conn.execute('''CREATE TABLE IF NOT EXISTS olx_oferty (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produkt_id INTEGER,
                olx_advert_id TEXT,
                tytul TEXT DEFAULT '',
                cena REAL DEFAULT 0,
                status TEXT DEFAULT 'draft',
                data_utworzenia TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                data_aktualizacji TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (produkt_id) REFERENCES produkty(id)
            )''')
        except Exception as e:
            print(f"[WARN] OLX table: {e}")

        # Tabela przedmiotów Vinted
        try:
            conn.execute('''CREATE TABLE IF NOT EXISTS vinted_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produkt_id INTEGER,
                vinted_item_id TEXT,
                tytul TEXT DEFAULT '',
                cena REAL DEFAULT 0,
                status TEXT DEFAULT 'in_progress',
                data_utworzenia TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                data_aktualizacji TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (produkt_id) REFERENCES produkty(id)
            )''')
        except Exception as e:
            print(f"[WARN] Vinted table: {e}")

        # Tabela pallet_deals (monitoring okazji palet)
        conn.execute('''CREATE TABLE IF NOT EXISTS pallet_deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            external_id TEXT,
            title TEXT NOT NULL,
            url TEXT,
            price REAL DEFAULT 0,
            currency TEXT DEFAULT 'PLN',
            category TEXT DEFAULT '',
            image_url TEXT DEFAULT '',
            items_count INTEGER DEFAULT 0,
            market_value REAL DEFAULT 0,
            matched_keywords TEXT DEFAULT '',
            notified INTEGER DEFAULT 0,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, external_id)
        )''')

        # Tabela wydanych licencji (serwer licencji)
        conn.execute('''CREATE TABLE IF NOT EXISTS licenses_issued (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT UNIQUE,
            client_name TEXT,
            plan TEXT DEFAULT 'pro',
            hwid TEXT DEFAULT '',
            expires INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            last_heartbeat TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')

        # === AUDIT LOG — krytyczne akcje administracyjne ===
        # Loguj wszystkie privileged actions (system update, restart, backup
        # restore, zmiana konfiguracji) — dla forensics i compliance.
        conn.execute('''CREATE TABLE IF NOT EXISTS admin_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id INTEGER,
            username TEXT,
            role TEXT,
            action TEXT NOT NULL,
            details TEXT,
            ip_address TEXT,
            user_agent TEXT,
            success INTEGER DEFAULT 1,
            error_message TEXT
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON admin_audit_log(timestamp DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_audit_user ON admin_audit_log(user_id, timestamp DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_audit_action ON admin_audit_log(action, timestamp DESC)')

        # === API v1 — Public REST API ===
        # Klucze API dla zewnetrznych integracji (sklepy, scripts, partnerzy).
        # key_hash: werkzeug pbkdf2 hash. Plain key pokazywany userowi RAZ przy
        # tworzeniu. key_prefix: szybki lookup (indexed).
        conn.execute('''CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT NOT NULL,
            key_prefix TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP,
            revoked_at TIMESTAMP,
            rate_limit_per_min INTEGER DEFAULT 60
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(key_prefix)')
        # Partial index: tylko aktywne klucze (pozwala SELECT ... WHERE revoked_at IS NULL
        # byc super-szybkim nawet dla tysiecy revoked kluczy historycznie)
        try:
            conn.execute('CREATE INDEX IF NOT EXISTS idx_api_keys_active '
                         'ON api_keys(revoked_at) WHERE revoked_at IS NULL')
        except Exception:
            # Stare SQLite moga nie wspierac partial index — fallback na zwykly
            conn.execute('CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys(revoked_at)')

        # Log uzycia API per-key — do analytics/billing/debug
        conn.execute('''CREATE TABLE IF NOT EXISTS api_usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_id INTEGER NOT NULL,
            endpoint TEXT NOT NULL,
            method TEXT NOT NULL,
            status_code INTEGER,
            ip_address TEXT,
            response_time_ms INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_api_usage_key_ts '
                     'ON api_usage_log(api_key_id, created_at DESC)')

        # Zarejestrowane webhooki outbound (klient dostaje eventy z naszej strony)
        conn.execute('''CREATE TABLE IF NOT EXISTS webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            events TEXT NOT NULL,
            secret TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (api_key_id) REFERENCES api_keys(id) ON DELETE CASCADE
        )''')
        try:
            conn.execute('CREATE INDEX IF NOT EXISTS idx_webhooks_active '
                         'ON webhooks(active) WHERE active = 1')
        except Exception:
            conn.execute('CREATE INDEX IF NOT EXISTS idx_webhooks_active ON webhooks(active)')

        # Kolejka dostaw webhookow — retry z exponential backoff
        conn.execute('''CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            webhook_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER DEFAULT 0,
            last_error TEXT,
            next_retry_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (webhook_id) REFERENCES webhooks(id) ON DELETE CASCADE
        )''')
        try:
            conn.execute('CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_pending '
                         "ON webhook_deliveries(status, next_retry_at) "
                         "WHERE status = 'pending'")
        except Exception:
            conn.execute('CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_pending '
                         'ON webhook_deliveries(status, next_retry_at)')

        # Domyślna konfiguracja
        defaults = [
            ('telegram_bot_token', ''),
            ('telegram_chat_id', ''),
            ('telegram_enabled', 'true'),
            ('telegram_alert_sprzedaz', 'true'),
            ('telegram_alert_niski_stan', 'true'),
            ('telegram_alert_nowa_oferta', 'false'),
            ('telegram_raport_dzienny', 'true'),
            ('allegro_client_id', ''),
            ('allegro_client_secret', ''),
            ('allegro_access_token', ''),
            ('allegro_refresh_token', ''),
            ('allegro_token_expires', ''),
            ('allegro_sandbox', 'false'),
            ('allegro_redirect_uri', 'http://localhost:5000/allegro/callback'),
            ('allegro_last_event_check', ''),
            ('domyslna_marza', '40'),
            ('domyslna_kategoria', 'inne'),
            ('app_base_url', 'http://localhost:5000'),
        ]
        
        for klucz, wartosc in defaults:
            try:
                conn.execute('INSERT INTO config (klucz, wartosc) VALUES (?, ?)', (klucz, wartosc))
            except sqlite3.IntegrityError:
                pass  # Już istnieje
        
        # === AUTO-MIGRACJE ===
        _migrate_cols = [
            ('palety', 'typ', "ALTER TABLE palety ADD COLUMN typ TEXT DEFAULT 'paleta'"),
            ('palety', 'dostarczona', "ALTER TABLE palety ADD COLUMN dostarczona INTEGER DEFAULT 0"),
            ('palety', 'cena_zakupu_netto', "ALTER TABLE palety ADD COLUMN cena_zakupu_netto REAL DEFAULT 0"),
            ('palety', 'ilosc_sztuk', "ALTER TABLE palety ADD COLUMN ilosc_sztuk INTEGER DEFAULT 0"),
            ('palety', 'ocena_status', "ALTER TABLE palety ADD COLUMN ocena_status TEXT DEFAULT ''"),
            ('produkty', 'stan_przyjecia', "ALTER TABLE produkty ADD COLUMN stan_przyjecia TEXT DEFAULT ''"),
            ('produkty', 'notatki_przyjecia', "ALTER TABLE produkty ADD COLUMN notatki_przyjecia TEXT DEFAULT ''"),
            ('produkty', 'klasa_jakosci', "ALTER TABLE produkty ADD COLUMN klasa_jakosci TEXT DEFAULT ''"),
            ('licenses_issued', 'plan', "ALTER TABLE licenses_issued ADD COLUMN plan TEXT DEFAULT 'MAX'"),
            ('licenses_issued', 'expires', "ALTER TABLE licenses_issued ADD COLUMN expires TEXT"),
            ('licenses_issued', 'hwid', "ALTER TABLE licenses_issued ADD COLUMN hwid TEXT DEFAULT ''"),
            ('licenses_issued', 'active', "ALTER TABLE licenses_issued ADD COLUMN active INTEGER DEFAULT 1"),
            ('licenses_issued', 'expires_date', "ALTER TABLE licenses_issued ADD COLUMN expires_date TEXT"),
        ]
        for _tbl, _col, _sql in _migrate_cols:
            try:
                conn.execute(f'SELECT {_col} FROM {_tbl} LIMIT 1')
            except:
                try:
                    conn.execute(_sql)
                    print(f'  [OK] Migracja: dodano {_tbl}.{_col}')
                except:
                    pass

        # === INDEKSY dla wydajności ===
        # Sprzedaze - najczęściej skanowana tabela
        conn.execute('CREATE INDEX IF NOT EXISTS idx_sprzedaze_produkt_id ON sprzedaze(produkt_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_sprzedaze_oferta_id ON sprzedaze(oferta_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_sprzedaze_status ON sprzedaze(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_sprzedaze_data ON sprzedaze(data_sprzedazy)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_sprzedaze_allegro_order ON sprzedaze(allegro_order_id)')
        # Produkty - FK i statusy
        conn.execute('CREATE INDEX IF NOT EXISTS idx_produkty_paleta_id ON produkty(paleta_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_produkty_status ON produkty(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_produkty_ean ON produkty(ean)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_produkty_asin ON produkty(asin)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_produkty_lokalizacja ON produkty(lokalizacja)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_produkty_kategoria ON produkty(kategoria)')
        # Oferty - FK
        conn.execute('CREATE INDEX IF NOT EXISTS idx_oferty_produkt_id ON oferty(produkt_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_oferty_allegro_id ON oferty(allegro_id)')
        # Palety - dostawca i data
        conn.execute('CREATE INDEX IF NOT EXISTS idx_palety_dostawca ON palety(dostawca)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_palety_data_zakupu ON palety(data_zakupu)')
        # Composite indeksy dla dashboard queries
        conn.execute('CREATE INDEX IF NOT EXISTS idx_sprzedaze_status_data ON sprzedaze(status, data_sprzedazy)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_produkty_status_data ON produkty(status, data_dodania)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_produkty_paleta_status ON produkty(paleta_id, status)')

        conn.commit()

# === CACHE dla statystyk ===
_stats_cache = {'data': None, 'time': 0}
_STATS_TTL = 30  # sekund

def invalidate_stats_cache():
    """Wywołaj po operacjach zapisu (sprzedaż, nowy produkt itp.)"""
    _stats_cache['time'] = 0

def get_config(klucz, default=''):
    """Pobiera wartość konfiguracji. Auto-deszyfruje wartości ENC:xxx."""
    conn = get_db()
    row = conn.execute('SELECT wartosc FROM config WHERE klucz = ?', (klucz,)).fetchone()
    if not row:
        return default
    val = row['wartosc']
    # Auto-deszyfruj zaszyfrowane wartości
    if val and val.startswith('ENC:'):
        f = _get_fernet()
        if f:
            try:
                return f.decrypt(val[4:].encode()).decode()
            except Exception:
                pass
    return val

def set_config(klucz, wartosc):
    """Ustawia wartość konfiguracji"""
    conn = get_db()
    conn.execute('''INSERT OR REPLACE INTO config (klucz, wartosc, data_aktualizacji)
                    VALUES (?, ?, CURRENT_TIMESTAMP)''', (klucz, wartosc))
    conn.commit()
    invalidate_config_cache()


# ============================================================
# CONFIG CACHE — unika powtarzanych SELECT-ów na config
# ============================================================
_config_cache = {}
_config_cache_time = 0
_CONFIG_TTL = 60  # sekund

def get_config_cached(klucz, default=''):
    """Pobiera config z cache (TTL 60s)"""
    global _config_cache, _config_cache_time
    now = time.time()
    if (now - _config_cache_time) > _CONFIG_TTL:
        _config_cache = {}
        _config_cache_time = now
    if klucz not in _config_cache:
        _config_cache[klucz] = get_config(klucz, default)
    return _config_cache.get(klucz, default)

def invalidate_config_cache():
    """Czyści cache configu (wywołaj po set_config)"""
    global _config_cache, _config_cache_time
    _config_cache = {}
    _config_cache_time = 0


# ============================================================
# SECRETS — szyfrowanie wrażliwych danych w config
# ============================================================
_fernet_instance = None

def _get_fernet():
    """Lazy-init Fernet encryption.

    Klucz ladowany przez modules.key_loader.load_encryption_key() ktory
    sprawdza (w kolejnosci):
      1. env var AKCES_ENCRYPTION_KEY (systemd EnvironmentFile)
      2. /etc/akces/env.key (produkcja, chmod 600)
      3. ~/.akces/env.key (dev fallback)
      4. <app_dir>/.env.key (LEGACY, print warning)

    Zobacz docs/DEPLOYMENT.md po szczegoly deploymentu.
    """
    global _fernet_instance
    if _fernet_instance:
        return _fernet_instance
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        print("[WARN] cryptography not installed — secrets stored as plaintext. Run: pip install cryptography")
        return None

    try:
        from modules.key_loader import load_encryption_key
        key_bytes, source = load_encryption_key(auto_generate=True, migrate_legacy=True)
    except Exception as e:
        print(f"[ERR] Nie moge zaladowac klucza szyfrowania: {e}")
        return None

    # Log skad pochodzi klucz (pomaga diagnostyce) — bez drukowania klucza.
    print(f"[OK] Klucz szyfrowania zaladowany ze zrodla: {source}")
    _fernet_instance = Fernet(key_bytes)
    return _fernet_instance


def set_secret(klucz, wartosc):
    """Zapisuje zaszyfrowaną wartość do config. Fallback: plaintext jeśli brak cryptography."""
    f = _get_fernet()
    if f and wartosc:
        encrypted = f.encrypt(wartosc.encode()).decode()
        set_config(klucz, f'ENC:{encrypted}')
    else:
        set_config(klucz, wartosc)


def get_secret(klucz, default=''):
    """Odczytuje zaszyfrowaną wartość z config. Backwards compatible z plaintext."""
    raw = get_config(klucz, default)
    if not raw or not raw.startswith('ENC:'):
        return raw  # Plaintext (stare wartości) — backwards compatible
    f = _get_fernet()
    if not f:
        return raw  # Brak cryptography — zwróć raw
    try:
        return f.decrypt(raw[4:].encode()).decode()
    except Exception as e:
        print(f"[WARN] Nie można odszyfrować {klucz}: {e}")
        return default


def migrate_secrets():
    """Migruje plaintext sekrety do zaszyfrowanych. Bezpieczne do wielokrotnego wywołania."""
    secret_keys = [
        'allegro_client_secret', 'allegro_access_token', 'allegro_refresh_token',
        'olx_client_secret', 'olx_access_token', 'olx_refresh_token',
        'vinted_secret',
        'ngrok_auth_token',
        'gemini_api_key',
        'telegram_bot_token', 'telegram_chat_id',
    ]
    f = _get_fernet()
    if not f:
        return 0
    migrated = 0
    for key in secret_keys:
        val = get_config(key, '')
        if val and not val.startswith('ENC:'):
            set_secret(key, val)
            migrated += 1
    if migrated > 0:
        print(f"[OK] Zaszyfrowano {migrated} sekretów w bazie danych")
    return migrated

# ============================================================
# DUPLIKATY PRODUKTÓW — sprawdzanie po ASIN/EAN przed dodaniem
# ============================================================

def find_duplicate_product(asin=None, ean=None, nazwa=None):
    """
    Szuka istniejącego produktu w DB po ASIN, EAN lub nazwie.
    Zwraca dict z danymi produktu lub None jeśli nie znaleziono.

    Priorytet: ASIN > EAN > nazwa (exact match).
    """
    conn = get_db()

    # Szukaj po ASIN (najdokładniejsze)
    if asin and asin.strip():
        asin = asin.strip().upper()
        row = conn.execute("""
            SELECT p.id, p.nazwa, p.asin, p.ean, p.ilosc, p.lokalizacja, p.regal,
                   COALESCE(pal.nazwa, '') as paleta_nazwa
            FROM produkty p
            LEFT JOIN palety pal ON p.paleta_id = pal.id
            WHERE UPPER(TRIM(p.asin)) = ? AND p.ilosc > 0
            ORDER BY p.ilosc DESC LIMIT 1
        """, (asin,)).fetchone()
        if row:
            return dict(row)

    # Szukaj po EAN
    if ean and ean.strip():
        ean = ean.strip()
        row = conn.execute("""
            SELECT p.id, p.nazwa, p.asin, p.ean, p.ilosc, p.lokalizacja, p.regal,
                   COALESCE(pal.nazwa, '') as paleta_nazwa
            FROM produkty p
            LEFT JOIN palety pal ON p.paleta_id = pal.id
            WHERE TRIM(p.ean) = ? AND p.ilosc > 0
            ORDER BY p.ilosc DESC LIMIT 1
        """, (ean,)).fetchone()
        if row:
            return dict(row)

    # Szukaj po ASIN (nawet z ilosc=0 — kiedyś sprzedawaliśmy)
    if asin and asin.strip():
        row = conn.execute("""
            SELECT p.id, p.nazwa, p.asin, p.ean, p.ilosc, p.lokalizacja, p.regal,
                   COALESCE(pal.nazwa, '') as paleta_nazwa
            FROM produkty p
            LEFT JOIN palety pal ON p.paleta_id = pal.id
            WHERE UPPER(TRIM(p.asin)) = ?
            ORDER BY p.id DESC LIMIT 1
        """, (asin.strip().upper(),)).fetchone()
        if row:
            return dict(row)

    return None


def add_quantity_to_existing(product_id, quantity=1):
    """Dodaje ilość do istniejącego produktu zamiast tworzyć duplikat."""
    conn = get_db()
    conn.execute(
        "UPDATE produkty SET ilosc = ilosc + ? WHERE id = ?",
        (quantity, product_id)
    )
    conn.commit()
    row = conn.execute("SELECT id, nazwa, ilosc FROM produkty WHERE id = ?", (product_id,)).fetchone()
    return dict(row) if row else None


def is_module_enabled(name):
    """Sprawdza czy moduł jest włączony. OLX/Vinted domyślnie wyłączone."""
    default = '0' if name in ('olx', 'vinted') else '1'
    return get_config_cached(f'module_{name}', default) == '1'


def get_dostawcy_list():
    """Zwraca posortowana liste unikalnych dostawcow z bazy + custom z configu"""
    conn = get_db()
    # Dostawcy z istniejacych palet
    db_dostawcy = conn.execute(
        "SELECT DISTINCT dostawca FROM palety WHERE dostawca IS NOT NULL AND dostawca != '' ORDER BY dostawca"
    ).fetchall()
    dostawcy = [r[0] for r in db_dostawcy]

    # Dostawcy z istniejacych produktow
    db_prod = conn.execute(
        "SELECT DISTINCT dostawca FROM produkty WHERE dostawca IS NOT NULL AND dostawca != '' ORDER BY dostawca"
    ).fetchall()
    for r in db_prod:
        if r[0] not in dostawcy:
            dostawcy.append(r[0])

    # Custom dostawcy z configu
    custom = get_config('custom_dostawcy', '')
    if custom:
        for d in custom.split(','):
            d = d.strip()
            if d and d not in dostawcy:
                dostawcy.append(d)

    # Domyslne sugestie dla nowych instalacji
    if not dostawcy:
        dostawcy = ['Jobalots', 'Warrington', 'Amazon Returns', 'Inny']

    return sorted(dostawcy)


def save_custom_dostawca(dostawca):
    """Zapisuje nowego dostawce do listy custom w configu"""
    if not dostawca:
        return
    existing = get_config('custom_dostawcy', '')
    existing_list = [d.strip() for d in existing.split(',') if d.strip()] if existing else []
    if dostawca not in existing_list:
        existing_list.append(dostawca)
        set_config('custom_dostawcy', ','.join(existing_list))


def query_db(query, args=(), one=False):
    """Wykonuje zapytanie i zwraca wyniki"""
    with get_db() as conn:
        cur = conn.execute(query, args)
        rv = cur.fetchall()
        return (rv[0] if rv else None) if one else rv

def execute_db(query, args=()):
    """Wykonuje zapytanie modyfikujące"""
    with get_db() as conn:
        conn.execute(query, args)
        conn.commit()


def auto_anonymize_old_data():
    """
    RODO: Automatycznie anonimizuje dane osobowe starsze niz okres retencji.
    Zachowuje kwoty i statystyki do celow ksiegowych.
    Wywolywane z backup daemon (raz dziennie).
    """
    try:
        retention_years = get_config('data_retention_years', '5')
        if retention_years == '0':
            return 0  # Wylaczone

        years = int(retention_years)
        if years <= 0:
            return 0

        conn = get_db()
        cursor = conn.execute(
            """UPDATE sprzedaze SET kupujacy='Dane zanonimizowane', adres='Zanonimizowane'
               WHERE kupujacy != 'Dane zanonimizowane'
               AND kupujacy IS NOT NULL
               AND kupujacy != ''
              
               AND data_sprzedazy < datetime('now', ? || ' years')""",
            (f'-{years}',)
        )
        conn.commit()
        count = cursor.rowcount
        if count > 0:
            print(f"[RODO] Zanonimizowano {count} rekordow starszych niz {years} lat")
        return count
    except Exception as e:
        print(f"[RODO] Blad auto-anonimizacji: {e}")
        return 0


# ============================================================
# JEDNORAZOWA MIGRACJA - reset fałszywych dat wystawienia
# ============================================================
def migrate_reset_fake_data_wystawienia():
    """
    Zeruje data_wystawienia dla ofert gdzie data była ustawiona jako CURRENT_TIMESTAMP
    przy syncowaniu (nie prawdziwa data Allegro). Po wywołaniu tej funkcji,
    kolejny sync z Allegro pobierze prawdziwe daty publication.startingAt.
    
    Heurystyka: jeśli oferta ma allegro_id (pochodzi z Allegro) ale data_wystawienia
    wygląda jak czas synca (np. ta sama godzina dla wielu ofert) — zerujemy.
    Bezpieczniej: zerujemy WSZYSTKIE oferty z allegro_id, niech sync wpisze prawdziwe.
    """
    try:
        with get_db() as conn:
            # Sprawdź czy migracja już była wykonana (Polish schema: klucz/wartosc)
            done = conn.execute(
                "SELECT wartosc FROM config WHERE klucz='migr_reset_wystawienia_v1' LIMIT 1"
            ).fetchone()
            if done:
                return
            # Zeruj data_wystawienia dla wszystkich ofert z allegro_id
            # (przy następnym syncu dostaną prawdziwą datę z Allegro API)
            cnt = conn.execute(
                "SELECT COUNT(*) FROM oferty WHERE allegro_id IS NOT NULL AND allegro_id != ''"
            ).fetchone()[0]
            conn.execute(
                "UPDATE oferty SET data_wystawienia = NULL WHERE allegro_id IS NOT NULL AND allegro_id != ''"
            )
            conn.execute(
                "INSERT OR REPLACE INTO config(klucz, wartosc) VALUES('migr_reset_wystawienia_v1', '1')"
            )
            conn.commit()
            print(f"[OK] Migracja: zresetowano data_wystawienia dla {cnt} ofert (zostaną uzupełnione przy syncu Allegro)")
    except Exception as e:
        print(f"[WARN] Migracja reset wystawienia: {e}")


def maintenance_reindex_if_needed():
    """REINDEX trzyma exclusive lock na DB — robimy go MAX raz na 7 dni
    i poza godzinami szczytu. Wolany w background po starcie."""
    import time as _time
    try:
        last = get_config('last_reindex_ts', '0')
        try:
            last_ts = float(last)
        except Exception:
            last_ts = 0
        if _time.time() - last_ts < 7 * 86400:
            return  # nie czas jeszcze
        with get_db() as conn:
            conn.execute("REINDEX")
            conn.commit()
        set_config('last_reindex_ts', str(_time.time()))
        print("  [OK] REINDEX (raz/7dni) zakonczony")
    except Exception as e:
        print(f"  [WARN] REINDEX: {e}")


def fix_product_status_integrity():
    """
    Naprawia niespójności statusów produktów (szybkie 2 UPDATE-y, bez REINDEX):
    1. status='sprzedany' ale ilosc > 0 → status='magazyn'
    2. status='magazyn'/'wystawiony' ale ilosc = 0 → status='sprzedany'
    Uruchamiane przy starcie aplikacji.

    REINDEX wyniesiony do maintenance_reindex_if_needed() - blokowal DB
    i powodowal "database is locked" w auto-sync Allegro.
    """
    try:
        with get_db() as conn:
            # 1. Produkty ze statusem 'sprzedany' ale ilosc > 0 → przywróć do magazynu
            fix1 = conn.execute('''
                UPDATE produkty SET status = 'magazyn'
                WHERE status = 'sprzedany' AND ilosc > 0
            ''').rowcount

            if fix1 > 0:
                print(f"  [BUIL] Integralność: {fix1} produktów (status sprzedany→magazyn, mają ilosc>0)")

            # 2. Produkty w magazynie/wystawiony ale z ilością 0 → oznacz jako sprzedane
            fix2 = conn.execute('''
                UPDATE produkty SET status = 'sprzedany'
                WHERE status IN ('magazyn', 'wystawiony') AND ilosc = 0
            ''').rowcount

            if fix2 > 0:
                print(f"  [BUIL] Integralność: {fix2} produktów (ilosc=0, status→sprzedany)")

            if fix1 > 0 or fix2 > 0:
                conn.commit()
            else:
                print("  [OK] Integralność produktów OK")

    except Exception as e:
        print(f"[WARN] Fix integralności: {e}")


# ============================================================
# STATYSTYKI
# ============================================================

def get_full_stats():
    """Pobiera pełne statystyki dla dashboardu (cached 30s)"""
    now = time.time()
    if _stats_cache['data'] and (now - _stats_cache['time']) < _STATS_TTL:
        return _stats_cache['data']

    with get_db() as conn:
        today = datetime.now().strftime('%Y-%m-%d')
        month_start = datetime.now().strftime('%Y-%m-01')
        days_30_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        
        stats = {}
        
        # === PALETY ===
        # Palety w tym miesiącu
        row = conn.execute('''
            SELECT COUNT(*) as cnt, COALESCE(SUM(cena_zakupu), 0) as suma
            FROM palety WHERE date(data_zakupu) >= ? AND COALESCE(typ, 'paleta') = 'paleta'
        ''', (month_start,)).fetchone()
        stats['palety_miesiac'] = row['cnt']
        
        # Koszt miesięczny = większa wartość z: palet lub produktów
        koszt_palet_msc = row['suma'] or 0
        koszt_produktow_msc = conn.execute('''
            SELECT COALESCE(SUM(CASE WHEN cena_brutto > 0 THEN cena_brutto ELSE cena_netto END), 0) as suma
            FROM produkty p
            JOIN palety pal ON p.paleta_id = pal.id
            WHERE date(pal.data_zakupu) >= ?
        ''', (month_start,)).fetchone()['suma'] or 0
        stats['palety_miesiac_koszt'] = max(koszt_palet_msc, koszt_produktow_msc)
        
        # Palety łącznie (bez boxów)
        row = conn.execute('''
            SELECT COUNT(*) as cnt, COALESCE(SUM(cena_zakupu), 0) as suma
            FROM palety WHERE COALESCE(typ, 'paleta') = 'paleta'
        ''').fetchone()
        stats['palety_lacznie'] = row['cnt']
        
        # Koszt łączny = większa wartość z: palet lub produktów
        koszt_palet = row['suma'] or 0
        koszt_produktow = conn.execute('''
            SELECT COALESCE(SUM(CASE WHEN cena_brutto > 0 THEN cena_brutto ELSE cena_netto END), 0) as suma
            FROM produkty
        ''').fetchone()['suma'] or 0
        stats['palety_lacznie_koszt'] = max(koszt_palet, koszt_produktow)
        
        # === MAGAZYN ===
        # Produkty na magazynie — tylko statusy magazyn/wystawiony (spójnie z KPI dashboard)
        row = conn.execute('''
            SELECT COUNT(*) as cnt, COALESCE(SUM(ilosc), 0) as sztuki,
                   COALESCE(SUM(cena_allegro * ilosc), 0) as wartosc
            FROM produkty WHERE status IN ('magazyn', 'wystawiony')
        ''').fetchone()
        stats['magazyn_produkty'] = row['cnt']
        stats['magazyn_sztuki'] = row['sztuki']
        stats['magazyn_wartosc'] = row['wartosc']
        
        # Produkty wystawione (aktywne oferty)
        row = conn.execute('''
            SELECT COUNT(*) as cnt FROM produkty WHERE status = 'wystawiony'
        ''').fetchone()
        stats['wystawione'] = row['cnt']
        
        # Stojące >30 dni (bez sprzedaży)
        row = conn.execute('''
            SELECT COUNT(*) as cnt FROM produkty 
            WHERE status IN ('magazyn', 'wystawiony') 
            AND date(data_dodania) < ? AND ilosc > 0
        ''', (days_30_ago,)).fetchone()
        stats['stojace_30dni'] = row['cnt']
        
        # === SPRZEDAŻ DZIŚ ===
        # Liczymy tylko opłacone (bez zwrotów, anulowanych i ręcznych korekt)
        # data_sprzedazy jest już w czasie lokalnym (PL) - nie konwertujemy
        row = conn.execute('''
            SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc + COALESCE(koszt_dostawy, 0)), 0) as suma
            FROM sprzedaze WHERE
                date(REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) = ?
            AND status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')
            AND (allegro_order_id IS NULL OR allegro_order_id NOT LIKE 'MANUAL-%')

        ''', (today,)).fetchone()
        stats['sprzedaz_dzis_cnt'] = row['cnt']
        stats['sprzedaz_dzis_suma'] = row['suma']

        # Zwroty dziś (do wyświetlenia)
        row_zwroty = conn.execute('''
            SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc + COALESCE(koszt_dostawy, 0)), 0) as suma
            FROM sprzedaze WHERE
                date(REPLACE(SUBSTR(data_sprzedazy,1,19),'T',' ')) = ?
            AND status = 'zwrot'
        ''', (today,)).fetchone()
        stats['zwroty_dzis_cnt'] = row_zwroty['cnt']
        stats['zwroty_dzis_suma'] = row_zwroty['suma']
        
        # === DO WYSŁANIA (status = 'nowa') ===
        row = conn.execute('''
            SELECT COUNT(*) as cnt FROM sprzedaze WHERE status = 'nowa'
        ''').fetchone()
        stats['do_wyslania'] = row['cnt']
        
        # === SPRZEDAŻ W MIESIĄCU ===
        # Tylko opłacone (bez zwrotów i anulowanych)
        row = conn.execute('''
            SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc + COALESCE(koszt_dostawy, 0)), 0) as suma
            FROM sprzedaze WHERE date(data_sprzedazy) >= ?
            AND status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')

        ''', (month_start,)).fetchone()
        stats['sprzedaz_miesiac_cnt'] = row['cnt']
        stats['sprzedaz_miesiac_suma'] = row['suma']
        # Dolicz sprzedaże prywatne w miesiącu
        try:
            row_pryw_msc = conn.execute('''
                SELECT COUNT(*) as cnt, COALESCE(SUM(kwota), 0) as suma
                FROM sprzedaze_prywatne WHERE date(data) >= ?
            ''', (month_start,)).fetchone()
            stats['sprzedaz_miesiac_cnt'] += row_pryw_msc['cnt'] or 0
            stats['sprzedaz_miesiac_suma'] += row_pryw_msc['suma'] or 0
        except Exception:
            pass
        
        # Zwroty w miesiącu
        row_zwroty_msc = conn.execute('''
            SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc + COALESCE(koszt_dostawy, 0)), 0) as suma
            FROM sprzedaze WHERE date(data_sprzedazy) >= ? AND status = 'zwrot'
        ''', (month_start,)).fetchone()
        stats['zwroty_miesiac_cnt'] = row_zwroty_msc['cnt']
        stats['zwroty_miesiac_suma'] = row_zwroty_msc['suma']
        
        # === SPRZEDAŻ ŁĄCZNIE ===
        # Tylko opłacone (bez zwrotów i anulowanych)
        row = conn.execute('''
            SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc + COALESCE(koszt_dostawy, 0)), 0) as suma
            FROM sprzedaze WHERE status NOT IN ('zwrot', 'anulowane', 'anulowana')

        ''').fetchone()
        # Dolicz sprzedaże prywatne (poza Allegro)
        try:
            row_pryw = conn.execute('''
                SELECT COUNT(*) as cnt, COALESCE(SUM(kwota), 0) as suma
                FROM sprzedaze_prywatne
            ''').fetchone()
            pryw_cnt = row_pryw['cnt'] or 0
            pryw_suma = row_pryw['suma'] or 0
        except:
            pryw_cnt = 0
            pryw_suma = 0
        stats['sprzedaz_lacznie_cnt'] = row['cnt'] + pryw_cnt
        stats['sprzedaz_lacznie_suma'] = row['suma'] + pryw_suma
        stats['sprzedaz_lacznie_pryw_suma'] = pryw_suma
        
        # Zwroty łącznie
        row_zwroty_all = conn.execute('''
            SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc + COALESCE(koszt_dostawy, 0)), 0) as suma
            FROM sprzedaze WHERE status = 'zwrot'
        ''').fetchone()
        stats['zwroty_lacznie_cnt'] = row_zwroty_all['cnt']
        stats['zwroty_lacznie_suma'] = row_zwroty_all['suma']
        
        # === ŚREDNIA WARTOŚĆ ZAMÓWIENIA ===
        if stats['sprzedaz_lacznie_cnt'] > 0:
            stats['srednia_zamowienie'] = stats['sprzedaz_lacznie_suma'] / stats['sprzedaz_lacznie_cnt']
        else:
            stats['srednia_zamowienie'] = 0
        
        # === ZYSK SZACOWANY (miesiąc) — model COGS ===
        # Zysk = Przychód ze sprzedaży - Koszt SPRZEDANYCH produktów - Prowizja Allegro (11%)
        # Koszt per produkt = paleta.cena_zakupu / łączna ilość sztuk z palety
        # Dzięki temu: sprzedajesz w marcu produkt z palety kupionej w styczniu
        # → koszt ląduje w marcu (kiedy sprzedałeś), nie w styczniu (kiedy kupiłeś)
        # prowizja obliczona niżej po odjęciu zwrotów

        # COGS = koszt sprzedanych produktów w tym miesiącu
        # Dla każdej sprzedaży: koszt = paleta.cena_zakupu / ilość_sztuk_z_palety
        # ZWROT: razem z COGS pobieramy też przychod_z_cogs, zeby moc doszacowac
        # koszt dla sprzedazy bez produkt_id (Allegro synced bez powiazania)
        cogs_row = conn.execute('''
            SELECT
              COALESCE(SUM(
                CASE
                    WHEN pal.cena_zakupu > 0 AND pal_total.total_szt > 0
                    THEN (pal.cena_zakupu / pal_total.total_szt) * s.ilosc
                    ELSE 0
                END
              ), 0) as cogs,
              COALESCE(SUM(
                CASE
                    WHEN pal.cena_zakupu > 0 AND pal_total.total_szt > 0
                    THEN s.cena * s.ilosc
                    ELSE 0
                END
              ), 0) as przychod_powiazany
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            LEFT JOIN palety pal ON p.paleta_id = pal.id
            LEFT JOIN (
                SELECT pr.paleta_id,
                    COALESCE(SUM(pr.ilosc), 0)
                    + COALESCE(SUM(pr.sprzedano_offline), 0)
                    + COALESCE((
                        SELECT SUM(sp2.ilosc) FROM sprzedaze sp2
                        JOIN produkty pp2 ON sp2.produkt_id = pp2.id
                        WHERE pp2.paleta_id = pr.paleta_id
                        AND sp2.status NOT IN ('zwrot','anulowane','anulowana')
                    ), 0) as total_szt
                FROM produkty pr GROUP BY pr.paleta_id
            ) pal_total ON pal_total.paleta_id = pal.id
            WHERE date(s.data_sprzedazy) >= ?
            AND s.status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
        ''', (month_start,)).fetchone()
        cogs_powiazany = cogs_row['cogs'] or 0
        przychod_powiazany = cogs_row['przychod_powiazany'] or 0
        przychod_total_msc = stats.get('sprzedaz_miesiac_suma', 0) or 0

        # Doszacowanie kosztu dla sprzedazy bez produkt_id:
        # ratio = COGS_powiazany / przychod_powiazany  (sredni stosunek koszt/przychod)
        # cogs_dosacowany = (przychod_total - przychod_powiazany) * ratio
        if przychod_powiazany > 0 and przychod_total_msc > przychod_powiazany:
            ratio_kosztu = cogs_powiazany / przychod_powiazany
            przychod_bez_powiazania = przychod_total_msc - przychod_powiazany
            cogs_dosacowany = przychod_bez_powiazania * ratio_kosztu
        else:
            cogs_dosacowany = 0

        cogs_miesiac = cogs_powiazany + cogs_dosacowany

        # Fallback gdy w ogole nie ma COGS (zadne sprzedaze nie maja produkt_id z paleta)
        if cogs_miesiac > 0:
            koszt_sprzedanych = cogs_miesiac
        else:
            koszt_sprzedanych = koszt_palet_msc  # fallback na koszt palet kupionych w msc

        # Statystyki diagnostyczne — pokazuja jaka czesc sprzedazy ma realne COGS
        stats['cogs_powiazany_msc'] = cogs_powiazany
        stats['cogs_dosacowany_msc'] = cogs_dosacowany
        stats['przychod_powiazany_msc'] = przychod_powiazany
        stats['cogs_pokrycie_pct'] = round(przychod_powiazany / przychod_total_msc * 100) if przychod_total_msc > 0 else 0

        # sprzedaz_miesiac_suma już wyklucza zwroty (status NOT IN 'zwrot')
        # Zysk liczymy jak kalkulator marzy: przychod NETTO (bez VAT 23%) - koszt brutto - prowizja od NETTO
        przychod_po_zwrotach = stats['sprzedaz_miesiac_suma']  # brutto z Allegro
        przychod_netto_msc = przychod_po_zwrotach / 1.23 if przychod_po_zwrotach > 0 else 0
        prowizja_msc = przychod_netto_msc * 0.11

        # Koszty operacyjne z tabeli `koszty` w biezacym miesiacu
        # Wykluczamy kategorie:
        # - 'zakup' bo to zakup towaru = juz w cogs/koszt_palet (zeby nie dublowac)
        # - 'allegro' bo prowizja Allegro juz liczona automatycznie ponizej (11% z netto)
        try:
            # WHERE wyklucza tylko 'zakup' (dubluje cogs); 'allegro' zliczamy osobno przez CASE
            # zeby uzyc go jako prowizji (max szacunek vs recznie wpisany billing).
            # ABS bo koszty czasem sa zapisywane jako ujemne (UI dodaje znak minus)
            koszty_op_row = conn.execute('''
                SELECT COALESCE(SUM(CASE WHEN COALESCE(kategoria,'') != 'allegro' THEN ABS(kwota) ELSE 0 END), 0) as suma_op,
                       COALESCE(SUM(CASE WHEN COALESCE(kategoria,'') = 'allegro' THEN ABS(kwota) ELSE 0 END), 0) as allegro_recznie
                FROM koszty
                WHERE date(data) >= ? AND COALESCE(kategoria,'') != 'zakup'
            ''', (month_start,)).fetchone()
            koszty_op_msc = float(koszty_op_row['suma_op'] or 0)
            allegro_recznie_msc = float(koszty_op_row['allegro_recznie'] or 0)
        except Exception:
            koszty_op_msc = 0
            allegro_recznie_msc = 0

        # Prowizja Allegro: jesli user wpisal recznie z billingu, uzyj tego (dokladniejsze).
        # Inaczej szacuj jako 11% z netto (tylko gdy nie ma rekordow w `koszty` z kategoria 'allegro').
        prowizja_finalna = max(prowizja_msc, allegro_recznie_msc)

        # Zysk = przychod_netto - koszt_sprzedanych - prowizja - koszty_operacyjne
        stats['zysk_miesiac'] = przychod_netto_msc - koszt_sprzedanych - prowizja_finalna - koszty_op_msc
        stats['przychod_netto_msc'] = przychod_netto_msc
        stats['prowizja_msc'] = prowizja_finalna
        stats['prowizja_estimowana_msc'] = prowizja_msc
        stats['allegro_recznie_msc'] = allegro_recznie_msc
        stats['koszty_op_msc'] = koszty_op_msc
        stats['koszt_sprzedanych_msc'] = koszt_sprzedanych
        stats['cogs_miesiac'] = cogs_miesiac
        stats['koszt_palet_msc'] = koszt_palet_msc  # do porównania

        # ROI miesięczny (zysk / koszt * 100)
        if koszt_sprzedanych > 0:
            stats['roi_miesiac'] = (stats['zysk_miesiac'] / koszt_sprzedanych) * 100
        else:
            stats['roi_miesiac'] = 0
        
        # === TOP 5 PRODUKTÓW (najlepiej sprzedające się) ===
        # Używa nazwa i zdjecie z sprzedaze (naprawione przez napraw-nazwy)
        top_produkty = conn.execute('''
            SELECT
                CASE
                    WHEN s.nazwa IS NOT NULL AND s.nazwa != '' AND s.nazwa != 'Produkt' THEN SUBSTR(s.nazwa, 1, 50)
                    WHEN o.tytul IS NOT NULL AND o.tytul != '' THEN SUBSTR(o.tytul, 1, 50)
                    WHEN p.nazwa IS NOT NULL AND p.nazwa != '' THEN p.nazwa
                    ELSE 'Produkt #' || s.id
                END as produkt_nazwa,
                COALESCE(NULLIF(s.zdjecie_url,''), NULLIF(p.zdjecie_url,''), '') as zdjecie_url,
                COUNT(s.id) as sprzedazy_cnt,
                COALESCE(SUM(s.cena * s.ilosc), 0) as sprzedazy_suma
            FROM sprzedaze s
            LEFT JOIN oferty o ON s.oferta_id = o.id
            LEFT JOIN produkty p ON COALESCE(s.produkt_id, o.produkt_id) = p.id
            WHERE s.status NOT IN ('zwrot', 'anulowane', 'anulowana')
            GROUP BY produkt_nazwa
            ORDER BY sprzedazy_cnt DESC
            LIMIT 5
        ''').fetchall()
        stats['top_produkty'] = [{'nazwa': row['produkt_nazwa'], 'zdjecie_url': row['zdjecie_url'], 'sprzedazy_cnt': row['sprzedazy_cnt'], 'sprzedazy_suma': row['sprzedazy_suma']} for row in top_produkty]

        # Fallback: szukaj zdjęć po nazwie w produkty jeśli brak
        for tp in stats['top_produkty']:
            if not tp['zdjecie_url'] and tp['nazwa']:
                img_row = conn.execute(
                    "SELECT zdjecie_url FROM produkty WHERE zdjecie_url != '' AND nazwa LIKE ? LIMIT 1",
                    (tp['nazwa'][:20] + '%',)
                ).fetchone()
                if img_row:
                    tp['zdjecie_url'] = img_row[0]

        # === TOP 5 DOSTAWCÓW (najlepszy ROI) ===
        # CTE: przychód z sprzedaży + koszt = SUM(palety.cena_zakupu) per dostawca
        # NIE używamy cena_brutto (to RRP/MSRP, nie koszt zakupu!)
        top_dostawcy = conn.execute('''
            WITH dostawca_przychod AS (
                SELECT
                    COALESCE(
                        NULLIF(pal.dostawca, ''),
                        NULLIF(p.dostawca, ''),
                        NULLIF(pal2.dostawca, ''),
                        NULLIF(p2.dostawca, ''),
                        'Nieznany'
                    ) as dostawca_nazwa,
                    COUNT(DISTINCT s.id) as produktow,
                    COALESCE(SUM(s.cena * s.ilosc), 0) as przychod,
                    COUNT(s.id) as sprzedazy_cnt
                FROM sprzedaze s
                LEFT JOIN produkty p ON s.produkt_id = p.id
                LEFT JOIN palety pal ON p.paleta_id = pal.id
                LEFT JOIN oferty o ON s.oferta_id = o.id
                LEFT JOIN produkty p2 ON o.produkt_id = p2.id
                LEFT JOIN palety pal2 ON p2.paleta_id = pal2.id
                WHERE s.status NOT IN ('zwrot', 'anulowane', 'anulowana')
                GROUP BY dostawca_nazwa
                HAVING dostawca_nazwa != 'Nieznany'
            ),
            dostawca_koszt AS (
                SELECT
                    COALESCE(NULLIF(dostawca, ''), 'Nieznany') as dostawca_nazwa,
                    SUM(COALESCE(cena_zakupu, 0)) as koszt
                FROM palety
                WHERE cena_zakupu > 0
                GROUP BY dostawca_nazwa
            )
            SELECT
                dp.dostawca_nazwa,
                dp.produktow,
                dp.przychod,
                COALESCE(dk.koszt, 0) as koszt,
                dp.sprzedazy_cnt
            FROM dostawca_przychod dp
            LEFT JOIN dostawca_koszt dk ON dp.dostawca_nazwa = dk.dostawca_nazwa
            ORDER BY dp.przychod DESC
            LIMIT 5
        ''').fetchall()

        dostawcy_lista = []
        for row in top_dostawcy:
            d = {
                'dostawca': row['dostawca_nazwa'],
                'produktow': row['produktow'],
                'przychod': row['przychod'],
                'koszt': row['koszt'],
                'sprzedazy_cnt': row['sprzedazy_cnt']
            }
            # Oblicz ROI: (przychód - koszt - prowizja) / koszt * 100
            prowizja = d['przychod'] * 0.11
            if d['koszt'] > 0:
                d['roi'] = ((d['przychod'] - d['koszt'] - prowizja) / d['koszt']) * 100
            else:
                d['roi'] = 0
            dostawcy_lista.append(d)

        # Sortuj po ROI
        dostawcy_lista.sort(key=lambda x: x['roi'], reverse=True)
        stats['top_dostawcy'] = dostawcy_lista
        
        # === SPRZEDAŻ PER DZIEŃ (ostatnie 7 dni) ===
        sprzedaz_dni = conn.execute('''
            SELECT date(data_sprzedazy) as dzien,
                   COUNT(*) as cnt,
                   COALESCE(SUM(cena * ilosc), 0) as suma
            FROM sprzedaze 
            WHERE date(data_sprzedazy) >= date('now', '-7 days')
            AND status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')
            GROUP BY date(data_sprzedazy)
            ORDER BY dzien DESC
        ''').fetchall()
        stats['sprzedaz_dni'] = [dict(row) for row in sprzedaz_dni]

        _stats_cache['data'] = stats
        _stats_cache['time'] = time.time()
        return stats


def get_palety_list(limit=50):
    """Pobiera listę palet z pełnymi statystykami sprzedaży"""
    # sprzedano_szt = MAX z (status='sprzedany', tabela sprzedaze) + sprzedano_offline
    # żeby uniknąć podwójnego liczenia

    # Sprawdź czy kolumny offline istnieją
    conn = get_db()
    has_offline = False
    try:
        conn.execute("SELECT sprzedano_offline, przychod_offline FROM produkty LIMIT 1")
        has_offline = True
    except:
        pass

    if has_offline:
        # Wykluczamy produkty sprzedane offline z liczenia Allegro
        return query_db('''
            SELECT p.*, 
                   (SELECT COUNT(*) FROM produkty WHERE paleta_id = p.id) as produktow,
                   (SELECT COALESCE(SUM(CASE WHEN status IN ('sprzedany','wyslany','uszkodzony','naprawa','zlomowany') THEN 0 ELSE ilosc END), 0) FROM produkty WHERE paleta_id = p.id) as sztuk_w_magazynie,
                   (SELECT COALESCE(SUM(cena_allegro * ilosc), 0) FROM produkty WHERE paleta_id = p.id) as wartosc_detalu,
                   (SELECT COALESCE(SUM(cena_brutto), 0) FROM produkty WHERE paleta_id = p.id) as wartosc_zakupu_produktow,
                   (SELECT COALESCE(SUM(CASE WHEN status = 'sprzedany' AND (sprzedano_offline IS NULL OR sprzedano_offline = 0) THEN 1 ELSE 0 END), 0) FROM produkty WHERE paleta_id = p.id) as sprzedano_status,
                   COALESCE((SELECT SUM(s.ilosc) FROM sprzedaze s JOIN produkty pr ON s.produkt_id = pr.id WHERE pr.paleta_id = p.id AND s.status NOT IN ('anulowana', 'zwrot') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')), 0) as sprzedano_tabela,
                   (SELECT COALESCE(SUM(CASE WHEN status = 'sprzedany' AND (sprzedano_offline IS NULL OR sprzedano_offline = 0) THEN cena_allegro ELSE 0 END), 0) FROM produkty WHERE paleta_id = p.id) as sprzedano_wartosc_status,
                   COALESCE((SELECT SUM(s.cena * s.ilosc) FROM sprzedaze s JOIN produkty pr ON s.produkt_id = pr.id WHERE pr.paleta_id = p.id AND s.status NOT IN ('anulowana', 'zwrot') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')), 0) as sprzedano_wartosc_tabela,
                   (SELECT COALESCE(SUM(CASE WHEN status = 'sprzedany' THEN cena_brutto ELSE 0 END), 0) FROM produkty WHERE paleta_id = p.id) as sprzedano_koszt,
                   (SELECT COALESCE(SUM(CASE WHEN cena_brutto > 0 THEN cena_brutto WHEN cena_netto > 0 THEN cena_netto * 1.23 ELSE 0 END), 0) FROM produkty WHERE paleta_id = p.id) as koszt_produktow_all,
                   (SELECT COALESCE(SUM(sprzedano_offline), 0) FROM produkty WHERE paleta_id = p.id) as sprzedano_offline,
                   (SELECT COALESCE(SUM(przychod_offline), 0) FROM produkty WHERE paleta_id = p.id) as przychod_offline,
                   (SELECT COALESCE(SUM(ilosc), 0) FROM produkty WHERE paleta_id = p.id) as sztuk_lacznie_total
            FROM palety p
            ORDER BY CAST(SUBSTR(p.nazwa, INSTR(p.nazwa,'#')+1) AS INTEGER) DESC, data_zakupu DESC
            LIMIT ?
        ''', (limit,))
    else:
        return query_db('''
            SELECT p.*,
                   (SELECT COUNT(*) FROM produkty WHERE paleta_id = p.id) as produktow,
                   (SELECT COALESCE(SUM(CASE WHEN status IN ('sprzedany','wyslany','uszkodzony','naprawa','zlomowany') THEN 0 ELSE ilosc END), 0) FROM produkty WHERE paleta_id = p.id) as sztuk_w_magazynie,
                   (SELECT COALESCE(SUM(cena_allegro * ilosc), 0) FROM produkty WHERE paleta_id = p.id) as wartosc_detalu,
                   (SELECT COALESCE(SUM(cena_brutto), 0) FROM produkty WHERE paleta_id = p.id) as wartosc_zakupu_produktow,
                   (SELECT COALESCE(SUM(CASE WHEN status = 'sprzedany' THEN 1 ELSE 0 END), 0) FROM produkty WHERE paleta_id = p.id) as sprzedano_status,
                   COALESCE((SELECT SUM(s.ilosc) FROM sprzedaze s JOIN produkty pr ON s.produkt_id = pr.id WHERE pr.paleta_id = p.id AND s.status NOT IN ('anulowana', 'zwrot') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')), 0) as sprzedano_tabela,
                   (SELECT COALESCE(SUM(CASE WHEN status = 'sprzedany' THEN cena_allegro ELSE 0 END), 0) FROM produkty WHERE paleta_id = p.id) as sprzedano_wartosc_status,
                   COALESCE((SELECT SUM(s.cena * s.ilosc) FROM sprzedaze s JOIN produkty pr ON s.produkt_id = pr.id WHERE pr.paleta_id = p.id AND s.status NOT IN ('anulowana', 'zwrot') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')), 0) as sprzedano_wartosc_tabela,
                   (SELECT COALESCE(SUM(CASE WHEN status = 'sprzedany' THEN cena_brutto ELSE 0 END), 0) FROM produkty WHERE paleta_id = p.id) as sprzedano_koszt,
                   (SELECT COALESCE(SUM(CASE WHEN cena_brutto > 0 THEN cena_brutto WHEN cena_netto > 0 THEN cena_netto * 1.23 ELSE 0 END), 0) FROM produkty WHERE paleta_id = p.id) as koszt_produktow_all,
                   0 as sprzedano_offline,
                   0 as przychod_offline,
                   (SELECT COALESCE(SUM(ilosc), 0) FROM produkty WHERE paleta_id = p.id) as sztuk_lacznie_total
            FROM palety p
            ORDER BY CAST(SUBSTR(p.nazwa, INSTR(p.nazwa,'#')+1) AS INTEGER) DESC, data_zakupu DESC
            LIMIT ?
        ''', (limit,))


def add_paleta(nazwa, dostawca, cena_zakupu, data_zakupu=None, notatki='', regal='', typ='paleta'):
    """Dodaje nową paletę/box. cena_zakupu = brutto z faktury. typ = 'paleta' lub 'box'."""
    if not data_zakupu:
        data_zakupu = datetime.now().strftime('%Y-%m-%d')

    try:
        cena_brutto = float(cena_zakupu) if cena_zakupu else 0
    except:
        cena_brutto = 0
    cena_netto = round(cena_brutto / 1.23, 2) if cena_brutto > 0 else 0

    with get_db() as conn:
        cur = conn.execute('''
            INSERT INTO palety (nazwa, dostawca, cena_zakupu, cena_zakupu_netto, data_zakupu, notatki, regal, typ)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (nazwa, dostawca, cena_brutto, cena_netto, data_zakupu, notatki, regal, typ or 'paleta'))
        paleta_id = cur.lastrowid
        conn.commit()
        return paleta_id


# ============================================================
# HISTORIA PRODUKTU
# ============================================================

def add_historia(produkt_id, akcja, opis, dane=None):
    """
    Dodaje wpis do historii produktu.
    
    Akcje:
    - 'dodano' - produkt dodany do magazynu
    - 'edytowano' - edycja produktu
    - 'wystawiono' - wystawiono na Allegro
    - 'sprzedano' - sprzedaż
    - 'wyslano' - wysłano do klienta
    - 'zmiana_ceny' - zmiana ceny
    - 'zmiana_lokalizacji' - zmiana lokalizacji
    - 'zmiana_ilosci' - zmiana ilości
    - 'drukowano' - wydrukowano etykietę
    - 'skanowano' - zeskanowano produkt
    - 'importowano' - zaimportowano z Excel/CSV
    - 'scrapowano' - scrapowano z Amazon
    - 'wygenerowano_opis' - wygenerowano opis AI
    - 'dodano_zdjecia' - dodano zdjęcia
    - 'przeniesiono' - przeniesiono między paletami
    - 'oznaczono' - oznaczono/otagowano
    """
    import json
    with get_db() as conn:
        conn.execute('''
            INSERT INTO historia_produktu (produkt_id, akcja, opis, dane_json)
            VALUES (?, ?, ?, ?)
        ''', (produkt_id, akcja, opis, json.dumps(dane) if dane else None))
        conn.commit()


def get_historia(produkt_id, limit=20):
    """Pobiera historię produktu"""
    return query_db('''
        SELECT * FROM historia_produktu
        WHERE produkt_id = ?
        ORDER BY data DESC
        LIMIT ?
    ''', (produkt_id, limit))


def log_admin_action(action, details=None, success=True, error_message=None,
                     user_id=None, username=None, role=None,
                     ip_address=None, user_agent=None):
    """Zapisz krytyczna akcje administracyjna do audit logu.

    Loguje kto/kiedy/co/skad wykonal privileged action. Dla:
    - /system/update (git pull + restart)
    - /system/gemini-model (zmiana AI = koszty)
    - /backup/create, /backup/restore (manipulacja danymi)
    - /admin/update (upload ZIP)
    - Zmiany userow, ustawien bezpieczenstwa

    Parametry user_id/username/role/ip domyslnie pobierane z flask.session
    i flask.request — wystarczy wolac log_admin_action('system_update', {...}).

    Args:
        action: short code ('system_update', 'backup_restore', itp.)
        details: dict z dodatkowymi danymi (serialized do JSON)
        success: czy akcja sie powiodla
        error_message: komunikat bledu jesli success=False
    """
    import json as _json
    try:
        from flask import session, request
        # Auto-fill z flask context jesli dostepny
        if user_id is None:
            user_id = session.get('user_id')
        if username is None:
            username = session.get('username', '')
        if role is None:
            role = session.get('rola', '')
        if ip_address is None:
            # Uzyj prawdziwego IP (CF-Connecting-IP > X-Real-IP > remote_addr)
            ip_address = (
                request.headers.get('CF-Connecting-IP')
                or request.headers.get('X-Real-IP')
                or request.remote_addr
                or ''
            )
        if user_agent is None:
            user_agent = request.headers.get('User-Agent', '')[:500]  # trim long UA
    except (ImportError, RuntimeError):
        # Poza Flask context (CLI, scheduler) — OK, uzyj przekazanych wartosci
        pass

    details_json = _json.dumps(details, default=str, ensure_ascii=False) if details else None

    try:
        with get_db() as conn:
            conn.execute('''
                INSERT INTO admin_audit_log
                (user_id, username, role, action, details, ip_address,
                 user_agent, success, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, username, role, action, details_json,
                  ip_address, user_agent, 1 if success else 0, error_message))
            conn.commit()
    except Exception as _e:
        # Audit log failure NIE MOZE blokowac akcji biznesowej — log do stderr
        print(f"[WARN] admin_audit_log insert failed: {_e}")


def get_admin_audit_log(limit=100, action=None, user_id=None):
    """Pobiera ostatnie wpisy audit logu, opcjonalnie filtruj po akcji/userze."""
    sql = 'SELECT * FROM admin_audit_log WHERE 1=1'
    args = []
    if action:
        sql += ' AND action = ?'
        args.append(action)
    if user_id:
        sql += ' AND user_id = ?'
        args.append(user_id)
    sql += ' ORDER BY timestamp DESC LIMIT ?'
    args.append(limit)
    return query_db(sql, tuple(args))


def get_historia_all(limit=50):
    """Pobiera ostatnią historię ze wszystkich produktów"""
    return query_db('''
        SELECT h.*, p.nazwa as produkt_nazwa
        FROM historia_produktu h
        LEFT JOIN produkty p ON h.produkt_id = p.id
        ORDER BY h.data DESC
        LIMIT ?
    ''', (limit,))


_insights_cache = {}
_insights_cache_time = 0
_INSIGHTS_TTL = 60  # seconds

def get_insights():
    """Analiza sprzedaży — insights na dashboard (cached 60s)"""
    global _insights_cache, _insights_cache_time
    now = time.time()
    if (now - _insights_cache_time) < _INSIGHTS_TTL and _insights_cache:
        return _insights_cache

    conn = get_db()
    insights = {}

    # 1. [LOCA] Najszybciej schodzące (top 8, ostatnie 30 dni)
    insights['top_sellers'] = conn.execute('''
        SELECT p.id, p.nazwa, p.zdjecie_url, p.ilosc as stan,
               COUNT(s.id) as sprzedano_szt,
               COALESCE(SUM(s.cena * s.ilosc), 0) as przychod,
               p.kategoria
        FROM sprzedaze s
        JOIN produkty p ON p.id = s.produkt_id
        WHERE s.data_sprzedazy >= date('now', '-30 days')
          AND s.status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
        GROUP BY p.id
        ORDER BY sprzedano_szt DESC
        LIMIT 8
    ''').fetchall()

    # 2. [WARN] Kończy się — niski stan + miały sprzedaże = warto dokupić
    insights['low_stock'] = conn.execute('''
        SELECT p.id, p.nazwa, p.zdjecie_url, p.ilosc as stan,
               COUNT(s.id) as sprzedano_szt,
               COALESCE(SUM(s.cena * s.ilosc), 0) as przychod,
               p.kategoria, p.cena_allegro
        FROM produkty p
        LEFT JOIN sprzedaze s ON s.produkt_id = p.id
            AND s.status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
        WHERE p.ilosc <= 2 AND p.ilosc > 0
          AND p.status IN ('magazyn', 'wystawiony')
        GROUP BY p.id
        HAVING sprzedano_szt > 0
        ORDER BY sprzedano_szt DESC
        LIMIT 8
    ''').fetchall()

    # 3. 💡 Warto dokupić — dostawcy/kategorie z najlepszym ROI
    insights['best_categories'] = conn.execute('''
        SELECT p.kategoria,
               COUNT(DISTINCT p.id) as produktow,
               COALESCE(SUM(s.cena * s.ilosc), 0) as przychod,
               COUNT(s.id) as sprzedazy,
               ROUND(AVG(julianday(s.data_sprzedazy) - julianday(p.data_dodania)), 0) as avg_dni_do_sprzedazy
        FROM produkty p
        JOIN sprzedaze s ON s.produkt_id = p.id
            AND s.status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
        WHERE p.kategoria IS NOT NULL AND p.kategoria != ''
          AND s.data_sprzedazy >= date('now', '-90 days')
        GROUP BY p.kategoria
        HAVING sprzedazy >= 2
        ORDER BY przychod DESC
        LIMIT 6
    ''').fetchall()

    # 4. [BLOC] Czego unikać — leżą >60 dni bez sprzedaży
    insights['stale'] = conn.execute('''
        SELECT p.id, p.nazwa, p.zdjecie_url, p.ilosc as stan,
               p.cena_allegro,
               CAST(julianday('now') - julianday(p.data_dodania) AS INTEGER) as dni_w_magazynie,
               pal.dostawca,
               ROUND(COALESCE(pal.cena_zakupu, 0) / NULLIF(pal.ilosc_produktow, 0), 2) as koszt_szt
        FROM produkty p
        LEFT JOIN palety pal ON pal.id = p.paleta_id
        LEFT JOIN sprzedaze s ON s.produkt_id = p.id
            AND s.status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
        WHERE p.status IN ('magazyn', 'wystawiony')
          AND p.ilosc > 0
          AND p.data_dodania <= date('now', '-60 days')
        GROUP BY p.id
        HAVING COUNT(s.id) = 0
        ORDER BY dni_w_magazynie DESC
        LIMIT 8
    ''').fetchall()

    _insights_cache = insights
    _insights_cache_time = time.time()
    return insights
