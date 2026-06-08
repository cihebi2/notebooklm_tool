param(
  [int]$IntervalSeconds = 480,
  [int]$PerAccountDelaySeconds = 20,
  [switch]$Once
)

$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
  throw "Python venv not found. Run .\run.ps1 or install requirements first."
}

$script = Join-Path $PSScriptRoot "scripts\notebooklm_keepalive_accounts.py"
$argsList = @(
  $script,
  "--interval-seconds", "$IntervalSeconds",
  "--per-account-delay-seconds", "$PerAccountDelaySeconds"
)
if ($Once) {
  $argsList += "--once"
}

& $python @argsList
