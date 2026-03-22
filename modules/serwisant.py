"""
Moduł Serwisant — zarządzanie uszkodzonymi produktami i naprawami
"""
from flask import Blueprint, request, redirect, url_for, session, jsonify, render_template_string
from modules.database import get_db

serwisant_bp = Blueprint('serwisant', __name__)


def _render(content, page_title='Serwis'):
    template = """{% extends "base.html" %}
{% block page_title %}""" + page_title + """{% endblock %}
{% block content %}
{{ content|safe }}
{% endblock %}"""
    from flask import current_app
    return render_template_string(template,
        content=content,
        version=current_app.config.get('VERSION', ''),
        brand_name=current_app.config.get('BRAND_NAME', 'Akces Hub'),
        current_user=session.get('user'))


@serwisant_bp.route('/')
def serwis_lista():
    """Lista produktów w serwisie"""
    conn = get_db()
    filtr = request.args.get('status', '')

    if filtr:
        items = conn.execute('''
            SELECT s.*, p.nazwa as produkt_nazwa, p.zdjecie_url, p.kod_magazynowy,
                   p.asin, p.ean, pal.dostawca
            FROM serwis s
            JOIN produkty p ON p.id = s.produkt_id
            LEFT JOIN palety pal ON pal.id = p.paleta_id
            WHERE s.status = ?
            ORDER BY s.data_przyjecia DESC
        ''', (filtr,)).fetchall()
    else:
        items = conn.execute('''
            SELECT s.*, p.nazwa as produkt_nazwa, p.zdjecie_url, p.kod_magazynowy,
                   p.asin, p.ean, pal.dostawca
            FROM serwis s
            JOIN produkty p ON p.id = s.produkt_id
            LEFT JOIN palety pal ON pal.id = p.paleta_id
            WHERE s.status != 'zwrocony' AND s.status != 'zlomowany'
            ORDER BY s.data_przyjecia DESC
        ''').fetchall()

    # Statystyki
    stats = conn.execute('''
        SELECT
            COUNT(CASE WHEN status = 'przyjety' THEN 1 END) as przyjete,
            COUNT(CASE WHEN status = 'w_naprawie' THEN 1 END) as w_naprawie,
            COUNT(CASE WHEN status = 'naprawiony' THEN 1 END) as naprawione,
            COUNT(CASE WHEN status = 'zwrocony' THEN 1 END) as zwrocone,
            COUNT(CASE WHEN status = 'zlomowany' THEN 1 END) as zlomowane,
            COALESCE(SUM(koszt_naprawy), 0) as laczny_koszt
        FROM serwis
    ''').fetchone()

    status_colors = {
        'przyjety': ('#f59e0b', 'Przyjęty'),
        'w_naprawie': ('#6366f1', 'W naprawie'),
        'naprawiony': ('#22c55e', 'Naprawiony'),
        'zwrocony': ('#10b981', 'Zwrócony'),
        'zlomowany': ('#ef4444', 'Złomowany'),
    }

    # Buduj HTML
    html = '''
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
        <h2 style="margin:0;font-size:1.3rem">🔧 Serwis</h2>
        <span style="color:var(--text-muted);font-size:0.85rem">Zarządzanie naprawami produktów</span>
    </div>

    <!-- Statystyki -->
    <div class="stat-row" style="margin-bottom:20px;grid-template-columns:repeat(6,1fr)">
        <a href="/serwis?status=przyjety" class="stat-box">
            <div class="stat-val" style="color:#f59e0b">''' + str(stats['przyjete']) + '''</div>
            <div class="stat-lbl">Przyjęte</div>
        </a>
        <a href="/serwis?status=w_naprawie" class="stat-box">
            <div class="stat-val" style="color:#6366f1">''' + str(stats['w_naprawie']) + '''</div>
            <div class="stat-lbl">W naprawie</div>
        </a>
        <a href="/serwis?status=naprawiony" class="stat-box">
            <div class="stat-val" style="color:#22c55e">''' + str(stats['naprawione']) + '''</div>
            <div class="stat-lbl">Naprawione</div>
        </a>
        <a href="/serwis?status=zwrocony" class="stat-box">
            <div class="stat-val" style="color:#10b981">''' + str(stats['zwrocone']) + '''</div>
            <div class="stat-lbl">Zwrócone</div>
        </a>
        <a href="/serwis?status=zlomowany" class="stat-box">
            <div class="stat-val" style="color:#ef4444">''' + str(stats['zlomowane']) + '''</div>
            <div class="stat-lbl">Złomowane</div>
        </a>
        <div class="stat-box">
            <div class="stat-val" style="color:var(--orange)">''' + f"{stats['laczny_koszt']:.0f}" + ''' zł</div>
            <div class="stat-lbl">Koszty napraw</div>
        </div>
    </div>

    <!-- Filtry -->
    <div style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap">
        <a href="/serwis" class="btn ''' + ('btn-primary' if not filtr else '') + '''" style="padding:6px 14px;font-size:0.8rem;border-radius:8px;text-decoration:none;background:''' + ('var(--accent)' if not filtr else 'rgba(255,255,255,0.08)') + ''';color:#fff;border:none">Aktywne</a>
        <a href="/serwis?status=przyjety" class="btn" style="padding:6px 14px;font-size:0.8rem;border-radius:8px;text-decoration:none;background:''' + ('var(--accent)' if filtr=='przyjety' else 'rgba(255,255,255,0.08)') + ''';color:#fff;border:none">📥 Przyjęte</a>
        <a href="/serwis?status=w_naprawie" class="btn" style="padding:6px 14px;font-size:0.8rem;border-radius:8px;text-decoration:none;background:''' + ('var(--accent)' if filtr=='w_naprawie' else 'rgba(255,255,255,0.08)') + ''';color:#fff;border:none">🔧 W naprawie</a>
        <a href="/serwis?status=naprawiony" class="btn" style="padding:6px 14px;font-size:0.8rem;border-radius:8px;text-decoration:none;background:''' + ('var(--accent)' if filtr=='naprawiony' else 'rgba(255,255,255,0.08)') + ''';color:#fff;border:none">✅ Naprawione</a>
        <a href="/serwis?status=zwrocony" class="btn" style="padding:6px 14px;font-size:0.8rem;border-radius:8px;text-decoration:none;background:''' + ('var(--accent)' if filtr=='zwrocony' else 'rgba(255,255,255,0.08)') + ''';color:#fff;border:none">🔄 Zwrócone</a>
        <a href="/serwis?status=zlomowany" class="btn" style="padding:6px 14px;font-size:0.8rem;border-radius:8px;text-decoration:none;background:''' + ('var(--accent)' if filtr=='zlomowany' else 'rgba(255,255,255,0.08)') + ''';color:#fff;border:none">🗑️ Złomowane</a>
    </div>

    <!-- Lista -->
    <div class="card">
    '''

    if not items:
        html += '<div style="text-align:center;padding:40px;color:var(--text-muted)">Brak produktów w serwisie 🎉</div>'
    else:
        for item in items:
            s_color, s_label = status_colors.get(item['status'], ('#888', item['status']))
            kod = item['kod_magazynowy'] or f"ID-{item['produkt_id']}"
            dni = ''
            if item['data_przyjecia']:
                from datetime import datetime
                try:
                    dt = datetime.strptime(str(item['data_przyjecia'])[:10], '%Y-%m-%d')
                    dni_val = (datetime.now() - dt).days
                    dni = f' · {dni_val} dni'
                except:
                    pass

            html += f'''
            <div style="display:flex;align-items:center;gap:12px;padding:12px;border-bottom:1px solid rgba(255,255,255,0.06)">
                <img src="{item['zdjecie_url'] or '/static/placeholder.png'}" style="width:48px;height:48px;border-radius:8px;object-fit:cover;background:#1a1a2e" onerror="this.src='/static/placeholder.png'">
                <div style="flex:1;min-width:0">
                    <div style="font-weight:600;font-size:0.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                        <a href="/magazyn/produkt/{kod}" style="color:inherit;text-decoration:none">{(item['produkt_nazwa'] or '?')[:50]}</a>
                    </div>
                    <div style="font-size:0.75rem;color:var(--text-muted)">{kod} · {item['dostawca'] or ''}{dni}</div>
                    <div style="font-size:0.8rem;color:#f59e0b;margin-top:2px">⚠️ {item['opis_usterki'] or 'Brak opisu usterki'}</div>
                </div>
                <div style="text-align:center;min-width:60px">
                    <div style="font-size:0.85rem;font-weight:700">{item['ilosc_szt']} szt</div>
                    {f'<div style="font-size:0.7rem;color:var(--text-muted)">{item["koszt_naprawy"]:.0f} zł</div>' if item['koszt_naprawy'] else ''}
                </div>
                <div style="min-width:100px;text-align:center">
                    <span style="display:inline-block;padding:4px 12px;border-radius:20px;font-size:0.75rem;font-weight:600;background:rgba(0,0,0,0.3);color:{s_color};border:1px solid {s_color}40">{s_label}</span>
                </div>
                <div style="display:flex;gap:6px">
            '''

            # Akcje zależne od statusu
            if item['status'] == 'przyjety':
                html += f'''
                    <button onclick="aktualizujSerwis({item['id']}, 'w_naprawie')" class="btn" style="padding:4px 10px;font-size:0.75rem;background:#6366f1;border:none;border-radius:6px;color:#fff;cursor:pointer">🔧 Naprawiaj</button>
                    <button onclick="zlomuj({item['id']})" class="btn" style="padding:4px 10px;font-size:0.75rem;background:#ef4444;border:none;border-radius:6px;color:#fff;cursor:pointer">🗑️</button>
                '''
            elif item['status'] == 'w_naprawie':
                html += f'''
                    <button onclick="zakonczNaprawe({item['id']})" class="btn" style="padding:4px 10px;font-size:0.75rem;background:#22c55e;border:none;border-radius:6px;color:#fff;cursor:pointer">✅ Naprawiony</button>
                    <button onclick="zlomuj({item['id']})" class="btn" style="padding:4px 10px;font-size:0.75rem;background:#ef4444;border:none;border-radius:6px;color:#fff;cursor:pointer">🗑️</button>
                '''
            elif item['status'] == 'naprawiony':
                html += f'''
                    <button onclick="zwrocDoMagazynu({item['id']})" class="btn" style="padding:4px 10px;font-size:0.75rem;background:#10b981;border:none;border-radius:6px;color:#fff;cursor:pointer">🔄 Zwróć do mag.</button>
                '''

            html += '''
                </div>
            </div>
            '''

    html += '</div>'

    # JavaScript
    html += '''
    <script>
    function aktualizujSerwis(id, nowyStatus) {
        fetch('/serwis/api/aktualizuj/' + id, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({status: nowyStatus})
        }).then(r => r.json()).then(d => {
            if (d.ok) location.reload();
            else alert(d.error || 'Błąd');
        });
    }
    function zakonczNaprawe(id) {
        var koszt = prompt('Koszt naprawy (zł):', '0');
        if (koszt === null) return;
        fetch('/serwis/api/aktualizuj/' + id, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({status: 'naprawiony', koszt_naprawy: parseFloat(koszt) || 0})
        }).then(r => r.json()).then(d => {
            if (d.ok) location.reload();
            else alert(d.error || 'Błąd');
        });
    }
    function zwrocDoMagazynu(id) {
        fetch('/serwis/api/zakoncz/' + id, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({akcja: 'zwroc'})
        }).then(r => r.json()).then(d => {
            if (d.ok) location.reload();
            else alert(d.error || 'Błąd');
        });
    }
    function zlomuj(id) {
        if (!confirm('Na pewno złomować? Ilość zostanie trwale odjęta.')) return;
        fetch('/serwis/api/zakoncz/' + id, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({akcja: 'zlomuj'})
        }).then(r => r.json()).then(d => {
            if (d.ok) location.reload();
            else alert(d.error || 'Błąd');
        });
    }
    </script>
    '''

    return _render(html, 'Serwis')


