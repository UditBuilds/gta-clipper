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
