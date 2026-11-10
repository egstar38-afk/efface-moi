@echo off
rem Ouvre Efface-moi quand elle tourne sur votre serveur : tunnel SSH, puis la page dans le navigateur.
chcp 65001 >nul
cd /d "%~dp0"
title Efface-moi (serveur)
if not exist data mkdir data
if exist data\serveur.txt goto connexion
set /p HOTE=Nom SSH de votre serveur (par exemple monserveur, ou utilisateur@192.168.1.20) : 
if "%HOTE%"=="" exit /b 1
>data\serveur.txt echo %HOTE%
:connexion
set /p HOTE=<data\serveur.txt
echo Connexion a %HOTE%... La page s ouvre dans quelques secondes.
echo Laissez cette fenetre ouverte pendant que vous utilisez Efface-moi. Fermez-la pour vous deconnecter.
start "" /min cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:8766/"
ssh -o ExitOnForwardFailure=yes -N -L 8766:127.0.0.1:8765 %HOTE%
if errorlevel 1 pause
