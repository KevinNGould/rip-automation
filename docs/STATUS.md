# Build status (Code → chat handoff)

Claude Code rewrites this file at the end of every working session and pushes it. Chat reads this file first. Decisions and open questions live in `docs/DECISIONS.md`; the spec lives in `docs/SPEC.md`. Git history holds older versions of this file.

**Last updated:** 2026-10-15, after the 11:36 first real rip · **SPEC §12 step 4 done; Code stops before step 5 and checks in** · **HEAD:** see `git log -1`

(Times are from the 8-color PC's logs. Earlier versions of this file and DECISIONS.md carried some guessed "~12:xx" times; those are corrected.)

---

## Where we are

| SPEC §12 step | State |
|---|---|
| 1–3 | **Done** |
| 4. Driver multi-file runs + length gate + hand-download check + `start_from_order` | **Done.** First real agent rip at 11:36 (below). |
| 5. Heartbeat + Pi dashboard | Not started. **Waiting for Kevin/chat to say go.** |
| 6–8 | Not started |

Tests: `python -m unittest` from the repo root runs 131 tests, all passing.

## First real run (11:36)

`python -m ripauto.agent --once --force` (Kevin, admin prompt), run `8color-1-20261015-113554`:

| | |
|---|---|
| Sheets | #47946 (22×120, 121.2 in measured) + #47947 (22×12, 13.2 in, low-res) |
| Load | one multi-file Add, then F3 |
| Planned | 135.4 in (2 pictures, 1.0 in gap) |
| Preview (OCR) | 22.5 × 135.4, gate ok |
| Rip | Print in the preview → Ripping 41…87 → RIP finished 100, about 35 s |
| Queue size | 22.500×135.400; measured gap 1.0 in = `gap_in` |
| After | both `ripped` in the ledger; files in `ripped\8color-1-20261015-113554\`; run record in `state/runs/` |

Nothing was printed: Kevin prints from PO-TRY's RIP queue as usual.

## How we got here today (short)

- **10:18:** Kevin verified the port (`--inspect-queue`, one-sheet dry run).
- **10:41:** incident: backlog downloaded by mistake, contained. Already printed by hand, so no harm (DECISIONS.md).
- **11:05:** probe dry run. The preview's size isn't readable by UI automation, and the canvas doesn't list its pictures.
- **~11:1x:** Kevin's screenshot showed the size drawn on the preview, so the length gate became **OCR of the preview** (Windows' built-in OCR), with a post-rip queue-size backstop. `gap_in` was set to 1.0.
- **11:16–11:17:**
  - 3-sheet dry runs: OCR 22.5 × 65.237, gate ok.
  - Multi-file Add proven.
  - F3 confirmed.
- **Before 11:29:**
  - Floor `start_from_order` = **47943** (Kevin had handled everything through #47942).
  - Hand check verified on Kevin's real hand downloads (#47932/#47935/#47940/#47942 → by_hand).
  - #47946 and #47947 arrived and were downloaded on Kevin's go-ahead.
- **11:29:** first agent dry run. OCR misread (`*2.500`, `135400`) and the gate cancelled correctly. OCR made sturdier: label strips only, pink painted out, the lost decimal restored, a width check.
- **11:34:** agent dry run passed. **11:36:** first real rip passed.

## What exists (ripauto/)

- `config.py`: `machines.json` (committed: lines, product rules, machines, enabled) + `machine.json` (local: id, folders, limits, `start_from_order` 47943, `gap_in` 1.0, `multi_add` on) + `secrets.json`.
- `fetch.py`: Shopify → queue folder. `--download` refuses without a floor; new sheets enter the ledger as `queued`.
- `sheets.py`: app file naming, position, PNG size from DPI.
- `planner.py`: batching (SPEC §6, with Kevin's Q1 rule).
- `ledger.py`: `queued → in_run → ripped | held | by_hand`, plus `copies_ripped`.
- `handcheck.py`: SPEC §4 check against PO-TRY's queue and Downloads.
- `potry.py`: the driver. Multi-file runs, OCR length gate (±max(3 in, 1%) of planned, never over the cap), queue statuses, run matching by any picture name.
- `ocr.py` + `tools/ocr_image.ps1`: reads the preview's width/length labels.
- `agent.py`: one cycle (`--once`, `--dry-run`, `--force`, `--plan`). The loop is step 6.

## Needs a decision / tidy (DECISIONS.md → Open)

- **Kevin/chat: go for step 5** (heartbeat + Pi dashboard)?
- **Chat (before CMYK/UV go live):** UV sheets currently route to the CMYK line ("title contains" overlaps). Proposed: match on "title starts with".
- **Chat:** spec wording to tidy:
  - §3/§4 still put product rules in `machine.json`.
  - §3's layout should list `ledger.py`, `handcheck.py` and `ocr.py`.
  - §7 step 6 should say "read off the preview by OCR", not "via UIA".
- **Chat/Kevin:** confirm the Q1 reading (copies split only when they can't all fit under the cap).
- **Open (Code):** closing PO-TRY job tabs after a run (a dozen have piled up from today's tests).
- Still open: Q4 (flush minutes), Q5 (UV/CMYK details), Q6 (PC host names), the dedicated Shopify app.

## Housekeeping

- `C:\Users\kevin\Downloads\8color-hold-accidental-20261015` holds the 21 files from the incident. Their ledger entries are `held`. Clear the folder whenever you like.
- `C:\Users\kevin\Downloads\8color-dryrun` holds copies of three already-printed sheets for dry runs.
- `config/secrets.json` still has default file permissions; locking it is Kevin's call.
