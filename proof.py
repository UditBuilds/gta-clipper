"""Proof close-ups for /launch Shorts (launch-fixes): what made the Step 0 sample ("How to make GTA 6 cops FORGET you")
by hand, done by the tool.

  zoom_rect(name, prof, frame)      the part of the picture a close-up shows: "stars" (the wanted-star row and the
                                    heat icons under it), "tip" (the game's tip box, top left), "minimap", or a
                                    rect [x, y, w, h] given in the recording's pixels. Always 16:9, inside the picture.
  cut_zoom(video, start, length, rect, out)   that part of the recording as a 1280x720 clip (H.264, the same
                                    limited-range handling as clip.py), for a card or a compare in the Short.
  hud6(video, a, b, prof)           GTA 6 only, every half second of a..b: how many wanted stars are lit, what they
                                    look like (red, red outline, white outline, white) and how many heat icons
                                    (outfit, face, car) sit under them; and hud6_events(): when the icons or the star
                                    look change.

Known limit (Nov 19): clip.py's calibrated star rule misses hollow white stars over a dark background (recording
_022442 at 16:15 reads 0 stars); those samples are "no stars" here too, never a wrong look.

The tip box and the heat icons were measured on the owner's launch-night footage (Nov 19, recording _014828): the
tip box sits at x 30-470, y 286-436 of 1920x1080; the icons are red discs in a row under the stars, right-aligned
under the last star, about 2.15 star radii below the stars' middles and 2.06 radii apart. Standard library + ffmpeg.
"""

import math
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import clip

ZOOM_W, ZOOM_H = 1280, 720
TIP_RECT = (0, 175, 560, 315)          # 1920x1080 pixels: the tip box with some room round it (16:9)
MINIMAP_PAD = (150, 130, 50)           # round the minimap arrow: left/right, above, below (px at 1080p)
ICON_DY, ICON_DX, ICON_R = 2.15, 2.06, 0.72   # heat icons: below the stars' middle, apart, radius (in star radii)
ICON_RED = 0.35                        # a disc this red is an icon
WHITE_STROKE = 170                     # a lit outline star whose stroke is this bright is a white one
SOLID_WHITE, SOLID_GAP = 200, 60       # ...and solid white when its middle is this bright, and this much brighter
                                       # than round it (measured Nov 19: hollow white stars' middles are the picture)
STEP_S = 0.5


class ProofError(Exception):
    """A close-up can't be made (bad rect, ffmpeg failed)."""


def sixteen_nine(x, y, w, h, frame):
    """The smallest 16:9 box round x, y, w, h (whole pixels), moved inside the picture."""
    fw, fh = frame
    if w / h < 16 / 9:
        w2, h2 = h * 16 / 9, h
    else:
        w2, h2 = w, w * 9 / 16
    w2, h2 = min(fw, round(w2)), min(fh, round(h2))
    cx, cy = x + w / 2, y + h / 2
    x2 = min(max(0, round(cx - w2 / 2)), fw - w2)
    y2 = min(max(0, round(cy - h2 / 2)), fh - h2)
    return [x2, y2, w2, h2]


def zoom_rect(name, prof, frame=(1920, 1080)):
    """[x, y, w, h] of a named close-up, in the picture's own pixels (16:9)."""
    sx, sy = frame[0] / prof.frame[0], frame[1] / prof.frame[1]
    if isinstance(name, list) and len(name) == 4 and all(isinstance(v, (int, float)) for v in name):
        x, y, w, h = name
        if w <= 0 or h <= 0:
            raise ProofError(f"a zoom rect is [x, y, w, h] with w and h above 0, not {name}")
        return sixteen_nine(x, y, w, h, frame)
    if name == "stars":
        if not prof.stars_on:
            raise ProofError(f"profile {prof.name} has no wanted stars to zoom on")
        r, tips = prof.star_radius, prof.star_tips_x
        x0, x1 = tips[0] - r - 12, tips[-1] + r + 12
        y0 = max(0, prof.star_tip_y - 10)
        y1 = prof.star_tip_y + r + ICON_DY * r + ICON_R * r + 14     # down to the heat icons
        return sixteen_nine(x0 * sx, y0 * sy, (x1 - x0) * sx, (y1 - y0) * sy, frame)
    if name == "tip":
        x, y, w, h = TIP_RECT
        return sixteen_nine(x * sx, y * sy, w * sx, h * sy, frame)
    if name == "minimap":
        if getattr(prof, "minimap_method", "bars") == "arrow":
            cx, cy = prof.arrow["centre"]
            left, up, down = MINIMAP_PAD
            return sixteen_nine((cx - left) * sx, (cy - up) * sy, 2 * left * sx, (up + down) * sy, frame)
        x, y, w, h = prof.bar_box
        return sixteen_nine(x * sx, (y - 200) * sy, w * sx, (h + 200) * sy, frame)
    raise ProofError(f"a zoom is \"stars\", \"tip\", \"minimap\" or [x, y, w, h], not {name!r}")


