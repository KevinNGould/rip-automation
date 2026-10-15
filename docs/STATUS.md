# Build status (Code → chat handoff)

Claude Code rewrites this file at the end of every working session and pushes it. Chat reads this file first. Decisions and open questions live in `docs/DECISIONS.md`; the spec lives in `docs/SPEC.md`. Git history holds older versions of this file.

**Last updated:** 2026-10-15 ~11:56 (printer PC clock) · **SPEC §12 step 5 done; Code stops before step 6 and checks in** · **HEAD:** see `git log -1`

---

## Where we are

| SPEC §12 step | State |
|---|---|
| 1–3 | **Done** |
| 4. Driver, length gate, hand check, floor | **Done.** First real agent rip 11:36 (#47946 + #47947, 135.4 in). |
| 5. Heartbeat + Pi dashboard + leak test | **Done.** Live at **http://osshub.local:8085**. |
| 6. Agent loop, pause switch, Task Scheduler | Not started. **Waiting for Kevin/chat to say go.** |
| 7–8 | Not started |

Tests: `python -m unittest` from the repo root runs 149 on the printer PC (all pass; 7 dashboard tests skip without Flask). Those 7 pass on the hub Pi.

## After step 5 (Kevin's request)

The Operations Hub (:8081) now has a **RIP Automation** button and greys out **Production Command** (:8084). :8084 shows a "replaced by RIP Automation" popup. Details in DECISIONS.md. The :8081 change isn't seen visually yet, because it's behind the hub PIN.

## Step 5: what was built

- **`ripauto/status.py`:** builds the SPEC §8 heartbeat from the ledger and run records. Free text (hold reasons, errors) is scrubbed of paths, links, emails, app file/job names, tokens and IDs. A `Reporter` sends on every state change plus every 30 s, and never stops a rip if the Pi is down.
- **`ripauto/agent.py`:** reports `planning → ripping` (orders, planned inches, progress % from PO-TRY's queue) `→ idle`. A cancelled gate is `holding`, a stopped run is `error`, and the exit is `stopped`.
- **`dashboard/oss_rip.py`:** the hub's new `oss_rip` service on :8085 (systemd, same pattern as `oss_command`):
  - `POST /api/heartbeat` with `X-Agent-Key`; incoming data re-cleaned on the Pi
  - `GET /api/machines`, and the page at `/`
  - one card per machine from `machines.json`: running/stopped/offline (2 min), current run + progress, inches today, queue, held sheets, last error
  - display-only, no PIN
- **Leak test** (`tests/test_status.py`) per SPEC §8, plus a Pi-side test that incoming heartbeats are cleaned.
- **Config:** `dashboard_url` in `machine.json` = `http://osshub.local:8085/api/heartbeat`. `dashboard_key` in `secrets.json` was copied from the hub without being printed.

## Verified

- On the hub: `oss_rip` active and enabled; settings and state files chmod 600; the other five services (`oss_command`, `oss_hub`, `oss_printer`, `oss_tracker`, `oss_uptime`) still active.
- A heartbeat from the 8-color PC shows Printer #3 (8-color): stopped, 135.4 in today, 1 run / 2 sheets, 0 waiting. The four other printers show "switched off".
- Not yet seen live: the ripping view (orders + progress bar). It appears on the next real `agent --once` run.

## Needs a decision / tidy (DECISIONS.md → Open)

- **Kevin/chat: go for step 6** (agent loop, pause switch, Task Scheduler)?
- **Kevin: Shopify token exposure.** It was printed into the Code transcript during step 5 (not committed). Rotate now, or wait for the dedicated app (step 7)? Rotating the shared token affects the hub, scanners, printer and tracker.
- **Chat:** the dashboard has no PIN (display-only, no private data). OK, or gate it like the other hub pages?
- **Chat:** `dashboard_key` sits in `secrets.json`; SPEC §3 says "Shopify credential only".
- **Chat (before CMYK/UV go live):** UV sheets route to the CMYK line; proposed fix is "title starts with".
- **Chat:** spec wording:
  - §3/§4 still put product rules in `machine.json`.
  - §3's layout should list `ledger.py`, `handcheck.py`, `ocr.py` and `status.py`.
  - §7 step 6 should say "OCR", not "UIA".
- **Chat/Kevin:** confirm the Q1 reading (copies split only when they can't all fit under the cap).
- **Open (Code):** closing PO-TRY job tabs after a run.
- Still open: Q4 (flush minutes), Q5 (UV/CMYK details), Q6 (PC host names).

## Housekeeping

- Deploying dashboard changes: copy `dashboard/oss_rip.py` (and `config/machines.json` as `~/oss_rip_machines.json`) to `pi@osshub.local`, strip CRs, then `sudo systemctl restart oss_rip`. The README has the commands.
- `C:\Users\kevin\Downloads\8color-hold-accidental-20261015`: the 21 files from the 10:41 incident; ledger entries are now `by_hand`. Clear the folder whenever you like.
- `C:\Users\kevin\Downloads\8color-dryrun`: copies of three already-printed sheets for dry runs.
- `config/secrets.json` still has default file permissions; locking it is Kevin's call.
