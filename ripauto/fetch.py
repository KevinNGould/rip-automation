"""
fetch.py  -  Shopify -> print files for this machine (SPEC §4).

Finds unfulfilled Shopify orders containing this machine's products and
(optionally) downloads each "_Print Ready File" PNG into the queue folder.
Downloading through this link gives the same file as the Build a Gang Sheet
"Gang Sheet" button (label strip + QR) and ticks the app's Downloaded counter.

Ported from trial/fetch_8color.py. Product rules come from config/machines.json
(this machine's line), folders from config/machine.json, the credential from
config/secrets.json, and the ledger lives in state/processed.json.

Usage (from the repo root):
  python -m ripauto.fetch                 list only, downloads nothing
  python -m ripauto.fetch --download      download new sheets to the queue folder
  python -m ripauto.fetch --mark-done     record everything currently listed as
                                          already handled (no downloads)
  python -m ripauto.fetch --from 47910    ignore orders numbered below 47910
                                          (default: start_from_order in machine.json)
  python -m ripauto.fetch --download --limit 1   download just one sheet

--download refuses to run without a floor (start_from_order or --from), so
the backlog of old unfulfilled orders is never pulled in by accident.

Standard library only; needs Python 3.9+.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from . import ledger as ledgers
from .config import ConfigError, load_machine, load_or_exit, load_secrets
from .lock import agent_running
from .sheets import file_stem, print_ready_positions, safe_name, sheet_size

MAX_PAGES = 10  # 50 orders per page
ENABLED_KINDS = ("gang_sheet",)   # image_by_size stays off until verified (SPEC §2)

ORDERS_QUERY = """
query($cursor: String) {
  orders(first: 50, after: $cursor, sortKey: CREATED_AT, reverse: true,
         query: "fulfillment_status:unfulfilled status:open") {
    pageInfo { hasNextPage endCursor }
    nodes {
      name
      legacyResourceId
      createdAt
      %NAMES%
      lineItems(first: 50) {
        nodes { id title variantTitle quantity customAttributes { key value } }
      }
    }
  }
}
"""
# Customer name fields. If the token isn't allowed to read them, the script
# drops them and carries on (the file name just won't include the customer).
NAME_FIELDS = "customer { displayName } billingAddress { name } shippingAddress { name }"


# ---------- floor ----------

def order_floor(cli_from, cfg, downloading):
    """Lowest order number to look at: --from, else start_from_order.

    Downloading with no floor at all is refused (SPEC §4 go-live floor);
    listing is fine."""
    floor = cli_from or cfg.get("start_from_order") or 0
    if downloading and not floor:
        raise ConfigError("set start_from_order in machine.json (or pass --from) before "
                          "downloading, so old unfulfilled orders aren't pulled in")
    return floor


# ---------- product rules ----------

def match_rule(title, rules):
    """The one product rule (from any line) this line item title belongs to, or None.

    Two matching rules is a config error (SPEC §2): refuse rather than guess."""
    t = (title or "").lower()
    hits = [r for r in rules if r["title_contains"].lower() in t]
    if len(hits) > 1:
        raise ConfigError(f"product title {title!r} matches more than one product rule")
    return hits[0] if hits else None


# ---------- Shopify ----------

def get_token(cfg, secrets):
    # Older custom apps hand out a permanent token (shpat_...); use it as-is.
    if (secrets.get("access_token") or "").startswith("shp"):
        return secrets["access_token"]
    # Newer Dev Dashboard apps: trade client ID + secret for a 24-hour token.
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": secrets["client_id"],
        "client_secret": secrets["client_secret"],
    }).encode()
    req = urllib.request.Request(
        f"https://{cfg['shop']}/admin/oauth/access_token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["access_token"]
    except urllib.error.HTTPError as e:
        # The response body can echo request details; report the status only.
        raise ShopifyError(f"Could not get a Shopify token (HTTP {e.code}).") from None


class ShopifyError(Exception):
    pass


def graphql(cfg, token, query, variables):
    req = urllib.request.Request(
        f"https://{cfg['shop']}/admin/api/{cfg['api_version']}/graphql.json",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = json.load(r)
    if payload.get("errors"):
        raise ShopifyError(json.dumps(payload["errors"], indent=2))
    return payload["data"]


def customer_name(order):
    for getter in (lambda o: o["customer"]["displayName"],
                   lambda o: o["shippingAddress"]["name"],
                   lambda o: o["billingAddress"]["name"]):
        try:
            v = getter(order)
            if v:
                return v.strip()
        except (KeyError, TypeError):
            pass
    return ""


def sheets_from_orders(orders, shop_rules, line, min_order=0):
    """This line's items from GraphQL order nodes, oldest order first.

    shop_rules holds every line's rules (config.all_rules), so a title that
    matches two lines is caught here even if the other line is switched off."""
    sheets = []
    for order in orders:
        num = int(re.sub(r"\D", "", order["name"]) or 0)
        if min_order and num < min_order:
            continue
        lines = order["lineItems"]["nodes"]
        positions = print_ready_positions(lines)
        for li in lines:
            rule = match_rule(li["title"], shop_rules)
            if not rule or rule["line"] != line or rule["kind"] not in ENABLED_KINDS:
                continue
            attrs = {a["key"]: a["value"] for a in li["customAttributes"]}
            sheets.append({
                "order": order["name"],
                "order_num": num,
                "order_id": order.get("legacyResourceId", ""),
                "created_at": order.get("createdAt", ""),
                "customer": customer_name(order),
                "line_id": li["id"].rsplit("/", 1)[-1],
                "position": positions.get(li["id"], 0),
                "size": li["variantTitle"] or "",
                "qty": li["quantity"],
                "url": attrs.get("_Print Ready File"),
                "actual_height": attrs.get("_Actual Height"),
                "low_res": attrs.get("_Has Low Resolution", "").lower() == "yes",
            })
    sheets.sort(key=lambda s: (s["order_num"], s["position"], s["line_id"]))
    return sheets


def find_sheets(cfg, token, min_order, note=print):
    """Return this machine's line items with a print-ready file, oldest order first.
    Raises ShopifyError (and urllib errors) rather than exiting, so the agent
    loop can log and try again later."""
    query = ORDERS_QUERY.replace("%NAMES%", NAME_FIELDS)
    orders, cursor = [], None
    for _ in range(MAX_PAGES):
        try:
            data = graphql(cfg, token, query, {"cursor": cursor})["orders"]
        except ShopifyError:
            if NAME_FIELDS not in query:
                raise
            note("Note: this token can't read customer names; file names will leave them out.")
            query = ORDERS_QUERY.replace("%NAMES%", "")
            data = graphql(cfg, token, query, {"cursor": cursor})["orders"]
        orders += data["nodes"]
        if not data["pageInfo"]["hasNextPage"]:
            break
        cursor = data["pageInfo"]["endCursor"]
    return sheets_from_orders(orders, cfg["shop_rules"], cfg["line"], min_order)


# ---------- download ----------

def built_name(sheet):
    """The Build a Gang Sheet app's file name for this sheet (see sheets.file_stem)."""
    return file_stem(sheet["order_num"], sheet["customer"], sheet["order_id"],
                     sheet["size"], sheet["position"], sheet["qty"])


