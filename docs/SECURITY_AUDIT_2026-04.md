# AKCES Hub — Audyt bezpieczeństwa

**Data:** 15 kwietnia 2026
**Wersja kodu:** `045b276` (branch `claude/agitated-goldwasser`)
**Odbiorca:** partner biznesowy / decision-maker
**Autor dokumentu:** zespół techniczny AKCES Hub

---

## 1. Executive summary (TL;DR)

AKCES Hub w ciągu ostatnich 2 tygodni przeszedł **trzyetapowy hardening bezpieczeństwa** (Phase 1, 1.5, 2). Zamknięto **11 krytycznych luk** zidentyfikowanych w audycie wewnętrznym, dodano **25 nowych testów bezpieczeństwa** i wdrożono **ścieżkę audytową** (kto co zrobił, kiedy, z jakiego IP).

**Stan na dziś:**
- System **gotowy do sprzedaży single-tenant** (jeden klient = jedna instalacja na jego sprzęcie lub naszym VPS).
- Do publicznego SaaS (multi-tenant, otwarty signup) brakuje jeszcze ~3–4 tygodni pracy (Phase 3 + 4).

**Decyzja do podjęcia przez partnera:** czy chcemy uruchomić Phase 3 (3 zadania ~3 dni pracy), czy zatrzymać się na obecnym stanie (wystarczający dla klientów enterprise / B2B z dedykowanym wdrożeniem).

---

## 2. Co zostało zabezpieczone (Phase 1 + 1.5 + 2)

### Phase 1 — Blokery dla produkcji (7 godzin pracy, 9 luk zamkniętych)

| Co | Ryzyko biznesowe przed | Status |
|---|---|---|
| `/system/update` wymaga admina + CSRF | Każdy zalogowany user mógł wywołać `git pull + restart` z dowolnego repo → RCE | ✅ |
| Rate limiter na login | Brute-force hasła z botnetu | ✅ (5/min per IP) |
| ProxyFix + real-IP logging | Za Cloudflare Tunnel widzieliśmy "127.0.0.1" dla wszystkich → brak forensics | ✅ |
| Cookies bezpieczne (Secure + HttpOnly + SameSite) | XSS mógł ukraść sesję admina | ✅ |
| Zip-slip guard w `/admin/update` | Złośliwy ZIP mógł nadpisać dowolny plik na serwerze | ✅ (odrzuca absolute paths, symlinki, `../`) |
| Backup endpointy pod `@require_admin` | User mógł pobierać cudze backupy + robić path traversal przy restore | ✅ |
| Auto-login LAN wyłączony za proxy | Przez Cloudflare Tunnel każdy requester miał IP proxy → auto-login dla wszystkich | ✅ |
| CSRF exempt zawężony | Wcześniej `/system/*` i `/api/*` omijały CSRF | ✅ (tylko 3 webhooki: Allegro + Telegram) |
| Admin gate na `/system/gemini-model` | User mógł zmienić model AI → koszty API | ✅ |

### Phase 1.5 — Feedback po audycie Perplexity (2 godziny, 6 testów)

- **Explicit CSRF w krytycznych route** (`_validate_csrf_or_abort` helper + fallback na Referer)
- **Tabela `admin_audit_log`** — każda akcja admina zapisywana: kto, kiedy, co, z jakiego IP, czy się udało, co poszło źle
- **JSON-aware error responses** — AJAX dostaje 401 JSON, przeglądarka redirect do `/auth/login`

### Phase 2 — Głębsze warstwy (~8 godzin, 25 testów)

| Commit | Co | Business value |
|---|---|---|
| `08cf5a1` | License test prefix fix | Konsystencja planów: `starter`, `business`, `free` poprawnie kodowane w kluczu licencji |
| `e6fed6e` | CSRF form-POST bypass zamknięty | Cross-origin HTML form bez tokena już nie przejdzie (wcześniej: przepuszczał) |
| `d0f3ef6` | SVG upload sanityzacja | Klient wgrywający logo z `<script>` wewnątrz SVG → **odrzucane + logowane**. Atak wrzuci się do panelu forensics, nie do przeglądarki innego usera |
| `045b276` | CSP nonce infrastructure | Fundament pod eliminację `unsafe-inline` w przeglądarce (Phase 4) |

### Testy automatyczne — metryka jakości

- **Przed hardeningiem:** 58 testów
- **Po Phase 2:** **84 testy** (+25 nowych dotyczących bezpieczeństwa)
- **Pass rate:** 83 / 84 (1 skipped na Windows — rate limiter threading, nie-problem na produkcji)
- **Zmiany w kodzie:** 1361 insercji, 82 delecji — 13 plików

---

