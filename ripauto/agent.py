"""
agent.py  -  the agent: fetch -> plan -> hand check -> rip, keeping the ledger
(SPEC §4, §6, §7, §9).

One cycle:
  1. Sheets left in_run by an interrupted cycle are held for a person.
  2. Plan the queue; planner holds go into the ledger. Nothing ready -> done,
     without touching PO-TRY.
  3. Only if a run is ready, nobody has used this PC for require_idle_s, and
     PO-TRY isn't already ripping: hand-download check (PO-TRY queue +
     Downloads), re-plan, then for each ready run (up to --max-runs) check its
     sheets by hand again, load, gate and rip it. Fully ripped files move to
     ripped\\{run-id}\\ and a run record goes to state/runs/{run-id}.json.
  Gate refusals and anything unexpected hold the run's sheets and end the
  cycle; nothing retries automatically.

The loop (--loop, what Task Scheduler starts at logon): fetch every
fetch_every_min, a cycle every LOOP_EVERY_S, a heartbeat to the Pi on every
change. Pause switch: while state\\PAUSE exists it finishes what it's doing,
then sits idle reporting "stopped" (tools\\pause.cmd / tools\\resume.cmd).
Log: state\\agent.log, rotated daily.

Run from an ADMINISTRATOR Command Prompt in the repo root:
  python -m ripauto.agent --plan                 show the plan; no PO-TRY, no changes
  python -m ripauto.agent --once --dry-run       one cycle, Cancel at the preview,
                                                 ledger left untouched
  python -m ripauto.agent --once --force --dry-run   same, using the first run even
                                                 if it isn't full or due yet
  python -m ripauto.agent --once                 one real cycle
  python -m ripauto.agent --loop                 run until stopped (Task Scheduler)
"""

import argparse
import copy
import json
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import ledger as L
from .config import STATE_DIR, ConfigError, load_dashboard_key, load_machine, load_or_exit, load_secrets
from .handcheck import BY_HAND, FREE, download_names, hand_check, queue_job_names
from .lock import AgentLock, agent_running
from .planner import Limits, plan
from .potry import RIPPING_STATUSES, parse_size_cell
from .status import NullReporter, Reporter
from .warning import RIP_NOW_PATH, Gate, Warner
from .handsoff import HumanInput

RUNS_DIR = STATE_DIR / "runs"
PAUSE_PATH = STATE_DIR / "PAUSE"
LOOP_EVERY_S = 20
COUNTDOWN_TICK_S = 2          # while the rip warning is up, check more often
ERROR_BACKOFF_S = 60
COUNTDOWN = "rip warning countdown"
BUSY = "PO-TRY is busy ripping"
UNREADABLE = "can't read PO-TRY's queue or Downloads"
UNREADABLE_ERROR_AFTER = 3
INTERRUPTED = "interrupted by someone using the PC"
RETRY_AFTER_INTERRUPT_S = 300
RESTART_PATH = STATE_DIR / "RESTART"     # RIP Agent window "Restart agent" (picks up new code)
TASK_NAME = "OSS RIP Agent"


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
          can_start=lambda run: True, on_start=lambda: None, quiet=False):
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
    if not can_start(chosen[0]):
        return done(COUNTDOWN)

    reporter.update(state="planning")
    rows = driver.queue_rows()
    dl_names = dl_names_fn()
    if rows is None or dl_names is None:
        # Machine-level trouble, not a problem with any sheet (CRITIQUE B3):
        # touch nothing this pass; the loop flags an error after 3 in a row.
        what = "PO-TRY's RIP queue" if rows is None else "the Downloads folder"
        log(f"Can't read {what}; skipping this pass.")
        return done(UNREADABLE)
    if ripping_now(rows):
        log("PO-TRY is still ripping a job; waiting.")
        return done(BUSY)
    own = own_job_names(runs_dir)
    queued = [s.key for s in L.queued_sheets(ledger)]
    if apply_hand_check(ledger, queued, queue_job_names(rows, own), dl_names, now, log):
        save()
        chosen = pick(plan_and_hold(ledger, now, limits, save, log), force, log)

    for n, run in enumerate(chosen[:max_runs], 1):
        keys = [i.sheet.key for i in run.items]
        rows, dl_names = driver.queue_rows(), dl_names_fn()
        if rows is None or dl_names is None:
            log("Can't read PO-TRY's RIP queue or Downloads right before the run; skipping this pass.")
            return done(UNREADABLE)
        if apply_hand_check(ledger, keys, queue_job_names(rows, own), dl_names, now, log):
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
            on_start()
            try:
                result = driver.rip(pictures, run.planned_in, dry_run=True, on_progress=progress)
            except HumanInput as e:
                log(f"Dry run interrupted: {e}.")
                reporter.update(state="idle", current_run=None)
                return done(INTERRUPTED)
            write_record(runs_dir, record_for(run_id + "-dry", cfg, run, pictures, result,
                                              "dry_run", True))
            summary["dry_runs"].append(run_id)
            reporter.update(current_run=None)
            continue

        L.start_run(ledger, run_id, run.items, now)
        save()
        try:
            on_start()                      # close the corner warning before taking the mouse
            result = driver.rip(pictures, run.planned_in, dry_run=False, on_progress=progress)
        except HumanInput as e:
            # A person used the PC before Print (CRITIQUE A1). The driver backed
            # out; nothing is wrong with these sheets, so they stay queued.
            L.unstart_run(ledger, keys, now)
            write_record(runs_dir, dict(record_for(run_id, cfg, run, pictures, None, "interrupted", False),
                                        note=str(e)))
            save()
            log(f"Run {run_id} interrupted: {e}. Sheets stay queued; trying again later.")
            reporter.update(state="idle", current_run=None)
            return done(INTERRUPTED)
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

