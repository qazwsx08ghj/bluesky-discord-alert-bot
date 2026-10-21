@echo off
rem Make a backup of the bot's data now. The copy goes into the backups folder next to this file.
setlocal
title Bluesky Discord Alert Bot - Backup
set "COMMON=%~dp0docker\scripts\common.bat"
call "%COMMON%" :init
call "%COMMON%" :check_docker || goto :failed
call "%COMMON%" :is_set_up
if errorlevel 1 goto :nothing
echo  Backing up the bot's data...
call "%COMMON%" :backup manual || goto :failed
echo.
echo  Done. The backup holds your settings, including passwords, so keep it private.
call "%COMMON%" :pause_end
exit /b 0

:nothing
echo  The bot hasn't been set up on this computer yet, so there is nothing to back up.
call "%COMMON%" :pause_end
exit /b 0

:failed
call "%COMMON%" :pause_end
exit /b 1
