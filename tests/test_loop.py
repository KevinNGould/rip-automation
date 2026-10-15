import json
import os
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

from ripauto import ledger as L
from ripauto.agent import run_loop
from ripauto.warning import Warner
from ripauto.lock import AgentLock, agent_running, lock_holder
from ripauto.potry import rotate_log
from tests.test_agent import CFG, FakeDriver, RecordingReporter

NOW = datetime(2026, 10, 15, 12, 0, 0)


class FakeTime:
    """Clock + sleep: each sleep moves the clock; stops the loop after max_sleeps."""

    def __init__(self, stop, max_sleeps, on_sleep=None):
        self.t, self.sleeps, self.stop, self.max = 1000.0, 0, stop, max_sleeps
        self.on_sleep = on_sleep

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s
        self.sleeps += 1
        if self.on_sleep:
            self.on_sleep(self)
        if self.sleeps >= self.max:
            self.stop.set()


class Loop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.queue, self.runs = root / "queue", root / "runs"
        self.queue.mkdir()
        self.pause = root / "PAUSE"
        self.rip_now = root / "RIP_NOW"
        self.pending, self.response = root / "rip_pending.json", root / "rip_response.json"
        self.cfg = dict(CFG, queue_dir=str(self.queue), fetch_every_min=2, warn_before_rip_s=60,
                        still_before_rip_s=5, snooze_min=5)
        self.ledger = {}
        self.fetches = 0
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, key, order_num, length):
        name = f"{order_num}-1-22in x 12in 1ft-1-(1).png"
        (self.queue / name).write_bytes(b"png")
        self.ledger[key] = {"order": f"#{order_num}", "order_num": order_num, "line_id": key,
                            "position": 1, "qty": 1, "length_in": length, "width_in": 22.1,
                            "low_res": False, "file": str(self.queue / name),
                            "fetched_at": NOW.isoformat(timespec="seconds"),
                            "status": L.QUEUED, "copies_ripped": 0}

    def fetcher(self, new=None):
        def f(ledger):
            self.fetches += 1
            if new and self.fetches == 1:
                self.add(*new)
                return [self.ledger[new[0]]]
            return []
        return f

    def loop(self, driver, max_sleeps, fetcher=None, idle=lambda: 600, on_sleep=None):
        stop = threading.Event()
        ft = FakeTime(stop, max_sleeps, on_sleep)
        rep = RecordingReporter()
        run_loop(self.cfg, self.ledger, driver, fetcher=fetcher or self.fetcher(),
                 dl_names_fn=lambda: [], reporter=rep, save=lambda: None, log=self.logs.append,
                 pause_path=self.pause, idle_s=idle, clock=ft.clock, now_fn=lambda: NOW,
                 sleep=ft.sleep, stop=stop, runs_dir=self.runs,
                 warner=Warner(launch=False, pending=self.pending, response=self.response),
                 rip_now_path=self.rip_now, restart_path=Path(self.tmp.name) / "RESTART")
        return rep, ft

    def press(self, action):
        """What the popup's buttons do."""
        self.response.write_text(json.dumps({"action": action}))

    def test_fetches_on_start_then_every_two_minutes(self):
        self.loop(FakeDriver(), max_sleeps=13)      # 13 x 20 s = 260 s
        self.assertEqual(self.fetches, 3)            # t=0, 120, 240

    def test_fetched_full_run_gets_ripped_after_the_warning(self):
        d = FakeDriver()
        seen = []

        def watch(ft):
            if self.pending.exists():
                p = json.loads(self.pending.read_text())
                seen.append((round(p["deadline"] - ft.t), p["planned_in"], p["orders"]))
        rep, ft = self.loop(d, max_sleeps=40, fetcher=self.fetcher(new=("a", 47950, 600.0)),
                            on_sleep=watch)
        self.assertEqual(len(d.calls), 1)
        self.assertEqual(L.status(self.ledger["a"]), L.RIPPED)
        self.assertIn("ripping", rep.states())
        self.assertEqual(seen[0], (58, 600.0, ["#47950"]))      # countdown from 60 s, shown at once
        self.assertTrue(any("Rip warning shown" in line for line in self.logs))
        self.assertFalse(self.pending.exists())                   # popup closed once it started

    def test_at_zero_it_waits_for_a_still_mouse(self):
        self.add("a", 47950, 600.0)
        d = FakeDriver()
        messages = []
        self.loop(d, max_sleeps=45, idle=lambda: 1,                # someone keeps using the PC
                  on_sleep=lambda ft: messages.append(json.loads(self.pending.read_text())["message"])
                  if self.pending.exists() else None)
        self.assertEqual(d.calls, [])
        self.assertEqual(messages[0], None)                        # counting down first
        self.assertEqual(messages[-1], "Starting as soon as the mouse and keyboard are still")
        self.assertFalse(self.pending.exists())                    # loop stopped: popup closed
        self.assertEqual(L.status(self.ledger["a"]), L.QUEUED)

    def test_busy_po_try_keeps_the_popup_and_skips_a_new_countdown(self):
        self.add("a", 47950, 600.0)
        d = FakeDriver(rows=[{"Job name": "47000-x-(1)", "Status": "Ripping", "Processing": "40"}])
        msgs, deadlines = [], set()

        def watch(ft):
            if self.pending.exists():
                p = json.loads(self.pending.read_text())
                msgs.append(p["message"])
                deadlines.add(p["deadline"])
            if ft.sleeps == 40:                              # the hand job finishes
                d.rows = [{"Job name": "47000-x-(1)", "Status": "RIP finished", "Processing": "100"}]
        self.loop(d, max_sleeps=44, on_sleep=watch)
        self.assertIn("Waiting for PO-TRY to finish ripping another job", msgs)
        self.assertEqual(len(d.calls), 1)                    # ripped once PO-TRY was free...
        self.assertEqual(len(deadlines), 1)                  # ...with no second 60 s countdown

    def test_unreadable_queue_errors_after_three_and_recovers(self):
        self.add("a", 47950, 600.0)
        d = FakeDriver()
        d.rows = None

        def recover(ft):
            if ft.sleeps == 45:
                d.rows = []
        rep, _ = self.loop(d, max_sleeps=50, on_sleep=recover)
        errors = [c for c in rep.calls if c.get("state") == "error"]
        self.assertEqual(len(errors), 1)                           # flagged once, after 3 misses
        self.assertIn("can't read PO-TRY's RIP queue", errors[0]["last_error"])
        self.assertIn("PO-TRY's queue is readable again.", self.logs)
        self.assertEqual(len(d.calls), 1)                           # then ripped normally
        self.assertEqual(L.status(self.ledger["a"]), L.RIPPED)

    def test_wait_5_min(self):
        self.add("a", 47950, 600.0)
        d = FakeDriver()
        self.loop(d, max_sleeps=14, on_sleep=lambda ft: self.press("snooze") if ft.sleeps == 2 else None)
        self.assertEqual(d.calls, [])                              # 14 sleeps < 5 min
        self.assertIn("Rip warning: waiting 5 min (snoozed).", self.logs)
        self.assertFalse(self.pending.exists())

    def test_popup_rip_now_skips_the_wait(self):
        self.add("a", 47950, 600.0)
        d = FakeDriver()
        self.loop(d, max_sleeps=6, idle=lambda: 0,                 # button click = mouse just moved
                  on_sleep=lambda ft: self.press("now") if ft.sleeps == 1 else None)
        self.assertEqual(len(d.calls), 1)

    def test_popup_pause(self):
        self.add("a", 47950, 600.0)
        d = FakeDriver()
        rep, _ = self.loop(d, max_sleeps=5, on_sleep=lambda ft: self.press("pause") if ft.sleeps == 1 else None)
        self.assertEqual(d.calls, [])
        self.assertTrue(self.pause.exists())
        self.assertIn("stopped", rep.states())

    def test_rip_now_flag_rips_a_partial_run(self):
        self.add("a", 47950, 100.0)                                # not full, not due
        self.rip_now.write_text("")
        d = FakeDriver()
        seen = []
        rep, _ = self.loop(d, max_sleeps=10, on_sleep=lambda ft: seen.append(
            json.loads(self.pending.read_text())["rip_now"]) if self.pending.exists() else None)
        self.assertEqual(len(d.calls), 1)
        self.assertTrue(seen and all(seen))                        # popup said "Rip now in …"
        self.assertFalse(self.rip_now.exists())

    def test_restart_request_exits_at_a_safe_point(self):
        restart = Path(self.tmp.name) / "RESTART"
        restart.write_text("")
        stop = threading.Event()
        ft = FakeTime(stop, 5)
        result = run_loop(self.cfg, self.ledger, FakeDriver(), fetcher=self.fetcher(),
                          dl_names_fn=lambda: [], reporter=RecordingReporter(), save=lambda: None,
                          log=self.logs.append, pause_path=self.pause, idle_s=lambda: 600,
                          clock=ft.clock, now_fn=lambda: NOW, sleep=ft.sleep, stop=stop,
                          runs_dir=self.runs, warner=Warner(launch=False, pending=self.pending,
                                                            response=self.response),
                          rip_now_path=self.rip_now, restart_path=restart)
        self.assertEqual(result, "restart")
        self.assertFalse(restart.exists())
        self.assertEqual(self.fetches, 0)

    def test_rip_now_with_nothing_waiting(self):
        self.rip_now.write_text("")
        self.loop(FakeDriver(), max_sleeps=2)
        self.assertIn("Rip now: nothing is waiting to rip.", self.logs)
        self.assertFalse(self.rip_now.exists())

    def test_pause_switch(self):
        self.add("a", 47950, 600.0)
        self.pause.write_text("")
        d = FakeDriver()

        def resume_after_three(ft):
            if ft.sleeps == 3:
                self.pause.unlink()
        rep, _ = self.loop(d, max_sleeps=40, on_sleep=resume_after_three)   # + 60 s warning
        self.assertEqual(rep.states()[:2], ["idle", "stopped"])
        self.assertIn("Resumed.", self.logs)
        self.assertEqual(len(d.calls), 1)             # ripped only after the pause ended
        self.assertGreaterEqual(self.fetches, 1)      # fetched only after the pause ended
        self.assertLess(self.logs.index("Resumed."),
                        next(i for i, line in enumerate(self.logs) if "Rip warning shown" in line))

    def test_pause_appearing_mid_cycle_blocks_the_next_run(self):
        self.add("a", 47950, 600.0)
        self.pause.write_text("")
        d = FakeDriver()
        self.loop(d, max_sleeps=3)
        self.assertEqual((d.calls, self.fetches), ([], 0))

    def test_errors_are_reported_and_the_loop_carries_on(self):
        def flaky(ledger):
            self.fetches += 1
            if self.fetches == 1:
                raise OSError("network down")
            return []
        d = FakeDriver()
        d.resets = 0
        d.reset = lambda: setattr(d, "resets", d.resets + 1)
        # A failed fetch is retried at the normal 2-minute spacing, not hammered:
        # 60 s back-off + 3 x 20 s, then the second fetch.
        rep, ft = self.loop(d, max_sleeps=6, fetcher=flaky)
        self.assertIn("ERROR OSError: network down", self.logs)
        self.assertIn("error", rep.states())
        self.assertEqual(d.resets, 1)
        self.assertGreaterEqual(self.fetches, 2)


