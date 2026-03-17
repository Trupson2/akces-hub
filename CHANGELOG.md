# Historia zmian — Akces Hub

## 17.03.2026

### Bezpieczenstwo (OWASP ZAP)
- Dodano naglowki bezpieczenstwa: CSP, HSTS, X-Frame-Options, X-Content-Type-Options
- Ukryto wersje serwera w odpowiedziach HTTP
- Ochrona CSRF dla formularzy (flask-wtf)
- Cache-control dla prywatnych stron (no-store)
- Rate limiting na logowanie (flask-limiter)

### System aktualizacji
- Dashboard pokazuje status wersji: "System aktualny" lub "Dostepna aktualizacja"
- Automatyczne sprawdzanie nowych wersji co 1 godzine
- Przycisk "Aktualizuj" — git pull + restart z poziomu przegladarki
- Powiadomienie Telegram o dostepnej aktualizacji

### Nowe funkcje
- Kreator pierwszej konfiguracji (branding, moduly) po instalacji
- Strona changelog — historia zmian po polsku
- Branding dostepny globalnie we wszystkich szablonach

---

## 16.03.2026

### Bezpieczenstwo (Bandit)
- Naprawiono wszystkie podatnosci SQL injection w 9 modulach
- Parametryzowane zapytania (OWASP Defense #1) zamiast f-stringow
- MD5 uzywany bez kontekstu bezpieczenstwa (usedforsecurity=False)
- Bandit: 0 HIGH, 0 nowych MEDIUM

### Strona /poziom (grywalizacja)
- Skill tree, osiagniecia, droga do 1M zlotych
- Przychod miesieczny i roczny z bazy danych (identycznie jak dashboard)
- Prognoza na koniec miesiaca i roku (srednia dzienna)
- Link na dashboardzie do strony /poziom

### Inne naprawy
- Generowanie meta tytulow — naprawiono blad SyntaxError (credentials fetch)
- Automatyczne wylogowanie po 30 minutach bezczynnosci
- Usunieto 36 plikow testowych z katalogu glownego

---

## 15.03.2026

### System logowania
- Modul auth.py — sesje, hashowanie hasel SHA-256 + sol
- Rate limiting: max 5 prob na 15 min (ochrona brute-force)
- First-run setup — tworzenie konta admin przy pierwszym uruchomieniu
- Automatyczny SECRET_KEY zapisywany do pliku

### Szablony HTML
- Wyciagnieto 13 szablonow z app.py do folderu templates/
- Wspolny layout base.html (nawigacja, CSS, JS)
- app.py skrocony o ~1880 linii

### Backup na Google Drive
- Automatyczny sync backupow przez rclone
- Endpoint /backup/sync-gdrive do recznego uruchomienia

### Optymalizacja
- Naprawa circular import (/analityka 500)
- Logowanie bledow 500/404 do plikow
- Stock sync — 3 nowe fallbacki (EAN, ASIN, smart text)
- Usunieto 647 linii martwego kodu

---

## 06.03.2026

### Integracje
- OLX Partner API — OAuth2, tworzenie/publikacja/usuwanie ofert
- Vinted Pro API — HMAC-SHA256, tworzenie/publikacja/usuwanie ofert
- Przyciski "Wystaw na OLX/Vinted" na stronie produktu

### Optymalizacja bazy danych
- Usunieto PRAGMA integrity_check z get_db() (skanowalo cala baze!)
- Zmieniono PRAGMA synchronous z FULL na NORMAL
- Usunieto 95x conn.close() na poolowanych polaczeniach

### Naprawa kalkulacji zysku
- Blad: uzywano ceny detalicznej zamiast kosztu zakupu palety
- Poprawka: zysk = przychod - koszt_palet - prowizja_11%

---

## Wczesniejsze wersje
- Paletomat — automatyczne wystawianie na Allegro
- Scraper produktow z Amazona (zdjecia, opisy, ceny)
- Magazyn 3D z heatmapa i wizualizacja regalow
- Generator AI opisow produktow (Gemini API)
- Modul wysylek InPost — etykiety, sledzenie paczek
- Telegram bot — powiadomienia o zamowieniach
- Kalkulator rentownosci palet
- System zarzadzania paletami zwrotow
- Dashboard z analityka sprzedazy
