@echo off
rem Update the bot to the newest version, keeping all accounts, settings and history.
rem It checks GitHub for a newer version (stable, or test versions for a test version's folder),
rem says when there is none, and otherwise installs it: a backup first, a trial run, and it goes
rem back by itself if the new version doesn't work. See docs/DOCKER.md, section 3.
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

rem The rest runs from a copy, because an update replaces this file while it runs. On one line,
rem so nothing more is read from this file once it has started.
call "%COMMON%" :copy_for_update || goto :failed
call "%UPDATE_COMMON%" :update_main & if errorlevel 1 (exit /b 1) else exit /b 0

:first_time
echo  The bot hasn't been set up on this computer yet. Starting the setup instead...
echo.
call "%~dp0setup.bat"
exit /b %errorlevel%

:failed
echo.
echo  The update didn't finish. Nothing was deleted.
call "%COMMON%" :pause_end
exit /b 1
