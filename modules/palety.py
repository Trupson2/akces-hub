"""
Moduł palet — routes dla /palety/*, /produkt/* (edycja), /produkty/* (meta)
"""
from flask import Blueprint, request, redirect, session, flash, jsonify, Response, current_app, render_template
from datetime import datetime
import os
import json

palety_bp = Blueprint('palety', __name__)


def _get_css():
    from modules.shared import CSS
    return CSS


def _get_gemini_client():
    """Pobiera GEMINI_CLIENT z głównego modułu app"""
    try:
        from app import GEMINI_CLIENT
        return GEMINI_CLIENT
    except ImportError:
        return None


def _get_extract_allegro_params():
    """Pobiera extract_allegro_params z głównego modułu app"""
    from app import extract_allegro_params
    return extract_allegro_params


def _get_auto_kategoryzuj():
    """Pobiera auto_kategoryzuj z shared (unika circular import)"""
    from modules.shared import auto_kategoryzuj
    return auto_kategoryzuj


# ============================================================
# EXTRAKTOR ALLEGRO - REGENERUJ META TITLE
# ============================================================
@palety_bp.route('/produkty/<int:produkt_id>/regenerate-meta-title', methods=['POST', 'OPTIONS'])
def produkt_regenerate_meta_title(produkt_id):
    """Regeneruje meta_title dla pojedynczego produktu"""
    # CORS preflight
    if request.method == 'OPTIONS':
        response = jsonify({'success': True})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response

    from modules.database import get_db, get_config

    try:
        # Sprawdź klucz Gemini z DB config
        gemini_key = get_config('gemini_api_key', '')
        if not gemini_key:
            response = jsonify({'success': False, 'error': 'Brak klucza Gemini API - ustaw w Ustawieniach'})
            response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
            return response

        # Pobierz produkt
        conn = get_db()
        produkt = conn.execute('SELECT nazwa, ean, asin FROM produkty WHERE id = ?', (produkt_id,)).fetchone()

        if not produkt:
            response = jsonify({'success': False, 'error': 'Produkt nie znaleziony'})
            response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
            return response

        # Generuj meta_title
        from modules.smart_importer import generate_meta_title
        meta_title = generate_meta_title(
            produkt_nazwa=produkt['nazwa'] or '',
            produkt_ean=produkt['ean'] or '',
            produkt_asin=produkt['asin'] or ''
        )

        if not meta_title:
            response = jsonify({'success': False, 'error': 'Nie udało się wygenerować tytułu'})
            response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
            return response

        # Zapisz do bazy
        conn.execute('UPDATE produkty SET meta_title = ? WHERE id = ?', (meta_title, produkt_id))
        conn.commit()

        response = jsonify({'success': True, 'meta_title': meta_title})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        return response

    except Exception as e:
        response = jsonify({'success': False, 'error': str(e)})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        return response


# ============================================================
# EXTRAKTOR ALLEGRO - BATCH GENERATION
# ============================================================
@palety_bp.route('/api/generate_meta_title_batch', methods=['POST', 'OPTIONS'])
def generate_meta_title_batch():
    """Generuje meta_title dla wielu produktów naraz"""
    # CORS preflight
    if request.method == 'OPTIONS':
        response = jsonify({'success': True})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response

    import time
    from modules.database import get_db

    try:
        data = request.get_json()
        product_ids = data.get('product_ids', [])

        # BATCH SIZE LIMIT (zwiększony dla paid tier)
        MAX_BATCH_SIZE = 100  # Zwiększone z 10 na 100 dla paid tier
        if len(product_ids) > MAX_BATCH_SIZE:
            response = jsonify({
                'success': False,
                'error': f'Zbyt dużo produktów! Max {MAX_BATCH_SIZE} na raz. Zaznacz mniej produktów lub podziel na mniejsze batche.'
            })
            response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
            return response

        if not product_ids:
            response = jsonify({'success': False, 'error': 'Brak produktów do przetworzenia'})
            response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
            return response

        # Sprawdź API key
        from modules.database import get_config
        gemini_key = get_config('gemini_api_key', '')
        if not gemini_key:
            response = jsonify({'success': False, 'error': 'Brak klucza Gemini API - ustaw w Ustawieniach'})
            response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
            return response

        conn = get_db()
        results = {
            'success': True,
            'total': len(product_ids),
            'generated': 0,
            'failed': 0,
            'details': []
        }

        # Generuj dla każdego produktu
        print(f"\n🚀 [BATCH START] Przetwarzam {len(product_ids)} produktów...")

        for idx, product_id in enumerate(product_ids, 1):
            try:
                print(f"\n📦 [{idx}/{len(product_ids)}] Processing product ID: {product_id}")

                # Pobierz produkt
                produkt = conn.execute('SELECT nazwa, ean, asin FROM produkty WHERE id = ?', (product_id,)).fetchone()

                if not produkt:
                    print(f"   ✗ Produkt nie znaleziony w bazie!")
                    results['failed'] += 1
                    results['details'].append({
                        'id': product_id,
                        'status': 'error',
                        'error': 'Produkt nie znaleziony'
                    })
                    continue

                print(f"   → Nazwa z bazy: {produkt['nazwa'][:50]}...")

                # Generuj meta_title
                from modules.smart_importer import generate_meta_title
                meta_title = generate_meta_title(
                    produkt_nazwa=produkt['nazwa'] or '',
                    produkt_ean=produkt['ean'] or '',
                    produkt_asin=produkt['asin'] or ''
                )

                print(f"   ← Otrzymano meta_title: {meta_title[:75] if meta_title else 'BRAK'}")

                if meta_title:
                    # Zapisz do bazy
                    conn.execute('UPDATE produkty SET meta_title = ? WHERE id = ?', (meta_title, product_id))
                    conn.commit()
                    print(f"   ✓ Zapisano do bazy")

                    results['generated'] += 1
                    results['details'].append({
                        'id': product_id,
                        'status': 'success',
                        'meta_title': meta_title
                    })
                else:
                    print(f"   ✗ Brak meta_title (puste)")
                    results['failed'] += 1
                    results['details'].append({
                        'id': product_id,
                        'status': 'error',
                        'error': 'Nie udało się wygenerować tytułu'
                    })

                # Delay dla rate limiting (WOLNIEJ = STABILNIEJ)
                if idx < len(product_ids):
                    # Start z większym delay dla stabilności
                    if not hasattr(generate_meta_title_batch, '_api_delay'):
                        generate_meta_title_batch._api_delay = 2.0  # 2s = ~30 req/min (BEZPIECZNY!)

                    print(f"   ⏳ Czekam {generate_meta_title_batch._api_delay}s przed następnym...")
                    time.sleep(generate_meta_title_batch._api_delay)

            except Exception as e:
                error_msg = str(e)

                # Sprawdź czy to błąd quota (429)
                if '429' in error_msg or 'quota' in error_msg.lower() or 'exceeded' in error_msg.lower():
                    # AUTO-SLOWDOWN: zwiększ delay
                    if not hasattr(generate_meta_title_batch, '_api_delay'):
                        generate_meta_title_batch._api_delay = 2.0  # Start z 2s

                    old_delay = generate_meta_title_batch._api_delay
                    generate_meta_title_batch._api_delay = min(old_delay * 2, 10.0)  # Max 10s

                    print(f"   ⚠️  QUOTA EXCEEDED! Zwiększam delay: {old_delay}s → {generate_meta_title_batch._api_delay}s")
                    print(f"   💡 WOLNIEJ = STABILNIEJ!")

                    results['failed'] += 1
                    results['details'].append({
                        'id': product_id,
                        'status': 'error',
                        'error': f'⏰ Quota exceeded! Zwiększono delay do {generate_meta_title_batch._api_delay}s. Upgrade do PAID = 2000 RPM (tylko dodaj kartę!)'
                    })
                    # NIE przerywaj - spróbuj dalej z większym delay
                    continue
                else:
                    results['failed'] += 1
                    results['details'].append({
                        'id': product_id,
                        'status': 'error',
                        'error': error_msg
                    })

        response = jsonify(results)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        return response

    except Exception as e:
        response = jsonify({'success': False, 'error': str(e)})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        return response


