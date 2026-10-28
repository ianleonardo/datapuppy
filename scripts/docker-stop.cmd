@echo off
rem docker-stop.cmd - stop the AI SQL Assistant container gracefully (DuckDB checkpoint; forced after 40 s).
rem Usage: docker-stop.cmd [--remove]     --remove also deletes the container (data and config stay on the host)
rem Exit codes: 0 stopped, 1 failed, 2 not running.
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0docker-settings.cmd" || goto :fail
set "REMOVE=0"
if /i "%~1"=="--remove" set "REMOVE=1"

docker container inspect %CONTAINER_NAME% >nul 2>&1
if errorlevel 1 (
  echo SQL Assistant container '%CONTAINER_NAME%' does not exist.
  call :pausemaybe
  exit /b 2
)
set "RC=2"
for /f "delims=" %%i in ('docker container inspect -f "{{.State.Running}}" %CONTAINER_NAME%') do set "RUNNING=%%i"
if "!RUNNING!"=="true" (
  echo Stopping SQL Assistant container '%CONTAINER_NAME%' ^(up to 40 s^)...
  docker stop -t 40 %CONTAINER_NAME% >nul || goto :fail
  for /f "delims=" %%c in ('docker container inspect -f "{{.State.ExitCode}}" %CONTAINER_NAME%') do (
    if "%%c"=="137" echo WARNING: had to force-stop; DuckDB will replay its WAL on next start. 1>&2
  )
  echo Stopped.
  set "RC=0"
) else (
  echo SQL Assistant container '%CONTAINER_NAME%' is not running.
)
if "%REMOVE%"=="1" (
  docker rm %CONTAINER_NAME% >nul && echo Container removed.
)
call :pausemaybe
exit /b %RC%

:pausemaybe
echo %CMDCMDLINE% | find /i "/c" >nul && pause
exit /b 0

:fail
call :pausemaybe
exit /b 1
