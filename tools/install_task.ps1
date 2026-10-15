# install_task.ps1  -  run the RIP agent at logon via Windows Task Scheduler (SPEC §9).
#
# Run once from an ADMINISTRATOR PowerShell in the repo root:
#   powershell -ExecutionPolicy Bypass -File tools\install_task.ps1
#
# What it sets up ("OSS RIP Agent"):
#   - starts  pythonw -m ripauto.agent --loop  in this repo when you log on
#   - "Run with highest privileges" (PO-TRY runs as admin)
#   - only while you're logged on (it drives PO-TRY on your desktop)
#   - restarts it a minute after it stops unexpectedly, no time limit
#   - no console window; watch state\agent.log or the dashboard instead
# It also starts the task right away. Undo with tools\uninstall_task.ps1.

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$TaskName = "OSS RIP Agent"

$principalCheck = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from an administrator PowerShell."
}

$python = (Get-Command python.exe).Source
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "pythonw.exe not found next to $python" }
if (-not (Test-Path (Join-Path $Repo "config\machine.json"))) { throw "config\machine.json is missing in $Repo" }

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m ripauto.agent --loop" -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Force `
    -Description "rip-automation agent: fetches 8-color sheets, batches and rips them in PO-TRY. Never prints. Pause with tools\pause.cmd." | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Installed '$TaskName' for $user and started it (state: $((Get-ScheduledTask -TaskName $TaskName).State), last result: $($info.LastTaskResult))."
Write-Host "Log: $Repo\state\agent.log    Dashboard: http://osshub.local:8085"
