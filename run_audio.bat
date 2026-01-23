@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM NotebookLM Auto Audio Runner (Windows)
REM Put:
REM   - your report at: report.md  (or pass a file path as the first argument)
REM   - your account cookies at: accounts\*.json  (each is a storage_state.json from notebooklm-py)
REM Then double-click this file.

cd /d "%~dp0"

REM Use UTF-8 in Python where possible (helps with Chinese paths/content)
set PYTHONUTF8=1

REM Resolve report path
set "REPORT=%~1"
if "%REPORT%"=="" set "REPORT=report.md"

REM Create folders if missing
if not exist "accounts" (
  mkdir "accounts" >nul 2>&1
)
if not exist "outputs" (
  mkdir "outputs" >nul 2>&1
)

if not exist "%REPORT%" (
  echo [ERROR] Report file not found: "%REPORT%"
  echo Put your report into: "%~dp0report.md"
  echo Or run: run_audio.bat "C:\path\to\your\report.md"
  echo.
  echo Accounts folder: "%~dp0accounts\"
  echo Put each account's storage_state.json into accounts\*.json
  echo Example login (once per account):
  echo   pip install "notebooklm-py[browser]" ^&^& playwright install chromium
  echo   notebooklm --storage accounts\acc1.json login
  echo.
  pause
  exit /b 1
)

REM Create venv if needed
if not exist ".venv\\Scripts\\python.exe" (
  echo [INFO] Creating venv...
  py -3 -m venv ".venv" 2>nul || python -m venv ".venv"
  if errorlevel 1 (
    echo [ERROR] Failed to create venv. Please ensure Python 3.10+ is installed.
    pause
    exit /b 1
  )
)

set "PY=.venv\\Scripts\\python.exe"

echo [INFO] Installing dependencies (first run may take a while)...
"%PY%" -m pip install --upgrade pip >nul 2>&1
"%PY%" -m pip install -r "%~dp0requirements.txt" >nul 2>&1

echo [INFO] Starting...
echo   Report   : %REPORT%
echo   Accounts : %~dp0accounts\*.json
echo   Output   : %~dp0outputs\
echo.

"%PY%" -X utf8 "%~dp0notebooklm_auto_audio.py" ^
  --report "%REPORT%" ^
  --accounts-dir "%~dp0accounts" ^
  --out-dir "%~dp0outputs" ^
  --min-minutes 40 ^
  --max-attempts 20 ^
  --strategy roundrobin ^
  --audio-length LONG ^
  --audio-format DEEP_DIVE ^
  --language zh ^
  --convert-mp3 ^
  --delete-short-artifacts

echo.
if errorlevel 1 (
  echo [DONE] Finished with errors. See messages above.
) else (
  echo [DONE] Completed.
)
pause
