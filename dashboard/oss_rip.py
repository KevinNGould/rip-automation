#!/usr/bin/env python3
"""
One Source Supply — RIP Automation dashboard  (rip-automation SPEC §8)
══════════════════════════════════════════════════════════════════════
Display-only status page on the hub Pi. Each printer PC's agent POSTs a
heartbeat every 30 s; this shows one card per machine:
running / stopped / offline (no heartbeat for 2 min), current run with order
numbers and progress, inches ripped today, the waiting queue, flags, last error.

Nothing here can start a fetch or a rip, and it needs no Shopify access.

Files (all in /home/pi):
  oss_rip.py                 this service
  oss_rip_machines.json      copy of rip-automation config/machines.json
                             (machine list and labels: rename/add/switch off there)
  oss_rip_settings.json      agent_key (made on first run, chmod 600)
  oss_rip_state.json         last heartbeat per machine (survives restarts)

Agents authenticate with the X-Agent-Key header, the same pattern as
oss_command.py. Incoming heartbeats are re-checked here: known machine ids
only, whitelisted fields, order numbers must look like order numbers, free
text is scrubbed again of paths, links, emails, file names and long IDs.

Run:   python3 oss_rip.py           → http://osshub.local:8085
Auto-start:
  sudo cp oss_rip.service /etc/systemd/system/
  sudo systemctl enable oss_rip && sudo systemctl start oss_rip
"""

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("rip")

HOME = Path.home()
CONFIG = {
    "web_port": 8085,
    "machines_path": HOME / "oss_rip_machines.json",
    "settings_path": HOME / "oss_rip_settings.json",
    "state_path": HOME / "oss_rip_state.json",
    "db_path": HOME / "oss_rip.db",          # order history for search (chmod 600)
    "offline_after_s": 120,
}
STATES = ("idle", "fetching", "planning", "ripping", "holding", "error", "stopped")


# ── files ─────────────────────────────────────────────────────────────

def write_private(path, data):
    """Write JSON readable by this user only, atomically (same as oss_command)."""
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        log.error(f"{path} unreadable ({e}); using defaults")
        return default


def load_settings():
    settings = load_json(CONFIG["settings_path"], {})
    if not settings.get("agent_key"):
        settings["agent_key"] = os.urandom(24).hex()
        write_private(CONFIG["settings_path"], settings)
        log.info(f"Made a new agent key in {CONFIG['settings_path']}")
    return settings


def load_machines():
    """[(machine_id, label, line_label, enabled)] from oss_rip_machines.json."""
    shop = load_json(CONFIG["machines_path"], {})
    lines = shop.get("lines") or {}
    out = []
    for mid, m in (shop.get("machines") or {}).items():
        line = lines.get(m.get("line"), {})
        out.append((str(mid), str(m.get("label") or mid), str(line.get("label") or m.get("line") or ""),
                    bool(m.get("enabled"))))
    return out


# ── cleaning incoming heartbeats ──────────────────────────────────────

_SCRUBS = [
    (re.compile(r"https?://\S+", re.I), "[link]"),
    (re.compile(r"[A-Za-z]:[\\/][^|;\n]*?\.(?:png|jpe?g|pdf|zip|tiff?|prn|json|txt|exe)\b", re.I), "[path]"),
    (re.compile(r"[A-Za-z]:[\\/][^\s|,;]*"), "[path]"),
    (re.compile(r"/(?:home|Users|tmp|mnt)/\S+"), "[path]"),
    (re.compile(r"\S+@\S+\.\w+"), "[email]"),
    (re.compile(r"\b(\d{4,7})-[^\n|]*?\(\d+\)(?: ?\(\d+\)| \[\d+\])?(?:\.\w{3,4})?"), r"#\1 file"),
    (re.compile(r"\b(\d{4,7})-[^\n|]*?\.(?:png|jpe?g|pdf|zip|tiff?|prn)\b", re.I), r"#\1 file"),
    (re.compile(r"\bshp\w{3,}\b", re.I), "[token]"),
    (re.compile(r"\b\d{10,}\b"), "[id]"),
]
_ORDER = re.compile(r"^#?\d{3,8}$")


def scrub(text, limit=160):
    if text is None:
        return None
    s = str(text)
    for pattern, repl in _SCRUBS:
        s = pattern.sub(repl, s)
    return s[:limit]


def num(v, lo=0.0, hi=1e7):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if lo <= f <= hi else None


def order(v):
    s = str(v or "").strip()
    return s if _ORDER.match(s) else None


_WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?([+-]\d{2}:\d{2}|Z)?)?$")
OUTCOMES = ("ripped", "gate", "stopped", "over_cap")
REASONS = ("full", "flush", "waiting")


def when(v):
    s = str(v or "").strip()
    return s if _WHEN.match(s) else None


def orders(v, limit=40):
    return [o for o in map(order, v[:limit]) if o] if isinstance(v, list) else []


def dicts(v, limit):
    return [d for d in v[:limit] if isinstance(d, dict)] if isinstance(v, list) else []


