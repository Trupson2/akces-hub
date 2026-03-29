# 📖 AKCES HUB ENTERPRISE — Instrukcja Użytkownika

> Wersja: marzec 2026 | Platforma: Raspberry Pi 5 + Flask + SQLite

---

## 🚀 URUCHOMIENIE SYSTEMU

```bash
cd ~/clean_v32
python app.py
```

Otwórz przeglądarkę: `http://localhost:5000` (lokalnie) lub przez ngrok (zdalnie).

**Nie zamykaj terminala** — app musi działać w tle. Możesz też ustawić autostart przez systemd.

---

## 📦 MODUŁ: PALETOMAT

### Import palety z Excela

1. **Paletomat** → **Import Excel**
2. Plik `.xlsx` z kolumnami: `Product Name`, `ASIN`, `EAN`, `Quantity`, `Price`
3. Podaj nazwę palety, dostawcę, cenę zakupu
4. System automatycznie:
   - Scrapuje Amazon (tytuły, zdjęcia, parametry)
   - Generuje wyceny AI
   - Tworzy oferty Allegro (drafty)

### Generowanie ofert Allegro

1. Wejdź w paletę → **Generuj oferty**
2. Sprawdź/edytuj ceny
3. **Wyślij do Allegro** → tworzy drafty w panelu Allegro

---

## 📍 MODUŁ: MAGAZYNIER

### Lokalizacja produktu

**Ręcznie:** Magazynier → Palety → Produkt → Edytuj lokalizację → Regal / Półka / Pozycja

**Skaner:** Zeskanuj QR na etykiecie → otwiera kartę produktu w przeglądarce

### Etykieta QR

1. Wejdź w produkt
2. **Drukuj etykietę** → wybierz drukarkę (Niimbot B1 / Vretti 420B)
3. Etykieta zawiera: nazwę, lokalizację (A1-2-3), QR

### Studio Foto 📸

**Magazyn → Studio Foto** (`/magazyn/studio-foto`)

- **Kolejka** — lista zleceń przetwarzania zdjęć z statusem (new / processing / done / error)
- **Bez packshotu** — grid produktów bez przetworzonego zdjęcia
- Kliknij **Dodaj do kolejki** → tworzy zlecenie dla photo daemona na Pi
- **Zlec wszystkie** → masowe dodanie do kolejki

Photo daemon (na Pi) odbiera zlecenia, usuwa tło przez ComfyUI + BiRefNet i generuje warianty:
- `allegro_main` — 1200×1200 px
- `vinted` — 800×800 px
- `thumb` — 300×300 px

Przy karcie każdego produktu widoczny jest badge statusu zdjęć.

---

## 📊 MODUŁ: ANALYTICS / DASHBOARD

### Co pokazuje Dashboard?

| Karta | Opis |
|-------|------|
| Przychód dziś / msc | Sprzedaż Allegro (bez offline, bez kosztów dostawy) |
| Produkty | Łączna liczba sztuk w magazynie |
| Palety | Aktywne palety |
| Wynik | Przychód − koszty palet − prowizja Allegro (11%) |

### Analityka

- Wykres sprzedaży miesięcznej
- TOP produkty / flopy (>60 dni bez ruchu)
- ROI per dostawca / paleta
- Oszczędność czasu (AI tytułów, automatyzacje)

### ROI Palety

```
ROI = (Sprzedaż - Koszt zakupu) / Koszt zakupu × 100%
```

---

## 🏆 MODUŁ: WINNING PRODUCTS

**Analityka → Winning Products** (`/analityka/winning`)

Automatyczna analiza bestsellerów Allegro — system ocenia produkty pod kątem potencjału sprzedażowego.

### Jak używać

1. Kliknij **🔄 Przelicz teraz**
2. System pobiera oferty z Allegro i scoruje każdy produkt (5 składowych)
3. Wyniki pojawiają się w tabeli posortowane od najlepszych

### Scoring — 5 składowych

