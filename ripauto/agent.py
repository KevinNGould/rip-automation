"""
agent.py  -  one cycle of plan -> hand check -> rip, keeping the ledger (SPEC §6, §7, §4).

Step 4 provides a single cycle (--once). The loop, pause switch and Task
Scheduler setup are step 6.

One cycle:
  1. Sheets left in_run by an interrupted cycle are held for a person.
  2. Hand-download check on every queued sheet (PO-TRY queue + Downloads).
  3. Plan the queue; planner holds go into the ledger.
  4. For each ready run (up to --max-runs): check its sheets by hand again,
     then load, gate and rip it. Fully ripped files move to ripped\\{run-id}\\
     and a run record goes to state/runs/{run-id}.json.
  Gate refusals and anything unexpected hold the run's sheets and end the
  cycle; nothing retries automatically.

Run from an ADMINISTRATOR Command Prompt in the repo root:
  python -m ripauto.agent --plan                 show the plan; no PO-TRY, no changes
  python -m ripauto.agent --once --dry-run       everything, Cancel at the preview,
                                                 ledger left untouched
  python -m ripauto.agent --once --force --dry-run   same, using the first run even
                                                 if it isn't full or due yet
  python -m ripauto.agent --once                 for real (Kevin's go-ahead first)
"""

import argparse
import copy
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import ledger as L
from .config import STATE_DIR, load_dashboard_key, load_machine, load_or_exit
from .handcheck import BY_HAND, FREE, download_names, hand_check, queue_job_names
from .planner import Limits, plan
from .potry import RIPPING_STATUSES, parse_size_cell
from .status import NullReporter, Reporter

RUNS_DIR = STATE_DIR / "runs"


class Stop(Exception):
    pass


# ---------------- run records ----------------

def own_job_names(runs_dir):
    """Job names of runs the agent itself put in PO-TRY's queue."""
    names = []
    for p in sorted(runs_dir.glob("*.json")) if runs_dir.exists() else []:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("job_name") and not rec.get("dry_run"):
            names.append(rec["job_name"])
    return names


def write_record(runs_dir, record):
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{record['id']}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")


# ---------------- the cycle ----------------

def apply_hand_check(ledger, keys, queue_names, dl_names, now, log):
    """Check these ledger keys; mark by_hand / hold manual. Returns keys that aren't free."""
    taken = []
    for key in keys:
        rec = ledger[key]
        verdict, detail = hand_check(rec["order_num"], rec.get("position", 1), queue_names, dl_names)
        if verdict == FREE:
            continue
        taken.append(key)
        if verdict == BY_HAND:
            L.mark_by_hand(ledger, key, detail, now)
            log(f"  {rec['order']} sheet {rec.get('position', 1)}: taken by hand ({detail})")
        else:
            L.hold(ledger, [key], f"manual check: {detail}", now)
            log(f"  {rec['order']} sheet {rec.get('position', 1)}: held, manual check ({detail})")
    return taken


def run_pictures(ledger, run):
    """Pictures for a run (one path per copy), or raise Stop if a file is missing."""
    pictures = []
    for item in run.items:
        path = Path(ledger[item.sheet.key].get("file") or "")
        if not path.is_file():
            raise Stop(f"{item.sheet.order} file is missing: {path.name or '(none)'}")
        pictures += [path] * item.copies
    return pictures


def measured(run, result):
    """Actual length from the queue row (else the preview), and the gap per
    picture it implies, for calibrating gap_in."""
    size = parse_size_cell(((result or {}).get("row") or {}).get("Size (inch)")) \
        or (result or {}).get("preview")
    if not size:
        return None, None
    artwork = sum(i.sheet.length_in * i.copies for i in run.items)
    gap = (size[1] - artwork) / (run.pictures - 1) if run.pictures > 1 else None
    return size[1], (round(gap, 3) if gap is not None else None)


