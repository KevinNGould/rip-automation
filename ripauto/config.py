"""
Loads the config files (SPEC §2, §3):

  config/machines.json  committed: lines, their product rules, and the machines
                        in each (labels, enabled). Shared by every PC.
  config/machine.json   which machine this PC is, plus its local settings
                        (folders, limits). No secrets.
  config/secrets.json   the Shopify credential only. Never logged, never sent
                        anywhere but Shopify.

machine.json and secrets.json are git-ignored. Copy the *.example.json files to start.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO / "config"
STATE_DIR = REPO / "state"
MACHINES_PATH = CONFIG_DIR / "machines.json"
MACHINE_PATH = CONFIG_DIR / "machine.json"
SECRETS_PATH = CONFIG_DIR / "secrets.json"

MACHINE_REQUIRED = ("machine_id", "queue_dir", "shop", "cap_in", "target_in", "window_in")
MOVED_TO_SHOP = ("label", "product_rules", "enabled")   # these live in machines.json now
MACHINE_DEFAULTS = {
    "api_version": "2026-04",
    "potry_exe": r"C:\Program Files\PO-TRY\MtFlatbedPrinterApp.exe",
    "potry_prnlist": r"C:\Program Files\PO-TRY\PrnList",
    "gap_in": 1.0,             # measured 1.02 in per gap on the 3-sheet preview, 2026-10-15
    "flush_after_min": 30,
    "fetch_every_min": 2,
    "max_width_in": None,      # printable width; None = not checked
    "start_from_order": None,  # go-live floor (SPEC §4); fetch --download refuses without it
}


class ConfigError(Exception):
    pass


def _read(path):
    if not path.exists():
        raise ConfigError(f"Missing {path.name}. "
                          f"Copy {path.stem}.example.json next to it and fill it in.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path.name} isn't valid JSON: {e}") from None


def all_rules(shop):
    """Every product rule in the shop, each tagged with its line."""
    return [dict(rule, line=line_id)
            for line_id, line in shop["lines"].items()
            for rule in line.get("product_rules", [])]


def check_shop(shop):
    """Refuse a machines.json that could route one product to two lines, or
    that enables two machines on one line (no dispatcher yet, SPEC §2)."""
    lines, machines = shop.get("lines") or {}, shop.get("machines") or {}
    if not lines or not machines:
        raise ConfigError("machines.json: needs 'lines' and 'machines'")
    rules = all_rules(shop)
    for r in rules:
        if not r.get("title_contains") or not r.get("kind"):
            raise ConfigError(f"machines.json: a {r['line']} product rule needs title_contains and kind")
    for i, a in enumerate(rules):
        for b in rules[i + 1:]:
            ta, tb = a["title_contains"].lower(), b["title_contains"].lower()
            if a["line"] != b["line"] and (ta in tb or tb in ta):
                raise ConfigError(f"machines.json: {a['title_contains']!r} ({a['line']}) and "
                                  f"{b['title_contains']!r} ({b['line']}) can match the same product")
    enabled = {}
    for mid, m in machines.items():
        if m.get("line") not in lines:
            raise ConfigError(f"machines.json: machine {mid} names unknown line {m.get('line')!r}")
        if m.get("enabled"):
            enabled.setdefault(m["line"], []).append(mid)
    for line_id, mids in enabled.items():
        if len(mids) > 1:
            raise ConfigError(f"machines.json: {' and '.join(mids)} are both enabled on line "
                              f"{line_id}; only one per line until the dispatcher exists")


def load_shop(path=MACHINES_PATH):
    shop = _read(path)
    check_shop(shop)
    return shop


def load_machine(path=MACHINE_PATH, shop_path=MACHINES_PATH):
    """This PC's settings merged with its machine and line from machines.json.

    Adds: line, label, line_label, product_rules (this line's), shop_rules
    (every line's, for the runtime overlap check)."""
    local = _read(path)
    moved = [k for k in MOVED_TO_SHOP if k in local]
    if moved:
        raise ConfigError(f"machine.json: {', '.join(moved)} now live in machines.json; "
                          f"remove them from machine.json")
    cfg = dict(MACHINE_DEFAULTS)
    cfg.update(local)
    missing = [k for k in MACHINE_REQUIRED if cfg.get(k) in (None, "", [])]
    if missing:
        raise ConfigError(f"machine.json: missing or empty {', '.join(missing)}")
    if not cfg["target_in"] + cfg["window_in"] <= cfg["cap_in"]:
        raise ConfigError("machine.json: target_in + window_in must not exceed cap_in")
    hand = cfg.get("hand_download_dir")
    if hand and Path(hand).resolve() == Path(cfg["queue_dir"]).resolve():
        raise ConfigError("machine.json: hand_download_dir must not be the queue_dir; "
                          "the agent's own downloads would look like hand downloads")

    shop = load_shop(shop_path)
    me = shop["machines"].get(cfg["machine_id"])
    if me is None:
        raise ConfigError(f"machine.json: machine_id {cfg['machine_id']!r} isn't in machines.json")
    if not me.get("enabled"):
        raise ConfigError(f"{cfg['machine_id']} is switched off in machines.json")
    line = shop["lines"][me["line"]]
    cfg.update(line=me["line"], label=me.get("label") or cfg["machine_id"],
               line_label=line.get("label") or me["line"],
               product_rules=line.get("product_rules", []), shop_rules=all_rules(shop))
    return cfg


def load_secrets(path=SECRETS_PATH):
    sec = _read(path)
    has_token = (sec.get("access_token") or "").startswith("shp")
    has_client = all(sec.get(k) for k in ("client_id", "client_secret"))
    if not (has_token or has_client):
        raise ConfigError("secrets.json: fill in either 'access_token' (shpat_...) "
                          "or both 'client_id' and 'client_secret'.")
    return sec


def load_or_exit(loader, *args):
    """For command-line entry points: print the problem and quit."""
    try:
        return loader(*args)
    except ConfigError as e:
        sys.exit(str(e))
