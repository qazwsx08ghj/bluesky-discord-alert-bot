@echo off
echo Starting Bluesky Discord Alert Bot...
echo.
call venv\Scripts\activate
echo Updating the database (safe to run every time)...
python manage.py backup_db --if-pending
if errorlevel 1 (
  echo.
  echo The database backup failed, so the database was not changed and the bot was not started.
  pause
  exit /b 1
)
python manage.py migrate --fake-initial
if errorlevel 1 (
  echo.
  echo The database update failed, so the bot was not started.
  echo No data was deleted. Please share the error above when asking for help.
  pause
  exit /b 1
)
rem Local only: the control panel listens on this computer (127.0.0.1) and nowhere else.
python manage.py runserver 127.0.0.1:8000
pause
