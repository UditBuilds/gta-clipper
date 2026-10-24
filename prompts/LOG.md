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
| test1 | 4b v1 | 4/5 | 4/5 | 0 | 5/5\* | 0\* | 21.0 s | 2 4 5 6 7 |
| test1 | 8b v1 | 5/5 | 5/5 | 0 | 5/5\* | 0\* | 23.8 s | 2 3 4 5 6 |
| test1 | 4b v2 | 4/5 | 4/5 | 0 | 5/5\* | 0\* | 21.0 s | 2 4 5 6 7 |
| test1 | 8b v2 | 5/5 | 5/5 | 0 | 5/5 | 0 | 26.8 s | 1 2 3 4 5 |
| test1 | 4b v3 | 4/5 | 4/5 | 0 | 5/5\* | 0\* | 21.0 s | 2 5 6 7 1f |
| test1 | 8b v3 | 5/5 | 5/5 | 0 | 5/5 | 0 | 26.8 s | 1 2 3 4 5 |
| test2 | off | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 1 2 3 4 5 |
| test2 | 4b v1 | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 1 2 4 5 3f |
| test2 | 8b v1 | 2/5 | 2/3 | 0 | 2/5 | 1 | 15.0 s | 1 3 4 5 6 |
| test2 | 4b v2 | 2/5 | 2/3 | 0 | 2/5 | 1 | 15.3 s | 2 3 5 9 1f |
| test2 | 8b v2 | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 1 2 3 4 5 |
| test2 | 4b v3 | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 2 4 5 1f 3f |
| test2 | 8b v3 | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 1 2 3 4 5 |

\* Updated in PR 7. The owner rated test1 candidates 6 (10:20.3-10:35.3) and 7 (12:30.9-12:45.9) postable
(`eval/ratings/test1_v6_4b.json`, 2026-10-24). Before that these rows read 3/5 with 2 unrated (4b v1-v3) and 4/5 with
1 unrated (8b v1). No other row changes.

The judge tables above were computed on the 10 candidates rated at the time, and the v1-v3 stopping decisions stand.
With the 12 rated candidates of PR 7 (test1 1-7, test2 1-5) they read, as costly / kept but not postable / agreements:
v1 4b 2 / 1 / 9, v1 8b 2 / 2 / 8, v2 4b 3 / 1 / 8, v2 8b 0 / 2 / 10, v3 4b 3 / 0 / 9, v3 8b 0 / 2 / 10. Both new
candidates were kept by every model and version, so only agreements change.

# Stars hint prompts (v7, `--vlm-hint stars`)

A separate series from the veto prompts above. Everything is veto_v3's (contact sheet version 2, the six categories,
the veto and fill rules, the two Instruct models, the options); the only new input is one line per candidate with
the seconds of wanted stars clip.py measured inside the clip, e.g. "Measured by the tool: wanted stars visible for
27.5 of 30.0 s." The line is computed from the 0.5 s steps clip.py marks as having stars, cut at the clip's edges.

**Stopping rule (set by the owner before any result):** at most two versions, `stars_v1.txt` and `stars_v2.txt`.
Stop at the first version where neither model vetoes a candidate rated postable; otherwise stars_v2 is final,
whatever its results. A v2 is written only to fix a mistake v1 made, and its reason is logged here before it runs.
No version may describe a test1/test2 moment.

**Rated set, fixed before any stars run** (ratings as of commit 4821ae7): 12 candidates, test1 1-7 (all postable)
and test2 1-5 (1 and 3 not postable, the clumsy crashes). Ratings added after a version's runs don't change whether
that version passed.

The lines the dev set gives (known before any run, from the v6 runs' timelines): test1 #1 30.0 of 30.0 s, #2 29.0 of
30.0, #3 27.8 of 28.8, #4 27.5 of 30.0, #5-#10 0.0 of 15.0; test2 every candidate 0.0 s (#2 of 16.5 s, the rest of
15.0). So the hint can only help test1 1-4, which hold all three of the 4b's costly v3 vetoes; everywhere else it
says 0 s.

