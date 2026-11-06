@echo off
REM Construit KubiosLicences.exe (dossier dist\). Necessite Python 3.12+.
cd /d "%~dp0"
if not exist .venv-build\Scripts\python.exe python -m venv .venv-build
.venv-build\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv-build\Scripts\python.exe -m pip install --quiet cryptography==50.0.2 pyinstaller
.venv-build\Scripts\pyinstaller.exe --noconfirm --onefile --windowed --name KubiosLicences kubios_licences.py
echo.
echo Termine : dist\KubiosLicences.exe
pause
