# Historia zmian (auto-generated)

## 28.04.2026

- fix(mass-edit): odfiltruj "dla siebie" z bezposredniego wystawiania z palety
- fix(przyjecie): markDlaSiebie z data-attributes zamiast krchych selektorow
- fix(przyjecie): natychmiastowe oznaczenie "Dla siebie" + liczy sie jako ocenione
- feat(dla_siebie): przycisk w ekranie oceniania palety + zakladka filtra na liscie
- fix(stock): przerzuc sprzedaz na inna palete gdy oryginalny produkt pusty
- fix(zysk): bug w SQL - WHERE wykluczalo 'allegro' zanim CASE go zlapal
- fix(zysk): zsynchronizuj prowizje Allegro miedzy dashboardem a tax settlement
- fix(kategorie): Material Symbols spany w KATEGORIE_DISPLAY na emoji
- fix(statystyki): w miesiecznym to zaliczka PIT, nie PIT (PIT-36L jest roczny)
- feat(statystyki): rozliczenie podatkowe miesieczne (VAT + PIT) obok rocznego
- fix(zysk+ux): dropdown emoji, import netto/brutto, koszty operacyjne w zysku
- fix(sprzedaze): napraw duplikujace sie zamowienia + auto-backfill po syncu
- fix(zysk): licz zysk jak kalkulator marzy + doszacuj COGS dla nieprzypisanych sprzedazy
- feat(magazyn): flaga "dla siebie" - blokuje wystawianie na Allegro

## 26.04.2026

- fix(pwa): cache buster ?v=2 na ikonach ĹĽeby ominÄ…Ä‡ Cloudflare cache
- fix(pwa): usun dynamiczny /static/icon-{192,512}.png ktory zwracal SVG
- fix(pwa): wywal ngrok-skip-browser-warning ze wszystkich fetchy w base.html
- fix(pwa): SW v14 - force reload otwartych kart przy aktywacji
- fix(pwa): /manifest.json + /sw.js whitelisted we wszystkich auth middleware
- fix(pwa): napraw install prompt - URL ikon zamiast data: URI
- fix(pwa): dodaj manifest + SW registration do login + paletomat
- fix(allegro): zapisuj nowe oferty jako 'draft' zamiast 'aktywna'

## 23.04.2026

- feat(nav): dodaj link 'Do wystawienia' w sidebarze
- feat(magazyn): dodaj strone 'Do wystawienia' ze szkicami i niewystawionymi
- fix(allegro): usun bullet pointy Amazona z opisu, napraw & i umlauty

## 22.04.2026

- fix(scraper): filtruj Amazon UI smieci z bullet points (obrazy niedostepne, rankingi, kategorie)
- fix(sse): usun Connection:keep-alive - hop-by-hop header crashuje Waitress (PEP 3333)
- fix(sse): napraw utracono-polaczenie w streamie Allegro (ping + try/except + waitress timeout)

## 21.04.2026

- fix(title-gen): wyĹ‚Ä…cz thinking mode Gemini 2.5 (thinkingBudget=0) + wiÄ™cej tokenĂłw na odpowiedĹş
- fix(title-gen): zĹ‚Ä…cz wszystkie non-thought parts (Gemini 2.5 interleaves thinking)
- fix(title-gen): pomiĹ„ thinking parts Gemini 2.5 (parts[0]=thought, nie odpowiedĹş)
- fix(title-gen): uzyj Gemini zamiast Google Translate + napraw HTML w prompcie

## 16.04.2026

- feat(booth): sesja 1 - szkielet + mock motor control

## 15.04.2026

