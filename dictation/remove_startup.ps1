$StartupFolder = [System.Environment]::GetFolderPath('Startup')
$ShortcutPath = Join-Path $StartupFolder "Deepgram Dictation.lnk"

if (Test-Path $ShortcutPath) {
    Remove-Item $ShortcutPath -Force
    Write-Host "Atalho removido da inicializacao do Windows com sucesso."
} else {
    Write-Host "Atalho nao encontrado na pasta de inicializacao."
}
