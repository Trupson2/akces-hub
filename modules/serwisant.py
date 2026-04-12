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
        current_user=session.get('username'))


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

    # Buduj HTML — System Monitor design
    total_items = stats['przyjete'] + stats['w_naprawie'] + stats['naprawione'] + stats['zwrocone'] + stats['zlomowane']

    def _pct(val):
        return int((val / total_items * 100) if total_items > 0 else 0)

    # SVG gauge helper: radius=42, circumference=263.9
    _C = 263.9
    def _gauge_offset(pct):
        return round(_C * (1 - pct / 100), 1)

    queue_pct  = _pct(stats['przyjete'] + stats['w_naprawie'])
    repair_pct = _pct(stats['naprawione'])
    error_pct  = _pct(stats['zlomowane'])
    active_jobs = stats['przyjete'] + stats['w_naprawie']
    completion_pct = _pct(stats['naprawione'] + stats['zwrocone'])

    html = f'''
    <style>
        :root{{--sv-cyan:#8ff5ff;--sv-pink:#ff6b9b;--sv-lime:#cafd00;--sv-lime-dim:#beee00;--sv-card:#131315;--sv-card2:#19191c;--sv-card3:#1f1f22;--sv-card4:#262528;--sv-border:rgba(255,255,255,0.06);--sv-text:#f9f5f8;--sv-muted:#adaaad}}

        /* ─── Grid BG ─── */
        .sm-grid-bg{{background-size:40px 40px;background-image:linear-gradient(to right,rgba(143,245,255,0.04) 1px,transparent 1px),linear-gradient(to bottom,rgba(143,245,255,0.04) 1px,transparent 1px);position:fixed;inset:0;pointer-events:none;z-index:0}}

        /* ─── Header ─── */
        .sm-hdr{{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:28px;flex-wrap:wrap;gap:12px}}
        .sm-hdr-title{{font-family:"Space Grotesk",sans-serif;font-size:clamp(1.8rem,4vw,2.8rem);font-weight:900;letter-spacing:-0.04em;line-height:1;color:var(--sv-text)}}
        .sm-hdr-title span{{color:var(--sv-cyan)}}
        .sm-hdr-sub{{font-size:0.68rem;color:var(--sv-muted);text-transform:uppercase;letter-spacing:0.15em;margin-top:6px;font-family:"Space Grotesk",sans-serif}}
        .sm-status-pill{{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;background:rgba(143,245,255,0.06);border:1px solid rgba(143,245,255,0.15);font-size:0.68rem;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;color:var(--sv-cyan);font-family:"Space Grotesk",sans-serif}}
        .sm-dot{{width:7px;height:7px;border-radius:50%;background:var(--sv-lime);box-shadow:0 0 8px var(--sv-lime);animation:sm-pulse 1.5s ease-in-out infinite}}
        @keyframes sm-pulse{{0%,100%{{opacity:1;box-shadow:0 0 6px var(--sv-lime)}}50%{{opacity:0.4;box-shadow:0 0 2px var(--sv-lime)}}}}

        /* ─── Gauge grid ─── */
        .sm-gauges{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:20px}}
        @media(max-width:640px){{.sm-gauges{{grid-template-columns:1fr 1fr}}}}
        .sm-gauge-card{{background:var(--sv-card);border:1px solid rgba(255,255,255,0.05);padding:20px 16px;text-align:center;position:relative;overflow:hidden}}
        .sm-gauge-card::before{{content:"";position:absolute;top:0;left:0;right:0;height:2px}}
        .sm-gauge-cyan::before{{background:linear-gradient(90deg,transparent,var(--sv-cyan),transparent)}}
        .sm-gauge-pink::before{{background:linear-gradient(90deg,transparent,var(--sv-pink),transparent)}}
        .sm-gauge-lime::before{{background:linear-gradient(90deg,transparent,var(--sv-lime),transparent)}}
        .sm-gauge-svg{{width:110px;height:110px;margin:0 auto 10px}}
        .sm-gauge-track{{fill:none;stroke:rgba(255,255,255,0.06);stroke-width:8;stroke-linecap:round}}
        .sm-gauge-fill{{fill:none;stroke-width:8;stroke-linecap:round;transition:stroke-dashoffset 1s ease;transform:rotate(-90deg);transform-origin:center}}
        .sm-gauge-cyan .sm-gauge-fill{{stroke:var(--sv-cyan);filter:drop-shadow(0 0 4px rgba(143,245,255,0.5))}}
        .sm-gauge-pink .sm-gauge-fill{{stroke:var(--sv-pink);filter:drop-shadow(0 0 4px rgba(255,107,155,0.5))}}
        .sm-gauge-lime .sm-gauge-fill{{stroke:var(--sv-lime);filter:drop-shadow(0 0 4px rgba(202,253,0,0.5))}}
        .sm-gauge-pct{{font-family:"Space Grotesk",sans-serif;font-size:1.6rem;font-weight:900;letter-spacing:-0.04em;line-height:1}}
        .sm-gauge-cyan .sm-gauge-pct{{color:var(--sv-cyan)}}
        .sm-gauge-pink .sm-gauge-pct{{color:var(--sv-pink)}}
        .sm-gauge-lime .sm-gauge-pct{{color:var(--sv-lime)}}
        .sm-gauge-lbl{{font-size:0.6rem;font-weight:700;text-transform:uppercase;letter-spacing:0.15em;color:var(--sv-muted);margin-top:4px;font-family:"Space Grotesk",sans-serif}}
        .sm-gauge-cnt{{font-size:0.72rem;color:var(--sv-muted);margin-top:2px}}

        /* ─── Station specs ─── */
        .sm-specs{{display:grid;grid-template-columns:repeat(4,1fr);gap:2px;margin-bottom:20px;background:rgba(255,255,255,0.03)}}
        @media(max-width:640px){{.sm-specs{{grid-template-columns:1fr 1fr}}}}
        .sm-spec{{background:var(--sv-card);padding:14px 16px}}
        .sm-spec-lbl{{font-size:0.58rem;text-transform:uppercase;letter-spacing:0.15em;color:var(--sv-muted);font-weight:700;font-family:"Space Grotesk",sans-serif;margin-bottom:6px}}
        .sm-spec-val{{font-family:"Space Grotesk",sans-serif;font-size:1.15rem;font-weight:800;letter-spacing:-0.03em}}
        .sm-spec-val.cyan{{color:var(--sv-cyan)}}
        .sm-spec-val.pink{{color:var(--sv-pink)}}
        .sm-spec-val.lime{{color:var(--sv-lime)}}

        /* ─── Filter pills ─── */
        .sv-filters{{display:flex;gap:4px;overflow-x:auto;padding:4px;background:var(--sv-card);margin-bottom:16px;scrollbar-width:none}}
        .sv-filters::-webkit-scrollbar{{display:none}}
        .sv-filter-pill{{padding:8px 16px;font-size:0.72rem;font-weight:600;text-decoration:none;color:var(--sv-muted);background:transparent;border:none;white-space:nowrap;transition:all 0.15s;cursor:pointer;font-family:"Space Grotesk",sans-serif;text-transform:uppercase;letter-spacing:0.05em}}
        .sv-filter-pill:hover{{color:var(--sv-text);background:rgba(255,255,255,0.05)}}
        .sv-filter-pill.active{{background:var(--sv-cyan);color:#005d63;font-weight:700}}

        /* ─── Process queue table ─── */
        .sm-table-wrap{{background:var(--sv-card);border:1px solid rgba(255,255,255,0.04)}}
        .sm-table-hdr{{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.04)}}
        .sm-table-title{{font-family:"Space Grotesk",sans-serif;font-size:0.72rem;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;color:var(--sv-cyan)}}
        .sm-table-cnt{{font-size:0.6rem;color:var(--sv-muted);font-family:"Space Grotesk",sans-serif}}
        .sm-queue{{display:flex;flex-direction:column}}
        .sm-row{{display:flex;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.03);transition:background 0.15s}}
        .sm-row:last-child{{border-bottom:none}}
        .sm-row:hover{{background:var(--sv-card3)}}
        .sm-row-cyan{{border-left:3px solid var(--sv-cyan)}}
        .sm-row-pink{{border-left:3px solid var(--sv-pink)}}
        .sm-row-lime{{border-left:3px solid var(--sv-lime)}}
        .sm-row-muted{{border-left:3px solid var(--sv-muted)}}
        .sm-row-img{{width:44px;height:44px;object-fit:cover;background:var(--sv-card2);flex-shrink:0}}
        .sm-row-pid{{font-family:"Space Grotesk",sans-serif;font-size:0.55rem;font-weight:700;color:rgba(143,245,255,0.5);text-transform:uppercase;letter-spacing:0.1em;margin-bottom:2px}}
        .sm-row-name{{font-size:0.82rem;font-weight:600;color:var(--sv-text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
        .sm-row-name a{{color:inherit;text-decoration:none}}
        .sm-row-name a:hover{{color:var(--sv-cyan)}}
        .sm-row-meta{{font-size:0.65rem;color:var(--sv-muted);margin-top:2px}}
        .sm-row-fault{{font-size:0.72rem;color:var(--sv-pink);margin-top:2px;display:flex;align-items:center;gap:3px}}
        .sm-badge{{display:inline-block;padding:3px 10px;font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:0.08em;white-space:nowrap;font-family:"Space Grotesk",sans-serif}}
        .sm-badge-cyan{{background:rgba(143,245,255,0.1);color:var(--sv-cyan);border:1px solid rgba(143,245,255,0.2)}}
        .sm-badge-pink{{background:rgba(255,107,155,0.1);color:var(--sv-pink);border:1px solid rgba(255,107,155,0.2)}}
        .sm-badge-lime{{background:rgba(202,253,0,0.1);color:var(--sv-lime);border:1px solid rgba(202,253,0,0.2)}}
        .sm-badge-muted{{background:rgba(173,170,173,0.1);color:var(--sv-muted);border:1px solid rgba(173,170,173,0.2)}}
        .sm-badge-red{{background:rgba(255,107,155,0.08);color:#ff4f7f;border:1px solid rgba(255,79,127,0.2)}}
        .sm-act{{font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:0.65rem;text-transform:uppercase;letter-spacing:0.04em;padding:5px 12px;border:none;cursor:pointer;transition:all 0.15s}}
        .sm-act:hover{{opacity:0.85}}
        .sm-act-repair{{background:var(--sv-pink);color:#47001f}}
        .sm-act-done{{background:var(--sv-lime);color:#1a2e00}}
        .sm-act-return{{background:var(--sv-cyan);color:#005d63}}
        .sm-act-scrap{{background:rgba(255,79,127,0.12);color:#ff4f7f;border:1px solid rgba(255,79,127,0.25)}}

        /* ─── Empty ─── */
        .sm-empty{{padding:60px 30px;text-align:center;border:1px dashed rgba(143,245,255,0.1)}}
        .sm-empty-val{{font-family:"Space Grotesk",sans-serif;font-size:4rem;font-weight:900;color:rgba(143,245,255,0.06);margin-bottom:12px}}
        .sm-empty-txt{{font-size:0.85rem;color:var(--sv-muted)}}

        @media(max-width:768px){{
            .sm-row{{flex-wrap:wrap}}
            .sm-row-actions{{width:100%;display:flex;justify-content:flex-end;gap:6px}}
        }}
    </style>

    <div class="sm-grid-bg"></div>
    <div style="position:relative;z-index:1">

    <!-- ═══ HEADER ═══ -->
    <div class="sm-hdr">
        <div>
            <div class="sm-hdr-title">SYS<span>_MONITOR</span></div>
            <div class="sm-hdr-sub">STATION_01 &nbsp;|&nbsp; REPAIR_QUEUE &nbsp;|&nbsp; NODE_ALPHA</div>
        </div>
        <div class="sm-status-pill">
            <div class="sm-dot"></div>
            SYSTEM_ONLINE
        </div>
    </div>

    <!-- ═══ GAUGES ═══ -->
    <div class="sm-gauges">
        <a href="/serwis?status=przyjety" class="sm-gauge-card sm-gauge-cyan" style="text-decoration:none;color:inherit">
            <svg class="sm-gauge-svg" viewBox="0 0 110 110">
                <circle class="sm-gauge-track" cx="55" cy="55" r="42"/>
                <circle class="sm-gauge-fill" cx="55" cy="55" r="42"
                    stroke-dasharray="{_C} {_C}"
                    stroke-dashoffset="{_gauge_offset(queue_pct)}"/>
            </svg>
            <div class="sm-gauge-pct">{queue_pct}%</div>
            <div class="sm-gauge-lbl">QUEUE_LOAD</div>
            <div class="sm-gauge-cnt">{active_jobs} aktywnych</div>
        </a>
        <a href="/serwis?status=naprawiony" class="sm-gauge-card sm-gauge-lime" style="text-decoration:none;color:inherit">
            <svg class="sm-gauge-svg" viewBox="0 0 110 110">
                <circle class="sm-gauge-track" cx="55" cy="55" r="42"/>
                <circle class="sm-gauge-fill" cx="55" cy="55" r="42"
                    stroke-dasharray="{_C} {_C}"
                    stroke-dashoffset="{_gauge_offset(repair_pct)}"/>
            </svg>
            <div class="sm-gauge-pct">{repair_pct}%</div>
            <div class="sm-gauge-lbl">REPAIR_RATE</div>
            <div class="sm-gauge-cnt">{stats["naprawione"]} naprawionych</div>
        </a>
        <a href="/serwis?status=zlomowany" class="sm-gauge-card sm-gauge-pink" style="text-decoration:none;color:inherit">
            <svg class="sm-gauge-svg" viewBox="0 0 110 110">
                <circle class="sm-gauge-track" cx="55" cy="55" r="42"/>
                <circle class="sm-gauge-fill" cx="55" cy="55" r="42"
                    stroke-dasharray="{_C} {_C}"
                    stroke-dashoffset="{_gauge_offset(error_pct)}"/>
            </svg>
            <div class="sm-gauge-pct">{error_pct}%</div>
            <div class="sm-gauge-lbl">ERROR_RATE</div>
            <div class="sm-gauge-cnt">{stats["zlomowane"]} złomowanych</div>
        </a>
    </div>

    <!-- ═══ STATION SPECS ═══ -->
    <div class="sm-specs">
        <div class="sm-spec">
            <div class="sm-spec-lbl">TOTAL_JOBS</div>
            <div class="sm-spec-val cyan">{total_items}</div>
        </div>
        <div class="sm-spec">
            <div class="sm-spec-lbl">ACTIVE_PROC</div>
            <div class="sm-spec-val pink">{active_jobs}</div>
        </div>
        <div class="sm-spec">
            <div class="sm-spec-lbl">COMPLETION</div>
            <div class="sm-spec-val lime">{completion_pct}%</div>
        </div>
        <div class="sm-spec">
            <div class="sm-spec-lbl">REPAIR_COST</div>
            <div class="sm-spec-val" style="color:var(--sv-muted)">{stats["laczny_koszt"]:.0f} zl</div>
        </div>
    </div>

    <!-- ═══ FILTER ═══ -->
    <div class="sv-filters">
        <a href="/serwis" class="sv-filter-pill {'active' if not filtr else ''}">Aktywne</a>
        <a href="/serwis?status=przyjety" class="sv-filter-pill {'active' if filtr=='przyjety' else ''}"><span class=material-symbols-outlined style="font-size:0.75rem">download</span> Przyjete</a>
        <a href="/serwis?status=w_naprawie" class="sv-filter-pill {'active' if filtr=='w_naprawie' else ''}"><span class=material-symbols-outlined style="font-size:0.75rem">build</span> W naprawie</a>
        <a href="/serwis?status=naprawiony" class="sv-filter-pill {'active' if filtr=='naprawiony' else ''}"><span class=material-symbols-outlined style="font-size:0.75rem">check_circle</span> Naprawione</a>
        <a href="/serwis?status=zwrocony" class="sv-filter-pill {'active' if filtr=='zwrocony' else ''}"><span class=material-symbols-outlined style="font-size:0.75rem">sync</span> Zwrocone</a>
        <a href="/serwis?status=zlomowany" class="sv-filter-pill {'active' if filtr=='zlomowany' else ''}"><span class=material-symbols-outlined style="font-size:0.75rem">delete</span> Zlomowane</a>
    </div>

    <!-- ═══ PROCESS QUEUE ═══ -->
    <div class="sm-table-wrap">
        <div class="sm-table-hdr">
            <div class="sm-table-title"><span class=material-symbols-outlined style="font-size:0.9rem;vertical-align:middle">queue</span> PROCESS_QUEUE</div>
            <div class="sm-table-cnt">{len(items)} procesow</div>
        </div>
    '''

    if not items:
        html += '''
        <div class="sm-empty">
            <div class="sm-empty-val">IDLE</div>
            <div class="sm-empty-txt">Brak procesow w kolejce</div>
        </div>
        '''
    else:
        html += '<div class="sm-queue">'
        row_cls = {
            'przyjety': 'sm-row-cyan',
            'w_naprawie': 'sm-row-pink',
            'naprawiony': 'sm-row-lime',
            'zwrocony': 'sm-row-muted',
            'zlomowany': 'sm-row-muted',
        }
        badge_cls_map = {
            'przyjety': 'sm-badge-cyan',
            'w_naprawie': 'sm-badge-pink',
            'naprawiony': 'sm-badge-lime',
            'zwrocony': 'sm-badge-muted',
            'zlomowany': 'sm-badge-red',
        }

        for item in items:
            s_color, s_label = status_colors.get(item['status'], ('#888', item['status']))
            kod = item['kod_magazynowy'] or f"ID-{item['produkt_id']}"
            dni = ''
            if item['data_przyjecia']:
                from datetime import datetime
                try:
                    dt = datetime.strptime(str(item['data_przyjecia'])[:10], '%Y-%m-%d')
                    dni_val = (datetime.now() - dt).days
                    dni = f'{dni_val}d'
                except:
                    pass

            rcls = row_cls.get(item['status'], '')
            bcls = badge_cls_map.get(item['status'], 'sm-badge-muted')

            html += f'''
            <div class="sm-row {rcls}">
                <img src="{item['zdjecie_url'] or '/static/placeholder.png'}" class="sm-row-img" onerror="this.src='/static/placeholder.png'">
                <div style="flex:1;min-width:0">
                    <div class="sm-row-pid">PID_{item['id']:04d} &nbsp;|&nbsp; {kod}{(' &nbsp;|&nbsp; ' + dni) if dni else ''}</div>
                    <div class="sm-row-name"><a href="/magazyn/produkt/{kod}">{(item['produkt_nazwa'] or '?')[:55]}</a></div>
                    <div class="sm-row-meta">{item['dostawca'] or 'brak dostawcy'} &nbsp;&middot;&nbsp; {item['ilosc_szt']} szt{(' &nbsp;&middot;&nbsp; ' + str(int(item['koszt_naprawy'])) + ' zl') if item['koszt_naprawy'] else ''}</div>
                    <div class="sm-row-fault"><span class=material-symbols-outlined style="font-size:0.75rem">bolt</span> {item['opis_usterki'] or 'Brak opisu usterki'}</div>
                </div>
                <span class="sm-badge {bcls}">{s_label}</span>
                <div style="display:flex;gap:6px;flex-shrink:0" class="sm-row-actions">
            '''

            if item['status'] == 'przyjety':
                html += f'''
                    <button onclick="aktualizujSerwis({item['id']}, 'w_naprawie')" class="sm-act sm-act-repair"><span class=material-symbols-outlined style="font-size:0.8rem">build</span> REPAIR</button>
                    <button onclick="zlomuj({item['id']})" class="sm-act sm-act-scrap"><span class=material-symbols-outlined style="font-size:0.8rem">delete</span></button>
                '''
            elif item['status'] == 'w_naprawie':
                html += f'''
                    <button onclick="zakonczNaprawe({item['id']})" class="sm-act sm-act-done"><span class=material-symbols-outlined style="font-size:0.8rem">check_circle</span> DONE</button>
                    <button onclick="zlomuj({item['id']})" class="sm-act sm-act-scrap"><span class=material-symbols-outlined style="font-size:0.8rem">delete</span></button>
                '''
            elif item['status'] == 'naprawiony':
                html += f'''
                    <button onclick="zwrocDoMagazynu({item['id']})" class="sm-act sm-act-return"><span class=material-symbols-outlined style="font-size:0.8rem">sync</span> RETURN</button>
                '''

            html += '''
                </div>
            </div>
            '''

        html += '</div>'

    html += '</div></div>'  # sm-table-wrap + outer div

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