def download(sheet, folder):
    folder.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(sheet["url"], headers={"User-Agent": "OSS-rip-fetch/1.0"})
    part = folder / f"{sheet['order_num']}-{sheet['line_id']}.part"
    with urllib.request.urlopen(req, timeout=300) as r, open(part, "wb") as f:
        server_name = r.headers.get_filename()
        while chunk := r.read(1 << 20):
            f.write(chunk)
    with open(part, "rb") as f:
        is_png = f.read(8) == b"\x89PNG\r\n\x1a\n"
    if not is_png:
        # Usually the app is still making a big sheet; the next fetch tries
        # again. A virus scan may still hold the .part (2026-10-15 22:03,
        # WinError 32); the next download overwrites it anyway.
        try:
            part.unlink()
        except OSError:
            pass
        raise ValueError("downloaded file is not a PNG (yet); trying again next fetch")

    # Prefer the app's own name if its server sends one and it's for this order.
    stem = None
    if server_name:
        candidate = safe_name(Path(server_name).stem)
        if candidate.startswith(f"{sheet['order_num']}-"):
            stem = candidate
    stem = stem or built_name(sheet)
    dest = folder / f"{stem}.png"
    n = 2
    while dest.exists():
        dest = folder / f"{stem} [{n}].png"
        n += 1
    part.replace(dest)
    return dest