class Lock(unittest.TestCase):
    def test_one_holder_and_stale_locks(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "agent.lock"
            self.assertFalse(agent_running(p))
            p.write_text("999999")                      # dead process: stale
            self.assertIsNone(lock_holder(p))
            with AgentLock(p):
                self.assertEqual(p.read_text(), str(os.getpid()))
                self.assertFalse(agent_running(p))       # our own lock doesn't block us
            self.assertFalse(p.exists())

    def test_live_holder_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "agent.lock"
            p.write_text(str(os.getppid()))             # the parent process is alive
            self.assertTrue(agent_running(p))
            with self.assertRaises(RuntimeError):
                AgentLock(p).__enter__()


class LogRotation(unittest.TestCase):
    def test_rotates_on_a_new_day_and_prunes(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "agent.log"
            log.write_text("yesterday\n")
            y = datetime(2026, 10, 14, 18, 0).timestamp()
            os.utime(log, (y, y))
            old = Path(d) / "agent-2026-08-01.log"
            old.write_text("old\n")
            rotate_log(log, now=datetime(2026, 10, 15, 8, 0))
            self.assertFalse(log.exists())
            self.assertEqual((Path(d) / "agent-2026-10-14.log").read_text(), "yesterday\n")
            self.assertFalse(old.exists())                # older than 30 days

    def test_same_day_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "agent.log"
            log.write_text("today\n")
            rotate_log(log)
            self.assertTrue(log.exists())


if __name__ == "__main__":
    unittest.main()
