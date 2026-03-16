"""
Modul ustawien i administracji -- routes dla /ustawienia/*, /admin/*, /settings/*, /raport/*
"""
from flask import Blueprint, request, redirect, session, flash, jsonify, Response, current_app, url_for, render_template
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

    # Module toggles
    modules_cfg = {
        'paletomat': {'name': 'Paletomat', 'desc': 'Skaner palet, scraping Amazon', 'enabled': is_module_enabled('paletomat')},
        'magazynier': {'name': 'Magazynier', 'desc': 'Zarzadzanie magazynem', 'enabled': is_module_enabled('magazynier')},
        'allegro': {'name': 'Allegro', 'desc': 'Integracja z Allegro', 'enabled': is_module_enabled('allegro')},
        'olx': {'name': 'OLX', 'desc': 'Integracja z OLX', 'enabled': is_module_enabled('olx')},
        'vinted': {'name': 'Vinted', 'desc': 'Integracja z Vinted', 'enabled': is_module_enabled('vinted')},
        'telegram': {'name': 'Telegram', 'desc': 'Bot Telegram', 'enabled': is_module_enabled('telegram')},
    }
    brand_name = get_config('brand_name', 'AKCES HUB')
    brand_color = get_config('brand_color', '#6366f1')

    # Sprawdz czy to ngrok URL
    is_ngrok = 'ngrok' in base_url

    from modules.shared import CSS

    html = CSS + '''
    <div class="container">
        <div class="header">
            <h1>⚙️ USTAWIENIA SYSTEMU</h1>
            <small>Konfiguracja ''' + brand_name + '''</small>
        </div>

        <form action="/ustawienia/save" method="POST">
            <div class="card" style="padding:15px">
                <div style="font-weight:600;margin-bottom:15px">🌐 Adres URL aplikacji (dla QR kodow)</div>

                <div style="padding:12px;background:rgba(234,179,8,0.1);border:1px solid rgba(234,179,8,0.3);border-radius:8px;margin-bottom:15px">
                    <div style="font-size:0.85rem;color:#eab308">
                        <b>⚠️ WAZNE:</b> Zeby QR kody dzialaly z telefonu, wpisz swoj adres ngrok!
                    </div>
                </div>

                <input type="text" name="app_base_url" id="baseUrlInput" value="''' + base_url + '''"
                    placeholder="https://xxx.ngrok-free.dev"
                    class="form-ctrl" style="padding:12px;font-size:1rem;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;width:100%">

                <div style="margin-top:10px;font-size:0.8rem;color:#64748b">
                    ''' + ('✅ Ngrok wykryty - QR kody beda dzialac z telefonu!' if is_ngrok else '⚠️ localhost - QR kody nie beda dzialac z telefonu') + '''
                </div>
            </div>

            <button type="submit" style="width:100%;padding:14px;background:#3b82f6;border:none;border-radius:10px;color:#fff;font-weight:600;font-size:1rem;cursor:pointer;margin-top:10px">💾 ZAPISZ</button>
        </form>

        <!-- RAPORTY EMAIL -->
        <div style="margin-top:20px;padding:15px;background:linear-gradient(135deg,rgba(59,130,246,0.1),rgba(37,99,235,0.1));border:1px solid rgba(59,130,246,0.3);border-radius:12px">
            <div style="font-weight:600;margin-bottom:15px;color:#3b82f6;display:flex;align-items:center;gap:10px">
                📧 Raporty Email
                <span style="font-size:0.75rem;padding:3px 8px;background:''' + ('#22c55e' if email_cfg.get('enabled') else '#64748b') + ''';border-radius:10px;color:#fff">
                    ''' + ('WLACZONE' if email_cfg.get('enabled') else 'WYLACZONE') + '''
                </span>
            </div>

            <form action="/ustawienia/email" method="POST">
                <div style="margin-bottom:12px">
                    <label style="font-size:0.8rem;color:#64748b">Email (Gmail)</label>
                    <input type="email" name="email" value="''' + (email_cfg.get('email') or '') + '''"
                        placeholder="twoj@gmail.com"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:5px">
                </div>

                <div style="margin-bottom:12px">
                    <label style="font-size:0.8rem;color:#64748b">Haslo aplikacji (nie zwykle haslo!)</label>
                    <input type="password" name="password" placeholder="''' + ('••••••••••••••••' if email_cfg.get('password') else 'xxxx xxxx xxxx xxxx') + '''"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:5px">
                </div>

                <div style="margin-bottom:15px">
                    <label style="font-size:0.8rem;color:#64748b">Odbiorca (opcjonalnie, domyslnie = nadawca)</label>
                    <input type="email" name="recipient" value="''' + (email_cfg.get('recipient') or '') + '''"
                        placeholder="Zostaw puste jesli ten sam email"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:5px">
                </div>

                <div style="margin-bottom:15px">
                    <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
                        <input type="checkbox" name="enabled" ''' + ('checked' if email_cfg.get('enabled') else '') + ''' style="width:18px;height:18px">
                        <span style="color:#fff">Wlacz raporty email</span>
                    </label>
                </div>

                <button type="submit" style="width:100%;padding:12px;background:#3b82f6;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">
                    💾 Zapisz konfiguracje email
                </button>
            </form>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:15px">
                <a href="/raport/podglad" target="_blank" style="display:block;text-align:center;padding:10px;background:#1e1e2e;border:1px solid #3b82f6;border-radius:8px;color:#3b82f6;text-decoration:none;font-weight:600;font-size:0.85rem">
                    👁️ Podglad raportu
                </a>
                <a href="/raport/wyslij" onclick="return confirm('Wyslac raport tygodniowy na email?')" style="display:block;text-align:center;padding:10px;background:#22c55e;border-radius:8px;color:#fff;text-decoration:none;font-weight:600;font-size:0.85rem">
                    📤 Wyslij teraz
                </a>
            </div>

            <div style="margin-top:12px;padding:10px;background:#1e1e2e;border-radius:8px;font-size:0.8rem;color:#64748b">
                <b>💡 Jak uzyskac haslo aplikacji Gmail?</b><br>
                1. Wejdz na <a href="https://myaccount.google.com/apppasswords" target="_blank" style="color:#3b82f6">myaccount.google.com/apppasswords</a><br>
                2. Wybierz "Poczta" i "Windows"<br>
                3. Skopiuj 16-znakowe haslo (bez spacji)
            </div>
        </div>


        <!-- Ngrok Token (auto-connect na Pi) -->
        <div style="margin-top:20px;padding:15px;background:linear-gradient(135deg,rgba(139,92,246,0.1),rgba(88,28,135,0.1));border:1px solid rgba(139,92,246,0.3);border-radius:12px">
            <div style="font-weight:600;margin-bottom:10px;color:#8b5cf6">🚀 Ngrok - Zdalny dostep</div>
            <form action="/ustawienia/ngrok-token" method="POST">
                <div style="margin-bottom:12px">
                    <label style="font-size:0.8rem;color:#64748b">Auth Token (z <a href="https://dashboard.ngrok.com/get-started/your-authtoken" target="_blank" style="color:#8b5cf6">dashboard.ngrok.com</a>)</label>
                    <input type="password" name="ngrok_token" value="''' + get_config('ngrok_auth_token', '') + '''"
                        placeholder="2abc...xyz123"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:5px;font-family:monospace">
                </div>
                <div style="margin-bottom:12px">
                    <label style="font-size:0.8rem;color:#64748b">Stala domena (opcjonalnie, np. akceshub.ngrok.dev)</label>
                    <input type="text" name="ngrok_domain" value="''' + get_config('ngrok_domain', '') + '''"
                        placeholder="twoja-domena.ngrok-free.dev"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:5px;font-family:monospace">
                </div>
                <button type="submit" style="width:100%;padding:12px;background:#8b5cf6;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">
                    💾 Zapisz i polacz
                </button>
            </form>
            <div style="margin-top:10px;font-size:0.8rem;color:#64748b">
                Na Raspberry Pi ngrok startuje automatycznie. Token zapisuje sie w bazie danych.
            </div>
        </div>

        <!-- KREATOR API KEYS -->
        <div style="margin-top:20px;padding:15px;background:linear-gradient(135deg,rgba(99,102,241,0.15),rgba(139,92,246,0.15));border:1px solid rgba(99,102,241,0.4);border-radius:12px">
            <a href="/ustawienia/kreator" style="display:flex;align-items:center;gap:12px;text-decoration:none;color:#fff">
                <div style="font-size:2rem">🔧</div>
                <div>
                    <div style="font-weight:700;font-size:1.05rem">Kreator konfiguracji</div>
                    <div style="font-size:0.8rem;color:#94a3b8">Wszystkie klucze API w jednym miejscu (Allegro, Telegram, Gemini, OLX...)</div>
                </div>
                <div style="margin-left:auto;font-size:1.2rem;color:#6366f1">→</div>
            </a>
        </div>

        <!-- KIOSK MODE -->
        <div style="margin-top:20px;padding:15px;background:linear-gradient(135deg,rgba(99,102,241,0.1),rgba(59,130,246,0.1));border:1px solid rgba(99,102,241,0.3);border-radius:12px">
            <div style="font-weight:600;margin-bottom:8px;color:#818cf8">📺 Tryb Kiosk (Raspberry Pi)</div>
            <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:12px">
                Optymalizacja UI dla ekranu dotykowego 7"
            </div>
            <div style="display:flex;gap:10px">
                <a href="/?kiosk=1" style="flex:1;text-align:center;padding:14px;background:#6366f1;border-radius:10px;color:#fff;text-decoration:none;font-weight:600;font-size:0.95rem">
                    ✅ Wlacz Kiosk
                </a>
                <a href="/?kiosk=0" style="flex:1;text-align:center;padding:14px;background:var(--bg-tertiary,#1e1e2e);border:1px solid var(--border-color,#2a2a3a);border-radius:10px;color:#fff;text-decoration:none;font-weight:600;font-size:0.95rem">
                    ❌ Wylacz Kiosk
                </a>
            </div>
        </div>

        <!-- MODULY -->
        <div style="margin-top:20px;padding:15px;background:linear-gradient(135deg,rgba(34,197,94,0.1),rgba(22,163,74,0.1));border:1px solid rgba(34,197,94,0.3);border-radius:12px">
            <div style="font-weight:600;margin-bottom:15px;color:#22c55e">🧩 Moduly systemu</div>
            <form action="/ustawienia/modules" method="POST">
                <div style="display:grid;gap:10px">
                    ''' + ''.join([f'''
                    <label style="display:flex;align-items:center;gap:12px;padding:10px;background:#1e1e2e;border-radius:8px;cursor:pointer">
                        <input type="checkbox" name="module_{key}" {'checked' if mod['enabled'] else ''} style="width:18px;height:18px;accent-color:#22c55e">
                        <div>
                            <div style="font-weight:600;font-size:0.9rem">{mod['name']}</div>
                            <div style="font-size:0.75rem;color:#64748b">{mod['desc']}</div>
                        </div>
                    </label>''' for key, mod in modules_cfg.items()]) + '''
                </div>
                <button type="submit" style="width:100%;padding:12px;background:#22c55e;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer;margin-top:12px">
                    💾 Zapisz moduly
                </button>
            </form>
        </div>

        <!-- BRANDING -->
        <div style="margin-top:20px;padding:15px;background:linear-gradient(135deg,rgba(236,72,153,0.1),rgba(168,85,247,0.1));border:1px solid rgba(236,72,153,0.3);border-radius:12px">
            <div style="font-weight:600;margin-bottom:15px;color:#ec4899">🎨 Branding</div>
            <form action="/ustawienia/branding" method="POST" enctype="multipart/form-data">
                <div style="margin-bottom:12px">
                    <label style="font-size:0.8rem;color:#64748b">Nazwa systemu</label>
                    <input type="text" name="brand_name" value="''' + brand_name + '''"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:5px">
                </div>
                <div style="margin-bottom:12px">
                    <label style="font-size:0.8rem;color:#64748b">Logo firmy (PNG/JPG, max 500KB)</label>
                    <div style="display:flex;gap:10px;align-items:center;margin-top:5px">
                        ''' + (f'<img src="/static/brand_logo.png?v={int(__import__("time").time())}" style="height:40px;border-radius:6px">' if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'static', 'brand_logo.png')) else '<span style="color:#64748b;font-size:0.85rem">Brak logo</span>') + '''
                        <input type="file" name="brand_logo" accept="image/png,image/jpeg"
                            style="flex:1;padding:8px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#e2e8f0;font-size:0.85rem">
                    </div>
                </div>
                <div style="margin-bottom:12px">
                    <label style="font-size:0.8rem;color:#64748b">Kolor przewodni</label>
                    <div style="display:flex;gap:10px;align-items:center;margin-top:5px">
                        <input type="color" name="brand_color" value="''' + brand_color + '''" style="width:50px;height:38px;border:none;background:none;cursor:pointer">
                        <input type="text" value="''' + brand_color + '''"
                            style="flex:1;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-family:monospace"
                            onchange="this.previousElementSibling.value=this.value" readonly>
                    </div>
                </div>
                <button type="submit" style="width:100%;padding:12px;background:linear-gradient(135deg,#ec4899,#a855f7);border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">
                    💾 Zapisz branding
                </button>
            </form>
        </div>

        <!-- UZYTKOWNICY -->
        <div style="margin-top:20px;display:grid;gap:10px">
            <a href="/auth/users" style="display:block;text-align:center;padding:14px;background:linear-gradient(135deg,rgba(99,102,241,0.2),rgba(139,92,246,0.2));border:1px solid rgba(99,102,241,0.3);border-radius:12px;color:#818cf8;text-decoration:none;font-weight:600;font-size:1rem">
                👥 Zarzadzanie uzytkownikami
            </a>
            <a href="/auth/zmien-haslo" style="display:block;text-align:center;padding:14px;background:linear-gradient(135deg,rgba(245,158,11,0.1),rgba(234,179,8,0.1));border:1px solid rgba(245,158,11,0.3);border-radius:12px;color:#f59e0b;text-decoration:none;font-weight:600;font-size:1rem">
                🔒 Zmien haslo
            </a>
        </div>

        <!-- AKTUALIZACJA SYSTEMU — przeniesiona do Narzędzia -->
        <div style="margin-top:20px;padding:15px;background:rgba(34,197,94,0.05);border:1px solid rgba(34,197,94,0.2);border-radius:12px;text-align:center">
            <a href="/narzedzia" style="color:#22c55e;font-weight:600;text-decoration:none;font-size:1rem">🔄 Aktualizacja systemu → Narzędzia</a>
        </div>

        <!-- DANGER ZONE -->
        <div style="margin-top:20px;padding:15px;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:12px">
            <div style="font-weight:600;margin-bottom:10px;color:#ef4444">⚠️ Strefa niebezpieczna</div>
            <div style="font-size:0.85rem;color:#94a3b8;margin-bottom:15px">
                Wyczysc testowe dane. Ta operacja jest nieodwracalna!
            </div>

            <div style="display:grid;gap:10px">
                <form method="POST" action="/ustawienia/reset-sprzedaze" onsubmit="return confirm('Na pewno wyczyscic historie sprzedazy?')">
                    <button type="submit" style="width:100%;padding:12px;background:#ef4444;border-radius:8px;color:#fff;border:none;font-weight:600;cursor:pointer;font-size:0.9rem">
                        🗑️ Wyczysc historie sprzedazy
                    </button>
                </form>

                <form method="POST" action="/ustawienia/reset-magazyn" onsubmit="return confirm('⚠️ UWAGA!\\n\\nTo usunie WSZYSTKIE produkty z magazynu!\\n\\nNa pewno kontynuowac?')">
                    <button type="submit" style="width:100%;padding:12px;background:#dc2626;border-radius:8px;color:#fff;border:none;font-weight:600;cursor:pointer;font-size:0.9rem">
                        🗑️ Wyczysc magazyn (produkty)
                    </button>
                </form>

                <form method="POST" action="/ustawienia/reset-palety" onsubmit="return confirm('⚠️ UWAGA!\\n\\nTo usunie WSZYSTKIE palety i powiazane produkty!\\n\\nNa pewno kontynuowac?')">
                    <button type="submit" style="width:100%;padding:12px;background:#b91c1c;border-radius:8px;color:#fff;border:none;font-weight:600;cursor:pointer;font-size:0.9rem">
                        🗑️ Wyczysc palety
                    </button>
                </form>

                <form method="POST" action="/ustawienia/reset-scraped" onsubmit="return confirm('Wyczyscic zescrapowane produkty z Palatomatu?')">
                    <button type="submit" style="width:100%;padding:12px;background:#991b1b;border-radius:8px;color:#fff;border:none;font-weight:600;cursor:pointer;font-size:0.9rem">
                        🗑️ Wyczysc scraped (Paletomat)
                    </button>
                </form>
            </div>
        </div>

        <a href="/" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:20px">← Powrot</a>
    </div>
    '''
    return html


