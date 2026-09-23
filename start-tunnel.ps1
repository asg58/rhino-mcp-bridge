param(
    [Parameter(Mandatory=$true)][string]$TunnelId
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Tunnel = Join-Path $ProjectDir ".tools\tunnel-client.exe"
$PyExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$EnvFile = Join-Path $ProjectDir ".env.ps1"

if (-not (Test-Path $Tunnel)) {
    throw "tunnel-client.exe ontbreekt. Run eerst .\download-tunnel-client.ps1"
}
if (-not (Test-Path $PyExe)) {
    throw "Python venv ontbreekt. Run eerst .\setup.ps1"
}
if (-not (Test-Path $EnvFile)) {
    throw ".env.ps1 ontbreekt. Run eerst .\setup.ps1"
}
if (-not $env:CONTROL_PLANE_API_KEY) {
    throw "CONTROL_PLANE_API_KEY is niet gezet in deze PowerShell-sessie."
}

. $EnvFile

$McpCommand = "$PyExe -m rhino_bridge.server"

Write-Host ""
Write-Host "Checking tunnel configuration..." -ForegroundColor Cyan
Write-Host "Tunnel: $TunnelId"
Write-Host "MCP:    $McpCommand"
Write-Host ""

$doctorArgs = @(
    "doctor",
    "--control-plane.tunnel-id", $TunnelId,
    "--mcp.command", $McpCommand,
    "--explain"
)

& $Tunnel @doctorArgs

if ($LASTEXITCODE -ne 0) {
    throw "tunnel-client doctor failed"
}

Write-Host ""
Write-Host "Doctor passed. Starting tunnel..." -ForegroundColor Green
Write-Host "Laat dit PowerShell-venster open zolang ChatGPT de bridge gebruikt."
Write-Host ""

$runArgs = @(
    "run",
    "--control-plane.tunnel-id", $TunnelId,
    "--mcp.command", $McpCommand,
    "--health.listen-addr", "127.0.0.1:8080"
)

& $Tunnel @runArgs
