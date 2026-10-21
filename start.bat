@echo off
rem Start the bot (if it isn't running) and open the control panel.
setlocal
title Bluesky Discord Alert Bot
set "COMMON=%~dp0docker\scripts\common.bat"
echo  Bluesky to Discord Alert Bot
echo.
call "%COMMON%" :init
call "%COMMON%" :check_docker || goto :failed

call "%COMMON%" :is_set_up
if errorlevel 1 goto :first_time

call "%COMMON%" :check_port || goto :failed
echo  Starting the bot...
call "%COMMON%" :up || goto :failed
call "%COMMON%" :wait_ready || goto :failed
call "%COMMON%" :open_browser
echo.
echo  The bot is running. You can close this window: the bot keeps running in Docker Desktop.
call "%COMMON%" :pause_end
exit /b 0

:first_time
echo  The bot hasn't been set up on this computer yet. Starting the setup...
echo.
call "%~dp0setup.bat"
exit /b %errorlevel%

:failed
echo.
echo  The bot couldn't be started. Nothing was deleted.
call "%COMMON%" :pause_end
exit /b 1
