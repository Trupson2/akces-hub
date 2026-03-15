#!/bin/bash
# ===========================================
# AKCES HUB - Update script
# Wspiera: git pull (jesli git repo) LUB reczny upload przez /ustawienia
# ===========================================
set -e

APP_DIR="/home/pi/akces-hub"
BACKUP_DIR="$APP_DIR/backups"

echo "============================================"
echo "  AKCES HUB - Aktualizacja"
echo "============================================"

# 1. Backup bazy przed update
echo "[1/3] Backup bazy..."
mkdir -p "$BACKUP_DIR"
TS=$(date +%Y%m%d_%H%M%S)
if [ -f "$APP_DIR/akces_hub.db" ]; then
    sqlite3 "$APP_DIR/akces_hub.db" ".backup '$BACKUP_DIR/pre_update_${TS}.db'"
    echo "  -> $BACKUP_DIR/pre_update_${TS}.db"
else
    echo "  -> Brak bazy (nowa instalacja?)"
fi

# 2. Git pull (jesli to git repo)
echo "[2/3] Pobieranie zmian..."
cd "$APP_DIR"
if [ -d ".git" ]; then
    git stash 2>/dev/null || true
    git pull --ff-only origin main
    git stash pop 2>/dev/null || true
    echo "  -> Git pull OK"
else
    echo "  -> Brak repo git — uzyj uploadu ZIP w /ustawienia"
    echo "  -> Lub: scp -r /sciezka/do/projektu/* pi@IP:/home/pi/akces-hub/"
fi

# 3. Pip install (jesli jest requirements.txt)
echo "[3/3] Aktualizacja zaleznosci Python..."
if [ -f "$APP_DIR/requirements.txt" ]; then
    "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet 2>/dev/null || \
    "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" --break-system-packages --quiet
    echo "  -> OK"
else
    echo "  -> Brak requirements.txt, pomijam"
fi

echo ""
echo "============================================"
echo "  GOTOWE! Restart: sudo systemctl restart akceshub.service"
echo "============================================"
