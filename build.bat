@echo off
cd /d "%~dp0"

echo ================================
echo WATERAI BUILD
echo ================================

if not exist app.py (
    echo ERROR: Khong thay app.py
    echo Hay dat build.bat cung thu muc voi app.py
    pause
    exit /b
)

if not exist requirements.txt (
    echo Tao requirements.txt mac dinh...
    echo flask> requirements.txt
    echo waitress>> requirements.txt
    echo opencv-python>> requirements.txt
    echo numpy>> requirements.txt
    echo asyncua>> requirements.txt
    echo pyinstaller>> requirements.txt
)

if not exist .venv (
    python -m venv .venv
)

call .venv\Scripts\activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

pyinstaller ^
  --onedir ^
  --name WaterAI ^
  --clean ^
  --noconfirm ^
  --collect-all cv2 ^
  --collect-all numpy ^
  --collect-all asyncua ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --add-data "config.json;." ^
  --add-data "web.config;." ^
  app.py

echo.
echo Build xong.
echo File EXE tai: dist\WaterAI\WaterAI.exe
pause