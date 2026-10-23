@echo off
rem Shared steps for setup.bat, start.bat, update.bat, stop.bat, backup.bat and restore.bat.
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
rem   ALERTBOT_DOCKER_WAIT_SECONDS (180), ALERTBOT_READY_WAIT_SECONDS (240), ALERTBOT_DESKTOP_GRACE_SECONDS (45)
rem   ALERTBOT_DESKTOP_RUNNING (1/0), ALERTBOT_DESKTOP_RESTART (a command), ALERTBOT_RESTORE_CHOICE
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
set "DESKTOP_GRACE=45"
if defined ALERTBOT_DESKTOP_GRACE_SECONDS set "DESKTOP_GRACE=%ALERTBOT_DESKTOP_GRACE_SECONDS%"
exit /b 0


rem -- Is Docker Desktop installed, running and set to Linux containers? ------------------------
:check_docker
if defined CUSTOM_DOCKER goto :check_compose
where docker >nul 2>&1
if errorlevel 1 goto :msg_no_docker
:check_compose
%ALERTBOT_DOCKER% compose version >nul 2>&1
if errorlevel 1 goto :msg_no_compose
call :compose_new_enough
if errorlevel 1 goto :msg_old_compose
%ALERTBOT_DOCKER% info >nul 2>&1
if not errorlevel 1 goto :engine_ok
rem Not answering. If Docker Desktop isn't open, start it and wait. If it is open, it may just
rem be starting; after a short wait it is probably stuck, and a restart is offered.
set "DD_RESTARTED="
set "STUCK_WAIT=%DOCKER_WAIT%"
call :desktop_running
if errorlevel 1 (call :start_desktop) else (set "STUCK_WAIT=%DESKTOP_GRACE%")
set /a WAITED=0
:engine_wait
%ALERTBOT_DOCKER% info >nul 2>&1
if not errorlevel 1 goto :engine_ready
if %WAITED% geq %STUCK_WAIT% goto :engine_stuck
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

:engine_stuck
if defined DD_RESTARTED goto :msg_engine_down
call :desktop_running
if errorlevel 1 goto :msg_engine_down
rem The automated tests use a fake docker; they never restart the real Docker Desktop.
if defined CUSTOM_DOCKER if not defined ALERTBOT_DESKTOP_RESTART goto :msg_engine_down
if defined ALERTBOT_NO_DESKTOP_START if not defined ALERTBOT_DESKTOP_RESTART goto :msg_engine_down
echo.
echo  Docker Desktop is open, but it isn't responding. Restarting it usually fixes this.
echo  The bot's data isn't affected.
if defined ALERTBOT_ASSUME_YES goto :do_restart
choice /c YN /t 60 /d Y /m " Restart Docker Desktop now"
if errorlevel 2 goto :msg_engine_down
:do_restart
call :restart_desktop
set "DD_RESTARTED=1"
set "STUCK_WAIT=%DOCKER_WAIT%"
set /a WAITED=0
goto :engine_wait

:desktop_running
if "%ALERTBOT_DESKTOP_RUNNING%"=="1" exit /b 0
if "%ALERTBOT_DESKTOP_RUNNING%"=="0" exit /b 1
tasklist /fi "imagename eq Docker Desktop.exe" /nh 2>nul | find /i "Docker Desktop.exe" >nul
exit /b %errorlevel%

:restart_desktop
echo  Restarting Docker Desktop. This can take a minute or two...
if defined ALERTBOT_DESKTOP_RESTART (
  call %ALERTBOT_DESKTOP_RESTART%
  exit /b 0
)
rem Newer Docker Desktop restarts itself on request; it then waits below for the engine.
%ALERTBOT_DOCKER% desktop restart --detach --timeout 120 >nul 2>&1
if not errorlevel 1 exit /b 0
rem Older versions: end every Docker Desktop process (also its helpers, which can otherwise
rem block the next start), stop its Linux VM, and start it again.
taskkill /f /im "Docker Desktop.exe" >nul 2>&1
taskkill /f /im "com.docker.*" >nul 2>&1
wsl --terminate docker-desktop >nul 2>&1
wsl --terminate docker-desktop-data >nul 2>&1
ping -n 4 127.0.0.1 >nul
set "DD_EXE=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
if exist "%DD_EXE%" start "" "%DD_EXE%"
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