def clean_detail(data):
    """The dashboard-detail sections: queue, next run, ripped log, history."""
    qd = data.get("queue_detail") if isinstance(data.get("queue_detail"), dict) else {}
    sheets = []
    for s in dicts(qd.get("sheets"), 60):
        if order(s.get("order")):
            sheets.append({"order": order(s.get("order")), "position": num(s.get("position"), 0, 99),
                           "copies": num(s.get("copies"), 0, 999), "length_in": num(s.get("length_in")),
                           "waited_min": num(s.get("waited_min"), 0, 1e6), "low_res": bool(s.get("low_res"))})
    nr = qd.get("next_run") if isinstance(qd.get("next_run"), dict) else None
    next_run = None
    if nr:
        next_run = {"orders": orders(nr.get("orders")), "planned_in": num(nr.get("planned_in")),
                    "pictures": num(nr.get("pictures"), 0, 999), "ready": bool(nr.get("ready")),
                    "reason": nr.get("reason") if nr.get("reason") in REASONS else None,
                    "flush_at": when(nr.get("flush_at")), "to_full_in": num(nr.get("to_full_in")),
                    "runs_planned": num(nr.get("runs_planned"), 0, 999)}
    runs = []
    for r in dicts(data.get("recent_runs"), 25):
        runs.append({"id": scrub(r.get("id"), 60), "ended": when(r.get("ended")),
                     "orders": orders(r.get("orders")), "pictures": num(r.get("pictures"), 0, 999),
                     "planned_in": num(r.get("planned_in")), "actual_in": num(r.get("actual_in")),
                     "outcome": r.get("outcome") if r.get("outcome") in OUTCOMES else None})
    by_hand = []
    for b in dicts(data.get("by_hand"), 25):
        if order(b.get("order")):
            by_hand.append({"order": order(b.get("order")), "position": num(b.get("position"), 0, 99),
                            "copies": num(b.get("copies"), 0, 999), "length_in": num(b.get("length_in")),
                            "at": when(b.get("at"))})
    hist = []
    for h in dicts(data.get("history"), 60):
        if when(h.get("date")):
            hist.append({"date": when(h.get("date"))[:10], "inches": num(h.get("inches")),
                         "runs": num(h.get("runs"), 0, 1e4), "sheets": num(h.get("sheets"), 0, 1e5),
                         "by_hand": num(h.get("by_hand"), 0, 1e5)})
    return {"queue_detail": {"sheets": sheets, "more": num(qd.get("more"), 0, 1e5) or 0,
                             "next_run": next_run},
            "recent_runs": runs, "by_hand": by_hand, "history": hist}


def clean_heartbeat(data):
    """Only the fields SPEC §8 allows, checked and trimmed."""
    run = data.get("current_run")
    clean_run = None
    if isinstance(run, dict):
        clean_run = {
            "id": scrub(run.get("id"), 60),
            "orders": [o for o in map(order, run["orders"][:40]) if o]
            if isinstance(run.get("orders"), list) else [],
            "planned_in": num(run.get("planned_in")),
            "progress_pct": num(run.get("progress_pct"), 0, 100),
        }
    today = data.get("today") if isinstance(data.get("today"), dict) else {}
    queue = data.get("queue") if isinstance(data.get("queue"), dict) else {}
    flags = []
    for f in (data.get("flags") or [])[:25] if isinstance(data.get("flags"), list) else []:
        if isinstance(f, dict) and order(f.get("order")):
            flags.append({"order": order(f.get("order")), "reason": scrub(f.get("reason"))})
    state = data.get("state")
    return {
        **clean_detail(data),
        "host": scrub(data.get("host"), 40),
        "agent_version": scrub(data.get("agent_version"), 20),
        "state": state if state in STATES else "error",
        "since": scrub(data.get("since"), 40),
        "current_run": clean_run,
        "today": {"inches_ripped": num(today.get("inches_ripped")),
                  "sheets": num(today.get("sheets")), "runs": num(today.get("runs"))},
        "queue": {"sheets": num(queue.get("sheets")), "inches": num(queue.get("inches")),
                  "oldest_wait_min": num(queue.get("oldest_wait_min"))},
        "flags": flags,
        "last_error": scrub(data.get("last_error")),
        "sent_at": scrub(data.get("sent_at"), 40),
    }


# ── app ───────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 128 * 1024   # heartbeat with queue/history is ~15 KB

settings = load_settings()
_lock = threading.Lock()
latest = load_json(CONFIG["state_path"], {})     # machine_id -> {"received": epoch, "hb": {...}}


# ── order history (search "where did #47946 end up?") ──────────────────
# Built from cleaned heartbeats only, so it holds the same things the page
# shows: order numbers, sheet positions, machines, run ids, inches, times.
# One row per (order, machine, kind, ref); a repeat sighting updates last_seen.
#   kind  waiting | ripped | gate | stopped | over_cap | by_hand | held
#   ref   sheet position (waiting, by_hand), run id (runs), "" (held)

def db():
    path = str(CONFIG["db_path"])
    if not os.path.exists(path):
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    con = sqlite3.connect(path, timeout=10)
    con.execute("""CREATE TABLE IF NOT EXISTS sightings (
        order_num TEXT NOT NULL, machine_id TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL,
        detail TEXT, inches REAL, at TEXT, first_seen REAL, last_seen REAL,
        PRIMARY KEY (order_num, machine_id, kind, ref))""")
    con.execute("CREATE INDEX IF NOT EXISTS sightings_order ON sightings(order_num)")
    return con


