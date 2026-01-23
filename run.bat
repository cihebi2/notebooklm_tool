@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Double-click friendly runner for Windows (CMD)
REM Equivalent to run.ps1, but avoids PowerShell association/execution-policy issues.

cd /d "%~dp0"
set PYTHONUTF8=1

REM Optional: pass port as first argument, e.g. run.bat 8001
set "PORT=%~1"

if not exist ".venv\\Scripts\\python.exe" (
  echo [INFO] Creating venv...
  py -3 -m venv ".venv" 2>nul || python -m venv ".venv"
  if errorlevel 1 (
    echo [ERROR] Failed to create venv. Please install Python 3.10+ and retry.
    pause
    exit /b 1
  )
)

set "PY=.venv\\Scripts\\python.exe"

echo [INFO] Installing dependencies...
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip install failed.
  pause
  exit /b 1
)

if "%PORT%"=="" (
  echo [INFO] Selecting an available port...
  for %%P in (8000 8001 8002 8003 8080 8888 9000) do (
    "%PY%" -c "import socket,sys; p=int(sys.argv[1]); s=socket.socket(); s.bind(('127.0.0.1',p)); s.close()" %%P >nul 2>&1
    if not errorlevel 1 (
      set "PORT=%%P"
      goto :port_found
    )
  )
)
:port_found

if "%PORT%"=="" (
  echo [ERROR] Could not find a free port.
  echo Try closing other local servers, or run: run.bat 8001
  pause
  exit /b 1
)

echo [INFO] Starting server: http://127.0.0.1:%PORT%
echo Press CTRL+C to stop.

REM Note: On Windows, uvicorn --reload switches to SelectorEventLoop which breaks Playwright (browser login).
REM Enable reload only when you really need it: run.bat 8000 reload
set "RELOAD=%~2"
if /I "%RELOAD%"=="reload" (
  echo [INFO] Dev mode: reload enabled - may break browser login on Windows.
  "%PY%" -m uvicorn app.main:app --reload --host 127.0.0.1 --port %PORT%
) else (
  "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
)

pause
