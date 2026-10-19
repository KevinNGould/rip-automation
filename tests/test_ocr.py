import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ripauto.ocr import inch_values, length_line, read_preview_size, size_from_ocr

ARIAL = Path(r"C:\Windows\Fonts\arial.ttf")
PINK = (230, 0, 230)
DARK = (45, 45, 45)


def fake_preview(width_text, length_text, sheet_h=430, sheet_w=150, width_line_from=None,
                 tick_through_label_at=None):
    """A stand-in for PO-TRY's preview: dark area, white sheet, pink dimension
    lines with ticks touching the labels, width label above the sheet and the
    length label turned sideways on the left line, breaking it in two."""
    font = ImageFont.truetype(str(ARIAL), 15)
    img = Image.new("RGB", (360, 500), DARK)
    d = ImageDraw.Draw(img)
    top, left = 30, 110
    d.rectangle((left, top, left + sheet_w, top + sheet_h), fill=(250, 250, 250))
    # width line, ticks right against the text
    d.line((width_line_from if width_line_from is not None else left, 18, left + sheet_w, 18),
           fill=PINK, width=1)
    d.line((left, 8, left, top), fill=PINK)
    d.line((left + sheet_w, 8, left + sheet_w, top), fill=PINK)
    d.rectangle((left + 25, 9, left + sheet_w - 25, 27), fill=DARK)
    d.text((left + 28, 9), width_text, font=font, fill=(255, 255, 255))
    if tick_through_label_at is not None:
        # a very long sheet is drawn narrower than its label: a tick crosses the text
        d.line((left + 28 + tick_through_label_at, 8, left + 28 + tick_through_label_at, top),
               fill=PINK)
    # length line on the left, corner ticks at the sheet's top and bottom
    x = 80
    d.line((x, top, x, top + sheet_h), fill=PINK)
    d.line((x - 25, top, left, top), fill=PINK)
    d.line((x - 25, top + sheet_h, left, top + sheet_h), fill=PINK)
    label = Image.new("RGB", (120, 22), DARK)
    ImageDraw.Draw(label).text((4, 2), length_text, font=font, fill=(255, 255, 255))
    img.paste(label.rotate(-90, expand=True), (x - 11, 180))
    return img


class Parse(unittest.TestCase):
    def test_inch_values(self):
        self.assertEqual(inch_values(["22.500 inch"]), [22.5])
        self.assertEqual(inch_values(["65,237 inch", "DOLPHINS 97", "2025"]), [65.237])
        self.assertEqual(inch_values(["601.001inch"]), [601.001])

    def test_lost_decimal_point_is_restored(self):
        # OCR on the #47946 + #47947 preview, 2026-10-15 11:30: "135.400 inch" read as "135400 inch".
        self.assertEqual(inch_values(["135400 inch"]), [135.4])
        self.assertEqual(inch_values(["7200 inch"]), [7.2])
        self.assertEqual(inch_values(["650 inch"]), [650.0])     # 3 digits: left alone
        # Every misread in the saved previews, 2026-10-15..19: "4" read as "A",
        # "0" as "O", stray spaces, decimal point lost.
        for text, want in [("183A00 inch", 183.4), ("183 AOO inch", 183.4), ("48.1 OO inch", 48.1),
                           ("69AOO inch", 69.4), ("89A90 inch", 89.49), ("33A00 inch", 33.4),
                           ("189AOO inch", 189.4)]:
            self.assertEqual(inch_values([text]), [want], text)
        # A token that still isn't a number is unreadable, never its tail
        # (2026-10-19 13:25: "183A00" gave 0.0 from the "00").
        self.assertEqual(inch_values(["18X.400 inch"]), [])
        self.assertEqual(inch_values(["DOLPHINS 97", "LOGO inch"]), [])
        # 2026-10-16 02:44, #48029: "22.500 irch" (a tick through the n) and "260451 inch".
        self.assertEqual(size_from_ocr(["22.500 irch"], ["260451 inch"], 23), (22.5, 260.451))

    def test_size_from_ocr(self):
        self.assertEqual(size_from_ocr(["22.500 inch", "THE DEARBORN ACADEMY"], ["65.237 inch"], 23),
                         (22.5, 65.237))

    def test_missing_or_extra_values_are_unreadable(self):
        self.assertIsNone(size_from_ocr([], ["65.237 inch"]))
        self.assertIsNone(size_from_ocr(["22.500 inch"], []))
        self.assertIsNone(size_from_ocr(["22.500 inch", "4 inch logo"], ["65.237 inch"]))
        self.assertIsNone(size_from_ocr(["22.500 inch"], ["65.237 inch", "12 inch"]))

    def test_implausible_width_is_unreadable(self):
        # 11:30: a pink tick merged into the first "2": "*2.500 inchk".
        self.assertIsNone(size_from_ocr(["*2.500 inchk"], ["135400 inch"], max_width_in=23))
        self.assertIsNone(size_from_ocr(["601.001 inch"], ["22.500 inch"], max_width_in=23))


