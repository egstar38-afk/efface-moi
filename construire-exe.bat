@echo off
rem Fabrique EffaceMoi.exe dans le dossier dist (un seul fichier, sans fenetre noire). Necessite Python et Internet.
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller || goto erreur
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name EffaceMoi --icon web/icone.ico --add-data "web;web" efface.py || goto erreur
echo.
echo Termine : EffaceMoi.exe est dans le dossier dist.
pause
exit /b 0
:erreur
echo La fabrication a echoue.
pause
exit /b 1
