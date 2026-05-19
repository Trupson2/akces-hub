# AKCES Hub — Deployment na Raspberry Pi (produkcja)

Dokument opisuje jak wdrozyc AKCES Hub na dedykowanym Raspberry Pi pod systemd
z bezpieczna konfiguracja kluczy. Zakres:

- [1. Uzytkownik aplikacji i katalogi](#1-uzytkownik-aplikacji-i-katalogi)
- [2. Klucz szyfrowania (env.key)](#2-klucz-szyfrowania-envkey)
- [3. Konfiguracja systemd](#3-konfiguracja-systemd)
- [4. Migracja ze starej instalacji (.env.key w folderze app)](#4-migracja-ze-starej-instalacji-envkey-w-folderze-app)
- [5. Cloudflare Turnstile (ochrona loginu)](#5-cloudflare-turnstile-ochrona-loginu)
- [6. 2FA TOTP dla adminow](#6-2fa-totp-dla-adminow)

---

## 1. Uzytkownik aplikacji i katalogi

Aplikacja NIE powinna dzialac jako `root`. Stworz dedykowanego uzytkownika:

```bash
sudo useradd -r -m -s /usr/sbin/nologin akces
sudo mkdir -p /opt/akces-hub
sudo chown akces:akces /opt/akces-hub
```

Uzytkownik `akces` nie moze sie logowac (`/usr/sbin/nologin`), ma tylko home
dla cache'ow (m.in. dla fallback `~/.akces/env.key`).

---

## 2. Klucz szyfrowania (env.key)

Klucz sluzy do szyfrowania tokenow API (Allegro OAuth, Telegram bot, OLX, Vinted)
w tabeli `config`. **Utrata klucza = utrata wszystkich zaszyfrowanych tokenow.**

### Priorytet ladowania (zobacz `modules/key_loader.py`)

1. `AKCES_ENCRYPTION_KEY` — zmienna srodowiskowa (z systemd EnvironmentFile).
   **REKOMENDOWANE dla produkcji** — klucz nie leci z backupem, nie trafia
   przypadkiem do gita.
2. `/etc/akces/env.key` — plik chmod 600, wlasciciel `akces:akces`.
   Alternatywa dla EnvironmentFile.
3. `~/.akces/env.key` — user-space fallback (dev / gdy brak root).
4. `<app_dir>/.env.key` — **DEPRECATED** (legacy). Wyswietla warning i
   automatycznie kopiuje do `~/.akces/env.key`.

### Konfiguracja produkcyjna (rekomendowane: systemd EnvironmentFile)

```bash
# 1. Utwórz katalog /etc/akces z wlasciwymi uprawnieniami
sudo mkdir -p /etc/akces
sudo chown root:akces /etc/akces
sudo chmod 750 /etc/akces

# 2. Jesli juz masz klucz w <app_dir>/.env.key - skopiuj go
sudo cp /opt/akces-hub/.env.key /etc/akces/env.key
# JESLI to pierwsza instalacja - wygeneruj:
sudo -u akces python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" | sudo tee /etc/akces/env.key

# 3. Uprawnienia: tylko root + akces moze czytac
sudo chown root:akces /etc/akces/env.key
sudo chmod 640 /etc/akces/env.key

# 4. Zweryfikuj
sudo -u akces cat /etc/akces/env.key   # powinno dzialac
sudo -u nobody cat /etc/akces/env.key  # powinno byc PERMISSION DENIED
```

**Format pliku dla systemd EnvironmentFile:**

```
# /etc/akces/env.key
AKCES_ENCRYPTION_KEY=XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX=
```

**Format dla bezposredniego odczytu** (gdy NIE uzywamy systemd EnvironmentFile,
a `modules.key_loader` czyta plik wprost) — plik zawiera tylko sam klucz bez
prefixu `AKCES_ENCRYPTION_KEY=`:

```
# /etc/akces/env.key
XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX=
```

**Wybierz JEDEN z dwoch formatow w zaleznosci od podejscia:**
- Uzywasz systemd `EnvironmentFile=` -> uzyj formatu `KEY=VALUE`.
- Uzywasz tylko pliku (bez env var) -> uzyj formatu z samym kluczem.

---

## 3. Konfiguracja systemd

Plik unit: `/etc/systemd/system/akces-hub.service`

```ini
[Unit]
Description=AKCES Hub (paletomat + magazynier + integracje)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=akces
Group=akces
WorkingDirectory=/opt/akces-hub
# Klucz szyfrowania dostarczany jako ENV var (nie w folderze aplikacji)
EnvironmentFile=/etc/akces/env.key
# Opcjonalne: dodatkowe env vars (FLASK_ENV, CORS_ORIGINS itd.)
# EnvironmentFile=/etc/akces/env.extra
ExecStart=/opt/akces-hub/.venv/bin/python app.py
Restart=on-failure
RestartSec=5

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/akces-hub /etc/akces
# Dla katalogu backupow dopisz kolejny ReadWritePaths

[Install]
WantedBy=multi-user.target
```

**Uruchomienie:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now akces-hub
sudo systemctl status akces-hub
sudo journalctl -u akces-hub -f   # tail logow
```

Przy starcie w logach powinno byc:

```
[OK] Klucz szyfrowania zaladowany ze zrodla: env:AKCES_ENCRYPTION_KEY
```

---

## 4. Migracja ze starej instalacji (`.env.key` w folderze app)

Jesli masz juz dziala instalacje z kluczem w `/opt/akces-hub/.env.key`:

```bash
# 1. Zatrzymaj serwis
sudo systemctl stop akces-hub

# 2. Skopiuj klucz (nie przenos - zostaw kopie zapasowa!)
sudo mkdir -p /etc/akces
sudo cp /opt/akces-hub/.env.key /etc/akces/env.key
sudo chown root:akces /etc/akces/env.key
sudo chmod 640 /etc/akces/env.key

# 3. (Opcjonalnie) - jesli uzywasz systemd EnvironmentFile, przekonwertuj
#    plik na format KEY=VALUE
sudo sh -c 'echo "AKCES_ENCRYPTION_KEY=$(cat /etc/akces/env.key)" > /etc/akces/env.key.tmp && mv /etc/akces/env.key.tmp /etc/akces/env.key'
sudo chmod 640 /etc/akces/env.key

# 4. Zaktualizuj unit file systemd z EnvironmentFile=/etc/akces/env.key
sudo systemctl daemon-reload

# 5. Restartuj serwis
sudo systemctl start akces-hub

# 6. Sprawdz ze dziala (powinno ladowac klucz z ENV, nie z pliku)
sudo journalctl -u akces-hub -n 50 | grep "Klucz szyfrowania"
# Powinno pokazac: "Klucz szyfrowania zaladowany ze zrodla: env:AKCES_ENCRYPTION_KEY"

# 7. Dopiero PO weryfikacji - usun stary plik z folderu aplikacji
sudo rm /opt/akces-hub/.env.key
```

**UWAGA:** nie usuwaj `/opt/akces-hub/.env.key` zanim nowe zrodlo nie dziala.
Bez klucza stracilbys dostep do wszystkich zaszyfrowanych tokenow API.

---

## 5. Cloudflare Turnstile (ochrona loginu)

Turnstile to darmowy CAPTCHA-less challenge od Cloudflare (alternatywa reCAPTCHA,
RODO-friendly, zero tracking).

### Rejestracja

1. Zaloguj sie do <https://dash.cloudflare.com> (konto darmowe wystarczy).
2. `Turnstile` -> `Add site`:
   - Widget mode: **Managed** (rekomendowane - Cloudflare sam decyduje kiedy pokazac challenge).
   - Domains: podaj domeny ktore beda sluzyc panel (np. `akces.twojadomena.pl`).
3. Skopiuj `Site key` (public) i `Secret key` (server-side).

### Konfiguracja w AKCES Hub

W systemd EnvironmentFile (`/etc/akces/env.key` lub osobny `/etc/akces/env.extra`):

```
TURNSTILE_SITE_KEY=0x4AAAAAAAsomethingpublic
TURNSTILE_SECRET_KEY=0x4AAAAAAAsomethingsecret
```

Po restarcie serwisu na `/auth/login` pojawi sie widget Turnstile. Logowanie
bez wypelnienia challengea zostanie odrzucone z HTTP 403 i zalogowane w
`admin_audit_log` jako `login_turnstile_fail`.

**Backward-compat:** jesli `TURNSTILE_SITE_KEY` lub `TURNSTILE_SECRET_KEY` jest
puste, feature jest wylaczony (login dziala jak wczesniej).

---

## 6. 2FA TOTP dla adminow

2FA jest **opt-in** — kazdy user moze wlaczyc w `Ustawienia -> Bezpieczenstwo`.

### Jak uzytkownik wlacza 2FA

1. `Ustawienia -> Bezpieczenstwo -> Wlacz 2FA`
2. Zeskanuj QR kod aplikacja Google Authenticator / Authy / 1Password
3. Wpisz 6-cyfrowy kod do potwierdzenia
4. Zapisz 8 backup codes (jednokrotnego uzytku, do odzyskania w razie utraty telefonu)

### Jak wylaczyc 2FA

`Ustawienia -> Bezpieczenstwo -> Wylacz 2FA` — wymaga aktualnego kodu TOTP
(zeby ktos kto ukradl sesje nie mogl wylaczyc 2FA).

### Recovery (utrata telefonu)

Uzyj jednego z backup codes w formularzu `/auth/2fa/verify` — kazdy kod dziala
raz i od razu znika z puli. Po ich wyczerpaniu:

- Administrator serwera moze zrobic reset przez CLI:
  ```bash
  sudo -u akces sqlite3 /opt/akces-hub/akces_hub.db \
      "UPDATE users SET totp_enabled=0, totp_secret=NULL, totp_backup_codes=NULL WHERE username='NAZWA';"
  ```

---

## 7. Model rol — pierwszy klient (WAZNE)

**Decyzja bezpieczenstwa (PHASE 1.5, 2026-05):**

- **Pierwszy klient = 1 administrator.** Managed install: konto zalozone
  przez `/setup` ma role `admin` i zarzadza systemem samodzielnie.
- **Zabezpieczenie twarde (zweryfikowane):** zmiana/nadanie roli
  (`POST /users/role/<id>`, `auth.py:1383`) jest `@require_role('admin')`.
  **Self-eskalacja niemozliwa** — zaden non-admin nie awansuje sam ani
  nikogo. To fundament tej decyzji (chroniony testem regresyjnym).
- **Ograniczenie znane:** rola `magazynier` jest egzekwowana przez
  *prefix-allowlist* (`ROLE_ALLOWED_PATHS`, `auth.py:189`), nie przez
  dekoratory per-route. Destrukcyjne POST-y magazynu/palet
  (`/magazyn/produkt/<code>/usun`, `/magazyn/paleta/<n>/usun`,
  `/magazyn/api/palety-usun`) NIE maja wlasnego `@require_role` — konto
  `magazynier` moze je wywolac.
- **Zasada na pierwszego klienta:** **NIE nadawac roli `magazynier`**
  dopoki nie wdrozone twarde dekoratory per-route (roadmapa PHASE 3 —
  "po pierwszym kliencie"). Dla 1-admin instalacji ryzyko nie wystepuje
  (nikt nie ma tej roli). Gdy klient bedzie chcial konto pracownika
  magazynu — najpierw PHASE 3.

---

## Checklist wdrozenia produkcyjnego

- [ ] User `akces` utworzony, katalog `/opt/akces-hub` owned przez `akces:akces`
- [ ] **Tylko konto `admin`** — NIE tworzyc kont z rola `magazynier` (patrz sekcja 7)
- [ ] `/etc/akces/env.key` istnieje, chmod 640, owner `root:akces`
- [ ] Systemd unit z `User=akces` i `EnvironmentFile=/etc/akces/env.key`
- [ ] W logach startowych: `Klucz szyfrowania zaladowany ze zrodla: env:AKCES_ENCRYPTION_KEY`
- [ ] Stary `<app_dir>/.env.key` usuniety (po weryfikacji nowego zrodla)
- [ ] `SESSION_COOKIE_SECURE=True` (HTTPS-only cookies) — jesli za Cloudflare
- [ ] `TURNSTILE_SITE_KEY` + `TURNSTILE_SECRET_KEY` skonfigurowane (opcjonalne)
- [ ] Pierwszy admin wlaczyl 2FA (rekomendowane dla kont `admin`)
- [ ] Regularne backupy bazy (NIE zawierajace `/etc/akces/env.key`!)
