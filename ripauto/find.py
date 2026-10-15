"""
find.py  -  where did an order go, on this PC? (the dashboard searches every machine;
this goes deeper on one: file names, hold reasons, the full log lines).

  python -m ripauto.find 47946          everything this PC knows about #47946
  python -m ripauto.find 4794           every order starting with 4794

Looks in the ledger (state/processed.json), the run records (state/runs/) and
the logs (state/agent*.log, state/rip_log.txt). Read-only. Output stays on
this PC, so it shows file names in full.
"""

import json
import re
import sys
from pathlib import Path

from . import ledger as L
from .config import STATE_DIR

RUNS_DIR = STATE_DIR / "runs"


def ledger_hits(ledger, digits):
    out = []
    for key, rec in ledger.items():
        num = str(rec.get("order_num") or re.sub(r"\D", "", str(rec.get("order", ""))))
        if num.startswith(digits):
            out.append((key, rec))
    out.sort(key=lambda kr: (kr[1].get("order_num") or 0, kr[1].get("position") or 0))
    return out


def run_hits(runs_dir, digits):
    out = []
    for p in sorted(Path(runs_dir).glob("*.json")) if Path(runs_dir).exists() else []:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if any(re.sub(r"\D", "", str(o)).startswith(digits) for o in rec.get("orders", [])):
            out.append(rec)
    return out


def log_hits(state_dir, digits, limit=60):
    pattern = re.compile(rf"(?<!\d)#?{digits}\d*(?!\d)")
    files = sorted(Path(state_dir).glob("agent*.log")) + [Path(state_dir) / "rip_log.txt"]
    out = []
    for f in files:
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if pattern.search(line):
                    out.append(f"{f.name}: {line}")
        except OSError:
            continue
    return out[-limit:]


def report(digits, ledger, runs_dir=RUNS_DIR, state_dir=STATE_DIR, out=print):
    hits = ledger_hits(ledger, digits)
    runs = run_hits(runs_dir, digits)
    logs = log_hits(state_dir, digits)
    if not (hits or runs or logs):
        out(f"Nothing on this PC for an order starting with {digits}.")
        return False
    if hits:
        out("Ledger:")
        for key, rec in hits:
            st = L.status(rec)
            extra = rec.get("hold_reason") or rec.get("by_hand") or ""
            out(f"  {rec.get('order')} sheet {rec.get('position', '?')}  {st:9}"
                f"  copies {rec.get('copies_ripped', 0)}/{rec.get('qty', '?')}"
                f"  {rec.get('length_in') or '?'} in  fetched {rec.get('fetched_at') or rec.get('downloaded_at', '?')}"
                f"  runs {', '.join(rec.get('runs', [])) or '-'}")
            if extra:
                out(f"      {extra}")
            if rec.get("file"):
                out(f"      file: {rec['file']}")
    if runs:
        out("Runs:")
        for r in runs:
            out(f"  {r.get('id')}  {r.get('outcome')}  {r.get('ended') or r.get('started')}"
                f"  planned {r.get('planned_in')} in, actual {r.get('actual_in')} in"
                f"  orders {' '.join(r.get('orders', []))}"
                + ("  (dry run)" if r.get("dry_run") else ""))
    if logs:
        out(f"Log lines ({len(logs)}):")
        for line in logs:
            out("  " + line)
    return True


def main():
    if len(sys.argv) != 2 or len(re.sub(r"\D", "", sys.argv[1])) < 3:
        sys.exit("Usage: python -m ripauto.find <order number, at least 3 digits>")
    report(re.sub(r"\D", "", sys.argv[1]), L.load())


if __name__ == "__main__":
    main()
