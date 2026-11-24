# PR 33 notes (Nov 24): what was checked by hand

## The bike crash: is it the one already posted?

**No. Two different moments; nothing was marked posted.**

| | Posted Nov 20, "This GTA 6 motorbike ride ends BADLY" | Bank 1121-164843-1736 |
|---|---|---|
| Recording | `_20261119134026` (Nov 19, 13:40) | `_20261121164843` (Nov 21, 16:48: recorded the day after the Short went up) |
| Seconds | 7:37-7:54 (`_used_footage.json`, `launch-2026-11-20-part2` Short 6) | 28:42-29:05, hit at 29:05 |
| Frames | one rider, white tank top, no helmet; an SUV pulls out beside a green CRUDA truck | two riders with helmets, thrown over a car at another crossing; 1 wanted star |

It is still a repeat of the *topic* "motorbike crash (Short 6)" in `posted_topics.json`, which costs it 2 points in the picker.

## The jet ski (`_20261119134026`): does it prove "I lost the cops on a jet ski"?

**No, so it wasn't made.** Frames every 1 s, and every 0.25-0.33 s round 13:08-13:13:

- 12:24-12:25: running into the sea. "Crime reported" comes up and **2 stars** light, as grey/white outlines, with the
  clothes and appearance icons under them. No police car, boat, helicopter or officer is on screen then or later.
- 12:25-12:54: wading, then swimming out to a jet ski beside a boat. 12:56: on it. 2 outline stars all the way.
- 13:06-13:09: open sea, the stars blink. 13:10: they turn **solid red** as the map label changes to "Atlantic Ocean".
- 13:11: they fade out, and from 13:10.8 to 13:16 the game's own tip says **"The cops are searching for you [star] and they
  have your description"**. "Heat cleared", which the game shows when it is really over (`_014828`, 6:33), never comes.
- 13:36: the pause map still shows the red search area round the beach.

It wasn't a cutscene, a death or a mission ending, and the player is on the jet ski. But the game says the opposite of the
hook as the stars go, and there is no cop to lose. The moment also failed the shot check on Nov 20 (only the character's
back over an empty sea), so `/launch` leaves it out by itself. Its payoff of 2/3 ("the stars rise with police on screen")
comes from the vision model calling that tip a police pursuit: the true payoff is 1/3.

## What was made instead: the two-up bike crash (`_20261121164843`, 28:44-29:13)

`/launch prepare` on the whole bank picked it first (payoff 3/3, 4.41 points after -4 for the posted topics "wanted stars"
and "motorbike crash (Short 6)"); then the truck crash (`_135610`, 7:17) and the boar (`_143644`, 6:52, a stunt like the
last posted). Seen on the frames: the bike passes a red car (29:04.3), hits an oncoming SUV on the centre line (29:04.9),
both riders are thrown over the crossing, the driver lands on the helmet (29:06.4); both are rising at 29:09.5, the driver
stands at 29:11 and the passenger walks in from 29:11.5. The recording stands still from 29:12.55, so the Short ends there.

- **Blood:** the safety check flags 29:07-29:11. Looked at: the red lines on the road are painted markings; there is a
  small stain on the white line at the left, outside the 9:16 window, and grazes on the arms. No body, no pool.
- **It is a narrated Short, not an action one,** because prepare decided that ("both fit; narrated fits better": 4.25 to
  2.92) and launch.md says to keep it. So it has no slow-mo replay.
- **Whisper, first 2 s:** "Can you survive crashing this bike?" was heard as "and you survive crashing this bike."; "Could
  you ..." as "You survived crashing this bike."; "Can anyone survive crashing this bike?" as it is said (0.77).
