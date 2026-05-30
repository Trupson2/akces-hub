#!/usr/bin/env bash
# Naprawa tunelu na ZAJĘTEJ maszynie (wspólny /etc/cloudflared/config.yml
# przejmował token). Przełącza tunel klienta w tryb "named" z WŁASNYM
# configiem (ingress + credentials z dekodowanego tokenu) — nie czyta już
# cudzego configu, nie rusza istniejących tuneli.
#
# Użycie: sudo bash fix_tunnel.sh <client> <domain> <port>
#   sudo bash fix_tunnel.sh maciek maciek.akceshub.com 5003
set -euo pipefail

CLIENT="${1:-maciek}"
DOMAIN="${2:-maciek.akceshub.com}"
PORT="${3:-5003}"

ENVF="/etc/akces/${CLIENT}-tunnel.env"
CREDS="/etc/akces/${CLIENT}-creds.json"
CFG="/etc/akces/${CLIENT}-cf.yml"
UNIT="/etc/systemd/system/akces-${CLIENT}-tunnel.service"

[[ $EUID -eq 0 ]] || { echo "Uruchom przez sudo"; exit 1; }
[[ -f "$ENVF" ]] || { echo "Brak $ENVF — najpierw instalator"; exit 1; }

TOKEN="$(sed -n 's/^TUNNEL_TOKEN=//p' "$ENVF")"
[[ -n "$TOKEN" ]] || { echo "Brak tokenu w $ENVF"; exit 1; }

# Dekoduj token -> credentials.json (token = base64(JSON{a,t,s}))
TID="$(python3 - "$TOKEN" "$CREDS" <<'PY'
import base64, json, sys
tok = sys.argv[1].strip()
tok += '=' * (-len(tok) % 4)
try:
    d = json.loads(base64.b64decode(tok))
except Exception:
    d = json.loads(base64.urlsafe_b64decode(tok))
creds = {"AccountTag": d["a"], "TunnelID": d["t"], "TunnelSecret": d["s"]}
with open(sys.argv[2], "w") as f:
    json.dump(creds, f)
print(d["t"])
PY
)"
chmod 600 "$CREDS"; chown root:root "$CREDS"
echo ">>> Tunnel ID z tokenu: ${TID}"

# Własny config (named mode) — explicit --config => ignoruje /etc/cloudflared/config.yml
cat > "$CFG" <<YAML
tunnel: ${TID}
credentials-file: ${CREDS}
no-autoupdate: true
ingress:
  - hostname: ${DOMAIN}
    service: http://localhost:${PORT}
  - service: http_status:404
YAML
chmod 600 "$CFG"

CF_BIN="$(command -v cloudflared)"
cat > "$UNIT" <<UNITEOF
[Unit]
Description=Cloudflare Tunnel — Akces Hub (${CLIENT}) -> localhost:${PORT}
After=network-online.target akces-${CLIENT}.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=${CF_BIN} tunnel --config ${CFG} run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNITEOF

systemctl daemon-reload
systemctl restart "akces-${CLIENT}-tunnel"
sleep 6

echo "=== status ==="
systemctl is-active "akces-${CLIENT}-tunnel" || true
echo "=== logi ==="
journalctl -u "akces-${CLIENT}-tunnel" --no-pager -n 30 \
  | grep -iE "starting tunnel|registered tunnel connection|config|error|hostname" \
  | grep -ivE "context canceled|accept stream|icmp router|datagram handler" \
  | tail -8
echo ""
echo ">>> Jesli wyzej widac 'Registered tunnel connection' i Tunnel ID = ${TID}"
echo ">>> oraz ${TID} = ID tunelu akces-${CLIENT} w panelu -> dziala. Odswiez ${DOMAIN}."
