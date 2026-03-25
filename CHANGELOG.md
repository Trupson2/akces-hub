# Historia zmian — Akces Hub

## 25.03.2026

### Oceń stan — Split tworzy osobne produkty
- [NOWE] Split mode w "Oceń stan" tworzy osobne rekordy produktów w bazie per stan
- [NOWE] Każdy split produkt dostaje auto-generated kod_magazynowy (MAG-XXXXX)
- [NOWE] Historia zmian (add_historia) dodawana dla każdego split produktu
- [NOWE] Ewidencja sztuk tworzona per split produkt (nie tylko oryginał)
- [NOWE] Badge klasy jakości na liście produktów (A=lime, A-=cyan, B=yellow, C=orange, D=red)
- [NOWE] Stan przyjęcia widoczny na liście produktów w magazynie
- [FIX] kod_magazynowy wykluczony z kopiowania przy split (trigger generuje nowy)

### EULA — Cyberpunk Terminal Redesign
- [NOWE] Szablon `eula.html` — terminal-style cyberpunk z bento grid layout
- [NOWE] Terminal reader z kolorowym scrollem (lime text, cyan/pink/lime headers)
- [NOWE] Sidebar: podsumowanie, quick navigation, kontakt
- [NOWE] Terminal bar z dots (pink/lime/cyan) + footer (integrity/auth status)
- [NOWE] Data grid cards (zbierane dane + standard szyfrowania)
- [NOWE] Ambient glow effects (cyan top-right, pink bottom-left)
- [NOWE] Numbered sections z kolorowym badge (01-08)
- [ZMIANA] Ekstrakcja inline HTML z eula.py do templates/eula.html
- [ZMIANA] Route używa render_template() zamiast render_template_string()

### Karta produktu — Cyberpunk Redesign
- [NOWE] Szablon `produkt_detail.html` — pełny cyberpunk design (pd-* CSS classes)
- [NOWE] Hero section z produktem: zdjęcie, nazwa, status panel z neon border
- [NOWE] Quick actions grid: -1 SZT, ETYKIETA, EDYTUJ, WYSTAW, USUŃ
- [NOWE] Location panel z gradient top-border (cyan→pink→lime)
- [NOWE] Details grid: kod mag, EAN, ASIN, ilość, stan, klasa, koszty, zysk/szt
- [NOWE] Profit highlight card (full-width, kolorowany zielony/czerwony)
- [NOWE] Siblings panel — partie tego samego produktu per klasa
- [NOWE] Modals: GPSR, Rozbij na sztuki, Naprawa, Oceń stan — cyberpunk style
- [NOWE] Ewidencja sztuk z inline edycją stanu/statusu/notatek
- [NOWE] Filtr Jinja2 `parse_json` dla historii zmian produktu
- [ZMIANA] Ekstrakcja ~700 linii inline HTML z magazynier.py do szablonu Jinja2
- [ZMIANA] Route `produkt()` używa `render_template()` zamiast f-string budowania

### EAN/ASIN Display Fix
- [FIX] EAN "N/A"/"NAN"/"NONE" traktowane jako brak — fallback na ASIN
- [FIX] Poprawka w 5 miejscach: palety.py, magazynier.py (4x), magazyn_index.html
- [FIX] Kolumna `klasa_jakosci` nie istniała w DB — naprawione SQL queries w siblings

---

## 23.03.2026

