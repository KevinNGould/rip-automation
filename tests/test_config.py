import json
import tempfile
import unittest
from pathlib import Path

from ripauto.config import MACHINES_PATH, ConfigError, check_shop, load_machine, load_shop
from tests.helpers import SHOP, shop

LOCAL = {"machine_id": "8color-1", "queue_dir": "C:\\q", "shop": "x.myshopify.com",
         "cap_in": 650, "target_in": 600, "window_in": 48, "max_width_in": 23}


class Shop(unittest.TestCase):
    def test_committed_machines_json_is_valid(self):
        s = load_shop(MACHINES_PATH)
        enabled = [m for m, v in s["machines"].items() if v.get("enabled")]
        self.assertEqual(enabled, ["8color-1"])

    def test_fixture_passes(self):
        check_shop(SHOP)

    def test_two_enabled_on_one_line_refused(self):
        with self.assertRaisesRegex(ConfigError, "cmyk-1 and cmyk-2"):
            check_shop(shop(**{"cmyk-1": {"enabled": True}, "cmyk-2": {"enabled": True}}))

    def test_one_enabled_per_line_on_different_lines_is_fine(self):
        check_shop(shop(**{"cmyk-1": {"enabled": True}}))

    def test_rules_that_can_match_one_title_on_two_lines_refused(self):
        s = shop()
        s["lines"]["uv"]["product_rules"] = [{"title_contains": "8 Color CMYK", "kind": "gang_sheet"}]
        with self.assertRaisesRegex(ConfigError, "can match the same product"):
            check_shop(s)

    def test_unknown_line_refused(self):
        with self.assertRaises(ConfigError):
            check_shop(shop(**{"x-1": {"line": "nope", "enabled": False}}))

    def test_rule_needs_kind(self):
        s = shop()
        s["lines"]["uv"]["product_rules"] = [{"title_contains": "UV Gang Sheet"}]
        with self.assertRaises(ConfigError):
            check_shop(s)


class Machine(unittest.TestCase):
    def load(self, local, shop_data=SHOP):
        with tempfile.TemporaryDirectory() as d:
            mp, sp = Path(d) / "machine.json", Path(d) / "machines.json"
            mp.write_text(json.dumps(local), encoding="utf-8")
            sp.write_text(json.dumps(shop_data), encoding="utf-8")
            return load_machine(mp, sp)

    def test_merges_line_and_label(self):
        cfg = self.load(LOCAL)
        self.assertEqual((cfg["line"], cfg["label"], cfg["line_label"]),
                         ("8color", "Printer #3", "8-Color DTF"))
        self.assertEqual(len(cfg["product_rules"]), 1)
        self.assertEqual({r["line"] for r in cfg["shop_rules"]}, {"8color", "cmyk"})
        self.assertEqual((cfg["max_width_in"], cfg["gap_in"]), (23, 1.0))

    def test_old_keys_in_machine_json_refused(self):
        with self.assertRaisesRegex(ConfigError, "product_rules now live in machines.json"):
            self.load(dict(LOCAL, product_rules=[]))

    def test_unknown_machine_refused(self):
        with self.assertRaisesRegex(ConfigError, "isn't in machines.json"):
            self.load(dict(LOCAL, machine_id="8color"))

    def test_switched_off_machine_refused(self):
        with self.assertRaisesRegex(ConfigError, "switched off"):
            self.load(dict(LOCAL, machine_id="cmyk-1"))

    def test_fill_window_must_fit_under_cap(self):
        with self.assertRaises(ConfigError):
            self.load(dict(LOCAL, window_in=60))


if __name__ == "__main__":
    unittest.main()
