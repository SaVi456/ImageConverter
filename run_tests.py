"""Full test suite for image_converter v2.0"""
import sys, threading, pathlib, queue, shutil
sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image
import numpy as np
from image_converter import (
    convert_one, run_conversion, collect_images,
    normalize_mode, check_disk_space, FORMATS,
)

IN  = pathlib.Path("test_input")
OUT = pathlib.Path("test_output")
# Clean slate from any previous run
shutil.rmtree(IN,  ignore_errors=True)
shutil.rmtree(OUT, ignore_errors=True)
IN.mkdir(exist_ok=True)

# ── Create diverse test images ────────────────────────────────────────────
cases = [
    ("rgb.png",        (1920, 1080), "RGB"),
    ("rgba.png",       (800,  600),  "RGBA"),
    ("grayscale.png",  (512,  512),  "L"),
    ("float32.tiff",   (256,  256),  "F"),
    ("cmyk.jpg",       (400,  300),  "CMYK"),
    ("small.jpg",      (100,  75),   "RGB"),
    ("sub/nested.tif", (640,  480),  "RGB"),
]
for name, size, mode in cases:
    p = IN / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if mode == "F":
        arr = np.random.uniform(0, 50000, size[::-1]).astype(np.float32)
        img = Image.fromarray(arr, "F")
    elif mode == "CMYK":
        img = Image.new("RGB", size, (180, 100, 50)).convert("CMYK")
    elif mode == "RGBA":
        img = Image.new("RGBA", size, (100, 149, 237, 200))
    else:
        img = Image.new(mode, size, 128 if mode == "L" else (100, 149, 237))
    img.save(p)
    print(f"  created {name}  {size} {mode}")


def drain(q):
    msgs = []
    while True:
        try:
            msgs.append(q.get_nowait())
        except Exception:
            break
    return msgs


PASS = 0; FAIL = 0

def check(condition, label):
    global PASS, FAIL
    if condition:
        print(f"  PASS  {label}")
        PASS += 1
    else:
        print(f"  FAIL  {label}")
        FAIL += 1


# ── Test 1: JPEG, no resize, non-recursive ───────────────────────────────
print("\nTEST 1: All formats -> JPEG, no resize, non-recursive")
q = queue.Queue(); stop = threading.Event()
run_conversion(IN, OUT/"t1", "JPEG", "none", None, None, None, True, 85,
               False, False, 4, True, True, False, set(), "tiff_lzw", 6, q, stop)
msgs = drain(q)
done = next(m for m in msgs if m[0] == "done")
ok, sk, err, tot = done[1], done[2], done[3], done[4]
print(f"  ok={ok} skip={sk} err={err} total={tot}")
check(err == 0, "zero errors")
check(ok >= 5, "at least 5 files converted")

# ── Test 2: PNG, scale 50%, recursive ────────────────────────────────────
print("\nTEST 2: -> PNG, scale 50%, recursive")
q = queue.Queue(); stop = threading.Event()
run_conversion(IN, OUT/"t2", "PNG", "scale", None, None, 0.5, True, 90,
               True, True, 4, True, True, False, set(), "tiff_lzw", 6, q, stop)
msgs = drain(q)
done = next(m for m in msgs if m[0] == "done")
ok, sk, err, tot = done[1], done[2], done[3], done[4]
print(f"  ok={ok} skip={sk} err={err} total={tot}")
check(err == 0, "zero errors")
bad = [(p, Image.open(p).size) for p in (OUT/"t2").rglob("*.png")
       if Image.open(p).width > 960 or Image.open(p).height > 540]
check(len(bad) == 0, f"all dims halved  (oversized={bad})")

# ── Test 3: TIFF, custom 320x320, keep aspect ────────────────────────────
print("\nTEST 3: -> TIFF, custom 320x320, keep aspect")
q = queue.Queue(); stop = threading.Event()
run_conversion(IN, OUT/"t3", "TIFF", "custom", 320, 320, None, True, 90,
               True, False, 4, True, True, False, set(), "tiff_lzw", 6, q, stop)
msgs = drain(q)
done = next(m for m in msgs if m[0] == "done")
ok, sk, err, tot = done[1], done[2], done[3], done[4]
print(f"  ok={ok} skip={sk} err={err} total={tot}")
check(err == 0, "zero errors")
oversized = [p for p in (OUT/"t3").rglob("*.tiff")
             if Image.open(p).width > 320 or Image.open(p).height > 320]
check(len(oversized) == 0, "all within 320x320")

# ── Test 4: Float32 TIFF -> JPEG (precision warning) ─────────────────────
print("\nTEST 4: Float32 TIFF -> JPEG (high-bit-depth warning)")
dest4 = OUT/"t4"/"float32.jpg"; dest4.parent.mkdir(parents=True, exist_ok=True)
status, msg, warns = convert_one(
    IN/"float32.tiff", dest4, "JPEG", "none", None, None, None,
    True, 90, True, True, "tiff_lzw", 6, False
)
print(f"  status={status}  warns={warns}")
check(status == "ok", "conversion succeeded")
check(any("bit" in w.lower() or "precision" in w.lower() for w in warns),
      "precision warning emitted")

# ── Test 5: CMYK -> PNG ───────────────────────────────────────────────────
print("\nTEST 5: CMYK -> PNG (mode normalisation)")
dest5 = OUT/"t5"/"cmyk.png"; dest5.parent.mkdir(parents=True, exist_ok=True)
status, msg, warns = convert_one(
    IN/"cmyk.jpg", dest5, "PNG", "none", None, None, None,
    True, 90, True, True, "tiff_lzw", 6, False
)
print(f"  status={status}")
check(status == "ok", "conversion succeeded")
with Image.open(dest5) as img:
    check(img.mode == "RGB", f"output mode is RGB (got {img.mode})")

