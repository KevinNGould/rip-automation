"""Test fixtures: tiny PNGs and Shopify-shaped order nodes."""

import struct
import zlib

EIGHT = "DTF Color-Pro (8 Color CMYK+RGBO)"
CMYK = "DTF (4 Color CMYK) - Gang Sheet Builder"
RULES = [{"title_contains": EIGHT, "kind": "gang_sheet"}]


def _chunk(ctype, data):
    return (struct.pack(">I", len(data)) + ctype + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF))


def png_bytes(width, height, ppm=None, unit=1):
    """A PNG header with the given pixel size and optional pHYs (pixels per
    unit). The image data is a stub; only the header chunks matter here."""
    out = b"\x89PNG\r\n\x1a\n"
    out += _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    out += _chunk(b"bKGD", b"\x00\x00\x00\x00\x00\x00")
    if ppm is not None:
        out += _chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, unit))
    out += _chunk(b"IDAT", zlib.compress(b"\x00"))
    out += _chunk(b"IEND", b"")
    return out


def line(item_id, title=EIGHT, variant="22in x 12in (1ft)", qty=1, attrs=None):
    if attrs is None:
        attrs = {"_Print Ready File": f"https://example.test/{item_id}.png"}
    return {
        "id": f"gid://shopify/LineItem/{item_id}",
        "title": title,
        "variantTitle": variant,
        "quantity": qty,
        "customAttributes": [{"key": k, "value": v} for k, v in attrs.items()],
    }


def order(name, legacy_id, lines, customer=None):
    node = {
        "name": name,
        "legacyResourceId": legacy_id,
        "createdAt": "2026-10-15T09:00:00Z",
        "lineItems": {"nodes": lines},
    }
    if customer is not None:
        node["customer"] = {"displayName": customer}
    return node
