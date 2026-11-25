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

## Known limits

- The rule reads what a 4B model says. An attack the index's line doesn't name (it names one line every 6 s) is not found.
- A helicopter or plane taken with no "approaches / opens / boards / climbs" line on foot right before it is not found.
- "Player flies" is the model's word for being in control of a plane; `_135610`'s jet never leaves the ground. Scripts must not say it flies.
- The posted boar Short is type "stunt" in the owner's list, so an animal attack right after it isn't held back as "the same type".
