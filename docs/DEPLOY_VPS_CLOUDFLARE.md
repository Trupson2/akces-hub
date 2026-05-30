# Akces Hub — instancja na VPS + Cloudflare Tunnel

Wdrożenie **osobnej instancji** Akces Hub na Twoim VPS, wystawionej pod
subdomenę w Twojej domenie Cloudflare (przykład w tym dokumencie:
**`maciek.akceshub.com`**).

> **Najważniejsze:** aplikacja **NIE musi działać na komputerze klienta**.
> Chodzi 24/7 na Twoim VPS, a klient (Maciek) **tylko otwiera
> `https://maciek.akceshub.com` w przeglądarce**. Cloudflare Tunnel jest
> tylko „bramą" — łączy się wychodząco z VPS do Cloudflare, więc na VPS
> **nie otwierasz żadnego portu** w internecie.

```
  Maciek (przeglądarka)
        │  https://maciek.akceshub.com
        ▼
  Cloudflare (HTTPS, WAF, cache)
        │  tunel wychodzący (cloudflared)
        ▼
  Twój VPS:  cloudflared ──► 127.0.0.1:5000 (waitress + Flask)
                                   │
                                   ▼
                             akces_hub.db (SQLite, na dysku VPS)
```

Dlaczego ten wariant (a nie komp Macka):
- działa, gdy Maciek ma wyłączony komputer,
- Gemini / Allegro lecą z łącza VPS (stabilne), nie z jego biura,
- to Ty robisz update i backup,
- znika błąd „Utracono połączenie" przy wystawianiu — Cloudflare flushuje
  strumień SSE (dokładnie tak jak u Adriana).

---

## 0. Czego potrzebujesz przed startem

| Rzecz | Skąd |
|---|---|
| VPS z Ubuntu 22.04+/Debian 12 (1 vCPU, 2 GB RAM wystarczy) | Hetzner / Mikrus / OVH / Oracle Free |
| Domena `akceshub.com` dodana do Cloudflare | masz (DNS zarządza Cloudflare) |
| Token tunelu Cloudflare | utworzysz w kroku 1 |
| (opcjonalnie) GitHub token do `git pull` | jeśli repo prywatne |
| (opcjonalnie) klucz Gemini | https://aistudio.google.com/apikey |

---

## 1. Cloudflare — utwórz tunel (robisz to TY w panelu, ~3 min)

To jedyna część, której nie da się zrobić z poziomu VPS-a — wymaga Twojego
konta Cloudflare.

1. Wejdź na **https://one.dash.cloudflare.com** → **Networks → Tunnels**
   (Zero Trust; darmowy plan wystarczy).
2. **Create a tunnel** → typ **Cloudflared** → nazwa np. `akces-maciek`.
3. Na ekranie instalacji Cloudflare pokaże komendę z **tokenem**
   (`eyJhIjoi...` — długi ciąg). **Skopiuj sam token** — wkleisz go do
   instalatora VPS w kroku 2. Nie musisz uruchamiać komendy z ekranu ręcznie.
4. Zakładka **Public Hostnames → Add a public hostname**:
   - **Subdomain:** `maciek`
   - **Domain:** `akceshub.com`
   - **Service Type:** `HTTP`
   - **URL:** `localhost:5000` (albo `localhost:<port>` jeśli używasz `--port`
     — np. na zajętej maszynie, gdzie 5000 jest wzięte, dajesz `localhost:5001`)
   - Zapisz.

> **Zajęta maszyna (np. Pi z już działającym Akces Hub na 5000)?** Użyj
> `--port 5001` (lub innego wolnego) w instalatorze. Skrypt tworzy **własną,
> osobno nazwaną** usługę tunelu `akces-<klient>-tunnel.service` i **nie rusza**
> istniejącego `cloudflared`/`ngrok`. Wtedy w Public Hostname dasz `localhost:5001`.

To wszystko po stronie Cloudflare. Rekord DNS `maciek.akceshub.com` powstaje
automatycznie i wskazuje na tunel.

---

## 2. Maszyna (VPS lub własny serwer/Pi) — jedna komenda instalacyjna

Zaloguj się jako użytkownik z `sudo` i uruchom instalator z repo.
Podmień `<TOKEN_TUNELU>` na token z kroku 1.

**Repo publiczne:**
```bash
curl -fsSL https://raw.githubusercontent.com/Trupson2/akces-hub/main/deploy/setup_vps_cloudflare.sh -o setup.sh
sudo bash setup.sh \
  --client maciek \
  --domain maciek.akceshub.com \
  --tunnel-token "<TOKEN_TUNELU>"
```