`--vlm-rule stars` (no boring_driving_or_walking veto with 5.0 s or more of measured stars) is a code rule applied to
the cached veto_v3 answers, not a prompt; it is reported with the other configs in eval/BASELINE.md.

## stars_v1 (`stars_v1.txt`), reason logged before running

What it fixes: every costly veto in PR 6 was a chase the model called `boring_driving_or_walking`. In stills a chase
looks like ordinary driving, and the stars are a row of about 25x6 px; both models also wrote "wanted stars" for
test2 clips, which have none. The tool already knows when stars show.

veto_v3 with these changes, nothing else:
- v3's star sentences in the HUD hints ("Wanted stars are a row of up to five small star shapes at the very top-right
  corner. If any frame shows them, the police are after the player and the clip is not boring driving.") are
  replaced by the candidate's line and a paragraph: the line was measured on the full-size video every half second
  and is reliable; the stars are too small on the frames to read, so trust the line over the frames; measured stars
  mean the police are after the player, so not boring driving; 0 s of stars does NOT mean boring, since gang chases,
  shootouts, fights and stunts have no stars.
- The minimap and subtitle hints stay, as their own paragraph (the cutscene category relies on the minimap).
- `reason`: "Mention police, wanted stars, weapons or damage only if you can clearly see them" loses "wanted stars".

### stars_v1 results, 2026-10-24

