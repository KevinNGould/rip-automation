"""
RIP Agent control window. Double-click the desktop shortcut "RIP Agent"
(made by tools\\make_shortcut.ps1), or run:  pythonw tools\\rip_agent_control.pyw

One big button: Pause / Resume / Start. Needs no admin rights.
"Held & queued sheets" lists sheets waiting on a person or a run, and marks
them done by hand or re-queues them (Kevin, 2026-11-02).
Logic lives in ripauto/control.py.
"""

import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox
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
        root.geometry("460x440")
        root.minsize(400, 360)

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
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(8, 0))
        self.rip_btn = tk.Button(row, text="Rip now", font=("Segoe UI", 11, "bold"), relief="flat",
                                 bg="#2f6fd0", fg="#ffffff", activebackground="#2f6fd0",
                                 activeforeground="#ffffff", disabledforeground="#8b919c",
                                 cursor="hand2", command=self.do_rip_now)
        self.rip_btn.pack(side="left", fill="x", expand=True, ipady=4)
        self.restart_btn = tk.Button(row, text="Restart agent", font=("Segoe UI", 10), relief="flat",
                                     bg=CARD, fg=TEXT, activebackground="#2c3038", activeforeground=TEXT,
                                     disabledforeground="#6b717c", cursor="hand2", command=self.do_restart)
        self.restart_btn.pack(side="left", padx=(8, 0), ipady=4, ipadx=8)
        self.note = tk.Label(body, text="", font=("Segoe UI", 9), bg=BG, fg=DIM, anchor="w",
                             justify="left", wraplength=420)
        self.note.pack(fill="x", pady=(6, 0))

        links = tk.Frame(body, bg=BG)
        links.pack(fill="x", pady=(8, 6))
        for text, cmd in (("Open dashboard", self.open_dashboard), ("Open log", control.open_log)):
            tk.Button(links, text=text, font=("Segoe UI", 10), relief="flat", bg=CARD, fg=TEXT,
                      activebackground="#2c3038", activeforeground=TEXT, cursor="hand2",
                      command=cmd).pack(side="left", padx=(0, 8), ipadx=8, ipady=3)

        self.sheets_btn = tk.Button(body, text="Held & queued sheets", font=("Segoe UI", 10),
                                    relief="flat", bg=CARD, fg=TEXT, activebackground="#2c3038",
                                    activeforeground=TEXT, cursor="hand2", command=self.open_sheets)
        self.sheets_btn.pack(fill="x", pady=(0, 6), ipady=3)
        self.sheets_win = None

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
            try:
                rows = control.sheets_for_a_person()
            except Exception:
                rows = []
            self.root.after(0, lambda: (self.show(result, lines), self.show_sheet_count(rows)))
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
        if info.get("rip_now_pending"):
            self.rip_btn.configure(text="Rip now requested…", state="disabled", bg=CARD)
        else:
            ok = info.get("rip_now")
            self.rip_btn.configure(text="Rip now", state="normal" if ok else "disabled",
                                   bg="#2f6fd0" if ok else CARD)
        if info.get("restart_pending"):
            self.restart_btn.configure(text="Restarting…", state="disabled")
        else:
            self.restart_btn.configure(text="Restart agent", state="normal" if running else "disabled")
        self.log.configure(text="\n".join(lines) or "No log yet.")

    def show_sheet_count(self, rows):
        held = sum(1 for r in rows if r["status"] == "held")
        text = "Held & queued sheets"
        if rows:
            text += f"  ({held} held, {len(rows) - held} queued)"
        self.sheets_btn.configure(text=text, bg="#8a6d1f" if held else CARD)

    def open_sheets(self):
        if self.sheets_win is not None and self.sheets_win.win.winfo_exists():
            self.sheets_win.win.lift()
            return
        self.sheets_win = SheetsWindow(self.root, on_change=self.update_once)

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

    def do_rip_now(self):
        control.rip_now()
        self.note.configure(text="Rip now: a 10-second countdown pops up in the corner, then it rips. "
                                 "Hands off the mouse once it starts.")
        self.update_once()

    def do_restart(self):
        self.note.configure(text="Restarting: the agent stops at its next safe point (never mid-run), "
                                 "then this window starts it again.")
        self.restart_btn.configure(text="Restarting…", state="disabled")

        def work():
            ok, msg = control.restart_and_wait()
            self.root.after(0, lambda: (self.note.configure(text=msg), self.update_once()))
        threading.Thread(target=work, daemon=True).start()

    def open_dashboard(self):
        base = control.dashboard_base(self.cfg)
        if base:
            webbrowser.open(f"{base}/#m={self.cfg['machine_id']}")


