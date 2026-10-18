$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $root 'api')
try {
    uv run --locked uvicorn call_intelligence_api.main:app --reload --host 127.0.0.1 --port 8000
} finally {
    Pop-Location
}