def record_sightings(mid, hb, now=None):
    now = now or time.time()
    rows = []                                # (order, kind, ref, detail, inches, at)
    qd = hb.get("queue_detail") or {}
    for s in qd.get("sheets", []):
        rows.append((s["order"], "waiting", str(int(s["position"] or 0)),
                     f"{int(s['copies'] or 1)} cop{'y' if (s['copies'] or 1) == 1 else 'ies'}",
                     s["length_in"], None))
    for r in hb.get("recent_runs", []):
        if not r.get("outcome") or not r.get("id"):
            continue
        for o in r.get("orders", []):
            rows.append((o, r["outcome"], r["id"], f"{int(r['pictures'] or 0)} pictures in run",
                         r.get("actual_in") or r.get("planned_in"), r.get("ended")))
    for b in hb.get("by_hand", []):
        rows.append((b["order"], "by_hand", str(int(b["position"] or 0)), "found done by hand",
                     b.get("length_in"), b.get("at")))
    for f in hb.get("flags", []):
        rows.append((f["order"], "held", "", f.get("reason"), None, None))
    if not rows:
        return
    con = db()
    try:
        with con:
            for order_s, kind, ref, detail, inches, at in rows:
                con.execute("""INSERT INTO sightings VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(order_num, machine_id, kind, ref) DO UPDATE SET
                      detail=excluded.detail, inches=COALESCE(excluded.inches, inches),
                      at=COALESCE(excluded.at, at), last_seen=excluded.last_seen""",
                            (order_s.lstrip("#"), mid, kind, ref, detail, inches, at, now, now))
    finally:
        con.close()


def search_orders(q, limit_orders=50):
    """Every sighting of orders whose number starts with q (digits, 3+)."""
    digits = re.sub(r"\D", "", q or "")
    if len(digits) < 3:
        return None
    labels = {m[0]: m[1] for m in load_machines()}
    con = db()
    try:
        found = [r[0] for r in con.execute(
            "SELECT order_num, MAX(last_seen) m FROM sightings WHERE order_num LIKE ? "
            "GROUP BY order_num ORDER BY m DESC LIMIT ?", (digits + "%", limit_orders))]
        out = []
        for o in found:
            events = [dict(zip(("machine_id", "kind", "ref", "detail", "inches", "at", "first_seen", "last_seen"), r))
                      for r in con.execute("SELECT machine_id, kind, ref, detail, inches, at, first_seen, last_seen "
                                           "FROM sightings WHERE order_num=? ORDER BY first_seen", (o,))]
            for e in events:
                e["label"] = labels.get(e["machine_id"], e["machine_id"])
                for k in ("first_seen", "last_seen"):
                    e[k] = datetime.fromtimestamp(e[k]).astimezone().isoformat(timespec="seconds")
            out.append({"order": "#" + o, "events": events})
        return out
    finally:
        con.close()


@app.route("/api/search")
def api_search():
    results = search_orders(request.args.get("q", ""))
    if results is None:
        return jsonify({"ok": False, "error": "type at least 3 digits of an order number"}), 400
    return jsonify({"ok": True, "orders": results})


@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    key = request.headers.get("X-Agent-Key", "")
    if not settings.get("agent_key") or key != settings["agent_key"]:
        return jsonify({"ok": False}), 401
    data = request.get_json(silent=True) or {}
    mid = str(data.get("machine_id", ""))[:32]
    if mid not in {m[0] for m in load_machines()}:
        return jsonify({"ok": False, "error": "unknown machine_id"}), 400
    with _lock:
        latest[mid] = {"received": time.time(), "hb": clean_heartbeat(data)}
        try:
            write_private(CONFIG["state_path"], latest)
        except Exception as e:
            log.error(f"state save failed: {e}")
        try:
            record_sightings(mid, latest[mid]["hb"])
        except Exception as e:
            log.error(f"order history save failed: {e}")
    return jsonify({"ok": True})


@app.route("/api/machines")
def api_machines():
    now = time.time()
    cards = []
    with _lock:
        for mid, label, line_label, enabled in load_machines():
            entry = latest.get(mid)
            age = now - entry["received"] if entry else None
            if entry is None:
                status = "never"
            elif age > CONFIG["offline_after_s"]:
                status = "offline"
            elif entry["hb"]["state"] == "stopped":
                status = "stopped"
            else:
                status = "running"
            cards.append({"machine_id": mid, "label": label, "line": line_label,
                          "enabled": enabled, "status": status,
                          "seen_s_ago": int(age) if age is not None else None,
                          "hb": entry["hb"] if entry else None})
    return jsonify({"machines": cards, "server_time": datetime.now(timezone.utc).isoformat()})


@app.route("/")
@app.route("/tv")
def index():
    # One page; it shows the floor-TV view when opened at /tv, and a machine's
    # detail view at /#m=<machine_id>.
    return render_template_string(PAGE)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RIP Automation</title>
