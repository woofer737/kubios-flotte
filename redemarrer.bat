@echo off
REM ==================================================================
REM  Redemarre Kubios (arrete puis relance la tache "Kubios Flotte").
REM  Clic droit > Executer en tant qu'administrateur.
REM ==================================================================
net session >nul 2>&1 || (echo Ce script doit etre execute en tant qu'administrateur. & pause & exit /b 1)
powershell -NoProfile -Command "Stop-ScheduledTask -TaskName 'Kubios Flotte' -ErrorAction SilentlyContinue"
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and $_.CommandLine -like '*run.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
timeout /t 2 /nobreak >nul
powershell -NoProfile -Command "Start-ScheduledTask -TaskName 'Kubios Flotte'"
echo Kubios redemarre. Ouvrez http://localhost:8080 dans environ 10 secondes.
pause
