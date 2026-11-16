---
description: Turn a topic into a finished Short built from the owner's own gameplay footage (script, footage picks, render, upload text)
argument-hint: <topic>
---

Make one Short from the owner's **own gameplay footage** about this topic: **$ARGUMENTS**

The owner's only job is reviewing. Do every step yourself, in order, and stop only where a step says to.
Work in `C:\AI\gta-clipper` on Windows. Use `.\.venv-tts\Scripts\python.exe` for `footage_short.py`. Never upload anything.

## 1. See what footage there is

```powershell
.\.venv\Scripts\python.exe library.py list
.\.venv\Scripts\python.exe library.py shotlist
.\.venv\Scripts\python.exe library.py find --stars-rise
```

The Short can only show what the library has. A shot the table marks "nothing yet" will fall back to a Rockstar
picture, so write the script round the footage that exists. Never run anything on `C:\AI\gta\heldout`.

Which game: a Short about GTA 6 gameplay uses `"profile": "gta6"` footage only; a GTA 5 Short uses `"gta5"`. Never
show GTA 5 footage as if it were GTA 6.

## 2. Read the facts

Read `facts.json`. **Every spoken or on-screen claim about GTA 6 must come from a fact in it whose status is
`confirmed` or `reported`**, exactly as in `/new-short` (step 2 there): never an `unverified` fact, never a leak,
a `reported` fact only in its `safe_wording`, every number from a listed fact. A fact with `"time_sensitive": true`
that was checked more than 3 days ago stops the render: show the owner the list, never pass `--allow-stale`.
What the owner did in their own footage ("I got 3 stars") is not a claim about GTA 6 and needs no fact.

## 3. Write the script

Write it to `C:\AI\gta\shorts\_drafts\<slug>.json`. It is a `make_short.py` script (title, description, hashtags,
voice, lines) with footage beats:

```json
{
  "topic": "the topic as given",
  "title": "32-46 characters, 1-2 words in ALL CAPS",
  "description": "One or two plain sentences. No hashtags here.",
  "hashtags": ["#GTA5", "#GTA6", "#Shorts"],
  "voice": "am_puck", "speed": 1.05, "music": "auto", "sfx": true,
  "end_pill": "@NeonGetaway • Subscribe",
  "footage": {"game_audio": true, "profile": "gta5"},
  "lines": [
    {"say": "What the voice says.", "facts": [],
     "beats": [{"beat": "full", "shot": {"activity": ["chase"], "min_stars": 2}},
               {"beat": "card", "card_aspect": "16:9", "shot": {"stars_rise": true}},
               {"beat": "card", "card": "13 YEARS"}],
     "green": [], "red": []}
  ]
}
```

- **`"shot"`** asks the library for footage. It is a `library.py` query (`activity`, `min_stars`, `max_stars`,
  `min_len`, `vehicle`, `police`, `text`, `after`, `video`, `stars_rise`) or a shot-list id such as
  `"police-chase/long-chase"`.
- **`"beat": "full"`** fills the screen with the middle of the gameplay (the HUD is cut off). **`"beat": "card"`**
  floats the gameplay as a framed card over a blurred copy, so the minimap and the wanted stars stay in view: use it
  with `"card_aspect": "16:9"` for a beat about the stars. `"banner"` puts a 1-3 word pill over full-screen footage.
- A beat about wanted stars (`min_stars` 1 or more, or `"stars_rise": true`) gets a moment where the stars go up.
- Add `"query": "police car"` to a shot beat to choose its Rockstar fallback picture yourself.
- A GTA 6 tie-in line may use a normal picture beat (`"query": "..."`, as in `/new-short`) and must list its facts.
- Text cards, `count` cards and labels work as in every explainer Short.

**The words**: 20-35 s is about 65-105 spoken words. A hook in the first 2 seconds that is literally true, then the
footage carries it; a last line that loops back into the first. Numbers as digits.

**The beats**: 2 beats a line keeps a new picture every 1.5-2.5 s. Don't ask for the same thing five times: the tool
avoids repeating footage, but a library with one chase can only show it once well.

## 4. Check, then render

```powershell
.\.venv-tts\Scripts\python.exe footage_short.py C:\AI\gta\shorts\_drafts\<slug>.json --check
```

It checks the script, makes the voice once to learn each beat's length, picks the footage and writes `picks.html`.
Fix what it lists (too long or too short: it says how many words to add or cut) and run it again. Then render:

```powershell
.\.venv-tts\Scripts\python.exe footage_short.py C:\AI\gta\shorts\_drafts\<slug>.json
```

**NOT READY** names the reason: the length, a beat that never got on screen, or footage that is not clean.

## 5. Look at the picks yourself

Open `picks.html` from the Short's folder (and read `picks.json`). For every beat: does the thumbnail show what the
voice says over it? A beat marked ROCKSTAR FALLBACK found no footage: change its `shot`, or accept the picture.
If a pick doesn't fit, narrow that beat's query (`text`, `video`, `min_len`, `vehicle`) and render again. Read
`claims.txt` once against the script.

## 6. Report

In plain, simple English:

1. The paths: `short.mp4`, `picks.html`, `upload.txt`, `claims.txt`.
2. The title, the length, and how many beats came from the owner's footage and how many fell back to Rockstar.
3. For each footage beat, one line: recording, time, and why it was picked.
4. Anything you were not sure about, and any line that uses a `reported` fact.
