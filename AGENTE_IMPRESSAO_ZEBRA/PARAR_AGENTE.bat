@echo off
taskkill /IM NoromakPrintAgent.exe /F >nul 2>&1
if errorlevel 1 (echo O agente nao estava em execucao.) else (echo Agente encerrado.)
pause
