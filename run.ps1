$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath ".venv")) {
  python -m venv .venv
}

& .\.venv\Scripts\pip install -r requirements.txt

# Note: On Windows, `uvicorn --reload` switches to SelectorEventLoop which breaks Playwright (browser login).
# Use reload only when developing.
& .\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000
