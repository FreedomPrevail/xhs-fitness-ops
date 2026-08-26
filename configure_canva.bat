@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Please start start_dashboard.bat once to install the project environment.
  pause
  exit /b 2
)

echo Canva Developer Portal settings:
echo   Scope: design:content:write
echo   Redirect: http://127.0.0.1:8765/oauth/callback
echo.
set /p CANVA_SETUP_CLIENT_ID=Enter Canva Client ID: 
if "%CANVA_SETUP_CLIENT_ID%"=="" (
  echo Client ID is required.
  pause
  exit /b 2
)

".venv\Scripts\python.exe" -m canva_connect configure --client-id "%CANVA_SETUP_CLIENT_ID%" || goto :failed
".venv\Scripts\python.exe" -m canva_connect login || goto :failed
echo.
echo Canva is connected. Use Send to Canva in the dashboard.
pause
exit /b 0

:failed
echo Canva setup failed. Check docs\CANVA_CONNECT_API.md.
pause
exit /b 1
