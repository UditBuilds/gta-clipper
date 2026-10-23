# gta-clipper (v4)

Finds the most action-heavy moments in a gameplay recording and cuts them into vertical
1080x1920 Shorts clips (15-30 s each, cut to the action). Built and tested on Windows 11 for GTA 5 PS5 captures.

No AI model is needed for the analysis. It uses Python's standard library plus ffmpeg. An NVIDIA GPU is used
for decoding when it can be (see [GPU decoding](#gpu-decoding)), but isn't required.

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
   - **Phone (v3).** The bottom-right corner is checked for Michael's phone showing a call (the iFruit call
     screen). Three parts must all match: the black face of the phone above the status bar, the bright battery
     icon in the status bar, and the even grey call panel on either side of the logo. ffmpeg averages that corner
     into 8x4-pixel cells first, so this costs little. A stretch of 1.5 s or more with the phone up, grown 0.5 s
     on each side like a missing minimap, is **cut out of clips** (v4). Unlike a missing minimap it is not a
     wall: see step 5.
   - If the bars show in less than 20 % of the video, it isn't this HUD (another game, or a different layout or
     resolution). The HUD rules then switch off with a WARNING, and scoring is loudness + picture change as in v1.
     `--no-hud` switches them off by hand.
4. The loudness and picture signals are put into 0.5 s steps. Each step is scored by how far above the video's *own*
   median it is (a robust z-score, capped at 6). The two scores are averaged into one score per step, and the
   star boost is added on top.
5. The best moment is found on a lightly smoothed copy of that score. **The clip is cut to the action around it
   (v4):**
   - A step is *active* when it scores 2.0 or more (the floor of step 7). Every step with wanted stars is active,
     because of the +3.0 boost, so for police action the edges are where the stars appear and go.
   - The action is the run of active steps around the peak, joined across quiet gaps of up to **2.5 s**. A longer
     gap, or a stretch with no minimap, ends it. 2.5 s is the lead-in plus the tail: a gap that short would be
     covered by the tail of one burst and the lead-in of the next anyway.
   - The clip starts **1.0 s** before the action's first active step and ends **1.5 s** after its last.
   - Clips are **15-30 s** (`--min-len`, `--max-len`). A shorter clip gets the missing time added, 40 % of it before
     the action. If the action runs longer than 30 s, the clip is the 30 s centred on the peak.
   - **Phone at frame level.** The phone's call screen is not a wall. Its steps keep their score, so action during
     a call is found, but the phone stretch is cut out of the clip afterwards. If that splits the clip, the piece
     with the highest-scoring step is kept. What is left must still be 15 s long and contain a step scoring 2.0
     or more; otherwise there is no clip there.

   - A clip that can't reach 15 s (walls, other clips, file ends, the phone cut) is dropped, and the next
     candidate is taken. A shorter clip is never made.

   The lead-in, tail and gap were set by the owner before any v4 run. The minimum was first 8 s (above the up-to-5 s
   false stretches the minimap check can leave inside cutscenes, see Known limits). The owner raised it to 15 s
   after rating the 8 s test2 clips: two were "too short". That is the owner's judgement of a postable length, not
   a score; no other number changed.
6. Picking is greedy: best moment first, then the next best that doesn't overlap.
   `--min-gap` is the minimum distance from the end of one clip to the start of the next.
   Stretches with no minimap are hard walls (no gap needed).
   **Spread (v3):** the video is split into *action sequences*, and at most **2 clips** come from any one
   sequence. A sequence ends at an excluded stretch, or after **20 s** in which no step reaches the 2.0 floor
   (step 7). Shorter lulls, like a straight stretch in a chase, stay inside the sequence. A clip belongs to the
   sequence of its highest-scoring step (v4: of the part left after the phone is cut out). Both numbers were
   chosen before looking at any eval result: 20 s was then the default shortest clip, so a lull that long held
   nothing worth a clip. v4 keeps them unchanged although the shortest clip is now 15 s. Phone stretches still end
   a sequence, as in v3.
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
| `--min-len` | 15 | shortest clip, seconds |
| `--max-len` | 30 | longest clip, seconds |
| `--min-gap` | 10 | seconds between the end of one clip and the start of the next |
| `--crop` | off | plain centre crop instead of the full frame over a blurred background (cuts off the minimap) |
| `--no-hud` | off | ignore the GTA V HUD: no exclusions and no star boost, so it scores like v1 |
| `--cpu-decode` | off | decode on the CPU even when the GPU could; the scores are the same (for comparisons) |
| `--out` | `C:\AI\gta\output` | output root |

Encoding uses `h264_nvenc` when a 1-frame test encode succeeds, and `libx264` otherwise.

### GPU decoding

