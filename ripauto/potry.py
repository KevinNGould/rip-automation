"""
potry.py  -  PO-TRY driver (SPEC §7).

rip_pictures() loads one run into PO-TRY as a single job:
    New  ->  Add each picture (one per copy)  ->  F3 intelligent layout, once
    ->  RIP  ->  read the job size in the Print preview (length gate)
    ->  "Print" in the preview (this only starts the rip), or Cancel
    ->  waits for the queue row to finish  ->  back to the layout view.
ripauto.agent decides what goes in a run and keeps the ledger.

It stops at the first thing that doesn't look right. It never touches anything
in the RIP queue screen's toolbar, and the only "Print" it ever presses is the
one inside the Print preview dialog.

Ported from trial/rip_driver.py; PO-TRY paths and the queue folder come from
config/machine.json and the log goes to state/rip_log.txt.

This command only inspects and dry-runs; real rips go through
python -m ripauto.agent --once. Run from an ADMINISTRATOR Command Prompt in
the repo root (PO-TRY runs as admin):
  python -m ripauto.potry --inspect-queue                 read the RIP queue, rip nothing
  python -m ripauto.potry --read-template                 read the open job's colour template
  python -m ripauto.potry --dry-run --limit 1             one file, Cancel at the preview
  python -m ripauto.potry --dry-run --batch --folder F    every file in F as ONE job
      add --pause-at loaded,preview   to stop there for tools\\probe_potry.py
      add --try-multi-add             to try one Add dialog for all files

Hands off the mouse and keyboard while it runs (F3 needs PO-TRY in front).
Ctrl+C in this window stops it.

Setup once:  pip install pywinauto
"""

import argparse
import csv
import io
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from pywinauto import Application, Desktop
except ImportError:          # checked in main(), so tests can import this module
    Application = Desktop = None

from .config import STATE_DIR, load_machine, load_or_exit
from .handsoff import HandsOff, HumanInput
from .planner import planned_length
from .sheets import png_size_in

LOG_PATH = STATE_DIR / "agent.log"         # earlier logs stay in state/rip_log.txt
EXE_NAME = "MtFlatbedPrinterApp.exe"
EXE_PATH = None             # set from machine.json by configure()
PRN_DIR = None
HANDS = HandsOff()            # notices a person using the PC mid-sequence (CRITIQUE A1)

LOAD_TIMEOUT = 300          # picture load
PREVIEW_TIMEOUT = 120       # Print preview dialog to appear
RIP_TIMEOUT = 45 * 60       # a 50 ft sheet took ~9 min; leave plenty of room
SETTLE = 2.0                # pause after each UI step


class Stop(Exception):
    """Something didn't look right; stop cleanly."""


# ---------------- small helpers ----------------

def configure(cfg):
    global EXE_PATH, PRN_DIR
    EXE_PATH = cfg["potry_exe"]
    PRN_DIR = Path(cfg["potry_prnlist"])


LOG_KEEP_DAYS = 30


def rotate_log(path, now=None):
    """If path was last written on an earlier day, rename it agent-YYYY-MM-DD.log
    (that day) and drop dated logs older than LOG_KEEP_DAYS."""
    now = now or datetime.now()
    try:
        written = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return
    if written.date() >= now.date():
        return
    dated = path.with_name(f"{path.stem}-{written:%Y-%m-%d}{path.suffix}")
    try:
        path.replace(dated)
    except OSError:
        return
    for old in path.parent.glob(f"{path.stem}-????-??-??{path.suffix}"):
        try:
            day = datetime.strptime(old.stem[len(path.stem) + 1:], "%Y-%m-%d")
        except ValueError:
            continue
        if (now - day).days > LOG_KEEP_DAYS:
            try:
                old.unlink()
            except OSError:
                pass


def log(msg):
    """Print and append to state/agent.log (rotated daily, SPEC §9). Never
    pass secrets here; file names and order numbers are fine (local only)."""
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    rotate_log(LOG_PATH)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def wait_for(fn, timeout, what, interval=0.5):
    end = time.time() + timeout
    while time.time() < end:
        try:
            result = fn()
            if result:
                return result
        except Exception:
            pass
        time.sleep(interval)
    raise Stop(f"timed out waiting for {what}")


def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def num(text):
    if not text:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(text))
    return float(m.group()) if m else None


# ---------------- pure helpers (unit-tested) ----------------

# Queue statuses seen so far. Add new ones here as they show up.
RIPPING_STATUSES = ("Ripping",)
DONE_STATUSES = ("RIP finished", "Transmission finished")
# Preview length vs planned: off by more than max(this, 1%) either way means a
# picture is missing or doubled. The smallest sheet (6 in + label strip + gap)
# is about 8 in. Kevin's 3-sheet dry runs: planned 65.197, preview 65.237.
LENGTH_TOLERANCE_IN = 3.0
LENGTH_TOLERANCE_PCT = 0.01
MULTI_ADD_CHUNK = 10         # files per Add dialog, to keep the name box short


def rip_state(status, processing):
    """'done', 'working' or 'failed' for a queue row.

    Anything after a finished rip counts as done (an operator may print before
    the agent looks): a status containing 'finish', or Processing at 100 with
    any status other than Ripping. 'fail'/'error' in the status is a failure."""
    s = (status or "").strip()
    low = s.lower()
    if "fail" in low or "error" in low:
        return "failed"
    if s in DONE_STATUSES or "finish" in low:
        return "done"
    if num(processing) == 100 and s not in RIPPING_STATUSES:
        return "done"
    return "working"


_PAIR = re.compile(r"(\d+(?:\.\d+)?)\s*(?:inch|in)?\s*[×xX*]\s*(\d+(?:\.\d+)?)\s*(?:inch|in)?", re.I)


def parse_size_cell(text):
    """'22.500×601.001' (queue Size column) -> (22.5, 601.001), else None."""
    m = _PAIR.search(text or "")
    return (float(m.group(1)), float(m.group(2))) if m else None