@ustawienia_bp.route('/ustawienia/kreator')
def ustawienia_kreator():
    """Kreator konfiguracji - wszystkie klucze API w jednym miejscu"""
    from modules.database import get_config
    from modules.shared import CSS

    # Pobierz wszystkie klucze
    cfg = {
        'allegro_client_id': get_config('allegro_client_id', ''),
        'allegro_client_secret': get_config('allegro_client_secret', ''),
        'allegro_redirect_uri': get_config('allegro_redirect_uri', ''),
        'telegram_bot_token': get_config('telegram_bot_token', ''),
        'telegram_chat_id': get_config('telegram_chat_id', ''),
        'support_chat_id': get_config('support_chat_id', ''),
        'gemini_api_key': get_config('gemini_api_key', ''),
        'perplexity_api_key': get_config('perplexity_api_key', ''),
        'ngrok_auth_token': get_config('ngrok_auth_token', ''),
        'ngrok_domain': get_config('ngrok_domain', ''),
        'olx_client_id': get_config('olx_client_id', ''),
        'olx_client_secret': get_config('olx_client_secret', ''),
        'olx_redirect_uri': get_config('olx_redirect_uri', ''),
    }

    def status_dot(key):
        return '🟢' if cfg.get(key) else '🔴'

    def mask(val):
        if not val:
            return ''
        if len(val) <= 8:
            return '••••••••'
        return val[:4] + '•' * (len(val) - 8) + val[-4:]

    saved_count = request.args.get('saved', '')
    saved_msg = ''
    if saved_count:
        saved_msg = f'<div style="padding:12px;background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.4);border-radius:10px;margin-bottom:15px;color:#22c55e;font-weight:600;text-align:center">✅ Zapisano {saved_count} kluczy API!</div>'

    html = CSS + f'''
    <div class="container" style="max-width:700px;margin:auto;padding-bottom:80px">
        <div class="header">
            <h1>🔧 KREATOR KONFIGURACJI</h1>
            <small>Wszystkie klucze API w jednym miejscu</small>
        </div>

        {saved_msg}

        <div style="padding:12px;background:rgba(59,130,246,0.1);border:1px solid rgba(59,130,246,0.3);border-radius:10px;margin-bottom:20px;font-size:0.85rem;color:#93c5fd">
            💡 Wypelnij klucze API dla serwisow z ktorych korzystasz. Kazdy serwis mozna skonfigurowac niezaleznie.
        </div>

        <!-- STATUS OVERVIEW -->
        <div class="card" style="padding:15px;margin-bottom:20px">
            <div style="font-weight:700;margin-bottom:12px;font-size:1.1rem">📊 Status integracji</div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:0.9rem">
                <div>{status_dot('allegro_client_id')} Allegro</div>
                <div>{status_dot('telegram_bot_token')} Telegram</div>
                <div>{status_dot('gemini_api_key')} Gemini AI</div>
                <div>{status_dot('perplexity_api_key')} Perplexity AI</div>
                <div>{status_dot('ngrok_auth_token')} Ngrok</div>
                <div>{status_dot('olx_client_id')} OLX</div>
            </div>
        </div>

        <form method="POST" action="/ustawienia/kreator/save">

        <!-- ALLEGRO -->
        <details class="card" style="padding:0;margin-bottom:12px" {"open" if not cfg['allegro_client_id'] else ""}>
            <summary style="padding:15px;cursor:pointer;font-weight:700;font-size:1rem;list-style:none;display:flex;align-items:center;gap:10px">
                {status_dot('allegro_client_id')} 🛒 Allegro API
                <span style="margin-left:auto;font-size:0.75rem;color:#64748b">▼</span>
            </summary>
            <div style="padding:0 15px 15px">
                <div style="font-size:0.8rem;color:#64748b;margin-bottom:12px">
                    Zarejestruj aplikacje na <a href="https://apps.developer.allegro.pl" target="_blank" style="color:#6366f1">apps.developer.allegro.pl</a>
                </div>
                <div style="margin-bottom:10px">
                    <label style="font-size:0.8rem;color:#94a3b8">Client ID</label>
                    <input type="text" name="allegro_client_id" value="{cfg['allegro_client_id']}"
                        placeholder="Twoj Client ID z Allegro"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:4px;font-family:monospace;font-size:0.85rem">
                </div>
                <div style="margin-bottom:10px">
                    <label style="font-size:0.8rem;color:#94a3b8">Client Secret</label>
                    <input type="password" name="allegro_client_secret" value="{cfg['allegro_client_secret']}"
                        placeholder="Twoj Client Secret"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:4px;font-family:monospace;font-size:0.85rem">
                </div>
                <div>
                    <label style="font-size:0.8rem;color:#94a3b8">Redirect URI</label>
                    <input type="text" name="allegro_redirect_uri" value="{cfg['allegro_redirect_uri']}"
                        placeholder="https://twoja-domena.ngrok-free.dev/allegro/callback"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:4px;font-family:monospace;font-size:0.85rem">
                </div>
            </div>
        </details>

        <!-- TELEGRAM -->
        <details class="card" style="padding:0;margin-bottom:12px" {"open" if not cfg['telegram_bot_token'] else ""}>
            <summary style="padding:15px;cursor:pointer;font-weight:700;font-size:1rem;list-style:none;display:flex;align-items:center;gap:10px">
                {status_dot('telegram_bot_token')} 💬 Telegram Bot
                <span style="margin-left:auto;font-size:0.75rem;color:#64748b">▼</span>
            </summary>
            <div style="padding:0 15px 15px">
                <div style="font-size:0.8rem;color:#64748b;margin-bottom:12px">
                    Stworz bota przez <a href="https://t.me/BotFather" target="_blank" style="color:#6366f1">@BotFather</a> na Telegramie
                </div>
                <div style="margin-bottom:10px">
                    <label style="font-size:0.8rem;color:#94a3b8">Bot Token</label>
                    <input type="password" name="telegram_bot_token" value="{cfg['telegram_bot_token']}"
                        placeholder="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:4px;font-family:monospace;font-size:0.85rem">
                </div>
                <div style="margin-bottom:10px">
                    <label style="font-size:0.8rem;color:#94a3b8">Chat ID (powiadomienia o sprzedazach)</label>
                    <input type="text" name="telegram_chat_id" value="{cfg['telegram_chat_id']}"
                        placeholder="-1001234567890"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:4px;font-family:monospace;font-size:0.85rem">
                </div>
                <div>
                    <label style="font-size:0.8rem;color:#94a3b8">Support Chat ID (Twoj prywatny, opcjonalnie)</label>
                    <input type="text" name="support_chat_id" value="{cfg['support_chat_id']}"
                        placeholder="123456789"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:4px;font-family:monospace;font-size:0.85rem">
                </div>
            </div>
        </details>

        <!-- GEMINI -->
        <details class="card" style="padding:0;margin-bottom:12px" {"open" if not cfg['gemini_api_key'] else ""}>
            <summary style="padding:15px;cursor:pointer;font-weight:700;font-size:1rem;list-style:none;display:flex;align-items:center;gap:10px">
                {status_dot('gemini_api_key')} ✨ Google Gemini AI
                <span style="margin-left:auto;font-size:0.75rem;color:#64748b">▼</span>
            </summary>
            <div style="padding:0 15px 15px">
                <div style="font-size:0.8rem;color:#64748b;margin-bottom:12px">
                    Pobierz klucz z <a href="https://aistudio.google.com/apikey" target="_blank" style="color:#6366f1">aistudio.google.com/apikey</a> (darmowy!)
                </div>
                <div>
                    <label style="font-size:0.8rem;color:#94a3b8">API Key</label>
                    <input type="password" name="gemini_api_key" value="{cfg['gemini_api_key']}"
                        placeholder="AIzaSy..."
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:4px;font-family:monospace;font-size:0.85rem">
                </div>
            </div>
        </details>

        <!-- PERPLEXITY -->
        <details class="card" style="padding:0;margin-bottom:12px" {"open" if not cfg['perplexity_api_key'] else ""}>
            <summary style="padding:15px;cursor:pointer;font-weight:700;font-size:1rem;list-style:none;display:flex;align-items:center;gap:10px">
                {status_dot('perplexity_api_key')} 🔍 Perplexity AI
                <span style="margin-left:auto;font-size:0.75rem;color:#64748b">▼</span>
            </summary>
            <div style="padding:0 15px 15px">
                <div style="font-size:0.8rem;color:#64748b;margin-bottom:12px">
                    Klucz z <a href="https://www.perplexity.ai/settings/api" target="_blank" style="color:#6366f1">perplexity.ai/settings/api</a> (do analizy okazji)
                </div>
                <div>
                    <label style="font-size:0.8rem;color:#94a3b8">API Key</label>
                    <input type="password" name="perplexity_api_key" value="{cfg['perplexity_api_key']}"
                        placeholder="pplx-..."
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:4px;font-family:monospace;font-size:0.85rem">
                </div>
            </div>
        </details>

        <!-- NGROK -->
        <details class="card" style="padding:0;margin-bottom:12px" {"open" if not cfg['ngrok_auth_token'] else ""}>
            <summary style="padding:15px;cursor:pointer;font-weight:700;font-size:1rem;list-style:none;display:flex;align-items:center;gap:10px">
                {status_dot('ngrok_auth_token')} 🚀 Ngrok (zdalny dostep)
                <span style="margin-left:auto;font-size:0.75rem;color:#64748b">▼</span>
            </summary>
            <div style="padding:0 15px 15px">
                <div style="font-size:0.8rem;color:#64748b;margin-bottom:12px">
                    Token z <a href="https://dashboard.ngrok.com/get-started/your-authtoken" target="_blank" style="color:#6366f1">dashboard.ngrok.com</a>
                </div>
                <div style="margin-bottom:10px">
                    <label style="font-size:0.8rem;color:#94a3b8">Auth Token</label>
                    <input type="password" name="ngrok_auth_token" value="{cfg['ngrok_auth_token']}"
                        placeholder="2abc...xyz"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:4px;font-family:monospace;font-size:0.85rem">
                </div>
                <div>
                    <label style="font-size:0.8rem;color:#94a3b8">Stala domena (opcjonalnie)</label>
                    <input type="text" name="ngrok_domain" value="{cfg['ngrok_domain']}"
                        placeholder="twoja-firma.ngrok-free.dev"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:4px;font-family:monospace;font-size:0.85rem">
                </div>
            </div>
        </details>

        <!-- OLX -->
        <details class="card" style="padding:0;margin-bottom:12px">
            <summary style="padding:15px;cursor:pointer;font-weight:700;font-size:1rem;list-style:none;display:flex;align-items:center;gap:10px">
                {status_dot('olx_client_id')} 📦 OLX API (opcjonalnie)
                <span style="margin-left:auto;font-size:0.75rem;color:#64748b">▼</span>
            </summary>
            <div style="padding:0 15px 15px">
                <div style="font-size:0.8rem;color:#64748b;margin-bottom:12px">
                    Zarejestruj app na <a href="https://developer.olx.pl" target="_blank" style="color:#6366f1">developer.olx.pl</a>
                </div>
                <div style="margin-bottom:10px">
                    <label style="font-size:0.8rem;color:#94a3b8">Client ID</label>
                    <input type="text" name="olx_client_id" value="{cfg['olx_client_id']}"
                        placeholder="OLX Client ID"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:4px;font-family:monospace;font-size:0.85rem">
                </div>
                <div style="margin-bottom:10px">
                    <label style="font-size:0.8rem;color:#94a3b8">Client Secret</label>
                    <input type="password" name="olx_client_secret" value="{cfg['olx_client_secret']}"
                        placeholder="OLX Client Secret"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:4px;font-family:monospace;font-size:0.85rem">
                </div>
                <div>
                    <label style="font-size:0.8rem;color:#94a3b8">Redirect URI</label>
                    <input type="text" name="olx_redirect_uri" value="{cfg['olx_redirect_uri']}"
                        placeholder="https://twoja-domena.ngrok-free.dev/olx/callback"
                        style="width:100%;padding:10px;background:#1e1e2e;border:1px solid #2a2a3a;border-radius:8px;color:#fff;margin-top:4px;font-family:monospace;font-size:0.85rem">
                </div>
            </div>
        </details>

        <!-- SAVE ALL -->
        <button type="submit" style="width:100%;padding:16px;background:linear-gradient(135deg,#6366f1,#8b5cf6);border:none;border-radius:12px;color:#fff;font-weight:700;font-size:1.1rem;cursor:pointer;margin-top:10px;box-shadow:0 4px 15px rgba(99,102,241,0.3)">
            💾 ZAPISZ WSZYSTKO
        </button>

        </form>

        <a href="/ustawienia" style="display:block;text-align:center;color:#64748b;text-decoration:none;margin-top:20px">← Powrot do ustawien</a>
    </div>
    '''
    return html


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
                <div style="font-size:3rem;margin-bottom:20px">⚠️</div>
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
                <div style="font-size:3rem;margin-bottom:20px">✅</div>
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
                <div style="font-size:3rem;margin-bottom:20px">❌</div>
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
            <div style="font-size:3rem;margin-bottom:20px">✅</div>
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
            <div style="font-size:3rem;margin-bottom:20px">✅</div>
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
        scraped_cnt = conn.execute(f'DELETE FROM scraped WHERE asin IN ({placeholders})', asiny_list).rowcount

    # Usun produkty i palety
    conn.execute('DELETE FROM produkty WHERE paleta_id IS NOT NULL')
    conn.execute('DELETE FROM palety')
    conn.commit()

    return f'''
    <html><head><meta http-equiv="refresh" content="3;url=/ustawienia"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px">✅</div>
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
            <div style="font-size:3rem;margin-bottom:20px">✅</div>
            <div style="font-size:1.2rem">Scraped wyczyszczony!</div>
            <div style="color:#64748b;margin-top:10px">Usunieto {cnt} zescrapowanych produktow</div>
        </div>
    </body></html>
    '''


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

        # 2. Git pull
        logs.append('[2/4] Git pull...')
        if not os.path.isdir(os.path.join(app_dir, '.git')):
            logs.append('  -> Brak repo git. Inicjalizuje...')
            subprocess.run(['git', 'init'], cwd=app_dir, capture_output=True, timeout=10)
            subprocess.run(['git', 'remote', 'add', 'origin', 'https://github.com/Trupson2/akces-hub.git'],
                         cwd=app_dir, capture_output=True, timeout=10)

        r = subprocess.run(['git', 'pull', '--ff-only', 'origin', 'main'],
                          cwd=app_dir, capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            logs.append(f'  -> {r.stdout.strip()}')
        else:
            # Try with reset if ff-only fails
            subprocess.run(['git', 'fetch', 'origin'], cwd=app_dir, capture_output=True, timeout=60)
            r2 = subprocess.run(['git', 'reset', '--hard', 'origin/main'],
                               cwd=app_dir, capture_output=True, text=True, timeout=30)
            if r2.returncode == 0:
                logs.append(f'  -> Reset do origin/main OK')
            else:
                logs.append(f'  -> Git error: {r.stderr[:200]}')

        # 3. Pip install
        logs.append('[3/4] Pip install...')
        req = os.path.join(app_dir, 'requirements.txt')
        venv_pip = os.path.join(app_dir, 'venv', 'bin', 'pip')
        if os.path.exists(req) and os.path.exists(venv_pip):
            r = subprocess.run([venv_pip, 'install', '-r', req, '--quiet'],
                              capture_output=True, text=True, timeout=120)
            logs.append('  -> OK' if r.returncode == 0 else f'  -> {r.stderr[:100]}')
        else:
            logs.append('  -> Pomijam')

        # 4. Restart
        logs.append('[4/4] Restart Flask...')
        r = subprocess.run(['sudo', 'systemctl', 'restart', 'akceshub.service'],
                          capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            logs.append('  -> OK!')
        else:
            logs.append(f'  -> {r.stderr[:200]}')

        logs.append('')
        logs.append('AKTUALIZACJA ZAKONCZONA!')

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

        # 4. Restart Flask
        logs.append('[4/4] Restart serwisu...')
        try:
            result = subprocess.run(
                ['sudo', 'systemctl', 'restart', 'akceshub.service'],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                logs.append('  -> Flask zrestartowany!')
            else:
                logs.append(f'  -> Restart blad: {result.stderr[:200]}')
                logs.append('  -> Sprobuj recznie: sudo systemctl restart akceshub.service')
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
    <body><h1>📦 Deploy modulu</h1>
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
    <button type="submit">🚀 Deploy</button></form>
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
        <div style="font-size:3rem">✅</div>
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
            flash('✅ Ustawienia drukowania zapisane!', 'success')
        else:
            flash('⚠️ Blad zapisywania ustawien', 'error')

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
            flash(f'✅ Test drukowania na Niimbot B1 zakonczony!', 'success')
        elif printer_type == 'vretti':
            from modules.vretti_print import test_print as vretti_test
            vretti_test()
            flash(f'✅ Test drukowania na Vretti 420B zakonczony!', 'success')
        else:
            flash(f'⚠️ Nieznany typ drukarki: {printer_type}', 'error')
    except ImportError as e:
        flash(f'⚠️ Modul drukarki nie znaleziony: {e}', 'error')
    except Exception as e:
        flash(f'❌ Blad drukowania: {e}', 'error')

    return redirect(url_for('ustawienia.printing_settings'))
