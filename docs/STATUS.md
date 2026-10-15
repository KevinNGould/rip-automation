# Build status (Code → chat handoff)

Claude Code rewrites this file at the end of every working session and pushes it. Chat reads this file first. Decisions and open questions live in `docs/DECISIONS.md`; the spec lives in `docs/SPEC.md`. Git history holds older versions of this file.

**Last updated:** 2026-10-15 ~13:15 (printer PC clock) · **8-color agent live and unattended on the new code since 13:10. First fully unattended rip at 13:13** (#47957 #47960 #47964, 197.037 in, after the corner warning). 332.4 in ripped today over 2 runs; hand check skipped #47951 and #47952. See DECISIONS.md "Proven (13:13)" · **HEAD:** see `git log -1`

## Go-live (12:26)

- Kevin installed the task (`tools\install_task.ps1`): "OSS RIP Agent" running as 8-COLOR\kevin, last result 267009 (running).
- `state\agent.log`: loop started 12:26:14. The first fetch downloaded **5** sheets at or above the floor:

  | Order | Measured |
  |---|---|
  | #47951 | 37.2 in |
  | #47952 | 67.2 in |
  | #47957 | 13.2 in |
  | #47960 | 156.6 in |
  | #47964 | 25.2 in |

- Plan: one run of **303.4 in**, 5 pictures, not full. It's due at **12:56** (30-min flush), then rips once the PC has had a minute without mouse or keyboard.
- Dashboard: Printer #3 is **running / idle** and heard within seconds. Next run 303.4 in at 12:56; order search finds #47960 "waiting".
- **Not yet seen:** the first unattended rip (due ~12:56). Next session: check `state\agent.log`, the dashboard's ripped log, and the run record.

## Latest: dashboard build-out + order search (live at http://osshub.local:8085)

- **Floor → machine detail** (click a card): tiles, Now, Next run (when it goes), Waiting table, Ripped log (agent runs + found by hand), inches-per-day chart for 30 days with tooltip and table view.
- **`/tv`:** big-number wall view.
- **Order search across machines** (Kevin, "important"): the header search box finds an order's trail on every printer (waiting, ripped in which run, held, by hand) from the Pi's new order history (`~/oss_rip.db`). On a printer PC, `python -m ripauto.find <order>` gives the full local detail, including file path and log lines.
- Still display-only (Kevin: interactive later). Checked in the browser: detail, chart, search (#4794 → #47946/#47947 ripped in run 8color-1-20261015-113554) and TV.
- Details: DECISIONS.md, "dashboard build-out and order search". Tests: 168 on the printer PC + 10 dashboard tests on the Pi, all passing.

---

## Where we are

| SPEC §12 step | State |
|---|---|
| 1–3 | **Done** |
| 4. Driver, length gate, hand check, floor | **Done.** First real agent rip 11:36 (#47946 + #47947, 135.4 in). |
| 5. Heartbeat + Pi dashboard + leak test | **Done.** Live at **http://osshub.local:8085**. |
| 6. Agent loop, pause switch, Task Scheduler | **Done; live since 12:26** (Task Scheduler). First unattended rip due ~12:56. |
| 7. Dedicated Shopify app | Not started (needs Kevin in Shopify's Dev Dashboard). |
| 8. CMYK + UV | Not started (needs the routing fix first). |

Tests: `python -m unittest` from the repo root runs 168 on the printer PC (all pass; 10 dashboard tests skip without Flask). Those 10 pass on the hub Pi.

## Step 6: what was built

- **`agent --loop`:** fetch every 2 min, a cycle every 20 s, heartbeats throughout. It only touches PO-TRY when a run is ready and PO-TRY isn't ripping. Since ~12:40 (needs one restart to go live) a 60 s corner warning comes first, replacing the old "60 s with no input" rule. Errors are logged, reported, PO-TRY is reconnected, and it carries on.
- **Pause switch:** `tools\pause.cmd` / `tools\resume.cmd` (`state\PAUSE`). A run in progress finishes first; the dashboard shows "stopped" while paused.
- **`tools\install_task.ps1`:** Task Scheduler "OSS RIP Agent": pythonw at logon, highest privileges, restart on failure. Also `uninstall_task.ps1` and `agent_status.cmd`.
- **Log:** `state\agent.log`, rotated daily, 30 days kept.
- **Lock:** one loop at a time; `fetch --download` and `agent --once` refuse while it runs.
- After a rip, PO-TRY stays on the RIP queue view, where people print.
- Details and reasoning: DECISIONS.md, "step 6 build".

### Kevin: install it (administrator PowerShell)

```
cd C:\Users\kevin\projects\rip-automation
powershell -ExecutionPolicy Bypass -File tools\install_task.ps1
```

It starts right away. At the time of writing, three new 8-color sheets are waiting at or above the floor: #47951 (36 in), #47952 (66 in) and #47957 (12 in). The loop will download them at once. Together they're about 120 in (not full), so they go out as one partial run ~30 min later, once nobody has touched the PC for a minute. Any of them already downloaded by hand are skipped. Watch http://osshub.local:8085 (planning → ripping with progress → idle) and `tools\agent_status.cmd`.

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
