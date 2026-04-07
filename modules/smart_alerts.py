"""
Smart Alerts — inteligentne alerty i raporty
1. Martwy stock (oferty 30+ dni bez sprzedaży)
2. Codzienny raport (podsumowanie dnia o 20:00)
3. Auto-sugestia obniżki (dużo views, 0 sprzedaży)
"""

import threading
import time
from datetime import datetime, timedelta, date


def _get_db():
    from .database import get_db
    return get_db()


def _send(msg, silent=True):
    from .telegram_bot import send_telegram
    return send_telegram(msg, silent=silent)


# ============================================================
# 1. MARTWY STOCK — oferty aktywne 30+ dni bez sprzedaży
# ============================================================
def check_dead_stock():
    """Sprawdź oferty bez sprzedaży od 30+ dni. Zwraca listę."""
    conn = _get_db()
    cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

    dead = conn.execute('''
        SELECT o.id, o.tytul, o.cena, o.wyswietlenia, o.data_wystawienia, o.allegro_id,
               p.id as pid, p.nazwa, p.ilosc, p.kod_magazynowy
        FROM oferty o
        JOIN produkty p ON p.id = o.produkt_id
        LEFT JOIN sprzedaze s ON s.produkt_id = p.id
            AND s.data_sprzedazy > ?
            AND COALESCE(s.status, '') NOT IN ('anulowana', 'anulowane', 'zwrot')
        WHERE o.status = 'aktywna'
          AND o.data_wystawienia < ?
          AND p.ilosc > 0
          AND s.id IS NULL
        ORDER BY o.wyswietlenia DESC
    ''', (cutoff, cutoff)).fetchall()

    return [dict(d) for d in dead]


def alert_dead_stock():
    """Wyślij alert o martwym stocku na Telegram."""
    dead = check_dead_stock()
    if not dead:
        return

    msg = f"🪦 <b>MARTWY STOCK — {len(dead)} ofert bez sprzedaży 30+ dni</b>\n\n"
    for i, d in enumerate(dead[:10]):
        days = (datetime.now() - datetime.strptime(str(d['data_wystawienia'])[:10], '%Y-%m-%d')).days if d.get('data_wystawienia') else '?'
        views = d.get('wyswietlenia') or 0
        msg += f"• {d['tytul'][:40]} — <b>{d['cena']:.0f} zł</b> ({views} wysw., {days} dni)\n"

    if len(dead) > 10:
        msg += f"\n...i {len(dead) - 10} więcej"

    msg += f"\n\n💡 Rozważ obniżkę cen lub odświeżenie ofert"
    _send(msg, silent=True)


