import unittest

from ripauto.busy import wait_until_loaded
from ripauto.potry import fallback_load_s, short_of_planned


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def feed(steps):
    """sample() over a script of (cpu % of one core, MB read that second)."""
    state = {"cpu": 0.0, "read": 1_000_000_000, "i": 0}

    def sample():
        i = state["i"]
        if i:
            pct, mb = steps[min(i - 1, len(steps) - 1)]
            state["cpu"] += pct / 100
            state["read"] += int(mb * 1e6)
        state["i"] += 1
        return state["cpu"], state["read"]
    return sample


def wait(steps, expect_mb, timeout=300):
    c = Clock()
    return wait_until_loaded(feed(steps), expect_mb * 1e6, timeout, sleep=c.sleep, clock=c)


class LoadWait(unittest.TestCase):
    def test_big_picture_waits_for_reads_and_quiet(self):
        # 2026-10-15 14:07 case: 320 MB loading at ~40 MB/s, busy decoding after.
        steps = [(100, 40)] * 8 + [(90, 0)] * 10 + [(1, 0)] * 10
        done, note = wait(steps, 320)
        self.assertTrue(done)
        self.assertIn("320 of 320 MB read", note)
        self.assertIn("quiet after 22 s", note)          # 18 busy + 4 quiet

    def test_quiet_gap_mid_load_is_not_done(self):
        # A pause in CPU before the file has been read doesn't count.
        steps = [(100, 20)] * 3 + [(1, 0)] * 5 + [(100, 40)] * 4 + [(1, 0)] * 10
        done, note = wait(steps, 200)
        self.assertTrue(done)
        self.assertIn("quiet after 16 s", note)

    def test_reads_never_arrive_gives_up_waiting_after_stall(self):
        done, note = wait([(1, 0)] * 30, 100)
        self.assertTrue(done)
        self.assertIn("nothing happening", note)

    def test_timeout(self):
        done, note = wait([(100, 1)] * 400, 1000, timeout=60)
        self.assertFalse(done)
        self.assertIn("still busy", note)

    def test_counters_unreadable(self):
        done, note = wait_until_loaded(lambda: (None, None), 1e6, 60, sleep=lambda s: None)
        self.assertFalse(done)

    def test_fallback_scales_with_size(self):
        self.assertAlmostEqual(fallback_load_s(320e6), 52.0)
        self.assertEqual(fallback_load_s(0), 20)


class ShortRetry(unittest.TestCase):
    def test_only_a_short_preview_is_retried(self):
        self.assertTrue(short_of_planned("preview shows 24.0 in but 295.4 in was planned (a picture may not have loaded)"))
        self.assertFalse(short_of_planned(None))
        self.assertFalse(short_of_planned("preview length unreadable"))
        self.assertFalse(short_of_planned("preview 650.2 in is over 650 in"))


if __name__ == "__main__":
    unittest.main()
