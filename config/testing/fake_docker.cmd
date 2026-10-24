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
rem   The updater (update.bat):
rem   FAKE_PLAN         what "update-plan" answers: update, up_to_date, blocked; unset: nothing
rem   FAKE_STEPS, FAKE_FINAL   for FAKE_PLAN=update: steps in the path (1), and the last version
rem   FAKE_TARGET_VERSION   what "describe" of the new image says (default 1.0.0-rc.4)
rem   FAKE_IMAGE_MISSING=<text>  "image inspect" of an image named with <text> fails (so the image is pulled); FAKE_PULL_FAIL=1 the pull fails
rem   FAKE_BUNDLE_DIR   where "cp" takes the new version's files from (windows\, windows.sha256)
rem   FAKE_CP_FAIL=1    "cp" fails
rem   FAKE_PREFLIGHT, FAKE_TRIAL, FAKE_RESTORE   exit codes of those steps (default 0)
rem   FAKE_FP_LATER     what "fingerprint" answers after its first answer (default: the same)
rem   FAKE_LOCK_HELD=1  the update lock exists until it is removed ("rm -f ...-update-lock")
>>"%FAKE_DOCKER_LOG%" echo docker %*
if "%~1"=="info" goto :info
if "%~1"=="volume" goto :volume
if "%~1"=="compose" goto :compose
if "%~1"=="ps" goto :ps
if "%~1"=="inspect" goto :inspect
if "%~1"=="image" if "%~2"=="inspect" goto :image_inspect
if "%~1"=="run" goto :run
if "%~1"=="create" goto :create
if "%~1"=="cp" goto :cp
if "%~1"=="pull" goto :pull
if "%~1"=="rm" goto :rm
exit /b 0

:run
rem A "-e NAME=value" that reached docker as "NAME value" (a bare "=" inside for /f) is a bug.
for %%E in (DATABASE_PATH ALERTBOT_UPDATE_TEST_API) do echo %* | findstr /c:"%%E" >nul && (echo %* | findstr /c:"%%E=" >nul || (echo fake docker: -e %%E lost its "=" 1>&2& exit /b 125))
echo %* | findstr /c:" update-plan" >nul && goto :plan
echo %* | findstr /c:" describe" >nul && goto :describe
echo %* | findstr /c:" update-preflight" >nul && goto :preflight
echo %* | findstr /c:" trial" >nul && goto :trial
echo %* | findstr /c:" fingerprint" >nul && goto :fingerprint
echo %* | findstr /c:" restore " >nul && goto :restore_run
exit /b 0

:plan
if not defined FAKE_PLAN exit /b 0
if "%FAKE_PLAN%"=="update" goto :plan_update
echo RESULT=%FAKE_PLAN%
echo REASON=%FAKE_PLAN%
echo CURRENT_VERSION=1.0.0-rc.3
if "%FAKE_PLAN%"=="up_to_date" echo MESSAGE=You have the newest version 1.0.0-rc.3.
if "%FAKE_PLAN%"=="blocked" echo MESSAGE=Couldn't check for updates: GitHub couldn't be reached. Nothing was changed.
if not "%FAKE_PLAN%"=="blocked" echo SEEN_REVISION=2
if not "%FAKE_PLAN%"=="blocked" echo SEEN_DIGEST=2222222222222222222222222222222222222222222222222222222222222222
exit /b 0

:plan_update
set "STEPS=1"
if defined FAKE_STEPS set "STEPS=%FAKE_STEPS%"
set "FINAL=1.0.0-rc.4"
if defined FAKE_FINAL set "FINAL=%FAKE_FINAL%"
echo RESULT=update
echo REASON=update
echo CURRENT_VERSION=1.0.0-rc.3
echo MESSAGE=%FINAL% is available - you have 1.0.0-rc.3.
echo TARGET_VERSION=1.0.0-rc.4
echo TARGET_IMAGE=ghcr.io/qazwsx08ghj/bluesky-discord-alert-bot:1.0.0-rc.4@sha256:4444444444444444444444444444444444444444444444444444444444444444
echo FINAL_VERSION=%FINAL%
echo STEPS=%STEPS%
echo SEEN_REVISION=3
echo SEEN_DIGEST=3333333333333333333333333333333333333333333333333333333333333333
exit /b 0

:describe
set "V=1.0.0-rc.4"
if defined FAKE_TARGET_VERSION set "V=%FAKE_TARGET_VERSION%"
echo {
echo   "version": "%V%",
echo   "problems": []
echo }
exit /b 0

:preflight
if defined FAKE_PREFLIGHT echo fake docker: preflight failed
if defined FAKE_PREFLIGHT exit /b %FAKE_PREFLIGHT%
echo Preflight passed.
exit /b 0

:trial
if defined FAKE_TRIAL echo fake docker: a migration failed
if defined FAKE_TRIAL exit /b %FAKE_TRIAL%
echo Self-test passed.
exit /b 0

:fingerprint
if exist "%FAKE_DOCKER_LOG%.fp" goto :fingerprint_later
> "%FAKE_DOCKER_LOG%.fp" echo seen
echo FINGERPRINT=aaaaaaaaaaaaaaaaaaaa
exit /b 0
:fingerprint_later
if defined FAKE_FP_LATER echo FINGERPRINT=%FAKE_FP_LATER%
if not defined FAKE_FP_LATER echo FINGERPRINT=aaaaaaaaaaaaaaaaaaaa
exit /b 0

:restore_run
if defined FAKE_RESTORE exit /b %FAKE_RESTORE%
echo Restored.
exit /b 0

:create
echo %* | findstr /c:"-update-lock" >nul || exit /b 0
if not defined FAKE_LOCK_HELD exit /b 0
if exist "%FAKE_DOCKER_LOG%.lockfree" exit /b 0
exit /b 1

:rm
echo %* | findstr /c:"-update-lock" >nul && > "%FAKE_DOCKER_LOG%.lockfree" echo free
exit /b 0

:cp
if defined FAKE_CP_FAIL exit /b 1
echo %* | findstr /c:"windows.sha256" >nul && goto :cp_sums
xcopy /e /i /y /q "%FAKE_BUNDLE_DIR%\windows" "%~3\windows" >nul
exit /b %errorlevel%
:cp_sums
copy /y "%FAKE_BUNDLE_DIR%\windows.sha256" "%~3" >nul
exit /b %errorlevel%

:pull
if defined FAKE_PULL_FAIL exit /b 1
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
if defined FAKE_IMAGE_MISSING echo %* | findstr /c:"%FAKE_IMAGE_MISSING%" >nul && exit /b 1
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
if not defined FAKE_FAIL_ON goto :compose_ok
echo %* | findstr /c:"%FAKE_FAIL_ON%" >nul
if errorlevel 1 goto :compose_ok
echo fake docker: failing on purpose
exit /b 1

:compose_ok
echo %* | findstr /c:"backup --label before-update" >nul || exit /b 0
echo Database backed up to /data/backups/db-before-update-20261024-000000-000001.sqlite3 (checked: complete and undamaged)
exit /b 0

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
