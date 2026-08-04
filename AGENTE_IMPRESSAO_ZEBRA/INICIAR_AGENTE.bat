@echo off
set "DIR=%LOCALAPPDATA%\NoromakPrintAgent"
if not exist "%DIR%\NoromakPrintAgent.exe" (
  echo O agente ainda nao foi configurado. Execute CONFIGURAR_AGENTE_IMPRESSAO.bat.
  pause
  exit /b 1
)
start "" "%DIR%\NoromakPrintAgent.exe"
echo Agente iniciado.
timeout /t 2 >nul
