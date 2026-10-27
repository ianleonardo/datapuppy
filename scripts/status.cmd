@echo off
rem status.cmd - show whether the SQL Assistant is running
rem Pure batch: no PowerShell needed. The work is done by the app's own CLI (python -m app.cli status).
setlocal EnableExtensions
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "INSTALL_DIR=%%~fI"
if not defined SQLASSIST_CONFIG set "SQLASSIST_CONFIG=%INSTALL_DIR%\config\sqlassist.toml"
set "PY=%INSTALL_DIR%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo ERROR: Python venv not found at "%INSTALL_DIR%\.venv". Create it with: python -m venv .venv 1>&2
  exit /b 1
)
pushd "%INSTALL_DIR%\server"
"%PY%" -m app.cli status %*
set "RC=%ERRORLEVEL%"
popd
if /i not "%~1"=="--foreground" (
  echo %CMDCMDLINE% | find /i "/c" >nul && pause
)
exit /b %RC%
