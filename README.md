# gta-clipper (v2)

Finds the most action-heavy moments in a gameplay recording and cuts them into vertical
1080x1920 Shorts clips (20-30 s each). Built and tested on Windows 11 for GTA 5 PS5 captures.

No AI model and no GPU is needed for the analysis. It uses Python's standard library plus ffmpeg.

## How it picks moments

1. **Loudness.** ffmpeg's `ebur128` filter measures momentary loudness (LUFS) every 0.1 s.
2. **Picture change.** Frames are shrunk to 320 px wide and compared at 10 fps. ffmpeg's scene
   score (0-1) rises when the picture changes suddenly, such as a cut, flash or explosion.
3. **HUD (v2, GTA V on PS5).** The same decode also takes 2 small crops per second from fixed places on screen.
   - **Minimap.** The tool reads the health, armour and special-ability bars under the minimap. The map itself
     flashes red/blue while you're wanted, but the bars don't. A stretch of 1.5 s or more without them (pause/map
     screens, cutscenes, menus) is **excluded**: no clip may touch it. The exclusion grows 0.5 s on each side to
     cover fades.
   - **Wanted stars.** The star row's edges are compared with the outline of the five star slots. This matches
     filled, empty and flashing stars, but not the ammo or cash text that sits in that row when you're not wanted.
     Steps where stars show (2 of 3 neighbouring samples) get **+3.0** added to their score.
   - If the bars show in less than 20 % of the video, it isn't this HUD (another game, or a different layout or
     resolution). The HUD rules then switch off with a WARNING, and scoring is loudness + picture change as in v1.
     `--no-hud` switches them off by hand.
4. The loudness and picture signals are put into 0.5 s steps. Each step is scored by how far above the video's *own*
   median it is (a robust z-score, capped at 6). The two scores are averaged into one score per step, and the
   star boost is added on top.
5. The best moment is found on a lightly smoothed copy of that score. The clip is built around it:
   20 s for a short burst, up to 30 s if the action keeps going. About 40 % of any spare time goes
   *before* the action, as build-up.
6. Picking is greedy: best moment first, then the next best that doesn't overlap.
   `--min-gap` is the minimum distance from the end of one clip to the start of the next.
   Excluded stretches are hard walls (no gap needed).
7. A clip must contain at least one step scoring 2.0 or more. Quiet stretches are never picked,
   so you can get fewer clips than `--count`. The run prints a NOTE when that happens.

The scoring and HUD constants (weights, caps, smoothing, floor, crop boxes, thresholds) are at the top of
`clip.py`. Every run records them in `scores.json`.

## Install

Needs ffmpeg and ffprobe on PATH. This machine has the Gyan "full" build 9.0, installed with
`winget install Gyan.FFmpeg`. It also needs Python; tested on 3.14.7.

```powershell
cd C:\AI\gta-clipper
python -m venv .venv          # project-local; nothing to pip install (standard library only)
```

## Run

```powershell
.\.venv\Scripts\python.exe clip.py C:\AI\gta\input\my_session.mp4
```

