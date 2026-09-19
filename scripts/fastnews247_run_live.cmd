@echo off
setlocal
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
if not exist logs mkdir logs
python scripts\fastnews247_mvp.py --once --post >> logs\fastnews247_scheduler.log 2>&1
exit /b %ERRORLEVEL%
