"""
Moduł wysyłek — routes dla /wysylki/*
"""
from flask import Blueprint, request, redirect, session, flash, jsonify, Response, current_app, render_template, render_template_string, make_response
from datetime import datetime
from collections import defaultdict
import time as _time
import json

wysylki_bp = Blueprint('wysylki', __name__)

# ============================================================
# CACHE zamówień Allegro
# ============================================================
_wysylki_cache = {'data': None, 'timestamp': 0, 'raw': None}
_WYSYLKI_CACHE_TTL = 120  # 2 minuty

def _pobierz_zamowienia_allegro(force_refresh=False):
    """Pobiera zamówienia z Allegro API z cache (2 min TTL)"""
    import time as _time
    from modules.database import get_db
    from modules.allegro_api import get_orders, is_authenticated

    now = _time.time()
    if not force_refresh and _wysylki_cache['data'] is not None and (now - _wysylki_cache['timestamp']) < _WYSYLKI_CACHE_TTL:
        return _wysylki_cache['data'], _wysylki_cache['raw']

    zamowienia = []
    produkty_cnt = 0
    wartosc = 0
    raw_orders = None

    # Pobieramy z LOKALNEJ BAZY (status='nowa') zamiast z Allegro API
    # Dzięki temu oznaczone jako wysłane w bazie znikają z listy
    conn = get_db()
    rows = conn.execute('''
        SELECT s.id, s.allegro_order_id, s.nazwa, s.cena, s.ilosc, s.kupujacy,
               s.data_sprzedazy, s.adres, s.produkt_id,
               p.lokalizacja, p.regal, p.zdjecie_url
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        WHERE s.status IN ('nowa', 'nadana')
        ORDER BY s.data_sprzedazy DESC
    ''').fetchall()

    # Grupuj po allegro_order_id (jedno zamówienie = wiele produktów)
    orders_map = {}
    for row in rows:
        oid = row['allegro_order_id'] or f"LOCAL-{row['id']}"
        if oid not in orders_map:
            orders_map[oid] = {
                'order_id': oid,
                'order_id_short': oid[:8] if oid else '',
                'buyer': row['kupujacy'] or 'Nieznany',
                'date': (row['data_sprzedazy'] or '')[:10],
                'address': row['adres'] or 'Brak adresu',
                'pickup_point': '',
                'produkty': [],
                'total_sum': 0
            }
        lok = row['lokalizacja'] or row['regal'] or ''
        name = row['nazwa'] or 'Produkt'
        qty = row['ilosc'] or 1
        price = row['cena'] or 0
        orders_map[oid]['total_sum'] += price * qty
        produkty_cnt += qty
        orders_map[oid]['produkty'].append({
            'name': name,
            'name_short': name[:50] + '...' if len(name) > 50 else name,
            'qty': qty, 'price': price,
            'lokalizacja': lok,
            'zdjecie_url': row['zdjecie_url'] or ''
        })

    for o in orders_map.values():
        o['total'] = f"{o['total_sum']:.0f}"
        wartosc += o['total_sum']
        zamowienia.append(o)

    raw_orders = None

    result = {'zamowienia': zamowienia, 'produkty_cnt': produkty_cnt, 'wartosc': f"{wartosc:.0f}"}
    _wysylki_cache['data'] = result
    _wysylki_cache['raw'] = raw_orders
    _wysylki_cache['timestamp'] = now
    return result, raw_orders

def _zwroc_zamowienie_full(order):
    """Helper - formatuje odpowiedź z WSZYSTKIMI produktami zamówienia (np. ze skanowanej etykiety)"""
    from modules.database import get_db
    order_id = order.get('id', '')
    buyer = order.get('buyer', {}).get('login', 'Nieznany')

    delivery = order.get('delivery', {})
    address_data = delivery.get('address', {})
    address = ', '.join([p for p in [
        address_data.get('street', ''), address_data.get('city', ''), address_data.get('zipCode', '')
    ] if p])
    pickup_point = ''
    if delivery.get('pickupPoint'):
        pp = delivery.get('pickupPoint', {})
        pickup_point = f"{pp.get('name', '')} - {pp.get('address', {}).get('street', '')}"

    total = sum(float(i.get('price', {}).get('amount', 0)) * int(i.get('quantity', 1))
               for i in order.get('lineItems', []))

    # Zbierz WSZYSTKIE produkty z lokalizacjami i zdjęciami
    conn = get_db()
    produkty = []
    for item in order.get('lineItems', []):
        offer_id = item.get('offer', {}).get('id', '')
        name = item.get('offer', {}).get('name', 'Produkt')
        qty = int(item.get('quantity', 1))
        lokalizacja = ''
        zdjecie_url = ''
        if offer_id:
            # Szukaj po tabeli oferty
            p = conn.execute('''
                SELECT p.lokalizacja, p.regal, p.zdjecie_url
                FROM produkty p JOIN oferty o ON o.produkt_id = p.id
                WHERE o.allegro_id = ? LIMIT 1
            ''', (offer_id,)).fetchone()
            if not p:
                # Fallback: szukaj po nazwie produktu (fuzzy match)
                words = [w for w in name.split()[:4] if len(w) > 2]
                if words:
                    like = '%' + '%'.join(words[:3]) + '%'
                    p = conn.execute('''
                        SELECT lokalizacja, regal, zdjecie_url
                        FROM produkty WHERE nazwa LIKE ? AND ilosc > 0 LIMIT 1
                    ''', (like,)).fetchone()
            if p:
                lokalizacja = p['lokalizacja'] or p['regal'] or ''
                zdjecie_url = p['zdjecie_url'] or ''
        produkty.append({
            'nazwa': name[:60],
            'qty': qty,
            'lokalizacja': lokalizacja,
            'zdjecie_url': zdjecie_url
        })

    return jsonify({
        'zamowienie': {
            'order_id': order_id,
            'buyer': buyer,
            'address': address or 'Brak adresu',
            'pickup_point': pickup_point,
            'total': f"{total:.0f}",
            'produkt_nazwa': produkty[0]['nazwa'] if produkty else '',
            'inne_produkty': len(produkty) - 1,
            'produkty': produkty,
            'lokalizacja': produkty[0]['lokalizacja'] if produkty else None,
            'asin': None, 'ean': None, 'stan_magazynowy': None
        }
    })