<style>
  :root { --bg:#14161a; --card:#1d2026; --line:#2c3038; --text:#e8eaed; --dim:#8b919c; --mute:#6b717c;
          --ok:#3ecf8e; --busy:#4aa3ff; --warn:#f5b84a; --bad:#ff5d5d; --bar:#3d8fe0; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:15px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
  a { color:inherit; }
  header { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap;
           padding:14px 20px; border-bottom:1px solid var(--line); }
  h1 { font-size:20px; margin:0; letter-spacing:.3px; }
  nav { display:flex; gap:6px; }
  nav a { text-decoration:none; font-size:13px; padding:5px 11px; border-radius:99px; border:1px solid var(--line); color:var(--dim); }
  nav a.on { color:var(--text); border-color:var(--dim); }
  .sub { color:var(--dim); font-size:13px; }
  main { padding:16px 20px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); gap:16px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
  .card.click { cursor:pointer; } .card.click:hover { border-color:var(--dim); }
  .card.off { opacity:.55; }
  .top { display:flex; justify-content:space-between; align-items:center; gap:8px; }
  .name { font-weight:650; font-size:17px; }
  .line { color:var(--dim); font-size:13px; }
  .pill { font-size:12px; font-weight:650; padding:3px 9px; border-radius:99px;
          text-transform:uppercase; letter-spacing:.5px; white-space:nowrap; }
  .running { background:rgba(62,207,142,.15); color:var(--ok); }
  .ripping { background:rgba(74,163,255,.15); color:var(--busy); }
  .stopped, .never, .switched-off { background:rgba(139,145,156,.15); color:var(--dim); }
  .offline, .error { background:rgba(255,93,93,.15); color:var(--bad); }
  .holding { background:rgba(245,184,74,.15); color:var(--warn); }
  .stats { display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; margin-top:12px; }
  .stat { background:rgba(255,255,255,.03); border-radius:8px; padding:8px; }
  .stat b { display:block; font-size:20px; }
  .stat span { color:var(--dim); font-size:12px; }
  .run { margin-top:12px; padding:10px; border-radius:8px; background:rgba(74,163,255,.08); }
  .bar { height:6px; background:var(--line); border-radius:3px; margin-top:6px; overflow:hidden; }
  .bar i { display:block; height:100%; background:var(--busy); }
  .flags { margin-top:10px; font-size:13px; }
  .flag { color:var(--warn); margin-top:3px; }
  .err { margin-top:10px; font-size:13px; color:var(--bad); }
  .foot { margin-top:10px; color:var(--dim); font-size:12px; }
  /* detail view */
  .back { font-size:13px; color:var(--dim); text-decoration:none; }
  .dhead { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; flex-wrap:wrap; margin:6px 0 14px; }
  .dhead .name { font-size:22px; }
  .tiles { display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:10px; margin-bottom:16px; }
  .tile { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
  .tile b { display:block; font-size:26px; font-variant-numeric:tabular-nums; }
  .tile span { color:var(--dim); font-size:12px; }
  .cols { display:grid; grid-template-columns:repeat(auto-fit, minmax(340px, 1fr)); gap:16px; }
  .panel { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin-bottom:16px; min-width:0; }
  .panel h2 { font-size:14px; margin:0 0 10px; color:var(--dim); font-weight:600; text-transform:uppercase; letter-spacing:.6px; }
  .big { font-size:18px; font-weight:600; }
  table { width:100%; border-collapse:collapse; font-size:13px; font-variant-numeric:tabular-nums; }
  th { text-align:left; color:var(--mute); font-weight:500; padding:4px 6px; border-bottom:1px solid var(--line); }
  td { padding:5px 6px; border-bottom:1px solid rgba(255,255,255,.04); vertical-align:top; }
  td.num, th.num { text-align:right; }
  .tag { font-size:11px; padding:1px 7px; border-radius:99px; background:rgba(139,145,156,.15); color:var(--dim); white-space:nowrap; }
  .tag.ok { background:rgba(62,207,142,.15); color:var(--ok); }
  .tag.warn { background:rgba(245,184,74,.15); color:var(--warn); }
  .tag.bad { background:rgba(255,93,93,.15); color:var(--bad); }
  .empty { color:var(--mute); font-size:13px; padding:6px 0; }
  .scroll { max-height:340px; overflow:auto; }
  .chart { position:relative; }
  .chart svg { display:block; width:100%; height:auto; }
  .tip { position:absolute; pointer-events:none; background:#0d0f12; border:1px solid var(--line); border-radius:8px;
         padding:7px 9px; font-size:12px; white-space:nowrap; transform:translate(-50%, -100%); display:none; }
  .tip b { display:block; margin-bottom:2px; }
  .sumrow { display:flex; gap:18px; flex-wrap:wrap; margin-top:10px; font-size:13px; color:var(--dim); }
  .sumrow b { color:var(--text); }
  details { margin-top:10px; font-size:13px; } summary { cursor:pointer; color:var(--dim); }
  /* search */
  #find { display:flex; gap:6px; flex:1; max-width:380px; min-width:220px; }
  #find input { flex:1; min-width:0; background:var(--card); color:var(--text); border:1px solid var(--line);
                border-radius:8px; padding:7px 10px; font:inherit; font-size:14px; }
  #find input:focus { outline:none; border-color:var(--busy); }
  #find button { background:var(--card); color:var(--text); border:1px solid var(--line); border-radius:8px;
                 padding:7px 12px; font:inherit; font-size:14px; cursor:pointer; }
  .order { margin-bottom:14px; }
  .order h3 { margin:0 0 8px; font-size:18px; }
  .ev { display:grid; grid-template-columns:120px 1fr; gap:10px; padding:6px 0; border-top:1px solid rgba(255,255,255,.05); font-size:14px; }
  .ev .when { color:var(--dim); font-size:13px; font-variant-numeric:tabular-nums; }
  @media (max-width:520px) { .ev { grid-template-columns:1fr; gap:2px; } }
  /* TV view */
  body.tv header { display:none; }
  body.tv main { padding:28px 34px; }
  .tvgrid { display:grid; gap:24px; grid-template-columns:repeat(auto-fit, minmax(520px, 1fr)); }
  .tvcard { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:26px 30px; }
  .tvtop { display:flex; justify-content:space-between; align-items:center; }
  .tvname { font-size:34px; font-weight:700; }
  .tvpill { font-size:22px; padding:6px 18px; }
  .tvrun { margin-top:20px; font-size:24px; }
  .tvbar { height:22px; background:var(--line); border-radius:11px; overflow:hidden; margin-top:10px; }
  .tvbar i { display:block; height:100%; background:var(--busy); }
  .tvnums { display:grid; grid-template-columns:repeat(3, 1fr); gap:16px; margin-top:24px; }
  .tvnums b { display:block; font-size:56px; line-height:1.05; font-variant-numeric:tabular-nums; }
  .tvnums span { color:var(--dim); font-size:18px; }
  .tvnext { margin-top:20px; font-size:20px; color:var(--dim); }
  .tvwarn { margin-top:14px; font-size:20px; color:var(--warn); }
  .tvclock { text-align:right; color:var(--dim); font-size:20px; margin-top:22px; }
  @media (max-width:520px) { header, main { padding-left:16px; padding-right:16px; }
    .tvgrid { grid-template-columns:1fr; } .tvnums b { font-size:38px; } }