# ============================================================
# EXTRAKTOR ALLEGRO - UI
# ============================================================
@palety_bp.route('/produkty/<int:produkt_id>/extract-params')
def produkt_extract_params(produkt_id):
    """Strona z parametrami Allegro wygenerowanymi przez AI"""
    from modules.database import get_db
    CSS = _get_css()
    GEMINI_CLIENT = _get_gemini_client()
    extract_allegro_params = _get_extract_allegro_params()

    conn = get_db()
    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()

    if not produkt:
        return redirect('/palety')

    # Sprawdź czy Gemini jest dostępne
    if not GEMINI_CLIENT:
        error_html = CSS + '''
        <div class="container">
            <div class="header">
                <h1>⚠️ Extraktor Allegro</h1>
                <small>Gemini AI niedostępne</small>
            </div>
            <div style="background:#1e1e2e;padding:20px;border-radius:12px;margin-bottom:20px">
                <p style="color:#ef4444">Aby użyć Extraktora Allegro, ustaw GEMINI_API_KEY w gemini_config.py:</p>
                <code style="background:#0a0a0f;padding:10px;display:block;margin-top:10px;color:#22c55e">
                GEMINI_API_KEY = 'twoj_klucz_api'
                </code>
                <p style="margin-top:15px;color:#64748b;font-size:0.9rem">
                Klucz API możesz uzyskać na: <a href="https://aistudio.google.com/apikey" target="_blank" style="color:#3b82f6">Google AI Studio</a>
                </p>
            </div>
            <a href="/palety" class="back">&larr; Powrót do palet</a>
        </div>
        '''
        return error_html

    # Generuj parametry
    # Dociągnij bullet_points ze scraped żeby tytuł był lepszy
    bullet_points = []
    if produkt.get('asin'):
        import json as _json
        scraped_row = conn.execute('SELECT bullet_points FROM scraped WHERE asin = ?', (produkt['asin'],)).fetchone()
        if scraped_row and scraped_row['bullet_points']:
            try:
                bullet_points = _json.loads(scraped_row['bullet_points'])
            except:
                pass

    result = extract_allegro_params(
        produkt_nazwa=produkt['nazwa'] or '',
        produkt_ean=produkt['ean'] or '',
        produkt_asin=produkt['asin'] or '',
        bullet_points=bullet_points
    )

    # Sprawdź błędy
    if 'error' in result and result['error']:
        error_html = CSS + f'''
        <div class="container">
            <div class="header">
                <h1>❌ Błąd Extraktora</h1>
                <small>Produkt #{produkt_id}</small>
            </div>
            <div style="background:#1e1e2e;padding:20px;border-radius:12px;margin-bottom:20px">
                <p style="color:#ef4444">{result['error']}</p>
            </div>
            <a href="javascript:history.back()" class="back">&larr; Powrót</a>
        </div>
        '''
        return error_html

    meta_title = result.get('meta_title', '')
    params = result.get('params', {})

    # Buduj tabelkę parametrów
    params_html = ''
    for key, value in params.items():
        params_html += f'''
        <tr style="border-bottom:1px solid #2a2a3a">
            <td style="padding:12px;color:#64748b;font-weight:600">{key}</td>
            <td style="padding:12px;color:#fff">{value}</td>
        </tr>
        '''

    if not params_html:
        params_html = '<tr><td colspan="2" style="padding:20px;text-align:center;color:#64748b">Brak parametrów</td></tr>'

    # Strona wyników
    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>🤖 Extraktor Allegro</h1>
            <small>Produkt #{produkt_id}</small>
        </div>

        <!-- META TITLE -->
        <div style="background:linear-gradient(135deg,rgba(34,197,94,0.15),rgba(16,185,129,0.1));border:2px solid rgba(34,197,94,0.5);border-radius:12px;padding:20px;margin-bottom:20px">
            <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin-bottom:8px">📝 META TYTUŁ ALLEGRO (Skopiuj poniżej)</div>
            <div style="background:#1e1e2e;padding:15px;border-radius:8px;font-size:1.1rem;font-weight:600;color:#22c55e;cursor:pointer"
                 onclick="navigator.clipboard.writeText(this.innerText); alert('Skopiowano do schowka!')">
                {meta_title or 'Brak tytułu'}
            </div>
            <div style="font-size:0.7rem;color:#64748b;margin-top:8px">💡 Kliknij aby skopiować</div>
        </div>

        <!-- ORYGINALNA NAZWA -->
        <div style="background:#1e1e2e;padding:15px;border-radius:12px;margin-bottom:20px">
            <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin-bottom:8px">📦 ORYGINALNA NAZWA</div>
            <div style="color:#fff;font-size:0.9rem">{produkt['nazwa'] or 'Brak nazwy'}</div>
        </div>

        <!-- PARAMETRY TECHNICZNE -->
        <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin-bottom:10px">⚙️ PARAMETRY TECHNICZNE</div>
        <div style="background:#1e1e2e;border-radius:12px;overflow:hidden;margin-bottom:20px">
            <table style="width:100%;border-collapse:collapse">
                {params_html}
            </table>
        </div>

        <!-- PRZYCISKI -->
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:20px">
            <form action="/produkty/{produkt_id}/quick-draft" method="POST" style="margin:0">
                <input type="hidden" name="meta_title" value="{meta_title}">
                <button type="submit" style="width:100%;padding:12px;background:linear-gradient(135deg,#22c55e,#16a34a);border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">
                    🚀 Wystaw szkic
                </button>
            </form>
            <button onclick="window.print()" style="padding:12px;background:#3b82f6;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">
                🖨️ Drukuj
            </button>
            <button onclick="window.location.reload()" style="padding:12px;background:#8b5cf6;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">
                🔄 Regeneruj
            </button>
        </div>

        <a href="javascript:history.back()" class="back">&larr; Powrót</a>
    </div>
    '''

    return html


@palety_bp.route('/produkty/<int:produkt_id>/quick-draft', methods=['POST'])
def produkt_quick_draft(produkt_id):
    """Szybkie wystawienie szkicu na Allegro z wygenerowanym META_TITLE"""
    from modules.database import get_db
    from modules.allegro_api import create_offer, is_authenticated, upload_image_to_allegro
    import re
    CSS = _get_css()

    meta_title = request.form.get('meta_title', '').strip()[:75]

    if not meta_title:
        return redirect(f'/produkty/{produkt_id}/extract-params?error=no_title')

    # Sprawdź autoryzację Allegro
    if not is_authenticated():
        error_html = CSS + f'''
        <div class="container">
            <div class="header">
                <h1>❌ Błąd Allegro</h1>
                <small>Produkt #{produkt_id}</small>
            </div>
            <div style="background:#1e1e2e;padding:20px;border-radius:12px;margin-bottom:20px">
                <p style="color:#ef4444;font-weight:600">Nie jesteś zalogowany do Allegro!</p>
                <p style="margin-top:10px;color:#64748b">Musisz najpierw połączyć konto Allegro w ustawieniach.</p>
                <a href="/allegro/auth" style="display:inline-block;margin-top:15px;padding:12px 24px;background:#22c55e;border-radius:8px;color:#fff;text-decoration:none;font-weight:600">
                    Połącz Allegro
                </a>
            </div>
            <a href="javascript:history.back()" class="back">&larr; Powrót</a>
        </div>
        '''
        return error_html

    # Pobierz dane produktu
    conn = get_db()
    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()

    if not produkt:
        return redirect('/palety')

    # Przygotuj dane oferty
    # Preferuj tytul_seo z scraped (wygenerowany przez AI z bullet points) nad meta_title z formularza
    tytul = meta_title
    if produkt.get('asin'):
        import json as _json
        scraped_seo = conn.execute('SELECT tytul_seo FROM scraped WHERE asin = ?', (produkt['asin'],)).fetchone()
        if scraped_seo and scraped_seo['tytul_seo'] and len(scraped_seo['tytul_seo']) > 10:
            tytul = scraped_seo['tytul_seo'][:75]
            print(f"   🎯 Używam tytul_seo ze scraped: {tytul}")
    cena = produkt['cena_allegro'] or 100.0
    ilosc = produkt['ilosc'] or 1
    ean = produkt['ean'] or None
    kategoria = produkt['kategoria'] or ''

    # Generuj prosty opis (albo użyj istniejącego)
    opis = produkt['opis_ai'] if produkt['opis_ai'] else f'''
    <p><strong>{produkt['nazwa']}</strong></p>
    <p>Stan: {produkt['stan'] or 'Używany'}</p>
    <p>Ilość: {ilosc} szt.</p>
    '''

    # Pobierz zdjęcia z kolumny images (lokalne ścieżki) lub fallback na scraped/zdjecie_url
    zdjecia = []

    # Sposób 1: Pobierz z produkty.images (lokalne ścieżki)
    if produkt.get('images'):
        try:
            import json
            images_data = produkt['images']
            if isinstance(images_data, str):
                zdjecia = json.loads(images_data) if images_data and images_data != '[]' else []
            elif isinstance(images_data, list):
                zdjecia = images_data
            if zdjecia:
                print(f"   📸 [SOURCE] produkty.images: {len(zdjecia)} plików")
        except Exception as e:
            print(f"   ⚠️  [ERROR] Parse images: {e}")

    # Sposób 2: FALLBACK na scraped.wszystkie_zdjecia (lokalne ścieżki przez ASIN)
    if not zdjecia and produkt.get('asin'):
        try:
            import json
            scraped = conn.execute('SELECT wszystkie_zdjecia FROM scraped WHERE asin = ?', (produkt['asin'],)).fetchone()
            if scraped and scraped['wszystkie_zdjecia']:
                try:
                    scraped_images = json.loads(scraped['wszystkie_zdjecia'])
                    if scraped_images and len(scraped_images) > 0:
                        zdjecia = scraped_images
                        print(f"   📸 [SOURCE] scraped.wszystkie_zdjecia: {len(zdjecia)} plików")
                except:
                    pass
        except Exception as e:
            print(f"   ⚠️  [ERROR] Read scraped: {e}")

    # Sposób 3: Fallback na zdjecie_url
    if not zdjecia and produkt['zdjecie_url']:
        img_url = produkt['zdjecie_url']
        if 'media-amazon.com' in img_url:
            img_url = re.sub(r'\._[A-Z0-9_,]+_\.', '._AC_SL1500_.', img_url)
        zdjecia = [img_url]
        print(f"   📸 [SOURCE] produkty.zdjecie_url: 1 URL")

    # Zamknij połączenie dopiero teraz

    print(f"   📸 [TOTAL] {len(zdjecia)} zdjęć do uploadu")

    # Upload zdjęć do Allegro (LOKALNE PLIKI lub URL)
    uploaded_urls = []
    print(f"   📤 Uploaduję {len(zdjecia[:8])} zdjęć do Allegro...")
    for idx, path_or_url in enumerate(zdjecia[:8], 1):
        try:
            # Sprawdź czy to lokalny plik czy URL
            if isinstance(path_or_url, str) and not path_or_url.startswith('http'):
                print(f"      [{idx}/{min(len(zdjecia), 8)}] Local file: {path_or_url}")
            else:
                print(f"      [{idx}/{min(len(zdjecia), 8)}] URL: {path_or_url[:60]}...")

            allegro_url = upload_image_to_allegro(path_or_url)
            if allegro_url:
                uploaded_urls.append(allegro_url)
                print(f"      ✓ [{idx}/{min(len(zdjecia), 8)}] Success!")
            else:
                print(f"      ✗ [{idx}/{min(len(zdjecia), 8)}] Failed")
        except Exception as e:
            print(f"      ✗ [{idx}/{min(len(zdjecia), 8)}] Error: {str(e)[:80]}")

    print(f"   ✅ Uploaded {len(uploaded_urls)}/{min(len(zdjecia), 8)} zdjęć")

    # Utwórz ofertę jako szkic
    try:
        offer_data = {
            'name': tytul,
            'category': {'id': kategoria} if kategoria else None,
            'sellingMode': {
                'price': {
                    'amount': str(cena),
                    'currency': 'PLN'
                }
            },
            'stock': {
                'available': ilosc
            },
            'description': {
                'sections': [
                    {
                        'items': [
                            {
                                'type': 'TEXT',
                                'content': opis
                            }
                        ]
                    }
                ]
            },
            'images': [{'url': url} for url in uploaded_urls] if uploaded_urls else [],
            'publication': {
                'status': 'INACTIVE'  # Szkic
            }
        }

        # Dodaj EAN jeśli jest
        if ean:
            offer_data['ean'] = [ean]

        # Wywołaj Allegro API
        result = create_offer(offer_data)

        if result and 'id' in result:
            offer_id = result['id']

            # Zaktualizuj status w bazie
            conn = get_db()
            conn.execute('''
                UPDATE produkty
                SET status = 'szkic',
                    krotki_tytul = ?
                WHERE id = ?
            ''', (tytul, produkt_id))
            conn.commit()

            # Sukces!
            success_html = CSS + f'''
            <div class="container">
                <div class="header">
                    <h1>✅ Szkic utworzony!</h1>
                    <small>Produkt #{produkt_id}</small>
                </div>

                <div style="background:linear-gradient(135deg,rgba(34,197,94,0.15),rgba(16,185,129,0.1));border:2px solid rgba(34,197,94,0.5);border-radius:12px;padding:20px;margin-bottom:20px">
                    <div style="font-size:1.2rem;font-weight:600;color:#22c55e;margin-bottom:10px">🎉 Oferta na Allegro!</div>
                    <div style="color:#fff;margin-bottom:15px">
                        <strong>Tytuł:</strong> {tytul}<br>
                        <strong>Cena:</strong> {cena:.2f} PLN<br>
                        <strong>ID Allegro:</strong> {offer_id}
                    </div>
                    <a href="https://allegro.pl/moje-allegro/sprzedaz/drafted/{offer_id}" target="_blank"
                       style="display:inline-block;padding:12px 24px;background:#22c55e;border-radius:8px;color:#fff;text-decoration:none;font-weight:600">
                        📝 Zobacz szkic na Allegro
                    </a>
                </div>

                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px">
                    <a href="/produkty/{produkt_id}/extract-params" style="display:block;padding:12px;background:#3b82f6;border-radius:8px;color:#fff;text-decoration:none;text-align:center;font-weight:600">
                        🔄 Wygeneruj ponownie
                    </a>
                    <a href="javascript:history.back()" style="display:block;padding:12px;background:#64748b;border-radius:8px;color:#fff;text-decoration:none;text-align:center;font-weight:600">
                        ← Powrót
                    </a>
                </div>
            </div>
            '''
            return success_html
        else:
            raise Exception("Nie otrzymano ID oferty z Allegro")

    except Exception as e:
        # Błąd
        error_html = CSS + f'''
        <div class="container">
            <div class="header">
                <h1>❌ Błąd wystawiania</h1>
                <small>Produkt #{produkt_id}</small>
            </div>
            <div style="background:#1e1e2e;padding:20px;border-radius:12px;margin-bottom:20px">
                <p style="color:#ef4444;font-weight:600">Nie udało się wystawić szkicu:</p>
                <p style="margin-top:10px;color:#64748b">{str(e)}</p>
            </div>
            <a href="javascript:history.back()" class="back">&larr; Powrót</a>
        </div>
        '''
        return error_html


@palety_bp.route('/palety/<int:paleta_id>/edit', methods=['GET', 'POST'])
def paleta_edit(paleta_id):
    """Edycja palety - formularz"""
    from modules.database import get_db
    CSS = _get_css()

    conn = get_db()

    if request.method == 'POST':
        # Pobierz dane z formularza
        nazwa = request.form.get('nazwa', '').strip()
        dostawca = request.form.get('dostawca', '').strip()
        regal = request.form.get('regal', '').strip()
        cena_zakupu = float(request.form.get('cena_zakupu', 0))
        # cena_zakupu = brutto
        cena_zakupu_netto = round(cena_zakupu / 1.23, 2) if cena_zakupu > 0 else 0
        data_zakupu = request.form.get('data_zakupu', '')
        notatki = request.form.get('notatki', '').strip()
        koszt_jedn = float(request.form.get('koszt_jednostkowy', 0) or 0)

        # Zaktualizuj paletę
        conn.execute('''
            UPDATE palety
            SET nazwa = ?, dostawca = ?, cena_zakupu = ?, cena_zakupu_netto = ?, data_zakupu = ?, notatki = ?, regal = ?, koszt_jednostkowy = ?
            WHERE id = ?
        ''', (nazwa, dostawca, cena_zakupu, cena_zakupu_netto, data_zakupu, notatki, regal, koszt_jedn, paleta_id))
        conn.commit()

        return redirect(f'/palety/{paleta_id}?success=updated')

    # GET - wyświetl formularz
    paleta = conn.execute('SELECT * FROM palety WHERE id = ?', (paleta_id,)).fetchone()

    if not paleta:
        return redirect('/palety')

    # Buduj formularz
    html = CSS
    html += '<div class="container">'
    html += '<div class="header"><h1>&#x270F; Edytuj Palete</h1><small>ID: ' + str(paleta_id) + '</small></div>'

    html += '<form method="POST" style="background:#1e1e2e;padding:20px;border-radius:12px;margin-bottom:20px">'

    # Nazwa
    html += '<div style="margin-bottom:15px">'
    html += '<label style="display:block;color:#64748b;font-size:0.85rem;margin-bottom:5px">Nazwa palety</label>'
    html += '<input type="text" name="nazwa" value="' + (paleta['nazwa'] or '') + '" '
    html += 'style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">'
    html += '</div>'

    # Dostawca
    html += '<div style="margin-bottom:15px">'
    html += '<label style="display:block;color:#64748b;font-size:0.85rem;margin-bottom:5px">Dostawca</label>'
    html += '<select name="dostawca" style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">'

    dostawcy = ['', 'Warrington', 'Miglo', 'Jobalots', 'Inny']
    for d in dostawcy:
        selected = ' selected' if d == paleta['dostawca'] else ''
        html += f'<option value="{d}"{selected}>{d or "— Wybierz —"}</option>'

    html += '</select>'
    html += '</div>'

    # Regal / Lokalizacja
    html += '<div style="margin-bottom:15px">'
    html += '<label style="display:block;color:#64748b;font-size:0.85rem;margin-bottom:5px">📍 Regal / Lokalizacja</label>'
    try:
        regal_value = paleta['regal'] or ''
    except (KeyError, TypeError):
        regal_value = ''
    html += '<input type="text" name="regal" value="' + regal_value + '" placeholder="np. Migło, Regał A1" '
    html += 'style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">'
    html += '</div>'

    # Cena zakupu
    html += '<div style="margin-bottom:15px">'
    html += '<label style="display:block;color:#64748b;font-size:0.85rem;margin-bottom:5px">Cena zakupu (PLN brutto)</label>'
    html += '<input type="number" name="cena_zakupu" value="' + str(paleta['cena_zakupu'] or 0) + '" step="0.01" '
    html += 'style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">'
    html += '</div>'

    # Koszt jednostkowy (netto/szt) - STAŁY
    _kj_val = 0
    try:
        _kj_val = float(paleta['koszt_jednostkowy'] or 0)
    except:
        pass
    html += '<div style="margin-bottom:15px">'
    html += '<label style="display:block;color:#64748b;font-size:0.85rem;margin-bottom:5px">Koszt jednostkowy (netto/szt) - staly</label>'
    html += '<input type="number" name="koszt_jednostkowy" value="' + str(_kj_val) + '" step="0.01" min="0" '
    html += 'style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #f59e0b;border-radius:8px;color:#fff;font-size:1rem" '
    html += 'placeholder="Zostaw 0 dla auto-obliczenia z ceny palety">'
    html += '<div style="font-size:0.7rem;color:#64748b;margin-top:4px">Cena netto za 1 sztuke. Zostaw 0 = auto z ceny palety / ilosc sztuk</div>'
    html += '</div>'

    # Data zakupu
    html += '<div style="margin-bottom:15px">'
    html += '<label style="display:block;color:#64748b;font-size:0.85rem;margin-bottom:5px">Data zakupu</label>'
    html += '<input type="date" name="data_zakupu" value="' + (paleta['data_zakupu'] or '') + '" '
    html += 'style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">'
    html += '</div>'

    # Notatki
    html += '<div style="margin-bottom:15px">'
    html += '<label style="display:block;color:#64748b;font-size:0.85rem;margin-bottom:5px">Notatki</label>'
    html += '<textarea name="notatki" rows="4" '
    html += 'style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">'
    html += (paleta['notatki'] or '')
    html += '</textarea>'
    html += '</div>'

    # Przyciski
    html += '<div style="display:flex;gap:10px;margin-top:20px">'
    html += '<button type="submit" style="flex:1;padding:12px;background:#22c55e;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">Zapisz zmiany</button>'
    html += '<a href="/palety/' + str(paleta_id) + '" style="flex:1;padding:12px;background:#ef4444;border:none;border-radius:8px;color:#fff;font-weight:600;text-align:center;text-decoration:none;display:block">Anuluj</a>'
    html += '</div>'

    html += '</form>'

    html += '<a href="/palety/' + str(paleta_id) + '" class="back">&larr; Powrot do palety</a>'
    html += '</div>'

    return html


# ============================================================
# ZARZĄDZANIE PALETAMI
# ============================================================
@palety_bp.route('/palety/napraw-ceny')
def napraw_ceny_palet():
    """Uzupełnia brakujące ceny zakupu w paletach (cena_zakupu = 0) - zapisuje netto i brutto"""
    from modules.database import get_db

    conn = get_db()

    # Sprawdź czy kolumna cena_zakupu_netto istnieje
    kolumny = [desc[0] for desc in conn.execute('PRAGMA table_info(palety)').fetchall()]
    ma_kolumne_netto = 'cena_zakupu_netto' in kolumny

    # Jeśli nie ma - dodaj ją
    if not ma_kolumne_netto:
        try:
            conn.execute('ALTER TABLE palety ADD COLUMN cena_zakupu_netto REAL DEFAULT 0')
            conn.commit()
            ma_kolumne_netto = True
            print("✅ Dodano kolumnę cena_zakupu_netto")
        except:
            pass

    # Pobierz palety z cena_zakupu = 0
    palety = conn.execute('''
        SELECT id, nazwa FROM palety WHERE cena_zakupu IS NULL OR cena_zakupu = 0
    ''').fetchall()

    updated = 0

    for p in palety:
        # Oblicz sumę cen brutto produktów (cena_brutto to ŁĄCZNA cena za produkt, nie za sztukę)
        suma_brutto = conn.execute('''
            SELECT COALESCE(SUM(cena_brutto), 0) FROM produkty WHERE paleta_id = ?
        ''', (p['id'],)).fetchone()[0]
        suma_netto = round(suma_brutto / 1.23, 2) if suma_brutto > 0 else 0

        if suma_brutto > 0:
            # cena_zakupu = BRUTTO
            if ma_kolumne_netto:
                conn.execute('''
                    UPDATE palety SET cena_zakupu = ?, cena_zakupu_netto = ? WHERE id = ?
                ''', (suma_brutto, suma_netto, p['id']))
            else:
                conn.execute('UPDATE palety SET cena_zakupu = ? WHERE id = ?', (suma_brutto, p['id']))
            updated += 1
            print(f"✅ Naprawiono paletę {p['id']}: {p['nazwa']} -> {suma_netto:.0f} netto | {suma_brutto:.0f} brutto")

    conn.commit()

    return f'''
    <html><head><meta http-equiv="refresh" content="2;url=/palety"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px">✅</div>
            <div style="font-size:1.2rem">Naprawiono {updated} palet!</div>
            <div style="color:#64748b;margin-top:10px">Ceny zakupu (netto + brutto) zostały uzupełnione</div>
        </div>
    </body></html>
    '''


@palety_bp.route('/palety/przelicz-brutto')
def przelicz_brutto_palet():
    """Przelicza WSZYSTKIE palety - cena_zakupu = suma netto produktów * 1.23"""
    from modules.database import get_db

    conn = get_db()

    # Pobierz WSZYSTKIE palety
    palety = conn.execute('SELECT id, nazwa, cena_zakupu FROM palety').fetchall()

    updated = 0

    for p in palety:
        # Oblicz sumę cen brutto produktów (cena_brutto = ŁĄCZNA za produkt)
        suma_brutto = conn.execute('''
            SELECT COALESCE(SUM(cena_brutto), 0) FROM produkty WHERE paleta_id = ?
        ''', (p['id'],)).fetchone()[0]
        suma_netto = round(suma_brutto / 1.23, 2) if suma_brutto > 0 else 0

        if suma_brutto > 0:
            stara_cena = p['cena_zakupu'] or 0
            conn.execute('UPDATE palety SET cena_zakupu = ?, cena_zakupu_netto = ? WHERE id = ?', (suma_brutto, suma_netto, p['id']))
            updated += 1
            print(f"✅ Paleta {p['id']}: {p['nazwa']} -> {stara_cena:.0f} → {suma_brutto:.0f} zł brutto")

    conn.commit()

    return f'''
    <html><head><meta http-equiv="refresh" content="2;url=/palety"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px">✅</div>
            <div style="font-size:1.2rem">Przeliczono {updated} palet!</div>
            <div style="color:#64748b;margin-top:10px">Wszystkie ceny zakupu = suma netto × 1.23 (brutto)</div>
        </div>
    </body></html>
    '''


@palety_bp.route('/palety')
def palety_lista():
    from modules.database import get_palety_list, get_full_stats
    CSS = _get_css()

    palety = get_palety_list(100)
    stats = get_full_stats()

    palety_html = ''
    for p in palety:
        data = p['data_zakupu'] if p['data_zakupu'] else 'Brak daty'
        # Bezpieczne pobieranie wartości - sqlite3.Row nie ma .get()
        try:
            wartosc_zakupu_prod = p['wartosc_zakupu_produktow'] or 0
        except (KeyError, TypeError):
            wartosc_zakupu_prod = 0

        # Bezpieczne pobieranie regalu
        try:
            regal = p['regal'] if p['regal'] else ''
        except (KeyError, TypeError):
            regal = ''

        # Statystyki sprzedaży
        try:
            sztuk_w_magazynie = p['sztuk_w_magazynie'] or 0
            sprzedano_status = p['sprzedano_status'] or 0
            sprzedano_tabela = p['sprzedano_tabela'] or 0
            try:
                sprzedano_offline = p['sprzedano_offline'] or 0  # sprzedane poza Allegro
            except:
                sprzedano_offline = 0
            try:
                przychod_offline = p['przychod_offline'] or 0  # przychód ze sprzedaży offline
            except:
                przychod_offline = 0
            sprzedano_wartosc_status = p['sprzedano_wartosc_status'] or 0
            sprzedano_wartosc_tabela = p['sprzedano_wartosc_tabela'] or 0
            # ZMIANA: Użyj ceny liczonej z produktów zamiast z tabeli palety
            koszt_palety = p['cena_zakupu'] or wartosc_zakupu_prod  # Cena z palety, fallback na produkty

            # FIX: sprzedano_tabela JUŻ ZAWIERA offline (kupujacy='offline')
            # więc NIE dodajemy sprzedano_offline/przychod_offline osobno!
            if sprzedano_tabela > 0:
                sprzedano_szt = sprzedano_tabela
                sprzedano_wartosc = sprzedano_wartosc_tabela
            else:
                sprzedano_szt = sprzedano_status + sprzedano_offline
                sprzedano_wartosc = sprzedano_wartosc_status + przychod_offline
            # FIX: użyj MAX z dwóch źródeł:
            # 1) sztuk_w_magazynie + sprzedano_szt (dla produktów z ilosc=0 po sprzedaży)
            # 2) SUM(ilosc) z produktów (dla produktów z zachowanym oryginalnym ilosc)
            # Np. Plecaki: ilosc=42 ale sprzedano 17 → max(0+17, 42) = 42
            # Np. Bieżnie: ilosc=0 (sprzedany) → max(0+13, 0) = 13
            stary_lacznie = sztuk_w_magazynie + sprzedano_szt
            try:
                ilosc_total = p['sztuk_lacznie_total'] or 0
            except (KeyError, TypeError):
                ilosc_total = 0
            sztuk_lacznie = max(stary_lacznie, ilosc_total)
            # Przelicz sztuk_w_magazynie na resztę
            sztuk_w_magazynie = max(0, sztuk_lacznie - sprzedano_szt)
        except (KeyError, TypeError):
            sztuk_lacznie = 0
            sprzedano_szt = 0
            sprzedano_wartosc = 0
            koszt_palety = 0

        # Oblicz koszt sprzedanych na podstawie średniej ceny za sztukę
        if sztuk_lacznie > 0 and koszt_palety > 0:
            srednia_cena_szt = koszt_palety / sztuk_lacznie
            sprzedano_koszt = sprzedano_szt * srednia_cena_szt
        else:
            sprzedano_koszt = 0

        # Oblicz zysk netto (przychód - koszt)
        zysk_netto = sprzedano_wartosc - sprzedano_koszt

        # Pasek postępu sprzedaży
        procent_sprzedane = (sprzedano_szt / sztuk_lacznie * 100) if sztuk_lacznie > 0 else 0
        progress_color = '#22c55e' if procent_sprzedane >= 50 else '#eab308' if procent_sprzedane >= 20 else '#64748b'

        palety_html += f'''
        <div style="background:#1e1e2e;border-radius:12px;padding:12px;margin-bottom:10px">
            <div style="display:flex;justify-content:space-between;align-items:start">
                <div>
                    <div style="font-weight:600">{p['nazwa'] or f"Paleta #{p['id']}"}</div>
                    <div style="font-size:0.8rem;color:#64748b">{p['dostawca']} • {data}</div>
                    {f'<div style="font-size:0.75rem;color:#8b5cf6;margin-top:2px">📍 Regal: {regal}</div>' if regal else ''}
                </div>
                <div style="text-align:right">
                    <div style="font-weight:600;color:#ef4444">{koszt_palety:.0f} zł</div>
                    <div style="font-size:0.75rem;color:#64748b">{p['produktow']} prod.</div>
                </div>
            </div>

            <!-- PASEK SPRZEDAŻY -->
            <div style="margin-top:10px;background:#0a0a0f;border-radius:6px;padding:8px">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                    <span style="font-size:0.75rem;color:#94a3b8">📊 Sprzedano:</span>
                    <span style="font-size:0.85rem;font-weight:700;color:{progress_color}">{sprzedano_szt} / {sztuk_lacznie} szt</span>
                </div>
                <div style="background:#1e1e2e;border-radius:4px;height:8px;overflow:hidden">
                    <div style="background:{progress_color};width:{procent_sprzedane:.0f}%;height:100%;border-radius:4px;transition:width 0.3s"></div>
                </div>
                {f'<div style="display:flex;justify-content:space-between;margin-top:6px;font-size:0.7rem"><span style="color:#22c55e">💰 Zysk: {zysk_netto:+.0f} zł</span><span style="color:#64748b">({procent_sprzedane:.0f}%)</span></div>' if sprzedano_szt > 0 else ''}
            </div>

            <div style="margin-top:8px;display:flex;justify-content:space-between;font-size:0.75rem">
                <span style="color:#ef4444">💰 Zakup: {koszt_palety:.0f} zł</span>
                <span style="color:#22c55e">Detal: {p['wartosc_detalu']:.0f} zł</span>
            </div>
            <a href="/palety/{p['id']}" style="display:block;text-align:center;color:#3b82f6;margin-top:8px;font-size:0.8rem">Szczegóły →</a>
        </div>
        '''

    if not palety:
        palety_html = '<div style="text-align:center;color:#64748b;padding:30px">Brak palet. Dodaj pierwszą!</div>'

    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>📦 PALETY</h1>
            <small>Zarządzaj zakupami</small>
        </div>

        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:15px">
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#3b82f6">{stats['palety_lacznie']}</div>
                <div style="font-size:0.7rem;color:#64748b">ŁĄCZNIE</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#3b82f6">{stats['palety_miesiac']}</div>
                <div style="font-size:0.7rem;color:#64748b">TEN MSC</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#ef4444">{stats['palety_lacznie_koszt']:.0f}</div>
                <div style="font-size:0.7rem;color:#64748b">WYDANE ZŁ</div>
            </div>
        </div>

        <a href="/palety/dodaj" class="btn" style="display:block;width:100%;padding:14px;background:#22c55e;border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600;margin-bottom:10px">➕ DODAJ PALETĘ</a>

        <a href="/palety/przelicz-brutto" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-bottom:15px;font-size:0.8rem" onclick="return confirm('Przeliczyć ceny zakupu wszystkich palet na brutto (netto × 1.23)?')">🔧 Przelicz ceny na brutto (+23% VAT)</a>

        <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin-bottom:10px">OSTATNIE PALETY</div>

        {palety_html}

        <a href="/statystyki" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:15px">← Statystyki</a>
    </div>
    '''
    return html