def _zwroc_zamowienie(order, item, produkt_z_bazy):
    """Helper - formatuje odpowiedź z zamówieniem"""
    order_id = order.get('id', '')
    buyer = order.get('buyer', {}).get('login', 'Nieznany')
    offer_name = item.get('offer', {}).get('name', '')

    delivery = order.get('delivery', {})
    address_data = delivery.get('address', {})
    address = ', '.join([p for p in [
        address_data.get('street', ''),
        address_data.get('city', ''),
        address_data.get('zipCode', '')
    ] if p])

    pickup_point = ''
    if delivery.get('pickupPoint'):
        pp = delivery.get('pickupPoint', {})
        pickup_point = f"{pp.get('name', '')} - {pp.get('address', {}).get('street', '')}"

    total = sum(float(i.get('price', {}).get('amount', 0)) * int(i.get('quantity', 1))
               for i in order.get('lineItems', []))

    inne_produkty = len(order.get('lineItems', [])) - 1

    return jsonify({
        'zamowienie': {
            'order_id': order_id,
            'buyer': buyer,
            'address': address or 'Brak adresu',
            'pickup_point': pickup_point,
            'total': f"{total:.0f}",
            'produkt_nazwa': offer_name[:60],
            'inne_produkty': inne_produkty,
            'asin': produkt_z_bazy['asin'] if produkt_z_bazy else None,
            'ean': produkt_z_bazy['ean'] if produkt_z_bazy else None,
            'lokalizacja': (produkt_z_bazy['lokalizacja'] or produkt_z_bazy['regal']) if produkt_z_bazy else None,
            'stan_magazynowy': produkt_z_bazy['ilosc'] if produkt_z_bazy else None
        }
    })


# ============================================================
# ROUTES
# ============================================================

@wysylki_bp.route('/wysylki/allegro')
def wysylki_allegro():
    """Lista zamówień do wysłania z Allegro API z lokalizacjami produktów"""
    VERSION = current_app.config.get('VERSION', '')
    force = request.args.get('refresh', '') == '1'

    # Przy odświeżeniu — najpierw sync z Allegro (aktualizuje statusy wysłanych)
    if force:
        try:
            from modules.allegro_api import sync_orders
            print(f"[Wysylki] START sync...")
            result = sync_orders(today_only=False)  # Sync cały miesiąc
            print(f"[Wysylki] DONE sync: {result}")
        except Exception as e:
            import traceback
            print(f"[Wysylki] Sync error: {e}")
            traceback.print_exc()

    result, _ = _pobierz_zamowienia_allegro(force_refresh=force)

    return render_template('wysylki.html',
        version=VERSION,
        zamowienia=result['zamowienia'],
        zamowienia_cnt=len(result['zamowienia']),
        produkty_cnt=result['produkty_cnt'],
        wartosc=result['wartosc'],
        active_wysylki='active', active_home='', active_magazyn='',
        active_paletomat='', active_allegro='', active_monitor='', active_narzedzia=''
    )

@wysylki_bp.route('/wysylki/sync')
def wysylki_sync():
    """Odświeża zamówienia z Allegro"""
    from modules.allegro_api import sync_orders
    sync_orders(today_only=False, notify=False)
    return redirect('/wysylki/allegro')


@wysylki_bp.route('/wysylki/pakowanie')
def wysylki_pakowanie():
    """Stacja pakowania ze skanerem"""
    VERSION = current_app.config.get('VERSION', '')
    return render_template('pakowanie.html',
        version=VERSION,
        active_wysylki='active', active_home='', active_magazyn='',
        active_paletomat='', active_allegro='', active_monitor='', active_narzedzia='')


