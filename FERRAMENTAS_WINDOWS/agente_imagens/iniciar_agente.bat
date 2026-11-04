@echo off
rem Agente de imagens do Noromak (V20.17). O instalar.bat cria o atalho deste arquivo na pasta de inicializacao.
cd /d "%~dp0"
set "PY=python"
python -c "import sys" >nul 2>&1 || set "PY=py -3"
start "Agente de imagens Noromak" /min %PY% agente_imagens.py
