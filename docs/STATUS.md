# Build status (Code → chat handoff)

Claude Code rewrites this file at the end of every working session and pushes it. Chat reads this file first. Decisions and open questions live in `docs/DECISIONS.md`; the spec lives in `docs/SPEC.md`. Git history holds older versions of this file.

**Last updated:** 2026-10-15 ~11:40, OCR length gate built; waiting on Kevin's live check · **HEAD:** see `git log -1`

## Latest (11:40): read this first

- **Length gate settled:** Kevin's screenshot showed the preview draws `22.500 inch` (width) and `65.237 inch` (length, sideways). The driver now screenshots the preview's picture area and reads it with Windows' built-in OCR; on Kevin's screenshot it read exactly 22.500 × 65.237. A post-rip check of the queue row's size is the backstop. `gap_in` is now 1.0 (measured 1.02). Details: DECISIONS.md, 11:3x entry.
- **Not yet seen live:** the capture inside PO-TRY, and whether multi-add loaded all three (the screenshot came from the one-at-a-time run). Both are checked by Kevin's two dry runs below.
- Tests: 124, all passing (including a real Windows OCR test on a drawn stand-in preview).

### Kevin: two dry runs (one administrator Command Prompt is enough)

**Setup:** open an administrator Command Prompt and run `cd C:\Users\kevin\projects\rip-automation`. PO-TRY open and maximized.

**Step 1: one-at-a-time dry run, now with the OCR gate**
1. Run: `python -m ripauto.potry --dry-run --batch --folder C:\Users\kevin\Downloads\8color-dryrun`
2. Hands off. It ends with Cancel and "All done."
3. Look for this line near the end: `preview size: (22.5, 65.237)   planned 65.2 in   gate: ok`

**Step 2: same, adding all three in one Add**
1. Run: `python -m ripauto.potry --dry-run --batch --folder C:\Users\kevin\Downloads\8color-dryrun --try-multi-add`
2. Hands off. It ends with Cancel.
3. If the preview size again reads about 65.2, multi-add loaded all three and the agent can use it.

**Step 3: tell Code "done"**, plus the `start_from_order` number if you have it. Code reads the log and the saved OCR images from disk.

Nothing in these steps prints or rips; both end with Cancel.

---

## Where we are

| SPEC §12 step | State |
|---|---|
| 1–3 | **Done** |
| 4. Driver multi-file runs + length gate + hand-download check + `start_from_order` | **Code done and unit-tested. 3-sheet batch dry run passed (11:05).** OCR length gate built (11:40); live check pending (Kevin, above). No real rip yet. |
| 5–8 | Not started |

Tests: `python -m unittest` from the repo root runs 124 tests, all passing.

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

- 11:05: the probe dry run and the multi-add experiment are done; findings are in DECISIONS.md. `C:\Users\kevin\Downloads\8color-dryrun` holds copies of three already-printed sheets (#47862 48 in, #47874 6 in, #47918 12 in) for dry runs.

**Still needed:** the `start_from_order` number (oldest 8-color order still at 1/0 in the app's Unfulfilled tab).

**After the live OCR check passes, with Kevin's go-ahead** (not yet):
- `python -m ripauto.fetch --download` (with the floor set)
- `python -m ripauto.agent --once --dry-run --force`
- then one real `python -m ripauto.agent --once --force`

## Needs a decision / tidy (DECISIONS.md → Open)

- **Chat:** SPEC §3/§4 wording still puts product rules in `machine.json`. §3's layout should also list `ledger.py` and `handcheck.py`.
- **Chat/Kevin:** confirm the Q1 reading (copies split only when they can't all fit under the cap).
- **Chat:** SPEC §7 step 6 should say "read off the preview by OCR" instead of "via UIA" (DECISIONS.md, 11:3x).
- **Open (Code):** closing job tabs after a run (12 piled up). Picture count still can't be read directly; the OCR length vs planned length covers a missing picture of 12 in or more.
- Still open: Q4–Q6, confirming that F3 lands, and the dedicated Shopify app.

## Housekeeping

- `C:\Users\kevin\Downloads\8color-hold-accidental-20261015` holds the 21 files from the incident. Their ledger entries are `held`. Clear the folder whenever you like.
- The new `config/secrets.json` still has default file permissions; locking it is Kevin's call.
