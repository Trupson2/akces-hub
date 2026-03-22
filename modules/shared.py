"""
Współdzielone zasoby — CSS, stałe i funkcje używane przez wiele modułów.
Import: from modules.shared import CSS, auto_kategoryzuj, KATEGORIE_DISPLAY
(Unika circular import z app.py)
"""
import re as _re_kat

CSS = '''
<style>
:root {
    --bg-primary: #0a0a0f;
    --bg-secondary: #12121a;
    --bg-tertiary: #1e1e2e;
    --border-color: #2a2a3a;
    --text-primary: #ffffff;
    --text-secondary: #b0bec5;
    --text-muted: #78909c;
    --accent-blue: #3b82f6;
    --accent-green: #22c55e;
    --accent-yellow: #eab308;
    --accent-red: #ef4444;
    --accent-purple: #8b5cf6;
    --accent-orange: #ff5a00;
    --nav-bg: #0a0a0f;
}
[data-theme="light"] {
    --bg-primary: #f8fafc; --bg-secondary: #ffffff; --bg-tertiary: #f1f5f9;
    --border-color: #e2e8f0; --text-primary: #1e293b; --text-secondary: #475569;
    --text-muted: #94a3b8; --accent-blue: #2563eb; --accent-green: #16a34a;
    --accent-yellow: #ca8a04; --accent-red: #dc2626; --accent-purple: #7c3aed;
    --accent-orange: #ea580c; --nav-bg: #ffffff;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg-primary);color:var(--text-primary);min-height:100vh;transition:background 0.3s,color 0.3s}
button,a,.btn,.card,.quick-btn,.module,.tool-card,.list-item,[onclick]{-webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent;outline:none}
button:active,a:active,.btn:active,[onclick]:active{outline:none}
body.kiosk,body.kiosk *{cursor:none!important}
.container{max-width:1600px;margin:0 auto;padding:20px;padding-bottom:90px}
.header{text-align:center;padding:25px 0;border-bottom:1px solid var(--border-color);margin-bottom:25px}
.header h1{font-size:1.8rem;background:linear-gradient(135deg,var(--accent-blue),var(--accent-purple));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.header small{color:var(--text-muted);font-size:0.85rem}
.theme-toggle{position:fixed;top:15px;right:15px;z-index:200;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:50%;width:44px;height:44px;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:1.3rem;transition:all 0.3s}
.theme-toggle:hover{transform:scale(1.1);border-color:var(--accent-blue)}
.card{background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:16px;padding:24px;margin-bottom:18px;transition:all 0.2s}
.card:hover{border-color:var(--accent-blue)}
.card-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.card-title{font-weight:600;font-size:1.05rem}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
.stat{background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:14px;padding:16px;text-align:center;transition:all 0.2s}
.stat:hover{border-color:var(--accent-blue)}
.stat-value{font-size:1.6rem;font-weight:700;color:var(--accent-blue)}
.stat-value.green{color:var(--accent-green)}
.stat-value.yellow{color:var(--accent-yellow)}
.stat-label{font-size:0.8rem;color:var(--text-muted);text-transform:uppercase;margin-top:5px}
.today-stats{background:linear-gradient(135deg,rgba(34,197,94,0.1),rgba(16,185,129,0.1));border:1px solid rgba(34,197,94,0.3);border-radius:16px;padding:20px;margin-bottom:20px}
.today-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:15px}
.today-title{color:var(--accent-green);font-weight:600;font-size:1.15rem}
.today-date{color:var(--text-muted);font-size:0.85rem}
.today-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;text-align:center}
.today-value{font-size:2rem;font-weight:700;color:var(--accent-green)}
.today-label{font-size:0.8rem;color:var(--text-muted)}
.quick-actions{display:grid;grid-template-columns:repeat(6,1fr);gap:16px;margin-bottom:24px}
.quick-btn{background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:14px;padding:20px 15px;text-align:center;color:var(--text-primary);text-decoration:none;transition:all 0.2s}
.quick-btn:hover{border-color:var(--accent-blue);transform:translateY(-2px)}
.quick-btn .icon{font-size:1.8rem;margin-bottom:10px}
.quick-btn .label{font-size:0.85rem;color:var(--text-secondary)}
.quick-btn.active{border-color:var(--accent-green);background:rgba(34,197,94,0.1)}
.quick-btn.alert{border-color:var(--accent-red);background:rgba(239,68,68,0.1)}
.modules-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px;margin-bottom:24px}
.module{background:linear-gradient(135deg,var(--bg-tertiary),var(--bg-secondary));border:1px solid var(--border-color);border-radius:16px;padding:20px;margin-bottom:0;text-decoration:none;color:var(--text-primary);display:block;transition:all 0.2s}
.module:hover{border-color:var(--accent-blue);transform:translateY(-3px)}
.module.purple{background:linear-gradient(135deg,rgba(139,92,246,0.2),rgba(88,28,135,0.2));border-color:rgba(139,92,246,0.3)}
.module.blue{background:linear-gradient(135deg,rgba(59,130,246,0.2),rgba(37,99,235,0.2));border-color:rgba(59,130,246,0.3)}
.module.orange{background:linear-gradient(135deg,rgba(255,90,0,0.2),rgba(200,70,0,0.2));border-color:rgba(255,90,0,0.3)}
.module-header{display:flex;align-items:center;gap:14px;margin-bottom:12px}
.module-icon{font-size:2.4rem}
.module-title{font-weight:700;font-size:1.2rem}
.module-desc{font-size:0.9rem;color:var(--text-secondary)}
.module-stats{display:flex;gap:12px;margin-top:14px;flex-wrap:wrap}
.module-stat{background:rgba(0,0,0,0.2);padding:8px 14px;border-radius:8px;font-size:0.85rem}
.module-stat strong{color:var(--accent-green)}
.btn{display:block;width:100%;padding:14px 24px;font-size:1rem;font-weight:600;text-align:center;text-decoration:none;border:none;border-radius:12px;cursor:pointer;margin-bottom:14px;color:#fff;transition:all 0.2s}
.btn-primary{background:var(--accent-blue)}.btn-primary:hover{background:#2563eb;transform:translateY(-1px)}
.btn-success{background:var(--accent-green)}.btn-success:hover{background:#16a34a}
.btn-purple{background:linear-gradient(135deg,var(--accent-purple),#7c3aed)}
.btn-secondary{background:var(--bg-tertiary);border:1px solid var(--border-color);color:var(--text-primary)}
.btn-danger{background:var(--accent-red)}.btn-warning{background:var(--accent-yellow);color:#000}
.btn-sm{padding:10px 18px;font-size:0.9rem;width:auto;display:inline-block}
.tools-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px}
.tool-card{background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:14px;padding:18px;text-decoration:none;color:var(--text-primary);transition:all 0.2s}
.tool-card:hover{border-color:var(--accent-blue);transform:translateY(-2px)}
.tool-icon{font-size:2rem;margin-bottom:10px}.tool-name{font-weight:600;font-size:0.95rem}
.tool-desc{font-size:0.75rem;color:var(--text-muted);margin-top:5px}
.list-item{display:flex;align-items:center;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:12px;padding:16px 20px;margin-bottom:12px;text-decoration:none;color:var(--text-primary);transition:all 0.2s}
.list-item:hover{border-color:var(--accent-blue)}
.list-item img{width:52px;height:52px;object-fit:contain;background:#fff;border-radius:10px;margin-right:14px}
.list-item-info{flex:1;min-width:0}.list-item-title{font-weight:600;font-size:0.95rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.list-item-meta{font-size:0.8rem;color:var(--text-muted)}.list-item-right{text-align:right;margin-left:12px}
.list-item-value{font-weight:700;color:var(--accent-blue)}.list-item-sub{font-size:0.75rem;color:var(--text-muted)}
.activity-item{display:flex;align-items:center;gap:14px;padding:14px;background:var(--bg-secondary);border-radius:12px;margin-bottom:10px}
.activity-dot{width:10px;height:10px;border-radius:50%}
.activity-dot.green{background:var(--accent-green)}.activity-dot.yellow{background:var(--accent-yellow)}.activity-dot.red{background:var(--accent-red)}
.activity-content{flex:1}.activity-msg{font-size:0.95rem}.activity-time{font-size:0.75rem;color:var(--text-muted)}
.form-group{margin-bottom:18px}.form-group label{display:block;font-size:0.85rem;color:var(--text-secondary);margin-bottom:8px;font-weight:500}
.form-control{width:100%;padding:14px;background:var(--bg-primary);border:1px solid var(--border-color);border-radius:10px;color:var(--text-primary);font-size:1rem;transition:border-color 0.2s}
.form-control:focus{outline:none;border-color:var(--accent-blue)}.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.alert{padding:14px 18px;border-radius:12px;margin-bottom:18px;font-size:0.95rem}
.alert-success{background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.3);color:var(--accent-green)}
.alert-warning{background:rgba(234,179,8,0.15);border:1px solid rgba(234,179,8,0.3);color:var(--accent-yellow)}
.alert-error{background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);color:var(--accent-red)}
.status-bar{display:flex;align-items:center;justify-content:space-between;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:12px;padding:14px 18px;margin-bottom:18px}
.status-bar.online{border-color:rgba(34,197,94,0.5);background:rgba(34,197,94,0.1)}
.status-bar.offline{border-color:rgba(239,68,68,0.5);background:rgba(239,68,68,0.1)}
.status-indicator{display:flex;align-items:center;gap:12px}
.status-dot{width:12px;height:12px;border-radius:50%;background:var(--text-muted)}
.status-dot.online{background:var(--accent-green);animation:pulse 2s infinite}
.status-dot.offline{background:var(--accent-red)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.5}}
.section-title{color:var(--accent-blue);font-weight:600;font-size:0.95rem;margin:25px 0 15px;display:flex;align-items:center;gap:10px}
.calc-result{background:var(--bg-primary);border-radius:12px;padding:18px;margin-top:18px}
.calc-row{display:flex;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--border-color)}
.calc-row:last-child{border:none}.calc-label{color:var(--text-secondary)}.calc-value{font-weight:700}
.calc-value.green{color:var(--accent-green)}.calc-value.red{color:var(--accent-red)}.calc-value.big{font-size:1.6rem}
.calc-highlight{border-top:2px solid var(--accent-green);padding-top:18px;margin-top:12px}
.sugestia{background:var(--bg-tertiary);border-radius:12px;padding:18px;text-align:center;margin-top:18px}
.sugestia-value{font-size:2.2rem;font-weight:700;color:var(--accent-yellow)}
.opis-box{background:var(--bg-tertiary);border-radius:12px;padding:18px;white-space:pre-wrap;font-size:0.95rem;line-height:1.7;max-height:280px;overflow-y:auto;margin:18px 0}
.toggle-row{display:flex;align-items:center;justify-content:space-between;padding:14px;background:var(--bg-primary);border-radius:12px;margin-bottom:10px}
.toggle-label{font-size:0.95rem}
.toggle{width:48px;height:26px;background:var(--bg-tertiary);border-radius:13px;padding:3px;cursor:pointer;transition:all 0.2s}
.toggle.on{background:var(--accent-blue)}.toggle-knob{width:20px;height:20px;background:#fff;border-radius:50%;transition:all 0.2s}
.toggle.on .toggle-knob{transform:translateX(22px)}
.log-item{display:flex;align-items:center;gap:12px;padding:12px;background:var(--bg-primary);border-radius:10px;margin-bottom:8px}
.log-icon{width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:1.1rem}
.log-icon.sale{background:rgba(34,197,94,0.2)}.log-icon.alert{background:rgba(234,179,8,0.2)}.log-icon.report{background:rgba(59,130,246,0.2)}
.log-content{flex:1;min-width:0}.log-msg{font-size:0.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.log-time{font-size:0.75rem;color:var(--text-muted)}.log-status{font-size:0.75rem;color:var(--accent-green)}
.back{display:block;text-align:center;color:var(--text-muted);text-decoration:none;padding:18px;font-size:0.95rem;transition:color 0.2s}
.back:hover{color:var(--text-primary)}
.bottom-nav{position:fixed;bottom:0;left:0;right:0;background:var(--nav-bg);border-top:1px solid var(--border-color);padding:10px 0;z-index:100}
.bottom-nav-inner{max-width:1600px;margin:0 auto;display:flex;justify-content:space-around}
.nav-item{text-align:center;color:var(--text-muted);text-decoration:none;padding:10px 20px;border-radius:12px;transition:all 0.2s}
.nav-item:hover,.nav-item.active{color:var(--accent-blue);background:rgba(59,130,246,0.1)}
.nav-icon{font-size:1.5rem;margin-bottom:4px}.nav-label{font-size:0.75rem}
.badge{display:inline-block;padding:4px 10px;border-radius:10px;font-size:0.75rem;font-weight:600}
.badge-success{background:rgba(34,197,94,0.2);color:var(--accent-green)}
.badge-warning{background:rgba(234,179,8,0.2);color:var(--accent-yellow)}
.badge-error{background:rgba(239,68,68,0.2);color:var(--accent-red)}
.version-badge{position:fixed;bottom:75px;right:15px;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:8px;padding:4px 10px;font-size:0.7rem;color:var(--text-muted);z-index:99}
@media (min-width:1600px){.container{max-width:1600px;padding:30px}.modules-grid{grid-template-columns:repeat(2,1fr)}.tools-grid{grid-template-columns:repeat(4,1fr)}.stats{grid-template-columns:repeat(4,1fr)}.quick-actions{grid-template-columns:repeat(6,1fr);gap:20px}}
@media (min-width:1200px) and (max-width:1599px){.container{max-width:1400px;padding:25px}.modules-grid{grid-template-columns:repeat(2,1fr)}.tools-grid{grid-template-columns:repeat(4,1fr)}}
@media (max-width:1199px){.container{max-width:100%;padding:20px}.modules-grid{grid-template-columns:repeat(2,1fr)}.tools-grid{grid-template-columns:repeat(3,1fr)}}
@media (max-width:900px){.container{max-width:100%;padding:15px}.modules-grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(3,1fr)}.quick-actions{grid-template-columns:repeat(5,1fr)}.tools-grid{grid-template-columns:repeat(2,1fr)}}
@media (max-width:768px){.container{padding:12px}.stats{grid-template-columns:repeat(2,1fr)}.quick-actions{grid-template-columns:repeat(4,1fr)}.today-value{font-size:1.6rem}.stat-value{font-size:1.4rem}.module-title{font-size:1.05rem}.module-icon{font-size:2rem}.form-row{grid-template-columns:1fr}.theme-toggle{width:40px;height:40px;font-size:1.1rem}}
@media (max-width:480px){.container{padding:10px}.header h1{font-size:1.4rem}.header{padding:18px 0}.quick-actions{grid-template-columns:repeat(3,1fr);gap:8px}.quick-btn{padding:12px 8px}.quick-btn .icon{font-size:1.3rem}.quick-btn .label{font-size:0.65rem}.stats{grid-template-columns:repeat(2,1fr);gap:8px}.stat{padding:12px}.stat-value{font-size:1.3rem}.today-grid{gap:8px}.today-value{font-size:1.4rem}.today-label{font-size:0.7rem}.module{padding:16px}.module-stats{gap:8px}.module-stat{padding:6px 10px;font-size:0.75rem}.tools-grid{grid-template-columns:1fr 1fr}.btn{padding:13px;font-size:0.95rem}.bottom-nav-inner{justify-content:space-between;padding:0 4px}.nav-item{padding:6px 6px}.nav-icon{font-size:1.4rem}.nav-label{font-size:0.7rem}.theme-toggle{top:10px;right:10px;width:36px;height:36px;font-size:1rem}}
@media (max-width:360px){.quick-actions{grid-template-columns:repeat(3,1fr)}.stats{grid-template-columns:1fr 1fr}.today-grid{grid-template-columns:1fr 1fr 1fr}.tools-grid{grid-template-columns:1fr}}
</style>
'''


