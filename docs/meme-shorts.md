# Meme Shorts (PR 39, Nov 27)

A second kind of Short, next to the narrated ones: a funny moment from the owner's own GTA 6 footage, the game's own sound, one
static caption on top, almost no editing. `meme_short.py` is a separate mode; /launch, engine v4 and every narrated Short are
unchanged.

**Why.** The last three narrated Shorts got 203-1,241 views. The hits were physics chaos with a quick payoff (the overpass jump,
20.7K; the black SUV crash, 11.9K). A channel of funny GTA 6 character moments (`@wisebenda`, 11.2K subscribers) gets 6.9M-9.6M
views a Short with real game audio, one caption and almost no editing; the owner's frame-by-frame teardown of 12 of their Shorts
gave the rules below.

```powershell
.\.venv-tts\Scripts\python.exe meme_short.py marked                                     # YOUR marked moments -> Shorts (below)
.\.venv\Scripts\python.exe meme_short.py find --top 15                                  # suggestions only (index + bank, read only)
.\.venv-tts\Scripts\python.exe meme_short.py hear "Grand Theft Auto VI_20261121223830" 196 228   # Whisper on that window only
.\.venv\Scripts\python.exe meme_short.py check spec.json                                # rules 1-8, renders nothing
.\.venv-tts\Scripts\python.exe meme_short.py render spec.json --out C:\AI\gta\shorts\meme-<date>\<n>-<slug>
.\.venv\Scripts\python.exe memetest.py                                                  # 40 checks, about 30 s
```

## Marked mode (Nov 28): you mark the moments, the pipeline only edits

Sample 1 (the GPS crash) was picked by the finder from the swearing in its dialogue, not from anything funny on screen: you
stand, get hit from behind, a guy gets out, it ends. So the finder is not trusted to judge "funny" any more; it stays as a
suggestion list. **You** mark the moments while you play, two ways, and `meme_short.py marked` cuts, captions and renders each new
one.

### Way 1: save a clip on the PS5 (best: the exact moment)

The menu names below were written from memory, not checked on a console: each step says **(check on your PS5)**.

1. Right after the funny moment, press the **Create** button, choose **Save recent gameplay**, then **Save short video clip** and
   **the last 30 seconds** (1 minute is fine too). **(check on your PS5)** The clip ends just after the moment; the tool looks for
   the moment in the clip's last 25 s.
