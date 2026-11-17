@echo off
rem Starts the app at http://127.0.0.1:8000 (run setup.bat once first).
cd /d "%~dp0"
if not exist backend\.venv\Scripts\python.exe (echo Run setup.bat first. & pause & exit /b 1)
start "" http://127.0.0.1:8000
backend\.venv\Scripts\python.exe -X utf8 -m uvicorn nsm.api.app:app --app-dir backend --host 127.0.0.1 --port 8000
pause
