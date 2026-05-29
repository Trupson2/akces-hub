# Akces Hub - Instalacja Windows (Wariant ZIP)

Instrukcja dla klienta koncowego ktory dostal pakiet ZIP do hostowania
na wlasnym komputerze Windows. Nie wymaga znajomosci serwerow / Linuksa.

> Wariant Pi/VPS (systemd): [`DEPLOYMENT.md`](DEPLOYMENT.md)

---

## 1. Wymagania

| Komponent | Minimum | Zalecane |
|---|---|---|
| System operacyjny | Windows 10 (64-bit) | Windows 11 |
| RAM | 4 GB | 8 GB |
| Wolne miejsce na dysku | 2 GB | 10 GB (zalecane ze wzgledu na lokalne zdjecia + backupy) |
| Polaczenie internet | wymagane (Allegro API, update'y) | swiatlowod / 5G |
| Konto admin Windows | wymagane do pierwszej instalacji | - |

Embedded Python 3.11+ jest w paczce - **nie musisz instalowac Pythona**.

---

## 2. Pierwsze uruchomienie

1. **Rozpakuj** plik `akces-hub-XYZ.zip` do **stalej lokalizacji** (np. `C:\AkcesHub\`).
   - NIE rozpakowuj na pulpit / w Pobrane - tam pliki sa kasowane przez automatyczne czyszczenie.
   - NIE rozpakowuj w `Program Files` - Windows wymaga uprawnien admin na zapis.

2. Wejdz do folderu i kliknij **dwukrotnie `INSTALL.bat`**.
   - Skrypt:
     - Sprawdza embedded Python
     - Instaluje biblioteki (requirements.txt)
     - Tworzy ikone na pulpicie
     - Otwiera przegladarke na `http://127.0.0.1:5000`

3. **Pierwsza konfiguracja**:
   - Otworzy sie wizard `/setup`
   - Wpisz nazwe firmy, wybierz kolor brandingu, opcjonalnie wgraj logo
   - **WAZNE**: zaloz konto admina (login + haslo) - to bedzie Twoj glowny dostep
   - Dalsze ustawienia (Allegro, Telegram) mozesz zrobic pozniej w `/ustawienia`

4. Pierwsze logowanie:
   - Wejdz na `http://127.0.0.1:5000`
   - Zaloguj sie loginem/haslem z poprzedniego kroku

> **Uwaga bezpieczenstwa**: aplikacja sluzbowo nasluchuje TYLKO na `127.0.0.1`
> (localhost). Nikt z LAN-u biurowego nie ma do niej dostepu - to jest
> domysle i zalecane. Jezeli chcesz wystawic na inne komputery / internet,
> ustaw [Cloudflare Tunnel](#7-cloudflare-tunnel-opcjonalnie).

---

## 3. Co dalej - konfiguracja podstawowych modulow

### Allegro (wystawianie ofert)
1. Wejdz na `https://apps.developer.allegro.pl/` - zaloz aplikacje OAuth
2. W Akces Hub: `/allegro/config` -> wpisz Client ID + Client Secret
3. Klik **Polacz z Allegro** -> autoryzuj
4. Wybierz cennik wysylki + miasto + kod pocztowy

### Telegram (powiadomienia)
1. W Telegram: napisz do `@BotFather` -> `/newbot`
2. Skopiuj **bot token**
3. W Akces Hub: `/ustawienia/telegram` -> wpisz token + Twoje chat_id
4. Test: kliknij "Wyslij testowa wiadomosc"

### GPSR (od 13.12.2024 wymagane na Allegro)
1. Allegro Panel -> Konto -> GPSR -> Producenci -> dodaj
2. Allegro Panel -> Konto -> GPSR -> Osoby odpowiedzialne -> dodaj
3. W Akces Hub: `/allegro/config` -> sekcja "GPSR" -> wybierz domyslnych

---

## 4. Codzienna praca - autostart

Aby Akces Hub uruchamial sie z Windowsem:

1. Wcisnij `Win + R` -> wpisz `shell:startup` -> Enter
2. Skopiuj plik `START_AKCES.bat` z folderu instalacyjnego do tej lokalizacji
3. Restart komputera - aplikacja sama wstanie

Aby zatrzymac aplikacje:
- Wcisnij `Ctrl+C` w oknie konsoli, lub
- Zamknij okno konsoli, lub
- W Menedzerze Zadan -> znajdz `pythonw.exe` i zakoncz

---

## 5. Update

Update jest **automatyczny** - nie musisz pobierac nowych ZIPow.

1. Gdy pojawi sie nowa wersja, w UI na gorze pokaze sie banner **"Dostepna aktualizacja"**
2. Klik baner -> aplikacja:
   - Robi backup DB (`backups/updates/`)
   - Robi backup kodu
   - Pobiera nowa wersje z [GitHub](https://github.com/Trupson2/akces-hub)
   - Restart automatyczny
   - Reload strony po 6s
3. Jezeli cos pojdzie nie tak -> automatyczny rollback

**Manualny update** (jezeli baner nie dziala):
- F12 -> Console -> wpisz:
  ```js
  fetch('/system/update-from-public',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(console.log)
  ```

---

## 6. Backup DB

System robi **automatyczne backupy DB co godzine** -> `backups/automatic/`.
Pre-update backup jest osobno -> `backups/updates/`.

Recommended: raz w tygodniu skopiuj caly folder `backups/` na zewnetrzny
nosnik (pendrive, OneDrive, Dropbox).

W razie awarii:
1. Zatrzymaj aplikacje
2. Skopiuj `backups/automatic/akces_hub_YYYY-MM-DD_HHMM.db` -> nadpisz `akces_hub.db`
3. Wystartuj aplikacje

---

## 7. Cloudflare Tunnel (opcjonalnie)

Jezeli chcesz udostepnic aplikacje pod wlasna domena (np. `magazyn.mojafirma.pl`)
bez wystawiania portu, zainstaluj Cloudflare Tunnel:

1. Zaloz konto Cloudflare + dodaj domene
2. Pobierz `cloudflared.exe`
3. `cloudflared tunnel login`
4. `cloudflared tunnel create akces-hub`
5. `cloudflared tunnel route dns akces-hub magazyn.mojafirma.pl`
6. Config (`%USERPROFILE%\.cloudflared\config.yml`):
   ```yaml
   tunnel: <UUID-tunelu>
   credentials-file: C:\Users\TWOJUSER\.cloudflared\<UUID>.json
   ingress:
     - hostname: magazyn.mojafirma.pl
       service: http://127.0.0.1:5000
     - service: http_status:404
   ```
7. `cloudflared service install`
8. Po restartie aplikacja jest pod `https://magazyn.mojafirma.pl`

> **WAZNE bezpieczenstwo**: po wystawieniu na public URL **wlacz 2FA dla
> admina** (Ustawienia -> Bezpieczenstwo -> 2FA TOTP).

---

## 8. Troubleshooting

| Problem | Co sprawdzic |
|---|---|
| "Aplikacja nie dziala" po update | Defender skanuje pythonw.exe - poczekaj 30s, F5 |
| Banner aktualizacji nie pokazuje sie | Background check co 2 min. Hard refresh (Ctrl+Shift+R). |
| Allegro: "401 Unauthorized" | Token wygasl - `/allegro/config` -> Polacz ponownie |
| GPSR errors przy wystawianiu | Brak producenta/osoby w Allegro Panel. Dodaj w GPSR. |
| Wystawiajac dubluje oferty | Zaktualizuj do >= v1.0.87 (anti-duplicate lock) |
| Excel import: ceny = 0 | Zaktualizuj do >= v1.0.91 (rozszerzony parser) |
| Nie widze nowej wersji | Sprawdz `https://github.com/Trupson2/akces-hub/releases` |

W razie pytan: kontakt z dostawca (banner u dolu UI - klik "Wsparcie").

---

## 9. Odinstalowanie

1. Zatrzymaj aplikacje (zamknij okno konsoli)
2. Wykonaj backup `backups/automatic/akces_hub_*.db` -> bezpieczne miejsce
3. Usun folder instalacyjny (`C:\AkcesHub\` lub gdzie wybrales)
4. Usun ikone na pulpicie
5. Usun `START_AKCES.bat` z `shell:startup` (jezeli dodales)

Licencja pozostaje wazna - mozesz odinstalowac i zainstalowac ponownie
w razie potrzeby (np. zmiana komputera).