def record_for(run_id, cfg, run, pictures, result, outcome, dry_run):
    actual_in, gap_in = measured(run, result)
    return {
        "id": run_id,
        "machine_id": cfg["machine_id"],
        "dry_run": dry_run,
        "outcome": outcome,                  # ripped | gate | stopped | dry_run
        "job_name": (result or {}).get("job_name"),
        "orders": run.orders,
        "items": [{"order": i.sheet.order, "line_id": i.sheet.key,
                   "position": i.sheet.position, "copies": i.copies} for i in run.items],
        "files": [p.name for p in dict.fromkeys(pictures)],
        "pictures": run.pictures,
        "planned_in": run.planned_in,
        "preview": (result or {}).get("preview"),
        "queue_size": ((result or {}).get("row") or {}).get("Size (inch)"),
        "actual_in": actual_in,
        "gap_measured_in": gap_in,
        "gap_planned_in": cfg.get("gap_in"),
        "gate": (result or {}).get("gate"),
        "started": (result or {}).get("started"),
        "ended": (result or {}).get("ended"),
    }


def move_ripped(ledger, run, run_id, queue_dir):
    """Files whose every copy is now ripped go to ripped\\{run-id}\\."""
    dest = Path(queue_dir) / "ripped" / run_id
    for item in run.items:
        rec = ledger[item.sheet.key]
        if L.status(rec) != L.RIPPED:
            continue                         # copies still to go; file stays queued
        src = Path(rec["file"])
        if src.is_file():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest / src.name))
            rec["file"] = str(dest / src.name)


def ripping_now(rows):
    """True if PO-TRY's queue shows a job still ripping (someone else's, or ours)."""
    return any((r.get("Status") or "").strip() in RIPPING_STATUSES for r in rows or [])


def plan_and_hold(ledger, now, limits, save, log, quiet=False):
    """Plan the queue; planner holds go straight into the ledger."""
    runs, holds = plan(L.queued_sheets(ledger), now, limits)
    for h in holds:
        L.hold(ledger, [h.sheet.key], h.reason, now)
        log(f"  {h.sheet.order} sheet {h.sheet.position}: held, {h.reason}")
    if holds:
        save()
    if not quiet:
        for r in runs:
            log(f"Planned {r.planned_in:6.1f} in, {r.pictures} picture(s), {r.reason:7} "
                f"{' '.join(r.orders)}")
    return runs


def pick(runs, force, log):
    chosen = [r for r in runs if r.ready]
    if not chosen and force and runs:
        log("Nothing ready; --force takes the first run anyway.")
        return runs[:1]
    return chosen


