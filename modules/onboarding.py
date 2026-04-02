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
<html lang="pl" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kreator konfiguracji — Akces Hub</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700;800;900&family=Manrope:wght@400;500;600;700;800&display=swap">
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=swap" rel="stylesheet">
<style>
/* ── Stitch Design System — Onboarding ── */
:root {
    --bg: #0e0e10;
    --surface-low: #131315;
    --surface: #19191c;
    --surface-high: #1f1f22;
    --border: #48474a;
    --border-dim: rgba(72,71,74,0.25);
    --on-surface: #f9f5f8;
    --on-surface-variant: #adaaad;
    --muted: #767577;
    --primary: #8ff5ff;
    --secondary: #ff6b9b;
    --tertiary: #beee00;
    --font-display: 'Space Grotesk', 'Inter', sans-serif;
    --font-body: 'Manrope', 'Inter', system-ui, sans-serif;
    --radius: 16px;
    --radius-sm: 10px;
}
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: var(--font-body);
    background: var(--bg);
    color: var(--on-surface);
    min-height: 100vh;
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding: 40px 20px;
    overflow-y: auto;
}

/* Grid background */
body::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image:
        linear-gradient(rgba(143,245,255,0.03) 1px, transparent 1px),
        linear-gradient(90deg, rgba(143,245,255,0.03) 1px, transparent 1px);
    background-size: 40px 40px;
    pointer-events: none;
    z-index: 0;
}

/* Top glow */
body::after {
    content: '';
    position: fixed;
    top: -200px;
    left: 50%;
    transform: translateX(-50%);
    width: 800px;
    height: 500px;
    background: radial-gradient(ellipse at center, rgba(143,245,255,0.06) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
}

.material-symbols-outlined {
    font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24;
    font-size: 1.3rem;
    vertical-align: middle;
}

/* ── Container ── */
.onboarding-container {
    width: 100%;
    max-width: 640px;
    position: relative;
    z-index: 1;
}

/* ── Header ── */
.onboarding-header {
    text-align: center;
    margin-bottom: 40px;
}
.onboarding-logo {
    width: 56px;
    height: 56px;
    margin: 0 auto 20px;
    background: rgba(143,245,255,0.08);
    border: 1px solid rgba(143,245,255,0.15);
    border-radius: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.5rem;
    font-weight: 900;
    color: var(--primary);
    font-family: var(--font-display);
    box-shadow: 0 0 30px rgba(143,245,255,0.08);
}
.onboarding-header h1 {
    font-family: var(--font-display);
    font-size: 1.6rem;
    font-weight: 800;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--primary);
    margin-bottom: 8px;
    text-shadow: 0 0 30px rgba(143,245,255,0.25);
}
.onboarding-header p {
    color: var(--on-surface-variant);
    font-size: 0.88rem;
    font-weight: 500;
}

/* ── Progress stepper — diamond dots ── */
.progress-stepper {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0;
    margin-bottom: 36px;
    position: relative;
}
.progress-dot {
    width: 14px;
    height: 14px;
    transform: rotate(45deg);
    border: 2px solid var(--border);
    background: var(--surface);
    position: relative;
    z-index: 2;
    transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
    cursor: default;
}
.progress-dot.active {
    border-color: var(--primary);
    background: var(--primary);
    box-shadow: 0 0 12px rgba(143,245,255,0.4), 0 0 24px rgba(143,245,255,0.15);
}
.progress-dot.done {
    border-color: var(--tertiary);
    background: var(--tertiary);
    box-shadow: 0 0 8px rgba(190,238,0,0.3);
}
.progress-line {
    width: 80px;
    height: 2px;
    background: var(--border-dim);
    position: relative;
    z-index: 1;
    transition: background 0.4s;
}
.progress-line.done {
    background: linear-gradient(90deg, var(--tertiary), var(--primary));
}
.progress-labels {
    display: flex;
    justify-content: space-between;
    max-width: 320px;
    margin: -4px auto 32px;
    padding: 0 6px;
}
.progress-lbl {
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    color: var(--muted);
    font-weight: 600;
    transition: color 0.3s;
    text-align: center;
    width: 90px;
}
.progress-lbl.active {
    color: var(--primary);
}
.progress-lbl.done {
    color: var(--tertiary);
}

