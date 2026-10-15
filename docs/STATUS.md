# Build status (Code → chat handoff)

Claude Code rewrites this file at the end of every working session and pushes it. Chat reads this file first. Decisions and open questions live in `docs/DECISIONS.md`; the spec lives in `docs/SPEC.md`. Git history holds older versions of this file.

**Last updated:** 2026-10-15 ~12:35, agent dry run on #47946 + #47947 passed (OCR gate ok); first real rip waits on Kevin's go-ahead · **HEAD:** see `git log -1`

## Latest (11:50): read this first

- **Verified live in PO-TRY (Kevin, 11:16–11:17):**
  - The OCR gate read `(22.5, 65.237)` against 65.2 in planned on both 3-sheet dry runs.
  - Multi-add loaded all three files in one Add, since the length was the same.
  - F3 lands.
- **Changes since:**
  - The gate now cancels if the preview length is off from planned by more than max(3 in, 1%) either way, which catches a missing or doubled picture.
  - The agent uses multi-add by default, in chunks of 10 with a one-at-a-time fallback.
  - The queue row is matched by any picture's name.
- Tests: 127, all passing.
- **Step 4's PO-TRY side is done apart from a real rip.** Still open: closing job tabs (a dozen are piled up) and the picture count (covered by the tight length gate).

### Path to the first real multi-sheet rip

1. ~~Kevin: give `start_from_order`~~ **Done ~11:55: 47943.**
2. ~~Code: list-only + hand check~~ **Done ~12:1x:** #47946 (22×120) and #47947 (22×12, low-res) came in; both had print files and were free in the hand check.
3. ~~Kevin's go-ahead → Code: download~~ **Done ~12:1x** (`fetch --download --limit 2`):
   - #47946 → 121.2 in measured from the PNG
   - #47947 → 13.2 in

   `agent --plan`: one run of **135.4 in**, 2 pictures, `waiting` (not full; the flush comes 30 min after download).
4. ~~Kevin, admin prompt: agent dry run~~ **Passed ~12:35** on the re-run after the OCR fix:
   - `preview size: (22.5, 135.4)   planned 135.4 in   gate: ok`, then Cancel; ledger untouched.
   - F3 put the second sheet at Y = 122.2 (the 121.2 in sheet plus the 1.0 in gap), matching the plan exactly.
   - (The first try at ~12:2x misread the OCR and cancelled; see DECISIONS.md.) The command was: `python -m ripauto.agent --once --dry-run --force`. This runs the hand check, plans, loads the first run, reads the preview and presses Cancel; the ledger stays untouched.
5. **← NOW, waiting on Kevin's go-ahead. Admin prompt:** `python -m ripauto.agent --once --force`. This is the first real run: it rips (never prints), records the run, and moves the files to `ripped\{run-id}\`.

---

## Where we are

| SPEC §12 step | State |
|---|---|
| 1–3 | **Done** |
| 4. Driver multi-file runs + length gate + hand-download check + `start_from_order` | **Code done; dry runs pass live, including the OCR gate and multi-add (11:17).** No real rip yet: floor is #47943, waiting for new orders (path above). |
| 5–8 | Not started |

Tests: `python -m unittest` from the repo root runs 131 tests, all passing.

Earlier today: the 10:41 incident (backlog downloaded by mistake) is resolved. Those orders were already printed by hand; details are in DECISIONS.md.

## Step 4: what was built

- **`ripauto/ledger.py`:** sheet states `queued → in_run → ripped | held | by_hand`, plus `copies_ripped` so split copies finish over several runs.
- **`ripauto/fetch.py`:** `--download` refuses to run without a floor (`start_from_order` in `machine.json`, or `--from`). New sheets enter the ledger as `queued`.
- **`ripauto/handcheck.py`:** SPEC §4 check against PO-TRY queue job names and the top level of Downloads. It returns by_hand, manual or free, and it ignores the agent's own jobs.
- **`ripauto/potry.py`:**
  - `rip_pictures` loads one run as one job, one picture per copy.
  - It reads the job size in the Print preview and applies the length gate: Cancel if the size is unreadable, over the cap, or far shorter than planned.
  - Queue statuses are kept in one list; the row's `Size (inch)` is recorded as the actual length.
  - This command is now dry-run/inspect only, with `--batch`, `--pause-at loaded,preview` and `--try-multi-add` for probing.
- **`ripauto/agent.py`:** one cycle (`--once`):
  1. Hold sheets left mid-run.
  2. Hand check.
  3. Plan.
  4. Re-check each run just before it starts.
  5. Rip, finish the ledger, move fully ripped files to `ripped\{run-id}\`, and write `state/runs/{run-id}.json`.

  `--plan` shows the plan without touching PO-TRY or the ledger. `--dry-run` leaves the ledger untouched.

## Verified (without PO-TRY)

- All 121 unit tests pass, including a full simulated cycle with a fake PO-TRY. The cases covered:
  - rip and move files, flush timing, `--force`
  - dry run untouched, gate refusal, driver stop, interrupted run
  - by hand in Downloads or the queue, manual check, unreadable queue
  - the agent's own earlier job not counted as hand work
  - split copies over two runs
  - a missing file
- The hand check against the real Downloads folder (read-only) marks #47905, #47914, #47927 and #47928 as already taken by hand. Only #47932 and #47935 are free.
- Live `fetch` (list only) and `agent --plan` run. The plan is empty right now because nothing is queued until `start_from_order` is set and a download runs.

## Earlier Kevin checks (done)

- 11:05: the probe dry run and the first multi-add attempt.
- 11:16–11:17: live OCR gate plus multi-add dry runs, both passed.
- `C:\Users\kevin\Downloads\8color-dryrun` holds copies of three already-printed sheets (#47862 48 in, #47874 6 in, #47918 12 in) for dry runs.

## Needs a decision / tidy (DECISIONS.md → Open)

- **Chat (before CMYK/UV go live):** UV sheets currently route to the CMYK line, because "title contains" overlaps. Proposed: match on "title starts with". This doesn't affect the 8-color: Kevin asked about 4-color sheets being skipped, and the check showed 8-color routing is right and 4-color is skipped on purpose (CMYK is off).

- **Chat:** SPEC §3/§4 wording still puts product rules in `machine.json`. §3's layout should also list `ledger.py` and `handcheck.py`.
- **Chat/Kevin:** confirm the Q1 reading (copies split only when they can't all fit under the cap).
- **Chat:** SPEC §7 step 6 should say "read off the preview by OCR" instead of "via UIA" (DECISIONS.md, 11:3x).
- **Open (Code):** closing job tabs after a run (12 piled up). Picture count still can't be read directly; the OCR length vs planned length covers a missing picture of 12 in or more.
- Still open: Q4–Q6, confirming that F3 lands, and the dedicated Shopify app.

## Housekeeping

- `C:\Users\kevin\Downloads\8color-hold-accidental-20261015` holds the 21 files from the incident. Their ledger entries are `held`. Clear the folder whenever you like.
- The new `config/secrets.json` still has default file permissions; locking it is Kevin's call.
