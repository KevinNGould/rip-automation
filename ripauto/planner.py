"""
planner.py  -  group waiting sheets into runs (SPEC §6).

plan(sheets, now, limits) is a pure function: no files, no clock, no PO-TRY.
It returns every run it would make from the current queue, each marked ready
or waiting, plus the sheets it refuses to rip (holds).

Rules, in SPEC order:
  1. Each copy of a qty-N sheet is its own picture, and a picture is never
     split. A sheet's copies stay in one run when together they fit under
     cap_in; otherwise they go copy by copy, so they land in consecutive runs.
  2. An order's sheets stay together when they fit under cap_in; an order too
     long for one run goes sheet by sheet, the same way.
  3. Oldest first, then first-fit: a unit joins the current run only if the
     run's planned length stays <= target_in + window_in. A unit alone in a
     run may go up to cap_in.
  4. A run is ready when full (>= target_in - window_in) or when its oldest
     sheet has waited flush_after_min.
  5. A sheet whose single copy exceeds cap_in is held, never ripped.
  6. Strictly first come, first served: order number, then position.
     Low-res sheets rip normally; the flag rides along on the Sheet.

Planned length = sum(length x copies) + gap_in x (pictures - 1). The cap
applies to planned length, gaps included.
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
    qty: int = 1                  # copies still to rip
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
class RunItem:
    sheet: Sheet
    copies: int                   # how many of the sheet's copies go in this run


@dataclass
class Run:
    items: List[RunItem]
    planned_in: float
    pictures: int
    oldest_wait_min: float
    ready: bool
    reason: str                   # "full" | "flush" | "waiting"

    @property
    def sheets(self):
        return [i.sheet for i in self.items]

    @property
    def orders(self):
        return list(dict.fromkeys(i.sheet.order for i in self.items))


@dataclass
class Hold:
    sheet: Sheet
    reason: str


@dataclass(eq=False)          # compared by identity when removing taken units
class _Unit:
    items: List[RunItem] = field(default_factory=list)

    @property
    def inches(self):
        return sum(i.sheet.length_in * i.copies for i in self.items)

    @property
    def pictures(self):
        return sum(i.copies for i in self.items)


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
    if s.length_in > lim.cap_in + EPS:
        return f"over {lim.cap_in:g} in"
    return None


def _fits_alone(unit, lim):
    return planned_length(unit.inches, unit.pictures, lim.gap_in) <= lim.cap_in + EPS


def _units(sheets, lim):
    """Whole orders where they fit under the cap, else whole sheets, else
    single copies. Keeps queue order, so split work lands in consecutive runs."""
    by_order = {}
    for s in sheets:
        by_order.setdefault(s.order, []).append(s)
    units = []
    for order_sheets in by_order.values():
        whole = _Unit([RunItem(s, s.qty) for s in order_sheets])
        if _fits_alone(whole, lim):
            units.append(whole)
            continue
        for s in order_sheets:
            sheet_unit = _Unit([RunItem(s, s.qty)])
            if _fits_alone(sheet_unit, lim):
                units.append(sheet_unit)
            else:
                units.extend(_Unit([RunItem(s, 1)]) for _ in range(s.qty))
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
        runs.append(_finish(taken, inches, pictures, now, limits))
    return runs, holds


def _finish(units, inches, pictures, now, lim):
    merged = {}                   # one item per sheet, copies added up
    for u in units:
        for i in u.items:
            if i.sheet.key in merged:
                merged[i.sheet.key].copies += i.copies
            else:
                merged[i.sheet.key] = RunItem(i.sheet, i.copies)
    items = list(merged.values())
    planned = planned_length(inches, pictures, lim.gap_in)
    wait = max((now - i.sheet.ready_at).total_seconds() / 60 for i in items)
    if planned >= lim.full_min - EPS:
        ready, reason = True, "full"
    elif wait >= lim.flush_after_min:
        ready, reason = True, "flush"
    else:
        ready, reason = False, "waiting"
    return Run(items, round(planned, 3), pictures, round(wait, 1), ready, reason)