@wysylki_bp.route('/api/wysylki/szukaj')
def api_wysylki_szukaj():
    """API - szuka zamówienia po EAN/ASIN/nazwie/order_id (z cache)"""
    from modules.database import get_db
    from modules.allegro_api import is_authenticated

    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'error': 'Podaj EAN, ASIN, nazwę lub zeskanuj etykietę'})

    if not is_authenticated():
        return jsonify({'error': 'Nie zalogowano do Allegro'})

    print(f"🔍 Szukam zamówienia dla: {q}")

    # Pobierz zamówienia z cache (szybko!)
    result, raw_orders = _pobierz_zamowienia_allegro()

    if not raw_orders or 'checkoutForms' not in raw_orders:
        return jsonify({'error': 'Brak zamówień do wysłania'})

    q_lower = q.lower().strip()

    # === 1. Szukaj po ORDER ID (etykieta wysyłkowa) ===
    for order in raw_orders.get('checkoutForms', []):
        order_id = order.get('id', '')
        # Dopasuj pełny order_id lub jego fragment (min 8 znaków)
        if order_id and (q_lower == order_id.lower() or
                        (len(q) >= 8 and q_lower in order_id.lower()) or
                        order_id.lower().startswith(q_lower)):
            print(f"   → ✅ Znaleziono po order_id: {order_id}")
            items = order.get('lineItems', [])
            item = items[0] if items else {}
            return _zwroc_zamowienie_full(order)

    # === 2. Szukaj po EAN/ASIN w bazie danych ===
    conn = get_db()
    produkt_z_bazy = conn.execute('''
        SELECT p.id, p.nazwa, p.ean, p.asin, p.ilosc, p.lokalizacja, p.regal, p.zdjecie_url,
               o.allegro_id, o.tytul
        FROM produkty p
        LEFT JOIN oferty o ON o.produkt_id = p.id
        WHERE p.ean = ? OR p.asin = ? OR LOWER(p.asin) = LOWER(?) OR p.kod_magazynowy = ?
        LIMIT 1
    ''', (q, q, q, q.upper())).fetchone()

    if produkt_z_bazy:
        print(f"   → Znaleziono w bazie: {produkt_z_bazy['nazwa'][:40]}")

    # === 3. Szukaj w zamówieniach po allegro_id / frazie ===
    szukane_allegro_ids = []
    szukane_frazy = [q_lower]

    if produkt_z_bazy:
        if produkt_z_bazy['allegro_id']:
            szukane_allegro_ids.append(str(produkt_z_bazy['allegro_id']))
        if produkt_z_bazy['tytul']:
            szukane_frazy.append(produkt_z_bazy['tytul'].lower()[:30])
        if produkt_z_bazy['nazwa']:
            words = produkt_z_bazy['nazwa'].split()[:3]
            for word in words:
                if len(word) > 3:
                    szukane_frazy.append(word.lower())

    for order in raw_orders.get('checkoutForms', []):
        for item in order.get('lineItems', []):
            offer_name = item.get('offer', {}).get('name', '')
            offer_id = str(item.get('offer', {}).get('id', ''))

            if offer_id in szukane_allegro_ids:
                print(f"   → ✅ Znaleziono po allegro_id: {offer_id}")
                return _zwroc_zamowienie(order, item, produkt_z_bazy)

            for fraza in szukane_frazy:
                if len(fraza) > 3 and fraza in offer_name.lower():
                    print(f"   → ✅ Znaleziono po frazie '{fraza}'")
                    return _zwroc_zamowienie(order, item, produkt_z_bazy)

    if produkt_z_bazy:
        return jsonify({
            'error': f'Produkt "{produkt_z_bazy["nazwa"][:40]}" (stan: {produkt_z_bazy["ilosc"]} szt.) - brak zamówienia do wysłania',
            'produkt': {
                'nazwa': produkt_z_bazy['nazwa'],
                'asin': produkt_z_bazy['asin'],
                'ean': produkt_z_bazy['ean'],
                'ilosc': produkt_z_bazy['ilosc'],
                'lokalizacja': produkt_z_bazy['lokalizacja'] or produkt_z_bazy['regal']
            }
        })

    return jsonify({'error': f'Nie znaleziono: {q}'})


