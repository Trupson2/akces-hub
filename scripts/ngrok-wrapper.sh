#!/bin/bash
# Ngrok wrapper - stabilny autostart z retry i cleanup

NGROK_URL="unsatiating-dirgelike-audrina.ngrok-free.dev"
NGROK_BIN="/usr/local/bin/ngrok"
MAX_RETRIES=5
RETRY_DELAY=30

# Czekaj na siec
echo "[ngrok-wrapper] Czekam na siec..."
for i in $(seq 1 30); do
    if ping -c 1 -W 2 google.com > /dev/null 2>&1; then
        echo "[ngrok-wrapper] Siec dziala po ${i}s"
        break
    fi
    sleep 2
done

# Czekaj na apke
echo "[ngrok-wrapper] Czekam na akces-hub..."
for i in $(seq 1 30); do
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/ 2>/dev/null | grep -q "200"; then
        echo "[ngrok-wrapper] Apka dziala po ${i}s"
        break
    fi
    sleep 2
done

# Ubij stare procesy ngrok
pkill -f ngrok 2>/dev/null
sleep 3

# Startuj ngrok z retry
for attempt in $(seq 1 $MAX_RETRIES); do
    echo "[ngrok-wrapper] Proba $attempt/$MAX_RETRIES..."

    # Uruchom ngrok
    $NGROK_BIN http 5000 --url=$NGROK_URL &
    NGROK_PID=$!

    # Czekaj 10s i sprawdz czy zyje
    sleep 10

    if kill -0 $NGROK_PID 2>/dev/null; then
        # Sprawdz czy tunnel dziala
        TUNNEL_URL=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['tunnels'][0]['public_url'] if d['tunnels'] else '')" 2>/dev/null)

        if [ -n "$TUNNEL_URL" ]; then
            echo "[ngrok-wrapper] Tunnel aktywny: $TUNNEL_URL"
            # Czekaj na zakonczenie procesu
            wait $NGROK_PID
            EXIT_CODE=$?
            echo "[ngrok-wrapper] Ngrok zakonczony z kodem $EXIT_CODE"
        else
            echo "[ngrok-wrapper] Tunnel nie dziala, restart..."
            kill $NGROK_PID 2>/dev/null
            sleep 5
        fi
    else
        echo "[ngrok-wrapper] Ngrok umarl, czekam ${RETRY_DELAY}s..."
    fi

    # Cleanup przed retry
    pkill -f ngrok 2>/dev/null
    sleep $RETRY_DELAY
done

echo "[ngrok-wrapper] Wyczerpano proby, poddaje sie."
exit 1
