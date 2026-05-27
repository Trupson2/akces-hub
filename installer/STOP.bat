@echo off
REM ============================================
REM AKCES HUB - STOP
REM Zatrzymuje wszystkie procesy Pythona zwiazane z app
REM ============================================
echo Zatrzymuje AKCES HUB...
taskkill /F /FI "WINDOWTITLE eq AKCES HUB*" 2>nul
taskkill /F /IM pythonw.exe 2>nul
echo Gotowe.
timeout /t 2 /nobreak >nul