class Geometry(unittest.TestCase):
    def test_finds_length_line_and_sheet_top(self):
        self.assertEqual(length_line(fake_preview("22.500 inch", "135.400 inch")), (80, 30))

    def test_width_line_crossing_the_length_line_is_not_the_sheet_top(self):
        # 2026-10-15 13:00: on a narrow run the width line poked out left across
        # the length line's column, and the width label got cut in half.
        img = fake_preview("22.500 inch", "195.037 inch", width_line_from=60)
        self.assertEqual(length_line(img), (80, 30))

    def test_pink_line_through_a_letter_keeps_the_stroke(self):
        # 2026-10-16 02:44: the width tick ran through the "n" of "inch".
        from ripauto.ocr import SCALE, clean
        img = Image.new("RGB", (9, 9), (40, 40, 40))
        d = ImageDraw.Draw(img)
        d.rectangle((3, 0, 5, 8), fill=(255, 255, 255))       # a stem 3 px wide
        d.line((4, 0, 4, 8), fill=PINK)                        # tick down its middle
        d.line((7, 0, 7, 8), fill=PINK)                        # tick over background
        out = clean(img)
        mid = lambda x: out.getpixel((x * SCALE + 1, 4 * SCALE + 1))
        self.assertGreater(min(mid(4)), 200)                   # stem whole
        self.assertLess(max(mid(7)), 60)                       # background stays dark

    def test_no_pink_no_line(self):
        self.assertIsNone(length_line(Image.new("RGB", (100, 100), DARK)))


class Zooms(unittest.TestCase):
    """read_preview_size tries the next zoom only when a reading doesn't pass."""

    def run_with(self, by_scale, accept=None):
        import ripauto.ocr as o
        saved = o.ocr_lines
        calls = []

        def fake(path):
            scale = 3 if "-x" not in Path(path).name else int(Path(path).name.split("-x")[1][0])
            calls.append(scale)
            across, sideways = by_scale[scale]
            return across if "across" in Path(path).name else sideways
        o.ocr_lines = fake
        try:
            with tempfile.TemporaryDirectory() as tmp:
                size, seen = read_preview_size(fake_preview("22.500 inch", "183.400 inch"), tmp, "t",
                                               23, scales=(3, 4, 5), accept=accept)
        finally:
            o.ocr_lines = saved
        return size, seen, sorted(set(calls))

    def test_first_good_reading_stops(self):
        size, seen, scales = self.run_with({3: (["22.500 inch"], ["183.400 inch"])})
        self.assertEqual((size, scales, len(seen["tries"])), ((22.5, 183.4), [3], 1))

    def test_unreadable_then_clean_at_next_zoom(self):
        size, seen, scales = self.run_with({3: (["22.500 inch"], ["18X.400 inch"]),
                                            4: (["22.500 inch"], ["183.400 inch"])})
        self.assertEqual((size, scales), ((22.5, 183.4), [3, 4]))

    def test_off_plan_reading_is_checked_at_other_zooms_then_kept(self):
        # A genuinely short preview reads short at every zoom: the first reading
        # is returned so the gate can say why.
        short = (["22.500 inch"], ["24.020 inch"])
        size, seen, scales = self.run_with({3: short, 4: short, 5: short},
                                           accept=lambda s: abs(s[1] - 183.4) < 3)
        self.assertEqual((size, scales, len(seen["tries"])), ((22.5, 24.02), [3, 4, 5], 3))


@unittest.skipUnless(sys.platform == "win32" and ARIAL.exists() and shutil.which("powershell"),
                     "needs Windows OCR")
class WindowsOcr(unittest.TestCase):
    def read(self, img):
        with tempfile.TemporaryDirectory() as tmp:
            size, seen = read_preview_size(img, tmp, "t", max_width_in=23)
        return size, seen

    def test_reads_a_drawn_preview(self):
        size, seen = self.read(fake_preview("22.500 inch", "601.001 inch"))
        self.assertEqual(seen["how"], "label strips")
        self.assertEqual(size, (22.5, 601.001), seen)

    def test_reads_a_narrow_run_with_crossing_width_line(self):
        size, seen = self.read(fake_preview("22.500 inch", "195.037 inch", width_line_from=60))
        self.assertEqual(size, (22.5, 195.037), seen)

    def test_reads_a_long_run_with_a_tick_through_the_width_label(self):
        size, seen = self.read(fake_preview("22.500 inch", "260.451 inch", tick_through_label_at=58))
        self.assertEqual(size, (22.5, 260.451), seen)

    def test_reads_a_short_run(self):
        size, seen = self.read(fake_preview("22.500 inch", "135.400 inch", sheet_h=200))
        self.assertEqual(size, (22.5, 135.4), seen)


if __name__ == "__main__":
    unittest.main()
