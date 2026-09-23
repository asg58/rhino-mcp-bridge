param(
    [Parameter(Mandatory=$true)][string]$TunnelId,
    [string]$Profile = "rhino-bridge"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Tunnel = Join-Path $ProjectDir ".tools\tunnel-client.exe"
$PyExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (-not (Test-Path $Tunnel)) { throw "Run .\download-tunnel-client.ps1 first" }
if (-not (Test-Path $PyExe)) { throw "Run .\setup.ps1 first" }
if (-not $env:CONTROL_PLANE_API_KEY) { throw "Set CONTROL_PLANE_API_KEY in this PowerShell session first. Do not commit it." }
if (-not (Test-Path "$ProjectDir\.env.ps1")) { throw "Missing .env.ps1; run setup.ps1 first" }

. "$ProjectDir\.env.ps1"

$McpCommand = ('"{0}" -m rhino_bridge.server' -f $PyExe)

Write-Host "Preparing OpenAI tunnel profile '$Profile'..." -ForegroundColor Cyan
& $Tunnel init --sample sample_mcp_stdio_local --profile $Profile --tunnel-id $TunnelId --mcp-command $McpCommand

if ($LASTEXITCODE -ne 0) {
    Write-Host "Profile init returned non-zero; it may already exist. Continuing with doctor." -ForegroundColor Yellow
}

& $Tunnel doctor --profile $Profile --explain
if ($LASTEXITCODE -ne 0) { throw "tunnel-client doctor failed" }

Write-Host "Starting tunnel. Keep this PowerShell window open." -ForegroundColor Green
& $Tunnel run --profile $Profile
