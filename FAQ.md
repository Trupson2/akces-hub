# FAQ - Najczesciej Zadawane Pytania

---

## INSTALACJA & SETUP

**Q: Jakie sa wymagania systemowe?**
A: Raspberry Pi 4 (2GB+ RAM), dowolny VPS z Linuxem, lub Windows/Mac. Python 3.7+, 2GB dysku minimum (8GB+ zalecane na zdjecia).

**Q: Czy dziala na Raspberry Pi?**
A: Tak — to glowna platforma docelowa. Raspbian Bookworm 64-bit, Flask + SQLite, kiosk mode (Chromium fullscreen).

**Q: Czy dziala na Mac?**
A: Tak, wymaga Python 3.7+. Instalacja identyczna jak na Linux.

**Q: Czy potrzebuje internetu?**
A: Tak — do API Allegro, scrapingu Amazon, Gemini AI. Magazyn lokalnie dziala offline, ale synchronizacja wymaga sieci.

**Q: Ile zajmuje instalacja?**
A: 15-30 minut. Na Raspberry Pi: `git clone`, `pip install -r requirements.txt`, `python app.py`.

**Q: Jak uruchomic na nowym Raspberry Pi?**
A:
```bash
git clone <repo-url> /home/pi/akces-hub
cd /home/pi/akces-hub
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```
Przy pierwszym uruchomieniu system poprosi o stworzenie konta admin.

**Q: Jak zaktualizowac system?**
A:
```bash
cd /home/pi/akces-hub
git pull
pip install -r requirements.txt
sudo systemctl restart akceshub
```

---

## TECHNICZNE

**Q: Jaka baza danych?**
A: SQLite z WAL mode — plik lokalny, zero konfiguracji, connection pooling per thread.

**Q: Czy moge zmienic na MySQL/PostgreSQL?**
A: Tak, ale wymaga modyfikacji kodu. SQLite wystarcza dla 95% uzytkownikow (testowane na 100k+ rekordow).

**Q: Czy dane sa bezpieczne?**
A: Tak:
- Wszystko lokalnie (twoj serwer, twoje dane)
- Logowanie z hashowaniem SHA-256 + sol
- Rate limiting: max 5 prob logowania na 15 min per IP
- Auto-generowany SECRET_KEY (nie hardcoded)
- Audyt SQL injection (bandit) — parametryzowane zapytania
- Timeouty na wszystkich requestach HTTP

**Q: Co jesli zawiesi sie system?**
A: `sudo systemctl restart akceshub` (Pi) lub Ctrl+C + `python app.py`. Dane bezpieczne — SQLite WAL zapewnia integralnosc.

**Q: Czy moge uzywac na kilku komputerach?**
A: System dziala jako serwer web — wchodzisz przez przegladarke z dowolnego urzadzenia w sieci.

---

## MAGAZYN

**Q: Jak dodac produkty z palety?**
A: 4 sposoby:
1. **Z Amazona** — wklej ASIN w Paletomat → system sciaga zdjecia, opisy, specyfikacje
2. **Import hurtowy** — skopiuj HTML ze strony dostawcy → system rozpozna produkty automatycznie
3. **Recznie** — dodaj produkt w Magazynie, wpisz dane recznie
4. **CSV import** — przygotuj plik CSV i zaimportuj hurtowo (3-krokowy wizard z podgladem)

**Q: Jak dziala skanowanie?**
A: Skanuj 3 typy kodow:
- **EAN** (kod kreskowy) — wyszukuje produkt po EAN
- **ASIN** (kod Amazon, np. B0CZ3W8SRK) — wyszukuje po ASIN
- **MAG-XXXXX** (wewnetrzny) — wyszukuje po kodzie systemowym

Dzialaja skanery USB, Bluetooth, oraz kamera telefonu (przez przegladarke).

**Q: Jakie drukarki etykiet dzialaja?**
A: **Niimbot B1** (Bluetooth/BLE) — glowna obslugiwana. Etykiety 4x6 cali z QR kodem + nazwa + cena, 203 DPI. Adapter BT 5.0 zalecany.

**Q: Co to jest wizualizacja 3D magazynu?**
A: Interaktywny edytor ukladu magazynu — regaly, polki, pozycje. Kazdy produkt ma przypisana lokalizacje. Przydatne gdy masz 100+ produktow.

---

## PALETOMAT (wystawianie na Allegro)

**Q: Jak wystawic produkt na Allegro?**
A:
1. Wejdz w produkt w Magazynie → "Wystaw na Allegro"
2. System sprawdza deduplikacje (czy nie jest juz wystawiony)
3. Generator: gotowy tytul (SEO), opis HTML, do 8 zdjec, auto-cena
4. Sprawdz, popraw, kliknij "Utworz oferte" → draft
5. "Publikuj" gdy gotowe

**Q: Jak dziala masowe wystawianie?**
A: Wejdz w palete → "Wystaw cala palete". System kolejno tworzy oferty dla kazdego produktu. Postep na zywo (Server-Sent Events). 10-50 produktow w kilka minut.

**Q: Co to jest Nocny Kombajn?**
A: Automatyczny cron (~2:00 w nocy):
1. Szuka produktow bez ofert (status "magazyn")
2. Sciaga dane z Amazona
3. Generuje opisy via Gemini AI
4. Tworzy drafty na Allegro
5. Rano: przegladas, poprawiasz ceny, publikujesz

Oszczedza 1-2h dziennie.

**Q: Jak dzialaja AI zdjecia?**
A: Gemini 2.0 Flash generuje dodatkowe zdjecia:
- **Wymiary** — produkt z liniami pomiarowymi
- **W uzyciu** — produkt w kontekscie (np. lampa na biurku)
- **Lifestyle** — produkt w stylowej scenerii

