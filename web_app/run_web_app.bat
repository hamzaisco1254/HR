@echo off
echo Démarrage de l'application web HR Document Generator...
echo.

cd /d "%~dp0"

echo Installation des dépendances...
pip install -r requirements.txt
echo.

echo Démarrage du serveur...
python app.py

pause