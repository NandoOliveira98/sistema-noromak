@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Configurar QZ Silencioso - Expedicao Web 9.3

echo ==============================================================
echo  QZ TRAY SILENCIOSO - NOROMAK 9.3
echo ==============================================================
echo.
echo Esta configuracao e feita UMA VEZ no computador conectado a Zebra.
echo O QZ Tray sera executado sem interface, portanto nao exibira Allow.
echo Quando o Windows pedir permissao de administrador, clique em SIM.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$arg='-NoProfile -ExecutionPolicy Bypass -File ""%~dp0CONFIGURAR_QZ_TRAY_WEB.ps1"" -Modo Sistema'; $p=Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arg; exit $p.ExitCode"
if errorlevel 1 goto ERRO

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0CONFIGURAR_QZ_TRAY_WEB.ps1" -Modo Usuario
if errorlevel 1 goto ERRO

echo.
echo ==============================================================
echo  CONFIGURACAO CONCLUIDA
echo ==============================================================
echo O QZ Tray agora funciona em modo silencioso e a Estacao Zebra foi aberta.
echo Nao sera necessario clicar em Allow durante as impressoes pelo celular.
echo.
pause
exit /b 0

:ERRO
echo.
echo NAO FOI POSSIVEL CONCLUIR.
echo Envie CONFIGURACAO_QZ_RESULTADO.txt e o arquivo
echo %%LOCALAPPDATA%%\NoromakQZHeadless\qz-headless.log.
echo.
pause
exit /b 1