@wysylki_bp.route('/wysylki/nadaj/<order_id>')
def wysylki_nadaj(order_id):
    """Tworzy przesyłkę (jeśli nie istnieje) i zwraca etykietę PDF"""
    from modules.allegro_api import create_and_get_label
    
    print(f"🖨️ Nadawanie przesyłki dla zamówienia: {order_id}")
    
    # Spróbuj utworzyć przesyłkę i pobrać etykietę
    label_pdf, shipment_id, error = create_and_get_label(order_id)
    
    if error:
        return f'''
        <html>
        <head><meta charset="utf-8"><title>Błąd</title></head>
        <body style="font-family:sans-serif;padding:40px;background:#12121a;color:#fff">
            <h2>❌ Błąd nadawania przesyłki</h2>
            <p style="color:#ef4444">{error}</p>
            <p>Zamówienie: {order_id[:8]}...</p>
            <p style="color:#64748b;font-size:0.9rem;margin-top:20px">Możliwe przyczyny:</p>
            <ul style="color:#64748b;font-size:0.85rem">
                <li>Brak uprawnień API do tworzenia przesyłek</li>
                <li>Zamówienie już ma nadaną przesyłkę ręcznie</li>
                <li>Problem z metodą dostawy</li>
            </ul>
            <a href="https://allegro.pl/moje-allegro/sprzedaz/zamowienia/{order_id}" target="_blank" style="display:inline-block;margin:20px 0;padding:12px 20px;background:#3b82f6;color:#fff;text-decoration:none;border-radius:8px;font-weight:600">📦 Nadaj ręcznie na Allegro →</a><br>
            <a href="/wysylki" style="color:#64748b">← Powrót do wysyłek</a>
        </body>
        </html>
        ''', 400
    
    if label_pdf:
        print(f"   → ✅ Etykieta gotowa! Rozmiar: {len(label_pdf)} bytes")
        # Zwróć PDF do druku
        response = make_response(label_pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename=etykieta_{order_id[:8]}.pdf'
        return response
    else:
        # Przesyłka utworzona ale brak etykiety
        return f'''
        <html>
        <head><meta charset="utf-8"><title>Przesyłka utworzona</title></head>
        <body style="font-family:sans-serif;padding:40px;background:#12121a;color:#fff">
            <h2>✅ Przesyłka utworzona!</h2>
            <p>ID przesyłki: {shipment_id}</p>
            <p style="color:#f59e0b">Etykieta może być niedostępna od razu. Spróbuj pobrać za chwilę.</p>
            <a href="/wysylki/etykieta/{order_id}" style="display:inline-block;margin:20px 0;padding:12px 20px;background:#22c55e;color:#fff;text-decoration:none;border-radius:8px;font-weight:600">🖨️ Pobierz etykietę</a><br>
            <a href="/wysylki" style="color:#64748b">← Powrót do wysyłek</a>
        </body>
        </html>
        '''


@wysylki_bp.route('/wysylki/etykieta/<order_id>')
def wysylki_etykieta(order_id):
    """Pobiera etykietę PDF dla istniejącej przesyłki"""
    from modules.allegro_api import get_shipment_label
    
    label_pdf, shipment_id, error = get_shipment_label(order_id)
    
    if error == "BRAK_PRZESYLKI":
        return f'''
        <html>
        <head><meta charset="utf-8"><title>Brak przesyłki</title></head>
        <body style="font-family:sans-serif;padding:40px;background:#12121a;color:#fff">
            <h2>📦 Przesyłka nie została jeszcze nadana</h2>
            <p>Najpierw nadaj przesyłkę na Allegro, potem wróć po etykietę.</p>
            <a href="https://allegro.pl/moje-allegro/sprzedaz/zamowienia/{order_id}" target="_blank" style="display:inline-block;margin:20px 0;padding:12px 20px;background:#3b82f6;color:#fff;text-decoration:none;border-radius:8px;font-weight:600">📦 Nadaj na Allegro →</a><br>
            <a href="/wysylki" style="color:#64748b">← Powrót do wysyłek</a>
        </body>
        </html>
        '''
    
    if error:
        return f'''
        <html>
        <head><meta charset="utf-8"><title>Błąd</title></head>
        <body style="font-family:sans-serif;padding:40px;background:#12121a;color:#fff">
            <h2>❌ Błąd pobierania etykiety</h2>
            <p style="color:#ef4444">{error}</p>
            <a href="https://allegro.pl/moje-allegro/sprzedaz/zamowienia/{order_id}" target="_blank" style="color:#3b82f6;display:block;margin:20px 0">📦 Pobierz etykietę na Allegro →</a>
            <a href="/wysylki" style="color:#64748b">← Powrót do wysyłek</a>
        </body>
        </html>
        ''', 400
    
    response = make_response(label_pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=etykieta_{order_id[:8]}.pdf'
    return response


# ============================================================
# WYSŁKI (Widok dla dziadka)
# ============================================================

@wysylki_bp.route('/wysylki/wyczysc-all')
def wysylki_wyczysc_all():
    """Oznacza WSZYSTKIE zamówienia ze statusem 'nowa'/'nowe' jako wysłane"""
    from modules.database import get_db
    conn = get_db()
    cnt = conn.execute('''
        UPDATE sprzedaze SET status = 'wyslana'
        WHERE status IN ('nowa', 'nowe', 'nadana')
    ''').rowcount
    conn.commit()
    print(f"🗑️ Wyczyszczono {cnt} zamówień → wyslana")
    return redirect('/wysylki/allegro')


@wysylki_bp.route('/wysylki/debug-sync')
def wysylki_debug_sync():
    """Diagnostyka: pokaż co jest w bazie + sync z logami"""
    from modules.database import get_db
    conn = get_db()

    # Pokaż statusy w DB
    stats = conn.execute('''
        SELECT status, COUNT(*) as cnt FROM sprzedaze
        GROUP BY status ORDER BY cnt DESC
    ''').fetchall()

    # Pokaż zamówienia 'nowa'
    nowe = conn.execute('''
        SELECT id, allegro_order_id, nazwa, cena, kupujacy, data_sprzedazy, status
        FROM sprzedaze WHERE status IN ('nowa', 'nowe', 'nadana')
        ORDER BY data_sprzedazy DESC
    ''').fetchall()

    result = {
        'statusy': {r['status']: r['cnt'] for r in stats},
        'nowe_zamowienia': [{
            'id': r['id'],
            'order_id': r['allegro_order_id'] or '',
            'nazwa': (r['nazwa'] or '')[:50],
            'cena': r['cena'],
            'kupujacy': r['kupujacy'],
            'data': r['data_sprzedazy'],
            'status': r['status']
        } for r in nowe]
    }

    return jsonify(result)


@wysylki_bp.route('/wysylki/wyslano/<int:id>', methods=['POST'])
def wysylki_wyslano(id):
    """Oznacza zamówienie jako wysłane"""
    from modules.database import get_db, add_historia
    conn = get_db()
    
    # Pobierz dane sprzedaży
    sprzedaz = conn.execute('SELECT * FROM sprzedaze WHERE id = ?', (id,)).fetchone()
    
    # Oznacz jako wysłane
    conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('wyslana', id))
    conn.commit()
    
    # Dodaj historię do produktu jeśli jest powiązany
    if sprzedaz and sprzedaz['produkt_id']:
        add_historia(sprzedaz['produkt_id'], 'wyslano', f'Wysłano do klienta: {sprzedaz["kupujacy"] or "—"}', 
            {'kupujacy': sprzedaz['kupujacy'], 'cena': sprzedaz['cena']})
    
    return redirect('/wysylki')


@wysylki_bp.route('/wysylki/cofnij/<int:id>', methods=['POST'])
def wysylki_cofnij(id):
    """Cofa status wysłania"""
    from modules.database import get_db
    conn = get_db()
    conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('nowa', id))
    conn.commit()
    return redirect('/wysylki')


# ============================================================
# SYSTEM WYSŁEK - CHECKBOXY I BULK ACTIONS
# ============================================================