| Flag | Default | Meaning |
|---|---|---|
| `--count` | 5 | how many clips to make (fewer if there aren't enough loud/busy moments) |
| `--min-len` | 20 | shortest clip, seconds |
| `--max-len` | 30 | longest clip, seconds |
| `--min-gap` | 10 | seconds between the end of one clip and the start of the next |
| `--crop` | off | plain centre crop instead of the full frame over a blurred background (cuts off the minimap) |
| `--no-hud` | off | ignore the GTA V HUD: no exclusions and no star boost, so it scores like v1 |
| `--out` | `C:\AI\gta\output` | output root |

Encoding uses `h264_nvenc` when a 1-frame test encode succeeds, and `libx264` otherwise.

## Output

Each run gets its own folder, so earlier runs are never overwritten:
`C:\AI\gta\output\<video name>\<YYYYMMDD-HHMMSS>\`

- `clip_01_<start>.mp4` ... clips ranked best first. The start time in the source is in the name.
- `scores.json` holds the input info, flags, scoring constants, timings and, for each clip, its
  start/end, peak time and why it was picked (loudness vs median, picture change vs median, z-scores,
  seconds with wanted stars). Its `hud` block has the HUD state, constants and the excluded stretches.
- `timeline.csv` has every 0.5 s step: loudness, picture change, hard cuts/flashes, both z-scores,
  the combined and smoothed score, and which clip (if any) covers it. Plot it to tune the scoring.
  v2 appends four columns at the end: `star_score`, `stars`, `minimap` and `excluded`. The earlier columns are
  unchanged, but `combined` now includes the star boost and is 0 in excluded steps.

## Review sheet

```powershell
.\.venv\Scripts\python.exe make_review.py C:\AI\gta\input\test1.webm C:\AI\gta\output\test1\<run> C:\AI\gta\review\test1_review.html
```

This builds one HTML page per run, with one thumbnail every 10 s (`--every`), each labelled with its timestamp.
- Thumbnails inside a picked clip are outlined in that clip's colour.
- Under each thumbnail is clip.py's highest score in the next 10 s. It turns orange at the 2.0 floor, so busy
  stretches that weren't picked stand out.
- The images are embedded, so the page opens by double-click with no server.
- Clicking a thumbnail plays the source from that time. That part needs the source to stay where it is.
- Thumbnails assume a limited-range source, which is what PS5 SDR captures contain.

## Self-test

```powershell
.\.venv\Scripts\python.exe selftest.py            # add --crop or other clip.py flags; --regen rebuilds the video
```

It builds `C:\AI\gta\test\synthetic_test_3min.mp4`. That is 3 minutes of static colour bars with a
fake minimap and a timecode, a quiet noise bed, and loud bursts plus white flashes at 0:40, 1:30 and
2:20. It runs `clip.py` on that file and passes if:
- each planted time falls inside a clip whose peak is within 5 s of it,
- every clip is 1080x1920, has audio, and is 20-30 s long (0.1 s tolerance), and
- the HUD rules switched themselves off, since the synthetic video has no GTA V HUD.

It also saves one frame per clip in the run's `frames\` folder, so you can check the layout.

The HUD detectors are checked on real frames of the owner's test1.webm, which is not in the repo:

```powershell
.\.venv\Scripts\python.exe hudtest.py            # default C:\AI\gta\input\test1.webm
```

- 3:00 (pause map): the minimap must read as absent.
- 2:50 (wanted): stars must read as present.
- 8:30 (phone call): stars absent, minimap present.
- Two extra calibration frames: the minimap flashing red, and the ammo counter in the star row.

If the footage is missing, it prints SKIP and exits with code 2. It never reports a pass without the footage.

## Synthetic test results (2026-10-20)

Test machine: HP Omen 15, i7-14650HX, RTX 5060 Laptop GPU (driver 616.56), Windows 11, ffmpeg 9.0 (Gyan full build),
Python 3.14.7. The encoder chosen was `h264_nvenc`. The test video is 1920x1080, 60 fps, 180 s, with AAC stereo.

**Result: PASS** with default flags. It also passed with `--crop`.

| Planted event | Clip (rank) | Peak found | Offset |
|---|---|---|---|
| 0:40 | 0:32.5-0:52.5 (1) | 0:40.0 | +0.0 s |
| 1:30 | 1:22.5-1:42.5 (3) | 1:30.0 | +0.0 s |
| 2:20 | 2:12.5-2:32.5 (2) | 2:20.0 | +0.0 s |

- **ffprobe:** all 3 clips are 1080x1920 h264, 20.000 s long, with AAC stereo audio. The same held in the `--crop` run.
- **Count:** the default `--count 5` produced 3 clips plus a NOTE, because the score floor rejected the quiet stretches.
- **Signals:** the quiet bed measured -42.7 LUFS and the bursts -5.7 LUFS. During the events, both the loudness and the picture-change scores hit the cap (z = 6).
- **Frames:** checked one frame per clip by eye. In the default layout, the full frame is centred over the blur, and the minimap box and timecode are visible. With `--crop`, both are cut off. The burned-in timecode equals the clip start + 2 s, which confirms where each clip was cut.
- **Runtime of `clip.py` on the 3-minute video:** 16.4 s in total.
  - audio analysis: 0.2 s
  - picture analysis: 4.1 s
  - encoding 3 clips: 11.9 s
- **Long-action path,** checked with `--min-len 1 --max-len 1.5` so the tool must keep the densest part of an event: it picked 0:40.0-0:41.5, 1:30.0-1:31.5 and 2:20.0-2:21.5, exactly the planted bursts, each 1.500 s long.
- **Not tested yet:**
  - real gameplay footage
  - the libx264 fallback
  - input with no audio
  - the HDR warning

## Evaluation

The self-test only proves that planted bursts are found. `eval.py` checks a run on real footage against a
hand-made answer key in `eval/answer_keys/` (one per test video; the videos themselves are not in the repo):

- `action`: stretches a viewer would want clipped, such as shootouts, chases, crashes, carjacks and driving with wanted stars.
- `never_pick`: pause/map screens, cutscenes, phone calls and shop menus. Each has a `kind`.

Everything else is neutral. Times are seconds in the source.

```powershell
.\.venv\Scripts\python.exe eval.py C:\AI\gta\output\test1\<run>      # key defaults to eval\answer_keys\test1.json
```

It lists each clip with the ranges it touches and by how many seconds, then prints:
- **overlap precision:** picked clips that touch an action range at all, out of the clips picked
- **strict precision:** picked clips with at least 50 % of their length inside action ranges, out of the clips picked
- **recall:** action ranges touched by at least one clip, out of all action ranges (any shared time counts)
- **bad picks:** clips that touch a never-pick range (any shared time counts)

- **postable:** clips the owner rated postable, out of the clips picked, e.g. `3/5 (0 unrated)`

Overlap precision counts a clip that only grazes an action range by a second; strict precision doesn't. The last
line (`SUMMARY ...`) is meant for scripts: `precision=` is overlap precision and `strict=` is strict precision.

**Owner ratings** live in `eval/ratings/<video>_<version>.json`. Each rated clip is stored by its time range in the
source (not its clip number) with `postable` true/false and the owner's reason. eval.py reads every ratings file for
the video. A clip from any run reuses a rating only if the two time ranges share at least 80 % of the longer one,
so each covers 80 % of the other. Otherwise it shows as `unrated`. A rating is about the cut as well as the
content ("bad cut"), so a 20 s clip inside a rated 30 s clip stays unrated.

The keys were drafted from contact sheets, one frame every 10 s, with pause-map, phone-call and cutscene edges
checked on 2.5 s and 0.5 s sheets. The owner confirmed them on 2026-10-22. Revision 1 then fixed five edges from
0.5 s frames; each key's `revisions` block lists the frame evidence. Edges that were only seen on the 10 s sheets
are good to about 5 s. The v1 baseline and the v2 comparison are in [eval/BASELINE.md](eval/BASELINE.md).

## Known limits

- **The HUD rules only know GTA V's PS5 layout.**
  - The crop boxes were measured on 1920x1088 PS5 captures and scale with the frame, but only 1920x1088 has been
    tested.
  - A different in-game safe-zone/HUD-size setting moves the HUD and breaks the boxes. The 20 % guard then turns
    the rules off.
  - GTA 6 will need new boxes and new detectors.
- **No signal for phone calls.** During a call the minimap stays and no stars show, so v2 neither excludes nor
  boosts a phone call.
- **Short HUD hides inside action are excluded (by decision).** The game hides the HUD for some mission
  set-pieces, and v2 treats every stretch of 1.5 s or more without the minimap like a cutscene. Seen:
  - the house collapse in Marriage Counseling (test2 3:19.5-3:35.5)
  - a 5 s camera shot in the yacht chase (test1 11:10-11:15)

  Those moments can't be clipped, even though they're action. This is why test2's recall fell from 3/3 to 2/3.
- **The +3.0 wanted-star boost (kept by decision) outweighs most other action.**
  - Any step with stars outranks almost everything without them.
  - 4 of the 8 action ranges in the answer keys never show stars: the yacht chase, the house pull, the gang chase,
    and the crash and car switch. They compete on loudness and picture change alone.
- **All five test1 picks come from one sequence.** They all sit in the 0:48-4:18 police stretch. The yacht chase
  (10:00-12:30), which held v1's top two picks, gets none. Picking has no rule yet to spread clips across
  different action sequences.
- **The minimap check can be fooled.** It reads the three bars under the minimap. A busy cutscene background can
  look like them for a few seconds, which leaves short gaps in an exclusion. In the two test videos those gaps
  were 5 s or less, too short for a 20 s clip.
- **The review sheet's score bar** assumes 0-6. With the star boost, `combined` reaches 9, so the bar just shows full.
- ffmpeg's scene score is built to flag *sudden* change (cuts, flashes). Steady fast motion,
  such as a long high-speed drive, probably scores low. That is not yet checked on real footage,
  so look at the `visual_change` column in `timeline.csv`.
- HDR input is not tone-mapped. The tool prints a warning if the file is HDR. Turn off HDR capture
  on the PS5, or expect washed-out colours.
- PS5 VP9 (`.webm`) captures contradict themselves about brightness range. The container says limited
  (16-235), but the VP9 header says full (0-255). The pixels are limited range (measured on two captures,
  2026-10-21). ffmpeg follows the header, so clips used to be flagged full range and looked washed out in
  players that read the flag. When the input is VP9 and its container says limited, `clip.py` now rewrites
  that header bit while cutting (ffmpeg's `vp9_metadata` bitstream filter). Pixel values are not changed.
  `scores.json` records this as `vp9_range_relabel`.
- No captions, no AI detection, no UI. These are deliberately left for later versions.
