"""Engine v4 (editor-v3): the measured parts of payoff-first editing. Needs numpy, so it runs under .venv-tts only
(launch.py render does); clip.py stays standard library only and imports nothing from here.

- shot clips with a FIXED 9:16 window (no pan, no zoom move: the game camera already moves), 5 % in, so a
  stabilised piece and a plain one look the same size;
- measured stabilisation: vidstab on each piece between the game's own camera cuts, kept only where it measures
  calmer than the raw piece (Nov 20, recording _023422: calm chase-cam driving was already steady and vidstab made it
  worse; the impact jolt went from 8.8 to 2.0 px);
- the first-frame check (Short 2 of part3 opened on a blurry leaf), the loudest moment of a stretch (the hit), and
  the game's camera cuts.
"""
import math
import re
import shutil
import subprocess
from pathlib import Path

import clip
import proof

FPS = 30
W, H = 1080, 1920
ZOOM = 1.05                               # every shot is 5 % in: vidstab's margin, the same on every piece
STAB_WIDE = 1.25                          # vidstab measures and moves a window this much wider than the 9:16 one
STAB_DETECT = "vidstabdetect=shakiness=5:accuracy=15:result={trf}"
STAB_MOVE = "vidstabtransform=input={trf}:smoothing=15:optzoom=0:zoom=5:interpol=bicubic:crop=black"
STAB_MIN_FRAMES = 12                      # a piece shorter than this (0.4 s) is left as it is
SHAKE_MIN_PX = 2.0                        # a piece calmer than this (jitter RMS, screen px) is left as it is
SHAKE_BETTER = 0.85                       # stabilised is kept only when it measures this much calmer or better
SCENE = 0.25                              # ffmpeg's scene score over this is a camera cut...
CUT_ALONE_S = 0.15                        # ...when no other spike is this near: a run of spikes is a fast camera
                                          # swing (getting out of a car), not a cut (Nov 20: 6 in a row at 4:39.8)
LEAD_S = 0.45                             # vidstab measures from this far before a piece (same camera), so its
                                          # correction starts near zero where the piece joins the one before
PAYOFF_SPLIT_S = 0.3                      # a shot is also split this long before its payoff: the calm setup and the
                                          # jolt are judged apart (one piece for both kept the jolt, Nov 20)
# First frame (calibrated Nov 20 on part3 Short 2's leaf: blocked frames 3.0-7.5 detail and 41-65 % flat; clear
# gameplay frames 11.7-17.9 detail and at most 15 % flat).
FLAT_BLOCK, FLAT_LEVEL, FLAT_MAX = 30, 2.0, 0.30
DETAIL_SHARE = 0.6                        # frame 0's detail at least this share of the same shot's later frames
REPICK_S, REPICK_STEP_S = 1.5, 0.1        # a bad first frame moves the start later, up to this far
PIECE_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-bf", "0", "-g", "30"]
COLOUR_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


class SteadyError(Exception):
    pass


def run(args, cwd=None, text=True):
    done = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *map(str, args)], cwd=cwd,
                          capture_output=True, text=text)
    if done.returncode:
        raise SteadyError(f"ffmpeg: {str(done.stderr).strip()[-600:]}")
    return done


def info_of(video):
    return clip.probe(shutil.which("ffprobe"), Path(video))


def colour_fix(info):
    return (":in_color_matrix=bt709:in_range=tv:out_color_matrix=bt709:out_range=tv"
            if clip.needs_range_fix(info["video_codec"], info["color_range"]) else "")


def geometry(info, focus=0.5):
    """The fixed 9:16 window, 5 % in, centred on focus (0-1 across): (static crop, vidstab window crop, kept crop).
    The centre is held where the wider vidstab window still fits, so both crops show the same picture."""
    sw, sh = proof.shown_size(info)
    ch = int(round(sh / ZOOM / 2)) * 2
    cw = int(round(ch * 9 / 16 / 2)) * 2
    full_w = proof.crop_width(sh)
    ww = min(sw, int(round(full_w * STAB_WIDE / 2)) * 2)
    cx = min(max(focus * sw, ww / 2), sw - ww / 2)
    static = f"crop={cw}:{ch}:{int(round(cx - cw / 2))}:{(sh - ch) // 2}"
    wx = int(round(cx - ww / 2))
    window = f"crop={ww}:{sh}:{wx}:0"
    keep = f"crop={full_w}:{sh}:{(ww - full_w) // 2}:0"
    return static, window, keep


