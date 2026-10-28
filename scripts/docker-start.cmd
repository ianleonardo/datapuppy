@echo off
rem docker-start.cmd - start the AI SQL Assistant container (pure batch, no PowerShell).
rem  1. Looks in IMAGE_DIR for the newest image file (<name>-<stamp>-linux-amd64.tar/.tar.gz). If it is newer than the
rem     image Docker has (or Docker has none), loads it.
rem  2. If the container was created from an older image, replaces it (data and config are on the host).
rem  3. Mounts DATA_DIR (read-write) at /data and CONFIG_DIR at /app/config, then waits until healthy.
rem Usage: docker-start.cmd [--data-dir DIR] [--config-dir DIR] [--port N] [--bind ADDR] [--image-file FILE]
rem                         [--no-load] [--recreate]
rem Settings: docker\docker.conf. Exit codes: 0 OK, 1 failed, 2 already running.
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0docker-settings.cmd" || goto :fail

set "IMAGE_FILE=" & set "NO_LOAD=0" & set "RECREATE=0"
:args
if "%~1"=="" goto :argsdone
if /i "%~1"=="--data-dir" (set "DATA_DIR=%~2" & shift & shift & goto :args)
if /i "%~1"=="--config-dir" (set "CONFIG_DIR=%~2" & shift & shift & goto :args)
if /i "%~1"=="--port" (set "HOST_PORT=%~2" & shift & shift & goto :args)
if /i "%~1"=="--bind" (set "HOST_BIND=%~2" & shift & shift & goto :args)
if /i "%~1"=="--image-file" (set "IMAGE_FILE=%~f2" & shift & shift & goto :args)
if /i "%~1"=="--no-load" (set "NO_LOAD=1" & shift & goto :args)
if /i "%~1"=="--recreate" (set "RECREATE=1" & shift & goto :args)
echo Unknown option: %~1 1>&2
goto :fail
:argsdone
set "IMAGE=%IMAGE_NAME%:latest"

rem ---- 1) newer image file? ----------------------------------------------------------------------------------------
if "%NO_LOAD%"=="0" (
  call :stampof "%IMAGE%" CUR_STAMP
  if not defined IMAGE_FILE (
    call "%~dp0docker-settings.cmd" :abs IMAGE_DIR
    for /f "delims=" %%f in ('dir /b /a-d /o-n "!IMAGE_DIR!\%IMAGE_NAME%-*-linux-amd64.tar*" 2^>nul ^| findstr /v /i "\.part$"') do (
      if not defined IMAGE_FILE set "IMAGE_FILE=!IMAGE_DIR!\%%f"
    )
  )
  if defined IMAGE_FILE (
    if not exist "!IMAGE_FILE!" (echo ERROR: image file not found: !IMAGE_FILE! 1>&2 & goto :fail)
    for %%f in ("!IMAGE_FILE!") do set "FILE_STAMP=%%~nxf"
    set "FILE_STAMP=!FILE_STAMP:%IMAGE_NAME%-=!"
    set "FILE_STAMP=!FILE_STAMP:-linux-amd64.tar.gz=!"
    set "FILE_STAMP=!FILE_STAMP:-linux-amd64.tar=!"
    set "DOLOAD=0"
    if not defined CUR_STAMP set "DOLOAD=1"
    if defined CUR_STAMP if "!FILE_STAMP!" GTR "!CUR_STAMP!" set "DOLOAD=1"
    if "!DOLOAD!"=="1" (
      if defined CUR_STAMP (set "HAVE=!CUR_STAMP!") else (set "HAVE=none")
      echo ==^> Loading image file !IMAGE_FILE! ^(build !FILE_STAMP!; Docker has !HAVE!^)
      docker load -i "!IMAGE_FILE!" || goto :fail
      call :stampof "%IMAGE%" NEW_STAMP
      if not "!NEW_STAMP!"=="!FILE_STAMP!" docker tag %IMAGE_NAME%:!FILE_STAMP! %IMAGE% >nul 2>&1
    ) else (
      echo Image is up to date ^(build !CUR_STAMP!; newest file is !FILE_STAMP!^).
    )
  )
)
docker image inspect %IMAGE% >nul 2>&1 || (
  echo ERROR: no %IMAGE% image in Docker and no image file in %IMAGE_DIR%. Run scripts\docker-build.cmd first, 1>&2
  echo        or copy an image file ^(%IMAGE_NAME%-^<stamp^>-linux-amd64.tar^) into %IMAGE_DIR%. 1>&2
  goto :fail
)
for /f "delims=" %%i in ('docker image inspect -f "{{.Id}}" %IMAGE%') do set "IMG_ID=%%i"
call :stampof "%IMAGE%" CUR_STAMP
echo Using %IMAGE% ^(build !CUR_STAMP!^)

