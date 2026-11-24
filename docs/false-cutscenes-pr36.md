# PR 36: false cutscenes, what was measured and looked at (Nov 24)

## The cause

The index calls a stretch a cutscene when the minimap isn't seen for 1.5 s or more. For GTA 6 "the minimap is seen" means
its player arrow is: a white pointer in a dark disc (`clip.arrow_seen`, profiles/gta6.json). **GTA 6 draws that icon in two
ways.** The other one is the reverse: a dark pointer in a white disc with a dark outline. The swamp had nothing to do with
it: the same grass and the same map read fine at 70.5 s and again at 90.5 s, with the dark icon.

`_143644`, what the detector measures (it needs a bright middle of 0.316 or more **and** a dark ring of 0.488 or more):

| recording second | icon on screen | middle that is bright (170+) | ring that is dark (95 or less) | ring's mean brightness | old verdict |
|---|---|---:|---:|---:|---|
| 70.0 | dark disc | 0.66 | 1.00 | 35 | seen |
| 70.5 | dark disc | 0.66 | 1.00 | 37 | seen |
| 71.0 | light disc | 0.16 | 0.00 | 214 | not seen |
| 78.0 | light disc | 0.21 | 0.00 | 213 | not seen |
| 80.0 (the bite) | light disc | 0.18 | 0.00 | 212 | not seen |
| 84.5 (on the ground) | light disc | 0.26 | 0.00 | 214 | not seen |
| 90.0 | light disc | 0.21 | 0.00 | 214 | not seen |
| 90.5 | dark disc | 0.71 | 1.00 | 36 | seen |

(73.5-74.0 is the binoculars and 75.5 the weapon wheel: no minimap for half a second, which is not a cutscene.)

Every half second of all 23 recordings was measured (the 18 read with the HUD, the 5 without): 1,131 half seconds have the
light icon, in 8 recordings, most of them (645) in `_135610`. They are one tight cluster, with nothing near it:

- light icons: 0.64-0.81 of the ring is 170 or brighter, 0.55-0.71 of the middle is 95 or darker, 0.52-1.0 of the outline
  (7.8-8.8 px from the middle) is 150 or darker;
- of the other 10,099 half seconds without a dark icon **not one** has over 0.5 of the ring **and** over 0.2 of the middle.
  The 19 nearest are sign text, a street name on the map, rocks, a stick, edges (6 of them are test fixtures).

48 random light icons out of the 1,131 were looked at: 48 are the icon.

## The fix

