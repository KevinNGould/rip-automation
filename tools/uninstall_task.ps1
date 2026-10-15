# uninstall_task.ps1  -  stop the RIP agent and remove its scheduled task.
# Run from an ADMINISTRATOR PowerShell:
#   powershell -ExecutionPolicy Bypass -File tools\uninstall_task.ps1
# A run in progress is cut off; pause first (tools\pause.cmd) and wait for
# "stopped" on the dashboard if a rip might be going.

$ErrorActionPreference = "Stop"
$TaskName = "OSS RIP Agent"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed '$TaskName'."
} else {
    Write-Host "'$TaskName' isn't installed."
}
