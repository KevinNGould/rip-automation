import tempfile
import unittest
from pathlib import Path

from ripauto.potry import (batch_pictures, copies_from_name, find_row, gate_reason,
                           job_name_for, parse_size_cell, rip_state)
from tests.helpers import png_bytes


class RipState(unittest.TestCase):
    def test_statuses_seen_so_far(self):
        self.assertEqual(rip_state("Ripping", "39"), "working")
        self.assertEqual(rip_state("Ripping", "100"), "working")
        self.assertEqual(rip_state("RIP finished", "100"), "done")
        self.assertEqual(rip_state("Transmission finished", "100"), "done")

    def test_later_unknown_status_after_full_rip_is_done(self):
        self.assertEqual(rip_state("Transmitting", "100"), "done")
        self.assertEqual(rip_state("Transmitting", "40"), "working")

    def test_failures(self):
        self.assertEqual(rip_state("RIP failed", "12"), "failed")
        self.assertEqual(rip_state("Error", ""), "failed")

    def test_empty(self):
        self.assertEqual(rip_state("", ""), "working")


class Sizes(unittest.TestCase):
    def test_queue_size_cell(self):
        self.assertEqual(parse_size_cell("22.500×601.001"), (22.5, 601.001))
        self.assertEqual(parse_size_cell("22.500x121.200"), (22.5, 121.2))
        self.assertIsNone(parse_size_cell(""))


class Gate(unittest.TestCase):
    def test_ok(self):
        self.assertIsNone(gate_reason((22.5, 601.0), 650, 588.0))
        self.assertIsNone(gate_reason((22.5, 650.0), 650, 650.0))

    def test_unreadable(self):
        self.assertEqual(gate_reason(None, 650, 600), "preview length unreadable")

    def test_over_cap(self):
        self.assertIn("over 650 in", gate_reason((22.5, 650.2), 650, 600))

    def test_much_shorter_than_planned(self):
        self.assertIn("may not have loaded", gate_reason((22.5, 300.0), 650, 600.0))
        self.assertIsNone(gate_reason((22.5, 590.0), 650, 600.0))   # within 30 in (5%)
        self.assertIsNone(gate_reason((22.5, 1.0), 650, 10.0))      # within 12 in


class Naming(unittest.TestCase):
    def test_job_name_is_first_picture_stem(self):
        self.assertEqual(job_name_for([Path("C:/q/47918-1-22in x 12in 1ft-1-(2).png"),
                                       Path("C:/q/x.png")]),
                         "47918-1-22in x 12in 1ft-1-(2)")

    def test_find_row_by_job_name_and_time(self):
        rows = [{"Job name": "47918-a-(1)", "Establishing time": "2026-10-15 09:00:00"},
                {"Job name": "47918-a-(1)", "Establishing time": "2026-10-15 12:00:05"},
                {"Job name": "47920-b-(1)", "Establishing time": "2026-10-15 12:00:06"}]
        self.assertEqual(find_row(rows, "47918-a-(1)", "2026-10-15 12:00:00"), rows[1])
        self.assertIsNone(find_row(rows, "47918-a-(1)", "2026-10-15 13:00:00"))

    def test_copies_from_name(self):
        self.assertEqual(copies_from_name(Path("47918-1-22in x 12in 1ft-1-(3).png")), 3)
        self.assertEqual(copies_from_name(Path("47918-1-22in x 12in 1ft-1-(3) [2].png")), 3)
        self.assertEqual(copies_from_name(Path("plain.png")), 1)


class Batch(unittest.TestCase):
    def test_batch_expands_copies_and_plans_length(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "1-x-22in x 12in 1ft-1-(2).png"
            b = Path(d) / "2-x-22in x 24in 2ft-1-(1).png"
            a.write_bytes(png_bytes(6630, 3000, 11811))     # 10.0 in
            b.write_bytes(png_bytes(6630, 6000, 11811))     # 20.0 in
            pictures, planned = batch_pictures([a, b], gap_in=0.5)
        self.assertEqual([p.name for p in pictures], [a.name, a.name, b.name])
        self.assertAlmostEqual(planned, 10.0 * 2 + 20.0 + 0.5 * 2, places=2)


if __name__ == "__main__":
    unittest.main()
