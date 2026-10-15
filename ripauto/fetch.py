"""
fetch.py  -  Shopify -> print files for this machine (SPEC §4).

Finds unfulfilled Shopify orders containing this machine's products and
(optionally) downloads each "_Print Ready File" PNG into the queue folder.
Downloading through this link gives the same file as the Build a Gang Sheet
"Gang Sheet" button (label strip + QR) and ticks the app's Downloaded counter.

Ported from trial/fetch_8color.py. Product rules and folders come from
config/machine.json, the credential from config/secrets.json, and the ledger
lives in state/processed.json.

Usage (from the repo root):
  python -m ripauto.fetch                 list only, downloads nothing
  python -m ripauto.fetch --download      download new sheets to the queue folder
  python -m ripauto.fetch --mark-done     record everything currently listed as
                                          already handled (no downloads)
  python -m ripauto.fetch --from 47910    ignore orders numbered below 47910
  python -m ripauto.fetch --download --limit 1   download just one sheet

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

from .config import STATE_DIR, ConfigError, load_machine, load_or_exit, load_secrets

LEDGER_PATH = STATE_DIR / "processed.json"
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


# ---------- ledger ----------

def load_ledger():
    if LEDGER_PATH.exists():
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    return {}


def save_ledger(ledger):
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    tmp.replace(LEDGER_PATH)


# ---------- product rules ----------

def match_rule(title, rules):
    """The one product rule this line item title belongs to, or None.

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
        sys.exit(f"Could not get a Shopify token (HTTP {e.code}).")


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


def find_sheets(cfg, token, min_order):
    """Return this machine's line items with a print-ready file, oldest order first."""
    rules = [r for r in cfg["product_rules"] if r["kind"] in ENABLED_KINDS]
    query = ORDERS_QUERY.replace("%NAMES%", NAME_FIELDS)
    sheets, cursor = [], None
    for _ in range(MAX_PAGES):
        try:
            data = graphql(cfg, token, query, {"cursor": cursor})["orders"]
        except ShopifyError as e:
            if NAME_FIELDS not in query:
                sys.exit(f"Shopify returned errors: {e}")
            print("Note: this token can't read customer names; file names will leave them out.")
            query = ORDERS_QUERY.replace("%NAMES%", "")
            try:
                data = graphql(cfg, token, query, {"cursor": cursor})["orders"]
            except ShopifyError as e2:
                sys.exit(f"Shopify returned errors: {e2}")
        for order in data["nodes"]:
            num = int(re.sub(r"\D", "", order["name"]) or 0)
            if min_order and num < min_order:
                continue
            for li in order["lineItems"]["nodes"]:
                if not match_rule(li["title"], rules):
                    continue
                attrs = {a["key"]: a["value"] for a in li["customAttributes"]}
                url = attrs.get("_Print Ready File")
                sheets.append({
                    "order": order["name"],
                    "order_num": num,
                    "order_id": order.get("legacyResourceId", ""),
                    "customer": customer_name(order),
                    "line_id": li["id"].rsplit("/", 1)[-1],
                    "size": li["variantTitle"] or "",
                    "qty": li["quantity"],
                    "url": url,
                    "low_res": attrs.get("_Has Low Resolution", "").lower() == "yes",
                })
        if not data["pageInfo"]["hasNextPage"]:
            break
        cursor = data["pageInfo"]["endCursor"]
    sheets.sort(key=lambda s: (s["order_num"], s["line_id"]))
    return sheets


# ---------- download ----------

def safe_name(text):
    """Strip only what Windows forbids in file names; keep spaces and ( )."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text)
    return re.sub(r"\s+", " ", text).strip().rstrip(".")


def built_name(sheet):
    """Same pattern Build a Gang Sheet uses:
    order-Customer-orderID-size-qty-(1), e.g.
    47918-Kaitlyn Carter-18943728582971-22in x 12in 1ft-1-(1)
    The app also puts the customer's design name before (1); Shopify doesn't
    have that, so it's left out here."""
    size = sheet["size"].replace("/", "-").replace("(", "").replace(")", "")
    parts = [str(sheet["order_num"]), sheet["customer"], sheet["order_id"],
             size, str(sheet["qty"]), "(1)"]
    return safe_name("-".join(p for p in parts if p))


def download(sheet, folder):
    folder.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(sheet["url"], headers={"User-Agent": "OSS-rip-fetch/1.0"})
    part = folder / f"{sheet['order_num']}-{sheet['line_id']}.part"
    with urllib.request.urlopen(req, timeout=300) as r, open(part, "wb") as f:
        server_name = r.headers.get_filename()
        while chunk := r.read(1 << 20):
            f.write(chunk)
    with open(part, "rb") as f:
        if f.read(8) != b"\x89PNG\r\n\x1a\n":
            part.unlink()
            raise ValueError("downloaded file is not a PNG")

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


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="List or download this machine's gang sheets.")
    ap.add_argument("--download", action="store_true", help="download new sheets")
    ap.add_argument("--mark-done", action="store_true", help="record current list as handled")
    ap.add_argument("--from", dest="min_order", type=int, default=0, help="lowest order number to include")
    ap.add_argument("--limit", type=int, default=0, help="max sheets to download this run")
    args = ap.parse_args()

    cfg = load_or_exit(load_machine)
    secrets = load_or_exit(load_secrets)
    ledger = load_ledger()
    token = get_token(cfg, secrets)
    try:
        sheets = find_sheets(cfg, token, args.min_order)
    except ConfigError as e:
        sys.exit(f"machine.json: {e}")
    new = [s for s in sheets if s["line_id"] not in ledger]

    print(f"\n{cfg['label']} sheets on unfulfilled orders: {len(sheets)}   not yet handled: {len(new)}\n")
    for s in sheets:
        status = "done" if s["line_id"] in ledger else "NEW"
        flags = []
        if s["low_res"]:
            flags.append("LOW-RES")
        if s["qty"] > 1:
            flags.append(f"QTY {s['qty']}")
        if not s["url"]:
            flags.append("NO FILE LINK")
        print(f"  {status:4}  {s['order']:8} {s['size']:22} {'  '.join(flags)}")

    if args.mark_done:
        stamp = datetime.now().isoformat(timespec="seconds")
        for s in new:
            ledger[s["line_id"]] = {"order": s["order"], "marked_done": stamp}
        save_ledger(ledger)
        print(f"\nMarked {len(new)} sheet(s) as handled. Nothing was downloaded.")
        return

    if not args.download:
        print("\nList only. Run with --download to fetch the NEW ones.")
        return

    folder = Path(cfg["queue_dir"])
    todo = [s for s in new if s["url"]]
    if args.limit:
        todo = todo[: args.limit]
    print(f"\nDownloading {len(todo)} sheet(s) to {folder}")
    for s in todo:
        try:
            path = download(s, folder)
        except Exception as e:  # keep going on one bad file
            print(f"  FAILED {s['order']}: {e}")
            continue
        ledger[s["line_id"]] = {
            "order": s["order"],
            "file": str(path),
            "low_res": s["low_res"],
            "downloaded_at": datetime.now().isoformat(timespec="seconds"),
        }
        save_ledger(ledger)
        print(f"  ok  {s['order']} -> {path.name}")


if __name__ == "__main__":
    main()