def gate_reason(size, cap_in, planned_in, pictures=1, gap_in=0.0):
    """Why the run must not be ripped, or None to go ahead (SPEC §7 step 6).

    planned_in includes gap_in between every picture, but PO-TRY's F3 spacing
    isn't constant: measured 1.02 in per gap on one run and 0.02 on another
    (2026-10-15). So the preview may come out up to (pictures - 1) x gap_in
    shorter than planned and still be fine; tolerance applies on top."""
    if size is None:
        return "preview length unreadable"
    w, l = size
    if l > cap_in or w > cap_in:
        return f"preview shows {l:.1f} in, over {cap_in:g} in"
    if planned_in:
        tolerance = max(LENGTH_TOLERANCE_IN, LENGTH_TOLERANCE_PCT * planned_in)
        no_gaps = planned_in - max(0, pictures - 1) * (gap_in or 0)
        if l < no_gaps - tolerance:
            return (f"preview shows {l:.1f} in but {planned_in:.1f} in was planned "
                    f"(a picture may not have loaded)")
        if l > planned_in + tolerance:
            return (f"preview shows {l:.1f} in but {planned_in:.1f} in was planned "
                    f"(a picture may have loaded twice)")
    return None


def multi_add_chunks(distinct):
    """Groups of 2..MULTI_ADD_CHUNK distinct files for one Add dialog each.
    A lone file left over goes through a normal single Add."""
    chunks = [distinct[i:i + MULTI_ADD_CHUNK] for i in range(0, len(distinct), MULTI_ADD_CHUNK)]
    return [c for c in chunks if len(c) > 1]


def job_name_for(pictures):
    """PO-TRY names a job after its first picture's file name."""
    return Path(pictures[0]).stem


def visible(ctrl):
    try:
        return ctrl.is_visible() and ctrl.rectangle().width() > 0
    except Exception:
        return False


def combo_text(combo):
    """A dropdown's shown text, read without opening it (never texts() or
    expand(): those open the list). None if nothing readable."""
    readers = (lambda: combo.iface_value.CurrentValue,
               lambda: combo.selected_text(),
               lambda: combo.legacy_properties().get("Value"),
               lambda: combo.window_text())
    for read in readers:
        try:
            text = (read() or "").strip()
        except Exception:
            continue
        if text:
            return text
    return None


def read_template(main):
    """The colour template shown in the open job's settings panel: the
    dropdown at its top, beside the save / + / gear buttons (QComboBox in
    SettingsWidget, tools/potry_controls_loaded.txt). Read-only; None if the
    panel isn't on screen or the text can't be read."""
    for panel in main.descendants(class_name="SettingsWidget"):
        if not visible(panel):
            continue
        for combo in panel.descendants(control_type="ComboBox"):
            if visible(combo):
                return combo_text(combo)
    return None


def template_reason(shown, required):
    """None when the job's template is the required one (or no check is set),
    else why the run must not go ahead."""
    if not required:
        return None
    if shown is None:
        return f"couldn't read the colour template (needs {required})"
    if shown.strip().casefold() != required.strip().casefold():
        return f"colour template is {shown!r}, not {required!r}"
    return None


def name_of(ctrl):
    try:
        return (ctrl.element_info.name or "").strip()
    except Exception:
        return ""


def press(ctrl, opens_dialog=False):
    """Press a button.

    Plain buttons are invoked without moving the mouse. Buttons that open a
    dialog (Add, RIP) or act inside one (Print) get a real mouse click instead:
    PO-TRY doesn't answer an invoke until the dialog it opens is closed, which
    would freeze the driver.

    While the hands-off guard is armed (New until Print), a person using the
    PC since the agent's last action stops everything before the click
    (CRITIQUE A1)."""
    what = name_of(ctrl) or "a button"
    if opens_dialog:
        try:
            ctrl.top_level_parent().set_focus()
        except Exception:
            pass
        HANDS.mark()                            # focusing can register as input
        HANDS.check(f"before clicking {what}", need_front=True)
        ctrl.click_input()
    else:
        HANDS.check(f"before pressing {what}")
        try:
            ctrl.invoke()
        except Exception:
            HANDS.check(f"before clicking {what}", need_front=True)
            ctrl.click_input()
    HANDS.mark()
    time.sleep(SETTLE)


def read_value(ctrl):
    for getter in (lambda: ctrl.iface_value.CurrentValue,
                   lambda: ctrl.legacy_properties().get("Value"),
                   lambda: ctrl.window_text()):
        try:
            v = getter()
            if v:
                return str(v)
        except Exception:
            pass
    return None


def load_queue_folder(override, cfg):
    return Path(override) if override else Path(cfg["queue_dir"])


# ---------------- PO-TRY: finding things ----------------

def find_pid():
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    ).stdout
    for row in csv.reader(io.StringIO(out)):
        if row and row[0].lower() == EXE_NAME.lower():
            return int(row[1])
    return None


def connect():
    pid = find_pid()
    if not pid:
        log("PO-TRY isn't running; starting it")
        subprocess.Popen([EXE_PATH], cwd=str(Path(EXE_PATH).parent))
        pid = wait_for(find_pid, 60, "PO-TRY to start")
    app = Application(backend="uia").connect(process=pid, timeout=20)
    main = wait_for(
        lambda: app.window(class_name="MainWindow").wrapper_object(),
        90, "the PO-TRY main window")
    return main, pid


def layout_toolbar(main):
    """The layout-view toolbar (the one holding 'Add'), or None if not showing."""
    adds = [c for c in main.descendants(control_type="Button")
            if name_of(c) == "Add" and visible(c)]
    return adds[0].parent() if adds else None


def tool_button(main, name):
    tb = layout_toolbar(main)
    if tb is None:
        raise Stop("the layout view isn't showing")
    for c in tb.children():
        if name_of(c) == name and visible(c):
            return c
    raise Stop(f"couldn't find the '{name}' button in the layout toolbar")


def view_toggle(main):
    """The >> / << button at the top right that flips layout <-> RIP queue."""
    r = main.rectangle()
    for c in main.descendants(control_type="Button"):
        try:
            cr = c.rectangle()
            if (not name_of(c) and visible(c)
                    and c.element_info.class_name == "QToolButton"
                    and cr.top < r.top + 110 and cr.right > r.right - 90):
                return c
        except Exception:
            continue
    return None


