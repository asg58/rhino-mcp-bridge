$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ToolsDir = Join-Path $ProjectDir ".tools"
New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null

$release = Invoke-RestMethod -Uri "https://api.github.com/repos/openai/tunnel-client/releases/latest" -Headers @{"User-Agent"="rhino-mcp-bridge"}
$assets = @($release.assets)

$asset = $assets | Where-Object {
    $_.name -match '(?i)windows' -and $_.name -match '(?i)(amd64|x86_64)' -and $_.name -match '(?i)\.zip$'
} | Select-Object -First 1

if (-not $asset) {
    $asset = $assets | Where-Object {
        $_.name -match '(?i)windows' -and $_.name -match '(?i)(amd64|x86_64)' -and $_.name -match '(?i)\.exe$'
    } | Select-Object -First 1
}

if (-not $asset) {
    Write-Host "Windows amd64 asset niet automatisch gevonden. Beschikbare assets:" -ForegroundColor Yellow
    $assets | ForEach-Object { Write-Host $_.name }
    throw "Download tunnel-client handmatig en zet tunnel-client.exe in .tools"
}

$downloadPath = Join-Path $ToolsDir $asset.name
Write-Host "Downloading $($asset.name) from $($release.tag_name)..." -ForegroundColor Cyan
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $downloadPath

if ($asset.name -match '(?i)\.zip$') {
    $extract = Join-Path $ToolsDir "tunnel-client"
    if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
    Expand-Archive -LiteralPath $downloadPath -DestinationPath $extract -Force
    $exe = Get-ChildItem -Path $extract -Recurse -Filter "tunnel-client.exe" | Select-Object -First 1
} else {
    $exe = Get-Item $downloadPath
}

if (-not $exe) { throw "tunnel-client.exe not found after download" }

Copy-Item -LiteralPath $exe.FullName -Destination (Join-Path $ToolsDir "tunnel-client.exe") -Force
Write-Host "Ready: $(Join-Path $ToolsDir 'tunnel-client.exe')" -ForegroundColor Green
& (Join-Path $ToolsDir "tunnel-client.exe") help quickstart
