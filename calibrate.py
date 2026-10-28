"""Make a HUD profile (profiles/<name>.json) for clip.py from a few labelled frames of a recording (v9).

  1. --add grabs one frame of a video at each time you give (as you'd see it, after the container crop).
  2. A local page (http://127.0.0.1, this machine only) walks you through it in plain English:
     - you draw one rough box round the coloured bars by the minimap, and the tool finds the bars in it
       (find_strips: what most frames share; the scene changes, the HUD doesn't);
     - the tool guesses what each frame shows (HUD shown or hidden from those bars, black screens, stars and phone
       "no") and you confirm each picture ("Looks right") or change it. Measure uses confirmed labels only, and
       the saved profile records how many guesses were kept or changed;
     - you draw one box round the star row and the tool finds the stars in it (fit_stars: the outer edges of
       what differs between pictures with and without stars); you confirm the green outlines or redo them.
       4 clicks on the first and last star are the fallback;
     - you draw the phone on zoomed frames.
  3. "Measure" reads every labelled frame the way clip.py will: its own HUD filter chain on the video itself
     (same range handling), and its own detectors. Each limit is set halfway between the frames that show the
     thing and the nearest frame that doesn't. A limit with no such frame beyond it gets a fixed margin and is
     marked "one side only". The page shows each limit, the gap on each side and every frame that comes out wrong.
  4. "Save" writes profiles/<name>.json. It never overwrites a profile and never reads one, so a re-calibration
     starts blank.

Not measured (fade timing and judgement, not something a still frame shows): the stretch durations, the star
boost, the 20 % guard, the phone's cell size and min_bars. They come from DEFAULTS, which are GTA V's values
because those are the only ones we have. The profile lists them under "calibration".

The session (frames, labels, drawings, results, and when the page was opened, measured and saved) is kept in
C:\\AI\\gta\\calib\\<name>\\session.json, so you can stop and carry on later. Python standard library + ffmpeg only.
The page is calibrate_page.html, next to this file.

Usage:  python calibrate.py <name> [--add <video> <time> [<time> ...]] ... [--port N] [--no-browser]
  e.g.  python calibrate.py gta6 --add C:\\AI\\gta\\input\\gta6_day1.webm 1:40 2:20 3:05 --add other.webm 0:30
        python calibrate.py gta6          (carry on with the frames already added)
Times are m:ss, h:mm:ss or seconds.
"""

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import clip

CALIB_ROOT = Path(r"C:\AI\gta\calib")
NAME_RE = re.compile(r"^[a-z0-9_]{1,40}$")
# Values a still frame can't show. They are GTA V's (gta5's), the only ones we have; the profile lists them.
DEFAULTS = {"stretch_min_s": 1.5, "stretch_pad_s": 0.5, "min_share": 0.2, "star_boost": 3.0, "phone_cell": [8, 4]}
GREY_CHROMA = 12.0       # a colour whose channels are all within this of each other is grey (compression noise level)
SIN54 = math.sin(math.radians(54))  # a 5-pointed star's lowest tips sit SIN54 * radius below its centre
COS18 = math.cos(math.radians(18))  # ... and its two side points COS18 * radius left and right of it
WORKERS = 4              # ffmpeg frame grabs at once
# Finding the bars inside one rough box (find_strips). Judgement values, not measured on any game; calibtest.py
# checks them on made-up HUDs (different rows, thicknesses, colours, with and without a see-through edge).
BAR_CHROMA = 40.0        # a bar pixel's usual colour has its brightest channel at least this far above its darkest
BAR_SAME_HUE = 0.9       # a frame's colour "agrees" when it points the same way from grey (cosine; about 26 degrees)
BAR_AGREE = 0.5          # ... on at least half of the frames used
BAR_ROW_SHARE = 0.5      # bar rows: at least half as many bar pixels as the fullest row
BAR_COL_SHARE = 0.6      # a column is part of a bar when 60 % of the bar rows are bar pixels there
BAR_MIN_W = 6            # narrower coloured runs are not bars
BAR_HUE_SPLIT = 30.0     # a hue change of more than this (degrees) between neighbouring columns starts a new bar
BAR_MAX_AREA = 160_000   # a bigger rough box is refused (slow, and it picks up more than the bars)
# Finding the stars inside the drawn star box (fit_stars). Judgement values, not measured on any game; calibtest.py
# checks them on made-up star rows (sizes, shapes, slot counts, lit and unlit slots, text on the "no" pictures).
STAR_TOL = 1.9           # outlines stay this close to the star box (geometry_profile allows 2 px)
STAR_MIN_R = 3.0         # smaller stars can't be told from noise
STAR_MIN_FILL = 0.45     # the stars are at least this much of the box's height (the box is drawn round the row)
STAR_MIN_GAP = 1.95      # star slots at least this many radii apart: a star is 1.9 radii wide, so no overlaps
STAR_MIN_YES = STAR_MIN_NO = 2  # "stars: yes" and "no" pictures needed to find the stars
STAR_FIT_INNER = 0.45    # the coarse search's inner-corner ratio; the fine fit then finds the real one
STAR_INNER = (0.3, 0.7)  # inner corners at 0.3-0.7 of the radius (a regular star: 0.38); spikier or fatter is no star
STAR_COARSE, STAR_REFINE, STAR_KEEP = 12, 6, 3  # sizes/heights given a full row search; rows refined; rows scored
STAR_PAD = 4             # zeros round the target grid
BLACK_LEVEL, BLACK_SHARE = 20, 0.99  # a black screen: 99 % of a 160x90 copy of the frame darker than 20 (of 255)
REAL = {"hud": ("shown", "hidden"), "stars": ("yes", "no"), "phone": ("yes", "no")}  # labels Measure uses; "?" = not sure


class CalibError(Exception):
    """Something the person calibrating can fix: the message says what, in plain words."""


def parse_time(text):
    """Seconds from 'm:ss', 'h:mm:ss' (decimals allowed) or plain seconds."""
    parts = text.strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise CalibError(f"{text!r} is not a time (use m:ss, h:mm:ss or seconds)")
    if len(nums) > 3 or any(n < 0 for n in nums) or any(n >= 60 for n in nums[1:]):
        raise CalibError(f"{text!r} is not a time (use m:ss, h:mm:ss or seconds)")
    secs = 0.0
    for n in nums:
        secs = secs * 60 + n
    return secs


def range_fix(info):
    return info["video_codec"] == "vp9" and info["color_range"] == "tv"  # same test as clip.py main()


def shown_size(info):
    c = info["container_crop"]
    return info["width"] - c["left"] - c["right"], info["height"] - c["top"] - c["bottom"]


# --- Geometry from the drawing ---

def star_geometry(top_first, top_last, low, inner, slots):
    """Star outline numbers from 4 clicks: the first star's top point, the last star's top point, the first star's
    lowest point and one of its inner corners. The star is clip.py's: 5 points, the top one straight up.
    The messages name the points by the numbers the page draws next to them (1 to 4, in that order)."""
    (ax, ay), (bx, by) = top_first, top_last
    r = (low[1] - ay) / (1 + SIN54)
    if r <= 1:
        raise CalibError("point 3 (the first star's lowest point) must be lower down than point 1 (its top point): "
                         "click point 3 on one of the first star's two bottom tips")
    if abs(low[0] - ax) > r:  # the bottom tips sit 0.59 radius left and right of the top point
        raise CalibError("point 3 should be a bottom tip of the same star as point 1: just below it, a little to the "
                         "left or right. Click point 1 or point 3 again")
    if slots == 1 and abs(bx - ax) > r:
        raise CalibError("you typed 1 star slot, but point 2 is on another star. Count every star position in the "
                         "row, lit or not, and type that number")
    spacing = (bx - ax) / (slots - 1) if slots > 1 else 0.0
    if slots > 1 and spacing <= r:
        raise CalibError("point 2 (the last star's top point) must be to the right of point 1 by more than a star's "
                         "width: click it on the rightmost star slot, and check the number of star slots")
    ratio = math.hypot(inner[0] - ax, inner[1] - (ay + r)) / r
    if ratio > 0.85:
        raise CalibError("point 4 is too far from the first star's middle to be an inner corner: click a notch "
                         "between two of the first star's points")
    if ratio < 0.15:
        raise CalibError("point 4 is too close to the first star's middle: click a notch between two of its points, "
                         "where the edge turns inwards")
    return {"first_tip_x": round(ax, 2), "tip_spacing": round(spacing, 3), "tip_y": round(ay, 2),
            "radius": round(r, 2), "inner": round(ratio, 3), "level_px": round(by - ay, 1)}


def luminance(raw, w, h):
    return [0.2126 * raw[i] + 0.7152 * raw[i + 1] + 0.0722 * raw[i + 2] for i in range(0, w * h * 3, 3)]