def ledger_record(sheet, path, now=None):
    """What the ledger keeps per downloaded sheet (SPEC §4)."""
    size = sheet_size(path, {"_Actual Height": sheet.get("actual_height")}, sheet["size"])
    rnd = lambda v: round(v, 3) if v else None
    return {
        "order": sheet["order"],
        "order_num": sheet["order_num"],
        "line_id": sheet["line_id"],
        "position": sheet["position"],
        "qty": sheet["qty"],
        "size": sheet["size"],
        "length_in": rnd(size["length_in"]),
        "length_source": size["length_source"],
        "width_in": rnd(size["width_in"]),
        "width_source": size["width_source"],
        "low_res": sheet["low_res"],
        "file": str(path),
        "fetched_at": (now or datetime.now()).isoformat(timespec="seconds"),
        "status": ledgers.QUEUED,
        "copies_ripped": 0,
    }


def download_new(cfg, ledger, sheets, *, limit=0, save=None, log=print):
    """Download the sheets the ledger hasn't seen (and that have a file link),
    adding each to the ledger as queued. One bad file doesn't stop the rest.
    Returns the new ledger records."""
    folder = Path(cfg["queue_dir"])
    todo = [s for s in sheets if s["line_id"] not in ledger and s["url"]]
    if limit:
        todo = todo[:limit]
    if todo:
        log(f"Downloading {len(todo)} sheet(s) to {folder}")
    added = []
    for s in todo:
        try:
            path = download(s, folder)
        except Exception as e:  # keep going on one bad file
            log(f"  FAILED {s['order']}: {type(e).__name__}: {e}")
            continue
        rec = ledger_record(s, path)
        ledger[s["line_id"]] = rec
        (save or ledgers.save)(ledger)
        added.append(rec)
        length = (f"{rec['length_in']:.1f} in ({rec['length_source']})"
                  if rec["length_in"] else "length unknown")
        log(f"  ok  {s['order']} -> {path.name}   {length}")
    return added


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="List or download this machine's gang sheets.")
    ap.add_argument("--download", action="store_true", help="download new sheets")
    ap.add_argument("--mark-done", action="store_true", help="record current list as handled")
    ap.add_argument("--from", dest="min_order", type=int, default=0,
                    help="lowest order number to include (default: start_from_order)")
    ap.add_argument("--limit", type=int, default=0, help="max sheets to download this run")
    args = ap.parse_args()

    cfg = load_or_exit(load_machine)
    secrets = load_or_exit(load_secrets)
    try:
        floor = order_floor(args.min_order, cfg, args.download)
    except ConfigError as e:
        sys.exit(f"Not downloading: {e}.")
    if (args.download or args.mark_done) and agent_running():
        sys.exit("The agent loop is running and owns the ledger. Pause it first "
                 "(tools\\pause.cmd) or let it fetch.")
    ledger = ledgers.load()
    try:
        token = get_token(cfg, secrets)
        sheets = find_sheets(cfg, token, floor)
    except ConfigError as e:
        sys.exit(f"machines.json: {e}")
    except ShopifyError as e:
        sys.exit(f"Shopify: {e}")
    new = [s for s in sheets if s["line_id"] not in ledger]

    from_note = f" from #{floor}" if floor else ""
    print(f"\n{cfg['label']} sheets on unfulfilled orders{from_note}: {len(sheets)}   "
          f"not yet handled: {len(new)}\n")
    for s in sheets:
        rec = ledger.get(s["line_id"])
        status = ledgers.status(rec) if rec else "NEW"
        flags = []
        if s["low_res"]:
            flags.append("LOW-RES")
        if s["qty"] > 1:
            flags.append(f"QTY {s['qty']}")
        if s["position"] > 1:
            flags.append(f"SHEET {s['position']} OF ORDER")
        if not s["url"]:
            flags.append("NO FILE LINK")
        print(f"  {status:9}  {s['order']:8} {s['size']:22} {'  '.join(flags)}")

    if args.mark_done:
        stamp = datetime.now().isoformat(timespec="seconds")
        for s in new:
            ledger[s["line_id"]] = {"order": s["order"], "marked_done": stamp}
        ledgers.save(ledger)
        print(f"\nMarked {len(new)} sheet(s) as handled. Nothing was downloaded.")
        return

    if not args.download:
        print("\nList only. Run with --download to fetch the NEW ones.")
        return

    print()
    added = download_new(cfg, ledger, sheets, limit=args.limit)
    if not added:
        print("Nothing new to download.")


if __name__ == "__main__":
    main()
