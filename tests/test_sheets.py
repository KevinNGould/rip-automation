import io
import tempfile
import unittest
from pathlib import Path

from ripauto.sheets import (file_stem, png_header, png_size_in, print_ready_positions,
                            safe_name, sheet_size, size_slug, title_size_in)
from tests.helpers import line, png_bytes

PPM_300DPI = 11811   # what the #47918 file carries (299.9994 dpi)


class Naming(unittest.TestCase):
    def test_size_slug(self):
        self.assertEqual(size_slug("22in x 12in (1ft)"), "22in x 12in 1ft")
        self.assertEqual(size_slug("22in x 78in (6 1/2ft)"), "22in x 78in 6 1-2ft")
        self.assertEqual(size_slug(""), "")

    def test_spec_example(self):
        self.assertEqual(
            file_stem(47918, "Kaitlyn Carter", "18943728582971", "22in x 12in (1ft)", 1, 1),
            "47918-Kaitlyn Carter-18943728582971-22in x 12in 1ft-1-(1)")

    def test_quantity_goes_last_position_in_middle(self):
        # #47862 evidence: second gang sheet in the order; qty 3 is made up.
        self.assertEqual(
            file_stem(47862, "", "18900000000000", "22in x 600in (50ft)", 2, 3),
            "47862-18900000000000-22in x 600in 50ft-2-(3)")

    def test_blank_customer_is_left_out(self):
        self.assertEqual(
            file_stem(47918, "", "18943728582971", "22in x 12in (1ft)", 1, 1),
            "47918-18943728582971-22in x 12in 1ft-1-(1)")

    def test_windows_forbidden_characters_removed(self):
        self.assertEqual(safe_name('A<b>:c"d/e\\f|g?h*i.'), "Abcdefghi")
        self.assertEqual(
            file_stem(1, "Jo / Al: Co.", "9", "22in x 6in (1/2ft)", 1, 2),
            "1-Jo Al Co.-9-22in x 6in 1-2ft-1-(2)")


class Positions(unittest.TestCase):
    def test_counts_only_print_ready_items_in_shopify_order(self):
        items = [
            line(1, title="Shipping protection", attrs={}),
            line(2, title="DTF (4 Color CMYK) - Gang Sheet Builder"),
            line(3),
            line(4, title="DTF (4 Color CMYK) - Image by Size",
                 attrs={"_Print Ready": "https://example.test/x", "Uploaded File": "y"}),
            line(5),
        ]
        pos = print_ready_positions(items)
        self.assertEqual(pos, {
            "gid://shopify/LineItem/2": 1,
            "gid://shopify/LineItem/3": 2,
            "gid://shopify/LineItem/4": 3,
            "gid://shopify/LineItem/5": 4,
        })


class PngSize(unittest.TestCase):
    def test_47918_dimensions(self):
        # The real file: 6630 x 3077 px at 11811 px/m; PO-TRY showed W=22.100 H=10.257.
        w, h = png_size_in(io.BytesIO(png_bytes(6630, 3077, PPM_300DPI)))
        self.assertAlmostEqual(w, 22.100, places=2)
        self.assertAlmostEqual(h, 10.257, places=2)

    def test_header_fields(self):
        info = png_header(io.BytesIO(png_bytes(10, 20, 5000)))
        self.assertEqual(info, {"width_px": 10, "height_px": 20, "ppm_x": 5000, "ppm_y": 5000})

    def test_no_phys_gives_none(self):
        self.assertIsNone(png_size_in(io.BytesIO(png_bytes(6630, 3077))))

    def test_phys_without_metre_unit_gives_none(self):
        self.assertIsNone(png_size_in(io.BytesIO(png_bytes(6630, 3077, 1, unit=0))))

    def test_not_a_png(self):
        with self.assertRaises(ValueError):
            png_header(io.BytesIO(b"GIF89a....."))

    def test_reads_from_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.png"
            p.write_bytes(png_bytes(6600, 180000, PPM_300DPI))
            self.assertAlmostEqual(png_size_in(p)[1], 600.0, places=1)


class TitleSize(unittest.TestCase):
    def test_variants_seen_in_the_shop(self):
        self.assertEqual(title_size_in("22in x 12in (1ft)"), (22.0, 12.0))
        self.assertEqual(title_size_in("22in x 342in (28 1/2ft)"), (22.0, 342.0))
        self.assertEqual(title_size_in("22 in x 6in (1/2ft)"), (22.0, 6.0))
        self.assertIsNone(title_size_in("Default Title"))
        self.assertIsNone(title_size_in(None))


class SheetSize(unittest.TestCase):
    def test_png_wins(self):
        png = io.BytesIO(png_bytes(6630, 3077, PPM_300DPI))
        s = sheet_size(png, {"_Actual Height": "12"}, "22in x 12in (1ft)")
        self.assertEqual((s["length_source"], s["width_source"]), ("png", "png"))
        self.assertAlmostEqual(s["length_in"], 10.257, places=2)

    def test_actual_height_attribute_next(self):
        png = io.BytesIO(png_bytes(6630, 3077))       # no pHYs
        s = sheet_size(png, {"_Actual Height": "11.5 in"}, "22in x 12in (1ft)")
        self.assertEqual(s["length_in"], 11.5)
        self.assertEqual(s["length_source"], "attr")
        self.assertEqual((s["width_in"], s["width_source"]), (22.0, "title"))

    def test_title_last(self):
        s = sheet_size(Path("does-not-exist.png"), {}, "22in x 78in (6 1/2ft)")
        self.assertEqual((s["length_in"], s["length_source"]), (78.0, "title"))

    def test_nothing_known(self):
        s = sheet_size(None, {"_Actual Height": ""}, "Default Title")
        self.assertEqual(s, {"width_in": None, "length_in": None,
                             "width_source": None, "length_source": None})


if __name__ == "__main__":
    unittest.main()
