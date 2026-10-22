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
