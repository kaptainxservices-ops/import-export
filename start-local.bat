@echo off
rem Starts the backend and the dashboard locally, each in its own window.
start "Backend - import-export" cmd /k "cd /d %~dp0 && call .venv\Scripts\activate.bat && uvicorn app.main:app --reload"
start "Frontend - import-export" cmd /k "cd /d %~dp0frontend && (if not exist node_modules npm install) && npm run dev"