def seconds_since_input():
    """Seconds since the last mouse or keyboard input on this PC (Windows), or
    a large number where that can't be read."""
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 10 ** 6
        return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return 10 ** 6


class PotryDriver:
    """The real PO-TRY, through ripauto.potry. Needs an elevated prompt.
    Connects on first use (so an idle loop never opens PO-TRY) and again
    after reset(), e.g. when PO-TRY was restarted."""

    def __init__(self, cfg):
        from . import potry
        self.potry, self.cfg = potry, cfg
        potry.configure(cfg)
        if potry.Application is None:
            sys.exit("pywinauto is missing. Run:  pip install pywinauto")
        if not potry.is_admin():
            sys.exit("Run this from an administrator Command Prompt. PO-TRY runs as admin.")
        self.main = self.pid = None

    def _ensure(self):
        if self.main is None:
            self.main, self.pid = self.potry.connect()
            try:
                self.main.maximize()             # SPEC §9: PO-TRY maximized
            except Exception:
                pass

    def reset(self):
        self.main = self.pid = None

    def queue_rows(self):
        self._ensure()
        self.potry.show_queue(self.main)
        self.potry.time.sleep(self.potry.SETTLE)
        return self.potry.read_queue(self.main)

    def rip(self, pictures, planned_in, dry_run, on_progress=None):
        self._ensure()
        try:
            return self.potry.rip_pictures(self.main, self.pid, pictures, self.cfg,
                                           planned_in, dry_run,
                                           multi_add=self.cfg.get("multi_add", True),
                                           on_progress=on_progress)
        except self.potry.Stop as e:
            raise Stop(str(e)) from None


# ---------------- the loop ----------------

def make_fetcher(cfg, secrets, log):
    """fetcher(ledger): download new sheets from the floor up. Raises on trouble."""
    from . import fetch
    floor = fetch.order_floor(0, cfg, downloading=True)

    def fetcher(ledger):
        token = fetch.get_token(cfg, secrets)
        sheets = fetch.find_sheets(cfg, token, floor, note=lambda *_: None)
        return fetch.download_new(cfg, ledger, sheets, log=log)
    return fetcher


