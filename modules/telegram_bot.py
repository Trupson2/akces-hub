"""
Telegram Bot module - powiadomienia i alerty
"""

import threading
import time
import requests
from datetime import datetime, timedelta
from flask import Blueprint, render_template_string, request, redirect, jsonify

from .database import get_db, get_config, set_config, query_db, execute_db

telegram_bp = Blueprint('telegram', __name__)

# Stan bota
_bot_running = False
_bot_thread = None

# ============================================================
# WHATSAPP (TextMeBot) - dla dziadka
# ============================================================
def get_whatsapp_key():
    return get_config('whatsapp_api_key', '')

def get_whatsapp_phone():
    return get_config('whatsapp_phone', '')

def whatsapp_enabled():
    return get_config('whatsapp_enabled', 'false') == 'true'

def send_whatsapp(message):
    """Wysyła wiadomość przez WhatsApp (TextMeBot)"""
    api_key = get_whatsapp_key()
    phone = get_whatsapp_phone()
    
    if not api_key or not phone:
        print("[WhatsApp] Brak API key lub numeru")
        return False
    
    if not whatsapp_enabled():
        print("[WhatsApp] Wyłączony")
        return False
    
    try:
        # TextMeBot API
        url = "https://api.textmebot.com/send.php"
        params = {
            'recipient': phone,
            'apikey': api_key,
            'text': message
        }
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            print(f"[WhatsApp] Wysłano: {message[:30]}...")
            return True
        else:
            print(f"[WhatsApp] Błąd: {response.text}")
            return False
            
    except Exception as e:
        print(f"[WhatsApp] Wyjątek: {e}")
        return False


def alert_whatsapp_sprzedaz(nazwa, miasto=''):
    """Wysyła alert o sprzedaży na WhatsApp dziadka"""
    msg = f"📦 WYŚLIJ:\n{nazwa[:40]}"
    if miasto:
        msg += f"\n📍 {miasto}"
    return send_whatsapp(msg)


# ============================================================
# FUNKCJE TELEGRAM API
# ============================================================
def get_bot_token():
    return get_config('telegram_bot_token', '')

def get_chat_id():
    return get_config('telegram_chat_id', '')

def bot_status():
    """Sprawdza czy bot jest włączony"""
    return get_config('telegram_enabled', 'true') == 'true'

def send_telegram(message, parse_mode='HTML', silent=False):
    """Wysyła wiadomość przez Telegram i zapisuje message_id do późniejszego usunięcia
    
    Args:
        message: Treść wiadomości
        parse_mode: Format tekstu (HTML lub Markdown)
        silent: True = cicha wiadomość bez dźwięku, False = z dźwiękiem
    """
    token = get_bot_token()
    chat_id = get_chat_id()
    
    if not token or not chat_id:
        print("[Telegram] Brak tokena lub chat_id")
        return False
    
    if not bot_status():
        print("[Telegram] Bot wyłączony")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': parse_mode,
            'disable_notification': silent  # False = dźwięk włączony!
        }
        response = requests.post(url, data=data, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            message_id = result.get('result', {}).get('message_id')
            # Loguj wysłaną wiadomość z message_id
            log_message('sent', message, message_id)
            return True
        else:
            print(f"[Telegram] Błąd: {response.text}")
            return False
            
    except Exception as e:
        print(f"[Telegram] Wyjątek: {e}")
        return False


def send_telegram_support(message, parse_mode='HTML'):
    """Wysyła wiadomość na chat supportu (do właściciela systemu).
    Używa tego samego bota, ale osobny chat_id z configu 'support_chat_id'.
    Jeśli support_chat_id nie ustawiony — wysyła na główny chat_id.
    """
    token = get_bot_token()
    support_chat = get_config('support_chat_id', '')
    chat_id = support_chat if support_chat else get_chat_id()

    if not token or not chat_id:
        print("[Telegram Support] Brak tokena lub chat_id")
        return False

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': parse_mode,
            'disable_notification': False  # Support = zawsze z dzwiekiem
        }
        response = requests.post(url, data=data, timeout=10)

        if response.status_code == 200:
            return True
        else:
            print(f"[Telegram Support] Błąd: {response.text}")
            return False

    except Exception as e:
        print(f"[Telegram Support] Wyjątek: {e}")
        return False


def delete_telegram_message(message_id):
    """Usuwa pojedynczą wiadomość z czatu Telegram"""
    token = get_bot_token()
    chat_id = get_chat_id()
    
    if not token or not chat_id:
        return False
    
    try:
        url = f"https://api.telegram.org/bot{token}/deleteMessage"
        data = {
            'chat_id': chat_id,
            'message_id': message_id
        }
        response = requests.post(url, data=data, timeout=5)
        return response.status_code == 200 and response.json().get('ok', False)
    except:
        return False


def clear_telegram_chat(days_old=1, max_messages=50):
    """Czyści ostatnie wiadomości bota z czatu Telegram"""
    token = get_bot_token()
    chat_id = get_chat_id()
    
    if not token or not chat_id:
        return 0
    
    deleted = 0
    
    try:
        # Wyślij tymczasową wiadomość żeby poznać aktualny message_id
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        response = requests.post(url, data={
            'chat_id': chat_id,
            'text': '🧹'
        }, timeout=5)
        
        if response.status_code != 200:
            return 0
        
        result = response.json()
        current_id = result.get('result', {}).get('message_id', 0)
        
        if not current_id:
            return 0
        
        # Usuń tę wiadomość też
        delete_telegram_message(current_id)
        
        # Iteruj wstecz i usuwaj (szybko, bez czekania)
        for msg_id in range(current_id - 1, max(0, current_id - max_messages), -1):
            delete_telegram_message(msg_id)
            deleted += 1
        
    except Exception as e:
        print(f"[Telegram] Błąd: {e}")
    
    return deleted


def log_message(typ, msg, message_id=None):
    """Zapisuje log wiadomości do bazy z message_id"""
    try:
        execute_db(
            'INSERT INTO telegram_logs (typ, wiadomosc, status, message_id) VALUES (?, ?, ?, ?)',
            (typ, msg[:500], 'sent', message_id)
        )
    except:
        # Fallback bez message_id (stara struktura tabeli)
        try:
            execute_db(
                'INSERT INTO telegram_logs (typ, wiadomosc, status) VALUES (?, ?, ?)',
                (typ, msg[:500], 'sent')
            )
        except:
            pass

def get_logs(limit=20):
    """Pobiera ostatnie logi"""
    return query_db(
        'SELECT * FROM telegram_logs ORDER BY data DESC LIMIT ?', 
        (limit,)
    )

# ============================================================
# FUNKCJE ALERTÓW
# ============================================================
def alert_sprzedaz(produkt_nazwa, cena, kupujacy='', lokalizacja='', regal='', paleta='', ilosc_zostalo=None):
    """Wysyła alert o sprzedaży z dźwiękiem + lokalizacja w magazynie!"""
    if get_config('telegram_alert_sprzedaz', 'true') != 'true':
        return False

    msg = f"🔔💰 <b>SPRZEDAŻ!</b> 💰🔔\n\n"
    msg += f"📦 {produkt_nazwa}\n"
    msg += f"💵 <b>{cena:.2f} zł</b>\n"
    if kupujacy:
        msg += f"👤 {kupujacy}\n"

    # Lokalizacja w magazynie - żeby od razu wiedzieć skąd wziąć produkt
    loc_parts = []
    if regal:
        loc_parts.append(f"<span class=material-symbols-outlined>location_on</span> Regał: <b>{regal}</b>")
    if lokalizacja:
        loc_parts.append(f"🗺 Miejsce: <b>{lokalizacja}</b>")
    if paleta:
        loc_parts.append(f"<span class=material-symbols-outlined>inventory_2</span> {paleta}")
    if loc_parts:
        msg += f"\n{'  │  '.join(loc_parts)}\n"

    # Stan magazynowy po sprzedaży
    if ilosc_zostalo is not None:
        if ilosc_zostalo == 0:
            msg += f"\n⚠️ <b>OSTATNIA SZTUKA — brak w magazynie!</b>"
        elif ilosc_zostalo <= 3:
            msg += f"\n⚠️ Zostało tylko: <b>{ilosc_zostalo} szt</b>"
        else:
            msg += f"\n📊 W magazynie: {ilosc_zostalo} szt"

    msg += f"\n\n⏰ {datetime.now():%H:%M:%S}"

    # silent=False wymusza dźwięk powiadomienia
    return send_telegram(msg, silent=False)

