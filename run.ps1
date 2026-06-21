param(
    [ValidateSet("run", "scan", "test-email", "web")]
    [string]$Command = "run"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath ".env")) {
    Write-Host "Missing .env configuration file." -ForegroundColor Yellow
    Write-Host "Create it with: Copy-Item .env.example .env"
    exit 1
}

$env:PYTHONPATH = Join-Path $PSScriptRoot "src"
python -m crypto_trader $Command
