import random
import unittest
from datetime import datetime, timedelta

from ripauto.planner import Limits, Sheet, plan

NOW = datetime(2026, 10, 15, 12, 0, 0)
LIM = Limits(cap_in=650, target_in=600, window_in=48, gap_in=0.5, flush_after_min=30)
NO_GAP = Limits(cap_in=650, target_in=600, window_in=48, gap_in=0, flush_after_min=30)


def sheet(order_num, length, qty=1, position=1, waited_min=0, width=22.1, key=None):
    return Sheet(key=key or f"{order_num}-{position}", order=f"#{order_num}",
                 order_num=order_num, length_in=length, qty=qty, position=position,
                 width_in=width, ready_at=NOW - timedelta(minutes=waited_min))


def keys(run):
    return [s.key for s in run.sheets]


def copies(run):
    return [(i.sheet.key, i.copies) for i in run.items]


class CapEdges(unittest.TestCase):
    def test_exactly_650_rips_alone(self):
        runs, holds = plan([sheet(1, 650.0)], NOW, LIM)
        self.assertEqual(holds, [])
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].planned_in, 650.0)
        self.assertEqual((runs[0].ready, runs[0].reason), (True, "full"))

    def test_651_is_held(self):
        runs, holds = plan([sheet(1, 651.0)], NOW, LIM)
        self.assertEqual(runs, [])
        self.assertEqual(holds[0].reason, "over 650 in")

    def test_qty_pushing_over_splits_copies_across_runs(self):
        runs, holds = plan([sheet(1, 400.0, qty=2)], NOW, LIM)
        self.assertEqual(holds, [])
        self.assertEqual([copies(r) for r in runs], [[("1-1", 1)], [("1-1", 1)]])
        self.assertEqual([r.planned_in for r in runs], [400.0, 400.0])

    def test_qty_over_cap_only_because_of_gaps_splits(self):
        # 2 x 325 = 650, plus one 0.5 in gap = 650.5 > cap.
        runs, holds = plan([sheet(1, 325.0, qty=2)], NOW, LIM)
        self.assertEqual(holds, [])
        self.assertEqual([copies(r) for r in runs], [[("1-1", 1)], [("1-1", 1)]])

    def test_single_copy_over_cap_is_held_whatever_the_qty(self):
        runs, holds = plan([sheet(1, 651.0, qty=3)], NOW, LIM)
        self.assertEqual(runs, [])
        self.assertEqual(holds[0].reason, "over 650 in")

    def test_split_copies_share_runs_with_other_sheets(self):
        # 3 x 300 can't fit under the cap together, so copies go one by one:
        # run 1 = 300 + 300 (+gap) + #2's 40 (+gap); the third copy waits for run 2.
        runs, _ = plan([sheet(1, 300.0, qty=3), sheet(2, 40.0)], NOW, LIM)
        self.assertEqual([copies(r) for r in runs],
                         [[("1-1", 2), ("2-1", 1)], [("1-1", 1)]])
        self.assertEqual(runs[0].planned_in, 641.0)

    def test_copies_that_fit_together_are_not_split(self):
        # 3 x 100 fits under the cap, so it moves to the next run whole
        # rather than leaving one copy behind in a run that only has room for one.
        runs, _ = plan([sheet(1, 500.0), sheet(2, 100.0, qty=3)], NOW, NO_GAP)
        self.assertEqual([copies(r) for r in runs], [[("1-1", 1)], [("2-1", 3)]])

    def test_qty_within_cap_counts_every_copy(self):
        runs, _ = plan([sheet(1, 200.0, qty=3)], NOW, LIM)
        self.assertEqual(runs[0].pictures, 3)
        self.assertEqual(runs[0].planned_in, 601.0)      # 600 + 2 gaps

    def test_649_alone_is_fine_but_nothing_joins_it(self):
        runs, _ = plan([sheet(1, 649.0), sheet(2, 1.0)], NOW, NO_GAP)
        self.assertEqual([keys(r) for r in runs], [["1-1"], ["2-1"]])

    def test_fill_limit_is_648_with_gaps(self):
        # 300 + 0.5 + 347.5 = 648 exactly: allowed. 347.6 would not be.
        runs, _ = plan([sheet(1, 300.0), sheet(2, 347.5)], NOW, LIM)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].planned_in, 648.0)
        runs, _ = plan([sheet(1, 300.0), sheet(2, 347.6)], NOW, LIM)
        self.assertEqual(len(runs), 2)