def alert_niski_stan(produkt_nazwa, ilosc, ean=''):
    """Wysyła alert o niskim stanie magazynowym z dźwiękiem"""
    if get_config('telegram_alert_niski_stan', 'true') != 'true':
        return False
    
    msg = f"⚠️🔔 <b>NISKI STAN!</b>\n\n"
    msg += f"📦 {produkt_nazwa}\n"
    msg += f"🔢 Zostało: <b>{ilosc} szt</b>\n"
    if ean:
        msg += f"🏷️ {ean}\n"
    msg += f"\n⏰ {datetime.now():%H:%M:%S}"
    
    return send_telegram(msg, silent=False)

def alert_nowa_oferta(tytul, cena):
    """Wysyła alert o nowej ofercie"""
    if get_config('telegram_alert_nowa_oferta', 'false') != 'true':
        return False
    
    msg = f"📦 <b>NOWA OFERTA</b>\n\n"
    msg += f"📝 {tytul}\n"
    msg += f"💵 {cena:.2f} zł\n"
    msg += f"\n⏰ {datetime.now():%H:%M:%S}"
    
    # Nowe oferty bez dźwięku (nie są pilne)
    return send_telegram(msg, silent=True)

def raport_dzienny():
    """Wysyła raport dzienny z pełnymi statystykami"""
    if get_config('telegram_raport_dzienny', 'true') != 'true':
        return False
    
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    month_start = datetime.now().strftime('%Y-%m-01')
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    
    # Dziś
    dzis = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena*ilosc), 0) as suma 
        FROM sprzedaze WHERE date(data_sprzedazy) = ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')
        AND (allegro_order_id IS NULL OR allegro_order_id NOT LIKE 'MANUAL-%')
    ''', (today,)).fetchone()
    
    # Tydzień
    tydzien = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena*ilosc), 0) as suma 
        FROM sprzedaze WHERE date(data_sprzedazy) >= ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''', (week_ago,)).fetchone()
    
    # Miesiąc
    miesiac = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena*ilosc), 0) as suma 
        FROM sprzedaze WHERE date(data_sprzedazy) >= ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''', (month_start,)).fetchone()
    
    # Magazyn
    magazyn = conn.execute('SELECT COUNT(*) as cnt, COALESCE(SUM(ilosc), 0) as szt FROM produkty WHERE status IN ("magazyn","wystawiony") AND ilosc > 0').fetchone()
    
    # Do wysłania
    do_wyslania = conn.execute("SELECT COUNT(*) FROM sprzedaze WHERE status = 'nowa'").fetchone()[0]
    
    # Wczoraj
    wczoraj = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    wczoraj_stat = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena*ilosc), 0) as suma
        FROM sprzedaze WHERE date(data_sprzedazy) = ?
        AND status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')
    ''', (wczoraj,)).fetchone()

    # Top 3 sprzedaże wczoraj
    top = conn.execute('''
        SELECT nazwa, cena, ilosc FROM sprzedaze
        WHERE date(data_sprzedazy) = ? AND status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')
        ORDER BY cena DESC LIMIT 3
    ''', (wczoraj,)).fetchall()


    msg = f"📊 <b>RAPORT DZIENNY</b>\n"
    msg += f"📅 {datetime.now():%d.%m.%Y (%A)}\n\n"

    msg += f"📦 <b>WCZORAJ:</b>\n"
    msg += f"  Sprzedaży: <b>{wczoraj_stat['cnt']}</b> szt | <b>{wczoraj_stat['suma']:.0f} zł</b>\n"

    msg += f"\n📦 <b>DZIŚ:</b>\n"
    msg += f"  Sprzedaży: <b>{dzis['cnt']}</b> szt | <b>{dzis['suma']:.0f} zł</b>\n"

    if top:
        msg += f"\n🏆 <b>TOP WCZORAJ:</b>\n"
        for t in top:
            nazwa = (t['nazwa'] or 'Produkt')[:30]
            msg += f"  • {nazwa} — {t['cena']:.0f} zł x{t['ilosc']}\n"

    msg += f"\n📈 <b>TYDZIEŃ:</b> {tydzien['cnt']} szt | <b>{tydzien['suma']:.0f} zł</b>\n"
    msg += f"📅 <b>MIESIĄC:</b> {miesiac['cnt']} szt | <b>{miesiac['suma']:.0f} zł</b>\n\n"

    msg += f"📦 Magazyn: {magazyn['cnt']} produktów ({magazyn['szt']} szt)\n"

    if do_wyslania > 0:
        msg += f"🚚 <b>DO WYSŁANIA: {do_wyslania}</b>\n"

    msg += f"\n✨ Miłego dnia!"
    
    return send_telegram(msg, silent=True)

# ============================================================
# BOT THREAD (opcjonalny - do schedulera)
# ============================================================
def start_bot():
    """Uruchamia bota w tle"""
    global _bot_running, _bot_thread
    
    if _bot_running:
        return
    
    _bot_running = True
    _bot_thread = threading.Thread(target=_bot_loop, daemon=True)
    _bot_thread.start()
    print("[Telegram] Bot uruchomiony")

def stop_bot():
    """Zatrzymuje bota"""
    global _bot_running
    _bot_running = False
    print("[Telegram] Bot zatrzymany")

def _bot_loop():
    """Główna pętla bota - raport dzienny + auto-monitoring zamówień"""
    global _bot_running
    last_report_date = None
    last_order_check = 0
    
    # Interwał sprawdzania zamówień (sekundy)
    ORDER_CHECK_INTERVAL = 300  # 5 minut
    
    while _bot_running:
        try:
            now = datetime.now()
            
            # === RAPORT DZIENNY O 9:00 ===
            if now.hour == 9 and now.minute == 0:
                today = now.strftime('%Y-%m-%d')
                # Deduplikacja przez DB — zapobiega podwojnym raportom przy wielu instancjach
                db_last = get_config('telegram_last_raport_date', '')
                if last_report_date != today and db_last != today:
                    set_config('telegram_last_raport_date', today)
                    raport_dzienny()
                    last_report_date = today
            
            # === AUTO-MONITORING ZAMÓWIEŃ ===
            if get_config('telegram_auto_monitor', 'true') == 'true':
                if time.time() - last_order_check >= ORDER_CHECK_INTERVAL:
                    try:
                        # 1. Sprawdź nowe zamówienia z API Allegro
                        new_orders = check_new_orders()
                        if new_orders:
                            print(f"[Bot] Znaleziono {len(new_orders)} nowych zamówień!")
                            for order in reversed(new_orders):
                                was_new = save_order_to_db(order)
                                if was_new:
                                    send_order_notification(order)
                                    time.sleep(1)

                        # 2. Fallback: wyślij alerty dla zamówień zapisanych przez sync_orders z notified=0
                        try:
                            from .database import get_db as _gdb
                            _conn = _gdb()
                            _unnotified = _conn.execute(
                                "SELECT id, nazwa, cena, kupujacy FROM sprzedaze WHERE notified=0 AND date(data_sprzedazy) >= date('now','-1 day') LIMIT 10"
                            ).fetchall()
                            for _un in _unnotified:
                                alert_sprzedaz(_un['nazwa'] or 'Produkt', _un['cena'] or 0, _un['kupujacy'] or '')
                                _conn.execute('UPDATE sprzedaze SET notified=1 WHERE id=?', (_un['id'],))
                                time.sleep(1)
                            if _unnotified:
                                _conn.commit()
                                print(f"[Bot] Wysłano {len(_unnotified)} zaległych powiadomień")
                        except Exception as _e2:
                            print(f"[Bot] Fallback notyfikacji: {_e2}")
                    except Exception as e:
                        print(f"[Bot] Błąd monitoringu: {e}")
                    last_order_check = time.time()
            
            time.sleep(30)  # Sprawdzaj co 30 sekund
            
        except Exception as e:
            print(f"[Telegram] Błąd w pętli: {e}")
            time.sleep(60)