def finish(info, speed=1.0, grade=""):
    slow = f",setpts={1 / speed:g}*(PTS-STARTPTS)" if speed != 1.0 else ""
    return f"{slow},scale={W}:{H}:flags=lanczos{colour_fix(info)},fps={FPS}{grade},format=yuv420p,setsar=1"


def count_frames(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return int((out.stdout or "0").strip() or 0)


def make_piece(video, src, frames, out, info, focus=0.5, stabilise=False, speed=1.0, grade="", detect=None):
    """One piece of a shot (one game camera): frames at FPS from the recording's second src, through the fixed
    window; stabilise runs vidstab (measured over detect=(a, b), the piece itself by default)."""
    out = Path(out)
    static, window, keep = geometry(info, focus)
    seconds = frames / FPS * speed
    if stabilise:
        a, b = detect or (src, src + seconds + 0.05)
        trf = f"{out.stem}.trf"
        run(["-ss", f"{a:.4f}", "-t", f"{b - a:.4f}", "-i", video, "-vf",
             f"{window},{STAB_DETECT.format(trf=trf)}", "-f", "null", "-"], cwd=out.parent)
        vf = (f"{window},{STAB_MOVE.format(trf=trf)},trim=start={src - a:.4f},setpts=PTS-STARTPTS,{keep}"
              + finish(info, speed, grade))
        run(["-ss", f"{a:.4f}", "-t", f"{b - a:.4f}", "-i", video, "-an", "-vf", vf, "-frames:v", frames, *PIECE_ARGS,
             *COLOUR_TAGS, out.name], cwd=out.parent)
        (out.parent / trf).unlink(missing_ok=True)
    else:
        run(["-ss", f"{src:.4f}", "-t", f"{seconds + 0.3:.4f}", "-i", video, "-an", "-vf",
             "setpts=PTS-STARTPTS," + static + finish(info, speed, grade), "-frames:v", frames, *PIECE_ARGS,
             *COLOUR_TAGS, out.name], cwd=out.parent)
    got = count_frames(out)
    if got != frames:
        raise SteadyError(f"{out.name}: made {got} frames, wanted {frames}")
    return out


def gray_frames(path, w=270, h=480, at=None):
    """A clip's frames as grey float arrays (all of them, or the one at `at` seconds)."""
    import numpy as np
    pre = ["-ss", f"{at:.3f}"] if at is not None else []
    post = ["-frames:v", "1"] if at is not None else []
    raw = subprocess.run(["ffmpeg", "-v", "error", *pre, "-i", str(path), *post, "-vf", f"scale={w}:{h},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.float32)


def phase_shift(a, b):
    import numpy as np
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    r = np.fft.fft2(a * win) * np.conj(np.fft.fft2(b * win))
    c = np.fft.ifft2(r / (np.abs(r) + 1e-9)).real
    y, x = np.unravel_index(np.argmax(c), c.shape)
    return (x - a.shape[1] if x > a.shape[1] // 2 else x), (y - a.shape[0] if y > a.shape[0] // 2 else y)


def shake_of(frames):
    """(x, y, black edge) for grey frames: the jitter of the picture's top part (the background), in 1080-wide
    screen px: the RMS of the frame-to-frame shift less its 9-frame running average. A smooth camera move scores low,
    a judder or a jolt high. black edge: a frame shows a black border (stabilised past its margin)."""
    import numpy as np
    if len(frames) < 3:
        return 0.0, 0.0, False
    h, w = frames.shape[1:]
    top = frames[:, int(h * 0.05):int(h * 0.40), :]
    s = np.array([phase_shift(top[i], top[i + 1]) for i in range(len(top) - 1)], dtype=np.float32) * (W / w)
    k = min(9, len(s))
    smooth = np.stack([np.convolve(s[:, j], np.ones(k) / k, mode="same") for j in range(2)], axis=1)
    j = s - smooth
    edges = np.concatenate([frames[:, :3, :].mean(axis=(1, 2)), frames[:, -3:, :].mean(axis=(1, 2)),
                            frames[:, :, :3].mean(axis=(1, 2)), frames[:, :, -3:].mean(axis=(1, 2))])
    return (float(np.sqrt((j[:, 0] ** 2).mean())), float(np.sqrt((j[:, 1] ** 2).mean())), bool((edges < 8).any()))


def keep_stabilised(raw, stab):
    """The rule: stabilise only a piece that shakes, and only when that measures calmer with no black edge."""
    rx, ry, _ = raw
    sx, sy, black = stab
    return max(rx, ry) >= SHAKE_MIN_PX and math.hypot(sx, sy) < SHAKE_BETTER * math.hypot(rx, ry) and not black


def lone_spikes(times, alone=CUT_ALONE_S):
    """The spikes with no other spike within `alone` seconds: the game's camera cuts. A run is a fast swing."""
    times = sorted(times)
    return [t for k, t in enumerate(times)
            if (k == 0 or t - times[k - 1] > alone) and (k + 1 == len(times) or times[k + 1] - t > alone)]


def scene_cuts(video, a, b, threshold=SCENE):
    """The recording's seconds in a..b where the picture cuts (the game switching camera)."""
    done = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(video), "-an",
                           "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
                          capture_output=True, text=True)
    return lone_spikes([round(a + float(t), 3) for t in re.findall(r"pts_time:([0-9.]+)", done.stderr)])


def split_points(a, frames, cuts, speed=1.0):
    """Frame counts of the pieces a shot of `frames` frames from a splits into at the camera cuts: a piece ends on
    the last frame before a cut (floor: Nov 20, a piece that ended on the cut frame showed one frame of the next
    camera). speed: a slowed shot (the replay, 0.5) shows each second of the recording for 1/speed seconds."""
    edges = sorted({math.floor((c - a) / speed * FPS) for c in cuts if 0 < (c - a) / speed * FPS < frames})
    sizes, last = [], 0
    for e in edges + [frames]:
        if e - last > 0:
            sizes.append(e - last)
        last = e
    return sizes


def make_shot(video, a, frames, out, info=None, focus=0.5, grade="", log=print, stabilise=True, splits=(), speed=1.0):
    """A shot's clip: split at the game's camera cuts (and at `splits`, such as just before the payoff), each piece
    measured raw and stabilised, the calmer one kept, joined. vidstab measures a piece from LEAD_S before it when the
    same camera was on then. speed: 0.5 for the slow-mo replay (frames are the Short's; the recording's seconds are
    frames / FPS * speed). Returns the report: per piece its seconds, both measures and what was kept."""
    import numpy as np  # noqa: F401  (fails early under .venv)
    out = Path(out)
    info = info or info_of(video)
    cuts = scene_cuts(video, a, a + frames / FPS * speed)
    work = out.parent / f"{out.stem}_pieces"
    work.mkdir(parents=True, exist_ok=True)
    pieces, report, done = [], [], 0
    for k, n in enumerate(split_points(a, frames, list(cuts) + list(splits), speed)):
        src = a + done / FPS * speed
        camera_from = max([a] + [c for c in cuts if c <= src + 1e-6])
        raw = make_piece(video, src, n, work / f"p{k}_raw.mp4", info, focus, False, speed=speed, grade=grade)
        entry = {"from_s": round(src, 3), "frames": n, "raw_px": None, "stabilised_px": None, "stabilised": False}
        if stabilise and n >= STAB_MIN_FRAMES:
            r = shake_of(gray_frames(raw))
            lead = max(camera_from, src - LEAD_S)
            stab = make_piece(video, src, n, work / f"p{k}_stab.mp4", info, focus, True, speed=speed, grade=grade,
                              detect=(lead, src + n / FPS * speed + 0.05))
            s = shake_of(gray_frames(stab))
            keep = keep_stabilised(r, s)
            entry.update(raw_px=[round(r[0], 1), round(r[1], 1)], stabilised_px=[round(s[0], 1), round(s[1], 1)],
                         black_edge=s[2], stabilised=keep)
            pieces.append(stab if keep else raw)
        else:
            pieces.append(raw)
        report.append(entry)
        done += n
    (work / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in pieces), encoding="utf-8")
    run(["-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", out.resolve()], cwd=work)
    if count_frames(out) != frames:
        raise SteadyError(f"{out.name}: the joined shot has {count_frames(out)} frames, wanted {frames}")
    return {"camera_cuts": cuts, "pieces": report}


def add_inset(clip_path, video, inset, out, info, prof):
    """A shot clip with a HUD close-up on top (proof's geometry: inside the safe area, off the captions, zoom capped):
    inset = {"zoom": "stars"|..., "box": "stars"|"icons"|None, "from_s": recording s, "t0", "t1": s into the clip}."""
    rect, detail = proof.inset_rect(inset.get("zoom", "stars"), prof, tuple(prof.frame))
    box = inset.get("box")
    box = proof.hud_box(box, prof) if isinstance(box, str) else detail
    x, y, w, h = rect
    b = proof.INSET_BORDER
    draw = ""
    if box:
        bx, by, bw, bh = proof.box_in_inset(box, rect)
        draw = f",drawbox=x={bx}:y={by}:w={bw}:h={bh}:color={proof.BOX_RGB}:t={proof.BOX_T}"
    length = inset["t1"] - inset["t0"]
    graph = (f"[1:v]crop={w}:{h}:{x}:{y},scale={proof.INSET_W}:{proof.INSET_H}:flags=lanczos{colour_fix(info)}{draw},"
             f"pad={proof.INSET_W + 2 * b}:{proof.INSET_H + 2 * b}:{b}:{b}:white,format=yuv420p,"
             f"setpts=PTS-STARTPTS+{inset['t0']:.3f}/TB[ins];"
             f"[0:v][ins]overlay={proof.INSET_X}:{proof.INSET_Y}:eof_action=pass:"
             f"enable='between(t\\,{inset['t0']:.3f}\\,{inset['t1']:.3f})'[v]")
    run(["-i", clip_path, "-ss", f"{inset['from_s']:.3f}", "-t", f"{length + 0.1:.3f}", "-i", video,
         "-filter_complex", graph, "-map", "[v]", *PIECE_ARGS, *COLOUR_TAGS, out])
    return {"rect": rect, "box": proof.box_in_inset(box, rect) if box else None, **inset}


# --- First frame ---

def frame_marks(gray):
    """(detail, flat share) of one grey frame: the mean |Laplacian| over its middle 70 % (rows), and the share of
    30x30 blocks with almost no detail (a blur, a leaf, a pole across the lens)."""
    import numpy as np
    g = gray
    lap = np.abs(4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:])
    h, w = lap.shape
    core = lap[int(h * 0.15):int(h * 0.85)]
    b = FLAT_BLOCK
    blocks = lap[:h // b * b, :w // b * b].reshape(h // b, b, w // b, b).mean(axis=(1, 3))
    return float(core.mean()), float((blocks < FLAT_LEVEL).mean())


def frame_verdict(first, later):
    """Problems with a first frame, from its marks and the marks of the same shot's later frames."""
    import numpy as np
    detail, flat = first
    out = []
    if flat >= FLAT_MAX:
        out.append(f"{flat:.0%} of it is a blur or blocked (at most {FLAT_MAX:.0%})")
    ref = float(np.median([d for d, _ in later])) if later else None
    if ref and detail < DETAIL_SHARE * ref:
        out.append(f"it is blurry: detail {detail:.1f} against {ref:.1f} later in the shot")
    return out


def source_frame(video, t, info, focus=0.5):
    """The recording's frame at t through the shot's fixed window, grey 270x480."""
    import numpy as np
    static, _, _ = geometry(info, focus)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", "-vf",
                          f"{static},scale=270:480,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(480, 270).astype(np.float32)


def clear_start(video, a, latest, info=None, focus=0.5, step=REPICK_STEP_S):
    """(start, problems, tried): the first second from a (up to `latest`, at most REPICK_S later) whose frame is
    clear: subject visible, nothing blurry or blocking it. problems is empty when one was found."""
    info = info or info_of(video)
    later = [frame_marks(source_frame(video, a + d, info, focus)) for d in (0.6, 1.2, 1.8)]
    tried, t = [], a
    while t <= min(latest, a + REPICK_S) + 1e-6:
        marks = frame_marks(source_frame(video, t, info, focus))
        why = frame_verdict(marks, later)
        tried.append({"at": round(t, 2), "detail": round(marks[0], 1), "flat": round(marks[1], 2), "why": why})
        if not why:
            return round(t, 3), [], tried
        t += step
    return a, [f"the first frame ({a:.1f} s) is not clear: " + "; ".join(tried[0]["why"])
               + f", and no frame up to {REPICK_S:g} s later is; start the Short elsewhere"], tried


# --- Sound ---

def loudest(video, a, b):
    """The recording's second in a..b with the loudest momentary sound (ebur128's 400 ms M, centred), or None."""
    done = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(video), "-vn",
                           "-af", "ebur128=metadata=1,ametadata=print:key=lavfi.r128.M", "-f", "null", "-"],
                          capture_output=True, text=True)
    best, best_t, t = None, None, None
    for line in done.stderr.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r"lavfi\.r128\.M=(-?[0-9.]+|-inf)", line)
        if m and t is not None and m.group(1) != "-inf":
            v = float(m.group(1))
            if t >= 0.4 and (best is None or v > best):
                best, best_t = v, t
    return None if best_t is None else round(a + best_t - 0.2, 2)
