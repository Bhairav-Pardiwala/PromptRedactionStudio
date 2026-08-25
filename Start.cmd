@echo off
REM Double-click target for Prompt Redaction Studio.
REM Exists because .ps1 files open in Notepad on double-click and are blocked by ExecutionPolicy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
