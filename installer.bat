@echo off
setlocal
REM ==================================================================
REM  Installe l'environnement Python et les composantes de Kubios.
REM  A refaire sur CHAQUE ordinateur : le dossier .venv ne se copie pas
REM  d'un ordinateur a l'autre (il pointe vers le Python du poste).
REM ==================================================================
cd /d "%~dp0"

REM --- 1. Trouver un vrai Python 3.10+ (pas le raccourci du Microsoft Store)
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
if not defined PY (
  echo.
  echo  Python 3.12 n'est pas installe sur cet ordinateur.
  echo  1. Telechargez-le : https://www.python.org/downloads/
  echo  2. Pendant l'installation, cochez "Add python.exe to PATH".
  echo  3. Relancez installer.bat.
  echo.
  pause
  exit /b 1
)
for /f "delims=" %%v in ('%PY% -c "import sys; print(sys.version.split()[0])"') do echo Python trouve : %%v

REM --- 2. Environnement .venv : on le refait s'il vient d'un autre ordinateur ou s'il est brise
if exist .venv (
  .venv\Scripts\python.exe -c "import sys" >nul 2>nul
  if errorlevel 1 (
    echo Le dossier .venv vient d'un autre ordinateur ou est endommage : il est recree.
    rmdir /s /q .venv
  )
)
if not exist .venv (
  echo Creation de l'environnement Python...
  %PY% -m venv .venv
  if errorlevel 1 (echo Echec de la creation de .venv & pause & exit /b 1)
)

REM --- 3. Composantes
echo Installation des composantes (1 a 3 minutes)...
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\python.exe -m pip install -r requirements.txt -q
if errorlevel 1 (echo Echec de l'installation des composantes : verifiez la connexion Internet. & pause & exit /b 1)

echo.
echo Installation terminee.
echo  - Pour tester : demarrer.bat
echo  - Pour le demarrage automatique : clic droit sur installer-service.bat, Executer en tant qu'administrateur
pause