@wysylki_bp.route('/wysylki')
def wysylki_lista():
    """Lista zamówień do wysyłki z checkboxami (status='nowa') - GRUPOWANE PO ZAMÓWIENIU"""
    from modules.database import get_db
    VERSION = current_app.config.get('VERSION', '')
    from collections import defaultdict
    
    # Pobierz filtr użytkownika z parametru URL
    user_filter = request.args.get('user', '')
    
    conn = get_db()
    
    # Pobierz listę dostępnych użytkowników (dostawców)
    users = conn.execute('''
        SELECT DISTINCT p.dostawca 
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        WHERE s.status IN ('nowa', 'nadana') AND p.dostawca IS NOT NULL AND p.dostawca != ''
        ORDER BY p.dostawca
    ''').fetchall()
    users_list = [u['dostawca'] for u in users]
    
    # Query z filtrem użytkownika - pobieramy też nazwę z oferty i sprzedaży
    if user_filter and user_filter != 'wszyscy':
        zamowienia = conn.execute('''
            SELECT s.*, 
                   COALESCE(p.nazwa, s.nazwa, 'Produkt') as produkt_nazwa, 
                   p.lokalizacja, p.dostawca, p.ean, p.asin,
                   COALESCE(o.tytul, s.nazwa, '') as oferta_tytul
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            LEFT JOIN oferty o ON s.oferta_id = o.id
            WHERE s.status IN ('nowa', 'nadana') AND p.dostawca = ?
            ORDER BY s.allegro_order_id DESC, s.data_sprzedazy DESC
        ''', (user_filter,)).fetchall()
    else:
        zamowienia = conn.execute('''
            SELECT s.*, 
                   COALESCE(p.nazwa, s.nazwa, 'Produkt') as produkt_nazwa, 
                   p.lokalizacja, p.dostawca, p.ean, p.asin,
                   COALESCE(o.tytul, s.nazwa, '') as oferta_tytul
            FROM sprzedaze s
            LEFT JOIN produkty p ON s.produkt_id = p.id
            LEFT JOIN oferty o ON s.oferta_id = o.id
            WHERE s.status IN ('nowa', 'nadana')
            ORDER BY s.allegro_order_id DESC, s.data_sprzedazy DESC
        ''').fetchall()
    
    
    # Uzupełnij brakujące lokalizacje — konwertuj na dict i szukaj po słowach kluczowych
    zamowienia_list = []
    for z in zamowienia:
        z = dict(z)
        if not z.get('lokalizacja'):
            nazwa = z.get('produkt_nazwa') or z.get('oferta_tytul') or z.get('nazwa') or ''
            # Szukaj po każdym istotnym słowie (min 4 znaki, pomijaj generyczne)
            skip = {'produkt', 'zestaw', 'sztuk', 'nowy', 'nowa', 'nowe', 'czarny', 'bialy', 'szary'}
            words = [w for w in nazwa.split() if len(w) >= 4 and w.lower() not in skip]
            for w in words[:5]:
                p = conn.execute(
                    'SELECT lokalizacja, regal FROM produkty WHERE nazwa LIKE ? AND (lokalizacja IS NOT NULL AND lokalizacja != "" OR regal IS NOT NULL AND regal != "") LIMIT 1',
                    (f'%{w}%',)
                ).fetchone()
                if p and (p['lokalizacja'] or p['regal']):
                    z['lokalizacja'] = p['lokalizacja'] or p['regal']
                    break
        zamowienia_list.append(z)
    zamowienia = zamowienia_list

    # Grupuj zamówienia po allegro_order_id lub kupujacy+data
    grouped_orders = defaultdict(list)
    for z in zamowienia:
        # Klucz grupowania: allegro_order_id lub kupujacy+data (pierwsze 16 znaków)
        order_key = z['allegro_order_id'] or f"{z['kupujacy']}_{(z['data_sprzedazy'] or '')[:16]}"
        grouped_orders[order_key].append(z)
    
    # Buduj HTML z checkboxami - GRUPOWANE
    items_html = ''
    if len(zamowienia) == 0:
        items_html = '<div style="text-align:center;color:var(--text-muted);padding:30px">🎉 Wszystkie zamówienia wysłane!</div>'
    else:
        for order_key, items in grouped_orders.items():
            first_item = items[0]
            
            # Zbierz wszystkie IDs do checkboxa
            all_ids = ','.join([str(z['id']) for z in items])
            
            # Oblicz łączną cenę i ilość
            total_price = sum(z['cena'] or 0 for z in items)
            total_qty = sum(z['ilosc'] or 1 for z in items)
            
            # Zbierz nazwy produktów
            product_names = []
            for z in items:
                nazwa = z['produkt_nazwa'] or z['oferta_tytul'] or 'Produkt'
                # Skróć nazwę ale zachowaj czytelność
                if len(nazwa) > 60:
                    nazwa = nazwa[:57] + '...'
                qty = z['ilosc'] or 1
                if qty > 1:
                    product_names.append(f"{nazwa} (x{qty})")
                else:
                    product_names.append(nazwa)
            
            # Jeśli wiele produktów - pokaż je osobno
            if len(items) > 1:
                products_display = '<br>'.join([f"• {n}" for n in product_names])
                badge = f'<span style="background:var(--orange);color:#000;padding:2px 6px;border-radius:4px;font-size:0.7rem;font-weight:700;margin-left:8px">{len(items)} produkty</span>'
            else:
                products_display = product_names[0] if product_names else 'Produkt'
                badge = ''
            
            lokalizacja = first_item['lokalizacja'] or '—'
            dostawca = first_item['dostawca'] or 'Niezdefiniowany'
            code = first_item['ean'] or first_item['asin'] or '—'

            # Status badge: nadana = etykieta wydrukowana
            status_raw = first_item.get('status', 'nowa')
            if status_raw == 'nadana':
                badge += ' <span style="background:var(--blue);color:#fff;padding:2px 6px;border-radius:4px;font-size:0.7rem;font-weight:700">📦 NADANA</span>'
            
            # Formatuj datę
            data_raw = first_item['data_sprzedazy'] or ''
            if 'T' in data_raw:
                data_str = data_raw[:16].replace('T', ' ')
            else:
                data_str = data_raw[:16]
            
            items_html += f'''
            <div style="display:flex;align-items:flex-start;background:var(--bg-card);border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:8px">
                <label for="chk_{first_item['id']}" style="display:flex;align-items:flex-start;flex:1;cursor:pointer">
                    <input type="checkbox" id="chk_{first_item['id']}" name="ids" value="{all_ids}"
                           style="width:20px;height:20px;margin-right:12px;margin-top:4px;cursor:pointer;accent-color:var(--green)">
                    <div style="flex:1;min-width:0">
                        <div style="font-weight:600;font-size:0.9rem;line-height:1.4">{products_display}{badge}</div>
                        <div style="font-size:0.75rem;color:var(--text-muted);margin-top:4px">
                            📍 {lokalizacja} &nbsp;|&nbsp; 👤 {dostawca} &nbsp;|&nbsp; 🏷️ {code}
                        </div>
                        <div style="font-size:0.7rem;color:var(--text-muted);margin-top:2px">
                            🛒 {first_item['kupujacy']} &nbsp;|&nbsp; 📅 {data_str}
                        </div>
                    </div>
                    <div style="text-align:right;margin-left:10px">
                        <div style="font-weight:700;color:var(--green);font-size:1.1rem">{total_price:.0f} zł</div>
                        <div style="font-size:0.7rem;color:var(--text-muted)">x{total_qty}</div>
                    </div>
                </label>
                <div style="display:flex;flex-direction:column;gap:4px;margin-left:10px">
                    <a href="/wysylki/oznacz-wyslane?ids={all_ids}" style="padding:6px 10px;background:var(--green);border-radius:6px;color:#fff;text-decoration:none;font-size:0.7rem;font-weight:600;text-align:center">✅ Wysłane</a>
                    <a href="https://allegro.pl/moje-allegro/sprzedaz/zamowienia/{first_item['allegro_order_id'] or ''}" target="_blank" style="padding:6px 10px;background:var(--blue);border-radius:6px;color:#fff;text-decoration:none;font-size:0.7rem;text-align:center">Allegro</a>
                </div>
            </div>
            '''
    
    # Selektor użytkownika
    user_options = '<option value="wszyscy" ' + ('selected' if not user_filter or user_filter == 'wszyscy' else '') + '>👥 Wszyscy</option>'
    for user in users_list:
        selected = 'selected' if user_filter == user else ''
        user_options += f'<option value="{user}" {selected}>{user}</option>'
    
    user_selector = f'''
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:15px;background:var(--bg-card);border:1px solid var(--border);border-radius:10px;padding:12px">
        <label style="font-size:0.85rem;color:var(--text-secondary);font-weight:600">👤 UŻYTKOWNIK:</label>
        <select id="user-select" onchange="window.location.href='/wysylki?user=' + this.value"
                style="flex:1;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 12px;border-radius:8px;font-size:0.9rem;cursor:pointer">
            {user_options}
        </select>
    </div>
    '''
    
    # Liczba zamówień vs produktów
    orders_count = len(grouped_orders)
    products_count = len(zamowienia)
    count_info = f'{orders_count} zamówień' if orders_count != products_count else f'{orders_count} zamówień'
    if orders_count != products_count:
        count_info += f' <span style="font-size:0.75rem;color:var(--text-muted)">({products_count} produktów)</span>'
    
    html_content = f'''
        {user_selector}

        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:15px">
            <a href="/wysylki/pakowanie" style="display:block;padding:12px;background:var(--orange);border-radius:10px;color:#fff;text-decoration:none;text-align:center;font-weight:600">📱 Skanuj</a>
            <a href="/sync-miesiac" onclick="startSync(this)" style="display:block;padding:12px;background:var(--blue);border-radius:10px;color:#fff;text-decoration:none;text-align:center;font-weight:600">🔄 Sync Allegro</a>
            <a href="/wysylki/allegro" style="display:block;padding:12px;background:var(--green);border-radius:10px;color:#fff;text-decoration:none;text-align:center;font-weight:600">📦 Allegro Live</a>
            <a href="/wysylki/sync-stany" style="display:block;padding:12px;background:var(--accent2);border-radius:10px;color:#fff;text-decoration:none;text-align:center;font-weight:600">📦 Sync Stany</a>
        </div>

        <form id="bulk-form" method="POST" action="/wysylki/bulk-wyslane">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;background:var(--bg-card);border:1px solid var(--border);border-radius:10px;padding:12px">
                <div>
                    <span style="font-size:1.5rem;font-weight:700;color:var(--yellow)">{orders_count}</span>
                    <span style="font-size:0.85rem;color:var(--text-muted);margin-left:8px">{count_info}</span>
                </div>
                <div style="display:flex;gap:8px">
                    <button type="button" onclick="selectAll()"
                            style="background:var(--blue);border:none;color:#fff;padding:8px 16px;border-radius:8px;font-size:0.85rem;cursor:pointer;font-weight:600">
                        ✓ Zaznacz wszystkie
                    </button>
                    <button type="submit"
                            style="background:var(--green);border:none;color:#fff;padding:8px 16px;border-radius:8px;font-size:0.85rem;cursor:pointer;font-weight:600">
                        ✈️ Oznacz jako wysłane
                    </button>
                </div>
            </div>

            {items_html}
        </form>

        <div style="margin-top:20px;text-align:center">
            <a href="/sprzedaze" style="color:var(--text-muted);text-decoration:none;font-size:0.85rem">← Zobacz wszystkie sprzedaże</a>
        </div>
        <a href="/" style="display:block;text-align:center;color:var(--text-muted);text-decoration:none;margin-top:10px">← Dashboard</a>

    <style>@keyframes kspin{{to{{transform:rotate(360deg)}}}}</style>
    <script>
    function startSync(el) {{
        el.innerHTML = '<span style="display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,0.3);border-top-color:#fff;border-radius:50%;animation:kspin .6s linear infinite"></span> Sync...';
        el.style.opacity = '0.7';
    }}
    function selectAll() {{
        const checkboxes = document.querySelectorAll('input[name="ids"]');
        const allChecked = Array.from(checkboxes).every(cb => cb.checked);
        checkboxes.forEach(cb => cb.checked = !allChecked);
    }}

    // Prevent form submit if no checkboxes selected
    document.getElementById('bulk-form').addEventListener('submit', function(e) {{
        const checked = document.querySelectorAll('input[name="ids"]:checked');
        if (checked.length === 0) {{
            e.preventDefault();
            alert('Zaznacz przynajmniej jedno zamówienie!');
        }} else {{
            if (!confirm('Oznaczyć ' + checked.length + ' zamówień jako wysłane?')) {{
                e.preventDefault();
            }}
        }}
    }});
    </script>
    '''
    template = """{% extends "base.html" %}
{% block page_title %}Do wysylki{% endblock %}
{% block content %}
{{ content|safe }}
{% endblock %}"""
    return render_template_string(template,
        content=html_content,
        version=current_app.config.get('VERSION', ''),
        brand_name=current_app.config.get('BRAND_NAME', 'Akces Hub'),
        current_user=session.get('user'))


