param(
    [string]$RhinoRoot = "C:\Program Files\Rhino 8",
    [string]$WorkspaceRoot = "D:\mijn\_app\rhino2"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path -LiteralPath $RhinoRoot -PathType Container)) {
    $RhinoRoot = Read-Host "Rhino map niet gevonden. Geef Rhino root"
}
if (-not (Test-Path -LiteralPath $WorkspaceRoot -PathType Container)) {
    $WorkspaceRoot = Read-Host "Workspace niet gevonden. Geef VS Code workspace"
}
if (-not (Test-Path -LiteralPath $RhinoRoot -PathType Container)) { throw "Rhino root bestaat niet: $RhinoRoot" }
if (-not (Test-Path -LiteralPath $WorkspaceRoot -PathType Container)) { throw "Workspace bestaat niet: $WorkspaceRoot" }

$RhinoRoot = (Resolve-Path -LiteralPath $RhinoRoot).Path
$WorkspaceRoot = (Resolve-Path -LiteralPath $WorkspaceRoot).Path

$Python = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $Python = "py" }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $Python = "python" }
else { throw "Python 3.10+ is nodig" }

Write-Host "[1/4] Virtual environment" -ForegroundColor Cyan
& $Python -m venv "$ProjectDir\.venv"
$PyExe = "$ProjectDir\.venv\Scripts\python.exe"

Write-Host "[2/4] Dependencies" -ForegroundColor Cyan
& $PyExe -m pip install --upgrade pip
& $PyExe -m pip install -e "$ProjectDir[dev]"

$envFile = Join-Path $ProjectDir ".env.ps1"
@"
Set-Item Env:RHINO_ROOT '$($RhinoRoot.Replace("'", "''"))'
Set-Item Env:WORKSPACE_ROOT '$($WorkspaceRoot.Replace("'", "''"))'
Set-Item Env:RHINO_BRIDGE_ALLOW_SECRETS '0'
"@ | Set-Content -LiteralPath $envFile -Encoding UTF8

Write-Host "[3/4] Security tests" -ForegroundColor Cyan
. $envFile
& $PyExe -m pytest -q "$ProjectDir\tests"
if ($LASTEXITCODE -ne 0) { throw "Tests failed" }

$config = @{
    mcpServers = @{
        "rhino-bridge" = @{
            command = $PyExe
            args = @("-m", "rhino_bridge.server")
            env = @{
                RHINO_ROOT = $RhinoRoot
                WORKSPACE_ROOT = $WorkspaceRoot
                RHINO_BRIDGE_ALLOW_SECRETS = "0"
            }
        }
    }
} | ConvertTo-Json -Depth 8

$config | Set-Content -LiteralPath (Join-Path $ProjectDir "mcp-config.json") -Encoding UTF8

Write-Host "[4/4] Ready" -ForegroundColor Green
Write-Host "Rhino (read-only): $RhinoRoot"
Write-Host "Workspace: $WorkspaceRoot"
Write-Host "Run .\start-local.ps1 for a local MCP start."
Write-Host "Then run .\download-tunnel-client.ps1 and .\start-tunnel.ps1 -TunnelId tunnel_..."