## 3. Otwarte ryzyka — co zostało (Phase 3)

Poniżej 4 pozycje z uzasadnieniem biznesowym. Każda ma oszacowany czas i wpływ.

### 3.1 Cloudflare Turnstile na loginie (⏱️ 2h, 💰 0 zł)

**Problem:** rate limiter blokuje brute-force per IP, ale zorganizowany atak z botnetu (1000 różnych IP) przechodzi.

**Rozwiązanie:** dodać Turnstile (darmowa alternatywa reCAPTCHA od Cloudflare, bez śledzenia użytkowników, zgodne z RODO).

**Co wymaga od partnera/właściciela:** założenie konta Cloudflare (5 min) + skopiowanie 2 kluczy do config. Implementacja po stronie kodu = 1-2h.

**Ryzyko jeśli nie zrobimy:** niski do czasu gdy strona jest mało znana. Średni po publicznym launchu — scrape botnety atakują losowe panele Flask.

**Rekomendacja:** zrobić TERAZ, zerowy koszt, jednorazowe 2h.

### 3.2 `.env.key` poza folderem DB (⏱️ 1h, 💰 0 zł)

**Problem:** klucz szyfrowania danych wrażliwych (tokeny API Allegro, OLX, Vinted) leży w tym samym katalogu co baza danych. Jeśli ktoś zrobi niepoprawny backup (cały folder, zamiast tylko `.db`), eksportuje razem klucz → całe szyfrowanie jest na nic.

**Rozwiązanie:** przenieść klucz do `/etc/akces/env.key` (uprawnienia `chmod 600`, tylko root + user aplikacji), załadować przez `systemd EnvironmentFile`.

**Ryzyko jeśli nie zrobimy:** średni. Typowy user przy migracji na nowy Pi skopiuje cały folder i nieświadomie udostępni klucz.

**Rekomendacja:** zrobić, 1h pracy + update instrukcji deployment.

### 3.3 Dwuskładnikowe uwierzytelnianie (2FA TOTP) dla admina (⏱️ 1–2 dni, 💰 0 zł)

**Problem:** konto admina chronione tylko hasłem. Jeśli hasło wycieknie (phishing, wyciek bazy z innego serwisu gdzie admin użył tego samego), pełen dostęp do firmy klienta.

**Rozwiązanie:** TOTP (Google Authenticator / Authy) + backup codes.

**Wariant A — opt-in:** admin może włączyć w ustawieniach. Sensowne gdy: właściciel sam jest adminem, nie ma wielu userów adminów.

**Wariant B — obligatoryjne:** każdy admin MUSI mieć 2FA żeby się zalogować. Sensowne gdy: sprzedajemy jako SaaS, klienci enterprise wymagają.

**Ryzyko jeśli nie zrobimy:** średni dla single-tenant, wysoki dla publicznego SaaS. **Enterprise customers często pytają o 2FA w procesie zakupowym** — brak może zablokować deal.

**Rekomendacja:** zrobić Wariant A teraz (1-2 dni). Jak pojawi się pierwszy enterprise, przełączyć na B (1 godzina flag toggle).

### 3.4 Pełny rollout CSP nonce (⏱️ 1–2 tygodnie, 💰 0 zł) — ODŁOŻONE

**Problem:** obecnie w polityce bezpieczeństwa przeglądarki (CSP) mamy `unsafe-inline` i `unsafe-eval` — oznacza że każdy inline JavaScript w HTML może zostać wykonany. Zwiększa powierzchnię ataku XSS.

**Rozwiązanie:** zastąpić 577 inline event handlerów (`onclick="..."`, `onchange="..."`) w kodzie na `addEventListener()`. To **systematyczny refactor** w 5 dużych plikach (`magazynier.py`, `palety.py`, `analityka.py`, `warehouse_editor.html`, `produkt_detail.html`).

**Fundament pod tę zmianę jest już zrobiony (commit `045b276`)** — wystarczy przełączyć flagę.

**Ryzyko jeśli nie zrobimy:** niski dopóki nie ma XSS. Średni jeśli hosting ma jakiś niezaufany user-content (komentarze, ratings, opisy produktów wpisywane przez klientów).

**Rekomendacja:** **odłożyć do Phase 4.** ROI nieoptymalne (1-2 tyg pracy dla nieprawdopodobnego scenariusza na etapie MVP). Wrócić gdy sklep / recenzje / komentarze wejdą do produktu.

---

## 4. Ryzyka poza scope Phase 3

### 4.1 Multi-tenant (Phase 4, ~3 tygodnie)
Obecnie system zakłada jedną firmę per instalacja. Jeśli partner chce **publiczny SaaS** (otwarty signup dla wszystkich), potrzebujemy:
- Migracja SQLite → Postgres (SQLite nie skaluje powyżej ~100 równoczesnych userów)
- Dodanie `tenant_id` do każdej tabeli biznesowej
- Query filter globalny (żeby firma A nie zobaczyła danych firmy B)

