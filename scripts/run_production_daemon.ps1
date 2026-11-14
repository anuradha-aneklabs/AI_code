# Harvey AI Sales Agent — Production Windows Daemon Runner
# Runs the dashboard server on port 5555 and ensures automatic restart on unexpected exit.

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Starting Harvey AI Sales Agent Production Service (Windows)" -ForegroundColor Green
Write-Host "Project Root: $ProjectRoot" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment not found at $PythonExe. Run setup first."
    exit 1
}

# Pre-flight check: ensure port 5555 is free
$PortInUse = Get-NetTCPConnection -LocalPort 5555 -ErrorAction SilentlyContinue
if ($PortInUse) {
    Write-Host "Port 5555 is already bound. Checking if existing Harvey process is alive..." -ForegroundColor Yellow
}

# Run loop with automatic restart
while ($true) {
    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Launching Harvey Dashboard on 127.0.0.1:5555..." -ForegroundColor Green
    & $PythonExe -m harvey.cli dashboard --port 5555
    $ExitCode = $LASTEXITCODE
    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Harvey exited with code $ExitCode. Restarting in 5 seconds..." -ForegroundColor Red
    Start-Sleep -Seconds 5
}