</style></head>
<body>
<header><h1>RIP Automation</h1>
  <form id="find" onsubmit="findOrder(event)"><input id="q" inputmode="numeric" autocomplete="off"
    placeholder="Find order #, e.g. 47946" aria-label="Find an order number"><button>Find</button></form>
  <nav><a href="/" id="nav-floor">Floor</a><a href="/tv" id="nav-tv">TV</a></nav>
  <span class="sub" id="clock">display only</span></header>
<main id="root"><div class="sub">Loading…</div></main>
<script>
const TV = location.pathname.replace(/\/+$/, "") === "/tv";
if (TV) document.body.classList.add("tv");
document.getElementById(TV ? "nav-tv" : "nav-floor").classList.add("on");
let DATA = null;

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const n = (v, d=0) => v === null || v === undefined ? "–" : Number(v).toLocaleString(undefined, {maximumFractionDigits:d, minimumFractionDigits:0});
const t = iso => { if (!iso) return "–"; const d = new Date(iso); return isNaN(d) ? "–" : d.toLocaleTimeString([], {hour:"numeric", minute:"2-digit"}); };
const dt = iso => { if (!iso) return "–"; const d = new Date(iso); if (isNaN(d)) return "–";
  const today = new Date().toDateString() === d.toDateString();
  return today ? t(iso) : d.toLocaleDateString([], {month:"short", day:"numeric"}) + " " + t(iso); };
function ago(s) { if (s === null || s === undefined) return "never"; if (s < 60) return s + " s ago";
  if (s < 3600) return Math.round(s/60) + " min ago"; return Math.round(s/3600) + " h ago"; }
function mins(m) { if (m === null || m === undefined) return "–"; return m < 60 ? n(m) + " min" : n(m/60, 1) + " h"; }

function pillOf(m) {
  const hb = m.hb;
  if (!m.enabled && m.status === "never") return ["switched off", "switched-off"];
  if (m.status === "running" && hb) return [hb.state, {ripping:"ripping", holding:"holding", error:"error"}[hb.state] || "running"];
  return [m.status, m.status];
}

// ---------- floor view (cards) ----------
function card(m) {
  const hb = m.hb, [pill, cls] = pillOf(m);
  const click = hb ? `onclick="location.hash='m=${encodeURIComponent(m.machine_id)}'"` : "";
  let h = `<div class="card ${m.enabled ? "" : "off"} ${hb ? "click" : ""}" ${click}><div class="top"><div>
    <div class="name">${esc(m.label)}</div><div class="line">${esc(m.line)}</div></div>
    <span class="pill ${cls}">${esc(pill)}</span></div>`;
  if (hb) {
    const td = hb.today || {}, q = hb.queue || {};
    if (hb.current_run && m.status === "running") {
      const r = hb.current_run, p = r.progress_pct;
      h += `<div class="run"><b>Run</b> ${esc((r.orders || []).join(" "))}
        <div class="sub">${n(r.planned_in, 1)} in planned${p === null ? "" : " · " + n(p) + "%"}</div>
        ${p === null ? "" : `<div class="bar"><i style="width:${Math.max(0, Math.min(100, p))}%"></i></div>`}</div>`;
    }
    h += `<div class="stats">
      <div class="stat"><b>${n(td.inches_ripped, 1)}</b><span>in ripped today</span></div>
      <div class="stat"><b>${n(td.runs)}</b><span>runs · ${n(td.sheets)} sheets</span></div>
      <div class="stat"><b>${n(q.sheets)}</b><span>waiting · ${n(q.inches, 0)} in · ${n(q.oldest_wait_min)} min</span></div></div>`;
    if ((hb.flags || []).length) h += `<div class="flags"><b>Held (${hb.flags.length})</b> · open for details</div>`;
    if (hb.last_error) h += `<div class="err">Last error: ${esc(hb.last_error)}</div>`;
    h += `<div class="foot">${esc(hb.host)} · agent ${esc(hb.agent_version)} · heard ${ago(m.seen_s_ago)} · click for detail</div>`;
  } else if (m.enabled) {
    h += `<div class="foot">No heartbeat yet.</div>`;
  }
  return h + `</div>`;
}
function renderFloor() {
  return `<div class="grid">${DATA.machines.map(card).join("")}</div>`;
}

