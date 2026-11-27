# Payoffs two checks see: an animal attack, an aircraft taken (Nov 25)

The owner's rule (Nov 25): *an animal attack and taking an aircraft count as a payoff when two checks agree (same 2-of-3 payoff
bar as other picks).* Until then only a crash, an explosion, a jump or fall, or a star change counted, so the gator attack and the
airliner theft stayed at 1/3 and /launch left them out.

## The two checks

The vision model looks at every second of a recording twice, with different questions and different frames. They are the two checks:

| | the index | the safety question |
|---|---|---|
| frames | 4, 1.5 s apart, one sheet every 6 s | 4, 1 s apart (a new look where nobody has looked) |
| says | a line, an activity, a vehicle | a note |
| **animal attack** | a line that says an animal attacks (`payoffs.attack_in`), or that he fights one | the same: one of the two must say ATTACK, the other attack or FIGHT; the same animal; a shared second or more |
| **aircraft taken** | on foot at an aircraft (walks up to it, opens its door, boards it), then flying a plane or helicopter in the next sheet | sees the PLAYER fly it or board it ("Player flies ...") |

Both read what the text says happens: a denied thing ("no attack") and what is written on the picture ("text: ...") don't count. One check
alone is never enough, and the same check twice is one check.

- **Tier 2/3** (`launch.payoff_strength`), "an event two checks see": it meets the bar every pick must meet, an impact (3/3) still outranks
  it. 1.0 point, as a HUD change the topic candidates count is.
- It is a type of its own ("animal attack", "aircraft taken") for the no-same-type-twice rule, an ACTION payoff at the quality gate, and an
  evidence item a line may cite.
- **No label, no peak, no window and no id changes.** A moment of 10 s or more with one is on the action shelf.
- A moment has an attack when it holds its first 2 s, and an aircraft when it holds the change from outside it to inside it.
- Where the index says one and nobody has looked at its seconds, the bank asks the safety question once (at most 2 sheets an event).

`payoffs.py` (the rule, standard library), `bank.payoff_events`, and 10 checks in banktest, written before the rule.

## What it finds in all 23 recordings

Compared on the same saved data (no model call): the code of PR 36, this code with the rule off, this code with it on.

- PR 36's code against this code with the rule off: **0 of 445 entries differ.**
- The rule on: **4 entries change**, no id comes or goes, the moments that may never be shown are the same 30.

| entry | seconds | before | after | the two checks |
|---|---|---|---|---|
| `1119-135610-0722` | 11:58-12:25 | 1/3, proof shelf, "star rise" | 2/3, action shelf, "aircraft taken" | index "Player approaches airplane, opens door, walks up stairs" then "Plane flying low ...", plane; note "Player flies low over airport with aircraft on ground." |
| `1119-143644-0069` | 1:09-1:27 | 1/3, action shelf, "shootout" | 2/3, action shelf, "animal attack" | index "Player aims gun at alligator, then gets attacked and falls"; note "Player fights alligator on dirt path." |
| `1119-143644-0077` | 1:17-1:27 (list item) | 1/3 | 2/3, still a list item | the same attack |
| `1119-143644-0077b` | 1:17-1:24 (list item) | 1/3 | 2/3, still a list item | the same attack |

Moments with payoff 2/3 or more: 23 before, 27 after. Action shelf 25 to 26, proof shelf 38 to 37. 0 model calls.

**Looked at, frame by frame:** he climbs the flyUS jet's stairs (11:58-12:01), at 12:01.4 the screen goes black for a moment, and at 12:01.6
the HUD shows a plane's name (BOTTCHER 015A) and height. The gator bites him at 1:20.

**The negatives that matter, real ones:**
- `_135610` 13:02-13:13: he climbs the pink jet's stairs, stands at its door and walks away. The index says vehicle "plane" for that
  sheet, and is on foot: nothing. (Checked in `near_a_plane_is_not_taking_it`.)
- `_165142`: the biplane is in the air when the recording starts and after each map screen: nothing.
- `_143644` 1:30-1:48: he walks past, aims at and steps on the same gator: nothing more than the one attack.
- The boar (7:00) is posted and 3/3 as before (a fall): the second check never saw it attack, so the new rule doesn't see it at all.

## Not the rule's doing: 21 labels that a rebuild refreshes

The saved bank (Nov 24, 20:38) was written before that night's /launch looked at its candidates' footage. Any rebuild, PR 36's code too,
now reads those newer saved looks, and 21 entries get another note in their label (3 of them another kind: `1119-014828-0534` and
`1119-135610-0893` unsure to a calm activity, `1119-051818-0485` unsure to a find). No payoff, score, shelf or status changes with it.

## Deliberate breaks: 26 of 26 caught

One at a time, in a copy of the repo, each must make banktest fail: any animal agrees with any; two fights are an attack; the seconds
need not match; the note names a plane instead of the player in it; no note needed; a vehicle called plane is enough; any sheet before
counts as on foot at it; on foot anywhere; driving up to it; denied attacks; written words; the player attacking it; a man's attack near
an animal; a moment that starts after it; the tier is 1; the tier is 3; the gate doesn't know it; no evidence item; the type is the star
rise's; the new term outranks an impact; the proof shelf; never asked about unseen seconds; asked again where answers are saved; no
points; every moment gets the recording's events; the category is the index's again. The first version of the checks missed 6 of them
(a vehicle called plane, driving up, written words, the type, the shelf, the category); the cases were added.

