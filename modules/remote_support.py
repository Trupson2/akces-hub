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

        if not user_message:
            flash('Opisz problem przed wysłaniem', 'error')
            return redirect('/support/zgloszenie')

        # Zbierz info o systemie
        info = _get_system_info()

        # Wyślij na Telegram
        try:
            from .telegram_bot import send_telegram_support
            msg = _format_telegram_message(info, user_message)
            result = send_telegram_support(msg, parse_mode='HTML')

            if result:
                flash('✅ Zgłoszenie wysłane! Skontaktujemy się wkrótce.', 'success')
            else:
                flash('⚠️ Zgłoszenie zapisane, ale nie udało się wysłać powiadomienia. Spróbuj ponownie.', 'warning')
        except Exception as e:
            flash(f'❌ Błąd wysyłania: {str(e)}', 'error')

        return redirect('/support/zgloszenie')

    # GET — formularz
    from .database import get_config
    brand = get_config('brand_name', 'AKCES HUB')

    try:
        from app import CSS
        css = CSS
    except:
        css = ''

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

    <div class="header">
        <h1>🆘 Pomoc techniczna</h1>
        <p>Opisz swój problem, a skontaktujemy się z Tobą</p>
    </div>

    <div class="card">
        <form method="POST">
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

            <button type="submit" class="btn-send">📨 Wyślij zgłoszenie</button>
        </form>
    </div>

    <div class="card" style="text-align: center;">
        <p style="margin: 0; color: #94a3b8;">Po wysłaniu zgłoszenia otrzymamy powiadomienie<br>z pełną diagnostyką systemu. Nie musisz nic więcej robić.</p>
    </div>
</div>
</body></html>'''

    return html


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
