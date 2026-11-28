# A day's footage, scanned for the owner to judge (Nov 28)

The owner's brief: find and rank candidates in 10 new recordings, show them on one page they can judge fast, render drafts, and
let the owner make the final pick (the finder can't judge "funny": it picked a GPS crash for the swearing in its dialogue).

```powershell
.\.venv\Scripts\python.exe day_scan.py measure  <stems>   # clip.py as /launch runs it, into the bank's clip_runs (7 min / 92 min)
.\.venv\Scripts\python.exe day_scan.py spikes   <stems>   # candidate windows from its timeline.csv -> review\<day>\scan.json
.\.venv\Scripts\python.exe day_scan.py overview <stems>   # a frame every 10 s, 30 a sheet, to look at by eye (2 min)
.\.venv-tts\Scripts\python.exe day_scan.py transcribe <stems>  # Whisper over whole recordings, voice detector, beam 1 (3.5 min)
.\.venv\Scripts\python.exe day_scan.py page              # review\<day>\index.html from judged.json (5.5 min, previews)
```

`hear`, `look` (the local vision model on each spike) and `sheets` are in the file but were **not run on Nov 28**: the spikes
were weak, so the frames were looked at by eye (overview sheets, then strips round each candidate) and the transcripts gave the
dialogue. The vision model ran only inside the renders (the kept safety question).

`judged.json` is written by hand after looking at the frames: what happens, the lines, a type, a rank, flags. The page shows 5
thumbnails and a 480p preview with the game's sound for each candidate (best guess first, at most 20) and each robbery step.

## Calls made on Nov 28 (the owner was away)

- **No full index.** `bank.py build` indexes every new recording with the vision model: at the Nov 24 rate (60 min of footage took
  41 min) the 92 minutes would take about an hour, and the brief said "vision only around the spikes". The clip.py runs were made
  into `C:\AI\gta\bank\clip_runs`, which `bank.py build` reuses; the bank, the index and the records were not touched.
- **The spikes were weak.** clip.py's picture z-score is capped at 6 and every camera cut hits it; the funny moments were found
  by the whole-recording transcripts and by eye on the overview sheets. The spike list only sorted what to look at.
- **Wanted stars**: clip.py's GTA 6 star reader reads the red security-camera icon (a CCTV camera in a red circle, shown during a
  takedown in a robbery) as 1 star: 131054 41.5-51 s, 132609 70-70.5 and 78.5-79 s. Checked on full-size crops; no star was on
  screen. Not changed in clip.py (no time to measure a fix on the old recordings); noted for the owner.
- **`meme_short.py render` on footage with no index**: it raised (`load_index`). It now uses the index when there is one and else
  the bare index marked mode already used (flag NOT INDEXED, the safety question every second); memetest check
  `render_takes_a_recording_with_no_index_and_flags_it`.
- **`make_short.py`'s picks.jpg** cropped PS5 VP9 recordings by their coded size (1920x1088) while ffmpeg hands the filter 1080
  rows (the container crops them): the crop now clamps to the decoded frame. No check was added for it (a fixture needs a VP9
  file with a container crop); the real render of the robbery Short ran through it.
- **The robbery Short is an explainer (`make_short.py`), not engine v4.** v4 renders only a /launch pick, and a pick needs a payoff
  of 2/3 ("never picked by any route"); a robbery that works is not something the bank can detect (bank.py's own note), so a
  zero-star robbery can't be picked without a rule change. `footage_short.py` refuses a script with no library shot, and none of
  these recordings is in the library. `make_short.py` with the footage named by file and time is the narrated pipeline that runs.
  Its bar is 34-39 s and at most 115 words; at the owner's 1.20 speed 115 words is 30.3 s, so it is NOT READY by that bar
  (not loosened).
- **Kept checks blocked 4 drafts** (not overridden): the safety question called three side-encounter conversations cutscenes
  (the HUD is hidden and the game's camera is used), and saw blood in the first 7 s of a street fight.
