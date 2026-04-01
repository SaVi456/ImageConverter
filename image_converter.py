"""
Image Converter  v2.1.0
Robust batch image format conversion and resizing for scientists.
100% offline — parallel processing — metadata-preserving.
"""

import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageSequence, UnidentifiedImageError

# ── Windows: enable DPI awareness for crisp rendering on HiDPI monitors ──
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

# ── Allow large scientific images (microscopy / astronomy) ────────────────
Image.MAX_IMAGE_PIXELS = None

# ─────────────────────────────────────────────────────────────────────────
VERSION = "2.1.0"
CONFIG_DIR = Path.home() / ".imageconverter"
CONFIG_FILE = CONFIG_DIR / "settings.json"
LOG_DIR = CONFIG_DIR / "logs"

# ── Supported output formats ──────────────────────────────────────────────
FORMATS = {
    "JPEG":     {"ext": ".jpg",  "quality": True,  "alpha": False},
    "PNG":      {"ext": ".png",  "quality": False, "alpha": True},
    "TIFF":     {"ext": ".tiff", "quality": False, "alpha": True},
    "WebP":     {"ext": ".webp", "quality": True,  "alpha": True},
    "BMP":      {"ext": ".bmp",  "quality": False, "alpha": False},
    "GIF":      {"ext": ".gif",  "quality": False, "alpha": True},
    "ICO":      {"ext": ".ico",  "quality": False, "alpha": True},
    "JPEG2000": {"ext": ".jp2",  "quality": True,  "alpha": True},
    "PPM":      {"ext": ".ppm",  "quality": False, "alpha": False},
    "TGA":      {"ext": ".tga",  "quality": False, "alpha": True},
}

# ── Supported input extensions ────────────────────────────────────────────
INPUT_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tiff", ".tif",
    ".bmp", ".webp", ".gif", ".ico",
    ".ppm", ".pgm", ".pbm", ".pnm",
    ".eps", ".pcx", ".xbm", ".sgi",
    ".jp2", ".j2k", ".tga", ".dds",
    ".hdr",
}

TIFF_COMPRESSIONS = ["tiff_lzw", "tiff_deflate", "none"]

# Rough output-size multipliers relative to raw uncompressed size
FORMAT_SIZE_FACTOR = {
    "JPEG": 0.15, "WebP": 0.12, "PNG": 0.50, "TIFF": 0.80,
    "BMP": 1.0, "GIF": 0.30, "ICO": 0.20,
    "JPEG2000": 0.20, "PPM": 1.0, "TGA": 0.80,
}

# ─────────────────────────────────────────────────────────────────────────
# Settings persistence
# ─────────────────────────────────────────────────────────────────────────

DEFAULTS: dict = {
    "version": 1,
    "input_dir": "",
    "output_dir": "",
    "fmt": "PNG",
    "jpeg_quality": 90,
    "resize_mode": "none",
    "width": "",
    "height": "",
    "scale": 50.0,
    "keep_aspect": True,
    "overwrite": False,
    "recursive": False,
    "num_workers": min(8, os.cpu_count() or 4),
    "verify_output": True,
    "preserve_metadata": True,
    "dry_run": False,
    "tiff_compression": "tiff_lzw",
    "png_compression": 6,
    "filter_ext": "",
    "log_to_file": False,
    "window_geometry": "",
    "filename_suffix": "",
    "webp_lossless": False,
    "tiff_16bit": False,
}


def load_settings() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
            # Merge with defaults; skip keys whose type has changed (schema safety)
            result = dict(DEFAULTS)
            for k, v in data.items():
                if k in DEFAULTS and type(v) is type(DEFAULTS[k]):
                    result[k] = v
            return result
        except Exception:
            pass
    return dict(DEFAULTS)


