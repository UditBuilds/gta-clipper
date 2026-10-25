# Calibration sessions kept as records

`calibrate.py` keeps each session in `C:\AI\gta\calib\<name>\session.json` (outside the repo). Sessions worth
keeping are copied here unedited.

| File | What it is |
|---|---|
| `gta5_recal_try1_session.json` | The owner's first blind re-calibration of GTA V (2026-10-25, 15:56-16:19), stopped after about 25 minutes and **not saved** as a profile. It holds the 24 frames, the owner's labels, boxes and star clicks, and the last Measure report. Measure missed 3 "HUD shown" frames (test1 1:40, 2:20; test2 5:30) and the star step kept failing. The causes were calibrate.py bugs (fixed in the commits after this one): dim bar colours got no colour rule, bar names on the page didn't match Measure's, and the star tool sent every extra click to the last point. sha256 9bb5baaa2bb47d464a2603c5a38718f07c49e7e493333726ecae793c1e84e76b. |
