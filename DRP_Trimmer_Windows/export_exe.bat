@echo off
title Build DRP Trimmer Windows Executable
echo ===================================================
echo   Building DRP Trimmer Standalone Windows .exe
echo ===================================================
echo.

echo [1/3] Checking / Installing PyInstaller...
pip install pyinstaller
if %ERRORLEVEL% NEQ 0 (
    echo Failed to install PyInstaller. Make sure Python and pip are in your PATH.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [2/3] Building self-contained DRP_Trimmer.exe with embedded FFmpeg...
pyinstaller --onefile --noconsole --clean --noconfirm --add-binary "ffmpeg.exe;." --add-binary "ffprobe.exe;." --name "DRP_Trimmer" drp_trimmer_gui.py
if %ERRORLEVEL% NEQ 0 (
    echo Build failed!
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [3/3] Copying ffmpeg.exe and ffprobe.exe alongside the exe for convenience...
if not exist "dist" mkdir dist
copy /y ffmpeg.exe dist\
copy /y ffprobe.exe dist\

echo.
echo ===================================================
echo   SUCCESS! Your standalone executable is ready:
echo   Folder: dist\DRP_Trimmer.exe
echo ===================================================
echo.
pause
