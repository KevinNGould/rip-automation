"""
Loads the two config files (SPEC §3):

  config/machine.json   which machine this PC is, rules, folders, limits. No secrets.
  config/secrets.json   the Shopify credential only. Never logged, never sent
                        anywhere but Shopify.

Both are git-ignored. Copy the *.example.json files to start.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO / "config"
STATE_DIR = REPO / "state"
MACHINE_PATH = CONFIG_DIR / "machine.json"
SECRETS_PATH = CONFIG_DIR / "secrets.json"

MACHINE_REQUIRED = ("machine_id", "label", "product_rules", "queue_dir", "shop",
                    "cap_in", "target_in", "window_in")
MACHINE_DEFAULTS = {
    "enabled": True,
    "api_version": "2026-04",
    "potry_exe": r"C:\Program Files\PO-TRY\MtFlatbedPrinterApp.exe",
    "potry_prnlist": r"C:\Program Files\PO-TRY\PrnList",
    "gap_in": 0.5,
    "flush_after_min": 30,
    "fetch_every_min": 2,
    "max_width_in": None,      # printable width; None = not checked (DECISIONS, Open)
}


class ConfigError(Exception):
    pass


def _read(path):
    if not path.exists():
        raise ConfigError(f"Missing {path.relative_to(REPO)}. "
                          f"Copy {path.stem}.example.json next to it and fill it in.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path.relative_to(REPO)} isn't valid JSON: {e}") from None


def load_machine(path=MACHINE_PATH):
    cfg = dict(MACHINE_DEFAULTS)
    cfg.update(_read(path))
    missing = [k for k in MACHINE_REQUIRED if cfg.get(k) in (None, "", [])]
    if missing:
        raise ConfigError(f"machine.json: missing or empty {', '.join(missing)}")
    for i, rule in enumerate(cfg["product_rules"]):
        if not rule.get("title_contains") or not rule.get("kind"):
            raise ConfigError(f"machine.json: product_rules[{i}] needs title_contains and kind")
    if not cfg["target_in"] + cfg["window_in"] <= cfg["cap_in"]:
        raise ConfigError("machine.json: target_in + window_in must not exceed cap_in")
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
