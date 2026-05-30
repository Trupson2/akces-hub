#!/usr/bin/env bash
# ============================================================
# AKCES HUB — instalator instancji na VPS + Cloudflare Tunnel
#
# Stawia OSOBNĄ instancję per klient (user + katalog + usługa +
# tunel) i wystawia ją pod subdomenę w Twojej domenie Cloudflare.
# Aplikacja chodzi na VPS — komputer klienta nie jest potrzebny.
#
# Użycie:
#   sudo bash setup_vps_cloudflare.sh \
#     --client maciek \
#     --domain maciek.akceshub.com \
#     --tunnel-token "eyJhIjoi..." \
#     [--gh-token <github_token>] [--gemini-key <klucz>] \
#     [--branch main] [--repo Trupson2/akces-hub]
#
# Token tunelu bierzesz z: Cloudflare Zero Trust -> Networks -> Tunnels
#   -> Create tunnel (Cloudflared) -> skopiuj token z ekranu instalacji.
# Public Hostname w tym samym panelu: maciek / akceshub.com -> HTTP localhost:5000
# ============================================================
set -euo pipefail

# ---- Domyślne ----
CLIENT=""
DOMAIN=""
TUNNEL_TOKEN=""
GH_TOKEN=""
GEMINI_KEY=""
BRANCH="main"
REPO="Trupson2/akces-hub"

# ---- Parsowanie argumentów ----
while [[ $# -gt 0 ]]; do
  case "$1" in
    --client)        CLIENT="$2"; shift 2;;
    --domain)        DOMAIN="$2"; shift 2;;
    --tunnel-token)  TUNNEL_TOKEN="$2"; shift 2;;
    --gh-token)      GH_TOKEN="$2"; shift 2;;
    --gemini-key)    GEMINI_KEY="$2"; shift 2;;
    --branch)        BRANCH="$2"; shift 2;;
    --repo)          REPO="$2"; shift 2;;
    -h|--help)       grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "Nieznany argument: $1"; exit 1;;
  esac
done

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
err() { echo -e "${RED}[BŁĄD]${NC} $*" >&2; exit 1; }
info() { echo -e "${YELLOW}$*${NC}"; }
ok() { echo -e "${GREEN}$*${NC}"; }

[[ $EUID -eq 0 ]] || err "Uruchom przez sudo/root."
[[ -n "$CLIENT" ]] || err "Brak --client (np. --client maciek)."
[[ -n "$DOMAIN" ]] || err "Brak --domain (np. --domain maciek.akceshub.com)."
[[ -n "$TUNNEL_TOKEN" ]] || err "Brak --tunnel-token (token z panelu Cloudflare)."
[[ "$CLIENT" =~ ^[a-z0-9-]+$ ]] || err "--client tylko małe litery/cyfry/myślnik."

APP_USER="akces-${CLIENT}"
APP_DIR="/opt/akces-${CLIENT}"
SERVICE="akces-${CLIENT}"
ENV_FILE="/etc/akces/${CLIENT}.env"

echo "============================================"
echo "  AKCES HUB — VPS + Cloudflare Tunnel"
echo "  Klient:    ${CLIENT}"
echo "  Domena:    https://${DOMAIN}"
echo "  Katalog:   ${APP_DIR}"
echo "  Usługa:    ${SERVICE}.service"
echo "============================================"

# ---- 1. Pakiety systemowe ----
info "[1/7] Pakiety systemowe..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git curl sqlite3 ufw ca-certificates

# ---- 2. Użytkownik + repo ----
info "[2/7] Użytkownik ${APP_USER} + klonowanie repo..."
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

# ---- 4. Plik środowiskowy (sekrety) ----
info "[4/7] Konfiguracja środowiska..."
mkdir -p /etc/akces
if [[ ! -f "$ENV_FILE" ]]; then
  # Klucz szyfrowania (Fernet) — stały per instancja, żeby sekrety w DB przetrwały restart
  ENC_KEY="$("$APP_DIR/venv/bin/python" - <<'PY'
