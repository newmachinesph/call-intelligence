$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $root 'api')
try {
    uv run --locked python -m scripts.export_openapi
} finally {
    Pop-Location
}
Push-Location (Join-Path $root 'web')
try {
    pnpm.cmd exec openapi-typescript contracts/openapi.json -o contracts/api.d.ts
} finally {
    Pop-Location
}
