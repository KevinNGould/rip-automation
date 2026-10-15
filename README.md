# rip-automation

Pulls new gang sheet orders from Shopify, batches them, and rips them in PO-TRY on each printer PC, with a read-only status dashboard on the Pi. Ripping only: nothing here ever prints.

- `docs/SPEC.md`: what to build and the rules it must keep (start here)
- `docs/DECISIONS.md`: dated decisions and open questions
- `trial/`: the two scripts that proved the 8-color flow on 2026-10-15 (`fetch_8color.py`, `rip_driver.py`). Reference for porting; not the final layout.
- `tools/probe_potry.py`: read-only dump of what Windows automation sees in PO-TRY
- `config/*.example.json`: copy to `machine.json` / `secrets.json` on each PC. The real ones are git-ignored and must never be committed.

Run anything that touches PO-TRY from an administrator prompt; PO-TRY runs elevated.