# ==================== API ====================

@serwisant_bp.route('/api/przyjmij/<int:product_id>', methods=['POST'])
def api_przyjmij(product_id):
    """Przyjmij produkt do serwisu"""
    try:
        data = request.get_json() or {}
        opis = data.get('opis_usterki', '').strip()
        ilosc = int(data.get('ilosc', 1) or 1)
        technik = data.get('technik', '').strip()

        conn = get_db()
        p = conn.execute('SELECT id, nazwa, ilosc, status FROM produkty WHERE id = ?', (product_id,)).fetchone()
        if not p:
            return jsonify({'ok': False, 'error': 'Produkt nie znaleziony'})

        if ilosc > p['ilosc']:
            return jsonify({'ok': False, 'error': f'Za dużo — produkt ma tylko {p["ilosc"]} szt'})

        # Utwórz rekord serwisu
        conn.execute('''
            INSERT INTO serwis (produkt_id, technik, opis_usterki, ilosc_szt, status)
            VALUES (?, ?, ?, ?, 'przyjety')
        ''', (product_id, technik, opis, ilosc))

        # Odejmij z magazynu
        new_ilosc = p['ilosc'] - ilosc
        if new_ilosc <= 0:
            conn.execute('UPDATE produkty SET ilosc = 0, status = ? WHERE id = ?', ('naprawa', product_id))
        else:
            conn.execute('UPDATE produkty SET ilosc = ? WHERE id = ?', (new_ilosc, product_id))

        conn.commit()
        return jsonify({'ok': True, 'msg': f'Przyjęto {ilosc} szt do serwisu'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]})


