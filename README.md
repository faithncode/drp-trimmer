# DRP Trimmer

Cross-platform (macOS & Windows) desktop tool for trimming multicam ATEM / DaVinci Resolve Project (`.drp`) recordings and exporting synced Premiere XML or FCPXML.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Windows-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Features

- **Automated Project Scanning**: Point to a project folder containing `.drp` switcher logs and camera ISO files (`… CAM 1.mp4`, `… CAM 2.mp4`, etc.) — the tool automatically matches angles and layouts.
- **Stacked Fallback Mode**: If no `.drp` is present, it automatically stacks detected camera ISO files onto individual synchronized tracks.
- **Multicam In/Out Trimming**: Set In/Out timecode (supports `HH:MM:SS:FF`, `HH:MM:SS`, `MM:SS`, or seconds) or use quick preset buttons.
- **Lossless Stream-Copy Footage Trimming**: Optionally cut camera ISO footage with FFmpeg without re-encoding (instant, lossless stream copy).
- **XML Exporters**:
  - **Premiere Pro XML** (`.xml` / `xmeml`)
  - **Final Cut Pro XML** (`.fcpxml`)
- **Audio Routing**: Assign any camera ISO as the sustained, unbroken primary audio track across A1/A2.
- **Dissolve & Transition Preservation**: Recognizes switcher cuts and cross dissolves directly from ATEM mix effect block events.

---

## Quick Start

### macOS
1. Ensure Python 3.10+ and FFmpeg are installed:
   ```bash
   brew install ffmpeg
   ```
2. Run the application:
   ```bash
   python3 drp_trimmer_gui.py
   ```
   *(Or build a standalone `.app` using PyInstaller)*

### Windows
1. Clone or extract the repository.
2. Run the launcher:
   ```cmd
   run.bat
   ```
3. To package into a single standalone `.exe` with FFmpeg embedded:
   ```cmd
   DRP_Trimmer_Windows\export_exe.bat
   ```

---

## License
MIT License
