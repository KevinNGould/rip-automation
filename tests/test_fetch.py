import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from ripauto.config import ConfigError
from ripauto.fetch import built_name, ledger_record, match_rule, sheets_from_orders
from tests.helpers import CMYK, EIGHT, RULES, line, order, png_bytes


class SheetsFromOrders(unittest.TestCase):
    def test_position_counts_other_machines_gang_sheets(self):
        orders = [order("#47862", "18900000000000", [
            line(1, title=CMYK, variant="22in x 48in (4ft)"),
            line(2, variant="22in x 600in (50ft)", qty=2),
        ])]
        [s] = sheets_from_orders(orders, RULES)
        self.assertEqual((s["line_id"], s["position"], s["qty"]), ("2", 2, 2))
        self.assertEqual(built_name(s), "47862-18900000000000-22in x 600in 50ft-2-(2)")

    def test_oldest_order_first_then_position(self):
        orders = [
            order("#47920", "2", [line(30)]),
            order("#47918", "1", [line(20, variant="22in x 24in (2ft)"), line(10)]),
        ]
        got = [(s["order_num"], s["position"]) for s in sheets_from_orders(orders, RULES)]
        self.assertEqual(got, [(47918, 1), (47918, 2), (47920, 1)])

    def test_min_order_and_unmatched_products(self):
        orders = [order("#47000", "1", [line(1)]),
                  order("#47900", "2", [line(2, title="Something else")])]
        self.assertEqual(sheets_from_orders(orders, RULES, min_order=47100), [])

    def test_customer_and_attributes_carried(self):
        attrs = {"_Print Ready File": "https://example.test/a.png",
                 "_Has Low Resolution": "Yes", "_Actual Height": "11.9"}
        orders = [order("#47918", "18943728582971", [line(5, attrs=attrs)],
                        customer="Kaitlyn Carter")]
        [s] = sheets_from_orders(orders, RULES)
        self.assertTrue(s["low_res"])
        self.assertEqual(s["actual_height"], "11.9")
        self.assertEqual(built_name(s), "47918-Kaitlyn Carter-18943728582971-22in x 12in 1ft-1-(1)")

    def test_disabled_kinds_ignored(self):
        rules = RULES + [{"title_contains": "Image by Size", "kind": "image_by_size"}]
        orders = [order("#1", "1", [line(1, title="DTF (4 Color CMYK) - Image by Size")])]
        self.assertEqual(sheets_from_orders(orders, rules), [])


class MatchRule(unittest.TestCase):
    def test_case_insensitive(self):
        self.assertIs(match_rule(EIGHT.upper(), RULES), RULES[0])
        self.assertIsNone(match_rule(CMYK, RULES))

    def test_two_rules_is_a_config_error(self):
        rules = RULES + [{"title_contains": "8 Color", "kind": "gang_sheet"}]
        with self.assertRaises(ConfigError):
            match_rule(EIGHT, rules)


class LedgerRecord(unittest.TestCase):
    def test_measures_the_downloaded_png(self):
        [s] = sheets_from_orders([order("#47918", "18943728582971", [line(7)])], RULES)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.png"
            p.write_bytes(png_bytes(6630, 3077, 11811))
            rec = ledger_record(s, p, now=datetime(2026, 10, 15, 9, 21, 56))
        self.assertEqual(rec["length_in"], 10.257)
        self.assertEqual(rec["length_source"], "png")
        self.assertEqual(rec["width_in"], 22.1)
        self.assertEqual((rec["order"], rec["line_id"], rec["position"], rec["qty"]),
                         ("#47918", "7", 1, 1))
        self.assertEqual(rec["fetched_at"], "2026-10-15T09:21:56")
        self.assertNotIn("url", rec)
        self.assertNotIn("customer", rec)


if __name__ == "__main__":
    unittest.main()
