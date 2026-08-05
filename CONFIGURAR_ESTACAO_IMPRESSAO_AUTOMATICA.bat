@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Configurar Estacao Zebra Automatica - Noromak

echo ==============================================================
echo  ESTACAO ZEBRA AUTOMATICA - NOROMAK 9.2
echo ==============================================================
echo.
echo Esta configuracao e feita somente UMA VEZ no computador da Zebra.
echo Depois, as etiquetas enviadas pelo celular serao impressas sem Allow
echo e sem clicar em nenhum botao neste computador.
echo.
echo Quando o Windows pedir permissao de administrador, clique em SIM.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$arg='-NoProfile -ExecutionPolicy Bypass -File ""%~dp0CONFIGURAR_QZ_TRAY_WEB.ps1"" -Modo Sistema'; $p=Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arg; exit $p.ExitCode"
if errorlevel 1 goto ERRO

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0CONFIGURAR_QZ_TRAY_WEB.ps1" -Modo Usuario
if errorlevel 1 goto ERRO

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0CONFIGURAR_ESTACAO_AUTOMATICA.ps1"
if errorlevel 1 goto ERRO

echo.
echo ==============================================================
echo  CONFIGURACAO CONCLUIDA
echo ==============================================================
echo.
echo A Estacao Zebra foi aberta.
echo Entre UMA VEZ com um usuario do estoque e confirme que os dois status
echo ficaram verdes. A partir disso, nao sera necessario usar o computador
 echo durante as impressoes feitas pelo celular.
echo.
pause
exit /b 0

:ERRO
echo.
echo NAO FOI POSSIVEL CONCLUIR A CONFIGURACAO.
echo Envie os arquivos CONFIGURACAO_QZ_RESULTADO.txt e
echo CONFIGURACAO_ESTACAO_RESULTADO.txt desta pasta.
echo.
pause
exit /b 1
