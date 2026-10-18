$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
pnpm.cmd --dir (Join-Path $root 'web') dev
