import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ripauto.ocr import inch_values, read_preview_size, size_from_ocr

ARIAL = Path(r"C:\Windows\Fonts\arial.ttf")


class Parse(unittest.TestCase):
    def test_inch_values(self):
        self.assertEqual(inch_values(["22.500 inch"]), [22.5])
        self.assertEqual(inch_values(["65,237 inch", "DOLPHINS 97", "2025"]), [65.237])
        self.assertEqual(inch_values(["601.001inch"]), [601.001])

    def test_size_from_ocr(self):
        # What Windows OCR returned for Kevin's 3-sheet preview screenshot (11:2x).
        self.assertEqual(size_from_ocr(["22.500 inch", "THE DEARBORN ACADEMY"], ["65.237 inch"], 23),
                         (22.5, 65.237))

    def test_missing_or_extra_values_are_unreadable(self):
        self.assertIsNone(size_from_ocr([], ["65.237 inch"]))
        self.assertIsNone(size_from_ocr(["22.500 inch"], []))
        self.assertIsNone(size_from_ocr(["22.500 inch", "4 inch logo"], ["65.237 inch"]))
        self.assertIsNone(size_from_ocr(["22.500 inch"], ["65.237 inch", "12 inch"]))

    def test_implausible_width_is_unreadable(self):
        self.assertIsNone(size_from_ocr(["601.001 inch"], ["22.500 inch"], max_width_in=23))


@unittest.skipUnless(sys.platform == "win32" and ARIAL.exists() and shutil.which("powershell"),
                     "needs Windows OCR")
class WindowsOcr(unittest.TestCase):
    def test_reads_a_drawn_preview(self):
        """A stand-in for PO-TRY's preview: dark area, width label across the
        top, length label turned sideways down the left edge."""
        font = ImageFont.truetype(str(ARIAL), 15)
        img = Image.new("RGB", (360, 500), (45, 45, 45))
        d = ImageDraw.Draw(img)
        d.rectangle((95, 35, 255, 470), fill=(250, 250, 250))
        d.text((130, 12), "22.500 inch", font=font, fill=(255, 255, 255))
        label = Image.new("RGB", (120, 22), (45, 45, 45))
        ImageDraw.Draw(label).text((4, 2), "601.001 inch", font=font, fill=(255, 255, 255))
        img.paste(label.rotate(-90, expand=True), (60, 190))
        with tempfile.TemporaryDirectory() as tmp:
            size, seen = read_preview_size(img, tmp, "t", max_width_in=23)
        self.assertEqual(size, (22.5, 601.001), seen)


if __name__ == "__main__":
    unittest.main()
