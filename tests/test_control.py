import unittest

from ripauto.control import dashboard_base, describe

IDLE_NEXT = {"state": "idle", "flags": [], "queue_detail": {"next_run": {
    "planned_in": 303.4, "ready": False, "flush_at": "2026-10-15T12:56:26-04:00"}}}


class Describe(unittest.TestCase):
    def test_off(self):
        d = describe(False, False, None)
        self.assertEqual((d["kind"], d["button"], d["action"]), ("off", "Start agent", "start"))
        self.assertIn("pause switch is also on", describe(False, True, None)["detail"])

    def test_on_waiting_for_flush(self):
        d = describe(True, False, IDLE_NEXT)
        self.assertEqual((d["kind"], d["headline"], d["action"]), ("on", "ON", "pause"))
        self.assertIn("303 in", d["detail"])
        self.assertIn("12:56", d["detail"])

    def test_on_ripping_with_progress(self):
        hb = {"state": "ripping", "current_run": {"orders": ["#47951", "#47952"], "progress_pct": 68}}
        d = describe(True, False, hb)
        self.assertIn("Ripping #47951 #47952: 68%", d["detail"])

    def test_pausing_mid_rip_then_paused(self):
        self.assertEqual(describe(True, True, {"state": "ripping"})["kind"], "pausing")
        d = describe(True, True, {"state": "stopped"})
        self.assertEqual((d["kind"], d["button"], d["action"]), ("paused", "Resume agent", "resume"))

    def test_other_states(self):
        self.assertIn("Waiting for new", describe(True, False, {"state": "idle"})["detail"])
        self.assertIn("Can't reach the dashboard", describe(True, False, None)["detail"])
        self.assertIn("needs a look: boom", describe(True, False, {"state": "error", "last_error": "boom"})["detail"])
        ready = {"state": "idle", "queue_detail": {"next_run": {"planned_in": 600, "ready": True}}}
        self.assertIn("is ready", describe(True, False, ready)["detail"])
        held = dict(IDLE_NEXT, flags=[{"order": "#1"}, {"order": "#2"}])
        self.assertIn("2 sheets held", describe(True, False, held)["detail"])

    def test_rip_now_only_when_it_makes_sense(self):
        from ripauto.control import can_rip_now
        self.assertTrue(can_rip_now(True, False, IDLE_NEXT))
        self.assertFalse(can_rip_now(False, False, IDLE_NEXT))           # agent off
        self.assertFalse(can_rip_now(True, True, IDLE_NEXT))             # paused
        self.assertFalse(can_rip_now(True, False, {"state": "ripping"}))
        self.assertFalse(can_rip_now(True, False, {"state": "idle"}))    # nothing waiting
        self.assertFalse(can_rip_now(True, False, None))

    def test_dashboard_base(self):
        self.assertEqual(dashboard_base({"dashboard_url": "http://osshub.local:8085/api/heartbeat"}),
                         "http://osshub.local:8085")
        self.assertEqual(dashboard_base({}), "")


if __name__ == "__main__":
    unittest.main()