# ============================================================
# SZABLONY
# ============================================================
TELEGRAM_EXTRA_CSS = '''<style>
.tg-status{display:flex;align-items:center;justify-content:space-between;padding:15px;border-radius:12px;margin-bottom:15px}
.tg-status.on{background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.3)}
.tg-status.off{background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3)}
.tg-status-info{display:flex;align-items:center;gap:12px}
.tg-status-dot{width:12px;height:12px;border-radius:50%}
.tg-status-dot.on{background:var(--green);animation:pulse 2s infinite}
.tg-status-dot.off{background:var(--red)}
.tg-status-text{font-weight:600}
.tg-status-sub{font-size:0.75rem;color:var(--text-muted)}
.tg-section{color:var(--accent);font-weight:600;font-size:0.85rem;margin:20px 0 10px;display:flex;align-items:center;gap:6px}
.btn-on{background:var(--green)}
.btn-off{background:var(--red)}
.log-icon.test{background:rgba(139,92,246,0.2)}
.log-status{font-size:0.65rem;color:var(--green)}
.alert-ok{background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.3);color:var(--green)}
</style>'''

def render(content, page_title='Powiadomienia'):
    from flask import render_template_string, session
    from flask import current_app
    template = """{% extends "base.html" %}
{% block page_title %}""" + page_title + """{% endblock %}
{% block content %}
""" + TELEGRAM_EXTRA_CSS + """
{{ content|safe }}
{% endblock %}"""
    return render_template_string(template,
        content=content,
        version=current_app.config.get('VERSION',''),
        brand_name=current_app.config.get('BRAND_NAME','Akces Hub'),
        current_user=session.get('user'))

# ============================================================
# ROUTES
# ============================================================
@telegram_bp.route('/')
def index():
    is_on = bot_status()
    token = get_bot_token()
    chat_id = get_chat_id()
    support_chat_id = get_config('support_chat_id', '')
    logs = get_logs(10)

    # WhatsApp config
    wa_key = get_whatsapp_key()
    wa_phone = get_whatsapp_phone()
    wa_on = whatsapp_enabled()
    
    # Konfiguracja alertów
    alerts = {
        'sprzedaz': get_config('telegram_alert_sprzedaz', 'true') == 'true',
        'niski_stan': get_config('telegram_alert_niski_stan', 'true') == 'true',
        'nowa_oferta': get_config('telegram_alert_nowa_oferta', 'false') == 'true',
        'raport_dzienny': get_config('telegram_raport_dzienny', 'true') == 'true',
    }
    
    status_class = 'on' if is_on else 'off'
    btn_class = 'btn-off' if is_on else 'btn-on'
    btn_text = 'WYŁĄCZ' if is_on else 'WŁĄCZ'
    
    wa_status_class = 'on' if wa_on else 'off'
    wa_btn_class = 'btn-off' if wa_on else 'btn-on'
    wa_btn_text = 'WYŁĄCZ' if wa_on else 'WŁĄCZ'
    
    html = f'''
    <div class="header"><h1><span class=material-symbols-outlined>smart_toy</span> POWIADOMIENIA</h1><small>Telegram + WhatsApp</small></div>
    
    <!-- TELEGRAM -->
    <div class="tg-section"><span class=material-symbols-outlined>smartphone</span> TELEGRAM (Adrian)</div>
    <div class="tg-status {status_class}">
        <div class="tg-status-info">
            <div class="tg-status-dot {status_class}"></div>
            <div>
                <div class="tg-status-text">{'Aktywny' if is_on else 'Wyłączony'}</div>
            </div>
        </div>
        <form action="/telegram/toggle" method="POST" style="margin:0">
            <button type="submit" class="btn {btn_class}">{btn_text}</button>
        </form>
    </div>
    
    <div class="card">
        <form action="/telegram/config" method="POST">
            <div class="form-group">
                <label>Bot Token</label>
                <input type="text" name="token" class="form-control" value="{token}" placeholder="123456:ABC...">
            </div>
            <div class="form-group">
                <label>Chat ID (powiadomienia klienta)</label>
                <input type="text" name="chat_id" class="form-control" value="{chat_id}" placeholder="123456789">
            </div>
            <div class="form-group">
                <label>Support Chat ID (zgłoszenia → do Ciebie)</label>
                <input type="text" name="support_chat_id" class="form-control" value="{support_chat_id}" placeholder="Twój osobisty chat_id dla zgłoszeń supportu">
                <small style="color:#94a3b8;display:block;margin-top:4px">Jeśli puste — zgłoszenia lecą na główny Chat ID</small>
            </div>
            <button type="submit" class="btn btn-primary"><span class=material-symbols-outlined>save</span> ZAPISZ</button>
        </form>
    </div>
    
    <!-- WHATSAPP -->
    <div class="tg-section"><span class=material-symbols-outlined>smartphone</span> WHATSAPP (Dziadek)</div>
    <div class="tg-status {wa_status_class}">
        <div class="tg-status-info">
            <div class="tg-status-dot {wa_status_class}"></div>
            <div>
                <div class="tg-status-text">{'Aktywny' if wa_on else 'Wyłączony'}</div>
                <div class="tg-status-sub">TextMeBot</div>
            </div>
        </div>
        <form action="/telegram/whatsapp/toggle" method="POST" style="margin:0">
            <button type="submit" class="btn {wa_btn_class}">{wa_btn_text}</button>
        </form>
    </div>
    
    <div class="card">
        <form action="/telegram/whatsapp/config" method="POST">
            <div class="form-group">
                <label>Numer telefonu (z +48)</label>
                <input type="text" name="phone" class="form-control" value="{wa_phone}" placeholder="+48123456789">
            </div>
            <div class="form-group">
                <label>API Key (z TextMeBot)</label>
                <input type="text" name="api_key" class="form-control" value="{wa_key}" placeholder="abc123...">
            </div>
            <button type="submit" class="btn btn-primary"><span class=material-symbols-outlined>save</span> ZAPISZ</button>
        </form>
        <form action="/telegram/whatsapp/test" method="POST" style="margin-top:10px">
            <button type="submit" class="btn btn-secondary"><span class=material-symbols-outlined>science</span> TEST WHATSAPP</button>
        </form>
    </div>
    
    <div class="card">
        <div class="card-title"><span class=material-symbols-outlined>notifications</span> POWIADOMIENIA</div>
    '''
    
    toggles = [
        ('sprzedaz', '<span class=material-symbols-outlined>payments</span> Nowa sprzedaż', alerts['sprzedaz']),
        ('niski_stan', '<span class=material-symbols-outlined>warning</span> Niski stan magazynowy', alerts['niski_stan']),
        ('nowa_oferta', '<span class=material-symbols-outlined>inventory_2</span> Nowa oferta wystawiona', alerts['nowa_oferta']),
        ('raport_dzienny', '<span class=material-symbols-outlined>bar_chart</span> Raport dzienny (9:00)', alerts['raport_dzienny']),
    ]
    
    for key, label, is_active in toggles:
        active_class = 'on' if is_active else ''
        html += f'''
        <form action="/telegram/alert/{key}" method="POST" class="toggle-row">
            <span class="toggle-label">{label}</span>
            <button type="submit" class="toggle {active_class}"><span class="toggle-knob"></span></button>
        </form>
        '''
    
    html += '''
    </div>
    
    <a href="/telegram/live" class="btn btn-primary" style="margin-bottom:10px;background:linear-gradient(135deg,#22c55e,#16a34a)"><span class=material-symbols-outlined>bar_chart</span> SPRZEDAŻ LIVE<br><small>Dashboard na żywo z auto-odświeżaniem</small></a>
    
    <a href="/telegram/monitor" class="btn btn-ok" style="margin-bottom:10px"><span class=material-symbols-outlined>notifications</span> MONITORING SPRZEDAŻY<br><small>Automatyczne powiadomienia o nowych zamówieniach</small></a>
    
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:15px">
        <form action="/telegram/test" method="POST" style="margin:0">
            <button type="submit" class="btn btn-secondary" style="width:100%;margin:0"><span class=material-symbols-outlined>science</span> TEST</button>
        </form>
        <form action="/telegram/clear" method="POST" style="margin:0">
            <input type="hidden" name="days" value="1">
            <button type="submit" class="btn btn-secondary" style="width:100%;margin:0;background:rgba(239,68,68,0.2);border-color:rgba(239,68,68,0.3)"><span class=material-symbols-outlined>mop</span> WYCZYŚĆ CZAT</button>
        </form>
    </div>
    
    <div class="tg-section">📜 OSTATNIE WIADOMOŚCI</div>
    '''
    
    for log in logs:
        typ = log['typ'] or 'test'
        icon_map = {'sale': '<span class=material-symbols-outlined>payments</span>', 'alert': '<span class=material-symbols-outlined>warning</span>', 'report': '<span class=material-symbols-outlined>bar_chart</span>', 'test': '<span class=material-symbols-outlined>science</span>', 'sent': '<span class=material-symbols-outlined>upload</span>'}
        icon = icon_map.get(typ, '<span class=material-symbols-outlined>upload</span>')
        
        html += f'''
        <div class="log-item">
            <div class="log-icon {typ}">{icon}</div>
            <div class="log-content">
                <div class="log-msg">{log['wiadomosc'][:50]}...</div>
                <div class="log-time">{log['data']}</div>
            </div>
            <div class="log-status">✓ sent</div>
        </div>
        '''
    
    if not logs:
        html += '<div style="text-align:center;color:#64748b;padding:20px">Brak wiadomości</div>'
    
    html += '<a href="/" class="back">← Powrót</a>'
    
    return render(html)

