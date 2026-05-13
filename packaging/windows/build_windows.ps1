param(
  [string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$DistDir = Join-Path $Root "dist\windows"
New-Item -ItemType Directory -Force $DistDir | Out-Null
$Python = (Get-Command python).Source

& $Python -m pip install --upgrade pip pyinstaller
& $Python -m pip install -r "$Root\backend\requirements.txt"

Push-Location $Root
try {
  & $Python -m PyInstaller `
    --noconfirm `
    --onefile `
    --name "foundryvtt-modulator-api-$Version" `
    backend\run_fastapi.py
  Copy-Item -Force ".\dist\foundryvtt-modulator-api-$Version.exe" $DistDir
}
finally {
  Pop-Location
}

Write-Host "Built: $DistDir\foundryvtt-modulator-api-$Version.exe"
