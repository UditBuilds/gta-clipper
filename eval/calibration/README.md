# Calibration sessions kept as records

`calibrate.py` keeps each session in `C:\AI\gta\calib\<name>\session.json` (outside the repo). Sessions worth
keeping are copied here unedited.

| File | What it is |
|---|---|
| `gta5_recal_try1_session.json` | The owner's first blind re-calibration of GTA V (2026-10-25, 15:56-16:19), stopped after about 25 minutes and **not saved** as a profile. It holds the 24 frames, the owner's labels, boxes and star clicks, and the last Measure report. Measure missed 3 "HUD shown" frames (test1 1:40, 2:20; test2 5:30) and the star step kept failing. The causes were calibrate.py bugs (fixed in the commits after this one): dim bar colours got no colour rule, bar names on the page didn't match Measure's, and the star tool sent every extra click to the last point. sha256 9bb5baaa2bb47d464a2603c5a38718f07c49e7e493333726ecae793c1e84e76b. |
| `gta5_recal2_session.json` | The owner's second blind re-calibration of GTA V (2026-10-28, same 24 frames), saved as `gta5_recal2` (the profile itself is not in the repo). Bars (found from one rough box) and stars (found by the new star auto-fit, 2 slots) matched gta5 on every hudtest check and on test1. The phone failed 3 of 4 blind hudtest phone checks and caused 1 bad pick on test2 (a clip through the Lester call): its dark and bright areas were drawn on parts that change from call to call, and Measure had only 3 call pictures, all from test2. Page open to save took 109.4 min, most of it waiting on two calibrate.py fixes (boxes past the picture edge, star auto-fit); the final stretch with the star auto-fit took 1.4 min. sha256 e86c67cded480434d28be209395c3d40da69199c360a9bf4044626d24163fc2d. |
