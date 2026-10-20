# Decisions and open questions

Newest first. Claude (chat) keeps this file; Claude Code adds entries when it decides something during the build. Build progress and next steps are in `docs/STATUS.md`.

## 2026-10-20

**Noted (Kevin): the colour template is now always `Template_impro`**
- It's set in PO-TRY's job panel (the dropdown above "Color mode - Follow the picture"). No code change: the agent never reads or touches the template dropdown or anything in that panel. It rips with whatever the new job shows. Checked by `git grep` of ripauto/, tools/ and docs/.
- **Open (for chat):** if a new job ever came up on a different template, the agent would rip with it. A read-only check (read the dropdown's text before RIP; cancel and hold if it isn't `Template_impro`) would catch that without adding any clicks. Not built; offered to Kevin.

## 2026-10-19

**Built (Code, 13:50): OCR maps look-alike letters, and a doubtful reading gets a second zoom**
- **Four days unattended (10-16 08:31 → 10-19 13:28):** 27 runs ripped. The length check cancelled 3, all on misread length labels; none was a real size problem:
  - 10-16 20:19, #48134: "48.1 OO inch" → unreadable (held since).
  - 10-19 13:25 and 13:27, #48263 + #48266: "183A00 inch" → the parser took the trailing "00" as **0.0 in** → "short" → retry → stopped by the hands-off guard (Kevin was at the PC).
- Re-OCR of all 41 saved previews at zooms ×2–×5 shows Windows OCR reads PO-TRY's label font with "4" as "A" and "0" as "O", sometimes with a stray space or a lost decimal point. It varies by zoom: "183A00" at ×3, "183400" at ×4, "183.400" at ×5.
- **Parser (`ocr.inch_values`):** takes the whole token before "inch" and maps A→4 and O→0 (plus the comma as a point, and stray spaces). If the result still isn't a number, it's skipped and so counts as unreadable; it never uses part of a token. The lost-decimal rule is unchanged.
- **Zooms (`read_preview_size(scales, accept)`):** OCR at ×3 first. Only if that reading is unreadable or fails the gate, try ×4 and then ×5. It uses the first reading that passes the gate; if none passes, it keeps the first reading so the gate reports why. A genuinely wrong preview reads wrong at every zoom, so this can't talk the gate into a bad rip. It just costs about 4 s more when a run is cancelled. Every zoom tried is logged.
- **Replay:** all 41 saved previews now read right at ×3, including the 3 failures. Tests: 216 pass.
- **Logging:** the corner popup's **Rip now** button now logs "Rip now pressed in the rip warning" (13:26:52's quick restart was that button, but the log didn't say so).
- **Download "not a PNG (yet)"** happened 4 times (12:43, 13:14, 18:42, 13:20). Each retry 2 min later worked. Working as intended.

## 2026-10-16

**Built (Code, 08:30): OCR keeps letters whole where a pink tick crosses the width label**
- At 02:44, #48029 (260 in) was cancelled with "preview length unreadable". The sheet is drawn narrower than its label on long runs, so a tick crossed the "n" of "inch" and OCR read "irch".
- `clean()` no longer paints pink to flat background. A pink pixel whose nearest non-pink pixels on both sides (left/right, else up/down) are lit (any channel > 75, which covers ClearType fringes) becomes white text; otherwise it becomes background.
- `_INCH` also accepts "irch". It's safe because the width must still be 20–24 in, and there must be exactly one value per label.
- All 14 saved previews read correctly with the new code.

## 2026-10-15

**Built (Code, 14:30): wait for big pictures to finish loading; retry a short preview**
- **What happened at 14:07.** Rip-now run of #47977 (24 in, 37 MB) and #47979 (270 in, 320 MB). Multi-add took both files. The W box showed a value right away, so the old `wait_loaded` went on; only #47977 was on the canvas when F3 and RIP were pressed. The preview read 24.02 in against 295.4 planned, so the gate cancelled the run. Nothing was ripped. Both sheets are held, and the run's tab was closed with "No" to save. Kevin saw the same thing from the floor.
- **Fix 1: wait on PO-TRY's process, not its window (`ripauto/busy.py`).**
  - After each Add, the agent waits until PO-TRY has read ~90% of the added files' bytes (`GetProcessIoCounters`), then until its CPU has been under 8% of a core for 4 s in a row (`GetProcessTimes`).
  - Measured idle: 0–2%. It's read-only, and it works on the elevated PO-TRY even from a plain prompt.
  - If the reads never arrive but nothing has happened for 15 s, it goes on and leaves the rest to the length gate.
  - If the counters can't be read, it falls back to a fixed 20 s plus 1 s per 10 MB (52 s for #47979).
  - The log line now ends with what was seen, e.g. "(320 of 320 MB read, quiet after 22 s)", so we learn the real load times.
- **Fix 2: a short preview gets 2 retries before the hold.** Only when the gate says "may not have loaded":
  - Cancel the preview, wait 30 s (then 60 s on the second retry) plus the quiet check, then F3, RIP and the OCR length check again.
  - Other gate reasons (unreadable, over cap, loaded twice) cancel straight away as before.
  - The hands-off guard stays armed throughout, so a person at the PC still aborts the run.
- `--force` "takes the first run anyway" now logs once, not every 2 s countdown tick.
- Tests: `tests/test_busy.py` (7). 210 pass.

**Built (Code, after the critique): priority 1, i.e. A1, A4, B3 (not live until the agent restarts)**
- **B3 (e2c21ea):** if PO-TRY's RIP queue *or* the Downloads folder can't be read, the pass is skipped and nothing is touched; sheets stay queued. While a warning is up, the popup says "Can't check PO-TRY's queue right now; trying again". After 3 misses in a row the dashboard shows error "can't read PO-TRY's RIP queue…", cleared when a read works.
- **A1 (1c8e97d, `ripauto/handsoff.py`):** armed from New until Print. Before every press, keystroke and focus change, the driver checks for any of:
  - input since the agent's own last action (its own clicks and keys are marked, so only later input counts)
  - the pointer not being where the agent left it
  - before a real click, a window from another program in front

  On any of these: back out through UI automation only (Cancel on the preview or Add dialog, no mouse), the sheets go back to **queued**, the run record says `interrupted`, and the corner popup counts down **5 min** with "Rip interrupted: someone used the mouse or keyboard". Rip now / Wait / Pause still work. After Print the guard is off.
- **A4 (f055a77):** the agent records each job tab it opens (name + PO-TRY process id; a PO-TRY restart starts the list over, because names repeat) and closes its *earlier* tabs right before its next New. The last run's tab stays open for a person to look at. It **never closes a tab it didn't open**.
  - How to close a tab isn't visible to UI automation, so `python -m ripauto.potry --probe-tab-close` (Kevin, once, admin, agent paused) opens an empty tab and tries close button → right-click "Close" → middle-click → Ctrl+W → Ctrl+F4 on it, saving the first that works to `state\potry_tab_close.json`.
  - A "save changes?" prompt is only ever answered by a button named No / Don't save.
  - Until a method is proven the agent leaves tabs alone (as now).
