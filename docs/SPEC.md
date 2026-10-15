# OSS RIP Automation — Spec v1

Owner: Kevin (One Source Supply). Spec author: Claude (chat). Implementer: Claude Code.
Status: 8-color single-sheet flow proven 2026-10-15 (#47918 fetched and ripped hands-free). This spec turns the two trial scripts into a multi-machine system. **Only the 8-color machine is enabled in v1**; the others get config slots and code paths but stay switched off.

---

## 1. What it does

On each printer PC, one program loops:

1. **Fetch**: pull new sheets for *this machine's products* from Shopify and download the print files.
2. **Plan**: group waiting sheets into runs ("batches") close to a target length, never over the machine's cap.
3. **Rip**: load each batch into PO-TRY as one job, intelligent layout once, RIP, confirm. Never print.
4. **Report**: send a small status heartbeat to the Pi dashboard.

Ripping is fully automatic. People print from PO-TRY's RIP queue as they do today.

---

## 2. Machines and routing

All machines run the same PO-TRY software, so one codebase serves all of them. Each PC has a `machine.json` that says which machine it is.

| machine_id | Machine | Products it takes (title contains) | Length cap | v1 |
|---|---|---|---|---|
| `8color` | 8-color CMYK+RGBO DTF | `DTF Color-Pro (8 Color CMYK+RGBO)` | 650 in | **enabled** |
| `cmyk` | 5-head CMYK DTF | `DTF (4 Color CMYK) - Gang Sheet Builder`, `DTF (4 Color CMYK) - Image by Size` | TBD | off |
| `uvdtf` | 3-head UV DTF | TBD (Kevin to supply product titles) | TBD | off |

Rules:
- A line item belongs to exactly one machine. If a title matches no enabled machine, it's ignored. If it matches two, that's a config error: refuse to start.
- The table above lives in a committed `config/machines.json` (product rules only, no secrets). Every agent loads it at startup to run the overlap check, then uses the entry named by its own `machine.json` → `machine_id`.
- **Image by Size** items carry a different attribute (`_Print Ready`, a signed `…/shopify/gang/print-ready?...` URL, plus `Uploaded File`) instead of `_Print Ready File`. Build the code path behind a feature flag, but don't enable it until a test order is verified the way #47918 was.

---

## 3. Repo layout

```
rip-automation/
  docs/SPEC.md                 this file (Claude edits)
  docs/DECISIONS.md            dated log of decisions + open questions
  ripauto/
    fetch.py                   Shopify -> downloads (from fetch_8color.py)
    sheets.py                  sheet facts: length, qty, naming
    planner.py                 batching
    potry.py                   PO-TRY driver (from rip_driver.py)
    status.py                  heartbeat sender
    agent.py                   the loop
  tools/probe_potry.py         read-only UI dump (diagnostics)
  config/machines.json         committed: every machine's product rules (§2)
  config/machine.example.json  committed
  config/secrets.example.json  committed, empty values
  dashboard/                   Pi side (read-only status page)
  tests/                       unit tests for planner, naming, queue parsing
  .gitignore                   MUST include config/machine.json, config/secrets.json, *.log, state/
```

**Config split (non-negotiable):**
- `config/machine.json`: machine_id, label, product rules, folders, cap/target/window, flush minutes, dashboard URL. No secrets. Safe to show.
- `config/secrets.json`: Shopify credential only. Never committed, never sent anywhere except Shopify, never logged, never put in a heartbeat.

---

## 4. Fetch

Carry over `fetch_8color.py` behavior (GraphQL `orders`, `fulfillment_status:unfulfilled status:open`, line item `customAttributes`, ledger `state/processed.json` keyed by line item ID, PNG signature check, `.part` then rename).

Changes:
- Product rules come from `machine.json`, not a hard-coded string.
- **File naming must match the Build a Gang Sheet app**, minus the design name (Shopify doesn't store it; Kevin accepted that):
  `{order#}-{Customer Name}-{order ID}-{size}-{position}-({qty})`
  e.g. `47918-Kaitlyn Carter-18943728582971-22in x 12in 1ft-1-(1)`
  - `order ID` = order `legacyResourceId`.
  - `size` = variant title with `(` `)` removed and `/` → `-` (`22in x 78in (6 1/2ft)` → `22in x 78in 6 1-2ft`).
  - `position` = 1-based index of this line item among the order's Build-a-Gang-Sheet line items (items carrying `_Print Ready File` or `_Print Ready`), in Shopify's line item order. Evidence: #47862 had `…48in 4ft-1-…` and `…600in 50ft-2-…`.
  - `(qty)` = line item quantity. **Correction to the trial script**, which put quantity in the middle slot and a constant `(1)` at the end.
  - If the server sends a `Content-Disposition` filename starting with `{order#}-`, use that instead (it didn't on #47918, but keep the check).
- Customer name needs `read_customers` + protected customer data "Name". The trial token lacks it, so names are blank. See §10.
- Record in the ledger, per sheet: order#, line ID, position, qty, measured length (§5), low-res flag, file path, fetched_at.

Known risk: if someone downloads an 8-color order by hand *and* the agent fetches it, it gets ripped twice. The agent can't read the app's 1/0 counter. Rule for the shop: once the 8-color agent is live, nobody hand-downloads 8-color orders.

---

## 5. Sheet facts

- **Length** (inches) = PNG pixel height ÷ DPI from the PNG `pHYs` chunk. Fallbacks in order: `_Actual Height` attribute, then the number in the variant title. Store which source was used.
- **Width** likewise; anything wider than the machine's printable width → hold.
- **Copies**: quantity N means N physical sheets. For batching, a sheet with qty N occupies N × length. **Open question Q1** (§11) is how to load them; default is to Add the same file N times to the job.

---

## 6. Batching (planner)

Inputs: all fetched, unripped, unheld sheets for this machine, oldest first.

Per-machine settings (8-color values):
- `cap_in` = **650**: hard limit, never exceeded, ever.
- `target_in` = 600, `window_in` = 48 → a "full" run is 552–648 in.
- `gap_in`: estimated spacing intelligent layout adds between sheets (start at 0.5, tune from real runs).
- `flush_after_min` = 30 (Kevin: "after oldest waits X min"; X configurable).

Rules:
1. Never split a sheet. All N copies of a qty-N sheet go in the same run.
2. Keep an order's sheets together when they fit; if an order's sheets together exceed the cap, they may go in consecutive runs.
3. Fill oldest-first. Planned length = Σ(length × qty) + gap × (count − 1). Add the next sheet only if planned length stays ≤ `target_in + window_in` (648). After the oldest-first pass, try smaller later sheets to fill toward the target (first-fit), still ≤ 648.
4. Rip a run when it's **full** (≥ 552), or when the **oldest sheet in it has waited ≥ flush_after_min**.
5. A single sheet whose length × qty > cap: **hold**, flag "over 650 in", never rip. If qty pushes it over but one copy fits, still hold (needs a human decision; see Q1).
6. Real-world check (§7 step 6) is the final word, not the plan.

Planner must be a pure function with unit tests: given sheets + now, return runs + holds.

---

## 7. Ripping a run (PO-TRY driver)

Carry over everything proven in `rip_driver.py`, including these findings:
- Python must run elevated (PO-TRY runs as admin).
- UIA backend works. Layout toolbar buttons are named: New, Add, Auto layout, RIP. Intelligent layout is the **unnamed** two-part button after "Auto layout"; trigger it with **F3**.
- Add and RIP need a real mouse click (UIA invoke blocks until their dialog closes). New and the view toggle can use invoke.
- The "Please select picture" file dialog appears nested under the main window in the UIA tree; file name box automation_id `1148`, Open button id `1`.
- RIP queue: headers are UIA type `Header`, cells `DataItem`; Status `RIP finished`; Processing is a bare number.
- **The RIP queue toolbar has a real "Print" button (QToolButton).** Only ever press a `QPushButton` named Print inside a window titled "Print preview".

Steps per run:
1. Show layout view. **New**.
2. **Add** each file (× its qty), one Add per file. (Test whether the dialog accepts multiple files in one go; if it does, use it.)
3. Confirm the expected number of pictures loaded.
4. **F3** once. (Open item: confirm the keypress lands. Fallback: click the first half of the unnamed layout button.)
5. **RIP** → wait for Print preview.
6. **Length gate:** read the job length from the preview (it displays e.g. `22.500 inch` × `601.001 inch`; find it via UIA). If the length is unreadable, or > `cap_in`: press **Cancel**, hold every sheet in the run with reason, report error. Do not retry automatically.
7. Otherwise **Print** (= confirm rip). Wait for the run's row in the queue to read `RIP finished` (match by first file's name and establishing time ≥ start). Error column non-empty → stop, flag.
8. Move the run's files to `ripped\{run-id}\`, write a run record (files, orders, planned length, actual length, start/end).
9. Back to layout view.

Job tabs pile up (one New per run). Investigate closing the run's tab after step 8.

---

## 8. Status heartbeat and Pi dashboard

Agent sends JSON every 30 s (and on every state change) via HTTP POST to the Pi over the LAN. The Pi page is **display-only**; nothing on the Pi can trigger fetches or rips.

```json
{
  "machine_id": "8color",
  "label": "8-Color DTF",
  "host": "PRINTPC-8C",
  "agent_version": "1.0.0",
  "state": "ripping",              // idle | fetching | planning | ripping | holding | error | stopped
  "since": "2026-10-15T09:31:03-04:00",
  "current_run": {"id": "8c-20261015-0931", "orders": ["#47918", "#47920"], "planned_in": 588.0, "progress_pct": 68},
  "today": {"inches_ripped": 1212.4, "sheets": 31, "runs": 3},
  "queue": {"sheets": 7, "inches": 214.5, "oldest_wait_min": 12},
  "flags": [{"order": "#47901", "reason": "over 650 in"}],
  "last_error": null,
  "sent_at": "2026-10-15T09:31:13-04:00"
}
```

**Allowed in a heartbeat:** order numbers, inches, counts, states, machine names, error text.
**Never:** customer names, emails, addresses, file URLs, Shopify IDs or tokens, file paths containing customer names. Unit test: serialize a heartbeat from a run full of real-looking data and assert none of those appear.

Dashboard (Pi): one card per machine: running/stopped/offline (offline = no heartbeat for 2 min), current run with order numbers and progress, inches ripped today, queue waiting (sheets/inches/oldest wait), flags and last error. The Pi endpoint needs no Shopify access. If the existing hub has a pattern for ingesting status, follow it (the Code thread that built the hub knows it).

---

## 9. Running it

- Windows Task Scheduler on each printer PC: start `agent.py` at logon, "Run with highest privileges", restart on failure.
- Loop: fetch every 2 min; plan/rip whenever PO-TRY is idle.
- **Pause switch:** if `state/PAUSE` exists, finish the current step, then stop at the next safe point (never mid-dialog) and report `stopped`.
- PO-TRY must be maximized and nobody should use the mouse on that PC during a rip. The agent announces `ripping` on the dashboard while it's in control.
- Logs: `state/agent.log`, rotated daily. No secrets in logs.

---

## 10. Credentials

Create a dedicated Shopify Dev Dashboard app for this system (scopes: `read_orders`, `read_customers`; protected customer data: Name). It gives a client ID + secret; the agent exchanges them for a 24-hour token on each run (already supported). This replaces borrowing the Pi paperwork-printer's `shpat_` token, so this system can be revoked on its own. The same app's credentials go into `secrets.json` on each printer PC.

---

## 11. Open questions (Kevin)

- **Q1 Quantity:** for a qty-2 sheet today, do you Add the file twice to one job, or rip once and set copies? And if 2 copies won't fit under 650 but 1 does, can the copies go in separate runs?
- **Q2 Low-res:** sheets flagged `_Has Low Resolution` (customer confirmed "print anyway"): rip normally and just show the flag, or hold for a look?
- **Q3 Priority:** anything that should jump the queue (rush shipping, local pickup, specific tags)?
- **Q4 Flush time:** is 30 min the right wait before a partial run goes out?
- **Q5 UV DTF / CMYK:** product titles for UV DTF, and length caps and targets for the CMYK and UV machines.
- **Q6 PCs:** computer names for each machine (used as `host` on the dashboard).

---

## 12. Build order for Code

1. Repo skeleton, config split, `.gitignore`; port the two scripts into `ripauto/` unchanged except config loading. Re-run the #47918-style test.
2. Naming fix (§4) + sheet length from PNG (§5) + tests.
3. Planner (§6) as a pure function + tests (include: exactly 650, 651, qty pushing over, flush timing, order kept together).
4. Driver: multi-file runs + preview length gate (§7). Dry-run a 2–3 sheet batch, then a real one.
5. Heartbeat + Pi dashboard (§8) + leak test.
6. Agent loop, pause switch, Task Scheduler setup (§9).
7. Dedicated Shopify app swap (§10).
8. Later: CMYK and UV machines.
