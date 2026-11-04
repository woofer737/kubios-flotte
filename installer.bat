@echo off
REM Installe l'environnement Python et les dependances du logiciel Kubios.
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.12+ est requis : https://www.python.org/downloads/ & pause & exit /b 1)
if not exist .venv python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\python.exe -m pip install -r requirements.txt -q
echo.
echo Installation terminee. Lancez demarrer.bat pour tester, ou installer-service.bat (en administrateur) pour le demarrage automatique.
pause