rem Docker Compose 2.23.0 or later (Docker Desktop 4.25 or later): the oldest version the scripts
rem are tested with. Sets COMPOSE_VERSION. A version that can't be read or understood (it must look
rem like 2.23.0, v2.23.0 or 2.24.6-desktop.1) counts as not supported: nothing is run.
:compose_new_enough
set "COMPOSE_VERSION="
set "COMPOSE_UNREADABLE=1"
set "CV_MAJOR="
set "CV_MINOR="
for /f "usebackq delims=" %%V in (`%ALERTBOT_DOCKER% compose version --short 2^>nul`) do set "COMPOSE_VERSION=%%V"
if not defined COMPOSE_VERSION exit /b 1
echo.%COMPOSE_VERSION%| findstr /r /x /c:"v*[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*[-+.a-zA-Z0-9]*" >nul || exit /b 1
for /f "tokens=1,2 delims=.-+ " %%A in ("%COMPOSE_VERSION:v=%") do (
  set "CV_MAJOR=%%A"
  set "CV_MINOR=%%B"
)
if not defined CV_MINOR exit /b 1
set "COMPOSE_UNREADABLE="
if %CV_MAJOR% gtr 2 exit /b 0
if %CV_MAJOR% lss 2 exit /b 1
if %CV_MINOR% geq 23 exit /b 0
exit /b 1

:msg_old_compose
echo.
if not defined COMPOSE_UNREADABLE (
  echo  Your Docker Desktop is too old for this bot: it has Docker Compose %COMPOSE_VERSION%,
  echo  and the bot needs Docker Compose 2.23.0 or later ^(Docker Desktop 4.25 or later^).
) else (
  echo  The version of Docker Compose on this computer couldn't be read, so it isn't known whether
  echo  it works with this bot. It needs Docker Compose 2.23.0 or later ^(Docker Desktop 4.25 or later^).
)
echo  Open Docker Desktop, install its latest update, then double-click this file again.
echo  Nothing was changed.
exit /b 1

:msg_engine_down
echo.
echo  Docker Desktop isn't running yet, or it isn't working right now.
echo   1. Open Docker Desktop from the Start menu.
echo   2. Wait until it says "Engine running" in the bottom left corner.
echo   3. Then double-click this file again.
echo  If Docker Desktop is open but this keeps happening, restart the computer, or quit
echo  Docker Desktop (right-click the whale icon next to the clock) and open it again.
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
%ALERTBOT_CURL% -sS --max-time 3 "%URL%api/health/" 2>nul | findstr /c:"newer_data" >nul
if not errorlevel 1 goto :msg_newer_running
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


rem -- Can this folder's version use the bot's data? (before starting or replacing the bot) -------
rem Versions 1.0.0-rc.2 and later know which data they can use. Exit code 3: a newer version
rem updated the data last, so nothing is started or replaced, and a newer bot that is running
rem keeps running. Any other failure stops nothing: the bot checks again itself when it starts.
:check_compat
%COMPOSE% run --rm --no-deps web python manage.py check_compat >nul 2>&1
if errorlevel 4 exit /b 0
if errorlevel 3 goto :msg_newer_data
exit /b 0

:msg_newer_data
echo.
echo  STOP: this folder has an OLDER version of the bot than the one that last used your data.
echo  Nothing was started or changed. If the newer version was running, it is still running.
echo   - To keep using the newer version: close this window and use start.bat in the folder
echo     of your newest download. Then delete this older folder, so it isn't used by mistake.
echo   - To go back to this older version on purpose: double-click restore.bat in this folder
echo     and choose a backup made before the update (its name starts with db-before-update).
exit /b 1

:msg_newer_running
echo.
echo  This folder's version of the bot is OLDER than your data, so it shows a warning page
echo  instead of running. Your data wasn't changed, but the bot isn't running right now.
echo   - Start the newer version: double-click start.bat in the folder of your newest download.
echo   - Or go back to this older version on purpose: double-click restore.bat in this folder
echo     and choose a backup made before the update (its name starts with db-before-update).
exit /b 1


rem -- Which folder ran the bot until now? Docker keeps it on the bot's containers ---------------
rem Sets PREVIOUS_DIR (empty if unknown). Only reads. Docker answers in UTF-8, so the console reads it
rem as UTF-8 meanwhile (folder names in other languages stay readable), then goes back to its own code page.
:previous_folder
set "PREVIOUS_DIR="
set "LABELS="
set "OLD_CP="
for /f "tokens=2 delims=:." %%P in ('chcp 2^>nul') do set /a OLD_CP=%%P >nul 2>&1
chcp 65001 >nul 2>&1
for /f "usebackq delims=" %%L in (`%ALERTBOT_DOCKER% ps -a --filter "label=com.docker.compose.project=%PROJECT%" --filter "label=com.docker.compose.service=web" --format "{{.Labels}}" 2^>nul`) do set "LABELS=%%L"
if defined OLD_CP chcp %OLD_CP% >nul 2>&1
if not defined LABELS exit /b 0
set "REST=%LABELS:*project.working_dir=%"
if "%REST%"=="%LABELS%" exit /b 0
for /f "tokens=1 delims=," %%D in ("%REST:~1%") do set "PREVIOUS_DIR=%%D"
exit /b 0

