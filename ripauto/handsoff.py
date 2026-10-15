"""
handsoff.py  -  notice a person using the PC while the agent drives PO-TRY
(CRITIQUE-2026-10-15 A1).

From New until Print is pressed, the agent clicks at screen positions. If a
person grabs the mouse, switches windows or types in that span, a click can
land somewhere unintended (the RIP queue view has a real Print button). So
before every step the driver calls check(). It raises HumanInput when:

  - mouse/keyboard input happened after the agent's own last action
    (the agent's own clicks and keys also count as input, so after each of
    them it calls mark(), and only later input is a person), or
  - the mouse pointer isn't where the agent's last action left it, or
  - before a real click: a window from another program is in front.

The driver then backs out (Cancel), the sheets stay queued, and the agent
tries again in 5 minutes.
"""

import time


def ms_after(a, b):
    """How many ms tick a is after tick b (negative if before), allowing for
    the 32-bit millisecond counter wrapping every ~49 days."""
    d = (a - b) & 0xFFFFFFFF
    return d - 0x100000000 if d >= 0x80000000 else d


class HumanInput(Exception):
    """A person used the PC mid-sequence; the run was backed out, nothing is wrong with it."""


def _win():
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    def last_input_ms():
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        user32.GetLastInputInfo(ctypes.byref(info))
        return info.dwTime

    def tick_ms():
        return kernel32.GetTickCount() & 0xFFFFFFFF

    def cursor():
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def foreground_pid():
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return 0
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    return last_input_ms, tick_ms, cursor, foreground_pid


class HandsOff:
    GRACE_MS = 300            # input within this long after our own action is ours

    def __init__(self, last_input_ms=None, tick_ms=None, cursor=None, foreground_pid=None):
        if last_input_ms is None:
            try:
                last_input_ms, tick_ms, cursor, foreground_pid = _win()
            except Exception:                 # not Windows (tests elsewhere): never trips
                last_input_ms = tick_ms = lambda: 0
                cursor = lambda: (0, 0)
                foreground_pid = lambda: 0
        self.last_input_ms, self.tick_ms = last_input_ms, tick_ms
        self.cursor, self.foreground_pid = cursor, foreground_pid
        self.armed = False
        self.pid = None
        self.own_until = 0
        self.cursor_at = None

    def arm(self, pid):
        self.pid, self.armed = pid, True
        self.mark()

    def disarm(self):
        self.armed = False

    def mark(self):
        """Call right after each of the agent's own actions (click, keys, focus)."""
        time.sleep(0.05)                       # let injected input register first
        self.own_until = (self.tick_ms() + self.GRACE_MS) & 0xFFFFFFFF
        self.cursor_at = self.cursor()

    def check(self, where, need_front=False):
        """Raise HumanInput if a person has used the PC since the last mark()."""
        if not self.armed:
            return
        if ms_after(self.last_input_ms(), self.own_until) > 0:
            raise HumanInput(f"someone used the mouse or keyboard ({where})")
        if self.cursor_at is not None and self.cursor() != self.cursor_at:
            raise HumanInput(f"the mouse moved ({where})")
        if need_front:
            fg = self.foreground_pid()
            if fg and self.pid and fg != self.pid:
                raise HumanInput(f"another window came to the front ({where})")