def star_target(yes_lums, no_lums, w, h):
    """What a star outline should line up with, as rows of a grid padded with STAR_PAD zeros all round.

    The stars are what differs between the "stars: yes" and "no" pictures: |mean brightness with stars - without|
    is high over each whole star (dark outline and lit or unlit fill alike) and about 0 elsewhere, since the scene
    behind changes. The edges of that difference are the stars' outer edges. Edges inside a star (fill against
    outline) mostly cancel, so a smaller star inside the real one doesn't fit better. The edge map is
    clip.star_score's (|dx| + |dy|), mean-centred like clip's template and blurred like its outline
    (exp(-(d/1.5)^2))."""
    diff = [abs(sum(m[i] for m in yes_lums) / len(yes_lums) - sum(m[i] for m in no_lums) / len(no_lums))
            for i in range(w * h)]
    e = [0.0] * (w * h)
    for y in range(1, h - 1):
        for i in range(y * w + 1, y * w + w - 1):
            e[i] = abs(diff[i + 1] - diff[i - 1]) + abs(diff[i + w] - diff[i - w])
    mean = sum(e) / len(e)
    P = STAR_PAD
    W, H = w + 2 * P, h + 2 * P
    base = [[0.0] * W for _ in range(H)]
    for y in range(h):
        base[y + P][P:P + w] = [v - mean for v in e[y * w:(y + 1) * w]]
    k = [math.exp(-(t / 1.5) ** 2) for t in range(-4, 5)]  # exp(-(dx/1.5)^2) * exp(-(dy/1.5)^2) = exp(-(d/1.5)^2)
    across = [[sum(k[j] * row[x + j - 4] for j in range(9) if 0 <= x + j - 4 < W) for x in range(W)] for row in base]
    return [[sum(k[j] * across[y + j - 4][x] for j in range(9) if 0 <= y + j - 4 < H) for x in range(W)]
            for y in range(H)]


def star_samples(r, inner, step=0.5):
    """Points along one star outline (clip's star: 5 points, the top one straight up), relative to its top
    point: (dx, dy, length of outline the point stands for)."""
    pts = [(r_ * math.cos(a), r + r_ * math.sin(a)) for i in range(10)
           for a, r_ in [(-math.pi / 2 + i * math.pi / 5, r if i % 2 == 0 else r * inner)]]
    out = []
    for i in range(10):
        (ax, ay), (bx, by) = pts[i], pts[(i + 1) % 10]
        length = math.hypot(bx - ax, by - ay)
        m = max(1, math.ceil(length / step))
        out += [(ax + (bx - ax) * (j + 0.5) / m, ay + (by - ay) * (j + 0.5) / m, length / m) for j in range(m)]
    return out


def star_row_score(grid, tip_x, spacing, tip_y, r, inner, slots):
    """How well a row of star outlines lines up with the target (box pixels; about clip's correlation, up to a
    constant): the target summed along the outlines, over the square root of their length."""
    total = length = 0.0
    smp = star_samples(r, inner)
    for k in range(slots):
        ox = tip_x + k * spacing - 0.5 + STAR_PAD
        oy = tip_y - 0.5 + STAR_PAD
        for dx, dy, wt in smp:
            x, y = ox + dx, oy + dy
            ix, iy = math.floor(x), math.floor(y)
            fx, fy = x - ix, y - iy
            r0, r1 = grid[iy], grid[iy + 1]
            total += wt * ((1 - fy) * ((1 - fx) * r0[ix] + fx * r0[ix + 1]) + fy * ((1 - fx) * r1[ix] + fx * r1[ix + 1]))
            length += wt
    return total / math.sqrt(length)


def star_fits_box(w, h, tip_x, spacing, tip_y, r, inner, slots):
    """The outlines stay inside the box (within STAR_TOL), the stars don't overlap and the shape is a star."""
    return (r >= max(STAR_MIN_R, STAR_MIN_FILL * h / (1 + SIN54)) and STAR_INNER[0] <= inner <= STAR_INNER[1] and tip_y >= -STAR_TOL and tip_y + r * (1 + SIN54) <= h + STAR_TOL
            and tip_x - r * COS18 >= -STAR_TOL and tip_x + spacing * (slots - 1) + r * COS18 <= w + STAR_TOL
            and (slots == 1 or spacing >= STAR_MIN_GAP * r))


def fit_star_row(grid, w, h, slots=None):
    """The star rows that line up best with the target grid (star_target), best first: up to STAR_KEEP of
    (score, (tip_x, spacing, tip_y, r, inner, slots)) in box pixels. slots None = try every count that fits.

    Coarse: for every size and height on a 0.5 px / 1 px grid, one star's score at every whole-pixel position
    (from the target's shifted rows), then the best evenly spaced row of 1-10 of those stars. Fine: the best few
    rows moved, spaced, sized and shaped in ever smaller steps while the score rises."""
    r_max = min((h + 2 * STAR_TOL) / (1 + SIN54), (w + 2 * STAR_TOL) / (2 * COS18))
    r_min = max(STAR_MIN_R, STAR_MIN_FILL * h / (1 + SIN54))
    singles = []
    r = r_min
    while r <= r_max + 1e-9:
        smp = star_samples(r, STAR_FIT_INNER)
        L = sum(s[2] for s in smp)
        lo, hi = math.ceil(r * COS18 - STAR_TOL), math.floor(w + STAR_TOL - r * COS18)
        ty = -STAR_TOL
        while hi >= lo and ty + r * (1 + SIN54) <= h + STAR_TOL + 1e-9:
            weights = {}
            for dx, dy, wt in smp:
                x, y = dx - 0.5 + STAR_PAD, ty + dy - 0.5 + STAR_PAD
                ix, iy = math.floor(x), math.floor(y)
                fx, fy = x - ix, y - iy
                for key, part in (((iy, ix), (1 - fx) * (1 - fy)), ((iy, ix + 1), fx * (1 - fy)),
                                  ((iy + 1, ix), (1 - fx) * fy), ((iy + 1, ix + 1), fx * fy)):
                    weights[key] = weights.get(key, 0.0) + wt * part
            one = [0.0] * (hi - lo + 1)
            for (iy, ix), wt in weights.items():
                seg = grid[iy][lo + ix:hi + ix + 1]
                one = [a + wt * b for a, b in zip(one, seg)]
            singles.append((max(one) / math.sqrt(L), r, ty, one, lo, L))
            ty += 1.0
        r += 0.5
    if not singles:
        raise CalibError("the star box is too small for a star: drag it again round the whole star row")
    singles.sort(key=lambda c: -c[0])
    rows = []
    for _, r, ty, one, lo, L in singles[:STAR_COARSE]:
        span = len(one) - 1
        for n in ([slots] if slots else range(1, 11)):
            if n == 1:
                i = max(range(len(one)), key=one.__getitem__)
                rows.append((one[i] / math.sqrt(L), (lo + i, 0.0, ty, r, STAR_FIT_INNER, 1)))
                continue
            best = None
            s = max(STAR_MIN_GAP * r, 2.0)
            while s * (n - 1) <= span:
                for x0 in range(0, span - math.ceil(s * (n - 1)) + 1):
                    v = sum(one[round(x0 + k * s)] for k in range(n))
                    if best is None or v > best[0]:
                        best = (v, x0, s)
                s += 0.5
            if best:
                rows.append((best[0] / math.sqrt(n * L), (lo + best[1], best[2], ty, r, STAR_FIT_INNER, n)))
    if not rows:
        raise CalibError(f"{slots} star slots don't fit side by side in the star box: check the number, or drag the "
                         "box again round the whole star row")
    rows.sort(key=lambda c: -c[0])
    picked = []
    for score, p in rows:  # the best few, no two the same layout
        if all(p[5] != q[5] or abs(p[0] - q[0]) > 1 or abs(p[3] - q[3]) > 0.5 or abs(p[2] - q[2]) > 1 for _, q in picked):
            picked.append((score, p))
        if len(picked) == STAR_REFINE:
            break
    out = [refine_star_row(grid, w, h, p) for _, p in picked]
    out.sort(key=lambda c: -c[0])
    return out[:STAR_KEEP]


def refine_star_row(grid, w, h, p):
    """(score, params) after moving tip_x, spacing, tip_y, r and inner in ever smaller steps while the score rises."""
    p = list(p)
    steps = [0.5, 0.25 if p[5] > 1 else 0.0, 0.5, 0.25, 0.04]
    least = [0.05, 0.01, 0.05, 0.02, 0.005]
    best = star_row_score(grid, *p)
    while any(st >= lo for st, lo in zip(steps, least)):
        moved = False
        for i in range(5):
            if steps[i] < least[i]:
                continue
            for sign in (1, -1):
                q = list(p)
                q[i] += sign * steps[i]
                if star_fits_box(w, h, *q):
                    v = star_row_score(grid, *q)
                    if v > best + 1e-12:
                        best, p, moved = v, q, True
                        break
        if not moved:
            steps = [st / 2 for st in steps]
    return best, tuple(p)


