@echo off
REM ============================================
REM AKCES HUB - INSTALL (first-time setup)
REM Uruchamiane RAZ po rozpakowaniu zipa
REM ============================================
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

echo ============================================
echo   AKCES HUB - Instalacja
echo ============================================
echo.

REM ---- 1. Sprawdz/uzyj embedded Python ----
if exist "python\python.exe" (
    set "PY=%~dp0..\python\python.exe"
    echo [1/5] Python: embedded ^(folder python\^)
) else (
    where python >nul 2>&1
    if !ERRORLEVEL! neq 0 (
        echo BLAD: Python nie jest zainstalowany.
        echo Pobierz Python 3.10+ z: https://www.python.org/downloads/
        echo Wazne: zaznacz "Add Python to PATH" podczas instalacji!
        start "" "https://www.python.org/downloads/"
        pause
        exit /b 1
    )
    set "PY=python"
    echo [1/5] Python: systemowy
)

REM ---- 2. Pip install requirements ----
echo [2/5] Instaluje biblioteki Python ^(moze potrwac 2-3 min^)...
"%PY%" -m pip install --upgrade pip --quiet
"%PY%" -m pip install -r requirements.txt --quiet
if !ERRORLEVEL! neq 0 (
    echo BLAD: Instalacja bibliotek nie powiodla sie.
    echo Sprobuj uruchomic recznie: %PY% -m pip install -r requirements.txt
    pause
    exit /b 1
)
echo      OK

REM ---- 3. Inicjalizacja bazy (init_db) ----
echo [3/5] Inicjalizacja bazy danych...
"%PY%" -c "from modules.database import init_db; init_db(); print('OK')"

REM ---- 4. Skrot na pulpicie + autostart ----
echo [4/5] Tworze skrot na pulpicie + autostart...
set "SHORTCUT=%USERPROFILE%\Desktop\AKCES HUB.lnk"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AKCES HUB.lnk"
set "TARGET=%~dp0START.bat"

powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT%'); $s.TargetPath = '%TARGET%'; $s.WorkingDirectory = '%~dp0..'; $s.WindowStyle = 7; $s.Description = 'AKCES HUB'; $s.Save()"
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%STARTUP%'); $s.TargetPath = '%TARGET%'; $s.WorkingDirectory = '%~dp0..'; $s.WindowStyle = 7; $s.Description = 'AKCES HUB autostart'; $s.Save()"
echo      OK ^(pulpit + autostart Windows^)

REM ---- 5. Uruchom + otworz przegladarke ----
echo [5/5] Uruchamiam AKCES HUB...
call "%~dp0START.bat"

echo.
echo ============================================
echo   GOTOWE!
echo ============================================
echo.
echo Aplikacja dziala na: http://localhost:5000
echo Skrot na pulpicie: AKCES HUB
echo Autostart: TAK ^(uruchamia sie przy starcie Windowsa^)
echo.
echo Pierwsze logowanie: stworz uzytkownika admin w przegladarce.
echo.
pause
