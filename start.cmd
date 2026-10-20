@echo off
cd /d "%~dp0"
python bootstrap.py
if errorlevel 1 pause