def star_scores(box, geom, raws):
    """clip.star_score of each raw star box (bytes, the box's own width) with the outline geom (frame pixels)."""
    prof = SimpleNamespace(star_box=tuple(box), hud_w=box[2], star_tip_y=geom["tip_y"], star_radius=geom["radius"],
                           star_inner=geom["inner"],
                           star_tips_x=tuple(geom["first_tip_x"] + geom["tip_spacing"] * k for k in range(geom["slots"])))
    prof.star_template = clip.star_template(prof)
    return [clip.star_score(raw, prof) for raw in raws]


def fit_stars(box, yes_raws, no_raws, slots=None):
    """The star row inside box ([x, y, w, h], whole frame pixels) from the star box of the "stars: yes" and "no"
    pictures (raw RGB bytes, box size); slots None = find the number too. Of the best rows the target picks, the
    best fitting one whose clip.star_score separates the two groups wins (the best fitting one if none does).
    Returns the outline (frame pixels), the lowest "yes" and highest "no" score and whether they separate."""
    x0, y0, w, h = box
    for raws, what, need in ((yes_raws, "yes", STAR_MIN_YES), (no_raws, "no", STAR_MIN_NO)):
        if len(raws) < need:
            raise CalibError(f"mark at least {need} pictures 'stars: {what}' in step 2 first ({len(raws)} so far): "
                             "the tool finds the stars from what differs between the two")
    grid = star_target([luminance(r, w, h) for r in yes_raws], [luminance(r, w, h) for r in no_raws], w, h)
    best = None
    for _, (tx, sp, ty, r, inner, n) in fit_star_row(grid, w, h, slots):  # best fitting first
        geom = {"first_tip_x": round(x0 + tx, 2), "tip_spacing": round(sp, 3), "tip_y": round(y0 + ty, 2),
                "radius": round(r, 2), "inner": round(inner, 3), "slots": n}
        yes, no = star_scores(box, geom, yes_raws), star_scores(box, geom, no_raws)
        if best is None or min(yes) > max(no) >= best[0]:  # the first one that separates, else the first
            best = (min(yes) - max(no), geom, yes, no)
        if best[0] > 0:
            break
    gap, geom, yes, no = best
    return {**geom, "box": list(box), "slots_typed": bool(slots), "yes": len(yes), "no": len(no),
            "lowest_yes": round(min(yes), 3), "highest_no": round(max(no), 3), "separates": gap > 0,
            "confirmed": False}


def in_frame(rect, frame, what="box"):
    """rect [x, y, w, h] cut to the picture's edges: a box dragged past an edge would make clip.py refuse the
    profile. A box already inside (in whole pixels) comes back unchanged; one wholly outside is refused."""
    fw, fh = frame
    x, y, w, h = rect
    if round(x) >= 0 and round(y) >= 0 and round(x) + round(w) <= fw and round(y) + round(h) <= fh:
        return rect
    x0, y0, x1, y1 = max(0, round(x)), max(0, round(y)), min(fw, round(x + w)), min(fh, round(y + h))
    if x1 - x0 < 1 or y1 - y0 < 1:
        raise CalibError(f"the {what} is outside the picture: draw it again on the picture")
    return [x0, y0, x1 - x0, y1 - y0]


