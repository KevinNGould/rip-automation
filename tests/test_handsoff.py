import unittest

from ripauto.handsoff import HandsOff, HumanInput, ms_after


class FakePC:
    """A clock, an input timestamp, a pointer and a foreground process."""

    def __init__(self):
        self.t, self.last_input, self.pos, self.fg = 10_000, 9_000, (100, 100), 42

    def guard(self):
        return HandsOff(last_input_ms=lambda: self.last_input, tick_ms=lambda: self.t,
                        cursor=lambda: self.pos, foreground_pid=lambda: self.fg)

    def our_click(self, g, pos):
        """The agent clicks: input happens now, pointer moves, then mark()."""
        self.pos, self.last_input = pos, self.t
        g.mark()


class Guard(unittest.TestCase):
    def setUp(self):
        self.pc = FakePC()
        self.g = self.pc.guard()
        self.g.arm(pid=42)

    def test_quiet_pc_passes(self):
        self.pc.t += 5000
        self.g.check("Add")
        self.g.check("RIP", need_front=True)

    def test_our_own_clicks_dont_trip_it(self):
        self.pc.our_click(self.g, (300, 60))
        self.pc.t += 4000
        self.g.check("next step", need_front=True)

    def test_a_person_typing_trips_it(self):
        self.pc.t += 2000
        self.pc.last_input = self.pc.t                  # keyboard: pointer unchanged
        with self.assertRaisesRegex(HumanInput, "someone used the mouse or keyboard"):
            self.g.check("F3")

    def test_the_mouse_moving_trips_it(self):
        self.pc.pos = (101, 100)
        with self.assertRaisesRegex(HumanInput, "the mouse moved"):
            self.g.check("RIP")

    def test_another_window_in_front_trips_it_before_a_click(self):
        self.pc.fg = 7
        self.g.check("waiting for load")                 # not a click: fine
        with self.assertRaisesRegex(HumanInput, "another window"):
            self.g.check("Print", need_front=True)

    def test_disarmed_never_trips(self):
        self.g.disarm()
        self.pc.pos, self.pc.last_input = (0, 0), self.pc.t + 99
        self.g.check("after Print", need_front=True)

    def test_tick_wraparound(self):
        self.assertEqual(ms_after(5, 0xFFFFFFF0), 21)
        self.assertEqual(ms_after(0xFFFFFFF0, 5), -21)
        self.assertEqual(ms_after(1000, 400), 600)


if __name__ == "__main__":
    unittest.main()
