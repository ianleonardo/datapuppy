@echo off
rem docker-settings.cmd - shared settings for docker-build/start/stop.cmd (CALLed, not run directly).
rem Environment variables win over docker\docker.conf, which wins over the defaults below.
if "%~1"==":abs" goto :abs
for %%I in ("%~dp0..") do set "SQA_ROOT=%%~fI"
if not defined SQA_DOCKER_CONF set "SQA_DOCKER_CONF=%SQA_ROOT%\docker\docker.conf"
if exist "%SQA_DOCKER_CONF%" (
  for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SQA_DOCKER_CONF%") do (
    if not defined %%a set "%%a=%%b"
  )
)
if not defined IMAGE_NAME set "IMAGE_NAME=sqlassist"
if not defined CONTAINER_NAME set "CONTAINER_NAME=sqlassist"
if not defined IMAGE_DIR set "IMAGE_DIR=docker\images"
if not defined KEEP_IMAGES set "KEEP_IMAGES=3"
if not defined DATA_DIR set "DATA_DIR=data"
if not defined CONFIG_DIR set "CONFIG_DIR=config"
if not defined HOST_BIND set "HOST_BIND=127.0.0.1"
if not defined HOST_PORT set "HOST_PORT=8080"
if not defined RUN_AS set "RUN_AS=auto"
if not defined BASE_IMAGE set "BASE_IMAGE=python:3.12-slim-bookworm"
if not defined DRIVERS set "DRIVERS=oracledb"

where docker >nul 2>&1 || (echo ERROR: docker is not installed or not on PATH. 1>&2 & exit /b 1)
docker info >nul 2>&1 || (echo ERROR: cannot reach the Docker daemon. Start Docker Desktop and try again. 1>&2 & exit /b 1)
exit /b 0

rem ---------------------------------------------------------------------------------------------------------------
rem  call "%~dp0docker-settings.cmd" :abs VAR  -> makes %VAR% an absolute path (relative to the project folder)
:abs
setlocal EnableDelayedExpansion
set "p=!%~2!"
set "p=!p:/=\!"
if "!p:~1,1!"==":" (set "r=!p!") else if "!p:~0,2!"=="\\" (set "r=!p!") else (set "r=%SQA_ROOT%\!p!")
if not exist "!r!" mkdir "!r!" >nul 2>&1
for %%I in ("!r!") do set "r=%%~fI"
endlocal & set "%~2=%r%"
exit /b 0
