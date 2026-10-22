@echo off
rem Fake "curl" for config/tests_deploy.py. FAKE_CURL_EXIT: exit code (0 = the panel answers).
>>"%FAKE_DOCKER_LOG%" echo curl %*
if defined FAKE_CURL_BODY echo %FAKE_CURL_BODY%
if defined FAKE_CURL_EXIT exit /b %FAKE_CURL_EXIT%
exit /b 0