@palety_bp.route('/palety/dodaj', methods=['GET', 'POST'])
def paleta_dodaj():
    from modules.database import add_paleta
    CSS = _get_css()

    if request.method == 'POST':
        nazwa = request.form.get('nazwa', '')
        dostawca = request.form.get('dostawca', 'Jobalots')  # Domyślnie Jobalots
        regal = request.form.get('regal', '')

        # Bezpieczna konwersja ceny
        cena_str = request.form.get('cena', '0')
        try:
            cena = float(cena_str) if cena_str else 0
        except:
            cena = 0

        data = request.form.get('data', '')
        notatki = request.form.get('notatki', '')

        # Debug log
        print(f"📦 Dodaję paletę: nazwa={nazwa}, dostawca={dostawca}, cena={cena}")

        paleta_id = add_paleta(nazwa, dostawca, cena, data, notatki, regal)

        print(f"✅ Utworzono paletę ID: {paleta_id}")

        return redirect(f'/palety/{paleta_id}')

    html = CSS + '''
    <div class="container">
        <div class="header">
            <h1>➕ NOWA PALETA</h1>
            <small>Dodaj zakupioną paletę</small>
        </div>

        <!-- IMPORT Z EXCEL -->
        <a href="/palety/import-xlsx" style="display:block;background:linear-gradient(135deg,#22c55e,#16a34a);border-radius:12px;padding:16px;margin-bottom:10px;text-decoration:none;color:#fff">
            <div style="display:flex;align-items:center;gap:12px">
                <div style="font-size:2rem">📊</div>
                <div>
                    <div style="font-weight:600;font-size:1.1rem">IMPORT Z EXCEL</div>
                    <div style="font-size:0.8rem;opacity:0.9">Wrzuć plik XLSX z listą produktów</div>
                </div>
                <div style="margin-left:auto;font-size:1.5rem">→</div>
            </div>
        </a>

        <a href="/palety/bulk-import" style="display:block;background:linear-gradient(135deg,#3b82f6,#2563eb);border-radius:12px;padding:14px;margin-bottom:10px;text-decoration:none;color:#fff">
            <div style="display:flex;align-items:center;gap:12px">
                <div style="font-size:2rem">📦</div>
                <div>
                    <div style="font-weight:600;font-size:1rem">BULK IMPORT (wiele palet)</div>
                    <div style="font-size:0.75rem;opacity:0.9">Importuj kilka palet naraz z osobnymi plikami</div>
                </div>
                <div style="margin-left:auto;font-size:1.5rem">→</div>
            </div>
        </a>

        <div style="text-align:center;color:#64748b;font-size:0.8rem;margin-bottom:15px">— lub dodaj ręcznie —</div>

        <form method="POST" style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:18px">
            <div style="margin-bottom:12px">
                <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Nazwa / Opis</label>
                <input type="text" name="nazwa" placeholder="np. Mix elektronika #15" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">
            </div>

            <div style="margin-bottom:12px">
                <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Dostawca</label>
                <select name="dostawca" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">
                    <option value="Jobalots">Jobalots</option>
                    <option value="Warrington">Warrington</option>
                    <option value="Miglo">Miglo</option>
                    <option value="Inny">Inny</option>
                </select>
            </div>

            <div style="margin-bottom:12px">
                <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">📍 Regal / Lokalizacja</label>
                <input type="text" name="regal" placeholder="np. Migło, Regał A1, itp." style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Cena zakupu brutto (zł)</label>
                    <input type="number" name="cena" placeholder="2500" step="0.01" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Data zakupu</label>
                    <input type="date" name="data" value="''' + datetime.now().strftime('%Y-%m-%d') + '''" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">
                </div>
            </div>

            <div style="margin-bottom:15px">
                <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Notatki</label>
                <textarea name="notatki" rows="2" placeholder="Opcjonalne uwagi..." style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem;resize:vertical"></textarea>
            </div>

            <button type="submit" style="width:100%;padding:14px;background:#22c55e;border:none;border-radius:10px;color:#fff;font-weight:600;font-size:1rem;cursor:pointer">💾 ZAPISZ PALETĘ</button>
        </form>

        <a href="/palety" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:15px">← Anuluj</a>
    </div>
    '''
    return html


@palety_bp.route('/palety/import-xlsx', methods=['GET', 'POST'])
def paleta_import_xlsx():
    """Import palety z pliku Excel"""
    import pandas as pd
    from modules.database import get_db, add_paleta
    CSS = _get_css()
    auto_kategoryzuj = _get_auto_kategoryzuj()

    if request.method == 'POST':
        # Obsługa uploadu pliku
        if 'file' not in request.files:
            return redirect('/palety/import-xlsx?error=no_file')

        file = request.files['file']
        if file.filename == '':
            return redirect('/palety/import-xlsx?error=no_file')

        if not file.filename.endswith(('.xlsx', '.xls')):
            return redirect('/palety/import-xlsx?error=wrong_format')

        try:
            # Wczytaj Excel
            df = pd.read_excel(file)

            # Pobierz dane palety z formularza
            nazwa = request.form.get('nazwa', file.filename)
            dostawca = request.form.get('dostawca', 'Jobalots')
            regal = request.form.get('regal', '')
            cena_zakupu = float(request.form.get('cena', 0) or 0)
            data_zakupu = request.form.get('data', datetime.now().strftime('%Y-%m-%d'))

            # Mapowanie kolumn (elastyczne)
            col_nazwa = request.form.get('col_nazwa', '')
            col_ean = request.form.get('col_ean', '')
            col_ilosc = request.form.get('col_ilosc', '')
            col_cena = request.form.get('col_cena', '')
            col_cena_detal = request.form.get('col_cena_detal', '')

            # Utwórz paletę
            paleta_id = add_paleta(nazwa, dostawca, cena_zakupu, data_zakupu, f'Import z: {file.filename}', regal)

            # Dodaj produkty
            conn = get_db()
            produkty_dodane = 0

            for idx, row in df.iterrows():
                try:
                    # Pobierz wartości z wybranych kolumn
                    prod_nazwa = str(row[col_nazwa]) if col_nazwa and col_nazwa in df.columns else f'Produkt {idx+1}'
                    prod_ean = str(row[col_ean]) if col_ean and col_ean in df.columns else ''
                    prod_ilosc = int(row[col_ilosc]) if col_ilosc and col_ilosc in df.columns and pd.notna(row[col_ilosc]) else 1
                    prod_cena = float(row[col_cena]) if col_cena and col_cena in df.columns and pd.notna(row[col_cena]) else 0
                    prod_cena_detal = float(row[col_cena_detal]) if col_cena_detal and col_cena_detal in df.columns and pd.notna(row[col_cena_detal]) else prod_cena * 2
                    # cena_brutto = cena_netto * 1.23 (VAT 23%)
                    prod_cena_brutto = round(prod_cena * 1.23, 2)

                    # Pomiń puste wiersze
                    if not prod_nazwa or prod_nazwa == 'nan' or prod_nazwa.strip() == '':
                        continue

                    # Auto-kategoryzacja na podstawie nazwy
                    prod_kategoria = auto_kategoryzuj(prod_nazwa)

                    conn.execute('''
                        INSERT INTO produkty (nazwa, ean, ilosc, cena_netto, cena_brutto, cena_allegro, paleta_id, dostawca, status, kategoria)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'magazyn', ?)
                    ''', (prod_nazwa[:200], prod_ean, prod_ilosc, prod_cena, prod_cena_brutto, prod_cena_detal, paleta_id, dostawca, prod_kategoria))

                    # Dodaj do historii - WYŁĄCZONE (konflikt bazy danych)
                    produkt_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
                    # from modules.database import add_historia
                    # add_historia(produkt_id, 'importowano', f'Zaimportowano z Excel ({nazwa})', {'dostawca': dostawca, 'ilosc': prod_ilosc, 'paleta_id': paleta_id})

                    produkty_dodane += 1

                except Exception as e:
                    print(f"Błąd wiersza {idx}: {e}")
                    continue

            # Aktualizuj liczbę produktów w palecie
            conn.execute('UPDATE palety SET ilosc_produktow = ? WHERE id = ?', (produkty_dodane, paleta_id))

            # NIE przeliczaj z sumy - cena_zakupu = STAŁA od momentu importu
            stara_cena = conn.execute('SELECT COALESCE(cena_zakupu, 0) FROM palety WHERE id = ?', (paleta_id,)).fetchone()[0]
            if stara_cena == 0:
                # Nowa paleta - ustaw z sumy cen brutto produktów (cena_brutto = ŁĄCZNA za produkt)
                suma_brutto = conn.execute('SELECT COALESCE(SUM(cena_brutto), 0) FROM produkty WHERE paleta_id = ?', (paleta_id,)).fetchone()[0]
                nowa_netto = round(suma_brutto / 1.23, 2) if suma_brutto > 0 else 0
                nowa_brutto = suma_brutto
            else:
                # Istniejąca paleta - nie ruszaj ceny, tylko dodaj nowe produkty
                nowe_netto = 0  # zostaje stara cena, nowe produkty były importowane przez paletomat który sam akumuluje
                nowa_netto = round(stara_cena / 1.23, 2)
                nowa_brutto = stara_cena

            kolumny = [desc[0] for desc in conn.execute('PRAGMA table_info(palety)').fetchall()]
            if 'cena_zakupu_netto' not in kolumny:
                try:
                    conn.execute('ALTER TABLE palety ADD COLUMN cena_zakupu_netto REAL DEFAULT 0')
                except:
                    pass

            try:
                conn.execute('UPDATE palety SET cena_zakupu = ?, cena_zakupu_netto = ? WHERE id = ?',
                    (nowa_brutto, nowa_netto, paleta_id))
            except:
                conn.execute('UPDATE palety SET cena_zakupu = ? WHERE id = ?', (nowa_brutto, paleta_id))
            print(f"💰 Cena zakupu palety (stała): {nowa_netto:.2f} netto | {nowa_brutto:.2f} brutto")

            conn.commit()

            return redirect(f'/palety/{paleta_id}?imported={produkty_dodane}')

        except Exception as e:
            return redirect(f'/palety/import-xlsx?error={str(e)[:50]}')

    # GET - pokaż formularz lub podgląd kolumn
    preview_html = ''
    columns = []

    # Jeśli jest plik w sesji - pokaż podgląd
    if 'xlsx_preview' in request.args:
        # TODO: obsługa podglądu
        pass

    error = request.args.get('error', '')
    error_html = ''
    if error:
        error_html = f'<div style="background:#ef4444;color:#fff;padding:12px;border-radius:8px;margin-bottom:15px">⚠️ Błąd: {error}</div>'

    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>📊 IMPORT Z EXCEL</h1>
            <small>Wrzuć plik XLSX z produktami</small>
        </div>

        {error_html}

        <form method="POST" enctype="multipart/form-data" style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:18px">

            <!-- PLIK -->
            <div style="margin-bottom:15px">
                <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">📁 Plik Excel (.xlsx)</label>
                <input type="file" name="file" accept=".xlsx,.xls" required
                    style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
            </div>

            <!-- DANE PALETY -->
            <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin:20px 0 10px;letter-spacing:1px">📦 DANE PALETY</div>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Nazwa palety</label>
                    <input type="text" name="nazwa" placeholder="np. Jobalots #15" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
                </div>
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Dostawca</label>
                    <select name="dostawca" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
                        <option value="Jobalots">Jobalots</option>
                        <option value="Warrington">Warrington</option>
                        <option value="Miglo">Miglo</option>
                        <option value="Inny">Inny</option>
                    </select>
                </div>
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:15px">
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Cena zakupu brutto (zł)</label>
                    <input type="number" name="cena" placeholder="2500" step="0.01" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
                </div>
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Data zakupu</label>
                    <input type="date" name="data" value="{datetime.now().strftime('%Y-%m-%d')}" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
                </div>
            </div>

            <div style="margin-bottom:15px">
                <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">📍 Regal / Lokalizacja</label>
                <input type="text" name="regal" placeholder="np. Migło, Regał A1, itp." style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
            </div>

            <!-- MAPOWANIE KOLUMN -->
            <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin:20px 0 10px;letter-spacing:1px">🔗 MAPOWANIE KOLUMN</div>
            <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:12px">Wpisz nazwy kolumn z Twojego Excela (dokładnie jak w nagłówku)</div>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px">Kolumna z NAZWĄ *</label>
                    <input type="text" name="col_nazwa" placeholder="np. Description" required
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.9rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px">Kolumna z EAN</label>
                    <input type="text" name="col_ean" placeholder="np. EAN / Barcode"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.9rem">
                </div>
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:15px">
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px">Ilość</label>
                    <input type="text" name="col_ilosc" placeholder="np. Qty"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.9rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px">Cena zakupu</label>
                    <input type="text" name="col_cena" placeholder="np. Unit Price"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.9rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px">RRP / Detal</label>
                    <input type="text" name="col_cena_detal" placeholder="np. RRP"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.9rem">
                </div>
            </div>

            <button type="submit" style="width:100%;padding:14px;background:linear-gradient(135deg,#22c55e,#16a34a);border:none;border-radius:10px;color:#fff;font-weight:600;font-size:1rem;cursor:pointer">
                📥 IMPORTUJ PALETĘ
            </button>
        </form>

        <!-- PRZYKŁADOWE NAZWY KOLUMN -->
        <div style="margin-top:15px;padding:15px;background:#1e1e2e;border-radius:12px">
            <div style="font-weight:600;margin-bottom:10px;color:#94a3b8">💡 Przykładowe nazwy kolumn</div>
            <div style="font-size:0.85rem;color:#64748b">
                <b>Jobalots:</b> Description, EAN, Qty, Unit Price, RRP<br>
                <b>Warrington:</b> Item Description, Barcode, Quantity, Cost, Retail<br>
                <b>Miglo:</b> Nazwa, EAN, Ilość, Cena, Cena detal
            </div>
        </div>

        <a href="/palety/dodaj" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:15px">← Powrót</a>
    </div>
    '''
    return html


# ═══════════════════════════════════════════════════════════════════════════
# BULK IMPORT - WIELE PALET NARAZ
# ═══════════════════════════════════════════════════════════════════════════

@palety_bp.route('/palety/bulk-import', methods=['GET', 'POST'])
def paleta_bulk_import():
    """Import wielu palet naraz - każda z osobnym plikiem XLSX i nazwą"""
    import pandas as pd
    from modules.database import get_db, add_paleta

    auto_kategoryzuj = _get_auto_kategoryzuj()
    CSS = _get_css()
    
    if request.method == 'POST':
        try:
            conn = get_db()
            
            # Pobierz wspólne ustawienia
            dostawca = request.form.get('dostawca', 'Jobalots')
            col_nazwa = request.form.get('col_nazwa', '')
            col_ean = request.form.get('col_ean', '')
            col_ilosc = request.form.get('col_ilosc', '')
            col_cena = request.form.get('col_cena', '')
            col_cena_detal = request.form.get('col_cena_detal', '')
            
            wyniki = []
            
            # Iteruj po plikach (max 20)
            for i in range(20):
                file_key = f'file_{i}'
                name_key = f'nazwa_{i}'
                cena_key = f'cena_{i}'
                regal_key = f'regal_{i}'
                
                if file_key not in request.files:
                    continue
                
                file = request.files[file_key]
                if not file or file.filename == '':
                    continue
                
                if not file.filename.endswith(('.xlsx', '.xls')):
                    wyniki.append({'nazwa': file.filename, 'status': 'error', 'msg': 'Nieprawidłowy format'})
                    continue
                
                # Nazwa palety
                nazwa = request.form.get(name_key, '').strip()
                if not nazwa:
                    # Auto-nazwa z pliku
                    nazwa = file.filename.rsplit('.', 1)[0]
                
                cena_zakupu = float(request.form.get(cena_key, 0) or 0)
                regal = request.form.get(regal_key, '').strip()
                data_zakupu = request.form.get('data', datetime.now().strftime('%Y-%m-%d'))
                
                try:
                    df = pd.read_excel(file)
                    
                    # Utwórz paletę
                    paleta_id = add_paleta(nazwa, dostawca, cena_zakupu, data_zakupu, f'Bulk import: {file.filename}', regal)
                    
                    produkty_dodane = 0
                    for idx, row in df.iterrows():
                        try:
                            prod_nazwa = str(row[col_nazwa]) if col_nazwa and col_nazwa in df.columns else f'Produkt {idx+1}'
                            prod_ean = str(row[col_ean]) if col_ean and col_ean in df.columns else ''
                            prod_ilosc = int(row[col_ilosc]) if col_ilosc and col_ilosc in df.columns and pd.notna(row[col_ilosc]) else 1
                            prod_cena = float(row[col_cena]) if col_cena and col_cena in df.columns and pd.notna(row[col_cena]) else 0
                            prod_cena_detal = float(row[col_cena_detal]) if col_cena_detal and col_cena_detal in df.columns and pd.notna(row[col_cena_detal]) else prod_cena * 2
                            # cena_brutto = cena_netto * 1.23 (VAT 23%)
                            prod_cena_brutto = round(prod_cena * 1.23, 2)
                            
                            if not prod_nazwa or prod_nazwa == 'nan' or prod_nazwa.strip() == '':
                                continue
                            
                            prod_kategoria = auto_kategoryzuj(prod_nazwa)
                            
                            conn.execute('''
                                INSERT INTO produkty (nazwa, ean, ilosc, cena_netto, cena_brutto, cena_allegro, paleta_id, dostawca, status, kategoria)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'magazyn', ?)
                            ''', (prod_nazwa[:200], prod_ean, prod_ilosc, prod_cena, prod_cena_brutto, prod_cena_detal, paleta_id, dostawca, prod_kategoria))
                            
                            produkty_dodane += 1
                        except:
                            continue
                    
                    # Aktualizuj liczbę i cenę
                    conn.execute('UPDATE palety SET ilosc_produktow = ? WHERE id = ?', (produkty_dodane, paleta_id))
                    
                    if cena_zakupu == 0:
                        # Auto-oblicz z produktów (cena_brutto = ŁĄCZNA za produkt)
                        suma_brutto = conn.execute('SELECT COALESCE(SUM(cena_brutto), 0) FROM produkty WHERE paleta_id = ?', (paleta_id,)).fetchone()[0]
                        suma_netto = round(suma_brutto / 1.23, 2) if suma_brutto > 0 else 0
                        try:
                            conn.execute('UPDATE palety SET cena_zakupu = ?, cena_zakupu_netto = ? WHERE id = ?', (suma_brutto, suma_netto, paleta_id))
                        except:
                            conn.execute('UPDATE palety SET cena_zakupu = ? WHERE id = ?', (suma_brutto, paleta_id))
                    
                    wyniki.append({
                        'nazwa': nazwa, 'status': 'ok', 'paleta_id': paleta_id,
                        'produkty': produkty_dodane, 'plik': file.filename
                    })
                    
                except Exception as e:
                    wyniki.append({'nazwa': nazwa or file.filename, 'status': 'error', 'msg': str(e)[:80]})
            
            conn.commit()
            
            # Pokaż wyniki
            ok_count = sum(1 for w in wyniki if w['status'] == 'ok')
            err_count = sum(1 for w in wyniki if w['status'] == 'error')
            
            results_html = ''
            for w in wyniki:
                if w['status'] == 'ok':
                    results_html += f'''
                    <div style="display:flex;align-items:center;gap:10px;padding:12px;background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);border-radius:10px;margin-bottom:8px">
                        <div style="font-size:1.5rem">✅</div>
                        <div style="flex:1">
                            <div style="font-weight:600">{w['nazwa']}</div>
                            <div style="font-size:0.8rem;color:#64748b">{w['produkty']} produktów • {w['plik']}</div>
                        </div>
                        <a href="/palety/{w['paleta_id']}" style="color:#3b82f6;text-decoration:none;font-size:0.85rem">Otwórz →</a>
                    </div>'''
                else:
                    results_html += f'''
                    <div style="display:flex;align-items:center;gap:10px;padding:12px;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:10px;margin-bottom:8px">
                        <div style="font-size:1.5rem">❌</div>
                        <div style="flex:1">
                            <div style="font-weight:600">{w['nazwa']}</div>
                            <div style="font-size:0.8rem;color:#ef4444">{w.get('msg', 'Błąd')}</div>
                        </div>
                    </div>'''
            
            html = CSS + f'''
            <div class="container">
                <div class="header">
                    <h1>📊 WYNIKI IMPORTU</h1>
                    <small>Zaimportowano {ok_count} palet{', błędy: ' + str(err_count) if err_count else ''}</small>
                </div>
                {results_html}
                <a href="/palety" class="btn" style="display:block;width:100%;padding:14px;background:#3b82f6;border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600;margin-top:15px">📦 Przejdź do palet</a>
                <a href="/palety/bulk-import" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:10px">📊 Importuj kolejne</a>
            </div>'''
            return html
            
        except Exception as e:
            return redirect(f'/palety/bulk-import?error={str(e)[:50]}')
    
    # === GET - formularz ===
    error = request.args.get('error', '')
    error_html = f'<div style="background:#ef4444;color:#fff;padding:12px;border-radius:8px;margin-bottom:15px">⚠️ {error}</div>' if error else ''
    
    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>📊 BULK IMPORT PALET</h1>
            <small>Importuj wiele palet naraz — każda z osobnym plikiem XLSX</small>
        </div>
        
        {error_html}
        
        <form method="POST" enctype="multipart/form-data" id="bulk-form">
        
        <!-- WSPÓLNE USTAWIENIA -->
        <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:18px;margin-bottom:15px">
            <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin-bottom:12px;letter-spacing:1px">⚙️ WSPÓLNE USTAWIENIA</div>
            
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Dostawca</label>
                    <select name="dostawca" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
                        <option value="Jobalots">Jobalots</option>
                        <option value="Warrington">Warrington</option>
                        <option value="Miglo">Miglo</option>
                        <option value="Inny">Inny</option>
                    </select>
                </div>
                <div>
                    <label style="display:block;font-size:0.8rem;color:#94a3b8;margin-bottom:5px">Data zakupu</label>
                    <input type="date" name="data" value="{datetime.now().strftime('%Y-%m-%d')}" style="width:100%;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff">
                </div>
            </div>
            
            <!-- MAPOWANIE KOLUMN -->
            <div style="font-size:0.7rem;color:#64748b;text-transform:uppercase;margin:15px 0 8px;letter-spacing:1px">🔗 MAPOWANIE KOLUMN (wspólne dla wszystkich plików)</div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">Kolumna NAZWA *</label>
                    <input type="text" name="col_nazwa" placeholder="np. Description" required
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">Kolumna EAN</label>
                    <input type="text" name="col_ean" placeholder="np. EAN"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">Ilość</label>
                    <input type="text" name="col_ilosc" placeholder="Qty"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">Cena zakupu</label>
                    <input type="text" name="col_cena" placeholder="Unit Price"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">RRP / Detal</label>
                    <input type="text" name="col_cena_detal" placeholder="RRP"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
            </div>
        </div>
        
        <!-- PALETY -->
        <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin-bottom:10px;letter-spacing:1px">📦 PALETY DO IMPORTU</div>
        
        <div id="palety-container"></div>
        
        <button type="button" onclick="addPaleta()" style="width:100%;padding:14px;background:#1e1e2e;border:2px dashed #3b82f6;border-radius:12px;color:#3b82f6;font-weight:600;cursor:pointer;margin-bottom:15px;font-size:0.95rem">
            ➕ DODAJ PALETĘ
        </button>
        
        <button type="submit" id="submit-btn" disabled style="width:100%;padding:16px;background:linear-gradient(135deg,#22c55e,#16a34a);border:none;border-radius:12px;color:#fff;font-weight:700;font-size:1.1rem;cursor:pointer;opacity:0.5">
            📥 IMPORTUJ WSZYSTKIE
        </button>
        
        </form>
        
        <a href="/palety/dodaj" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:15px">← Powrót</a>
    </div>
    
    <script>
    let paletaCount = 0;
    
    function addPaleta() {{
        const i = paletaCount++;
        const container = document.getElementById('palety-container');
        
        const div = document.createElement('div');
        div.className = 'paleta-row';
        div.id = 'paleta-' + i;
        div.style.cssText = 'background:#12121a;border:1px solid #1e1e2e;border-radius:14px;padding:15px;margin-bottom:10px;position:relative';
        
        div.innerHTML = `
            <button type="button" onclick="removePaleta(${{i}})" style="position:absolute;top:10px;right:10px;background:rgba(239,68,68,0.2);border:none;border-radius:8px;color:#ef4444;padding:4px 10px;cursor:pointer;font-size:0.8rem">✕</button>
            
            <div style="font-weight:600;color:#3b82f6;margin-bottom:10px;font-size:0.9rem">📦 Paleta #${{i+1}}</div>
            
            <div style="margin-bottom:10px">
                <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">📁 Plik Excel</label>
                <input type="file" name="file_${{i}}" accept=".xlsx,.xls" required onchange="updateFileName(this, ${{i}})"
                    style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
            </div>
            
            <div style="display:grid;grid-template-columns:2fr 1fr 1fr;gap:8px">
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">Nazwa palety</label>
                    <input type="text" name="nazwa_${{i}}" id="nazwa-${{i}}" placeholder="Auto z nazwy pliku"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">Cena brutto</label>
                    <input type="number" name="cena_${{i}}" placeholder="0" step="0.01"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px">Regał</label>
                    <input type="text" name="regal_${{i}}" placeholder="A1"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:0.85rem">
                </div>
            </div>
        `;
        
        container.appendChild(div);
        updateSubmitBtn();
    }}
    
    function removePaleta(i) {{
        const el = document.getElementById('paleta-' + i);
        if (el) el.remove();
        updateSubmitBtn();
    }}
    
    function updateFileName(input, i) {{
        const nameField = document.getElementById('nazwa-' + i);
        if (nameField && !nameField.value && input.files.length) {{
            // Auto-fill nazwa z pliku (bez rozszerzenia)
            nameField.placeholder = input.files[0].name.replace(/\\.[^.]+$/, '');
        }}
    }}
    
    function updateSubmitBtn() {{
        const rows = document.querySelectorAll('.paleta-row');
        const btn = document.getElementById('submit-btn');
        btn.disabled = rows.length === 0;
        btn.style.opacity = rows.length === 0 ? '0.5' : '1';
        btn.textContent = rows.length === 0 ? '📥 DODAJ PALETY POWYŻEJ' : `📥 IMPORTUJ ${{rows.length}} PALET`;
    }}
    
    // Dodaj pierwszą od razu
    addPaleta();
    </script>
    '''
    return html

