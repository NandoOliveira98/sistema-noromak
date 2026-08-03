@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Configurar QZ Tray - Sistema de Conferencia 6.9

set "PASTA_TEMP=%TEMP%\NoromakQZSetupV69"
set "EXE_TEMP=%PASTA_TEMP%\SistemaConferenciaRedeV6.exe"
set "RESULTADO_TEMP=%PASTA_TEMP%\CONFIGURACAO_QZ_RESULTADO.txt"

if not exist "%PASTA_TEMP%" mkdir "%PASTA_TEMP%" >nul 2>&1
copy /y "%~dp0SistemaConferenciaRedeV6.exe" "%EXE_TEMP%" >nul
if errorlevel 1 (
  echo Nao foi possivel copiar o configurador para a pasta temporaria.
  pause
  exit /b 1
)
if exist "%RESULTADO_TEMP%" del /q "%RESULTADO_TEMP%" >nul 2>&1

echo ==============================================================
echo  CONFIGURACAO DO QZ TRAY - SISTEMA DE CONFERENCIA 6.9
echo ==============================================================
echo.
echo O Windows exibira uma solicitacao de permissao uma unica vez.
echo Clique em SIM. Essa permissao e necessaria para instalar a raiz
echo de confianca dentro da pasta do QZ Tray.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process -FilePath $env:EXE_TEMP -ArgumentList '--configurar-qz' -Verb RunAs -Wait -PassThru; exit $p.ExitCode"
set "RESULTADO=%ERRORLEVEL%"

if exist "%RESULTADO_TEMP%" copy /y "%RESULTADO_TEMP%" "%~dp0CONFIGURACAO_QZ_RESULTADO.txt" >nul

echo.
if "%RESULTADO%"=="0" (
  echo CONFIGURACAO CONCLUIDA.
  echo Feche todas as abas do sistema, aguarde o QZ Tray abrir e entre novamente.
) else (
  echo NAO FOI POSSIVEL CONCLUIR A CONFIGURACAO.
  echo Consulte o arquivo CONFIGURACAO_QZ_RESULTADO.txt nesta pasta.
)
echo.
pause
exit /b %RESULTADO%
