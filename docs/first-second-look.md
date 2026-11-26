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

## The outside reviews' claims, checked (file:line on main fecbb83)

| claim | verdict | where |
|---|---|---|
| captions render flat yellow, one word a line | confirmed | explainer.py:1887-1912 (`colour ... HIGHLIGHT` :1908), called from assemble.py:183 |
| 80 % of winning Shorts open with a statement; mine mostly with questions | first half not checkable here; second partly confirmed | of the 9 posted Shorts whose first line I could read, 5 open with a question (all of the last 3), 4 with a statement or command |
| 4-8 s voice gaps | confirmed raw, but each sits on the silent payoff + replay | airliner 5.71 s, animal 7.79 s, overpass 6.2 s (log.json); outside the payoff/replay the longest is 3.25 s |
| render_v4 has no punch-in zoom | confirmed | steady.py:23 (`ZOOM = 1.05`, fixed), :82-95 (one fixed window) |
| the first-word check runs on the mixed short.mp4 and misheard "Can" (steal -> take) | confirmed | launch.py:3512, speech.py:92-94 (first 2 s); launch-2026-11-25-part3-a2 render.json: "and anyone steal"; nov25-watch-list.txt:8 |
| the "Can anyone ..." advice is misleading | confirmed | speech.py:111, printed in the very failure where "Can anyone" was misheard |
| card_words ignores "?" | confirmed | cutplan.py:421 (`[.!]*`) |
| a manual [0.0, 400.0] locks 35+ bank moments | partly: the entry is real, it locked 27 | _used_footage.json:83-90 (before; backup 20261126-120910) |
| titles stack hashtags | refuted | make_short.py:82-83 refuses "#" in a title (truth.py:265 for /launch); make_short.py:156-159 puts the tags on their own line under the description; no posted upload.txt has one |
| some end cards show @NeonGetaway | confirmed | launch.py:2058, explainer.py:809, examples/*.json, .claude/commands/new-short.md:52, footage-short.md:45 |
| slow-mo replay only for action Shorts | confirmed (rule 5) | cutplan.py:171; launch.py:3283 and :3386 |
| a whoosh on the cut into the replay | confirmed | assemble.py:198 |

## What the checks guarantee, and what they don't

- `key_verb`: the word or a regular form of it (steal, steals, stealing; irregular ones like "stole" don't count) is
  in line 1, the open card and the title. A word every line has ("the", "this", "you", "GTA", "can") is refused. Whether
  it is the *key* verb is the writer's choice.
- `stakes_at_0`: declared, not seen. Code checks the cited evidence covers shot 1's start and a why is written; the vision
  model doesn't judge whether the stakes are readable.
- First second: 2 frames (0.0, 0.5 s) through frame_v1's fields. It catches empty, back-only, too dark and a missing
  vehicle/police/street/water. It can't tell walking or running on a busy street, and an animal is only a flag.
- Punch-in: on the middle of the picture. A subject at the side can be cut off at 1.2 (today's Short: the white sedan at
  the right edge, 9.4-12.4 s).
- Gap rule: between lines only. Before line 1 (by 0.2 s) and after the last line (0.5 s) were already rules.
- Captions: a line never shows a card word while the card is up; widths are fitted by measuring with libass.

## Noticed, not fixed

- The game's crash camera hides the minimap, so the index calls it a cutscene ("hud: no minimap", never_use): today's
  red-pickup crash camera (_025552, 347.0-351.5 s) can't be used. A false cutscene of the PR 36 kind.
- `render_one` runs the twice-a-second first-7-s check (blood, subtitle swearing) only when `start_s` is set: a Short
  whose shot 1 starts later than its moment's window gets only prepare's once-a-second look there. Run by hand for today's
  Short (336.5-343.5 s): nothing.
- FIRST WORDS IN THE MIX on today's Short: the voice alone is heard "watch the red pickup", the mix "watch the room".
- explainer.py's own captions (/new-short, /footage-short) are still one yellow word at a time: rule 1 was built for /launch.
- The proof moments above the payoff bar all repeat the posted star topics, so today's options are all action.
