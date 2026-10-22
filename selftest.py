"""End-to-end self-test for clip.py on a synthetic video.

Builds a 3-minute 1080p60 video (static picture, quiet noise bed) with loud bursts plus white
flashes planted at 0:40, 1:30 and 2:20, runs clip.py on it, then checks every clip with ffprobe and
saves one frame per clip as a PNG.

Pass = each planted time has a clip whose highest-scoring step is within 5 s of it and whose
time range contains it. Every clip must be 1080x1920 with an audio stream, and its length must
be within the requested range (with a 0.1 s tolerance for frame and AAC rounding). The video has
no GTA V HUD, so clip.py must also switch its HUD rules off by itself.

The HUD detectors are checked on real footage by hudtest.py.

Usage:  python selftest.py [--regen] [extra clip.py flags, e.g. --crop --count 3]
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEST_DIR = Path(r"C:\AI\gta\test")
VIDEO = TEST_DIR / "synthetic_test_3min.mp4"
OUT_ROOT = Path(r"C:\AI\gta\output")
DURATION = 180
EVENTS = [40.0, 90.0, 140.0]
BURST = 1.5        # seconds of noise burst + flashing per event
TOLERANCE = 5.0    # seconds, peak vs planted time
LEN_TOL = 0.1      # seconds, duration rounding tolerance
FRAME_AT = 2.0     # seconds into each clip for the PNG (well before the flash)


def make_video(ffmpeg):
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    events = "+".join(f"between(t,{t},{t + BURST})" for t in EVENTS)
    # Static colour bars, a framed box bottom-left standing in for the minimap, a running timecode,
    # and a full-frame white flash toggling 4 times a second during each event.
    video = (f"smptehdbars=size=1920x1080:rate=60:duration={DURATION},"
             "drawbox=x=40:y=700:w=340:h=340:color=0x1e2a1e:t=fill,"
             "drawbox=x=40:y=700:w=340:h=340:color=white:t=6,"
             "drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':text='%{pts\\:hms}':"
             "x=w-tw-60:y=60:fontsize=64:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=12,"
             f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='({events})*lt(mod(t,0.25),0.125)'")
    # Quiet noise bed (about -40 dBFS) plus loud noise bursts during each event.
    audio = (f"aevalsrc='0.01*(2*random(0)-1)+0.7*(2*random(1)-1)*({events})'"
             f":s=48000:d={DURATION}")
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", video, "-f", "lavfi", "-i", audio,
                    "-map", "0:v", "-map", "1:a", "-ac", "2",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(VIDEO)], check=True)


def ffprobe_clip(ffprobe, path):
    info = json.loads(subprocess.run([ffprobe, "-v", "error", "-show_format", "-show_streams",
                                      "-of", "json", str(path)],
                                     capture_output=True, text=True, check=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = [s for s in info["streams"] if s["codec_type"] == "audio"]
    return {"width": v["width"], "height": v["height"], "video_codec": v["codec_name"],
            "duration": float(info["format"]["duration"]),
            "audio": f"{a[0]['codec_name']} {a[0].get('channels')}ch" if a else None}


def main():
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    args = sys.argv[1:]
    regen = "--regen" in args
    clip_args = [a for a in args if a != "--regen"]

    if regen or not VIDEO.exists():
        print(f"Generating {VIDEO} ...", flush=True)
        t0 = time.perf_counter()
        make_video(ffmpeg)
        print(f"  made in {time.perf_counter() - t0:.1f} s", flush=True)

    print("Running clip.py ...", flush=True)
    t0 = time.perf_counter()
    subprocess.run([sys.executable, str(HERE / "clip.py"), str(VIDEO)] + clip_args, check=True)
    wall = time.perf_counter() - t0

    run_dir = max((OUT_ROOT / VIDEO.stem).iterdir(), key=lambda p: p.stat().st_mtime)
    scores = json.loads((run_dir / "scores.json").read_text(encoding="utf-8"))
    st, clips = scores["settings"], scores["clips"]
    ok = True

    print(f"\nRun folder: {run_dir}")
    hud_state = scores.get("hud", {}).get("state", "missing")
    hud_off = hud_state.startswith("off")
    ok &= hud_off
    print(f"HUD rules: {hud_state} ({'OK, there is no GTA V HUD here' if hud_off else 'BAD, expected off'})")
    print("Planted events:")
    for ev in EVENTS:
        hit = [c for c in clips if abs(c["peak_time_s"] - ev) <= TOLERANCE and c["start_s"] <= ev <= c["end_s"]]
        if hit:
            c = hit[0]
            print(f"  PASS {ev:6.1f} s -> clip {c['rank']} {c['start_s']:.2f}-{c['end_s']:.2f} s, "
                  f"peak {c['peak_time_s']:.2f} s (off by {c['peak_time_s'] - ev:+.2f} s)")
        else:
            ok = False
            print(f"  FAIL {ev:6.1f} s -> no clip with peak within {TOLERANCE} s")

    print("Clip files (ffprobe):")
    frames = run_dir / "frames"
    frames.mkdir(exist_ok=True)
    for c in clips:
        p = ffprobe_clip(ffprobe, run_dir / c["file"])
        good = ((p["width"], p["height"]) == (1080, 1920) and p["audio"] is not None
                and st["min_len"] - LEN_TOL <= p["duration"] <= st["max_len"] + LEN_TOL)
        ok &= good
        print(f"  {'OK  ' if good else 'BAD '} {c['file']}: {p['width']}x{p['height']} {p['video_codec']}, "
              f"{p['duration']:.3f} s, audio {p['audio']}")
        png = frames / (Path(c["file"]).stem + ".png")
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(FRAME_AT),
                        "-i", str(run_dir / c["file"]), "-frames:v", "1", str(png)], check=True)
    print(f"Frames: {frames}")
    print(f"clip.py wall time: {wall:.1f} s; its own timing: {scores['timing_s']}")
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
