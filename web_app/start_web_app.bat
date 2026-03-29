@echo off
echo ========================================
echo HR Document Generator - Web Version
echo ========================================
echo.

echo Step 1: Installing dependencies...
echo This may take a few minutes...
pip install Flask==2.3.3 python-docx==0.8.11 pandas==2.2.0 openpyxl==3.11.0 requests==2.31.0 Werkzeug==2.3.7
echo.

echo Step 2: Checking installation...
python -c "import flask; print('Flask version:', flask.__version__)" 2>nul
if %errorlevel% neq 0 (
    echo ERROR: Flask installation failed!
    echo Please run: pip install Flask
    pause
    exit /b 1
)
echo.

echo Step 3: Starting web server...
echo The web app will be available at: http://localhost:5000
echo Press Ctrl+C to stop the server
echo.

python app.py

pause