@echo off
title Build DRP Trimmer Windows Executable
cd /d "%~dp0\.."

echo ===================================================
echo   Building DRP Trimmer Standalone Windows .exe
echo ===================================================
echo.

echo [1/2] Checking / Installing PyInstaller...
pip install pyinstaller
if %ERRORLEVEL% NEQ 0 (
    echo Failed to install PyInstaller. Make sure Python and pip are in your PATH.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [2/2] Building executable...
if exist "ffmpeg.exe" (
    echo Found local ffmpeg.exe - embedding inside .exe...
    pyinstaller --onefile --noconsole --clean --noconfirm --add-binary "ffmpeg.exe;." --add-binary "ffprobe.exe;." --name "DRP_Trimmer" drp_trimmer_gui.py
) else (
    echo Building standard standalone .exe...
    pyinstaller --onefile --noconsole --clean --noconfirm --name "DRP_Trimmer" drp_trimmer_gui.py
)

if %ERRORLEVEL% NEQ 0 (
    echo Build failed!
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ===================================================
echo   SUCCESS! Your executable is ready:
echo   Location: dist\DRP_Trimmer.exe
echo ===================================================
echo.
pause
