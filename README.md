# DRP Trimmer

Cross-platform (macOS & Windows) desktop application and CLI tool for trimming multicam ATEM / DaVinci Resolve Project (`.drp`) recordings and exporting synced Premiere XML or FCPXML.

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

## Project Structure

```
drp-trimmer/
├── drp_trimmer_gui.py       # Main Desktop GUI application
├── drp_to_stacked_xml.py    # Command-line (CLI) conversion utility
├── samples/                 # Sample DRP switcher logs & reference XML
│   ├── sample.drp
│   └── sample_stacked_premiere.xml
├── scripts/                 # Standalone compilation scripts
│   ├── build_mac_app.sh     # Builds dist/DRP Trimmer.app for macOS
│   └── build_windows_exe.bat # Builds dist/DRP_Trimmer.exe for Windows
├── requirements.txt         # Dependencies (FFmpeg required)
├── run.bat                  # 1-click launcher for Windows
├── run.sh                   # 1-click launcher for macOS/Linux
└── README.md
```

---

## Quick Start

### Running the GUI App

#### macOS:
```bash
brew install ffmpeg
./run.sh
```

#### Windows:
Double-click `run.bat` or run:
```cmd
python drp_trimmer_gui.py
```

---

### Command-Line (CLI) Usage

You can also run headless batch conversions using `drp_to_stacked_xml.py`:

```bash
# Export to Premiere Pro XML:
python3 drp_to_stacked_xml.py samples/sample.drp output.xml --media-root "/path/to/media"

# Export to Final Cut Pro XML:
python3 drp_to_stacked_xml.py samples/sample.drp output.fcpxml --media-root "/path/to/media"
```

---

### Building Standalone Executables

- **macOS (`.app` bundle):**
  ```bash
  ./scripts/build_mac_app.sh
  ```
  Produces `dist/DRP Trimmer.app`.

- **Windows (`.exe` standalone):**
  Double-click `scripts\build_windows_exe.bat`.
  Produces `dist\DRP_Trimmer.exe` (with embedded FFmpeg support if `ffmpeg.exe` is present in the directory).

---

## License
MIT License
