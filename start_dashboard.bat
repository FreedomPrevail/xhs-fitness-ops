@echo off
setlocal
cd /d "%~dp0"

where opencli >nul 2>nul
if errorlevel 1 (
  echo Notice: opencli was not found. The dashboard and local generation can still run,
  echo but Xiaohongshu connected collection will be unavailable.
)

if not exist ".venv\Scripts\python.exe" (
  where python >nul 2>nul || (
    echo Python 3 was not found. Install Python 3.10 or later first.
    pause
    exit /b 2
  )
  echo Creating the project Python environment...
  python -m venv .venv || exit /b 1
)

".venv\Scripts\python.exe" -c "import flask, yaml, PIL, jieba, jinja2, playwright, pptx, keyring" >nul 2>nul
if errorlevel 1 (
  echo Installing project dependencies...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt || exit /b 1
)

start "" "http://127.0.0.1:5000"
".venv\Scripts\python.exe" app.py