### Stitch Design System — Dashboard Redesign
- [NOWE] Glass-morphism UI — klasy `glass-card`, `glass-panel` z backdrop-blur i saturate
- [NOWE] Neon color palette: cyan #00f1fe, purple #c180ff, green #5bf083
- [NOWE] Font Space Grotesk na nagłówkach (.font-display)
- [NOWE] Efekty neon-glow-primary/secondary/tertiary z box-shadow
- [NOWE] Animacje: neonPulse, bannerShimmer na kartach
- [NOWE] KPI karty ze sparkline barami (7-dniowa historia)
- [NOWE] System Monitor — okrągłe gauges z kolorowym border (Temp/CPU/RAM/Dysk)
- [NOWE] PALETOMAT OPEN banner — gradient shimmer + neon-pulse animation
- [NOWE] Monthly Results — glass-panel z Przychód/COGS/Zysk + secondary metrics
- [NOWE] Top Produkty — medale gold/silver/bronze przy rankingu
- [NOWE] Top Dostawcy — ROI gauge circles z kolorowym kodowaniem
- [NOWE] Ostatnia Aktywność → Live Feed z pionowymi color-barami + pulsujące dots
- [NOWE] Licencja — gradient tło, glass-panel stats, neon accents
- [NOWE] Top Produkty + Top Dostawcy side-by-side grid layout
- [NOWE] Activity + Licencja side-by-side grid layout
- [ZMIANA] Stitch CSS classes dodane do base.html (stitch-kpi, sparkline-*, sys-gauge, neon-text-*)
- [ZMIANA] Responsive overrides dla stitch layout (900px, 600px breakpoints)
- [FIX] PALETOMAT OPEN banner — gradient-clip niewidoczny na dark bg, zamieniony na neon-text

### Stitch na podstronach
- [NOWE] Magazynier (lista palet) — glass-card items, neon toolbar, cyan focus glow
- [NOWE] Palety (szczegóły, bulk import) — glass stats, neon progress bars, blur modals
- [NOWE] Analityka (bilans palet) — glass backgrounds, neon borders
- [NOWE] Warehouse 3D — dark bg, Space Grotesk, cyan grid, glass sidebar
- [NOWE] Command Center — moduły+akcje+analityka w jednej karcie glass-card
- [ZMIANA] Bulk color replacement: stare fioletowe/niebieskie → neon cyan/purple/green
- [ZMIANA] Container max-width 1400→1800px, fonty +15-25% na całym dashboardzie

---

## 22.03.2026

### System licencji i subskrypcje
- [NOWE] HWID Binding — licencja przypisana do konkretnego urządzenia
- [NOWE] Heartbeat 24h — weryfikacja licencji z serwerem co 24h
- [NOWE] Ochrona przed cofaniem zegara systemowego (2h grace period)
- [NOWE] Ekran wygaśnięcia subskrypcji z przyciskiem "Skopiuj ID klienta"
- [NOWE] Licznik dni do końca subskrypcji w sidebarze (kolorowy)
- [NOWE] Panel admina `/admin/subscriptions` — zarządzanie licencjami
- [NOWE] Przycisk "Przedłuż o 30 dni" w panelu admina
- [NOWE] Email + Telegram powiadomienia o wygasających licencjach (7 dni przed + w dniu)
- [NOWE] Konfiguracja SMTP w ustawieniach + test email
- [NOWE] Tabela `licenses_issued` z HWID, planem, datą wygaśnięcia

### EULA i Onboarding
- [NOWE] Ekran EULA — wymuszony scroll, checkbox odblokowany po przeczytaniu
- [NOWE] Onboarding Wizard 3-krokowy (Allegro API → backup → ustawienia)
- [NOWE] Flow: Licencja → Setup konta → EULA → Onboarding → Dashboard

### RODO i zgodność prawna
- [NOWE] Anonimizacja danych kupujących jednym kliknięciem
- [NOWE] Auto-retencja danych (3/5/7 lat) z auto-anonimizacją
- [NOWE] Parametry integracji (DPD cennik, warunki zwrotów/reklamacji)

### Auto-Updater i monitoring
- [NOWE] Auto-check aktualizacji co 6h (GitHub API)
- [NOWE] Fioletowy pasek "Dostępna aktualizacja" w topbarze
- [NOWE] Silent Crash Logger — error.log + Telegram webhook
- [NOWE] Globalna obsługa błędów z elegancką stroną 500

### Paletomat Open Teaser
- [NOWE] Pulsujący neonowy baner na dashboardzie
- [NOWE] Modal z zapowiedziami funkcji + Early Access webhook
- [NOWE] Lead collection przez Telegram

### Feature Requests
- [NOWE] Formularz "Zaproponuj nową funkcję" z priorytetem
- [NOWE] Wysyłka na Telegram + Discord webhook
- [NOWE] Podział Pomoc na "Zgłoś błąd" + "Zaproponuj funkcję"

