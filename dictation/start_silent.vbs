Set WshShell = CreateObject("WScript.Shell")
Set Fso = CreateObject("Scripting.FileSystemObject")
Dir = Fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = Dir
WshShell.Run "pythonw """ & Dir & "\dictation.py""", 0, False
Set WshShell = Nothing
