"""
Modul zdalnego supportu — zgłaszanie problemów, info o systemie, zdalny dostep
"""
import os
import platform
import subprocess
from datetime import datetime
from flask import Blueprint, request, redirect, session, flash, jsonify, current_app

support_bp = Blueprint('support', __name__)


def _get_system_info():
    """Zbiera informacje o systemie do diagnostyki"""
    info = {}

    # Podstawowe info
    info['hostname'] = platform.node()
    info['platform'] = platform.platform()
    info['python'] = platform.python_version()
    info['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Wersja aplikacji
    try:
        from app import VERSION
        info['app_version'] = VERSION
    except:
        info['app_version'] = '?'

    # Uptime i zasoby (tylko na Linux/Pi)
    try:
        import psutil
        info['cpu_percent'] = f"{psutil.cpu_percent(interval=1)}%"
        mem = psutil.virtual_memory()
        info['ram_used'] = f"{mem.used // (1024*1024)} MB / {mem.total // (1024*1024)} MB ({mem.percent}%)"
        disk = psutil.disk_usage('/')
        info['disk_used'] = f"{disk.used // (1024**3)} GB / {disk.total // (1024**3)} GB ({disk.percent}%)"

        # Uptime
        import time
        boot = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot
        days = uptime.days
        hours = uptime.seconds // 3600
        info['uptime'] = f"{days}d {hours}h"
    except:
        info['cpu_percent'] = '?'
        info['ram_used'] = '?'
        info['disk_used'] = '?'
        info['uptime'] = '?'

    # Temperatura CPU (Raspberry Pi)
    try:
        temp = subprocess.check_output(['vcgencmd', 'measure_temp'], timeout=5).decode().strip()
        info['cpu_temp'] = temp.replace('temp=', '')
    except:
        info['cpu_temp'] = '?'

    # DB size
    try:
        db_path = current_app.config.get('DATABASE', 'akces_hub.db')
        if os.path.exists(db_path):
            size_mb = os.path.getsize(db_path) / (1024 * 1024)
            info['db_size'] = f"{size_mb:.1f} MB"
        else:
            info['db_size'] = '?'
    except:
        info['db_size'] = '?'

    # Ngrok URL
    try:
        import requests as req
        r = req.get('http://127.0.0.1:4040/api/tunnels', timeout=3)
        tunnels = r.json().get('tunnels', [])
        if tunnels:
            info['ngrok_url'] = tunnels[0].get('public_url', '?')
        else:
            info['ngrok_url'] = 'brak tunelu'
    except:
        info['ngrok_url'] = 'ngrok niedostepny'

    # Ostatnie logi błędów
    try:
        result = subprocess.check_output(
            ['journalctl', '-u', 'akceshub', '-n', '10', '--no-pager', '-p', 'err'],
            timeout=5
        ).decode().strip()
        info['recent_errors'] = result[-500:] if result else 'brak bledow'
    except:
        info['recent_errors'] = 'brak dostepu do journalctl'

    return info


def _format_telegram_message(info, user_message=''):
    """Formatuje wiadomosc Telegram z info o systemie"""
    from .database import get_config
    brand = get_config('brand_name', 'AKCES HUB')

    msg = f"🆘 <b>ZGŁOSZENIE SUPPORTU</b>\n"
    msg += f"━━━━━━━━━━━━━━━━━━\n"
    msg += f"📍 <b>Instancja:</b> {brand} ({info.get('hostname', '?')})\n"
    msg += f"🕐 <b>Czas:</b> {info.get('time', '?')}\n"
    msg += f"📦 <b>Wersja:</b> {info.get('app_version', '?')}\n"
    msg += f"🌐 <b>Ngrok:</b> {info.get('ngrok_url', '?')}\n"

    kontakt = info.get('kontakt', '')
    if kontakt:
        msg += f"\n📋 <b>Kontakt klienta:</b>\n{kontakt}\n"

    if user_message:
        msg += f"\n💬 <b>Opis problemu:</b>\n{user_message}\n"

    msg += f"\n📊 <b>System:</b>\n"
    msg += f"  CPU: {info.get('cpu_percent', '?')} | Temp: {info.get('cpu_temp', '?')}\n"
    msg += f"  RAM: {info.get('ram_used', '?')}\n"
    msg += f"  Dysk: {info.get('disk_used', '?')}\n"
    msg += f"  DB: {info.get('db_size', '?')}\n"
    msg += f"  Uptime: {info.get('uptime', '?')}\n"

    errors = info.get('recent_errors', '')
    if errors and errors != 'brak bledow' and errors != 'brak dostepu do journalctl':
        msg += f"\n🔴 <b>Ostatnie bledy:</b>\n<code>{errors[:300]}</code>\n"

    msg += f"\n━━━━━━━━━━━━━━━━━━"
    return msg


@support_bp.route('/support/zgloszenie', methods=['GET', 'POST'])
def support_zgloszenie():
    """Strona zgłaszania problemu + wysyłka na Telegram"""

    if request.method == 'POST':
        user_message = request.form.get('opis', '').strip()
        kontakt_nazwa = request.form.get('kontakt_nazwa', '').strip()
        kontakt_email = request.form.get('kontakt_email', '').strip()
        kontakt_telefon = request.form.get('kontakt_telefon', '').strip()

        if not user_message:
            flash('Opisz problem przed wysłaniem', 'error')
            return redirect('/support/zgloszenie')

        # Zbierz info o systemie
        info = _get_system_info()

        # Dodaj dane kontaktowe klienta
        kontakt_info = ''
        if kontakt_nazwa:
            kontakt_info += f"👤 {kontakt_nazwa}\n"
        if kontakt_email:
            kontakt_info += f"📧 {kontakt_email}\n"
        if kontakt_telefon:
            kontakt_info += f"📱 {kontakt_telefon}\n"
        info['kontakt'] = kontakt_info.strip()

        # Wyślij na Telegram (hardcoded support — zawsze do właściciela)
        SUPPORT_BOT_TOKEN = '8538336125:AAE4qMWsz2tth9RKJ3zT9SqqKetpGHyS6GY'
        SUPPORT_CHAT_ID = '5441603126'
        try:
            import requests as _req
            msg = _format_telegram_message(info, user_message)
            resp = _req.post(
                f'https://api.telegram.org/bot{SUPPORT_BOT_TOKEN}/sendMessage',
                json={'chat_id': SUPPORT_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
                timeout=10
            )
            if resp.ok and resp.json().get('ok'):
                flash('✅ Zgłoszenie wysłane! Odpowiemy w ciągu 24h.', 'success')
            else:
                flash('⚠️ Nie udało się wysłać. Spróbuj ponownie.', 'warning')
        except Exception as e:
            flash(f'❌ Błąd wysyłania: {str(e)}', 'error')

        return redirect('/support/zgloszenie')

    # GET — formularz
    from .database import get_config
    brand = get_config('brand_name', 'AKCES HUB')

    # Flash messages
    flash_html = ''
    try:
        from flask import get_flashed_messages
        messages = get_flashed_messages(with_categories=True)
        for category, message in messages:
            if category == 'success':
                bg, color, border = '#064e3b', '#34d399', '#065f46'
            elif category == 'error':
                bg, color, border = '#450a0a', '#fca5a5', '#7f1d1d'
            else:
                bg, color, border = '#422006', '#fbbf24', '#713f12'
            flash_html += f'<div style="padding:14px 18px;border-radius:10px;margin-bottom:16px;font-weight:600;background:{bg};color:{color};border:1px solid {border}">{message}</div>'
    except Exception:
        pass

    try:
        from modules.shared import CSS
        css = CSS
    except:
        css = ''

    # Dane kontaktowe supportu
    s_email = get_config('support_email', '')
    s_phone = get_config('support_phone', '')
    s_info = get_config('support_info', '')
    s_email_html = f'<div style="color:#93c5fd;margin-bottom:4px"><a href="mailto:{s_email}" style="color:#93c5fd;text-decoration:none">📧 {s_email}</a></div>' if s_email else ''
    s_phone_html = f'<div style="color:#93c5fd;margin-bottom:4px"><a href="tel:{s_phone}" style="color:#93c5fd;text-decoration:none">📱 {s_phone}</a></div>' if s_phone else ''
    s_info_html = f'<div style="color:#94a3b8;font-size:0.85rem;margin-top:8px">{s_info}</div>' if s_info else '<div style="color:#64748b;font-size:0.85rem">Odpowiadamy zazwyczaj w ciagu 24h</div>'

    html = f'''<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pomoc techniczna — {brand}</title>
<style>{css}</style>
<style>
body {{ background: #0a0a0f; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; padding: 20px; }}
.container {{ max-width: 600px; margin: 0 auto; }}
.header {{ text-align: center; margin-bottom: 30px; }}
.header h1 {{ font-size: 1.5rem; margin: 0; }}
.header p {{ color: #94a3b8; margin-top: 8px; }}
.card {{ background: #12121a; border: 1px solid #1e1e2e; border-radius: 12px; padding: 24px; margin-bottom: 20px; }}
.form-group {{ margin-bottom: 16px; }}
.form-group label {{ display: block; font-weight: 600; margin-bottom: 8px; color: #e2e8f0; }}
.form-group textarea {{ width: 100%; min-height: 150px; background: #1a1a2e; border: 1px solid #2d2d44; border-radius: 8px; padding: 12px; color: #e2e8f0; font-size: 1rem; resize: vertical; box-sizing: border-box; }}
.form-group textarea:focus {{ outline: none; border-color: #6366f1; }}
.btn-send {{ background: #6366f1; color: #fff; border: none; padding: 14px 28px; border-radius: 10px; font-size: 1rem; font-weight: 600; cursor: pointer; width: 100%; }}
.btn-send:hover {{ background: #4f46e5; }}
.tips {{ color: #94a3b8; font-size: 0.85rem; }}
.tips li {{ margin-bottom: 6px; }}
.back-link {{ display: inline-block; margin-bottom: 20px; color: #6366f1; text-decoration: none; }}
</style>
</head><body>
<div class="container">
    <a href="/ustawienia" class="back-link">← Ustawienia</a>

    {flash_html}

    <div class="header">
        <h1>🆘 Pomoc techniczna</h1>
        <p>Opisz swój problem, a skontaktujemy się z Tobą</p>
    </div>

    <div class="card">
        <form method="POST">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px">
                <div>
                    <label style="display:block;font-weight:600;margin-bottom:6px;font-size:0.9rem">Imie / Firma</label>
                    <input type="text" name="kontakt_nazwa" placeholder="Jan Kowalski"
                        style="width:100%;padding:10px;background:#1a1a2e;border:1px solid #2d2d44;border-radius:8px;color:#e2e8f0;font-size:0.9rem;box-sizing:border-box">
                </div>
                <div>
                    <label style="display:block;font-weight:600;margin-bottom:6px;font-size:0.9rem">Telefon</label>
                    <input type="tel" name="kontakt_telefon" placeholder="+48 123 456 789"
                        style="width:100%;padding:10px;background:#1a1a2e;border:1px solid #2d2d44;border-radius:8px;color:#e2e8f0;font-size:0.9rem;box-sizing:border-box">
                </div>
            </div>
            <div style="margin-bottom:16px">
                <label style="display:block;font-weight:600;margin-bottom:6px;font-size:0.9rem">Email</label>
                <input type="email" name="kontakt_email" placeholder="jan@firma.pl"
                    style="width:100%;padding:10px;background:#1a1a2e;border:1px solid #2d2d44;border-radius:8px;color:#e2e8f0;font-size:0.9rem;box-sizing:border-box">
            </div>
            <div class="form-group">
                <label>Opis problemu</label>
                <textarea name="opis" placeholder="Co nie działa? Opisz krok po kroku co robiłeś i co się stało...&#10;&#10;Np.: Klikam 'Dodaj paletę', wpisuję dane i po kliknięciu 'Zapisz' wyskakuje błąd..." required></textarea>
            </div>

            <div class="tips" style="margin-bottom: 16px;">
                <p style="margin-bottom: 6px;"><b>💡 Wskazówki:</b></p>
                <ul style="margin: 0; padding-left: 20px;">
                    <li>Opisz dokładnie co klikałeś</li>
                    <li>Podaj jaki błąd wyskoczył (jeśli jakiś był)</li>
                    <li>Napisz na jakiej stronie/zakładce to się dzieje</li>
                </ul>
            </div>

            <div style="margin-bottom:16px">
                <label style="display:flex;align-items:flex-start;gap:10px;cursor:pointer;font-size:0.8rem;color:#94a3b8;line-height:1.4">
                    <input type="checkbox" name="rodo" required style="width:18px;height:18px;margin-top:2px;flex-shrink:0;accent-color:#6366f1">
                    <span>Wyrazam zgode na przetwarzanie moich danych osobowych w celu obslugi zgloszenia zgodnie z <a href="/polityka-prywatnosci" target="_blank" style="color:#6366f1">Polityka Prywatnosci</a>. Dane beda przetwarzane wylacznie do rozwiazania problemu.</span>
                </label>
            </div>

            <button type="submit" class="btn-send">📨 Wyślij zgłoszenie</button>
        </form>
    </div>

    <div class="card" style="text-align: center;">
        <p style="margin: 0 0 12px; color: #94a3b8;">Po wysłaniu zgłoszenia otrzymamy powiadomienie<br>z pełną diagnostyką systemu.</p>
        <div style="border-top:1px solid #2d2d44;padding-top:12px;margin-top:12px">
            <div style="font-weight:600;margin-bottom:8px;color:#e2e8f0">📞 Kontakt z supportem</div>
            {s_email_html}
            {s_phone_html}
            {s_info_html}
        </div>
    </div>
</div>
</body></html>'''

    return html


@support_bp.route('/polityka-prywatnosci')
def polityka_prywatnosci():
    """Polityka prywatności RODO"""
    from .database import get_config
    brand = get_config('brand_name', 'AKCES HUB')
    support_email = get_config('support_email', 'kontakt@firma.pl')

    return f'''<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Polityka Prywatności — {brand}</title>
<style>
body {{ background:#0a0a0f;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,sans-serif;margin:0;padding:20px;line-height:1.6 }}
.container {{ max-width:700px;margin:auto }}
h1 {{ color:#fff;font-size:1.5rem }}
h2 {{ color:#93c5fd;font-size:1.1rem;margin-top:25px }}
a {{ color:#6366f1 }}
.back {{ display:inline-block;margin-bottom:20px;color:#6366f1;text-decoration:none }}
</style>
</head><body>
<div class="container">
    <a href="javascript:history.back()" class="back">← Powrot</a>
    <h1>📋 Polityka Prywatnosci</h1>
    <p><strong>{brand}</strong> — ostatnia aktualizacja: marzec 2026</p>

    <h2>1. Administrator danych</h2>
    <p>Administratorem danych osobowych jest operator systemu {brand}. Kontakt: {support_email}</p>

    <h2>2. Jakie dane zbieramy</h2>
    <ul>
        <li>Dane podane w formularzu zgloszenia: imie/firma, email, telefon</li>
        <li>Dane techniczne systemu (diagnostyka serwera)</li>
        <li>Dane logowania: login, zaszyfrowane haslo</li>
    </ul>

    <h2>3. Cel przetwarzania</h2>
    <ul>
        <li>Obsluga zgloszen technicznych (art. 6 ust. 1 lit. a RODO — zgoda)</li>
        <li>Swiadczenie uslugi (art. 6 ust. 1 lit. b RODO — umowa)</li>
        <li>Diagnostyka i naprawa bledow systemu</li>
    </ul>

    <h2>4. Okres przechowywania</h2>
    <p>Dane ze zgloszen przechowujemy przez okres obslugi zgloszenia, nie dluzej niz 12 miesiecy od zamkniecia sprawy.</p>

    <h2>5. Twoje prawa</h2>
    <p>Masz prawo do: dostepu do danych, sprostowania, usuniecia, ograniczenia przetwarzania, przenoszenia danych oraz sprzeciwu. Kontakt: {support_email}</p>

    <h2>6. Bezpieczenstwo</h2>
    <p>Dane sa przechowywane na zabezpieczonym serwerze. Hasla sa szyfrowane. Polaczenie jest chronione przez HTTPS.</p>

    <h2>7. Udostepnianie danych</h2>
    <p>Nie udostepniamy danych osobowych podmiotom trzecim, chyba ze wymaga tego prawo.</p>
</div>
</body></html>'''


@support_bp.route('/api/support/system-info')
def api_support_system_info():
    """API: zwraca info o systemie (dla admina)"""
    if session.get('rola') != 'admin':
        return jsonify({'error': 'Brak uprawnień'}), 403

    info = _get_system_info()
    return jsonify(info)


@support_bp.route('/api/support/heartbeat')
def api_support_heartbeat():
    """Heartbeat endpoint — monitoring czy instancja żyje.
    Może być pingowany co X minut z centralnego serwera."""
    from .database import get_config

    info = {
        'status': 'ok',
        'time': datetime.now().isoformat(),
        'hostname': platform.node(),
        'brand': get_config('brand_name', 'AKCES HUB'),
    }

    try:
        from app import VERSION
        info['version'] = VERSION
    except:
        pass

    return jsonify(info)
