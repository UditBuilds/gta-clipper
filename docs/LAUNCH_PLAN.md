# /launch plan (PR 25, S3), step 0

## Files

- **New** `launch.py`: `prepare` (find recordings, calibrate or fall back, index, mine and rank, safety pass,
  `candidates.json` with evidence frames) and `render` (check Claude's `shorts.json`, render, `upload.txt`,
  `review.html`). Flags: `--dry-run`, `--videos`.
- **New** `truth.py` (the script truth rules, built on `facts.py`), `safety.py` (safety pass, first-7-s gore check,
  used-footage log, true-peak guard), `prompts/safety_v1.txt`, `profiles/gta6_nohud.json` and `gta5_nohud.json`
  (match no HUD on purpose, so the HUD rules are off), `launchtest.py`, `.claude/commands/launch.md`, `LAUNCH_NIGHT.md`.
- **Small changes**: `calibrate_guided.py` gets `auto_accept` (takes every guess, as the tests' stand-in owner does);
  `index.py`'s `ask_model` takes a schema (so safety.py reuses it) and `index_video` a `cache_dir`;
  `footage_short.py` logs pinned footage; `explainer.py` runs the true-peak guard; `shotlist.json`; README.
  **`clip.py` is not changed**: launch runs it as it is and reads its `timeline.csv` (loudness + picture change + stars).

## Flow

1. Recordings in `C:\AI\gta\input` not yet in the library (never `heldout`), in the order of the PS5 file-name time.
2. Profile `gta6` exists: use it. Else guided calibration with `auto_accept` on the longest recording.
3. `index.py` on every recording (default settings) and `clip.py` on each (alongside the model calls).
4. Per recording: HUD mode or NO-HUD mode (below).
5. Mine, rank, safety pass, pick 3, choose each type: `candidates.json`.
6. Claude writes `shorts.json`, then `render`; a refused check means Claude fixes and reruns (3 tries), then the next Short.

## Moment mining

Candidates: clip.py's own picks (15-30 s windows), star rises (`library.star_rises`), chase, shootout, flying and boat
segments of 8 s or more, and notable-line hits (crash, explosion, flip, jump, fire; man and woman, two characters).
Overlapping candidates merge into one moment (35 s at most). Score, with every term saved as evidence:
action = clip.py's best `pick_smoothed` in the window (0-3); star rise +2 and +0.5 per star gained; chase or
shootout +2, flying or boat +1; crash or explosion +1.5; stunt +1.5; police seen +0.5; model confidence x 0.5.
Dropped: anything that fails the safety pass, and anything that overlaps `_used_footage.json`.
The 3 picks: the best moment in each third of the session; an empty third takes the next best moment at least
5 min from the others; a small penalty for a category already picked.

## Type (decided in code, with the reason written down)

ACTION CLIP when one strong peak carries it (a crash, stunt or shootout, or action 2.5 or more) in 15-30 s.
NARRATED when there is something to describe over time: 3 or more different evidence items (for example star rise,
chase, police seen) and 24 s or more of clean footage. Both fit: the higher score wins, and the 3 Shorts include at
least one of each type when both exist. Narrated footage plays in real time behind the voice, as alternating full and
16:9 card beats (card on lines about the stars, so the HUD stays in view).

## How on_screen claims are checked (truth.py)

Each evidence item has an id (E1, E2 ...), a kind (activity, stars, seen, police, event), its times in the recording
and the numbers it supports. Every narrated line lists `"evidence"` and `"facts"`. A line is refused when:
- it cites no evidence, or none of it is on screen while the line is spoken (1.5 s either way, from the voice timing);
- it uses a word whose evidence it doesn't cite: stars or wanted need a HUD star item; police or cops need police
  seen, a chase or stars; crash, chase, shoot, gun, fly, boat each need theirs;
- it says a number that isn't in its evidence or its facts (facts.py's number check);
- it sounds like a claim about how the game works ("you can", "lets you", "the game", "always", "in GTA 6") and lists
  no fact; reported facts only in their safe wording; stale facts refused;
- it has a name other than Jason or Lucia (a capitalised word not in a short allowlist or its facts) or a spoiler word.
Hooks, title and description get the same checks. What code can't judge becomes a FLAG on `review.html`.

## Safety

- Never-use: the index's never_use, plus a safety question on every finalist (4-frame sheets, a frame a second):
  phone on screen, menu or map, loading, cutscene, blood, burned-in subtitles. This catches the phone without phone
  calibration. I will measure it on the dev phone calls the guided profile missed.
- Gore: the first 7 s of the final Short (the cold open included) at 2 frames a second. Gore: start later, or drop.
- Used footage: every shown span, pinned ones too, is logged; /launch never overlaps one. `--dry-run` reads and
  writes nothing there.
- True peak: a true-peak limiter on the final mix, then ffmpeg's `ebur128` true-peak meter. Over -1.0 dBTP: 1 dB
  lower, twice, then FAIL.

## No-HUD fallback

Triggers: guided calibration fails or can't start; or, for one recording, the HUD and the model disagree (no minimap
on over 20 % of the sheets the model calls gameplay), the minimap shows in under 50 % of it, or the star count changes
over 6 times a minute. That recording is indexed again with `<game>_nohud` (model answers are cached, so only the HUD
pass runs). Labels come from the model only, with no stars and no star rises. Mining uses clip.py's loudness and
picture score and the labels. The safety pass takes a frame every 0.5 s. Narration may not mention stars or the wanted
level, and `review.html` says NO-HUD MODE.

## Time for 2 h of footage

Calibration 4 min; index 74 min (12.3 min per 20 min measured in PR 21, 92 % of it the model); clip.py 9 min,
alongside the model; safety pass 4 min; Claude's writing 5 min; 3 renders 4 min. **About 90 min.** From 2:30 AM that
ends about 4:00 AM, before your review and the 4:30 AM slot.

## Decisions for the owner

1. `--every 12` halves the model calls (/launch about 55 min) but is unmeasured. Measure it with `indexeval.py` on
   test1/test2 in the dry run, and use it on launch night only if it still finds all 5 test1 moments?
2. Cold open and zoom need a peak; the Nov 6 rule says only a typed one. For /launch: take it from evidence (a star
   change, or the impact frame Claude picks from a strip of frames 0.2 s apart), never `peak_time_s`, flag it AUTO PEAK,
   and give a one-command re-render without it?
3. /launch picks the starred hook itself (this brief overrides "wait for approval", for /launch only).
4. Put the true-peak guard in `explainer.py` too, so `/new-short` and `/footage-short` get it?
