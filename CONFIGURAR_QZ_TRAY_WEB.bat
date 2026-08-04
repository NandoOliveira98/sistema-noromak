@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Configurar QZ Tray - Expedicao Web 7.7

echo ==============================================================
echo  CONFIGURAR QZ TRAY - SISTEMA DE CONFERENCIA WEB 7.7
echo ==============================================================
echo.
echo Esta configuracao e feita apenas UMA VEZ para cada usuario do Windows.
echo Primeiro sera instalada a raiz do certificado no computador.
echo Depois o certificado sera autorizado no perfil do usuario que esta logado.
echo.
echo Quando o Windows pedir permissao de administrador, clique em SIM.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$arg='-NoProfile -ExecutionPolicy Bypass -File ""%~dp0CONFIGURAR_QZ_TRAY_WEB.ps1"" -Modo Sistema'; $p=Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arg; exit $p.ExitCode"
if errorlevel 1 goto ERRO

echo.
echo Autorizando a impressao silenciosa para o usuario atual...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0CONFIGURAR_QZ_TRAY_WEB.ps1" -Modo Usuario
if errorlevel 1 goto ERRO

echo.
echo CONFIGURACAO CONCLUIDA.
echo Feche as abas da Expedicao e abra novamente pelo NoromakWeb.
echo A tela Allow do QZ Tray nao deve mais aparecer neste usuario.
echo.
pause
exit /b 0

:ERRO
echo.
echo NAO FOI POSSIVEL CONCLUIR.
echo Consulte CONFIGURACAO_QZ_RESULTADO.txt nesta pasta.
echo.
pause
exit /b 1
