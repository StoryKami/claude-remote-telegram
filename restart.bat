@echo off
cd /d "%~dp0"
title claude-remote-telegram
if exist .pid (
    set /p PID=<.pid
    taskkill /F /PID %PID% 2>nul
    del .pid
)
timeout /t 1 /nobreak >nul
.venv\Scripts\python.exe -c "import os; open('.pid','w').write(str(os.getpid()))"
.venv\Scripts\python.exe -m src.main
pause
