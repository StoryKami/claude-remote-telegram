@echo off
cd /d "%~dp0"
taskkill /F /IM python.exe 2>nul
timeout /t 1 /nobreak >nul
.venv\Scripts\python.exe -m src.main
