"""
Modul ustawien i administracji -- routes dla /ustawienia/*, /admin/*, /settings/*, /raport/*
"""
from flask import Blueprint, request, redirect, session, flash, jsonify, Response, current_app, url_for, render_template, render_template_string
from modules.database import get_db, get_config, set_config
import os

ustawienia_bp = Blueprint('ustawienia', __name__)


# ============================================================
# USTAWIENIA SYSTEMU
# ============================================================
@ustawienia_bp.route('/ustawienia')
def ustawienia():
    from modules.database import get_config, is_module_enabled
    from modules.email_reports import get_email_config

    base_url = get_config('app_base_url', 'http://localhost:5000')
    email_cfg = get_email_config()

    # SMTP config for license mailer
    smtp_cfg = {
        'host': get_config('smtp_host', ''),
        'port': get_config('smtp_port', '587'),
        'user': get_config('smtp_user', ''),
        'password': get_config('smtp_password', ''),
        'admin_email': get_config('admin_email', ''),
    }

    # Module toggles
    modules_cfg = {
        'paletomat': {'name': 'Paletomat', 'desc': 'Skaner palet, scraping Amazon', 'enabled': is_module_enabled('paletomat')},
        'magazynier': {'name': 'Magazynier', 'desc': 'Zarzadzanie magazynem', 'enabled': is_module_enabled('magazynier')},
        'allegro': {'name': 'Allegro', 'desc': 'Integracja z Allegro', 'enabled': is_module_enabled('allegro')},
        'olx': {'name': 'OLX', 'desc': 'Integracja z OLX', 'enabled': is_module_enabled('olx')},
        'vinted': {'name': 'Vinted', 'desc': 'Integracja z Vinted', 'enabled': is_module_enabled('vinted')},
        'telegram': {'name': 'Telegram', 'desc': 'Bot Telegram', 'enabled': is_module_enabled('telegram')},
    }
    brand_name_val = get_config('brand_name', 'AKCES HUB')
    brand_color = get_config('brand_color', '#6366f1')

    # Sprawdz czy to ngrok URL
    is_ngrok = 'ngrok' in base_url

    has_logo = os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'static', 'brand_logo.png'))
    import time as _time
    logo_bust = int(_time.time())

    ngrok_token = get_config('ngrok_auth_token', '')
    ngrok_domain = get_config('ngrok_domain', '')
    data_retention_years = get_config('data_retention_years', '5')
    dpd_cennik_id = get_config('dpd_cennik_id', 'bf1a1cf0-6a1e-41b3-a42e-d46846b35f43')
    zwroty_warunki_id = get_config('zwroty_warunki_id', '7b75ba63-0967-4536-a439-730f8e563a59')
    reklamacje_warunki_id = get_config('reklamacje_warunki_id', '128af307-9341-4f8c-b406-63b9060cce7d')

    USTAWIENIA_TEMPLATE = '''{% extends "base.html" %}
{% block page_title %}Ustawienia systemu{% endblock %}
{% block content %}
<style>
.settings-section{margin-bottom:20px}
.settings-card{background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin-bottom:16px;box-shadow:var(--shadow)}
.settings-card-accent{position:relative;overflow:hidden}
.settings-card-accent::before{content:'';position:absolute;top:0;left:0;right:0;height:3px}
.settings-card-accent.blue::before{background:linear-gradient(90deg,var(--blue),#2563eb)}
.settings-card-accent.purple::before{background:linear-gradient(90deg,var(--accent),var(--accent2))}
.settings-card-accent.green::before{background:linear-gradient(90deg,var(--green),#16a34a)}
.settings-card-accent.pink::before{background:linear-gradient(90deg,#ec4899,#a855f7)}
.settings-card-accent.red::before{background:linear-gradient(90deg,var(--red),#dc2626)}
.section-header{display:flex;align-items:center;gap:10px;margin-bottom:16px}
.section-header-icon{font-size:1.2rem}
.section-header-title{font-weight:700;font-size:0.95rem}
.section-header-badge{font-size:0.7rem;padding:3px 10px;border-radius:10px;color:#fff;font-weight:600}
.module-toggle{display:flex;align-items:center;gap:12px;padding:12px;background:var(--bg);border-radius:var(--radius-sm);cursor:pointer;margin-bottom:8px;border:1px solid var(--border);transition:all 0.2s}
.module-toggle:hover{border-color:var(--accent)}
.module-toggle input[type=checkbox]{width:18px;height:18px;accent-color:var(--green);cursor:pointer}
.module-name{font-weight:600;font-size:0.88rem}
.module-desc{font-size:0.73rem;color:var(--text-muted);margin-top:2px}
.link-card{display:flex;align-items:center;gap:12px;padding:16px;background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);text-decoration:none;color:var(--text);transition:all 0.2s;box-shadow:var(--shadow)}
.link-card:hover{border-color:var(--accent);transform:translateY(-1px);box-shadow:var(--shadow-md)}
.link-card-icon{font-size:1.5rem}
.link-card-title{font-weight:700;font-size:0.95rem}
.link-card-desc{font-size:0.78rem;color:var(--text-muted);margin-top:2px}
.link-card-arrow{margin-left:auto;font-size:1.1rem;color:var(--accent)}
.btn-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px}
.hint-box{padding:12px;background:var(--bg);border-radius:var(--radius-sm);font-size:0.78rem;color:var(--text-muted);margin-top:12px;line-height:1.6}
.hint-box a{color:var(--accent)}
.hint-box b{color:var(--text-secondary)}
.danger-btn{width:100%;padding:12px;border-radius:var(--radius-sm);color:#fff;border:none;font-weight:600;cursor:pointer;font-size:0.88rem;transition:all 0.2s}
.danger-btn:hover{transform:translateY(-1px);box-shadow:var(--shadow-md)}
</style>

<!-- URL aplikacji -->
<div class="settings-card settings-card-accent blue">
    <div class="section-header">
        <span class="section-header-icon"><span class=material-symbols-outlined>language</span></span>
        <span class="section-header-title">Adres URL aplikacji (dla QR kodow)</span>
    </div>
    <div class="alert alert-warning" style="margin-bottom:14px">
        <b>WAZNE:</b> Zeby QR kody dzialaly z telefonu, wpisz swoj adres ngrok!
    </div>
    <form action="/ustawienia/save" method="POST">
        <div class="form-group">
            <label>Adres URL</label>
            <input type="text" name="app_base_url" id="baseUrlInput" value="{{ base_url }}"
                placeholder="https://xxx.ngrok-free.dev" class="form-control" style="font-family:monospace">
        </div>
        <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:12px">
            {% if is_ngrok %}
                <span style="color:var(--green)">Ngrok wykryty - QR kody beda dzialac z telefonu!</span>
            {% else %}
                <span style="color:var(--yellow)">localhost - QR kody nie beda dzialac z telefonu</span>
            {% endif %}
        </div>
        <button type="submit" class="btn btn-primary">Zapisz adres URL</button>
    </form>
</div>

<!-- RAPORTY EMAIL -->
<div class="settings-card settings-card-accent blue">
    <div class="section-header">
        <span class="section-header-icon"><span class=material-symbols-outlined>email</span></span>
        <span class="section-header-title">Raporty Email</span>
        <span class="section-header-badge" style="background:{{ '#22c55e' if email_cfg.get('enabled') else 'var(--text-muted)' }}">
            {{ 'WLACZONE' if email_cfg.get('enabled') else 'WYLACZONE' }}
        </span>
    </div>
    <form action="/ustawienia/email" method="POST">
        <div class="form-group">
            <label>Email (Gmail)</label>
            <input type="email" name="email" value="{{ email_cfg.get('email') or '' }}"
                placeholder="twoj@gmail.com" class="form-control">
        </div>
        <div class="form-group">
            <label>Haslo aplikacji (nie zwykle haslo!)</label>
            <input type="password" name="password"
                placeholder="{{ '••••••••••••••••' if email_cfg.get('password') else 'xxxx xxxx xxxx xxxx' }}"
                class="form-control">
        </div>
        <div class="form-group">
            <label>Odbiorca (opcjonalnie, domyslnie = nadawca)</label>
            <input type="email" name="recipient" value="{{ email_cfg.get('recipient') or '' }}"
                placeholder="Zostaw puste jesli ten sam email" class="form-control">
        </div>
        <div class="toggle-row" style="margin-bottom:14px">
            <span style="font-size:0.88rem;font-weight:500">Wlacz raporty email</span>
            <label style="cursor:pointer">
                <input type="checkbox" name="enabled" {{ 'checked' if email_cfg.get('enabled') else '' }} style="width:18px;height:18px">
            </label>
        </div>
        <button type="submit" class="btn btn-primary">Zapisz konfiguracje email</button>
    </form>
    <div class="btn-grid">
        <a href="/raport/podglad" target="_blank" class="btn btn-secondary btn-sm" style="text-align:center;display:block;width:100%">
            Podglad raportu
        </a>
        <a href="/raport/wyslij" onclick="return confirm('Wyslac raport tygodniowy na email?')" class="btn btn-success btn-sm" style="text-align:center;display:block;width:100%">
            Wyslij teraz
        </a>
    </div>
    <div class="hint-box">
        <b>Jak uzyskac haslo aplikacji Gmail?</b><br>
        1. Wejdz na <a href="https://myaccount.google.com/apppasswords" target="_blank">myaccount.google.com/apppasswords</a><br>
        2. Wybierz "Poczta" i "Windows"<br>
        3. Skopiuj 16-znakowe haslo (bez spacji)
    </div>
</div>

<!-- NGROK -->
<div class="settings-card settings-card-accent purple">
    <div class="section-header">
        <span class="section-header-icon"><span class=material-symbols-outlined>rocket_launch</span></span>
        <span class="section-header-title">Ngrok - Zdalny dostep</span>
    </div>
    <form action="/ustawienia/ngrok-token" method="POST">
        <div class="form-group">
            <label>Auth Token (z <a href="https://dashboard.ngrok.com/get-started/your-authtoken" target="_blank" style="color:var(--accent2)">dashboard.ngrok.com</a>)</label>
            <input type="password" name="ngrok_token" value="{{ ngrok_token }}"
                placeholder="2abc...xyz123" class="form-control" style="font-family:monospace">
        </div>
        <div class="form-group">
            <label>Stala domena (opcjonalnie, np. akceshub.ngrok.dev)</label>
            <input type="text" name="ngrok_domain" value="{{ ngrok_domain }}"
                placeholder="twoja-domena.ngrok-free.dev" class="form-control" style="font-family:monospace">
        </div>
        <button type="submit" class="btn btn-purple">Zapisz i polacz</button>
    </form>
    <div style="margin-top:10px;font-size:0.78rem;color:var(--text-muted)">
        Na Raspberry Pi ngrok startuje automatycznie. Token zapisuje sie w bazie danych.
    </div>
</div>

<!-- KREATOR API KEYS -->
<a href="/ustawienia/kreator" class="link-card" style="margin-bottom:16px">
    <span class="link-card-icon"><span class=material-symbols-outlined>build</span></span>
    <div>
        <div class="link-card-title">Kreator konfiguracji</div>
        <div class="link-card-desc">Wszystkie klucze API w jednym miejscu (Allegro, Telegram, Gemini, OLX...)</div>
    </div>
    <span class="link-card-arrow">→</span>
</a>

<!-- KIOSK MODE -->
<div class="settings-card settings-card-accent purple">
    <div class="section-header">
        <span class="section-header-icon"><span class=material-symbols-outlined>tv</span></span>
        <span class="section-header-title">Tryb Kiosk (Raspberry Pi)</span>
    </div>
    <div style="font-size:0.82rem;color:var(--text-muted);margin-bottom:14px">
        Optymalizacja UI dla ekranu dotykowego 7"
    </div>
    <div class="btn-grid">
        <a href="/?kiosk=1" class="btn btn-primary btn-sm" style="text-align:center;display:block;width:100%">Wlacz Kiosk</a>
        <a href="/?kiosk=0" class="btn btn-secondary btn-sm" style="text-align:center;display:block;width:100%">Wylacz Kiosk</a>
    </div>
</div>

<!-- MODULY -->
<div class="settings-card settings-card-accent green">
    <div class="section-header">
        <span class="section-header-icon">🧩</span>
        <span class="section-header-title">Moduly systemu</span>
    </div>
    <form action="/ustawienia/modules" method="POST">
        {% for key, mod in modules_cfg.items() %}
        <label class="module-toggle">
            <input type="checkbox" name="module_{{ key }}" {{ 'checked' if mod.enabled else '' }}>
            <div>
                <div class="module-name">{{ mod.name }}</div>
                <div class="module-desc">{{ mod.desc }}</div>
            </div>
        </label>
        {% endfor %}
        <button type="submit" class="btn btn-success" style="margin-top:8px">Zapisz moduly</button>
    </form>
</div>

<!-- BRANDING -->
<div class="settings-card settings-card-accent pink">
    <div class="section-header">
        <span class="section-header-icon"><span class=material-symbols-outlined>palette</span></span>
        <span class="section-header-title">Branding</span>
    </div>
    <form action="/ustawienia/branding" method="POST" enctype="multipart/form-data">
        <div class="form-group">
            <label>Nazwa systemu</label>
            <input type="text" name="brand_name" value="{{ brand_name_val }}" class="form-control">
        </div>
        <div class="form-group">
            <label>Logo firmy (PNG/JPG, max 500KB)</label>
            <div style="display:flex;gap:10px;align-items:center;margin-top:5px">
                {% if has_logo %}
                    <img src="/static/brand_logo.png?v={{ logo_bust }}" style="height:40px;border-radius:6px">
                {% else %}
                    <span style="color:var(--text-muted);font-size:0.85rem">Brak logo</span>
                {% endif %}
                <input type="file" name="brand_logo" accept="image/png,image/jpeg" class="form-control" style="flex:1">
            </div>
        </div>
        <div class="form-group">
            <label>Kolor przewodni</label>
            <div style="display:flex;gap:10px;align-items:center;margin-top:5px">
                <input type="color" name="brand_color" value="{{ brand_color }}" style="width:50px;height:38px;border:none;background:none;cursor:pointer">
                <input type="text" value="{{ brand_color }}" class="form-control" style="flex:1;font-family:monospace"
                    onchange="this.previousElementSibling.value=this.value" readonly>
            </div>
        </div>
        <button type="submit" class="btn" style="background:linear-gradient(135deg,#ec4899,#a855f7)">Zapisz branding</button>
    </form>
</div>

<!-- UZYTKOWNICY -->
<div style="display:grid;gap:10px;margin-bottom:16px">
    <a href="/auth/users" class="link-card">
        <span class="link-card-icon">👥</span>
        <div class="link-card-title">Zarzadzanie uzytkownikami</div>
        <span class="link-card-arrow">→</span>
    </a>
    <a href="/auth/zmien-haslo" class="link-card">
        <span class="link-card-icon"><span class=material-symbols-outlined>lock</span></span>
        <div class="link-card-title">Zmien haslo</div>
        <span class="link-card-arrow">→</span>
    </a>
</div>

<!-- AKTUALIZACJA SYSTEMU -->
<a href="/narzedzia" class="link-card" style="margin-bottom:16px">
    <span class="link-card-icon"><span class=material-symbols-outlined>sync</span></span>
    <div class="link-card-title">Aktualizacja systemu → Narzedzia</div>
    <span class="link-card-arrow">→</span>
</a>

<!-- DANE NADAWCY NA ETYKIECIE -->
<div class="settings-card settings-card-accent blue" id="nadawca">
    <div class="section-header">
        <span class="section-header-icon"><span class=material-symbols-outlined>inventory_2</span></span>
        <span class="section-header-title">Dane nadawcy na etykiecie</span>
    </div>
    <form action="/ustawienia/nadawca/save" method="POST">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div class="form-group">
                <label>Imię</label>
                <input type="text" name="firma_imie" value="{{ firma_imie }}" placeholder="Andrzej" class="form-control">
            </div>
            <div class="form-group">
                <label>Nazwisko</label>
                <input type="text" name="firma_nazwisko" value="{{ firma_nazwisko }}" placeholder="Gauza" class="form-control">
            </div>
        </div>
        <div class="form-group">
            <label>Nazwa firmy</label>
            <input type="text" name="firma_nazwa" value="{{ firma_nazwa }}" placeholder="AKCES" class="form-control">
        </div>
        <div class="form-group">
            <label>Ulica i numer</label>
            <input type="text" name="firma_ulica" value="{{ firma_ulica }}" placeholder="Poniatowskiego 13" class="form-control">
        </div>
        <div style="display:grid;grid-template-columns:1fr 2fr;gap:12px">
            <div class="form-group">
                <label>Kod pocztowy</label>
                <input type="text" name="allegro_postcode" value="{{ allegro_postcode }}" placeholder="74-505" class="form-control">
            </div>
            <div class="form-group">
                <label>Miejscowość</label>
                <input type="text" name="allegro_city" value="{{ allegro_city }}" placeholder="Mieszkowice" class="form-control">
            </div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div class="form-group">
                <label>E-mail</label>
                <input type="email" name="firma_email" value="{{ firma_email }}" placeholder="agauza@interia.eu" class="form-control">
            </div>
            <div class="form-group">
                <label>Telefon</label>
                <input type="text" name="firma_telefon" value="{{ firma_telefon }}" placeholder="+48 604 753 407" class="form-control">
            </div>
        </div>
        <button type="submit" class="btn btn-primary">Zapisz dane nadawcy</button>
    </form>
</div>

<!-- PARAMETRY INTEGRACJI I REGULAMINY -->
<div class="settings-card settings-card-accent blue">
    <div class="section-header">
        <span class="section-header-icon"><span class=material-symbols-outlined>assignment</span></span>
        <span class="section-header-title">Parametry Integracji i Regulaminy</span>
    </div>
    <form action="/ustawienia/integracje-parametry" method="POST">
        <div class="form-group">
            <label>ID cennika DPD</label>
            <input type="text" name="dpd_cennik_id" value="{{ dpd_cennik_id }}"
                placeholder="bf1a1cf0-6a1e-41b3-a42e-d46846b35f43" class="form-control" style="font-family:monospace;font-size:0.85rem">
        </div>
        <div class="form-group">
            <label>ID warunkow zwrotow</label>
            <input type="text" name="zwroty_warunki_id" value="{{ zwroty_warunki_id }}"
                placeholder="7b75ba63-0967-4536-a439-730f8e563a59" class="form-control" style="font-family:monospace;font-size:0.85rem">
        </div>
        <div class="form-group">
            <label>ID warunkow reklamacji</label>
            <input type="text" name="reklamacje_warunki_id" value="{{ reklamacje_warunki_id }}"
                placeholder="128af307-9341-4f8c-b406-63b9060cce7d" class="form-control" style="font-family:monospace;font-size:0.85rem">
        </div>
        <button type="submit" class="btn btn-primary">Zapisz parametry integracji</button>
    </form>
</div>

<!-- RODO - RETENCJA DANYCH -->
<div class="settings-card settings-card-accent green">
    <div class="section-header">
        <span class="section-header-icon"><span class=material-symbols-outlined>shield</span></span>
        <span class="section-header-title">Retencja danych (RODO)</span>
    </div>
    <form action="/ustawienia/retencja" method="POST">
        <div class="form-group">
            <label>Okres przechowywania danych osobowych</label>
            <select name="data_retention_years" class="form-control">
                <option value="3" {{ 'selected' if data_retention_years == '3' else '' }}>3 lata</option>
                <option value="5" {{ 'selected' if data_retention_years == '5' else '' }}>5 lat</option>
                <option value="7" {{ 'selected' if data_retention_years == '7' else '' }}>7 lat</option>
                <option value="0" {{ 'selected' if data_retention_years == '0' else '' }}>Nigdy (bez automatycznej anonimizacji)</option>
            </select>
        </div>
        <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:12px;line-height:1.6">
            Dane starsze niz wybrany okres zostana automatycznie zanonimizowane (nie usuniete).<br>
            Kwoty i statystyki pozostana do celow ksiegowych.
        </div>
        <button type="submit" class="btn btn-primary">Zapisz ustawienia retencji</button>
    </form>
</div>

<!-- BAZA DANYCH -->
<div class="settings-card" style="margin-bottom:16px">
    <div class="section-header">
        <span class="section-header-icon"><span class=material-symbols-outlined>save</span></span>
        <span class="section-header-title">Baza danych</span>
    </div>
    <div style="display:grid;gap:10px">
        <form method="POST" action="/ustawienia/upload-db" enctype="multipart/form-data" onsubmit="return confirm('UWAGA!\\n\\nTo nadpisze obecna baze danych!\\nAktualny backup zostanie utworzony automatycznie.\\n\\nKontynuowac?')">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <div style="font-size:0.82rem;color:var(--text-muted);margin-bottom:10px">Wgraj plik bazy danych (.db) — np. od innego uzytkownika lub z backupu</div>
            <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
                <input type="file" name="db_file" accept=".db" required style="flex:1;min-width:200px;font-size:0.85rem;color:var(--text)">
                <button type="submit" style="background:#3b82f6;color:#fff;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-weight:600;white-space:nowrap">Wgraj baze</button>
            </div>
        </form>
        <a href="/ustawienia/download-db" style="display:inline-flex;align-items:center;gap:6px;color:#22c55e;font-size:0.85rem;text-decoration:none;margin-top:4px">
            ⬇ Pobierz aktualna baze danych
        </a>
    </div>
</div>

<!-- ZARZADZANIE DOSTAWCAMI -->
<div class="settings-card settings-card-accent blue">
    <div class="section-header">
        <span class="section-header-icon">🚚</span>
        <span class="section-header-title">Zarzadzanie dostawcami</span>
    </div>
    <div style="font-size:0.82rem;color:var(--text-muted);margin-bottom:14px">
        Lista dodatkowych dostawcow wyswietlanych w listach rozwijanych. Dostawcy z istniejacych palet i produktow sa dolaczani automatycznie.
    </div>
    <form action="/ustawienia/dostawcy" method="POST">
        <div class="form-group">
            <label>Dodatkowi dostawcy (oddzieleni przecinkami)</label>
            <input type="text" name="custom_dostawcy" value="{{ custom_dostawcy or '' }}"
                placeholder="np. Jobalots, Warrington, MojaFirma" class="form-control">
        </div>
        <button type="submit" class="btn btn-primary">Zapisz liste dostawcow</button>
    </form>
</div>

<!-- SMTP CONFIG (License Mailer) -->
<div class="settings-card settings-card-accent purple">
    <div class="section-header">
        <span class="section-header-icon"><span class=material-symbols-outlined>email</span></span>
        <span class="section-header-title">Konfiguracja Email (SMTP)</span>
        <span class="section-header-badge" style="background:{{ '#22c55e' if smtp_cfg.get('host') and smtp_cfg.get('user') else 'var(--text-muted)' }}">
            {{ 'SKONFIGUROWANY' if smtp_cfg.get('host') and smtp_cfg.get('user') else 'NIESKONFIGUROWANY' }}
        </span>
    </div>
    <div style="font-size:0.82rem;color:var(--text-muted);margin-bottom:14px">
        Ustawienia SMTP do powiadomien o wygasajacych licencjach. Codziennie sprawdzamy licencje i wysylamy powiadomienia.
    </div>
    <form action="/ustawienia/smtp" method="POST">
        <div class="form-group">
            <label>SMTP Host</label>
            <input type="text" name="smtp_host" value="{{ smtp_cfg.get('host') or '' }}"
                placeholder="smtp.gmail.com" class="form-control">
        </div>
        <div class="form-group">
            <label>SMTP Port</label>
            <input type="number" name="smtp_port" value="{{ smtp_cfg.get('port') or '587' }}"
                placeholder="587" class="form-control">
        </div>
        <div class="form-group">
            <label>SMTP User</label>
            <input type="text" name="smtp_user" value="{{ smtp_cfg.get('user') or '' }}"
                placeholder="email@example.com" class="form-control">
        </div>
        <div class="form-group">
            <label>SMTP Password</label>
            <input type="password" name="smtp_password"
                placeholder="{{ '••••••••••••' if smtp_cfg.get('password') else 'Haslo SMTP' }}"
                class="form-control">
        </div>
        <div class="form-group">
            <label>Admin Email (odbiorca powiadomien)</label>
            <input type="email" name="admin_email" value="{{ smtp_cfg.get('admin_email') or '' }}"
                placeholder="admin@example.com" class="form-control">
        </div>
        <button type="submit" class="btn btn-primary">Zapisz konfiguracje SMTP</button>
    </form>
    <div class="btn-grid" style="margin-top:12px">
        <form method="POST" action="/ustawienia/smtp-test" style="width:100%">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <button type="submit" class="btn btn-secondary btn-sm" style="width:100%">Wyslij testowy email</button>
        </form>
    </div>
</div>

<!-- DANGER ZONE -->
<div class="settings-card settings-card-accent red">
    <div class="section-header">
        <span class="section-header-icon"><span class=material-symbols-outlined>warning</span></span>
        <span class="section-header-title" style="color:var(--red)">Strefa niebezpieczna</span>
    </div>
    <div style="font-size:0.82rem;color:var(--text-muted);margin-bottom:14px">
        Wyczysc testowe dane. Ta operacja jest nieodwracalna!
    </div>
    <div style="display:grid;gap:10px">
        <form method="POST" action="/ustawienia/reset-sprzedaze" onsubmit="return confirm('Na pewno wyczyscic historie sprzedazy?')">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <button type="submit" class="danger-btn" style="background:var(--red)">Wyczysc historie sprzedazy</button>
        </form>
        <form method="POST" action="/ustawienia/reset-magazyn" onsubmit="return confirm('UWAGA!\\n\\nTo usunie WSZYSTKIE produkty z magazynu!\\n\\nNa pewno kontynuowac?')">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <button type="submit" class="danger-btn" style="background:#dc2626">Wyczysc magazyn (produkty)</button>
        </form>
        <form method="POST" action="/ustawienia/reset-palety" onsubmit="return confirm('UWAGA!\\n\\nTo usunie WSZYSTKIE palety i powiazane produkty!\\n\\nNa pewno kontynuowac?')">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <button type="submit" class="danger-btn" style="background:#b91c1c">Wyczysc palety</button>
        </form>
        <form method="POST" action="/ustawienia/reset-scraped" onsubmit="return confirm('Wyczyscic zescrapowane produkty z Palatomatu?')">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <button type="submit" class="danger-btn" style="background:#991b1b">Wyczysc scraped (Paletomat)</button>
        </form>
    </div>
</div>
{% endblock %}
'''
    return render_template_string(USTAWIENIA_TEMPLATE,
        version=current_app.config.get('VERSION', ''),
        brand_name=current_app.config.get('BRAND_NAME', 'Akces Hub'),
        current_user=session.get('user'),
        base_url=base_url,
        email_cfg=email_cfg,
        is_ngrok=is_ngrok,
        modules_cfg=modules_cfg,
        brand_name_val=brand_name_val,
        brand_color=brand_color,
        has_logo=has_logo,
        logo_bust=logo_bust,
        ngrok_token=ngrok_token,
        ngrok_domain=ngrok_domain,
        data_retention_years=data_retention_years,
        dpd_cennik_id=dpd_cennik_id,
        zwroty_warunki_id=zwroty_warunki_id,
        reklamacje_warunki_id=reklamacje_warunki_id,
        smtp_cfg=smtp_cfg,
        firma_imie=get_config('firma_imie', ''),
        firma_nazwisko=get_config('firma_nazwisko', ''),
        firma_nazwa=get_config('firma_nazwa', ''),
        firma_ulica=get_config('firma_ulica', ''),
        allegro_postcode=get_config('allegro_postcode', ''),
        allegro_city=get_config('allegro_city', ''),
        firma_email=get_config('firma_email', ''),
        firma_telefon=get_config('firma_telefon', ''),
        custom_dostawcy=get_config('custom_dostawcy', ''),
    )


