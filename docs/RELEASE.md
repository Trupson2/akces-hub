# AKCES Hub — Release i wydanie klientowi

Skrócona, praktyczna instrukcja dla **operatora**: jak zbudować
i zweryfikować paczkę, oraz dla **klienta**: jak ją uruchomić.
Wszystkie kroki są wykonalne copy/paste.

> Konfigurację serwera (systemd, klucz Fernet, 2FA, role) opisuje
> `docs/DEPLOYMENT.md`. Tutaj jest tylko pętla release: build → verify
> → ship → post-install → sanity.

---

## 1. Operator — przed budową paczki

> **UWAGA**: te 3 sanity to **maszyna operatora** (gdzie masz repo
> źródłowe), NIE serwer klienta/Pi. Uruchom **w katalogu repo**
> (gdzie istnieje `app.py` + `modules/` + `tests/` + `.git/`).
> Dla weryfikacji **po wdrożeniu** na serwerze klienta → sekcja 5
> (`curl`/`journalctl`/`sqlite3`, działają bez kodu źródłowego).

```bash
cd /sciezka/do/repo/akces-hub   # MUSI być repo z .git/, NIE ~ ani /opt

# (a) Testy regresji muszą być zielone (PHASE 1+2+3+4):
python -m pytest tests/test_no_secrets_in_logs.py tests/test_phase2_ops.py \
                 tests/test_phase3_audit.py tests/test_phase3_syntax.py \
                 tests/test_phase4_cleanup.py tests/test_phase4_release.py \
                 tests/test_rendered_js_syntax.py -q

# (b) Żaden plik trackowany nie zawiera tokenu JWT:
#     UWAGA: jeśli uruchomisz poza repo git, git grep zwróci błąd
#     i gałąź "|| OK" wyda FAŁSZYWE "OK". Sprawdź że jesteś w repo:
git rev-parse --is-inside-work-tree && \
  git grep -nE 'eyJ[A-Za-z0-9_-]{20,}' -- . ':(exclude)tests/*'   # MUSI być puste

# (c) Cały kod się kompiluje:
python -m compileall -q modules/ app.py                          # zero błędów
```

Dowolny z tych kroków FAIL → **nie pakować**, najpierw napraw.

---

## 2. Operator — budowa paczki

**Dwie metody, obie bezpieczne. Wybierz JEDNĄ:**

### A) `git archive` (rekomendowane — bez historii git, automatycznie respektuje `.gitignore`)

```bash
git archive --format=zip --prefix=akces-hub/ -o ~/akces-hub-release.zip HEAD
```

Plusy: brak historii (żaden historyczny `nohup.out` nie trafia do klienta);
automatycznie pomija wszystko z `.gitignore` (sekrety, `*.db*`, `backups/`,
`cloud_exports/`, `tools/generate_license.py`).

### B) `tools/build_release.py` (filesystem ZIP z hardenowaną listą EXCLUDE + auto-weryfikacją)

```bash
python tools/build_release.py --output ~/akces-hub-release.zip
```

Buduje ZIP **i automatycznie weryfikuje** — skanuje gotową paczkę po
zakazanych nazwach plików, rozszerzeniach i sygnaturach tokenów (`eyJ…`).
Kończy `[OK]` (exit 0) lub `[FAIL]` (exit 1) — operator NIE wyśle paczki
która nie przeszła.

---

## 3. Operator — sanity paczki (manualnie, kontrolnie)

```bash
ZIP=~/akces-hub-release.zip

# (1) Brak sekretów po nazwie:
unzip -l "$ZIP" | grep -iE '\.secret_key|\.license_secret|\.env(\.|$)|nohup|\.db(-wal|-shm)?$'
#  → MUSI być puste

# (2) Brak tokenów w treści:
unzip -p "$ZIP" 'akces-hub/*.py' 'akces-hub/*.md' 'akces-hub/*.txt' 2>/dev/null \
  | grep -cE 'eyJ[A-Za-z0-9_-]{20,}'
#  → MUSI być 0

# (3) Brak generatora licencji:
unzip -l "$ZIP" | grep -E 'tools/(generate_license|build_release)\.py'
#  → MUSI być puste

# (4) Rozmiar sanity (typowo ~10-15 MB):
ls -lh "$ZIP"
```

