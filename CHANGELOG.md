# Historia zmian (auto-generated)

## 27.05.2026

- fix(db): migrate_reset_fake_data_wystawienia uzywa Polish schema klucz/wartosc
- fix(import): get_config + set_config na top-level w app.py
- fix(privacy+ui): generic launcher / dostawcy placeholder / kiosk LAN configurable
- fix(privacy): WYWAL hardcoded personal data (Adrian Gauza, Poniatowskiego, Mieszkowice...)
- feat(kod_magazynowy): configurowalny prefix per klient (Ty=MAG, Kolega=ZAG)
- feat(magazyn): własny kod_magazynowy + bulk reformat
- feat(magazyn): ręczne dodawanie produktu z formularzem
- fix(ui): kiosk mode OPT-IN (domyslnie wyłączony — klient widzi pełen dashboard)
- fix(ui): wyłączalny auto-kiosk + ?full=1 / ?kiosk=0 override
- fix(ui): /api/cloudflare-status hostname z config (NIE hardcoded)
- fix(ui): sidebar Sklepakces.pl link conditionally hidden (non-owners)
- fix(ui): PhonkBot widget DOMYŚLNIE OFF (osobny projekt, opt-in via config)
- feat(poziom): wizard ustawiania celów per-klient (NIE hardcoded 1M)
- fix(ui): per-instance platform_name + cloudflare_url (NIE hardcoded)
- fix(release): EXCLUDE static/downloads + DB backups + accidental .zip files
- feat(release): embed MASTER LICENSE_SECRET w zipie klienta (default ON)
- fix(license): import get_config w licence_activate handler
- fix(release): EXCLUDE single-digit debug files (1, 2, 3, tmp.txt)
- fix(release): EXCLUDE nested akces-hub/ subfolder + photo_daemon
- fix(release): EXCLUDE niepotrzebne foldery/pliki klienta
- fix(push): NoneType format crash gdy license denied + complete error dict
- security+ux: defense-in-depth sklepakces gate + delete licencji
- fix(sklepakces): whitelist case-insensitive + substring match (UX)

## 25.05.2026

- security: sklepakces gate przez license.client whitelist (NIE plan)
- security: gate sklepakces integration za plan enterprise/business
- security: wywal hardcoded ngrok URL (audit pre-sale)
- fix(push): --force działa w trybie --all (re-push wszystkich z bypass mirror)
- feat(scripts): fetch_allegro_images.py — pobierz zdjęcia z Allegro dla stubów
- feat(scripts): relink_orphaned_oferty.py — fix oferty.produkt_id=NULL
- feat(scripts): import_allegro_legacy.py — import starych aukcji Allegro do Hub
- feat(push): --include-listed — push też produkty wystawione na Allegro

## 24.05.2026

