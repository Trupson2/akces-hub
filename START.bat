@echo off
echo ==========================================
echo AKCES HUB - Uruchamianie
echo ==========================================
echo.

echo 1. Zamykam stare procesy Python...
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM pythonw.exe >nul 2>&1
timeout /t 2 /nobreak >nul
echo    OK!
echo.

echo 2. Instaluje zaleznosci (pierwszy raz)...
pip install -r requirements.txt >nul 2>&1
echo    OK!
echo.

echo 3. Uruchamianie serwera...
echo.
echo Otworz w przegladarce: http://localhost:5000
echo.
echo WAZNE: Nie zamykaj tego okna!
echo Aby zatrzymac: Ctrl + C
echo.

python app.py

pause
