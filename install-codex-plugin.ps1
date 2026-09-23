$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Tunnel = Join-Path $ProjectDir ".tools\tunnel-client.exe"
$ExportDir = Join-Path $ProjectDir ".tools\tunnel-mcp"

if (-not (Test-Path -LiteralPath $Tunnel -PathType Leaf)) {
    throw "tunnel-client.exe ontbreekt: $Tunnel"
}

$Tunnel = (Get-Item -LiteralPath $Tunnel).FullName
Write-Host "Tunnel client: $Tunnel" -ForegroundColor Cyan

# Prove Windows can execute the binary before continuing.
& $Tunnel --version
if ($LASTEXITCODE -ne 0) {
    throw "tunnel-client.exe kan niet worden uitgevoerd"
}

if (Test-Path -LiteralPath $ExportDir) {
    Remove-Item -LiteralPath $ExportDir -Recurse -Force
}

Write-Host "Exporting Codex plugin bundle..." -ForegroundColor Cyan
& $Tunnel codex plugin export --dir $ExportDir
if ($LASTEXITCODE -ne 0) {
    throw "Plugin export failed"
}

$Installer = Join-Path $ExportDir "scripts\Install-Plugin.ps1"
if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    throw "Install-Plugin.ps1 niet gevonden na export: $Installer"
}

Write-Host "Installing Codex plugin using official Windows fallback..." -ForegroundColor Cyan
powershell -NoProfile -ExecutionPolicy Bypass -File $Installer --tunnel-client-bin $Tunnel
if ($LASTEXITCODE -ne 0) {
    throw "Codex plugin installation failed"
}

Write-Host ""
Write-Host "Codex plugin installed. Verifying..." -ForegroundColor Green
& $Tunnel codex status
& $Tunnel codex diagnose --json

Write-Host ""
Write-Host "Herstart nu Codex/VS Code zodat de plugin inventory opnieuw wordt geladen." -ForegroundColor Green
