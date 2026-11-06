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
  - before a real click: a window from another program is in front, and
    PO-TRY can't be brought back to the front (FrontWindow).

The driver then backs out (Cancel), the sheets stay queued, and the agent
tries again in 5 minutes.

A window in front with no mouse/keyboard input is a program, not a person
(2026-11-05 16:39 -> 11-06 08:28: ~120 runs stopped, one stray job tab
each). So check() first tries to bring PO-TRY back to the front, and only
if that fails raises FrontWindow, naming the program in the way.
"""

import time


def ms_after(a, b):
    """How many ms tick a is after tick b (negative if before), allowing for
    the 32-bit millisecond counter wrapping every ~49 days."""
    d = (a - b) & 0xFFFFFFFF
    return d - 0x100000000 if d >= 0x80000000 else d


class HumanInput(Exception):
    """A person used the PC mid-sequence; the run was backed out, nothing is wrong with it."""


class FrontWindow(HumanInput):
    """Another program's window stays in front of PO-TRY and couldn't be moved
    behind it. Not a person; the run was backed out, nothing is wrong with it."""

    def __init__(self, message, program="?"):
        super().__init__(message)
        self.program = program


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


def foreground_info():
    """(program name, window title) of the window in front, for the log."""
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "nothing", ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    name = f"pid {pid.value}"
    h = kernel32.OpenProcess(0x1000, False, pid.value)       # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        buf, size = ctypes.create_unicode_buffer(512), wintypes.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            name = buf.value.split("\\")[-1]
        kernel32.CloseHandle(h)
    return name, title.value


def bring_to_front(pid, tries=3, wait_s=1.0):
    """Put the process's biggest visible window in front, the way a click on
    its taskbar button would. Clicks nothing, types nothing. True when it's
    in front afterwards."""
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            found.append(((r.right - r.left) * (r.bottom - r.top), hwnd))
        return True
    user32.EnumWindows(each, 0)
    if not found:
        return False
    target = max(found)[1]
    me = kernel32.GetCurrentThreadId()
    for _ in range(tries):
        fg = user32.GetForegroundWindow()
        threads = {user32.GetWindowThreadProcessId(fg, None), user32.GetWindowThreadProcessId(target, None)}
        attached = [t for t in threads - {0, me} if user32.AttachThreadInput(me, t, True)]
        try:
            if user32.IsIconic(target):
                user32.ShowWindow(target, 9)                  # SW_RESTORE
            user32.BringWindowToTop(target)
            user32.SetForegroundWindow(target)
        finally:
            for t in attached:
                user32.AttachThreadInput(me, t, False)
        time.sleep(wait_s)
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(owner))
        if owner.value == pid:
            return True
    return False


class HandsOff:
    GRACE_MS = 300            # input within this long after our own action is ours

    def __init__(self, last_input_ms=None, tick_ms=None, cursor=None, foreground_pid=None,
                 refocus=None, who_is_in_front=None):
        if last_input_ms is None:
            try:
                last_input_ms, tick_ms, cursor, foreground_pid = _win()
                refocus = refocus or bring_to_front
                who_is_in_front = who_is_in_front or foreground_info
            except Exception:                 # not Windows (tests elsewhere): never trips
                last_input_ms = tick_ms = lambda: 0
                cursor = lambda: (0, 0)
                foreground_pid = lambda: 0
        self.last_input_ms, self.tick_ms = last_input_ms, tick_ms
        self.cursor, self.foreground_pid = cursor, foreground_pid
        self.refocus = refocus or (lambda pid: False)
        self.who_is_in_front = who_is_in_front or (lambda: ("?", ""))
        self.armed = False
        self.pid = None
        self.own_until = 0
        self.cursor_at = None
        self.brought_back = None       # (program, title) last moved behind PO-TRY, for the log
        self.log = lambda message: None

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
                # No input and the pointer hasn't moved: a program, not a person.
                program, title = self.who_is_in_front()
                if self.refocus(self.pid):
                    self.mark()
                    self.brought_back = (program, title)
                    self.log(f"  {program} {title[:40]!r} was in front; PO-TRY brought back to the front")
                    return
                raise FrontWindow(f"{program} is in front of PO-TRY and won't go behind it ({where})",
                                  program=program)