def show_layout(main):
    if layout_toolbar(main):
        return
    toggle = view_toggle(main)
    if not toggle:
        raise Stop("couldn't find the view toggle arrows")
    press(toggle)
    wait_for(lambda: layout_toolbar(main), 15, "the layout view")


def show_queue(main):
    if not layout_toolbar(main):
        return
    toggle = view_toggle(main)
    if not toggle:
        raise Stop("couldn't find the view toggle arrows")
    press(toggle)
    wait_for(lambda: layout_toolbar(main) is None, 15, "the RIP queue view")


def job_tab_count(main):
    return sum(1 for c in main.descendants(control_type="TabItem")
               if re.fullmatch(r"Job\d+", name_of(c)))


def layout_values(main):
    """X / Y / W / H boxes at the bottom left of the layout view."""
    boxes = [w for w in main.descendants() if w.element_info.class_name == "LayoutSettingWidget"]
    if not boxes:
        return {}
    spins = [s for s in boxes[0].descendants(control_type="Spinner") if visible(s)]
    spins.sort(key=lambda s: (s.rectangle().left, s.rectangle().top))
    vals = {}
    for key, s in zip(("X", "Y", "W", "H"), spins):
        vals[key] = read_value(s)
    return vals


def fmt(vals):
    return " ".join(f"{k}={vals.get(k) or '?'}" for k in ("X", "Y", "W", "H"))


def process_windows(pid):
    try:
        return Desktop(backend="uia").windows(process=pid)
    except Exception:
        return []


def inside(ctrl, outer):
    a, b = ctrl.rectangle(), outer.rectangle()
    return b.left <= a.left and a.right <= b.right and b.top <= a.top and a.bottom <= b.bottom


def preview_buttons(w):
    """Print and Cancel inside a Print preview window, or (None, None).

    Only plain dialog buttons (QPushButton) inside the window's own frame count.
    The RIP queue toolbar's real 'Print' is a QToolButton, so it can never match.
    """
    found = {}
    for b in w.descendants(control_type="Button"):
        if (name_of(b) in ("Print", "Cancel") and visible(b)
                and b.element_info.class_name == "QPushButton" and inside(b, w)):
            if name_of(b) in found:          # two of the same: ambiguous, refuse
                return None, None
            found[name_of(b)] = b
    return found.get("Print"), found.get("Cancel")


def find_preview(main, pid):
    cands = list(process_windows(pid))
    try:
        cands += main.descendants(control_type="Window")
    except Exception:
        pass
    main_handle = getattr(main, "handle", None)
    for w in cands:
        try:
            if not visible(w):
                continue
            if w.element_info.class_name == "MainWindow" or (main_handle and w.handle == main_handle):
                continue                      # never treat the main window as the dialog
            names = {name_of(d) for d in w.descendants()}
        except Exception:
            continue
        if "Print preview" in names or name_of(w) == "Print preview":
            p, c = preview_buttons(w)
            if p and c:
                return w
    return None


# ---------------- PO-TRY: RIP queue table ----------------

def read_queue(main):
    """Rows of the RIP queue as dicts keyed by column header, or None if unreadable."""
    els = [e for e in main.descendants() if visible(e) and name_of(e)]
    header_types = ("Header", "HeaderItem")
    headers = {}
    for e in els:
        if e.element_info.control_type in header_types:
            headers[name_of(e)] = e.rectangle()
    if "Status" not in headers or "Job name" not in headers:
        return None
    head_bottom = max(r.bottom for r in headers.values())
    rows = {}
    for e in els:
        if e.element_info.control_type != "DataItem":
            continue
        r = e.rectangle()
        if r.top < head_bottom - 2:
            continue
        cx = (r.left + r.right) // 2
        col = next((h for h, hr in headers.items() if hr.left <= cx <= hr.right), None)
        if not col:
            continue
        key = round(r.top / 6)
        rows.setdefault(key, {})[col] = name_of(e)
    return [rows[k] for k in sorted(rows)]


def find_row(rows, job_names, started):
    """The newest queue row made at or after started whose job name starts with
    any of job_names (PO-TRY names a job after whichever picture came first,
    which with multi-add may not be the first in the run's list)."""
    if isinstance(job_names, str):
        job_names = [job_names]
    best = None
    for row in rows or []:
        name = row.get("Job name", "").replace("\n", "").strip()
        if any(name.startswith(j) for j in job_names):
            made = row.get("Establishing time", "")
            if not made or made >= started:
                best = row
    return best


def wait_for_rip(main, job_names, started_str, started_ts, on_progress=None):
    """Wait until the job's queue row says the rip is done; return that row
    (or {} when only the PrnList fallback could tell)."""
    show_queue(main)
    time.sleep(SETTLE)
    if read_queue(main) is None:
        wait_for_prn(job_names, started_ts)
        return {}

    end = time.time() + RIP_TIMEOUT
    last = None
    seen = False
    while time.time() < end:
        row = find_row(read_queue(main), job_names, started_str)
        if row:
            seen = True
            status = row.get("Status", "")
            prog = row.get("Processing", "")
            err = row.get("Error information", "").strip()
            if err:
                raise Stop(f"PO-TRY reported an error: {err}")
            if (status, prog) != last:
                log(f"  {status or '?'}  {prog}")
                if on_progress and num(prog) is not None:
                    try:
                        on_progress(num(prog))
                    except Exception:
                        pass            # reporting must never stop a rip
                if status and status not in RIPPING_STATUSES + DONE_STATUSES:
                    log(f"  (new queue status seen: {status!r}; add it to potry.py)")
                last = (status, prog)
            state = rip_state(status, prog)
            if state == "failed":
                raise Stop(f"PO-TRY status: {status}")
            if state == "done":
                return row
        elif not seen and time.time() > end - RIP_TIMEOUT + 60:
            raise Stop("the job never appeared in the RIP queue")
        time.sleep(3)
    raise Stop("the rip didn't finish in time")


