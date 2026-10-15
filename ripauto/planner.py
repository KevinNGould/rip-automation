"""
planner.py  -  group waiting sheets into runs (SPEC §6).

plan(sheets, now, limits) is a pure function: no files, no clock, no PO-TRY.
It returns every run it would make from the current queue, each marked ready
or waiting, plus the sheets it refuses to rip (holds).

Rules, in SPEC order:
  1. A sheet is never split: all N copies of a qty-N sheet go in one run.
  2. An order's sheets stay together when they fit in one run; an order too
     long for one run goes sheet by sheet, so it lands in consecutive runs.
  3. Oldest first, then first-fit: a unit joins the current run only if the
     run's planned length stays <= target_in + window_in. A unit alone in a
     run may go up to cap_in.
  4. A run is ready when full (>= target_in - window_in) or when its oldest
     sheet has waited flush_after_min.
  5. A sheet whose copies together exceed cap_in is held, never ripped.

Planned length = sum(length x qty) + gap_in x (pictures - 1), where every
copy is one picture. The cap applies to planned length, gaps included.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

EPS = 1e-6   # float noise only; the cap is otherwise exact


@dataclass(frozen=True)
class Limits:
    cap_in: float
    target_in: float
    window_in: float
    gap_in: float = 0.5
    flush_after_min: float = 30
    max_width_in: Optional[float] = None    # None = width not checked

    @property
    def fill_max(self):
        return self.target_in + self.window_in

    @property
    def full_min(self):
        return self.target_in - self.window_in

    @classmethod
    def from_config(cls, cfg):
        return cls(cap_in=cfg["cap_in"], target_in=cfg["target_in"],
                   window_in=cfg["window_in"], gap_in=cfg.get("gap_in", 0.5),
                   flush_after_min=cfg.get("flush_after_min", 30),
                   max_width_in=cfg.get("max_width_in"))


@dataclass(frozen=True)
class Sheet:
    key: str                      # Shopify line item ID
    order: str                    # "#47918"
    order_num: int
    length_in: Optional[float]
    ready_at: datetime            # when it landed in the queue (fetched_at)
    qty: int = 1
    position: int = 1
    width_in: Optional[float] = None
    low_res: bool = False

    @classmethod
    def from_ledger(cls, key, rec):
        return cls(key=key, order=rec["order"], order_num=rec["order_num"],
                   length_in=rec.get("length_in"),
                   ready_at=datetime.fromisoformat(rec["fetched_at"]),
                   qty=rec.get("qty", 1), position=rec.get("position", 1),
                   width_in=rec.get("width_in"), low_res=rec.get("low_res", False))


@dataclass
class Run:
    sheets: List[Sheet]
    planned_in: float
    pictures: int
    oldest_wait_min: float
    ready: bool
    reason: str                   # "full" | "flush" | "waiting"

    @property
    def orders(self):
        return list(dict.fromkeys(s.order for s in self.sheets))


@dataclass
class Hold:
    sheet: Sheet
    reason: str


@dataclass(eq=False)          # compared by identity when removing taken units
class _Unit:
    sheets: List[Sheet] = field(default_factory=list)

    @property
    def inches(self):
        return sum(s.length_in * s.qty for s in self.sheets)

    @property
    def pictures(self):
        return sum(s.qty for s in self.sheets)


def planned_length(inches, pictures, gap_in):
    return inches + gap_in * max(pictures - 1, 0)


def _hold_reason(s, lim):
    if s.length_in is None or s.length_in <= 0:
        return "length unknown"
    if s.qty < 1:
        return f"quantity {s.qty}"
    if lim.max_width_in is not None and s.width_in is not None \
            and s.width_in > lim.max_width_in + EPS:
        return f"wider than {lim.max_width_in:g} in ({s.width_in:.2f} in)"
    total = planned_length(s.length_in * s.qty, s.qty, lim.gap_in)
    if total > lim.cap_in + EPS:
        if s.qty > 1 and s.length_in <= lim.cap_in + EPS:
            return (f"over {lim.cap_in:g} in: {s.qty} copies x {s.length_in:.1f} in "
                    f"= {total:.1f} in (one copy fits; needs a decision)")
        return f"over {lim.cap_in:g} in"
    return None


def _units(sheets, lim):
    """Order groups, or single sheets when an order can't fit in one run."""
    by_order = {}
    for s in sheets:
        by_order.setdefault(s.order, _Unit()).sheets.append(s)
    units = []
    for u in by_order.values():
        if planned_length(u.inches, u.pictures, lim.gap_in) <= lim.cap_in + EPS:
            units.append(u)
        else:
            units.extend(_Unit([s]) for s in u.sheets)
    return units


def plan(sheets, now, limits) -> Tuple[List[Run], List[Hold]]:
    holds, ok = [], []
    for s in sheets:
        reason = _hold_reason(s, limits)
        if reason:
            holds.append(Hold(s, reason))
        else:
            ok.append(s)
    ok.sort(key=lambda s: (s.order_num, s.position, s.key))

    remaining = _units(ok, limits)
    runs = []
    while remaining:
        taken, inches, pictures = [], 0.0, 0
        for u in remaining:
            new_len = planned_length(inches + u.inches, pictures + u.pictures, limits.gap_in)
            limit = limits.cap_in if not taken else limits.fill_max
            if new_len <= limit + EPS:
                taken.append(u)
                inches += u.inches
                pictures += u.pictures
        remaining = [u for u in remaining if u not in taken]
        run_sheets = [s for u in taken for s in u.sheets]
        runs.append(_finish(run_sheets, inches, pictures, now, limits))
    return runs, holds


def _finish(sheets, inches, pictures, now, lim):
    planned = planned_length(inches, pictures, lim.gap_in)
    wait = max((now - s.ready_at).total_seconds() / 60 for s in sheets)
    if planned >= lim.full_min - EPS:
        ready, reason = True, "full"
    elif wait >= lim.flush_after_min:
        ready, reason = True, "flush"
    else:
        ready, reason = False, "waiting"
    return Run(sheets, round(planned, 3), pictures, round(wait, 1), ready, reason)
