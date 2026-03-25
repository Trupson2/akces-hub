"""Replace insights section in home.html with cyberpunk design."""

with open('templates/home.html', 'r', encoding='utf-8') as f:
    content = f.read()

old_start = '{% if insights %}\n<div class="font-display" style="font-weight:700;font-size:0.72rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--text-muted);margin-bottom:14px">'
idx = content.find(old_start)
if idx == -1:
    print("Start not found")
    exit(1)

block_start = idx
block_end = 36947 + len('{% endif %}')  # line 540

old_block = content[block_start:block_end]
print(f"Found block: {len(old_block)} chars")

new_block = """{% if insights %}
<!-- INSIGHTS — Cyberpunk data panels -->
<div style="display:flex;align-items:center;gap:6px;margin-bottom:14px">
    <span style="width:2px;height:14px;background:var(--neon-primary);border-radius:1px"></span>
    <span class="font-display" style="font-weight:700;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.1em;color:var(--neon-primary)">Insights</span>
</div>

<!-- NAJSZYBCIEJ SCHODZĄCE -->
<div style="margin-bottom:20px">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;padding:0 4px">
        <span class="font-display" style="font-weight:700;font-size:0.7rem;letter-spacing:0.15em;color:var(--text-muted);text-transform:uppercase">Najszybciej schodzące</span>
        <span style="font-size:0.6rem;font-weight:700;color:var(--neon-primary);display:flex;align-items:center;gap:4px">LIVE <span style="width:5px;height:5px;border-radius:50%;background:var(--neon-primary);display:inline-block;animation:neonPulse 2s infinite"></span></span>
    </div>
    {% if insights.top_sellers %}
    <div style="display:flex;flex-direction:column;gap:8px">
    {% for p in insights.top_sellers[:3] %}
    <a href="/magazyn/produkt/MAG-{{ '%05d'|format(p.id) }}" style="display:flex;align-items:center;gap:12px;padding:12px 14px;background:var(--bg-card,rgba(19,19,28,0.6));backdrop-filter:blur(12px);border-left:2px solid {% if loop.first %}var(--neon-primary){% else %}rgba(143,245,255,0.15){% endif %};text-decoration:none;color:inherit">
        <div style="width:48px;height:48px;background:rgba(255,255,255,0.03);border-radius:6px;overflow:hidden;flex-shrink:0">
            <img src="{{ p.zdjecie_url or '/static/placeholder.png' }}" style="width:100%;height:100%;object-fit:cover" onerror="this.src='/static/placeholder.png'" loading="lazy">
        </div>
        <div style="flex:1;min-width:0">
            <div class="font-display" style="font-size:0.82rem;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ p.nazwa[:40] }}</div>
            <div style="font-size:0.6rem;color:var(--text-muted);margin-top:3px">{{ p.kategoria or '' }}</div>
        </div>
        <div style="text-align:right">
            <div class="font-display" style="font-size:1.2rem;font-weight:900;color:var(--neon-primary);text-shadow:0 0 10px rgba(143,245,255,0.3)">{{ p.sprzedano_szt }}x</div>
            <div style="font-size:0.55rem;font-weight:700;color:var(--text-muted)">30 DNI</div>
        </div>
    </a>
    {% endfor %}
    </div>
    {% else %}
    <div style="color:var(--text-muted);font-size:0.8rem;padding:10px 14px">Brak danych</div>
    {% endif %}
</div>

<!-- KOŃCZY SIĘ — DOKUP! -->
<div style="margin-bottom:20px">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;padding:0 4px">
        <span class="font-display" style="font-weight:700;font-size:0.7rem;letter-spacing:0.15em;color:var(--neon-secondary);text-transform:uppercase">Kończy się — dokup!</span>
        <span class="material-symbols-outlined" style="font-size:0.9rem;color:var(--neon-secondary)">priority_high</span>
    </div>
    {% if insights.low_stock %}
    <div style="display:flex;flex-direction:column;gap:6px">
    {% for p in insights.low_stock[:4] %}
    <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 14px;background:var(--bg-card,rgba(19,19,28,0.6));border-left:{% if loop.first %}4{% else %}2{% endif %}px solid {% if loop.first %}var(--neon-secondary){% else %}rgba(255,107,155,0.2){% endif %}">
        <div>
            <div class="font-display" style="font-weight:700;font-size:0.85rem">{{ p.nazwa[:35] }}</div>
            <div style="display:flex;align-items:center;gap:8px;margin-top:4px">
                <span style="font-size:1rem;font-weight:900;color:var(--neon-secondary)">{{ p.stan }} szt</span>
            </div>
        </div>
        <a href="/magazyn/produkt/MAG-{{ '%05d'|format(p.id) }}" style="padding:6px 14px;{% if loop.first %}background:var(--neon-secondary);color:#fff{% else %}background:rgba(255,107,155,0.08);border:1px solid rgba(255,107,155,0.3);color:var(--neon-secondary){% endif %};font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:0.6rem;text-decoration:none;letter-spacing:0.1em">DOKUP</a>
    </div>
    {% endfor %}
    </div>
    {% else %}
    <div style="color:var(--text-muted);font-size:0.8rem;padding:10px 14px">Wszystko w normie</div>
    {% endif %}
</div>

<!-- NAJLEPSZE KATEGORIE — bar chart -->
<div style="background:var(--bg-card,rgba(19,19,28,0.6));backdrop-filter:blur(12px);padding:20px;margin-bottom:20px">
    <div class="font-display" style="font-weight:700;font-size:0.7rem;letter-spacing:0.15em;color:var(--text-muted);text-transform:uppercase;margin-bottom:18px">Najlepsze kategorie</div>
    {% if insights.best_categories %}
    <div style="display:flex;flex-direction:column;gap:18px">
    {% for c in insights.best_categories[:4] %}
    <div>
        <div style="display:flex;justify-content:space-between;align-items:end;margin-bottom:6px">
            <span class="font-display" style="font-weight:700;font-size:0.82rem;color:{% if loop.first %}var(--neon-primary){% else %}var(--text-muted){% endif %};text-transform:uppercase">{{ c.kategoria }}</span>
            <span style="font-weight:800;font-size:0.85rem">{{ c.przychod|int }} zł</span>
        </div>
        <div style="height:4px;width:100%;background:rgba(255,255,255,0.04);overflow:hidden">
            <div style="height:100%;background:{% if loop.first %}var(--neon-primary);box-shadow:0 0 8px rgba(143,245,255,0.6){% elif loop.index == 2 %}rgba(143,245,255,0.4){% else %}rgba(143,245,255,0.15){% endif %};width:{{ (c.przychod / insights.best_categories[0].przychod * 100)|int if insights.best_categories[0].przychod else 0 }}%"></div>
        </div>
    </div>
    {% endfor %}
    </div>
    {% else %}
    <div style="color:var(--text-muted);font-size:0.8rem;padding:10px 0">Za mało danych</div>
    {% endif %}
</div>

<!-- LEŻAKI — ROZWAŻ PRZECENĘ -->
<div style="margin-bottom:24px">
    <div style="display:flex;align-items:center;gap:6px;margin-bottom:10px;padding:0 4px">
        <span class="material-symbols-outlined" style="font-size:0.9rem;color:var(--neon-tertiary)">hourglass_empty</span>
        <span class="font-display" style="font-weight:700;font-size:0.7rem;letter-spacing:0.15em;color:var(--neon-tertiary);text-transform:uppercase">Leżaki — rozważ przecenę</span>
    </div>
    {% if insights.stale %}
    <div style="display:flex;flex-direction:column;gap:2px">
    {% for p in insights.stale[:5] %}
    <a href="/magazyn/produkt/MAG-{{ '%05d'|format(p.id) }}" style="display:flex;align-items:center;justify-content:space-between;padding:12px 14px;background:{% if loop.odd %}var(--bg-card,rgba(19,19,28,0.6)){% else %}rgba(19,19,28,0.3){% endif %};text-decoration:none;color:inherit">
        <div style="display:flex;align-items:center;gap:10px">
            <div style="width:3px;height:24px;background:rgba(190,238,0,{% if p.dni_w_magazynie > 90 %}0.5{% else %}0.15{% endif %})"></div>
            <div>
                <div class="font-display" style="font-size:0.82rem;font-weight:500">{{ p.nazwa[:35] }}</div>
                <div style="display:flex;align-items:center;gap:8px;margin-top:2px">
                    <span style="font-size:0.55rem;font-weight:700;color:var(--text-muted);letter-spacing:0.1em;text-transform:uppercase" class="dostawca-name">{{ p.dostawca or '' }}</span>
                    <span style="font-size:0.55rem;font-weight:900;color:var(--neon-tertiary);background:rgba(190,238,0,0.08);padding:1px 6px">{{ p.dni_w_magazynie }} DNI</span>
                </div>
            </div>
        </div>
        <div style="text-align:right">
            <div style="font-weight:700;font-size:0.85rem;color:var(--red)">{{ p.dni_w_magazynie }} dni</div>
            {% if p.koszt_szt %}<div style="font-size:0.55rem;color:var(--text-muted)">~{{ p.koszt_szt|int }} zł/szt</div>{% endif %}
        </div>
    </a>
    {% endfor %}
    </div>
    {% else %}
    <div style="color:var(--text-muted);font-size:0.8rem;padding:10px 14px">Brak leżaków</div>
    {% endif %}
</div>
{% endif %}"""

content = content[:block_start] + new_block + content[block_end:]

with open('templates/home.html', 'w', encoding='utf-8') as f:
    f.write(content)

print(f"OK - replaced {len(old_block)} -> {len(new_block)} chars")