// ---------- machine detail ----------
function nextRunText(nr) {
  if (!nr) return `<div class="empty">Nothing waiting.</div>`;
  const what = `<div class="big">${n(nr.planned_in, 1)} in · ${n(nr.pictures)} picture${nr.pictures == 1 ? "" : "s"}</div>
    <div class="sub">${esc((nr.orders || []).join(" "))}</div>`;
  let when;
  if (nr.ready) when = nr.reason === "full"
      ? "Full. It rips as soon as the PC has had a minute without mouse or keyboard."
      : "Waited long enough. It rips as soon as the PC has had a minute without mouse or keyboard.";
  else when = `Partial run. It goes at <b>${t(nr.flush_at)}</b> (30-minute wait), or sooner if ${n(nr.to_full_in, 0)} in more arrives.`;
  const more = nr.runs_planned > 1 ? `<div class="sub" style="margin-top:6px">${n(nr.runs_planned - 1)} more run${nr.runs_planned == 2 ? "" : "s"} planned after this one.</div>` : "";
  return what + `<div style="margin-top:8px">${when}</div>` + more;
}
function queueTable(qd) {
  const s = qd.sheets || [];
  if (!s.length) return `<div class="empty">No sheets waiting.</div>`;
  return `<div class="scroll"><table><tr><th>Order</th><th class="num">Sheet</th><th class="num">Copies</th>
    <th class="num">Length</th><th class="num">Waiting</th><th></th></tr>` +
    s.map(x => `<tr><td>${esc(x.order)}</td><td class="num">${n(x.position)}</td><td class="num">${n(x.copies)}</td>
      <td class="num">${n(x.length_in, 1)} in</td><td class="num">${mins(x.waited_min)}</td>
      <td>${x.low_res ? '<span class="tag warn">low-res</span>' : ""}</td></tr>`).join("") + `</table></div>` +
    (qd.more ? `<div class="sub">…and ${n(qd.more)} more</div>` : "");
}
function logTable(hb) {
  const rows = [];
  (hb.recent_runs || []).forEach(r => rows.push({when: r.ended, kind: "run", r}));
  (hb.by_hand || []).forEach(b => rows.push({when: b.at, kind: "hand", b}));
  rows.sort((a, b) => String(b.when || "").localeCompare(String(a.when || "")));
  if (!rows.length) return `<div class="empty">Nothing ripped yet.</div>`;
  const outcome = {ripped:["ok","ripped"], gate:["warn","cancelled at preview"], stopped:["bad","stopped"], over_cap:["bad","over cap"]};
  return `<div class="scroll"><table><tr><th>When</th><th>What</th><th>Orders</th><th class="num">Inches</th></tr>` +
    rows.slice(0, 40).map(x => {
      if (x.kind === "run") {
        const r = x.r, [c, label] = outcome[r.outcome] || ["", r.outcome || "?"];
        return `<tr><td>${dt(r.ended)}</td><td><span class="tag ${c}">${esc(label)}</span> agent · ${n(r.pictures)} pic</td>
          <td>${esc((r.orders || []).join(" "))}</td><td class="num">${n(r.actual_in ?? r.planned_in, 1)}</td></tr>`;
      }
      const b = x.b;
      return `<tr><td>${dt(b.at)}</td><td><span class="tag">by hand</span> noticed</td>
        <td>${esc(b.order)} sheet ${n(b.position)}</td><td class="num">${n(b.length_in, 1)}</td></tr>`;
    }).join("") + `</table></div>`;
}
function historyChart(hist) {
  if (!hist || !hist.length) return `<div class="empty">No history yet.</div>`;
  const W = 600, H = 170, padL = 34, padB = 22, padT = 8, plotW = W - padL - 6, plotH = H - padB - padT;
  const max = Math.max(...hist.map(h => h.inches || 0), 1);
  const step = max <= 100 ? 25 : max <= 500 ? 100 : max <= 2000 ? 500 : 1000;
  const top = Math.ceil(max / step) * step;
  const slot = plotW / hist.length, bw = Math.min(16, slot - 2);
  const y = v => padT + plotH - (v / top) * plotH;
  let g = "";
  for (let v = 0; v <= top; v += step) {
    g += `<line x1="${padL}" x2="${W - 6}" y1="${y(v)}" y2="${y(v)}" stroke="#2c3038" stroke-width="1"/>
      <text x="${padL - 6}" y="${y(v) + 4}" fill="#6b717c" font-size="10" text-anchor="end">${n(v)}</text>`;
  }
  hist.forEach((h, i) => {
    const x = padL + i * slot + (slot - bw) / 2, v = h.inches || 0;
    if (v > 0) {
      const yt = y(v), r = Math.min(4, (padT + plotH - yt) / 2);
      g += `<path d="M${x},${padT + plotH} V${yt + r} Q${x},${yt} ${x + r},${yt} H${x + bw - r} Q${x + bw},${yt} ${x + bw},${yt + r} V${padT + plotH} Z" fill="var(--bar)"/>`;
    }
    if (i % 7 === hist.length % 7 || i === hist.length - 1) {
      const d = new Date(h.date + "T12:00:00");
      g += `<text x="${x + bw / 2}" y="${H - 6}" fill="#6b717c" font-size="10" text-anchor="middle">${d.getMonth() + 1}/${d.getDate()}</text>`;
    }
    g += `<rect class="hit" data-i="${i}" x="${padL + i * slot}" y="${padT}" width="${slot}" height="${plotH}" fill="transparent"/>`;
  });
  const tot = hist.reduce((a, h) => ({inches: a.inches + (h.inches || 0), runs: a.runs + (h.runs || 0),
    sheets: a.sheets + (h.sheets || 0), hand: a.hand + (h.by_hand || 0)}), {inches:0, runs:0, sheets:0, hand:0});
  const days = hist.length;
  return `<div class="chart" id="chart"><svg viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Inches ripped by the agent per day, last ${days} days">${g}</svg><div class="tip" id="tip"></div></div>
    <div class="sumrow"><span><b>${n(tot.inches, 0)}</b> in ripped</span><span><b>${n(tot.runs)}</b> run${tot.runs == 1 ? "" : "s"}</span>
      <span><b>${tot.runs ? n(tot.inches / tot.runs, 0) : "–"}</b> in per run</span>
      <span><b>${n(tot.sheets)}</b> sheets by the agent</span><span><b>${n(tot.hand)}</b> found done by hand</span></div>
    <details><summary>Show as a table</summary><table><tr><th>Day</th><th class="num">Inches</th><th class="num">Runs</th>
      <th class="num">Sheets</th><th class="num">By hand</th></tr>` +
    hist.slice().reverse().filter(h => h.inches || h.runs || h.by_hand).map(h => `<tr><td>${esc(h.date)}</td>
      <td class="num">${n(h.inches, 1)}</td><td class="num">${n(h.runs)}</td><td class="num">${n(h.sheets)}</td>
      <td class="num">${n(h.by_hand)}</td></tr>`).join("") + `</table></details>`;
}
function wireChart(hist) {
  const c = document.getElementById("chart"), tip = document.getElementById("tip");
  if (!c) return;
  c.querySelectorAll(".hit").forEach(r => {
    r.addEventListener("mouseenter", () => {
      const h = hist[+r.dataset.i], box = c.getBoundingClientRect(), rb = r.getBoundingClientRect();
      tip.innerHTML = `<b>${esc(new Date(h.date + "T12:00:00").toLocaleDateString([], {weekday:"short", month:"short", day:"numeric"}))}</b>
        ${n(h.inches, 1)} in · ${n(h.runs)} run${h.runs == 1 ? "" : "s"} · ${n(h.sheets)} sheets<br>${n(h.by_hand)} found done by hand`;
      tip.style.left = (rb.left - box.left + rb.width / 2) + "px";
      tip.style.top = (rb.top - box.top + 4) + "px";
      tip.style.display = "block";
    });
    r.addEventListener("mouseleave", () => { tip.style.display = "none"; });
  });
}
function renderDetail(id) {
  const m = DATA.machines.find(x => x.machine_id === id);
  if (!m) return `<a class="back" href="#">← Floor</a><div class="empty">Unknown machine.</div>`;
  const hb = m.hb, [pill, cls] = pillOf(m);
  let h = `<a class="back" href="#">← Floor</a><div class="dhead"><div><div class="name">${esc(m.label)}</div>
    <div class="line">${esc(m.line)}${hb ? " · " + esc(hb.host) + " · agent " + esc(hb.agent_version) + " · heard " + ago(m.seen_s_ago) : ""}</div></div>
    <span class="pill ${cls}">${esc(pill)}</span></div>`;
  if (!hb) return h + `<div class="empty">No heartbeat from this machine yet.</div>`;
  const td = hb.today || {}, q = hb.queue || {}, qd = hb.queue_detail || {};
  h += `<div class="tiles">
    <div class="tile"><b>${n(td.inches_ripped, 1)}</b><span>in ripped today</span></div>
    <div class="tile"><b>${n(td.runs)}</b><span>runs today</span></div>
    <div class="tile"><b>${n(td.sheets)}</b><span>sheets today</span></div>
    <div class="tile"><b>${n(q.sheets)}</b><span>waiting · ${n(q.inches, 0)} in</span></div>
    <div class="tile"><b>${mins(q.oldest_wait_min)}</b><span>oldest wait</span></div></div>`;
  let now;
  if (hb.current_run && m.status === "running") {
    const r = hb.current_run, p = r.progress_pct;
    now = `<div class="big">Ripping ${esc((r.orders || []).join(" "))}</div>
      <div class="sub">${n(r.planned_in, 1)} in planned${p === null ? "" : " · " + n(p) + "%"} · since ${t(hb.since)}</div>
      ${p === null ? "" : `<div class="bar"><i style="width:${Math.max(0, Math.min(100, p))}%"></i></div>`}`;
  } else {
    now = `<div class="big">${esc(m.status === "running" ? hb.state : m.status)}</div><div class="sub">since ${t(hb.since)}</div>`;
  }
  if (hb.last_error) now += `<div class="err">Last error: ${esc(hb.last_error)}</div>`;
  if ((hb.flags || []).length) now += `<div class="flags"><b>Held (${hb.flags.length})</b>` +
    hb.flags.map(f => `<div class="flag">${esc(f.order)}: ${esc(f.reason)}</div>`).join("") + `</div>`;
  h += `<div class="cols"><div class="panel"><h2>Now</h2>${now}</div>
    <div class="panel"><h2>Next run</h2>${nextRunText(qd.next_run)}</div></div>
    <div class="cols"><div class="panel"><h2>Waiting</h2>${queueTable(qd)}</div>
    <div class="panel"><h2>Ripped log</h2>${logTable(hb)}</div></div>
    <div class="panel"><h2>Inches ripped per day · last ${n((hb.history || []).length)} days</h2>${historyChart(hb.history)}</div>`;
  return h;
}