- Tests: 198 pass on the printer PC (10 dashboard tests run on the Pi).
- **Live results (Kevin, 13:43–13:44):**
  - **A1 proven both ways.** A dry run with a deliberate mouse wiggle stopped "before opening the file", closed the Add dialog through automation and backed out, with nothing ripped. A hands-off dry run went through New, 3 Adds, F3, RIP and the preview (22.5 × 65.237, gate ok) and Cancel with **no false trip** from the agent's own clicks.
  - **A4 solved (~13:50):** Kevin's screenshot showed an × drawn inside each job tab that isn't exposed to UI automation. New method `x_button` clicks it by position, inside the tab's right half only, and verifies the tab is gone. The probe closed an empty tab (Job28) at **12 px from the tab's right edge**; saved in `state\potry_tab_close.json`. From the next run the agent closes the tabs it opened earlier, right before its New. Observation: the probe's tab bar listed only Job1–3, 9, 10 (either closed by hand or only on-screen tabs are exposed). A tab the agent recorded but can't see is dropped from its list without being touched.
  - Earlier, **A4: no close method found.** On an empty tab (Job25), close button (none exposed), right-click "Close", middle-click, Ctrl+W and Ctrl+F4 all failed. The probe now prints the tab bar's controls and any right-click menu items. Asked Kevin how he closes a tab by hand. Until a method is found the agent leaves tabs alone (as before); it records its own tabs, so closing can start later without guessing which are its own.
- **Restart, found the hard way (13:51–14:03):** Task Scheduler runs the agent inside a job object.
  - The agent's own relaunch helper dies with it, and Windows refuses `CREATE_BREAKAWAY_FROM_JOB` (tested). The 5-minute watchdog brought it back each time (13:53, 13:58, 14:03).
  - **Fix (ee66de7):** the RIP Agent window, which runs outside the job, does the relaunch. Restart raises the flag, waits for the agent to stop at its safe point, then runs the task. **Tested live 14:03: back in 16 s.** The watchdog stays as the backstop.
- **Code's slip (14:03):** that restart test went through the window's Start path, which also turns Pause off. So it **lifted Kevin's pause**. The agent has run unpaused since 14:03:21, which is the state step 5 was heading for, but it wasn't Kevin's click.
  - **Fix (dce9dd5):** Restart now keeps the pause switch as it was; only the Start button means "go".
  - Since 14:03 the agent has downloaded #47977 (24.0 in) and is downloading #47979 (large file). It rips them with the corner warning first; nothing ripped as of 14:04.
- **Kevin (14:06): close jobs as soon as they're fully ripped**, because too many open jobs make PO-TRY's tab bar flaky. **Built (2e03634):**
  - after "RIP finished" the agent switches to the layout view, closes its own tab(s) with the × click, and returns to the RIP queue view
  - runs cancelled at the preview close their tab too
  - still only tabs the agent opened (a person's tab may hold an unsaved layout, and the agent can't tell which person-made tabs are safely ripped)
  - guarded by the hands-off check

  Restarted onto it at 14:06:58 via the window's restart path: **back in 10 s, pause state kept** (off). First real use: the ~14:33 run (#47977, #47979).
- **Earlier code in the running agent (pid 20336, 14:03:21):** everything through c968dfd, i.e. A1, A4 with `x_button`, B3, corner warning. The window's relaunch/pause fixes are in the window, which picks them up when reopened.
- **Live steps (Kevin):** pause → tab-close probe → dry run with a deliberate mouse wiggle (should stop with "INTERRUPTED… Backed out") → dry run hands-off (should finish, and close the previous dry-run tab if the probe found a method) → Restart agent → Start. Spelled out in STATUS.md.

**Decided (chat, 13:40): critique of the live build**
Full text: `docs/CRITIQUE-2026-10-15.md`. In short:
- New risks to fix first: abort the rip sequence on any human mouse/keyboard input (A1); close PO-TRY job tabs (A4); an unreadable queue skips the cycle instead of holding sheets, error after 3 failures (B3).
- Resume after a pause or downtime of more than 15 min asks whether the agent takes the orders that arrived meanwhile; the default is "done by hand" (A2).
- Route by Shopify product ID, not title (A3). Replaces the "title starts with" proposal.
- Length gate: probe intelligent layout's spacing setting to pin the gap; otherwise verify picture-by-picture when the gate can't see a missing picture (B1). Pause on a PO-TRY version change, with "Trust this version" after a dry run (B2).
- Token: step 7 (dedicated app) this week, then rotate the shared token at a quiet time; rotate anyway if step 7 slips past a week (B6).
- Ledger: keep JSON, add daily backups and a 60-day prune (B5). Add alerting for errors and new holds (B8).
- Spec drift in STATUS §5 accepted; see critique §C and the amendment note at the top of SPEC.md.

**Decided (Kevin)**
- Fetch through the Shopify Admin API using the `_Print Ready File` line item attribute, not by clicking through the Build a Gang Sheet app. Verified on #47914 and #47918: same file as the app's "Gang Sheet" button (label strip + QR), and it flips the app's Downloaded counter to 1/1.
- File names follow the app's pattern minus the design name, which Shopify doesn't store. Kevin will live without the design name.
- The `(N)` at the end of the app's file name is the line item quantity. The middle number is the sheet's position within the order.
- 8-color: never rip a job longer than 650 in. Batches aim for about 600 in, give or take 48.
- Partial batches go out once the oldest sheet has waited X minutes (starting at 30).
- Ripping is fully automatic; people print from the RIP queue as before.
- Dashboard lives on the Pi, display-only. It shows running state, machine, inches ripped, order numbers in progress, queue waiting, errors and flags. No credentials or customer names reach the Pi.
- Code and chat collaborate through this repo: chat writes the spec and decisions, Code writes the scripts.
- Only the 8-color machine is enabled for now; CMYK (gang sheet + image by size) and UV DTF get a skeleton.

**Proven in the trial**
- #47918 fetched and ripped hands-free, about 30 s from New to "RIP finished". See `trial/`.

