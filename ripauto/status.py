"""
status.py  -  heartbeat to the Pi dashboard (SPEC §8).

The agent POSTs a small JSON status to the Pi every 30 s and on every state
change. The Pi only displays it; nothing on the Pi can start a fetch or a rip.

Allowed in a heartbeat: order numbers, inches, counts, states, machine names,
error text. Never: customer names, emails, addresses, file URLs, Shopify IDs
or tokens, file paths. Everything here is built from counts and order numbers;
free text (hold reasons, errors) goes through scrub() first, because those can
quote file and job names, which carry customer names.
"""

import json
import re
import socket
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

from . import __version__
from . import ledger as L

STATES = ("idle", "fetching", "planning", "ripping", "holding", "error", "stopped")
SEND_EVERY_S = 30
MAX_FLAGS = 25
MAX_TEXT = 160

# Free-text scrubbing. Order: paths and URLs first (they contain the rest).
_SCRUBS = [
    (re.compile(r"https?://\S+", re.I), "[link]"),
    # Windows paths: up to a file extension when there is one (our file names
    # have spaces), else up to the next space
    (re.compile(r"[A-Za-z]:[\\/][^|;\n]*?\.(?:png|jpe?g|pdf|zip|tiff?|prn|json|txt|exe)\b", re.I),
     "[path]"),
    (re.compile(r"[A-Za-z]:[\\/][^\s|,;]*"), "[path]"),
    (re.compile(r"\S+@\S+\.\w+"), "[email]"),
    # Build a Gang Sheet file/job names: {order}-...-({qty}), optional .png / copy suffix
    (re.compile(r"\b(\d{4,7})-[^\n|]*?\(\d+\)(?: ?\(\d+\)| \[\d+\])?(?:\.\w{3,4})?", re.I),
     r"#\1 file"),
    # any other order-numbered file name, e.g. a hand-saved "47862-Steve Springer.png"
    (re.compile(r"\b(\d{4,7})-[^\n|]*?\.(?:png|jpe?g|pdf|zip|tiff?|prn)\b", re.I), r"#\1 file"),
    (re.compile(r"\bshp\w{3,}\b", re.I), "[token]"),
    (re.compile(r"\b\d{10,}\b"), "[id]"),
]


def scrub(text):
    """Free text made safe for the Pi: no paths, links, emails, file/job names
    (which carry customer names), tokens or Shopify IDs. Trimmed to MAX_TEXT."""
    if text is None:
        return None
    s = str(text)
    for pattern, repl in _SCRUBS:
        s = pattern.sub(repl, s)
    return s[:MAX_TEXT]


def _now():
    return datetime.now().astimezone()


def _iso(dt):
    return dt.astimezone().isoformat(timespec="seconds") if dt else None


def queue_summary(ledger, now):
    """Sheets (copies) waiting, their inches, and the oldest wait in minutes."""
    sheets, inches, oldest = 0, 0.0, 0.0
    for rec in ledger.values():
        if L.status(rec) != L.QUEUED:
            continue
        left = L.copies_left(rec)
        if left <= 0:
            continue
        sheets += left
        inches += (rec.get("length_in") or 0) * left
        try:
            waited = (now.replace(tzinfo=None) - datetime.fromisoformat(rec["fetched_at"])).total_seconds() / 60
            oldest = max(oldest, waited)
        except (KeyError, ValueError):
            pass
    return {"sheets": sheets, "inches": round(inches, 1), "oldest_wait_min": int(oldest)}


def today_summary(runs_dir, now):
    """Real runs ripped today: inches (actual where known), sheets (pictures), runs."""
    inches, sheets, runs = 0.0, 0, 0
    day = now.strftime("%Y-%m-%d")
    for p in sorted(Path(runs_dir).glob("*.json")) if Path(runs_dir).exists() else []:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("dry_run") or rec.get("outcome") != "ripped":
            continue
        if not str(rec.get("ended") or rec.get("started") or "").startswith(day):
            continue
        inches += rec.get("actual_in") or rec.get("planned_in") or 0
        sheets += rec.get("pictures") or 0
        runs += 1
    return {"inches_ripped": round(inches, 1), "sheets": sheets, "runs": runs}