def cut_zoom(video, start, length, rect, out, info=None):
    """rect of video from start for length seconds, scaled to ZOOM_W x ZOOM_H, as an H.264 clip at out (no sound)."""
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    info = info or clip.probe(ffprobe, Path(video))
    x, y, w, h = rect
    c = info.get("container_crop") or {}
    shown_w = info["width"] - c.get("left", 0) - c.get("right", 0)
    shown_h = info["height"] - c.get("top", 0) - c.get("bottom", 0)
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > shown_w or y + h > shown_h:
        raise ProofError(f"the close-up {list(rect)} is not inside the {shown_w}x{shown_h} picture of "
                         f"{Path(video).name}")
    conv = ":in_color_matrix=bt709:in_range=tv:out_color_matrix=bt709:out_range=tv" if clip.needs_range_fix(
        info["video_codec"], info["color_range"]) else ""
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}", "-t", f"{length:.3f}",
                           "-i", str(video), "-an", "-vf",
                           f"crop={w}:{h}:{x}:{y},scale={ZOOM_W}:{ZOOM_H}:flags=lanczos{conv},format=yuv420p",
                           "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-r", "30",
                           "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
                           "-color_trc", "bt709", str(out)], capture_output=True, text=True)
    if done.returncode or not Path(out).is_file():
        raise ProofError(f"ffmpeg could not cut the close-up {rect} of {Path(video).name} at {start:.1f} s: "
                         + done.stderr.strip()[-300:])
    return Path(out)


# --- GTA 6: heat icons and the look of the stars ---

def _row_prof(prof):
    """A stand-in profile whose HUD frame is one crop: the star box and, under it, the heat icon row."""
    x, y, w, h = prof.star_box
    r = prof.star_radius
    bottom = math.ceil(prof.star_tip_y + r + (ICON_DY + ICON_R) * r + 4)
    rows = max(h, bottom - y)
    p = SimpleNamespace(star_box=(x, y, w, h), hud_w=w, star_tip_y=prof.star_tip_y, star_radius=r,
                        star_inner=prof.star_inner, star_tips_x=prof.star_tips_x, star_method="gta6",
                        gta6_rule=dict(prof.gta6_rule), gta6_masks=prof.gta6_masks, rows=rows)
    return p


def icon_count(rgb, rp):
    """GTA 6: how many heat icons (red discs) sit under the stars, counted from the right until a place has none."""
    x0, y0, w = rp.star_box[0], rp.star_box[1], rp.hud_w
    r = rp.star_radius
    cy = rp.star_tip_y + r + ICON_DY * r
    rad = ICON_R * r
    n = 0
    for k in range(len(rp.star_tips_x)):
        cx = rp.star_tips_x[-1] - k * ICON_DX * r
        red = tot = 0
        for yy in range(int(cy - rad), int(cy + rad) + 1):
            for xx in range(int(cx - rad), int(cx + rad) + 1):
                if math.hypot(xx + 0.5 - cx, yy + 0.5 - cy) > rad:
                    continue
                i = ((yy - y0) * w + (xx - x0)) * 3
                if 0 <= yy - y0 < rp.rows and 0 <= xx - x0 < w and i + 2 < len(rgb):
                    tot += 1
                    red += rgb[i] >= 90 and rgb[i] - max(rgb[i + 1], rgb[i + 2]) >= 50
        if not tot or red / tot < ICON_RED:
            break
        n += 1
    return n


def star_look(rgb, rp, count):
    """GTA 6: what the lit stars look like: "red" (solid red), "red outline", "white outline" or "white" (solid);
    None with none lit. Only the star at the row's filling end is read."""
    if not count:
        return None
    slots, _ = clip.gta6_slot_features(rgb, rp)
    f = slots[-1] if rp.gta6_rule["fill_from"] == "right" else slots[0]
    if f["red_mid"] >= rp.gta6_rule["red_mid"]:
        return "red"
    # A white outline has a bright white stroke just inside its dark edge; a red one is dark red there. A solid white
    # star is white in its middle too, far brighter than round it (an outline's middle is the picture behind it).
    if f["stroke"] >= WHITE_STROKE:
        return "white" if f["middle"] >= SOLID_WHITE and f["middle"] - f["round"] >= SOLID_GAP else "white outline"
    return "red outline"


