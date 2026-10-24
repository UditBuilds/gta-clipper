test1 and test2 are the **dev set (rules were tuned on these)**: every eval result in this file (precision, recall,
bad picks, postable) is on them. A held-out video exists and hasn't been used.

Each section below was scored with the eval.py of its day. PR 5 (last section) changed how ranges and ratings are
counted; its table rescores the latest v3 and v4 runs under the new rules. The older tables are left as they were.

# v1 baseline (2026-10-22)

Fresh runs of `clip.py` v1 (main at df572bd) with default flags (5 clips, 20-30 s, 10 s gap), scored by
`eval.py` against the confirmed answer keys in `answer_keys/`. Machine: HP Omen 15, i7-14650HX, RTX 5060
Laptop GPU, Windows 11, ffmpeg 9.0 (Gyan full build), Python 3.14.7. The encoder chosen was h264_nvenc. Nothing
else heavy was running.

| Video | Clips | Precision | Recall | Bad picks | Run time |
|---|---|---|---|---|---|
| test1.webm (15:24.7) | 5 | 4/5 (80 %) | 3/5 (60 %) | 2 | 213.7 s (picture analysis 184.9 s) |
| test2.webm (20:49.7) | 5 | 3/5 (60 %) | 3/3 (100 %) | 2 | 278.0 s (picture analysis 250.2 s) |

Run folders: `C:\AI\gta\output\test1\20261022-113852` and `C:\AI\gta\output\test2\20261022-114332`.
The picks are identical to the v1 runs of 2026-10-21. The scoring has no randomness.

## Clip by clip

test1:

| # | Clip | Action ranges touched | Never-pick ranges touched |
|---|---|---|---|
| 1 | 12:00.5-12:20.5 | yacht chase (20.0 s) | - |
| 2 | 10:18.4-10:38.4 | yacht chase (20.0 s) | - |
| 3 | 8:22.1-8:42.1 | - | phone call 8:12-8:42 (19.9 s) |
| 4 | 2:48.7-3:08.7 | shootout / wanted 2:10-2:57 (8.3 s) | pause map 2:57.5-3:02.0 (4.5 s) |
| 5 | 3:18.7-3:38.7 | driving with wanted stars 3:10-4:10 (20.0 s) | - |

Missed: 0:50-1:10 gun out + carjack, and 1:20-2:00 driving with wanted stars.

test2:

| # | Clip | Action ranges touched | Never-pick ranges touched |
|---|---|---|---|
| 1 | 0:00.0-0:20.0 | - (tennis-coach tail, neutral) | - |
| 2 | 4:36.7-4:56.7 | gang chase (20.0 s) | - |
| 3 | 0:39.8-0:59.8 | - (tennis-coach tail, neutral) | - |
| 4 | 2:36.8-2:56.8 | house pulled down (1.8 s) | cutscene 1:44-2:44 (7.2 s) |
| 5 | 18:57.7-19:17.7 | crash + Coquette (8.7 s) | phone call 18:48-19:09 (11.3 s) |

## Notes

- **test1 clip 4:** its highest-scoring step is at 2:57.5, the moment the pause map opens. The clip is centred on
  the menu transition, not on the action.
- **test1 clip 3:** this is the phone call. Its peak (8:27.5, score 4.03) is the highest single step v1 found in test1.
- **test2 recall is generous.** The metric counts any shared time, as defined. Clip 4 is mostly cutscene and only
  grazes the house-pulling range by 1.8 s. Clip 5 is more phone call (11.3 s) than action (8.7 s). If a clip had
  to share at least 5 s with an action range to count, test2 would score precision 2/5 and recall 2/3. This is
  commentary only: the headline numbers above use the brief's definition.
- **Bad picks by kind:** pause map 1, phone call 2, cutscene 1. Pause maps and cutscenes hide the minimap.
  Phone calls do not.

# v2 (HUD-aware scoring) compared with the baseline (2026-10-22)

