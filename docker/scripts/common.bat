@echo off
rem Shared steps for setup.bat, start.bat, update.bat, stop.bat and backup.bat.
rem Used as:   call "%~dp0docker\scripts\common.bat" :step [argument]
rem Every step returns 0 when it worked, and 1 after it has explained a problem to the user.
rem
rem These scripts never delete Docker volumes: the bot's data (accounts, login, channels,
rem settings, history and backups) lives in the volume and survives restarts and updates.
rem
rem Optional settings, used by the automated tests:
rem   ALERTBOT_DOCKER, ALERTBOT_CURL      the docker and curl commands to use
rem   ALERTBOT_PROJECT                    Docker project name (default alertbot)
rem   ALERTBOT_PORT, ALERTBOT_IMAGE       override docker\compose.env
rem   ALERTBOT_NO_BROWSER, ALERTBOT_NO_PAUSE, ALERTBOT_ASSUME_YES, ALERTBOT_NO_DESKTOP_START
rem   ALERTBOT_DOCKER_WAIT_SECONDS (180), ALERTBOT_READY_WAIT_SECONDS (240)
set "ALERTBOT_SCRIPTS=%~dp0"
call %*
exit /b %errorlevel%


:init
cd /d "%ALERTBOT_SCRIPTS%..\.."
set "BOT_DIR=%CD%"
set "CUSTOM_DOCKER="
if defined ALERTBOT_DOCKER set "CUSTOM_DOCKER=1"
if not defined ALERTBOT_DOCKER set "ALERTBOT_DOCKER=docker"
if not defined ALERTBOT_CURL set "ALERTBOT_CURL=curl.exe"
set "CFG_IMAGE="
set "CFG_PORT=8000"
if exist "docker\compose.env" for /f "usebackq eol=# tokens=1,* delims==" %%A in ("docker\compose.env") do (
  if /i "%%A"=="ALERTBOT_IMAGE" set "CFG_IMAGE=%%B"
  if /i "%%A"=="ALERTBOT_PORT" set "CFG_PORT=%%B"
)
if defined ALERTBOT_IMAGE set "CFG_IMAGE=%ALERTBOT_IMAGE%"
if defined ALERTBOT_PORT set "CFG_PORT=%ALERTBOT_PORT%"
set "PROJECT=alertbot"
if defined ALERTBOT_PROJECT set "PROJECT=%ALERTBOT_PROJECT%"
set "COMPOSE=%ALERTBOT_DOCKER% compose -p %PROJECT% --env-file docker\compose.env -f docker-compose.yml"
if not defined CFG_IMAGE set "COMPOSE=%COMPOSE% -f docker-compose.build.yml"
set "VOLUME=%PROJECT%_bot-data"
set "URL=http://127.0.0.1:%CFG_PORT%/"
set "DOCKER_WAIT=180"
if defined ALERTBOT_DOCKER_WAIT_SECONDS set "DOCKER_WAIT=%ALERTBOT_DOCKER_WAIT_SECONDS%"
set "READY_WAIT=240"
if defined ALERTBOT_READY_WAIT_SECONDS set "READY_WAIT=%ALERTBOT_READY_WAIT_SECONDS%"
exit /b 0


rem -- Is Docker Desktop installed, running and set to Linux containers? ------------------------
:check_docker
if defined CUSTOM_DOCKER goto :check_compose
where docker >nul 2>&1
if errorlevel 1 goto :msg_no_docker
:check_compose
%ALERTBOT_DOCKER% compose version >nul 2>&1
if errorlevel 1 goto :msg_no_compose
%ALERTBOT_DOCKER% info >nul 2>&1
if not errorlevel 1 goto :engine_ok
call :start_desktop
set /a WAITED=0
:engine_wait
%ALERTBOT_DOCKER% info >nul 2>&1
if not errorlevel 1 goto :engine_ready
if %WAITED% geq %DOCKER_WAIT% goto :msg_engine_down
ping -n 4 127.0.0.1 >nul
set /a WAITED+=3
<nul set /p "=."
goto :engine_wait
:engine_ready
echo.
:engine_ok
set "OSTYPE="
for /f "usebackq delims=" %%O in (`%ALERTBOT_DOCKER% info --format "{{.OSType}}" 2^>nul`) do set "OSTYPE=%%O"
if /i not "%OSTYPE%"=="linux" goto :msg_windows_containers
echo  Docker Desktop is running.
exit /b 0

:start_desktop
if defined ALERTBOT_NO_DESKTOP_START exit /b 0
set "DD_EXE=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
if not exist "%DD_EXE%" exit /b 0
echo  Starting Docker Desktop. This can take a minute or two...
start "" "%DD_EXE%"
exit /b 0

:msg_no_docker
echo.
echo  Docker Desktop isn't installed on this computer, or Windows can't find it.
echo   1. Download Docker Desktop from https://www.docker.com/products/docker-desktop/
echo   2. Install it. Restart the computer if it asks you to.
echo   3. Open Docker Desktop once and wait until it says "Engine running".
echo   4. Then double-click this file again.
exit /b 1

:msg_no_compose
echo.
echo  Your Docker Desktop is too old for this bot.
echo  Open Docker Desktop, install its latest update, then double-click this file again.
exit /b 1

:msg_engine_down
echo.
echo  Docker Desktop isn't running yet, or it isn't working right now.
echo   1. Open Docker Desktop from the Start menu.
echo   2. Wait until it says "Engine running" in the bottom left corner.
echo   3. Then double-click this file again.
echo  If Docker Desktop is open but this keeps happening, restart it: right-click the
echo  whale icon next to the clock and choose "Restart".
exit /b 1

