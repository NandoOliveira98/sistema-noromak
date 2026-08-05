@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-Process NoromakPrintService -ErrorAction SilentlyContinue ^| Stop-Process -Force; Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'NoromakPrintService' -ErrorAction SilentlyContinue; Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'NoromakPrintStation' -ErrorAction SilentlyContinue; Remove-Item -Recurse -Force (Join-Path $env:LOCALAPPDATA 'NoromakPrintService') -ErrorAction SilentlyContinue"
echo Noromak Print Service removido deste usuario do Windows.
pause
