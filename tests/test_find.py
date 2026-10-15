import json
import tempfile
import unittest
from pathlib import Path

from ripauto import ledger as L
from ripauto.find import report


class Find(unittest.TestCase):
    def test_reports_ledger_runs_and_log_lines(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            runs = state / "runs"
            runs.mkdir()
            (runs / "r1.json").write_text(json.dumps(
                {"id": "r1", "outcome": "ripped", "ended": "2026-10-15T11:36:55", "planned_in": 135.4,
                 "actual_in": 135.4, "orders": ["#47946", "#47947"]}))
            (state / "agent.log").write_text("11:36:57  Run r1 ripped: #47946 #47947\n"
                                             "11:40:00  Run r2 ripped: #147946 x147946\n")
            ledger = {"a": {"order": "#47946", "order_num": 47946, "position": 1, "qty": 1,
                            "copies_ripped": 1, "status": L.RIPPED, "runs": ["r1"],
                            "file": "C:\\q\\ripped\\r1\\47946-x-(1).png"},
                      "b": {"order": "#47100", "order_num": 47100, "status": L.QUEUED}}
            lines = []
            self.assertTrue(report("47946", ledger, runs_dir=runs, state_dir=state, out=lines.append))
            text = "\n".join(lines)
            self.assertIn("#47946 sheet 1  ripped", text)
            self.assertIn("r1  ripped", text)
            self.assertIn("Run r1 ripped: #47946", text)
            self.assertNotIn("#147946", text)          # a number merely containing 47946 isn't a match
            self.assertNotIn("#47100", text)
            self.assertFalse(report("99999", ledger, runs_dir=runs, state_dir=state, out=lines.append))


if __name__ == "__main__":
    unittest.main()