import base64, os
print(base64.urlsafe_b64encode(os.urandom(32)).decode())
PY
)"
  {
    echo "FLASK_ENV=production"
    echo "CLIENT_NAME=${CLIENT}"
    echo "AKCES_ENCRYPTION_KEY=${ENC_KEY}"
    [[ -n "$GEMINI_KEY" ]] && echo "GEMINI_API_KEY=${GEMINI_KEY}"
    echo "SESSION_COOKIE_SECURE=True"
  } > "$ENV_FILE"
  chmod 640 "$ENV_FILE"
  chown root:"$APP_USER" "$ENV_FILE"
  ok "  Utworzono ${ENV_FILE} (klucz szyfrowania wygenerowany)."
else
  info "  ${ENV_FILE} istnieje — pomijam."
fi

# ---- 5. Usługa systemd aplikacji ----
# Nazwa pliku zawiera 'akces-' ale to FLASK_ENV=production włącza ProxyFix
# i bind 0.0.0.0 (patrz app.py _is_proxied_deployment). Port 5000 zamykamy
# firewallem — cloudflared sięga go po localhost.
info "[5/7] Usługa systemd ${SERVICE}.service..."
cat > "/etc/systemd/system/${SERVICE}.service" <<UNIT
[Unit]
Description=Akces Hub (${CLIENT}) - waitress
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

# Hardening
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "${SERVICE}.service"
systemctl restart "${SERVICE}.service"

# ---- 6. cloudflared (tunel) ----
info "[6/7] Instalacja cloudflared + rejestracja tunelu..."
if ! command -v cloudflared &>/dev/null; then
  ARCH="$(dpkg --print-architecture)"   # amd64 / arm64
  curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb" -o /tmp/cloudflared.deb
  apt-get install -y /tmp/cloudflared.deb
  rm -f /tmp/cloudflared.deb
fi
# Instaluje cloudflared jako usługę z tokenem (mapowanie hostname robisz w panelu CF)
cloudflared service install "$TUNNEL_TOKEN" || {
  # jeśli usługa już istnieje, podmień token
  info "  cloudflared.service istnieje — odświeżam..."
  systemctl stop cloudflared 2>/dev/null || true
  cloudflared service uninstall 2>/dev/null || true
  cloudflared service install "$TUNNEL_TOKEN"
}
systemctl enable cloudflared 2>/dev/null || true
systemctl restart cloudflared

# ---- 7. Firewall ----
info "[7/7] Firewall UFW (5000 niewidoczny z internetu)..."
ufw allow 22/tcp >/dev/null 2>&1 || true
# celowo NIE otwieramy 5000 — ruch idzie przez tunel do localhost
yes | ufw enable >/dev/null 2>&1 || true

# ---- Health check ----
sleep 4
if curl -fsS -o /dev/null "http://127.0.0.1:5000" 2>/dev/null; then
  ok "  Aplikacja odpowiada na 127.0.0.1:5000."
else
  info "  Aplikacja jeszcze wstaje — sprawdź: journalctl -u ${SERVICE} -f"
fi

echo ""
ok "============================================"
ok "  GOTOWE — instancja ${CLIENT} postawiona"
ok "============================================"
echo ""
echo "  URL:        https://${DOMAIN}   (po skonfigurowaniu Public Hostname w CF)"
echo "  Pierwszy raz: https://${DOMAIN}/setup  -> konto admina, potem /onboarding"
echo ""
echo "  Usługi:"
echo "    systemctl status ${SERVICE}"
echo "    systemctl status cloudflared"
echo "    journalctl -u ${SERVICE} -f"
echo ""
info "  PRZYPOMNIENIE: w Cloudflare panelu dodaj Public Hostname:"
info "    Subdomain=${DOMAIN%%.*}  Domain=${DOMAIN#*.}  ->  HTTP  localhost:5000"
info "  oraz w UI włącz 2FA admina (publiczny URL)."
echo ""