class OtherHolds(unittest.TestCase):
    def test_unknown_length(self):
        runs, holds = plan([sheet(1, None)], NOW, LIM)
        self.assertEqual((runs, holds[0].reason), ([], "length unknown"))

    def test_too_wide(self):
        lim = Limits(650, 600, 48, max_width_in=22.5)
        runs, holds = plan([sheet(1, 100.0, width=23.0), sheet(2, 100.0, width=22.1)], NOW, lim)
        self.assertEqual([h.sheet.key for h in holds], ["1-1"])
        self.assertIn("wider than 22.5 in", holds[0].reason)
        self.assertEqual([keys(r) for r in runs], [["2-1"]])

    def test_width_unchecked_by_default(self):
        _, holds = plan([sheet(1, 100.0, width=40.0)], NOW, LIM)
        self.assertEqual(holds, [])

    def test_held_sheet_does_not_stop_rest_of_order(self):
        runs, holds = plan([sheet(1, 700.0, position=1), sheet(1, 50.0, position=2)], NOW, LIM)
        self.assertEqual(len(holds), 1)
        self.assertEqual([keys(r) for r in runs], [["1-2"]])


class Filling(unittest.TestCase):
    def test_oldest_first_then_first_fit_pulls_smaller_later_sheet(self):
        sheets = [sheet(1, 300.0), sheet(2, 300.0), sheet(3, 100.0), sheet(4, 40.0)]
        runs, _ = plan(sheets, NOW, NO_GAP)
        # 300 + 300 = 600; #3 (100) would reach 700; #4 (40) fits -> 640.
        self.assertEqual([keys(r) for r in runs], [["1-1", "2-1", "4-1"], ["3-1"]])
        self.assertEqual(runs[0].planned_in, 640.0)

    def test_full_run_is_ready_partial_waits(self):
        runs, _ = plan([sheet(1, 560.0), sheet(2, 560.0)], NOW, LIM)
        self.assertEqual([(r.ready, r.reason) for r in runs], [(True, "full"), (True, "full")])
        runs, _ = plan([sheet(1, 551.0)], NOW, LIM)
        self.assertEqual((runs[0].ready, runs[0].reason), (False, "waiting"))
        runs, _ = plan([sheet(1, 552.0)], NOW, LIM)
        self.assertEqual((runs[0].ready, runs[0].reason), (True, "full"))

    def test_sorts_by_order_number_and_position(self):
        sheets = [sheet(3, 10.0), sheet(1, 10.0, position=2), sheet(1, 10.0, position=1)]
        runs, _ = plan(sheets, NOW, LIM)
        self.assertEqual(keys(runs[0]), ["1-1", "1-2", "3-1"])

    def test_empty_queue(self):
        self.assertEqual(plan([], NOW, LIM), ([], []))


class Flush(unittest.TestCase):
    def test_flush_when_oldest_has_waited_long_enough(self):
        runs, _ = plan([sheet(1, 100.0, waited_min=29.9)], NOW, LIM)
        self.assertEqual((runs[0].ready, runs[0].reason), (False, "waiting"))
        runs, _ = plan([sheet(1, 100.0, waited_min=30)], NOW, LIM)
        self.assertEqual((runs[0].ready, runs[0].reason), (True, "flush"))

    def test_oldest_sheet_in_run_decides(self):
        runs, _ = plan([sheet(1, 100.0, waited_min=5), sheet(2, 100.0, waited_min=31)], NOW, LIM)
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0].reason, runs[0].oldest_wait_min), ("flush", 31.0))

    def test_flush_time_comes_from_limits(self):
        lim = Limits(650, 600, 48, flush_after_min=10)
        runs, _ = plan([sheet(1, 100.0, waited_min=10)], NOW, lim)
        self.assertTrue(runs[0].ready)