| Model | Run test1 | Run test2 | Vetoed but postable | Kept but not postable | Agreements |
|---|---|---|---|---|---|
| qwen3-vl:4b-instruct | 20261024-154516 | 20261024-154801 | **4 + 3** (test1 #1 3:10.2, #4 2:16.0, #6 10:20.3, #7 12:30.9; test2 #2 4:40.0, #4 5:06.5, #5 3:04.5) | 0 | 5 |
| qwen3-vl:8b-instruct | 20261024-155013 | 20261024-155318 | 0 | 1 (test2 #1) | 11 (test2 #3 clumsy crash vetoed) |

Result: fails the stopping rule (the 4b vetoes postable clips), so stars_v2 will be the last version.

What went wrong (the 4b; every one of its mistakes is `boring_driving_or_walking`):
- It used a positive line once (test1 #3, "wanted stars visible for 27.8 seconds, indicating police pursuit") but not
  for #1 (30.0 s) or #4 (27.5 s): "no visible police or weapons".
- With "0.0 of 15.0 s" it called every test2 candidate boring, 10 of 10, including the three postable ones v3 kept
  (#2, #4, #5), and also test1 #6 and #7, which v3 kept. Its reasons list what is absent: "no visible police, weapons,
  or damage", "no wanted stars are visible during the clip".
- Two sheets looked at before writing v2 (4b v1 runs above): test2 #2 (4:40.0) shows the car swerving onto a verge,
  a green slow-motion PEAK, and subtitles "Lose Madrazo's men" and "We gotta book, man. Go!"; test1 #1 (3:10.2) shows
  ordinary driving with the star row a few pixels wide in every frame.
- The 8b made no costly veto, vetoed one clumsy crash (test2 #3) for the first time, and wrote "police cars visible in
  frame 7" for test2 #4, which has none.

So v1's "Wanted stars mean the police are after the player" together with boring's "nobody chasing or being chased"
lets a small model reason backwards: 0 s, so nobody is chasing, so boring. And the rule for a positive line sat in a
paragraph far from where the category is chosen.

## stars_v2 (`stars_v2.txt`), reason logged before running; final whatever its results

Fixes v1's mistakes only (the 4b's boring vetoes). stars_v1 with these changes, nothing else; the line, the code and
everything else are unchanged:
- The sentence "Wanted stars mean the police are after the player, so a clip with measured stars is not boring
  driving." is removed from the stars paragraph, so nothing there links stars to "chasing" in a way that can be read
  backwards.
- The 0 s sentence now says that 0 s means neither that the clip is boring nor that nobody is chasing (gang chases,
  getaways, shootouts, fights and stunts have no wanted stars), and that with 0 s the model decides from the frames and
  the subtitles exactly as it would without the line. Plain wording, no capitals.
- The positive rule moves to where the category is chosen: `boring_driving_or_walking` ends "This category is wrong
  whenever the measured line shows more than 0 s of wanted stars: the police are after the player, so choose
  chase_or_shootout." `chase_or_shootout`'s "Fast driving with wanted stars counts as a chase" becomes "Driving or
  running while the measured line shows wanted stars counts as a chase."
- No draft of v2 was sent to a model before this entry.

### stars_v2 results, 2026-10-24 (final)

| Model | Run test1 | Run test2 | Vetoed but postable | Kept but not postable | Agreements |
|---|---|---|---|---|---|
| qwen3-vl:4b-instruct | 20261024-155844 | 20261024-160122 | **4** (test1 #1 3:10.2, #2 0:57.5, #3 3:50.2, #4 2:16.0) | 2 (test2 #1, #3) | 6 |
| qwen3-vl:8b-instruct | 20261024-160334 | 20261024-160635 | **1** (test1 #3 3:50.2) | 2 (test2 #1, #3) | 9 |

Result: the stopping rule is not met (both models veto postable clips), but stars_v2 is the last version allowed, so
it is final as it is. No stars_v3 was written.

- The 4b's 0 s problem is gone: in test2 it keeps all five rated candidates and vetoes only 6-10, and it keeps
  test1 #6 and #7 again. But it now keeps both clumsy crashes too ("other_action"), which v3 vetoed.
- A positive line still doesn't reach the 4b: it calls all four test1 candidates with stars (27.5-30.0 s) boring,
  "no visible police, weapons, or damage", although the boring definition now says it is wrong with stars.
- The 8b vetoes test1 #3 (27.8 of 28.8 s of stars) as boring, "no visible police or chase in any frame". It kept
  that candidate with v3 and stars_v1.
- The 8b now keeps test2 #10 (10:53.0, which shares 9 s with a never-pick phone-call range); every earlier version
  of both prompt series vetoed it. It is candidate 10, so it wasn't picked.

## Pipeline results of the stars prompts (eval.py)

Same columns as the table above; f = vetoed, used as filler. The test1 #6/#7 ratings of PR 7 apply.

| Video | Run | Hits (strict) | Recall | Bad picks | Postable@5 | Unrated | Average length | Picks |
|---|---|---|---|---|---|---|---|---|
| test1 | 4b stars_v1 | 5/5 | 5/5 | 0 | 5/5 | 0 | 26.8 s | 2 3 5 1f 4f |
| test1 | 8b stars_v1 | 5/5 | 5/5 | 0 | 5/5 | 0 | 26.8 s | 1 2 3 4 5 |
| test1 | 4b stars_v2 | 4/5 | 4/5 | 0 | 5/5 | 0 | 21.0 s | 5 6 7 1f 2f |
| test1 | 8b stars_v2 | 5/5 | 5/5 | 0 | 5/5 | 0 | 24.0 s | 1 2 4 5 6 |
| test2 | 4b stars_v1 | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 1f 2f 3f 4f 5f |
| test2 | 8b stars_v1 | 3/5 | 2/3 | 0 | 3/5 | 1 | 15.3 s | 1 2 4 5 6 |
| test2 | 4b stars_v2 | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 1 2 3 4 5 |
| test2 | 8b stars_v2 | 3/5 | 2/3 | 0 | 3/5 | 0 | 15.3 s | 1 2 3 4 5 |

Postable@5 hides most of these mistakes: when too few candidates survive, the vetoed ones come back as fillers (4b
stars_v1 on test2 vetoed all 10 and still scores 3/5). The judge table (Table B in eval/BASELINE.md) shows them.
The only unrated pick is 8b stars_v1's test2 #6 (15:57.1-16:12.1), from a failed version.