---

## 4. Klient — instalacja na własnym serwerze

Pełna instrukcja: **`docs/DEPLOYMENT.md`** sekcje 1-7.

Skrót pierwszego uruchomienia:

```bash
# 1. Rozpakuj paczkę
unzip akces-hub-release.zip -d /opt/
sudo chown -R akces:akces /opt/akces-hub

# 2. Wygeneruj WŁASNE sekrety (nie kopiuj z innego serwera!)
cd /opt/akces-hub
python3 -c "import secrets; open('.secret_key','w').write(secrets.token_hex(32))"
python3 -c "import secrets; open('.license_secret','w').write(secrets.token_hex(32))"
sudo mkdir -p /etc/akces
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
  | sudo tee /etc/akces/env.key
sudo chmod 640 /etc/akces/env.key && sudo chown root:akces /etc/akces/env.key

# 3. Dependencies + uruchomienie
pip3 install -r requirements.txt
# Skonfiguruj systemd wg docs/DEPLOYMENT.md sekcja 3
sudo systemctl enable --now akces-hub.service

# 4. Pierwszy admin przez przeglądarkę:
#    https://twoja-domena/   → /setup → utwórz konto admin
#    → włącz 2FA (rekomendowane, docs/DEPLOYMENT.md sekcja 6)
```

Klient **zakłada własną aplikację Allegro/OLX** (własny `client_id`/secret)
i autoryzuje ją na `/allegro/auth` (oraz `/olx/auth` jeśli używa OLX).

---

## 5. Klient/Operator — sanity po wdrożeniu (5 komend)

```bash
# 1. Serwis żyje i healthcheck OK:
curl -fsS https://twoja-domena/api/health | jq .
#  → status:"ok", db_status:"ok"  (HTTP 503 = DB padła)

# 2. Klucz Fernet ładowany z ENV (nie z pliku):
sudo journalctl -u akces-hub --since "5 min ago" | grep "Klucz szyfrowania"
#  → "zaladowany ze zrodla: env:AKCES_ENCRYPTION_KEY"

# 3. Brak tokenów w logach (PHASE 1 regresja):
sudo journalctl -u akces-hub --since "15 min ago" | grep -cE 'eyJ[A-Za-z0-9_-]{20,}'
#  → MUSI być 0

# 4. Backup daemon żyje:
sudo journalctl -u akces-hub | grep -i "backup.*daemon" | tail -3
#  → "Auto: RODO+license..." + backup co godzinę

# 5. Audyt loginu pracuje (zaloguj się raz, potem):
sqlite3 /opt/akces-hub/akces_hub.db \
  "SELECT action, username, timestamp FROM admin_audit_log ORDER BY id DESC LIMIT 3"
#  → musi być widoczny login_success / 2fa_verify_success
```

---

## 6. Po incydencie (jeśli token Allegro mógł wyciec)

Procedura rotacji + checklist — patrz `docs/PHASE1_INCIDENT_REPORT.md`
sekcje "Artefakty operacyjne PRZED wydaniem" i "Checklista RELEASE".

Krótko: zaloguj się jako admin → `/allegro/auth` → "Akceptuj" (30 s).
Dla OLX/Vinted analogicznie jeśli używane.

---

## 7. Co MUSI / NIE MOŻE być w paczce

**Musi (wszystko z paczki budowanej w sekcji 2):**

- `app.py`, `modules/`, `templates/`, `static/`, `requirements.txt`
- `docs/DEPLOYMENT.md`, `docs/RELEASE.md`
- `setup_client.sh`, `Dockerfile` (jeśli używa kontenera)

**Nie może (sprawdzane automatycznie w `build_release.py` + sekcja 3):**

- `.secret_key`, `.license_secret`, `.env`, `.env.key`, `.env.extra`
- `akces_hub.db` + `*.db-wal` + `*.db-shm`
- `nohup.out`, `*.out`, `*.log`
- `backups/`, `cloud_exports/`, `logs/` (treść)
- `tools/` (generator licencji)
- `.git/` (historia — przy `git archive` z definicji nie wchodzi)
- jakikolwiek string z sygnaturą `eyJ[20+].[10+]` (token JWT)