class SheetsWindow:
    """Held and queued sheets: mark done by hand, or re-queue held ones."""

    def __init__(self, root, on_change):
        self.on_change = on_change
        self.rows = []
        win = self.win = tk.Toplevel(root, bg=BG, padx=14, pady=12)
        win.title("Held & queued sheets")
        win.geometry("760x420")
        win.minsize(560, 300)
        tk.Label(win, text="Held: the agent stopped on it and won't rip it.   Queued: waiting for its run.\n"
                           "Click to pick a sheet; Ctrl-click or Shift-click for several.",
                 font=("Segoe UI", 10), bg=BG, fg=DIM, justify="left", anchor="w").pack(fill="x")
        frame = tk.Frame(win, bg=BG)
        frame.pack(fill="both", expand=True, pady=(8, 8))
        self.list = tk.Listbox(frame, selectmode="extended", font=("Consolas", 10), bg=CARD, fg=TEXT,
                               selectbackground="#2f6fd0", selectforeground="#ffffff",
                               highlightthickness=0, relief="flat", activestyle="none")
        bar = tk.Scrollbar(frame, command=self.list.yview)
        self.list.configure(yscrollcommand=bar.set)
        self.list.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        row = tk.Frame(win, bg=BG)
        row.pack(fill="x")
        style = dict(font=("Segoe UI", 10, "bold"), relief="flat", cursor="hand2", fg="#ffffff",
                     activeforeground="#ffffff")
        tk.Button(row, text="Mark done by hand", bg="#1f7a4d", activebackground="#1f7a4d",
                  command=self.by_hand, **style).pack(side="left", ipadx=10, ipady=4)
        tk.Button(row, text="Re-queue", bg="#2f6fd0", activebackground="#2f6fd0",
                  command=self.requeue, **style).pack(side="left", padx=(8, 0), ipadx=10, ipady=4)
        tk.Button(row, text="Refresh", font=("Segoe UI", 10), relief="flat", bg=CARD, fg=TEXT,
                  activebackground="#2c3038", activeforeground=TEXT, cursor="hand2",
                  command=self.refresh).pack(side="right", ipadx=8, ipady=3)
        self.note = tk.Label(win, text="", font=("Segoe UI", 9), bg=BG, fg=DIM, anchor="w",
                             justify="left", wraplength=720)
        self.note.pack(fill="x", pady=(6, 0))
        self.tick()

    def tick(self):
        if self.win.winfo_exists():
            self.refresh(keep_selection=True)
            self.win.after(5000, self.tick)

    def refresh(self, keep_selection=False):
        picked = set(self.selected_keys()) if keep_selection else set()
        try:
            self.rows = control.sheets_for_a_person()
        except Exception as e:
            self.note.configure(text=f"Couldn't read the ledger: {e}")
            return
        top = self.list.yview()[0]
        self.list.delete(0, "end")
        for i, r in enumerate(self.rows):
            length = f"{r['length_in']:.1f} in" if r["length_in"] else "?"
            self.list.insert("end", f"{r['status']:7} {r['order']:8} {length:>9}  {r['when']:16}  "
                                    f"{r['reason'][:70]}")
            self.list.itemconfigure(i, fg="#f0c674" if r["status"] == "held" else TEXT)
            if r["key"] in picked:
                self.list.selection_set(i)
        if not self.rows:
            self.list.insert("end", "Nothing held or queued.")
        self.list.yview_moveto(top)

    def selected(self):
        return [self.rows[i] for i in self.list.curselection() if i < len(self.rows)]

    def selected_keys(self):
        return [r["key"] for r in self.selected()]

    def by_hand(self):
        rows = self.selected()
        if not rows:
            self.note.configure(text="Pick one or more sheets first.")
            return
        orders = " ".join(dict.fromkeys(r["order"] for r in rows))
        if messagebox.askyesno("Mark done by hand",
                               f"Mark {len(rows)} sheet(s) as done by hand?\n\n{orders}\n\n"
                               "The agent will never rip them.", parent=self.win):
            self.act("by_hand", rows)

    def requeue(self):
        rows = [r for r in self.selected() if r["status"] == "held"]
        if not rows:
            self.note.configure(text="Re-queue is for held sheets: pick one or more held sheets.")
            return
        orders = " ".join(dict.fromkeys(r["order"] for r in rows))
        if messagebox.askyesno("Re-queue",
                               f"Put {len(rows)} held sheet(s) back in the queue?\n\n{orders}\n\n"
                               "The agent rips them in its next run. Only do this if they haven't been "
                               "ripped by hand: once a hand-ripped job is cleared from PO-TRY's queue, "
                               "the agent can't tell and would rip it again.", parent=self.win):
            self.act("requeue", rows)

    def act(self, action, rows):
        ok, msg = control.ledger_action(action, [r["key"] for r in rows])
        self.note.configure(text=msg)
        self.list.selection_clear(0, "end")
        self.refresh()
        self.on_change()


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