def run_loop(cfg, ledger, driver, *, fetcher, dl_names_fn, reporter, save, log,
             pause_path=PAUSE_PATH, idle_s=seconds_since_input, clock=time.time,
             now_fn=datetime.now, sleep=time.sleep, stop=None, runs_dir=RUNS_DIR,
             warner=None, rip_now_path=RIP_NOW_PATH, restart_path=None):
    """Fetch every fetch_every_min, cycle every LOOP_EVERY_S, until stop is set.

    Before a ready run starts, a corner popup warns "Auto rip in 60 s" (Gate in
    ripauto/warning.py); at zero it starts once mouse and keyboard have been
    still for a few seconds. state/RIP_NOW (the RIP Agent window's "Rip now")
    rips the next run even if it isn't full or due, after a short countdown.

    Pause switch: while pause_path exists, nothing new starts; the loop reports
    'stopped' and checks again every 10 s. A run already in progress finishes
    first (pause is only checked between cycles and before a run starts).
    Errors are logged and reported, PO-TRY is reconnected, and the loop waits
    ERROR_BACKOFF_S before trying again; it never exits on its own."""
    stop = stop or threading.Event()
    restart_path = restart_path or RESTART_PATH
    fetch_every = max(1, cfg.get("fetch_every_min", 2)) * 60
    gate = Gate(warner or Warner(), idle_s=idle_s, clock=clock, pause_path=pause_path,
                rip_now_path=rip_now_path, warn_s=cfg.get("warn_before_rip_s", 60),
                still_s=cfg.get("still_before_rip_s", 5),
                snooze_s=int(cfg.get("snooze_min", 5) * 60),
                corner=cfg.get("warning_corner", "bottom-right"), log=log)
    last_fetch = None
    paused = False
    last_wait = None
    unreadable = 0
    log(f"Agent loop started (fetch every {fetch_every // 60} min, cycle every {LOOP_EVERY_S} s, "
        f"{gate.warn_s} s corner warning before each run).")
    reporter.update(state="idle")

    while not stop.is_set():
        if restart_path.exists() and not gate.active:
            # Safe point: between cycles, no countdown. Stop here;
            # main() then relaunches the scheduled task with any new code.
            try:
                restart_path.unlink()
            except OSError:
                pass
            log("Restart requested: stopping now and starting again in a few seconds.")
            reporter.update(state="stopped", current_run=None)
            return "restart"
        if pause_path.exists():
            if not paused:
                log(f"Paused ({pause_path.name} exists). Delete it or run tools\\resume.cmd to carry on.")
                reporter.update(state="stopped", current_run=None)
                paused = True
            gate.reset()
            sleep(10)
            continue
        if paused:
            log("Resumed.")
            reporter.update(state="idle")
            paused, last_fetch = False, None

        try:
            if last_fetch is None or clock() - last_fetch >= fetch_every:
                reporter.update(state="fetching")
                last_fetch = clock()
                added = fetcher(ledger)
                if added:
                    log(f"Fetched {len(added)} new sheet(s).")
            rip_now = rip_now_path.exists()
            summary = cycle(cfg, ledger, driver, now_fn(), dl_names_fn=dl_names_fn,
                            runs_dir=runs_dir, save=save, log=log, reporter=reporter,
                            can_start=gate, on_start=gate.started, force=rip_now, quiet=True)
            if summary["waiting"] == UNREADABLE:
                unreadable += 1
                if unreadable == UNREADABLE_ERROR_AFTER:
                    log(f"Couldn't read PO-TRY's queue or Downloads {unreadable} times in a row.")
                    reporter.update(state="error", current_run=None,
                                    last_error="can't read PO-TRY's RIP queue (or Downloads); nothing is "
                                               "being ripped until it can")
                gate.hold("Can't check PO-TRY's queue right now; trying again")
            else:
                if unreadable >= UNREADABLE_ERROR_AFTER:
                    log("PO-TRY's queue is readable again.")
                    reporter.update(last_error=None)
                unreadable = 0
            if summary["waiting"] == BUSY:
                gate.hold("Waiting for PO-TRY to finish ripping another job")
            elif summary["waiting"] == INTERRUPTED:
                gate.retry_later(RETRY_AFTER_INTERRUPT_S,
                                 "Rip interrupted: someone used the mouse or keyboard. Trying again.")
            elif summary["waiting"] not in (COUNTDOWN, UNREADABLE):
                gate.reset()                       # run went, or isn't ready any more
            if rip_now and summary["waiting"] not in (COUNTDOWN, BUSY, UNREADABLE, INTERRUPTED):
                if summary["waiting"] in ("nothing queued", "no run ready yet"):
                    log("Rip now: nothing is waiting to rip.")
                try:
                    rip_now_path.unlink()
                except OSError:
                    pass
            if summary["waiting"] != last_wait and summary["waiting"] not in (None, "nothing queued", COUNTDOWN):
                log(f"Waiting: {summary['waiting']}.")
            last_wait = summary["waiting"]
            if summary["stopped"]:
                log(f"Run stopped: {summary['stopped']}. Those sheets are held; carrying on.")
        except Exception as e:                     # never let one bad cycle end the agent
            log(f"ERROR {type(e).__name__}: {e}")
            reporter.update(state="error", current_run=None,
                            last_error=f"{type(e).__name__}: {e}")
            gate.reset()
            try:
                driver.reset()
            except Exception:
                pass
            sleep(ERROR_BACKOFF_S)
            continue
        sleep(COUNTDOWN_TICK_S if gate.active else LOOP_EVERY_S)
    gate.reset()
    log("Agent loop stopped.")


# ---------------- command line ----------------

