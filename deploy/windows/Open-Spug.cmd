@echo off
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0Open-Spug.ps1"
if errorlevel 1 pause
