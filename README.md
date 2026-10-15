# rip-automation

Pulls new gang sheet orders from Shopify, batches them, and rips them in PO-TRY on each printer PC, with a read-only status dashboard on the Pi. Ripping only: nothing here ever prints.

- `docs/SPEC.md`: what to build and the rules it must keep (start here)
- `docs/DECISIONS.md`: dated decisions and open questions
- `docs/STATUS.md`: Code's handoff to chat: where the build is, what's verified, what's waiting on whom (rewritten each session)
- `trial/`: the two scripts that proved the 8-color flow on 2026-10-15 (`fetch_8color.py`, `rip_driver.py`). Reference for porting; not the final layout.
- `tools/probe_potry.py`: read-only dump of what Windows automation sees in PO-TRY
- `config/*.example.json`: copy to `machine.json` / `secrets.json` on each PC. The real ones are git-ignored and must never be committed.

Run anything that touches PO-TRY from an administrator prompt; PO-TRY runs elevated.

From the repo root:

```
python -m ripauto.fetch                          list this machine's sheets (downloads nothing)
python -m ripauto.fetch --download               download new sheets (needs start_from_order)
python -m ripauto.agent --plan                   show how queued sheets would be batched
python -m ripauto.agent --once --dry-run         one cycle, Cancel at the preview (admin)
python -m ripauto.agent --once                   one real cycle: hand check, plan, rip (admin)
python -m ripauto.potry --inspect-queue          read PO-TRY's RIP queue (admin)
python -m ripauto.potry --dry-run --batch --folder F   load F's files as one job, Cancel (admin)
python -m unittest                               run the tests
```

Runtime files (ledger `state/processed.json`, run records `state/runs/`, `state/rip_log.txt`) live in `state/`, which is git-ignored.

## Pi dashboard (`dashboard/`)

`oss_rip.py` runs on the hub Pi as the `oss_rip` service at http://osshub.local:8085. It's display-only; agents post heartbeats to `/api/heartbeat` with the key from `~/oss_rip_settings.json` (stored on each printer PC as `dashboard_key` in `config/secrets.json`).

Deploy or update from the repo root on a printer PC:

```
scp -i ~/.ssh/osshub_key dashboard/oss_rip.py dashboard/oss_rip.service pi@osshub.local:~/
scp -i ~/.ssh/osshub_key config/machines.json pi@osshub.local:~/oss_rip_machines.json
ssh -i ~/.ssh/osshub_key pi@osshub.local "sed -i 's/\r$//' ~/oss_rip.py ~/oss_rip.service ~/oss_rip_machines.json && sudo cp ~/oss_rip.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now oss_rip && sudo systemctl restart oss_rip"
```

Dashboard tests need Flask, so they run on the Pi: copy `dashboard/`, `tests/` and `config/machines.json` into a temp folder there and run `python3 -m unittest tests.test_dashboard`.