# ============================================================
# auto_kategoryzuj + KATEGORIE_DISPLAY
# Przeniesione z app.py żeby uniknąć circular import
# ============================================================

def auto_kategoryzuj(nazwa):
    """Automatycznie przypisz kategorię na podstawie nazwy produktu.

    WAŻNE: Kolejność sprawdzania ma znaczenie! Najpierw sprawdzamy specyficzne
    wielowyrazowe frazy (żeby uniknąć false positives z krótkich słów),
    potem bardziej ogólne kategorie.
    """
    nazwa_lower = (nazwa or '').lower()
    if not nazwa_lower.strip():
        return 'inne'

    # Helper: sprawdź czy słowo występuje jako oddzielne słowo (nie jako część innego słowa)
    def _word_match(words, text):
        """Sprawdź czy którekolwiek słowo z listy występuje w tekście"""
        return any(w in text for w in words)

    def _whole_word(word, text):
        """Sprawdź czy słowo występuje jako oddzielne (nie-substring)"""
        return bool(_re_kat.search(r'(?<![a-ząćęłńóśźż])' + _re_kat.escape(word) + r'(?![a-ząćęłńóśźż])', text))

    # ============================================================
    # RUNDA 1: BARDZO SPECYFICZNE FRAZY (multi-word, priorytetowe)
    # ============================================================

    if _word_match(['ekran ze stojak', 'ekran '], nazwa_lower) and \
       _word_match(['fotografii', 'fotograficzn', 'tło', 'backdrop', 'streaming'], nazwa_lower):
        return 'foto_video'

    if _word_match(['ekran projekcyjn', 'projektor ', 'projector', 'ekran przenośn',
                     'ekran kinowy', 'projection screen'], nazwa_lower):
        return 'rtv'

    if _word_match(['bieżnia', 'treadmill', 'walking pad', 'walkingpad', 'bieżni'], nazwa_lower):
        return 'sport'

    if _word_match(['dron ', 'dron,', 'drone', 'quadcopter', 'kwadrokopter', 'dron gps',
                     'mini dron', 'fpv', 'dji mini', 'dji mavic', 'dji air'], nazwa_lower):
        return 'foto_video'

    if _word_match(['pokrowce samochod', 'pokrowiec samochod', 'pokrowce na siedzen',
                     'pokrowce na fotele samochod', 'mata samochod', 'dywaniki samochod'], nazwa_lower):
        return 'motoryzacja'

    if _word_match(['wózek transportow', 'wózek ogrodow', 'wózek składan', 'taczka',
                     'wózek platformow', 'wózek magazynow'], nazwa_lower):
        return 'outdoor'

    if _word_match(['okap', 'hood kitchen', 'pochłaniacz', 'wyciąg kuchenn'], nazwa_lower):
        return 'agd_duze'

    if _word_match(['odkurzacz', 'vacuum', 'odkurzać'], nazwa_lower):
        return 'agd_male'

    if _word_match(['petkit', 'poidło fontanna', 'fontanna dla kot', 'fontanna dla ps',
                     'poidło dla kot', 'poidło dla ps', 'poidło automatyczne',
                     'drapak', 'legowisko dla', 'kuweta', 'karuzela dla kot',
                     'strzyżeni zwierząt', 'strzyżenia psów', 'maszynka do strzyż',
                     'oneisall', 'grooming', 'trymer dla ps', 'trymer dla zwierz'], nazwa_lower):
        return 'zwierzeta'

    if _word_match(['fotel biurow', 'krzesło biurow', 'office chair', 'fotel obrotow',
                     'fotel ergonomiczn', 'fotel gamingowy'], nazwa_lower):
        return 'biuro'

    if _word_match(['ramka na zdjęci', 'ramka cyfrowa', 'digital frame', 'photo frame',
                     'ramka wifi'], nazwa_lower):
        return 'foto_video'

    if _word_match(['walizk', 'suitcase', 'luggage', 'torba podróżn', 'zestaw walizek',
                     'cabin max', 'bagaż podręczn', 'bagaż kabinow', 'torba kabinow'], nazwa_lower):
        return 'bagaz'

    if _word_match(['plecak podróżn', 'plecak kabinow', 'plecak turystyczn',
                     'travel backpack', 'cabin backpack'], nazwa_lower):
        return 'bagaz'

    if _word_match(['obciążnik', 'ankle weight', 'wrist weight', 'mankiety obciąż',
                     'obciążenie na kostk', 'obciążenie na nadgarst'], nazwa_lower):
        return 'sport'

    if _word_match(['biustonosz sportow', 'stanik sportow', 'sportowa podprask',
                     'legginsy sportow', 'getry sportow', 'spodenki sportow',
                     'koszulka sportow', 'odzież sportow', 'biegania', 'do jogi',
                     'biustonosz do biegania', 'legginsy damskie', 'getry damskie',
                     'sportowy biustonosz', 'sportowa bielizna'], nazwa_lower):
        return 'sport'

    if _word_match(['biustonosz', 'stanik', 'bielizna', 'majtki', 'bokserki', 'figi',
                     'kalesony', 'rajstopy', 'skarpetki', 'skarpety', 'underwear',
                     'lingerie', 'bra ', 'panties', 'socks'], nazwa_lower):
        return 'moda'

    if _word_match(['legginsy', 'leggings', 'getry', 'rajtuzy'], nazwa_lower):
        return 'moda'

    if _word_match(['hamulec ręczny usb', 'hamulec ręczny pc', 'logitech g27', 'logitech g29',
                     'logitech g920', 'thrustmaster', 'sim racing', 'symulat'], nazwa_lower):
        return 'gaming'

    if _word_match(['łóżko polowe', 'łóżko składane', 'leżak turystyczn', 'leżak składan',
                     'łóżko turystyczn', 'camp bed', 'cot bed'], nazwa_lower):
        return 'outdoor'

    if _word_match(['kojec dla dzieci', 'kojec dziecięc', 'kojec składan'], nazwa_lower):
        return 'niemowleta'

    if _word_match(['poduszka ortopedyczn', 'poduszka do siedzenia', 'poduszka memory',
                     'poduszka kość ogonow', 'poduszka lędźwiow', 'podkładka do siedzenia',
                     'poduszka z otworem'], nazwa_lower):
        return 'rehabilitacja'

    if _word_match(['shelly', 'sonoff', 'ściemniacz wifi', 'dimmer wifi', 'smart switch',
                     'inteligentne gniazdko', 'smart plug', 'zigbee', 'z-wave', 'home assistant',
                     'ściemniacz', 'smart dimmer', 'meross', 'homekit wifi', 'sterownik wifi',
                     'sterownik homekit'], nazwa_lower):
        return 'smart_home'

    if _word_match(['ezviz c', 'ezviz cb', 'kamera obrotowa', 'kamera bezprzewodow',
                     'kamera akumulator', 'menborn', 'kamera wifi'], nazwa_lower):
        return 'smart_home'

    if _word_match(['pokrowiec kierownicy', 'pokrowc na fotel', 'pokrowc na siedzen',
                     'pokrowc fotel', 'pokrowc siedzen', 'kierownicy skóra',
                     'pokrowiec na kierownic', 'pokrowców na fotel', 'pokrowców na siedzen',
                     'komplet pokrowców na fotel', 'pokrowców na siedz'], nazwa_lower):
        return 'motoryzacja'

    if _word_match(['sakwa rowerow', 'sakwy rowerow', 'kółka rowerow', 'kółka boczne',
                     'kółka do rower', 'sakwa na bagażnik'], nazwa_lower):
        return 'sport'

    if _word_match(['namiot kempingow', 'kempingow', 'namiot turystyczn', 'namiot kopułow',
                     'łóżko kempingow'], nazwa_lower):
        return 'outdoor'

    if _word_match(['paralety', 'push up', 'pushup', 'push-up', 'poręcze do ćwicz',
                     'drążk baletow', 'balet', 'drążek baletow', 'drążki baletow'], nazwa_lower):
        return 'sport'

    if _word_match(['mata do yog', 'mata do ćwicz', 'mata fitness', 'mata gym'], nazwa_lower):
        return 'sport'

    if _word_match(['prasa termotransfer', 'sublimacj', 'termotransfer', 'heat press',
                     'prasa do koszulek', 'prasa do kubków'], nazwa_lower):
        return 'hobby'

    if _word_match(['gałki do kuchenk', 'gałki do piekarnik', 'gałka do kuchenk',
                     'gałka do piekarnik'], nazwa_lower):
        return 'agd_duze'

    if _word_match(['podkładki perkusyjn', 'pad perkusyjn', 'pałki perkusyjn',
                     'drum pad', 'practice pad', 'zestaw perkusyjn'], nazwa_lower):
        return 'muzyka'

    if _word_match(['siłownik termoelektr', 'siłownik zaworu', 'rozdzielacz podłogow',
                     'termostat podłogow'], nazwa_lower):
        return 'klimatyzacja'

    if _word_match(['dozownik', 'dispenser'], nazwa_lower):
        return 'dom_ogrod'

    if _word_match(['chłodzeni', 'cooling', 'radiator', 'cooler cpu', 'wentylator cpu',
                     'pasta termiczna', 'thermal paste', 'heat sink'], nazwa_lower):
        return 'komputery'

    # ============================================================
    # RUNDA 2: GŁÓWNE KATEGORIE
    # ============================================================

    if _word_match(['wallbox', 'evse', 'ev charger', 'type 2', 'type2', 'type-2',
        'ccs', 'chademo', 'tesla', 'charging station', 'stacja ładowania', 'ładowarka samochod', 'ładowarka ev',
        'electric vehicle', 'elektromobil', 'green cell ev', 'juice booster', 'go-e', 'easee', 'zappi',
        'mennekes', 'j1772', 'nema', '11kw', '22kw', '7kw', '3.6kw', '32a', '16a'], nazwa_lower):
        if _whole_word('ev', nazwa_lower) or not _word_match(['ev'], nazwa_lower):
            return 'ev_ladowarki'

    if _word_match(['softbox', 'ring light', 'lampa pierścieniowa', 'pierścieniowa',
        'tło fotograficzne', 'tlo fotograficzne', 'backdrop', 'greenscreen', 'green screen', 'tło świąteczne',
        'gopro', 'insta360', 'dji', 'osmo', 'action cam', 'kamera sportowa', 'kamera hd', 'kamera 4k',
        'smallrig', 'stabilizator', 'steadycam', 'follow focus', 'neewer', 'godox',
        'mikrofon', 'microphone', 'lavalier', 'shotgun mic', 'rode', 'boya', 'fifine',
        'teleprompter', 'prompter', 'stojak na tło', 'statyw oświetleniowy', 'light stand', 'boom arm',
        'panel led', 'oświetlenie fotograficzne', 'oświetlenie studyjne', 'video light',
        'transmisja', 'streaming', 'capture card', 'elgato', 'cam link', 'webkamera', 'webcam',
        'emart', 'raleno', 'fgen', 'lywygg', 'julius studio', 'limo studio',
        'fotograficzn', 'fotografi', 'papier foto', 'canon selphy', 'selphy', 'kp-108',
        'objektiv', 'obiektyw', 'lens cap', 'osłona obiektyw', 'filtr uv', 'filtr nd',
        'aparat fotograficzn', 'fujifilm', 'nikon', 'canon eos', 'sony alpha',
        'do fotografii', 'do streamingu', 'foto ', 'wkład do drukark'], nazwa_lower):
        return 'foto_video'

    if _word_match(['filament', 'drukarka 3d', '3d printer', 'druk 3d', 'nozzle', 'dysza', 'hotend', 'extruder',
        'creality', 'ender 3', 'prusa', 'anycubic', 'elegoo', 'żywica uv', 'szpula', 'jayo'], nazwa_lower):
        return 'druk3d'

    if _word_match(['kamera ip', 'kamera wifi', 'kamera wlan', 'monitoring', 'cctv',
        'hikvision', 'dahua', 'reolink', 'imou', 'tapo', 'arlo', 'blink', 'wyze', 'eufy',
        'smart home', 'smarthome', 'inteligentny dom', 'czujnik ruchu', 'motion sensor',
        'wideodomofon', 'domofon', 'dzwonek wifi', 'ring doorbell',
        'niania elektroniczna', 'baby monitor', 'kamera bezprzewodowa', 'kamera zewnętrzna'], nazwa_lower):
        return 'smart_home'

    if _word_match(['samochod', 'samochód', 'obd', 'diagnosty',
        'opona', 'kamera cofania', 'cofania', 'backup camera', 'reversing',
        'dash cam', 'dashcam', 'rejestrator jazdy', 'wideorejestrator', 'parkowania', 'czujnik parkowania',
        'nawigacja gps', 'uchwyt samochodowy', 'ładowarka samochodowa', 'car charger',
        'camecho', 'viofo', 'nextbase', '70mai', 'dywaniki samochodow',
        'pokrowiec samochodow', 'pokrowce samochodow', 'fotelik samochodow'], nazwa_lower):
        return 'motoryzacja'

    if _word_match(['teleskop', 'telescope', 'lornetka', 'binoculars', 'mikroskop', 'microscope',
        'okular', 'eyepiece', 'luneta', 'monokular', 'kolimator', 'collimator', 'svbony', 'celestron', 'bresser',
        'powiększenie', 'zoom optyczny', 'pryzmat'], nazwa_lower):
        return 'optyka'

    if _word_match(['karma dla', 'pet food', 'obroża', 'smycz', 'leash', 'klatka dla',
        'akwarium', 'aquarium', 'terrarium', 'legowisko', 'kuweta', 'litter', 'drapak',
        'zabawka dla ps', 'zabawka dla kot', 'miska dla', 'poidło dla',
        'karma', 'transporter dla', 'szelki dla psa', 'smycz dla psa', 'dla zwierząt',
        'dla psa', 'dla kota', 'petkit', 'fontanna dla', 'poidło', 'miska', 'gryzak dla'], nazwa_lower):
        return 'zwierzeta'

    if _word_match(['inkubator', 'incubator', 'wylęg', 'kurnik',
        'hodowla', 'breeding', 'karmnik', 'nawóz', 'fertilizer',
        'nasiona', 'seeds', 'szklarnia', 'greenhouse', 'growbox', 'hydroponika'], nazwa_lower):
        return 'rolnictwo'

    if _word_match(['świąteczn', 'christmas', 'dekoracj', 'decoration', 'ozdoba', 'ornament',
        'girlanda', 'garland', 'lampki choinkowe', 'choinka', 'bożonarodzeni', 'halloween', 'wielkanoc',
        'balony', 'balloon', 'konfetti'], nazwa_lower):
        return 'dekoracje'

    if _word_match(['mikser', 'blender', 'toster', 'czajnik', 'kettle',
        'żelazko', 'suszarka do włos', 'golarki', 'shaver', 'depilator', 'maszynka do golenia',
        'robot kuchenny', 'ekspres do kawy', 'ekspres ciśnieniow',
        'frytkownica', 'air fryer', 'opiekacz', 'mikrofala', 'microwave',
        'robot sprzątający', 'roomba', 'roborock', 'parowar', 'steamer', 'wyciskarka', 'juicer',
        'gofrownica', 'waffle maker', 'jajecznica', 'sandwich maker',
        'krajalnica', 'slicer', 'maszynka do mięsa', 'meat grinder'], nazwa_lower):
        return 'agd_male'

    if _word_match(['lodówka', 'fridge', 'pralka', 'washing machine', 'zmywarka', 'dishwasher',
        'piekarnik', 'oven', 'kuchenka', 'cooker', 'klimatyzator', 'air condition', 'freezer', 'zamrażar',
        'suszarka do prania', 'tumble dryer', 'płyta indukcyjn', 'płyta ceramiczn',
        'okap kuchenn'], nazwa_lower):
        return 'agd_duze'

    if _word_match(['żarówka', 'bulb', 'oświetlenie', 'lighting', 'kinkiet', 'plafon',
        'żyrandol', 'chandelier', 'taśma led', 'halogen', 'świecznik', 'latarnia',
        'lampka nocna', 'lampka biurkowa', 'lampka led', 'lampa stojąca', 'lampa sufitowa',
        'lampa wisząca', 'lampa podłogowa', 'listwa led', 'neon led'], nazwa_lower):
        return 'oswietlenie'

    if _word_match(['garnek', 'patelnia', 'naczyn', 'sztućce', 'cutlery',
        'talerz', 'kubek', 'szklanka', 'termos', 'thermos', 'lunch box',
        'deska do krojenia', 'cutting board', 'nóż kuchenny', 'kitchen knife', 'sitko',
        'rondelek', 'wok', 'taca', 'pojemnik kuchenn', 'pojemnik na żywność',
        'szczypce kuchenn', 'otwieracz', 'korkociąg'], nazwa_lower):
        return 'kuchnia'

    if _word_match(['cement', 'beton', 'cegła', 'brick', 'fuga', 'grout',
        'farba ścienn', 'farba do ścian', 'pędzel malarski', 'wałek malarski', 'szpachla', 'tynk',
        'złączka hydraul', 'zawór hydraul', 'uszczelka', 'silikon', 'klej montażowy',
        'wiertło', 'kołek rozporowy', 'wkręt', 'śruba', 'gwoźdź'], nazwa_lower):
        return 'budowa'

    if _word_match(['laptop', 'notebook', 'komputer', 'computer',
        'klawiatura', 'myszka komputerow', 'drukarka', 'printer', 'skaner', 'scanner', 'ssd', 'hdd',
        'procesor', 'cpu', 'gpu', 'karta graficzna', 'płyta główna', 'motherboard',
        'pendrive', 'dysk zewnętrzny', 'zewnętrzny dysk',
        'zasilacz komputerow', 'obudowa komputerow', 'pamięć ram'], nazwa_lower):
        return 'komputery'
    if _whole_word('monitor', nazwa_lower) and not _word_match(['baby monitor', 'niania', 'selfie'], nazwa_lower):
        return 'komputery'
    if _whole_word('router', nazwa_lower) or _whole_word('modem', nazwa_lower):
        return 'komputery'

    if _word_match(['biurko', 'krzesło biurowe', 'office chair', 'segregator',
        'długopis', 'ołówek', 'zeszyt', 'kalendarz biurow', 'planner',
        'tablica sucho', 'whiteboard', 'niszczarka', 'shredder', 'laminat', 'laminator',
        'organizer biurow', 'szuflada biurow', 'teczka', 'bindownica'], nazwa_lower):
        return 'biuro'

    if _word_match(['hulajnoga', 'scooter', 'rolki', 'siłownia',
        'hantle', 'dumbbell', 'orbitrek', 'yoga', 'fitness',
        'stepper', 'kettlebell', 'gryf olimpijski', 'sztanga', 'ćwiczeni',
        'rakieta', 'racket', 'tenisow', 'badminton', 'ping pong',
        'trampolin', 'hula hoop', 'skakanka', 'ekspander', 'guma oporow',
        'ławka treningow', 'drążek do podciąg', 'trening', 'sportow',
        'obciążnik', 'gumy fitness', 'mata do ćwicz', 'mata do yoga',
        'rękawice boksersk', 'worek bokserski', 'rękawice treningowe'], nazwa_lower):
        return 'sport'
    if _whole_word('rower', nazwa_lower) or _whole_word('bike', nazwa_lower):
        return 'sport'

    if _word_match(['namiot turystyczn', 'namiot kampingow', 'śpiwór', 'sleeping bag', 'karimata',
        'latarka', 'flashlight', 'kompas', 'nóż survivalowy', 'survival', 'paracord',
        'hiking', 'trekking', 'karabińczyk', 'carabiner', 'hamak', 'hammock',
        'łóżko polowe', 'leżak', 'torba termoizolacyjn', 'cooler bag',
        'menażka', 'kuchenka turystyczn', 'palnik gazowy', 'czołówka'], nazwa_lower):
        return 'outdoor'

    if _word_match(['smartfon', 'smartphone', 'iphone', 'samsung galaxy', 'xiaomi', 'redmi',
        'huawei', 'oppo', 'realme', 'oneplus', 'google pixel', 'mobile phone', 'cell phone',
        'motorola', 'nokia', 'poco', 'honor', 'do telefonu', 'do iphone', 'do samsung',
        'monitor do selfie'], nazwa_lower):
        return 'telefony'
    if _whole_word('telefon', nazwa_lower):
        return 'telefony'

    if _word_match(['ładowarka', 'charger', 'kabel usb', 'kabel lightning', 'kabel type-c',
        'słuchawki', 'headphone', 'earbuds', 'earphone',
        'powerbank', 'power bank', 'adapter', 'przejściówka', 'hub usb', 'stacja dokująca',
        'etui na telefon', 'case ', 'szkło hartowane', 'folia ochronn',
        'statyw', 'tripod', 'gimbal', 'selfi', 'selfie stick',
        'czytnik kart', 'card reader', 'ugreen', 'anker', 'baseus'], nazwa_lower):
        return 'akcesoria'
    if _whole_word('bluetooth', nazwa_lower) and not _word_match(['głośnik', 'speaker', 'soundbar'], nazwa_lower):
        return 'akcesoria'

    if _word_match(['telewizor', 'soundbar', 'głośnik', 'speaker', 'kino domowe',
        'projektor', 'projector', 'odtwarzacz', 'amplituner', 'subwoofer',
        'blu-ray', 'chromecast', 'fire stick', 'apple tv', 'roku',
        'kabel hdmi', 'kabel audio', 'ekran projekcyjn', 'kolumna', 'wieża audio'], nazwa_lower):
        return 'rtv'
    if _whole_word('tv', nazwa_lower) or _whole_word('radio', nazwa_lower):
        return 'rtv'

    if _word_match(['playstation', 'ps4', 'ps5', 'xbox', 'nintendo', 'konsola',
        'gamepad', 'kontroler gier', 'joystick', 'gaming',
        'oculus', 'quest', 'pad perkusyjny',
        'kierownica do gier', 'racing wheel', 'flight stick', 'hotas',
        'logitech g', 'razer', 'steelseries', 'hyperx',
        'mysz gamingowa', 'klawiatura gamingowa', 'podkładka gamingowa',
        'fotel gamingowy', 'sim racing'], nazwa_lower):
        return 'gaming'
    if _whole_word('vr', nazwa_lower):
        return 'gaming'

    if _word_match(['wiertarka', 'drill', 'wkrętarka', 'screwdriver', 'szlifierka', 'grinder',
        'piła', 'młotek', 'hammer', 'zestaw narzędzi', 'tool kit', 'kompresor',
        'spawarka', 'welder', 'lutownica', 'multimetr', 'poziomica', 'obcęgi', 'pliers', 'szczypce',
        'imadło', 'imbus', 'torx', 'klucz nasadow', 'klucz płaski', 'klucz oczkowy'], nazwa_lower):
        return 'narzedzia'
    if _whole_word('klucz', nazwa_lower) and _word_match(['nasaw', 'płask', 'oczkow', 'nasad', 'zestaw'], nazwa_lower):
        return 'narzedzia'

    if _word_match(['meble', 'furniture', 'ogród', 'garden',
        'dywan', 'carpet', 'rolet', 'żaluzj',
        'sofa', 'kanapa', 'szafka', 'cabinet', 'regał',
        'kosiarka', 'mower', 'podkaszarka', 'wąż ogrodowy', 'grill ogrodowy', 'parasol ogrodow',
        'nawadnianie', 'doniczka', 'wieszak', 'organizer dom',
        'pojemnik', 'kosz na śmieci', 'suszarka na pranie', 'deska do prasowania',
        'lustro', 'zegar ścienny', 'ramka na zdjęcia'], nazwa_lower):
        return 'dom_ogrod'
    if _whole_word('krzesło', nazwa_lower) or _whole_word('stół', nazwa_lower):
        return 'dom_ogrod'

    if _word_match(['zabawka', 'toy', 'klocki', 'lego', 'lalka', 'doll', 'pluszak', 'gra planszowa',
        'puzzle', 'samochodzik', 'kolejka elektryczn', 'dziecięc', 'piaskownic',
        'fotelik dziecięc', 'car seat', 'rowerek dziecięc',
        'kredki', 'plastelina', 'zjeżdżalnia', 'huśtawka dziecięc', 'bujak'], nazwa_lower):
        return 'zabawki'
    if _word_match(['wózek dziecięc', 'wózek spacer'], nazwa_lower):
        return 'zabawki'

    if _word_match(['peruka', 'wig ', 'wigs', 'perücke', 'hair extension', 'doczepiany włos',
        'syntetyczn', 'synthetic hair', 'lace front', 'lace wig', 'cosplay wig',
        'barsdar', 'emmor'], nazwa_lower):
        return 'uroda'

    if _word_match(['manekin', 'mannequin', 'bust form', 'torso display', 'krawiecke',
        'głowa styropianow', 'głowa do peruk'], nazwa_lower):
        return 'moda'

    if _word_match(['zgrzewarka', 'sealer', 'vacuum sealer', 'próżniowa', 'pakowarka',
        'folia do zgrzewania', 'food saver'], nazwa_lower):
        return 'agd_male'

    if _word_match(['huśtawka ogrodn', 'huśtawka dorosł', 'huśtawka bujana', 'swing chair',
        'brama ogrodn', 'furtka', 'bramka ogrodn', 'siatka ogrodn',
        'huśtawka', 'swing', 'schaukel'], nazwa_lower):
        return 'dom_ogrod'

    if _word_match(['peg ', 'pegi', 'pegs', 'footpeg', 'foot peg', 'bmx peg',
        'uchwyt rowerow'], nazwa_lower):
        return 'sport'

    if _word_match(['buty', 'shoes', 'ubrani', 'koszul', 'shirt', 'spodni', 'pants',
        'sukienk', 'dress', 'kurtk', 'jacket', 'bluza', 'sweater', 'czapk',
        'torebk', 'damska', 'portfel', 'wallet', 'biżuteria', 'jewelry', 'okulary',
        'garnitur', 'kamizelk', 'szalik', 'rękawiczk', 'kapelusz',
        'sneakers', 'sandały', 'botki', 'kozaki', 'klapki', 'trampki',
        't-shirt', 'polo', 'jeansy', 'dżinsy'], nazwa_lower):
        return 'moda'
    if _whole_word('plecak', nazwa_lower):
        return 'moda'
    if _whole_word('zegarek', nazwa_lower) or _whole_word('watch', nazwa_lower):
        return 'moda'
    if _whole_word('pasek', nazwa_lower) and not _word_match(['pasek klinow', 'pasek rozrząd', 'pasek do zegarki'], nazwa_lower):
        return 'moda'

    if _word_match(['masażer', 'massager', 'ciśnieniomierz', 'termometr medyczn', 'inhalator',
        'szczoteczka elektr', 'szczoteczka sonic', 'suszarka do włosów',
        'prostownica', 'lokówka', 'trymer do włos', 'trymer do brod',
        'waga łazienkowa', 'pulsoksymetr', 'glukometr', 'aparat słuchowy',
        'pistolet do masażu', 'masaż perkusyjn', 'masażer perkusyjn',
        'depilator', 'ipl', 'laser do depilacji',
        'irygator', 'waterpik', 'inhalator'], nazwa_lower):
        return 'zdrowie'

    if _word_match(['rampa', 'ramp', 'podjazd', 'inwalidzk', 'wheelchair', 'wózek inwalidzki',
        'balkonik', 'walker', 'chodzik', 'orteza', 'orthosis',
        'rehabilitacj', 'rehabilitation', 'ortopedyczn', 'orthopedic', 'temblak',
        'pas ortopedyczny', 'gorset ortopedyczn', 'kołnierz ortopedyczny',
        'materac przeciwodleżynowy', 'podpórka', 'kule inwalidzk',
        'stabilizator kolana', 'stabilizator nadgarst', 'opaska ortopedyczn'], nazwa_lower):
        return 'rehabilitacja'

    if _word_match(['kołdra', 'duvet', 'quilt', 'pościel', 'bedding', 'prześcieradło', 'sheet',
        'ręcznik', 'towel', 'szlafrok', 'bathrobe',
        'obrus', 'tablecloth', 'serwetka', 'narzuta', 'bedspread',
        'poszewka', 'pillowcase', 'firanka',
        'ściereczka', 'ścierka', 'mop'], nazwa_lower):
        return 'tekstylia'
    if _word_match(['poduszka', 'pillow', 'cushion'], nazwa_lower) and \
       not _word_match(['ortopedyczn', 'memory', 'do siedzenia', 'z otworem', 'kość ogonow'], nazwa_lower):
        return 'tekstylia'

    if _word_match(['szampon', 'shampoo', 'mydło', 'soap', 'żel pod prysznic', 'shower gel',
        'balsam', 'lotion', 'perfum', 'dezodorant', 'deodorant', 'pasta do zębów', 'toothpaste',
        'proszek do prania', 'detergent', 'płyn do mycia', 'środek czystości',
        'krem do twarzy', 'krem nawilżając', 'peeling', 'serum', 'tonik',
        'lakier do paznokci', 'żel do paznokci', 'manicure', 'pedicure'], nazwa_lower):
        return 'kosmetyki'

    if _word_match(['książka', 'book', 'audiobook', 'ebook', 'e-book', 'komiks', 'comic',
        'czasopismo', 'poradnik', 'encyklopedia', 'słownik', 'dictionary',
        'vinyl', 'płyta cd', 'płyta dvd'], nazwa_lower):
        return 'ksiazki'

    if _word_match(['prezent', 'gift', 'upominek', 'voucher',
        'opakowanie prezentowe', 'gift box', 'wstążka', 'papier do pakowania', 'wrapping'], nazwa_lower):
        return 'prezenty'

    if _word_match(['sejf', 'kłódka', 'padlock', 'zamek do drzwi', 'zamek szyfrowy',
        'gaśnica', 'extinguisher', 'czujnik dymu', 'smoke detector', 'apteczka',
        'kamizelka odblaskowa', 'czujnik gazu', 'czujnik czadu', 'kamera bezpieczeństwa'], nazwa_lower):
        return 'bezpieczenstwo'

    if _word_match(['torba podróżna', 'travel bag',
        'kosmetyczka', 'organizer podróżny', 'nerka', 'saszetka',
        'plecak podróżny', 'torba sportowa', 'torba na ramię'], nazwa_lower):
        return 'bagaz'

    if _word_match(['barbell', 'weight plate', 'ławka treningowa', 'bench press',
        'drążek do podciąg', 'pull-up bar', 'atlas treningow', 'suwnica', 'power rack',
        'gumy oporowe', 'resistance band', 'piłka gimnastyczna', 'gym ball'], nazwa_lower):
        return 'silownia'

    if _word_match(['e-bike', 'ebike', 'elektryczny rower',
        'kask rowerowy', 'cycling helmet', 'siodełko rowerow', 'pedał rowerow',
        'lampka rowerowa', 'bike light', 'bagażnik rowerowy', 'bike rack'], nazwa_lower):
        return 'rowery'

    if _word_match(['makita', 'dewalt', 'milwaukee', 'metabo', 'hikoki', 'einhell',
        'akumulatorow', 'cordless', 'bezszczotkow', 'brushless'], nazwa_lower):
        return 'elektronarzedzia'
    if 'bosch' in nazwa_lower and _word_match(['wiertark', 'szlifierk', 'piła', 'wkrętar', 'frezar',
                                                 'professional', 'gsr', 'gws', 'gbh'], nazwa_lower):
        return 'elektronarzedzia'

    if _word_match(['maszyna do szycia', 'sewing machine', 'overlock', 'hafciarka', 'embroidery',
        'farby akrylowe', 'acrylic paint', 'sztaluga', 'easel', 'płótno malarskie', 'canvas',
        'dziewiar', 'knitting', 'szydełk', 'crochet', 'scrapbook', 'decoupage', 'modelarstwo', 'airbrush',
        'diamond painting', 'malowanie po numerach', 'zestaw do malowania',
        'pyrograf', 'wypalarka', 'piaskowanie'], nazwa_lower):
        return 'hobby'

    if _word_match(['niemowl', 'infant', 'noworod', 'newborn', 'łóżeczko dziecięc', 'kojec', 'playpen',
        'przewijak', 'sterilizator butelek', 'podgrzewacz do butelek', 'bottle warmer',
        'mata edukacyjna', 'karuzela nad łóżeczk', 'nosidełko', 'baby carrier',
        'smoczek', 'butelka dla niemowl', 'pieluch'], nazwa_lower):
        return 'niemowleta'

    if _word_match(['głośnik samochodowy', 'car speaker', 'subwoofer samochodowy', 'car subwoofer',
        'wzmacniacz samochodowy', 'car amplifier', 'radio samochodowe', 'car radio', 'android auto', 'carplay',
        'tweetery', 'zwrotnica', 'kondensator car audio'], nazwa_lower):
        return 'car_audio'

    if _word_match(['wentylator', 'oczyszczacz powietrza', 'air purifier', 'nawilżacz', 'humidifier',
        'osuszacz', 'dehumidifier', 'klimatyzator przenośny', 'portable ac', 'rekuperator', 'filtr hepa',
        'wentylacja', 'cyrkulat'], nazwa_lower):
        return 'klimatyzacja'

    if _word_match(['growbox', 'grow box', 'namiot uprawowy', 'grow tent', 'lampa led grow', 'grow light',
        'hydroponik', 'hydroponic', 'system nawadniania', 'ph metr', 'ec metr'], nazwa_lower):
        return 'hydroponika'

    if _word_match(['wędka', 'fishing rod', 'kołowrotek', 'żyłka wędkars', 'fishing line',
        'przynęta', 'bait', 'lure', 'podbierak', 'landing net', 'echosonda', 'fish finder',
        'łódź wędkarska'], nazwa_lower):
        return 'wedkarstwo'
    if _whole_word('spinning', nazwa_lower) and _word_match(['wędka', 'kołowrot', 'fishing'], nazwa_lower):
        return 'wedkarstwo'

    if _word_match(['waga laboratoryjna', 'lab scale', 'waga precyzyjna', 'precision scale', 'pipeta', 'pipette',
        'probówka', 'test tube', 'zlewka', 'beaker', 'kolba miarowa', 'mikroskop laboratoryjny',
        'wirówka', 'centrifuge'], nazwa_lower):
        return 'laboratorium'

    if _word_match(['namiot imprezowy', 'party tent', 'pawilon', 'gazebo', 'oświetlenie sceniczne', 'stage light',
        'maszyna do dymu', 'fog machine', 'laser sceniczny', 'kula disco', 'disco ball', 'nagłośnienie', 'pa system',
        'mikser dj', 'dj mixer', 'kontroler dj', 'dj controller'], nazwa_lower):
        return 'event'

    if _word_match(['cb radio', 'krótkofalówka', 'walkie talkie', 'pmr', 'radiotelefon', 'antena cb',
        'skaner radiowy', 'radio scanner', 'sdr', 'radio amatorskie', 'ham radio', 'baofeng', 'midland'], nazwa_lower):
        return 'cb_radio'

    # ============================================================
    # RUNDA 3: OSTATECZNE FALLBACKI
    # ============================================================

    if _whole_word('lampa', nazwa_lower) and not _word_match(['pierścieniow', 'studyjn', 'fotograficzn'], nazwa_lower):
        return 'oswietlenie'

    if _whole_word('grill', nazwa_lower):
        return 'dom_ogrod'

    if _word_match(['produkt amazon', 'produkt b0'], nazwa_lower):
        return 'inne'

    return 'inne'


