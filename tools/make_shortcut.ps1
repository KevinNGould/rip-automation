# make_shortcut.ps1  -  put a "RIP Agent" shortcut on the desktop that opens the
# control window (big Pause / Resume / Start button). No admin needed.
#   powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe).Source
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "RIP Agent.lnk"

$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($lnk)
$s.TargetPath = $pythonw
$s.Arguments = "`"$Repo\tools\rip_agent_control.pyw`""
$s.WorkingDirectory = $Repo
$s.IconLocation = "$env:SystemRoot\System32\shell32.dll,16"
$s.Description = "Pause, resume or start the RIP agent"
$s.Save()
Write-Host "Made $lnk"
