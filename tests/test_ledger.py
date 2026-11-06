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


class Requeue(unittest.TestCase):
    def test_only_held_sheets_of_those_orders(self):
        led = {"a": rec(47977, status=L.HELD, hold_reason="length gate", run="r1"),
               "b": rec(47979, status=L.HELD, hold_reason="length gate"),
               "c": rec(47980, status=L.HELD, hold_reason="other"),
               "d": rec(47977, status=L.RIPPED),
               "e": rec(47979, status=L.BY_HAND)}
        self.assertEqual(L.requeue(led, ["#47977", "47979"], NOW), ["a", "b"])
        self.assertEqual(led["a"]["status"], L.QUEUED)
        self.assertNotIn("hold_reason", led["a"])
        self.assertNotIn("run", led["a"])
        self.assertEqual(led["c"]["status"], L.HELD)
        self.assertEqual((led["d"]["status"], led["e"]["status"]), (L.RIPPED, L.BY_HAND))


class SaveWhileSomeoneReads(unittest.TestCase):
    def test_save_waits_for_a_reader_to_let_go(self):
        # 2026-11-06 00:40: the RIP Agent window had processed.json open; the
        # agent's save failed and 4 sheets ended up held.
        import threading
        import time as t
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "processed.json"
            L.save({"a": rec(1)}, path)
            reader = open(path, encoding="utf-8")                 # like the window, mid-read
            threading.Thread(target=lambda: (t.sleep(0.3), reader.close())).start()
            L.save({"a": rec(1), "b": rec(2)}, path)
            self.assertEqual(sorted(L.load(path)), ["a", "b"])


class PersonRequests(unittest.TestCase):
    """Done by hand / re-queue from the RIP Agent window or the command line."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger_requests.json"
        self.led = {"h1": rec(48942, status=L.HELD, hold_reason="x"),
                    "h2": rec(48943, status=L.HELD, hold_reason="x"),
                    "q1": rec(49400),
                    "r1": rec(48942, status=L.RIPPED),
                    "i1": rec(49401, status=L.IN_RUN)}

    def tearDown(self):
        self.tmp.cleanup()

    def test_by_hand_takes_held_and_queued_only(self):
        L.request("by_hand", ["h1", "q1", "r1", "i1", "gone"], path=self.path)
        logs = []
        self.assertEqual(L.apply_requests(self.led, NOW, logs.append, self.path), 2)
        self.assertEqual([L.status(self.led[k]) for k in ("h1", "q1", "r1", "i1")],
                         [L.BY_HAND, L.BY_HAND, L.RIPPED, L.IN_RUN])
        self.assertIn("marked done by hand", self.led["h1"]["by_hand"])
        self.assertEqual(logs, ["RIP Agent window: marked done by hand 2 sheet(s): #48942 #49400"])
        self.assertFalse(self.path.exists())                      # consumed
        self.assertEqual(L.apply_requests(self.led, NOW, logs.append, self.path), 0)

    def test_requeue_held_only_and_requests_stack(self):
        L.request("requeue", ["h1", "q1"], path=self.path)
        L.request("by_hand", ["h2"], who="command line", path=self.path)
        self.assertEqual(L.apply_requests(self.led, NOW, lambda line: None, self.path), 2)
        self.assertEqual((L.status(self.led["h1"]), L.status(self.led["q1"]), L.status(self.led["h2"])),
                         (L.QUEUED, L.QUEUED, L.BY_HAND))
        self.assertNotIn("hold_reason", self.led["h1"])

    def test_applying_twice_is_harmless(self):
        L.request("by_hand", ["h1"], path=self.path)
        L.request("by_hand", ["h1"], path=self.path)
        self.assertEqual(L.apply_requests(self.led, NOW, lambda line: None, self.path), 1)

    def test_orders_to_keys(self):
        self.assertEqual(sorted(L.keys_for_orders(self.led, ["#48942", "48943"], (L.HELD,))), ["h1", "h2"])
        self.assertEqual(L.keys_for_orders(self.led, ["48942"], (L.HELD, L.QUEUED)), ["h1"])

    def test_apply_now_with_the_agent_off(self):
        ledger_path = Path(self.tmp.name) / "processed.json"
        L.save(self.led, ledger_path)
        self.assertEqual(L.apply_now("by_hand", ["h1", "h2"], "command line", log=lambda line: None,
                                     path=self.path, ledger_path=ledger_path), 2)
        self.assertEqual(L.status(L.load(ledger_path)["h2"]), L.BY_HAND)

    def test_unknown_action_refused(self):
        with self.assertRaises(ValueError):
            L.request("delete", ["h1"], path=self.path)


if __name__ == "__main__":
    unittest.main()