def flags_from(ledger):
    """Held sheets as {order, reason}, newest first, reasons scrubbed."""
    held = [r for r in ledger.values() if L.status(r) == L.HELD]
    held.sort(key=lambda r: r.get("updated_at") or r.get("fetched_at") or "", reverse=True)
    return [{"order": str(r.get("order", ""))[:12], "reason": scrub(r.get("hold_reason") or "held")}
            for r in held[:MAX_FLAGS]]


def build_heartbeat(cfg, ledger, runs_dir, *, state, since, current_run=None,
                    last_error=None, now=None, host=None):
    """The heartbeat dict (SPEC §8). Pure apart from reading run records."""
    now = now or _now()
    run = None
    if current_run:
        run = {"id": str(current_run.get("id", ""))[:60],
               "orders": [str(o)[:12] for o in current_run.get("orders", [])][:40],
               "planned_in": current_run.get("planned_in"),
               "progress_pct": current_run.get("progress_pct")}
    return {
        "machine_id": cfg["machine_id"],
        "label": cfg.get("label", cfg["machine_id"]),
        "host": (host or socket.gethostname())[:40],
        "agent_version": __version__,
        "state": state if state in STATES else "error",
        "since": _iso(since),
        "current_run": run,
        "today": today_summary(runs_dir, now),
        "queue": queue_summary(ledger, now),
        "flags": flags_from(ledger),
        "last_error": scrub(last_error),
        "sent_at": _iso(now),
    }


def send(url, key, heartbeat, timeout=5):
    """POST the heartbeat. Returns (ok, detail). Never raises; the key is sent
    only to the dashboard and never logged."""
    if not url or not key:
        return False, "no dashboard_url / dashboard_key configured"
    req = urllib.request.Request(
        url, data=json.dumps(heartbeat).encode(),
        headers={"Content-Type": "application/json", "X-Agent-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300, f"HTTP {r.status}"
    except Exception as e:             # network trouble must never stop a rip
        return False, type(e).__name__


class Reporter:
    """Keeps the agent's current state and sends heartbeats: immediately on a
    change, and every SEND_EVERY_S from a background thread."""

    def __init__(self, cfg, ledger, runs_dir, url, key, log=print, sender=send):
        self.cfg, self.ledger, self.runs_dir = cfg, ledger, runs_dir
        self.url, self.key, self.log, self.sender = url, key, log, sender
        self.state, self.since = "idle", _now()
        self.current_run, self.last_error = None, None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._warned = False

    def heartbeat(self):
        with self._lock:
            return build_heartbeat(self.cfg, self.ledger, self.runs_dir, state=self.state,
                                   since=self.since, current_run=self.current_run,
                                   last_error=self.last_error)

    def push(self):
        ok, detail = self.sender(self.url, self.key, self.heartbeat())
        if not ok and not self._warned:
            self.log(f"  (dashboard not reachable: {detail}; carrying on)")
            self._warned = True
        if ok:
            self._warned = False
        return ok

    def update(self, state=None, current_run=..., last_error=..., progress_pct=None):
        """Change what's reported and send at once. ... means 'leave as is'."""
        with self._lock:
            if state and state != self.state:
                self.state, self.since = state, _now()
            if current_run is not ...:
                self.current_run = dict(current_run) if current_run else None
            if last_error is not ...:
                self.last_error = last_error
            if progress_pct is not None and self.current_run is not None:
                self.current_run["progress_pct"] = progress_pct
        self.push()

    def start(self):
        def loop():
            while not self._stop.wait(SEND_EVERY_S):
                self.push()
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self, final_state="stopped"):
        self._stop.set()
        self.update(state=final_state, current_run=None)


class NullReporter:
    """Stand-in when no dashboard is configured (and in tests)."""

    def update(self, *a, **kw):
        pass

    def start(self):
        pass

    def stop(self, *a, **kw):
        pass
