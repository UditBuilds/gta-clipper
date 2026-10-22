"""Gameplay auto-clipper v3.

Finds the loudest, busiest moments in a gameplay recording and cuts them into
vertical 1080x1920 Shorts clips. Python standard library + ffmpeg only.

v2 also reads GTA V's HUD (PS5 layout): stretches where the minimap is gone (pause/map screens,
cutscenes) can't be picked, and steps where wanted stars show get a strong score boost.
v3 splits the video into action sequences and takes at most MAX_PER_SEQ clips from each.

Usage:  python clip.py <video> [--count N] [--min-len S] [--max-len S] [--min-gap S] [--crop] [--no-hud]
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
import tempfile
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

# --- HUD rules (v2), GTA V on PS5. Boxes are pixels of a 1920x1088 capture and scale with the frame. ---
HUD_REF_W, HUD_REF_H = 1920, 1088
HUD_FPS = 2              # HUD samples per second: one per timeline step
STAR_BOX = (1676, 46, 152, 42)   # x, y, w, h around the five wanted-star slots
STAR_TIPS_X = tuple(1696.5 + 28.6 * k for k in range(5))  # top point of each star slot
STAR_TIP_Y = 54.0        # top point of every star
STAR_RADIUS = 15.5       # star centre to tip
STAR_INNER = 0.42        # inner corner radius / tip radius
STAR_MIN_SCORE = 0.28    # outline match (correlation, -1..1) at which stars count as showing
BAR_BOX = (96, 1014, 272, 6)     # health, armour and special-ability bars under the minimap
BAR_SEGMENTS = ((104, 224), (240, 290), (306, 360))  # x ranges of those three bars
BAR_MIN_SAT = 0.3        # a bar is a clearly coloured strip, not grey
BAR_MIN_DISTINCT = 60.0  # RGB distance between passing bars, so one plain colour can't pass as two bars
MIN_HIDDEN = 1.5         # seconds the minimap must be missing before that stretch is excluded
HIDDEN_PAD = 0.5         # excluded stretches grow this much on each side (menu and cutscene fades)
STAR_BOOST = 3.0         # added to a step's combined score while wanted stars show
HUD_MIN_SHARE = 0.2      # minimap seen in less of the video than this: not this HUD, so HUD rules are off

# --- Spreading picks (v3). Chosen before looking at any eval result; not tuned to the test videos. ---
SEQ_QUIET = 20.0         # seconds with no step reaching MIN_PEAK_SCORE that end an action sequence
MAX_PER_SEQ = 2          # most clips taken from one action sequence

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


def star_template():
    """Soft outline of the five star slots over STAR_BOX, mean-centred and scaled to unit length."""
    x0, y0, w, h = STAR_BOX
    edges = []
    for tx in STAR_TIPS_X:
        cx, cy = tx, STAR_TIP_Y + STAR_RADIUS
        pts = [(cx + r * math.cos(a), cy + r * math.sin(a))
               for i in range(10)
               for a, r in [(-math.pi / 2 + i * math.pi / 5, STAR_RADIUS if i % 2 == 0 else STAR_RADIUS * STAR_INNER)]]
        edges += [(pts[i], pts[(i + 1) % 10]) for i in range(10)]

    def dist(px, py, a, b):
        (ax, ay), (bx, by) = a, b
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - ax - t * dx, py - ay - t * dy)

    t = [math.exp(-(min(dist(x0 + x + 0.5, y0 + y + 0.5, a, b) for a, b in edges) / 1.5) ** 2)
         for y in range(h) for x in range(w)]
    mean = sum(t) / len(t)
    norm = math.sqrt(sum((v - mean) ** 2 for v in t))
    return [(v - mean) / norm for v in t]


STAR_TEMPLATE = star_template()
HUD_W = max(STAR_BOX[2], BAR_BOX[2])
HUD_FRAME_BYTES = HUD_W * (STAR_BOX[3] + BAR_BOX[3]) * 3


def hud_filter(info, range_fix):
    """Filter chain: one frame in, the star box stacked on the bar box out, RGB at 1920x1088 scale."""
    sx, sy = info["width"] / HUD_REF_W, info["height"] / HUD_REF_H
    # Same range handling as make_review.py: PS5 VP9 pixels are limited range whatever the header says.
    conv = "in_color_matrix=bt709:in_range=tv:" if range_fix else ""
    parts = [f"[{tag}0]crop={round(w * sx)}:{round(h * sy)}:{round(x * sx)}:{round(y * sy)},"
             f"scale={w}:{h}:{conv}out_range=pc,format=rgb24,pad={HUD_W}:{h}[{tag}1]"
             for tag, (x, y, w, h) in (("s", STAR_BOX), ("b", BAR_BOX))]
    return "split=2[s0][b0];" + ";".join(parts) + ";[s1][b1]vstack"


def star_score(rgb):
    """How well edges in STAR_BOX line up with the star outlines (correlation, about 0 when no stars).

    Edges are used rather than brightness because filled (white), empty (grey) and flashing stars all
    have the same dark outline, and the ammo/cash text that sits in this row when you are not wanted
    has different shapes.
    """
    w, h = STAR_BOX[2], STAR_BOX[3]
    lum = [0.2126 * rgb[i] + 0.7152 * rgb[i + 1] + 0.0722 * rgb[i + 2]
           for y in range(h) for i in range(y * HUD_W * 3, (y * HUD_W + w) * 3, 3)]
    g = [0.0] * (w * h)
    for y in range(1, h - 1):
        for i in range(y * w + 1, y * w + w - 1):
            g[i] = abs(lum[i + 1] - lum[i - 1]) + abs(lum[i + w] - lum[i - w])
    mean = sum(g) / len(g)
    sd = math.sqrt(sum((v - mean) ** 2 for v in g))
    return sum(a * b for a, b in zip(STAR_TEMPLATE, g)) / sd if sd > 1e-6 else 0.0


def minimap_seen(rgb):
    """True when the health / armour / special-ability bars under the minimap are on screen.

    The minimap itself flashes red and blue while wanted, so the bars are checked instead. At least two
    must show their own colour (health green, or red when low; armour blue; ability yellow), and those
    must differ from each other, so a plain orange or beige background can't pass as two bars.
    """
    off = STAR_BOX[3] * HUD_W * 3
    x0, _, _, h = BAR_BOX
    means = []
    for a, b in BAR_SEGMENTS:
        acc = [0, 0, 0]
        for y in range(h):
            row = off + y * HUD_W * 3
            for i in range(row + (a - x0) * 3, row + (b - x0) * 3, 3):
                acc[0] += rgb[i]
                acc[1] += rgb[i + 1]
                acc[2] += rgb[i + 2]
        means.append([v / (h * (b - a)) for v in acc])

    def sat(c):
        return (max(c) - min(c)) / max(max(c), 1.0)

    (hr, hg, hb), (ar, ag, ab), (yr, yg, yb) = means
    checks = [(hg - max(hr, hb) >= 25) or (hr - max(hg, hb) >= 25),  # health
              ab - ar >= 40 and ag - ar >= 15,                        # armour
              yr - yb >= 40 and yg - yb >= 25]                        # special ability
    ok = [c for c, passed in zip(means, checks) if passed and sat(c) >= BAR_MIN_SAT]
    return len(ok) >= 2 and max(math.dist(p, q) for i, p in enumerate(ok) for q in ok[i + 1:]) >= BAR_MIN_DISTINCT


def analyse_video(ffmpeg, video, info, range_fix):
    """One decode for both picture signals.

    Returns the frame-to-frame change score (0..1) on small frames at ANALYSIS_FPS, the HUD samples
    (time, star score, minimap seen) at HUD_FPS, and the seconds spent on HUD pixel work.
    """
    graph = (f"[0:v:0]split=2[vs][vh];"
             f"[vs]fps={ANALYSIS_FPS},scale={ANALYSIS_WIDTH}:-2,"
             f"select='gte(scene,0)',metadata=print:key=lavfi.scene_score:file=scene.txt[scene];"
             f"[vh]fps={HUD_FPS},{hud_filter(info, range_fix)}[hud]")
    hud, t_px = [], 0.0
    with tempfile.TemporaryDirectory() as tmp:
        # The scene scores go to a file in tmp (relative path: no drive-letter escaping in the filter),
        # the HUD crops come through stdout and are scored as they arrive.
        with open(Path(tmp) / "stderr.txt", "w+", encoding="utf-8", errors="replace") as err:
            proc = subprocess.Popen([ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error",
                                     "-i", str(video.resolve()), "-filter_complex", graph,
                                     "-map", "[scene]", "-f", "null", "-",
                                     "-map", "[hud]", "-f", "rawvideo", "pipe:1"],
                                    stdout=subprocess.PIPE, stderr=err, cwd=tmp)
            while True:
                raw = proc.stdout.read(HUD_FRAME_BYTES)
                if len(raw) < HUD_FRAME_BYTES:
                    break
                t0 = time.perf_counter()
                hud.append((len(hud) / HUD_FPS, star_score(raw), minimap_seen(raw)))
                t_px += time.perf_counter() - t0
            proc.stdout.close()
            if proc.wait() != 0:
                err.seek(0)
                tail = "\n".join(err.read().strip().splitlines()[-15:])
                fail(f"picture analysis failed ({proc.returncode}):\n{tail}")
        text = (Path(tmp) / "scene.txt").read_text(encoding="utf-8", errors="replace")
    return parse_metadata(text, "lavfi.scene_score"), hud, t_px


def hud_steps(hud, n):
    """Per timeline step: star score, stars showing (2 of 3 neighbouring samples), minimap seen, excluded."""
    score = bucketize([(t, s) for t, s, _ in hud], n, max, None)
    seen = bucketize([(t, m) for t, _, m in hud], n, lambda g: 2 * sum(g) >= len(g), None)
    raw = [s is not None and s >= STAR_MIN_SCORE for s in score]
    stars = [sorted((raw[max(0, i - 1)], raw[i], raw[min(n - 1, i + 1)]))[1] for i in range(n)]
    # Missing minimap for MIN_HIDDEN seconds or more: exclude that stretch, grown by HIDDEN_PAD each side.
    # Steps with no sample (the last fraction of a second) count as seen.
    excluded, i = [False] * n, 0
    need, pad = round(MIN_HIDDEN / BUCKET), round(HIDDEN_PAD / BUCKET)
    while i < n:
        j = i
        while j < n and seen[j] is False:
            j += 1
        if j - i >= need:
            for k in range(max(0, i - pad), min(n, j + pad)):
                excluded[k] = True
        i = max(j, i + 1)
    return score, stars, [s is not False for s in seen], excluded


def runs(flags):
    """[(start_s, end_s)] for each run of True steps."""
    out, i = [], 0
    while i < len(flags):
        if flags[i]:
            j = i
            while j < len(flags) and flags[j]:
                j += 1
            out.append((round(i * BUCKET, 2), round(j * BUCKET, 2)))
            i = j
        else:
            i += 1
    return out


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


def action_sequences(combined, excluded):
    """Sequence number per step (None for quiet and excluded steps), and each sequence's (start_s, end_s).

    A step is active when its combined score reaches MIN_PEAK_SCORE, the score a clip needs somewhere in it.
    A sequence is a run of active steps; it ends at an excluded stretch or at SEQ_QUIET seconds without an
    active step. Shorter lulls stay inside the sequence.
    """
    seq, spans, last = [None] * len(combined), [], None
    gap = round(SEQ_QUIET / BUCKET)
    for i, (c, ex) in enumerate(zip(combined, excluded)):
        if ex:
            last = None
            continue
        if c < MIN_PEAK_SCORE:
            continue
        if last is None or i - last - 1 >= gap:
            spans.append([i, i])
        seq[i], spans[-1][1], last = len(spans) - 1, i, i
    return seq, [(round(a * BUCKET, 2), round((b + 1) * BUCKET, 2)) for a, b in spans]


def pick_clips(combined, smooth, usable_end, count, min_len, max_len, min_gap, excluded, seq):
    """Greedy: best remaining peak first; each clip keeps min_gap seconds clear on both sides.

    Excluded steps (no minimap) are hard walls: no peak inside them, and no clip reaches into them.
    A clip belongs to the action sequence of its highest-scoring step; once a sequence has MAX_PER_SEQ
    clips, further peaks in it are skipped.
    """
    n = len(smooth)
    baseline = statistics.median(smooth)
    taken, rejected, notes = [], [False] * n, []
    per_seq, seq_full = {}, False
    for p in sorted(range(n), key=lambda i: smooth[i], reverse=True):
        if len(taken) >= count:
            break
        if rejected[p] or excluded[p]:
            continue
        tp = (p + 0.5) * BUCKET
        if tp >= usable_end:
            continue
        # Free stretch around this peak, between excluded steps, already-picked clips (plus gap) and the file ends.
        lo = hi = p
        while lo > 0 and not excluded[lo - 1]:
            lo -= 1
        while hi + 1 < n and not excluded[hi + 1]:
            hi += 1
        fa, fb, blocked = lo * BUCKET, min(usable_end, (hi + 1) * BUCKET), False
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
        q = max(range(a, b + 1), key=lambda i: combined[i])
        if per_seq.get(seq[q], 0) >= MAX_PER_SEQ:
            seq_full = True
            continue
        per_seq[seq[q]] = per_seq.get(seq[q], 0) + 1
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
                      "event_span_s": [round(span_s, 2), round(span_e, 2)], "sequence": seq[q] + 1})
    if len(taken) < count:
        notes.append(f"asked for {count} clips, found {len(taken)}: no other stretch had a step scoring "
                     f">= {MIN_PEAK_SCORE} with room for a {min_len:g} s clip and {min_gap:g} s gaps"
                     + (" outside the stretches with no minimap" if any(excluded) else "")
                     + (f" in an action sequence with fewer than {MAX_PER_SEQ} clips" if seq_full else ""))
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
        "stars_s": sum(BUCKET for r in rows if r["stars"]),
        "excluded_s": sum(BUCKET for r in rows if r["excluded"]),
        "peak_star_boost": peak["star_boost"],
    })
    parts = [f"peak at {hms(peak['time_s'])} scored {peak['combined']:.2f} (floor {MIN_PEAK_SCORE})"]
    if stats["hud"]:
        parts.append(f"wanted stars in {clip['stars_s']:g} s of the clip"
                     + (f", peak step boosted +{STAR_BOOST:g}" if peak["star_boost"] else ""))
    if stats["has_audio"]:
        parts.append(f"loudness {peak['loudness_lufs']:.1f} LUFS vs median {stats['audio_median']:.1f} "
                     f"(z {peak['audio_z']:.1f}{' capped' if peak['audio_z'] >= Z_CAP else ''})")
    parts.append(f"visual change {peak['visual_change']:.3f} vs median {stats['visual_median']:.3f} "
                 f"(z {peak['visual_z']:.1f}{' capped' if peak['visual_z'] >= Z_CAP else ''})")
    parts.append(f"{clip['hard_changes']} hard cuts/flashes (scene > {HARD_CHANGE}) in clip")
    parts.append(f"event span {hms(clip['event_span_s'][0])}-{hms(clip['event_span_s'][1])}")
    a, b = stats["sequences"][clip["sequence"] - 1]
    parts.append(f"action sequence {clip['sequence']} ({hms(a)}-{hms(b)})")
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
    ap.add_argument("--no-hud", action="store_true",
                    help="ignore the GTA V HUD (no exclusions, no wanted-star boost): scores like v1")
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
    print("Analysing picture changes and HUD...")
    scene, hud, t_hud_px = analyse_video(ffmpeg, args.video, info, range_fix)
    vis = bucketize(scene, n, statistics.mean, 0.0)
    hard = bucketize(scene, n, lambda g: sum(1 for v in g if v > HARD_CHANGE), 0)
    t_video = time.perf_counter() - t0

    star_sc, stars, seen, excluded = hud_steps(hud, n)
    seen_share = sum(1 for _, _, m in hud if m) / len(hud) if hud else 0.0
    if args.no_hud:
        hud_on, hud_state = False, "off (--no-hud)"
    elif seen_share < HUD_MIN_SHARE:
        hud_on, hud_state = False, "off (no GTA V minimap found)"
        print(f"WARNING: no GTA V minimap where expected ({seen_share:.0%} of samples, needs "
              f"{HUD_MIN_SHARE:.0%}). HUD rules are off; scoring falls back to loudness + picture change.")
    else:
        hud_on, hud_state = True, "on"
    if not hud_on:
        excluded = [False] * n
    boost = [STAR_BOOST if hud_on and s else 0.0 for s in stars]
    excluded_runs = runs(excluded)
    print(f"HUD rules {hud_state}. Minimap seen in {seen_share:.0%} of samples, wanted stars in "
          f"{sum(stars) / n:.0%} of steps"
          + (f"; {len(excluded_runs)} stretch(es) with no minimap excluded "
             f"({sum(excluded) * BUCKET:.0f} s)" if hud_on else ""))

    wa, wv = (AUDIO_WEIGHT, VISUAL_WEIGHT) if info["has_audio"] else (0.0, 1.0)
    za, a_med, a_scale = robust_z(loud, AUDIO_SCALE_FLOOR)
    zv, v_med, v_scale = robust_z(vis, VISUAL_SCALE_FLOOR)
    # Excluded steps score 0, so they can't pull a smoothed peak towards them either.
    combined = [0.0 if excluded[i] else wa * za[i] + wv * zv[i] + boost[i] for i in range(n)]
    smooth = gaussian_smooth(combined, SMOOTH_SIGMA / BUCKET)
    seq, seq_spans = action_sequences(combined, excluded)
    tl = [{"time_s": round(i * BUCKET, 2), "loudness_lufs": round(loud[i], 2), "audio_z": za[i],
           "visual_change": round(vis[i], 4), "hard_changes": hard[i], "visual_z": zv[i],
           "combined": combined[i], "smoothed": smooth[i], "star_score": star_sc[i], "stars": stars[i],
           "minimap": seen[i], "excluded": excluded[i], "star_boost": boost[i], "sequence": seq[i]}
          for i in range(n)]
    print(f"Action sequences: {len(seq_spans)} (split at {SEQ_QUIET:g} s quiet gaps and excluded stretches); "
          f"at most {MAX_PER_SEQ} clips each")

    clips, baseline, notes = pick_clips(combined, smooth, usable_end, args.count,
                                        args.min_len, args.max_len, args.min_gap, excluded, seq)
    stats = {"has_audio": info["has_audio"], "audio_median": a_med, "visual_median": v_med, "hud": hud_on,
             "sequences": seq_spans}
    for clip in clips:
        explain(clip, tl, stats)
        if clip["excluded_s"] > 0:
            fail(f"internal error: clip {hms(clip['start_s'])}-{hms(clip['end_s'])} reaches into a "
                 f"no-minimap stretch ({clip['excluded_s']:g} s)")

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
              f"peak {hms(clip['peak_time_s'])}  score {clip['peak_score']:.2f}  sequence {clip['sequence']}")
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
                    "visual_z", "combined", "smoothed", "clip",
                    "star_score", "stars", "minimap", "excluded", "sequence"])
        for i, r in enumerate(tl):
            w.writerow([r["time_s"], r["loudness_lufs"], f"{r['audio_z']:.3f}", r["visual_change"],
                        r["hard_changes"], f"{r['visual_z']:.3f}", f"{r['combined']:.3f}",
                        f"{r['smoothed']:.3f}", in_clip.get(i, ""),
                        "" if r["star_score"] is None else f"{r['star_score']:.3f}",
                        int(r["stars"]), int(r["minimap"]), int(r["excluded"]),
                        "" if r["sequence"] is None else r["sequence"] + 1])

    total = time.perf_counter() - t_start
    scores = {
        "tool": "gta-clipper v3",
        "created": datetime.now().isoformat(timespec="seconds"),
        "input": str(args.video.resolve()),
        "input_info": info,
        "settings": {"count": args.count, "min_len": args.min_len, "max_len": args.max_len,
                     "min_gap": args.min_gap, "crop": args.crop, "no_hud": args.no_hud, "encoder": encoder},
        "hud": {"state": hud_state, "reference_size": [HUD_REF_W, HUD_REF_H], "samples_per_s": HUD_FPS,
                "star_box": STAR_BOX, "star_tips_x": STAR_TIPS_X, "star_tip_y": STAR_TIP_Y,
                "star_radius": STAR_RADIUS, "star_inner": STAR_INNER, "star_min_score": STAR_MIN_SCORE,
                "bar_box": BAR_BOX, "bar_segments": BAR_SEGMENTS, "bar_min_sat": BAR_MIN_SAT,
                "bar_min_distinct": BAR_MIN_DISTINCT, "min_hidden_s": MIN_HIDDEN, "hidden_pad_s": HIDDEN_PAD,
                "star_boost": STAR_BOOST, "min_share": HUD_MIN_SHARE,
                "minimap_seen_share": round(seen_share, 4), "stars_share": round(sum(stars) / n, 4),
                "excluded_s": sum(excluded) * BUCKET, "excluded_ranges_s": excluded_runs},
        "spread": {"seq_quiet_s": SEQ_QUIET, "max_per_sequence": MAX_PER_SEQ,
                   "active_score": MIN_PEAK_SCORE, "sequences_s": seq_spans},
        "scoring": {"bucket_s": BUCKET, "audio_weight": wa, "visual_weight": wv, "z_cap": Z_CAP,
                    "audio_scale_floor": AUDIO_SCALE_FLOOR, "visual_scale_floor": VISUAL_SCALE_FLOOR,
                    "loudness_floor_lufs": LOUDNESS_FLOOR, "smooth_sigma_s": SMOOTH_SIGMA,
                    "min_peak_score": MIN_PEAK_SCORE, "lead_frac": LEAD_FRAC, "hard_change": HARD_CHANGE,
                    "analysis_fps": ANALYSIS_FPS, "analysis_width": ANALYSIS_WIDTH,
                    "audio_median_lufs": round(a_med, 2), "audio_scale_lu": round(a_scale, 3),
                    "visual_median": round(v_med, 4), "visual_scale": round(v_scale, 4),
                    "smoothed_baseline": round(baseline, 3)},
        "timing_s": {"audio_analysis": round(t_audio, 2), "video_analysis": round(t_video, 2),
                     "hud_pixel_work": round(t_hud_px, 2), "encode": round(t_encode, 2), "total": round(total, 2)},
        "notes": notes,
        "clips": clips,
    }
    (run_dir / "scores.json").write_text(json.dumps(scores, indent=2), encoding="utf-8")
    print(f"Done in {total:.1f} s (audio {t_audio:.1f}, picture {t_video:.1f}, encode {t_encode:.1f}). "
          f"Scores: {run_dir / 'scores.json'}")


if __name__ == "__main__":
    main()