The picture analysis is the slow part: it decodes every frame of the video. v3 decodes on the GPU
(`-hwaccel cuda`, NVIDIA's NVDEC) when a 1-frame test decode works, and on the CPU otherwise. The run prints which
it used and why, and `scores.json` records it under `settings.decoder` and `settings.decoder_reason`. If GPU
decoding fails partway through, the analysis runs again on the CPU, with a WARNING.

The decoded frames stay on the GPU until `fps=` has dropped all but the 10 + 2 per second that are analysed.
Only those are copied back, then cropped and repacked so they are byte-for-byte the frames the CPU decoder gives.
That last step matters for PS5 captures: the `.webm` stores 1920x1088 and tells players to crop the bottom 8 rows.
The CPU decoder does that crop, but GPU frames come back uncropped, and the extra rows would change every
picture-change score. Only 8-bit 4:2:0 video (`yuv420p`) takes the GPU path; anything else decodes on the CPU.
Clip encoding is unchanged.

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
  v3 appends `sequence`: the action sequence a step belongs to (empty for quiet and excluded steps), then
  `phone` (1 while the phone's call screen shows). `excluded` now covers the phone stretches too.
  `scores.json` lists the sequences under `spread`, each clip's `sequence`, and the phone stretches under
  `hud.phone_ranges_s`.
  v4 leaves those 15 columns as v3 computes them (`combined` is still 0 in phone steps) and appends four:
  `pick_score` (the score v4 picks on: like `combined`, but phone steps keep their score), `pick_smoothed`,
  `phone_cut` (1 in the phone stretches cut out of clips, 0.5 s growth included) and `action` (the clip whose
  action span covers the step; the span can reach past the clip where the phone was cut or the 30 s cap applied).
  In `scores.json`, the `edges` block has the v4 settings, and each clip has its `action_span_s`, the `window_s`
  before the phone was cut out, and `phone_cut_s` (seconds removed).

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
- Each clip in the table has a **play clip** button that plays the clip file as cut, in the same player.
- A clip shorter than the thumbnail spacing may cover no thumbnail; it still shows as a "clip N starts" note.
- Thumbnails assume a limited-range source, which is what PS5 SDR captures contain.

## Self-test

```powershell
.\.venv\Scripts\python.exe selftest.py            # add --crop or other clip.py flags; --regen rebuilds the video
```

It builds `C:\AI\gta\test\synthetic_test_3min.mp4`. That is 3 minutes of static colour bars with a
fake minimap and a timecode, a quiet noise bed, and loud bursts plus white flashes at 0:40, 1:30 and
2:20. It runs `clip.py` on that file and passes if:
- each planted time falls inside a clip whose peak is within 5 s of it,
- every clip is 1080x1920, has audio, and is 15-30 s long (0.1 s tolerance), and
- the HUD rules switched themselves off, since the synthetic video has no GTA V HUD.

It also saves one frame per clip in the run's `frames\` folder, so you can check the layout.

The HUD detectors are checked on real frames of the owner's test1.webm and test2.webm, which are not in the repo:

```powershell
.\.venv\Scripts\python.exe hudtest.py            # defaults C:\AI\gta\input\test1.webm and test2.webm
```

- test1 3:00 (pause map): the minimap must read as absent.
- test1 2:50 (wanted): stars present, phone absent.
- test1 8:30 (phone call): stars absent, minimap present.
- test1 8:20 and test2 18:57 (phone calls): phone present.
- Four extra calibration frames: the minimap flashing red, the ammo counter in the star row, and two more calls
  (test2 4:10 and 12:10).

All the phone frames were used to set the phone check, so these are regression checks. The check was then run on
every 0.5 s of both videos; see `eval/BASELINE.md`.

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

**v4 (2026-10-23): PASS** with default flags (15-30 s). Each planted burst gives a 15.000 s clip with its peak exactly
on the planted time: 0:34.8-0:49.8, 1:24.8-1:39.8 and 2:14.8-2:29.8. All three are 1080x1920 h264 with AAC stereo.
The 1.5 s bursts are found as about 2 s of action, so 4.2 s of the missing time goes before and 6.3 s after.
(With the first 8 s minimum the clips were 8.000 s, 0:37.6-0:45.6 and so on.)

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
0.5 s frames. Revision 2 (2026-10-23, before any v4 run) set the four phone-call ranges to when the phone is on
screen, and moved the start of test2's crash range from 19:09 to 19:07.5, where the swerve starts. Each key's
`revisions` block lists the frame evidence. Edges that were only seen on the 10 s sheets are good to about 5 s. The v1 baseline and the v2 and v3 comparisons are in [eval/BASELINE.md](eval/BASELINE.md).

v3 on the two test videos: 0 bad picks in both, postable 4/5 (test1) and 3/5 (test2). With GPU decoding, full runs
took 81 s and 87 s, against 232 s and 297 s on the CPU, and `timeline.csv` was byte-identical either way.

v4 on the two test videos (keys at revision 2, 15 s minimum): strict hits 5/5 and 3/5, recall 5/5 and 2/3, average
clip length 26.8 s and 15.3 s (v3: 26.0 s and 20.0 s). test1 has 1 bad pick that shares 0.0 s with the never-pick
range (see Known limits). The owner rated the 8 s test2 clips (3/5 postable); most 15 s clips are unrated. Details
in [eval/BASELINE.md](eval/BASELINE.md).

## Known limits

- **The HUD rules only know GTA V's PS5 layout.**
  - The crop boxes were measured on 1920x1088 PS5 captures and scale with the frame, but only 1920x1088 has been
    tested.
  - A different in-game safe-zone/HUD-size setting moves the HUD and breaks the boxes. The 20 % guard then turns
    the rules off.
  - GTA 6 will need new boxes and new detectors.
- **The phone check only knows Michael's iFruit call screen.** It was set on four calls, all on Michael's phone.
  Franklin's and Trevor's phones look different, and so do the phone's other screens (contacts, texts, camera).
  None of those are in the test footage, so they would not be excluded.
- **A call with no phone on screen isn't seen.** In test2 10:53-11:02 a scripted call carries on into gameplay
  with Michael holding the phone to his ear and no phone on screen.
- **The phone cut follows the phone, not the conversation.** The dialogue often starts before the phone slides up
  or goes on after it's put away; that audio stays in clips. Since revision 2 the answer keys' phone-call ranges
  also follow the phone on screen (0.5 s frames).
- **The phone rule also removes action that happens during a call.** In test2 the car swerves, mounts the kerb
  and hits a lamp post (19:07.5-19:09.5) while the call screen is still up (until 19:09.5). v2's clip of it was
  rated postable by the owner. v3 excluded it. v4 finds the crash as a candidate, but cutting the phone out
  (to 19:10.0, with the 0.5 s growth) leaves 2.2 s whose best step scores 0.92, so there is no clip: the
  19:07.5-19:55 action range gets none. This was accepted by the owner before the v4 run; no rule was changed to
  win it back.
- **Short HUD hides inside action are excluded (by decision).** The game hides the HUD for some mission
  set-pieces, and v2 treats every stretch of 1.5 s or more without the minimap like a cutscene. Seen:
  - the house collapse in Marriage Counseling (test2 3:19.5-3:35.5)
  - a 5 s camera shot in the yacht chase (test1 11:10-11:15)

  Those moments can't be clipped, even though they're action. In v2 this cost test2's house-pull range. v3 clips
  the pull up to 3:19.5, cut half a second before the house falls, and so does v4 (3:04.5-3:19.5).
- **The +3.0 wanted-star boost (kept by decision) outweighs most other action.**
  - Any step with stars outranks almost everything without them.
  - 4 of the 8 action ranges in the answer keys never show stars: the yacht chase, the house pull, the gang chase,
    and the crash and car switch. They compete on loudness and picture change alone.
- **A pause map splits a sequence.** Any excluded stretch ends an action sequence, even a 2 s pause map in the
  middle of a chase. test1's two pause maps cut its police stretch into three sequences. So that stretch still gives
  4 of the 5 picks (2 + 0 + 2), even with the cap of 2 per sequence. The fifth pick now goes to the yacht chase,
  which v2 missed.
- **In a wanted-star stretch the whole stretch is "action" (v4).** The +3.0 boost makes every step with stars
  active, so the action runs as long as the stars do: 54-76 s in test1. Those clips are the 30 s centred on the
  peak, so their edges come from the 30 s cap, the 10 s gap to another clip or a wall, not from where something
  starts or stops. test1's 3:50.2-4:19.0 starts where the 10 s gap after 3:10.2-3:40.2 ends.
- **Short bursts give minimum-length clips (v4).** Four of test2's five actions last 1-4.5 s, so those clips are
  the 15 s minimum, mostly calm time added around the burst. A burst can be a camera cut or a flash: test1's
  12:02.3-12:17.3 peaks on a cut to Franklin on the boom (12:08), test2's 5:06.5-5:21.5 on a green flash (5:07.5).
- **A low-speed crash scores like a chase.** test2's two rejected clips (0:00.1-0:15.1 and 0:42.3-0:57.3, "boring"
  and "clumsy crash") peak on camera jolts from bumping a car and a gate. Their picture-change score is at the cap,
  like the chase and the house pull the owner liked, and test2 has no wanted stars, so nothing in the score tells
  them apart. Planned for PR 5.
- **eval.py counts a clip that only touches a never-pick range as a bad pick.** Ranges are closed intervals, and a
  never-pick range starts at the last gameplay frame before it. v4's test1 3:50.2-4:19.0 ends at 4:19.0, where the
  minimap wall starts and the key's safehouse cutscene range starts too: 0.0 s shared, counted as 1 bad pick.
  eval.py is unchanged.
- **Ratings rarely carry over to v4 clips.** A rating needs 80 % of the longer clip shared, so shorter or shifted
  v4 clips show as unrated until the owner rates them.
- **Strict precision can't see a bad cut.** Both clips the owner rejected in test1 ("boring, bad cut") sit fully
  inside action ranges, so they count as strict hits. Only the owner's ratings catch them.
- **The minimap check can be fooled.** It reads the three bars under the minimap. A busy cutscene background can
  look like them for a few seconds, which leaves short gaps in an exclusion. In the two test videos those gaps
  were 5 s or less, far shorter than the 15 s minimum clip.
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
