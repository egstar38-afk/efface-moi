@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Efface-moi
where python >nul 2>nul
if errorlevel 1 (
  echo Python est introuvable. Telechargez plutot EffaceMoi.exe, ou installez Python 3.10+ depuis https://www.python.org/downloads/
  pause
  exit /b 1
)
python efface.py %*
if errorlevel 1 pause
