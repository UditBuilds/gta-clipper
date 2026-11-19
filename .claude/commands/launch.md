---
description: Launch night - turn the owner's new gameplay recordings into at least 3 finished Shorts (topic explainers and action clips, every one voiced, captioned and edited like the owner's posted sample), with a review page, no questions asked
argument-hint: "[--count N] or --dry-run --game gta5 --videos <files>"
---

Make **at least 3** Shorts (or `--count N`) from the owner's **new gameplay recordings**, start to finish, and **ask
the owner nothing**. The owner reviews and uploads them on `review.html`; you never upload anything. Extra arguments:
**$ARGUMENTS** (empty on a normal night; `--count 5` for more; a trial uses `--dry-run --game gta5 --videos ...`).

**The bar (the owner's rule, Nov 19):** every Short is at least as good as the posted Step 0 sample, "How to make GTA 6
cops FORGET you" (`C:\AI\gta\shorts\2026-11-19-how-to-make-gta-6-cops-forget-you`, its script in
`C:\AI\gta\shorts\_drafts\gta-6-make-cops-forget-you.json`): the hook on screen from frame 0, the am_puck voice with
yellow word captions, music and sound effects, a setup -> payoff, and the owner's footage as the **proof**. **No Short
ever ships as raw gameplay**: if a pick can't be made that way, leave it out and say why.

**Topic first.** The main output teaches the viewer one thing about GTA 6 (a mechanic, a detail, a "wait, really?"),
with the footage proving it. Action clips (a crash, a shootout, a jump) are the second type: a short voice, setup ->
payoff, the game's own sound in.

Work in `C:\AI\gta-clipper` (Windows, PowerShell). `prepare` runs with `.\.venv\Scripts\python.exe`, `render` and
`frame` with `.\.venv-tts\Scripts\python.exe`. Never touch `C:\AI\gta\heldout`. If a command stops with `ERROR:`,
tell the owner the error in one plain sentence and what to do, and stop: that is the only time you stop early.

## 1. Prepare (up to about 90 minutes for 2 hours of footage)

```powershell
.\.venv\Scripts\python.exe launch.py prepare $ARGUMENTS
```