- test(api-v1): 37 tests covering all endpoints + auth + webhooks
- feat(api-v1): admin UI for API key management
- feat(api-v1): OpenAPI 3.0 spec + Swagger UI docs
- feat(api-v1): webhooks - registration + delivery worker + HMAC signatures
- feat(api-v1): stock + pallets endpoints
- feat(api-v1): orders CRUD + webhook trigger integration
- feat(api-v1): products CRUD endpoints + schemas
- feat(api-v1): DB schema - api_keys, api_usage_log, webhooks, webhook_deliveries
- feat(api-v1): infrastructure - blueprint, auth, rate limit, response helpers
- fix: scraper_hub / paletomat mial biale tlo zamiast dark
- fix: sync_returns nie oznaczal zwrotow gdy zamowienie bylo z innego miesiaca
- perf: sync_returns â€” batch UPDATE + paginacja + range filter (10-30x szybciej)
- security(hotfix): rollback nonce z CSP headera â€” rozjebuje inline styles
- docs: changelog 15.04 â€” Phase 1-3 security + landing + audit
- docs: security audit 2026-04 (Phase 1-3 summary dla partnera biznesowego)
- landing: typewriter effect + highlights section + workflow proof
- security: 2FA TOTP opt-in for users (pyotp + backup codes + UI)
- security: Cloudflare Turnstile on login (bot protection, disabled without keys)
- security: move encryption key outside DB folder (systemd EnvironmentFile)
- security: add CSP nonce infrastructure (fundament pod Phase 3 rollout)
- security: sanitize SVG uploads (defusedxml + reject scripts/events)
- security: close CSRF form-POST bypass (require token or same-origin Referer)
- security: fix license test prefix mapping (starter/business/free)
- security: Phase 1.5 â€” explicit CSRF + audit log na privileged actions
- security: Phase 1 critical hardening â€” access control, proxy trust, zip-slip

## 14.04.2026

- fix: mobile zostaje na home.html (pelny dashboard) + wieksze UI
- fix: kiosk mobile â€” przywroc sidebar jako slide-out drawer (pelna funkcjonalnosc)
- fix: kiosk mobile â€” force-remove sidebar via inline script (cache-proof)
- fix: mobile dostaje kiosk_home.html zamiast home.html (user complaint: za male)
- feat: kiosk widget â€” ngrok â†’ Cloudflare Tunnel (app.akceshub.com)
- landing: optimize UI sizes for mobile (<=768px and <=480px)
- fix: kiosk mode on mobile through Cloudflare Tunnel + update landing
- perf: switch to waitress WSGI server instead of Flask dev server
- fix: disable license heartbeat blocking - no dedicated license server
- fix: harden license middleware - catch all exceptions, not just ImportError

## 13.04.2026

- fix: zdjecia nie wyswietlaja sie na liscie produktow - pelny fallback chain
- fix: level shows 167K - include all sales except zwrot/anulowana, no double counting
- fix: restore sprzedaze_prywatne in level calculation (was 145K, should be ~157K)
- fix: restore offline filter in level - count only Allegro sales (~170K not 178K)
- fix: remove double-counting of offline sales in level calculation
- fix: include offline sales in poziom/level calculation
- fix: group multi-item orders into single Telegram notification
- feat: auto-backup before system update (git pull)

## 12.04.2026

- fix: CSRF referer check fails through ngrok proxy (403 on /system/update)
- security: fix rate limiting to use verified endpoint names
- resolve CHANGELOG conflict
- security: fix webhook bypass, apply CSV sanitization, add verification report
- security: full production hardening - rate limiting, session fix, webhooks, encrypted backups
- security: fix critical and high severity vulnerabilities
- fix: exclude offline sales from przychod_allegro_db to prevent double counting
- fix: replace inline onclick with data-attributes on Korekta button
- fix: update przychod_offline on offline sale and ensure sprzedaze record
- fix: rewrite offline sale to atomic SQL update, prevent race conditions
- fix: add missing CSRF tokens to dynamically created JS forms (korekty, sprzedaj offline)

## 11.04.2026

- fix: absolute stabilization of search with manual config reading and robust parsing
- fix: total restoration of search with unified API key helper and improved error reporting
- fix: stabilize search with AI competition fallback and global CSRF self-healing
- fix: implement Gemini AI search fallback and dynamic CSRF token handling
- fix: updated scrapers with JSON/showroom logic, added auto-translation to EN, and fixed CSRF in inventory corrections
- feat: rewrite scout_by_phrase - search Alibaba/AliExpress first, then check Allegro competition
- fix: remove duplicate old scout_by_phrase that was overriding new web-scraping version
- fix: replace blocked /offers/listing API with web scraping + fix Service Worker caching POST forms
- feat: added diagnostic items to Winning Scout UI to show why results are empty
- fix: relax winning scout search filters to prevent empty lists
- feat: security hardening (XSS, Rate Limiting, Fail2Ban) and Winning Scout phrase search filters

## 10.04.2026

- Zoptymalizowano scraper_laptop.py pod Allegro API v2

## 07.04.2026

- fix: use correct paleta_koszt_szt for profit calculation in Smart Insights

