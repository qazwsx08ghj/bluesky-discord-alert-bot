@echo off
rem Fake "docker" for config/tests_deploy.py: logs every call and answers per scenario.
rem   FAKE_DOCKER_LOG   file that gets one line per call
rem   FAKE_ENGINE=down  "docker info" fails (Docker Desktop not running), until the file
rem                     FAKE_ENGINE_UP_FILE exists (fake_restart.cmd creates it)
rem   FAKE_OSTYPE       what "docker info --format" prints (default linux)
rem   FAKE_COMPOSE=missing   "docker compose version" fails
rem   FAKE_VOLUME=missing    "docker volume inspect" fails (not set up yet)
rem   FAKE_FAIL_ON      a word; any compose call containing it fails
rem   FAKE_COMPAT=newer the compatibility check answers 3 (the data is from a newer version)
rem   FAKE_LABELS       what "docker ps --format {{.Labels}}" prints (the containers' labels)
rem   FAKE_COMPOSE_VERSION   what "docker compose version --short" prints (default 2.23.0)
rem   FAKE_COMPOSE_SHORT=fail "docker compose version --short" fails
rem   FAKE_VOLUME_USERS what "docker ps --filter volume=..." prints (containers using the data)
rem   FAKE_VOLUME_PS=fail    that "docker ps --filter volume=..." fails (Docker can't answer)
rem   FAKE_REPORT       the restore report: loss (exit 10), newer (3), bad (1), nocode (no IMPACT line);
rem                     default: nothing lost. It prints IMPACT=fakecode.
rem   FAKE_RESTORE=changed   the restore finds the data changed since the report (exit 11)
rem   FAKE_OWN_IDS      the bot's own running containers, separated by spaces ("compose ps -q")
rem   FAKE_OWN_PS=fail  that "compose ps -q" fails
rem   FAKE_RUNNING_IMAGE, FAKE_NEW_IMAGE   the image the running containers run ("inspect"), and
rem                     the image this folder would start ("image inspect")
>>"%FAKE_DOCKER_LOG%" echo docker %*
if "%~1"=="info" goto :info
if "%~1"=="volume" goto :volume
if "%~1"=="compose" goto :compose
if "%~1"=="ps" goto :ps
if "%~1"=="inspect" goto :inspect
if "%~1"=="image" if "%~2"=="inspect" goto :image_inspect
exit /b 0

:ps
echo %* | findstr /c:"volume=" >nul
if not errorlevel 1 goto :volume_users
if defined FAKE_LABELS echo %FAKE_LABELS%
exit /b 0

:volume_users
if "%FAKE_VOLUME_PS%"=="fail" exit /b 1
if defined FAKE_VOLUME_USERS echo %FAKE_VOLUME_USERS%
exit /b 0

:inspect
if defined FAKE_RUNNING_IMAGE echo %FAKE_RUNNING_IMAGE%
exit /b 0

:image_inspect
if defined FAKE_NEW_IMAGE echo %FAKE_NEW_IMAGE%
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
if "%~2"=="version" if "%~3"=="--short" goto :compose_version
echo %* | findstr /c:"restore --report" >nul
if not errorlevel 1 goto :report
echo %* | findstr /c:"restore --expect" >nul
if not errorlevel 1 goto :restore
echo %*| findstr /e /c:"ps -q" >nul
if not errorlevel 1 goto :own_ids
if not "%FAKE_COMPAT%"=="newer" goto :fail_on
echo %* | findstr /c:"check_compat" >nul
if not errorlevel 1 exit /b 3
:fail_on
if not defined FAKE_FAIL_ON exit /b 0
echo %* | findstr /c:"%FAKE_FAIL_ON%" >nul
if errorlevel 1 exit /b 0
echo fake docker: failing on purpose
exit /b 1

:compose_version
if "%FAKE_COMPOSE_SHORT%"=="fail" exit /b 1
if defined FAKE_COMPOSE_VERSION (echo %FAKE_COMPOSE_VERSION%) else (echo 2.23.0)
exit /b 0

:own_ids
if "%FAKE_OWN_PS%"=="fail" exit /b 1
for %%I in (%FAKE_OWN_IDS%) do echo %%I
exit /b 0

:restore
if "%FAKE_RESTORE%"=="changed" (
  echo The bot's data changed after the check you confirmed.
  exit /b 11
)
goto :fail_on

:report
if "%FAKE_REPORT%"=="loss" (
  echo Putting this backup back would undo what changed since it was made:
  echo   - 2 posts the bot noticed since the backup would be forgotten.
  echo IMPACT=fakecode
  exit /b 10
)
if "%FAKE_REPORT%"=="newer" exit /b 3
if "%FAKE_REPORT%"=="bad" (
  echo fake docker: this backup is damaged
  exit /b 1
)
echo Nothing you set up and no notification history would be lost.
if not "%FAKE_REPORT%"=="nocode" echo IMPACT=fakecode
exit /b 0
