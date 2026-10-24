@echo off
rem Put back a backup of the bot's data: accounts, settings, channels and history.
rem The current data is backed up first, so a restore can itself be undone.
rem Before anything changes, it says what the restore would undo. If that includes anything you
rem set up or any notification history, RESTORE must be typed to go on.
setlocal
title Bluesky Discord Alert Bot - Restore a backup
set "COMMON=%~dp0docker\scripts\common.bat"
echo ============================================================
echo  Bluesky to Discord Alert Bot - restore a backup
echo ============================================================
echo.
call "%COMMON%" :init
call "%COMMON%" :check_docker || goto :failed
call "%COMMON%" :check_unfinished_update || goto :failed
call "%COMMON%" :is_set_up
if errorlevel 1 goto :nothing

echo.
echo [1/4] Looking for backups...
call "%COMMON%" :export_backups || goto :failed
set /a COUNT=0
for /f "delims=" %%F in ('dir /b /a-d /o-d "backups\*.sqlite3" 2^>nul') do call :add "%%F"
if %COUNT%==0 goto :none
echo.
echo  Backups, newest first:
for /l %%N in (1,1,%COUNT%) do call :show %%N
echo.
set "CHOICE="
if defined ALERTBOT_RESTORE_CHOICE set "CHOICE=%ALERTBOT_RESTORE_CHOICE%"
if not defined CHOICE set /p "CHOICE= Type the number of the backup to put back, then press Enter (just Enter cancels): "
if not defined CHOICE goto :cancelled
set "FILE="
for /l %%N in (1,1,%COUNT%) do if "%CHOICE%"=="%%N" call set "FILE=%%FILE_%%N%%"
if not defined FILE goto :bad_choice

echo.
echo  This puts back %FILE%.
echo  The bot's current accounts, settings and history are replaced by the ones in that backup.
echo  A backup of the current data is made first, so you can go back.
echo.
echo  Checking what that would change (nothing is changed yet)...
call "%COMMON%" :restore_report "%FILE%"
set "REPORT=%errorlevel%"
if "%REPORT%"=="10" goto :confirm_loss
if "%REPORT%"=="3" goto :newer_backup
if not "%REPORT%"=="0" goto :unusable
if defined ALERTBOT_ASSUME_YES goto :go
choice /c YN /m " Continue"
if errorlevel 2 goto :cancelled
goto :go

:confirm_loss
echo.
echo  To put the backup back anyway, type RESTORE in capital letters and press Enter.
echo  Anything else cancels, and nothing is changed.
set "TYPED="
if defined ALERTBOT_RESTORE_CONFIRM set "TYPED=%ALERTBOT_RESTORE_CONFIRM%"
if not defined ALERTBOT_RESTORE_CONFIRM set /p "TYPED= Type RESTORE to continue: "
if not "%TYPED%"=="RESTORE" goto :cancelled
:go

echo.
echo [2/4] Stopping the bot...
call "%COMMON%" :stop || goto :failed
call "%COMMON%" :ensure_data_free || goto :failed_stopped

echo.
echo [3/4] Backing up the current data, then putting back %FILE%...
call "%COMMON%" :backup before-restore || goto :failed_stopped
call "%COMMON%" :restore_file "%FILE%" "%IMPACT%" || goto :failed_stopped

echo.
echo [4/4] Starting the bot again...
call "%COMMON%" :up || goto :failed
call "%COMMON%" :wait_ready || goto :failed
call "%COMMON%" :open_browser
echo.
echo  Done. The bot is running with the data from %FILE%.
call "%COMMON%" :pause_end
exit /b 0

:add
set /a COUNT+=1
set "FILE_%COUNT%=%~1"
set "WHEN_%COUNT%=%~t1"
exit /b 0

:show
call echo   %1. %%FILE_%1%%
exit /b 0

:nothing
echo  The bot hasn't been set up on this computer yet, so there is nothing to restore.
call "%COMMON%" :pause_end
exit /b 0

:none
echo  No backups were found. backup.bat and update.bat make them.
call "%COMMON%" :pause_end
exit /b 0

:bad_choice
echo  There is no backup number %CHOICE%. Nothing was changed.
call "%COMMON%" :pause_end
exit /b 1

:newer_backup
echo.
echo  That backup was made by a newer version of the bot than the one in this folder, so this
echo  version can't use it. Nothing was changed. Use restore.bat in the newer version's folder.
call "%COMMON%" :pause_end
exit /b 1

:unusable
echo.
echo  That backup can't be put back (the reason is above). Nothing was changed.
call "%COMMON%" :pause_end
exit /b 1

:cancelled
echo  Cancelled. Nothing was changed.
call "%COMMON%" :pause_end
exit /b 0

:failed_stopped
echo  The bot's data is unchanged. Starting it again...
call "%COMMON%" :up
:failed
echo.
echo  The restore didn't finish. Nothing was deleted.
call "%COMMON%" :pause_end
exit /b 1