@ustawienia_bp.route('/ustawienia/kreator')
def ustawienia_kreator():
    """Kreator konfiguracji - wszystkie klucze API w jednym miejscu"""
    from modules.database import get_config

    # Pobierz wszystkie klucze
    cfg = {
        'allegro_client_id': get_config('allegro_client_id', ''),
        'allegro_client_secret': get_config('allegro_client_secret', ''),
        'allegro_redirect_uri': get_config('allegro_redirect_uri', ''),
        'telegram_bot_token': get_config('telegram_bot_token', ''),
        'telegram_chat_id': get_config('telegram_chat_id', ''),
        'support_chat_id': get_config('support_chat_id', ''),
        'gemini_api_key': get_config('gemini_api_key', ''),
        'gemini_model': get_config('gemini_model', 'gemini-2.5-flash'),
        'perplexity_api_key': get_config('perplexity_api_key', ''),
        'ngrok_auth_token': get_config('ngrok_auth_token', ''),
        'ngrok_domain': get_config('ngrok_domain', ''),
        'olx_client_id': get_config('olx_client_id', ''),
        'olx_client_secret': get_config('olx_client_secret', ''),
        'olx_redirect_uri': get_config('olx_redirect_uri', ''),
        'support_email': get_config('support_email', ''),
        'support_phone': get_config('support_phone', ''),
        'support_info': get_config('support_info', ''),
        'rembg_vps_url': get_config('rembg_vps_url', ''),
        'rembg_vps_key': get_config('rembg_vps_key', ''),
    }

    def status_dot(key):
        return '●' if cfg.get(key) else '●'

    saved_count = request.args.get('saved', '')
    is_welcome = request.args.get('welcome', '')

    # Build status overview data
    integrations = [
        ('allegro_client_id', 'Allegro'),
        ('telegram_bot_token', 'Telegram'),
        ('gemini_api_key', 'Gemini AI'),
        ('perplexity_api_key', 'Perplexity AI'),
        ('ngrok_auth_token', 'Ngrok'),
        ('olx_client_id', 'OLX'),
        ('rembg_vps_url', 'Rembg VPS'),
    ]
    status_items = [(status_dot(k), name) for k, name in integrations]

    # Build sections config for template
    sections = [
        {
            'key': 'allegro_client_id', 'icon': '<span class=material-symbols-outlined>shopping_cart</span>', 'title': 'Allegro API',
            'hint': 'Zarejestruj aplikacje na <a href="https://apps.developer.allegro.pl" target="_blank" style="color:var(--accent)">apps.developer.allegro.pl</a>',
            'fields': [
                {'name': 'allegro_client_id', 'label': 'Client ID', 'type': 'text', 'placeholder': 'Twoj Client ID z Allegro'},
                {'name': 'allegro_client_secret', 'label': 'Client Secret', 'type': 'password', 'placeholder': 'Twoj Client Secret'},
                {'name': 'allegro_redirect_uri', 'label': 'Redirect URI', 'type': 'text', 'placeholder': 'https://twoja-domena.ngrok-free.dev/allegro/callback'},
            ]
        },
        {
            'key': 'telegram_bot_token', 'icon': '<span class=material-symbols-outlined>chat</span>', 'title': 'Telegram Bot',
            'hint': 'Stworz bota przez <a href="https://t.me/BotFather" target="_blank" style="color:var(--accent)">@BotFather</a> na Telegramie',
            'fields': [
                {'name': 'telegram_bot_token', 'label': 'Bot Token', 'type': 'password', 'placeholder': '123456789:ABCdefGHIjklMNOpqrsTUVwxyz'},
                {'name': 'telegram_chat_id', 'label': 'Chat ID (powiadomienia o sprzedazach)', 'type': 'text', 'placeholder': '-1001234567890'},
                {'name': 'support_chat_id', 'label': 'Support Chat ID (Twoj prywatny, opcjonalnie)', 'type': 'text', 'placeholder': '123456789'},
            ]
        },
        {
            'key': 'gemini_api_key', 'icon': '<span class=material-symbols-outlined>auto_awesome</span>', 'title': 'Google Gemini AI',
            'hint': 'Pobierz klucz z <a href="https://aistudio.google.com/apikey" target="_blank" style="color:var(--accent)">aistudio.google.com/apikey</a> (darmowy!)',
            'fields': [
                {'name': 'gemini_api_key', 'label': 'API Key', 'type': 'password', 'placeholder': 'AIzaSy...'},
                {'name': 'gemini_model', 'label': 'Model AI', 'type': 'select', 'options': [
                    ('gemini-2.5-flash', '<span class=material-symbols-outlined>bolt</span> Gemini 2.5 Flash — ZALECANY <span class=material-symbols-outlined style=color:#22c55e>check_circle</span> darmowy, stabilny'),
                    ('gemini-2.5-flash-lite', '<span class=material-symbols-outlined>air</span> Gemini 2.5 Flash Lite — szybszy, mniej dokładny'),
                    ('gemini-3.1-flash-lite-preview', '<span class=material-symbols-outlined>rocket_launch</span> Gemini 3.1 Flash Lite — najnowszy, testowy <span class=material-symbols-outlined>warning</span>'),
                    ('gemini-3.1-pro-preview', '<span class=material-symbols-outlined>psychology</span> Gemini 3.1 Pro — najlepszy, testowy, płatny <span class=material-symbols-outlined>payments</span>'),
                ]},
            ]
        },
        {
            'key': 'perplexity_api_key', 'icon': '<span class=material-symbols-outlined>search</span>', 'title': 'Perplexity AI',
            'hint': 'Klucz z <a href="https://www.perplexity.ai/settings/api" target="_blank" style="color:var(--accent)">perplexity.ai/settings/api</a> (do analizy okazji)',
            'fields': [
                {'name': 'perplexity_api_key', 'label': 'API Key', 'type': 'password', 'placeholder': 'pplx-...'},
            ]
        },
        {
            'key': 'ngrok_auth_token', 'icon': '<span class=material-symbols-outlined>rocket_launch</span>', 'title': 'Ngrok (zdalny dostep)',
            'hint': 'Token z <a href="https://dashboard.ngrok.com/get-started/your-authtoken" target="_blank" style="color:var(--accent)">dashboard.ngrok.com</a>',
            'fields': [
                {'name': 'ngrok_auth_token', 'label': 'Auth Token', 'type': 'password', 'placeholder': '2abc...xyz'},
                {'name': 'ngrok_domain', 'label': 'Stala domena (opcjonalnie)', 'type': 'text', 'placeholder': 'twoja-firma.ngrok-free.dev'},
            ]
        },
        {
            'key': 'support_email', 'icon': '<span class=material-symbols-outlined>call</span>', 'title': 'Dane kontaktowe (support)',
            'hint': 'Wyswietlane klientom na stronie zgloszenia problemu',
            'open_condition': 'support_nodata',
            'fields': [
                {'name': 'support_email', 'label': 'Email kontaktowy', 'type': 'email', 'placeholder': 'support@twojafirma.pl', 'mono': False},
                {'name': 'support_phone', 'label': 'Telefon', 'type': 'text', 'placeholder': '+48 123 456 789', 'mono': False},
                {'name': 'support_info', 'label': 'Dodatkowa informacja (opcjonalnie)', 'type': 'text', 'placeholder': 'np. Odpowiadamy pon-pt 9-17', 'mono': False},
            ]
        },
        {
            'key': 'olx_client_id', 'icon': '<span class=material-symbols-outlined>inventory_2</span>', 'title': 'OLX API (opcjonalnie)',
            'hint': 'Zarejestruj app na <a href="https://developer.olx.pl" target="_blank" style="color:var(--accent)">developer.olx.pl</a>',
            'always_closed': True,
            'fields': [
                {'name': 'olx_client_id', 'label': 'Client ID', 'type': 'text', 'placeholder': 'OLX Client ID'},
                {'name': 'olx_client_secret', 'label': 'Client Secret', 'type': 'password', 'placeholder': 'OLX Client Secret'},
                {'name': 'olx_redirect_uri', 'label': 'Redirect URI', 'type': 'text', 'placeholder': 'https://twoja-domena.ngrok-free.dev/olx/callback'},
            ]
        },
        {
            'key': 'rembg_vps_url', 'icon': '<span class=material-symbols-outlined>image</span>', 'title': 'Rembg VPS (usuwanie tla)',
            'hint': 'Serwer VPS z rembg do usuwania tel ze zdjec (nie obciaza Pi). Postaw <code>rembg_service.py</code> na VPS i wpisz adres.',
            'has_test': True,
            'fields': [
                {'name': 'rembg_vps_url', 'label': 'URL serwera', 'type': 'text', 'placeholder': 'http://123.45.67.89:5050'},
                {'name': 'rembg_vps_key', 'label': 'Klucz API (opcjonalny)', 'type': 'password', 'placeholder': 'Tajny klucz (REMBG_API_KEY na VPS)'},
            ]
        },
    ]

    # Determine open state
    support_nodata = not cfg['support_email'] and not cfg['support_phone']

    KREATOR_TEMPLATE = '''{% extends "base.html" %}
{% block page_title %}Kreator konfiguracji{% endblock %}
{% block content %}
<style>
.kreator-wrap{max-width:700px}
.kreator-detail{background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);margin-bottom:12px;box-shadow:var(--shadow);overflow:hidden}
.kreator-detail summary{padding:16px;cursor:pointer;font-weight:700;font-size:0.95rem;list-style:none;display:flex;align-items:center;gap:10px;transition:background 0.2s}
.kreator-detail summary:hover{background:var(--accent-soft)}
.kreator-detail summary .chevron{margin-left:auto;font-size:0.7rem;color:var(--text-muted);transition:transform 0.2s}
.kreator-detail[open] summary .chevron{transform:rotate(180deg)}
.kreator-detail-body{padding:0 16px 16px}
.kreator-detail-body .form-group{margin-bottom:10px}
.kreator-detail-body .form-control{font-family:monospace;font-size:0.85rem}
.kreator-hint{font-size:0.78rem;color:var(--text-muted);margin-bottom:12px;line-height:1.5}
.kreator-hint a{color:var(--accent)}
.status-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:0.88rem}
.status-grid-item{display:flex;align-items:center;gap:6px;padding:6px 0}
</style>

<div class="kreator-wrap">

{% if saved_count %}
<div class="alert alert-success" style="text-align:center;font-weight:600">Zapisano {{ saved_count }} kluczy API!</div>
{% elif is_welcome %}
<div class="card" style="text-align:center;padding:24px;background:var(--accent-soft);border-color:rgba(99,102,241,0.3)">
    <div style="font-size:1.5rem;margin-bottom:8px">👋</div>
    <div style="font-weight:700;font-size:1.1rem;margin-bottom:5px">Witaj w systemie!</div>
    <div style="font-size:0.85rem;color:var(--text-muted)">Skonfiguruj klucze API zeby odblokowac pelnie mozliwosci.<br>Mozesz to zrobic teraz lub wrocic pozniej z Ustawien.</div>
</div>
{% endif %}

<div class="alert" style="background:var(--blue-soft);border:1px solid rgba(59,130,246,0.2);color:var(--blue);margin-bottom:20px">
    Wypelnij klucze API dla serwisow z ktorych korzystasz. Kazdy serwis mozna skonfigurowac niezaleznie.
</div>

<!-- STATUS OVERVIEW -->
<div class="card" style="margin-bottom:20px">
    <div class="card-header">
        <div class="card-title">Status integracji</div>
    </div>
    <div class="status-grid">
        {% for dot, name in status_items %}
        <div class="status-grid-item">{{ dot }} {{ name }}</div>
        {% endfor %}
    </div>
</div>

<form method="POST" action="/ustawienia/kreator/save">
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

{% for section in sections %}
<details class="kreator-detail" {% if section.get('always_closed') %}{% elif section.get('open_condition') == 'support_nodata' and support_nodata %}open{% elif not section.get('open_condition') and not cfg.get(section.key) %}open{% endif %}>
    <summary>
        {{ '●' if cfg.get(section.key) else '●' }} {{ section.icon }} {{ section.title }}
        <span class="chevron">▼</span>
    </summary>
    <div class="kreator-detail-body">
        <div class="kreator-hint">{{ section.hint | safe }}</div>
        {% for field in section.fields %}
        <div class="form-group">
            <label>{{ field.label }}</label>
            {% if field.type == 'select' %}
            <select name="{{ field.name }}" class="form-control" style="font-size:0.9rem;padding:10px">
                {% for val, label in field.options %}
                <option value="{{ val }}" {{ 'selected' if cfg.get(field.name, '') == val else '' }}>{{ label }}</option>
                {% endfor %}
            </select>
            {% else %}
            <input type="{{ field.type }}" name="{{ field.name }}" value="{{ cfg.get(field.name, '') }}"
                placeholder="{{ field.get('placeholder', '') }}"
                class="form-control" {% if field.get('mono', True) %}style="font-family:monospace;font-size:0.85rem"{% endif %}>
            {% endif %}
        </div>
        {% endfor %}
        {% if section.get('has_test') %}
        <div id="vpsTestResult" style="font-size:0.8rem;margin-top:8px"></div>
        <button type="button" onclick="testVps()" class="btn btn-secondary btn-sm" style="margin-top:6px;width:auto">
            Test polaczenia
        </button>
        {% endif %}
    </div>
</details>
{% endfor %}

<button type="submit" class="btn btn-primary" style="font-size:1.05rem;padding:16px;margin-top:8px">ZAPISZ WSZYSTKO</button>

</form>

<a href="/ustawienia" class="back" style="margin-top:16px">← Powrot do ustawien</a>

</div>

<script>
function testVps() {
    var url = document.querySelector('input[name=rembg_vps_url]').value.trim();
    var res = document.getElementById('vpsTestResult');
    if(!url) { res.innerHTML='<span style="color:var(--red)">Wpisz URL!</span>'; return; }
    res.innerHTML='<span style="color:var(--yellow)">Testowanie...</span>';
    fetch(url.replace(/\/$/, '') + '/health')
        .then(r => r.json())
        .then(d => {
            if(d.status === 'ok' && d.rembg) {
                res.innerHTML='<span style="color:var(--green)">Polaczenie OK! Rembg dziala.</span>';
            } else {
                res.innerHTML='<span style="color:var(--red)">Serwer odpowiada ale rembg=' + d.rembg + '</span>';
            }
        })
        .catch(e => {
            res.innerHTML='<span style="color:var(--red)">Brak polaczenia: ' + e.message + '</span>';
        });
}
</script>
{% endblock %}
'''
    return render_template_string(KREATOR_TEMPLATE,
        version=current_app.config.get('VERSION', ''),
        brand_name=current_app.config.get('BRAND_NAME', 'Akces Hub'),
        current_user=session.get('user'),
        cfg=cfg,
        saved_count=saved_count,
        is_welcome=is_welcome,
        status_items=status_items,
        sections=sections,
        support_nodata=support_nodata,
    )


