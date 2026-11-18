---
description: Launch night - turn the owner's new gameplay recordings into 1-3 finished Shorts that each teach one thing, with a review page, no questions asked
argument-hint: "[nothing on launch night] or --dry-run --game gta5 --videos <files>"
---

Make **1 to 3** Shorts from the owner's **new gameplay recordings**, start to finish, and **ask the owner nothing**.
The owner reviews and uploads them later on `review.html`; you never upload anything. Extra arguments: **$ARGUMENTS**
(empty on launch night; a trial uses `--dry-run --game gta5 --videos ...`).

**The point of every Short (the owner's rule):** it gives a stranger something: a new fact about GTA 6, a "wait,
really?", or a clear payoff. The footage is the **proof**, not the content. Fewer good Shorts beat 3 weak ones: if a
pick can't make a Short that teaches one thing, leave it out.

Work in `C:\AI\gta-clipper` (Windows, PowerShell). `prepare` runs with `.\.venv\Scripts\python.exe`, `render` and
`frame` with `.\.venv-tts\Scripts\python.exe`. Never touch `C:\AI\gta\heldout`. If a command stops with `ERROR:`,
tell the owner the error in one plain sentence and what to do, and stop: that is the only time you stop early.

## 1. Prepare (up to about 90 minutes)

```powershell
.\.venv\Scripts\python.exe launch.py prepare $ARGUMENTS
```

Run it **in the background**; don't poll it or sleep, you are told when it ends. It finds the new recordings,
calibrates the HUD (or goes NO-HUD), indexes them, mines moments, runs the safety checks and the **quality gate**
(a moment with nothing to teach and no payoff is skipped), and picks up to 3. Its last lines name the run folder.
Everything you need is in `<run folder>\candidates.json`.

## 2. Look at each pick

For every entry of `picks`:
- `type`: `narrated` (the am_puck voice tells a story over ONE moment) or `action` (game sound, a burned-in hook, no
  voice). Decided in code; keep it.
- `format_options` (narrated): the formats this moment can teach, each with `why`. Pick one.
- `point.action_payoff` (action): the crash, explosion, stunt or star rise it pays off with.
- `window`, `anchor_s` (the main instant), `evidence` (E1, E2 ... with times and numbers; R1 / C1 is an F1's other
  side), `flags`, `mode` (`nohud` = never mention stars), `mission` (true = mission footage, see 3).
- Look at the frames: `sheet` (a frame every 2 s) and, for an action clip, every `peak_options[].strip`.

## 3. Write `<run folder>\shorts.json`

### The formats (narrated): each teaches ONE thing

| Format | The Short | Its hook | Its payoff |
|---|---|---|---|
| **F1** GTA 5 vs GTA 6 | what changed between the games, shown side by side | "GTA 6 changed ___" | the visible difference |
| **F2** Test | can you ___? I try it; the HUD gives the answer | "Can you lose 4 stars ___?" | the answer, on screen |
| **F3** 3 things | three details this moment shows, counted 1, 2, 3 | "3 things in my first chase" | the third, best one |
| **F4** First look | what ___ looks like in GTA 6 | "This is GTA 6's ___" | the best view of it |

### The rules for every narrated Short (code refuses what breaks them)

1. **One moment, in time order:** setup, then action, then result. The footage is the pick's window only and plays
   forward (it may skip ahead, never back). **Write the lines in the time order of their evidence.**
2. **Context in the first 2 s:** line 1 is the starred hook, spoken word for word: a **question** (`"claim": "ask"`)
   or a **claim** the Short pays off. The Short opens on the situation before the action (code checks the first 2 s
   are before `anchor_s`).
3. **Every line teaches or asks. No play-by-play.** Never "I drive ...", "So I cut across ...", "Then I ...". Each
   line is one of:
   - `"claim": "on_screen"` with `"evidence"` ids and `"teaches"`: what the viewer learns from it, in a few words
     ("the second star comes 10 s after the first");
   - `"claim": "fact"` with `"facts"` from facts.json (`confirmed`, or `reported` in its `safe_wording`) and
     `"teaches"`;
   - `"claim": "ask"`: a question ending in `?`, with `"answered_by"`: the number of the later line that answers it.
   A first-person line passes only when it states a measured result its evidence shows ("I got 4 stars in 2 s").
4. **Words match the picture:** the thing a line names (stars, police, a crash, anything from a `seen` item) must be
   on screen **within 0.5 s of the word**. Code places the footage to make that true, or refuses the line: then move
   the line, or cite what is on screen at that point.
5. **Overlays explain** (`"pops"` on a line, 1.5-2 s apart, up to 3 s with nothing meaningful; never filler):
   - `{"kind": "pip", "on": "stars", "hud": "stars"}`: the HUD corner with the wanted stars ringed. Only on a star
     word of a line that cites stars.
   - `{"kind": "circle", "on": "<word>", "evidence": "E5", "box": [x, y, w, h], "at": <recording s>}`: a red ring on
     the exact thing the line names; the `on` word must name that evidence. Get the box from a gridded frame:
     `.\.venv-tts\Scripts\python.exe launch.py frame --run "<run folder>" --short N --at 260.0 261.0` and read x, y,
     w, h in the frame's own pixels (the yellow numbers) with the Read tool. Not sure where it is? No circle.
   - `{"kind": "stat", "on": "<word>", "text": "4 WANTED STARS", "evidence": "E5"}`: 1-4 words that make sense on
     their own: a number and a thing, or a thing the evidence shows. Never a fragment like "KEEPS GOING".
   - `{"kind": "badge", "on": "<word>", "text": "1"}`: F3's count, 1, 2, 3.
   - F1 only: `{"kind": "pip", "on": "<word>"}` on a line that cites R1 or C1 shows the other game's side.
6. **The break** (Shorts over 30 s, optional, after a line in the middle): `{"after_line": N, "kind": "freeze",
   "evidence": "E7", "label": "4 WANTED STARS"}` freezes on that evidence with a ring and the label naming it; F1 may
   use `{"after_line": N, "kind": "split"}` (this game on top, the other below). **Never joke text.**
7. **The payoff** (`"payoff": true` on exactly one line) starts 45-75 % into the Short. The **last line** has
   `"loop": true`, has no full stop and runs into line 1 ("... and that is how fast" + "the cops come"). No spoken
   call to action: a silent subscribe pill ends every Short.
8. **F2:** never say why something happened ("so they lost me", "because I changed cars") unless both the change and
   the result are cited, in that order. Otherwise say what happened, not why.
9. **Mission footage** (`"mission": true`): only when the point is clear without the story; say why in
   `"mission_ok"`. Never quote dialogue or name story things (truth.py's spoiler words).
10. **Length 25-40 s:** the voice says about 3.8 words a second, so about 90-145 words; aim for 100-120.

Also on every narrated Short: `"format"`, `"format_why"` (one sentence), and optionally `"focus"` (0-1, where the
action is across the picture; default 0.5).

### Every Short (both types)

**Hooks.** 3 options in English, 3-6 words, readable in one second, true to the footage, no emoji, sentence case;
`star` is the best one's index. A narrated hook is line 1 word for word; an action clip's is burned in for 3 s.
**Title, description, hashtags** as before: title 32-46 characters with 1-2 words in ALL CAPS, description exactly
2 sentences, 3-5 hashtags (`#GTA6`, `#GTAVI`, `#Shorts`; a `--game gta5` trial uses `#GTA5` and never says GTA 6
about its own footage). **Action clip peak:** one of `peak_options`, `at_s` read off its strip, or `null`.
**A later start** (`"start_s"`) as before.

```json
{"shorts": [
  {"short": 1, "moment": "M3", "type": "narrated", "format": "F2",
   "format_why": "the stars rise to 3 and are all gone 40 s later: the HUD answers the question",
   "hooks": ["Can you lose 3 stars here?", "...", "..."], "star": 0,
   "title": "...", "description": "Sentence one. Sentence two.", "hashtags": ["#GTA6", "#GTAVI", "#Shorts"],
   "lines": [
     {"say": "Can you lose 3 stars here?", "claim": "ask", "evidence": [], "answered_by": 4},
     {"say": "Three wanted stars, and two police cars right behind me.", "claim": "on_screen",
      "evidence": ["E5", "E6"], "teaches": "how much heat 3 stars bring",
      "pops": [{"kind": "pip", "on": "stars", "hud": "stars"}]},
     {"...": "..."},
     {"say": "Forty seconds later the stars are gone.", "claim": "on_screen", "evidence": ["E12"],
      "teaches": "3 stars can be lost in 40 s", "payoff": true},
     {"say": "So yes, you", "claim": "on_screen", "evidence": ["E12"], "teaches": "the answer, looping to the question",
      "loop": true}]}
]}
```

Leave a pick out of `shorts.json` if it can't keep these rules; say so in the report.

## 4. Render

```powershell
.\.venv-tts\Scripts\python.exe launch.py render --run "<run folder>"
```

It checks shorts.json (truth, story and timing rules), renders every Short (edit engine v2: slow background cuts,
the pop-ins, yellow captions, am_puck, music and game sound, a true-peak limit under -1.0 dBTP), logs the footage as
used and writes `review.html`. A Short marked **FAILED** lists its problems: fix only that Short and render it alone
with `--short N`. At most 3 tries per Short; after that leave it FAILED and go on.

## 5. Look at what you made

For each READY Short make a sheet and look at it with the Read tool:

```powershell
ffmpeg -v error -y -i "<short folder>\short.mp4" -vf "fps=2,scale=270:-2,tile=8x10" -frames:v 1 "<short folder>\check.jpg"
```

Ask: would a stranger understand what this shows and why it matters? Is the hook clear, does each ring sit on the
thing the line names, is the payoff visible? No phone, menu, map, loading screen or gore early. Fix what a re-render
can fix; otherwise say so in the report.

## 6. Report, in plain English

1. The review page path first: `<run folder>\review.html`.
2. For each Short: READY or FAILED, the type and format, the hook, what it teaches, the length, its flags.
3. Picks left out and why (the quality gate's reasons are in `candidates.json` `rejected`).
4. NO-HUD mode, if it happened, and why. How long prepare and render took.
Ask nothing.