# ============================================================
# 2. CODZIENNY RAPORT — podsumowanie dnia
# ============================================================
def generate_daily_report():
    """Generuj raport dzienny i wyślij na Telegram."""
    conn = _get_db()
    today = date.today().isoformat()

    # Sprzedaże dzisiaj
    sales = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(cena * ilosc), 0) as przychod, COALESCE(SUM(ilosc), 0) as szt
        FROM sprzedaze
        WHERE date(data_sprzedazy) = ? AND COALESCE(status, '') NOT IN ('anulowana', 'anulowane', 'zwrot', '')
    ''', (today,)).fetchone()

    # Top sprzedaże dziś
    top = conn.execute('''
        SELECT nazwa, cena, ilosc FROM sprzedaze
        WHERE date(data_sprzedazy) = ? AND COALESCE(status, '') NOT IN ('anulowana', 'anulowane', 'zwrot', '')
        ORDER BY cena * ilosc DESC LIMIT 3
    ''', (today,)).fetchall()

    # Stan magazynu
    mag = conn.execute('''
        SELECT COUNT(*) as produkty, COALESCE(SUM(ilosc), 0) as szt
        FROM produkty WHERE ilosc > 0
    ''').fetchone()

    # Aktywne oferty
    oferty = conn.execute('''
        SELECT COUNT(*) as cnt FROM oferty WHERE status = 'aktywna'
    ''').fetchone()

    # Niewystawione (mają stock ale brak aktywnej oferty)
    niewystawione = conn.execute('''
        SELECT COUNT(*) as cnt FROM produkty p
        WHERE p.ilosc > 0
        AND NOT EXISTS (SELECT 1 FROM oferty o WHERE o.produkt_id = p.id AND o.status = 'aktywna')
    ''').fetchone()

    # Wyświetlenia dziś (delta od wczoraj)
    views_today = conn.execute('''
        SELECT COALESCE(SUM(o.wyswietlenia), 0) as total FROM oferty o WHERE o.status = 'aktywna'
    ''').fetchone()

    cnt = sales['cnt'] or 0
    przychod = sales['przychod'] or 0
    szt = sales['szt'] or 0

    msg = f"📊 <b>RAPORT DZIENNY — {datetime.now():%d.%m.%Y}</b>\n\n"

    if cnt > 0:
        msg += f"💰 Sprzedaż: <b>{cnt} zamówień</b> ({szt} szt)\n"
        msg += f"💵 Przychód: <b>{przychod:.0f} zł</b>\n"
        if top:
            msg += f"\n🏆 Top:\n"
            for t in top:
                msg += f"  • {(t['nazwa'] or '?')[:35]} — {t['cena']:.0f} zł\n"
    else:
        msg += f"😴 Brak sprzedaży dzisiaj\n"

    msg += f"\n📦 Magazyn: <b>{mag['szt']}</b> szt ({mag['produkty']} produktów)\n"
    msg += f"🛒 Aktywne oferty: <b>{oferty['cnt']}</b>\n"

    _niewy = niewystawione['cnt'] or 0
    if _niewy > 0:
        msg += f"⚠️ Niewystawione: <b>{_niewy}</b> produktów z zapasem\n"

    msg += f"\n👁 Łącznie wyświetleń: {views_today['total']:,}".replace(',', ' ')

    _send(msg, silent=True)


# ============================================================
# 3. SUGESTIE OBNIŻKI — dużo views, 0 sprzedaży
# ============================================================
def check_price_suggestions():
    """Znajdź oferty z dużą liczbą wyświetleń ale 0 sprzedaży."""
    conn = _get_db()

    # Oferty aktywne z 50+ views, bez sprzedaży w ostatnich 30 dniach
    cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

    candidates = conn.execute('''
        SELECT o.id, o.tytul, o.cena, o.wyswietlenia, o.obserwujacych, o.allegro_id,
               p.id as pid, p.nazwa, p.cena_allegro
        FROM oferty o
        JOIN produkty p ON p.id = o.produkt_id
        LEFT JOIN sprzedaze s ON s.produkt_id = p.id
            AND s.data_sprzedazy > ?
            AND COALESCE(s.status, '') NOT IN ('anulowana', 'anulowane', 'zwrot')
        WHERE o.status = 'aktywna'
          AND o.wyswietlenia >= 50
          AND p.ilosc > 0
          AND s.id IS NULL
        ORDER BY o.wyswietlenia DESC
        LIMIT 10
    ''', (cutoff,)).fetchall()

    return [dict(c) for c in candidates]


def alert_price_suggestions():
    """Wyślij sugestie obniżek na Telegram."""
    candidates = check_price_suggestions()
    if not candidates:
        return

    msg = f"📉 <b>SUGESTIE OBNIŻKI — {len(candidates)} ofert z views ale 0 sprzedaży</b>\n\n"
    for c in candidates[:8]:
        views = c.get('wyswietlenia') or 0
        watchers = c.get('obserwujacych') or 0
        cena = c.get('cena') or 0
        # Sugerowana cena: -10% do -15% w zależności od views
        discount = 0.12 if views > 200 else 0.10
        suggested = cena * (1 - discount)
        msg += f"• {c['tytul'][:40]}\n"
        msg += f"  👁 {views} wysw. | ❤️ {watchers} obs. | 💵 {cena:.0f} zł → <b>{suggested:.0f} zł</b> (-{discount*100:.0f}%)\n"

    msg += f"\n💡 Dużo wyświetleń bez sprzedaży = cena prawdopodobnie za wysoka"
    _send(msg, silent=True)


# ============================================================
# SCHEDULER — uruchom alerty w tle
# ============================================================
def start_smart_alerts():
    """Uruchom scheduler alertów w tle."""

    def _loop():
        # Poczekaj 2 min na start apki
        time.sleep(120)

        _last_daily = None
        _last_weekly = None

        while True:
            try:
                now = datetime.now()

                # Codzienny raport o 20:00
                if now.hour == 20 and now.minute < 6 and _last_daily != now.date():
                    _last_daily = now.date()
                    try:
                        generate_daily_report()
                        print(f"[SMAR] Raport dzienny wysłany")
                    except Exception as e:
                        print(f"[WARN] Raport dzienny error: {e}")

                # Martwy stock + sugestie obniżki — co poniedziałek o 10:00
                if now.weekday() == 0 and now.hour == 10 and now.minute < 6 and _last_weekly != now.date():
                    _last_weekly = now.date()
                    try:
                        alert_dead_stock()
                        print(f"[SMAR] Dead stock alert wysłany")
                    except Exception as e:
                        print(f"[WARN] Dead stock alert error: {e}")

                    try:
                        alert_price_suggestions()
                        print(f"[SMAR] Price suggestions wysłane")
                    except Exception as e:
                        print(f"[WARN] Price suggestions error: {e}")

            except Exception as e:
                print(f"[WARN] Smart alerts loop error: {e}")

            time.sleep(300)  # Sprawdzaj co 5 minut

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t
