import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from ripauto import ledger as L
from ripauto.status import (NullReporter, Reporter, build_heartbeat, by_hand_recent, history,
                            queue_detail, queue_summary, recent_runs, scrub, today_summary)

NOW = datetime(2026, 10, 15, 12, 0, 0).astimezone()
CFG = {"machine_id": "8color-1", "label": "Printer #3 (8-color)", "cap_in": 650,
       "target_in": 600, "window_in": 48, "gap_in": 1.0, "flush_after_min": 30}

# Real-looking private data that must never reach the Pi.
CUSTOMER = "Kaitlyn Carter"
CUSTOMER2 = "Steve Springer"
EMAIL = "kaitlyn.carter@example.com"
ADDRESS = "1234 Maple Ridge Dr"
URL = "https://app.dripappsserver.com/shopify/gang/print-ready?id=abc&sig=XYZ"
LINE_ID = "50806994469179"
ORDER_ID = "18943958253883"
TOKEN = "shpat_" + "fake" * 8      # built at runtime so no token-shaped literal is committed
QUEUE = r"C:\Users\kevin\Downloads\8color-queue"
FILE = f"47946-{CUSTOMER}-{ORDER_ID}-22in x 120in 10ft-1-(1).png"
FORBIDDEN = [CUSTOMER, "Kaitlyn", "Carter", CUSTOMER2, "Springer", EMAIL, ADDRESS, "Maple",
             URL, "dripapps", LINE_ID, ORDER_ID, TOKEN, "shpat_", "C:\\", "Users\\kevin",
             "8color-queue", ".png"]


def rec(order_num, status=L.QUEUED, qty=1, **extra):
    r = {"order": f"#{order_num}", "order_num": order_num, "line_id": LINE_ID, "position": 1,
         "qty": qty, "length_in": 100.0, "width_in": 22.1, "low_res": False,
         "file": f"{QUEUE}\\{FILE}", "fetched_at": "2026-10-15T11:30:00",
         "status": status, "copies_ripped": 0, "size": "22in x 120in (10ft)",
         # fields a careless future change might add; the heartbeat must not read them
         "customer": CUSTOMER, "email": EMAIL, "shipping_address": ADDRESS, "url": URL,
         "order_id": ORDER_ID}
    r.update(extra)
    return r


def leaky_ledger():
    return {
        "a": rec(47946),
        "b": rec(47947, qty=3, copies_ripped=1),
        "c": rec(47948, status=L.HELD, updated_at="2026-10-15T11:50:00",
                 hold_reason=f"manual check: Downloads: {FILE} (a different sheet of #47948)"),
        "d": rec(47949, status=L.HELD, updated_at="2026-10-15T11:40:00",
                 hold_reason=f"manual check: PO-TRY queue: 47949-{CUSTOMER2}-{ORDER_ID}-22in x 48in 4ft-2-New Gang Sheet-(1)"),
        "e": rec(47950, status=L.HELD, updated_at="2026-10-15T11:30:00",
                 hold_reason=f"manual check: Downloads: 47950-{CUSTOMER2}.png (position unreadable)"),
        "f": rec(47951, status=L.HELD, updated_at="2026-10-15T11:20:00",
                 hold_reason=f"manual check: run x stopped: {QUEUE}\\{FILE} is missing"),
        "g": rec(47952, status=L.BY_HAND, by_hand=f"Downloads: {FILE}"),
        "h": rec(47953, status=L.RIPPED, runs=["8color-1-20261015-113554"]),
    }


def write_run(runs_dir, name, **fields):
    runs_dir.mkdir(parents=True, exist_ok=True)
    base = {"id": name, "dry_run": False, "outcome": "ripped", "pictures": 2,
            "planned_in": 135.4, "actual_in": 135.4, "started": "2026-10-15T11:36:01",
            "ended": "2026-10-15T11:36:55", "job_name": FILE[:-4], "files": [FILE]}
    base.update(fields)
    (runs_dir / f"{name}.json").write_text(json.dumps(base), encoding="utf-8")