/* ── Step panels ── */
.step {
    display: none;
    animation: stepIn 0.35s ease;
}
.step.active {
    display: block;
}
@keyframes stepIn {
    from { opacity: 0; transform: translateY(12px); }
    to   { opacity: 1; transform: translateY(0); }
}

.step-card {
    background: var(--surface-low);
    border: 1px solid var(--border-dim);
    border-radius: var(--radius);
    padding: 32px 28px;
    backdrop-filter: blur(10px);
    position: relative;
    overflow: hidden;
}
/* Top accent line */
.step-card::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 2px;
    background: linear-gradient(90deg, var(--primary), var(--secondary), var(--tertiary));
    opacity: 0.6;
}

.step-icon-wrap {
    width: 52px;
    height: 52px;
    margin: 0 auto 16px;
    border-radius: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
}
.step-icon-wrap.cyan {
    background: rgba(143,245,255,0.08);
    border: 1px solid rgba(143,245,255,0.15);
}
.step-icon-wrap.pink {
    background: rgba(255,107,155,0.08);
    border: 1px solid rgba(255,107,155,0.15);
}
.step-icon-wrap.lime {
    background: rgba(190,238,0,0.08);
    border: 1px solid rgba(190,238,0,0.15);
}
.step-icon-wrap .material-symbols-outlined {
    font-size: 1.6rem;
}
.step-icon-wrap.cyan .material-symbols-outlined { color: var(--primary); }
.step-icon-wrap.pink .material-symbols-outlined { color: var(--secondary); }
.step-icon-wrap.lime .material-symbols-outlined { color: var(--tertiary); }

.step-title {
    font-family: var(--font-display);
    font-size: 1.1rem;
    font-weight: 700;
    text-align: center;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    margin-bottom: 6px;
    color: var(--on-surface);
}
.step-desc {
    font-size: 0.82rem;
    color: var(--on-surface-variant);
    text-align: center;
    margin-bottom: 28px;
    line-height: 1.5;
}

/* ── Form elements ── */
.form-group {
    margin-bottom: 20px;
}
.form-group label {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 0.72rem;
    font-weight: 700;
    color: var(--on-surface-variant);
    margin-bottom: 8px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}
.form-group label .material-symbols-outlined {
    font-size: 0.9rem;
    opacity: 0.6;
}
.form-group input,
.form-group select {
    width: 100%;
    padding: 12px 16px;
    background: var(--surface);
    border: 1px solid var(--border-dim);
    border-radius: var(--radius-sm);
    color: var(--on-surface);
    font-size: 0.88rem;
    font-family: var(--font-body);
    font-weight: 500;
    outline: none;
    transition: all 0.25s;
}
.form-group input:focus,
.form-group select:focus {
    border-color: rgba(143,245,255,0.4);
    box-shadow: 0 0 0 3px rgba(143,245,255,0.08), 0 0 20px rgba(143,245,255,0.05);
}
.form-group input::placeholder {
    color: var(--muted);
    font-weight: 400;
}
.form-group select option {
    background: var(--surface);
    color: var(--on-surface);
}
.form-hint {
    font-size: 0.7rem;
    color: var(--muted);
    margin-top: 6px;
    display: flex;
    align-items: center;
    gap: 4px;
}
.form-hint .material-symbols-outlined {
    font-size: 0.8rem;
}

