#!/usr/bin/env python3
"""
SCRAPER LAPTOP — uruchom na komputerze z dostępem do Amazon.
Scrapuje pełne tytuły produktów i wysyła na Pi (Akces Hub).

Użycie:
    python scraper_laptop.py

Konfiguracja poniżej (PI_URL):
"""

import re
import time
import json
import requests

# ═══════════════════════════════════════════════════════════════
# KONFIGURACJA — zmień PI_URL na swój adres ngrok lub lokalny IP
# ═══════════════════════════════════════════════════════════════
PI_URL = 'https://unsatiating-dirgelike-audrina.ngrok-free.dev'
# Alternatywnie w sieci lokalnej:
# PI_URL = 'http://192.168.100.X:5000'

SLEEP_BETWEEN = 2  # sekundy między requestami do Amazon
MAX_PRODUCTS = 200  # limit produktów do scrapowania


def scrape_amazon(asin):
    """Scrapuje dane produktu z Amazon — próbuje wiele domen"""
    domains = ['amazon.pl', 'amazon.de', 'amazon.com', 'amazon.co.uk', 'amazon.fr']

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'pl-PL,pl;q=0.9,en;q=0.7',
    })

    for domain in domains:
        try:
            # Cookies
            try:
                session.get(f'https://www.{domain}/', timeout=8)
            except:
                pass

            url = f'https://www.{domain}/dp/{asin}'
            resp = session.get(url, timeout=12)
            if resp.status_code != 200:
                continue

            text = resp.text
            if 'captcha' in text.lower() or 'robot check' in text.lower():
                print(f'  CAPTCHA na {domain}, próbuję dalej...')
                continue

            # Tytuł
            title = None
            for pattern in [
                r'<span id="productTitle"[^>]*>\s*([^<]+?)\s*</span>',
                r'<h1[^>]*id="title"[^>]*>.*?<span[^>]*>([^<]+)</span>',
            ]:
                m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
                if m:
                    t = re.sub(r'\s+', ' ', m.group(1).strip())
                    if len(t) > 15:
                        title = t
                        break

            if not title:
                # Fallback: <title> tag
                m = re.search(r'<title>(.+?)</title>', text, re.IGNORECASE)
                if m:
                    t = m.group(1).split(':')[0].split('|')[0].split(' - Amazon')[0].strip()
                    t = re.sub(r'\s+', ' ', t)
                    if len(t) > 15 and 'amazon' not in t.lower():
                        title = t

            if not title:
                continue

            # Bullet points
            bullet_points = []
            bp_matches = re.findall(r'<span class="a-list-item">\s*([^<]{15,300})\s*</span>', text)
            for bp in bp_matches[:6]:
                bp_clean = re.sub(r'\s+', ' ', bp.strip())
                if len(bp_clean) > 10 and not bp_clean.startswith('{'):
                    bullet_points.append(bp_clean)

            # Zdjęcia
            all_images = []
            img_matches = re.findall(r'"hiRes"\s*:\s*"(https://[^"]+)"', text)
            if img_matches:
                all_images = list(dict.fromkeys(img_matches))[:8]
            if not all_images:
                img_matches = re.findall(r'"large"\s*:\s*"(https://[^"]+)"', text)
                all_images = list(dict.fromkeys(img_matches))[:8]

            # Cena
            price = 0
            price_m = re.search(r'class="a-price-whole">(\d[\d\s,.]*)<', text)
            if price_m:
                try:
                    price = float(price_m.group(1).replace(' ', '').replace('.', '').replace(',', '.'))
                except:
                    pass

            # Kategoria
            category = ''
            cat_m = re.search(r'<a[^>]*class="a-link-normal a-color-tertiary"[^>]*>\s*([^<]+)', text)
            if cat_m:
                category = cat_m.group(1).strip()

            return {
                'title': title,
                'bullet_points': bullet_points,
                'all_images': all_images,
                'price': price,
                'category': category,
                'domain': domain,
            }

        except Exception as e:
            print(f'  Błąd {domain}: {e}')
            continue

    return None