@wysylki_bp.route('/wysylki/wyslano-order/<order_id>')
def wyslano_order(order_id):
    """Oznacza wszystkie produkty z danego zamówienia Allegro jako wysłane"""
    from modules.database import get_db
    
    conn = get_db()
    
    # Oznacz wszystkie sprzedaże z tym order_id jako wysłane
    result = conn.execute('''
        UPDATE sprzedaze SET status = 'wyslana' 
        WHERE allegro_order_id = ? AND status IN ('nowa', 'nadana')
    ''', (order_id,))
    
    updated = result.rowcount
    conn.commit()
    
    flash(f'✅ Oznaczono {updated} produktów jako wysłane', 'success')
    return redirect('/wysylki/allegro')

@wysylki_bp.route('/wysylki/drukuj')
def wysylki_drukuj():
    """Strona druku listy pakowania ze zdjęciami (z cache)"""
    result, _ = _pobierz_zamowienia_allegro()
    zamowienia = result['zamowienia']
    produkty_cnt = result['produkty_cnt']
    wartosc = float(result['wartosc']) if result['wartosc'] else 0

    html = f'''<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Lista pakowania - {len(zamowienia)} zamówień</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, system-ui, Arial, sans-serif; font-size:12px; color:#000; background:#fff; padding:10px; }}
h1 {{ font-size:18px; text-align:center; margin-bottom:4px; }}
.summary {{ text-align:center; font-size:11px; color:#666; margin-bottom:12px; padding-bottom:8px; border-bottom:2px solid #000; }}
.order {{ display:flex; align-items:center; border:1px solid #ccc; border-radius:6px; margin-bottom:6px; overflow:hidden; page-break-inside:avoid; }}
.order-num {{ min-width:36px; background:#f0f0f0; display:flex; align-items:center; justify-content:center; font-size:16px; font-weight:700; padding:8px 4px; }}
.order-img {{ padding:6px; display:flex; flex-direction:column; gap:3px; }}
.order-img img {{ width:50px; height:50px; object-fit:contain; border:1px solid #ddd; border-radius:4px; background:#fff; }}
.order-info {{ flex:1; padding:6px 8px; }}
.order-info .name {{ font-weight:600; font-size:12px; margin-bottom:2px; }}
.order-info .addr {{ font-size:11px; color:#555; }}
.order-info .loc {{ display:inline-block; background:#666; color:#fff; padding:1px 6px; border-radius:3px; font-size:10px; font-weight:600; margin-top:3px; }}
.checkbox {{ width:16px; height:16px; border:2px solid #999; border-radius:3px; margin:0 6px; flex-shrink:0; }}
@media print {{
    body {{ padding:5px; }}
    .order {{ margin-bottom:4px; }}
}}
</style>
</head><body>
<h1>📦 LISTA PAKOWANIA</h1>
<div class="summary">{len(zamowienia)} zamówień · {produkty_cnt} produktów · {wartosc:.0f} zł · {datetime.now().strftime("%d.%m.%Y %H:%M")}</div>
'''

    for i, z in enumerate(zamowienia, 1):
        imgs_html = ''
        names_html = ''
        locs_html = ''
        for p in z['produkty']:
            img_src = p['zdjecie_url'] or 'https://via.placeholder.com/50'
            imgs_html += f'<img src="{img_src}" onerror="this.src=\'https://via.placeholder.com/50\'">'
            qty_str = f' <b>(×{p["qty"]})</b>' if p['qty'] > 1 else ''
            names_html += f'<div class="name">{p["name"][:60]}{qty_str}</div>'
            if p['lokalizacja']:
                locs_html += f'<span class="loc">📦 {p["lokalizacja"]}</span> '

        addr = z['pickup_point'] if z['pickup_point'] else z['address']

        html += f'''<div class="order">
    <div class="order-num">{i}</div>
    <div class="checkbox"></div>
    <div class="order-img">{imgs_html}</div>
    <div class="order-info">
        {names_html}
        <div class="addr">📍 {addr}</div>
        {locs_html}
    </div>
</div>
'''

    html += '''
<script>window.onload = function() { window.print(); }</script>
</body></html>'''

    return html

