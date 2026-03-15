#!/usr/bin/env python3
"""
Dzienny raport poranny - uruchamiany przez cron/systemd timer o 8:00
1. Skanuje palety z Warrington i Jobalots
2. Wysyla raport dzienny z analiza palet + TOP okazje
3. W poniedzialek wysyla raport tygodniowy
"""
import sys, os, time

# Auto-detect app directory
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)
os.chdir(APP_DIR)

from datetime import datetime
import requests


def run_analyze_trends():
    """Odpala analyze_trends.py zeby policzyc scoring okazji przed raportem"""
    try:
        print("[analiza] Licze trendy i scoring okazji...", flush=True)
        from analyze_trends import analyze
        cnt = analyze()
        print(f"[analiza] Trendy OK: {cnt} produktow przeliczonych", flush=True)
        return True
    except Exception as e:
        print(f"[analiza] Blad analizy trendow: {e}", flush=True)
    return False


def scan_warrington():
    """Skanuje palety z warrington.store"""
    try:
        print("[scan] Skanuje Warrington...", flush=True)
        resp = requests.get('http://localhost:5000/analityka/okazje/scrape-warrington', timeout=60)
        if resp.status_code == 200:
            data = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
            cnt = data.get('count', '?')
            print(f"[scan] Warrington: {cnt} palet znalezionych", flush=True)
            return True
        else:
            print(f"[scan] Warrington: status {resp.status_code}", flush=True)
    except Exception as e:
        print(f"[scan] Warrington blad: {e}", flush=True)
    return False


def scan_jobalots():
    """Skanuje palety z jobalots.com"""
    try:
        print("[scan] Skanuje Jobalots...", flush=True)
        resp = requests.get('http://localhost:5000/analityka/okazje/scrape-jobalots', timeout=60)
        if resp.status_code == 200:
            data = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
            cnt = data.get('count', '?')
            print(f"[scan] Jobalots: {cnt} palet znalezionych", flush=True)
            return True
        else:
            print(f"[scan] Jobalots: status {resp.status_code}", flush=True)
    except Exception as e:
        print(f"[scan] Jobalots blad: {e}", flush=True)
    return False


def main():
    print(f"[raport] {datetime.now().strftime('%Y-%m-%d %H:%M')} - Start poranny...", flush=True)

    # 1. Analiza trendow - policz scoring PRZED raportem
    run_analyze_trends()
    time.sleep(1)

    # 2. Skanuj palety (wymaga dzialajacego Flask na localhost:5000)
    try:
        scan_warrington()
        time.sleep(2)
        scan_jobalots()
        time.sleep(2)
    except Exception as e:
        print(f"[scan] Blad skanowania: {e}", flush=True)

    # 3. Wyslij raport dzienny
    try:
        from modules.email_reports import send_daily_report, send_weekly_report

        ok, msg = send_daily_report()
        if ok:
            print(f"[raport] Raport dzienny wyslany!", flush=True)
        else:
            print(f"[raport] Blad dziennego: {msg}", flush=True)

        # 4. Tygodniowy w poniedzialek
        if datetime.now().weekday() == 0:
            ok2, msg2 = send_weekly_report()
            if ok2:
                print(f"[raport] Raport tygodniowy wyslany!", flush=True)
            else:
                print(f"[raport] Blad tygodniowego: {msg2}", flush=True)

    except Exception as e:
        print(f"[raport] BLAD: {e}", flush=True)
        sys.exit(1)

    print(f"[raport] Zakonczono.", flush=True)


if __name__ == '__main__':
    main()
