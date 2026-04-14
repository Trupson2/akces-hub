# Historia zmian (auto-generated)

## 15.04.2026

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
- security: fix webhook bypass, apply CSV sanitization, add verification report
- security: full production hardening - rate limiting, session fix, webhooks, encrypted backups
- security: fix critical and high severity vulnerabilities
- fix: exclude offline sales from przychod_allegro_db to prevent double counting
- fix: replace inline onclick with data-attributes on Korekta button
- fix: update przychod_offline on offline sale and ensure sprzedaze record
- fix: rewrite offline sale to atomic SQL update, prevent race conditions

## 07.04.2026

- fix: use correct paleta_koszt_szt for profit calculation in Smart Insights
- feat: add Smart Insights page and automated Telegram alerts
- feat: add filter buttons on pallet detail (aktywne/szkice/niewystawione/w magazynie)
- feat: show real Allegro offer status on pallet detail page
- fix: improve bundle matching with complementary categories and keywords
- feat: cross-pallet stock count in Telegram sale notifications

## 06.04.2026

- feat: add dedicated Zestawy Allegro page for bundle suggestions
- feat: add stock column to Allegro Performance and bundle suggestions to product detail
- fix: filter Amazon cross-selling and store promo from bullet points
- fix: expand Allegro banned phrases filter for descriptions

## 05.04.2026

- fix: translate English/German titles to Polish before formatting
- feat: use Google Translate instead of Gemini for product titles
- fix: edit form shows pallet cost instead of Amazon price for BRUTTO/SZT
- fix: filter Amazon junk from bullet points before description generation
- fix: filter Amazon marketing junk from bullet points
- feat: replace Gemini with programmatic SEO title generation
- fix: brand at end of title, strip quantities (szt/pack)
- fix: skip translation for already-Polish Amazon titles
- feat: auto-scrape Amazon (BS4) when name too short in Regeneruj
- feat: use BeautifulSoup for Amazon title/bullet scraping
- fix: strip commas from generated SEO titles
- fix: use optimize_title_seo as fallback instead of raw nazwa
- fix: strip ASIN from input before Gemini, remove broken title fallback
- fix: remove ASIN codes from generated SEO titles
- fix: improve SEO title generation - inject bullet_points/ASIN into prompt (#114)
- fix: improve SEO title generation - inject bullet_points/ASIN into prompt

## 03.04.2026

- fix: PhonkBot dashboard opens in same window (kiosk friendly)
- fix: disable old kiosk.css overrides in kiosk_home.html
- fix: kiosk fullscreen - override .container max-width:1400px
- fix: PhonkBot links use dynamic hostname instead of localhost
- fix: force Gemini to rewrite bullet points, not copy/translate them
- fix: replace broken emoji placeholders with real emoji
- fix: batch meta title - skip stale tytul_seo, always use Gemini AI
- fix: remove fast path in meta title - always use Gemini AI

## 02.04.2026

- fix: kiosk on Pi IP 192.168.100.200 + localhost
- cleanup: remove kiosk debug logging
- debug: log kiosk detection - remote, xff, pi_screen
- fix: kiosk only when accessed from Pi localhost, not remote
- fix: kiosk only on Pi (Linux ARM) or ?kiosk=1, desktop/mobile get home.html
- feat: mobile gets home.html, desktop/Pi gets kiosk_home.html
- fix: kiosk 40px side padding + full width header
- fix: kiosk full width - calc(100vw - 250px sidebar)
- fix: kiosk left padding 260px to clear 250px sidebar
- fix: kiosk padding - keep left space for sidebar, expand right
- fix: restore kiosk base.html extends + fullscreen content override
- feat: kiosk fullscreen - standalone layout, no sidebar, wider grid
- feat: kiosk_home.html as default dashboard after login
- fix: add /dashboard route + fix all redirects for launcher flow
- feat: PhonkBot kiosk widget + launcher + proxy routes

## 01.04.2026

- fix: restock alert uses listing date not inventory date (#113)
- fix: batch dedup for Allegro listings, show total order price, stabilize ngrok (#112)

## 31.03.2026

- feat: winning products â€” badge NOWE + sortowanie po dacie
- fix: PWA offline â†’ przekierowanie na ngrok URL
- fix: ngrok start/stop cross-platform + token z env
- feat: marĹĽa na rÄ™kÄ™ po VAT 23% + PIT liniowy 19%
- fix: marĹĽa netto % liczona od koszt_palet_msc zamiast COGS
- feat: marĹĽa netto % na dashboardzie w kaflu Zysk
- fix: ujednolicenie przychodu w dashboardzie â€” jedna baza dla przychod/prowizja/zysk
- fix: grupowanie po ASIN+stan + poprawna suma ilosci w streamie
- feat: parametr Stan (11323) + dedup per condition
- fix: extract_parameters_with_ai - REST API zamiast SDK google.generativeai
- fix: usun condition z payloadu product-offers - unsupported property
- feat: wizualne grupowanie po ASIN w widoku palety
- fix: condition produktu wysylane do Allegro przy tworzeniu oferty
- feat: ASIN dedup przy quick-draft - dodaj ilosc do istniejacej oferty Allegro
- fix: usun cross-pallet dedup - kazda paleta ma wlasne ilosci
- feat: ASIN/EAN deduplication across pallets + API endpoints
- security: rate limiting + configurable license URL + path traversal fix
- fix: remove SENT query - not valid checkout-forms status
- fix: SENT without date filter, only READY_FOR_PROCESSING with date
- fix: remove CANCELLED from sync - also returns 400 with date filter
- fix: remove FILLED/BOUGHT from sync - Allegro returns 400 with date filter