`index.light_arrow_seen` (index.py; clip.py and profiles/gta6.json are unchanged): the minimap is also seen when that icon is.
54 lines. It can only add "seen", so nothing can become a cutscene through it. Saved HUD readings of an arrow profile are
made again when a recording is indexed again (the rule is part of hud.json's key). A sheet the vision model itself calls a
cutscene, a menu, the phone or loading stays never_use whatever the HUD shows, and one or two wrong half seconds in a row can't
open a cutscene (the 0.5 s kept round each side closes the gap).

clip.py's own runs still know the dark icon only. The bank and /launch read only their sound and picture changes.

## Before the fix: 20 stretches looked at

Drawn with a fixed seed (36) from the 184 stretches of the 18 HUD recordings the index called a cutscene because of the minimap,
at most 2 a recording; 4 frames of each.

| # | recording, seconds | what the frames show | verdict | after the fix |
|---|---|---|---|---|
| 1 | `_011548` 12.5-16.0 | the radio wheel, blurred | menu | blocked |
| 2 | `_014828` 572.0-574.5 | the radio wheel | menu | blocked |
| 3 | `_022442` 31.5-38.5 | reading a magazine, HUD off | gameplay, HUD really off | blocked |
| 4 | `_022442` 762.0-767.0 | the map screen | menu | blocked |
| 5 | `_023422` 227.0-232.0 | black bars, a wreck from above | **real (cinematic camera)** | blocked |
| 6 | `_025552` 322.0-331.5 | driving, the minimap is there | **misread** | gameplay |
| 7 | `_025552` 660.0-664.0 | a talk, no HUD, subtitles | **real** | blocked |
| 8 | `_035639` 44.0-49.5 | phone, then the camera swings into a scene | **real** | blocked |
| 9 | `_035639` 164.5-241.5 | the roof scene: close-ups, no HUD | **real** | blocked |
| 10 | `_040546` 143.5-159.5 | black bars, the mission title | **real** | blocked |
| 11 | `_134026` 451.0-456.0 | the pause menu | menu | blocked |
| 12 | `_135610` 600.0-633.5 | driving, shot at, the minimap is there | **misread** | gameplay |
| 13 | `_135610` 685.5-722.0 | wanted, driving on the airfield, the minimap is there | **misread** | gameplay |
| 14 | `_143644` 53.0-68.5 | the focus view, then binoculars: no minimap | gameplay, HUD really off | blocked |
| 15 | `_143644` 159.5-163.5 | the focus view: no minimap | gameplay, HUD really off | blocked |
| 16 | `_165142` 443.0-444.0 | fade to a dark screen | menu | blocked |
| 17 | `_184417` 39.5-64.5 | a neighbour talks, no HUD, subtitles | **real** | blocked |
| 18 | `_184417` 116.0-123.0 | the map screen | menu | blocked |
| 19 | `_164843` 180.0-183.5 | the weapon wheel | menu | blocked |
| 20 | `_164843` 1094.0-1110.5 | a car's info panel, HUD off | gameplay, HUD really off | blocked |

So: 6 real cutscenes, 3 misreads, 7 menus or wheels and 4 gameplay stretches where the game itself hides the minimap. Five more
real ones were picked on purpose, the long story scenes: `_014828` 802.5-932.0, `_040546` 180.0-387.5, `_010806` 66.0-262.5,
`_025552` 556.5-625.0, `_015444` 105.5-128.5 (black bars or dialogue framing in every frame looked at).

## After the fix: every flip looked at

The real code over all 18 recordings (0 model calls, star readings and model answers unchanged): **34 stretches, 579.5 s, go from
cutscene to gameplay; none goes the other way** (it can't). 7 recordings change; those 7 were indexed again.

| recording | flips | seconds of the recording | what the frames show |
|---|---:|---|---|
| `_022442` | 6 | 444.5-458.5, 635.0-639.0, 792.0-797.5, 839.5-858.0, 996.0-1006.5, 1009.0-1020.5 | a wrecked car and a gun, driving, a fist fight, a shootout in a yard, running from and driving past the police |
| `_023422` | 2 | 155.5-172.0, 233.0-249.0 | driving the white SUV |
| `_025552` | 1 | 322.0-331.5 | driving |
| `_134026` | 2 | 61.5-69.0, 574.5-594.0 | driving; the player knocks down two women (nothing for a Short) |
| `_135610` | 9 | 351.0-374.0, 390.5-401.0, 457.5-461.0, 563.0-569.0, 584.0-594.5, 600.0-633.5, 648.5-673.0, 685.5-722.0, 739.5-925.5 | the police chase by truck and SUV, the airport on foot, a police car taken, the airfield |
| `_143644` | 3 | **70.5-91.0 (the gator)**, 295.5-310.0, 385.5-404.0 | the gator attack, shooting at a dock, wading with a bag |
| `_164843` | 11 | 906.5-912.5, 934.5-939.0, 943.5-951.5, 1367.5-1373.0, 1377.0-1381.5, 1386.5-1389.5, 1621.0-1624.0, 1673.0-1676.0, 1676.5-1684.0, 1702.0-1712.5, 1715.5-1719.0 | driving, a crash at a crossing, walking, getting on a bike |

Looked at: all 34 (6 or more frames each, the 186 s one every 5 s). The minimap is on screen in all 34; none holds a cutscene. Four
hold the weapon wheel for under 1.5 s, as the rule always allowed.

| hand-checked | stays blocked | becomes gameplay |
|---|---:|---:|
| real cutscenes (6 drawn + 5 picked) | **11 of 11** | 0 |
| menus, wheels, loading | 7 of 7 | 0 |
| gameplay with the HUD really off | 4 of 4 | 0 |
| misreads in the 20 (the minimap is on screen) | 0 | **3 of 3** |
| every flip of all recordings | | 34 of 34 are gameplay |

## Not fixed

- **The underwater icon.** Under water the disc is grey, not white, and it doesn't separate as cleanly. One HUD recording has it:
  `_135610` 165.5-192.0 (a dive that ends in "wasted" at a jellyfish), 26.5 s still called a cutscene. So of the 35 stretches whose
  minimap is on screen, 34 are fixed.
- **Gameplay where the game hides the minimap** (the focus view, binoculars, a car's info panel, a magazine, the safe house) is
  still never_use. The minimap can't tell it from a cutscene.
- The 5 recordings indexed with no HUD (`_005008`, `_043305`, `_051818`, `_162122`, `_014057`) are as they were.
- `_164843` 1453.5-1468.5 (after the car hits the tree) is still blocked: the minimap is not there in those frames.
