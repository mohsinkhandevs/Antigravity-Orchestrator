' ==============================================================================
' Antigravity CDP Studio -- Silent Background Launcher (No Terminal Window)
' ==============================================================================
Option Explicit

Dim WshShell, fso, scriptDir, pythonwExe, cmd

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Ensure working directory is always set to this project folder
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir

' 1. Check Python 3.14 user installation directory
pythonwExe = WshShell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe")

' 2. Fallback to global PATH pythonw if custom location
If Not fso.FileExists(pythonwExe) Then
    pythonwExe = "pythonw.exe"
End If

' Build command to launch main.py silently without any console window
cmd = """" & pythonwExe & """ """ & scriptDir & "\main.py"""

' Run options:
'   1     = SW_SHOWNORMAL (Displays Studio GUI window; pythonw.exe ensures NO console/cmd window)
'   False = Asynchronous (Launches application and exits VBS immediately)
WshShell.Run cmd, 1, False

Set WshShell = Nothing
Set fso = Nothing
