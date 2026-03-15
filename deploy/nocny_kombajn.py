#!/usr/bin/env python3
"""
NOCNY KOMBAJN - automatyczne przygotowanie szkiców Allegro
Odpala się o 2:00 w nocy.

Flow (identyczny jak mass-edit):
1. Znajdź produkty w statusie 'magazyn' BEZ oferty na Allegro
2. Autowycena z Amazon (jeśli brak cena_allegro)
3. Pobierz bullet_points (Amazon cechy)
4. Generuj opis HTML PRO z Gemini AI (jak mass-edit)
5. Upload max 8 zdjęć
6. Stwórz szkic (draft) na Allegro z GPSR
7. Rano user przegląda szkice, poprawia ceny, klika 'Opublikuj'
"""

import sys, os, time, json, re
from datetime import datetime

# Dodaj ścieżkę projektu
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)
os.chdir(APP_DIR)

def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)

def run_kombajn():
    """Główna funkcja nocnego kombajnu — identyczny flow jak mass-edit"""
    from modules.database import get_db, get_config
    from modules.allegro_api import is_authenticated, create_offer, upload_image_to_allegro, search_categories
    from modules.utils import generuj_opis_html_pro, optimize_title_seo, generuj_gpsr_info

    log("🌙 NOCNY KOMBAJN - START")
    log("=" * 50)

    # Sprawdź czy Allegro jest połączone
    if not is_authenticated():
        log("❌ Allegro nie połączone - pomijam")
        return 0

    # Pobierz klucz Gemini API (potrzebny do opisów)
    gemini_key = get_config('gemini_api_key', '')
    if gemini_key:
        log("🤖 Gemini API: ✅ aktywne (opisy AI)")
    else:
        log("⚠️ Gemini API: brak klucza (opisy z szablonu)")

    conn = get_db()

    # 1. Znajdź produkty BEZ oferty na Allegro
    # Warunki: status 'magazyn' + brak oferty + ilosc > 0 + GOTOWY do wystawienia
    # "Gotowy" = ma zdjęcie (zdjecie_url lub images) LUB ma ASIN (zidentyfikowany)
    # ANTI-DUPLIKAT: sprawdza po produkt_id ORAZ po ASIN (żeby nie wystawiać tego samego produktu 2x)
    produkty = conn.execute('''
        SELECT p.id, p.nazwa, p.cena_allegro, p.cena_netto, p.cena_brutto,
               p.ilosc, p.ean, p.asin, p.stan, p.kategoria, p.opis_ai,
               p.zdjecie_url, p.images, p.paleta_id, p.bullet_points, p.meta_title,
               pal.nazwa as paleta_nazwa, pal.cena_zakupu as paleta_cena
        FROM produkty p
        LEFT JOIN palety pal ON p.paleta_id = pal.id
        WHERE p.status = 'magazyn'
        AND p.ilosc > 0
        AND p.nazwa IS NOT NULL
        AND p.nazwa != ''
        AND p.id NOT IN (SELECT DISTINCT produkt_id FROM oferty WHERE produkt_id IS NOT NULL)
        AND (
            (p.zdjecie_url IS NOT NULL AND p.zdjecie_url != '')
            OR (p.images IS NOT NULL AND p.images != '' AND p.images != '[]')
            OR (p.asin IS NOT NULL AND p.asin != '')
        )
        AND p.id NOT IN (
            SELECT p2.id FROM produkty p2
            WHERE p2.asin IS NOT NULL AND p2.asin != ''
            AND p2.status IN ('szkic', 'wystawiony')
            AND EXISTS (
                SELECT 1 FROM produkty p3
                WHERE p3.asin = p2.asin AND p3.id != p2.id
                AND p3.status = 'magazyn'
            )
        )
        ORDER BY p.data_dodania DESC
    ''').fetchall()

    # Dodatkowa deduplication po ASIN — zostaw tylko 1 produkt per ASIN
    seen_asins = set()
    unique_produkty = []
    for p in produkty:
        asin = p['asin']
        if asin and asin in seen_asins:
            log(f"   ⏭️ Pomijam duplikat ASIN {asin} (#{p['id']})")
            continue
        if asin:
            seen_asins.add(asin)
        unique_produkty.append(p)

    if len(produkty) != len(unique_produkty):
        log(f"   🔄 Deduplikacja ASIN: {len(produkty)} → {len(unique_produkty)}")
    produkty = unique_produkty

    total = len(produkty)
    log(f"📦 Znaleziono {total} produktów bez oferty Allegro")

    if total == 0:
        log("✅ Wszystkie produkty mają oferty - nic do roboty!")
        conn.close()
        return 0

    # Limity: max 20 szkiców na noc (żeby nie przeciążać API)
    MAX_DRAFTS = 20
    produkty = produkty[:MAX_DRAFTS]
    log(f"📝 Przetwarzam {len(produkty)} produktów (limit: {MAX_DRAFTS})")

    created = 0
    errors = 0

    for idx, p in enumerate(produkty, 1):
        pid = p['id']
        nazwa = p['nazwa']
        asin = p['asin'] or None
        kategoria = p['kategoria'] or 'inne'
        log(f"\n--- [{idx}/{len(produkty)}] #{pid}: {nazwa[:60]}")

        try:
            # Otwórz świeże połączenie (create_offer zamyka poprzednie)
            conn = get_db()

            # ========== ANTI-DUPLIKAT: sprawdź ASIN w oferty + na Allegro ==========
            if asin:
                # Sprawdź czy inny produkt z tym ASIN już ma ofertę
                existing = conn.execute('''
                    SELECT o.allegro_id FROM oferty o
                    JOIN produkty p2 ON o.produkt_id = p2.id
                    WHERE p2.asin = ? AND o.produkt_id != ?
                ''', (asin, pid)).fetchone()
                if existing:
                    log(f"   ⏭️ SKIP — ASIN {asin} już ma ofertę: {existing['allegro_id']}")
                    # Zmień status na szkic żeby nie próbować ponownie
                    conn.execute("UPDATE produkty SET status = 'szkic' WHERE id = ?", (pid,))
                    conn.commit()
                    continue

            # ========== 2. AUTOWYCENA ==========
            cena = p['cena_allegro'] or 0
            if cena <= 0:
                cena = _autowycena_amazon(conn, p)
                if cena <= 0:
                    if p['cena_brutto'] and p['cena_brutto'] > 0:
                        cena = round(p['cena_brutto'] * 1.3, 2)
                    elif p['cena_netto'] and p['cena_netto'] > 0:
                        cena = round(p['cena_netto'] * 1.6, 2)
                    else:
                        cena = 99.99
                    log(f"   💰 Fallback cena: {cena} zł")
                conn.execute('UPDATE produkty SET cena_allegro = ? WHERE id = ?', (cena, pid))
                conn.commit()
            log(f"   💰 Cena: {cena} zł")

            # ========== 3. TYTUŁ SEO (jak mass-edit) ==========
            if p['meta_title'] and len(str(p['meta_title']).strip()) > 10:
                tytul = str(p['meta_title']).strip()[:75]
                log(f"   📝 Tytuł (meta_title): {tytul[:65]}")
            else:
                # Sprawdź scraped.tytul_seo
                tytul = None
                if asin:
                    scraped = conn.execute(
                        'SELECT tytul_seo FROM scraped WHERE asin = ?', (asin,)
                    ).fetchone()
                    if scraped and scraped['tytul_seo'] and len(scraped['tytul_seo']) > 10:
                        tytul = scraped['tytul_seo'][:75]
                if not tytul:
                    tytul = optimize_title_seo(nazwa, 75)
                log(f"   📝 Tytuł: {tytul[:65]}")

            # ========== 4. BULLET POINTS (jak mass-edit) ==========
            bullet_points = []
            bp_raw = p['bullet_points'] or ''
            if bp_raw:
                try:
                    bullet_points = json.loads(bp_raw) if isinstance(bp_raw, str) else bp_raw
                    if not isinstance(bullet_points, list):
                        bullet_points = []
                except:
                    bullet_points = []

            # Fallback: scraped.bullet_points
            if not bullet_points and asin:
                try:
                    scraped = conn.execute(
                        'SELECT bullet_points FROM scraped WHERE asin = ?', (asin,)
                    ).fetchone()
                    if scraped and scraped['bullet_points']:
                        bullet_points = json.loads(scraped['bullet_points']) if isinstance(scraped['bullet_points'], str) else scraped['bullet_points']
                        if not isinstance(bullet_points, list):
                            bullet_points = []
                except:
                    pass

            if bullet_points:
                log(f"   📋 Bullet points: {len(bullet_points)} cech")
            else:
                log(f"   📋 Bullet points: brak (opis z szablonu)")

            # ========== 5. OPIS HTML PRO (identycznie jak mass-edit) ==========
            zdjecie_url = p['zdjecie_url'] or ''
            log(f"   📝 Generuję opis PRO...")

            opis_html, _ = generuj_opis_html_pro(
                nazwa,
                [zdjecie_url] if zdjecie_url else [],
                kategoria,
                bullet_points,
                gemini_key=gemini_key,
                asin=asin
            )
            log(f"   📝 Opis: {len(opis_html)} znaków HTML")

            # ========== 6. GPSR (jak mass-edit) ==========
            gpsr = generuj_gpsr_info(nazwa, kategoria)

            # ========== 7. ZDJĘCIA - upload max 8 (jak mass-edit) ==========
            zdjecia_urls = _upload_zdjecia(conn, p)
            log(f"   📸 Zdjęć: {len(zdjecia_urls)}")

            # ========== 8. KATEGORIA Allegro ==========
            kat_id = None
            # Najpierw spróbuj istniejącą (numeryczną)
            if p['kategoria']:
                try:
                    k = str(p['kategoria']).strip()
                    if k.isdigit():
                        kat_id = k
                except:
                    pass

            # Jeśli brak — search_categories (jak mass-edit)
            if not kat_id:
                try:
                    cat_result, cat_error = search_categories(nazwa[:50])
                    if cat_result and cat_result.get('matchingCategories'):
                        kat_id = cat_result['matchingCategories'][0].get('id')
                        log(f"   🏷️ Kategoria: {kat_id}")
                except:
                    pass

            # ========== 9. STWÓRZ SZKIC (draft) ==========
            result = create_offer(
                tytul[:75],
                opis_html,
                cena,
                zdjecia_urls=zdjecia_urls if zdjecia_urls else None,
                kategoria_id=kat_id,
                ilosc=int(p['ilosc'] or 1),
                ean=p['ean'] or None,
                asin=asin,
                gpsr=gpsr,
                bullet_points=bullet_points
            )

            # create_offer zwraca (result, error) tuple
            if isinstance(result, tuple):
                result, error_msg = result
            else:
                error_msg = None

            # create_offer zamyka połączenie DB — otwórz nowe
            conn = get_db()

            if result and isinstance(result, dict) and 'id' in result:
                offer_id = result['id']
                conn.execute('''
                    INSERT OR IGNORE INTO oferty
                    (allegro_id, produkt_id, tytul, cena, status, data_wystawienia)
                    VALUES (?, ?, ?, ?, 'draft', ?)
                ''', (offer_id, pid, tytul[:200], cena, datetime.now().isoformat()))
                conn.execute("UPDATE produkty SET status = 'szkic' WHERE id = ?", (pid,))
                conn.commit()

                created += 1
                log(f"   ✅ Szkic utworzony: {offer_id}")
            else:
                errors += 1
                log(f"   ❌ Błąd: {error_msg or result}")

            # Pauza między ofertami (Gemini + Allegro API)
            time.sleep(3)

        except Exception as e:
            errors += 1
            log(f"   ❌ Błąd: {e}")
            import traceback
            traceback.print_exc()
            continue

    conn.close()

    log(f"\n{'=' * 50}")
    log(f"🌙 NOCNY KOMBAJN - KONIEC")
    log(f"   ✅ Utworzono szkiców: {created}")
    log(f"   ❌ Błędów: {errors}")
    log(f"   📦 Pominięto: {total - len(produkty)} (ponad limit {MAX_DRAFTS})")

    # Wyślij powiadomienie na Telegram
    try:
        from modules.telegram_bot import send_telegram
        msg = f"🌙 <b>Nocny Kombajn</b>\n\n"
        msg += f"📝 Szkiców utworzonych: <b>{created}</b>\n"
        if errors:
            msg += f"❌ Błędów: {errors}\n"
        if total > MAX_DRAFTS:
            msg += f"📦 Czeka jeszcze: {total - MAX_DRAFTS} produktów\n"
        msg += f"\n💡 Wejdź w Allegro → Szkice i przejrzyj ceny!"
        send_telegram(msg, silent=True)
    except:
        pass

    return created


