param(
  [string]$ServiceName = "FoundryVTTModulator",
  [string]$PythonExe = "$env:LocalAppData\Programs\Python\Python311\python.exe",
  [string]$WorkingDirectory = "D:\dev\foundryvtt-modulator",
  [string]$DataRoot = "D:\rpg\foundry\foundryVersions\FoundryVTT-WindowsPortable-13.351",
  [int]$Port = 8787
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PythonExe)) {
  throw "Python executable not found: $PythonExe"
}
if (-not (Test-Path $WorkingDirectory)) {
  throw "Working directory not found: $WorkingDirectory"
}

$binPath = "`"$env:ComSpec`" /c cd /d `"$WorkingDirectory`" && set RESOLVER_DATA_ROOT=$DataRoot && set RESOLVER_BIND_HOST=0.0.0.0 && set RESOLVER_BIND_PORT=$Port && `"$PythonExe`" -m service.server"

sc.exe create $ServiceName binPath= "$binPath" start= auto | Out-Null
sc.exe description $ServiceName "FoundryVTT Modulator API" | Out-Null
sc.exe start $ServiceName | Out-Null

Write-Host "Service installed and started: $ServiceName"