Oryginalne zdjecia z Amazona na pozycjach 1-4, AI-generowane na 5-7.

**Q: Jak dziala auto-wycena?**
A: `cena = (koszt_jednostkowy / (1 - prowizja)) * (1 + marza)`
- Koszt jednostkowy = cena_zakupu_palety / ilosc_produktow
- Prowizja = 11% (Allegro, konfigurowalne per kategoria)
- Marza = 20-40% (konfigurowalne)

**Q: Co jesli produkt juz jest wystawiony na Allegro?**
A: System sprawdza po EAN i ASIN. Jesli znajdzie aktywna oferte — proponuje **dodanie sztuk** do istniejacej zamiast tworzenia nowej. Mozna tez wymusic nowa oferte.

---

## ALLEGRO

**Q: Czy Allegro API jest platne?**
A: NIE. Allegro REST API jest calkowicie darmowe. Potrzebujesz konta firmowego + client_id/secret z panelu deweloperskiego.

**Q: Zamowienia nie synchronizuja sie — co robic?**
A:
1. Sprawdz token: `/allegro/check`
2. Kliknij "Synchronizuj" na stronie zamowien
3. System matching: oferta_id → EAN → ASIN regex → smart text (confidence >= 0.55)
4. Recznie: `/allegro/polacz-sprzedaze`

**Q: Stan magazynowy nie odejmuje sie po sprzedazy?**
A: System odejmuje gdy sync_orders znajdzie dopasowanie zamowienia do produktu. Jesli matching zawiedzie (brak EAN/ASIN) — stan nie zostanie odjety. Rozwiazanie:
1. Upewnij sie ze oferty maja poprawne EAN/ASIN
2. Uruchom backfill: `/allegro/polacz-sprzedaze`
3. Popraw recznie w karcie produktu

**Q: Allegro OAuth error?**
A: Sprawdz Client ID/Secret, Redirect URL musi byc: `http://localhost:5000/allegro/callback` (lub twoj publiczny URL). Tokeny odswiezaja sie automatycznie.

---

## ANALITYKA

**Q: Jak sprawdzic czy paleta sie oplacila?**
A: `/analityka/palety` — ROI per paleta:
- **ROI > 100%** = paleta sie oplacila
- **Koszt/sztuke** = ile zaplaciles za 1 produkt
- **Zysk/sztuke** = ile zarabiasz srednio

**Q: Co to sa "okazje"?**
A: `/analityka/okazje` skanuje strony Twoich dostawcow palet:
- **Monitoring dostawcow** — automatyczne skanowanie nowych ofert (konfigurowalne zrodla)
- **Filtrowanie** — po slowach kluczowych i kategoriach
- **Perplexity AI** — doglebna analiza konkretnej palety
- **Przelicznik walut** — ceny GBP/EUR automatycznie na PLN (kurs NBP)

---

## POWIADOMIENIA

**Q: Jak skonfigurowac Telegram?**
A:
1. @BotFather na Telegramie → `/newbot` → skopiuj token
2. `/telegram/config` → wklej token + chat_id
3. "Test" → powinien przyjsc komunikat

**Q: Jakie powiadomienia dostane?**
A:
- Nowa sprzedaz — natychmiast
- Nowa okazja paletowa — alert
- Raport dzienny — 8:00
- Raport tygodniowy — poniedzialki

**Q: Czy jest WhatsApp?**
A: Tak, przez TextMeBot API. Konfiguracja w `/telegram` (ten sam modul).

---

## BACKUP

**Q: Jak dziala backup?**
A:
- **Co godzine** — automatyczna kopia SQLite do `/backups/`
- **Google Drive** — sync przez rclone po kazdym backupie
- **24 kopie rotacyjne** — ostatnie 24 godziny
- **Restore** — `/magazyn/backup` → wybierz backup → "Przywroc"

**Q: Czy moge robic backup recznie?**
A: Tak — `/magazyn/backup` → "Utworz backup teraz". Mozesz tez wymusic sync na GDrive: `/backup/sync-gdrive`.

---

## OLX / VINTED

**Q: Czy moge sprzedawac na OLX i Vinted?**
A: Moduly istnieja, ale sa opcjonalne (domyslnie wylaczone). Wlacz w `/ustawienia`:
- **OLX** — wymaga konta deweloperskiego na developer.olx.pl (OAuth2)
- **Vinted** — wymaga konta Vinted (cookie-based auth)

---

## PROBLEMY

**Q: Blad 500 na stronie?**
A: Sprawdz `logs/akces_hub.log` — pelny traceback. Najczestsze przyczyny:
- Wygasly token API (Allegro/Gemini) → `/allegro/auth`
- Baza zablokowana → `sudo systemctl restart akceshub`
- Brakujacy modul → `pip install -r requirements.txt`

**Q: System wolno dziala na RPi?**
A:
- Sprawdz `htop` — CPU/RAM
- Baza > 500MB? Archiwizuj stare dane
- Duzo zdjec? Cleanup: `/paletomat/generator/cleanup`
- WAL checkpoint: mozna uruchomic z poziomu `/magazyn/backup`

**Q: Scraping Amazon nie znajduje produktow?**
A: Sprawdz: ASIN to 10 znakow (zaczyna sie od B0), VPN moze blokowac, produkt moze byc usuniety z Amazona.

**Q: Import CSV nie dziala?**
A: Sprawdz: format .xlsx/.csv, kolumny z nazwa/ASIN/cena, dane w pierwszym arkuszu. Wizard importu pokazuje podglad przed wykonaniem.

---

*Nie znalazles odpowiedzi? Sprawdz logi (`logs/akces_hub.log`) lub napisz do supportu.*

*FAQ aktualizowane: 2026-03-16*