These are the same machine, default flags and CPU decoding, run one at a time. Scored against the answer keys at
revision 1 (five edges fixed from 0.5 s frames; see each key's `revisions` block). Run folders:
`test1\20261022-120429` and `test2\20261022-120930`.

| Video | Version | Precision | Recall | Bad picks | Run time |
|---|---|---|---|---|---|
| test1 | v1 | 4/5 | 3/5 | 2 (phone call 8:22, pause map 3:00) | 213.7 s |
| test1 | v2 | 5/5 | 4/5 | 0 | 229.7 s |
| test2 | v1 | 3/5 | 3/3 | 2 (cutscene 2:36, phone call 18:57) | 278.0 s |
| test2 | v2 | 3/5 | 2/3 | 1 (phone call 18:57) | 290.3 s |

Revision 1 changed one number: test1 v2's bad picks went from 1 to 0. Every v1 number, including the baseline
above, is unchanged.

v2 picks:
- **test1:** 3:03.5-3:33.5, 1:18.0-1:48.0, 3:48.5-4:18.5, 2:26.0-2:46.0 and 0:48.0-1:08.0. All five are in the
  wanted-star stretch. The yacht chase (no stars) is missed.
- **test2:** 0:00-0:20, 4:36.8-4:56.8, 0:39.7-0:59.7, 18:57.6-19:17.6 and 5:06.8-5:26.8. test2 has no wanted
  stars, so the only change from v1 is that the cutscene pick at 2:36 became a second gang-chase clip.

Notes:
- **Against the keys as first confirmed, test1 v2 had 1 bad pick.** Clip 3 (3:48.5-4:18.5) overlapped the
  safehouse-intro range, then 4:15-4:38, by 3.5 s. That start had come from the 10 s sheet. Frames at 0.5 s showed
  driving with the minimap and two stars until 4:19.0, so revision 1 moved the start to 4:19.0. Clip 3 ends 0.5 s
  before the exclusion v2 itself starts there.
- **v2's detector found a second pause map** at test1 2:46.5-2:48.5. The first key missed it; revision 1 added it.
  No run picks it.
- **test2's recall drops** because the house collapse (2:55-3:35) is partly a scripted camera with the HUD hidden,
  so v2 excludes 3:19.5-3:35.5. v1 only "hit" it with a cutscene clip that grazed it by 1.8 s.
- **v2 has no phone-call signal.** The minimap stays up during calls and no stars show. test1's 8:22 call
  dropped out only because boosted wanted stretches outranked it. test2's 18:57 call is still picked.
- **Run time** rose 16.0 s on test1 and 12.3 s on test2. Picture analysis (now including the HUD crops) took
  +8.5 s and +13.6 s. The HUD pixel work in Python took 4.3 s and 5.8 s, overlapped with decoding. On test1, clips
  got longer (three 30 s clips), adding 7.8 s of encoding.
- **v1's signals are untouched.** The loudness, picture-change, hard-cut and z-score columns of `timeline.csv`
  match the v1 runs in every row.

# v3 (strict eval, owner ratings, spread, phone, GPU decoding) compared (2026-10-22)

Same machine and default flags, run one at a time. Each video was run twice: with GPU decoding (the default) and
with `--cpu-decode`. Both runs of a video pick the same clips, and their `timeline.csv` files are byte-identical.
Run folders: `test1\20261022-130115` (GPU), `test1\20261022-130517` (CPU), `test2\20261022-130700` (GPU),
`test2\20261022-131206` (CPU). Scored against the answer keys at revision 1 and the owner's ratings in
`ratings/`:
- the v2 clips, rated 2026-10-22;
- the two v3 clips no earlier rating covered, rated the same day after these runs.

The v2 runs the owner rated are `test1\20261022-120429` and `test2\20261022-120930`. They match the v2 picks above
(test1 clip 3 is 3:48.5-4:18.5). The brief for this PR named `113852` and `114332`, but those are the v1 runs.

| Video | Version | Overlap precision | Strict precision | Recall | Bad picks | Postable | Run time (picture analysis) |
|---|---|---|---|---|---|---|---|
| test1 | v1 | 4/5 | 3/5 | 3/5 | 2 | 1/5 (4 unrated) | 213.73 s (184.86 s) |
| test1 | v2 | 5/5 | 5/5 | 4/5 | 0 | 3/5 (0 unrated) | 229.75 s (193.35 s) |
| test1 | v3, GPU | 5/5 | 5/5 | 4/5 | 0 | 4/5 (0 unrated) | **81.24 s** (44.29 s) |
| test1 | v3, CPU | 5/5 | 5/5 | 4/5 | 0 | 4/5 (0 unrated) | 232.20 s (196.74 s) |
| test2 | v1 | 3/5 | 1/5 | 3/3 | 2 | 2/5 (1 unrated) | 278.00 s (250.17 s) |
| test2 | v2 | 3/5 | 2/5 | 2/3 | 1 | 3/5 (0 unrated) | 290.35 s (263.74 s) |
| test2 | v3, GPU | 3/5 | 3/5 | 2/3 | 0 | 3/5 (0 unrated) | **87.34 s** (58.86 s) |
| test2 | v3, CPU | 3/5 | 3/5 | 2/3 | 0 | 3/5 (0 unrated) | 297.20 s (269.09 s) |

- **Overlap precision** is the old precision; **strict precision** needs half the clip inside action ranges.
- **Postable** comes from the owner's ratings. A clip reuses a rating only if the two time ranges share at least 80 % of
  the longer one.
- v1's test1 clip 12:00.5-12:20.5 shares 19.7 s with the rated v3 clip 12:00.2-12:20.2, so it counts as
  postable. Its other four clips are unrated.

## v3 picks

- **test1:** 3:03.5-3:33.5, 1:18.0-1:48.0, 3:48.5-4:18.5, 2:26.0-2:46.0 and **12:00.2-12:20.2**.
  - Clips 1-4 are v2's clips 1-4.
  - v2's fifth pick, 0:48.0-1:08.0 (rated not postable: "boring, bad cut"), was the third from the same action
    sequence. The cap of 2 skipped it, and the next-best peak was in the yacht chase. The owner rated that clip
    postable: "great chase".
  - The 0:50-1:10 carjack range is now missed, so recall stays 4/5.
- **test2:** 0:00.0-0:20.0, 4:37.0-4:57.0, 0:39.7-0:59.7, 5:07.0-5:27.0 and **2:59.5-3:19.5**.
  - The phone rule removed v2's 18:57.6-19:17.6. Its place went to the house pull, 2:59.5-3:19.5, which the
    owner rated postable: "good crazy clip".
  - The two gang-chase clips moved 0.2 s later and keep their v2 ratings (19.8 s of 20 shared).
  - Spread changed no test2 pick. The two "clumsy crashes" clips are in different sequences, 34.5 s apart.

The item-3 success test passes:
- test1's yacht chase gets a clip.
- No bad pick returns: not the 2:46.5 or 3:00 pause maps, and not the 8:12-8:42 phone call.

Bad picks are 0 in both videos.

## Notes

- **Strict precision can't see a bad cut.** test1 v2 scores 5/5 strict, but the owner would post only 3. The two
  rejects sit fully inside action ranges, so only the owner's ratings catch them.
- **Spread, as defined, still lets one stretch hold four picks.**
  - Settings: a sequence ends at an excluded stretch or after 20 s with no step scoring 2.0 or more, and at most
    2 clips come from each sequence.
  - test1 has 11 sequences. Its pause maps at 2:46.5 and 3:00 split the police stretch into three: 0:33.5-2:46.0,
    2:49.0-2:57.0 and 3:03.0-4:19.0. That stretch still gives 4 of the 5 picks (2 + 0 + 2). The middle sequence
    is 8 s, too short for a clip.
  - The yacht chase falls into three sequences, at 20 s+ lulls and the 11:10-11:15 HUD hide: 9:49.0-10:34.5,
    11:01.5-11:02.0 and 11:43.5-12:15.0.
  - test2 has 14 sequences.
  - The settings were chosen before any v3 run and not changed afterwards.
- **The phone check found exactly the phone calls in the keys.** It was run on every 0.5 s of both videos (4,348
  samples).
  - test1: 8:12.5-8:40.5 (key 8:12-8:42).
  - test2: 4:02.5-4:32.5, 11:59.0-12:25.0 and 18:48.5-19:09.5 (keys 4:02-4:34, 11:57.5-12:26, 18:48-19:09).
  - It detected nothing outside those ranges, even with much looser limits.
  - It misses test2 10:53-11:02. There a scripted call carries on into gameplay, Michael holds the phone to his
    ear, and nothing shows on screen.
- **The exclusion edges are up to 1 s inside the keys' call ranges.** The exclusions are 8:12.0-8:41.0,
  4:02.0-4:33.0, 11:58.5-12:25.5 and 18:48.0-19:10.0. At the uncovered seconds the phone is already off screen
  (frames checked at test1 8:41-8:43 and test2 12:26-12:27, 18:47.5-18:48), but the call's dialogue goes on.
- **The phone rule removed a crash the owner liked.** v2's 18:57.6-19:17.6 (rated postable) peaked at 19:07.5
  (score 3.52). Frames 19:06.5-19:11.5 show the car swerving past a truck, mounting the kerb and hitting the lamp
  post, with the call screen up until 19:09.0. The crash itself is inside the phone exclusion. After 19:10 no step
  reaches 2.0 (the highest is 1.88 at 19:17.5), so the 19:09-19:55 action range gets no clip. The key says the call
  "ends right before the 19:10 crash"; the frames show the crash starting during it. The key is unchanged.
- **GPU decoding gives the same numbers.**
  - Picture analysis is 4.4x and 4.5x faster than v2 (193.35 -> 44.29 s and 263.74 -> 58.86 s).
  - Whole runs are 2.8x and 3.3x faster. Clip encoding (35.6 s and 26.7 s) is now 44 % and 31 % of the run.
  - `timeline.csv` is byte-identical on GPU and CPU for both videos: every row and all 15 columns.
- **v3 leaves v2's signals alone.**
  - Unchanged in every row of both videos, compared with the v2 runs: loudness, audio_z, visual_change,
    hard_changes, visual_z, star_score, stars and minimap.
  - combined, smoothed and excluded differ only at steps within 0.5 s of a phone step.

# v4 (clips cut to the action edges, first with an 8 s minimum) compared with v3 (2026-10-23)

Same machine, default flags, GPU decoding, run one at a time. v3 was run again from a separate checkout of main
(dd64c62) so both versions ran on the same day. Scored against the answer keys at **revision 2** (phone-call ranges
follow the phone on screen; test2's crash range starts at 19:07.5). Revision 2 was committed before any v4 run.
Run folders: v3 `test1\20261023-202444`, `test2\20261023-202755`; v4 `test1\20261023-203050`,
`test2\20261023-203232`.

| Video | Version | Hits (strict) | Overlap | Recall | Bad picks | Average length | Lengths (s) | Postable | Run time |
|---|---|---|---|---|---|---|---|---|---|
| test1 | v3 | 5/5 | 5/5 | 4/5 | 0 | 26.0 s | 30, 30, 30, 20, 20 | 4/5 (0 unrated) | 87.9 s |
| test1 | v4 | 5/5 | 5/5 | **5/5** | **1** (0.0 s shared) | 25.4 s | 30, 30, 28.75, 30, 8 | 1/5 (4 unrated) | 84.4 s |
| test2 | v3 | 3/5 | 3/5 | 2/3 | 0 | 20.0 s | 20, 20, 20, 20, 20 | 3/5 (0 unrated) | 94.7 s |
| test2 | v4 | 3/5 | 3/5 | 2/3 | 0 | **9.7 s** | 8, 16.5, 8, 8, 8 | 1/5 (4 unrated) | 79.4 s |

- **Hits** are strict hits: at least 50 % of the clip inside action ranges. Average length is the mean of
  `duration_s` in `scores.json`.
- **The key revision changes no v3 number.** The 2026-10-22 v3 runs score the same on revisions 1 and 2.
- **The fresh v3 runs reproduce v3.** They pick the same clips as on 2026-10-22, and their `timeline.csv` files are
  byte-identical to those runs.
- **Postable** is mostly "unrated": a rating carries over only with 80 % of the longer clip shared. The two rated
  v4 clips reuse v2 ratings: test1 3:50.2-4:19.0 (28.2 s shared with 3:48.5-4:18.5) and test2 4:40.0-4:56.5
  (16.5 s shared with 4:36.8-4:56.8).

## v4 picks

| Video | # | Clip | Action found | Peak (score) | What decides the edges |
|---|---|---|---|---|---|
| test1 | 1 | 3:10.2-3:40.2 | 3:03.0-4:19.0 (76 s) | 3:24.5 (5.68) | 30 s centred on the peak (wanted stars throughout) |
| test1 | 2 | 0:57.5-1:27.5 | 0:58.5-1:52.5 (54 s) | 1:08.5 (5.66) | start 1 s before the action; end by the 30 s cap |
| test1 | 3 | 3:50.2-4:19.0 | 3:50.5-4:19.0 | 4:01.5 (6.00) | start: 10 s gap after clip 1; end: minimap wall (safehouse intro) |
| test1 | 4 | 2:16.0-2:46.0 | 2:07.5-2:46.0 (38.5 s) | 2:35.0 (4.82) | end: pause-map wall at 2:46.0; 30 s back from it |
| test1 | 5 | 12:05.1-12:13.1 | 12:07.5-12:09.5 (2 s) | 12:07.5 (3.29) | 8 s minimum around a 2 s burst |
| test2 | 1 | 0:02.9-0:10.9 | 0:04.5-0:08.5 (4 s) | 0:07.5 (3.31) | 8 s minimum |
| test2 | 2 | 4:40.0-4:56.5 | 4:41.0-4:55.0 (14 s) | 4:49.5 (3.61) | 1 s before, 1.5 s after the action |
| test2 | 3 | 0:45.1-0:53.1 | 0:46.5-0:51.0 (4.5 s) | 0:47.5 (4.07) | 8 s minimum |
| test2 | 4 | 5:06.5-5:14.5 | 5:07.0-5:08.5 (1.5 s) | 5:07.5 (3.70) | 8 s minimum |
| test2 | 5 | 3:11.5-3:19.5 | 3:17.5-3:18.5 (1 s) | 3:18.0 (3.55) | end: minimap wall (house collapse); 8 s back from it |

- **v4 picks the same five moments as v3.** In both videos all five smoothed peaks, their order and their
  sequences are identical; only the cuts differ.
- **Why test1's recall rises to 5/5.**
  - For the peak at 1:08.8, v3 kept the densest 30 s, 1:18.0-1:48.0.
  - v4 centres 30 s on the peak, and the window may not start more than 1 s before the action (0:58.5). That
    gives 0:57.5-1:27.5.
  - This clip reaches 12.5 s into the 0:50-1:10 carjack range.
- **test1's bad pick is eval's closed-interval rule, not a cut into the cutscene.** Clip 3 ends at 4:19.0, where
  the minimap wall starts (the safehouse intro, 4:19.5, minus the 0.5 s growth) and where the key's cutscene range
  starts too (revision 1 put that range's start on the last gameplay frame, 4:19.0). Shared time: 0.0 s.
  eval.py was not changed.
- **Stills of the v4 clips** (1 s apart; test2 clip 2 at 1.5 s):
  - test2 1 (0:02.9-0:10.9): pulling out of the drive, sideswiping a yellow sports car about 0:07-0:10.
  - test2 3 (0:45.1-0:53.1): an RV pulls out, the truck hits a gate about 0:48, then drives on.
  - These are the same "clumsy crashes" as v3's 0:00-0:20 and 0:39.7-0:59.7, now cut tighter.
  - test2 4 (5:06.5-5:14.5): a green slow-motion flash at 5:07.5, then driving.
  - test2 5 (3:11.5-3:19.5): the truck winching the house, ending as it tips.
  - test1 5 (12:05.1-12:13.1): driving behind the swinging boom on the bridge, with a camera cut to Franklin on
    the boom at 12:08.

## The lamp-post crash (test2 19:07.5-19:09.5)

It did not come back, and no rule was changed to win it back:
- **The crash was a candidate.** Its smoothed peak (19:07.5, 1.84) ranks above v4's fifth pick (1.69), so the
  greedy picker reached it.
- **The action found is short.** It is 19:07.0-19:08.0: only 19:07.0 (2.41) and 19:07.5 (3.52) are active. The
  lamp-post step at 19:09.5 scores 1.43.
- **The window was 19:04.2-19:12.2** (8 s). The phone stretch runs 18:48.0-19:10.0: the call screen is up until
  19:09.5, plus the 0.5 s growth.
- **Cutting the phone out leaves 19:10.0-19:12.2:** 2.2 s, with a best step of 0.92. That fails both rules:
  shorter than 8 s, and no step scoring 2.0 or more.
- **Nothing after it qualifies either.** With `--count 20`, the nearest pick is 19:22.0-19:30.0, the car switch.

## The phone at frame level

- **The rule changed no pick on these videos.** No v4 clip has any phone cut out: test1's call peak (8:33.0,
  smoothed 1.97) is 345th of 1,851 steps by smoothed score.
- **Even 20 clips don't change that.** With `--count 20`, still no clip in either video has phone time cut out.

## v3 columns unchanged

All 15 v3 columns of `timeline.csv` match the fresh v3 runs in every row of both videos (1,851 and 2,501 rows),
except `clip`, which marks the picked clips. v4 appends `pick_score`, `pick_smoothed`, `phone_cut` and `action`.

## Changed after looking at test1/test2

- **No number was changed.** The lead-in (1 s), tail (1.5 s), gap (2.5 s), 8 s minimum and 30 s maximum were
  set before any v4 run.
- **One order was fixed after the first v4 run of test1.** The 30 s cap was applied before the window was held
  inside the walls, so test1's 2:17.5-2:46.0 came out 28.5 s. Now walls first, then the cap: 2:16.0-2:46.0,
  30 s. Only that clip changed. The table above is from the fixed code.

# v4 with a 15 s minimum (2026-10-23)

## Owner ratings of the 8 s test2 clips

Rated with the review page's play-clip button against `test2\20261023-203232` (`ratings/test2_v4.json`):

| # | Clip | Owner |
|---|---|---|
| 1 | 0:02.9-0:10.9 | not postable: "boring" |
| 2 | 4:40.0-4:56.5 | postable: "good action sequence" |
| 3 | 0:45.1-0:53.1 | not postable: "clumsy crash, same problem as clip 1" |
| 4 | 5:06.5-5:14.5 | "kinda postable - too short" (stored as postable) |
| 5 | 3:11.5-3:19.5 | postable: "most enjoyable - but too short" |

With these ratings the 8 s run scores postable 3/5 (0 unrated). For clip 2, eval shows the v2 rating of
4:36.8-4:56.8 instead: both ratings share all 16.5 s, and eval.py keeps the first file on a tie. Both say postable.

## The change

The owner set the shortest clip to **15 s** (from 8 s): "too short" was the owner's judgement of a postable length,
not a score. It applies to the extension of short actions and to the piece left after the phone cut. A clip that
can't reach 15 s is dropped and the next candidate taken. No other rule or number changed.

## Results

Same machine, default flags, GPU, run one at a time, keys at revision 2. v3 was run again from a separate checkout of
main. Runs: v3 `test1\20261023-205249`, `test2\20261023-205553`; v4 (15 s) `test1\20261023-205413`,
`test2\20261023-205723`.

| Video | Version | Hits (strict) | Recall | Bad picks | Average length | Lengths (s) | Postable | Run time |
|---|---|---|---|---|---|---|---|---|
| test1 | v3 | 5/5 | 4/5 | 0 | 26.0 s | 30, 30, 30, 20, 20 | 4/5 (0 unrated) | 84.2 s |
| test1 | v4, 8 s | 5/5 | 5/5 | 1 | 25.4 s | 30, 30, 28.75, 30, 8 | 1/5 (4 unrated) | 84.4 s |
| test1 | v4, 15 s | 5/5 | 5/5 | 1 | 26.8 s | 30, 30, 28.75, 30, 15 | 1/5 (4 unrated) | 83.7 s |
| test2 | v3 | 3/5 | 2/3 | 0 | 20.0 s | 20, 20, 20, 20, 20 | 3/5 (0 unrated) | 90.1 s |
| test2 | v4, 8 s | 3/5 | 2/3 | 0 | 9.7 s | 8, 16.5, 8, 8, 8 | 3/5 (0 unrated) | 79.4 s |
| test2 | v4, 15 s | 3/5 | 2/3 | 0 | 15.3 s | 15, 16.5, 15, 15, 15 | 1/5 (4 unrated) | 83.9 s |

- **No new picks.** At 15 s, v4 picks the same five moments as at 8 s in both videos (and as v3). The clips
  that were shorter than 15 s are longer now; the others are unchanged:
  - test1 5: 12:05.1-12:13.1 → 12:02.3-12:17.3.
  - test2 1: 0:02.9-0:10.9 → 0:00.1-0:15.1.
  - test2 3: 0:45.1-0:53.1 → 0:42.3-0:57.3.
  - test2 4: 5:06.5-5:14.5 → 5:06.5-5:21.5.
  - test2 5: 3:11.5-3:19.5 → 3:04.5-3:19.5. It still ends at the house-collapse wall, so all 7 s were added
    before the action.
  - Unchanged: test1 1-4 and test2 2 (4:40.0-4:56.5).
- **The bad pick is the same.** test1's 3:50.2-4:19.0 touches the safehouse cutscene range at 4:19.0 with 0.0 s
  shared.
- **The fresh v3 runs reproduce v3.** Their `timeline.csv` is byte-identical to the 2026-10-22 v3 runs, and v4's
  15 v3 columns match them in every row except `clip`.
- **The lamp-post crash still gets no clip.** What is left after the phone cut is 2.2 s, under 15 s.
- **Postable** for the 15 s clips is mostly unrated: the 8 s ratings don't carry over to them (8 s is under 80 % of
  15 s).

# PR 5: eval fixes and Postable@5 (2026-10-23)

clip.py is unchanged, so no pick, clip edge or `timeline.csv` column changed. The table rescores the runs of the
section above with PR 5's eval.py (keys at revision 2): v3 `test1\20261023-205249`, `test2\20261023-205553`; v4
(15 s) `test1\20261023-205413`, `test2\20261023-205723`. clip.py's last change (20:51) predates both v4 runs.

## What changed in eval.py

- **Half-open ranges.** A clip hits a range only if they share more than 0 s. Before, a clip that only touched a range
  at its edge (0.0 s shared) counted. A range whose end isn't after its start now stops eval.py with an error; no key
  has one.
- **Ratings follow the peak.** A rating applies to a picked clip when the picked clip is 15-30 s long, the rated clip
  is at least 15 s long, and the two peaks (`peak_time_s`) are at most 4 s apart, or it is the same clip (start and end
  within 0.01 s), whatever the peaks. Ratings of clips under 15 s stay in the files as history but don't count. Of
  several, a rating of the same clip wins; then the newest: rated date, then run timestamp, then nearest peak. The old
  rule needed 80 % of the longer clip shared.
- **Peaks of old ratings.** The rating files from before PR 5 store no peak and were not rewritten.
  `rating_peaks.json` holds their peaks, read from the run each file names; all 20 rated clips matched exactly one
  clip there. Newer rating files carry `peak_s` on each clip.
- **Postable@5.** Of the first 5 picks by rank, how many have a postable rating, out of 5. Unrated picks among them
  are counted separately and never count as postable.

## Results (dev set)

| Video | Version | Hits (strict) | Postable@5 | Unrated | Recall | Bad picks | Average length |
|---|---|---|---|---|---|---|---|
| test1 | v3 | 5/5 | 4/5 | 0 | 4/5 | 0 | 26.0 s |
| test1 | v4 | 5/5 | 5/5 | 0 | 5/5 | **0** (was 1) | 26.8 s |
| test2 | v3 | 3/5 | 3/5 | 0 | 2/3 | 0 | 20.0 s |
| test2 | v4 | 3/5 | 3/5 | 0 | 2/3 | 0 | 15.3 s |

The same runs and rating files under the old rules: test1 v3 postable 4/5 (0 unrated), v4 4/5 (1 unrated) with
1 bad pick; test2 v3 3/5 (0 unrated), v4 3/5 (2 unrated).

- **Bad picks.** Only test1 v4's 3:50.2-4:19.0 changes (1 → 0): it shares 0.0 s with the safehouse cutscene range,
  4:19.0-4:38.0. Old and new eval.py were run on all 22 test1/test2 runs (v1-v4). The only other changes are the two
  earlier v4 runs of test1 (`20261023-202612`, `20261023-203050`): the same clip, the same 1 → 0. No precision,
  strict or recall number changed in any run.
- **A clip's own rating beats a newer one of another cut.** test1 v3's clip 4, 2:26.0-2:46.0, keeps its own v2
  rating, not postable ("boring, bad cut"), although the owner later rated v4's 2:16.0-2:46.0 (same peak, 2:35.0)
  postable. With plain newest-wins, as this PR first built it, clip 4 took that rating and test1 v3 scored 5/5; the
  owner chose the same-clip rule instead. It changes no other Postable@5: test1 v3's 12:00.2-12:20.2 and test2 v3's
  2:59.5-3:19.5 now take their own ratings instead of newer ones of the same moment, and all of them say postable.

## Which rating each clip takes

| Video | Version | # | Clip (peak) | Rating | From (rated clip, peak) |
|---|---|---|---|---|---|
| test1 | v3 | 1 | 3:03.5-3:33.5 (3:24.5) | postable | test1_v2, the same clip |
| test1 | v3 | 2 | 1:18.0-1:48.0 (1:32.5) | postable | test1_v2, the same clip |
| test1 | v3 | 3 | 3:48.5-4:18.5 (4:01.5) | postable | test1_v2, the same clip |
| test1 | v3 | 4 | 2:26.0-2:46.0 (2:35.0) | not postable | test1_v2, the same clip |
| test1 | v3 | 5 | 12:00.2-12:20.2 (12:03.5) | postable | test1_v3, the same clip |
| test1 | v4 | 1 | 3:10.2-3:40.2 (3:24.5) | postable | test1_v2, 3:03.5-3:33.5 (3:24.5) |
| test1 | v4 | 2 | 0:57.5-1:27.5 (1:08.5) | postable | test1_v4_15s_b, the same clip |
| test1 | v4 | 3 | 3:50.2-4:19.0 (4:01.5) | postable | test1_v2, 3:48.5-4:18.5 (4:01.5) |
| test1 | v4 | 4 | 2:16.0-2:46.0 (2:35.0) | postable | test1_v4_15s_b, the same clip |
| test1 | v4 | 5 | 12:02.3-12:17.3 (12:03.5) | postable | test1_v4_15s, the same clip |
| test2 | v3 | 1 | 0:00.0-0:20.0 (0:07.5) | not postable | test2_v2, the same clip |
| test2 | v3 | 2 | 4:37.0-4:57.0 (4:49.5) | postable | test2_v4, 4:40.0-4:56.5 (4:49.5) |
| test2 | v3 | 3 | 0:39.7-0:59.7 (0:47.5) | not postable | test2_v2, the same clip |
| test2 | v3 | 4 | 5:07.0-5:27.0 (5:07.5) | postable | test2_v4_15s, 5:06.5-5:21.5 (5:07.5) |
| test2 | v3 | 5 | 2:59.5-3:19.5 (3:07.0) | postable | test2_v3, the same clip |
| test2 | v4 | 1 | 0:00.1-0:15.1 (0:07.5) | not postable | test2_v2, 0:00.0-0:20.0 (0:07.5) |
| test2 | v4 | 2 | 4:40.0-4:56.5 (4:49.5) | postable | test2_v4, the same clip |
| test2 | v4 | 3 | 0:42.3-0:57.3 (0:47.5) | not postable | test2_v2, 0:39.7-0:59.7 (0:47.5) |
| test2 | v4 | 4 | 5:06.5-5:21.5 (5:07.5) | postable | test2_v4_15s, the same clip |
| test2 | v4 | 5 | 3:04.5-3:19.5 (3:07.0) | postable | test2_v4_15s, the same clip |

- test2 v4 clips 1 and 3 take v2's "clumsy crashes" ratings (same peaks); the owner confirmed that is right.
- The four 8 s ratings in `test2_v4.json` (0:02.9, 0:45.1, 5:06.5 and 3:11.5) are history and not used. Its 16.5 s
  clip, 4:40.0-4:56.5, counts.

## Owner ratings of test1 v4 clips 2 and 4

Rated on 2026-10-23 against `test1\20261023-205413` and stored in `ratings/test1_v4_15s_b.json`, a new file, so
`test1_v4_15s.json` is unchanged: 0:57.5-1:27.5 postable and 2:16.0-2:46.0 postable, no reason given. Without these,
the new rule gave both clips a v2 rating with the same peak: 0:48.0-1:08.0 (peak 1:06.5) and 2:26.0-2:46.0
(peak 2:35.0), both "not postable, boring, bad cut", given for other cuts.

## Outside review (Argon), checked against the code and data

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | eval's `hits()` counts a 0.0 s touch | true; fixed | `eval.py` used `<=` both ways; test1 v4 3:50.2-4:19.0 was a bad pick with 0.0 s shared |
| 2 | v4 doesn't extend clips to 15 s and still makes 8 s clips | false | `clip.py` extends to `--min-len` (default 15) and drops shorter; the 15 s runs' lengths are 15-30 s. The reviewer read `ratings/test2_v4.json`, which rates the 8 s run |
| 3 | HUD boxes scale by height/1088, so 1080/2160 footage turns HUD detection off silently | partly | The arithmetic is right (see README Known limits), but the 20 % guard prints a WARNING and `scores.json` records the HUD state. Not seen on such footage. Left for a HUD-profile PR |
| 4 | The phone-cut loop skips active steps after a short phone blip | false | `s` only moves on a phone step, to that step's end, which is where the next step starts. A synthetic check through `pick_clips` kept action that starts right after a phone stretch |
| 5 | `steps(a, b)` is off by one at exact 0.5 s multiples | false | The end is exclusive, and dividing by 0.5 is exact: `steps(0, 10)` is steps 0-19. A piece ending at 47.0 s did not count a 9.0 step at 47.0-47.5 |
| 6 | Muted or missing audio crashes or divides by zero | false | 60 s synthetic videos with no audio stream and with a silent track both ran to the end (exit 0, 2 clips). Silence reads -163.5 LUFS, floored to -70; the 1.0 LU scale floor prevents the division by zero |
| 7 | `ratings/test1_v4.json` is missing | partly | It never existed: test1's 8 s clips were never rated. Nothing to fix |
| 8 | The 80 % rule drops ratings when the clip length changes | true; replaced | Not silent: such clips printed "unrated". Replaced by the peak rule above |

Found while checking claim 4, not changed: when cutting the phone out splits a clip, the piece with the best step is
kept even if it is under 15 s, and then the candidate is dropped (README, Known limits). No test1/test2 clip has
phone time cut.

## Tests

- `selftest.py`: PASS. The same three 15.000 s clips as before, 0:34.8-0:49.8, 1:24.8-1:39.8 and 2:14.8-2:29.8.
- `hudtest.py`: PASS (5 required checks and 4 calibration frames).
- `evaltest.py` (new): PASS, 20 checks on made-up clips, ranges and ratings. Each group was run before its change:
  4 of the 6 range checks, 7 of the 8 rating checks, all 3 Postable@5 checks and all 3 same-clip checks failed then.

# v6: local vision-model veto (2026-10-24)

Same machine, GPU decoding, default flags plus `--vlm`, every run one at a time with `C:\AI\scripts\gpu_log.ps1`
logging the GPU once per second. Ollama 0.40.8, `qwen3-vl:4b-instruct` (digest 84f7ed8042fa) and
`qwen3-vl:8b-instruct` (70e58e37beb1), Q4_K_M, from Ollama's library. Before each run: 12.2-12.5 GB RAM free, about
410 MiB of VRAM in use, no model loaded in Ollama. After each run Ollama had no model loaded. Keys at revision 2;
ratings as in PR 5 (no new rating files).

## `--vlm off` is v5

| Video | v6 off run | v5 run | timeline.csv | scores.json |
|---|---|---|---|---|
| test1 | 20261024-140327 | 20261023-205413 | byte-identical | identical apart from created, timing, `tool` ("gta-clipper v6") and the new `settings.vlm` ("off") |
| test2 | 20261024-140514 | 20261023-205723 | byte-identical | the same |

## The candidates

With `--vlm` the picker makes 10 candidates. In both videos candidates 1-5 are the v5 picks, in the same order, so a
veto can only bring in candidates 6-10:
- test1: 10:20.3, 12:30.9, 14:27.5, 9:55.3, 11:37.3;
- test2: 15:57.1, 13:30.6, 13:01.7, 16:49.2, 10:53.0.

None of them has an owner rating. test2's 10:53.0-11:08.0 shares 9 s with the never-pick range 10:38-11:02 (the call
that carries on with no phone on screen). Both models vetoed it with every prompt version, so it was never picked.