KATEGORIE_DISPLAY = {
    'ev_ladowarki': '⚡ Ładowarki EV',
    'foto_video': '📸 Foto/Video',
    'druk3d': '🖨️ Druk 3D',
    'smart_home': '📹 Smart Home',
    'motoryzacja': '🚗 Motoryzacja',
    'optyka': '🔭 Optyka',
    'rolnictwo': '🐣 Rolnictwo',
    'dekoracje': '🎄 Dekoracje',
    'oswietlenie': '💡 Oświetlenie',
    'kuchnia': '🍳 Kuchnia',
    'budowa': '🛠️ Budowa',
    'biuro': '💼 Biuro',
    'outdoor': '🎒 Outdoor',
    'rehabilitacja': '♿ Rehabilitacja',
    'tekstylia': '🛏️ Tekstylia',
    'kosmetyki': '🧴 Kosmetyki',
    'ksiazki': '📚 Książki',
    'prezenty': '🎁 Prezenty',
    'bezpieczenstwo': '🔒 Bezpieczeństwo',
    'bagaz': '🧳 Bagaż',
    'silownia': '🏋️ Siłownia',
    'rowery': '🚴 Rowery',
    'hulajnogi': '🛴 Hulajnogi',
    'agd_male': '🔌 AGD małe',
    'agd_duze': '🏠 AGD duże',
    'komputery': '💻 Komputery/IT',
    'telefony': '📱 Telefony',
    'akcesoria': '🔋 Akcesoria',
    'rtv': '📺 RTV/Audio',
    'gaming': '🎮 Gaming',
    'narzedzia': '🔧 Narzędzia',
    'dom_ogrod': '🏡 Dom i ogród',
    'sport': '⚽ Sport/Fitness',
    'zabawki': '🧸 Zabawki',
    'moda': '👕 Moda',
    'zdrowie': '💊 Zdrowie/Uroda',
    'zwierzeta': '🐾 Zwierzęta',
    'muzyka': '🎸 Muzyka',
    'elektronarzedzia': '🧰 Elektronarzędzia',
    'hobby': '🎨 Hobby/Rękodzieło',
    'niemowleta': '🍼 Niemowlęta',
    'car_audio': '🔊 Car Audio',
    'klimatyzacja': '🌡️ Klimatyzacja',
    'hydroponika': '🪴 Hydroponika',
    'wedkarstwo': '🎣 Wędkarstwo',
    'laboratorium': '🔬 Laboratorium',
    'event': '🎪 Event/Imprezy',
    'cb_radio': '📡 CB/Radio',
    'elektronika': '📷 Elektronika',
    'inne': '📦 Inne'
}

