#!/usr/bin/env python3
"""
Ngrok tunnel service for Raspberry Pi.
Runs as systemd service, auto-reconnects, saves URL to DB config.
"""
import sys, os, time

# Auto-detect app directory (where this script's parent dir is)
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)
os.chdir(APP_DIR)

try:
    from pyngrok import ngrok, conf
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyngrok', '--break-system-packages', '--quiet'])
    from pyngrok import ngrok, conf

FLASK_PORT = 5000

def get_ngrok_token():
    """Read ngrok token from DB config or start_remote_access.py"""
    # Try DB first
    try:
        from modules.database import get_config
        token = get_config('ngrok_auth_token', '')
        if token:
            print(f"[ngrok] Token found in database", flush=True)
            return token
    except Exception as e:
        print(f"[ngrok] DB read error: {e}", flush=True)

    # Fallback: read from start_remote_access.py
    sra_path = os.path.join(APP_DIR, 'start_remote_access.py')
    try:
        with open(sra_path) as f:
            for line in f:
                if 'NGROK_AUTH_TOKEN' in line and '=' in line and '#' not in line.split('=')[0]:
                    token = line.split('=', 1)[1].strip().strip('"').strip("'")
                    if token:
                        print(f"[ngrok] Token found in start_remote_access.py", flush=True)
                        return token
    except:
        pass
    return ''

def save_url_to_config(url):
    """Save ngrok URL to DB config so QR codes work"""
    try:
        from modules.database import set_config
        set_config('app_base_url', url)
        print(f"[ngrok] URL saved to config: {url}", flush=True)
    except Exception as e:
        print(f"[ngrok] Could not save URL: {e}", flush=True)

def main():
    print(f"[ngrok] App dir: {APP_DIR}", flush=True)
    token = get_ngrok_token()
    if not token:
        print("[ngrok] ERROR: No auth token found!", flush=True)
        print("[ngrok] Set it in /ustawienia or in start_remote_access.py", flush=True)
        sys.exit(1)

    ngrok.set_auth_token(token)
    retry = 0

    while True:
        try:
            print(f"[ngrok] Connecting (attempt {retry + 1})...", flush=True)
            tunnel = ngrok.connect(FLASK_PORT, bind_tls=True)
            url = tunnel.public_url
            print(f"[ngrok] ONLINE: {url}", flush=True)
            save_url_to_config(url)
            retry = 0

            # Monitor tunnel
            while True:
                time.sleep(30)
                try:
                    tunnels = ngrok.get_tunnels()
                    if not tunnels:
                        raise Exception("Tunnel lost")
                except:
                    print("[ngrok] Tunnel lost, reconnecting...", flush=True)
                    break

        except KeyboardInterrupt:
            break
        except Exception as e:
            retry += 1
            wait = min(retry * 5, 60)
            print(f"[ngrok] Error: {e}, retry in {wait}s...", flush=True)
            time.sleep(wait)
            try:
                ngrok.kill()
            except:
                pass

    try:
        ngrok.kill()
    except:
        pass
    print("[ngrok] Stopped", flush=True)

if __name__ == '__main__':
    main()