## What /launch picks with it (Nov 25, `--count 6`, 0 model calls)

Three crashes (3/3), a false star rise (`1119-135610-0907`, see PR 36), **the gator (2/3, ACTION, "animal attack")** and the runway. Not the
airliner: `launch.pool_of` keeps 60 s between two candidates of a recording, and the runway moment (`1119-135610-0678`, 40 s earlier,
11.65 points with its posted-topic cost, rendered and not posted) takes its place in the pool. Nothing was changed for it. For the airliner
Short the same prepare was run with only that moment offered (every check, the gate, the bar and the types as they are): it passes all of
them, NARRATED, and a third look agrees ("Player flying low over airport with altitude indicator").

## Known limits

- The rule reads what a 4B model says. An attack the index's line doesn't name (it names one line every 6 s) is not found.
- A helicopter or plane taken with no "approaches / opens / boards / climbs" line on foot right before it is not found.
- "Player flies" is the model's word for being in control of a plane; `_135610`'s jet never leaves the ground. Scripts must not say it flies.
- The posted boar Short is type "stunt" in the owner's list, so an animal attack right after it isn't held back as "the same type".

## The review (Nov 27)

A read-only review of this PR (G37) listed wordings that wrongly count or wrongly miss, tests that can't fail, the pool gap and the
bank file. The review's own text was not in the repo or on GitHub; each claim as the owner summed it up was checked against the code
(the old lines and the new are in the commits) and against made-up lines before anything was changed. 7 checks were written first; each
failed on the code before the fix.

**Wordings (payoffs.py).** Before, these counted: "Player narrowly avoids being bitten by an alligator", "is almost attacked by a gator",
"dodges a shark attack", "Alligator is attacked with a knife" (the gator is the one attacked), "chased by a dog" (no contact), "Billboard
showing a shark attack", a poster, a TV, curly quotes, "Player watches a plane flying low" (the note's player only watches), "talks to
pilots", "flies off his motorcycle", a helicopter pad, an aircraft carrier, "runs to his car as a plane flies overhead", "walks toward the
hangar near a plane", "approaches a car with a helicopter overhead". None counts now. These were missed and count now: "shoots alligator that
attacks him", "aims at the alligator as it attacks", "gets bit by a dog", "snaps at", "pounces". How:

- What only nearly happens or would (avoids, dodges, almost, nearly, tries to, before it can, instead of) is blanked to the end of its part
  of the line, so "nearly dies after an alligator attack" keeps the attack. What is on a picture (billboard, poster, sign, TV, screen,
  statue) or said (talks about, news, radio, shouts about) is blanked the same way; curly quotes are quotes.
- "gets attacked" counts only when the one attacked isn't an animal. "chased" is no attack.
- The note must have the player fly it or get in it with only words like "is", "now", "then" between, and not watch it; "flies off / out /
  through the windshield" is a crash. The index's on-foot line must have the aircraft as what he goes at (a few words after "approaches",
  "runs to", "climbs stairs to"), with no other vehicle or "near/as/while" in between, and no driving in the line.
- Two attacks a sheet apart are one event only when it is the same animal (a gator and then a dog were one event, with both checks).

Judgment calls: "Player drives up to the plane and approaches it on foot" is not on foot at it (a line that drives is left out; "approaches
it" names no aircraft). "chased by" is no attack.

**On the real data nothing changes:** the same dry run as above (0 model calls, every write blocked, the same posted list for every run):
PR 36's code against this code with the rule off, 0 of 445 entries differ; rule off against rule on, the same 4 (`1119-135610-0722`,
`1119-143644-0069`, `-0077`, `-0077b`) with the same numbers; this PR before the review against after it, 0 of 445.

**Tests that couldn't fail.** The pink jet's first case in `near_a_plane_is_not_taking_it` has no flying sheet at all, so on its own it can't
catch "a vehicle called plane is enough" (its `stairs` case does). Every rule above now has a negative case of its own, at the text level and
in the bank (`the_bank_counts_none_of_the_reviewed_wordings`).

**The pool gap (G37 #16).** The runway star rise (`1119-135610-0678`, 11:18-11:35, tier 2) and the airliner (11:58-12:25, tier 2) are 40 s
apart and share no second; `launch.pool_of`'s 60 s gap kept the airliner out, so it was never checked. Ranking by tier first would change
nothing (both are 2/3). The smallest fix: the gap keeps out a candidate of the **same kind** (category, as the picker's near-duplicate rule
reads it) or one that **shares seconds**, not a different payoff. The picker's own rules (near-duplicates, 2 a recording) are unchanged. On
the live bank (read only): `--count 3`'s pool of 15 swaps `1119-135610-0907` for `1119-135610-0640` (both 2/3); `--count 6`'s pool of 25 swaps
three 0-1/3 candidates for 0640 and two 1/3 ones. (The airliner itself is posted now, so it isn't offered.)

**The bank file (G37 #10).** `bank.read_bank` read a cut-short, locked or wrong-shaped `content_bank.json` as no bank: a build then gave
every moment a new id (keep_ids had nothing to keep) and wrote over it, with no copy. Now it is `index.StateError` (every program prints it as
a plain ERROR) for build, posted, rejected and page, read before any mining; and a build copies the old `content_bank.json` and `bank.html`
to `bank\backups\<time>` before it writes.
