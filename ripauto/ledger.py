"""
ledger.py  -  per-sheet record of what the agent has done (state/processed.json).

Keyed by Shopify line item ID. A sheet moves through:

  queued   downloaded, waiting to be planned into a run
  in_run   loaded into PO-TRY as part of a run that hasn't finished yet
  ripped   every copy ripped                         (final)
  held     won't be ripped without a person          (final until someone clears it)
  by_hand  a person downloaded or ripped it by hand  (final, SPEC §4)

A sheet whose copies were split across runs goes back to queued after each
run until copies_ripped reaches qty. Entries written by the trial or by
fetch --mark-done carry no status and are never planned.

Everything here edits the ledger dict in place; load() and save() are the
only file access.
"""

import json
from datetime import datetime

from .config import STATE_DIR
from .planner import Sheet

LEDGER_PATH = STATE_DIR / "processed.json"

QUEUED, IN_RUN, RIPPED, HELD, BY_HAND = "queued", "in_run", "ripped", "held", "by_hand"


def load(path=LEDGER_PATH):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save(ledger, path=LEDGER_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    tmp.replace(path)


def _stamp(now):
    return (now or datetime.now()).isoformat(timespec="seconds")


def status(rec):
    return rec.get("status") or "untracked"


def copies_left(rec):
    return rec.get("qty", 1) - rec.get("copies_ripped", 0)


def queued_sheets(ledger):
    """Planner input: queued sheets with copies still to rip (Sheet.qty = copies left)."""
    out = []
    for key, rec in ledger.items():
        if status(rec) == QUEUED and copies_left(rec) > 0:
            out.append(Sheet.from_ledger(key, dict(rec, qty=copies_left(rec))))
    return out


def in_run_keys(ledger):
    """Sheets left in_run, e.g. after a crash mid-run. Nobody knows whether
    PO-TRY ripped them, so the agent holds them for a person to check."""
    return [k for k, rec in ledger.items() if status(rec) == IN_RUN]


def start_run(ledger, run_id, items, now=None):
    for item in items:
        rec = ledger[item.sheet.key]
        rec.update(status=IN_RUN, run=run_id, run_copies=item.copies, updated_at=_stamp(now))


def finish_run(ledger, run_id, items, now=None):
    for item in items:
        rec = ledger[item.sheet.key]
        rec["copies_ripped"] = rec.get("copies_ripped", 0) + item.copies
        rec.setdefault("runs", []).append(run_id)
        rec.pop("run", None)
        rec.pop("run_copies", None)
        rec["status"] = RIPPED if copies_left(rec) <= 0 else QUEUED
        rec["updated_at"] = _stamp(now)


def unstart_run(ledger, keys, now=None):
    """A run that never got as far as Print (a person used the PC): its sheets
    go back to queued as if it hadn't started."""
    for key in keys:
        rec = ledger[key]
        rec.pop("run", None)
        rec.pop("run_copies", None)
        rec.update(status=QUEUED, updated_at=_stamp(now))


def hold(ledger, keys, reason, now=None):
    for key in keys:
        rec = ledger[key]
        rec.pop("run_copies", None)
        rec.update(status=HELD, hold_reason=reason, updated_at=_stamp(now))


def mark_by_hand(ledger, key, detail, now=None):
    ledger[key].update(status=BY_HAND, by_hand=detail, updated_at=_stamp(now))


def requeue(ledger, orders, now=None):
    """Held sheets of these order numbers go back to queued (a person checked
    them). Returns the keys changed; ripped, by-hand and queued sheets are left alone."""
    want = {str(o).lstrip("#") for o in orders}
    changed = []
    for key, rec in ledger.items():
        if str(rec.get("order", "")).lstrip("#") in want and status(rec) == HELD:
            rec.pop("hold_reason", None)
            rec.pop("run", None)
            rec.pop("run_copies", None)
            rec.update(status=QUEUED, updated_at=_stamp(now))
            changed.append(key)
    return changed


def main(argv=None):
    import argparse
    from .lock import agent_running
    ap = argparse.ArgumentParser(description="Ledger fixes a person asks for.")
    ap.add_argument("--requeue", nargs="+", metavar="ORDER",
                    help="put these orders' held sheets back in the queue")
    args = ap.parse_args(argv)
    if args.requeue:
        if agent_running():
            raise SystemExit("The agent is running and keeps its own copy of the ledger. "
                             "Stop it first (RIP Agent window, or Restart agent), then run this.")
        ledger = load()
        keys = requeue(ledger, args.requeue)
        save(ledger)
        print(f"Re-queued {len(keys)} sheet(s): " + ", ".join(ledger[k]["order"] for k in keys)
              if keys else "No held sheets for those orders.")


if __name__ == "__main__":
    main()
