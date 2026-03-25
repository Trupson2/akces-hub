"""
Onboarding Wizard — 3-krokowy kreator konfiguracji po akceptacji EULA.
Kroki: 1) Allegro API  2) Backup path  3) Ustawienia podstawowe
"""
import os
from flask import Blueprint, request, redirect, render_template_string, flash
from modules.database import get_config, set_config

onboarding_bp = Blueprint('onboarding', __name__)


def is_onboarding_completed():
    """Sprawdza czy onboarding zostal ukonczony"""
    return get_config('onboarding_completed', '0') == '1'


ONBOARDING_TEMPLATE = '''<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kreator konfiguracji - Akces Hub</title>
<style>
:root {
    --bg: #0a0a0f;
    --bg-card: #12121a;
    --bg-tertiary: #1e1e2e;
    --border: #2a2a3a;
    --text: #ffffff;
    --text-secondary: #b0bec5;
    --text-muted: #78909c;
    --accent: #6366f1;
    --accent2: #8b5cf6;
    --accent3: #a855f7;
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
    align-items: flex-start;
    justify-content: center;
    padding: 40px 20px;
    overflow-y: auto;
}
.onboarding-container {
    width: 100%;
    max-width: 800px;
    position: relative;
}
.onboarding-header {
    text-align: center;
    margin-bottom: 32px;
}
.onboarding-header h1 {
    font-size: 1.8rem;
    font-weight: 700;
    background: linear-gradient(135deg, var(--accent), var(--accent2), var(--accent3));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 6px;
}
.onboarding-header p {
    color: var(--text-muted);
    font-size: 0.9rem;
}

/* Progress bar */
.progress-bar-wrap {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 28px;
    padding: 0 10px;
}
.progress-step {
    flex: 1;
    height: 4px;
    border-radius: 4px;
    background: var(--border);
    transition: background 0.5s ease;
}
.progress-step.active {
    background: linear-gradient(90deg, var(--accent), var(--accent2));
}
.progress-step.done {
    background: var(--green);
}
.progress-label {
    font-size: 0.75rem;
    color: var(--text-muted);
    white-space: nowrap;
}

/* Steps container */
.steps-viewport {
    overflow: visible;
    position: relative;
}
.steps-track {
    display: block;
}
.step {
    width: 100%;
    display: none;
    padding: 0 4px;
}
.step.active {
    display: block;
}
.step-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 24px 24px;
}
.step-icon {
    font-size: 2.4rem;
    text-align: center;
    margin-bottom: 10px;
}
.step-title {
    font-size: 1.15rem;
    font-weight: 700;
    text-align: center;
    margin-bottom: 6px;
}
.step-desc {
    font-size: 0.82rem;
    color: var(--text-muted);
    text-align: center;
    margin-bottom: 24px;
}

/* Form elements */
.form-group {
    margin-bottom: 18px;
}
.form-group label {
    display: block;
    font-size: 0.82rem;
    font-weight: 600;
    color: var(--text-secondary);
    margin-bottom: 6px;
}
.form-group input, .form-group select {
    width: 100%;
    padding: 12px 16px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text);
    font-size: 0.92rem;
    outline: none;
    transition: border-color 0.2s;
}
.form-group input:focus, .form-group select:focus {
    border-color: var(--accent);
}
.form-group input::placeholder {
    color: var(--text-muted);
}
.form-group select option {
    background: var(--bg-card);
    color: var(--text);
}
.form-hint {
    font-size: 0.72rem;
    color: var(--text-muted);
    margin-top: 4px;
}

/* Buttons */
.step-actions {
    display: flex;
    gap: 12px;
    margin-top: 24px;
}
.btn-skip {
    flex: 1;
    padding: 12px;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text-muted);
    font-size: 0.9rem;
    cursor: pointer;
    transition: all 0.2s;
}
.btn-skip:hover {
    border-color: var(--text-muted);
    color: var(--text-secondary);
}
.btn-next {
    flex: 2;
    padding: 12px;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    border: none;
    border-radius: 10px;
    color: #fff;
    font-size: 0.9rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
}
.btn-next:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 15px rgba(99,102,241,0.3);
}
.btn-finish {
    width: 100%;
    padding: 14px;
    background: linear-gradient(135deg, var(--green), #16a34a);
    border: none;
    border-radius: 10px;
    color: #fff;
    font-size: 1rem;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.2s;
    margin-top: 24px;
}
.btn-finish:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 15px rgba(34,197,94,0.3);
}

/* Animated bg dots */
.bg-dots {
    position: fixed;
    inset: 0;
    z-index: -1;
    overflow: hidden;
}
.bg-dots span {
    position: absolute;
    width: 3px;
    height: 3px;
    background: rgba(99,102,241,0.15);
    border-radius: 50%;
    animation: float 8s infinite ease-in-out;
}
@keyframes float {
    0%, 100% { transform: translateY(0); opacity: 0.3; }
    50% { transform: translateY(-30px); opacity: 0.7; }
}
</style>
</head>
<body>

<div class="bg-dots" id="bgDots"></div>

<div class="onboarding-container">
    <div class="onboarding-header">
        <h1>Witaj w systemie!</h1>
        <p>{{ brand_name }} &mdash; skonfiguruj podstawowe ustawienia</p>
    </div>

    <div class="progress-bar-wrap">
        <span class="progress-label" id="progLabel">1 / 3</span>
        <div class="progress-step active" id="prog1"></div>
        <div class="progress-step" id="prog2"></div>
        <div class="progress-step" id="prog3"></div>
    </div>

    <div class="steps-viewport">
        <div class="steps-track" id="stepsTrack">

            <!-- STEP 1: Allegro API -->
            <div class="step active">
                <div class="step-card">
                    <div class="step-icon"><span class=material-symbols-outlined>shopping_cart</span></div>
                    <div class="step-title">Podpiecie API Allegro</div>
                    <div class="step-desc">Polacz konto Allegro aby synchronizowac zamowienia i oferty</div>
                    <div class="form-group">
                        <label>Client ID</label>
                        <input type="text" id="allegro_client_id" placeholder="Wklej Client ID z Allegro Developer" value="{{ allegro_client_id }}">
                    </div>
                    <div class="form-group">
                        <label>Client Secret</label>
                        <input type="password" id="allegro_client_secret" placeholder="Wklej Client Secret" value="{{ allegro_client_secret }}">
                        <div class="form-hint">Znajdziesz w panelu developer.allegro.pl &rarr; Moje aplikacje</div>
                    </div>
                    <div class="step-actions">
                        <button class="btn-skip" onclick="goStep(2)">Pomin</button>
                        <button class="btn-next" onclick="saveStep(1)">Dalej &rarr;</button>
                    </div>
                </div>
            </div>

            <!-- STEP 2: Backup path -->
            <div class="step">
                <div class="step-card">
                    <div class="step-icon">&#128190;</div>
                    <div class="step-title">Sciezka backupu</div>
                    <div class="step-desc">Wskazaz folder na kopie zapasowe bazy danych</div>
                    <div class="form-group">
                        <label>Katalog backupu</label>
                        <input type="text" id="backup_path" placeholder="/home/pi/akces-hub/backups" value="{{ backup_path }}">
                        <div class="form-hint">Domyslnie: {{ default_backup_path }}</div>
                    </div>
                    <div class="step-actions">
                        <button class="btn-skip" onclick="goStep(3)">Pomin</button>
                        <button class="btn-next" onclick="saveStep(2)">Dalej &rarr;</button>
                    </div>
                </div>
            </div>

            <!-- STEP 3: Ustawienia -->
            <div class="step">
                <div class="step-card">
                    <div class="step-icon">&#9881;&#65039;</div>
                    <div class="step-title">Podstawowe ustawienia</div>
                    <div class="step-desc">Waluta, magazyn i nazwa marki</div>
                    <div class="form-group">
                        <label>Waluta</label>
                        <select id="currency">
                            <option value="PLN" {{ 'selected' if currency == 'PLN' else '' }}>PLN &mdash; zloty polski</option>
                            <option value="EUR" {{ 'selected' if currency == 'EUR' else '' }}>EUR &mdash; euro</option>
                            <option value="USD" {{ 'selected' if currency == 'USD' else '' }}>USD &mdash; dolar amerykanski</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Domyslna nazwa magazynu</label>
                        <input type="text" id="default_warehouse" placeholder="np. Magazyn Glowny" value="{{ default_warehouse }}">
                    </div>
                    <div class="form-group">
                        <label>Nazwa marki / firmy</label>
                        <input type="text" id="brand_name" placeholder="np. AKCES HUB" value="{{ brand_name }}">
                        <div class="form-hint">Wyswietlana w naglowku i na dokumentach</div>
                    </div>
                    <button class="btn-finish" onclick="saveStep(3)">Zakoncz konfiguracje &#10003;</button>
                </div>
            </div>

        </div>
    </div>
</div>

<script>
let currentStep = 1;

function goStep(n) {
    currentStep = n;
    // Show/hide steps
    var steps = document.querySelectorAll('.step');
    steps.forEach(function(s, idx) {
        s.classList.toggle('active', idx === n - 1);
    });
    document.getElementById('progLabel').textContent = n + ' / 3';
    for (let i = 1; i <= 3; i++) {
        const el = document.getElementById('prog' + i);
        el.className = 'progress-step';
        if (i < n) el.classList.add('done');
        else if (i === n) el.classList.add('active');
    }
}

function saveStep(step) {
    let data = {};
    if (step === 1) {
        data.allegro_client_id = document.getElementById('allegro_client_id').value.trim();
        data.allegro_client_secret = document.getElementById('allegro_client_secret').value.trim();
    } else if (step === 2) {
        data.backup_path = document.getElementById('backup_path').value.trim();
    } else if (step === 3) {
        data.currency = document.getElementById('currency').value;
        data.default_warehouse = document.getElementById('default_warehouse').value.trim();
        data.brand_name = document.getElementById('brand_name').value.trim();
    }
    data.step = step;

    fetch('/onboarding', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    }).then(r => r.json()).then(d => {
        if (d.ok) {
            if (step === 3) {
                window.location.href = '/';
            } else {
                goStep(step + 1);
            }
        }
    }).catch(() => {
        if (step < 3) goStep(step + 1);
        else window.location.href = '/';
    });
}

// Generate background dots
(function(){
    const container = document.getElementById('bgDots');
    for (let i = 0; i < 30; i++) {
        const dot = document.createElement('span');
        dot.style.left = Math.random() * 100 + '%';
        dot.style.top = Math.random() * 100 + '%';
        dot.style.animationDelay = (Math.random() * 8) + 's';
        dot.style.animationDuration = (6 + Math.random() * 6) + 's';
        container.appendChild(dot);
    }
})();
</script>
</body>
</html>'''