def wait_for_prn(job_names, started_ts):
    """Fallback when the queue table can't be read: watch PrnList for the .prn
    to appear and stop growing for 30 seconds."""
    log("  can't read the queue table; watching PrnList instead")
    end = time.time() + RIP_TIMEOUT
    last_size, stable_since = None, None
    while time.time() < end:
        prns = [p for j in job_names for p in PRN_DIR.glob(f"{glob_escape(j)}*.prn")
                if p.stat().st_mtime >= started_ts - 5]
        if prns:
            size = max(prns, key=lambda p: p.stat().st_mtime).stat().st_size
            if size == last_size and size > 0:
                if time.time() - stable_since >= 30:
                    return
            else:
                last_size, stable_since = size, time.time()
        time.sleep(3)
    raise Stop("the .prn file never finished")


def glob_escape(text):
    return re.sub(r"([\[\]*?])", r"[\1]", text)


# ---------------- PO-TRY: the steps ----------------

def new_job(main):
    """New job; returns the new tab's name (e.g. 'Job13'), or None if unclear."""
    before = set(job_tab_names(main))
    press(tool_button(main, "New"))
    wait_for(lambda: len(job_tab_names(main)) > len(before), 15, "a new job tab")
    added = [n for n in job_tab_names(main) if n not in before]
    return added[0] if len(added) == 1 else None


# ---------------- job tabs (CRITIQUE A4) ----------------
# Each run opens a job tab (New) and nothing closes it, so tabs full of
# full-resolution sheets pile up. The agent closes only tabs it opened itself
# (names recorded with PO-TRY's process id; a restarted PO-TRY reuses names,
# so the list starts over), and only with a method the probe proved
# (python -m ripauto.potry --probe-tab-close).

AGENT_TABS_PATH = STATE_DIR / "agent_tabs.json"
TAB_CLOSE_PATH = STATE_DIR / "potry_tab_close.json"
TAB_CLOSE_METHODS = ("x_button", "close_button", "context_menu", "middle_click", "ctrl_w", "ctrl_f4")
# The × PO-TRY draws at the right end of each job tab (Kevin's screenshot,
# 2026-10-15) isn't exposed to UI automation, so it's clicked by position:
# this many pixels in from the tab's right edge, vertically centred.
X_OFFSETS = (12, 16, 9, 20)
PROBE_VERBOSE = False         # the probe prints what it sees


def job_tab_names(main):
    return [name_of(c) for c in main.descendants(control_type="TabItem")
            if re.fullmatch(r"Job\d+", name_of(c))]


def job_tab(main, name):
    for c in main.descendants(control_type="TabItem"):
        if name_of(c) == name:
            return c
    return None


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def agent_tabs(pid):
    data = _read_json(AGENT_TABS_PATH, {})
    return list(data.get("tabs", [])) if data.get("pid") == pid else []


def _save_agent_tabs(pid, tabs):
    AGENT_TABS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_TABS_PATH.write_text(json.dumps({"pid": pid, "tabs": tabs}), encoding="utf-8")


def remember_tab(pid, name):
    if name:
        tabs = agent_tabs(pid)
        if name not in tabs:
            _save_agent_tabs(pid, tabs + [name])


def forget_tab(pid, name):
    _save_agent_tabs(pid, [t for t in agent_tabs(pid) if t != name])


def tab_close_method():
    return _read_json(TAB_CLOSE_PATH, {}).get("method")


def tab_close_offset():
    return _read_json(TAB_CLOSE_PATH, {}).get("x_offset")


def answer_save_prompt(main, pid):
    """If closing a tab asks to save, answer No through a button named No /
    Don't save (never anything else). Returns True if it answered."""
    for w in candidate_windows(main, pid) + [main]:
        try:
            for b in w.descendants(control_type="Button"):
                if visible(b) and name_of(b).replace("’", "'") in ("No", "Don't save", "Don't Save"):
                    b.invoke()
                    log("  answered No to 'save changes?'")
                    return True
        except Exception:
            continue
    return False


LAST_X_OFFSET = [None]


def try_close_tab(main, pid, name, method, x_offset=None):
    """One attempt with one method; True if the tab is gone afterwards."""
    tab = job_tab(main, name)
    if tab is None:
        return True
    try:
        if method == "x_button":
            r = tab.rectangle()
            offsets = [x_offset] if x_offset else list(X_OFFSETS)
            for dx in offsets:
                x, y = r.right - dx, (r.top + r.bottom) // 2
                if x <= r.left + (r.right - r.left) // 2:
                    continue                            # never click outside the tab's × end
                HANDS.check(f"before clicking × on {name}", need_front=True)
                tab.click_input(coords=(x - r.left, y - r.top))
                HANDS.mark()
                time.sleep(1.5)
                answer_save_prompt(main, pid)
                time.sleep(0.5)
                if job_tab(main, name) is None:
                    LAST_X_OFFSET[0] = dx
                    return True
                tab = job_tab(main, name)
                if tab is None:
                    return True
                r = tab.rectangle()
            return False
        if method == "close_button":
            buttons = [b for b in tab.descendants(control_type="Button") if visible(b)]
            if not buttons:
                return False
            buttons[0].invoke()
        elif method == "context_menu":
            HANDS.check(f"before right-clicking {name}", need_front=True)
            tab.click_input(button="right")
            HANDS.mark()
            time.sleep(1)
            items = []
            for w in process_windows(pid) + [main]:
                try:
                    items += [m for m in w.descendants(control_type="MenuItem") if visible(m)]
                except Exception:
                    pass
            names = [name_of(m) for m in items]
            if PROBE_VERBOSE:
                print(f"    right-click menu items: {names or 'none appeared'}")
            close = [m for m in items if name_of(m).lower().replace("&", "") in ("close", "close tab", "close job")]
            if len(close) != 1:
                for w in process_windows(pid):          # close the menu again, without the mouse
                    try:
                        w.type_keys("{ESC}")
                    except Exception:
                        pass
                return False
            close[0].invoke()
        elif method == "middle_click":
            HANDS.check(f"before middle-clicking {name}", need_front=True)
            tab.click_input(button="middle")
            HANDS.mark()
        elif method in ("ctrl_w", "ctrl_f4"):
            HANDS.check(f"before selecting {name}")
            try:
                tab.select()
            except Exception:
                return False
            HANDS.check(f"before closing {name}", need_front=True)
            main.type_keys("^w" if method == "ctrl_w" else "^{F4}")
            HANDS.mark()
        else:
            return False
    except HumanInput:
        raise
    except Exception:
        return False
    time.sleep(1.5)
    answer_save_prompt(main, pid)
    time.sleep(0.5)
    return job_tab(main, name) is None


