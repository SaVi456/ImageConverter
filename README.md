# Image Converter

**Batch image conversion and resizing for scientists and researchers.**

A fast, fully offline desktop application for converting and resizing large image datasets. Built for labs and research groups that work with hundreds or thousands of images locally — no cloud upload, no subscription, no data leaving your machine.

![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-blue)
![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Why this exists

Most image conversion tools are either cloud-based (your data leaves your machine), slow (one file at a time), or designed for photographers rather than scientists. This tool was built to handle large local datasets — microscopy stacks, astronomy images, remote sensing archives — with features that matter for research: 16-bit depth preservation, multi-frame TIFF support, lossless WebP for masks, metadata preservation, and parallel processing that saturates your CPU.

---

## Features

### Conversion
- **10 output formats** — JPEG, PNG, TIFF, WebP, BMP, GIF, ICO, JPEG2000, PPM, TGA
- **22 input formats** — all common raster formats including TIFF variants (`.tif`, `.tiff`), NetPBM (`.ppm`, `.pgm`, `.pbm`, `.pnm`), JPEG2000 (`.jp2`, `.j2k`), TGA, DDS, HDR, EPS, and more
- **Multi-frame TIFF** — all frames preserved when converting TIFF stacks to TIFF
- **16-bit depth preservation** — keep full precision for scientific TIFF images (I / I;16 modes) without downconverting to 8-bit
- **WebP lossless mode** — ideal for segmentation masks, labels, and annotation maps
- **Format-quality controls** — JPEG/WebP quality (1–100), TIFF compression (LZW, Deflate, none), PNG compression level (0–9)

### Processing
- **Parallel workers** — configurable thread count, defaults to your CPU core count
- **Memory-safe batching** — processes large datasets in chunks to avoid out-of-memory crashes
- **Atomic writes** — files are written to a temporary path then renamed, so a crash never leaves a corrupted output file
- **Output verification** — re-opens every output file after writing to confirm it is readable

### Metadata
- **EXIF, ICC profile, and DPI** preserved across JPEG, PNG, TIFF, and WebP conversions
- **EXIF dimension tags** automatically updated after resize

### Resize
- **No resize** — format conversion only
- **Scale by %** — e.g. 50% halves both dimensions; scales above 400% prompt a confirmation
- **Custom pixel dimensions** — set width, height, or both; optional aspect-ratio lock

### Workflow
- **Output filename suffix** — append a string (e.g. `_converted`) to all output filenames to keep originals and results side-by-side
- **Extension filter** — process only `.tif` files, only `.png` files, or any subset
- **Recursive sub-folder** processing with mirrored output folder structure
- **Skip existing / overwrite** toggle
- **Dry run** — preview what would be converted without writing any files
- **Session export/import** — save and share your exact settings as a JSON file

### Safety
- **Disk space pre-flight check** — estimates output size and warns before starting
- **Mid-run disk monitoring** — stops automatically if free space drops below 512 MB
- **Name collision detection** — warns when two source files share a stem and would map to the same output file
- **Same-folder protection** — warns when input and output directories overlap

### Interface
- **Live progress bar** with files/sec throughput and ETA
- **Filterable log** — show/hide OK, Skipped, Errors, Warnings, and Info entries independently
- **Open Output button** — jump straight to the output folder after conversion completes
- **Auto-save log** — writes a timestamped `.txt` log after every run
- **Persistent settings** — all options saved between sessions

---

## Requirements

- Python 3.9 or newer
- Windows 10/11, macOS 12+, or Linux with a desktop environment (X11 or Wayland)
- ~50 MB disk space for the virtual environment

---

## Installation

### Windows

```
setup.bat
```

### macOS / Linux

```bash
chmod +x setup.sh run.sh
./setup.sh
```

The setup script creates a virtual environment and installs [Pillow](https://pillow.readthedocs.io/) and [NumPy](https://numpy.org/). If you run setup again later, it upgrades dependencies in place.

---

## Running

### Windows

```
run.bat
```

### macOS / Linux

```bash
./run.sh
```

Or activate the virtual environment manually and run:

```bash
source venv/bin/activate      # macOS / Linux
venv\Scripts\activate         # Windows
python image_converter.py
```

---

## Usage

1. **Select input folder** — click Browse or type a path. The file count updates automatically.
2. **Select output folder** — auto-suggested as `converted/` next to your input folder.
3. **Choose output format** — use the dropdown. Format-specific controls (quality, compression, lossless) enable automatically.
4. **Set resize options** — leave at "No resize" for format-only conversion, or choose scale % or custom dimensions.
5. **Click Convert** (or press `Ctrl+Enter`).

After conversion, click **Open Output** to jump to the result folder.

---

## Supported Formats

### Output

| Format | Extension | Quality control | Alpha | Notes |
|--------|-----------|----------------|-------|-------|
| JPEG | `.jpg` | Yes (1–100) | No | Optimize + subsampling |
| PNG | `.png` | Compression 0–9 | Yes | |
| TIFF | `.tiff` | LZW / Deflate / none | Yes | Multi-frame, 16-bit |
| WebP | `.webp` | Yes (1–100) or lossless | Yes | |
| BMP | `.bmp` | — | No | |
| GIF | `.gif` | — | Yes | Palette-quantized |
| ICO | `.ico` | — | Yes | Max 256×256 |
| JPEG2000 | `.jp2` | Yes (quality layers) | Yes | |
| PPM | `.ppm` | — | No | |
| TGA | `.tga` | — | Yes | |

### Input

`.jpg` `.jpeg` `.png` `.tiff` `.tif` `.bmp` `.webp` `.gif` `.ico` `.ppm` `.pgm` `.pbm` `.pnm` `.eps` `.pcx` `.xbm` `.sgi` `.jp2` `.j2k` `.tga` `.dds` `.hdr`

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Enter` | Start conversion |
| `Esc` | Stop running conversion |
| `Ctrl+L` | Clear log |
| `Alt+F4` | Quit |

---

## Scientific image notes

### High-bit-depth images (16-bit, 32-bit float)
By default, images with I, I;16, or F modes are converted to 8-bit with a warning in the log. For TIFF output, enable **"TIFF: preserve 16-bit"** to keep full precision without downconversion.

Float32 images (mode F) are always normalized to 8-bit using the image's actual data range — this is a display conversion and reduces scientific precision. Use TIFF + 16-bit preservation for data integrity.

### Multi-frame TIFF stacks
When converting TIFF → TIFF, all frames are preserved. When converting to any other format, only the first frame is converted and a warning is logged. Use TIFF output to preserve stacks.

### Lossless vs lossy WebP
For annotation masks, segmentation maps, or any image where pixel-exact values matter, enable **"WebP lossless"**. For photographic content where some quality loss is acceptable, use the quality slider instead.

---

## Settings and logs

| Path | Contents |
|------|----------|
| `~/.imageconverter/settings.json` | Persistent UI settings |
| `~/.imageconverter/logs/image_converter.log` | Rotating app log (when auto-save is on) |
| `~/.imageconverter/logs/conversion_YYYYMMDD_HHMMSS.txt` | Per-run logs |

Use **File → Open Log Folder** to navigate there directly.

---

## Troubleshooting

**"No supported image files found"**
Check that your input folder contains files with supported extensions. Use the **Filter extensions** field to restrict which formats are scanned, or clear it to scan all 22 input types.

**Large files cause the app to slow down**
Reduce the worker count. High parallelism on slow storage can saturate I/O and slow overall throughput. Try 2–4 workers for HDD, 4–8 for SSD.

**Out of memory error on a very large image**
This is a Pillow/OS limitation. The app sets `Image.MAX_IMAGE_PIXELS = None` to remove Pillow's default size cap, but very large images (e.g. gigapixel microscopy) require proportional RAM. Reduce the image size first or increase system RAM.

**TIFF output looks wrong after 16-bit conversion**
Ensure **"TIFF: preserve 16-bit"** is checked and your source is truly a 16-bit TIFF (I or I;16 mode). Float32 sources (F mode) are always converted to 8-bit.

**Setup fails on Linux**
Pillow requires some system libraries. Install them first:
```bash
sudo apt install python3-tk libjpeg-dev zlib1g-dev   # Debian/Ubuntu
sudo dnf install python3-tkinter libjpeg-devel        # Fedora
```

**App doesn't start on macOS**
macOS may block unsigned apps. Right-click `run.sh` → Open, or run from Terminal.

---

## Project structure

```
image_converter.py   Application entry point — GUI and conversion engine
run_tests.py         Test suite (32 tests)
requirements.txt     Python dependencies (Pillow, NumPy)
setup.bat / .sh      First-time setup scripts
run.bat / .sh        Launch scripts
```

---

## Running tests

```bash
source venv/bin/activate
python run_tests.py
```

The test suite creates synthetic test images, runs all conversion paths, and cleans up. It covers format conversion, resizing, mode normalization, metadata handling, multi-frame TIFF, 16-bit preservation, WebP lossless, output suffix, dry run, stop, skip, atomic writes, and error detection.

---

## Contributors

| Contributor | Role |
|-------------|------|
| [SaVi456](https://github.com/SaVi456) | Creator & maintainer |
| [Claude Sonnet 4.6](https://claude.ai) (Anthropic) | Co-developer |

---

## License

[MIT](LICENSE)