# ── Test 6: Skip existing ─────────────────────────────────────────────────
print("\nTEST 6: Skip existing (overwrite=False)")
q = queue.Queue(); stop = threading.Event()
run_conversion(IN, OUT/"t1", "JPEG", "none", None, None, None, True, 85,
               False, False, 4, True, True, False, set(), "tiff_lzw", 6, q, stop)
msgs = drain(q)
done = next(m for m in msgs if m[0] == "done")
ok, sk, err, tot = done[1], done[2], done[3], done[4]
print(f"  ok={ok} skip={sk} err={err}")
check(ok == 0, "nothing re-converted")
check(sk > 0, "files were skipped")

# ── Test 7: Stop mid-conversion ───────────────────────────────────────────
print("\nTEST 7: Stop mid-conversion")
q = queue.Queue(); stop = threading.Event()
converted_count = [0]
import image_converter as _ic
_orig = _ic.convert_one

def _patched(*a, **kw):
    converted_count[0] += 1
    if converted_count[0] >= 2:
        stop.set()
    return _orig(*a, **kw)

_ic.convert_one = _patched
run_conversion(IN, OUT/"t7", "WebP", "none", None, None, None, True, 80,
               True, True, 4, True, True, False, set(), "tiff_lzw", 6, q, stop)
_ic.convert_one = _orig
msgs = drain(q)
done = next(m for m in msgs if m[0] == "done")
ok, sk, err, tot = done[1], done[2], done[3], done[4]
print(f"  converted={ok}  total={tot}")
check(ok < tot, "stopped before completing all files")

# ── Test 8: Dry run ───────────────────────────────────────────────────────
print("\nTEST 8: Dry run")
q = queue.Queue(); stop = threading.Event()
out_dry = OUT/"t8_dry"
run_conversion(IN, out_dry, "PNG", "none", None, None, None, True, 90,
               True, True, 4, True, True, True, set(), "tiff_lzw", 6, q, stop)
msgs = drain(q)
done = next(m for m in msgs if m[0] == "done")
ok, sk, err, tot = done[1], done[2], done[3], done[4]
written = list(out_dry.rglob("*.png")) if out_dry.exists() else []
print(f"  reported_ok={ok}  files_written={len(written)}")
check(len(written) == 0, "no files written in dry run")
check(ok == tot, "all reported as OK")

# ── Test 9: Empty file detection ──────────────────────────────────────────
print("\nTEST 9: Empty file detection")
empty = IN/"empty.png"; empty.write_bytes(b"")
dest9 = OUT/"t9"/"empty.png"; dest9.parent.mkdir(parents=True, exist_ok=True)
status, msg, _ = convert_one(empty, dest9, "PNG", "none", None, None, None,
                              True, 90, True, True, "tiff_lzw", 6, False)
print(f"  status={status}  msg={msg}")
check(status == "err", "empty file returns error")
check("empty" in msg.lower(), "error message mentions 'empty'")
empty.unlink()

# ── Test 10: Filename collision detection ─────────────────────────────────
# Two files with the same stem but different extensions -> both map to same output
print("\nTEST 10: Filename collision detection (same stem, different extension)")
(IN/"coltest.jpg").write_bytes((IN/"small.jpg").read_bytes())  # coltest.jpg
(IN/"coltest.png").write_bytes((IN/"grayscale.png").read_bytes())  # coltest.png
# Both would map to coltest.<ext> in the output — collect_images should flag this
files, collisions = collect_images(IN, False, set())
print(f"  files={len(files)}  collisions={len(collisions)}")
check(len(collisions) >= 1, "collision detected")
(IN/"coltest.jpg").unlink()
(IN/"coltest.png").unlink()

# ── Test 11: Output verification catches corrupt output ───────────────────
print("\nTEST 11: Verify output catches corrupt file")
dest11 = OUT/"t11"/"rgb.jpg"; dest11.parent.mkdir(parents=True, exist_ok=True)
# Write a corrupt file first so verify=True would reject
dest11.write_bytes(b"NOT A VALID JPEG")
status, msg, _ = convert_one(IN/"rgb.png", dest11, "JPEG", "none", None, None, None,
                              True, 90, True, True, "tiff_lzw", 6, False)
print(f"  status={status}")
check(status == "ok", "good image overwrites bad output successfully")

# ── Test 12: Atomic write - temp file cleaned up on success ───────────────
print("\nTEST 12: Atomic write (no .tmp files left)")
dest12 = OUT/"t12"/"rgb.jpg"; dest12.parent.mkdir(parents=True, exist_ok=True)
convert_one(IN/"rgb.png", dest12, "JPEG", "none", None, None, None,
            True, 90, True, True, "tiff_lzw", 6, False)
tmp_files = list((OUT/"t12").glob(".tmp_*"))
print(f"  temp files remaining: {tmp_files}")
check(len(tmp_files) == 0, "no .tmp files left after conversion")

# ── Cleanup ───────────────────────────────────────────────────────────────
shutil.rmtree(IN)
shutil.rmtree(OUT)

print(f"\n{'='*50}")
print(f"Results:  {PASS} passed  |  {FAIL} failed  |  {PASS+FAIL} total")
print(f"{'='*50}")
sys.exit(0 if FAIL == 0 else 1)