def close_agent_tabs(main, pid, keep=()):
    """Close the job tabs this agent opened (except `keep`), with the proven
    method. Best effort: anything odd just leaves the tab for next time."""
    method = tab_close_method()
    if not method:
        return 0
    closed = 0
    HANDS.arm(pid)
    try:
        for name in agent_tabs(pid):
            if name in keep:
                continue
            if try_close_tab(main, pid, name, method, x_offset=tab_close_offset()):
                forget_tab(pid, name)
                closed += 1
            else:
                log(f"  couldn't close job tab {name} ({method}); leaving it")
                break
    except HumanInput as e:
        log(f"  stopped closing job tabs: {e}")
    finally:
        HANDS.disarm()
    if closed:
        log(f"  closed {closed} job tab(s) the agent had opened")
    return closed


def close_finished_tabs(main, pid):
    """Right after a run is fully ripped (Kevin: too many open jobs make the
    tab bar flaky): close the agent's job tabs, then go back to the RIP queue
    view where people print. Skipped (and retried before the next run) if a
    person is using the PC."""
    if not tab_close_method() or not agent_tabs(pid):
        return
    try:
        show_layout(main)
        close_agent_tabs(main, pid)
    except Stop as e:
        log(f"  couldn't close the finished job's tab ({e}); trying again before the next run")
    finally:
        try:
            show_queue(main)
        except Stop:
            pass


def probe_tab_close(main, pid):
    """For Kevin, once: open a fresh empty job tab and find a way to close it.
    Saves the first method that works to state/potry_tab_close.json."""
    global PROBE_VERBOSE
    PROBE_VERBOSE = True
    show_layout(main)
    try:
        bar = [c for c in main.descendants(control_type="Tab") if visible(c)]
        for b in bar:
            print(f"  tab bar {name_of(b)!r}: " + ", ".join(
                f"{c.element_info.control_type}:{name_of(c)!r}" for c in b.children()))
    except Exception:
        pass
    name = new_job(main)
    if not name:
        print("Couldn't tell which tab New made; nothing tried.")
        return None
    print(f"Made an empty job tab {name}; trying ways to close it…")
    try:
        tab = job_tab(main, name)
        kids = [f"{c.element_info.control_type}:{name_of(c)!r}" for c in tab.descendants()] if tab else []
        print(f"  tab's own controls: {kids or 'none'}")
    except Exception:
        pass
    for method in TAB_CLOSE_METHODS:
        ok = try_close_tab(main, pid, name, method)
        print(f"  {method:13} -> {'CLOSED' if ok else 'no'}")
        if ok:
            TAB_CLOSE_PATH.parent.mkdir(parents=True, exist_ok=True)
            TAB_CLOSE_PATH.write_text(json.dumps({"method": method, "x_offset": LAST_X_OFFSET[0],
                                                  "proven": datetime.now().isoformat(timespec="seconds"),
                                                  "potry_pid": pid}), encoding="utf-8")
            extra = f" (× at {LAST_X_OFFSET[0]} px from the tab's right edge)" if method == "x_button" else ""
            print(f"Saved: the agent will close its own job tabs with '{method}'{extra}.")
            return method
    print("None worked. The agent will keep leaving tabs open; tell Code.")
    return None


def window_key(w):
    try:
        return w.handle or tuple(w.element_info.runtime_id or ())
    except Exception:
        return id(w)


def candidate_windows(main, pid):
    """Top-level windows of PO-TRY plus windows Windows files under the main
    window (owned dialogs often show up there rather than at the top)."""
    found = list(process_windows(pid))
    try:
        found += main.descendants(control_type="Window")
    except Exception:
        pass
    return [w for w in found if w.element_info.class_name not in ("MainWindow", "LayoutSettingFloatingWidget")]


def file_name_box(w):
    edits = [e for e in w.descendants(control_type="Edit") if visible(e)]
    return (next((e for e in edits if e.element_info.automation_id == "1148"), None)
            or next((e for e in edits if "file name" in name_of(e).lower()), None))


def open_add_dialog(main, pid):
    before = {window_key(w) for w in candidate_windows(main, pid)}
    press(tool_button(main, "Add"), opens_dialog=True)

    def new_dialog():
        for w in candidate_windows(main, pid):
            if window_key(w) in before or not visible(w):
                continue
            try:
                if file_name_box(w):
                    return w
            except Exception:
                pass
        return None

    try:
        dlg = wait_for(new_dialog, 30, "the Add picture file dialog")
    except Stop:
        seen = []
        for w in candidate_windows(main, pid):
            try:
                seen.append(f"{w.element_info.class_name}:{name_of(w)!r} visible={visible(w)}")
            except Exception:
                pass
        log("  windows visible to the driver: " + ("; ".join(seen) or "none"))
        raise
    return dlg


def keys(ctrl, text, where, **kw):
    """Type into a PO-TRY control, guarded like press()."""
    HANDS.check(f"before {where}", need_front=True)
    ctrl.type_keys(text, **kw)
    HANDS.mark()


def fill_name_box(dlg, text):
    box = file_name_box(dlg)
    if box is None:
        raise Stop("couldn't find the file name box in the open dialog")
    try:
        box.set_edit_text(text)
    except Exception:
        box.set_focus()
        HANDS.mark()
        keys(box, "^a{BACKSPACE}", "typing a file name")
        keys(box, re.sub(r"([+^%~(){}])", r"{\1}", text), "typing a file name", with_spaces=True)
    time.sleep(0.5)
    return box


