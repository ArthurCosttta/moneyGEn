@echo off
setlocal
cd /d "%~dp0"
title Flow Local - Playwright fixture sem creditos
where node.exe >nul 2>nul || (echo Node.js de QA nao encontrado.& pause & exit /b 1)
where python.exe >nul 2>nul || (echo Python de QA nao encontrado.& pause & exit /b 1)
if not exist node_modules\@playwright\test (call npm install --ignore-scripts || goto failed)
python scripts\run_e2e.py
if errorlevel 1 goto failed
echo 30 testes Playwright aprovados. Provider: fixture. Nenhum credito usado.
pause
exit /b 0
:failed
echo Falha no teste de interface. Consulte qa\playwright-report.json.
pause
exit /b 1