rem ---- 2) host folders ---------------------------------------------------------------------------------------------
call "%~dp0docker-settings.cmd" :abs DATA_DIR
call "%~dp0docker-settings.cmd" :abs CONFIG_DIR
if not exist "%CONFIG_DIR%\sqlassist.toml" if exist "%SQA_ROOT%\config\sqlassist.example.toml" (
  copy /y "%SQA_ROOT%\config\sqlassist.example.toml" "%CONFIG_DIR%\sqlassist.toml" >nul
  echo NOTE: created %CONFIG_DIR%\sqlassist.toml from the example. Review it ^(LLM endpoints, TLS / allow_insecure_http^).
)
set "USER_ARG="
if /i not "%RUN_AS%"=="auto" if /i not "%RUN_AS%"=="root" set "USER_ARG=--user %RUN_AS%"
set "TZ_ARG="
if defined TZ set "TZ_ARG=-e TZ=%TZ%"

rem ---- 3) container -------------------------------------------------------------------------------------------------
docker container inspect %CONTAINER_NAME% >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%i in ('docker container inspect -f "{{.Image}}" %CONTAINER_NAME%') do set "C_IMG=%%i"
  for /f "delims=" %%i in ('docker container inspect -f "{{.State.Running}}" %CONTAINER_NAME%') do set "RUNNING=%%i"
  if "!C_IMG!"=="!IMG_ID!" if "%RECREATE%"=="0" (
    if "!RUNNING!"=="true" (
      echo SQL Assistant container '%CONTAINER_NAME%' is already running.
      call :pausemaybe
      exit /b 2
    )
    echo ==^> Starting existing container %CONTAINER_NAME%
    docker start %CONTAINER_NAME% >nul || goto :fail
    goto :wait
  )
  if "%RECREATE%"=="1" (echo ==^> Re-creating container %CONTAINER_NAME%) else (echo ==^> Image changed: replacing container %CONTAINER_NAME% ^(data and config stay on the host^))
  if "!RUNNING!"=="true" docker stop -t 40 %CONTAINER_NAME% >nul
  docker rm %CONTAINER_NAME% >nul || goto :fail
)
echo ==^> Creating container %CONTAINER_NAME%
docker run -d --name %CONTAINER_NAME% --init --restart unless-stopped --platform linux/amd64 --stop-timeout 40 ^
  -p %HOST_BIND%:%HOST_PORT%:8080 -v "%DATA_DIR%:/data" -v "%CONFIG_DIR%:/app/config" ^
  --label org.sqlassist.role=app %USER_ARG% %TZ_ARG% %IMAGE% >nul || goto :fail

rem ---- 4) wait for /healthz -----------------------------------------------------------------------------------------
:wait
<nul set /p "=Waiting for SQL Assistant"
for /l %%n in (1,1,90) do (
  set "CSTATE=" & set "RESTARTS="
  for /f "tokens=1,2" %%i in ('docker container inspect -f "{{.State.Status}} {{.RestartCount}}" %CONTAINER_NAME%') do (set "CSTATE=%%i" & set "RESTARTS=%%j")
  set "BAD=0"
  if not "!CSTATE!"=="running" set "BAD=1"
  if not "!RESTARTS!"=="0" set "BAD=1"
  if "!BAD!"=="1" (
    echo.
    echo ERROR: the container stopped during startup. Last log lines: 1>&2
    docker logs --tail 25 %CONTAINER_NAME%
    docker update --restart no %CONTAINER_NAME% >nul 2>&1
    docker stop -t 5 %CONTAINER_NAME% >nul 2>&1
    goto :fail
  )
  set "URL="
  for /f "delims=" %%u in ('docker exec %CONTAINER_NAME% python3 -m app.cli health --print-url 2^>nul') do set "URL=%%u"
  docker exec %CONTAINER_NAME% python3 -m app.cli health >nul 2>&1
  if not errorlevel 1 (
    for /f "delims=:" %%s in ("!URL!") do set "SCHEME=%%s"
    if not defined SCHEME set "SCHEME=http"
    set "SHOW_HOST=%HOST_BIND%"
    if "!SHOW_HOST!"=="0.0.0.0" set "SHOW_HOST=%COMPUTERNAME%"
    echo.
    echo SQL Assistant is up: !SCHEME!://!SHOW_HOST!:%HOST_PORT%  ^(data: %DATA_DIR%, config: %CONFIG_DIR%^)
    if exist "%DATA_DIR%\run\initial-admin-password.txt" echo First start: initial admin password is in %DATA_DIR%\run\initial-admin-password.txt
    call :pausemaybe
    exit /b 0
  )
  <nul set /p "=."
  ping -n 2 127.0.0.1 >nul
)
echo.
echo WARNING: not healthy after 90 s. Check: docker logs %CONTAINER_NAME% 1>&2
goto :fail

rem ---- helpers ------------------------------------------------------------------------------------------------------
:stampof
rem :stampof IMAGE VAR -> VAR = value of label org.sqlassist.build (empty if the image or label is missing)
set "%~2="
for /f "tokens=2" %%s in ('docker image inspect -f "{{range $k, $v := .Config.Labels}}{{println $k $v}}{{end}}" %~1 2^>nul ^| findstr /b /c:"org.sqlassist.build "') do set "%~2=%%s"
exit /b 0

:pausemaybe
echo %CMDCMDLINE% | find /i "/c" >nul && pause
exit /b 0

:fail
call :pausemaybe
exit /b 1
