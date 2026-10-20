@echo off
echo ============================================
echo  Bluesky Discord Alert Bot - First Time Setup
echo ============================================
echo.

echo [1/4] Creating virtual environment...
python -m venv venv
call venv\Scripts\activate

echo.
echo [2/4] Installing required packages...
pip install -r requirements.txt

echo.
echo [3/4] Setting up database...
python manage.py migrate --fake-initial

echo.
echo [4/4] Creating admin account...
echo You will be asked to create a username and password for the admin panel.
echo.
python manage.py createsuperuser

echo.
echo ============================================
echo  Setup complete!
echo  Run start.bat to launch the bot.
echo ============================================
pause
