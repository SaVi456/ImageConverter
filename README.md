# Image Converter

Batch image format conversion and resizing GUI app for scientists working with large local datasets. 100% offline, no cloud dependencies.

![Python](https://img.shields.io/badge/Python-3.9+-blue) ![License](https://img.shields.io/badge/License-MIT-green)

## Features

- **10 output formats:** JPEG, PNG, TIFF, WebP, BMP, GIF, ICO, JPEG2000, PPM, TGA
- **21 input formats** including TIFF variants, PBM/PGM/PPM, JPEG2000, TGA, DDS, HDR
- **Resize options:** none, scale by %, or custom pixel dimensions with optional aspect-ratio lock
- **Parallel processing** with configurable worker threads
- **Metadata preservation** (EXIF, ICC profiles, DPI, XMP)
- **JPEG/WebP quality slider** and per-format compression controls (TIFF LZW/Deflate, PNG 0-9)
- **Safety features:** atomic writes, output verification, disk space checks, mid-batch stop
- **Dry run mode** to preview what would be converted without writing files
- **Recursive sub-folder** processing
- **Persistent settings** saved between sessions
- **Session export/import** for sharing configurations
- **Live progress bar** with ETA, scrollable filterable log, and auto-save log option
- **High-bit-depth support** for scientific images (microscopy, astronomy) with automatic normalization

## Requirements

- Python 3.9+
- Windows, macOS, or Linux

## Setup

### Windows

```
setup.bat
```

### macOS / Linux

```
chmod +x setup.sh
./setup.sh
```

This creates a virtual environment and installs dependencies (Pillow, NumPy).

## Usage

### Windows

```
run.bat
```

### macOS / Linux

```
./run.sh
```

1. Select an **input folder** containing your images
2. Select an **output folder** (auto-suggested as `converted/` next to input)
3. Choose output format, quality, and resize options
4. Click **Convert** (or press `Ctrl+Enter`)

### Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Enter` | Start conversion |
| `Escape` | Stop conversion |
| `Ctrl+L` | Clear log |

## Running Tests

```
python run_tests.py
```

Runs 12 test scenarios covering format conversion, resizing, metadata, error handling, dry run, stop, and atomic writes.

## Project Structure

```
image_converter.py   Main application (GUI + conversion engine)
run_tests.py         Test suite
requirements.txt     Python dependencies
setup.bat / .sh      First-time setup scripts
run.bat / .sh        Launch scripts
```

## Settings

Settings are saved to `~/.imageconverter/settings.json` and restored on next launch. Logs are written to `~/.imageconverter/logs/` when auto-save log is enabled.

## License

[MIT](LICENSE)
