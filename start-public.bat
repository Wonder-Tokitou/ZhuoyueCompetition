@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
python scripts\bootstrap.py public %*
if errorlevel 1 pause
