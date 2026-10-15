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
from .planner import planned_length
from .sheets import png_size_in

LOG_PATH = STATE_DIR / "rip_log.txt"
EXE_NAME = "MtFlatbedPrinterApp.exe"
EXE_PATH = None             # set from machine.json by configure()
PRN_DIR = None

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


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
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
LENGTH_SHORTFALL_IN = 12.0   # preview this much shorter than planned -> a picture may be missing


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


def gate_reason(size, cap_in, planned_in):
    """Why the run must not be ripped, or None to go ahead (SPEC §7 step 6)."""
    if size is None:
        return "preview length unreadable"
    w, l = size
    if l > cap_in or w > cap_in:
        return f"preview shows {l:.1f} in, over {cap_in:g} in"
    if planned_in and l < planned_in - max(LENGTH_SHORTFALL_IN, 0.05 * planned_in):
        return (f"preview shows {l:.1f} in but {planned_in:.1f} in was planned "
                f"(a picture may not have loaded)")
    return None


def job_name_for(pictures):
    """PO-TRY names a job after its first picture's file name."""
    return Path(pictures[0]).stem


def visible(ctrl):
    try:
        return ctrl.is_visible() and ctrl.rectangle().width() > 0
    except Exception:
        return False


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
    would freeze the driver."""
    if opens_dialog:
        try:
            ctrl.top_level_parent().set_focus()
        except Exception:
            pass
        ctrl.click_input()
    else:
        try:
            ctrl.invoke()
        except Exception:
            ctrl.click_input()
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


def find_row(rows, job_name, started):
    """The newest queue row for this job made at or after started."""
    best = None
    for row in rows or []:
        if row.get("Job name", "").replace("\n", "").strip().startswith(job_name):
            made = row.get("Establishing time", "")
            if not made or made >= started:
                best = row
    return best


def wait_for_rip(main, job_name, started_str, started_ts):
    """Wait until the job's queue row says the rip is done; return that row
    (or {} when only the PrnList fallback could tell)."""
    show_queue(main)
    time.sleep(SETTLE)
    if read_queue(main) is None:
        wait_for_prn(job_name, started_ts)
        return {}

    end = time.time() + RIP_TIMEOUT
    last = None
    seen = False
    while time.time() < end:
        row = find_row(read_queue(main), job_name, started_str)
        if row:
            seen = True
            status = row.get("Status", "")
            prog = row.get("Processing", "")
            err = row.get("Error information", "").strip()
            if err:
                raise Stop(f"PO-TRY reported an error: {err}")
            if (status, prog) != last:
                log(f"  {status or '?'}  {prog}")
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


def wait_for_prn(job_name, started_ts):
    """Fallback when the queue table can't be read: watch PrnList for the .prn
    to appear and stop growing for 30 seconds."""
    log("  can't read the queue table; watching PrnList instead")
    end = time.time() + RIP_TIMEOUT
    last_size, stable_since = None, None
    while time.time() < end:
        prns = [p for p in PRN_DIR.glob(f"{glob_escape(job_name)}*.prn")
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
    before = job_tab_count(main)
    press(tool_button(main, "New"))
    wait_for(lambda: job_tab_count(main) > before, 15, "a new job tab")


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


def fill_name_box(dlg, text):
    box = file_name_box(dlg)
    if box is None:
        raise Stop("couldn't find the file name box in the open dialog")
    try:
        box.set_edit_text(text)
    except Exception:
        box.set_focus()
        box.type_keys("^a{BACKSPACE}")
        box.type_keys(re.sub(r"([+^%~(){}])", r"{\1}", text), with_spaces=True)
    time.sleep(0.5)
    return box


def press_open(dlg, box):
    opens = [b for b in dlg.descendants(control_type="Button")
             if b.element_info.automation_id == "1" and visible(b)]
    if opens:
        press(opens[0])
    else:
        box.type_keys("{ENTER}")


def add_picture(main, pid, path):
    dlg = open_add_dialog(main, pid)
    press_open(dlg, fill_name_box(dlg, str(path)))
    wait_for(lambda: not visible(dlg), 30, "the file dialog to close")
    wait_loaded(main)


def add_pictures_together(main, pid, paths):
    """Experiment (SPEC §7 step 2): several files in one Add, the way a person
    would multi-select. Only used with --try-multi-add on a dry run."""
    folder = paths[0].parent
    if any(p.parent != folder for p in paths):
        raise Stop("multi-add needs all files in one folder")
    dlg = open_add_dialog(main, pid)
    box = fill_name_box(dlg, str(folder))
    box.type_keys("{ENTER}")                      # go to the folder first
    time.sleep(SETTLE)
    press_open(dlg, fill_name_box(dlg, " ".join(f'"{p.name}"' for p in paths)))
    try:
        wait_for(lambda: not visible(dlg), 30, "the file dialog to close")
    except Stop:
        try:
            dlg.type_keys("{ESC}")
        except Exception:
            pass
        raise Stop("the Add dialog didn't take several files at once")
    wait_loaded(main)


def wait_loaded(main):
    vals = layout_values(main)
    if num(vals.get("W")) is None:
        log("  can't read the W box; giving the picture a fixed 20 s to load")
        time.sleep(20)
        return
    vals = wait_for(lambda: (v := layout_values(main)) and (num(v.get("W")) or 0) > 0 and v,
                    LOAD_TIMEOUT, "the picture to load (W stayed at 0)", interval=1)
    time.sleep(SETTLE)
    log(f"  loaded   {fmt(vals)}")


def intelligent_layout(main):
    main.set_focus()
    time.sleep(0.5)
    main.type_keys("{F3}")
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


def read_preview(preview, job, max_width_in):
    """(width, length) read off the preview by OCR, or None (SPEC §7 step 6)."""
    from .ocr import read_preview_size
    image = capture_preview(preview)
    if image is None:
        log("  couldn't capture the preview's drawing area")
        return None
    stem = f"{datetime.now():%Y%m%d-%H%M%S}-{job[:40]}"
    size, seen = read_preview_size(image, PREVIEW_DIR, stem, max_width_in)
    if size is None:
        log(f"  OCR across: {seen['across']}   sideways: {seen['sideways']}")
    return size


def pause(point):
    input(f"\n  PAUSED at {point}. Run  python tools\\probe_potry.py  in another "
          f"administrator prompt now, then press Enter here to carry on... ")


def rip_pictures(main, pid, pictures, cfg, planned_in, dry_run,
                 pause_at=(), multi_add=False):
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
    new_job(main)
    log("  new job")
    if multi_add:
        distinct = list(dict.fromkeys(pictures))
        add_pictures_together(main, pid, distinct)
        log(f"  multi-add took {len(distinct)} file(s) in one go")
        extra = list(pictures)
        for p in distinct:
            extra.remove(p)
        pictures_to_add = extra
    else:
        pictures_to_add = list(pictures)
    for i, p in enumerate(pictures_to_add, 1):
        add_picture(main, pid, p)
        log(f"  added {i}/{len(pictures_to_add)}  {p.name}")
    if "loaded" in pause_at:
        pause("all pictures loaded, before F3")
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

    size = read_preview(preview, job, cfg.get("max_width_in"))
    gate = gate_reason(size, cfg["cap_in"], planned_in)
    log(f"  preview size: {size}   planned {planned_in:.1f} in   gate: {gate or 'ok'}")
    if "preview" in pause_at:
        pause("the Print preview")
    result = {"job_name": job, "preview": size, "gate": gate, "row": None,
              "started": started.isoformat(timespec="seconds"), "ended": None}

    if dry_run or gate:
        press(cancel_btn, opens_dialog=True)
        wait_for(lambda: not find_preview(main, pid), 30, "the preview to close")
        log("  pressed Cancel" + (" (dry run)" if dry_run else f": {gate}"))
        result["ended"] = datetime.now().isoformat(timespec="seconds")
        show_layout(main)
        return result

    press(print_btn, opens_dialog=True)
    wait_for(lambda: not find_preview(main, pid), 60, "the preview to close")
    log("  rip started")
    result["row"] = wait_for_rip(main, job, started_str, started_ts)
    result["ended"] = datetime.now().isoformat(timespec="seconds")
    log(f"  rip done; queue size {result['row'].get('Size (inch)', '?')}")
    show_layout(main)
    return result


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
    args = ap.parse_args()

    if not args.inspect_queue and not args.dry_run:
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
    except Stop as e:
        log(f"STOPPED: {e}")
        sys.exit(1)
    log("All done.")


if __name__ == "__main__":
    main()
