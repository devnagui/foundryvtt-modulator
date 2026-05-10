param(
  [string]$ServiceName = "FoundryVTTModulator"
)

$ErrorActionPreference = "Continue"
sc.exe stop $ServiceName | Out-Null
Start-Sleep -Seconds 1
sc.exe delete $ServiceName | Out-Null
Write-Host "Service removed: $ServiceName"
