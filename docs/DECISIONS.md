# Decisions and open questions

Newest first. Claude (chat) keeps this file; Claude Code adds entries when it decides something during the build. Build progress and next steps are in `docs/STATUS.md`.

## 2026-10-15

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
- Confirm the driver's F3 keypress actually lands.
- Replace the borrowed Pi paperwork-printer token with a dedicated Shopify app (read_orders, read_customers, Name access).
- ~~(Code) "Matches two machines → refuse" can't be fully checked~~ settled: `config/machines.json`, built (see Code entry above).
- ~~(Code) Printable width for 8-color?~~ settled: 23 in.
- (Code) **SPEC wording to tidy:** §3's config-split bullet still lists "label, product rules" under `machine.json`, and §4 says "Product rules come from `machine.json`". Both contradict §2 (rules live in `machines.json`). Code follows §2.
- ~~(Code) Backlog cutoff~~ settled 10:31, see above. Was: **Backlog cutoff before go-live (step 6).** The live list shows 26 unhandled 8-color sheets on unfulfilled orders, going back to #43227. Most were presumably printed by hand already. Before the agent runs unattended, we need either `fetch --mark-done` (optionally with `--from`) or a `start_from_order` setting. Also #47914 was hand-downloaded during the verification, but the agent's ledger still shows it as new.