## Table A: the pipeline, frozen prompt v3

| Video | `--vlm` | Run | Hits (strict) | Recall | Bad picks | Postable@5 | Unrated | Average length | Model time | Run time |
|---|---|---|---|---|---|---|---|---|---|---|
| test1 | off | 20261024-140327 | 5/5 | 5/5 | 0 | 5/5 | 0 | 26.8 s | - | 79.9 s |
| test1 | 4b-instruct | 20261024-143602 | 4/5 | 4/5 | 0 | 3/5 | 2 | 21.0 s | 45.0 s | 131.8 s |
| test1 | 8b-instruct | 20261024-144132 | 5/5 | 5/5 | 0 | 5/5 | 0 | 26.8 s | 64.0 s | 158.2 s |
| test2 | off | 20261024-140514 | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | - | 84.9 s |
| test2 | 4b-instruct | 20261024-143345 | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 46.6 s | 140.3 s |
| test2 | 8b-instruct | 20261024-143856 | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 66.4 s | 161.3 s |

- **Model time** is `vlm_model` + `vlm_unload` on a first run (nothing cached): the model load, 10 calls and the
  unload. Building the 10 sheets adds 10.2-10.9 s. A cached re-run makes no call.
- **test1, 4b.** It vetoed candidates 1, 3, 4 and 8-10.
  - The clips are 2, 5, 6, 7, then 1 as filler.
  - Recall drops because the shootout range (2:10-2:57) lost its clip (candidate 4, 2:16.0).
  - The new pick 12:30.9-12:45.9 is 0 % inside action ranges (after the yacht chase ends at 12:30).
  - Two picks (10:20.3, 12:30.9) are unrated, so its Postable@5 of 3/5 is provisional.
