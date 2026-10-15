"""
control.py  -  what the RIP Agent control window shows and does
(tools/rip_agent_control.pyw; desktop shortcut "RIP Agent").

The window needs no admin rights. It reads:
  - whether the agent loop is running (state/agent.lock + its process)
  - whether the pause switch is on (state/PAUSE)
  - what the agent is doing, from the Pi dashboard (the same heartbeat)
  - the last lines of state/agent.log
and has one main button: Pause / Resume / Start.

Pause and Resume just create or delete state/PAUSE (the agent finishes any
run in progress first). Start asks Task Scheduler to run "OSS RIP Agent",
which a normal user may do for their own task.
"""

import json
import os
import subprocess
import urllib.request
from datetime import datetime

from .config import STATE_DIR
from .lock import agent_running

TASK_NAME = "OSS RIP Agent"
PAUSE_PATH = STATE_DIR / "PAUSE"
LOG_PATH = STATE_DIR / "agent.log"


def _hhmm(iso):
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%I:%M %p").lstrip("0")
    except (TypeError, ValueError):
        return "?"


def describe(running, paused, hb):
    """What to show: {"kind", "headline", "detail", "button", "action"}.
    kind: on | pausing | paused | off. hb is this machine's latest heartbeat
    from the dashboard (or None when the dashboard can't be reached)."""
    state = (hb or {}).get("state")
    if not running:
        detail = "Nothing is being fetched or ripped."
        if paused:
            detail += " (The pause switch is also on; Start turns it off.)"
        return {"kind": "off", "headline": "Agent is OFF", "detail": detail,
                "button": "Start agent", "action": "start"}
    if paused:
        if state == "ripping":
            return {"kind": "pausing", "headline": "PAUSING",
                    "detail": "Finishing the current run. After that nothing new starts.",
                    "button": "Resume agent", "action": "resume"}
        return {"kind": "paused", "headline": "PAUSED",
                "detail": "Nothing new will be fetched or ripped until you resume.",
                "button": "Resume agent", "action": "resume"}

    if hb is None:
        detail = "Running. (Can't reach the dashboard for details right now.)"
    elif state == "ripping":
        run = hb.get("current_run") or {}
        pct = run.get("progress_pct")
        detail = (f"Ripping {' '.join(run.get('orders') or [])}"
                  + (f": {pct:.0f}%" if isinstance(pct, (int, float)) else "")
                  + ". Please leave the mouse alone.")
    elif state == "fetching":
        detail = "Checking Shopify for new sheets."
    elif state == "planning":
        detail = "Getting a run ready."
    elif state in ("error", "holding"):
        detail = f"Last run needs a look: {hb.get('last_error') or 'see the dashboard'}"
    else:
        nr = (hb.get("queue_detail") or {}).get("next_run")
        if not nr:
            detail = "Waiting for new 8-color orders."
        elif nr.get("ready"):
            detail = (f"Next run ({nr.get('planned_in', 0):.0f} in) is ready. It starts once "
                      f"the PC has had a minute without mouse or keyboard.")
        else:
            detail = (f"Next run: {nr.get('planned_in', 0):.0f} in, at {_hhmm(nr.get('flush_at'))} "
                      f"or sooner if it fills up.")
    held = len((hb or {}).get("flags") or [])
    if held:
        detail += f"  {held} sheet{'s' if held != 1 else ''} held for a person (see dashboard)."
    return {"kind": "on", "headline": "ON", "detail": detail,
            "button": "Pause agent", "action": "pause"}


def dashboard_base(cfg):
    url = cfg.get("dashboard_url") or ""
    return url.split("/api/")[0] if "/api/" in url else url.rstrip("/")


def fetch_heartbeat(cfg, timeout=3):
    """This machine's latest heartbeat from the dashboard, or None."""
    base = dashboard_base(cfg)
    if not base:
        return None
    try:
        with urllib.request.urlopen(f"{base}/api/machines", timeout=timeout) as r:
            data = json.load(r)
    except Exception:
        return None
    for m in data.get("machines", []):
        if m.get("machine_id") == cfg.get("machine_id"):
            return m.get("hb")
    return None


def last_log_lines(n=4, path=LOG_PATH):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [line[11:] if len(line) > 11 and line[4] == "-" else line for line in lines[-n:]]


def status(cfg):
    running = agent_running()
    paused = PAUSE_PATH.exists()
    hb = fetch_heartbeat(cfg)
    info = describe(running, paused, hb)
    info["rip_now"] = can_rip_now(running, paused, hb)
    info["rip_now_pending"] = RIP_NOW_PATH.exists()
    info["restart_pending"] = RESTART_PATH.exists()
    return info, running, paused


def pause():
    PAUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PAUSE_PATH.write_text("paused from the RIP Agent window\n", encoding="utf-8")


def resume():
    try:
        PAUSE_PATH.unlink()
    except FileNotFoundError:
        pass


RIP_NOW_PATH = STATE_DIR / "RIP_NOW"
RESTART_PATH = STATE_DIR / "RESTART"


def can_rip_now(running, paused, hb):
    """Rip now makes sense when the agent is on, not paused, idle, and something waits."""
    if not running or paused or not hb or hb.get("state") in ("ripping", "planning"):
        return False
    return bool((hb.get("queue_detail") or {}).get("next_run"))


def rip_now():
    """Ask the agent to rip the next run now (a 10 s popup countdown first)."""
    RIP_NOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RIP_NOW_PATH.write_text("from the RIP Agent window\n", encoding="utf-8")


def request_restart():
    """Ask the agent to restart at its next safe point (to load new code).
    Task Scheduler starts it again about a minute later."""
    RESTART_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESTART_PATH.write_text("from the RIP Agent window\n", encoding="utf-8")


def start():
    """Turn the pause switch off and ask Task Scheduler to run the agent.
    Returns (ok, message)."""
    resume()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run(["schtasks", "/run", "/tn", TASK_NAME], capture_output=True,
                             text=True, timeout=20, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"Couldn't ask Task Scheduler: {e}"
    if out.returncode != 0:
        return False, (out.stderr or out.stdout).strip() or "Task Scheduler refused."
    return True, "Starting… (give it a few seconds)"


def open_log():
    if LOG_PATH.exists():
        os.startfile(str(LOG_PATH))       # Windows: opens in the default text editor
