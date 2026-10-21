@echo off
rem Fake Docker Desktop restart for config/tests_deploy.py (the real one is never touched).
rem Logs "restart-desktop". Afterwards the fake engine answers, unless the argument is "broken".
>>"%FAKE_DOCKER_LOG%" echo restart-desktop
if "%~1"=="broken" exit /b 0
>"%FAKE_ENGINE_UP_FILE%" echo up
exit /b 0