@ustawienia_bp.route('/ustawienia/nadawca/save', methods=['POST'])
def ustawienia_nadawca_save():
    """Zapisuje dane nadawcy na etykiecie wysyłkowej"""
    from modules.database import set_config, invalidate_config_cache

    fields = {
        'firma_imie': request.form.get('firma_imie', '').strip(),
        'firma_nazwisko': request.form.get('firma_nazwisko', '').strip(),
        'firma_nazwa': request.form.get('firma_nazwa', '').strip(),
        'firma_ulica': request.form.get('firma_ulica', '').strip(),
        'allegro_postcode': request.form.get('allegro_postcode', '').strip(),
        'allegro_city': request.form.get('allegro_city', '').strip(),
        'firma_email': request.form.get('firma_email', '').strip(),
        'firma_telefon': request.form.get('firma_telefon', '').strip(),
    }

    for key, val in fields.items():
        set_config(key, val)

    invalidate_config_cache()
    flash('Dane nadawcy zapisane!', 'success')
    return redirect('/ustawienia#nadawca')


@ustawienia_bp.route('/ustawienia/kreator/save', methods=['POST'])
def ustawienia_kreator_save():
    """Zapisuje wszystkie klucze API z kreatora"""
    from modules.database import set_config, invalidate_config_cache

    keys = [
        'allegro_client_id', 'allegro_client_secret', 'allegro_redirect_uri',
        'telegram_bot_token', 'telegram_chat_id', 'support_chat_id',
        'gemini_api_key', 'perplexity_api_key',
        'ngrok_auth_token', 'ngrok_domain',
        'olx_client_id', 'olx_client_secret', 'olx_redirect_uri',
        'support_email', 'support_phone', 'support_info',
        'rembg_vps_url', 'rembg_vps_key',
    ]

    saved = 0
    for key in keys:
        val = request.form.get(key, '').strip()
        if val:
            set_config(key, val)
            saved += 1

    invalidate_config_cache()

    # Reinit Gemini client if key changed
    gemini_key = request.form.get('gemini_api_key', '').strip()
    if gemini_key:
        try:
            import google.generativeai as genai
            current_app.config['GEMINI_CLIENT'] = genai.Client(api_key=gemini_key)
        except Exception:
            pass

    return redirect(f'/ustawienia/kreator?saved={saved}')