**Repo prywatne** — potrzebny token GitHub (PAT, uprawnienie *Contents: Read*).
Skrypt leży na branchu `claude/access-hub-status-2DOEu`, a aplikacja klonuje
się z `main`:
```bash
GH="<GITHUB_TOKEN>"
curl -fsSL -H "Authorization: token $GH" \
  "https://raw.githubusercontent.com/Trupson2/akces-hub/claude/access-hub-status-2DOEu/deploy/setup_vps_cloudflare.sh" -o setup.sh
sudo bash setup.sh \
  --client maciek \
  --domain maciek.akceshub.com \
  --tunnel-token "<TOKEN_TUNELU>" \
  --gh-token "$GH"
```

Opcjonalne flagi:
- `--gh-token <TOKEN>` — gdy repo prywatne (klonowanie + późniejszy `git pull`)
- `--gemini-key <KLUCZ>` — wpisze klucz Gemini do env (można też później w UI)

Instalator:
1. instaluje system + Python + zależności,
2. tworzy użytkownika `akces-maciek` i katalog `/opt/akces-maciek`,
3. klonuje repo, robi venv, `pip install -r requirements.txt`,
4. tworzy usługę **systemd** `akces-maciek.service` (waitress, `FLASK_ENV=production`
   → ProxyFix + prawdziwe IP klientów za tunelem),
5. instaluje `cloudflared` i rejestruje tunel jako usługę (z Twoim tokenem),
6. ustawia firewall UFW (port 5000 **nie** jest wystawiony do internetu —
   cloudflared sięga go po `localhost`).

Po zakończeniu otwórz **https://maciek.akceshub.com** — powinien pojawić się
wizard `/setup`.

---

## 3. Pierwsza konfiguracja (Ty albo Maciek)

1. `https://maciek.akceshub.com/setup` → załóż konto admina (min. 8 znaków).
2. `/onboarding` → klucz licencji + Allegro (Client ID/Secret z
   https://apps.developer.allegro.pl/), cennik wysyłki, miasto, kod pocztowy.
3. **Bezpieczeństwo (WAŻNE skoro to publiczny URL):** Ustawienia →
   Bezpieczeństwo → **włącz 2FA (TOTP)** dla admina.
4. (Opcjonalnie) Cloudflare Turnstile na logowaniu — patrz `docs/DEPLOYMENT.md` §5.

---

## 4. Codzienna obsługa

```bash
# status aplikacji i tunelu
sudo systemctl status akces-maciek
sudo systemctl status cloudflared

# logi na żywo
sudo journalctl -u akces-maciek -f
sudo journalctl -u cloudflared -f

# restart po zmianach
sudo systemctl restart akces-maciek
```

**Update** działa tak jak zwykle — banner w UI (git install: `git fetch +
reset --hard`), albo ręcznie:
```bash
sudo -u akces-maciek -H bash -c 'cd /opt/akces-maciek && git fetch origin && git reset --hard origin/main'
sudo systemctl restart akces-maciek
```

**Backup** — aplikacja robi automatyczny backup DB co godzinę do
`/opt/akces-maciek/backups/`. Dodatkowo warto włączyć w UI mirror off-site
(Magazyn → Backup → folder OneDrive/Dropbox) albo cron `rsync` na zewnątrz.

---

## 5. Kolejni klienci na tym samym VPS

Instalator jest wielodostępny — każdy klient to osobny user + katalog +
usługa + tunel. Dla następnego klienta:

```bash
sudo bash setup_vps_cloudflare.sh --client adrian --domain adrian.akceshub.com --tunnel-token "<TOKEN_ADRIANA>"
```

Każda instancja ma własną bazę `akces_hub.db`, własne sekrety i własną
aplikację Allegro — pełna izolacja.

---

## 6. Alternatywa: tunel zarządzany lokalnie (config.yml)

Jeśli wolisz tunel zarządzany plikiem (zamiast tokenem z panelu), użyj
`deploy/cloudflared-config.yml.example` + `cloudflared tunnel login`. Token
z panelu (krok 1) jest jednak prostszy i zalecany — całe mapowanie hostname
trzymasz w dashboardzie Cloudflare.

---

## 7. Troubleshooting

| Problem | Co sprawdzić |
|---|---|
| `maciek.akceshub.com` → 502/error 1033 | `systemctl status akces-maciek` (apka padła?) i czy Public Hostname URL = `localhost:5000` |
| Tunel „DOWN" w panelu | `systemctl status cloudflared`, `journalctl -u cloudflared -f`; token poprawny? |
| Logowanie odrzuca z „external" | upewnij się że usługa ma `Environment=FLASK_ENV=production` (ProxyFix → realne IP) |
| Allegro 401 | token wygasł → `/allegro/config` → Połącz ponownie |
| Wolny start / OOM | VPS < 2 GB RAM + pandas/Pillow; dołóż swap lub RAM |
