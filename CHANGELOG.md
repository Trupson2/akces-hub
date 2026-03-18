# Historia zmian — Akces Hub

## 18.03.2026

### Etykiety i druk
- [NOWE] Usunięto barcode EAN z etykiet — zostaje sam QR kod (czytelniej)
- [NOWE] EAN wyswietlany jako tekst pod QR kodem
- [FIX] Etykiety jednolite — brak roznic miedzy produktami z/bez EAN

### Wysylki
- [NOWE] Nowy flow: nadanie etykiety → status "nadana" (nie znika z listy)
- [NOWE] Badge "NADANA" na liscie wysylek
- [FIX] Lokalizacja regalu widoczna w wysylkach (fallback po nazwie produktu)
- [FIX] Stare zamowienia z produkt_id=NULL tez pokazuja lokalizacje

### GPSR
- [FIX] GPSR zmieniony z SDK google.generativeai na REST API (dzialal tylko na PC)
- [FIX] Fallback template — zamiana znakow • na * (Allegro wymaga gwiazdek)

### Statystyki i wykresy
- [FIX] Wykres przychodu doliczal teraz prywatna sprzedaz do slupka
- [NOWE] Zolty slupek "w tym prywatna" na wykresie miesięcznym
- [FIX] Masowa edycja cen — wartosc mnozy cene × ilosc (nie tylko cene)

### System
- [FIX] Logowanie — database is locked nie blokuje juz logowania (timeout 30s + WAL)
- [OPTYM.] Update checker co 15 min zamiast 1h + reset cache przy restarcie
- [FIX] Ngrok domain zmieniony na unsatiating-dirgelike-audrina.ngrok-free.dev

---

## 17.03.2026

### Bezpieczenstwo (OWASP ZAP)
- [NOWE] Dodano naglowki bezpieczenstwa: CSP, HSTS, X-Frame-Options, X-Content-Type-Options
- Ukryto wersje serwera w odpowiedziach HTTP
- [SECURITY] Ochrona CSRF dla formularzy (flask-wtf)
- Cache-control dla prywatnych stron (no-store)
- Rate limiting na logowanie (flask-limiter)

### System aktualizacji
- Dashboard pokazuje status wersji: "System aktualny" lub "Dostepna aktualizacja"
- [NOWE] Automatyczne sprawdzanie nowych wersji co 15 minut
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