- **test2, 4b.** It vetoed both clumsy crashes (candidates 1 and 3) and 6-10.
  - Only 3 candidates survived, so the crashes came back as fillers.
  - The clips are v5's five, reordered: 4:40.0, 5:06.5, 3:04.5, then 0:00.1 and 0:42.3 as fillers.
  - Postable@5 is unchanged, because it counts the first 5 whatever their order.
- **8b.** It vetoed no candidate in test1, and only 7-10 in test2. Its clips equal v5's in both videos.
- **Bad picks** stay 0 in every run.

The same numbers for prompts v1 and v2 are in `prompts/LOG.md`.

## Table B: the model as a judge

The 10 candidates with an owner rating are test1 1-5 (all postable) and test2 1-5 (1 and 3 not postable: the clumsy
crashes). Each candidate's rating is found with eval.py's rule. Counted with `vlmeval.py`, both videos together:

| Prompt | Model | Vetoed but postable (costly) | Kept but not postable | Agreements (vetoed / kept) |
|---|---|---|---|---|
| v1 | 4b-instruct | 2 (test1 3:10.2, 3:50.2) | 1 (test2 0:00.1) | 7 (1 / 6) |
| v1 | 8b-instruct | 2 (test1 3:10.2, test2 4:40.0) | 2 (test2 0:00.1, 0:42.3) | 6 (0 / 6) |
| v2 | 4b-instruct | 3 (test1 3:10.2, 3:50.2, test2 5:06.5) | 1 (test2 0:42.3) | 6 (1 / 5) |
| v2 | 8b-instruct | 0 | 2 (test2 0:00.1, 0:42.3) | 8 (0 / 8) |
| **v3** | **4b-instruct** | **3** (test1 3:10.2, 3:50.2, 2:16.0) | **0** | **7 (2 / 5)** |
| **v3** | **8b-instruct** | **0** | **2** (test2 0:00.1, 0:42.3) | **8 (0 / 8)** |