def _autowycena_amazon(conn, p):
    """Próbuje pobrać cenę z Amazon — kurs NBP"""
    if not p['asin']:
        return 0
    try:
        from modules.utils import scrape_amazon_product
        from modules.magazynier import _amazon_price_to_pln
        log(f"   🔍 Amazon scrape: {p['asin']}")
        data = scrape_amazon_product(p['asin'])
        if data and data.get('price') and data['price'] > 0:
            cena_pln = _amazon_price_to_pln(data['price'], data.get('domain'))
            cena = round(cena_pln * 0.85, 2)
            cena = int(cena) + 0.99 if cena > 10 else cena
            log(f"   🔍 Amazon cena: {data['price']} ({data.get('domain','?')}) → {cena_pln:.2f} PLN → Allegro: {cena} zł")
            return cena
    except Exception as e:
        log(f"   ⚠️ Amazon error: {e}")
    return 0


def _upload_zdjecia(conn, p):
    """Upload zdjęć do Allegro, zwraca listę URL-i (max 8, jak mass-edit).
    Priorytet: URL-e (Amazon) > lokalne pliki
    """
    from modules.allegro_api import upload_image_to_allegro

    zdjecia = []

    def _is_url(path):
        return isinstance(path, str) and path.startswith('http')

    def _local_exists(path):
        if not isinstance(path, str) or path.startswith('http'):
            return True
        full = os.path.join(APP_DIR, path) if not os.path.isabs(path) else path
        return os.path.exists(full)

    # 1. zdjecie_url — zwykle Amazon URL
    if p['zdjecie_url'] and _is_url(p['zdjecie_url']):
        img_url = p['zdjecie_url']
        if 'media-amazon.com' in img_url:
            img_url = re.sub(r'\._[A-Z0-9_,]+_\.', '._AC_SL1500_.', img_url)
        zdjecia = [img_url]

    # 2. scraped — URL-e Amazon (wszystkie zdjęcia produktu)
    if p['asin']:
        try:
            scraped = conn.execute(
                'SELECT images, wszystkie_zdjecia FROM scraped WHERE asin = ?', (p['asin'],)
            ).fetchone()
            if scraped:
                for field in ['wszystkie_zdjecia', 'images']:
                    if scraped[field]:
                        try:
                            imgs = json.loads(scraped[field])
                            url_imgs = [img for img in imgs if _is_url(img)]
                            local_imgs = [img for img in imgs if not _is_url(img) and _local_exists(img)]
                            if url_imgs:
                                for i, u in enumerate(url_imgs):
                                    if 'media-amazon.com' in u:
                                        url_imgs[i] = re.sub(r'\._[A-Z0-9_,]+_\.', '._AC_SL1500_.', u)
                                zdjecia = url_imgs
                                break
                            elif local_imgs:
                                zdjecia = local_imgs
                                break
                        except:
                            pass
        except:
            pass

    # 3. produkty.images (JSON array)
    if not zdjecia and p['images']:
        try:
            imgs = json.loads(p['images']) if isinstance(p['images'], str) else p['images']
            if imgs:
                valid = [img for img in imgs if _is_url(img) or _local_exists(img)]
                if valid:
                    zdjecia = valid
        except:
            pass

    # 4. Ostatni fallback: zdjecie_url (nawet lokalne)
    if not zdjecia and p['zdjecie_url']:
        zdjecia = [p['zdjecie_url']]

    if not zdjecia:
        return []

    # Upload max 8 zdjęć (jak mass-edit, nie 6)
    uploaded = []
    for img in zdjecia[:8]:
        try:
            url = upload_image_to_allegro(img)
            if url:
                uploaded.append(url)
        except Exception as e:
            log(f"   ⚠️ Upload fail: {str(e)[:60]}")
        time.sleep(0.5)

    return uploaded


if __name__ == '__main__':
    run_kombajn()
