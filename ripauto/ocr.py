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
MIN_SEGMENT = 8      # pink pixels in a row that count as the length line itself
BACKGROUND = (45, 45, 45)

# The whole token before "inch": a digit, then digits, OCR look-alikes and
# stray spaces ("183 AOO inch"). "irch": 2026-10-16 02:44.
_INCH = re.compile(r"(?<![\w.])(\d[\dOoA.,]*(?: [\dOoA.,]+)*) ?i[nr]ch", re.I)
# Windows OCR misreads in PO-TRY's label font (all 41 saved previews,
# 2026-10-15..19): "4" as "A" ("183A00", "89A90"), "0" as "O" ("48.1 OO").
_LOOKALIKES = str.maketrans({"A": "4", "a": "4", "O": "0", "o": "0", ",": ".", " ": None})
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


# ---------------- parsing ----------------

def inch_values(lines):
    """Every 'N inch' number in OCR lines. Look-alike letters are mapped back
    to digits, a comma is the decimal point, and a 4+ digit number with no
    point gets one before its last three digits (PO-TRY always shows three
    decimals). A token that still isn't a number is skipped, so it counts as
    unreadable rather than as part of a number (2026-10-19: "183A00" once
    gave 0.0 from its last two digits)."""
    out = []
    for line in lines:
        for m in _INCH.finditer(line):
            text = m.group(1).translate(_LOOKALIKES)
            if not _NUMBER.fullmatch(text):
                continue
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
    # The sheet's top is where the line's first real segment starts. Skip short
    # crossings: on a narrow run the width line pokes out left across this
    # column (2026-10-15 13:00 run: it cut the width label in half).
    y = 0
    while y < h:
        if is_pink(px[x, y]):
            start = y
            while y < h and is_pink(px[x, y]):
                y += 1
            if y - start >= MIN_SEGMENT:
                return x, start
        y += 1
    return None


TEXT = (255, 255, 255)
LIT = 75             # any channel above this next to a line is (ClearType-tinted) label text


def _fill(src, xx, yy, w, h, reach=3):
    """What a pink pixel would be without the line. The labels are white text
    on dark, drawn with ClearType colour fringes; so with lit pixels on both
    sides of it (left/right first, then up/down) it was text, else background."""
    def nearest(dx, dy):
        for step in range(1, reach + 1):
            x2, y2 = xx + dx * step, yy + dy * step
            if not (0 <= x2 < w and 0 <= y2 < h):
                return None
            if not is_pink(src[x2, y2]):
                return src[x2, y2]
        return None
    for a, b in ((nearest(-1, 0), nearest(1, 0)), (nearest(0, -1), nearest(0, 1))):
        if a and b:
            return TEXT if max(a[:3]) > LIT and max(b[:3]) > LIT else BACKGROUND
    return BACKGROUND


def clean(image, scale=SCALE):
    """Paint pink out, then scale up for OCR.

    On a very long sheet the preview draws the sheet narrower than its own
    width label, and the width line's end tick runs through the text
    (2026-10-16 02:44: "22.500 irch"). So a pink pixel takes the colour of
    its neighbours rather than flat background: a letter stroke it crosses
    stays whole."""
    img = image.convert("RGB")
    src = img.copy().load()
    px = img.load()
    for yy in range(img.height):
        for xx in range(img.width):
            if is_pink(src[xx, yy]):
                px[xx, yy] = _fill(src, xx, yy, img.width, img.height)
    return img.resize((img.width * scale, img.height * scale), Image.LANCZOS)


def label_strips(image, scale=SCALE):
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
    return clean(across, scale), clean(sideways, scale).rotate(90, expand=True)


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


def read_preview_size(image, workdir, stem, max_width_in=None, scales=(SCALE,), accept=None):
    """OCR a captured preview image. Saves the capture and the OCR inputs in
    workdir ({stem}-capture/-across/-sideways.png, "-x4" etc. for later zooms)
    for checking later.

    Tries each zoom in `scales` until a reading passes accept(size) (default:
    any reading at all); a misread at one zoom is often clean at another
    (2026-10-19: "183A00" at x3, "183.400" at x5). Returns the first passing
    reading, else the first reading, else None, with
    {"how", "across", "sideways", "tries"}; across/sideways are the last OCR."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    image = image.convert("RGB")
    image.save(workdir / f"{stem}-capture.png")
    accept = accept or (lambda size: True)
    first, tries, seen = None, [], {}
    for scale in scales:
        strips = label_strips(image, scale)
        if strips is not None:
            how, (across_img, sideways_img) = "label strips", strips
        else:
            big = image.resize((image.width * scale, image.height * scale), Image.LANCZOS)
            how, across_img, sideways_img = "whole image", big, big.rotate(90, expand=True)
        tag = "" if scale == scales[0] else f"-x{scale}"
        across_path = workdir / f"{stem}{tag}-across.png"
        sideways_path = workdir / f"{stem}{tag}-sideways.png"
        across_img.save(across_path)
        sideways_img.save(sideways_path)
        across, sideways = ocr_lines(across_path), ocr_lines(sideways_path)
        size = (size_from_ocr(across, sideways, max_width_in)
                if across is not None and sideways is not None else None)
        tries.append({"scale": scale, "across": across, "sideways": sideways, "size": size})
        seen = {"how": how, "across": across, "sideways": sideways, "tries": tries}
        if size is not None and accept(size):
            return size, seen
        first = first or size
    return first, seen
