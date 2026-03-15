# CHANGELOG - AKCES HUB Enterprise

## 2026-03-15 — Szablony HTML, backup Google Drive, reinstall Pi, bezpieczenstwo

### System logowania (auth)
- Nowy modul `modules/auth.py` — Flask Blueprint z sesja
- Hashowanie hasel SHA-256 + sol (salt)
- Rate limiting: max 5 prob na 15 min per IP (ochrona brute-force)
- First-run setup — tworzenie konta admin przy pierwszym uruchomieniu
- Middleware `before_request` wymuszajacy logowanie na wszystkich stronach
- Publiczne endpointy: `/static/`, `/api/health`, login, setup
- Przycisk wylogowania w `templates/base.html`
- Sesja wygasa po 24h

### Bezpieczenstwo
- Auto-generowany `SECRET_KEY` zapisywany do `.secret_key` (nie hardcoded)
- `.gitignore` — chroni `.secret_key`, `.env`, `akces_hub.db`, `backups/`, `gemini_config.py`
- Audyt SQL injection (bandit): 42 f-string execute() sprawdzone — 0 prawdziwych luk
- Naprawiono 1 prawdziwy SQL injection w `app.py:13454` (string-join IDs → parametryzowane)
- `timeout=` dodany do WSZYSTKICH wywolan `requests.get/post/put/patch/delete` w calym projekcie
- pip-audit: zaktualizowano wszystkie podatne pakiety do czystego stanu

### Szablony HTML
- Wyciagnieto 13 szablonow HTML z `app.py` do folderu `templates/`
- Stworzono `templates/base.html` — wspolny layout (nav, CSS, JS)
- Pliki: home, kiosk_home, wysylki, pakowanie, narzedzia, kalkulator, generator, export, raporty, powiadomienia, wybor_konta, dziadek, offline
- Zamieniono `render_template_string()` na `render_template()`
- `app.py` skrocony o ~1880 linii (z ~16600 do ~14650)

### Backup na Google Drive
- Dodano sync backupow do Google Drive przez `rclone` w `backup_manager.py`
- Po kazdym backupie automatycznie wysyla kopie na GDrive (w tle)
- Endpoint `/backup/sync-gdrive` do recznego sync
- Cache sprawdzania rclone — sprawdza raz, nie przy kazdym uzyciu

### Optymalizacja backup daemona
- `threading.Event` zamiast busy-wait (3600x `sleep(1)`) — natychmiastowe zatrzymanie
- Bare `except: pass` zamienione na logowanie bledow
- Usunieto bezuzyteczny `--progress` z `capture_output`

### Raspberry Pi
- Reinstall Pi OS (Bookworm) z powodu "failed to start session"
- Przygotowano konfiguracje kiosku (Wayfire autostart + Flask + Chromium)

---

## 2026-03-06 — Integracje OLX i Vinted

### OLX
- Dodano `modules/olx_api.py` — OAuth2, OLX Partner API (developer.olx.pl)
- Tabela DB: `olx_oferty`
- Strona konfiguracji, tworzenie/publikacja/usuwanie ofert

### Vinted
- Dodano `modules/vinted_api.py` — HMAC-SHA256, Vinted Pro API
- Tabela DB: `vinted_items`
- Strona konfiguracji, tworzenie/publikacja/usuwanie ofert

### Wspolne
- Przyciski "Wystaw na OLX/Vinted" na stronie szczegolowej produktu

---

## 2026-03-06 — Optymalizacja bazy danych i naprawy

### Wydajnosc bazy
- Usunieto `PRAGMA integrity_check` z `get_db()` — skanowalo cala baze przy kazdym polaczeniu
- Zmieniono `PRAGMA synchronous` z FULL na NORMAL (bezpieczne z WAL)
- Usunieto 95x `conn.close()` na poolowanych polaczeniach (lamalo pool, lock contention)
- Naprawiono 3 puste bloki `finally:` po usunieciu conn.close()

### Naprawa kalkulacji zysku
- Blad: uzywalo `cena_brutto` (MSRP/Amazon) jako koszt zakupu
- Poprawka: koszt = `palety.cena_zakupu` (rzeczywisty koszt palety)
- Formula: zysk = przychod - koszt_palet_miesiaca - prowizja_11%