/* ── Buttons ── */
.step-actions {
    display: flex;
    gap: 12px;
    margin-top: 28px;
}
.btn-skip {
    flex: 1;
    padding: 12px 16px;
    background: transparent;
    border: 1px solid var(--border-dim);
    border-radius: var(--radius-sm);
    color: var(--on-surface-variant);
    font-size: 0.85rem;
    font-weight: 600;
    font-family: var(--font-body);
    cursor: pointer;
    transition: all 0.25s;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
}
.btn-skip:hover {
    border-color: var(--border);
    color: var(--on-surface);
    background: rgba(255,255,255,0.03);
}
.btn-next {
    flex: 2;
    padding: 12px 16px;
    background: linear-gradient(135deg, rgba(143,245,255,0.15), rgba(143,245,255,0.05));
    border: 1px solid rgba(143,245,255,0.25);
    border-radius: var(--radius-sm);
    color: var(--primary);
    font-size: 0.85rem;
    font-weight: 700;
    font-family: var(--font-display);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    cursor: pointer;
    transition: all 0.25s;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
}
.btn-next:hover {
    background: linear-gradient(135deg, rgba(143,245,255,0.22), rgba(143,245,255,0.1));
    border-color: rgba(143,245,255,0.4);
    box-shadow: 0 0 20px rgba(143,245,255,0.12);
    transform: translateY(-1px);
}
.btn-next .material-symbols-outlined {
    font-size: 1.1rem;
    transition: transform 0.2s;
}
.btn-next:hover .material-symbols-outlined {
    transform: translateX(3px);
}
.btn-finish {
    width: 100%;
    padding: 14px 16px;
    background: linear-gradient(135deg, rgba(190,238,0,0.15), rgba(190,238,0,0.05));
    border: 1px solid rgba(190,238,0,0.3);
    border-radius: var(--radius-sm);
    color: var(--tertiary);
    font-size: 0.9rem;
    font-weight: 700;
    font-family: var(--font-display);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    cursor: pointer;
    transition: all 0.25s;
    margin-top: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
}
.btn-finish:hover {
    background: linear-gradient(135deg, rgba(190,238,0,0.25), rgba(190,238,0,0.12));
    border-color: rgba(190,238,0,0.5);
    box-shadow: 0 0 25px rgba(190,238,0,0.12);
    transform: translateY(-1px);
}

/* ── Test connection button ── */
.btn-test {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 14px;
    background: rgba(255,107,155,0.08);
    border: 1px solid rgba(255,107,155,0.2);
    border-radius: 8px;
    color: var(--secondary);
    font-size: 0.75rem;
    font-weight: 600;
    font-family: var(--font-body);
    cursor: pointer;
    transition: all 0.25s;
    margin-top: 4px;
}
.btn-test:hover {
    background: rgba(255,107,155,0.15);
    border-color: rgba(255,107,155,0.35);
    box-shadow: 0 0 12px rgba(255,107,155,0.1);
}
.btn-test .material-symbols-outlined {
    font-size: 0.95rem;
}
.btn-test.testing {
    opacity: 0.7;
    pointer-events: none;
}
.btn-test.success {
    background: rgba(190,238,0,0.1);
    border-color: rgba(190,238,0,0.3);
    color: var(--tertiary);
}
.btn-test.fail {
    background: rgba(239,68,68,0.1);
    border-color: rgba(239,68,68,0.3);
    color: #ef4444;
}

