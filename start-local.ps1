$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path "$ProjectDir\.venv\Scripts\python.exe")) { throw "Run setup.ps1 first" }
if (-not (Test-Path "$ProjectDir\.env.ps1")) { throw "Missing .env.ps1; run setup.ps1 first" }
. "$ProjectDir\.env.ps1"
& "$ProjectDir\.venv\Scripts\python.exe" -m rhino_bridge.server
