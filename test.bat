@echo off
rem Runs the backend tests and the evaluation against the keyword and BM25 baselines.
cd /d "%~dp0"
backend\.venv\Scripts\python.exe -X utf8 -m pytest -q backend\tests
backend\.venv\Scripts\python.exe -X utf8 scripts\evaluate.py
pause
