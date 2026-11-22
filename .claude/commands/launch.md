---
description: Launch night - turn the owner's new gameplay recordings into finished Shorts, 3 when 3 moments meet the payoff bar and fewer when not (topic explainers and action clips, every one voiced, captioned and edited like the owner's posted sample), with a review page, no questions asked
argument-hint: "[--count N] or --dry-run --game gta5 --videos <files>"
---

Make **3** Shorts (or `--count N`) from the owner's **new gameplay recordings**, start to finish, and **ask
the owner nothing**. **Fewer is right when fewer moments have a clear payoff** (the payoff bar below, the owner, Nov 22):
never fill a night with a weak clip. If not one moment qualifies, `prepare` stops with an `ERROR:` saying so. The owner reviews and uploads them on `review.html`; you never upload anything. Extra arguments:
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
swearing in the subtitles, and **never a child near guns, shooting, a body or blood, or a body with blood**, anywhere
in it (the Nov 20 dock shootout); and its **labels are checked against what the vision model sees in its own frames** (a
"crash", "shootout", "police" or "jump" that isn't seen is dropped). Mission dialogue costs points. Then the picks
(PR 29): **the clearest payoff first** (`payoff_strength`: 3 an impact the frames confirmed, 2 a HUD change with a
visible cause, 1 a HUD change alone or action with no single event, 0 quiet), then points; **a payoff below 2/3 is never
picked, by any route** (the owner, Nov 22: a star rise with no cause shown, or plain driving, is not a Short; there are
fewer picks, or none, rather than a weak one, and each moment left out says so in `candidates.json`); **no
near-duplicates** (two picks of one recording must be a different kind of moment or 10+ min apart) and **at most 2
picks a recording** unless nothing else qualifies; **both an action clip and a topic** when the pool has a moment at
2/3 or more of each; a moment that repeats a topic already posted (`posted_topics.json`: the owner's list) costs
points, "wanted stars" / "stars appear" and "crime reported" the most; **no Short of the same type as one of the last
2 posted** (no crash right after a crash, whatever the label says: "truck crash" is a crash) while a candidate of
another type meets the 2/3 bar; when none does, one such Short may come, not two, and its `pick_why` says so; a moment
that **already failed the shot check** at an earlier render is left out before the pool. Each pick's `pick_why` says why it beat the others. Everything is in `<run folder>\candidates.json`.

## 2. Look at each pick

For every entry of `picks`:
- `type`: `narrated` (a topic explainer, 15-39 s) or `action` (15-25 s). Decided in code; keep it.
- `topic` (a topic candidate): what the footage proves (`id`, `why`, `at_s`) and the facts.json ids it may use
  (`facts`). `format_options`: the formats this moment can teach (T1 a topic, F1-F4 as before); an action pick is A1.
- `payoff_strength` and `pick_why` (why this moment won), `repeats` (posted topics it repeats).
- `window`, `anchor_s`, `evidence` (E1, E2 ...: what the footage shows, when, and where that comes from: the HUD's stars,
  `heat` (the icons under the stars) and `star_look` (red, red outline, white outline), activities, events the frames
  confirmed, the model's lines), `flags`, `mode`, `mission`, `dropped_labels` (what the frames did not confirm).
- **Look at the frames**: `sheet` (a frame every 2 s). For a close look at an exact second:
  `.\.venv-tts\Scripts\python.exe launch.py frame --run "<run folder>" --short N --at 260.0 261.0`.
- `C:\AI\gta\facts\own` and `.\.venv\Scripts\python.exe facts.py own`: facts from the owner's own footage (the game's
  own words on screen). Only **confirmed** ones may be used.

## 3. Write `<run folder>\shorts.json` (engine v4: payoff first)

The owner's editing rules (Nov 20, after the part3 Shorts: a skipped crash, repeated scenes, a wobbling picture, a
blurry first frame, a slow AI-sounding voice). Code refuses what breaks them (cutplan.py, truth.py, steady.py):

1. **Payoff first.** Find the one moment the Short is for and write it down first: `"payoff": {"from": s, "to": s,
   "what": "what happens, in plain words"}` (0.6-8 s of the recording: the whole hit, the whole change). It plays
   **whole, at real speed, inside one shot**: never cut, never skipped. An action payoff must hold the loudest
   moment of the footage (the hit itself; the game's camera switch often comes a second later). **No clear payoff,
   no Short**: leave the pick out and say why. The report names every Short's payoff.
2. **Shots are spans, picked before the words**: `"shots": [{"from": s, "to": s}, ...]`, in time order, each at
   least 0.8 s, within 60 s of the pick's window: the setup (at least 1.5 s before the payoff), the payoff's shot,
   then the result. **No second twice**: not enough unique footage makes a shorter Short (**15 s at least**; action
   15-25 s, narrated 15-39 s), never a repeat or a loop. End each shot where something happens or right after a line.
   One fixed 9:16 window per shot (`"focus"` 0-1 across the picture, 0.5 by default); nothing pans or zooms; the
   picture is steadied where that measures calmer.
3. **The first frame is clear**: the subject visible, nothing blurry or in front of it (a frame that isn't moves the
   start later by itself, up to 1.5 s, or fails). A hook that names the HUD (stars, icons) opens on its close-up:
   `"inset": {"zoom": "stars", "box": "icons", "start": 0.0, "length": 1.8}` on shot 1.
4. **The voice goes into the picture.** Each line has `"at"`: the recording second where it starts. It must end
   before its shot's cut (code says by how much it runs over). In an action Short no line talks over the payoff:
   say "Wait for it." before it and the reaction after it, and let the game's sound play the hit.
5. **Punchy, to the viewer, like a gamer friend**: lines of 5-9 words (never more than 9 a sentence), "you", one point
   then the payoff. No play-by-play ("Then the SUV pulls up ..."). `"claim"` is `on_screen` (cite `evidence` on
   screen while it is said, and `teaches`), `fact` (`facts` from facts.json), `ask` (a question with `answered_by`)
   or **`hype`**: at most 5 words that claim nothing ("Watch this.", "Wait for it."). Anything a line names (a truck,
   the police, stars) needs evidence that shows it. Voice: am_puck at **1.2** (`"speed"` changes it).
6. **One slow-mo replay, action Shorts only** (the only second shown twice): `"replay": {"from": s, "to": s}` names
   the **impact itself** (inside the payoff, holding the loudest moment, under 1.5 s; read it off a 30 fps strip). It
   plays once at 0.5x, right after the payoff (at most 2 s on screen: a longer impact shows the 1 s round the hit),
   with the game's sound slowed and no voice. Use it when the hit is too fast to see; skip it otherwise. No ring is
   drawn on it (nothing tracks the object reliably). End the payoff before the game's camera swings away: the
   replay comes straight after it (Short 6's payoff ran 0.3 s into a swing, which flashed just before the replay).
7. **Words the voice says unclearly get misheard** (Nov 20: "Lane closed?" was heard as "Laying closed", "Easy." as
   "He's here"). The swear check transcribes the finished Short; if its words differ from yours, reword.

```json
{"shorts": [
  {"short": 4, "moment": "M21", "type": "action", "format": "A1",
   "format_why": "a black SUV bounces off a truck into the player's lane: setup, the hit, the reaction",
   "payoff": {"from": 268.6, "to": 270.2, "what": "the black SUV hits the truck and you plough straight through it"},
   "hooks": ["This lane closed FAST", "Watch the black SUV", "GTA 6 traffic is brutal"], "star": 1,
   "title": "Watch the black SUV in GTA 6", "description": "Sentence one. Sentence two.",
   "hashtags": ["#GTA6", "#GTAVI", "#Shorts", "#GTA6Gameplay"],
   "shots": [{"from": 265.0, "to": 271.6}, {"from": 278.4, "to": 280.4}, {"from": 282.0, "to": 287.6},
             {"from": 288.0, "to": 291.6}],
   "lines": [
     {"say": "Watch this, you're flying through traffic.", "claim": "on_screen", "evidence": ["E1"],
      "teaches": "the setup: a fast weave through traffic", "at": 265.25},
     {"say": "Wait for it.", "claim": "hype", "at": 267.45},
     {"say": "And the truck didn't even notice.", "claim": "on_screen", "evidence": ["E3"],
      "teaches": "the truck keeps driving after the hit", "at": 270.15},
     {"...": "..."}]}
]}
```

**Hooks.** 3 options in English, 3-6 words, readable in one second, true to the footage, no emoji. **An action
hook names what to watch in the first second ("Watch the black SUV") and never states the result ("ends badly")**:
the SUV Short kept 71 % of viewers, the bike Short ("This bike ride ends badly") only 32 %. `star` is the best
one, shown as the opening card from frame 0 (`open_card` sets it by hand, `"LINE 1|LINE 2"`). Whatever it tells the
viewer to watch must be easy to find in the picture (no ring is drawn for you): pick footage where it is clear.

**Still true:** words match the picture (a label in `dropped_labels` is not on screen: never name it); one moment, in
time order; no story spoilers, no names but Jason and Lucia, no dialogue quotes; mission footage needs
`"mission_ok"`; F2 never says why something happened unless the change and the result are both cited, in order; no
spoken call to action (a silent subscribe pill ends every Short).

**Title** 32-46 characters with 1-2 words in ALL CAPS (Title Case is fine), description exactly 2 sentences, 3-5
hashtags (`#GTA6`, `#GTAVI`, `#Shorts`; a `--game gta5` trial uses `#GTA5`).

Leave a pick out of `shorts.json` if it can't keep these rules; say why in the report. (`--engine v3` renders the old
beat format, with its loop ending, for comparison only.)

## 4. Render

```powershell
.\.venv-tts\Scripts\python.exe launch.py render --run "<run folder>"
```

It checks shorts.json (the payoff, the shots, truth and timing rules), finds a clear first frame, makes every shot
(one fixed window, steadied where that measures calmer, HUD close-ups), places the voice, renders every Short (engine
v4: the hook card, am_puck at 1.2, yellow captions, music, whooshes on the cuts, the game's own sound up for an action
payoff, the subscribe pill; no loop ending; true peak under -1.0 dBTP), checks every caption against its frame, logs
the footage as used and writes `review.html`. A Short marked **FAILED** lists its problems: fix only that Short
and render it alone with `--short N`. At most 3 tries per Short; after that leave it FAILED and go on.

## 5. Look at what you made

For each READY Short make a sheet and look at it with the Read tool, then at 3-4 full frames (`-ss <t>`):

```powershell
ffmpeg -v error -y -i "<short folder>\short.mp4" -vf "fps=2,scale=216:-2,tile=10x8" -frames:v 1 "<short folder>\check.jpg"
```

Ask: would a stranger understand what this shows and why it matters? Is the hook readable at frame 0 and is what it
names on screen? Does the payoff play whole, with nothing repeated? Does every close-up show what its line names? Is
the picture steady (no wobble)? No phone, menu, map, loading screen, gore or names on screen. Fix what a re-render
can fix; otherwise say so in the report.

## 6. Report, in plain English

1. HUD first: if `candidates.json` `hud.mode` isn't `hud`, say so at the very top, with why.
2. The review page path: `<run folder>\review.html`.
3. For each Short: READY or FAILED, the type and format, **its payoff (what, and where in the Short)**, its payoff
   strength and why it was picked (`pick_why`), the replay if it has one, the 3 hooks
   with the starred one marked, what it teaches or shows, the length, the title, its flags.
4. Candidates left out and why (`candidates.json` `rejected`, and picks you didn't write).
5. How long prepare and render took. Ask nothing.