# ═══════════════════════════════════════════════════════════════════════════
# MASOWA EDYCJA PALET - Adrian's custom feature v3.1.0
# ═══════════════════════════════════════════════════════════════════════════

@palety_bp.route('/palety/<int:paleta_id>/mass-edit')
def paleta_mass_edit(paleta_id):
    """Strona masowej edycji produktów z palety"""
    from modules.database import get_db

    CSS = _get_css()
    
    conn = get_db()
    paleta = conn.execute('SELECT * FROM palety WHERE id = ?', (paleta_id,)).fetchone()
    
    if not paleta:
        return redirect('/palety')
    
    # Pobierz produkty z magazynu
    produkty = conn.execute('''
        SELECT * FROM produkty 
        WHERE paleta_id = ? 
        ORDER BY 
            CASE 
                WHEN status = 'wystawiony' THEN 1
                WHEN status = 'magazyn' THEN 2
                ELSE 3
            END,
            data_dodania DESC
    ''', (paleta_id,)).fetchall()
    
    # Stats
    stats = conn.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'wystawiony' THEN 1 ELSE 0 END) as wystawione,
            SUM(CASE WHEN status = 'magazyn' THEN 1 ELSE 0 END) as magazyn,
            COALESCE(SUM(cena_allegro * ilosc), 0) as wartosc_total
        FROM produkty 
        WHERE paleta_id = ?
    ''', (paleta_id,)).fetchone()
    
    
    if not produkty or len(produkty) == 0:
        return CSS + f'''
        <div class="container">
            <div class="header">
                <h1>⚠️ Brak produktów</h1>
                <small>Paleta #{paleta_id}</small>
            </div>
            <div class="alert alert-warning">
                Ta paleta nie ma jeszcze żadnych produktów. Najpierw zaimportuj produkty z Excel.
            </div>
            <a href="/magazyn/import?paleta_id={paleta_id}" class="btn btn-primary">📥 Importuj produkty</a>
            <a href="/palety/{paleta_id}" class="btn btn-secondary">← Powrót</a>
        </div>
        '''
    
    # Generuj HTML produktów
    produkty_html = ''
    wybrane_count = 0
    
    for p in produkty:
        # Kolory statusów
        if p['status'] == 'wystawiony':
            status_badge = '<span style="background:#22c55e;color:#000;padding:4px 10px;border-radius:6px;font-size:0.75rem;font-weight:600">✅ WYSTAWIONE</span>'
            row_bg = 'rgba(34,197,94,0.05)'
            checkbox_disabled = 'disabled'
            checkbox_checked = ''
        elif p['status'] == 'magazyn':
            status_badge = '<span style="background:#3b82f6;color:#fff;padding:4px 10px;border-radius:6px;font-size:0.75rem;font-weight:600">🔵 MAGAZYN</span>'
            row_bg = 'rgba(59,130,246,0.05)'
            checkbox_disabled = ''
            checkbox_checked = 'checked'
            wybrane_count += 1
        elif p['status'] == 'szkic':
            status_badge = '<span style="background:#8b5cf6;color:#fff;padding:4px 10px;border-radius:6px;font-size:0.75rem;font-weight:600">📝 SZKIC</span>'
            row_bg = 'rgba(139,92,246,0.05)'
            checkbox_disabled = ''
            checkbox_checked = 'checked'
            wybrane_count += 1
        else:
            status_badge = '<span style="background:#64748b;color:#fff;padding:4px 10px;border-radius:6px;font-size:0.75rem;font-weight:600">⚪ NOWY</span>'
            row_bg = 'rgba(100,116,139,0.05)'
            checkbox_disabled = ''
            checkbox_checked = ''
        
        # Cena jednostkowa zakupu - z palety (cena_zakupu / ilosc_sztuk), fallback na cena_brutto/ilosc
        paleta_ilosc_szt_w = 0
        try:
            paleta_ilosc_szt_w = paleta['ilosc_sztuk'] or 0
        except:
            pass
        paleta_cena_zak_w = paleta['cena_zakupu'] or 0
        if paleta_cena_zak_w > 0 and paleta_ilosc_szt_w > 0:
            brutto_szt_w = paleta_cena_zak_w / paleta_ilosc_szt_w
            netto_szt_w = round(brutto_szt_w / 1.23, 2)
            ceny_tekst = f"Za szt: {netto_szt_w:.2f} zł netto / {brutto_szt_w:.2f} zł brutto (z palety)"
        else:
            ilosc_produktu = p['ilosc'] if p['ilosc'] > 0 else 1
            brutto_total = p['cena_brutto'] if p['cena_brutto'] > 0 else 0
            netto_total = p['cena_netto'] if p['cena_netto'] > 0 else 0
            brutto_szt_w = brutto_total if brutto_total > 0 else 0  # już jednostkowa, nie dzielić!
            netto_szt_w = netto_total if netto_total > 0 else 0
            if netto_total > 0 and brutto_total > 0:
                ceny_tekst = f"Za szt: {netto_szt_w:.2f} zł netto / {brutto_szt_w:.2f} zł brutto"
            elif netto_total > 0:
                ceny_tekst = f"Za szt: {netto_szt_w:.2f} zł netto"
            else:
                ceny_tekst = f"Za szt: {brutto_szt_w:.2f} zł brutto"
        
        img_html = ''
        if p['zdjecie_url']:
            img_html = f'<img src="{p["zdjecie_url"]}" style="width:50px;height:50px;object-fit:contain;border-radius:8px;background:#fff;margin-right:10px">'
        
        cena_input = f'''
        <input type="number"
               class="price-input"
               data-product-id="{p['id']}"
               value="{p['cena_allegro']:.0f}"
               min="1"
               step="1"
               style="width:90px;padding:10px 8px;background:var(--bg-primary);border:2px solid var(--border-color);border-radius:10px;color:var(--text-primary);text-align:center;font-weight:700;font-size:1rem;min-height:42px"
               {checkbox_disabled}>
        '''
        
        produkty_html += (f'''
        <div class="product-row" style="background:{row_bg};border:1px solid var(--border-color);border-radius:12px;padding:14px;margin-bottom:10px">
            ''' + (f'''
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;padding:8px 10px;background:rgba(34,197,94,0.1);border-radius:8px">
                <div style="font-size:0.85rem;color:#22c55e;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600">📝 {str(p["meta_title"])[:80]}</div>
                <button onclick="regenerateMetaTitle({p["id"]}, this)"
                        style="padding:8px 14px;background:#8b5cf6;border:none;border-radius:8px;color:#fff;font-size:0.8rem;cursor:pointer;white-space:nowrap;min-height:36px">
                    🔄 Regeneruj
                </button>
            </div>
            ''' if 'meta_title' in p.keys() and p['meta_title'] else f'''
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;padding:8px 10px;background:rgba(239,68,68,0.1);border-radius:8px">
                <div style="font-size:0.85rem;color:#ef4444;flex:1;font-weight:600">⚠️ Brak META TITLE</div>
                <button onclick="regenerateMetaTitle({p["id"]}, this)"
                        style="padding:8px 14px;background:#22c55e;border:none;border-radius:8px;color:#fff;font-size:0.8rem;cursor:pointer;white-space:nowrap;min-height:36px">
                    ✨ Generuj
                </button>
            </div>
            ''') + f'''
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
                <input type="checkbox"
                       class="product-checkbox"
                       data-product-id="{p['id']}"
                       value="{p['id']}"
                       {checkbox_checked}
                       {checkbox_disabled}
                       style="width:24px;height:24px;min-width:24px;cursor:pointer">
                {img_html}
                <div style="flex:1;min-width:0">
                    <div style="font-weight:600;font-size:0.95rem;line-height:1.3">{p['nazwa'][:60]}</div>
                </div>
                {status_badge}
            </div>
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                <div style="font-size:0.8rem;color:var(--text-muted);flex:1;min-width:150px">
                    {p['ean'] or p['asin'] or '—'} •
                    Lokalizacja: {p['lokalizacja'] or '—'} •
                    Ilość: <span data-qty-id="{p['id']}">{p['ilosc']}</span>
                </div>
                <div style="font-size:0.75rem;color:#ef4444">💰 {ceny_tekst}</div>
                <div style="display:flex;align-items:center;gap:6px">
                    <span style="font-size:0.75rem;color:var(--text-muted)">CENA:</span>
                    {cena_input}
                </div>
            </div>
        </div>
        ''')

    html = CSS + f'''
    <style>
    .me-stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-bottom:16px }}
    .me-stat {{ background:var(--bg-secondary); border:1px solid var(--border-color); border-radius:12px; padding:12px; text-align:center }}
    .me-stat-num {{ font-size:1.3rem; font-weight:700 }}
    .me-stat-label {{ font-size:0.7rem; color:var(--text-muted) }}
    .me-bottom {{ position:fixed; bottom:0; left:0; right:0; background:var(--bg-secondary); border-top:2px solid var(--accent-blue); padding:12px 8px; z-index:100 }}
    .me-bottom-inner {{ display:flex; flex-direction:column; gap:8px; max-width:1600px; margin:0 auto }}
    .me-bottom-row {{ display:flex; gap:8px }}
    .me-btn {{ flex:1; margin:0; padding:14px 10px; font-size:0.95rem; font-weight:600; border:none; border-radius:10px; color:#fff; cursor:pointer; min-height:48px; text-align:center; text-decoration:none; display:flex; align-items:center; justify-content:center }}
    .me-btn-back {{ background:var(--bg-primary); color:var(--text-primary); border:1px solid var(--border-color); flex:0 0 auto; padding:14px 16px }}
    .me-btn-meta {{ background:linear-gradient(135deg,#8b5cf6,#6d28d9) }}
    .me-btn-wystaw {{ background:linear-gradient(135deg,#22c55e,#16a34a) }}
    .me-info {{ font-size:0.8rem; margin-bottom:12px; padding:10px; background:rgba(34,197,94,0.1); border-radius:10px; color:var(--text-muted) }}
    @media(max-width:768px) {{
        .me-stats {{ grid-template-columns:repeat(2,1fr) }}
        .me-stat-num {{ font-size:1.1rem }}
        .me-bottom {{ padding:10px 6px }}
        .me-bottom-row {{ gap:6px }}
        .me-btn {{ padding:12px 8px; font-size:0.85rem; min-height:44px }}
    }}
    @media(max-width:480px) {{
        .me-stats {{ grid-template-columns:repeat(2,1fr); gap:6px }}
    }}
    </style>
    <div class="container">
        <div class="header">
            <h1>✏️ Masowa edycja cen</h1>
            <small>{paleta['nazwa'] or f"Paleta #{paleta_id}"}</small>
        </div>

        <div class="me-stats">
            <div class="me-stat">
                <div class="me-stat-num" style="color:var(--accent-blue)">{stats['total']}</div>
                <div class="me-stat-label">WSZYSTKICH</div>
            </div>
            <div class="me-stat">
                <div class="me-stat-num" style="color:var(--accent-green)">{stats['wystawione']}</div>
                <div class="me-stat-label">WYSTAWIONE</div>
            </div>
            <div class="me-stat">
                <div class="me-stat-num" style="color:var(--accent-blue)" id="count-selected">{wybrane_count}</div>
                <div class="me-stat-label">ZAZNACZONE</div>
            </div>
            <div class="me-stat">
                <div class="me-stat-num" style="color:var(--accent-green)" id="value-total">{stats['wartosc_total']:.0f} zł</div>
                <div class="me-stat-label">WARTOŚĆ</div>
            </div>
        </div>

        <div class="me-info">
            💡 Zaznacz produkty → edytuj ceny → kliknij <b>Wystaw</b>. Wystawione (zielone) nie można zaznaczyć.
        </div>

        <div id="products-list" style="padding-bottom:140px">
            {produkty_html}
        </div>

        <div class="me-bottom">
            <div class="me-bottom-inner">
                <div class="me-bottom-row">
                    <a href="/palety/{paleta_id}" class="me-btn me-btn-back">← Powrót</a>
                    <button id="btn-select-all" class="me-btn" style="background:#334155" onclick="toggleSelectAll()">
                        ☑️ Zaznacz wszystkie
                    </button>
                </div>
                <div class="me-bottom-row">
                    <button id="btn-batch-meta" class="me-btn me-btn-meta" onclick="batchGenerateMetaTitles()">
                        ✨ Generuj META (<span id="count-meta-btn">{wybrane_count}</span>)
                    </button>
                    <button id="btn-wystaw" class="me-btn me-btn-wystaw" onclick="wystawZaznaczone()">
                        🚀 Wystaw (<span id="count-btn">{wybrane_count}</span>)
                    </button>
                </div>
            </div>
        </div>
    </div>
    
    <script>
    function updateCounter() {{
        const checkboxes = document.querySelectorAll('.product-checkbox:checked:not(:disabled)');
        const count = checkboxes.length;
        document.getElementById('count-selected').textContent = count;
        document.getElementById('count-btn').textContent = count;
        document.getElementById('count-meta-btn').textContent = count;
        document.getElementById('btn-wystaw').disabled = count === 0;
        document.getElementById('btn-batch-meta').disabled = count === 0;
        
        let total = 0;
        checkboxes.forEach(cb => {{
            const productId = cb.dataset.productId;
            const priceInput = document.querySelector('.price-input[data-product-id="' + productId + '"]');
            const qtyEl = document.querySelector('[data-qty-id="' + productId + '"]');
            const qty = qtyEl ? (parseInt(qtyEl.textContent) || 1) : 1;
            if (priceInput) {{
                total += (parseFloat(priceInput.value) || 0) * qty;
            }}
        }});
        document.getElementById('value-total').textContent = total.toFixed(0) + ' zł';
    }}
    
    const priceInputs = document.querySelectorAll('.price-input');
    priceInputs.forEach(input => {{
        let timeout;
        input.addEventListener('input', function() {{
            clearTimeout(timeout);
            const productId = this.dataset.productId;
            const newPrice = parseFloat(this.value) || 0;
            this.style.borderColor = '#eab308';
            
            timeout = setTimeout(() => {{
                fetch('/palety/api/update-price', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        product_id: productId,
                        price: newPrice
                    }})
                }})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        input.style.borderColor = '#22c55e';
                        setTimeout(() => input.style.borderColor = '', 1000);
                        updateCounter();
                    }} else {{
                        input.style.borderColor = '#ef4444';
                        alert('Błąd zapisu: ' + data.error);
                    }}
                }})
                .catch(err => {{
                    input.style.borderColor = '#ef4444';
                    console.error('Error:', err);
                }});
            }}, 800);
        }});
    }});
    
    const checkboxes = document.querySelectorAll('.product-checkbox');
    checkboxes.forEach(cb => {{
        cb.addEventListener('change', updateCounter);
    }});
    
    function wystawZaznaczone() {{
        const checked = document.querySelectorAll('.product-checkbox:checked:not(:disabled)');
        if (checked.length === 0) {{
            alert('Zaznacz przynajmniej 1 produkt!');
            return;
        }}
        const productIds = Array.from(checked).map(cb => cb.value);
        window.location.href = '/paletomat/generator/mass-create-from-paleta?paleta_id={paleta_id}&ids=' + productIds.join(',');
    }}
    
    function batchGenerateMetaTitles() {{
        const checked = document.querySelectorAll('.product-checkbox:checked:not(:disabled)');
        if (checked.length === 0) {{
            alert('Zaznacz przynajmniej 1 produkt!');
            return;
        }}
        
        // BATCH LIMIT (zwiększony dla paid tier)
        const MAX_BATCH = 100;  // Zwiększone z 10 na 100
        if (checked.length > MAX_BATCH) {{
            alert(`❌ Zbyt dużo produktów!\\n\\nZaznaczono: ${{checked.length}}\\nMax: ${{MAX_BATCH}}\\n\\nZaznacz mniej produktów lub podziel na mniejsze batche.`);
            return;
        }}
        
        // Oblicz czas (5s delay na produkt dla bezpieczeństwa)
        const estimatedTime = checked.length * 5;
        const minutes = Math.floor(estimatedTime / 60);
        const seconds = estimatedTime % 60;
        const timeStr = minutes > 0 ? `${{minutes}}min ${{seconds}}s` : `${{seconds}}s`;
        
        if (!confirm(`🤖 Wygenerować META TITLE dla ${{checked.length}} produktów?\\n\\n⏱️  Szacowany czas: ~${{timeStr}}\\n⚠️  5s opóźnienie między produktami (safe rate limiting)\\n\\nKontynuować?`)) {{
            return;
        }}
        
        const productIds = Array.from(checked).map(cb => cb.value);
        const button = document.getElementById('btn-batch-meta');
        const originalText = button.innerHTML;
        
        // Disable button and show progress
        button.disabled = true;
        button.innerHTML = '⏳ Generuję 0/' + productIds.length + '... (może zająć ~' + timeStr + ')';
        
        fetch('/api/generate_meta_title_batch', {{
            method: 'POST',
            mode: 'cors',
            credentials: 'same-origin',
            headers: {{ 
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }},
            body: JSON.stringify({{ product_ids: productIds }})
        }})
        .then(res => res.json())
        .then(data => {{
            if (data.success) {{
                // Sprawdź czy były błędy quota
                const quotaErrors = data.details ? data.details.filter(d => d.error && d.error.includes('Quota')).length : 0;
                
                let msg = `✅ Gotowe!\\n\\nWygenerowano: ${{data.generated}}\\nBłędy: ${{data.failed}}`;
                
                if (quotaErrors > 0) {{
                    msg += `\\n\\n⚠️  Quota exceeded!\\nPoczekaj do jutra (reset o 9:00 AM)\\nlub upgrade do paid tier.`;
                }}
                
                alert(msg);
                location.reload();
            }} else {{
                // Lepsze error messages
                let errorMsg = data.error || 'Nieznany błąd';
                
                if (errorMsg.includes('Zbyt dużo')) {{
                    errorMsg = `❌ ${{errorMsg}}\\n\\nTIP: Zaznacz max 10 produktów lub poczekaj do jutra na reset quota.`;
                }}
                
                alert('❌ Błąd:\\n\\n' + errorMsg);
                button.disabled = false;
                button.innerHTML = originalText;
            }}
        }})
        .catch(err => {{
            alert('❌ Błąd połączenia:\\n\\n' + err + '\\n\\nSprawdź console (F12) dla szczegółów.');
            button.disabled = false;
            button.innerHTML = originalText;
        }});
    }}
    
    function toggleSelectAll() {{
        const checkboxes = document.querySelectorAll('.product-checkbox:not(:disabled)');
        const allChecked = Array.from(checkboxes).every(cb => cb.checked);
        checkboxes.forEach(cb => {{ cb.checked = !allChecked; }});
        const btn = document.getElementById('btn-select-all');
        btn.innerHTML = allChecked ? '☑️ Zaznacz wszystkie' : '☐ Odznacz wszystkie';
        updateCounter();
    }}

    function regenerateMetaTitle(productId, button) {{
        const originalText = button.innerHTML;
        button.disabled = true;
        button.innerHTML = '⏳ Generuję...';
        
        fetch('/produkty/' + productId + '/regenerate-meta-title', {{
            method: 'POST',
            mode: 'cors',
            credentials: 'same-origin',
            headers: {{ 
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }}
        }})
        .then(res => res.json())
        .then(data => {{
            if (data.success) {{
                // Odśwież stronę aby pokazać nowy META TITLE
                location.reload();
            }} else {{
                alert('Błąd: ' + (data.error || 'Nieznany błąd'));
                button.disabled = false;
                button.innerHTML = originalText;
            }}
        }})
        .catch(err => {{
            alert('Błąd połączenia: ' + err);
            button.disabled = false;
            button.innerHTML = originalText;
        }});
    }}
    
    updateCounter();
    </script>
    '''
    
    return html

@palety_bp.route('/palety/api/update-price', methods=['POST'])
def api_update_price():
    """API do aktualizacji ceny produktu"""
    from modules.database import get_db
    
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        new_price = float(data.get('price', 0))
        
        if not product_id or new_price < 0:
            return jsonify({'success': False, 'error': 'Nieprawidłowe dane'})
        
        conn = get_db()
        
        # Pobierz starą cenę do historii
        old_product = conn.execute('SELECT cena_allegro, paleta_id FROM produkty WHERE id = ?', (product_id,)).fetchone()
        old_price = old_product['cena_allegro'] if old_product else 0
        
        conn.execute('UPDATE produkty SET cena_allegro = ? WHERE id = ?', (new_price, product_id))
        
        # Dodaj do historii jeśli cena się zmieniła
        if old_price != new_price:
            from modules.database import add_historia
            add_historia(product_id, 'zmiana_ceny', f'Zmiana ceny Allegro: {old_price:.0f} → {new_price:.0f} zł', 
                {'stara_cena': old_price, 'nowa_cena': new_price})
        
        product = conn.execute('SELECT paleta_id FROM produkty WHERE id = ?', (product_id,)).fetchone()
        
        if product and product['paleta_id']:
            stats = conn.execute('''
                SELECT COALESCE(SUM(cena_allegro * ilosc), 0) as total
                FROM produkty WHERE paleta_id = ?
            ''', (product['paleta_id'],)).fetchone()
            conn.commit()
            return jsonify({'success': True, 'new_total': float(stats['total'])})
        
        conn.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"Error updating price: {e}")
        return jsonify({'success': False, 'error': str(e)})

@palety_bp.route('/palety/<int:paleta_id>')
def paleta_szczegoly(paleta_id):
    """Widok szczegółów palety z kolorami statusów"""
    from modules.database import get_db

    CSS = _get_css()
    
    conn = get_db()
    paleta = conn.execute('SELECT * FROM palety WHERE id = ?', (paleta_id,)).fetchone()
    produkty = conn.execute('SELECT * FROM produkty WHERE paleta_id = ? ORDER BY data_dodania DESC', (paleta_id,)).fetchall()
    
    # MIGRACJA: przenieś przychod_offline z produkty -> sprzedaze (PRZED obliczaniem stats)
    try:
        from datetime import datetime as _dtm
        stare_offline = conn.execute("""
            SELECT p.id, p.nazwa, p.przychod_offline, p.sprzedano_offline, pal.data_zakupu
            FROM produkty p LEFT JOIN palety pal ON pal.id = p.paleta_id
            WHERE p.paleta_id = ? AND p.sprzedano_offline > 0 AND p.przychod_offline > 0
              AND NOT EXISTS (SELECT 1 FROM sprzedaze s WHERE s.produkt_id=p.id AND s.kupujacy='offline' AND s.cena>0)
        """, (paleta_id,)).fetchall()
        for row in stare_offline:
            data = row['data_zakupu'] or _dtm.now().strftime('%Y-%m-%dT%H:%M:%S')
            cena_szt = round(row['przychod_offline'] / max(row['sprzedano_offline'], 1), 2)
            conn.execute("DELETE FROM sprzedaze WHERE produkt_id=? AND kupujacy='offline' AND cena=0", (row['id'],))
            conn.execute("INSERT INTO sprzedaze (produkt_id,nazwa,cena,ilosc,status,data_sprzedazy,kupujacy,notified) VALUES (?,?,?,?,'sprzedana',?,'offline',1)",
                (row['id'], row['nazwa'] or f'Produkt #{row["id"]}', cena_szt, row['sprzedano_offline'], data))
            conn.execute("UPDATE produkty SET przychod_offline=0 WHERE id=?", (row['id'],))
        if stare_offline:
            conn.commit()
            print(f"✅ Migracja palety {paleta_id}: {len(stare_offline)} offline -> sprzedaze")
    except Exception as _em:
        print(f"⚠️ Migracja palety: {_em}")

    # Zeruj przychod_offline I sprzedano_offline dla produktów które mają już rekord w sprzedaze (cleanup)
    # FIX: zeruj OBA pola — wcześniej tylko przychod_offline, co powodowało mismatch (+1 sprzedanych)
    try:
        conn.execute('''
            UPDATE produkty SET przychod_offline = 0, sprzedano_offline = 0
            WHERE paleta_id = ? AND (przychod_offline > 0 OR sprzedano_offline > 0)
              AND EXISTS (
                  SELECT 1 FROM sprzedaze s
                  WHERE s.produkt_id = produkty.id AND s.kupujacy = 'offline' AND s.cena > 0
              )
        ''', (paleta_id,))
        conn.commit()
    except:
        pass

    # Sprawdź czy kolumny offline istnieją
    has_offline_columns = False
    try:
        conn.execute("SELECT sprzedano_offline, przychod_offline FROM produkty LIMIT 1")
        has_offline_columns = True
    except:
        pass
    
    if has_offline_columns:
        # Wykluczamy produkty sprzedane offline z liczenia Allegro (żeby się nie duplikowały)
        # sprzedane_produkty_allegro = tylko te bez offline (sprawdzamy sprzedano_offline, nie przychod)
        stats = conn.execute('''
            SELECT COUNT(*) as cnt, 
                   COALESCE(SUM(ilosc), 0) as sztuki,
                   COALESCE(SUM(CASE WHEN status IN ('wystawiony', 'szkic') THEN cena_allegro * ilosc ELSE 0 END), 0) as wartosc,
                   COALESCE(SUM(CASE WHEN status = 'sprzedany' AND (sprzedano_offline IS NULL OR sprzedano_offline = 0) THEN cena_allegro ELSE 0 END), 0) as sprzedano_wartosc,
                   COALESCE(SUM(CASE WHEN status = 'sprzedany' AND (sprzedano_offline IS NULL OR sprzedano_offline = 0) THEN 1 ELSE 0 END), 0) as sprzedane_produkty,
                   COALESCE(SUM(CASE WHEN status = 'sprzedany' THEN cena_brutto ELSE 0 END), 0) as sprzedano_koszt,
                   SUM(CASE WHEN status = 'wystawiony' THEN 1 ELSE 0 END) as wystawione,
                   SUM(CASE WHEN status = 'magazyn' THEN 1 ELSE 0 END) as magazyn,
                   SUM(CASE WHEN status = 'sprzedany' THEN 1 ELSE 0 END) as sprzedane_cnt,
                   COALESCE(SUM(cena_brutto), 0) as zakup_brutto_suma,
                   COALESCE(SUM(cena_netto), 0) as zakup_netto_suma,
                   COALESCE(SUM(sprzedano_offline), 0) as sprzedano_offline_suma,
                   COALESCE(SUM(przychod_offline), 0) as przychod_offline_suma
            FROM produkty WHERE paleta_id = ?
        ''', (paleta_id,)).fetchone()
    else:
        stats = conn.execute('''
            SELECT COUNT(*) as cnt, 
                   COALESCE(SUM(ilosc), 0) as sztuki,
                   COALESCE(SUM(CASE WHEN status IN ('wystawiony', 'szkic') THEN cena_allegro * ilosc ELSE 0 END), 0) as wartosc,
                   COALESCE(SUM(CASE WHEN status = 'sprzedany' THEN cena_allegro ELSE 0 END), 0) as sprzedano_wartosc,
                   COALESCE(SUM(CASE WHEN status = 'sprzedany' THEN 1 ELSE 0 END), 0) as sprzedane_produkty,
                   COALESCE(SUM(CASE WHEN status = 'sprzedany' THEN cena_brutto ELSE 0 END), 0) as sprzedano_koszt,
                   SUM(CASE WHEN status = 'wystawiony' THEN 1 ELSE 0 END) as wystawione,
                   SUM(CASE WHEN status = 'magazyn' THEN 1 ELSE 0 END) as magazyn,
                   SUM(CASE WHEN status = 'sprzedany' THEN 1 ELSE 0 END) as sprzedane_cnt,
                   COALESCE(SUM(cena_brutto), 0) as zakup_brutto_suma,
                   COALESCE(SUM(cena_netto), 0) as zakup_netto_suma,
                   0 as sprzedano_offline_suma,
                   0 as przychod_offline_suma
            FROM produkty WHERE paleta_id = ?
        ''', (paleta_id,)).fetchone()
    
    # Pobierz rzeczywistą sprzedaż z tabeli sprzedaze (dla dokładniejszych danych)
    # Obliczamy koszt na podstawie średniej ceny za sztukę (cena_brutto / ilosc_oryginalna)
    sprzedaz_stats = conn.execute('''
        SELECT COALESCE(SUM(s.cena * s.ilosc), 0) as przychod,
               COALESCE(SUM(s.ilosc), 0) as szt_sprzedanych
        FROM sprzedaze s
        JOIN produkty p ON s.produkt_id = p.id
        WHERE p.paleta_id = ?
          AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot','')
    ''', (paleta_id,)).fetchone()
    
    if not paleta:
        return redirect('/palety')
    
    # Bezpieczne pobieranie ceny netto (kolumna może nie istnieć w starej bazie)
    cena_zakupu_netto = 0
    try:
        # Sprawdź czy kolumna istnieje
        kolumny = [desc[0] for desc in conn.execute('PRAGMA table_info(palety)').fetchall()]
        if 'cena_zakupu_netto' in kolumny:
            val = conn.execute('SELECT cena_zakupu_netto FROM palety WHERE id = ?', (paleta_id,)).fetchone()
            cena_zakupu_netto = val[0] if val and val[0] else 0
    except:
        pass
    
    # AUTO-NAPRAWA: Jeśli cena_zakupu = 0, zapisz aktualną sumę (jednorazowo!)
    cena_zakupu = paleta['cena_zakupu'] or 0
    if cena_zakupu == 0:
        # Oblicz sumę netto z produktów
        suma_netto = stats['zakup_netto_suma'] or 0
        suma_brutto = round(suma_netto * 1.23, 2)
        
        if suma_netto > 0:
            # cena_zakupu = BRUTTO
            kolumny = [desc[0] for desc in conn.execute('PRAGMA table_info(palety)').fetchall()]
            if 'cena_zakupu_netto' in kolumny:
                conn.execute('''
                    UPDATE palety SET cena_zakupu = ?, cena_zakupu_netto = ? WHERE id = ?
                ''', (suma_brutto, suma_netto, paleta_id))
            else:
                conn.execute('UPDATE palety SET cena_zakupu = ? WHERE id = ?', (suma_brutto, paleta_id))
            conn.commit()
            cena_zakupu = suma_brutto
            cena_zakupu_netto = suma_netto
            print(f"💰 Auto-naprawiono cenę zakupu palety #{paleta_id}: {suma_netto:.2f} netto | {suma_brutto:.2f} brutto")
    
    # Przychód offline ze sprzedaze (nowe rekordy po migracji)
    przychod_offline_sprzedaze = conn.execute('''
        SELECT COALESCE(SUM(s.cena * s.ilosc), 0)
        FROM sprzedaze s
        JOIN produkty p ON s.produkt_id = p.id
        WHERE p.paleta_id = ? AND s.kupujacy = 'offline'
          AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot','')
    ''', (paleta_id,)).fetchone()[0] or 0

    # Przychód offline ze starych danych - TYLKO dla produktów bez rekordu w sprzedaze
    # (żeby nie liczyć podwójnie tych które już mają rekord w sprzedaze)
    przychod_offline_stare = conn.execute('''
        SELECT COALESCE(SUM(przychod_offline), 0)
        FROM produkty
        WHERE paleta_id = ? AND przychod_offline > 0
          AND NOT EXISTS (
              SELECT 1 FROM sprzedaze s
              WHERE s.produkt_id = produkty.id
                AND s.kupujacy = 'offline'
                AND s.cena > 0
          )
    ''', (paleta_id,)).fetchone()[0] or 0

    # Przychód z Allegro (sprzedaze bez offline)
    przychod_allegro_db = conn.execute('''
        SELECT COALESCE(SUM(s.cena * s.ilosc), 0)
        FROM sprzedaze s
        JOIN produkty p ON s.produkt_id = p.id
        WHERE p.paleta_id = ? AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
          AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot','')
    ''', (paleta_id,)).fetchone()[0] or 0

    
    # cena_zakupu w bazie = BRUTTO
    koszt_palety_brutto = cena_zakupu
    koszt_palety_netto = cena_zakupu_netto if cena_zakupu_netto > 0 else round(cena_zakupu / 1.23, 2)

    # STAŁY koszt jednostkowy (NETTO/szt) - raz ustawiony, nie zmienia się
    try:
        _kj_netto = float(paleta['koszt_jednostkowy'] or 0)
    except:
        _kj_netto = 0
    if _kj_netto == 0 and koszt_palety_netto > 0:
        # Auto-set: oblicz raz z aktualnej ilości (magazyn + sprzedane)
        _total = (stats['sztuki'] or 0) + (sprzedaz_stats['szt_sprzedanych'] or 0)
        if _total > 0:
            _kj_netto = round(koszt_palety_netto / _total, 2)
            try:
                conn.execute('UPDATE palety SET koszt_jednostkowy = ? WHERE id = ?', (_kj_netto, paleta_id))
                conn.commit()
                print(f"💰 Auto-set koszt_jednostkowy palety #{paleta_id}: {_kj_netto:.2f} zł/szt netto")
            except:
                pass
    koszt_jednostkowy_netto = _kj_netto
    koszt_jednostkowy_brutto = round(_kj_netto * 1.23, 2) if _kj_netto > 0 else 0

    # Rzeczywiste dane sprzedaży
    # sprzedane_produkty = liczba produktów ze statusem 'sprzedany' (każdy = min 1 szt)
    # sprzedaz_stats = dane z tabeli sprzedaze (dokładniejsze - ma ilość sztuk)
    # sprzedano_offline = ile sprzedano poza Allegro (bez statystyk)
    
    sprzedano_szt_db = sprzedaz_stats['szt_sprzedanych'] or 0  # z tabeli sprzedaze
    sprzedane_produkty = stats['sprzedane_produkty'] or 0  # liczba produktów ze statusem sprzedany
    try:
        sprzedano_offline = stats['sprzedano_offline_suma'] or 0  # sprzedane poza Allegro
    except:
        sprzedano_offline = 0
    try:
        przychod_offline = stats['przychod_offline_suma'] or 0  # przychód ze sprzedaży offline
    except:
        przychod_offline = 0
    
    from modules.database import get_db as _gdb_cnt
    conn_cnt = _gdb_cnt()
    print(f"📊 STATS paleta #{paleta_id}:")
    print(f"   - sprzedano_szt_db (tabela sprzedaze): {sprzedano_szt_db}")
    print(f"   - sprzedane_produkty (status=sprzedany bez offline): {sprzedane_produkty}")
    print(f"   - sprzedano_offline (suma): {sprzedano_offline}")
    print(f"   - przychod_offline (suma): {przychod_offline}")
    
    # Suma wszystkich źródeł sprzedaży
    # sprzedano_szt_db (z tabeli sprzedaze) już zawiera offline
    # Dla produktów bez rekordu w sprzedaze (stare dane) - użyj sprzedano_offline
    # Unikaj podwójnego liczenia
    offline_w_sprzedaze = conn_cnt.execute('''
        SELECT COALESCE(SUM(s.ilosc),0) FROM sprzedaze s
        JOIN produkty p ON s.produkt_id=p.id
        WHERE p.paleta_id=? AND s.kupujacy='offline'
        AND COALESCE(s.status,'') NOT IN ('anulowana','anulowane','zwrot','')
    ''', (paleta_id,)).fetchone()[0] or 0
    conn_cnt.close()
    offline_bez_sprzedaze = max(0, sprzedano_offline - offline_w_sprzedaze)
    sprzedano_szt = sprzedano_szt_db + offline_bez_sprzedaze
    print(f"   - WYNIK sprzedano_szt = {sprzedano_szt_db} (sprzedaze) + {offline_bez_sprzedaze} (offline bez sprzedaze) = {sprzedano_szt}")
    
    # Przychód - preferuj dane z produkty (sprzedano_wartosc), bo tabela sprzedaze może być niekompletna
    przychod_z_produktow = stats['sprzedano_wartosc'] or 0  # SUM(cena_allegro) WHERE status='sprzedany'
    przychod_z_sprzedazy = sprzedaz_stats['przychod'] or 0  # SUM(cena*ilosc) z tabeli sprzedaze
    
    przychod_rzeczywisty = przychod_allegro_db + przychod_offline_sprzedaze + przychod_offline_stare
    przychod_z_sprzedazy = przychod_allegro_db + przychod_offline_sprzedaze  # dla printu
    
    print(f"📊 PRZYCHOD: z_produktow={przychod_z_produktow}, z_sprzedazy={przychod_z_sprzedazy}, offline={przychod_offline}, SUMA={przychod_rzeczywisty}")
    
    # Koszt sprzedanych = stały koszt/szt × ilość sprzedana
    wszystkie_szt = (stats['sztuki'] or 0) + sprzedano_szt
    if koszt_jednostkowy_brutto > 0:
        koszt_sprzedanych = sprzedano_szt * koszt_jednostkowy_brutto
    elif wszystkie_szt > 0 and koszt_palety_brutto > 0:
        koszt_sprzedanych = (sprzedano_szt / wszystkie_szt) * koszt_palety_brutto
    else:
        koszt_sprzedanych = 0
    
    zysk_rzeczywisty = przychod_rzeczywisty - koszt_sprzedanych
    
    # DEBUG: wyświetl info o każdym produkcie
    print(f"📦 PRODUKTY na palecie #{paleta_id}:")
    for p in produkty:
        try:
            offline_szt = p['sprzedano_offline'] or 0
        except:
            offline_szt = 0
        try:
            offline_przychod = p['przychod_offline'] or 0
        except:
            offline_przychod = 0
        nazwa = (p['nazwa'] or '')[:30]
        print(f"   - ID:{p['id']} | {nazwa} | status={p['status']} | ilosc={p['ilosc']} | offline_szt={offline_szt} | offline_przychod={offline_przychod}")
    
    produkty_html = ''
    for p in produkty:
        # Pobierz offline_szt dla tego produktu
        try:
            p_offline_szt = p['sprzedano_offline'] or 0
        except:
            p_offline_szt = 0
            
        if p['status'] == 'sprzedany':
            status_color = '#22c55e'
            status_icon = '✅'
            status_text = 'SPRZEDANY'
        elif p['status'] == 'wystawiony':
            status_color = '#3b82f6'
            status_icon = '🔵'
            status_text = 'WYSTAWIONY'
        elif p['status'] == 'magazyn':
            status_color = '#eab308'
            status_icon = '📦'
            status_text = 'MAGAZYN'
        else:
            status_color = '#64748b'
            status_icon = '⚪'
            status_text = 'NOWY'
        
        # Cena jednostkowa zakupu - STAŁA z palety (koszt_jednostkowy = netto)
        if koszt_jednostkowy_netto > 0:
            netto_szt = koszt_jednostkowy_netto
            brutto_szt = koszt_jednostkowy_brutto
            cena_glowna = f"{netto_szt:.2f} zł/szt netto (stała)"
            cena_dodatkowa = ""
        else:
            brutto_szt = 0
            netto_szt = 0
            cena_glowna = "brak - ustaw w edycji palety"
            cena_dodatkowa = ""
        
        stan_opcje = ''
        stany = ['Nowy', 'Nowy w otwartym opakowaniu', 'Używany', 'Uszkodzony', 'Odnowiony']
        for s in stany:
            sel = 'selected' if (p['stan'] or 'Nowy') == s else ''
            stan_opcje += f'<option value="{s}" {sel}>{s}</option>'

        status_opcje = ''
        statusy = [('magazyn','📦 Magazyn'),('wystawiony','🛒 Wystawiony'),('sprzedany','💰 Sprzedany'),('uszkodzony','⚠️ Uszkodzony'),('zwrot','↩️ Zwrot')]
        for sv, sl in statusy:
            sel = 'selected' if (p['status'] or 'magazyn') == sv else ''
            status_opcje += f'<option value="{sv}" {sel}>{sl}</option>'

        produkty_html += f'''
        <div style="background:#1e1e2e;border-radius:8px;padding:10px;margin-bottom:8px" data-produkt-id="{p['id']}" data-ilosc="{p['ilosc']}">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:8px">
                <div style="flex:1;min-width:0">
                    <div style="font-size:0.85rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                        <a href="/magazyn/produkt/{p['id']}" style="color:#fff;text-decoration:none">{p['nazwa'][:45]}</a>
                    </div>
                    <div style="font-size:0.7rem;color:#64748b;margin-top:2px;display:flex;align-items:center;gap:4px">
                        <span>{p['ean'] or p['asin'] or '—'} •</span>
                        <button onclick="szybkaMinus({p['id']},{p['ilosc']},{int(p['cena_allegro'] or 0)})" style="background:#ef4444;border:none;border-radius:4px;color:#fff;width:20px;height:20px;font-size:0.7rem;cursor:pointer;padding:0;line-height:20px" {'disabled' if (p['ilosc'] or 0) == 0 else ''}>-</button>
                        <span style="color:#fff;font-weight:600" id="ilosc-{p['id']}">{p['ilosc']}</span>
                        <button onclick="szybkaPlus({p['id']},{p['ilosc']})" style="background:#22c55e;border:none;border-radius:4px;color:#fff;width:20px;height:20px;font-size:0.7rem;cursor:pointer;padding:0;line-height:20px">+</button>
                        <span>szt • {p['lokalizacja'] or '—'}</span>
                    </div>
                    <div class="sztuki-dots"></div>
                </div>
                <div style="text-align:right;flex-shrink:0">
                    <div style="font-weight:600;color:#22c55e">{p['cena_allegro']:.0f} zł</div>
                    <div style="font-size:0.65rem;color:#ef4444">{cena_glowna}</div>
                </div>
            </div>

            <!-- INLINE EDYCJA STANU I STATUSU -->
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px">
                <div>
                    <div style="font-size:0.6rem;color:#64748b;margin-bottom:3px">🏷️ STAN</div>
                    <select onchange="zapiszPole({p['id']}, 'stan', this.value, this)"
                        style="width:100%;background:#12121a;border:1px solid #334155;border-radius:6px;color:#fff;padding:5px 6px;font-size:0.72rem">
                        {stan_opcje}
                    </select>
                </div>
                <div>
                    <div style="font-size:0.6rem;color:#64748b;margin-bottom:3px">📦 STATUS</div>
                    <select onchange="zapiszPole({p['id']}, 'status', this.value, this)"
                        style="width:100%;background:#12121a;border:1px solid #334155;border-radius:6px;color:#fff;padding:5px 6px;font-size:0.72rem">
                        {status_opcje}
                    </select>
                </div>
            </div>

            <div style="display:flex;gap:4px">
                <button type="button"
                   onclick="document.getElementById('korektaProduktId').value='{p['id']}';document.getElementById('korektaIlosc').value={p['ilosc'] or 0};document.getElementById('maxIlosc').value={p['ilosc'] or 0};document.getElementById('sprzedajIlosc').value=1;document.getElementById('sprzedajIlosc').max={p['ilosc'] or 0};document.getElementById('sprzedajCena').value='{int(p['cena_allegro'] or p['cena_brutto'] or 0)}';document.getElementById('offlineSzt').value='{int(p_offline_szt)}';var cs=document.getElementById('cofnijOfflineSection');if({int(p_offline_szt)}>0){{cs.style.display='block';document.getElementById('offlineInfo').textContent='{int(p_offline_szt)} szt.';document.getElementById('cofnijIlosc').value=1;document.getElementById('cofnijIlosc').max={int(p_offline_szt)}}}else{{cs.style.display='none'}};document.getElementById('modalKorekta').style.display='block'"
                   style="padding:6px 10px;background:#f97316;border:none;border-radius:6px;color:#fff;font-size:0.65rem;font-weight:600;cursor:pointer;flex:1">
                    ✏️ Korekta
                </button>
                <a href="/magazyn/produkt/{p['id']}/edytuj"
                   style="padding:6px 10px;background:#3b82f6;border-radius:6px;color:#fff;text-decoration:none;font-size:0.65rem;font-weight:600;text-align:center;flex:1">
                    🖊️ Edytuj
                </a>
                <button onclick="pokazMenu(event, {p['id']}, {p['ilosc']}, '{p['nazwa'][:30].replace(chr(39), chr(96)).replace(chr(34), chr(96))}', this)"
                   style="padding:6px 10px;background:#334155;border:none;border-radius:6px;color:#fff;font-size:0.65rem;font-weight:600;cursor:pointer;flex:1">
                    ⋯ Akcje
                </button>
            </div>
        </div>
        '''
    
    if not produkty_html:
        produkty_html = '<div style="text-align:center;color:#64748b;padding:20px">Brak produktów. Importuj Excel!</div>'
    
    # ROI liczony na podstawie wartości Allegro (wystawione + szkic), nie sprzedanych
    zysk_potencjalny = stats['wartosc'] - koszt_palety_brutto
    roi = (zysk_potencjalny / koszt_palety_brutto * 100) if koszt_palety_brutto > 0 else 0
    
    # Bezpieczne pobieranie regału
    try:
        regal_palety = paleta['regal'] if paleta['regal'] else ''
    except (KeyError, TypeError):
        regal_palety = ''
    
    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>📦 {paleta['nazwa'] or f"Paleta #{paleta['id']}"}</h1>
            <small>{paleta['dostawca']} • {paleta['data_zakupu']}</small>
            {f'<div style="margin-top:6px;font-size:0.85rem;color:#8b5cf6">📍 Regal: {regal_palety}</div>' if regal_palety else ''}
        </div>
        
        <!-- GŁÓWNE STATYSTYKI SPRZEDAŻY -->
        <div style="background:linear-gradient(135deg,#065f46,#064e3b);border:2px solid #10b981;border-radius:16px;padding:15px;margin-bottom:15px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                <div style="font-size:0.8rem;color:#6ee7b7;text-transform:uppercase;letter-spacing:1px">📊 SPRZEDAŻ Z PALETY</div>
                <div style="font-size:1.8rem;font-weight:800;color:#fff">{sprzedano_szt} <span style="font-size:0.9rem;color:#6ee7b7">/ {(stats['sztuki'] or 0) + sprzedano_szt} szt</span></div>
            </div>
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">
                <div style="text-align:center">
                    <div style="font-size:1.3rem;font-weight:700;color:#22c55e">{przychod_rzeczywisty:.0f} zł</div>
                    <div style="font-size:0.65rem;color:#6ee7b7">PRZYCHÓD</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:1.3rem;font-weight:700;color:#ef4444">-{koszt_sprzedanych:.0f} zł</div>
                    <div style="font-size:0.65rem;color:#6ee7b7">KOSZT</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:1.3rem;font-weight:700;color:{'#22c55e' if zysk_rzeczywisty >= 0 else '#ef4444'}">{zysk_rzeczywisty:+.0f} zł</div>
                    <div style="font-size:0.65rem;color:#6ee7b7">ZYSK</div>
                </div>
            </div>
        </div>
        
        <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:15px">
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.3rem;font-weight:700;color:#f97316">{koszt_palety_netto:.0f} zł</div>
                <div style="font-size:0.7rem;color:#64748b">KOSZT NETTO (STAŁY)</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.3rem;font-weight:700;color:#ef4444">{koszt_palety_brutto:.0f} zł</div>
                <div style="font-size:0.7rem;color:#64748b">KOSZT BRUTTO (STAŁY)</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.3rem;font-weight:700;color:#22c55e">{stats['wartosc']:.0f} zł</div>
                <div style="font-size:0.7rem;color:#64748b">WARTOŚĆ ALLEGRO</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.3rem;font-weight:700;color:#3b82f6">{stats['cnt']} <span style="font-size:0.8rem;color:#64748b">({stats['sztuki']} szt)</span></div>
                <div style="font-size:0.7rem;color:#64748b">PRODUKTÓW</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:12px;text-align:center">
                <div style="font-size:1.3rem;font-weight:700;color:#22c55e">{sprzedano_szt}</div>
                <div style="font-size:0.7rem;color:#64748b">SPRZEDANYCH</div>
            </div>
        </div>
        
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:15px">
            <div style="background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);border-radius:10px;padding:10px;text-align:center">
                <div style="font-size:1.1rem;font-weight:600;color:#22c55e">✅ {stats['wystawione'] or 0}</div>
                <div style="font-size:0.7rem;color:#64748b">WYSTAWIONE</div>
            </div>
            <div style="background:rgba(234,179,8,0.1);border:1px solid rgba(234,179,8,0.3);border-radius:10px;padding:10px;text-align:center">
                <div style="font-size:1.1rem;font-weight:600;color:#eab308">📦 {stats['magazyn'] or 0}</div>
                <div style="font-size:0.7rem;color:#64748b">W MAGAZYNIE</div>
            </div>
            <div style="background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:10px;padding:10px;text-align:center">
                <div style="font-size:1.1rem;font-weight:600;color:#ef4444">📊 {roi:.1f}%</div>
                <div style="font-size:0.7rem;color:#64748b">ROI</div>
            </div>
        </div>
        
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:15px">
            <a href="/palety/{paleta_id}/mass-edit" class="btn" style="display:block;padding:14px;background:linear-gradient(135deg,#8b5cf6,#7c3aed);border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600">✏️ MASOWE WYSTAWIANIE</a>
            <a href="/magazyn/import?paleta_id={paleta_id}" class="btn" style="display:block;padding:14px;background:#3b82f6;border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600">📥 IMPORTUJ EXCEL</a>
            <a href="/palety/{paleta_id}/edit" class="btn" style="display:block;padding:14px;background:linear-gradient(135deg,#f59e0b,#d97706);border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:600">⚙️ EDYTUJ PALETE</a>
        </div>
        
        <!-- PRZEKAZ ZYSK NA CEL -->
        ''' + ('''
        <form action="/goal/add-contribution" method="POST" style="background:linear-gradient(135deg,rgba(34,197,94,0.15),rgba(16,185,129,0.1));border:1px solid rgba(34,197,94,0.3);border-radius:12px;padding:15px;margin-bottom:15px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                <div>
                    <div style="font-weight:600;color:#22c55e;font-size:1.05rem">&#x1F697; Przekaz zysk na Hyundaia i30 N</div>
                    <div style="font-size:0.75rem;color:#647d8b;margin-top:3px">Potencjalny zysk: ''' + str(int(zysk_potencjalny)) + ''' PLN</div>
                </div>
            </div>
            <div style="display:flex;gap:10px;align-items:center">
                <input type="hidden" name="paleta_id" value="''' + str(paleta_id) + '''">
                <input type="hidden" name="description" value="Zysk z palety ''' + str(paleta['nazwa'] or paleta_id) + '''">
                <input type="number" name="amount" placeholder="Kwota PLN" required min="1" step="1"
                       value="''' + str(max(0, int(zysk_potencjalny))) + '''"
                       style="flex:1;padding:12px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem">
                <button type="submit" style="padding:12px 24px;background:#22c55e;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer;white-space:nowrap">&#x1F4B0; PRZEKAZ</button>
            </div>
        </form>
        ''' if zysk_potencjalny > 0 else '') + '''
        
        <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;margin-bottom:10px">PRODUKTY (''' + str(stats['cnt']) + ''')</div>
        
        ''' + produkty_html + '''
        
        <form method="POST" action="/palety/''' + str(paleta_id) + '''/delete" style="margin-top:20px" onsubmit="return confirm('⚠️ UWAGA!\\n\\nTo usunie tę paletę i wszystkie jej produkty (''' + str(stats['cnt']) + ''' szt.)\\n\\nNa pewno kontynuować?')">
            <button type="submit" style="width:100%;padding:12px;background:#ef4444;border:none;border-radius:10px;color:#fff;font-weight:600;cursor:pointer">
                🗑️ USUŃ PALETĘ
            </button>
        </form>
        
        <a href="/palety" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:15px">← Powrót do palet</a>
    </div>
    
    <!-- MODAL KOREKTY ILOŚCI -->
    <div id="modalKorekta" onclick="if(event.target===this)this.style.display='none'" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.8);z-index:1000;padding:20px">
        <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:16px;max-width:400px;margin:50px auto;padding:20px">
            <h3 style="color:#fff;margin:0 0 15px">✏️ Korekta produktu</h3>
            
            <!-- KOREKTA ILOŚCI: natywny HTML form → zero zależności od JS -->
            <form method="POST" action="/sprzedaze/korekta-ilosci">
                <input type="hidden" name="produkt_id" id="korektaProduktId" value="">
                <div style="margin-bottom:15px">
                    <label style="display:block;font-size:0.8rem;color:#64748b;margin-bottom:5px">Zmień ilość na:</label>
                    <input type="number" name="nowa_ilosc" id="korektaIlosc" min="0" style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #1e1e2e;border-radius:8px;color:#fff;font-size:1rem">
                </div>
                <div style="display:flex;gap:10px;margin-bottom:15px">
                    <button type="submit" style="flex:1;padding:12px;background:#3b82f6;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">💾 Zapisz ilość</button>
                    <button type="button" onclick="document.getElementById('modalKorekta').style.display='none'" style="padding:12px 16px;background:#64748b;border:none;border-radius:8px;color:#fff;cursor:pointer">✕</button>
                </div>
            </form>
            
            <div style="border-top:1px solid #1e1e2e;padding-top:15px;margin-top:10px">
                <label style="display:block;font-size:0.8rem;color:#f59e0b;margin-bottom:8px">📦 Sprzedaż offline (bez statystyk Allegro):</label>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
                    <div>
                        <label style="font-size:0.7rem;color:#64748b">Ile szt.:</label>
                        <input type="number" id="sprzedajIlosc" min="1" value="1" style="width:100%;padding:10px;background:#0a0a0f;border:1px solid #f59e0b;border-radius:8px;color:#fff;font-size:1rem;text-align:center">
                    </div>
                    <div>
                        <label style="font-size:0.7rem;color:#64748b">Cena sprzedaży (zł):</label>
                        <input type="number" id="sprzedajCena" min="0.01" step="0.01" required placeholder="Wpisz cenę zł" style="width:100%;padding:10px;background:#0a0a0f;border:2px solid #f59e0b;border-radius:8px;color:#fff;font-size:1rem;text-align:center">
                    </div>
                </div>
                <button onclick="oznaczSprzedany()" style="width:100%;padding:12px;background:#f59e0b;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">📦 Sprzedaj offline</button>
                <div style="font-size:0.65rem;color:#64748b;margin-top:6px;text-align:center">
                    Dolicza do przychodu palety, ale NIE do statystyk sprzedaży Allegro
                </div>
            </div>

            <!-- OLX / Vinted ukryte -->

            <!-- COFNIJ OFFLINE - widoczne tylko gdy są sprzedane offline -->
            <div id="cofnijOfflineSection" style="display:none;border-top:1px solid #1e1e2e;padding-top:15px;margin-top:15px">
                <label style="display:block;font-size:0.8rem;color:#ef4444;margin-bottom:8px">🔄 Cofnij sprzedaż offline:</label>
                <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px">
                    <span style="font-size:0.75rem;color:#64748b">Sprzedano offline:</span>
                    <span id="offlineInfo" style="color:#f59e0b;font-weight:600">0 szt.</span>
                </div>
                <div style="display:flex;gap:10px">
                    <input type="number" id="cofnijIlosc" min="1" value="1" style="flex:1;padding:10px;background:#0a0a0f;border:1px solid #ef4444;border-radius:8px;color:#fff;font-size:1rem;text-align:center">
                    <button onclick="cofnijOffline()" style="padding:12px 20px;background:#ef4444;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">🔄 Cofnij</button>
                </div>
            </div>
            
            <!-- COFNIJ SPRZEDAŻ - cofa wszystkie sprzedaże produktu -->
            <div style="border-top:1px solid #1e1e2e;padding-top:15px;margin-top:15px">
                <label style="display:block;font-size:0.8rem;color:#ef4444;margin-bottom:8px">🔄 Cofnij sprzedaż (przywróć do magazynu):</label>
                <button onclick="cofnijSprzedaz()" style="width:100%;padding:12px;background:#ef4444;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">🔄 Cofnij sprzedaż</button>
                <div style="font-size:0.65rem;color:#64748b;margin-top:6px;text-align:center">
                    Cofa sprzedaż, przywraca ilość i zmienia status produktu na magazyn
                </div>
            </div>

            <input type="hidden" id="maxIlosc">
            <input type="hidden" id="offlineSzt">
        </div>
    </div>
    
    <script>
    function pokazKorekta(produktId, aktualnaIlosc, cena, offlineSzt) {
        document.getElementById('korektaProduktId').value = produktId;
        document.getElementById('korektaIlosc').value = aktualnaIlosc;
        document.getElementById('maxIlosc').value = aktualnaIlosc;
        document.getElementById('sprzedajIlosc').value = 1;
        document.getElementById('sprzedajIlosc').max = aktualnaIlosc;
        document.getElementById('sprzedajCena').value = cena || '';
        document.getElementById('offlineSzt').value = offlineSzt || 0;
        const cofnijSection = document.getElementById('cofnijOfflineSection');
        if (offlineSzt && offlineSzt > 0) {
            cofnijSection.style.display = 'block';
            document.getElementById('offlineInfo').textContent = offlineSzt + ' szt.';
            document.getElementById('cofnijIlosc').value = 1;
            document.getElementById('cofnijIlosc').max = offlineSzt;
        } else {
            cofnijSection.style.display = 'none';
        }
        document.getElementById('modalKorekta').style.display = 'block';
    }

    // Event delegation - działa nawet gdy onclick jest zablokowane
    document.addEventListener('click', function(e) {
        const btn = e.target.closest('.btn-korekta');
        if (btn) {
            e.preventDefault();
            pokazKorekta(
                btn.dataset.pid,
                parseInt(btn.dataset.ilosc),
                parseInt(btn.dataset.cena),
                parseInt(btn.dataset.offline)
            );
        }
    });
    
    function zamknijModal() {
        document.getElementById('modalKorekta').style.display = 'none';
    }

    function zapiszPole(produktId, pole, wartosc, el) {
        const fd = new FormData();
        fd.append('pole', pole);
        fd.append('wartosc', wartosc);
        fetch('/produkt/' + produktId + '/szybka-edycja', {method: 'POST', body: fd})
            .then(r => r.json())
            .then(d => {
                if (d.ok) {
                    el.style.border = '1px solid #22c55e';
                    if (pole === 'status' || d.reload) {
                        setTimeout(() => location.reload(), 400);
                    } else {
                        setTimeout(() => el.style.border = '1px solid #334155', 1200);
                    }
                } else {
                    el.style.border = '1px solid #ef4444';
                    alert('Błąd: ' + d.msg);
                }
            })
            .catch(() => { el.style.border = '1px solid #ef4444'; });
    }
    
    function cofnijOffline() {
        const ilosc = document.getElementById('cofnijIlosc').value;
        const maxOffline = document.getElementById('offlineSzt').value;

        if (parseInt(ilosc) > parseInt(maxOffline)) {
            alert('Nie możesz cofnąć więcej niż sprzedano offline (' + maxOffline + ' szt.)');
            return;
        }

        if (!confirm('Cofnąć ' + ilosc + ' szt. ze sprzedaży offline?\\n\\n(Produkty wrócą do magazynu)')) return;

        const produktId = document.getElementById('korektaProduktId').value;
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '/produkt/cofnij-offline/' + produktId;
        const inp = document.createElement('input');
        inp.type = 'hidden'; inp.name = 'ilosc'; inp.value = ilosc;
        form.appendChild(inp);
        document.body.appendChild(form);
        form.submit();
    }

    function cofnijSprzedaz() {
        const produktId = document.getElementById('korektaProduktId').value;
        if (!confirm('Cofnąć sprzedaż tego produktu?\\n\\nProdukt wróci do magazynu, sprzedaż zostanie oznaczona jako zwrot.')) return;
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '/produkt/cofnij-sprzedaz/' + produktId;
        document.body.appendChild(form);
        form.submit();
    }
    
    function zapiszKorekta() {
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '/sprzedaze/korekta-ilosci';
        
        const produktId = document.createElement('input');
        produktId.name = 'produkt_id';
        produktId.value = document.getElementById('korektaProduktId').value;
        form.appendChild(produktId);
        
        const ilosc = document.createElement('input');
        ilosc.name = 'nowa_ilosc';
        ilosc.value = document.getElementById('korektaIlosc').value;
        form.appendChild(ilosc);
        
        document.body.appendChild(form);
        form.submit();
    }
    
    function oznaczSprzedany() {
        const ilosc = document.getElementById('sprzedajIlosc').value;
        const cena = document.getElementById('sprzedajCena').value || 0;
        const maxIlosc = document.getElementById('maxIlosc').value;
        
        if (parseInt(ilosc) > parseInt(maxIlosc)) {
            alert('Nie możesz sprzedać więcej niż masz w magazynie (' + maxIlosc + ' szt.)');
            return;
        }
        
        const przychod = (parseFloat(cena) * parseInt(ilosc)).toFixed(2);
        if (!cena || parseFloat(cena) <= 0) {
            alert('Podaj cenę sprzedaży (zł) — pole nie może być puste ani zerowe.');
            document.getElementById('sprzedajCena').focus();
            return;
        }
        if (!confirm('Sprzedaż offline:\\n\\n' + ilosc + ' szt. × ' + cena + ' zł = ' + przychod + ' zł\\n\\n(Doliczy do przychodu palety)')) return;
        
        const produktId = document.getElementById('korektaProduktId').value;
        
        const cenaFixed = String(cena).replace(',', '.');
        console.log('OFFLINE SALE:', produktId, 'ilosc=' + ilosc, 'cena=' + cenaFixed);
        if (parseFloat(cena) <= 0) {
            if (!confirm('Cena wynosi 0 zł - czy na pewno chcesz sprzedać za darmo?')) return;
        }
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '/produkt/oznacz-sprzedany/' + produktId;
        const inpIlosc = document.createElement('input');
        inpIlosc.type = 'hidden'; inpIlosc.name = 'ilosc'; inpIlosc.value = ilosc;
        form.appendChild(inpIlosc);
        const inpCena = document.createElement('input');
        inpCena.type = 'hidden'; inpCena.name = 'cena'; inpCena.value = cenaFixed;
        form.appendChild(inpCena);
        document.body.appendChild(form);
        form.submit();
    }
    
    // Zamknij modal klikając poza nim
    document.getElementById('modalKorekta').addEventListener('click', function(e) {
        if (e.target === this) zamknijModal();
    });

    function szybkaMinus(produktId, aktIlosc, cena) {
        if (aktIlosc <= 0) { alert('Brak sztuk do odjęcia'); return; }
        const nowaIlosc = aktIlosc - 1;
        if (!confirm('Odjąć 1 szt? (' + aktIlosc + ' → ' + nowaIlosc + ')')) return;
        _submitKorekta(produktId, nowaIlosc);
    }
    function szybkaPlus(produktId, aktIlosc) {
        _submitKorekta(produktId, aktIlosc + 1);
    }
    function _submitKorekta(produktId, nowaIlosc) {
        const f = document.createElement('form');
        f.method = 'POST'; f.action = '/sprzedaze/korekta-ilosci';
        f.innerHTML = '<input name="produkt_id" value="'+produktId+'"><input name="nowa_ilosc" value="'+nowaIlosc+'">';
        document.body.appendChild(f); f.submit();
    }
    </script>

    <!-- MODAL: ROZBIJ NA SZTUKI -->
    <div id="modalRozbij" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.8);z-index:1000;overflow-y:auto;padding:20px">
      <div style="background:#1e1e2e;border-radius:16px;padding:20px;max-width:440px;margin:0 auto">
        <div style="font-size:1.2rem;font-weight:700;margin-bottom:4px">🎯 Rozbij stan na sztuki</div>
        <div id="rozbijNazwa" style="color:#94a3b8;font-size:0.85rem;margin-bottom:15px"></div>
        <div style="background:#12121a;border-radius:10px;padding:12px;margin-bottom:15px">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px">
            <span style="color:#94a3b8">Łącznie sztuk:</span>
            <span id="rozbijLacznie" style="font-weight:700"></span>
          </div>
          <div style="display:flex;justify-content:space-between">
            <span style="color:#94a3b8">Suma wpisanych:</span>
            <span id="rozbijSuma" style="font-weight:700;color:#22c55e"></span>
          </div>
        </div>
        <div id="rozbijStany"></div>
        <div style="color:#94a3b8;font-size:0.75rem;margin:12px 0 8px">Szybkie ustawienie:</div>
        <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:15px">
          <button onclick="rozbijSzybko('Nowy')" style="padding:6px 12px;background:#22c55e22;border:1px solid #22c55e;border-radius:8px;color:#22c55e;font-size:0.78rem;cursor:pointer">🟢 Wszystko nowe</button>
          <button onclick="rozbijSzybko('Powystawowy')" style="padding:6px 12px;background:#3b82f622;border:1px solid #3b82f6;border-radius:8px;color:#3b82f6;font-size:0.78rem;cursor:pointer">🔵 Powystawowe</button>
          <button onclick="rozbijSzybko('Używany')" style="padding:6px 12px;background:#eab30822;border:1px solid #eab308;border-radius:8px;color:#eab308;font-size:0.78rem;cursor:pointer">🟡 Używane</button>
          <button onclick="rozbijSzybko('Uszkodzony')" style="padding:6px 12px;background:#ef444422;border:1px solid #ef4444;border-radius:8px;color:#ef4444;font-size:0.78rem;cursor:pointer">🔴 Uszkodzone</button>
        </div>
        <div style="display:flex;gap:8px">
          <button onclick="rozbijWyczysc()" style="flex:1;padding:12px;background:#1e1e2e;border:1px solid #334155;border-radius:10px;color:#94a3b8;cursor:pointer">Wyczyść</button>
          <button onclick="zamknijRozbij()" style="flex:1;padding:12px;background:#334155;border:none;border-radius:10px;color:#fff;cursor:pointer">Anuluj</button>
          <button onclick="zapiszRozbij()" style="flex:1;padding:12px;background:#22c55e;border:none;border-radius:10px;color:#000;font-weight:700;cursor:pointer">✓ Zapisz</button>
        </div>
      </div>
    </div>

    <!-- MODAL: DO NAPRAWY -->
    <div id="modalNaprawa" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.8);z-index:1000;overflow-y:auto;padding:20px">
      <div style="background:#1e1e2e;border-radius:16px;padding:20px;max-width:440px;margin:0 auto">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
          <div style="font-size:1.2rem;font-weight:700">🔧 Do naprawy</div>
          <button onclick="zamknijNaprawa()" style="background:none;border:none;color:#94a3b8;font-size:1.3rem;cursor:pointer">✕</button>
        </div>
        <div id="naprawaNazwa" style="color:#94a3b8;font-size:0.85rem;margin-bottom:15px"></div>
        <div id="naprawaLista"></div>
        <button onclick="zamknijNaprawa()" style="width:100%;padding:12px;background:#334155;border:none;border-radius:10px;color:#fff;margin-top:10px;cursor:pointer">Zamknij</button>
      </div>
    </div>

    <!-- MENU KONTEKSTOWE -->
    <div id="menuKontekst" style="display:none;position:fixed;z-index:2000;background:#1e1e2e;border:1px solid #334155;border-radius:12px;padding:8px;min-width:220px;box-shadow:0 8px 32px rgba(0,0,0,0.6)">
      <div id="menuNaglowek" style="color:#64748b;font-size:0.7rem;font-weight:600;padding:4px 10px;margin-bottom:4px"></div>
      <div id="menuStatusy"></div>
      <div style="color:#64748b;font-size:0.7rem;font-weight:600;padding:4px 10px;margin:4px 0;border-top:1px solid #334155;padding-top:8px">INNE AKCJE</div>
      <div id="menuInne"></div>
    </div>

    <script>
    let _rozbijId = null, _rozbijIlosc = 0;
    let _naprawaId = null;
    let _menuId = null;

    const STANY_KOLORY = {
      'Nowy': '#22c55e', 'Powystawowy': '#3b82f6',
      'Używany': '#eab308', 'Uszkodzony': '#ef4444', 'Odnowiony': '#8b5cf6'
    };

    // ---- ROZBIJ NA SZTUKI ----
    function pokazRozbij(produktId, ilosc, nazwa) {
        _rozbijId = produktId; _rozbijIlosc = ilosc;
        document.getElementById('rozbijNazwa').textContent = nazwa;
        document.getElementById('rozbijLacznie').textContent = ilosc;
        fetch('/api/sztuki/' + produktId)
          .then(r => r.json()).then(d => {
            const istniejace = {};
            (d.sztuki || []).forEach(s => { istniejace[s.stan] = (istniejace[s.stan]||0)+1; });
            renderRozbijStany(istniejace);
          }).catch(() => renderRozbijStany({}));
        document.getElementById('modalRozbij').style.display = 'block';
    }
    function renderRozbijStany(wartosci) {
        const stany = ['Nowy','Powystawowy','Używany','Uszkodzony'];
        let html = '';
        stany.forEach(s => {
            const kolor = STANY_KOLORY[s];
            const val = wartosci[s] || 0;
            html += `<div style="display:flex;align-items:center;gap:12px;background:${kolor}11;border:1px solid ${kolor}44;border-radius:10px;padding:12px;margin-bottom:8px">
              <div style="width:14px;height:14px;border-radius:50%;background:${kolor};flex-shrink:0"></div>
              <div style="flex:1;font-weight:600">${s}</div>
              <button onclick="zmienjRozbij('${s}',-1)" style="width:36px;height:36px;background:#1e1e2e;border:1px solid #334155;border-radius:8px;color:#fff;font-size:1.1rem;cursor:pointer">−</button>
              <input type="number" id="rozbij_${s}" value="${val}" min="0" max="${_rozbijIlosc}"
                style="width:60px;text-align:center;background:#12121a;border:1px solid #334155;border-radius:8px;color:#fff;padding:6px;font-size:1rem"
                oninput="aktualizujSume()">
              <button onclick="zmienjRozbij('${s}',1)" style="width:36px;height:36px;background:#1e1e2e;border:1px solid #334155;border-radius:8px;color:#fff;font-size:1.1rem;cursor:pointer">+</button>
            </div>`;
        });
        document.getElementById('rozbijStany').innerHTML = html;
        aktualizujSume();
    }
    function zmienjRozbij(stan, delta) {
        const el = document.getElementById('rozbij_' + stan);
        el.value = Math.max(0, parseInt(el.value||0) + delta);
        aktualizujSume();
    }
    function aktualizujSume() {
        const stany = ['Nowy','Powystawowy','Używany','Uszkodzony'];
        let suma = 0;
        stany.forEach(s => { suma += parseInt(document.getElementById('rozbij_'+s)?.value||0); });
        const el = document.getElementById('rozbijSuma');
        el.textContent = suma + ' / ' + _rozbijIlosc;
        el.style.color = suma === _rozbijIlosc ? '#22c55e' : '#ef4444';
    }
    function rozbijSzybko(stan) {
        ['Nowy','Powystawowy','Używany','Uszkodzony'].forEach(s => {
            const el = document.getElementById('rozbij_'+s);
            if(el) el.value = s === stan ? _rozbijIlosc : 0;
        });
        aktualizujSume();
    }
    function rozbijWyczysc() {
        ['Nowy','Powystawowy','Używany','Uszkodzony'].forEach(s => {
            const el = document.getElementById('rozbij_'+s);
            if(el) el.value = 0;
        });
        aktualizujSume();
    }
    function zamknijRozbij() { document.getElementById('modalRozbij').style.display='none'; }
    function zapiszRozbij() {
        const stany = ['Nowy','Powystawowy','Używany','Uszkodzony'];
        let suma = 0, podzial = {};
        stany.forEach(s => {
            const v = parseInt(document.getElementById('rozbij_'+s)?.value||0);
            if(v > 0) { podzial[s] = v; suma += v; }
        });
        if(suma !== _rozbijIlosc) { alert('Suma musi wynosić ' + _rozbijIlosc + ' sztuk!'); return; }
        fetch('/api/sztuki/' + _rozbijId + '/rozbij', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({podzial})
        }).then(r=>r.json()).then(d => {
            if(d.ok) { zamknijRozbij(); location.reload(); }
        });
    }

    // ---- NAPRAWA ----
    function pokazNaprawa(produktId, nazwa, ilosc) {
        _naprawaId = produktId;
        document.getElementById('naprawaNazwa').textContent = nazwa + ' — ' + ilosc + ' szt.';
        document.getElementById('naprawaLista').innerHTML = '<div style="color:#64748b;text-align:center;padding:20px">Ładowanie...</div>';
        document.getElementById('modalNaprawa').style.display = 'block';
        fetch('/api/sztuki/' + produktId).then(r=>r.json()).then(d => {
            renderNaprawaLista(d.sztuki || [], ilosc);
        });
    }
    function renderNaprawaLista(sztuki, ilosc) {
        // Fill missing units
        const pelna = [];
        for(let i=1; i<=ilosc; i++) {
            pelna.push(sztuki.find(s=>s.numer===i) || {id:null, numer:i, stan:'Nowy', status:'magazyn', opis_naprawy:''});
        }
        let html = '';
        pelna.forEach(s => {
            if(s.status === 'naprawa') {
                html += `<div style="background:#f59e0b15;border:1px solid #f59e0b55;border-radius:10px;padding:12px;margin-bottom:8px">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                    <div style="font-weight:700;color:#f59e0b">🔧 szt. ${s.numer} <span style="font-size:0.7rem">DO NAPRAWY</span></div>
                    <div style="display:flex;gap:6px">
                      <button onclick="edytujNaprawa(${s.id}, '${(s.opis_naprawy||'').replace(/'/g,"\'")}')"
                        style="padding:4px 10px;background:#8b5cf6;border:none;border-radius:6px;color:#fff;font-size:0.72rem;cursor:pointer">✏️ Edytuj</button>
                      <button onclick="cofnijNaprawa(${s.id})"
                        style="padding:4px 10px;background:#ef444422;border:1px solid #ef4444;border-radius:6px;color:#ef4444;font-size:0.72rem;cursor:pointer">↩ Cofnij</button>
                    </div>
                  </div>
                  <div style="background:#1e1e2e;border-radius:6px;padding:8px;font-size:0.8rem">📝 ${s.opis_naprawy || '—'}</div>
                  ${s.data_naprawy ? `<div style="font-size:0.7rem;color:#64748b;margin-top:4px">${s.data_naprawy}</div>` : ''}
                </div>`;
            } else {
                html += `<div style="background:#12121a;border:1px solid #1e1e2e;border-radius:10px;padding:12px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center">
                  <div style="display:flex;align-items:center;gap:10px">
                    <div style="width:12px;height:12px;border-radius:3px;background:${STANY_KOLORY[s.stan]||'#64748b'}"></div>
                    <span style="font-weight:600">szt. ${s.numer}</span>
                    <span style="font-size:0.72rem;color:#64748b">${s.stan}</span>
                  </div>
                  <button onclick="dodajNaprawa(${s.id || 0}, ${s.numer}, ${_naprawaId})"
                    style="padding:6px 14px;background:#f59e0b;border:none;border-radius:8px;color:#000;font-size:0.75rem;font-weight:700;cursor:pointer">+ Do naprawy</button>
                </div>`;
            }
        });
        document.getElementById('naprawaLista').innerHTML = html;
    }
    function dodajNaprawa(sztukiId, numer, produktId) {
        const opis = prompt('Opis usterki dla szt. ' + numer + ':');
        if(opis === null) return;
        const doSave = (id) => {
            fetch('/api/sztuki/jednostka/' + id + '/naprawa', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({opis})
            }).then(r=>r.json()).then(() => {
                fetch('/api/sztuki/' + produktId).then(r=>r.json()).then(d => {
                    const ilosc = parseInt(document.getElementById('naprawaNazwa').textContent.match(/\d+ szt/)[0]);
                    renderNaprawaLista(d.sztuki||[], ilosc);
                    location.reload();
                });
            });
        };
        if(sztukiId > 0) { doSave(sztukiId); }
        else {
            // Auto-create unit first
            fetch('/api/sztuki/' + produktId + '/rozbij', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({podzial:{'Nowy': parseInt(document.getElementById('naprawaNazwa').textContent.match(/\d+/)[0])}})
            }).then(r=>r.json()).then(() => {
                fetch('/api/sztuki/' + produktId).then(r=>r.json()).then(d => {
                    const szt = (d.sztuki||[]).find(s=>s.numer===numer);
                    if(szt) doSave(szt.id);
                });
            });
        }
    }
    function edytujNaprawa(id, opisCurrent) {
        const opis = prompt('Edytuj opis naprawy:', opisCurrent);
        if(opis === null) return;
        fetch('/api/sztuki/jednostka/' + id + '/naprawa', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({opis})
        }).then(() => location.reload());
    }
    function cofnijNaprawa(id) {
        fetch('/api/sztuki/jednostka/' + id + '/naprawa', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({cofnij: true})
        }).then(() => location.reload());
    }
    function zamknijNaprawa() { document.getElementById('modalNaprawa').style.display='none'; }

    // ---- MENU KONTEKSTOWE ----
    function pokazMenu(evt, produktId, ilosc, nazwa) {
        evt.stopPropagation();
        _menuId = produktId;
        const menu = document.getElementById('menuKontekst');
        document.getElementById('menuNaglowek').textContent = 'ZMIEŃ STATUS (dostępne: ' + ilosc + '/' + ilosc + ')';
        document.getElementById('menuStatusy').innerHTML = `
            <div onclick="menuStatus('sprzedany')" class="menu-item">✅ Sprzedane</div>
            <div onclick="menuStatus('sprzedany_uszkodzony')" class="menu-item">⚠️ Sprzedane uszkodzone</div>
            <div onclick="menuNaprawyModal(${produktId}, '${nazwa.replace(/'/g,"\'")}', ${ilosc})" class="menu-item">🔧 Do naprawy...</div>
            <div onclick="menuStatus('wyrzucenie')" class="menu-item">🗑️ Do wyrzucenia</div>
            <div onclick="menuStatus('zwrot')" class="menu-item">↩️ Oddane (zwrot)</div>`;
        document.getElementById('menuInne').innerHTML = `
            <div onclick="pokazRozbij(${produktId}, ${ilosc}, '${nazwa.replace(/'/g,"\'")}'); zamknijMenu()" class="menu-item">🎯 Rozbij na sztuki</div>
            <a href="/magazyn/produkt/${produktId}/edytuj" class="menu-item" style="text-decoration:none;display:block;color:#fff">✏️ Edytuj produkt</a>`;
        const rect = evt.target.getBoundingClientRect();
        menu.style.display = 'block';
        menu.style.top = (rect.bottom + window.scrollY + 4) + 'px';
        menu.style.left = Math.min(rect.left, window.innerWidth - 240) + 'px';
    }
    function menuStatus(status) {
        if(!_menuId) return;
        zapiszPole(_menuId, 'status', status, document.createElement('span'));
        zamknijMenu();
        setTimeout(() => location.reload(), 300);
    }
    function menuNaprawyModal(id, nazwa, ilosc) {
        zamknijMenu();
        pokazNaprawa(id, nazwa, ilosc);
    }
    function zamknijMenu() { document.getElementById('menuKontekst').style.display='none'; }
    document.addEventListener('click', zamknijMenu);

    // Style dla menu
    document.head.insertAdjacentHTML('beforeend', `<style>
    .menu-item { padding:10px 14px; cursor:pointer; border-radius:8px; font-size:0.88rem; }
    .menu-item:hover { background:#334155; }
    </style>`);

    // Kropki stanów na kartach
    const KOLORY_STAN = {'Nowy':'#22c55e','Powystawowy':'#3b82f6','Używany':'#eab308','Uszkodzony':'#ef4444','Odnowiony':'#8b5cf6'};
    document.querySelectorAll('[data-produkt-id]').forEach(el => {
        const pid = el.dataset.produktId;
        const ilosc = parseInt(el.dataset.ilosc || 0);
        if(ilosc < 1) return;
        fetch('/api/sztuki/' + pid).then(r=>r.json()).then(d => {
            if(!d.sztuki || d.sztuki.length === 0) return;
            const counts = {};
            const naprawy = d.sztuki.filter(s => s.status === 'naprawa').length;
            d.sztuki.forEach(s => { counts[s.stan] = (counts[s.stan]||0)+1; });
            let html = '<div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:4px">';
            Object.entries(counts).forEach(([k,v]) => {
                const kolor = KOLORY_STAN[k] || '#64748b';
                html += `<span style="background:${kolor}33;border:1px solid ${kolor};color:${kolor};border-radius:20px;padding:1px 7px;font-size:0.62rem;font-weight:700">●${k.slice(0,3)} ${v}</span>`;
            });
            if(naprawy > 0) {
                html += `<span style="background:#f9730333;border:1px solid #f97316;color:#f97316;border-radius:20px;padding:1px 7px;font-size:0.62rem;font-weight:700">🔧${naprawy}</span>`;
            }
            html += '</div>';
            const dotsEl = el.querySelector('.sztuki-dots');
            if(dotsEl) dotsEl.innerHTML = html;
        }).catch(()=>{});
    });
    </script>
    '''
    return html