def save_settings(settings: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────
# File logging
# ─────────────────────────────────────────────────────────────────────────

def setup_file_logging() -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            LOG_DIR / "image_converter.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────
# Image mode normalisation
# ─────────────────────────────────────────────────────────────────────────

def normalize_mode(
    img: Image.Image, fmt: str, keep_16bit: bool = False
) -> tuple[Image.Image, list[str]]:
    """
    Convert the image mode to one compatible with *fmt*.
    Returns (converted_img, list_of_warning_strings).
    """
    warnings: list[str] = []
    mode = img.mode

    # ── 16-bit TIFF passthrough ───────────────────────────────────────
    if keep_16bit and fmt == "TIFF" and mode in ("I", "I;16", "I;16B"):
        return img, []

    # ── High-bit-depth scientific images ─────────────────────────────
    if mode in ("I", "I;16", "I;16B", "I;32", "F"):
        warnings.append(
            f"High-bit-depth source ({mode}) converted to 8-bit — "
            "scientific precision may be reduced."
        )
        if mode == "F":
            # Normalise float data to 0–255 using the actual data range
            try:
                import numpy as np
                arr = np.array(img, dtype=np.float32)
                lo, hi = arr.min(), arr.max()
                if hi > lo:
                    arr = ((arr - lo) / (hi - lo) * 255).astype(np.uint8)
                else:
                    arr = np.zeros(arr.shape, dtype=np.uint8)
                img = Image.fromarray(arr, mode="L")
            except ImportError:
                img = img.convert("L")
        else:
            img = img.convert("L")
        mode = img.mode

    # ── Per-format rules ──────────────────────────────────────────────
    if fmt == "JPEG":
        if mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif mode == "LA":
            bg = Image.new("L", img.size, 255)
            bg.paste(img, mask=img.split()[1])
            img = bg.convert("RGB")
        elif mode == "PA":
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif mode == "P":
            img = img.convert("RGB")
        elif mode not in ("RGB", "L", "CMYK", "YCbCr"):
            img = img.convert("RGB")

    elif fmt == "PNG":
        if mode in ("CMYK", "YCbCr"):
            img = img.convert("RGB")

    elif fmt in ("BMP", "TGA"):
        if mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGB")

    elif fmt == "PPM":
        if mode not in ("RGB", "L"):
            img = img.convert("RGB")

    elif fmt == "GIF":
        if mode not in ("P", "L"):
            img = img.convert("RGB").quantize(colors=256)

    elif fmt == "ICO":
        if mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        if img.width > 256 or img.height > 256:
            warnings.append("ICO is limited to 256×256 — image resized.")
            img = img.copy()
            img.thumbnail((256, 256), Image.LANCZOS)

    elif fmt == "JPEG2000":
        if mode in ("CMYK", "YCbCr", "P"):
            img = img.convert("RGB")

    elif fmt == "WebP":
        if mode in ("CMYK", "YCbCr", "P"):
            img = img.convert("RGB")
        elif mode == "PA":
            img = img.convert("RGBA")

    elif fmt == "TIFF":
        if mode == "YCbCr":
            img = img.convert("RGB")

    return img, warnings


# ─────────────────────────────────────────────────────────────────────────
# Disk space utilities
# ─────────────────────────────────────────────────────────────────────────

def estimate_output_bytes(
    files: list[Path], fmt: str,
    resize_mode: str, scale: float, width: int, height: int,
) -> int:
    factor = FORMAT_SIZE_FACTOR.get(fmt, 0.5)
    total = sum(f.stat().st_size for f in files)
    if resize_mode == "scale" and scale:
        total *= scale ** 2
    elif resize_mode == "custom" and width and height:
        total *= 0.5  # rough estimate without opening files
    return int(total * factor)


def check_disk_space(
    output_dir: Path, estimated_bytes: int
) -> tuple[bool, bool, str]:
    """
    Returns (ok_to_proceed, show_warning, message).
    ok_to_proceed=False → refuse to start.
    show_warning=True   → prompt the user.
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(output_dir).free
        est_gb = estimated_bytes / 1e9
        free_gb = free / 1e9
        if estimated_bytes > free:
            return False, True, (
                f"Insufficient disk space.\n"
                f"Estimated output: ~{est_gb:.1f} GB\n"
                f"Free space:        {free_gb:.1f} GB"
            )
        if estimated_bytes > free * 0.8:
            return True, True, (
                f"Low disk space warning.\n"
                f"Estimated output: ~{est_gb:.1f} GB\n"
                f"Free space:        {free_gb:.1f} GB\nContinue?"
            )
        return True, False, ""
    except Exception:
        return True, False, ""


# ─────────────────────────────────────────────────────────────────────────
# Single-file conversion (runs in worker threads)
# ─────────────────────────────────────────────────────────────────────────

def _apply_resize(
    img: Image.Image,
    resize_mode: str,
    width: Optional[int],
    height: Optional[int],
    scale: Optional[float],
    keep_aspect: bool,
) -> Image.Image:
    """Apply resize to a single frame."""
    if resize_mode == "custom":
        tw = width or img.width
        th = height or img.height
        if keep_aspect:
            img = img.copy()
            img.thumbnail((tw, th), Image.LANCZOS)
        else:
            img = img.resize((tw, th), Image.LANCZOS)
    elif resize_mode == "scale" and scale and scale != 1.0:
        nw = max(1, int(img.width * scale))
        nh = max(1, int(img.height * scale))
        img = img.resize((nw, nh), Image.LANCZOS)
    return img


def convert_one(
    src: Path,
    dest: Path,
    fmt: str,
    resize_mode: str,
    width: Optional[int],
    height: Optional[int],
    scale: Optional[float],
    keep_aspect: bool,
    jpeg_quality: int,
    preserve_metadata: bool,
    verify_output: bool,
    tiff_compression: str,
    png_compression: int,
    dry_run: bool,
    webp_lossless: bool = False,
    keep_16bit: bool = False,
) -> tuple[str, str, list[str]]:
    """
    Convert *src* → *dest*.
    Returns (status, log_message, warnings).
    status: "ok" | "err"
    """
    if dry_run:
        return "ok", f"[DRY]  {src.name}  ->  {dest.name}", []

    extra_warns: list[str] = []

    try:
        if src.stat().st_size == 0:
            return "err", f"[ERR]  {src.name}: empty file (0 bytes)", []

        with Image.open(src) as img:
            img.load()  # force full decode — surfaces corruption immediately
            orig_dims = f"{img.width}x{img.height}"

            # ── Multi-frame detection ─────────────────────────────────
            n_frames = getattr(img, "n_frames", 1)
            extra_raw_frames: list[Image.Image] = []
            if n_frames > 1:
                if fmt == "TIFF":
                    all_frames = list(ImageSequence.Iterator(img))
                    # Use a single-frame copy of frame 0 so Pillow doesn't
                    # re-iterate the source frames when saving with save_all=True.
                    img = all_frames[0].copy()
                    extra_raw_frames = [f.copy() for f in all_frames[1:]]
                else:
                    extra_warns.append(
                        f"Multi-frame source ({n_frames} frames) — converting frame 1 only."
                    )

            # ── Collect metadata ──────────────────────────────────────
            meta: dict = {}
            if preserve_metadata:
                for key in ("exif", "icc_profile", "dpi", "xmp"):
                    try:
                        val = img.info.get(key)
                        if val is not None:
                            meta[key] = val
                    except Exception:
                        pass

            # ── Mode normalisation ────────────────────────────────────
            img, mode_warns = normalize_mode(img, fmt, keep_16bit)
            extra_warns.extend(mode_warns)

            # ── Resize ───────────────────────────────────────────────
            img = _apply_resize(img, resize_mode, width, height, scale, keep_aspect)

            # Update EXIF pixel-dimension tags after resize
            if preserve_metadata and "exif" in meta and orig_dims != f"{img.width}x{img.height}":
                try:
                    exif_obj = img.getexif()
                    exif_obj[40962] = img.width   # PixelXDimension
                    exif_obj[40963] = img.height  # PixelYDimension
                    meta["exif"] = exif_obj.tobytes()
                except Exception:
                    pass

            # ── Process extra frames for multi-frame TIFF output ──────
            processed_extras: list[Image.Image] = []
            for ef in extra_raw_frames:
                ef, _ = normalize_mode(ef, fmt, keep_16bit)
                ef = _apply_resize(ef, resize_mode, width, height, scale, keep_aspect)
                processed_extras.append(ef)

            # ── Build save kwargs ─────────────────────────────────────
            kw: dict = {}

            if "dpi" in meta and fmt in ("JPEG", "PNG", "TIFF"):
                kw["dpi"] = meta["dpi"]

            if fmt == "JPEG":
                kw.update(
                    quality=jpeg_quality,
                    optimize=True,
                    subsampling=0 if jpeg_quality >= 90 else 2,
                )
                if "exif" in meta:
                    kw["exif"] = meta["exif"]
                if "icc_profile" in meta:
                    kw["icc_profile"] = meta["icc_profile"]

            elif fmt == "PNG":
                kw.update(optimize=True, compress_level=png_compression)
                if "exif" in meta:
                    try:
                        kw["exif"] = meta["exif"]
                    except Exception:
                        pass
                if "icc_profile" in meta:
                    kw["icc_profile"] = meta["icc_profile"]

            elif fmt == "WebP":
                if webp_lossless:
                    kw.update(lossless=True, method=6)
                else:
                    kw.update(quality=jpeg_quality, method=6)
                if "exif" in meta:
                    kw["exif"] = meta["exif"]
                if "icc_profile" in meta:
                    kw["icc_profile"] = meta["icc_profile"]

            elif fmt == "TIFF":
                if tiff_compression != "none":
                    kw["compression"] = tiff_compression
                if "exif" in meta:
                    try:
                        kw["exif"] = meta["exif"]
                    except Exception:
                        pass
                if "icc_profile" in meta:
                    kw["icc_profile"] = meta["icc_profile"]
                if processed_extras:
                    kw["save_all"] = True
                    kw["append_images"] = processed_extras

            elif fmt == "JPEG2000":
                # Lossless by default; irreversible=True for lossy
                if jpeg_quality < 100:
                    kw.update(irreversible=True, quality_layers=[jpeg_quality])

            elif fmt == "GIF":
                kw["optimize"] = True

            # ── Atomic write (temp → rename) ──────────────────────────
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.parent / f".tmp_{os.getpid()}_{dest.name}"
            try:
                img.save(tmp, format=fmt, **kw)
                os.replace(tmp, dest)   # atomic on NTFS and POSIX
            except Exception:
                try:
                    tmp.unlink()
                except Exception:
                    pass
                raise

            # ── Verify output ─────────────────────────────────────────
            if verify_output:
                try:
                    with Image.open(dest) as chk:
                        chk.load()
                except Exception as e:
                    try:
                        dest.unlink()
                    except Exception:
                        pass
                    return "err", f"[ERR]  {src.name}: output verification failed — {e}", []

            new_dims = f"{img.width}x{img.height}"
            resize_note = f" [{orig_dims}->{new_dims}]" if orig_dims != new_dims else ""
            frame_note = f" ({n_frames} frames)" if n_frames > 1 and fmt == "TIFF" else ""
            size_kb = dest.stat().st_size / 1024
            msg = (
                f"[OK]   {src.name}  ->  {dest.name}"
                f"{resize_note}{frame_note}  ({size_kb:.0f} KB)"
            )
            return "ok", msg, extra_warns

    except UnidentifiedImageError:
        return "err", f"[ERR]  {src.name}: unrecognised or corrupt image", []
    except PermissionError as e:
        return "err", f"[ERR]  {src.name}: permission denied — {e}", []
    except MemoryError:
        return "err", f"[ERR]  {src.name}: out of memory (image too large)", []
    except OSError as e:
        if getattr(e, "errno", None) == 28:
            return "err", f"[ERR]  {src.name}: DISK FULL — stopping", []
        return "err", f"[ERR]  {src.name}: {e}", []
    except Exception as e:
        tb_line = traceback.format_exc().splitlines()[-1]
        return "err", f"[ERR]  {src.name}: {e}  [{tb_line}]", []


# ─────────────────────────────────────────────────────────────────────────
# File collection
# ─────────────────────────────────────────────────────────────────────────

def collect_images(
    input_dir: Path, recursive: bool, filter_ext: set
) -> tuple[list[Path], list[tuple[Path, Path]]]:
    """
    Returns (files, collisions).
    collisions = list of (kept, dropped) path pairs with the same
    case-insensitive stem in the same directory.
    """
    pattern = "**/*" if recursive else "*"
    exts = filter_ext if filter_ext else INPUT_EXTENSIONS
    files: list[Path] = []
    seen: dict[tuple, Path] = {}
    collisions: list[tuple[Path, Path]] = []

    for p in sorted(input_dir.glob(pattern)):
        if not p.is_file():
            continue
        if p.suffix.lower() not in exts:
            continue
        key = (p.parent, p.stem.lower())
        if key in seen:
            collisions.append((seen[key], p))
        else:
            seen[key] = p
            files.append(p)

    return files, collisions


# ─────────────────────────────────────────────────────────────────────────
# Batch runner (executed in a background thread)
# ─────────────────────────────────────────────────────────────────────────

def run_conversion(
    input_dir: Path,
    output_dir: Path,
    fmt: str,
    resize_mode: str,
    width: Optional[int],
    height: Optional[int],
    scale: Optional[float],
    keep_aspect: bool,
    jpeg_quality: int,
    overwrite: bool,
    recursive: bool,
    num_workers: int,
    verify_output: bool,
    preserve_metadata: bool,
    dry_run: bool,
    filter_ext: set,
    tiff_compression: str,
    png_compression: int,
    msg_queue: queue.Queue,
    stop_event: threading.Event,
    filename_suffix: str = "",
    webp_lossless: bool = False,
    keep_16bit: bool = False,
) -> None:

    def put(kind: str, *args) -> None:
        msg_queue.put((kind, *args))

    output_dir.mkdir(parents=True, exist_ok=True)
    ext = FORMATS[fmt]["ext"]
    files, collisions = collect_images(input_dir, recursive, filter_ext)

    for kept, dropped in collisions:
        put("log", "warn",
            f"[WARN] Name collision: {kept.name} and {dropped.name}"
            " — second file skipped")

    if not files:
        put("log", "info", "No supported image files found in the input folder.")
        put("done", 0, 0, 0, 0)
        return

    label = "[DRY RUN] " if dry_run else ""
    put("log", "info",
        f"{label}Found {len(files)} image(s).  "
        f"Workers: {num_workers}  |  Format: {fmt}  |  "
        f"Verify: {verify_output}  |  Preserve metadata: {preserve_metadata}")

    # Pre-filter already-existing destinations
    tasks: list[tuple[Path, Path]] = []
    pre_skip = 0
    for src in files:
        rel = src.relative_to(input_dir)
        dest = output_dir / rel.with_name(rel.stem + filename_suffix + ext)
        if dest.exists() and not overwrite and not dry_run:
            put("log", "skip", f"[SKIP] {rel}")
            pre_skip += 1
        else:
            tasks.append((src, dest))

    total = len(files)
    done = pre_skip
    ok = 0
    skipped = pre_skip
    errors = 0
    start = time.monotonic()

    # Process in memory-safe batches
    BATCH = max(num_workers, min(200, max(1, total // 4)))
    idx = 0

    while idx < len(tasks) and not stop_event.is_set():
        batch = tasks[idx: idx + BATCH]
        idx += BATCH

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {
                executor.submit(
                    convert_one,
                    src, dest, fmt, resize_mode, width, height, scale,
                    keep_aspect, jpeg_quality, preserve_metadata,
                    verify_output, tiff_compression, png_compression, dry_run,
                    webp_lossless, keep_16bit,
                ): src
                for src, dest in batch
            }

            for future in as_completed(futures):
                if stop_event.is_set():
                    for f in futures:
                        f.cancel()
                    put("log", "warn", "Stopped by user.")
                    put("done", ok, skipped, errors, total)
                    return

                done += 1
                src = futures[future]

                try:
                    status, msg, warns = future.result()
                except Exception as exc:
                    status = "err"
                    msg = f"[ERR]  {src.name}: {exc}"
                    warns = []

                for w in warns:
                    put("log", "warn", f"[WARN] {src.name}: {w}")

                put("log", status, msg)
                logging.info(msg)

                if status == "ok":
                    ok += 1
                else:
                    errors += 1

                elapsed = time.monotonic() - start
                rate = done / elapsed if elapsed > 0 else 0
                remaining = (total - done) / rate if rate > 0 else 0
                put("progress", done, total, rate, remaining, src.name)

                # Mid-run disk check every 50 files
                if status == "ok" and done % 50 == 0:
                    try:
                        free = shutil.disk_usage(output_dir).free
                        if free < 512 * 1024 * 1024:  # 512 MB
                            put("log", "warn",
                                f"[WARN] Disk space critically low "
                                f"({free // 1_000_000:.0f} MB free) — stopping.")
                            stop_event.set()
                    except Exception:
                        pass

                # Abort immediately on disk-full error
                if status == "err" and "DISK FULL" in msg:
                    stop_event.set()

    put("done", ok, skipped, errors, total)


# ─────────────────────────────────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────────────────────────────────

_FONT_UI   = ("Segoe UI", 9)      if sys.platform == "win32" else ("TkDefaultFont", 9)
_FONT_BOLD = ("Segoe UI", 9, "bold") if sys.platform == "win32" else ("TkDefaultFont", 9, "bold")
_FONT_H    = ("Segoe UI", 15, "bold") if sys.platform == "win32" else ("TkDefaultFont", 14, "bold")
_FONT_LOG  = ("Consolas", 9)      if sys.platform == "win32" else ("Courier", 9)
_FONT_LOG_BOLD = ("Consolas", 9, "bold") if sys.platform == "win32" else ("Courier", 9, "bold")


class ImageConverterApp(tk.Tk):

    def __init__(self) -> None:
        super().__init__()

        self._settings = load_settings()
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._msg_queue: queue.Queue = queue.Queue()
        self._log_lines: list[str] = []
        self._log_filter_vars: dict[str, tk.BooleanVar] = {}
        self._rescan_after_id: Optional[str] = None
        self._last_output_dir: str = ""

        self.title(f"Image Converter  v{VERSION}")
        self.resizable(True, True)
        self.minsize(760, 580)

        self._build_menu()
        self._build_ui()
        self._load_settings_to_ui()
        self._center_window()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._bind_shortcuts()
        self._poll_queue()

    # ── Menu ──────────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        mb = tk.Menu(self)
        self.config(menu=mb)

        fm = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="File", menu=fm)
        fm.add_command(label="Export Session…", command=self._export_session)
        fm.add_command(label="Import Session…", command=self._import_session)
        fm.add_separator()
        fm.add_command(label="Open Log Folder", command=self._open_log_folder)
        fm.add_separator()
        fm.add_command(label="Quit", command=self._on_close, accelerator="Alt+F4")

        hm = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="Help", menu=hm)
        hm.add_command(label="Keyboard Shortcuts", command=self._show_shortcuts)
        hm.add_command(label="About", command=self._show_about)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 3}

        # Header
        hdr = tk.Frame(self, bg="#1e3a5f")
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text=f"Image Converter  v{VERSION}",
                 font=_FONT_H, fg="white", bg="#1e3a5f", pady=8).pack()
        tk.Label(hdr,
                 text="Batch convert & resize image datasets  —  100% offline  —  parallel processing",
                 font=_FONT_UI, fg="#aaccee", bg="#1e3a5f", pady=2).pack()

        # ── Folders ───────────────────────────────────────────────────
        ff = tk.LabelFrame(self, text=" Folders ", font=_FONT_BOLD, **pad)
        ff.pack(fill=tk.X, **pad)

        self._input_var  = tk.StringVar()
        self._output_var = tk.StringVar()
        self._file_count_var = tk.StringVar(value="")

        self._make_folder_row(ff, "Input folder:",  self._input_var,
                              self._browse_input,  self._file_count_var)
        self._make_folder_row(ff, "Output folder:", self._output_var,
                              self._browse_output, None)

        # ── Conversion options ────────────────────────────────────────
        cf = tk.LabelFrame(self, text=" Conversion Options ", font=_FONT_BOLD, **pad)
        cf.pack(fill=tk.X, **pad)

        r1 = tk.Frame(cf); r1.pack(fill=tk.X, padx=8, pady=2)
        tk.Label(r1, text="Output format:", width=16, anchor="w").pack(side=tk.LEFT)
        self._fmt_var = tk.StringVar(value="PNG")
        ttk.Combobox(r1, textvariable=self._fmt_var,
                     values=list(FORMATS.keys()), state="readonly", width=12
                     ).pack(side=tk.LEFT, padx=4)
        tk.Label(r1, text="  JPEG/WebP quality:").pack(side=tk.LEFT)
        self._quality_var = tk.IntVar(value=90)
        self._quality_spinbox = tk.Spinbox(r1, from_=1, to=100,
                                           textvariable=self._quality_var, width=5)
        self._quality_spinbox.pack(side=tk.LEFT, padx=4)
        tk.Label(r1, text="  Workers:").pack(side=tk.LEFT)
        self._workers_var = tk.IntVar(value=DEFAULTS["num_workers"])
        tk.Spinbox(r1, from_=1, to=64, textvariable=self._workers_var,
                   width=4).pack(side=tk.LEFT, padx=4)

        r2 = tk.Frame(cf); r2.pack(fill=tk.X, padx=8, pady=2)
        tk.Label(r2, text="TIFF compression:", width=16, anchor="w").pack(side=tk.LEFT)
        self._tiff_comp_var = tk.StringVar(value="tiff_lzw")
        self._tiff_comp_combo = ttk.Combobox(r2, textvariable=self._tiff_comp_var,
                                             values=TIFF_COMPRESSIONS, state="readonly", width=14)
        self._tiff_comp_combo.pack(side=tk.LEFT, padx=4)
        tk.Label(r2, text="  PNG compression (0-9):").pack(side=tk.LEFT)
        self._png_comp_var = tk.IntVar(value=6)
        self._png_comp_spinbox = tk.Spinbox(r2, from_=0, to=9,
                                            textvariable=self._png_comp_var, width=4)
        self._png_comp_spinbox.pack(side=tk.LEFT, padx=4)

        r3 = tk.Frame(cf); r3.pack(fill=tk.X, padx=8, pady=2)
        tk.Label(r3, text="Filter extensions:", width=16, anchor="w").pack(side=tk.LEFT)
        self._filter_ext_var = tk.StringVar()
        tk.Entry(r3, textvariable=self._filter_ext_var, width=20).pack(side=tk.LEFT, padx=4)
        tk.Label(r3, text="(e.g. .tif,.png)", fg="gray",
                 font=_FONT_UI).pack(side=tk.LEFT)
        tk.Label(r3, text="  Output suffix:", anchor="w").pack(side=tk.LEFT, padx=(12, 0))
        self._suffix_var = tk.StringVar()
        tk.Entry(r3, textvariable=self._suffix_var, width=14).pack(side=tk.LEFT, padx=4)
        tk.Label(r3, text="(e.g. _converted)", fg="gray",
                 font=_FONT_UI).pack(side=tk.LEFT)

        r4 = tk.Frame(cf); r4.pack(fill=tk.X, padx=8, pady=2)
        self._overwrite_var     = tk.BooleanVar()
        self._recursive_var     = tk.BooleanVar()
        self._preserve_meta_var = tk.BooleanVar(value=True)
        self._webp_lossless_var = tk.BooleanVar()
        tk.Checkbutton(r4, text="Overwrite existing",
                       variable=self._overwrite_var).pack(side=tk.LEFT)
        tk.Checkbutton(r4, text="Sub-folders",
                       variable=self._recursive_var).pack(side=tk.LEFT, padx=10)
        tk.Checkbutton(r4, text="Preserve metadata  (EXIF / ICC / DPI)",
                       variable=self._preserve_meta_var).pack(side=tk.LEFT, padx=10)
        self._webp_lossless_cb = tk.Checkbutton(r4, text="WebP lossless",
                                                variable=self._webp_lossless_var)
        self._webp_lossless_cb.pack(side=tk.LEFT, padx=10)

        r5 = tk.Frame(cf); r5.pack(fill=tk.X, padx=8, pady=2)
        self._verify_var      = tk.BooleanVar(value=True)
        self._dry_run_var     = tk.BooleanVar()
        self._log_to_file_var = tk.BooleanVar()
        self._tiff_16bit_var  = tk.BooleanVar()
        tk.Checkbutton(r5, text="Verify output files",
                       variable=self._verify_var).pack(side=tk.LEFT)
        tk.Checkbutton(r5, text="Dry run  (preview only)",
                       variable=self._dry_run_var).pack(side=tk.LEFT, padx=10)
        tk.Checkbutton(r5, text="Auto-save log",
                       variable=self._log_to_file_var).pack(side=tk.LEFT, padx=10)
        self._tiff_16bit_cb = tk.Checkbutton(r5, text="TIFF: preserve 16-bit",
                                             variable=self._tiff_16bit_var)
        self._tiff_16bit_cb.pack(side=tk.LEFT, padx=10)

        # Format-change trace — enables/disables format-specific controls
        self._fmt_var.trace_add("write", lambda *_: self._on_fmt_change())

        # File-count re-scan when recursive or filter changes
        self._recursive_var.trace_add("write", lambda *_: self._trigger_rescan())
        self._filter_ext_var.trace_add("write", lambda *_: self._trigger_rescan())

        # ── Resize ────────────────────────────────────────────────────
        rf = tk.LabelFrame(self, text=" Resize Options ", font=_FONT_BOLD, **pad)
        rf.pack(fill=tk.X, **pad)

        self._resize_mode = tk.StringVar(value="none")
        left_rf = tk.Frame(rf); left_rf.pack(side=tk.LEFT)
        for label, val in [("No resize", "none"),
                            ("Scale by %", "scale"),
                            ("Custom px", "custom")]:
            tk.Radiobutton(left_rf, text=label, variable=self._resize_mode,
                           value=val, command=self._on_resize_mode
                           ).pack(anchor="w", padx=8)

        right_rf = tk.Frame(rf); right_rf.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._scale_frame = tk.Frame(right_rf)
        self._scale_frame.pack(anchor="w", padx=8, pady=2)
        tk.Label(self._scale_frame, text="Scale %:").pack(side=tk.LEFT)
        self._scale_var = tk.DoubleVar(value=50.0)
        self._scale_spinbox = tk.Spinbox(self._scale_frame, from_=1, to=10000, increment=5,
                                         textvariable=self._scale_var, width=7)
        self._scale_spinbox.pack(side=tk.LEFT, padx=4)

        self._dim_frame = tk.Frame(right_rf)
        self._dim_frame.pack(anchor="w", padx=8, pady=2)
        tk.Label(self._dim_frame, text="Width (px):").pack(side=tk.LEFT)
        self._width_var = tk.StringVar()
        tk.Entry(self._dim_frame, textvariable=self._width_var,
                 width=7).pack(side=tk.LEFT, padx=3)
        tk.Label(self._dim_frame, text="Height (px):").pack(side=tk.LEFT)
        self._height_var = tk.StringVar()
        tk.Entry(self._dim_frame, textvariable=self._height_var,
                 width=7).pack(side=tk.LEFT, padx=3)
        self._aspect_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self._dim_frame, text="Keep aspect ratio",
                       variable=self._aspect_var).pack(side=tk.LEFT, padx=6)

        self._on_resize_mode()

        # ── Progress ──────────────────────────────────────────────────
        pf = tk.LabelFrame(self, text=" Progress ", font=_FONT_BOLD, **pad)
        pf.pack(fill=tk.X, **pad)

        self._progress_var = tk.DoubleVar()
        ttk.Progressbar(pf, variable=self._progress_var,
                        maximum=100).pack(fill=tk.X, padx=8, pady=(4, 2))
        pr = tk.Frame(pf); pr.pack(fill=tk.X, padx=8, pady=(0, 4))
        self._status_var = tk.StringVar(value="Ready.")
        tk.Label(pr, textvariable=self._status_var,
                 anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._eta_var = tk.StringVar(value="")
        tk.Label(pr, textvariable=self._eta_var,
                 anchor="e", fg="#555").pack(side=tk.RIGHT)

        # ── Log ───────────────────────────────────────────────────────
        lf = tk.LabelFrame(self, text=" Log ", font=_FONT_BOLD, **pad)
        lf.pack(fill=tk.BOTH, expand=True, **pad)

        # Filter bar
        fbar = tk.Frame(lf); fbar.pack(fill=tk.X, padx=4, pady=(2, 0))
        tk.Label(fbar, text="Show:", fg="gray").pack(side=tk.LEFT)
        for tag, label, color in [
            ("ok",   "OK",     "#1a7f37"),
            ("skip", "Skipped","#b08000"),
            ("err",  "Errors", "#cf222e"),
            ("warn", "Warnings","#d1560f"),
            ("info", "Info",   "#0550ae"),
        ]:
            v = tk.BooleanVar(value=True)
            self._log_filter_vars[tag] = v
            tk.Checkbutton(fbar, text=label, fg=color, variable=v,
                           command=lambda t=tag, bv=v: self._on_filter_change(t, bv)
                           ).pack(side=tk.LEFT, padx=4)

        self._log_widget = tk.Text(
            lf, height=5, state=tk.DISABLED,
            font=_FONT_LOG, wrap=tk.NONE,
        )
        self._log_widget.tag_configure("ok",   foreground="#1a7f37")
        self._log_widget.tag_configure("skip", foreground="#b08000")
        self._log_widget.tag_configure("err",  foreground="#cf222e", font=_FONT_LOG_BOLD)
        self._log_widget.tag_configure("warn", foreground="#d1560f")
        self._log_widget.tag_configure("info", foreground="#0550ae")
        self._log_widget.tag_configure("done", foreground="#333333", font=_FONT_LOG_BOLD)

        sb_y = tk.Scrollbar(lf, command=self._log_widget.yview)
        sb_x = tk.Scrollbar(lf, orient=tk.HORIZONTAL, command=self._log_widget.xview)
        self._log_widget.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        sb_y.pack(side=tk.RIGHT,  fill=tk.Y)
        sb_x.pack(side=tk.BOTTOM, fill=tk.X)
        self._log_widget.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # ── Buttons ───────────────────────────────────────────────────
        bf = tk.Frame(self); bf.pack(fill=tk.X, padx=10, pady=6)

        tk.Button(bf, text="Clear Log",  command=self._clear_log,  width=10).pack(side=tk.LEFT, padx=4)
        tk.Button(bf, text="Export Log", command=self._export_log, width=10).pack(side=tk.LEFT, padx=4)

        self._open_output_btn = tk.Button(
            bf, text="Open Output", command=self._open_output_folder,
            width=12, state=tk.DISABLED,
        )
        self._open_output_btn.pack(side=tk.LEFT, padx=4)

        self._stop_btn = tk.Button(
            bf, text="Stop  [Esc]", command=self._stop,
            width=12, state=tk.DISABLED, bg="#c0392b", fg="white",
        )
        self._stop_btn.pack(side=tk.RIGHT, padx=4)

        self._convert_btn = tk.Button(
            bf, text="Convert  [Ctrl+Enter]", command=self._start_conversion,
            width=22, bg="#1e3a5f", fg="white", font=_FONT_BOLD,
        )
        self._convert_btn.pack(side=tk.RIGHT, padx=4)

    def _make_folder_row(
        self, parent, label: str, var: tk.StringVar,
        cmd, count_var: Optional[tk.StringVar],
    ) -> None:
        row = tk.Frame(parent); row.pack(fill=tk.X, padx=8, pady=2)
        tk.Label(row, text=label, width=14, anchor="w").pack(side=tk.LEFT)
        tk.Entry(row, textvariable=var).pack(side=tk.LEFT, padx=4,
                                             fill=tk.X, expand=True)
        if count_var:
            tk.Label(row, textvariable=count_var, fg="#0550ae",
                     width=14, anchor="w").pack(side=tk.LEFT)
        tk.Button(row, text="Browse…", command=cmd, width=9).pack(side=tk.RIGHT)

    # ── Keyboard shortcuts ────────────────────────────────────────────────

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-Return>", lambda _: self._start_conversion())
        self.bind("<Escape>",         lambda _: self._stop())
        self.bind("<Control-l>",      lambda _: self._clear_log())

    # ── Format-aware control enabling ────────────────────────────────────

    def _on_fmt_change(self) -> None:
        fmt = self._fmt_var.get()
        quality_state = tk.NORMAL if fmt in ("JPEG", "WebP", "JPEG2000") else tk.DISABLED
        self._quality_spinbox.configure(state=quality_state)
        self._png_comp_spinbox.configure(
            state=tk.NORMAL if fmt == "PNG" else tk.DISABLED)
        self._tiff_comp_combo.configure(
            state="readonly" if fmt == "TIFF" else tk.DISABLED)
        self._webp_lossless_cb.configure(
            state=tk.NORMAL if fmt == "WebP" else tk.DISABLED)
        self._tiff_16bit_cb.configure(
            state=tk.NORMAL if fmt == "TIFF" else tk.DISABLED)

    # ── Events ────────────────────────────────────────────────────────────

    def _browse_input(self) -> None:
        d = filedialog.askdirectory(title="Select input folder")
        if d:
            self._input_var.set(d)
            if not self._output_var.get():
                self._output_var.set(str(Path(d).parent / "converted"))
            self._file_count_var.set("Scanning…")
            threading.Thread(
                target=self._scan_file_count,
                args=(Path(d), self._filter_ext_var.get(), self._recursive_var.get()),
                daemon=True,
            ).start()

    def _browse_output(self) -> None:
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self._output_var.set(d)

    def _trigger_rescan(self) -> None:
        """Debounced re-scan of input file count when filter or recursive changes."""
        d = self._input_var.get().strip()
        if not d:
            return
        if self._rescan_after_id:
            self.after_cancel(self._rescan_after_id)
        fe_raw = self._filter_ext_var.get()
        recursive = self._recursive_var.get()
        self._file_count_var.set("Scanning…")
        self._rescan_after_id = self.after(
            250,
            lambda: threading.Thread(
                target=self._scan_file_count,
                args=(Path(d), fe_raw, recursive),
                daemon=True,
            ).start()
        )

    def _scan_file_count(self, d: Path, fe_raw: str = "", recursive: bool = False) -> None:
        try:
            fe = self._parse_filter_ext(fe_raw)
            exts = fe if fe else INPUT_EXTENSIONS
            pattern = "**/*" if recursive else "*"
            n = sum(1 for p in d.glob(pattern)
                    if p.is_file() and p.suffix.lower() in exts)
            self.after(0, lambda: self._file_count_var.set(f"{n:,} image(s)"))
        except Exception:
            self.after(0, lambda: self._file_count_var.set(""))

    def _on_resize_mode(self) -> None:
        mode = self._resize_mode.get()
        for w in self._scale_frame.winfo_children():
            if isinstance(w, (tk.Spinbox, tk.Entry)):
                w.configure(state=tk.NORMAL if mode == "scale" else tk.DISABLED)
        for w in self._dim_frame.winfo_children():
            if isinstance(w, (tk.Entry, tk.Checkbutton)):
                w.configure(state=tk.NORMAL if mode == "custom" else tk.DISABLED)

    def _on_filter_change(self, tag: str, var: tk.BooleanVar) -> None:
        self._log_widget.tag_configure(tag, elide=not var.get())

    def _clear_log(self) -> None:
        self._log_widget.configure(state=tk.NORMAL)
        self._log_widget.delete("1.0", tk.END)
        self._log_widget.configure(state=tk.DISABLED)
        self._log_lines.clear()
        self._progress_var.set(0)
        self._status_var.set("Ready.")
        self._eta_var.set("")
        self._open_output_btn.configure(state=tk.DISABLED)

    def _export_log(self) -> None:
        if not self._log_lines:
            messagebox.showinfo("Export Log", "Log is empty.")
            return
        path = filedialog.asksaveasfilename(
            title="Save log file",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(self._log_lines))
                messagebox.showinfo("Saved", f"Log saved to:\n{path}")
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def _open_output_folder(self) -> None:
        path = self._last_output_dir or self._output_var.get().strip()
        if path and Path(path).exists():
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])

    def _stop(self) -> None:
        if self._worker and self._worker.is_alive():
            self._stop_event.set()
            self._stop_btn.configure(state=tk.DISABLED)
            self._status_var.set("Stopping…")

    def _on_close(self) -> None:
        if self._worker and self._worker.is_alive():
            if not messagebox.askyesno("Quit", "A conversion is running. Stop and quit?"):
                return
            self._stop_event.set()
            self._worker.join(timeout=3)
            if self._worker.is_alive():
                if not messagebox.askyesno(
                    "Still running",
                    "Conversion is still finishing. Force quit?"
                ):
                    return
        self._save_current_settings()
        self.destroy()

    # ── Validation & launch ───────────────────────────────────────────────

    @staticmethod
    def _parse_filter_ext(raw: str) -> set:
        result = set()
        for ext in raw.split(","):
            ext = ext.strip().lower()
            if ext and not ext.startswith("."):
                ext = "." + ext
            if ext:
                result.add(ext)
        return result

    def _start_conversion(self) -> None:
        input_str  = self._input_var.get().strip()
        output_str = self._output_var.get().strip()

        if not input_str:
            messagebox.showerror("Missing input", "Please select an input folder.")
            return
        input_dir = Path(input_str)
        if not input_dir.exists() or not input_dir.is_dir():
            messagebox.showerror("Invalid input",
                                 f"Input folder does not exist:\n{input_dir}")
            return
        if not output_str:
            messagebox.showerror("Missing output", "Please select an output folder.")
            return
        output_dir = Path(output_str)

        # Guard: output inside input with overwrite
        try:
            if (output_dir == input_dir or input_dir in output_dir.parents) \
               and self._overwrite_var.get():
                if not messagebox.askyesno(
                    "Warning",
                    "Output folder is inside the input folder and Overwrite is ON.\n"
                    "Source files could be overwritten. Continue?"
                ):
                    return
        except Exception:
            pass

        # Write-permission test
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            probe = output_dir / ".write_test"
            probe.touch()
            probe.unlink()
        except PermissionError:
            messagebox.showerror("Permission denied",
                                 f"Cannot write to output folder:\n{output_dir}")
            return
        except Exception as e:
            messagebox.showerror("Output folder error", str(e))
            return

        # Resize params
        mode   = self._resize_mode.get()
        width  = height = None
        scale  = None

        if mode == "custom":
            try:
                width  = int(self._width_var.get())  if self._width_var.get().strip()  else None
                height = int(self._height_var.get()) if self._height_var.get().strip() else None
            except ValueError:
                messagebox.showerror("Invalid size", "Width and height must be integers.")
                return
            if width is None and height is None:
                messagebox.showerror("Missing size", "Enter at least one dimension.")
                return
            if (width and width <= 0) or (height and height <= 0):
                messagebox.showerror("Invalid size", "Dimensions must be positive.")
                return

        elif mode == "scale":
            try:
                scale = float(self._scale_var.get()) / 100.0
                if scale <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Invalid scale", "Scale must be a positive number.")
                return
            if scale > 4.0 and not messagebox.askyesno(
                "Large upscale warning",
                f"Scale {scale*100:.0f}% will significantly increase image sizes\n"
                "and may require large amounts of RAM and disk space.\nContinue?"
            ):
                return

        filter_ext = self._parse_filter_ext(self._filter_ext_var.get())

        # Disk space pre-flight
        try:
            files_est, _ = collect_images(input_dir, self._recursive_var.get(), filter_ext)
            if files_est:
                estimated = estimate_output_bytes(
                    files_est, self._fmt_var.get(),
                    mode, scale or 1.0, width or 0, height or 0,
                )
                ok_space, warn_space, msg_space = check_disk_space(output_dir, estimated)
                if not ok_space:
                    messagebox.showerror("Insufficient disk space", msg_space)
                    return
                if warn_space and not messagebox.askyesno("Disk space warning", msg_space):
                    return
        except Exception:
            pass

        self._last_output_dir = str(output_dir)
        self._save_current_settings()
        self._stop_event.clear()
        self._msg_queue = queue.Queue()
        self._convert_btn.configure(state=tk.DISABLED)
        self._stop_btn.configure(state=tk.NORMAL)
        self._open_output_btn.configure(state=tk.DISABLED)
        self._clear_log()
        self._status_var.set("Starting…")

        self._worker = threading.Thread(
            target=run_conversion,
            kwargs=dict(
                input_dir=input_dir,
                output_dir=output_dir,
                fmt=self._fmt_var.get(),
                resize_mode=mode,
                width=width,
                height=height,
                scale=scale,
                keep_aspect=self._aspect_var.get(),
                jpeg_quality=self._quality_var.get(),
                overwrite=self._overwrite_var.get(),
                recursive=self._recursive_var.get(),
                num_workers=self._workers_var.get(),
                verify_output=self._verify_var.get(),
                preserve_metadata=self._preserve_meta_var.get(),
                dry_run=self._dry_run_var.get(),
                filter_ext=filter_ext,
                tiff_compression=self._tiff_comp_var.get(),
                png_compression=self._png_comp_var.get(),
                msg_queue=self._msg_queue,
                stop_event=self._stop_event,
                filename_suffix=self._suffix_var.get().strip(),
                webp_lossless=self._webp_lossless_var.get(),
                keep_16bit=self._tiff_16bit_var.get(),
            ),
            daemon=True,
        )
        self._worker.start()

    # ── Queue polling (25 fps, drains up to 30 messages per tick) ────────

    def _poll_queue(self) -> None:
        try:
            for _ in range(30):
                kind, *args = self._msg_queue.get_nowait()

                if kind == "log":
                    tag, message = args
                    self._append_log(tag, message)

                elif kind == "progress":
                    current, total, rate, remaining, fname = args
                    self._progress_var.set(current / total * 100)
                    self._status_var.set(
                        f"{current:,} / {total:,}  ({rate:.1f} files/s)  —  {fname}"
                    )
                    if remaining > 0 and current < total:
                        m, s = divmod(int(remaining), 60)
                        self._eta_var.set(f"ETA  {m}m {s:02d}s")
                    else:
                        self._eta_var.set("")

                elif kind == "done":
                    ok, skipped, errors, total = args
                    self._convert_btn.configure(state=tk.NORMAL)
                    self._stop_btn.configure(state=tk.DISABLED)
                    self._progress_var.set(100)
                    self._eta_var.set("")
                    self._status_var.set(
                        f"Done  —  {ok:,} converted  |  {skipped:,} skipped  "
                        f"|  {errors:,} error(s)  |  {total:,} total"
                    )
                    sep = "=" * 56
                    self._append_log("done",
                        f"\n{sep}\n"
                        f"Finished:  {ok:,} converted  |  {skipped:,} skipped  "
                        f"|  {errors:,} error(s)\n{sep}"
                    )
                    if errors:
                        self._append_log("warn",
                            f"[WARN] {errors} file(s) had errors — "
                            "use Export Log to save a full report.")
                    if ok > 0:
                        self._open_output_btn.configure(state=tk.NORMAL)
                    if self._log_to_file_var.get():
                        self._auto_save_log()

        except queue.Empty:
            pass
        finally:
            self.after(40, self._poll_queue)

    def _append_log(self, tag: str, message: str) -> None:
        self._log_lines.append(message)
        # Cap to 5 000 lines to prevent memory bloat on huge datasets
        if len(self._log_lines) > 5000:
            self._log_lines = self._log_lines[-4000:]
            self._log_widget.configure(state=tk.NORMAL)
            self._log_widget.delete("1.0", tk.END)
            self._log_widget.insert(tk.END,
                                    "--- earlier lines truncated ---\n", "warn")
            self._log_widget.configure(state=tk.DISABLED)

        self._log_widget.configure(state=tk.NORMAL)
        fv = self._log_filter_vars.get(tag)
        elide = not fv.get() if fv is not None else False
        self._log_widget.tag_configure(tag, elide=elide)
        self._log_widget.insert(tk.END, message + "\n", tag)
        self._log_widget.see(tk.END)
        self._log_widget.configure(state=tk.DISABLED)

    # ── Menu handlers ─────────────────────────────────────────────────────

    def _export_session(self) -> None:
        self._save_current_settings()
        path = filedialog.asksaveasfilename(
            title="Export session",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        session = dict(self._settings)
        session.update({
            "_app_version":    VERSION,
            "_pillow_version": Image.__version__,
            "_python_version": sys.version,
            "_platform":       sys.platform,
            "_exported_at":    time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(session, f, indent=2, ensure_ascii=False)
            messagebox.showinfo("Session exported", f"Saved to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    def _import_session(self) -> None:
        path = filedialog.askopenfilename(
            title="Import session",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if not k.startswith("_") and k in self._settings:
                    self._settings[k] = v
            self._load_settings_to_ui()
            messagebox.showinfo("Session imported", "Settings restored.")
        except Exception as e:
            messagebox.showerror("Import failed", str(e))

    def _open_log_folder(self) -> None:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(LOG_DIR)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(LOG_DIR)])
            else:
                subprocess.Popen(["xdg-open", str(LOG_DIR)])
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _show_shortcuts(self) -> None:
        messagebox.showinfo("Keyboard Shortcuts",
            "Ctrl+Enter   Start conversion\n"
            "Escape       Stop conversion\n"
            "Ctrl+L       Clear log\n"
            "Alt+F4       Quit"
        )

    def _show_about(self) -> None:
        messagebox.showinfo(
            f"About  Image Converter  v{VERSION}",
            f"Image Converter  v{VERSION}\n\n"
            f"Python   {sys.version.split()[0]}\n"
            f"Pillow   {Image.__version__}\n\n"
            "Designed for scientists working with large local datasets.\n"
            "Parallel processing  |  Metadata-preserving  |  100% offline\n\n"
            f"Settings:  {CONFIG_FILE}\n"
            f"Logs:      {LOG_DIR}"
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _auto_save_log(self) -> None:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            log_path = LOG_DIR / f"conversion_{ts}.txt"
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self._log_lines))
        except Exception:
            pass

    def _center_window(self) -> None:
        geom = self._settings.get("window_geometry", "")
        if geom:
            try:
                self.geometry(geom)
                return
            except Exception:
                pass
        self.update_idletasks()
        w  = 820
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        tb = 60
        h  = min(760, sh - tb)
        x  = (sw - w) // 2
        y  = max(0, (sh - h - tb) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _save_current_settings(self) -> None:
        s = self._settings
        s["input_dir"]        = self._input_var.get()
        s["output_dir"]       = self._output_var.get()
        s["fmt"]              = self._fmt_var.get()
        s["jpeg_quality"]     = self._quality_var.get()
        s["resize_mode"]      = self._resize_mode.get()
        s["width"]            = self._width_var.get()
        s["height"]           = self._height_var.get()
        s["scale"]            = self._scale_var.get()
        s["keep_aspect"]      = self._aspect_var.get()
        s["overwrite"]        = self._overwrite_var.get()
        s["recursive"]        = self._recursive_var.get()
        s["num_workers"]      = self._workers_var.get()
        s["verify_output"]    = self._verify_var.get()
        s["preserve_metadata"]= self._preserve_meta_var.get()
        s["dry_run"]          = self._dry_run_var.get()
        s["tiff_compression"] = self._tiff_comp_var.get()
        s["png_compression"]  = self._png_comp_var.get()
        s["filter_ext"]       = self._filter_ext_var.get()
        s["log_to_file"]      = self._log_to_file_var.get()
        s["window_geometry"]  = self.geometry()
        s["filename_suffix"]  = self._suffix_var.get()
        s["webp_lossless"]    = self._webp_lossless_var.get()
        s["tiff_16bit"]       = self._tiff_16bit_var.get()
        save_settings(s)

    def _load_settings_to_ui(self) -> None:
        s = self._settings
        self._input_var.set(s.get("input_dir",  ""))
        self._output_var.set(s.get("output_dir", ""))
        self._fmt_var.set(s.get("fmt", "PNG"))
        self._quality_var.set(s.get("jpeg_quality", 90))
        self._resize_mode.set(s.get("resize_mode", "none"))
        self._width_var.set(s.get("width",  ""))
        self._height_var.set(s.get("height", ""))
        self._scale_var.set(s.get("scale", 50.0))
        self._aspect_var.set(s.get("keep_aspect", True))
        self._overwrite_var.set(s.get("overwrite", False))
        self._recursive_var.set(s.get("recursive", False))
        self._workers_var.set(s.get("num_workers", DEFAULTS["num_workers"]))
        self._verify_var.set(s.get("verify_output", True))
        self._preserve_meta_var.set(s.get("preserve_metadata", True))
        self._dry_run_var.set(s.get("dry_run", False))
        self._tiff_comp_var.set(s.get("tiff_compression", "tiff_lzw"))
        self._png_comp_var.set(s.get("png_compression", 6))
        self._filter_ext_var.set(s.get("filter_ext", ""))
        self._log_to_file_var.set(s.get("log_to_file", False))
        self._suffix_var.set(s.get("filename_suffix", ""))
        self._webp_lossless_var.set(s.get("webp_lossless", False))
        self._tiff_16bit_var.set(s.get("tiff_16bit", False))
        self._on_resize_mode()
        self._on_fmt_change()


# ─────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if load_settings().get("log_to_file"):
        setup_file_logging()
    app = ImageConverterApp()
    app.mainloop()
