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
`winget install Gyan.FFmpeg`. It also needs Python 3.14 (any recent 3.x should work).

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

## Known limits (v1)

- The scene score reacts to *sudden* change, so steady fast motion (e.g. a long high-speed drive)
  scores low. Explosions, flashes and camera cuts score high.
- HDR input is not tone-mapped. The tool prints a warning if the file is HDR. Turn off HDR capture
  on the PS5, or expect washed-out colours.
- No captions, no AI detection, no UI. These are deliberately left for later versions.