// ---------- TV ----------
function tvCard(m) {
  const hb = m.hb, [pill, cls] = pillOf(m);
  let h = `<div class="tvcard"><div class="tvtop"><div class="tvname">${esc(m.label)}</div>
    <span class="pill tvpill ${cls}">${esc(pill)}</span></div>`;
  if (!hb) return h + `<div class="tvnext">No heartbeat yet.</div></div>`;
  const td = hb.today || {}, q = hb.queue || {}, nr = (hb.queue_detail || {}).next_run;
  if (hb.current_run && m.status === "running") {
    const r = hb.current_run, p = r.progress_pct;
    h += `<div class="tvrun">Ripping <b>${esc((r.orders || []).join(" "))}</b> · ${n(r.planned_in, 0)} in
      ${p === null ? "" : `<div class="tvbar"><i style="width:${Math.max(0, Math.min(100, p))}%"></i></div>`}</div>`;
  }
  h += `<div class="tvnums"><div><b>${n(td.inches_ripped, 0)}</b><span>in ripped today</span></div>
    <div><b>${n(td.runs)}</b><span>runs today</span></div>
    <div><b>${n(q.sheets)}</b><span>waiting · ${n(q.inches, 0)} in</span></div></div>`;
  if (nr && !(hb.current_run && m.status === "running"))
    h += `<div class="tvnext">Next: ${n(nr.planned_in, 0)} in · ${nr.ready ? "ready now" : "at " + t(nr.flush_at)}</div>`;
  if ((hb.flags || []).length) h += `<div class="tvwarn">${n(hb.flags.length)} held, need a look</div>`;
  if (hb.last_error) h += `<div class="tvwarn" style="color:var(--bad)">${esc(hb.last_error)}</div>`;
  return h + `</div>`;
}
function renderTV() {
  const ms = DATA.machines.filter(m => m.enabled);
  return `<div class="tvgrid">${(ms.length ? ms : DATA.machines).map(tvCard).join("")}</div>
    <div class="tvclock">${new Date().toLocaleTimeString([], {hour:"numeric", minute:"2-digit"})} · RIP Automation</div>`;
}

