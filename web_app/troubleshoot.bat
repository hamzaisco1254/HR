@echo off
echo ========================================
echo Web App Troubleshooting
echo ========================================
echo.

echo Checking Python installation...
python --version
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8+ from https://python.org
    pause
    exit /b 1
)
echo.

echo Checking pip...
pip --version
if %errorlevel% neq 0 (
    echo ERROR: pip is not available
    pause
    exit /b 1
)
echo.

echo Checking Flask...
python -c "import flask; print('Flask version:', flask.__version__)" 2>nul
if %errorlevel% neq 0 (
    echo Flask not installed. Installing...
    pip install Flask
    if %errorlevel% neq 0 (
        echo ERROR: Failed to install Flask
        pause
        exit /b 1
    )
) else (
    echo Flask is installed.
)
echo.

echo Checking other dependencies...
python -c "import pandas, openpyxl, requests, docx; print('All dependencies OK')" 2>nul
if %errorlevel% neq 0 (
    echo Installing missing dependencies...
    pip install pandas openpyxl requests python-docx
    if %errorlevel% neq 0 (
        echo ERROR: Failed to install dependencies
        pause
        exit /b 1
    )
) else (
    echo All dependencies are installed.
)
echo.

echo Checking web app files...
if not exist "app.py" (
    echo ERROR: app.py not found in current directory
    pause
    exit /b 1
)
if not exist "templates" (
    echo ERROR: templates directory not found
    pause
    exit /b 1
)
echo All files present.
echo.

echo ========================================
echo Everything looks good! Try running:
echo start_web_app.bat
echo ========================================

pause