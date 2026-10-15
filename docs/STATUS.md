# Build status (Code → chat handoff)

Claude Code rewrites this file at the end of every working session and pushes it. Chat reads this file first. Decisions and open questions live in `docs/DECISIONS.md`; the spec lives in `docs/SPEC.md`. Git history holds older versions of this file.

**Last updated:** 2026-10-15 afternoon, step 4 code done; waiting on Kevin's admin-prompt dry runs · **HEAD:** see `git log -1`

---

## Where we are

| SPEC §12 step | State |
|---|---|
| 1–3 | **Done** |
| 4. Driver multi-file runs + length gate + hand-download check + `start_from_order` | **Code done and unit-tested. PO-TRY dry runs not yet done** (needs Kevin at an admin prompt, below). No real multi-sheet rip yet. |
| 5–8 | Not started |

Tests: `python -m unittest` from the repo root runs 121 tests, all passing.

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

## Kevin: steps at an ADMINISTRATOR prompt in `C:\Users\kevin\projects\rip-automation`

`C:\Users\kevin\Downloads\8color-dryrun` holds copies of three already-printed sheets (#47918 12 in, #47874 6 in, #47862 48 in; planned 64.2 in). Every step below presses **Cancel**, never Print. Hands off the mouse while it runs.

1. **Batch dry run, paused at the preview, for the probe:**
   `python -m ripauto.potry --dry-run --batch --folder C:\Users\kevin\Downloads\8color-dryrun --pause-at loaded,preview`
   At each PAUSED prompt, open a second admin prompt in the same folder and run `python tools\probe_potry.py`, then press Enter in the first. (Code reads `tools\potry_controls.txt` from disk afterwards. Running the probe twice overwrites it, so if you can, copy it to `potry_controls_loaded.txt` after the first pause.)
2. **Multi-file Add experiment:**
   `python -m ripauto.potry --dry-run --batch --folder C:\Users\kevin\Downloads\8color-dryrun --try-multi-add`
3. Tell Code when they're done (or paste the end of `state\rip_log.txt`). Code then fixes the preview-size reading and picture count from the probe output.

**Also needed:** the `start_from_order` number (oldest 8-color order still at 1/0 in the app's Unfulfilled tab).

**After that, with Kevin's go-ahead** (not yet):
- `python -m ripauto.fetch --download` (with the floor set)
- `python -m ripauto.agent --once --dry-run --force`
- then one real `python -m ripauto.agent --once --force`

## Needs a decision / tidy (DECISIONS.md → Open)

- **Chat:** SPEC §3/§4 wording still puts product rules in `machine.json`. §3's layout should also list `ledger.py` and `handcheck.py`.
- **Chat/Kevin:** confirm the Q1 reading (copies split only when they can't all fit under the cap).
- **Open (Code):** closing job tabs after a run; whether multi-file Add works; how to count pictures in PO-TRY.
- Still open: Q4–Q6, confirming that F3 lands, and the dedicated Shopify app.

## Housekeeping

- `C:\Users\kevin\Downloads\8color-hold-accidental-20261015` holds the 21 files from the incident. Their ledger entries are `held`. Clear the folder whenever you like.
- The new `config/secrets.json` still has default file permissions; locking it is Kevin's call.