@telegram_bp.route('/toggle', methods=['POST'])
def toggle():
    """Włącza/wyłącza bota"""
    current = get_config('telegram_enabled', 'true')
    new_value = 'false' if current == 'true' else 'true'
    set_config('telegram_enabled', new_value)
    return redirect('/telegram')

@telegram_bp.route('/config', methods=['POST'])
def config():
    """Zapisuje konfigurację"""
    token = request.form.get('token', '').strip()
    chat_id = request.form.get('chat_id', '').strip()
    
    set_config('telegram_bot_token', token)
    set_config('telegram_chat_id', chat_id)

    # Support chat_id (osobny, do zgłoszeń klientów → właściciel)
    support_chat = request.form.get('support_chat_id', '').strip()
    if support_chat:
        set_config('support_chat_id', support_chat)

    return redirect('/telegram')


# ============================================================
# WHATSAPP ENDPOINTS
# ============================================================
@telegram_bp.route('/whatsapp/toggle', methods=['POST'])
def whatsapp_toggle():
    """Włącza/wyłącza WhatsApp"""
    current = get_config('whatsapp_enabled', 'false')
    new_value = 'false' if current == 'true' else 'true'
    set_config('whatsapp_enabled', new_value)
    return redirect('/telegram')


@telegram_bp.route('/whatsapp/config', methods=['POST'])
def whatsapp_config():
    """Zapisuje konfigurację WhatsApp"""
    phone = request.form.get('phone', '').strip()
    api_key = request.form.get('api_key', '').strip()
    
    set_config('whatsapp_phone', phone)
    set_config('whatsapp_api_key', api_key)
    
    return redirect('/telegram')


@telegram_bp.route('/whatsapp/test', methods=['POST'])
def whatsapp_test():
    """Wysyła wiadomość testową na WhatsApp"""
    msg = f"🧪 TEST z {get_config('brand_name', 'Akces Hub')}\n⏰ {datetime.now():%H:%M:%S}"
    success = send_whatsapp(msg)
    
    if success:
        return redirect('/telegram')
    else:
        return render('''
            <div class="header"><h1><span class=material-symbols-outlined style=color:#ef4444>cancel</span> BŁĄD</h1></div>
            <div class="alert" style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);color:#ef4444">
                Nie udało się wysłać WhatsApp.<br>
                Sprawdź numer i API key.
            </div>
            <a href="/telegram" class="btn btn-primary">← Powrót</a>
        ''')


@telegram_bp.route('/alert/<key>', methods=['POST'])
def toggle_alert(key):
    """Przełącza alert"""
    config_key = f'telegram_alert_{key}'
    current = get_config(config_key, 'false')
    new_value = 'false' if current == 'true' else 'true'
    set_config(config_key, new_value)
    return redirect('/telegram')

@telegram_bp.route('/test', methods=['POST'])
def test():
    """Wysyła wiadomość testową"""
    msg = f"🧪 <b>TEST</b>\n\nWiadomość testowa z {get_config('brand_name', 'Akces Hub')}\n⏰ {datetime.now():%H:%M:%S}"
    success = send_telegram(msg)
    
    if success:
        return redirect('/telegram')
    else:
        return render('''
            <div class="header"><h1><span class=material-symbols-outlined style=color:#ef4444>cancel</span> BŁĄD</h1></div>
            <div class="alert" style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);color:#ef4444">
                Nie udało się wysłać wiadomości.<br>
                Sprawdź token i chat ID.
            </div>
            <a href="/telegram" class="btn btn-primary">← Powrót</a>
        ''')


@telegram_bp.route('/clear', methods=['POST'])
def clear_chat():
    """Czyści stare wiadomości z czatu Telegram - w tle"""
    import threading
    
    def cleanup_task():
        clear_telegram_chat()
    
    # Uruchom w tle
    threading.Thread(target=cleanup_task, daemon=True).start()
    
    return f'''
    <html><head><meta http-equiv="refresh" content="1;url=/telegram"></head>
    <body style="background:#0a0a0f;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center">
            <div style="font-size:3rem;margin-bottom:20px"><span class=material-symbols-outlined>mop</span></div>
            <div style="font-size:1.2rem">Czyszczenie w tle...</div>
            <div style="color:#64748b;margin-top:10px">Sprawdź Telegram za chwilę</div>
        </div>
    </body></html>
    '''


# ============================================================
# API
# ============================================================
@telegram_bp.route('/api/send', methods=['POST'])
def api_send():
    """API do wysyłania wiadomości"""
    data = request.json or {}
    message = data.get('message', '')
    
    if not message:
        return jsonify({'success': False, 'error': 'No message'}), 400
    
    success = send_telegram(message)
    return jsonify({'success': success})