**Proven (13:13): first fully unattended rip, on the new code**
- Kevin OK'd it. Code stopped the agent with `schtasks /end` (no admin needed), re-queued #47957/#47960/#47964, and started it with `schtasks /run`.
- Kevin had clicked **Restart agent** in the RIP Agent window around 13:09 (confirmed). Its `state\RESTART` flag made the new agent stop 2 s after its 13:08:59 start, as designed. That showed Task Scheduler's restart-on-failure does **not** restart a task that exits non-zero. Fixed (8b6c086): Restart now relaunches through `schtasks /run` itself, and `install_task.ps1` gains a 5-minute watchdog trigger. The watchdog needs Kevin to re-run the installer as admin.
- 13:10:36: agent back on the new code. **13:10:42: corner warning "197 in, 3 sheets, #47957 #47960 #47964; starts in 60 s".** 13:11:54: started after the countdown plus 5 s still. Multi-add, F3, **OCR 22.5 × 197.037** against 197.0 planned, gate ok, RIP finished 13:13:07 (~50 s), queue size 22.500×197.037.
- Ledger: all three ripped. Files are in `ripped\8color-1-20261015-131144\`. Run record: planned = preview = actual = 197.037, gap 1.0. Note: the same three sheets gave 195.037 (gaps ≈ 0) at 13:00, so F3 spacing varies between attempts, not just between sheet sets. The gate now allows 0–1 in per gap.
- Dashboard: running/idle; 332.4 in today over 2 runs (5 sheets); 0 held. Order search for #47960 shows the whole trail: waiting → cancelled at preview (13:00) → held → ripped in run …131144. #47952 shows waiting → done by hand.
- **Watchdog live (~13:17):** Kevin re-ran `install_task.ps1` as admin. The task now has the logon trigger plus a time trigger repeating every 5 min (from 13:18), with MultipleInstances IgnoreNew. "Last result 2147946720" (0x800710E0, request refused) was only the installer's start request being ignored because the agent was already running (same process, 18488, uninterrupted).
- **Small follow-up (Code):** the agent's own downloaded copies of by-hand sheets (#47951, #47952) stay in `8color-queue`. Move them to `8color-queue\by_hand\` when the hand check marks them, so the queue folder only holds work still to do.

**First unattended run (12:59–13:00): hand check right, OCR cancelled, fixed**
- The run was due 12:56:26 and waited for 60 s without input (old rule), because Kevin was at the PC. It started 12:59:57.
- **Hand check, on real hand work:** #47951 was found in Kevin's Downloads and #47952 in PO-TRY's RIP queue (ripped by hand). Both were marked by_hand and skipped.
- **Run:** #47957 + #47960 + #47964, planned 197.0 in, loaded by multi-add, F3, preview. OCR read the length as **195.037** but **no width**: on this narrow run the pink width line pokes out across the length line's column, the sheet top was found too high, and the width label was cut in half. The gate said "unreadable" → **Cancel**; nothing ripped, and the 3 sheets are held. The safety net worked.
- **Fix (1075eb1):** the sheet top is now the start of the length line's first 8+ px segment. All four captured previews read correctly, including this one (22.5 × 195.037).
- **F3 spacing varies:** 1.02 in per gap on the 3-sheet test, 1.0 on the 11:36 run, **0.02** here (195.037 against 195.0 of artwork). **Fix (646f1c9):** the shortfall check now allows gaps anywhere from 0 to `gap_in`, so long runs with tight spacing aren't wrongly cancelled. The cap and over-length checks still use the full planned length. **Open:** with many pictures, a missing small sheet is now harder to spot by length alone. Run records keep the measured gaps; revisit once there's data from full runs.
- **Next:** restart the agent onto the new code (OCR fix, gap rule, corner warning, Rip now, Restart button) and re-queue #47957/#47960/#47964. They get the hand check again before ripping. Waiting on Kevin's OK.

**Decided (Kevin + Code, ~12:40): corner rip warning, Rip now, Restart agent (built, not live until one restart)**
- **Kevin:** "a windows status widget … in one of the four corners … for 60 seconds … a warning that an auto rip is imminent." Kevin also asked about a force-rip button.
- **Built (`ripauto/warning.py`, `tools/rip_warning.pyw`):** when a run is ready, the agent shows an always-on-top popup in a screen corner (`warning_corner`, default bottom-right, above the taskbar).
  - It reads **"Auto rip in 60 s"** (`warn_before_rip_s`), then the run's inches, sheet count and order numbers, and "hands off the mouse when it starts; nothing prints".
  - Buttons: **Rip now** / **Wait 5 min** (`snooze_min`) / **Pause agent**.
  - It's a no-activate tool window and hands focus back to whatever had it, so typing elsewhere isn't interrupted. Tested: Chrome kept the foreground while it was up.
  - At zero it starts once mouse and keyboard have been still for **5 s** (`still_before_rip_s`), so it never grabs the mouse mid-movement. The popup closes just before the agent takes over.
  - **This replaces "60 s with no input before a run"** (`require_idle_s` is gone). A person at the PC now gets 60 s notice and a choice, instead of the run silently waiting until they walk away.
- **When the box appears** (Kevin asked):
  - It appears the moment a run becomes ready: full (≥ 552 in planned, which can happen right after a big order downloads) or due (its oldest sheet has waited 30 min).
  - It doesn't appear while paused, during a "Wait 5 min" snooze, or with nothing waiting.
  - It stays up until the agent actually takes the mouse.
  - If PO-TRY is busy ripping another job when the countdown ends, the box stays up saying "Waiting for PO-TRY to finish ripping another job", and the run starts as soon as PO-TRY is free, with **no second countdown**. Code fixed this while answering; it used to restart the 60 s every pass. A pending Rip now also survives a busy PO-TRY.
- **Rip now** (RIP Agent window, or the popup's button): drops `state\RIP_NOW`. The agent rips the next planned run even if it isn't full or due, after a 10 s popup countdown. Every safety stays: hand check, preview length gate, PO-TRY-busy check, pause.
- **Restart agent** (RIP Agent window): drops `state\RESTART`. The loop exits at a safe point (between cycles, never mid-run or mid-countdown) with code 3, and Task Scheduler's restart-on-failure starts it again about a minute later with any new code. No admin needed.
- **Not live yet:** the agent running since 12:26 loaded the old code. The switch needs one restart: this first time by Task Scheduler (End + Run) or an admin prompt, since the old code doesn't know the RESTART flag. After that, Restart agent works from the window.

**Decided (Kevin + Code, after go-live): control window, and ripping by hand ahead of the agent**
- **Kevin: "need a more intuitive way of enabling and disabling the agent."** A "RIP Agent" desktop shortcut (`tools\make_shortcut.ps1`) opens `tools\rip_agent_control.pyw`:
  - a coloured banner: ON (and what it's doing: next run time, or "Ripping #… 68%"), PAUSING (finishing the current run), PAUSED, OFF
  - one big button: Pause / Resume / Start
  - Open dashboard, Open log, and the last log lines

  No admin needed: Pause/Resume toggle `state\PAUSE`; Start runs `schtasks /run` on the user's own task (checked: works unelevated, and is ignored while the agent already runs). Logic in `ripauto/control.py`, tested. The shortcut is on the 8-color PC's desktop.
- **Kevin: "what happens if I rip it before it rips itself?"** Right before each run, the agent checks every sheet in it against two places, and marks a match by_hand (never ripped):
  - Downloads (an app-named file with the same order # + sheet position)
  - PO-TRY's RIP queue job names

  The run is re-planned without that sheet. **Gap:** a job loaded by hand from the agent's own `8color-queue` files, with several files in one job, is only visible by its first file's name, so sheets 2+ would rip twice. Safe ways: download from the app as usual, or pause the agent first.
- **(Code) Proposed small fix, after the first unattended run:** if a queued sheet's file has been moved out of `8color-queue` (someone took it), hold just that sheet ("file removed from queue folder; taken by hand?") and rip the rest. Today it holds the whole run for a manual check.

**Decided (Kevin + Code, after step 6): dashboard build-out and order search**
- **Kevin asked for all four:** ripped log, queue & next run, stats & history, floor TV mode. The dashboard stays **display-only for now**; Kevin will add interactive features later.
- **Kevin (mid-build), important:** "need to be able to search logs for when we're spread out across possibly 5 machines exactly maybe where specific jobs end up".
  - **Built:** the Pi keeps an **order history** (`~/oss_rip.db`, SQLite, chmod 600) of every sighting in the cleaned heartbeats from every machine: waiting (sheet), ripped in run X (inches, pictures), cancelled at preview / stopped / over cap, found done by hand, held (reason). The header search box (`/#q=<digits>`, API `GET /api/search?q=`) shows each matching order's trail across machines, oldest first. Prefix search: "4794" finds #47940–#47949.
  - On each printer PC, `python -m ripauto.find <order>` goes deeper locally: ledger state, the file's current path, runs, and every log line mentioning it.
  - Limits: history starts at deploy time (2026-10-15 ~12:15). It only knows what an agent saw: a sheet done entirely by hand on a PC whose agent never noticed it won't appear. If the Pi is down for more than ~25 runs, the runs it missed are absent there; they're still in each PC's own run records.