@ustawienia_bp.route('/ustawienia/save', methods=['POST'])
def ustawienia_save():
    from modules.database import set_config

    base_url = request.form.get('app_base_url', 'http://localhost:5000').strip()
    # Usun trailing slash
    base_url = base_url.rstrip('/')

    set_config('app_base_url', base_url)

    return redirect('/ustawienia')


@ustawienia_bp.route('/ustawienia/integracje-parametry', methods=['POST'])
def ustawienia_integracje_parametry():
    """Zapisuje parametry integracji (DPD cennik, warunki zwrotow/reklamacji)"""
    from modules.database import set_config
    for key in ('dpd_cennik_id', 'zwroty_warunki_id', 'reklamacje_warunki_id'):
        val = request.form.get(key, '').strip()
        if val:
            set_config(key, val)
    return redirect('/ustawienia')


@ustawienia_bp.route('/ustawienia/retencja', methods=['POST'])
def ustawienia_retencja():
    """Zapisuje ustawienia retencji danych (RODO)"""
    from modules.database import set_config
    val = request.form.get('data_retention_years', '5').strip()
    if val not in ('0', '3', '5', '7'):
        val = '5'
    set_config('data_retention_years', val)
    return redirect('/ustawienia')


@ustawienia_bp.route('/ustawienia/modules', methods=['POST'])
def ustawienia_modules():
    """Zapisuje wlaczone/wylaczone moduly"""
    from modules.database import set_config, invalidate_config_cache
    module_names = ['paletomat', 'magazynier', 'allegro', 'olx', 'vinted', 'telegram']
    for name in module_names:
        val = '1' if request.form.get(f'module_{name}') else '0'
        set_config(f'module_{name}', val)
    invalidate_config_cache()
    return redirect('/ustawienia')