rem -- After the bot has moved to this folder: name the old one, so it isn't used by mistake ------
:old_folder_note
if not defined PREVIOUS_DIR exit /b 0
if /i "%PREVIOUS_DIR%"=="%BOT_DIR%" exit /b 0
echo.
echo  IMPORTANT: until now the bot was run from another folder:
echo     %PREVIOUS_DIR%
echo  That folder has an older version. Don't use its files any more: they would start the older
echo  version again. Once you're happy with this version, delete that folder. From now on, use
echo  the files in this folder:
echo     %BOT_DIR%
exit /b 0


rem -- After a successful update: remove this bot's old, unused images ----------------------------
rem Only untagged images with this bot's label, which nothing uses any more. Volumes (the data)
rem and the running version are never touched. A failure here doesn't matter.
:tidy_images
%ALERTBOT_DOCKER% image prune -f --filter "label=org.opencontainers.image.title=Bluesky Discord Alert Bot" >nul 2>&1
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

rem -- restore.bat: list the backups, stop the bot, put a backup back ----------------------------
:export_backups
if not exist "backups" mkdir "backups"
%COMPOSE% run --rm --no-deps -v "%BOT_DIR%\backups:/export" web backups
if errorlevel 1 goto :msg_backups_failed
exit /b 0

:msg_backups_failed
echo.
echo  The backups inside Docker couldn't be listed. Nothing was changed.
exit /b 1

:stop
%COMPOSE% stop
if errorlevel 1 goto :msg_stop_failed
exit /b 0

:msg_stop_failed
echo.
echo  The bot couldn't be stopped, so nothing was restored. Your data is unchanged.
exit /b 1

rem Puts the backup back, but only if the comparison made again with the bot stopped gives the same
rem result as the report that was confirmed (%~2, its IMPACT code). Exit code 11: the data changed
rem since that report; nothing was put back.
:restore_file
%COMPOSE% run --rm --no-deps -v "%BOT_DIR%\backups:/restore:ro" web restore --expect "%~2" "/restore/%~1"
if errorlevel 12 goto :msg_restore_failed
if errorlevel 11 goto :msg_restore_changed
if errorlevel 1 goto :msg_restore_failed
exit /b 0

:msg_restore_changed
echo.
echo  Your data changed after you confirmed (what is different is shown above), so the backup
echo  wasn't put back and nothing was changed. Run restore.bat again to see what it would undo now.
exit /b 1

rem What putting a backup back would undo. Changes nothing, so the bot may keep running meanwhile.
rem Exit code 0: nothing would be lost; 10: something would be (restore.bat asks for RESTORE);
rem 3: the backup is from a newer version; anything else: it can't be used (the reason is shown).
rem Sets IMPACT: the report's code, which the restore checks again (:restore_file).
:restore_report
set "IMPACT="
set "REPORT_FILE=%TEMP%\alertbot-report-%RANDOM%%RANDOM%.txt"
%COMPOSE% run --rm --no-deps -v "%BOT_DIR%\backups:/restore:ro" web restore --report "/restore/%~1" > "%REPORT_FILE%" 2>&1
set "REPORT_RESULT=%errorlevel%"
findstr /v /b /c:"IMPACT=" "%REPORT_FILE%"
for /f "usebackq tokens=1,* delims==" %%A in ("%REPORT_FILE%") do if "%%A"=="IMPACT" set "IMPACT=%%B"
del "%REPORT_FILE%" >nul 2>&1
if "%REPORT_RESULT%"=="0" if not defined IMPACT exit /b 1
if "%REPORT_RESULT%"=="10" if not defined IMPACT exit /b 1
exit /b %REPORT_RESULT%

