import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from ripauto import ledger as L
from ripauto.agent import Stop, cycle, own_job_names

NOW = datetime(2026, 10, 15, 12, 0, 0)
CFG = {"machine_id": "8color-1", "cap_in": 650, "target_in": 600, "window_in": 48,
       "gap_in": 0.5, "flush_after_min": 30, "max_width_in": 23}


class FakeDriver:
    def __init__(self, rows=(), gate=None, fail=None, size=None, row_size="22.500×600.000"):
        self.rows, self.gate, self.fail, self.size = list(rows), gate, fail, size
        self.row_size = row_size
        self.calls = []

    def queue_rows(self):
        return self.rows

    def rip(self, pictures, planned_in, dry_run, on_progress=None):
        self.calls.append(([p.name for p in pictures], planned_in, dry_run))
        if on_progress and not dry_run:
            for pct in (41, 87, 100):
                on_progress(pct)
        if self.fail:
            raise Stop(self.fail)
        return {"job_name": pictures[0].stem, "preview": self.size or (22.5, planned_in),
                "gate": self.gate, "row": None if dry_run else {"Size (inch)": self.row_size},
                "started": "s", "ended": "e"}


class RecordingReporter:
    def __init__(self):
        self.calls = []

    def update(self, **kw):
        self.calls.append(kw)

    def states(self):
        out = []
        for c in self.calls:
            if c.get("state") and (not out or out[-1] != c["state"]):
                out.append(c["state"])
        return out


