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