Run it **in the background**; don't poll it or sleep, you are told when it ends. It finds the new recordings (only
those not indexed yet), reads the HUD with `profiles/gta6.json` (every HUD step in `<run folder>\logs\hud.txt`),
indexes them, then builds a **pool of 15-25 candidates** spread over all recordings: **topic candidates first** (the
shot-list topics the footage matches, and GTA 6 HUD changes such as the heat icons or the stars' look), then the best
action moments. Every candidate is checked: no cutscene, menu, map, phone, loading screen, blood in the first 7 s or
swearing in the subtitles; and its **labels are checked against what the vision model sees in its own frames** (a
"crash", "shootout", "police" or "jump" that isn't seen is dropped). Mission dialogue costs points. The best that pass
are picked. Everything is in `<run folder>\candidates.json`.

## 2. Look at each pick

For every entry of `picks`:
- `type`: `narrated` (a topic explainer, about 25-39 s) or `action` (16-32 s). Decided in code; keep it.
- `topic` (a topic candidate): what the footage proves (`id`, `why`, `at_s`) and the facts.json ids it may use
  (`facts`). `format_options`: the formats this moment can teach (T1 a topic, F1-F4 as before); an action pick is A1.
- `window`, `anchor_s`, `evidence` (E1, E2 ...: what the footage shows, when, and where that comes from: the HUD's stars,
  `heat` (the icons under the stars) and `star_look` (red, red outline, white outline), activities, events the frames
  confirmed, the model's lines), `flags`, `mode`, `mission`, `dropped_labels` (what the frames did not confirm).
- **Look at the frames**: `sheet` (a frame every 2 s). For a close look at an exact second:
  `.\.venv-tts\Scripts\python.exe launch.py frame --run "<run folder>" --short N --at 260.0 261.0`.
- `C:\AI\gta\facts\own` and `.\.venv\Scripts\python.exe facts.py own`: facts from the owner's own footage (the game's
  own words on screen). Only **confirmed** ones may be used.

## 3. Write `<run folder>\shorts.json`

One entry per pick you make (at least 3 when 3 or more picks can keep the rules). Like the sample:

```json
{"shorts": [
  {"short": 1, "moment": "M3", "type": "narrated", "format": "T1",
   "format_why": "the HUD shows the stars turn to outlines once the icons are gone: the footage proves the claim",
   "hooks": ["Can cops forget you?", "Make GTA 6 cops forget you", "Watch these icons vanish"], "star": 0,
   "title": "How to make GTA 6 cops FORGET you", "description": "Sentence one. Sentence two.",
   "hashtags": ["#GTA6", "#GTAVI", "#Shorts", "#GTA6Gameplay"],
   "lines": [
     {"say": "Can you make the cops in GTA 6 forget you?", "claim": "ask", "evidence": [], "answered_by": 6,
      "beats": [{"beat": "full", "at": 98.0}]},
     {"say": "Look closer: small icons under the stars reportedly show what police know about you.",
      "claim": "fact", "facts": ["wanted-heat-icons"], "evidence": ["E4"], "teaches": "the icons are what police know",
      "beats": [{"beat": "card", "at": 100.0, "zoom": "stars", "label": {"text": "What police know", "style": "info"}},
                {"beat": "card", "at": 12.0, "zoom": "tip", "label": {"text": "Game tip", "style": "grey"}}]},
     {"say": "After a respray, the car icon is gone.", "claim": "on_screen", "evidence": ["E9"],
      "teaches": "a respray clears the car icon",
      "beats": [{"beat": "full", "at": 124.0},
                {"beat": "compare", "top": {"at": 102.0, "zoom": "stars", "label": "Before"},
                                    "bottom": {"at": 149.5, "zoom": "stars", "label": "After respray"}}]},
     {"...": "..."},
     {"say": "Then the heat clears, and the stars disappear.", "claim": "on_screen", "evidence": ["E14"],
      "teaches": "the stars go once nothing is known", "payoff": true,
      "beats": [{"beat": "card", "at": 391.5, "zoom": "stars", "label": {"text": "Heat cleared", "style": "info"}}]},
     {"say": "So next time you're wanted, before you run, ask yourself", "claim": "on_screen", "evidence": ["E2"],
      "teaches": "the loop back to the question", "loop": true, "beats": [{"beat": "full", "at": 145.0}]}]}
]}
```

**Hooks.** 3 options in English, 3-6 words, readable in one second, true to the footage, no emoji; `star` is the best
one. The starred hook is shown as the **opening card from frame 0** (`open_card` sets it by hand, `"LINE 1|LINE 2"`);
line 1 is the spoken hook (a question or a claim the Short pays off).

**Lines** (code refuses what breaks these; see truth.py):
1. **Every line teaches or asks; no play-by-play** ("I drive ...", "Then I ..."). Each is `"claim": "on_screen"`
   (cite `evidence` shown while it is said, and `teaches`), `"fact"` (`facts` from facts.json: `confirmed`, or
   `reported` in its `safe_wording`; and `teaches`) or `"ask"` (a question with `answered_by`).
2. **Words match the picture**: stars, police, a crash, icons ... are named only with evidence that shows them, on
   screen while the line is said. A label in `dropped_labels` is not on screen: never name it.
3. **One moment, in time order**: beats go forward through the recording (a compare may show an earlier "before").
4. **Exactly one payoff** (`"payoff": true`), 45-85 % into the Short; the **last line** has `"loop": true`, no full
   stop, and runs into line 1. No spoken call to action (a silent subscribe pill ends every Short).
5. **No story spoilers, no names but Jason and Lucia, no dialogue quotes.** Mission footage needs `"mission_ok"`.
6. **F2:** never say why something happened unless the change and the result are both cited, in that order.

**Beats** (on every line, 1-3 each, so a new picture every 1.5-2.5 s, full screen and cards in turn):
- `{"beat": "full", "at": <recording s>}`: the footage on the whole screen (the HUD corners are cut off); `"focus"`
  0-1 moves the crop across.
- `{"beat": "card", "at": s, "zoom": "stars" | "tip" | "minimap" | [x, y, w, h], "label": ...}`: a framed card; with
  `zoom`, a close-up of that part of the picture, readable on a phone (the star row with the heat icons under it, the
  game's tip box at the top left, the minimap, or a box you read off a `frame` picture). Use it for every proof.
- `{"beat": "compare", "top": {"at", "zoom", "label"}, "bottom": {...}}`: before and after, stacked.
- `{"beat": "banner", "at": s, "text": "1-3 words"}`: a pill over full-screen footage.
- `"at"` is a second of the pick's own recording, near its window. Every beat's footage must be clean in the index
  and not shown by an earlier Short (code checks). A beat with no `beats` at all gets footage at its evidence.
- **Action Shorts** (format A1): 3-5 short lines, about 50-80 words: the setup (what is happening), the moment (a
  `full` beat at the impact, the game's sound is in), the payoff (a close card or a banner on the result), the loop.

**Length:** narrated 25-39 s (about 95-115 words at speed 1.0), action 16-32 s. Title 32-46 characters with 1-2
words in ALL CAPS (Title Case is fine), description exactly 2 sentences, 3-5 hashtags (`#GTA6`, `#GTAVI`,
`#Shorts`; a `--game gta5` trial uses `#GTA5`).

Leave a pick out of `shorts.json` if it can't keep these rules; say why in the report.

## 4. Render

```powershell
.\.venv-tts\Scripts\python.exe launch.py render --run "<run folder>"
```

It checks shorts.json (truth, story and timing rules), cuts the close-ups, renders every Short (engine v3: the hook
card, am_puck, yellow captions, music and sound effects, the subscribe pill and the loop; true peak under -1.0 dBTP),
logs the footage as used and writes `review.html`. A Short marked **FAILED** lists its problems: fix only that Short
and render it alone with `--short N`. At most 3 tries per Short; after that leave it FAILED and go on.

## 5. Look at what you made

For each READY Short make a sheet and look at it with the Read tool, then at 3-4 full frames (`-ss <t>`):

```powershell
ffmpeg -v error -y -i "<short folder>\short.mp4" -vf "fps=2,scale=216:-2,tile=10x8" -frames:v 1 "<short folder>\check.jpg"
```

Ask: would a stranger understand what this shows and why it matters? Is the hook readable at frame 0, does every
close-up show what its line names, is the payoff visible? No phone, menu, map, loading screen, gore or names on
screen. Fix what a re-render can fix; otherwise say so in the report.

## 6. Report, in plain English

1. HUD first: if `candidates.json` `hud.mode` isn't `hud`, say so at the very top, with why.
2. The review page path: `<run folder>\review.html`.
3. For each Short: READY or FAILED, the type and format, the 3 hooks with the starred one marked, what it teaches or
   shows, the length, the title, its flags.
4. Candidates left out and why (`candidates.json` `rejected`, and picks you didn't write).
5. How long prepare and render took. Ask nothing.