| Składowa | Waga | Co mierzy |
|----------|------|-----------|
| Trend | 20% | Popularność (watchers, views, sprzedaż) |
| Konkurencja | 15% | Nasycenie rynku, % Smart |
| Marża | 35% | Szacowana marża vs koszty |
| Dopasowanie | 20% | Pasuje do Twojego portfolio |
| Zwroty | 10% | Ryzyko zwrotów dla tej kategorii |

### Akcje per produkt

- **🛒** — Dodaj do Listy Zakupów (panel na dole strony)
- **📝** — Szkic oferty — modal z tytułem/kategorią/ceną gotowym do copy-paste + link do Allegro
- **↗** — Otwórz podobną ofertę na Allegro
- **✕** — Ignoruj (znika z listy, nie wraca po odświeżeniu)

### Lista Zakupów

Przycisk **🛒 Lista zakupów** (góra strony) otwiera panel z pozycjami:
- Statusy: **Nowy → Zamówiony → Otrzymany → Pominięty**
- Zmieniasz status przez dropdown

### Konfiguracja

Winning Products działa na domyślnych kategoriach Allegro (Elektronika, AGD, Dom).
Aby ustawić własne kategorie — wklej ID kategorii z Allegro:

```bash
python -c "
from modules.database import set_config
import json
# ID kategorii z URL np. allegro.pl/kategoria/xxx
set_config('winning_categories', json.dumps(['258682', '257993']))
"
```

**Wymagane:** Allegro API musi być zalogowane (Ustawienia → Allegro → Autoryzuj).

**Cooldown:** 30 min między kolejnymi skanami (domyślnie). Skrócenie do testów:
```bash
python -c "from modules.database import set_config; set_config('winning_cooldown_minutes','1')"
```

---

## 🚚 MODUŁ: WYSYŁKI

**Wysyłki → Allegro** (`/wysylki/allegro`)

### Widok zamówień

Lista zamówień ze statusem `nowa` / `nadana`. Każde zamówienie pokazuje:
- Produkty, lokalizację magazynową, zdjęcia
- Badge: InPost / Orlen / DPD

### Pakowanie (Scan-to-Pack)

**Wysyłki → Skanuj** — stacja pakowania:
1. Zeskanuj zamówienie
2. System pokazuje co spakować + lokalizację
3. Potwierdź → status zmienia się na `spakowane`

### Nadawanie etykiet

#### Pojedyncze zamówienie
Kliknij **Nadaj** przy zamówieniu → etykieta PDF otwiera się w nowej karcie.

#### Bulk — wiele naraz ✨ NOWE
1. Zaznacz checkboxy przy zamówieniach
2. Kliknij **📦 Nadaj zaznaczone** (w pasku bulk)
3. Modal z progress barem — system nadaje sekwencyjnie:
   - InPost / paczkomat → gabaryt B (domyślnie)
   - Orlen Paczka → gabaryt M + auto-zamawia podjazd
   - DPD / kurier → 30×25×15 cm, 1 kg
4. Po zakończeniu: podsumowanie + **Drukuj etykiety** → otwiera każdą w nowej karcie

#### Oznaczanie jako wysłane (bez etykiety)
Zaznacz zamówienia → **Wyślij zaznaczone** → status `wyslana`, znikają z listy.

---

## 🛒 MODUŁ: ALLEGRO

### Połączenie z Allegro API