:msg_windows_containers
echo.
echo  Docker Desktop is set to "Windows containers", but this bot needs "Linux containers".
echo  Right-click the whale icon next to the clock, choose "Switch to Linux containers...",
echo  wait a moment, then double-click this file again.
exit /b 1


rem -- Has the bot been set up here before (does its data volume exist)? -------------------------
:is_set_up
%ALERTBOT_DOCKER% volume inspect %VOLUME% >nul 2>&1
exit /b %errorlevel%


rem -- Is the panel's port free (unless the bot itself is already using it)? ---------------------
:check_port
set "RUNNING="
for /f "usebackq delims=" %%C in (`%COMPOSE% ps -q web 2^>nul`) do set "RUNNING=1"
if defined RUNNING exit /b 0
netstat -ano -p tcp | findstr /r /c:"127\.0\.0\.1:%CFG_PORT% .*LISTENING" /c:"0\.0\.0\.0:%CFG_PORT% .*LISTENING" >nul
if errorlevel 1 exit /b 0
echo.
echo  Another program is already using port %CFG_PORT% on this computer, so the control panel can't start.
echo   - If the earlier version of the bot is running in a black window, close that window first.
echo     Two copies of the bot running at once would announce posts twice.
echo   - Otherwise, open docker\compose.env in Notepad and change ALERTBOT_PORT to another number,
echo     for example 8080. Then double-click this file again.
exit /b 1


rem -- Build the bot here, or download the published version -------------------------------------
:get_image
if defined CFG_IMAGE goto :pull_image
echo  Building the bot from this folder. The first time can take several minutes...
%COMPOSE% build %BUILD_ARGS%
if errorlevel 1 goto :msg_get_failed
exit /b 0
:pull_image
echo  Downloading the bot...
%COMPOSE% pull
if errorlevel 1 goto :msg_get_failed
exit /b 0

:msg_get_failed
echo.
echo  The bot program couldn't be prepared. Check that this computer is connected to the
echo  internet, then try again. Nothing was changed, and your data is safe.
exit /b 1


rem -- Bring over the accounts and settings of the earlier, non-Docker version -------------------
:import_old
if not exist "db.sqlite3" if not exist ".env" exit /b 0
call :is_set_up
if not errorlevel 1 exit /b 0
echo  Found accounts and settings from an earlier version of the bot in this folder.
if defined ALERTBOT_ASSUME_YES goto :do_import
choice /c YN /t 120 /d Y /m " Copy them into the new version? The old files are not changed"
if errorlevel 2 exit /b 0
:do_import
%COMPOSE% run --rm --no-deps -v "%BOT_DIR%:/import:ro" -e LEGACY_ENV_FILE=/import/.env web import-old
if errorlevel 1 goto :msg_import_failed
exit /b 0

:msg_import_failed
echo.
echo  The earlier version's data couldn't be copied. Its files were not changed.
echo  Run setup.bat again, or answer N to start without the old data.
exit /b 1


rem -- Back up the database, and keep a copy in the backups folder next to these scripts ---------
:backup
if not exist "backups" mkdir "backups"
%COMPOSE% run --rm --no-deps -v "%BOT_DIR%\backups:/export" web backup --label %~1 --export /export
if errorlevel 1 goto :msg_backup_failed
echo  A copy of the backup is in %BOT_DIR%\backups
exit /b 0

:msg_backup_failed
echo.
echo  The backup didn't work, so nothing else was done. Your data is unchanged.
exit /b 1


rem -- Start (or restart with a new version) -----------------------------------------------------
:up
%COMPOSE% up -d
if errorlevel 1 goto :msg_up_failed
exit /b 0

:msg_up_failed
echo.
echo  Docker couldn't start the bot. Your data is safe: nothing was deleted.
call :save_logs
exit /b 1


rem -- Wait until the control panel answers ------------------------------------------------------
:wait_ready
echo  Waiting for the control panel to be ready...
set /a WAITED=0
:ready_loop
%ALERTBOT_CURL% -fsS -o nul --max-time 3 "%URL%api/health/" >nul 2>&1
if not errorlevel 1 goto :ready_ok
if %WAITED% geq %READY_WAIT% goto :msg_not_ready
ping -n 3 127.0.0.1 >nul
set /a WAITED+=2
<nul set /p "=."
goto :ready_loop
:ready_ok
echo.
echo  The control panel is ready: %URL%
exit /b 0

:msg_not_ready
echo.
echo  The bot started, but the control panel didn't answer in time. Your data is safe.
call :save_logs
exit /b 1

:save_logs
if not exist "logs" mkdir "logs"
set "LOGFILE=logs\problem-report.txt"
> "%LOGFILE%" echo Bluesky Discord Alert Bot - problem report
>> "%LOGFILE%" echo Passwords and Discord webhook links are hidden in the bot's messages.
%COMPOSE% ps -a >> "%LOGFILE%" 2>&1
%COMPOSE% logs --no-color --tail 300 >> "%LOGFILE%" 2>&1
echo  Details were saved to %BOT_DIR%\%LOGFILE%
echo  If you ask for help, share that file.
exit /b 0


rem -- Finishing touches -------------------------------------------------------------------------
:open_browser
if defined ALERTBOT_NO_BROWSER exit /b 0
start "" "%URL%"
exit /b 0

:pause_end
if defined ALERTBOT_NO_PAUSE exit /b 0
echo.
pause
exit /b 0