@palety_bp.route('/produkt/<int:produkt_id>/szybka-edycja', methods=['POST'])
def produkt_szybka_edycja(produkt_id):
    """Szybka inline zmiana pola produktu (stan, status) z palety"""
    from modules.database import get_db
    # jsonify, request already imported at module level
    
    pole = request.form.get('pole', '').strip()
    wartosc = request.form.get('wartosc', '').strip()
    
    # Dozwolone pola do edycji inline
    DOZWOLONE = {'stan', 'status', 'lokalizacja', 'cena_allegro'}
    if pole not in DOZWOLONE:
        return jsonify({'ok': False, 'msg': 'Niedozwolone pole'}), 400
    
    conn = get_db()
    p = conn.execute('SELECT id, ilosc, status, nazwa, cena_allegro, cena_brutto FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not p:
        return jsonify({'ok': False, 'msg': 'Nie znaleziono'}), 404

    # Zabezpieczenie integralności: zmiana statusu na 'sprzedany' → zeruj ilosc + dodaj sprzedaz
    if pole == 'status' and wartosc == 'sprzedany' and (p['ilosc'] or 0) > 0:
        ilosc_sprzedana = p['ilosc'] or 1
        cena = float(p['cena_allegro'] or p['cena_brutto'] or 0)
        conn.execute('UPDATE produkty SET status = ?, ilosc = 0 WHERE id = ?', (wartosc, produkt_id))
        # Utwórz rekord sprzedaży żeby itemy nie "znikały" ze statystyk palety
        if cena > 0:
            from datetime import datetime
            conn.execute(
                '''INSERT INTO sprzedaze (produkt_id, nazwa, cena, ilosc, status, data_sprzedazy, kupujacy, notified)
                   VALUES (?, ?, ?, ?, 'sprzedana', ?, 'inline', 1)''',
                (produkt_id, p['nazwa'] or f'Produkt #{produkt_id}', cena, ilosc_sprzedana,
                 datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))
            )
        conn.commit()
        return jsonify({'ok': True, 'msg': f'Sprzedano {ilosc_sprzedana} szt.', 'reload': True})

    # Zabezpieczenie: zmiana statusu z 'sprzedany' na inny → jeśli ilosc=0, to ostrzeżenie
    if pole == 'status' and p['status'] == 'sprzedany' and wartosc != 'sprzedany' and (p['ilosc'] or 0) == 0:
        return jsonify({'ok': False, 'msg': 'Produkt ma ilość 0 — najpierw skoryguj ilość'}), 400

    conn.execute('UPDATE produkty SET ' + pole + ' = ? WHERE id = ?', (wartosc, produkt_id))
    conn.commit()
    return jsonify({'ok': True})

