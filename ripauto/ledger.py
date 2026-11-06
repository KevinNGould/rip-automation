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
import time
from datetime import datetime

from .config import STATE_DIR
from .planner import Sheet

LEDGER_PATH = STATE_DIR / "processed.json"

QUEUED, IN_RUN, RIPPED, HELD, BY_HAND = "queued", "in_run", "ripped", "held", "by_hand"


def load(path=LEDGER_PATH):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save(ledger, path=LEDGER_PATH, tries=40, wait_s=0.05):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    # Windows refuses the replace while another program has the file open.
    # The RIP Agent window reads it every few seconds; one clash at the start
    # of a run (2026-11-06 00:40) left 4 sheets "in run" and so held.
    for attempt in range(tries):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(wait_s)


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


def keys_for_orders(ledger, orders, statuses):
    """Sheet keys of these order numbers ("#48942" or "48942") in these statuses."""
    want = {str(o).strip().lstrip("#") for o in orders}
    return [k for k, rec in ledger.items()
            if str(rec.get("order", "")).lstrip("#") in want and status(rec) in statuses]


def requeue_keys(ledger, keys, now=None):
    """Held sheets go back to queued (a person checked them). Returns the keys
    changed; anything not held is left alone."""
    changed = []
    for key in keys:
        rec = ledger.get(key)
        if rec is None or status(rec) != HELD:
            continue
        rec.pop("hold_reason", None)
        rec.pop("run", None)
        rec.pop("run_copies", None)
        rec.update(status=QUEUED, updated_at=_stamp(now))
        changed.append(key)
    return changed


def requeue(ledger, orders, now=None):
    """Held sheets of these order numbers go back to queued."""
    return requeue_keys(ledger, keys_for_orders(ledger, orders, (HELD,)), now)


def done_by_hand(ledger, keys, detail, now=None):
    """Held or queued sheets a person ripped (or will rip) themself: by_hand,
    never planned again. Returns the keys changed; in-run, ripped and
    already-by-hand sheets are left alone."""
    changed = []
    for key in keys:
        rec = ledger.get(key)
        if rec is None or status(rec) not in (HELD, QUEUED):
            continue
        rec.pop("run_copies", None)
        mark_by_hand(ledger, key, detail, now)
        changed.append(key)
    return changed


# ---------------- requests from a person ----------------
# The running agent keeps the ledger in memory, so the RIP Agent window and
# the command line can't edit processed.json under it. They drop a request
# here instead; the agent applies it between cycles (within ~20 s, paused or
# not). With the agent off, apply_now() does the same straight away.

REQUESTS_PATH = STATE_DIR / "ledger_requests.json"
ACTIONS = ("by_hand", "requeue")


def request(action, keys, who="RIP Agent window", path=REQUESTS_PATH):
    """Queue a change for the agent: by_hand or requeue these sheet keys."""
    if action not in ACTIONS:
        raise ValueError(f"unknown ledger action {action!r}")
    items = []
    if path.exists():
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            items = []
    items.append({"action": action, "keys": list(keys), "who": who, "at": _stamp(None)})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
    tmp.replace(path)


def apply_requests(ledger, now=None, log=print, path=REQUESTS_PATH):
    """Apply and remove waiting requests. Returns how many sheets changed.
    Applying one twice is harmless: each change checks the sheet's status."""
    if not path.exists():
        return 0
    grabbed = path.with_suffix(".applying")
    try:
        path.replace(grabbed)              # anything written after this waits for the next pass
        items = json.loads(grabbed.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log(f"Couldn't read a ledger request ({type(e).__name__}); skipped.")
        try:
            grabbed.unlink()
        except OSError:
            pass
        return 0
    changed = 0
    for item in items if isinstance(items, list) else []:
        keys, who = item.get("keys") or [], item.get("who") or "a person"
        if item.get("action") == "by_hand":
            done = done_by_hand(ledger, keys, f"marked done by hand ({who})", now)
            verb = "marked done by hand"
        elif item.get("action") == "requeue":
            done = requeue_keys(ledger, keys, now)
            verb = "re-queued"
        else:
            continue
        if done:
            orders = " ".join(dict.fromkeys(ledger[k].get("order", "?") for k in done))
            log(f"{who}: {verb} {len(done)} sheet(s): {orders}")
        changed += len(done)
    try:
        grabbed.unlink()
    except OSError:
        pass
    return changed


def apply_now(action, keys, who, log=print, path=REQUESTS_PATH, ledger_path=LEDGER_PATH):
    """With the agent off: request + apply to the ledger file at once."""
    request(action, keys, who, path)
    ledger = load(ledger_path)
    changed = apply_requests(ledger, log=log, path=path)
    if changed:
        save(ledger, ledger_path)
    return changed


def main(argv=None):
    import argparse
    from .lock import agent_running
    ap = argparse.ArgumentParser(description="Ledger fixes a person asks for. Safe while the agent runs: "
                                             "it applies them within about 20 seconds.")
    ap.add_argument("--requeue", nargs="+", metavar="ORDER",
                    help="put these orders' held sheets back in the queue")
    ap.add_argument("--by-hand", nargs="+", metavar="ORDER",
                    help="mark these orders' held or queued sheets done by hand (never ripped by the agent)")
    args = ap.parse_args(argv)
    if not (args.requeue or args.by_hand):
        ap.print_help()
        return
    action, orders = ("requeue", args.requeue) if args.requeue else ("by_hand", args.by_hand)
    ledger = load()
    keys = keys_for_orders(ledger, orders, (HELD,) if action == "requeue" else (HELD, QUEUED))
    if not keys:
        print("No " + ("held" if action == "requeue" else "held or queued") + " sheets for those orders.")
        return
    names = " ".join(dict.fromkeys(ledger[k]["order"] for k in keys))
    if agent_running():
        request(action, keys, who="command line")
        print(f"Sent to the agent ({len(keys)} sheet(s): {names}); it applies it within about 20 seconds.")
    else:
        apply_now(action, keys, who="command line")


if __name__ == "__main__":
    main()