class Leak(unittest.TestCase):
    def test_heartbeat_carries_no_private_data(self):
        with tempfile.TemporaryDirectory() as d:
            runs = Path(d)
            write_run(runs, "r1")
            hb = build_heartbeat(
                CFG, leaky_ledger(), runs, state="error", since=NOW,
                current_run={"id": "8color-1-20261015-113554", "orders": ["#47946", "#47947"],
                             "planned_in": 135.4, "progress_pct": 68,
                             "files": [FILE], "job_name": FILE},          # extra keys ignored
                last_error=f"Stop: {QUEUE}\\{FILE} not found; token {TOKEN}; see {URL}; "
                           f"line {LINE_ID}; mail {EMAIL}",
                now=NOW, host="8-color")
        text = json.dumps(hb)
        for bad in FORBIDDEN:
            self.assertNotIn(bad, text, f"{bad!r} leaked into the heartbeat")
        # ...while the useful parts survive
        self.assertIn("#47948", text)
        self.assertIn("manual check", text)
        self.assertIn("[path] is missing", text)
        self.assertIn("[path] not found", hb["last_error"])
        self.assertEqual(hb["current_run"]["orders"], ["#47946", "#47947"])
        self.assertEqual(set(hb["current_run"]), {"id", "orders", "planned_in", "progress_pct"})
        # the dashboard-detail sections really were built from the leaky data (not empty)
        self.assertEqual([s["order"] for s in hb["queue_detail"]["sheets"]], ["#47946", "#47947"])
        self.assertIsNotNone(hb["queue_detail"]["next_run"])
        self.assertEqual(hb["recent_runs"][0]["orders"], [])           # write_run has no orders
        self.assertEqual([b["order"] for b in hb["by_hand"]], ["#47952"])
        self.assertEqual(len(hb["history"]), 30)

    def test_scrub_examples(self):
        self.assertEqual(scrub(f"manual check: Downloads: {FILE} (x)"),
                         "manual check: Downloads: #47946 file (x)")
        self.assertEqual(scrub(f"Downloads: 47950-{CUSTOMER2}.png"), "Downloads: #47950 file")
        self.assertEqual(scrub(f"open {QUEUE}\\{FILE}"), "open [path]")
        self.assertEqual(scrub(f"see {URL}"), "see [link]")
        self.assertEqual(scrub(f"line {LINE_ID}"), "line [id]")
        self.assertEqual(scrub(f"key {TOKEN}"), "key [token]")
        self.assertEqual(scrub("preview shows 651.2 in, over 650 in"),
                         "preview shows 651.2 in, over 650 in")
        self.assertIsNone(scrub(None))
        self.assertEqual(len(scrub("x" * 500)), 160)


class Summaries(unittest.TestCase):
    def test_queue_counts_copies_left_and_oldest_wait(self):
        q = queue_summary(leaky_ledger(), NOW)
        self.assertEqual(q, {"sheets": 3, "inches": 300.0, "oldest_wait_min": 30})

    def test_today_counts_only_real_ripped_runs_from_today(self):
        with tempfile.TemporaryDirectory() as d:
            runs = Path(d)
            write_run(runs, "r1")
            write_run(runs, "r2", actual_in=None, planned_in=600.0, pictures=5)
            write_run(runs, "dry", dry_run=True)
            write_run(runs, "gate", outcome="gate")
            write_run(runs, "old", started="2026-10-14T16:00:00", ended="2026-10-14T16:01:00")
            self.assertEqual(today_summary(runs, NOW),
                             {"inches_ripped": 735.4, "sheets": 7, "runs": 2})
            self.assertEqual(today_summary(runs / "missing", NOW),
                             {"inches_ripped": 0.0, "sheets": 0, "runs": 0})

    def test_shape_matches_spec(self):
        with tempfile.TemporaryDirectory() as d:
            hb = build_heartbeat(CFG, {}, Path(d), state="idle", since=NOW, now=NOW, host="8-color")
        self.assertEqual(set(hb), {"machine_id", "label", "host", "agent_version", "state", "since",
                                   "current_run", "today", "queue", "flags", "last_error", "sent_at",
                                   "queue_detail", "recent_runs", "by_hand", "history"})
        self.assertEqual((hb["state"], hb["current_run"], hb["flags"]), ("idle", None, []))
        self.assertEqual(build_heartbeat(CFG, {}, Path(d), state="bogus", since=NOW)["state"], "error")

    def test_flags_newest_first(self):
        flags = build_heartbeat(CFG, leaky_ledger(), Path("nowhere"), state="idle", since=NOW)["flags"]
        self.assertEqual([f["order"] for f in flags], ["#47948", "#47949", "#47950", "#47951"])


