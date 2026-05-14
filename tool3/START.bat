@echo off
title Instagram Date Finder
echo.
echo  ==========================================
echo   Instagram Date Finder - Starting...
echo  ==========================================
echo.

:: Kill any old instance on port 5001
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":5001 "') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Start the server (it will print the IP addresses)
python "%~dp0app.py"
pause
