"""
lock.py  -  one agent loop at a time (state/agent.lock holds its process id).

The loop owns the ledger while it runs, so `fetch --download`, `--mark-done`
and `agent --once` refuse to run alongside it. A lock left by a process that
has died is treated as stale and taken over.
"""

import os
import sys

from .config import STATE_DIR

LOCK_PATH = STATE_DIR / "agent.lock"


def pid_alive(pid):
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            k32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259                        # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def lock_holder(path=LOCK_PATH):
    """Process id holding the lock, or None (no lock, or a stale one)."""
    try:
        pid = int(path.read_text().strip() or 0)
    except (OSError, ValueError):
        return None
    return pid if pid_alive(pid) and pid != os.getpid() else None


def agent_running(path=LOCK_PATH):
    return lock_holder(path) is not None


class AgentLock:
    """with AgentLock(): ...  Raises RuntimeError if another loop holds it."""

    def __init__(self, path=LOCK_PATH):
        self.path = path

    def __enter__(self):
        holder = lock_holder(self.path)
        if holder:
            raise RuntimeError(f"another agent loop is running (process {holder})")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        try:
            if self.path.read_text().strip() == str(os.getpid()):
                self.path.unlink()
        except OSError:
            pass
        return False