def cycle(cfg, ledger, driver, now, *, dl_names_fn, runs_dir=RUNS_DIR, save=lambda: None,
          dry_run=False, force=False, max_runs=1, log=print, reporter=None,
          can_start=lambda: True, quiet=False):
    """One pass. driver needs .queue_rows() and .rip(pictures, planned_in, dry_run,
    on_progress). reporter (status.Reporter) gets every state change for the
    Pi dashboard. On a dry run the caller's ledger is left untouched.

    PO-TRY is only touched when a run is ready to rip, nobody is using the PC
    (can_start), and PO-TRY isn't already ripping. People print from this PC,
    so the agent stays out of PO-TRY otherwise. Returns a summary dict."""
    reporter = reporter or NullReporter()
    if dry_run:
        ledger = copy.deepcopy(ledger)
        save = lambda: None
    limits = Limits.from_config(cfg)
    summary = {"ripped": [], "held": [], "by_hand": [], "dry_runs": [], "stopped": None,
               "waiting": None}

    def stopped(message, state="error"):
        summary["stopped"] = message
        reporter.update(state=state, current_run=None, last_error=message)

    def done(waiting=None):
        summary["waiting"] = waiting
        if not summary["stopped"]:
            reporter.update(state="idle", current_run=None)
        summary["held"] = [k for k, r in ledger.items() if L.status(r) == L.HELD]
        summary["by_hand"] = [k for k, r in ledger.items() if L.status(r) == L.BY_HAND]
        return summary

    stuck = L.in_run_keys(ledger)
    if stuck:
        L.hold(ledger, stuck, "manual check: a run was interrupted; check PO-TRY's queue", now)
        log(f"Held {len(stuck)} sheet(s) left mid-run by an earlier cycle.")
        save()

    if not L.queued_sheets(ledger):
        return done("nothing queued")
    chosen = pick(plan_and_hold(ledger, now, limits, save, log, quiet), force, log)
    if not chosen:
        return done("no run ready yet")
    if not can_start():
        return done("someone is using this PC")

    reporter.update(state="planning")
    rows = driver.queue_rows()
    if ripping_now(rows):
        log("PO-TRY is still ripping a job; waiting.")
        return done("PO-TRY is busy ripping")
    own = own_job_names(runs_dir)
    queued = [s.key for s in L.queued_sheets(ledger)]
    if apply_hand_check(ledger, queued, queue_job_names(rows, own), dl_names_fn(), now, log):
        save()
        chosen = pick(plan_and_hold(ledger, now, limits, save, log), force, log)

    for n, run in enumerate(chosen[:max_runs], 1):
        keys = [i.sheet.key for i in run.items]
        if apply_hand_check(ledger, keys, queue_job_names(driver.queue_rows(), own),
                            dl_names_fn(), now, log):
            save()
            log("A sheet in this run was taken by hand just now; re-planning next cycle.")
            break
        run_id = f"{cfg['machine_id']}-{now:%Y%m%d-%H%M%S}" + (f"-{n}" if n > 1 else "")
        try:
            pictures = run_pictures(ledger, run)
        except Stop as e:
            L.hold(ledger, keys, f"manual check: {e}", now)
            save()
            stopped(str(e))
            log(f"STOPPED before loading: {e}")
            break

        reporter.update(state="ripping", current_run={
            "id": run_id + ("-dry" if dry_run else ""), "orders": run.orders,
            "planned_in": run.planned_in, "progress_pct": 0})
        progress = lambda pct: reporter.update(progress_pct=pct)
        if dry_run:
            result = driver.rip(pictures, run.planned_in, dry_run=True, on_progress=progress)
            write_record(runs_dir, record_for(run_id + "-dry", cfg, run, pictures, result,
                                              "dry_run", True))
            summary["dry_runs"].append(run_id)
            reporter.update(current_run=None)
            continue

        L.start_run(ledger, run_id, run.items, now)
        save()
        try:
            result = driver.rip(pictures, run.planned_in, dry_run=False, on_progress=progress)
        except Exception as e:               # Stop from the driver, or anything else
            L.hold(ledger, keys, f"manual check: run {run_id} stopped: {e}", now)
            write_record(runs_dir, record_for(run_id, cfg, run, pictures, None, "stopped", False))
            save()
            stopped(str(e))
            log(f"STOPPED during run {run_id}: {e}")
            break
        if result["gate"]:
            L.hold(ledger, keys, f"length gate: {result['gate']}", now)
            write_record(runs_dir, record_for(run_id, cfg, run, pictures, result, "gate", False))
            save()
            stopped(result["gate"], state="holding")
            log(f"Run {run_id} cancelled at the preview: {result['gate']}")
            break
        L.finish_run(ledger, run_id, run.items, now)
        move_ripped(ledger, run, run_id, cfg["queue_dir"])
        queue_size = parse_size_cell((result.get("row") or {}).get("Size (inch)"))
        if queue_size and queue_size[1] > cfg["cap_in"]:
            # Backstop: the preview gate passed but the ripped job is too long.
            # The agent never touches the queue toolbar; a person deletes it.
            problem = (f"ripped job {result['job_name']} is {queue_size[1]:.1f} in, over "
                       f"{cfg['cap_in']:g} in: delete it from the RIP queue by hand, don't print it")
            L.hold(ledger, keys, problem, now)
            write_record(runs_dir, record_for(run_id, cfg, run, pictures, result, "over_cap", False))
            save()
            stopped(problem)
            log(f"Run {run_id}: {problem}")
            break
        write_record(runs_dir, record_for(run_id, cfg, run, pictures, result, "ripped", False))
        save()
        summary["ripped"].append(run_id)
        reporter.update(current_run=None, last_error=None)
        log(f"Run {run_id} ripped: {' '.join(run.orders)}")

    return done()


