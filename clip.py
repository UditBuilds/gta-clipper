"""Gameplay auto-clipper v1.

Finds the loudest, busiest moments in a gameplay recording and cuts them into
vertical 1080x1920 Shorts clips. Python standard library + ffmpeg only.

Usage:  python clip.py <video> [--count N] [--min-len S] [--max-len S] [--min-gap S] [--crop]
"""

import argparse
import csv
import json
import math
import re
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_OUT = Path(r"C:\AI\gta\output")

# --- Scoring constants. All of them are written into scores.json, so runs can be compared. ---
BUCKET = 0.5             # seconds per timeline step
AUDIO_WEIGHT = 0.5       # share of the combined score that comes from loudness
VISUAL_WEIGHT = 0.5      # share that comes from how fast the picture changes
Z_CAP = 6.0              # cap per signal, so one huge spike can't drown out the other signal
AUDIO_SCALE_FLOOR = 1.0  # LU; stops near-constant audio turning tiny wobbles into big scores
VISUAL_SCALE_FLOOR = 0.005
LOUDNESS_FLOOR = -70.0   # LUFS; ebur128 reports about -120 for silence and its 0.4 s warm-up
SMOOTH_SIGMA = 1.0       # seconds; Gaussian smoothing used to find dense stretches of action
MIN_PEAK_SCORE = 2.0     # a clip must contain at least one step scoring this high
LEAD_FRAC = 0.4          # share of a clip's spare time placed before the action (build-up)
HARD_CHANGE = 0.3        # scene score counted as a cut or flash (reported, not scored)
ANALYSIS_FPS = 10        # frames per second compared for visual change
ANALYSIS_WIDTH = 320     # frames are shrunk to this width before comparing
END_MARGIN = 0.1         # seconds kept clear of the end of the file

OUT_W, OUT_H = 1080, 1920


def fail(msg):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(1)


def run(cmd):
    """Run a command, return stdout as text; exit with ffmpeg's last error lines on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        fail(f"command failed ({proc.returncode}): {' '.join(cmd[:6])} ...\n{tail}")
    return proc.stdout


def hms(seconds):
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:04.1f}"


def probe(ffprobe, video):
    info = json.loads(run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)]))
    streams = info.get("streams", [])
    vstreams = [s for s in streams if s.get("codec_type") == "video"]
    if not vstreams:
        fail(f"no video stream in {video}")
    v = vstreams[0]
    num, _, den = v.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den) if float(den or 0) else 0.0
    return {
        "duration": float(info["format"]["duration"]),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": round(fps, 3),
        "video_codec": v.get("codec_name"),
        "color_transfer": v.get("color_transfer"),
        "color_range": v.get("color_range"),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def parse_metadata(text, key):
    """Parse ffmpeg metadata=print output into a list of (time, value)."""
    out, t = [], None
    for line in text.splitlines():
        m = re.search(r"pts_time:(\S+)", line)
        if m:
            t = float(m.group(1))
        elif line.startswith(key + "=") and t is not None:
            raw = line.split("=", 1)[1].strip()
            try:
                out.append((t, float(raw)))
            except ValueError:
                pass  # "-inf" / "nan"
    return out


def analyse_audio(ffmpeg, video):
    """Momentary loudness (EBU R128, 400 ms window) every 100 ms."""
    text = run([ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-i", str(video),
                "-map", "0:a:0", "-vn", "-sn", "-dn",
                "-af", "ebur128=metadata=1,ametadata=print:key=lavfi.r128.M:file=-",
                "-f", "null", "-"])
    return parse_metadata(text, "lavfi.r128.M")


def analyse_video(ffmpeg, video):
    """Frame-to-frame change score (0..1) on small frames sampled at ANALYSIS_FPS."""
    vf = (f"fps={ANALYSIS_FPS},scale={ANALYSIS_WIDTH}:-2,"
          "select='gte(scene,0)',metadata=print:key=lavfi.scene_score:file=-")
    text = run([ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-i", str(video),
                "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", vf, "-f", "null", "-"])
    return parse_metadata(text, "lavfi.scene_score")


def bucketize(samples, n, reduce_fn, empty):
    groups = [[] for _ in range(n)]
    for t, v in samples:
        i = int(t // BUCKET)
        if 0 <= i < n:
            groups[i].append(v)
    return [reduce_fn(g) if g else empty for g in groups]


def robust_z(values, floor):
    """Score each value by how far above the video's own median it is, in robust standard deviations."""
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    scale = max(1.4826 * mad, floor)
    return [min(Z_CAP, max(0.0, (v - med) / scale)) for v in values], med, scale


