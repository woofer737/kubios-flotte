@echo off
REM Arrete et retire le demarrage automatique de Kubios (les donnees sont conservees).
net session >nul 2>&1 || (echo Executez en tant qu'administrateur. & pause & exit /b 1)
powershell -NoProfile -Command "Stop-ScheduledTask -TaskName 'Kubios Flotte' -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName 'Kubios Flotte' -Confirm:$false"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*run.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
netsh advfirewall firewall delete rule name="Kubios Flotte" >nul 2>&1
echo Service retire.
pause
