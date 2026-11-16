@echo off
echo Encerrando Deepgram Dictation...
powershell -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*dictation.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('Processo finalizado: ' + $_.ProcessId) }"
echo Concluido.
pause