def press_open(dlg, box):
    opens = [b for b in dlg.descendants(control_type="Button")
             if b.element_info.automation_id == "1" and visible(b)]
    if opens:
        press(opens[0])
    else:
        keys(box, "{ENTER}", "opening the file")


def add_picture(main, pid, path, meter=None):
    dlg = open_add_dialog(main, pid)
    box = fill_name_box(dlg, str(path))
    start_read = meter()[1] if meter else None
    press_open(dlg, box)
    wait_for(lambda: not visible(dlg), 30, "the file dialog to close")
    wait_loaded(main, meter, start_read, file_bytes([path]))


class MultiAddRefused(Stop):
    """The Add dialog wouldn't take several names; nothing was added."""


def add_pictures_together(main, pid, paths, meter=None):
    """Several files in one Add, the way a person would multi-select (SPEC §7
    step 2). Proven on Kevin's 3-sheet dry run, 2026-10-15 11:17."""
    folder = paths[0].parent
    if any(p.parent != folder for p in paths):
        raise MultiAddRefused("multi-add needs all files in one folder")
    dlg = open_add_dialog(main, pid)
    box = fill_name_box(dlg, str(folder))
    keys(box, "{ENTER}", "opening the folder")    # go to the folder first
    time.sleep(SETTLE)
    box = fill_name_box(dlg, " ".join(f'"{p.name}"' for p in paths))
    start_read = meter()[1] if meter else None
    press_open(dlg, box)
    try:
        wait_for(lambda: not visible(dlg), 30, "the file dialog to close")
    except Stop:
        try:
            keys(dlg, "{ESC}", "closing the Add dialog")
        except Exception:
            pass
        raise MultiAddRefused("the Add dialog didn't take several files at once")
    wait_loaded(main, meter, start_read, file_bytes(paths))


def file_bytes(paths):
    total = 0
    for p in paths:
        try:
            total += Path(p).stat().st_size
        except OSError:
            pass
    return total


def fallback_load_s(nbytes):
    """Fixed wait when PO-TRY's process counters can't be read: 20 s plus
    1 s per 10 MB, so a 320 MB sheet gets ~52 s."""
    return 20 + nbytes / 10e6


def wait_loaded(main, meter=None, start_read=None, nbytes=0):
    """Wait for the pictures just added to be on the canvas. The W box only
    shows that *something* is selected; for a big file it fills in long
    before the picture is there (2026-10-15 14:07), so also wait for PO-TRY
    to have read the files and gone quiet (busy.py)."""
    from .busy import wait_until_loaded
    vals = layout_values(main)
    if num(vals.get("W")) is not None:
        vals = wait_for(lambda: (v := layout_values(main)) and (num(v.get("W")) or 0) > 0 and v,
                        LOAD_TIMEOUT, "the picture to load (W stayed at 0)", interval=1)
    done, note = (wait_until_loaded(meter, nbytes, LOAD_TIMEOUT, start_read=start_read)
                  if meter else (False, "no process meter"))
    if not done:
        wait_s = fallback_load_s(nbytes)
        log(f"  {note}; giving the pictures a fixed {wait_s:.0f} s to load")
        time.sleep(wait_s)
        note = f"waited {wait_s:.0f} s"
    time.sleep(SETTLE)
    log(f"  loaded   {fmt(layout_values(main))}   ({note})")


def short_of_planned(gate):
    """The gate's 'a picture may not have loaded' reason: worth waiting and
    laying out again before giving up."""
    return bool(gate) and "may not have loaded" in gate


LOAD_RETRIES = 2              # Cancel, wait, F3 and RIP again when the preview is short


def intelligent_layout(main):
    HANDS.check("before F3")
    main.set_focus()
    HANDS.mark()
    time.sleep(0.5)
    HANDS.check("before F3", need_front=True)
    main.type_keys("{F3}")
    HANDS.mark()
    time.sleep(3)
    log(f"  F3 done  {fmt(layout_values(main))}")


PREVIEW_DIR = STATE_DIR / "previews"


def capture_preview(preview):
    """Image of the preview's drawing area (the QGraphicsView holding the
    sheet and its size labels), or None if it can't be found or captured."""
    views = [d for d in preview.descendants()
             if d.element_info.class_name == "QGraphicsView" and visible(d)]
    if not views:
        return None
    view = max(views, key=lambda v: v.rectangle().width() * v.rectangle().height())
    try:
        return view.capture_as_image()
    except Exception:
        try:
            from PIL import ImageGrab
            r = view.rectangle()
            return ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
        except Exception:
            return None


OCR_SCALES = (3, 4, 5)        # later zooms only when the first reading is unreadable or off-plan


def read_preview(preview, job, max_width_in, accept=None):
    """(width, length) read off the preview by OCR, or None (SPEC §7 step 6).
    accept(size): does this reading pass the gate? If not, OCR again at the
    next zoom before believing it."""
    from .ocr import read_preview_size
    image = capture_preview(preview)
    if image is None:
        log("  couldn't capture the preview's drawing area")
        return None
    stem = f"{datetime.now():%Y%m%d-%H%M%S}-{job[:40]}"
    size, seen = read_preview_size(image, PREVIEW_DIR, stem, max_width_in,
                                   scales=OCR_SCALES, accept=accept)
    tries = seen.get("tries", [])
    if len(tries) > 1 or size is None:
        for t in tries:
            log(f"  OCR x{t['scale']}  across: {t['across']}   sideways: {t['sideways']}   -> {t['size']}")
    return size


def pause(point):
    input(f"\n  PAUSED at {point}. Run  python tools\\probe_potry.py  in another "
          f"administrator prompt now, then press Enter here to carry on... ")


