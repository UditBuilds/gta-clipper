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
