#!/usr/bin/env bash
# ============================================================
# AKCES HUB — instalator instancji + Cloudflare Tunnel
#
# Stawia OSOBNĄ, izolowaną instancję per klient (user + katalog +
# usługa + WŁASNY tunel) i wystawia ją pod subdomenę w Twojej domenie
# Cloudflare. Działa zarówno na czystym VPS, jak i NA ZAJĘTEJ maszynie
# obok istniejących usług (osobny port + osobno nazwany tunel — NIE
# rusza istniejącego cloudflared/ngrok).
#
# Użycie (przykład — dodatkowy klient na zajętym Pi, port 5001):
#   sudo bash setup_vps_cloudflare.sh \
#     --client maciek \
#     --domain maciek.akceshub.com \
#     --port 5001 \
#     --tunnel-token "eyJhIjoi..." \
#     --gh-token "github_pat_..."
#
# Opcje:
#   --client <nazwa>     wymagane (a-z0-9-)
#   --domain <host>      wymagane (np. maciek.akceshub.com)
#   --tunnel-token <t>   wymagane (token tunelu z panelu Cloudflare)
#   --port <n>           port lokalny aplikacji (domyślnie 5000)
#   --gh-token <t>       token GitHub (repo prywatne)
#   --gemini-key <k>     klucz Gemini (opcjonalnie)
#   --branch <b>         branch repo aplikacji (domyślnie main)
#   --repo <o/r>         repo (domyślnie Trupson2/akces-hub)
#   --firewall           włącz UFW (domyślnie NIE — na zajętej maszynie
#                        mogłoby odciąć istniejące usługi/LAN)
#
# WAŻNE: w panelu Cloudflare, dla tunelu tego klienta, ustaw Public
# Hostname -> Type HTTP -> URL localhost:<port> (TEN SAM port co --port).
# ============================================================
set -euo pipefail

CLIENT=""; DOMAIN=""; TUNNEL_TOKEN=""; GH_TOKEN=""; GEMINI_KEY=""
PORT="5000"; BRANCH="main"; REPO="Trupson2/akces-hub"; DO_FIREWALL="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --client)        CLIENT="$2"; shift 2;;
    --domain)        DOMAIN="$2"; shift 2;;
    --tunnel-token)  TUNNEL_TOKEN="$2"; shift 2;;
    --port)          PORT="$2"; shift 2;;
    --gh-token)      GH_TOKEN="$2"; shift 2;;
    --gemini-key)    GEMINI_KEY="$2"; shift 2;;
    --branch)        BRANCH="$2"; shift 2;;
    --repo)          REPO="$2"; shift 2;;
    --firewall)      DO_FIREWALL="1"; shift;;
    -h|--help)       grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "Nieznany argument: $1"; exit 1;;
  esac
done

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
err()  { echo -e "${RED}[BŁĄD]${NC} $*" >&2; exit 1; }
info() { echo -e "${YELLOW}$*${NC}"; }
ok()   { echo -e "${GREEN}$*${NC}"; }

[[ $EUID -eq 0 ]] || err "Uruchom przez sudo/root."
[[ -n "$CLIENT" ]] || err "Brak --client (np. --client maciek)."
[[ -n "$DOMAIN" ]] || err "Brak --domain (np. --domain maciek.akceshub.com)."
[[ -n "$TUNNEL_TOKEN" ]] || err "Brak --tunnel-token (token z panelu Cloudflare)."
[[ "$CLIENT" =~ ^[a-z0-9-]+$ ]] || err "--client tylko małe litery/cyfry/myślnik."
[[ "$PORT" =~ ^[0-9]+$ ]] || err "--port musi być liczbą."

APP_USER="akces-${CLIENT}"
APP_DIR="/opt/akces-${CLIENT}"
SERVICE="akces-${CLIENT}"
TUNNEL_SVC="akces-${CLIENT}-tunnel"
ENV_FILE="/etc/akces/${CLIENT}.env"
TUNNEL_ENV="/etc/akces/${CLIENT}-tunnel.env"

echo "============================================"
echo "  AKCES HUB — instalacja izolowanej instancji"
echo "  Klient:      ${CLIENT}"
echo "  Domena:      https://${DOMAIN}"
echo "  Port lokalny: ${PORT}"
echo "  Katalog:     ${APP_DIR}"
echo "  Usługa app:  ${SERVICE}.service"
echo "  Usługa tunel:${TUNNEL_SVC}.service"
echo "============================================"

# ---- 0. Pre-flight: nie rozwalaj zajętej maszyny ----
info "[0/7] Kontrola kolizji..."
if ss -ltn "( sport = :${PORT} )" 2>/dev/null | grep -q LISTEN; then
  err "Port ${PORT} jest ZAJĘTY przez inną usługę. Wybierz wolny, np. --port 5002."
fi
if [[ -f "/etc/systemd/system/${TUNNEL_SVC}.service" ]]; then
  info "  ${TUNNEL_SVC}.service już istnieje — zostanie nadpisany (ten sam klient)."
fi
ok "  Port ${PORT} wolny. Istniejące usługi (cloudflared/ngrok/akces-hub) NIE są ruszane."

# ---- 1. Pakiety systemowe ----
info "[1/7] Pakiety systemowe..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git curl sqlite3 ca-certificates

# ---- 2. Użytkownik + repo ----
info "[2/7] Użytkownik ${APP_USER} + repo..."
id "$APP_USER" &>/dev/null || useradd -m -s /bin/bash "$APP_USER"
mkdir -p "$APP_DIR"
if [[ -n "$GH_TOKEN" ]]; then
  CLONE_URL="https://${GH_TOKEN}@github.com/${REPO}.git"
