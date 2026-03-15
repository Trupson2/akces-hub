#!/bin/bash
# Akces Hub - Chromium Kiosk Mode Launcher
# Waveshare 7" Touch (1024x600)

# Wait for Flask to be ready
echo "Czekam na serwer Akces Hub..."
for i in $(seq 1 30); do
    if curl -s http://localhost:5000 > /dev/null 2>&1; then
        echo "Serwer gotowy!"
        break
    fi
    sleep 1
done

# Hide cursor after 3 seconds of inactivity
unclutter -idle 3 -root &

# Disable screen blanking / screensaver
xset s off
xset -dpms
xset s noblank

# Clear Chromium crash flags (prevents "restore session" popup)
CHROMIUM_DIR="$HOME/.config/chromium"
if [ -d "$CHROMIUM_DIR/Default" ]; then
    sed -i 's/"exited_cleanly":false/"exited_cleanly":true/' \
        "$CHROMIUM_DIR/Default/Preferences" 2>/dev/null
    sed -i 's/"exit_type":"Crashed"/"exit_type":"Normal"/' \
        "$CHROMIUM_DIR/Default/Preferences" 2>/dev/null
fi

# Launch Chromium in kiosk mode
chromium \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-translate \
    --no-first-run \
    --fast \
    --fast-start \
    --disable-features=TranslateUI \
    --check-for-update-interval=31536000 \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --disable-session-crashed-bubble \
    --disable-component-update \
    --window-size=1024,600 \
    --window-position=0,0 \
    --touch-events=enabled \
    "http://localhost:5000/?kiosk=1"
