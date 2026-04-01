#!/bin/bash
# ===========================================
# AKCES HUB - Instalacja na Raspberry Pi 5
# Waveshare 7" Touch (1024x600) + Ngrok
# ===========================================
set -e

APP_DIR="/opt/akces-hub"
APP_USER="akces"

echo "============================================"
echo "  AKCES HUB - Instalator Raspberry Pi 5"
echo "============================================"
echo ""

# 1. System packages
echo "[1/8] Instalacja pakietow systemowych..."
sudo apt update
sudo apt install -y \
    python3 python3-pip python3-venv \
    chromium \
    bluetooth bluez libbluetooth-dev \
    libglib2.0-dev \
    unclutter \
    sqlite3 \
    curl \
    fonts-noto-color-emoji

# 2. Create app user (if not exists)
echo "[2/8] Tworzenie uzytkownika $APP_USER..."
if ! id "$APP_USER" &>/dev/null; then
    sudo useradd -m -s /bin/bash "$APP_USER"
fi
sudo usermod -aG bluetooth "$APP_USER"
sudo usermod -aG video "$APP_USER"

# 3. Copy app files
echo "[3/8] Kopiowanie plikow do $APP_DIR..."
sudo mkdir -p "$APP_DIR"
sudo rsync -av --exclude='venv' --exclude='__pycache__' --exclude='.git' . "$APP_DIR/"
sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# 4. Python virtual environment + dependencies
echo "[4/8] Tworzenie srodowiska Python + instalacja zaleznosci..."
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"
# Ngrok
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install pyngrok

# 5. Bluetooth permissions (for Niimbot BLE printer)
echo "[5/8] Konfiguracja Bluetooth BLE..."
sudo cp "$APP_DIR/deploy/99-bluetooth.rules" /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
PYTHON_BIN="$APP_DIR/venv/bin/python3"
if [ -f "$PYTHON_BIN" ]; then
    sudo setcap 'cap_net_raw,cap_net_admin+eip' "$PYTHON_BIN"
fi

# 6. systemd services (Flask + Ngrok)
echo "[6/8] Instalacja serwisow systemd..."
sudo cp "$APP_DIR/deploy/akces-hub.service" /etc/systemd/system/
sudo cp "$APP_DIR/deploy/akces-ngrok.service" /etc/systemd/system/
# Usun stary ngrok.service jesli istnieje (unikamy konfliktu)
sudo systemctl stop ngrok.service 2>/dev/null
sudo systemctl disable ngrok.service 2>/dev/null
sudo rm -f /etc/systemd/system/ngrok.service
sudo systemctl daemon-reload
sudo systemctl enable akces-hub.service
sudo systemctl enable akces-ngrok.service
sudo systemctl start akces-hub.service
# Wait for Flask to start before ngrok
sleep 3
sudo systemctl start akces-ngrok.service

# 7. Kiosk auto-start
echo "[7/8] Konfiguracja trybu kiosk..."
AUTOSTART_DIR="/home/$APP_USER/.config/autostart"
sudo -u "$APP_USER" mkdir -p "$AUTOSTART_DIR"
sudo cp "$APP_DIR/deploy/kiosk.desktop" "$AUTOSTART_DIR/"
sudo chown "$APP_USER:$APP_USER" "$AUTOSTART_DIR/kiosk.desktop"
sudo cp "$APP_DIR/deploy/kiosk.sh" "/home/$APP_USER/kiosk.sh"
sudo chmod +x "/home/$APP_USER/kiosk.sh"
sudo chown "$APP_USER:$APP_USER" "/home/$APP_USER/kiosk.sh"

# 8. Auto-login to desktop
echo "[8/8] Auto-login..."
sudo raspi-config nonint do_boot_behaviour B4

echo ""
echo "============================================"
echo "  INSTALACJA ZAKONCZONA!"
echo ""
echo "  WAZNE: Ustaw token Ngrok!"
echo "  Opcja A: W pliku start_remote_access.py"
echo "    NGROK_AUTH_TOKEN = \"twoj_token\""
echo "  Opcja B: W /ustawienia w przegladarce"
echo ""
echo "  Restartuj Pi: sudo reboot"
echo ""
echo "  Po restarcie automatycznie:"
echo "  - Flask na porcie 5000"
echo "  - Ngrok tunnel (zdalny dostep)"
echo "  - Chromium kiosk (dashboard)"
echo ""
echo "  Komendy:"
echo "  sudo systemctl status akces-hub"
echo "  sudo systemctl status akces-ngrok"
echo "  sudo journalctl -u akces-ngrok -f"
echo "============================================"
