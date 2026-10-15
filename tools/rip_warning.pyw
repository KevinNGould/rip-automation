"""
Corner popup: "Auto rip in 60 s". Started by the agent (ripauto/warning.py)
when a run is ready; closes itself when the agent deletes state/rip_pending.json.

Always on top, borderless, and it never takes keyboard focus (so typing
elsewhere isn't interrupted). Buttons write state/rip_response.json.
"""

import json
import sys
import time
import tkinter as tk
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PENDING = REPO / "state" / "rip_pending.json"
RESPONSE = REPO / "state" / "rip_response.json"
W, H, MARGIN = 380, 150, 18
BG, TEXT, DIM, AMBER, BLUE = "#1d2026", "#e8eaed", "#9aa0aa", "#f5b84a", "#4aa3ff"


def corner_xy(corner, screen_w, screen_h, w=W, h=H, margin=MARGIN, taskbar=48):
    """Top-left position for the popup in a screen corner (above the taskbar)."""
    right = screen_w - w - margin
    bottom = screen_h - h - margin - taskbar
    return {"top-left": (margin, margin), "top-right": (right, margin),
            "bottom-left": (margin, bottom)}.get(corner, (right, bottom))


def read_pending():
    try:
        return json.loads(PENDING.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def respond(action):
    tmp = RESPONSE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"action": action, "at": time.time()}), encoding="utf-8")
    tmp.replace(RESPONSE)


def foreground():
    try:
        import ctypes
        return ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        return 0


def no_activate(root, previous):
    """Windows: make the popup a no-activate tool window (off the taskbar, never
    focused by clicks), then hand focus back to whatever had it before the
    popup appeared, so typing elsewhere carries on."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = int(root.wm_frame(), 16)
        GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST = -20, 0x08000000, 0x80, 0x8
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
        if previous and previous != hwnd and user32.GetForegroundWindow() != previous:
            user32.SetForegroundWindow(previous)
    except Exception:
        pass


class Popup:
    def __init__(self, root, previous=0):
        self.root, self.previous = root, previous
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=AMBER)                         # thin amber frame
        inner = tk.Frame(root, bg=BG, padx=14, pady=10)
        inner.pack(fill="both", expand=True, padx=2, pady=2)
        self.title = tk.Label(inner, text="", font=("Segoe UI", 16, "bold"), bg=BG, fg=AMBER, anchor="w")
        self.title.pack(fill="x")
        self.what = tk.Label(inner, text="", font=("Segoe UI", 10), bg=BG, fg=TEXT, anchor="w",
                             justify="left", wraplength=W - 40)
        self.what.pack(fill="x")
        self.hint = tk.Label(inner, text="Hands off the mouse when it starts. PO-TRY only rips; nothing prints.",
                             font=("Segoe UI", 9), bg=BG, fg=DIM, anchor="w", justify="left",
                             wraplength=W - 40)
        self.hint.pack(fill="x", pady=(2, 8))
        row = tk.Frame(inner, bg=BG)
        row.pack(fill="x")
        for text, action, color in (("Rip now", "now", BLUE), ("Wait 5 min", "snooze", "#2c3038"),
                                    ("Pause agent", "pause", "#2c3038")):
            tk.Button(row, text=text, font=("Segoe UI", 10, "bold"), relief="flat", bg=color, fg="#ffffff",
                      activebackground=color, activeforeground="#ffffff", cursor="hand2",
                      command=lambda a=action: self.press(a)).pack(side="left", padx=(0, 6), ipadx=8, ipady=2)
        self.placed = None
        self.tick()

    def press(self, action):
        respond(action)
        self.title.configure(text={"now": "Ripping in a moment…", "snooze": "OK, waiting 5 min",
                                   "pause": "Pausing the agent"}[action])

    def tick(self):
        p = read_pending()
        if p is None:
            self.root.destroy()
            return
        corner = p.get("corner") or "bottom-right"
        if corner != self.placed:
            x, y = corner_xy(corner, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
            self.root.geometry(f"{W}x{H}+{x}+{y}")
            self.placed = corner
            self.root.after(50, lambda: no_activate(self.root, self.previous))
        left = max(0, int(round(p.get("deadline", 0) - time.time())))
        if p.get("message"):
            self.title.configure(text=p["message"] if left == 0 else f"Auto rip in {left} s")
        else:
            self.title.configure(text=f"{'Rip now' if p.get('rip_now') else 'Auto rip'} in {left} s")
        orders = " ".join(p.get("orders") or [])
        if p.get("more_orders"):
            orders += f" +{p['more_orders']} more"
        self.what.configure(text=f"{p.get('planned_in', 0):.0f} in · {p.get('pictures', 0)} sheet"
                                 f"{'' if p.get('pictures') == 1 else 's'} · {orders}")
        if p.get("note"):
            self.hint.configure(text=p["note"], fg=AMBER)
            if left > 60 and not p.get("message"):
                self.title.configure(text=f"Auto rip in {left // 60}:{left % 60:02d}")
        else:
            self.hint.configure(text="Hands off the mouse when it starts. PO-TRY only rips; nothing prints.",
                                fg=DIM)
        self.root.after(250, self.tick)


def main():
    if read_pending() is None:
        sys.exit(0)
    previous = foreground()
    root = tk.Tk()
    Popup(root, previous)
    root.mainloop()


if __name__ == "__main__":
    main()