@onboarding_bp.route('/onboarding', methods=['GET'])
def onboarding_page():
    """Wyswietla wizard onboardingu — 3 kroki"""
    if is_onboarding_completed():
        return redirect('/')

    # Detect default backup path
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_backup = os.path.join(base, 'backups')
    if os.path.exists('/home/pi'):
        default_backup = '/home/pi/akces-hub/backups'

    return render_template_string(ONBOARDING_TEMPLATE,
        brand_name=get_config('brand_name', 'AKCES HUB'),
        allegro_client_id=get_config('allegro_client_id', ''),
        allegro_client_secret=get_config('allegro_client_secret', ''),
        backup_path=get_config('backup_path', default_backup),
        default_backup_path=default_backup,
        currency=get_config('currency', 'PLN'),
        default_warehouse=get_config('default_warehouse', ''),
        brand_name_val=get_config('brand_name', 'AKCES HUB'),
    )


@onboarding_bp.route('/onboarding', methods=['POST'])
def onboarding_save():
    """Zapisuje dane z danego kroku onboardingu"""
    from flask import jsonify
    data = request.get_json(silent=True) or {}
    step = data.get('step', 0)

    if step == 1:
        cid = data.get('allegro_client_id', '').strip()
        csecret = data.get('allegro_client_secret', '').strip()
        if cid:
            set_config('allegro_client_id', cid)
        if csecret:
            set_config('allegro_client_secret', csecret)

    elif step == 2:
        bp = data.get('backup_path', '').strip()
        if bp:
            set_config('backup_path', bp)

    elif step == 3:
        currency = data.get('currency', 'PLN').strip()
        warehouse = data.get('default_warehouse', '').strip()
        brand = data.get('brand_name', '').strip()
        if currency:
            set_config('currency', currency)
        if warehouse:
            set_config('default_warehouse', warehouse)
        if brand:
            set_config('brand_name', brand)
            # Clear cached brand name
            try:
                from modules.database import clear_config_cache
                clear_config_cache()
            except ImportError:
                pass

        # Final step — mark onboarding as completed
        set_config('onboarding_completed', '1')

    return jsonify({'ok': True, 'step': step})
