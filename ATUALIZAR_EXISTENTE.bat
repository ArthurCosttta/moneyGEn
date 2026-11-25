@echo off
setlocal
cd /d "%~dp0"
title Flow Local - Atualizar preservando o login
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0atualizar.ps1"
if errorlevel 1 pause
endlocal
