$WshShell = New-Object -ComObject WScript.Shell
$StartupFolder = [System.Environment]::GetFolderPath('Startup')
$ShortcutPath = Join-Path $StartupFolder "Deepgram Dictation.lnk"
$WorkingDir = $PSScriptRoot
$TargetVBS = Join-Path $WorkingDir "start_silent.vbs"

$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "wscript.exe"
$Shortcut.Arguments = "`"$TargetVBS`""
$Shortcut.WorkingDirectory = $WorkingDir
$Shortcut.Description = "Ditado por voz Deepgram"
$Shortcut.Save()

Write-Host "Atalho adicionado na inicializacao do Windows:"
Write-Host $ShortcutPath
