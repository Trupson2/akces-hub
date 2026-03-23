"""
Modul sprzedazy — routes dla /sprzedaze/* i /produkt/* (sprzedaz offline)
"""
from flask import Blueprint, request, redirect, session, flash, jsonify, Response, current_app, render_template_string
from datetime import datetime

sprzedaze_bp = Blueprint('sprzedaze', __name__)


def _normalize_pl(text):
    """Zamienia polskie znaki na ASCII do porownan"""
    if not text:
        return ''
    pl_map = str.maketrans('ąćęłńóśźżĄĆĘŁŃÓŚŹŻ', 'acelnoszzACELNOSZZ')
    return text.translate(pl_map).lower()


# ============================================================
# SPRZEDAZE I ZWROTY
# ============================================================

SPRZEDAZE_LISTA_TEMPLATE = '''
{% extends "base.html" %}
{% block page_title %}Sprzedaze{% endblock %}
{% block content %}

<!-- Filtr miesiaca -->
<div style="margin-bottom:16px">
    <select onchange="window.location.href='/sprzedaze?miesiac='+this.value" class="form-control">
        {% for m in miesiace_options %}
        <option value="{{ m.value }}" {{ 'selected' if m.value == miesiac_filter else '' }}>{{ m.label }}</option>
        {% endfor %}
    </select>
</div>

<!-- Przyciski akcji -->
<div class="quick-actions" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px">
    <a href="/sync-custom?from={{ miesiac_filter }}-01" class="qa-btn">
        <span class="qa-icon" style="background:var(--yellow-soft)">🔄</span>
        Sync miesiac
    </a>
    <a href="/sprzedaze/sync-zwroty?miesiac={{ miesiac_filter }}" class="qa-btn">
        <span class="qa-icon" style="background:var(--red-soft)">🔄</span>
        Sync zwrotow
    </a>
    <a href="/sprzedaze/napraw-nazwy?miesiac={{ miesiac_filter }}" class="qa-btn">
        <span class="qa-icon" style="background:rgba(0,241,254,0.12)">🔧</span>
        Napraw dane
    </a>
    <a href="/sprzedaze/dopasuj" class="qa-btn">
        <span class="qa-icon" style="background:rgba(0,241,254,0.12)">🔗</span>
        Dopasuj
    </a>
</div>

<!-- Komunikaty -->
{% if msg == 'success' %}
<div class="alert alert-success" style="text-align:center">Oznaczono {{ msg_cnt }} zwrotow z Allegro</div>
{% elif msg == 'none' %}
<div class="alert alert-warning" style="text-align:center">Brak nowych zwrotow w Allegro</div>
{% elif msg == 'allegro_auth' %}
<div class="alert alert-error" style="text-align:center">Zaloguj sie do Allegro</div>
{% elif msg == 'error' %}
<div class="alert alert-error" style="text-align:center">Blad: {{ msg_detail }}</div>
{% elif msg == 'naprawiono' %}
<div class="alert alert-success" style="text-align:center">Naprawiono dane {{ msg_cnt }} produktow (nazwy + zdjecia)</div>
{% endif %}

<!-- KPI karty -->
<div class="kpi-grid" style="grid-template-columns:repeat(3,1fr)">
    <div class="kpi-card green">
        <div class="kpi-icon">💰</div>
        <div class="kpi-value">{{ przychod }} zl</div>
        <div class="kpi-label">Przychod</div>
    </div>
    <div class="kpi-card orange">
        <div class="kpi-icon">🔄</div>
        <div class="kpi-value">{{ zwroty_cnt }}</div>
        <div class="kpi-label">Zwrotow</div>
    </div>
    <div class="kpi-card" style="--card-color:var(--red)">
        <div class="kpi-icon" style="background:var(--red-soft)">📉</div>
        <div class="kpi-value" style="color:var(--red)">-{{ zwroty_suma }} zl</div>
        <div class="kpi-label">Wartosc zwrotow</div>
    </div>
</div>

<div class="section-title">{{ msc_nazwa|upper }} ({{ sprzedaze|length }} zamowien)</div>

{% if sprzedaze %}
{% for s in sprzedaze %}
<div class="list-item" style="{% if s.is_zwrot %}opacity:0.5{% endif %}">
    <div style="min-width:50px;text-align:center;margin-right:14px;padding-right:14px;border-right:1px solid var(--border)">
        <div style="font-size:1.3rem;font-weight:700;color:#00f1fe;font-family:'Space Grotesk',sans-serif">{{ s.dzien }}</div>
        <div style="font-size:0.65rem;color:var(--text-muted)">{{ s.miesiac_skrot }}</div>
    </div>
    <div class="list-item-info">
        <div class="list-item-title">{{ s.nazwa }}</div>
        <div class="list-item-meta">
            {{ s.kupujacy }}
            {% if s.kupujacy and s.kupujacy != 'Dane zanonimizowane' and s.kupujacy != 'offline' %}
            <button onclick="anonimizujKlienta('{{ s.kupujacy|e }}', this)" class="btn-anon" title="RODO: Anonimizuj dane klienta">&#128274; Anonimizuj</button>
            {% endif %}
            {% if s.is_zwrot %}
            <span class="badge badge-error">ZWROT</span>
            {% endif %}
        </div>
    </div>
    <div class="list-item-right">
        <div class="list-item-value" style="color:{% if s.is_zwrot %}var(--red){% else %}#5bf083{% endif %}">
            {{ '-' if s.is_zwrot else '' }}{{ s.cena_fmt }} zl
        </div>
        <div class="list-item-sub">x{{ s.ilosc }}</div>
    </div>
    <div style="margin-left:10px">
        {% if s.is_manual %}
        <form method="POST" action="/sprzedaze/usun/{{ s.id }}" style="display:inline;margin:0" onsubmit="return confirm('Usunac te sprzedaz i przywrocic ilosc?')">
            <input type="hidden" name="miesiac" value="{{ miesiac_filter }}">
            <button type="submit" class="btn btn-warning btn-sm">🗑️ Usun</button>
        </form>
        {% elif s.is_zwrot %}
        <form method="POST" action="/sprzedaze/unzwrot/{{ s.id }}" style="display:inline;margin:0">
            <input type="hidden" name="miesiac" value="{{ miesiac_filter }}">
            <button type="submit" class="btn btn-success btn-sm">Cofnij</button>
        </form>
        {% else %}
        <form method="POST" action="/sprzedaze/zwrot/{{ s.id }}" style="display:inline;margin:0">
            <input type="hidden" name="miesiac" value="{{ miesiac_filter }}">
            <button type="submit" class="btn btn-danger btn-sm">Zwrot</button>
        </form>
        {% endif %}
    </div>
</div>
{% endfor %}
{% else %}
<div class="card" style="text-align:center;color:var(--text-muted);padding:30px">Brak sprzedazy w tym miesiacu</div>
{% endif %}

<a href="/statystyki" class="back">&#8592; Statystyki</a>

<style>
.btn-anon{background:none;border:1px solid rgba(255,255,255,0.08);color:var(--text-muted);font-size:0.65rem;padding:2px 6px;border-radius:4px;cursor:pointer;margin-left:6px;transition:all 0.2s}
.btn-anon:hover{border-color:#ff4d6a;color:#ff4d6a;background:rgba(239,68,68,0.1)}
.kpi-value{font-family:'Space Grotesk',sans-serif}
.section-title{font-family:'Space Grotesk',sans-serif;text-shadow:0 0 20px rgba(0,241,254,0.3)}
.kpi-card{background:rgba(15,15,30,0.65);backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.08)}
.list-item{background:rgba(15,15,30,0.65);backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.08)}
.card{background:rgba(15,15,30,0.65);backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.08)}
.form-control:focus{border-color:#00f1fe;box-shadow:0 0 0 3px rgba(0,241,254,0.15)}
.btn-success{background:rgba(91,240,131,0.12);border:1px solid rgba(91,240,131,0.3);color:#5bf083}
.btn-success:hover{background:rgba(91,240,131,0.22);box-shadow:0 0 16px rgba(91,240,131,0.2)}
.btn-primary{background:rgba(0,241,254,0.12);border:1px solid rgba(0,241,254,0.3);color:#00f1fe}
.btn-primary:hover{background:rgba(0,241,254,0.22);box-shadow:0 0 16px rgba(0,241,254,0.2)}
.btn-danger{background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.3);color:#ff4d6a}
.btn-warning{background:rgba(251,191,36,0.12);border:1px solid rgba(251,191,36,0.3);color:#fbbf24}
.qa-btn{background:rgba(15,15,30,0.65);backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.08)}
.back{color:#00f1fe}
</style>
<script>
function anonimizujKlienta(buyerName, btn) {
    if (!confirm('RODO: Czy na pewno chcesz zanonimizowac dane klienta "' + buyerName + '"?\\n\\nTa operacja jest nieodwracalna. Dane osobowe zostana usuniete, ale kwoty i statystyki pozostana.')) return;
    btn.disabled = true;
    btn.textContent = '...';
    fetch('/magazyn/api/anonimizuj-klienta', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({buyer_name: buyerName})
    })
    .then(r => r.json())
    .then(d => {
        if (d.ok) {
            alert('Zanonimizowano ' + d.count + ' rekordow klienta.');
            location.reload();
        } else {
            alert('Blad: ' + (d.error || 'Nieznany blad'));
            btn.disabled = false;
            btn.innerHTML = '&#128274; Anonimizuj';
        }
    })
    .catch(e => {
        alert('Blad polaczenia: ' + e);
        btn.disabled = false;
        btn.innerHTML = '&#128274; Anonimizuj';
    });
}
</script>

{% endblock %}
'''


