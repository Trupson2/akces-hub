#!/bin/bash
# ===========================================
# AKCES HUB - Ngrok Watchdog
# Jeden stabilny mechanizm: start + monitoring
# Automatyczny restart po dowolnym failure
# ===========================================

NGROK_BIN="/usr/local/bin/ngrok"
NGROK_CONFIG="/home/pi/.config/ngrok/ngrok.yml"
APP_CONFIG="/home/pi/akces-hub/deploy/ngrok.yml"
FLASK_PORT=5000
CHECK_INTERVAL=60
MAX_FAILS=5
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

# Czekaj na siec (max 2 minuty)
log "Czekam na siec..."
for i in $(seq 1 60); do
    if ping -c 1 -W 2 8.8.8.8 > /dev/null 2>&1; then
        log "Siec dostepna po ${i}x2s"
        break
    fi
    if [ "$i" -eq 60 ]; then
        log "WARN: Timeout sieci, probuje dalej..."
    fi
    sleep 2
done

# Czekaj na Flask (max 2 minuty)
log "Czekam na Flask na porcie $FLASK_PORT..."
for i in $(seq 1 60); do
    if curl -s -o /dev/null -w '%{http_code}' "http://localhost:$FLASK_PORT/" 2>/dev/null | grep -qE '200|302'; then
        log "Flask gotowy!"
        break
    fi
    if [ "$i" -eq 60 ]; then
        log "WARN: Flask timeout, probuje dalej..."
    fi
    sleep 2
done

# Glowna petla - nieskonczona
while true; do
    # Ubij stare procesy ngrok
    killall ngrok 2>/dev/null
    sleep 2

    # Startuj ngrok
    log "Uruchamiam ngrok..."
    if [ -f "$APP_CONFIG" ]; then
        $NGROK_BIN start --all --config "$NGROK_CONFIG" --config "$APP_CONFIG" --log=stdout --log-level=warn &
    else
        # Fallback - pojedynczy tunel
        $NGROK_BIN http $FLASK_PORT --log=stdout --log-level=warn &
    fi
    NGROK_PID=$!
    sleep 10
    FAIL_COUNT=0

    # Sprawdz czy w ogole zyje
    if ! kill -0 $NGROK_PID 2>/dev/null; then
        log "ERROR: Ngrok umarl na starcie, restart za 30s..."
        sleep 30
        continue
    fi

    log "Ngrok uruchomiony (PID: $NGROK_PID), monitoring..."

    # Monitoring loop
    while true; do
        sleep $CHECK_INTERVAL

        # Sprawdz czy proces zyje
        if ! kill -0 $NGROK_PID 2>/dev/null; then
            log "Ngrok process umarl, restart..."
            break
        fi

        # Health check przez API
        HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://localhost:4040/api/tunnels" 2>/dev/null)

        if [ "$HTTP_CODE" = "200" ]; then
            TUNNELS=$(curl -s --max-time 5 "http://localhost:4040/api/tunnels" 2>/dev/null | grep -c "public_url")
            if [ "$TUNNELS" -gt 0 ]; then
                FAIL_COUNT=0
            else
                FAIL_COUNT=$((FAIL_COUNT + 1))
                log "WARN: Brak aktywnych tuneli ($FAIL_COUNT/$MAX_FAILS)"
            fi
        else
            FAIL_COUNT=$((FAIL_COUNT + 1))
            log "WARN: Ngrok API nie odpowiada ($FAIL_COUNT/$MAX_FAILS)"
        fi

        # Za duzo failow - restart
        if [ "$FAIL_COUNT" -ge "$MAX_FAILS" ]; then
            log "ERROR: $MAX_FAILS consecutive fails - restart ngrok"
            killall ngrok 2>/dev/null
            sleep 5
            break
        fi
    done

    # Czekaj przed ponowna proba
    log "Restart za 10s..."
    sleep 10
done