### Demo Mode
- [NOWE] Przełącznik Demo Mode w topbarze (ikona oka)
- [NOWE] Ukrywanie nazw dostawców na DOSTAWCA_XXX
- [NOWE] localStorage — przetrwa odświeżanie
- [NOWE] Działa na: palety, produkty, analityka, dashboard

### Dostawcy dynamiczni
- [NOWE] Dynamiczna lista dostawców z bazy (nie hardcoded)
- [NOWE] "Dodaj nowego..." w dropdown — wpisz własnego dostawcę
- [NOWE] Auto-save nowego dostawcy do przyszłych formularzy
- [NOWE] Zarządzanie dostawcami w Ustawieniach

### Bulk Import
- [NOWE] Obsługa ZIP — wrzuć .zip z wieloma Excelami
- [NOWE] Auto-tworzenie palety z każdego pliku w ZIP
- [NOWE] Szybki przycisk "Import z ZIP" na górze formularza

### Kategorie produktów
- [NOWE] ~150 nowych słów kluczowych (peruki, rampy, zgrzewarki, huśtawki, pegi)
- [NOWE] Nowe kategorie: uroda, dom_ogrod rozszerzone
- [NOWE] Rozszerzony mapping Allegro category IDs

### UI/UX
- [ZMIANA] Brand name dynamiczny z configu (nie hardcoded)
- [ZMIANA] Spójne nazwy na wszystkich stronach
- [NOWE] Karta licencji na dashboardzie (plan, dni, data wygaśnięcia)

### Ngrok
- [FIX] Konfiguracja ngrok Pro (akceshub.ngrok.dev)
- [FIX] Authtoken w serwisie systemd
- [NOWE] Serwis autostart z `--url` flag

### Auto-wycena
- [NOWE] Gemini AI batch pricing (zamiast Amazon scrape)
- [NOWE] Instant CDN response + background scrape zdjęć
- [FIX] event.target → this w onclick handler

### Analiza palet
- [NOWE] Tłumaczenie nazw produktów na polski (Gemini)
- [NOWE] Klikalny ASIN z linkiem do Amazon
- [NOWE] Filtrowanie po słowie kluczowym (np. "peruka")
- [NOWE] Sortowanie po popycie (wysoki na górze)
- [NOWE] Weryfikacja cen na Allegro API
- [NOWE] Progress bar z % i info o batchu

### Zgrupuj w Box
- [NOWE] Zaznaczanie palet → "Zgrupuj w Box"
- [NOWE] Pole cena zakupu (ręczne zamiast auto-sumy RRP)
- [NOWE] Stare palety usuwane po zgrupowaniu

---

## 20.03.2026

### Paleta / Box rozdzielenie
- [NOWE] Kolumna `typ` w tabeli palety — rozroznienie palet i boxow
- [NOWE] Boxy nie sa liczone w statystykach palet (filtr COALESCE)
- [NOWE] Badge 📫 BOX na liscie palet
- [NOWE] Selektor typu (Paleta / Box) w formularzach importu

### Przyjecie palety
- [NOWE] Ekran przyjecia palety (/magazyn/przyjecie/<id>) — szybka ocena stanu produktow
- [NOWE] 5 stanow: Nowy, Jak nowy, Dobry, Uszkodzony, Zniszczony
- [NOWE] Tryb podzialu sztuk (✂️ Podziel) — rozne stany dla roznych sztuk tego samego produktu
- [NOWE] Przy zapisie podzialu system tworzy osobne rekordy w bazie
- [NOWE] Analiza AI zdjec (📸) przez Gemini 2.0 Flash — automatyczna ocena stanu i opis wad
- [NOWE] Pasek postepu ocenionych produktow
- [NOWE] Przycisk "Przyjecie" na stronie szczegalow palety

### Etykiety Niimbot
- [ZMIANA] Ukryto Vretti z UI (kod zachowany na przyszlosc)
- [NOWE] Stan przyjecia drukowany na etykiecie Niimbot
- [NOWE] Filtr po palecie na stronie etykiet
- [NOWE] Zaznacz wszystkie / Odznacz + licznik zaznaczonych
- [NOWE] Wyszukiwarka produktow na stronie etykiet
- [NOWE] Przycisk "Etykiety" na stronie szczegalow palety (z filtrem)
- [FIX] Przycisk DRUKUJ NIIMBOT — sticky zamiast fixed (nie naklada sie na produkty)

