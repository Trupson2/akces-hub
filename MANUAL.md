# 📖 AKCES HUB - Instrukcja Użytkownika

## Przewodnik dla użytkownika końcowego

---

## 🚀 URUCHOMIENIE SYSTEMU:

### 1. Otwórz CMD/Terminal
```bash
cd [folder_z_systemem]
```

### 2. Uruchom aplikację
```bash
python app.py
```

### 3. Otwórz przeglądarkę
```
http://localhost:5000
```

**WAŻNE:** Nie zamykaj okna CMD/Terminal - system musi działać w tle!

---

## 📦 MODUŁ: PALETOMAT

### Jak zaimportować paletę?

#### Krok 1: Przygotuj plik Excel
```
Kolumny które system rozumie:
- Product Name / Nazwa
- ASIN (Amazon)
- EAN / Barcode
- Quantity / Ilosc / Qty
- Price / Cena / Cost
```

#### Krok 2: Import
1. Kliknij **"Paletomat"** w menu
2. **"Import Excel"**
3. Wybierz plik
4. Nazwij paletę (np. "Jobalots 03.02")
5. Wybierz dostawcę
6. Podaj cenę zakupu palety
7. Kliknij **"Importuj"**

#### Krok 3: Scraping Amazon
1. System automatycznie znajdzie produkty na Amazon
2. Pobierze: tytuły, zdjęcia, parametry
3. To zajmie 2-5 minut (zależnie od ilości)

#### Krok 4: Generowanie ofert Allegro
1. Kliknij **"Generuj oferty Allegro"**
2. System stworzy wyceny (AI-powered)
3. Możesz edytować ceny przed wystawieniem

---

## 📍 MODUŁ: MAGAZYNIER

### Jak przypisać lokalizację produktowi?

#### Metoda 1: Ręczna
1. Wejdź w **"Magazynier"** → **"Palety"**
2. Kliknij paletę
3. Kliknij produkt
4. **"Edytuj lokalizację"**
5. Wybierz: Regal - Półka - Pozycja
6. Zapisz

#### Metoda 2: 3D (wizualna)
1. **"Magazynier"** → **"3D Visualization"**
2. Kliknij regał → półkę → pozycję
3. Wybierz produkt z listy
4. Kliknij **"Przypisz"**

### Jak wydrukować etykietę QR?

1. Wejdź w produkt (kliknij nazwę)
2. **"Drukuj etykietę"**
3. Wybierz drukarkę (Niimbot / Vretti)
4. Kliknij **"Drukuj"**

**Etykieta zawiera:**
- Nazwa produktu
- Lokalizacja (A1-2-3)
- QR code (link do produktu w systemie)

### Jak skanować QR?

1. **Metoda A:** Smartfon + kamera
   - Zeskanuj QR
   - Otwórz link → przejdzie do produktu

2. **Metoda B:** Skaner bluetooth
   - Sparuj skaner z komputerem
   - Zeskanuj → system otworzy produkt

---

## 📊 MODUŁ: ANALYTICS

### Co pokazuje Dashboard?

**Karty główne:**
- **Palety:** Łączna liczba palet
- **Produkty:** Łączna liczba sztuk
- **Wartość:** Suma wartości magazynu
- **Sprzedane:** Wartość sprzedaży (msc/rok)

**Wykresy:**
- Sprzedaż dzienna/tygodniowa/miesięczna
- Top 10 produktów
- Flopy (produkty >60 dni bez ruchu)

**Statystyki dostawców:**
- ROI per dostawca
- Średnia marża
- Najlepsze palety

### Jak policzyć ROI palety?

System robi to automatycznie:
```
ROI = (Sprzedaż - Koszt zakupu) / Koszt zakupu × 100%

Przykład:
Paleta kosztowała: 1000 PLN
Sprzedaż: 2500 PLN
ROI = (2500 - 1000) / 1000 × 100% = 150%
```

---

## 🛒 MODUŁ: ALLEGRO

### Jak połączyć z Allegro?

