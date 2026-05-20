# PHASE 1 — Incydent z logami: RAPORT KOŃCOWY (operacyjny)

Branch: `fix/allegro-token-refresh` · Zakres: `b51bf56^..26a65e8` (8 commitów)
Data: 2026-05 · Status: **ZAMKNIĘTY** (kod) · Pozostają czynności operacyjne

---

## Executive summary

Incydent wycieku danych dostępowych do logów opanowany: **18 sinków**
logujących surowe `response.text`/`resp.text`/`.content` (w tym pełne
tokeny OAuth Allegro, OLX, Vinted) zamienione na status-only. `nohup.out`
z tokenem usunięty z repo, niebezpieczne ścieżki update wyłączone.
**`git grep` JWT na trackowanych plikach = zero trafień** — repo bezpieczne
do wydania przez `git archive`/`git clone --depth 1`.

---

## Naprawione sinki (plik, commit, opis)

### commit `b51bf56` (PHASE 1.1 — pierwotny)
| Plik | Funkcja | Logowało |
|---|---|---|
| allegro_api.py | refresh_access_token() else | response.text token-endpoint |
| allegro_api.py | OAuth callback `[OAuth] Response` | **200 body = pełny access+refresh token** |
| allegro_api.py | OAuth callback err_msg fallback | response.text |
| allegro_api.py | allegro_request() non-JSON | response.text |
| allegro_api.py | shipment-label WARN | response.text (adres/RODO) |
| token_refresh.py | _handle_failure() API | response.text → log + Telegram |

Helper `_safe_resp_err(resp)` → `HTTP <status> (<error_code>)`, nigdy body/error_description.

### commit `1941f7f` (PHASE 1.1+ — szerszy scan, 7 przeoczonych)
| Plik:linia | Logowało |
|---|---|
| olx_api.py:101 | **OLX token-refresh response.text** (token leak, klasa 1.1) |
| telegram_bot.py:58/129/163 | WhatsApp/Telegram/Support response.text |
| title_generator_ai.py:218 | Gemini response.text[:200] |
| utils.py:2213/2802 | Gemini HTML / SEO response.text[:200] |

### commit `26a65e8` (PHASE 1.1++ — wzorzec `resp.text`, 5 przeoczonych)
| Plik:linia | Logowało |
|---|---|
| allegro_api.py (GPSR) | resp.text[:300] |
| vinted_api.py:280 | **Vinted photo upload resp.text** (integracja klienta) |
| magazynier.py:7967 | auto-wycena Gemini resp.text[:200] |
| pallet_monitor.py:1185 | Perplexity API resp.text[:200] |
| winning_scout.py:748 | scout AI resp.text[:100] |

> `winning_scout.py:802 len(resp.text) chars` — zostawione: to DŁUGOŚĆ (liczba), nie treść = bezpieczne.

