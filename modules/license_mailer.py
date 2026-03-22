"""
License expiry notifications - sends email to admin about expiring/expired licenses.
SMTP config stored in DB config table (not hardcoded).
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta


def get_smtp_config():
    """Read SMTP config from DB"""
    from modules.database import get_config
    return {
        'host': get_config('smtp_host', ''),
        'port': int(get_config('smtp_port', '587')),
        'user': get_config('smtp_user', ''),
        'password': get_config('smtp_password', ''),
        'from_email': get_config('smtp_from', ''),
        'admin_email': get_config('admin_email', ''),
    }


def send_email(to, subject, html_body):
    """Send email via SMTP"""
    cfg = get_smtp_config()
    if not cfg['host'] or not cfg['user'] or not cfg['password']:
        print('[Mailer] SMTP not configured, skipping email')
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = cfg['from_email'] or cfg['user']
        msg['To'] = to
        msg.attach(MIMEText(html_body, 'html'))

        with smtplib.SMTP(cfg['host'], cfg['port']) as server:
            server.starttls()
            server.login(cfg['user'], cfg['password'])
            server.sendmail(msg['From'], [to], msg.as_string())

        print(f'[Mailer] Email sent to {to}: {subject}')
        return True
    except Exception as e:
        print(f'[Mailer] Error sending email: {e}')
        return False


def check_expiring_licenses():
    """Check all licenses and send notifications for expiring/expired ones"""
    from modules.database import get_db, get_config

    admin_email = get_config('admin_email', '')
    if not admin_email:
        print('[Mailer] No admin_email configured, skipping license check')
        return

    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    week_from_now = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')

    # Find licenses expiring in exactly 7 days
    expiring_7d = conn.execute(
        "SELECT * FROM licenses_issued WHERE active=1 AND expires=?",
        (week_from_now,)
    ).fetchall()

    # Find licenses that expired today
    expired_today = conn.execute(
        "SELECT * FROM licenses_issued WHERE active=1 AND expires=?",
        (today,)
    ).fetchall()

    for lic in expiring_7d:
        subject = f'Licencja wygasa za 7 dni — {lic["client_name"] or "Klient"}'
        html = _build_expiring_html(lic)
        send_email(admin_email, subject, html)

    for lic in expired_today:
        subject = f'Licencja wygasla — {lic["client_name"] or "Klient"}'
        html = _build_expired_html(lic)
        send_email(admin_email, subject, html)

    # Also send to Telegram as backup
    _send_telegram_backup(expiring_7d, expired_today)

    total = len(expiring_7d) + len(expired_today)
    if total:
        print(f'[Mailer] Sent {total} license notifications')


def _build_expiring_html(lic):
    """Build HTML for 7-day expiry warning"""
    key_display = f'{lic["license_key"][:8]}...{lic["license_key"][-4:]}' if lic["license_key"] and len(lic["license_key"]) > 12 else (lic["license_key"] or "—")
    return f'''
    <div style="font-family:Arial;max-width:600px;margin:0 auto;background:#1a1a2e;color:#e2e8f0;padding:30px;border-radius:12px">
        <h2 style="color:#fbbf24">Przypomnienie o platnosci</h2>
        <p>Licencja klienta wygasa za <strong>7 dni</strong>:</p>
        <table style="width:100%;border-collapse:collapse;margin:20px 0">
            <tr><td style="padding:8px;color:#94a3b8">Klient:</td><td style="padding:8px;color:#fff;font-weight:700">{lic["client_name"] or "—"}</td></tr>
            <tr><td style="padding:8px;color:#94a3b8">Plan:</td><td style="padding:8px;color:#8b5cf6;font-weight:700">{lic["plan"] or "MAX"}</td></tr>
            <tr><td style="padding:8px;color:#94a3b8">Wygasa:</td><td style="padding:8px;color:#fbbf24;font-weight:700">{lic["expires"]}</td></tr>
            <tr><td style="padding:8px;color:#94a3b8">HWID:</td><td style="padding:8px;font-family:monospace;color:#64748b">{lic["hwid"] or "—"}</td></tr>
            <tr><td style="padding:8px;color:#94a3b8">Klucz:</td><td style="padding:8px;font-family:monospace;color:#64748b">{key_display}</td></tr>
        </table>
        <p style="color:#94a3b8;font-size:0.85rem">Wyslij przypomnienie o platnosci do klienta.</p>
    </div>
    '''


def _build_expired_html(lic):
    """Build HTML for expired license notification"""
    key_display = f'{lic["license_key"][:8]}...{lic["license_key"][-4:]}' if lic["license_key"] and len(lic["license_key"]) > 12 else (lic["license_key"] or "—")
    return f'''
    <div style="font-family:Arial;max-width:600px;margin:0 auto;background:#1a1a2e;color:#e2e8f0;padding:30px;border-radius:12px">
        <h2 style="color:#ef4444">Dostep zablokowany</h2>
        <p>Licencja klienta <strong>wygasla dzisiaj</strong> i dostep zostal zablokowany:</p>
        <table style="width:100%;border-collapse:collapse;margin:20px 0">
            <tr><td style="padding:8px;color:#94a3b8">Klient:</td><td style="padding:8px;color:#fff;font-weight:700">{lic["client_name"] or "—"}</td></tr>
            <tr><td style="padding:8px;color:#94a3b8">Plan:</td><td style="padding:8px;color:#8b5cf6;font-weight:700">{lic["plan"] or "MAX"}</td></tr>
            <tr><td style="padding:8px;color:#94a3b8">Wygasl:</td><td style="padding:8px;color:#ef4444;font-weight:700">{lic["expires"]}</td></tr>
            <tr><td style="padding:8px;color:#94a3b8">HWID:</td><td style="padding:8px;font-family:monospace;color:#64748b">{lic["hwid"] or "—"}</td></tr>
            <tr><td style="padding:8px;color:#94a3b8">Klucz:</td><td style="padding:8px;font-family:monospace;color:#64748b">{key_display}</td></tr>
        </table>
        <p style="color:#94a3b8;font-size:0.85rem">Skontaktuj sie z klientem w sprawie odnowienia.</p>
    </div>
    '''


def _send_telegram_backup(expiring_7d, expired_today):
    """Send backup notifications via Telegram"""
    try:
        import requests
        from modules.database import get_config

        bot_token = get_config('telegram_bot_token', '')
        chat_id = get_config('telegram_chat_id', '')

        if not bot_token or not chat_id:
            return

        for lic in expiring_7d:
            msg = (f"Licencja wygasa za 7 dni\n"
                   f"Klient: {lic['client_name']}\n"
                   f"Plan: {lic['plan']}\n"
                   f"Wygasa: {lic['expires']}")
            requests.post(
                f'https://api.telegram.org/bot{bot_token}/sendMessage',
                json={'chat_id': chat_id, 'text': msg}, timeout=10
            )

        for lic in expired_today:
            msg = (f"Licencja WYGASLA\n"
                   f"Klient: {lic['client_name']}\n"
                   f"Plan: {lic['plan']}\n"
                   f"Wygasl: {lic['expires']}")
            requests.post(
                f'https://api.telegram.org/bot{bot_token}/sendMessage',
                json={'chat_id': chat_id, 'text': msg}, timeout=10
            )
    except Exception:
        pass