def snap_box(rect, cell):
    """[x, y, w, h] in whole pixels, w and h cut down to whole cells (at least one)."""
    x, y, w, h = (round(v) for v in rect)
    return [x, y, max(cell[0], w // cell[0] * cell[0]), max(cell[1], h // cell[1] * cell[1])]


def snap_area(rect, box, cell, what="area"):
    """rect moved onto the box's cell grid (nearest cell edges, at least one cell) and kept inside the box. An area
    drawn more than one cell outside the box is refused: cutting it down without a word would read something else."""
    bx, by, bw, bh = box
    cw, ch = cell
    x, y, w, h = rect
    if x < bx - cw or y < by - ch or x + w > bx + bw + cw or y + h > by + bh + ch:
        raise CalibError(f"the {what} goes outside the phone box: draw it inside the yellow phone box, or draw the "
                         "phone box bigger first")

    def edges(a, length, origin, size, step):
        lo = min(max(round((a - origin) / step), 0), size // step - 1)
        hi = min(max(round((a + length - origin) / step), lo + 1), size // step)
        return origin + lo * step, (hi - lo) * step

    nx, nw = edges(x, w, bx, bw, cw)
    ny, nh = edges(y, h, by, bh, ch)
    return [nx, ny, nw, nh]


def bar_row(boxes):
    """The minimap box (the bars' shared rows) and each bar's x range, from one box per bar."""
    if len(boxes) < 2:
        raise CalibError("draw at least 2 bar boxes")
    y0 = max(round(b[1]) for b in boxes)
    y1 = min(round(b[1] + b[3]) for b in boxes)
    if y1 <= y0:
        raise CalibError("the bar boxes must overlap in height (the bars sit in one row): redraw them level")
    xr = sorted([round(b[0]), round(b[0] + b[2])] for b in boxes)
    for a, b in zip(xr, xr[1:]):
        if b[0] < a[1]:
            raise CalibError("two bar boxes overlap side by side: draw one box inside each bar")
    return [xr[0][0], y0, xr[-1][1] - xr[0][0], y1 - y0], xr


def from_grey(c):
    """The colour minus its grey part: which way, and how far, it points from grey."""
    m = (c[0] + c[1] + c[2]) / 3
    return c[0] - m, c[1] - m, c[2] - m


def same_hue(c, ref_vec, ref_len):
    """True when colour c is clearly coloured and points the same way from grey as the reference (BAR_SAME_HUE)."""
    if max(c) - min(c) < BAR_CHROMA / 2:
        return False
    v = from_grey(c)
    return v[0] * ref_vec[0] + v[1] * ref_vec[1] + v[2] * ref_vec[2] >= BAR_SAME_HUE * math.hypot(*v) * ref_len


def hue(c):
    """Hue in degrees: 0 red, 60 yellow, 120 green, 180 cyan, 240 blue, 300 magenta."""
    return math.degrees(math.atan2(math.sqrt(3) * (c[1] - c[2]), 2 * c[0] - c[1] - c[2])) % 360


def hue_name(c):
    names = ((0, "red"), (30, "orange"), (60, "yellow"), (120, "green"), (180, "cyan"), (240, "blue"),
             (285, "purple"), (330, "pink"), (360, "red"))
    h = hue(c)
    return min(names, key=lambda n: abs(n[0] - h))[1]


def find_strips(images, w, h):
    """The coloured bars inside a rough box, from what most frames share. images: RGB bytes (w x h) of frames with
    the HUD on screen (mostly). The scene changes from frame to frame and the HUD doesn't, so a bar pixel is one
    whose usual (median) colour is clearly coloured and which has that same hue on at least half the frames. The
    bars' rows are the fullest run of such rows, less one antialiased row at each edge when there are 4 or more;
    a see-through edge round the bars takes the scene's colour, so it isn't part of them. Along those rows, each run
    of bar columns with one hue is one bar, less its antialiased ends.
    Returns {"rows": [y0, y1], "bars": [[x0, x1], ...], "colours": [...], "names": [...]} in box pixels (ends
    excluded), or raises CalibError saying what to do."""
    n = len(images)
    if n < 3:
        raise CalibError("finding the bars needs at least 3 pictures with the HUD on screen: add more with --add")
    half = n // 2
    bar = [None] * (w * h)  # the usual colour of each bar pixel
    for p in range(w * h):
        i = p * 3
        med = tuple(sorted(im[i + k] for im in images)[half] for k in range(3))
        if max(med) - min(med) < BAR_CHROMA:
            continue
        ref = from_grey(med)
        ref_len = math.hypot(*ref)
        if sum(same_hue((im[i], im[i + 1], im[i + 2]), ref, ref_len) for im in images) >= BAR_AGREE * n:
            bar[p] = med
    share = [sum(bar[y * w + x] is not None for x in range(w)) / w for y in range(h)]
    if max(share) < 0.1:
        raise CalibError("no coloured bars found in that box. Draw it round the coloured bars under the minimap, on a "
                         "picture where the HUD is on screen, with a little space round them")
    runs, y = [], 0
    while y < h:
        if share[y] >= BAR_ROW_SHARE * max(share):
            y0 = y
            while y < h and share[y] >= BAR_ROW_SHARE * max(share):
                y += 1
            runs.append((y0, y))
        y += 1
    y0, y1 = max(runs, key=lambda r: (r[1] - r[0], sum(share[r[0]:r[1]])))
    if y1 - y0 >= 4:
        y0, y1 = y0 + 1, y1 - 1
    cols = []  # each column's usual colour, or None when it isn't part of a bar
    for x in range(w):
        got = [bar[y * w + x] for y in range(y0, y1) if bar[y * w + x] is not None]
        cols.append(tuple(sum(c[k] for c in got) / len(got) for k in range(3))
                    if len(got) >= BAR_COL_SHARE * (y1 - y0) else None)
    bars, x = [], 0
    while x < w:
        if cols[x] is None:
            x += 1
            continue
        x0 = x
        x += 1
        while x < w and cols[x] is not None and min(abs(hue(cols[x]) - hue(cols[x - 1])),
                                                    360 - abs(hue(cols[x]) - hue(cols[x - 1]))) <= BAR_HUE_SPLIT:
            x += 1
        if x - x0 >= BAR_MIN_W:
            trim = 2 if x - x0 >= 12 else 1
            bars.append([x0 + trim, x - trim])
    if len(bars) < 2:
        raise CalibError(f"found {'only one coloured bar' if bars else 'no coloured bars'} in that box; the tool needs "
                         "at least 2 (for example health and armour). Draw the box round all the bars under the "
                         "minimap, with a little space round them")
    colours = [tuple(round(sum(cols[x][k] for x in range(a, b)) / (b - a), 1) for k in range(3)) for a, b in bars]
    return {"rows": [y0, y1], "bars": bars, "colours": colours, "names": [hue_name(c) for c in colours]}


def find_bars_in(images, w, h):
    """find_strips() twice: on all the frames given, then on those its bars say show the HUD (when that leaves 3 or
    more and drops some), so frames without the HUD don't blur the bars' edges or split a part-empty bar."""
    found = find_strips(images, w, h)
    hud = [im for im in images if hud_guess(im, w, found)[0] == "shown"]
    if 3 <= len(hud) < len(images):
        try:
            found = find_strips(hud, w, h)
        except CalibError:
            pass  # keep the first answer
    return found


def area_mean(raw, w, xr, yr):
    """Mean [r, g, b] of columns xr and rows yr of an RGB image w pixels wide."""
    acc, n = [0, 0, 0], 0
    for y in range(*yr):
        for i in range((y * w + xr[0]) * 3, (y * w + xr[1]) * 3, 3):
            acc[0] += raw[i]
            acc[1] += raw[i + 1]
            acc[2] += raw[i + 2]
            n += 1
    return [v / n for v in acc]


def hud_guess(raw, w, found):
    """The tool's guess for one frame: (shown or hidden, why), from whether the found bars have their usual colour
    there. A bar's colour may change (health turning red), so one bar may be missing and it still counts."""
    y0, y1 = found["rows"]
    here = sum(same_hue(area_mean(raw, w, xr, (y0, y1)), from_grey(ref), math.hypot(*from_grey(ref)))
               for xr, ref in zip(found["bars"], found["colours"]))
    n = len(found["bars"])
    if here >= max(2, n - 1):
        return "shown", f"{here} of {n} minimap bars found"
    return "hidden", "no minimap bars: map, cutscene or menu?" if here == 0 else f"only {here} of {n} minimap bars found"


def label_summary(frames):
    """How the labels Measure used came about: tool's guess kept, guess changed, not sure, or no guess."""
    out = {}
    for k in REAL:
        s = {"guess_kept": 0, "guess_changed": 0, "not_sure": 0, "no_guess": 0}
        for f in frames:
            v, g = f["labels"].get(k), (f.get("suggest") or {}).get(k)
            if v is None:
                continue
            s["not_sure" if v == "?" else "no_guess" if g is None else "guess_kept" if v == g else "guess_changed"] += 1
        out[k] = s
    return out


# --- Limits ---

def at_least(shown, hidden, digits=1):
    """Limit for "must be at least": halfway between the lowest shown value and the highest hidden value below
    it. With no hidden value below it: half the lowest shown value ("one side only")."""
    lo = min(shown)
    below = [v for v in hidden if v < lo]
    if below:
        return round((lo + max(below)) / 2, digits), "halfway", max(below)
    return round(lo / 2, digits), "one side only", None


def at_most(shown, hidden, digits=1, cap=255.0):
    """Limit for "must be at most": halfway between the highest shown value and the lowest hidden value above it.
    With no hidden value above it: 1.5 x the highest shown value + 5 ("one side only")."""
    hi = max(shown)
    above = [v for v in hidden if v > hi]
    if above:
        return round((hi + min(above)) / 2, digits), "halfway", min(above)
    return round(min(cap, hi * 1.5 + 5), digits), "one side only", None


def family(c):
    """A colour's own channels: those above the middle of its brightest and darkest channel (green for green, red
    and green for yellow, green and blue for cyan). Empty for grey, where all three are within GREY_CHROMA."""
    hi, lo = max(c), min(c)
    if hi - lo < GREY_CHROMA:
        return ()
    return tuple(k for k in range(3) if c[k] > (hi + lo) / 2)


def colour_groups(colours):
    """Shown colours of one bar, grouped by colour family; every coloured frame lands in exactly one group, so none
    is left without a rule. The commonest family starts the first group. A family nested in a group's (green inside
    yellow) joins it; one that isn't (red next to green: health turning red) starts its own group.
    Grey colours (the bar isn't there: a picture ticked "HUD shown" by mistake, or an empty bar) join no group. They
    would drag the limits down to nothing, so that every picture passes; left out, they fail the rule and Measure
    lists them as missed, with the reason. All grey: one group of all three channels, which fit_bar() can't test."""
    fams = [family(c) for c in colours]
    counts = {}
    for f in fams:
        if f:
            counts[f] = counts.get(f, 0) + 1
    groups, home = [], {}
    for f in sorted(counts, key=lambda f: (-counts[f], f)):
        g = next((g for g in groups if set(f) <= set(g[0]) or set(g[0]) <= set(f)), None)
        if g is None:
            g = (f, [])
            groups.append(g)
        home[f] = g
    if not groups:
        return {(0, 1, 2): list(colours)}
    for c, f in zip(colours, fams):
        if f:
            home[f][1].append(c)
    return dict(groups)


CH = "rgb"
CH_NAME = {"r": "red", "g": "green", "b": "blue"}


def term_text(c, hi, lo):
    return f"{CH_NAME[CH[hi]]} beats {' and '.join(CH_NAME[CH[i]] for i in lo)} by {c[hi] - max(c[i] for i in lo):.0f}"


def bar_check(c, rule, min_sat):
    """None when colour c passes one bar's checks (clip.py's), else what fails, in plain words."""
    sat = clip.saturation(c)
    if sat < min_sat:
        return f"colour too weak ({sat:.2f}, needs {min_sat:g})"
    if clip.bar_colour_ok(c, rule):
        return None
    # the colour group closest to passing, and its term furthest from passing
    _, hi, lo, least = min(max((least - (c[hi] - max(c[i] for i in lo)), hi, lo, least) for hi, lo, least in group)
                           for group in rule)
    return f"{term_text(c, hi, lo)}, needs {least:g}"


def hud_why(means, prof):
    """Why minimap_seen() says what it says on one frame, in plain words."""
    ok, fails = [], []
    for i, (c, rule) in enumerate(zip(means, prof.bar_rules), 1):
        w = bar_check(c, rule, prof.bar_min_sat)
        if w:
            fails.append(f"bar {i}: {w}")
        else:
            ok.append(c)
    if len(ok) < prof.bar_min_count:
        return f"{len(ok)} of {len(means)} bars pass, needs {prof.bar_min_count} ({'; '.join(fails)})"
    far = max(math.dist(a, b) for k, a in enumerate(ok) for b in ok[k + 1:])
    if far < prof.bar_min_distinct:
        return f"the passing bars are too alike in colour (distance {far:.0f}, needs {prof.bar_min_distinct:g})"
    return f"{len(ok)} of {len(means)} bars pass as coloured bars" + (f" ({'; '.join(fails)})" if fails else "")


def phone_why(m, prof):
    """Which of phone_seen()'s checks fail on one frame, in plain words."""
    lo, hi = prof.phone_panel_range
    tests = [("dark area's brightest cell", m["face_max"], "at most", prof.phone_face_max),
             ("bright area's average", m["battery_mean"], "at least", prof.phone_battery_min),
             ("grey areas' darkest cell", m["panel_min"], "at least", lo),
             ("grey areas' brightest cell", m["panel_max"], "at most", hi),
             ("grey areas' spread", m["panel_max"] - m["panel_min"], "at most", prof.phone_panel_spread),
             ("grey areas' tint", m["panel_tint"], "at most", prof.phone_panel_tint)]
    bad = [f"{what} {v:.0f}, needs {kind} {lim:g}" for what, v, kind, lim in tests
           if not (v <= lim if kind == "at most" else v >= lim)]
    return "; ".join(bad) if bad else "every phone check passes"


def term_value(c, hi, lo):
    return c[hi] - max(c[i] for i in lo)


def fit_bar(i, shown, hidden, override):
    """any_of groups for bar i: each strong channel must beat the bar's weak channels by a limit set from the
    frames. Returns (any_of for the profile, number rows for the report)."""
    any_of, rows = [], []
    for strong, cols in sorted(colour_groups(shown).items()):
        weak = tuple(k for k in range(3) if k not in strong)
        if not weak:
            continue  # grey or white on these frames: no colour to test
        group = []
        for hi in strong:
            key = f"bar{i}.{''.join(CH[k] for k in strong)}.{CH[hi]}"
            sv = [term_value(c, hi, weak) for c in cols]
            hv = [term_value(c, hi, weak) for c in hidden]
            value, how, near = at_least(sv, hv)
            if key in override:
                value, how = override[key], "set by you"
            group.append({"hi": CH[hi], "lo": [CH[k] for k in weak], "min": value})
            rows.append({"key": key, "what": f"bar {i}: {CH_NAME[CH[hi]]} beats "
                                             f"{' and '.join(CH_NAME[CH[k]] for k in weak)} by at least",
                         "shown": [round(min(sv), 1), round(max(sv), 1)],
                         "hidden_near": None if near is None else round(near, 1),
                         "value": value, "how": how, "kind": "at least"})
        any_of.append(group)
    return any_of, rows


def number_row(key, what, shown, hidden, kind, override, digits=1):
    fn = at_least if kind == "at least" else at_most
    value, how, near = fn(shown, hidden, digits)
    if key in override:
        value, how = override[key], "set by you"
    return value, {"key": key, "what": what, "shown": [round(min(shown), digits + 1), round(max(shown), digits + 1)],
                   "hidden_near": None if near is None else round(near, digits + 1), "value": value, "how": how,
                   "kind": kind}


# --- Profiles ---

def geometry_profile(name, game, frame, drawing):
    """A profile with the drawn geometry and stand-in limits, for measuring. Missing parts are left off and
    reported; the minimap needs its bars before anything can be measured. Every box is cut to the picture's
    edges first (in_frame); the drawing itself is left as drawn."""
    d = drawing
    notes = []
    bars = d.get("bars") or []
    try:
        mm_box, xr = bar_row([in_frame(b["box"], frame, f"bar {i} box") for i, b in enumerate(bars, 1)])
    except CalibError as e:
        notes.append(("minimap", str(e)))
        mm_box, xr = [0, 0, 16, 2], [[0, 8], [8, 16]]
    profile = {"name": name, "game": game or "this game", "frame": list(frame),
               "stretch": {"min_s": DEFAULTS["stretch_min_s"], "pad_s": DEFAULTS["stretch_pad_s"]},
               "minimap": {"box": mm_box,
                           "bars": [{"name": f"bar {i}", "x_range": r,
                                     "any_of": [[{"hi": "r", "lo": ["g"], "min": 0}]]} for i, r in enumerate(xr, 1)],
                           "min_sat": 0.0, "min_bars": max(2, len(xr) - 1), "min_distinct": 0.0,
                           "min_share": DEFAULTS["min_share"]}}
    st = d.get("stars") or {}
    if st.get("off"):
        profile["stars"] = {"on": False}
    else:
        try:
            if not st.get("box"):
                raise CalibError("draw the box around the star row")
            star_box = in_frame(st["box"], frame, "star box")
            box = [round(v) for v in star_box]
            if st.get("mode") == "clicks":  # the fallback: 4 clicks and a typed number of slots
                c = st.get("clicks") or {}
                if not isinstance(st.get("slots"), int) or not 1 <= st["slots"] <= 10:
                    raise CalibError("type how many star slots the row has")
                missing = [k for k in ("top_first", "top_last", "low", "inner") if not c.get(k)]
                if missing:
                    raise CalibError("click the 4 star points (" + ", ".join(missing) + " still missing)")
                g = star_geometry(c["top_first"], c["top_last"], c["low"], c["inner"], st["slots"])
                slots = st["slots"]
            else:  # the default: the tool finds the stars in the box (Session.fit_stars), you confirm them
                g = st.get("fit")
                if not g:
                    raise CalibError("press 'Find the stars' so the tool finds them in your box")
                if g.get("box") != box:
                    raise CalibError("the star box changed after the stars were found: press 'Find the stars' again")
                if not g.get("confirmed"):
                    raise CalibError("check the green outlines on a few pictures, then press 'Looks right' (or 'Redo')")
                slots = g["slots"]
            x0, y0, w, h = star_box
            r, last = g["radius"], g["first_tip_x"] + g["tip_spacing"] * (slots - 1)
            cut = [side for side, out in (("top", g["tip_y"] < y0 - 2), ("bottom", g["tip_y"] + r * (1 + SIN54) > y0 + h + 2),
                                          ("left", g["first_tip_x"] - r * COS18 < x0 - 2),
                                          ("right", last + r * COS18 > x0 + w + 2)) if out]
            if cut:
                raise CalibError(f"the green star outlines go past the {' and '.join(cut)} of the star box: drag the "
                                 "box again so every star fits inside it, with a little space round them")
            profile["stars"] = {"on": True, "box": box, "slots": slots,
                                **{k: g[k] for k in ("first_tip_x", "tip_spacing", "tip_y", "radius", "inner")},
                                "min_score": 1.0, "boost": DEFAULTS["star_boost"]}
            if abs(g.get("level_px", 0)) > 2:
                notes.append(("stars", f"the first and last top points are {abs(g['level_px']):g} px apart in "
                                       "height; the outline uses the first one"))
        except CalibError as e:
            notes.append(("stars", str(e)))
            profile["stars"] = {"on": False, "_missing": True}
    ph = d.get("phone") or {}
    cell = DEFAULTS["phone_cell"]
    if ph.get("off"):
        profile["phone"] = {"on": False}
    elif not (ph.get("box") and ph.get("dark") and ph.get("bright") and ph.get("grey")):
        notes.append(("phone", "draw the phone box, then its dark, bright and grey areas"))
        profile["phone"] = {"on": False, "_missing": True}
    else:
        try:
            box = snap_box(in_frame(ph["box"], frame, "phone box"), cell)
            area = lambda r, what: snap_area(in_frame(r, frame, what), box, cell, what)
            profile["phone"] = {"on": True, "box": box, "cell": list(cell),
                                "dark": {"area": area(ph["dark"], "dark area"), "max": 0.0},
                                "bright": {"area": area(ph["bright"], "bright area"), "min_mean": 0.0},
                                "grey": {"areas": [area(a, f"grey area {i}") for i, a in enumerate(ph["grey"], 1)],
                                         "min": 0.0, "max": 0.0, "max_spread": 0.0, "max_tint": 0.0}}
        except CalibError as e:
            notes.append(("phone", str(e)))
            profile["phone"] = {"on": False, "_missing": True}
    return profile, notes


def load(profile, path):
    """Write profile to path (named <name>.json) and load it with clip.py's own loader."""
    clean = json.loads(json.dumps(profile))
    for sec in ("stars", "phone"):
        clean[sec].pop("_missing", None)
    path.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    try:
        return clip.load_profile(profile["name"], path)
    except clip.ProfileError as e:
        raise CalibError(f"the drawing doesn't make a valid profile: {e}")


# --- The session ---

class Session:
    def __init__(self, name):
        self.name = name
        self.dir = CALIB_ROOT / name
        self.file = self.dir / "session.json"
        self.lock = threading.Lock()
        self.cache = {}  # (frame id, geometry) -> raw HUD bytes
        if self.file.is_file():
            self.data = json.loads(self.file.read_text(encoding="utf-8"))
        else:
            self.data = {"name": name, "created": now(), "game": "", "frames": [], "videos": {},
                         "drawing": {"stars": {}, "bars": [], "phone": {"grey": []}}, "overrides": {}, "log": []}
        self.ffmpeg, self.ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        if not self.ffmpeg or not self.ffprobe:
            raise CalibError("ffmpeg/ffprobe not found on PATH (install: winget install Gyan.FFmpeg)")

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        tmp.replace(self.file)

    def event(self, what, **extra):
        self.data["log"].append({"event": what, "time": now(), **extra})

    def add(self, video, times):
        video = Path(video).resolve()
        if not video.is_file():
            raise CalibError(f"video not found: {video}")
        key = str(video)
        if key not in self.data["videos"]:
            self.data["videos"][key] = clip.probe(self.ffprobe, video)
        info = self.data["videos"][key]
        size = list(shown_size(info))
        for f in self.data["frames"]:
            if self.data["videos"][f["video"]] is not info and list(shown_size(self.data["videos"][f["video"]])) != size:
                raise CalibError(f"{video.name} is {size[0]}x{size[1]} but the frames already added are "
                                 f"{f['w']}x{f['h']}: use one picture size per profile")
        (self.dir / "frames").mkdir(parents=True, exist_ok=True)
        conv = "scale=in_color_matrix=bt709:in_range=tv:out_range=pc," if range_fix(info) else ""
        for text in times:
            t = parse_time(text)
            if t >= info["duration"]:
                raise CalibError(f"{text} is past the end of {video.name} ({clip.hms(info['duration'])})")
            if any(f["video"] == key and abs(f["time_s"] - t) < 1e-6 for f in self.data["frames"]):
                print(f"  {video.name} {clip.hms(t)}: already added")
                continue
            fid = f"f{len(self.data['frames']) + 1:02d}"
            png = self.dir / "frames" / f"{fid}.png"
            clip.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", key,
                      "-frames:v", "1", "-vf", conv + "format=rgb24", str(png)])
            self.data["frames"].append({"id": fid, "video": key, "video_name": video.name, "time_s": t,
                                        "time": clip.hms(t), "w": size[0], "h": size[1],
                                        "labels": {"hud": None, "stars": None, "phone": None}})
            print(f"  {fid}: {video.name} {clip.hms(t)}")
        self.ensure_guesses()
        self.save()

    def is_black(self, png):
        out = subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(png), "-vf",
                              "scale=160:90:flags=area,format=gray", "-f", "rawvideo", "-"], capture_output=True)
        raw = out.stdout
        if out.returncode != 0 or not raw:
            raise CalibError(f"couldn't read {png}: " + out.stderr.decode("utf-8", "replace").strip()[-300:])
        return sum(v < BLACK_LEVEL for v in raw) >= BLACK_SHARE * len(raw)

    def ensure_guesses(self):
        """The tool's first guesses for frames that have none: a black screen is HUD hidden, no stars, no phone;
        otherwise stars and phone start at "no" (most frames show neither) and HUD waits for the bars (find_bars).
        Guesses are only shown: Measure uses a label once it's confirmed on the page ("Looks right" or a click)."""
        for f in self.data["frames"]:
            if "suggest" not in f:
                f["black"] = self.is_black(self.dir / "frames" / f"{f['id']}.png")
                f["suggest"] = ({"hud": "hidden", "stars": "no", "phone": "no", "why": "black screen"} if f["black"]
                                else {"hud": None, "stars": "no", "phone": "no", "why": ""})

    def read_area(self, frames, rect):
        """rect [x, y, w, h] of each frame, from the video, with the same range handling as clip.py's filter chain."""
        x, y, w, h = rect

        def one(f):
            k = (f["id"], "area", tuple(rect))
            if k not in self.cache:
                info = self.data["videos"][f["video"]]
                conv = "in_color_matrix=bt709:in_range=tv:" if range_fix(info) else ""
                out = subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{f['time_s']:.3f}",
                                      "-i", f["video"], "-frames:v", "1", "-vf",
                                      f"crop={w}:{h}:{x}:{y},scale={w}:{h}:{conv}out_range=pc,format=rgb24",
                                      "-f", "rawvideo", "-"], capture_output=True)
                if out.returncode != 0 or len(out.stdout) != w * h * 3:
                    raise CalibError(f"couldn't read frame {f['id']} ({f['video_name']} {f['time']}): "
                                     + out.stderr.decode("utf-8", "replace").strip()[-300:])
                self.cache[k] = out.stdout
            return self.cache[k]
        with ThreadPoolExecutor(WORKERS) as pool:
            return dict(zip((f["id"] for f in frames), pool.map(one, frames)))

    def find_bars(self, rect):
        """Find the bars inside one rough box (find_strips) and guess HUD shown or hidden on every frame.
        The bars are found from the frames confirmed "HUD shown" when there are 3 or more, else from every frame
        not black and not confirmed hidden (the HUD must then be on most of them). Returns a message for the page."""
        fw, fh = self.frame_size() or (1920, 1080)
        x0, y0 = max(0, round(rect[0])), max(0, round(rect[1]))
        x1, y1 = min(fw, round(rect[0] + rect[2])), min(fh, round(rect[1] + rect[3]))
        if x1 - x0 < 12 or y1 - y0 < 3:
            raise CalibError("that box is too small: draw it round all the coloured bars under the minimap, with a "
                             "little space round them")
        if (x1 - x0) * (y1 - y0) > BAR_MAX_AREA:
            raise CalibError("that box is very big: draw it closer round the coloured bars under the minimap (a "
                             "little space round them is fine)")
        area = [x0, y0, x1 - x0, y1 - y0]
        frames = self.data["frames"]
        sure = [f for f in frames if f["labels"].get("hud") == "shown"]
        use = sure if len(sure) >= 3 else [f for f in frames if not f.get("black") and f["labels"].get("hud") != "hidden"]
        raws = self.read_area(frames, area)
        found = find_bars_in([raws[f["id"]] for f in use], area[2], area[3])
        r0, r1 = found["rows"]
        self.data["drawing"]["bars"] = [{"box": [x0 + a, y0 + r0, b - a, r1 - r0], "auto": True} for a, b in found["bars"]]
        self.data["drawing"]["bar_area"] = area
        for f in frames:
            if not f.get("black"):
                f["suggest"]["hud"], f["suggest"]["why"] = hud_guess(raws[f["id"]], area[2], found)
        shown = sum(f["suggest"]["hud"] == "shown" for f in frames)
        self.event("find_bars", area=area, bars=len(found["bars"]), frames_used=len(use))
        self.save()
        return (f"Found {len(found['bars'])} bars ({', '.join(found['names'])}) in one row, from {len(use)} pictures. "
                f"The tool's guess: HUD shown on {shown} pictures, hidden on {len(frames) - shown}.")

    def fit_stars(self, slots=None):
        """Find the star row inside the drawn star box from the confirmed "stars: yes" and "no" pictures (fit_stars)
        and keep it, unconfirmed, as drawing.stars.fit. slots: a number typed on the page, else None (found too).
        Returns (the fit, a message for the page)."""
        frame = self.frame_size()
        st = self.data["drawing"].setdefault("stars", {})
        if not frame:
            raise CalibError("add frames first (all of one picture size)")
        if not st.get("box"):
            raise CalibError("drag the box around the star row first")
        if slots is not None and not (isinstance(slots, int) and 1 <= slots <= 10):
            raise CalibError("the number of star slots must be 1 to 10 (or leave it empty)")
        box = [round(v) for v in in_frame(st["box"], frame, "star box")]
        if box[2] < 8 or box[3] < 6:
            raise CalibError("the star box is too small: drag it round the whole star row")
        frames = self.data["frames"]
        yes = [f for f in frames if f["labels"].get("stars") == "yes"]
        no = [f for f in frames if f["labels"].get("stars") == "no"]
        raws = self.read_area(yes + no, box)
        fit = fit_stars(box, [raws[f["id"]] for f in yes], [raws[f["id"]] for f in no], slots)
        st["fit"] = fit
        self.event("fit_stars", box=box, slots_typed=slots, slots=fit["slots"], separates=fit["separates"])
        self.save()
        found = f"{fit['slots']} star slot{'s' if fit['slots'] > 1 else ''}" + (" (the number you typed)" if slots else "")
        if fit["separates"]:
            msg = (f"Found {found} in your box, from {fit['yes']} pictures with stars and {fit['no']} without. "
                   f"With these outlines every picture with stars scores at least {fit['lowest_yes']:.3f} and every "
                   f"picture without at most {fit['highest_no']:.3f}, so they can be told apart.")
        else:
            msg = (f"Found {found} in your box, but with these outlines a picture without stars scores "
                   f"{fit['highest_no']:.3f} and one with stars only {fit['lowest_yes']:.3f}: they can't be told "
                   "apart yet. Check the 'stars' labels in step 2, or drag the star box again a little tighter.")
        return fit, msg

    def frame_size(self):
        sizes = {(f["w"], f["h"]) for f in self.data["frames"]}
        return sizes.pop() if len(sizes) == 1 else None

    def grab(self, frames, prof, geom_key):
        """Raw HUD bytes of each frame through clip.py's own filter chain, from the video itself."""
        def one(f):
            k = (f["id"], geom_key)
            if k not in self.cache:
                info = self.data["videos"][f["video"]]
                out = subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{f['time_s']:.3f}",
                                      "-i", f["video"], "-frames:v", "1", "-vf", clip.hud_filter(info, range_fix(info), prof),
                                      "-f", "rawvideo", "-"], capture_output=True)
                if out.returncode != 0 or len(out.stdout) != prof.frame_bytes:
                    raise CalibError(f"couldn't read frame {f['id']} ({f['video_name']} {f['time']}): "
                                     + out.stderr.decode("utf-8", "replace").strip()[-300:])
                self.cache[k] = out.stdout
            return self.cache[k]
        with ThreadPoolExecutor(WORKERS) as pool:
            return dict(zip((f["id"] for f in frames), pool.map(one, frames)))

    def measure(self):
        """Fit every limit from the labelled frames and check the result on them. Returns (report, profile)."""
        d = self.data
        frame = self.frame_size()
        if not frame:
            raise CalibError("add frames first (all of one picture size)")
        cand = self.dir / "candidate"
        cand.mkdir(exist_ok=True)
        geom, notes = geometry_profile(self.name, d.get("game"), frame, d["drawing"])
        problems = dict(notes)
        if "minimap" in problems:
            raise CalibError("minimap bars: " + problems["minimap"])
        gprof = load(geom, cand / f"{self.name}.json")
        frames = [f for f in d["frames"] if any(f["labels"].get(k) in v for k, v in REAL.items())]
        if not frames:
            raise CalibError("confirm what at least some pictures show (step 2)")
        raws = self.grab(frames, gprof, json.dumps(geom, sort_keys=True))
        ov = d.get("overrides") or {}
        lab = {f["id"]: f["labels"] for f in frames}
        ids = lambda rule, val: [f["id"] for f in frames if lab[f["id"]][rule] == val]
        report = {"rules": {}, "frames": []}
        prof = json.loads(json.dumps(geom))

        # Minimap bars
        means = {fid: clip.bar_means(raws[fid], gprof) for fid in raws}
        hud_yes, hud_no = ids("hud", "shown"), ids("hud", "hidden")
        rows, bad = [], []
        if not hud_yes:
            bad.append("tick at least one frame as 'HUD shown'")
        else:
            for i, bar in enumerate(prof["minimap"]["bars"], 1):
                any_of, r = fit_bar(i, [means[f][i - 1] for f in hud_yes], [means[f][i - 1] for f in hud_no], ov)
                if not any_of:
                    bad.append(f"bar {i} looks grey or white on the 'HUD shown' frames, not coloured: draw its box "
                               "inside the coloured part")
                    any_of = [[{"hi": "r", "lo": ["g"], "min": 256}]]  # can never pass
                bar["any_of"] = any_of
                rows += r
            # grey bars on "HUD shown" pictures don't set the colour-strength limit either (see colour_groups)
            sat_yes = [clip.saturation(means[f][i]) for f in hud_yes for i in range(len(means[f]))
                       if family(means[f][i])] or [0.0]
            sat_no = [clip.saturation(means[f][i]) for f in hud_no for i in range(len(means[f]))]
            v, row = number_row("min_sat", "every bar's colour strength (0 = grey, 1 = pure colour) at least",
                                sat_yes, sat_no, "at least", ov, 3)
            prof["minimap"]["min_sat"] = v
            rows.append(row)
            p1 = load(prof, cand / f"{self.name}.json")

            def passing(fid):
                return [c for c, rule in zip(means[fid], p1.bar_rules)
                        if clip.bar_colour_ok(c, rule) and clip.saturation(c) >= p1.bar_min_sat]

            def closest(cols):
                return min(math.dist(a, b) for k, a in enumerate(cols) for b in cols[k + 1:])

            d_yes = [closest(passing(f)) for f in hud_yes if len(passing(f)) >= 2]
            d_no = [closest(passing(f)) for f in hud_no if len(passing(f)) >= 2]
            if d_yes:
                v, row = number_row("min_distinct", "passing bars differ in colour (distance 0-441) by at least",
                                    d_yes, d_no, "at least", ov)
                if row["how"] == "one side only":
                    # A backup check: no hidden frame had 2 bars passing the colour checks, so there is nothing
                    # for it to separate; half the smallest shown distance only stops one plain colour passing twice.
                    row["how"] = "backup check: half the smallest"
                prof["minimap"]["min_distinct"] = v
                rows.append(row)
            else:
                bad.append("on no 'HUD shown' frame do 2 bars pass: check the bar boxes")
        report["rules"]["minimap"] = {"rows": rows, "bad": bad,
                                      "min_bars": prof["minimap"]["min_bars"], "bars": len(prof["minimap"]["bars"])}

        # Stars
        srows, sbad = [], []
        scores = {}
        if prof["stars"].get("on"):
            scores = {fid: clip.star_score(raws[fid], gprof) for fid in raws}
            yes, no = [scores[f] for f in ids("stars", "yes")], [scores[f] for f in ids("stars", "no")]
            if not yes:
                sbad.append("tick at least one frame as 'stars: yes'")
            elif no and min(yes) <= max(no):
                sbad.append(f"the two groups overlap: a frame with stars scores {min(yes):.3f}, a frame without "
                            f"scores {max(no):.3f}. Check the labels, the star box and the "
                            + ("4 clicks" if d["drawing"].get("stars", {}).get("mode") == "clicks"
                               else "green star outlines (step 3)"))
            if yes:
                v, row = number_row("min_score", "star outline match (0 = none, 1 = perfect) at least", yes, no,
                                    "at least", ov, 3)
                if sbad and "min_score" not in ov:
                    v = 1.0  # no honest limit: nothing counts as stars until fixed
                    row.update(value=None, how="no limit: the groups overlap")
                prof["stars"]["min_score"] = v
                srows.append(row)
        elif prof["stars"].get("_missing"):
            sbad.append(problems["stars"])
        report["rules"]["stars"] = {"rows": srows, "bad": sbad, "off": not prof["stars"].get("on")
                                    and not prof["stars"].get("_missing")}

        # Phone
        prows, pbad = [], []
        pm = {}
        if prof["phone"].get("on"):
            pm = {fid: clip.phone_measure(raws[fid], gprof) for fid in raws}
            yes, no = ids("phone", "yes"), ids("phone", "no")
            if not yes:
                pbad.append("tick at least one frame as 'phone: yes'")
            else:
                def vals(group, key):
                    return [pm[f]["panel_max"] - pm[f]["panel_min"] if key == "spread" else pm[f][key] for f in group]
                spec = [("phone.dark", "face_max", "dark area: its brightest cell (0 = black, 255 = white) at most",
                         "at most", ("dark", "max")),
                        ("phone.bright", "battery_mean", "bright area: its average brightness at least", "at least",
                         ("bright", "min_mean")),
                        ("phone.grey_min", "panel_min", "grey areas: their darkest cell at least", "at least",
                         ("grey", "min")),
                        ("phone.grey_max", "panel_max", "grey areas: their brightest cell at most", "at most",
                         ("grey", "max")),
                        ("phone.grey_spread", "spread", "grey areas: brightest minus darkest cell at most", "at most",
                         ("grey", "max_spread")),
                        ("phone.grey_tint", "panel_tint", "grey areas: colour tint (red/green/blue difference) at most",
                         "at most", ("grey", "max_tint"))]
                for key, mkey, what, kind, (part, field) in spec:
                    v, row = number_row(key, what, vals(yes, mkey), vals(no, mkey), kind, ov)
                    prof["phone"][part][field] = v
                    prows.append(row)
        elif prof["phone"].get("_missing"):
            pbad.append(problems["phone"])
        report["rules"]["phone"] = {"rows": prows, "bad": pbad, "off": not prof["phone"].get("on")
                                    and not prof["phone"].get("_missing")}

        # The finished profile on every labelled frame, with clip.py's own detectors
        final = load(prof, cand / f"{self.name}.json")
        yes_word = {"hud": "shown", "stars": "yes", "phone": "yes"}
        for f in frames:
            fid, lb = f["id"], lab[f["id"]]
            raw = raws[fid]
            got = {"hud": "shown" if clip.minimap_seen(raw, final) else "hidden"}
            why = {"hud": hud_why(clip.bar_means(raw, final), final)}
            if final.stars_on:
                score = clip.star_score(raw, final)
                got["stars"] = "yes" if score >= final.star_min_score else "no"
                why["stars"] = f"star outline match {score:.3f}, limit {final.star_min_score:g}"
            if final.phone_on:
                got["phone"] = "yes" if clip.phone_seen(raw, final) else "no"
                why["phone"] = phone_why(clip.phone_measure(raw, final), final)
            # the reason is kept only where the tool and the label disagree
            why = {k: v for k, v in why.items() if lb.get(k) in ("shown", "hidden", "yes", "no") and k in got
                   and (got[k] == yes_word[k]) != (lb[k] == yes_word[k])}
            report["frames"].append({"id": fid, "label": f"{f['video_name']} {f['time']}", "want": lb, "got": got,
                                     "why": why,
                                     "star_score": round(scores[fid], 3) if fid in scores else None,
                                     "bars": [[round(v) for v in c] for c in means[fid]],
                                     "phone": {k: round(v, 1) for k, v in pm[fid].items()} if fid in pm else None})
        for rule, key in (("minimap", "hud"), ("stars", "stars"), ("phone", "phone")):
            r = report["rules"][rule]
            missed = [x["label"] for x in report["frames"]
                      if key in x["got"] and x["want"][key] in ("shown", "yes") and x["got"][key] != x["want"][key]]
            false = [x["label"] for x in report["frames"]
                     if key in x["got"] and x["want"][key] in ("hidden", "no") and x["got"][key] != x["want"][key]]
            r["missed"], r["false_alarms"] = missed, false
            r["status"] = ("off" if r.get("off") else "problem" if r["bad"] or missed or false
                           else "check" if any(row["how"] == "one side only" for row in r["rows"]) else "ready")
            r["used"] = {"shown": len(ids(key, "shown" if key == "hud" else "yes")),
                         "hidden": len(ids(key, "hidden" if key == "hud" else "no"))}
        ph = prof["phone"]
        report["snapped"] = {"minimap_box": prof["minimap"]["box"],
                             "phone": {"box": ph["box"], "dark": ph["dark"]["area"], "bright": ph["bright"]["area"],
                                       "grey": ph["grey"]["areas"]} if ph.get("on") else None}
        report["can_save"] = (not report["rules"]["minimap"]["bad"]
                              and not report["rules"]["stars"]["bad"] and not report["rules"]["phone"]["bad"]
                              and bool(d.get("game")))
        report["clean"] = all(report["rules"][r]["status"] in ("ready", "off") for r in report["rules"])
        report["inherited"] = inherited(prof)
        return report, prof

    def save_profile(self, force_with_problems):
        report, prof = self.measure()
        if not report["can_save"]:
            raise CalibError("not ready to save: " + ("type the game's name (step 2); " if not self.data.get("game")
                                                       else "") + "fix the rules marked Problem first")
        if not report["clean"] and not force_with_problems:
            raise CalibError("some frames still come out wrong or a limit is one-sided; press 'Save anyway' to keep it")
        out = clip.PROFILE_DIR / f"{self.name}.json"
        if out.exists():
            raise CalibError(f"{out} already exists; calibrate.py never overwrites a profile. Pick another name")
        log = self.data["log"]
        bars = self.data["drawing"].get("bars") or []
        opened = next((e["time"] for e in log if e["event"] == "page_opened"), None)
        saved = now()
        prof["notes"] = (f"Made by calibrate.py on {saved[:10]} from {len(report['frames'])} labelled frames of "
                         f"{', '.join(sorted({Path(v).name for v in self.data['videos']}))}. Rectangles are [x, y, w, h] "
                         f"in pixels of the {prof['frame'][0]}x{prof['frame'][1]} picture as shown.")
        prof["calibration"] = {"tool": "calibrate.py", "session": str(self.file), "frames": len(report["frames"]),
                               "measured": [row["key"] for r in report["rules"].values() for row in r["rows"]],
                               "set_by_hand": sorted(self.data.get("overrides") or {}),
                               "inherited": report["inherited"],
                               "status": {k: v["status"] for k, v in report["rules"].items()},
                               "bars": ("found by the tool from one rough box" if bars and all(b.get("auto") for b in bars)
                                        else "drawn by hand"),
                               "stars": star_source(self.data["drawing"].get("stars") or {}),
                               "labels": label_summary(self.data["frames"]),
                               "page_opened": opened, "saved": saved}
        clean = json.loads(json.dumps(prof))
        for sec in ("stars", "phone"):
            clean[sec].pop("_missing", None)
        clip.PROFILE_DIR.mkdir(exist_ok=True)
        text = json.dumps(clean, indent=2)
        with open(out, "x", encoding="utf-8") as fh:  # "x": fails if it appeared meanwhile
            fh.write(text + "\n")
        clip.load_profile(self.name)  # what clip.py will read
        self.event("save", path=str(out), clean=report["clean"])
        self.data["saved"] = {"path": str(out), "time": saved, "report": report}
        self.save()
        return out, minutes(opened, saved)


