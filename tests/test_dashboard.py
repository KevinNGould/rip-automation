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
                         {"/", "/api/heartbeat", "/api/machines"})

    def test_oversized_body_refused(self):
        r = self.client.post("/api/heartbeat", data="x" * (70 * 1024),
                             headers={"X-Agent-Key": self.key, "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)


if __name__ == "__main__":
    unittest.main()
