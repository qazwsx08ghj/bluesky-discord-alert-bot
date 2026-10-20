@echo off
echo Starting Bluesky Discord Alert Bot...
echo.
call venv\Scripts\activate
echo Updating the database (safe to run every time)...
python manage.py migrate --fake-initial
if errorlevel 1 (
  echo.
  echo The database update failed, so the bot was not started.
  echo No data was deleted. Please share the error above when asking for help.
  pause
  exit /b 1
)
python manage.py runserver
pause