@telegram_bp.route('/api/status')
def api_status():
    """API status bota"""
    return jsonify({
        'enabled': bot_status(),
        'configured': bool(get_bot_token() and get_chat_id())
    })

@telegram_bp.route('/test-raport')
def test_raport():
    """Wysyła testowy raport dzienny na Telegram"""
    ok = raport_dzienny()
    if ok:
        return jsonify({'ok': True, 'message': 'Raport wysłany na Telegram!'})
    return jsonify({'ok': False, 'error': 'Nie udało się wysłać (bot wyłączony lub brak konfiguracji)'})


# ============================================================
# MONITORING SPRZEDAŻY ALLEGRO
# ============================================================
_monitor_running = False
_monitor_thread = None
_last_check_time = None

def get_last_order_id():
    """Pobiera ID ostatniego sprawdzonego zamówienia"""
    return get_config('allegro_last_order_id', '')

def set_last_order_id(order_id):
    """Zapisuje ID ostatniego sprawdzonego zamówienia"""
    set_config('allegro_last_order_id', order_id)

def check_new_orders():
    """Sprawdza nowe zamówienia z Allegro API"""
    global _last_check_time
    
    try:
        from .allegro_api import is_authenticated, allegro_request
        
        if not is_authenticated():
            return []
        
        # Pobierz zamówienia z ostatnich 24h
        result, error = allegro_request('GET', '/order/checkout-forms', params={
            'status': 'READY_FOR_PROCESSING',
            'limit': 20
        })
        
        if error or not result:
            print(f"[Monitor] Błąd pobierania zamówień: {error}")
            return []
        
        orders = result.get('checkoutForms', [])
        last_known_id = get_last_order_id()
        new_orders = []
        
        for order in orders:
            order_id = order.get('id', '')
            
            # Jeśli to nowe zamówienie
            if last_known_id and order_id == last_known_id:
                break  # Doszliśmy do ostatniego znanego
            
            new_orders.append(order)
        
        # Zapisz najnowsze ID
        if orders:
            set_last_order_id(orders[0].get('id', ''))
        
        _last_check_time = datetime.now()
        
        return new_orders
        
    except Exception as e:
        print(f"[Monitor] Wyjątek: {e}")
        return []

def format_order_notification(order):
    """Formatuje powiadomienie o zamówieniu"""
    try:
        # Dane zamówienia
        order_id = order.get('id', 'N/A')
        
        # Produkty
        items = order.get('lineItems', [])
        produkty_txt = ""
        total = 0
        
        for item in items:
            nazwa = item.get('offer', {}).get('name', 'Produkt')[:40]
            qty = item.get('quantity', 1)
            price = float(item.get('price', {}).get('amount', 0))
            total += price * qty
            produkty_txt += f"<span class=material-symbols-outlined>inventory_2</span> {nazwa}\n   {qty} szt × {price:.2f} zł\n"
        
        # Kupujący
        buyer = order.get('buyer', {})
        buyer_name = f"{buyer.get('firstName', '')} {buyer.get('lastName', '')}"
        buyer_login = buyer.get('login', '')
        
        # Adres
        delivery = order.get('delivery', {}).get('address', {})
        miasto = delivery.get('city', '')
        kod = delivery.get('postCode', '')
        ulica = delivery.get('street', '')
        
        # Formatuj wiadomość
        msg = f"🎉 <b>NOWA SPRZEDAŻ!</b>\n"
        msg += f"{'━'*25}\n\n"
        msg += produkty_txt
        msg += f"\n💰 <b>SUMA: {total:.2f} zł</b>\n\n"
        msg += f"👤 {buyer_name}\n"
        if buyer_login:
            msg += f"🏷️ @{buyer_login}\n"
        msg += f"\n📍 <b>WYSYŁKA:</b>\n"
        msg += f"{ulica}\n{kod} {miasto}\n\n"
        msg += f"🔗 ID: <code>{order_id[:8]}...</code>\n"
        msg += f"⏰ {datetime.now():%H:%M:%S}"
        
        return msg
        
    except Exception as e:
        return f"<span class=material-symbols-outlined>celebration</span> <b>NOWA SPRZEDAŻ!</b>\n\nBłąd parsowania: {e}"

def send_order_notification(order):
    """Wysyła powiadomienie o zamówieniu"""
    msg = format_order_notification(order)
    return send_telegram(msg)

def save_order_to_db(order):
    """Zapisuje zamówienie do bazy danych"""
    try:
        from .database import get_db
        from datetime import datetime as _dt
        conn = get_db()

        order_id = order.get('id', '')

        # Sprawdź czy zamówienie już istnieje
        existing = conn.execute(
            'SELECT id FROM sprzedaze WHERE allegro_order_id = ?',
            (order_id,)
        ).fetchone()

        if existing:
            return False  # Już zapisane

        # Pobierz dane kupującego (login Allegro, nie imię)
        buyer = order.get('buyer', {})
        kupujacy = buyer.get('login', '')
        if not kupujacy:
            kupujacy = f"{buyer.get('firstName', '')} {buyer.get('lastName', '')}".strip() or 'Nieznany'

        # Data zamówienia z Allegro
        order_date_raw = order.get('boughtAt') or order.get('updatedAt') or ''
        try:
            dt_str = order_date_raw.replace('Z', '+00:00')
            dt = _dt.fromisoformat(dt_str)
            dt_local = dt.astimezone().replace(tzinfo=None)
            order_date = dt_local.strftime('%Y-%m-%d %H:%M:%S')
        except:
            order_date = _dt.now().strftime('%Y-%m-%d %H:%M:%S')

        delivery = order.get('delivery', {}).get('address', {})
        adres = f"{delivery.get('street', '')}, {delivery.get('postCode', '')} {delivery.get('city', '')}".strip(', ')

        # Zapisz każdy produkt z zamówienia
        items = order.get('lineItems', [])
        for item in items:
            offer = item.get('offer', {})
            nazwa = (offer.get('name') or 'Produkt')[:100]
            qty = item.get('quantity', 1)
            price = float(item.get('price', {}).get('amount', 0))
            offer_id = offer.get('id', '')

            # Znajdź produkt_id i oferta_id
            produkt_id = None
            oferta_db_id = None
            if offer_id:
                oferta = conn.execute('SELECT id, produkt_id FROM oferty WHERE allegro_id = ?', (offer_id,)).fetchone()
                if oferta:
                    oferta_db_id = oferta['id']
                    produkt_id = oferta['produkt_id']

            conn.execute('''
                INSERT INTO sprzedaze (allegro_order_id, cena, ilosc, kupujacy, adres, status,
                                       nazwa, data_sprzedazy, produkt_id, oferta_id, notified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ''', (order_id, price, qty, kupujacy, adres, 'nowa',
                  nazwa, order_date, produkt_id, oferta_db_id))

            # Automatyczne odejmowanie ze stanu magazynowego
            if produkt_id:
                conn.execute('''
                    UPDATE produkty SET
                        ilosc = MAX(0, ilosc - ?),
                        status = CASE WHEN ilosc - ? <= 0 THEN 'sprzedany' ELSE status END
                    WHERE id = ?
                ''', (qty, qty, produkt_id))
                print(f"[DB] [INVE] Odjęto {qty} szt. z produktu #{produkt_id}")

        conn.commit()
        print(f"[DB] Zapisano zamówienie {order_id[:8]}... ({len(items)} produktów)")
        return True

    except Exception as e:
        print(f"[DB] Błąd zapisu zamówienia: {e}")
        return False

