# AKCES HUB — System Zarządzania Paletami Zwrotów

**Wersja:** 6.1 | **Platforma:** Web (Flask + SQLite) | **Deployment:** Raspberry Pi / VPS / lokalnie

---

## Dla kogo?

AKCES HUB to kompletny system do zarządzania biznesem opartym na **paletach zwrotów z Amazona** — od zakupu palety, przez skanowanie produktów, wycenę, wystawianie na Allegro, aż po analizę zysków.

Idealny dla:
- Reselerów palet zwrotów (Amazon, Wayfair, inne)
- Jednoosobowych działalności i małych firm (1-5 osób)
- Osób sprzedających na Allegro, OLX, Vinted
- Każdego kto chce zautomatyzować sprzedaż produktów z palet

---

## Co dostajesz?

### 1. MAGAZYN — Pełna kontrola nad towarem
- **Skanowanie produktów** — skaner kodów kreskowych (EAN, ASIN, kody MAG-XXXXX)
- **Karty produktów** — zdjęcia, opisy, ceny, status, historia zmian
- **Zarządzanie paletami** — koszt zakupu, dostawca, ROI per paleta
- **Lokalizacja w magazynie** — regały, półki, wizualizacja 3D
- **Etykiety** — druk etykiet z QR kodem na drukarce termicznej Niimbot B1
- **Import/Export** — CSV, Excel, bulk operacje

### 2. PALETOMAT — Automatyczne wystawianie na Allegro
- **Scraper Amazon** — wklej ASIN, system ściąga zdjęcia, opisy, specyfikacje
- **Import hurtowy** — wklej HTML ze strony dostawcy, system rozpozna produkty
- **Generator ofert** — jednym kliknięciem tworzy gotową ofertę Allegro:
  - Tytuł zoptymalizowany pod SEO
  - Opis HTML (profesjonalny szablon)
  - Do 8 zdjęć (oryginalne + AI-generowane)
  - Automatyczna wycena na podstawie kosztu palety
  - Kategoryzacja + parametry Allegro
- **Masowe wystawianie** — wystaw całą paletę (10-50 produktów) w kilka minut
- **AI zdjęcia** — Gemini generuje lifestyle, wymiary, detale, kontekst użycia
- **Deduplikacja** — system sprawdza czy produkt jest już wystawiony

### 3. ALLEGRO — Pełna integracja
- **OAuth2** — bezpieczne połączenie z kontem Allegro
- **Synchronizacja zamówień** — automatyczne pobieranie sprzedaży
- **Aktualizacja stanów** — sprzedaż na Allegro = odejmowanie ze stanu magazynowego
- **Zarządzanie ofertami** — publikuj, edytuj, usuwaj z poziomu systemu
- **Smart matching** — łączenie zamówień z produktami po EAN, ASIN, nazwie

### 4. ANALITYKA — Wiesz ile zarabiasz
- **Dashboard KPI** — przychody, zyski, ROI, marże w jednym miejscu
- **Analiza palet** — która paleta się opłaciła, a która nie
- **Analiza kategorii** — elektronika vs zabawki vs dom — co sprzedaje się najlepiej
- **Czas sprzedaży** — ile dni produkt leży zanim się sprzeda
- **Statystyki dostawców** — porównanie dostawców per ROI, koszt, wolumen
- **Raport miesięczny** — Excel z pełnym rozliczeniem

### 5. OKAZJE — Skaner palet do kupienia
- **Monitoring dostawców** — automatyczne skanowanie nowych palet u Twoich dostawców
- **Konfigurowalne źródła** — dodaj swoich dostawców, system będzie ich monitorować
- **Perplexity AI** — analiza opłacalności palety przed zakupem
- **Przelicznik walut** — ceny w GBP/EUR automatycznie na PLN (kurs NBP)
- **Scoring ROI** — system ocenia każdą ofertę

### 6. POWIADOMIENIA — Telegram + WhatsApp
- **Nowa sprzedaż** — natychmiastowe powiadomienie na telefon
- **Nowa okazja** — alert gdy pojawi się paleta spełniająca kryteria
- **Raport dzienny** — podsumowanie dnia o 8:00 rano
- **Raport tygodniowy** — poniedziałkowe zestawienie

### 7. AUTOMATYZACJA — System pracuje za Ciebie
- **Nocny Kombajn** — w nocy system sam tworzy drafty ofert na Allegro
- **Auto-backup** — co godzinę kopia bazy na Google Drive (rclone)
- **Token refresh** — tokeny API odświeżają się automatycznie
- **Monitor palet** — co godzinę sprawdza nowe oferty palet

---

## Wymagania techniczne

| Element | Minimum | Zalecane |
|---------|---------|----------|
| Sprzęt | Raspberry Pi 4 (2GB RAM) | RPi 4 (4GB) lub dowolny VPS |
| System | Linux (Raspbian/Ubuntu) | Raspbian Lite 64-bit |
| Python | 3.7+ | 3.11+ |
| Dysk | 2 GB wolnego | 8 GB+ (na zdjęcia) |
| Internet | Wymagany (API) | Stałe łącze |

**Dodatkowe usługi (darmowe/własne):**
- Konto Allegro (do sprzedaży)
- Klucz API Gemini (Google AI Studio — darmowy tier wystarcza)
- Bot Telegram (darmowy via @BotFather)
- ngrok (darmowy — do zdalnego dostępu)

---

## Cennik

| Pakiet | Co zawiera | Cena |
|--------|-----------|------|
| **Starter** | Magazyn + Paletomat + Allegro | Do ustalenia |
| **Pro** | Starter + Analityka + Telegram + Automatyzacja | Do ustalenia |
| **Enterprise** | Pro + OLX + Vinted + dedykowane wsparcie | Do ustalenia |

*Jednorazowa licencja — bez abonamentu. Aktualizacje w cenie przez 12 miesięcy.*

---

## Jak to wygląda?

System działa w przeglądarce — na komputerze, tablecie i telefonie.

- **Dashboard** — przegląd stanu magazynu, ostatnich sprzedaży, KPI
- **Ciemny/jasny motyw** — automatycznie dopasowuje się do systemu
- **Responsywny** — działa na telefonie (skanowanie produktów w magazynie)
- **Polski interfejs** — cały system po polsku

---

## Przewaga nad konkurencją

| Cecha | AKCES HUB | BaseLinker | Ręczne Excele |
|-------|-----------|------------|---------------|
| Scraping Amazon (ASIN) | ✅ | ❌ | ❌ |
| AI opisy + zdjęcia | ✅ | ❌ | ❌ |
| Analiza opłacalności palet | ✅ | ❌ | ❌ |
| Skaner okazji u dostawców | ✅ | ❌ | ❌ |
| Masowe wystawianie z palety | ✅ | Częściowo | ❌ |
| Koszt miesięczny | 0 zł* | 79-299 zł/mies | 0 zł |
| Raspberry Pi | ✅ | ❌ | N/A |

*\* Po jednorazowym zakupie licencji. Koszty API (Gemini, ngrok) zależą od użycia — darmowe limity zazwyczaj wystarczają.*

---

## Kontakt

Zainteresowany? Napisz:
- **Email:** [do uzupełnienia]
- **Telegram:** [do uzupełnienia]

---

*AKCES HUB v6.1 — Zbudowany przez resellera, dla resellerów.*
