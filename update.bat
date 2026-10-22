@echo off
rem Update the bot to the newest version, keeping all accounts, settings and history.
rem A backup is made first; if anything fails before the restart, the current version keeps running.
setlocal
title Bluesky Discord Alert Bot - Update
set "COMMON=%~dp0docker\scripts\common.bat"
echo ============================================================
echo  Bluesky to Discord Alert Bot - update
echo ============================================================
echo.
call "%COMMON%" :init
call "%COMMON%" :check_docker || goto :failed

call "%COMMON%" :is_set_up
if errorlevel 1 goto :first_time

echo.
echo [1/4] Backing up your data...
call "%COMMON%" :backup before-update || goto :failed

echo.
echo [2/4] Getting the new version...
set "BUILD_ARGS=--pull"
call "%COMMON%" :get_image || goto :keep_running
call "%COMMON%" :check_compat || goto :failed
call "%COMMON%" :previous_folder

echo.
echo [3/4] Restarting the bot with the new version...
call "%COMMON%" :up || goto :failed_after_backup

echo.
echo [4/4] Checking that everything works...
call "%COMMON%" :wait_ready || goto :failed_after_backup
call "%COMMON%" :tidy_images
call "%COMMON%" :open_browser
echo.
echo  The update is complete. Your accounts, settings and history were kept.
call "%COMMON%" :old_folder_note
call "%COMMON%" :pause_end
exit /b 0

:first_time
echo  The bot hasn't been set up on this computer yet. Starting the setup instead...
echo.
call "%~dp0setup.bat"
exit /b %errorlevel%

:keep_running
echo  The current version keeps running.
call "%COMMON%" :up
goto :failed

:failed_after_backup
echo.
echo  A backup from just before the update is in the backups folder next to this file.
echo  See "If an update goes wrong" in docs\DOCKER.md, or ask for help with logs\problem-report.txt.
:failed
echo.
echo  The update didn't finish. Nothing was deleted.
call "%COMMON%" :pause_end
exit /b 1