// ---------- order search (every machine) ----------
let SEARCH = {q: null, at: 0, data: null, error: null};
function findOrder(ev) {
  ev.preventDefault();
  const digits = document.getElementById("q").value.replace(/\D/g, "");
  if (digits) location.hash = "q=" + digits;
}
async function loadSearch(q) {
  try {
    const r = await fetch("/api/search?q=" + encodeURIComponent(q), {cache: "no-store"});
    const d = await r.json();
    SEARCH = {q, at: Date.now(), data: d.ok ? d.orders : null, error: d.ok ? null : d.error};
  } catch (e) { SEARCH = {q, at: Date.now(), data: null, error: "can't reach the hub"}; }
  render();
}
function eventLine(e) {
  const on = `<b>${esc(e.label)}</b>`, inch = e.inches ? ` · ${n(e.inches, 1)} in` : "";
  switch (e.kind) {
    case "waiting": return [e.first_seen, `Waiting on ${on} (sheet ${esc(e.ref)}, ${esc(e.detail)}${inch}). Last seen waiting ${dt(e.last_seen)}.`];
    case "ripped": return [e.at || e.first_seen, `<span class="tag ok">ripped</span> on ${on} in run ${esc(e.ref)} (${e.inches ? n(e.inches, 1) + " in, " : ""}${esc(e.detail.replace(" in run", ""))}).`];
    case "gate": return [e.at || e.first_seen, `<span class="tag warn">cancelled at preview</span> on ${on}, run ${esc(e.ref)}. Held for a person.`];
    case "stopped": return [e.at || e.first_seen, `<span class="tag bad">run stopped</span> on ${on}, run ${esc(e.ref)}. Held for a person.`];
    case "over_cap": return [e.at || e.first_seen, `<span class="tag bad">over cap</span> on ${on}, run ${esc(e.ref)}. Delete from the RIP queue by hand.`];
    case "by_hand": return [e.at || e.first_seen, `<span class="tag">by hand</span> found done by hand on ${on} (sheet ${esc(e.ref)}${inch}).`];
    case "held": return [e.first_seen, `<span class="tag warn">held</span> on ${on}: ${esc(e.detail)}. Last seen held ${dt(e.last_seen)}.`];
  }
  return [e.first_seen, `${esc(e.kind)} on ${on}`];
}
function renderSearch(q) {
  if (SEARCH.q !== q || Date.now() - SEARCH.at > 5000) loadSearch(q);
  let h = `<a class="back" href="#">← Floor</a><div class="dhead"><div class="name">Order search: ${esc(q)}</div></div>`;
  if (SEARCH.q !== q) return h + `<div class="sub">Searching…</div>`;
  if (SEARCH.error) return h + `<div class="empty">${esc(SEARCH.error)}</div>`;
  if (!SEARCH.data.length) return h + `<div class="empty">No order starting with ${esc(q)} has been seen on any machine
    since the dashboard started keeping history. Orders done entirely by hand only show up if a machine's agent noticed them.</div>`;
  return h + SEARCH.data.map(o => {
    const evs = o.events.map(eventLine).sort((a, b) => String(a[0]).localeCompare(String(b[0])));
    return `<div class="panel order"><h3>${esc(o.order)}</h3>` +
      evs.map(([w, txt]) => `<div class="ev"><div class="when">${dt(w)}</div><div>${txt}</div></div>`).join("") + `</div>`;
  }).join("");
}

// ---------- refresh ----------
function render() {
  if (!DATA) return;
  const root = document.getElementById("root");
  const m = location.hash.match(/^#m=(.+)$/);
  const q = location.hash.match(/^#q=(\d+)$/);
  if (TV) root.innerHTML = renderTV();
  else if (q) root.innerHTML = renderSearch(q[1]);
  else if (m) {
    root.innerHTML = renderDetail(decodeURIComponent(m[1]));
    const mm = DATA.machines.find(x => x.machine_id === decodeURIComponent(m[1]));
    if (mm && mm.hb) wireChart(mm.hb.history || []);
  } else root.innerHTML = renderFloor();
}
async function refresh() {
  try {
    const r = await fetch("/api/machines", {cache: "no-store"});
    DATA = await r.json();
    render();
    document.getElementById("clock").textContent = "display only · updated " + new Date().toLocaleTimeString();
  } catch (e) {
    document.getElementById("clock").textContent = "can't reach the hub · retrying";
  }
}
window.addEventListener("hashchange", render);
refresh(); setInterval(refresh, 5000);
</script></body></html>"""


if __name__ == "__main__":
    log.info(f"RIP dashboard on :{CONFIG['web_port']} — machines from {CONFIG['machines_path']}")
    app.run(host="0.0.0.0", port=CONFIG["web_port"], debug=False, use_reloader=False)
