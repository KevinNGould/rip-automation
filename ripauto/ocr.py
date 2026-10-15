"""
ocr.py  -  read the run size off PO-TRY's Print preview (SPEC §7 step 6).

The preview draws the job's width across the top ("22.500 inch") and its
length sideways down the left edge ("135.400 inch"), each on a pink dimension
line, inside a graphics area that UI automation can't see into. The driver
captures that area and this module reads it with Windows' built-in OCR
(tools/ocr_image.ps1):

  1. find the pink length line (the busiest pink column on the left); it
     runs from the sheet's top edge down, and the width label sits above it
  2. cut a thin strip around each, so no sheet artwork reaches OCR, and
     paint the pink out (a pink tick touching "22.500" once read as "*2.500")
  3. OCR the width strip as-is and the length strip turned 90° CCW
  4. PO-TRY always prints three decimals, so "135400 inch" (point lost) is
     read as 135.400

Anything other than exactly one plausible value from each strip counts as
unreadable, and the gate cancels the run. If the pink lines can't be found,
the whole image is OCR'd both ways instead.
"""

import re
import subprocess
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
OCR_SCRIPT = REPO / "tools" / "ocr_image.ps1"
SCALE = 3            # small UI text reads far better upscaled
OCR_TIMEOUT = 60
STRIP = 14           # half-height of a label strip, in captured pixels
BACKGROUND = (45, 45, 45)

_INCH = re.compile(r"(\d+(?:[.,]\d+)?)\s*inch", re.I)


# ---------------- parsing ----------------

def inch_values(lines):
    """Every 'N inch' number in OCR lines. A comma is read as the decimal
    point, and a 4+ digit number with no point gets one before its last three
    digits (PO-TRY always shows three decimals)."""
    out = []
    for line in lines:
        for m in _INCH.finditer(line):
            text = m.group(1).replace(",", ".")
            if "." not in text and len(text) >= 4:
                text = text[:-3] + "." + text[-3:]
            out.append(float(text))
    return out


def size_from_ocr(across_lines, sideways_lines, max_width_in=None):
    """(width, length) from the two OCR passes, or None if not exactly one
    value each, or the width isn't a plausible canvas width."""
    across, sideways = inch_values(across_lines), inch_values(sideways_lines)
    if len(across) != 1 or len(sideways) != 1:
        return None
    width, length = across[0], sideways[0]
    if max_width_in and not (max_width_in - 3 <= width <= max_width_in + 1):
        return None      # a misread width means neither number can be trusted
    return width, length


# ---------------- image handling ----------------

def is_pink(p):
    r, g, b = p[:3]
    return r > 150 and b > 150 and g < 120


def length_line(image):
    """(column, top row) of the length dimension line, or None.

    It's the pinkest column in the left 60% of the preview and runs the
    full height of the sheet (broken in the middle by its own label), so its
    first pink pixel is the sheet's top edge; the width label sits above that.
    (The width line itself is unreliable to find: on a long, narrow run its
    label covers most of it.)"""
    img = image.convert("RGB")
    w, h = img.size
    px = img.load()
    cols = [sum(is_pink(px[x, y]) for y in range(h)) for x in range(int(w * 0.6))]
    if not cols:
        return None
    x = max(range(len(cols)), key=cols.__getitem__)
    if cols[x] < 40:
        return None
    top = next(y for y in range(h) if is_pink(px[x, y]))
    return x, top


def clean(image):
    """Paint pink out to the background, then scale up for OCR."""
    img = image.convert("RGB")
    px = img.load()
    for yy in range(img.height):
        for xx in range(img.width):
            if is_pink(px[xx, yy]):
                px[xx, yy] = BACKGROUND
    return img.resize((img.width * SCALE, img.height * SCALE), Image.LANCZOS)


def label_strips(image):
    """(width strip, length strip turned upright), or None without the pink lines."""
    found = length_line(image)
    if found is None:
        return None
    x, sheet_top = found
    if sheet_top < 8:
        return None                              # no room above for the width label
    w, h = image.size
    across = image.crop((0, 0, w, sheet_top))
    sideways = image.crop((max(0, x - STRIP), sheet_top, min(w, x + STRIP), h))
    return clean(across), clean(sideways).rotate(90, expand=True)


# ---------------- OCR ----------------

def ocr_lines(image_path):
    """Text lines from Windows OCR, or None if OCR itself failed."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(OCR_SCRIPT), str(image_path)],
            capture_output=True, text=True, timeout=OCR_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def read_preview_size(image, workdir, stem, max_width_in=None):
    """OCR a captured preview image. Saves the capture and the two OCR inputs
    in workdir ({stem}-capture/-across/-sideways.png) for checking later.
    Returns ((width, length) or None, {"how", "across", "sideways"})."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    image = image.convert("RGB")
    image.save(workdir / f"{stem}-capture.png")
    strips = label_strips(image)
    if strips is not None:
        how, (across_img, sideways_img) = "label strips", strips
    else:
        big = image.resize((image.width * SCALE, image.height * SCALE), Image.LANCZOS)
        how, across_img, sideways_img = "whole image", big, big.rotate(90, expand=True)
    across_path = workdir / f"{stem}-across.png"
    sideways_path = workdir / f"{stem}-sideways.png"
    across_img.save(across_path)
    sideways_img.save(sideways_path)
    across, sideways = ocr_lines(across_path), ocr_lines(sideways_path)
    seen = {"how": how, "across": across, "sideways": sideways}
    if across is None or sideways is None:
        return None, seen
    return size_from_ocr(across, sideways, max_width_in), seen
