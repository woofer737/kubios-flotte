@echo off
REM ==================================================================
REM  Arrete Kubios (sans retirer le demarrage automatique).
REM  Il redemarrera au prochain redemarrage de l'ordinateur,
REM  ou avec redemarrer.bat.
REM  Clic droit > Executer en tant qu'administrateur.
REM ==================================================================
net session >nul 2>&1 || (echo Ce script doit etre execute en tant qu'administrateur. & pause & exit /b 1)
powershell -NoProfile -Command "Stop-ScheduledTask -TaskName 'Kubios Flotte' -ErrorAction SilentlyContinue"
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and $_.CommandLine -like '*run.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Kubios est arrete.
pause
