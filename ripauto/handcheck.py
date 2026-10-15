"""
handcheck.py  -  was this sheet already taken by hand? (SPEC §4)

People keep downloading and ripping some orders by hand, and Shopify has no
trace of the app's Downloaded counter. So before a sheet is planned, and again
right before its run starts, the agent looks in two places on this PC:

  - PO-TRY's RIP queue: job names (minus the agent's own jobs)
  - the top level of hand_download_dir (the person's Downloads folder)

Both carry Build a Gang Sheet file names, e.g.
  47862-Steve Springer-18942545264955-22in x 600in 50ft-2-New Gang Sheet-(1)
where the number right after the size is the sheet's position in the order.

Outcomes per sheet:
  by_hand  a name with this order# and this position: skip it for good
  manual   can't tell: same order# but position unreadable or different, or a
           place couldn't be read at all. Hold for a person.
  free     neither place mentions the order#: the agent may take it
"""

import re

FREE, BY_HAND, MANUAL = "free", "by_hand", "manual"

# The size slug, then -{position}-. Feet part like "1ft", "6 1-2ft", "1-2ft".
_SIZE_THEN_POS = re.compile(
    r"\d+(?:\.\d+)?\s?in\s?x\s?\d+(?:\.\d+)?\s?in(?: [\d -]*ft)?-(\d+)-", re.I)


def name_position(order_num, name):
    """(is this order's file, position or None) for an app-style file or job name."""
    name = (name or "").replace("\n", "").strip()
    if not name.startswith(f"{order_num}-"):
        return False, None
    m = _SIZE_THEN_POS.search(name)
    return True, (int(m.group(1)) if m else None)


def check_source(order_num, position, names, source):
    """Verdict from one place. names=None means the place couldn't be read."""
    if names is None:
        return MANUAL, f"{source} couldn't be read"
    unreadable = other = None
    for n in names:
        hit, pos = name_position(order_num, n)
        if not hit:
            continue
        if pos == position:
            return BY_HAND, f"{source}: {n}"
        if pos is None:
            unreadable = unreadable or n
        else:
            other = other or n
    if unreadable:
        return MANUAL, f"{source}: {unreadable} (position unreadable)"
    if other:
        return MANUAL, f"{source}: {other} (a different sheet of #{order_num})"
    return FREE, ""


def hand_check(order_num, position, queue_names, download_names):
    """Combine both places: any by_hand wins, then any manual, else free."""
    results = [check_source(order_num, position, queue_names, "PO-TRY queue"),
               check_source(order_num, position, download_names, "Downloads")]
    for verdict in (BY_HAND, MANUAL):
        for v, detail in results:
            if v == verdict:
                return v, detail
    return FREE, ""


def download_names(folder):
    """File names at the top level of folder, or None if it can't be read."""
    try:
        return [p.name for p in folder.iterdir() if p.is_file()]
    except OSError:
        return None


def queue_job_names(rows, own_job_names=()):
    """Job names from PO-TRY queue rows, minus the agent's own jobs.
    rows=None (table unreadable) passes through as None."""
    if rows is None:
        return None
    own = {n.strip() for n in own_job_names}
    names = [(r.get("Job name") or "").replace("\n", "").strip() for r in rows]
    return [n for n in names if n and n not in own]
