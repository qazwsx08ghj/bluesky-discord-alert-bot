@echo off
rem Stop the bot. Nothing is deleted; start.bat starts it again.
setlocal
title Bluesky Discord Alert Bot - Stop
set "COMMON=%~dp0docker\scripts\common.bat"
call "%COMMON%" :init
call "%COMMON%" :check_docker || goto :failed
echo  Stopping the bot...
%COMPOSE% stop
if errorlevel 1 goto :failed
echo.
echo  The bot is stopped. No posts are checked or sent until you double-click start.bat.
echo  Your accounts, settings and history are kept.
call "%COMMON%" :pause_end
exit /b 0

:failed
echo.
echo  The bot couldn't be stopped. Nothing was deleted.
call "%COMMON%" :pause_end
exit /b 1
