' Inicia a telinha sem abrir janela (duplo clique ou na Inicialização do Windows)
Set sh = CreateObject("WScript.Shell")
base = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
sh.Run """" & base & "\.venv\Scripts\pythonw.exe"" """ & base & "\screen.py""", 0, False