def main():
    print('=' * 60)
    print('AKCES HUB — Remote Amazon Scraper')
    print(f'Serwer: {PI_URL}')
    print('=' * 60)

    # Pobierz listę ASINów do scrapowania
    print('\n[1/3] Pobieram listę ASINów...')
    try:
        r = requests.get(
            f'{PI_URL}/paletomat/api/scraper/asins-needed',
            headers={'ngrok-skip-browser-warning': '1'},
            timeout=15
        )
        data = r.json()
    except Exception as e:
        print(f'BŁĄD połączenia z Pi: {e}')
        print(f'Sprawdź czy {PI_URL} jest dostępny')
        return

    asins = data.get('asins', [])[:MAX_PRODUCTS]
    print(f'   Znaleziono {len(asins)} produktów do scrapowania')

    if not asins:
        print('   Wszystkie produkty mają pełne nazwy!')
        return

    # Scrapuj Amazon
    print(f'\n[2/3] Scrapuję Amazon ({len(asins)} produktów)...')
    ok, fail = 0, 0
    for i, asin in enumerate(asins):
        print(f'\n  [{i+1}/{len(asins)}] {asin}...', end=' ')
        result = scrape_amazon(asin)

        if result and result.get('title'):
            print(f'OK: {result["title"][:50]}...')

            # Wyślij na Pi
            try:
                resp = requests.post(
                    f'{PI_URL}/paletomat/api/scraper/update',
                    json={
                        'asin': asin,
                        'title': result['title'],
                        'bullet_points': result.get('bullet_points', []),
                        'all_images': result.get('all_images', []),
                        'price': result.get('price', 0),
                        'category': result.get('category', ''),
                    },
                    headers={'ngrok-skip-browser-warning': '1', 'Content-Type': 'application/json'},
                    timeout=10
                )
                if resp.status_code == 200:
                    ok += 1
                else:
                    print(f'    BŁĄD wysyłania: {resp.status_code} {resp.text[:100]}')
                    fail += 1
            except Exception as e:
                print(f'    BŁĄD wysyłania: {e}')
                fail += 1
        else:
            print('FAIL (brak danych)')
            fail += 1

        time.sleep(SLEEP_BETWEEN)

    # Podsumowanie
    print('\n' + '=' * 60)
    print(f'[3/3] GOTOWE!')
    print(f'   Sukces: {ok}')
    print(f'   Błędy:  {fail}')
    print(f'   Teraz wejdź w Akces Hub → Produkty → kliknij Regeneruj')
    print('=' * 60)


def daemon():
    """Tryb daemon — sprawdza co 5 minut czy są nowe ASINy do scrapowania"""
    import sys
    print('=' * 60)
    print('AKCES HUB — Remote Scraper DAEMON')
    print(f'Serwer: {PI_URL}')
    print('Sprawdzam co 5 minut...')
    print('Ctrl+C aby zatrzymać')
    print('=' * 60)

    while True:
        try:
            r = requests.get(
                f'{PI_URL}/paletomat/api/scraper/asins-needed',
                headers={'ngrok-skip-browser-warning': '1'},
                timeout=15
            )
            data = r.json()
            asins = data.get('asins', [])[:MAX_PRODUCTS]

            if asins:
                print(f'\n[{time.strftime("%H:%M")}] Znaleziono {len(asins)} nowych ASINów — scrapuję...')
                ok, fail = 0, 0
                for i, asin in enumerate(asins):
                    print(f'  [{i+1}/{len(asins)}] {asin}...', end=' ')
                    result = scrape_amazon(asin)
                    if result and result.get('title'):
                        print(f'OK: {result["title"][:45]}')
                        try:
                            requests.post(
                                f'{PI_URL}/paletomat/api/scraper/update',
                                json={'asin': asin, 'title': result['title'],
                                      'bullet_points': result.get('bullet_points', []),
                                      'all_images': result.get('all_images', []),
                                      'price': result.get('price', 0),
                                      'category': result.get('category', '')},
                                headers={'ngrok-skip-browser-warning': '1', 'Content-Type': 'application/json'},
                                timeout=10
                            )
                            ok += 1
                        except:
                            fail += 1
                    else:
                        print('FAIL')
                        fail += 1
                    time.sleep(SLEEP_BETWEEN)
                print(f'  Gotowe: {ok} OK, {fail} błędów')
            else:
                print(f'[{time.strftime("%H:%M")}] Brak nowych ASINów — czekam 5 min...')

        except Exception as e:
            print(f'[{time.strftime("%H:%M")}] Błąd: {e} — ponawiam za 5 min...')

        time.sleep(300)  # 5 minut


if __name__ == '__main__':
    import sys
    if '--daemon' in sys.argv or '-d' in sys.argv:
        daemon()
    else:
        main()
