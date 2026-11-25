@echo off
setlocal
cd /d "%~dp0"
title Flow Local - Preparando o aplicativo
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1"
if errorlevel 1 (
  echo.
  echo Nao foi possivel iniciar. Consulte dados\instalacao.log e LEIA-ME.md.
  pause
)
endlocal
