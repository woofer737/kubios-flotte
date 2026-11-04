@echo off
REM ==================================================================
REM  Installe Kubios comme service de demarrage automatique Windows.
REM  A executer UNE FOIS en tant qu'administrateur (clic droit >
REM  Executer en tant qu'administrateur).
REM  Le logiciel demarre avec l'ordinateur (meme sans session ouverte),
REM  sous le compte SYSTEM, et se relance tout seul en cas de panne.
REM ==================================================================
cd /d "%~dp0"
net session >nul 2>&1 || (echo Ce script doit etre execute en tant qu'administrateur. & pause & exit /b 1)
if not exist .venv\Scripts\pythonw.exe (echo Lancez d'abord installer.bat & pause & exit /b 1)

set TASK=Kubios Flotte
set APPDIR=%~dp0
set APPDIR=%APPDIR:~0,-1%

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$a = New-ScheduledTaskAction -Execute '%APPDIR%\.venv\Scripts\pythonw.exe' -Argument ('\"' + '%APPDIR%\run.py' + '\"') -WorkingDirectory '%APPDIR%';" ^
  "$t = New-ScheduledTaskTrigger -AtStartup;" ^
  "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero);" ^
  "$p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest;" ^
  "Register-ScheduledTask -TaskName '%TASK%' -Action $a -Trigger $t -Settings $s -Principal $p -Force | Out-Null;" ^
  "Start-ScheduledTask -TaskName '%TASK%'"

REM Ouvre le port 8080 dans le pare-feu pour les autres postes du reseau
netsh advfirewall firewall delete rule name="Kubios Flotte" >nul 2>&1
netsh advfirewall firewall add rule name="Kubios Flotte" dir=in action=allow protocol=TCP localport=8080 >nul

echo.
echo Service "%TASK%" installe et demarre.
echo Acces : http://localhost:8080  (ou http://%COMPUTERNAME%:8080 depuis un autre poste)
echo Journal : %APPDIR%\data\kubios.log
pause
