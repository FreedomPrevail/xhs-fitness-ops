@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Project environment not found. Run start_dashboard.bat first.
  exit /b 2
)
".venv\Scripts\python.exe" -m personal_ops.cli %*
