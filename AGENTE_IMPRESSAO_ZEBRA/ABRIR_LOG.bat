@echo off
set "LOG=%LOCALAPPDATA%\NoromakPrintAgent\agent.log"
if not exist "%LOG%" (
  echo O log ainda nao existe. Configure e inicie o agente primeiro.
  pause
  exit /b 1
)
notepad.exe "%LOG%"
