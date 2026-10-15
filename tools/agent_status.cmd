@echo off
rem Is the RIP agent running, paused? Shows the scheduled task and the last log lines.
cd /d "%~dp0.."
schtasks /query /tn "OSS RIP Agent" /fo list 2>nul | findstr /i "Status Last"
if exist state\PAUSE (echo Pause switch: ON  ^(tools\resume.cmd to carry on^)) else (echo Pause switch: off)
echo.
echo --- last lines of state\agent.log ---
powershell -NoProfile -Command "if (Test-Path state\agent.log) { Get-Content state\agent.log -Tail 15 } else { 'no log yet' }"
