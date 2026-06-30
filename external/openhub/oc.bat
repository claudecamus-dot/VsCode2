@echo off
REM Windows wrapper to run openhub's oc.sh using bash (Git Bash or WSL)
SETLOCAL
SET SCRIPT_DIR=%~dp0
bash "%SCRIPT_DIR%oc.sh" %*
ENDLOCAL
