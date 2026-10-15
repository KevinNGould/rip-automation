@echo off
rem Resume the RIP agent after tools\pause.cmd.
if exist "%~dp0..\state\PAUSE" del "%~dp0..\state\PAUSE"
echo RIP agent resumed (within about 10 seconds).