@palety_bp.route('/palety/<int:paleta_id>/delete', methods=['POST'])
def paleta_delete(paleta_id):
    """Usuwa pojedynczą paletę i wszystkie jej produkty"""
    from modules.database import get_db
    
    conn = get_db()
    
    # Pobierz ASIN-y produktów do usunięcia ze scraped
    asiny = conn.execute('SELECT asin FROM produkty WHERE paleta_id = ? AND asin IS NOT NULL', (paleta_id,)).fetchall()
    asiny_list = [row[0] for row in asiny if row[0]]
    
    # Usuń produkty z palety ze scraped (Paletomat)
    scraped_cnt = 0
    if asiny_list:
        placeholders = ','.join(['?' for _ in asiny_list])
        scraped_cnt = conn.execute('DELETE FROM scraped WHERE asin IN (' + placeholders + ')', asiny_list).rowcount
    
    # Usuń produkty z palety
    produkty_cnt = conn.execute('DELETE FROM produkty WHERE paleta_id = ?', (paleta_id,)).rowcount
    
    # Usuń paletę
    conn.execute('DELETE FROM palety WHERE id = ?', (paleta_id,))
    conn.commit()
    
    return f'''
    <html><head><meta http-equiv="refresh" content="2;url=/palety"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px">✅</div>
            <div style="font-size:1.2rem">Paleta usunięta!</div>
            <div style="color:#64748b;margin-top:10px">
                Usunięto {produkty_cnt} produktów{f' i {scraped_cnt} z Palatomatu' if scraped_cnt > 0 else ''}
            </div>
        </div>
    </body></html>
    '''