def gaussian_smooth(values, sigma_steps):
    radius = max(1, math.ceil(3 * sigma_steps))
    kernel = [math.exp(-0.5 * (k / sigma_steps) ** 2) for k in range(-radius, radius + 1)]
    n, out = len(values), []
    for i in range(n):
        acc = wsum = 0.0
        for k, w in enumerate(kernel):
            j = i + k - radius
            if 0 <= j < n:
                acc += w * values[j]
                wsum += w
        out.append(acc / wsum)
    return out


def pick_clips(combined, smooth, usable_end, count, min_len, max_len, min_gap):
    """Greedy: best remaining peak first; each clip keeps min_gap seconds clear on both sides."""
    n = len(smooth)
    baseline = statistics.median(smooth)
    taken, rejected, notes = [], [False] * n, []
    for p in sorted(range(n), key=lambda i: smooth[i], reverse=True):
        if len(taken) >= count:
            break
        if rejected[p]:
            continue
        tp = (p + 0.5) * BUCKET
        if tp >= usable_end:
            continue
        # Free stretch around this peak, between already-picked clips (plus gap) and the file ends.
        fa, fb, blocked = 0.0, usable_end, False
        for c in taken:
            s, e = c["start_s"] - min_gap, c["end_s"] + min_gap
            if s < tp < e:
                blocked = True
                break
            if e <= tp:
                fa = max(fa, e)
            if s >= tp:
                fb = min(fb, s)
        if blocked:
            continue
        if fb - fa < min_len:
            for i in range(int(fa // BUCKET), min(n, int(fb // BUCKET) + 1)):
                rejected[i] = True
            continue
        # Event span: neighbouring steps whose smoothed score is at least halfway up the peak.
        half = baseline + 0.5 * (smooth[p] - baseline)
        a = b = p
        while a - 1 >= 0 and smooth[a - 1] >= half and (a - 1) * BUCKET >= fa:
            a -= 1
        while b + 1 < n and smooth[b + 1] >= half and (b + 2) * BUCKET <= fb:
            b += 1
        if max(combined[a:b + 1]) < MIN_PEAK_SCORE:
            for i in range(a, b + 1):
                rejected[i] = True
            continue
        span_s, span_e = a * BUCKET, min((b + 1) * BUCKET, fb)
        span = span_e - span_s
        length = min(max(span, min_len), max_len, fb - fa)
        if span > length:
            # Long action: keep the densest `length` seconds of it.
            steps = int(round(length / BUCKET))
            best_i, best_sum = a, -1.0
            for i in range(a, b - steps + 2):
                s = sum(combined[i:i + steps])
                if s > best_sum:
                    best_i, best_sum = i, s
            start = best_i * BUCKET
        else:
            start = span_s - LEAD_FRAC * (length - span)
        start = round(min(max(start, fa), fb - length), 2)
        taken.append({"start_s": start, "end_s": round(start + length, 2), "duration_s": round(length, 2),
                      "smoothed_peak_time_s": round(tp, 2), "smoothed_peak_score": round(smooth[p], 3),
                      "event_span_s": [round(span_s, 2), round(span_e, 2)]})
    if len(taken) < count:
        notes.append(f"asked for {count} clips, found {len(taken)}: no other stretch had a step scoring "
                     f">= {MIN_PEAK_SCORE} with room for a {min_len:g} s clip and {min_gap:g} s gaps")
    return taken, baseline, notes


def explain(clip, tl, stats):
    i0 = int(clip["start_s"] // BUCKET)
    i1 = min(len(tl), int(math.ceil(clip["end_s"] / BUCKET)))
    rows = tl[i0:i1]
    peak = max(rows, key=lambda r: r["combined"])
    clip.update({
        "peak_time_s": peak["time_s"],
        "peak_score": round(peak["combined"], 3),
        "mean_score": round(statistics.mean(r["combined"] for r in rows), 3),
        "peak_loudness_lufs": peak["loudness_lufs"],
        "peak_audio_z": round(peak["audio_z"], 2),
        "peak_visual_change": peak["visual_change"],
        "peak_visual_z": round(peak["visual_z"], 2),
        "hard_changes": sum(r["hard_changes"] for r in rows),
    })
    parts = [f"peak at {hms(peak['time_s'])} scored {peak['combined']:.2f} (floor {MIN_PEAK_SCORE})"]
    if stats["has_audio"]:
        parts.append(f"loudness {peak['loudness_lufs']:.1f} LUFS vs median {stats['audio_median']:.1f} "
                     f"(z {peak['audio_z']:.1f}{' capped' if peak['audio_z'] >= Z_CAP else ''})")
    parts.append(f"visual change {peak['visual_change']:.3f} vs median {stats['visual_median']:.3f} "
                 f"(z {peak['visual_z']:.1f}{' capped' if peak['visual_z'] >= Z_CAP else ''})")
    parts.append(f"{clip['hard_changes']} hard cuts/flashes (scene > {HARD_CHANGE}) in clip")
    parts.append(f"event span {hms(clip['event_span_s'][0])}-{hms(clip['event_span_s'][1])}")
    clip["reason"] = "; ".join(parts)


def pick_encoder(ffmpeg):
    """h264_nvenc if a real 1-frame test encode works, else libx264."""
    test = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                           "-i", "color=black:s=256x256:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"],
                          capture_output=True)
    if test.returncode == 0:
        return "h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq",
                              "-rc", "vbr", "-cq", "21", "-b:v", "0", "-profile:v", "high"]
    return "libx264", ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-profile:v", "high"]


