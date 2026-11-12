@echo off
REM Lance Kubios manuellement (fenetre visible). Ouvrez ensuite http://localhost:8080
cd /d "%~dp0"
.venv\Scripts\python.exe -c "import sys" >nul 2>nul
if errorlevel 1 (
  echo L'environnement Python n'est pas installe sur cet ordinateur.
  echo Lancez d'abord installer.bat.
  pause
  exit /b 1
)
.venv\Scripts\python.exe run.py
