"""
Moduł sprzedaży — routes dla /sprzedaze/* i /produkt/* (sprzedaż offline)
"""
from flask import Blueprint, request, redirect, session, flash, jsonify, Response, current_app
from datetime import datetime

sprzedaze_bp = Blueprint('sprzedaze', __name__)


def _get_css():
    from modules.shared import CSS
    return CSS


def _normalize_pl(text):
    """Zamienia polskie znaki na ASCII do porównań"""
    if not text:
        return ''
    pl_map = str.maketrans('ąćęłńóśźżĄĆĘŁŃÓŚŹŻ', 'acelnoszzACELNOSZZ')
    return text.translate(pl_map).lower()


# ============================================================
# SPRZEDAŻE I ZWROTY
# ============================================================
@sprzedaze_bp.route('/sprzedaze')
def sprzedaze_lista():
    """Lista sprzedaży z możliwością oznaczenia zwrotów"""
    from modules.database import get_db

    # Filtr miesiąca z query string
    miesiac_filter = request.args.get('miesiac', '')

    # Komunikat z sync zwrotów
    msg = request.args.get('msg', '')
    msg_cnt = request.args.get('cnt', '0')
    msg_detail = request.args.get('detail', '')

    # Domyślnie bieżący miesiąc
    if not miesiac_filter:
        miesiac_filter = datetime.now().strftime('%Y-%m')

    conn = get_db()

    # Pobierz sprzedaże z wybranego miesiąca
    sprzedaze = conn.execute('''
        SELECT s.*,
               COALESCE(p.nazwa, s.nazwa, 'Brak nazwy') as produkt_nazwa
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        LEFT JOIN oferty o ON s.oferta_id = o.id
        WHERE strftime('%Y-%m', s.data_sprzedazy) = ?
          AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
        ORDER BY s.data_sprzedazy DESC
    ''', (miesiac_filter,)).fetchall()

    # Statystyki dla wybranego miesiąca
    stats = conn.execute('''
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status != 'zwrot' THEN cena * ilosc ELSE 0 END) as przychod,
            SUM(CASE WHEN status = 'zwrot' THEN 1 ELSE 0 END) as zwroty_cnt,
            SUM(CASE WHEN status = 'zwrot' THEN cena * ilosc ELSE 0 END) as zwroty_suma
        FROM sprzedaze
        WHERE strftime('%Y-%m', data_sprzedazy) = ?
          AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''', (miesiac_filter,)).fetchone()

    # Lista dostępnych miesięcy
    miesiace_db = conn.execute('''
        SELECT DISTINCT strftime('%Y-%m', data_sprzedazy) as miesiac
        FROM sprzedaze
        ORDER BY miesiac DESC
        LIMIT 12
    ''').fetchall()

    # Generuj opcje select
    miesiace_nazwy = {
        '01': 'Styczeń', '02': 'Luty', '03': 'Marzec', '04': 'Kwiecień',
        '05': 'Maj', '06': 'Czerwiec', '07': 'Lipiec', '08': 'Sierpień',
        '09': 'Wrzesień', '10': 'Październik', '11': 'Listopad', '12': 'Grudzień'
    }

    select_options = ''
    for m in miesiace_db:
        msc = m['miesiac']
        rok = msc[:4]
        msc_num = msc[5:7]
        nazwa = f"{miesiace_nazwy.get(msc_num, msc_num)} {rok}"
        selected = 'selected' if msc == miesiac_filter else ''
        select_options += f'<option value="{msc}" {selected}>{nazwa}</option>'

    items_html = ''
    for s in sprzedaze:
        is_zwrot = s['status'] == 'zwrot'
        is_manual = (s['allegro_order_id'] or '').startswith('MANUAL-')
        status_badge = '<span style="color:#ef4444;font-size:0.75rem">🔄 ZWROT</span>' if is_zwrot else ''
        opacity = '0.5' if is_zwrot else '1'

        # Nazwa produktu - kilka źródeł
        try:
            nazwa = s['produkt_nazwa'] or s['nazwa'] or ''
        except (IndexError, KeyError):
            try:
                nazwa = s['produkt_nazwa'] or ''
            except (IndexError, KeyError):
                nazwa = ''
        if not nazwa or nazwa == 'Produkt':
            # Fallback - użyj kupującego ale zaznacz że brak nazwy
            nazwa = f"Zamówienie od {s['kupujacy']}"

        # Formatuj datę ładnie
        data_raw = s['data_sprzedazy'] or ''
        if 'T' in data_raw:
            data_str = data_raw[:10]  # YYYY-MM-DD
        else:
            data_str = data_raw[:10]

        # Dzień i miesiąc
        try:
            parts = data_str.split('-')
            dzien = parts[2] if len(parts) >= 3 else '??'
            miesiac_num = int(parts[1]) if len(parts) >= 2 else 0
            miesiace = ['', 'STY', 'LUT', 'MAR', 'KWI', 'MAJ', 'CZE', 'LIP', 'SIE', 'WRZ', 'PAŹ', 'LIS', 'GRU']
            miesiac = miesiace[miesiac_num] if 0 < miesiac_num <= 12 else '???'
        except:
            dzien = '??'
            miesiac = '???'

        # Określ przycisk akcji
        if is_manual:
            akcja_btn = f'<form method="POST" action="/sprzedaze/usun/{s["id"]}" style="display:inline;margin:0" onsubmit="return confirm(\'Usunąć tę sprzedaż i przywrócić ilość?\')"><input type="hidden" name="miesiac" value="{miesiac_filter}"><button type="submit" style="padding:6px 10px;background:#f97316;border-radius:6px;color:#fff;border:none;cursor:pointer;font-size:0.75rem">🗑️ Usuń</button></form>'
        elif is_zwrot:
            akcja_btn = f'<form method="POST" action="/sprzedaze/unzwrot/{s["id"]}" style="display:inline;margin:0"><input type="hidden" name="miesiac" value="{miesiac_filter}"><button type="submit" style="padding:6px 10px;background:#22c55e;border-radius:6px;color:#fff;border:none;cursor:pointer;font-size:0.75rem">Cofnij</button></form>'
        else:
            akcja_btn = f'<form method="POST" action="/sprzedaze/zwrot/{s["id"]}" style="display:inline;margin:0"><input type="hidden" name="miesiac" value="{miesiac_filter}"><button type="submit" style="padding:6px 10px;background:#ef4444;border-radius:6px;color:#fff;border:none;cursor:pointer;font-size:0.75rem">Zwrot</button></form>'

        items_html += f'''
        <div style="display:flex;align-items:center;background:#12121a;border:1px solid #1e1e2e;border-radius:10px;padding:12px;margin-bottom:8px;opacity:{opacity}">
            <div style="min-width:50px;text-align:center;margin-right:12px;padding-right:12px;border-right:1px solid #2a2a3a">
                <div style="font-size:1.3rem;font-weight:700;color:#3b82f6">{dzien}</div>
                <div style="font-size:0.65rem;color:#64748b">{miesiac}</div>
            </div>
            <div style="flex:1;min-width:0">
                <div style="font-weight:600;font-size:0.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{nazwa[:40]}</div>
                <div style="font-size:0.75rem;color:#64748b">{s['kupujacy']} {status_badge}</div>
            </div>
            <div style="text-align:right;margin-left:10px">
                <div style="font-weight:700;color:{'#ef4444' if is_zwrot else '#22c55e'}">{'-' if is_zwrot else ''}{s['cena']:.0f} zł</div>
                <div style="font-size:0.7rem;color:#64748b">x{s['ilosc']}</div>
            </div>
            <div style="margin-left:10px">
                {akcja_btn}
            </div>
        </div>
        '''

    # Nazwa wybranego miesiąca do wyświetlenia
    msc_num = miesiac_filter[5:7] if len(miesiac_filter) >= 7 else '01'
    msc_rok = miesiac_filter[:4] if len(miesiac_filter) >= 4 else '2026'
    msc_nazwa = f"{miesiace_nazwy.get(msc_num, msc_num)} {msc_rok}"

    # Komunikat z sync zwrotów
    msg_html = ''
    if msg == 'success':
        msg_html = f'<div style="background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.3);padding:12px;border-radius:8px;margin-bottom:15px;text-align:center;color:#22c55e">✅ Oznaczono {msg_cnt} zwrotów z Allegro</div>'
    elif msg == 'none':
        msg_html = '<div style="background:rgba(234,179,8,0.15);border:1px solid rgba(234,179,8,0.3);padding:12px;border-radius:8px;margin-bottom:15px;text-align:center;color:#eab308">ℹ️ Brak nowych zwrotów w Allegro</div>'
    elif msg == 'allegro_auth':
        msg_html = '<div style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);padding:12px;border-radius:8px;margin-bottom:15px;text-align:center;color:#ef4444">❌ Zaloguj się do Allegro</div>'
    elif msg == 'error':
        msg_html = f'<div style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);padding:12px;border-radius:8px;margin-bottom:15px;text-align:center;color:#ef4444">❌ Błąd: {msg_detail}</div>'
    elif msg == 'naprawiono':
        msg_html = f'<div style="background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.3);padding:12px;border-radius:8px;margin-bottom:15px;text-align:center;color:#22c55e">✅ Naprawiono dane {msg_cnt} produktów (nazwy + zdjęcia)</div>'

    CSS = _get_css()
    html = CSS + f'''
    <div class="container">
        <div class="header">
            <h1>💰 SPRZEDAŻE</h1>
            <small>Lista zamówień i zwroty</small>
        </div>

        <!-- Filtr miesiąca -->
        <div style="margin-bottom:15px">
            <select onchange="window.location.href='/sprzedaze?miesiac='+this.value"
                    style="width:100%;padding:12px;background:#12121a;border:1px solid #3b82f6;border-radius:8px;color:#fff;font-size:1rem;cursor:pointer">
                {select_options}
            </select>
        </div>

        <!-- Przyciski akcji -->
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:15px">
            <a href="/sync-custom?from={miesiac_filter}-01"
               style="display:block;text-align:center;padding:10px;background:#f59e0b;border-radius:8px;color:#fff;text-decoration:none;font-weight:600;font-size:0.85rem">
                🔄 Sync miesiąc
            </a>
            <a href="/sprzedaze/sync-zwroty?miesiac={miesiac_filter}"
               style="display:block;text-align:center;padding:10px;background:#ef4444;border-radius:8px;color:#fff;text-decoration:none;font-weight:600;font-size:0.85rem">
                🔄 Sync zwrotów
            </a>
            <a href="/sprzedaze/napraw-nazwy?miesiac={miesiac_filter}"
               style="display:block;text-align:center;padding:10px;background:#3b82f6;border-radius:8px;color:#fff;text-decoration:none;font-weight:600;font-size:0.85rem">
                🔧 Napraw dane
            </a>
            <a href="/sprzedaze/dopasuj"
               style="display:block;text-align:center;padding:10px;background:#8b5cf6;border-radius:8px;color:#fff;text-decoration:none;font-weight:600;font-size:0.85rem">
                🔗 Dopasuj
            </a>
        </div>

        {msg_html}

        <!-- Statystyki miesiąca -->
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px">
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#22c55e">{stats['przychod'] or 0:.0f} zł</div>
                <div style="font-size:0.7rem;color:#64748b">PRZYCHÓD</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#ef4444">{stats['zwroty_cnt'] or 0}</div>
                <div style="font-size:0.7rem;color:#64748b">ZWROTÓW</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#ef4444">-{stats['zwroty_suma'] or 0:.0f} zł</div>
                <div style="font-size:0.7rem;color:#64748b">WARTOŚĆ ZWROTÓW</div>
            </div>
        </div>

        <div style="font-size:0.8rem;color:#64748b;margin-bottom:10px">{msc_nazwa.upper()} ({len(sprzedaze)} zamówień)</div>

        {items_html if items_html else '<div style="text-align:center;color:#64748b;padding:30px">Brak sprzedaży w tym miesiącu</div>'}

        <a href="/statystyki" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:20px">← Statystyki</a>
    </div>
    '''
    return html


