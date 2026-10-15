"""
ocr.py  -  read the run size off PO-TRY's Print preview (SPEC §7 step 6).

The preview draws the job's width across the top ("22.500 inch") and its
length sideways down the left edge ("65.237 inch"), inside a graphics area
that UI automation can't see into. So the driver captures that area and this
module reads it with Windows' built-in OCR (tools/ocr_image.ps1):

  - the image as-is gives the width
  - the image turned 90° counter-clockwise gives the length

Anything other than exactly one "N inch" in each pass counts as unreadable,
and the gate cancels the run.
"""

import re
import subprocess
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
OCR_SCRIPT = REPO / "tools" / "ocr_image.ps1"
SCALE = 3            # small UI text reads far better upscaled
OCR_TIMEOUT = 60

_INCH = re.compile(r"(\d+(?:[.,]\d+)?)\s*inch", re.I)


def inch_values(lines):
    """Every 'N inch' number in OCR lines; a comma is read as the decimal point."""
    return [float(m.group(1).replace(",", ".")) for line in lines for m in _INCH.finditer(line)]


def size_from_ocr(across_lines, sideways_lines, max_width_in=None):
    """(width, length) from the two OCR passes, or None if not exactly one each."""
    across, sideways = inch_values(across_lines), inch_values(sideways_lines)
    if len(across) != 1 or len(sideways) != 1:
        return None
    width, length = across[0], sideways[0]
    if max_width_in and width > max_width_in + 1:
        return None      # the "width" isn't a plausible width: don't trust either number
    return width, length


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
    """OCR a captured preview image. Saves the two OCR inputs in workdir as
    {stem}-across.png / {stem}-sideways.png (kept for checking later).
    Returns ((width, length) or None, {"across": lines, "sideways": lines})."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    big = image.convert("RGB").resize((image.width * SCALE, image.height * SCALE), Image.LANCZOS)
    across_path = workdir / f"{stem}-across.png"
    sideways_path = workdir / f"{stem}-sideways.png"
    big.save(across_path)
    big.rotate(90, expand=True).save(sideways_path)
    across, sideways = ocr_lines(across_path), ocr_lines(sideways_path)
    seen = {"across": across, "sideways": sideways}
    if across is None or sideways is None:
        return None, seen
    return size_from_ocr(across, sideways, max_width_in), seen
