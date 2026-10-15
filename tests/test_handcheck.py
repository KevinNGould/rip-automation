import tempfile
import unittest
from pathlib import Path

from ripauto.handcheck import (BY_HAND, FREE, MANUAL, check_source, download_names,
                               hand_check, name_position, queue_job_names)

# Real job names from PO-TRY's queue on 2026-10-15 (hand-made jobs).
STEVE = "47862-Steve Springer-18942545264955-22in x 600in 50ft-2-New Gang Sheet-(1)"
TIFF = "47914-Tiffany Lechota-18943670452539-22in x 120in 10ft-1-6th grade and birds-(1)"
BETH = "47860-Beth Williams-18942525538619-22in x 78in 6 1-2ft-1-New Gang Sheet-(1)"


class NamePosition(unittest.TestCase):
    def test_app_names_from_the_queue(self):
        self.assertEqual(name_position(47862, STEVE), (True, 2))
        self.assertEqual(name_position(47914, TIFF), (True, 1))
        self.assertEqual(name_position(47860, BETH), (True, 1))     # "6 1-2ft" feet part

    def test_agent_names_and_odd_sizes(self):
        self.assertEqual(name_position(47874, "47874-18942850171195-22 in x 6in 1-2ft-1-(1).png"),
                         (True, 1))
        self.assertEqual(name_position(47918, "47918-18943728582971-22in x 12in 1ft-3-(2) (1).png"),
                         (True, 3))

    def test_other_orders_dont_match(self):
        self.assertEqual(name_position(4786, STEVE), (False, None))
        self.assertEqual(name_position(478620, STEVE), (False, None))
        self.assertEqual(name_position(47862, "invoice-47862.pdf"), (False, None))

    def test_order_match_without_readable_position(self):
        self.assertEqual(name_position(47862, "47862-Steve Springer-gang.png"), (True, None))

    def test_job_name_with_line_break(self):
        self.assertEqual(name_position(47862, STEVE[:30] + "\n" + STEVE[30:]), (True, 2))


class Outcomes(unittest.TestCase):
    def test_by_hand_in_queue(self):
        self.assertEqual(hand_check(47862, 2, [STEVE], [])[0], BY_HAND)

    def test_by_hand_in_downloads(self):
        v, detail = hand_check(47914, 1, [], [TIFF + ".png", "holiday.jpg"])
        self.assertEqual(v, BY_HAND)
        self.assertIn("Downloads", detail)

    def test_free_when_order_nowhere(self):
        self.assertEqual(hand_check(47935, 1, [STEVE, TIFF], [BETH + ".png"]), (FREE, ""))

    def test_other_sheet_of_same_order_is_manual(self):
        v, detail = hand_check(47862, 1, [STEVE], [])
        self.assertEqual(v, MANUAL)
        self.assertIn("different sheet", detail)

    def test_unreadable_position_is_manual(self):
        v, detail = hand_check(47862, 1, [], ["47862-gang sheet.png"])
        self.assertEqual(v, MANUAL)
        self.assertIn("unreadable", detail)

    def test_unreadable_queue_is_manual(self):
        v, detail = hand_check(47935, 1, None, [])
        self.assertEqual(v, MANUAL)
        self.assertIn("PO-TRY queue couldn't be read", detail)

    def test_unreadable_downloads_is_manual(self):
        self.assertEqual(hand_check(47935, 1, [], None)[0], MANUAL)

    def test_by_hand_beats_manual(self):
        # Queue unreadable, but Downloads clearly has this exact sheet.
        self.assertEqual(hand_check(47914, 1, None, [TIFF + ".png"])[0], BY_HAND)
        # Both sheets of an order downloaded: the exact match wins over the other one.
        self.assertEqual(check_source(47862, 2, [BETH, STEVE.replace("-2-", "-1-"), STEVE], "q")[0],
                         BY_HAND)


class Readers(unittest.TestCase):
    def test_download_names_top_level_files_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / (TIFF + ".png")).write_bytes(b"x")
            (root / "8color-queue").mkdir()
            (root / "8color-queue" / (STEVE + ".png")).write_bytes(b"x")
            self.assertEqual(download_names(root), [TIFF + ".png"])
            self.assertIsNone(download_names(root / "missing"))

    def test_queue_job_names_drop_own_jobs(self):
        rows = [{"Job name": STEVE}, {"Job name": "47918-1-22in x 12in 1ft-1-(1)"}, {"Status": "x"}]
        self.assertEqual(queue_job_names(rows, own_job_names=["47918-1-22in x 12in 1ft-1-(1)"]),
                         [STEVE])
        self.assertIsNone(queue_job_names(None))


if __name__ == "__main__":
    unittest.main()