# ---------------- PO-TRY side ----------------

class PotryDriver:
    """The real PO-TRY, through ripauto.potry. Needs an elevated prompt."""

    def __init__(self, cfg):
        from . import potry
        self.potry, self.cfg = potry, cfg
        potry.configure(cfg)
        if potry.Application is None:
            sys.exit("pywinauto is missing. Run:  pip install pywinauto")
        if not potry.is_admin():
            sys.exit("Run this from an administrator Command Prompt. PO-TRY runs as admin.")
        self.main, self.pid = potry.connect()

    def queue_rows(self):
        self.potry.show_queue(self.main)
        self.potry.time.sleep(self.potry.SETTLE)
        return self.potry.read_queue(self.main)

    def rip(self, pictures, planned_in, dry_run, on_progress=None):
        try:
            return self.potry.rip_pictures(self.main, self.pid, pictures, self.cfg,
                                           planned_in, dry_run,
                                           multi_add=self.cfg.get("multi_add", True),
                                           on_progress=on_progress)
        except self.potry.Stop as e:
            raise Stop(str(e)) from None


def main():
    ap = argparse.ArgumentParser(description="One plan -> hand check -> rip cycle.")
    ap.add_argument("--once", action="store_true", help="run one cycle against PO-TRY")
    ap.add_argument("--plan", action="store_true", help="show the plan only; no PO-TRY, no changes")
    ap.add_argument("--dry-run", action="store_true", help="Cancel at the preview; ledger untouched")
    ap.add_argument("--force", action="store_true", help="use the first run even if not ready")
    ap.add_argument("--max-runs", type=int, default=1, help="runs to rip this cycle (default 1)")
    args = ap.parse_args()
    if args.once == args.plan:
        sys.exit("Pick one: --plan or --once")

    cfg = load_or_exit(load_machine)
    ledger = L.load()
    now = datetime.now()

    if args.plan:
        runs, holds = plan(L.queued_sheets(ledger), now, Limits.from_config(cfg))
        print(f"{len(L.queued_sheets(ledger))} queued sheet(s); hand check not run (needs PO-TRY).")
        for r in runs:
            print(f"  {r.planned_in:6.1f} in  {r.pictures:2} picture(s)  {r.reason:7}  "
                  f"waited {r.oldest_wait_min:.0f} min  {' '.join(r.orders)}")
        for h in holds:
            print(f"  HOLD {h.sheet.order} sheet {h.sheet.position}: {h.reason}")
        return

    from .potry import log
    driver = PotryDriver(cfg)
    hand_dir = Path(cfg["hand_download_dir"]) if cfg.get("hand_download_dir") else None
    if hand_dir is None:
        sys.exit("Set hand_download_dir in machine.json (SPEC §4).")
    key = load_dashboard_key()
    if cfg.get("dashboard_url") and key:
        reporter = Reporter(cfg, ledger, RUNS_DIR, cfg["dashboard_url"], key, log=log)
    else:
        log("No dashboard_url / dashboard_key set; not sending heartbeats.")
        reporter = NullReporter()
    reporter.start()
    try:
        summary = cycle(cfg, ledger, driver, now, dl_names_fn=lambda: download_names(hand_dir),
                        save=lambda: L.save(ledger), dry_run=args.dry_run, force=args.force,
                        max_runs=args.max_runs, log=log, reporter=reporter)
    finally:
        reporter.stop("stopped")             # --once ends here; the loop (step 6) won't
    log(f"Cycle done: ripped {len(summary['ripped'])}, dry runs {len(summary['dry_runs'])}, "
        f"held total {len(summary['held'])}, by hand total {len(summary['by_hand'])}"
        + (f", stopped: {summary['stopped']}" if summary["stopped"] else ""))
    if summary["stopped"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
