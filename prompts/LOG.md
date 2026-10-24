# Veto prompt versions

Every prompt tried on the dev set (test1, test2) is kept here with its results, failed ones included. At most 3
versions may be tried; the last one is frozen for the held-out test.

**Stopping rule (approved by the owner before any result):** a new version is written only to fix a mistake the
previous version made, and its reason is logged here before it runs. Stop at the first version where neither model
vetoes a clip the owner rated postable; otherwise v3 is final, whatever its results. No version may describe a
test1/test2 moment.

All runs: contact sheet version 2 (8 frames, 1536x480, the frame nearest the peak moved onto it), temperature 0,
seed 42, num_ctx 4096, thinking off (the Instruct builds have no thinking mode). Table B counts the 10 candidates
with an owner rating (test1 1-5, all postable; test2 1-5, of which 1 and 3 are not postable).

## v1 (`veto_v1.txt`), 2026-10-24

The text approved in the plan, plus one sentence for the PEAK label (decision 3 came after the plan).

| Model | Run test1 | Run test2 | Vetoed but postable | Kept but not postable | Agreements |
|---|---|---|---|---|---|
| qwen3-vl:4b-instruct | 20261024-140946 | 20261024-140721 | **2** (test1 #1 3:10.2, #3 3:50.2) | 1 (test2 #1) | 7 |
| qwen3-vl:8b-instruct | 20261024-141511 | 20261024-141231 | **1 + 1** (test1 #1 3:10.2, test2 #2 4:40.0) | 2 (test2 #1, #3) | 6 |

Result: fails the stopping rule (both models veto postable clips).

What went wrong: all four costly vetoes are chases (police with wanted stars in test1, a gang chase in test2) that
the model called `boring_driving_or_walking`, with reasons like "drives through city streets ... with no chase or
combat". Still frames can't show speed or who is behind, and the wanted stars are about 30x8 px in a 384x216
frame. The 4b also wrote "wanted stars" for two test2 clips, which have none.

## v2 (`veto_v2.txt`), reason logged before running

Fixes v1's only costly mistake, chases called boring driving. Two changes, nothing else:
- `boring_driving_or_walking` now needs calm, ordinary play in every frame, and says that driving alone is not
  enough because stills can't show speed: possible racing, escaping or following goes to chase_or_shootout or
  other_action.
- The unsure sentence now also says that every candidate was picked for unusually busy sound and picture, so a calm
  clip should be rare (true for every candidate: each has a step scoring 2.0 or more).

The clumsy-crash misses (kept but not postable) are not addressed in v2: they don't count for the stopping rule,
and a stricter clumsy rule could add costly vetoes.

### v2 results, 2026-10-24

| Model | Run test1 | Run test2 | Vetoed but postable | Kept but not postable | Agreements |
|---|---|---|---|---|---|
| qwen3-vl:4b-instruct | 20261024-142200 | 20261024-141944 | **2 + 1** (test1 #1 3:10.2, #3 3:50.2; test2 #4 5:06.5) | 1 (test2 #3) | 6 |
| qwen3-vl:8b-instruct | 20261024-142722 | 20261024-142448 | 0 | 2 (test2 #1, #3) | 8 |

Result: fails the stopping rule (the 4b still vetoes postable clips), so v3 will be the last version.

What went wrong:
- The 4b didn't move: it still calls both test1 police stretches boring driving ("no visible chase or combat"),
  and now also calls test2 #4 (5:06.5) boring.
- The 8b now makes no costly veto, but it vetoes none of the 10 rated candidates either: both clumsy crashes
  (test2 #1 and #3) are kept as other_action ("navigating turns and gates"). On test1 it vetoes nothing at all, so
  its picks equal v5's.

## v3 (`veto_v3.txt`), reason logged before running; final whatever its results

Before writing v3, the five sheets behind v2's mistakes were looked at (v2 runs above):
- test1 #1 (3:10.2) and #3 (3:50.2), the police stretches: every frame is the player's car on a city street; no
  police car is in any frame. The wanted stars are there in most frames but tiny (a row of about 25x6 px at the top
  right). The PEAK frames (3:24.5, 4:01.5) show ordinary driving too.
- test2 #4 (5:06.5): the PEAK is the green slow-motion flash; the other frames are driving, with subtitles such as
  "Push the pedal, man. We gotta move", "Get rid of them" and "We gotta book, man. Go!".
- test2 #1 (0:00.1) and #3 (0:42.3), the clumsy crashes: the PEAK frame shows the bump in both (the truck against
  a parked sports car; the truck against a gate).

So a first v3 draft, which said "decide mainly from the PEAK", was dropped before any run: on the police stretches
the PEAK shows plain driving, so it would push toward boring. v3 changes from v2, nothing else:
- For the 4b's police misses: the HUD hint now says what the stars look like (a row of up to five small star
  shapes at the very top right) and that if any frame shows them the clip is not boring driving. Both models have
  written "wanted stars" for test2 clips, which have none; such a mistake can only keep a clip, never veto one.
- For the 4b's veto of test2 #4: a hint that the subtitles at the bottom are what the characters say and often tell
  whether someone is chasing, escaping or fighting.
- `reason` must say what happens at the PEAK too, and mention police, wanted stars, weapons or damage only if it can
  clearly see them (a list of things to look for can make a small model report things that aren't there).
- For the 8b's kept clumsy crashes: `clumsy_crash_or_stuck` now starts "at the PEAK the player accidentally bumps
  into ...", since the PEAK frame shows the bump. The PEAK label line adds "the moment the clip was picked for".
- `boring_driving_or_walking` is v2's text, unchanged.

### v3 results, 2026-10-24 (frozen)

| Model | Run test1 | Run test2 | Vetoed but postable | Kept but not postable | Agreements |
|---|---|---|---|---|---|
| qwen3-vl:4b-instruct | 20261024-143602 | 20261024-143345 | **3** (test1 #1 3:10.2, #3 3:50.2, #4 2:16.0) | 0 | 7 (both clumsy crashes vetoed) |
| qwen3-vl:8b-instruct | 20261024-144132 | 20261024-143856 | 0 | 2 (test2 #1, #3) | 8 |

Result: the stopping rule is not met (the 4b still vetoes postable clips), but v3 is the last version allowed, so it
is frozen as it is. No v4 was written.

- The 4b now gets all 5 rated test2 candidates right, both clumsy crashes included ("the PEAK frame shows the truck
  hitting the gate"). On test1 it still calls the police stretches calm driving ("no visible police or wanted
  stars"), and now also vetoes #4 (2:16.0, the player on foot, then a car) as boring.
- The 8b vetoes nothing on test1 and only unrated candidates on test2 (#7-#10), so its picks equal v5's in both
  videos. It wrote "police pursuit indicated by wanted stars" for test2 #2 and #4; test2 has no wanted stars.
- test2 #10 (10:53.0-11:08.0, which overlaps the never-pick phone-call range 10:38-11:02 by 9 s) was vetoed by both
  models in all three versions.

## Pipeline results for every version (eval.py)

Postable@5 counts unrated picks as not postable. "Picks" are candidate numbers in final order; f = vetoed, used as
filler. `--vlm off` is v5 (identical timeline.csv).

| Video | Run | Hits (strict) | Recall | Bad picks | Postable@5 | Unrated | Average length | Picks |
|---|---|---|---|---|---|---|---|---|
| test1 | off | 5/5 | 5/5 | 0 | 5/5 | 0 | 26.8 s | 1 2 3 4 5 |
| test1 | 4b v1 | 4/5 | 4/5 | 0 | 3/5 | 2 | 21.0 s | 2 4 5 6 7 |
| test1 | 8b v1 | 5/5 | 5/5 | 0 | 4/5 | 1 | 23.8 s | 2 3 4 5 6 |
| test1 | 4b v2 | 4/5 | 4/5 | 0 | 3/5 | 2 | 21.0 s | 2 4 5 6 7 |
| test1 | 8b v2 | 5/5 | 5/5 | 0 | 5/5 | 0 | 26.8 s | 1 2 3 4 5 |
| test1 | 4b v3 | 4/5 | 4/5 | 0 | 3/5 | 2 | 21.0 s | 2 5 6 7 1f |
| test1 | 8b v3 | 5/5 | 5/5 | 0 | 5/5 | 0 | 26.8 s | 1 2 3 4 5 |
| test2 | off | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 1 2 3 4 5 |
| test2 | 4b v1 | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 1 2 4 5 3f |
| test2 | 8b v1 | 2/5 | 2/3 | 0 | 2/5 | 1 | 15.0 s | 1 3 4 5 6 |
| test2 | 4b v2 | 2/5 | 2/3 | 0 | 2/5 | 1 | 15.3 s | 2 3 5 9 1f |
| test2 | 8b v2 | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 1 2 3 4 5 |
| test2 | 4b v3 | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 2 4 5 1f 3f |
| test2 | 8b v3 | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 1 2 3 4 5 |
