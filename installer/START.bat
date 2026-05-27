@echo off
REM ============================================
REM AKCES HUB - START
REM Uruchamia aplikacje w tle (bez okna konsoli)
REM i otwiera przegladarke na localhost:5000
REM ============================================
setlocal
cd /d "%~dp0\.."

REM Sprawdz czy jest embedded Python (folder python\)
if exist "python\python.exe" (
    set "PYTHON_EXE=%~dp0..\python\pythonw.exe"
) else (
    REM Fallback: system Python
    where pythonw >nul 2>&1
    if %ERRORLEVEL% neq 0 (
        echo BLAD: Python nie jest zainstalowany.
        echo Pobierz z https://www.python.org/downloads/ ^(zaznacz "Add to PATH"^)
        pause
        exit /b 1
    )
    set "PYTHON_EXE=pythonw.exe"
)

REM Sprawdz czy juz nie chodzi (lock plik z PID)
if exist ".running_pid" (
    for /f "tokens=*" %%i in (.running_pid) do (
        tasklist /FI "PID eq %%i" 2>nul | findstr /i "python" >nul
        if not errorlevel 1 (
            echo AKCES HUB juz dziala ^(PID %%i^). Otwieram przegladarke...
            start "" "http://localhost:5000"
            exit /b 0
        )
    )
)

REM Wystartuj pythonw cicho w tle
echo Uruchamiam AKCES HUB...
start /b "" "%PYTHON_EXE%" app.py

REM Daj 4s na start serwera
timeout /t 4 /nobreak >nul

REM Otworz przegladarke
start "" "http://localhost:5000"

REM Watcher: jak proces wyjdzie z kodem >0 (np. po update przez .restart_pending), uruchom ponownie
REM Glowny watch loop w osobnym oknie (uzytkownik moze go zamknac)
REM start "AKCES HUB Watchdog" cmd /c "%~dp0watchdog.bat"

exit /b 0
