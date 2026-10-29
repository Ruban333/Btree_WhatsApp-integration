@echo off
cd /d %~dp0backend
if not exist ..\.env copy ..\.env.example ..\.env >nul
rem Free port 8000 if an old copy of this server is still running
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
echo Starting on http://localhost:8000  (Ctrl+C to stop)
..\.venv\Scripts\python -m uvicorn main:app --host 0.0.0.0 --port 8000
