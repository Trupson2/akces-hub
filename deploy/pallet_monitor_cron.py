#!/usr/bin/env python3
"""
PALLET MONITOR CRON - uruchamiany przez systemd timer

Harmonogram:
  Co godzine: skan OBU źródeł (warrington + jobalots)
  Peak hours (10:xx, 16:xx): co 5 min warrington
  8:30 i 13:00: extra jobalots
"""

import sys
import os
from datetime import datetime

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)
os.chdir(APP_DIR)


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def main():
    now = datetime.now()
    hour = now.hour
    minute = now.minute

    log(f"Pallet Monitor CRON - {now:%Y-%m-%d %H:%M}")

    # Peak hours (10:xx, 16:xx) co 5 min → tylko warrington
    if (hour == 10 or hour == 16) and minute > 0:
        source = 'warrington'
        log(f"Peak hour {hour}:xx - Warrington only")
    # 8:30 i 13:00 → jobalots
    elif (hour == 8 and 25 <= minute <= 35) or (hour == 13 and minute <= 10):
        source = 'jobalots'
        log(f"Jobalots window")
    else:
        # Co godzine o :00 → oba źródła
        source = 'all'
        log(f"Hourly scan - all sources")

    log(f"Skanuje: {source}")

    try:
        from modules.pallet_monitor import run_monitor
        new_deals, all_matched = run_monitor(source=source, notify=True)
        log(f"Wynik: {len(new_deals)} nowych, {len(all_matched)} matched")
    except Exception as e:
        log(f"ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
