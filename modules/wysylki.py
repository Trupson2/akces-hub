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
# Packed orders tracking (DB-backed, survives restarts)
# ============================================================
def _get_packed_orders():
    """Get set of packed order_ids from DB"""
    from modules.database import get_db
    try:
        conn = get_db()
        conn.execute('''CREATE TABLE IF NOT EXISTS packed_orders (
            order_id TEXT PRIMARY KEY,
            packed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        # Auto-clean entries older than 3 days
        conn.execute("DELETE FROM packed_orders WHERE packed_at < datetime('now', '-3 days')")
        rows = conn.execute('SELECT order_id FROM packed_orders').fetchall()
        return {r['order_id'] for r in rows}
    except Exception as e:
        print(f"[WARN] packed_orders read error: {e}")
        return set()

def _add_packed_order(order_id):
    from modules.database import get_db
    try:
        conn = get_db()
        conn.execute('INSERT OR IGNORE INTO packed_orders (order_id) VALUES (?)', (order_id,))
        conn.commit()
    except Exception as e:
        print(f"[WARN] packed_orders write error: {e}")

def _remove_packed_order(order_id):
    from modules.database import get_db
    try:
        conn = get_db()
        conn.execute('DELETE FROM packed_orders WHERE order_id = ?', (order_id,))
        conn.commit()
    except Exception as e:
        print(f"[WARN] packed_orders delete error: {e}")

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
               s.data_sprzedazy, s.adres, s.produkt_id, s.metoda_dostawy,
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
                'delivery_method': row['metoda_dostawy'] or '',
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

def _get_delivery_info(order):
    """Wyciąga info o dostawie: metoda, punkt, adres, sugestia pakowania"""
    delivery = order.get('delivery', {})
    method = delivery.get('method', {})
    method_name = method.get('name', '')
    method_id = method.get('id', '')

    address_data = delivery.get('address', {})
    address = ', '.join([p for p in [
        address_data.get('street', ''), address_data.get('city', ''), address_data.get('zipCode', '')
    ] if p])

    pickup_point = ''
    pickup_name = ''
    if delivery.get('pickupPoint'):
        pp = delivery.get('pickupPoint', {})
        pickup_name = pp.get('name', '')
        pickup_point = f"{pickup_name} - {pp.get('address', {}).get('street', '')}"

    # Rozpoznaj typ dostawy - sprawdź ZARÓWNO nazwę metody JAK I pickup point ID
    method_lower = method_name.lower()
    pickup_id = (delivery.get('pickupPoint', {}).get('id', '') or '').upper()

    # Pickup point ID zaczyna się od prefixu przewoźnika:
    # InPost paczkomaty: np. KRA010, PNET0924, WAW123M
    # Orlen Paczka: np. ORL-xxx, ORLxxx
    is_orlen_pickup = pickup_id.startswith('ORL')
    # InPost: dowolny pickup point z literami+cyframi (nie Orlen)
    is_inpost_pickup = bool(pickup_id) and not is_orlen_pickup

    if 'orlen' in method_lower or is_orlen_pickup:
        delivery_type = 'paczkomat_orlen'
        pack_hint = '⛽ Orlen Paczka — gabaryty S/M/L, max 41×38×64cm, max 15kg.'
    elif any(x in method_lower for x in ['paczkomat', 'inpost', 'automat', 'paczka w ruchu']) or is_inpost_pickup:
        delivery_type = 'paczkomat'
        pack_hint = '📬 InPost Paczkomat — gabaryty A/B/C, max 41×38×64cm, max 25kg.'
    elif any(x in method_lower for x in ['kurier', 'dpd', 'dhl', 'ups', 'fedex', 'gls', 'pocztex']):
        delivery_type = 'kurier'
        pack_hint = '<span class=material-symbols-outlined>local_shipping</span> Kurier — zabezpiecz folią bąbelkową, oklej taśmą.'
    elif any(x in method_lower for x in ['list', 'poczt', 'polecony']):
        delivery_type = 'list'
        pack_hint = '✉ List/poczta — koperta bąbelkowa lub mały karton.'
    elif any(x in method_lower for x in ['odbiór', 'osobisty', 'osobist']):
        delivery_type = 'odbior'
        pack_hint = '<span class="material-symbols-outlined">home</span> Odbiór osobisty — przygotuj do wydania.'
    elif pickup_name:
        delivery_type = 'punkt'
        pack_hint = f'<span class=material-symbols-outlined>pin_drop</span> Punkt odbioru: {pickup_name} — standardowy karton.'
    else:
        delivery_type = 'inny'
        pack_hint = f'<span class=material-symbols-outlined>inventory_2</span> {method_name or "Standardowa wysyłka"} — zabezpiecz odpowiednio.'

    return {
        'method_name': method_name,
        'delivery_type': delivery_type,
        'address': address or 'Brak adresu',
        'pickup_point': pickup_point,
        'pickup_point_id': pickup_id,
        'pack_hint': pack_hint,
    }


def _zwroc_zamowienie_full(order):
    """Helper - formatuje odpowiedź z WSZYSTKIMI produktami zamówienia (np. ze skanowanej etykiety)"""
    from modules.database import get_db
    order_id = order.get('id', '')
    buyer = order.get('buyer', {}).get('login', 'Nieznany')

    del_info = _get_delivery_info(order)

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
            'address': del_info['address'],
            'pickup_point': del_info['pickup_point'],
            'pickup_point_id': del_info.get('pickup_point_id', ''),
            'delivery_type': del_info['delivery_type'],
            'delivery_method': del_info['method_name'],
            'pack_hint': del_info['pack_hint'],
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

    del_info = _get_delivery_info(order)

    total = sum(float(i.get('price', {}).get('amount', 0)) * int(i.get('quantity', 1))
               for i in order.get('lineItems', []))

    inne_produkty = len(order.get('lineItems', [])) - 1

    return jsonify({
        'zamowienie': {
            'order_id': order_id,
            'buyer': buyer,
            'address': del_info['address'],
            'pickup_point': del_info['pickup_point'],
            'pickup_point_id': del_info.get('pickup_point_id', ''),
            'delivery_type': del_info['delivery_type'],
            'delivery_method': del_info['method_name'],
            'pack_hint': del_info['pack_hint'],
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


@wysylki_bp.route('/api/wysylki/pending')
def api_wysylki_pending():
    """API - zwraca liste zamowien oczekujacych na pakowanie.

    OPTYMALIZACJA: cache 30s w pamieci (5-10x szybszy refresh).
    Allegro round-trip to 2-5s, lokalna iteracja to <50ms.
    Pierwszy request -> fetch z Allegro + cache. Kolejne (do 30s) -> instant.
    """
    from modules.allegro_api import is_authenticated, get_orders
    from modules.database import get_db
    import time as _t

    # CACHE: zwroc gotowy wynik jesli mlodszy niz 30s
    global _PENDING_CACHE
    try:
        _cache = _PENDING_CACHE
    except NameError:
        _cache = None
    if _cache and (_t.time() - _cache.get('ts', 0)) < 30:
        # Refresh shipped_today (zmienia sie czesto, ale szybkie)
        try:
            conn = get_db()
            from datetime import date
            today = date.today().isoformat()
            shipped_row = conn.execute("SELECT COUNT(*) as cnt FROM sprzedaze WHERE status IN ('wyslana','nadana') AND date(data_sprzedazy) = ?", (today,)).fetchone()
            _cache['data']['shipped_today'] = shipped_row['cnt'] if shipped_row else 0
        except Exception:
            pass
        return jsonify(_cache['data'])

    orders = []
    shipped_today = 0

    try:
        conn = get_db()
        from datetime import date
        today = date.today().isoformat()
        shipped_row = conn.execute("SELECT COUNT(*) as cnt FROM sprzedaze WHERE status IN ('wyslana','nadana') AND date(data_sprzedazy) = ?", (today,)).fetchone()
        shipped_today = shipped_row['cnt'] if shipped_row else 0
    except Exception as e:
        print(f"[WARN] shipped_today query error: {e}")

    # Próbuj Allegro API
    if is_authenticated():
        try:
            raw_result = get_orders(status='READY_FOR_PROCESSING')
            raw_orders = raw_result[0] if isinstance(raw_result, tuple) else raw_result
            if raw_orders:
                for order in raw_orders.get('checkoutForms', []):
                    order_id = order.get('id', '')
                    buyer = order.get('buyer', {}).get('login', 'Nieznany')
                    delivery = order.get('delivery', {})
                    method_name = delivery.get('method', {}).get('name', '')
                    pickup = delivery.get('pickupPoint', {})
                    pickup_id = pickup.get('id', '')
                    pickup_name = pickup.get('name', '')
                    items = order.get('lineItems', [])
                    total = sum(float(i.get('price', {}).get('amount', 0)) * int(i.get('quantity', 1)) for i in items)

                    ml = method_name.lower()
                    pid = (pickup_id or '').upper()
                    if 'orlen' in ml or pid.startswith('ORL'):
                        carrier = 'Orlen'
                    elif any(x in ml for x in ['inpost', 'paczkomat', 'paczka w ruchu']) or (pid and not pid.startswith('ORL')):
                        carrier = 'InPost'
                    elif 'dpd' in ml:
                        carrier = 'DPD'
                    elif 'dhl' in ml:
                        carrier = 'DHL'
                    else:
                        carrier = method_name[:15] or 'Kurier'

                    pickup_display = ''
                    if pickup_name:
                        pp_addr = pickup.get('address', {})
                        pickup_display = f"{pickup_name} - {pp_addr.get('street', '')} {pp_addr.get('city', '')}".strip()

                    # Skip orders already packed locally
                    if order_id in _get_packed_orders():
                        continue
                    orders.append({
                        'order_id': order_id,
                        'buyer': buyer,
                        'carrier': carrier,
                        'method': method_name,
                        'pickup_point': pickup_display,
                        'items_count': len(items),
                        'total': f"{total:.0f}",
                    })
        except Exception as e:
            print(f"[api/wysylki/pending] Allegro API error: {e}")

    # Uzupełnij z lokalnej bazy - zamówienia które są w bazie ale nie przyszły z API
    existing_ids = {o['order_id'] for o in orders}
    try:
        conn = get_db()
        rows = conn.execute('''
            SELECT s.allegro_order_id, s.kupujacy, s.metoda_dostawy, s.adres,
                   SUM(s.ilosc) as total_qty, SUM(s.cena * s.ilosc) as total_val
            FROM sprzedaze s
            WHERE s.status IN ('nowa', 'nadana')
            AND s.allegro_order_id IS NOT NULL AND s.allegro_order_id != ''
            GROUP BY s.allegro_order_id
        ''').fetchall()
        for r in rows:
            oid = r['allegro_order_id']
            if oid in existing_ids or oid in _get_packed_orders():
                continue
            dm = (r['metoda_dostawy'] or '').lower()
            addr = (r['adres'] or '').lower()
            detect = dm if dm else addr
            if 'orlen' in detect:
                carrier = 'Orlen'
            elif 'inpost' in detect or 'paczkomat' in detect or 'paczkopunkt' in detect:
                carrier = 'InPost'
            elif 'dpd' in detect:
                carrier = 'DPD'
            elif 'dhl' in detect:
                carrier = 'DHL'
            else:
                carrier = 'DPD'  # default kurier = DPD
            # Pickup point z adresu (jeśli paczkomat)
            pickup = r['adres'] if carrier in ('InPost', 'Orlen') else ''
            orders.append({
                'order_id': oid,
                'buyer': r['kupujacy'] or 'Nieznany',
                'carrier': carrier,
                'method': r['metoda_dostawy'] or '',
                'pickup_point': pickup,
                'items_count': r['total_qty'] or 1,
                'total': f"{r['total_val'] or 0:.0f}",
            })
    except Exception as e:
            print(f"[WARN] DB fallback orders error: {e}")

    _result = {'orders': orders, 'total': len(orders), 'shipped_today': shipped_today}
    # ZAPISZ DO CACHE (30s) - kolejne /api/wysylki/pending requesty pojda instant
    try:
        _PENDING_CACHE = {'ts': _t.time(), 'data': _result}
        globals()['_PENDING_CACHE'] = _PENDING_CACHE
    except Exception:
        pass
    return jsonify(_result)


@wysylki_bp.route('/api/wysylki/cennik')
def api_wysylki_cennik():
    """API - zwraca cennik paczkomatów z Allegro API"""
    from modules.allegro_api import is_authenticated, get_shipping_rates, get_allegro_config

    # Domyślne ceny (Wysyłam z Allegro standardowe stawki)
    cennik = {
        'inpost': {'A': '6.40', 'B': '8.50', 'C': '11.50'},
        'orlen':  {'S': '6.00', 'M': '8.00', 'L': '10.50'}
    }

    if is_authenticated():
        try:
            config = get_allegro_config()
            shipping_id = config.get('shipping_id', '')
            if shipping_id:
                rates, err = get_shipping_rates()
                if rates and not err:
                    for rate_set in rates.get('shippingRates', []):
                        if rate_set.get('id') == shipping_id:
                            for rate in rate_set.get('rates', []):
                                method_name = (rate.get('deliveryMethod', {}).get('name', '') or '').lower()
                                first_price = rate.get('firstItemRate', {}).get('amount', '')
                                if not first_price:
                                    continue
                                # InPost paczkomat - wyciągnij cenę per gabaryt
                                if 'inpost' in method_name or 'paczkomat' in method_name:
                                    if 'orlen' not in method_name:
                                        # Przypisz po rozmiarze w nazwie
                                        if 'gabaryt a' in method_name or 'mały' in method_name or 'small' in method_name:
                                            cennik['inpost']['A'] = first_price
                                        elif 'gabaryt b' in method_name or 'średni' in method_name or 'medium' in method_name:
                                            cennik['inpost']['B'] = first_price
                                        elif 'gabaryt c' in method_name or 'duży' in method_name or 'large' in method_name:
                                            cennik['inpost']['C'] = first_price
                                # Orlen Paczka
                                if 'orlen' in method_name:
                                    if 'gabaryt s' in method_name or 'mały' in method_name or 'small' in method_name:
                                        cennik['orlen']['S'] = first_price
                                    elif 'gabaryt m' in method_name or 'średni' in method_name or 'medium' in method_name:
                                        cennik['orlen']['M'] = first_price
                                    elif 'gabaryt l' in method_name or 'duży' in method_name or 'large' in method_name:
                                        cennik['orlen']['L'] = first_price
        except Exception as e:
            print(f"[WARN] Cennik API error: {e}")

    return jsonify(cennik)


@wysylki_bp.route('/api/wysylki/mark-packed', methods=['POST'])
def api_mark_packed():
    """Mark order as packed (hides from pending list until shipped/nadana)."""
    data = request.get_json(silent=True) or {}
    order_id = data.get('order_id', '')
    if not order_id:
        return jsonify({'error': 'Brak order_id'}), 400
    _add_packed_order(order_id)
    return jsonify({'ok': True})


@wysylki_bp.route('/api/wysylki/backfill-carriers', methods=['POST'])
def api_backfill_carriers():
    """Jednorazowy backfill: pobierz metoda_dostawy z Allegro API dla zamówień z pustym carrier"""
    from modules.database import get_db
    from modules.allegro_api import get_order_details, is_authenticated

    if not is_authenticated():
        return jsonify({'error': 'Nie zalogowano do Allegro'}), 401

    conn = get_db()
    rows = conn.execute('''
        SELECT DISTINCT allegro_order_id FROM sprzedaze
        WHERE allegro_order_id IS NOT NULL AND allegro_order_id != ''
        AND (metoda_dostawy IS NULL OR metoda_dostawy = '')
        AND status IN ('nowa', 'nadana')
    ''').fetchall()

    updated = 0
    errors = 0
    for row in rows:
        oid = row['allegro_order_id']
        try:
            order, err = get_order_details(oid)
            if err or not order:
                errors += 1
                continue
            delivery = order.get('delivery', {})
            method_name = (delivery.get('method', {}).get('name', '') or '').lower()
            pickup_id = (delivery.get('pickupPoint', {}).get('id', '') or '').upper()

            if 'orlen' in method_name or pickup_id.startswith('ORL'):
                carrier = 'Orlen'
            elif any(x in method_name for x in ['inpost', 'paczkomat', 'paczka w ruchu']) or (pickup_id and not pickup_id.startswith('ORL')):
                carrier = 'InPost'
            elif 'dpd' in method_name:
                carrier = 'DPD'
            elif 'dhl' in method_name:
                carrier = 'DHL'
            else:
                carrier = (delivery.get('method', {}).get('name', '') or '')[:20] or 'Kurier'

            conn.execute('UPDATE sprzedaze SET metoda_dostawy = ? WHERE allegro_order_id = ? AND (metoda_dostawy IS NULL OR metoda_dostawy = "")', (carrier, oid))
            updated += 1
            print(f"[BACKFILL] {oid[:12]}... → {carrier}")
        except Exception as e:
            errors += 1
            print(f"[BACKFILL] Error {oid[:12]}...: {e}")

    conn.commit()
    # Clear cache so page refreshes with new data
    global _wysylki_cache
    _wysylki_cache = {'data': None, 'raw': None, 'timestamp': 0}

    return jsonify({'success': True, 'updated': updated, 'errors': errors, 'total': len(rows)})


@wysylki_bp.route('/api/wysylki/unpack', methods=['POST'])
def api_unpack():
    """Remove order from packed list (show again in pending)."""
    data = request.get_json(silent=True) or {}
    order_id = data.get('order_id', '')
    _remove_packed_order(order_id)
    return jsonify({'ok': True})


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

    print(f"[SEARCH] Szukam zamówienia dla: {q}")

    # Pobierz zamówienia z cache (szybko!)
    result, raw_orders = _pobierz_zamowienia_allegro()

    if not raw_orders or 'checkoutForms' not in raw_orders:
        # Allegro API puste — spróbuj szukać w bazie
        conn = get_db()
        produkt_z_bazy = conn.execute('''
            SELECT p.id, p.nazwa, p.ean, p.asin, p.ilosc, p.lokalizacja, p.regal, p.zdjecie_url
            FROM produkty p
            WHERE p.ean = ? OR p.asin = ? OR LOWER(p.asin) = LOWER(?) OR p.kod_magazynowy = ?
            LIMIT 1
        ''', (q, q, q, q.upper())).fetchone()
        if produkt_z_bazy:
            db_order = conn.execute('''
                SELECT s.id, s.allegro_order_id, s.nazwa, s.cena, s.kupujacy, s.adres, s.ilosc
                FROM sprzedaze s
                WHERE s.produkt_id = ? AND s.status IN ('nowa', 'nowe')
                ORDER BY s.data_sprzedazy DESC LIMIT 1
            ''', (produkt_z_bazy['id'],)).fetchone()
            if db_order:
                lok = produkt_z_bazy['lokalizacja'] or produkt_z_bazy['regal'] or ''
                allegro_oid = db_order['allegro_order_id']

                # Jeśli mamy allegro_order_id, pobierz szczegóły z Allegro API (delivery info)
                if allegro_oid and raw_orders:
                    for ao in raw_orders.get('checkoutForms', []):
                        if ao.get('id') == allegro_oid:
                            print(f"   → [CHECK_CIRCLE] Znaleziono w Allegro API po allegro_order_id z bazy")
                            return _zwroc_zamowienie_full(ao)

                # Fallback bez Allegro API - spróbuj wykryć typ z adresu
                adres = (db_order['adres'] or '').lower()
                if 'paczkomat' in adres or 'inpost' in adres:
                    del_type = 'paczkomat'
                    del_hint = '📬 InPost Paczkomat — wybierz gabaryt A/B/C'
                elif 'orlen' in adres:
                    del_type = 'paczkomat_orlen'
                    del_hint = '⛽ Orlen Paczka — wybierz gabaryt S/M/L'
                else:
                    del_type = 'kurier'
                    del_hint = '<span class=material-symbols-outlined>inventory_2</span> Sprawdź metodę dostawy na Allegro'

                # Wyciągnij pickup_point z adresu jeśli jest paczkomat
                pickup = ''
                pickup_id = ''
                if 'paczkomat' in adres or 'inpost' in adres or 'orlen' in adres:
                    pickup = db_order['adres'] or ''
                    # Spróbuj wyciągnąć ID paczkomatu (np. PNET0924)
                    import re
                    m = re.search(r'([A-Z]{2,5}\d{3,6}[A-Z]?)', (db_order['adres'] or '').upper())
                    if m:
                        pickup_id = m.group(1)

                return jsonify({
                    'zamowienie': {
                        'order_id': allegro_oid or str(db_order['id']),
                        'buyer': db_order['kupujacy'] or 'Nieznany',
                        'address': db_order['adres'] or '',
                        'pickup_point': pickup,
                        'pickup_point_id': pickup_id,
                        'delivery_type': del_type,
                        'delivery_method': del_type,
                        'pack_hint': del_hint,
                        'total': str(db_order['cena'] or 0),
                        'produkt_nazwa': db_order['nazwa'],
                        'inne_produkty': 0,
                        'produkty': [{
                            'nazwa': db_order['nazwa'],
                            'qty': db_order['ilosc'] or 1,
                            'lokalizacja': lok,
                            'zdjecie_url': produkt_z_bazy['zdjecie_url'] or ''
                        }],
                        'lokalizacja': lok,
                        'asin': produkt_z_bazy['asin'],
                        'ean': produkt_z_bazy['ean'],
                        'stan_magazynowy': produkt_z_bazy['ilosc']
                    }
                })
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
        # Szukaj po allegro_order_id w bazie (fallback gdy API puste)
        db_by_oid = conn.execute('''
            SELECT s.id, s.allegro_order_id, s.nazwa, s.cena, s.kupujacy, s.adres, s.ilosc, s.produkt_id
            FROM sprzedaze s
            WHERE s.allegro_order_id = ? OR s.allegro_order_id LIKE ?
            LIMIT 1
        ''', (q, f'{q}%')).fetchone()
        if db_by_oid:
            lok = ''
            zdjecie = ''
            ean = ''
            asin_val = ''
            stan = 0
            if db_by_oid['produkt_id']:
                prod = conn.execute('SELECT lokalizacja, regal, zdjecie_url, ean, asin, ilosc FROM produkty WHERE id=?', (db_by_oid['produkt_id'],)).fetchone()
                if prod:
                    lok = prod['lokalizacja'] or prod['regal'] or ''
                    zdjecie = prod['zdjecie_url'] or ''
                    ean = prod['ean'] or ''
                    asin_val = prod['asin'] or ''
                    stan = prod['ilosc'] or 0

            adres = (db_by_oid['adres'] or '').lower()
            if 'paczkomat' in adres or 'inpost' in adres:
                del_type = 'paczkomat'
            elif 'orlen' in adres:
                del_type = 'paczkomat_orlen'
            else:
                del_type = 'kurier'

            import re
            pickup_id = ''
            m = re.search(r'([A-Z]{2,5}\d{3,6}[A-Z]?)', (db_by_oid['adres'] or '').upper())
            if m:
                pickup_id = m.group(1)

            return jsonify({
                'zamowienie': {
                    'order_id': db_by_oid['allegro_order_id'] or str(db_by_oid['id']),
                    'buyer': db_by_oid['kupujacy'] or 'Nieznany',
                    'address': db_by_oid['adres'] or '',
                    'pickup_point': db_by_oid['adres'] or '' if del_type != 'kurier' else '',
                    'pickup_point_id': pickup_id,
                    'delivery_type': del_type,
                    'delivery_method': del_type,
                    'total': str(db_by_oid['cena'] or 0),
                    'produkt_nazwa': db_by_oid['nazwa'],
                    'inne_produkty': 0,
                    'produkty': [{
                        'nazwa': db_by_oid['nazwa'],
                        'qty': db_by_oid['ilosc'] or 1,
                        'lokalizacja': lok,
                        'zdjecie_url': zdjecie
                    }],
                    'lokalizacja': lok,
                    'asin': asin_val,
                    'ean': ean,
                    'stan_magazynowy': stan
                }
            })
        return jsonify({'error': 'Nie znaleziono produktu ani zamówienia w bazie'})

    q_lower = q.lower().strip()

    # === 1. Szukaj po ORDER ID (etykieta wysyłkowa) ===
    for order in raw_orders.get('checkoutForms', []):
        order_id = order.get('id', '')
        # Dopasuj pełny order_id lub jego fragment (min 8 znaków)
        if order_id and (q_lower == order_id.lower() or
                        (len(q) >= 8 and q_lower in order_id.lower()) or
                        order_id.lower().startswith(q_lower)):
            print(f"   → [CHECK_CIRCLE] Znaleziono po order_id: {order_id}")
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
                print(f"   → [CHECK_CIRCLE] Znaleziono po allegro_id: {offer_id}")
                return _zwroc_zamowienie(order, item, produkt_z_bazy)

            for fraza in szukane_frazy:
                if len(fraza) > 3 and fraza in offer_name.lower():
                    print(f"   → [CHECK_CIRCLE] Znaleziono po frazie '{fraza}'")
                    return _zwroc_zamowienie(order, item, produkt_z_bazy)

    # === 4. Fallback: szukaj w bazie sprzedaze (zamówienia nowa/nowe) ===
    if produkt_z_bazy:
        db_order = conn.execute('''
            SELECT s.id, s.allegro_order_id, s.nazwa, s.cena, s.kupujacy, s.adres, s.ilosc
            FROM sprzedaze s
            WHERE s.produkt_id = ? AND s.status IN ('nowa', 'nowe')
            ORDER BY s.data_sprzedazy DESC LIMIT 1
        ''', (produkt_z_bazy['id'],)).fetchone()

        if db_order:
            print(f"   → [CHECK_CIRCLE] Znaleziono w bazie (sprzedaze id={db_order['id']})")
            lok = produkt_z_bazy['lokalizacja'] or produkt_z_bazy['regal'] or ''
            return jsonify({
                'zamowienie': {
                    'order_id': db_order['allegro_order_id'] or str(db_order['id']),
                    'buyer': db_order['kupujacy'] or 'Nieznany',
                    'address': db_order['adres'] or '',
                    'pickup_point': '',
                    'delivery_type': 'kurier',
                    'delivery_method': '',
                    'pack_hint': '<span class=material-symbols-outlined>inventory_2</span> Sprawdź metodę dostawy na Allegro',
                    'total': str(db_order['cena'] or 0),
                    'produkt_nazwa': db_order['nazwa'],
                    'inne_produkty': 0,
                    'produkty': [{
                        'nazwa': db_order['nazwa'],
                        'qty': db_order['ilosc'] or 1,
                        'lokalizacja': lok,
                        'zdjecie_url': produkt_z_bazy['zdjecie_url'] or ''
                    }],
                    'lokalizacja': lok,
                    'asin': produkt_z_bazy['asin'],
                    'ean': produkt_z_bazy['ean'],
                    'stan_magazynowy': produkt_z_bazy['ilosc']
                }
            })

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
    """Tworzy przesyłkę (jeśli nie istnieje) i zwraca etykietę PDF lub JSON error"""
    from modules.allegro_api import create_and_get_label, get_order_details

    print(f"[PRINT] Nadawanie przesyłki dla zamówienia: {order_id}")

    # Stacja pakowania zawsze wywołuje ten endpoint przez fetch (AJAX)
    # Zawsze zwracaj JSON przy błędach, PDF przy sukcesie
    wants_json = True
    test_mode = request.args.get('test') == '1'

    # ── TRYB TESTOWY ──
    if test_mode:
        # Wymaga zalogowanego usera
        if not session.get('username'):
            return jsonify({'success': False, 'error': 'Zaloguj się aby użyć trybu testowego'}), 403
        print(f"   → [SCIE] TRYB TESTOWY (user: {session.get('username')}) - nie wysyłam do Allegro API")
        order, ord_err = get_order_details(order_id)
        if ord_err:
            return jsonify({'success': False, 'error': f'Nie można pobrać zamówienia: {ord_err}', 'test_mode': True}), 400

        delivery = order.get('delivery', {})
        method_name = delivery.get('method', {}).get('name', 'nieznana')
        method_id = delivery.get('method', {}).get('id', '')
        pickup = delivery.get('pickupPoint', {})
        addr = delivery.get('address', {})
        items = order.get('lineItems', [])
        buyer = order.get('buyer', {})

        method_low = method_name.lower()
        is_orlen = 'orlen' in method_low
        is_inpost = any(kw in method_low for kw in ['inpost', 'paczkomat', 'paczka w ruchu']) and not is_orlen
        carrier = 'InPost' if is_inpost else ('Orlen Paczka' if is_orlen else 'DPD/Kurier')

        test_data = {
            'success': True,
            'test_mode': True,
            'order_id': order_id,
            'carrier': carrier,
            'delivery_method': method_name,
            'delivery_method_id': method_id,
            'pickup_point': pickup.get('id', 'brak'),
            'pickup_name': pickup.get('name', ''),
            'address': {
                'name': f"{addr.get('firstName', '')} {addr.get('lastName', '')}".strip(),
                'street': addr.get('street', ''),
                'city': addr.get('city', ''),
                'zip': addr.get('zipCode', ''),
                'phone': addr.get('phoneNumber', ''),
            },
            'buyer': {
                'login': buyer.get('login', ''),
                'email': buyer.get('email', ''),
            },
            'items': [{'name': i['offer']['name'][:50], 'qty': i.get('quantity', 1), 'price': i.get('price', {}).get('amount', '0')} for i in items],
            'total': order.get('summary', {}).get('totalToPay', {}).get('amount', '0'),
            'payload_preview': {
                'deliveryMethodId': method_id,
                'credentialsId': 'bf1a1cf0-...(DPD)' if not is_inpost else 'allegro_shipping_id (InPost)',
                'lineItemIds': [i.get('id', '') for i in items],
                'pickupPointId': pickup.get('id') if pickup.get('id') else 'N/A',
            },
            'message': f'TEST OK - gotowy do nadania przez {carrier}'
        }
        return jsonify(test_data)

    # Parsuj gabaryt z query params
    parcel_size = request.args.get('size')  # A, B, C for InPost
    dimensions = None
    if request.args.get('dim_l'):
        dimensions = {
            'length': request.args.get('dim_l', '30'),
            'width': request.args.get('dim_w', '25'),
            'height': request.args.get('dim_h', '15'),
            'weight_kg': request.args.get('dim_kg', '1'),
        }

    # Spróbuj utworzyć przesyłkę i pobrać etykietę
    try:
        label_pdf, shipment_id, error = create_and_get_label(order_id, parcel_size=parcel_size, dimensions=dimensions)
    except Exception as e:
        error = f"Wyjątek serwera: {str(e)}"
        label_pdf, shipment_id = None, None
        print(f"   → [CANCEL] Wyjątek: {e}")

    if error:
        allegro_url = f"https://allegro.pl/moje-allegro/sprzedaz/zamowienia/{order_id}"
        if wants_json:
            return jsonify({
                'success': False,
                'error': error,
                'order_id': order_id,
                'allegro_url': allegro_url
            }), 400
        return f'''
        <html>
        <head><meta charset="utf-8"><title>Błąd</title>
        <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=swap" rel="stylesheet"></head>
        <body style="font-family:sans-serif;padding:40px;background:#12121a;color:#fff">
            <h2><span class=material-symbols-outlined>cancel</span> Błąd nadawania przesyłki</h2>
            <p style="color:#ef4444">{error}</p>
            <p>Zamówienie: {order_id[:8]}...</p>
            <p style="color:#64748b;font-size:0.9rem;margin-top:20px">Możliwe przyczyny:</p>
            <ul style="color:#64748b;font-size:0.85rem">
                <li>Brak uprawnień API do tworzenia przesyłek</li>
                <li>Zamówienie już ma nadaną przesyłkę ręcznie</li>
                <li>Problem z metodą dostawy</li>
            </ul>
            <a href="{allegro_url}" target="_blank" style="display:inline-block;margin:20px 0;padding:12px 20px;background:#3b82f6;color:#fff;text-decoration:none;border-radius:8px;font-weight:600"><span class=material-symbols-outlined>inventory_2</span> Nadaj ręcznie na Allegro →</a><br>
            <a href="/wysylki" style="color:#64748b">← Powrót do wysyłek</a>
        </body>
        </html>
        ''', 400

    if label_pdf:
        print(f"   → [CHECK_CIRCLE] Etykieta gotowa! Rozmiar: {len(label_pdf)} bytes")
        # Oznacz jako wyslana + zaktualizuj status produktu
        try:
            from modules.database import get_db as _gdb
            _conn = _gdb()
            _conn.execute("UPDATE sprzedaze SET status = 'wyslana' WHERE allegro_order_id = ? AND status IN ('nowa', 'nadana')", (order_id,))
            _sprzedane = _conn.execute(
                "SELECT produkt_id FROM sprzedaze WHERE allegro_order_id = ? AND produkt_id IS NOT NULL", (order_id,)
            ).fetchall()
            for _s in _sprzedane:
                _conn.execute("""
                    UPDATE produkty SET status = 'sprzedany'
                    WHERE id = ? AND ilosc <= 0
                    AND status NOT IN ('sprzedany','wyslany','uszkodzony','zlomowany','naprawa')
                """, (_s['produkt_id'],))
            _conn.commit()
        except Exception as _e:
            print(f"[WARN] Status update po nadaniu: {_e}")
        if wants_json:
            import base64
            # Detect carrier for frontend (pickup button)
            try:
                _ord, _ = get_order_details(order_id)
                _dm = (_ord or {}).get('delivery', {}).get('method', {}).get('name', '').lower()
                _carrier = 'DPD' if 'dpd' in _dm else ('DHL' if 'dhl' in _dm else ('Orlen' if 'orlen' in _dm else ('InPost' if any(x in _dm for x in ['inpost', 'paczkomat']) else 'Kurier')))
            except Exception as e:
                print(f"[WARN] carrier detect error: {e}")
                _carrier = 'Kurier'
            return jsonify({
                'success': True,
                'shipment_id': shipment_id,
                'carrier': _carrier,
                'label_url': f'/wysylki/etykieta/{order_id}',
                'label_base64': base64.b64encode(label_pdf).decode('utf-8')
            })
        # Zwróć PDF do druku
        response = make_response(label_pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename=etykieta_{order_id[:8]}.pdf'
        return response
    else:
        # Przesyłka utworzona ale brak etykiety
        if wants_json:
            return jsonify({
                'success': True,
                'shipment_id': shipment_id,
                'label_url': f'/wysylki/etykieta/{order_id}',
                'message': 'Przesyłka utworzona, etykieta może być dostępna za chwilę'
            })
        return f'''
        <html>
        <head><meta charset="utf-8"><title>Przesyłka utworzona</title>
        <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=swap" rel="stylesheet"></head>
        <body style="font-family:sans-serif;padding:40px;background:#12121a;color:#fff">
            <h2><span class=material-symbols-outlined>check_circle</span> Przesyłka utworzona!</h2>
            <p>ID przesyłki: {shipment_id}</p>
            <p style="color:#f59e0b">Etykieta może być niedostępna od razu. Spróbuj pobrać za chwilę.</p>
            <a href="/wysylki/etykieta/{order_id}" style="display:inline-block;margin:20px 0;padding:12px 20px;background:#22c55e;color:#fff;text-decoration:none;border-radius:8px;font-weight:600"><span class=material-symbols-outlined>print</span> Pobierz etykietę</a><br>
            <a href="/wysylki" style="color:#64748b">← Powrót do wysyłek</a>
        </body>
        </html>
        '''


@wysylki_bp.route('/wysylki/podjazd/<order_id>', methods=['POST'])
def wysylki_podjazd(order_id):
    """Zamawia podjazd kuriera DPD dla przesyłki"""
    from modules.allegro_api import allegro_request, get_wysylam_z_allegro_shipments
    import uuid

    print(f"[PICKUP] Zamawianie podjazdu kuriera dla: {order_id}")

    # 1. Pobierz shipment ID
    result, error = get_wysylam_z_allegro_shipments(order_id)
    if error or not result or not result.get('shipments'):
        return jsonify({'success': False, 'error': f'Brak przesyłki do podjazdu: {error or "nie znaleziono"}'}), 400

    shipment = result['shipments'][0]
    shipment_id = shipment.get('id')
    print(f"   → Shipment ID: {shipment_id}")

    # 2. Pobierz propozycje terminów podjazdu
    proposals_result, proposals_error = allegro_request('POST', '/shipment-management/pickup-proposals', data={
        'shipmentIds': [shipment_id]
    })
    print(f"   → Pickup proposals: {proposals_result}")
    if proposals_error:
        print(f"   → Proposals error: {proposals_error}")
        return jsonify({'success': False, 'error': f'Błąd propozycji podjazdu: {proposals_error}'}), 400

    # 3. Wybierz pierwszą dostępną propozycję (lub ANY)
    proposal_id = 'ANY'
    proposals = (proposals_result or {}).get('pickupDateProposals', [])
    if proposals:
        proposal_id = proposals[0].get('id', 'ANY')
        print(f"   → Wybrany termin: {proposals[0].get('date', '?')} (ID: {proposal_id})")
    else:
        print(f"   → Brak propozycji, używam ANY")

    # 4. Zamów podjazd
    command_id = str(uuid.uuid4())
    pickup_result, pickup_error = allegro_request('POST', '/shipment-management/pickups/create-commands', data={
        'commandId': command_id,
        'input': {
            'shipmentIds': [shipment_id],
            'pickupDateProposalId': proposal_id
        }
    })
    print(f"   → Pickup result: {pickup_result}")
    if pickup_error:
        print(f"   → Pickup error: {pickup_error}")
        return jsonify({'success': False, 'error': f'Błąd zamawiania podjazdu: {pickup_error}'}), 400

    return jsonify({
        'success': True,
        'message': 'Podjazd kuriera zamówiony!',
        'proposal_date': proposals[0].get('date', 'najbliższy termin') if proposals else 'najbliższy termin'
    })


@wysylki_bp.route('/wysylki/etykieta/<order_id>')
def wysylki_etykieta(order_id):
    """Pobiera etykietę PDF dla istniejącej przesyłki"""
    from modules.allegro_api import get_shipment_label
    
    label_pdf, shipment_id, error = get_shipment_label(order_id)
    
    if error == "BRAK_PRZESYLKI":
        return f'''
        <html>
        <head><meta charset="utf-8"><title>Brak przesyłki</title>
        <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=swap" rel="stylesheet"></head>
        <body style="font-family:sans-serif;padding:40px;background:#12121a;color:#fff">
            <h2><span class=material-symbols-outlined>inventory_2</span> Przesyłka nie została jeszcze nadana</h2>
            <p>Najpierw nadaj przesyłkę na Allegro, potem wróć po etykietę.</p>
            <a href="https://allegro.pl/moje-allegro/sprzedaz/zamowienia/{order_id}" target="_blank" style="display:inline-block;margin:20px 0;padding:12px 20px;background:#3b82f6;color:#fff;text-decoration:none;border-radius:8px;font-weight:600"><span class=material-symbols-outlined>inventory_2</span> Nadaj na Allegro →</a><br>
            <a href="/wysylki" style="color:#64748b">← Powrót do wysyłek</a>
        </body>
        </html>
        '''
    
    if error:
        return f'''
        <html>
        <head><meta charset="utf-8"><title>Błąd</title>
        <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=swap" rel="stylesheet"></head>
        <body style="font-family:sans-serif;padding:40px;background:#12121a;color:#fff">
            <h2><span class=material-symbols-outlined>cancel</span> Błąd pobierania etykiety</h2>
            <p style="color:#ef4444">{error}</p>
            <a href="https://allegro.pl/moje-allegro/sprzedaz/zamowienia/{order_id}" target="_blank" style="color:#3b82f6;display:block;margin:20px 0"><span class=material-symbols-outlined>inventory_2</span> Pobierz etykietę na Allegro →</a>
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
    print(f"[DELETE] Wyczyszczono {cnt} zamówień → wyslana")
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
        items_html = '<div style="text-align:center;color:var(--text-muted);padding:30px">[CELEBRATION] Wszystkie zamówienia wysłane!</div>'
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

            # Delivery type badge - preferuj metoda_dostawy, fallback na adres
            _md = (first_item.get('metoda_dostawy') or '').lower()
            adres_lower = (first_item.get('adres') or '').lower()
            kupujacy_lower = (first_item.get('kupujacy') or '').lower()
            all_text = _md + ' ' + adres_lower + ' ' + kupujacy_lower
            if 'inpost' in all_text or 'paczkomat' in all_text:
                badge += ' <span style="background:#ffcd00;color:#000;padding:2px 6px;border-radius:4px;font-size:0.65rem;font-weight:800;letter-spacing:0.5px">INPOST</span>'
            elif 'one box' in all_text or 'allegro one' in all_text or 'one-box' in all_text:
                badge += ' <span style="background:#ff5a00;color:#fff;padding:2px 6px;border-radius:4px;font-size:0.65rem;font-weight:800;letter-spacing:0.5px">ALLEGRO ONE</span>'
            elif 'orlen' in all_text:
                badge += ' <span style="background:#e4002b;color:#fff;padding:2px 6px;border-radius:4px;font-size:0.65rem;font-weight:800;letter-spacing:0.5px">ORLEN</span>'
            elif 'dpd' in all_text:
                badge += ' <span style="background:#dc0032;color:#fff;padding:2px 6px;border-radius:4px;font-size:0.65rem;font-weight:800;letter-spacing:0.5px">DPD</span>'
            elif 'dhl' in all_text:
                badge += ' <span style="background:#ffcc00;color:#000;padding:2px 6px;border-radius:4px;font-size:0.65rem;font-weight:800;letter-spacing:0.5px">DHL</span>'
            elif 'ups' in all_text:
                badge += ' <span style="background:#351c15;color:#ffb500;padding:2px 6px;border-radius:4px;font-size:0.65rem;font-weight:800;letter-spacing:0.5px">UPS</span>'
            elif 'kurier' in all_text or 'pocztex' in all_text or 'gls' in all_text:
                badge += ' <span style="background:var(--accent);color:#fff;padding:2px 6px;border-radius:4px;font-size:0.65rem;font-weight:800;letter-spacing:0.5px">KURIER</span>'

            # Status badge: nadana = etykieta wydrukowana
            status_raw = first_item.get('status', 'nowa')
            if status_raw == 'nadana':
                badge += ' <span style="background:var(--blue);color:#fff;padding:2px 6px;border-radius:4px;font-size:0.7rem;font-weight:700"><span class=material-symbols-outlined>inventory_2</span> NADANA</span>'
            
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
                            <span class=material-symbols-outlined>pin_drop</span> {lokalizacja} &nbsp;|&nbsp; <span class="material-symbols-outlined">person</span> {dostawca} &nbsp;|&nbsp; <span class=material-symbols-outlined>label</span> {code}
                        </div>
                        <div style="font-size:0.7rem;color:var(--text-muted);margin-top:2px">
                            <span class=material-symbols-outlined>person</span> {first_item['kupujacy']} · <span class=material-symbols-outlined>location_on</span> {(first_item.get('adres') or 'Brak adresu')[:60]}
                        </div>
                        <div style="font-size:0.65rem;color:var(--text-muted);margin-top:1px">
                            <span class=material-symbols-outlined>calendar_month</span> {data_str}
                        </div>
                    </div>
                    <div style="text-align:right;margin-left:10px">
                        <div style="font-weight:700;color:var(--green);font-size:1.1rem">{total_price:.0f} zł</div>
                        <div style="font-size:0.7rem;color:var(--text-muted)">x{total_qty}</div>
                    </div>
                </label>
                <div style="display:flex;flex-direction:column;gap:4px;margin-left:10px">
                    <a href="/wysylki/oznacz-wyslane?ids={all_ids}" style="padding:6px 10px;background:var(--green);border-radius:6px;color:#fff;text-decoration:none;font-size:0.7rem;font-weight:600;text-align:center"><span class=material-symbols-outlined>check_circle</span> Wysłane</a>
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
        <label style="font-size:0.85rem;color:var(--text-secondary);font-weight:600"><span class="material-symbols-outlined">person</span> UŻYTKOWNIK:</label>
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
            <a href="/wysylki/pakowanie" style="display:block;padding:12px;background:var(--orange);border-radius:10px;color:#fff;text-decoration:none;text-align:center;font-weight:600">[SMARTPHONE] Skanuj</a>
            <a href="/sync-miesiac" onclick="startSync(this)" style="display:block;padding:12px;background:var(--blue);border-radius:10px;color:#fff;text-decoration:none;text-align:center;font-weight:600"><span class=material-symbols-outlined>sync</span> Sync Allegro</a>
            <a href="/wysylki/allegro" style="display:block;padding:12px;background:var(--green);border-radius:10px;color:#fff;text-decoration:none;text-align:center;font-weight:600"><span class=material-symbols-outlined>inventory_2</span> Allegro Live</a>
            <a href="/wysylki/sync-stany" style="display:block;padding:12px;background:var(--accent2);border-radius:10px;color:#fff;text-decoration:none;text-align:center;font-weight:600"><span class=material-symbols-outlined>inventory_2</span> Sync Stany</a>
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
                        ✈ Oznacz jako wysłane
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
        current_user=session.get('username'))


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
    
    flash(f'<span class=material-symbols-outlined>check_circle</span> Oznaczono {updated} produktów jako wysłane', 'success')
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
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=swap" rel="stylesheet">
<style>
.material-symbols-outlined {{ font-family: 'Material Symbols Outlined'; font-size: 1rem; vertical-align: middle; }}
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
<h1><span class=material-symbols-outlined>inventory_2</span> LISTA PAKOWANIA</h1>
<div class="summary">{len(zamowienia)} zamówień · {produkty_cnt} produktów · {wartosc:.0f} zł · {datetime.now().strftime("%d.%m.%Y %H:%M")}</div>
'''

    for i, z in enumerate(zamowienia, 1):
        imgs_html = ''
        names_html = ''
        locs_html = ''
        for p in z['produkty']:
            _placeholder = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='50' height='50'%3E%3Crect fill='%2312121a' width='50' height='50'/%3E%3Ctext x='25' y='30' fill='%23555' text-anchor='middle' font-size='16'%3E%F0%9F%93%A6%3C/text%3E%3C/svg%3E"
            img_src = p['zdjecie_url'] or _placeholder
            imgs_html += f'<img src="{img_src}" onerror="this.src=\'{_placeholder}\'">'
            qty_str = f' <b>(×{p["qty"]})</b>' if p['qty'] > 1 else ''
            names_html += f'<div class="name">{p["name"][:60]}{qty_str}</div>'
            if p['lokalizacja']:
                locs_html += f'<span class="loc"><span class=material-symbols-outlined>inventory_2</span> {p["lokalizacja"]}</span> '

        addr = z['pickup_point'] if z['pickup_point'] else z['address']

        html += f'''<div class="order">
    <div class="order-num">{i}</div>
    <div class="checkbox"></div>
    <div class="order-img">{imgs_html}</div>
    <div class="order-info">
        {names_html}
        <div class="addr"><span class=material-symbols-outlined>pin_drop</span> {addr}</div>
        {locs_html}
    </div>