def monitor_loop():
    """Główna pętla monitoringu"""
    global _monitor_running
    
    interval = int(get_config('allegro_monitor_interval', '300'))  # domyślnie 5 min
    last_cleanup_day = None  # Do śledzenia czy dziś już czyszczono
    
    print(f"[Monitor] Start - sprawdzam co {interval}s")
    
    while _monitor_running:
        try:
            # === AUTOMATYCZNE CZYSZCZENIE O PÓŁNOCY ===
            now = datetime.now()
            today = now.date()
            
            # Czyść raz dziennie między 00:00 a 00:10
            if now.hour == 0 and now.minute < 10 and last_cleanup_day != today:
                print("[Monitor] [MOP] Automatyczne czyszczenie czatu Telegram...")
                try:
                    deleted = clear_telegram_chat(days_old=1)
                    print(f"[Monitor] Wyczyszczono {deleted} wiadomości")
                    last_cleanup_day = today
                except Exception as e:
                    print(f"[Monitor] Błąd czyszczenia: {e}")
            
            # === SPRAWDZANIE ZAMÓWIEŃ ===
            new_orders = check_new_orders()
            
            if new_orders:
                print(f"[Monitor] Znaleziono {len(new_orders)} nowych zamówień!")
                
                # Wysyłaj od najstarszego do najnowszego
                for order in reversed(new_orders):
                    # Zapisz do bazy - wyślij powiadomienie TYLKO jeśli to nowe zamówienie
                    was_new = save_order_to_db(order)
                    if was_new:
                        send_order_notification(order)
                        time.sleep(1)  # Nie spamuj
                    
        except Exception as e:
            print(f"[Monitor] Błąd w pętli: {e}")
        
        # Czekaj przed następnym sprawdzeniem
        for _ in range(interval):
            if not _monitor_running:
                break
            time.sleep(1)
    
    print("[Monitor] Stop")

def start_monitor():
    """Uruchamia monitoring w tle"""
    global _monitor_running, _monitor_thread
    
    if _monitor_running:
        return False
    
    _monitor_running = True
    _monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    _monitor_thread.start()
    return True

def stop_monitor():
    """Zatrzymuje monitoring"""
    global _monitor_running
    _monitor_running = False
    return True

def is_monitor_running():
    """Sprawdza czy monitoring działa"""
    return _monitor_running


# ============================================================
# STRONA MONITORINGU
# ============================================================
@telegram_bp.route('/monitor')
def monitor_page():
    """Strona konfiguracji monitoringu sprzedaży"""
    from .allegro_api import is_authenticated
    
    allegro_ok = is_authenticated()
    monitor_on = is_monitor_running()
    interval = get_config('allegro_monitor_interval', '300')
    last_order = get_last_order_id()
    
    html = f'''
    <div class="header"><h1><span class=material-symbols-outlined>notifications</span> MONITORING SPRZEDAŻY</h1><small>Powiadomienia Telegram o nowych zamówieniach</small></div>
    
    <div class="card" style="padding:15px;margin-bottom:15px">
        <div style="display:flex;align-items:center;justify-content:space-between">
            <div>
                <div style="font-size:1.2rem;font-weight:600">{'● AKTYWNY' if monitor_on else '● WYŁĄCZONY'}</div>
                <div style="font-size:0.8rem;color:#64748b">{'Sprawdzam co ' + str(int(int(interval)/60)) + ' min' if monitor_on else 'Kliknij START aby włączyć'}</div>
            </div>
            <div>
                {'<a href="/telegram/monitor/stop" class="btn btn-err" style="padding:10px 20px">⏹ STOP</a>' if monitor_on else '<a href="/telegram/monitor/start" class="btn btn-ok" style="padding:10px 20px">▶ START</a>'}
            </div>
        </div>
    </div>
    '''
    
    # Status połączeń
    telegram_ok = bool(get_bot_token() and get_chat_id())
    
    html += f'''
    <div class="tg-section"><span class=material-symbols-outlined>satellite_alt</span> STATUS</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:15px">
        <div class="item">
            <span style="font-size:1.5rem;margin-right:10px">{'<span class=material-symbols-outlined style=color:#22c55e>check_circle</span>' if telegram_ok else '<span class=material-symbols-outlined style=color:#ef4444>cancel</span>'}</span>
            <div class="item-info">
                <div class="item-name">Telegram</div>
                <div class="item-meta">{'Skonfigurowany' if telegram_ok else 'Brak tokena/chat_id'}</div>
            </div>
        </div>
        <div class="item">
            <span style="font-size:1.5rem;margin-right:10px">{'<span class=material-symbols-outlined style=color:#22c55e>check_circle</span>' if allegro_ok else '<span class=material-symbols-outlined style=color:#ef4444>cancel</span>'}</span>
            <div class="item-info">
                <div class="item-name">Allegro API</div>
                <div class="item-meta">{'Połączono' if allegro_ok else 'Niezalogowany'}</div>
            </div>
        </div>
    </div>
    '''
    
    # Ustawienia
    html += f'''
    <div class="tg-section"><span class=material-symbols-outlined>settings</span> USTAWIENIA</div>
    <form action="/telegram/monitor/settings" method="POST" class="card" style="padding:15px">
        <div class="form-group">
            <label>Częstotliwość sprawdzania (sekundy)</label>
            <select name="interval" class="form-control">
                <option value="60" {'selected' if interval == '60' else ''}>1 minuta</option>
                <option value="180" {'selected' if interval == '180' else ''}>3 minuty</option>
                <option value="300" {'selected' if interval == '300' else ''}>5 minut</option>
                <option value="600" {'selected' if interval == '600' else ''}>10 minut</option>
            </select>
        </div>
        <button type="submit" class="btn btn-primary"><span class=material-symbols-outlined>save</span> ZAPISZ</button>
    </form>
    
    <div class="tg-section"><span class=material-symbols-outlined>science</span> TEST</div>
    <a href="/telegram/monitor/check" class="btn btn-secondary"><span class=material-symbols-outlined>search</span> SPRAWDŹ TERAZ</a>
    <p style="font-size:0.75rem;color:#64748b;margin-top:10px">Ostatnie znane zamówienie: {last_order[:8] if last_order else 'brak'}...</p>
    '''
    
    if not telegram_ok:
        html += '<div class="alert alert-warn" style="margin-top:15px"><span class=material-symbols-outlined>warning</span> Najpierw skonfiguruj Telegram → <a href="/telegram" style="color:#eab308">Ustawienia</a></div>'
    
    if not allegro_ok:
        html += '<div class="alert alert-warn" style="margin-top:15px"><span class=material-symbols-outlined>warning</span> Najpierw zaloguj się do Allegro → <a href="/allegro" style="color:#eab308">Połącz</a></div>'
    
    html += '<a href="/telegram" class="back">← Powrót</a>'
    return render(html, page_title='Monitoring')


@telegram_bp.route('/monitor/start')
def monitor_start():
    """Uruchamia monitoring"""
    start_monitor()
    return redirect('/telegram/monitor')

@telegram_bp.route('/monitor/stop')
def monitor_stop():
    """Zatrzymuje monitoring"""
    stop_monitor()
    return redirect('/telegram/monitor')

@telegram_bp.route('/monitor/settings', methods=['POST'])
def monitor_settings():
    """Zapisuje ustawienia monitoringu"""
    interval = request.form.get('interval', '300')
    set_config('allegro_monitor_interval', interval)
    return redirect('/telegram/monitor')