### Licencje
- [NOWE] Plan Enterprise (E) w systemie licencji
- [NOWE] Przycisk upgrade do Enterprise (dev-only)
- [FIX] Paletomat pokazywal BUSINESS zamiast ENTERPRISE

### Magazynier
- [NOWE] Masowe usuwanie palet — przycisk 🗑️ Usuń na liscie palet (z potwierdzeniem)
- [FIX] EAN / ASIN — kliknij aby skopiowac do schowka (📋)
- [FIX] Tekst EAN/ASIN zaznaczalny (user-select:all)
- [ZMIANA] Etykieta ceny w importach: "Cena zakupu (aukcja/faktura)" z ostrzezeniem
- [FIX] Dostawca i regal widoczne na stronie szczegalow palety (brakowalo w zapytaniu SQL)
- [FIX] Modal edycji palety poprawnie wyswietla dostawce i regal

### Kiosk
- [NOWE] Redesign dashboardu kiosk pod 16" monitor 1920x1200
- [FIX] Tryb kiosk nie przecieka na PC (URL-param only, bez cookies)

### Paletomat
- [NOWE] Wykres produktow (Chart.js) z przelacznikiem dziennie/laczna
- [FIX] Rozmiar wykresu — wrapper div z fixed height

### Baza danych
- [NOWE] Auto-migracja: typ, dostarczona, stan_przyjecia, notatki_przyjecia
- [NOWE] Klucz openai_api_key usuniety, uzywa gemini_api_key

---

## 19.03.2026

### Dashboard SaaS Redesign
- [NOWE] Kompletny redesign layoutu — sidebar nawigacja + topbar (styl SaaS)
- [NOWE] Ciemny motyw domyslny z animowanym tlem (czasteczki/konstelacje tech)
- [NOWE] Sidebar z sekcjami: Dashboard, Magazyn, Sprzedaz, Analityka + szybki dostep
- [NOWE] KPI karty na stronie glownej (sprzedaze, przychod, do wysylki)
- [NOWE] System panel z stat-row (Temp, CPU, RAM, Dysk)
- [NOWE] Moduly w gridzie z badge'ami (Paletomat, Magazyn, Allegro, Magazyn 3D)
- [NOWE] Drill-down panele dla Statystyki, Zakupy, Lezaki
- [NOWE] CSS design system: zmienne kolorow, .kpi-card, .card, .stat-row, .module-card, .qa-btn
- [NOWE] Responsive sidebar — hamburger menu na mobile (<900px)
- [ZMIANA] Usunieto stary bottom-nav, przeniesiono do sidebara
- [ZMIANA] Wszystkie hardcoded kolory zamienione na CSS variables

### Profit Analyzer
- [NOWE] Dashboard analizy zysków w stylu vSprint (/analytics/profit)
- [NOWE] Rachunek wyników (waterfall) — przychód, COGS, prowizja, koszty op., zysk netto
- [NOWE] Tabela P&L miesięczna z sumami
- [NOWE] Rentowność per paleta i per dostawca z ROI
- [NOWE] Wykres sprzedaży dziennej (30 dni) + zysku miesięcznego
- [NOWE] Top kategorii produktów (90 dni)
- [NOWE] Filtr czasowy: 3 / 6 / 12 miesięcy

### Generator licencji
- [NOWE] Panel generowania licencji w GUI (/narzedzia/licencje)
- [NOWE] Obsługa: dni, miesiące, bezterminowo
- [NOWE] Lista wygenerowanych licencji z kopiowaniem/pobieraniem JSON
- [FIX] Zabezpieczenie — dostęp tylko dla roli admin

### SSE & Dedup (fixes z 18-19.03)
- [FIX] SSE keepalive dla generowania opisu, GPSR i create_offer
- [FIX] SSE keepalive podczas uploadu zdjęć
- [FIX] Dedup: zaostrzenie matchowania nazw (ignore generic words, 50% threshold)
- [FIX] Dedup: EAN skip gdy produkt ma ASIN
- [FIX] Dedup: weryfikacja nazwy w kroku produkt_id + auto-odlinkowanie błędnych powiązań

---

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