1. [https://apps.developer.allegro.pl/](https://apps.developer.allegro.pl/) → Nowa aplikacja
2. Redirect URL: `http://localhost:5000/allegro/callback` (lokalnie) lub ngrok URL
3. Skopiuj **Client ID** i **Client Secret**
4. **Ustawienia → Allegro API** → wklej dane → **Autoryzuj**

**Token wygasa co ~12h** — system auto-odświeża w tle. Jeśli coś nie działa, kliknij "Odśwież token".

---

## 📸 PHOTO DAEMON (Raspberry Pi 5)

Daemon działa w tle i przetwarza zdjęcia przez ComfyUI (RTX 3070 PC) z modelem BiRefNet.

### Uruchomienie

```bash
# Watcher — skanuje folder INBOX/ co minutę
python photo_daemon/photo_watcher.py

# Worker — przetwarza zlecenia z kolejki
python photo_daemon/photo_worker.py

# Panel statusu (port 5051)
python photo_daemon/status_app.py
```

Lub przez cron:
```
* * * * * cd ~/clean_v32 && python photo_daemon/photo_watcher.py
* * * * * cd ~/clean_v32 && python photo_daemon/photo_worker.py
```

### Jak działa

1. Wrzuć zdjęcie do `INBOX/` (nazwa = SKU produktu, np. `EAN123456.jpg`)
2. Watcher wykrywa → tworzy zlecenie w DB
3. Worker: orientacja EXIF → crop → enhance → ComfyUI (usuwanie tła) → 3 warianty
4. Wyniki zapisywane w `processed_photos` + status produktu `images_ready=1`

### Konfiguracja (`photo_daemon/config.yaml`)

```yaml
comfyui:
  url: "http://192.168.1.x:8188"  # IP komputera z RTX 3070
  mock_mode: false                # true = pomija ComfyUI (do testów)
paths:
  inbox: "/home/pi/INBOX"
  db_path: "/home/pi/clean_v32/akces_hub.db"
```

---

## 💰 MODUŁ: PODATKI / FINANSE

**Magazynier → Podatki** (`/magazyn/podatki`)

Pokazuje przychód za **bieżący rok** (domyślnie 2026):
- Sprzedaż Allegro (bez offline, bez kosztów dostawy)
- Sprzedaż prywatna (kable, inne — z zakładki Sprzedaż Prywatna)
- Prowizja Allegro (11%)
- Szacunkowy podatek (ryczałt 3% dla palety zwrotów)

---

## 🔧 ROZWIĄZYWANIE PROBLEMÓW

### Winning Products — 0 wyników

1. **Allegro token wygasł** → Ustawienia → Allegro → Odśwież token
2. **Próg za wysoki** → przesuń suwak Min. Szansa na 0.00
3. **Brak kategorii** → skonfiguruj ID kategorii Allegro (patrz sekcja Konfiguracja wyżej)

### System się nie uruchamia

```bash
python --version          # Sprawdź Python 3.10+
pip install -r requirements.txt
lsof -i :5000             # Czy port jest zajęty?
```

### Import Excel nie działa

- Plik musi być `.xlsx` (nie `.xls`)
- Dane w pierwszym arkuszu
- Kolumny: `Product Name`, `EAN`, `Quantity`, `Price`

### Drukarka etykiet nie działa

**Niimbot B1 (Bluetooth):**
- Sparuj w ustawieniach systemowych
- Sprawdź czy adapter BT 5.0 działa

**Vretti 420B (USB):**
- Sprawdź port COM (Menedżer urządzeń)
- Spróbuj inny kabel USB

### Photo Daemon nie przetwarza

1. Sprawdź czy ComfyUI działa na PC: `http://192.168.1.x:8188`
2. Sprawdź IP w `config.yaml`
3. Ustaw `mock_mode: true` żeby testować bez ComfyUI

---

## ❓ FAQ

**Q: Czy mogę używać na kilku komputerach?**
A: Tak, baza SQLite jest przenośna. Jeden Pi = serwer, reszta przez przeglądarkę.

**Q: Czy dane są bezpieczne?**
A: Tak, wszystko lokalnie na Pi. Baza w `akces_hub.db`.

**Q: Ile kosztuje Allegro API?**
A: Bezpłatne.

**Q: Jak sprawdzić logi błędów?**
A: W terminalu gdzie uruchomiony `python app.py` — wszystkie print/logi są widoczne.

**Q: Token Allegro wygasł — co zrobić?**
A: Ustawienia → Allegro API → Odśwież token. Jeśli nie pomaga → Autoryzuj ponownie.

**Q: Winning Products nie znajduje nic — dlaczego?**
A: Najczęściej wygasły token Allegro lub zbyt wysoki próg. Ustaw suwak na 0.00 i odśwież token.

---

## 📞 POMOC TECHNICZNA

**Dostępność:** Pn–Pt 9:00–18:00
**Email:** do 24h | **Telefon:** od razu (w godzinach pracy)

---

*AKCES HUB ENTERPRISE — oszczędza czas, automatyzuje workflow, zwiększa marże.*