@ustawienia_bp.route('/ustawienia/branding', methods=['POST'])
def ustawienia_branding():
    """Zapisuje branding + logo"""
    from modules.database import set_config, invalidate_config_cache
    brand_name = request.form.get('brand_name', 'AKCES HUB').strip()
    brand_color = request.form.get('brand_color', '#6366f1').strip()
    set_config('brand_name', brand_name)
    set_config('brand_color', brand_color)

    # Logo upload
    logo = request.files.get('brand_logo')
    if logo and logo.filename:
        ext = logo.filename.rsplit('.', 1)[-1].lower()
        if ext in ('png', 'jpg', 'jpeg'):
            from PIL import Image
            import io
            img_data = logo.read()
            if len(img_data) <= 512 * 1024:  # max 500KB
                img = Image.open(io.BytesIO(img_data))
                # Resize if too large (max 200px height)
                if img.height > 200:
                    ratio = 200 / img.height
                    img = img.resize((int(img.width * ratio), 200), Image.LANCZOS)
                logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'static', 'brand_logo.png')
                img.save(logo_path, 'PNG', optimize=True)

    invalidate_config_cache()
    return redirect('/ustawienia')


@ustawienia_bp.route('/ustawienia/ngrok-token', methods=['POST'])
def ustawienia_ngrok_token():
    from modules.database import set_config
    token = request.form.get('ngrok_token', '').strip()
    domain = request.form.get('ngrok_domain', '').strip()
    if token:
        set_config('ngrok_auth_token', token)
    set_config('ngrok_domain', domain)
    return redirect('/ustawienia')


@ustawienia_bp.route('/ustawienia/email', methods=['POST'])
def ustawienia_email():
    """Zapisuje konfiguracje email"""
    from modules.email_reports import get_email_config, save_email_config

    config = get_email_config()

    email = request.form.get('email', '').strip()
    password = request.form.get('password', '').strip()
    recipient = request.form.get('recipient', '').strip()
    enabled = 'enabled' in request.form

    config['email'] = email
    if password:  # Tylko jesli wpisano nowe haslo
        config['password'] = password
    config['recipient'] = recipient
    config['enabled'] = enabled

    save_email_config(config)

    return redirect('/ustawienia')


@ustawienia_bp.route('/ustawienia/dostawcy', methods=['POST'])
def ustawienia_dostawcy():
    """Zapisuje liste custom dostawcow"""
    raw = request.form.get('custom_dostawcy', '').strip()
    # Oczysc: usun puste, trim
    cleaned = ','.join([d.strip() for d in raw.split(',') if d.strip()])
    set_config('custom_dostawcy', cleaned)
    return redirect('/ustawienia')


@ustawienia_bp.route('/ustawienia/smtp', methods=['POST'])
def ustawienia_smtp():
    """Zapisuje konfiguracje SMTP (license mailer)"""
    from modules.database import set_config, invalidate_config_cache

    smtp_host = request.form.get('smtp_host', '').strip()
    smtp_port = request.form.get('smtp_port', '587').strip()
    smtp_user = request.form.get('smtp_user', '').strip()
    smtp_password = request.form.get('smtp_password', '').strip()
    admin_email = request.form.get('admin_email', '').strip()

    set_config('smtp_host', smtp_host)
    set_config('smtp_port', smtp_port)
    set_config('smtp_user', smtp_user)
    if smtp_password:  # Only update if a new password was entered
        set_config('smtp_password', smtp_password)
    set_config('admin_email', admin_email)

    invalidate_config_cache()
    return redirect('/ustawienia')


@ustawienia_bp.route('/ustawienia/smtp-test', methods=['POST'])
def ustawienia_smtp_test():
    """Wysyla testowy email SMTP"""
    from modules.license_mailer import send_email, get_smtp_config

    cfg = get_smtp_config()
    admin_email = cfg['admin_email']

    if not admin_email:
        return '<html><body style="background:#0a0a0f;color:#ef4444;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh"><div>Brak admin_email w konfiguracji SMTP!</div></body></html>'

    html_body = '''
    <div style="font-family:Arial;max-width:600px;margin:0 auto;background:#1a1a2e;color:#e2e8f0;padding:30px;border-radius:12px">
        <h2 style="color:#22c55e">Test SMTP</h2>
        <p>Konfiguracja SMTP dziala poprawnie. Powiadomienia o licencjach beda wysylane na ten adres.</p>
    </div>
    '''
    ok = send_email(admin_email, 'Test SMTP — Akces Hub', html_body)

    if ok:
        color, msg = '#22c55e', 'Testowy email wyslany!'
    else:
        color, msg = '#ef4444', 'Blad wysylania. Sprawdz konfiguracje SMTP.'

    return f'<html><body style="background:#0a0a0f;color:{color};font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh"><div>{msg}</div></body></html>'


# ============================================================
# RAPORTY
# ============================================================
@ustawienia_bp.route('/raport/podglad')
def raport_podglad():
    """Podglad raportu tygodniowego"""
    from modules.email_reports import generate_weekly_report
    html = generate_weekly_report()
    return html

