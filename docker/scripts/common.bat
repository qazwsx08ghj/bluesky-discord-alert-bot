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
set "CFG_CHANNEL="
if exist "docker\compose.env" for /f "usebackq eol=# tokens=1,* delims==" %%A in ("docker\compose.env") do (
  if /i "%%A"=="ALERTBOT_IMAGE" set "CFG_IMAGE=%%B"
  if /i "%%A"=="ALERTBOT_PORT" set "CFG_PORT=%%B"
  if /i "%%A"=="ALERTBOT_CHANNEL" set "CFG_CHANNEL=%%B"
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

rem == update.bat: the one-click update (docs/DOCKER.md, section 3) ================================
rem update.bat runs this from a copy in %TEMP% (:copy_for_update): an update replaces this folder's
rem scripts, update.bat and this file included, while they run.
rem   1. Finish or undo an update that stopped part-way (docker\update\state.txt).
rem   2. Ask the installed version whether a newer one exists (update-plan: GitHub's releases,
rem      never older, withdrawn versions skipped, fails closed). Up to date: nothing else happens.
rem   3. Pull the new version by digest; check its version and its files (hashes); check it can
rem      update the data (update-preflight, on a copy). Nothing has changed yet.
rem   4. Stop the bot; nothing else may use the data; back it up.
rem   5. Keep the current files (docker\update\previous), put the new ones in place, pin the new image
rem      in docker\compose.env (every other setting there is kept); update the data in a trial run
rem      (no network, no port); note a fingerprint of the data.
rem   6. Start the new version and check it answers as that version. Done.
rem If something fails before step 4, nothing has changed. From step 5 on, it goes back by itself:
rem the previous files, the backup, the previous version. If the new version started but failed,
rem it goes back only if the data's fingerprint shows nothing was written since the trial run;
rem otherwise it stops and says what to do.

:copy_for_update
set "UPDATE_COMMON=%TEMP%\alertbot-update-%RANDOM%%RANDOM%.bat"
copy /y "%ALERTBOT_SCRIPTS%common.bat" "%UPDATE_COMMON%" >nul
exit /b %errorlevel%

:update_main
set "UPD=docker\update"
if not exist "%UPD%" mkdir "%UPD%"
call :runner_vars
call :update_recover || exit /b 1
if not defined CFG_IMAGE goto :update_source
call :update_channel
echo.
echo [1/6] Checking for a newer version (%CHANNEL% channel)...
call :update_plan || goto :update_failed
call :running_version || goto :update_unknown_running
if "%PLAN_RESULT%"=="update" if not defined OWN_RUNNING goto :update_offer
if "%PLAN_RESULT%"=="update" if defined SAME_VERSION goto :update_offer
if defined OWN_RUNNING if not defined SAME_VERSION goto :update_switch
if "%PLAN_RESULT%"=="up_to_date" goto :update_current
echo  %PLAN_MESSAGE%
goto :update_failed

:update_unknown_running
call :msg_data_use_unknown_running
goto :update_failed

:update_current
call :remember_seen
echo  %PLAN_MESSAGE%
if not defined OWN_RUNNING echo  The bot isn't running right now: start.bat starts it.
echo  Nothing was changed.
call :pause_end
exit /b 0

rem The bot runs another version than this folder's (for example the folder of a newer download):
rem move it to this folder's version, as update.bat always did.
:update_switch
echo  The bot is running a different version than this folder's %PLAN_CURRENT_VERSION%. Moving it to this folder's version...
echo.
echo [2/6] Backing up your data...
call :backup before-update || goto :update_failed
call :get_image || goto :update_keep_running
call :check_compat || goto :update_failed
call :previous_folder
echo [3/6] Restarting the bot with this folder's version...
call :prepare_start || goto :update_failed
call :up || goto :update_failed_after_backup
call :wait_ready || goto :update_failed_after_backup
call :tidy_images
call :remember_seen
call :open_browser
echo.
echo  The update is complete. Your accounts, settings and history were kept.
call :old_folder_note
if "%PLAN_RESULT%"=="update" echo  A newer version, %PLAN_FINAL_VERSION%, is available too: run update.bat again to install it.
call :pause_end
exit /b 0

rem A folder with the source code (no published image): rebuild it, as update.bat always did.
:update_source
echo.
echo [1/4] Backing up your data...
call :backup before-update || goto :update_failed
echo.
echo [2/4] Getting the new version...
set "BUILD_ARGS=--pull"
call :get_image || goto :update_keep_running
call :check_compat || goto :update_failed
call :previous_folder
echo.
echo [3/4] Restarting the bot with the new version...
call :prepare_start || goto :update_failed
call :up || goto :update_failed_after_backup
echo.
echo [4/4] Checking that everything works...
call :wait_ready || goto :update_failed_after_backup
call :tidy_images
call :open_browser
echo.
echo  The update is complete. Your accounts, settings and history were kept.
call :old_folder_note
call :pause_end
exit /b 0

:update_keep_running
echo  The current version keeps running.
call :up
goto :update_failed

:update_failed_after_backup
echo.
echo  A backup from just before the update is in the backups folder next to this file.
echo  See "If an update goes wrong" in docs\DOCKER.md, or ask for help with logs\problem-report.txt.
:update_failed
echo.
echo  The update didn't finish. Nothing was deleted.
call :pause_end
exit /b 1

:update_offer
echo.
echo  %PLAN_MESSAGE%
if defined ALERTBOT_ASSUME_YES goto :update_go
choice /c YN /m " Update to %PLAN_TARGET_VERSION% now"
if errorlevel 2 goto :update_cancelled
goto :update_go

:update_cancelled
echo  Cancelled. Nothing was changed.
call :pause_end
exit /b 0

:update_go
set "OLD_IMAGE=%CFG_IMAGE%"
set "NEW_IMAGE=%PLAN_TARGET_IMAGE%"
rem Tests only: their images aren't on ghcr.io.
if defined ALERTBOT_UPDATE_TEST_IMAGE set "NEW_IMAGE=%ALERTBOT_UPDATE_TEST_IMAGE%"
set "TARGET_VERSION=%PLAN_TARGET_VERSION%"
set "WAS_RUNNING="
if defined OWN_RUNNING set "WAS_RUNNING=1"
set "BACKUP_FILE="
set "FP_TRIAL="
call :update_lock || goto :update_failed
call :state BEGIN
echo.
echo [2/6] Downloading %TARGET_VERSION%...
call :pull_ref "%NEW_IMAGE%" || goto :update_abort
call :check_version "%NEW_IMAGE%" || goto :update_abort
echo [3/6] Checking its files, and that it can update your data...
call :stage_bundle || goto :update_abort
call :preflight || goto :update_abort
echo [4/6] Stopping the bot and backing up your data...
call :state STOPPING
%COMPOSE% stop || goto :update_abort_restart
call :ensure_data_free || goto :update_abort_restart
call :backup_named before-update || goto :update_abort_restart
call :state BACKED_UP
echo [5/6] Installing the new version's files, then updating your data in a trial run...
call :save_previous || goto :update_abort_restart
call :state SWAPPING
call :swap_files || goto :update_rollback
call :state SWAPPED
call :trial || goto :update_rollback
call :fingerprint FP_TRIAL || goto :update_rollback
call :state TRIAL_OK
echo [6/6] Starting %TARGET_VERSION%...
call :state STARTING
call :up || goto :update_start_failed
call :wait_ready || goto :update_start_failed
call :running_is "%TARGET_VERSION%" || goto :update_start_failed
call :state DONE
call :remember_seen
call :update_unlock
call :open_browser
echo.
echo  The update is complete: %TARGET_VERSION% is running. Your accounts, settings and history were kept.
echo  A backup from just before the update is in the backups folder (%BACKUP_FILE%).
if not "%PLAN_STEPS%"=="1" echo  The next step towards %PLAN_FINAL_VERSION% is ready: run update.bat again to install it.
call :pause_end
exit /b 0

rem -- When a step fails ---------------------------------------------------------------------------
:update_abort
call :state ABORTED
call :update_unlock
echo.
echo  The update was stopped before anything changed. The current version keeps running as before.
goto :update_failed

:update_abort_restart
call :state ABORTED
if defined WAS_RUNNING call :up
call :update_unlock
echo.
echo  The update was stopped before anything changed. The bot was started again as it was.
goto :update_failed

:update_start_failed
echo.
echo  %TARGET_VERSION% didn't start properly.
call :save_logs
%COMPOSE% stop >nul 2>&1
set "FP_NOW="
call :fingerprint FP_NOW || goto :update_needs_user
if not "%FP_NOW%"=="%FP_TRIAL%" goto :update_needs_user
goto :update_rollback

:update_rollback
echo.
echo  Going back to the previous version, with your data as it was before the update...
call :state ROLLING_BACK
%COMPOSE% stop >nul 2>&1
call :ensure_data_free || goto :update_needs_user
call :restore_previous || goto :update_needs_user
if defined BACKUP_FILE call :restore_backup || goto :update_needs_user
rem Saved before the old version starts: resuming from here must never put the backup back again
rem (the old bot may already have changed data or sent notifications since).
call :state RESTORED
:update_rollback_start
if not defined WAS_RUNNING goto :update_rolled_back
call :up || goto :update_needs_user
call :wait_ready || goto :update_needs_user
:update_rolled_back
call :state ROLLED_BACK
call :update_unlock
echo  Done: the previous version is back, with your data as it was before the update. Nothing was lost.
goto :update_failed

:update_needs_user
call :state NEEDS_USER
call :update_unlock
echo.
echo  STOP: the update couldn't be finished or undone by itself, so it is left as it is for you to decide.
echo  Kept for you: the backup from just before the update (backups\%BACKUP_FILE%) and the previous
echo  version's files (docker\update\previous).
echo   - To keep the new version: run start.bat. If it works, run update.bat and type KEEP.
echo   - To go back: copy everything in docker\update\previous into this folder (replacing the files),
echo     then run restore.bat and choose %BACKUP_FILE%. Then run update.bat and type BACK.
echo  If you ask for help, share logs\problem-report.txt.
call :pause_end
exit /b 1

rem -- An update that stopped part-way (the window was closed, the computer turned off, ...) -------
:update_recover
if not exist "%UPD%\state.txt" exit /b 0
call :state_read
if "%ST_STATE%"=="DONE" exit /b 0
if "%ST_STATE%"=="ABORTED" exit /b 0
if "%ST_STATE%"=="ROLLED_BACK" exit /b 0
set "OLD_IMAGE=%ST_OLD_IMAGE%"
set "NEW_IMAGE=%ST_NEW_IMAGE%"
set "TARGET_VERSION=%ST_TARGET_VERSION%"
set "BACKUP_FILE=%ST_BACKUP_FILE%"
set "FP_TRIAL=%ST_FP_TRIAL%"
set "WAS_RUNNING=%ST_WAS_RUNNING%"
if "%ST_STATE%"=="NEEDS_USER" goto :recover_needs_user
echo.
echo  An earlier update stopped part-way (at %ST_STATE%). It is finished or undone first.
call :update_lock || goto :update_failed
if "%ST_STATE%"=="BEGIN" goto :recover_nothing_changed
if "%ST_STATE%"=="STOPPING" goto :recover_restart
if "%ST_STATE%"=="BACKED_UP" goto :recover_restart
if "%ST_STATE%"=="STARTING" goto :recover_starting
if "%ST_STATE%"=="RESTORED" goto :recover_restored
goto :update_rollback

:recover_restored
echo  Your data had already been put back; starting the previous version.
goto :update_rollback_start

:recover_nothing_changed
call :state ABORTED
call :update_unlock
echo  Nothing had changed yet.
exit /b 0

:recover_restart
if defined WAS_RUNNING call :up
call :state ABORTED
call :update_unlock
echo  Nothing had changed yet; the bot was started again as it was.
exit /b 0

:recover_starting
call :wait_ready || goto :update_start_failed
call :running_is "%TARGET_VERSION%" || goto :update_start_failed
call :state DONE
call :update_unlock
echo  It had finished: %TARGET_VERSION% is running.
exit /b 0

:recover_needs_user
echo.
echo  An earlier update to %TARGET_VERSION% needs your decision (see the earlier message).
echo   - If the bot works with the new version, type KEEP to keep it.
echo   - If you went back by hand (docker\update\previous, then restore.bat with %BACKUP_FILE%),
echo     type BACK.
echo   - Otherwise close this window and follow the steps it gave.
set "TYPED="
if defined ALERTBOT_UPDATE_RESOLVE set "TYPED=%ALERTBOT_UPDATE_RESOLVE%"
if not defined ALERTBOT_UPDATE_RESOLVE set /p "TYPED= Type KEEP or BACK: "
if "%TYPED%"=="KEEP" goto :recover_kept
if not "%TYPED%"=="BACK" goto :update_failed
call :state ROLLED_BACK
echo  Noted: you went back by hand. Checking for updates now...
exit /b 0
:recover_kept
call :state DONE
echo  Kept. Checking for updates now...
exit /b 0

rem -- The steps -----------------------------------------------------------------------------------
:runner_vars
rem A one-off container on the bot's data: no network, no port, no extra rights, nothing written
rem outside /data and /tmp. Used for the trial run, the fingerprint and putting a backup back.
rem Each -e is quoted: inside for /f a bare "=" would be read as a space.
set "RUNNER=%ALERTBOT_DOCKER% run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges -v %VOLUME%:/data -e "DATABASE_PATH=/data/db.sqlite3" -e "BACKUP_DIR=/data/backups" -e "SECRET_KEY_FILE=/data/secret_key" -e "CACHE_DIR=/data/cache" -e "SCHEDULER_IN_WEB=0" -e "DEBUG=False" -e "ALLOWED_HOSTS=localhost" -e "TZ=UTC""
exit /b 0

:update_channel
set "CHANNEL=%ALERTBOT_CHANNEL%"
if not defined CHANNEL set "CHANNEL=%CFG_CHANNEL%"
if defined CHANNEL exit /b 0
set "CHANNEL=stable"
echo %CFG_IMAGE%| findstr /r /c:":[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*-" >nul && set "CHANNEL=prerelease"
exit /b 0

:update_plan
for %%V in (RESULT REASON CURRENT_VERSION MESSAGE TARGET_VERSION TARGET_IMAGE FINAL_VERSION STEPS SEEN_REVISION SEEN_DIGEST) do set "PLAN_%%V="
call :pull_ref "%CFG_IMAGE%" || exit /b 1
set "SEEN_R="
set "SEEN_D="
if exist "%UPD%\seen.txt" for /f "usebackq tokens=1,* delims==" %%A in ("%UPD%\seen.txt") do (
  if "%%A"=="REVISION" set "SEEN_R=%%B"
  if "%%A"=="DIGEST" set "SEEN_D=%%B"
)
set "SEEN_ARGS="
if defined SEEN_R set "SEEN_ARGS=--seen-revision %SEEN_R% --seen-digest %SEEN_D%"
set "TEST_OPTS="
rem Tests only (honoured only by images built for the tests): a fake GitHub on a test network.
if defined ALERTBOT_UPDATE_TEST_NETWORK set "TEST_OPTS=--network %ALERTBOT_UPDATE_TEST_NETWORK%"
if defined ALERTBOT_UPDATE_TEST_API set "TEST_OPTS=%TEST_OPTS% -e "ALERTBOT_UPDATE_TEST_API=%ALERTBOT_UPDATE_TEST_API%""
for /f "usebackq tokens=1,* delims==" %%A in (`%ALERTBOT_DOCKER% run --rm --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges %TEST_OPTS% "%CFG_IMAGE%" update-plan --channel %CHANNEL% %SEEN_ARGS% 2^>nul`) do set "PLAN_%%A=%%B"
if defined PLAN_RESULT exit /b 0
echo.
echo  Couldn't check for updates: this folder's version didn't answer. Nothing was changed.
echo  Check the internet connection and try again. If it keeps happening, download the newest
echo  version from the releases page and run update.bat in its folder.
exit /b 1

:remember_seen
if not defined PLAN_SEEN_REVISION exit /b 0
> "%UPD%\seen.txt" echo REVISION=%PLAN_SEEN_REVISION%
>> "%UPD%\seen.txt" echo DIGEST=%PLAN_SEEN_DIGEST%
exit /b 0

:update_lock
%ALERTBOT_DOCKER% create --name %PROJECT%-update-lock --label alertbot.update=1 "%CFG_IMAGE%" true >nul 2>&1
if not errorlevel 1 exit /b 0
echo.
echo  Another update is running, or one was interrupted.
echo  If no other update window is open, type YES to go on.
set "TYPED="
if defined ALERTBOT_UPDATE_TAKE_LOCK set "TYPED=%ALERTBOT_UPDATE_TAKE_LOCK%"
if not defined ALERTBOT_UPDATE_TAKE_LOCK set /p "TYPED= Type YES to go on: "
if not "%TYPED%"=="YES" exit /b 1
%ALERTBOT_DOCKER% rm -f %PROJECT%-update-lock >nul 2>&1
%ALERTBOT_DOCKER% create --name %PROJECT%-update-lock --label alertbot.update=1 "%CFG_IMAGE%" true >nul 2>&1
exit /b %errorlevel%

:update_unlock
%ALERTBOT_DOCKER% rm -f %PROJECT%-update-lock >nul 2>&1
exit /b 0

:state
set "STATE=%~1"
> "%UPD%\state.txt" echo STATE=%STATE%
>> "%UPD%\state.txt" echo OLD_IMAGE=%OLD_IMAGE%
>> "%UPD%\state.txt" echo NEW_IMAGE=%NEW_IMAGE%
>> "%UPD%\state.txt" echo TARGET_VERSION=%TARGET_VERSION%
>> "%UPD%\state.txt" echo BACKUP_FILE=%BACKUP_FILE%
>> "%UPD%\state.txt" echo FP_TRIAL=%FP_TRIAL%
>> "%UPD%\state.txt" echo WAS_RUNNING=%WAS_RUNNING%
exit /b 0

:state_read
for %%V in (STATE OLD_IMAGE NEW_IMAGE TARGET_VERSION BACKUP_FILE FP_TRIAL WAS_RUNNING) do set "ST_%%V="
for /f "usebackq tokens=1,* delims==" %%A in ("%UPD%\state.txt") do set "ST_%%A=%%B"
exit /b 0

:pull_ref
%ALERTBOT_DOCKER% image inspect "%~1" >nul 2>&1
if not errorlevel 1 exit /b 0
echo  Downloading %~1 ...
%ALERTBOT_DOCKER% pull "%~1"
if errorlevel 1 goto :msg_get_failed
exit /b 0

:check_version
%ALERTBOT_DOCKER% run --rm --network none "%~1" describe 2>nul | findstr /c:"\"version\": \"%TARGET_VERSION%\"" >nul
if not errorlevel 1 exit /b 0
echo  The downloaded program isn't version %TARGET_VERSION%, so it wasn't installed.
exit /b 1

:stage_bundle
set "STAGE=%UPD%\staging"
if not exist "%STAGE%" mkdir "%STAGE%"
%ALERTBOT_DOCKER% rm -f %PROJECT%-update-files >nul 2>&1
%ALERTBOT_DOCKER% create --name %PROJECT%-update-files "%NEW_IMAGE%" >nul 2>&1
if errorlevel 1 goto :msg_stage_failed
%ALERTBOT_DOCKER% cp %PROJECT%-update-files:/app/bundle/windows "%STAGE%" >nul 2>&1
set "CP1=%errorlevel%"
%ALERTBOT_DOCKER% cp %PROJECT%-update-files:/app/bundle/windows.sha256 "%STAGE%\windows.sha256" >nul 2>&1
set "CP2=%errorlevel%"
%ALERTBOT_DOCKER% rm -f %PROJECT%-update-files >nul 2>&1
if not "%CP1%%CP2%"=="00" goto :msg_stage_failed
rem The list has LF line ends (findstr /e would miss them), so the loop notes the files it needs.
set "BAD="
set "HAS_UPDATE="
set "HAS_COMMON="
for /f "usebackq tokens=1,2" %%H in ("%STAGE%\windows.sha256") do (
  if "%%I"=="update.bat" set "HAS_UPDATE=1"
  if "%%I"=="docker/scripts/common.bat" set "HAS_COMMON=1"
  call :check_file %%H "%%I"
)
if not defined HAS_UPDATE goto :msg_stage_failed
if not defined HAS_COMMON goto :msg_stage_failed
if not defined BAD exit /b 0
echo  The new version's files didn't check out (%BAD%), so nothing was installed.
exit /b 1

:msg_stage_failed
echo  The new version's files couldn't be copied out of it, so nothing was installed.
exit /b 1

:check_file
set "NAME=%~2"
echo %NAME%| findstr /c:".." /c:":" >nul && set "BAD=an unsafe file name" && exit /b 0
set "WIN=%NAME:/=\%"
if not exist "%STAGE%\windows\%WIN%" set "BAD=%NAME% is missing" && exit /b 0
set "GOT="
for /f "skip=1 delims=" %%L in ('certutil -hashfile "%STAGE%\windows\%WIN%" SHA256 2^>nul') do if not defined GOT set "GOT=%%L"
if not defined GOT set "BAD=%NAME% couldn't be checked" && exit /b 0
set "GOT=%GOT: =%"
if /i not "%GOT%"=="%~1" set "BAD=%NAME% doesn't match its hash"
exit /b 0

:preflight
%RUNNER% "%NEW_IMAGE%" update-preflight
if not errorlevel 1 exit /b 0
echo  %TARGET_VERSION% can't take over your data (the reason is above). Nothing was changed.
exit /b 1

:backup_named
if not exist "backups" mkdir "backups"
set "BACKUP_FILE="
set "BACKUP_PATH="
for /f "usebackq tokens=5" %%P in (`%COMPOSE% run --rm --no-deps -v "%BOT_DIR%\backups:/export" web backup --label %~1 --export /export 2^>nul ^| findstr /b /c:"Database backed up to"`) do set "BACKUP_PATH=%%P"
if not defined BACKUP_PATH goto :msg_backup_failed
set "BACKUP_FILE=%BACKUP_PATH:/data/backups/=%"
echo  Backed up to %BACKUP_FILE% (a copy is in the backups folder).
exit /b 0

:save_previous
set "PREV=%UPD%\previous"
if not exist "%PREV%\docker\scripts" mkdir "%PREV%\docker\scripts"
for %%F in (setup.bat start.bat update.bat stop.bat backup.bat restore.bat docker-compose.yml README.txt README.zh-TW.txt LICENSE THIRD_PARTY_NOTICES.txt docker\compose.env docker\scripts\common.bat) do if exist "%%F" copy /y "%%F" "%PREV%\%%F" >nul || exit /b 1
exit /b 0

:restore_previous
for %%F in (setup.bat start.bat update.bat stop.bat backup.bat restore.bat docker-compose.yml README.txt README.zh-TW.txt LICENSE THIRD_PARTY_NOTICES.txt docker\compose.env docker\scripts\common.bat) do if exist "%UPD%\previous\%%F" copy /y "%UPD%\previous\%%F" "%%F" >nul || exit /b 1
exit /b 0

:swap_files
for /f "usebackq tokens=1,2" %%H in ("%UPD%\staging\windows.sha256") do call :swap_one "%%I" || exit /b 1
findstr /v /b /c:"ALERTBOT_IMAGE=" "docker\compose.env" > "docker\compose.env.new"
>> "docker\compose.env.new" echo ALERTBOT_IMAGE=%NEW_IMAGE%
move /y "docker\compose.env.new" "docker\compose.env" >nul || exit /b 1
exit /b 0

:swap_one
set "WIN=%~1"
set "WIN=%WIN:/=\%"
copy /y "%UPD%\staging\windows\%WIN%" "%WIN%" >nul
exit /b %errorlevel%

:trial
%RUNNER% "%NEW_IMAGE%" trial
if not errorlevel 1 exit /b 0
echo  Updating your data with %TARGET_VERSION% failed in the trial run (the reason is above).
exit /b 1

:fingerprint
set "%~1="
for /f "usebackq tokens=1,* delims==" %%A in (`%RUNNER% "%NEW_IMAGE%" fingerprint 2^>nul`) do if "%%A"=="FINGERPRINT" set "%~1=%%B"
if not defined %~1 exit /b 1
exit /b 0

:restore_backup
%RUNNER% "%OLD_IMAGE%" restore "/data/backups/%BACKUP_FILE%"
exit /b %errorlevel%

:running_is
%ALERTBOT_CURL% -sS --max-time 5 "%URL%api/health/" 2>nul | findstr /c:"%~1" >nul
exit /b %errorlevel%

rem start.bat, setup.bat, restore.bat: not while an update is part-way through.
:check_unfinished_update
if not exist "docker\update\state.txt" exit /b 0
set "ST_STATE="
for /f "usebackq tokens=1,* delims==" %%A in ("docker\update\state.txt") do if "%%A"=="STATE" set "ST_STATE=%%B"
for %%S in (DONE ABORTED ROLLED_BACK NEEDS_USER) do if "%ST_STATE%"=="%%S" exit /b 0
echo.
echo  An update didn't finish (it stopped at %ST_STATE%). Run update.bat first: it finishes it or
echo  undoes it, safely. Nothing was started or changed.
exit /b 1