def star_source(st):
    """Where the saved star outline came from, for the profile's calibration block."""
    if st.get("off"):
        return "switched off"
    if st.get("mode") == "clicks":
        return "from 4 clicks"
    fit = st.get("fit") or {}
    return ("found by the tool in the drawn box and confirmed"
            + (", with the number of slots typed in" if fit.get("slots_typed") else ""))


def inherited(prof):
    out = [f"stretch min_s {prof['stretch']['min_s']} and pad_s {prof['stretch']['pad_s']}",
           f"minimap min_share {prof['minimap']['min_share']}",
           f"minimap min_bars {prof['minimap']['min_bars']} (bars - 1, at least 2)"]
    if prof["stars"].get("on"):
        out.append(f"stars boost {prof['stars']['boost']}")
    if prof["phone"].get("on"):
        out.append(f"phone cell {prof['phone']['cell']}")
    return out


def now():
    return datetime.now().isoformat(timespec="seconds")


def minutes(a, b):
    if not a:
        return None
    return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60, 1)


# --- The page's server (127.0.0.1 only) ---

def serve(session, port, browser):
    page = (Path(__file__).resolve().parent / "calibrate_page.html").read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with session.lock:
                    session.event("page_opened")
                    session.save()
                self.send(200, page.replace("__NAME__", session.name), "text/html; charset=utf-8")
            elif self.path == "/session":
                d = session.data
                self.send(200, {"name": session.name, "game": d.get("game", ""), "frames": d["frames"],
                                "drawing": d["drawing"], "overrides": d.get("overrides", {}),
                                "frame_size": session.frame_size(), "saved": d.get("saved"),
                                "exists": (clip.PROFILE_DIR / f"{session.name}.json").exists(),
                                "cell": DEFAULTS["phone_cell"], "log": d["log"]})
            elif re.fullmatch(r"/frame/f\d+\.png", self.path):
                png = session.dir / "frames" / self.path.split("/")[-1]
                if png.is_file():
                    self.send(200, png.read_bytes(), "image/png")
                else:
                    self.send(404, {"error": "no such frame"})
            else:
                self.send(404, {"error": "not found"})

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            except ValueError:
                self.send(400, {"error": "bad request"})
                return
            try:
                with session.lock:
                    if self.path in ("/state", "/measure", "/save", "/findbars", "/fitstars"):
                        for k in ("drawing", "overrides", "game"):
                            if k in body:
                                session.data[k] = body[k]
                        for f in session.data["frames"]:
                            if f["id"] in body.get("labels", {}):
                                f["labels"] = body["labels"][f["id"]]
                    if self.path == "/state":
                        session.save()
                        self.send(200, {"ok": True})
                    elif self.path == "/measure":
                        report, _ = session.measure()
                        session.event("measure", clean=report["clean"])
                        session.data["last_report"] = report
                        session.save()
                        self.send(200, report)
                    elif self.path == "/findbars":
                        rect = body.get("rect")
                        if not (isinstance(rect, list) and len(rect) == 4):
                            raise CalibError("draw a box round the bars first")
                        message = session.find_bars(rect)
                        self.send(200, {"message": message, "drawing": session.data["drawing"],
                                        "frames": session.data["frames"]})
                    elif self.path == "/fitstars":
                        fit, message = session.fit_stars(body.get("slots"))
                        self.send(200, {"fit": fit, "message": message})
                    elif self.path == "/check":  # the drawing's problems, while drawing (nothing is read or kept)
                        _, notes = geometry_profile(session.name, "", session.frame_size() or (1920, 1080),
                                                    body.get("drawing") or {})
                        self.send(200, {"notes": dict(notes)})
                    elif self.path == "/save":
                        out, mins = session.save_profile(bool(body.get("anyway")))
                        print(f"Saved {out}" + (f" ({mins:g} min after the page was first opened)" if mins else ""))
                        self.send(200, {"path": str(out), "minutes": mins})
                    elif self.path == "/quit":
                        self.send(200, {"ok": True})
                        threading.Thread(target=httpd.shutdown, daemon=True).start()
                    else:
                        self.send(404, {"error": "not found"})
            except CalibError as e:
                session.save()
                self.send(400, {"error": str(e)})

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"Calibration page: {url}  (this machine only; press Ctrl+C here or 'Finish' on the page to stop)")
    if browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    print("Stopped. Session kept in", session.file)


