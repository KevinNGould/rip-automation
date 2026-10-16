# Build status (Code → chat handoff)

Claude Code rewrites this file at the end of every working session and pushes it. Chat reads this file first. Decisions and their reasoning live in `docs/DECISIONS.md` (newest first); the spec lives in `docs/SPEC.md`. Git history holds older versions of this file.

**Last updated:** 2026-10-15 ~14:30 (printer PC clock) · **HEAD:** see `git log -1`

## Latest (22:40): first unattended evening: 5 runs, all ripped, none held

Kevin watched over RustDesk. The agent (pid 20268) ran from 14:16 without a restart.

| Run | Orders | Planned | Preview / queue | Load wait |
|---|---|---|---|---|
| 16:02 | #47984 #47986 | 104.9 | 104.897 | 53 of 13 MB read, quiet after 15 s |
| 17:42 | #47996 | 22.1 | 22.077 | 78 of 20 MB, 8 s |
| 18:56 | #48001 ×3 sheets | 41.6 | 41.600 | 16 of 4 MB, 6 s |
| 21:43 | #48009 #48011 | 69.4 | 69.400 | 216 of 45 MB, 12 s |
| 22:30 | #48015 #48017 | 189.4 | 189.400 | 826 of 182 MB, 34 s |

- **Load wait proven on fresh files.** PO-TRY's read counter does count loads from disk; it reads ~4× the file size. So the wait is "read it all + CPU quiet". Every gate passed first time, with no retries needed.
- **22:03 download hiccup, #48017 (168 in sheet):** "PermissionError WinError 32 … .part", 1 s into the download. It retried at 22:05, and the sheet ripped in the 22:30 run.
  - Likely cause (not proven): the app hadn't finished making the big sheet, so the response wasn't a PNG, and a virus scan was holding the `.part` when the agent tried to delete it.
  - Fix: the cleanup now tolerates that, and the log says "not a PNG (yet); trying again next fetch". It takes effect at the next agent restart; no rush.
- Dashboard timeouts at 15:31 and 21:57: one heartbeat each, carried on.

## Earlier (14:40): load-wait fix is LIVE; #47977 + #47979 ripped; unattended evening next

- 14:16: agent stopped, `python -m ripauto.ledger --requeue 47977 47979` (new command; it refuses while the agent runs), started again (pid 20268, pause off).
- 14:18: Kevin forced the run (Rip now). The log read "68 of 357 MB read and nothing happening for 15 s; going on". As Kevin pointed out, PO-TRY reloads pictures it has already loaded from its own memory. Preview 295.424 = planned, gate ok, ripped 14:26, and the finished job's tab was closed.
- **Not yet proven:** that a *fresh* load from disk shows in PO-TRY's read counter. The first run of new files tonight will show it in the "loaded … (N of M MB read …)" line. If reads don't count, the 15 s quiet rule plus the length gate (with its 2 retries) still cover it.
- **Tonight:** Kevin leaves the PC on with PO-TRY open and watches over RustDesk. Checked (read-only): sleep, hibernate and display-off are all *never*; no lock-on-idle is set. Kevin's checklist is in his chat reply: RustDesk view-only, "lock after session end" off, Windows Update paused.

## Earlier (14:30): the 14:07 run was cut short by a big picture still loading; fixed, not yet live

