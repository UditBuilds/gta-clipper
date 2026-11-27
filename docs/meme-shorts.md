# Meme Shorts (PR 39, Nov 27)

A second kind of Short, next to the narrated ones: a funny moment from the owner's own GTA 6 footage, the game's own sound, one
static caption on top, almost no editing. `meme_short.py` is a separate mode; /launch, engine v4 and every narrated Short are
unchanged.

**Why.** The last three narrated Shorts got 203-1,241 views. The hits were physics chaos with a quick payoff (the overpass jump,
20.7K; the black SUV crash, 11.9K). A channel of funny GTA 6 character moments (`@wisebenda`, 11.2K subscribers) gets 6.9M-9.6M
views a Short with real game audio, one caption and almost no editing; the owner's frame-by-frame teardown of 12 of their Shorts
gave the rules below.

```powershell
.\.venv\Scripts\python.exe meme_short.py find --top 15                                  # funny candidates (index + bank, read only)
.\.venv-tts\Scripts\python.exe meme_short.py hear "Grand Theft Auto VI_20261121223830" 196 228   # Whisper on that window only
.\.venv\Scripts\python.exe meme_short.py check spec.json                                # rules 1-8, renders nothing
.\.venv-tts\Scripts\python.exe meme_short.py render spec.json --out C:\AI\gta\shorts\meme-<date>\<n>-<slug>
.\.venv\Scripts\python.exe memetest.py                                                  # 26 checks, about 20 s
```

## The rules, and where they are checked

Each rule is a function in `meme_short.py` that returns problems ("rule N: ..."); `problems(spec)` runs them all and `render()`
refuses a spec with any. `memetest.py` has specs that must pass and specs that must fail for each one, and tiny real renders whose
frames are measured.

1. **Length** (`length_problems`): a gag (physics or a sight gag) 7-12 s; dialogue that builds to one payoff 15-24 s; never over 30 s.
2. **First second** (`first_second_problems`): motion or an impact by 1.0 s, or the first spoken line by 0.5 s; a gag's punchline at
   0:02-0:06. The motion is declared (`motion_s`); the render also reads clip.py's picture change and loudness over the first second
   from the bank's raw data and flags (never fails) a start that changes less than half the recording.
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
