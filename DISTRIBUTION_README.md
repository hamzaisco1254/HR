# HR Document Generator - Distribution Guide

## Making the App Shareable

Your HR Document Generator can be packaged as a standalone executable that runs on any Windows computer without requiring Python installation.

### Quick Build (Recommended)

1. **Install build dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the build script:**
   Double-click `build_app.bat` or run:
   ```bash
   .\build_app.bat
   ```

3. **Find the executable:**
   The executable will be created at: `dist/HR_Document_Generator.exe`

### Manual Build

If you prefer more control:

```bash
pyinstaller --clean HR_Document_Generator.spec
```

### Sharing the App

1. **Copy the executable:** `dist/HR_Document_Generator.exe`
2. **Share with others:** Send the `.exe` file via email, USB drive, or cloud storage
3. **Recipients can run it immediately** on any Windows 10/11 computer

### What Gets Included

The executable includes:
- ✅ All Python code and dependencies
- ✅ PyQt6 GUI framework
- ✅ Document templates
- ✅ Configuration files
- ✅ Required libraries (python-docx, pandas, etc.)

### System Requirements for Recipients

- Windows 10 or 11
- No Python installation required
- No admin privileges needed
- ~50MB free disk space

### Troubleshooting

**Build fails:**
- Ensure all dependencies are installed: `pip install -r requirements.txt`
- Check that you have sufficient disk space
- Try running as administrator

**Executable won't run on other computers:**
- Make sure you're building on the same Windows version as the target
- Test on a different computer first
- Check antivirus software isn't blocking the executable

### Alternative: Web Version

If you need the app accessible from multiple devices simultaneously, consider converting it to a web application using Flask or FastAPI. Let me know if you'd like help with that approach.