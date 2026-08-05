@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0CONFIGURAR_NOROMAK_PRINT_SERVICE.ps1"
if errorlevel 1 (
  echo.
  echo A configuracao encontrou um erro. Consulte CONFIGURACAO_RESULTADO.txt.
)
pause
