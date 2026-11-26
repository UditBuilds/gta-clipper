# The first second (PR 38, Nov 26): what changed, and what each change guarantees

The owner's rules of Nov 26 after two outside reviews of the channel: the hits showed the stakes at 0:00, the flops opened
on empty grass, a filler hook, or a title whose verb the voice didn't say.

## Step 2: the cops Short's footage, unlocked to what it really showed

`_used_footage.json` held a hand-made entry for the posted Step 0 Short
(`2026-11-19-how-to-make-gta-6-cops-forget-you`): `[0.0, 400.0]` of recording `_20261119014828`. That locked 27 of the bank's 38
moments of the recording (15 proof, 12 list), though the Short showed 35 s of it.

- **The real spans** come from the Short's own `log.json` (each beat: its source, its second in the source, how long it was
  up) and `script.json`. 9 of its 20 beats were close-ups cut from the recording on Nov 19 (`shorts\_samples\heat-zoom`);
  where each starts was read back from the commands that made them (the hud_* set at 15:15 IST, the tips at 15:10: the
  files' times match), and checked on the pixels: three samples match the recording best at exactly that second (PSNR
  14.1 / 20.0 / 26.3 dB there, lower 0.5 s either side). The compare beat counts both its cards; the loop ending replayed
  0.0-1.5 s. 20 beats, merged where they touch: **17 spans, 34.4 s** (the Short is 35.1 s).
- **Dry run first** (a copy of the bank, refreshed against the new file): 12 moments go from posted to new, 15 stay posted
  (the Short showed half a second or more of each), nothing else changes. Every other entry of the file and the rejected
  list are the same.
- **Applied** (statuses refreshed, the bank not rebuilt). Copies of `_used_footage.json`, `posted_topics.json`,
  `content_bank.json` and `bank.html` as they were: `C:\AI\gta\bank\backups\20261126-120910`.

Unlocked (all of recording _014828; none reaches the 2/3 payoff bar, so /launch still won't pick them):

| id | shelf | seconds | label | payoff |
|---|---|---|---|---|
| 1119-014828-0016 | proof | 16.0-29.0 | police chase | 1 |
| 1119-014828-0051 | proof | 51.0-61.0 | wanted stars go up (0 to 2) | 1 |
| 1119-014828-0061 | proof | 61.0-79.9 | wanted stars go up (0 to 2) | 1 |
| 1119-014828-0079 | proof | 79.9-95.2 | police chase | 1 |
| 1119-014828-0163 | proof | 163.4-179.0 | police chase | 1 |
| 1119-014828-0179 | proof | 179.0-195.4 | police chase | 1 |
| 1119-014828-0195 | proof | 195.4-211.7 | police chase | 1 |
| 1119-014828-0209 | list | 209.5-219.5 | police chase | 1 |
| 1119-014828-0211 | proof | 211.7-233.0 | police chase | 1 |
| 1119-014828-0233 | list | 233.0-256.0 | on foot | 0 |
| 1119-014828-0319 | list | 319.0-336.5 | unsure | 0 |
| 1119-014828-0380 | list | 380.5-384.5 | unsure | 0 |

**The warning (`footage_short.long_locks`, `LOCK_WARN_S = 45`).** An entry that locks more than 45 s (overlaps counted once,
every recording added up; a Short is 15-39 s) is named with a WARNING by `bank.py build`, `bank.py page`, `bank.py posted`
and `launch.py prepare`. On the old file it names the cops entry (400 s) and nothing else; on the new one, nothing.

## Steps 3-7: what changed in the code (engine v4, /launch)

Each change was a failing check first (editortest, banktest, explainertest), then the fix; commits in that order.

- **Captions** (`assemble.caption_events`, `caption_lines`, `accents`): 2-3 words a line, Lilita One at 120 px (drawn
  smaller only to fit 900 px), white, 1-2 accent words (the key word yellow, a thing to watch cyan), a 12 px black outline,
  centred on y 1260. A line never crosses a sentence, a word under the open card (PR 33) or the card's end, and breaks at a
  comma. The first real render captioned "No slowing down." as NO alone, then SLOWING DOWN; a line a little wide is now
  drawn a little smaller instead (`LINE_UNITS_MAX`).
- **Hooks** (`cutplan.hook_rule_problems`): no filler ("Watch this." / "Watch this, ..." / "Wait for it", also in every line
  through `truth.filler_in`); a starred hook or line 1 that asks needs `"stakes_at_0"` (evidence covering shot 1's start, a
  why of 3+ words); `"key_verb"` in line 1, the open card and the title. `cutplan.card_words` drops "in GTA 6" before "?".
- **End card**: `explainer.HANDLE = "@NeonGetawayYT"`; any other @handle in a pill is refused (explainer and edit2);
  launch.py's pill, the examples and the commands' examples say @NeonGetawayYT.
- **First words** (`launch.hook_voice`): Whisper hears voice.wav from line 1's start to its end, 0.08 s before the first
  word; that fails the Short. The mixed short.mp4 is checked too, as a flag only (FIRST WORDS IN THE MIX).
- **First second** (`cutplan.subject_of`, `subject_problems`, `launch.first_frame_check`): the subject is named (hook,
  `"subject"` or payoff); the frame question looks at shot 1 at 0.0 and 0.5 s: empty, back-only or too dark fails, and so
  does a subject whose field it doesn't see (vehicle, police, street, water); an animal is a flag.
- **Voice gaps** (`cutplan.gap_problems`): at most 3.5 s with no voice between two lines, the payoff and the replay left out.
- **Punch-in** (`steady.punch_in`): perspective filter, from 1.05 to 1.2 of the recording over the payoff, held to the
  shot's end and over the replay. **Narrated replay** (`cutplan.is_impact`, `replay_of`): a Short whose evidence has a
  crash, stunt, jump or fall may replay its impact; no line talks over a replay; the game's sound comes up over it.
  **No whoosh into the replay** (`assemble.cut_sfx`).
- **Fixtures**: editortest's line 1 "Watch this." is "Watch closely." (filler now), its key verb "watch", its title "WATCH
  this GTA 6 car on the highway", its shot 2 ends at 34.2 s (it ended on 5.7 s of quiet). Checks that pinned values of the
  old fixture say the new ones; none was removed or loosened. `testdata/v4_commands.json` was re-recorded four times on
  purpose; its `history` names every command that changed.

## Step 8: today's Short

`C:\AI\gta\shorts\launch-2026-11-26-v2\1-watch-this-red-pickup-in-gta-6-traffic\short.mp4` (READY, 15.0 s). The first
render (`launch-2026-11-26`) captioned NO alone; it is left as it was, not posted.

- Options (prepared with only these bank moments offered, the bank read, not rebuilt): **M2 the red pickup** (_025552
  5:36-5:54, star), M3 the blue convertible (_164843 23:53-24:13, into a yard), M5 the white pickup at sunset (_135610
  7:23-7:43, its footage rendered once before). The black SUV T-bone (_164843 15:37) lost to M3 as a near-duplicate.
- The game's crash camera on the red pickup (347.0-351.5 s) is marked a cutscene by the index ("hud: no minimap"), so the
  Short shows the hit on the chase camera, then in slow motion, and skips the crash camera. Not changed (a known class of
  false cutscenes, PR 36).
