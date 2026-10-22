"""Known-frame checks for clip.py's HUD detectors, on the owner's GTA V PS5 captures test1.webm and test2.webm.

The footage is not in the repo. Each check grabs one frame, runs it through clip.py's own HUD filter chain
and detectors, and compares with what the frame is known to show:

  test1 3:00  pause map                 -> minimap absent
  test1 2:50  body on road, wanted      -> stars present, phone absent
  test1 8:30  phone call                -> stars absent, minimap present
  test1 8:20  phone call (Jimmy)        -> phone present
  test2 18:57 phone call (Lester)       -> phone present

Extra frames guard the hard cases. All of them, and the three phone frames above, were used to set the
detectors, so they are regression checks only:
  test1 1:00  minimap flashing red while wanted -> minimap present, stars present
  test1 4:40  ammo counter sits in the star row -> stars absent, minimap present
  test2 4:10  phone call (unknown number)       -> phone present
  test2 12:10 phone call (Dr. Friedlander)      -> phone present

Single frames are checked, so the 2-of-3 smoothing clip.py applies to stars is not involved here.
Exit code 0 = all pass, 1 = a check failed, 2 = footage missing (SKIP, never a pass).

Usage:  python hudtest.py [path\\to\\test1.webm] [path\\to\\test2.webm]
"""

import shutil
import subprocess
import sys
from pathlib import Path

import clip

DEFAULT_VIDEOS = {"test1": Path(r"C:\AI\gta\input\test1.webm"), "test2": Path(r"C:\AI\gta\input\test2.webm")}
CHECKS = [  # video, time (s), what is on screen, expected detector output
    ("test1", 180.0, "3:00 pause map", {"minimap": False}),
    ("test1", 170.0, "2:50 body on road, wanted", {"stars": True, "phone": False}),
    ("test1", 510.0, "8:30 phone call", {"stars": False, "minimap": True}),
    ("test1", 500.0, "8:20 phone call (Jimmy)", {"phone": True}),
    ("test2", 1137.0, "18:57 phone call (Lester)", {"phone": True}),
]
EXTRA = [
    ("test1", 60.0, "1:00 minimap flashing red, wanted", {"minimap": True, "stars": True}),
    ("test1", 280.0, "4:40 ammo counter in the star row", {"stars": False, "minimap": True}),
    ("test2", 250.0, "4:10 phone call (unknown number)", {"phone": True}),
    ("test2", 730.0, "12:10 phone call (Dr. Friedlander)", {"phone": True}),
]


def main():
    videos = dict(DEFAULT_VIDEOS)
    for name, arg in zip(videos, sys.argv[1:]):
        videos[name] = Path(arg)
    missing = [str(p) for p in videos.values() if not p.is_file()]
    if missing:
        print(f"SKIP: {', '.join(missing)} not found. These checks need the owner's test footage.")
        sys.exit(2)
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    vf = {}
    for name, video in videos.items():
        info = clip.probe(ffprobe, video)
        range_fix = info["video_codec"] == "vp9" and info["color_range"] == "tv"  # same test as clip.py main()
        vf[name] = clip.hud_filter(info, range_fix)
    ok = True
    for group, checks in (("required", CHECKS), ("extra (calibration frames)", EXTRA)):
        print(f"{group}:")
        for name, t, what, want in checks:
            raw = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}",
                                  "-i", str(videos[name]), "-frames:v", "1", "-vf", vf[name],
                                  "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
            if len(raw) != clip.HUD_FRAME_BYTES:
                print(f"  FAIL {name} {what}: got {len(raw)} bytes, expected {clip.HUD_FRAME_BYTES}")
                ok = False
                continue
            score = clip.star_score(raw)
            got = {"stars": score >= clip.STAR_MIN_SCORE, "minimap": clip.minimap_seen(raw),
                   "phone": clip.phone_seen(raw)}
            good = all(got[k] == v for k, v in want.items())
            ok &= good
            shown = ", ".join(f"{k} {'present' if got[k] else 'absent'}" for k in want)
            m = clip.phone_measure(raw)
            print(f"  {'PASS' if good else 'FAIL'} {name} {what}: {shown}  (star score {score:.3f}, "
                  f"threshold {clip.STAR_MIN_SCORE}; phone face {m['face_max']:.0f}, battery {m['battery_mean']:.0f}, "
                  f"panel {m['panel_min']:.0f}-{m['panel_max']:.0f}, tint {m['panel_tint']})")
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
