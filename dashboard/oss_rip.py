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
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

settings = load_settings()
_lock = threading.Lock()
latest = load_json(CONFIG["state_path"], {})     # machine_id -> {"received": epoch, "hb": {...}}


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
def index():
    return render_template_string(PAGE)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RIP Automation</title>
<style>
  :root { --bg:#14161a; --card:#1d2026; --line:#2c3038; --text:#e8eaed; --dim:#8b919c;
          --ok:#3ecf8e; --busy:#4aa3ff; --warn:#f5b84a; --bad:#ff5d5d; --off:#5c6370; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:15px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
  header { display:flex; justify-content:space-between; align-items:baseline;
           padding:16px 20px; border-bottom:1px solid var(--line); }
  h1 { font-size:20px; margin:0; letter-spacing:.3px; }
  .sub { color:var(--dim); font-size:13px; }
  main { display:grid; grid-template-columns:repeat(auto-fill, minmax(320px, 1fr));
         gap:16px; padding:16px 20px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
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
  @media (max-width:420px) { header, main { padding-left:16px; padding-right:16px; } }
</style></head>
<body>
<header><h1>RIP Automation</h1><span class="sub" id="clock">display only</span></header>
<main id="cards"><div class="sub">Loading…</div></main>
<script>
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const n = (v, d=0) => v === null || v === undefined ? "–" : Number(v).toLocaleString(undefined, {maximumFractionDigits:d});
function ago(s) { if (s === null) return "never"; if (s < 60) return s + " s ago";
  if (s < 3600) return Math.round(s/60) + " min ago"; return Math.round(s/3600) + " h ago"; }
function card(m) {
  const hb = m.hb;
  let pill = m.status, cls = m.status;
  if (!m.enabled && m.status === "never") { pill = "switched off"; cls = "switched-off"; }
  else if (m.status === "running" && hb) {
    pill = hb.state; cls = {ripping:"ripping", holding:"holding", error:"error"}[hb.state] || "running"; }
  let h = `<div class="card ${m.enabled ? "" : "off"}"><div class="top"><div>
    <div class="name">${esc(m.label)}</div><div class="line">${esc(m.line)}</div></div>
    <span class="pill ${cls}">${esc(pill)}</span></div>`;
  if (hb) {
    const t = hb.today || {}, q = hb.queue || {};
    if (hb.current_run && m.status === "running") {
      const r = hb.current_run, p = r.progress_pct;
      h += `<div class="run"><b>Run</b> ${esc((r.orders || []).join(" "))}
        <div class="sub">${n(r.planned_in, 1)} in planned${p === null ? "" : " · " + n(p) + "%"}</div>
        ${p === null ? "" : `<div class="bar"><i style="width:${Math.max(0, Math.min(100, p))}%"></i></div>`}</div>`;
    }
    h += `<div class="stats">
      <div class="stat"><b>${n(t.inches_ripped, 1)}</b><span>in ripped today</span></div>
      <div class="stat"><b>${n(t.runs)}</b><span>runs · ${n(t.sheets)} sheets</span></div>
      <div class="stat"><b>${n(q.sheets)}</b><span>waiting · ${n(q.inches, 0)} in · ${n(q.oldest_wait_min)} min</span></div></div>`;
    if ((hb.flags || []).length) {
      h += `<div class="flags"><b>Held (${hb.flags.length})</b>` +
        hb.flags.slice(0, 8).map(f => `<div class="flag">${esc(f.order)} — ${esc(f.reason)}</div>`).join("") +
        (hb.flags.length > 8 ? `<div class="sub">…and ${hb.flags.length - 8} more</div>` : "") + `</div>`;
    }
    if (hb.last_error) h += `<div class="err">Last error: ${esc(hb.last_error)}</div>`;
    h += `<div class="foot">${esc(hb.host)} · agent ${esc(hb.agent_version)} · heard ${ago(m.seen_s_ago)}</div>`;
  } else if (m.enabled) {
    h += `<div class="foot">No heartbeat yet.</div>`;
  }
  return h + `</div>`;
}
async function refresh() {
  try {
    const r = await fetch("/api/machines", {cache: "no-store"});
    const d = await r.json();
    document.getElementById("cards").innerHTML = d.machines.map(card).join("");
    document.getElementById("clock").textContent = "display only · updated " + new Date().toLocaleTimeString();
  } catch (e) {
    document.getElementById("clock").textContent = "can't reach the hub · retrying";
  }
}
refresh(); setInterval(refresh, 5000);
</script></body></html>"""


if __name__ == "__main__":
    log.info(f"RIP dashboard on :{CONFIG['web_port']} — machines from {CONFIG['machines_path']}")
    app.run(host="0.0.0.0", port=CONFIG["web_port"], debug=False, use_reloader=False)