#### Krok 1: Rejestracja aplikacji
1. Wejdź na [https://apps.developer.allegro.pl/](https://apps.developer.allegro.pl/)
2. Zaloguj się
3. **"Nowa aplikacja"**
4. Wpisz dane:
   - Nazwa: AkcesHub
   - Redirect URL: `http://localhost:5000/allegro/callback`
5. Zapisz **Client ID** i **Client Secret**

#### Krok 2: Konfiguracja w systemie
1. **"Ustawienia"** → **"Allegro API"**
2. Wklej Client ID i Client Secret
3. Kliknij **"Autoryzuj"**
4. Zaloguj się na Allegro
5. Potwierdź uprawnienia

#### Krok 3: Test
1. Wróć do systemu
2. Status powinien pokazać: ✅ **Połączono**

### Jak wystawić produkty?

1. **"Paletomat"** → Wybierz paletę
2. **"Generuj oferty Allegro"**
3. Sprawdź ceny (możesz edytować)
4. Kliknij **"Wyślij do Allegro"**
5. System utworzy draft oferty
6. Możesz je aktywować w panelu Allegro

---

## 🔧 ROZWIĄZYWANIE PROBLEMÓW:

### System się nie uruchamia?
```
Sprawdź:
1. Czy Python jest zainstalowany? (python --version)
2. Czy zainstalowałeś zależności? (pip install -r requirements.txt)
3. Czy port 5000 jest wolny? (zamknij inne programy)
```

### Import Excel nie działa?
```
Sprawdź:
1. Czy plik ma rozszerzenie .xlsx (nie .xls)?
2. Czy kolumny mają odpowiednie nazwy?
3. Czy dane są w pierwszym arkuszu?
```

### Scraping Amazon nie znajduje produktów?
```
Możliwe przyczyny:
1. Błędny ASIN (sprawdź na Amazon)
2. Produkt usunięty z Amazon
3. Zablokowane połączenie (firewall/VPN)

Rozwiązanie:
- Sprawdź ASIN ręcznie na Amazon.com/Amazon.co.uk
- Dodaj produkt ręcznie jeśli scraping zawiedzie
```

### Drukarka etykiet nie działa?
```
Sprawdź:
1. Czy drukarka jest włączona?
2. Czy jest połączona (USB/Bluetooth)?
3. Czy zainstalowane sterowniki?

Niimbot B1:
- Bluetooth: sparuj w ustawieniach Windows
- Adapter BT 5.0 zalecany

Vretti 420B:
- USB: powinno działać od razu
- Sprawdź port COM w menedżerze urządzeń
```

---

## ❓ FAQ:

**Q: Czy mogę używać na kilku komputerach?**  
A: Tak, skopiuj folder z systemem. Baza SQLite jest przenośna.

**Q: Czy dane są bezpieczne?**  
A: Tak, wszystko jest lokalnie na Twoim komputerze.

**Q: Czy muszę mieć internet?**  
A: Tak - do scrapingu Amazon i Allegro API.

**Q: Ile kosztuje Allegro API?**  
A: GRATIS! Nie ma opłat za korzystanie z Allegro API.

**Q: Czy mogę eksportować dane?**  
A: Tak, baza jest w formacie SQLite - możesz ją otworzyć dowolnym narzędziem.

**Q: Co jeśli znajdę błąd?**  
A: Skontaktuj się z dostawcą systemu (dane kontaktowe w umowie).

---

## 📞 POMOC TECHNICZNA:

**Kontakt z dostawcą:**  
[DANE DOSTAWCY - wpisz przy instalacji]

**Dostępność:**  
Pn-Pt: 9:00-18:00

**Czas reakcji:**  
- Email: do 24h
- Telefon: od razu (w godzinach pracy)

---

## 🎓 SZKOLENIA:

**Podstawowe:** 1h (objęte ceną setupu)  
**Zaawansowane:** 2-4h (opcjonalnie)

**Tematy dodatkowe:**
- Optymalizacja workflow
- Zaawansowane funkcje magazynu
- Integracje z innymi systemami
- Własne raporty i statystyki

---

**Powodzenia!** 🚀

System został zaprojektowany aby oszczędzać Twój czas.  
Jeśli masz pytania - zawsze możesz skontaktować się z dostawcą!