@sprzedaze_bp.route('/sprzedaze')
def sprzedaze_lista():
    """Lista sprzedazy z mozliwoscia oznaczenia zwrotow"""
    from modules.database import get_db

    # Filtr miesiaca z query string
    miesiac_filter = request.args.get('miesiac', '')

    # Komunikat z sync zwrotow
    msg = request.args.get('msg', '')
    msg_cnt = request.args.get('cnt', '0')
    msg_detail = request.args.get('detail', '')

    # Domyslnie biezacy miesiac
    if not miesiac_filter:
        miesiac_filter = datetime.now().strftime('%Y-%m')

    conn = get_db()

    # Pobierz sprzedaze z wybranego miesiaca
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

    # Statystyki dla wybranego miesiaca
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

    # Lista dostepnych miesiecy
    miesiace_db = conn.execute('''
        SELECT DISTINCT strftime('%Y-%m', data_sprzedazy) as miesiac
        FROM sprzedaze
        ORDER BY miesiac DESC
        LIMIT 12
    ''').fetchall()

    # Generuj opcje select
    miesiace_nazwy = {
        '01': 'Styczen', '02': 'Luty', '03': 'Marzec', '04': 'Kwiecien',
        '05': 'Maj', '06': 'Czerwiec', '07': 'Lipiec', '08': 'Sierpien',
        '09': 'Wrzesien', '10': 'Pazdziernik', '11': 'Listopad', '12': 'Grudzien'
    }

    miesiace_options = []
    for m in miesiace_db:
        msc = m['miesiac']
        rok = msc[:4]
        msc_num = msc[5:7]
        nazwa = f"{miesiace_nazwy.get(msc_num, msc_num)} {rok}"
        miesiace_options.append({'value': msc, 'label': nazwa})

    # Przygotuj dane sprzedazy dla szablonu
    items = []
    for s in sprzedaze:
        is_zwrot = s['status'] == 'zwrot'
        is_manual = (s['allegro_order_id'] or '').startswith('MANUAL-')

        # Nazwa produktu - kilka zrodel
        try:
            nazwa = s['produkt_nazwa'] or s['nazwa'] or ''
        except (IndexError, KeyError):
            try:
                nazwa = s['produkt_nazwa'] or ''
            except (IndexError, KeyError):
                nazwa = ''
        if not nazwa or nazwa == 'Produkt':
            nazwa = f"Zamowienie od {s['kupujacy']}"

        # Formatuj date ladnie
        data_raw = s['data_sprzedazy'] or ''
        if 'T' in data_raw:
            data_str = data_raw[:10]
        else:
            data_str = data_raw[:10]

        # Dzien i miesiac
        try:
            parts = data_str.split('-')
            dzien = parts[2] if len(parts) >= 3 else '??'
            miesiac_num = int(parts[1]) if len(parts) >= 2 else 0
            miesiace_skr = ['', 'STY', 'LUT', 'MAR', 'KWI', 'MAJ', 'CZE', 'LIP', 'SIE', 'WRZ', 'PAZ', 'LIS', 'GRU']
            miesiac_skrot = miesiace_skr[miesiac_num] if 0 < miesiac_num <= 12 else '???'
        except:
            dzien = '??'
            miesiac_skrot = '???'

        items.append({
            'id': s['id'],
            'nazwa': nazwa[:40],
            'kupujacy': s['kupujacy'],
            'is_zwrot': is_zwrot,
            'is_manual': is_manual,
            'cena_fmt': f"{s['cena']:.0f}",
            'ilosc': s['ilosc'],
            'dzien': dzien,
            'miesiac_skrot': miesiac_skrot,
        })

    # Nazwa wybranego miesiaca do wyswietlenia
    msc_num = miesiac_filter[5:7] if len(miesiac_filter) >= 7 else '01'
    msc_rok = miesiac_filter[:4] if len(miesiac_filter) >= 4 else '2026'
    msc_nazwa = f"{miesiace_nazwy.get(msc_num, msc_num)} {msc_rok}"

    return render_template_string(
        SPRZEDAZE_LISTA_TEMPLATE,
        miesiac_filter=miesiac_filter,
        miesiace_options=miesiace_options,
        sprzedaze=items,
        przychod=f"{stats['przychod'] or 0:.0f}",
        zwroty_cnt=stats['zwroty_cnt'] or 0,
        zwroty_suma=f"{stats['zwroty_suma'] or 0:.0f}",
        msc_nazwa=msc_nazwa,
        msg=msg,
        msg_cnt=msg_cnt,
        msg_detail=msg_detail,
        version=current_app.config.get('VERSION', ''),
        brand_name=current_app.config.get('BRAND_NAME', 'Akces Hub'),
        current_user=session.get('user'),
    )


