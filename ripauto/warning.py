"""
warning.py  -  the "auto rip in 60 s" corner popup and the gate that decides
when a ready run may start (Kevin, after go-live).

When a run is ready, the agent doesn't take the mouse straight away. It shows
a small always-on-top box in a screen corner (tools/rip_warning.pyw):

    Auto rip in 60 s
    303 in · 5 sheets · #47951 #47952 …
    [Rip now]  [Wait 5 min]  [Pause agent]

At zero it starts as soon as mouse and keyboard have been still for
still_before_rip_s (5 s), so it never grabs the mouse mid-movement. "Rip now"
(in the box, or the RIP Agent window's button, which drops state/RIP_NOW) skips
the wait: a short countdown, then it goes.

The agent and the popup talk through two small files in state/:
  rip_pending.json   agent -> popup: what's coming and when (deleted = close)
  rip_response.json  popup -> agent: {"action": "now" | "snooze" | "pause"}
"""

import json
import subprocess
import sys
import time
from pathlib import Path

from .config import REPO, STATE_DIR

PENDING_PATH = STATE_DIR / "rip_pending.json"
RESPONSE_PATH = STATE_DIR / "rip_response.json"
RIP_NOW_PATH = STATE_DIR / "RIP_NOW"
POPUP = REPO / "tools" / "rip_warning.pyw"


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


class Warner:
    """Shows, updates and closes the corner popup (a separate pythonw process)."""

    def __init__(self, launch=True, pending=PENDING_PATH, response=RESPONSE_PATH):
        self.launch, self.pending, self.response = launch, pending, response
        self.proc = None

    def show(self, info):
        _write_json(self.pending, info)
        if self.launch and (self.proc is None or self.proc.poll() is not None):
            exe = Path(sys.executable)
            pythonw = exe.with_name("pythonw.exe")
            try:
                self.proc = subprocess.Popen([str(pythonw if pythonw.exists() else exe), str(POPUP)],
                                             cwd=str(REPO))
            except OSError:
                self.proc = None                     # no popup; the countdown still runs

    def poll(self):
        """The popup's last button press ('now', 'snooze', 'pause'), or None."""
        try:
            action = json.loads(self.response.read_text(encoding="utf-8")).get("action")
        except (OSError, ValueError):
            return None
        try:
            self.response.unlink()
        except OSError:
            pass
        return action

    def close(self):
        try:
            self.pending.unlink()                    # the popup closes itself when this goes
        except OSError:
            pass


class Gate:
    """can_start(run) for the loop: True once the warning has run its course."""

    def __init__(self, warner, *, idle_s, clock=time.time, pause_path=STATE_DIR / "PAUSE",
                 rip_now_path=RIP_NOW_PATH, warn_s=60, rip_now_s=10, still_s=5, snooze_s=300,
                 corner="bottom-right", log=print):
        self.warner, self.idle_s, self.clock = warner, idle_s, clock
        self.pause_path, self.rip_now_path = pause_path, rip_now_path
        self.warn_s, self.rip_now_s, self.still_s, self.snooze_s = warn_s, rip_now_s, still_s, snooze_s
        self.corner, self.log = corner, log
        self.deadline = None
        self.go = False
        self.snoozed_until = 0.0
        self.last_info = None
        self.prev_info = None          # the last run shown, kept after it started
        self.note = None               # extra line in the popup (e.g. "interrupted")

    @property
    def active(self):
        return self.deadline is not None

    def reset(self):
        if self.deadline is not None:
            self.warner.close()
        self.deadline, self.go, self.last_info, self.note = None, False, None, None

    def retry_later(self, seconds, note):
        """The run started but a person interrupted it before Print: count down
        again from `seconds` with a note saying why (CRITIQUE A1)."""
        now = self.clock()
        info = self.last_info or self.prev_info
        self.reset()
        self.deadline, self.note = now + seconds, note
        self.log(f"Trying again in {seconds // 60} min.")
        if info:
            self.last_info = dict(info, deadline=self.deadline, message=None, note=note, rip_now=False)
            self.warner.show(self.last_info)

    def hold(self, message):
        """The run was cleared to start but can't yet (PO-TRY is busy ripping
        another job). Keep the popup up saying why; the countdown is over, so
        the next check starts the run as soon as it can, with no new countdown."""
        if self.deadline is None or self.last_info is None:
            return
        self.deadline = min(self.deadline, self.clock())
        self.last_info = dict(self.last_info, deadline=self.deadline, message=message)
        self.warner.show(self.last_info)

    def started(self):
        """Called right before the agent takes the mouse: close the popup."""
        self.prev_info = self.last_info
        self.reset()

    def __call__(self, run):
        now = self.clock()
        if self.pause_path.exists():
            self.reset()
            return False
        rip_now = self.rip_now_path.exists()
        action = self.warner.poll()
        if action == "pause":
            self.pause_path.parent.mkdir(parents=True, exist_ok=True)
            self.pause_path.write_text("paused from the rip warning\n", encoding="utf-8")
            self.log("Paused from the rip warning.")
            self.reset()
            return False
        if action == "snooze":
            self.snoozed_until = now + self.snooze_s
            self.log(f"Rip warning: waiting {self.snooze_s // 60} min (snoozed).")
            self.reset()
            return False
        if action == "now":
            self.go, self.deadline = True, now + 3            # 3 s to let go of the mouse
        if not rip_now and not self.go and now < self.snoozed_until:
            return False
        if self.deadline is None:
            self.deadline = now + (self.rip_now_s if rip_now else self.warn_s)
            self.log(f"Rip warning shown: {run.planned_in:.0f} in, {run.pictures} sheet(s), "
                     f"{' '.join(run.orders)}; starts in {self.deadline - now:.0f} s.")
        message = None
        cleared = False
        if now >= self.deadline:
            if self.go or self.idle_s() >= self.still_s:
                cleared = True
                message = "Starting now. Hands off the mouse."
            else:
                message = "Starting as soon as the mouse and keyboard are still"
        # The popup stays up until the agent actually takes over (started()),
        # so a busy PO-TRY can be explained in it rather than restarting the count.
        self.last_info = {
            "deadline": self.deadline, "message": message, "corner": self.corner,
            "planned_in": run.planned_in, "pictures": run.pictures, "orders": run.orders[:12],
            "more_orders": max(0, len(run.orders) - 12), "rip_now": bool(rip_now or self.go),
            "note": self.note,
        }
        self.warner.show(self.last_info)
        return cleared
