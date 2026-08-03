@echo off
setlocal
cd /d "%~dp0"
"%~dp0SistemaConferenciaRedeV6.exe" --encerrar >nul 2>&1
taskkill /F /IM SistemaConferenciaRedeV6.exe >nul 2>&1
taskkill /F /IM SistemaConferenciaRedeV5.exe >nul 2>&1
taskkill /F /IM SistemaConferencia.exe >nul 2>&1
echo Versoes do Sistema de Conferencia encerradas neste computador.
timeout /t 2 /nobreak >nul
endlocal