@ustawienia_bp.route('/raport/dzienny')
def raport_dzienny_podglad():
    """Podglad raportu dziennego z analiza palet"""
    from modules.email_reports import generate_daily_report
    return generate_daily_report()

@ustawienia_bp.route('/raport/dzienny/wyslij')
def raport_dzienny_wyslij():
    """Wysyla raport dzienny na email"""
    from modules.email_reports import send_daily_report
    success, msg = send_daily_report()
    color = '#22c55e' if success else '#ef4444'
    icon = 'Wyslano!' if success else f'Blad: {msg}'
    return f'<html><body style="background:#0a0a0f;color:{color};font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh"><div>{icon}</div></body></html>'


@ustawienia_bp.route('/raport/wyslij')
def raport_wyslij():
    """Wysyla raport tygodniowy na email"""
    from modules.email_reports import send_weekly_report, get_email_config

    config = get_email_config()

    if not config.get('enabled'):
        return '''
        <html><head><meta http-equiv="refresh" content="3;url=/ustawienia"></head>
        <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
            <div style="text-align:center">
                <div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined>warning</span></div>
                <div style="font-size:1.2rem;color:#f59e0b">Email nie jest wlaczony!</div>
                <div style="color:#64748b;margin-top:10px">Wlacz w ustawieniach</div>
            </div>
        </body></html>
        '''

    success, msg = send_weekly_report()

    if success:
        return f'''
        <html><head><meta http-equiv="refresh" content="3;url=/ustawienia"></head>
        <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
            <div style="text-align:center">
                <div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined style=color:#22c55e>check_circle</span></div>
                <div style="font-size:1.2rem">Raport wyslany!</div>
                <div style="color:#64748b;margin-top:10px">Sprawdz email: {config.get('recipient') or config.get('email')}</div>
            </div>
        </body></html>
        '''
    else:
        return f'''
        <html><head><meta http-equiv="refresh" content="5;url=/ustawienia"></head>
        <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
            <div style="text-align:center">
                <div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined style=color:#ef4444>cancel</span></div>
                <div style="font-size:1.2rem;color:#ef4444">Blad wysylania!</div>
                <div style="color:#64748b;margin-top:10px;max-width:400px">{msg}</div>
            </div>
        </body></html>
        '''


# ============================================================
# RESET / DANGER ZONE
# ============================================================
@ustawienia_bp.route('/ustawienia/reset-sprzedaze', methods=['POST'])
def reset_sprzedaze():
    """Czysci historie sprzedazy"""
    if session.get('rola') != 'admin':
        return 'Brak uprawnien (tylko admin)', 403
    from modules.database import get_db
    conn = get_db()
    conn.execute('DELETE FROM sprzedaze')
    conn.commit()

    return '''
    <html><head><meta http-equiv="refresh" content="2;url=/ustawienia"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined style=color:#22c55e>check_circle</span></div>
            <div style="font-size:1.2rem">Historia sprzedazy wyczyszczona!</div>
            <div style="color:#64748b;margin-top:10px">Przekierowuje...</div>
        </div>
    </body></html>
    '''


@ustawienia_bp.route('/ustawienia/reset-magazyn', methods=['POST'])
def reset_magazyn():
    """Czysci wszystkie produkty z magazynu"""
    if session.get('rola') != 'admin':
        return 'Brak uprawnien (tylko admin)', 403
    from modules.database import get_db
    conn = get_db()
    cnt = conn.execute('SELECT COUNT(*) FROM produkty').fetchone()[0]
    conn.execute('DELETE FROM produkty')
    conn.commit()

    return f'''
    <html><head><meta http-equiv="refresh" content="2;url=/ustawienia"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined style=color:#22c55e>check_circle</span></div>
            <div style="font-size:1.2rem">Magazyn wyczyszczony!</div>
            <div style="color:#64748b;margin-top:10px">Usunieto {cnt} produktow</div>
        </div>
    </body></html>
    '''


@ustawienia_bp.route('/ustawienia/reset-palety', methods=['POST'])
def reset_palety():
    """Czysci wszystkie palety i powiazane produkty (takze ze scraped)"""
    if session.get('rola') != 'admin':
        return 'Brak uprawnien (tylko admin)', 403
    from modules.database import get_db
    conn = get_db()
    palety_cnt = conn.execute('SELECT COUNT(*) FROM palety').fetchone()[0]
    produkty_cnt = conn.execute('SELECT COUNT(*) FROM produkty WHERE paleta_id IS NOT NULL').fetchone()[0]

    # NOWE: Pobierz ASINy produktow z palet
    asiny = conn.execute('SELECT DISTINCT asin FROM produkty WHERE paleta_id IS NOT NULL AND asin != ""').fetchall()
    asiny_list = [row[0] for row in asiny if row[0]]

    # NOWE: Usun te produkty ze scraped (Paletomat)
    scraped_cnt = 0
    if asiny_list:
        placeholders = ','.join(['?' for _ in asiny_list])
        scraped_cnt = conn.execute('DELETE FROM scraped WHERE asin IN (' + placeholders + ')', asiny_list).rowcount

    # Usun produkty i palety
    conn.execute('DELETE FROM produkty WHERE paleta_id IS NOT NULL')
    conn.execute('DELETE FROM palety')
    conn.commit()

    return f'''
    <html><head><meta http-equiv="refresh" content="3;url=/ustawienia"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined style=color:#22c55e>check_circle</span></div>
            <div style="font-size:1.2rem">Palety wyczyszczone!</div>
            <div style="color:#64748b;margin-top:10px">
                Usunieto {palety_cnt} palet, {produkty_cnt} produktow z magazynu
                {f' i {scraped_cnt} produktow ze scraped' if scraped_cnt > 0 else ''}
            </div>
        </div>
    </body></html>
    '''


@ustawienia_bp.route('/ustawienia/reset-scraped', methods=['POST'])
def reset_scraped():
    """Czysci zescrapowane produkty z Palatomatu"""
    if session.get('rola') != 'admin':
        return 'Brak uprawnien (tylko admin)', 403
    from modules.database import get_db
    conn = get_db()
    cnt = conn.execute('SELECT COUNT(*) FROM scraped').fetchone()[0]
    conn.execute('DELETE FROM scraped')
    conn.commit()

    return f'''
    <html><head><meta http-equiv="refresh" content="2;url=/ustawienia"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined style=color:#22c55e>check_circle</span></div>
            <div style="font-size:1.2rem">Scraped wyczyszczony!</div>
            <div style="color:#64748b;margin-top:10px">Usunieto {cnt} zescrapowanych produktow</div>
        </div>
    </body></html>
    '''


