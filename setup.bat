@echo off
rem First-time setup with Docker Desktop. Safe to run again: it never deletes data.
setlocal
title Bluesky Discord Alert Bot - Setup
set "COMMON=%~dp0docker\scripts\common.bat"
echo ============================================================
echo  Bluesky to Discord Alert Bot - first-time setup
echo ============================================================
echo.
call "%COMMON%" :init

echo [1/5] Checking Docker Desktop...
call "%COMMON%" :check_docker || goto :failed
call "%COMMON%" :check_unfinished_update || goto :failed
call "%COMMON%" :check_port || goto :failed

echo.
echo [2/5] Preparing the bot...
call "%COMMON%" :get_image || goto :failed

echo.
echo [3/5] Looking for data from an earlier version...
call "%COMMON%" :import_old || goto :failed

echo.
echo [4/5] Starting the bot...
rem An existing install: is its data from a newer version than this folder's?
call "%COMMON%" :is_set_up
if not errorlevel 1 call "%COMMON%" :check_compat || goto :failed
call "%COMMON%" :previous_folder
call "%COMMON%" :prepare_start || goto :failed
call "%COMMON%" :up || goto :failed

echo.
echo [5/5] Opening the control panel...
call "%COMMON%" :wait_ready || goto :failed
call "%COMMON%" :open_browser
call "%COMMON%" :old_folder_note
echo.
echo  Setup is complete. The bot now runs in Docker Desktop, also after a restart of the
echo  computer, as long as Docker Desktop starts when you sign in.
echo  Next time, double-click start.bat to open the control panel.
call "%COMMON%" :pause_end
exit /b 0

:failed
echo.
echo  Setup didn't finish. Nothing was deleted, so it's safe to run setup.bat again.
call "%COMMON%" :pause_end
exit /b 1