/* ── Toast ── */
.toast {
    position: fixed;
    top: 24px;
    right: 24px;
    background: var(--surface-high);
    border: 1px solid var(--border-dim);
    border-radius: var(--radius-sm);
    padding: 14px 20px;
    font-size: 0.82rem;
    font-weight: 600;
    color: var(--on-surface);
    display: flex;
    align-items: center;
    gap: 10px;
    z-index: 999;
    transform: translateX(120%);
    transition: transform 0.35s cubic-bezier(0.4, 0, 0.2, 1);
    box-shadow: 0 8px 30px rgba(0,0,0,0.4);
}
.toast.show {
    transform: translateX(0);
}
.toast.success { border-left: 3px solid var(--tertiary); }
.toast.error   { border-left: 3px solid #ef4444; }

/* ── Responsive ── */
@media (max-width: 640px) {
    body {
        padding: 20px 12px;
    }
    .onboarding-container {
        max-width: 100%;
    }
    .step-card {
        padding: 24px 18px;
    }
    .onboarding-header h1 {
        font-size: 1.3rem;
    }
    .progress-line {
        width: 50px;
    }
    .progress-labels {
        max-width: 260px;
    }
    .progress-lbl {
        font-size: 0.56rem;
        width: 70px;
    }
    .step-actions {
        flex-direction: column-reverse;
    }
    .btn-skip, .btn-next {
        flex: unset;
        width: 100%;
    }
}
</style>
</head>
<body>

<div id="toast" class="toast">
    <span class="material-symbols-outlined" id="toastIcon">check_circle</span>
    <span id="toastMsg"></span>
</div>

<div class="onboarding-container">

    <!-- Header -->
    <div class="onboarding-header">
        <div class="onboarding-logo">A</div>
        <h1>Kreator konfiguracji</h1>
        <p>{{ brand_name }} &mdash; skonfiguruj podstawowe ustawienia w 3 krokach</p>
    </div>

    <!-- Progress stepper -->
    <div class="progress-stepper">
        <div class="progress-dot active" id="dot1"></div>
        <div class="progress-line" id="line1"></div>
        <div class="progress-dot" id="dot2"></div>
        <div class="progress-line" id="line2"></div>
        <div class="progress-dot" id="dot3"></div>
    </div>
    <div class="progress-labels">
        <span class="progress-lbl active" id="lbl1">Allegro API</span>
        <span class="progress-lbl" id="lbl2">Backup</span>
        <span class="progress-lbl" id="lbl3">Ustawienia</span>
    </div>

    <!-- Steps -->
    <div id="stepsTrack">

        <!-- STEP 1: Allegro API -->
        <div class="step active">
            <div class="step-card">
                <div class="step-icon-wrap cyan">
                    <span class="material-symbols-outlined">shopping_cart</span>
                </div>
                <div class="step-title">Podpiecie API Allegro</div>
                <div class="step-desc">Polacz konto Allegro aby synchronizowac zamowienia i oferty automatycznie.</div>

                <div class="form-group">
                    <label>
                        <span class="material-symbols-outlined">key</span>
                        Client ID
                    </label>
                    <input type="text" id="allegro_client_id" placeholder="Wklej Client ID z Allegro Developer" value="{{ allegro_client_id }}">
                </div>
                <div class="form-group">
                    <label>
                        <span class="material-symbols-outlined">lock</span>
                        Client Secret
                    </label>
                    <input type="password" id="allegro_client_secret" placeholder="Wklej Client Secret" value="{{ allegro_client_secret }}">
                    <div class="form-hint">
                        <span class="material-symbols-outlined">info</span>
                        Znajdziesz w panelu developer.allegro.pl &rarr; Moje aplikacje
                    </div>
                </div>

                <button class="btn-test" id="btnTestAllegro" onclick="testAllegro()">
                    <span class="material-symbols-outlined">wifi_tethering</span>
                    Test polaczenia
                </button>

                <div class="step-actions">
                    <button class="btn-skip" onclick="goStep(2)">
                        <span class="material-symbols-outlined" style="font-size:1rem">skip_next</span>
                        Pomin
                    </button>
                    <button class="btn-next" onclick="saveStep(1)">
                        Zapisz i dalej
                        <span class="material-symbols-outlined">arrow_forward</span>
                    </button>
                </div>
            </div>
        </div>

        <!-- STEP 2: Backup path -->
        <div class="step">
            <div class="step-card">
                <div class="step-icon-wrap pink">
                    <span class="material-symbols-outlined">backup</span>
                </div>
                <div class="step-title">Sciezka backupu</div>
                <div class="step-desc">Wskazaz folder na kopie zapasowe bazy danych. Automatyczne backupy chronia Twoje dane.</div>

                <div class="form-group">
                    <label>
                        <span class="material-symbols-outlined">folder</span>
                        Katalog backupu
                    </label>
                    <input type="text" id="backup_path" placeholder="/home/pi/akces-hub/backups" value="{{ backup_path }}">
                    <div class="form-hint">
                        <span class="material-symbols-outlined">info</span>
                        Domyslnie: {{ default_backup_path }}
                    </div>
                </div>

                <div class="step-actions">
                    <button class="btn-skip" onclick="goStep(3)">
                        <span class="material-symbols-outlined" style="font-size:1rem">skip_next</span>
                        Pomin
                    </button>
                    <button class="btn-next" onclick="saveStep(2)">
                        Zapisz i dalej
                        <span class="material-symbols-outlined">arrow_forward</span>
                    </button>
                </div>
            </div>
        </div>

        <!-- STEP 3: Ustawienia -->
        <div class="step">
            <div class="step-card">
                <div class="step-icon-wrap lime">
                    <span class="material-symbols-outlined">tune</span>
                </div>
                <div class="step-title">Podstawowe ustawienia</div>
                <div class="step-desc">Waluta, magazyn i nazwa marki — mozesz zmienic pozniej w ustawieniach.</div>

                <div class="form-group">
                    <label>
                        <span class="material-symbols-outlined">payments</span>
                        Waluta
                    </label>
                    <select id="currency">
                        <option value="PLN" {{ 'selected' if currency == 'PLN' else '' }}>PLN &mdash; zloty polski</option>
                        <option value="EUR" {{ 'selected' if currency == 'EUR' else '' }}>EUR &mdash; euro</option>
                        <option value="USD" {{ 'selected' if currency == 'USD' else '' }}>USD &mdash; dolar amerykanski</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>
                        <span class="material-symbols-outlined">warehouse</span>
                        Domyslna nazwa magazynu
                    </label>
                    <input type="text" id="default_warehouse" placeholder="np. Magazyn Glowny" value="{{ default_warehouse }}">
                </div>
                <div class="form-group">
                    <label>
                        <span class="material-symbols-outlined">badge</span>
                        Nazwa marki / firmy
                    </label>
                    <input type="text" id="brand_name" placeholder="np. AKCES HUB" value="{{ brand_name }}">
                    <div class="form-hint">
                        <span class="material-symbols-outlined">info</span>
                        Wyswietlana w naglowku i na dokumentach
                    </div>
                </div>

                <button class="btn-finish" onclick="saveStep(3)">
                    <span class="material-symbols-outlined">check_circle</span>
                    Zakoncz konfiguracje
                </button>
            </div>
        </div>

    </div>
</div>

<script>
let currentStep = 1;

function goStep(n) {
    currentStep = n;
    var steps = document.querySelectorAll('.step');
    steps.forEach(function(s, idx) {
        s.classList.toggle('active', idx === n - 1);
    });
    // Update diamond dots
    for (let i = 1; i <= 3; i++) {
        const dot = document.getElementById('dot' + i);
        const lbl = document.getElementById('lbl' + i);
        dot.className = 'progress-dot';
        lbl.className = 'progress-lbl';
        if (i < n) {
            dot.classList.add('done');
            lbl.classList.add('done');
        } else if (i === n) {
            dot.classList.add('active');
            lbl.classList.add('active');
        }
    }
    // Update lines
    for (let i = 1; i <= 2; i++) {
        const line = document.getElementById('line' + i);
        line.className = 'progress-line';
        if (i < n) line.classList.add('done');
    }
}

function showToast(msg, type) {
    const toast = document.getElementById('toast');
    const icon = document.getElementById('toastIcon');
    const text = document.getElementById('toastMsg');
    toast.className = 'toast ' + type;
    icon.textContent = type === 'success' ? 'check_circle' : 'error';
    text.textContent = msg;
    toast.classList.add('show');
    setTimeout(function() { toast.classList.remove('show'); }, 3000);
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
                showToast('Konfiguracja zakonczona!', 'success');
                setTimeout(function() { window.location.href = '/dashboard'; }, 800);
            } else {
                showToast('Zapisano krok ' + step, 'success');
                goStep(step + 1);
            }
        }
    }).catch(() => {
        if (step < 3) goStep(step + 1);
        else window.location.href = '/dashboard';
    });
}

