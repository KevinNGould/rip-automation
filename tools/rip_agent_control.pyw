"""
RIP Agent control window. Double-click the desktop shortcut "RIP Agent"
(made by tools\\make_shortcut.ps1), or run:  pythonw tools\\rip_agent_control.pyw

One big button: Pause / Resume / Start. Needs no admin rights.
Logic lives in ripauto/control.py.
"""

import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ripauto import control                      # noqa: E402
from ripauto.config import ConfigError, load_machine   # noqa: E402

COLORS = {  # banner background, text
    "on": ("#1f7a4d", "#ffffff"),
    "pausing": ("#b7791f", "#ffffff"),
    "paused": ("#8a6d1f", "#ffffff"),
    "off": ("#9b2c2c", "#ffffff"),
    "wait": ("#3a3f47", "#ffffff"),
}
BG, CARD, TEXT, DIM = "#14161a", "#1d2026", "#e8eaed", "#8b919c"
REFRESH_MS = 3000


class App:
    def __init__(self, root, cfg):
        self.root, self.cfg = root, cfg
        self.busy = False
        root.title(f"RIP Agent: {cfg.get('label', cfg['machine_id'])}")
        root.configure(bg=BG)
        root.geometry("460x330")
        root.minsize(400, 300)

        self.banner = tk.Frame(root, bg=COLORS["wait"][0], padx=18, pady=14)
        self.banner.pack(fill="x")
        self.headline = tk.Label(self.banner, text="Checking…", font=("Segoe UI", 22, "bold"),
                                 bg=COLORS["wait"][0], fg="#ffffff", anchor="w")
        self.headline.pack(fill="x")
        self.detail = tk.Label(self.banner, text="", font=("Segoe UI", 11), wraplength=420,
                               justify="left", bg=COLORS["wait"][0], fg="#ffffff", anchor="w")
        self.detail.pack(fill="x", pady=(4, 0))

        body = tk.Frame(root, bg=BG, padx=18, pady=14)
        body.pack(fill="both", expand=True)
        self.button = tk.Button(body, text="…", font=("Segoe UI", 14, "bold"), height=1,
                                relief="flat", bg="#2c3038", fg=TEXT, activebackground="#3a3f47",
                                activeforeground=TEXT, cursor="hand2", command=self.act)
        self.button.pack(fill="x", ipady=8)
        self.note = tk.Label(body, text="", font=("Segoe UI", 9), bg=BG, fg=DIM, anchor="w")
        self.note.pack(fill="x", pady=(6, 0))

        links = tk.Frame(body, bg=BG)
        links.pack(fill="x", pady=(8, 6))
        for text, cmd in (("Open dashboard", self.open_dashboard), ("Open log", control.open_log)):
            tk.Button(links, text=text, font=("Segoe UI", 10), relief="flat", bg=CARD, fg=TEXT,
                      activebackground="#2c3038", activeforeground=TEXT, cursor="hand2",
                      command=cmd).pack(side="left", padx=(0, 8), ipadx=8, ipady=3)

        self.log = tk.Label(body, text="", font=("Consolas", 9), bg=CARD, fg=DIM, anchor="nw",
                            justify="left", padx=8, pady=6)
        self.log.pack(fill="both", expand=True)

        self.state = None
        self.refresh()

    # ---- refresh (dashboard call happens off the UI thread) ----
    def refresh(self):
        self.update_once()
        self.root.after(REFRESH_MS, self.refresh)

    def update_once(self):
        def work():
            try:
                result = control.status(self.cfg)
            except Exception as e:                       # never let the window die
                result = ({"kind": "wait", "headline": "?", "detail": f"Couldn't read status: {e}",
                           "button": "Retry", "action": "none"}, False, False)
            lines = control.last_log_lines(4)
            self.root.after(0, lambda: self.show(result, lines))
        threading.Thread(target=work, daemon=True).start()

    def show(self, result, lines):
        info, running, paused = result
        self.state = info
        bg, fg = COLORS.get(info["kind"], COLORS["wait"])
        for w in (self.banner, self.headline, self.detail):
            w.configure(bg=bg)
        self.headline.configure(text=info["headline"], fg=fg)
        self.detail.configure(text=info["detail"], fg=fg)
        if not self.busy:
            self.button.configure(text=info["button"], state="normal")
        self.log.configure(text="\n".join(lines) or "No log yet.")

    # ---- actions ----
    def act(self):
        action = (self.state or {}).get("action")
        if action == "pause":
            control.pause()
            self.note.configure(text="Paused. Any run in progress finishes first.")
        elif action == "resume":
            control.resume()
            self.note.configure(text="Resumed. It picks up within about 10 seconds.")
        elif action == "start":
            self.busy = True
            self.button.configure(text="Starting…", state="disabled")

            def work():
                ok, msg = control.start()
                self.root.after(0, lambda: (self.note.configure(text=msg), setattr(self, "busy", False),
                                            self.update_once()))
            threading.Thread(target=work, daemon=True).start()
            return
        self.update_once()

    def open_dashboard(self):
        base = control.dashboard_base(self.cfg)
        if base:
            webbrowser.open(f"{base}/#m={self.cfg['machine_id']}")


def main():
    try:
        cfg = load_machine()
    except ConfigError as e:
        cfg = {"machine_id": "?", "label": f"config problem: {e}"}
    root = tk.Tk()
    App(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
