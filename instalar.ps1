# Instala el worker v3 de Hodor en Windows: copia el kit a %USERPROFILE%\.hodor-v3\kit y lo deja corriendo al iniciar sesión.
$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$dest = Join-Path $env:USERPROFILE ".hodor-v3\kit"
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { throw "Falta el CLI claude: instálalo e inicia sesión (claude) antes de seguir." }
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw "Falta Python 3." }
New-Item -ItemType Directory -Force -Path $dest | Out-Null
# Actualizar no borra nada: se reemplaza el código y se conserva tu config.json, tus análisis y tu base local (~/.hodor-v3).
& $py (Join-Path $dir "fusionar_config.py") (Join-Path $dir "config.json") (Join-Path $dest "config.json")
if ($LASTEXITCODE -ne 0) { throw "No pude preparar config.json." }
Copy-Item (Join-Path $dir "revisor_dev.py"), (Join-Path $dir "pruebas_dev.py"), (Join-Path $dir "panel.html"), (Join-Path $dir "fusionar_config.py") $dest -Force
# El plan de pruebas lee la BD de ci con tu usuario de solo lectura: necesita pymysql.
& $py -m pip install --user --quiet pymysql
if ($LASTEXITCODE -ne 0) { Write-Host "No pude instalar pymysql: el plan de pruebas no podrá leer la BD (pip install --user pymysql)." }
& $py (Join-Path $dest "revisor_dev.py") --estado
$pyw = $py -replace "python.exe$", "pythonw.exe"
$accion = New-ScheduledTaskAction -Execute $pyw -Argument "`"$(Join-Path $dest 'revisor_dev.py')`"" -WorkingDirectory $dest
$disparo = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "HodorV3Worker" -Action $accion -Trigger $disparo -Settings $ajustes -Force | Out-Null
Stop-ScheduledTask -TaskName "HodorV3Worker" -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName "HodorV3Worker"
Start-Sleep -Seconds 3
Start-Process "http://127.0.0.1:7788"
Write-Host "Listo: el worker v3 corre en segundo plano. Tu panel: http://127.0.0.1:7788 · Log: $env:USERPROFILE\.hodor-v3\worker.log"