@telegram_bp.route('/monitor/check')
def monitor_check():
    """Ręczne sprawdzenie zamówień"""
    new_orders = check_new_orders()
    
    if new_orders:
        sent_count = 0
        for order in reversed(new_orders):
            # Zapisz do bazy - wyślij powiadomienie TYLKO jeśli to nowe zamówienie
            was_new = save_order_to_db(order)
            if was_new:
                send_order_notification(order)
                sent_count += 1

        return render(f'''
            <div class="header"><h1><span class=material-symbols-outlined style=color:#22c55e>check_circle</span> SPRAWDZONO</h1></div>
            <div class="alert alert-ok">Znaleziono {len(new_orders)} zamówień, wysłano {sent_count} nowych powiadomień.</div>
            <a href="/telegram/monitor" class="back">← Powrót</a>
        ''')
    else:
        return render('''
            <div class="header"><h1><span class=material-symbols-outlined style=color:#22c55e>check_circle</span> SPRAWDZONO</h1></div>
            <div class="alert" style="background:#1e1e2e">Brak nowych zamówień</div>
            <a href="/telegram/monitor" class="back">← Powrót</a>
        ''')


# ============================================================
# LIVE SALES DASHBOARD
# ============================================================
@telegram_bp.route('/live')
def live_dashboard():
    """Dashboard sprzedaży na żywo z auto-odświeżaniem"""
    
    auto_monitor = get_config('telegram_auto_monitor', 'true') == 'true'
    
    html = '''
    <div class="header">
        <h1><span class=material-symbols-outlined>bar_chart</span> SPRZEDAŻ LIVE</h1>
        <small id="last-update">Ładowanie...</small>
    </div>
    
    <!-- STATUSY -->
    <div style="display:flex;gap:8px;margin-bottom:15px;flex-wrap:wrap">
        <div id="status-monitor" style="padding:6px 12px;border-radius:20px;font-size:0.75rem;font-weight:600"></div>
        <div id="status-allegro" style="padding:6px 12px;border-radius:20px;font-size:0.75rem;font-weight:600"></div>
    </div>
    
    <!-- KAFELKI STATYSTYK -->
    <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:15px">
        <div class="card" style="text-align:center;padding:15px">
            <div style="font-size:0.65rem;color:var(--text-muted);text-transform:uppercase;margin-bottom:4px">DZIŚ</div>
            <div id="stat-today-cnt" style="font-size:2rem;font-weight:800;color:var(--green)">-</div>
            <div id="stat-today-sum" style="font-size:1rem;color:var(--text-muted)">- zł</div>
        </div>
        <div class="card" style="text-align:center;padding:15px">
            <div style="font-size:0.65rem;color:var(--text-muted);text-transform:uppercase;margin-bottom:4px">DO WYSŁANIA</div>
            <div id="stat-pending" style="font-size:2rem;font-weight:800;color:#eab308">-</div>
            <div style="font-size:0.7rem;color:var(--text-muted)">zamówień</div>
        </div>
        <div class="card" style="text-align:center;padding:15px">
            <div style="font-size:0.65rem;color:var(--text-muted);text-transform:uppercase;margin-bottom:4px">TYDZIEŃ</div>
            <div id="stat-week-cnt" style="font-size:1.3rem;font-weight:700;color:var(--accent)">-</div>
            <div id="stat-week-sum" style="font-size:0.85rem;color:var(--text-muted)">- zł</div>
        </div>
        <div class="card" style="text-align:center;padding:15px">
            <div style="font-size:0.65rem;color:var(--text-muted);text-transform:uppercase;margin-bottom:4px">MIESIĄC</div>
            <div id="stat-month-cnt" style="font-size:1.3rem;font-weight:700;color:var(--accent)">-</div>
            <div id="stat-month-sum" style="font-size:0.85rem;color:var(--text-muted)">- zł</div>
        </div>
    </div>
    
    <!-- ŚREDNI PRZYCHÓD -->
    <div class="card" style="display:flex;justify-content:space-around;padding:12px;margin-bottom:15px">
        <div style="text-align:center">
            <div style="font-size:0.6rem;color:var(--text-muted)">ŚR. DZIENNIE</div>
            <div id="stat-avg-day" style="font-weight:700">-</div>
        </div>
        <div style="text-align:center">
            <div style="font-size:0.6rem;color:var(--text-muted)">ŚR. / SZT</div>
            <div id="stat-avg-item" style="font-weight:700">-</div>
        </div>
        <div style="text-align:center">
            <div style="font-size:0.6rem;color:var(--text-muted)">ŁĄCZNIE (ALL)</div>
            <div id="stat-total" style="font-weight:700">-</div>
        </div>
    </div>
    
    <!-- OSTATNIE SPRZEDAŻE -->
    <div class="tg-section"><span class=material-symbols-outlined>notifications</span> OSTATNIE ZAMÓWIENIA</div>
    <div id="recent-orders" style="margin-bottom:15px">
        <div style="text-align:center;color:var(--text-muted);padding:20px">Ładowanie...</div>
    </div>
    
    <!-- TOP PRODUKTY -->
    <div class="tg-section"><span class=material-symbols-outlined>emoji_events</span> TOP PRODUKTY (miesiąc)</div>
    <div id="top-products" style="margin-bottom:15px">
        <div style="text-align:center;color:var(--text-muted);padding:20px">Ładowanie...</div>
    </div>
    
    <!-- AKCJE -->
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:15px">
        <a href="/telegram/monitor/check" class="btn btn-secondary" style="text-align:center;text-decoration:none"><span class=material-symbols-outlined>search</span> Sprawdź teraz</a>
        <a href="/sprzedaze" class="btn btn-secondary" style="text-align:center;text-decoration:none"><span class=material-symbols-outlined>assignment</span> Wszystkie</a>
    </div>
    ''' + f'''
    <div class="toggle-row" style="margin-bottom:15px">
        <span class="toggle-label"><span class=material-symbols-outlined>sync</span> Auto-monitoring (co 5 min)</span>
        <form action="/telegram/live/toggle-auto" method="POST" style="margin:0">
            <button type="submit" class="toggle {'on' if auto_monitor else ''}"><span class="toggle-knob"></span></button>
        </form>
    </div>
    ''' + '''
    
    <a href="/telegram" class="back">← Powiadomienia</a>
    
    <script>
    let refreshInterval = 30000; // 30s
    
    async function refreshData() {
        try {
            const resp = await fetch('/telegram/api/live-stats');
            const data = await resp.json();
            
            // Statusy
            const monEl = document.getElementById('status-monitor');
            monEl.textContent = data.monitor_running ? '● Monitoring ON' : '● Monitoring OFF';
            monEl.style.background = data.monitor_running ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)';
            monEl.style.color = data.monitor_running ? '#22c55e' : '#ef4444';
            
            const allEl = document.getElementById('status-allegro');
            allEl.textContent = data.allegro_ok ? '<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Allegro' : '<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Allegro';
            allEl.style.background = data.allegro_ok ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)';
            allEl.style.color = data.allegro_ok ? '#22c55e' : '#ef4444';
            
            // Kafelki
            document.getElementById('stat-today-cnt').textContent = data.today.cnt;
            document.getElementById('stat-today-sum').textContent = data.today.sum.toFixed(0) + ' zł';
            document.getElementById('stat-pending').textContent = data.pending;
            document.getElementById('stat-week-cnt').textContent = data.week.cnt;
            document.getElementById('stat-week-sum').textContent = data.week.sum.toFixed(0) + ' zł';
            document.getElementById('stat-month-cnt').textContent = data.month.cnt;
            document.getElementById('stat-month-sum').textContent = data.month.sum.toFixed(0) + ' zł';
            
            // Średnie
            document.getElementById('stat-avg-day').textContent = data.avg_day.toFixed(0) + ' zł';
            document.getElementById('stat-avg-item').textContent = data.avg_item.toFixed(0) + ' zł';
            document.getElementById('stat-total').textContent = data.total.sum.toFixed(0) + ' zł';
            
            // Ostatnie zamówienia
            const ordersEl = document.getElementById('recent-orders');
            if (data.recent.length === 0) {
                ordersEl.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:20px">Brak zamówień dziś</div>';
            } else {
                ordersEl.innerHTML = data.recent.map(o => `
                    <div class="log-item" style="margin-bottom:6px">
                        <div class="log-icon sale"><span class=material-symbols-outlined>payments</span></div>
                        <div class="log-content">
                            <div class="log-msg">${o.nazwa}</div>
                            <div class="log-time">${o.kupujacy} • ${o.data}</div>
                        </div>
                        <div style="font-weight:700;color:var(--green);white-space:nowrap">${o.cena.toFixed(0)} zł</div>
                    </div>
                `).join('');
            }
            
            // Top produkty
            const topEl = document.getElementById('top-products');
            if (data.top_products.length === 0) {
                topEl.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:20px">Brak danych</div>';
            } else {
                topEl.innerHTML = data.top_products.map((p, i) => `
                    <div style="display:flex;align-items:center;gap:10px;padding:8px;background:var(--bg);border-radius:8px;margin-bottom:4px">
                        <div style="font-size:1.2rem;width:30px;text-align:center">${['<span class=material-symbols-outlined>emoji_events</span>','<span class=material-symbols-outlined>emoji_events</span>','<span class=material-symbols-outlined>emoji_events</span>','4⃣','5⃣'][i] || '<span class=material-symbols-outlined>inventory_2</span>'}</div>
                        <div style="flex:1;min-width:0">
                            <div style="font-size:0.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${p.nazwa}</div>
                            <div style="font-size:0.65rem;color:var(--text-muted)">${p.cnt} szt</div>
                        </div>
                        <div style="font-weight:700;font-size:0.85rem;white-space:nowrap">${p.suma.toFixed(0)} zł</div>
                    </div>
                `).join('');
            }
            
            // Timestamp
            document.getElementById('last-update').textContent = 'Odświeżono: ' + new Date().toLocaleTimeString('pl');
            
        } catch(e) {
            console.error('Refresh error:', e);
        }
    }
    
    refreshData();
    setInterval(refreshData, refreshInterval);
    </script>
    '''

    return render(html, page_title='Sprzedaz Live')