The 20 unrated candidates (6-10 of each video) aren't counted. With so few rated candidates, and all three prompt
versions judged on the same 10, this table can show a gross failure, not an accuracy.

What the costly mistakes have in common:
- Every costly veto in every version is a stretch of driving, or walking to a car, during a chase: test1's police
  stretches, test2's gang chase and its getaway after the slow-motion flash.
- The model called each one boring driving.
- On the sheets, these clips show the player's car on an ordinary street. There is no police car in frame, and the
  wanted stars are a row of about 25x6 px.

## GPU memory and timing

| Model | Ollama `/api/ps`: size / on the GPU | Peak GPU memory used (nvidia-smi, whole GPU) | Peak temperature | First call (load) | Other calls |
|---|---|---|---|---|---|
| 4b-instruct | 3.33 / 3.33 GiB | 4,876-4,906 MiB | 63 C | 6.5-7.8 s (3.0-4.3 s) | 3.6 s |
| 8b-instruct | 5.90 / 5.07 GiB (0.83 GiB on the CPU) | 6,942-6,944 MiB | 69 C | 9.7-12.0 s (4.5-6.5 s) | 5.3 s |

- About 410 MiB is in use before a run.
- Shared GPU memory on the NVIDIA adapter peaked at about 490 MB (4b) and 900 MB (8b). The log doesn't show whether
  that was at load (pinned memory) or during the calls.
- No run went above 85 C. The unload took 4.1-4.2 s every time, and nvidia-smi was back to about 430 MiB afterwards.
- Prompt v3 plus the sheet is 1,632 prompt tokens. Answers are 49-86 tokens, so `num_predict` 300 never cut one off.

## The installed `qwen3-vl:4b` / `:8b` (Thinking builds)

- Probed once before the build, on a sheet of test1 6:00-6:15, a cutscene that is not a candidate.
- With `think: false`, both still returned about 1,200 characters of thinking.
- The 4b used all 300 output tokens thinking and returned no JSON. The 8b thought, then returned valid JSON.
- The owner chose the Instruct builds. Both Thinking builds are still installed.

## Tests

`selftest.py` PASS (the same three 15.000 s clips). `hudtest.py` PASS. `evaltest.py` PASS (20 checks). `vlmtest.py`
PASS (25 checks).
