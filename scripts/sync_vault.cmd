@echo off
setlocal

cd /d "%~dp0.."
set "PROJECT_ROOT=%CD%"

set "LOG_DIR=%PROJECT_ROOT%\data\logs"
set "LOG_FILE=%LOG_DIR%\vault-sync.log"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo.>> "%LOG_FILE%"
echo [%date% %time%] Vault synchronization started.>> "%LOG_FILE%"

cd /d "%PROJECT_ROOT%"
uv run python -m scripts.ingest_vault >> "%LOG_FILE%" 2>&1

set "SYNC_EXIT_CODE=%ERRORLEVEL%"
echo [%date% %time%] Vault synchronization finished with exit code %SYNC_EXIT_CODE%.>> "%LOG_FILE%"

exit /b %SYNC_EXIT_CODE%
