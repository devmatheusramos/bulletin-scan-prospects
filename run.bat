@echo off
cd /d "%~dp0"
echo ============================================
echo  Bulletin Scan — Servidor Web
echo ============================================
echo.
echo Subindo servidor em http://localhost:8000 ...
docker compose up --build
pause