function testAllegro() {
    const btn = document.getElementById('btnTestAllegro');
    const cid = document.getElementById('allegro_client_id').value.trim();
    const csecret = document.getElementById('allegro_client_secret').value.trim();
    if (!cid || !csecret) {
        showToast('Wypelnij Client ID i Client Secret', 'error');
        return;
    }
    btn.classList.add('testing');
    btn.querySelector('.material-symbols-outlined').textContent = 'hourglass_empty';
    // Simulate test (no real endpoint yet — visual feedback)
    setTimeout(function() {
        btn.classList.remove('testing');
        if (cid.length > 10 && csecret.length > 10) {
            btn.classList.add('success');
            btn.querySelector('.material-symbols-outlined').textContent = 'check_circle';
            showToast('Dane wygladaja poprawnie', 'success');
        } else {
            btn.classList.add('fail');
            btn.querySelector('.material-symbols-outlined').textContent = 'error';
            showToast('Sprawdz dane — za krotkie', 'error');
        }
        setTimeout(function() {
            btn.className = 'btn-test';
            btn.querySelector('.material-symbols-outlined').textContent = 'wifi_tethering';
        }, 3000);
    }, 1200);
}
</script>
</body>
</html>'''


@onboarding_bp.route('/onboarding', methods=['GET'])
def onboarding_page():
    """Wyswietla wizard onboardingu — 3 kroki"""
    if is_onboarding_completed():
        return redirect('/dashboard')

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
