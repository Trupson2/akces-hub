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

    # Buduj HTML — Stitch design system
    total_items = stats['przyjete'] + stats['w_naprawie'] + stats['naprawione'] + stats['zwrocone'] + stats['zlomowane']

    html = '''
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700;900&family=Manrope:wght@300;400;500;600;700;800&display=swap');

        /* ─── Base ─── */
        .sv-wrap{max-width:1100px;margin:0 auto;font-family:'Manrope',sans-serif;position:relative;z-index:1;padding:0 8px}
        .sv-headline{font-family:'Space Grotesk',sans-serif}
        .sv-label{font-family:'Manrope',sans-serif;font-size:10px;text-transform:uppercase;letter-spacing:0.2em;color:rgba(255,255,255,0.45)}

        /* ─── Neon colours ─── */
        :root{--sv-cyan:#8ff5ff;--sv-pink:#ff6b9b;--sv-lime:#cafd00;--sv-lime-dim:#beee00;--sv-bg:#0e0e10;--sv-card:#131315;--sv-card2:#19191c;--sv-card3:#1f1f22;--sv-card4:#262528;--sv-border:rgba(255,255,255,0.06);--sv-text:#f9f5f8;--sv-muted:#adaaad}

        /* ─── Cyber grid bg ─── */
        .sv-grid-bg{background-size:40px 40px;background-image:linear-gradient(to right,rgba(143,245,255,0.05) 1px,transparent 1px),linear-gradient(to bottom,rgba(143,245,255,0.05) 1px,transparent 1px);position:fixed;inset:0;pointer-events:none;z-index:0}

        /* ─── Stat cards scroll ─── */
        .sv-stats-scroll{display:flex;gap:12px;overflow-x:auto;padding-bottom:8px;scrollbar-width:thin;scrollbar-color:rgba(143,245,255,0.2) transparent;-webkit-overflow-scrolling:touch}
        .sv-stats-scroll::-webkit-scrollbar{height:4px}
        .sv-stats-scroll::-webkit-scrollbar-track{background:transparent}
        .sv-stats-scroll::-webkit-scrollbar-thumb{background:rgba(143,245,255,0.2);border-radius:2px}
        .sv-stat-card{min-width:192px;width:192px;flex-shrink:0;background:var(--sv-card);padding:16px 18px;border-left:4px solid var(--sv-cyan);transition:all 0.2s;text-decoration:none;color:var(--sv-text);display:block}
        .sv-stat-card:hover{background:var(--sv-card3);box-shadow:0 0 15px rgba(143,245,255,0.15)}
        .sv-stat-card .sv-stat-icon{font-size:18px;margin-bottom:6px;opacity:0.7}
        .sv-stat-card .sv-stat-label{font-family:'Manrope',sans-serif;font-size:10px;text-transform:uppercase;letter-spacing:0.15em;color:var(--sv-muted);margin-bottom:4px}
        .sv-stat-card .sv-stat-val{font-family:'Space Grotesk',sans-serif;font-size:2rem;font-weight:900;letter-spacing:-0.04em;line-height:1.1}
        .sv-stat-card .sv-progress{height:3px;background:var(--sv-card4);border-radius:2px;overflow:hidden;margin-top:10px}
        .sv-stat-card .sv-progress-bar{height:100%;border-radius:2px;transition:width 0.5s}

        /* card colour variants */
        .sv-stat-cyan{border-left-color:var(--sv-cyan)}
        .sv-stat-cyan .sv-stat-val{color:var(--sv-cyan)}
        .sv-stat-cyan .sv-progress-bar{background:var(--sv-cyan);box-shadow:0 0 8px var(--sv-cyan)}
        .sv-stat-pink{border-left-color:var(--sv-pink)}
        .sv-stat-pink .sv-stat-val{color:var(--sv-pink)}
        .sv-stat-pink .sv-progress-bar{background:var(--sv-pink);box-shadow:0 0 8px var(--sv-pink)}
        .sv-stat-lime{border-left-color:var(--sv-lime)}
        .sv-stat-lime .sv-stat-val{color:var(--sv-lime)}
        .sv-stat-lime .sv-progress-bar{background:var(--sv-lime);box-shadow:0 0 8px var(--sv-lime)}
        .sv-stat-outline{border-left-color:var(--sv-muted);border:1px solid var(--sv-border);border-left:4px solid var(--sv-muted)}
        .sv-stat-outline .sv-stat-val{color:var(--sv-muted)}
        .sv-stat-outline .sv-progress-bar{background:var(--sv-muted)}
        .sv-stat-secondary{border-left-color:var(--sv-pink);background:rgba(255,107,155,0.05)}
        .sv-stat-secondary .sv-stat-val{color:var(--sv-pink)}
        .sv-stat-secondary .sv-progress-bar{background:var(--sv-pink);box-shadow:0 0 8px rgba(255,107,155,0.3)}
        .sv-stat-cost{border-left-color:var(--sv-lime-dim)}
        .sv-stat-cost .sv-stat-val{color:var(--sv-lime-dim);font-size:1.5rem}
        .sv-stat-cost .sv-progress-bar{background:var(--sv-lime-dim);box-shadow:0 0 8px var(--sv-lime-dim)}

        /* ─── Filter pills ─── */
        .sv-filters{display:flex;gap:4px;overflow-x:auto;padding:4px;background:var(--sv-card);border-radius:999px;margin-bottom:20px;scrollbar-width:none;-ms-overflow-style:none}
        .sv-filters::-webkit-scrollbar{display:none}
        .sv-filter-pill{padding:8px 18px;font-family:'Manrope',sans-serif;font-size:0.8rem;font-weight:600;border-radius:999px;text-decoration:none;color:var(--sv-muted);background:transparent;border:none;white-space:nowrap;transition:all 0.15s;cursor:pointer}
        .sv-filter-pill:hover{color:var(--sv-text);background:rgba(255,255,255,0.06)}
        .sv-filter-pill.active{background:var(--sv-cyan);color:#005d63;font-weight:700}

        /* ─── Empty state ─── */
        .sv-empty{border:2px dashed rgba(143,245,255,0.15);padding:60px 30px;text-align:center;position:relative;overflow:hidden}
        .sv-empty-watermark{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-family:'Space Grotesk',sans-serif;font-size:clamp(3rem,8vw,6rem);font-weight:900;font-style:italic;letter-spacing:-0.04em;color:rgba(143,245,255,0.04);white-space:nowrap;pointer-events:none}
        .sv-empty-emoji{font-size:3rem;margin-bottom:16px}
        .sv-empty-text{font-family:'Manrope',sans-serif;font-size:1rem;color:var(--sv-muted);margin-bottom:20px}
        .sv-btn-scan{font-family:'Space Grotesk',sans-serif;font-weight:900;text-transform:uppercase;letter-spacing:-0.02em;font-style:italic;padding:14px 28px;border:none;cursor:pointer;transition:all 0.15s;font-size:0.95rem;background:var(--sv-cyan);color:#005d63;box-shadow:0 4px 15px rgba(143,245,255,0.3)}
        .sv-btn-scan:hover{transform:scale(1.02);box-shadow:0 4px 25px rgba(143,245,255,0.4)}
        .sv-btn-scan:active{transform:scale(0.95)}

        /* ─── List items ─── */
        .sv-list{display:flex;flex-direction:column;gap:2px}
        .sv-item{display:flex;align-items:center;gap:14px;padding:16px 20px;background:var(--sv-card);border-left:3px solid transparent;transition:all 0.2s}
        .sv-item:hover{background:var(--sv-card3);box-shadow:0 0 15px rgba(143,245,255,0.08)}
        .sv-item-cyan{border-left-color:var(--sv-cyan)}
        .sv-item-pink{border-left-color:var(--sv-pink)}
        .sv-item-lime{border-left-color:var(--sv-lime)}
        .sv-item-muted{border-left-color:var(--sv-muted)}
        .sv-item-red{border-left-color:var(--sv-pink)}
        .sv-item-img{width:52px;height:52px;border-radius:4px;object-fit:cover;background:var(--sv-card2);flex-shrink:0}
        .sv-item-name{font-family:'Space Grotesk',sans-serif;font-weight:600;font-size:0.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--sv-text)}
        .sv-item-name a{color:inherit;text-decoration:none}
        .sv-item-name a:hover{color:var(--sv-cyan)}
        .sv-item-meta{font-family:'Manrope',sans-serif;font-size:0.72rem;color:var(--sv-muted);margin-top:2px}
        .sv-item-fault{font-family:'Manrope',sans-serif;font-size:0.78rem;color:var(--sv-pink);margin-top:3px;display:flex;align-items:center;gap:4px}
        .sv-item-qty{font-family:'Space Grotesk',sans-serif;font-size:0.95rem;font-weight:700;text-align:center;min-width:55px;color:var(--sv-text)}
        .sv-item-cost{font-family:'Manrope',sans-serif;font-size:0.68rem;color:var(--sv-muted)}

        /* ─── Status badge ─── */
        .sv-badge{display:inline-block;padding:4px 14px;font-family:'Manrope',sans-serif;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:0.08em;white-space:nowrap}
        .sv-badge-cyan{background:rgba(143,245,255,0.1);color:var(--sv-cyan);border:1px solid rgba(143,245,255,0.2)}
        .sv-badge-pink{background:rgba(255,107,155,0.1);color:var(--sv-pink);border:1px solid rgba(255,107,155,0.2)}
        .sv-badge-lime{background:rgba(202,253,0,0.1);color:var(--sv-lime);border:1px solid rgba(202,253,0,0.2)}
        .sv-badge-muted{background:rgba(173,170,173,0.1);color:var(--sv-muted);border:1px solid rgba(173,170,173,0.2)}
        .sv-badge-red{background:rgba(255,107,155,0.08);color:#ff4f7f;border:1px solid rgba(255,79,127,0.2)}

        /* ─── Action buttons ─── */
        .sv-act{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.02em;padding:6px 14px;border:none;cursor:pointer;transition:all 0.15s;color:#fff}
        .sv-act:hover{transform:scale(1.03)}
        .sv-act:active{transform:scale(0.95)}
        .sv-act-repair{background:var(--sv-pink);color:#47001f}
        .sv-act-done{background:var(--sv-lime);color:#1a2e00}
        .sv-act-return{background:var(--sv-cyan);color:#005d63}
        .sv-act-scrap{background:rgba(255,79,127,0.15);color:#ff4f7f;border:1px solid rgba(255,79,127,0.3)}
        .sv-act-scrap:hover{background:rgba(255,79,127,0.25)}

        /* ─── SYS_LOG badge ─── */
        .sv-syslog{font-family:'Manrope',sans-serif;font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:0.12em;padding:2px 8px;background:rgba(143,245,255,0.08);color:rgba(143,245,255,0.5);display:inline-block;margin-bottom:4px}

        /* ─── Pulse dot ─── */
        .sv-pulse-dot{width:8px;height:8px;border-radius:50%;animation:sv-dot-pulse 2s infinite}
        @keyframes sv-dot-pulse{0%,100%{box-shadow:0 0 5px rgba(202,253,0,0.4)}50%{box-shadow:0 0 15px rgba(202,253,0,0.8)}}

        /* ─── Responsive ─── */
        @media(max-width:768px){
            .sv-stats-scroll{gap:8px}
            .sv-stat-card{min-width:160px;width:160px;padding:12px 14px}
            .sv-item{flex-wrap:wrap;gap:10px;padding:14px 16px}
            .sv-item-actions{width:100%;display:flex;justify-content:flex-end}
        }
    </style>

    <div class="sv-grid-bg"></div>

    <div class="sv-wrap">

    <!-- ═══ Header ═══ -->
    <div style="display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:32px;flex-wrap:wrap;gap:16px">
        <div>
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
                <div class="sv-pulse-dot" style="background:var(--sv-lime-dim)"></div>
                <span class="sv-label" style="color:var(--sv-lime-dim);font-weight:900">REALTIME_FEED</span>
            </div>
            <h1 class="sv-headline" style="font-size:clamp(2.5rem,6vw,4rem);font-weight:900;font-style:italic;letter-spacing:-0.04em;line-height:1;color:var(--sv-text)">
                SERVICE<span style="color:var(--sv-cyan)">_METRICS</span>
            </h1>
        </div>
    </div>

    <!-- ═══ Stat cards (horizontal scroll) ═══ -->
    <div class="sv-stats-scroll" style="margin-bottom:24px">
    '''

    # Stat card helper
    def _pct(val):
        return int((val / total_items * 100) if total_items > 0 else 0)

    html += f'''
        <a href="/serwis?status=przyjety" class="sv-stat-card sv-stat-cyan">
            <div class="sv-stat-icon"><span class=material-symbols-outlined style=font-size:1rem>download</span></div>
            <div class="sv-stat-label">Accepted</div>
            <div class="sv-stat-val">{stats['przyjete']}</div>
            <div class="sv-progress"><div class="sv-progress-bar" style="width:{_pct(stats['przyjete'])}%"></div></div>
        </a>
        <a href="/serwis?status=w_naprawie" class="sv-stat-card sv-stat-pink">
            <div class="sv-stat-icon"><span class=material-symbols-outlined style=font-size:1rem>build</span></div>
            <div class="sv-stat-label">In Repair</div>
            <div class="sv-stat-val">{stats['w_naprawie']}</div>
            <div class="sv-progress"><div class="sv-progress-bar" style="width:{_pct(stats['w_naprawie'])}%"></div></div>
        </a>
        <a href="/serwis?status=naprawiony" class="sv-stat-card sv-stat-lime">
            <div class="sv-stat-icon"><span class=material-symbols-outlined style=font-size:1rem>check_circle</span></div>
            <div class="sv-stat-label">Repaired</div>
            <div class="sv-stat-val">{stats['naprawione']}</div>
            <div class="sv-progress"><div class="sv-progress-bar" style="width:{_pct(stats['naprawione'])}%"></div></div>
        </a>
        <a href="/serwis?status=zwrocony" class="sv-stat-card sv-stat-outline">
            <div class="sv-stat-icon"><span class=material-symbols-outlined style=font-size:1rem>sync</span></div>
            <div class="sv-stat-label">Returned</div>
            <div class="sv-stat-val">{stats['zwrocone']}</div>
            <div class="sv-progress"><div class="sv-progress-bar" style="width:{_pct(stats['zwrocone'])}%"></div></div>
        </a>
        <a href="/serwis?status=zlomowany" class="sv-stat-card sv-stat-secondary">
            <div class="sv-stat-icon"><span class=material-symbols-outlined style=font-size:1rem>delete</span></div>
            <div class="sv-stat-label">Scrapped</div>
            <div class="sv-stat-val">{stats['zlomowane']}</div>
            <div class="sv-progress"><div class="sv-progress-bar" style="width:{_pct(stats['zlomowane'])}%"></div></div>
        </a>
        <div class="sv-stat-card sv-stat-cost">
            <div class="sv-stat-icon"><span class=material-symbols-outlined style=font-size:1rem>paid</span></div>
            <div class="sv-stat-label">Total Costs</div>
            <div class="sv-stat-val">{stats['laczny_koszt']:.0f} zł</div>
            <div class="sv-progress"><div class="sv-progress-bar" style="width:100%"></div></div>
        </div>
    </div>

    <!-- ═══ Filter pills ═══ -->
    <div class="sv-filters">
        <a href="/serwis" class="sv-filter-pill ''' + ('active' if not filtr else '') + '''">Aktywne</a>
        <a href="/serwis?status=przyjety" class="sv-filter-pill ''' + ('active' if filtr=='przyjety' else '') + '''"><span class=material-symbols-outlined style=font-size:1rem>download</span> Przyjęte</a>
        <a href="/serwis?status=w_naprawie" class="sv-filter-pill ''' + ('active' if filtr=='w_naprawie' else '') + '''"><span class=material-symbols-outlined style=font-size:1rem>build</span> W naprawie</a>
        <a href="/serwis?status=naprawiony" class="sv-filter-pill ''' + ('active' if filtr=='naprawiony' else '') + '''"><span class=material-symbols-outlined style=font-size:1rem>check_circle</span> Naprawione</a>
        <a href="/serwis?status=zwrocony" class="sv-filter-pill ''' + ('active' if filtr=='zwrocony' else '') + '''"><span class=material-symbols-outlined style=font-size:1rem>sync</span> Zwrócone</a>
        <a href="/serwis?status=zlomowany" class="sv-filter-pill ''' + ('active' if filtr=='zlomowany' else '') + '''"><span class=material-symbols-outlined style=font-size:1rem>delete</span> Złomowane</a>
    </div>

    <!-- ═══ List ═══ -->
    '''

    if not items:
        html += '''
        <div class="sv-empty">
            <div class="sv-empty-watermark">EMPTY_VOID</div>
            <div class="sv-empty-emoji">🥳</div>
            <div class="sv-empty-text">Brak produktów w serwisie</div>
            <button class="sv-btn-scan" onclick="window.location.href='/magazyn'">Scan New Ticket</button>
        </div>
        '''
    else:
        html += '<div class="sv-list">'
        status_border = {
            'przyjety': 'sv-item-cyan',
            'w_naprawie': 'sv-item-pink',
            'naprawiony': 'sv-item-lime',
            'zwrocony': 'sv-item-muted',
            'zlomowany': 'sv-item-red',
        }
        badge_class = {
            'przyjety': 'sv-badge-cyan',
            'w_naprawie': 'sv-badge-pink',
            'naprawiony': 'sv-badge-lime',
            'zwrocony': 'sv-badge-muted',
            'zlomowany': 'sv-badge-red',
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
                    dni = f' · {dni_val} dni'
                except:
                    pass

            border_cls = status_border.get(item['status'], '')
            badge_cls = badge_class.get(item['status'], 'sv-badge-muted')

            html += f'''
            <div class="sv-item {border_cls}">
                <img src="{item['zdjecie_url'] or '/static/placeholder.png'}" class="sv-item-img" onerror="this.src='/static/placeholder.png'">
                <div style="flex:1;min-width:0">
                    <span class="sv-syslog">SYS_LOG #{item['id']}</span>
                    <div class="sv-item-name">
                        <a href="/magazyn/produkt/{kod}">{(item['produkt_nazwa'] or '?')[:50]}</a>
                    </div>
                    <div class="sv-item-meta">{kod} · {item['dostawca'] or ''}{dni}</div>
                    <div class="sv-item-fault"><span class=material-symbols-outlined style=font-size:1rem>bolt</span> {item['opis_usterki'] or 'Brak opisu usterki'}</div>
                </div>
                <div style="text-align:center;min-width:55px">
                    <div class="sv-item-qty">{item['ilosc_szt']} szt</div>
                    {f'<div class="sv-item-cost">{item["koszt_naprawy"]:.0f} zł</div>' if item['koszt_naprawy'] else ''}
                </div>
                <div style="min-width:100px;text-align:center">
                    <span class="sv-badge {badge_cls}">{s_label}</span>
                </div>
                <div style="display:flex;gap:6px;flex-shrink:0" class="sv-item-actions">
            '''

            # Akcje zależne od statusu
            if item['status'] == 'przyjety':
                html += f'''
                    <button onclick="aktualizujSerwis({item['id']}, 'w_naprawie')" class="sv-act sv-act-repair"><span class=material-symbols-outlined style=font-size:1rem>build</span> Naprawiaj</button>
                    <button onclick="zlomuj({item['id']})" class="sv-act sv-act-scrap"><span class=material-symbols-outlined style=font-size:1rem>delete</span></button>
                '''
            elif item['status'] == 'w_naprawie':
                html += f'''
                    <button onclick="zakonczNaprawe({item['id']})" class="sv-act sv-act-done"><span class=material-symbols-outlined style=font-size:1rem>check_circle</span> Naprawiony</button>
                    <button onclick="zlomuj({item['id']})" class="sv-act sv-act-scrap"><span class=material-symbols-outlined style=font-size:1rem>delete</span></button>
                '''
            elif item['status'] == 'naprawiony':
                html += f'''
                    <button onclick="zwrocDoMagazynu({item['id']})" class="sv-act sv-act-return"><span class=material-symbols-outlined style=font-size:1rem>sync</span> Zwróć do mag.</button>
                '''

            html += '''
                </div>
            </div>
            '''

        html += '</div>'

    html += '</div>'  # sv-wrap

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
