@echo off
echo Building HR Document Generator Executable...
echo.

echo Step 1: Installing/Updating dependencies...
pip install -r requirements.txt
echo.

echo Step 2: Creating directories if they don't exist...
if not exist "output" mkdir output
if not exist "config" mkdir config
echo.

echo Step 3: Building executable with PyInstaller...
pyinstaller --clean HR_Document_Generator.spec
echo.

echo Step 4: Copying additional files...
if exist "dist\HR_Document_Generator.exe" (
    echo Executable created successfully!
    echo Location: dist\HR_Document_Generator.exe
    echo.
    echo You can now share this executable file with others.
    echo They can run it on any Windows computer without installing Python.
) else (
    echo Build failed. Check the output above for errors.
)
echo.
pause