def make_reporter(cfg, ledger, log):
    key = load_dashboard_key()
    if cfg.get("dashboard_url") and key:
        return Reporter(cfg, ledger, RUNS_DIR, cfg["dashboard_url"], key, log=log)
    log("No dashboard_url / dashboard_key set; not sending heartbeats.")
    return NullReporter()


def main():
    ap = argparse.ArgumentParser(description="The RIP agent: fetch, plan, hand check, rip.")
    ap.add_argument("--loop", action="store_true", help="run until stopped (Task Scheduler)")
    ap.add_argument("--once", action="store_true", help="run one cycle against PO-TRY")
    ap.add_argument("--plan", action="store_true", help="show the plan only; no PO-TRY, no changes")
    ap.add_argument("--dry-run", action="store_true", help="--once only: Cancel at the preview")
    ap.add_argument("--force", action="store_true", help="--once only: first run even if not ready")
    ap.add_argument("--max-runs", type=int, default=1, help="--once only: runs this cycle")
    args = ap.parse_args()
    if [args.loop, args.once, args.plan].count(True) != 1:
        sys.exit("Pick one: --loop, --once or --plan")

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
        if agent_running():
            print("(The agent loop is running.)")
        return

    from .potry import log
    hand_dir = Path(cfg["hand_download_dir"]) if cfg.get("hand_download_dir") else None
    if hand_dir is None:
        sys.exit("Set hand_download_dir in machine.json (SPEC §4).")
    dl_names = lambda: download_names(hand_dir)

    if args.once:
        if agent_running():
            sys.exit("The agent loop is running. Pause it (tools\\pause.cmd) and wait for "
                     "'stopped' on the dashboard, or end the scheduled task, first.")
        driver = PotryDriver(cfg)
        reporter = make_reporter(cfg, ledger, log)
        reporter.start()
        try:
            summary = cycle(cfg, ledger, driver, now, dl_names_fn=dl_names,
                            save=lambda: L.save(ledger), dry_run=args.dry_run, force=args.force,
                            max_runs=args.max_runs, log=log, reporter=reporter)
        finally:
            reporter.stop("stopped")
        log(f"Cycle done: ripped {len(summary['ripped'])}, dry runs {len(summary['dry_runs'])}, "
            f"held total {len(summary['held'])}, by hand total {len(summary['by_hand'])}"
            + (f", waiting: {summary['waiting']}" if summary["waiting"] else "")
            + (f", stopped: {summary['stopped']}" if summary["stopped"] else ""))
        if summary["stopped"]:
            sys.exit(1)
        return

    # --loop
    try:
        fetcher = make_fetcher(cfg, load_or_exit(load_secrets), log)
    except ConfigError as e:
        sys.exit(f"Not starting the loop: {e}.")
    lock = AgentLock()
    try:
        lock.__enter__()
    except RuntimeError as e:
        sys.exit(f"Not starting: {e}.")
    driver = PotryDriver(cfg)
    reporter = make_reporter(cfg, ledger, log)
    reporter.start()
    try:
        result = run_loop(cfg, ledger, driver, fetcher=fetcher, dl_names_fn=dl_names,
                          reporter=reporter, save=lambda: L.save(ledger), log=log)
    except KeyboardInterrupt:
        log("Stopped with Ctrl+C.")
        result = None
    finally:
        reporter.stop("stopped")
        lock.__exit__(None, None, None)
    if result == "restart":
        relaunch_via_task_scheduler(log)


def relaunch_via_task_scheduler(log, task=TASK_NAME):
    """Start the scheduled task again a few seconds after this process exits.
    (Task Scheduler's "restart on failure" only covers a task that fails to
    start, not one that exits with an error code: found 2026-10-15 13:09.)
    A detached cmd waits 5 s, so our lock is gone, then runs the task."""
    # Task Scheduler runs the agent inside a job object, and a helper left in
    # that job dies with the agent (13:51: no relaunch; the watchdog brought
    # it back at 13:53). So the helper asks to break away from the job; if
    # Windows refuses, the 5-minute watchdog trigger restarts the agent.
    base = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    cmd = ["cmd", "/c", f'ping -n 6 127.0.0.1 >nul & schtasks /run /tn "{task}"']   # ~5 s wait, no console needed
    for flags, how in ((base | breakaway, "in a few seconds"), (base, "within 5 minutes (watchdog)")):
        try:
            subprocess.Popen(cmd, creationflags=flags, close_fds=True)
            log(f"Relaunching through Task Scheduler {how}.")
            return
        except OSError:
            continue
    log("Couldn't start the relaunch helper; the watchdog starts the agent again within 5 minutes.")


if __name__ == "__main__":
    main()
