@echo off
setlocal
cd /d "%~dp0"
title Flow Local - Trava de creditos ativa
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1" -NoSpend
if errorlevel 1 pause
endlocal
