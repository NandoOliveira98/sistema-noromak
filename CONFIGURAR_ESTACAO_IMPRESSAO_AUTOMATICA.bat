@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Estacao Zebra Silenciosa - Noromak 9.3
call "%~dp0CONFIGURAR_QZ_TRAY_WEB.bat"
exit /b %errorlevel%