- fix(titles): meta_title (Polish display) > krotki_tytul (SEO short) > nazwa
- feat(scripts): generate_polish_titles.py — bulk polski krotki_tytul via Gemini
- fix(gpsr): brand override OVERRIDES fallback CET (precedence bug)
- fix(fix_gpsr_addresses): poprawna nazwa funkcji + lepsza diagnostyka
- fix(gpsr): relax INCOMPLETE filter — name bez address > AKCES fallback
- fix(palety): pełne tytuły produktów (2-line wrap zamiast ellipsis)
- refactor(palety): unify pallet view — redirect /palety/<id> → /magazyn/paleta-id/<id>
- feat(stock-sync): EAN-based multi-pallet pooling — nie zamykać aukcji gdy inna paleta ma stock
- feat(scripts): fix_gpsr_addresses.py — end-to-end naprawa adresów na sklepie
- fix(gpsr): protect manual entries z --force + wymuszać address+email
- fix(gemini): dynamic thinking_budget=-1 + step-by-step prompt
- fix(gemini): enable thinking_budget=2048 — brand recall WYMAGA reasoning
- fix(gemini): revert notes removal (regresja ATUMTEK) + silence SDK logs
- fix(gemini): remove 'notes' field z prompt — przyczyna JSON truncation
- chore(gemini): migracja google.generativeai → google.genai (SDK 2025)
- feat(stock-sync): sprzedaż na sklepie → auto-zamknięcie aukcji Allegro
- fix(gemini): 2.5-flash thinking mode obcina output → disable + bump tokens
- fix(gemini): tolerant JSON parsing — strip markdown wrap + regex fallback
- fix(gemini): model gemini-2.5-flash (current) + 429 retry z backoff
- fix(gpsr): Gemini prompt strict — NIE używaj CET/Amazon Retourenkauf jako guess
- feat: scripts/auto_fill_gpsr_brands.py — Gemini auto-fill EU rep per brand
- fix(gpsr): hard timeout 18s/Playwright + skip gdy brand override istnieje
- feat(sklepakces_push): auto-detect gabaryt → WC shipping class "gabaryt"
- feat(gpsr): brand-based override + auto-populate (per producent EU rep)
- fix(gpsr): EU responsible person fallback z AKCES → CET PRODUCT SERVICE (legal)
- feat(sklepakces): GPSR manufacturer fallback z Gemini brand (parameters JSON)
- fix(fetch_amazon_gpsr CLI): respektuj config amazon_gpsr_playwright_fallback
- fix(sklepakces): dedup EAN — 2 produkty Hub z tym samym EAN → SKIP drugi push
- feat: link oferty.produkt_id z Hub produkty (po EAN/GTIN lub fuzzy title)
- feat: scripts/sync_allegro_descriptions.py — fetch FAKTYCZNE opisy z Allegro
- fix(sklepakces): strip <img>/<figure>/<picture> z opisów (Allegro/scraped/Gemini)
- fix(enrich_paleta): Gemini model fallback chain (Google retires versions)
- feat(sklepakces_dashboard): responsive mobile CSS (telefon-friendly)
- feat(sklepakces): cache scraped.opis_html + scraped.tytul_seo + PL fallback title
- fix(sklepakces): paleta-supplier blacklist + Gemini enrich script
- feat(sklepakces): single DELETE per produkt w dashboardzie (🗑 button per row)
- fix(sklepakces_dashboard): 2 osobne przyciski zamiast JS prompt (blocked)
- fix(sklepakces): default safe "allegro_only" + skip-bez-ceny zamiast error
- feat(sklepakces_dashboard): "Synchronizuj wszystko" pushuje też NEW eligible + opcjonalny allegro-only
- perf(sklepakces_dashboard): cache 10s + CTE window function (zamiast 350× subquery)
- fix(sklepakces_dashboard): background thread + progress banner dla "Re-push wszystkie"

## 23.05.2026

- feat(sklepakces_push): auto-gen WC attributes dla Specyfikacja tab
- feat(sklepakces_push): auto-generuj minimalny opis HTML gdy brak Allegro+Gemini
- feat(gpsr): Playwright headless browser fallback dla lazy-loaded Amazon GPSR

## 22.05.2026

- feat(sklepakces): bulk delete WSZYSTKICH Hub produktów z WC + dashboard button
- feat(sklepakces): dark theme + sidebar nav + WC publish/draft stats
- fix(gpsr): content-based parser — szuka po HEADER TEXT zamiast po ID anchorach
- feat(sklepakces): Dashboard UI — przegląd pushed produktów + akcje
- feat: opis z aktywnej Allegro + parser Amazon GPSR 2024 layout
- feat(sklepakces_push): pobieraj STOCK z aktywnej oferty Allegro (oferty.ilosc)
- debug(gpsr): rozroznij captcha vs brak-GPSR-w-HTML w fallback messages
- feat(sklepakces_push): pełne mapowanie Hub kategoria → WC slugs + hero tile parents
- feat(sklepakces_push): bierz cenę z AKTYWNEJ aukcji Allegro + alerty Telegram
- fix(sklepakces_push): poprawione mapowanie ceny + multi-image attach + --force
- fix(gpsr): nie cachuj fallback "dziadka", lepsza captcha detection, --purge-cache CLI
- feat(gpsr): Amazon scraper dla danych UE 2023/988 + auto-publish przy push
- fix(sklepakces_push): canonical path bez '/wp-json' (WP REST router strippuje prefix)
- feat(sklepakces): Hub→sklepakces.pl product push (OUTGOING sync, HMAC-signed POST)

## 20.05.2026

- feat(autowycena): data-driven kalibracja — AI uczy sie z realnych sprzedazy
- fix(autowycena): kalibracja przykladow dla specjalistycznych produktow
- fix(autowycena): radykalna poprawa promptu Gemini + dolny prog cena_allegro
- fix(security): PHASE 1.1++ post-deploy — taint-flow leak allegro_api.py:413
- docs(release): jasno oznacz że sanity sekcji 1 jest na maszynie operatora
- chore(changelog): regen RC (final auto-stamp PHASE 1-4)

## 19.05.2026

- chore(changelog): regen po PHASE 4 (autogeneracja UTF-8)
- docs: PHASE 4 — RELEASE.md (konsolidacja release workflow)
- fix(cleanup): PHASE 4 — encoding CHANGELOG + invalid escape sequences
- fix(release): PHASE 4 — domknij luki sekretów w build_release + auto-weryfikacja
- feat(audit): PHASE 3 — audyt login/logout (rozliczalność dostępu)