@serwisant_bp.route('/api/aktualizuj/<int:serwis_id>', methods=['POST'])
def api_aktualizuj(serwis_id):
    """Aktualizuj status naprawy"""
    try:
        data = request.get_json() or {}
        nowy_status = data.get('status', '')
        koszt = float(data.get('koszt_naprawy', 0) or 0)
        uwagi = data.get('uwagi', '')

        if nowy_status not in ('przyjety', 'w_naprawie', 'naprawiony'):
            return jsonify({'ok': False, 'error': 'Nieprawidłowy status'})

        conn = get_db()
        # Whitelist dozwolonych kolumn (ochrona przed SQL injection)
        _ALLOWED_COLS = {'status', 'koszt_naprawy', 'uwagi', 'data_zakonczenia'}
        updates = ['status = ?']
        params = [nowy_status]

        if koszt > 0:
            updates.append('koszt_naprawy = ?')
            params.append(koszt)
        if uwagi:
            updates.append('uwagi = ?')
            params.append(uwagi)
        if nowy_status in ('naprawiony',):
            updates.append("data_zakonczenia = datetime('now')")

        # Walidacja kolumn
        for u in updates:
            col_name = u.split(' ')[0]
            if col_name not in _ALLOWED_COLS:
                return jsonify({'ok': False, 'error': 'Nieprawidlowa kolumna'}), 400

        params.append(serwis_id)
        conn.execute(f"UPDATE serwis SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]})


@serwisant_bp.route('/api/zakoncz/<int:serwis_id>', methods=['POST'])
def api_zakoncz(serwis_id):
    """Zakończ naprawę — zwróć do magazynu lub złomuj"""
    try:
        data = request.get_json() or {}
        akcja = data.get('akcja', '')  # 'zwroc' lub 'zlomuj'

        conn = get_db()
        s = conn.execute('SELECT * FROM serwis WHERE id = ?', (serwis_id,)).fetchone()
        if not s:
            return jsonify({'ok': False, 'error': 'Rekord serwisu nie znaleziony'})

        p = conn.execute('SELECT id, ilosc, status FROM produkty WHERE id = ?', (s['produkt_id'],)).fetchone()
        if not p:
            return jsonify({'ok': False, 'error': 'Produkt nie znaleziony'})

        if akcja == 'zwroc':
            # Zwróć do magazynu — dodaj ilość z powrotem
            conn.execute('UPDATE produkty SET ilosc = ilosc + ?, status = ? WHERE id = ?',
                         (s['ilosc_szt'], 'magazyn', s['produkt_id']))
            conn.execute("UPDATE serwis SET status = 'zwrocony', data_zakonczenia = datetime('now') WHERE id = ?",
                         (serwis_id,))
            conn.commit()
            return jsonify({'ok': True, 'msg': f'Zwrócono {s["ilosc_szt"]} szt do magazynu'})

        elif akcja == 'zlomuj':
            # Złomuj — nie zwracaj ilości (została już odjęta przy przyjęciu)
            conn.execute("UPDATE serwis SET status = 'zlomowany', data_zakonczenia = datetime('now') WHERE id = ?",
                         (serwis_id,))
            # Jeśli produkt ma 0 szt i status naprawa, ustaw status na 'zlomowany'
            if p['ilosc'] <= 0 and p['status'] == 'naprawa':
                conn.execute("UPDATE produkty SET status = 'zlomowany' WHERE id = ?", (s['produkt_id'],))
            conn.commit()
            return jsonify({'ok': True, 'msg': f'Złomowano {s["ilosc_szt"]} szt'})

        else:
            return jsonify({'ok': False, 'error': 'Nieznana akcja'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]})
