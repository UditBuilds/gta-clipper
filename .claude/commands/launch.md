---
description: Launch night - turn the owner's new gameplay recordings into 3 finished Shorts with a review page, no questions asked
argument-hint: "[nothing on launch night] or --dry-run --game gta5 --videos <files>"
---

Make 3 Shorts from the owner's **new gameplay recordings**, start to finish, and **ask the owner nothing**. The owner
reviews and uploads them later on `review.html`; you never upload anything. Extra arguments: **$ARGUMENTS** (empty on
launch night; a trial uses `--dry-run --game gta5 --videos ...`).

Work in `C:\AI\gta-clipper` (Windows, PowerShell). `prepare` runs with `.\.venv\Scripts\python.exe`, `render` with
`.\.venv-tts\Scripts\python.exe`. Never touch `C:\AI\gta\heldout`. If a command stops with `ERROR:`, tell the owner the
error in one plain sentence and what to do, and stop: that is the only time you stop early.

## 1. Prepare (up to about 90 minutes)

```powershell
.\.venv\Scripts\python.exe launch.py prepare $ARGUMENTS
```

Run it **in the background** (the shell tool's background option): it takes a long time (about 75 minutes for 2 hours
of footage, most of it the vision model). Don't poll it or sleep; you are told when it ends. It finds the new
recordings, calibrates the HUD (or goes NO-HUD), indexes them, mines the moments, runs the safety checks and picks 3.
Its last lines name the run folder. Everything you need is in `<run folder>\candidates.json`.

## 2. Look at each pick

For every entry of `picks` in `candidates.json`:

- `type` is `action` (an ACTION CLIP: game sound, music, a burned-in hook, no voice) or `narrated` (the am_puck voice
  over the footage). It was decided in code; **keep it**.
- `window` is the footage in seconds of the recording `video`. `evidence` lists what the footage shows, each with an
  `id` (E1, E2 ...), a `kind`, a `value`, its times (`from_s`, `to_s`) and the `numbers` it supports.
- Look at the frames: open `sheet` (a frame every 2 s, each with its time in the recording) with the Read tool. For an
  ACTION CLIP also open every `peak_options[].strip` (a frame every 0.2 s round a crash, explosion, stunt or star rise).
- `flags` and `mode` (`nohud` = NO-HUD MODE: never mention stars or the wanted level).

## 3. Write `<run folder>\shorts.json`

```json
{"shorts": [
  {"short": 1, "moment": "M3", "type": "action",
   "hooks": ["first option", "second option", "third option"], "star": 1,
   "peak": {"evidence": "E5", "at_s": 412.6},
   "title": "...", "description": "Sentence one. Sentence two.", "hashtags": ["#GTA6", "#GTAVI", "#Shorts"]},
  {"short": 2, "moment": "M1", "type": "narrated",
   "hooks": ["The cops are right behind me", "...", "..."], "star": 0,
   "title": "...", "description": "...", "hashtags": ["#GTA6", "#GTAVI", "#Shorts"],
   "lines": [
     {"say": "The cops are right behind me", "claim": "on_screen", "evidence": ["E2"], "facts": []},
     {"say": "Then my first wanted star shows up.", "claim": "on_screen", "evidence": ["E7"], "facts": []},
     {"say": "In GTA 6 the stars reportedly turn hollow when the cops lose sight of you.", "claim": "fact",
      "evidence": [], "facts": ["some-fact-id"]}]}
]}
```

**Hooks (every Short).** Write 3 options in English, 3-6 words each, readable in one second, teasing the moment
without spoiling it, literally true of the footage, no emoji, in sentence case. Pick the best yourself: `star` is its
index (0, 1 or 2). On an ACTION CLIP the starred hook is burned in for the first 3 s; on a NARRATED Short it is
**spoken as line 1, word for word**.

**Title, description, hashtags.** Title 32-46 characters with 1 or 2 words in ALL CAPS (GTA, PS5 don't count),
sentence case otherwise. Description: **exactly 2 sentences**, no hashtags. 3-5 hashtags: `#GTA6`, `#GTAVI`, `#Shorts`
(+1-2 fitting ones). A `--game gta5` trial uses `#GTA5` instead of the GTA 6 tags and never calls it GTA 6.

**ACTION CLIP peak.** The cold open and zoom need a peak, and only from evidence: choose one of `peak_options`, look at
its strip, and set `at_s` to the recording time printed on the frame where the hit happens (the impact, the explosion,
the landing, or the star rise's own `at_s`). Unsure, or no `peak_options`: `"peak": null` (no cold open, no zoom).

**NARRATED lines.** 20-35 s of voice is about 55-95 words, and it must fit the window (`window[1] - window[0]`
seconds). The footage plays on in real time under the voice from `window[0]`, so a line is on screen at about
`window[0] + (words before it / all words) x voice length`: **put the lines in the time order of their evidence**, and
each line must cite evidence shown while it is spoken. Every line has:
- `"claim": "on_screen"` (what the footage shows) with `"evidence"` ids, or
- `"claim": "fact"` (how the game works) with `"facts"` from `facts.json`: `confirmed`, or `reported` in its exact
  `safe_wording`, never `unverified`, never one with `time_sensitive: true` checked more than 3 days ago. A GTA 5
  trial uses no GTA 6 facts. Prefer describing the moment over explaining the game.

**What the code refuses** (render lists every refusal; fix it and render again):
- a word without its evidence: star/stars/wanted need a `stars` item; cop/cops/police need `police`, a chase or stars;
  crash/smash/wreck need a crash `event`; explode/explosion/blast an explosion; jump/flip/airborne/stunt a stunt;
  chase/escape/getaway a chase, stars or police; shoot/gun/shootout a shootout; fly/helicopter/plane flying; boat;
  swim. In NO-HUD mode no star word at all;
- a number that is not in the cited evidence's `numbers` or the line's facts ("two stars" needs a 2);
- a claim about how the game works ("you can", "the game", "cops will", "lets you", "every time", "if you", "system")
  in a line with no fact. "GTA 6" and "GTA VI" as the game's name are always fine;
- any name but Jason and Lucia (a capitalised word mid-sentence), and story words: story, plot, ending, mission,
  dies, betray, twist, boss, cutscene.

## 4. Render

```powershell
.\.venv-tts\Scripts\python.exe launch.py render --run "<run folder>"
```

It checks shorts.json, renders every Short (3-6 minutes for 3), limits the sound under -1.0 dBTP, logs the footage
as used and writes `review.html`. A Short marked **FAILED** lists its problems: fix only that Short in shorts.json and
render it alone with `--short N`. Try at most 3 times per Short; after that leave it FAILED (the review page shows
why) and go on.

## 5. Look at what you made

For each READY Short make one sheet of the rendered video and look at it with the Read tool:

```powershell
ffmpeg -v error -y -i "<short folder>\short.mp4" -vf "fps=1/2,scale=270:-2,tile=6x3" -frames:v 1 "<short folder>\check.jpg"
```

Check: the hook is readable, no phone, menu, map or loading screen, nothing gory in the first seconds, and the
footage matches the words. If something is wrong that a re-render can fix (a wrong hook or cold open: the commands are
on the review page), fix it; otherwise leave it and say so in the report.

## 6. Report, in plain English

1. The review page path, first: `<run folder>\review.html`.
2. For each Short: READY or FAILED, the type, the starred hook, the length, and its flags in a few words.
3. NO-HUD mode, if it happened, and why (from `candidates.json`, `calibration` and `recordings[].hud_check`).
4. How long prepare and render took.
Ask nothing. The owner watches, fixes with the commands on the page if needed, and uploads.
