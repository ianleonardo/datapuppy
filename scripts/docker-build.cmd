@echo off
rem docker-build.cmd - build the AI SQL Assistant image for linux/amd64 and save it as an image file in IMAGE_DIR.
rem Usage: docker-build.cmd [--rebuild-web] [--no-save] [--no-cache] [--tag STAMP] [--drivers "oracledb mariadb"]
rem                         [--base-image IMAGE] [--pip-index-url URL]
rem Pure batch (no PowerShell). Settings: docker\docker.conf. Exit codes: 0 OK, 1 failed.
setlocal EnableExtensions
call "%~dp0docker-settings.cmd" || goto :fail

set "REBUILD_WEB=0" & set "SAVE=1" & set "NO_CACHE=" & set "STAMP="
:args
if "%~1"=="" goto :argsdone
if /i "%~1"=="--rebuild-web" (set "REBUILD_WEB=1" & shift & goto :args)
if /i "%~1"=="--no-save" (set "SAVE=0" & shift & goto :args)
if /i "%~1"=="--no-cache" (set "NO_CACHE=--no-cache" & shift & goto :args)
if /i "%~1"=="--tag" (set "STAMP=%~2" & shift & shift & goto :args)
if /i "%~1"=="--drivers" (set "DRIVERS=%~2" & shift & shift & goto :args)
if /i "%~1"=="--base-image" (set "BASE_IMAGE=%~2" & shift & shift & goto :args)
if /i "%~1"=="--pip-index-url" (set "PIP_INDEX_URL=%~2" & shift & shift & goto :args)
echo Unknown option: %~1 1>&2
goto :fail
:argsdone

rem build stamp YYYYMMDD-HHMMSS in UTC, taken from the Docker daemon clock (locale independent)
if not defined STAMP (
  for /f "delims=" %%t in ('docker info -f "{{.SystemTime}}"') do set "SYSTIME=%%t"
)
if not defined STAMP (
  call :utcstamp
)
if not defined STAMP (echo ERROR: could not determine a build stamp; pass --tag YYYYMMDD-HHMMSS 1>&2 & goto :fail)

if "%REBUILD_WEB%"=="1" (
  echo ==^> Building the web UI with Vite+ ^(vp^)
  where vp >nul 2>&1 || (echo ERROR: 'vp' is not on PATH. Install it with vp-setup.exe from https://setup.viteplus.dev 1>&2 & goto :fail)
  pushd "%SQA_ROOT%\web"
  call vp install || (popd & goto :fail)
  call vp build || (popd & goto :fail)
  popd
  if exist "%SQA_ROOT%\server\app\static" rmdir /s /q "%SQA_ROOT%\server\app\static"
  xcopy "%SQA_ROOT%\web\dist" "%SQA_ROOT%\server\app\static\" /e /i /q /y >nul || goto :fail
)
if not exist "%SQA_ROOT%\server\app\static\index.html" (
  echo ERROR: server\app\static\index.html is missing. Run with --rebuild-web ^(needs vp^) first. 1>&2
  goto :fail
)

echo ==^> Building image %IMAGE_NAME%:%STAMP% for linux/amd64
set "BARGS=--platform linux/amd64 -f "%SQA_ROOT%\docker\Dockerfile" -t %IMAGE_NAME%:%STAMP% -t %IMAGE_NAME%:latest --build-arg BUILD_STAMP=%STAMP% --build-arg BASE_IMAGE=%BASE_IMAGE% --build-arg "DRIVERS=%DRIVERS%""
if defined BUILD_NETWORK set "BARGS=%BARGS% --network %BUILD_NETWORK%"
if defined PIP_INDEX_URL set "BARGS=%BARGS% --build-arg PIP_INDEX_URL=%PIP_INDEX_URL%"
if defined HTTP_PROXY set "BARGS=%BARGS% --build-arg HTTP_PROXY=%HTTP_PROXY%"
if defined HTTPS_PROXY set "BARGS=%BARGS% --build-arg HTTPS_PROXY=%HTTPS_PROXY%"
if defined NO_PROXY set "BARGS=%BARGS% --build-arg NO_PROXY=%NO_PROXY%"
docker buildx version >nul 2>&1
if errorlevel 1 (
  docker build %NO_CACHE% %BARGS% "%SQA_ROOT%" || goto :fail
) else (
  docker buildx build --load %NO_CACHE% %BARGS% "%SQA_ROOT%" || goto :fail
)

set "ARCH="
for /f "delims=" %%a in ('docker image inspect -f "{{.Os}}/{{.Architecture}}" %IMAGE_NAME%:%STAMP%') do set "ARCH=%%a"
if /i not "%ARCH%"=="linux/amd64" (echo ERROR: built image is %ARCH%, expected linux/amd64. 1>&2 & goto :fail)
echo Built %IMAGE_NAME%:%STAMP% ^(%ARCH%^), also tagged %IMAGE_NAME%:latest

if "%SAVE%"=="1" (
  call "%~dp0docker-settings.cmd" :abs IMAGE_DIR
  call :save || goto :fail
)
echo Done. Start it with scripts\docker-start.cmd
call :pausemaybe
exit /b 0

:save
set "OUT=%IMAGE_DIR%\%IMAGE_NAME%-%STAMP%-linux-amd64.tar"
echo ==^> Saving image file %OUT%
docker save -o "%OUT%.part" %IMAGE_NAME%:%STAMP% %IMAGE_NAME%:latest || exit /b 1
move /y "%OUT%.part" "%OUT%" >nul || exit /b 1
for %%f in ("%OUT%") do echo Saved %%~zf bytes  %OUT%
rem keep the newest KEEP_IMAGES files (names sort by stamp)
for /f "skip=%KEEP_IMAGES% delims=" %%f in ('dir /b /a-d /o-n "%IMAGE_DIR%\%IMAGE_NAME%-*-linux-amd64.tar*" 2^>nul ^| findstr /v /i "\.part$"') do (
  echo Removing old image file %%f
  del /q "%IMAGE_DIR%\%%f"
)
exit /b 0

:utcstamp
rem SYSTIME looks like 2026-10-28T02:10:33.123456789Z or ...+08:00 ; Docker Desktop's VM runs on UTC
if not defined SYSTIME exit /b 0
set "STAMP=%SYSTIME:~0,4%%SYSTIME:~5,2%%SYSTIME:~8,2%-%SYSTIME:~11,2%%SYSTIME:~14,2%%SYSTIME:~17,2%"
exit /b 0

:pausemaybe
echo %CMDCMDLINE% | find /i "/c" >nul && pause
exit /b 0

:fail
call :pausemaybe
exit /b 1