def rip_pictures(main, pid, pictures, cfg, planned_in, dry_run,
                 pause_at=(), multi_add=False, on_progress=None):
    """Load pictures (one per copy) into one new job, F3, RIP, gate, confirm.

    Returns {"job_name", "preview", "gate", "row", "started", "ended"}.
    gate is None when the length gate passed. When it didn't, or on a dry run,
    Cancel was pressed and nothing was ripped. Raises Stop when something
    doesn't look right; then nobody knows how far PO-TRY got."""
    job = job_name_for(pictures)
    started = datetime.now()
    log(f"=== run of {len(pictures)} picture(s), planned {planned_in:.1f} in, job {job}"
        f"{'   (DRY RUN)' if dry_run else ''}")
    show_layout(main)
    # Job tabs the agent opened on earlier runs (CRITIQUE A4). The last run's
    # tab stays open until now, so a person can look at its layout.
    close_agent_tabs(main, pid)
    # Hands-off guard from New until Print (CRITIQUE A1). Not armed when the
    # dry-run probe pauses for a person to press Enter.
    if not pause_at:
        HANDS.arm(pid)
    try:
        tab = new_job(main)
        remember_tab(pid, tab)
        log(f"  new job ({tab or 'tab name unclear'})")
        from .busy import ProcessMeter
        meter = ProcessMeter(pid)
        pictures_to_add = list(pictures)
        if multi_add:
            distinct = list(dict.fromkeys(pictures))
            for chunk in multi_add_chunks(distinct):
                try:
                    add_pictures_together(main, pid, chunk, meter)
                except MultiAddRefused as e:
                    log(f"  multi-add refused ({e}); adding the rest one at a time")
                    break
                for p in chunk:
                    pictures_to_add.remove(p)
                log(f"  multi-add took {len(chunk)} file(s) in one go")
        for i, p in enumerate(pictures_to_add, 1):
            add_picture(main, pid, p, meter)
            log(f"  added {i}/{len(pictures_to_add)}  {p.name}")
        if "loaded" in pause_at:
            pause("all pictures loaded, before F3")

        # Colour template (Kevin, 2026-10-20): read only, before RIP is pressed.
        # A wrong or unreadable template backs out without ripping; the sheets are held.
        shown = read_template(main)
        wrong = template_reason(shown, cfg.get("required_template"))
        if cfg.get("required_template"):
            log(f"  template: {shown!r}" + (f"   STOP: {wrong}" if wrong else "   ok"))
        if wrong:
            HANDS.disarm()
            close_agent_tabs(main, pid)
            return {"job_name": job, "preview": None, "gate": wrong, "check": "template",
                    "row": None, "started": started.isoformat(timespec="seconds"),
                    "ended": datetime.now().isoformat(timespec="seconds")}

        for attempt in range(LOAD_RETRIES + 1):
            intelligent_layout(main)
            # Mark the time before RIP is pressed, so the queue row's timestamp is
            # never earlier than this whichever moment PO-TRY stamps it.
            started_ts = time.time() - 5
            started_str = datetime.fromtimestamp(started_ts).strftime("%Y-%m-%d %H:%M:%S")
            press(tool_button(main, "RIP"), opens_dialog=True)
            preview = wait_for(lambda: find_preview(main, pid), PREVIEW_TIMEOUT, "the Print preview dialog")
            print_btn, cancel_btn = preview_buttons(preview)
            if not (print_btn and cancel_btn):
                raise Stop("the preview's Print/Cancel buttons couldn't be confirmed")

            gate_for = lambda s: gate_reason(s, cfg["cap_in"], planned_in, len(pictures),
                                             cfg.get("gap_in", 0))
            size = read_preview(preview, job, cfg.get("max_width_in"),
                                accept=lambda s: gate_for(s) is None)
            gate = gate_for(size)
            log(f"  preview size: {size}   planned {planned_in:.1f} in   gate: {gate or 'ok'}")
            if not short_of_planned(gate) or attempt == LOAD_RETRIES:
                break
            # Short: most likely a big picture still loading (2026-10-15 14:07).
            # Cancel, let PO-TRY finish, lay out again and look again.
            press(cancel_btn, opens_dialog=True)
            wait_for(lambda: not find_preview(main, pid), 30, "the preview to close")
            wait_s = 30 * (attempt + 1)
            log(f"  short of planned; pressed Cancel, waiting {wait_s} s more for "
                f"pictures to load, then F3 and RIP again ({attempt + 1}/{LOAD_RETRIES})")
            time.sleep(wait_s)
            wait_loaded(main, meter, None, 0)
        if "preview" in pause_at:
            pause("the Print preview")
        result = {"job_name": job, "preview": size, "gate": gate, "row": None,
                  "started": started.isoformat(timespec="seconds"), "ended": None}

        if dry_run or gate:
            press(cancel_btn, opens_dialog=True)
            HANDS.disarm()
            wait_for(lambda: not find_preview(main, pid), 30, "the preview to close")
            log("  pressed Cancel" + (" (dry run)" if dry_run else f": {gate}"))
            result["ended"] = datetime.now().isoformat(timespec="seconds")
            show_layout(main)
            close_agent_tabs(main, pid)          # nothing ripped; its pictures needn't stay loaded
            return result

        press(print_btn, opens_dialog=True)
    except HumanInput as e:
        HANDS.disarm()
        log(f"  STOPPED: {e}. Backing out; nothing was ripped.")
        back_out(main, pid)
        raise
    finally:
        HANDS.disarm()                       # from here PO-TRY rips on its own
    wait_for(lambda: not find_preview(main, pid), 60, "the preview to close")
    log("  rip started")
    stems = [Path(p).stem for p in dict.fromkeys(pictures)]
    result["row"] = wait_for_rip(main, stems, started_str, started_ts, on_progress)
    actual_name = (result["row"].get("Job name") or "").replace("\n", "").strip()
    if actual_name:
        result["job_name"] = actual_name        # what PO-TRY actually called it
    result["ended"] = datetime.now().isoformat(timespec="seconds")
    log(f"  rip done; queue size {result['row'].get('Size (inch)', '?')}")
    close_finished_tabs(main, pid)
    # Left on the RIP queue view: that's where people print the job from.
    return result