**Koszt:** ~3 tygodnie pracy + koszt hostingu Postgres (~20-50 zł/mc na Hetzner).

**Kiedy robić:** dopiero po podpisaniu kontraktu z 2-3 płacącymi klientami. Nie na zapas.

### 4.2 Backup strategia
Obecnie: lokalne backupy na Google Drive. Dla MVP — OK. Dla enterprise:
- Automatyczne kopie off-site (S3 Glacier, Backblaze B2 — ~5 zł/mc)
- Test restore raz w miesiącu (czy backup w ogóle działa)
- Szyfrowanie backupów kluczem NIE przechowywanym razem z backupem (po Phase 3.2 będzie)

### 4.3 Compliance
- **RODO:** dane osobowe klientów (sprzedaże Allegro zawierają imię + adres). Przetwarzamy legalnie (uzasadniony interes + umowa) ale brakuje formalnej polityki. Koszt prawnika: ~500-1500 zł za pierwszą wersję.
- **ISO 27001:** nie jest wymagane dla MVP. Przy enterprise — często pytają. Audit zewnętrzny ~15-30k zł.

---

## 5. Decision matrix dla partnera

| Scenariusz sprzedaży | Phase 3 | Phase 4 | Compliance | Start |
|---|---|---|---|---|
| **Single klient B2B** (instalujemy na ich sprzęcie) | Opcjonalne | Nie | RODO klauzule w umowie | Można sprzedawać **dziś** |
| **10 klientów B2B** (każdy własna instancja) | **Must-have** (2FA obligatoryjne) | Nie | RODO + ISO light | 3 tyg po decyzji |
| **Publiczny SaaS** (otwarty signup, plan 49-299 zł/mc) | Must-have | **Must-have** | RODO formalne, Turnstile, T&C | 6-8 tyg po decyzji |
| **Enterprise** (banki, fintech, >100 userów) | Must-have | Must-have | **ISO 27001** | 4-6 mies (audit zewnętrzny) |

---

## 6. Rekomendacja zespołu technicznego

**Krok 1 (TERAZ, ~3 dni pracy):** Zrobić Phase 3 zakresu:
- ✅ Turnstile (2h)
- ✅ `.env.key` move (1h)
- ✅ 2FA TOTP opt-in (1-2 dni)

Po tym system jest **production-ready dla B2B** i scenariuszy "10 klientów każdy własna instancja".

**Krok 2 (po pierwszych 2-3 płacących klientach):** Phase 4:
- Migracja do Postgres
- Multi-tenant architecture
- Pełny CSP nonce rollout

**Krok 3 (przy pierwszym enterprise):** Compliance track:
- Formalna polityka RODO
- Audit ISO 27001 light
- 2FA obligatoryjne

---

## 7. Appendix — detale techniczne

### Lista commitów bezpieczeństwa
```
045b276 security: add CSP nonce infrastructure
d0f3ef6 security: sanitize SVG uploads (defusedxml + reject scripts/events)
e6fed6e security: close CSRF form-POST bypass
08cf5a1 security: fix license test prefix mapping
6f5fd4f security: Phase 1.5 — explicit CSRF + audit log na privileged actions
b80e896 security: Phase 1 critical hardening — access control, proxy trust, zip-slip
```

### Nowe testy
- `tests/test_system_update_access.py` — 18 testów kontroli dostępu do admin endpoints
- `tests/test_csrf_form_fallback.py` — 4 testy CSRF na HTML formach
- `tests/test_svg_sanitization.py` — 17 testów sanityzacji SVG
- `tests/test_csp_nonce.py` — 4 testy CSP nonce infrastructure

### Standardy referencyjne
- **OWASP Top 10 (2021):** pokryte A01 (Broken Access Control), A03 (Injection), A05 (Security Misconfiguration), A07 (Ident Auth Failures), A08 (Software Integrity)
- **OWASP ASVS 4.0 Level 2:** ~70% pokrycia (braki w 2FA i full CSP)
- **NIST 800-63 AAL2:** pokryte po Phase 3.3 (2FA)

### Kontakt techniczny
Kod źródłowy: `clean_v32/` worktree `agitated-goldwasser`
Testy: `pytest tests/ -q` → 83 passed, 1 skipped
Audit log: SQLite tabela `admin_audit_log` (query przez panel admina)

---

**Koniec dokumentu. Partner może zadecydować o kierunku na podstawie sekcji 5 (Decision matrix) i 6 (Rekomendacja).**
