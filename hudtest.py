"""Known-frame checks for clip.py's HUD detectors, on the owner's GTA V PS5 capture test1.webm.

The footage is not in the repo. Each check grabs one frame, runs it through clip.py's own HUD filter chain
and detectors, and compares with what the frame is known to show:

  test1 3:00  pause map                 -> minimap absent
  test1 2:50  body on road, wanted      -> stars present
  test1 8:30  phone call                -> stars absent, minimap present

Two extra frames guard the hard cases (both were used to set the detectors, so they are regression checks only):
  test1 1:00  minimap flashing red while wanted -> minimap present, stars present
  test1 4:40  ammo counter sits in the star row -> stars absent, minimap present

Single frames are checked, so the 2-of-3 smoothing clip.py applies to stars is not involved here.
Exit code 0 = all pass, 1 = a check failed, 2 = footage missing (SKIP, never a pass).

Usage:  python hudtest.py [path\\to\\test1.webm]
"""

import shutil
import subprocess
import sys
from pathlib import Path

import clip

DEFAULT_VIDEO = Path(r"C:\AI\gta\input\test1.webm")
CHECKS = [  # time (s), what is on screen, expected detector output
    (180.0, "3:00 pause map", {"minimap": False}),
    (170.0, "2:50 body on road, wanted", {"stars": True}),
    (510.0, "8:30 phone call", {"stars": False, "minimap": True}),
]
EXTRA = [
    (60.0, "1:00 minimap flashing red, wanted", {"minimap": True, "stars": True}),
    (280.0, "4:40 ammo counter in the star row", {"stars": False, "minimap": True}),
]


def main():
    video = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_VIDEO
    if not video.is_file():
        print(f"SKIP: {video} not found. These checks need the owner's test footage.")
        sys.exit(2)
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    info = clip.probe(ffprobe, video)
    range_fix = info["video_codec"] == "vp9" and info["color_range"] == "tv"  # same test as clip.py main()
    vf = clip.hud_filter(info, range_fix)
    ok = True
    for group, checks in (("required", CHECKS), ("extra (calibration frames)", EXTRA)):
        print(f"{group}:")
        for t, what, want in checks:
            raw = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video),
                                  "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-"],
                                 capture_output=True, check=True).stdout
            if len(raw) != clip.HUD_FRAME_BYTES:
                print(f"  FAIL {what}: got {len(raw)} bytes, expected {clip.HUD_FRAME_BYTES}")
                ok = False
                continue
            score = clip.star_score(raw)
            got = {"stars": score >= clip.STAR_MIN_SCORE, "minimap": clip.minimap_seen(raw)}
            good = all(got[k] == v for k, v in want.items())
            ok &= good
            shown = ", ".join(f"{k} {'present' if got[k] else 'absent'}" for k in want)
            print(f"  {'PASS' if good else 'FAIL'} {what}: {shown}  (star score {score:.3f}, "
                  f"threshold {clip.STAR_MIN_SCORE})")
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
