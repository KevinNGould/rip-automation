import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from ripauto import ledger as L
from ripauto.planner import RunItem, Sheet

NOW = datetime(2026, 10, 15, 12, 0, 0)


def rec(order_num, qty=1, status=L.QUEUED, **extra):
    r = {"order": f"#{order_num}", "order_num": order_num, "position": 1, "qty": qty,
         "length_in": 100.0, "width_in": 22.1, "low_res": False,
         "fetched_at": "2026-10-15T11:00:00", "status": status, "copies_ripped": 0}
    r.update(extra)
    return r


def item(ledger, key, copies):
    return RunItem(Sheet.from_ledger(key, ledger[key]), copies)


class Queue(unittest.TestCase):
    def test_only_queued_with_copies_left(self):
        led = {"a": rec(1), "b": rec(2, status=L.RIPPED), "c": rec(3, status=L.HELD),
               "d": rec(4, status=L.BY_HAND), "e": rec(5, qty=3, copies_ripped=1),
               "trial": {"order": "#47918", "file": "x.png", "downloaded_at": "..."},
               "marked": {"order": "#1", "marked_done": "..."}}
        got = {s.key: s.qty for s in L.queued_sheets(led)}
        self.assertEqual(got, {"a": 1, "e": 2})

    def test_untracked_entries(self):
        self.assertEqual(L.status({"order": "#1", "marked_done": "x"}), "untracked")


class Runs(unittest.TestCase):
    def test_full_rip(self):
        led = {"a": rec(1, qty=2)}
        items = [item(led, "a", 2)]
        L.start_run(led, "r1", items, NOW)
        self.assertEqual((L.status(led["a"]), led["a"]["run"]), (L.IN_RUN, "r1"))
        self.assertEqual(L.in_run_keys(led), ["a"])
        L.finish_run(led, "r1", items, NOW)
        self.assertEqual(L.status(led["a"]), L.RIPPED)
        self.assertEqual((led["a"]["copies_ripped"], led["a"]["runs"]), (2, ["r1"]))
        self.assertNotIn("run", led["a"])
        self.assertEqual(L.queued_sheets(led), [])

    def test_split_copies_go_back_to_queue_until_done(self):
        led = {"a": rec(1, qty=3)}
        L.start_run(led, "r1", [item(led, "a", 2)], NOW)
        L.finish_run(led, "r1", [item(led, "a", 2)], NOW)
        self.assertEqual(L.status(led["a"]), L.QUEUED)
        [s] = L.queued_sheets(led)
        self.assertEqual(s.qty, 1)
        L.start_run(led, "r2", [item(led, "a", 1)], NOW)
        L.finish_run(led, "r2", [item(led, "a", 1)], NOW)
        self.assertEqual(L.status(led["a"]), L.RIPPED)
        self.assertEqual(led["a"]["runs"], ["r1", "r2"])

    def test_unstart_run_puts_sheets_back(self):
        led = {"a": rec(1, qty=2)}
        L.start_run(led, "r1", [item(led, "a", 2)], NOW)
        L.unstart_run(led, ["a"], NOW)
        self.assertEqual(L.status(led["a"]), L.QUEUED)
        self.assertNotIn("run", led["a"])
        self.assertEqual(led["a"].get("copies_ripped"), 0)
        self.assertEqual(L.in_run_keys(led), [])

    def test_hold_and_by_hand(self):
        led = {"a": rec(1), "b": rec(2)}
        L.hold(led, ["a"], "over 650 in", NOW)
        L.mark_by_hand(led, "b", "Downloads: 2-x-22in x 12in 1ft-1-y-(1).png", NOW)
        self.assertEqual((L.status(led["a"]), led["a"]["hold_reason"]), (L.HELD, "over 650 in"))
        self.assertEqual(L.status(led["b"]), L.BY_HAND)
        self.assertEqual(L.queued_sheets(led), [])

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state" / "processed.json"
            L.save({"a": rec(1)}, p)
            self.assertEqual(L.load(p)["a"]["order"], "#1")
            self.assertEqual(L.load(Path(d) / "missing.json"), {})


if __name__ == "__main__":
    unittest.main()