def main():
    ap = argparse.ArgumentParser(description="Make a HUD profile for clip.py from labelled frames (local page).")
    ap.add_argument("name", help="profile name: profiles/NAME.json (lowercase letters, digits, _)")
    ap.add_argument("--add", nargs="+", action="append", default=[], metavar="VIDEO_THEN_TIMES",
                    help="a video, then the times to grab from it (m:ss, h:mm:ss or seconds); repeat for more videos")
    ap.add_argument("--port", type=int, default=0, help="port for the page (default: any free one)")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = ap.parse_args()
    if not NAME_RE.match(args.name):
        sys.exit("ERROR: the name must be 1-40 lowercase letters, digits or _ (it becomes profiles/NAME.json)")
    if (clip.PROFILE_DIR / f"{args.name}.json").exists():
        sys.exit(f"ERROR: profiles/{args.name}.json already exists; calibrate.py never overwrites a profile")
    try:
        session = Session(args.name)
        for group in args.add:
            if len(group) < 2:
                raise CalibError("--add needs a video and at least one time")
            print(f"Grabbing frames from {group[0]} ...")
            session.add(group[0], group[1:])
        if session.data["frames"]:
            session.ensure_guesses()  # sessions started before the label guesses
            session.save()
    except CalibError as e:
        sys.exit("ERROR: " + str(e))
    if not session.data["frames"]:
        sys.exit("ERROR: no frames yet: use --add <video> <time> ...")
    print(f"Session {session.file}: {len(session.data['frames'])} frame(s)")
    serve(session, args.port, not args.no_browser)


if __name__ == "__main__":
    main()