- **14:07 Rip-now run (#47977 + #47979) was cancelled by the length gate:** the preview read 24.0 in, against 295.4 planned. #47979 is a 320 MB, 270 in PNG and was still loading when F3 and RIP were pressed. Nothing was ripped. Both sheets are **held**.
- **Tab closing worked in real use.** Before New: "closed 2 job tab(s)" (Job26/27). After Cancel: "answered No to 'save changes?'" and "closed 1 job tab(s)".
- **Fix built and tested (DECISIONS.md, top entry):**
  - Each Add waits until PO-TRY has read the files and gone quiet (process counters).
  - A short preview gets 2 retries (Cancel, wait, F3 + RIP again) before it's held.
- **Not live yet.** It needs: Kevin's OK → re-queue #47977/#47979 → Restart agent. The live agent (pid 20384) runs the 14:06 code until then.
- Next per the critique: A2 resume prompt, then B1 spacing probe (needs Kevin), B2 version check.

## Earlier (14:07): critique priority 1 is LIVE, plus closing each job tab right after its rip

- The agent (pid 20384, since 14:07:03) runs **A1** (proven live both ways), **A4** (× close proven on an empty tab, `x_button` at 12 px), and **B3**. It's unpaused and downloading #47977 and #47979.
- **Restart agent** now relaunches through the RIP Agent window (tested: 16 s) and keeps the pause switch as it was. Kevin should close and reopen the window from the desktop shortcut to pick up that fix.
- Code's slip: a restart test at 14:03 lifted Kevin's pause (DECISIONS.md, top entry). Fixed.
- Next per the critique: A2 resume prompt, then B1 spacing probe (needs Kevin), B2 version check.

## Earlier: critique priority 1 built (A1, A4, B3), Kevin's live steps

Chat's critique: `docs/CRITIQUE-2026-10-15.md`. Batch 1 is built and tested (details in DECISIONS.md, top entry). The live agent keeps running the 13:10 code until the restart in step 5.

**Kevin, in order:**
1. **RIP Agent window → Pause agent**, and wait until it says PAUSED.
2. **Admin prompt** in `C:\Users\kevin\projects\rip-automation`: `python -m ripauto.potry --probe-tab-close`. It opens an empty job tab and tries ways to close it; hands off while it runs. Tell Code which line says CLOSED (or "None worked").
3. **Guard test:** `python -m ripauto.potry --dry-run --batch --folder C:\Users\kevin\Downloads\8color-dryrun`. **Wiggle the mouse** a few seconds after it starts. Expected: "INTERRUPTED: the mouse moved … Backed out; nothing was ripped", with no dialog left open in PO-TRY.
4. **Same command again, hands off.** Expected: "All done." If step 2 found a method, it first closes the job tab from step 3.
5. **RIP Agent window:** close it and reopen it from the desktop shortcut (to load the new window code). Click **Restart agent**. When it shows OFF, click **Start**, which also turns Pause off. The agent now runs batch 1.

## Handoff for critique (Kevin, 13:20)

Kevin: "take what we have and hand off to chat to critique". **The 8-color agent is live and stays running unattended during the critique.** Code isn't changing anything until chat's critique comes back.

What chat is asked for: critique the build against the spec and against real use on the print floor. Most useful: what's unsafe, what will break first, what's over- or under-built, and which spec changes to make official. Specific questions are at the end.

---

## 1. Where we are

| SPEC §12 step | State |
|---|---|
| 1–3 Port, naming/length, planner | **Done** |
| 4 Driver, length gate, hand check, floor | **Done**; first agent rip 11:36 |
| 5 Heartbeat + Pi dashboard + leak test | **Done**; http://osshub.local:8085 |
| 6 Agent loop, pause, Task Scheduler | **Done and live** since 12:26; first fully unattended rip at 13:13 |
| 7 Dedicated Shopify app | Not started (needs Kevin in Shopify's Dev Dashboard) |
| 8 CMYK + UV | Not started (blocked on the routing fix + Q5/Q6) |

Beyond the spec, at Kevin's request:
- dashboard detail view, TV view, and **order search across machines**
- **RIP Agent control window** (desktop shortcut)
- **corner "Auto rip in 60 s" warning** with Rip now / Wait 5 min / Pause
- Production Command (:8084) retired in favour of :8085

Repo: 66 commits. `ripauto/` + `dashboard/` + `tools/` ≈ 4,700 lines. **Tests: 183 on the printer PC pass; 10 dashboard tests skip there (no Flask) and pass on the hub Pi.**

## 2. Live right now

- **Printer PC (8-color, "8-COLOR"):** Task Scheduler "OSS RIP Agent" runs `pythonw -m ripauto.agent --loop` at logon, with highest privileges, plus a **5-minute watchdog trigger** (IgnoreNew). Current process: 18488, started 13:10:36.
- **Hub Pi:** `oss_rip` service on :8085 (systemd). The :8081 portal has a RIP Automation button; :8084 shows a "replaced" popup.
- **Today's results:**

  | Time | Run | Length | Outcome |
  |---|---|---|---|
  | 11:36 | #47946 + #47947 | 135.4 in | ripped (driven by `--once`) |
  | 13:00 | #47957, #47960, #47964 | — | cancelled at the preview (OCR bug, fixed) |
  | 13:13 | #47957, #47960, #47964 | 197.037 in | ripped fully unattended |

  The hand check skipped #47951 (in Downloads) and #47952 (in PO-TRY's queue). Total 332.4 in over 2 runs, nothing printed by the agent, nothing held. Floor `start_from_order` = 47943.
- **Code loaded in the running agent:** 13:10 version. It predates two later commits: the restart-relaunch fix (8b6c086), which the watchdog covers anyway, and nothing else that changes behaviour. The next restart picks everything up.

## 3. How it works (one pass of the loop)

1. Every 2 min, **fetch** (`ripauto/fetch.py`): Shopify GraphQL, unfulfilled orders ≥ floor. Each line item is routed by title via the committed `config/machines.json` (lines → machines). The `_Print Ready File` PNG is downloaded, named like the app's files, measured from its PNG DPI, and put in the **ledger** (`state/processed.json`) as `queued`.
2. Every 20 s, **plan** (`planner.py`, pure): batch to 552–648 in, never over 650. A run is ready when it's full or its oldest sheet has waited 30 min. With nothing ready, PO-TRY isn't touched.
3. **Corner warning** (`warning.py` + `tools/rip_warning.pyw`): "Auto rip in 60 s", with Rip now / Wait 5 min / Pause. At zero it waits for 5 s with no mouse or keyboard input. It never takes keyboard focus.
4. **PO-TRY busy?** If a queue row says "Ripping", it waits, and the popup stays up with no new countdown.
5. **Hand check** (`handcheck.py`): the order # + sheet position is looked up in PO-TRY's queue job names and the top level of Downloads. A match is marked `by_hand`; uncertain cases are held as "manual check"; then it re-plans.
6. **Rip** (`potry.py`, pywinauto UIA):
   1. New job; multi-file Add (chunks of 10); F3 intelligent layout; RIP.
   2. **Length gate** on the Print preview: **Windows OCR** of the drawn "W inch / L inch" labels (`ocr.py`).
   3. The gate cancels if the reading is unreadable, over 650 in, more than max(3 in, 1%) longer than planned, or shorter than planned-without-gaps minus that tolerance.
   4. Otherwise Print in the preview (this only rips), then wait for "RIP finished"/later, and check the queue `Size (inch)` as a backstop.
7. **Ledger + records:** sheets go `ripped`; files move to `ripped\{run-id}\`; a run record lands in `state/runs/`. Every state change sends a **heartbeat** to the Pi (`status.py`; scrubbed, leak-tested).
8. **Controls:**
   - RIP Agent window: Pause/Resume/Start, Rip now, Restart, log + dashboard links
   - the pause switch is `state\PAUSE`
   - `python -m ripauto.find <order>` searches locally; the dashboard search covers every machine

## 4. Known weak spots (Code's own list, for chat to weigh)

1. **The length gate can't prove every picture loaded.** PO-TRY doesn't expose the canvas's pictures, and F3 spacing varies between 0 and 1 in per gap, even for the same three sheets (195.037 at 13:00, 197.037 at 13:13). The gate allows 0–1 in per gap, so on a long run of many pictures a missing small sheet (~8 in) can slip through. Multi-add itself is proven (3/3 several times).
2. **UI automation is brittle by nature.** It depends on PO-TRY 2.24.1's control names (Add/RIP/New, the Print preview's QPushButtons, file dialog id 1148) and on the preview's look, which the OCR reads. A PO-TRY update could break it. The safe-failure path is tested (cancel → hold), but nothing detects a version change up front.
3. **OCR is the only pre-rip size check.** It reads a screenshot of the preview (fixed once already). It cancels on anything other than one plausible width plus one length, and the queue-size backstop runs after the rip. An OCR misread that lands within tolerance of the plan would pass; that's unlikely, but not impossible.
4. **One hand-check gap:** a job loaded by hand from the agent's own `8color-queue` files, with several files in one job, is only visible by its first file's name, so sheets 2+ could rip twice. Downloads-based hand work is caught (proven today).
5. **A file missing from the queue folder holds the whole run** (manual check), not just that sheet.
6. **Agent copies of by-hand sheets stay in `8color-queue`** (#47951, #47952 now). Harmless, but untidy.
7. **PO-TRY job tabs pile up**: one New per run, never closed. About a dozen today.
8. **The ledger is a single JSON file**, rewritten atomically after each change. There are no backups, and the loop owns it while running. Corruption or a bad edit would lose state; the files in `ripped\` and the run records would allow a rebuild by hand.
9. **An unreadable PO-TRY queue holds every queued sheet** (the spec's rule). It's correct but could be noisy if reading the table is flaky. It hasn't happened yet.
10. **The order history on the Pi starts at deploy time (~12:15 today)** and only knows what an agent saw. A by-hand record's time is when the agent noticed it, not when the person did the work.
11. **Security/privacy:**
    - **The shared Shopify token was printed into a Code transcript today** (not committed). Kevin's call on rotating it vs. waiting for step 7.
    - `config\secrets.json` has default file permissions.
    - The dashboard has no PIN (display-only, order numbers only).
    - Heartbeats carry more than SPEC §8's minimum (queue detail, history), under the same scrub + leak test.
12. **Routing bug, not live:** UV sheets route to the CMYK line ("title contains" overlap). It must be fixed before CMYK/UV are switched on; the proposal is "title starts with".
13. **Single machine only so far.** The machines.json overlap check and one-enabled-per-line rule are tested, but no second printer PC exists to try them on.

## 5. Spec drift: needs chat to make it official (or reject it)

- **§2 / §3 / §4:** product rules live in the committed `config/machines.json` (lines → machines), not `machine.json`. §3's layout lacks:
  - `ledger.py`, `handcheck.py`, `ocr.py`, `status.py`
  - `warning.py`, `control.py`, `lock.py`, `find.py`
- **§3:** `secrets.json` also holds `dashboard_key` (the Pi's agent key), not "Shopify credential only".
- **§7 step 6:** the length is read off the preview by **OCR** (UIA can't see it), with gaps of 0–1 in allowed. A queue-size check after the rip is the backstop.
- **§8:** the heartbeat has extra sections; the dashboard has detail, TV and search views and a local order-history DB (`~/oss_rip.db`).
- **§9:**
  - The idle rule became a 60 s corner warning plus 5 s of stillness.
  - Pause doesn't exit the process.
  - Task Scheduler gets a watchdog trigger, because restart-on-failure doesn't cover exits.
  - The control window + desktop shortcut are the main on/off.
- **§6 Q1:** Code's reading is that copies split across runs only when they can't all fit under the cap. Kevin and chat haven't confirmed it.
- **Floor:** `start_from_order` = 47943 (everything earlier was handled by hand).

## 6. Questions for chat's critique

1. Is the length gate's tolerance (0–1 in per gap + max(3 in, 1%)) the right trade-off between false cancels and missed pictures? Should long runs be capped by **picture count** too, to keep missing-sheet detection meaningful?
2. Should the agent refuse to run when PO-TRY's version or window layout isn't the one it was proven on (2.24.1)?
3. Is holding **everything** on an unreadable queue too blunt for an unattended loop?
4. Any failure mode in the hand check (§4 of the spec) that the two-place check misses? Gap 4 above is the known one.
5. Should the ledger get daily backups, or move to SQLite?
6. Rotate the shared token now, or prioritise step 7 (dedicated Shopify app)?
7. Priorities for what's next. Code's suggestion:
   - (a) by-hand files out of the queue folder
   - (b) missing file holds just that sheet
   - (c) close PO-TRY job tabs
   - (d) step 7, the Shopify app
   - (e) the routing fix, then CMYK/UV
8. Is anything over-built: the dashboard extras, the control window, the corner popup?

## 7. Kevin's quick reference

- **On/off:** "RIP Agent" desktop shortcut → Pause / Resume / Start; Rip now; Restart. Use Pause, not "End task" (the watchdog restarts it within 5 min).
- **Watch:** http://osshub.local:8085 (floor, `/#m=8color-1` detail, `/tv`, search box), or `tools\agent_status.cmd`.
- **Find an order on this PC:** `python -m ripauto.find 47960`.
- **Logs:** `state\agent.log` (daily, 30 days kept).
- **By hand:** download from the app as usual and the agent skips it, or Pause first.
- **Housekeeping:**
  - `Downloads\8color-hold-accidental-20261015` (the 10:41 incident files, all `by_hand`) can be cleared.
  - `Downloads\8color-dryrun` holds dry-run copies.
