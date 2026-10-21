@echo off
rem For developers: first-time setup WITHOUT Docker, using a Python virtual environment (venv).
rem Most people should use setup.bat / start.bat in the main folder instead (Docker).
rem Don't run both versions at once: posts would be announced twice.
cd /d "%~dp0.."
echo ============================================
echo  Bluesky Discord Alert Bot - First Time Setup
echo ============================================
echo.

echo [1/3] Creating virtual environment...
python -m venv venv
call venv\Scripts\activate

echo.
echo [2/3] Installing required packages...
pip install -r requirements-dev.txt

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
echo  Run dev\start-without-docker.bat to launch the bot.
echo  For the control panel, build it once: in frontend\ run npm ci, then npm run build.
echo ============================================
pause
