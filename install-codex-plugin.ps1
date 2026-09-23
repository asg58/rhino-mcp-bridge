$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Tunnel = Join-Path $ProjectDir ".tools\tunnel-client.exe"
$SourceDir = Join-Path $ProjectDir ".tools\tunnel-client-src-v0.0.14"

if (-not (Test-Path -LiteralPath $Tunnel -PathType Leaf)) {
    throw "tunnel-client.exe ontbreekt: $Tunnel"
}

$Tunnel = (Get-Item -LiteralPath $Tunnel).FullName
Write-Host "Tunnel client: $Tunnel" -ForegroundColor Cyan

# Prove the downloaded Windows binary itself works.
& $Tunnel --version
if ($LASTEXITCODE -ne 0) {
    throw "tunnel-client.exe kan niet worden uitgevoerd"
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git is nodig voor deze Windows workaround"
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js is nodig voor de Tunnel MCP Codex plugin (de plugin start mcp/server.cjs met node)"
}

# tunnel-client v0.0.14 contains a Windows-only installer bug:
# pkg/codexplugin/writeBinaryHint checks Unix executable bits (0o111).
# A valid Windows .exe can therefore be rejected even though Windows executes it.
# Work around ONLY that installer check by installing the official v0.0.14
# plugin bundle directly from OpenAI's own tagged source tree.
Write-Host ""
Write-Host "Installing official OpenAI Tunnel MCP plugin (v0.0.14 Windows workaround)..." -ForegroundColor Cyan

if (Test-Path -LiteralPath $SourceDir) {
    Remove-Item -LiteralPath $SourceDir -Recurse -Force
}

& git clone --depth 1 --branch v0.0.14 https://github.com/openai/tunnel-client.git $SourceDir
if ($LASTEXITCODE -ne 0) {
    throw "Kon OpenAI tunnel-client v0.0.14 broncode niet ophalen"
}

$PluginSource = Join-Path $SourceDir "plugins\tunnel-mcp"
$ManifestSource = Join-Path $PluginSource ".codex-plugin\plugin.json"
if (-not (Test-Path -LiteralPath $ManifestSource -PathType Leaf)) {
    throw "Officiele plugin bundle niet gevonden in OpenAI v0.0.14 source"
}

$CodexHome = if ($env:CODEX_HOME -and $env:CODEX_HOME.Trim()) {
    $env:CODEX_HOME.Trim()
} else {
    Join-Path $HOME ".codex"
}

$Target = Join-Path $CodexHome "plugins\cache\debug\tunnel-mcp\local"
$TargetParent = Split-Path -Parent $Target
New-Item -ItemType Directory -Force -Path $TargetParent | Out-Null

if (Test-Path -LiteralPath $Target) {
    Remove-Item -LiteralPath $Target -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Target | Out-Null

Copy-Item -Path (Join-Path $PluginSource "*") -Destination $Target -Recurse -Force
# Copy hidden files/directories that wildcard copy can omit on some PowerShell versions.
Copy-Item -LiteralPath (Join-Path $PluginSource ".codex-plugin") -Destination $Target -Recurse -Force
Copy-Item -LiteralPath (Join-Path $PluginSource ".mcp.json") -Destination $Target -Force

$HintPath = Join-Path $Target ".tunnel-client-bin"
Set-Content -LiteralPath $HintPath -Value $Tunnel -Encoding UTF8

$ConfigPath = Join-Path $CodexHome "config.toml"
New-Item -ItemType Directory -Force -Path $CodexHome | Out-Null
if (Test-Path -LiteralPath $ConfigPath) {
    $Config = Get-Content -LiteralPath $ConfigPath -Raw
} else {
    $Config = ""
}

# Remove any stale tunnel-mcp@debug section, then add the canonical enabled section.
$PluginSectionPattern = '(?ms)^\[plugins\."tunnel-mcp@debug"\][^\[]*(?=^\[|\z)'
$Config = [regex]::Replace($Config, $PluginSectionPattern, "")
$Config = $Config.TrimEnd() + [Environment]::NewLine + [Environment]::NewLine +
@'
[plugins."tunnel-mcp@debug"]
enabled = true
'@

# Ensure Codex plugins are globally enabled without disturbing other feature settings.
$FeaturesPattern = '(?ms)^\[features\](.*?)(?=^\[|\z)'
$FeatureMatch = [regex]::Match($Config, $FeaturesPattern)
if ($FeatureMatch.Success) {
    $FeaturesBlock = $FeatureMatch.Value
    if ($FeaturesBlock -match '(?m)^plugins\s*=\s*(true|false)\s*$') {
        $NewFeaturesBlock = [regex]::Replace($FeaturesBlock, '(?m)^plugins\s*=\s*(true|false)\s*$', 'plugins = true')
    } else {
        $NewFeaturesBlock = $FeaturesBlock.TrimEnd() + [Environment]::NewLine + "plugins = true" + [Environment]::NewLine
    }
    $Config = $Config.Substring(0, $FeatureMatch.Index) + $NewFeaturesBlock + $Config.Substring($FeatureMatch.Index + $FeatureMatch.Length)
} else {
    $Config = $Config.TrimEnd() + [Environment]::NewLine + [Environment]::NewLine +
@'
[features]
plugins = true
'@
}

Set-Content -LiteralPath $ConfigPath -Value ($Config.Trim() + [Environment]::NewLine) -Encoding UTF8

$Manifest = Join-Path $Target ".codex-plugin\plugin.json"
if (-not (Test-Path -LiteralPath $Manifest -PathType Leaf)) {
    throw "Installatiecontrole faalde: plugin.json ontbreekt"
}
if (-not (Test-Path -LiteralPath $HintPath -PathType Leaf)) {
    throw "Installatiecontrole faalde: .tunnel-client-bin ontbreekt"
}

Write-Host ""
Write-Host "Installed plugin target:" -ForegroundColor Green
Write-Host "  $Target"
Write-Host "Binary hint:" -ForegroundColor Green
Write-Host "  $(Get-Content -LiteralPath $HintPath -TotalCount 1)"
Write-Host "Codex config:" -ForegroundColor Green
Write-Host "  $ConfigPath"

Write-Host ""
Write-Host "Checking tunnel-client view of Codex..." -ForegroundColor Cyan
& $Tunnel codex status --json
$statusExit = $LASTEXITCODE
& $Tunnel codex diagnose --json
$diagExit = $LASTEXITCODE

Write-Host ""
if ($statusExit -eq 0) {
    Write-Host "Codex plugin files/config are installed." -ForegroundColor Green
} else {
    Write-Host "Plugin files are installed, but 'codex status' returned $statusExit. Check output above." -ForegroundColor Yellow
}
if ($diagExit -ne 0) {
    Write-Host "'codex diagnose' returned $diagExit. The on-disk install checks above still passed." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Sluit nu alle Codex/VS Code vensters volledig af en start ze opnieuw." -ForegroundColor Green