def hud6(video, a, b, prof, info=None):
    """GTA 6, every STEP_S of a..b: [(time, lit stars, their look, heat icons)]. [] for a profile that isn't GTA 6's."""
    if getattr(prof, "star_method", "outline") != "gta6" or not prof.stars_on:
        return []
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    info = info or clip.probe(ffprobe, Path(video))
    rp = _row_prof(prof)
    x, y, w, _ = rp.star_box
    c = info["container_crop"]
    shown_w, shown_h = info["width"] - c["left"] - c["right"], info["height"] - c["top"] - c["bottom"]
    sx, sy = shown_w / prof.frame[0], shown_h / prof.frame[1]
    conv = "in_color_matrix=bt709:in_range=tv:" if clip.needs_range_fix(info["video_codec"], info["color_range"]) else ""
    vf = (f"fps={1 / STEP_S:g},crop={round(w * sx)}:{round(rp.rows * sy)}:{round(x * sx)}:{round(y * sy)},"
          f"scale={w}:{rp.rows}:{conv}out_range=pc,format=rgb24")
    done = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(video), "-vf", vf,
                           "-f", "rawvideo", "-"], capture_output=True)
    size = w * rp.rows * 3
    out = []
    for k in range(len(done.stdout) // size):
        raw = done.stdout[k * size:(k + 1) * size]
        n = clip.gta6_star_count(raw, rp)
        # With no star lit the row (and the icons under it) isn't on screen: icons unknown, not 0.
        out.append((round(a + k * STEP_S, 2), n, star_look(raw, rp, n), icon_count(raw, rp) if n else None))
    return out


def steady_runs(values, hold=4):
    """[(first index, value)] of each run of one value at least `hold` samples long (2 s); shorter runs are
    misreads and are left out."""
    runs, k = [], 0
    while k < len(values):
        j = k
        while j < len(values) and values[j] == values[k]:
            j += 1
        if j - k >= hold:
            runs.append((k, values[k]))
        k = j
    return runs


def hud6_events(series):
    """When the heat icons or the stars' look change, from hud6(): [{"kind": "heat" | "star look", "at_s",
    "before", "after"}], at the first sample of the new value. A value must hold 2 s to count (shorter ones are
    misreads), and samples with no star row on screen are unknown and skipped."""
    events = []
    for key, idx in (("heat", 3), ("star look", 2)):
        known = [(s[0], s[idx]) for s in series if s[idx] is not None]
        runs = steady_runs([v for _, v in known])
        for (_, before), (k, after) in zip(runs, runs[1:]):
            if after != before:
                events.append({"kind": key, "at_s": known[k][0], "before": before, "after": after})
    return sorted(events, key=lambda e: e["at_s"])


# --- Full-screen gameplay (the owner's feedback on the posted sample, Nov 19) ---
# 1. Every beat is the gameplay full screen: a 9:16 window of the recording that follows the action. A HUD close-up is
#    an inset on top of it for at most INSET_MAX_S, never a small box over a blurred background.
# 4. The inset (and anything else we put on screen) sits inside YouTube's safe area: explainer.py's x 60-960,
#    y 380-1540, and off the caption band.
# 5. Zoom cap: an inset shows at most ZOOM_CAP screen pixels per recording pixel, so 1080p stays sharp; a detail that
#    would need more zoom is shown in a wider crop with a red box round it.
W, H = 1080, 1920
SAFE = (60, 380, 960, 1540)                 # x0, y0, x1, y1 (explainer.SAFE_LEFT, SAFE_TOP, W - SAFE_RIGHT, SAFE_BOTTOM)
CAPTION_BAND = (1130, 1510)                 # explainer's caption bands, the word and the phrase style together
INSET_W, INSET_H, INSET_BORDER = 840, 472, 6
INSET_X, INSET_Y = 84, 600                  # top-left of the inset's white edge: centred on explainer's CENTRE_X (510)
ZOOM_CAP = 2.0
INSET_MAX_S = 2.0
INSET_START_S, INSET_LEN_S = 0.2, 1.8       # by default an inset shows from 0.2 s into its beat, for 1.8 s
BOX_RGB, BOX_T = "0xFF2D2D", 6
CLIP_S = 8.0                                # a beat's clip: longer than any beat is on screen
FOLLOW_FPS, FOLLOW_W = 5, 320               # the action is looked for at 5 frames a second, 320 px wide
FOLLOW_ROWS = (0.15, 0.80)                  # ...between the HUD's top row and the minimap/subtitles
CENTRE_PULL = 0.5                           # how strongly the window prefers the middle (the player in third person)
FOLLOW_SHARE = 1.3                          # the action must be this much denser in the window than overall to move it
FOLLOW_MAX_STEP = 0.04                      # the window moves at most this share of the width per sample (0.2 s)
FOLLOW_EASE = 0.3
DARK_MEAN, DARK_SHARE, DARK_LUM = 55, 0.6, 40     # too dark: mean brightness under 55, or 60 % of pixels under 40
BLANK_STD, BLANK_EDGES, BLANK_SOFT_STD = 18, 0.025, 40   # mostly blank: hardly any contrast, or few edges and soft
                                                        # contrast (measured Nov 19: the dark garage 1.8 %, streets 5.7-8.3 %)


def crop_width(shown_h):
    """The 9:16 window's width for a picture shown_h high, even."""
    return int(round(shown_h * 9 / 16 / 2)) * 2


def hud_box(name, prof):
    """[x, y, w, h] round a part of the GTA 6 HUD, in the recording's pixels: "stars" (the star row) or "icons" (the
    heat icons under it)."""
    r, tips = prof.star_radius, prof.star_tips_x
    if name == "stars":
        return [tips[0] - r - 6, max(0, prof.star_tip_y - 6), tips[-1] - tips[0] + 2 * r + 12, 2 * r + 12]
    if name == "icons":
        cy, rad = prof.star_tip_y + r + ICON_DY * r, ICON_R * r
        x0 = tips[0] - rad - 6
        return [x0, cy - rad - 6, tips[-1] + rad + 6 - x0, 2 * rad + 12]
    raise ProofError(f"a box is \"stars\", \"icons\" or [x, y, w, h], not {name!r}")


def inset_rect(zoom, prof, frame=(1920, 1080), cap=ZOOM_CAP):
    """(rect, detail): the part of the recording an inset shows, and the close-up asked for when the zoom cap made
    the rect wider (None when it didn't). rect is 16:9 and inside the picture."""
    detail = zoom_rect(zoom, prof, frame)
    need = math.ceil(INSET_W / cap)
    x, y, w, h = detail
    if w >= need:
        return detail, None
    return sixteen_nine(x + w / 2 - need / 2, y + h / 2 - need * 9 / 32, need, need * 9 / 16, frame), detail


def inset_zoom(rect):
    """Screen pixels per recording pixel in an inset of rect."""
    return INSET_W / rect[2]


def safe_zone_problems(rect, what):
    """Where an on-screen rect [x, y, w, h] (1080x1920) leaves YouTube's safe area or covers the captions."""
    x, y, w, h = rect
    out = []
    if x < SAFE[0] or y < SAFE[1] or x + w > SAFE[2] or y + h > SAFE[3]:
        out.append(f"{what} at x {x}-{x + w}, y {y}-{y + h} is outside YouTube's safe area (x {SAFE[0]}-{SAFE[2]}, "
                   f"y {SAFE[1]}-{SAFE[3]}): under the buttons on the right or the title at the bottom")
    if y < CAPTION_BAND[1] and y + h > CAPTION_BAND[0]:
        out.append(f"{what} at y {y}-{y + h} covers the captions (y {CAPTION_BAND[0]}-{CAPTION_BAND[1]})")
    return out


def inset_screen_rect():
    """Where the inset sits on the Short, white edge included: [x, y, w, h]."""
    return [INSET_X, INSET_Y, INSET_W + 2 * INSET_BORDER, INSET_H + 2 * INSET_BORDER]


def box_in_inset(box, rect):
    """A recording-pixel box as pixels of the inset picture (inside its white edge), clipped to it."""
    s = INSET_W / rect[2]
    x0 = max(0, round((box[0] - rect[0]) * s))
    y0 = max(0, round((box[1] - rect[1]) * s))
    x1 = min(INSET_W, round((box[0] + box[2] - rect[0]) * s))
    y1 = min(INSET_H, round((box[1] + box[3] - rect[1]) * s))
    return [x0, y0, max(2, x1 - x0), max(2, y1 - y0)]


def gray_frames(video, start, length, fps, width, info=None):
    """The recording's frames from start, fps a second, scaled to width x (width * 9 / 16), as grey bytes, one per
    frame: (frames, width, height)."""
    ffmpeg = shutil.which("ffmpeg")
    height = int(round(width * 9 / 16))
    done = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{length:.3f}", "-i",
                           str(video), "-vf", f"fps={fps:g},scale={width}:{height},format=gray", "-f", "rawvideo", "-"],
                          capture_output=True)
    size = width * height
    return [done.stdout[k * size:(k + 1) * size] for k in range(len(done.stdout) // size)], width, height


def follow_track(frames, width, height, crop_frac):
    """Where the action is in each frame (grey bytes, width x height): the middle of a window crop_frac of the width,
    as a share of the width (0-1). Motion (the change from the frame before) between FOLLOW_ROWS is summed per
    column; the window with the most of it, pulled towards the middle, wins, but only when the motion is clearly
    bunched there (a camera turn moves everything, and then the window stays in the middle with the player). Eased
    and speed-limited, so the crop glides."""
    half = crop_frac / 2
    win = max(1, round(crop_frac * width))
    r0, r1 = int(FOLLOW_ROWS[0] * height), int(FOLLOW_ROWS[1] * height)
    targets, prev = [], None
    for f in frames:
        target = 0.5
        if prev is not None:
            col = [0] * width
            for y in range(r0, r1):
                o = y * width
                for x in range(width):
                    col[x] += abs(f[o + x] - prev[o + x])
            total = sum(col)
            if total > 0:
                pre = [0]
                for v in col:
                    pre.append(pre[-1] + v)
                best, best_c, best_e = -1.0, 0.5, 0
                for c0 in range(0, width - win + 1):
                    e = pre[c0 + win] - pre[c0]
                    mid = (c0 + win / 2) / width
                    score = e * (1 - CENTRE_PULL * abs(mid - 0.5) * 2)
                    if score > best:
                        best, best_c, best_e = score, mid, e
                if best_e / total >= FOLLOW_SHARE * win / width:
                    target = best_c
        targets.append(target)
        prev = f
    out, cur = [], 0.5
    for t in targets:
        want = cur + FOLLOW_EASE * (t - cur)
        cur += max(-FOLLOW_MAX_STEP, min(FOLLOW_MAX_STEP, want - cur))
        cur = min(1 - half, max(half, cur))
        out.append(cur)
    return out


def crop_keys(track, fps, shown_w, crop_w, every=0.5):
    """[(t, left x in pixels)] of the 9:16 window every `every` s, from a follow_track at fps."""
    step = max(1, round(every * fps))
    keys = []
    for k in range(0, len(track), step):
        left = round(track[k] * shown_w - crop_w / 2)
        keys.append((round(k / fps, 3), min(max(0, left), shown_w - crop_w)))
    return keys or [(0.0, (shown_w - crop_w) // 2)]


def crop_expr(keys):
    """ffmpeg's crop x as a function of t: straight lines between the keys, held after the last. Commas are escaped
    for a filter graph."""
    if len(keys) == 1:
        return str(keys[0][1])
    expr = str(keys[-1][1])
    for (t0, x0), (t1, x1) in reversed(list(zip(keys, keys[1:]))):
        expr = f"if(lt(t\\,{t1:g})\\,lerp({x0}\\,{x1}\\,(t-{t0:g})/{t1 - t0:g})\\,{expr})"
    return expr


def shown_size(info):
    c = info.get("container_crop") or {}
    return (info["width"] - c.get("left", 0) - c.get("right", 0), info["height"] - c.get("top", 0) - c.get("bottom", 0))


def vertical_clip(video, start, length, out, keys=None, inset=None, sound=False, info=None):
    """A 1080x1920 clip of the recording from start: the 9:16 window at keys ([(t, left x)]; the middle when None),
    full screen. inset: {"rect": the recording rect it shows, "from_s": the recording second it shows at t0, "t0", "t1":
    when it is on in the clip, "box": [x, y, w, h] in inset pixels or None}: the close-up on top, white-edged, at
    inset_screen_rect(). sound keeps the game's sound."""
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    info = info or clip.probe(ffprobe, Path(video))
    shown_w, shown_h = shown_size(info)
    cw = crop_width(shown_h)
    keys = keys or [(0.0, (shown_w - cw) // 2)]
    conv = ":in_color_matrix=bt709:in_range=tv:out_color_matrix=bt709:out_range=tv" if clip.needs_range_fix(
        info["video_codec"], info["color_range"]) else ""
    args = [ffmpeg, "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}", "-t", f"{length:.3f}", "-i", str(video)]
    graph = f"[0:v]crop={cw}:{shown_h}:'{crop_expr(keys)}':0,scale={W}:{H}:flags=lanczos{conv},format=yuv420p"
    if inset:
        x, y, w, h = inset["rect"]
        if x < 0 or y < 0 or x + w > shown_w or y + h > shown_h:
            raise ProofError(f"the inset {list(inset['rect'])} is not inside the {shown_w}x{shown_h} picture")
        src = max(0.0, inset["from_s"] - inset["t0"])
        args += ["-ss", f"{src:.3f}", "-t", f"{length:.3f}", "-i", str(video)]
        box = ""
        if inset.get("box"):
            bx, by, bw, bh = inset["box"]
            box = f",drawbox=x={bx}:y={by}:w={bw}:h={bh}:color={BOX_RGB}:t={BOX_T}"
        b = INSET_BORDER
        graph += (f"[main];[1:v]crop={w}:{h}:{x}:{y},scale={INSET_W}:{INSET_H}:flags=lanczos{conv}{box},"
                  f"pad={INSET_W + 2 * b}:{INSET_H + 2 * b}:{b}:{b}:white,format=yuv420p[ins];"
                  f"[main][ins]overlay={INSET_X}:{INSET_Y}:enable='between(t\\,{inset['t0']:g}\\,{inset['t1']:g})'")
    graph += "[v]"
    args += ["-filter_complex", graph, "-map", "[v]"]
    args += ["-map", "0:a?", "-c:a", "aac", "-b:a", "192k"] if sound else ["-an"]
    args += ["-c:v", "libx264", "-crf", "17", "-preset", "veryfast", "-r", "30", "-color_range", "tv",
             "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", str(out)]
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(args, capture_output=True, text=True)
    if done.returncode or not Path(out).is_file():
        raise ProofError(f"ffmpeg could not make the full-screen clip of {Path(video).name} at {start:.1f} s: "
                         + done.stderr.strip()[-300:])
    return Path(out)


def clip_frames(path, times, width=270, height=480, gray=True):
    """Frames of a finished clip at times (s), scaled to width x height: grey or RGB bytes, one per time."""
    ffmpeg = shutil.which("ffmpeg")
    out = []
    for t in times:
        done = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1",
                               "-vf", f"scale={width}:{height},format={'gray' if gray else 'rgb24'}", "-f", "rawvideo",
                               "-"], capture_output=True)
        out.append(done.stdout)
    return out


def frame_stats(gray, width, height, skip=None):
    """{"mean", "dark", "std", "edges"} of a grey frame: its mean brightness, the share of pixels under DARK_LUM, the
    spread of brightness and the share of pixels on an edge. skip: [x, y, w, h] left out (an inset on top)."""
    vals, edges = [], 0
    for y in range(0, height - 1):
        o = y * width
        for x in range(0, width - 1):
            if skip and skip[0] <= x < skip[0] + skip[2] and skip[1] <= y < skip[1] + skip[3]:
                continue
            v = gray[o + x]
            vals.append(v)
            if abs(v - gray[o + x + 1]) + abs(v - gray[o + width + x]) > 40:
                edges += 1
    if not vals:
        return {"mean": 0.0, "dark": 1.0, "std": 0.0, "edges": 0.0}
    mean = sum(vals) / len(vals)
    std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
    return {"mean": round(mean, 1), "dark": round(sum(v < DARK_LUM for v in vals) / len(vals), 3),
            "std": round(std, 1), "edges": round(edges / len(vals), 3)}


def shot_problems(stats):
    """Why a full-screen shot is no B-roll (rule 3), from frame_stats: too dark or mostly blank; [] when fine."""
    out = []
    if stats["mean"] < DARK_MEAN or stats["dark"] > DARK_SHARE:
        out.append(f"too dark (mean brightness {stats['mean']:.0f} of 255, {stats['dark']:.0%} of it near black)")
    if stats["std"] < BLANK_STD or (stats["edges"] < BLANK_EDGES and stats["std"] < BLANK_SOFT_STD):
        out.append(f"mostly blank (contrast {stats['std']:.0f}, edges {stats['edges']:.1%})")
    return out