@wysylki_bp.route('/wysylki/bulk-wyslane-allegro', methods=['POST'])
def bulk_wyslane_allegro():
    """Bulk oznaczanie zamówień Allegro jako wysłane (z checkboxów)"""
    from modules.database import get_db

    order_ids = request.form.getlist('order_ids')

    if not order_ids:
        flash('Nie zaznaczono żadnych zamówień', 'error')
        return redirect('/wysylki/allegro')

    conn = get_db()
    total_updated = 0

    for order_id in order_ids:
        result = conn.execute('''
            UPDATE sprzedaze SET status = 'wyslana'
            WHERE allegro_order_id = ? AND status IN ('nowa', 'nadana')
        ''', (order_id,))
        total_updated += result.rowcount

    conn.commit()

    flash(f'✅ Wysłano {len(order_ids)} zamówień ({total_updated} produktów)', 'success')
    return redirect('/wysylki/allegro')


@wysylki_bp.route('/wysylki/oznacz-wyslane')
def oznacz_wyslane_pojedyncze():
    """Oznacza pojedyncze zamówienie jako wysłane (z GET)"""
    from modules.database import get_db
    
    ids_raw = request.args.get('ids', '')
    
    if not ids_raw:
        return redirect('/wysylki')
    
    # Rozdziel comma-separated IDs
    all_ids = []
    for single_id in ids_raw.split(','):
        single_id = single_id.strip()
        if single_id and single_id.isdigit():
            all_ids.append(int(single_id))
    
    if not all_ids:
        return redirect('/wysylki')
    
    conn = get_db()
    
    # Zmień status na 'wyslana'
    placeholders = ','.join(['?' for _ in all_ids])
    conn.execute('UPDATE sprzedaze SET status = "wyslana" WHERE id IN (' + placeholders + ')', all_ids)
    conn.commit()

    return redirect('/wysylki')