</div>
'''

    html += '''
<script>window.onload = function() { window.print(); }</script>
</body></html>'''

    return html

@wysylki_bp.route('/wysylki/bulk-nadaj', methods=['POST'])
def bulk_nadaj():
    """Bulk tworzenie przesyłek i etykiet dla zaznaczonych zamówień"""
    from modules.allegro_api import create_and_get_label, get_order_details
    import base64
    import io

    data = request.get_json(silent=True) or {}
    items = data.get('items', [])  # [{order_id, size, dim_l, dim_w, dim_h, dim_kg}, ...]

    if not items:
        return jsonify({'success': False, 'error': 'Brak zamówień do nadania'}), 400

    results = []
    pdf_pages = []
    pickup_orders = []  # orders needing courier pickup (DPD/DHL)

    for i, item in enumerate(items):
        order_id = item.get('order_id', '')
        size = item.get('size')  # A/B/C/S/M/L or None
        # Custom REF (user override) - pojawia sie na etykiecie DPD/InPost
        custom_ref = (item.get('custom_ref') or '').strip()[:50] or None
        dimensions = None

        if item.get('dim_l'):
            dimensions = {
                'length': item.get('dim_l', '30'),
                'width': item.get('dim_w', '25'),
                'height': item.get('dim_h', '15'),
                'weight_kg': item.get('dim_kg', '1'),
            }

        print(f"[BULK] Nadaję {i+1}/{len(items)}: {order_id[:12]}... size={size} dims={dimensions} ref={custom_ref or 'auto'}")

        try:
            label_pdf, shipment_id, error = create_and_get_label(
                order_id, reference=custom_ref, parcel_size=size, dimensions=dimensions
            )
        except Exception as e:
            error = str(e)
            label_pdf, shipment_id = None, None

        if error:
            print(f"[BULK]   FAIL: {error}")
            results.append({'order_id': order_id, 'success': False, 'error': error})
        else:
            print(f"[BULK]   OK: shipment={shipment_id}, pdf={len(label_pdf) if label_pdf else 0}B")
            results.append({'order_id': order_id, 'success': True, 'shipment_id': shipment_id})
            if label_pdf:
                pdf_pages.append(label_pdf)

            # Detect carrier for pickup
            try:
                _ord, _ = get_order_details(order_id)
                _dm = (_ord or {}).get('delivery', {}).get('method', {}).get('name', '').lower()
                if 'orlen' in _dm:
                    # Auto pickup for Orlen (free)
                    try:
                        from modules.allegro_api import allegro_request, get_wysylam_z_allegro_shipments
                        import uuid
                        sr, _ = get_wysylam_z_allegro_shipments(order_id)
                        if sr and sr.get('shipments'):
                            sid = sr['shipments'][0].get('id')
                            pr, _ = allegro_request('POST', '/shipment-management/pickup-proposals', data={'shipmentIds': [sid]})
                            pid = ((pr or {}).get('pickupDateProposals', [{}])[0].get('id', 'ANY')) if pr else 'ANY'
                            allegro_request('POST', '/shipment-management/pickups/create-commands', data={
                                'commandId': str(uuid.uuid4()),
                                'input': {'shipmentIds': [sid], 'pickupDateProposalId': pid}
                            })
                            print(f"[BULK]   Orlen pickup ordered for {order_id[:12]}")
                    except Exception as pe:
                        print(f"[BULK]   Orlen pickup error: {pe}")
                elif 'dpd' in _dm or 'dhl' in _dm:
                    pickup_orders.append(order_id)
            except:
                pass

            # Mark as shipped in DB (wyslana = znika z listy do wysyłki)
            from modules.database import get_db
            conn = get_db()
            conn.execute("UPDATE sprzedaze SET status = 'wyslana' WHERE allegro_order_id = ? AND status IN ('nowa', 'nadana')", (order_id,))
            # Aktualizuj status produktu jeśli ilosc = 0
            _sprzedane = conn.execute(
                "SELECT produkt_id, ilosc FROM sprzedaze WHERE allegro_order_id = ? AND produkt_id IS NOT NULL",
                (order_id,)
            ).fetchall()
            for _s in _sprzedane:
                conn.execute("""
                    UPDATE produkty SET status = 'sprzedany'
                    WHERE id = ? AND ilosc <= 0
                    AND status NOT IN ('sprzedany','wyslany','uszkodzony','zlomowany','naprawa')
                """, (_s['produkt_id'],))
            conn.commit()

    # Merge PDFs into one
    merged_pdf_b64 = None
    if pdf_pages:
        try:
            from pypdf import PdfWriter, PdfReader
            writer = PdfWriter()
            for pdf_data in pdf_pages:
                reader = PdfReader(io.BytesIO(pdf_data))
                for page in reader.pages:
                    writer.add_page(page)
            output = io.BytesIO()
            writer.write(output)
            merged_pdf_b64 = base64.b64encode(output.getvalue()).decode('utf-8')
            print(f"[BULK] Merged {len(pdf_pages)} PDFs, size={len(output.getvalue())}B")
        except Exception as e:
            print(f"[BULK] PDF merge error: {e}")
            if pdf_pages:
                merged_pdf_b64 = base64.b64encode(pdf_pages[0]).decode('utf-8')

    success_count = sum(1 for r in results if r['success'])
    fail_count = sum(1 for r in results if not r['success'])

    return jsonify({
        'success': True,
        'results': results,
        'success_count': success_count,
        'fail_count': fail_count,
        'total': len(items),
        'merged_pdf': merged_pdf_b64,
        'pickup_orders': pickup_orders,
    })


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

    flash(f'<span class=material-symbols-outlined>check_circle</span> Wysłano {len(order_ids)} zamówień ({total_updated} produktów)', 'success')
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
    
    flash(f'<span class=material-symbols-outlined>check_circle</span> Zaktualizowano {updated} produktów, połączono {polaczone} sprzedaży', 'success')
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
            <div style="font-size:3rem;margin-bottom:20px">✈</div>
            <div style="font-size:1.2rem">Oznaczono {len(all_ids)} produktów jako wysłane!</div>
            <div style="color:#64748b;margin-top:10px">Przekierowuję...</div>
        </div>
    </body></html>
    '''

