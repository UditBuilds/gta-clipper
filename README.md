# gta-clipper (v7)

Finds the most action-heavy moments in a gameplay recording and cuts them into vertical
1080x1920 Shorts clips (15-30 s each, cut to the action). Built and tested on Windows 11 for GTA 5 PS5 captures.

The analysis needs no AI model. It uses Python's standard library plus ffmpeg. An NVIDIA GPU is used
for decoding when it can be (see [GPU decoding](#gpu-decoding)), but isn't required. v6 adds an optional veto by a
local vision model through Ollama (`--vlm`, off by default): see [Stage 2](#stage-2-vision-model-veto-v6-optional).
v7 can give that veto the wanted stars the tool measures (`--vlm-hint stars`, `--vlm-rule stars`, both off by
default): see [v7](#v7-measured-wanted-stars-optional).

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
| `--vlm` | `off` | `qwen3-vl:4b-instruct` or `qwen3-vl:8b-instruct`: a local vision model may veto candidates ([Stage 2](#stage-2-vision-model-veto-v6-optional)) |
| `--vlm-hint` | `none` | `stars`: the prompt tells the model each candidate's measured seconds of wanted stars ([v7](#v7-measured-wanted-stars-optional)) |
| `--vlm-rule` | `none` | `stars`: a `boring_driving_or_walking` veto is cancelled when the clip has at least 5.0 s of measured stars ([v7](#v7-measured-wanted-stars-optional)) |

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
- For a `--vlm` run the page also lists the picks that have no owner rating yet, then every candidate the model
  judged, vetoed ones included: the contact sheet it saw, its category and reason, kept / vetoed / "vetoed, used as
  filler" / model error, and the owner's rating where one applies (eval.py's rule). Vetoed candidates have no clip
  file; their time button plays the source from the candidate's start.

## Stage 2: vision-model veto (v6, optional)

The scores can't tell good action from clumsy action: a low-speed bump scores like a chase (see Known limits). With
`--vlm MODEL`, a local vision model looks at each candidate and may veto it. It never ranks or moves a clip.

```powershell
.\.venv\Scripts\python.exe clip.py C:\AI\gta\input\my_session.mp4 --vlm qwen3-vl:8b-instruct
```

1. **Candidates.** The picker makes 2 x `--count` clips (10 by default) exactly as without `--vlm`. It only uses the
   count to know when to stop, so the first `--count` of them are the clips it makes without `--vlm` (vlmtest.py
   checks this on made-up scores; on test1 and test2 candidates 1-5 are the v5 picks).
2. **Contact sheet.** One JPEG per candidate, 8 frames in 4 columns and 2 rows, read left to right, top row first.
   - The frames sit at the midpoints of 8 equal slices of the clip. The one nearest the clip's peak (`peak_time_s`)
     is moved exactly onto it and labelled PEAK in yellow. Moving the nearest frame keeps the time order.
   - Each frame is 384x216 (a fifth of 1080p) under a 24 px black bar with its number and source time, so the label
     covers no HUD. The sheet is 1536x480, about 190 KB, built by ffmpeg on the CPU in about 1 s.
   - Why this size: big enough to see cars, people, damage, menus and subtitles; small enough to keep each call at a
     few seconds on 8 GB. Both sides are multiples of 32 px. Ollama counts the sheet as 1,046 image tokens (measured:
     1,455 prompt tokens with the v1 prompt, 409 without the sheet).
   - `SHEET_VERSION` (now 2) is part of the cache key; version 1 was the plan's 8 even frames, never sent to a model.
3. **The call.** `POST /api/chat` to Ollama at `http://localhost:11434` (urllib; the sheet as base64). The body has
   the prompt and the sheet, `format` = a JSON schema, temperature 0, seed 42, `num_ctx` 4096, `num_predict` 300.
   The schema asks for `reason` (one sentence) first, then `player_stalled` (true/false), then `category`, one of
   `stunt_or_explosion`, `chase_or_shootout`, `other_action`, `clumsy_crash_or_stuck`, `boring_driving_or_walking`,
   `menu_or_cutscene`. The reason comes first so the model describes the frames before it picks. Thinking is turned
   off (`think: false`) when a model has a thinking mode; the Instruct builds have none, so the field is left out.
4. **Veto.** `clumsy_crash_or_stuck`, `boring_driving_or_walking` and `menu_or_cutscene` veto; `player_stalled` is
   recorded but doesn't veto.
   - The candidates are walked in rank order: a vetoed one is skipped and the next one moves up. The first `--count`
     left are the clips.
   - If fewer survive, the best-ranked vetoed candidates fill the gap, after the survivors, marked "vetoed, used as
     filler".
   - An answer that can't be used (the call failed or timed out, bad JSON, unknown category) never vetoes: the
     candidate counts as kept and is marked as a model error.
   - Trimming, the 15-30 s length, spread and the phone rule all still hold: the clips are a subset of picks that
     already met them.
5. **Order.** Signals (GPU decode), then contact sheets (CPU), then model calls, then the model is unloaded
   (`keep_alive: 0`, and the run waits until Ollama's `/api/ps` no longer lists it), then the clips are rendered
   (NVENC). Ollama and the model are checked before the slow analysis: if either is missing the run stops with an
   error, so `--vlm` never falls back to off without saying so.
6. **Cache.** Each usable answer is stored in `C:\AI\gta\vlm_cache\<video>\<sha256>.json`. The key is the video's name
   and size (videos are never hashed), the clip's start, end and peak, the model's name and digest, the prompt
   version and its sha256, the sheet version, the options and the think setting. A re-run takes the stored answer and
   makes no call. Failed answers aren't stored, so they are asked again next time. Delete the folder to ask afresh.
7. **Prompt.** `prompts/veto_v3.txt`, frozen for the held-out test. Line endings are made LF before use and hashing,
   so a CRLF checkout sends the same text. All three versions tried on test1/test2, the stopping rule and every result
   are in [prompts/LOG.md](prompts/LOG.md).

**Models.** `qwen3-vl:4b-instruct` (3.3 GB) and `qwen3-vl:8b-instruct` (6.2 GB) from Ollama's official library,
Apache-2.0. The plain `qwen3-vl:4b` and `qwen3-vl:8b` tags are the Thinking builds. Ollama accepts `think: false` for
them, but they think anyway: about 1,200 characters per answer, and the 4b ran out of tokens before it answered. So
they aren't offered.

**Output.**
- `candidates\cand_NN_<start>.jpg`: every candidate's contact sheet.
- scores.json gets a `vlm` block with:
  - every candidate: category, player_stalled, reason, kept/vetoed/filler/error, cached or not, the sheet's frame
    times and the call's timings;
  - the model's digest and how much of it was on the GPU;
  - the prompt's version and sha256.
- `timing_s` gets `vlm_sheets`, `vlm_model` and `vlm_unload`.
- `clips` holds only the final clips, each with its `candidate_rank` and the model's verdict, so eval.py works
  unchanged. `settings.vlm` names the model ("off" by default).
- With `--vlm off`, timeline.csv and scores.json are v5's apart from `tool` ("gta-clipper v6") and `settings.vlm`:
  checked on test1 and test2 (see BASELINE). v7 adds `settings.vlm_hint` and `settings.vlm_rule` and says
  "gta-clipper v7"; nothing else changes (checked again on both videos, see BASELINE v7).

**The model as a judge.** `vlmeval.py <run_dir> [...]` compares every candidate's veto with the owner's rating, where
one applies: vetoed but postable (the costly mistake), kept but not postable, and agreements. Runs are added up per
config (model, prompt, star rule). A veto cancelled by v7's star rule counts as kept.

### v7: measured wanted stars (optional)

Every costly veto in v6 was a police chase the model called `boring_driving_or_walking`. On the contact sheet a chase
looks like ordinary driving, and the wanted stars are a row of about 25x6 px. clip.py already measures the stars on the
full-size video (step 3 above), so v7 can hand that measurement to the veto in two ways. Both are off by default, and
both need `--vlm` and the HUD rules.

- **Measured stars.** For each candidate: the seconds of the clip covered by 0.5 s steps with wanted stars. A step at
  the clip's edge counts only for its part inside the clip, so the value is never longer than the clip. (`stars_s` in
  scores.json counts every step the clip touches in full, so a 30.0 s clip that starts mid-step can show 30.5 s;
  `stars_s` is unchanged.)
- **`--vlm-hint stars`.** The prompt is `prompts/stars_v2.txt`, the final of the two versions allowed (stars_v1 failed
  the stopping rule; both, and every result, are in [prompts/LOG.md](prompts/LOG.md)). It is veto_v3 with the star
  sentences replaced. Its `{MEASURED_STARS}` line becomes, per candidate, e.g. "Measured by the tool: wanted stars
  visible for 27.5 of 30.0 s." The prompt says the line is reliable and the model's own reading of the stars is not;
  that 0 s of stars means neither boring nor that nobody is chasing (gang chases, getaways, shootouts, fights and
  stunts have none), so with 0 s the model decides as it would without the line; and, in the category list, that
  `boring_driving_or_walking` is wrong when the line shows more than 0 s. The hint and the line are part of the cache
  key. The categories, the veto and fill rules, the sheet and the options are v6's.
- **`--vlm-rule stars`.** A code rule after the model has answered: a `boring_driving_or_walking` veto is cancelled
  when the clip has at least 5.0 s of measured stars (`vlm.STAR_RULE_MIN_S`, fixed by the owner before any result).
  `clumsy_crash_or_stuck` and `menu_or_cutscene` vetoes are never cancelled. The model's answer, and so the cache, is
  the same as without the rule, so a run after a plain `--vlm` run makes no model call.
- **HUD off.** With `--no-hud` either option stops the run at once. If the minimap isn't found (the HUD rules switch
  themselves off), the run stops after the analysis, before any model call or clip.
- **Output.** `settings.vlm_hint` and `settings.vlm_rule`. With either option on, each candidate's `vlm` entry has
  `stars_in_clip_s`; with the hint also `hint_text` (the line sent), and the vlm block has `hint`; with the rule also
  `star_rule_kept`, and the vlm block has `star_rule` (category, 5.0 s, vetoes cancelled). A candidate the rule kept
  has the status "kept by the star rule". Without the options these keys are absent and the vlm block is v6's.
- **Review page.** Each candidate card shows the line in the prompt, or the measured stars, and whether the rule
  cancelled its veto.

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

eval.py's rules (half-open ranges, the rating rule, Postable@5) are checked on made-up clips, ranges and ratings. No
footage or run folder is needed:

```powershell
.\.venv\Scripts\python.exe evaltest.py
```

The v6 veto and v7's measured stars are checked on made-up model answers, with no Ollama, footage or ffmpeg:

```powershell
.\.venv\Scripts\python.exe vlmtest.py
```

- veto and fill, including fewer survivors and fewer candidates than `--count`;
- model errors never veto, and bad answers are errors;
- the request body;
- the sheet's frame times and filter;
- the cache: a stored answer makes no call, errors aren't stored, every key part changes the key;
- unloading;
- a missing model or Ollama stops the run;
- CRLF prompts;
- vlmeval's outcomes;
- the first 5 of 10 candidates being the 5 picks;
- v7: the stars line's text, the stars cut at the clip's edges (never longer than the clip), the placeholder filled
  exactly once, the cache key without a hint being v6's and with one including the line, each candidate getting its
  own line, the star rule (5.0 s, boring only), the HUD errors (also through clip.py's command line), and veto_step
  with the rule, the hint, and neither (v6's shape).

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

**test1 and test2 are the dev set (rules were tuned on these).** Every eval result (precision, recall, bad picks,
postable) in this README and in [eval/BASELINE.md](eval/BASELINE.md) is on them. The held-out videos (heldout1,
heldout2) haven't been used; the one-time test on them is planned, before any of their output exists, in
[eval/HELDOUT_PLAN.md](eval/HELDOUT_PLAN.md).

```powershell
.\.venv\Scripts\python.exe eval.py C:\AI\gta\output\test1\<run>      # key defaults to eval\answer_keys\test1.json
```

It lists each clip with the ranges it shares time with and by how many seconds, then prints:
- **overlap precision:** picked clips that share more than 0 s with an action range, out of the clips picked
- **strict precision (hits):** picked clips with at least 50 % of their length inside action ranges, out of the
  clips picked
- **Postable@5:** of the first 5 picks by rank, how many the owner rated postable, out of 5 (also out of 5 when a run
  picks fewer). The unrated picks among them are shown separately and never count as postable.
- **recall:** action ranges that share more than 0 s with at least one clip, out of all action ranges
- **bad picks:** clips that share more than 0 s with a never-pick range
- **postable:** the same as Postable@5 over all picked clips, e.g. `3/5 (0 unrated)`

Ranges are half-open (PR 5): a clip that ends exactly where a range starts shares 0 s and doesn't count. Before
PR 5 it did, which made test1 v4's 3:50.2-4:19.0 a bad pick. A range whose end isn't after its start stops eval.py
with an error. (The answer keys' notes still say "closed intervals"; they predate PR 5 and are unchanged.)

Overlap precision counts a clip that only grazes an action range by a second; strict precision doesn't. The last
line (`SUMMARY ...`) is meant for scripts: `precision=` is overlap precision, `strict=` is strict precision, and
`postable_at5=` / `unrated_at5=` are Postable@5.

**Owner ratings** live in `eval/ratings/<video>_<version>.json`. Each rated clip is stored by its time range in the
source (not its clip number) with `postable` true/false and the owner's reason. eval.py reads every ratings file for
the video. Since PR 5 a rating belongs to the rated clip's **peak** (`peak_time_s` in `scores.json`: the start of
its highest-scoring 0.5 s step). It applies to a picked clip when:
- the picked clip is 15-30 s long,
- the rated clip is at least 15 s long (ratings of shorter clips stay in the files as history but don't count), and
- the two peaks are at most 4 s apart.

A rating of the exact same clip (start and end within 0.01 s) beats every other rating, and applies even if the two
peaks are more than 4 s apart (the length rules still apply). Otherwise, if several apply, the newest wins: the latest
`rated` date, then the latest run (its folder name is a timestamp, and dates have no time of day), then the nearest
peak. If none applies, the clip is `unrated`. Between different cuts the cut isn't compared, so a rating carries over to
another cut of the same moment, "bad cut" ratings included, unless the clip has a rating of its own. (Before PR 5 a
rating carried over only when the two clips shared 80 % of the longer one.) The rating files from before PR 5 store no peak and were not
rewritten: `eval/rating_peaks.json` has their peaks, read from the run each file names. Newer rating files put
`peak_s` on each clip. eval.py stops if a rated clip has neither.

The keys were drafted from contact sheets, one frame every 10 s, with pause-map, phone-call and cutscene edges
checked on 2.5 s and 0.5 s sheets. The owner confirmed them on 2026-10-22. Revision 1 then fixed five edges from
0.5 s frames. Revision 2 (2026-10-23, before any v4 run) set the four phone-call ranges to when the phone is on
screen, and moved the start of test2's crash range from 19:09 to 19:07.5, where the swerve starts. Each key's
`revisions` block lists the frame evidence. Edges that were only seen on the 10 s sheets are good to about 5 s. The v1 baseline and the v2 and v3 comparisons are in [eval/BASELINE.md](eval/BASELINE.md).

v3 with GPU decoding: full runs took 81 s and 87 s, against 232 s and 297 s on the CPU, and `timeline.csv` was
byte-identical either way.

v3 and v4 (15 s minimum) on the dev set, scored with PR 5's eval.py (keys at revision 2):

| Video | Version | Hits (strict) | Postable@5 | Unrated | Recall | Bad picks | Average length |
|---|---|---|---|---|---|---|---|
| test1 | v3 | 5/5 | 4/5 | 0 | 4/5 | 0 | 26.0 s |
| test1 | v4 | 5/5 | 5/5 | 0 | 5/5 | 0 | 26.8 s |
| test2 | v3 | 3/5 | 3/5 | 0 | 2/3 | 0 | 20.0 s |
| test2 | v4 | 3/5 | 3/5 | 0 | 2/3 | 0 | 15.3 s |

test1 v3's 2:26.0-2:46.0 keeps its own rating, "not postable, boring, bad cut", although the owner later rated v4's
2:16.0-2:46.0 (same peak) postable: a clip's own rating beats a newer rating of another cut. Details and the earlier
numbers are in [eval/BASELINE.md](eval/BASELINE.md).

v6 with the frozen prompt v3, on the dev set (2026-10-24). Postable@5 counts unrated picks as not postable. The 4b's
test1 row includes the owner's ratings of its two new picks (both postable, added in PR 7; before that it read 3/5 with
2 unrated):

| Video | `--vlm` | Hits (strict) | Recall | Bad picks | Postable@5 | Unrated | Average length | Model time |
|---|---|---|---|---|---|---|---|---|
| test1 | off (= v5) | 5/5 | 5/5 | 0 | 5/5 | 0 | 26.8 s | - |
| test1 | 4b-instruct | 4/5 | 4/5 | 0 | 5/5 | 0 | 21.0 s | 45.0 s |
| test1 | 8b-instruct | 5/5 | 5/5 | 0 | 5/5 | 0 | 26.8 s | 64.0 s |
| test2 | off (= v5) | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | - |
| test2 | 4b-instruct | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 46.6 s |
| test2 | 8b-instruct | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 66.4 s |

- On the 12 candidates with an owner rating (10 in v6, plus test1's two new picks), the 4b vetoed 3 postable clips
  (all in test1) and both clumsy crashes. The 8b vetoed no rated candidate, so its picks equal v5's in both videos.
- In test2 the 4b's picks are v5's five clips, with the two clumsy crashes moved to the end as fillers.
- Model time is the model step plus unloading, first run (nothing cached); building the sheets adds about 10.5 s.
- Details, the judge table per prompt version and GPU memory are in [eval/BASELINE.md](eval/BASELINE.md).

v7 on the dev set (2026-10-24). "+ stars" is `--vlm-hint stars` with the final prompt stars_v2 (stars_v1 failed the
stopping rule, see [prompts/LOG.md](prompts/LOG.md)). "+ star rule" is `--vlm-rule stars` on the cached v3 answers.
Every pick of these runs has an owner rating.

| Config | Hits (strict) test1 / test2 | Recall | Bad picks | Postable@5 | Vetoed but postable | Kept but not postable | Model time (per video) |
|---|---|---|---|---|---|---|---|
| off (= v5) | 5/5, 3/5 | 5/5, 2/3 | 0 | 5/5, 3/5 | - | - | - |
| 4b v3 | 4/5, 3/5 | 4/5, 2/3 | 0 | 5/5, 3/5 | 3 | 0 | 45-47 s |
| 8b v3 | 5/5, 3/5 | 5/5, 2/3 | 0 | 5/5, 3/5 | 0 | 2 | 64-66 s |
| **4b v3 + star rule** | 5/5, 3/5 | 5/5, 2/3 | 0 | 5/5, 3/5 | **0** | **0** | 45-47 s |
| 8b v3 + star rule | 5/5, 3/5 | 5/5, 2/3 | 0 | 5/5, 3/5 | 0 | 2 | 64-66 s |
| 4b + stars | 4/5, 3/5 | 4/5, 2/3 | 0 | 5/5, 3/5 | 4 | 2 | 45-46 s |
| 8b + stars | 5/5, 3/5 | 5/5, 2/3 | 0 | 5/5, 3/5 | 1 | 2 | 62-66 s |

- The last two columns are the model as a judge on the 12 rated candidates (test1 1-7, test2 1-5).
- **The stars line in the prompt didn't work.** With either prompt version the 4b still called clips with 27.5-30 s
  of measured stars boring ("no visible police"). With stars_v2 the 8b did too, once (test1 3:50.2).
- **The 0 s line.** With stars_v1, "0.0 s" made the 4b veto all ten test2 candidates, three of them postable.
  stars_v2 fixed that, but the 4b then kept both clumsy crashes.
- **The star rule** fixes the 4b's three costly vetoes and keeps its correct ones, with no extra model call. On these
  12 candidates it is the only config with no mistake.
- **Postable@5 is the same for every config,** because vetoed candidates come back as fillers.
- With `--vlm off` and with the v6 options, v7 gives v6's results (same timelines and picks; v6's cached answers
  used, 0 model calls).
- Tables A, B and C, and the selection rule of the planned held-out test, are in
  [eval/BASELINE.md](eval/BASELINE.md). The test itself is planned in
  [eval/HELDOUT_PLAN.md](eval/HELDOUT_PLAN.md) and not run.

## Known limits

- **The HUD rules only know GTA V's PS5 layout.**
  - The crop boxes were measured on 1920x1088 PS5 captures and scale with the frame, but only 1920x1088 has been
    tested.
  - They scale by height/1088, but a PS5 `.webm` stores 1088 rows and shows 1080 (the bottom 8 are cropped), so the
    HUD sits in a 1080-row picture. In a file that really is 1080 or 2160 rows tall, the boxes land about 7 px
    (in 1080 terms) above the HUD. The 6 px bar box covers rows 1007-1012 instead of about 1014-1019 (2160: rows
    2013-2024 instead of about 2028-2039). The bars are then never seen, and the 20 % guard turns all HUD rules off
    with a WARNING. This is worked out from the crop arithmetic, not seen on such footage. Left for a HUD-profile PR.
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
  them apart. v6's `--vlm` veto is the attempt at this. With prompt v3, the 4b vetoes both; the 8b vetoes neither.
- **A rating follows the moment, not the cut (PR 5).** A rating applies to any 15-30 s clip whose peak is within 4 s
  of the rated clip's, so a "bad cut" rating carries over to a different cut of the same moment. Only a clip's own
  rating (the exact same clip) beats that: test1 v3's 2:26.0-2:46.0 stays "not postable, boring, bad cut" although
  v4's 2:16.0-2:46.0 (same peak, 2:35.0) was rated postable later. A clip with no rating of its own takes the newest
  rating of its moment, for clips of older runs too.
- **Rated peaks are fixed; new peaks follow the current scoring.** A rated clip's peak is the one its own run found.
  If a later scoring change (such as the low-speed-crash item above) moves a clip's best step by more than 4 s, its
  old ratings stop applying, or apply to a different moment. Not tested.
- **The phone cut keeps the piece with the best step, even a short one.** If cutting the phone out splits a clip, the
  piece with the highest-scoring step is kept. If that piece is under 15 s, the candidate is dropped, even when
  another piece would make a valid clip. Seen in a synthetic check (a 1 s piece with a 5.0 step beat a 7 s piece of
  3.0 steps, and no clip came out). No test1/test2 clip has phone time cut, so it changes nothing there.
- **A silent audio track halves the top score.** With no audio stream, picture change gets all the weight (scores up
  to 6.0). A silent track keeps half the weight on loudness, which then scores 0, so scores top out at 3.0 (still above
  the 2.0 floor). Both cases ran without errors on 60 s synthetic videos (2026-10-23).
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
- **A vetoed candidate still blocks its neighbours (v6).** Candidates 6-10 were picked with 1-5 already taken.
  - A vetoed candidate keeps its 10 s gap and its slot in the spread rule.
  - So a good moment right next to a vetoed crash can't take its place. The next-ranked candidate moves up instead.
  - Accepted by the owner (2026-10-24).
- **Stills can't show motion (v6).** Eight frames show where the car is, not how fast it goes or who is behind it.
  - A chase without a police car in frame can look like ordinary driving.
  - The 4b called test1's police stretches (3:10.2 and 3:50.2) "normal driving" with all three prompt versions.
  - The 8b called test2's gang chase "boring driving" with prompt v1.
- **Wanted stars are tiny on the sheet (v6).** The star row is about 25x6 px in a 384x216 frame.
  - Both models have also written "wanted stars" for test2 clips, which have none.
  - With prompt v3 such a mistake can only keep a clip: stars mean "not boring".
- **Telling the model the measured stars doesn't make it use them (v7).** With both stars prompts the 4b called
  clips with 27.5-30 s of measured stars boring, and with stars_v2 the 8b did once. Only the code rule
  (`--vlm-rule stars`) reliably keeps them.
- **The star rule only knows police chases (v7).** It cancels a boring veto only when the clip has 5.0 s or more of
  stars. A gang chase, a getaway or a fight has no stars, so a costly veto of one is not caught. None of the 12 rated
  candidates was such a case with the 4b and v3, but the 4b made such vetoes with stars_v1 (test2 4:40.0, 5:06.5).
- **The star rule trusts the star detector (v7).** The measure is the detector's per-step result (2 of 3 samples),
  which was checked on hudtest's frames and lines up with the answer keys' "wanted stars" ranges in test1, and measures 0 s
  in test2. It was not checked frame by frame over whole videos, and 5.0 s of false stars would cancel a veto.
- **The measured stars and `stars_s` differ (v7).** `stars_s` in scores.json counts whole 0.5 s steps (30.5 s in a
  30.0 s clip); the hint and the rule use the stars cut at the clip's edges. `stars_s` was left as it is so that
  `--vlm off` output doesn't change.
- **12 rated candidates (v7).** All configs and both stars prompts were judged on the same 12 candidates (test1 1-7,
  test2 1-5). That can show a gross failure, not an accuracy, and the star rule's 0 mistakes there is not a
  measured accuracy.
- **Evenly spaced frames can still miss a short event (v6).** Frames are 1.9 s apart in a 15 s clip and 3.75 s apart
  in a 30 s clip. The peak frame guarantees one frame on the picker's best moment, not on every event.
- **The model-as-judge numbers rest on 10 rated candidates (v6; 12 since PR 7).**
  - test1's five are all postable, so there the only possible mistake is a costly veto.
  - test2 has 3 postable and 2 not postable (the clumsy crashes).
  - That can show a gross failure, not an accuracy. The three prompt versions were all judged on those 10.
- **New picks are unrated (v6).** A veto moves candidates 6-10 up. They count as not postable in Postable@5 until the
  owner rates them.
- **Temperature 0 on a GPU isn't guaranteed to repeat exactly (v6).**
  - Two back-to-back calls with the same sheet gave identical replies.
  - Calls after the model is reloaded were not compared.
  - The cache makes re-runs repeat the stored answers.
- **The 8b doesn't fully fit on the GPU (v6).** Ollama reports 5.07 of its 5.90 GiB on the GPU (num_ctx 4096) and runs
  the rest on the CPU. It works, about 5 s per candidate against the 4b's 3.5 s. Peak GPU use was 6.9 GB of 8.
- **The Thinking builds ignore `think: false` (v6).** They write their reasoning anyway, so only the Instruct builds
  are offered.
- No captions and no UI. These are deliberately left for later versions.
