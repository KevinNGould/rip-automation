"""
sheets.py  -  facts about one gang sheet: file name, position, size (SPEC §4, §5).

Pure functions; nothing here talks to Shopify or PO-TRY.
"""

import re
import struct

PNG_SIG = b"\x89PNG\r\n\x1a\n"
PRINT_READY_KEYS = ("_Print Ready File", "_Print Ready")   # gang sheet, image by size
INCH_PER_METER = 1 / 0.0254


# ---------- naming ----------

def safe_name(text):
    """Strip only what Windows forbids in file names; keep spaces and ( )."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text)
    return re.sub(r"\s+", " ", text).strip().rstrip(".")


def size_slug(variant_title):
    """Variant title as the app writes it in file names:
    '22in x 78in (6 1/2ft)' -> '22in x 78in 6 1-2ft'."""
    return (variant_title or "").replace("(", "").replace(")", "").replace("/", "-")


def file_stem(order_num, customer, order_id, size, position, qty):
    """Build a Gang Sheet's file name pattern, minus the design name:
    {order#}-{Customer Name}-{order ID}-{size}-{position}-({qty}), e.g.
    47918-Kaitlyn Carter-18943728582971-22in x 12in 1ft-1-(1).
    Empty parts (a blank customer name) are left out, not left as '--'."""
    parts = [str(order_num), customer or "", str(order_id or ""), size_slug(size),
             str(position), f"({qty})"]
    return safe_name("-".join(p for p in parts if p))


def print_ready_positions(line_items):
    """{line item id: 1-based position} among the order's Build-a-Gang-Sheet
    line items (those carrying a print-ready attribute), in Shopify's order.
    Other products in the order don't count, whichever machine they're for."""
    positions, n = {}, 0
    for li in line_items:
        keys = {a["key"] for a in li.get("customAttributes") or []}
        if keys.intersection(PRINT_READY_KEYS):
            n += 1
            positions[li["id"]] = n
    return positions


# ---------- size ----------

def png_header(source):
    """Pixel size and physical resolution from a PNG's header chunks.

    Returns {"width_px", "height_px", "ppm_x", "ppm_y"}; ppm_* (pixels per
    metre) are None when there's no pHYs chunk or its unit isn't metres.
    Reads only up to the first IDAT, so it's cheap on a 50 ft sheet."""
    f = open(source, "rb") if not hasattr(source, "read") else source
    try:
        if f.read(8) != PNG_SIG:
            raise ValueError("not a PNG")
        info = {"width_px": None, "height_px": None, "ppm_x": None, "ppm_y": None}
        while True:
            head = f.read(8)
            if len(head) < 8:
                break
            length, ctype = struct.unpack(">I4s", head)
            if ctype == b"IHDR":
                info["width_px"], info["height_px"] = struct.unpack(">II", f.read(length)[:8])
                f.seek(4, 1)
            elif ctype == b"pHYs":
                x, y, unit = struct.unpack(">IIB", f.read(length)[:9])
                if unit == 1 and x and y:
                    info["ppm_x"], info["ppm_y"] = x, y
                f.seek(4, 1)
            elif ctype in (b"IDAT", b"IEND"):
                break
            else:
                f.seek(length + 4, 1)
        if info["width_px"] is None:
            raise ValueError("PNG has no IHDR chunk")
        return info
    finally:
        if f is not source:
            f.close()


def png_size_in(source):
    """(width_in, length_in) from the PNG's pixels and pHYs DPI, or None."""
    h = png_header(source)
    if not h["ppm_x"]:
        return None
    return (h["width_px"] / h["ppm_x"] * INCH_PER_METER,
            h["height_px"] / h["ppm_y"] * INCH_PER_METER)


_TITLE_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*in\s*[x×]\s*(\d+(?:\.\d+)?)\s*in", re.I)


def title_size_in(variant_title):
    """(width_in, length_in) from a variant title like '22in x 78in (6 1/2ft)', or None."""
    m = _TITLE_SIZE.search(variant_title or "")
    return (float(m.group(1)), float(m.group(2))) if m else None


def _first_number(text):
    m = re.search(r"\d+(?:\.\d+)?", str(text or ""))
    return float(m.group()) if m else None


def sheet_size(png=None, attrs=None, variant_title=""):
    """Width and length in inches, with where each came from (SPEC §5).

    Length: PNG pixels / pHYs DPI, then the _Actual Height attribute, then the
    variant title. Width: PNG, then the variant title. A source that can't be
    read is skipped. Returns {"width_in", "length_in", "width_source",
    "length_source"}; values are None (source None) when nothing worked."""
    out = {"width_in": None, "length_in": None, "width_source": None, "length_source": None}
    if png is not None:
        try:
            measured = png_size_in(png)
        except (OSError, ValueError, struct.error):
            measured = None
        if measured:
            out.update(width_in=measured[0], length_in=measured[1],
                       width_source="png", length_source="png")
            return out
    actual = _first_number((attrs or {}).get("_Actual Height"))
    if actual:
        out.update(length_in=actual, length_source="attr")
    titled = title_size_in(variant_title)
    if titled:
        out.update(width_in=titled[0], width_source="title")
        if out["length_in"] is None:
            out.update(length_in=titled[1], length_source="title")
    return out
