"""Tests for dashboard/oss_rip.py (the Pi side). Needs Flask, which the hub Pi
has; skipped elsewhere. Run on the Pi from a copy of the repo:
    python3 -m unittest tests.test_dashboard"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

try:
    import flask  # noqa: F401
    HAVE_FLASK = True
except ImportError:
    HAVE_FLASK = False

HB = {
    "machine_id": "8color-1", "label": "Printer #3 (8-color)", "host": "8-color",
    "agent_version": "0.1.0", "state": "ripping", "since": "2026-10-15T11:36:01-04:00",
    "current_run": {"id": "8color-1-20261015-113554", "orders": ["#47946", "#47947", "Kaitlyn"],
                    "planned_in": 135.4, "progress_pct": 68, "files": ["x.png"]},
    "today": {"inches_ripped": 135.4, "sheets": 2, "runs": 1},
    "queue": {"sheets": 0, "inches": 0, "oldest_wait_min": 0},
    "flags": [{"order": "#47948", "reason": "manual check: Downloads: 47948-Kaitlyn Carter-1-(1).png"},
              {"order": "not an order", "reason": "x"}],
    "last_error": r"Stop: C:\Users\kevin\Downloads\8color-queue\47946-Kaitlyn Carter-(1).png missing",
    "sent_at": "2026-10-15T11:36:13-04:00",
    "customer": "Kaitlyn Carter",
    "queue_detail": {"sheets": [{"order": "#47951", "position": 1, "copies": 1, "length_in": 37.2,
                                 "waited_min": 12, "low_res": True, "file": "47951-Kaitlyn Carter-(1).png"},
                                {"order": "Kaitlyn Carter", "position": 1}],
                     "next_run": {"orders": ["#47951"], "planned_in": 37.2, "pictures": 1, "ready": False,
                                  "reason": "waiting", "flush_at": "2026-10-15T12:06:00-04:00",
                                  "to_full_in": 514.8, "runs_planned": 1, "url": "https://x"}},
    "recent_runs": [{"id": "8color-1-20261015-113554", "ended": "2026-10-15T11:36:55",
                     "orders": ["#47946", "#47947"], "pictures": 2, "planned_in": 135.4,
                     "actual_in": 135.4, "outcome": "ripped", "job_name": "47946-Kaitlyn Carter-(1)"}],
    "by_hand": [{"order": "#47932", "position": 1, "copies": 1, "length_in": 13.2,
                 "at": "2026-10-15T11:20:00", "detail": "Downloads: 47932-Kaitlyn Carter.png"}],
    "history": [{"date": "2026-10-15", "inches": 135.4, "runs": 1, "sheets": 2, "by_hand": 1},
                {"date": "not a date", "inches": 5}],
}


@unittest.skipUnless(HAVE_FLASK, "needs Flask (run on the hub Pi)")
class Dashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        os.environ["HOME"] = os.environ["USERPROFILE"] = cls.home
        shutil.copy(REPO / "config" / "machines.json", Path(cls.home) / "oss_rip_machines.json")
        spec = importlib.util.spec_from_file_location("oss_rip", REPO / "dashboard" / "oss_rip.py")
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.client = cls.mod.app.test_client()
        cls.key = cls.mod.settings["agent_key"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)

    def post(self, body, key=None):
        return self.client.post("/api/heartbeat", json=body,
                                headers={"X-Agent-Key": self.key if key is None else key})

    def cards(self):
        return {c["machine_id"]: c for c in self.client.get("/api/machines").get_json()["machines"]}

    def test_settings_file_is_private_and_key_made(self):
        p = Path(self.home) / "oss_rip_settings.json"
        self.assertEqual(len(json.loads(p.read_text())["agent_key"]), 48)
        if os.name == "posix":
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_rejects_bad_key_and_unknown_machine(self):
        self.assertEqual(self.post(HB, key="wrong").status_code, 401)
        self.assertEqual(self.post(dict(HB, machine_id="nope")).status_code, 400)

    def test_heartbeat_is_cleaned_and_shown(self):
        self.assertEqual(self.post(HB).status_code, 200)
        card = self.cards()["8color-1"]
        self.assertEqual(card["status"], "running")
        hb = card["hb"]
        self.assertEqual(hb["current_run"]["orders"], ["#47946", "#47947"])
        self.assertNotIn("files", hb["current_run"])
        self.assertEqual([f["order"] for f in hb["flags"]], ["#47948"])
        text = json.dumps(card)
        for bad in ("Kaitlyn", "Carter", "C:\\", "8color-queue", ".png", "customer"):
            self.assertNotIn(bad, text)
        self.assertIn("#47948 file", text)

    def test_detail_sections_are_cleaned(self):
        self.post(HB)
        hb = self.cards()["8color-1"]["hb"]
        qd = hb["queue_detail"]
        self.assertEqual([s["order"] for s in qd["sheets"]], ["#47951"])
        self.assertNotIn("file", qd["sheets"][0])
        self.assertNotIn("url", qd["next_run"])
        self.assertEqual(qd["next_run"]["reason"], "waiting")
        self.assertEqual(hb["recent_runs"][0]["orders"], ["#47946", "#47947"])
        self.assertNotIn("job_name", hb["recent_runs"][0])
        self.assertEqual(hb["by_hand"][0]["order"], "#47932")
        self.assertEqual([h["date"] for h in hb["history"]], ["2026-10-15"])
        self.assertNotIn("Kaitlyn", json.dumps(hb))

    def test_order_search_across_machines(self):
        self.post(HB)
        self.post(dict(HB, machine_id="cmyk-1", recent_runs=[], by_hand=[], flags=[],
                       queue_detail={"sheets": [{"order": "#47946", "position": 2, "copies": 1,
                                                 "length_in": 50.0, "waited_min": 3}]}))
        r = self.client.get("/api/search?q=%2347946").get_json()
        self.assertTrue(r["ok"])
        [o] = r["orders"]
        self.assertEqual(o["order"], "#47946")
        kinds = {(e["machine_id"], e["kind"]) for e in o["events"]}
        self.assertEqual(kinds, {("8color-1", "ripped"), ("cmyk-1", "waiting")})
        ripped = [e for e in o["events"] if e["kind"] == "ripped"][0]
        self.assertEqual((ripped["ref"], ripped["inches"], ripped["label"]),
                         ("8color-1-20261015-113554", 135.4, "Printer #3 (8-color)"))
        # prefix search finds several orders; held + by-hand sightings recorded too
        r = self.client.get("/api/search?q=4794").get_json()
        found = {o["order"] for o in r["orders"]}
        self.assertTrue({"#47946", "#47947", "#47948"} <= found)   # #47948 = held flag
        self.assertNotIn("#47951", found)
        r = self.client.get("/api/search?q=4793").get_json()
        self.assertEqual([e["kind"] for e in r["orders"][0]["events"]], ["by_hand"])
        self.assertEqual(self.client.get("/api/search?q=47").status_code, 400)

    def test_repeat_sightings_update_not_duplicate(self):
        self.post(HB)
        self.post(HB)
        r = self.client.get("/api/search?q=47951").get_json()
        self.assertEqual(len(r["orders"][0]["events"]), 1)
        if os.name == "posix":
            db = Path(self.home) / "oss_rip.db"
            self.assertEqual(db.stat().st_mode & 0o777, 0o600)

    def test_every_machine_gets_a_card(self):
        cards = self.cards()
        self.assertEqual(set(cards), {"8color-1", "cmyk-1", "cmyk-2", "uv-1", "uv-2"})
        self.assertFalse(cards["cmyk-1"]["enabled"])
        self.assertEqual(cards["uv-2"]["status"], "never")

    def test_offline_and_stopped(self):
        self.post(dict(HB, state="stopped"))
        self.assertEqual(self.cards()["8color-1"]["status"], "stopped")
        self.mod.latest["8color-1"]["received"] -= 121
        self.assertEqual(self.cards()["8color-1"]["status"], "offline")
        self.post(HB)

    def test_page_and_no_control_routes(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("RIP Automation", page)
        rules = {r.rule for r in self.mod.app.url_map.iter_rules()}
        self.assertEqual(rules - {"/static/<path:filename>"},
                         {"/", "/tv", "/api/heartbeat", "/api/machines", "/api/search"})
        for path in ("/api/machines", "/api/search"):           # read-only: GET only
            methods = {m for r in self.mod.app.url_map.iter_rules() if r.rule == path for m in r.methods}
            self.assertEqual(methods - {"HEAD", "OPTIONS"}, {"GET"})
        self.assertIn("Find order", self.client.get("/tv").get_data(as_text=True))

    def test_oversized_body_refused(self):
        r = self.client.post("/api/heartbeat", data="x" * (140 * 1024),
                             headers={"X-Agent-Key": self.key, "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)


if __name__ == "__main__":
    unittest.main()
