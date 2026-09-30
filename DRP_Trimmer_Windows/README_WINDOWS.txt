=====================================================
            DRP TRIMMER - WINDOWS PACKAGE
=====================================================

What's inside:
  - drp_trimmer_gui.py : Main application script
  - ffmpeg.exe         : Windows FFmpeg binary (already included!)
  - ffprobe.exe        : Windows FFprobe binary (already included!)
  - run.bat            : 1-click launcher to run with Python
  - export_exe.bat     : 1-click script to compile into standalone .exe

-----------------------------------------------------
OPTION 1: Run directly with Python
-----------------------------------------------------
1. Double-click "run.bat"
   (Make sure Python 3.10+ is installed on the machine with "Add Python to PATH" checked).
2. The app will launch immediately with FFmpeg already detected!

-----------------------------------------------------
OPTION 2: Export to a standalone Windows .exe
-----------------------------------------------------
1. Double-click "export_exe.bat"
2. Wait a minute for PyInstaller to bundle the application.
3. You will find "DRP_Trimmer.exe" inside the "dist\" folder.
   This .exe has FFmpeg embedded inside, meaning you can copy
   DRP_Trimmer.exe to any Windows computer and it will work
   without installing Python or FFmpeg!
=====================================================