# ── Klasa jakości (auto-detect z nazwy/stanu) ────────────────────
KLASA_JAKOSCI_MAP = {
    'A': '🟢 A',
    'A-': '🔵 A-',
    'B': '🟡 B',
    'C': '🟠 C',
    'D': '🔴 D',
}

def auto_klasa_jakosci(nazwa='', stan=''):
    """Auto-detect klasa jakości na podstawie nazwy produktu i stanu.
    Amazon returns grading: A=new, B=like new/open box, C=used, D=damaged
    """
    nazwa_lower = (nazwa or '').lower()
    stan_lower = (stan or '').lower()

    # Z nazwy — Amazon grading keywords
    if any(w in nazwa_lower for w in ['brand new', 'factory sealed', 'sealed', 'nowy fabryczny']):
        return 'A'
    if any(w in nazwa_lower for w in ['like new', 'open box', 'opened', 'jak nowy', 'otwarte opakowanie']):
        return 'A-'
    if any(w in nazwa_lower for w in ['good condition', 'dobry stan', 'lightly used', 'gently used']):
        return 'B'
    if any(w in nazwa_lower for w in ['fair condition', 'used', 'visible wear', 'używany', 'ślady użytkowania']):
        return 'C'
    if any(w in nazwa_lower for w in ['damaged', 'broken', 'defective', 'uszkodzony', 'niekompletny', 'parts only']):
        return 'D'

    # Z stanu produktu
    if stan_lower in ['nowy']:
        return 'A'
    if stan_lower in ['powystawowy', 'jak nowy']:
        return 'A-'
    if stan_lower in ['używany', 'odnowiony']:
        return 'B'
    if stan_lower in ['uszkodzony']:
        return 'D'

    return ''