@sprzedaze_bp.route('/sprzedaze/zwrot/<int:sale_id>', methods=['POST'])
def oznacz_zwrot(sale_id):
    """Oznacza sprzedaż jako zwrot"""
    from modules.database import get_db
    conn = get_db()
    conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('zwrot', sale_id))
    conn.commit()
    # Zachowaj filtr miesiąca
    miesiac = request.form.get('miesiac', '')
    return redirect(f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze')


@sprzedaze_bp.route('/sprzedaze/unzwrot/<int:sale_id>', methods=['POST'])
def cofnij_zwrot(sale_id):
    """Cofa oznaczenie zwrotu"""
    from modules.database import get_db
    conn = get_db()
    conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('wyslana', sale_id))
    conn.commit()
    # Zachowaj filtr miesiąca
    miesiac = request.form.get('miesiac', '')
    return redirect(f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze')


@sprzedaze_bp.route('/sprzedaze/usun/<int:sale_id>', methods=['POST'])
def usun_sprzedaz(sale_id):
    """Usuwa sprzedaż (ręczną korektę) i przywraca ilość produktu"""
    from modules.database import get_db
    miesiac = request.form.get('miesiac', '')

    conn = get_db()

    # Pobierz dane sprzedaży
    sprzedaz = conn.execute('SELECT * FROM sprzedaze WHERE id = ?', (sale_id,)).fetchone()

    if not sprzedaz:
        flash('Nie znaleziono sprzedaży', 'error')
        return redirect(f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze')

    # Sprawdź czy to ręczna korekta
    if not (sprzedaz['allegro_order_id'] or '').startswith('MANUAL-'):
        flash('Można usuwać tylko ręczne korekty', 'error')
        return redirect(f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze')

    # Przywróć ilość produktu
    if sprzedaz['produkt_id']:
        conn.execute('''
            UPDATE produkty
            SET ilosc = ilosc + ?,
                status = CASE WHEN status = 'sprzedany' THEN 'wystawiony' ELSE status END
            WHERE id = ?
        ''', (sprzedaz['ilosc'], sprzedaz['produkt_id']))

    # Usuń wpis sprzedaży
    conn.execute('DELETE FROM sprzedaze WHERE id = ?', (sale_id,))

    conn.commit()

    flash(f'✅ Usunięto sprzedaż i przywrócono {sprzedaz["ilosc"]} szt. do magazynu', 'success')
    return redirect(f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze')


@sprzedaze_bp.route('/sprzedaze/sync-zwroty')
def sync_zwroty_allegro():
    """Synchronizuje zwroty z Allegro API dla wybranego miesiąca"""
    from modules.allegro_api import sync_returns, is_authenticated

    miesiac = request.args.get('miesiac', '')
    base_url = f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze'

    if not is_authenticated():
        return redirect(f'{base_url}&msg=allegro_auth')

    try:
        updated, error = sync_returns(miesiac if miesiac else None)
        if error:
            return redirect(f'{base_url}&msg=error&detail={error[:50]}')
        elif updated > 0:
            return redirect(f'{base_url}&msg=success&cnt={updated}')
        else:
            return redirect(f'{base_url}&msg=none')
    except Exception as e:
        print(f"❌ Błąd sync_returns: {e}")
        return redirect(f'{base_url}&msg=error&detail={str(e)[:50]}')


@sprzedaze_bp.route('/sprzedaze/napraw-nazwy')
def napraw_nazwy_sprzedazy():
    """Uzupełnia brakujące nazwy, zdjęcia i daty w sprzedażach z Allegro API"""
    from modules.allegro_api import is_authenticated, allegro_request
    from modules.database import get_db

    miesiac = request.args.get('miesiac', '')
    if not miesiac:
        miesiac = datetime.now().strftime('%Y-%m')

    if not is_authenticated():
        return redirect(f'/sprzedaze?miesiac={miesiac}&msg=allegro_auth')

    conn = get_db()

    # Upewnij się że kolumny istnieją
    try:
        conn.execute('ALTER TABLE sprzedaze ADD COLUMN nazwa TEXT DEFAULT ""')
    except:
        pass
    try:
        conn.execute('ALTER TABLE sprzedaze ADD COLUMN zdjecie_url TEXT DEFAULT ""')
    except:
        pass

    # Pobierz sprzedaże z wybranego miesiąca bez nazwy/zdjęcia
    try:
        sprzedaze = conn.execute('''
            SELECT s.id, s.allegro_order_id, s.nazwa, s.zdjecie_url, s.oferta_id,
                   s.data_sprzedazy,
                   COALESCE(o.tytul, s.nazwa, '') as oferta_tytul, p.zdjecie_url as produkt_zdjecie
            FROM sprzedaze s
            LEFT JOIN oferty o ON s.oferta_id = o.id
            LEFT JOIN produkty p ON COALESCE(s.produkt_id, o.produkt_id) = p.id
            WHERE (s.nazwa IS NULL OR s.nazwa = '' OR s.nazwa = 'Produkt'
                   OR s.zdjecie_url IS NULL OR s.zdjecie_url = '')
            AND s.allegro_order_id IS NOT NULL
            AND strftime('%Y-%m', s.data_sprzedazy) = ?
            LIMIT 100
        ''', (miesiac,)).fetchall()
    except Exception as e:
        print(f"Query error: {e}")
        sprzedaze = conn.execute('''
            SELECT s.id, s.allegro_order_id, s.oferta_id, s.data_sprzedazy,
                   COALESCE(o.tytul, s.nazwa, '') as oferta_tytul
            FROM sprzedaze s
            LEFT JOIN oferty o ON s.oferta_id = o.id
            WHERE strftime('%Y-%m', s.data_sprzedazy) = ?
            LIMIT 100
        ''', (miesiac,)).fetchall()

    updated = 0

    # Helper do bezpiecznego dostępu do sqlite3.Row
    def safe_get(row, key, default=None):
        try:
            val = row[key]
            return val if val else default
        except (KeyError, IndexError):
            return default

    for s in sprzedaze:
        new_name = safe_get(s, 'nazwa')
        if new_name == 'Produkt':
            new_name = None
        new_image = safe_get(s, 'zdjecie_url') or safe_get(s, 'produkt_zdjecie')

        # Metoda 1: z tabeli oferty/produkty
        if not new_name and safe_get(s, 'oferta_tytul'):
            new_name = s['oferta_tytul'][:100]

        # Metoda 2: pobierz z Allegro API (nazwa + zdjęcie + popraw datę)
        new_date = None
        if s['allegro_order_id']:
            try:
                order_data, err = allegro_request('GET', f"/order/checkout-forms/{s['allegro_order_id']}")
                if order_data:
                    # Popraw datę z boughtAt
                    bought_at = order_data.get('boughtAt', '')
                    if bought_at:
                        try:
                            from datetime import datetime as _dt
                            dt_str = bought_at.replace('Z', '+00:00')
                            dt = _dt.fromisoformat(dt_str)
                            dt_local = dt.astimezone().replace(tzinfo=None)
                            correct_date = dt_local.strftime('%Y-%m-%d %H:%M:%S')
                            current_date = safe_get(s, 'data_sprzedazy', '')
                            # Napraw jeśli data jest inna (inny dzień)
                            if correct_date[:10] != (current_date or '')[:10]:
                                new_date = correct_date
                                print(f"📅 Poprawiam datę: {s['id']} {current_date[:10]} → {correct_date[:10]}")
                        except Exception as de:
                            print(f"Date parse error: {de}")

                    if 'lineItems' in order_data:
                        for item in order_data['lineItems']:
                            offer = item.get('offer', {})
                            if not new_name:
                                name = offer.get('name', '')
                                if name:
                                    new_name = name[:100]
                            # Pobierz zdjęcie z oferty
                            if not new_image:
                                offer_id = offer.get('id')
                                if offer_id:
                                    try:
                                        offer_data, _ = allegro_request('GET', f'/sale/product-offers/{offer_id}')
                                        if offer_data:
                                            images = offer_data.get('images', [])
                                            if images:
                                                new_image = images[0].get('url', '')
                                    except:
                                        pass
                            if new_name:
                                break
            except Exception as e:
                print(f"API error: {e}")

        # Aktualizuj jeśli znaleziono coś nowego (nazwa, zdjęcie lub data)
        if new_name or new_image or new_date:
            try:
                updates = []
                params = []
                if new_name:
                    updates.append('nazwa = ?')
                    params.append(new_name)
                if new_image:
                    updates.append('zdjecie_url = ?')
                    params.append(new_image)
                if new_date:
                    updates.append('data_sprzedazy = ?')
                    params.append(new_date)
                params.append(s['id'])
                set_clause = ", ".join(updates)
                conn.execute('UPDATE sprzedaze SET ' + set_clause + ' WHERE id = ?', params)  # noqa: B608
                updated += 1
                date_info = f' | data: {new_date[:10]}' if new_date else ''
                print(f"✅ Naprawiono: {s['id']} -> {(new_name or '')[:40]}... | img: {'✓' if new_image else '✗'}{date_info}")
            except Exception as e:
                print(f"❌ Błąd update: {e}")

    conn.commit()

    return redirect(f'/sprzedaze?miesiac={miesiac}&msg=naprawiono&cnt={updated}')


@sprzedaze_bp.route('/sprzedaze/dodaj-reczna', methods=['POST'])
def sprzedaze_dodaj_reczna():
    """Ręczne dodanie sprzedaży (korekta)"""
    from modules.database import get_db
    from datetime import datetime

    produkt_id = request.form.get('produkt_id', type=int)
    ilosc = request.form.get('ilosc', 1, type=int)
    cena = request.form.get('cena', 0, type=float)
    kupujacy = request.form.get('kupujacy', 'Ręczna korekta')

    if not produkt_id:
        flash('Brak ID produktu', 'error')
        return redirect(request.referrer or '/palety')

    conn = get_db()

    # Pobierz dane produktu
    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        flash('Nie znaleziono produktu', 'error')
        return redirect(request.referrer or '/palety')

    # Dodaj wpis do sprzedaze
    conn.execute('''INSERT INTO sprzedaze
        (allegro_order_id, cena, ilosc, kupujacy, status, data_sprzedazy, produkt_id, nazwa)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (f'MANUAL-{datetime.now().strftime("%Y%m%d%H%M%S")}', cena, ilosc, kupujacy,
         'nowa', datetime.now().isoformat(), produkt_id, produkt['nazwa']))

    # Zaktualizuj ilość w produkcie
    new_qty = max(0, produkt['ilosc'] - ilosc)
    conn.execute('''UPDATE produkty SET
        ilosc = ?,
        status = CASE WHEN ? = 0 THEN 'sprzedany' ELSE status END
        WHERE id = ?''', (new_qty, new_qty, produkt_id))

    conn.commit()

    flash(f'✅ Dodano sprzedaż: {ilosc} szt. za {cena:.0f} zł', 'success')
    return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')


# ==================== DOPASOWYWANIE SPRZEDAZY ====================

@sprzedaze_bp.route('/api/sprzedaze/szukaj-produkt')
def api_sprzedaze_szukaj_produkt():
    """API - wyszukuje produkty (multi-word, diacritics-insensitive)"""
    from modules.database import get_db
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'results': []})

    conn = get_db()

    # Normalizuj słowa do porównań (bez polskich znaków)
    words = [_normalize_pl(w) for w in q.split() if len(w) >= 2][:6]
    if not words:
        return jsonify({'results': []})

    # Pobierz wszystkie produkty (188 rekordów — szybko)
    all_products = conn.execute('''
        SELECT p.id, p.nazwa, p.ean, p.asin, p.ilosc, p.cena_allegro, p.zdjecie_url,
               COALESCE(pal.nazwa, '') as paleta_nazwa
        FROM produkty p
        LEFT JOIN palety pal ON p.paleta_id = pal.id
        ORDER BY p.id DESC
    ''').fetchall()

    # Filtruj w Pythonie (normalizacja polskich znaków)
    scored = []
    q_norm = _normalize_pl(q)
    min_match = max(2, int(len(words) * 0.6))  # min 60% słów lub 2

    for p in all_products:
        nazwa_norm = _normalize_pl(p['nazwa'] or '')
        ean = (p['ean'] or '').lower()
        asin = (p['asin'] or '').lower()

        # EAN/ASIN exact match — priorytet
        if q_norm in ean or q_norm in asin:
            scored.append((100, p))
            continue

        # Multi-word: liczymy ile słów matchuje
        hits = sum(1 for w in words if w in nazwa_norm)
        if hits >= min_match:
            scored.append((hits, p))

    # Sortuj: najlepsze dopasowanie na górze
    scored.sort(key=lambda x: -x[0])
    results = [p for _, p in scored[:15]]

    return jsonify({'results': [
        {'id': r['id'], 'nazwa': r['nazwa'], 'ean': r['ean'] or '', 'asin': r['asin'] or '',
         'ilosc': r['ilosc'], 'cena_allegro': r['cena_allegro'] or 0,
         'zdjecie_url': r['zdjecie_url'] or '', 'paleta': r['paleta_nazwa']}
        for r in results
    ]})


@sprzedaze_bp.route('/api/sprzedaze/dopasuj', methods=['POST'])
def api_sprzedaze_dopasuj():
    """API - dopasowuje grupę sprzedaży do produktu + historia"""
    from modules.database import get_db, add_historia

    sale_ids_str = request.form.get('sale_ids', '')
    produkt_id = request.form.get('produkt_id', type=int)

    if not sale_ids_str or not produkt_id:
        return jsonify({'ok': False, 'msg': 'Brak danych'}), 400

    try:
        sale_ids = [int(x.strip()) for x in sale_ids_str.split(',') if x.strip()]
    except ValueError:
        return jsonify({'ok': False, 'msg': 'Nieprawidłowe ID'}), 400

    if not sale_ids:
        return jsonify({'ok': False, 'msg': 'Brak ID sprzedaży'}), 400

    conn = get_db()

    produkt = conn.execute('SELECT id, nazwa, ilosc FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        return jsonify({'ok': False, 'msg': 'Produkt nie znaleziony'}), 404

    # Pobierz szczegóły sprzedaży PRZED update (do historii)
    placeholders = ','.join(['?' for _ in sale_ids])
    sprzedaze = conn.execute(
        'SELECT id, nazwa, cena, ilosc, data_sprzedazy'
        ' FROM sprzedaze WHERE id IN (' + placeholders + ') AND produkt_id IS NULL',
        sale_ids).fetchall()

    # Update produkt_id
    updated = conn.execute(
        'UPDATE sprzedaze SET produkt_id = ?'
        ' WHERE id IN (' + placeholders + ') AND produkt_id IS NULL',
        [produkt_id] + sale_ids)

    # Dodaj historię dla każdej dopasowanej sprzedaży
    for s in sprzedaze:
        przychod = (s['cena'] or 0) * (s['ilosc'] or 1)
        try:
            add_historia(produkt_id, 'sprzedano',
                f'Dopasowano sprzedaż #{s["id"]}: {s["ilosc"] or 1} szt. za {przychod:.0f} zł ({s["data_sprzedazy"][:10] if s["data_sprzedazy"] else "?"})',
                {'sprzedaz_id': s['id'], 'cena': s['cena'], 'ilosc': s['ilosc'],
                 'data_sprzedazy': s['data_sprzedazy'], 'zrodlo': 'dopasowanie'})
        except:
            pass

    conn.commit()

    return jsonify({
        'ok': True,
        'matched': updated.rowcount,
        'product_name': produkt['nazwa']
    })


@sprzedaze_bp.route('/api/sprzedaze/auto-dopasuj', methods=['POST'])
def api_sprzedaze_auto_dopasuj():
    """API - automatycznie dopasowuje wszystkie sugestie"""
    from modules.database import get_db, add_historia

    conn = get_db()

    grupy = conn.execute('''
        SELECT TRIM(nazwa) as grupa_nazwa, GROUP_CONCAT(id) as sale_ids, COUNT(*) as cnt
        FROM sprzedaze
        WHERE produkt_id IS NULL AND nazwa IS NOT NULL AND TRIM(nazwa) != ''
          AND status NOT IN ('anulowana', 'zwrot')
        GROUP BY TRIM(nazwa)
    ''').fetchall()

    matched_groups = 0
    total_sales = 0

    for g in grupy:
        # Multi-word matching
        words = [w for w in g['grupa_nazwa'].split() if len(w) >= 3][:4]
        if len(words) < 2:
            continue

        where_parts = []
        params = []
        for w in words:
            where_parts.append("LOWER(nazwa) LIKE ?")
            params.append(f'%{w.lower()}%')

        where_clause = ' AND '.join(where_parts)
        match = conn.execute(
            'SELECT id FROM produkty WHERE ' + where_clause + ' LIMIT 1',
            params).fetchone()

        if match:
            sale_ids = [int(x) for x in g['sale_ids'].split(',')]
            ph = ','.join(['?' for _ in sale_ids])

            # Pobierz szczegóły sprzedaży PRZED update (do historii)
            sprzedaze = conn.execute(
                'SELECT id, nazwa, cena, ilosc, data_sprzedazy'
                ' FROM sprzedaze WHERE id IN (' + ph + ') AND produkt_id IS NULL',
                sale_ids).fetchall()

            conn.execute(
                'UPDATE sprzedaze SET produkt_id = ?'
                ' WHERE id IN (' + ph + ') AND produkt_id IS NULL',
                [match['id']] + sale_ids)

            # Dodaj historię dla każdej dopasowanej sprzedaży
            for s in sprzedaze:
                przychod = (s['cena'] or 0) * (s['ilosc'] or 1)
                try:
                    add_historia(match['id'], 'sprzedano',
                        f'Auto-dopasowano sprzedaż #{s["id"]}: {s["ilosc"] or 1} szt. za {przychod:.0f} zł ({s["data_sprzedazy"][:10] if s["data_sprzedazy"] else "?"})',
                        {'sprzedaz_id': s['id'], 'cena': s['cena'], 'ilosc': s['ilosc'],
                         'data_sprzedazy': s['data_sprzedazy'], 'zrodlo': 'auto-dopasowanie'})
                except:
                    pass

            matched_groups += 1
            total_sales += g['cnt']

    conn.commit()

    return jsonify({
        'ok': True,
        'matched': matched_groups,
        'total_sales': total_sales
    })


@sprzedaze_bp.route('/api/sprzedaze/repair', methods=['POST'])
def api_sprzedaze_repair():
    """Naprawa danych: usunięcie duplikatów + aktualizacja stanów magazynowych"""
    from modules.database import get_db
    conn = get_db()
    repairs = []

    # === 1. Usuń duplikaty zamówień ===
    dupes = conn.execute('''
        SELECT allegro_order_id, nazwa, cena, COUNT(*) as cnt,
               MIN(id) as keep_id, GROUP_CONCAT(id) as all_ids
        FROM sprzedaze
        WHERE allegro_order_id IS NOT NULL
        GROUP BY allegro_order_id, nazwa, cena
        HAVING COUNT(*) > 1
    ''').fetchall()

    removed = 0
    for d in dupes:
        all_ids = [int(x) for x in d['all_ids'].split(',')]
        to_delete = [i for i in all_ids if i != d['keep_id']]
        if to_delete:
            ph = ','.join(['?' for _ in to_delete])
            conn.execute('DELETE FROM sprzedaze WHERE id IN (' + ph + ')', to_delete)
            removed += len(to_delete)
    if removed:
        repairs.append(f'Usunięto {removed} duplikatów zamówień')

    # === 2. Przelicz stany magazynowe na podstawie sprzedaży ===
    # Pobierz ile sztuk sprzedano per produkt
    sold = conn.execute('''
        SELECT produkt_id, SUM(ilosc) as sold_qty
        FROM sprzedaze
        WHERE produkt_id IS NOT NULL
        AND status NOT IN ('anulowana', 'zwrot')
        GROUP BY produkt_id
    ''').fetchall()

    stock_fixed = 0
    for s in sold:
        pid = s['produkt_id']
        sold_qty = s['sold_qty'] or 0

        # Pobierz oryginalną ilość z palety (zakupowa)
        prod = conn.execute('''
            SELECT p.id, p.ilosc, p.nazwa, p.status,
                   COALESCE(p.ilosc + (SELECT COALESCE(SUM(sp.ilosc), 0)
                       FROM sprzedaze sp WHERE sp.produkt_id = p.id
                       AND sp.status NOT IN ('anulowana', 'zwrot')), p.ilosc) as original_qty
            FROM produkty p WHERE p.id = ?
        ''', (pid,)).fetchone()

        if not prod:
            continue

        # Oblicz prawidłową ilość
        correct_qty = max(0, prod['ilosc'])  # Obecna ilość

        # Jeśli status nie jest 'sprzedany' ale ilosc powinna być 0
        if sold_qty > 0 and prod['ilosc'] > 0:
            # Sprawdź czy stock został odjęty - porównaj oczekiwane
            pass  # Stock jest już odjęty przez sync

        # Jeśli ilosc=0 ale status nie jest 'sprzedany'
        if prod['ilosc'] <= 0 and prod['status'] not in ('sprzedany', 'wysłane'):
            conn.execute("UPDATE produkty SET status = 'sprzedany' WHERE id = ?", (pid,))
            stock_fixed += 1

        # Jeśli ilosc > 0 ale status jest 'sprzedany' (błędnie oznaczony)
        if prod['ilosc'] > 0 and prod['status'] == 'sprzedany':
            conn.execute("UPDATE produkty SET status = 'wystawiony' WHERE id = ?", (pid,))
            stock_fixed += 1

    if stock_fixed:
        repairs.append(f'Naprawiono status {stock_fixed} produktów')

    # === 3. Linkowanie przez tabelę oferty (allegro_id → produkt_id) ===
    linked = 0
    unlinked = conn.execute('''
        SELECT s.id, s.allegro_order_id
        FROM sprzedaze s
        WHERE s.produkt_id IS NULL AND s.oferta_id IS NOT NULL
        AND s.status NOT IN ('anulowana', 'zwrot')
    ''').fetchall()
    for sale in unlinked:
        oferta = conn.execute('SELECT produkt_id FROM oferty WHERE id = ?', (sale['oferta_id'],)).fetchone()
        if oferta and oferta['produkt_id']:
            conn.execute('UPDATE sprzedaze SET produkt_id = ? WHERE id = ?', (oferta['produkt_id'], sale['id']))
            linked += 1
    if linked:
        repairs.append(f'Połączono {linked} sprzedaży przez oferty')

    conn.commit()

    return jsonify({
        'ok': True,
        'repairs': repairs,
        'removed_duplicates': removed,
        'stock_fixed': stock_fixed,
        'linked': linked
    })


@sprzedaze_bp.route('/sprzedaze/dopasuj')
def sprzedaze_dopasuj():
    """Strona dopasowywania sprzedaży do produktów"""
    from modules.database import get_db
    import html as html_mod

    conn = get_db()

    # Grupuj niedopasowane sprzedaże po nazwie
    grupy = conn.execute('''
        SELECT
            COALESCE(NULLIF(TRIM(nazwa), ''), '(brak nazwy)') as grupa_nazwa,
            COUNT(*) as cnt,
            SUM(cena * ilosc) as wartosc,
            GROUP_CONCAT(id) as sale_ids
        FROM sprzedaze
        WHERE produkt_id IS NULL
          AND status NOT IN ('anulowana', 'zwrot')
        GROUP BY CASE
            WHEN nazwa IS NULL OR TRIM(nazwa) = '' THEN '(brak nazwy)'
            ELSE TRIM(nazwa)
        END
        ORDER BY cnt DESC
    ''').fetchall()

    total_unmatched = sum(g['cnt'] for g in grupy)

    # Auto-sugestie — szukaj produktu po nazwie
    suggestions = {}
    for g in grupy:
        if g['grupa_nazwa'] == '(brak nazwy)':
            continue
        # Multi-word matching: weź 3-4 kluczowe słowa i szukaj AND
        words = [w for w in g['grupa_nazwa'].split() if len(w) >= 3][:4]
        if len(words) < 2:
            continue
        where_parts = []
        params = []
        for w in words:
            where_parts.append("LOWER(nazwa) LIKE ?")
            params.append(f'%{w.lower()}%')
        where_clause = ' AND '.join(where_parts)
        match = conn.execute(
            'SELECT id, nazwa, zdjecie_url FROM produkty'
            ' WHERE ' + where_clause + ' ORDER BY id DESC LIMIT 1',
            params).fetchone()
        if match:
            suggestions[g['grupa_nazwa']] = dict(match)

    # Buduj HTML grup
    groups_html = ''
    for g in grupy:
        nazwa = html_mod.escape(g['grupa_nazwa'])
        nazwa_js = html_mod.escape(g['grupa_nazwa']).replace("'", "\\'").replace('"', '&quot;')
        sale_ids = g['sale_ids']
        cnt = g['cnt']
        wartosc = g['wartosc'] or 0

        sug = suggestions.get(g['grupa_nazwa'])
        sug_html = ''
        if sug:
            sug_nazwa = html_mod.escape(sug['nazwa'][:55])
            sug_img = html_mod.escape(sug.get('zdjecie_url') or '')
            sug_html = f'''
            <div style="background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);border-radius:8px;padding:8px;margin-top:8px;display:flex;align-items:center;gap:8px">
                <img src="{sug_img}" style="width:32px;height:32px;object-fit:contain;background:#fff;border-radius:6px" onerror="this.style.display='none'">
                <div style="flex:1;min-width:0">
                    <div style="font-size:0.75rem;color:#22c55e">Sugestia:</div>
                    <div style="font-size:0.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{sug_nazwa}</div>
                </div>
                <button onclick="dopasuj('{sale_ids}', {sug['id']}, this)"
                        style="padding:6px 12px;background:#22c55e;border:none;border-radius:6px;color:#fff;font-size:0.75rem;cursor:pointer;white-space:nowrap">
                    ✓ Dopasuj
                </button>
            </div>'''

        groups_html += f'''
        <div class="grupa-item" style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:14px;margin-bottom:10px">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
                <div style="flex:1;min-width:0">
                    <div style="font-weight:600;font-size:0.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#fff">{nazwa[:60]}</div>
                    <div style="font-size:0.75rem;color:#64748b">{cnt} szt. | {wartosc:.0f} zł</div>
                </div>
                <button onclick="openSearch('{nazwa_js}', '{sale_ids}')"
                        style="padding:8px 14px;background:#3b82f6;border:none;border-radius:8px;color:#fff;font-size:0.8rem;cursor:pointer;white-space:nowrap">
                    🔍 Szukaj
                </button>
            </div>
            {sug_html}
        </div>
        '''

    # Przycisk auto-dopasuj (tylko gdy są sugestie)
    auto_btn_html = ''
    if suggestions:
        auto_btn_html = f'''
        <button onclick="autoMatchAll()"
                style="width:100%;padding:14px;background:linear-gradient(135deg,#22c55e,#16a34a);border:none;border-radius:10px;color:#fff;font-weight:700;font-size:1rem;cursor:pointer;margin-bottom:20px">
            ⚡ Auto-dopasuj {len(suggestions)} sugestii
        </button>
        '''

    CSS = _get_css()
    page_html = CSS + f'''
    <div style="max-width:700px;margin:0 auto;padding:15px 15px 100px">
        <div style="text-align:center;margin-bottom:20px">
            <h1 style="color:#fff;font-size:1.5rem;margin:0">🔗 DOPASUJ SPRZEDAŻE</h1>
            <small style="color:#64748b">Połącz niedopasowane sprzedaże z produktami</small>
        </div>

        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px">
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#ef4444">{total_unmatched}</div>
                <div style="font-size:0.7rem;color:#64748b">NIEDOPASOWANYCH</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#f59e0b">{len(grupy)}</div>
                <div style="font-size:0.7rem;color:#64748b">GRUP</div>
            </div>
            <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:12px;padding:15px;text-align:center">
                <div style="font-size:1.5rem;font-weight:700;color:#22c55e">{len(suggestions)}</div>
                <div style="font-size:0.7rem;color:#64748b">SUGESTII</div>
            </div>
        </div>

        {auto_btn_html}

        <div id="grupy-lista">
        {groups_html}
        </div>

        <a href="/sprzedaze" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:20px;padding:15px">← Powrót do sprzedaży</a>
    </div>

    <!-- Modal szukania -->
    <div id="searchModal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.85);z-index:1000;padding:15px;overflow-y:auto">
        <div style="max-width:500px;margin:40px auto;background:#12121a;border:1px solid #1e1e2e;border-radius:16px;padding:20px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px">
                <h3 style="color:#3b82f6;margin:0;font-size:1.1rem">🔍 Szukaj produktu</h3>
                <button onclick="closeModal()" style="background:none;border:none;color:#64748b;font-size:1.5rem;cursor:pointer">&times;</button>
            </div>
            <div id="modalInfo" style="background:#0a0a0f;padding:10px;border-radius:8px;margin-bottom:12px;font-size:0.8rem;color:#94a3b8"></div>
            <input id="szukajInput" type="text" placeholder="Szukaj po nazwie, EAN, ASIN..."
                   style="width:100%;padding:12px;background:#0a0a0f;border:1px solid #1e1e2e;border-radius:10px;color:#fff;font-size:1rem;margin-bottom:12px;box-sizing:border-box"
                   oninput="debounceSearch(this.value)">
            <div id="wyniki" style="max-height:50vh;overflow-y:auto"></div>
        </div>
    </div>

    <script>
    let _saleIds = '';
    let _timer = null;

    function openSearch(nazwa, saleIds) {{
        _saleIds = saleIds;
        document.getElementById('searchModal').style.display = 'block';
        document.getElementById('modalInfo').textContent = nazwa.substring(0, 50) + ' (' + saleIds.split(',').length + ' szt.)';
        const inp = document.getElementById('szukajInput');
        inp.value = nazwa.substring(0, 30);
        inp.focus();
        debounceSearch(inp.value);
    }}

    function closeModal() {{
        document.getElementById('searchModal').style.display = 'none';
        _saleIds = '';
    }}

    function debounceSearch(q) {{
        clearTimeout(_timer);
        _timer = setTimeout(() => doSearch(q), 300);
    }}

    function doSearch(q) {{
        if (q.length < 2) {{ document.getElementById('wyniki').innerHTML = ''; return; }}
        document.getElementById('wyniki').innerHTML = '<div style="text-align:center;padding:20px;color:#64748b">Szukam...</div>';

        fetch('/api/sprzedaze/szukaj-produkt?q=' + encodeURIComponent(q))
            .then(r => r.json())
            .then(data => {{
                let h = '';
                if (data.results && data.results.length > 0) {{
                    data.results.forEach(p => {{
                        h += '<div style="display:flex;align-items:center;background:#0a0a0f;border:1px solid #1e1e2e;border-radius:10px;padding:10px;margin-bottom:8px;cursor:pointer" '
                           + 'onclick="dopasuj(\\'' + _saleIds + '\\', ' + p.id + ', this)">'
                           + '<img src="' + (p.zdjecie_url||'') + '" style="width:40px;height:40px;object-fit:contain;background:#fff;border-radius:8px;margin-right:10px" onerror="this.style.display=\\'none\\'">'
                           + '<div style="flex:1;min-width:0">'
                           + '<div style="font-size:0.85rem;font-weight:600;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + p.nazwa.substring(0,55) + '</div>'
                           + '<div style="font-size:0.7rem;color:#64748b">' + (p.ean||'') + ' ' + (p.asin||'') + ' | ' + p.paleta + '</div>'
                           + '</div>'
                           + '<div style="color:#22c55e;font-weight:700;margin-left:10px;font-size:0.85rem">' + (p.cena_allegro||0) + ' zl</div>'
                           + '</div>';
                    }});
                }} else {{
                    h = '<div style="text-align:center;padding:20px;color:#64748b">Brak wyników</div>';
                }}
                document.getElementById('wyniki').innerHTML = h;
            }});
    }}

    function dopasuj(saleIds, produktId, btn) {{
        const cnt = saleIds.split(',').length;
        if (!confirm('Dopasować ' + cnt + ' sprzedaży do tego produktu?')) return;

        btn.style.opacity = '0.5';
        btn.style.pointerEvents = 'none';

        const fd = new FormData();
        fd.append('sale_ids', saleIds);
        fd.append('produkt_id', produktId);

        fetch('/api/sprzedaze/dopasuj', {{method: 'POST', body: fd}})
            .then(r => r.json())
            .then(d => {{
                if (d.ok) {{
                    closeModal();
                    // Ukryj dopasowaną grupę
                    const items = document.querySelectorAll('.grupa-item');
                    items.forEach(el => {{
                        if (el.innerHTML.includes(saleIds.split(',')[0])) {{
                            el.style.opacity = '0.2';
                            el.style.pointerEvents = 'none';
                            el.innerHTML = '<div style="text-align:center;color:#22c55e;padding:10px">✓ Dopasowano ' + d.matched + ' szt. → ' + d.product_name.substring(0,40) + '</div>';
                        }}
                    }});
                }} else {{
                    alert('Błąd: ' + d.msg);
                    btn.style.opacity = '1';
                    btn.style.pointerEvents = 'auto';
                }}
            }})
            .catch(() => {{
                alert('Błąd połączenia');
                btn.style.opacity = '1';
                btn.style.pointerEvents = 'auto';
            }});
    }}

    function autoMatchAll() {{
        if (!confirm('Auto-dopasować wszystkie sugestie?\\nTo połączy sprzedaże z zasugerowanymi produktami.')) return;

        fetch('/api/sprzedaze/auto-dopasuj', {{method: 'POST'}})
            .then(r => r.json())
            .then(d => {{
                if (d.ok) {{
                    alert('Dopasowano ' + d.matched + ' grup (' + d.total_sales + ' sprzedaży)');
                    location.reload();
                }} else {{
                    alert('Błąd: ' + d.msg);
                }}
            }});
    }}

    // Zamknij modal kliknięciem w tło
    document.getElementById('searchModal').addEventListener('click', function(e) {{
        if (e.target === this) closeModal();
    }});
    </script>
    '''

    return page_html


@sprzedaze_bp.route('/sprzedaze/korekta-ilosci', methods=['POST'])
def sprzedaze_korekta_ilosci():
    """Ręczna korekta ilości produktu - jeśli ilość rośnie, cofa też sprzedaże"""
    from modules.database import get_db

    produkt_id = request.form.get('produkt_id', type=int)
    nowa_ilosc = request.form.get('nowa_ilosc', type=int)

    if produkt_id is None or nowa_ilosc is None:
        flash('Brak danych', 'error')
        return redirect(request.referrer or '/palety')

    conn = get_db()

    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        flash('Nie znaleziono produktu', 'error')
        return redirect(request.referrer or '/palety')

    stara_ilosc = produkt['ilosc'] or 0

    # Jeśli ilość rośnie (korekta w górę) → cofnij sprzedaże i odlicz przychód
    if nowa_ilosc > stara_ilosc:
        # Oznacz aktywne sprzedaże jako zwrot
        sprzedaze = conn.execute('''
            SELECT id, ilosc FROM sprzedaze
            WHERE produkt_id = ? AND COALESCE(status,'') NOT IN ('zwrot','anulowane','anulowana')
        ''', (produkt_id,)).fetchall()
        for s in sprzedaze:
            conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('zwrot', s['id']))

        # Wyczyść offline stats
        try:
            conn.execute('UPDATE produkty SET sprzedano_offline = 0, przychod_offline = 0 WHERE id = ?', (produkt_id,))
        except:
            pass

    # Określ nowy status
    if nowa_ilosc == 0:
        nowy_status = 'sprzedany'
    elif produkt['status'] == 'sprzedany':
        nowy_status = 'magazyn'
    else:
        nowy_status = produkt['status']

    # Zaktualizuj ilość i status
    conn.execute('UPDATE produkty SET ilosc = ?, status = ? WHERE id = ?',
                 (nowa_ilosc, nowy_status, produkt_id))

    conn.commit()

    flash(f'✅ Zaktualizowano ilość: {stara_ilosc} → {nowa_ilosc} szt.', 'success')
    return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')


@sprzedaze_bp.route('/produkt/oznacz-sprzedany/<int:produkt_id>', methods=['POST'])
def produkt_oznacz_sprzedany(produkt_id):
    """Oznacza produkt jako sprzedany BEZ dodawania do statystyk sprzedaży Allegro.
    Zmienia ilość produktu, zapisuje ile sprzedano offline i za ile.
    """
    from modules.database import get_db

    ilosc_sprzedana = request.form.get('ilosc', 1, type=int)
    _cena_raw = request.form.get('cena', '0').replace(',', '.')
    try:
        cena_sprzedazy = float(_cena_raw)
    except:
        cena_sprzedazy = 0.0
    przychod = ilosc_sprzedana * cena_sprzedazy

    print(f"📦 OFFLINE SALE: produkt={produkt_id}, ilosc={ilosc_sprzedana}, cena={cena_sprzedazy}, przychod={przychod}")
    print(f"   args: {dict(request.args)}")

    conn = get_db()

    # Dodaj kolumny OSOBNO jeśli nie istnieją
    try:
        conn.execute("SELECT sprzedano_offline FROM produkty LIMIT 1")
    except:
        try:
            conn.execute("ALTER TABLE produkty ADD COLUMN sprzedano_offline INTEGER DEFAULT 0")
            conn.commit()
            print("✅ Dodano kolumnę sprzedano_offline")
        except:
            pass

    try:
        conn.execute("SELECT przychod_offline FROM produkty LIMIT 1")
    except:
        try:
            conn.execute("ALTER TABLE produkty ADD COLUMN przychod_offline REAL DEFAULT 0")
            conn.commit()
            print("✅ Dodano kolumnę przychod_offline")
        except Exception as e:
            print(f"❌ Błąd dodawania przychod_offline: {e}")

    # Pobierz produkt
    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        flash('❌ Nie znaleziono produktu', 'error')
        return redirect(request.referrer or '/')

    stara_ilosc = produkt['ilosc'] or 1
    nowa_ilosc = max(0, stara_ilosc - ilosc_sprzedana)
    nowy_status = 'sprzedany' if nowa_ilosc == 0 else produkt['status']

    # Pobierz obecne wartości offline (mogą być NULL lub nie istnieć)
    try:
        obecne_szt_offline = produkt['sprzedano_offline'] or 0
    except:
        obecne_szt_offline = 0
    try:
        obecny_przychod_offline = produkt['przychod_offline'] or 0
    except:
        obecny_przychod_offline = 0

    nowe_szt_offline = obecne_szt_offline + ilosc_sprzedana
    nowy_przychod_offline = obecny_przychod_offline  # NIE aktualizuj - przychód trafia do sprzedaze

    print(f"📊 UPDATE: ilosc={nowa_ilosc}, status={nowy_status}, offline_szt={nowe_szt_offline}, offline_przychod={nowy_przychod_offline}")

    # Aktualizuj produkt - ilość, status, sprzedano_offline i przychod_offline
    try:
        conn.execute('''
            UPDATE produkty
            SET ilosc = ?, status = ?, sprzedano_offline = ?, przychod_offline = ?
            WHERE id = ?
        ''', (nowa_ilosc, nowy_status, nowe_szt_offline, nowy_przychod_offline, produkt_id))
        print("✅ UPDATE wykonany z offline")
    except Exception as e:
        print(f"❌ UPDATE failed, fallback: {e}")
        # Fallback - tylko ilość i status
        conn.execute('''
            UPDATE produkty
            SET ilosc = ?, status = ?
            WHERE id = ?
        ''', (nowa_ilosc, nowy_status, produkt_id))

    # KLUCZOWE: Dodaj rekord do sprzedaze żeby trafił do statystyk/dashboardu
    from datetime import datetime as _dt
    try:
        nazwa_prod = produkt['nazwa'] or f'Produkt #{produkt_id}'
        conn.execute('''
            INSERT INTO sprzedaze
                (produkt_id, nazwa, cena, ilosc, status, data_sprzedazy, kupujacy, notified)
            VALUES (?, ?, ?, ?, 'sprzedana', ?, 'offline', 1)
        ''', (produkt_id, nazwa_prod, cena_sprzedazy, ilosc_sprzedana,
              _dt.now().strftime('%Y-%m-%dT%H:%M:%S')))
        print(f"✅ Dodano do sprzedaze: {nazwa_prod} × {ilosc_sprzedana} szt. × {cena_sprzedazy:.0f} zł = {przychod:.0f} zł")
    except Exception as e:
        print(f"❌ INSERT sprzedaze failed: {e}")

    try:
        conn.commit()
    except Exception as e:
        print(f"❌ COMMIT failed: {e}")
        flash(f'❌ Błąd zapisu do bazy: {e}', 'error')
        return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')

    if przychod > 0:
        flash(f'✅ Sprzedano offline: {ilosc_sprzedana} szt. × {cena_sprzedazy:.0f} zł = {przychod:.0f} zł', 'success')
    else:
        flash(f'✅ Sprzedano {ilosc_sprzedana} szt. (zostało: {nowa_ilosc})', 'success')

    return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')


@sprzedaze_bp.route('/produkt/cofnij-offline/<int:produkt_id>', methods=['POST'])
def produkt_cofnij_offline(produkt_id):
    """Cofa sprzedaż offline - zwraca produkty do magazynu."""
    from modules.database import get_db

    ilosc_do_cofniecia = request.form.get('ilosc', 1, type=int)

    conn = get_db()

    # Pobierz produkt
    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        flash('❌ Nie znaleziono produktu', 'error')
        return redirect(request.referrer or '/')

    # Pobierz obecne wartości offline
    try:
        obecne_szt_offline = produkt['sprzedano_offline'] or 0
    except:
        obecne_szt_offline = 0
    try:
        obecny_przychod_offline = produkt['przychod_offline'] or 0
    except:
        obecny_przychod_offline = 0

    if ilosc_do_cofniecia > obecne_szt_offline:
        flash(f'❌ Nie można cofnąć {ilosc_do_cofniecia} szt. - sprzedano tylko {obecne_szt_offline} szt. offline', 'error')
        return redirect(request.referrer or '/')

    # Oblicz nowe wartości
    nowe_szt_offline = obecne_szt_offline - ilosc_do_cofniecia

    # Proporcjonalnie zmniejsz przychód
    if obecne_szt_offline > 0:
        przychod_za_szt = obecny_przychod_offline / obecne_szt_offline
        nowy_przychod_offline = nowe_szt_offline * przychod_za_szt
    else:
        nowy_przychod_offline = 0

    # Zwiększ ilość w magazynie
    stara_ilosc = produkt['ilosc'] or 0
    nowa_ilosc = stara_ilosc + ilosc_do_cofniecia

    # Zmień status jeśli produkt miał status 'sprzedany' i był sprzedany tylko offline
    nowy_status = produkt['status']
    if produkt['status'] == 'sprzedany' and nowe_szt_offline == 0:
        nowy_status = 'wystawiony'  # Wróć do wystawionego

    # Aktualizuj produkt
    try:
        conn.execute('''
            UPDATE produkty
            SET ilosc = ?, status = ?, sprzedano_offline = ?, przychod_offline = ?
            WHERE id = ?
        ''', (nowa_ilosc, nowy_status, nowe_szt_offline, nowy_przychod_offline, produkt_id))
    except:
        conn.execute('''
            UPDATE produkty
            SET ilosc = ?, status = ?
            WHERE id = ?
        ''', (nowa_ilosc, nowy_status, produkt_id))

    # FIX: Aktualizuj też rekordy w tabeli sprzedaze (kupujacy='offline')
    # Bez tego cofnięcie pojedyncze nie działało — rekord sprzedaży dalej liczony w statystykach
    pozostalo_do_cofniecia = ilosc_do_cofniecia
    sprzedaze_offline = conn.execute('''
        SELECT id, ilosc FROM sprzedaze
        WHERE produkt_id = ? AND kupujacy = 'offline'
        AND COALESCE(status,'') NOT IN ('zwrot','anulowane','anulowana')
        ORDER BY id DESC
    ''', (produkt_id,)).fetchall()

    for s in sprzedaze_offline:
        if pozostalo_do_cofniecia <= 0:
            break
        s_ilosc = s['ilosc'] or 0
        if pozostalo_do_cofniecia >= s_ilosc:
            # Cofamy cały rekord
            conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('zwrot', s['id']))
            pozostalo_do_cofniecia -= s_ilosc
        else:
            # Cofamy częściowo — zmniejsz ilość w rekordzie
            conn.execute('UPDATE sprzedaze SET ilosc = ? WHERE id = ?', (s_ilosc - pozostalo_do_cofniecia, s['id']))
            pozostalo_do_cofniecia = 0

    conn.commit()

    flash(f'🔄 Cofnięto {ilosc_do_cofniecia} szt. ze sprzedaży offline (pozostało offline: {nowe_szt_offline})', 'success')

    return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')


@sprzedaze_bp.route('/produkt/cofnij-sprzedaz/<int:produkt_id>', methods=['POST'])
def produkt_cofnij_sprzedaz(produkt_id):
    """Cofa sprzedaż produktu - przywraca ilość i oznacza sprzedaże jako zwrot"""
    from modules.database import get_db

    conn = get_db()

    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        flash('❌ Nie znaleziono produktu', 'error')
        return redirect(request.referrer or '/')

    # Znajdź aktywne sprzedaże dla tego produktu
    sprzedaze = conn.execute('''
        SELECT id, ilosc, cena FROM sprzedaze
        WHERE produkt_id = ? AND COALESCE(status,'') NOT IN ('zwrot','anulowane','anulowana')
    ''', (produkt_id,)).fetchall()

    if not sprzedaze:
        flash('ℹ️ Brak sprzedaży do cofnięcia dla tego produktu', 'info')
        return redirect(request.referrer or '/')

    # Oblicz sumę cofanych sztuk
    cofniete_szt = sum(s['ilosc'] for s in sprzedaze)

    # Oznacz sprzedaże jako zwrot
    for s in sprzedaze:
        conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('zwrot', s['id']))

    # Przywróć ilość produktu i zmień status na magazyn
    nowa_ilosc = (produkt['ilosc'] or 0) + cofniete_szt
    conn.execute('UPDATE produkty SET ilosc = ?, status = ? WHERE id = ?',
                 (nowa_ilosc, 'magazyn', produkt_id))

    # Wyczyść offline stats jeśli istnieją
    try:
        conn.execute('UPDATE produkty SET sprzedano_offline = 0, przychod_offline = 0 WHERE id = ?', (produkt_id,))
    except:
        pass

    conn.commit()

    flash(f'🔄 Cofnięto sprzedaż: {cofniete_szt} szt. wraca do magazynu', 'success')
    return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')