class Detail(unittest.TestCase):
    def test_queue_detail_and_next_run(self):
        led = {"a": rec(47960, length_in=200.0, fetched_at="2026-10-15T11:40:00"),
               "b": rec(47961, length_in=100.0, qty=2, fetched_at="2026-10-15T11:50:00"),
               "c": rec(47962, status=L.RIPPED)}
        q = queue_detail(CFG, led, NOW)
        self.assertEqual([(s["order"], s["copies"], s["waited_min"]) for s in q["sheets"]],
                         [("#47960", 1, 20), ("#47961", 2, 10)])
        nr = q["next_run"]
        self.assertEqual((nr["orders"], nr["planned_in"], nr["pictures"]), (["#47960", "#47961"], 402.0, 3))
        self.assertEqual((nr["ready"], nr["reason"], nr["to_full_in"]), (False, "waiting", 150.0))
        self.assertTrue(nr["flush_at"].startswith("2026-10-15T12:10:00"))   # oldest + 30 min
        self.assertIsNone(queue_detail(CFG, {}, NOW)["next_run"])

    def test_history_runs_and_by_hand(self):
        recs = [{"outcome": "ripped", "ended": "2026-10-15T11:36:55", "actual_in": 135.4, "pictures": 2},
                {"outcome": "ripped", "ended": "2026-10-14T09:00:00", "planned_in": 600.0, "pictures": 9},
                {"outcome": "gate", "ended": "2026-10-15T10:00:00", "planned_in": 50.0, "pictures": 1},
                {"outcome": "ripped", "ended": "2026-08-01T09:00:00", "actual_in": 1.0, "pictures": 1}]
        led = {"h": rec(47970, status=L.BY_HAND, updated_at="2026-10-15T10:00:00")}
        h = history(recs, led, NOW, days=3)
        self.assertEqual([r["date"] for r in h], ["2026-10-13", "2026-10-14", "2026-10-15"])
        self.assertEqual(h[1], {"date": "2026-10-14", "inches": 600.0, "runs": 1, "sheets": 9, "by_hand": 0})
        self.assertEqual(h[2], {"date": "2026-10-15", "inches": 135.4, "runs": 1, "sheets": 2, "by_hand": 1})
        rr = recent_runs(recs, limit=2)
        self.assertEqual([r["outcome"] for r in rr], ["ripped", "gate"])   # newest two, newest first
        self.assertEqual(by_hand_recent(led)[0]["order"], "#47970")


class ReporterTest(unittest.TestCase):
    def test_sends_on_change_and_keeps_progress(self):
        sent = []
        with tempfile.TemporaryDirectory() as d:
            r = Reporter(CFG, leaky_ledger(), Path(d), "http://pi/api/heartbeat", "k",
                         log=lambda *_: None, sender=lambda u, k, hb: (sent.append(hb), (True, "ok"))[1])
            r.update(state="ripping", current_run={"id": "r", "orders": ["#1"], "planned_in": 10.0})
            r.update(progress_pct=41)
            r.update(state="idle", current_run=None)
            r.stop()
        self.assertEqual([h["state"] for h in sent], ["ripping", "ripping", "idle", "stopped"])
        self.assertEqual(sent[1]["current_run"]["progress_pct"], 41)
        self.assertIsNone(sent[2]["current_run"])

    def test_unreachable_dashboard_is_reported_once_and_never_raises(self):
        logs = []
        with tempfile.TemporaryDirectory() as d:
            r = Reporter(CFG, {}, Path(d), "http://pi/x", "k", log=logs.append,
                         sender=lambda u, k, hb: (False, "URLError"))
            r.update(state="planning")
            r.update(state="ripping")
        self.assertEqual(len(logs), 1)
        self.assertIn("not reachable", logs[0])

    def test_null_reporter(self):
        n = NullReporter()
        n.update(state="ripping")
        n.start()
        n.stop()


if __name__ == "__main__":
    unittest.main()
