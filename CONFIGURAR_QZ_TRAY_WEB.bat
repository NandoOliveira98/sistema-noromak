@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Configurar QZ Tray - Expedicao Web 7.1

echo ==============================================================
echo  CONFIGURAR QZ TRAY - SISTEMA DE CONFERENCIA WEB 7.1
echo ==============================================================
echo.
echo Esta configuracao precisa ser feita apenas UMA VEZ em cada computador.
echo O Windows pedira permissao de administrador. Clique em SIM.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File ""%~dp0CONFIGURAR_QZ_TRAY_WEB.ps1""'; exit $p.ExitCode"
set "RESULTADO=%ERRORLEVEL%"

echo.
if "%RESULTADO%"=="0" (
  echo CONFIGURACAO CONCLUIDA.
  echo Feche as abas da Expedicao e abra novamente pelo NoromakWeb.
) else (
  echo NAO FOI POSSIVEL CONCLUIR.
  echo Consulte CONFIGURACAO_QZ_RESULTADO.txt nesta pasta.
)
echo.
pause
exit /b %RESULTADO%
