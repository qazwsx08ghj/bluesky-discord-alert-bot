@echo off
rem Fake "docker" for config/tests_deploy.py: logs every call and answers per scenario.
rem   FAKE_DOCKER_LOG   file that gets one line per call
rem   FAKE_ENGINE=down  "docker info" fails (Docker Desktop not running), until the file
rem                     FAKE_ENGINE_UP_FILE exists (fake_restart.cmd creates it)
rem   FAKE_OSTYPE       what "docker info --format" prints (default linux)
rem   FAKE_COMPOSE=missing   "docker compose version" fails
rem   FAKE_VOLUME=missing    "docker volume inspect" fails (not set up yet)
rem   FAKE_FAIL_ON      a word; any compose call containing it fails
>>"%FAKE_DOCKER_LOG%" echo docker %*
if "%~1"=="info" goto :info
if "%~1"=="volume" goto :volume
if "%~1"=="compose" goto :compose
exit /b 0

:info
if "%FAKE_ENGINE%"=="down" if not exist "%FAKE_ENGINE_UP_FILE%" exit /b 1
if not "%~2"=="--format" exit /b 0
if defined FAKE_OSTYPE (echo %FAKE_OSTYPE%) else (echo linux)
exit /b 0

:volume
if "%FAKE_VOLUME%"=="missing" exit /b 1
exit /b 0

:compose
if "%~2"=="version" if "%FAKE_COMPOSE%"=="missing" exit /b 1
if not defined FAKE_FAIL_ON exit /b 0
echo %* | findstr /c:"%FAKE_FAIL_ON%" >nul
if errorlevel 1 exit /b 0
echo fake docker: failing on purpose
exit /b 1