def vertical_filter(crop):
    if crop:
        return (f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
                f"crop={OUT_W}:{OUT_H},setsar=1,format=yuv420p[v]")
    # Full frame centred over a blurred, zoomed copy. The blur runs at 1/4 size to stay fast.
    bw, bh = OUT_W // 4, OUT_H // 4
    return (f"[0:v]split=2[bg][fg];"
            f"[bg]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"gblur=sigma=6,scale={OUT_W}:{OUT_H}[bgb];"
            f"[fg]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v]")


def render(ffmpeg, video, clip, out_path, crop, enc_args, has_audio, range_fix):
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-y"]
    if range_fix:
        cmd += ["-bsf:v", "vp9_metadata=color_range=tv"]
    cmd += ["-ss", f"{clip['start_s']:.3f}", "-i", str(video), "-t", f"{clip['duration_s']:.3f}",
            "-filter_complex", vertical_filter(crop), "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += enc_args + ["-movflags", "+faststart", str(out_path)]
    run(cmd)


def main():
    ap = argparse.ArgumentParser(description="Cut the most action-heavy moments of a gameplay video "
                                             "into vertical 1080x1920 clips.")
    ap.add_argument("video", type=Path)
    ap.add_argument("--count", type=int, default=5, help="clips to make (default 5)")
    ap.add_argument("--min-len", type=float, default=20.0, help="shortest clip, seconds (default 20)")
    ap.add_argument("--max-len", type=float, default=30.0, help="longest clip, seconds (default 30)")
    ap.add_argument("--min-gap", type=float, default=10.0,
                    help="seconds between the end of one clip and the start of the next (default 10)")
    ap.add_argument("--crop", action="store_true", help="plain centre crop instead of blurred background")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output root (default {DEFAULT_OUT})")
    args = ap.parse_args()

    if not args.video.is_file():
        fail(f"video not found: {args.video}")
    if args.count < 1 or args.min_len <= 0 or args.min_gap < 0 or args.max_len < args.min_len:
        fail("need --count >= 1, --min-len > 0, --min-gap >= 0 and --max-len >= --min-len")
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        fail("ffmpeg/ffprobe not found on PATH (install: winget install Gyan.FFmpeg)")

    t_start = time.perf_counter()
    info = probe(ffprobe, args.video)
    usable_end = info["duration"] - END_MARGIN
    if usable_end < args.min_len:
        fail(f"video is {info['duration']:.1f} s, shorter than --min-len {args.min_len:g}")
    print(f"Input: {args.video.name}  {info['width']}x{info['height']} {info['fps']} fps  "
          f"{hms(info['duration'])} ({info['duration']:.1f} s)")
    if info["color_transfer"] in ("smpte2084", "arib-std-b67"):
        print("WARNING: input is HDR. v1 encodes SDR without tone mapping, so colours may look washed out.")
    # PS5 VP9 captures: the container says limited range but the VP9 header says full range, and ffmpeg
    # follows the header. Clips then come out flagged full range and look washed out, so trust the container.
    range_fix = info["video_codec"] == "vp9" and info["color_range"] == "tv"
    info["vp9_range_relabel"] = range_fix
    if range_fix:
        print("Input is VP9 marked limited range by its container: clips will be flagged limited range too.")

    n = int(math.ceil(info["duration"] / BUCKET))
    t0 = time.perf_counter()
    if info["has_audio"]:
        print("Analysing audio loudness...")
        loud = bucketize(analyse_audio(ffmpeg, args.video), n,
                         lambda g: max(LOUDNESS_FLOOR, max(g)), LOUDNESS_FLOOR)
    else:
        print("WARNING: no audio stream; scoring on picture changes only, clips will be silent.")
        loud = [LOUDNESS_FLOOR] * n
    t_audio = time.perf_counter() - t0

    t0 = time.perf_counter()
    print("Analysing picture changes...")
    scene = analyse_video(ffmpeg, args.video)
    vis = bucketize(scene, n, statistics.mean, 0.0)
    hard = bucketize(scene, n, lambda g: sum(1 for v in g if v > HARD_CHANGE), 0)
    t_video = time.perf_counter() - t0

    wa, wv = (AUDIO_WEIGHT, VISUAL_WEIGHT) if info["has_audio"] else (0.0, 1.0)
    za, a_med, a_scale = robust_z(loud, AUDIO_SCALE_FLOOR)
    zv, v_med, v_scale = robust_z(vis, VISUAL_SCALE_FLOOR)
    combined = [wa * x + wv * y for x, y in zip(za, zv)]
    smooth = gaussian_smooth(combined, SMOOTH_SIGMA / BUCKET)
    tl = [{"time_s": round(i * BUCKET, 2), "loudness_lufs": round(loud[i], 2), "audio_z": za[i],
           "visual_change": round(vis[i], 4), "hard_changes": hard[i], "visual_z": zv[i],
           "combined": combined[i], "smoothed": smooth[i]} for i in range(n)]

    clips, baseline, notes = pick_clips(combined, smooth, usable_end, args.count,
                                        args.min_len, args.max_len, args.min_gap)
    stats = {"has_audio": info["has_audio"], "audio_median": a_med, "visual_median": v_med}
    for clip in clips:
        explain(clip, tl, stats)

    run_dir = args.out / args.video.stem / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    encoder, enc_args = pick_encoder(ffmpeg)
    print(f"Encoder: {encoder}. Writing {len(clips)} clip(s) to {run_dir}")
    t0 = time.perf_counter()
    for rank, clip in enumerate(clips, 1):
        m, s = divmod(int(clip["start_s"]), 60)
        name = f"clip_{rank:02d}_{m}m{s:02d}s.mp4"
        clip["rank"], clip["file"] = rank, name
        print(f"  [{rank}] {hms(clip['start_s'])}-{hms(clip['end_s'])} ({clip['duration_s']:.1f} s)  "
              f"peak {hms(clip['peak_time_s'])}  score {clip['peak_score']:.2f}")
        render(ffmpeg, args.video, clip, run_dir / name, args.crop, enc_args, info["has_audio"], range_fix)
    t_encode = time.perf_counter() - t0

    for note in notes:
        print("NOTE: " + note)
    in_clip = {}
    for clip in clips:
        for i in range(int(clip["start_s"] // BUCKET), min(n, int(math.ceil(clip["end_s"] / BUCKET)))):
            in_clip[i] = clip["rank"]
    with open(run_dir / "timeline.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "loudness_lufs", "audio_z", "visual_change", "hard_changes",
                    "visual_z", "combined", "smoothed", "clip"])
        for i, r in enumerate(tl):
            w.writerow([r["time_s"], r["loudness_lufs"], f"{r['audio_z']:.3f}", r["visual_change"],
                        r["hard_changes"], f"{r['visual_z']:.3f}", f"{r['combined']:.3f}",
                        f"{r['smoothed']:.3f}", in_clip.get(i, "")])

    total = time.perf_counter() - t_start
    scores = {
        "tool": "gta-clipper v1",
        "created": datetime.now().isoformat(timespec="seconds"),
        "input": str(args.video.resolve()),
        "input_info": info,
        "settings": {"count": args.count, "min_len": args.min_len, "max_len": args.max_len,
                     "min_gap": args.min_gap, "crop": args.crop, "encoder": encoder},
        "scoring": {"bucket_s": BUCKET, "audio_weight": wa, "visual_weight": wv, "z_cap": Z_CAP,
                    "audio_scale_floor": AUDIO_SCALE_FLOOR, "visual_scale_floor": VISUAL_SCALE_FLOOR,
                    "loudness_floor_lufs": LOUDNESS_FLOOR, "smooth_sigma_s": SMOOTH_SIGMA,
                    "min_peak_score": MIN_PEAK_SCORE, "lead_frac": LEAD_FRAC, "hard_change": HARD_CHANGE,
                    "analysis_fps": ANALYSIS_FPS, "analysis_width": ANALYSIS_WIDTH,
                    "audio_median_lufs": round(a_med, 2), "audio_scale_lu": round(a_scale, 3),
                    "visual_median": round(v_med, 4), "visual_scale": round(v_scale, 4),
                    "smoothed_baseline": round(baseline, 3)},
        "timing_s": {"audio_analysis": round(t_audio, 2), "video_analysis": round(t_video, 2),
                     "encode": round(t_encode, 2), "total": round(total, 2)},
        "notes": notes,
        "clips": clips,
    }
    (run_dir / "scores.json").write_text(json.dumps(scores, indent=2), encoding="utf-8")
    print(f"Done in {total:.1f} s (audio {t_audio:.1f}, picture {t_video:.1f}, encode {t_encode:.1f}). "
          f"Scores: {run_dir / 'scores.json'}")


if __name__ == "__main__":
    main()
