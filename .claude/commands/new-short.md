---
description: Turn a topic into a finished, checked GTA 6 explainer Short (script, pictures, render, upload text)
argument-hint: <topic>
---

Make one explainer Short about this topic: **$ARGUMENTS**

The owner's only job is reviewing. Do every step yourself, in order, and stop only where a step says to.
Work in `C:\AI\gta-clipper` on Windows. Use `.\.venv-tts\Scripts\python.exe` for `make_short.py`. Never upload anything.

## 1. Score the topic

Give it a score from 1 to 5 for **how much it matters to almost every GTA 6 player on day one**, with one line why.

- 5: every player needs it before or on launch day (the release date, pre-load, unlock time, download size).
- 3: most players will care (the map, the main characters).
- 1: a small group only (PC players, Collector's Edition buyers, one console's bundle).

If the score is under 3, tell the owner in one line and carry on: `make_short.py` will warn as well.

## 2. Read the facts

Read `facts.json`. **Every claim in the Short must come from a fact in it whose status is `confirmed` or `reported`.**

- Never use an `unverified` fact. Never use something you know that is not in the file. Never use a leak.
- A `reported` fact must be said and shown in its `safe_wording` (or a wording in its `also_ok`), such as
  "over 200 GB", never the exact figure.
- Every number said or shown on a line must be in the wording of a fact that line lists.
- Never change a fact's status, and never add a fact as anything but `unverified`.
- **Stale facts.** A fact with `"time_sensitive": true` can change from day to day (launch-week dates, times and
  sizes). If the script would use one whose `checked_on` is more than 3 days before today, **do not render.**
  `make_short.py` refuses and lists those facts. Stop and show the owner that list in plain English. Never pass
  `--allow-stale` yourself: that flag is the owner's. If the owner asks you to check the facts again, open each
  fact's source links; if the fact still holds, set its `checked_on` to today; if it changed, do not edit the claim:
  tell the owner what the source says now.
- If the checked facts can't carry the topic for 34-39 s, **stop and tell the owner which facts are missing.** Offer
  to add them to `facts.json` as `unverified` for the owner to check. Do not pad with unchecked claims.

## 3. Write the script

Write it to `C:\AI\gta\shorts\_drafts\<slug>.json`. It is an `explainer.py` script (see "Explainer Shorts" and "Topic
to Short" in `README.md`) with these parts:

```json
{
  "topic": "the topic as given",
  "topic_score": {"score": 5, "why": "one line"},
  "title": "32-46 characters, see below",
  "description": "One or two plain sentences. No hashtags here.",
  "hashtags": ["#GTA6", "#GTAVI", "#Shorts"],
  "voice": "am_puck", "speed": 1.1, "music": "auto", "sfx": true,
  "end_pill": "@NeonGetaway • Subscribe",
  "lines": [
    {"say": "What the voice says.", "facts": ["release-date"],
     "beats": [{"beat": "full", "query": "city skyline night", "region": "Vice City"},
               {"beat": "count", "card": "19 NOV"}],
     "green": [], "red": []}
  ]
}
```

**The words** (34-39 s, about 95-105 spoken words):

1. Seconds 0-2: a claim that is true and surprising. It must be literally true. No bait-and-switch, no "just kidding".
2. Then "let me explain" or "here's why".
3. By second 3: visual proof, readable in under 1 second (a `count` card, a `snippet`, a labelled picture).
4. A twist or an escalation.
5. A payoff: what the player should do or now knows.
6. A last line that loops back into the first line.

Write numbers as digits. Write "70 dollars", not "$70". Every line has `"facts"`: the IDs it states, or `[]`.

**The pictures** (4-6 visual changes every 10 s, mixing full-screen beats and cards):

- Ask for pictures by what they show: `"query": "swamp airboat"`, with `"region"` when the place matters. Use words
  the tags use: things you can see (helicopter, yacht, skyline, alligator, police car, Jason, Lucia).
- Use a `"file"` only when one exact picture is needed. A `lockon` needs one for its `box`, and so does a shot with
  `"marks"`: a mark sits on a spot of one picture, and the check refuses a mark that misses the picked picture.
- Pictures with logos or big text are left out unless the shot says `"text_ok": true`. Use that only when the
  logo or text is the point (an editions topic, the cover art).
- A video is picked only with `"media": "video"`. Rockstar's character loops last 1-1.5 s, so give them short shots.
- A label that names a region must sit on a picture from that region. Don't name a region on a `lockon`.
- Beat types: `full`, `lockon`, `card`, `compare`, `snippet`, `count`, `banner`. A shot over 2.5 s warns.

**The title**: 32-46 characters. ALL CAPS on only 1 or 2 words (GTA and PS5 don't count). It may end with `...` or
`!?`. No hashtags. It must be true, like the hook. 3-5 hashtags go in `"hashtags"`.

## 4. Check, then render

```powershell
.\.venv-tts\Scripts\python.exe make_short.py C:\AI\gta\shorts\_drafts\<slug>.json --check
```

Fix every problem it lists and run it again until it passes. A refused picture names the closest match: change the
query, or the beat, rather than passing `--allow-weak`. If it lists facts that were "last checked more than 3 days
ago", stop there and tell the owner (see "Stale facts" in step 2). Then render:

```powershell
.\.venv-tts\Scripts\python.exe make_short.py C:\AI\gta\shorts\_drafts\<slug>.json
```

- **NOT READY because of the length:** cut or add words (or set `speed` between 1.0 and 1.1) and render again.
- **NOT READY because of the pacing:** add or split shots where it names a gap, and render again.

## 5. Look at the picks yourself

Open `picks.jpg` from the Short's folder and look at every row. The picture on the left must show what the line says
and what the words on it say. If one doesn't fit, change that shot's query (or name a file) and render again. Read
`claims.txt` once against the script: each line must say only what its facts say.

## 6. Report

In plain, simple English:

1. The topic score and its one line.
2. The paths: `short.mp4`, `picks.jpg`, `upload.txt`, `claims.txt`.
3. The title, and the pacing check (the lines `make_short.py` printed).
4. Anything you were not sure about, and any fact you wanted but could not use.
5. Any line that uses a `reported` fact, so the owner can hear that the voice says it is only reported.
