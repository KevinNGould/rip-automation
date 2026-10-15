"""
probe_potry.py  -  read-only look inside PO-TRY's windows.

Lists every window and control that Windows automation can see in a running
MtFlatbedPrinterApp.exe, using both automation backends, and writes the
result to potry_controls.txt next to this script.

It does NOT click, type, or change anything in PO-TRY.

Setup once:   pip install pywinauto
Run:          open PO-TRY normally, then  python probe_potry.py
"""

import csv
import io
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from pywinauto import Application, Desktop
except ImportError:
    sys.exit("pywinauto is missing. Run:  pip install pywinauto")

EXE_NAME = "MtFlatbedPrinterApp.exe"
OUT = Path(__file__).resolve().with_name("potry_controls.txt")


def find_pid():
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    ).stdout
    for row in csv.reader(io.StringIO(out)):
        if row and row[0].lower() == EXE_NAME.lower():
            return int(row[1])
    return None


def describe(ctrl):
    info = ctrl.element_info
    parts = [
        f"type={getattr(info, 'control_type', None) or '-'}",
        f"class={info.class_name or '-'}",
        f"name={info.name!r}",
    ]
    auto_id = getattr(info, "automation_id", None)
    if auto_id:
        parts.append(f"id={auto_id!r}")
    try:
        r = ctrl.rectangle()
        parts.append(f"rect=({r.left},{r.top},{r.right},{r.bottom})")
    except Exception:
        pass
    return "  ".join(parts)


def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def dump_backend(pid, backend, lines):
    lines.append(f"\n================ backend: {backend} ================")
    try:
        app = Application(backend=backend).connect(process=pid, timeout=10)
        windows = app.windows()
    except Exception as e:
        lines.append(f"connect by process failed: {e}")
        lines.append("trying the desktop window list instead...")
        try:
            windows = Desktop(backend=backend).windows(process=pid)
        except Exception as e2:
            lines.append(f"desktop route failed too: {e2}")
            return
    lines.append(f"top-level windows: {len(windows)}")
    for w in windows:
        lines.append(f"\n--- WINDOW  {describe(w)}")
        try:
            kids = w.descendants()
        except Exception as e:
            lines.append(f"    could not list controls: {e}")
            continue
        named = sum(1 for k in kids if k.element_info.name)
        lines.append(f"    controls: {len(kids)}   with a name: {named}")
        for k in kids:
            lines.append("    " + describe(k))


def main():
    pid = find_pid()
    if not pid:
        sys.exit(f"{EXE_NAME} isn't running. Open PO-TRY first, then run this again.")
    admin = is_admin()
    lines = [f"PO-TRY control probe  {datetime.now():%Y-%m-%d %H:%M:%S}  pid={pid}  "
             f"running as admin: {'yes' if admin else 'NO'}"]
    if not admin:
        print("Note: this prompt is NOT running as administrator. If PO-TRY is, "
              "Windows will hide its controls. Re-run from an admin Command Prompt.")
    for backend in ("uia", "win32"):
        dump_backend(pid, backend, lines)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT}  ({len(lines)} lines)")


if __name__ == "__main__":
    main()
