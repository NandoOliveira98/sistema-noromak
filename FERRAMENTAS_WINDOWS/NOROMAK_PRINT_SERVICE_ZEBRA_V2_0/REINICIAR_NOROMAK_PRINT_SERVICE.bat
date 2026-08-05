@echo off
set "BASE=%LOCALAPPDATA%\NoromakPrintService"
taskkill /IM NoromakPrintService.exe /F >nul 2>nul
timeout /t 1 /nobreak >nul
start "" "%BASE%\NoromakPrintService.exe"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:18181/"
