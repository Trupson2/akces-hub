#!/bin/bash
# Pi Thermal Protection — uruchom raz na Pi
# Ustawia fan na 50°C + throttling CPU na 1.5GHz (zamiast 2.4GHz)
# Dzięki temu Pi nie przegrzewa się przy nocnym generowaniu

echo "=== Pi Thermal Protection Setup ==="

# 1. Fan na 50°C (jeśli jest GPIO fan)
if grep -q "gpio-fan" /boot/firmware/config.txt 2>/dev/null; then
    sudo sed -i 's/temp=[0-9]*/temp=50000/' /boot/firmware/config.txt
    echo "✅ Fan threshold ustawiony na 50°C"
elif grep -q "gpio-fan" /boot/config.txt 2>/dev/null; then
    sudo sed -i 's/temp=[0-9]*/temp=50000/' /boot/config.txt
    echo "✅ Fan threshold ustawiony na 50°C"
else
    # Dodaj konfigurację fana
    echo "dtoverlay=gpio-fan,gpiopin=14,temp=50000" | sudo tee -a /boot/firmware/config.txt 2>/dev/null || \
    echo "dtoverlay=gpio-fan,gpiopin=14,temp=50000" | sudo tee -a /boot/config.txt
    echo "✅ Dodano konfigurację fana (50°C)"
fi

# 2. CPU max frequency — ogranicz do 1.5GHz (Pi 5 domyślnie 2.4GHz)
# To DRASTYCZNIE zmniejsza temperaturę
echo "arm_freq=1500" | sudo tee -a /boot/firmware/config.txt 2>/dev/null || \
echo "arm_freq=1500" | sudo tee -a /boot/config.txt
echo "✅ CPU throttled do 1.5GHz"

# 3. GPU memory — zmniejsz (nie używamy GPU)
echo "gpu_mem=64" | sudo tee -a /boot/firmware/config.txt 2>/dev/null || \
echo "gpu_mem=64" | sudo tee -a /boot/config.txt
echo "✅ GPU memory zmniejszona do 64MB"

echo ""
echo "=== GOTOWE! Wymagany restart ==="
echo "sudo reboot"