class Env:
    """A temp queue folder, runs folder and ledger."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.queue = root / "queue"
        self.runs = root / "runs"
        self.queue.mkdir()
        self.cfg = dict(CFG, queue_dir=str(self.queue))
        self.ledger = {}
        self.downloads = []

    def add(self, key, order_num, length, qty=1, position=1, waited_min=0):
        name = f"{order_num}-1-22in x 12in 1ft-{position}-({qty}).png"
        (self.queue / name).write_bytes(b"png")
        fetched = NOW.replace(minute=0) if waited_min else NOW
        if waited_min:
            fetched = datetime.fromtimestamp(NOW.timestamp() - waited_min * 60)
        self.ledger[key] = {"order": f"#{order_num}", "order_num": order_num, "line_id": key,
                            "position": position, "qty": qty, "length_in": length,
                            "width_in": 22.1, "low_res": False, "file": str(self.queue / name),
                            "fetched_at": fetched.isoformat(timespec="seconds"),
                            "status": L.QUEUED, "copies_ripped": 0}
        return name

    def run(self, driver, **kw):
        saves = []
        summary = cycle(self.cfg, self.ledger, driver, NOW,
                        dl_names_fn=lambda: self.downloads, runs_dir=self.runs,
                        save=lambda: saves.append(1), log=lambda *_: None, **kw)
        return summary, saves

    def close(self):
        self.tmp.cleanup()


class Cycle(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_full_run_is_ripped_moved_and_recorded(self):
        a = self.env.add("a", 47930, 300.0)
        b = self.env.add("b", 47931, 290.0, qty=1)
        d = FakeDriver()
        summary, saves = self.env.run(d)
        self.assertEqual(len(summary["ripped"]), 1)
        self.assertEqual(d.calls[0][0], [a, b])
        self.assertFalse(d.calls[0][2])
        for k, name in (("a", a), ("b", b)):
            self.assertEqual(L.status(self.env.ledger[k]), L.RIPPED)
            self.assertTrue(Path(self.env.ledger[k]["file"]).is_file())
            self.assertIn("ripped", self.env.ledger[k]["file"])
        self.assertFalse((self.env.queue / a).exists())
        [rec] = [json.loads(p.read_text()) for p in self.env.runs.glob("*.json")]
        self.assertEqual((rec["outcome"], rec["orders"], rec["queue_size"]),
                         ("ripped", ["#47930", "#47931"], "22.500×600.000"))
        self.assertEqual(own_job_names(self.env.runs), [Path(a).stem])
        self.assertTrue(saves)

    def test_partial_run_waits_until_flush(self):
        self.env.add("a", 47930, 100.0)
        d = FakeDriver()
        summary, _ = self.env.run(d)
        self.assertEqual((d.calls, summary["ripped"]), ([], []))
        self.env.ledger["a"]["fetched_at"] = "2026-10-15T11:00:00"
        summary, _ = self.env.run(d)
        self.assertEqual(len(summary["ripped"]), 1)

    def test_force_takes_first_run(self):
        self.env.add("a", 47930, 100.0)
        d = FakeDriver()
        self.env.run(d, force=True, dry_run=True)
        self.assertEqual(len(d.calls), 1)

    def test_dry_run_leaves_ledger_and_files_alone(self):
        a = self.env.add("a", 47930, 600.0)
        before = json.dumps(self.env.ledger, sort_keys=True)
        d = FakeDriver()
        summary, saves = self.env.run(d, dry_run=True)
        self.assertTrue(d.calls[0][2])
        self.assertEqual(json.dumps(self.env.ledger, sort_keys=True), before)
        self.assertTrue((self.env.queue / a).exists())
        self.assertEqual(saves, [])
        self.assertEqual(len(summary["dry_runs"]), 1)
        self.assertEqual(own_job_names(self.env.runs), [])     # dry runs aren't "own" jobs

    def test_gate_refusal_holds_whole_run(self):
        self.env.add("a", 47930, 300.0)
        self.env.add("b", 47931, 300.0)
        summary, _ = self.env.run(FakeDriver(gate="preview length unreadable"))
        self.assertEqual(summary["ripped"], [])
        for k in ("a", "b"):
            self.assertEqual(L.status(self.env.ledger[k]), L.HELD)
            self.assertIn("length gate", self.env.ledger[k]["hold_reason"])
        self.assertEqual(summary["stopped"], "preview length unreadable")

    def test_driver_stop_holds_run_for_manual_check(self):
        self.env.add("a", 47930, 600.0)
        summary, _ = self.env.run(FakeDriver(fail="the job never appeared in the RIP queue"))
        self.assertEqual(L.status(self.env.ledger["a"]), L.HELD)
        self.assertIn("manual check", self.env.ledger["a"]["hold_reason"])
        self.assertIsNotNone(summary["stopped"])

    def test_interrupted_run_is_held_next_cycle(self):
        self.env.add("a", 47930, 600.0)
        self.env.ledger["a"]["status"] = L.IN_RUN
        d = FakeDriver()
        self.env.run(d)
        self.assertEqual(L.status(self.env.ledger["a"]), L.HELD)
        self.assertEqual(d.calls, [])

    def test_hand_download_skips_sheet(self):
        self.env.add("a", 47930, 600.0)
        self.env.downloads = ["47930-Jo-1-22in x 12in 1ft-1-Design-(1).png"]
        d = FakeDriver()
        self.env.run(d)
        self.assertEqual(L.status(self.env.ledger["a"]), L.BY_HAND)
        self.assertEqual(d.calls, [])

    def test_hand_check_in_queue_and_manual(self):
        self.env.add("a", 47930, 600.0)
        self.env.add("b", 47940, 600.0, position=2)
        rows = [{"Job name": "47930-Jo-1-22in x 12in 1ft-1-Design-(1)"},
                {"Job name": "47940-Al-2-22in x 12in 1ft-1-Design-(1)"}]   # other sheet of #47940
        self.env.run(FakeDriver(rows=rows))
        self.assertEqual(L.status(self.env.ledger["a"]), L.BY_HAND)
        self.assertEqual(L.status(self.env.ledger["b"]), L.HELD)

    def test_unreadable_queue_holds_everything_for_manual_check(self):
        self.env.add("a", 47930, 600.0)
        d = FakeDriver()
        d.rows = None
        self.env.run(d)
        self.assertEqual(L.status(self.env.ledger["a"]), L.HELD)

    def test_own_previous_job_is_not_mistaken_for_hand_work(self):
        a = self.env.add("a", 47930, 400.0, qty=2)      # 2 x 400: copies split
        d = FakeDriver()
        self.env.run(d)                                  # first copy alone isn't full...
        self.assertEqual(d.calls, [])
        self.env.ledger["a"]["fetched_at"] = "2026-10-15T11:00:00"
        self.env.run(d)                                  # ...flush: copy 1 ripped
        self.assertEqual(L.status(self.env.ledger["a"]), L.QUEUED)
        self.assertEqual(self.env.ledger["a"]["copies_ripped"], 1)
        self.assertTrue((self.env.queue / a).exists())   # file stays for copy 2
        d.rows = [{"Job name": Path(a).stem}]            # our own job now in PO-TRY's queue
        self.env.run(d)
        self.assertEqual(L.status(self.env.ledger["a"]), L.RIPPED)
        self.assertFalse((self.env.queue / a).exists())

    def test_planner_holds_reach_the_ledger(self):
        self.env.add("a", 47930, 700.0)
        self.env.run(FakeDriver())
        self.assertEqual(self.env.ledger["a"]["hold_reason"], "over 650 in")

    def test_missing_file_holds_run(self):
        self.env.add("a", 47930, 600.0)
        Path(self.env.ledger["a"]["file"]).unlink()
        summary, _ = self.env.run(FakeDriver())
        self.assertEqual(L.status(self.env.ledger["a"]), L.HELD)
        self.assertIn("missing", summary["stopped"])

    def test_ripped_job_over_cap_is_flagged_for_deletion(self):
        self.env.add("a", 47930, 600.0)
        summary, _ = self.env.run(FakeDriver(row_size="22.500×651.200"))
        self.assertEqual(summary["ripped"], [])
        self.assertEqual(L.status(self.env.ledger["a"]), L.HELD)
        self.assertIn("delete it from the RIP queue by hand", self.env.ledger["a"]["hold_reason"])
        self.assertEqual(self.env.ledger["a"]["copies_ripped"], 1)     # it was ripped
        [rec] = [json.loads(p.read_text()) for p in self.env.runs.glob("*.json")]
        self.assertEqual(rec["outcome"], "over_cap")

    def test_run_record_measures_gap(self):
        # Kevin's 3-sheet dry run: 45.74 + 7.2 + 10.257 in of artwork, preview 65.237 in.
        self.env.add("a", 47862, 45.74)
        self.env.add("b", 47874, 7.2)
        self.env.add("c", 47918, 10.257, waited_min=40)
        self.env.run(FakeDriver(row_size="22.500×65.237"))
        [rec] = [json.loads(p.read_text()) for p in self.env.runs.glob("*.json")]
        self.assertEqual(rec["actual_in"], 65.237)
        self.assertAlmostEqual(rec["gap_measured_in"], 1.02, places=2)

    def test_reports_states_to_the_dashboard(self):
        self.env.add("a", 47930, 600.0)
        rep = RecordingReporter()
        self.env.run(FakeDriver(), reporter=rep)
        self.assertEqual(rep.states(), ["planning", "ripping", "idle"])
        ripping = [u for u in rep.calls if u.get("state") == "ripping"][0]
        self.assertEqual(ripping["current_run"]["orders"], ["#47930"])
        self.assertEqual([u["progress_pct"] for u in rep.calls if u.get("progress_pct")], [41, 87, 100])

    def test_reports_holding_and_error(self):
        self.env.add("a", 47930, 600.0)
        rep = RecordingReporter()
        self.env.run(FakeDriver(gate="preview length unreadable"), reporter=rep)
        self.assertEqual(rep.states()[-1], "holding")
        self.assertEqual(rep.calls[-1]["last_error"], "preview length unreadable")
        self.env.add("b", 47931, 600.0)
        rep = RecordingReporter()
        self.env.run(FakeDriver(fail="the job never appeared in the RIP queue"), reporter=rep)
        self.assertEqual(rep.states()[-1], "error")

    def test_two_runs_in_one_cycle_get_distinct_ids(self):
        self.env.add("a", 47930, 600.0)
        self.env.add("b", 47931, 600.0)
        summary, _ = self.env.run(FakeDriver(), max_runs=2)
        self.assertEqual(len(set(summary["ripped"])), 2)


if __name__ == "__main__":
    unittest.main()
