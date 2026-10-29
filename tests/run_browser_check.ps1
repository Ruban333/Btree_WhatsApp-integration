# Starts one fresh preview server (admin on 8011, with demo data; the browser test switches users itself), then runs the browser test.
# Usage: powershell -File tests\run_browser_check.ps1 [output folder]
param([string]$Out = "$env:TEMP\btree-browser")
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
Get-NetTCPConnection -LocalPort 8011 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep 1
$log = "$env:TEMP\btree-prev"; New-Item -ItemType Directory -Force $log | Out-Null
Start-Process -FilePath $py -ArgumentList '-I', (Join-Path $root 'tests\preview.py'), 'admin', '8011' -WindowStyle Hidden `
  -RedirectStandardOutput "$log\p8011.out" -RedirectStandardError "$log\p8011.err" | Out-Null
$up = $false
for ($i = 0; $i -lt 90 -and -not $up; $i++) { Start-Sleep 1; try { Invoke-WebRequest "http://127.0.0.1:8011/api/auth/me" -UseBasicParsing -TimeoutSec 2 | Out-Null; $up = $true } catch {} }
if (-not $up) { Write-Host "preview on 8011 did not start"; Get-Content "$log\p8011.err" -Tail 10; exit 1 }
Remove-Item -Recurse -Force $Out -ErrorAction SilentlyContinue
node (Join-Path $root 'tests\browser_check.mjs') $Out
$code = $LASTEXITCODE
Get-NetTCPConnection -LocalPort 8011 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
exit $code
