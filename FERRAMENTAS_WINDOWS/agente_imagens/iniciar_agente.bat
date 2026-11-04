@echo off
rem Agente de imagens do Noromak (V20.17). Para subir com o Windows: atalho deste arquivo em
rem shell:startup (Win+R, digite shell:startup, cole o atalho).
cd /d "%~dp0"
start "Agente de imagens Noromak" /min python agente_imagens.py
