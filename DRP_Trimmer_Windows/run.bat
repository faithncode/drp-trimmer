@echo off
title DRP Trimmer
echo Starting DRP Trimmer...
python drp_trimmer_gui.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo An error occurred. If Python is not installed or not in PATH, please install Python 3.10+ from python.org.
    pause
)
