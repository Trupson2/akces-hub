#!/bin/bash
# ============================================================
# AKCES HUB — Setup skrypt dla Pi klienta
# Użycie: scp setup_client.sh pi@IP:~ && ssh pi@IP "bash setup_client.sh"
# ============================================================

set -e

echo "============================================"
echo "  AKCES HUB — Konfiguracja klienta"
echo "============================================"

# Kolory
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# ---- 1. Parametry ----
read -p "Nazwa klienta (np. sklep-jan): " CLIENT_NAME
read -p "Domena ngrok (np. ${CLIENT_NAME}.ngrok.dev): " NGROK_DOMAIN
read -p "Token ngrok: " NGROK_TOKEN
read -p "Token GitHub (read-only, do git pull): " GH_TOKEN

echo ""

# ---- 2. Aktualizacja systemu ----
echo -e "${YELLOW}[1/8] Aktualizacja systemu...${NC}"
sudo apt update && sudo apt upgrade -y

# ---- 3. Instalacja zaleznosci ----
echo -e "${YELLOW}[2/8] Instalacja Python i zaleznosci...${NC}"
sudo apt install -y python3 python3-pip python3-venv git curl sqlite3

# ---- 4. Klonowanie repo ----
echo -e "${YELLOW}[3/8] Klonowanie AKCES HUB...${NC}"
cd /home/pi
if [ -d "akces-hub" ]; then
    echo "Katalog akces-hub istnieje, pomijam klonowanie"
    cd akces-hub
    git remote set-url origin "https://${GH_TOKEN}@github.com/Trupson2/akces-hub.git"
    git pull
else
    git clone "https://${GH_TOKEN}@github.com/Trupson2/akces-hub.git"
    cd akces-hub
fi

# ---- 5. Virtualenv + pip ----
echo -e "${YELLOW}[4/8] Instalacja bibliotek Python...${NC}"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# ---- 6. Plik .env ----
echo -e "${YELLOW}[5/8] Konfiguracja .env...${NC}"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "CLIENT_NAME=${CLIENT_NAME}" >> .env
    echo "NGROK_DOMAIN=${NGROK_DOMAIN}" >> .env
    echo -e "${GREEN}Plik .env utworzony — uzupelnij klucze API${NC}"
else
    echo ".env juz istnieje, pomijam"
fi

# ---- 7. Ngrok ----
echo -e "${YELLOW}[6/8] Konfiguracja ngrok...${NC}"
if ! command -v ngrok &> /dev/null; then
    curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok-v3-stable-linux-arm64.tgz | sudo tar xz -C /usr/local/bin
fi
ngrok config add-authtoken "${NGROK_TOKEN}"

# Ngrok service
sudo tee /etc/systemd/system/ngrok.service > /dev/null <<NGROK_EOF
[Unit]
Description=Ngrok tunnel for AKCES HUB
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
ExecStart=/usr/local/bin/ngrok http 5000 --domain=${NGROK_DOMAIN} --log=stdout
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
NGROK_EOF

sudo systemctl daemon-reload
sudo systemctl enable ngrok
sudo systemctl start ngrok

# ---- 8. Serwis AKCES HUB ----
echo -e "${YELLOW}[7/8] Tworzenie serwisu systemd...${NC}"
sudo tee /etc/systemd/system/akceshub.service > /dev/null <<SERVICE_EOF
[Unit]
Description=AKCES HUB Enterprise
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/akces-hub
EnvironmentFile=/home/pi/akces-hub/.env
ExecStart=/home/pi/akces-hub/venv/bin/python app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

sudo systemctl daemon-reload
sudo systemctl enable akceshub
sudo systemctl start akceshub

# ---- 9. Klucz SSH supportu ----
echo -e "${YELLOW}[8/8] Konfiguracja SSH dla zdalnego supportu...${NC}"
mkdir -p ~/.ssh
chmod 700 ~/.ssh

# Twój klucz publiczny do supportu (WKLEJ SWOJ KLUCZ PONIZEJ)
SUPPORT_KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX support@akceshub"

if ! grep -q "support@akceshub" ~/.ssh/authorized_keys 2>/dev/null; then
    echo "${SUPPORT_KEY}" >> ~/.ssh/authorized_keys
    chmod 600 ~/.ssh/authorized_keys
    echo -e "${GREEN}Klucz SSH supportu dodany${NC}"
else
    echo "Klucz SSH supportu juz istnieje"
fi

# ---- Gotowe! ----
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  AKCES HUB zainstalowany!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo -e "  Panel:    http://localhost:5000"
echo -e "  Ngrok:    https://${NGROK_DOMAIN}"
echo -e "  SSH:      ssh pi@${NGROK_DOMAIN}"
echo -e "  Klient:   ${CLIENT_NAME}"
echo ""
echo -e "  Serwisy:"
echo -e "    sudo systemctl status akceshub"
echo -e "    sudo systemctl status ngrok"
echo ""
echo -e "${YELLOW}  Pamietaj uzupelnic .env (klucze API)!${NC}"
echo ""
