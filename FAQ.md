# ❓ FAQ - Najczęściej Zadawane Pytania

---

## 🔧 INSTALACJA & SETUP:

**Q: Jakie są wymagania systemowe?**  
A: Windows 10/11 lub Ubuntu 22.04+, Python 3.11+, 4GB RAM, 1GB dysku.

**Q: Czy działa na Mac?**  
A: Tak, wymaga Python 3.11+. Instalacja identyczna jak na Linux.

**Q: Czy potrzebuję internetu?**  
A: Tak - do scrapingu Amazon i Allegro API. Praca offline ograniczona.

**Q: Ile zajmuje instalacja?**  
A: 15-30 minut (zależnie od prędkości internetu).

**Q: Czy mogę zainstalować na serwerze?**  
A: Tak, działa na dowolnym serwerze Linux/Windows. Port 5000 domyślnie.

---

## 💰 LICENCJA & RESELLING:

**Q: Czy mogę sprzedawać unlimited klientom?**  
A: Tak, brak limitu.

**Q: Czy mogę modyfikować kod?**  
A: Tak, pełne prawa.

**Q: Czy mogę zmienić branding (white-label)?**  
A: Tak, dozwolone.

**Q: Czy mogę odsprzedać kod innym resellerom?**  
A: NIE. Licencja dla klientów końcowych, nie dla innych resellerów.

**Q: Jakie prowizje płacę od sprzedaży?**  
A: ZERO. Kupujesz raz, sprzedajesz bez limitów.

---

## 🛠️ TECHNICZNE:

**Q: Jaka baza danych?**  
A: SQLite - plik lokalny, łatwy backup, zero konfiguracji.

**Q: Czy mogę zmienić na MySQL/PostgreSQL?**  
A: Tak, ale wymaga modyfikacji kodu. SQLite wystarcza dla 95% użytkowników.

**Q: Czy dane są bezpieczne?**  
A: Tak, wszystko lokalnie. Backup przez zwykłe kopiowanie pliku .db.

**Q: Co jeśli zawiesi się system?**  
A: Restart (Ctrl+C, potem RUN.bat). Dane zapisane, nic się nie straci.

**Q: Czy mogę używać na kilku komputerach?**  
A: Tak, skopiuj folder. Baza przenośna.

---

## 📦 FUNKCJE:

**Q: Jakie formaty Excel wspiera import?**  
A: .xlsx, .xls, .csv. Rozpoznaje kolumny automatycznie (AI-powered).

**Q: Skąd scraping pobiera dane?**  
A: Amazon.com, Amazon.co.uk, Amazon.de. Tytuły, zdjęcia, parametry.

**Q: Czy mogę dodawać produkty ręcznie?**  
A: Tak, w Magazynierze.

**Q: Jakie drukarki etykiet działają?**  
A: Niimbot B1 (Bluetooth), Vretti 420B (USB). Inne termiczne też mogą działać.

**Q: Czy mogę dostosować layout etykiet?**  
A: Tak, edytuj templates/label.html.

**Q: Czy Allegro API jest płatne?**  
A: NIE. Allegro API jest całkowicie darmowe.

---

## 💵 SPRZEDAŻ (dla resellera):

**Q: Jakie ceny polecacie?**  
A: Setup 1500-2500 PLN, Abonament 400-600 PLN/msc, SaaS 500-800 PLN/msc.

**Q: Jak szybko znajdę pierwszego klienta?**  
A: 1-4 tygodnie (FB Groups, LinkedIn, targi, znajomi).

**Q: Jak długo trwa instalacja u klienta?**  
A: 2-4h (zdalna) lub 1 dzień (na miejscu z szkoleniem).

**Q: Co jeśli klient ma problem techniczny?**  
A: Sprawdź MANUAL.md, Google, ChatGPT. Ostateczność: kontakt ze mną (jeśli masz wsparcie).

**Q: Czy muszę umieć programować?**  
A: NIE. Wystarczy instalacja Python i uruchamianie skryptów (copy-paste).

---

## 📊 BUSINESS:

**Q: Ile realistycznie zarobię?**  
A: 3 klientów setup = 6000 PLN. 10 klientów abonament = 5000 PLN/msc. Rok = 60k PLN+.

**Q: Czy to full-time biznes?**  
A: Może być! 20+ klientów = 10-15k PLN/msc (wystarczy na życie).

**Q: Czy konkurencja jest duża?**  
A: Średnia. BaseLinker dominuje, ale drogi i skomplikowany. To jest nisza (palety zwrotów).

**Q: Jak przekonać klienta?**  
A: Demo na żywo (15 min), pokaz ROI (oszczędność 40h/msc), referencje (system działa produkcyjnie).

---

## 🚨 PROBLEMY:

**Q: Import Excel nie działa?**  
A: Sprawdź: format .xlsx, kolumny z nazwą/ASIN/ceną, dane w pierwszym arkuszu.

**Q: Scraping Amazon nie znajduje produktów?**  
A: Sprawdź ASIN (10 znaków), VPN może blokować, produkty usunięte z Amazon.

**Q: Drukarka etykiet nie łączy się?**  
A: Niimbot B1: sparuj Bluetooth (Windows Settings), adapter BT 5.0 zalecany. Vretti: sprawdź USB.

**Q: Allegro OAuth error?**  
A: Sprawdź Client ID/Secret, Redirect URL: http://localhost:5000/allegro/callback

**Q: System wolno działa?**  
A: Sprawdź RAM (min 4GB), zamknij inne programy, wyłącz antivirus (może skanować bazę).

---

## 🔄 AKTUALIZACJE:

**Q: Czy będą aktualizacje?**  
A: Zależy od Twojej opcji zakupu (A: 12 msc, B: lifetime, C: brak).

**Q: Jak zaktualizować system?**  
A: Backup bazy (.db), zastąp pliki, uruchom. Baza kompatybilna wstecz.

**Q: Co z nowymi funkcjami?**  
A: Feature requests rozważane (priorytet: klienci z abonamentem B).

---

## 📞 WSPARCIE:

**Q: Gdzie szukać pomocy?**  
A: 1) MANUAL.md, 2) FAQ.md (ten plik), 3) Google error, 4) ChatGPT/Claude, 5) Kontakt (jeśli masz abonament).

**Q: Jaki czas reakcji?**  
A: Email: do 24h (dni robocze). Telefon: w godzinach pracy (jeśli masz abonament).

**Q: Czy jest forum/Discord?**  
A: Nie w tej wersji. Kontakt bezpośredni (email/tel).

---

## 📈 SKALOWANIE:

**Q: Czy system radzi sobie z 1000+ paletami?**  
A: Tak. SQLite wydajny do ~100k rekordów. Testowane na dużych bazach.

**Q: Czy mogę mieć wielu użytkowników?**  
A: Jednoczesny dostęp OK, ale brak systemu uprawnień (wszyscy admin).

**Q: Czy mogę hostować dla wielu klientów?**  
A: Tak (SaaS model), wymaga osobnych instancji per klient.

---

**Nie znalazłeś odpowiedzi?**

Skontaktuj się:
- Email: [KONTAKT]
- Telefon: [TELEFON]
- Dostępność: Pn-Pt 9-18

---

*FAQ aktualizowane na bieżąco*
