"""
potry.py  -  PO-TRY driver (SPEC §7).

Takes each PNG waiting in the queue folder (the one ripauto.fetch downloads
into) and, one at a time:
    New  ->  Add (pick the file)  ->  F3 intelligent layout, once
    ->  RIP  ->  "Print" in the Print preview dialog (this only starts the rip)
    ->  waits for "RIP finished"  ->  moves the PNG into  <queue>\\ripped
then goes back to the layout view for the next one.

It stops at the first thing that doesn't look right and leaves that file where
it is. It never touches anything in the RIP queue screen's toolbar, and the
only "Print" it ever presses is the one inside the Print preview dialog.

Ported from trial/rip_driver.py; PO-TRY paths and the queue folder come from
config/machine.json and the log goes to state/rip_log.txt.

Run from an ADMINISTRATOR Command Prompt in the repo root (PO-TRY runs as admin):
  python -m ripauto.potry --inspect-queue        read the RIP queue table, rip nothing
  python -m ripauto.potry --dry-run --limit 1    every step, but Cancel instead of Print
  python -m ripauto.potry --limit 1              rip one sheet for real
  python -m ripauto.potry                        rip everything in the queue folder

Hands off the mouse and keyboard while it runs (F3 needs PO-TRY in front).
Ctrl+C in this window stops it.

Setup once:  pip install pywinauto
"""

import argparse
import csv
import io
import re
import shutil
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


def find_row(rows, order_prefix, started):
    best = None
    for row in rows or []:
        if row.get("Job name", "").replace("\n", "").startswith(order_prefix):
            made = row.get("Establishing time", "")
            if not made or made >= started:
                best = row
    return best


def wait_for_rip(main, order_prefix, started_str, started_ts):
    show_queue(main)
    time.sleep(SETTLE)
    if read_queue(main) is None:
        return wait_for_prn(order_prefix, started_ts)

    end = time.time() + RIP_TIMEOUT
    last = None
    seen = False
    while time.time() < end:
        row = find_row(read_queue(main), order_prefix, started_str)
        if row:
            seen = True
            status = row.get("Status", "")
            prog = row.get("Processing", "")
            err = row.get("Error information", "").strip()
            if err:
                raise Stop(f"PO-TRY reported an error: {err}")
            if (status, prog) != last:
                log(f"  {status or '?'}  {prog}")
                last = (status, prog)
            if "finish" in status.lower():
                return
        elif not seen and time.time() > end - RIP_TIMEOUT + 60:
            raise Stop("the job never appeared in the RIP queue")
        time.sleep(3)
    raise Stop("the rip didn't finish in time")


def wait_for_prn(order_prefix, started_ts):
    """Fallback when the queue table can't be read: watch PrnList for the .prn
    to appear and stop growing for 30 seconds."""
    log("  can't read the queue table; watching PrnList instead")
    end = time.time() + RIP_TIMEOUT
    last_size, stable_since = None, None
    while time.time() < end:
        prns = [p for p in PRN_DIR.glob(f"{order_prefix}*.prn")
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


def add_picture(main, pid, path):
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
    box = file_name_box(dlg)
    if box is None:
        raise Stop("couldn't find the file name box in the open dialog")
    try:
        box.set_edit_text(str(path))
    except Exception:
        box.set_focus()
        box.type_keys("^a{BACKSPACE}")
        box.type_keys(re.sub(r"([+^%~(){}])", r"{\1}", str(path)), with_spaces=True)
    time.sleep(0.5)
    opens = [b for b in dlg.descendants(control_type="Button")
             if b.element_info.automation_id == "1" and visible(b)]
    if opens:
        press(opens[0])
    else:
        box.type_keys("{ENTER}")
    wait_for(lambda: not visible(dlg), 30, "the file dialog to close")

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


def rip_one(main, pid, png, dry_run, done_dir):
    order_prefix = png.name.split("-", 1)[0] + "-"
    log(f"=== {png.name}{'   (DRY RUN)' if dry_run else ''}")
    show_layout(main)
    new_job(main)
    log("  new job")
    add_picture(main, pid, png)
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
    if dry_run:
        press(cancel_btn, opens_dialog=True)
        wait_for(lambda: not find_preview(main, pid), 30, "the preview to close")
        log("  preview found, pressed Cancel. Dry run complete for this sheet.")
        return
    press(print_btn, opens_dialog=True)
    wait_for(lambda: not find_preview(main, pid), 60, "the preview to close")
    log("  rip started")
    wait_for_rip(main, order_prefix, started_str, started_ts)
    done_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(png), str(done_dir / png.name))
    log("  RIP finished; moved file to ripped\\")
    show_layout(main)


# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser(description="Load queued sheets into PO-TRY and rip them.")
    ap.add_argument("--dry-run", action="store_true", help="do every step but press Cancel")
    ap.add_argument("--limit", type=int, default=0, help="max sheets this run")
    ap.add_argument("--inspect-queue", action="store_true", help="read the RIP queue and exit")
    ap.add_argument("--folder", help="queue folder (default: queue_dir in machine.json)")
    args = ap.parse_args()

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
    log(f"{len(files)} sheet(s) to {'dry-run' if args.dry_run else 'rip'} from {queue}")

    for png in files:
        try:
            rip_one(main_win, pid, png, args.dry_run, queue / "ripped")
        except Stop as e:
            log(f"STOPPED on {png.name}: {e}")
            log("That file is still in the queue folder. Nothing further was loaded.")
            sys.exit(1)
    log("All done.")


if __name__ == "__main__":
    main()
