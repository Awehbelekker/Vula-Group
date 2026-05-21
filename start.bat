@echo off
REM Vula Group — Windows Startup Script
REM Run this to start all local services

echo.
echo  Vula Group AI — Starting services...
echo  ======================================

REM 1. Start Qdrant vector store
echo.
echo  [1/3] Starting Qdrant...
start "Qdrant" /min "%LOCALAPPDATA%\Programs\Qdrant\qdrant.exe" --config-path "%LOCALAPPDATA%\Programs\Qdrant\config.yaml"
timeout /t 3 /nobreak >nul

REM 2. Start Vula API server
echo  [2/3] Starting Vula API (port 7438)...
cd /d "%~dp0vula_mind"
start "Vula API" /min cmd /c "python -m uvicorn vula.api.server:app --host 0.0.0.0 --port 7438 --reload"
timeout /t 3 /nobreak >nul

REM 3. Start Dashboard dev server
echo  [3/3] Starting Dashboard (port 3000)...
cd /d "%~dp0vula_dashboard"
start "Vula Dashboard" /min cmd /c "npm run dev"
timeout /t 2 /nobreak >nul

echo.
echo  All services started!
echo.
echo  Dashboard:  http://localhost:3000
echo  API:        http://localhost:7438
echo  API Docs:   http://localhost:7438/docs  (set DEBUG=true in .env)
echo  Qdrant:     http://localhost:6333
echo.
echo  Ollama must be running separately (it usually auto-starts)
echo.
pause