class OrdersTogether(unittest.TestCase):
    def test_order_kept_together_moves_to_next_run(self):
        sheets = [sheet(1, 400.0),
                  sheet(2, 150.0, position=1), sheet(2, 150.0, position=2),
                  sheet(3, 200.0)]
        runs, _ = plan(sheets, NOW, NO_GAP)
        # #2 together is 300: 400 + 300 > 648, so #2 can't join run 1 whole.
        # #3 (200) fits run 1 -> 600. #2 goes whole into run 2.
        self.assertEqual([keys(r) for r in runs], [["1-1", "3-1"], ["2-1", "2-2"]])

    def test_order_over_cap_splits_into_consecutive_runs(self):
        sheets = [sheet(5, 400.0, position=1), sheet(5, 400.0, position=2),
                  sheet(5, 100.0, position=3)]
        runs, holds = plan(sheets, NOW, NO_GAP)
        self.assertEqual(holds, [])
        self.assertEqual([keys(r) for r in runs], [["5-1", "5-3"], ["5-2"]])
        self.assertEqual([r.orders for r in runs], [["#5"], ["#5"]])

    def test_order_between_fill_limit_and_cap_rips_alone(self):
        sheets = [sheet(1, 325.0, position=1), sheet(1, 324.0, position=2), sheet(2, 10.0)]
        runs, _ = plan(sheets, NOW, NO_GAP)
        self.assertEqual([keys(r) for r in runs], [["1-1", "1-2"], ["2-1"]])
        self.assertEqual(runs[0].planned_in, 649.0)


class Invariants(unittest.TestCase):
    def test_random_queues_never_break_the_rules(self):
        rng = random.Random(47918)
        for _ in range(500):
            sheets = []
            for n in range(rng.randint(0, 25)):
                for pos in range(1, rng.choice([1, 1, 1, 2, 3]) + 1):
                    sheets.append(sheet(
                        47000 + n, rng.choice([6, 12, 18, 24, 54, 78, 120, 342, 600, 649, 700])
                        + rng.random(), qty=rng.choice([1, 1, 1, 2, 3]), position=pos,
                        waited_min=rng.randint(0, 60)))
            runs, holds = plan(sheets, NOW, LIM)
            placed = {}
            for r in runs:
                for i in r.items:
                    placed[i.sheet.key] = placed.get(i.sheet.key, 0) + i.copies
            held = {h.sheet.key for h in holds}
            self.assertFalse(held & set(placed))
            for s in sheets:                       # every copy ripped once, or held
                if s.key in held:
                    self.assertGreater(s.length_in, LIM.cap_in)
                else:
                    self.assertEqual(placed.get(s.key), s.qty, s.key)
            for s in sheets:                       # copies that fit together stay together
                if s.key not in held and s.length_in * s.qty + 0.5 * (s.qty - 1) <= LIM.cap_in:
                    self.assertEqual(sum(1 for r in runs if s.key in keys(r)), 1, s.key)
            for r in runs:
                self.assertLessEqual(r.planned_in, LIM.cap_in)
                if r.planned_in > LIM.fill_max + 1e-6:      # only a lone sheet or order
                    self.assertEqual(len(r.orders), 1)
                total = sum(i.sheet.length_in * i.copies for i in r.items) + 0.5 * (r.pictures - 1)
                self.assertEqual(r.pictures, sum(i.copies for i in r.items))
                self.assertAlmostEqual(r.planned_in, total, places=2)
            # An order that fits in one run is never split.
            orders = {}
            for s in sheets:
                orders.setdefault(s.order, []).append(s)
            for o, ss in orders.items():
                ss = [s for s in ss if s.key not in held]
                if not ss:
                    continue
                pics = sum(s.qty for s in ss)
                if sum(s.length_in * s.qty for s in ss) + 0.5 * (pics - 1) <= LIM.cap_in:
                    homes = {i for i, r in enumerate(runs) for s in r.sheets if s.order == o}
                    self.assertEqual(len(homes), 1, o)


class FromLedger(unittest.TestCase):
    def test_builds_sheet_from_ledger_record(self):
        rec = {"order": "#47918", "order_num": 47918, "line_id": "50806457008443",
               "position": 1, "qty": 1, "length_in": 10.257, "width_in": 22.1,
               "low_res": False, "fetched_at": "2026-10-15T09:21:56"}
        s = Sheet.from_ledger("50806457008443", rec)
        self.assertEqual((s.order_num, s.length_in, s.ready_at),
                         (47918, 10.257, datetime(2026, 10, 15, 9, 21, 56)))

    def test_limits_from_machine_config(self):
        cfg = {"cap_in": 650, "target_in": 600, "window_in": 48, "gap_in": 0.5,
               "flush_after_min": 30}
        lim = Limits.from_config(cfg)
        self.assertEqual((lim.fill_max, lim.full_min, lim.max_width_in), (648, 552, None))


if __name__ == "__main__":
    unittest.main()
