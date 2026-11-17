# PR 26 (S4): story layer + edit engine v2 — plan

## Context
The /launch dry-run Shorts (PR 25) work but aren't worth posting. The owner found two causes. (1) The editing is
repetitive: `launch.layout()` flips the gameplay between full screen and card every ~7 words, so the background
changes 7+ times every 10 s and nothing sits on top. (2) There's no story: no claim in the first 2 s and no payoff.
The reference (`docs/reference/gta6videos_breakdown.html`) shows the fix: slow background cuts (3.5–5 s) under a fast
pop-in layer (every 1.5–2 s, each with a sound effect), a script that goes claim → bridge → pivot → payoff, the payoff
held for 4–5 s, and a loop ending. S4 adds this as a "story layer", plus a whisper check for swearing in the game's
own sound. Hard rules: never merge; branch `feat/story-layer` (PR #26); commit + push after every step; no test is
loosened; test1 `timeline.csv` stays byte-identical to run `20261104-152836`; clip.py stays standard-library only;
every PR 25 truth and safety check keeps working.

## Files
| File | What |
|---|---|
| `edit2.py` (new, .venv-tts) | Edit engine v2: plans and renders the background track, pop-in layer, captions, break, pill, loop end and audio. Built on explainer.py's own parts, not a copy of them (list below). |
| `edit2test.py` (new) | Tests for the scheduler, layout, captions, break, loop, the SFX for each pop-in, and a small real render |
| `transcribe.py` (new, .venv-tts) | faster-whisper small.en: GPU (CTranslate2 CUDA) first, else CPU int8. Writes a JSON of words with times per stretch of footage. |
| `safety.py` | `spoken_in()`: prepare (in .venv) runs transcribe.py as a subprocess, then checks the words with the existing `PROFANITY` regex. Swearing in the first 7 s moves the start or drops the type (the same path as `bad_start`); swearing later gets a PROFANITY flag. Saves `transcript.json` per pick as evidence (kind `heard`), and lines may never quote it. |
| `launch.py` | `render_narrated` / `render_action` build an edit2 plan (engine v2 is the default; `--engine v1` keeps today's). `candidates.json` gains `format_options`, F1 `compare` clips and `dryrun_standin`. The SOUND flag text changes once whisper runs. |
| `truth.py` | New checks: `format` (F1–F4) must be in the pick's `format_options`, and the 3 Shorts must use ≥ 2 formats; claim/question in the first 2.5 s; payoff line lands at 45–75 %; loop line (no spoken CTA, no closing full stop, flows into line 1); pop-in and break text get the same word/number/name/spoiler checks; lines may cite `C` (comparison) evidence ids. |
| `footage_short.py` | An optional `"engine": "v2"` that renders through edit2 (default unchanged). |
| `explainer.py` | Changes only where edit2 needs a hook, each with a default that keeps today's output exactly: `card_layer(..., radius, border)` params; one shared `FOREGROUND_SFX` map. explainertest must pass unchanged. |
| `.claude/commands/launch.md` | The formats, the arc rules, the pop cues and break text Claude writes, and the word budget. |
| `LAUNCH_NIGHT.md`, `shotlist.json` | An "EXPERIMENTS TO PLAY" list mapped to F1–F4, and which F1 moments my GTA 5 library already has |
| `README.md`, `docs/S4_PLAN.md` | Docs (the plan below is committed as docs/S4_PLAN.md) |

**explainer.py parts edit2 reuses:** `synthesize` / `normalise_voice` (voice and word times), `music_bed`, `game_bed`,
`sfx_track` + `whoosh` / `hit` / `lock_click` / `mouse_click`, `normalise`, `card_layer` / `rounded_rect` /
`write_png`, `word_events` (yellow captions), `measure` / `measure_labels` / `label_events` / `mark_drawing` /
`end_pill_events` / `dialogue` / `ass_document`, `video_filter` / `background_chain` / `source_args`, `pick_encoder`,
`pick_music`. Also reused: picker.py (Rockstar pictures), library.py + `footage_short.candidates/score` (finding
footage), and safety.py's `guard_true_peak`.

## Edit engine v2 (how a Short is put together)
1. **Voice first.** `synthesize` gives every word's start and end, and sentence gaps are already 0.12 s, so there are
   no pauses over 250 ms (checked through `longest_quiet`). The video ends 150 ms after the last syllable.
2. **Background track.** Cuts only on word boundaries. Walk the timeline and cut at the word gap nearest 4.4 s after
   the last cut, kept inside 3.5–5.0 s; a pause or a line start wins a tie. Narrated: each shot shows the moment's
   footage starting at the evidence of the line spoken over it (so truth.py's on-screen check stays true by
   construction, and its spans come from the real shots), otherwise it carries on from the last shot. Layouts:
   `full` (a 9:16 crop around the focus) or `wide` (explainer's 16:9 framed card over a blur, so the HUD shows). The
   payoff window (45–75 % of the length) is one held shot of 4–5 s.
3. **Pop-in layer.** Claude can tie a pop to a word in its line: `"pops": [{"kind": "pip", "on": "stars", ...}]`. It
   lands at that word's onset minus 0.08 s, so the whoosh peaks on the word. Then the scheduler fills the gaps
   (outside the payoff hold and the break). Gaps aim for 1.5–2.0 s and may stretch to 3.0 s; pops are never closer
   than 1.5 s. **The owner's rule: a filler pop must mean something.** The only fillers allowed are circle/zoom on real
   evidence (HUD stars, minimap, a vehicle or cop in the frame), a 1–3 word stat/text pill taken from the line, or a
   number badge. **Never an unrelated Rockstar picture.** If nothing meaningful fits, the gap is left open (up to
   3.0 s). Every gap over 2.0 s is written to the log with its reason. A filled gap takes the strongest evidence word
   in range (stars, cop, crash, water...). It takes the first pop kind that has what it needs:
   - **circle + zoom**: a red ring (`mark_drawing("circle")`) within 300 ms of the word, with a 1.00→1.18× zoom over
     1.5 s about the target's first spot. The starting box comes from the HUD profile (stars box, minimap box) or
     from a qwen3-vl bounding box with confidence ≥ 0.6. Low confidence: no circle. A HUD target outside the 9:16
     crop becomes a PIP of the HUD corner, with the ring and the zoom inside the card. *(Step 0 finding:)* cars move
     and the camera pans, so a box read once (or every 0.8 s) misses. `edit2.follow()` follows the target frame by
     frame (normalised cross-correlation on grey half-size frames). The ring ends where the match falls under 0.5,
     and it is left out (with a log line) if that leaves under 0.6 s.
   - **PIP evidence card**: 760×480, 28 px corners, 4 px white border, drop shadow, centre (540, 1320), 200 ms
     ease-out slide-up; overlaid in the final pass. It shows my own clip (another evidence moment) or a GTA 5
     comparison clip (F1). A Rockstar picture (picker.py) appears only when the line is about what that picture shows,
     labelled "ROCKSTAR"; it is never filler. It carries a small pill label ("GTA 5", "GTA 6", "ROCKSTAR",
     "DRYRUN STAND-IN").
   - **number badge** (F3): explainer's pink "number" pill.
   - **stat/text card**: a Look C pill of 1–3 words from the line.
   SFX for each kind (all made in code, already in explainer): PIP → whoosh, circle → lock click, badge or stat → hit.
4. **Midpoint break** (Shorts over 30 s, at 15–19 s, 1.5 s): F1 gets a top/bottom split (GTA 5 top, GTA 6 bottom);
   the others get a B&W freeze-frame (`hue=s=0`, a slight push-in) with 3–6 words of deadpan text that Claude writes,
   on a black top bar. The music cuts out for the freeze. In a Short of 30 s or less there is no break.
5. **End:** the silent subscribe pill (`end_pill_events`) in the last 2 s. Loop: the last ~1 s of the background plays
   the footage just before the first frame (the window keeps `LOOP_LEAD_S`), and the last line runs into line 1.
6. **Captions** (a setting): `yellow` is today's look (`word_events`: Lilita One, yellow, pop); `rsvp` is one word,
   white, ALL CAPS, Anton, cap height about 104 px, 4 px black outline and shadow, 60 ms pop 0.92→1.00. **Both sit at
   Y ≈ 740**, so the bottom zone (960–1720) stays free for pop-ins and the sample compares only the style.
7. **Audio:** voice at −14 LUFS; music and game beds as in explainer (ducked, the game rises in pauses); an SFX on
   every pop; then limiter, `guard_true_peak` (< −1.0 dBTP).
8. **Safety in the render:** pop-in footage gets the same clean-footage check as the background (`footage_short.unclean`
   on every shown span, PIP sources included). Nothing new appears in the first 7 s except footage already checked
   for gore and swearing (a PIP source in the first 7 s goes through `bad_start` too). Every overlay's text goes
   through truth.py.
9. **Action clips (v2):** the window plays as it is (no voice). The hook banner and cold open stay (hooks.py). Added:
   about 1 s of slow-mo at 0.6× on the peak (`setpts`, `atempo`); a ring around the HUD stars (the profile box mapped
   into clip.py's centred 16:9 frame) when a star rise is in view; the end replays the best 1.5 s around the peak, so
   it loops into the cold open.

## Story formats (step 3)
- **prepare computes `format_options`** per pick, in code, from evidence only:
  - **F1 UPGRADE** if a matching GTA 5 clip is found (below);
  - **F2 TEST** if the window shows an experiment: a star rise followed in the same window by stars falling to 0, a
    car change (the model's lines) or water;
  - **F3 COUNTDOWN** if there are ≥ 3 distinct evidence items (`distinct_items`), or 3 picks of the session;
  - **F4 FIRST LOOK** if the moment's activity or event is its first in the session (the first drive, star, chase,
    boat, swim...).
- **F2 rule (the owner's):** never claim cause and effect ("changing my car made them lose me") unless both the change
  and the result are on screen, in that order (evidence items in time order, both cited). Otherwise the line says
  what happened, not why. truth.py refuses cause words (made, so, because, lost them by, that's why...) in an F2 line
  unless it cites a change item and a later result item.
- **Claude picks** `"format"` plus `"format_why"` for each Short, and the 3 Shorts use ≥ 2 formats. The arc is checked
  from word timings: claim or question by 2.5 s, the `"payoff": true` line starting at 45–75 %, `"loop": true` on the
  last line, 25–40 s, no spoken CTA.
- **How F1 finds a GTA 5 clip:** map the moment's category or activity (chase, star rise, crash, boat, swim, flying,
  night drive) to a `library.find` query on the gta5 recordings (`profile: "gta5"`, same activity, `min_stars` when
  stars, text hits for crash/explosion). Rank with `footage_short.score`, skip used footage, and require it to be
  clean (`footage_short.clean`). Its evidence gets ids C1, C2... so "Remember in GTA 5…" lines cite what the GTA 5
  clip shows. A line may only describe what both clips show.
- **The dry run's GTA 6 side:** all dev footage is GTA 5, so F1 uses Rockstar's official GTA VI media (picker.py over
  `C:\AI\gta\rockstar`) as the "GTA 6" side, with a DRYRUN STAND-IN pill burned in and a red flag on review.html.
  The code refuses a stand-in unless `dry_run` is true. Lines about it may only use its tags.

## Steps and STOPs
0. Branch `feat/story-layer`; commit this plan as `docs/S4_PLAN.md`; build edit2's core. Render a ~15 s sample from
   the river-truck chase (`Grand Theft Auto V_20261026163544`, 4:19–4:33 and 4:41–4:45, skipping 4:36–4:40, which
   swears in the subtitles): a claim in the first 2.5 s, 2 background cuts, meaningful pop-ins only (a PIP of the
   HUD with the stars circled, a circle + zoom on the police car or truck, stat pills from the line, no Rockstar
   filler), a 1.5 s B&W break shown early for the demo, the subscribe pill and the loop end. Rendered in **yellow** and **rsvp** to
   `C:\AI\gta\shorts\_samples\s4\` (`sample_yellow.mp4`, `sample_rsvp.mp4` + a contact sheet). I check the frames,
   commit, push → **STOP** for the owner's pick.
1. `pip install faster-whisper` into .venv-tts; the small.en model goes to `C:\AI\gta\models\whisper\`. If the GPU
   needs extra CUDA wheels (cuBLAS/cuDNN 12), those aren't approved: fall back to CPU int8 and say so. Add
   transcribe.py, wire `spoken_in` into `check_moment` and the render-time `bad_start`, and add tests (stubbed
   transcriber + one real clip).
2. Edit engine v2 complete, plus edit2test; launch.py and footage_short.py use it.
3. Formats F1–F4 in launch.md, launch.py, truth.py, plus tests.
4. LAUNCH_NIGHT.md + shotlist.json experiments.
5. `/launch --dry-run --game gta5` → 3 Shorts in ≥ 2 formats; check every frame (sheets at 0.5 s) and listen for
   swearing via the transcripts.
6. All 12 suites + edit2test, test1 `timeline.csv` vs `20261104-152836`, README, open PR #26 (never merge). Report
   in the owner's format.

## Verification
- Every suite: launchtest, footagetest, explainertest, hooktest, shorttest, indextest, vlmtest, calibtest, evaltest,
  spreadtest, hudtest, selftest + edit2test; no existing check weakened.
- test1: `clip.py` on test1, then `timeline.csv` compared byte for byte with `C:\AI\gta\output\test1\20261104-152836`.
- Each render: the log's pacing (background shot lengths 3.5–5.0 s, pop-in gaps 1.5–2.0 s, payoff hold 4–5 s);
  `log.json` shows the SFX at each pop; true peak < −1.0 dBTP; contact sheets checked by eye.
- Pre-flight before GPU work (Ollama has nothing loaded, free RAM ≥ 10 GB); the Ollama model is unloaded after the
  bbox calls.

## Step 0 result: the sample (Nov 17)
- `edit2.py` (the engine's core) and `examples/s4_sample.json`. Rendered with
  `.venv-tts\Scripts\python.exe edit2.py examples\s4_sample.json --out <folder> --caption-style yellow|rsvp`
  to `C:\AI\gta\shorts\_samples\s4\sample_yellow.mp4` and `sample_rsvp.mp4` (14.3 s each, identical apart from
  the captions).
- Footage: the river-truck chase (`Grand Theft Auto V_20261026163544`, 4:17-4:35, which skips the swearing in the
  subtitles at 4:36-4:42). Background: 3 shots (3.8, 3.6, 4.4 s), a 1.5 s B&W break after line 3, a 1.0 s loop ending.
- Pop-ins: a HUD card with the 4 wanted stars ringed and zoomed (on "stars"), a ring that follows a police car twice
  (on "cops" and "concrete"), and 2 stat pills from the line ("INTO THE WATER", "KEEPS GOING", held through the
  payoff). No Rockstar filler. Gaps over 2.0 s are logged: 2.5 s (nothing meaningful fits), 3.5 s (the break +
  payoff hold), 2.6 s (payoff hold).
- Claim at 0.06-1.6 s; payoff line at 63 % of the Short; the last line ("...and I am still at") runs into the first
  ("Four stars and a truck in the river").
- Sound: -14.1 LUFS, true peak -1.4 dBTP; the voice's longest pause is 0.19 s.
- The circle targets' starting points were read by eye from gridded frames for this sample. In step 2 they come from
  the HUD profile or qwen3-vl.
- Known: the GTA V mission subtitles are burned in and get cut off by the 9:16 crop (GTA 6 is recorded with
  subtitles off). Line 2 originally said "on my tail"; the frames show the police cars ahead of the truck, so it says
  "right in front of me".