rem Before the database is changed as a whole (a restore, or a start that may update it): nothing
rem may be using the bot's data. The bot's own containers are stopped by then, but a container
rem started from another folder (by an older version's scripts, say) could still have the data
rem open and write to it. 1.0.0-rc.2 and older don't take the data lock, so this check is what
rem protects against them. Only looks; nothing is stopped here. If Docker can't answer, it is
rem not known whether the data is in use, and nothing is done.
rem (ALERTBOT-OK is printed only if Docker answered: no answer means "unknown", not "nothing".)
:ensure_data_free
set "USERS="
set "DOCKER_ANSWERED="
for /f "usebackq delims=" %%N in (`%ALERTBOT_DOCKER% ps --filter "volume=%VOLUME%" --format "{{.Names}}" 2^>nul ^&^& echo ALERTBOT-OK`) do (
  if "%%N"=="ALERTBOT-OK" (set "DOCKER_ANSWERED=1") else (call :add_user "%%N")
)
if not defined DOCKER_ANSWERED goto :msg_data_use_unknown
if not defined USERS exit /b 0
echo.
echo  Something else is still using the bot's data, so the backup wasn't put back:
echo     %USERS%
echo  It is probably the bot started from another folder. Stop it there (with stop.bat in that
echo  folder, or in Docker Desktop), then run restore.bat again. Nothing was changed.
exit /b 1

:add_user
if defined USERS (set "USERS=%USERS%, %~1") else (set "USERS=%~1")
exit /b 0

:msg_data_use_unknown
echo.
echo  Docker couldn't say whether anything else is using the bot's data right now, so nothing
echo  was changed. Wait a moment and try again. If this keeps happening, restart Docker Desktop.
exit /b 1


rem -- Before start, setup and update start this folder's version --------------------------------
rem Starting another version than the one running can update the database (its web service
rem migrates it on start). Nothing else may use the data meanwhile, and 1.0.0-rc.2 and older
rem don't take the data lock. So unless the bot is already running exactly this folder's version:
rem  1. the bot's own containers are stopped first (the checker finishes the notification it is
rem     sending), then
rem  2. no other container may use the data volume (:ensure_data_free); if one does, the stopped
rem     containers are started again as they were, and nothing else is done.
rem Whatever Docker can't answer counts against going on: nothing is started or changed.
:prepare_start
call :running_version || goto :msg_data_use_unknown_running
if defined SAME_VERSION exit /b 0
if not defined OWN_RUNNING goto :prepare_check_free
echo  Stopping the running bot first (it finishes any notification it is sending)...
%COMPOSE% stop
if errorlevel 1 goto :msg_bot_not_stopped
:prepare_check_free
call :ensure_data_free
if not errorlevel 1 exit /b 0
if defined OWN_RUNNING %COMPOSE% start >nul 2>&1
exit /b 1

rem Sets OWN_RUNNING (the bot's own containers are running) and SAME_VERSION (all of them run the
rem image this folder would start). Exit code 1 if Docker can't say which containers run.
:running_version
set "OWN_RUNNING="
set "SAME_VERSION="
set "ALL_SAME=1"
set "NEW_IMAGE="
set "IMAGE_REF=%CFG_IMAGE%"
if not defined IMAGE_REF set "IMAGE_REF=%ALERTBOT_LOCAL_IMAGE%"
if not defined IMAGE_REF set "IMAGE_REF=bluesky-alert-bot:local"
for /f "usebackq delims=" %%I in (`%ALERTBOT_DOCKER% image inspect --format "{{.Id}}" "%IMAGE_REF%" 2^>nul`) do set "NEW_IMAGE=%%I"
set "DOCKER_ANSWERED="
for /f "usebackq delims=" %%C in (`%COMPOSE% ps -q 2^>nul ^&^& echo ALERTBOT-OK`) do (
  if "%%C"=="ALERTBOT-OK" (set "DOCKER_ANSWERED=1") else (call :own_container %%C)
)
if not defined DOCKER_ANSWERED exit /b 1
if defined OWN_RUNNING if defined ALL_SAME if defined NEW_IMAGE set "SAME_VERSION=1"
exit /b 0

:own_container
set "OWN_RUNNING=1"
set "IMG="
for /f "usebackq delims=" %%I in (`%ALERTBOT_DOCKER% inspect --format "{{.Image}}" %1 2^>nul`) do set "IMG=%%I"
if not defined IMG set "ALL_SAME="
if /i not "%IMG%"=="%NEW_IMAGE%" set "ALL_SAME="
exit /b 0

:msg_data_use_unknown_running
echo.
echo  Docker couldn't say whether the bot is running right now, so nothing was started or
echo  changed. Wait a moment and try again. If this keeps happening, restart Docker Desktop.
exit /b 1

:msg_bot_not_stopped
echo.
echo  The running bot couldn't be stopped, so this version wasn't started. Your data is
echo  unchanged. Try again in a moment.
exit /b 1

:msg_restore_failed
echo.
echo  That backup couldn't be put back (the reason is above). The bot's data is unchanged.
exit /b 1
