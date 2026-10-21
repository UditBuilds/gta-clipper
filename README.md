# gta-clipper (v1)

Finds the most action-heavy moments in a gameplay recording and cuts them into vertical
1080x1920 Shorts clips (20-30 s each). Built and tested on Windows 11 for GTA 5 PS5 captures.

No AI model and no GPU is needed for the analysis. It uses Python's standard library plus ffmpeg.

## How it picks moments

1. **Loudness.** ffmpeg's `ebur128` filter measures momentary loudness (LUFS) every 0.1 s.
2. **Picture change.** Frames are shrunk to 320 px wide and compared at 10 fps. ffmpeg's scene
   score (0-1) rises when the picture changes suddenly, such as a cut, flash or explosion.
3. Both signals are put into 0.5 s steps. Each step is scored by how far above the video's *own*
   median it is (a robust z-score, capped at 6). The two scores are averaged into one score per step.
4. The best moment is found on a lightly smoothed copy of that score. The clip is built around it:
   20 s for a short burst, up to 30 s if the action keeps going. About 40 % of any spare time goes
   *before* the action, as build-up.
5. Picking is greedy: best moment first, then the next best that doesn't overlap.
   `--min-gap` is the minimum distance from the end of one clip to the start of the next.
6. A clip must contain at least one step scoring 2.0 or more. Quiet stretches are never picked,
   so you can get fewer clips than `--count`. The run prints a NOTE when that happens.

The scoring constants (weights, caps, smoothing, floor) are at the top of `clip.py`.
Every run records them in `scores.json`.

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
| `--out` | `C:\AI\gta\output` | output root |

Encoding uses `h264_nvenc` when a 1-frame test encode succeeds, and `libx264` otherwise.

## Output

Each run gets its own folder, so earlier runs are never overwritten:
`C:\AI\gta\output\<video name>\<YYYYMMDD-HHMMSS>\`

- `clip_01_<start>.mp4` ... clips ranked best first. The start time in the source is in the name.
- `scores.json` holds the input info, flags, scoring constants, timings and, for each clip, its
  start/end, peak time and why it was picked (loudness vs median, picture change vs median, z-scores).
- `timeline.csv` has every 0.5 s step: loudness, picture change, hard cuts/flashes, both z-scores,
  the combined and smoothed score, and which clip (if any) covers it. Plot it to tune the scoring.

## Self-test

```powershell
.\.venv\Scripts\python.exe selftest.py            # add --crop or other clip.py flags; --regen rebuilds the video
```

It builds `C:\AI\gta\test\synthetic_test_3min.mp4`. That is 3 minutes of static colour bars with a
fake minimap and a timecode, a quiet noise bed, and loud bursts plus white flashes at 0:40, 1:30 and
2:20. It runs `clip.py` on that file and passes if:
- each planted time falls inside a clip whose peak is within 5 s of it, and
- every clip is 1080x1920, has audio, and is 20-30 s long (0.1 s tolerance).

It also saves one frame per clip in the run's `frames\` folder, so you can check the layout.

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

## Known limits (v1)

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
