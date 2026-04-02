"""
EULA (End User License Agreement) module - ekran akceptacji regulaminu
"""
from flask import Blueprint, request, redirect, session, render_template_string, render_template
from modules.database import get_config, set_config
from flask_wtf.csrf import generate_csrf

eula_bp = Blueprint('eula', __name__)


EULA_TEMPLATE = '''<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Regulamin - Akces Hub</title>
<style>
:root {
    --bg: #0a0a0f;
    --bg-card: #12121a;
    --border: #2a2a3a;
    --text: #ffffff;
    --text-muted: #78909c;
    --accent: #6366f1;
    --accent2: #8b5cf6;
    --green: #22c55e;
    --radius: 16px;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
}
.eula-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.7);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 9999;
    padding: 20px;
}
.scroll-hint {
    text-align: center;
    font-size: 0.75rem;
    color: var(--accent);
    margin-bottom: 12px;
    opacity: 1;
    transition: opacity 0.3s;
}
.scroll-hint.hidden { opacity: 0; }
.eula-container {
    max-width: 700px;
    width: 100%;
}
.eula-header {
    text-align: center;
    margin-bottom: 30px;
}
.eula-logo {
    font-size: 2.5rem;
    margin-bottom: 10px;
}
.eula-brand {
    font-size: 1.4rem;
    font-weight: 700;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.eula-subtitle {
    color: var(--text-muted);
    font-size: 0.85rem;
    margin-top: 6px;
}
.eula-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 28px;
}
.eula-title {
    font-size: 1.1rem;
    font-weight: 700;
    margin-bottom: 16px;
    text-align: center;
}
.eula-scroll {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
    max-height: 350px;
    overflow-y: auto;
    font-size: 0.82rem;
    line-height: 1.7;
    color: var(--text-muted);
    margin-bottom: 20px;
}
.eula-scroll::-webkit-scrollbar { width: 6px; }
.eula-scroll::-webkit-scrollbar-track { background: var(--bg); border-radius: 3px; }
.eula-scroll::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
.eula-scroll h3 {
    color: var(--text);
    font-size: 0.9rem;
    margin: 16px 0 8px 0;
}
.eula-scroll h3:first-child { margin-top: 0; }
.eula-scroll p { margin-bottom: 8px; }
.eula-scroll ul { margin: 8px 0 8px 20px; }
.eula-scroll li { margin-bottom: 4px; }
.eula-checkbox {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 16px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    margin-bottom: 16px;
    cursor: pointer;
    transition: border-color 0.2s;
}
.eula-checkbox:hover { border-color: var(--accent); }
.eula-checkbox input[type="checkbox"] {
    width: 20px;
    height: 20px;
    accent-color: var(--green);
    cursor: pointer;
    margin-top: 2px;
    flex-shrink: 0;
}
.eula-checkbox-text {
    font-size: 0.85rem;
    line-height: 1.5;
}
.eula-btn {
    width: 100%;
    padding: 16px;
    border: none;
    border-radius: 10px;
    font-size: 1rem;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.2s;
    color: #fff;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
}
.eula-btn:disabled {
    opacity: 0.3;
    cursor: not-allowed;
    filter: grayscale(0.5);
}
.eula-btn:not(:disabled):hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 20px rgba(99, 102, 241, 0.4);
}
</style>
</head>
<body>
<div class="eula-overlay" id="eulaOverlay">
<div class="eula-container">
    <div class="eula-header">
        <div class="eula-logo">&#128736;</div>
        <div class="eula-brand">{{ brand_name }}</div>
        <div class="eula-subtitle">Regulamin oprogramowania</div>
    </div>

    <div class="eula-card">
        <div class="eula-title">Regulamin Oprogramowania</div>

        <div class="eula-scroll" id="eulaScroll">
            <h3>1. Postanowienia ogolne</h3>
            <p>Niniejszy regulamin okresla zasady korzystania z oprogramowania Akces Hub (dalej: "Oprogramowanie"). Korzystanie z Oprogramowania oznacza akceptacje ponizszych warunkow.</p>

            <h3>2. Licencja i zakres uzytkowania</h3>
            <p>Oprogramowanie jest udostepniane na podstawie licencji, ktora uprawnia do:</p>
            <ul>
                <li>Korzystania z Oprogramowania do celow zwiazanych z prowadzeniem dzialalnosci gospodarczej</li>
                <li>Zarzadzania magazynem, sprzedaza, paletami zwrotow i integracjami z platformami e-commerce</li>
                <li>Generowania raportow i statystyk sprzedazy</li>
            </ul>
            <p>Uzytkownik nie ma prawa do kopiowania, modyfikowania, rozpowszechniania ani dekompilacji Oprogramowania bez pisemnej zgody tworcy.</p>

            <h3>3. Ochrona danych osobowych (RODO)</h3>
            <p>Oprogramowanie przetwarza dane osobowe klientow (kupujacych) w zakresie niezbednym do realizacji zamowien i prowadzenia ksiegowosci. Uzytkownik, jako administrator danych, zobowiazuje sie do:</p>
            <ul>
                <li>Przestrzegania przepisow RODO (Rozporzadzenie 2016/679)</li>
                <li>Korzystania z wbudowanych narzedzi anonimizacji danych</li>
                <li>Ustawienia odpowiedniego okresu retencji danych</li>
            </ul>

            <h3>4. Wylaczenie odpowiedzialnosci</h3>
            <p><strong>Tworca nie ponosi odpowiedzialnosci za bledy w wyliczeniach finansowych,</strong> w tym lecz nie wylacznie: obliczenia zysku, kosztow, marzy, prowizji, wartosci magazynu i statystyk sprzedazy.</p>
            <p>Oprogramowanie jest narzedziem wspierajacym, a ostateczna odpowiedzialnosc za poprawnosc danych finansowych i ksiegowych spoczywa na uzytkowniku.</p>

            <h3>5. Dostepnosc i wsparcie</h3>
            <p>Tworca doklada staran, aby Oprogramowanie dzialalo poprawnie, jednak nie gwarantuje jego nieprzerwanej dostepnosci. Aktualizacje i poprawki sa dostarczane wedlug uznania tworcy.</p>

            <h3>6. Integracje z serwisami zewnetrznymi</h3>
            <p>Oprogramowanie integruje sie z serwisami takimi jak Allegro, OLX, Vinted, Telegram i innymi. Tworca nie ponosi odpowiedzialnosci za zmiany w API tych serwisow ani za ich dostepnosc.</p>

            <h3>7. Postanowienia koncowe</h3>
            <p>Tworca zastrzega sobie prawo do zmiany niniejszego regulaminu. O istotnych zmianach uzytkownik zostanie poinformowany przy kolejnym uruchomieniu Oprogramowania.</p>
        </div>

        <div class="scroll-hint" id="scrollHint">▼ Przescrolluj regulamin do konca, aby kontynuowac ▼</div>

        <form method="POST" action="/eula">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <label class="eula-checkbox" style="opacity:0.4;pointer-events:none" id="eulaLabel">
                <input type="checkbox" id="eulaCheck" disabled onchange="document.getElementById('eulaSubmit').disabled = !this.checked">
                <span class="eula-checkbox-text">
                    Akceptuje warunki licencji, w tym wylaczenie odpowiedzialnosci tworcy za bledy w wyliczeniach finansowych.
                </span>
            </label>
            <button type="submit" id="eulaSubmit" class="eula-btn" disabled>Przejdz do aplikacji</button>
        </form>
    </div>
</div>
</div>

<script>
// Block ESC key
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') e.preventDefault();
});

// Detect scroll to bottom
var scrollBox = document.getElementById('eulaScroll');
var eulaCheck = document.getElementById('eulaCheck');
var eulaLabel = document.getElementById('eulaLabel');
var scrollHint = document.getElementById('scrollHint');

scrollBox.addEventListener('scroll', function() {
    var atBottom = scrollBox.scrollTop + scrollBox.clientHeight >= scrollBox.scrollHeight - 20;
    if (atBottom) {
        eulaCheck.disabled = false;
        eulaLabel.style.opacity = '1';
        eulaLabel.style.pointerEvents = 'auto';
        scrollHint.classList.add('hidden');
    }
});

// Block click outside overlay
document.getElementById('eulaOverlay').addEventListener('click', function(e) {
    if (e.target === this) e.stopPropagation();
});
</script>
</body>
</html>
'''


@eula_bp.route('/eula', methods=['GET'])
def eula_page():
    """Wyswietla strone EULA"""
    brand_name = get_config('brand_name', 'AKCES HUB')
    return render_template('eula.html', brand_name=brand_name)


@eula_bp.route('/eula', methods=['POST'])
def eula_accept():
    """Akceptacja EULA"""
    set_config('eula_accepted', '1')
    return redirect('/dashboard')


def is_eula_accepted():
    """Sprawdza czy EULA zostalo zaakceptowane"""
    return get_config('eula_accepted', '') == '1'
