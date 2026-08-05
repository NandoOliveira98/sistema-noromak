@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Configurar QZ Tray - Expedicao Web 9.2

echo ==============================================================
echo  CONFIGURAR QZ TRAY - SISTEMA DE CONFERENCIA WEB 9.2
echo ==============================================================
echo.
echo Esta configuracao e feita apenas UMA VEZ no computador da Zebra.
echo Ela autoriza a impressao silenciosa para todos os usuarios e verifica se o QZ ficou ativo.
echo.
echo Quando o Windows pedir permissao de administrador, clique em SIM.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$arg='-NoProfile -ExecutionPolicy Bypass -File ""%~dp0CONFIGURAR_QZ_TRAY_WEB.ps1"" -Modo Sistema'; $p=Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arg; exit $p.ExitCode"
if errorlevel 1 goto ERRO

echo.
echo Autorizando o certificado para o usuario atual...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0CONFIGURAR_QZ_TRAY_WEB.ps1" -Modo Usuario
if errorlevel 1 goto ERRO

echo.
echo CONFIGURACAO CONCLUIDA COM SUCESSO.
echo Feche todas as abas da Expedicao e abra novamente.
echo.
pause
exit /b 0

:ERRO
echo.
echo NAO FOI POSSIVEL CONCLUIR.
echo Envie o arquivo CONFIGURACAO_QZ_RESULTADO.txt desta pasta.
echo.
pause
exit /b 1