def back_out(main, pid):
    """After a person interrupted: close the Print preview or the Add dialog
    with Cancel, through UI automation only (no mouse, no keys)."""
    try:
        preview = find_preview(main, pid)
        if preview:
            _, cancel = preview_buttons(preview)
            if cancel:
                cancel.invoke()
                log("  pressed Cancel on the preview")
        for w in candidate_windows(main, pid):
            if visible(w) and file_name_box(w):
                for b in w.descendants(control_type="Button"):
                    if b.element_info.automation_id == "2" and visible(b):   # Cancel
                        b.invoke()
                        log("  closed the Add dialog")
                        break
    except Exception as e:                    # best effort: the agent holds nothing either way
        log(f"  couldn't fully back out ({type(e).__name__}); PO-TRY may need a look")


def copies_from_name(path):
    """The (N) at the end of an app-style file name, default 1."""
    m = re.search(r"\((\d+)\)\s*(?:\[\d+\])?$", path.stem)
    return int(m.group(1)) if m else 1


def batch_pictures(files, gap_in):
    """A folder's PNGs as one run: each file x its (N) copies, and the planned length."""
    pictures, inches = [], 0.0
    for f in files:
        n = copies_from_name(f)
        size = png_size_in(f)
        if size is None:
            raise Stop(f"{f.name} has no DPI, can't plan its length")
        pictures += [f] * n
        inches += size[1] * n
    return pictures, planned_length(inches, len(pictures), gap_in)


# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser(
        description="Dry-run or inspect PO-TRY. Real rips go through: python -m ripauto.agent --once")
    ap.add_argument("--dry-run", action="store_true", help="do every step but press Cancel (required)")
    ap.add_argument("--limit", type=int, default=0, help="max files")
    ap.add_argument("--inspect-queue", action="store_true", help="read the RIP queue and exit")
    ap.add_argument("--folder", help="folder of PNGs (default: queue_dir in machine.json)")
    ap.add_argument("--batch", action="store_true",
                    help="load every file (x its (N) copies) into ONE job, like a real run")
    ap.add_argument("--pause-at", default="",
                    help="comma list: loaded, preview. Waits for Enter so the probe can run")
    ap.add_argument("--try-multi-add", action="store_true",
                    help="with --batch: try adding all files in one Add dialog")
    ap.add_argument("--probe-tab-close", action="store_true",
                    help="open an empty job tab and find a way to close it (pause the agent first)")
    ap.add_argument("--read-template", action="store_true",
                    help="read the open job's colour template and exit (reads only; clicks nothing)")
    args = ap.parse_args()

    if not (args.inspect_queue or args.dry_run or args.probe_tab_close or args.read_template):
        sys.exit("This command only dry-runs now. Real rips go through the ledger:\n"
                 "  python -m ripauto.agent --once")
    if args.try_multi_add and not args.batch:
        sys.exit("--try-multi-add needs --batch")
    pause_at = {p.strip() for p in args.pause_at.split(",") if p.strip()}
    if pause_at - {"loaded", "preview"}:
        sys.exit("--pause-at takes: loaded, preview")

    cfg = load_or_exit(load_machine)
    configure(cfg)
    if Application is None:
        sys.exit("pywinauto is missing. Run:  pip install pywinauto")
    if not is_admin():
        sys.exit("Run this from an administrator Command Prompt. PO-TRY runs as admin, "
                 "so Windows hides it from normal programs.")

    main_win, pid = connect()

    if args.probe_tab_close:
        probe_tab_close(main_win, pid)
        return

    if args.read_template:
        required = cfg.get("required_template")
        panels = [p for p in main_win.descendants(class_name="SettingsWidget") if visible(p)]
        print(f"Visible settings panels: {len(panels)}")
        for panel in panels:
            for combo in panel.descendants(control_type="ComboBox"):
                print(f"  dropdown at {combo.rectangle()}  visible={visible(combo)}")
                for label, read in (("value", lambda: combo.iface_value.CurrentValue),
                                    ("selected", lambda: combo.selected_text()),
                                    ("legacy", lambda: combo.legacy_properties().get("Value")),
                                    ("window_text", lambda: combo.window_text())):
                    try:
                        print(f"    {label:12} {read()!r}")
                    except Exception as e:
                        print(f"    {label:12} ({type(e).__name__})")
        shown = read_template(main_win)
        print(f"Template read: {shown!r}   required: {required!r}   "
              f"-> {template_reason(shown, required) or 'ok'}")
        if not panels:
            print("No settings panel on screen: open a job tab (not the RIP queue view) and run this again.")
        return

    if args.inspect_queue:
        was_layout = layout_toolbar(main_win) is not None
        show_queue(main_win)
        time.sleep(SETTLE)
        rows = read_queue(main_win)
        if rows is None:
            print("Couldn't read the queue table. The driver will fall back to watching PrnList.")
        else:
            print(f"Read {len(rows)} row(s):")
            for r in rows:
                print("  ", r.get("Job name", "?")[:40].replace("\n", " "), "|",
                      r.get("Status", "?"), "|", r.get("Processing", "?"))
        if was_layout:
            show_layout(main_win)
        return

    queue = load_queue_folder(args.folder, cfg)
    files = sorted((p for p in queue.glob("*.png")),
                   key=lambda p: (int(num(p.name.split("-")[0]) or 0), p.name))
    if args.limit:
        files = files[: args.limit]
    if not files:
        print(f"Nothing waiting in {queue}")
        return
    log(f"{len(files)} file(s) to dry-run from {queue}{' as one batch' if args.batch else ''}")

    try:
        if args.batch:
            pictures, planned = batch_pictures(files, cfg["gap_in"])
            rip_pictures(main_win, pid, pictures, cfg, planned, dry_run=True,
                         pause_at=pause_at, multi_add=args.try_multi_add)
        else:
            for png in files:
                pictures, planned = batch_pictures([png], cfg["gap_in"])
                rip_pictures(main_win, pid, pictures, cfg, planned, dry_run=True,
                             pause_at=pause_at)
    except HumanInput as e:
        log(f"INTERRUPTED: {e}. Backed out; nothing was ripped.")
        sys.exit(1)
    except Stop as e:
        log(f"STOPPED: {e}")
        sys.exit(1)
    log("All done.")


if __name__ == "__main__":
    main()