@wysylki_bp.route('/wysylki/sync-stany')
def sync_stany_magazynowe():
    """Synchronizuje stany magazynowe - aktualizuje ilości produktów na podstawie sprzedaży"""
    from modules.database import get_db
    
    conn = get_db()
    
    # Znajdź sprzedaże które mają produkt_id ale stan może być nieaktualny
    # Dla każdego produktu oblicz ile powinno być na stanie
    produkty_do_aktualizacji = conn.execute('''
        SELECT p.id, p.nazwa, p.ilosc as aktualna_ilosc,
               COALESCE(SUM(s.ilosc), 0) as sprzedano,
               (SELECT COALESCE(SUM(ilosc_oryginalna), p.ilosc + COALESCE(SUM(s.ilosc), 0)) 
                FROM produkty WHERE id = p.id) as ilosc_oryginalna
        FROM produkty p
        LEFT JOIN sprzedaze s ON s.produkt_id = p.id AND s.status != 'anulowana' AND s.status != 'zwrot'
        GROUP BY p.id
        HAVING sprzedano > 0
    ''').fetchall()
    
    updated = 0
    for prod in produkty_do_aktualizacji:
        # Oblicz poprawną ilość = oryginalna - sprzedano
        # Problem: nie mamy ilosc_oryginalna, więc użyjemy aktualna + sprzedano jako "oryginalna"
        # i sprawdzimy czy aktualna jest poprawna
        pass
    
    # Prostsze podejście - znajdź sprzedaże bez połączenia z produktem i spróbuj połączyć
    sprzedaze_bez_produktu = conn.execute('''
        SELECT s.id, s.nazwa, s.allegro_order_id
        FROM sprzedaze s
        WHERE s.produkt_id IS NULL AND s.nazwa IS NOT NULL AND s.nazwa != ''
        LIMIT 100
    ''').fetchall()
    
    polaczone = 0
    for s in sprzedaze_bez_produktu:
        # Szukaj produktu po nazwie (pierwsze 30 znaków)
        nazwa_szukaj = (s['nazwa'] or '')[:30].lower()
        if len(nazwa_szukaj) < 5:
            continue
            
        produkt = conn.execute('''
            SELECT id FROM produkty 
            WHERE LOWER(nazwa) LIKE ? 
            LIMIT 1
        ''', (f'%{nazwa_szukaj}%',)).fetchone()
        
        if produkt:
            conn.execute('UPDATE sprzedaze SET produkt_id = ? WHERE id = ?', (produkt['id'], s['id']))
            polaczone += 1
    
    # Teraz przelicz stany dla wszystkich produktów z nowymi sprzedażami
    # Podejście: dla każdego produktu ze sprzedażą, zmniejsz ilość o ile sprzedano
    produkty_ze_sprzedaza = conn.execute('''
        SELECT p.id, p.nazwa, p.ilosc, 
               COALESCE((SELECT SUM(s.ilosc) FROM sprzedaze s 
                         WHERE s.produkt_id = p.id 
                         AND s.status NOT IN ('anulowana', 'zwrot')
                         AND s.id NOT IN (SELECT id FROM sprzedaze WHERE produkt_id = p.id AND przeliczone = 1)), 0) as nowe_sprzedaze
        FROM produkty p
        WHERE EXISTS (SELECT 1 FROM sprzedaze s WHERE s.produkt_id = p.id AND s.status NOT IN ('anulowana', 'zwrot'))
    ''').fetchall()
    
    # Sprawdź czy kolumna 'przeliczone' istnieje
    try:
        conn.execute('ALTER TABLE sprzedaze ADD COLUMN przeliczone INTEGER DEFAULT 0')
    except:
        pass
    
    # Aktualizuj stany
    for prod in produkty_ze_sprzedaza:
        if prod['nowe_sprzedaze'] and prod['nowe_sprzedaze'] > 0:
            new_qty = max(0, prod['ilosc'] - prod['nowe_sprzedaze'])
            conn.execute('''
                UPDATE produkty SET 
                    ilosc = ?,
                    status = CASE WHEN ? = 0 THEN 'sprzedany' ELSE status END
                WHERE id = ?
            ''', (new_qty, new_qty, prod['id']))
            
            # Oznacz sprzedaże jako przeliczone
            conn.execute('''
                UPDATE sprzedaze SET przeliczone = 1 
                WHERE produkt_id = ? AND status NOT IN ('anulowana', 'zwrot')
            ''', (prod['id'],))
            
            updated += 1
            print(f"📦 Stock: {prod['nazwa'][:30]} ({prod['ilosc']} -> {new_qty})")
    
    conn.commit()
    
    flash(f'✅ Zaktualizowano {updated} produktów, połączono {polaczone} sprzedaży', 'success')
    return redirect('/wysylki')


@wysylki_bp.route('/wysylki/bulk-wyslane', methods=['POST'])
def bulk_oznacz_wyslane():
    """Bulk update - oznacza zaznaczone zamówienia jako wysłane (obsługuje zgrupowane zamówienia)"""
    from modules.database import get_db
    
    raw_ids = request.form.getlist('ids')
    
    if not raw_ids:
        return redirect('/wysylki')
    
    # Rozdziel comma-separated IDs (dla zgrupowanych zamówień)
    all_ids = []
    for id_group in raw_ids:
        for single_id in id_group.split(','):
            single_id = single_id.strip()
            if single_id and single_id.isdigit():
                all_ids.append(int(single_id))
    
    if not all_ids:
        return redirect('/wysylki')
    
    conn = get_db()
    
    # Zmień status na 'wyslana' dla zaznaczonych
    placeholders = ','.join(['?' for _ in all_ids])
    conn.execute('UPDATE sprzedaze SET status = "wyslana" WHERE id IN (' + placeholders + ')', all_ids)
    conn.commit()
    
    # Success message - pokazuj liczbę produktów
    return f'''
    <html><head><meta http-equiv="refresh" content="2;url=/wysylki"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px">✈️</div>
            <div style="font-size:1.2rem">Oznaczono {len(all_ids)} produktów jako wysłane!</div>
            <div style="color:#64748b;margin-top:10px">Przekierowuję...</div>
        </div>
    </body></html>
    '''

