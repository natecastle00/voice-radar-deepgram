# Cria uma tarefa agendada do Windows que roda o radar toda noite (23:50).
# Uso: .\windows-nightly.ps1   (de dentro da pasta deploy)
$radar = Join-Path (Split-Path $PSScriptRoot -Parent) "radar\radar.py"
$python = (Get-Command python).Source
$action = New-ScheduledTaskAction -Execute $python -Argument "`"$radar`" nightly"
$trigger = New-ScheduledTaskTrigger -Daily -At 23:50
Register-ScheduledTask -TaskName "Voice Radar Nightly" -Action $action -Trigger $trigger -Force