2. After playing: open the **Media Gallery**, select the saved clips, **... > Copy to USB drive**. **(check on your PS5)**
3. On the laptop, copy **only those clips** (not full recordings) into `C:\AI\gta\marked\`. The PS5 writes them to
   `PS5\CREATE\Video Clips\<game>` on the stick **(check on your PS5)**:
   ```powershell
   Copy-Item "E:\PS5\CREATE\Video Clips\*\Grand Theft Auto VI_20261128*.webm" C:\AI\gta\marked\    # E: = the stick; that day's clips
   ```
   Full recordings still go to `C:\AI\gta\input` as before. A full recording put in `marked` would only give a Short of its last 25 s.

### Way 2: write the time in `C:\AI\gta\marks.txt` (when you are recording anyway)

One line a moment: the clock time (IST, as the PS5 or your phone shows it) and a few words for yourself. With seconds if you can.

```
2026-11-28
11:47 bear attacks protesters
23:05:30 car flips off the bridge
7:05 pm truck jump into the pool
```

- **The window** is 20 s before to 5 s after the time (you note it a little late). A time with seconds gets exactly that. A time
  to the minute (`11:47`) covers its whole minute: 20 s before 11:47:00 to 5 s after 11:47:59 (my call: a minute mark anchored at
  :00 would miss a moment at :40 and cut some other spike instead). The biggest jump of sound and picture in it is taken as the
  punch, so **seconds make it exact**.
- **The day**: a line with only a date (`2026-11-28`) dates the lines under it. Without one, a time is the latest moment with that
  clock time before marks.txt was last saved (so write the lines on the day, or put the date line on top). It is worked out once,
  the first time the line is seen, and kept.
- **am / pm**: `7:05 pm` is 19:05. `11:47` with neither is read as 11:47 first and, when no recording covers that, as 23:47 (the
  pick says so).
- **The recording** must be in `C:\AI\gta\input`. A PS5 file name is the time the recording **ended** (when it was saved), in IST:
  checked on your files on Nov 28. Read as the start, three back-to-back pairs (`_014828`/`_015444`, `_022442`/`_023422`,
  `_035639`/`_040546`) would overlap by 450-600 s, and their frames at the overlap differ; read as the end, every gap between
  recordings is 0 s or more (`_134026` ends on the pause menu and `_135610` starts on the same menu 5 s later). The `.mp4`'s own
  creation time (19:20:08 UTC) is the 00:50:08 in its name, so the names are IST. Recording seconds = the mark - (name time - length).
- A line it can't read, a time no recording covers yet (copy the recording and run again) and a time that is the same moment as a
  saved clip are **listed with the reason**, never skipped silently.

### What `meme_short.py marked` does with each new clip or line

1. **Places it**: the clip's last 25 s, or the recording and window above.
2. **Finds the punch**: every 0.1 s, the picture's change between frames and the sound's level (the same measurements as rule 2),
   each against its own usual level there (robust z-scores, added); the biggest jump in the window, at its start.
3. **Hears the lines** round it (Whisper, 8 s before to 11 s after) and takes the last line that starts within 1 s before to 4 s
   after the punch and still fits (a line too long for a 12 s gag is left out, and the pick says so).
4. **Cuts it** to the rules: a gag of 7-12 s, the punch 2-6 s in (3 s when there is a choice), the hard cut 0.5-1.5 s after that
   last line (1.0 s when it fits), and a start whose first second passes rule 2 measured. When no plain cut starts alive (with no
   line after the punch, rules 1 and 7 put it 5.5-6 s in, behind a long lead-in), it uses rule 6's one replay: the main part stops
   just after the punch, then the punch again from 1 s before it for 3 s at 0.5x. Letterbox, so nothing is cropped away (change
   `layout` and `focus` in the spec for full-bleed).
5. **Captions it from what is seen**: the vision model looks at 4 frames round the punch (prompt `meme_caption_v1`, reason in
   `prompts/LOG.md`), with the lines heard given censored as context only, and writes what happens, 3 captions and a title. A
   caption with a swear word (written censored or not) or that breaks rule 3 is dropped; fewer than 3 kept are filled with stock
   ones and flagged; the first kept one is starred. The model leaves memory before anything else runs.
6. **Renders it** with every kept check (`meme_short.render`: rule 2 measured, the index's labels when the file is indexed, used
   or never-shown footage, the safety question every second, blood in the first 7 s, the sound, Whisper). A file with no index (a
   saved clip, a recording not indexed yet) is checked by the safety question alone and flagged `NOT INDEXED`. Nothing is indexed.
7. **Lists everything** in `C:\AI\gta\shorts\marked-<date>\picks.md`: each Short with where it came from, the punch, what was seen
   and heard, the 3 captions (starred), the title, notes and flags, and the line to re-render it after you edit its `spec.json`;
   then every moment not made, with the reason.

The record `C:\AI\gta\marked\marked_record.json` keeps what is done: a moment that was made is never made again (a clip by its
name and size, a line by its text and day); one that failed or is waiting is tried again by the next run. If the record can't be
read, the run stops with an ERROR and changes nothing (never read as empty: that would make everything again). Marked mode
writes nothing to `_used_footage.json`, `posted_topics.json`, the bank or the index.

## The rules, and where they are checked

Each rule is a function in `meme_short.py` that returns problems ("rule N: ..."); `problems(spec)` runs them all and `render()`
refuses a spec with any. `memetest.py` has specs that must pass and specs that must fail for each one, and tiny real renders whose
frames are measured.

1. **Length** (`length_problems`): a gag (physics or a sight gag) 7-12 s; dialogue that builds to one payoff 15-24 s; never over 30 s.
2. **First second** (`first_second_problems`): motion or an impact by 1.0 s, or the first spoken line by 0.5 s; a gag's punchline at
   0:02-0:06. The motion is declared (`motion_s`) and, since Nov 28, also **measured on the cut itself** (`measure_start`,
   `start_verdict`): every 0.1 s, the change between frames of a 96x54 gray copy and the sound's level. The first second passes
   when its busiest 0.2 s moves at least 0.5x the busy level of the rest of the Short (its 90th percentile; a replay counts,
   slowed), or when its first 0.5 s of sound comes within 3 dB of the rest's loud level (90th percentile). A still, quiet start
   stops a production render before anything is encoded ("rule 2 (measured)"). The old flag compared the first second with the
   bank's raw series, which are clip.py's z-scores cut at 0 below the median, so their median was 0 (0.0 and 0.01 on the
   samples) and it could never fire. The 90th percentile, not the 75th: in a 7 s gag the punch fills less than a quarter of the
   rest, so the 75th sat on the lead-in and a still start looked as busy as the rest. On the owner's footage: sample 1 changes
   20.24 (needs 6.05); sample 2's picture is still (2.61, needs 8.38) but its line at 0.4 s makes it -26.3 dB (needs -27.9); the
   bear recording at 4:30-4:40, where Jason stands still by the truck for 2.5 s, is 0.96 (needs 6.32) and -32.1 dB (needs
   -20.5), and is refused. Of eight other 10 s cuts where clip.py's loudness and picture change are both under the recording's
   median for 1.5 s, three are refused (a bar talk with little movement, looked at; two stretches of `_051818`, not looked
   at) and five pass.
3. **Caption** (`caption_problems`, `caption_rule_problems`, `ass_document`, `measure_caption`): exactly 3 options with one starred;
   2-6 words, up to 10 only for "<Subject> tried <X> and this happens..."; never "Watch the X" or "Can you X?"; swears written
   censored (`censor()`: fkng, sht, btch); no trailer names (only Jason and Lucia) and no story words (truth.py's lists); one swap at
   most, after the punchline; shown from 0.00 s to past the last frame (an event ending on the last frame can miss it), the swap at the
   same centisecond on both sides. White Arial Bold, a 4 px black outline, no shadow, BorderStyle 1 (3 would be a box), the middle at
   y 346 (18 %) full-bleed, the middle of the top bar when letterboxed. libass was checked to select Arial-BoldMT. After a render the
   caption layer alone is drawn over black and measured on its first and last frames and both sides of the swap (frames picked by
   number: a seek by time landed a frame off and missed the last one).
4. **Layout** (`layout_problems`, `geometry`): `full` (a 9:16 crop centred on the subject's `focus`, kept inside the picture) or
   `letterbox` (the 16:9 picture 1080x608 at y 656, black bars). Anything else, a blurred background included, is refused.
5. **Sound** (`audio_problems`, `key_problems`, `mix_sound`): no voice, script or narration key is accepted; the game's sound is brought
   to -14 LUFS by explainer's `normalise` and through `safety.guard_true_peak`; the measured file must be within 1 LU and under
   -1 dBTP. An optional music bed (off unless `"music"` names a file in `C:\AI\gta\music`) sits 12 dB under the game between lines and
   20 dB under it while a heard line plays, with 0.15 s ramps.
6. **Edits** (`edit_problems`, `key_problems`, `graph`): none by default. At most one: a punch-in of 1.20-1.35x toward the subject,
   starting 0.5 s before to 0.2 s after the punchline, for 1-2 s; or one replay that jumps back 0.5-1 s, plays 2-3.5 s at 0.5x (its sound
   an octave down) and comes 0.2-1.5 s after the punchline. Keys for whooshes, booms, sound effects, arrows, freezes, inserts, shakes,
   flashes or a second edit are refused.
7. **Ending** (`ending_problems`): the last frame 0.5-1.5 s after the punchline or the last reaction line (with a replay, after the
   replay's own punchline). End cards, pills, subscribe and loops are refused.
8. **Title** (`title_problems`): 2-12 words, at most 60 characters, no hashtag, no @handle, censored, no emoji, no trailer name or story
   word, never "Watch the" / "Can you".

**Kept from the repo (rule 9)**, `footage_problems` and `vision_checks`, production renders only: the footage is clean in the index
(no cutscene, menu, phone or loading screen within 0.5 s, `footage_short.clean`); it overlaps nothing in `_used_footage.json` (posted,
rendered or rejected) and nothing the bank may never show; the vision model's safety question (safety_v3, once a second, with its own
cache in the Short's folder so the library's cache is left alone) blocks a phone, menu, map, loading screen or cutscene and anything
`safety.never_shown` finds; blood in the first 7 s (twice a second, and the index's own lines) blocks; subtitles and blood after 7 s
are flags. The model is unloaded before ffmpeg and Whisper run. Whisper hears the finished Short: swearing in the game's sound is
allowed and always flagged.

**Calls made.** "a", "an" and "the" don't count towards the words: the owner's own example "Jason Became a Gorilla for a Sec" is 7
words with them and the rule says 2-6. No emoji in v1: Pillow isn't installed and no new packages are allowed, so any emoji or symbol
is refused (never an empty box). Meme mode writes `meme.json`, never `render.json` (launch.py reads every render.json under the shorts
folder), and writes nothing to `_used_footage.json`, `posted_topics.json`, the bank or the index.

## Candidates of Nov 27 (step 2)

`meme_short.py find` read all 23 GTA 6 indexes, the bank's never-shown list and `_used_footage.json` (38 hits). The finder has no
"crash" kind, so the bank's three unused 3/3 crashes were added by hand. Each candidate below was looked at on the index's frame
sheets, the best on half-second strips; Whisper ran on three windows only. Best first:

1. `_20261121164843` 15:43-15:55 (943.6-955.5). The black SUV follows the GPS's "Turn Right ... 0 ft" straight into a silver car at an
   intersection; on the crash camera the other driver gets out: "Hey yo! What the f, man? My f-ing ride, bruh! Damn!" A physics
   payoff plus an NPC reaction. High.
2. `_20261121223830` 3:20-3:42 (200.6-222.5). Bear protesters ("FREE THE BEAR!!") block an Animal Services truck: "This driver is
   trying to deliver an enslaved bear directly into the hands of sadistic zoo owners." Driver: "I didn't do sh*t! I'm just doing my
   job. It's a f-in' bear! What am I gonna do here? Let it out?" Protester: "You are oppressing our sister of the north's right to
   roam!" Absurd dialogue building to one line. High (a side encounter with prompts: it may be a mission).
3. `_20261119134026` 12:02-12:10 (722.5-730.3). Jason walks up to beach guys, throws his drink, drops one ("Hey, hey! Get off me!") and
   the game pops "Extremely violent behavior will contribute toward a psycho criminal profile". Medium-high. **Blocked** at render by
   the kept safety question: "phone on screen at 12:02-12:06" (a beach guy holds up his phone to film; the check is meant for the
   phone's own screen). Not overridden. The weapon wheel opens at 12:01 and 12:11, so the window can't move.
4. `_20261119025552` 4:50-5:02 (290-302). The red pickup on "U-Turn Left 0 ft" takes the crossover, spins and hits a black SUV, then
   drives on. Medium; the same GPS joke as 1.
5. `_20261119051818` 8:44-8:57 (524-537). A diver after the shark scare: "This is a thousand percent your fault, Dobbs. Let me on that
   f-in' boat! Out! I want out!" Medium; mission, a name and a swear burned into the subtitles, the weapon wheel at 8:48.
6. `_20261119035639` 7:02-7:14 (422-434). "Wait, hold on! I'm goin' there now, I just got lost!" / "Shut the f up. You f-ing stood us up,
   boy!" Medium-low; mission, two swears burned in.
7. `_20261119135610` 9:06-9:22 (546-562). Jason pulls a driver out of a pickup in a parking lot at sunset and drives off. Low: a plain
   carjack.
8. `_20261121164843` 23:43-23:59 (1423-1439). A blue convertible, door open, T-bones a car at an intersection. Medium physics, no joke.
9. `_20261119014828` 10:46-10:56. Parked by a man with a dog: "No, no, I am sorry, that house is a goddamn revolving door." Low.
10. `_20261119134026` 10:28-10:52. Jason walks a dog on the beach; a man takes a photo. Low.
11. `_20261120162122` 6:28-6:44. Two men argue by a truck (index line only, not viewed). Low.
12. `_20261121164843` 3:04-3:20. A store clerk held up at gunpoint. Low (a gun from the first frame).
13. `_20261119022442` 7:38-7:50. The player threatens a man on the ground and he runs (index line only). Low.
14. `_20261120175255` 0:02-0:26. A red truck stuck in dense forest, then the player falls ("Ugh..."). Out: 15.0-25.8 s is in
    `_used_footage.json` and the next stretch is a never-use one.
15. `_20261120162122` 0:48-2:54. Nightclub dancing. Low: long, little happens.

Not taken: the alley fight (`_20261119022442` 13:06) lies inside never-shown `1119-022442-0779` (a body and blood); "a man attacks a
woman" (`_20261119134026` 9:42) is not a joke.

## Samples of Nov 27 (step 3)

In `C:\AI\gta\shorts\meme-2026-11-27\<n>-<slug>\`: `short.mp4`, `captions.txt` (3 options, the starred one marked `*`), `title.txt`,
`meme.json` (the spec, the timeline, the sound, the measurements, the safety looks and the flags). The specs are in `examples\meme\`.
Each render ran every kept check (the index, the used footage, the safety question with 7-10 new looks, Whisper); the first and last
frames of each were looked at.

| folder | length | layout | edit | caption (starred) | LUFS / dBTP | flags |
|---|---|---|---|---|---|---|
| `1-the-gps-said-turn-right` | 11.9 s gag | letterbox | none | **The GPS said turn right** | -14.19 / -1.5 (limited from -0.8) | swearing in the game sound at 3.8, 4.5, 7.1, 9.0 s |
| `1b-the-gps-said-turn-right-music` | 11.9 s gag | letterbox | none, music bed on | the same | -14.21 / -1.5 | the same |
| `2-free-the-bear` | 21.9 s dialogue | letterbox | none | **"Free the bear"** | -14.2 / -1.1 | swearing at 5.6, 7.8 s; game subtitles all through (they say "sh*t", "f-in'", "a*s") |
| `3-just-another-day-for-jason` | not rendered | | | | | BLOCKED by the safety question (see candidate 3) |

- Every caption measured in the top bar (1: y 265-402, 2: y 303-351; the bar ends at 656) on the first and last frames.
- **The music bed (1b)**, `Future - Anno Domini Beats.mp3`, picked without listening (no way to judge a track's mood here). Subtracting
  render 1 from 1b leaves about -27.7 LUFS before the crash and -31.7 LUFS under the reaction lines, against -12.3 and -15.4 for the
  game sound: roughly 15-16 dB under it. That method leaves some game sound in, so read it as approximate. The levels are set against
  the game's average loudness, not its loudness at that moment.
- **Short 2's ending**: the protester starts his next line ("Come on! They'll spend a fortune...") straight after the payoff, so any cut
  0.5-1.5 s after it hears "Come on!" and shows the next subtitle on the last frames. Whisper also didn't hear the first line ("This
  driver is trying to deliver an enslaved bear ...") under the chanting; its time (201.0 s) comes from the subtitle frames.
- Short 1's GPS box is small in the letterbox (about 170 px wide); the caption says the joke.

## The bear, checked (Nov 28)

Sample 2 (`2-free-the-bear`) ends on a protester's line. The known punchline of this street encounter is that the protesters get
the polar bear freed and it attacks them, so `_20261121223830` was checked from 3:42 to its end, 6:11.8 (there is no 6:12-7:00):
Whisper on 3:38-6:12, then the vision model (`qwen3-vl:4b-instruct`) one frame a second in 4 s sheets ("is a bear, an open truck
door, an animal attacking or a vehicle hitting a person in view?"), then every half second by eye from 3:42 to 4:48. **No bear is
ever freed or seen, and nothing attacks anyone.** What happens instead:

- 3:42-4:16: Jason and two protesters work at the back doors of the Animal Services truck; the doors stay shut ("they used this
  liberation-proof lock. Do you know how to open one of those?").
- 4:17-4:29: at the cab, the game offers two choices on screen, **RUN THEM OVER** and **OPEN THE DOORS**; "Does anyone have any
  explosives or something? But only ones that don't produce carbon gas!"; "Come on, you thinking about it? Run 'em over."
- 4:30-4:33: the driver: "Yeah, f- it! I can't get fired over this. I'm goin'!" Jason walks back to his red car.
- 4:34-4:38: behind him, the truck drives through the protesters: "Oh f-! He just ran over Mason!" (small, in the background).
  The protesters scatter; 4:44 "Sh-! Dodos down! Abort the plan!"; Jason drives away.
- 4:42-6:12: driving, the phone (texting Lucia, never usable), a cabin, a cutscene at 6:03.

The freed-bear ending is the **OPEN THE DOORS** branch, which this play did not take, so no 2b was rendered. The vision model
said "doors open" once (3:42; the frames show them shut) and put the run-over on Jason's car (it is the truck): its lines were
read against the frames, not trusted alone. The checking script and its answers are not kept in the repo (a one-off).