@telegram_bp.route('/live/toggle-auto', methods=['POST'])
def toggle_auto_monitor():
    """Przełącza auto-monitoring w _bot_loop"""
    current = get_config('telegram_auto_monitor', 'true')
    new_val = 'false' if current == 'true' else 'true'
    set_config('telegram_auto_monitor', new_val)
    return redirect('/telegram/live')


@telegram_bp.route('/api/live-stats')
def api_live_stats():
    """API endpoint - statystyki na żywo dla dashboardu"""
    conn = get_db()
    
    today = datetime.now().strftime('%Y-%m-%d')
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    month_start = datetime.now().strftime('%Y-%m-01')
    days_in_month = datetime.now().day
    
    NOT_CANCELLED = " AND status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (kupujacy IS NULL OR kupujacy != 'offline')"

    # Dziś
    row = conn.execute(
        'SELECT COUNT(*) as cnt, COALESCE(SUM(cena*ilosc), 0) as suma'
        ' FROM sprzedaze WHERE date(data_sprzedazy) = ?' + NOT_CANCELLED,
        (today,)).fetchone()
    today_stats = {'cnt': row['cnt'], 'sum': row['suma']}

    # Do wysłania
    pending = conn.execute("SELECT COUNT(*) FROM sprzedaze WHERE status = 'nowa'").fetchone()[0]

    # Tydzień
    row = conn.execute(
        'SELECT COUNT(*) as cnt, COALESCE(SUM(cena*ilosc), 0) as suma'
        ' FROM sprzedaze WHERE date(data_sprzedazy) >= ?' + NOT_CANCELLED,
        (week_ago,)).fetchone()
    week_stats = {'cnt': row['cnt'], 'sum': row['suma']}

    # Miesiąc
    row = conn.execute(
        'SELECT COUNT(*) as cnt, COALESCE(SUM(cena*ilosc), 0) as suma'
        ' FROM sprzedaze WHERE date(data_sprzedazy) >= ?' + NOT_CANCELLED,
        (month_start,)).fetchone()
    month_stats = {'cnt': row['cnt'], 'sum': row['suma']}

    # Łącznie (sprzedaze + prywatne)
    row = conn.execute(
        'SELECT COUNT(*) as cnt, COALESCE(SUM(cena*ilosc), 0) as suma'
        ' FROM sprzedaze WHERE 1=1' + NOT_CANCELLED
    ).fetchone()
    total_cnt = row['cnt']
    total_sum = row['suma']
    # Dolicz sprzedaże prywatne
    try:
        row_pryw = conn.execute('SELECT COUNT(*) as cnt, COALESCE(SUM(kwota), 0) as suma FROM sprzedaze_prywatne').fetchone()
        total_cnt += row_pryw['cnt'] or 0
        total_sum += row_pryw['suma'] or 0
    except:
        pass
    total_stats = {'cnt': total_cnt, 'sum': total_sum}
    
    # Średnia dzienna (z miesiąca)
    avg_day = month_stats['sum'] / max(days_in_month, 1)
    avg_item = month_stats['sum'] / max(month_stats['cnt'], 1)
    
    # Ostatnie 15 zamówień (bez offline)
    recent = conn.execute('''
        SELECT s.nazwa, s.cena, s.ilosc, s.kupujacy, s.data_sprzedazy, s.status,
               COALESCE(NULLIF(s.nazwa,''), p.nazwa) as produkt_nazwa
        FROM sprzedaze s
        LEFT JOIN produkty p ON s.produkt_id = p.id
        WHERE date(s.data_sprzedazy) >= ?
          AND s.status NOT IN ('zwrot', 'anulowane', 'anulowana') AND (s.kupujacy IS NULL OR s.kupujacy != 'offline')
         
        ORDER BY s.data_sprzedazy DESC LIMIT 15
    ''', (week_ago,)).fetchall()

    recent_list = []
    for r in recent:
        nazwa = r['produkt_nazwa'] or r['nazwa'] or 'Produkt'
        data_str = ''
        try:
            dt = datetime.fromisoformat(r['data_sprzedazy'])
            data_str = dt.strftime('%d.%m %H:%M')
        except:
            data_str = str(r['data_sprzedazy'])[:16]
        
        recent_list.append({
            'nazwa': nazwa[:40],
            'cena': r['cena'] * r['ilosc'],
            'kupujacy': (r['kupujacy'] or '')[:20],
            'data': data_str,
            'status': r['status']
        })
    
    # Top produkty miesiąca
    top = conn.execute(
        'SELECT nazwa, COUNT(*) as cnt, SUM(cena*ilosc) as suma'
        ' FROM sprzedaze WHERE date(data_sprzedazy) >= ?' + NOT_CANCELLED +
        " AND nazwa IS NOT NULL AND nazwa != ''"
        ' GROUP BY nazwa ORDER BY suma DESC LIMIT 5',
        (month_start,)).fetchall()
    
    top_list = [{'nazwa': (t['nazwa'] or 'Produkt')[:35], 'cnt': t['cnt'], 'suma': t['suma']} for t in top]
    
    
    # Monitor/Allegro status
    try:
        from .allegro_api import is_authenticated
        allegro_ok = is_authenticated()
    except:
        allegro_ok = False
    
    return jsonify({
        'today': today_stats,
        'pending': pending,
        'week': week_stats,
        'month': month_stats,
        'total': total_stats,
        'avg_day': avg_day,
        'avg_item': avg_item,
        'recent': recent_list,
        'top_products': top_list,
        'monitor_running': is_monitor_running() or (get_config('telegram_auto_monitor', 'true') == 'true' and _bot_running),
        'allegro_ok': allegro_ok,
        'timestamp': datetime.now().isoformat()
    })
