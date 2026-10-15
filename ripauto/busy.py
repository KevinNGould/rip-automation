"""Is PO-TRY still loading? Read off its process counters, never its window.

PO-TRY loads a picture in the background after the Add dialog closes; the W
box shows a value long before a big picture is on the canvas (2026-10-15
14:07: a 320 MB, 270 in sheet wasn't there when F3 and RIP were pressed).
Two signals that it's done: it has read about as many bytes as the files
hold, and its CPU has gone quiet (idle PO-TRY measured at 0-2% of a core).
"""
import ctypes
import time

QUIET_CPU_PCT = 8.0       # % of one core; idle PO-TRY measured 0-2%
QUIET_FOR_S = 4           # this many quiet seconds in a row
STALL_S = 15              # reads short of the target but nothing happening this long: give up waiting
READ_SHARE = 0.9          # PNG bytes read before "read it all" counts


class _IO(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _FT(ctypes.Structure):
    _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]


class ProcessMeter:
    """CPU seconds and bytes read so far by one process (None when unreadable)."""

    def __init__(self, pid):
        self.k = ctypes.windll.kernel32
        self.h = self.k.OpenProcess(0x1000, False, pid)     # PROCESS_QUERY_LIMITED_INFORMATION

    def __call__(self):
        if not self.h:
            return None, None
        ft = [_FT() for _ in range(4)]
        cpu = None
        if self.k.GetProcessTimes(self.h, *(ctypes.byref(f) for f in ft)):
            cpu = sum((f.hi << 32 | f.lo) / 1e7 for f in ft[2:])
        io = _IO()
        read = io.read_bytes if self.k.GetProcessIoCounters(self.h, ctypes.byref(io)) else None
        return cpu, read

    def close(self):
        if self.h:
            self.k.CloseHandle(self.h)
            self.h = None

    __del__ = close


def wait_until_loaded(sample, expect_bytes, timeout, *, start_read=None,
                      sleep=time.sleep, clock=time.monotonic):
    """Wait until the process has read ~expect_bytes since start_read and its
    CPU has been quiet for QUIET_FOR_S seconds. sample() -> (cpu_s, read_bytes).

    Returns (done, note): done False when the counters couldn't be read or
    the wait timed out; note says what was seen, for the log."""
    t0 = clock()
    cpu, read = sample()
    if cpu is None:
        return False, "can't read PO-TRY's process counters"
    base = start_read if start_read is not None else read
    target = expect_bytes * READ_SHARE
    quiet = stall = 0.0
    last_t, last_cpu, last_read = t0, cpu, read
    while clock() - t0 < timeout:
        sleep(1)
        now = clock()
        cpu, read = sample()
        if cpu is None:
            return False, "lost PO-TRY's process counters"
        dt = max(now - last_t, 1e-6)
        pct = 100 * (cpu - last_cpu) / dt
        quiet = quiet + dt if pct < QUIET_CPU_PCT else 0.0
        reads_done = read is None or read - base >= target
        stall = stall + dt if (read == last_read and pct < QUIET_CPU_PCT) else 0.0
        last_t, last_cpu, last_read = now, cpu, read
        got = f"{(read - base) / 1e6:.0f} of {expect_bytes / 1e6:.0f} MB read" if read is not None else "reads unknown"
        if reads_done and quiet >= QUIET_FOR_S:
            return True, f"{got}, quiet after {now - t0:.0f} s"
        if not reads_done and stall >= STALL_S:
            return True, f"{got} and nothing happening for {STALL_S} s; going on (the length check backs this up)"
    return False, f"still busy after {timeout} s"