# ============================================================
# BAZA DANYCH - UPLOAD / DOWNLOAD
# ============================================================
@ustawienia_bp.route('/ustawienia/upload-db', methods=['POST'])
def upload_db():
    """Wgraj plik bazy danych"""
    import sqlite3 as sq
    from datetime import datetime as dt

    f = request.files.get('db_file')
    if not f or not f.filename.endswith('.db'):
        return '<html><body style="background:#0a0a0f;color:#ef4444;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh"><div>Wybierz plik .db</div></body></html>'

    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(app_dir, 'akces_hub.db')

    # 1. Backup aktualnej bazy
    backup_dir = os.path.join(app_dir, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    ts = dt.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(backup_dir, f'pre_upload_{ts}.db')
    if os.path.exists(db_path):
        try:
            src = sq.connect(db_path)
            dst = sq.connect(backup_path)
            src.backup(dst)
            dst.close()
            src.close()
        except Exception:
            import shutil
            shutil.copy2(db_path, backup_path)

    # 2. Zapisz uploadowany plik do temp
    tmp_path = db_path + '.upload_tmp'
    f.save(tmp_path)

    # 3. Sprawdz czy to poprawna baza SQLite
    try:
        test_conn = sq.connect(tmp_path)
        test_conn.execute('SELECT COUNT(*) FROM palety')
        test_conn.close()
    except Exception as e:
        os.remove(tmp_path)
        return f'''<html><body style="background:#0a0a0f;color:#ef4444;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh">
        <div style="text-align:center"><div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined style=color:#ef4444>cancel</span></div>
        <div>Nieprawidlowy plik bazy: {e}</div>
        <a href="/ustawienia" style="color:#818cf8;margin-top:20px;display:block">← Powrot</a></div></body></html>'''

    # 4. Podmien baze
    import shutil
    shutil.move(tmp_path, db_path)

    return '''<html><head><meta http-equiv="refresh" content="2;url=/ustawienia"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined style=color:#22c55e>check_circle</span></div>
            <div style="font-size:1.2rem">Baza danych wgrana!</div>
            <div style="color:#64748b;margin-top:10px">Backup starej bazy zapisany. Przekierowywanie...</div>
        </div>
    </body></html>'''


@ustawienia_bp.route('/ustawienia/download-db')
def download_db():
    """Pobierz aktualna baze danych"""
    from flask import send_file
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(app_dir, 'akces_hub.db')
    if not os.path.exists(db_path):
        return 'Brak bazy', 404
    from datetime import datetime as dt
    ts = dt.now().strftime('%Y%m%d_%H%M%S')
    return send_file(db_path, as_attachment=True, download_name=f'akces_hub_{ts}.db')


# ============================================================
# ADMIN - AKTUALIZACJA SYSTEMU
# ============================================================
@ustawienia_bp.route('/admin/update-git', methods=['POST'])
def admin_update_git():
    """Aktualizacja systemu -- git pull + pip install + restart"""
    import subprocess
    from html import escape

    if session.get('rola') != 'admin':
        return 'Brak uprawnien', 403

    page_style = 'background:#0a0a0f;color:#e2e8f0;font-family:monospace;padding:40px;white-space:pre-wrap'
    logs = []
    back = '<a href="/ustawienia" style="color:#818cf8;text-decoration:none">← Powrot do ustawien</a>'

    try:
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # 1. Backup bazy
        logs.append('[1/4] Backup bazy...')
        db_path = os.path.join(app_dir, 'akces_hub.db')
        if os.path.exists(db_path):
            import sqlite3 as sq
            from datetime import datetime as dt
            backup_dir = os.path.join(app_dir, 'backups')
            os.makedirs(backup_dir, exist_ok=True)
            ts = dt.now().strftime('%Y%m%d_%H%M%S')
            bp = os.path.join(backup_dir, f'pre_update_{ts}.db')
            src = sq.connect(db_path)
            dst = sq.connect(bp)
            src.backup(dst)
            dst.close()
            src.close()
            logs.append(f'  -> Backup OK ({os.path.getsize(bp)/1024/1024:.1f} MB)')
        else:
            logs.append('  -> Brak bazy')

        # 2. Pobierz najnowszy kod
        logs.append('[2/4] Pobieranie aktualizacji...')
        github_repo = get_config('github_repo', 'Trupson2/akces-hub')
        github_token = get_config('github_token', '')

        # Najpierw probuj git pull (jesli jest repo + token)
        updated_via_git = False
        if os.path.isdir(os.path.join(app_dir, '.git')):
            # Ustaw token w git URL jesli dostepny
            if github_token:
                remote_url = f'https://{github_token}@github.com/{github_repo}.git'
                subprocess.run(['git', 'remote', 'set-url', 'origin', remote_url],
                              cwd=app_dir, capture_output=True, timeout=10)
            r = subprocess.run(['git', 'pull', '--ff-only', 'origin', 'main'],
                              cwd=app_dir, capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and 'Already up to date' not in r.stdout:
                logs.append(f'  -> Git pull: {r.stdout.strip()}')
                updated_via_git = True
            elif r.returncode == 0:
                logs.append(f'  -> {r.stdout.strip()}')
                updated_via_git = True

        # Fallback: pobierz ZIP z tokenem (prywatne repo)
        if not updated_via_git:
            import urllib.request, zipfile, shutil
            tmp_zip = os.path.join(app_dir, '_update.zip')
            tmp_dir = os.path.join(app_dir, '_update_tmp')
            skip_patterns = {'venv', 'backups', '__pycache__', '.git', 'akces_hub.db',
                             'cloud_exports', '_update_tmp', 'node_modules', '.secret_key'}
            try:
                zip_url = f'https://api.github.com/repos/{github_repo}/zipball/main'
                req_obj = urllib.request.Request(zip_url)
                if github_token:
                    req_obj.add_header('Authorization', f'token {github_token}')
                    req_obj.add_header('Accept', 'application/vnd.github+json')
                with urllib.request.urlopen(req_obj, timeout=60) as resp:
                    with open(tmp_zip, 'wb') as fout:
                        fout.write(resp.read())
                file_size = os.path.getsize(tmp_zip)
                if file_size < 1000:
                    os.remove(tmp_zip)
                    logs.append('  -> Blad: nie mozna pobrac (sprawdz github_token w config)')
                else:
                    logs.append(f'  -> Pobrano ({file_size/1024:.0f} KB)')
                    if os.path.exists(tmp_dir):
                        shutil.rmtree(tmp_dir)
                    os.makedirs(tmp_dir)
                    with zipfile.ZipFile(tmp_zip, 'r') as zf:
                        zf.extractall(tmp_dir)
                    os.remove(tmp_zip)

                    extracted = os.listdir(tmp_dir)
                    src_dir = tmp_dir
                    if len(extracted) == 1 and os.path.isdir(os.path.join(tmp_dir, extracted[0])):
                        src_dir = os.path.join(tmp_dir, extracted[0])

                    file_count = 0
                    for root, dirs, files in os.walk(src_dir):
                        dirs[:] = [d for d in dirs if d not in skip_patterns]
                        rel = os.path.relpath(root, src_dir)
                        for fname in files:
                            if fname.endswith(('.pyc', '.db')) or fname in skip_patterns:
                                continue
                            src_file = os.path.join(root, fname)
                            if rel == '.':
                                dst_file = os.path.join(app_dir, fname)
                            else:
                                dst_dir_path = os.path.join(app_dir, rel)
                                os.makedirs(dst_dir_path, exist_ok=True)
                                dst_file = os.path.join(dst_dir_path, fname)
                            shutil.copy2(src_file, dst_file)
                            file_count += 1
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    logs.append(f'  -> Zaktualizowano {file_count} plikow')
            except Exception as e:
                logs.append(f'  -> Blad pobierania: {e}')
                if os.path.exists(tmp_zip):
                    os.remove(tmp_zip)
                if os.path.exists(tmp_dir):
                    shutil.rmtree(tmp_dir, ignore_errors=True)

        # 3. Pip install
        logs.append('[3/4] Pip install...')
        req = os.path.join(app_dir, 'requirements.txt')
        venv_pip = os.path.join(app_dir, 'venv', 'bin', 'pip')
        venv_pip_win = os.path.join(app_dir, 'venv', 'Scripts', 'pip.exe')
        if os.path.exists(req):
            if os.path.exists(venv_pip):
                r = subprocess.run([venv_pip, 'install', '-r', req, '--quiet'],
                                  capture_output=True, text=True, timeout=120)
            elif os.path.exists(venv_pip_win):
                r = subprocess.run([venv_pip_win, 'install', '-r', req, '--quiet'],
                                  capture_output=True, text=True, timeout=120)
            else:
                pip_cmd = 'pip3' if os.name != 'nt' else 'pip'
                cmd = [pip_cmd, 'install', '-r', req, '--quiet']
                if os.name != 'nt':
                    cmd.append('--break-system-packages')
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            logs.append('  -> OK' if r.returncode == 0 else f'  -> {r.stderr[:100]}')
        else:
            logs.append('  -> Pomijam (brak requirements.txt)')

        # 4. Restart (wykryj platform i serwis)
        logs.append('[4/4] Restart Flask...')
        restarted = False

        # Linux: probuj systemd
        if os.name != 'nt':
            for svc in ['akces-hub.service', 'akceshub.service']:
                try:
                    r = subprocess.run(['sudo', 'systemctl', 'is-enabled', svc],
                                      capture_output=True, text=True, timeout=5)
                    if r.returncode == 0:
                        r = subprocess.run(['sudo', 'systemctl', 'restart', svc],
                                          capture_output=True, text=True, timeout=30)
                        if r.returncode == 0:
                            logs.append(f'  -> {svc} zrestartowany!')
                            restarted = True
                        else:
                            logs.append(f'  -> {r.stderr[:200]}')
                        break
                except Exception:
                    pass

        # Fallback: restart procesu Python (Windows/Linux bez systemd)
        if not restarted:
            logs.append('  -> Kod zaktualizowany! Zrestartuj aplikacje recznie.')
            logs.append('     (zamknij i uruchom ponownie python app.py)')

        logs.append('')
        logs.append('AKTUALIZACJA ZAKONCZONA!')

        # Wyczysc flage update_available po pomyslnej aktualizacji
        try:
            set_config('update_available', '0')
            set_config('update_check_cache', '')
        except Exception:
            pass

        content = escape('\n'.join(logs))
        return f'''<html><head><meta charset="UTF-8"></head>
        <body style="{page_style}">
        <h2 style="color:#22c55e">Aktualizacja zakonczona!</h2>
        <pre style="font-size:0.85rem">{content}</pre>
        <p style="color:#94a3b8;margin-top:20px">Strona moze byc niedostepna przez kilka sekund po restarcie.</p>
        {back}
        </body></html>'''

    except Exception as e:
        logs.append(f'BLAD: {e}')
        content = escape('\n'.join(logs))
        return f'''<html><head><meta charset="UTF-8"></head>
        <body style="{page_style}">
        <h2 style="color:#ef4444">Blad aktualizacji</h2>
        <pre style="font-size:0.85rem">{content}</pre>
        {back}
        </body></html>'''


@ustawienia_bp.route('/admin/update', methods=['POST'])
def admin_update():
    """Aktualizacja systemu -- upload ZIP + backup + rozpakowanie + restart"""
    import subprocess, zipfile, shutil
    from html import escape

    if session.get('rola') != 'admin':
        return 'Brak uprawnien', 403

    page_style = 'background:#0a0a0f;color:#e2e8f0;font-family:monospace;padding:40px;white-space:pre-wrap'
    logs = []

    def page(title, color, extra=''):
        content = escape('\n'.join(logs))
        back = '<a href="/ustawienia" style="color:#818cf8;text-decoration:none">← Powrot do ustawien</a>'
        return f'''<html><head><meta charset="UTF-8"></head>
        <body style="{page_style}">
        <h2 style="color:{color}">{title}</h2>
        <pre style="font-size:0.85rem">{content}</pre>
        {extra}
        {back}
        </body></html>'''

    try:
        f = request.files.get('update_zip')
        if not f or not f.filename.endswith('.zip'):
            return page('Blad: Wybierz plik ZIP', '#ef4444')

        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        tmp_dir = os.path.join(app_dir, '_update_tmp')
        skip_patterns = {'venv', 'backups', '__pycache__', '.git', 'akces_hub.db',
                         'cloud_exports', '_update_tmp', 'node_modules'}

        # 1. Backup bazy
        logs.append('[1/4] Backup bazy danych...')
        db_path = os.path.join(app_dir, 'akces_hub.db')
        if os.path.exists(db_path):
            backup_dir = os.path.join(app_dir, 'backups')
            os.makedirs(backup_dir, exist_ok=True)
            import sqlite3 as sq
            from datetime import datetime as dt
            ts = dt.now().strftime('%Y%m%d_%H%M%S')
            backup_path = os.path.join(backup_dir, f'pre_update_{ts}.db')
            src = sq.connect(db_path)
            dst = sq.connect(backup_path)
            src.backup(dst)
            dst.close()
            src.close()
            size_mb = os.path.getsize(backup_path) / (1024 * 1024)
            logs.append(f'  -> {backup_path} ({size_mb:.1f} MB)')
        else:
            logs.append('  -> Brak bazy (nowa instalacja?)')

        # 2. Rozpakuj ZIP do temp
        logs.append('[2/4] Rozpakowywanie ZIP...')
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
        os.makedirs(tmp_dir)

        f.save(os.path.join(tmp_dir, 'update.zip'))
        with zipfile.ZipFile(os.path.join(tmp_dir, 'update.zip'), 'r') as zf:
            zf.extractall(tmp_dir)
        os.remove(os.path.join(tmp_dir, 'update.zip'))

        # Znajdz root folder w ZIP (moze byc nested)
        extracted = os.listdir(tmp_dir)
        src_dir = tmp_dir
        if len(extracted) == 1 and os.path.isdir(os.path.join(tmp_dir, extracted[0])):
            src_dir = os.path.join(tmp_dir, extracted[0])

        # Sprawdz czy to prawidlowa paczka (ma app.py?)
        if not os.path.exists(os.path.join(src_dir, 'app.py')):
            shutil.rmtree(tmp_dir)
            logs.append('  -> BLAD: Paczka nie zawiera app.py!')
            return page('Blad: Nieprawidlowa paczka', '#ef4444')

        logs.append(f'  -> Rozpakowano ({len(os.listdir(src_dir))} plikow/folderow)')

        # 3. Kopiuj pliki (pomijaj venv, backups, db, __pycache__)
        logs.append('[3/4] Kopiowanie plikow...')
        updated = 0
        for root, dirs, files in os.walk(src_dir):
            # Filtruj katalogi
            dirs[:] = [d for d in dirs if d not in skip_patterns and not d.endswith('.pyc')]
            rel = os.path.relpath(root, src_dir)

            for fname in files:
                if fname.endswith(('.pyc', '.db')) or fname in skip_patterns:
                    continue
                src_file = os.path.join(root, fname)
                if rel == '.':
                    dst_file = os.path.join(app_dir, fname)
                else:
                    dst_dir_path = os.path.join(app_dir, rel)
                    os.makedirs(dst_dir_path, exist_ok=True)
                    dst_file = os.path.join(dst_dir_path, fname)
                shutil.copy2(src_file, dst_file)
                updated += 1

        logs.append(f'  -> Skopiowano {updated} plikow')

        # Cleanup tmp
        shutil.rmtree(tmp_dir, ignore_errors=True)

        # 4. Restart (wykryj nazwe serwisu automatycznie)
        logs.append('[4/4] Restart serwisu...')
        try:
            service_name = None
            for svc in ['akces-hub.service', 'akceshub.service']:
                r2 = subprocess.run(['sudo', 'systemctl', 'is-enabled', svc],
                                   capture_output=True, text=True, timeout=5)
                if r2.returncode == 0:
                    service_name = svc
                    break
            if service_name:
                result = subprocess.run(
                    ['sudo', 'systemctl', 'restart', service_name],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    logs.append(f'  -> {service_name} zrestartowany!')
                else:
                    logs.append(f'  -> Restart blad: {result.stderr[:200]}')
            else:
                logs.append('  -> Nie znaleziono serwisu. Zrestartuj recznie.')
        except Exception as e:
            logs.append(f'  -> Nie mozna zrestartowac automatycznie: {e}')
            logs.append('  -> Po odswiezeniu strona moze byc niedostepna przez chwile')

        logs.append('')
        logs.append('AKTUALIZACJA ZAKONCZONA!')

        return page('Aktualizacja zakonczona!', '#22c55e',
                     '<p style="color:#94a3b8;margin-top:20px">Strona moze byc niedostepna przez kilka sekund po restarcie.</p>')

    except Exception as e:
        logs.append(f'BLAD: {e}')
        # Cleanup on error
        tmp_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '_update_tmp')
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return page(f'Blad aktualizacji', '#ef4444')


@ustawienia_bp.route('/admin/deploy', methods=['GET', 'POST'])
def admin_deploy():
    """Deploy plikow modulow przez upload HTTP"""
    if request.method == 'POST':
        f = request.files.get('file')
        target = request.form.get('target', '')
        if not f or not target:
            return jsonify({'error': 'Brak pliku lub target'}), 400

        # Tylko dozwolone sciezki
        ALLOWED = {
            'modules/magazynier.py',
            'modules/printer_manager.py',
            'modules/allegro_api.py',
            'modules/olx_api.py',
            'modules/vinted_api.py',
            'modules/database.py',
            'app.py',
        }
        if target not in ALLOWED:
            return jsonify({'error': f'Niedozwolona sciezka: {target}'}), 403

        import shutil
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        full_path = os.path.join(app_dir, target)
        # Backup
        if os.path.exists(full_path):
            shutil.copy2(full_path, full_path + '.bak')

        f.save(full_path)
        return jsonify({'ok': True, 'msg': f'Zapisano {target} ({os.path.getsize(full_path)} bytes). Restart wymagany.'})

    # GET -- formularz
    return '''<!DOCTYPE html><html><head><title>Deploy</title>
    <style>body{font-family:sans-serif;background:#0f172a;color:#e2e8f0;max-width:600px;margin:50px auto;padding:20px}
    select,input,button{padding:10px;margin:8px 0;width:100%;border-radius:8px;border:1px solid #334155;background:#1e293b;color:#fff;font-size:1rem}
    button{background:#22c55e;cursor:pointer;font-weight:600;border:none}
    #result{margin-top:20px;padding:15px;border-radius:8px;display:none}</style></head>
    <body><h1><span class=material-symbols-outlined>inventory_2</span> Deploy modulu</h1>
    <form id="f" enctype="multipart/form-data">
    <label>Modul:</label>
    <select name="target">
    <option value="modules/magazynier.py">modules/magazynier.py</option>
    <option value="modules/printer_manager.py">modules/printer_manager.py</option>
    <option value="modules/allegro_api.py">modules/allegro_api.py</option>
    <option value="modules/olx_api.py">modules/olx_api.py</option>
    <option value="modules/vinted_api.py">modules/vinted_api.py</option>
    <option value="modules/database.py">modules/database.py</option>
    <option value="app.py">app.py</option>
    </select>
    <label>Plik:</label><input type="file" name="file" accept=".py">
    <button type="submit"><span class=material-symbols-outlined>rocket_launch</span> Deploy</button></form>
    <div id="result"></div>
    <script>document.getElementById('f').onsubmit=async e=>{e.preventDefault();
    const r=document.getElementById('result');r.style.display='block';r.style.background='#1e293b';r.textContent='Wysylanie...';
    const fd=new FormData(e.target);const res=await fetch('/admin/deploy',{method:'POST',body:fd});
    const d=await res.json();r.style.background=d.ok?'#166534':'#7f1d1d';r.textContent=d.ok?d.msg:d.error;}</script>
    <p style="margin-top:30px;font-size:0.8rem;color:#64748b">Po deploymencie zrestartuj usluge: <code>sudo systemctl restart akces-hub</code></p>
    </body></html>'''


@ustawienia_bp.route('/admin/przelicz-palety')
def admin_przelicz_palety():
    """Jednorazowe przeliczenie cen palet z sumy cena_netto produktow.
    cena_netto = laczny koszt zakupu produktu (niezmienny, niezalezny od sprzedazy).
    """
    from modules.database import get_db
    conn = get_db()

    kolumny = [desc[0] for desc in conn.execute('PRAGMA table_info(palety)').fetchall()]
    if 'cena_zakupu_netto' not in kolumny:
        try:
            conn.execute('ALTER TABLE palety ADD COLUMN cena_zakupu_netto REAL DEFAULT 0')
            conn.commit()
        except:
            pass

    palety = conn.execute('SELECT id, nazwa, cena_zakupu FROM palety').fetchall()
    updated = 0

    for p in palety:
        # SUM(cena_brutto) - koszt zakupu wszystkich produktow w palecie
        # cena_brutto to LACZNA cena za dany produkt (nie za sztuke)
        suma_brutto = conn.execute(
            'SELECT COALESCE(SUM(cena_brutto), 0) FROM produkty WHERE paleta_id = ?',
            (p['id'],)
        ).fetchone()[0]
        suma_netto = round(suma_brutto / 1.23, 2) if suma_brutto > 0 else 0

        if suma_brutto > 0:
            conn.execute(
                'UPDATE palety SET cena_zakupu = ?, cena_zakupu_netto = ? WHERE id = ?',
                (suma_brutto, suma_netto, p['id'])
            )
            updated += 1

    conn.commit()

    return f"""
    <html>
    <head>
        <meta http-equiv="refresh" content="3;url=/palety">
        <style>body{{background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column;gap:16px}}</style>
    </head>
    <body>
        <div style="font-size:3rem"><span class=material-symbols-outlined style=color:#22c55e>check_circle</span></div>
        <div style="font-size:1.4rem;font-weight:700">Przeliczono {updated} palet!</div>
        <div style="color:#64748b;font-size:0.9rem">cena_zakupu = suma cena_netto x 1.23 (wartosc z importu, stala)</div>
        <div style="color:#3b82f6;font-size:0.85rem">Przekierowanie za 3 sekundy...</div>
    </body>
    </html>
    """


# ============================================================
# SETTINGS - DRUKOWANIE
# ============================================================
@ustawienia_bp.route('/settings/printing', methods=['GET', 'POST'])
def printing_settings():
    """Strona ustawien drukowania"""
    from modules.printing_config import load_config, save_full_config, get_printer_settings

    if request.method == 'POST':
        # Pobierz dane z formularza
        auto_print = request.form.get('auto_print') == 'on'
        printer_type = request.form.get('printer_type', 'niimbot')
        print_copies = int(request.form.get('print_copies', 1))
        ask_before = request.form.get('ask_before_print') == 'on'

        # Zapisz do config
        config = load_config()
        config['auto_print_enabled'] = auto_print
        config['default_printer'] = printer_type
        config['print_copies'] = print_copies
        config['ask_before_print'] = ask_before

        if save_full_config(config):
            flash('<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Ustawienia drukowania zapisane!', 'success')
        else:
            flash('<span class=material-symbols-outlined>warning</span> Blad zapisywania ustawien', 'error')

        return redirect(url_for('ustawienia.printing_settings'))

    # GET - wyswietl formularz
    settings = get_printer_settings()

    return render_template('settings_printing.html',
        auto_print=settings['auto_print'],
        printer=settings['printer'],
        copies=settings['copies'],
        ask_before=settings['ask_before']
    )


@ustawienia_bp.route('/settings/printing/test', methods=['POST'])
def test_print():
    """Testowe drukowanie"""
    printer_type = request.form.get('printer_type', 'niimbot')

    try:
        # Import odpowiedniego modulu drukarki
        if printer_type == 'niimbot':
            from modules.niimbot_print import test_print as niimbot_test
            niimbot_test()
            flash(f'<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Test drukowania na Niimbot B1 zakonczony!', 'success')
        elif printer_type == 'vretti':
            from modules.vretti_print import test_print as vretti_test
            vretti_test()
            flash(f'<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Test drukowania na Vretti 420B zakonczony!', 'success')
        else:
            flash(f'<span class=material-symbols-outlined>warning</span> Nieznany typ drukarki: {printer_type}', 'error')
    except ImportError as e:
        flash(f'<span class=material-symbols-outlined>warning</span> Modul drukarki nie znaleziony: {e}', 'error')
    except Exception as e:
        flash(f'<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Blad drukowania: {e}', 'error')

    return redirect(url_for('ustawienia.printing_settings'))
