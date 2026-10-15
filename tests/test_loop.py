import os
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

from ripauto import ledger as L
from ripauto.agent import run_loop
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
        self.cfg = dict(CFG, queue_dir=str(self.queue), fetch_every_min=2, require_idle_s=60)
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
                 sleep=ft.sleep, stop=stop, runs_dir=self.runs)
        return rep, ft

    def test_fetches_on_start_then_every_two_minutes(self):
        self.loop(FakeDriver(), max_sleeps=13)      # 13 x 20 s = 260 s
        self.assertEqual(self.fetches, 3)            # t=0, 120, 240

    def test_fetched_full_run_gets_ripped(self):
        d = FakeDriver()
        rep, _ = self.loop(d, max_sleeps=2, fetcher=self.fetcher(new=("a", 47950, 600.0)))
        self.assertEqual(len(d.calls), 1)
        self.assertEqual(L.status(self.ledger["a"]), L.RIPPED)
        self.assertIn("ripping", rep.states())

    def test_waits_for_the_pc_to_be_idle(self):
        self.add("a", 47950, 600.0)
        d = FakeDriver()
        self.loop(d, max_sleeps=3, idle=lambda: 5)
        self.assertEqual(d.calls, [])
        self.assertIn("Waiting: someone is using this PC.", self.logs)
        self.assertEqual(L.status(self.ledger["a"]), L.QUEUED)

    def test_pause_switch(self):
        self.add("a", 47950, 600.0)
        self.pause.write_text("")
        d = FakeDriver()

        def resume_after_three(ft):
            if ft.sleeps == 3:
                self.pause.unlink()
        rep, _ = self.loop(d, max_sleeps=5, on_sleep=resume_after_three)
        self.assertEqual(rep.states()[:2], ["idle", "stopped"])
        self.assertIn("Resumed.", self.logs)
        self.assertEqual(len(d.calls), 1)             # ripped only after the pause ended
        self.assertEqual(self.fetches, 1)

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
