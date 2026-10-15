# Build status (Code → chat handoff)

Claude Code rewrites this file at the end of every working session and pushes it. Chat reads this file first. Decisions and open questions live in `docs/DECISIONS.md`; the spec lives in `docs/SPEC.md`. Git history holds older versions of this file.

**Last updated:** 2026-10-15 ~10:50, step 4 started then **paused after an incident** · **HEAD:** see `git log -1`

---

## ⚠ Incident: read before anything else

At 10:41–10:43 Code accidentally ran `fetch --download` with no floor and downloaded 20 backlog sheets (#43227 … #47874), plus part of #47887, before killing it. **Nothing was ripped and PO-TRY wasn't touched.** All 21 files are now in `C:\Users\kevin\Downloads\8color-hold-accidental-20261015`, and their ledger entries are `held`. Full list and cause: DECISIONS.md → "Incident".

**Resolved with Kevin:** the range (#43227 from Aug 27 through #47874 from last night) was already downloaded and printed by hand, so the counters were already 1/1 and the floor reading is unaffected. Files stay held. Step 4 resumed.

---

## Where we are

| SPEC §12 step | State |
|---|---|
| 1. Skeleton, config split, port scripts | **Done.** Kevin verified `--inspect-queue` and the dry run on the printer PC (10:18). |
| 2. Naming fix + PNG length + tests | **Done** |
| 3. Planner + tests | **Done**, now on the new Q1 rules |
| 4. Driver multi-file runs + length gate + hand-download check + `start_from_order` | **Go-ahead given; paused after the incident above.** `ledger.py` drafted, not committed |
| 5–8 | Not started |

Tests: `python -m unittest` from the repo root runs 66 tests, all passing.

## This session (pulled chat's 4 commits, then)

- **`config/machines.json`** (committed): lines `8color` / `cmyk` / `uv` with their product rules, and machines `8color-1` (enabled), `cmyk-1`, `cmyk-2`, `uv-1`, `uv-2` (off). Labels follow Kevin's shop map.
- **`ripauto/config.py`:** loads `machines.json` and refuses to start in three cases:
  - rules on two lines could match one title
  - two machines on one line are enabled
  - this PC's machine is unknown or switched off

  It merges this machine's line, label and rules into the config.
- **`machine.json` (local and example):** `machine_id` is now `8color-1`, `max_width_in` is 23, and `label`, `product_rules` and `enabled` were removed. They now live only in `machines.json`; a leftover copy is refused.
- **`ripauto/fetch.py`:** matches each title against every line's rules. A title that matches two rules stops the fetch; otherwise it keeps this line's items.
- **`ripauto/planner.py`:** applies Kevin's Q1 answer.
  - Each copy is its own picture.
  - A sheet is held only when one copy is over 650 in.
  - Copies split across runs only when together they're over the cap (how "when they fit" is read: DECISIONS → Code entry).
  - Runs now list `RunItem(sheet, copies)`.

## Verified

- All 66 tests pass, including a check that the committed `machines.json` loads and that only `8color-1` is enabled.
- Live list-only fetch with the new config: 29 sheets on unfulfilled orders, 28 not yet handled.
- Planner pass over the live queue (lengths from titles/attributes, nothing downloaded): 637.1 / 605.5 / 645.1 in full runs, plus one 52.8 in sheet (#47935) waiting. No holds at 23 in width.

## Waiting on Kevin

1. **Go-ahead for step 4.**
2. **`start_from_order`:** the oldest 8-color order still showing **1/0** in the app's Unfulfilled tab (SPEC §4 go-live floor).

## Needs a decision / tidy (details in DECISIONS.md → Open)

- **Chat:** SPEC §3 and §4 still say product rules live in `machine.json`. Code follows §2 (`machines.json`); please tidy the wording.
- **Chat/Kevin:** check the Q1 reading. Copies split only when they can't all fit under the cap, not merely when the current run is nearly full.
- Still open: Q4 (flush minutes), Q5 (UV titles, CMYK/UV caps), Q6 (five PC host names), confirming that F3 lands, and the dedicated Shopify app.

## Housekeeping notes

- The new `config/secrets.json` still has default file permissions; the old trial `config.json` was locked to user `kevin`. Locking it is Kevin's call, so it's unchanged.
- `C:\Users\kevin\Downloads\8color-dryrun` holds a copy of the #47918 sheet for dry runs. It can stay for step 4's dry runs.

## Step 4 plan (approved by chat; updated with this session's spec changes)

1. **Ledger states** per sheet: `queued` → `in_run` → `ripped` / `held` / `by_hand`. Because copies can now split, also track `copies_ripped`, so a sheet is `ripped` only when all its copies are done. The planner gets `qty − copies_ripped` as `Sheet.qty`. Run records go in `state/runs/{run-id}.json`.
2. **`start_from_order`** in `machine.json`: fetch ignores orders below it, and `--from` still overrides it on the command line.
3. **Hand-download check** (SPEC §4): before planning, and again right before each run starts, check two places:
   - PO-TRY's queue rows (`{order#}-` prefix plus position)
   - the top level of `hand_download_dir`, for an app-named file with the same order# and position

   Outcomes: `by_hand`, free, or held as "manual check". The decision logic is a pure function with a test for each outcome; reading the folder and the queue are thin wrappers around it.
4. **Driver takes a run** (files × copies) instead of one PNG. First test whether the Add dialog accepts several files at once; if not, do one Add per picture. Confirm the loaded picture count; this needs a read-only probe while a multi-picture job is loaded.
5. **Preview length gate:** a `--pause-at-preview` dry-run flag that waits for Enter before pressing Cancel, so `tools/probe_potry.py` can find the `… inch` text. Cancel and hold the whole run if the length is unreadable or over `cap_in`.
6. **Queue statuses** kept in one list in `potry.py`. "RIP finished", "Transmission finished" and any later status count as done; new strings get added as they're seen.
7. **Test runs:** dry-run a 2–3 sheet batch from copies in `8color-dryrun`, then one real run only with Kevin's go-ahead.
