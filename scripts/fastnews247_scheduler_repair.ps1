$ErrorActionPreference = 'Stop'
$taskName = 'OpenClaw Fast News 247'
$root = Split-Path $PSScriptRoot -Parent
$python = (Get-Command python).Source
$pythonw = Join-Path (Split-Path $python -Parent) 'pythonw.exe'
if (!(Test-Path $pythonw)) { throw 'pythonw unavailable' }
$task = Get-ScheduledTask -TaskName $taskName
$task | Export-ScheduledTask | Set-Content -Encoding Unicode (Join-Path $root 'storage\fastnews247\scheduler_before_repair.xml')
$action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"' + (Join-Path $PSScriptRoot 'fastnews247_scheduled.pyw') + '"') -WorkingDirectory $root
Set-ScheduledTask -TaskName $taskName -Action $action | Out-Null
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State,Actions
