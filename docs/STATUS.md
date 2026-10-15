# Build status (Code → chat handoff)

Claude Code rewrites this file at the end of every working session and pushes it. Chat reads this file first. Decisions and open questions live in `docs/DECISIONS.md`; the spec lives in `docs/SPEC.md`. Git history holds older versions of this file.

**Last updated:** 2026-10-15, after build steps 1–3 · **HEAD:** see `git log -1`

---

## Where we are

| SPEC §12 step | State |
|---|---|
| 1. Skeleton, config split, port scripts | **Done.** Fetch re-checked live. PO-TRY checks still to run (below). |
| 2. Naming fix + PNG length + tests | **Done** |
| 3. Planner + tests | **Done** |
| 4. Driver: multi-file runs + preview length gate | **Not started. Waiting for Kevin's go-ahead** (instructed stop point) |
| 5–8 | Not started |

Tests: `python -m unittest` from the repo root runs 49 tests, all passing.

## What exists now

- `ripauto/config.py`: loads `config/machine.json` (no secrets) and `config/secrets.json` (Shopify credential only). Both are git-ignored and exist on the 8-color PC.
- `ripauto/fetch.py`: port of `trial/fetch_8color.py`. Product rules come from `machine.json`. File names follow `{order#}-{customer}-{order ID}-{size}-{position}-({qty})`. The ledger is `state/processed.json`, and each downloaded sheet's record holds position, qty, measured length/width and where it came from, the low-res flag, the file and `fetched_at`.
- `ripauto/sheets.py`: naming, gang-sheet position, PNG size from `pHYs`, with fallbacks to `_Actual Height` and then the variant title.
- `ripauto/planner.py`: `plan(sheets, now, limits) -> (runs, holds)`, a pure function covering SPEC §6.
- `ripauto/potry.py`: port of `trial/rip_driver.py`. Behavior is unchanged: one sheet per job, and no length gate yet.

## Verified

- Ported fetch, list-only, against live Shopify: output is identical to the trial script (27 sheets, #47918 done).
- Positions match the app's file names: #47862 → `…48in 4ft-1-…` and `…600in 50ft-2-…`.
- Real #47918 PNG: 6630 × 3077 px at 11811 px/m = **22.100 × 10.257 in**, the same as PO-TRY's W/H readout.
- `_Actual Height` comes in inches (`"44.58 in"`) but leaves out the label strip: #47918 says 9.06 in against 10.257 in measured.
- A planner dry pass over today's live queue (lengths from titles/attributes, no downloads) made 3 runs (637.1 / 593.0 / 645.1 in), all full, with no holds.
- Token: it now exists only in `config/secrets.json`. The old `C:\Users\kevin\rip-auto\config.json` has a blank token plus a pointer note, so the trial scripts no longer run.

## Not verified yet (Kevin, admin Command Prompt, in the repo folder)

1. `python -m ripauto.potry --inspect-queue`: it should list the RIP queue rows, including #47918 as `RIP finished`.
2. `python -m ripauto.potry --dry-run --limit 1 --folder C:\Users\kevin\Downloads\8color-dryrun`: this folder holds a **copy** of the #47918 sheet, so the real queue folder isn't touched. You should see New → Add → F3 → RIP → preview → **Cancel**, and the log ends "Dry run complete". The log is in `state/rip_log.txt`.

Report back what each printed (or paste `state/rip_log.txt`).

## Needs a decision (details in DECISIONS.md → Open, items marked "(Code)")

- **Backlog cutoff before go-live.** The agent sees 26 unhandled 8-color sheets back to #43227, including #47914, which was hand-downloaded. Choose between `fetch --mark-done [--from N]` and a `start_from_order` setting. This must be settled before step 6.
- **Printable width** for 8-color (`max_width_in`, currently unchecked).
- **Cross-machine product overlap check:** proposal for a committed `config/machines.json`.
- Still open from before: Q1–Q6, and confirming that F3 lands.

## Housekeeping notes

- The old trial `config.json` was ACL-restricted to user `kevin`. The new `config/secrets.json` has default permissions; restricting it the same way is suggested. Kevin's call, so Code hasn't changed any permissions.
- Commits are made as `KevinNGould` (repo-local git config) with a Claude co-author line.

## Proposed next step for Code: step 4 (for chat/Kevin to review before it starts)

1. Ledger states per sheet: `queued` → `in_run` → `ripped` / `held`, with a run record in `state/runs/{run-id}.json`.
2. Driver takes a run (list of files × qty) instead of one PNG. First, test whether the Add dialog accepts several files at once; if it doesn't, use one Add per picture.
3. Confirm the loaded picture count. How to count pictures in the layout is unknown, so a read-only probe is needed while a multi-picture job is loaded.
4. Preview length gate: find the `… inch` texts in the Print preview through UIA. This needs a read-only probe while the preview is open, so add a `--pause-at-preview` flag to the dry run that waits for Enter before pressing Cancel.
5. Dry-run a 2–3 sheet batch from copies, then one real run only with Kevin's go-ahead.
