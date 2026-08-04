@echo off
setlocal
taskkill /IM NoromakPrintAgent.exe /F >nul 2>&1
set "ATALHO=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Noromak Print Agent.lnk"
if exist "%ATALHO%" del /f /q "%ATALHO%"
set "DIR=%LOCALAPPDATA%\NoromakPrintAgent"
if exist "%DIR%" rmdir /s /q "%DIR%"
echo Noromak Print Agent removido deste usuario do Windows.
pause
