@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "PORT=8001"
set /a CLEARED=0

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% "') do (
    taskkill /PID %%P /F >nul 2>&1
    if not errorlevel 1 set /a CLEARED+=1
)

if %CLEARED% GTR 0 (
    echo Cleared %CLEARED% process^(es^) on port %PORT%
)

uvicorn app.main:app --host 0.0.0.0 --port %PORT% --reload
