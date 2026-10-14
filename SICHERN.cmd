@echo off
setlocal
cd /d "%~dp0"
"%~dp0Laufzeit\python.exe" "%~dp0Programm\portable\launcher.py" backup
if errorlevel 1 echo Vorgang nicht abgeschlossen. Bitte Fehlermeldung oben beachten.
pause
