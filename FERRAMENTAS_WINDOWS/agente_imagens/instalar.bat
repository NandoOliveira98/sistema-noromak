@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Instalar o Agente de imagens do Noromak
echo.
echo ===== Agente de imagens do Noromak - instalacao =====
echo.

rem 0. Revisao 04/11: instala numa pasta so deste usuario do Windows (a chave da API fica la, fora de Downloads e
rem    de pastas compartilhadas). Rodado de outro lugar, copia tudo para la e continua de la.
set "DESTINO=%LOCALAPPDATA%\AgenteImagensNoromak"
if /i not "%~dp0"=="%DESTINO%\" (
  echo [0/5] Copiando o agente para %DESTINO% ...
  robocopy "%~dp0." "%DESTINO%" /E /XF config.env agente.log tunel.log agente.lock /NFL /NDL /NJH /NJS /NP >nul
  if errorlevel 8 (
    echo Nao foi possivel copiar para %DESTINO%.
    pause
    exit /b 1
  )
  cd /d "%DESTINO%"
  call "%DESTINO%\instalar.bat"
  exit /b
)

rem 1. Python 3.10 ou mais novo
set "PY="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  echo [1/5] Python nao encontrado. Instalando o Python 3.12 pelo winget, o instalador oficial do Windows...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  echo.
  echo Python instalado. FECHE esta janela e rode o instalar.bat de novo.
  pause
  exit /b 1
)
echo [1/5] Python OK

rem 2. Bibliotecas
echo [2/5] Instalando as bibliotecas (Pillow e anthropic)...
%PY% -m pip install --user --upgrade --quiet pillow anthropic
if errorlevel 1 (
  echo Nao foi possivel instalar as bibliotecas. Confira a internet e rode de novo.
  pause
  exit /b 1
)

rem 3. config.env (so o usuario atual do Windows le e altera: tem a chave da API)
if not exist config.env copy config.env.exemplo config.env >nul
icacls config.env /inheritance:r /grant:r "%USERNAME%:F" >nul 2>&1
echo [3/5] Vai abrir o config.env no Bloco de Notas:
echo        cole a chave da API em ANTHROPIC_API_KEY e o token em NOROMAK_AGENTE_TOKEN, salve e feche.
notepad config.env

rem 4. Atalho para subir com o Windows
choice /c SN /m "[4/5] Ligar o agente sozinho quando o Windows iniciar"
if errorlevel 2 goto teste
powershell -NoProfile -Command "$a=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Agente de imagens Noromak.lnk'); $a.TargetPath='%~dp0iniciar_agente.bat'; $a.WorkingDirectory='%~dp0'; $a.WindowStyle=7; $a.Save()"
echo        Atalho criado.

:teste
echo [5/5] Conferindo a instalacao...
%PY% agente_imagens.py --testar
echo.
echo Instalado em %DESTINO%. Pode apagar a pasta extraida do zip.
echo Para ligar agora: de dois cliques em iniciar_agente.bat (nessa pasta). So deixe UM agente ligado por vez na empresa.
start "" explorer "%DESTINO%"
pause
