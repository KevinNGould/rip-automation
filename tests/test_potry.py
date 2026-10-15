import tempfile
import unittest
from pathlib import Path

from ripauto.potry import (batch_pictures, copies_from_name, find_row, gate_reason,
                           job_name_for, multi_add_chunks, parse_size_cell, rip_state)
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
        # Kevin's 3-sheet dry runs, 2026-10-15 11:16 and 11:17.
        self.assertIsNone(gate_reason((22.5, 65.237), 650, 65.197))
        self.assertIsNone(gate_reason((22.5, 650.0), 650, 648.0))

    def test_unreadable(self):
        self.assertEqual(gate_reason(None, 650, 600), "preview length unreadable")

    def test_over_cap(self):
        self.assertIn("over 650 in", gate_reason((22.5, 650.2), 650, 648))

    def test_missing_picture(self):
        # The 6 in sheet (7.2 in + 1 in gap) missing from the 3-sheet run.
        self.assertIn("may not have loaded", gate_reason((22.5, 57.0), 650, 65.197))
        # 1% of 600 = 6 in tolerance on a long run.
        self.assertIsNone(gate_reason((22.5, 595.0), 650, 600.0))
        self.assertIn("may not have loaded", gate_reason((22.5, 591.0), 650, 600.0))

    def test_gaps_may_come_out_smaller_than_planned(self):
        # 2026-10-15 13:00: 3 pictures, 195.0 in of artwork, planned 197.0 with
        # 1 in gaps; the preview showed 195.037 (gaps ~0).
        self.assertIsNone(gate_reason((22.5, 195.037), 650, 197.0, pictures=3, gap_in=1.0))
        # A long run of 20 pictures with ~0 gaps is 19 in short of planned: still fine.
        self.assertIsNone(gate_reason((22.5, 581.0), 650, 600.0, pictures=20, gap_in=1.0))
        # ...but shorter than the artwork alone by more than the tolerance is not.
        self.assertIn("may not have loaded", gate_reason((22.5, 570.0), 650, 600.0, pictures=20, gap_in=1.0))

    def test_doubled_picture(self):
        self.assertIn("loaded twice", gate_reason((22.5, 73.5), 650, 65.197))
        self.assertIsNone(gate_reason((22.5, 67.5), 650, 65.197))   # within 3 in


class AgentTabs(unittest.TestCase):
    def setUp(self):
        import ripauto.potry as p
        self.p = p
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (p.AGENT_TABS_PATH, p.TAB_CLOSE_PATH)
        p.AGENT_TABS_PATH = Path(self.tmp.name) / "agent_tabs.json"
        p.TAB_CLOSE_PATH = Path(self.tmp.name) / "potry_tab_close.json"

    def tearDown(self):
        self.p.AGENT_TABS_PATH, self.p.TAB_CLOSE_PATH = self.saved
        self.tmp.cleanup()

    def test_remember_and_forget_only_our_tabs(self):
        p = self.p
        p.remember_tab(1234, "Job13")
        p.remember_tab(1234, "Job14")
        p.remember_tab(1234, "Job14")
        p.remember_tab(1234, None)
        self.assertEqual(p.agent_tabs(1234), ["Job13", "Job14"])
        p.forget_tab(1234, "Job13")
        self.assertEqual(p.agent_tabs(1234), ["Job14"])

    def test_restarted_po_try_starts_the_list_over(self):
        # A new PO-TRY process reuses names like Job3, which may be a person's tab.
        self.p.remember_tab(1234, "Job3")
        self.assertEqual(self.p.agent_tabs(5678), [])

    def test_finished_run_closes_its_tab_then_back_to_queue_view(self):
        p, calls = self.p, []
        saved = (p.show_layout, p.show_queue, p.close_agent_tabs)
        p.show_layout = lambda main: calls.append("layout")
        p.show_queue = lambda main: calls.append("queue")
        p.close_agent_tabs = lambda main, pid, keep=(): calls.append("close")
        try:
            p.remember_tab(1234, "Job30")
            p.close_finished_tabs(main=None, pid=1234)      # no proven method: untouched
            self.assertEqual(calls, [])
            p.TAB_CLOSE_PATH.write_text('{"method": "x_button", "x_offset": 12}')
            p.close_finished_tabs(main=None, pid=1234)
            self.assertEqual(calls, ["layout", "close", "queue"])
        finally:
            p.show_layout, p.show_queue, p.close_agent_tabs = saved

    def test_nothing_closed_until_a_method_is_proven(self):
        self.p.remember_tab(1234, "Job13")
        self.assertIsNone(self.p.tab_close_method())
        self.assertEqual(self.p.close_agent_tabs(main=None, pid=1234), 0)   # never touches PO-TRY
        self.p.TAB_CLOSE_PATH.write_text('{"method": "context_menu"}')
        self.assertEqual(self.p.tab_close_method(), "context_menu")


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

    def test_find_row_by_any_picture_name(self):
        rows = [{"Job name": "47920-b-(1)", "Establishing time": "2026-10-15 12:00:06"}]
        self.assertEqual(find_row(rows, ["47918-a-(1)", "47920-b-(1)"], "2026-10-15 12:00:00"),
                         rows[0])

    def test_multi_add_chunks(self):
        files = [Path(f"{n}.png") for n in range(23)]
        self.assertEqual([len(c) for c in multi_add_chunks(files)], [10, 10, 3])
        self.assertEqual(multi_add_chunks(files[:1]), [])
        self.assertEqual([len(c) for c in multi_add_chunks(files[:11])], [10])  # 1 left: single Add

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
