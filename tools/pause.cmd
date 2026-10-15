@echo off
rem Pause the RIP agent: it finishes anything in progress, then starts nothing
rem new and shows "stopped" on the dashboard until tools\resume.cmd.
if not exist "%~dp0..\state" mkdir "%~dp0..\state"
type nul > "%~dp0..\state\PAUSE"
echo RIP agent paused. It finishes any run in progress, then waits.
echo Run tools\resume.cmd to carry on.
