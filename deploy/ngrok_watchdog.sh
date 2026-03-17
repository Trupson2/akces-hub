#!/bin/bash
# ===========================================
# AKCES HUB - Ngrok Watchdog
# Odpala ngrok + monitoruje tunnel co 30s
# Jesli tunnel padnie - restartuje ngrok
# ===========================================

NGROK_BIN="/usr/local/bin/ngrok"
NGROK_URL="unsatiating-dirgelike-audrina.ngrok-free.dev"
FLASK_PORT=5000
CHECK_INTERVAL=30
MAX_FAILS=3
FAIL_COUNT=0

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

cleanup() {
    log "Stopping ngrok..."
    killall ngrok 2>/dev/null
    exit 0
}

trap cleanup SIGTERM SIGINT

# Czekaj na Flask
log "Waiting for Flask on port $FLASK_PORT..."
for i in $(seq 1 30); do
    if curl -s -o /dev/null -w '%{http_code}' "http://localhost:$FLASK_PORT/" 2>/dev/null | grep -qE '200|302'; then
        log "Flask ready!"
        break
    fi
    sleep 2
done

while true; do
    # Sprawdz czy ngrok process zyje
    if ! pgrep -x ngrok > /dev/null 2>&1; then
        log "Starting ngrok tunnel..."
        $NGROK_BIN http --url=$NGROK_URL $FLASK_PORT --log=stdout --log-level=warn &
        NGROK_PID=$!
        sleep 5
        FAIL_COUNT=0
    fi

    # Health check - sprawdz czy tunnel odpowiada
    sleep $CHECK_INTERVAL

    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://localhost:4040/api/tunnels" 2>/dev/null)

    if [ "$HTTP_CODE" = "200" ]; then
        # Tunnel API odpowiada - sprawdz czy sa aktywne tunnele
        TUNNELS=$(curl -s --max-time 5 "http://localhost:4040/api/tunnels" 2>/dev/null | grep -c "public_url")
        if [ "$TUNNELS" -gt 0 ]; then
            FAIL_COUNT=0
        else
            FAIL_COUNT=$((FAIL_COUNT + 1))
            log "WARNING: No active tunnels (fail $FAIL_COUNT/$MAX_FAILS)"
        fi
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        log "WARNING: Ngrok API not responding (fail $FAIL_COUNT/$MAX_FAILS)"
    fi

    # Za duzo failow - restart ngrok
    if [ "$FAIL_COUNT" -ge "$MAX_FAILS" ]; then
        log "ERROR: $MAX_FAILS consecutive fails - restarting ngrok"
        killall ngrok 2>/dev/null
        sleep 3
        FAIL_COUNT=0
        # Petla wroci na gere i odpali ngrok ponownie
    fi
done
