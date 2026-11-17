@echo off
rem One-time setup: installs the backend and the dashboard, then loads the synthetic demo data.
rem Needs Python 3.13 + uv, and Node.js 20+ (see README "Prerequisites").
cd /d "%~dp0"
where uv >nul 2>nul || (echo uv is not installed. Install it from https://docs.astral.sh/uv/ and run this again. & pause & exit /b 1)
where npm >nul 2>nul || (echo Node.js / npm is not installed. Install Node.js 20+ and run this again. & pause & exit /b 1)
echo [1/4] Installing the backend...
pushd backend && uv sync --python 3.13 || (popd & echo Backend install failed. & pause & exit /b 1)
popd
echo [2/4] Installing the dashboard...
call npm --prefix frontend install || (echo Dashboard install failed. & pause & exit /b 1)
echo [3/4] Building the dashboard...
call npm --prefix frontend run build || (echo Dashboard build failed. & pause & exit /b 1)
if not exist .env copy .env.example .env >nul
echo [4/4] Loading the demo data (65 CVs, 7 jobs)...
backend\.venv\Scripts\python.exe -X utf8 scripts\seed_demo.py --reset || (echo Demo data failed. & pause & exit /b 1)
echo.
echo Setup done. Double-click run.bat to start the app.
pause