@sprzedaze_bp.route('/sprzedaze/zwrot/<int:sale_id>', methods=['POST'])
def oznacz_zwrot(sale_id):
    """Oznacza sprzedaz jako zwrot"""
    from modules.database import get_db
    conn = get_db()
    conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('zwrot', sale_id))
    conn.commit()
    # Zachowaj filtr miesiaca
    miesiac = request.form.get('miesiac', '')
    return redirect(f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze')


@sprzedaze_bp.route('/sprzedaze/unzwrot/<int:sale_id>', methods=['POST'])
def cofnij_zwrot(sale_id):
    """Cofa oznaczenie zwrotu"""
    from modules.database import get_db
    conn = get_db()
    conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('wyslana', sale_id))
    conn.commit()
    # Zachowaj filtr miesiaca
    miesiac = request.form.get('miesiac', '')
    return redirect(f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze')


@sprzedaze_bp.route('/sprzedaze/usun/<int:sale_id>', methods=['POST'])
def usun_sprzedaz(sale_id):
    """Usuwa sprzedaz (reczna korekta) i przywraca ilosc produktu"""
    from modules.database import get_db
    miesiac = request.form.get('miesiac', '')

    conn = get_db()

    # Pobierz dane sprzedazy
    sprzedaz = conn.execute('SELECT * FROM sprzedaze WHERE id = ?', (sale_id,)).fetchone()

    if not sprzedaz:
        flash('Nie znaleziono sprzedazy', 'error')
        return redirect(f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze')

    # Sprawdz czy to reczna korekta
    if not (sprzedaz['allegro_order_id'] or '').startswith('MANUAL-'):
        flash('Mozna usuwac tylko reczne korekty', 'error')
        return redirect(f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze')

    # Przywroc ilosc produktu
    if sprzedaz['produkt_id']:
        conn.execute('''
            UPDATE produkty
            SET ilosc = ilosc + ?,
                status = CASE WHEN status = 'sprzedany' THEN 'wystawiony' ELSE status END
            WHERE id = ?
        ''', (sprzedaz['ilosc'], sprzedaz['produkt_id']))

    # Usun wpis sprzedazy
    conn.execute('DELETE FROM sprzedaze WHERE id = ?', (sale_id,))

    conn.commit()

    flash(f'Usunieto sprzedaz i przywrocono {sprzedaz["ilosc"]} szt. do magazynu', 'success')
    return redirect(f'/sprzedaze?miesiac={miesiac}' if miesiac else '/sprzedaze')


@sprzedaze_bp.route('/sprzedaze/sync-zwroty')
def sync_zwroty_allegro():
    """Synchronizuje zwroty z Allegro API dla wybranego miesiaca"""
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
        print(f"Blad sync_returns: {e}")
        return redirect(f'{base_url}&msg=error&detail={str(e)[:50]}')


@sprzedaze_bp.route('/sprzedaze/napraw-nazwy')
def napraw_nazwy_sprzedazy():
    """Uzupelnia brakujace nazwy, zdjecia i daty w sprzedazach z Allegro API"""
    from modules.allegro_api import is_authenticated, allegro_request
    from modules.database import get_db

    miesiac = request.args.get('miesiac', '')
    if not miesiac:
        miesiac = datetime.now().strftime('%Y-%m')

    if not is_authenticated():
        return redirect(f'/sprzedaze?miesiac={miesiac}&msg=allegro_auth')

    conn = get_db()

    # Upewnij sie ze kolumny istnieja
    try:
        conn.execute('ALTER TABLE sprzedaze ADD COLUMN nazwa TEXT DEFAULT ""')
    except:
        pass
    try:
        conn.execute('ALTER TABLE sprzedaze ADD COLUMN zdjecie_url TEXT DEFAULT ""')
    except:
        pass

    # Pobierz sprzedaze z wybranego miesiaca bez nazwy/zdjecia
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

    # Helper do bezpiecznego dostepu do sqlite3.Row
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

        # Metoda 2: pobierz z Allegro API (nazwa + zdjecie + popraw date)
        new_date = None
        if s['allegro_order_id']:
            try:
                order_data, err = allegro_request('GET', f"/order/checkout-forms/{s['allegro_order_id']}")
                if order_data:
                    # Popraw date z boughtAt
                    bought_at = order_data.get('boughtAt', '')
                    if bought_at:
                        try:
                            from datetime import datetime as _dt
                            dt_str = bought_at.replace('Z', '+00:00')
                            dt = _dt.fromisoformat(dt_str)
                            dt_local = dt.astimezone().replace(tzinfo=None)
                            correct_date = dt_local.strftime('%Y-%m-%d %H:%M:%S')
                            current_date = safe_get(s, 'data_sprzedazy', '')
                            # Napraw jesli data jest inna (inny dzien)
                            if correct_date[:10] != (current_date or '')[:10]:
                                new_date = correct_date
                                print(f"Poprawiam date: {s['id']} {current_date[:10]} -> {correct_date[:10]}")
                        except Exception as de:
                            print(f"Date parse error: {de}")

                    if 'lineItems' in order_data:
                        for item in order_data['lineItems']:
                            offer = item.get('offer', {})
                            if not new_name:
                                name = offer.get('name', '')
                                if name:
                                    new_name = name[:100]
                            # Pobierz zdjecie z oferty
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

        # Aktualizuj jesli znaleziono cos nowego (nazwa, zdjecie lub data)
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
                print(f"Naprawiono: {s['id']} -> {(new_name or '')[:40]}... | img: {'ok' if new_image else 'brak'}{date_info}")
            except Exception as e:
                print(f"Blad update: {e}")

    conn.commit()

    return redirect(f'/sprzedaze?miesiac={miesiac}&msg=naprawiono&cnt={updated}')


@sprzedaze_bp.route('/sprzedaze/dodaj-reczna', methods=['POST'])
def sprzedaze_dodaj_reczna():
    """Reczne dodanie sprzedazy (korekta)"""
    from modules.database import get_db
    from datetime import datetime

    produkt_id = request.form.get('produkt_id', type=int)
    ilosc = request.form.get('ilosc', 1, type=int)
    cena = request.form.get('cena', 0, type=float)
    kupujacy = request.form.get('kupujacy', 'Reczna korekta')

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

    # Zaktualizuj ilosc w produkcie
    new_qty = max(0, produkt['ilosc'] - ilosc)
    conn.execute('''UPDATE produkty SET
        ilosc = ?,
        status = CASE WHEN ? = 0 THEN 'sprzedany' ELSE status END
        WHERE id = ?''', (new_qty, new_qty, produkt_id))

    conn.commit()

    flash(f'Dodano sprzedaz: {ilosc} szt. za {cena:.0f} zl', 'success')
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

    # Normalizuj slowa do porownan (bez polskich znakow)
    words = [_normalize_pl(w) for w in q.split() if len(w) >= 2][:6]
    if not words:
        return jsonify({'results': []})

    # Pobierz wszystkie produkty (188 rekordow — szybko)
    all_products = conn.execute('''
        SELECT p.id, p.nazwa, p.ean, p.asin, p.ilosc, p.cena_allegro, p.zdjecie_url,
               COALESCE(pal.nazwa, '') as paleta_nazwa
        FROM produkty p
        LEFT JOIN palety pal ON p.paleta_id = pal.id
        ORDER BY p.id DESC
    ''').fetchall()

    # Filtruj w Pythonie (normalizacja polskich znakow)
    scored = []
    q_norm = _normalize_pl(q)
    min_match = max(2, int(len(words) * 0.6))  # min 60% slow lub 2

    for p in all_products:
        nazwa_norm = _normalize_pl(p['nazwa'] or '')
        ean = (p['ean'] or '').lower()
        asin = (p['asin'] or '').lower()

        # EAN/ASIN exact match — priorytet
        if q_norm in ean or q_norm in asin:
            scored.append((100, p))
            continue

        # Multi-word: liczymy ile slow matchuje
        hits = sum(1 for w in words if w in nazwa_norm)
        if hits >= min_match:
            scored.append((hits, p))

    # Sortuj: najlepsze dopasowanie na gorze
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
    """API - dopasowuje grupe sprzedazy do produktu + historia"""
    from modules.database import get_db, add_historia

    sale_ids_str = request.form.get('sale_ids', '')
    produkt_id = request.form.get('produkt_id', type=int)

    if not sale_ids_str or not produkt_id:
        return jsonify({'ok': False, 'msg': 'Brak danych'}), 400

    try:
        sale_ids = [int(x.strip()) for x in sale_ids_str.split(',') if x.strip()]
    except ValueError:
        return jsonify({'ok': False, 'msg': 'Nieprawidlowe ID'}), 400

    if not sale_ids:
        return jsonify({'ok': False, 'msg': 'Brak ID sprzedazy'}), 400

    conn = get_db()

    produkt = conn.execute('SELECT id, nazwa, ilosc FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        return jsonify({'ok': False, 'msg': 'Produkt nie znaleziony'}), 404

    # Pobierz szczegoly sprzedazy PRZED update (do historii)
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

    # Dodaj historie dla kazdej dopasowanej sprzedazy
    for s in sprzedaze:
        przychod = (s['cena'] or 0) * (s['ilosc'] or 1)
        try:
            add_historia(produkt_id, 'sprzedano',
                f'Dopasowano sprzedaz #{s["id"]}: {s["ilosc"] or 1} szt. za {przychod:.0f} zl ({s["data_sprzedazy"][:10] if s["data_sprzedazy"] else "?"})',
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

            # Pobierz szczegoly sprzedazy PRZED update (do historii)
            sprzedaze = conn.execute(
                'SELECT id, nazwa, cena, ilosc, data_sprzedazy'
                ' FROM sprzedaze WHERE id IN (' + ph + ') AND produkt_id IS NULL',
                sale_ids).fetchall()

            conn.execute(
                'UPDATE sprzedaze SET produkt_id = ?'
                ' WHERE id IN (' + ph + ') AND produkt_id IS NULL',
                [match['id']] + sale_ids)

            # Dodaj historie dla kazdej dopasowanej sprzedazy
            for s in sprzedaze:
                przychod = (s['cena'] or 0) * (s['ilosc'] or 1)
                try:
                    add_historia(match['id'], 'sprzedano',
                        f'Auto-dopasowano sprzedaz #{s["id"]}: {s["ilosc"] or 1} szt. za {przychod:.0f} zl ({s["data_sprzedazy"][:10] if s["data_sprzedazy"] else "?"})',
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
    """Naprawa danych: usuniecie duplikatow + aktualizacja stanow magazynowych"""
    from modules.database import get_db
    conn = get_db()
    repairs = []

    # === 1. Usun duplikaty zamowien ===
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
        repairs.append(f'Usunieto {removed} duplikatow zamowien')

    # === 2. Przelicz stany magazynowe na podstawie sprzedazy ===
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

        # Pobierz oryginalna ilosc z palety (zakupowa)
        prod = conn.execute('''
            SELECT p.id, p.ilosc, p.nazwa, p.status,
                   COALESCE(p.ilosc + (SELECT COALESCE(SUM(sp.ilosc), 0)
                       FROM sprzedaze sp WHERE sp.produkt_id = p.id
                       AND sp.status NOT IN ('anulowana', 'zwrot')), p.ilosc) as original_qty
            FROM produkty p WHERE p.id = ?
        ''', (pid,)).fetchone()

        if not prod:
            continue

        # Oblicz prawidlowa ilosc
        correct_qty = max(0, prod['ilosc'])  # Obecna ilosc

        # Jesli status nie jest 'sprzedany' ale ilosc powinna byc 0
        if sold_qty > 0 and prod['ilosc'] > 0:
            # Sprawdz czy stock zostal odjety - porownaj oczekiwane
            pass  # Stock jest juz odjety przez sync

        # Jesli ilosc=0 ale status nie jest 'sprzedany'
        if prod['ilosc'] <= 0 and prod['status'] not in ('sprzedany', 'wysłane'):
            conn.execute("UPDATE produkty SET status = 'sprzedany' WHERE id = ?", (pid,))
            stock_fixed += 1

        # Jesli ilosc > 0 ale status jest 'sprzedany' (blednie oznaczony)
        if prod['ilosc'] > 0 and prod['status'] == 'sprzedany':
            conn.execute("UPDATE produkty SET status = 'wystawiony' WHERE id = ?", (pid,))
            stock_fixed += 1

    if stock_fixed:
        repairs.append(f'Naprawiono status {stock_fixed} produktow')

    # === 3. Linkowanie przez tabele oferty (allegro_id -> produkt_id) ===
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
        repairs.append(f'Polaczono {linked} sprzedazy przez oferty')

    conn.commit()

    return jsonify({
        'ok': True,
        'repairs': repairs,
        'removed_duplicates': removed,
        'stock_fixed': stock_fixed,
        'linked': linked
    })


DOPASUJ_TEMPLATE = '''
{% extends "base.html" %}
{% block page_title %}Dopasuj sprzedaze{% endblock %}
{% block content %}

<!-- KPI karty -->
<div class="kpi-grid" style="grid-template-columns:repeat(3,1fr)">
    <div class="kpi-card" style="--card-color:var(--red)">
        <div class="kpi-icon" style="background:var(--red-soft)">🔴</div>
        <div class="kpi-value" style="color:var(--red)">{{ total_unmatched }}</div>
        <div class="kpi-label">Niedopasowanych</div>
    </div>
    <div class="kpi-card orange">
        <div class="kpi-icon">📁</div>
        <div class="kpi-value">{{ grupy_count }}</div>
        <div class="kpi-label">Grup</div>
    </div>
    <div class="kpi-card green">
        <div class="kpi-icon">💡</div>
        <div class="kpi-value">{{ suggestions_count }}</div>
        <div class="kpi-label">Sugestii</div>
    </div>
</div>

{% if suggestions_count > 0 %}
<button onclick="autoMatchAll()" class="btn btn-success" style="margin-bottom:20px">
    ⚡ Auto-dopasuj {{ suggestions_count }} sugestii
</button>
{% endif %}

<div id="grupy-lista">
{% for g in grupy %}
<div class="grupa-item card" style="padding:14px">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
        <div style="flex:1;min-width:0">
            <div class="list-item-title">{{ g.nazwa_display }}</div>
            <div class="list-item-meta">{{ g.cnt }} szt. | {{ g.wartosc_fmt }} zl</div>
        </div>
        <button onclick="openSearch('{{ g.nazwa_js }}', '{{ g.sale_ids }}')" class="btn btn-primary btn-sm">
            🔍 Szukaj
        </button>
    </div>
    {% if g.suggestion %}
    <div style="background:rgba(91,240,131,0.12);border:1px solid rgba(91,240,131,0.3);border-radius:8px;padding:8px;margin-top:8px;display:flex;align-items:center;gap:8px">
        <img src="{{ g.suggestion.zdjecie_url }}" style="width:32px;height:32px;object-fit:contain;background:#fff;border-radius:6px" onerror="this.style.display='none'">
        <div style="flex:1;min-width:0">
            <div style="font-size:0.75rem;color:#5bf083">Sugestia:</div>
            <div style="font-size:0.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ g.suggestion.nazwa }}</div>
        </div>
        <button onclick="dopasuj('{{ g.sale_ids }}', {{ g.suggestion.id }}, this)" class="btn btn-success btn-sm">
            &#10003; Dopasuj
        </button>
    </div>
    {% endif %}
</div>
{% endfor %}
</div>

<a href="/sprzedaze" class="back">&#8592; Powrot do sprzedazy</a>

<!-- Modal szukania -->
<div id="searchModal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.85);z-index:1000;padding:15px;overflow-y:auto">
    <div class="card" style="max-width:500px;margin:40px auto;padding:20px;background:rgba(15,15,30,0.85);backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.08)">
        <div class="card-header">
            <div class="card-title" style="color:#00f1fe;font-family:'Space Grotesk',sans-serif;text-shadow:0 0 20px rgba(0,241,254,0.3)">🔍 Szukaj produktu</div>
            <button onclick="closeModal()" style="background:none;border:none;color:var(--text-muted);font-size:1.5rem;cursor:pointer">&times;</button>
        </div>
        <div id="modalInfo" style="background:var(--bg);padding:10px;border-radius:8px;margin-bottom:12px;font-size:0.8rem;color:var(--text-secondary)"></div>
        <input id="szukajInput" type="text" placeholder="Szukaj po nazwie, EAN, ASIN..."
               class="form-control" style="margin-bottom:12px"
               oninput="debounceSearch(this.value)">
        <div id="wyniki" style="max-height:50vh;overflow-y:auto"></div>
    </div>
</div>

<script>
let _saleIds = '';
let _timer = null;

function openSearch(nazwa, saleIds) {
    _saleIds = saleIds;
    document.getElementById('searchModal').style.display = 'block';
    document.getElementById('modalInfo').textContent = nazwa.substring(0, 50) + ' (' + saleIds.split(',').length + ' szt.)';
    const inp = document.getElementById('szukajInput');
    inp.value = nazwa.substring(0, 30);
    inp.focus();
    debounceSearch(inp.value);
}

function closeModal() {
    document.getElementById('searchModal').style.display = 'none';
    _saleIds = '';
}

function debounceSearch(q) {
    clearTimeout(_timer);
    _timer = setTimeout(() => doSearch(q), 300);
}

function doSearch(q) {
    if (q.length < 2) { document.getElementById('wyniki').innerHTML = ''; return; }
    document.getElementById('wyniki').innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted)">Szukam...</div>';

    fetch('/api/sprzedaze/szukaj-produkt?q=' + encodeURIComponent(q))
        .then(r => r.json())
        .then(data => {
            let h = '';
            if (data.results && data.results.length > 0) {
                data.results.forEach(p => {
                    h += '<div class="list-item" style="cursor:pointer;margin-bottom:8px" '
                       + 'onclick="dopasuj(\'' + _saleIds + '\', ' + p.id + ', this)">'
                       + '<img src="' + (p.zdjecie_url||'') + '" style="width:40px;height:40px;object-fit:contain;background:#fff;border-radius:8px;margin-right:10px" onerror="this.style.display=\'none\'">'
                       + '<div class="list-item-info">'
                       + '<div class="list-item-title">' + p.nazwa.substring(0,55) + '</div>'
                       + '<div class="list-item-meta">' + (p.ean||'') + ' ' + (p.asin||'') + ' | ' + p.paleta + '</div>'
                       + '</div>'
                       + '<div class="list-item-right"><div class="list-item-value">' + (p.cena_allegro||0) + ' zl</div></div>'
                       + '</div>';
                });
            } else {
                h = '<div style="text-align:center;padding:20px;color:var(--text-muted)">Brak wynikow</div>';
            }
            document.getElementById('wyniki').innerHTML = h;
        });
}

function dopasuj(saleIds, produktId, btn) {
    const cnt = saleIds.split(',').length;
    if (!confirm('Dopasowac ' + cnt + ' sprzedazy do tego produktu?')) return;

    btn.style.opacity = '0.5';
    btn.style.pointerEvents = 'none';

    const fd = new FormData();
    fd.append('sale_ids', saleIds);
    fd.append('produkt_id', produktId);

    fetch('/api/sprzedaze/dopasuj', {method: 'POST', body: fd})
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                closeModal();
                // Ukryj dopasowana grupe
                const items = document.querySelectorAll('.grupa-item');
                items.forEach(el => {
                    if (el.innerHTML.includes(saleIds.split(',')[0])) {
                        el.style.opacity = '0.2';
                        el.style.pointerEvents = 'none';
                        el.innerHTML = '<div style="text-align:center;color:#5bf083;padding:10px">&#10003; Dopasowano ' + d.matched + ' szt. &rarr; ' + d.product_name.substring(0,40) + '</div>';
                    }
                });
            } else {
                alert('Blad: ' + d.msg);
                btn.style.opacity = '1';
                btn.style.pointerEvents = 'auto';
            }
        })
        .catch(() => {
            alert('Blad polaczenia');
            btn.style.opacity = '1';
            btn.style.pointerEvents = 'auto';
        });
}

function autoMatchAll() {
    if (!confirm('Auto-dopasowac wszystkie sugestie?\nTo polaczy sprzedaze z zasugerowanymi produktami.')) return;

    fetch('/api/sprzedaze/auto-dopasuj', {method: 'POST'})
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                alert('Dopasowano ' + d.matched + ' grup (' + d.total_sales + ' sprzedazy)');
                location.reload();
            } else {
                alert('Blad: ' + d.msg);
            }
        });
}

// Zamknij modal kliknieciem w tlo
document.getElementById('searchModal').addEventListener('click', function(e) {
    if (e.target === this) closeModal();
});
</script>

<style>
.kpi-value{font-family:'Space Grotesk',sans-serif}
.kpi-card{background:rgba(15,15,30,0.65);backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.08)}
.card{background:rgba(15,15,30,0.65);backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.08)}
.list-item{background:rgba(15,15,30,0.65);backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.08)}
.section-title,.card-title{font-family:'Space Grotesk',sans-serif}
.btn-success{background:rgba(91,240,131,0.12);border:1px solid rgba(91,240,131,0.3);color:#5bf083}
.btn-success:hover{background:rgba(91,240,131,0.22);box-shadow:0 0 16px rgba(91,240,131,0.2)}
.btn-primary{background:rgba(0,241,254,0.12);border:1px solid rgba(0,241,254,0.3);color:#00f1fe}
.btn-primary:hover{background:rgba(0,241,254,0.22);box-shadow:0 0 16px rgba(0,241,254,0.2)}
.form-control:focus{border-color:#00f1fe;box-shadow:0 0 0 3px rgba(0,241,254,0.15)}
</style>

{% endblock %}
'''


@sprzedaze_bp.route('/sprzedaze/dopasuj')
def sprzedaze_dopasuj():
    """Strona dopasowywania sprzedazy do produktow"""
    from modules.database import get_db
    import html as html_mod

    conn = get_db()

    # Grupuj niedopasowane sprzedaze po nazwie
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
        # Multi-word matching: wez 3-4 kluczowe slowa i szukaj AND
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

    # Przygotuj dane grup dla szablonu
    grupy_data = []
    for g in grupy:
        nazwa = html_mod.escape(g['grupa_nazwa'])
        nazwa_js = html_mod.escape(g['grupa_nazwa']).replace("'", "\\'").replace('"', '&quot;')
        sale_ids = g['sale_ids']
        cnt = g['cnt']
        wartosc = g['wartosc'] or 0

        sug = suggestions.get(g['grupa_nazwa'])
        sug_data = None
        if sug:
            sug_data = {
                'id': sug['id'],
                'nazwa': html_mod.escape(sug['nazwa'][:55]),
                'zdjecie_url': html_mod.escape(sug.get('zdjecie_url') or ''),
            }

        grupy_data.append({
            'nazwa_display': nazwa[:60],
            'nazwa_js': nazwa_js,
            'sale_ids': sale_ids,
            'cnt': cnt,
            'wartosc_fmt': f"{wartosc:.0f}",
            'suggestion': sug_data,
        })

    return render_template_string(
        DOPASUJ_TEMPLATE,
        total_unmatched=total_unmatched,
        grupy_count=len(grupy),
        suggestions_count=len(suggestions),
        grupy=grupy_data,
        version=current_app.config.get('VERSION', ''),
        brand_name=current_app.config.get('BRAND_NAME', 'Akces Hub'),
        current_user=session.get('user'),
    )


@sprzedaze_bp.route('/sprzedaze/korekta-ilosci', methods=['POST'])
def sprzedaze_korekta_ilosci():
    """Reczna korekta ilosci produktu - jesli ilosc rosnie, cofa tez sprzedaze"""
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

    # Jesli ilosc rosnie (korekta w gore) -> cofnij sprzedaze i odlicz przychod
    if nowa_ilosc > stara_ilosc:
        # Oznacz aktywne sprzedaze jako zwrot
        sprzedaze = conn.execute('''
            SELECT id, ilosc FROM sprzedaze
            WHERE produkt_id = ? AND COALESCE(status,'') NOT IN ('zwrot','anulowane','anulowana')
        ''', (produkt_id,)).fetchall()
        for s in sprzedaze:
            conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('zwrot', s['id']))

        # Wyczysc offline stats
        try:
            conn.execute('UPDATE produkty SET sprzedano_offline = 0, przychod_offline = 0 WHERE id = ?', (produkt_id,))
        except:
            pass

    # Okresl nowy status
    if nowa_ilosc == 0:
        nowy_status = 'sprzedany'
    elif produkt['status'] == 'sprzedany':
        nowy_status = 'magazyn'
    else:
        nowy_status = produkt['status']

    # Zaktualizuj ilosc i status
    conn.execute('UPDATE produkty SET ilosc = ?, status = ? WHERE id = ?',
                 (nowa_ilosc, nowy_status, produkt_id))

    conn.commit()

    flash(f'Zaktualizowano ilosc: {stara_ilosc} -> {nowa_ilosc} szt.', 'success')
    return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')


@sprzedaze_bp.route('/produkt/oznacz-sprzedany/<int:produkt_id>', methods=['POST'])
def produkt_oznacz_sprzedany(produkt_id):
    """Oznacza produkt jako sprzedany BEZ dodawania do statystyk sprzedazy Allegro.
    Zmienia ilosc produktu, zapisuje ile sprzedano offline i za ile.
    """
    from modules.database import get_db

    ilosc_sprzedana = request.form.get('ilosc', 1, type=int)
    _cena_raw = request.form.get('cena', '0').replace(',', '.')
    try:
        cena_sprzedazy = float(_cena_raw)
    except:
        cena_sprzedazy = 0.0
    przychod = ilosc_sprzedana * cena_sprzedazy

    print(f"OFFLINE SALE: produkt={produkt_id}, ilosc={ilosc_sprzedana}, cena={cena_sprzedazy}, przychod={przychod}")
    print(f"   args: {dict(request.args)}")

    conn = get_db()

    # Dodaj kolumny OSOBNO jesli nie istnieja
    try:
        conn.execute("SELECT sprzedano_offline FROM produkty LIMIT 1")
    except:
        try:
            conn.execute("ALTER TABLE produkty ADD COLUMN sprzedano_offline INTEGER DEFAULT 0")
            conn.commit()
            print("Dodano kolumne sprzedano_offline")
        except:
            pass

    try:
        conn.execute("SELECT przychod_offline FROM produkty LIMIT 1")
    except:
        try:
            conn.execute("ALTER TABLE produkty ADD COLUMN przychod_offline REAL DEFAULT 0")
            conn.commit()
            print("Dodano kolumne przychod_offline")
        except Exception as e:
            print(f"Blad dodawania przychod_offline: {e}")

    # Pobierz produkt
    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        flash('Nie znaleziono produktu', 'error')
        return redirect(request.referrer or '/')

    stara_ilosc = produkt['ilosc'] or 1
    nowa_ilosc = max(0, stara_ilosc - ilosc_sprzedana)
    nowy_status = 'sprzedany' if nowa_ilosc == 0 else produkt['status']

    # Pobierz obecne wartosci offline (moga byc NULL lub nie istniec)
    try:
        obecne_szt_offline = produkt['sprzedano_offline'] or 0
    except:
        obecne_szt_offline = 0
    try:
        obecny_przychod_offline = produkt['przychod_offline'] or 0
    except:
        obecny_przychod_offline = 0

    nowe_szt_offline = obecne_szt_offline + ilosc_sprzedana
    nowy_przychod_offline = obecny_przychod_offline  # NIE aktualizuj - przychod trafia do sprzedaze

    print(f"UPDATE: ilosc={nowa_ilosc}, status={nowy_status}, offline_szt={nowe_szt_offline}, offline_przychod={nowy_przychod_offline}")

    # Aktualizuj produkt - ilosc, status, sprzedano_offline i przychod_offline
    try:
        conn.execute('''
            UPDATE produkty
            SET ilosc = ?, status = ?, sprzedano_offline = ?, przychod_offline = ?
            WHERE id = ?
        ''', (nowa_ilosc, nowy_status, nowe_szt_offline, nowy_przychod_offline, produkt_id))
        print("UPDATE wykonany z offline")
    except Exception as e:
        print(f"UPDATE failed, fallback: {e}")
        # Fallback - tylko ilosc i status
        conn.execute('''
            UPDATE produkty
            SET ilosc = ?, status = ?
            WHERE id = ?
        ''', (nowa_ilosc, nowy_status, produkt_id))

    # KLUCZOWE: Dodaj rekord do sprzedaze zeby trafil do statystyk/dashboardu
    from datetime import datetime as _dt
    try:
        nazwa_prod = produkt['nazwa'] or f'Produkt #{produkt_id}'
        conn.execute('''
            INSERT INTO sprzedaze
                (produkt_id, nazwa, cena, ilosc, status, data_sprzedazy, kupujacy, notified)
            VALUES (?, ?, ?, ?, 'sprzedana', ?, 'offline', 1)
        ''', (produkt_id, nazwa_prod, cena_sprzedazy, ilosc_sprzedana,
              _dt.now().strftime('%Y-%m-%dT%H:%M:%S')))
        print(f"Dodano do sprzedaze: {nazwa_prod} x {ilosc_sprzedana} szt. x {cena_sprzedazy:.0f} zl = {przychod:.0f} zl")
    except Exception as e:
        print(f"INSERT sprzedaze failed: {e}")

    try:
        conn.commit()
    except Exception as e:
        print(f"COMMIT failed: {e}")
        flash(f'Blad zapisu do bazy: {e}', 'error')
        return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')

    if przychod > 0:
        flash(f'Sprzedano offline: {ilosc_sprzedana} szt. x {cena_sprzedazy:.0f} zl = {przychod:.0f} zl', 'success')
    else:
        flash(f'Sprzedano {ilosc_sprzedana} szt. (zostalo: {nowa_ilosc})', 'success')

    return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')


@sprzedaze_bp.route('/produkt/cofnij-offline/<int:produkt_id>', methods=['POST'])
def produkt_cofnij_offline(produkt_id):
    """Cofa sprzedaz offline - zwraca produkty do magazynu."""
    from modules.database import get_db

    ilosc_do_cofniecia = request.form.get('ilosc', 1, type=int)

    conn = get_db()

    # Pobierz produkt
    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        flash('Nie znaleziono produktu', 'error')
        return redirect(request.referrer or '/')

    # Pobierz obecne wartosci offline
    try:
        obecne_szt_offline = produkt['sprzedano_offline'] or 0
    except:
        obecne_szt_offline = 0
    try:
        obecny_przychod_offline = produkt['przychod_offline'] or 0
    except:
        obecny_przychod_offline = 0

    if ilosc_do_cofniecia > obecne_szt_offline:
        flash(f'Nie mozna cofnac {ilosc_do_cofniecia} szt. - sprzedano tylko {obecne_szt_offline} szt. offline', 'error')
        return redirect(request.referrer or '/')

    # Oblicz nowe wartosci
    nowe_szt_offline = obecne_szt_offline - ilosc_do_cofniecia

    # Proporcjonalnie zmniejsz przychod
    if obecne_szt_offline > 0:
        przychod_za_szt = obecny_przychod_offline / obecne_szt_offline
        nowy_przychod_offline = nowe_szt_offline * przychod_za_szt
    else:
        nowy_przychod_offline = 0

    # Zwieksz ilosc w magazynie
    stara_ilosc = produkt['ilosc'] or 0
    nowa_ilosc = stara_ilosc + ilosc_do_cofniecia

    # Zmien status jesli produkt mial status 'sprzedany' i byl sprzedany tylko offline
    nowy_status = produkt['status']
    if produkt['status'] == 'sprzedany' and nowe_szt_offline == 0:
        nowy_status = 'wystawiony'  # Wroc do wystawionego

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

    # FIX: Aktualizuj tez rekordy w tabeli sprzedaze (kupujacy='offline')
    # Bez tego cofniecie pojedyncze nie dzialalo — rekord sprzedazy dalej liczony w statystykach
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
            # Cofamy caly rekord
            conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('zwrot', s['id']))
            pozostalo_do_cofniecia -= s_ilosc
        else:
            # Cofamy czesciowo — zmniejsz ilosc w rekordzie
            conn.execute('UPDATE sprzedaze SET ilosc = ? WHERE id = ?', (s_ilosc - pozostalo_do_cofniecia, s['id']))
            pozostalo_do_cofniecia = 0

    conn.commit()

    flash(f'Cofnieto {ilosc_do_cofniecia} szt. ze sprzedazy offline (pozostalo offline: {nowe_szt_offline})', 'success')

    return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')


@sprzedaze_bp.route('/produkt/cofnij-sprzedaz/<int:produkt_id>', methods=['POST'])
def produkt_cofnij_sprzedaz(produkt_id):
    """Cofa sprzedaz produktu - przywraca ilosc i oznacza sprzedaze jako zwrot"""
    from modules.database import get_db

    conn = get_db()

    produkt = conn.execute('SELECT * FROM produkty WHERE id = ?', (produkt_id,)).fetchone()
    if not produkt:
        flash('Nie znaleziono produktu', 'error')
        return redirect(request.referrer or '/')

    # Znajdz aktywne sprzedaze dla tego produktu
    sprzedaze = conn.execute('''
        SELECT id, ilosc, cena FROM sprzedaze
        WHERE produkt_id = ? AND COALESCE(status,'') NOT IN ('zwrot','anulowane','anulowana')
    ''', (produkt_id,)).fetchall()

    if not sprzedaze:
        flash('Brak sprzedazy do cofniecia dla tego produktu', 'info')
        return redirect(request.referrer or '/')

    # Oblicz sume cofanych sztuk
    cofniete_szt = sum(s['ilosc'] for s in sprzedaze)

    # Oznacz sprzedaze jako zwrot
    for s in sprzedaze:
        conn.execute('UPDATE sprzedaze SET status = ? WHERE id = ?', ('zwrot', s['id']))

    # Przywroc ilosc produktu i zmien status na magazyn
    nowa_ilosc = (produkt['ilosc'] or 0) + cofniete_szt
    conn.execute('UPDATE produkty SET ilosc = ?, status = ? WHERE id = ?',
                 (nowa_ilosc, 'magazyn', produkt_id))

    # Wyczysc offline stats jesli istnieja
    try:
        conn.execute('UPDATE produkty SET sprzedano_offline = 0, przychod_offline = 0 WHERE id = ?', (produkt_id,))
    except:
        pass

    conn.commit()

    flash(f'Cofnieto sprzedaz: {cofniete_szt} szt. wraca do magazynu', 'success')
    return redirect(request.referrer or f'/palety/{produkt["paleta_id"]}')
