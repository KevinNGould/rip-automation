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
python -m ripauto.find 47946                     where did this order go on this PC (ledger, runs, logs)
python -m ripauto.potry --inspect-queue          read PO-TRY's RIP queue (admin)
python -m ripauto.potry --dry-run --batch --folder F   load F's files as one job, Cancel (admin)
python -m unittest                               run the tests
```

Runtime files (ledger `state/processed.json`, run records `state/runs/`, `state/rip_log.txt`) live in `state/`, which is git-ignored.

## Running unattended (Task Scheduler)

Install once from an **administrator** PowerShell in the repo root:

```
powershell -ExecutionPolicy Bypass -File tools\install_task.ps1
```

This registers "OSS RIP Agent": `pythonw -m ripauto.agent --loop` at logon, with highest privileges, restart on failure and no console window. It starts right away. Day to day:

- **"RIP Agent" desktop shortcut** (made by `tools\make_shortcut.ps1`, no admin): a small window showing ON / PAUSING / PAUSED / OFF and what the agent is doing, with one big button: **Pause agent / Resume agent / Start agent**. Below it: **Rip now** (rip the next run even if it isn't full, after a 10 s countdown), **Restart agent** (to load new code), and links to the dashboard and the log.
- **Corner warning:** before every run a popup in the bottom-right corner says "Auto rip in 60 s" with what's going, plus **Rip now / Wait 5 min / Pause agent**. At zero it starts once the mouse and keyboard have been still for 5 s. It never steals keyboard focus. Corner and timings are in `machine.json` (`warning_corner`, `warn_before_rip_s`, `still_before_rip_s`, `snooze_min`).
- `tools\pause.cmd` / `tools\resume.cmd`: the same pause switch from a prompt. It finishes any run in progress, then waits and shows "stopped" on the dashboard.
- `tools\agent_status.cmd`: task state, pause switch, last log lines.
- `state\agent.log`: the log, rotated daily (30 days kept).
- `tools\uninstall_task.ps1` (admin): remove it.

The loop:
- fetches every 2 min and checks for a ready run every 20 s
- only touches PO-TRY when a run is full or due, nobody has used the mouse or keyboard for 60 s, and PO-TRY isn't already ripping
- never prints

## Pi dashboard (`dashboard/`)

`oss_rip.py` runs on the hub Pi as the `oss_rip` service at http://osshub.local:8085. It's display-only; agents post heartbeats to `/api/heartbeat` with the key from `~/oss_rip_settings.json` (stored on each printer PC as `dashboard_key` in `config/secrets.json`).

Views:
- Floor (`/`): one card per machine.
- Machine detail (`/#m=8color-1`): queue, next run, ripped log, 30-day chart.
- Wall screen (`/tv`).
- Order search (`/#q=47946`): the order's trail across every machine, from `~/oss_rip.db`.

Deploy or update from the repo root on a printer PC:

```
scp -i ~/.ssh/osshub_key dashboard/oss_rip.py dashboard/oss_rip.service pi@osshub.local:~/
scp -i ~/.ssh/osshub_key config/machines.json pi@osshub.local:~/oss_rip_machines.json
ssh -i ~/.ssh/osshub_key pi@osshub.local "sed -i 's/\r$//' ~/oss_rip.py ~/oss_rip.service ~/oss_rip_machines.json && sudo cp ~/oss_rip.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now oss_rip && sudo systemctl restart oss_rip"
```

Dashboard tests need Flask, so they run on the Pi: copy `dashboard/`, `tests/` and `config/machines.json` into a temp folder there and run `python3 -m unittest tests.test_dashboard`.
