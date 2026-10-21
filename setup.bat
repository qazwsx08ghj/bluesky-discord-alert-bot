@echo off
echo ============================================
echo  Bluesky Discord Alert Bot - First Time Setup
echo ============================================
echo.

echo [1/3] Creating virtual environment...
python -m venv venv
call venv\Scripts\activate

echo.
echo [2/3] Installing required packages...
pip install -r requirements.txt

echo.
echo [3/3] Setting up database...
python manage.py backup_db --if-pending
if errorlevel 1 (
  echo The database backup failed, so the database was not changed.
  pause
  exit /b 1
)
python manage.py migrate --fake-initial

echo.
echo ============================================
echo  Setup complete!
echo  Run start.bat to launch the bot.
echo ============================================
pause
