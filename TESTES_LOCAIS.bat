@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".runtime\tools\gflow-cli\Scripts\python.exe" (
  echo Abra INICIAR.bat uma vez para instalar o programa primeiro.
  pause
  exit /b 1
)
echo Testes locais sem Google e sem gasto de creditos.
".runtime\tools\gflow-cli\Scripts\python.exe" -m unittest discover -s tests -v
if errorlevel 1 goto failed
".runtime\tools\gflow-cli\Scripts\python.exe" tests\validar_contrato_cli.py
if errorlevel 1 goto failed
echo.
echo Testes locais aprovados. O aceite real no Flow continua separado.
pause
exit /b 0
:failed
echo.
echo Houve falha nos testes. Consulte a mensagem acima.
pause
exit /b 1
