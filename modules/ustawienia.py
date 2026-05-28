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

    # Auto-update (GitHub Releases) — dla klient\xf3w bez gita
    github_release_repo = get_config('github_release_repo', '')
    github_release_token = get_config('github_release_token', '')
    has_github_token = bool(github_release_token)

    # 2FA TOTP status (Phase 3)
    totp_enabled = False
    totp_backup_remaining = 0
    try:
        from modules.auth import _get_auth_db
        from modules.totp import backup_codes_remaining
        uid = session.get('user_id')
        if uid:
            _c = _get_auth_db()
            _r = _c.execute(
                'SELECT totp_enabled, totp_backup_codes FROM users WHERE id = ?', (uid,)
            ).fetchone()
            _c.close()
            if _r:
                totp_enabled = bool(_r['totp_enabled'])
                totp_backup_remaining = backup_codes_remaining(_r['totp_backup_codes'] or '')
    except Exception:
        pass
    # KAZDY klient ma WLASNE Allegro UUIDs (cenniki kurierow, polityki zwrotow/gwarancji).
    # 'dpd_cennik_id' to LEGACY nazwa - moze zawierac ID dowolnego kuriera (DPD/GLS/DHL...).
    # Defaults puste -> klient WPISZE w /ustawienia.
    dpd_cennik_id = get_config('dpd_cennik_id', '')
    inpost_cennik_id = get_config('inpost_cennik_id', '')
    zwroty_warunki_id = get_config('zwroty_warunki_id', '')
    reklamacje_warunki_id = get_config('reklamacje_warunki_id', '')

    return render_template('ustawienia.html',
        version=current_app.config.get('VERSION', ''),
        brand_name=current_app.config.get('BRAND_NAME', 'Akces Hub'),
        current_user=session.get('username'),
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
        inpost_cennik_id=inpost_cennik_id,
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
        totp_enabled=totp_enabled,
        totp_backup_remaining=totp_backup_remaining,
        github_release_repo=github_release_repo,
        has_github_token=has_github_token,
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
        'ai_model_analiza_palet': get_config('ai_model_analiza_palet', 'gemini-2.5-flash'),
        'ai_model_zdjecia': get_config('ai_model_zdjecia', 'gemini-2.5-flash-lite'),
        'ai_model_wycena': get_config('ai_model_wycena', 'gemini-2.5-flash'),
        'ai_model_tytuly': get_config('ai_model_tytuly', 'gemini-2.5-flash'),
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
        'notion_token': get_config('notion_token', ''),
        'notion_database_id': get_config('notion_database_id', ''),
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
        ('notion_token', 'Notion'),
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
                {'name': 'gemini_model', 'label': 'Model AI (globalny fallback)', 'type': 'select', 'options': [
                    ('gemini-2.5-flash', 'Gemini 2.5 Flash (zalecany)'),
                    ('gemini-2.5-flash-lite', 'Gemini 2.5 Flash Lite (szybszy, tańszy)'),
                    ('gemini-2.0-flash', 'Gemini 2.0 Flash (poprzednia generacja)'),
                    ('gemini-2.0-flash-lite', 'Gemini 2.0 Flash Lite'),
                ]},
                {'type': 'header', 'label': 'Model AI per sektor'},
                {'name': 'ai_model_analiza_palet', 'label': 'Analiza palet', 'type': 'select',
                 'hint': 'Analiza manifestu palety, wycena produktów, czas sprzedaży',
                 'options': [
                    ('gemini-2.5-flash', 'Gemini 2.5 Flash (zalecany)'),
                    ('gemini-2.5-flash-lite', 'Gemini 2.5 Flash Lite (szybszy, tańszy)'),
                    ('gemini-2.0-flash', 'Gemini 2.0 Flash (poprzednia generacja)'),
                    ('gemini-2.0-flash-lite', 'Gemini 2.0 Flash Lite'),
                ]},
                {'name': 'ai_model_zdjecia', 'label': 'Analiza zdjęć (stan produktu)', 'type': 'select',
                 'hint': 'Ocena stanu produktu ze zdjęcia',
                 'options': [
                    ('gemini-2.5-flash', 'Gemini 2.5 Flash (zalecany)'),
                    ('gemini-2.5-flash-lite', 'Gemini 2.5 Flash Lite (szybszy, tańszy)'),
                    ('gemini-2.0-flash', 'Gemini 2.0 Flash (poprzednia generacja)'),
                    ('gemini-2.0-flash-lite', 'Gemini 2.0 Flash Lite'),
                ]},
                {'name': 'ai_model_wycena', 'label': 'Auto-wycena produktów', 'type': 'select',
                 'hint': 'Automatyczna wycena cen sprzedaży produktów',
                 'options': [
                    ('gemini-2.5-flash', 'Gemini 2.5 Flash (zalecany)'),
                    ('gemini-2.5-flash-lite', 'Gemini 2.5 Flash Lite (szybszy, tańszy)'),
                    ('gemini-2.0-flash', 'Gemini 2.0 Flash (poprzednia generacja)'),
                    ('gemini-2.0-flash-lite', 'Gemini 2.0 Flash Lite'),
                ]},
                {'name': 'ai_model_tytuly', 'label': 'Generowanie tytułów Allegro', 'type': 'select',
                 'hint': 'Generowanie SEO tytułów ofert Allegro',
                 'options': [
                    ('gemini-2.5-flash', 'Gemini 2.5 Flash (zalecany)'),
                    ('gemini-2.5-flash-lite', 'Gemini 2.5 Flash Lite (szybszy, tańszy)'),
                    ('gemini-2.0-flash', 'Gemini 2.0 Flash (poprzednia generacja)'),
                    ('gemini-2.0-flash-lite', 'Gemini 2.0 Flash Lite'),
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
        {
            'key': 'notion_token', 'icon': '<span class=material-symbols-outlined>checklist</span>', 'title': 'Notion — Daily Tasks',
            'hint': 'Stworz integracje na <a href="https://www.notion.so/my-integrations" target="_blank" style="color:var(--accent)">notion.so/my-integrations</a>, nadaj dostep do bazy, wklej Database ID z URL strony.',
            'always_closed': True,
            'fields': [
                {'name': 'notion_token', 'label': 'Integration Token (Secret)', 'type': 'password', 'placeholder': 'secret_...'},
                {'name': 'notion_database_id', 'label': 'Database ID', 'type': 'text', 'placeholder': 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'},
            ]
        },
    ]

    # Determine open state
    support_nodata = not cfg['support_email'] and not cfg['support_phone']

    KREATOR_TEMPLATE = '''{% extends "base.html" %}
{% block page_title %}Kreator konfiguracji{% endblock %}
{% block content %}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Manrope:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=swap" rel="stylesheet">

<style>
/* ── KREATOR CYBERPUNK RESET ── */
.kre-root{--c-bg:#0e0e10;--c-panel:rgba(20,22,28,0.72);--c-border:rgba(143,245,255,0.10);--c-cyan:#8ff5ff;--c-pink:#ff6b9b;--c-lime:#cafd00;--c-green:#22c55e;--c-red:#ef4444;--c-text:#e4e4e7;--c-muted:#71717a;--c-input-bg:#111116;--c-input-border:rgba(143,245,255,0.12);--radius:14px;font-family:'Manrope',system-ui,sans-serif;color:var(--c-text)}
.kre-root *,.kre-root *::before,.kre-root *::after{box-sizing:border-box}
.kre-root{position:relative;min-height:100vh;background:var(--c-bg);padding:32px 16px 64px;margin:-20px -20px 0;overflow:hidden}

/* kinetic grid */
.kre-root::before{content:'';position:absolute;inset:0;
  background-image:
    linear-gradient(rgba(143,245,255,0.03) 1px,transparent 1px),
    linear-gradient(90deg,rgba(143,245,255,0.03) 1px,transparent 1px);
  background-size:60px 60px;pointer-events:none;z-index:0}
.kre-root>*{position:relative;z-index:1}

.kre-wrap{max-width:740px;margin:0 auto}

/* ── HEADER ── */
.kre-header{text-align:center;margin-bottom:36px}
.kre-header-label{font-size:10px;font-weight:600;letter-spacing:3px;text-transform:uppercase;color:var(--c-cyan);margin-bottom:8px}
.kre-header h1{font-family:'Space Grotesk',sans-serif;font-size:2rem;font-weight:700;color:#fff;margin:0 0 6px;
  text-shadow:0 0 30px rgba(143,245,255,0.35)}
.kre-header p{font-size:0.88rem;color:var(--c-muted);margin:0;line-height:1.6}

/* ── ALERTS ── */
.kre-alert{backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border-radius:var(--radius);padding:16px 20px;margin-bottom:24px;font-size:0.88rem;line-height:1.6;text-align:center}
.kre-alert-success{background:rgba(34,197,94,0.10);border:1px solid rgba(34,197,94,0.25);color:var(--c-green);font-weight:600}
.kre-alert-welcome{background:rgba(143,245,255,0.06);border:1px solid rgba(143,245,255,0.15);color:var(--c-text)}
.kre-alert-welcome strong{color:var(--c-cyan);font-family:'Space Grotesk',sans-serif;font-size:1.05rem;display:block;margin-bottom:4px}
.kre-alert-info{background:rgba(143,245,255,0.05);border:1px solid rgba(143,245,255,0.12);color:var(--c-muted)}

/* ── STATUS GRID ── */
.kre-status-panel{background:var(--c-panel);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid var(--c-border);border-radius:var(--radius);padding:20px 24px;margin-bottom:28px}
.kre-status-title{font-family:'Space Grotesk',sans-serif;font-size:11px;font-weight:600;letter-spacing:2.5px;text-transform:uppercase;color:var(--c-cyan);margin-bottom:16px;display:flex;align-items:center;gap:8px}
.kre-status-title .material-symbols-outlined{font-size:18px;color:var(--c-cyan)}
.kre-status-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px 16px}
.kre-status-item{display:flex;align-items:center;gap:8px;padding:7px 0;font-size:0.84rem;color:var(--c-text)}
.kre-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.kre-dot-on{background:var(--c-green);box-shadow:0 0 8px rgba(34,197,94,0.5)}
.kre-dot-off{background:var(--c-red);box-shadow:0 0 8px rgba(239,68,68,0.4)}

/* ── SECTION PANELS ── */
.kre-section{background:var(--c-panel);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid var(--c-border);border-radius:var(--radius);margin-bottom:14px;overflow:hidden;transition:border-color 0.3s}
.kre-section:hover{border-color:rgba(143,245,255,0.22)}
.kre-section[data-accent="cyan"]{border-left:3px solid var(--c-cyan)}
.kre-section[data-accent="pink"]{border-left:3px solid var(--c-pink)}
.kre-section[data-accent="lime"]{border-left:3px solid var(--c-lime)}
.kre-section summary{padding:16px 20px;cursor:pointer;list-style:none;display:flex;align-items:center;gap:12px;transition:background 0.2s;user-select:none}
.kre-section summary::-webkit-details-marker{display:none}
.kre-section summary:hover{background:rgba(143,245,255,0.03)}
.kre-section summary .material-symbols-outlined{font-size:22px;color:var(--c-cyan);opacity:0.8}
.kre-section[data-accent="pink"] summary .material-symbols-outlined{color:var(--c-pink)}
.kre-section[data-accent="lime"] summary .material-symbols-outlined{color:var(--c-lime)}
.kre-section-title{font-family:'Space Grotesk',sans-serif;font-weight:600;font-size:0.95rem;color:#fff;flex:1}
.kre-section-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.kre-section-chevron{font-size:18px;color:var(--c-muted);transition:transform 0.25s}
.kre-section[open] .kre-section-chevron{transform:rotate(180deg)}

.kre-section-body{padding:0 20px 20px}
.kre-section-hint{font-size:0.78rem;color:var(--c-muted);margin-bottom:14px;line-height:1.6}
.kre-section-hint a{color:var(--c-cyan);text-decoration:underline;text-underline-offset:2px}
.kre-section-hint code{background:rgba(143,245,255,0.08);color:var(--c-cyan);padding:1px 6px;border-radius:4px;font-size:0.76rem}

/* ── FORM FIELDS ── */
.kre-field{margin-bottom:12px}
.kre-field label{display:block;font-size:10px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:var(--c-muted);margin-bottom:6px}
.kre-field input,.kre-field select{width:100%;background:var(--c-input-bg);border:1px solid var(--c-input-border);border-radius:8px;padding:10px 14px;color:var(--c-text);font-family:'JetBrains Mono','Fira Code',monospace;font-size:0.84rem;transition:border-color 0.2s,box-shadow 0.2s;outline:none}
.kre-field input:focus,.kre-field select:focus{border-color:var(--c-cyan);box-shadow:0 0 0 3px rgba(143,245,255,0.08)}
.kre-field input::placeholder{color:rgba(113,113,122,0.6)}
.kre-field select{cursor:pointer;-webkit-appearance:none;appearance:none;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='%2371717a' viewBox='0 0 16 16'%3E%3Cpath d='M8 11L3 6h10z'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 12px center}
.kre-field select option{background:#18181b;color:var(--c-text)}

/* ── TEST BUTTON ── */
.kre-btn-test{display:inline-flex;align-items:center;gap:6px;margin-top:8px;padding:8px 16px;background:rgba(143,245,255,0.08);border:1px solid rgba(143,245,255,0.2);border-radius:8px;color:var(--c-cyan);font-family:'Space Grotesk',sans-serif;font-size:0.78rem;font-weight:600;letter-spacing:1px;text-transform:uppercase;cursor:pointer;transition:all 0.2s}
.kre-btn-test:hover{background:rgba(143,245,255,0.14);border-color:rgba(143,245,255,0.35)}
#vpsTestResult{font-size:0.8rem;margin-top:8px}

/* ── SAVE BUTTON ── */
.kre-btn-save{display:block;width:100%;padding:16px;margin-top:20px;background:linear-gradient(135deg,var(--c-pink),#e0558a);border:none;border-radius:var(--radius);color:#fff;font-family:'Space Grotesk',sans-serif;font-size:1rem;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;cursor:pointer;transition:all 0.3s;
  box-shadow:0 0 20px rgba(255,107,155,0.2)}
.kre-btn-save:hover{box-shadow:0 0 35px rgba(255,107,155,0.35);transform:translateY(-1px)}

/* ── BACK LINK ── */
.kre-back{display:inline-flex;align-items:center;gap:6px;margin-top:20px;font-size:0.84rem;color:var(--c-muted);text-decoration:none;transition:color 0.2s}
.kre-back:hover{color:var(--c-cyan)}

/* ── RESPONSIVE ── */
@media(max-width:600px){
  .kre-root{padding:20px 10px 48px}
  .kre-header h1{font-size:1.5rem}
  .kre-status-grid{grid-template-columns:1fr}
  .kre-section summary{padding:14px 16px}
  .kre-section-body{padding:0 16px 16px}
}
</style>

<div class="kre-root">
<div class="kre-wrap">

<!-- HEADER -->
<div class="kre-header">
    <div class="kre-header-label">Kreator konfiguracji</div>
    <h1>API CREDENTIALS</h1>
    <p>Wypelnij klucze API dla serwisow z ktorych korzystasz.<br>Kazdy serwis mozna skonfigurowac niezaleznie.</p>
</div>

<!-- ALERTS -->
{% if saved_count %}
<div class="kre-alert kre-alert-success">Zapisano {{ saved_count }} kluczy API!</div>
{% elif is_welcome %}
<div class="kre-alert kre-alert-welcome">
    <strong>Witaj w systemie!</strong>
    Skonfiguruj klucze API zeby odblokowac pelnie mozliwosci.<br>Mozesz to zrobic teraz lub wrocic pozniej z Ustawien.
</div>
{% endif %}

<!-- STATUS OVERVIEW -->
<div class="kre-status-panel">
    <div class="kre-status-title">
        <span class="material-symbols-outlined">monitoring</span>
        Status integracji
    </div>
    <div class="kre-status-grid">
        {% for dot, name in status_items %}
        <div class="kre-status-item">
            {% if 'green' in dot or 'success' in dot or '#22c55e' in dot or 'var(--green)' in dot %}
            <span class="kre-dot kre-dot-on"></span>
            {% else %}
            <span class="kre-dot kre-dot-off"></span>
            {% endif %}
            {{ name }}
        </div>
        {% endfor %}
    </div>
</div>

<!-- FORM -->
<form method="POST" action="/ustawienia/kreator/save">
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

{% for section in sections %}
{% set accent = 'lime' if 'gemini' in section.key or 'perplexity' in section.key else ('pink' if 'olx' in section.key or 'ngrok' in section.key or 'support' in section.key or 'rembg' in section.key else 'cyan') %}
<details class="kre-section" data-accent="{{ accent }}" {% if section.get('always_closed') %}{% elif section.get('open_condition') == 'support_nodata' and support_nodata %}open{% elif not section.get('open_condition') and not cfg.get(section.key) %}open{% endif %}>
    <summary>
        {{ section.icon | safe }}
        <span class="kre-section-title">{{ section.title }}</span>
        <span class="kre-section-dot {% if cfg.get(section.key) %}kre-dot-on{% else %}kre-dot-off{% endif %}"></span>
        <span class="material-symbols-outlined kre-section-chevron">expand_more</span>
    </summary>
    <div class="kre-section-body">
        <div class="kre-section-hint">{{ section.hint | safe }}</div>
        {% for field in section.fields %}
        {% if field.type == 'header' %}
        <div style="font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--c-muted);margin:16px 0 8px;border-top:1px solid var(--c-border);padding-top:12px">{{ field.label }}</div>
        {% else %}
        <div class="kre-field">
            <label>{{ field.label }}</label>
            {% if field.type == 'select' %}
            <select name="{{ field.name }}">
                {% for val, label in field.options %}
                <option value="{{ val }}" {{ 'selected' if cfg.get(field.name, '') == val else '' }}>{{ label }}</option>
                {% endfor %}
            </select>
            {% else %}
            <input type="{{ field.type }}" name="{{ field.name }}" value="{{ cfg.get(field.name, '') }}"
                placeholder="{{ field.get('placeholder', '') }}">
            {% endif %}
            {% if field.get('hint') %}
            <div style="font-size:0.65rem;color:var(--c-muted);margin-top:3px">{{ field.hint }}</div>
            {% endif %}
        </div>
        {% endif %}
        {% endfor %}
        {% if section.get('has_test') %}
        <div id="vpsTestResult"></div>
        <button type="button" onclick="testVps()" class="kre-btn-test">
            <span class="material-symbols-outlined" style="font-size:16px">cable</span>
            Test polaczenia
        </button>
        {% endif %}
    </div>
</details>
{% endfor %}

<button type="submit" class="kre-btn-save">ZAPISZ WSZYSTKO</button>

</form>

<a href="/ustawienia" class="kre-back">
    <span class="material-symbols-outlined" style="font-size:18px">arrow_back</span>
    Powrot do ustawien
</a>

</div>
</div>

<script>
function testVps() {
    var url = document.querySelector('input[name=rembg_vps_url]').value.trim();
    var res = document.getElementById('vpsTestResult');
    if(!url) { res.innerHTML='<span style="color:#ef4444">Wpisz URL!</span>'; return; }
    res.innerHTML='<span style="color:#eab308">Testowanie...</span>';
    fetch(url.replace(/\\/$/, '') + '/health')
        .then(r => r.json())
        .then(d => {
            if(d.status === 'ok' && d.rembg) {
                res.innerHTML='<span style="color:#22c55e">Polaczenie OK! Rembg dziala.</span>';
            } else {
                res.innerHTML='<span style="color:#ef4444">Serwer odpowiada ale rembg=' + d.rembg + '</span>';
            }
        })
        .catch(e => {
            res.innerHTML='<span style="color:#ef4444">Brak polaczenia: ' + e.message + '</span>';
        });
}
</script>
{% endblock %}
'''
    return render_template_string(KREATOR_TEMPLATE,
        version=current_app.config.get('VERSION', ''),
        brand_name=current_app.config.get('BRAND_NAME', 'Akces Hub'),
        current_user=session.get('username'),
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
        'gemini_api_key', 'gemini_model', 'perplexity_api_key',
        'ngrok_auth_token', 'ngrok_domain',
        'olx_client_id', 'olx_client_secret', 'olx_redirect_uri',
        'support_email', 'support_phone', 'support_info',
        'rembg_vps_url', 'rembg_vps_key',
        'notion_token', 'notion_database_id',
    ]

    saved = 0
    for key in keys:
        val = request.form.get(key, '').strip()
        if val:
            set_config(key, val)
            saved += 1

    # Per-sector AI model selectors (validate against allowed values)
    _allowed_ai_models = ['gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.0-flash', 'gemini-2.0-flash-lite']
    for key in ['ai_model_analiza_palet', 'ai_model_zdjecia', 'ai_model_wycena', 'ai_model_tytuly']:
        val = request.form.get(key)
        if val in _allowed_ai_models:
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


# ════════════════════════════════════════════════════════════════════════
# CUSTOM PROMPT do generowania opisow produktow (Gemini AI)
# Klient moze nadpisac domyslny styl Adriana wlasnym prompt-em.
# ════════════════════════════════════════════════════════════════════════

@ustawienia_bp.route('/ustawienia/ai-prompt', methods=['GET'])
def ustawienia_ai_prompt():
    """Edytor custom promptu do Gemini AI dla opisow Allegro."""
    from modules.utils import DEFAULT_OPIS_PROMPT
    current = get_config('gemini_opis_prompt', '')
    is_custom = bool(current.strip())

    return render_template_string('''{% extends "base.html" %}
{% block page_title %}Custom prompt AI{% endblock %}
{% block content %}
<div style="max-width:1200px;margin:auto">
    <div class="hdr">
        <h1><span class="material-symbols-outlined">smart_toy</span> CUSTOM PROMPT AI</h1>
        <div style="font-size:0.85rem;color:#94a3b8;margin-top:6px">
            Edytor promptu wysyłanego do Gemini przy generowaniu opisów produktów Allegro
        </div>
    </div>

    <div style="margin-bottom:18px">
        {% if is_custom %}
        <span style="background:rgba(34,197,94,0.15);color:#22c55e;padding:4px 12px;border-radius:6px;font-size:0.78rem;font-weight:700;border:1px solid #22c55e">✓ Aktywny custom prompt</span>
        {% else %}
        <span style="background:rgba(143,245,255,0.1);color:#8ff5ff;padding:4px 12px;border-radius:6px;font-size:0.78rem;font-weight:700;border:1px solid rgba(143,245,255,0.3)">Aktualnie: domyślny prompt</span>
        {% endif %}
    </div>

    <div class="card" style="padding:18px;margin-bottom:16px;background:rgba(143,245,255,0.04);border:1px solid rgba(143,245,255,0.15)">
        <div style="font-weight:700;color:#8ff5ff;margin-bottom:10px">
            <span class="material-symbols-outlined" style="font-size:1rem;vertical-align:middle">info</span> Jak to działa
        </div>
        <ul style="font-size:0.82rem;color:#94a3b8;line-height:1.7;padding-left:20px;margin:0">
            <li><b>Pusty prompt</b> = używany domyślny styl (jak u dostawcy aplikacji)</li>
            <li><b>Własny prompt</b> = AI generuje opisy WG TWOICH instrukcji, w TWOIM stylu</li>
            <li>Dostępne placeholdery (Gemini je podstawi):
                <code style="background:#0f1019;padding:2px 6px;border-radius:4px;color:#beee00">{{ '{nazwa}' }}</code>,
                <code style="background:#0f1019;padding:2px 6px;border-radius:4px;color:#beee00">{{ '{features_text}' }}</code>,
                <code style="background:#0f1019;padding:2px 6px;border-radius:4px;color:#beee00">{{ '{typ}' }}</code>
            </li>
            <li><b>UWAGA:</b> filtr fraz blokowanych przez Allegro (np "skontaktuj się", "podaruj", "gwarancja") jest stosowany ZAWSZE po wygenerowaniu — nie da się go wyłączyć, chroni Twoje oferty</li>
        </ul>
    </div>

    <form action="/ustawienia/ai-prompt" method="POST">
        <div class="card" style="padding:18px;margin-bottom:14px">
            <label style="display:block;font-weight:700;color:#8ff5ff;margin-bottom:10px;font-size:0.9rem">
                <span class="material-symbols-outlined" style="font-size:1rem;vertical-align:middle">edit_note</span>
                Twój prompt do Gemini (zostaw puste żeby używać domyślnego)
            </label>
            <textarea name="prompt" rows="24"
                style="width:100%;padding:12px;background:#0f1019;border:1px solid #2d3748;border-radius:8px;color:#e2e8f0;font-family:'Cascadia Code', 'Consolas', monospace;font-size:0.82rem;line-height:1.5;resize:vertical;min-height:300px">{{ current }}</textarea>
            <div style="font-size:0.72rem;color:#64748b;margin-top:6px">
                Wskazówka: kliknij "Wczytaj domyślny" żeby zobaczyć stock prompt i edytować od jego bazy
            </div>
        </div>

        <div style="display:flex;gap:10px;flex-wrap:wrap">
            <button type="submit" class="btn btn-ok" style="padding:12px 22px;font-weight:700">
                <span class="material-symbols-outlined" style="font-size:1rem;vertical-align:middle">save</span> Zapisz prompt
            </button>
            <button type="button" onclick="loadDefault()" class="btn btn-2" style="padding:12px 22px">
                <span class="material-symbols-outlined" style="font-size:1rem;vertical-align:middle">content_copy</span> Wczytaj domyślny do edycji
            </button>
            <button type="button" onclick="resetPrompt()" class="btn btn-2" style="padding:12px 22px;background:rgba(239,68,68,0.1);border-color:rgba(239,68,68,0.3);color:#ef4444">
                <span class="material-symbols-outlined" style="font-size:1rem;vertical-align:middle">restart_alt</span> Reset (wróć do domyślnego)
            </button>
            <a href="/ustawienia" class="back" style="margin-left:auto">← Ustawienia</a>
        </div>
    </form>
</div>

<script id="defaultPromptData" type="application/json">{{ default_json|safe }}</script>
<script>
const DEFAULT_PROMPT = JSON.parse(document.getElementById('defaultPromptData').textContent);
function loadDefault() {
    const ta = document.querySelector('textarea[name="prompt"]');
    if (ta.value.trim() && !confirm('Nadpisać aktualną zawartość domyślnym promptem? (możesz potem edytować)')) return;
    ta.value = DEFAULT_PROMPT;
}
function resetPrompt() {
    if (!confirm('Wyczyścić Twój prompt? Wrócimy do domyślnego (zachowane bezpiecznie w kodzie).')) return;
    document.querySelector('textarea[name="prompt"]').value = '';
    document.querySelector('form').submit();
}
</script>
{% endblock %}
''',
        current=current,
        is_custom=is_custom,
        default_json=__import__('json').dumps(DEFAULT_OPIS_PROMPT),
    )


@ustawienia_bp.route('/ustawienia/ai-prompt', methods=['POST'])
def ustawienia_ai_prompt_save():
    """Zapisz custom prompt (pusty = przywroc domyslny)."""
    prompt = request.form.get('prompt', '').strip()
    # Pusty -> kasujemy custom (uzywany bedzie DEFAULT_OPIS_PROMPT)
    set_config('gemini_opis_prompt', prompt)
    return redirect('/ustawienia/ai-prompt')


# ════════════════════════════════════════════════════════════════════════
# LAYOUT OPISU ALLEGRO - klient wybiera uklad sekcji opisu (zdjecie/tekst)
# ════════════════════════════════════════════════════════════════════════

@ustawienia_bp.route('/ustawienia/layout-opisu', methods=['GET'])
def ustawienia_layout_opisu():
    """Wybor layoutu opisu Allegro + max zdjec do opisu (2/4/6/8)."""
    from modules.allegro_api import LAYOUTS_AVAILABLE, LAYOUTS_INFO
    current = get_config('allegro_opis_layout', 'klasyczny').strip().lower()
    if current not in LAYOUTS_AVAILABLE:
        current = 'klasyczny'
    try:
        current_max_img = int(get_config('allegro_max_zdjec_opis', '8') or '8')
    except (ValueError, TypeError):
        current_max_img = 8
    if current_max_img not in (2, 4, 6, 8):
        current_max_img = 8

    return render_template_string('''{% extends "base.html" %}
{% block page_title %}Layout opisu Allegro{% endblock %}
{% block content %}
<style>
.layout-card {
    padding:16px;border-radius:12px;border:2px solid rgba(255,255,255,0.06);
    background:rgba(22,26,33,0.5);transition:all 0.2s;height:100%;
    display:flex;flex-direction:column;gap:10px;
}
.layout-card:hover { border-color:rgba(143,245,255,0.35); transform:translateY(-2px); }
.layout-card.selected {
    box-shadow:0 0 24px rgba(143,245,255,0.2);
    background:rgba(143,245,255,0.06);
}
.layout-icon-wrap {
    width:48px;height:48px;border-radius:10px;display:flex;align-items:center;
    justify-content:center;flex-shrink:0;
}
.layout-icon-wrap .material-symbols-outlined {
    font-size:28px;line-height:1;
}
.layout-save-btn {
    display:inline-flex !important;width:auto !important;padding:14px 32px;
    font-size:0.95rem;font-weight:800;border:none;border-radius:12px;cursor:pointer;
    background:linear-gradient(135deg,#22c55e,#16a34a);color:#fff !important;
    transition:all 0.2s;text-transform:uppercase;letter-spacing:0.5px;
    align-items:center;gap:8px;
}
.layout-save-btn:hover { transform:translateY(-2px); box-shadow:0 8px 20px rgba(34,197,94,0.3); }
.layout-save-btn .material-symbols-outlined { font-size:1.1rem; }
</style>

<div style="max-width:1280px;margin:auto">
    <div class="hdr">
        <h1><span class="material-symbols-outlined">view_quilt</span> LAYOUT OPISU ALLEGRO</h1>
        <div style="font-size:0.85rem;color:#94a3b8;margin-top:6px">
            Wybierz układ sekcji opisu (gdzie zdjęcia, gdzie tekst) przy wystawianiu ofert
        </div>
    </div>

    <div class="card" style="padding:18px;margin-bottom:18px;background:rgba(143,245,255,0.04);border:1px solid rgba(143,245,255,0.15)">
        <div style="font-weight:700;color:#8ff5ff;margin-bottom:8px">
            <span class="material-symbols-outlined" style="font-size:1rem;vertical-align:middle">info</span> Jak to działa
        </div>
        <div style="font-size:0.82rem;color:#94a3b8;line-height:1.6">
            Allegro pokazuje opis jako sekcje. Każda sekcja może mieć 1 lub 2 elementy (zdjęcie, tekst, zdjęcie+tekst).
            To ustawienie wpływa na <b>kolejność</b> i <b>parowanie</b> tych elementów. Nazwa, ASIN, parametry i GPSR są budowane osobno.
        </div>
    </div>

    <form action="/ustawienia/layout-opisu" method="POST">
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin-bottom:24px">
        {% for key in layouts_order %}
            {% set info = layouts_info[key] %}
            <label style="cursor:pointer;display:block">
                <input type="radio" name="layout" value="{{ key }}" {{ 'checked' if key == current else '' }}
                    style="position:absolute;opacity:0;pointer-events:none"
                    onchange="document.querySelectorAll('.layout-card').forEach(c => c.classList.remove('selected')); this.closest('label').querySelector('.layout-card').classList.add('selected')">
                <div class="layout-card {{ 'selected' if key == current else '' }}" style="border-color:{{ info.color if key == current else 'rgba(255,255,255,0.06)' }}">
                    <div style="display:flex;align-items:center;gap:12px">
                        <div class="layout-icon-wrap" style="background:{{ info.color }}1a;border:1px solid {{ info.color }}55">
                            <span class="material-symbols-outlined" style="color:{{ info.color }}">{{ info.icon }}</span>
                        </div>
                        <div style="flex:1;min-width:0">
                            <div style="font-weight:800;color:{{ info.color }};font-size:1rem;line-height:1.2">{{ info.label }}</div>
                            {% if key == 'klasyczny' %}<div style="margin-top:4px"><span style="font-size:0.65rem;background:rgba(190,238,0,0.15);color:#beee00;padding:2px 8px;border-radius:4px;font-weight:700;letter-spacing:0.5px">DOMYŚLNY</span></div>{% endif %}
                        </div>
                    </div>
                    <div style="font-size:0.78rem;color:#94a3b8;line-height:1.5;min-height:55px">
                        {{ info.desc }}
                    </div>
                    <pre style="background:#0f1019;border:1px solid #2d3748;border-radius:6px;padding:10px;color:#e2e8f0;font-family:'Cascadia Code', 'Consolas', monospace;font-size:0.72rem;line-height:1.4;margin:0;white-space:pre;flex:1">{{ info.wireframe }}</pre>
                </div>
            </label>
        {% endfor %}
        </div>

        <div class="card" style="padding:16px;margin-bottom:18px">
            <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
                <div class="layout-icon-wrap" style="background:rgba(143,245,255,0.1);border:1px solid rgba(143,245,255,0.3)">
                    <span class="material-symbols-outlined" style="color:#8ff5ff">image</span>
                </div>
                <div style="flex:1;min-width:200px">
                    <div style="font-weight:800;color:#8ff5ff;font-size:0.95rem">Max zdjęć w opisie</div>
                    <div style="font-size:0.78rem;color:#94a3b8;line-height:1.5;margin-top:4px">
                        Ile zdjęć produktu wstawić w opisie Allegro (galeria oferty zawsze pokazuje wszystkie). Mniej zdjęć = szybciej się ładuje + krótszy opis.
                    </div>
                </div>
                <select name="max_img" style="padding:10px 14px;background:#0f1019;border:1px solid #2d3748;border-radius:8px;color:#e2e8f0;font-size:0.95rem;font-weight:700;min-width:120px">
                    {% for n in [2, 4, 6, 8] %}
                    <option value="{{ n }}" {{ 'selected' if n == current_max_img else '' }}>{{ n }} zdjęć{% if n == 8 %} (domyślnie){% endif %}</option>
                    {% endfor %}
                </select>
            </div>
        </div>

        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
            <button type="submit" class="layout-save-btn">
                <span class="material-symbols-outlined">save</span> Zapisz wybrany layout
            </button>
            <a href="/ustawienia" class="back" style="margin-left:auto">← Ustawienia</a>
        </div>
    </form>

    <div class="card" style="padding:14px;margin-top:18px;background:rgba(245,158,11,0.05);border:1px solid rgba(245,158,11,0.2)">
        <div style="font-size:0.78rem;color:#fbbf24">
            <span class="material-symbols-outlined" style="font-size:1rem;vertical-align:middle">warning</span>
            <b>Uwaga:</b> Layout wpływa tylko na <b>nowo wystawiane</b> oferty (po zapisaniu). Już wystawione oferty zachowują swój istniejący opis.
        </div>
    </div>
</div>
{% endblock %}
''',
        current=current,
        current_max_img=current_max_img,
        layouts_order=LAYOUTS_AVAILABLE,
        layouts_info=LAYOUTS_INFO,
    )


@ustawienia_bp.route('/ustawienia/layout-opisu', methods=['POST'])
def ustawienia_layout_opisu_save():
    """Zapisz wybrany layout (whitelist) + max zdjec w opisie."""
    from modules.allegro_api import LAYOUTS_AVAILABLE
    chosen = request.form.get('layout', '').strip().lower()
    if chosen not in LAYOUTS_AVAILABLE:
        chosen = 'klasyczny'
    set_config('allegro_opis_layout', chosen)

    try:
        max_img = int(request.form.get('max_img', '8'))
    except (ValueError, TypeError):
        max_img = 8
    if max_img not in (2, 4, 6, 8):
        max_img = 8
    set_config('allegro_max_zdjec_opis', str(max_img))

    return redirect('/ustawienia/layout-opisu')


@ustawienia_bp.route('/ustawienia/integracje-parametry', methods=['POST'])
def ustawienia_integracje_parametry():
    """Zapisuje parametry integracji (cenniki kurierow, polityki zwrotow/reklamacji).
    UWAGA: zapisuje TEZ puste stringi -> klient moze CELOWO wyczyscic pole
    (np. nie uzywa InPostu, kasuje cennik)."""
    from modules.database import set_config
    for key in ('dpd_cennik_id', 'inpost_cennik_id', 'zwroty_warunki_id', 'reklamacje_warunki_id'):
        # Pole TYLKO jesli istnieje w formularzu (None => skip, '' => clear)
        if key in request.form:
            set_config(key, request.form.get(key, '').strip())
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


@ustawienia_bp.route('/ustawienia/security', methods=['POST'])
def ustawienia_security():
    """Zapisuje ustawienia bezpieczeństwa"""
    from modules.database import set_config
    auto_login = 'true' if request.form.get('auto_login_lan') == 'on' else 'false'
    set_config('auto_login_lan', auto_login)
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

    # PWA icon upload — auto-resize to 192x192 and 512x512
    pwa_icon = request.files.get('pwa_icon')
    if pwa_icon and pwa_icon.filename:
        ext = pwa_icon.filename.rsplit('.', 1)[-1].lower()
        if ext in ('png', 'jpg', 'jpeg'):
            from PIL import Image
            import io
            img_data = pwa_icon.read()
            if len(img_data) <= 2 * 1024 * 1024:  # max 2MB
                img = Image.open(io.BytesIO(img_data)).convert('RGBA')
                static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'static')
                for size in (192, 512):
                    resized = img.resize((size, size), Image.LANCZOS)
                    resized.save(os.path.join(static_dir, f'icon-{size}.png'), 'PNG', optimize=True)

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


@ustawienia_bp.route('/ustawienia/github-token', methods=['POST'])
def ustawienia_github_token():
    """Konfiguracja GitHub tokena dla auto-update z PRIVATE repo.

    Token (fine-grained PAT) dostarcza Adrian przez Telegram/email.
    Klient wkleja TYLKO RAZ. Token jest read-only (scope: repo content).

    Walidacja: prefix github_pat_/ghp_/gho_ + test API call do repo.
    """
    from modules.database import set_config, get_config
    token = request.form.get('github_token', '').strip()
    repo = request.form.get('github_repo', '').strip()

    if token == '__CLEAR__':
        # Specjalna komenda — usun token z DB
        set_config('github_release_token', '')
        return redirect('/ustawienia?msg=token_usuniety')

    if not token:
        return redirect('/ustawienia?error=brak_tokena')

    # Sanity check prefix
    if not (token.startswith('github_pat_') or token.startswith('ghp_') or token.startswith('gho_')):
        return redirect('/ustawienia?error=zly_format_tokena')

    # Test API call — sprawdz czy token + repo dzialaja zanim zapiszemy
    test_repo = repo or get_config('github_release_repo', 'Trupson2/akces-hub-release')
    try:
        import requests as _req
        r = _req.get(
            f'https://api.github.com/repos/{test_repo}',
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'},
            timeout=10,
        )
        if r.status_code != 200:
            return redirect(f'/ustawienia?error=token_nieprawidlowy_status_{r.status_code}')
    except Exception:
        # Nie blokujemy save jesli brak internetu — token moze byc OK
        pass

    set_config('github_release_token', token)
    if repo:
        set_config('github_release_repo', repo)
    return redirect('/ustawienia?msg=token_zapisany')


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
    """Pobierz aktualna baze danych — TYLKO admin"""
    from flask import send_file, session, abort
    if not session.get('user_id'):
        abort(401, 'Wymagane logowanie')
    try:
        from modules.database import get_db
        user = get_db().execute(
            'SELECT rola FROM users WHERE id = ?', (session['user_id'],)
        ).fetchone()
        if not user or user['rola'] != 'admin':
            abort(403, 'Tylko administrator może pobrać bazę danych')
    except Exception:
        abort(403, 'Brak uprawnień')
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
    # FIX 2026-05 (PHASE 1.3): WYŁĄCZONE przed sprzedażą. Brak CSRF +
    # fallback pobierał ZIP z GitHub bez weryfikacji podpisu = RCE przy
    # przejętej sesji admina. Redundantne wobec /system/update (app.py
    # ~2518: require_admin + CSRF + audit). Jedyna dozwolona ścieżka
    # aktualizacji = /system/update. Przywrócić tylko z CSRF + weryfikacją
    # podpisu (PHASE 3.4).
    from flask import abort
    abort(404)
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
            # CHANGELOG.md auto-generowany przy starcie -> zawsze ma local changes,
            # bez tego git pull wala "would be overwritten by merge".
            try:
                subprocess.run(['git', 'checkout', 'HEAD', '--', 'CHANGELOG.md'],
                              cwd=app_dir, capture_output=True, timeout=10)
            except Exception:
                pass
            r = subprocess.run(['git', 'pull', '--ff-only', '--autostash', 'origin', 'main'],
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
    """Aktualizacja systemu -- upload ZIP + backup + rozpakowanie + restart.
    TYLKO admin. Logowanie do audit logu: kto, kiedy, z jakiego IP, czy sukces."""
    # FIX 2026-05 (PHASE 1.3): WYŁĄCZONE przed sprzedażą. Brak inline CSRF
    # na uploadzie ZIP nadpisującym pliki = RCE przy przejętej sesji.
    # Redundantne wobec /system/update (app.py ~2518). Jedyna dozwolona
    # ścieżka = /system/update. Przywrócić tylko z CSRF (PHASE 3.4).
    from flask import abort
    abort(404)
    import subprocess, zipfile, shutil
    from html import escape
    try:
        from modules.database import log_admin_action
    except Exception:
        log_admin_action = lambda *a, **k: None

    if session.get('rola') != 'admin':
        log_admin_action('admin_update_zip', {'reason': 'not_admin'}, success=False,
                         error_message='Brak uprawnien')
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

        zip_path = os.path.join(tmp_dir, 'update.zip')
        f.save(zip_path)

        # ── ZIP-SLIP GUARD ──
        # Weryfikuj każdy członek archiwum PRZED extractall — atakujący mógłby
        # zrobić ZIP z plikiem "../../../etc/cron.d/pwn" i nadpisać system.
        # Referencja: https://snyk.io/research/zip-slip-vulnerability
        _tmp_real = os.path.realpath(tmp_dir)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for member in zf.infolist():
                # Odrzuć absolute paths (np. "/etc/passwd")
                if os.path.isabs(member.filename) or member.filename.startswith(('/', '\\')):
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    logs.append(f'  -> BLAD: Absolute path w ZIP: {member.filename}')
                    log_admin_action('admin_update_zip',
                                     {'attack': 'zip_slip_absolute', 'filename': member.filename[:200]},
                                     success=False, error_message='Absolute path w ZIP')
                    return page('Blad: Niebezpieczny ZIP (absolute path)', '#ef4444')
                # Odrzuć ścieżki które wychodzą poza tmp_dir ("../../../")
                target = os.path.realpath(os.path.join(tmp_dir, member.filename))
                if os.path.commonpath([_tmp_real, target]) != _tmp_real:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    logs.append(f'  -> BLAD: Path traversal w ZIP: {member.filename}')
                    log_admin_action('admin_update_zip',
                                     {'attack': 'zip_slip_traversal', 'filename': member.filename[:200]},
                                     success=False, error_message='Path traversal w ZIP')
                    return page('Blad: Niebezpieczny ZIP (path traversal)', '#ef4444')
                # Odrzuć symlinki (mogą wskazywać poza tmp_dir)
                # external_attr high bits = Unix mode; 0o120000 == symlink
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    logs.append(f'  -> BLAD: Symlink w ZIP: {member.filename}')
                    log_admin_action('admin_update_zip',
                                     {'attack': 'zip_slip_symlink', 'filename': member.filename[:200]},
                                     success=False, error_message='Symlink w ZIP')
                    return page('Blad: Niebezpieczny ZIP (symlink)', '#ef4444')
            # Walidacja przeszła — extractall jest teraz bezpieczny
            zf.extractall(tmp_dir)
        os.remove(zip_path)

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
        log_admin_action('admin_update_zip', {'files_updated': updated}, success=True)

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