- **Heartbeat additions** (beyond SPEC §8's minimum, same privacy rules; the leak test covers each with data in it):
  - `queue_detail`: each waiting sheet (order, sheet, copies, length, minutes waited, low-res), plus the next run (orders, planned inches, ready/reason, flush time, inches to full)
  - `recent_runs`: last 25
  - `by_hand`: last 25
  - `history`: 30 days of inches / runs / sheets / by-hand

  About 5 KB; the Pi's request cap went from 64 to 128 KB. The Pi re-cleans every new field: whitelist, order-number and date patterns, length caps.
- **Views:**
  - **Floor** (cards; click one for detail)
  - **Machine detail** (`/#m=<id>`): tiles, Now, Next run with when it goes, Waiting table, Ripped log (agent runs + found by hand, newest first), inches-per-day bar chart for 30 days with hover tooltip and a table view
  - **TV** (`/tv`): big numbers for a wall screen
  - **Search**

  Chart color `#3d8fe0` passed the dataviz validator on the dark card surface. Hand counts are a different measure, so they're in the tooltip and summary, not on a second axis.
- **Note:** a machine whose agent isn't running shows **offline** 2 minutes after its last heartbeat. That's correct today, because the loop isn't installed yet.

**Decided (Code, step 6 build): the agent loop**
- **`python -m ripauto.agent --loop`:** fetch every `fetch_every_min` (2), a cycle every 20 s, heartbeats on every change plus every 30 s. Errors are logged and reported (`error` + last_error), PO-TRY is reconnected, and after a 60 s back-off it carries on; the loop never exits on its own. A failed fetch is retried at the normal 2-minute spacing.
- **The loop stays out of PO-TRY unless it's about to rip.** People print from this PC's RIP queue, so a cycle first plans from the ledger alone. It only touches PO-TRY (to read the queue for the hand check, then rip) when all of these hold:
  - a run is full or due
  - nobody has used the mouse or keyboard for **`require_idle_s` = 60 s** (Windows `GetLastInputInfo`)
  - no queue row says "Ripping"
  - the pause switch is off

  This is stricter than SPEC §9's "nobody should use the mouse during a rip": the agent waits for the PC to be idle instead of assuming it.
- **Hand check timing:** it now runs when a run is ready (just before ripping), not on every pass. Sheets taken by hand are still caught before anything is loaded, and the per-run re-check stays.
- **Pause switch:** while `state\PAUSE` exists, nothing new starts and the dashboard shows `stopped`. A run in progress finishes first: pause is checked between cycles and again right before a run starts, never mid-dialog. The process stays running and resumes when the file is removed (`tools\pause.cmd` / `tools\resume.cmd`). That way Task Scheduler doesn't need to restart anything; SPEC §9 says "stop", and this reads it as stop working, not exit.
- **PO-TRY:**
  - The loop connects only when it first needs PO-TRY (starting it if it isn't running) and maximizes it (SPEC §9).
  - After a real rip PO-TRY is left on the **RIP queue view**, where people print the job. It used to go back to the layout view.
- **Log:** `state\agent.log`, rotated at the first write of a new day to `agent-YYYY-MM-DD.log`, 30 days kept. `state\rip_log.txt` keeps everything before step 6. Only order numbers, file names and states are logged; never the token or the dashboard key.
- **One loop at a time:** `state\agent.lock` holds the process id; a stale lock is taken over. While the loop runs, `fetch --download`, `fetch --mark-done` and `agent --once` refuse to run, so only one thing writes the ledger.
- **Task Scheduler** (`tools\install_task.ps1`, run once as admin):
  - task "OSS RIP Agent", started at logon of the current user
  - Interactive (it needs the desktop to drive PO-TRY), Highest privileges
  - runs `pythonw` (no console window); restart every minute on failure, up to 999 times; no time limit; one instance

**Decided (Kevin, after step 5): Production Command (:8084) retired in favour of RIP Automation (:8085)**
- Kevin: :8084 (`oss_command.py`, "Production Command") was an earlier attempt at this and is now mostly redundant.
- **Operations Hub (:8081, `oss_uptime.py`):**
  - new **RIP Automation** button → `http://osshub.local:8085`
  - the Production Command button is greyed out (grayscale, dashed, "Replaced by RIP Automation") but still clickable
  - each external button now shows its real port; it used to say "Port 8080" for all
- **:8084:** shows a popup on every visit: "Production Command has been replaced", with **Open RIP Automation** and **Stay here**. The page itself is unchanged and still runs.
- Deployed to the hub with backups first (`~/oss_uptime.py.bak-20261015`, `~/oss_command.py.bak-20261015`). All six hub services were active after the restart. Those two files live outside this repo (`C:\Users\kevin\projects\`), so they aren't versioned here.
- Not done: :8084's workstation-agent features (download ledger, scanner cross-reference, screenshots) weren't moved anywhere. Its service is still running. Retiring it fully (stopping the service, uninstalling `oss_ws_agent.exe` from the three PCs) is Kevin's call.

**Decided (Code, step 5 build): heartbeat + Pi dashboard**
- **Hub pattern followed** (from `oss_command.py`):
  - new independent service `oss_rip` (`dashboard/oss_rip.py`) on **osshub.local:8085**, its own systemd unit
  - settings `~/oss_rip_settings.json` (chmod 600) with a generated agent key; agents send it as `X-Agent-Key`
  - request bodies capped at 64 KB; JSON via `get_json(silent)`
  - no existing hub service was touched; ports 8080–8084 belong to the others
- **Display-only, no PIN.** The page and `/api/machines` are open on the LAN, since they hold only order numbers, inches and states. There are no routes that can start anything (a test checks the route list). Other hub pages are PIN-gated; **chat/Kevin:** say if this one should be too.
- **Machine cards come from `machines.json`.** Deploy copies `config/machines.json` to `~/oss_rip_machines.json`. Renaming, adding or switching off a printer is a config change: edit, re-copy, `systemctl restart oss_rip`.
- **Offline** = no heartbeat for 120 s, judged by the Pi's own clock, not `sent_at`. The last heartbeat per machine survives restarts (`~/oss_rip_state.json`, chmod 600).
- **Private data:**
  - The agent builds heartbeats only from counts, order numbers and states.
  - Hold reasons and errors go through `scrub()`: paths, links, emails, app file and job names (which carry customer names), tokens, 10+ digit IDs.
  - The Pi re-cleans everything again: known machine ids only, whitelisted fields, order-number pattern, same scrub.
  - The SPEC §8 leak test (`tests/test_status.py`) feeds customer names, emails, an address, file URLs, Windows paths, Shopify IDs and a token through a ledger and run records, and asserts none reach the JSON. A Pi-side test does the same for incoming heartbeats.
- **`dashboard_key` lives in `config/secrets.json`** next to the Shopify credential, and is sent only to the Pi. SPEC §3 says secrets.json is "Shopify credential only"; **chat:** please widen that to "credentials", or say where the key should live instead.
- **What the agent reports:**
  - Heartbeats go out on every state change: planning → ripping (with progress from PO-TRY's queue Processing %) → idle, or holding (gate cancelled) / error (stopped).
  - They also go out every 30 s from a background thread, and as a final `stopped` when `agent --once` exits.
  - An unreachable Pi is logged once and never stops a rip.
  - `today.sheets` counts pictures (physical sheets, copies included) from today's real ripped runs; `today.inches_ripped` uses the queue's actual length.
- **The 20 accidental-download holds were moved to `by_hand`** (Kevin: already downloaded and printed by hand), so the dashboard flags only what needs a person.
- **Live check:** a heartbeat from the 8-color PC shows on `http://osshub.local:8085` as Printer #3 (8-color), stopped, 135.4 in today (1 run, 2 sheets), 0 waiting; the other 4 printers show "switched off". The ripping view (run orders + progress bar) appears on the next real run.

**Incident (Code, step 5): Shopify token printed into the Code transcript**
- GitHub's secret scanning blocked a push because the leak test held a made-up `shpat_…` literal. The fake token is now built at runtime, and that push went through.
- While checking the repo for other token-shaped strings, Code ran a plain `grep -r` over the working folder. It matched the git-ignored `config/secrets.json` and **printed the real (borrowed, shared) Shopify token into the Code session transcript**.
- **Not committed, not pushed:** `secrets.json` is git-ignored and appears in no commit (checked with `git log --all`).
- **Kevin to decide:** treat the token as exposed and rotate it? It's shared with the hub, the scanners, the paperwork printer and the tracker, so rotating it touches all of them. The dedicated Shopify app (SPEC §10, step 7) would give this system its own credential and end the sharing.
- Rule from now on: secret checks use `git grep` (tracked files only), and nothing reads or prints `secrets.json`.

**Proven (11:36): first real multi-sheet rip by the agent**
- `python -m ripauto.agent --once --force` (Kevin, admin prompt) on #47946 (121.2 in) + #47947 (13.2 in):
  - hand check, multi-add of 2 files, F3
  - preview OCR 22.5 × 135.4 against 135.4 planned, gate ok
  - Print in the preview, then "Ripping 41…87" → "RIP finished 100" in ~35 s
  - queue size `22.500×135.400`
- Ledger: both `ripped` (1/1 copies, run `8color-1-20261015-113554`); files moved to `ripped\8color-1-20261015-113554\`.
- The run record has planned 135.4, preview 135.4, actual 135.4, **measured gap 1.0 in** (= `gap_in`).
- The agent's own job is now recognized by the hand check.
- Nothing was printed. Printing stays with people, from PO-TRY's RIP queue.
- **SPEC §12 step 4 is done.** Code stops here and checks in before step 5 (heartbeat + Pi dashboard).

**Found and fixed (Code, between 11:30 and 11:34): OCR misread on the first live agent dry run**
- Kevin's `agent --once --dry-run --force` on #47946 + #47947 (135.4 in planned) worked end to end:
  - hand check, plan, multi-add of 2 files, F3, RIP, preview, Cancel
  - ledger untouched
- But OCR read the preview as `*2.500 inchk` and `135400 inch`. The pink tick touching the "2" merged into it, and the decimal point was lost. The gate saw 135400 in (over cap) and **cancelled, as it should**.
- **Fix:**
  - **Find the sheet:** locate the pink length line; its first pink pixel is the sheet's top edge.
  - **Width:** OCR only the band above the sheet.
  - **Length:** OCR only a thin strip around that line.
  - **Clean the strips:** pink is painted out first, and no artwork is in view.
  - **Lost decimal:** a 4+ digit value with no point gets one before its last three digits, since PO-TRY always prints three decimals (`135400` → 135.400).
  - **Width check:** the width must be within 3 in of the 23 in canvas, or the reading is rejected.
- **Re-checked on all four previews captured so far** (Kevin's screenshot, both 3-sheet dry runs, this run): all read correctly (22.5 × 65.237 three times, 22.5 × 135.4). 131 tests pass.
- Safety net unchanged: even a "clean" misread has to land within max(3 in, 1%) of the planned length and under the cap, or the run is cancelled.

**Decided (Kevin, ~11:2x, before the 11:29 dry run): go-live floor**
- Kevin has no undownloaded 8-color orders; the last one in, **#47942**, he already grabbed by hand. So **`start_from_order` = 47943**, set in `machine.json` on the 8-color PC. List-only fetch from #47943 shows 0 sheets, so nothing is waiting.
- Hand check verified on real hand downloads: #47932, #47935, #47940 and #47942 all come back **by_hand** against Kevin's Downloads folder (read-only check). #47932 and #47935 were "free" at the 11:xx check, before Kevin downloaded them.
- The first real multi-sheet rip has to wait for new 8-color orders at #47943 or above. For that first test, **nobody hand-downloads them**. If someone does, the hand check skips them (safe), but there's then nothing to test on.

**Verified live (Kevin, 11:16–11:17) and decided (Code)**
- **OCR gate works inside PO-TRY.** Both 3-sheet dry runs logged `preview size: (22.5, 65.237)   planned 65.2 in   gate: ok`, then Cancel.
- **Multi-file Add works.** With all three files in one Add, the preview read the same 65.237 in, so all three loaded. The agent now uses it by default (`multi_add`, chunks of 10 files per dialog). If the dialog ever refuses, the rest go in one at a time in the same job.
- **F3 lands.** In the multi-add run the selected picture moved from Y 52.98 to 46.78 when F3 ran, and both runs gave the same stacked 65.237 in layout. This closes the "confirm F3" open item.
- **The W/H "loaded" check proves nothing.** In the one-at-a-time run the boxes stayed on the first picture (H 45.780) for all three adds. So the OCR length is the real check that every picture loaded, and the gate is now **tight both ways**: cancel if the preview length is off from planned by more than max(3 in, 1%). The smallest sheet is about 8 in including its gap, so a missing or doubled picture is caught. Measured today: planned 65.197, preview 65.237 (0.04 in apart). If real runs show F3 spacing varies more than this, the tolerance needs loosening; run records keep the numbers.
- PO-TRY names a job after whichever picture comes first, which with multi-add may not be the first in the agent's list. The queue row is matched against every picture's name, and the run record keeps the name PO-TRY actually used.

**Decided (Kevin + Code, ~11:1x, before the 11:16 dry runs): length gate reads the preview by OCR (option A), with the post-rip check (B) as a backstop**
- Kevin's screenshot of the 3-sheet preview shows **`22.500 inch`** across the top and **`65.237 inch`** sideways down the left edge of the picture area, with all three sheets drawn. Length is the one that matters.
- The driver captures the preview's picture area (`QGraphicsView`, a screenshot only) and runs Windows' built-in OCR (`tools/ocr_image.ps1`, en-US engine already on the 8-color PC). The image as-is gives the width; turned 90° counter-clockwise, it gives the length. On Kevin's screenshot, OCR returned exactly `22.500 inch` and `65.237 inch`. Gate rule: anything other than exactly one "N inch" in each pass is unreadable → Cancel and hold. OCR inputs are kept in `state/previews/` for checking.
- **Backstop:** after the rip, the queue row's `Size (inch)` is checked. If it's over `cap_in`, the run is held with "delete it from the RIP queue by hand, don't print it". The agent never touches the queue toolbar.
- **`gap_in` = 1.0** (was 0.5). Kevin's preview: 65.237 in for 63.197 in of artwork over 2 gaps = 1.02 in per gap. Every run record now stores `actual_in` and `gap_measured_in`, so this can be tuned from real runs.
- This replaces SPEC §7 step 6's "find it via UIA" (not possible). The intent is unchanged: read the length off the preview before confirming. **Chat:** please update the spec wording.

**Found (Code, from Kevin's 11:05 dry runs and probes)**
- The 3-sheet batch dry run worked end to end: New → 3 Adds → F3 → RIP → preview → Cancel. Log: `state/rip_log.txt` on the 8-color PC.
- **The Print preview exposes no size text to automation.** Its readable controls are all settings: driver, copies, error compensation, print-area margins, mirror/rotate, information output. The picture is drawn in a `QGraphicsView` that automation can't see into. So SPEC §7 step 6's "find it via UIA" can't be done, and the gate correctly reported "preview length unreadable" and cancelled both times. **As built, no real rip can happen: every run would be cancelled at the gate.** That's the safe state until the question under Open is decided.
- **Pictures can't be counted through automation either.** The layout canvas (`LayoutDesignGraphicsView`) doesn't expose its pictures; only the X/Y/W/H boxes of the selected one are readable.
- **Multi-file Add:** with `--try-multi-add`, the dialog closed after taking all three names, and the W/H boxes then showed the last file (22.1 × 10.257 in, #47918). That's consistent with all three loading, and the load took 9 s against ~19 s one at a time. It can't be confirmed by counting (see above), so Kevin is asked whether he saw three pictures.
- **Gap is probably about 1 in, not 0.5.** After F3, #47918 sat at Y = 46.78 in, right after the 45.74 in #47862 sheet. That's one data point. At 18 pictures, a 0.5 in estimate would under-plan by about 9 in. `gap_in` should be calibrated from real runs using the queue's `Size (inch)`.
- **Job tabs:** 12 are open now (Job1–Job12, mostly from dry runs). Tab items expose no close control, so closing tabs needs another route (to be probed).

**Decided (Code, step 4 build)**
- **Real rips only through the ledger.** `python -m ripauto.potry` now only inspects and dry-runs (`--dry-run` is required). Real rips go through `python -m ripauto.agent --once`, which keeps the ledger, the hand check and the run records. The old "rip everything in the queue folder" path is gone, because it bypassed all three.
- New modules not in SPEC §3's layout: `ripauto/ledger.py` (sheet states) and `ripauto/handcheck.py` (SPEC §4 check). `agent.py` has one cycle (`--once`) and `--plan`; the loop is step 6.
- **Ledger states:** `queued → in_run → ripped | held | by_hand`, plus `copies_ripped` for split copies. A sheet goes back to `queued` until every copy is done, and its file stays in the queue folder until then. Entries without a status (the trial's #47918, `--mark-done`) are "untracked" and never planned. Sheets found `in_run` at the start of a cycle (an interrupted run) are held for a person, since nobody knows what PO-TRY did with them.
- **Hand check details:**
  - The agent's own jobs in PO-TRY's queue are recognized by their job name (first picture's file name, from the run records) and skipped, so copy 2 of a split sheet isn't mistaken for hand work.
  - Per spec, an unreadable queue table holds every queued sheet as "manual check". That could be noisy if reading the table is flaky; watch it.
  - `hand_download_dir` is a new `machine.json` key (8-color: `C:\Users\kevin\Downloads`). It must not equal `queue_dir`.
  - A read-only check against the real Downloads folder (11:xx) found #47905, #47914, #47927 and #47928 already downloaded by hand; #47932 and #47935 came out free.
- **Length gate:**
  - It reads every text in the Print preview. It accepts either one text holding "W inch × L inch", or exactly two "N inch" texts (width first). If the first value is too wide to be the width, the pair is swapped.
  - It cancels when the size is unreadable, over `cap_in`, or more than max(12 in, 5%) shorter than planned (a picture probably didn't load).
  - The exact preview layout is unconfirmed until the probe runs at `--pause-at preview`.
- **Picture count (SPEC §7 step 3):** not read from PO-TRY yet, because no control for it is known. For now each Add waits for the picture to load (W box > 0), and the gate's shortfall check catches a missing large picture. A probe at `--pause-at loaded` should show whether PO-TRY lists the pictures somewhere countable.
- **Queue statuses:** kept in one list in `potry.py`. Done = a status containing "finish", or Processing at 100 with anything but "Ripping". Failed = "fail" or "error" in the status. Unknown statuses are logged so they can be added.
- After a real rip, the run record keeps the queue row's `Size (inch)` (e.g. `22.500×601.001`) as the actual length.
- **Dry runs** (`agent --once --dry-run`) leave the ledger untouched. They still write a run record named `…-dry`, which doesn't count as one of the agent's own jobs.
- **Run IDs** look like `8color-1-20261015-133005`, with `-2`, `-3` added when one cycle rips several runs.

**Incident (Code, 10:41–10:43): backlog downloaded by mistake**
- While adding the `start_from_order` guard to `fetch --download`, Code ran `fetch --download` to see the guard refuse. The edit had silently failed to apply, so the old code ran with no floor and downloaded 20 sheets from the backlog, plus part of a 21st, before Code killed it. **Nothing was ripped; PO-TRY wasn't touched.**
- Sheets downloaded: #43227, #47347, #47435, #47497, #47538, #47541, #47552, #47615, #47704, #47749, #47768, #47771, #47775, #47799, #47806, #47814, #47860, #47862 (both sheets), #47874. #47887 was cut off partway (the `.part` file).
- **Side effect:** downloading through `_Print Ready File` flips the app's Downloaded counter. Those orders probably now show **1/1** in the Unfulfilled tab even if nobody printed them, and #47887 may too. That makes the "oldest order still at 1/0" reading for `start_from_order` unreliable for anything up to #47887.
- Contained: all 21 files moved to `C:\Users\kevin\Downloads\8color-hold-accidental-20261015` (nothing deleted). Their ledger entries are `held`, with the reason, so nothing will plan or re-download them. The queue folder is empty apart from `ripped\`.
- Rule from now on: Code never runs `fetch --download` (or anything that downloads, rips or prints) to test something. Guards get unit tests; live commands are list-only unless Kevin asks.
- **Kevin (10:5x): "that's alright".** #43227 is from Aug 27, 2026, and #47874 is from last night; Kevin had already downloaded and printed it by hand this morning. So the accidental range was already handled, and the counters it touched were already 1/1. The held files and ledger entries stay as they are (held, never ripped); the hold folder can be cleared whenever Kevin likes. Floor reading for `start_from_order` is unaffected.

**Decided (Code, applying the 10:xx answers to steps 1–3)**
- **Q1, how "when they fit" is read:** the same way as rule 2 for orders. A sheet's copies split only when **all copies together exceed the cap**, so they could never share one run; then they go one copy at a time into consecutive runs. If the copies fit under the cap together but not in the current run's remaining room, the whole sheet moves to the next run rather than leaving some copies behind. Example: 3 × 300 in is split (copies 1–2 plus other work in run 1, copy 3 in run 2); 3 × 100 in after a 500 in sheet moves whole to run 2. If Kevin would rather fill the current run with some copies and move the rest, that's a small change to `_units`/packing; say so.
- Width: copies stack lengthwise on the 23 in canvas, and 22.1–22.5 in sheets can't sit side by side, so "overrunning" means run length, as chat read it. Nothing in the code suggests otherwise. `max_width_in` = 23 is in `machine.json` and the example.
- `config/machines.json` shape: `lines` (label, `product_rules`) and `machines` (line, label, `enabled`). `enabled` lives here, not in `machine.json`, so the one-machine-per-line check can see every machine. `machine.json` keeps `machine_id` plus local settings, and refuses to load if it still has `label`, `product_rules` or `enabled`, so there's only ever one source for them. A switched-off machine refuses to start.
- Overlap check, two layers: at startup, any two rules on different lines where one `title_contains` contains the other are refused. At fetch time, a title that matches more than one rule (any line, enabled or not) stops the fetch. The startup check can't catch two unrelated strings that both happen to appear in one title; the fetch-time check does.
- `machine_id` for the 8-color PC is now `8color-1` (was `8color`).
- `potry.py` already treats any status containing "finish" as done, so "Transmission finished" is handled today. "Transmitting" or other statuses without "finish" are not yet; that goes into step 4's status list.

**Decided (Kevin, 10:31): working by hand alongside the agent; the shop map**
- People will keep downloading some orders by hand (special instructions etc.), so overlap has to be detected, not forbidden. Shopify has no trace of the app's 1/0 → 1/1 counter (#47930 vs #47932 compared: no tag, metafield, attribute or event differs). Detection is local: PO-TRY's RIP queue plus a scan of `C:\Users\kevin\Downloads` (hand-downloads for 8-color always happen on the 8-color PC). Match → skip as "taken by hand". **If the agent can't tell, it holds the sheet as "manual check".** Details in SPEC §4.
- Go-live floor: `start_from_order` = the oldest 8-color order still at 1/0 in the app's Unfulfilled tab, read by Kevin once. This replaces the "backlog cutoff" open item.
- Shop map: CMYK Printer #1, CMYK Printer #2, 8-color Printer #3, UV Printer #1 (old), UV Printer #2 (new). Each has its own PC. SPEC §2 now has lines (cmyk, 8color, uv) with machines inside them. Two machines on one line must not both be enabled until the dispatcher exists.
- Future: a shared network dispatch folder routes work to each printer PC (SPEC §13). The dashboard must be configurable for machines being added, renamed or switched on and off.
- The dashboard doubles as the record of what's been ripped and when, by the agent or by hand.

**Decided (Kevin, 10:22): answers to Q1–Q3 and width**
- **Q1 Quantity:** each copy is loaded as its own picture (Add the file N times). Keep a sheet's copies in one run when they fit; when they don't, the remaining copies go into the next run(s), oldest-first as usual. So copies **can** split across runs. A sheet is held only when a **single copy** won't fit under the cap. This replaces SPEC §6 rule 1's "all N copies in the same run" and the qty part of rule 5. (Kevin said "overrunning max width"; read as overrunning the run length, since copies stack lengthwise on a 23 in canvas. Flag it if the code suggests otherwise.)
- **Q2 Low-res:** rip normally; carry the flag to the dashboard. No hold.
- **Q3 Priority:** none. Strictly first come, first served: lowest order number first, then position. (This is what the planner already does.)
- **Width:** 8-color canvas is 23 in and never changes; sheets come in around 22.1–22.5 in. Set `max_width_in` = 23 for 8-color; hold anything wider.

**Verified on the printer PC (10:18)**
- Ported driver passes both checks: `--inspect-queue` read 4 rows; dry run on the #47918 copy went New → load → F3 → preview → Cancel cleanly. Repo lives at `C:\Users\kevin\projects\rip-automation` on the 8-color PC.
- New queue status seen: **"Transmission finished"** (Steve Springer's job, after it was printed). So statuses move on past "RIP finished" once a job is sent to the printer. For step 4: treat any later status as rip-complete too, since an operator may print before the agent polls. Collect the full set of status strings as they show up (likely something like "Transmitting" too) and keep them in one place in `potry.py`. For step 5: the dashboard can also report inches printed today from these statuses.

**Decided (chat, reviewing steps 1–3)**
- Steps 1–3 reviewed: 49 tests pass, no secrets tracked. Code's planner decisions below are accepted as written.
- Yes to a committed `config/machines.json` listing every machine's product rules (no secrets). Each agent loads it at startup and refuses to start if any title rule could match more than one machine. Each PC's `machine.json` names its `machine_id` and keeps its local settings (folders, limits). SPEC §2 and §3 updated.
- Step 4 plan in STATUS.md approved as proposed, including `--pause-at-preview` for the read-only probe. The length gate cancels when the length is unreadable, not only when it's over the cap.

**Decided (Code, build steps 1–3)**
- The token moved from `C:\Users\kevin\rip-auto\config.json` to `config/secrets.json` on the 8-color PC. The old file keeps a blank `access_token` and a note saying where it went, so the trial scripts no longer run. The trial ledger was copied to `state/processed.json` so the agent won't fetch #47918 again.
- When getting a token fails, the error now shows only the HTTP status, not Shopify's response body.
- `_Actual Height` comes as e.g. `"44.58 in"` (inches) but leaves out the label strip. #47918 says 9.06 in; the downloaded PNG measures 10.257 in, which matches PO-TRY. The PNG wins; the attribute is used only when the PNG has no `pHYs` chunk.
- Planner: the cap covers planned length *including gaps*. So a qty-N sheet is held when its copies plus gaps go over 650 in (2 × 325 in = 650 + 0.5 gap → held). This is a little stricter than "length × qty".
- Planner: a lone sheet, or a whole order that fits under the cap, may make up a run of up to `cap_in` (650 in) by itself. Anything joining a run must keep it at or under `target_in + window_in` (648 in). Without this, rule 3 would block a 649–650 in sheet that rule 5 says can rip.
- Planner: the flush clock starts when the sheet landed in the queue (`fetched_at`), not when the order was placed. Queue order is order number, then position.
- Planner: a held sheet doesn't hold back the rest of its order. Low-res sheets aren't held (Q2 is still open); the flag carries through.
- Tests use stdlib `unittest` (`python -m unittest` from the repo root), because pytest isn't installed on the printer PC.

**Open**
- Q4 Is 30 min the right flush wait?
- Q5 UV DTF product titles; caps and targets for CMYK and UV.
- Q6 Computer (host) names for each of the five printer PCs.
- ~~Confirm the driver's F3 keypress actually lands.~~ Confirmed 11:17 (see "Verified live").
- Replace the borrowed Pi paperwork-printer token with a dedicated Shopify app (read_orders, read_customers, Name access).
- ~~(Code) "Matches two machines → refuse" can't be fully checked~~ settled: `config/machines.json`, built (see Code entry above).
- ~~(Code) Printable width for 8-color?~~ settled: 23 in.
- ~~(Code) DECISION NEEDED: length gate~~ settled ~11:1x: OCR on the preview + post-rip backstop (see above). Was: **how to check run length before confirming the rip** (SPEC §7 step 6 can't be done through automation; see "Found" above). Options:
  - **A. Windows OCR on the preview.** Take a screenshot of the preview's picture area and read it with Windows' built-in OCR. The en-US engine is present on the 8-color PC and Pillow is installed. This only works **if the preview actually draws "22.500 inch × 601.001 inch" somewhere**, which is unconfirmed. Code can check without risk: Kevin pauses a dry run at the preview, Code takes a screenshot and OCRs it (read-only), Kevin presses Enter, and the run Cancels.
  - **B. Rip, then check.** After Print, the queue row's `Size (inch)` (e.g. `22.500×601.001`) is readable. If it's over the cap, the agent holds the run and flags "delete this job from the RIP queue by hand"; it never touches the queue toolbar. Weaker: an over-cap job sits ripped in the queue until someone deletes it.
  - **C. Trust the planner with margin.** Calibrate `gap_in` from real runs, and plan to a lower ceiling (e.g. 640) so real length stays under 650. Use B as the backstop.

  Code recommends **A if the text is there, plus B always** (B also gives the numbers to calibrate `gap_in`). If the preview has no size text, then **C + B**.
- (Code) **UV sheets would route to the CMYK line (found ~11:2x, read-only check of 45 unfulfilled orders from #47900).** The shop's four DTF products are:
  - `DTF (4 Color CMYK) - Gang Sheet Builder - INSTA Peel Film` (28 items)
  - `DTF (4 Color CMYK) - Image by Size - INSTA Peel Film` (8 items)
  - `DTF Color-Pro (8 Color CMYK+RGBO) - Gang Sheet Builder - INSTA Peel Film` (9 items)
  - `UV DTF (4 Color CMYK) - Gang Sheet Builder - UV Transfer Stickers` (2 items)

  Matching is "title contains", and the UV title *contains* the CMYK rule's text, so UV sheets land on the `cmyk` line. **No effect today:** only `8color-1` is enabled; the 8-color sheets (all 9) routed correctly, and 4-color, UV and blanks/HTV are all skipped. But it must be fixed before CMYK or UV is switched on. Also, adding a UV rule as things stand would trip the overlap check and stop every fetch.

  **Proposed fix (spec change, chat to decide):** match on **title starts with** instead of contains. Rules would be:
  - 8color: `DTF Color-Pro (8 Color CMYK+RGBO)`
  - cmyk: `DTF (4 Color CMYK) - Gang Sheet Builder`, `DTF (4 Color CMYK) - Image by Size`
  - uv: `UV DTF (4 Color CMYK) - Gang Sheet Builder`

  None of these is a prefix of another, so the "matches two lines → refuse" rule keeps working.
- Q5 (partly answered by the data): the UV DTF product title is `UV DTF (4 Color CMYK) - Gang Sheet Builder - UV Transfer Stickers`. Kevin to confirm it's the only UV product, and whether UV #1 and #2 can both print it.
- (Code) **Job tabs pile up** (one New per run, SPEC §7). Closing the run's tab isn't built yet; it needs a probe of the tab's close control.
- ~~(Code) Multi-file Add~~ confirmed 11:17 by the OCR length; now the default.
- (Code) **SPEC wording to tidy:** §3's config-split bullet still lists "label, product rules" under `machine.json`, and §4 says "Product rules come from `machine.json`". Both contradict §2 (rules live in `machines.json`). Code follows §2.
- ~~(Code) Backlog cutoff~~ settled 10:31, see above. Was: **Backlog cutoff before go-live (step 6).** The live list shows 26 unhandled 8-color sheets on unfulfilled orders, going back to #43227. Most were presumably printed by hand already. Before the agent runs unattended, we need either `fetch --mark-done` (optionally with `--from`) or a `start_from_order` setting. Also #47914 was hand-downloaded during the verification, but the agent's ledger still shows it as new.

