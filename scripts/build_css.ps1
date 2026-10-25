# ─────────────────────────────────────────────────────────────────────────────
# scripts/build_css.ps1  ·  Build Tailwind CSS for production (Windows)
#
# Uses the Tailwind CSS standalone CLI (no Node.js required).
# Downloads the binary on first run and caches it in scripts\.
#
# Usage (PowerShell):
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\scripts\build_css.ps1
#
# The output file (src\promptcut\ui\static\app.css) is committed so that
# Docker and the live demo work without internet access.
# ─────────────────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$BinPath     = Join-Path $ScriptDir "tailwindcss.exe"

# ── 1. Download standalone binary if missing ──────────────────────────────────
if (-not (Test-Path $BinPath)) {
    Write-Host "Downloading Tailwind standalone CLI for Windows (v3.4.17)..." -ForegroundColor Cyan
    $Url = "https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-windows-x64.exe"
    try {
        Invoke-WebRequest -Uri $Url -OutFile $BinPath -UseBasicParsing
        Write-Host "Binary saved to scripts\tailwindcss.exe" -ForegroundColor Green
    } catch {
        Write-Error "Download failed. Check your internet connection or download manually from:`n$Url"
        exit 1
    }
}

# ── 2. Build ──────────────────────────────────────────────────────────────────
$InputCss  = Join-Path $ProjectRoot "src\promptcut\ui\input.css"
$OutputCss = Join-Path $ProjectRoot "src\promptcut\ui\static\app.css"
$Config    = Join-Path $ProjectRoot "tailwind.config.js"

Write-Host "Building Tailwind CSS..." -ForegroundColor Cyan
& $BinPath `
    --config $Config `
    --input  $InputCss `
    --output $OutputCss `
    --minify

if ($LASTEXITCODE -ne 0) {
    Write-Error "Tailwind build failed (exit $LASTEXITCODE)."
    exit $LASTEXITCODE
}

$Size = (Get-Item $OutputCss).Length
Write-Host "Built: src\promptcut\ui\static\app.css ($Size bytes, minified)" -ForegroundColor Green