else
  CLONE_URL="https://github.com/${REPO}.git"
fi
if [[ -d "$APP_DIR/.git" ]]; then
  info "  Repo istnieje — aktualizuję (reset --hard)..."
  git -C "$APP_DIR" remote set-url origin "$CLONE_URL"
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" reset --hard "origin/${BRANCH}"
else
  git clone --branch "$BRANCH" "$CLONE_URL" "$APP_DIR"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# ---- 3. Virtualenv + zależności ----
info "[3/7] Python venv + requirements..."
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# ---- 4. Plik środowiskowy (sekrety + port) ----
info "[4/7] Konfiguracja środowiska..."
mkdir -p /etc/akces
if [[ ! -f "$ENV_FILE" ]]; then
  ENC_KEY="$("$APP_DIR/venv/bin/python" - <<'PY'
import base64, os
print(base64.urlsafe_b64encode(os.urandom(32)).decode())
PY
)"
  {
    echo "FLASK_ENV=production"
    echo "CLIENT_NAME=${CLIENT}"
    echo "AKCES_PORT=${PORT}"
    echo "AKCES_ENCRYPTION_KEY=${ENC_KEY}"
    [[ -n "$GEMINI_KEY" ]] && echo "GEMINI_API_KEY=${GEMINI_KEY}"
    echo "SESSION_COOKIE_SECURE=True"
  } > "$ENV_FILE"
  chmod 640 "$ENV_FILE"; chown root:"$APP_USER" "$ENV_FILE"
  ok "  Utworzono ${ENV_FILE}."
else
  # zaktualizuj sam port (reszta sekretów bez zmian)
  if grep -q '^AKCES_PORT=' "$ENV_FILE"; then
    sed -i "s/^AKCES_PORT=.*/AKCES_PORT=${PORT}/" "$ENV_FILE"
  else
    echo "AKCES_PORT=${PORT}" >> "$ENV_FILE"
  fi
  info "  ${ENV_FILE} istnieje — zaktualizowano AKCES_PORT=${PORT}."
fi

# ---- 5. Usługa systemd aplikacji ----
# FLASK_ENV=production -> ProxyFix (realne IP klientów za tunelem).
info "[5/7] Usługa ${SERVICE}.service (port ${PORT})..."
cat > "/etc/systemd/system/${SERVICE}.service" <<UNIT
[Unit]
Description=Akces Hub (${CLIENT}) - waitress :${PORT}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
ExecStart=${APP_DIR}/venv/bin/python app.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable "${SERVICE}.service"
systemctl restart "${SERVICE}.service"

# ---- 6. Tunel (WŁASNA usługa — nie rusza istniejącego cloudflared) ----
info "[6/7] Dedykowany tunel ${TUNNEL_SVC}.service..."
if ! command -v cloudflared &>/dev/null; then
  ARCH="$(dpkg --print-architecture)"   # arm64 / amd64
  curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb" -o /tmp/cloudflared.deb
  apt-get install -y /tmp/cloudflared.deb
  rm -f /tmp/cloudflared.deb
else
  info "  cloudflared już zainstalowany — używam istniejącego binarki."
fi
CF_BIN="$(command -v cloudflared)"

# token w osobnym pliku 600 (nie w unicie widocznym przez systemctl cat)
echo "TUNNEL_TOKEN=${TUNNEL_TOKEN}" > "$TUNNEL_ENV"
chmod 600 "$TUNNEL_ENV"; chown root:root "$TUNNEL_ENV"

cat > "/etc/systemd/system/${TUNNEL_SVC}.service" <<UNIT
[Unit]
Description=Cloudflare Tunnel — Akces Hub (${CLIENT}) -> localhost:${PORT}
After=network-online.target ${SERVICE}.service
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=${TUNNEL_ENV}
ExecStart=${CF_BIN} tunnel --no-autoupdate run --token \${TUNNEL_TOKEN}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable "${TUNNEL_SVC}.service"
systemctl restart "${TUNNEL_SVC}.service"

# ---- 7. Firewall (opcjonalnie) ----
if [[ "$DO_FIREWALL" == "1" ]]; then
  info "[7/7] UFW (--firewall) — port ${PORT} tylko lokalnie..."
  ufw allow 22/tcp >/dev/null 2>&1 || true
  yes | ufw enable >/dev/null 2>&1 || true
else
  info "[7/7] UFW pominięty (domyślnie). Tunel jest wychodzący — port ${PORT} nie musi być publiczny."
fi

# ---- Health check ----
sleep 4
if curl -fsS -o /dev/null "http://127.0.0.1:${PORT}" 2>/dev/null; then
  ok "  Aplikacja odpowiada na 127.0.0.1:${PORT}."
else
  info "  Aplikacja jeszcze wstaje — sprawdź: journalctl -u ${SERVICE} -f"
fi

echo ""
ok "============================================"
ok "  GOTOWE — instancja ${CLIENT} (port ${PORT})"
ok "============================================"
echo ""
echo "  URL:          https://${DOMAIN}"
echo "  Pierwszy raz: https://${DOMAIN}/setup -> konto admina, potem /onboarding"
echo ""
echo "  Usługi (TYLKO te dwie nowe — reszta nietknięta):"
echo "    systemctl status ${SERVICE}"
echo "    systemctl status ${TUNNEL_SVC}"
echo "    journalctl -u ${SERVICE} -f"
echo ""
info "  KONIECZNIE w panelu Cloudflare (tunel tego klienta) -> Public Hostname:"
info "    Subdomain=${DOMAIN%%.*}  Domain=${DOMAIN#*.}  ->  HTTP  URL: localhost:${PORT}"
info "  oraz w UI włącz 2FA admina (publiczny URL)."
echo ""