### Pozostałe PHASE 1
- `964042a` 1.2 — `nohup.out` (token) usunięty (−3097 linii), `.gitignore` `*.out`
- `e3ac817` 1.3 — `/admin/update-git` + `/admin/update` → `abort(404)` (RCE bez CSRF)
- `1b969ee` 1.4 — README zsynchronizowany (martwe INSTALL.bat / fałszywe „demo")
- `32b2ff9`+`3d78895` 1.5 — rola magazynier: decyzja 1-admin + doc + test fundamentu

---

## Testy

```bash
python -m pytest tests/test_no_secrets_in_logs.py tests/test_system_update_access.py tests/test_rendered_js_syntax.py -q
```
| Test | Wynik |
|---|---|
| test_no_secrets_in_logs.py | ✅ 2 passed — skan **całego** modules/*.py, regex `(response\|resp)\.(text\|content)`, wyklucza `len()`/marker |
| test_rendered_js_syntax.py | ✅ passed |
| test_system_update_access.py | ✅ 23 passed / ⚠️ 3 failed = **pre-existing** `sqlite3.OperationalError: no such table: config` (środowiskowe, zweryfikowane na baseline `81d98e0`, NIE regresja). Nowe: update-git→404, update→404, role-deny — zielone |

`py_compile`: ✅ wszystkie zmienione moduły **poza** `winning_scout.py`.

---

## ⚠️ Pre-existing poza zakresem (NIE PHASE 1)

`winning_scout.py:729` — strukturalny SyntaxError (`cdef` + złamane
wcięcie, funkcja `_gemini_search_fallback` wklejona w pętlę `for/if`).
**Potwierdzone w `b51bf56^` — sprzed PHASE 1, NIE regresja.** Scheduler
winning_scout nie wstaje (znane od początku sesji, osobny spawn_task).
Mój security-fix `:748` poprawny i izolowany; plik był niekompilowalny
przed tą zmianą. Naprawa wymaga analizy logiki funkcji — poza „tylko
poprawki bezpieczeństwa". **Rekomendacja: osobne zadanie przed wydaniem.**

---

## Git history — tokeny historyczne (WAŻNE dla OPS/prawnych)

`nohup.out` z tokenem **nadal w poprzednich commitach** (`git rm` nie
czyści historii). Tokeny są **martwe** (Allegro access 12h wygasł,
refresh wielokrotnie zrotowany w sesji). Opcje przed wydaniem:

- **Zalecane (managed install): `git archive` / `git clone --depth 1`**
  — paczka NIE zawiera historii → token historyczny nie trafia do klienta.
  Zero ryzyka, zero pracy. **Wystarczające.**
- Alternatywa (jeśli wydajesz pełne repo z historią): `git filter-repo
  --path nohup.out --invert-paths` lub fresh repo (squash). Ryzykowne
  (przepisanie historii, divergence z Pi) — **nie zalecane teraz**,
  skoro tokeny martwe i archive je pomija.
- OPS/prawne: token był martwy w chwili wykrycia → brak czynnego wektora;
  rotacja (niżej) zamyka temat formalnie.

---

## Artefakty operacyjne PRZED wydaniem

### 1. Rotacja tokenów (operator, 30 s)
```
Zaloguj się jako admin → otwórz:  https://app.akceshub.com/allegro/auth
  → "Akceptuj" na stronie Allegro → "Pomyślnie połączono".
Jeśli klient używa OLX:     https://app.akceshub.com/olx/auth
Jeśli klient używa Vinted:  ponowne logowanie Vinted w panelu integracji.
```
Rotować: **tylko Allegro/OLX/Vinted tokeny** (były logowane).
**NIE** `client_secret`/`SECRET_KEY`/`.env.key`/`.license_secret` —
zweryfikowane: nigdzie nie logowane, nie wyciekły.

### 2. Pakowanie wydania
```bash
git archive --format=tar.gz --prefix=akces-hub/ -o /tmp/akces-hub-release.tar.gz HEAD
# albo:
git clone --depth 1 file://$(pwd) /tmp/akces-hub-fresh
```
**Pliki które NIE mogą wejść** (git archive/clone pomija je automatycznie
— są w `.gitignore`): `.secret_key`, `.license_secret`, `.env.key`,
`akces_hub.db`(+`-wal/-shm`), `nohup.out`, `*.log`, `backups/`,
`cloud_exports/`, `tools/generate_license.py`.
**Nie pakuj kopiowaniem katalogu** — wtedy untracked sekrety/baza wejdą.

### 3. Sekrety po stronie klienta (generować lokalnie, nie kopiować)
```bash
python -c "import secrets;open('.secret_key','w').write(secrets.token_hex(32))"
python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())" | sudo tee /etc/akces/env.key
python -c "import secrets;open('.license_secret','w').write(secrets.token_hex(32))"
# Klucz licencji generujesz TY: python tools/generate_license.py "Klient" pro 12
# Baza akces_hub.db tworzy się automatycznie przy 1. starcie (database.py init_db).
# Klient zakłada WŁASNĄ aplikację Allegro/OLX (własny client_id/secret).
```

### 4. Bash one-liner — wszystkie sanity checks (operator copy/paste)
```bash
cd /sciezka/do/repo && \
echo "[1] JWT w trackowanych:" && (git grep -nE 'eyJ[A-Za-z0-9_-]{20,}' -- . ':(exclude)tests/*' && echo "FAIL" || echo "OK-pusto") && \
echo "[2] response/resp.text w sinkach:" && (grep -rnE '(print|log\w*|_handle_failure|_send_alert)\([^)]*\b(response|resp)\.(text|content)\b' modules/ | grep -vE 'len\(|body ukryty|_safe_resp_err' && echo "FAIL" || echo "OK-pusto") && \
echo "[3] artefakty trackowane:" && (git ls-files | grep -iE '\.(out|log|db|sqlite)$|nohup|^backups/' && echo "FAIL" || echo "OK-pusto") && \
echo "[4] testy:" && python -m pytest tests/test_no_secrets_in_logs.py tests/test_rendered_js_syntax.py -q 2>&1 | tail -1 && \
echo "[5] (na serwerze) tokeny w logach:" && echo "    sudo journalctl -u akces-hub --since '15 min ago' | grep -c 'eyJ[A-Za-z0-9_-]\{20,\}'   # MUSI = 0"
```

---

## Co powiedzieć klientowi (3 zdania, non-tech)

> System przeszedł audyt bezpieczeństwa — poprawiliśmy zapisywanie logów
> tak, by nie trafiały tam żadne dane dostępowe. Twoja instancja dostaje
> świeże, własne klucze i własne połączenie z Allegro/OLX — nic nie jest
> współdzielone. Po instalacji wykonujemy jednorazowe ponowne połączenie
> z Allegro (30 sekund, opisane w instrukcji) i system jest gotowy.

---

## Checklista RELEASE

- [ ] `git grep -nE 'eyJ[A-Za-z0-9_-]{20,}' -- . ':(exclude)tests/*'` → pusto *(zweryfikowane)*
- [ ] `pytest tests/test_no_secrets_in_logs.py tests/test_rendered_js_syntax.py -q` → zielone *(zweryfikowane)*
- [ ] Paczka przez `git archive`/`git clone --depth 1` (NIE kopiowanie katalogu)
- [ ] Weryfikacja paczki: grep JWT pusto + brak `.secret_key`/`.license_secret`/`akces_hub.db`
- [ ] Rotacja: `/allegro/auth` (+`/olx/auth`/Vinted jeśli używane); `journalctl|grep -c eyJ` = 0
- [ ] Sekrety na serwerze klienta wygenerowane lokalnie; klient ma własną aplikację Allegro
- [ ] Klucz licencji wygenerowany (`tools/generate_license.py`), `tools/` NIE w paczce
- [ ] (Decyzja) `winning_scout.py:729` strukturalny SyntaxError — osobne zadanie przed wydaniem (scheduler nie wstaje)

---

## Uwaga: /admin/update* zwracają 404 dla WSZYSTKICH (zamierzone)

PHASE 1.3 **celowo wyłączył** `/admin/update-git` i `/admin/update`
(`abort(404)`) — miały RCE bez CSRF. **404 dla admina też jest poprawne
i zamierzone** (nie bug do „naprawienia na 200"). Jedyna dozwolona
ścieżka aktualizacji = `/system/update` (`require_admin` + CSRF + audit),
pokryta testami `test_system_update_access.py`. Przywrócenie `/admin/*`
z CSRF = roadmapa PHASE 3, nie teraz.
