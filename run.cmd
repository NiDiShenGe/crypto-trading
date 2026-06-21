@echo off
cd /d "%~dp0"

if not exist ".env" (
    echo Missing .env configuration file.
    echo Creating .env from .env.example...
    copy /Y ".env.example" ".env" >nul
    echo Please edit .env and fill in your QQ email authorization code.
    notepad ".env"
    exit /b 1
)

set "PYTHONPATH=%CD%\src"
if "%~1"=="" (
    python -m crypto_trader run
) else (
    python -m crypto_trader %1
)

if errorlevel 1 (
    echo.
    echo Startup failed. Review the error above.
    exit /b 1
)
